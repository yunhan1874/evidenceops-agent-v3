from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import Settings


@dataclass(frozen=True)
class ExternalUser:
    external_user_id: str
    display_name: str
    open_id: str | None = None
    union_id: str | None = None


class FeishuClient:
    def __init__(self, settings: Settings, http_client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.http = http_client

    async def exchange_code(self, code: str) -> dict:
        response = await self.http.post(
            self.settings.feishu_access_token_url,
            json={
                "app_id": self.settings.feishu_app_id,
                "app_secret": self.settings.feishu_app_secret.get_secret_value(),
                "grant_type": "authorization_code",
                "code": code,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise ValueError(payload.get("msg") or "飞书授权失败")
        return payload["data"]

    async def get_user(self, access_token: str) -> ExternalUser:
        response = await self.http.get(
            self.settings.feishu_userinfo_url,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        display_name = (
            data.get("name")
            or data.get("employee_name")
            or data.get("en_name")
            or data.get("user_name")
        )
        external_id = data.get("user_id") or data.get("open_id") or data.get("union_id")
        if not display_name or not external_id:
            raise ValueError("飞书用户信息缺少姓名或唯一用户标识")
        return ExternalUser(
            external_user_id=str(external_id),
            display_name=str(display_name).strip(),
            open_id=data.get("open_id"),
            union_id=data.get("union_id"),
        )
