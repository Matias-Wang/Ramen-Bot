"""
店家照片解析服務：把「會過期的簽章網址」隔離在伺服器端。

背景
----
Google Places API (New) 的 Media 端點回傳的 `photoUri` 是一個
`lh3.googleusercontent.com` 的**有時效簽章網址**，過期後仍是合法的 https 格式，
但實際請求會回 403。先前的設計把這個網址直接存進 `ramen_data.json` /
Firestore 的 `image_url` 欄位當成永久網址使用，因此每一筆遲早都會失效
（2026-08-01 實測：正式環境 180 筆中 168 筆已失效，佔 93.3%）。

由於 LINE 訊息一旦推播出去就永遠留在使用者的聊天記錄中、無法回頭修改，
存進訊息裡的圖片網址**必須永久有效**。因此改為：Flex Message 中放本服務的
`/photo/<place_id>` 代理網址（永不變動），由伺服器端在每次被載入時解析出
當下有效的簽章網址並 302 導向。API 金鑰因此不會外流至使用者端。

快取策略
--------
1. `photo_name`（`places/{place_id}/photos/{photo_id}`）本身不會過期，
   首次取得後即寫回店家資料，之後換新網址只需 1 次 Media 呼叫。
2. 解析出的簽章網址以 `_URL_TTL_SECONDS` 短期快取於記憶體，避免同一張圖
   被反覆載入時每次都打 API。TTL 取得保守，因為簽章實際壽命未公開。
"""

import os
import time
from typing import Dict, Optional, Tuple

# <使用者自訂變數>
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
RESET = "\033[0m"

# 解析失敗時使用的預設圖，與 core/flex_handler.py 的 hero 預設圖一致
DEFAULT_PHOTO_URL = (
    "https://images.unsplash.com/photo-1569718212165-3a8278d5f624"
    "?auto=format&fit=crop&w=800&q=80"
)

# 簽章網址記憶體快取：place_id -> (url, 到期時戳)
_URL_TTL_SECONDS = 1800
_URL_CACHE_MAX = 500
_url_cache: Dict[str, Tuple[str, float]] = {}

PHOTO_MAX_HEIGHT_PX = 800


def _lookup_photo_name(place_id: str) -> Optional[str]:
    """
    取得店家照片的資源名稱，優先使用店家資料中已存的 `photo_name`。

    查無時才呼叫 Places API Place Details，並把結果寫回店家資料（含模組層級
    快取），使後續請求不必重複這次呼叫。

    Parameters
    ----------
    place_id : str
        Google Places 店家 ID。

    Returns
    -------
    Optional[str]
        照片資源名稱，查無或呼叫失敗時回傳 None。
    """
    # 延遲匯入：避免 core 於模組載入階段即相依 skills，並沿用既有店家快取
    from skills.Search_skill import _get_gmaps, _load_all_shops, persist_shop_summary

    shop = next(
        (s for s in _load_all_shops() if s.get("place_id") == place_id), None
    )
    if shop:
        cached = (shop.get("photo_name") or "").strip()
        if cached:
            return cached

    photo_name = _get_gmaps().get_photo_name_by_place_id(place_id)
    if photo_name and shop:
        # persist_shop_summary 為通用單欄位寫回工具（本地 JSON / Firestore merge）
        persist_shop_summary(shop, "photo_name", photo_name)
    return photo_name


def resolve_photo_url(place_id: str) -> Optional[str]:
    """
    解析出指定店家當下有效的照片簽章網址。

    Parameters
    ----------
    place_id : str
        Google Places 店家 ID。

    Returns
    -------
    Optional[str]
        當下有效的照片網址；查無照片、配額用盡或呼叫失敗時回傳 None
        （由呼叫端決定改用預設圖）。
    """
    if not place_id:
        return None

    now = time.time()
    cached = _url_cache.get(place_id)
    if cached and now < cached[1]:
        return cached[0]

    try:
        photo_name = _lookup_photo_name(place_id)
        if not photo_name:
            return None

        from skills.Search_skill import _get_gmaps

        url = _get_gmaps().get_photo_url(photo_name, PHOTO_MAX_HEIGHT_PX)
    except Exception as e:
        print(f"{RED}STEP ERROR: 解析 {place_id} 照片網址失敗: {e}{RESET}")
        return None

    if not url:
        return None

    if len(_url_cache) >= _URL_CACHE_MAX:
        for _pid in [p for p, (_, exp) in _url_cache.items() if exp <= now]:
            _url_cache.pop(_pid, None)
    _url_cache[place_id] = (url, now + _URL_TTL_SECONDS)
    return url


def photo_proxy_url(place_id: Optional[str]) -> Optional[str]:
    """
    組出供 Flex Message 使用的永久照片代理網址。

    需設定環境變數 `PHOTO_PROXY_BASE`（本服務的對外網址，例如
    `https://ramen-bot-xxxx.asia-east1.run.app`）；未設定時回傳 None，
    由呼叫端回退至既有的 `image_url` 欄位或預設圖。

    Parameters
    ----------
    place_id : Optional[str]
        Google Places 店家 ID。

    Returns
    -------
    Optional[str]
        `{PHOTO_PROXY_BASE}/photo/{place_id}`，條件不足時回傳 None。
    """
    base = (os.getenv("PHOTO_PROXY_BASE") or "").rstrip("/")
    if not base or not place_id:
        return None
    return f"{base}/photo/{place_id}"
