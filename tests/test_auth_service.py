"""Tests for token handling in StravaAuthService (no network)."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from dotenv import dotenv_values

from strava_data.auth_service import StravaAuthService


class AuthServiceTests(unittest.TestCase):
    def _service(self, td: str, expires_at: int) -> tuple[StravaAuthService, Path]:
        env_path = Path(td) / ".env"
        env_path.write_text(
            "STRAVA_CLIENT_ID=123\n"
            "STRAVA_CLIENT_SECRET=secret\n"
            "STRAVA_ACCESS_TOKEN=access-1\n"
            "STRAVA_REFRESH_TOKEN=refresh-1\n"
            f"STRAVA_EXPIRES_AT={expires_at}\n",
            encoding="utf-8",
        )
        return StravaAuthService(str(env_path)), env_path

    def test_valid_token_configures_refresh_without_warning(self) -> None:
        expires_at = int(time.time()) + 3600
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {}, clear=True):
            svc, _ = self._service(td, expires_at)
            client = svc.ensure_token_valid()
            self.assertEqual(client.access_token, "access-1")
            self.assertEqual(client.refresh_token, "refresh-1")
            self.assertEqual(client.token_expires, expires_at)
            with self.assertNoLogs(level="WARNING"):
                client.protocol.refresh_expired_token()

    def test_save_tokens_persists_auto_refreshed_values(self) -> None:
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {}, clear=True):
            svc, env_path = self._service(td, int(time.time()) + 3600)
            client = svc.ensure_token_valid()
            client.access_token = "access-2"
            client.refresh_token = "refresh-2"
            client.token_expires = 1999999999
            svc.save_tokens()
            saved = dotenv_values(env_path)
            self.assertEqual(saved["STRAVA_ACCESS_TOKEN"], "access-2")
            self.assertEqual(saved["STRAVA_REFRESH_TOKEN"], "refresh-2")
            self.assertEqual(saved["STRAVA_EXPIRES_AT"], "1999999999")
            self.assertEqual(saved["STRAVA_CLIENT_SECRET"], "secret")


if __name__ == "__main__":
    unittest.main()
