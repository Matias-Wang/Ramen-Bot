"""
Feedback Skill：回收使用者在 LINE 對話中的店家資料錯誤回報，
並提供查詢待處理回報清單的工具。

儲存路徑（雙路徑）：
  本地：log/feedback_reports.json
  生產：Firestore feedback_reports collection
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

USE_FIRESTORE = os.getenv("DATA_BACKEND", "local") == "firestore"

RED = "\033[91m"
GREEN = "\033[92m"
RESET = "\033[0m"

_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "log"
)
FEEDBACK_LOG_PATH = os.path.join(_LOG_DIR, "feedback_reports.json")
FIRESTORE_COLLECTION = "feedback_reports"


def collect_report(
    shop_name: Optional[str],
    error_description: str,
    user_id: str,
) -> bool:
    """
    將使用者的店家資料錯誤回報寫入待處理清單。

    Parameters
    ----------
    shop_name : str or None
        使用者指稱的店家名稱；若未指明則為 None。
    error_description : str
        使用者描述的錯誤內容。
    user_id : str
        LINE 使用者 ID。

    Returns
    -------
    bool
        寫入成功回傳 True，失敗回傳 False。
    """
    print(f"{GREEN}STEP 1: 建立錯誤回報並寫入儲存{RESET}")
    report = {
        "id": str(uuid.uuid4()),
        "shop_name": shop_name,
        "error_description": error_description,
        "user_id": user_id,
        "reported_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }
    try:
        if USE_FIRESTORE:
            from services.firestore_client import get_db
            db = get_db()
            db.collection(FIRESTORE_COLLECTION).document(report["id"]).set(report)
        else:
            _append_to_local(report)
        print(
            f"{GREEN}STEP 2: 回報已寫入 (id={report['id'][:8]}..., "
            f"shop={shop_name or '未指定'}){RESET}"
        )
        return True
    except Exception as e:
        print(f"{RED}STEP 1 ERROR:{e}{RESET}")
        return False


def _append_to_local(report: dict) -> None:
    """
    讀取既有 JSON → 附加新紀錄 → 回寫。

    Parameters
    ----------
    report : dict
        已建立的回報資料字典。
    """
    os.makedirs(_LOG_DIR, exist_ok=True)
    if os.path.exists(FEEDBACK_LOG_PATH):
        with open(FEEDBACK_LOG_PATH, "r", encoding="utf-8") as f:
            reports: list[dict] = json.load(f)
    else:
        reports = []
    reports.append(report)
    with open(FEEDBACK_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(reports, f, ensure_ascii=False, indent=2)


def check_pending_reports() -> list[dict]:
    """
    讀取所有狀態為 "pending" 的錯誤回報，並印出至 console 供人工確認。

    Returns
    -------
    list[dict]
        待處理回報清單；無待處理項目時回傳空清單。
    """
    print(f"{GREEN}STEP 1: 讀取待處理錯誤回報{RESET}")
    try:
        if USE_FIRESTORE:
            from google.cloud.firestore_v1.base_query import FieldFilter

            from services.firestore_client import get_db
            db = get_db()
            docs = (
                db.collection(FIRESTORE_COLLECTION)
                .where(filter=FieldFilter("status", "==", "pending"))
                .stream()
            )
            pending = [doc.to_dict() for doc in docs]
        else:
            if not os.path.exists(FEEDBACK_LOG_PATH):
                pending = []
            else:
                with open(FEEDBACK_LOG_PATH, "r", encoding="utf-8") as f:
                    all_reports: list[dict] = json.load(f)
                pending = [r for r in all_reports if r.get("status") == "pending"]

        if pending:
            print(f"{RED}[FEEDBACK] 有 {len(pending)} 筆待處理回報：{RESET}")
            for r in pending:
                shop = r.get("shop_name") or "（未指定店家）"
                desc = r.get("error_description", "")
                reported_at = r.get("reported_at", "")
                print(f"{RED}  - [{reported_at}] {shop}：{desc}{RESET}")
        else:
            print(f"{GREEN}[FEEDBACK] 目前無待處理回報{RESET}")
        return pending
    except Exception as e:
        print(f"{RED}STEP 1 ERROR:{e}{RESET}")
        return []
