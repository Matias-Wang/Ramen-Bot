import os
import requests
from typing import Optional, Dict, Any
import googlemaps
from dotenv import load_dotenv

from core.usage_tracker import check_and_increment

load_dotenv()

# <使用者自訂變數>
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
BLUE = "\033[94m"
CYAN = "\033[96m"
MAGAENTA = "\033[95m"
RESET = "\033[0m"

PLACES_NEW_BASE_URL = "https://places.googleapis.com/v1"


class GoogleMapsService:
    """
    Google Maps API 服務類別

    本類別符合 ARCHITECTURE.md v3.1 規範中的「工具化流程 (Skill-based)」：
    1. Geocoding API：實作 get_latlng，用於 Search Skill 的地理位置比對。
    2. Places API (New)：實作 get_shop_details，透過 Text Search 取得即時評分與照片。
    3. Media Proxy：實作 get_photo_url，將照片資源名稱轉換為 HTTPS URL。

    技術優化：
    - Field Masking：在呼叫時僅請求必要欄位，控管 API 支出。
    - 照片高度上限：800px，符合行動裝置與 LINE Flex Message 規範。
    """

    def __init__(self) -> None:
        """初始化 Google Maps 客戶端"""
        print(f"{GREEN}STEP: 初始化 Google Maps 服務{RESET}")
        self.api_key = os.getenv("GOOGLE_MAPS_API_KEY")
        if self.api_key:
            try:
                self.gmaps = googlemaps.Client(key=self.api_key)
            except Exception as e:
                print(f"{RED}STEP ERROR: 初始化 Google Maps 客戶端失敗: {e}{RESET}")
                self.gmaps = None
        else:
            print(f"{YELLOW}警告: 未設定 GOOGLE_MAPS_API_KEY{RESET}")
            self.gmaps = None

    def get_latlng(self, address: str) -> Optional[Dict[str, float]]:
        """
        Geocoding API：將地址轉換為經緯度座標。
        用於 Search Skill 中的地理位置過濾。

        Parameters
        ----------
        address : str
            要轉換的地址。

        Returns
        -------
        Optional[Dict[str, float]]
            包含 'lat' 和 'lng' 的字典，若失敗則回傳 None。
        """
        if not self.gmaps:
            return None

        if not check_and_increment("google_maps_api"):
            return None

        print(f"{GREEN}STEP: 正在獲取地址經緯度 - {address}{RESET}")
        try:
            geocode_result = self.gmaps.geocode(address, language="zh-TW", region="tw")
            if geocode_result:
                location = geocode_result[0]["geometry"]["location"]
                return {"lat": location["lat"], "lng": location["lng"]}
        except Exception as e:
            print(f"{RED}STEP ERROR: 獲取經緯度失敗: {e}{RESET}")

        return None

    def get_shop_details(self, name: str, location: str = "") -> Optional[Dict[str, Any]]:
        """
        Places API (New) Text Search：透過店名搜尋即時資訊。

        使用 Field Masking 僅抓取 id, displayName, rating,
        userRatingCount, formattedAddress, photos 欄位以控管成本。

        Parameters
        ----------
        name : str
            店名。
        location : str, optional
            搜尋的地區範圍，預設為空字串。

        Returns
        -------
        Optional[Dict[str, Any]]
            標準化後的店家資訊字典，包含 place_id、name、rating、
            user_ratings_total、formatted_address、photo_url。
            若找不到則回傳 None。
        """
        if not self.api_key:
            return None

        if not check_and_increment("google_maps_api"):
            return None

        query = f"{location} {name}" if location else name
        print(f"{GREEN}STEP: 正在搜尋店家詳細資訊 (Places API New) - {query}{RESET}")

        try:
            url = f"{PLACES_NEW_BASE_URL}/places:searchText"
            headers = {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": (
                    "places.id,"
                    "places.displayName,"
                    "places.rating,"
                    "places.userRatingCount,"
                    "places.formattedAddress,"
                    "places.photos"
                ),
            }
            body = {
                "textQuery": query,
                "languageCode": "zh-TW",
                "maxResultCount": 1,
            }

            response = requests.post(url, json=body, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()

            places = data.get("places", [])
            if not places:
                print(f"{YELLOW}找不到店家: {query}{RESET}")
                return None

            place = places[0]

            # 取得第一張照片 URL
            photo_url = None
            photos = place.get("photos", [])
            if photos:
                photo_name = photos[0].get("name")
                if photo_name:
                    photo_url = self.get_photo_url(photo_name)

            return {
                "place_id": place.get("id"),
                "name": place.get("displayName", {}).get("text", name),
                "rating": place.get("rating"),
                "user_ratings_total": place.get("userRatingCount"),
                "formatted_address": place.get("formattedAddress"),
                "photo_url": photo_url,
            }

        except Exception as e:
            print(f"{RED}STEP ERROR: 獲取店家詳細資訊失敗: {e}{RESET}")

        return None

    def verify_shop_status(self, name: str, location: str = "") -> Optional[Dict[str, Any]]:
        """
        Places API (New) Text Search：驗證店家營業狀態並取得座標與 place_id。

        用於離線資料處理腳本（例如建立新店家候選清單），
        過濾已永久歇業（CLOSED_PERMANENTLY）的店家。
        Field Masking 僅請求 places.id, places.businessStatus,
        places.location, places.formattedAddress。

        Parameters
        ----------
        name : str
            店名。
        location : str, optional
            搜尋的地區範圍，預設為空字串。

        Returns
        -------
        Optional[Dict[str, Any]]
            包含 place_id、business_status、coordinates、address 的字典，
            若找不到則回傳 None。
        """
        if not self.api_key:
            return None

        if not check_and_increment("google_maps_api"):
            return None

        query = f"{location} {name} 拉麵".strip() if location else f"{name} 拉麵"
        print(f"{GREEN}STEP: 正在驗證店家狀態 - {query}{RESET}")

        try:
            url = f"{PLACES_NEW_BASE_URL}/places:searchText"
            headers = {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": (
                    "places.id,"
                    "places.businessStatus,"
                    "places.location,"
                    "places.formattedAddress"
                ),
            }
            body = {
                "textQuery": query,
                "languageCode": "zh-TW",
                "maxResultCount": 1,
            }
            response = requests.post(url, json=body, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()

            places = data.get("places", [])
            if not places:
                return None

            place = places[0]
            loc = place.get("location", {})
            return {
                "place_id": place.get("id"),
                "business_status": place.get("businessStatus", "OPERATIONAL"),
                "coordinates": {
                    "lat": loc.get("latitude"),
                    "lng": loc.get("longitude"),
                },
                "address": place.get("formattedAddress"),
            }
        except Exception as e:
            print(f"{RED}STEP ERROR: 驗證 {name} 失敗: {e}{RESET}")
            return None

    def get_photo_name_by_place_id(self, place_id: str) -> Optional[str]:
        """
        Places API (New) Place Details：取得店家第一張照片的資源名稱。

        photo_name（格式 `places/{place_id}/photos/{photo_id}`）本身**不會過期**，
        適合持久化至資料庫；真正有時效的是 get_photo_url() 用它換回來的簽章網址。
        兩者分開後，後續每次換新網址只需 1 次 Media 呼叫，不必重複查 Place Details。

        Parameters
        ----------
        place_id : str
            Google Places 店家 ID（格式：ChIJ...）。

        Returns
        -------
        Optional[str]
            照片資源名稱，查無照片或失敗時回傳 None。
        """
        if not self.api_key:
            return None

        if not check_and_increment("google_maps_api"):
            return None

        try:
            url = f"{PLACES_NEW_BASE_URL}/places/{place_id}"
            headers = {
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": "photos",
            }
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            photos = response.json().get("photos", [])
            if not photos:
                return None
            return photos[0].get("name")
        except Exception as e:
            print(f"{RED}STEP ERROR: 用 place_id 取得照片資源名稱失敗: {e}{RESET}")
            return None

    def get_photo_by_place_id(self, place_id: str, max_height_px: int = 800) -> Optional[str]:
        """
        Places API (New)：直接用 place_id 取得照片 CDN URL（Details + Media 兩次呼叫）。

        供離線批次腳本使用。回傳的是有時效的簽章網址，**不應長期持久化**
        （見 services/photo_service.py 的說明）。

        Parameters
        ----------
        place_id : str
            Google Places 店家 ID（格式：ChIJ...）。
        max_height_px : int, optional
            照片最大高度 (px)，預設為 800。

        Returns
        -------
        Optional[str]
            照片 CDN URL，若失敗則回傳 None。
        """
        photo_name = self.get_photo_name_by_place_id(place_id)
        if not photo_name:
            return None
        return self.get_photo_url(photo_name, max_height_px)

    def get_opening_hours_by_place_id(self, place_id: str) -> Optional[Dict[str, Any]]:
        """
        Places API (New) Place Details：用 place_id 取得營業時間。

        Field Masking 僅請求 regularOpeningHours，供「現在有開的店」查詢使用。
        每間店僅需 1 次 API 呼叫。

        Parameters
        ----------
        place_id : str
            Google Places 店家 ID（格式：ChIJ...）。

        Returns
        -------
        Optional[Dict[str, Any]]
            {"periods": [...], "weekday_text": [...]}；periods 為 API 原生
            {open:{day,hour,minute}, close:{...}} 結構。查無營業時間時 periods 為空清單，
            API 呼叫失敗時回傳 None。
        """
        if not self.api_key:
            return None

        if not check_and_increment("google_maps_api"):
            return None

        try:
            url = f"{PLACES_NEW_BASE_URL}/places/{place_id}"
            headers = {
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": "regularOpeningHours",
            }
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            hours = data.get("regularOpeningHours", {})
            return {
                "periods": hours.get("periods", []),
                "weekday_text": hours.get("weekdayDescriptions", []),
            }
        except Exception as e:
            print(f"{RED}STEP ERROR: 用 place_id 取得營業時間失敗: {e}{RESET}")
            return None

    def get_photo_url(
        self,
        photo_name: str,
        max_height_px: int = 800,
        max_width_px: int = 1000,
    ) -> Optional[str]:
        """
        Places API (New) Media：將照片資源名稱轉換為有效的 HTTPS URL。

        同時指定高與寬的上限，確保回傳圖片的兩邊都不超過 LINE Flex Message
        的 1024px 限制（僅限高時，橫幅照片的寬可達 1067px 而無法顯示）。

        Parameters
        ----------
        photo_name : str
            Places API (New) 回傳的照片資源名稱，
            格式為 'places/{place_id}/photos/{photo_id}'。
        max_height_px : int, optional
            照片最大高度 (px)，預設為 800。
        max_width_px : int, optional
            照片最大寬度 (px)，預設為 1000（留 24px 餘裕給 LINE 的 1024 上限）。

        Returns
        -------
        Optional[str]
            照片的直接 HTTPS URL，若失敗則回傳 None。
        """
        if not self.api_key:
            return None

        if not check_and_increment("google_maps_api"):
            return None

        try:
            url = f"{PLACES_NEW_BASE_URL}/{photo_name}/media"
            params = {
                "maxHeightPx": max_height_px,
                "maxWidthPx": max_width_px,
                "skipHttpRedirect": "true",
                "key": self.api_key,
            }
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json().get("photoUri")
        except Exception as e:
            print(f"{RED}STEP ERROR: 獲取照片 URL 失敗: {e}{RESET}")
            return None


if __name__ == "__main__":
    # 測試程式碼
    service = GoogleMapsService()

    # 測試 1: Geocoding API - 獲取經緯度
    addr = "台北市南港區"
    latlng = service.get_latlng(addr)
    print(f"地址: {addr} -> 經緯度: {latlng}")

    # 測試 2: Places API (New) - 搜尋店家詳細資訊
    shop_name = "極濃豚骨一番"
    shop_loc = "南港"
    details = service.get_shop_details(shop_name, shop_loc)
    if details:
        print(f"店家名稱: {details.get('name')}")
        print(f"評分: {details.get('rating')} ({details.get('user_ratings_total')} 則評論)")
        print(f"地址: {details.get('formatted_address')}")
        print(f"照片 URL: {details.get('photo_url')}")
    else:
        print("找不到店家資訊")
