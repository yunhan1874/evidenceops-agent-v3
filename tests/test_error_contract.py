from __future__ import annotations

import unittest

from app.core.errors import AppError, audit_error


class ErrorContractTests(unittest.TestCase):
    def test_operational_error_persists_only_stable_code(self) -> None:
        error = AppError("UPSTREAM_TIMEOUT", "服务超时", retryable=True)
        self.assertEqual(audit_error(error), "UPSTREAM_TIMEOUT")
        self.assertTrue(error.retryable)

    def test_unknown_error_does_not_persist_message(self) -> None:
        error = RuntimeError("postgresql://user:secret@private-host/database")
        self.assertEqual(audit_error(error), "RuntimeError")


if __name__ == "__main__":
    unittest.main()
