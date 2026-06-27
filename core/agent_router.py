import json
import os
import re
import time
from typing import Any

from google import genai
from google.genai import types

from skills.Search_skill import (
    filter_ramen_data,
    generate_recommendations,
    summarize_description,
)
from skills.info_skill import InfoSkill
from skills.knowledge_skill import KnowledgeSkill
from core.prompts import IDENTIFY_INSTRUCTION_PROMPT
from core.usage_tracker import check_and_increment, record_tokens

RED = '\033[91m'
GREEN = '\033[92m'
CYAN = '\033[96m'
RESET = '\033[0m'


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
        raw = self._extract_text(response)
        raw = re.sub(r'```(?:json)?', '', str(raw))
        m = re.search(r'(\{.*\})', raw, re.S)
        if not m:
            raise ValueError('無法從回應抽出 JSON，請檢查 Gemini 輸出格式。')
        return json.loads(m.group(1).replace("'", '"'))

    @staticmethod
    def _fallback_result(message: str = "系統發生未預期的錯誤，請稍後再試。") -> dict:
        """
        回傳標準降級結果，供頂層例外捕捉使用。

        Parameters
        ----------
        message : str
            顯示給使用者的錯誤提示。

        Returns
        -------
        dict
            intent 為 FALLBACK 的結果字典。
        """
        return {
            "intent": "FALLBACK",
            "data": [],
            "recommendations": [],
            "ui_tag": "TEXT",
            "message": message,
        }

    def dispatch(self, user_text: str) -> dict:
        """
        解析使用者輸入的意圖，並分發至對應技能執行。

        Parameters
        ----------
        user_text : str
            使用者原始輸入文字。

        Returns
        -------
        dict
            包含 intent、data、recommendations、ui_tag、message 的結果字典。
            若所有步驟均失敗則回傳 FALLBACK intent。
        """
        try:
            return self._dispatch_inner(user_text)
        except Exception as e:
            print(f"{RED}DISPATCH CRITICAL ERROR: {e}{RESET}")
            return self._fallback_result()

    def _dispatch_inner(self, user_text: str) -> dict:
        """dispatch 的核心邏輯，由頂層 try/except 包覆。"""
        _t0 = time.time()

        # --- STEP 1: 意圖解析 ---
        print(f"{GREEN}STEP 1: 呼叫 Gemini 解析使用者意圖{RESET}")
        try:
            if not check_and_increment("llm_gemini"):
                raise Exception("LLM 每日使用上限已達")
            model_result = self.client.models.generate_content(
                model=self.model_name,
                contents=user_text,
                config=types.GenerateContentConfig(
                    system_instruction=IDENTIFY_INSTRUCTION_PROMPT,
                ),
            )
            if model_result.usage_metadata:
                record_tokens(model_result.usage_metadata.total_token_count or 0)
            intent_data = self._parse_intent_json(model_result)
            print(f"[DEBUG] AI 解析意圖: {intent_data}")
        except Exception as e:
            print(f"{RED}STEP 1 ERROR:{e}{RESET}")
            intent_data = {"intent": "SEARCH_BY_CRITERIA", "ui_tag": "TEXT"}
        print(f"{CYAN}[TIMER] STEP 1 完成，耗時 {time.time() - _t0:.1f}s{RESET}")

        intent = intent_data.get('intent', 'SEARCH_BY_CRITERIA').upper()

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
                results = filter_ramen_data(intent_data)
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

        return {
            "intent": intent,
            "data": results,
            "recommendations": recommendations,
            "ui_tag": intent_data.get('ui_tag', 'CAROUSEL'),
            "message": knowledge_answer if intent == 'KNOWLEDGE_QUERY' else None,
        }
