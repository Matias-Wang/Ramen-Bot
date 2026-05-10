import json
import re
from typing import Any

import google.generativeai as genai

from skills.Search_skill import filter_ramen_data, generate_recommendations
from skills.info_skill import InfoSkill
from core.prompts import IDENTIFY_INSTRUCTION_PROMPT
from core.usage_tracker import check_and_increment, record_tokens

RED = '\033[91m'
GREEN = '\033[92m'
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
        self.identify_model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=IDENTIFY_INSTRUCTION_PROMPT
        )
        self.recommend_model = genai.GenerativeModel(model_name=model_name)
        self.info_skill = InfoSkill()

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
        """
        # --- STEP 1: 意圖解析 ---
        print(f"{GREEN}STEP 1: 呼叫 Gemini 解析使用者意圖{RESET}")
        try:
            if not check_and_increment("llm_gemini"):
                raise Exception("LLM 每日使用上限已達")
            model_result = self.identify_model.generate_content(user_text)
            if hasattr(model_result, "usage_metadata") and model_result.usage_metadata:
                record_tokens(model_result.usage_metadata.total_token_count or 0)
            intent_data = self._parse_intent_json(model_result)
            print(f"[DEBUG] AI 解析意圖: {intent_data}")
        except Exception as e:
            print(f"{RED}STEP 1 ERROR: {e}{RESET}")
            intent_data = {"intent": "SEARCH_BY_CRITERIA", "ui_tag": "TEXT"}

        intent = intent_data.get('intent', 'SEARCH_BY_CRITERIA').upper()

        # --- STEP 2: Skill 執行 ---
        print(f"{GREEN}STEP 2: 執行 Skill — {intent}{RESET}")
        try:
            if intent == 'GET_SPECIFIC_INFO':
                shop_name = intent_data.get('shop_name') or intent_data.get('location')
                result_data = self.info_skill.get_shop_info(shop_name, intent_data.get('location', ""))
                results = [result_data] if result_data else []
            elif intent == 'KNOWLEDGE_QUERY':
                results = []
            else:  # SEARCH_BY_CRITERIA
                results = filter_ramen_data(intent_data)
        except Exception as e:
            print(f"{RED}STEP 2 ERROR: {e}{RESET}")
            results = []

        # --- STEP 3: 推薦文生成 ---
        print(f"{GREEN}STEP 3: 生成推薦文{RESET}")
        recommendations = []
        try:
            if results and intent != 'KNOWLEDGE_QUERY':
                recommendations = generate_recommendations(results, self.recommend_model)
        except Exception as e:
            print(f"{RED}STEP 3 ERROR: {e}{RESET}")
            recommendations = []

        return {
            "intent": intent,
            "data": results,
            "recommendations": recommendations,
            "ui_tag": intent_data.get('ui_tag', 'CAROUSEL'),
            "message": "百科功能開發中！" if intent == 'KNOWLEDGE_QUERY' else None
        }
