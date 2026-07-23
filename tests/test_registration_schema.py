import unittest
from unittest.mock import patch

import register
import validate_keys
from webui.scripts import ENV_SCHEMA, SCRIPTS


def _script(script_id):
    return next(item for item in SCRIPTS if item["id"] == script_id)


class RegistrationSchemaTests(unittest.TestCase):
    def test_claude_validator_reuses_clash_and_modern_fingerprint(self):
        with patch.dict(
            validate_keys.os.environ,
            {
                "CLASH_PROXY": "http://user:pass@127.0.0.1:7897",
                "CLAUDE_BROWSER_CORE_VERSION": "146",
            },
        ):
            options = validate_keys.validation_browser_options()

        self.assertEqual(options["proxyType"], "http")
        self.assertEqual(options["host"], "127.0.0.1")
        self.assertEqual(options["port"], "7897")
        self.assertEqual(options["proxyUserName"], "user")
        self.assertEqual(options["proxyPassword"], "pass")
        self.assertEqual(
            options["browserFingerPrint"]["coreVersion"], "146"
        )

    def test_both_grok_tasks_expose_sub2api_import(self):
        for script_id in ("register_grok", "register_grok_browser"):
            flags = {item["flag"] for item in _script(script_id)["args"]}
            self.assertIn("--sub2api", flags)
            self.assertIn("--sub2api-group", flags)

    def test_grok_browser_exposes_mailbox_rotation(self):
        args = {item["flag"]: item for item in _script("register_grok_browser")["args"]}
        self.assertEqual(args["--mailbox-attempts"]["default"], 6)

    def test_chatgpt_exposes_fixed_node(self):
        args = {item["flag"]: item for item in _script("register_chatgpt")["args"]}
        self.assertEqual(args["--node"]["default"], "auto")
        oauth_args = {item["flag"]: item for item in _script("oauth_codex")["args"]}
        self.assertEqual(oauth_args["--node"]["default"], "auto")

    def test_claude_defaults_to_latest_rt(self):
        args = {item["flag"]: item for item in _script("register_claude")["args"]}
        self.assertTrue(args["--latest-rt"]["default"])
        self.assertIn("--client-id", args)
        self.assertEqual(args["--node"]["default"], "auto")
        self.assertEqual(args["--challenge-node-retries"]["default"], 3)
        self.assertEqual(args["--captcha-manual-timeout"]["default"], 0)

    def test_webui_exposes_claude_solver_configuration_only(self):
        keys = {
            item["key"]
            for group in ENV_SCHEMA
            for item in group["items"]
        }
        self.assertTrue(
            {
                "CLAUDE_HCAPTCHA_SOLVE_RETRIES",
                "CLAUDE_VISION_API_BASE",
                "CLAUDE_VISION_API_KEY",
                "CLAUDE_VISION_MODEL",
                "CLAUDE_NODE_PROBE_LIMIT",
                "CLAUDE_NODE_PROBE_TIMEOUT_SECONDS",
                "CLAUDE_BROWSER_CORE_VERSION",
            }.issubset(keys)
        )
    def test_claude_graph_reader_receives_client_and_timestamp(self):
        with patch("common.mailbox.get_link_by_token", return_value="https://claude.ai/magic-link#ok") as reader:
            result = register.get_magic_link_by_token(
                "user@example.com",
                "refresh-token",
                client_id="client-id",
                max_wait=12,
                received_after=123.0,
            )
        self.assertEqual(result, "https://claude.ai/magic-link#ok")
        self.assertEqual(reader.call_args.kwargs["client_id"], "client-id")
        self.assertEqual(reader.call_args.kwargs["received_after"], 123.0)


if __name__ == "__main__":
    unittest.main()
