import json
import re
import google.generativeai as genai
from skills.Search_skill import filter_ramen_data, generate_recommendations
from skills.info_skill import InfoSkill
from prompts import IDENTIFY_INSTRUCTION_PROMPT
from log.usage_tracker import check_and_increment, record_tokens

# 顏色變數（用於 Router 內部的錯誤輸出）
RED = '\033[91m'
RESET = '\033[0m'

class AgentRouter:
    """
    意圖分發中樞 (Agentic Router)。
    """

    def __init__(self, model_name):
        self.identify_model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=IDENTIFY_INSTRUCTION_PROMPT
        )
        self.recommend_model = genai.GenerativeModel(model_name=model_name)
        self.info_skill = InfoSkill()

    def _extract_text(self, obj):
        for attr in ('text', 'output', 'content', 'candidates'):
            if hasattr(obj, attr):
                return getattr(obj, attr)
        return str(obj)

    def _parse_intent_json(self, response):
        raw = self._extract_text(response)
        raw = re.sub(r'```(?:json)?', '', str(raw))
        m = re.search(r'(\{.*\})', raw, re.S)
        if not m:
            raise ValueError('無法從回應抽出 JSON，請檢查 Gemini 輸出格式。')
        return json.loads(m.group(1).replace("'", '"'))

    def dispatch(self, user_text):
        """
        解析意圖並執行對應技能，包含詳細錯誤回報。
        """
        # --- STEP 1: 意圖解析 ---
        try:
            if not check_and_increment("llm_gemini"):
                raise Exception("LLM 每日使用上限已達")
            model_result = self.identify_model.generate_content(user_text)
            if hasattr(model_result, "usage_metadata") and model_result.usage_metadata:
                record_tokens(model_result.usage_metadata.total_token_count or 0)
            intent_data = self._parse_intent_json(model_result)
            print(f"[DEBUG] AI 解析意圖: {intent_data}")
        except Exception as e:
            print(f"{RED} STEP 1 ERROR：{e}{RESET}")
            # 發生錯誤時的降級處理
            intent_data = {"intent": "SEARCH_BY_CRITERIA", "ui_tag": "TEXT"}
        
        intent = intent_data.get('intent', 'SEARCH_BY_CRITERIA').upper()
        
        # --- STEP 2: Skill 執行 ---
        try:
            if intent == 'GET_SPECIFIC_INFO':
                shop_name = intent_data.get('shop_name') or intent_data.get('location')
                result_data = self.info_skill.get_shop_info(shop_name, intent_data.get('location', ""))
                results = [result_data] if result_data else []
            elif intent == 'KNOWLEDGE_QUERY':
                results = []
            else: # SEARCH_BY_CRITERIA
                results = filter_ramen_data(intent_data)
        except Exception as e:
            print(f"{RED} STEP 2 ERROR：{e}{RESET}")
            results = []

        # --- STEP 3: 推薦文生成 ---
        recommendations = []
        try:
            if results and intent != 'KNOWLEDGE_QUERY':
                recommendations = generate_recommendations(results, self.recommend_model)
        except Exception as e:
            print(f"{RED} STEP 3 ERROR：{e}{RESET}")
            recommendations = []

        # 組裝最終結果回傳給 app.py
        return {
            "intent": intent,
            "data": results,
            "recommendations": recommendations,
            "ui_tag": intent_data.get('ui_tag', 'CAROUSEL'),
            "message": "百科功能開發中！" if intent == 'KNOWLEDGE_QUERY' else None
        }
