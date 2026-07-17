import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

CONFIG_DIR_ENV = "CLAUDE_CODE_CONFIG_DIR"

from claude_code_auth import ClaudeCodeOAuthManager


class _MockResponse:
    def __init__(
        self, *, status_code: int, payload: dict | None = None, text: str = ""
    ) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class ClaudeCodeOAuthManagerTests(unittest.TestCase):
    def test_refresh_persists_to_plaintext_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            credentials_path = config_dir / ".credentials.json"
            initial_payload = {
                "claudeAiOauth": {
                    "accessToken": "expired-token",
                    "refreshToken": "refresh-me",
                    "expiresAt": int(time.time() * 1000) + 60_000,
                }
            }
            credentials_path.write_text(json.dumps(initial_payload), encoding="utf-8")

            def fake_post(url, json=None, timeout=None, headers=None):
                self.assertEqual(url, "https://console.anthropic.com/v1/oauth/token")
                self.assertEqual(json["refresh_token"], "refresh-me")
                return _MockResponse(
                    status_code=200,
                    payload={
                        "access_token": "new-access",
                        "refresh_token": "new-refresh",
                        "expires_in": 3600,
                        "scope": "user.read",
                    },
                )

            def fake_get(url, headers=None, timeout=None):
                self.assertTrue(url.endswith("/api/oauth/profile"))
                return _MockResponse(
                    status_code=200,
                    payload={"organization": {"organization_type": "team"}},
                )

            with mock.patch(
                "claude_code_auth.manager.platform.system", return_value="Linux"
            ):
                with mock.patch.dict(os.environ, {CONFIG_DIR_ENV: tmp}, clear=False):
                    manager = ClaudeCodeOAuthManager()

            with (
                mock.patch(
                    "claude_code_auth.manager.requests.post", side_effect=fake_post
                ) as post_mock,
                mock.patch(
                    "claude_code_auth.manager.requests.get", side_effect=fake_get
                ),
            ):
                tokens = manager.refresh()
                self.assertEqual(tokens.access_token, "new-access")
                self.assertEqual(tokens.refresh_token, "new-refresh")
                self.assertEqual(tokens.subscription_type, "team")
                self.assertEqual(tokens.scopes, ("user.read",))
                post_mock.assert_called_once()

            saved = json.loads(credentials_path.read_text(encoding="utf-8"))
            stored = saved["claudeAiOauth"]
            self.assertEqual(stored["accessToken"], "new-access")
            self.assertEqual(stored["refreshToken"], "new-refresh")
            self.assertGreater(
                stored["expiresAt"], initial_payload["claudeAiOauth"]["expiresAt"]
            )

    def test_access_token_triggers_refresh_when_expiring(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            credentials_path = config_dir / ".credentials.json"
            initial_payload = {
                "claudeAiOauth": {
                    "accessToken": "stale-token",
                    "refreshToken": "refresh-me",
                    "expiresAt": int(time.time() * 1000) - 1,
                }
            }
            credentials_path.write_text(json.dumps(initial_payload), encoding="utf-8")

            def fake_post(url, json=None, timeout=None, headers=None):
                return _MockResponse(
                    status_code=200,
                    payload={
                        "access_token": "fresh-token",
                        "refresh_token": "refresh-me",
                        "expires_in": 60,
                    },
                )

            def fake_get(url, headers=None, timeout=None):
                return _MockResponse(status_code=200, payload={})

            with mock.patch(
                "claude_code_auth.manager.platform.system", return_value="Linux"
            ):
                with mock.patch.dict(os.environ, {CONFIG_DIR_ENV: tmp}, clear=False):
                    manager = ClaudeCodeOAuthManager()

            with (
                mock.patch(
                    "claude_code_auth.manager.requests.post", side_effect=fake_post
                ),
                mock.patch(
                    "claude_code_auth.manager.requests.get", side_effect=fake_get
                ),
            ):
                token = manager.access_token
                self.assertEqual(token, "fresh-token")

            saved = json.loads(credentials_path.read_text(encoding="utf-8"))
            stored = saved["claudeAiOauth"]
            self.assertEqual(stored["accessToken"], "fresh-token")
            self.assertGreater(
                stored["expiresAt"], initial_payload["claudeAiOauth"]["expiresAt"]
            )

    def test_session_id_is_sticky_across_build_headers_calls(self) -> None:
        """X-Claude-Code-Session-Id must be minted once per manager instance
        and reused on every call; X-Client-Request-Id must be fresh every
        call. Regression test for a bug where both were regenerated per
        build_headers() call, which does not match real Claude Code
        (cc-xray: sessionId is a process-lifetime singleton)."""
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            credentials_path = config_dir / ".credentials.json"
            credentials_path.write_text(
                json.dumps(
                    {
                        "claudeAiOauth": {
                            "accessToken": "valid-token",
                            "refreshToken": "refresh-me",
                            "expiresAt": int(time.time() * 1000) + 3_600_000,
                        }
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch(
                "claude_code_auth.manager.platform.system", return_value="Linux"
            ):
                with mock.patch.dict(os.environ, {CONFIG_DIR_ENV: tmp}, clear=False):
                    manager = ClaudeCodeOAuthManager()

            headers1 = manager.build_headers()
            headers2 = manager.build_headers()

            self.assertEqual(
                headers1["X-Claude-Code-Session-Id"],
                headers2["X-Claude-Code-Session-Id"],
            )
            self.assertNotEqual(
                headers1["X-Client-Request-Id"],
                headers2["X-Client-Request-Id"],
            )


if __name__ == "__main__":
    unittest.main()
