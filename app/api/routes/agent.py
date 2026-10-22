from __future__ import annotations

import asyncio
import logging
import time
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from langgraph.errors import GraphRecursionError
from langgraph.types import Command
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.core.errors import audit_error
from app.db.session import get_db
from app.repositories.core import RunRepository, SessionRepository
from app.schemas.agent import (
    CreateRunRequest,
    DeleteRunResponse,
    HarnessSummary,
    ResumeRunRequest,
    RunEventResponse,
    RunResponse,
    ToolCallResponse,
)
from app.services.context_manager import ContextManager
from app.services.evidence import extract_evidence_passport
from app.services.request_keys import hash_idempotency_key, request_fingerprint

router = APIRouter(prefix="/agent", tags=["agent"])
logger = logging.getLogger(__name__)


@router.post("/runs", response_model=RunResponse)
async def create_run(
    body: CreateRunRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RunResponse:
    session = await SessionRepository.get_owned(
        db, session_id=body.session_id, user_id=uuid.UUID(current_user.id)
    )
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")

    run_id = uuid.uuid4()
    trace_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    key_hash = None
    fingerprint = None
    try:
        if idempotency_key is not None:
            key_hash = hash_idempotency_key(user_id=current_user.id, key=idempotency_key)
            fingerprint = request_fingerprint(
                session.id,
                body.query,
                body.document_a,
                body.document_b,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if key_hash is not None:
        existing = await RunRepository.get_by_idempotency_key_owned(
            db, idempotency_key_hash=key_hash, user_id=uuid.UUID(current_user.id)
        )
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise HTTPException(status_code=409, detail="幂等键已用于不同的请求内容")
            return _run_to_response(existing)

    try:
        await RunRepository.create(
            db,
            run_id=run_id,
            request_id=f"{trace_id}:{run_id}",
            session_id=session.id,
            user_id=uuid.UUID(current_user.id),
            query=body.query,
            idempotency_key_hash=key_hash,
            request_fingerprint=fingerprint,
        )
    except IntegrityError as exc:
        await db.rollback()
        if key_hash is None:
            raise
        existing = await RunRepository.get_by_idempotency_key_owned(
            db, idempotency_key_hash=key_hash, user_id=uuid.UUID(current_user.id)
        )
        if existing is None or existing.request_fingerprint != fingerprint:
            raise HTTPException(status_code=409, detail="幂等键冲突") from exc
        return _run_to_response(existing)

    history = await RunRepository.recent_context(
        db,
        session_id=session.id,
        user_id=uuid.UUID(current_user.id),
        limit=request.app.state.harness.settings.context_max_turns,
    )
    context = ContextManager(
        max_chars=request.app.state.harness.settings.context_max_chars,
        max_turns=request.app.state.harness.settings.context_max_turns,
    ).prepare(body.query, history)

    state = {
        "request_id": trace_id,
        "run_id": str(run_id),
        "user_id": current_user.id,
        "session_id": str(session.id),
        "ragflow_session_id": session.ragflow_session_id,
        "query": body.query,
        "document_a": body.document_a,
        "document_b": body.document_b,
        "citations": [],
        "user_role": current_user.role,
        "tool_call_count": 0,
        "context_snapshot": context,
        "model_routes": [],
    }
    settings = request.app.state.harness.settings
    budget = request.app.state.harness.budget_snapshot(body.query)
    await request.app.state.harness.event(
        str(run_id), event_type="lifecycle", name="agent.run_started",
        payload={"versions": request.app.state.harness.version_fingerprint(), "budget": budget},
        parent_name=None,
    )
    await request.app.state.harness.event(
        str(run_id), event_type="context", name="context.prepared", payload=context,
    )
    if not budget["within_token_budget"]:
        await RunRepository.update(
            db, run_id=run_id, status="failed", error_message="llm_token_budget_exceeded",
            final_answer="输入超过本次运行的模型 Token 预算。", finished=True,
        )
        await request.app.state.harness.event(
            str(run_id), event_type="termination", name="agent.terminated", status="failed",
            payload={"reason": "llm_token_budget_exceeded", "tool_call_count": 0},
            parent_name=None,
        )
        return await _load_response(db, run_id, uuid.UUID(current_user.id), refresh=True)

    config = {
        "configurable": {"thread_id": str(run_id)},
        "recursion_limit": settings.max_agent_steps,
    }
    started = time.perf_counter()
    try:
        async with asyncio.timeout(settings.max_run_seconds):
            result = await request.app.state.agent_graph.ainvoke(state, config=config)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        await RunRepository.update(db, run_id=run_id, latency_ms=elapsed_ms)
        await request.app.state.harness.event(
            str(run_id), event_type="span", name="http.agent_run",
            duration_ms=elapsed_ms,
            payload={"interrupted": bool(result.get("__interrupt__"))}, parent_name=None,
        )
    except (TimeoutError, GraphRecursionError) as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        reason = "run_timeout" if isinstance(exc, TimeoutError) else "step_budget_exceeded"
        await RunRepository.update(
            db, run_id=run_id, status="failed", error_message=reason,
            final_answer=f"执行 Harness 已安全终止本次运行：{reason}。",
            latency_ms=elapsed_ms, finished=True,
        )
        await request.app.state.harness.event(
            str(run_id), event_type="termination", name="agent.terminated", status="failed",
            duration_ms=elapsed_ms, payload={"reason": reason}, parent_name=None,
        )
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.exception("Agent execution failed run_id=%s request_id=%s", run_id, trace_id)
        await RunRepository.update(
            db,
            run_id=run_id,
            status="failed",
            error_message=audit_error(exc),
            latency_ms=elapsed_ms,
            finished=True,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "AGENT_EXECUTION_FAILED",
                "message": "Agent 执行失败，请使用 request_id 联系管理员排查。",
                "request_id": trace_id,
            },
        ) from exc

    return await _load_response(db, run_id, uuid.UUID(current_user.id))


@router.post("/runs/{run_id}/resume", response_model=RunResponse)
async def resume_run(
    run_id: uuid.UUID,
    body: ResumeRunRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RunResponse:
    run = await RunRepository.get_owned(db, run_id=run_id, user_id=uuid.UUID(current_user.id))
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在或无权访问")
    if run.status != "pending_approval":
        raise HTTPException(status_code=409, detail="当前运行不处于待审批状态")

    claimed = await RunRepository.claim_pending_approval(
        db, run_id=run_id, user_id=uuid.UUID(current_user.id)
    )
    if not claimed:
        raise HTTPException(status_code=409, detail="审批已被其他请求处理或正在处理中")

    settings = request.app.state.harness.settings
    config = {
        "configurable": {"thread_id": str(run_id)},
        "recursion_limit": settings.max_agent_steps,
    }
    try:
        async with asyncio.timeout(settings.max_run_seconds):
            await request.app.state.agent_graph.ainvoke(
                Command(resume={"approved": body.approved, "comment": body.comment}),
                config=config,
            )
    except Exception as exc:
        logger.exception("Agent resume failed run_id=%s", run_id)
        await RunRepository.update(
            db,
            run_id=run_id,
            # A failure after claiming the approval has an uncertain side-effect
            # boundary. Do not make it automatically retryable and risk duplicates.
            status="recovery_required",
            error_message=audit_error(exc),
            finished=True,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "AGENT_RESUME_REQUIRES_RECONCILIATION",
                "message": "审批恢复失败，系统已阻止自动重试，请联系管理员核对副作用状态。",
                "request_id": getattr(request.state, "request_id", None),
            },
        ) from exc
    return await _load_response(db, run_id, uuid.UUID(current_user.id), refresh=True)


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RunResponse:
    return await _load_response(db, run_id, uuid.UUID(current_user.id))


@router.get("/runs/{run_id}/harness", response_model=HarnessSummary)
async def get_run_harness(
    run_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HarnessSummary:
    run = await RunRepository.get_owned(db, run_id=run_id, user_id=uuid.UUID(current_user.id))
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在或无权访问")
    return _harness_summary(run.events)


@router.get("/tool-contracts")
async def get_tool_contracts(
    request: Request,
    _: CurrentUser = Depends(get_current_user),
) -> dict[str, object]:
    return {"deny_by_default": True, "contracts": request.app.state.tool_policy.manifest()}


@router.delete("/runs/{run_id}", response_model=DeleteRunResponse)
async def delete_run(
    run_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DeleteRunResponse:
    run = await RunRepository.soft_delete_owned(
        db, run_id=run_id, user_id=uuid.UUID(current_user.id)
    )
    if run is None:
        raise HTTPException(status_code=404, detail="消息不存在或无权访问")
    return DeleteRunResponse(
        run_id=run.id,
        session_id=run.session_id,
        status=run.status,
        message="消息删除成功",
    )


async def _load_response(
    db: AsyncSession, run_id: uuid.UUID, user_id: uuid.UUID, *, refresh: bool = False
) -> RunResponse:
    run = await RunRepository.get_owned(
        db, run_id=run_id, user_id=user_id, with_tools=True, refresh=refresh
    )
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在或无权访问")
    return _run_to_response(run)


def _run_to_response(run) -> RunResponse:
    return RunResponse(
        run_id=run.id,
        session_id=run.session_id,
        status=run.status,
        intent=run.intent,
        answer=run.final_answer,
        citations=run.citations or [],
        evidence_passport=extract_evidence_passport(run.tool_calls),
        pending_approval=run.pending_approval,
        error_message=run.error_message,
        tool_calls=[ToolCallResponse.model_validate(item) for item in run.tool_calls],
        harness=_harness_summary(run.events),
    )


def _harness_summary(events) -> HarnessSummary:
    versions: dict[str, str] = {}
    budget: dict[str, object] = {}
    context: dict[str, object] = {}
    model_routes: list[dict[str, object]] = []
    termination_reason = None
    for event in events:
        if event.name == "agent.run_started":
            versions = event.payload.get("versions") or {}
            budget = event.payload.get("budget") or {}
        elif event.name == "context.prepared":
            context = event.payload
        elif event.name.startswith("llm."):
            model_routes.append(event.payload)
        elif event.event_type == "termination":
            termination_reason = event.payload.get("reason")
    return HarnessSummary(
        versions=versions,
        budget=budget,
        context=context,
        model_routes=model_routes,
        termination_reason=termination_reason,
        events=[RunEventResponse.model_validate(item) for item in events],
    )
