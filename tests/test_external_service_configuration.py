from __future__ import annotations

import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "external_configuration", ROOT / "scripts/configure_external_services.py"
)
configuration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configuration)


class ConfigurationTest(unittest.TestCase):
    def test_secret_patch_contains_only_supplied_keys_and_preserves_other_data(self):
        result = subprocess.CompletedProcess([], 0, "", "")
        with patch.object(configuration.subprocess, "run", return_value=result) as run:
            configuration.patch(["kubectl"], "secret", "banxia-secrets",
                                {"BANXIA_OBJECT_ACCESS_KEY": "private-key"})
        args, kwargs = run.call_args
        self.assertNotIn("private-key", " ".join(args[0]))
        payload = json.loads(kwargs["input"])
        self.assertEqual(payload, {"data": {"BANXIA_OBJECT_ACCESS_KEY": "cHJpdmF0ZS1rZXk="}})
        self.assertIn("--type=merge", args[0])
        self.assertTrue(kwargs["capture_output"])

    def test_cluster_error_does_not_echo_secret_patch(self):
        result = subprocess.CompletedProcess([], 1, "", "private-key")
        with patch.object(configuration.subprocess, "run", return_value=result), \
                self.assertRaisesRegex(RuntimeError, "patch failed") as error:
            configuration.patch(["kubectl"], "secret", "banxia-secrets",
                                {"BANXIA_OBJECT_ACCESS_KEY": "private-key"})
        self.assertNotIn("private-key", str(error.exception))

    def test_smtp_rejects_plaintext_transport(self):
        with patch.object(configuration, "prompt",
                          side_effect=["smtp.example.com", "25", "none"]), \
                self.assertRaisesRegex(ValueError, "ssl or starttls"):
            configuration.smtp_config()

    def test_tos_rejects_url_with_embedded_credentials(self):
        with patch.object(configuration, "prompt",
                          return_value="https://private-key@objects.example.com"), \
                self.assertRaisesRegex(ValueError, "hostname"):
            configuration.tos_config()

    def test_complete_validation_precedes_any_cluster_change(self):
        with patch("sys.argv", ["configure", "--tos", "--smtp"]), \
                patch.object(configuration.sys.stdin, "isatty", return_value=True), \
                patch.object(configuration, "tos_config", return_value=({"endpoint": "x"}, {"ak": "x"})), \
                patch.object(configuration, "smtp_config", side_effect=ValueError("invalid SMTP")), \
                patch.object(configuration, "patch") as apply, \
                self.assertRaisesRegex(SystemExit, "invalid SMTP"):
            configuration.main()
        apply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
