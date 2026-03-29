import os
import googlemaps
from dotenv import load_dotenv

load_dotenv()

class GoogleMapsService:
    def __init__(self):
        self.api_key = os.getenv("GOOGLE_MAPS_API_KEY")
        if self.api_key:
            self.gmaps = googlemaps.Client(key=self.api_key)
        else:
            self.gmaps = None

    def get_place_id(self, name, location=""):
        """透過店名與地區獲取 place_id"""
        if not self.gmaps:
            return None
        
        query = f"{location} {name}" if location else name
        try:
            # 使用 find_place 獲取 place_id
            result = self.gmaps.find_place(
                input=query,
                input_type="textquery",
                fields=["place_id"]
            )
            if result.get("status") == "OK" and result.get("candidates"):
                return result["candidates"][0]["place_id"]
        except Exception as e:
            print(f"Error getting place_id: {e}")
        return None

    def get_place_details(self, place_id):
        """透過 place_id 獲取詳細資訊 (rating, user_ratings_total, photo_reference)"""
        if not self.gmaps:
            return None
        
        try:
            # 限制欄位以節省費用 (Field Masking)
            # 舊版 API 欄位：rating, user_ratings_total, photos, formatted_address
            result = self.gmaps.place(
                place_id=place_id,
                fields=["name", "rating", "user_ratings_total", "photo", "formatted_address", "geometry"]
            )
            if result.get("status") == "OK":
                return result.get("result")
        except Exception as e:
            print(f"Error getting place details: {e}")
        return None

    def get_photo_url(self, photo_reference, max_width=400):
        """獲取照片的有效 URL"""
        if not self.gmaps or not photo_reference:
            return None
        
        # 這裡其實是返回一個直接可以用的 URL 或是處理過的連結
        # googlemaps client 的 photo() 會返回一個 generator 包含原始數據
        # 實際上我們可能需要組合成一個直接可用的 API 連結，或者下載後再處理
        # 為了簡化，我們先回傳 Google Maps 的 Photo API URL 模板 (需要 API KEY)
        return f"https://maps.googleapis.com/maps/api/place/photo?maxwidth={max_width}&photoreference={photo_reference}&key={self.api_key}"

if __name__ == "__main__":
    # 測試
    service = GoogleMapsService()
    pid = service.get_place_id("極濃豚骨一番", "南港")
    print(f"Place ID: {pid}")
    if pid:
        details = service.get_place_details(pid)
        print(f"Details: {details}")
