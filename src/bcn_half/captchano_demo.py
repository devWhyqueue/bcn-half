"""Test one CaptchaNo token against Google's public demo; keep the result visible."""

import argparse
import json
import logging
import re
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import Error, Page, sync_playwright
from playwright.sync_api import TimeoutError as BrowserTimeout

from bcn_half.bot import load_env
from bcn_half.captcha import (
    CAPTCHA_TIMEOUT,
    DEMO_URL,
    apply_solution,
    failure_reason,
    request_solution,
)

log = logging.getLogger("bcn-half")


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


def _verify_demo(page: Page, solution: dict) -> bool:
    form = page.locator("form").first
    if page.url != DEMO_URL or form.evaluate("form => form.action") != DEMO_URL:
        raise ValueError("Refusing to submit outside the exact Google demo URL")
    apply_solution(page, solution)
    form.locator('input[type="submit"], button[type="submit"]').click()
    try:
        page.get_by_text(re.compile("Verification Success", re.I)).wait_for(
            timeout=30000
        )
        return True
    except BrowserTimeout:
        return False


def run_demo(timeout: int = CAPTCHA_TIMEOUT, keep_open: bool = True) -> bool:
    """Run one public-demo solve and retain its server-verification result."""
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
                solution = request_solution(DEMO_URL, sitekey, user_agent, timeout)
                result["provider_ready"] = True
                result["google_accepted"] = _verify_demo(page, solution)
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
                    if keep_open:
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
    return bool(result["google_accepted"])


def main() -> int:
    """Load credentials privately and run the public-demo command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=CAPTCHA_TIMEOUT)
    parser.add_argument(
        "--keep-open", action=argparse.BooleanOptionalAction, default=True
    )
    args = parser.parse_args()
    if not 1 <= args.timeout <= 600:
        parser.error("--timeout must be between 1 and 600 seconds")
    load_env()
    return 0 if run_demo(args.timeout, args.keep_open) else 1


def cli() -> None:
    """Run the standalone demo command with console logging."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    raise SystemExit(main())


if __name__ == "__main__":
    cli()
