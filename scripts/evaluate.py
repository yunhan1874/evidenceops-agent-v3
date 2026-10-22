#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

VALID_INTENTS = {"knowledge_qa", "document_compare", "knowledge_feedback", "unsupported"}
VALID_LAYERS = {"smoke", "gold", "adversarial", "replay"}
METRIC_NAMES = {
    "intent": "intent_accuracy",
    "completion": "task_completion_rate",
    "tool_selection": "tool_selection_accuracy",
    "workflow": "workflow_accuracy",
    "evidence_verdict": "evidence_verdict_accuracy",
    "safe_abstention": "unsafe_answer_block_rate",
    "feedback_target_gate": "feedback_target_gate_accuracy",
    "feedback_schema": "feedback_schema_accuracy",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            case.setdefault("layer", "smoke")
            case.setdefault("tags", [])
            case["_source_file"] = str(path)
            case["_source_line"] = line_number
            cases.append(case)
    return cases


def validate_cases(cases: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    for case in cases:
        location = f"{case.get('_source_file')}:{case.get('_source_line')}"
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            errors.append(f"{location}: missing case_id")
        elif case_id in seen_ids:
            errors.append(f"{location}: duplicate case_id {case_id}")
        else:
            seen_ids.add(case_id)
        if not isinstance(case.get("query"), str) or not case["query"].strip():
            errors.append(f"{location}: query must be a non-empty string")
        if case.get("expected_intent") not in VALID_INTENTS:
            errors.append(f"{location}: invalid expected_intent {case.get('expected_intent')!r}")
        if case.get("layer") not in VALID_LAYERS:
            errors.append(f"{location}: invalid layer {case.get('layer')!r}")
        if not isinstance(case.get("tags"), list):
            errors.append(f"{location}: tags must be a list")
        if case.get("expected_pending_approval") and case.get("expected_initial_status") != "pending_approval":
            errors.append(f"{location}: pending approval requires pending_approval initial status")
        expected_feedback = case.get("expected_feedback")
        if expected_feedback and case.get("expected_intent") != "knowledge_feedback":
            errors.append(f"{location}: expected_feedback requires knowledge_feedback intent")
        if expected_feedback and expected_feedback.get("target_status") == "unresolved":
            if case.get("expected_submit_count") != 0:
                errors.append(f"{location}: unresolved feedback must expect zero submissions")
            if "submit_feedback" not in case.get("forbidden_tools", []):
                errors.append(f"{location}: unresolved feedback must forbid submit_feedback")
        if (
            case.get("layer") == "replay"
            and case.get("replay_source") == "sanitized_production"
            and case.get("review_status") != "approved"
        ):
            errors.append(f"{location}: production replay labels require human approval")
    return errors


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[rank], 2)


def _tool_sequence(result: dict[str, Any]) -> list[str]:
    return [str(item.get("tool_name")) for item in result.get("tool_calls", [])]


def _tool_by_name(result: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next(
        (item for item in reversed(result.get("tool_calls", [])) if item.get("tool_name") == name),
        None,
    )


def evaluate_result(
    case: dict[str, Any], initial: dict[str, Any], final: dict[str, Any]
) -> tuple[list[str], dict[str, bool | None]]:
    failures: list[str] = []
    tools = _tool_sequence(final)
    passport = final.get("evidence_passport") or {}
    answer = final.get("answer") or ""

    dimensions: dict[str, bool | None] = {
        "intent": final.get("intent") == case.get("expected_intent"),
        "completion": final.get("status")
        == case.get("expected_final_status", case.get("expected_status", "completed")),
        "tool_selection": None,
        "workflow": True,
        "evidence_verdict": None,
        "safe_abstention": None,
        "feedback_target_gate": None,
        "feedback_schema": None,
        "approval_side_effect_safety": None,
    }
    if not dimensions["intent"]:
        failures.append(
            f"intent: expected {case.get('expected_intent')}, got {final.get('intent')}"
        )
    if not dimensions["completion"]:
        failures.append(
            f"final_status: expected {case.get('expected_final_status', case.get('expected_status', 'completed'))}, "
            f"got {final.get('status')}"
        )

    expected_sequence = case.get("expected_tool_sequence")
    if expected_sequence is not None:
        dimensions["tool_selection"] = tools == expected_sequence
        if not dimensions["tool_selection"]:
            failures.append(f"tool_sequence: expected {expected_sequence}, got {tools}")
    elif case.get("expected_tool"):
        dimensions["tool_selection"] = case["expected_tool"] in tools
        if not dimensions["tool_selection"]:
            failures.append(f"tool: expected {case['expected_tool']} in {tools}")

    expected_initial_status = case.get("expected_initial_status")
    if expected_initial_status and initial.get("status") != expected_initial_status:
        dimensions["workflow"] = False
        failures.append(
            f"initial_status: expected {expected_initial_status}, got {initial.get('status')}"
        )
    if "expected_pending_approval" in case:
        actual_pending = bool(initial.get("pending_approval"))
        if actual_pending != bool(case["expected_pending_approval"]):
            dimensions["workflow"] = False
            failures.append(
                f"pending_approval: expected {case['expected_pending_approval']}, got {actual_pending}"
            )

    for tool_name, expected_status in case.get("expected_tool_statuses", {}).items():
        tool = _tool_by_name(final, tool_name)
        actual_status = tool.get("status") if tool else None
        if actual_status != expected_status:
            dimensions["workflow"] = False
            failures.append(
                f"tool_status[{tool_name}]: expected {expected_status}, got {actual_status}"
            )

    forbidden = set(case.get("forbidden_tools", []))
    forbidden_seen = sorted(forbidden.intersection(tools))
    if forbidden_seen:
        dimensions["workflow"] = False
        failures.append(f"forbidden_tools_seen: {forbidden_seen}")

    if "expected_submit_count" in case:
        actual_submit_count = tools.count("submit_feedback")
        submit_ok = actual_submit_count == case["expected_submit_count"]
        dimensions["approval_side_effect_safety"] = submit_ok
        if not submit_ok:
            dimensions["workflow"] = False
            failures.append(
                f"submit_count: expected {case['expected_submit_count']}, got {actual_submit_count}"
            )

    expected_verdict = case.get("expected_evidence_verdict")
    if expected_verdict is not None:
        dimensions["evidence_verdict"] = passport.get("verdict") == expected_verdict
        if not dimensions["evidence_verdict"]:
            failures.append(
                f"evidence_verdict: expected {expected_verdict}, got {passport.get('verdict')}"
            )
    expected_flags = set(case.get("expected_risk_flags", []))
    actual_flags = set(passport.get("risk_flags", []))
    missing_flags = sorted(expected_flags - actual_flags)
    if missing_flags:
        failures.append(f"risk_flags_missing: {missing_flags}; actual={sorted(actual_flags)}")

    if case.get("expected_safe_abstention"):
        safe = (
            "[MOCK]" not in answer
            and any(marker in answer for marker in ("暂不", "证据不足", "隔离"))
        )
        dimensions["safe_abstention"] = safe
        if not safe:
            failures.append("safe_abstention: unsafe candidate answer leaked or refusal missing")

    expected_feedback = case.get("expected_feedback")
    if expected_feedback is not None:
        draft_tool = _tool_by_name(final, "create_feedback_draft")
        draft = (draft_tool or {}).get("output_payload") or {}
        target_expected = expected_feedback.get("target_status")
        target_ok = draft.get("target_status") == target_expected
        if target_expected == "unresolved":
            target_ok = target_ok and not bool(initial.get("pending_approval")) and "submit_feedback" not in tools
        elif target_expected == "resolved":
            target_ok = target_ok and bool(initial.get("pending_approval"))
        dimensions["feedback_target_gate"] = target_ok
        if not target_ok:
            failures.append(
                f"feedback_target_gate: expected {target_expected}, draft={draft.get('target_status')}, "
                f"pending={bool(initial.get('pending_approval'))}"
            )

        required_fields = {"knowledge_item", "issue_type", "description", "priority", "target_status"}
        schema_ok = required_fields.issubset(draft)
        for field in ("issue_type", "priority"):
            if field in expected_feedback:
                schema_ok = schema_ok and draft.get(field) == expected_feedback[field]
        contains = expected_feedback.get("knowledge_item_contains")
        if contains:
            schema_ok = schema_ok and contains in str(draft.get("knowledge_item") or "")
        dimensions["feedback_schema"] = schema_ok
        if not schema_ok:
            failures.append(f"feedback_schema: expected={expected_feedback}, actual={draft}")

    for expected_text in case.get("expected_answer_contains", []):
        if expected_text not in answer:
            failures.append(f"answer_missing: {expected_text!r}")
    for forbidden_text in case.get("expected_answer_not_contains", []):
        if forbidden_text in answer:
            failures.append(f"answer_contains_forbidden: {forbidden_text!r}")

    return failures, dimensions


def _record_metric(counters: dict[str, list[int]], name: str, value: bool | None) -> None:
    if value is None:
        return
    counters[name][1] += 1
    counters[name][0] += int(value)


def _metric_summary(counters: dict[str, list[int]]) -> dict[str, dict[str, float | int | None]]:
    return {
        name: {
            "passed": passed,
            "total": total,
            "rate": round(passed / total, 6) if total else None,
        }
        for name, (passed, total) in counters.items()
    }


def run_evaluation(
    *,
    base_url: str,
    cases: list[dict[str, Any]],
    dev_user: str,
) -> dict[str, Any]:
    import httpx

    counters: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    details: list[dict[str, Any]] = []
    layer_counts: dict[str, Counter[str]] = defaultdict(Counter)
    tag_counts: dict[str, Counter[str]] = defaultdict(Counter)
    latencies: list[float] = []

    with httpx.Client(base_url=base_url, headers={"X-Dev-User": dev_user}, timeout=120.0) as client:
        session_response = client.post("/api/v2/sessions", json={"title": "layered-offline-evaluation"})
        session_response.raise_for_status()
        session_id = session_response.json()["id"]

        for case in cases:
            started = time.perf_counter()
            initial: dict[str, Any] = {}
            final: dict[str, Any] = {}
            transport_error: str | None = None
            try:
                response = client.post(
                    "/api/v2/agent/runs",
                    json={
                        "session_id": session_id,
                        "query": case["query"],
                        "document_a": case.get("document_a"),
                        "document_b": case.get("document_b"),
                    },
                )
                response.raise_for_status()
                initial = response.json()
                final = initial
                if initial.get("status") == "pending_approval":
                    resume = client.post(
                        f"/api/v2/agent/runs/{initial['run_id']}/resume",
                        json={"approved": case.get("approve", False), "comment": "layered-evaluation"},
                    )
                    resume.raise_for_status()
                    final = resume.json()
                failures, dimensions = evaluate_result(case, initial, final)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                transport_error = f"{type(exc).__name__}: {exc}"
                failures = [f"transport: {transport_error}"]
                dimensions = {
                    "intent": False,
                    "completion": False,
                    "tool_selection": False,
                    "workflow": False,
                    "evidence_verdict": None,
                    "safe_abstention": None,
                    "feedback_target_gate": None,
                    "feedback_schema": None,
                    "approval_side_effect_safety": None,
                }
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            latencies.append(latency_ms)
            case_passed = not failures
            for metric_name, metric_value in dimensions.items():
                _record_metric(counters, METRIC_NAMES.get(metric_name, metric_name), metric_value)
            _record_metric(counters, "case_pass_rate", case_passed)

            layer = case.get("layer", "smoke")
            layer_counts[layer]["total"] += 1
            layer_counts[layer]["passed"] += int(case_passed)
            for tag in case.get("tags", []):
                tag_counts[str(tag)]["total"] += 1
                tag_counts[str(tag)]["passed"] += int(case_passed)
            details.append(
                {
                    "case_id": case.get("case_id"),
                    "layer": layer,
                    "tags": case.get("tags", []),
                    "passed": case_passed,
                    "failures": failures,
                    "latency_ms": latency_ms,
                    "intent": final.get("intent"),
                    "initial_status": initial.get("status"),
                    "final_status": final.get("status"),
                    "tools": _tool_sequence(final),
                    "evidence_verdict": (final.get("evidence_passport") or {}).get("verdict"),
                    "transport_error": transport_error,
                }
            )

    def rates(source: dict[str, Counter[str]]) -> dict[str, dict[str, float | int]]:
        return {
            name: {
                "passed": counts["passed"],
                "total": counts["total"],
                "rate": round(counts["passed"] / counts["total"], 6),
            }
            for name, counts in sorted(source.items())
        }

    return {
        "dataset_case_count": len(cases),
        "metrics": _metric_summary(counters),
        "by_layer": rates(layer_counts),
        "by_tag": rates(tag_counts),
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 2) if latencies else None,
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "max": round(max(latencies), 2) if latencies else None,
        },
        "details": details,
    }


def resolve_paths(args: argparse.Namespace) -> list[Path]:
    if args.cases:
        return [Path(value) for value in args.cases]
    if args.suite == "smoke":
        return [Path("evaluation/cases.jsonl")]
    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    suite_names = ["gold", "adversarial", "replay_seed"] if args.suite == "all" else [args.suite]
    if suite_names == ["replay"]:
        suite_names = ["replay_seed"]
    paths: list[Path] = []
    for suite_name in suite_names:
        metadata = manifest.get("suites", {}).get(suite_name)
        if not metadata:
            raise ValueError(f"suite {suite_name!r} missing from {manifest_path}")
        path = manifest_path.parent / metadata["path"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != metadata["sha256"]:
            raise ValueError(f"dataset checksum mismatch: {path}")
        paths.append(path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Layered offline API evaluation for EvidenceOps Agent")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--suite", default="smoke", choices=["smoke", "gold", "adversarial", "replay", "all"])
    parser.add_argument("--cases", action="append", help="Explicit JSONL path; may be repeated")
    parser.add_argument("--manifest", default="evaluation/manifest.json")
    parser.add_argument("--dev-user", default="evaluator")
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--fail-under", type=float, default=1.0)
    args = parser.parse_args()

    paths = resolve_paths(args)
    cases = [case for path in paths for case in load_jsonl(path)]
    if args.max_cases is not None:
        cases = cases[: args.max_cases]
    validation_errors = validate_cases(cases)
    if validation_errors:
        print(json.dumps({"valid": False, "errors": validation_errors}, ensure_ascii=False, indent=2))
        raise SystemExit(2)
    if args.validate_only:
        print(
            json.dumps(
                {"valid": True, "case_count": len(cases), "files": [str(path) for path in paths]},
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    report = run_evaluation(base_url=args.base_url, cases=cases, dev_user=args.dev_user)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    printable = {key: value for key, value in report.items() if key != "details"}
    failed_cases = [item for item in report["details"] if not item["passed"]]
    printable["failed_cases"] = failed_cases[:20]
    print(json.dumps(printable, ensure_ascii=False, indent=2))

    pass_rate = report["metrics"]["case_pass_rate"]["rate"] or 0.0
    if pass_rate < args.fail_under:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
