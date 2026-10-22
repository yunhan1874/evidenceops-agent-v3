from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agent.graph import build_agent_graph
from app.api.routes import agent, auth, health, sessions
from app.clients.feishu import FeishuClient
from app.clients.llm import LLMClient
from app.clients.ragflow import RagFlowClient
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.versions import APP_VERSION
from app.db.session import close_database, init_database
from app.services.evidence import EvidenceGuard, EvidencePolicy
from app.services.harness import HarnessService
from app.services.request_keys import normalize_request_id
from app.services.tool_policy import ToolPolicyEngine
from app.services.whitelist import Whitelist

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.assert_safe_runtime()
    await init_database()
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=90.0, write=30.0, pool=10.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )
    app.state.feishu_client = FeishuClient(settings, http_client)
    app.state.ragflow_client = RagFlowClient(settings, http_client)
    llm_client = LLMClient(settings, http_client)
    harness = HarnessService(settings)
    tool_policy = ToolPolicyEngine(
        timeout_seconds=min(settings.rag_request_timeout_seconds, settings.max_run_seconds),
        max_output_chars=settings.tool_max_output_chars,
        max_calls=settings.max_tool_calls,
        allowed_egress_hosts=settings.allowed_egress_hosts,
    )
    app.state.harness = harness
    app.state.tool_policy = tool_policy
    evidence_guard = EvidenceGuard(
        EvidencePolicy(
            min_average_score=settings.evidence_min_average_score,
            high_risk_min_score=settings.evidence_high_risk_min_score,
            compare_min_sources=settings.evidence_compare_min_sources,
            max_citations=settings.evidence_max_citations,
        )
    )
    app.state.whitelist = Whitelist.from_excel(settings.whitelist_path)

    checkpoint_context = AsyncPostgresSaver.from_conn_string(settings.checkpoint_database_url)
    checkpointer = await checkpoint_context.__aenter__()
    await checkpointer.setup()
    app.state.agent_graph = build_agent_graph(
        ragflow=app.state.ragflow_client,
        llm=llm_client,
        evidence_guard=evidence_guard,
        settings=settings,
        harness=harness,
        tool_policy=tool_policy,
        checkpointer=checkpointer,
    )
    logger.info("Agent application started")
    try:
        yield
    finally:
        await checkpoint_context.__aexit__(None, None, None)
        await http_client.aclose()
        await close_database()
        logger.info("Agent application stopped")


app = FastAPI(
    title=settings.app_name,
    version=APP_VERSION,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Idempotency-Key",
        "X-Request-ID",
        "X-Dev-User",
    ],
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = normalize_request_id(request.headers.get("X-Request-ID"))
    request.state.request_id = request_id
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request_id=%s method=%s path=%s status=%s latency_ms=%s",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        int((time.perf_counter() - started) * 1000),
    )
    return response


app.include_router(health.router)
app.include_router(auth.router, prefix="/api/v2")
app.include_router(sessions.router, prefix="/api/v2")
app.include_router(agent.router, prefix="/api/v2")
