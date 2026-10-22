from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.feishu import ExternalUser
from app.core.config import Settings, get_settings
from app.db.models import User
from app.db.session import get_db
from app.repositories.core import UserRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CurrentUser:
    id: str
    external_user_id: str
    display_name: str
    role: str


async def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    x_dev_user: str | None = Header(default=None, alias="X-Dev-User"),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    if settings.dev_auth_enabled and x_dev_user:
        external_id = x_dev_user.strip()
        role = "admin" if external_id in settings.admin_user_ids else "user"
        user = await UserRepository.upsert(
            db,
            external_user_id=external_id,
            display_name=external_id,
            role=role,
        )
        return _to_current_user(user)

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 Bearer access token",
        )
    token = authorization.removeprefix("Bearer ").strip()
    try:
        external: ExternalUser = await request.app.state.feishu_client.get_user(token)
    except Exception as exc:
        logger.warning(
            "Feishu identity verification failed request_id=%s error_type=%s",
            getattr(request.state, "request_id", None),
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=401,
            detail={"code": "FEISHU_AUTH_FAILED", "message": "飞书身份验证失败"},
        ) from exc

    whitelist = request.app.state.whitelist
    if not whitelist.allows(
        external.external_user_id,
        external.display_name,
        allow_empty=settings.app_env != "production",
    ):
        raise HTTPException(status_code=403, detail="用户不在系统白名单中")

    user = await UserRepository.upsert(
        db,
        external_user_id=external.external_user_id,
        display_name=external.display_name,
    )
    if not user.active:
        raise HTTPException(status_code=403, detail="用户已停用")
    return _to_current_user(user)


def require_admin(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return current_user


def _to_current_user(user: User) -> CurrentUser:
    return CurrentUser(
        id=str(user.id),
        external_user_id=user.external_user_id,
        display_name=user.display_name,
        role=user.role,
    )
