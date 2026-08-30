import concurrent.futures
import copy
import json
import os
import threading
import time
import re
import math
import random
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import requests
from google import genai
from google.genai import types

from core.prompts import INFO_SUMMARY_PROMPT, RECOMMEND_PROMPT
from services.google_maps import GoogleMapsService
from core.usage_tracker import check_and_increment, record_tokens
from core.llm_retry import generate_with_retry
from core import timing

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
# 背景刷新狀態旗標：確保快取過期時同一時間只有一個刷新執行緒
_shops_refreshing: bool = False
_refresh_lock = threading.Lock()
# Geocoding 記憶體快取（process 內，最快）。生產模式另有 Firestore 共用層。
_geocode_cache: Dict[str, Optional[Dict[str, float]]] = {}
# Firestore 共用 Geocoding 快取 collection：地名→座標幾乎不變，跨 worker/
# 重啟持久化，減少重複的 Geocoding API 呼叫與配額消耗。
_GEOCODE_COLLECTION = "geocode_cache"
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


def _fetch_all_shops() -> List[Dict[str, Any]]:
    """
    實際從資料源讀取全部店家（Firestore 或本地 JSON），不經過快取。

    Returns
    -------
    List[Dict[str, Any]]
        店家清單；讀取失敗時回傳空清單（由呼叫端決定是否沿用舊快取）。
    """
    if USE_FIRESTORE:
        try:
            from services.firestore_client import get_db
            _t_fs = time.time()
            db = get_db()
            fetched = [doc.to_dict() for doc in db.collection("ramen_shops").stream()]
            print(f"{GREEN}STEP: Firestore 讀取完成，共 {len(fetched)} 筆，"
                  f"耗時 {time.time() - _t_fs:.1f}s{RESET}")
            return fetched
        except Exception as e:
            print(f"{RED}STEP ERROR: Firestore 讀取失敗: {e}{RESET}")
            return []

    data_path = os.path.join("data", "ramen_data.json")
    try:
        with open(data_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"{RED}STEP ERROR: 找不到 {data_path} 檔案{RESET}")
        return []


def _refresh_shops_cache() -> None:
    """背景刷新店家快取；讀取失敗則保留既有快取，等下次再試。"""
    global _shops_cache, _shops_cache_time, _shops_refreshing
    try:
        fetched = _fetch_all_shops()
        if fetched:
            _shops_cache = fetched
            _shops_cache_time = time.time()
            print(f"{GREEN}STEP: 背景刷新店家快取完成，共 {len(fetched)} 筆{RESET}")
    except Exception as e:
        print(f"{RED}STEP ERROR: 背景刷新店家快取失敗: {e}{RESET}")
    finally:
        with _refresh_lock:
            _shops_refreshing = False


def _load_all_shops() -> List[Dict[str, Any]]:
    """
    讀取全部店家資料，結果快取 _CACHE_TTL_SECONDS（24 小時）以減少 gRPC 呼叫。

    採 stale-while-revalidate：快取過期時**先回傳舊資料**、把重新讀取丟到背景
    執行緒，使用者的等待路徑上永遠不會出現整批 180 筆的重讀。只有行程啟動後
    完全沒有快取時（app.py 的預熱）才會同步讀取。

    runtime 寫回（persist_shop_summary）會同步更新記憶體快取；外部（migration/
    腳本）更新資料源後，線上 worker 最長需一個 TTL 週期或重啟才會看到，屬已知取捨。

    Returns
    -------
    List[Dict[str, Any]]
        店家清單。
    """
    global _shops_cache, _shops_cache_time, _shops_refreshing
    now = time.time()
    age = now - _shops_cache_time

    if _shops_cache and age < _CACHE_TTL_SECONDS:
        print(f"{CYAN}[CACHE] 使用店家快取（剩餘 "
              f"{int(_CACHE_TTL_SECONDS - age)} 秒）{RESET}")
        return _shops_cache

    if _shops_cache:
        # 已過期但仍有舊資料：立即回傳，刷新交給背景，不讓使用者承擔重讀延遲。
        # _shops_refreshing 確保同一時間只有一個刷新執行緒。
        with _refresh_lock:
            should_start = not _shops_refreshing
            if should_start:
                _shops_refreshing = True
        if should_start:
            print(f"{YELLOW}[CACHE] 店家快取已過期，先回傳舊資料並於背景刷新{RESET}")
            threading.Thread(target=_refresh_shops_cache, daemon=True).start()
        return _shops_cache

    # 完全無快取（行程啟動後首次讀取，由 app.py 預熱觸發，不在使用者路徑上）
    fetched = _fetch_all_shops()
    if fetched:
        _shops_cache = fetched
        _shops_cache_time = time.time()
    return _shops_cache


def _is_image_url_alive(url: str, timeout: float = 3.0) -> bool:
    """
    對圖片網址發 HEAD request，確認是否仍可載入。

    Google Places API 回傳的 photoUri 是有時效性的簽章網址，過期後仍是合法的
    https 格式，但實際請求會回 403，僅靠字串判斷無法得知是否已失效。

    Parameters
    ----------
    url : str
        要檢查的圖片網址。
    timeout : float
        逾時秒數，預設 3 秒（此檢查在使用者等待回覆的路徑上執行，須夠快）。

    Returns
    -------
    bool
        HTTP 狀態碼為 200 回傳 True；逾時、連線失敗或非 200 一律視為失效。
    """
    try:
        return requests.head(url, timeout=timeout, allow_redirects=True).status_code == 200
    except requests.RequestException:
        return False


def _refresh_shop_image(shop: Dict[str, Any]) -> None:
    """
    重新呼叫 Google Places API 取得新的照片網址，連同更新時間一併寫回資料源。

    於**回覆送出之後**在背景執行，fire-and-forget，任何失敗都只印出錯誤、
    不拋出。`image_url_renew_date` 記錄本次更新時刻（UTC ISO 8601），供日後
    觀測簽章網址的實際存活時間，是決定刷新策略的依據。

    Parameters
    ----------
    shop : Dict[str, Any]
        圖片網址已過期的店家資料字典（需含 place_id 才能查詢）。
    """
    place_id = shop.get("place_id")
    name = shop.get("name") or "未知店名"
    if not place_id:
        return
    try:
        new_url = _get_gmaps().get_photo_by_place_id(place_id)
    except Exception as e:
        print(f"{RED}STEP ERROR: 重新取得 {name} 圖片失敗: {e}{RESET}")
        return
    if not new_url:
        print(f"{YELLOW}STEP: 查無 {name} 的新照片{RESET}")
        return

    renew_date = datetime.now(timezone.utc).isoformat()
    persist_shop_fields(
        shop, {"image_url": new_url, "image_url_renew_date": renew_date}
    )
    print(f"{GREEN}STEP: 已更新 {name} 的圖片網址（{renew_date}）{RESET}")


def resolve_shop_images(
    shops: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    在組裝 Flex Message 前，平行檢查店家圖片網址是否仍存活。

    過期者的 image_url 置為 None，讓 flex_handler 既有的 fallback 自動改用
    預設圖，使用者不會看到破圖。**本函式不觸發任何 API 更新**——需要更新的
    店家以第二個回傳值交給呼叫端，於回覆送出後再呼叫
    `refresh_shop_images_async()`，確保更新不與回覆搶資源。

    Parameters
    ----------
    shops : List[Dict[str, Any]]
        即將顯示給使用者的店家清單（通常 ≤3 筆，回覆前的最後一步呼叫）。

    Returns
    -------
    tuple[List[Dict[str, Any]], List[Dict[str, Any]]]
        (可直接顯示的店家清單（淺複製）, 網址已過期、待回覆後更新的原始店家清單)。
    """
    if not shops:
        return shops, []

    urls = [s.get("image_url") for s in shops]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(shops)) as ex:
        alive_flags = list(
            ex.map(lambda u: _is_image_url_alive(u) if u else True, urls)
        )

    resolved: List[Dict[str, Any]] = []
    stale: List[Dict[str, Any]] = []
    for shop, url, alive in zip(shops, urls, alive_flags):
        shop_copy = copy.copy(shop)
        if url and not alive:
            print(f"{YELLOW}STEP: {shop.get('name')} 圖片網址已過期，本次用預設圖{RESET}")
            shop_copy["image_url"] = None
            stale.append(shop)
        resolved.append(shop_copy)
    return resolved, stale


def refresh_shop_images_async(shops: List[Dict[str, Any]]) -> None:
    """
    於回覆送出後，在背景更新這批店家的圖片網址。

    Parameters
    ----------
    shops : List[Dict[str, Any]]
        `resolve_shop_images()` 回傳的待更新店家清單。
    """
    for shop in shops:
        threading.Thread(
            target=_refresh_shop_image, args=(shop,), daemon=True
        ).start()


def _geocode_doc_id(location: str) -> str:
    """將地名正規化為 Firestore 安全的 document id（避免 '/' 破壞路徑）。"""
    return location.strip().replace("/", "_") or "unknown"


def _load_geocode_from_firestore(location: str) -> Optional[Dict[str, float]]:
    """從 Firestore `geocode_cache` 讀取地名對應座標，查無或失敗回傳 None。"""
    try:
        from services.firestore_client import get_db

        snap = (
            get_db()
            .collection(_GEOCODE_COLLECTION)
            .document(_geocode_doc_id(location))
            .get()
        )
        if snap.exists:
            data = snap.to_dict() or {}
            lat, lng = data.get("lat"), data.get("lng")
            if lat is not None and lng is not None:
                return {"lat": lat, "lng": lng}
    except Exception as e:
        print(f"{RED}STEP ERROR: Firestore geocode 快取讀取失敗: {e}{RESET}")
    return None


def _save_geocode_to_firestore(location: str, coords: Dict[str, float]) -> None:
    """將成功的 Geocoding 結果寫入 Firestore `geocode_cache`（fire-and-forget）。"""
    try:
        from services.firestore_client import get_db

        get_db().collection(_GEOCODE_COLLECTION).document(
            _geocode_doc_id(location)
        ).set(
            {
                "query": location,
                "lat": coords["lat"],
                "lng": coords["lng"],
                "cached_at": datetime.now(timezone.utc),
            }
        )
    except Exception as e:
        print(f"{RED}STEP ERROR: Firestore geocode 快取寫入失敗: {e}{RESET}")


def _get_latlng_cached(location: str) -> Optional[Dict[str, float]]:
    """
    Geocoding 結果三層快取：記憶體 → Firestore（生產）→ Geocoding API。

    地名→座標幾乎不變，故成功結果下沉至 Firestore `geocode_cache` 跨 worker/
    重啟共用，減少重複 API 呼叫與配額消耗。失敗（None）僅留在 process 記憶體、
    不寫入 Firestore，避免把暫時性失敗永久快取（重啟後可重試）。

    Parameters
    ----------
    location : str
        目標地名（已由 _build_geocode_query 正規化的查詢字串）。

    Returns
    -------
    Optional[Dict[str, float]]
        {'lat': ..., 'lng': ...} 或 None。
    """
    # 1. 記憶體快取（最快）
    if location in _geocode_cache:
        return _geocode_cache[location]

    # 2. Firestore 共用快取（僅生產模式；跨 worker/重啟持久化）
    if USE_FIRESTORE:
        cached = _load_geocode_from_firestore(location)
        if cached is not None:
            print(f"{CYAN}[CACHE] 命中 Firestore geocode 快取：{location}{RESET}")
            _geocode_cache[location] = cached
            return cached

    # 3. 呼叫 Geocoding API，成功結果回寫兩層快取
    gmaps = _get_gmaps()
    result = gmaps.get_latlng(location)
    _geocode_cache[location] = result
    if USE_FIRESTORE and result is not None:
        _save_geocode_to_firestore(location, result)
    return result


def _build_geocode_query(location: str) -> str:
    """
    根據使用者提供的地名組合 Geocoding API 查詢字串。

    店家資料以台北市為主，地名若未包含縣市資訊，預設補上「台北市」前綴，
    避免「中山區」等同名地點被解析到外縣市或海外座標。
    若地名以「站」結尾（捷運/車站名稱），改用「台北捷運」前綴，避免被
    Geocoding 解析到同名但不相關的巴士站/地標（例如「中山站」被誤判為
    萬華區的「中山堂(西門)」公車站，導致跨區搜尋結果）。

    Parameters
    ----------
    location : str
        使用者提到的原始地名。

    Returns
    -------
    str
        用於 Geocoding API 的查詢字串。
    """
    if location.endswith("站"):
        return f"台北捷運{location}"
    if not re.search(r"(市|縣)", location) and not location.startswith("台北"):
        return f"台北市{location}"
    return location


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


def is_open_at(opening_hours: Optional[Dict[str, Any]], dt: datetime) -> bool:
    """
    判斷店家在指定時間點是否營業中。

    opening_hours 的 periods 採 Google Places API (New) `regularOpeningHours.periods`
    原生結構：每筆為 `{"open": {"day": 0-6, "hour": 0-23, "minute": 0-59},
    "close": {...}}`，其中 day 以 0=週日、1=週一 ... 6=週六 編碼（與 Places API 一致）。
    正確處理跨午夜營業（close 落在 open 之後的隔日）。

    Parameters
    ----------
    opening_hours : Optional[Dict[str, Any]]
        店家營業時間資料，須含 "periods" 鍵；為 None、缺 periods 或 periods 為空時回傳 False。
    dt : datetime
        欲判斷的時間點（台北當地時間）。

    Returns
    -------
    bool
        該時間點是否落在任一營業時段內。
    """
    if not opening_hours:
        return False
    periods = opening_hours.get("periods") or []
    if not periods:
        return False

    # Places API day 編碼：0=週日 ... 6=週六；Python weekday()：0=週一 ... 6=週日
    now_day = (dt.weekday() + 1) % 7
    now_minutes = dt.hour * 60 + dt.minute

    for period in periods:
        open_p = period.get("open") or {}
        close_p = period.get("close") or {}
        if "day" not in open_p:
            continue
        open_day = open_p.get("day")
        open_min = open_p.get("hour", 0) * 60 + open_p.get("minute", 0)

        # Places API 僅在「24 小時營業」時省略 close 欄位，視為全時段營業
        if not close_p or "day" not in close_p:
            return True

        close_day = close_p.get("day")
        close_min = close_p.get("hour", 0) * 60 + close_p.get("minute", 0)

        if open_day == close_day:
            # 當日內時段
            if now_day == open_day and open_min <= now_minutes < close_min:
                return True
        else:
            # 跨午夜時段：從 open_day 的 open_min 到 close_day 的 close_min
            if now_day == open_day and now_minutes >= open_min:
                return True
            if now_day == close_day and now_minutes < close_min:
                return True

    return False


def _dedupe_by_place_id(shops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    以 place_id 去除重複店家，保留每組第一筆，維持原始順序。

    ramen_data.json 部分店家因 IG 貼文逐則匯入，同一實體店家（同 place_id）
    存在多筆內容不一致的紀錄（例如描述長度不同、店名一者標「暫停營業」
    一者未標）。去重僅用於避免同一家店在單次搜尋結果中被抽中兩次，
    不修改快取或原始資料，也不對「哪筆內容才正確」做判斷——距離排序後
    呼叫可讓「最近的一筆」勝出，其餘情境則保留 JSON 中最先出現的一筆。
    place_id 缺失時退回以 name 去重（防呆；目前資料全數有 place_id）。

    Parameters
    ----------
    shops : List[Dict[str, Any]]
        候選店家清單（可能包含同 place_id 的重複項）。

    Returns
    -------
    List[Dict[str, Any]]
        去重後的店家清單，保留輸入順序。
    """
    seen: set = set()
    deduped = []
    for shop in shops:
        key = shop.get("place_id") or shop.get("name")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(shop)
    return deduped


def filter_by_location(
    lat: float,
    lng: float,
    radius_km: float = 5.0,
    style: Optional[str] = None,
) -> tuple[List[Dict[str, Any]], Optional[float]]:
    """
    以使用者分享的精確座標（LINE 位置訊息）為中心，找出最近的拉麵店。

    與 filter_ramen_data 不同，本函式不經過 Geocoding、不做隨機抽選，
    直接對店家快取跑 Haversine 距離並依距離排序取最近數間，供
    LocationMessage 事件使用。

    Parameters
    ----------
    lat : float
        使用者所在緯度。
    lng : float
        使用者所在經度。
    radius_km : float
        搜尋半徑（公里），預設 5km。
    style : Optional[str]
        口味關鍵字，None 或空字串表示不限口味。

    Returns
    -------
    tuple[List[Dict[str, Any]], Optional[float]]
        (最近 ≤3 間符合半徑的店家, 全資料集最近一間的距離)。
        第二個值僅在無結果時供回覆訊息說明「最近一間有多遠」，
        無任何可比對店家時為 None。
    """
    print(f"{GREEN}STEP: 開始執行定位推薦（({lat}, {lng}) 半徑 {radius_km}km）{RESET}")

    all_shops = _load_all_shops()
    if not all_shops:
        return [], None

    target_style = style or ""
    within_radius: List[Dict[str, Any]] = []
    nearest_km: Optional[float] = None

    for _shop in all_shops:
        # 已標記暫停營業的店家不應推薦給使用者
        if "暫停營業" in (_shop.get("name") or ""):
            continue

        shop_coords = _shop.get("coordinates")
        has_coords = (
            shop_coords
            and shop_coords.get("lat") is not None
            and shop_coords.get("lng") is not None
        )
        if not has_coords:
            continue

        # 口味比對（模糊）
        shop_style = _shop.get("style") or ""
        if target_style and target_style not in shop_style:
            continue

        dist = calculate_distance(lat, lng, shop_coords["lat"], shop_coords["lng"])
        if nearest_km is None or dist < nearest_km:
            nearest_km = round(dist, 2)

        if dist <= radius_km:
            shop = copy.copy(_shop)  # 防止寫入 distance_km 汙染快取原始 dict
            shop["distance_km"] = round(dist, 2)
            within_radius.append(shop)

    within_radius.sort(key=lambda x: x.get("distance_km", 999))
    within_radius = _dedupe_by_place_id(within_radius)
    results = within_radius[:3]

    print(f"{GREEN}STEP: 定位推薦完成，半徑內 {len(within_radius)} 間，回傳 {len(results)} 間{RESET}")
    return results, nearest_km


# 使用者說「推薦」「好吃」這類形容詞時，Gemini 偶爾會誤填進 style 欄位。
# 這不是有效的篩選條件，須視同未指定口味。agent_router 判斷「是否有可用的搜尋
# 條件」時亦須套用同一份定義，否則兩處對「有 style」的認定會不一致——router
# 放行、filter 卻清空，等於又落回「無條件 = 比對全部店家」的失效模式。
GENERIC_STYLE_KEYWORDS = ("推薦", "好吃", "熱門")

# 資料集除大台北外，也收錄使用者旅日時記錄的店家（大阪 9、福岡 5、東京 5、
# 熊本 2、奈良 1，共 22 筆）。使用者未指定地區時，這些店家會與台灣店家一同
# 進入隨機抽選，推薦出使用者根本去不了的店（見 DEVELOP_HISTORY.md 2026-06-29
# 大阪店家誤推薦事件）。僅在「使用者沒講地區」時排除；明確查詢「大阪的拉麵」
# 時不套用，否則會查不到東西。filter_by_location()（GPS 路徑）不需此過濾，
# 因其以真實座標做半徑比對，海外店家本就落在範圍外。
_OVERSEAS_PATTERN = re.compile(
    r"大阪|東京|福岡|熊本|奈良|京都|北海道|札幌|名古屋|橫濱|神戶|日本"
)


def filter_ramen_data(
    intent_data: Dict[str, Any], current_time: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    核心篩選邏輯：根據 AI 解析出的意圖，從本地 JSON 篩選店家。
    包含地理位置經緯度比對邏輯。

    Parameters
    ----------
    intent_data : Dict[str, Any]
        由 Agent Router 解析出的意圖資料。
    current_time : Optional[str]
        台北時間字串（格式 "%Y-%m-%d %H:%M"）。當 intent_data 的 open_now 為真時，
        用於過濾「當下正在營業」的店家；None 則不做營業時間過濾。

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

    # 過濾 AI 誤抓的形容詞（定義與 agent_router 共用，見 GENERIC_STYLE_KEYWORDS）
    if target_style in GENERIC_STYLE_KEYWORDS:
        target_style = ""

    # 「現在有開」查詢：解析當下時間，供 is_open_at 過濾營業中的店家
    open_now = bool(intent_data.get("open_now"))
    now_dt = None
    if open_now and current_time:
        try:
            now_dt = datetime.strptime(current_time, "%Y-%m-%d %H:%M")
        except ValueError:
            print(f"{YELLOW}警告: current_time 格式無法解析（{current_time}），略過營業時間過濾{RESET}")
            open_now = False

    print(f"{CYAN}[DEBUG] 原始條件 - 地區: '{target_location}', 口味: '{target_style}', "
          f"open_now: {open_now}{RESET}")

    # --- 地理位置處理 (Geocoding) ---
    target_coords = None
    # 行政區（區/市/縣結尾）面積大且形狀不規則，Geocoding 回傳的幾何中心點常
    # 離店家聚集處超過 2km；實測顯示半徑放大到能涵蓋整個行政區時，也會等量
    # 圈入鄰近行政區的店家（無安全半徑值，例如中山區需 4km 才能涵蓋全部店家，
    # 但同半徑下會多圈入 22 間大安/內湖/士林/新竹店家）。故行政區查詢改為直接
    # 比對店家 location 欄位字串，僅捷運站等精確點查詢才使用 Geocoding + Haversine。
    is_district_query = bool(re.search(r"(區|市|縣)$", target_location))
    if target_location and not is_district_query:
        geocode_location = _build_geocode_query(target_location)

        _t_geo = time.time()
        target_coords = _get_latlng_cached(geocode_location)
        _geo_elapsed = time.time() - _t_geo
        if target_coords:
            print(f"{GREEN}STEP: 取得目標座標成功 - '{geocode_location}' -> {target_coords}，耗時 {_geo_elapsed:.1f}s{RESET}")
        else:
            print(f"{YELLOW}警告: 無法獲取 '{geocode_location}' 的經緯度（耗時 {_geo_elapsed:.1f}s），將使用字串模糊比對回退機制。{RESET}")

    filtered_results = []
    # 預設搜尋半徑 (公里)；2km 確保結果貼近使用者指定地點。
    # 使用者若明確指定範圍（例如「方圓 5 公里」），intent 的 radius_km 覆寫預設值。
    SEARCH_RADIUS_KM = 2.0
    radius_km = intent_data.get("radius_km")
    if isinstance(radius_km, (int, float)) and radius_km > 0:
        SEARCH_RADIUS_KM = float(radius_km)
        print(f"{CYAN}[DEBUG] 使用者指定搜尋半徑：{SEARCH_RADIUS_KM}km{RESET}")

    for _shop in all_shops:
        # 已標記暫停營業的店家不應推薦給使用者
        if "暫停營業" in (_shop.get("name") or ""):
            continue

        # 使用者未指定地區時排除海外店家（詳見 _OVERSEAS_PATTERN 註解）
        if not target_location and _OVERSEAS_PATTERN.search(
            _shop.get("location") or ""
        ):
            continue

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
            has_coords = (
                shop_coords
                and shop_coords.get("lat") is not None
                and shop_coords.get("lng") is not None
            )
            if has_coords:
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
                # shop_loc 為空字串時，"" in clean_target_loc 恆為 True，
                # 須先排除以避免缺少 location 欄位的店家誤配對任何地區查詢。
                if shop_loc and (
                    clean_target_loc in shop_loc or shop_loc in clean_target_loc
                ):
                    match_location = True
        else:
            # 無目標座標資料，使用字串比對
            clean_target_loc = target_location.replace("市", "").replace("區", "").replace("縣", "")
            shop_loc = shop.get("location") or ""
            if shop_loc and (
                clean_target_loc in shop_loc or shop_loc in clean_target_loc
            ):
                match_location = True

        if not match_location:
            continue

        # 3. 營業時間比對（僅「現在有開」查詢啟用）
        # 無 opening_hours 資料的店家無法確認營業狀態，寧缺勿錯，一律排除。
        if open_now and now_dt is not None:
            if not is_open_at(shop.get("opening_hours"), now_dt):
                continue

        filtered_results.append(shop)

    # 若有距離資訊，依距離排序
    has_distance = any("distance_km" in s for s in filtered_results)
    if has_distance:
        filtered_results.sort(key=lambda x: x.get("distance_km", 999))

    # 去除同 place_id 的重複店家，避免同一家店在結果中出現兩次
    filtered_results = _dedupe_by_place_id(filtered_results)

    if len(filtered_results) > 3:
        if has_distance:
            # 有精確距離資訊（捷運站/地址等座標查詢）時，固定取最近 2 間，
            # 第 3 間從次近的候選池中隨機挑選：確保結果貼近使用者指定地點，
            # 同時保留少量多樣性，避免同一地點每次查詢結果完全相同。
            NEARBY_POOL_SIZE = 6
            top2 = filtered_results[:2]
            pool = filtered_results[2:2 + NEARBY_POOL_SIZE]
            third = [random.choice(pool)] if pool else []
            filtered_results = top2 + third
        else:
            # 無距離資訊（純地區字串/口味比對）：維持原本全池隨機抽選，
            # 避免每次結果順序固定。
            filtered_results = random.sample(filtered_results, 3)

    print(f"{GREEN}STEP: 篩選完成，共找到 {len(filtered_results)} 間店家{RESET}")
    return filtered_results


# --- AI 摘要欄位持久化 ---


def _summary_source(shop: Dict[str, Any]) -> Dict[str, Any]:
    """
    建構供 AI 摘要使用的店家資料視圖，排除查詢期暫存的 distance_km。

    distance_km 由 filter_ramen_data / filter_by_location 依「當次查詢」寫入，
    若被納入摘要並快取，會讓 search_ai_summary 綁定特定距離而失去查詢無關性。

    Parameters
    ----------
    shop : Dict[str, Any]
        店家資料字典。

    Returns
    -------
    Dict[str, Any]
        不含 distance_km 的店家資料（無此欄位時直接回傳原 dict）。
    """
    if "distance_km" not in shop:
        return shop
    return {k: v for k, v in shop.items() if k != "distance_km"}


def _update_cache_field(shop_id: Any, name: str, field: str, value: str) -> None:
    """
    更新模組層級店家快取中對應店家的單一欄位。

    讓同一 TTL 窗內的後續請求可直接命中快取欄位，不必再次呼叫 LLM。

    Parameters
    ----------
    shop_id : Any
        店家 id（優先比對）。
    name : str
        店家名稱（id 不符時的後備比對鍵）。
    field : str
        欲更新的欄位名稱。
    value : str
        欲寫入的內容。
    """
    for _s in _shops_cache:
        if (shop_id and _s.get("id") == shop_id) or _s.get("name") == name:
            _s[field] = value
            break


def persist_shop_fields(shop: Dict[str, Any], fields: Dict[str, Any]) -> None:
    """
    將單一店家的多個欄位一次寫回資料源，並同步更新模組層級快取。

    僅寫入指定欄位（Firestore merge / 本地更新對應 key），不寫整包 shop dict，
    以免 distance_km 等查詢期暫存欄位汙染持久化資料。一次寫入多欄可確保
    image_url 與 image_url_renew_date 這類必須同進退的欄位不會不一致。

    Parameters
    ----------
    shop : Dict[str, Any]
        店家資料字典（可能為查詢期的淺複製）。
    fields : Dict[str, Any]
        欲寫入的欄位與值；為空時不做任何事。
    """
    if not fields:
        return

    name = shop.get("name") or ""
    shop_id = shop.get("id")
    for field, value in fields.items():
        _update_cache_field(shop_id, name, field, value)

    try:
        if USE_FIRESTORE:
            from services.firestore_client import get_db

            db = get_db()
            doc_id = str(shop_id or name or "unknown").strip().replace("/", "_")
            db.collection("ramen_shops").document(doc_id).set(fields, merge=True)
        else:
            data_path = os.path.join("data", "ramen_data.json")
            with open(data_path, "r", encoding="utf-8") as f:
                all_shops = json.load(f)
            for _s in all_shops:
                if (shop_id and _s.get("id") == shop_id) or _s.get("name") == name:
                    _s.update(fields)
                    break
            with open(data_path, "w", encoding="utf-8") as f:
                json.dump(all_shops, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"{RED}STEP ERROR: 寫回 {list(fields)} 失敗: {e}{RESET}")


def persist_shop_summary(shop: Dict[str, Any], field: str, value: str) -> None:
    """
    將單一店家的 AI 摘要欄位寫回資料源。

    value 為空字串時（LLM 生成失敗）不寫入，保留下次重試機會。

    Parameters
    ----------
    shop : Dict[str, Any]
        店家資料字典（可能為查詢期的淺複製）。
    field : str
        欲寫入的欄位名稱（search_ai_summary 或 info_ai_summary）。
    value : str
        欲寫入的內容。
    """
    if not value:
        return
    persist_shop_fields(shop, {field: value})


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
        # gemini-2.5-flash 預設會先消耗 thinking tokens（實測單次最多 380+），
        # 常吃光 max_output_tokens 預算導致可見文字被硬切斷在句子中間；
        # 這類短文案不需要推理，故關閉 thinking。
        result = generate_with_retry(
            lambda: client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.6,
                    max_output_tokens=400,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            ),
            label="推薦文",
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
    results: List[Optional[str]] = [None] * len(selected)

    # 先讀取快取欄位，僅對缺少 search_ai_summary 的店家呼叫 LLM
    pending: List[tuple[int, Dict[str, Any]]] = []
    for i, s in enumerate(selected):
        cached = (s.get("search_ai_summary") or "").strip()
        if cached:
            results[i] = cached
        else:
            pending.append((i, s))

    if not pending:
        print(f"{GREEN}STEP: 推薦文全部命中 search_ai_summary 快取（{len(selected)} 筆），略過 LLM{RESET}")
        return [r or "" for r in results]

    # 使用預熱 pool；若 pool 未初始化則 fallback 至主 client
    clients = (
        _rec_client_pool[:len(pending)]
        if len(_rec_client_pool) >= len(pending)
        else [client] * len(pending)
    )

    print(f"{GREEN}STEP: {len(pending)}/{len(selected)} 筆需生成推薦文（其餘命中快取），"
          f"Pool Client × ThreadPool 並行{RESET}")

    # 於父執行緒取得計時收集器；worker 執行緒不繼承 contextvars，故顯式傳入
    recs = timing.current_records()

    def _timed_rec(name: str, shop_summary: str, rec_client: Any) -> str:
        """在 worker 執行緒內計時單筆推薦文 LLM 呼叫，並記錄至父收集器。"""
        _t = time.time()
        text = _get_recommendation_threaded(shop_summary, rec_client, model_name)
        timing.record(name, time.time() - _t, records=recs)
        return text

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(pending)) as executor:
            futures = [
                (
                    idx,
                    shop,
                    executor.submit(
                        _timed_rec,
                        f"rec[{idx}]",
                        build_shop_summary(_summary_source(shop)),
                        c,
                    ),
                )
                for (idx, shop), c in zip(pending, clients)
            ]
            for idx, shop, future in futures:
                text = future.result(timeout=30)
                results[idx] = text
                # 生成成功才寫回（persist 內部對空字串 no-op），供下次查詢重用
                persist_shop_summary(shop, "search_ai_summary", text)
    except Exception as e:
        print(f"{RED}STEP ERROR: 推薦文並行流程失敗: {e}{RESET}")

    return [r or "" for r in results]


def summarize_description(shop: Dict[str, Any], client: Any, model_name: str) -> str:
    """
    將店家資料（含 style、features、完整 IG 食記）摘要為約 100~150 字的介紹文字。

    輸入欄位與 generate_recommendations() 比照一致，皆透過 build_shop_summary()
    建構，確保兩條 Skill 路徑提供給 LLM 的店家資訊範圍相同。

    Parameters
    ----------
    shop : Dict[str, Any]
        單一店家完整資料。
    client : Any
        Gemini client 實例。
    model_name : str
        Gemini 模型名稱。

    Returns
    -------
    str
        摘要文字，失敗時回傳空字串（由呼叫端回退至原始 description）。
    """
    default = ""

    # 先讀取快取欄位；命中則直接回傳，不呼叫 LLM
    cached = (shop.get("info_ai_summary") or "").strip()
    if cached:
        print(f"{GREEN}STEP: 命中 info_ai_summary 快取，略過 LLM{RESET}")
        return cached

    try:
        if not check_and_increment("llm_gemini"):
            return default
        shop_summary = build_shop_summary(_summary_source(shop))
        prompt = INFO_SUMMARY_PROMPT.format(shop_summary=shop_summary)
        # 同 _get_recommendation_threaded：關閉 thinking，避免 thinking tokens
        # 吃光 max_output_tokens 預算導致介紹文被硬切斷。
        with timing.time_llm("info_summary"):
            result = generate_with_retry(
                lambda: client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.6,
                        max_output_tokens=600,
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                    ),
                ),
                label="店家摘要",
            )
        if result.usage_metadata:
            record_tokens(result.usage_metadata.total_token_count or 0)
        raw = result.text.strip()
        raw = re.sub(r"```\w*\s*", "", raw).strip()
        if not raw or any(c in raw for c in ["I will", "As an AI"]):
            return default
        # 生成成功才寫回，供下次查詢重用
        persist_shop_summary(shop, "info_ai_summary", raw)
        return raw
    except Exception as e:
        print(f"{RED}STEP ERROR: 摘要店家描述失敗: {e}{RESET}")
        return default
