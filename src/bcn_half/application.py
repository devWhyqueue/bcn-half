"""Open a matching marketplace entry, stopping before email or registration submission."""

import json
import logging
import re
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error, Page, Route, sync_playwright

from bcn_half.bot import parse_listings
from bcn_half.captcha import TOKEN_READY, failure_reason, handle_captcha

log = logging.getLogger("bcn-half")
HALF = re.compile(
    r"half[ -]marathon|medio marat[oó]n|media marat[oó]n|mitja marat[oó]|21\s*km",
    re.IGNORECASE,
)
EMAIL = "#cphCuerpo_tbEmailSolicitarCambio"
SUBMIT = "#cphCuerpo_btSolicitarTraspaso"


@dataclass(frozen=True)
class Listing:
    reference: str
    category: str
    price: str


@dataclass
class ApplicationResult:
    listing: Listing | None
    event_url: str
    status: str
    captcha: str = "not-checked"

    def message(self) -> str:
        """Build the Telegram status using the actual watched event URL."""
        details = (
            f"{self.listing.reference} · {self.listing.category} · {self.listing.price}"
            if self.listing
            else "No eligible bib"
        )
        return (
            f"Bib workflow: {self.status}\n{details}\nCAPTCHA: {self.captcha}"
            f"\nNo email or application submitted.\n{self.event_url}"
        )


def select_listing(rows: list[str], reference: str | None = None) -> Listing | None:
    """Select a half-marathon row by its exact reference, never by row position."""
    for row in rows:
        cells = row.split(" · ")
        if len(cells) < 3 or not HALF.search(cells[1]):
            continue
        if reference and cells[0] != reference:
            continue
        price = re.search(r"[\d.,]+\s*€", cells[2])
        if price:
            return Listing(cells[0], cells[1], price.group())
    return None


def _guard_request(route: Route) -> None:
    request = route.request
    submitted = request.post_data_buffer or b""
    if request.method == "POST" and (
        b"btSolicitarTraspaso" in submitted or b"tbEmailSolicitarCambio=" in submitted
    ):
        log.error("Blocked registration/email submission")
        route.abort()
    else:
        route.continue_()


def _open_listing(
    page: Page, result: ApplicationResult, reference: str | None, folder: Path
) -> ApplicationResult:
    url = result.event_url
    response = page.goto(url, wait_until="domcontentloaded")
    if response is None or response.status >= 400:
        raise RuntimeError("Marketplace navigation failed")
    page.locator("table.table-listados").wait_for()
    rows = parse_listings(page.content())
    if rows is None:
        raise ValueError("Listings table missing")
    listing = select_listing(rows, reference)
    result.listing = listing
    log.info("Browser parsed %d listings; selected %s", len(rows), listing)
    page.screenshot(path=str(folder / "listings.png"), full_page=True)
    if listing is None:
        result.status = "listing-unavailable"
        return result
    row = page.locator("table.table-listados tbody tr").filter(
        has=page.locator("td").filter(
            has_text=re.compile(r"^" + re.escape(listing.reference) + r"$")
        )
    )
    if row.count() != 1:
        result.status = "listing-unavailable"
        return result
    row.get_by_role("link", name="Apply for", exact=True).click()
    log.info("Clicked Apply for %s", listing.reference)
    page.locator(EMAIL).wait_for(state="visible")
    summary = page.locator("p.lead").inner_text()
    if not re.search(
        r"\b" + re.escape(listing.reference.removeprefix("REF")) + r"\b", summary
    ):
        raise RuntimeError("Application reference did not match")
    page.locator(SUBMIT).evaluate("button => button.disabled = true")
    page.screenshot(path=str(folder / "application.png"), full_page=True)
    result.status = "application-ready"
    return result


def _inspect_result(page: Page, result: ApplicationResult) -> None:
    log.info("%s", result.message())
    log.info(
        "Final state stays open: inspect it, then click Resume in Playwright Inspector"
    )
    try:
        page.pause()
    except Error:
        if not page.is_closed():
            raise
    if not page.is_closed() and page.evaluate(TOKEN_READY) is True:
        if result.captcha not in ("token-ready", "verified-manually"):
            result.captcha = "verified-manually"


def run_application(
    url: str,
    reference: str | None = None,
    *,
    keep_open: bool = False,
) -> ApplicationResult:
    """Run one visible, non-submitting workflow and save screenshots and a result."""
    if (
        urlsplit(url).scheme != "https"
        or urlsplit(url).netloc != "inscripciones.rpmsports.es"
        or not urlsplit(url).path.endswith("/transfer-registration")
    ):
        raise ValueError("Expected an HTTPS RPM Sports marketplace URL")
    folder = Path("artifacts") / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    folder.mkdir(parents=True)
    result = ApplicationResult(None, url, "browser-starting")
    handler = logging.FileHandler(folder / "workflow.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(handler)
    page = None
    try:
        with sync_playwright() as playwright:
            with playwright.chromium.launch(headless=False) as browser:
                page = browser.new_page(locale="en-US")
                page.set_default_timeout(30000)
                page.route("**/*", _guard_request)
                try:
                    result = _open_listing(page, result, reference, folder)
                    if result.status == "application-ready":
                        if page.locator("#divCaptchaTraspaso").is_visible():
                            page.locator(
                                '#divCaptchaTraspaso iframe[src*="/anchor"]'
                            ).wait_for()
                        result.captcha = handle_captcha(
                            page,
                            manual=not keep_open,
                        )
                    page.screenshot(path=str(folder / "result.png"), full_page=True)
                except (Error, OSError, ValueError, RuntimeError):
                    if not page.is_closed():
                        with suppress(Error):
                            page.screenshot(
                                path=str(folder / "failure.png"),
                                full_page=True,
                                timeout=5000,
                            )
                    if keep_open and not page.is_closed():
                        _inspect_result(page, result)
                    raise
                for name in ("automated-result.json", "result.json"):
                    (folder / name).write_text(
                        json.dumps(asdict(result), indent=2), encoding="utf-8"
                    )
                if keep_open and not page.is_closed():
                    _inspect_result(page, result)
                    if not page.is_closed():
                        page.screenshot(
                            path=str(folder / "inspected-result.png"), full_page=True
                        )
    except (Error, OSError, ValueError, RuntimeError) as error:
        result.status = (
            "browser-closed"
            if page is not None and page.is_closed()
            else f"application-failed at {result.status} ({type(error).__name__})"
        )
        log.error("%s: %s", result.status, failure_reason(error))
    finally:
        (folder / "result.json").write_text(
            json.dumps(asdict(result), indent=2), encoding="utf-8"
        )
        log.info("%s", result.message())
        log.info("Workflow diagnostics: %s", folder)
        log.removeHandler(handler)
        handler.close()
    return result
