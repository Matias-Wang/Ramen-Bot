import json
import os
import threading
import time
from datetime import date

from core import timing

# <使用者自訂變數>
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
RESET = "\033[0m"

LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "log", "usage.json"
)
USE_FIRESTORE = os.getenv("DATA_BACKEND", "local") == "firestore"

# 本地模式（單機多執行緒）序列化 read-modify-write，避免並行漏加計數。
# Firestore 模式改由 transaction 保證原子性，不使用此鎖。
_LOCAL_LOCK = threading.Lock()


def _default_data() -> dict:
    return {
        "date": str(date.today()),
        "google_maps_api": {"count": 0, "limit": 100},
        "llm_gemini": {"count": 0, "token_consumed": 0, "limit": 100},
        "line_api": {"count": 0, "limit": 100},
    }


def _get_firestore_doc():
    """Firestore config/daily_usage document 參考（使用全域 singleton）。"""
    from services.firestore_client import get_db
    db = get_db()
    return db.collection("config").document("daily_usage")


def _load() -> dict:
    if USE_FIRESTORE:
        try:
            doc_ref = _get_firestore_doc()
            snap = doc_ref.get()
            return snap.to_dict() if snap.exists else _default_data()
        except Exception as e:
            print(f"{RED}STEP ERROR: Firestore 讀取失敗: {e}{RESET}")
            return _default_data()
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return _default_data()


def _save(data: dict) -> None:
    if USE_FIRESTORE:
        try:
            doc_ref = _get_firestore_doc()
            doc_ref.set(data)
        except Exception as e:
            print(f"{RED}STEP ERROR: Firestore 寫入失敗: {e}{RESET}")
        return
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _reset_if_new_day(data: dict) -> dict:
    """若日期不是今天，將所有計數歸零並更新日期。"""
    today = str(date.today())
    if data.get("date") != today:
        print(f"{GREEN}STEP: 偵測到新的一天，重置使用量計數器{RESET}")
        fresh = _default_data()
        fresh["date"] = today
        return fresh
    return data


def _local_check_and_increment(key: str, count: int = 1) -> "bool | None":
    """本地模式：在 _LOCAL_LOCK 內完成 read-modify-write，避免並行漏加。

    Returns
    -------
    bool or None
        True 可繼續、False 已達上限、None 為未知鍵值。
    """
    with _LOCAL_LOCK:
        data = _reset_if_new_day(_load())
        entry = data.get(key)
        if entry is None:
            return None
        if entry["count"] + count > entry["limit"]:
            return False
        entry["count"] += count
        _save(data)
        return True


def _firestore_check_and_increment(key: str, count: int = 1) -> "bool | None":
    """Firestore 模式：以 transaction 原子完成讀-判斷-加，避免多副本並行漏加。

    Returns
    -------
    bool or None
        True 可繼續、False 已達上限、None 為未知鍵值。
    """
    from google.cloud import firestore

    from services.firestore_client import get_db

    db = get_db()
    doc_ref = db.collection("config").document("daily_usage")

    @firestore.transactional
    def _txn(transaction: "firestore.Transaction") -> "bool | None":
        snap = doc_ref.get(transaction=transaction)
        data = _reset_if_new_day(snap.to_dict() if snap.exists else _default_data())
        entry = data.get(key)
        if entry is None:
            return None
        if entry["count"] + count > entry["limit"]:
            # 跨日會由 _reset_if_new_day 歸零而不進本分支，故此處必為同日已達上限、
            # data 未變動，無需寫回（避免尖峰時的冗餘寫入）。
            return False
        entry["count"] += count
        transaction.set(doc_ref, data)
        return True

    return _txn(db.transaction())


def check_and_increment(key: str, count: int = 1) -> bool:
    """
    檢查指定 API 是否仍在每日配額內，若是則計數加上 count 並寫回。

    本地模式以 threading.Lock、Firestore 模式以 transaction 保證整段
    「讀-判斷-加」原子性，避免多執行緒/多副本並行下漏加計數而突破每日上限。

    Parameters
    ----------
    key : str
        追蹤鍵值，可為 "google_maps_api"、"llm_gemini"、"line_api"。
    count : int
        本次要計入的數量，預設 1。LINE 的用量以**訊息則數**計算，
        而單次 `push_message` 可帶多則訊息（例如引導文 + Flex 共 2 則），
        此時須傳入實際則數，否則計數會低於 LINE 實際計算的用量。

    Returns
    -------
    bool
        True 表示可繼續執行；False 表示加上 count 後會超出當日上限。
    """
    if os.getenv("E2E_TEST_MODE") == "1":
        print(f"{YELLOW}STEP: E2E_TEST_MODE 啟用，{key} 配額檢查略過（不計入每日配額）{RESET}")
        return True

    print(f"{GREEN}STEP: 檢查 {key} 使用配額{RESET}")
    _t0 = time.time()
    try:
        if USE_FIRESTORE:
            result = _firestore_check_and_increment(key, count)
        else:
            result = _local_check_and_increment(key, count)

        if result is None:
            print(f"{RED}STEP ERROR: 未知的追蹤鍵值 '{key}'{RESET}")
            return False
        if result is False:
            print(f"{RED}STEP ERROR: {key} 已達每日上限{RESET}")
            return False
        return True
    except Exception as e:
        # 追蹤本身失敗時不阻擋正常流程
        print(f"{RED}STEP ERROR: 使用量追蹤失敗: {e}{RESET}")
        return True
    finally:
        # Firestore 模式下 transaction 一次網路往返，納入 KPI 計時，
        # 避免「LLM 耗時」與「STEP 總耗時」間出現不明缺口。
        timing.record(f"quota:{key}", time.time() - _t0)


def record_tokens(tokens: int) -> None:
    """
    累加 LLM 呼叫消耗的 token 數量至 llm_gemini.token_consumed。

    Parameters
    ----------
    tokens : int
        本次呼叫消耗的 token 總量。
    """
    if os.getenv("E2E_TEST_MODE") == "1":
        return

    _t0 = time.time()
    try:
        if USE_FIRESTORE:
            from google.cloud import firestore

            from services.firestore_client import get_db

            db = get_db()
            doc_ref = db.collection("config").document("daily_usage")

            @firestore.transactional
            def _txn(transaction: "firestore.Transaction") -> None:
                snap = doc_ref.get(transaction=transaction)
                data = _reset_if_new_day(
                    snap.to_dict() if snap.exists else _default_data()
                )
                data["llm_gemini"]["token_consumed"] += tokens
                transaction.set(doc_ref, data)

            _txn(db.transaction())
        else:
            with _LOCAL_LOCK:
                data = _reset_if_new_day(_load())
                data["llm_gemini"]["token_consumed"] += tokens
                _save(data)
    except Exception as e:
        print(f"{RED}STEP ERROR: Token 記錄失敗: {e}{RESET}")
    finally:
        timing.record("quota:record_tokens", time.time() - _t0)
