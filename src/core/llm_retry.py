"""
Gemini 呼叫的暫時性錯誤重試工具。

Gemini 偶發 503（"high demand" 過載）、429（限流）、逾時等暫時性錯誤，
單次閃斷不應直接讓整個請求失敗。本模組提供統一的指數退避重試，供意圖解析、
推薦文、店家摘要、知識問答與 embedding 等 Gemini 呼叫共用。
"""

import time
from typing import Any, Callable

# <使用者自訂變數>
RED = "\033[91m"
RESET = "\033[0m"

# Gemini 暫時性錯誤（過載/限流/伺服器內部錯誤/逾時）特徵；命中則重試。
_TRANSIENT_ERROR_MARKERS = (
    "503", "unavailable", "overloaded", "500", "internal",
    "429", "resource_exhausted", "deadline", "timeout",
)


def is_transient_error(exc: Exception) -> bool:
    """
    判斷例外是否為可重試的暫時性錯誤（Gemini 過載/限流/逾時）。

    Parameters
    ----------
    exc : Exception
        Gemini 呼叫拋出的例外。

    Returns
    -------
    bool
        訊息含暫時性錯誤特徵（503/UNAVAILABLE/429/timeout 等）回傳 True。
    """
    msg = str(exc).lower()
    return any(marker in msg for marker in _TRANSIENT_ERROR_MARKERS)


def generate_with_retry(
    call: Callable[[], Any],
    attempts: int = 3,
    base_delay: float = 0.6,
    label: str = "LLM",
) -> Any:
    """
    執行 Gemini 呼叫，遇暫時性錯誤時以指數退避重試。

    非暫時性錯誤或重試用盡時向上拋出最後一次的例外，交由呼叫端降級處理。

    Parameters
    ----------
    call : Callable[[], Any]
        無參數的 Gemini 呼叫（以 lambda 包裝）。
    attempts : int
        最多嘗試次數（含首次），預設 3。
    base_delay : float
        指數退避基底秒數，預設 0.6（重試間隔 0.6s、1.2s...）。
    label : str
        呼叫用途標籤（例如 "intent"、"推薦文"），僅供重試日誌辨識。

    Returns
    -------
    Any
        Gemini 回應物件。

    Raises
    ------
    Exception
        重試用盡或非暫時性錯誤時，向上拋出最後一次的例外。
    """
    for i in range(attempts):
        try:
            return call()
        except Exception as e:
            if i < attempts - 1 and is_transient_error(e):
                print(f"{RED}WARN: {label} Gemini 暫時性錯誤，重試 "
                      f"{i + 1}/{attempts - 1}：{e}{RESET}")
                time.sleep(base_delay * (2 ** i))
                continue
            raise
