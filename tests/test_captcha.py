"""Check the local reCAPTCHA solver wrapper without contacting Google."""

import os
import threading
from unittest.mock import Mock, patch

import pytest
from patchright.sync_api import sync_playwright
from pypasser.captcha.recaptcha.v2 import flow
from pypasser.exceptions import AudioDecodeError, IpBlock

from bcn_half import captcha

SOLVE = "bcn_half.captcha.flow.V2Solver.solve"


def _page(widgets: int, token_ready: bool = False) -> Mock:
    page = Mock()
    page.locator.return_value.count.return_value = widgets
    page.locator.return_value.locator.return_value.count.return_value = widgets
    page.evaluate.return_value = token_ready
    return page


def test_no_widget_skips_solver():
    with patch(SOLVE) as solve:
        assert captcha.handle_captcha(_page(0)) == "not-present"
    solve.assert_not_called()


def test_existing_token_skips_solver():
    with patch(SOLVE) as solve:
        assert captcha.handle_captcha(_page(1, token_ready=True)) == "verified-manually"
    solve.assert_not_called()


def test_solved_token_is_ready():
    page = _page(1)
    with patch(SOLVE, return_value=Mock(is_solved=True)) as solve:
        assert captcha.handle_captcha(page) == "token-ready"
    solve.assert_called_once()
    assert solve.call_args.args[0].page is page


def test_ip_block_is_reported():
    with patch(SOLVE, side_effect=IpBlock()):
        assert captcha.handle_captcha(_page(1)) == "google-blocked"


@pytest.mark.parametrize(
    "outcome",
    [{"return_value": Mock(is_solved=False)}, {"side_effect": AudioDecodeError("x")}],
)
def test_unsolved_or_broken_solve_fails(outcome):
    with patch(SOLVE, **outcome):
        assert captcha.handle_captcha(_page(1)) == "solve-failed"


def test_telegram_token_is_redacted():
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "private"}, clear=True):
        assert captcha.failure_reason(RuntimeError("bad private")) == "bad [redacted]"


def test_overrides_match_transfer_widget():
    captcha.configure_solver()
    with sync_playwright() as playwright, playwright.chromium.launch() as browser:
        page = browser.new_page()
        page.set_content(
            '<textarea name="g-recaptcha-response" id="g-recaptcha-response">'
            '</textarea><div id="divCaptchaTraspaso"><textarea '
            'name="g-recaptcha-response" id="g-recaptcha-response-1"></textarea></div>'
        )
        field = page.locator(flow.TOKEN_SELECTORS[0])
        assert field.count() == 1
        assert field.get_attribute("id") == "g-recaptcha-response-1"
    assert "/recaptcha/api2/anchor" in flow.CHECKBOX_FRAME
    assert "/recaptcha/api2/bframe" in flow.AUDIO_FRAME


def _solve_widget_outside_transfer(outcome: list[str]) -> None:
    def anchor(route):
        route.fulfill(
            content_type="text/html", body='<span id="recaptcha-anchor">x</span>'
        )

    with sync_playwright() as playwright, playwright.chromium.launch() as browser:
        page = browser.new_page()
        page.route("https://www.google.com/recaptcha/api2/anchor*", anchor)
        page.set_content(
            '<div data-sitekey="k"><iframe src="https://www.google.com/recaptcha/'
            'api2/anchor?k=k"></iframe><textarea name="g-recaptcha-response">'
            "</textarea></div>"
        )
        page.set_default_timeout(30000)
        outcome.append(captcha.handle_captcha(page))


def test_unmatched_token_field_fails_instead_of_hanging():
    outcome: list[str] = []
    worker = threading.Thread(
        target=_solve_widget_outside_transfer, args=(outcome,), daemon=True
    )
    worker.start()
    worker.join(90)
    assert outcome == ["solve-failed"]
