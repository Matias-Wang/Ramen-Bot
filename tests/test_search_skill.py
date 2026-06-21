"""
測試 Search Skill 的純邏輯函式。
涵蓋：Haversine 距離計算、店家摘要建構。
"""

import pytest
import skills.Search_skill as search_skill
from skills.Search_skill import (
    calculate_distance,
    build_shop_summary,
    _build_geocode_query,
    filter_ramen_data,
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
    ]


@pytest.fixture(autouse=True)
def _mock_search_dependencies(monkeypatch):
    """以固定測試資料與模擬 Geocoding 取代 _load_all_shops / _get_latlng_cached，
    避免讀取正式 ramen_data.json 或呼叫真實 Google Maps API。"""
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
    def test_style_only_filter_matches_style(self):
        result = filter_ramen_data({"style": "豚骨"})
        assert result
        assert all(s["style"] == "豚骨" for s in result)

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
