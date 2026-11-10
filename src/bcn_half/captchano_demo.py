"""Test one CaptchaNo token against Google's public demo; keep the result visible."""

import argparse
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import Error, Page, sync_playwright
from playwright.sync_api import TimeoutError as BrowserTimeout

from bcn_half.bot import UA, load_env
from bcn_half.captcha import DEMO_URL, failure_reason

log = logging.getLogger("bcn-half")


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


def request_solution(sitekey: str, user_agent: str, timeout: float) -> dict:
    """Create one solve and poll it within the deadline; never log credentials/tokens."""
    key = os.environ.get("CAPTCHANO_API_KEY") or os.environ.get("CAPTCHANO_KEY")
    if not key:
        raise ValueError(
            "Set CAPTCHANO_API_KEY or CAPTCHANO_KEY in the environment/.env"
        )
    deadline = time.monotonic() + timeout
    payload = _request(
        key,
        "/v1/solve",
        timeout,
        {
            "type": "recaptcha_v2",
            "url": DEMO_URL,
            "sitekey": sitekey,
            "userAgent": user_agent,
        },
    )
    while True:
        status = payload.get("status")
        if status == "ready":
            solution = payload.get("solution")
            if (
                not isinstance(solution, dict)
                or not isinstance(solution.get("token"), str)
                or len(solution["token"]) < 100
            ):
                raise ValueError("CaptchaNo ready response has no usable token")
            return solution
        if status == "failed" or payload.get("error"):
            raise RuntimeError("CaptchaNo: " + _error_code(payload))
        task_id = payload.get("id")
        if status != "solving" or not isinstance(task_id, str) or not task_id:
            raise ValueError("CaptchaNo returned an unexpected task state")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("CaptchaNo deadline reached")
        log.info("CaptchaNo task is solving; polling again in 5 seconds")
        time.sleep(min(5, remaining))
        payload = _request(
            key,
            "/v1/task/" + urllib.parse.quote(task_id, safe=""),
            deadline - time.monotonic(),
        )


def _open_demo(page: Page) -> str:
    page.goto(DEMO_URL, wait_until="domcontentloaded")
    widget = page.locator("[data-sitekey]").first
    widget.wait_for(state="attached")
    sitekey = widget.get_attribute("data-sitekey")
    if not sitekey:
        raise ValueError("Public demo sitekey was not found")
    fields = page.locator(
        'form input:not([type]), form input[type="text"], form input[type="email"]'
    ).all()
    if len(fields) != 3:
        raise ValueError("Unexpected public demo form; refusing submission")
    for field, value in zip(
        fields, ("Demo", "Test", "captcha-test@example.invalid"), strict=True
    ):
        field.evaluate("(field, value) => field.value = value", value)
    return sitekey


def _verify_demo(page: Page, token: str) -> bool:
    form = page.locator("form").first
    if page.url != DEMO_URL or form.evaluate("form => form.action") != DEMO_URL:
        raise ValueError("Refusing to submit outside the exact Google demo URL")
    page.locator('[name="g-recaptcha-response"]').evaluate(
        "(field, token) => field.value = token", token
    )
    form.locator('input[type="submit"], button[type="submit"]').click()
    try:
        page.get_by_text(re.compile("Verification Success", re.I)).wait_for(
            timeout=30000
        )
        return True
    except BrowserTimeout:
        return False


def main() -> int:
    """Load credentials privately and perform a single visible public-demo test."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument(
        "--keep-open", action=argparse.BooleanOptionalAction, default=True
    )
    args = parser.parse_args()
    if not 1 <= args.timeout <= 600:
        parser.error("--timeout must be between 1 and 600 seconds")
    load_env()
    folder = Path("artifacts") / (
        "captchano-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    folder.mkdir(parents=True)
    result = {
        "provider": "captchano",
        "provider_ready": False,
        "google_accepted": False,
        "status": "starting",
    }
    handler = logging.FileHandler(folder / "workflow.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(handler)
    page = None
    try:
        with (
            sync_playwright() as playwright,
            playwright.chromium.launch(headless=False) as browser,
        ):
            page = browser.new_page(locale="en-US")
            try:
                sitekey = _open_demo(page)
                page.screenshot(path=str(folder / "before.png"), full_page=True)
                user_agent = page.evaluate("navigator.userAgent")
                log.info("Requesting one CaptchaNo solve for the public Google demo")
                solution = request_solution(sitekey, user_agent, args.timeout)
                result["provider_ready"] = True
                returned_agent = solution.get("userAgent")
                if (
                    isinstance(returned_agent, str)
                    and returned_agent
                    and returned_agent != user_agent
                ):
                    page.close()
                    page = browser.new_page(locale="en-US", user_agent=returned_agent)
                    _open_demo(page)
                result["google_accepted"] = _verify_demo(page, solution["token"])
                result["status"] = (
                    "google-verified"
                    if result["google_accepted"]
                    else "google-rejected"
                )
            except (Error, OSError, ValueError, RuntimeError) as error:
                result["status"] = "failed"
                result["error"] = failure_reason(error)
                log.error("CaptchaNo demo failed: %s", result["error"])
            finally:
                (folder / "result.json").write_text(
                    json.dumps(result, indent=2), encoding="utf-8"
                )
                log.info("Demo result: %s; evidence: %s", result["status"], folder)
                if page is not None and not page.is_closed():
                    with suppress(Error):
                        page.screenshot(path=str(folder / "result.png"), full_page=True)
                    if args.keep_open:
                        log.info(
                            "Final browser stays open. Inspect, then close it or click Resume in Playwright Inspector."
                        )
                        with suppress(Error):
                            page.pause()
    except (Error, OSError, ValueError, RuntimeError) as error:
        result["status"] = "failed"
        result["error"] = failure_reason(error)
        (folder / "result.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        log.error("CaptchaNo demo failed: %s", result["error"])
    finally:
        log.removeHandler(handler)
        handler.close()
    return 0 if result["google_accepted"] else 1


def cli() -> None:
    """Run the standalone demo command with console logging."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    raise SystemExit(main())


if __name__ == "__main__":
    cli()
