#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import uuid

import httpx


async def main() -> None:
    parser = argparse.ArgumentParser(description="Idempotency and concurrent approval integration test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    user = f"resilience-{uuid.uuid4()}"
    headers = {"X-Dev-User": user}
    async with httpx.AsyncClient(base_url=args.base_url, headers=headers, timeout=30.0) as client:
        session_response = await client.post("/api/v2/sessions", json={"title": "resilience-test"})
        session_response.raise_for_status()
        session_id = session_response.json()["id"]

        replay_headers = {"Idempotency-Key": "same-request-001"}
        request_body = {"session_id": session_id, "query": "公司的采购审批流程是什么？"}
        first = await client.post("/api/v2/agent/runs", json=request_body, headers=replay_headers)
        first.raise_for_status()
        replay = await client.post("/api/v2/agent/runs", json=request_body, headers=replay_headers)
        replay.raise_for_status()
        assert first.json()["run_id"] == replay.json()["run_id"], "replay created a second run"

        conflict = await client.post(
            "/api/v2/agent/runs",
            json={"session_id": session_id, "query": "同一个幂等键但请求内容已变化"},
            headers=replay_headers,
        )
        assert conflict.status_code == 409, conflict.text

        pending = await client.post(
            "/api/v2/agent/runs",
            json={"session_id": session_id, "query": "采购制度内容过期了，请反馈给知识库"},
        )
        pending.raise_for_status()
        pending_payload = pending.json()
        assert pending_payload["status"] == "pending_approval", pending_payload
        run_id = pending_payload["run_id"]

        async def approve(comment: str) -> httpx.Response:
            return await client.post(
                f"/api/v2/agent/runs/{run_id}/resume",
                json={"approved": True, "comment": comment},
            )

        approval_results = await asyncio.gather(approve("worker-a"), approve("worker-b"))
        approval_statuses = sorted(response.status_code for response in approval_results)
        assert approval_statuses == [200, 409], approval_statuses

        final = await client.get(f"/api/v2/agent/runs/{run_id}")
        final.raise_for_status()
        final_payload = final.json()
        submit_count = sum(
            item["tool_name"] == "submit_feedback" for item in final_payload["tool_calls"]
        )
        assert final_payload["status"] == "completed", final_payload
        assert submit_count == 1, f"expected one side effect, got {submit_count}"

    print(
        json.dumps(
            {
                "idempotent_replay_same_run": True,
                "same_key_different_payload_status": conflict.status_code,
                "concurrent_approval_statuses": approval_statuses,
                "submit_feedback_count": submit_count,
                "final_status": final_payload["status"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
