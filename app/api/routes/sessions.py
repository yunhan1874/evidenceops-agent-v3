import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.core.config import Settings, get_settings
from app.core.errors import AppError, audit_error
from app.db.session import get_db
from app.repositories.core import RunRepository, SessionRepository
from app.schemas.agent import (
    DeleteRunResponse,
    RunHistoryItem,
    SessionHistoryResponse,
    ToolCallResponse,
)
from app.schemas.session import CreateSessionRequest, SessionResponse
from app.services.evidence import extract_evidence_passport

router = APIRouter(prefix="/sessions", tags=["sessions"])
logger = logging.getLogger(__name__)


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateSessionRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SessionResponse:
    try:
        remote = await request.app.state.ragflow_client.create_session(
            body.title, current_user.external_user_id
        )
    except Exception as exc:
        logger.exception("RAGFlow session creation failed request_id=%s", request.state.request_id)
        message = exc.public_message if isinstance(exc, AppError) else "知识库会话创建失败"
        raise HTTPException(
            status_code=502,
            detail={
                "code": audit_error(exc),
                "message": message,
                "request_id": request.state.request_id,
            },
        ) from exc
    item = await SessionRepository.create(
        db,
        user_id=uuid.UUID(current_user.id),
        title=body.title,
        ragflow_chat_id=settings.rag_chat_id or "mock-chat",
        ragflow_session_id=str(remote["id"]),
    )
    return SessionResponse.model_validate(item)


@router.get("", response_model=list[SessionResponse])
async def list_sessions(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[SessionResponse]:
    items = await SessionRepository.list_owned(db, user_id=uuid.UUID(current_user.id))
    return [SessionResponse.model_validate(item) for item in items]


@router.get("/{session_id}/history", response_model=SessionHistoryResponse)
async def get_session_history(
    session_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionHistoryResponse:
    user_id = uuid.UUID(current_user.id)
    session = await SessionRepository.get_owned(db, session_id=session_id, user_id=user_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")
    runs, total = await RunRepository.list_owned_by_session(
        db, session_id=session_id, user_id=user_id, page=page, page_size=page_size
    )
    return SessionHistoryResponse(
        session_id=session_id,
        total=total,
        page=page,
        page_size=page_size,
        items=[_to_history_item(run) for run in runs],
    )


@router.delete("/{session_id}/messages/{run_id}", response_model=DeleteRunResponse)
async def delete_session_message(
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DeleteRunResponse:
    user_id = uuid.UUID(current_user.id)
    session = await SessionRepository.get_owned(db, session_id=session_id, user_id=user_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")
    run = await RunRepository.soft_delete_owned(
        db, run_id=run_id, session_id=session_id, user_id=user_id
    )
    if run is None:
        raise HTTPException(status_code=404, detail="消息不存在或无权访问")
    return DeleteRunResponse(
        run_id=run.id,
        session_id=run.session_id,
        status=run.status,
        message="消息删除成功",
    )


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    item = await SessionRepository.get_owned(
        db, session_id=session_id, user_id=uuid.UUID(current_user.id)
    )
    if item is None:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")
    try:
        await request.app.state.ragflow_client.delete_session(item.ragflow_session_id)
    except Exception as exc:
        logger.exception(
            "RAGFlow session deletion failed session_id=%s request_id=%s",
            session_id,
            request.state.request_id,
        )
        message = exc.public_message if isinstance(exc, AppError) else "知识库会话删除失败"
        raise HTTPException(
            status_code=502,
            detail={
                "code": audit_error(exc),
                "message": message,
                "request_id": request.state.request_id,
            },
        ) from exc
    item.status = "deleted"
    await db.commit()


def _to_history_item(run) -> RunHistoryItem:
    return RunHistoryItem(
        run_id=run.id,
        session_id=run.session_id,
        query=run.query,
        status=run.status,
        intent=run.intent,
        answer=run.final_answer,
        citations=run.citations or [],
        evidence_passport=extract_evidence_passport(run.tool_calls),
        pending_approval=run.pending_approval,
        error_message=run.error_message,
        created_at=run.created_at,
        finished_at=run.finished_at,
        tool_calls=[ToolCallResponse.model_validate(item) for item in run.tool_calls],
    )
