"""
測試店家照片代理服務 (core/photo_service.py)。
涵蓋：代理網址組裝、photo_name 快取優先、簽章網址記憶體快取、失敗回退。
"""

import pytest

import core.photo_service as photo_service
import skills.Search_skill as search_skill
from core.photo_service import photo_proxy_url, resolve_photo_url

_PLACE_ID = "ChIJtest123"
_PHOTO_NAME = f"places/{_PLACE_ID}/photos/AbCdEf"
_SIGNED_URL = "https://lh3.googleusercontent.com/place-photos/signed"


class _FakeGmaps:
    """記錄呼叫次數的假 GoogleMapsService。"""

    def __init__(self, photo_name=_PHOTO_NAME, photo_url=_SIGNED_URL):
        self._photo_name = photo_name
        self._photo_url = photo_url
        self.name_calls = 0
        self.url_calls = 0

    def get_photo_name_by_place_id(self, place_id):
        self.name_calls += 1
        return self._photo_name

    def get_photo_url(self, photo_name, max_height_px=800):
        self.url_calls += 1
        return self._photo_url


@pytest.fixture(autouse=True)
def _clear_cache():
    """每個測試前後清空簽章網址快取，避免跨測試殘留。"""
    photo_service._url_cache.clear()
    yield
    photo_service._url_cache.clear()


# ─── photo_proxy_url ───────────────────────────────────────────────────────────


class TestPhotoProxyUrl:
    def test_returns_none_without_env(self, monkeypatch):
        """未設定 PHOTO_PROXY_BASE 時回傳 None，由呼叫端回退至舊欄位。"""
        monkeypatch.delenv("PHOTO_PROXY_BASE", raising=False)
        assert photo_proxy_url(_PLACE_ID) is None

    def test_builds_url_from_env(self, monkeypatch):
        monkeypatch.setenv("PHOTO_PROXY_BASE", "https://bot.example.com")
        assert photo_proxy_url(_PLACE_ID) == f"https://bot.example.com/photo/{_PLACE_ID}"

    def test_strips_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("PHOTO_PROXY_BASE", "https://bot.example.com/")
        assert photo_proxy_url(_PLACE_ID) == f"https://bot.example.com/photo/{_PLACE_ID}"

    def test_returns_none_without_place_id(self, monkeypatch):
        monkeypatch.setenv("PHOTO_PROXY_BASE", "https://bot.example.com")
        assert photo_proxy_url(None) is None
        assert photo_proxy_url("") is None


# ─── resolve_photo_url ─────────────────────────────────────────────────────────


class TestResolvePhotoUrl:
    def test_empty_place_id_returns_none(self):
        assert resolve_photo_url("") is None

    def test_uses_stored_photo_name_without_details_call(self, monkeypatch):
        """店家資料已有 photo_name 時，不應再呼叫 Place Details。"""
        gmaps = _FakeGmaps()
        monkeypatch.setattr(search_skill, "_get_gmaps", lambda: gmaps)
        monkeypatch.setattr(
            search_skill,
            "_load_all_shops",
            lambda: [{"place_id": _PLACE_ID, "photo_name": _PHOTO_NAME}],
        )

        assert resolve_photo_url(_PLACE_ID) == _SIGNED_URL
        assert gmaps.name_calls == 0
        assert gmaps.url_calls == 1

    def test_fetches_and_persists_photo_name_when_missing(self, monkeypatch):
        """店家資料缺 photo_name 時呼叫 API，並把結果寫回供後續重用。"""
        gmaps = _FakeGmaps()
        shop = {"place_id": _PLACE_ID, "name": "測試拉麵"}
        monkeypatch.setattr(search_skill, "_get_gmaps", lambda: gmaps)
        monkeypatch.setattr(search_skill, "_load_all_shops", lambda: [shop])
        persisted = []
        monkeypatch.setattr(
            search_skill,
            "persist_shop_summary",
            lambda s, field, value: persisted.append((s["name"], field, value)),
        )

        assert resolve_photo_url(_PLACE_ID) == _SIGNED_URL
        assert gmaps.name_calls == 1
        assert persisted == [("測試拉麵", "photo_name", _PHOTO_NAME)]

    def test_unknown_place_id_still_resolves_via_api(self, monkeypatch):
        """place_id 不在店家資料中（例如已下架）仍可解析，只是不寫回。"""
        gmaps = _FakeGmaps()
        monkeypatch.setattr(search_skill, "_get_gmaps", lambda: gmaps)
        monkeypatch.setattr(search_skill, "_load_all_shops", lambda: [])
        monkeypatch.setattr(
            search_skill,
            "persist_shop_summary",
            lambda *a, **k: pytest.fail("查無店家時不應寫回"),
        )

        assert resolve_photo_url(_PLACE_ID) == _SIGNED_URL
        assert gmaps.name_calls == 1

    def test_second_call_hits_memory_cache(self, monkeypatch):
        """TTL 內第二次呼叫直接命中記憶體快取，完全不打 API。"""
        gmaps = _FakeGmaps()
        monkeypatch.setattr(search_skill, "_get_gmaps", lambda: gmaps)
        monkeypatch.setattr(
            search_skill,
            "_load_all_shops",
            lambda: [{"place_id": _PLACE_ID, "photo_name": _PHOTO_NAME}],
        )

        assert resolve_photo_url(_PLACE_ID) == _SIGNED_URL
        assert resolve_photo_url(_PLACE_ID) == _SIGNED_URL
        assert gmaps.url_calls == 1

    def test_expired_cache_refetches(self, monkeypatch):
        """快取到期後重新解析，取得新的簽章網址。"""
        gmaps = _FakeGmaps()
        monkeypatch.setattr(search_skill, "_get_gmaps", lambda: gmaps)
        monkeypatch.setattr(
            search_skill,
            "_load_all_shops",
            lambda: [{"place_id": _PLACE_ID, "photo_name": _PHOTO_NAME}],
        )

        resolve_photo_url(_PLACE_ID)
        # 手動把到期時戳撥到過去，模擬 TTL 已過
        url, _ = photo_service._url_cache[_PLACE_ID]
        photo_service._url_cache[_PLACE_ID] = (url, 0.0)

        resolve_photo_url(_PLACE_ID)
        assert gmaps.url_calls == 2

    def test_no_photo_name_returns_none(self, monkeypatch):
        """店家查無照片時回傳 None，由端點改用預設圖。"""
        gmaps = _FakeGmaps(photo_name=None)
        monkeypatch.setattr(search_skill, "_get_gmaps", lambda: gmaps)
        monkeypatch.setattr(search_skill, "_load_all_shops", lambda: [])

        assert resolve_photo_url(_PLACE_ID) is None
        assert gmaps.url_calls == 0

    def test_media_call_returning_none_returns_none(self, monkeypatch):
        """Media 呼叫失敗（例如配額用盡）時回傳 None，且不寫入快取。"""
        gmaps = _FakeGmaps(photo_url=None)
        monkeypatch.setattr(search_skill, "_get_gmaps", lambda: gmaps)
        monkeypatch.setattr(
            search_skill,
            "_load_all_shops",
            lambda: [{"place_id": _PLACE_ID, "photo_name": _PHOTO_NAME}],
        )

        assert resolve_photo_url(_PLACE_ID) is None
        assert _PLACE_ID not in photo_service._url_cache

    def test_exception_returns_none(self, monkeypatch):
        """底層拋例外時不外流，回傳 None 讓端點回退預設圖。"""

        def _boom():
            raise RuntimeError("API 掛了")

        monkeypatch.setattr(search_skill, "_get_gmaps", _boom)
        monkeypatch.setattr(search_skill, "_load_all_shops", lambda: [])

        assert resolve_photo_url(_PLACE_ID) is None


# ─── fetch_photo（串流代理：必須回位元組，不可用 302） ─────────────────────────


class _FakeResp:
    def __init__(self, status_code=200, content=b"JPEGBYTES", content_type="image/jpeg"):
        self.status_code = status_code
        self.content = content
        self.headers = {"Content-Type": content_type}


class TestFetchPhoto:
    """LINE 載入 hero 圖時不跟隨轉址，故端點必須自行取回位元組。"""

    def _stub_resolve(self, monkeypatch, url):
        monkeypatch.setattr(photo_service, "resolve_photo_url", lambda pid: url)

    def test_returns_image_bytes(self, monkeypatch):
        self._stub_resolve(monkeypatch, _SIGNED_URL)
        monkeypatch.setattr(
            photo_service.requests, "get", lambda *a, **k: _FakeResp()
        )
        assert photo_service.fetch_photo(_PLACE_ID) == (b"JPEGBYTES", "image/jpeg")

    def test_falls_back_to_default_image_when_no_url(self, monkeypatch):
        """解析不到店家照片時改抓預設圖，仍回傳位元組（不可回轉址）。"""
        self._stub_resolve(monkeypatch, None)
        requested = []

        def _get(url, *a, **k):
            requested.append(url)
            return _FakeResp(content=b"DEFAULT")

        monkeypatch.setattr(photo_service.requests, "get", _get)

        assert photo_service.fetch_photo(_PLACE_ID) == (b"DEFAULT", "image/jpeg")
        assert requested == [photo_service.DEFAULT_PHOTO_URL]

    def test_stale_cached_url_triggers_single_reresolve(self, monkeypatch):
        """快取內的簽章網址已失效時，清快取重解析一次再試。"""
        calls = {"resolve": 0, "get": 0}

        def _resolve(pid):
            calls["resolve"] += 1
            return _SIGNED_URL

        def _get(url, *a, **k):
            calls["get"] += 1
            # 第一次視為已失效（403），重解析後第二次成功
            return _FakeResp(403, b"", "text/html") if calls["get"] == 1 else _FakeResp()

        monkeypatch.setattr(photo_service, "resolve_photo_url", _resolve)
        monkeypatch.setattr(photo_service.requests, "get", _get)

        assert photo_service.fetch_photo(_PLACE_ID) == (b"JPEGBYTES", "image/jpeg")
        assert calls["resolve"] == 2
        assert calls["get"] == 2

    def test_non_image_content_type_rejected(self, monkeypatch):
        """回應非圖片（例如錯誤頁）時不可當成圖片回傳。"""
        self._stub_resolve(monkeypatch, None)
        monkeypatch.setattr(
            photo_service.requests,
            "get",
            lambda *a, **k: _FakeResp(200, b"<html>", "text/html"),
        )
        assert photo_service.fetch_photo(_PLACE_ID) is None

    def test_download_exception_returns_none(self, monkeypatch):
        self._stub_resolve(monkeypatch, None)

        def _boom(*a, **k):
            raise photo_service.requests.RequestException("網路壞了")

        monkeypatch.setattr(photo_service.requests, "get", _boom)
        assert photo_service.fetch_photo(_PLACE_ID) is None
