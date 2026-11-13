"""Keep one application browser on its worker thread, separate from HTTP polling."""

import logging
from contextlib import ExitStack, suppress

from patchright.sync_api import Error, Page, sync_playwright

from bcn_half.application import FAILURES, run_application
from bcn_half.captcha import failure_reason
from bcn_half.marketplace import check_url

log = logging.getLogger("bcn-half")


class ApplicationBrowser:
    """Own a warm page; prepare, apply, and close must run on the same worker."""

    def __init__(self, url: str) -> None:
        check_url(url)
        self.url = url
        self.resources = ExitStack()
        self.page: Page | None = None

    def _page(self) -> Page:
        if self.page is None or self.page.is_closed():
            self.close()
            playwright = self.resources.enter_context(sync_playwright())
            browser = self.resources.enter_context(
                playwright.chromium.launch(headless=False)
            )
            context = self.resources.enter_context(browser.new_context(locale="en-US"))
            self.page = context.new_page()
            self.page.set_default_timeout(30000)
            response = self.page.goto(self.url, wait_until="domcontentloaded")
            if response is None or response.status >= 400:
                raise RuntimeError("Marketplace-Vorbereitung fehlgeschlagen")
            self.page.locator("table.table-listados").wait_for()
        return self.page

    def prepare(self) -> None:
        """Warm browser assets without opening a listing or solving a CAPTCHA."""
        try:
            self._page()
        except FAILURES as error:
            log.warning(
                "Browser-Vorbereitung fehlgeschlagen: %s", failure_reason(error)
            )
            self.close()

    def apply(self, reference: str, email: str) -> tuple[str, bool]:
        """Run an attempt on the warm page and report whether the email was accepted."""
        try:
            result = run_application(
                self.url, reference, email=email, page=self._page()
            )
            if (
                result.status.startswith("application-failed")
                or result.status == "browser-closed"
            ):
                self.close()
            return result.message(), result.status == "email-submitted"
        except FAILURES as error:
            log.warning("Browser-Bewerbung fehlgeschlagen: %s", failure_reason(error))
            self.close()
            return (
                f"Bewerbung für {reference} fehlgeschlagen: {type(error).__name__}\n{self.url}",
                False,
            )

    def close(self) -> None:
        """Release the browser and driver on their owner thread."""
        with suppress(Error, OSError):
            self.resources.close()
        self.page = None
