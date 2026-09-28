"""
雲端數據同步腳本（地端執行）

將 Cloud Run 埋點累積的雲端數據同步回地端，供 Claude Code 分析、調 Prompt、
盤點資料盲區。每次執行皆為「完整覆寫」（非增量），確保地端檔案與雲端一致。

同步內容：
  Firestore `feedback_reports` → data_logs/tracking_feedbacks.json（JSON Array）
      映射為 {timestamp, user_id, feedback_text}
  Firestore `conversation_logs` → data_logs/tracking_conversations.jsonl（每行一筆 JSON）

使用方式：
  python scripts/fetch_cloud_data.py

前置需求：`.env` 已設定 GOOGLE_CLOUD_PROJECT_ID / FIRESTORE_DATABASE，
且執行環境有讀取該 Firestore 的權限（gcloud ADC）。
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)

from dotenv import load_dotenv
from google.cloud import firestore

from services.firestore_client import get_db

load_dotenv()

# <使用者自訂變數>
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
MAGAENTA = "\033[95m"
RESET = "\033[0m"

_OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data_logs"
)
FEEDBACKS_PATH = os.path.join(_OUTPUT_DIR, "tracking_feedbacks.json")
CONVERSATIONS_PATH = os.path.join(_OUTPUT_DIR, "tracking_conversations.jsonl")

FEEDBACK_COLLECTION = "feedback_reports"
CONVERSATION_COLLECTION = "conversation_logs"

_TAIPEI_TZ = timezone(timedelta(hours=8))


def _iso_to_taipei(raw: str) -> str:
    """
    將 ISO 8601 時間字串（feedback_reports 的 reported_at，UTC）轉為
    台北時間字串 "YYYY-MM-DD HH:MM:SS"；無法解析時原樣回傳。

    Parameters
    ----------
    raw : str
        ISO 8601 時間字串。

    Returns
    -------
    str
        台北時間字串，或無法解析時的原始輸入。
    """
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return raw


def fetch_feedbacks(db: firestore.Client) -> int:
    """
    讀取 Firestore feedback_reports，映射為精簡 schema 後覆寫本地 JSON Array。

    Parameters
    ----------
    db : firestore.Client
        Firestore 連線實例。

    Returns
    -------
    int
        寫入的回報筆數。
    """
    print(f"{GREEN}STEP 2: 同步 feedback_reports → tracking_feedbacks.json{RESET}")
    docs = db.collection(FEEDBACK_COLLECTION).stream()
    feedbacks = [
        {
            "timestamp": _iso_to_taipei(d.get("reported_at", "")),
            "user_id": d.get("user_id", ""),
            "feedback_text": d.get("error_description", ""),
        }
        for d in (doc.to_dict() for doc in docs)
    ]
    feedbacks.sort(key=lambda r: r["timestamp"])
    with open(FEEDBACKS_PATH, "w", encoding="utf-8") as f:
        json.dump(feedbacks, f, ensure_ascii=False, indent=2)
    print(f"{CYAN}  → 已寫入 {len(feedbacks)} 筆回報{RESET}")
    return len(feedbacks)


def fetch_conversations(db: firestore.Client) -> int:
    """
    讀取 Firestore conversation_logs，以每行一筆 JSON 覆寫本地 JSONL。

    Parameters
    ----------
    db : firestore.Client
        Firestore 連線實例。

    Returns
    -------
    int
        寫入的對話紀錄筆數。
    """
    print(
        f"{GREEN}STEP 3: 同步 conversation_logs → "
        f"tracking_conversations.jsonl{RESET}"
    )
    docs = db.collection(CONVERSATION_COLLECTION).stream()
    logs = [
        {
            "timestamp": d.get("timestamp", ""),
            "user_input": d.get("user_input", ""),
            "predicted_skill": d.get("predicted_skill", ""),
            "args": d.get("args", {}),
        }
        for d in (doc.to_dict() for doc in docs)
    ]
    logs.sort(key=lambda r: r["timestamp"])
    with open(CONVERSATIONS_PATH, "w", encoding="utf-8") as f:
        for log in logs:
            f.write(json.dumps(log, ensure_ascii=False) + "\n")
    print(f"{CYAN}  → 已寫入 {len(logs)} 筆對話紀錄{RESET}")
    return len(logs)


def main() -> None:
    """雲端數據同步主流程：連線 Firestore → 覆寫兩份地端檔案。"""
    print(f"{MAGAENTA}=== 雲端數據同步開始 ==={RESET}")
    try:
        os.makedirs(_OUTPUT_DIR, exist_ok=True)
        print(f"{GREEN}STEP 1: 連線 Firestore{RESET}")
        db = get_db()

        n_feedbacks = fetch_feedbacks(db)
        n_logs = fetch_conversations(db)

        print(
            f"{MAGAENTA}=== 同步完成：{n_feedbacks} 筆回報、"
            f"{n_logs} 筆對話紀錄 ==={RESET}"
        )
    except Exception as e:
        print(f"{RED}STEP ERROR:{e}{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
