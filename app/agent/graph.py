from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent.state import AgentState
from app.clients.llm import LLMClient
from app.clients.ragflow import RagFlowClient
from app.core.config import Settings
from app.core.errors import audit_error
from app.db.session import AsyncSessionFactory
from app.repositories.core import KnowledgeFeedbackRepository, RunRepository, ToolCallRepository
from app.services.evidence import EvidenceGuard
from app.services.harness import HarnessService
from app.services.tool_policy import ToolPolicyEngine


def _first_not_none(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def _citations_from_reference(
    reference: dict[str, Any], *, max_citations: int = 10, max_content_chars: int = 1000
) -> list[dict[str, Any]]:
    raw_chunks = reference.get("chunks") or reference.get("chunk_list") or []
    citations: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    if isinstance(raw_chunks, list):
        for index, chunk in enumerate(raw_chunks):
            if not isinstance(chunk, dict):
                continue
            document_name = str(
                chunk.get("document_name")
                or chunk.get("doc_name")
                or chunk.get("name")
                or "未知文档"
            )
            page = _first_not_none(chunk.get("page"), chunk.get("page_num"))
            content = str(chunk.get("content") or chunk.get("text") or "")[:max_content_chars]
            dedupe_key = (document_name, str(page), content)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            citations.append({
                "source_id": str(chunk.get("id") or chunk.get("chunk_id") or index),
                "document_name": document_name,
                "page": page,
                "score": _first_not_none(chunk.get("similarity"), chunk.get("score")),
                "content": content,
            })
            if len(citations) >= max_citations:
                break
    return citations


def build_agent_graph(
    *,
    ragflow: RagFlowClient,
    llm: LLMClient,
    evidence_guard: EvidenceGuard,
    settings: Settings,
    harness: HarnessService,
    tool_policy: ToolPolicyEngine,
    checkpointer: Any,
):
    async def authorize_tool(
        state: AgentState, tool_name: str, *, approved: bool = False
    ) -> tuple[bool, str, int]:
        calls = int(state.get("tool_call_count") or 0)
        allowed, reason = tool_policy.authorize(
            tool_name,
            role=state.get("user_role") or "user",
            approved=approved,
            calls_so_far=calls,
        )
        if allowed:
            if (
                tool_name in {"knowledge_search", "compare_documents"}
                and not ragflow.settings.rag_mock_mode
            ):
                allowed, reason = tool_policy.authorize_egress(
                    tool_name, ragflow.settings.rag_base_url
                )
            elif tool_name == "create_feedback_draft" and llm.settings.llm_base_url:
                allowed, reason = tool_policy.authorize_egress(
                    tool_name, llm.settings.llm_base_url
                )
        await harness.event(
            state["run_id"],
            event_type="policy",
            name=f"tool_policy.{tool_name}",
            status="allowed" if allowed else "denied",
            payload={
                "reason": reason,
                "risk": (
                    tool_policy.contracts[tool_name].risk
                    if tool_name in tool_policy.contracts
                    else "unknown"
                ),
                "calls_so_far": calls,
            },
        )
        return allowed, reason, calls

    def denied_result(calls: int, reason: str) -> dict[str, Any]:
        return {
            "tool_call_count": calls,
            "termination_reason": reason,
            "error": reason,
            "final_answer": f"执行 Harness 已安全终止本次运行：{reason}。",
        }

    async def classify_intent(state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        intent, route = await llm.classify_intent_with_metadata(
            state["query"],
            context_summary=(state.get("context_snapshot") or {}).get("summary", ""),
        )
        async with AsyncSessionFactory() as db:
            await RunRepository.update(db, run_id=uuid.UUID(state["run_id"]), intent=intent)
        model_call_skipped = bool(route.get("model_call_skipped"))
        await harness.event(
            state["run_id"],
            event_type="policy" if model_call_skipped else "span",
            name=(
                "feedback_target.preflight"
                if model_call_skipped
                else "llm.classify_intent"
            ),
            duration_ms=int((time.perf_counter() - started) * 1000), payload=route,
        )
        return {"intent": intent, "model_routes": [*(state.get("model_routes") or []), route]}

    async def knowledge_tool(state: AgentState) -> dict[str, Any]:
        allowed, reason, calls = await authorize_tool(state, "knowledge_search")
        if not allowed:
            return denied_result(calls, reason)
        tool_input = {"query": state["query"]}
        started = time.perf_counter()
        try:
            contract = tool_policy.contract("knowledge_search")
            async with asyncio.timeout(contract.timeout_seconds):
                result = await ragflow.chat(state["query"], state["ragflow_session_id"])
            citations = _citations_from_reference(
                result.get("reference") or {},
                max_citations=settings.evidence_max_citations,
                max_content_chars=min(1000, contract.max_output_chars),
            )
            answer, truncated = tool_policy.truncate_text(
                str(result.get("answer", "")), contract.max_output_chars
            )
            output = {"answer": answer, "citations": citations, "truncated": truncated}
            async with AsyncSessionFactory() as db:
                await ToolCallRepository.add(
                    db,
                    run_id=uuid.UUID(state["run_id"]),
                    tool_name="knowledge_search",
                    input_payload=tool_input,
                    output_payload=output,
                    status="success",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            await harness.event(
                state["run_id"], event_type="span", name="tool.knowledge_search",
                duration_ms=int((time.perf_counter() - started) * 1000),
                payload={"citation_count": len(citations), "output_truncated": truncated},
            )
            return {
                "candidate_answer": output["answer"],
                "citations": citations,
                "tool_call_count": calls + 1,
            }
        except Exception as exc:
            async with AsyncSessionFactory() as db:
                await ToolCallRepository.add(
                    db,
                    run_id=uuid.UUID(state["run_id"]),
                    tool_name="knowledge_search",
                    input_payload=tool_input,
                    output_payload=None,
                    status="failed",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    error_message=audit_error(exc),
                )
            await harness.event(
                state["run_id"], event_type="span", name="tool.knowledge_search", status="failed",
                duration_ms=int((time.perf_counter() - started) * 1000),
                payload={"error_type": type(exc).__name__},
            )
            return {
                "error": audit_error(exc),
                "final_answer": "知识库查询失败，请稍后重试。",
                "tool_call_count": calls + 1,
            }

    async def compare_documents_tool(state: AgentState) -> dict[str, Any]:
        allowed, reason, calls = await authorize_tool(state, "compare_documents")
        if not allowed:
            return denied_result(calls, reason)
        document_a = state.get("document_a") or "文档A"
        document_b = state.get("document_b") or "文档B"
        prompt = (
            f"用户问题：{state['query']}\n"
            f"请只基于知识库比较《{document_a}》和《{document_b}》。"
            "按新增、删除、修改、未变化四类输出；每项给出原文依据，未找到依据时明确说明。"
        )
        tool_input = {"query": state["query"], "document_a": document_a, "document_b": document_b}
        started = time.perf_counter()
        try:
            contract = tool_policy.contract("compare_documents")
            async with asyncio.timeout(contract.timeout_seconds):
                result = await ragflow.chat(prompt, state["ragflow_session_id"])
            citations = _citations_from_reference(
                result.get("reference") or {}, max_citations=settings.evidence_max_citations,
                max_content_chars=min(1000, contract.max_output_chars),
            )
            answer, truncated = tool_policy.truncate_text(
                str(result.get("answer", "")), contract.max_output_chars
            )
            output = {"answer": answer, "citations": citations, "truncated": truncated}
            async with AsyncSessionFactory() as db:
                await ToolCallRepository.add(
                    db,
                    run_id=uuid.UUID(state["run_id"]),
                    tool_name="compare_documents",
                    input_payload=tool_input,
                    output_payload=output,
                    status="success",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            await harness.event(
                state["run_id"], event_type="span", name="tool.compare_documents",
                duration_ms=int((time.perf_counter() - started) * 1000),
                payload={"citation_count": len(citations), "output_truncated": truncated},
            )
            return {
                "candidate_answer": output["answer"],
                "citations": citations,
                "tool_call_count": calls + 1,
            }
        except Exception as exc:
            async with AsyncSessionFactory() as db:
                await ToolCallRepository.add(
                    db,
                    run_id=uuid.UUID(state["run_id"]),
                    tool_name="compare_documents",
                    input_payload=tool_input,
                    output_payload=None,
                    status="failed",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    error_message=audit_error(exc),
                )
            return {
                "error": audit_error(exc),
                "final_answer": "文档对比失败，请检查文档名称后重试。",
                "tool_call_count": calls + 1,
            }

    async def verify_evidence(state: AgentState) -> dict[str, Any]:
        allowed, reason, calls = await authorize_tool(state, "evidence_guard")
        if not allowed:
            return denied_result(calls, reason)
        started = time.perf_counter()
        task = state["intent"]
        if task not in {"knowledge_qa", "document_compare"}:
            return {"error": "证据门禁收到不支持的任务类型", "final_answer": "证据校验失败。"}
        decision = evidence_guard.evaluate(
            query=state["query"],
            candidate_answer=state.get("candidate_answer") or "",
            citations=state.get("citations") or [],
            task=task,
        )
        passport = decision["passport"]
        async with AsyncSessionFactory() as db:
            await ToolCallRepository.add(
                db,
                run_id=uuid.UUID(state["run_id"]),
                tool_name="evidence_guard",
                input_payload={
                    "task": task,
                    "citation_count": len(state.get("citations") or []),
                    "policy_version": passport["policy_version"],
                },
                output_payload=passport,
                status="success" if passport["verdict"] == "grounded" else "guarded",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        return {
            "final_answer": decision["answer"],
            "citations": decision["citations"],
            "evidence_passport": passport,
            "tool_call_count": calls + 1,
        }

    async def prepare_feedback_tool(state: AgentState) -> dict[str, Any]:
        allowed, reason, calls = await authorize_tool(state, "create_feedback_draft")
        if not allowed:
            return denied_result(calls, reason)
        started = time.perf_counter()
        source_gate = llm.precheck_feedback_target(state["query"])
        target_resolved = source_gate.get("target_status") == "resolved"
        await harness.event(
            state["run_id"],
            event_type="policy",
            name="feedback_target.precheck",
            status="allowed" if target_resolved else "needs_input",
            payload={
                "target_status": source_gate.get("target_status"),
                "model_call_skipped": not target_resolved,
                "reason": (
                    "specific_target_found"
                    if target_resolved
                    else "feedback_target_unresolved"
                ),
            },
        )
        if not target_resolved:
            async with AsyncSessionFactory() as db:
                await ToolCallRepository.add(
                    db,
                    run_id=uuid.UUID(state["run_id"]),
                    tool_name="create_feedback_draft",
                    input_payload={"query": state["query"]},
                    output_payload=source_gate,
                    status="needs_input",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            return {
                "feedback_draft": source_gate,
                "feedback_target_resolved": False,
                "tool_call_count": calls + 1,
                "final_answer": (
                    "请具体说明知识库中哪一条知识存在问题，例如制度名称、条款、"
                    "流程节点或食品安全知识点；定位目标后我再生成反馈草稿供你确认。"
                ),
            }
        contract = tool_policy.contract("create_feedback_draft")
        async with asyncio.timeout(contract.timeout_seconds):
            draft, route = await llm.make_feedback_draft_with_metadata(state["query"])
        async with AsyncSessionFactory() as db:
            await ToolCallRepository.add(
                db,
                run_id=uuid.UUID(state["run_id"]),
                tool_name="create_feedback_draft",
                input_payload={"query": state["query"]},
                output_payload=draft,
                status="success" if target_resolved else "needs_input",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        target_resolved = draft.get("target_status") == "resolved"
        if not target_resolved:
            return {
                "feedback_draft": draft,
                "feedback_target_resolved": False,
                "tool_call_count": calls + 1,
                "model_routes": [*(state.get("model_routes") or []), route],
                "final_answer": (
                    "请具体说明知识库中哪一条知识存在问题，例如制度名称、条款、"
                    "流程节点或食品安全知识点；定位目标后我再生成反馈草稿供你确认。"
                ),
            }
        await harness.event(
            state["run_id"], event_type="span", name="llm.feedback_draft",
            duration_ms=int((time.perf_counter() - started) * 1000), payload=route,
        )
        return {
            "feedback_draft": draft, "feedback_target_resolved": True,
            "tool_call_count": calls + 1,
            "model_routes": [*(state.get("model_routes") or []), route],
        }

    async def mark_pending_approval(state: AgentState) -> dict[str, Any]:
        payload = {
            "action": "submit_knowledge_feedback",
            "risk_level": "high",
            "draft": state["feedback_draft"],
            "message": "该操作会提交知识库问题反馈，需要人工确认。",
        }
        async with AsyncSessionFactory() as db:
            await RunRepository.update(
                db,
                run_id=uuid.UUID(state["run_id"]),
                status="pending_approval",
                pending_approval=payload,
            )
        return {"pending_approval": payload}

    def approval_node(state: AgentState) -> dict[str, Any]:
        decision = interrupt(state["pending_approval"])
        if isinstance(decision, dict):
            approved = bool(decision.get("approved"))
            comment = decision.get("comment")
        else:
            approved = bool(decision)
            comment = None
        return {"approved": approved, "approval_comment": comment, "pending_approval": {}}

    async def submit_feedback_tool(state: AgentState) -> dict[str, Any]:
        allowed, reason, calls = await authorize_tool(
            state, "submit_feedback", approved=bool(state.get("approved"))
        )
        if not allowed:
            return denied_result(calls, reason)
        draft = state["feedback_draft"]
        started = time.perf_counter()
        try:
            async with AsyncSessionFactory() as db:
                feedback = await KnowledgeFeedbackRepository.create(
                    db,
                    user_id=uuid.UUID(state["user_id"]),
                    session_id=uuid.UUID(state["session_id"]),
                    knowledge_item=draft["knowledge_item"],
                    issue_type=draft["issue_type"],
                    description=draft["description"],
                    priority=draft["priority"],
                )
                output = {"feedback_id": str(feedback.id), "status": feedback.status}
                await ToolCallRepository.add(
                    db,
                    run_id=uuid.UUID(state["run_id"]),
                    tool_name="submit_feedback",
                    input_payload=draft,
                    output_payload=output,
                    status="success",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            return {
                "final_answer": (
                    f"知识库问题反馈已提交，编号：{feedback.id}，"
                    f"问题类型：{draft['issue_type']}，优先级：{draft['priority']}。"
                ),
                "tool_call_count": calls + 1,
            }
        except Exception as exc:
            async with AsyncSessionFactory() as db:
                await ToolCallRepository.add(
                    db,
                    run_id=uuid.UUID(state["run_id"]),
                    tool_name="submit_feedback",
                    input_payload=draft,
                    output_payload=None,
                    status="failed",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    error_message=audit_error(exc),
                )
            return {
                "error": audit_error(exc),
                "final_answer": "知识库问题反馈提交失败，请稍后重试。",
                "tool_call_count": calls + 1,
            }

    async def rejected_feedback(state: AgentState) -> dict[str, Any]:
        comment = state.get("approval_comment")
        suffix = f"原因：{comment}" if comment else ""
        return {"final_answer": f"已取消提交知识库问题反馈。{suffix}"}

    async def unsupported(state: AgentState) -> dict[str, Any]:
        return {
            "final_answer": "当前 Agent 仅支持企业知识问答、多文档对比和知识库问题反馈。",
            "citations": [],
        }

    async def finalize(state: AgentState) -> dict[str, Any]:
        async with AsyncSessionFactory() as db:
            await RunRepository.update(
                db,
                run_id=uuid.UUID(state["run_id"]),
                status="failed" if state.get("error") else "completed",
                final_answer=state.get("final_answer") or "",
                citations=state.get("citations") or [],
                pending_approval=None,
                error_message=state.get("error"),
                finished=True,
            )
        await harness.event(
            state["run_id"], event_type="termination", name="agent.terminated",
            status="failed" if state.get("error") else "success",
            payload={
                "reason": state.get("termination_reason") or (
                    "error" if state.get("error") else "completed"
                ),
                "tool_call_count": int(state.get("tool_call_count") or 0),
            },
            parent_name=None,
        )
        return {}

    def route_intent(state: AgentState) -> str:
        return state["intent"]

    def route_approval(state: AgentState) -> str:
        return "approved" if state.get("approved") else "rejected"

    def route_after_retrieval(state: AgentState) -> str:
        return "failed" if state.get("error") else "verify"

    def route_feedback_draft(state: AgentState) -> str:
        if state.get("error"):
            return "failed"
        return "approval" if state.get("feedback_target_resolved") else "clarify"

    builder = StateGraph(AgentState)
    builder.add_node("classify_intent", classify_intent)
    builder.add_node("knowledge_tool", knowledge_tool)
    builder.add_node("compare_documents_tool", compare_documents_tool)
    builder.add_node("verify_evidence", verify_evidence)
    builder.add_node("prepare_feedback_tool", prepare_feedback_tool)
    builder.add_node("mark_pending_approval", mark_pending_approval)
    builder.add_node("approval", approval_node)
    builder.add_node("submit_feedback_tool", submit_feedback_tool)
    builder.add_node("rejected_feedback", rejected_feedback)
    builder.add_node("unsupported", unsupported)
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "classify_intent")
    builder.add_conditional_edges(
        "classify_intent",
        route_intent,
        {
            "knowledge_qa": "knowledge_tool",
            "document_compare": "compare_documents_tool",
            "knowledge_feedback": "prepare_feedback_tool",
            "unsupported": "unsupported",
        },
    )
    builder.add_conditional_edges(
        "knowledge_tool",
        route_after_retrieval,
        {"verify": "verify_evidence", "failed": "finalize"},
    )
    builder.add_conditional_edges(
        "compare_documents_tool",
        route_after_retrieval,
        {"verify": "verify_evidence", "failed": "finalize"},
    )
    builder.add_edge("verify_evidence", "finalize")
    builder.add_edge("unsupported", "finalize")
    builder.add_conditional_edges(
        "prepare_feedback_tool",
        route_feedback_draft,
        {"approval": "mark_pending_approval", "clarify": "finalize", "failed": "finalize"},
    )
    builder.add_edge("mark_pending_approval", "approval")
    builder.add_conditional_edges(
        "approval",
        route_approval,
        {"approved": "submit_feedback_tool", "rejected": "rejected_feedback"},
    )
    builder.add_edge("submit_feedback_tool", "finalize")
    builder.add_edge("rejected_feedback", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)
