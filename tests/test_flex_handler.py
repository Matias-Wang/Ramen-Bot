"""
測試 Flex Handler 的 UI 組裝邏輯。
涵蓋：單一 Bubble 生成、Carousel 組裝。
"""

from datetime import datetime, timedelta, timezone

import pytest
from core.flex_handler import get_flex_bubble, assemble_carousel, _format_opening_hours

TZ = timezone(timedelta(hours=8))


def _period(open_day, open_hour, open_minute, close_day, close_hour, close_minute):
    """建立一筆 Places API 原生結構的營業時段。"""
    return {
        "open": {"day": open_day, "hour": open_hour, "minute": open_minute},
        "close": {"day": close_day, "hour": close_hour, "minute": close_minute},
    }


# ─── 測試輔助 ──────────────────────────────────────────────────────────────────

def _make_shop(**kwargs) -> dict:
    base = {
        "name": "一蘭拉麵",
        "location": "信義區",
        "style": "豚骨",
        "address": "台北市信義路五段7號",
    }
    base.update(kwargs)
    return base


# ─── get_flex_bubble ───────────────────────────────────────────────────────────

class TestGetFlexBubble:
    def test_returns_bubble_type(self):
        bubble = get_flex_bubble(_make_shop())
        assert bubble["type"] == "bubble"

    def test_name_in_body(self):
        bubble = get_flex_bubble(_make_shop(name="麵屋武藏"))
        assert bubble["body"]["contents"][0]["text"] == "麵屋武藏"

    def test_location_and_style_combined(self):
        bubble = get_flex_bubble(_make_shop(location="大安區", style="醬油"))
        assert "大安區" in bubble["body"]["contents"][1]["text"]
        assert "醬油" in bubble["body"]["contents"][1]["text"]

    def test_with_rating_shows_rating_text(self):
        bubble = get_flex_bubble(_make_shop(rating=4.5))
        body_texts = [c.get("text", "") for c in bubble["body"]["contents"]]
        assert any("4.5" in t for t in body_texts)

    def test_with_rating_and_review_count(self):
        bubble = get_flex_bubble(_make_shop(rating=4.3, user_ratings_total=1200))
        body_texts = [c.get("text", "") for c in bubble["body"]["contents"]]
        assert any("1,200" in t for t in body_texts)

    def test_without_rating_no_star_text(self):
        bubble = get_flex_bubble(_make_shop())
        body_texts = [c.get("text", "") for c in bubble["body"]["contents"]]
        assert not any("⭐" in t for t in body_texts)

    def test_recommendation_appears_in_body(self):
        bubble = get_flex_bubble(_make_shop(), recommendation="必吃！深夜限定濃厚版。")
        body_texts = [c.get("text", "") for c in bubble["body"]["contents"]]
        assert any("必吃！" in t for t in body_texts)

    def test_default_recommendation_when_none(self):
        bubble = get_flex_bubble(_make_shop(), recommendation=None)
        body_texts = [c.get("text", "") for c in bubble["body"]["contents"]]
        assert any("點擊查看地圖" in t for t in body_texts)

    def test_custom_image_url_replaces_default(self):
        url = "https://example.com/custom.jpg"
        bubble = get_flex_bubble(_make_shop(image_url=url))
        assert bubble["hero"]["url"] == url

    def test_map_button_always_in_footer(self):
        bubble = get_flex_bubble(_make_shop())
        labels = [c.get("action", {}).get("label", "") for c in bubble["footer"]["contents"]]
        assert any("地圖" in l for l in labels)

    def test_no_social_links_no_social_box(self):
        bubble = get_flex_bubble(_make_shop(social_links=[]))
        # footer 只有地圖按鈕，沒有 social_box
        assert len(bubble["footer"]["contents"]) == 1

    def test_one_social_link_creates_social_box(self):
        links = [{"label": "IG", "url": "https://www.instagram.com/test/"}]
        bubble = get_flex_bubble(_make_shop(social_links=links))
        social_box = bubble["footer"]["contents"][1]
        assert social_box["type"] == "box"
        assert len(social_box["contents"]) == 1

    def test_three_social_links_all_rendered(self):
        links = [
            {"label": "IG", "url": "https://www.instagram.com/a/"},
            {"label": "FB", "url": "https://www.facebook.com/a"},
            {"label": "官網", "url": "https://example.com"},
        ]
        bubble = get_flex_bubble(_make_shop(social_links=links))
        social_box = bubble["footer"]["contents"][1]
        assert len(social_box["contents"]) == 3

    def test_social_links_capped_at_three(self):
        """超過 3 個社群連結只取前 3 個"""
        links = [
            {"label": f"Link{i}", "url": f"https://example.com/{i}"}
            for i in range(5)
        ]
        bubble = get_flex_bubble(_make_shop(social_links=links))
        social_box = bubble["footer"]["contents"][1]
        assert len(social_box["contents"]) == 3

    # ─── None 欄位防呆（Firestore null 值）─────────────────────────────────────

    def test_none_name_does_not_crash(self):
        """name 為 None（Firestore null）時不應因 quote(None) 而崩潰，並回退預設文字"""
        bubble = get_flex_bubble(_make_shop(name=None))
        assert bubble["body"]["contents"][0]["text"] == "未知店名"

    def test_none_location_falls_back(self):
        bubble = get_flex_bubble(_make_shop(location=None, style=None))
        assert bubble["body"]["contents"][1]["text"] == "未知地區"

    def test_none_address_falls_back(self):
        shop = _make_shop(address=None)
        bubble = get_flex_bubble(shop)
        body_texts = [c.get("text", "") for c in bubble["body"]["contents"]]
        assert "暫無地址" in body_texts

    def test_none_fields_produce_valid_map_url(self):
        """name 為 None 時 map_url 仍應為有效字串（不可因 quote(None) 拋出例外）"""
        bubble = get_flex_bubble(_make_shop(name=None))
        map_button = bubble["footer"]["contents"][0]
        assert isinstance(map_button["action"]["uri"], str)


# ─── _format_opening_hours ─────────────────────────────────────────────────────

class TestFormatOpeningHours:
    # 週一 12:00 / 週一 22:00 / 週日 01:00 / 週日 05:00
    _MON_NOON = datetime(2026, 8, 10, 12, 0, tzinfo=TZ)
    _MON_NIGHT = datetime(2026, 8, 10, 22, 0, tzinfo=TZ)
    _SUN_1AM = datetime(2026, 8, 9, 1, 0, tzinfo=TZ)
    _SUN_5AM = datetime(2026, 8, 9, 5, 0, tzinfo=TZ)

    def test_no_data_returns_none(self):
        assert _format_opening_hours(None, self._MON_NOON) is None
        assert _format_opening_hours({}, self._MON_NOON) is None
        assert _format_opening_hours({"periods": []}, self._MON_NOON) is None

    def test_split_periods_joined(self):
        """下午有休息 → 兩段以「、」串接。"""
        hours = {"periods": [
            _period(1, 11, 0, 1, 14, 0),
            _period(1, 17, 0, 1, 22, 0),
        ]}
        assert _format_opening_hours(hours, self._MON_NOON) == "🕒 11:00-14:00、17:00-22:00"

    def test_single_period(self):
        """下午不休息 → 單一時段。"""
        hours = {"periods": [_period(1, 11, 0, 1, 20, 0)]}
        assert _format_opening_hours(hours, self._MON_NOON) == "🕒 11:00-20:00"

    def test_same_text_regardless_of_current_time(self):
        """只呈現時段，不因當下已打烊而改變顯示內容。"""
        hours = {"periods": [_period(1, 11, 0, 1, 14, 0)]}
        assert (
            _format_opening_hours(hours, self._MON_NOON)
            == _format_opening_hours(hours, self._MON_NIGHT)
            == "🕒 11:00-14:00"
        )

    def test_other_days_periods_excluded(self):
        """只列出今日（週一）的時段，週二的不應出現。"""
        hours = {"periods": [
            _period(1, 11, 0, 1, 14, 0),
            _period(2, 18, 0, 2, 21, 0),
        ]}
        assert _format_opening_hours(hours, self._MON_NOON) == "🕒 11:00-14:00"

    def test_day_off_when_no_period_today(self):
        hours = {"periods": [_period(2, 11, 0, 2, 14, 0)]}
        assert _format_opening_hours(hours, self._MON_NOON) == "🕒 今日公休"

    def test_cross_midnight_period_listed_on_open_day(self):
        """跨午夜時段歸屬於開始營業那天（週一 22:00 顯示 22:00-02:00）。"""
        hours = {"periods": [_period(1, 22, 0, 2, 2, 0)]}
        assert _format_opening_hours(hours, self._MON_NIGHT) == "🕒 22:00-02:00"

    def test_cross_midnight_carry_over_counts_as_today(self):
        """週六 22:00–週日 02:00，週日 01:00 仍在營業中，不可顯示為今日公休。"""
        hours = {"periods": [_period(6, 22, 0, 0, 2, 0)]}
        assert _format_opening_hours(hours, self._SUN_1AM) == "🕒 22:00-02:00"

    def test_cross_midnight_after_close_is_day_off(self):
        """同上店家，週日 05:00 已過打烊時間且今日無時段 → 今日公休。"""
        hours = {"periods": [_period(6, 22, 0, 0, 2, 0)]}
        assert _format_opening_hours(hours, self._SUN_5AM) == "🕒 今日公休"

    def test_24h_shop(self):
        """Places API 在 24 小時營業時省略 close 欄位。"""
        hours = {"periods": [{"open": {"day": 0, "hour": 0, "minute": 0}}]}
        assert _format_opening_hours(hours, self._MON_NOON) == "🕒 24 小時營業"


# ─── Bubble 上的營業時間呈現 ───────────────────────────────────────────────────

class TestBubbleOpeningHours:
    _HOURS = {"periods": [
        {"open": {"day": d, "hour": 11, "minute": 0},
         "close": {"day": d, "hour": 21, "minute": 0}}
        for d in range(7)
    ]}

    def test_hours_line_rendered(self):
        bubble = get_flex_bubble(_make_shop(opening_hours=self._HOURS))
        body_texts = [c.get("text", "") for c in bubble["body"]["contents"]]
        assert any("11:00-21:00" in t for t in body_texts)

    def test_no_hours_line_when_no_data(self):
        bubble = get_flex_bubble(_make_shop())
        body_texts = [c.get("text", "") for c in bubble["body"]["contents"]]
        assert not any("🕒" in t for t in body_texts)

    def test_hours_placed_before_separator(self):
        """營業時間應在分隔線之前（推薦文之上），不打斷既有版面順序。"""
        bubble = get_flex_bubble(_make_shop(opening_hours=self._HOURS))
        contents = bubble["body"]["contents"]
        sep_index = next(i for i, c in enumerate(contents) if c["type"] == "separator")
        hours_index = next(
            i for i, c in enumerate(contents) if "11:00-21:00" in c.get("text", "")
        )
        assert hours_index == sep_index - 1

    def test_recommendation_still_aligned_with_rating(self):
        """有評分時，插入營業時間不應打亂推薦文的配對。"""
        shop = _make_shop(rating=4.5, user_ratings_total=100, opening_hours=self._HOURS)
        bubble = get_flex_bubble(shop, recommendation="濃厚豚骨必吃。")
        body_texts = [c.get("text", "") for c in bubble["body"]["contents"]]
        assert any("⭐ 4.5" in t for t in body_texts)
        assert any("濃厚豚骨必吃。" == t for t in body_texts)
        assert any("台北市信義路五段7號" == t for t in body_texts)

    def test_recommendation_still_aligned_without_rating(self):
        """無評分時（body 少一列），插入營業時間同樣不應打亂配對。"""
        shop = _make_shop(opening_hours=self._HOURS)
        bubble = get_flex_bubble(shop, recommendation="清爽鹽味湯頭。")
        body_texts = [c.get("text", "") for c in bubble["body"]["contents"]]
        assert any("清爽鹽味湯頭。" == t for t in body_texts)
        assert any("台北市信義路五段7號" == t for t in body_texts)


# ─── assemble_carousel ─────────────────────────────────────────────────────────

class TestAssembleCarousel:
    def test_returns_carousel_type(self):
        result = assemble_carousel([_make_shop()])
        assert result["type"] == "carousel"

    def test_empty_list_gives_empty_carousel(self):
        result = assemble_carousel([])
        assert result["contents"] == []

    def test_one_shop_gives_one_bubble(self):
        result = assemble_carousel([_make_shop()])
        assert len(result["contents"]) == 1

    def test_three_shops_gives_three_bubbles(self):
        shops = [_make_shop(name=f"店家{i}") for i in range(3)]
        result = assemble_carousel(shops)
        assert len(result["contents"]) == 3

    def test_four_shops_capped_at_three(self):
        shops = [_make_shop(name=f"店家{i}") for i in range(4)]
        result = assemble_carousel(shops)
        assert len(result["contents"]) == 3

    def test_recommendations_aligned_with_shops(self):
        shops = [_make_shop(name=f"店家{i}") for i in range(2)]
        recs = ["推薦文A", "推薦文B"]
        result = assemble_carousel(shops, recommendations=recs)
        # 第一個 bubble 的 body 應包含 "推薦文A"
        body_texts_0 = [c.get("text", "") for c in result["contents"][0]["body"]["contents"]]
        body_texts_1 = [c.get("text", "") for c in result["contents"][1]["body"]["contents"]]
        assert any("推薦文A" in t for t in body_texts_0)
        assert any("推薦文B" in t for t in body_texts_1)

    def test_no_recommendations_uses_default(self):
        result = assemble_carousel([_make_shop()])
        body_texts = [c.get("text", "") for c in result["contents"][0]["body"]["contents"]]
        assert any("點擊查看地圖" in t for t in body_texts)
