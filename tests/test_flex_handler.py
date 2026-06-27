"""
測試 Flex Handler 的 UI 組裝邏輯。
涵蓋：單一 Bubble 生成、Carousel 組裝。
"""

import pytest
from core.flex_handler import get_flex_bubble, assemble_carousel


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
