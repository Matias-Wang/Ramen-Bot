import json
import copy
from urllib.parse import quote

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
                "color": "#666666",
                "wrap": True,
                "style": "italic",
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

    name = shop.get('name', '未知店名')
    location = shop.get('location', '未知地區')
    style = shop.get('style', '未知口味')
    address = shop.get('address', '暫無地址')
    _desc = shop.get("description") or ""
    rec_text = recommendation if recommendation else (_desc[:80] if _desc else "點擊查看地圖了解更多。")
    image_url = shop.get('image_url')
    if not (image_url and image_url.startswith('https://')):
        image_url = None
    map_url = shop.get('map_url') or f"https://www.google.com/maps/search/?api=1&query={quote(name)}"

    body_contents = bubble['body']['contents']
    body_contents[0]['text'] = name
    body_contents[1]['text'] = f"{location} · {style}"

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
