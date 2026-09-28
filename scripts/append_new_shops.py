"""
將新抓取的拉麵店資料附加至 ramen_data.json

使用方式：
    python scripts/append_new_shops.py <新資料檔案路徑>

說明：
    直接將新資料檔案中的店家清單附加到現有 ramen_data.json 後方，
    不檢查是否與既有店家重複。執行前會自動備份原始 ramen_data.json
    至 data/backup/。
"""

import argparse
import json
import os
import shutil
from datetime import datetime

# <使用者自訂變數>
RED = "\033[91m"
GREEN = "\033[92m"
RESET = "\033[0m"

RAMEN_DATA_PATH = os.path.join("data", "ramen_data.json")
BACKUP_DIR = os.path.join("data", "backup")


def load_json(path: str) -> list[dict]:
    """讀取 JSON 檔案並回傳店家清單。

    Parameters
    ----------
    path : str
        JSON 檔案路徑。

    Returns
    -------
    list[dict]
        店家資料清單。
    """
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def backup_ramen_data() -> str:
    """備份現有 ramen_data.json 至 data/backup/。

    Returns
    -------
    str
        備份檔案路徑。
    """
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"ramen_data_backup_{timestamp}.json")
    shutil.copy2(RAMEN_DATA_PATH, backup_path)
    return backup_path


def main() -> None:
    """主流程：將新資料附加至 ramen_data.json。"""
    parser = argparse.ArgumentParser(description="附加新拉麵店資料至 ramen_data.json")
    parser.add_argument(
        "input_file", help="新資料 JSON 檔案路徑（店家清單格式同 ramen_data.json）"
    )
    args = parser.parse_args()

    print(f"{GREEN}STEP 1: 讀取現有 {RAMEN_DATA_PATH}{RESET}")
    try:
        existing = load_json(RAMEN_DATA_PATH)
        print(f"  現有 {len(existing)} 筆")
    except Exception as e:
        print(f"{RED}STEP 1 ERROR:{e}{RESET}")
        return

    print(f"{GREEN}STEP 2: 讀取新資料 {args.input_file}{RESET}")
    try:
        new_data = load_json(args.input_file)
        print(f"  新增 {len(new_data)} 筆")
    except Exception as e:
        print(f"{RED}STEP 2 ERROR:{e}{RESET}")
        return

    print(f"{GREEN}STEP 3: 備份現有 ramen_data.json{RESET}")
    try:
        backup_path = backup_ramen_data()
        print(f"  已備份至 {backup_path}")
    except Exception as e:
        print(f"{RED}STEP 3 ERROR:{e}{RESET}")
        return

    print(f"{GREEN}STEP 4: 附加新資料並寫回{RESET}")
    try:
        merged = existing + new_data
        with open(RAMEN_DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        print(f"  完成，共 {len(merged)} 筆（原 {len(existing)} + 新增 {len(new_data)}）")
    except Exception as e:
        print(f"{RED}STEP 4 ERROR:{e}{RESET}")
        return


if __name__ == "__main__":
    main()
