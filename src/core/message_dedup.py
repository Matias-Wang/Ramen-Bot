import os
from collections import OrderedDict
from datetime import datetime, timezone

# <使用者自訂變數>
RED = "\033[91m"
GREEN = "\033[92m"
RESET = "\033[0m"

USE_FIRESTORE = os.getenv("DATA_BACKEND", "local") == "firestore"
FIRESTORE_COLLECTION = "processed_message_ids"

# 本地模式去重快取。以 OrderedDict 當有序集合並設上限，避免長時間運行時
# 無限增長（生產模式走 Firestore + TTL，不使用此結構）。
_MAX_SEEN_IDS = 10000
_seen_ids: "OrderedDict[str, None]" = OrderedDict()


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
        _seen_ids[message_id] = None
        if len(_seen_ids) > _MAX_SEEN_IDS:
            _seen_ids.popitem(last=False)  # 淘汰最舊的一筆
        return False
