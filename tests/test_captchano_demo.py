import unittest
from unittest.mock import Mock

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
            captchano_demo._verify_demo(page, {"token": "t" * 120})
        page.locator.return_value.locator.return_value.click.assert_not_called()

    def test_token_injection_does_not_imply_google_acceptance(self):
        page = Mock()
        page.url = DEMO_URL
        page.locator.return_value.first.evaluate.return_value = DEMO_URL
        page.get_by_text.return_value.wait_for.side_effect = BrowserTimeout(
            "no success state"
        )
        self.assertFalse(captchano_demo._verify_demo(page, {"token": "t" * 120}))

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
