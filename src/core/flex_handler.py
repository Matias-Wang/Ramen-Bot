import copy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import quote

TAIPEI_TZ = timezone(timedelta(hours=8))

# === 1. Flex Message 基礎模板 ===
BASE_BUBBLE_STRUCTURE = {
    "type": "bubble",
    "hero": {
        "type": "image",
        "url": "https://images.unsplash.com/photo-1569718212165-3a8278d5f624?auto=format&fit=crop&w=800&q=80",
        "size": "full",
        "aspectRatio": "20:13",
        "aspectMode": "cover"
    },
    "body": {
        "type": "box",
        "layout": "vertical",
        "contents": [
            {"type": "text", "text": "店名", "weight": "bold", "size": "xl"},
            {"type": "text", "text": "地區 · 口味", "size": "sm", "color": "#999999"},
            {"type": "text", "text": "評分", "size": "sm", "color": "#f5a623"},
            {"type": "text", "text": "地址", "size": "xs", "color": "#aaaaaa", "wrap": True},
            {"type": "separator", "margin": "lg"},
            {
                "type": "text",
                "text": "推薦文案",
                "size": "sm",
                "color": "#333333",
                "wrap": True,
                "margin": "md"
            }
        ]
    },
    "footer": {
        "type": "box",
        "layout": "vertical",
        "spacing": "sm",
        "contents": []
    }
}

def _format_clock(time_point: Dict[str, Any]) -> str:
    """
    將 Places API 的 {"hour": 11, "minute": 30} 格式化為 "11:30"。

    Parameters
    ----------
    time_point : Dict[str, Any]
        Places API period 中的 open 或 close 節點。

    Returns
    -------
    str
        24 小時制的 "HH:MM" 字串。
    """
    return f"{time_point.get('hour', 0):02d}:{time_point.get('minute', 0):02d}"


def _format_opening_hours(
    opening_hours: Optional[Dict[str, Any]], now: Optional[datetime] = None
) -> Optional[str]:
    """
    組出 Flex Bubble 顯示用的「今日營業時段」單行文字。

    時段來源為 Places API 原生的 `periods`（day 0=週日），而非英文的
    `weekday_text`，故不需額外呼叫 API 即可產出中文顯示文字。
    只呈現時段本身（例如 `11:00-14:00、17:00-22:00`），不判斷當下是否營業中；
    店家下午有無休息，自然反映為一段或多段。

    Parameters
    ----------
    opening_hours : Optional[Dict[str, Any]]
        店家營業時間資料，須含 "periods" 鍵。
    now : Optional[datetime]
        用以決定「今日」是星期幾，預設為當下的台北時間。

    Returns
    -------
    Optional[str]
        顯示用文字；無營業時間資料時回傳 None（呼叫端整行省略）。
    """
    if not opening_hours:
        return None
    periods = opening_hours.get("periods") or []
    if not periods:
        return None

    if now is None:
        now = datetime.now(TAIPEI_TZ)

    # Places API 僅在 24 小時營業時省略 close 欄位
    if any("day" not in (p.get("close") or {}) for p in periods):
        return "🕒 24 小時營業"

    # Places API day 編碼：0=週日 ... 6=週六；Python weekday()：0=週一 ... 6=週日
    today = (now.weekday() + 1) % 7
    today_periods = [p for p in periods if (p.get("open") or {}).get("day") == today]

    if not today_periods:
        # 今日無起始時段，但昨日跨午夜的時段可能仍在進行中（深夜營業的拉麵店）。
        # 若不納入，深夜時段查詢會對正在營業的店家誤顯示「今日公休」。
        now_minutes = now.hour * 60 + now.minute
        today_periods = [
            p
            for p in periods
            if p["close"].get("day") == today
            and now_minutes
            < p["close"].get("hour", 0) * 60 + p["close"].get("minute", 0)
        ]

    if not today_periods:
        return "🕒 今日公休"

    texts = [
        f"{_format_clock(p.get('open') or {})}-{_format_clock(p['close'])}"
        for p in today_periods
    ]
    return f"🕒 {'、'.join(texts)}"


def assemble_carousel(results, recommendations=None):
    """
    將多個店家資料組合成 LINE Carousel (輪播) 結構。
    """
    bubbles = []
    
    for i, s in enumerate(results[:3]):
        current_rec = None
        if isinstance(recommendations, list) and i < len(recommendations):
            current_rec = recommendations[i]
            
        bubble = get_flex_bubble(s, current_rec)
        if bubble:
            bubbles.append(bubble)
    
    return {
        "type": "carousel",
        "contents": bubbles
    }

def get_flex_bubble(shop, recommendation=None):
    """
    根據單一店家資料生成 Flex Bubble 字典。
    """
    bubble = copy.deepcopy(BASE_BUBBLE_STRUCTURE)

    # 使用 `or` 而非 dict.get 預設值，避免欄位存在但值為 None（如 Firestore null）
    # 造成 Flex Message 出現非字串的 text 欄位，或 quote() 因 None 而崩潰
    name = shop.get('name') or '未知店名'
    location = shop.get('location') or '未知地區'
    style = shop.get('style') or ""
    address = shop.get('address') or '暫無地址'
    _desc = shop.get("description") or ""
    rec_text = recommendation if recommendation else (_desc if _desc else "點擊查看地圖了解更多。")
    # image_url 由 Search_skill.resolve_shop_images() 於回覆前檢查過存活狀態，
    # 已過期者會被置為 None，此處自動回退至模板的預設拉麵圖。
    image_url = shop.get('image_url')
    if not (image_url and image_url.startswith('https://')):
        image_url = None
    map_url = shop.get('map_url') or f"https://www.google.com/maps/search/?api=1&query={quote(name)}"

    body_contents = bubble['body']['contents']
    body_contents[0]['text'] = name
    body_contents[1]['text'] = f"{location} · {style}" if style else location

    rating = shop.get('rating')
    user_ratings_total = shop.get('user_ratings_total')
    if rating:
        rating_text = f"⭐ {rating}"
        if user_ratings_total:
            rating_text += f"（{user_ratings_total:,} 則評論）"
        body_contents[2]['text'] = rating_text
        body_contents[3]['text'] = address
        body_contents[5]['text'] = rec_text
    else:
        body_contents.pop(2)
        body_contents[2]['text'] = address
        body_contents[4]['text'] = rec_text

    # 營業時間插在分隔線之前（地址之後），且於上方索引配對完成後才插入，
    # 避免影響既有的 rating 有無所造成的索引位移邏輯。
    hours_text = _format_opening_hours(shop.get('opening_hours'))
    if hours_text:
        separator_index = next(
            i for i, c in enumerate(body_contents) if c['type'] == 'separator'
        )
        body_contents.insert(separator_index, {
            "type": "text",
            "text": hours_text,
            "size": "xs",
            "color": "#666666",
            "wrap": True,
            "margin": "sm"
        })

    if image_url:
        bubble['hero']['url'] = image_url

    map_button = {
        "type": "button",
        "style": "primary",
        "color": "#A52A2A",
        "height": "sm",
        "action": {
            "type": "uri",
            "label": "📍 查看地圖",
            "uri": map_url
        }
    }
    bubble['footer']['contents'].append(map_button)

    social_links_data = shop.get('social_links', [])
    if social_links_data and isinstance(social_links_data, list):
        social_box = {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "contents": []
        }
        
        for link_obj in social_links_data[:3]:
            label = link_obj.get('label')
            url = link_obj.get('url')
            # 過濾：無 label、無 url、非 https
            if not label or not url or not url.startswith('https://'):
                continue

            social_box['contents'].append({
                "type": "button",
                "style": "secondary",
                "height": "sm",
                "action": {
                    "type": "uri",
                    "label": label, # 移除長度限制
                    "uri": url
                }
            })
        
        if social_box['contents']:
            bubble['footer']['contents'].append(social_box)

    return bubble
