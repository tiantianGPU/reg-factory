import base64
import io
import json
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import register
from common import proxy_switch


class ClaudeChallengeTests(unittest.IsolatedAsyncioTestCase):
    def test_claude_uses_modern_exit_ip_derived_browser_fingerprint(self):
        fingerprint = register.claude_browser_fingerprint()

        self.assertEqual(fingerprint["coreVersion"], "146")
        self.assertTrue(fingerprint["isIpCreateTimeZone"])
        self.assertTrue(fingerprint["isIpCreateLanguage"])
        self.assertTrue(fingerprint["isIpCreatePosition"])

    def test_grid_pick_parser_accepts_colon_and_answer_fallback(self):
        from vision_solver import vision

        self.assertEqual(vision._parse_picklist("pick: [1, 4]", 9), [1, 4])
        self.assertEqual(vision._parse_picklist("ANSWER=6", 9), [6])
        self.assertEqual(
            vision._parse_picklist(
                "Click the two shortest chains in grid cells #1 and #6.", 9
            ),
            [1, 6],
        )

    def test_local_bead_chain_detector_selects_two_shortest_chains(self):
        from PIL import Image, ImageDraw
        from vision_solver.imaging import detect_shortest_bead_chain_cells

        image = Image.new("RGB", (300, 300), (55, 45, 70))
        draw = ImageDraw.Draw(image)
        chains = (
            [(130 + i * 12, 50) for i in range(3)],
            [(50, 210 + i * 12) for i in range(4)],
            [(110 + i * 12, 150) for i in range(7)],
            [(205, 190 + i * 10) for i in range(9)],
        )
        colors = ((235, 130, 220), (100, 210, 235), (240, 205, 80), (230, 145, 100))
        for chain, color in zip(chains, colors):
            for x, y in chain:
                draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=color)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        image_b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
        boxes = []
        centers = []
        for row in range(3):
            for col in range(3):
                boxes.append((col * 100, row * 100, 100, 100))
                centers.append((col * 100 + 50, row * 100 + 50))

        picks = detect_shortest_bead_chain_cells(
            image_b64,
            {"boxes": boxes, "cells_xy": centers},
        )

        self.assertEqual(picks, [1, 6])

    def test_tree_climber_challenge_is_clarified_as_squirrels(self):
        prompt = register._claude_hcaptcha_grid_prompt(
            "Select animals that climb straight up tree trunks"
        )

        self.assertIn("squirrels", prompt)
        self.assertIn("PICK=[a,b,c]", prompt)

    def test_korean_shortest_chain_challenge_gets_click_target_prompt(self):
        prompt = register._claude_hcaptcha_grid_prompt(
            "\uac00\uc7a5 \uc9e7\uc740 \ub450 \uac1c\uc758 \uccb4\uc778\uc744 \ud074\ub9ad\ud558\uc138\uc694"
        )

        self.assertIn("TWO shortest chains", prompt)
        self.assertIn("exactly one numbered cell per chain", prompt)
        self.assertIn("PICK=[a,b]", prompt)

    def test_shortest_line_challenge_gets_strict_length_prompt(self):
        prompt = register._claude_hcaptcha_grid_prompt(
            "Please click on the TWO shortest lines"
        )

        self.assertIn("TWO shortest separate thick colored lines", prompt)
        self.assertIn("Ignore the textured background", prompt)
        self.assertIn("FROM=(x1,y1) TO=(x2,y2)", prompt)

        korean_prompt = register._claude_hcaptcha_grid_prompt(
            "\uac00\uc7a5 \uc9e7\uc740 \uc120 \ub450 \uac1c\ub97c \ud074\ub9ad\ud574 \uc8fc\uc138\uc694"
        )
        self.assertIn("TWO shortest separate thick colored lines", korean_prompt)

    def test_two_point_parser_accepts_normalized_click_targets(self):
        from vision_solver import vision

        self.assertEqual(
            vision._parse_points("FROM=(0.25,0.40) TO=(0.75,0.65)"),
            ((0.25, 0.40), (0.75, 0.65)),
        )

    def test_vision_request_uses_chrome_tls_after_requests_fail(self):
        from vision_solver import vision

        response = MagicMock(status_code=200)
        response.json.return_value = {
            "choices": [{"message": {"content": "PICK=[2]"}}]
        }
        with (
            patch.object(
                vision.requests,
                "post",
                side_effect=vision.requests.exceptions.ConnectionError("reset"),
            ) as regular_post,
            patch("curl_cffi.requests.post", return_value=response) as curl_post,
            patch.object(vision.time, "sleep"),
        ):
            answer = vision._ask_one(
                "https://vision.example", "key", "gemini-3.6-flash",
                "grid", "aW1hZ2U=",
            )

        self.assertEqual(answer, "PICK=[2]")
        self.assertEqual(regular_post.call_count, 3)
        curl_post.assert_called_once()

    async def test_dom_tile_grid_uses_valid_playwright_nth_selectors(self):
        from vision_solver import drivers
        from vision_solver.schema import CaptchaSpec

        frame = MagicMock()
        tiles = MagicMock()
        tiles.count = AsyncMock(return_value=2)
        frame.locator.return_value = tiles
        spec = CaptchaSpec(
            frame_match=["hcaptcha"],
            tile_sel=".task-image",
            max_rounds=1,
            settle_ms=0,
            success_gone_frame=False,
        )

        with (
            tempfile.TemporaryDirectory() as shot_dir,
            patch.object(drivers, "_find_frame", AsyncMock(return_value=frame)),
            patch.object(drivers, "_read_instruction", AsyncMock(return_value="select")),
            patch.object(
                drivers, "shot_element", AsyncMock(return_value="aW1hZ2U=")
            ) as shot,
            patch.object(
                drivers,
                "stitch_options_grid",
                return_value=("aW1hZ2U=", {"cells": []}),
            ),
            patch.object(drivers, "enhance_local", return_value="aW1hZ2U="),
            patch.object(drivers, "vote_picklist", return_value=([], {}, [])),
            patch.object(drivers.asyncio, "sleep", AsyncMock()),
        ):
            await drivers.solve_grid_select(MagicMock(), spec, shot_dir)

        selectors = [call.args[1] for call in shot.await_args_list]
        self.assertEqual(
            selectors,
            [".task-image >> nth=0", ".task-image >> nth=1"],
        )

    async def test_german_cookie_accept_button_is_dismissed(self):
        page = MagicMock()
        selector_locator = MagicMock()
        selector_locator.first = selector_locator
        selector_locator.count = AsyncMock(return_value=0)
        page.locator.return_value = selector_locator
        buttons = {}

        def button_for_label(_role, *, name, exact):
            self.assertTrue(exact)
            button = MagicMock()
            button.first = button
            button.count = AsyncMock(return_value=1 if name == "Annehmen" else 0)
            button.is_visible = AsyncMock(return_value=True)
            button.click = AsyncMock()
            buttons[name] = button
            return button

        page.get_by_role.side_effect = button_for_label
        with patch.object(register.asyncio, "sleep", AsyncMock()):
            dismissed = await register.dismiss_claude_cookie_banner(page)

        self.assertEqual(dismissed, "Annehmen")
        buttons["Annehmen"].click.assert_awaited_once_with(timeout=3000)

    async def test_german_cookie_reject_is_preferred_over_accept(self):
        page = MagicMock()
        selector_locator = MagicMock()
        selector_locator.first = selector_locator
        selector_locator.count = AsyncMock(return_value=0)
        page.locator.return_value = selector_locator
        clicked = []

        def button_for_label(_role, *, name, exact):
            self.assertTrue(exact)
            button = MagicMock()
            button.first = button
            button.count = AsyncMock(
                return_value=name in {"Alle Cookies ablehnen", "Annehmen"}
            )
            button.is_visible = AsyncMock(return_value=True)
            button.click = AsyncMock(side_effect=lambda **_kwargs: clicked.append(name))
            return button

        page.get_by_role.side_effect = button_for_label
        with patch.object(register.asyncio, "sleep", AsyncMock()):
            dismissed = await register.dismiss_claude_cookie_banner(page)

        self.assertEqual(dismissed, "Alle Cookies ablehnen")
        self.assertEqual(clicked, ["Alle Cookies ablehnen"])

    async def test_hidden_hcaptcha_preload_is_not_an_active_challenge(self):
        frame = MagicMock()
        frame.url = "https://newassets.hcaptcha.com/captcha/v1/preload"
        owner = MagicMock()
        owner.bounding_box = AsyncMock(return_value=None)
        frame.frame_element = AsyncMock(return_value=owner)
        frame.evaluate = AsyncMock(return_value=False)

        page = MagicMock()
        page.frames = [frame]
        widgets = MagicMock()
        widgets.count = AsyncMock(return_value=1)
        widgets.nth.return_value.is_visible = AsyncMock(return_value=False)
        page.locator.return_value = widgets

        self.assertFalse(await register._claude_hcaptcha_present(page))

    async def test_hidden_hcaptcha_capture_is_not_an_active_challenge(self):
        frame = MagicMock()
        frame.url = "https://newassets.hcaptcha.com/captcha/v1/frame=challenge"
        owner = MagicMock()
        owner.bounding_box = AsyncMock(return_value=None)
        frame.frame_element = AsyncMock(return_value=owner)
        frame.evaluate = AsyncMock(return_value=True)

        page = MagicMock()
        page.frames = [frame]
        widgets = MagicMock()
        widgets.count = AsyncMock(return_value=1)
        widgets.nth.return_value.is_visible = AsyncMock(return_value=False)
        page.locator.return_value = widgets

        self.assertFalse(await register._claude_hcaptcha_present(page))

    async def test_visible_hcaptcha_frame_is_an_active_challenge(self):
        frame = MagicMock()
        frame.url = "https://newassets.hcaptcha.com/captcha/v1/frame=challenge"
        owner = MagicMock()
        owner.bounding_box = AsyncMock(
            return_value={"x": 0, "y": 0, "width": 400, "height": 600}
        )
        frame.frame_element = AsyncMock(return_value=owner)

        page = MagicMock()
        page.frames = [frame]

        self.assertTrue(await register._claude_hcaptcha_present(page))

    async def test_magic_link_hcaptcha_uses_known_sitekey_fallback(self):
        page = MagicMock()
        page.url = "https://claude.ai/magic-link#nonce:email"
        page.frames = []
        page.evaluate = AsyncMock(return_value="browser-agent")

        params = await register._extract_claude_hcaptcha_params(page)

        self.assertEqual(
            params["sitekey"], register.CLAUDE_WEB_LOGIN_HCAPTCHA_SITEKEY
        )
        self.assertEqual(params["website_url"], "https://claude.ai/magic-link")

    async def test_loading_magic_link_falls_back_to_http_nonce_verification(self):
        page = MagicMock()
        with (
            patch.object(
                register,
                "prepare_claude_post_magic",
                AsyncMock(
                    side_effect=register.ClaudeChallengeError(
                        "Claude magic-link page stayed in loading state"
                    )
                ),
            ),
            patch.object(
                register,
                "verify_claude_magic_link_http",
                AsyncMock(return_value=True),
            ) as verify,
            patch.object(
                register,
                "verify_claude_magic_link_with_browser_token",
                AsyncMock(return_value=False),
            ),
        ):
            prepared = await register.prepare_claude_post_magic_with_http_fallback(
                page, "https://claude.ai/magic-link#nonce:email"
            )

        self.assertTrue(prepared)
        verify.assert_awaited_once_with(
            page, "https://claude.ai/magic-link#nonce:email"
        )

    async def test_expired_magic_link_does_not_use_http_fallback(self):
        page = MagicMock()
        with (
            patch.object(
                register,
                "prepare_claude_post_magic",
                AsyncMock(
                    side_effect=register.ClaudeChallengeError(
                        "Claude magic link expired before verification"
                    )
                ),
            ),
            patch.object(
                register,
                "verify_claude_magic_link_http",
                AsyncMock(return_value=True),
            ) as verify,
            patch.object(
                register,
                "verify_claude_magic_link_with_browser_token",
                AsyncMock(return_value=False),
            ),
        ):
            with self.assertRaises(register.ClaudeChallengeError):
                await register.prepare_claude_post_magic_with_http_fallback(
                    page, "https://claude.ai/magic-link#nonce:email"
                )

        verify.assert_not_awaited()

    async def test_unsolved_hcaptcha_falls_back_to_http_nonce_verification(self):
        page = MagicMock()
        with (
            patch.object(
                register,
                "prepare_claude_post_magic",
                AsyncMock(
                    side_effect=register.ClaudeChallengeError(
                        "Claude hCaptcha was not solved"
                    )
                ),
            ),
            patch.object(
                register,
                "verify_claude_magic_link_http",
                AsyncMock(return_value=True),
            ) as verify,
            patch.object(
                register,
                "verify_claude_magic_link_with_browser_token",
                AsyncMock(return_value=False),
            ),
        ):
            prepared = await register.prepare_claude_post_magic_with_http_fallback(
                page, "https://claude.ai/magic-link#nonce:email"
            )

        self.assertTrue(prepared)
        verify.assert_awaited_once()

    async def test_magic_link_fallback_prefers_pending_browser_session(self):
        page = MagicMock()
        with (
            patch.object(
                register,
                "prepare_claude_post_magic",
                AsyncMock(
                    side_effect=register.ClaudeChallengeError(
                        "Claude magic-link page stayed in loading state"
                    )
                ),
            ),
            patch.object(
                register,
                "verify_claude_magic_link_with_browser_token",
                AsyncMock(return_value=True),
            ) as browser_verify,
            patch.object(
                register,
                "verify_claude_magic_link_http",
                AsyncMock(return_value=True),
            ) as http_verify,
        ):
            prepared = await register.prepare_claude_post_magic_with_http_fallback(
                page, "https://claude.ai/magic-link#nonce:email"
            )

        self.assertTrue(prepared)
        browser_verify.assert_awaited_once_with(
            page, "https://claude.ai/magic-link#nonce:email"
        )
        http_verify.assert_not_awaited()

    async def test_cleared_hcaptcha_still_requires_magic_link_progress(self):
        page = MagicMock()
        page.url = "https://claude.ai/magic-link#nonce:email"
        page.evaluate = AsyncMock(return_value="loading")
        fields = MagicMock()
        fields.count = AsyncMock(return_value=0)
        page.locator.return_value = fields

        with (
            patch.object(
                register, "_claude_hcaptcha_present",
                AsyncMock(side_effect=[True, False]),
            ),
            patch.object(
                register, "solve_claude_hcaptcha", AsyncMock(return_value=True)
            ),
            patch.object(register.asyncio, "sleep", AsyncMock()),
            patch.object(
                register.time, "monotonic", side_effect=[0, 0, 0, 2]
            ),
        ):
            with self.assertRaisesRegex(
                register.ClaudeChallengeError, "stayed in loading state"
            ):
                await register.prepare_claude_post_magic(page, max_wait=1)

    async def test_native_magic_link_403_is_reported_without_captcha_retry(self):
        page = MagicMock()
        page._rf_magic_verify_status = 403
        with patch.object(
            register, "_claude_hcaptcha_present", AsyncMock(return_value=True)
        ) as present:
            with self.assertRaisesRegex(
                register.ClaudeChallengeError,
                "native magic-link verification rejected HTTP 403",
            ):
                await register.prepare_claude_post_magic(page, max_wait=25)

        present.assert_not_awaited()

    async def test_native_magic_link_200_success_is_authoritative(self):
        page = MagicMock()
        page._rf_magic_verify_status = 200
        page._rf_magic_verify_response_body = (
            '{"success":true,"account":{"uuid":"account-1"}'
        )
        page.goto = AsyncMock()

        with (
            patch.object(
                register, "_claude_hcaptcha_present", AsyncMock()
            ) as present,
            patch.object(register.asyncio, "sleep", AsyncMock()),
        ):
            prepared = await register.prepare_claude_post_magic(
                page, max_wait=25
            )

        self.assertTrue(prepared)
        present.assert_not_awaited()
        page.goto.assert_awaited_once_with(
            "https://claude.ai/", timeout=60000
        )

    async def test_native_200_after_slow_captcha_beats_deadline(self):
        page = MagicMock()
        page._rf_magic_verify_status = None
        page._rf_magic_verify_response_body = ""
        page.goto = AsyncMock()

        async def solve_and_accept(_page):
            page._rf_magic_verify_status = 200
            page._rf_magic_verify_response_body = '{"success":true}'
            return True

        with (
            patch.object(
                register, "_claude_hcaptcha_present", AsyncMock(return_value=True)
            ),
            patch.object(register, "solve_claude_hcaptcha", solve_and_accept),
            patch.object(register.asyncio, "sleep", AsyncMock()),
            patch.object(register.time, "monotonic", side_effect=[0, 0]),
        ):
            prepared = await register.prepare_claude_post_magic(
                page, max_wait=25
            )

        self.assertTrue(prepared)

    async def test_native_403_after_slow_captcha_is_not_hidden_as_loading(self):
        page = MagicMock()
        page._rf_magic_verify_status = None

        async def solve_and_reject(_page):
            page._rf_magic_verify_status = 403
            return True

        with (
            patch.object(
                register, "_claude_hcaptcha_present", AsyncMock(return_value=True)
            ),
            patch.object(register, "solve_claude_hcaptcha", solve_and_reject),
            patch.object(register.time, "monotonic", side_effect=[0, 0]),
        ):
            with self.assertRaisesRegex(
                register.ClaudeChallengeError,
                "native magic-link verification rejected HTTP 403",
            ):
                await register.prepare_claude_post_magic(page, max_wait=25)

    async def test_native_cloudflare_403_reloads_magic_link_once(self):
        page = MagicMock()
        page._rf_magic_verify_status = 403
        page._rf_magic_verify_response_body = "blocked"
        page._rf_magic_verify_response_headers = {"cf-mitigated": "challenge"}
        magic_link = "https://claude.ai/magic-link#nonce:email"

        with (
            patch.object(
                register,
                "prepare_claude_post_magic",
                AsyncMock(side_effect=[
                    register.ClaudeChallengeError(
                        "Claude native magic-link verification rejected HTTP 403"
                    ),
                    True,
                ]),
            ) as prepare,
            patch.object(
                register, "open_claude_magic_link", AsyncMock()
            ) as reopen,
        ):
            prepared = await register.prepare_claude_post_magic_with_http_fallback(
                page, magic_link, max_wait=25
            )

        self.assertTrue(prepared)
        reopen.assert_awaited_once_with(page, magic_link)
        self.assertEqual(prepare.await_args_list[1].kwargs["max_wait"], 45)
        self.assertTrue(page._rf_native_cf_reload_attempted)
        self.assertIsNone(page._rf_magic_verify_status)

    def test_concrete_nodes_include_country_only_leaf_proxies(self):
        catalog = {
            "proxies": {
                "numbered-01": {"type": "Vless"},
                "country-only": {"type": "Vless"},
                "nested-group": {"type": "Selector"},
            }
        }
        with (
            patch.object(
                proxy_switch,
                "list_nodes",
                return_value=["numbered-01", "country-only", "nested-group"],
            ),
            patch.object(proxy_switch, "_get", return_value=catalog),
        ):
            nodes = proxy_switch.concrete_nodes()

        self.assertEqual(nodes, ["numbered-01", "country-only"])

    def test_http_preflight_rejects_region_and_localized_challenge_pages(self):
        session = MagicMock()
        session.get.side_effect = [
            MagicMock(status_code=200, text="app-unavailable-in-region"),
            MagicMock(status_code=200, text="사람인지 확인하는 중입니다"),
        ]

        self.assertFalse(register._warm_claude_http_session(session, "test"))
        self.assertFalse(register._warm_claude_http_session(session, "test"))

    def test_http_preflight_accepts_real_login_html(self):
        session = MagicMock()
        session.get.return_value = MagicMock(
            status_code=200, text="<html><title>Claude</title></html>"
        )

        self.assertTrue(register._warm_claude_http_session(session, "test"))

    def test_api_response_rejects_200_html_challenge(self):
        response = MagicMock(
            status_code=200,
            text="<html><title>Just a moment...</title></html>",
        )
        response.headers = {"content-type": "text/html"}

        self.assertIsNone(register._claude_json_response(response))

    def test_api_response_accepts_json(self):
        response = MagicMock(status_code=200, text='{"ok":true}')
        response.headers = {"content-type": "application/json"}
        response.json.return_value = {"ok": True}

        self.assertEqual(register._claude_json_response(response), {"ok": True})

    async def test_bitbrowser_open_retries_transient_opening_state(self):
        browser = MagicMock()
        browser.open_browser.side_effect = [
            Exception("浏览器正在打开中"),
            {"ws": "ws://ready"},
        ]
        with patch.object(register.asyncio, "sleep", AsyncMock()):
            result = await register._open_bitbrowser_with_retry(browser, "profile")

        self.assertEqual(result["ws"], "ws://ready")
        self.assertEqual(browser.open_browser.call_count, 2)

    async def test_bitbrowser_open_retries_transient_tls_disconnect(self):
        browser = MagicMock()
        browser.open_browser.side_effect = [
            Exception("Client network socket disconnected before secure TLS connection"),
            {"ws": "ws://ready"},
        ]
        with patch.object(register.asyncio, "sleep", AsyncMock()):
            result = await register._open_bitbrowser_with_retry(browser, "profile")

        self.assertEqual(result["ws"], "ws://ready")
        self.assertEqual(browser.open_browser.call_count, 2)

    async def test_managed_challenge_never_uses_blind_coordinate_clicks(self):
        frame = MagicMock()
        frame.url = "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/"
        frame.click = AsyncMock()

        page = MagicMock()
        page.frames = [frame]
        page.evaluate = AsyncMock(return_value=[])
        page.mouse.click = AsyncMock()

        with patch.object(
            register, "_claude_managed_challenge_present", AsyncMock(return_value=True)
        ):
            solved = await register.solve_turnstile(page, max_wait=0)

        self.assertFalse(solved)
        frame.click.assert_not_awaited()
        page.mouse.click.assert_not_awaited()

    async def test_korean_challenge_redirect_is_managed_challenge(self):
        page = MagicMock()
        page.url = "https://claude.ai/api/challenge_redirect?to=%2Flogin"
        page.title = AsyncMock(return_value="Claude")
        page.locator.return_value.inner_text = AsyncMock(
            return_value="사람인지 확인하는 중입니다."
        )

        present = await register._claude_managed_challenge_present(page)

        self.assertTrue(present)

    async def test_japanese_full_page_verification_is_managed_challenge(self):
        page = MagicMock()
        page.url = "https://claude.ai/login"
        page.title = AsyncMock(return_value="Claude")
        page.locator.return_value.inner_text = AsyncMock(
            return_value="私はロボットではありません。これには数秒かかる場合があります。"
        )

        present = await register._claude_managed_challenge_present(page)

        self.assertTrue(present)

    async def test_login_form_rotates_failed_auto_node(self):
        page = MagicMock()
        page.goto = AsyncMock()
        page.screenshot = AsyncMock()
        page.context.clear_cookies = AsyncMock()

        with (
            patch.object(register, "CLAUDE_PROXY_AUTO", True),
            patch.object(register, "CLAUDE_PROXY_NODE", "node-1"),
            patch.object(
                register,
                "_claude_email_form_ready",
                AsyncMock(side_effect=[False, False, True]),
            ),
            patch.object(register, "solve_turnstile", AsyncMock(return_value=False)),
            patch.object(register, "_pick_claude_node", return_value="node-2") as pick,
            patch.object(register, "_record_claude_node") as record,
            patch.object(register.asyncio, "sleep", AsyncMock()),
        ):
            ready = await register.ensure_claude_login_form(
                page, challenge_wait=0, node_retries=1, manual_timeout=0
            )
            selected_node = register.CLAUDE_PROXY_NODE

        self.assertTrue(ready)
        self.assertEqual(selected_node, "node-2")
        pick.assert_called_once_with({"node-1"})
        record.assert_called_once_with("node-2")
        page.context.clear_cookies.assert_awaited_once()
        page.goto.assert_awaited_once_with(register.CLAUDE_LOGIN_URL, timeout=60000)

    async def test_manual_handoff_resumes_when_login_form_appears(self):
        page = MagicMock()
        page.screenshot = AsyncMock()

        with (
            patch.object(register, "CLAUDE_PROXY_AUTO", False),
            patch.object(
                register,
                "_claude_email_form_ready",
                AsyncMock(side_effect=[False, False, True]),
            ),
            patch.object(register, "solve_turnstile", AsyncMock(return_value=False)),
        ):
            ready = await register.ensure_claude_login_form(
                page, challenge_wait=0, node_retries=0, manual_timeout=30
            )

        self.assertTrue(ready)

    async def test_magic_link_api_uses_same_origin_payload(self):
        page = MagicMock()
        page.evaluate = AsyncMock(
            return_value={"ok": True, "status": 200, "error": "", "sso": False}
        )

        sent = await register.request_claude_magic_link(page, "mail@example.com")

        self.assertTrue(sent)
        payload = page.evaluate.call_args.args[1]
        self.assertEqual(payload, {"email": "mail@example.com"})
        script = page.evaluate.call_args.args[0]
        self.assertIn("/api/auth/send_magic_link", script)
        self.assertIn("source: 'claude'", script)

    async def test_magic_link_navigation_arms_hcaptcha_hook_first(self):
        page = MagicMock()
        page.goto = AsyncMock()
        with (
            patch.object(
                register, "_install_claude_hcaptcha_hook", AsyncMock()
            ) as install,
            patch.object(register.asyncio, "sleep", AsyncMock()),
        ):
            await register.open_claude_magic_link(
                page, "https://claude.ai/magic-link#nonce:email"
            )

        install.assert_awaited_once_with(page)
        page.goto.assert_awaited_once_with(
            "https://claude.ai/magic-link#nonce:email", timeout=60000
        )

    async def test_email_submission_prefers_visible_form_state(self):
        page = MagicMock()
        with (
            patch.object(
                register, "ensure_claude_login_form", AsyncMock(return_value=True)
            ) as ensure,
            patch.object(register, "human_type", AsyncMock()),
            patch.object(register, "click_continue_email", AsyncMock(return_value=True)),
            patch.object(register.asyncio, "sleep", AsyncMock()),
            patch.object(register, "request_claude_magic_link_http") as request,
        ):
            email_input = MagicMock()
            email_input.fill = AsyncMock()
            page.locator.return_value.first = email_input
            response = MagicMock(status=200, url="https://claude.ai/api/auth/send_magic_link")
            response_info = MagicMock()
            response_info.value = AsyncMock(return_value=response)()
            response_context = AsyncMock()
            response_context.__aenter__.return_value = response_info
            response_context.__aexit__.return_value = False
            page.expect_response.return_value = response_context
            requested_at = await register.submit_claude_email(
                page, "mail@example.com"
            )

        self.assertIsNotNone(requested_at)
        ensure.assert_awaited_once()
        request.assert_not_called()

    async def test_email_submission_uses_http_only_after_visible_attempts_fail(self):
        page = MagicMock()
        with (
            patch.object(
                register, "request_claude_magic_link", AsyncMock(return_value=False)
            ) as browser_request,
            patch.object(
                register,
                "request_claude_magic_link_http",
                return_value=True,
            ) as request,
            patch.object(
                register, "ensure_claude_login_form", AsyncMock(return_value=True)
            ) as ensure,
            patch.object(register, "human_type", AsyncMock()),
            patch.object(register, "click_continue_email", AsyncMock(return_value=False)),
            patch.object(register.asyncio, "sleep", AsyncMock()),
        ):
            email_input = MagicMock()
            email_input.fill = AsyncMock()
            page.locator.return_value.first = email_input
            page.expect_response.side_effect = TimeoutError("no response")
            page.goto = AsyncMock()
            requested_at = await register.submit_claude_email(
                page, "mail@example.com"
            )

        self.assertIsNotNone(requested_at)
        browser_request.assert_awaited_once_with(page, "mail@example.com")
        request.assert_called_once_with("mail@example.com")
        self.assertEqual(ensure.await_count, 2)

    async def test_email_submission_falls_back_to_current_browser_api(self):
        page = MagicMock()
        with (
            patch.object(
                register, "ensure_claude_login_form", AsyncMock(return_value=False)
            ),
            patch.object(
                register, "request_claude_magic_link", AsyncMock(return_value=True)
            ) as browser_request,
            patch.object(register, "request_claude_magic_link_http") as http_request,
        ):
            requested_at = await register.submit_claude_email(
                page, "mail@example.com"
            )

        self.assertIsNotNone(requested_at)
        browser_request.assert_awaited_once_with(page, "mail@example.com")
        http_request.assert_not_called()
        self.assertFalse(page._rf_visible_email_submitted)

    async def test_verified_magic_link_cookies_are_added_to_browser_context(self):
        page = MagicMock()
        page.context.add_cookies = AsyncMock()
        page.goto = AsyncMock()
        verified = {
            "cookies": {"sessionKey": "session-value", "activitySessionId": "activity"},
            "response": {"success": True},
        }
        with patch.object(register.asyncio, "to_thread", AsyncMock(return_value=verified)):
            success = await register.verify_claude_magic_link_http(
                page, "https://claude.ai/magic-link#nonce:email"
            )

        self.assertTrue(success)
        added = page.context.add_cookies.call_args.args[0]
        self.assertEqual({cookie["name"] for cookie in added}, {
            "sessionKey", "activitySessionId"
        })
        page.goto.assert_awaited_once_with("https://claude.ai/", timeout=60000)

    def test_node_picker_excludes_failed_nodes(self):
        switch = MagicMock()
        switch.concrete_nodes.return_value = ["recent", "good", "failed"]
        switch.find_working_node.return_value = "good"
        with (
            patch.object(register, "proxy_switch", switch),
            patch.object(register, "_recent_claude_nodes", return_value=["recent"]),
        ):
            node = register._pick_claude_node({"failed"})

        self.assertEqual(node, "good")
        self.assertEqual(switch.find_working_node.call_args.kwargs["candidates"], ["good"])

    def test_node_picker_limits_fresh_probe_batch(self):
        switch = MagicMock()
        switch.concrete_nodes.return_value = [f"node-{index}" for index in range(20)]
        switch.find_working_node.return_value = None
        sampled = ["node-1", "node-2", "node-3"]
        with (
            patch.object(register, "proxy_switch", switch),
            patch.object(register, "CLAUDE_NODE_PROBE_LIMIT", 3),
            patch.object(register, "_recent_claude_nodes", return_value=[]),
            patch.object(register.random, "sample", return_value=sampled),
        ):
            node = register._pick_claude_node()

        self.assertIsNone(node)
        self.assertEqual(switch.find_working_node.call_count, 1)
        self.assertEqual(
            switch.find_working_node.call_args.kwargs["candidates"], sampled
        )

    def test_node_picker_falls_back_to_most_recent_known_good(self):
        switch = MagicMock()
        switch.concrete_nodes.return_value = ["fresh", "older", "latest"]
        switch.find_working_node.side_effect = [None, "latest"]
        with (
            patch.object(register, "proxy_switch", switch),
            patch.object(register, "CLAUDE_NODE_PROBE_LIMIT", 1),
            patch.object(
                register, "_recent_claude_nodes", return_value=["older", "latest"]
            ),
            patch.object(register.random, "sample", return_value=["fresh"]),
        ):
            node = register._pick_claude_node()

        self.assertEqual(node, "latest")
        self.assertEqual(switch.find_working_node.call_count, 2)
        self.assertEqual(
            switch.find_working_node.call_args_list[1].kwargs["candidates"],
            ["latest"],
        )

    def test_node_picker_deduplicates_recent_nodes(self):
        switch = MagicMock()
        switch.concrete_nodes.return_value = ["fresh", "latest"]
        switch.find_working_node.return_value = None
        with (
            patch.object(register, "proxy_switch", switch),
            patch.object(register, "CLAUDE_NODE_PROBE_LIMIT", 1),
            patch.object(
                register, "_recent_claude_nodes", return_value=["latest", "latest"]
            ),
            patch.object(register.random, "sample", return_value=["fresh"]),
        ):
            node = register._pick_claude_node()

        self.assertIsNone(node)
        self.assertEqual(switch.find_working_node.call_count, 2)
        self.assertEqual(
            switch.find_working_node.call_args_list[1].kwargs["candidates"],
            ["latest"],
        )

    def test_yescaptcha_hcaptcha_uses_sitekey_rqdata_and_browser_ua(self):
        created = MagicMock()
        created.json.return_value = {"errorId": 0, "taskId": "task-1"}
        solved = MagicMock()
        solved.json.return_value = {
            "errorId": 0,
            "status": "ready",
            "solution": {
                "gRecaptchaResponse": "captcha-token",
                "respKey": "response-key",
                "userAgent": "returned-agent",
            },
        }
        params = {
            "website_url": "https://claude.ai/magic-link",
            "sitekey": "site-key",
            "rqdata": "request-data",
            "user_agent": "browser-agent",
            "is_invisible": False,
        }
        with (
            patch.object(register, "YESCAPTCHA_API_KEY", "api-key"),
            patch.object(register.requests, "post", side_effect=[created, solved]) as post,
            patch.object(register.time, "sleep"),
        ):
            result = register._solve_hcaptcha_yescaptcha(params, max_wait=5)

        self.assertEqual(result["token"], "captcha-token")
        self.assertEqual(result["resp_key"], "response-key")
        task = post.call_args_list[0].kwargs["json"]["task"]
        self.assertEqual(task["type"], "HCaptchaTaskProxyless")
        self.assertEqual(task["websiteKey"], "site-key")
        self.assertEqual(task["rqdata"], "request-data")
        self.assertEqual(task["userAgent"], "browser-agent")

    def test_yescaptcha_hcaptcha_falls_back_to_cn_endpoint(self):
        created = MagicMock()
        created.json.return_value = {"errorId": 0, "taskId": "task-cn"}
        solved = MagicMock()
        solved.json.return_value = {
            "errorId": 0,
            "status": "ready",
            "solution": {"gRecaptchaResponse": "captcha-token"},
        }
        params = {
            "website_url": "https://claude.ai/magic-link",
            "sitekey": "site-key",
            "rqdata": "",
            "user_agent": "browser-agent",
            "is_invisible": False,
        }
        with (
            patch.object(register, "YESCAPTCHA_API_KEY", "api-key"),
            patch.object(register, "YESCAPTCHA_API_BASE", "https://api.yescaptcha.com"),
            patch.object(
                register.requests,
                "post",
                side_effect=[register.requests.exceptions.SSLError("tls"), created, solved],
            ) as post,
            patch.object(register.time, "sleep"),
        ):
            result = register._solve_hcaptcha_yescaptcha(params, max_wait=5)

        self.assertEqual(result["token"], "captcha-token")
        self.assertEqual(
            post.call_args_list[1].args[0],
            "https://cn.yescaptcha.com/createTask",
        )

    def test_yescaptcha_hcaptcha_retries_transient_result_poll_error(self):
        created = MagicMock()
        created.json.return_value = {"errorId": 0, "taskId": "task-1"}
        solved = MagicMock()
        solved.json.return_value = {
            "errorId": 0,
            "status": "ready",
            "solution": {"gRecaptchaResponse": "captcha-token"},
        }
        params = {
            "website_url": "https://claude.ai/magic-link",
            "sitekey": "site-key",
            "rqdata": "",
            "user_agent": "browser-agent",
            "is_invisible": False,
        }
        with (
            patch.object(register, "YESCAPTCHA_API_KEY", "api-key"),
            patch.object(
                register.requests,
                "post",
                side_effect=[
                    created,
                    register.requests.exceptions.SSLError("transient tls"),
                    solved,
                ],
            ) as post,
            patch.object(register.time, "sleep"),
        ):
            result = register._solve_hcaptcha_yescaptcha(params, max_wait=5)

        self.assertEqual(result["token"], "captcha-token")
        self.assertEqual(post.call_count, 3)

    def test_claude_hcaptcha_retries_transient_solver_failure(self):
        solved = {"token": "captcha-token", "resp_key": "", "user_agent": ""}
        with (
            patch.object(register, "CLAUDE_HCAPTCHA_SOLVE_RETRIES", 2),
            patch.object(
                register, "_solve_hcaptcha_yescaptcha", side_effect=[None, solved]
            ) as solve,
        ):
            result = register._solve_claude_hcaptcha_yescaptcha({"sitekey": "key"})

        self.assertEqual(result, solved)
        self.assertEqual(solve.call_count, 2)

    async def test_hcaptcha_token_is_injected_into_callback_and_fields(self):
        frame = MagicMock()
        frame.evaluate = AsyncMock(return_value={"fields": 2, "callback": True})
        page = MagicMock()
        page.frames = [frame]

        injected = await register._inject_claude_hcaptcha_solution(
            page, {"token": "captcha-token", "resp_key": "response-key"}
        )

        self.assertTrue(injected)
        payload = frame.evaluate.call_args.args[1]
        self.assertEqual(payload["token"], "captcha-token")
        self.assertEqual(payload["respKey"], "response-key")

    async def test_claude_hcaptcha_prefers_native_vision_solver(self):
        page = MagicMock()
        page.screenshot = AsyncMock()
        with (
            patch.object(register, "_claude_hcaptcha_present", AsyncMock(return_value=True)),
            patch.object(
                register, "_solve_claude_hcaptcha_vision", AsyncMock(return_value=True)
            ) as vision,
        ):
            solved = await register.solve_claude_hcaptcha(page)

        self.assertTrue(solved)
        vision.assert_awaited_once_with(page)

    async def test_hidden_hcaptcha_frame_is_deferred_until_canvas_is_visible(self):
        page = MagicMock()
        page.frames = []
        page.screenshot = AsyncMock()
        with (
            patch.object(register, "_claude_hcaptcha_present", AsyncMock(return_value=True)),
            patch.object(
                register, "_solve_claude_hcaptcha_vision", AsyncMock(return_value=None)
            ),
        ):
            solved = await register.solve_claude_hcaptcha(page)

        self.assertIsNone(solved)

    async def test_vision_driver_prefers_matching_frame_with_visible_canvas(self):
        from vision_solver import drivers

        hidden = MagicMock()
        hidden.url = "https://hcaptcha.test/?frame=challenge&preload=1"
        hidden_canvas = MagicMock()
        hidden_canvas.first = hidden_canvas
        hidden_canvas.count = AsyncMock(return_value=1)
        hidden_canvas.bounding_box = AsyncMock(return_value=None)
        hidden.locator.return_value = hidden_canvas

        visible = MagicMock()
        visible.url = "https://hcaptcha.test/?frame=challenge"
        visible_canvas = MagicMock()
        visible_canvas.first = visible_canvas
        visible_canvas.count = AsyncMock(return_value=1)
        visible_canvas.bounding_box = AsyncMock(
            return_value={"width": 500, "height": 320}
        )
        visible.locator.return_value = visible_canvas

        page = MagicMock()
        page.frames = [hidden, visible]

        selected = await drivers._find_frame(page, ["frame=challenge"])

        self.assertIs(selected, visible)

    async def test_claude_vision_switches_korean_drag_challenge_to_drag_driver(self):
        challenge_frame = MagicMock()
        prompt = MagicMock()
        prompt.first = prompt
        prompt.inner_text = AsyncMock(
            return_value="\uc544\uc774\ucf58\uc744 \uc62c\ubc14\ub978 \uc704\uce58\uc5d0 \ub04c\uc5b4\ub2e4 \ub193\uc73c\uc138\uc694."
        )
        challenge_frame.locator.return_value = prompt
        page = MagicMock()

        async def check_driver(_page, spec, **_kwargs):
            self.assertEqual(spec.mode, "canvas_drag")
            self.assertEqual(spec.prompt, "")
            return False

        with (
            patch.object(register, "dismiss_claude_cookie_banner", AsyncMock()),
            patch.object(
                register, "_visible_claude_hcaptcha_frame",
                AsyncMock(return_value=challenge_frame),
            ),
            patch.object(
                register, "_select_claude_vision_voter",
                return_value=("https://vision.example", "key", "model"),
            ),
            patch("vision_solver.solve", AsyncMock(side_effect=check_driver)),
        ):
            solved = await register._solve_claude_hcaptcha_vision(page)

        self.assertFalse(solved)

    async def test_claude_vision_applies_chain_prompt_to_canvas_driver(self):
        from vision_solver import vision as vision_backend

        challenge_frame = MagicMock()
        prompt = MagicMock()
        prompt.first = prompt
        prompt.inner_text = AsyncMock(
            return_value="\uac00\uc7a5 \uc9e7\uc740 \ub450 \uac1c\uc758 \uccb4\uc778\uc744 \ud074\ub9ad\ud558\uc138\uc694"
        )
        tiles = MagicMock()
        tiles.count = AsyncMock(return_value=0)
        challenge_frame.locator.side_effect = lambda selector: (
            tiles if selector == ".task-image" else prompt
        )
        page = MagicMock()

        async def check_driver(_page, spec, **_kwargs):
            self.assertEqual(spec.mode, "canvas_grid")
            self.assertIn("TWO shortest chains", spec.prompt)
            self.assertIn("PICK=[a,b]", spec.prompt)
            self.assertEqual(spec.answer_max_tokens, 100)
            self.assertEqual(spec.deadline, 110)
            self.assertEqual(spec.answer_format, "SHORTEST_BEAD_CHAINS")
            self.assertEqual(
                [candidate[2] for candidate in vision_backend.VOTER_MODELS],
                ["gpt-5.5", "model"],
            )
            return False

        with (
            patch.object(register, "dismiss_claude_cookie_banner", AsyncMock()),
            patch.object(
                register, "_visible_claude_hcaptcha_frame",
                AsyncMock(return_value=challenge_frame),
            ),
            patch.object(
                register, "_select_claude_vision_voter",
                return_value=("https://vision.example", "key", "model"),
            ),
            patch.object(vision_backend, "PRIMARY_MODEL", "gpt-5.5"),
            patch.object(vision_backend, "VOTER_MODELS", []),
            patch("vision_solver.solve", AsyncMock(side_effect=check_driver)),
        ):
            solved = await register._solve_claude_hcaptcha_vision(page)

        self.assertFalse(solved)

    async def test_claude_vision_applies_two_point_mode_to_korean_lines(self):
        from vision_solver import vision as vision_backend

        challenge_frame = MagicMock()
        prompt = MagicMock()
        prompt.first = prompt
        prompt.inner_text = AsyncMock(
            return_value="\uac00\uc7a5 \uc9e7\uc740 \uc120 \ub450 \uac1c\ub97c \ud074\ub9ad\ud574 \uc8fc\uc138\uc694"
        )
        tiles = MagicMock()
        tiles.count = AsyncMock(return_value=0)
        challenge_frame.locator.side_effect = lambda selector: (
            tiles if selector == ".task-image" else prompt
        )
        page = MagicMock()

        async def check_driver(_page, spec, **_kwargs):
            self.assertEqual(spec.mode, "canvas_grid")
            self.assertEqual(spec.answer_format, "TWO_POINTS")
            self.assertIn("FROM=(x1,y1) TO=(x2,y2)", spec.prompt)
            self.assertEqual(
                [candidate[2] for candidate in vision_backend.VOTER_MODELS],
                ["gpt-5.5", "model", "gpt-5.5"],
            )
            self.assertEqual(
                [candidate[0] for candidate in vision_backend.VOTER_MODELS],
                [
                    "https://vision.example",
                    "https://vision.example",
                    "https://backup.example",
                ],
            )
            return False

        with (
            patch.object(register, "dismiss_claude_cookie_banner", AsyncMock()),
            patch.object(
                register, "_visible_claude_hcaptcha_frame",
                AsyncMock(return_value=challenge_frame),
            ),
            patch.object(
                register, "_select_claude_vision_voter",
                return_value=("https://vision.example", "key", "model"),
            ),
            patch.object(vision_backend, "PRIMARY_MODEL", "gpt-5.5"),
            patch.object(
                vision_backend,
                "VOTER_MODELS",
                [("https://backup.example", "backup-key", "gpt-5.5")],
            ),
            patch("vision_solver.solve", AsyncMock(side_effect=check_driver)),
        ):
            solved = await register._solve_claude_hcaptcha_vision(page)

        self.assertFalse(solved)

    def test_claude_vision_keeps_requested_agent_captcha_model(self):
        from common import agent_captcha

        response = MagicMock(status_code=200)
        response.json.return_value = {
            "data": [{"id": "gemini-3.5-flash-c"}]
        }
        with (
            patch.object(register, "CLAUDE_VISION_API_BASE", ""),
            patch.object(register, "CLAUDE_VISION_API_KEY", ""),
            patch.object(register, "CLAUDE_VISION_MODEL", "gemini-3.6-flash"),
            patch.object(agent_captcha, "ZZ_BASE", "https://vision.example"),
            patch.object(agent_captcha, "ZZ_KEY", "key"),
            patch.object(agent_captcha, "VISION_API_BASE", ""),
            patch.object(agent_captcha, "VISION_API_KEY", ""),
            patch.object(register.requests, "get", return_value=response),
        ):
            voter = register._select_claude_vision_voter()

        self.assertEqual(voter, (
            "https://vision.example", "key", "gemini-3.6-flash"
        ))

    def test_agent_captcha_retries_transient_gateway_errors(self):
        from vision_solver import vision

        response = MagicMock(status_code=200)
        response.json.return_value = {
            "choices": [{"message": {"content": "FROM=(0.2,0.3) TO=(0.7,0.8)"}}]
        }
        with (
            patch.object(
                vision.requests,
                "post",
                side_effect=[
                    vision.requests.exceptions.ConnectionError("reset"),
                    vision.requests.exceptions.SSLError("tls"),
                    response,
                ],
            ) as post,
            patch.object(vision.time, "sleep"),
        ):
            answer = vision._ask_one(
                "https://vision.example", "key", "gemini-3.6-flash",
                "drag puzzle", "aW1hZ2U="
            )

        self.assertIn("FROM=", answer)
        self.assertEqual(post.call_count, 3)
        self.assertEqual(
            post.call_args.kwargs["json"]["model"], "gemini-3.6-flash"
        )

    async def test_magic_link_is_verified_in_pending_browser_session(self):
        page = MagicMock()
        page.url = "https://claude.ai/magic-link#nonce-value:encoded-email"
        page._rf_magic_verify_template = {
            "url": "https://claude.ai/api/auth/verify_magic_link",
            "headers": {"content-type": "application/json"},
            "post_data": json.dumps({
                "arkose_session_token": "native-arkose-token",
                "source": "claude",
            }),
        }
        page.evaluate = AsyncMock(return_value={"ok": True, "status": 200, "error": ""})
        page.goto = AsyncMock()
        with patch.object(register.asyncio, "sleep", AsyncMock()):
            verified = await register._verify_claude_magic_link_browser_api(
                page, {"token": "captcha-token"}
            )

        self.assertTrue(verified)
        request = page.evaluate.call_args.args[1]
        payload = request["payload"]
        self.assertEqual(payload["credentials"]["nonce"], "nonce-value")
        self.assertEqual(
            payload["credentials"]["encoded_email_address"], "encoded-email"
        )
        self.assertEqual(
            payload["arkose_session_token"], "native-arkose-token"
        )
        self.assertEqual(
            payload["client_attestation"]["hcaptcha_token"], "captcha-token"
        )
        self.assertEqual(payload["source"], "claude")
        self.assertNotIn("oauth_client_id", payload)
        script = page.evaluate.call_args.args[0]
        self.assertIn("fetch(endpoint", script)
        page.goto.assert_awaited_once_with("https://claude.ai/", timeout=60000)

    async def test_magic_link_verification_replays_captured_request_shape(self):
        page = MagicMock()
        page.url = "https://claude.ai/"
        page._rf_magic_verify_template = {
            "url": "https://claude.ai/api/auth/verify_magic_link",
            "headers": {
                "content-type": "application/json",
                "anthropic-client-version": "web-test",
                "cookie": "must-not-be-replayed",
            },
            "post_data": json.dumps({
                "credentials": {"method": "nonce"},
                "locale": "ko-KR",
                "arkose_session_token": "expired-token",
                "source": "claude",
            }),
        }
        page.evaluate = AsyncMock(
            return_value={"ok": False, "status": 403, "error": ""}
        )

        verified = await register._verify_claude_magic_link_browser_api(
            page,
            {"token": "captcha-token"},
            magic_link="https://claude.ai/magic-link#nonce-value:encoded-email",
        )

        self.assertFalse(verified)
        request = page.evaluate.call_args.args[1]
        self.assertEqual(request["payload"]["source"], "claude")
        self.assertEqual(request["payload"]["locale"], "ko-KR")
        self.assertEqual(
            request["payload"]["arkose_session_token"], "expired-token"
        )
        self.assertEqual(
            request["payload"]["client_attestation"]["hcaptcha_token"],
            "captcha-token",
        )
        self.assertEqual(
            request["headers"]["anthropic-client-version"], "web-test"
        )
        self.assertNotIn("cookie", request["headers"])


if __name__ == "__main__":
    unittest.main()
