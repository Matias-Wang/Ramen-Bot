"""
測試 message_dedup 的本地模式去重邏輯。
涵蓋：首次出現、重複偵測、不同 id 互不影響。
"""

import pytest
import core.message_dedup as message_dedup


@pytest.fixture(autouse=True)
def reset_seen_ids():
    """每個測試都重置記憶體集合，避免測試間互相污染"""
    message_dedup._seen_ids.clear()
    yield
    message_dedup._seen_ids.clear()


class TestIsDuplicateMessage:
    def test_first_call_returns_false(self):
        assert message_dedup.is_duplicate_message("msg_1") is False

    def test_repeated_call_returns_true(self):
        message_dedup.is_duplicate_message("msg_1")
        assert message_dedup.is_duplicate_message("msg_1") is True

    def test_different_ids_do_not_collide(self):
        assert message_dedup.is_duplicate_message("msg_1") is False
        assert message_dedup.is_duplicate_message("msg_2") is False
        assert message_dedup.is_duplicate_message("msg_1") is True
        assert message_dedup.is_duplicate_message("msg_2") is True
