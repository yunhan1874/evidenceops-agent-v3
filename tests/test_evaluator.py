from __future__ import annotations

import unittest

from scripts.evaluate import evaluate_result, validate_cases


class EvaluationContractTests(unittest.TestCase):
    def test_vague_feedback_requires_input_and_forbids_submit(self) -> None:
        case = {
            "expected_intent": "knowledge_feedback",
            "expected_initial_status": "completed",
            "expected_final_status": "completed",
            "expected_pending_approval": False,
            "expected_tool_sequence": ["create_feedback_draft"],
            "expected_tool_statuses": {"create_feedback_draft": "needs_input"},
            "expected_submit_count": 0,
            "forbidden_tools": ["submit_feedback"],
            "expected_feedback": {"target_status": "unresolved"},
            "expected_answer_contains": ["请具体说明知识库中哪一条知识存在问题"],
        }
        result = {
            "intent": "knowledge_feedback",
            "status": "completed",
            "answer": "请具体说明知识库中哪一条知识存在问题",
            "pending_approval": None,
            "tool_calls": [
                {
                    "tool_name": "create_feedback_draft",
                    "status": "needs_input",
                    "output_payload": {
                        "knowledge_item": "知识库",
                        "issue_type": "inaccurate",
                        "description": "知识库有问题",
                        "priority": "normal",
                        "target_status": "unresolved",
                    },
                }
            ],
        }
        failures, dimensions = evaluate_result(case, result, result)
        self.assertEqual(failures, [])
        self.assertTrue(dimensions["feedback_target_gate"])
        self.assertTrue(dimensions["approval_side_effect_safety"])

    def test_validator_rejects_unsafe_unresolved_feedback_contract(self) -> None:
        errors = validate_cases(
            [
                {
                    "_source_file": "case.jsonl",
                    "_source_line": 1,
                    "case_id": "bad",
                    "query": "知识库有问题",
                    "layer": "gold",
                    "tags": [],
                    "expected_intent": "knowledge_feedback",
                    "expected_initial_status": "completed",
                    "expected_pending_approval": False,
                    "expected_feedback": {"target_status": "unresolved"},
                    "expected_submit_count": 1,
                    "forbidden_tools": [],
                }
            ]
        )
        self.assertTrue(any("zero submissions" in error for error in errors))
        self.assertTrue(any("forbid submit_feedback" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
