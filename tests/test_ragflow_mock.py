from __future__ import annotations

import unittest

from app.clients.ragflow import RagFlowClient


class RagFlowMockTests(unittest.TestCase):
    def test_double_source_fixture(self) -> None:
        result = RagFlowClient._mock_chat("比较两个版本【演练：双来源】")
        chunks = result["reference"]["chunks"]
        self.assertEqual(len(chunks), 2)
        self.assertEqual(len({chunk["document_name"] for chunk in chunks}), 2)

    def test_missing_score_fixture(self) -> None:
        result = RagFlowClient._mock_chat("报销制度是什么【演练：缺少分数】")
        self.assertNotIn("similarity", result["reference"]["chunks"][0])

    def test_empty_answer_fixture(self) -> None:
        result = RagFlowClient._mock_chat("报销制度是什么【演练：空答案】")
        self.assertEqual(result["answer"], "")

    def test_percentage_score_fixture(self) -> None:
        result = RagFlowClient._mock_chat("报销制度是什么【演练：百分制分数】")
        self.assertEqual(result["reference"]["chunks"][0]["similarity"], 88)


if __name__ == "__main__":
    unittest.main()
