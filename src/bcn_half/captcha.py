"""CaptchaNo token solving with a bounded task deadline and manual fallback."""

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from patchright.sync_api import Error, Locator, Page
from patchright.sync_api import TimeoutError as BrowserTimeout

from bcn_half.marketplace import UA

log = logging.getLogger("bcn-half")
DEMO_URL = "https://www.google.com/recaptcha/api2/demo"
CAPTCHA_TIMEOUT = 300
TOKEN_READY = """() => {
    const scope = document.querySelector('#divCaptchaTraspaso') || document;
    return [...scope.querySelectorAll('[name="g-recaptcha-response"]')]
        .some(field => field.value.length > 100);
}"""
CHALLENGE = (
    'iframe[src*="/recaptcha/api2/anchor"]:visible, '
    'iframe[src*="/recaptcha/enterprise/anchor"]:visible'
)


def failure_reason(error: BaseException) -> str:
    """Keep diagnostic reasons while removing configured credentials."""
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    reason = str(error).splitlines()[0] if str(error) else type(error).__name__
    for name in (
        "CAPTCHANO_API_KEY",
        "CAPTCHANO_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_IDS",
    ):
        secret = os.environ.get(name)
        if secret:
            reason = reason.replace(secret, "[redacted]")
    return reason


def wait_verified(page: Page, timeout: float) -> bool:
    """Wait for a client verification token; never submit the enclosing form."""
    try:
        page.wait_for_function(TOKEN_READY, timeout=max(1, timeout * 1000))
        return True
    except BrowserTimeout:
        return False


def _error_code(payload: dict) -> str:
    error = payload.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    return (
        code
        if isinstance(code, str) and re.fullmatch("[a-z_]{1,64}", code)
        else "provider_error"
    )


def _request(key: str, path: str, remaining: float, body: dict | None = None) -> dict:
    if remaining <= 0:
        raise TimeoutError("CaptchaNo deadline reached")
    request = urllib.request.Request(
        "https://api.captchano.com" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": UA,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=min(30, remaining)) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        try:
            payload = json.load(error)
        except (ValueError, OSError):
            payload = {}
        code = _error_code(payload) if isinstance(payload, dict) else "provider_error"
        response_type = error.headers.get("Content-Type", "unknown").split(";")[0]
        raise RuntimeError(
            f"CaptchaNo HTTP {error.code}: {code} ({response_type})"
        ) from None
    if not isinstance(payload, dict):
        raise ValueError("CaptchaNo response must be an object")
    return payload


def _ready_solution(payload: dict) -> dict | None:
    """Return a ready solution, None while the task is still solving, or raise."""
    status = payload.get("status")
    if status == "ready":
        solution = payload.get("solution")
        token = solution.get("token") if isinstance(solution, dict) else None
        if (
            not isinstance(solution, dict)
            or not isinstance(token, str)
            or len(token) < 100
        ):
            raise ValueError("CaptchaNo-Antwort enthält kein brauchbares Token")
        return solution
    if status == "failed" or payload.get("error"):
        raise RuntimeError("CaptchaNo: " + _error_code(payload))
    task_id = payload.get("id")
    if (
        status not in ("queued", "solving")
        or not isinstance(task_id, str)
        or not task_id
    ):
        raise ValueError("CaptchaNo meldet einen unerwarteten Aufgabenstatus")
    return None


def request_solution(
    url: str, sitekey: str, user_agent: str, timeout: float = CAPTCHA_TIMEOUT
) -> dict:
    """Create one solve and poll it within the deadline; never log credentials/tokens."""
    key = os.environ.get("CAPTCHANO_API_KEY") or os.environ.get("CAPTCHANO_KEY")
    if not key:
        raise ValueError("CAPTCHANO_API_KEY oder CAPTCHANO_KEY in Umgebung/.env setzen")
    deadline = time.monotonic() + timeout
    task = {
        "type": "recaptcha_v2",
        "url": url,
        "sitekey": sitekey,
        "userAgent": user_agent,
    }
    payload = _request(key, "/v1/solve", timeout, task)
    while (solution := _ready_solution(payload)) is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("CaptchaNo-Frist abgelaufen")
        log.info("CaptchaNo-Aufgabe läuft; neue Abfrage in 5 Sekunden")
        time.sleep(min(5, remaining))
        path = "/v1/task/" + urllib.parse.quote(payload["id"], safe="")
        payload = _request(key, path, deadline - time.monotonic())
    return solution


def _widget(page: Page) -> Page | Locator:
    transfer = page.locator("#divCaptchaTraspaso")
    return transfer if transfer.count() else page


def _sitekey(page: Page) -> str:
    widget = _widget(page)
    anchor = widget.locator(CHALLENGE).first
    if anchor.count():
        src = anchor.get_attribute("src") or ""
        key = urllib.parse.parse_qs(urllib.parse.urlsplit(src).query).get("k", [""])[0]
    else:
        key = (
            widget.locator("[data-sitekey]:visible").first.get_attribute("data-sitekey")
            or ""
        )
    if not key:
        raise ValueError("reCAPTCHA sitekey was not found")
    return key


def apply_solution(page: Page, solution: dict) -> None:
    """Place the token in its widget without calling callbacks or submitting forms.

    The CDP session stays attached because detaching would reset the user-agent override.
    """
    token = solution.get("token")
    if not isinstance(token, str) or len(token) < 100:
        raise ValueError("CaptchaNo solution has no usable token")
    user_agent = solution.get("userAgent")
    if (
        isinstance(user_agent, str)
        and user_agent
        and user_agent != page.evaluate("navigator.userAgent")
    ):
        session = page.context.new_cdp_session(page)
        session.send("Network.setUserAgentOverride", {"userAgent": user_agent})
    field = page.locator('#divCaptchaTraspaso [name="g-recaptcha-response"]')
    if not field.count():
        field = page.locator('[name="g-recaptcha-response"]').last
    field.evaluate("(field, token) => field.value = token", token)


def _has_widget(page: Page) -> bool:
    widget = _widget(page)
    challenges = widget.locator(CHALLENGE).count()
    return bool(challenges or widget.locator("[data-sitekey]:visible").count())


def handle_captcha(page: Page) -> str:
    """Request one CaptchaNo task only when the application has a CAPTCHA widget."""
    if not _has_widget(page):
        return "not-present"
    if page.evaluate(TOKEN_READY) is True:
        return "verified-manually"
    try:
        sitekey = _sitekey(page)
        user_agent = page.evaluate("navigator.userAgent")
        solution = request_solution(page.url, sitekey, user_agent)
        apply_solution(page, solution)
        return "token-ready"
    except (Error, OSError, ValueError, RuntimeError) as error:
        log.warning(
            "CaptchaNo fehlgeschlagen (%s): %s",
            type(error).__name__,
            failure_reason(error),
        )
    log.warning("CAPTCHA kann %ss lang manuell gelöst werden", CAPTCHA_TIMEOUT)
    return (
        "verified-manually"
        if wait_verified(page, CAPTCHA_TIMEOUT)
        else "manual-required"
    )
