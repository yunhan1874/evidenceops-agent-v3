from __future__ import annotations

import unittest

from scripts.prepare_replay_dataset import redact_text, sanitize_record


class ReplaySanitizerTests(unittest.TestCase):
    def test_redacts_common_identifiers_and_secrets(self) -> None:
        value = redact_text(
            "联系 13800138000 或 alice@example.com，工号 A-102，api_key=sk-secret"
        )
        self.assertNotIn("13800138000", value)
        self.assertNotIn("alice@example.com", value)
        self.assertNotIn("A-102", value)
        self.assertNotIn("sk-secret", value)

    def test_sanitized_replay_requires_human_review(self) -> None:
        item = sanitize_record(
            {
                "trace_id": "trace-1",
                "query": "采购制度是什么？",
                "intent": "knowledge_qa",
                "status": "completed",
                "tools": ["knowledge_search", "evidence_guard"],
                "evidence_passport": {"verdict": "grounded"},
                "user_id": "must-not-be-copied",
                "answer": "must-not-be-copied",
            },
            salt="0123456789abcdef",
        )
        self.assertEqual(item["review_status"], "unreviewed")
        self.assertEqual(item["replay_source"], "sanitized_production")
        self.assertNotIn("user_id", item)
        self.assertNotIn("answer", item)


if __name__ == "__main__":
    unittest.main()
