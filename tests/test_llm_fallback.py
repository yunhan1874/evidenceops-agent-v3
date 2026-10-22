import unittest

from app.clients.llm import LLMClient


class LLMFallbackTests(unittest.TestCase):
    def test_heuristic_router(self) -> None:
        self.assertEqual(LLMClient._heuristic_intent("公司的报销制度是什么"), "knowledge_qa")
        self.assertEqual(
            LLMClient._heuristic_intent("比较2025版和2026版制度的差异"),
            "document_compare",
        )
        self.assertEqual(
            LLMClient._heuristic_intent("食品添加剂标准里山梨酸钾用量不对，请反馈给知识库"),
            "knowledge_feedback",
        )
        self.assertEqual(
            LLMClient._heuristic_intent("不要提交反馈，我只想问标签审核流程是什么"),
            "knowledge_qa",
        )
        inaccurate = LLMClient._heuristic_feedback_draft(
            "食品添加剂标准里山梨酸钾用量说明不对，请反馈给知识库维护人员"
        )
        self.assertEqual(inaccurate["issue_type"], "inaccurate")
        self.assertEqual(inaccurate["priority"], "high")
        self.assertEqual(inaccurate["target_status"], "resolved")
        self.assertIn("山梨酸钾用量说明", inaccurate["knowledge_item"])

        outdated = LLMClient._heuristic_feedback_draft(
            "标签审核流程这条知识库内容可能过期了，请提交反馈"
        )
        self.assertEqual(outdated["issue_type"], "outdated")
        self.assertEqual(outdated["knowledge_item"], "标签审核流程")

        vague = LLMClient._heuristic_feedback_draft("知识库有问题，请提交反馈")
        self.assertEqual(vague["target_status"], "unresolved")
        for vague_query in (
            "标签内容不完整，请更新知识库",
            "食品知识有问题",
            "知识库好像写错了，帮忙反馈",
            "这个制度过期了，请更新",
            "流程缺东西，提交一下反馈",
            "标准看不明白，麻烦反馈",
        ):
            with self.subTest(vague_query=vague_query):
                self.assertEqual(
                    LLMClient._heuristic_feedback_draft(vague_query)["target_status"],
                    "unresolved",
                )

        self.assertEqual(
            LLMClient._heuristic_intent("过敏原标识要求中的豁免说明不准确，请纠错"),
            "knowledge_feedback",
        )
        self.assertEqual(
            LLMClient._heuristic_intent("请对比召回管理流程2025和2026有哪些变化"),
            "document_compare",
        )
        self.assertEqual(
            LLMClient._heuristic_intent("采购制度是什么？【演练：缺少分数】"),
            "knowledge_qa",
        )
        self.assertEqual(
            LLMClient._heuristic_intent("仓库盘点差异超过阈值后如何处理？"),
            "knowledge_qa",
        )
        self.assertEqual(LLMClient._heuristic_intent("给供应商打电话"), "unsupported")


if __name__ == "__main__":
    unittest.main()
