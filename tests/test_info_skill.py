"""
測試 Info Skill 的店家比對邏輯。
涵蓋：本地店名模糊比對、Google Places API 回傳店名相似度驗證。
"""

from unittest.mock import MagicMock, patch

import pytest
import skills.info_skill as info_skill
from skills.info_skill import InfoSkill, _find_shop_by_name


# ─── _find_shop_by_name ─────────────────────────────────────────────────────

class TestFindShopByName:
    def test_exact_match(self):
        shops = [{"name": "麒麟創作拉麵坊", "location": "中山區"}]
        assert _find_shop_by_name(shops, "麒麟創作拉麵坊") == shops[0]

    def test_fuzzy_match_above_threshold(self):
        """口語化簡稱應模糊比對到完整店名"""
        shops = [{"name": "麒麟創作拉麵坊", "location": "中山區"}]
        assert _find_shop_by_name(shops, "麒麟拉麵") == shops[0]

    def test_no_match_below_threshold(self):
        shops = [{"name": "麒麟創作拉麵坊", "location": "中山區"}]
        assert _find_shop_by_name(shops, "完全不相關的店名XYZ") is None

    def test_empty_shop_name_returns_none(self):
        shops = [{"name": "麒麟創作拉麵坊", "location": "中山區"}]
        assert _find_shop_by_name(shops, "") is None


# ─── InfoSkill.get_shop_info：Places API 相似度驗證 ────────────────────────

@pytest.fixture
def info_skill_instance(monkeypatch) -> InfoSkill:
    """建立 InfoSkill 實例，stub 掉 GoogleMapsService 與本地資料讀寫。"""
    monkeypatch.setattr(info_skill, "USE_FIRESTORE", False)
    with patch.object(info_skill, "GoogleMapsService"):
        skill = InfoSkill()
    monkeypatch.setattr(skill, "_load_data", lambda: [])
    monkeypatch.setattr(skill, "_save_one_shop", lambda shop: None)
    return skill


class TestGetShopInfoSimilarityGuard:
    def test_unrelated_place_result_is_rejected(self, info_skill_instance):
        """查詢不存在的店名時，Places API 模糊比對到完全不相關的店家
        （例如火鍋店）應被視為查無此店，而非當作有效結果回傳。"""
        info_skill_instance.gmaps_service.get_shop_details = MagicMock(
            return_value={
                "name": "海底撈火鍋 慶城店",
                "place_id": "xyz",
                "rating": 4.7,
                "user_ratings_total": 12593,
                "formatted_address": "臺北市松山區慶城街1號",
                "photo_url": None,
            }
        )
        result = info_skill_instance.get_shop_info("海底撈拉麵宇宙總部", "")
        assert result is None

    def test_similar_place_result_is_accepted(self, info_skill_instance):
        """Places API 回傳店名與查詢店名高度相似時，應正常採用該結果。"""
        info_skill_instance.gmaps_service.get_shop_details = MagicMock(
            return_value={
                "name": "中山豚骨拉麵",
                "place_id": "abc",
                "rating": 4.5,
                "user_ratings_total": 100,
                "formatted_address": "臺北市中山區中山北路一段1號",
                "photo_url": None,
            }
        )
        result = info_skill_instance.get_shop_info("中山豚骨拉麵", "中山區")
        assert result is not None
        assert result["name"] == "中山豚骨拉麵"
