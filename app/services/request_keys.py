from __future__ import annotations

import hashlib
import re
import uuid

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def normalize_request_id(value: str | None) -> str:
    """Accept log-safe request IDs only; generate a new ID for hostile input."""

    candidate = (value or "").strip()
    return candidate if _SAFE_TOKEN.fullmatch(candidate) else str(uuid.uuid4())


def hash_idempotency_key(*, user_id: str, key: str) -> str:
    """Hash a validated key so raw client tokens are never persisted."""

    candidate = key.strip()
    if not _SAFE_TOKEN.fullmatch(candidate):
        raise ValueError("Idempotency-Key 必须为 1-128 位字母、数字或 ._:-")
    return hashlib.sha256(f"{user_id}:{candidate}".encode()).hexdigest()


def request_fingerprint(*parts: object) -> str:
    """Fingerprint every semantic input covered by an idempotency key."""

    canonical = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
