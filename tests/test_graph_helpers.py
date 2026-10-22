from __future__ import annotations

import unittest

from app.agent.graph import _citations_from_reference


class CitationExtractionTests(unittest.TestCase):
    def test_zero_score_and_page_are_not_treated_as_missing(self) -> None:
        citations = _citations_from_reference(
            {
                "chunks": [
                    {
                        "document_name": "empty.pdf",
                        "content": "边界片段",
                        "similarity": 0.0,
                        "page": 0,
                    }
                ]
            }
        )
        self.assertEqual(citations[0]["score"], 0.0)
        self.assertEqual(citations[0]["page"], 0)


if __name__ == "__main__":
    unittest.main()
