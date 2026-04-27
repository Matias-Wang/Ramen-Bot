"""
地址 Geocoding 預處理腳本

功能：讀取 ramen_data.json，對缺少座標的店家自動呼叫
Geocoding API 補齊 lat/lng，並回寫 JSON。

執行方式：
    python geocode_shops.py
"""

import json
import os
from services.google_maps import GoogleMapsService

# <使用者自訂變數>
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
MAGAENTA = "\033[95m"
RESET = "\033[0m"

DATA_PATH = os.path.join("data", "ramen_data.json")


def _has_coordinates(shop: dict) -> bool:
    """
    判斷店家是否已有有效座標。

    Parameters
    ----------
    shop : dict
        店家資料字典。

    Returns
    -------
    bool
        True 表示已有有效座標，False 表示需要補齊。
    """
    coords = shop.get("coordinates")
    if not coords:
        return False
    lat = coords.get("lat")
    lng = coords.get("lng")
    return lat is not None and lng is not None


def run() -> None:
    """
    主執行流程：批次補齊 ramen_data.json 中缺少座標的店家。
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

    # STEP 2: 篩選需要補齊座標的店家
    print(f"{GREEN}STEP 2: 篩選缺少座標的店家{RESET}")
    needs_geocoding = [s for s in all_shops if not _has_coordinates(s)]
    skipped = len(all_shops) - len(needs_geocoding)

    print(f"  需補齊：{len(needs_geocoding)} 筆 / 已有座標跳過：{skipped} 筆")

    if not needs_geocoding:
        print(f"{CYAN}  所有店家已有座標，無需更新。{RESET}")
        return

    # STEP 3: 逐筆呼叫 Geocoding API
    print(f"{GREEN}STEP 3: 開始呼叫 Geocoding API{RESET}")
    gmaps = GoogleMapsService()
    success_count = 0
    fail_list = []

    for shop in needs_geocoding:
        name = shop.get("name", "未知店名")
        address = shop.get("address", "")

        if not address:
            print(f"{YELLOW}  [{name}] 無 address 欄位，略過{RESET}")
            fail_list.append(name)
            continue

        print(f"  正在處理：{name}（{address}）")
        try:
            coords = gmaps.get_latlng(address)
            if coords:
                shop["coordinates"] = coords
                success_count += 1
                print(f"{CYAN}    => 成功：lat={coords['lat']}, lng={coords['lng']}{RESET}")
            else:
                print(f"{YELLOW}    => 無法取得座標，略過{RESET}")
                fail_list.append(name)
        except Exception as e:
            print(f"{RED}STEP 3 ERROR: [{name}] Geocoding 失敗: {e}{RESET}")
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
