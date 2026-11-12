"""Local reCAPTCHA solving with PyPasser's audio challenge and offline Vosk."""

import logging
import os

from patchright.sync_api import Error, Locator, Page
from pypasser.captcha import recaptcha
from pypasser.captcha.recaptcha.v2 import flow
from pypasser.exceptions import IpBlock, PyPasserError

log = logging.getLogger("bcn-half")
TOKEN_READY = """() => {
    const scope = document.querySelector('#divCaptchaTraspaso') || document;
    return [...scope.querySelectorAll('[name="g-recaptcha-response"]')]
        .some(field => field.value.length > 100);
}"""
CHALLENGE = (
    'iframe[src*="/recaptcha/api2/anchor"]:visible, '
    'iframe[src*="/recaptcha/enterprise/anchor"]:visible'
)
SOLVE_ERRORS = (PyPasserError, Error, OSError, ValueError, RuntimeError, ImportError)


def failure_reason(error: BaseException) -> str:
    """Keep diagnostic reasons while removing configured credentials."""
    reason = str(error).splitlines()[0] if str(error) else type(error).__name__
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS"):
        secret = os.environ.get(name)
        if secret:
            reason = reason.replace(secret, "[redacted]")
    return reason


def configure_solver() -> None:
    """Point PyPasser at this site's frames and transfer token field.

    Frame titles are localised, and the transfer widget is the page's second
    reCAPTCHA; PyPasser's default token selector would wait forever.
    """
    flow.CHECKBOX_FRAME = "iframe[src*='/recaptcha/api2/anchor']"
    flow.AUDIO_FRAME = "iframe[src*='/recaptcha/api2/bframe']"
    flow.TOKEN_SELECTORS = ("#divCaptchaTraspaso [name='g-recaptcha-response']",)


def _widget(page: Page) -> Page | Locator:
    transfer = page.locator("#divCaptchaTraspaso")
    return transfer if transfer.count() else page


def _has_widget(page: Page) -> bool:
    widget = _widget(page)
    challenges = widget.locator(CHALLENGE).count()
    return bool(challenges or widget.locator("[data-sitekey]:visible").count())


def handle_captcha(page: Page) -> str:
    """Solve the CAPTCHA once, only when the application has a widget."""
    if not _has_widget(page):
        return "not-present"
    if page.evaluate(TOKEN_READY) is True:
        return "verified-manually"
    configure_solver()
    try:
        solved = recaptcha.v2.solve(page, backend="patchright").is_solved
    except IpBlock:
        log.warning("Google hat die CAPTCHA-Audioaufgabe blockiert")
        return "google-blocked"
    except SOLVE_ERRORS as error:
        log.warning(
            "CAPTCHA-Lösung fehlgeschlagen (%s): %s",
            type(error).__name__,
            failure_reason(error),
        )
        return "solve-failed"
    if not solved:
        log.warning("CAPTCHA wurde nicht gelöst")
        return "solve-failed"
    return "token-ready"
