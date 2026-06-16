"""
以真實 ramen_data.json 驗證 Flex Message 輸出內容的正確性。

涵蓋：
1. Bubble 欄位一致性 — name/location/style 與來源資料完全吻合
2. Carousel 對齊 — bubble 順序與 shops 清單嚴格一致，推薦文不會錯位
3. Description 交叉污染偵測 — 偵測 description 開頭是否含有其他店家的名稱
"""

import json
import os
import re

import pytest
from core.flex_handler import get_flex_bubble, assemble_carousel

RAMEN_DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "ramen_data.json"
)


@pytest.fixture(scope="module")
def real_shops() -> list[dict]:
    """
    載入真實 ramen_data.json 作為測試資料來源。

    Returns
    -------
    list[dict]
        完整店家清單。
    """
    with open(RAMEN_DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ─── Bubble 欄位一致性 ─────────────────────────────────────────────────────────

class TestBubbleFieldConsistency:
    """Flex bubble 的每個欄位必須與 ramen_data.json 的來源資料完全吻合。"""

    def test_name_field_matches_shop_name(self, real_shops):
        """bubble title 應等於 shop['name']（或 fallback '未知店名'）。"""
        mismatches = []
        for shop in real_shops:
            bubble = get_flex_bubble(shop)
            expected = shop.get("name") or "未知店名"
            actual = bubble["body"]["contents"][0]["text"]
            if actual != expected:
                mismatches.append(
                    f"shop_id={shop.get('id')} expected={expected!r} actual={actual!r}"
                )
        assert not mismatches, (
            f"{len(mismatches)} 筆 bubble name 不符：\n" + "\n".join(mismatches)
        )

    def test_location_appears_in_subtitle(self, real_shops):
        """subtitle 欄位必須包含 shop['location']。"""
        mismatches = []
        for shop in real_shops:
            loc = shop.get("location") or ""
            if not loc:
                continue
            bubble = get_flex_bubble(shop)
            subtitle = bubble["body"]["contents"][1]["text"]
            if loc not in subtitle:
                mismatches.append(
                    f"shop={shop.get('name')!r} loc={loc!r} subtitle={subtitle!r}"
                )
        assert not mismatches, (
            f"{len(mismatches)} 筆 location 不在 subtitle 中：\n"
            + "\n".join(mismatches)
        )

    def test_style_appears_in_subtitle_when_present(self, real_shops):
        """subtitle 欄位必須包含 shop['style']（若 style 非空）。"""
        mismatches = []
        for shop in real_shops:
            style = shop.get("style") or ""
            if not style:
                continue
            bubble = get_flex_bubble(shop)
            subtitle = bubble["body"]["contents"][1]["text"]
            if style not in subtitle:
                mismatches.append(
                    f"shop={shop.get('name')!r} style={style!r} subtitle={subtitle!r}"
                )
        assert not mismatches, (
            f"{len(mismatches)} 筆 style 不在 subtitle 中：\n"
            + "\n".join(mismatches)
        )

    def test_all_bubbles_have_map_button(self, real_shops):
        """每一個 bubble 的 footer 都必須有地圖按鈕。"""
        missing = []
        for shop in real_shops:
            bubble = get_flex_bubble(shop)
            labels = [
                c.get("action", {}).get("label", "")
                for c in bubble["footer"]["contents"]
            ]
            if not any("地圖" in l for l in labels):
                missing.append(shop.get("name"))
        assert not missing, f"{len(missing)} 筆店家的 bubble 缺少地圖按鈕：{missing}"

    def test_bubble_type_is_bubble(self, real_shops):
        """所有 bubble 的 type 欄位必須為 'bubble'（LINE Flex 格式要求）。"""
        wrong_type = []
        for shop in real_shops:
            bubble = get_flex_bubble(shop)
            if bubble.get("type") != "bubble":
                wrong_type.append(shop.get("name"))
        assert not wrong_type, (
            f"{len(wrong_type)} 筆 bubble type 不是 'bubble'：{wrong_type}"
        )


# ─── Carousel 對齊 ─────────────────────────────────────────────────────────────

class TestCarouselAlignment:
    """carousel 的 bubble 順序與 shops 清單嚴格對齊，推薦文不會錯位。"""

    def test_carousel_type_is_carousel(self, real_shops):
        carousel = assemble_carousel(real_shops[:3])
        assert carousel["type"] == "carousel"

    def test_bubble_names_in_correct_order(self, real_shops):
        """取前三筆，確認 carousel bubble 順序與 shops 清單嚴格一致。"""
        sample = real_shops[:3]
        carousel = assemble_carousel(sample)
        for i, bubble in enumerate(carousel["contents"]):
            expected = sample[i].get("name") or "未知店名"
            actual = bubble["body"]["contents"][0]["text"]
            assert actual == expected, (
                f"bubble[{i}] 店名錯位：expected={expected!r} actual={actual!r}"
            )

    def test_recommendations_aligned_with_shops(self, real_shops):
        """推薦文順序必須與 shops 清單完全一致，不可錯位。"""
        sample = real_shops[:3]
        recs = ["推薦文0", "推薦文1", "推薦文2"]
        carousel = assemble_carousel(sample, recommendations=recs)
        for i, bubble in enumerate(carousel["contents"]):
            body_texts = [c.get("text", "") for c in bubble["body"]["contents"]]
            assert any(f"推薦文{i}" in t for t in body_texts), (
                f"bubble[{i}] 未找到 '推薦文{i}'，推薦文可能錯位"
            )

    def test_carousel_bubble_count_matches_input(self, real_shops):
        """carousel 的 bubble 數量應等於輸入 shops 數量（上限 3）。"""
        for n in (1, 2, 3):
            carousel = assemble_carousel(real_shops[:n])
            assert len(carousel["contents"]) == n


# ─── Description 交叉污染偵測 ──────────────────────────────────────────────────

def _extract_leading_bracket_name(desc: str) -> str | None:
    """
    從 description 開頭的 【】 或 《》 中提取店名。

    Parameters
    ----------
    desc : str
        店家描述文字。

    Returns
    -------
    str or None
        括號內的店名；若無此格式則回傳 None。
    """
    m = re.match(r"^[【《](.+?)[】》]", desc.strip())
    return m.group(1).strip() if m else None


def _is_name_variant(name_a: str, name_b: str) -> bool:
    """
    判斷兩個店名是否為變體關係（去除空白、括號與(暫停營業)後，一方是另一方的子字串）。

    Parameters
    ----------
    name_a : str
        第一個店名。
    name_b : str
        第二個店名。

    Returns
    -------
    bool
        為變體關係回傳 True。
    """
    _clean = lambda s: re.sub(r"[\s　\-–—（）()\(\)暫停營業]", "", s)
    a, b = _clean(name_a), _clean(name_b)
    return a == b or a in b or b in a


class TestDescriptionCrossContamination:
    """
    偵測 description 開頭的 【店名】 或 《店名》 是否指向其他店家。

    只比對 description 開頭括號內的名稱，避免將「描述中提及同集團其他店」
    或「分店/暫停字樣造成的名稱差異」誤判為交叉污染。
    """

    def test_leading_bracket_name_matches_own_shop(self, real_shops):
        """
        description 以 【X】 或 《X》 開頭時，X 必須是本店的名稱或合理變體，
        不可以是其他店家的完整名稱。
        """
        shop_name_set = {s["name"] for s in real_shops if s.get("name")}
        mismatches = []
        for shop in real_shops:
            own_name = shop.get("name") or ""
            desc = (shop.get("description") or "").strip()
            if not desc:
                continue
            bracket_name = _extract_leading_bracket_name(desc)
            if not bracket_name:
                continue  # 無括號格式，略過
            if _is_name_variant(bracket_name, own_name):
                continue  # 同店名稱變體（分店字樣、空白差異等），略過
            # bracket_name 與 own_name 無關，但恰好是另一間店的名稱 → 交叉污染
            if bracket_name in shop_name_set:
                mismatches.append({
                    "shop": own_name,
                    "bracket_name": bracket_name,
                    "desc_preview": desc[:60],
                })
        assert not mismatches, (
            f"發現 {len(mismatches)} 筆 description 開頭店名與 shop.name 不符：\n"
            + "\n".join(
                f"  [{m['shop']}] 開頭為 [{m['bracket_name']}]："
                f"{m['desc_preview']!r}"
                for m in mismatches
            )
        )
