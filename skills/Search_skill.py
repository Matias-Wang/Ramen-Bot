import concurrent.futures
import copy
import json
import os
import time
import re
import math
import random
from typing import List, Dict, Any, Optional
from google import genai
from google.genai import types
from core.prompts import RECOMMEND_PROMPT
from services.google_maps import GoogleMapsService
from core.usage_tracker import check_and_increment, record_tokens

# <使用者自訂變數>
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
BLUE = "\033[94m"
CYAN = "\033[96m"
MAGAENTA = "\033[95m"
RESET = "\033[0m"

USE_FIRESTORE = os.getenv("DATA_BACKEND", "local") == "firestore"

# --- 模組層級快取 ---
_CACHE_TTL_SECONDS = 86400  # Firestore 店家快取 24 小時
_shops_cache: List[Dict[str, Any]] = []
_shops_cache_time: float = 0.0
_geocode_cache: Dict[str, Optional[Dict[str, float]]] = {}
_gmaps_instance: Optional[GoogleMapsService] = None

# 推薦文用的 Gemini client pool（預熱 3 個獨立實例，確保並行不序列化）
_rec_client_pool: List[Any] = []


def _get_gmaps() -> GoogleMapsService:
    """取得全域共用的 GoogleMapsService 實例（Singleton）。"""
    global _gmaps_instance
    if _gmaps_instance is None:
        _gmaps_instance = GoogleMapsService()
    return _gmaps_instance


def init_rec_client_pool(api_key: str, model_name: str, pool_size: int = 3) -> None:
    """
    建立並同時預熱 pool_size 個獨立 Gemini client。

    需在 app 啟動時呼叫。每個 client 對應一個推薦文生成執行緒，
    避免共用 client 的內部鎖導致序列化，同時利用預熱消除冷連線延遲。

    Parameters
    ----------
    api_key : str
        Gemini API 金鑰。
    model_name : str
        Gemini 模型名稱，用於預熱 API call。
    pool_size : int
        Pool 大小，預設為 3。
    """
    global _rec_client_pool

    def _create_and_warm(key: str) -> Any:
        c = genai.Client(api_key=key)
        c.models.generate_content(
            model=model_name,
            contents="hi",
            config=types.GenerateContentConfig(max_output_tokens=1),
        )
        return c

    with concurrent.futures.ThreadPoolExecutor(max_workers=pool_size) as ex:
        futures = [ex.submit(_create_and_warm, api_key) for _ in range(pool_size)]
        _rec_client_pool = [f.result() for f in futures]

    print(f"{GREEN}[STARTUP] Gemini Client Pool 初始化並預熱完成（{pool_size} 個實例）{RESET}")


def _load_all_shops() -> List[Dict[str, Any]]:
    """
    讀取全部店家資料，Firestore 結果快取 5 分鐘以減少 gRPC 呼叫次數。

    Returns
    -------
    List[Dict[str, Any]]
        店家清單。
    """
    global _shops_cache, _shops_cache_time
    now = time.time()

    if _shops_cache and (now - _shops_cache_time) < _CACHE_TTL_SECONDS:
        print(f"{CYAN}[CACHE] 使用 Firestore 店家快取（剩餘 "
              f"{int(_CACHE_TTL_SECONDS - (now - _shops_cache_time))} 秒）{RESET}")
        return _shops_cache

    if USE_FIRESTORE:
        try:
            from services.firestore_client import get_db
            _t_fs = time.time()
            db = get_db()
            fetched = [doc.to_dict() for doc in db.collection("ramen_shops").stream()]
            _shops_cache = fetched
            _shops_cache_time = time.time()
            print(f"{GREEN}STEP: Firestore 讀取完成，共 {len(_shops_cache)} 筆，"
                  f"耗時 {time.time() - _t_fs:.1f}s{RESET}")
        except Exception as e:
            print(f"{RED}STEP ERROR: Firestore 讀取失敗: {e}{RESET}")
            return _shops_cache if _shops_cache else []
    else:
        data_path = os.path.join("data", "ramen_data.json")
        try:
            with open(data_path, "r", encoding="utf-8") as f:
                _shops_cache = json.load(f)
            _shops_cache_time = time.time()
        except FileNotFoundError:
            print(f"{RED}STEP ERROR: 找不到 {data_path} 檔案{RESET}")
            return []

    return _shops_cache


def _get_latlng_cached(location: str) -> Optional[Dict[str, float]]:
    """
    Geocoding 結果快取，相同地名只呼叫一次 API。

    Parameters
    ----------
    location : str
        目標地名。

    Returns
    -------
    Optional[Dict[str, float]]
        {'lat': ..., 'lng': ...} 或 None。
    """
    if location in _geocode_cache:
        return _geocode_cache[location]
    gmaps = _get_gmaps()
    result = gmaps.get_latlng(location)
    _geocode_cache[location] = result
    return result


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

    all_shops = _load_all_shops()
    if not all_shops:
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
        _t_geo = time.time()
        target_coords = _get_latlng_cached(target_location)
        _geo_elapsed = time.time() - _t_geo
        if target_coords:
            print(f"{GREEN}STEP: 取得目標座標成功 - {target_coords}，耗時 {_geo_elapsed:.1f}s{RESET}")
        else:
            print(f"{YELLOW}警告: 無法獲取 '{target_location}' 的經緯度（耗時 {_geo_elapsed:.1f}s），將使用字串模糊比對回退機制。{RESET}")

    filtered_results = []
    # 預設搜尋半徑 (公里)；2km 確保結果貼近使用者指定地點
    SEARCH_RADIUS_KM = 2.0

    for _shop in all_shops:
        shop = copy.copy(_shop)  # 防止寫入 distance_km 汙染快取中的原始 dict
        # 1. 口味比對 (Fuzzy Match)
        shop_style = shop.get("style") or ""
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
                    shop["distance_km"] = round(dist, 2)
            else:
                # 店家無座標資料，則回退至字串比對
                clean_target_loc = target_location.replace("市", "").replace("區", "").replace("縣", "")
                shop_loc = shop.get("location") or ""
                if clean_target_loc in shop_loc or shop_loc in clean_target_loc:
                    match_location = True
        else:
            # 無目標座標資料，使用字串比對
            clean_target_loc = target_location.replace("市", "").replace("區", "").replace("縣", "")
            shop_loc = shop.get("location") or ""
            if clean_target_loc in shop_loc or shop_loc in clean_target_loc:
                match_location = True

        if match_location:
            filtered_results.append(shop)

    # 若有距離資訊，依距離排序
    if any("distance_km" in s for s in filtered_results):
        filtered_results.sort(key=lambda x: x.get("distance_km", 999))

    # 隨機抽選最多 3 筆回傳，避免每次結果順序固定
    if len(filtered_results) > 3:
        filtered_results = random.sample(filtered_results, 3)

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


def _get_recommendation_threaded(
    shop_summary: str, client: Any, model_name: str
) -> str:
    """
    在獨立執行緒中以預熱的 genai.Client 生成單筆推薦文。

    client 由 _rec_client_pool 提供，每個執行緒使用不同的 client 實例，
    確保無共用狀態，實現真正並行。

    Parameters
    ----------
    shop_summary : str
        店家摘要文字。
    client : Any
        已預熱的 Gemini client 實例。
    model_name : str
        Gemini 模型名稱。

    Returns
    -------
    str
        推薦文字串。
    """
    default = ""
    try:
        if not check_and_increment("llm_gemini"):
            return default
        prompt = RECOMMEND_PROMPT.format(shop_summary=shop_summary)
        result = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.6, max_output_tokens=400),
        )
        if result.usage_metadata:
            record_tokens(result.usage_metadata.total_token_count or 0)
        raw = result.text.strip()
        raw = re.sub(r"```\w*\s*", "", raw).strip()
        if not raw or any(c in raw for c in ["I will", "As an AI"]):
            return default
        return raw
    except Exception as e:
        print(f"{RED}STEP ERROR: 生成推薦文失敗: {e}{RESET}")
        return default


def generate_recommendations(
    shops_info: List[Dict[str, Any]], client: Any, model_name: str, num_shops: int = 3
) -> List[str]:
    """
    以 ThreadPoolExecutor 並行為多間店家生成 AI 推薦文。
    每個 thread 使用獨立的 genai.Client 實例，確保真正並行。

    Parameters
    ----------
    shops_info : List[Dict[str, Any]]
        篩選後的店家資訊列表。
    client : Any
        Gemini client 實例（僅用於取得 api_key，不傳入 thread）。
    model_name : str
        Gemini 模型名稱。
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
    # 使用預熱 pool；若 pool 未初始化則 fallback 至主 client
    clients = (
        _rec_client_pool[:len(selected)]
        if len(_rec_client_pool) >= len(selected)
        else [client] * len(selected)
    )

    print(f"{GREEN}STEP: 開始並行生成 {len(selected)} 筆推薦文（Pool Client × ThreadPool）{RESET}")
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(selected)) as executor:
            futures = [
                executor.submit(_get_recommendation_threaded, build_shop_summary(s), c, model_name)
                for s, c in zip(selected, clients)
            ]
            return [f.result(timeout=30) for f in futures]
    except Exception as e:
        print(f"{RED}STEP ERROR: 推薦文並行流程失敗: {e}{RESET}")
        return [""] * len(selected)
