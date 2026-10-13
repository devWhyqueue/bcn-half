"""Poll the Barcelona Half MarketPlace page and alert via Telegram when a bib is listed."""
import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser

DEFAULT_URL = (
    "https://inscripciones.rpmsports.es/es/evento/"
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
        elif tag == "table" and "table-listados" in (dict(attrs).get("class") or "").split():
            self.found = True
            self._table_depth = 1

    def handle_endtag(self, tag):
        if not self._table_depth:
            return
        if tag == "table":
            self._table_depth -= 1
            if not self._table_depth:
                self._in_body = False
        elif tag in ("td", "th") and self._cell is not None:
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


def parse_listings(html):
    """Return listing rows, or None if the table is missing (layout changed)."""
    p = _Listings()
    p.feed(html)
    return p.rows if p.found else None


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def send(token, chat_id, text):
    data = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
    ).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    with urllib.request.urlopen(url, data=data, timeout=20) as r:
        json.load(r)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        sys.exit("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
    url = os.environ.get("WATCH_URL", DEFAULT_URL)
    poll = int(os.environ.get("POLL_SECONDS", "60"))

    def notify(text):
        try:
            send(token, chat_id, text)
        except Exception:
            log.exception("telegram send failed")
            return False
        return True

    notify(f"bcn-half watching (every {poll}s)\n{url}")
    seen, fails, broken = set(), 0, False
    while True:
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
            seen &= current  # relisting alerts again
            log.info("poll ok: %d listings", len(rows))
        except Exception as e:
            fails += 1
            log.warning("poll failed (%d): %s", fails, e)
            if (fails >= FAIL_LIMIT or isinstance(e, ValueError)) and not broken:
                broken = notify(f"bcn-half checker BROKEN: {e}")
        time.sleep(poll)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    main()
