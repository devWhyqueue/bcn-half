import os
import unittest
from unittest.mock import Mock, patch

from bcn_half import captcha


class CaptchaTest(unittest.TestCase):
    def test_diagnostic_loop_stops_when_google_verifies(self):
        page = Mock()
        with patch("bcn_half.captcha.wait_verified", side_effect=[False, False, True]):
            with patch("bcn_half.captcha._solve_round") as recognize:
                self.assertTrue(captcha.solve(page, timeout=600, max_rounds=50))
        self.assertEqual(recognize.call_count, 2)

    def test_diagnostic_loop_stops_at_round_limit(self):
        page = Mock()
        with patch("bcn_half.captcha.wait_verified", return_value=False):
            with patch("bcn_half.captcha._solve_round") as recognize:
                self.assertFalse(captcha.solve(page, timeout=600, max_rounds=2))
        self.assertEqual(recognize.call_count, 2)

    def test_stalled_image_obeys_deadline(self):
        table = Mock()
        table.evaluate.return_value = None
        with patch("bcn_half.captcha.time.monotonic", side_effect=[0, 0, 0, 2]):
            with self.assertRaises(TimeoutError):
                captcha._wait_grid_ready(table, 1)
        table.evaluate.assert_called_once()

    def test_answer_for_changed_grid_is_discarded(self):
        challenge = Mock()
        table = challenge.locator.return_value.first
        table.screenshot.return_value = b"grid"
        table.evaluate.return_value = ["replacement"] * 9
        challenge.locator.return_value.inner_text.return_value = (
            "Select all images with\ncars"
        )
        with patch("bcn_half.captcha._wait_grid_ready", return_value=["original"] * 9):
            with (
                patch("bcn_half.captcha._recognize", return_value=[True] * 9),
                patch("bcn_half.captcha._click_answer") as click,
            ):
                captcha._solve_round(challenge, 100000000000, set())
        click.assert_not_called()

    def test_successful_solver_returns_verified_status(self):
        page = Mock()
        with patch("bcn_half.captcha.solve", return_value=True):
            self.assertEqual(
                captcha.handle_captcha(page, automatic=True), "verified-client-token"
            )

    def test_failure_reason_redacts_configured_key(self):
        with patch.dict(os.environ, {"SOLVECHA_API_KEY": "private-test-key"}):
            self.assertEqual(
                captcha.failure_reason(RuntimeError("bad private-test-key")),
                "bad [redacted]",
            )

    def test_dynamic_replacement_waits_before_verifying(self):
        challenge = Mock()
        with patch("bcn_half.captcha._wait_grid_ready") as wait:
            captcha._click_answer(
                challenge, [True] + [False] * 8, 100000000000, dynamic=True
            )
        wait.assert_called_once()
        self.assertNotIn(
            "#recaptcha-verify-button",
            [call.args[0] for call in challenge.locator.call_args_list],
        )

    def test_dynamic_grid_verifies_once_no_matches_remain(self):
        challenge = Mock()
        captcha._click_answer(challenge, [False] * 9, 100000000000, dynamic=True)
        challenge.locator.assert_any_call("#recaptcha-verify-button")

    def test_no_challenge_never_calls_solver(self):
        page = Mock()
        page.locator.return_value.count.return_value = 0
        with patch("bcn_half.captcha.solve") as solve:
            self.assertEqual(
                captcha.handle_captcha(page, automatic=True), "not-present"
            )
        solve.assert_not_called()

    def test_solver_disabled_without_live_permission(self):
        page = Mock()
        with (
            patch("bcn_half.captcha.solve") as solve,
            patch("bcn_half.captcha.wait_verified", return_value=False),
        ):
            self.assertEqual(
                captcha.handle_captcha(page, automatic=False, timeout=1),
                "manual-required",
            )
        solve.assert_not_called()

    def test_manual_fallback_after_failed_solver(self):
        page = Mock()
        with patch("bcn_half.captcha.solve", side_effect=RuntimeError("failed")):
            with patch("bcn_half.captcha.wait_verified", return_value=True):
                self.assertEqual(
                    captcha.handle_captcha(page, automatic=True, timeout=1),
                    "verified-manually",
                )

    def test_malformed_solver_answer_is_rejected(self):
        for data in ([True], [1] * 9, [True] * 16, {"data": []}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                captcha.validate_answer(data, 9)
