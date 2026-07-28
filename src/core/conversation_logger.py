"""
Conversation Logger：雲端對話特徵埋點。

在 AgentRouter 確定意圖與參數後，將該筆對話特徵非同步寫入 Firestore
`conversation_logs` collection，供地端 Claude Code 分析分類錯誤、盤點資料盲區。

僅在 `DATA_BACKEND=firestore`（雲端）時生效；本地終端測試模式為 no-op
（無真實使用者，不需要埋點）。寫入採 fire-and-forget 背景執行緒，
不阻塞 dispatch，且任何失敗都不影響主流程回覆。
"""

import os
import threading
from datetime import datetime, timezone, timedelta

USE_FIRESTORE = os.getenv("DATA_BACKEND", "local") == "firestore"

RED = "\033[91m"
GREEN = "\033[92m"
RESET = "\033[0m"

FIRESTORE_COLLECTION = "conversation_logs"
_TAIPEI_TZ = timezone(timedelta(hours=8))

# 對話日誌含 user_id 情境的原始輸入，屬個資；保留 90 天後由 Firestore TTL
# 自動清除（TTL policy 設在 expire_at 這個 Timestamp 欄位上）。
_RETENTION_DAYS = 90

# intent（AgentRouter 內部標籤）對應到 skill 名稱（地端分析用的可讀名稱）
_INTENT_TO_SKILL = {
    "SEARCH_BY_CRITERIA": "Search_skill",
    "GET_SPECIFIC_INFO": "info_skill",
    "KNOWLEDGE_QUERY": "knowledge_skill",
    "REPORT_ERROR": "feedback_skill",
}


def log_conversation(user_input: str, intent: str, intent_data: dict) -> None:
    """
    非同步記錄一筆對話特徵至 Firestore `conversation_logs`。

    Parameters
    ----------
    user_input : str
        使用者原始輸入文字。
    intent : str
        AgentRouter 解析出的意圖標籤（例如 "SEARCH_BY_CRITERIA"）。
    intent_data : dict
        Gemini 解析出的完整意圖字典，用於擷取參數（args）。

    Returns
    -------
    None
        本地模式直接返回；雲端模式則交由背景執行緒寫入，立即返回。
    """
    if not USE_FIRESTORE:
        return

    # 參數僅保留實際萃取到的欄位，排除意圖與 UI 標籤本身
    args = {
        k: v for k, v in intent_data.items() if k not in ("intent", "ui_tag")
    }
    record = {
        "timestamp": datetime.now(_TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "user_input": user_input,
        "predicted_skill": _INTENT_TO_SKILL.get(intent, intent),
        "args": args,
        # Firestore TTL 依此 Timestamp 欄位在到期後自動刪除本筆日誌（見 _RETENTION_DAYS）
        "expire_at": datetime.now(timezone.utc) + timedelta(days=_RETENTION_DAYS),
    }
    thread = threading.Thread(
        target=_write_to_firestore, args=(record,), daemon=True
    )
    thread.start()


def _write_to_firestore(record: dict) -> None:
    """
    背景執行緒實際寫入 Firestore（fire-and-forget，失敗僅記錄不拋出）。

    Parameters
    ----------
    record : dict
        已組裝完成的對話特徵紀錄。
    """
    print(f"{GREEN}STEP: 寫入對話日誌至 Firestore conversation_logs{RESET}")
    try:
        from services.firestore_client import get_db

        db = get_db()
        db.collection(FIRESTORE_COLLECTION).add(record)
    except Exception as e:
        print(f"{RED}STEP ERROR:{e}{RESET}")
