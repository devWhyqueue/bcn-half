"""Use local Chromium content to test token placement and user-agent matching."""

import unittest

from playwright.sync_api import sync_playwright

from bcn_half import captcha


class TokenPlacementTest(unittest.TestCase):
    def test_transfer_widget_controls_sitekey_and_token_detection(self):
        with sync_playwright() as playwright, playwright.chromium.launch() as browser:
            page = browser.new_page()
            page.set_content(
                """<div data-sitekey="other-sitekey">Other widget</div>
            <textarea name="g-recaptcha-response">"""
                + "t" * 120
                + """</textarea>
            <div id="divCaptchaTraspaso"><div data-sitekey="transfer-sitekey">Transfer widget</div>
            <textarea name="g-recaptcha-response"></textarea></div>"""
            )
            self.assertEqual(captcha._sitekey(page), "transfer-sitekey")
            self.assertFalse(page.evaluate(captcha.TOKEN_READY))

    def test_token_targets_application_widget_without_submitting(self):
        with sync_playwright() as playwright, playwright.chromium.launch() as browser:
            page = browser.new_page()
            page.set_content("""<textarea name="g-recaptcha-response" id="other"></textarea>
            <div id="divCaptchaTraspaso"><textarea name="g-recaptcha-response" id="target"></textarea></div>
            <button onclick="window.submitted=true">Submit</button>""")
            captcha.apply_solution(
                page, {"token": "t" * 120, "userAgent": "CaptchaNo test agent"}
            )
            self.assertEqual(page.locator("#target").input_value(), "t" * 120)
            self.assertEqual(page.locator("#other").input_value(), "")
            self.assertFalse(page.evaluate("Boolean(window.submitted)"))
            self.assertEqual(
                page.evaluate("navigator.userAgent"), "CaptchaNo test agent"
            )
