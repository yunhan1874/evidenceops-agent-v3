import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.repositories.core import UserRepository
from app.schemas.auth import FeishuCodeRequest, FeishuLoginResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


@router.post("/feishu/access-token", response_model=FeishuLoginResponse)
async def exchange_feishu_code(
    body: FeishuCodeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FeishuLoginResponse:
    if not settings.feishu_app_id or not settings.feishu_app_secret.get_secret_value():
        raise HTTPException(status_code=500, detail="飞书应用凭证未配置")
    try:
        token_data = await request.app.state.feishu_client.exchange_code(body.code)
        access_token = token_data["access_token"]
        external = await request.app.state.feishu_client.get_user(access_token)
    except Exception as exc:
        logger.exception(
            "Feishu login failed request_id=%s", getattr(request.state, "request_id", None)
        )
        raise HTTPException(
            status_code=502,
            detail={"code": "FEISHU_LOGIN_FAILED", "message": "飞书登录失败"},
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
    return FeishuLoginResponse(
        access_token=access_token,
        expires_in=int(token_data.get("expires_in", 0)),
        token_type=str(token_data.get("token_type", "Bearer")),
        user=UserResponse(
            id=str(user.id),
            external_user_id=user.external_user_id,
            display_name=user.display_name,
            role=user.role,
        ),
    )
