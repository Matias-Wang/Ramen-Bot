import os
from typing import Optional, Dict, Any
import googlemaps
from dotenv import load_dotenv

load_dotenv()

# <使用者自訂變數>
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
BLUE = "\033[94m"
CYAN = "\033[96m"
MAGAENTA = "\033[95m"
RESET = "\033[0m"


class GoogleMapsService:
    """
    Google Maps API 服務類別

    這個類別封裝了與 Google Maps API 相關的功能，包括獲取經緯度、店家詳細資訊等。
    注意：使用 Google Maps API 可能會產生費用，請確保在開發和測試過程中合理使用 API。
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
        將地址轉換為經緯度座標。用於提供地理位置相關的查詢。

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

        print(f"{GREEN}STEP: 正在獲取地址經緯度 - {address}{RESET}")
        try:
            geocode_result = self.gmaps.geocode(address, language="zh-TW")
            if geocode_result:
                location = geocode_result[0]["geometry"]["location"]
                return {"lat": location["lat"], "lng": location["lng"]}
        except Exception as e:
            print(f"{RED}STEP ERROR: 獲取經緯度失敗: {e}{RESET}")

        return None

    def get_shop_details(self, name: str, location: str = "") -> Optional[Dict[str, Any]]:
        """
        透過店名搜尋即時資訊 (含星等、照片)。

        Parameters
        ----------
        name : str
            店名。
        location : str, optional
            搜尋的地區範圍，預設為空字串。

        Returns
        -------
        Optional[Dict[str, Any]]
            包含店家詳細資訊的字典，包含名稱、評分、總評分數、格式化地址及照片 URL。
            若找不到則回傳 None。
        """
        if not self.gmaps:
            return None

        query = f"{location} {name}" if location else name
        print(f"{GREEN}STEP: 正在搜尋店家詳細資訊 - {query}{RESET}")

        try:
            # 1. 搜尋 Place ID
            place_result = self.gmaps.find_place(
                input=query, input_type="textquery", fields=["place_id"]
            )

            if place_result.get("status") != "OK" or not place_result.get("candidates"):
                print(f"{YELLOW}找不到店家: {query}{RESET}")
                return None

            place_id = place_result["candidates"][0]["place_id"]

            # 2. 透過 Place ID 獲取詳細資訊
            details_result = self.gmaps.place(
                place_id=place_id,
                fields=[
                    "name",
                    "rating",
                    "user_ratings_total",
                    "photo",
                    "formatted_address",
                    "geometry",
                ],
                language="zh-TW",
            )

            if details_result.get("status") == "OK":
                result = details_result.get("result", {})

                # 處理照片 URL (若有照片引用)
                if result.get("photos"):
                    photo_ref = result["photos"][0]["photo_reference"]
                    result["photo_url"] = self.get_photo_url(photo_ref)

                return result

        except Exception as e:
            print(f"{RED}STEP ERROR: 獲取店家詳細資訊失敗: {e}{RESET}")

        return None

    def get_photo_url(self, photo_reference: str, max_width: int = 400) -> str:
        """
        獲取照片的有效 URL。

        Parameters
        ----------
        photo_reference : str
            Google Maps API 回傳的照片引用字串。
        max_width : int, optional
            照片最大寬度，預設為 400。

        Returns
        -------
        str
            照片的直接連結 URL。
        """
        return (
            f"https://maps.googleapis.com/maps/api/place/photo"
            f"?maxwidth={max_width}&photoreference={photo_reference}&key={self.api_key}"
        )


if __name__ == "__main__":
    # 測試程式碼
    service = GoogleMapsService()

    # 測試 1: 獲取經緯度
    addr = "台北市南港區"
    latlng = service.get_latlng(addr)
    print(f"地址: {addr} -> 經緯度: {latlng}")

    # 測試 2: 搜尋店家詳細資訊
    shop_name = "極濃豚骨一番"
    shop_loc = "南港"
    details = service.get_shop_details(shop_name, shop_loc)
    if details:
        print(f"店家名稱: {details.get('name')}")
        print(f"評分: {details.get('rating')} ({details.get('user_ratings_total')} 則評論)")
        print(f"照片 URL: {details.get('photo_url')}")
        print(f"地址: {details.get('formatted_address')}")
