from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

EvidenceTask = Literal["knowledge_qa", "document_compare"]


def extract_evidence_passport(tool_calls: Iterable[Any]) -> dict[str, Any] | None:
    """Return the latest persisted passport without adding a column to agent_runs."""

    passport = None
    for tool_call in tool_calls:
        if getattr(tool_call, "tool_name", None) != "evidence_guard":
            continue
        payload = getattr(tool_call, "output_payload", None)
        if isinstance(payload, dict):
            passport = payload
    return passport


@dataclass(frozen=True)
class EvidencePolicy:
    """Deterministic serving-time policy for deciding whether an answer is safe to show."""

    version: str = "evidence-passport/v1"
    min_average_score: float = 0.55
    high_risk_min_score: float = 0.75
    compare_min_sources: int = 2
    max_citations: int = 10


class EvidenceGuard:
    """Build an explainable evidence passport and fail closed on unsafe retrieval results.

    This is deliberately deterministic: the same retrieval result always receives the
    same verdict, which makes the policy cheap, testable and suitable as a first gate.
    A semantic/LLM judge can be added behind this gate without replacing it.
    """

    _INJECTION_PATTERNS = (
        re.compile(r"ignore\s+(all\s+)?previous\s+instructions?", re.IGNORECASE),
        re.compile(r"reveal\s+(the\s+)?(system|developer)\s+prompt", re.IGNORECASE),
        re.compile(r"you\s+are\s+now\s+(in\s+)?developer\s+mode", re.IGNORECASE),
        re.compile(r"忽略.{0,12}(之前|以上|上述).{0,8}(指令|要求|提示)"),
        re.compile(r"(泄露|输出|展示).{0,12}(系统提示词|开发者消息|密钥|api\s*key)", re.IGNORECASE),
        re.compile(r"\b(system|assistant|developer)\s*:\s*", re.IGNORECASE),
    )
    _HIGH_RISK_TERMS = (
        "食品安全",
        "过敏原",
        "致敏",
        "添加剂",
        "用量",
        "限量",
        "保质期",
        "召回",
        "合规",
        "处罚",
        "法律",
        "法规",
        "人身安全",
        "medical",
        "legal",
        "compliance",
    )

    def __init__(self, policy: EvidencePolicy | None = None) -> None:
        self.policy = policy or EvidencePolicy()

    def evaluate(
        self,
        *,
        query: str,
        candidate_answer: str,
        citations: list[dict[str, Any]],
        task: EvidenceTask,
    ) -> dict[str, Any]:
        evidence, clean_citations, injection_detected = self._prepare_evidence(citations)
        scores = [item["score"] for item in evidence if item["score"] is not None]
        sources = {
            item["document_name"].strip().casefold()
            for item in evidence
            if item["document_name"].strip()
        }
        high_risk = any(term.casefold() in query.casefold() for term in self._HIGH_RISK_TERMS)
        average_score = sum(scores) / len(scores) if scores else None
        top_score = max(scores) if scores else None

        flags: list[str] = []
        reasons: list[str] = []
        if not candidate_answer.strip():
            flags.append("empty_answer")
            reasons.append("上游没有生成候选答案")
        if not evidence:
            flags.append("no_citation")
            reasons.append("没有返回可核验的引用片段")
        if evidence and len(scores) < len(evidence):
            flags.append("missing_relevance_score")
            reasons.append("部分引用缺少相关度分数")
        if average_score is not None and average_score < self.policy.min_average_score:
            flags.append("low_relevance")
            reasons.append(
                f"平均相关度 {average_score:.2f} 低于门槛 {self.policy.min_average_score:.2f}"
            )
        if task == "document_compare" and len(sources) < self.policy.compare_min_sources:
            flags.append("insufficient_source_diversity")
            reasons.append(
                f"文档对比需要至少 {self.policy.compare_min_sources} 个独立来源，当前为 {len(sources)}"
            )
        if high_risk:
            flags.append("high_risk_query")
            if top_score is None or top_score < self.policy.high_risk_min_score:
                flags.append("high_risk_evidence_too_weak")
                reasons.append(
                    f"高风险问题的最高相关度未达到 {self.policy.high_risk_min_score:.2f}"
                )
        if injection_detected:
            flags.append("indirect_prompt_injection")
            reasons.append("检索片段包含疑似指令劫持内容，已隔离原文")

        confidence = self._confidence(
            evidence_count=len(evidence),
            source_count=len(sources),
            average_score=average_score,
            injection_detected=injection_detected,
            has_answer=bool(candidate_answer.strip()),
        )
        verdict = self._verdict(flags)
        recovery_plan = self._recovery_plan(flags, task)
        passport = {
            "policy_version": self.policy.version,
            "task": task,
            "verdict": verdict,
            "confidence": confidence,
            "high_risk": high_risk,
            "coverage": {
                "citation_count": len(evidence),
                "source_count": len(sources),
                "scored_citation_count": len(scores),
                "average_score": round(average_score, 4) if average_score is not None else None,
                "top_score": round(top_score, 4) if top_score is not None else None,
            },
            "risk_flags": flags,
            "reasons": reasons or ["引用数量、相关度和来源覆盖满足当前策略"],
            "recommended_action": self._recommended_action(verdict),
            "recovery_plan": recovery_plan,
            "evidence": evidence,
        }
        return {
            "passport": passport,
            "citations": clean_citations,
            "answer": self._render_answer(candidate_answer, passport),
        }

    def _prepare_evidence(
        self, citations: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
        evidence: list[dict[str, Any]] = []
        clean_citations: list[dict[str, Any]] = []
        injection_detected = False
        for index, citation in enumerate(citations[: self.policy.max_citations]):
            content = str(citation.get("content") or citation.get("text") or "").strip()
            suspicious = self._contains_injection(content)
            injection_detected = injection_detected or suspicious
            safe_content = "[已隔离：疑似提示注入内容]" if suspicious else content[:1000]
            document_name = str(citation.get("document_name") or "未知文档")[:255]
            page = citation.get("page")
            score = self._normalize_score(citation.get("score"))
            source_id = str(citation.get("source_id") or index)[:255]
            evidence_id = hashlib.sha256(
                f"{document_name}|{page}|{safe_content}".encode()
            ).hexdigest()[:16]
            evidence.append(
                {
                    "evidence_id": evidence_id,
                    "source_id": source_id,
                    "document_name": document_name,
                    "page": page,
                    "score": score,
                    "snippet": safe_content[:240],
                    "quarantined": suspicious,
                }
            )
            clean_citations.append(
                {
                    "source_id": source_id,
                    "document_name": document_name,
                    "page": page,
                    "score": score,
                    "content": safe_content,
                }
            )
        return evidence, clean_citations, injection_detected

    def _confidence(
        self,
        *,
        evidence_count: int,
        source_count: int,
        average_score: float | None,
        injection_detected: bool,
        has_answer: bool,
    ) -> float:
        if not evidence_count or not has_answer:
            return 0.0
        relevance = average_score if average_score is not None else 0.35
        quantity = min(evidence_count / 3, 1.0)
        diversity = min(source_count / 2, 1.0)
        value = 0.6 * relevance + 0.2 * quantity + 0.2 * diversity
        if injection_detected:
            value = min(value, 0.05)
        return round(max(0.0, min(value, 1.0)), 4)

    @staticmethod
    def _normalize_score(value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            score = float(value)
        except (TypeError, ValueError):
            return None
        if 1 < score <= 100:
            score /= 100
        if score < 0 or score > 1:
            return None
        return round(score, 6)

    def _contains_injection(self, content: str) -> bool:
        return any(pattern.search(content) for pattern in self._INJECTION_PATTERNS)

    @staticmethod
    def _verdict(flags: list[str]) -> str:
        if "indirect_prompt_injection" in flags:
            return "blocked"
        hard_failures = {
            "empty_answer",
            "no_citation",
            "low_relevance",
            "insufficient_source_diversity",
            "high_risk_evidence_too_weak",
        }
        if hard_failures.intersection(flags):
            return "insufficient"
        if "missing_relevance_score" in flags:
            return "review_required"
        return "grounded"

    @staticmethod
    def _recommended_action(verdict: str) -> str:
        return {
            "grounded": "answer",
            "review_required": "answer_with_warning",
            "insufficient": "abstain_and_repair",
            "blocked": "quarantine_and_security_review",
        }[verdict]

    @staticmethod
    def _recovery_plan(flags: list[str], task: EvidenceTask) -> list[str]:
        actions: list[str] = []
        if "indirect_prompt_injection" in flags:
            actions.extend(["quarantine_source_chunk", "notify_knowledge_owner"])
        if "no_citation" in flags or "low_relevance" in flags:
            actions.extend(["rewrite_query", "retry_with_document_scope"])
        if "insufficient_source_diversity" in flags and task == "document_compare":
            actions.append("retrieve_each_document_separately")
        if "high_risk_evidence_too_weak" in flags:
            actions.append("require_human_domain_review")
        if "missing_relevance_score" in flags:
            actions.append("repair_retrieval_metadata")
        return list(dict.fromkeys(actions)) or ["none"]

    @staticmethod
    def _render_answer(candidate_answer: str, passport: dict[str, Any]) -> str:
        verdict = passport["verdict"]
        if verdict == "grounded":
            return candidate_answer
        if verdict == "review_required":
            return "⚠️ 证据元数据不完整，以下内容需人工复核：\n\n" + candidate_answer
        reasons = "；".join(passport["reasons"])
        if verdict == "blocked":
            return f"已隔离本次检索结果，暂不生成答案。原因：{reasons}。"
        return f"证据不足，我暂不输出确定性结论。原因：{reasons}。"
