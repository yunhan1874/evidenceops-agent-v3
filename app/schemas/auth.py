from pydantic import BaseModel, Field


class FeishuCodeRequest(BaseModel):
    code: str = Field(min_length=2, max_length=2048)


class UserResponse(BaseModel):
    id: str
    external_user_id: str
    display_name: str
    role: str


class FeishuLoginResponse(BaseModel):
    access_token: str
    expires_in: int
    token_type: str
    user: UserResponse
