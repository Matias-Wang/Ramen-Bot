import copy
import datetime
import difflib
import json
import os
from typing import Optional

from services.google_maps import GoogleMapsService

# 模糊比對門檻：低於此相似度視為「查無此店」，避免誤配到不相關店家
FUZZY_MATCH_THRESHOLD = 0.5
# 地區相符時的額外加分，用於同名/相似名候選間的排序
LOCATION_MATCH_BONUS = 0.1


def _find_shop_by_name(all_shops: list, shop_name: str, location: str = "") -> Optional[dict]:
    """
    在店家清單中尋找最符合 shop_name 的店家。

    使用者口語化的店名（如「麒麟拉麵」）常與資料庫完整店名
    （如「麒麟創作拉麵坊」）不完全相同，因此先做完全比對，
    再以 difflib 相似度做模糊比對，並用 location 是否相符加分排序。

    Parameters
    ----------
    all_shops : list
        店家資料清單。
    shop_name : str
        目標店家名稱（可能不完整或為口語化簡稱）。
    location : str, optional
        店家所在區域，用於相似度相近時的排序加分。

    Returns
    -------
    Optional[dict]
        最符合的店家資料字典，找不到則回傳 None。
    """
    if not shop_name:
        return None

    for s in all_shops:
        if s.get("name") == shop_name:
            return s

    best_shop, best_score = None, 0.0
    for s in all_shops:
        name = s.get("name") or ""
        score = difflib.SequenceMatcher(None, shop_name, name).ratio()
        if location and location in (s.get("location") or ""):
            score += LOCATION_MATCH_BONUS
        if score > best_score:
            best_shop, best_score = s, score

    return best_shop if best_score >= FUZZY_MATCH_THRESHOLD else None

# <使用者自訂變數>
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
BLUE = "\033[94m"
CYAN = "\033[96m"
MAGAENTA = "\033[95m"
RESET = "\033[0m"

DATA_PATH = os.path.join("data", "ramen_data.json")
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

        Firestore 模式下使用 Search_skill 的 24 小時模組層級快取，
        避免每次 GET_SPECIFIC_INFO 請求重複串流 178 筆文件。
        回傳深複製以防止外部修改汙染快取原始資料。

        Returns
        -------
        list
            店家資料清單，讀取失敗時回傳空清單。
        """
        if USE_FIRESTORE:
            try:
                from skills.Search_skill import _load_all_shops
                return copy.deepcopy(_load_all_shops())
            except Exception as e:
                print(f"{RED}STEP 2 ERROR:{e}{RESET}")
                return []
        try:
            with open(DATA_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"{RED}STEP 2 ERROR:{e}{RESET}")
            return []

    def _save_one_shop(self, shop: dict) -> None:
        """
        將單一店家資料寫回資料庫。

        Firestore 模式下只寫入一筆文件（merge=True），取代舊版批次寫入全部 178 筆。
        本地模式下讀出完整 JSON、更新對應條目後寫回。

        Parameters
        ----------
        shop : dict
            要寫入的店家資料字典。
        """
        if USE_FIRESTORE:
            try:
                from services.firestore_client import get_db

                db = get_db()
                raw_id = shop.get("id") or shop.get("name", "unknown")
                doc_id = str(raw_id).replace("/", "_")
                db.collection("ramen_shops").document(doc_id).set(shop, merge=True)
            except Exception as e:
                print(f"{RED}STEP 2 ERROR:{e}{RESET}")
            return
        try:
            with open(DATA_PATH, "r", encoding="utf-8") as f:
                all_shops = json.load(f)
            for i, s in enumerate(all_shops):
                if s.get("name") == shop.get("name"):
                    all_shops[i] = shop
                    break
            else:
                all_shops.append(shop)
            with open(DATA_PATH, "w", encoding="utf-8") as f:
                json.dump(all_shops, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"{RED}STEP 2 ERROR:{e}{RESET}")

    def get_shop_info(
        self, shop_name: str, location: str = ""
    ) -> Optional[dict]:
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
        location = location or ""
        all_shops = self._load_data()

        target_shop = _find_shop_by_name(all_shops, shop_name, location)

        needs_update = False
        if not target_shop:
            needs_update = True
        elif not target_shop.get("place_id"):
            needs_update = True
        elif target_shop.get("last_updated"):
            last_date = datetime.datetime.fromisoformat(
                target_shop["last_updated"]
            )
            if (datetime.datetime.now() - last_date).days > 7:
                needs_update = True
        else:
            needs_update = True

        if needs_update:
            print(
                f"{GREEN}STEP 2: 從 Google Maps 取得 {shop_name} 的最新資料{RESET}"
            )
            _was_existing = target_shop is not None
            details = self.gmaps_service.get_shop_details(shop_name, location)

            if details:
                fallback_img = (
                    target_shop.get("image_url") if target_shop else None
                )
                new_info = {
                    "name": details.get("name", shop_name),
                    "place_id": details.get("place_id"),
                    "rating": details.get("rating"),
                    "user_ratings_total": details.get("user_ratings_total"),
                    "address": details.get("formatted_address"),
                    "image_url": details.get("photo_url") or fallback_img,
                    "last_updated": datetime.datetime.now().isoformat(),
                }

                if target_shop:
                    # 既有店家已由 _find_shop_by_name 鎖定，name/description/style/
                    # location 須保持來自同一筆資料，不可被 Google Places 的 name 覆蓋
                    new_info.pop("name", None)
                    target_shop.update(new_info)
                else:
                    new_info["location"] = location
                    new_info["style"] = ""
                    new_info["description"] = ""
                    target_shop = new_info

                # 只對原本就存在資料庫的店家執行快取回寫，
                # 避免 Google Maps 查到的新店污染 ramen_shops 搜尋資料集
                if _was_existing:
                    self._save_one_shop(target_shop)
            else:
                # We can construct a custom exception or error string to conform to step error format
                print(f"{RED}STEP 2 ERROR:找不到店家詳細資訊 {shop_name}{RESET}")

        return target_shop


if __name__ == "__main__":
    skill = InfoSkill()
    info = skill.get_shop_info("辣麻味噌沾麵 鬼金棒 中山店", "中山區")
    print(f"Final Info: {info}")
