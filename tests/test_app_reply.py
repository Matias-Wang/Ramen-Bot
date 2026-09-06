"""
測試 app.py 的回覆組裝（引導文與 push_message 內容）。

`app.py` 先前是全專案唯一零測試覆蓋的檔案——它在 import 當下就建立 LINE client、
AgentRouter，並執行 5 個會打真實 API 的連線預熱，故需要先把這些替換掉才能載入。
本檔案提供該 mock 骨架，並針對「引導文有沒有正確併進 push_message」做斷言，
這正是 2026-08-30 引導文上線時只能靠 LINE 實機驗證的部分。
"""

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def app_mod(monkeypatch):
    """
    以 mock 取代所有外部依賴後載入 app 模組。

    Yields
    ------
    module
        已載入且外部依賴皆為 mock 的 app 模組；測試結束後自 sys.modules 移除，
        避免污染其他測試。
    """
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "test-secret")
    monkeypatch.setenv("GEMINI_MODEL", "test-model")
    monkeypatch.setenv("LINE_TAG", "0")
    # 釘死本地後端：DATA_BACKEND=firestore 時 app.py 會啟動 Firestore gRPC 心跳
    # 背景執行緒，測試的隔離就變成「剛好 .env 是 local」而非無條件成立。
    monkeypatch.setenv("DATA_BACKEND", "local")

    sys.modules.pop("app", None)
    # app.py 於 import 當下會跑 5 個連線預熱，其中 Geocoding 與推薦文 Client Pool
    # 會**實際打外部 API**（不受 E2E_TEST_MODE 影響——那只跳過配額計數，不擋呼叫）。
    # 測試不得依賴或消耗外部服務，故一併攔截。
    with patch("core.agent_router.AgentRouter"), \
         patch("linebot.v3.messaging.ApiClient"), \
         patch("linebot.v3.messaging.MessagingApi"), \
         patch("linebot.v3.WebhookHandler"), \
         patch("skills.feedback_skill.check_pending_reports"), \
         patch("skills.Search_skill._get_latlng_cached", return_value=None), \
         patch("skills.Search_skill.init_rec_client_pool"), \
         patch("skills.Search_skill._load_all_shops", return_value=[]):
        module = importlib.import_module("app")
    # 攔截配額計數而非用 E2E_TEST_MODE 跳過：後者會讓 check_and_increment
    # 第一行就短路，導致「三處 push 有沒有真的傳 count=2」完全沒有看守。
    quota_calls: list = []
    monkeypatch.setattr(
        module,
        "check_and_increment",
        lambda key, count=1: quota_calls.append((key, count)) or True,
    )
    module._test_quota_calls = quota_calls
    yield module
    sys.modules.pop("app", None)


def _sent_messages(app_mod):
    """
    取出 push_message 送出的 messages 陣列。

    **必須斷言只呼叫一次**：`_reply_to_line` 的頂層 `except` 會在失敗時改推
    「系統忙碌中」，若不檢查次數，測試就會在那句錯誤訊息上做斷言而空轉通過
    （2026-08-30 實際發生過，見 review_20260830_2302 的 001）。

    Parameters
    ----------
    app_mod : module
        已載入的 app 模組。

    Returns
    -------
    list
        push_message 送出的訊息列表。
    """
    assert app_mod.line_bot_api.push_message.called, "push_message 未被呼叫"
    assert app_mod.line_bot_api.push_message.call_count == 1, (
        "push_message 被呼叫 %d 次——通常代表主路徑拋例外後走了 except 分支"
        % app_mod.line_bot_api.push_message.call_count
    )
    request_obj = app_mod.line_bot_api.push_message.call_args[0][0]
    return request_obj.messages


_SHOP = {
    "id": "shop_1",
    "name": "測試拉麵",
    "location": "臺北市中山區",
    "style": "豚骨",
    "address": "臺北市中山區測試路 1 號",
    "coordinates": {"lat": 25.05, "lng": 121.52},
}


# ─── _reply_to_line：Search Carousel ───────────────────────────────────────────

class TestReplyCarouselIntro:
    """SEARCH_BY_CRITERIA 的 Carousel 前應附一句引導文，且與 Flex 併在
    **同一次** push_message 送出（LINE 單次 push 可帶 5 則，故不增加 push 次數）。"""

    def _dispatch_result(self, shops):
        return {
            "intent": "SEARCH_BY_CRITERIA",
            "data": shops,
            "recommendations": ["推薦文"] * len(shops),
            "ui_tag": "CAROUSEL",
            "message": None,
        }

    def test_intro_text_precedes_flex_in_same_push(self, app_mod, monkeypatch):
        shops = [dict(_SHOP, id="s%d" % i) for i in range(3)]
        app_mod.router.dispatch.return_value = self._dispatch_result(shops)
        monkeypatch.setattr(app_mod, "resolve_shop_images", lambda d: (d, []))
        monkeypatch.setattr(app_mod, "refresh_shop_images_async", lambda s: None)

        app_mod._reply_to_line("中山區推薦的拉麵", "U1", "2026-08-30 19:00", 0.0)

        messages = _sent_messages(app_mod)
        assert len(messages) == 2, "引導文與 Flex 應併在同一次 push"
        assert messages[0].text == "為你找到 3 間拉麵店，看看有沒有喜歡的："
        assert messages[1].__class__.__name__ == "FlexMessage"
        assert app_mod.line_bot_api.push_message.call_count == 1

    def test_quota_counted_as_two_messages(self, app_mod, monkeypatch):
        """LINE 用量以訊息則數計算。引導文 + Flex 共 2 則，配額必須計 2，
        只計 1 會讓本地計數低於 LINE 實際計算的用量。"""
        shops = [dict(_SHOP, id="s%d" % i) for i in range(3)]
        app_mod.router.dispatch.return_value = self._dispatch_result(shops)
        monkeypatch.setattr(app_mod, "resolve_shop_images", lambda d: (d, []))
        monkeypatch.setattr(app_mod, "refresh_shop_images_async", lambda s: None)

        app_mod._reply_to_line("中山區推薦的拉麵", "U1", "2026-08-30 19:00", 0.0)

        assert ("line_api", 2) in app_mod._test_quota_calls

    def test_intro_reflects_actual_result_count(self, app_mod, monkeypatch):
        """引導文的數字必須是實際回傳筆數，不可寫死 3。"""
        shops = [dict(_SHOP, id="s%d" % i) for i in range(2)]
        app_mod.router.dispatch.return_value = self._dispatch_result(shops)
        monkeypatch.setattr(app_mod, "resolve_shop_images", lambda d: (d, []))
        monkeypatch.setattr(app_mod, "refresh_shop_images_async", lambda s: None)

        app_mod._reply_to_line("中山區推薦的拉麵", "U1", "2026-08-30 19:00", 0.0)

        assert "2 間" in _sent_messages(app_mod)[0].text


# ─── _reply_to_line：Info 單一 Bubble ──────────────────────────────────────────

class TestReplyInfoIntro:
    def test_intro_contains_shop_name(self, app_mod, monkeypatch):
        app_mod.router.dispatch.return_value = {
            "intent": "GET_SPECIFIC_INFO",
            "data": [_SHOP],
            "recommendations": ["介紹文"],
            "ui_tag": "TEXT",
            "message": None,
        }
        monkeypatch.setattr(app_mod, "resolve_shop_images", lambda d: (d, []))
        monkeypatch.setattr(app_mod, "refresh_shop_images_async", lambda s: None)

        app_mod._reply_to_line("測試拉麵好吃嗎", "U1", "2026-08-30 19:00", 0.0)

        messages = _sent_messages(app_mod)
        assert len(messages) == 2
        assert "測試拉麵" in messages[0].text
        assert messages[1].__class__.__name__ == "FlexMessage"

    def test_missing_shop_name_falls_back(self, app_mod, monkeypatch):
        """店名缺漏時引導文不可出現 None。"""
        shop = dict(_SHOP)
        shop["name"] = None
        app_mod.router.dispatch.return_value = {
            "intent": "GET_SPECIFIC_INFO",
            "data": [shop],
            "recommendations": ["介紹文"],
            "ui_tag": "TEXT",
            "message": None,
        }
        monkeypatch.setattr(app_mod, "resolve_shop_images", lambda d: (d, []))
        monkeypatch.setattr(app_mod, "refresh_shop_images_async", lambda s: None)

        app_mod._reply_to_line("介紹一下", "U1", "2026-08-30 19:00", 0.0)

        # 護欄：確認走的是正式路徑而非 except 分支。少了這兩行斷言，本測試會在
        # 「系統忙碌中」那句錯誤訊息上做斷言而空轉通過——_sent_messages 的
        # call_count == 1 抓不到，因為 except 分支同樣只推一則訊息
        # （review_20260830_2302 阻擋項 001；2026-09-06 補上時已雙向實測）。
        messages = _sent_messages(app_mod)
        assert len(messages) == 2
        assert messages[1].__class__.__name__ == "FlexMessage"
        assert "None" not in messages[0].text


# ─── _reply_to_line：不該加引導文的路徑 ────────────────────────────────────────

class TestRepliesWithoutIntro:
    """FALLBACK 與 KNOWLEDGE_QUERY 是純文字回覆，不應被硬加引導文。"""

    def test_fallback_sends_single_text(self, app_mod):
        app_mod.router.dispatch.return_value = {
            "intent": "FALLBACK",
            "data": [],
            "recommendations": [],
            "ui_tag": "TEXT",
            "message": "系統忙碌中，請稍後再試。",
        }

        app_mod._reply_to_line("測試", "U1", "2026-08-30 19:00", 0.0)

        messages = _sent_messages(app_mod)
        assert len(messages) == 1
        assert messages[0].text == "系統忙碌中，請稍後再試。"

    def test_location_request_attaches_quick_reply(self, app_mod):
        """LOCATION_REQUEST 須附上「分享位置」快速回覆按鈕。
        （放在本 class 是因為它同樣屬於「單則純文字、不加引導文」的路徑，
        額外多驗一個 quick_reply 欄位。）"""
        app_mod.router.dispatch.return_value = {
            "intent": "FALLBACK",
            "data": [],
            "recommendations": [],
            "ui_tag": "LOCATION_REQUEST",
            "message": "請分享您的目前位置，我幫您找附近的拉麵店！",
        }

        app_mod._reply_to_line("我想吃拉麵", "U1", "2026-08-30 19:00", 0.0)

        messages = _sent_messages(app_mod)
        assert len(messages) == 1
        assert messages[0].quick_reply is not None

    def test_knowledge_sends_single_text(self, app_mod):
        app_mod.router.dispatch.return_value = {
            "intent": "KNOWLEDGE_QUERY",
            "data": [],
            "recommendations": [],
            "ui_tag": "TEXT",
            "message": "札幌拉麵是……",
        }

        app_mod._reply_to_line("札幌拉麵的特色", "U1", "2026-08-30 19:00", 0.0)

        messages = _sent_messages(app_mod)
        assert len(messages) == 1
        assert messages[0].text == "札幌拉麵是……"


# ─── _reply_location：定位推薦 ─────────────────────────────────────────────────

class TestReplyLocationIntro:
    def test_intro_precedes_flex(self, app_mod, monkeypatch):
        shops = [dict(_SHOP, id="s%d" % i) for i in range(3)]
        monkeypatch.setattr(
            app_mod, "filter_by_location", lambda *a, **k: (shops, 1.2)
        )
        monkeypatch.setattr(
            app_mod, "generate_recommendations", lambda *a, **k: ["推薦文"] * 3
        )
        monkeypatch.setattr(app_mod, "resolve_shop_images", lambda d: (d, []))
        monkeypatch.setattr(app_mod, "refresh_shop_images_async", lambda s: None)

        app_mod._reply_location(25.05, 121.52, "U1", 0.0)

        messages = _sent_messages(app_mod)
        assert len(messages) == 2
        assert "找到你附近 3 間拉麵店：" == messages[0].text
        assert messages[1].__class__.__name__ == "FlexMessage"

    def test_no_result_sends_single_text_with_nearest(self, app_mod, monkeypatch):
        """半徑內無結果時回純文字，並告知最近一間的距離。"""
        monkeypatch.setattr(app_mod, "filter_by_location", lambda *a, **k: ([], 7.5))

        app_mod._reply_location(25.05, 121.52, "U1", 0.0)

        messages = _sent_messages(app_mod)
        assert len(messages) == 1
        assert "7.5" in messages[0].text

    def test_user_id_passed_to_filter(self, app_mod, monkeypatch):
        """user_id 須傳給 filter_by_location，否則排除近期推薦不會生效。"""
        seen = {}

        def _fake(lat, lng, *args, **kwargs):
            seen.update(kwargs)
            return [], None

        monkeypatch.setattr(app_mod, "filter_by_location", _fake)

        app_mod._reply_location(25.05, 121.52, "U_abc", 0.0)

        assert seen.get("user_id") == "U_abc"
