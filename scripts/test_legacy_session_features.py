#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test for session history/delete APIs")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--dev-user", default="demo-user")
    args = parser.parse_args()

    headers = {"X-Dev-User": args.dev_user}
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=120.0) as client:
        session_response = client.post("/api/v2/sessions", json={"title": "legacy-feature-test"})
        session_response.raise_for_status()
        session_id = session_response.json()["id"]

        first_run = client.post(
            "/api/v2/agent/runs",
            json={"session_id": session_id, "query": "公司的采购审批流程是什么？"},
        )
        first_run.raise_for_status()
        first_run_id = first_run.json()["run_id"]

        second_run = client.post(
            "/api/v2/agent/runs",
            json={"session_id": session_id, "query": "公司的报销制度是什么？"},
        )
        second_run.raise_for_status()
        second_run_id = second_run.json()["run_id"]

        history_before = client.get(f"/api/v2/sessions/{session_id}/history")
        history_before.raise_for_status()
        before_payload = history_before.json()
        assert before_payload["total"] == 2, before_payload
        assert [item["run_id"] for item in before_payload["items"]] == [
            first_run_id,
            second_run_id,
        ]

        delete_message = client.delete(f"/api/v2/sessions/{session_id}/messages/{first_run_id}")
        delete_message.raise_for_status()
        delete_payload = delete_message.json()
        assert delete_payload["status"] == "deleted", delete_payload

        deleted_run = client.get(f"/api/v2/agent/runs/{first_run_id}")
        assert deleted_run.status_code == 404, deleted_run.text

        history_after = client.get(f"/api/v2/sessions/{session_id}/history")
        history_after.raise_for_status()
        after_payload = history_after.json()
        assert after_payload["total"] == 1, after_payload
        assert after_payload["items"][0]["run_id"] == second_run_id, after_payload

        delete_session = client.delete(f"/api/v2/sessions/{session_id}")
        assert delete_session.status_code == 204, delete_session.text

        deleted_session_history = client.get(f"/api/v2/sessions/{session_id}/history")
        assert deleted_session_history.status_code == 404, deleted_session_history.text

    print(
        json.dumps(
            {
                "session_id": session_id,
                "deleted_run_id": first_run_id,
                "remaining_run_id": second_run_id,
                "history_before_delete": before_payload["total"],
                "history_after_delete": after_payload["total"],
                "message_delete_status": delete_payload["status"],
                "session_delete_status": delete_session.status_code,
                "result": "ok",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
