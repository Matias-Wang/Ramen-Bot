"""
測試 core/recent_shops 的排除契約。

直接對函式測試而非透過 filter_ramen_data，才能精確控制候選數量，
驗證「排除」與「候選不足時放棄排除」兩條規則的邊界。
"""

import pytest

from core import recent_shops
from core.recent_shops import (
    exclude_recent,
    get_recent_shop_keys,
    record_shown_shops,
    shop_key,
)


@pytest.fixture(autouse=True)
def _clear_state():
    """每個測試前清空模組層級狀態，避免測試間互相污染。"""
    recent_shops._recent.clear()


def _shops(*names: str) -> list[dict]:
    """以店名快速建立測試用店家列表。"""
    return [{"id": n, "name": n} for n in names]


class TestShopKey:
    def test_prefers_place_id(self):
        """鍵值須對齊 _dedupe_by_place_id：同一家店的多筆口味變體 id 不同、
        place_id 相同，用 id 當鍵會讓同一家店逃過排除。"""
        assert shop_key({"place_id": "P1", "id": "abc", "name": "拉麵店"}) == "P1"

    def test_same_shop_different_variants_share_key(self):
        """實測資料有 33 組同 place_id 多筆（每筆是不同口味的記錄），
        這些必須被視為同一家店。"""
        a = {"place_id": "P1", "id": "111", "name": "拉麵公子"}
        b = {"place_id": "P1", "id": "222", "name": "拉麵公子"}
        assert shop_key(a) == shop_key(b)

    def test_falls_back_to_id_then_name(self):
        assert shop_key({"id": "abc", "name": "拉麵店"}) == "abc"
        assert shop_key({"name": "拉麵店"}) == "拉麵店"

    def test_missing_all_returns_empty(self):
        assert shop_key({}) == ""


class TestRecordAndRecall:
    def test_records_and_reads_back(self):
        record_shown_shops("U1", _shops("A", "B", "C"))
        assert get_recent_shop_keys("U1") == {"A", "B", "C"}

    def test_users_are_isolated(self):
        record_shown_shops("U1", _shops("A", "B", "C"))
        assert get_recent_shop_keys("U2") == set()

    def test_latest_record_replaces_previous(self):
        """只保留最近一次，不累積——否則反覆查詢同一地區會越推越少、終至無店可推。"""
        record_shown_shops("U1", _shops("A", "B", "C"))
        record_shown_shops("U1", _shops("D", "E", "F"))
        assert get_recent_shop_keys("U1") == {"D", "E", "F"}

    def test_expired_record_is_forgotten(self, monkeypatch):
        """超過 TTL 後應忘記，否則使用者隔天查同一地區會莫名被排除。"""
        record_shown_shops("U1", _shops("A", "B", "C"))
        monkeypatch.setattr(recent_shops, "_TTL_SECONDS", -1)
        assert get_recent_shop_keys("U1") == set()

    def test_empty_user_id_is_noop(self):
        record_shown_shops("", _shops("A"))
        assert get_recent_shop_keys("") == set()


class TestExcludeRecent:
    def test_excludes_recent_when_enough_remain(self):
        record_shown_shops("U1", _shops("A", "B", "C"))
        result = exclude_recent("U1", _shops("A", "B", "C", "D", "E", "F"))
        assert [s["id"] for s in result] == ["D", "E", "F"]

    def test_skips_exclusion_when_too_few_remain(self):
        """排除後不足 3 間時放棄排除——寧可重複，也不能少給結果。"""
        candidates = _shops("A", "B", "C", "D")
        record_shown_shops("U1", _shops("A", "B", "C"))
        result = exclude_recent("U1", candidates)
        assert [s["id"] for s in result] == ["A", "B", "C", "D"]

    def test_no_history_returns_input_unchanged(self):
        candidates = _shops("A", "B", "C")
        assert exclude_recent("U_never_seen", candidates) == candidates

    def test_keep_at_least_is_configurable(self):
        """候選只有 1 間時，把門檻降到 1 就會實際排除。"""
        record_shown_shops("U1", _shops("A", "B"))
        result = exclude_recent("U1", _shops("A", "B", "C"), keep_at_least=1)
        assert [s["id"] for s in result] == ["C"]
