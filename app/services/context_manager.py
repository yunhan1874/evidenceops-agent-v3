from __future__ import annotations

import re
from typing import Any


class ContextManager:
    """Build a small, auditable context instead of replaying an unbounded transcript."""

    _poison_patterns = (
        re.compile(r"ignore (?:all|any|the) previous instructions", re.IGNORECASE),
        re.compile(r"reveal (?:the )?(?:system prompt|secret|api key)", re.IGNORECASE),
        re.compile(r"忽略(?:此前|之前|以上|所有).{0,12}(?:指令|要求)"),
        re.compile(r"(?:泄露|输出|展示).{0,12}(?:系统提示词|密钥|token)", re.IGNORECASE),
    )

    def __init__(self, *, max_chars: int, max_turns: int) -> None:
        self.max_chars = max_chars
        self.max_turns = max_turns

    def prepare(self, query: str, history: list[dict[str, Any]]) -> dict[str, Any]:
        kept: list[str] = []
        poisoned_turns = 0
        truncated = False
        seen: set[str] = set()
        for item in history[-self.max_turns :]:
            user_text = str(item.get("query") or "").strip()
            answer = str(item.get("answer") or "").strip()
            joined = f"用户：{user_text}\n助手：{answer}"
            if any(pattern.search(joined) for pattern in self._poison_patterns):
                poisoned_turns += 1
                continue
            normalized = re.sub(r"\s+", " ", joined).casefold()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            kept.append(joined)

        summary = "\n---\n".join(kept)
        if len(summary) > self.max_chars:
            summary = summary[-self.max_chars :]
            truncated = True
        current_poisoned = any(pattern.search(query) for pattern in self._poison_patterns)
        return {
            "summary": summary,
            "turns_considered": min(len(history), self.max_turns),
            "turns_kept": len(kept),
            "poisoned_turns_removed": poisoned_turns,
            "current_query_poisoning_suspected": current_poisoned,
            "truncated": truncated,
            "max_chars": self.max_chars,
        }

