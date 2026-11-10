"""Test the shared CaptchaNo client without contacting a solver."""

import os
import unittest
from unittest.mock import Mock, patch

from bcn_half import captcha


class CaptchanoTest(unittest.TestCase):
    def test_configured_key_is_redacted_from_errors(self):
        with patch.dict(os.environ, {"CAPTCHANO_API_KEY": "private-test-key"}):
            self.assertEqual(
                captcha.failure_reason(RuntimeError("bad private-test-key")),
                "bad [redacted]",
            )

    def test_existing_token_does_not_create_a_paid_task(self):
        page = Mock()
        page.locator.return_value.count.return_value = 1
        page.evaluate.return_value = True
        with patch("bcn_half.captcha.request_solution") as request:
            self.assertEqual(captcha.handle_captcha(page), "verified-manually")
        request.assert_not_called()

    def test_requests_a_token_for_the_actual_application_url(self):
        payload = {"status": "ready", "solution": {"token": "t" * 120}}
        with patch.dict(
            os.environ, {"CAPTCHANO_API_KEY": "private-test-key"}, clear=True
        ):
            with patch("bcn_half.captcha._request", return_value=payload) as request:
                solution = captcha.request_solution(
                    "https://example.org/event", "sitekey", "browser-agent", 30
                )
        self.assertEqual(solution["token"], "t" * 120)
        self.assertEqual(request.call_args.args[1], "/v1/solve")
        self.assertEqual(request.call_args.args[3]["url"], "https://example.org/event")
        self.assertEqual(request.call_args.args[3]["userAgent"], "browser-agent")

    def test_queued_task_is_polled_without_creating_another_solve(self):
        responses = [
            {"status": "solving", "id": "slv_test"},
            {"status": "ready", "solution": {"token": "t" * 120}},
        ]
        with patch.dict(os.environ, {"CAPTCHANO_KEY": "private-test-key"}, clear=True):
            with patch("bcn_half.captcha._request", side_effect=responses) as request:
                with patch("bcn_half.captcha.time.sleep"):
                    captcha.request_solution(
                        "https://example.org", "sitekey", "browser-agent", 30
                    )
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.args[1], "/v1/task/slv_test")

    def test_failed_task_does_not_echo_its_message(self):
        payload = {
            "status": "failed",
            "error": {"code": "unsolvable", "message": "private-test-key"},
        }
        with patch.dict(
            os.environ, {"CAPTCHANO_API_KEY": "private-test-key"}, clear=True
        ):
            with patch("bcn_half.captcha._request", return_value=payload):
                with self.assertRaisesRegex(RuntimeError, "^CaptchaNo: unsolvable$"):
                    captcha.request_solution(
                        "https://example.org", "sitekey", "browser-agent", 30
                    )

    def test_provider_ready_without_token_is_rejected(self):
        with patch.dict(
            os.environ, {"CAPTCHANO_API_KEY": "private-test-key"}, clear=True
        ):
            with patch(
                "bcn_half.captcha._request",
                return_value={"status": "ready", "solution": {}},
            ):
                with self.assertRaisesRegex(ValueError, "Token"):
                    captcha.request_solution(
                        "https://example.org", "sitekey", "browser-agent", 30
                    )

    def test_polling_obeys_deadline(self):
        with patch.dict(
            os.environ, {"CAPTCHANO_API_KEY": "private-test-key"}, clear=True
        ):
            with patch(
                "bcn_half.captcha._request",
                return_value={"status": "solving", "id": "slv_test"},
            ) as request:
                with patch("bcn_half.captcha.time.monotonic", side_effect=[0, 31]):
                    with self.assertRaises(TimeoutError):
                        captcha.request_solution(
                            "https://example.org", "sitekey", "browser-agent", 30
                        )
        request.assert_called_once()

    def test_handler_requests_token_by_default_when_challenge_exists(self):
        page = Mock()
        page.url = "https://example.org/application"
        page.locator.return_value.count.return_value = 1
        page.evaluate.side_effect = [False, "browser-agent"]
        with patch("bcn_half.captcha._sitekey", return_value="sitekey"):
            with patch(
                "bcn_half.captcha.request_solution", return_value={"token": "t" * 120}
            ) as request:
                with patch("bcn_half.captcha.apply_solution") as apply:
                    self.assertEqual(captcha.handle_captcha(page), "token-ready")
        self.assertEqual(request.call_args.args[0], page.url)
        apply.assert_called_once()

    def test_no_widget_does_not_request_a_token(self):
        page = Mock()
        page.locator.return_value.count.return_value = 0
        with patch("bcn_half.captcha.request_solution") as request:
            self.assertEqual(captcha.handle_captcha(page), "not-present")
        request.assert_not_called()

    def test_failed_solver_allows_manual_completion(self):
        page = Mock()
        page.locator.return_value.count.return_value = 1
        page.evaluate.side_effect = [False, "browser-agent"]
        with patch("bcn_half.captcha._sitekey", return_value="sitekey"):
            with patch(
                "bcn_half.captcha.request_solution",
                side_effect=RuntimeError("unsolvable"),
            ):
                with patch("bcn_half.captcha.wait_verified", return_value=True):
                    self.assertEqual(captcha.handle_captcha(page), "verified-manually")
