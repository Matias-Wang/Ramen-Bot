"""
輕量結構化事件日誌（A4 可觀測性）。

保留既有彩色 STEP print（人類閱讀）不變，另以單行 JSON 輸出關鍵指標事件，
讓 Cloud Logging 可自動解析為結構化欄位，據以建立指標與告警
（例如 FALLBACK 率突增、端到端延遲 p95）。
"""

import json
from typing import Any


def emit_metric(event: str, **fields: Any) -> None:
    """
    輸出一行 JSON 結構化事件（best-effort，永不因日誌失敗影響主流程）。

    Parameters
    ----------
    event : str
        事件名稱（例如 "request"、"location_request"）。
    **fields : Any
        事件附帶欄位（例如 intent、total_s）。
    """
    try:
        print(json.dumps({"event": event, **fields}, ensure_ascii=False))
    except Exception:
        pass
