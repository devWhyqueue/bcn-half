"""Open a matching marketplace entry and submit one email to request its transfer."""

import json
import logging
import re
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Browser, Error, Page, Response, sync_playwright
from playwright.sync_api import TimeoutError as BrowserTimeout

from bcn_half.artifacts import close_log, open_artifacts
from bcn_half.captcha import TOKEN_READY, failure_reason, handle_captcha
from bcn_half.marketplace import Listing, check_url, parse_listings, select_listing

log = logging.getLogger("bcn-half")
EMAIL = "#cphCuerpo_tbEmailSolicitarCambio"
SUBMIT = "#cphCuerpo_btSolicitarTraspaso"
MODAL_TITLE = "#modal-mensaje #lblTituloMensaje"
RESERVED = re.compile(
    r"reserved for 24 hours|reservad[ao] (durante|por) 24 horas", re.IGNORECASE
)
SUBMITTABLE = ("not-present", "token-ready", "verified-manually")
FAILURES = (Error, OSError, ValueError, RuntimeError)


@dataclass
class ApplicationResult:
    listing: Listing | None
    event_url: str
    status: str
    email: str
    captcha: str = "not-checked"
    reply: str = ""

    def message(self) -> str:
        """Build the Telegram status using the actual watched event URL."""
        details = (
            f"{self.listing.reference} · {self.listing.category} · {self.listing.price}"
            if self.listing
            else "Kein passender Startplatz"
        )
        return (
            f"Bewerbung: {self.status}\n{details}\nCAPTCHA: {self.captcha}"
            f"\nE-Mail {self.email}: {self.reply or 'nicht gesendet'}\n{self.event_url}"
        )


def _find_listing(page: Page, result: ApplicationResult, reference: str | None) -> None:
    response = page.goto(result.event_url, wait_until="domcontentloaded")
    if response is None or response.status >= 400:
        raise RuntimeError("Marketplace-Navigation fehlgeschlagen")
    page.locator("table.table-listados").wait_for()
    rows = parse_listings(page.content())
    if rows is None:
        raise ValueError("Angebotstabelle fehlt")
    result.listing = select_listing(rows, reference)
    log.info("Browser hat %d Angebote gelesen; gewählt: %s", len(rows), result.listing)


def _open_form(page: Page, result: ApplicationResult, listing: Listing) -> None:
    row = page.locator("table.table-listados tbody tr").filter(
        has=page.locator("td").filter(
            has_text=re.compile(r"^" + re.escape(listing.reference) + r"$")
        )
    )
    if row.count() != 1:
        result.status = "listing-unavailable"
        return
    row.get_by_role("link", name="Apply for", exact=True).click()
    log.info("Apply for bei %s geklickt", listing.reference)
    page.locator(EMAIL).wait_for(state="visible")
    summary = page.locator("p.lead").inner_text()
    number = re.escape(listing.reference.removeprefix("REF"))
    if not re.search(r"\b" + number + r"\b", summary):
        raise RuntimeError("Bewerbungsreferenz stimmt nicht überein")
    result.status = "application-ready"


def _is_site_post(response: Response) -> bool:
    request = response.request
    return (
        request.method == "POST"
        and urlsplit(request.url).netloc == "inscripciones.rpmsports.es"
    )


def _record_reply(page: Page, response: Response, folder: Path) -> None:
    """Keep the raw server reply, final page, and screenshot whatever the outcome."""
    body = ""
    with suppress(Error):
        body = response.text()
    reply = f"{response.status} {response.url}\n\n{body}"
    (folder / "submit-response.txt").write_text(reply, encoding="utf-8")
    with suppress(BrowserTimeout):
        page.locator(MODAL_TITLE).wait_for(state="visible", timeout=15000)
    page.wait_for_load_state()
    (folder / "submitted.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path=str(folder / "submitted.png"), full_page=True)


def _submit_email(page: Page, result: ApplicationResult, folder: Path) -> None:
    """Submit the email once and classify the site's reply from its message modal."""
    page.locator(EMAIL).fill(result.email)
    with page.expect_response(_is_site_post) as response:
        page.locator(SUBMIT).click()
    log.info("E-Mail für %s gesendet", result.listing)
    _record_reply(page, response.value, folder)
    if not page.locator(MODAL_TITLE).is_visible():
        result.status = "email-unconfirmed"
        return
    title = page.locator(MODAL_TITLE).inner_text().strip()
    body = page.locator("#lblCuerpoMensaje").inner_text().strip()
    result.reply = f"{title}: {body}"
    result.status = "email-submitted" if RESERVED.search(body) else "email-rejected"


def _apply(
    page: Page, result: ApplicationResult, reference: str | None, folder: Path
) -> None:
    _find_listing(page, result, reference)
    page.screenshot(path=str(folder / "listings.png"), full_page=True)
    if result.listing is None:
        result.status = "listing-unavailable"
        return
    _open_form(page, result, result.listing)
    if result.status != "application-ready":
        return
    page.screenshot(path=str(folder / "application.png"), full_page=True)
    if page.locator("#divCaptchaTraspaso").is_visible():
        page.locator('#divCaptchaTraspaso iframe[src*="/anchor"]').wait_for()
    result.captcha = handle_captcha(page)
    if result.captcha in SUBMITTABLE:
        _submit_email(page, result, folder)


def _inspect_result(page: Page, result: ApplicationResult) -> None:
    log.info("%s", result.message())
    log.info(
        "Endzustand bleibt offen: prüfen, dann im Playwright Inspector Resume klicken"
    )
    try:
        page.pause()
    except Error:
        if not page.is_closed():
            raise
    if not page.is_closed() and page.evaluate(TOKEN_READY) is True:
        if result.captcha not in ("token-ready", "verified-manually"):
            result.captcha = "verified-manually"


def _run_in_browser(
    page: Page,
    result: ApplicationResult,
    reference: str | None,
    folder: Path,
    keep_open: bool,
) -> None:
    try:
        _apply(page, result, reference, folder)
        page.screenshot(path=str(folder / "result.png"), full_page=True)
    except FAILURES:
        if not page.is_closed():
            with suppress(Error):
                page.screenshot(
                    path=str(folder / "failure.png"), full_page=True, timeout=5000
                )
        if keep_open and not page.is_closed():
            _inspect_result(page, result)
        if page.is_closed():
            result.status = "browser-closed"
        raise
    _save(folder / "automated-result.json", result)
    if keep_open and not page.is_closed():
        _inspect_result(page, result)
        if not page.is_closed():
            page.screenshot(path=str(folder / "inspected-result.png"), full_page=True)


def _save(path: Path, result: ApplicationResult) -> None:
    path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")


def _close_log(
    handler: logging.Handler, folder: Path, result: ApplicationResult
) -> None:
    _save(folder / "result.json", result)
    log.info("%s", result.message())
    log.info("Diagnose: %s", folder)
    close_log(handler)


def _run_in_context(
    browser: Browser,
    result: ApplicationResult,
    reference: str | None,
    folder: Path,
    keep_open: bool,
    trace: bool,
) -> None:
    """Run the attempt; with trace, record DOM, network, and console in trace.zip."""
    context = browser.new_context(locale="en-US")
    if trace:
        context.tracing.start(screenshots=True, snapshots=True)
    try:
        page = context.new_page()
        page.set_default_timeout(30000)
        _run_in_browser(page, result, reference, folder, keep_open)
    finally:
        if trace:
            with suppress(Error):
                context.tracing.stop(path=str(folder / "trace.zip"))


def run_application(
    url: str,
    reference: str | None = None,
    *,
    email: str,
    keep_open: bool = False,
    trace: bool = False,
) -> ApplicationResult:
    """Run one visible workflow that submits the email; save screenshots and a result."""
    check_url(url)
    folder, handler = open_artifacts()
    result = ApplicationResult(None, url, "browser-starting", email)
    try:
        with (
            sync_playwright() as playwright,
            playwright.chromium.launch(headless=False) as browser,
        ):
            _run_in_context(browser, result, reference, folder, keep_open, trace)
    except FAILURES as error:
        if result.status != "browser-closed":
            result.status = (
                f"application-failed at {result.status} ({type(error).__name__})"
            )
        log.error("%s: %s", result.status, failure_reason(error))
    finally:
        _close_log(handler, folder, result)
    return result
