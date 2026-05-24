import json
import os
import datetime
from typing import Optional

from services.google_maps import GoogleMapsService

RED = '\033[91m'
YELLOW = '\033[93m'
GREEN = '\033[92m'
RESET = '\033[0m'

DATA_PATH = os.path.join('data', 'ramen_data.json')
USE_FIRESTORE = os.getenv("DATA_BACKEND", "local") == "firestore"


class InfoSkill:
    """
    特定店家資訊技能模組，具備 7 天 TTL 本地快取機制。
    """

    def __init__(self) -> None:
        self.gmaps_service = GoogleMapsService()

    def _load_data(self) -> list:
        """
        從資料庫讀取所有店家資料。

        Returns
        -------
        list
            店家資料清單，讀取失敗時回傳空清單。
        """
        if USE_FIRESTORE:
            try:
                from services.firestore_client import get_db
                db = get_db()
                return [doc.to_dict() for doc in db.collection("ramen_shops").stream()]
            except Exception as e:
                print(f"{RED}ERROR: Firestore 讀取失敗: {e}{RESET}")
                return []
        try:
            with open(DATA_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save_data(self, data: list) -> None:
        """
        將店家資料清單寫回資料庫。

        Parameters
        ----------
        data : list
            要寫入的店家資料清單。
        """
        if USE_FIRESTORE:
            try:
                from services.firestore_client import get_db
                db = get_db()
                batch = db.batch()
                for shop in data:
                    doc_id = str(shop.get("id") or shop.get("name", "unknown")).replace("/", "_")
                    batch.set(db.collection("ramen_shops").document(doc_id), shop, merge=True)
                batch.commit()
            except Exception as e:
                print(f"{RED}ERROR: Firestore 寫入失敗: {e}{RESET}")
            return
        try:
            with open(DATA_PATH, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"{RED}ERROR: 寫入資料庫失敗: {e}{RESET}")

    def get_shop_info(self, shop_name: str, location: str = "") -> Optional[dict]:
        """
        取得店家資訊，具備 7 天 TTL 快取機制。

        Parameters
        ----------
        shop_name : str
            目標店家名稱。
        location : str, optional
            店家所在區域，用於縮小搜尋範圍。

        Returns
        -------
        Optional[dict]
            店家資料字典，若查無資料則回傳 None。
        """
        all_shops = self._load_data()

        target_shop = next((s for s in all_shops if s['name'] == shop_name), None)

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
            print(f"{GREEN}STEP 2: 從 Google Maps 取得 {shop_name} 的最新資料{RESET}")
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
                    new_info["location"] = location
                    new_info["style"] = "未知"
                    all_shops.append(new_info)
                    target_shop = new_info

                self._save_data(all_shops)
            else:
                print(f"{RED}STEP 2 ERROR: 找不到店家詳細資訊 {shop_name}{RESET}")

        return target_shop


if __name__ == "__main__":
    skill = InfoSkill()
    info = skill.get_shop_info("辣麻味噌沾麵 鬼金棒 中山店", "中山區")
    print(f"Final Info: {info}")
