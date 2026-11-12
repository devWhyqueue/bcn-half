# bcn-half

Watch the Barcelona Half 2027 marketplace, notify Telegram when bibs appear, and
optionally request their transfer with your email addresses. The HTTP checker stays
lightweight; browser workflows solve a reCAPTCHA only when a challenge is present,
locally and for free: [PyPasser](https://pypi.org/project/PyPasser/) answers the
audio challenge with offline Vosk speech recognition in a
[Patchright](https://pypi.org/project/patchright/) browser. No solver key is needed.

```text
src/bcn_half/       watcher, application workflow, local CAPTCHA solver
tests/             mocked API tests and local Chromium fixtures
pyproject.toml     package, commands, dependencies, and tool configuration
uv.lock            locked runtime and development dependencies
```

## Setup

Requires Python 3.12 or newer and uv; local development pins Python 3.12.

```sh
cp .env.example .env
uv sync
uv run bcn-half
```

Set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_IDS` (a comma-separated list of chat IDs), and
`WATCH_URL`. The event URL is required; there is no built-in fallback.
Plain `.env` assignments load automatically without replacing values already in the
process environment. `POLL_SECONDS` is optional and defaults to 10. Notifications
use the configured URL; `--event-url` explicitly overrides it for a particular run.

The default command checks and notifies only. To apply for listed
bibs on a desktop machine, set `APPLY=true` in `.env` (or pass `--apply`) together
with `MAIL_ADDRESSES` (comma-separated). Applying without it exits with an error.
Optional `MAX_PRICE`
(euros, e.g. `110` or `110,50`) skips listings that cost more.

```sh
uv run patchright install chromium
uv run bcn-half --apply
```

One browser attempt runs at a time while HTTP polling continues. Each reference is
attempted once per run; restarting can reattempt listed bibs. Each attempt solves
the CAPTCHA once (at most three audio clips). The first solve downloads the Vosk
model (~39 MB) unless it is already cached; the Docker image ships it. Solving needs
`ffmpeg` on `PATH`. If Google blocks the audio challenge, the attempt is reported
as `captcha-blocked` with the event link so you can try by hand; a failed solve is
reported as `captcha-failed`. Neither is retried, and the next bib is attempted
normally.

Each new bib gets the first address not yet accepted. The email is submitted only
once the CAPTCHA is solved or absent. The site titles every reply `Warning`, so its
body decides: "REGISTRATION RESERVED FOR 24 HOURS" marks the address used, anything
else (for example a failed robot check) keeps it for the next bib. The site's reply is included in the Telegram message. When every address is
used, the bot notifies Telegram and exits. The confirmation link arrives by email
and must be completed within 24 hours; payment is never automated.

HTTP 429 and transient server errors receive at most two retries with exponential
backoff. The five-failed-polls checker alert and recovery message remain enabled.
Application and solver failures are reported separately from checker failures.

## Marketplace test

```sh
uv run bcn-half --test-application --event-url "https://inscripciones.rpmsports.es/en/event/zurich-rock-n-roll-running-series-madrid-2027/transfer-registration"
```

**This sends a real transfer request.** It selects an exact listing reference,
clicks its **Apply for** action, verifies the matching form, solves the CAPTCHA, and
submits the first address in `MAIL_ADDRESSES`. It
exits 0 only when the site accepts the email. No Telegram message is sent.

The test keeps the final browser state open until you close it or press
**Resume** in Playwright Inspector. `--no-keep-open` closes automatically. A failed
test returns a nonzero status. Screenshots, sanitized logs, and token-free JSON
results go into timestamped `artifacts/` directories, which remain gitignored.
`automated-result.json` preserves the marketplace result before inspection;
the state after inspection is recorded separately in `result.json`.
Each attempt also saves `submit-response.txt` (raw server reply to the submit POST)
and `submitted.html`. `--test-application` additionally records `trace.zip` (open
with `uv run patchright show-trace`); it contains the submitted email and the spent
CAPTCHA token. Credentials are never printed or saved. Tests send no Telegram
notifications.

## Development

`uv sync` includes the `dev` dependency group: click, Pyright, pysonar, pytest,
pytest-cov, Ruff, truststore, and Vulture. `uv sync --no-dev` installs runtime only.

```sh
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright
uv run vulture src --min-confidence 80
uv build
```

Tests block live HTTP requests from the application's clients. Browser fixtures
use local content and never contact Google. CLI `--help` avoids loading
`.env`. `uv run python -m bcn_half` is equivalent to the installed watcher command.

## Docker

```sh
docker compose up -d --build
docker compose logs -f
```

The image installs the locked package without dev dependencies, Playwright's
Chromium, and Xvfb. The watcher runs under `xvfb-run`, so `APPLY=true` works on
headless hosts (amd64 or arm64) with the same headed browser as on a desktop.
Compose sets `shm_size: 1gb` for Chromium and `init: true`
so `docker stop` shuts down cleanly. Diagnostic `artifacts/` stay inside the
container and are lost on rebuild.
`.dockerignore` excludes `.env`, environments, and diagnostic artifacts from the
build context; Compose supplies runtime variables through `env_file: .env`.
