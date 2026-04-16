import json
import os
import datetime
from services.google_maps import GoogleMapsService

DATA_PATH = os.path.join('data', 'ramen_data.json')

class InfoSkill:
    def __init__(self):
        self.gmaps_service = GoogleMapsService()

    def _load_data(self):
        try:
            with open(DATA_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save_data(self, data):
        try:
            with open(DATA_PATH, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving data: {e}")

    def get_shop_info(self, shop_name, location=""):
        """
        獲取店家資訊，具備快取機制。
        1. 檢查本地資料庫
        2. 若無 place_id 或資料過舊，呼叫 Google Maps API
        3. 回寫本地資料庫
        """
        all_shops = self._load_data()
        
        # 尋找本地店家
        target_shop = next((s for s in all_shops if s['name'] == shop_name), None)
        
        # 判斷是否需要更新 (若無 place_id 或超過 7 天)
        needs_update = False
        if not target_shop:
            needs_update = True
        elif not target_shop.get('place_id'):
            needs_update = True
        elif target_shop.get('last_updated'):
            last_date = datetime.datetime.fromisoformat(target_shop['last_updated'])
            if (datetime.datetime.now() - last_date).days > 7:
                needs_update = True
        else:
            needs_update = True

        if needs_update:
            print(f"Fetching fresh data for {shop_name} from Google Maps...")
            details = self.gmaps_service.get_shop_details(shop_name, location)

            if details:
                new_info = {
                    "name": details.get('name', shop_name),
                    "place_id": details.get('place_id'),
                    "rating": details.get('rating'),
                    "user_ratings_total": details.get('user_ratings_total'),
                    "address": details.get('formatted_address'),
                    "image_url": details.get('photo_url') or (target_shop.get('image_url') if target_shop else None),
                    "last_updated": datetime.datetime.now().isoformat()
                }

                if target_shop:
                    target_shop.update(new_info)
                else:
                    # 如果原本不在資料庫，新增一筆 (這裡可能需要更多欄位，暫以 details 為主)
                    new_info["location"] = location
                    new_info["style"] = "未知"
                    all_shops.append(new_info)
                    target_shop = new_info

                self._save_data(all_shops)
            else:
                print(f"Could not find shop details for {shop_name}")

        return target_shop

if __name__ == "__main__":
    # 測試
    skill = InfoSkill()
    info = skill.get_shop_info("辣麻味噌沾麵 鬼金棒 中山店", "中山區")
    print(f"Final Info: {info}")
