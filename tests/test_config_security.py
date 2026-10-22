from __future__ import annotations

import unittest

from app.core.config import Settings


class ProductionConfigTests(unittest.TestCase):
    def test_development_defaults_are_allowed(self) -> None:
        Settings(_env_file=None, app_env="development").assert_safe_runtime()

    def test_production_rejects_development_controls(self) -> None:
        settings = Settings(
            _env_file=None,
            app_env="production",
            dev_auth_enabled=True,
            rag_mock_mode=True,
        )
        with self.assertRaisesRegex(RuntimeError, "DEV_AUTH_ENABLED"):
            settings.assert_safe_runtime()


if __name__ == "__main__":
    unittest.main()
