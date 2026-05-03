import json
from typing import Any


def filter_ramen_data(intent_data: dict[str, Any]) -> list[dict]:
    """
    根據意圖資料從 JSON 中篩選符合條件的拉麵店。

    Parameters
    ----------
    intent_data : dict[str, Any]
        第一層 AI 輸出的意圖資料，包含 location、style 等欄位。

    Returns
    -------
    list[dict]
        符合條件的拉麵店資料清單。
    """
    try:
        with open('ramen_data.json', 'r', encoding='utf-8') as f:
            all_shops = json.load(f)
    except FileNotFoundError:
        print("錯誤：找不到 ramen_data.json 檔案")
        return []

    target_location = intent_data.get('location')
    target_style = intent_data.get('style')

    filtered_results = []
    for shop in all_shops:
        match_location = (target_location is None or shop['location'] == target_location)
        match_style = (target_style is None or shop['style'] == target_style)

        if match_location and match_style:
            filtered_results.append(shop)

    return filtered_results


if __name__ == "__main__":
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
