import json

# === 1. FM 變數：Flex Message 模板 ===
# 這是你確認過的設計範本，包含英雄圖、店名、地區口味、地址與 AI 推薦區塊
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

# === 2. 資料過濾邏輯 ===
def filter_ramen_data(intent):
    """
    根據意圖篩選資料庫內容。
    實作模糊比對以處理「地區」名稱不一致的問題。
    """
    # 載入資料庫（此處假設 ramen_data.json 與此檔同目錄）
    try:
        with open('ramen_data.json', 'r', encoding='utf-8') as f:
            ramen_database = json.load(f)
    except Exception as e:
        print(f"讀取資料庫失敗: {e}")
        return []

    target_loc = intent.get('location') or ""
    target_style = intent.get('style') or ""
    
    # 清理地區關鍵字（移除區、市、縣）
    clean_loc = target_loc.replace('市', '').replace('區', '').replace('縣', '')
    
    filtered = []
    for shop in ramen_database:
        shop_loc = shop.get('location', '')
        shop_style = shop.get('style', '')
        
        # 模糊比對地區與口味
        loc_match = not clean_loc or (clean_loc in shop_loc or shop_loc in clean_loc)
        style_match = not target_style or (target_style in shop_style)
        
        if loc_match and style_match:
            filtered.append(shop)
            
    return filtered

# === 3. FM 組裝主函式 (High-level) ===
def assemble_carousel(results, recommendations=None):
    """將多筆店家資料組合成輪播介面"""
    bubbles = []
    
    # 使用 enumerate 取得索引值 i
    # 原因：recommendations 是一個列表，我們需要透過 i 確保「第 1 家店」拿到「第 1 句推薦文」
    for i, s in enumerate(results[:3]):
        # 根據目前的索引 i 從列表中取出專屬該店的推薦字串
        current_rec = None
        if isinstance(recommendations, list) and i < len(recommendations):
            current_rec = recommendations[i]
            
        # 將對應的推薦文傳入輔助函式
        bubble = get_flex_bubble(s, current_rec)
        if bubble:
            bubbles.append(bubble)
    
    return {
        "type": "carousel",
        "contents": bubbles
    }

# === 4. FM 輔助函式 (Low-level) ===
def get_flex_bubble(shop, recommendation=None):
    """
    動態生成 Flex Message 氣泡 (完全基於字典操作)
    """
    # 1. 深度複製基礎結構，避免修改到原始藍圖
    import copy
    bubble = copy.deepcopy(BASE_BUBBLE_STRUCTURE)

    # 資料提取
    name = shop.get('name', '未知店名')
    location = shop.get('location', '未知地區')
    style = shop.get('style', '未知口味')
    address = shop.get('address', '暫無地址')
    rec_text = recommendation if recommendation else "點擊查看地圖了解更多。"

    # 2. 直接修改字典內容 (比字串替換更安全)
    # Body 部分
    body_contents = bubble['body']['contents']
    body_contents[0]['text'] = name
    body_contents[1]['text'] = f"{location} · {style}"
    body_contents[2]['text'] = address
    body_contents[4]['text'] = rec_text

    # 3. 建立「查看地圖」按鈕
    map_button = {
        "type": "button",
        "style": "primary",
        "color": "#A52A2A",
        "height": "sm",
        "action": {
            "type": "uri",
            "label": "📍 查看地圖",
            "uri": f"https://www.google.com/maps/search/?api=1&query={name}"
        }
    }
    bubble['footer']['contents'].append(map_button)

    # 4. 處理「Social Links」
    social_links_data = shop.get('social_links', [])
    if social_links_data and isinstance(social_links_data, list):
        social_box = {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "contents": []
        }
        
        
        # 直接遍歷列表中的每一個連結物件
        for link_obj in social_links_data[:3]: # 限制最多顯示 3 個按鈕避免 UI 跑版
            label = link_obj.get('label', '連結')
            url = link_obj.get('url', '[https://www.instagram.com/](https://www.instagram.com/)')
            
            social_box['contents'].append({
                "type": "button",
                "style": "secondary",
                "height": "sm",
                "action": {
                    "type": "uri",
                    "label": label, # 抓取物件中的 label 並限制長度
                    "uri": url          # 抓取物件中的 url
                }
            })
        
        # 只有在真的有按鈕時才加入 footer
        if social_box['contents']:
            bubble['footer']['contents'].append(social_box)

    return bubble