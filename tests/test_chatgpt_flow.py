import argparse
import asyncio
import inspect
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import register_chatgpt
import register_three_platforms
import run_full_flow
import oauth_codex


class ChatGPTFlowTests(unittest.TestCase):
    def test_visible_email_form_means_submission_did_not_advance(self):
        page = MagicMock()
        email_input = MagicMock()
        email_input.count = AsyncMock(return_value=1)
        email_input.is_visible = AsyncMock(return_value=True)
        page.locator.return_value.first = email_input

        advanced = asyncio.run(
            register_chatgpt.chatgpt_email_submission_advanced(page)
        )

        self.assertFalse(advanced)

    def test_missing_email_form_means_submission_advanced(self):
        page = MagicMock()
        email_input = MagicMock()
        email_input.count = AsyncMock(return_value=0)
        page.locator.return_value.first = email_input

        advanced = asyncio.run(
            register_chatgpt.chatgpt_email_submission_advanced(page)
        )

        self.assertTrue(advanced)

    def test_browser_mail_fallback_only_runs_on_last_graph_attempt(self):
        self.assertFalse(
            register_chatgpt.should_use_browser_mail_fallback(True, 0)
        )
        self.assertFalse(
            register_chatgpt.should_use_browser_mail_fallback(True, 1)
        )
        self.assertTrue(
            register_chatgpt.should_use_browser_mail_fallback(True, 2)
        )
        self.assertFalse(
            register_chatgpt.should_use_browser_mail_fallback(False, 2)
        )

    def test_stuck_onboarding_recovers_when_session_and_main_ui_exist(self):
        page = MagicMock()
        page.goto = AsyncMock()
        probe = MagicMock()
        probe.goto = AsyncMock()
        probe.close = AsyncMock()
        composer = MagicMock()
        composer.count = AsyncMock(return_value=1)
        probe.locator.return_value = composer
        page.context.new_page = AsyncMock(return_value=probe)

        with (
            patch(
                "common.session_export.fetch_chatgpt_session",
                AsyncMock(return_value={"accessToken": "token"}),
            ),
            patch.object(register_chatgpt.asyncio, "sleep", AsyncMock()),
        ):
            recovered = asyncio.run(
                register_chatgpt.recover_stuck_onboarding_session(page)
            )

        self.assertTrue(recovered)
        page.goto.assert_awaited_once()
        probe.close.assert_awaited_once()

    def test_required_onboarding_consents_are_checked(self):
        boxes = []
        for _ in range(3):
            box = MagicMock()
            box.is_checked = AsyncMock(side_effect=[False, True])
            box.check = AsyncMock()
            boxes.append(box)
        locator = MagicMock()
        locator.count = AsyncMock(return_value=3)
        locator.nth.side_effect = boxes
        page = MagicMock()
        page.locator.return_value = locator

        with patch.object(register_chatgpt.asyncio, "sleep", AsyncMock()):
            total, checked = asyncio.run(
                register_chatgpt.ensure_required_onboarding_consents(page)
            )

        self.assertEqual((total, checked), (3, 3))
        for box in boxes:
            box.check.assert_awaited_once_with(force=True, timeout=4000)

    def test_onboarding_consent_helper_ignores_optional_only_page(self):
        locator = MagicMock()
        locator.count = AsyncMock(return_value=0)
        page = MagicMock()
        page.locator.return_value = locator

        total, checked = asyncio.run(
            register_chatgpt.ensure_required_onboarding_consents(page)
        )

        self.assertEqual((total, checked), (0, 0))

    def test_cookie_buttons_include_german_accept_labels(self):
        self.assertIn("Annehmen", register_chatgpt._COOKIE_BTNS)
        self.assertIn("Alle akzeptieren", register_chatgpt._COOKIE_BTNS)
        self.assertNotIn("Ablehnen", register_chatgpt._COOKIE_BTNS)

    def test_browser_profile_uses_configured_clash_proxy(self):
        with patch.dict(
            os.environ,
            {"CLASH_PROXY": "http://proxy-user:proxy-pass@127.0.0.1:7897"},
        ):
            fields = register_chatgpt.clash_browser_proxy_fields()

        self.assertEqual(fields["proxyType"], "http")
        self.assertEqual(fields["host"], "127.0.0.1")
        self.assertEqual(fields["port"], "7897")
        self.assertEqual(fields["proxyUserName"], "proxy-user")
        self.assertEqual(fields["proxyPassword"], "proxy-pass")

    def test_region_rejection_is_parsed_from_auth_response(self):
        error = register_chatgpt._openai_error_from_text(
            '{"error":{"code":"unsupported_country_region_territory",'
            '"message":"Country, region, or territory not supported"}}',
            status=403,
            url="/api/accounts/create",
        )

        self.assertEqual(error["code"], "unsupported_country_region_territory")
        self.assertEqual(error["status"], 403)

    def test_blank_codex_numeric_env_uses_default(self):
        with patch.dict(os.environ, {"CODEX_SMS_TIMEOUT": ""}):
            self.assertEqual(register_chatgpt._env_int("CODEX_SMS_TIMEOUT", 150), 150)

    def test_oauth_can_continue_when_cookie_less_probe_is_blocked(self):
        with patch.object(register_chatgpt, "_active_cf_nodes", []):
            with patch.object(register_chatgpt, "CF_NODES", ["node-a"]):
                with patch.object(register_chatgpt.time, "sleep"):
                    with patch.object(register_chatgpt, "_activate_cf_node", return_value="node-a"):
                        with patch.object(
                            register_chatgpt,
                            "_probe_chatgpt_node",
                            return_value=(False, "JP", 403),
                        ):
                            selected = register_chatgpt.select_chatgpt_node(
                                "auto", allow_blocked=True
                            )
        self.assertEqual(selected, "node-a")

    def test_auto_nodes_are_discovered_from_current_clash_group(self):
        catalog = {
            "GLOBAL": {
                "type": "Selector",
                "all": [
                    "DIRECT",
                    "剩余流量：10 GB",
                    "🇯🇵 日本 | 01",
                    "nested-group",
                    "🇸🇬 新加坡 | 01",
                ],
            },
            "🇯🇵 日本 | 01": {"type": "VLESS"},
            "nested-group": {"type": "Selector"},
            "🇸🇬 新加坡 | 01": {"type": "Trojan"},
        }

        with patch.object(register_chatgpt, "_active_cf_nodes", []):
            with patch.object(register_chatgpt, "CF_NODES", []):
                with patch("_clash_verge.ClashClient") as client_class:
                    client_class.return_value.proxies.return_value = {
                        "proxies": catalog
                    }
                    candidates = register_chatgpt._chatgpt_node_candidates()

        self.assertEqual(candidates, ["🇯🇵 日本 | 01", "🇸🇬 新加坡 | 01"])

    def test_auto_selection_uses_discovered_node_names(self):
        probes = [(False, "JP", 403), (True, "SG", 200)]
        with patch.object(register_chatgpt, "_active_cf_nodes", []):
            with patch.object(
                register_chatgpt,
                "_discover_chatgpt_nodes",
                return_value=["🇯🇵 日本 | 01", "🇸🇬 新加坡 | 01"],
            ):
                with patch.object(register_chatgpt.time, "sleep"):
                    with patch.object(
                        register_chatgpt,
                        "_activate_cf_node",
                        side_effect=lambda node: node,
                    ) as activate:
                        with patch.object(
                            register_chatgpt,
                            "_probe_chatgpt_node",
                            side_effect=probes,
                        ):
                            selected = register_chatgpt.select_chatgpt_node("auto")

        self.assertEqual(selected, "🇸🇬 新加坡 | 01")
        self.assertEqual(
            [call.args[0] for call in activate.call_args_list],
            ["🇯🇵 日本 | 01", "🇸🇬 新加坡 | 01"],
        )

    def test_auto_nodes_interleave_regions_before_applying_probe_limit(self):
        candidates = [
            "🇯🇵 日本 | 01",
            "🇯🇵 日本 | 02",
            "🇸🇬 新加坡 | 01",
            "🇺🇸 美国 | 01",
            "other-node",
        ]

        self.assertEqual(
            register_chatgpt._order_chatgpt_nodes(candidates),
            [
                "🇯🇵 日本 | 01",
                "🇸🇬 新加坡 | 01",
                "🇺🇸 美国 | 01",
                "🇯🇵 日本 | 02",
                "other-node",
            ],
        )

    def test_three_platform_command_pins_chatgpt_node(self):
        args = argparse.Namespace(
            timeout=600,
            node="level1-test-node",
            keep_on_fail=False,
            import_c2a=False,
            codex=False,
        )
        command = register_three_platforms.build_command(
            "chatgpt",
            args,
            ("mail@example.com", "password", "refresh-token", "client-id"),
        )

        node_index = command.index("--node")
        self.assertEqual(command[node_index + 1], "level1-test-node")

    def test_platform_failure_returns_nonzero(self):
        results = [("chatgpt", False, 1, "chatgpt.log")]
        self.assertEqual(register_three_platforms.results_exit_code(results), 1)
        self.assertEqual(
            register_three_platforms.results_exit_code(
                [("chatgpt", True, 0, "chatgpt.log")]
            ),
            0,
        )

    def test_full_flow_redacts_credentials(self):
        rendered = run_full_flow.redact_command(
            ["python", "child.py", "--password", "mail-pass", "--token", "graph-token"]
        )
        self.assertNotIn("mail-pass", rendered)
        self.assertNotIn("graph-token", rendered)
        self.assertEqual(rendered.count("***"), 2)

    def test_standalone_oauth_propagates_failure_exit_code(self):
        source = inspect.getsource(oauth_codex.main)
        self.assertNotIn("sys.exit(", source)
        module_source = inspect.getsource(oauth_codex)
        self.assertIn("sys.exit(asyncio.run(main()))", module_source)


if __name__ == "__main__":
    unittest.main()
