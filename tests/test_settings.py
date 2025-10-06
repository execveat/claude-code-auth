import os
import unittest
from pathlib import Path

from claude_code_auth.settings import ClaudeEnvironment, load_settings


class SettingsTests(unittest.TestCase):
    def test_defaults(self) -> None:
        settings = load_settings(env={})
        self.assertEqual(settings.environment, ClaudeEnvironment.PROD)
        self.assertFalse(settings.config_dir_overridden)
        self.assertEqual(settings.request_timeout, (5.0, 20.0))

    def test_local_flag_overrides_environment(self) -> None:
        env = {
            "CLAUDE_CODE_USE_LOCAL_OAUTH": "true",
        }
        settings = load_settings(env=env)
        self.assertEqual(settings.environment, ClaudeEnvironment.LOCAL)
        self.assertEqual(settings.source_for("environment"), "CLAUDE_CODE_USE_LOCAL_OAUTH (derived)")

    def test_explicit_environment_wins_over_local_flag(self) -> None:
        env = {
            "CLAUDE_CODE_ENVIRONMENT": "staging",
            "CLAUDE_CODE_USE_LOCAL_OAUTH": "true",
        }
        settings = load_settings(env=env)
        self.assertEqual(settings.environment, ClaudeEnvironment.STAGING)
        self.assertEqual(settings.source_for("environment"), "CLAUDE_CODE_ENVIRONMENT")

    def test_config_dir_override_sets_flag(self) -> None:
        env = {
            "CLAUDE_CODE_CONFIG_DIR": "/tmp/claude",
        }
        settings = load_settings(env=env)
        self.assertTrue(settings.config_dir_overridden)
        self.assertEqual(settings.config_dir, Path("/tmp/claude"))
        self.assertEqual(settings.source_for("config_dir"), "CLAUDE_CODE_CONFIG_DIR")

    def test_keychain_services_split(self) -> None:
        env = {
            "CLAUDE_CODE_KEYCHAIN_SERVICES": os.pathsep.join(
                ["Claude Code-credentials", "Custom"]
            )
        }
        settings = load_settings(env=env)
        self.assertEqual(settings.keychain_services, ("Claude Code-credentials", "Custom"))


if __name__ == "__main__":
    unittest.main()
