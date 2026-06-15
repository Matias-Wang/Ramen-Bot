"""
測試 Search Skill 的純邏輯函式。
涵蓋：Haversine 距離計算、店家摘要建構。
"""

import pytest
from skills.Search_skill import calculate_distance, build_shop_summary, _build_geocode_query


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
