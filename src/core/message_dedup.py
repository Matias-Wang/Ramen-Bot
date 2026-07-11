import os
from datetime import datetime, timezone

# <使用者自訂變數>
RED = "\033[91m"
GREEN = "\033[92m"
RESET = "\033[0m"

USE_FIRESTORE = os.getenv("DATA_BACKEND", "local") == "firestore"
FIRESTORE_COLLECTION = "processed_message_ids"

_seen_ids: set[str] = set()


def is_duplicate_message(message_id: str) -> bool:
    """
    檢查 LINE message id 是否已處理過，若是首次出現則同時標記為已處理。

    Parameters
    ----------
    message_id : str
        LINE `event.message.id`。

    Returns
    -------
    bool
        True 表示重複訊息（應跳過後續處理），False 表示首次出現。
    """
    print(f"{GREEN}STEP: 檢查 message_id={message_id} 是否重複{RESET}")
    if USE_FIRESTORE:
        try:
            from services.firestore_client import get_db
            doc_ref = get_db().collection(FIRESTORE_COLLECTION).document(message_id)
            if doc_ref.get().exists:
                print(f"{GREEN}STEP: message_id={message_id} 為重複訊息，已跳過{RESET}")
                return True
            doc_ref.set({"received_at": datetime.now(timezone.utc)})
            return False
        except Exception as e:
            print(f"{RED}STEP ERROR: 訊息去重檢查失敗:{e}{RESET}")
            return False
    else:
        if message_id in _seen_ids:
            print(f"{GREEN}STEP: message_id={message_id} 為重複訊息，已跳過{RESET}")
            return True
        _seen_ids.add(message_id)
        return False
