from __future__ import annotations

import asyncio
import json
import math
import re
from typing import Any

import httpx

from app.core.config import Settings
from app.services.model_routing import ModelRoute, ModelRouter


class LLMClient:
    """Small OpenAI-compatible client with deterministic fallbacks."""

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.http = http_client
        self.router = ModelRouter(settings)

    async def classify_intent(self, query: str) -> str:
        intent, _ = await self.classify_intent_with_metadata(query)
        return intent

    async def classify_intent_with_metadata(
        self, query: str, *, context_summary: str = ""
    ) -> tuple[str, dict[str, Any]]:
        route = self.router.route(task="intent")
        metadata = self._route_metadata(route, query)
        heuristic_intent = self._heuristic_intent(query)
        if (
            heuristic_intent == "knowledge_feedback"
            and self.precheck_feedback_target(query)["target_status"] != "resolved"
        ):
            self._mark_model_skipped(
                metadata,
                route,
                reason="feedback_target_preflight_unresolved",
                target_status="unresolved",
            )
            return "knowledge_feedback", metadata
        if not self._enabled_for(route.selected_model):
            metadata["fallback_used"] = True
            return heuristic_intent, metadata
        system = (
            "你是企业 Agent 的意图分类器。只能返回 JSON："
            '{"intent":"knowledge_qa|document_compare|knowledge_feedback|unsupported"}。'
            "制度、流程、知识问题属于 knowledge_qa；比较两个版本/文件属于 document_compare；"
            "反馈知识库内容错误、过期、不完整或表述不清属于 knowledge_feedback；"
            "其他属于 unsupported。"
        )
        try:
            user = query
            if context_summary:
                user = f"仅供消歧的已净化历史摘要：\n{context_summary}\n\n当前请求：{query}"
            content, provider = await self._chat(system, user, model=route.selected_model)
            parsed = self._extract_json(content)
            intent = parsed.get("intent")
            if intent in {"knowledge_qa", "document_compare", "knowledge_feedback", "unsupported"}:
                self.router.record_success()
                metadata.update(self._usage_metadata(query, content))
                metadata.update(provider)
                return intent, metadata
        except Exception as exc:
            self.router.record_failure()
            metadata["failure_type"] = type(exc).__name__
            self._add_failure_metadata(metadata, exc)
        metadata["fallback_used"] = True
        return self._heuristic_intent(query), metadata

    async def make_feedback_draft(self, query: str) -> dict[str, str]:
        draft, _ = await self.make_feedback_draft_with_metadata(query)
        return draft

    async def make_feedback_draft_with_metadata(
        self, query: str
    ) -> tuple[dict[str, str], dict[str, Any]]:
        high_risk = any(
            word in query
            for word in ("食品安全", "过敏原", "添加剂", "召回", "合规", "法律", "法规")
        )
        route = self.router.route(task="feedback_draft", high_risk=high_risk)
        metadata = self._route_metadata(route, query)
        source_gate = self.precheck_feedback_target(query)
        if source_gate["target_status"] != "resolved":
            self._mark_model_skipped(
                metadata,
                route,
                reason="feedback_target_precheck_unresolved",
                target_status="unresolved",
            )
            return source_gate, metadata
        metadata["model_call_skipped"] = False
        metadata["precheck_target_status"] = "resolved"
        if not self._enabled_for(route.selected_model):
            metadata["fallback_used"] = True
            return self._heuristic_feedback_draft(query), metadata
        system = (
            "将员工对食品公司内部知识库的问题整理成反馈草稿，只返回 JSON，"
            "字段为 knowledge_item、issue_type、description、priority。"
            "issue_type 只能为 inaccurate、outdated、incomplete、ambiguous、content_issue。"
            "priority 只能为 low、normal、high。不要执行任何提交操作。"
        )
        try:
            content, provider = await self._chat(system, query, model=route.selected_model)
            parsed = self._extract_json(content)
            issue_type = parsed.get("issue_type", "content_issue")
            if issue_type not in {
                "inaccurate",
                "outdated",
                "incomplete",
                "ambiguous",
                "content_issue",
            }:
                issue_type = "content_issue"
            priority = parsed.get("priority", "normal")
            if priority not in {"low", "normal", "high"}:
                priority = "normal"
            draft = {
                "knowledge_item": str(parsed.get("knowledge_item") or query[:80])[:255],
                "issue_type": issue_type,
                "description": str(parsed.get("description") or query),
                "priority": priority,
            }
            # The target controls eligibility for a write workflow, so bind it
            # to the deterministic extraction from the original user text.
            # The model may classify and summarize the issue, but it cannot
            # replace the target with a different plausible-sounding item.
            draft["knowledge_item"] = source_gate["knowledge_item"]
            draft["target_status"] = "resolved"
            self.router.record_success()
            metadata.update(self._usage_metadata(query, content))
            metadata.update(provider)
            return draft, metadata
        except Exception as exc:
            self.router.record_failure()
            metadata["failure_type"] = type(exc).__name__
            self._add_failure_metadata(metadata, exc)
            metadata["fallback_used"] = True
            return self._heuristic_feedback_draft(query), metadata

    @property
    def _enabled(self) -> bool:
        return bool(
            self.settings.llm_base_url.strip()
            and self.settings.llm_model.strip()
            and self.settings.llm_api_key.get_secret_value()
        )

    def _enabled_for(self, model: str) -> bool:
        return bool(
            model != "deterministic-local-fallback"
            and self.settings.llm_base_url.strip()
            and self.settings.llm_api_key.get_secret_value()
        )

    async def _chat(
        self, system: str, user: str, *, model: str | None = None
    ) -> tuple[str, dict[str, Any]]:
        base = self.settings.llm_base_url.rstrip("/")
        url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
        response: httpx.Response | None = None
        retry_count = 0
        for attempt in range(self.settings.llm_max_attempts):
            try:
                response = await self.http.post(
                    url,
                    headers={
                        "Authorization": (
                            f"Bearer {self.settings.llm_api_key.get_secret_value()}"
                        ),
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model or self.settings.llm_model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "temperature": 0,
                    },
                    timeout=self.settings.llm_timeout_seconds,
                )
            except httpx.RequestError:
                if attempt + 1 >= self.settings.llm_max_attempts:
                    raise
                retry_count += 1
                await asyncio.sleep(self.settings.llm_retry_backoff_seconds * (2**attempt))
                continue
            if response.status_code not in {429, 500, 502, 503, 504}:
                break
            if attempt + 1 >= self.settings.llm_max_attempts:
                break
            retry_count += 1
            retry_after = response.headers.get("retry-after")
            try:
                delay = float(retry_after) if retry_after else 0.0
            except ValueError:
                delay = 0.0
            delay = max(delay, self.settings.llm_retry_backoff_seconds * (2**attempt))
            await asyncio.sleep(min(delay, 5.0))
        assert response is not None
        response.raise_for_status()
        payload = response.json()
        content = str(payload["choices"][0]["message"]["content"])
        provider = {
            "provider_model": str(payload.get("model") or model or self.settings.llm_model),
            "provider_usage": payload.get("usage") or {},
            "retry_count": retry_count,
        }
        request_id = response.headers.get("x-request-id") or response.headers.get("request-id")
        if request_id:
            provider["provider_request_id"] = request_id[:128]
        return content, provider

    @staticmethod
    def _add_failure_metadata(metadata: dict[str, Any], exc: Exception) -> None:
        if not isinstance(exc, httpx.HTTPStatusError):
            return
        metadata["provider_status_code"] = exc.response.status_code
        try:
            error = exc.response.json().get("error") or {}
        except ValueError:
            error = {}
        if error.get("code"):
            metadata["provider_error_code"] = str(error["code"])[:128]

    def _route_metadata(self, route: ModelRoute, text: str) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "task": route.task,
            "selected_model": route.selected_model,
            "fallback_models": list(route.fallback_models),
            "route_reason": route.reason,
            "circuit_open": route.circuit_open,
            "shadow_eligible": bool(self.settings.llm_shadow_model.strip()),
            "fallback_used": False,
            "estimated_input_tokens": math.ceil(len(text) / 4),
        }
        if self.settings.llm_shadow_model.strip():
            metadata["shadow_model"] = self.settings.llm_shadow_model.strip()
            metadata["shadow_executed"] = False
        return metadata

    @staticmethod
    def _mark_model_skipped(
        metadata: dict[str, Any],
        route: ModelRoute,
        *,
        reason: str,
        target_status: str,
    ) -> None:
        metadata.update(
            {
                "planned_model": route.selected_model,
                "selected_model": "not_invoked",
                "route_reason": reason,
                "model_call_skipped": True,
                "skip_reason": "feedback_target_unresolved",
                "precheck_target_status": target_status,
            }
        )

    @staticmethod
    def _usage_metadata(input_text: str, output_text: str) -> dict[str, int]:
        return {
            "estimated_input_tokens": math.ceil(len(input_text) / 4),
            "estimated_output_tokens": math.ceil(len(output_text) / 4),
        }

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.IGNORECASE).strip()
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
        return json.loads(cleaned)

    @classmethod
    def precheck_feedback_target(cls, query: str) -> dict[str, str]:
        """Resolve feedback target without invoking an external model."""

        return cls._heuristic_feedback_draft(query)

    @classmethod
    def _heuristic_feedback_draft(cls, query: str) -> dict[str, str]:
        """Build a deterministic knowledge-feedback draft when no LLM is available."""

        text = query.strip()
        issue_rules = (
            ("outdated", ("过期", "失效", "旧版", "未更新", "没有更新")),
            ("incomplete", ("不完整", "缺少", "遗漏", "没写全", "缺东西")),
            ("ambiguous", ("表述不清", "有歧义", "看不懂", "看不明白", "不明确")),
            ("inaccurate", ("不准确", "不一致", "不对", "错误", "有问题", "写错", "修正")),
        )
        issue_type = next(
            (name for name, words in issue_rules if any(word in text for word in words)),
            "content_issue",
        )

        knowledge_item = re.sub(
            r"^(?:请|麻烦|请帮我|帮我)(?:提交|提出|反馈|检查|修正)?",
            "",
            text,
        ).strip(" ，,。；;")
        split_pattern = "|".join(
            re.escape(word)
            for word in (
                "表述不清",
                "不准确",
                "不一致",
                "不完整",
                "有歧义",
                "有问题",
                "看不懂",
                "不明确",
                "未更新",
                "没有更新",
                "没写全",
                "缺东西",
                "看不明白",
                "过期",
                "失效",
                "旧版",
                "缺少",
                "遗漏",
                "不对",
                "错误",
                "写错",
                "需要修正",
                "修正",
            )
        )
        knowledge_item = re.split(split_pattern, knowledge_item, maxsplit=1)[0]
        knowledge_item = re.split(r"[，,。；;]", knowledge_item, maxsplit=1)[0]
        knowledge_item = re.sub(
            r"(?:这条)?知识库内容(?:可能|好像|疑似)?$|(?:可能|好像|疑似)$",
            "",
            knowledge_item,
        ).strip(" ，,。；;")
        knowledge_item = knowledge_item[:255] or "未指定知识点"

        high_risk_words = (
            "食品安全",
            "过敏原",
            "添加剂",
            "限量",
            "召回",
            "合规",
            "处罚",
            "法律",
            "法规",
        )
        priority = "high" if any(word in text for word in high_risk_words) else "normal"
        return {
            "knowledge_item": knowledge_item,
            "issue_type": issue_type,
            "description": text,
            "priority": priority,
            "target_status": "resolved"
            if cls._is_specific_knowledge_item(knowledge_item)
            else "unresolved",
        }

    @staticmethod
    def _is_specific_knowledge_item(value: str) -> bool:
        normalized = re.sub(r"\s+", "", value or "")
        generic_items = {
            "",
            "知识",
            "知识库",
            "知识库内容",
            "制度",
            "标准",
            "流程",
            "规定",
            "食品",
            "添加剂",
            "标签",
            "标签内容",
            "标签知识",
            "食品知识",
            "添加剂知识",
            "未指定知识点",
        }
        generic_pattern = re.compile(
            r"^(?:这个|那个|该)?(?:知识库(?:内容)?|知识|制度|标准|流程|规定|"
            r"食品(?:知识)?|添加剂(?:知识)?|标签(?:内容|知识)?)(?:好像|可能|疑似|需要)?$"
        )
        return (
            len(normalized) >= 4
            and normalized not in generic_items
            and not generic_pattern.fullmatch(normalized)
        )

    @staticmethod
    def _heuristic_intent(query: str) -> str:
        routing_query = re.sub(r"【演练：[^】]+】", "", query)
        lowered = routing_query.lower()
        feedback_words = ("反馈", "提交反馈", "提出反馈", "纠错", "修正", "更新知识库")
        issue_words = (
            "有问题",
            "不对",
            "错误",
            "写错",
            "过期",
            "未更新",
            "没有更新",
            "不完整",
            "缺少",
            "遗漏",
            "表述不清",
            "有歧义",
            "不准确",
            "不一致",
        )
        knowledge_words = (
            "知识库",
            "知识",
            "制度",
            "标准",
            "流程",
            "规定",
            "食品",
            "添加剂",
            "标签",
            "过敏原",
            "召回",
        )
        feedback_negated = bool(
            re.search(
                r"(?:不要|不用|无需|不需要|别)(?:再)?(?:帮我)?(?:提交|提出|创建)?反馈",
                routing_query,
            )
        )
        if any(
            phrase in routing_query
            for phrase in ("打电话", "发送邮件", "发邮件", "预订", "播放", "打开摄像头", "生成图片")
        ):
            return "unsupported"
        if (
            not feedback_negated
            and any(word in routing_query for word in feedback_words)
            and any(word in routing_query for word in knowledge_words)
        ) or (
            not feedback_negated
            and
            any(word in routing_query for word in issue_words)
            and any(word in routing_query for word in knowledge_words)
        ):
            return "knowledge_feedback"
        explicit_compare = any(word in routing_query for word in ("比较", "对比", "区别"))
        implicit_compare = any(word in routing_query for word in ("差异", "变化")) and any(
            word in routing_query for word in ("文档", "制度", "版本", "文件", "版", "旧", "新")
        )
        if explicit_compare or implicit_compare:
            return "document_compare"
        if any(
            word in routing_query
            for word in (
                "制度",
                "规定",
                "流程",
                "标准",
                "公司",
                "知识库",
                "审批",
                "报销",
                "供应商",
                "标签",
                "仓储",
                "培训",
                "投诉",
                "放行",
                "留样",
                "过敏原",
                "添加剂",
                "召回",
                "清洁",
                "消毒",
                "原料",
                "生产",
                "员工",
                "冷库",
                "资质",
                "异常",
                "如何",
                "什么",
                "哪些",
                "多少",
                "多久",
                "是否",
            )
        ):
            return "knowledge_qa"
        if any(word in lowered for word in ("policy", "procedure", "compare", "document")):
            return "knowledge_qa"
        return "unsupported"
