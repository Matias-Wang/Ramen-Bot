import os

from google.cloud import firestore

# <使用者自訂變數>
RED = "\033[91m"
GREEN = "\033[92m"
RESET = "\033[0m"

_db: "firestore.Client | None" = None


def get_db() -> "firestore.Client":
    """
    取得全域共用的 Firestore Client（Singleton）。

    Returns
    -------
    firestore.Client
        全域共用的 Firestore 連線實例。
    """
    global _db
    if _db is None:
        print(f"{GREEN}STEP: 初始化 Firestore Client（首次建立連線）{RESET}")
        _db = firestore.Client(
            project=os.getenv("GOOGLE_CLOUD_PROJECT_ID"),
            database=os.getenv("FIRESTORE_DATABASE", "(default)"),
        )
    return _db
