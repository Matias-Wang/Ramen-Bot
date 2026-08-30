"""
測試 Search Skill 的純邏輯函式。
涵蓋：Haversine 距離計算、店家摘要建構。
"""

import threading
import time
from datetime import datetime

import pytest
import skills.Search_skill as search_skill
from skills.Search_skill import (
    calculate_distance,
    build_shop_summary,
    _build_geocode_query,
    filter_ramen_data,
    filter_by_location,
    is_open_at,
)


# ─── calculate_distance ────────────────────────────────────────────────────────

class TestCalculateDistance:
    def test_same_point_is_zero(self):
        """相同座標距離應為 0"""
        assert calculate_distance(25.047, 121.517, 25.047, 121.517) == pytest.approx(0.0)

    def test_symmetric(self):
        """A→B 與 B→A 距離相同"""
        d1 = calculate_distance(25.047, 121.517, 25.014, 121.467)
        d2 = calculate_distance(25.014, 121.467, 25.047, 121.517)
        assert d1 == pytest.approx(d2, rel=1e-6)

    def test_within_5km_boundary(self):
        """信義區→大安區約 2km，應在 5km 以內"""
        d = calculate_distance(25.0339, 121.5645, 25.0264, 121.5436)
        assert d < 5.0

    def test_beyond_5km(self):
        """台北→桃園約 30km，應超過 5km"""
        d = calculate_distance(25.047, 121.517, 24.993, 121.301)
        assert d > 5.0

    def test_returns_float(self):
        result = calculate_distance(25.0, 121.0, 25.1, 121.1)
        assert isinstance(result, float)


# ─── build_shop_summary ────────────────────────────────────────────────────────

class TestBuildShopSummary:
    def _make_shop(self, **kwargs) -> dict:
        """建立測試用店家資料的輔助方法"""
        base = {
            "name": "測試拉麵",
            "location": "信義區",
            "style": "豚骨",
        }
        base.update(kwargs)
        return base

    def test_basic_summary_contains_name_location_style(self):
        shop = self._make_shop()
        result = build_shop_summary(shop)
        assert "測試拉麵" in result
        assert "信義區" in result
        assert "豚骨" in result

    def test_distance_inserted_when_present(self):
        shop = self._make_shop(distance_km=1.5)
        result = build_shop_summary(shop)
        assert "1.5" in result
        assert "公里" in result

    def test_no_distance_when_absent(self):
        shop = self._make_shop()
        result = build_shop_summary(shop)
        assert "公里" not in result

    def test_features_included(self):
        shop = self._make_shop(features=["手打麵", "限量", "深夜營業"])
        result = build_shop_summary(shop)
        assert "手打麵" in result
        assert "限量" in result

    def test_features_capped_at_four(self):
        shop = self._make_shop(features=["A", "B", "C", "D", "E"])
        result = build_shop_summary(shop)
        assert "E" not in result

    def test_description_appended(self):
        shop = self._make_shop(description="限量20碗，售完為止")
        result = build_shop_summary(shop)
        assert "限量20碗" in result

    def test_missing_name_uses_fallback(self):
        shop = {}
        result = build_shop_summary(shop)
        assert "未知店名" in result

    def test_empty_features_list(self):
        shop = self._make_shop(features=[])
        result = build_shop_summary(shop)
        assert "特色" not in result


# ─── _build_geocode_query ──────────────────────────────────────────────────────

class TestBuildGeocodeQuery:
    def test_station_name_uses_mrt_prefix(self):
        """地名以「站」結尾時，應使用「台北捷運」前綴避免比對到同名公車站/地標"""
        assert _build_geocode_query("中山站") == "台北捷運中山站"

    def test_district_without_city_gets_taipei_prefix(self):
        """地名未包含市/縣時，預設補上「台北市」前綴"""
        assert _build_geocode_query("中山區") == "台北市中山區"

    def test_district_name_only_gets_taipei_prefix(self):
        assert _build_geocode_query("中山") == "台北市中山"

    def test_already_has_city_left_unchanged(self):
        assert _build_geocode_query("台北市中山區") == "台北市中山區"

    def test_already_has_county_left_unchanged(self):
        assert _build_geocode_query("新北市板橋區") == "新北市板橋區"

    def test_already_starts_with_taipei_left_unchanged(self):
        assert _build_geocode_query("台北中山區") == "台北中山區"


# ─── filter_ramen_data ──────────────────────────────────────────────────────

# 模擬 Geocoding 結果，避免測試呼叫真實 Google Maps API。
# key 為 _build_geocode_query() 轉換後的查詢字串。
_MOCK_GEOCODE_RESULTS = {
    "台北捷運中山站": {"lat": 25.0532, "lng": 121.5201},
    "台北捷運公館站": {"lat": 25.0148, "lng": 121.5340},
    "台北市中山區": {"lat": 25.0633, "lng": 121.5258},
    "台北市大安區": {"lat": 25.0264, "lng": 121.5436},
    # 「南港」這類未帶「區」字的行政區名，Geocoding 回傳的是行政區幾何中心，
    # 常離店家聚集處超過 2km。此座標刻意設在遠處以重現該情境。
    "台北市南港": {"lat": 25.0312, "lng": 121.6112},
}


def _make_test_shops() -> list[dict]:
    """建立測試用店家清單，涵蓋有座標/無座標、不同地區與口味。"""
    return [
        {
            "id": "shop_zhongshan_tonkotsu",
            "name": "中山豚骨拉麵",
            "location": "台北市中山區",
            "style": "豚骨",
            "coordinates": {"lat": 25.0540, "lng": 121.5205},
        },
        {
            "id": "shop_zhongshan_miso",
            "name": "中山味噌拉麵",
            "location": "台北市中山區",
            "style": "味噌",
            "coordinates": {"lat": 25.0520, "lng": 121.5195},
        },
        {
            "id": "shop_gongguan_tonkotsu",
            "name": "公館豚骨拉麵",
            "location": "台北市中正區",
            "style": "豚骨",
            "coordinates": {"lat": 25.0150, "lng": 121.5342},
        },
        {
            "id": "shop_daan_tonkotsu",
            "name": "大安豚骨拉麵",
            "location": "台北市大安區",
            "style": "豚骨",
            "coordinates": {"lat": 25.0260, "lng": 121.5440},
        },
        {
            "id": "shop_no_coords",
            "name": "中山無座標拉麵",
            "location": "台北市中山區",
            "style": "醬油",
            "coordinates": {"lat": None, "lng": None},
        },
        {
            "id": "shop_far_away",
            "name": "桃園拉麵",
            "location": "桃園市",
            "style": "豚骨",
            "coordinates": {"lat": 24.993, "lng": 121.301},
        },
        {
            "id": "shop_missing_location",
            "name": "缺地區欄位拉麵",
            "location": None,
            "style": "豚骨",
            "coordinates": {"lat": 25.0521, "lng": 121.5198},
        },
        {
            "id": "shop_closed",
            "name": "中山暫停拉麵(暫停營業)",
            "location": "台北市中山區",
            "style": "豚骨",
            "coordinates": {"lat": 25.0530, "lng": 121.5200},
        },
        {
            "id": "shop_overseas",
            "name": "大阪豚骨拉麵",
            "location": "大阪市",
            "style": "豚骨",
            "coordinates": {"lat": 34.6937, "lng": 135.5023},
        },
        {
            "id": "shop_nangang",
            "name": "南港拉麵",
            "location": "台北市南港區",
            "style": "醬油",
            # 離「台北市南港」的 geocode 中心約 2.67km，超出 2km 預設半徑
            "coordinates": {"lat": 25.0499, "lng": 121.5945},
        },
    ]


@pytest.fixture(autouse=True)
def _mock_search_dependencies(monkeypatch):
    """以固定測試資料與模擬 Geocoding 取代 _load_all_shops / _get_latlng_cached，
    避免讀取正式 ramen_data.json 或呼叫真實 Google Maps API。
    同時清空 recent_shops 的模組層級狀態，避免測試間互相污染。"""
    from core import recent_shops
    recent_shops._recent.clear()
    monkeypatch.setattr(search_skill, "_load_all_shops", _make_test_shops)
    monkeypatch.setattr(
        search_skill,
        "_get_latlng_cached",
        lambda query: _MOCK_GEOCODE_RESULTS.get(query),
    )


class TestFilterRamenDataLocationQueries:
    """涵蓋「OO站附近的拉麵」「OO區的拉麵」等地理位置問法。"""

    def test_mrt_station_query_returns_nearby_shops_sorted_by_distance(self):
        result = filter_ramen_data({"location": "中山站"})
        ids = [s["id"] for s in result]
        assert "shop_zhongshan_tonkotsu" in ids
        assert "shop_zhongshan_miso" in ids
        assert "shop_daan_tonkotsu" not in ids
        for s in result:
            assert "distance_km" in s
        distances = [s["distance_km"] for s in result]
        assert distances == sorted(distances)

    def test_different_mrt_station_query_not_hardcoded(self):
        """換一個捷運站查詢，確認邏輯非針對單一站名硬編碼"""
        result = filter_ramen_data({"location": "公館站"})
        ids = [s["id"] for s in result]
        assert "shop_gongguan_tonkotsu" in ids
        assert "shop_zhongshan_tonkotsu" not in ids

    def test_district_query_gets_taipei_prefix(self):
        result = filter_ramen_data({"location": "大安區"})
        ids = [s["id"] for s in result]
        assert "shop_daan_tonkotsu" in ids

    def test_geocode_failure_falls_back_to_string_match(self, monkeypatch):
        """Geocoding 失敗（回傳 None）時應回退至字串模糊比對"""
        monkeypatch.setattr(search_skill, "_get_latlng_cached", lambda q: None)
        result = filter_ramen_data({"location": "中山區"})
        ids = [s["id"] for s in result]
        assert "shop_no_coords" in ids
        assert all("distance_km" not in s for s in result)

    def test_shop_without_coords_falls_back_within_geocoded_query(self):
        """target_coords 存在時，無座標的店家仍應透過字串比對被納入"""
        result = filter_ramen_data({"location": "中山區"})
        ids = [s["id"] for s in result]
        assert "shop_no_coords" in ids

    def test_district_query_skips_geocoding_and_uses_string_match(self, monkeypatch):
        """行政區查詢應完全跳過 Geocoding，改用 location 欄位字串比對。

        實測真實 API：「中山區」Geocoding 回傳的幾何中心點離店家聚集處超過
        2km，且半徑放大到能涵蓋整區時會等量誤抓鄰近行政區店家（無安全半徑
        值），故行政區查詢不應呼叫 Geocoding，避免重現此問題。
        """
        calls = []
        monkeypatch.setattr(
            search_skill,
            "_get_latlng_cached",
            lambda q: calls.append(q) or {"lat": 0.0, "lng": 0.0},
        )
        result = filter_ramen_data({"location": "中山區"})
        ids = [s["id"] for s in result]
        assert calls == []
        assert "shop_zhongshan_tonkotsu" in ids
        assert "shop_zhongshan_miso" in ids
        assert "shop_daan_tonkotsu" not in ids
        assert all("distance_km" not in s for s in result)

    def test_district_query_excludes_shop_with_missing_location(self):
        """location 欄位為 None 的店家不應因空字串比對而誤配對任何行政區查詢。"""
        result = filter_ramen_data({"location": "中山區", "style": "豚骨"})
        ids = [s["id"] for s in result]
        assert "shop_missing_location" not in ids


class TestFilterRamenDataStyleAndCombined:
    def test_closed_shop_is_excluded(self):
        """店名標記「暫停營業」的店家不應出現在任何搜尋結果中。"""
        result = filter_ramen_data({"location": "中山區", "style": "豚骨"})
        ids = [s["id"] for s in result]
        assert "shop_closed" not in ids

    def test_style_only_filter_matches_style(self):
        result = filter_ramen_data({"style": "豚骨"})
        assert result
        assert all(s["style"] == "豚骨" for s in result)

    def test_overseas_shop_excluded_when_no_location(self):
        """未指定地區時不應推薦海外店家——使用者根本去不了。
        （資料庫收錄了使用者旅日時記錄的 22 筆日本店家）"""
        for _ in range(30):  # 結果為隨機抽選，重複多次確保不是碰巧沒抽到
            result = filter_ramen_data({"style": "豚骨"})
            assert "shop_overseas" not in [s["id"] for s in result]

    def test_bare_district_name_falls_back_to_string_match(self):
        """「南港」這類未帶「區」字的行政區名，Geocoding 回傳行政區幾何中心，
        離店家超過 2km 半徑而回 0 筆（實測正式資料 2.67km）。與 bug#8（中山區）
        同一失效模式，但 is_district_query 只認「區/市/縣」結尾，光禿禿的地名會漏掉。
        座標查無結果時應退回字串比對，而非直接回空。"""
        result = filter_ramen_data({"location": "南港"})
        assert "shop_nangang" in [s["id"] for s in result]

    def test_district_suffix_query_still_works(self):
        """對照組：帶「區」字時本來就走字串比對，行為不變。"""
        result = filter_ramen_data({"location": "南港區"})
        assert "shop_nangang" in [s["id"] for s in result]

    def test_string_fallback_not_triggered_when_radius_has_results(self):
        """半徑內有結果時不可觸發 fallback——否則會把遠處同名地區的店家一起帶進來。
        中山站 2km 內有店，結果中不應出現大安區的店家。"""
        result = filter_ramen_data({"location": "中山站"})
        ids = [s["id"] for s in result]
        assert "shop_zhongshan_tonkotsu" in ids
        assert "shop_daan_tonkotsu" not in ids

    def test_user_id_reaches_recent_shops_store(self):
        """驗證 user_id 確實從 filter_ramen_data 一路傳到 recent_shops，
        且推薦結果有被記錄下來——排除規則本身由 tests/test_recent_shops.py 覆蓋。
        （此處不驗「第二次換一批」：測試資料每個地區的店家數不足以支撐排除，
        硬灌資料只為讓測試通過並不誠實。）"""
        from core.recent_shops import get_recent_shop_keys

        result = filter_ramen_data({"location": "中山區"}, user_id="U_recent_1")
        assert result
        assert get_recent_shop_keys("U_recent_1") == {s["id"] for s in result}

    def test_exclusion_skipped_when_too_few_candidates(self):
        """候選不足 3 間時放棄排除——寧可重複，也不能因排除而少給結果。"""
        first = filter_ramen_data({"location": "南港"}, user_id="U_recent_4")
        second = filter_ramen_data({"location": "南港"}, user_id="U_recent_4")
        assert [s["id"] for s in first] == ["shop_nangang"]
        assert [s["id"] for s in second] == ["shop_nangang"]

    def test_without_user_id_nothing_is_recorded(self):
        """未傳 user_id（本機測試、離線腳本）時完全不碰 recent_shops。
        （不可用「查兩次結果相同」當斷言——南港只有 1 間候選，
        `keep_at_least=3` 必然放棄排除，那種斷言即使排除生效也會通過。）"""
        from core import recent_shops

        result = filter_ramen_data({"location": "南港"})
        assert [s["id"] for s in result] == ["shop_nangang"]
        assert recent_shops._recent == {}

    def test_overseas_shop_included_when_explicitly_queried(self):
        """明確查詢「大阪」時仍須查得到，排除機制不可誤傷。"""
        result = filter_ramen_data({"location": "大阪市"})
        assert "shop_overseas" in [s["id"] for s in result]

    def test_generic_style_keyword_is_ignored(self):
        """style 為「推薦」等形容詞時應視為未指定，不會過濾掉所有結果"""
        result = filter_ramen_data({"style": "推薦"})
        assert result

    def test_combined_location_and_style(self):
        result = filter_ramen_data({"location": "中山站", "style": "味噌"})
        ids = [s["id"] for s in result]
        assert ids == ["shop_zhongshan_miso"]

    def test_no_match_returns_empty_list(self):
        result = filter_ramen_data({"location": "中山站", "style": "不存在的口味"})
        assert result == []

    def test_no_filters_returns_capped_results(self):
        result = filter_ramen_data({})
        assert len(result) == 3

    def test_more_than_three_matches_capped_at_three(self):
        result = filter_ramen_data({"style": "豚骨"})
        assert len(result) == 3


# ─── is_open_at ────────────────────────────────────────────────────────────────

class TestIsOpenAt:
    """營業時間判斷；periods 採 Places API day 編碼（0=週日 ... 6=週六）。"""

    # 2026-07-06 為週一（weekday()=0 → Places day 1）
    _MON_NOON = datetime(2026, 7, 6, 12, 0)
    _MON_EVENING = datetime(2026, 7, 6, 15, 30)
    # 2026-07-11 為週六（weekday()=5 → Places day 6）
    _SAT_1AM = datetime(2026, 7, 11, 1, 0)

    def test_open_within_same_day_period(self):
        hours = {"periods": [
            {"open": {"day": 1, "hour": 11}, "close": {"day": 1, "hour": 14}}
        ]}
        assert is_open_at(hours, self._MON_NOON) is True

    def test_closed_outside_same_day_period(self):
        hours = {"periods": [
            {"open": {"day": 1, "hour": 11}, "close": {"day": 1, "hour": 14}}
        ]}
        assert is_open_at(hours, self._MON_EVENING) is False

    def test_cross_midnight_period_open_after_midnight(self):
        """週五 18:00 開到週六 02:00，週六 01:00 應判為營業中。"""
        hours = {"periods": [
            {"open": {"day": 5, "hour": 18}, "close": {"day": 6, "hour": 2}}
        ]}
        assert is_open_at(hours, self._SAT_1AM) is True

    def test_empty_periods_is_closed(self):
        assert is_open_at({"periods": []}, self._MON_NOON) is False

    def test_none_hours_is_closed(self):
        assert is_open_at(None, self._MON_NOON) is False

    def test_missing_periods_key_is_closed(self):
        assert is_open_at({}, self._MON_NOON) is False

    def test_no_close_field_means_always_open(self):
        """Places API 僅在 24 小時營業時省略 close，任何時間皆營業中。"""
        hours = {"periods": [{"open": {"day": 0, "hour": 0, "minute": 0}}]}
        assert is_open_at(hours, self._MON_EVENING) is True


# ─── filter_by_location（LINE 位置訊息定位推薦） ─────────────────────────────────

_ZHONGSHAN_CENTER = (25.0532, 121.5201)  # 中山站附近


class TestFilterByLocation:
    def test_returns_nearby_shops_sorted_by_distance(self):
        results, nearest_km = filter_by_location(*_ZHONGSHAN_CENTER, radius_km=2.0)
        ids = [s["id"] for s in results]
        assert "shop_zhongshan_tonkotsu" in ids
        assert "shop_zhongshan_miso" in ids
        assert "shop_far_away" not in ids  # 桃園約 30km，超出半徑
        distances = [s["distance_km"] for s in results]
        assert distances == sorted(distances)

    def test_caps_at_three_results(self):
        results, _ = filter_by_location(*_ZHONGSHAN_CENTER, radius_km=50.0)
        assert len(results) <= 3

    def test_excludes_shop_without_coordinates(self):
        results, _ = filter_by_location(*_ZHONGSHAN_CENTER, radius_km=50.0)
        ids = [s["id"] for s in results]
        assert "shop_no_coords" not in ids

    def test_excludes_closed_shop(self):
        results, _ = filter_by_location(*_ZHONGSHAN_CENTER, radius_km=2.0)
        ids = [s["id"] for s in results]
        assert "shop_closed" not in ids

    def test_style_filter_applied(self):
        results, _ = filter_by_location(*_ZHONGSHAN_CENTER, radius_km=2.0, style="味噌")
        ids = [s["id"] for s in results]
        assert ids == ["shop_zhongshan_miso"]

    def test_empty_result_still_reports_nearest_km(self):
        """半徑內找不到時，nearest_km 仍應回傳最近一間的距離供回覆訊息使用。"""
        results, nearest_km = filter_by_location(*_ZHONGSHAN_CENTER, radius_km=0.01)
        assert results == []
        assert nearest_km is not None
        assert nearest_km > 0.01

    def test_does_not_mutate_cached_shops(self):
        """distance_km 應寫在複本上，不汙染快取原始 dict。"""
        filter_by_location(*_ZHONGSHAN_CENTER, radius_km=2.0)
        assert all("distance_km" not in s for s in _make_test_shops())


# ─── filter_ramen_data：radius_km 覆寫與 open_now 過濾 ──────────────────────────

class TestFilterRamenDataRadiusOverride:
    def test_custom_radius_shrinks_result(self):
        """極小的自訂半徑應排除原本 2km 內會命中的店家。"""
        full = filter_ramen_data({"location": "中山站"})
        tiny = filter_ramen_data({"location": "中山站", "radius_km": 0.02})
        assert len(full) > len(tiny)

    def test_district_query_ignores_radius(self):
        """行政區查詢走字串比對、不使用半徑，radius_km 不應影響結果。"""
        result = filter_ramen_data({"location": "中山區", "radius_km": 0.02})
        ids = [s["id"] for s in result]
        assert "shop_zhongshan_tonkotsu" in ids


def _make_open_now_shops() -> list[dict]:
    """建立含 opening_hours 的測試店家：一間營業中、一間已打烊、一間無資料。"""
    return [
        {
            "id": "shop_open",
            "name": "營業中拉麵",
            "location": "台北市中山區",
            "style": "豚骨",
            "coordinates": {"lat": 25.0540, "lng": 121.5205},
            "opening_hours": {"periods": [
                {"open": {"day": 1, "hour": 11}, "close": {"day": 1, "hour": 14}}
            ]},
        },
        {
            "id": "shop_shut",
            "name": "打烊拉麵",
            "location": "台北市中山區",
            "style": "豚骨",
            "coordinates": {"lat": 25.0520, "lng": 121.5195},
            "opening_hours": {"periods": [
                {"open": {"day": 1, "hour": 17}, "close": {"day": 1, "hour": 21}}
            ]},
        },
        {
            "id": "shop_no_hours",
            "name": "無營業時間拉麵",
            "location": "台北市中山區",
            "style": "豚骨",
            "coordinates": {"lat": 25.0530, "lng": 121.5200},
        },
    ]


class TestFilterRamenDataOpenNow:
    _MON_NOON = "2026-07-06 12:00"  # 週一 12:00

    def test_open_now_keeps_only_currently_open(self, monkeypatch):
        monkeypatch.setattr(search_skill, "_load_all_shops", _make_open_now_shops)
        result = filter_ramen_data(
            {"location": "中山區", "open_now": True}, current_time=self._MON_NOON
        )
        ids = [s["id"] for s in result]
        assert ids == ["shop_open"]

    def test_open_now_excludes_shop_without_hours(self, monkeypatch):
        monkeypatch.setattr(search_skill, "_load_all_shops", _make_open_now_shops)
        result = filter_ramen_data(
            {"location": "中山區", "open_now": True}, current_time=self._MON_NOON
        )
        ids = [s["id"] for s in result]
        assert "shop_no_hours" not in ids

    def test_without_open_now_all_shops_included(self, monkeypatch):
        """未要求 open_now 時，營業時間不參與過濾。"""
        monkeypatch.setattr(search_skill, "_load_all_shops", _make_open_now_shops)
        result = filter_ramen_data({"location": "中山區"}, current_time=self._MON_NOON)
        ids = [s["id"] for s in result]
        assert set(ids) == {"shop_open", "shop_shut", "shop_no_hours"}


# ─── _get_latlng_cached 三層快取 ───────────────────────────────────────────────

# 於 import 時捕捉真實函式（模組級 autouse fixture 會把模組屬性換成 mock，
# 直接呼叫真實函式物件即可繞過，且其內部讀取的相依仍受本測試 monkeypatch 影響）。
_real_get_latlng_cached = search_skill._get_latlng_cached


class TestGetLatLngCachedTiering:
    """三層快取順序：記憶體 → Firestore（生產）→ Geocoding API。"""

    @pytest.fixture(autouse=True)
    def _clear_mem_cache(self):
        search_skill._geocode_cache.clear()
        yield
        search_skill._geocode_cache.clear()

    def test_memory_hit_skips_firestore_and_api(self, monkeypatch):
        """記憶體命中即返回，不查 Firestore、不打 API。"""
        search_skill._geocode_cache["台北市"] = {"lat": 25.0, "lng": 121.5}
        monkeypatch.setattr(
            search_skill, "_load_geocode_from_firestore",
            lambda loc: pytest.fail("記憶體命中時不應查 Firestore"),
        )
        monkeypatch.setattr(
            search_skill, "_get_gmaps",
            lambda: pytest.fail("記憶體命中時不應打 API"),
        )
        assert _real_get_latlng_cached("台北市") == {"lat": 25.0, "lng": 121.5}

    def test_firestore_hit_skips_api_and_populates_memory(self, monkeypatch):
        """Firestore 命中即返回，不打 API，並回填記憶體快取。"""
        monkeypatch.setattr(search_skill, "USE_FIRESTORE", True)
        monkeypatch.setattr(
            search_skill, "_load_geocode_from_firestore",
            lambda loc: {"lat": 25.1, "lng": 121.6},
        )
        monkeypatch.setattr(
            search_skill, "_get_gmaps",
            lambda: pytest.fail("Firestore 命中時不應打 API"),
        )
        result = _real_get_latlng_cached("信義區")
        assert result == {"lat": 25.1, "lng": 121.6}
        assert search_skill._geocode_cache["信義區"] == {"lat": 25.1, "lng": 121.6}

    def test_api_fallthrough_writes_back_to_firestore(self, monkeypatch):
        """兩層皆 miss 時打 API，成功結果回寫 Firestore。"""
        monkeypatch.setattr(search_skill, "USE_FIRESTORE", True)
        monkeypatch.setattr(
            search_skill, "_load_geocode_from_firestore", lambda loc: None
        )

        class _FakeGmaps:
            def get_latlng(self, loc):
                return {"lat": 24.9, "lng": 121.4}

        monkeypatch.setattr(search_skill, "_get_gmaps", lambda: _FakeGmaps())
        saved = []
        monkeypatch.setattr(
            search_skill, "_save_geocode_to_firestore",
            lambda loc, coords: saved.append((loc, coords)),
        )
        result = _real_get_latlng_cached("桃園")
        assert result == {"lat": 24.9, "lng": 121.4}
        assert saved == [("桃園", {"lat": 24.9, "lng": 121.4})]

    def test_api_failure_not_persisted(self, monkeypatch):
        """Geocoding 失敗（None）不寫入 Firestore（避免永久快取暫時性失敗）。"""
        monkeypatch.setattr(search_skill, "USE_FIRESTORE", True)
        monkeypatch.setattr(
            search_skill, "_load_geocode_from_firestore", lambda loc: None
        )

        class _FakeGmaps:
            def get_latlng(self, loc):
                return None

        monkeypatch.setattr(search_skill, "_get_gmaps", lambda: _FakeGmaps())
        monkeypatch.setattr(
            search_skill, "_save_geocode_to_firestore",
            lambda loc, coords: pytest.fail("失敗結果不應寫入 Firestore"),
        )
        assert _real_get_latlng_cached("不存在的地名XYZ") is None


# ─── _load_all_shops 快取（stale-while-revalidate） ────────────────────────────

# 同 _get_latlng_cached：於 import 時捕捉真實函式，繞過 autouse fixture 的 mock
_real_load_all_shops = search_skill._load_all_shops


@pytest.fixture
def _reset_shop_cache():
    """每個測試前後重置模組層級店家快取與刷新旗標。"""
    search_skill._shops_cache = []
    search_skill._shops_cache_time = 0.0
    search_skill._shops_refreshing = False
    yield
    search_skill._shops_cache = []
    search_skill._shops_cache_time = 0.0
    search_skill._shops_refreshing = False


class TestLoadAllShopsCache:
    """快取過期時應立即回傳舊資料、把重讀丟到背景，不阻塞使用者路徑。"""

    def test_cold_start_fetches_synchronously(self, monkeypatch, _reset_shop_cache):
        """完全無快取時（啟動預熱）同步讀取並寫入快取。"""
        monkeypatch.setattr(
            search_skill, "_fetch_all_shops", lambda: [{"id": "a"}]
        )
        assert _real_load_all_shops() == [{"id": "a"}]
        assert search_skill._shops_cache == [{"id": "a"}]

    def test_fresh_cache_does_not_fetch(self, monkeypatch, _reset_shop_cache):
        """TTL 內直接回傳快取，不呼叫資料源。"""
        search_skill._shops_cache = [{"id": "cached"}]
        search_skill._shops_cache_time = time.time()
        monkeypatch.setattr(
            search_skill,
            "_fetch_all_shops",
            lambda: pytest.fail("快取未過期時不應讀取資料源"),
        )
        assert _real_load_all_shops() == [{"id": "cached"}]

    def test_expired_cache_returns_stale_immediately(
        self, monkeypatch, _reset_shop_cache
    ):
        """過期時立即回傳舊資料，並於背景完成刷新。"""
        started = threading.Event()
        finished = threading.Event()

        def _slow_fetch():
            started.set()
            finished.wait(timeout=2.0)
            return [{"id": "new"}]

        search_skill._shops_cache = [{"id": "old"}]
        search_skill._shops_cache_time = time.time() - search_skill._CACHE_TTL_SECONDS - 1
        monkeypatch.setattr(search_skill, "_fetch_all_shops", _slow_fetch)

        # 背景讀取尚未完成，呼叫端仍立刻拿到舊資料（不阻塞）
        assert _real_load_all_shops() == [{"id": "old"}]
        assert started.wait(timeout=2.0), "背景刷新執行緒未啟動"

        finished.set()
        for _ in range(100):
            if search_skill._shops_cache == [{"id": "new"}]:
                break
            time.sleep(0.02)
        assert search_skill._shops_cache == [{"id": "new"}]
        assert search_skill._shops_refreshing is False

    def test_concurrent_expiry_starts_single_refresh(
        self, monkeypatch, _reset_shop_cache
    ):
        """連續多次過期呼叫只會啟動一個刷新執行緒。"""
        calls = []
        release = threading.Event()

        def _blocking_fetch():
            calls.append(1)
            release.wait(timeout=2.0)
            return [{"id": "new"}]

        search_skill._shops_cache = [{"id": "old"}]
        search_skill._shops_cache_time = time.time() - search_skill._CACHE_TTL_SECONDS - 1
        monkeypatch.setattr(search_skill, "_fetch_all_shops", _blocking_fetch)

        for _ in range(5):
            assert _real_load_all_shops() == [{"id": "old"}]

        release.set()
        time.sleep(0.2)
        assert len(calls) == 1

    def test_failed_refresh_keeps_old_cache(self, monkeypatch, _reset_shop_cache):
        """背景刷新讀取失敗（回空清單）時保留舊快取，不清空資料。"""
        search_skill._shops_cache = [{"id": "old"}]
        search_skill._shops_cache_time = time.time() - search_skill._CACHE_TTL_SECONDS - 1
        monkeypatch.setattr(search_skill, "_fetch_all_shops", lambda: [])

        assert _real_load_all_shops() == [{"id": "old"}]
        time.sleep(0.2)
        assert search_skill._shops_cache == [{"id": "old"}]
        assert search_skill._shops_refreshing is False


# ─── 圖片網址存活檢查與回覆後更新 ─────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class TestIsImageUrlAlive:
    def test_200_is_alive(self, monkeypatch):
        monkeypatch.setattr(
            search_skill.requests, "head", lambda *a, **k: _FakeResponse(200)
        )
        assert search_skill._is_image_url_alive("https://example.com/a.jpg") is True

    def test_403_is_dead(self, monkeypatch):
        monkeypatch.setattr(
            search_skill.requests, "head", lambda *a, **k: _FakeResponse(403)
        )
        assert search_skill._is_image_url_alive("https://example.com/a.jpg") is False

    def test_request_exception_is_dead(self, monkeypatch):
        def _raise(*a, **k):
            raise search_skill.requests.RequestException("boom")

        monkeypatch.setattr(search_skill.requests, "head", _raise)
        assert search_skill._is_image_url_alive("https://example.com/a.jpg") is False


class TestResolveShopImages:
    """回覆前只做檢查，不觸發任何 API 更新（更新交給回覆後）。"""

    def test_empty_list(self):
        assert search_skill.resolve_shop_images([]) == ([], [])

    def test_alive_image_kept_and_not_marked_stale(self, monkeypatch):
        monkeypatch.setattr(search_skill, "_is_image_url_alive", lambda url: True)
        shop = {"id": "s1", "name": "活著的店", "image_url": "https://x/a.jpg"}
        resolved, stale = search_skill.resolve_shop_images([shop])
        assert resolved[0]["image_url"] == "https://x/a.jpg"
        assert stale == []

    def test_dead_image_uses_default_and_is_marked_stale(self, monkeypatch):
        monkeypatch.setattr(search_skill, "_is_image_url_alive", lambda url: False)
        shop = {"id": "s2", "name": "過期的店", "image_url": "https://x/dead.jpg"}
        resolved, stale = search_skill.resolve_shop_images([shop])
        # 本次回覆改用預設圖（image_url 置為 None 觸發 flex_handler 的 fallback）
        assert resolved[0]["image_url"] is None
        # 原始 dict 不受影響（淺複製保護快取）
        assert shop["image_url"] == "https://x/dead.jpg"
        assert len(stale) == 1 and stale[0]["id"] == "s2"

    def test_missing_image_url_not_marked_stale(self, monkeypatch):
        """本來就沒有圖的店家不需要更新，不應列入待更新清單。"""
        monkeypatch.setattr(search_skill, "_is_image_url_alive", lambda url: False)
        shop = {"id": "s3", "name": "無圖的店", "image_url": None}
        resolved, stale = search_skill.resolve_shop_images([shop])
        assert resolved[0]["image_url"] is None
        assert stale == []

    def test_no_api_call_during_check(self, monkeypatch):
        """檢查階段絕不可呼叫 Places API——那是回覆後才做的事。"""
        monkeypatch.setattr(search_skill, "_is_image_url_alive", lambda url: False)
        monkeypatch.setattr(
            search_skill,
            "_get_gmaps",
            lambda: pytest.fail("回覆前不應呼叫 Google Maps API"),
        )
        shop = {"id": "s4", "name": "過期的店", "image_url": "https://x/dead.jpg"}
        search_skill.resolve_shop_images([shop])


class TestRefreshShopImage:
    """回覆後的更新：同時寫回 image_url 與 image_url_renew_date。"""

    def test_no_place_id_skips_api_call(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            search_skill, "persist_shop_fields", lambda *a, **k: called.append(a)
        )
        search_skill._refresh_shop_image({"name": "無 place_id", "place_id": None})
        assert called == []

    def test_successful_refresh_persists_url_and_renew_date(self, monkeypatch):
        class _FakeGmaps:
            def get_photo_by_place_id(self, place_id):
                return "https://fresh.example.com/new.jpg"

        monkeypatch.setattr(search_skill, "_get_gmaps", lambda: _FakeGmaps())
        persisted = {}
        monkeypatch.setattr(
            search_skill,
            "persist_shop_fields",
            lambda shop, fields: persisted.update(fields),
        )

        search_skill._refresh_shop_image({"name": "過期店家", "place_id": "abc123"})

        assert persisted["image_url"] == "https://fresh.example.com/new.jpg"
        # 更新時間必須一併寫入，作為觀測簽章網址壽命的依據
        renew = persisted["image_url_renew_date"]
        parsed = datetime.fromisoformat(renew)
        assert parsed.tzinfo is not None, "須為帶時區的 ISO 8601，避免時區歧義"

    def test_api_returns_none_does_not_persist(self, monkeypatch):
        class _FakeGmaps:
            def get_photo_by_place_id(self, place_id):
                return None

        monkeypatch.setattr(search_skill, "_get_gmaps", lambda: _FakeGmaps())
        called = []
        monkeypatch.setattr(
            search_skill, "persist_shop_fields", lambda *a, **k: called.append(a)
        )
        search_skill._refresh_shop_image({"name": "查無照片", "place_id": "xyz"})
        assert called == []


class TestPersistShopFields:
    """image_url 與 image_url_renew_date 必須同一次寫入，避免兩者不一致。"""

    def test_writes_all_fields_in_single_datasource_call(self, monkeypatch):
        monkeypatch.setattr(search_skill, "USE_FIRESTORE", True)
        cache_updates = []
        monkeypatch.setattr(
            search_skill,
            "_update_cache_field",
            lambda sid, name, field, value: cache_updates.append((field, value)),
        )

        written = {}

        class _Doc:
            def set(self, fields, merge=False):
                written.update(fields)
                written["_merge"] = merge

        class _Col:
            def document(self, doc_id):
                written["_doc_id"] = doc_id
                return _Doc()

        class _Db:
            def collection(self, name):
                written["_collection"] = name
                return _Col()

        import services.firestore_client as fc

        monkeypatch.setattr(fc, "get_db", lambda: _Db())

        search_skill.persist_shop_fields(
            {"id": "s1", "name": "測試店"},
            {"image_url": "https://x/new.jpg", "image_url_renew_date": "2026-08-02T00:00:00+00:00"},
        )

        assert written["_collection"] == "ramen_shops"
        assert written["_doc_id"] == "s1"
        assert written["_merge"] is True
        assert written["image_url"] == "https://x/new.jpg"
        assert written["image_url_renew_date"] == "2026-08-02T00:00:00+00:00"
        # 記憶體快取也要同步，讓同一 TTL 窗內的後續查詢看到新值
        assert set(cache_updates) == {
            ("image_url", "https://x/new.jpg"),
            ("image_url_renew_date", "2026-08-02T00:00:00+00:00"),
        }

    def test_empty_fields_is_noop(self, monkeypatch):
        monkeypatch.setattr(
            search_skill,
            "_update_cache_field",
            lambda *a: pytest.fail("空欄位不應觸發任何寫入"),
        )
        search_skill.persist_shop_fields({"id": "s1", "name": "店"}, {})
