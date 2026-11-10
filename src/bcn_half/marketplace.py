"""Fetch the RPM Sports marketplace page and parse its listings table."""

import logging
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import override

UA = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"

log = logging.getLogger("bcn-half")


class _Listings(HTMLParser):
    """Collects one string per <tr> in the <tbody> of the table.table-listados.

    `_table_depth` is above zero while inside the listings table, counting nested tables.
    """

    def __init__(self) -> None:
        super().__init__()
        self.found = False
        self.rows: list[str] = []
        self._table_depth = 0
        self._in_body = False
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track entry into the listings table, its body, rows, and cells."""
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

    @override
    def handle_endtag(self, tag: str) -> None:
        """Close cells and rows, joining non-empty cells into one row string."""
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

    @override
    def handle_data(self, data: str) -> None:
        """Collect text inside the current cell."""
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
            log.warning("HTTP %s; neuer Versuch in %ss", error.code, delay)
            time.sleep(delay)
    raise RuntimeError("HTTP-Wiederholungslimit erreicht")
