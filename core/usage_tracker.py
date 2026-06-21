import json
import os
from datetime import date

# <使用者自訂變數>
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
RESET = "\033[0m"

LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "log", "usage.json")
USE_FIRESTORE = os.getenv("DATA_BACKEND", "local") == "firestore"


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


def check_and_increment(key: str) -> bool:
    """
    檢查指定 API 是否仍在每日配額內，若是則計數 +1 並寫回。

    Parameters
    ----------
    key : str
        追蹤鍵值，可為 "google_maps_api"、"llm_gemini"、"line_api"。

    Returns
    -------
    bool
        True 表示可繼續執行；False 表示已達當日上限。
    """
    if os.getenv("E2E_TEST_MODE") == "1":
        print(f"{YELLOW}STEP: E2E_TEST_MODE 啟用，{key} 配額檢查略過（不計入每日配額）{RESET}")
        return True

    print(f"{GREEN}STEP: 檢查 {key} 使用配額{RESET}")
    try:
        data = _load()
        data = _reset_if_new_day(data)

        entry = data.get(key)
        if entry is None:
            print(f"{RED}STEP ERROR: 未知的追蹤鍵值 '{key}'{RESET}")
            return False

        if entry["count"] >= entry["limit"]:
            print(f"{RED}STEP ERROR: {key} 已達每日上限 ({entry['limit']} 次){RESET}")
            return False

        entry["count"] += 1
        _save(data)
        return True
    except Exception as e:
        # 追蹤本身失敗時不阻擋正常流程
        print(f"{RED}STEP ERROR: 使用量追蹤失敗: {e}{RESET}")
        return True


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

    try:
        data = _load()
        data = _reset_if_new_day(data)
        data["llm_gemini"]["token_consumed"] += tokens
        _save(data)
    except Exception as e:
        print(f"{RED}STEP ERROR: Token 記錄失敗: {e}{RESET}")
