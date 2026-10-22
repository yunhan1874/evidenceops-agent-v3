#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

PII_PATTERNS = (
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
    (re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"), "[PHONE]"),
    (re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)"), "[NATIONAL_ID]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [TOKEN]"),
    (
        re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|secret)\s*[:=]\s*[^\s,;]+"),
        r"\1=[SECRET]",
    ),
    (re.compile(r"(?:工号|员工号)\s*[:：]?\s*[A-Za-z0-9_-]+"), "工号：[EMPLOYEE_ID]"),
)


def redact_text(value: str) -> str:
    redacted = value
    for pattern, replacement in PII_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _tool_names(record: dict[str, Any]) -> list[str]:
    tools = record.get("tool_calls") or record.get("tools") or []
    return [
        str(item.get("tool_name")) if isinstance(item, dict) else str(item)
        for item in tools
        if item
    ]


def sanitize_record(record: dict[str, Any], *, salt: str) -> dict[str, Any]:
    query = redact_text(str(record.get("query") or "").strip())
    if not query:
        raise ValueError("replay record has no query")
    intent = record.get("expected_intent") or record.get("intent")
    if intent not in {"knowledge_qa", "document_compare", "knowledge_feedback", "unsupported"}:
        raise ValueError(f"replay record has invalid intent: {intent!r}")
    trace_id = str(record.get("trace_id") or record.get("run_id") or query)
    trace_hash = hashlib.sha256(f"{salt}|{trace_id}".encode()).hexdigest()
    tools = _tool_names(record)
    initial_status = record.get("initial_status") or record.get("status") or "completed"
    final_status = record.get("final_status") or (
        "completed" if initial_status == "pending_approval" else initial_status
    )
    result: dict[str, Any] = {
        "schema_version": "evaluation-case/v2",
        "case_id": f"replay_prod_{trace_hash[:12]}",
        "layer": "replay",
        "tags": ["production_replay", "pending_human_review"],
        "query": query,
        "expected_intent": intent,
        "expected_initial_status": initial_status,
        "expected_final_status": final_status,
        "expected_pending_approval": initial_status == "pending_approval",
        "expected_tool_sequence": tools,
        "expected_submit_count": tools.count("submit_feedback"),
        "replay_source": "sanitized_production",
        "review_status": "unreviewed",
        "source_trace_hash": trace_hash,
    }
    for field in ("document_a", "document_b"):
        if record.get(field):
            result[field] = redact_text(str(record[field]))[:255]
    passport = record.get("evidence_passport") or {}
    verdict = record.get("expected_evidence_verdict") or passport.get("verdict")
    if verdict:
        result["expected_evidence_verdict"] = verdict
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a PII-reduced, human-review-required replay staging set"
    )
    parser.add_argument("input", type=Path, help="Raw JSONL exported from audited Agent runs")
    parser.add_argument("--output", type=Path, default=Path("evaluation/replay_staging.jsonl"))
    parser.add_argument("--salt-env", default="REPLAY_HASH_SALT")
    args = parser.parse_args()
    salt = os.getenv(args.salt_env, "")
    if len(salt) < 16:
        raise SystemExit(f"Set {args.salt_env} to at least 16 characters before processing traces")

    sanitized: list[dict[str, Any]] = []
    with args.input.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                sanitized.append(sanitize_record(json.loads(line), salt=salt))
            except (json.JSONDecodeError, ValueError) as exc:
                raise SystemExit(f"{args.input}:{line_number}: {exc}") from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in sanitized).encode(
            "utf-8"
        )
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "count": len(sanitized),
                "review_required": True,
                "next_step": "Human-review labels, set review_status=approved, then add to manifest.",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
