from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal
from urllib.parse import urlparse

Risk = Literal["read", "write", "high_risk"]


@dataclass(frozen=True)
class ToolContract:
    name: str
    version: str
    risk: Risk
    timeout_seconds: float
    max_output_chars: int
    allowed_roles: tuple[str, ...]
    input_schema: dict[str, str]
    output_schema: dict[str, str]
    egress: str | None = None

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["allowed_roles"] = list(self.allowed_roles)
        return value


class ToolPolicyEngine:
    """A small deny-by-default policy boundary around every graph tool."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_output_chars: int,
        max_calls: int,
        allowed_egress_hosts: set[str] | None = None,
    ) -> None:
        self.max_calls = max_calls
        self.allowed_egress_hosts = {
            value.casefold() for value in (allowed_egress_hosts or set())
        }
        common_roles = ("user", "admin")
        self.contracts = {
            "knowledge_search": ToolContract(
                "knowledge_search", "1.0", "read", timeout_seconds, max_output_chars,
                common_roles, {"query": "string"}, {"answer": "string", "citations": "array"},
                "configured-ragflow-host",
            ),
            "compare_documents": ToolContract(
                "compare_documents", "1.0", "read", timeout_seconds, max_output_chars,
                common_roles,
                {"query": "string", "document_a": "string", "document_b": "string"},
                {"answer": "string", "citations": "array"}, "configured-ragflow-host",
            ),
            "create_feedback_draft": ToolContract(
                "create_feedback_draft", "1.0", "read", timeout_seconds, max_output_chars,
                common_roles, {"query": "string"}, {"draft": "object"},
                "configured-llm-host-or-local-fallback",
            ),
            "evidence_guard": ToolContract(
                "evidence_guard", "1.0", "read", min(timeout_seconds, 5), max_output_chars,
                common_roles, {"candidate_answer": "string", "citations": "array"},
                {"passport": "object"}, None,
            ),
            "submit_feedback": ToolContract(
                "submit_feedback", "1.0", "high_risk", min(timeout_seconds, 10),
                max_output_chars, common_roles, {"draft": "object", "approved": "boolean"},
                {"feedback_id": "uuid", "status": "string"}, None,
            ),
        }

    def authorize(
        self, tool_name: str, *, role: str, approved: bool, calls_so_far: int
    ) -> tuple[bool, str]:
        contract = self.contracts.get(tool_name)
        if contract is None:
            return False, "unknown_tool"
        if role not in contract.allowed_roles:
            return False, "role_denied"
        if calls_so_far >= self.max_calls:
            return False, "tool_call_budget_exceeded"
        if contract.risk == "high_risk" and not approved:
            return False, "human_approval_required"
        return True, "allowed"

    def contract(self, tool_name: str) -> ToolContract:
        return self.contracts[tool_name]

    def authorize_egress(self, tool_name: str, url: str) -> tuple[bool, str]:
        contract = self.contracts.get(tool_name)
        if contract is None:
            return False, "unknown_tool"
        if contract.egress is None:
            return True, "no_egress_required"
        host = (urlparse(url).hostname or "").casefold()
        if host and host in self.allowed_egress_hosts:
            return True, "egress_allowed"
        return False, "egress_host_denied"

    def manifest(self) -> list[dict[str, Any]]:
        return [contract.public_dict() for contract in self.contracts.values()]

    @staticmethod
    def truncate_text(value: str, max_chars: int) -> tuple[str, bool]:
        if len(value) <= max_chars:
            return value, False
        return value[:max_chars] + "…[truncated]", True
