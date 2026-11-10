"""Poll the Barcelona Half MarketPlace page and alert via Telegram when a bib is listed."""

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from importlib import import_module
from pathlib import Path

DEFAULT_URL = (
    "https://inscripciones.rpmsports.es/en/event/"
    "hyundai-mitja-marato-barcelona-by-brooks-2027/transfer-registration"
)
UA = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
FAIL_LIMIT = 5

log = logging.getLogger("bcn-half")


class _Listings(HTMLParser):
    """Collects one string per <tr> in the <tbody> of the table.table-listados."""

    def __init__(self):
        super().__init__()
        self.found = False
        self.rows = []
        self._table_depth = 0  # >0 while inside the listings table
        self._in_body = False
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if self._table_depth:
            if tag == "table":
                self._table_depth += 1
            elif tag == "tbody":
                self._in_body = True
            elif tag == "tr" and self._in_body:
                self._row = []
            elif tag in ("td", "th") and self._row is not None:
                self._cell = []
        elif (
            tag == "table"
            and "table-listados" in (dict(attrs).get("class") or "").split()
        ):
            self.found = True
            self._table_depth = 1

    def handle_endtag(self, tag):
        if not self._table_depth:
            return
        if tag == "table":
            self._table_depth -= 1
            if not self._table_depth:
                self._in_body = False
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            cells = [c for c in self._row if c]
            if cells:
                self.rows.append(" · ".join(cells))
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_listings(html: str) -> list[str] | None:
    """Return listing rows, or None if the table is missing (layout changed)."""
    p = _Listings()
    p.feed(html)
    return p.rows if p.found else None


def fetch(url: str) -> str:
    """Fetch once, retrying transient HTTP failures at most twice with backoff."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            delay = 10 * 2**attempt
            retry_after = error.headers.get("Retry-After", "")
            if retry_after.isdigit():
                delay = max(delay, min(int(retry_after), 3600))
            log.warning("HTTP %s; retry in %ss", error.code, delay)
            time.sleep(delay)
    raise RuntimeError("HTTP retry limit reached")


def send(token: str, chat_id: str, text: str) -> None:
    """Send a listing or workflow status to a Telegram chat."""
    data = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
    ).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    with urllib.request.urlopen(url, data=data, timeout=20) as r:
        json.load(r)


def load_env(path: Path = Path(".env")) -> None:
    """Load plain dotenv assignments without overwriting the process environment."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if separator and key and not key.startswith("#"):
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key, value)


def _application(url: str, reference: str) -> str:
    try:
        workflow = import_module("bcn_half.application")
        result = workflow.run_application(
            url,
            reference,
            automatic=os.environ.get("SOLVECHA_LIVE_ENABLED", "false").lower()
            == "true",
        )
        return result.message()
    except (ImportError, OSError, ValueError, RuntimeError) as error:
        return f"Bib workflow failed for {reference}: {type(error).__name__}\n{url}"


def main() -> int:
    """Run the checker or one visible, non-submitting browser integration test."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--test-application", action="store_true")
    mode.add_argument("--test-captcha", action="store_true")
    parser.add_argument("--event-url")
    parser.add_argument(
        "--keep-open",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep test final state open for inspection (default); resume in Playwright Inspector",
    )
    args = parser.parse_args()
    load_env()
    url = args.event_url or os.environ.get("WATCH_URL") or DEFAULT_URL
    if (
        args.test_application
        or args.test_captcha
        or os.environ.get("APPLICATION_TEST_MODE", "false").lower() == "true"
    ):
        workflow = import_module("bcn_half.application")
        result = workflow.run_application(
            url,
            demo=args.test_captcha,
            automatic=args.test_captcha,
            manual=not args.test_captcha,
            keep_open=args.keep_open,
        )
        log.info("%s", result.message())
        if args.test_captcha:
            return 0 if result.captcha == "verified-client-token" else 1
        return (
            0
            if result.status == "application-ready"
            and result.captcha != "manual-required"
            else 1
        )
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_ids = [
        c.strip()
        for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",")
        if c.strip()
    ]
    if not token or not chat_ids:
        sys.exit("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
    poll = int(os.environ.get("POLL_SECONDS", "10"))
    if poll < 1:
        sys.exit("POLL_SECONDS must be positive")

    def notify(text: str) -> bool:
        """Report success if any configured chat received the message."""
        # True if any chat got it: one unreachable chat must not re-alert the others every poll
        sent = False
        for chat_id in chat_ids:
            try:
                send(token, chat_id, text)
                sent = True
            except (OSError, ValueError) as error:
                log.warning("telegram send failed (%s)", type(error).__name__)
        return sent

    notify(f"bcn-half watching (every {poll}s)\n{url}")
    enabled = os.environ.get("APPLICATION_ENABLED", "false").lower() == "true"
    workflow = None
    if enabled:
        try:
            workflow = import_module("bcn_half.application")
        except ImportError:
            notify(
                "Bib workflow unavailable: run uv sync and uv run playwright install chromium; checker continues"
            )
    executor = ThreadPoolExecutor(max_workers=1) if workflow else None
    pending, attempted, pending_reference = None, set(), None
    seen, fails, broken = set(), 0, False
    while True:
        if pending is not None and pending.done():
            error = pending.exception()
            message = (
                f"Bib workflow failed for {pending_reference}: {type(error).__name__}\n{url}"
                if error
                else pending.result()
            )
            if notify(message):
                pending = None
        try:
            rows = parse_listings(fetch(url))
            if rows is None:
                raise ValueError("listings table not found")
            fails = 0
            if broken:
                broken = False
                notify("bcn-half checker recovered")
            current = set(rows)
            new = [r for r in rows if r not in seen]
            # only mark seen once the alert went out, so a failed send retries next poll
            if new and notify("Bib listed!\n" + "\n".join(new) + f"\n{url}"):
                seen |= set(new)
            if workflow and executor is not None and pending is None:
                eligible = [row for row in rows if row.split(" · ")[0] not in attempted]
                listing = workflow.select_listing(eligible)
                if listing:
                    attempted.add(listing.reference)
                    pending_reference = listing.reference
                    pending = executor.submit(_application, url, listing.reference)
                    notify(
                        f"Bib workflow starting: {listing.reference} · {listing.price}\n{url}"
                    )
            seen &= current  # relisting alerts again
            log.info("poll ok: %d listings", len(rows))
        except (OSError, ValueError) as e:
            fails += 1
            log.warning("poll failed (%d): %s", fails, e)
            if (fails >= FAIL_LIMIT or isinstance(e, ValueError)) and not broken:
                broken = notify(f"bcn-half checker BROKEN: {e}")
        time.sleep(poll)


def cli() -> None:
    """Run the installed watcher command with console logging."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    sys.exit(main())


if __name__ == "__main__":
    cli()
