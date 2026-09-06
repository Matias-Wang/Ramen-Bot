"""
測試 Usage Tracker 的配額邏輯。
涵蓋：日期重置、配額檢查遞增、Token 累加。
使用 monkeypatch 將 LOG_PATH 導向 tmp_path，避免污染正式 log。
"""

import json
import pytest
from datetime import date, timedelta
import core.usage_tracker as usage_tracker


@pytest.fixture(autouse=True)
def redirect_log_path(tmp_path, monkeypatch):
    """每個測試都使用獨立的暫存 log 路徑"""
    test_log = tmp_path / "usage.json"
    monkeypatch.setattr(usage_tracker, "LOG_PATH", str(test_log))
    return test_log


# ─── _reset_if_new_day ─────────────────────────────────────────────────────────

class TestResetIfNewDay:
    def test_old_date_resets_counts(self):
        yesterday = str(date.today() - timedelta(days=1))
        old_data = {
            "date": yesterday,
            "google_maps_api": {"count": 50, "limit": 100},
            "llm_gemini": {"count": 80, "token_consumed": 5000, "limit": 100},
            "line_api": {"count": 30, "limit": 100},
        }
        result = usage_tracker._reset_if_new_day(old_data)
        assert result["date"] == str(date.today())
        assert result["google_maps_api"]["count"] == 0
        assert result["llm_gemini"]["count"] == 0
        assert result["line_api"]["count"] == 0

    def test_today_keeps_counts(self):
        today = str(date.today())
        data = {
            "date": today,
            "google_maps_api": {"count": 42, "limit": 100},
            "llm_gemini": {"count": 10, "token_consumed": 300, "limit": 100},
            "line_api": {"count": 7, "limit": 100},
        }
        result = usage_tracker._reset_if_new_day(data)
        assert result["google_maps_api"]["count"] == 42
        assert result["llm_gemini"]["count"] == 10


# ─── check_and_increment ──────────────────────────────────────────────────────

class TestCheckAndIncrement:
    def test_first_call_returns_true(self):
        assert usage_tracker.check_and_increment("google_maps_api") is True

    def test_count_increments_after_call(self, redirect_log_path):
        usage_tracker.check_and_increment("google_maps_api")
        data = json.loads(redirect_log_path.read_text(encoding="utf-8"))
        assert data["google_maps_api"]["count"] == 1

    def test_multiple_calls_accumulate(self, redirect_log_path):
        for _ in range(3):
            usage_tracker.check_and_increment("llm_gemini")
        data = json.loads(redirect_log_path.read_text(encoding="utf-8"))
        assert data["llm_gemini"]["count"] == 3

    def test_at_limit_returns_false(self, redirect_log_path):
        """寫入已達上限的資料後，呼叫應回傳 False"""
        at_limit = {
            "date": str(date.today()),
            "google_maps_api": {"count": 100, "limit": 100},
            "llm_gemini": {"count": 0, "token_consumed": 0, "limit": 100},
            "line_api": {"count": 0, "limit": 100},
        }
        redirect_log_path.write_text(json.dumps(at_limit), encoding="utf-8")
        assert usage_tracker.check_and_increment("google_maps_api") is False

    def test_at_limit_count_does_not_exceed(self, redirect_log_path):
        """達到上限後計數不應再增加"""
        at_limit = {
            "date": str(date.today()),
            "google_maps_api": {"count": 100, "limit": 100},
            "llm_gemini": {"count": 0, "token_consumed": 0, "limit": 100},
            "line_api": {"count": 0, "limit": 100},
        }
        redirect_log_path.write_text(json.dumps(at_limit), encoding="utf-8")
        usage_tracker.check_and_increment("google_maps_api")
        data = json.loads(redirect_log_path.read_text(encoding="utf-8"))
        assert data["google_maps_api"]["count"] == 100

    def test_unknown_key_returns_false(self):
        assert usage_tracker.check_and_increment("nonexistent_key") is False

    def test_all_valid_keys_accepted(self):
        for key in ("google_maps_api", "llm_gemini", "line_api"):
            assert usage_tracker.check_and_increment(key) is True

    def test_count_parameter_increments_by_that_amount(self, redirect_log_path):
        """LINE 的用量以訊息則數計算，單次 push 可帶多則（引導文 + Flex），
        只計 1 會讓本地配額低於 LINE 實際計算的用量。"""
        usage_tracker.check_and_increment("line_api", 2)
        data = json.loads(redirect_log_path.read_text(encoding="utf-8"))
        assert data["line_api"]["count"] == 2

    def test_count_defaults_to_one(self, redirect_log_path):
        usage_tracker.check_and_increment("line_api")
        data = json.loads(redirect_log_path.read_text(encoding="utf-8"))
        assert data["line_api"]["count"] == 1

    def test_rejects_when_count_would_exceed_limit(self, redirect_log_path):
        """剩餘額度不足以容納 count 時應拒絕，而不是先超出再說。"""
        # 先寫一次讓檔案生成，才讀得到 limit
        assert usage_tracker.check_and_increment("line_api") is True
        limit = json.loads(
            redirect_log_path.read_text(encoding="utf-8")
        )["line_api"]["limit"]
        for _ in range(limit - 2):
            assert usage_tracker.check_and_increment("line_api") is True
        # 只剩 1 額度，要求 2 則應被拒絕且不改變計數
        assert usage_tracker.check_and_increment("line_api", 2) is False
        data = json.loads(redirect_log_path.read_text(encoding="utf-8"))
        assert data["line_api"]["count"] == limit - 1
        # 但仍容得下 1 則
        assert usage_tracker.check_and_increment("line_api") is True


# ─── record_tokens ─────────────────────────────────────────────────────────────

class TestRecordTokens:
    def test_tokens_accumulate(self, redirect_log_path):
        usage_tracker.record_tokens(500)
        usage_tracker.record_tokens(300)
        data = json.loads(redirect_log_path.read_text(encoding="utf-8"))
        assert data["llm_gemini"]["token_consumed"] == 800

    def test_zero_tokens_does_not_crash(self):
        usage_tracker.record_tokens(0)

    def test_token_count_persisted_to_file(self, redirect_log_path):
        usage_tracker.record_tokens(1234)
        data = json.loads(redirect_log_path.read_text(encoding="utf-8"))
        assert data["llm_gemini"]["token_consumed"] == 1234
