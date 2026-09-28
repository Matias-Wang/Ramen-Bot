"""
批次補全店家 place_id 預處理腳本

掃描 ramen_data.json，對所有缺少 place_id 的店家呼叫
Google Places API (New) 取得 place_id、評分、評論數、地址與照片。

每次呼叫前會檢查每日 API 配額，達上限後自動停止。

使用方式：
    python scripts/update_api_data.py
    python scripts/update_api_data.py --dry-run   # 僅列出缺少 place_id 的店家，不呼叫 API
"""

import argparse
import json
import os
import sys
import datetime

import requests

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
from services.google_maps import GoogleMapsService
from core.usage_tracker import check_and_increment

load_dotenv()

# <使用者自訂變數>
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
MAGAENTA = "\033[95m"
RESET = "\033[0m"

DATA_PATH = os.path.join("data", "ramen_data.json")


def _load_data() -> list[dict]:
    """
    從本地 JSON 載入所有店家資料。

    Returns
    -------
    list[dict]
        店家清單，讀取失敗時回傳空清單。
    """
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"{RED}STEP 1 ERROR: 找不到 {DATA_PATH}{RESET}")
        return []
    except json.JSONDecodeError as e:
        print(f"{RED}STEP 1 ERROR: JSON 格式錯誤: {e}{RESET}")
        return []


def _save_data(data: list[dict]) -> None:
    """
    將更新後的店家清單寫回 JSON。

    Parameters
    ----------
    data : list[dict]
        要寫入的店家清單。
    """
    try:
        with open(DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"{RED}STEP 4 ERROR: 寫入失敗: {e}{RESET}")


def run(dry_run: bool = False) -> None:
    """
    執行批次補全流程（補全缺少 place_id 的店家）。

    Parameters
    ----------
    dry_run : bool
        若為 True，僅列印缺少 place_id 的店家，不呼叫 API 也不寫檔。
    """
    print(f"{GREEN}STEP 1: 載入 {DATA_PATH}{RESET}")
    shops = _load_data()
    if not shops:
        return

    missing = [s for s in shops if not s.get("place_id")]
    print(f"  總計 {len(shops)} 筆，缺少 place_id：{len(missing)} 筆")

    if not missing:
        print(f"{CYAN}  => 所有店家均已有 place_id，無需更新。{RESET}")
        return

    if dry_run:
        print(f"{YELLOW}--- Dry-run 模式，僅列印缺少 place_id 的店家 ---{RESET}")
        for s in missing:
            print(f"  {s.get('name', '未知')} | 地區: {s.get('location', '')} | "
                  f"地址: {s.get('address', '')}")
        return

    print(f"{GREEN}STEP 2: 初始化 Google Maps 服務{RESET}")
    gmaps = GoogleMapsService()
    updated = 0
    failed: list[str] = []

    print(f"{GREEN}STEP 3: 開始逐筆補全（共 {len(missing)} 筆）{RESET}")
    for shop in missing:
        name = shop.get("name", "未知店名")
        location = shop.get("location", "")

        if not check_and_increment("google_maps_api"):
            print(f"{RED}  => Google Maps API 每日配額已達上限，停止更新。{RESET}")
            break

        print(f"  查詢：{name}（{location}）")
        try:
            details = gmaps.get_shop_details(name, location)
        except Exception as e:
            print(f"{RED}  ERROR: {name} 查詢失敗: {e}{RESET}")
            failed.append(name)
            continue

        if not details:
            print(f"{YELLOW}  [SKIP] 找不到 {name} 的 API 資訊{RESET}")
            failed.append(name)
            continue

        shop["place_id"] = details.get("place_id")
        shop["rating"] = details.get("rating") or shop.get("rating")
        shop["user_ratings_total"] = (
            details.get("user_ratings_total") or shop.get("user_ratings_total")
        )
        shop["address"] = details.get("formatted_address") or shop.get("address")
        shop["image_url"] = details.get("photo_url") or shop.get("image_url")
        shop["last_updated"] = datetime.datetime.now().isoformat()
        updated += 1
        print(f"{CYAN}  [OK] {name} — place_id: {shop['place_id']}, rating: {shop['rating']}{RESET}")

    print(f"{GREEN}STEP 4: 回寫 {DATA_PATH}{RESET}")
    _save_data(shops)

    print(f"\n{MAGAENTA}{'=' * 40}")
    print(f"執行完畢：成功 {updated} 筆 / 失敗或略過 {len(failed)} 筆")
    if failed:
        print(f"失敗店家：{', '.join(failed)}")
    print(f"{'=' * 40}{RESET}")


def _is_photo_url_alive(url: str, timeout: float = 5.0) -> bool:
    """
    對圖片網址發 HEAD request，確認是否仍可載入。

    Google Places API (New) 回傳的 photoUri 是有時效性的簽章網址，
    過期後伺服器會回 403，LINE 端則直接顯示空白圖，且字串本身仍是
    合法的 https://lh3... 開頭，無法單靠字串判斷是否失效。

    Parameters
    ----------
    url : str
        要檢查的圖片網址。
    timeout : float
        逾時秒數，預設 5 秒。

    Returns
    -------
    bool
        HTTP 狀態碼為 200 回傳 True；逾時、連線失敗或非 200 一律視為失效。
    """
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def run_update_photos(dry_run: bool = False) -> None:
    """
    補全 image_url 模式：對所有缺少有效圖片、或圖片網址已過期的店家，
    用已儲存的 place_id 直接呼叫 Places API 取得照片 CDN URL，並記錄
    `image_url_renew_date`（取得時刻）供觀測簽章網址的實際存活時間。

    每間店需 2 次 API 呼叫（Place Details + Photo Media）。本腳本為離線維護
    工具，不屬於面向使用者的流量，可用環境變數 `E2E_TEST_MODE=1` 豁免
    `usage_tracker` 的每日配額，一次跑完全部店家：

        E2E_TEST_MODE=1 PYTHONUTF8=1 python scripts/update_api_data.py --update-photos

    未設此變數時仍受每日 100 次上限約束（約每天 50 間）。

    完成後需另行執行 migrate_to_firestore.py --mode=sync 同步至 Firestore。

    Parameters
    ----------
    dry_run : bool
        若為 True，僅列印需要更新的店家清單，不呼叫 API。
    """
    print(f"{GREEN}STEP 1: 載入 {DATA_PATH}{RESET}")
    shops = _load_data()
    if not shops:
        return

    print(f"{GREEN}STEP 2: 檢查現有圖片網址是否仍可載入（HEAD request）{RESET}")
    needs_photo = []
    for s in shops:
        if not s.get("place_id"):
            continue
        image_url = str(s.get("image_url", ""))
        safe_name = s.get("name", "未知").encode("cp950", errors="replace").decode("cp950")
        if not image_url.startswith("https://lh3"):
            needs_photo.append(s)
        elif not _is_photo_url_alive(image_url):
            print(f"{YELLOW}  [過期] {safe_name}{RESET}")
            needs_photo.append(s)
    print(f"  總計 {len(shops)} 筆，需更新圖片：{len(needs_photo)} 筆")

    if not needs_photo:
        print(f"{CYAN}  => 所有店家均已有有效圖片 URL，無需更新。{RESET}")
        return

    if dry_run:
        print(f"{YELLOW}--- Dry-run 模式，僅列印需要更新圖片的店家 ---{RESET}")
        for s in needs_photo:
            print(f"  {s.get('name', '未知')} | image_url: {s.get('image_url', '無')}")
        print(f"\n預計需要 {len(needs_photo) * 2} 次 API 呼叫，"
              f"每日上限 100 次，需分 {(len(needs_photo) * 2 + 99) // 100} 天執行。")
        return

    print(f"{GREEN}STEP 3: 初始化 Google Maps 服務{RESET}")
    gmaps = GoogleMapsService()
    updated = 0
    failed: list[str] = []

    print(f"{GREEN}STEP 4: 開始逐筆取得照片（共 {len(needs_photo)} 筆）{RESET}")
    for shop in needs_photo:
        name = shop.get("name", "未知店名")
        place_id = shop.get("place_id")
        safe_name = name.encode("cp950", errors="replace").decode("cp950")

        print(f"  取得照片：{safe_name}（place_id: {place_id}）")
        try:
            photo_url = gmaps.get_photo_by_place_id(place_id)
        except Exception as e:
            print(f"{RED}  ERROR: {safe_name} 取得照片失敗: {e}{RESET}")
            failed.append(name)
            continue

        if not photo_url:
            print(f"{YELLOW}  [SKIP] 找不到 {safe_name} 的照片{RESET}")
            failed.append(name)
            continue

        shop["image_url"] = photo_url
        # 記錄本次網址取得時刻（UTC ISO 8601，帶時區），供觀測簽章網址的實際
        # 存活時間；欄位語意與 runtime 的 Search_skill._refresh_shop_image() 一致。
        shop["image_url_renew_date"] = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()
        shop["last_updated"] = datetime.datetime.now().isoformat()
        updated += 1
        print(f"{CYAN}  [OK] {safe_name} — image_url 已更新{RESET}")

    print(f"{GREEN}STEP 5: 回寫 {DATA_PATH}{RESET}")
    _save_data(shops)

    print(f"\n{MAGAENTA}{'=' * 40}")
    print(f"圖片更新完畢：成功 {updated} 筆 / 失敗或略過 {len(failed)} 筆")
    if failed:
        safe_failed = [n.encode("cp950", errors="replace").decode("cp950") for n in failed]
        print(f"失敗店家：{', '.join(safe_failed)}")
    print(f"\n下一步：執行 python scripts/migrate_to_firestore.py --mode=sync 同步至 Firestore")
    print(f"{'=' * 40}{RESET}")


def run_update_hours(dry_run: bool = False) -> None:
    """
    補全 opening_hours 模式：對所有尚無營業時間資料的店家，
    用已儲存的 place_id 呼叫 Places API 取得 regularOpeningHours。

    每間店僅需 1 次 API 呼叫，每日限制 100 次 → 每天最多補全 100 間店。
    完成後需另行執行 migrate_to_firestore.py --mode=sync 同步至 Firestore。

    Parameters
    ----------
    dry_run : bool
        若為 True，僅列印需要更新的店家清單，不呼叫 API。
    """
    print(f"{GREEN}STEP 1: 載入 {DATA_PATH}{RESET}")
    shops = _load_data()
    if not shops:
        return

    # 找出尚無 opening_hours 欄位的店家（有 place_id 才能查）
    needs_hours = [
        s for s in shops
        if s.get("place_id") and not s.get("opening_hours")
    ]
    print(f"  總計 {len(shops)} 筆，需補營業時間：{len(needs_hours)} 筆")

    if not needs_hours:
        print(f"{CYAN}  => 所有店家均已有營業時間資料，無需更新。{RESET}")
        return

    if dry_run:
        print(f"{YELLOW}--- Dry-run 模式，僅列印需要補營業時間的店家 ---{RESET}")
        for s in needs_hours:
            print(f"  {s.get('name', '未知')} | place_id: {s.get('place_id')}")
        print(f"\n預計需要 {len(needs_hours)} 次 API 呼叫，"
              f"每日上限 100 次，需分 {(len(needs_hours) + 99) // 100} 天執行。")
        return

    print(f"{GREEN}STEP 2: 初始化 Google Maps 服務{RESET}")
    gmaps = GoogleMapsService()
    updated = 0
    failed: list[str] = []

    print(f"{GREEN}STEP 3: 開始逐筆取得營業時間（共 {len(needs_hours)} 筆）{RESET}")
    for shop in needs_hours:
        name = shop.get("name", "未知店名")
        place_id = shop.get("place_id")
        safe_name = name.encode("cp950", errors="replace").decode("cp950")

        if not check_and_increment("google_maps_api"):
            print(f"{RED}  => Google Maps API 每日配額已達上限，停止更新。{RESET}")
            break

        print(f"  取得營業時間：{safe_name}（place_id: {place_id}）")
        try:
            hours = gmaps.get_opening_hours_by_place_id(place_id)
        except Exception as e:
            print(f"{RED}  ERROR: {safe_name} 取得營業時間失敗: {e}{RESET}")
            failed.append(name)
            continue

        if hours is None:
            print(f"{YELLOW}  [SKIP] {safe_name} API 呼叫失敗{RESET}")
            failed.append(name)
            continue

        # 查無營業時間的店家亦寫入（periods 為空），避免下次重複查詢
        hours["last_updated"] = datetime.datetime.now().isoformat()
        shop["opening_hours"] = hours
        updated += 1
        n_periods = len(hours.get("periods", []))
        print(f"{CYAN}  [OK] {safe_name} — {n_periods} 個營業時段{RESET}")

    print(f"{GREEN}STEP 4: 回寫 {DATA_PATH}{RESET}")
    _save_data(shops)

    print(f"\n{MAGAENTA}{'=' * 40}")
    print(f"營業時間更新完畢：成功 {updated} 筆 / 失敗或略過 {len(failed)} 筆")
    if failed:
        safe_failed = [n.encode("cp950", errors="replace").decode("cp950") for n in failed]
        print(f"失敗店家：{', '.join(safe_failed)}")
    print(f"\n下一步：執行 python scripts/migrate_to_firestore.py --mode=sync 同步至 Firestore")
    print(f"{'=' * 40}{RESET}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="批次補全店家 place_id、圖片與營業時間")
    parser.add_argument("--dry-run", action="store_true", help="僅列印，不呼叫 API")
    parser.add_argument(
        "--update-photos",
        action="store_true",
        help="更新缺少圖片或圖片網址已過期的店家（使用 place_id 直接取照片，每間 2 次 API 呼叫）",
    )
    parser.add_argument(
        "--update-hours",
        action="store_true",
        help="補全尚無營業時間的店家（使用 place_id 取 regularOpeningHours，每間 1 次 API 呼叫）",
    )
    args = parser.parse_args()

    if args.update_photos:
        run_update_photos(dry_run=args.dry_run)
    elif args.update_hours:
        run_update_hours(dry_run=args.dry_run)
    else:
        run(dry_run=args.dry_run)
