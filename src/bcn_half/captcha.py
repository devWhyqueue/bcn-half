"""Bounded Solvecha image recognition, with visible manual fallback."""

import base64
import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import Error, FrameLocator, Locator, Page
from playwright.sync_api import TimeoutError as BrowserTimeout

log = logging.getLogger("bcn-half")
DEMO_URL = "https://www.google.com/recaptcha/api2/demo"
TOKEN_READY = """() => [...document.querySelectorAll('[name="g-recaptcha-response"]')]
    .some(field => field.value.length > 100)"""
CHALLENGE = (
    'iframe[src*="/recaptcha/api2/anchor"], iframe[src*="/recaptcha/enterprise/anchor"]'
)
GRID = "table.rc-imageselect-table-33, table.rc-imageselect-table-44"
IMAGE_KEY = "img => (img.currentSrc || img.src) + '|' + img.getAttribute('style')"
GRID_STATE = """table => {
    const tiles = [...table.querySelectorAll('td.rc-imageselect-tile')];
    if (![9, 16].includes(tiles.length)) return null;
    if (table.getAnimations({subtree: true}).some(a => a.playState === 'running')) return null;
    const keys = [];
    for (const tile of tiles) {
        const img = tile.querySelector('img');
        if (!img?.complete || !img.naturalWidth ||
            tile.classList.contains('rc-imageselect-dynamic-selected')) return null;
        for (let element = img; element !== table; element = element.parentElement) {
            const style = getComputedStyle(element);
            if (Number(style.opacity) < 0.99 || style.visibility === 'hidden') return null;
        }
        keys.push((img.currentSrc || img.src) + '|' + img.getAttribute('style'));
    }
    return keys;
}"""


def failure_reason(error: BaseException) -> str:
    """Keep diagnostic reasons while removing configured credentials."""
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    reason = str(error).splitlines()[0] if str(error) else type(error).__name__
    for name in (
        "SOLVECHA_API_KEY",
        "CAPTCHANO_API_KEY",
        "CAPTCHANO_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
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


def validate_answer(data: object, count: int) -> list[bool]:
    """Reject API responses that do not match the current challenge grid."""
    if (
        not isinstance(data, list)
        or len(data) != count
        or any(type(hit) is not bool for hit in data)
    ):
        raise ValueError("Solvecha returned an invalid tile selection")
    return data


def _request(
    key: str, deadline: float, body: dict | None = None, job: str = ""
) -> object:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Solvecha deadline reached")
    url = "https://api.solvecha.com/v1/recognition/recaptcha"
    url += "?" + (urllib.parse.urlencode({"id": job, "wait": 5}) if job else "sync=1")
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=min(30, remaining)) as response:
        payload = json.load(response)
    try:
        return payload["data"]
    except (KeyError, TypeError) as error:
        raise ValueError("Solvecha response has no recognition data") from error


def _recognize(
    task: str, grid: str, png: bytes, count: int, deadline: float
) -> list[bool]:
    key = os.environ.get("SOLVECHA_API_KEY", "")
    if not key:
        raise ValueError("SOLVECHA_API_KEY is missing")
    answer = _request(
        key,
        deadline,
        {"task": task, "grid": grid, "image_data": [base64.b64encode(png).decode()]},
    )
    for _ in range(20):
        if not isinstance(answer, str):
            return validate_answer(answer, count)
        answer = _request(key, deadline, job=answer)
    raise TimeoutError("Solvecha job did not finish")


def _remaining_ms(deadline: float) -> float:
    remaining = (deadline - time.monotonic()) * 1000
    if remaining <= 0:
        raise TimeoutError("CAPTCHA deadline reached")
    return remaining


def _wait_grid_ready(
    table: Locator, deadline: float, replacements: dict[int, str] | None = None
) -> list[str]:
    previous, stable_since = None, time.monotonic()
    while True:
        state = table.evaluate(GRID_STATE, timeout=_remaining_ms(deadline))
        ready = state and all(
            state[index] != old for index, old in (replacements or {}).items()
        )
        now = time.monotonic()
        if ready and state == previous and now - stable_since >= 0.5:
            return state
        if not ready or state != previous:
            stable_since = now
        previous = state if ready else None
        time.sleep(min(0.1, _remaining_ms(deadline) / 1000))


def _click_answer(
    challenge: FrameLocator, answer: list[bool], deadline: float, dynamic: bool
) -> None:
    tiles = challenge.locator("td.rc-imageselect-tile")
    replacements = {}
    for index, selected in enumerate(answer):
        if selected:
            if dynamic:
                replacements[index] = (
                    tiles.nth(index)
                    .locator("img")
                    .evaluate(IMAGE_KEY, timeout=_remaining_ms(deadline))
                )
            tiles.nth(index).click(timeout=_remaining_ms(deadline))
            log.info("Clicked CAPTCHA tile %d", index + 1)
    if replacements:
        log.info(
            "Waiting for %d replacement images to load and settle", len(replacements)
        )
        _wait_grid_ready(challenge.locator(GRID).first, deadline, replacements)
    if not dynamic or not any(answer):
        challenge.locator("#recaptcha-verify-button").click(
            timeout=_remaining_ms(deadline)
        )


def _solve_round(
    challenge: FrameLocator,
    deadline: float,
    seen: set[bytes],
    diagnostics: Path | None = None,
) -> None:
    table = challenge.locator(GRID).first
    table.wait_for(timeout=_remaining_ms(deadline))
    state = _wait_grid_ready(table, deadline)
    count = len(state)
    png = table.screenshot(timeout=_remaining_ms(deadline))
    fingerprint = hashlib.sha256(png).digest()
    if fingerprint in seen:
        raise RuntimeError("Unchanged failed challenge; refusing to resubmit")
    seen.add(fingerprint)
    instructions = challenge.locator(".rc-imageselect-instructions").inner_text(
        timeout=_remaining_ms(deadline)
    )
    task = " ".join(instructions.splitlines()[:2])
    capture = diagnostics / f"captcha-{len(seen):03d}" if diagnostics else None
    if capture:
        capture.with_suffix(".png").write_bytes(png)
    answer = _recognize(task, "4x4" if count == 16 else "3x3", png, count, deadline)
    selected = [index + 1 for index, hit in enumerate(answer) if hit]
    log.info(
        "Solvecha task %r: selected tiles %s (row order, starting at 1)", task, selected
    )
    if capture:
        capture.with_suffix(".json").write_text(
            json.dumps(
                {
                    "instructions": instructions,
                    "task": task,
                    "grid": "4x4" if count == 16 else "3x3",
                    "answer": answer,
                    "selected_tiles": selected,
                    "tile_order": "left to right, top to bottom, starting at 1",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    if table.evaluate(GRID_STATE, timeout=_remaining_ms(deadline)) != state:
        log.warning("Grid changed during recognition; discarding stale answer")
        return
    _click_answer(
        challenge, answer, deadline, dynamic="none left" in instructions.lower()
    )


def solve(
    page: Page, timeout: float, max_rounds: int, diagnostics: Path | None = None
) -> bool:
    """Solve one challenge with bounded image rounds and no page reloads."""
    deadline = time.monotonic() + timeout
    anchor = page.frame_locator(CHALLENGE).first.locator("#recaptcha-anchor")
    anchor.click(timeout=min(10000, timeout * 1000))
    challenge = page.frame_locator('iframe[src*="/recaptcha/api2/bframe"]').first
    seen = set()
    for round_number in range(max_rounds):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            log.warning("CAPTCHA time limit reached after %d rounds", round_number)
            return False
        if wait_verified(page, min(2.5, remaining)):
            return True
        _solve_round(challenge, deadline, seen, diagnostics)
        log.info("CAPTCHA recognition round %d/%d", round_number + 1, max_rounds)
    verified = wait_verified(page, min(5, max(0.001, deadline - time.monotonic())))
    if not verified:
        log.warning("CAPTCHA round limit reached (%d)", max_rounds)
    return verified


def handle_captcha(
    page: Page,
    automatic: bool = False,
    timeout: float = 180,
    max_rounds: int = 20,
    manual: bool = True,
    diagnostics: Path | None = None,
) -> str:
    """Run recognition only when a widget exists, then offer manual completion."""
    if not page.locator(CHALLENGE).count():
        return "not-present"
    if page.evaluate(TOKEN_READY) is True:
        return "verified-manually"
    if automatic:
        try:
            if solve(page, timeout, max_rounds, diagnostics):
                return "verified-client-token"
            log.warning(
                "No Google verification token within the configured round/time limits"
            )
        except (Error, OSError, ValueError, RuntimeError) as error:
            log.warning(
                "CAPTCHA automatic verification failed (%s): %s",
                type(error).__name__,
                failure_reason(error),
            )
    if manual:
        log.warning(
            "Manual CAPTCHA completion available in the visible browser for %ss",
            timeout,
        )
        if wait_verified(page, timeout):
            return "verified-manually"
    return "manual-required"
