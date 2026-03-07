import json

def filter_ramen_data(intent_data):
    """
    根據第一層 AI 輸出的意圖資料，從 JSON 中篩選出符合條件的拉麵店。
    """
    # 1. 讀取原始資料
    try:
        with open('ramen_data.json', 'r', encoding='utf-8') as f:
            all_shops = json.load(f)
    except FileNotFoundError:
        print("錯誤：找不到 ramen_data.json 檔案")
        return []
    
    # 2. 取得篩選條件（由第一層 AI 提供）
    # 如果 AI 沒抓到某個條件，預設為 None
    target_location = intent_data.get('location')
    target_style = intent_data.get('style')

    # 3. 執行過濾邏輯
    filtered_results = []
    for shop in all_shops:
        # 檢查地區是否符合 (若 AI 沒給地區，則視為不限)
        match_location = (target_location is None or shop['location'] == target_location)
        
        # 檢查口味是否符合 (若 AI 沒給口味，則視為不限)
        match_style = (target_style is None or shop['style'] == target_style)
        
        if match_location and match_style:
            filtered_results.append(shop)
            
    return filtered_results

if __name__ == "__main__":
# 模擬第一層 AI 輸出的結果
    test_intent = {
    "intent": "search",
    "location": "南港",
    "style": "豚骨",
    "ui_tag": "CAROUSEL"
    }

    results = filter_ramen_data(test_intent)
    print(f"找到 {len(results)} 間符合條件的店：")
    for r in results:
        print(f"- {r['name']} ({r['location']} / {r['style']})")