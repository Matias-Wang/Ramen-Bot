"""
測試 AgentRouter.dispatch() 的意圖分發路徑。

以 unittest.mock.patch 取代所有外部依賴（Gemini、各 Skill、usage_tracker），
驗證每種 intent 均走到正確的 Skill，並回傳符合規格的結果結構。
"""

import json
from typing import Generator

import pytest
from unittest.mock import MagicMock, patch

_SAMPLE_SHOP = {
    "id": "shop_pipeline_001",
    "name": "流水線拉麵",
    "location": "台北市中山區",
    "style": "豚骨",
    "address": "台北市中山區中山北路一段1號",
    "coordinates": {"lat": 25.054, "lng": 121.520},
}


def _make_gemini_response(intent_dict: dict) -> MagicMock:
    """
    建立回傳固定意圖 JSON 的 Gemini 模擬回應。

    Parameters
    ----------
    intent_dict : dict
        欲模擬的意圖解析結果。

    Returns
    -------
    MagicMock
        具備 .text 屬性的模擬 Gemini 回應物件。
    """
    resp = MagicMock()
    resp.text = json.dumps(intent_dict, ensure_ascii=False)
    resp.usage_metadata = None
    return resp


@pytest.fixture()
def router() -> Generator:
    """
    建立 AgentRouter 實例，以 patch 取代所有外部依賴。

    Yields
    ------
    AgentRouter
        已注入所有外部依賴 mock 的 router 實例；patches 隨測試結束自動還原。
    """
    with patch("core.agent_router.genai"), \
         patch("core.agent_router.InfoSkill") as mock_info_cls, \
         patch("core.agent_router.KnowledgeSkill") as mock_knowledge_cls, \
         patch("core.agent_router.check_and_increment", return_value=True), \
         patch("core.agent_router.record_tokens"), \
         patch("skills.Search_skill.check_and_increment", return_value=True), \
         patch("skills.Search_skill.record_tokens"):
        # SEARCH_BY_CRITERIA／GET_SPECIFIC_INFO 路徑會呼叫
        # skills.Search_skill 的 generate_recommendations／
        # summarize_description，其內部使用該檔案自己 import 的
        # check_and_increment／record_tokens 參照，與 core.agent_router
        # 的參照是不同物件，須個別 patch，否則會寫入真實 log/usage.json。
        from core.agent_router import AgentRouter
        r = AgentRouter("test-model")
        r.info_skill = mock_info_cls.return_value
        r.knowledge_skill = mock_knowledge_cls.return_value
        yield r


# ─── SEARCH_BY_CRITERIA ───────────────────────────────────────────────────────

class TestDispatchSearchByCriteria:
    """SEARCH_BY_CRITERIA：呼叫 filter_ramen_data，回傳 Carousel 結構。"""

    def test_returns_correct_intent_and_shop_data(self, router, monkeypatch):
        router.client.models.generate_content.return_value = _make_gemini_response({
            "intent": "SEARCH_BY_CRITERIA",
            "location": "中山站",
            "style": None,
            "shop_name": None,
            "query": None,
            "ui_tag": "CAROUSEL",
        })
        monkeypatch.setattr(
            "core.agent_router.filter_ramen_data", lambda _: [_SAMPLE_SHOP]
        )
        monkeypatch.setattr(
            "core.agent_router.generate_recommendations",
            lambda shops, client, model, num_shops: ["推薦文A"],
        )

        result = router.dispatch("中山站附近的拉麵")

        assert result["intent"] == "SEARCH_BY_CRITERIA"
        assert result["data"] == [_SAMPLE_SHOP]
        assert result["recommendations"] == ["推薦文A"]
        assert result["ui_tag"] == "CAROUSEL"
        assert result["message"] is None

    def test_no_results_returns_empty_data(self, router, monkeypatch):
        router.client.models.generate_content.return_value = _make_gemini_response({
            "intent": "SEARCH_BY_CRITERIA",
            "location": "火星",
            "style": None,
            "shop_name": None,
            "query": None,
            "ui_tag": "CAROUSEL",
        })
        monkeypatch.setattr(
            "core.agent_router.filter_ramen_data", lambda _: []
        )
        monkeypatch.setattr(
            "core.agent_router.generate_recommendations",
            lambda shops, client, model, num_shops: [],
        )

        result = router.dispatch("火星有什麼拉麵")

        assert result["intent"] == "SEARCH_BY_CRITERIA"
        assert result["data"] == []
        assert result["recommendations"] == []


# ─── GET_SPECIFIC_INFO ────────────────────────────────────────────────────────

class TestDispatchGetSpecificInfo:
    """GET_SPECIFIC_INFO：呼叫 InfoSkill.get_shop_info，回傳 Bubble 結構。"""

    def test_returns_correct_intent_and_single_shop(self, router, monkeypatch):
        router.client.models.generate_content.return_value = _make_gemini_response({
            "intent": "GET_SPECIFIC_INFO",
            "location": None,
            "style": None,
            "shop_name": "流水線拉麵",
            "query": None,
            "ui_tag": "TEXT",
        })
        router.info_skill.get_shop_info.return_value = _SAMPLE_SHOP
        monkeypatch.setattr(
            "core.agent_router.summarize_description",
            lambda shop, client, model: "LLM 摘要推薦文",
        )

        result = router.dispatch("介紹一下流水線拉麵")

        assert result["intent"] == "GET_SPECIFIC_INFO"
        assert result["data"] == [_SAMPLE_SHOP]
        assert result["ui_tag"] == "TEXT"
        router.info_skill.get_shop_info.assert_called_once()

    def test_summarize_failure_falls_back_to_style(self, router, monkeypatch):
        """summarize_description 失敗（回傳空字串）時，應改用 style 作為簡短介紹文字。"""
        shop_with_desc = {**_SAMPLE_SHOP, "description": "很長的原始 IG 食記內容…"}
        router.client.models.generate_content.return_value = _make_gemini_response({
            "intent": "GET_SPECIFIC_INFO",
            "location": None,
            "style": None,
            "shop_name": "流水線拉麵",
            "query": None,
            "ui_tag": "TEXT",
        })
        router.info_skill.get_shop_info.return_value = shop_with_desc
        monkeypatch.setattr(
            "core.agent_router.summarize_description",
            lambda shop, client, model: "",
        )

        result = router.dispatch("介紹一下流水線拉麵")

        assert result["recommendations"] == [shop_with_desc["style"]]

    def test_shop_not_found_returns_empty_data(self, router):
        router.client.models.generate_content.return_value = _make_gemini_response({
            "intent": "GET_SPECIFIC_INFO",
            "shop_name": "不存在的店",
            "location": None,
            "style": None,
            "query": None,
            "ui_tag": "TEXT",
        })
        router.info_skill.get_shop_info.return_value = None

        result = router.dispatch("介紹不存在的店")

        assert result["intent"] == "GET_SPECIFIC_INFO"
        assert result["data"] == []


# ─── KNOWLEDGE_QUERY ──────────────────────────────────────────────────────────

class TestDispatchKnowledgeQuery:
    """KNOWLEDGE_QUERY：呼叫 KnowledgeSkill.answer，回傳文字 message。"""

    def test_returns_knowledge_message(self, router):
        router.client.models.generate_content.return_value = _make_gemini_response({
            "intent": "KNOWLEDGE_QUERY",
            "location": None,
            "style": None,
            "shop_name": None,
            "query": "豚骨和醬油的差別",
            "ui_tag": "TEXT",
        })
        router.knowledge_skill.answer.return_value = "豚骨湯底以豬骨熬製，醬油以醬油調味..."

        result = router.dispatch("豚骨和醬油有什麼差別")

        assert result["intent"] == "KNOWLEDGE_QUERY"
        assert result["message"] == "豚骨湯底以豬骨熬製，醬油以醬油調味..."
        assert result["data"] == []
        assert result["recommendations"] == []
        router.knowledge_skill.answer.assert_called_once()


# ─── REPORT_ERROR ─────────────────────────────────────────────────────────────

class TestDispatchReportError:
    """REPORT_ERROR：萃取 shop_name + error_description，不呼叫推薦文生成。"""

    def test_returns_correct_fields(self, router):
        router.client.models.generate_content.return_value = _make_gemini_response({
            "intent": "REPORT_ERROR",
            "location": None,
            "style": None,
            "shop_name": "流水線拉麵",
            "query": "地址寫錯了，應該是台北市大安區",
            "ui_tag": "TEXT",
        })

        result = router.dispatch("流水線拉麵的地址寫錯了")

        assert result["intent"] == "REPORT_ERROR"
        assert len(result["data"]) == 1
        assert result["data"][0]["shop_name"] == "流水線拉麵"
        assert "地址寫錯了" in result["data"][0]["error_description"]
        assert result["recommendations"] == []

    def test_no_shop_name_still_captures_description(self, router):
        """未指定店名時，error_description 仍應正確擷取。"""
        router.client.models.generate_content.return_value = _make_gemini_response({
            "intent": "REPORT_ERROR",
            "location": None,
            "style": None,
            "shop_name": None,
            "query": "某家店的評分看起來有問題",
            "ui_tag": "TEXT",
        })

        result = router.dispatch("某家店的評分看起來有問題")

        assert result["intent"] == "REPORT_ERROR"
        assert result["data"][0]["shop_name"] is None
        assert "評分看起來有問題" in result["data"][0]["error_description"]


# ─── 頂層例外 Fallback ─────────────────────────────────────────────────────────

class TestDispatchFallback:
    """STEP 1（意圖解析）發生例外時，應直接回傳 FALLBACK，不可假裝成合法的
    無條件 SEARCH_BY_CRITERIA 繼續往下執行（會比對到與使用者所在地無關的店家）。"""

    def test_invalid_gemini_json_returns_fallback(self, router):
        resp = MagicMock()
        resp.text = "這不是 JSON"
        resp.usage_metadata = None
        router.client.models.generate_content.return_value = resp

        result = router.dispatch("??")

        assert result["intent"] == "FALLBACK"
        assert result["data"] == []
        assert result["recommendations"] == []

    def test_gemini_api_error_returns_fallback(self, router):
        router.client.models.generate_content.side_effect = Exception(
            "503 UNAVAILABLE"
        )

        result = router.dispatch("現在這個時間有適合吃的拉麵嗎")

        assert result["intent"] == "FALLBACK"
        assert result["data"] == []
