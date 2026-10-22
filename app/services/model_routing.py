from __future__ import annotations

import time
from dataclasses import dataclass

from app.core.config import Settings


@dataclass(frozen=True)
class ModelRoute:
    task: str
    selected_model: str
    fallback_models: tuple[str, ...]
    reason: str
    circuit_open: bool


class ModelRouter:
    """Risk-aware routing with a process-local circuit breaker and deterministic fallback."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._failures = 0
        self._opened_at: float | None = None

    def route(self, *, task: str, high_risk: bool = False) -> ModelRoute:
        circuit_open = self._is_open()
        router = self.settings.llm_router_model.strip() or self.settings.llm_model.strip()
        primary = self.settings.llm_primary_model.strip() or self.settings.llm_model.strip()
        high = self.settings.llm_high_risk_model.strip() or primary
        selected = high if high_risk else (router if task == "intent" else primary)
        reason = "high_risk_stronger_model" if high_risk else f"{task}_route"
        if not selected or circuit_open:
            selected = "deterministic-local-fallback"
            reason = "circuit_open" if circuit_open else "model_not_configured"
        fallbacks = tuple(
            item for item in (primary, "deterministic-local-fallback") if item and item != selected
        )
        return ModelRoute(task, selected, fallbacks, reason, circuit_open)

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.settings.llm_circuit_failure_threshold:
            self._opened_at = time.monotonic()

    def _is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self.settings.llm_circuit_cooldown_seconds:
            self._failures = 0
            self._opened_at = None
            return False
        return True

