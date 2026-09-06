"""
測試 timing.webhook_received_at 的 KPI 計時起點換算。
涵蓋：正常落差、冷啟動情境、邊界值、時鐘倒退、時戳過舊、未指定 now 時取本機時間。

此函式存在的理由：`min-instances=0` 之後，webhook handler 內的 `time.time()`
會漏掉冷啟動（容器啟動 + 模組載入）的時間，使端到端 KPI 系統性低報
（2026-09-03 實測 13.87s 只報 7.34s）。
"""

import time

import pytest

from core import timing


# ─── webhook_received_at ───────────────────────────────────────────────────────

class TestWebhookReceivedAt:
    def test_normal_lag_uses_line_timestamp(self):
        """落差在合理範圍內時，以 LINE 收訊時戳為起點而非本機時間。"""
        result = timing.webhook_received_at(1_700_000_000_000, now=1_700_000_000.5)
        assert result == pytest.approx(1_700_000_000.0)

    def test_cold_start_lag_is_counted(self):
        """冷啟動情境：容器啟動的 6.4 秒必須算進 KPI —— 本函式的存在理由。"""
        event_ms = 1_700_000_000_000
        now = 1_700_000_006.4  # handle_message 直到 6.4 秒後才被執行
        start = timing.webhook_received_at(event_ms, now=now)
        assert now - start == pytest.approx(6.4)

    def test_zero_lag(self):
        """完全同時亦視為合理，不觸發退回。"""
        result = timing.webhook_received_at(1_700_000_000_000, now=1_700_000_000.0)
        assert result == pytest.approx(1_700_000_000.0)

    def test_boundary_max_lag_is_inclusive(self):
        """落差正好等於上限時仍採用 LINE 時戳。"""
        now = 1_700_000_000.0 + timing.MAX_WEBHOOK_LAG_S
        result = timing.webhook_received_at(1_700_000_000_000, now=now)
        assert result == pytest.approx(1_700_000_000.0)

    def test_beyond_max_lag_falls_back_to_now(self):
        """時戳過舊（例如 LINE 重送舊訊息）時退回本機時間，避免 KPI 離譜。"""
        now = 1_700_000_000.0 + timing.MAX_WEBHOOK_LAG_S + 1
        result = timing.webhook_received_at(1_700_000_000_000, now=now)
        assert result == pytest.approx(now)

    def test_future_timestamp_falls_back_to_now(self):
        """時鐘偏移導致 LINE 時戳在未來時退回本機時間，避免 KPI 為負值。"""
        now = 1_700_000_000.0
        result = timing.webhook_received_at(1_700_000_010_000, now=now)
        assert result == pytest.approx(now)

    def test_now_defaults_to_wall_clock(self):
        """未指定 now 時以 time.time() 為準。"""
        event_ms = int((time.time() - 2) * 1000)
        result = timing.webhook_received_at(event_ms)
        assert result == pytest.approx(event_ms / 1000)
