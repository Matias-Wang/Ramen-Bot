import json

# === 1. FM 變數：Flex Message 模板 ===
# 這是你確認過的設計範本，包含英雄圖、店名、地區口味、地址與 AI 推薦區塊
FM_TEMPLATE = """
{
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
      { "type": "text", "text": "{SHOP_NAME}", "weight": "bold", "size": "xl" },
      { "type": "text", "text": "{LOCATION} · {STYLE}", "size": "sm", "color": "#999999" },
      { "type": "text", "text": "{ADDRESS}", "size": "xs", "color": "#aaaaaa", "wrap": true },
      { "type": "separator", "margin": "lg" },
      {
        "type": "text", 
        "text": "{AI_RECOMMENDATION}", 
        "size": "sm", 
        "color": "#666666", 
        "wrap": true, 
        "style": "italic",
        "margin": "md"
      }
    ]
  },
  "footer": {
    "type": "box",
    "layout": "vertical",
    "contents": [
      {
        "type": "button",
        "style": "primary",
        "color": "#A52A2A",
        "action": {
          "type": "uri",
          "label": "查看地圖",
          "uri": "https://www.google.com/maps/search/?api=1&query={SHOP_NAME}"
        }
      }
    ]
  }
}
"""

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
def assemble_carousel(results, recommendation=None):
    """
    將多個 Bubble 組合成 Carousel。
    設定：results[:3] 確保最多只產出 3 筆資料。
    """
    bubbles = []
    # 嚴格限制最多 3 筆資料
    for s in results[:3]: 
        bubble = get_flex_bubble(s, recommendation)
        if bubble:
            bubbles.append(bubble)
    
    return {
        "type": "carousel",
        "contents": bubbles
    }

# === 4. FM 輔助函式 (Low-level) ===
def get_flex_bubble(shop, recommendation = None):
    try:
        # 將模板轉為字典操作
        bubble = json.loads(FM_TEMPLATE)
        
        # 安全填充資料
        name = shop.get('name') or '未知店名'
        bubble['body']['contents'][0]['text'] = name    # 店名
        bubble['body']['contents'][1]['text'] = f"{shop.get('location')} · {shop.get('style')}"    # 地區與口味
        bubble['body']['contents'][2]['text'] = shop.get('address', '暫無地址')    # 地址
        bubble['body']['contents'][4]['text'] = recommendation or "點擊查看地圖了解更多。"    # AI 推薦
        
        # 填充按鈕連結
        bubble['footer']['contents'][0]['action']['uri'] = f"https://www.google.com"
        # bubble['footer']['contents'][0]['action']['uri'] = f"https://www.google.com/maps/search/?api=1&query={name}"
        
        return bubble
    except Exception as e:
        print(f"FM 組裝失敗: {e}")
        return None 