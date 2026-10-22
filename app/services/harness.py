from __future__ import annotations

import math
import uuid
from typing import Any

from app.core.config import Settings
from app.core.versions import (
    APP_VERSION,
    DATASET_VERSION,
    POLICY_VERSION,
    PROMPT_VERSION,
    TOOL_SCHEMA_VERSION,
)
from app.db.session import AsyncSessionFactory
from app.repositories.core import RunEventRepository


class HarnessService:
    """Persists the minimum execution harness needed to replay and explain a run."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def budget_snapshot(self, query: str) -> dict[str, Any]:
        estimated_input_tokens = math.ceil(len(query) / 4)
        return {
            "max_steps": self.settings.max_agent_steps,
            "max_tool_calls": self.settings.max_tool_calls,
            "max_run_seconds": self.settings.max_run_seconds,
            "max_llm_estimated_tokens": self.settings.max_llm_estimated_tokens,
            "estimated_input_tokens": estimated_input_tokens,
            "within_token_budget": estimated_input_tokens <= self.settings.max_llm_estimated_tokens,
        }

    def version_fingerprint(self) -> dict[str, str]:
        return {
            "app_version": APP_VERSION,
            "prompt_version": PROMPT_VERSION,
            "tool_schema_version": TOOL_SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "dataset_version": DATASET_VERSION,
        }

    async def event(
        self,
        run_id: str,
        *,
        event_type: str,
        name: str,
        status: str = "success",
        duration_ms: int | None = None,
        payload: dict[str, Any] | None = None,
        parent_name: str | None = "agent.run",
    ) -> None:
        async with AsyncSessionFactory() as db:
            await RunEventRepository.add(
                db,
                run_id=uuid.UUID(run_id),
                event_type=event_type,
                name=name,
                status=status,
                duration_ms=duration_ms,
                payload=payload or {},
                parent_name=parent_name,
            )

