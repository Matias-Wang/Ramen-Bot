import json
import os
import asyncio
import re
import math
from typing import List, Dict, Any, Optional
import google.generativeai as genai
from prompts import RECOMMEND_PROMPT
from services.google_maps import GoogleMapsService
from usage_tracker import check_and_increment, record_tokens

# <使用者自訂變數>
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
BLUE = "\033[94m"
CYAN = "\033[96m"
MAGAENTA = "\033[95m"
RESET = "\033[0m"


def calculate_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    使用 Haversine 公式計算兩點間的距離 (公里)。

    Parameters
    ----------
    lat1, lng1 : float
        起點經緯度。
    lat2, lng2 : float
        終點經緯度。

    Returns
    -------
    float
        距離 (公里)。
    """
    R = 6371  # 地球半徑 (km)
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(
        math.radians(lat2)
    ) * math.sin(d_lng / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def filter_ramen_data(intent_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    核心篩選邏輯：根據 AI 解析出的意圖，從本地 JSON 篩選店家。
    包含地理位置經緯度比對邏輯。

    Parameters
    ----------
    intent_data : Dict[str, Any]
        由 Agent Router 解析出的意圖資料。

    Returns
    -------
    List[Dict[str, Any]]
        篩選後的店家列表。
    """
    print(f"{GREEN}STEP: 開始執行 Search Skill 篩選邏輯{RESET}")

    data_path = os.path.join("data", "ramen_data.json")
    try:
        with open(data_path, "r", encoding="utf-8") as f:
            all_shops = json.load(f)
    except FileNotFoundError:
        print(f"{RED}STEP ERROR: 找不到 {data_path} 檔案{RESET}")
        return []

    target_location = intent_data.get("location") or ""
    target_style = intent_data.get("style") or ""

    # 過濾 AI 誤抓的形容詞
    if target_style in ["推薦", "好吃", "熱門"]:
        target_style = ""

    print(f"{CYAN}[DEBUG] 原始條件 - 地區: '{target_location}', 口味: '{target_style}'{RESET}")

    # --- 地理位置處理 (Geocoding) ---
    target_coords = None
    if target_location:
        gmaps = GoogleMapsService()
        target_coords = gmaps.get_latlng(target_location)
        if target_coords:
            print(f"{GREEN}STEP: 取得目標座標成功 - {target_coords}{RESET}")
        else:
            print(f"{YELLOW}警告: 無法獲取 '{target_location}' 的經緯度，將使用字串模糊比對回退機制。{RESET}")

    filtered_results = []
    # 預設搜尋半徑 (公里)
    SEARCH_RADIUS_KM = 5.0

    for shop in all_shops:
        # 1. 口味比對 (Fuzzy Match)
        shop_style = shop.get("style", "")
        match_style = not target_style or (target_style in shop_style)

        if not match_style:
            continue

        # 2. 地區/位置比對
        match_location = False
        if not target_location:
            match_location = True
        elif target_coords:
            # 使用經緯度比對
            shop_coords = shop.get("coordinates")
            if shop_coords:
                dist = calculate_distance(
                    target_coords["lat"],
                    target_coords["lng"],
                    shop_coords["lat"],
                    shop_coords["lng"],
                )
                # 若在搜尋半徑內，則視為匹配
                if dist <= SEARCH_RADIUS_KM:
                    match_location = True
                    # 將距離資訊存入 shop 物件以便後續排序或顯示 (可選)
                    shop["distance_km"] = round(dist, 2)
            else:
                # 店家無座標資料，則回退至字串比對
                clean_target_loc = target_location.replace("市", "").replace("區", "").replace("縣", "")
                shop_loc = shop.get("location", "")
                if clean_target_loc in shop_loc or shop_loc in clean_target_loc:
                    match_location = True
        else:
            # 無目標座標資料，使用字串比對
            clean_target_loc = target_location.replace("市", "").replace("區", "").replace("縣", "")
            shop_loc = shop.get("location", "")
            if clean_target_loc in shop_loc or shop_loc in clean_target_loc:
                match_location = True

        if match_location:
            filtered_results.append(shop)

    # 若有距離資訊，依距離排序
    if any("distance_km" in s for s in filtered_results):
        filtered_results.sort(key=lambda x: x.get("distance_km", 999))

    print(f"{GREEN}STEP: 篩選完成，共找到 {len(filtered_results)} 間店家{RESET}")
    return filtered_results


# --- AI 推薦文生成邏輯 ---


def build_shop_summary(shop: Dict[str, Any]) -> str:
    """構建店家摘要資訊以提供給 LLM"""
    name = shop.get("name") or "未知店名"
    loc = shop.get("location") or ""
    style = shop.get("style") or ""
    desc = shop.get("description") or ""
    features = shop.get("features") or []
    dist = shop.get("distance_km")
    
    feature_text = "、".join(features[:4]) if isinstance(features, list) else ""
    parts = [name, f"位於{loc}" if loc else "", f"風格：{style}" if style else ""]
    
    if dist:
        parts.insert(2, f"距離約 {dist} 公里")
        
    summary = "，".join(p for p in parts if p)
    if feature_text:
        summary += f"；特色：{feature_text}"
    if desc:
        summary += f"。簡介：{desc}"
    return summary


def get_one_recommendation(shop_summary: str, model: Any) -> str:
    """單筆推薦文生成 (同步轉非同步調用用)"""
    default = "點擊查看地圖了解更多。"
    try:
        if not check_and_increment("llm_gemini"):
            return default
        prompt = RECOMMEND_PROMPT.format(shop_summary=shop_summary)
        recommend_result = model.generate_content(
            prompt, generation_config={"temperature": 0.6, "max_output_tokens": 1200}
        )
        if hasattr(recommend_result, "usage_metadata") and recommend_result.usage_metadata:
            record_tokens(recommend_result.usage_metadata.total_token_count or 0)

        def _extract_text(obj):
            for attr in ("text", "output", "content", "candidates"):
                if hasattr(obj, attr):
                    return getattr(obj, attr)
            return str(obj)

        raw = _extract_text(recommend_result).strip()
        raw = re.sub(r"```\w*\s*", "", raw).strip()
        if not raw or any(c in raw for c in ["I will", "As an AI"]):
            return default
        return raw
    except Exception as e:
        print(f"{RED}STEP ERROR: 生成推薦文失敗: {e}{RESET}")
        return default


async def get_one_recommendation_async(shop_summary: str, model: Any):
    """將同步的生成過程包裝進非同步執行緒"""
    return await asyncio.to_thread(get_one_recommendation, shop_summary, model)


async def fetch_all_recommendations_async(summaries: List[str], model: Any):
    """並行獲取所有推薦文"""
    tasks = [get_one_recommendation_async(s, model) for s in summaries]
    return await asyncio.gather(*tasks, return_exceptions=True)


def generate_recommendations(shops_info: List[Dict[str, Any]], model: Any, num_shops: int = 3) -> List[str]:
    """
    對篩選出的店家生成 AI 推薦文。

    Parameters
    ----------
    shops_info : List[Dict[str, Any]]
        篩選後的店家資訊列表。
    model : Any
        Gemini 生成模型實例。
    num_shops : int, optional
        要生成的店家數量，預設為 3。

    Returns
    -------
    List[str]
        推薦文列表。
    """
    if not shops_info:
        return []

    selected = shops_info[:num_shops]
    summaries = [build_shop_summary(s) for s in selected]

    print(f"{GREEN}STEP: 開始並行生成 {len(selected)} 筆推薦文{RESET}")
    try:
        # 在現有的 event loop 中執行非同步任務
        # 若在同步環境下呼叫，則使用 asyncio.run (但在 app.py 或 processor.py 通常已是 async)
        try:
            loop = asyncio.get_running_loop()
            # 若已有 loop，則直接 await
            # 這裡為了通用性，若是在 script 直接跑，我們用 asyncio.run
            results = asyncio.run(fetch_all_recommendations_async(summaries, model))
        except RuntimeError:
            results = asyncio.run(fetch_all_recommendations_async(summaries, model))
            
        return [
            r if not isinstance(r, Exception) else "點擊查看地圖了解更多。"
            for r in results
        ]
    except Exception as e:
        print(f"{RED}STEP ERROR: 推薦文流程失敗: {e}{RESET}")
        return ["點擊查看地圖了解更多。"] * len(summaries)
