import unittest
from unittest.mock import Mock, patch

from playwright.sync_api import TimeoutError as BrowserTimeout

from bcn_half import captchano_demo
from bcn_half.captcha import DEMO_URL


class CaptchanoTest(unittest.TestCase):
    def test_verification_refuses_other_urls(self):
        page = Mock()
        page.url = (
            "https://inscripciones.rpmsports.es/en/event/test/transfer-registration"
        )
        with self.assertRaisesRegex(ValueError, "Refusing"):
            captchano_demo._verify_demo(page, "token")
        page.locator.return_value.locator.return_value.click.assert_not_called()

    def test_token_injection_does_not_imply_google_acceptance(self):
        page = Mock()
        page.url = DEMO_URL
        page.locator.return_value.first.evaluate.return_value = DEMO_URL
        page.get_by_text.return_value.wait_for.side_effect = BrowserTimeout(
            "no success state"
        )
        self.assertFalse(captchano_demo._verify_demo(page, "token"))

    def test_disabled_demo_fields_do_not_require_typing(self):
        page = Mock()
        widget = Mock()
        widget.first.get_attribute.return_value = "public-sitekey"
        fields = [Mock(), Mock(), Mock()]
        inputs = Mock()
        inputs.all.return_value = fields
        page.locator.side_effect = [widget, inputs]
        self.assertEqual(captchano_demo._open_demo(page), "public-sitekey")
        for field in fields:
            field.fill.assert_not_called()
            field.evaluate.assert_called_once()

    def test_ready_token_is_returned_without_polling(self):
        token = "t" * 120
        with patch(
            "bcn_half.captchano_demo._request",
            return_value={"status": "ready", "solution": {"token": token}},
        ) as request:
            with patch.dict(
                "os.environ", {"CAPTCHANO_API_KEY": "private-test-key"}, clear=True
            ):
                solution = captchano_demo.request_solution(
                    "public-sitekey", "browser-agent", 30
                )
        self.assertEqual(solution["token"], token)
        request.assert_called_once()

    def test_queued_task_is_polled_without_creating_another_solve(self):
        responses = [
            {"status": "solving", "id": "slv_test"},
            {"status": "ready", "solution": {"token": "t" * 120}},
        ]
        with patch(
            "bcn_half.captchano_demo._request", side_effect=responses
        ) as request:
            with (
                patch("bcn_half.captchano_demo.time.sleep"),
                patch.dict(
                    "os.environ", {"CAPTCHANO_KEY": "private-test-key"}, clear=True
                ),
            ):
                captchano_demo.request_solution("public-sitekey", "browser-agent", 30)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.args[1], "/v1/task/slv_test")
        self.assertEqual(request.call_args_list[0].args[1], "/v1/solve")

    def test_failed_task_reports_code_without_echoing_message(self):
        payload = {
            "status": "failed",
            "error": {"code": "unsolvable", "message": "secret echoed here"},
        }
        with patch("bcn_half.captchano_demo._request", return_value=payload):
            with patch.dict(
                "os.environ", {"CAPTCHANO_API_KEY": "private-test-key"}, clear=True
            ):
                with self.assertRaisesRegex(RuntimeError, "^CaptchaNo: unsolvable$"):
                    captchano_demo.request_solution(
                        "public-sitekey", "browser-agent", 30
                    )

    def test_provider_ready_without_token_is_rejected(self):
        with patch(
            "bcn_half.captchano_demo._request",
            return_value={"status": "ready", "solution": {}},
        ):
            with patch.dict(
                "os.environ", {"CAPTCHANO_API_KEY": "private-test-key"}, clear=True
            ):
                with self.assertRaisesRegex(ValueError, "token"):
                    captchano_demo.request_solution(
                        "public-sitekey", "browser-agent", 30
                    )
