import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from claude_code_auth.settings import Settings
from claude_code_auth.stores.keychain import KeychainStore


class KeychainStoreTests(unittest.TestCase):
    def test_load_returns_without_triggering_keychain_dump(self) -> None:
        payload = '{"claudeAiOauth": {"accessToken": "abc123"}}'

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "claude_code_auth.stores.keychain.platform.system",
                return_value="Darwin",
            ), mock.patch(
                "claude_code_auth.stores.keychain.read_keychain_entry",
                return_value=(payload, None),
            ) as read_mock, mock.patch(
                "claude_code_auth.keychain.discover_keychain_services"
            ) as discover_mock:
                store = KeychainStore(Settings(config_dir=Path(tmp)))
                tokens = store.load()

        self.assertIsNotNone(tokens)
        self.assertEqual(tokens.access_token, "abc123")
        read_mock.assert_called()
        discover_mock.assert_not_called()

    def test_discovery_uses_login_keychain_only(self) -> None:
        payload = '{"claudeAiOauth": {"accessToken": "xyz456"}}'

        login_result = subprocess.CompletedProcess(
            ["security", "login-keychain"], 0, '"/Users/test/Library/Keychains/login.keychain-db"\n', ""
        )
        dump_result = subprocess.CompletedProcess(
            ["security", "dump-keychain", "/Users/test/Library/Keychains/login.keychain-db"],
            0,
            '    "svce"="Claude Code-other"\n',
            "",
        )

        def fake_read(service: str):
            if service == "Claude Code-other":
                return payload, None
            return None, "item not found"

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "claude_code_auth.stores.keychain.platform.system",
                return_value="Darwin",
            ), mock.patch(
                "claude_code_auth.stores.keychain.read_keychain_entry",
                side_effect=fake_read,
            ), mock.patch(
                "claude_code_auth.keychain.subprocess.run"
            ) as run_mock:
                run_mock.side_effect = [login_result, dump_result]
                store = KeychainStore(Settings(config_dir=Path(tmp)))
                tokens = store.load()

        self.assertIsNotNone(tokens)
        self.assertEqual(tokens.access_token, "xyz456")
        self.assertEqual(
            run_mock.call_args_list[0].args[0], ["security", "login-keychain"]
        )
        self.assertEqual(
            run_mock.call_args_list[1].args[0],
            ["security", "dump-keychain", "/Users/test/Library/Keychains/login.keychain-db"],
        )


if __name__ == "__main__":
    unittest.main()
