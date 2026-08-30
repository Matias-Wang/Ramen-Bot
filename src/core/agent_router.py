import json
import os
import re
import time
from typing import Any

from google import genai
from google.genai import types

from skills.Search_skill import (
    GENERIC_STYLE_KEYWORDS,
    filter_ramen_data,
    generate_recommendations,
    summarize_description,
)
from skills.info_skill import InfoSkill
from skills.knowledge_skill import KnowledgeSkill
from core.prompts import IDENTIFY_INSTRUCTION_PROMPT
from core.usage_tracker import check_and_increment, record_tokens
from core.conversation_logger import log_conversation
from core.llm_retry import generate_with_retry
from core import timing

RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
RESET = '\033[0m'

# 使用者以「附近」等鄰近語意描述位置，但未提供可解析的實際地名時比對用；
# 這類輸入 Gemini 會將 location 解析為 null（無地名可抽取），若放行至
# SEARCH_BY_CRITERIA，filter_ramen_data() 會將「無地區條件」視為「符合全部
# 店家」進而從整個資料庫（含海外/外縣市店家）隨機抽選，與使用者實際所在地無關
# （見 DEVELOP_HISTORY.md 2026-06-29 大阪店家誤推薦事件；filter_by_location()
# 才是唯一會用真實 GPS 座標做 Haversine 過濾的路徑，僅在使用者分享 LINE
# 位置訊息時觸發，純文字查詢無法取得座標）。
_NEAR_ME_PATTERN = re.compile(r"附近|我這|我這裡|目前位置|現在位置|所在位置|current location|near me", re.IGNORECASE)

# 使用者詢問「這個機器人怎麼用」時比對用。這類問句不含拉麵知識，卻會被意圖解析
# 判為 KNOWLEDGE_QUERY 而進入 RAG 檢索、答非所問（實例：2026-08-06 日誌「怎麼使用」）。
# 沿用 _NEAR_ME_PATTERN 的作法在 STEP 1 之後攔截並回固定說明文案，不新增第五種
# intent（單一情境不值得擴充 schema），也不再多花一次 LLM 呼叫。
_HELP_PATTERN = re.compile(
    r"怎麼使用|怎麼用|如何使用|使用說明|使用方式|有什麼功能|能做什麼|會做什麼|^help$",
    re.IGNORECASE,
)
# 長度上限：問用法的句子都很短，藉此擋掉把用法詞夾在長句中的知識問題。
_HELP_MAX_LEN = 15
# 主詞守門：只有在句子確實在問「這個機器人」時才攔截。
# 這裡刻意用**正面表列**而非排除表列——「X 怎麼用」的 X 可以是任何拉麵領域名詞
# （食券機、替玉、海苔、餐券…），那是一份開放式清單，永遠列不完；漏列一個就會把
# 本來能被 knowledge/ramen_etiquette.md 答出來的問題誤判成「問機器人用法」。
# 反過來看，「這個機器人怎麼用」的主詞集合是封閉且很小的，判斷因此穩定得多。
_HELP_SUBJECT_PATTERN = re.compile(r"這個|這裡|你|妳|機器人|bot|系統", re.IGNORECASE)
_HELP_MESSAGE = (
    "我可以幫你做這些事：\n"
    "・找店：例如「中山區推薦的拉麵」「台北的豚骨拉麵」\n"
    "・找附近：直接分享你的位置，或說「附近有什麼拉麵」\n"
    "・問特定店家：例如「麵魚好吃嗎」\n"
    "・拉麵知識：例如「札幌拉麵的特色是什麼」\n"
    "・回報錯誤：例如「這家店的地址不對」"
)

# 意圖解析結構化輸出 Schema：搭配 response_mime_type="application/json"，
# 讓 Gemini 保證回傳合法 JSON，免除脆弱的字串修補（如全域替換單引號）。
# 欄位定義對齊 prompts.IDENTIFY_INSTRUCTION_PROMPT 的輸出格式。
INTENT_RESPONSE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    required=["intent"],
    properties={
        "intent": types.Schema(
            type=types.Type.STRING,
            enum=[
                "SEARCH_BY_CRITERIA",
                "GET_SPECIFIC_INFO",
                "KNOWLEDGE_QUERY",
                "REPORT_ERROR",
            ],
        ),
        "location": types.Schema(type=types.Type.STRING, nullable=True),
        "style": types.Schema(type=types.Type.STRING, nullable=True),
        "shop_name": types.Schema(type=types.Type.STRING, nullable=True),
        "query": types.Schema(type=types.Type.STRING, nullable=True),
        "radius_km": types.Schema(type=types.Type.NUMBER, nullable=True),
        "open_now": types.Schema(type=types.Type.BOOLEAN, nullable=True),
        "ui_tag": types.Schema(
            type=types.Type.STRING, enum=["CAROUSEL", "TEXT"], nullable=True
        ),
    },
)

def _style_or_none(style: Any) -> str | None:
    """
    將 Gemini 解析出的 style 正規化為「可用的口味條件」或 None。

    Parameters
    ----------
    style : Any
        意圖解析結果中的 style 欄位值。

    Returns
    -------
    str or None
        可用的口味關鍵字；若為空值或「推薦」等無篩選效力的形容詞則回傳 None。
    """
    if not style or style in GENERIC_STYLE_KEYWORDS:
        return None
    return style


class AgentRouter:
    """
    意圖分發中樞 (Agentic Router)。

    Parameters
    ----------
    model_name : str
        Gemini 模型名稱。
    """

    def __init__(self, model_name: str) -> None:
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.model_name = model_name
        self.info_skill = InfoSkill()
        self.knowledge_skill = KnowledgeSkill(self.client, model_name)

    def _extract_text(self, obj: Any) -> str:
        """
        從 Gemini 回應物件中提取文字內容。

        Parameters
        ----------
        obj : Any
            Gemini API 回應物件。

        Returns
        -------
        str
            提取到的文字內容。
        """
        for attr in ('text', 'output', 'content', 'candidates'):
            if hasattr(obj, attr):
                return getattr(obj, attr)
        return str(obj)

    def _parse_intent_json(self, response: Any) -> dict:
        """
        從 Gemini 回應中解析並回傳意圖 JSON。

        Parameters
        ----------
        response : Any
            Gemini API 回應物件。

        Returns
        -------
        dict
            解析後的意圖資料字典。

        Raises
        ------
        ValueError
            若無法從回應中抽出有效 JSON。
        """
        raw = str(self._extract_text(response)).strip()
        # 結構化輸出（response_schema）保證回傳合法 JSON，優先直接解析。
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # 防禦性 fallback：萬一模型未遵循 schema（含 ``` 圍欄或前後贅字），
        # 仍盡力抽取第一個 JSON 物件。不再做全域單引號替換，以免破壞含
        # 單引號的合法值（如英文縮寫）。
        cleaned = re.sub(r'```(?:json)?', '', raw)
        m = re.search(r'(\{.*\})', cleaned, re.S)
        if not m:
            raise ValueError('無法從回應抽出 JSON，請檢查 Gemini 輸出格式。')
        return json.loads(m.group(1))

    @staticmethod
    def _fallback_result(
        message: str = "系統發生未預期的錯誤，請稍後再試。",
        ui_tag: str = "TEXT",
    ) -> dict:
        """
        回傳標準降級結果，供頂層例外捕捉使用。

        Parameters
        ----------
        message : str
            顯示給使用者的錯誤提示。
        ui_tag : str
            回應 UI 型態，預設 "TEXT"；"LOCATION_REQUEST" 供 app.py 附加
            LINE 位置分享 Quick Reply 按鈕使用。

        Returns
        -------
        dict
            intent 為 FALLBACK 的結果字典。
        """
        return {
            "intent": "FALLBACK",
            "data": [],
            "recommendations": [],
            "ui_tag": ui_tag,
            "message": message,
        }

    def dispatch(
        self,
        user_text: str,
        current_time: str | None = None,
        user_id: str | None = None,
    ) -> dict:
        """
        解析使用者輸入的意圖，並分發至對應技能執行。

        Parameters
        ----------
        user_text : str
            使用者原始輸入文字。
        current_time : str or None
            台北時間字串（例如 "2026-06-28 14:30"），作為背景資訊提供給 Gemini
            判斷「現在」、「今天」等相對時間用語；若為 None 則不注入時間背景。
        user_id : str or None
            LINE 使用者 ID，供 Search Skill 排除該使用者近期已推薦過的店家；
            None（本機測試）則不套用排除。

        Returns
        -------
        dict
            包含 intent、data、recommendations、ui_tag、message 的結果字典。
            若所有步驟均失敗則回傳 FALLBACK intent。
        """
        records = timing.begin_collection()
        _t0 = time.time()
        try:
            result = self._dispatch_inner(user_text, current_time, user_id)
        except Exception as e:
            print(f"{RED}DISPATCH CRITICAL ERROR: {e}{RESET}")
            result = self._fallback_result()
        # 效能 KPI：附上各 LLM 呼叫與 dispatch 端到端耗時，供上層與驗證報告取用
        result["timing"] = timing.snapshot(records, time.time() - _t0)
        print(f"{CYAN}[KPI][dispatch] {timing.format_kpi(result['timing'])}{RESET}")
        return result

    def _dispatch_inner(
        self,
        user_text: str,
        current_time: str | None = None,
        user_id: str | None = None,
    ) -> dict:
        """dispatch 的核心邏輯，由頂層 try/except 包覆。"""
        _t0 = time.time()

        # --- STEP 1: 意圖解析 ---
        print(f"{GREEN}STEP 1: 呼叫 Gemini 解析使用者意圖{RESET}")
        try:
            if not check_and_increment("llm_gemini"):
                raise Exception("LLM 每日使用上限已達")
            contents = (
                f"[目前時間：{current_time}]\n{user_text}" if current_time else user_text
            )
            with timing.time_llm("intent"):
                model_result = generate_with_retry(
                    lambda: self.client.models.generate_content(
                        model=self.model_name,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            system_instruction=IDENTIFY_INSTRUCTION_PROMPT,
                            response_mime_type="application/json",
                            response_schema=INTENT_RESPONSE_SCHEMA,
                        ),
                    ),
                    label="intent",
                )
            if model_result.usage_metadata:
                record_tokens(model_result.usage_metadata.total_token_count or 0)
            intent_data = self._parse_intent_json(model_result)
            print(f"[DEBUG] AI 解析意圖: {intent_data}")
        except Exception as e:
            print(f"{RED}STEP 1 ERROR:{e}{RESET}")
            # 意圖解析失敗時直接回傳 FALLBACK，不可假裝成「無條件搜尋」繼續往下執行：
            # SEARCH_BY_CRITERIA 在 location/style 皆為空時會比對「全部」店家
            # （filter_ramen_data 視為無篩選條件），可能推薦出與使用者所在地無關的店家
            # （例如資料庫中其他城市的記錄）。
            return self._fallback_result("系統忙碌中，請稍後再試。")
        print(f"{CYAN}[TIMER] STEP 1 完成，耗時 {time.time() - _t0:.1f}s{RESET}")

        intent = intent_data.get('intent', 'SEARCH_BY_CRITERIA').upper()

        # 「怎麼使用」這類詢問機器人用法的問句：不進 RAG，直接回固定功能說明。
        # 命中條件為「夠短」且「用法詞」且（整句就是用法詞 或 主詞指向本機器人）。
        _help_text = user_text.strip()
        if (
            len(_help_text) <= _HELP_MAX_LEN
            and _HELP_PATTERN.search(_help_text)
            and (
                _HELP_PATTERN.fullmatch(_help_text)
                or _HELP_SUBJECT_PATTERN.search(_help_text)
            )
        ):
            print(f"{YELLOW}STEP: 偵測到詢問機器人用法，回覆功能說明{RESET}")
            log_conversation(user_text, "HELP", intent_data)
            return self._fallback_result(_HELP_MESSAGE)

        # 「附近」語意但 Gemini 未解析出實際地名：純文字訊息協定層級無座標可用
        # （LINE TextMessage 事件不含經緯度，僅 LocationMessage 才有），不可讓
        # SEARCH_BY_CRITERIA 在 location 為空時當作「不限地區」搜尋全部店家。
        # 改回傳 LOCATION_REQUEST，由 app.py 附加 LINE 位置分享 Quick Reply
        # 按鈕，使用者一鍵分享後會觸發 handle_location 走 filter_by_location()
        # 真正以座標做 Haversine 過濾。
        # 另涵蓋「我想吃拉麵」這類地區與口味皆未指定的查詢：兩個條件都空時
        # filter_ramen_data() 同樣會比對「全部」店家而隨機抽出無關店家，與
        # 「附近」情境是同一個失效模式，故一併導向位置分享流程。
        # 有口味無地區（例如「沾麵推薦」）不在此列——口味本身已是有效篩選條件，
        # 由 filter_ramen_data() 的海外店家排除機制處理即可。
        if (
            intent == "SEARCH_BY_CRITERIA"
            and not intent_data.get("location")
            and (
                _NEAR_ME_PATTERN.search(user_text)
                or _style_or_none(intent_data.get("style")) is None
            )
        ):
            print(f"{YELLOW}STEP: 無可用的地區條件，請使用者分享位置{RESET}")
            return self._fallback_result(
                "請分享您的目前位置，我幫您找附近的拉麵店！",
                ui_tag="LOCATION_REQUEST",
            )

        # --- STEP 2: Skill 執行 ---
        _t2 = time.time()
        print(f"{GREEN}STEP 2: 執行 Skill — {intent}{RESET}")
        knowledge_query = intent_data.get('query') or user_text
        try:
            if intent == "GET_SPECIFIC_INFO":
                shop_name = (
                    intent_data.get("shop_name") or intent_data.get("location")
                )
                loc = intent_data.get("location") or ""
                result_data = self.info_skill.get_shop_info(shop_name, loc)
                results = [result_data] if result_data else []
            elif intent == 'KNOWLEDGE_QUERY':
                results = []
            elif intent == "REPORT_ERROR":
                results = [{
                    "shop_name": intent_data.get("shop_name"),
                    "error_description": intent_data.get("query") or user_text,
                }]
            else:  # SEARCH_BY_CRITERIA
                results = filter_ramen_data(
                    intent_data, current_time=current_time, user_id=user_id
                )
        except Exception as e:
            print(f"{RED}STEP 2 ERROR:{e}{RESET}")
            results = []
        print(f"{CYAN}[TIMER] STEP 2 完成，耗時 {time.time() - _t2:.1f}s{RESET}")

        # --- STEP 3: 推薦文生成 / 知識庫回答 ---
        _t3 = time.time()
        print(f"{GREEN}STEP 3: 生成推薦文 / 知識庫回答{RESET}")
        recommendations = []
        knowledge_answer = None
        try:
            if intent == 'KNOWLEDGE_QUERY':
                knowledge_answer = self.knowledge_skill.answer(knowledge_query)
            elif intent == "REPORT_ERROR":
                pass  # 不需要推薦文，回報確認訊息由 app.py 處理
            elif results:
                if intent == "GET_SPECIFIC_INFO":
                    desc = results[0].get("description") or ""
                    if desc:
                        # 將店家資料（含 style/features/description）摘要為較長的介紹文字
                        # 摘要失敗時改用 style 作為簡短介紹，避免整段原始 description 直接顯示
                        summary = summarize_description(
                            results[0], self.client, self.model_name
                        )
                        recommendations = [summary or results[0].get("style") or ""]
                    else:
                        # 無預存描述（店家未收錄於知識庫），即時呼叫 Gemini 生成推薦文
                        recommendations = generate_recommendations(
                            results, self.client, self.model_name, num_shops=1
                        )
                else:
                    recommendations = generate_recommendations(
                        results, self.client, self.model_name, num_shops=3
                    )
        except Exception as e:
            print(f"{RED}STEP 3 ERROR:{e}{RESET}")
        print(f"{CYAN}[TIMER] STEP 3 完成，耗時 {time.time() - _t3:.1f}s，"
              f"dispatch 總耗時 {time.time() - _t0:.1f}s{RESET}")

        # 對話特徵埋點：非同步寫入雲端 conversation_logs（本地模式 no-op）
        log_conversation(user_text, intent, intent_data)

        return {
            "intent": intent,
            "data": results,
            "recommendations": recommendations,
            "ui_tag": intent_data.get('ui_tag', 'CAROUSEL'),
            "message": knowledge_answer if intent == 'KNOWLEDGE_QUERY' else None,
        }
