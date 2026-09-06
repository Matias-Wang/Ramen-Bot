"""
輕量計時工具：記錄單次請求的端到端耗時與各 LLM 呼叫耗時，作為效能 KPI。

以 contextvars 保存「當前請求」的計時收集器，讓分散在 router／skills 的 LLM
呼叫點無需改動函式簽名即可累積計時。推薦文並行執行緒（ThreadPoolExecutor）
不會繼承 contextvars，故由 generate_recommendations 先取得收集器再顯式傳入
worker（list.append 於 CPython 為原子操作，跨執行緒累積安全）。

未開啟收集（例如離線批次腳本）時，record 仍會列印但不累積，呼叫端零負擔。
"""

import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, List, Optional, Tuple

# <使用者自訂變數>
CYAN = "\033[96m"
RESET = "\033[0m"

# 每筆為 (LLM 呼叫名稱, 耗時秒數)
_Records = List[Tuple[str, float]]
_records: ContextVar[Optional[_Records]] = ContextVar("_timing_records", default=None)


# webhook 時戳與本機時鐘的最大合理落差（秒）。超出即視為時鐘偏移或 LINE
# 重送舊訊息，退回本機時間，避免 KPI 出現負值或離譜數值。
MAX_WEBHOOK_LAG_S = 300


def webhook_received_at(
    event_timestamp_ms: int, now: "Optional[float]" = None
) -> float:
    """
    換算 KPI 計時起點：LINE 伺服器收到該則訊息的時刻。

    須改用 LINE 的 `event.timestamp` 而非 webhook handler 內的 `time.time()`：
    後者在 `handle_message` 第一行取值，而該函式只有在容器啟動、模組載入完畢
    之後才會執行。`min-instances=0` 時冷啟動實測達 6~12 秒，這段完全不會被計入
    KPI（2026-09-03 實測：端到端 13.87s 只報 7.34s，低報 47%）。

    Parameters
    ----------
    event_timestamp_ms : int
        LINE webhook 事件的 `timestamp`，毫秒級 epoch。
    now : Optional[float]
        當前時間（epoch 秒）；None 時取 `time.time()`。供測試注入。

    Returns
    -------
    float
        KPI 計時起點（epoch 秒）。落差不在 0 ~ MAX_WEBHOOK_LAG_S 之間時退回 `now`。
    """
    current = time.time() if now is None else now
    received_at = event_timestamp_ms / 1000
    lag = current - received_at
    if not 0 <= lag <= MAX_WEBHOOK_LAG_S:
        print(
            f"{CYAN}STEP: webhook 時戳異常（與本機差 {lag:.1f}s），"
            f"KPI 改以本機時間起算{RESET}"
        )
        return current
    return received_at


def begin_collection() -> _Records:
    """
    開啟一次新的計時收集，並設為當前 context 的收集器。

    Returns
    -------
    _Records
        本次請求的計時收集器（空 list）。
    """
    records: _Records = []
    _records.set(records)
    return records


def current_records() -> Optional[_Records]:
    """
    取得當前 context 的計時收集器。

    Returns
    -------
    Optional[_Records]
        收集器 list；未開啟收集時為 None。
    """
    return _records.get()


def record(name: str, seconds: float, records: Optional[_Records] = None) -> None:
    """
    記錄一筆 LLM 計時並即時列印。

    Parameters
    ----------
    name : str
        呼叫名稱（例如 "intent"、"rec[0]"、"info_summary"、"knowledge"，
        或用量追蹤的 Firestore I/O "quota:llm_gemini" 等）。
    seconds : float
        該次呼叫耗時（秒）。
    records : Optional[_Records]
        指定寫入的收集器；為 None 時寫入當前 context 收集器（跨執行緒場景
        須顯式傳入父執行緒的收集器）。
    """
    target = records if records is not None else _records.get()
    if target is not None:
        target.append((name, round(seconds, 2)))
    print(f"{CYAN}[TIMED] {name}: {seconds:.2f}s{RESET}")


@contextmanager
def time_llm(name: str) -> Iterator[None]:
    """
    context manager：計時包住一次 LLM 呼叫，結束時記錄至當前收集器。

    Parameters
    ----------
    name : str
        LLM 呼叫名稱。
    """
    t0 = time.time()
    try:
        yield
    finally:
        record(name, time.time() - t0)


def snapshot(
    records: Optional[_Records], total_seconds: Optional[float] = None
) -> dict:
    """
    將計時收集器整理成報告用結構。

    Parameters
    ----------
    records : Optional[_Records]
        計時收集器。
    total_seconds : Optional[float]
        端到端總耗時（秒）；None 表示未量測。

    Returns
    -------
    dict
        {"total_s", "llm": [{"name", "s"}...], "llm_total_s", "llm_count"}。
    """
    recs = records or []
    return {
        "total_s": round(total_seconds, 2) if total_seconds is not None else None,
        "llm": [{"name": n, "s": s} for n, s in recs],
        "llm_total_s": round(sum(s for _, s in recs), 2),
        "llm_count": len(recs),
    }


def format_kpi(snap: dict) -> str:
    """
    把 snapshot 組成單行 KPI 字串，供 log 與報告使用。

    Parameters
    ----------
    snap : dict
        snapshot() 的回傳結構。

    Returns
    -------
    str
        例如 "端到端 3.2s | LLM 4 次 共 4.10s [intent=0.80s, rec[0]=1.10s, ...]"。
    """
    parts = [f"{d['name']}={d['s']}s" for d in snap["llm"]]
    total = f"{snap['total_s']}s" if snap["total_s"] is not None else "N/A"
    return (
        f"端到端 {total} | LLM {snap['llm_count']} 次 共 {snap['llm_total_s']}s"
        f" [{', '.join(parts)}]"
    )
