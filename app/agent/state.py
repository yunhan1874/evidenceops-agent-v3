from __future__ import annotations

from typing import Any, Literal, TypedDict


class AgentState(TypedDict, total=False):
    request_id: str
    run_id: str
    user_id: str
    session_id: str
    ragflow_session_id: str
    query: str
    document_a: str | None
    document_b: str | None

    intent: Literal["knowledge_qa", "document_compare", "knowledge_feedback", "unsupported"]
    candidate_answer: str
    final_answer: str
    citations: list[dict[str, Any]]
    evidence_passport: dict[str, Any]
    feedback_draft: dict[str, Any]
    feedback_target_resolved: bool
    approved: bool
    approval_comment: str | None
    pending_approval: dict[str, Any]
    user_role: str
    tool_call_count: int
    context_snapshot: dict[str, Any]
    model_routes: list[dict[str, Any]]
    termination_reason: str | None
    error: str | None
