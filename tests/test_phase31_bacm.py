"""Phase 31 tests: BACM-lite — Hierarchical Importance Compression."""
from __future__ import annotations

import os


class TestSegmentImportance:
    def test_recency_scoring(self):
        from openjarvis.sessions.compression import score_segment_importance

        # First segment (oldest) scores lower
        old_score = score_segment_importance(
            [{"role": "user", "content": "hello"}], 0, 10, None
        )
        # Last segment (newest) scores higher
        new_score = score_segment_importance(
            [{"role": "user", "content": "hello"}], 9, 10, None
        )
        assert new_score > old_score

    def test_tool_result_boost(self):
        from openjarvis.sessions.compression import score_segment_importance

        without_tool = score_segment_importance(
            [{"role": "user", "content": "hello"}], 5, 10, None
        )
        with_tool = score_segment_importance(
            [{"role": "user", "content": "hello"}, {"role": "tool", "content": "result"}],
            5, 10, None
        )
        assert with_tool > without_tool

    def test_task_relevance(self):
        from openjarvis.sessions.compression import score_segment_importance

        irrelevant = score_segment_importance(
            [{"role": "user", "content": "weather today"}], 5, 10, "deploy website"
        )
        relevant = score_segment_importance(
            [{"role": "user", "content": "deploy the website now"}], 5, 10, "deploy website"
        )
        assert relevant > irrelevant

    def test_system_message_boost(self):
        from openjarvis.sessions.compression import score_segment_importance

        without_sys = score_segment_importance(
            [{"role": "user", "content": "hello"}], 5, 10, None
        )
        with_sys = score_segment_importance(
            [{"role": "system", "content": "rules"}, {"role": "user", "content": "hello"}],
            5, 10, None
        )
        assert with_sys > without_sys

    def test_score_capped_at_one(self):
        from openjarvis.sessions.compression import score_segment_importance

        score = score_segment_importance(
            [
                {"role": "system", "content": "deploy website"},
                {"role": "tool", "content": "deployed"},
            ],
            9, 10, "deploy website"
        )
        assert score <= 1.0

    def test_single_segment(self):
        from openjarvis.sessions.compression import score_segment_importance

        score = score_segment_importance(
            [{"role": "user", "content": "hello"}], 0, 1, None
        )
        assert 0.0 <= score <= 1.0


class TestBACMCompressor:
    def setup_method(self):
        os.environ["BACM_COMPRESSION"] = "true"

    def teardown_method(self):
        os.environ.pop("BACM_COMPRESSION", None)

    def test_no_compression_above_50_pct(self):
        from openjarvis.sessions.compression import BACMCompressor

        msgs = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        c = BACMCompressor()
        result = c.compress(msgs, budget_ratio=0.6)
        assert len(result) == len(msgs)

    def test_partial_compression_25_to_50_pct(self):
        from openjarvis.sessions.compression import BACMCompressor

        msgs = [{"role": "user", "content": f"message {i}"} for i in range(30)]
        # Add tool results to make distinct segments
        msgs.insert(5, {"role": "tool", "content": "result 1"})
        msgs.insert(15, {"role": "tool", "content": "result 2"})
        msgs.insert(25, {"role": "tool", "content": "result 3"})

        c = BACMCompressor()
        result = c.compress(msgs, budget_ratio=0.35, current_task="test task")
        assert len(result) <= len(msgs)

    def test_aggressive_compression_below_25_pct(self):
        from openjarvis.sessions.compression import BACMCompressor

        msgs = [{"role": "user", "content": f"message {i}"} for i in range(30)]
        msgs.insert(5, {"role": "tool", "content": "result"})
        msgs.insert(15, {"role": "tool", "content": "result"})

        c = BACMCompressor()
        result = c.compress(msgs, budget_ratio=0.15)
        assert len(result) < len(msgs)

    def test_full_compression_below_10_pct(self):
        from openjarvis.sessions.compression import BACMCompressor

        msgs = [{"role": "user", "content": f"message {i}"} for i in range(30)]
        msgs.insert(5, {"role": "tool", "content": "result"})
        msgs.insert(15, {"role": "tool", "content": "result"})

        c = BACMCompressor()
        result = c.compress(msgs, budget_ratio=0.05)
        assert len(result) < len(msgs)

    def test_empty_messages(self):
        from openjarvis.sessions.compression import BACMCompressor

        c = BACMCompressor()
        assert c.compress([], 0.5) == []

    def test_disabled_flag(self):
        os.environ["BACM_COMPRESSION"] = "false"
        from openjarvis.sessions.compression import BACMCompressor

        msgs = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        c = BACMCompressor()
        result = c.compress(msgs, budget_ratio=0.05)
        assert len(result) == len(msgs)

    def test_segment_messages(self):
        from openjarvis.sessions.compression import BACMCompressor

        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "calling tool"},
            {"role": "tool", "content": "result1"},  # segment boundary
            {"role": "user", "content": "q2"},
            {"role": "tool", "content": "result2"},  # segment boundary
        ]
        c = BACMCompressor()
        segments = c._segment_messages(msgs)
        assert len(segments) >= 2
