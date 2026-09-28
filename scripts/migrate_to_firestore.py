"""
Firestore 資料匯入 / 同步腳本

三種執行模式：
  --mode=import   首次匯入：將 ramen_data.json 全部寫入 Firestore（覆蓋同 ID 的文件）
  --mode=sync     定期同步：upsert 模式，比對 id 欄位，有變動就更新，新店家新增，
                  Firestore 已有但 JSON 沒有的店家保留不刪除。
  --mode=clean    清理孤兒：刪除 Firestore 中存在但本地 JSON 沒有的文件
                  （例如 info_skill 從 Google Maps 查到後寫入的非正式店家）

使用方式：
  python scripts/migrate_to_firestore.py --mode=import
  python scripts/migrate_to_firestore.py --mode=sync
  python scripts/migrate_to_firestore.py --mode=clean
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
from google.cloud import firestore

load_dotenv()

# <使用者自訂變數>
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
MAGAENTA = "\033[95m"
RESET = "\033[0m"

DATA_PATH = os.path.join("data", "ramen_data.json")
COLLECTION = "ramen_shops"


def _doc_id(shop: dict) -> str:
    """
    取得 Firestore document ID。優先使用 id 欄位，fallback 至 name。

    Parameters
    ----------
    shop : dict
        店家資料字典。

    Returns
    -------
    str
        document ID 字串。
    """
    raw = shop.get("id") or shop.get("name", "unknown")
    return str(raw).strip().replace("/", "_")


def _load_local() -> list[dict]:
    """
    從本地 ramen_data.json 讀取店家清單。

    Returns
    -------
    list[dict]
        店家資料清單。
    """
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"{RED}ERROR: 找不到 {DATA_PATH}{RESET}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"{RED}ERROR: JSON 格式錯誤: {e}{RESET}")
        sys.exit(1)


def run_import(db: firestore.Client, shops: list[dict]) -> None:
    """
    首次匯入：以 batch 寫入所有店家，同 ID 文件直接覆蓋。

    Parameters
    ----------
    db : firestore.Client
        Firestore 客戶端。
    shops : list[dict]
        要寫入的店家清單。
    """
    print(f"{GREEN}STEP 2: 首次匯入模式，共 {len(shops)} 筆{RESET}")
    col = db.collection(COLLECTION)
    BATCH_SIZE = 500
    written = 0

    for i in range(0, len(shops), BATCH_SIZE):
        batch = db.batch()
        chunk = shops[i:i + BATCH_SIZE]
        for shop in chunk:
            doc_id = _doc_id(shop)
            batch.set(col.document(doc_id), shop)
        batch.commit()
        written += len(chunk)
        print(f"{CYAN}  => {written}/{len(shops)} 筆已寫入{RESET}")

    print(f"{GREEN}STEP 2: 匯入完成，共 {written} 筆{RESET}")


def run_sync(db: firestore.Client, shops: list[dict]) -> None:
    """
    定期同步（upsert）：比對本地與 Firestore，有變動就更新，新店家新增。
    Firestore 已有但本地沒有的店家不刪除（保留歷史資料）。

    Parameters
    ----------
    db : firestore.Client
        Firestore 客戶端。
    shops : list[dict]
        本地最新的店家清單。
    """
    print(f"{GREEN}STEP 2: 同步模式，讀取 Firestore 現有資料{RESET}")
    col = db.collection(COLLECTION)

    existing: dict[str, dict] = {
        doc.id: doc.to_dict() for doc in col.stream()
    }
    print(f"  Firestore 現有：{len(existing)} 筆  |  本地：{len(shops)} 筆")

    to_add = []
    to_update = []

    for shop in shops:
        doc_id = _doc_id(shop)
        if doc_id not in existing:
            to_add.append((doc_id, shop))
        else:
            if shop != existing[doc_id]:
                to_update.append((doc_id, shop))

    print(f"  新增：{len(to_add)} 筆  |  更新：{len(to_update)} 筆  |  無異動略過：{len(shops) - len(to_add) - len(to_update)} 筆")

    if not to_add and not to_update:
        print(f"{CYAN}  => Firestore 已是最新，無需同步{RESET}")
        return

    print(f"{GREEN}STEP 3: 寫入 Firestore{RESET}")
    all_ops = to_add + to_update
    BATCH_SIZE = 500
    written = 0

    for i in range(0, len(all_ops), BATCH_SIZE):
        batch = db.batch()
        for doc_id, shop in all_ops[i:i + BATCH_SIZE]:
            batch.set(col.document(doc_id), shop, merge=True)
        batch.commit()
        written += len(all_ops[i:i + BATCH_SIZE])

    print(f"{GREEN}STEP 3: 同步完成，共異動 {written} 筆{RESET}")


def run_clean(db: firestore.Client, shops: list[dict]) -> None:
    """
    清理孤兒：刪除 Firestore 中存在但本地 JSON 沒有的文件。

    適用於清除由 info_skill 從 Google Maps 查到後誤寫入 ramen_shops 的非正式店家。

    Parameters
    ----------
    db : firestore.Client
        Firestore 客戶端。
    shops : list[dict]
        本地 JSON 店家清單（作為保留白名單）。
    """
    print(f"{GREEN}STEP 2: 清理模式，讀取 Firestore 現有文件{RESET}")
    col = db.collection(COLLECTION)

    local_ids = {_doc_id(s) for s in shops}
    existing_docs = list(col.stream())
    orphans = [doc for doc in existing_docs if doc.id not in local_ids]

    print(f"  Firestore 共 {len(existing_docs)} 筆，本地 {len(shops)} 筆，孤兒 {len(orphans)} 筆")

    if not orphans:
        print(f"{CYAN}  => 無孤兒文件，無需清理{RESET}")
        return

    print(f"{YELLOW}  以下文件將被刪除：{RESET}")
    for doc in orphans:
        name = doc.to_dict().get("name", "")
        safe_name = name.encode("cp950", errors="replace").decode("cp950")
        print(f"    - {doc.id} ({safe_name})")

    confirm = input(f"\n{YELLOW}確認刪除以上 {len(orphans)} 筆孤兒文件？[y/N]: {RESET}").strip().lower()
    if confirm != "y":
        print(f"{CYAN}  => 已取消，未刪除任何文件{RESET}")
        return

    print(f"{GREEN}STEP 3: 刪除孤兒文件{RESET}")
    BATCH_SIZE = 500
    deleted = 0
    for i in range(0, len(orphans), BATCH_SIZE):
        batch = db.batch()
        for doc in orphans[i:i + BATCH_SIZE]:
            batch.delete(col.document(doc.id))
        batch.commit()
        deleted += len(orphans[i:i + BATCH_SIZE])

    print(f"{GREEN}STEP 3: 清理完成，共刪除 {deleted} 筆孤兒文件{RESET}")


def main() -> None:
    """主流程：解析參數並執行對應模式。"""
    parser = argparse.ArgumentParser(description="Firestore 資料匯入/同步腳本")
    parser.add_argument(
        "--mode",
        choices=["import", "sync", "clean"],
        required=True,
        help="import: 首次匯入  sync: 定期更新同步  clean: 刪除孤兒文件",
    )
    args = parser.parse_args()

    print(f"{GREEN}STEP 1: 載入本地資料與初始化 Firestore{RESET}")
    shops = _load_local()
    print(f"  載入 {len(shops)} 筆店家資料")

    db = firestore.Client(
        project=os.getenv("GOOGLE_CLOUD_PROJECT_ID"),
        database=os.getenv("FIRESTORE_DATABASE", "(default)"),
    )

    if args.mode == "import":
        run_import(db, shops)
    elif args.mode == "sync":
        run_sync(db, shops)
    else:
        run_clean(db, shops)

    print(f"\n{MAGAENTA}{'=' * 40}")
    print(f"完成！模式：{args.mode}  資料來源：{DATA_PATH}")
    print(f"{'=' * 40}{RESET}")


if __name__ == "__main__":
    main()
