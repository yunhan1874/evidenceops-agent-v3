from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import AgentRun, AgentSession, KnowledgeFeedback, RunEvent, ToolCall, User

_UNSET = object()


class UserRepository:
    @staticmethod
    async def upsert(
        db: AsyncSession,
        *,
        external_user_id: str,
        display_name: str,
        role: str = "user",
    ) -> User:
        result = await db.execute(select(User).where(User.external_user_id == external_user_id))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                external_user_id=external_user_id,
                display_name=display_name,
                role=role,
                active=True,
            )
            db.add(user)
        else:
            user.display_name = display_name
            if role == "admin":
                user.role = "admin"
        await db.commit()
        await db.refresh(user)
        return user


class SessionRepository:
    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        title: str,
        ragflow_chat_id: str,
        ragflow_session_id: str,
    ) -> AgentSession:
        item = AgentSession(
            user_id=user_id,
            title=title,
            ragflow_chat_id=ragflow_chat_id,
            ragflow_session_id=ragflow_session_id,
        )
        db.add(item)
        await db.commit()
        await db.refresh(item)
        return item

    @staticmethod
    async def get_owned(
        db: AsyncSession, *, session_id: uuid.UUID, user_id: uuid.UUID
    ) -> AgentSession | None:
        result = await db.execute(
            select(AgentSession).where(
                AgentSession.id == session_id,
                AgentSession.user_id == user_id,
                AgentSession.status == "active",
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_owned(db: AsyncSession, *, user_id: uuid.UUID) -> list[AgentSession]:
        result = await db.execute(
            select(AgentSession)
            .where(AgentSession.user_id == user_id, AgentSession.status == "active")
            .order_by(AgentSession.updated_at.desc())
        )
        return list(result.scalars().all())


class RunRepository:
    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        run_id: uuid.UUID,
        request_id: str,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        query: str,
        idempotency_key_hash: str | None = None,
        request_fingerprint: str | None = None,
    ) -> AgentRun:
        item = AgentRun(
            id=run_id,
            request_id=request_id,
            session_id=session_id,
            user_id=user_id,
            query=query,
            idempotency_key_hash=idempotency_key_hash,
            request_fingerprint=request_fingerprint,
            status="running",
        )
        db.add(item)
        await db.commit()
        await db.refresh(item)
        return item

    @staticmethod
    async def get_owned(
        db: AsyncSession,
        *,
        run_id: uuid.UUID,
        user_id: uuid.UUID,
        with_tools: bool = True,
        refresh: bool = False,
        include_deleted: bool = False,
    ) -> AgentRun | None:
        statement = select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user_id)
        if not include_deleted:
            statement = statement.where(AgentRun.status != "deleted")
        if with_tools:
            statement = statement.options(
                selectinload(AgentRun.tool_calls), selectinload(AgentRun.events)
            )
        if refresh:
            statement = statement.execution_options(populate_existing=True)
        result = await db.execute(statement)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_idempotency_key_owned(
        db: AsyncSession, *, idempotency_key_hash: str, user_id: uuid.UUID
    ) -> AgentRun | None:
        result = await db.execute(
            select(AgentRun)
            .where(
                AgentRun.idempotency_key_hash == idempotency_key_hash,
                AgentRun.user_id == user_id,
            )
            .options(selectinload(AgentRun.tool_calls), selectinload(AgentRun.events))
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_owned_by_session(
        db: AsyncSession,
        *,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        page: int,
        page_size: int,
    ) -> tuple[list[AgentRun], int]:
        filters = (
            AgentRun.session_id == session_id,
            AgentRun.user_id == user_id,
            AgentRun.status != "deleted",
        )
        total_result = await db.execute(select(func.count()).select_from(AgentRun).where(*filters))
        total = int(total_result.scalar_one())
        result = await db.execute(
            select(AgentRun)
            .where(*filters)
            .options(selectinload(AgentRun.tool_calls))
            .order_by(AgentRun.created_at.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    @staticmethod
    async def recent_context(
        db: AsyncSession,
        *,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        limit: int,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        result = await db.execute(
            select(AgentRun.query, AgentRun.final_answer)
            .where(
                AgentRun.session_id == session_id,
                AgentRun.user_id == user_id,
                AgentRun.status == "completed",
            )
            .order_by(AgentRun.created_at.desc())
            .limit(limit)
        )
        rows = list(reversed(result.all()))
        return [{"query": row.query, "answer": row.final_answer or ""} for row in rows]

    @staticmethod
    async def soft_delete_owned(
        db: AsyncSession,
        *,
        run_id: uuid.UUID,
        user_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
    ) -> AgentRun | None:
        statement = select(AgentRun).where(
            AgentRun.id == run_id,
            AgentRun.user_id == user_id,
            AgentRun.status != "deleted",
        )
        if session_id is not None:
            statement = statement.where(AgentRun.session_id == session_id)
        result = await db.execute(statement)
        item = result.scalar_one_or_none()
        if item is None:
            return None
        item.status = "deleted"
        item.pending_approval = None
        await db.commit()
        await db.refresh(item)
        return item

    @staticmethod
    async def claim_pending_approval(
        db: AsyncSession, *, run_id: uuid.UUID, user_id: uuid.UUID
    ) -> bool:
        """Atomically grant one caller the right to resume a side-effecting run.

        A read-then-write check is not sufficient here: two workers can both read
        ``pending_approval`` and submit the same feedback. The conditional UPDATE
        is the compare-and-set boundary and succeeds for exactly one caller.
        """

        result = await db.execute(
            sql_update(AgentRun)
            .where(
                AgentRun.id == run_id,
                AgentRun.user_id == user_id,
                AgentRun.status == "pending_approval",
            )
            .values(status="approval_processing")
            .returning(AgentRun.id)
        )
        claimed = result.scalar_one_or_none() is not None
        await db.commit()
        return claimed

    @staticmethod
    async def update(
        db: AsyncSession,
        *,
        run_id: uuid.UUID,
        intent: str | None = None,
        status: str | None = None,
        final_answer: str | None = None,
        citations: list[dict[str, Any]] | None = None,
        pending_approval: dict[str, Any] | None | object = _UNSET,
        error_message: str | None | object = _UNSET,
        latency_ms: int | None = None,
        finished: bool = False,
    ) -> None:
        result = await db.execute(select(AgentRun).where(AgentRun.id == run_id))
        item = result.scalar_one()
        if intent is not None:
            item.intent = intent
        if status is not None:
            item.status = status
        if final_answer is not None:
            item.final_answer = final_answer
        if citations is not None:
            item.citations = citations
        if pending_approval is not _UNSET:
            item.pending_approval = pending_approval  # type: ignore[assignment]
        if error_message is not _UNSET:
            item.error_message = error_message  # type: ignore[assignment]
        if latency_ms is not None:
            item.latency_ms = latency_ms
        if finished:
            item.finished_at = datetime.now(UTC)
        await db.commit()


class ToolCallRepository:
    @staticmethod
    async def add(
        db: AsyncSession,
        *,
        run_id: uuid.UUID,
        tool_name: str,
        input_payload: dict[str, Any],
        output_payload: dict[str, Any] | None,
        status: str,
        latency_ms: int | None,
        error_message: str | None = None,
    ) -> None:
        db.add(
            ToolCall(
                run_id=run_id,
                tool_name=tool_name,
                input_payload=input_payload,
                output_payload=output_payload,
                status=status,
                latency_ms=latency_ms,
                error_message=error_message,
            )
        )
        await db.commit()


class RunEventRepository:
    @staticmethod
    async def add(
        db: AsyncSession,
        *,
        run_id: uuid.UUID,
        event_type: str,
        name: str,
        status: str,
        duration_ms: int | None,
        payload: dict[str, Any],
        parent_name: str | None = "agent.run",
    ) -> None:
        db.add(
            RunEvent(
                run_id=run_id,
                event_type=event_type,
                name=name,
                parent_name=parent_name,
                status=status,
                duration_ms=duration_ms,
                payload=payload,
            )
        )
        await db.commit()

    @staticmethod
    async def list_for_run(db: AsyncSession, *, run_id: uuid.UUID) -> list[RunEvent]:
        result = await db.execute(
            select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.created_at.asc())
        )
        return list(result.scalars().all())


class KnowledgeFeedbackRepository:
    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        knowledge_item: str,
        issue_type: str,
        description: str,
        priority: str,
    ) -> KnowledgeFeedback:
        item = KnowledgeFeedback(
            user_id=user_id,
            session_id=session_id,
            knowledge_item=knowledge_item,
            issue_type=issue_type,
            description=description,
            priority=priority,
            status="submitted",
        )
        db.add(item)
        await db.commit()
        await db.refresh(item)
        return item
