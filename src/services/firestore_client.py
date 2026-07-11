import os
import threading
import time

from google.cloud import firestore

# <使用者自訂變數>
RED = "\033[91m"
GREEN = "\033[92m"
RESET = "\033[0m"

_db: "firestore.Client | None" = None
_heartbeat_thread: "threading.Thread | None" = None


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


def _run_heartbeat(interval_sec: int) -> None:
    """背景心跳：每隔 interval_sec 秒讀一次 config/daily_usage 的 date 欄位，
    讓 gRPC 連線保持活躍，避免網路中間件在閒置後靜默關閉連線（約 2-3 分鐘超時）。"""
    while True:
        time.sleep(interval_sec)
        try:
            db = get_db()
            db.collection("config").document("daily_usage").get(
                field_paths=["date"]
            )
        except Exception:
            pass


def start_heartbeat(interval_sec: int = 90) -> None:
    """
    啟動 Firestore gRPC 心跳背景執行緒（daemon）。

    每 interval_sec 秒發送一次輕量讀取請求，防止 gRPC 連線因閒置而失效，
    解決 Cloud Run 上回覆延遲 2-3 分鐘的根本原因。
    應在 app 啟動時（DATA_BACKEND=firestore）呼叫一次。

    Parameters
    ----------
    interval_sec : int
        心跳間隔秒數，預設 90 秒（低於多數網路中間件的 2 分鐘閒置超時）。
    """
    global _heartbeat_thread
    if _heartbeat_thread is not None and _heartbeat_thread.is_alive():
        return
    _heartbeat_thread = threading.Thread(
        target=_run_heartbeat,
        args=(interval_sec,),
        daemon=True,
        name="firestore-heartbeat",
    )
    _heartbeat_thread.start()
    print(f"{GREEN}[STARTUP] Firestore gRPC 心跳執行緒啟動（每 {interval_sec}s）{RESET}")
