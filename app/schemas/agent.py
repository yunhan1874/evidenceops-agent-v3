from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

Intent = Literal["knowledge_qa", "document_compare", "knowledge_feedback", "unsupported"]


class CreateRunRequest(BaseModel):
    session_id: UUID
    query: str = Field(min_length=1, max_length=4000)
    document_a: str | None = Field(default=None, max_length=255)
    document_b: str | None = Field(default=None, max_length=255)


class ResumeRunRequest(BaseModel):
    approved: bool
    comment: str | None = Field(default=None, max_length=1000)


class ToolCallResponse(BaseModel):
    tool_name: str
    input_payload: dict[str, Any]
    output_payload: dict[str, Any] | None
    status: str
    latency_ms: int | None
    error_message: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RunEventResponse(BaseModel):
    event_type: str
    name: str
    parent_name: str | None = None
    status: str
    duration_ms: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    model_config = {"from_attributes": True}


class HarnessSummary(BaseModel):
    versions: dict[str, str] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    model_routes: list[dict[str, Any]] = Field(default_factory=list)
    termination_reason: str | None = None
    events: list[RunEventResponse] = Field(default_factory=list)


class EvidenceCoverage(BaseModel):
    citation_count: int
    source_count: int
    scored_citation_count: int
    average_score: float | None = None
    top_score: float | None = None


class EvidenceItem(BaseModel):
    evidence_id: str
    source_id: str
    document_name: str
    page: int | str | None = None
    score: float | None = None
    snippet: str
    quarantined: bool = False


class EvidencePassport(BaseModel):
    policy_version: str
    task: Literal["knowledge_qa", "document_compare"]
    verdict: Literal["grounded", "review_required", "insufficient", "blocked"]
    confidence: float = Field(ge=0, le=1)
    high_risk: bool
    coverage: EvidenceCoverage
    risk_flags: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    recommended_action: Literal[
        "answer", "answer_with_warning", "abstain_and_repair", "quarantine_and_security_review"
    ]
    recovery_plan: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)


class RunResponse(BaseModel):
    run_id: UUID
    session_id: UUID
    status: str
    intent: str | None = None
    answer: str | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    evidence_passport: EvidencePassport | None = None
    pending_approval: dict[str, Any] | None = None
    error_message: str | None = None
    tool_calls: list[ToolCallResponse] = Field(default_factory=list)
    harness: HarnessSummary | None = None


class RunHistoryItem(BaseModel):
    run_id: UUID
    session_id: UUID
    query: str
    status: str
    intent: str | None = None
    answer: str | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    evidence_passport: EvidencePassport | None = None
    pending_approval: dict[str, Any] | None = None
    error_message: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
    tool_calls: list[ToolCallResponse] = Field(default_factory=list)


class SessionHistoryResponse(BaseModel):
    session_id: UUID
    total: int
    page: int
    page_size: int
    items: list[RunHistoryItem]


class DeleteRunResponse(BaseModel):
    run_id: UUID
    session_id: UUID
    status: str
    message: str
