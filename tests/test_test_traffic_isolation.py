"""
測試流量隔離：E2E_TEST_MODE=1 時，對話日誌與錯誤回報改寫入 test_ 前綴集合。
涵蓋：集合名稱規則、conversation_logger 與 feedback_skill 的實際寫入目標。
"""

from unittest.mock import MagicMock

import pytest

import core.conversation_logger as conversation_logger
import services.firestore_client as firestore_client
import skills.feedback_skill as feedback_skill


@pytest.fixture
def fake_db(monkeypatch):
    """以假 Firestore client 取代 get_db，記錄寫入的集合名稱"""
    db = MagicMock()
    monkeypatch.setattr(firestore_client, "get_db", lambda: db)
    return db


class TestCollectionName:
    def test_production_keeps_original_name(self, monkeypatch):
        monkeypatch.delenv("E2E_TEST_MODE", raising=False)
        name = firestore_client.collection_name("feedback_reports")
        assert name == "feedback_reports"

    def test_test_mode_adds_prefix(self, monkeypatch):
        monkeypatch.setenv("E2E_TEST_MODE", "1")
        assert (
            firestore_client.collection_name("conversation_logs")
            == "test_conversation_logs"
        )


class TestWriteTargets:
    @pytest.mark.parametrize(
        "mode, expected",
        [(None, "conversation_logs"), ("1", "test_conversation_logs")],
    )
    def test_conversation_log_collection(self, monkeypatch, fake_db, mode, expected):
        if mode is None:
            monkeypatch.delenv("E2E_TEST_MODE", raising=False)
        else:
            monkeypatch.setenv("E2E_TEST_MODE", mode)
        conversation_logger._write_to_firestore({"user_input": "中山區拉麵"})
        fake_db.collection.assert_called_once_with(expected)

    @pytest.mark.parametrize(
        "mode, expected",
        [(None, "feedback_reports"), ("1", "test_feedback_reports")],
    )
    def test_feedback_report_collection(self, monkeypatch, fake_db, mode, expected):
        if mode is None:
            monkeypatch.delenv("E2E_TEST_MODE", raising=False)
        else:
            monkeypatch.setenv("E2E_TEST_MODE", mode)
        monkeypatch.setattr(feedback_skill, "USE_FIRESTORE", True)
        assert feedback_skill.collect_report("某店", "地址錯誤", "U_test") is True
        fake_db.collection.assert_called_once_with(expected)
