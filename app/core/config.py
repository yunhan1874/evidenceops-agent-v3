from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded only from environment variables/.env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "EvidenceOps Enterprise Knowledge Agent"
    app_env: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    database_url: str = "postgresql+asyncpg://agent:agent@postgres:5432/agent"
    checkpoint_database_url: str = "postgresql://agent:agent@postgres:5432/agent?sslmode=disable"

    feishu_access_token_url: str = "https://open.feishu.cn/open-apis/authen/v1/access_token"
    feishu_userinfo_url: str = "https://open.feishu.cn/open-apis/authen/v1/user_info"
    feishu_app_id: str = ""
    feishu_app_secret: SecretStr = SecretStr("")
    excel_whitelist_path: str = ""

    # Local development only. Never enable this in production.
    dev_auth_enabled: bool = True
    dev_admin_users: str = "admin"

    rag_base_url: str = "http://ragflow:9380"
    rag_api_key: SecretStr = SecretStr("")
    rag_chat_id: str = ""
    rag_request_timeout_seconds: float = 90.0
    rag_mock_mode: bool = False

    # Optional OpenAI-compatible model endpoint (Qwen/vLLM/OpenAI-compatible service).
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_router_model: str = ""
    llm_primary_model: str = ""
    llm_high_risk_model: str = ""
    llm_shadow_model: str = ""
    llm_timeout_seconds: float = 45.0
    llm_max_attempts: int = Field(default=2, ge=1, le=3)
    llm_retry_backoff_seconds: float = Field(default=0.5, ge=0.1, le=5)
    llm_circuit_failure_threshold: int = Field(default=3, ge=1, le=20)
    llm_circuit_cooldown_seconds: float = Field(default=60.0, ge=1, le=3600)

    max_agent_steps: int = Field(default=8, ge=2, le=30)
    max_tool_calls: int = Field(default=6, ge=1, le=30)
    max_run_seconds: float = Field(default=120.0, ge=1, le=600)
    max_llm_estimated_tokens: int = Field(default=8000, ge=128, le=100000)
    max_query_length: int = Field(default=4000, ge=128, le=20000)
    context_max_chars: int = Field(default=3000, ge=256, le=20000)
    context_max_turns: int = Field(default=4, ge=0, le=20)
    tool_max_output_chars: int = Field(default=12000, ge=256, le=100000)
    tool_egress_allowlist: str = "ragflow,localhost,127.0.0.1"

    # Serving-time evidence policy. High-risk questions intentionally use a
    # stricter threshold than ordinary enterprise knowledge questions.
    evidence_min_average_score: float = Field(default=0.55, ge=0, le=1)
    evidence_high_risk_min_score: float = Field(default=0.75, ge=0, le=1)
    evidence_compare_min_sources: int = Field(default=2, ge=2, le=10)
    evidence_max_citations: int = Field(default=10, ge=1, le=50)

    @property
    def allowed_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def admin_user_ids(self) -> set[str]:
        return {item.strip() for item in self.dev_admin_users.split(",") if item.strip()}

    @property
    def allowed_egress_hosts(self) -> set[str]:
        return {
            item.strip().casefold()
            for item in self.tool_egress_allowlist.split(",")
            if item.strip()
        }

    @property
    def whitelist_path(self) -> Path | None:
        value = self.excel_whitelist_path.strip()
        return Path(value) if value else None

    def assert_safe_runtime(self) -> None:
        """Fail at startup instead of silently running production with dev controls."""

        if self.app_env.casefold() != "production":
            return
        errors = []
        if self.dev_auth_enabled:
            errors.append("DEV_AUTH_ENABLED 必须为 false")
        if self.rag_mock_mode:
            errors.append("RAG_MOCK_MODE 必须为 false")
        if not self.feishu_app_id or not self.feishu_app_secret.get_secret_value():
            errors.append("飞书生产凭证不能为空")
        if not self.rag_chat_id or not self.rag_api_key.get_secret_value():
            errors.append("RAGFlow 生产配置不能为空")
        if self.whitelist_path is None:
            errors.append("EXCEL_WHITELIST_PATH 必须配置")
        if "change-me" in self.database_url or "change-me" in self.checkpoint_database_url:
            errors.append("数据库默认密码必须替换")
        if "*" in self.allowed_origins:
            errors.append("生产 CORS_ORIGINS 不能包含通配符")
        if errors:
            raise RuntimeError("生产配置不安全：" + "；".join(errors))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
