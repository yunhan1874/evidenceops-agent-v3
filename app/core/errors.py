from __future__ import annotations


class AppError(RuntimeError):
    """An operational error with a stable, non-sensitive contract."""

    def __init__(self, code: str, public_message: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.public_message = public_message
        self.retryable = retryable


def audit_error(exc: Exception) -> str:
    """Keep API/database traces useful without persisting credentials or raw URLs."""

    if isinstance(exc, AppError):
        return exc.code
    return type(exc).__name__
