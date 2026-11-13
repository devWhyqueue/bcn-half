"""Poll the Barcelona Half marketplace, alert via Telegram, and optionally apply for bibs."""

import argparse
import logging
import os
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from importlib import import_module
from pathlib import Path

from bcn_half.marketplace import MarketplaceClient, parse_listings, select_listing
from bcn_half.telegram import Telegram

FAIL_LIMIT = 5

log = logging.getLogger("bcn-half")


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


def _env_list(name: str) -> list[str]:
    """Split a comma-separated environment variable into its non-empty values."""
    return [
        value.strip() for value in os.environ.get(name, "").split(",") if value.strip()
    ]


def _failure(reference: str, error: BaseException, url: str) -> str:
    return f"Bewerbung für {reference} fehlgeschlagen: {type(error).__name__}\n{url}"


class Watcher:
    """Poll the marketplace, alert Telegram, and apply with unused email addresses."""

    def __init__(
        self,
        url: str,
        telegram: Telegram,
        emails: list[str],
        apply: bool,
        max_price: float | None = None,
    ) -> None:
        self.url, self.telegram, self.emails = url, telegram, emails
        self.client = MarketplaceClient(url)
        self.max_price = max_price
        self.application = (
            import_module("bcn_half.browser").ApplicationBrowser(url) if apply else None
        )
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.seen: set[str] = set()
        self.attempted: set[str] = set()
        self.fails, self.broken = 0, False
        self.pending: Future[tuple[str, bool]] | None = None
        self.pending_reference, self.pending_email = "", ""

    def run(self, poll: int) -> int:
        """Poll until every email address was accepted; alert-only mode never ends."""
        try:
            if self.application is not None:
                self.executor.submit(self.application.prepare)
            self.telegram.notify(f"bcn-half beobachtet (alle {poll}s)\n{self.url}")
            while not self._finish_application():
                self._poll()
                time.sleep(poll)
            self.telegram.notify("Alle MAIL_ADDRESSES eingereicht; bcn-half beendet")
            return 0
        finally:
            self._close()

    def _close(self) -> None:
        try:
            if self.application is not None:
                self.executor.submit(self.application.close).result()
        finally:
            self.executor.shutdown(wait=True)
            self.client.close()

    def _finish_application(self) -> bool:
        if self.pending is None or not self.pending.done():
            return False
        error = self.pending.exception()
        message, submitted = (
            (_failure(self.pending_reference, error, self.url), False)
            if error
            else self.pending.result()
        )
        if submitted:
            self.emails.remove(self.pending_email)
        if self.telegram.notify(message) or submitted:
            self.pending = None
        return submitted and not self.emails

    def _poll(self) -> None:
        try:
            rows = parse_listings(self.client.fetch())
            if rows is None:
                raise ValueError("Angebotstabelle nicht gefunden")
            self._start_application(rows)
            self._recover()
            self._alert(rows)
            log.info("Abfrage ok: %d Angebote", len(rows))
        except (OSError, ValueError) as e:
            self.fails += 1
            log.warning("Abfrage fehlgeschlagen (%d): %s", self.fails, e)
            if (
                self.fails >= FAIL_LIMIT or isinstance(e, ValueError)
            ) and not self.broken:
                self.broken = self.telegram.notify(f"bcn-half Prüfung DEFEKT: {e}")

    def _recover(self) -> None:
        self.fails = 0
        if self.broken:
            self.broken = False
            self.telegram.notify("bcn-half Prüfung läuft wieder")

    def _alert(self, rows: list[str]) -> None:
        """Mark rows seen only after a sent alert; forget delisted rows to re-alert."""
        new = [r for r in rows if r not in self.seen]
        if new and self.telegram.notify(
            "Startplatz gelistet!\n" + "\n".join(new) + f"\n{self.url}"
        ):
            self.seen |= set(new)
        self.seen &= set(rows)

    def _start_application(self, rows: list[str]) -> None:
        if self.application is None or self.pending is not None:
            return
        eligible = [row for row in rows if row.split(" · ")[0] not in self.attempted]
        listing = select_listing(eligible, max_price=self.max_price)
        if listing is None:
            return
        self.attempted.add(listing.reference)
        self.pending_reference, self.pending_email = listing.reference, self.emails[0]
        self.pending = self.executor.submit(
            self.application.apply, listing.reference, self.pending_email
        )
        self.telegram.notify(
            f"Bewerbung startet: {listing.reference} · {listing.price}\n{self.url}"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--test-application",
        action="store_true",
        help="Einmal echt bewerben: erste Adresse aus MAIL_ADDRESSES wird abgeschickt",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Passende Startplätze mit MAIL_ADDRESSES beantragen (wie APPLY=true)",
    )
    parser.add_argument("--event-url")
    parser.add_argument(
        "--keep-open",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Endzustand der Tests offen lassen (Standard); Resume im Playwright Inspector",
    )
    return parser


def _telegram() -> Telegram:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_ids = _env_list("TELEGRAM_CHAT_IDS")
    if not token or not chat_ids:
        sys.exit("TELEGRAM_BOT_TOKEN und TELEGRAM_CHAT_IDS müssen gesetzt sein")
    return Telegram(token, chat_ids)


def _poll_seconds() -> int:
    poll = int(os.environ.get("POLL_SECONDS", "10"))
    if poll < 1:
        sys.exit("POLL_SECONDS muss positiv sein")
    return poll


def _max_price() -> float | None:
    value = os.environ.get("MAX_PRICE", "").strip()
    if not value:
        return None
    try:
        return float(value.replace(",", "."))
    except ValueError:
        sys.exit("MAX_PRICE muss eine Zahl sein, z. B. 110 oder 110,50")


def _test_application(url: str, email: str, keep_open: bool) -> int:
    workflow = import_module("bcn_half.application")
    result = workflow.run_application(url, email=email, keep_open=keep_open, trace=True)
    log.info("%s", result.message())
    return 0 if result.status == "email-submitted" else 1


def _application_settings(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> tuple[bool, list[str]]:
    """Return whether to apply and the addresses; applying requires addresses."""
    apply = args.apply or args.test_application or os.environ.get("APPLY", "") == "true"
    emails = _env_list("MAIL_ADDRESSES")
    if apply and not emails:
        parser.error("Zum Bewerben muss MAIL_ADDRESSES gesetzt sein")
    return apply, emails


def main() -> int:
    """Run the watcher or one real application test."""
    parser = _parser()
    args = parser.parse_args()
    load_env()
    url = (args.event_url or os.environ.get("WATCH_URL") or "").strip()
    if not url:
        parser.error(
            "WATCH_URL muss in .env gesetzt oder per --event-url angegeben werden"
        )
    apply, emails = _application_settings(parser, args)
    if args.test_application:
        return _test_application(url, emails[0], args.keep_open)
    telegram, poll = _telegram(), _poll_seconds()
    return Watcher(url, telegram, emails, apply, _max_price()).run(poll)


def cli() -> None:
    """Run the installed watcher command with console logging."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    sys.exit(main())


if __name__ == "__main__":
    cli()
