"""
API 資料批次更新腳本

功能：讀取 ramen_data.json，對每筆缺少 place_id 的店家呼叫
Places API (New)，補齊 place_id、rating、user_ratings_total、
image_url、last_updated，並回寫 JSON。

執行方式：
    python update_api_data.py
"""

import json
import os
import datetime
from services.google_maps import GoogleMapsService

# <使用者自訂變數>
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
MAGAENTA = "\033[95m"
RESET = "\033[0m"

DATA_PATH = os.path.join("data", "ramen_data.json")


def run() -> None:
    """
    主執行流程：批次補齊 ramen_data.json 中缺少 API 資料的店家。
    """
    # STEP 1: 讀取資料
    print(f"{GREEN}STEP 1: 讀取 {DATA_PATH}{RESET}")
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            all_shops = json.load(f)
    except FileNotFoundError:
        print(f"{RED}STEP 1 ERROR: 找不到 {DATA_PATH}{RESET}")
        return
    except json.JSONDecodeError as e:
        print(f"{RED}STEP 1 ERROR: JSON 格式錯誤: {e}{RESET}")
        return

    print(f"  共 {len(all_shops)} 筆店家資料")

    # STEP 2: 篩選需要更新的店家（缺少 place_id）
    print(f"{GREEN}STEP 2: 篩選缺少 place_id 的店家{RESET}")
    needs_update = [s for s in all_shops if not s.get("place_id")]
    skipped = len(all_shops) - len(needs_update)

    print(f"  需更新：{len(needs_update)} 筆 / 已有 place_id 跳過：{skipped} 筆")

    if not needs_update:
        print(f"{CYAN}  所有店家已有 API 資料，無需更新。{RESET}")
        return

    # STEP 3: 逐筆呼叫 Places API (New)
    print(f"{GREEN}STEP 3: 開始呼叫 Places API (New){RESET}")
    gmaps = GoogleMapsService()
    success_count = 0
    fail_list = []

    for shop in needs_update:
        name = shop.get("name", "未知店名")
        location = shop.get("location", "")

        print(f"  正在處理：{name}（{location}）")
        try:
            details = gmaps.get_shop_details(name, location)
            if details:
                shop["place_id"] = details.get("place_id")
                shop["rating"] = details.get("rating")
                shop["user_ratings_total"] = details.get("user_ratings_total")
                if details.get("image_url") or details.get("photo_url"):
                    shop["image_url"] = details.get("photo_url") or details.get("image_url")
                shop["last_updated"] = datetime.datetime.now().isoformat()
                success_count += 1
                print(f"{CYAN}    => 成功：place_id={details.get('place_id')}, rating={details.get('rating')}{RESET}")
            else:
                print(f"{YELLOW}    => 找不到店家資料，略過{RESET}")
                fail_list.append(name)
        except Exception as e:
            print(f"{RED}STEP 3 ERROR: [{name}] API 呼叫失敗: {e}{RESET}")
            fail_list.append(name)

    # STEP 4: 回寫 JSON
    print(f"{GREEN}STEP 4: 回寫 {DATA_PATH}{RESET}")
    try:
        with open(DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(all_shops, f, ensure_ascii=False, indent=2)
        print(f"{CYAN}  => 儲存完成{RESET}")
    except Exception as e:
        print(f"{RED}STEP 4 ERROR: 儲存失敗: {e}{RESET}")
        return

    # 結果摘要
    print(f"\n{MAGAENTA}{'=' * 40}")
    print(f"執行完畢：成功 {success_count} 筆 / 失敗 {len(fail_list)} 筆")
    if fail_list:
        print(f"失敗店家：{', '.join(fail_list)}")
    print(f"{'=' * 40}{RESET}")


if __name__ == "__main__":
    run()
