from __future__ import annotations

import re
import unittest

from app.services.request_keys import (
    hash_idempotency_key,
    normalize_request_id,
    request_fingerprint,
)


class RequestKeyTests(unittest.TestCase):
    def test_valid_request_id_is_preserved(self) -> None:
        self.assertEqual(normalize_request_id("trace-123:abc"), "trace-123:abc")

    def test_log_injection_request_id_is_replaced(self) -> None:
        generated = normalize_request_id("ok\nforged-log=true")
        self.assertRegex(
            generated,
            re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"),
        )

    def test_idempotency_key_is_scoped_to_user(self) -> None:
        first = hash_idempotency_key(user_id="user-a", key="checkout-1")
        replay = hash_idempotency_key(user_id="user-a", key="checkout-1")
        other_user = hash_idempotency_key(user_id="user-b", key="checkout-1")
        self.assertEqual(first, replay)
        self.assertNotEqual(first, other_user)
        self.assertNotIn("checkout-1", first)

    def test_invalid_idempotency_key_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            hash_idempotency_key(user_id="user-a", key="x" * 129)

    def test_fingerprint_covers_optional_document_inputs(self) -> None:
        base = request_fingerprint("session", "query", "doc-a", "doc-b")
        changed = request_fingerprint("session", "query", "doc-a", "doc-c")
        self.assertNotEqual(base, changed)


if __name__ == "__main__":
    unittest.main()
