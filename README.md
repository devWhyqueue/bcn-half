# bcn-half

Watch the Barcelona Half 2027 marketplace, notify Telegram when bibs appear, and
optionally request their transfer with your email addresses. **CaptchaNo is the default and only CAPTCHA solver.** The HTTP checker stays
lightweight; browser workflows request one remote token only when a challenge is
present. Solvecha's image-selection integration has been removed.

```text
src/bcn_half/       watcher, application workflow, CaptchaNo client and demo
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

The default command checks and notifies only. To apply for matching half-marathon
bibs on a desktop machine, set `APPLY=true` in `.env` (or pass `--apply`) together
with `MAIL_ADDRESSES` (comma-separated) and `CAPTCHANO_API_KEY` (alias
`CAPTCHANO_KEY`). Applying without both exits with an error.

```sh
uv run playwright install chromium
uv run bcn-half --apply
```

Applying consumes CaptchaNo credits. One browser attempt runs at a time while HTTP
polling continues. Each reference is attempted once per run; restarting can
reattempt listed bibs. CaptchaNo receives the actual application URL, sitekey, and
browser user agent; the token is placed in the widget without reloading the session.
If the solve fails, the CAPTCHA can be completed manually in the visible browser
for 300 seconds.

Each new bib gets the first address not yet accepted. The email is submitted only
once the CAPTCHA is solved or absent. The site's reply modal decides the outcome: a
`Warning`/error title keeps the address for the next bib, any other title marks it
used. The site's reply is included in the Telegram message. When every address is
used, the bot notifies Telegram and exits. The confirmation link arrives by email
and must be completed within 24 hours; payment is never automated.

HTTP 429 and transient server errors receive at most two retries with exponential
backoff. The five-failed-polls checker alert and recovery message remain enabled.
Application and solver failures are reported separately from checker failures.

## Public CAPTCHA demo

Both commands use the same CaptchaNo client:

```sh
uv run bcn-half --test-captcha
uv run captchano-demo
```

The client follows [CaptchaNo's Playwright integration](https://captchano.com/for/playwright).
It creates one reCAPTCHA v2 task and polls that task every five seconds within a
fixed 300-second default deadline. There are no image-recognition rounds or
CAPTCHA environment switches. For a longer public-demo deadline:

```sh
uv run captchano-demo --timeout 600
```

The demo submits the returned token only to Google's exact public demo URL using
synthetic display values. Its illustrative name/email fields remain disabled.
Success requires Google's **Verification Success** page, not just token injection.
This public-demo submission does not open or submit a marketplace application.

## Marketplace test

```sh
uv run bcn-half --test-application --event-url "https://inscripciones.rpmsports.es/en/event/zurich-rock-n-roll-running-series-madrid-2027/transfer-registration"
```

**This sends a real transfer request.** It selects an exact half-marathon reference,
clicks its **Apply for** action, verifies the matching form, solves the CAPTCHA, and
submits the first address in `MAIL_ADDRESSES`. `CAPTCHANO_API_KEY` is required. It
exits 0 only when the site accepts the email. No Telegram message is sent.

Both test modes keep the final browser state open until you close it or press
**Resume** in Playwright Inspector. `--no-keep-open` closes automatically. A failed
test returns a nonzero status. Screenshots, sanitized logs, and token-free JSON
results go into timestamped `artifacts/` directories, which remain gitignored.
`automated-result.json` preserves the marketplace result before inspection;
subsequent manual completion is recorded separately in `result.json`.
Credentials and solution tokens are never printed or saved. Tests send no Telegram
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
use local content and do not consume solver credits. CLI `--help` avoids loading
`.env`. `uv run python -m bcn_half` is equivalent to the installed watcher command.

## Docker

```sh
docker compose up -d --build
docker compose logs -f
```

The image installs the locked package without dev dependencies and runs the
notification checker. Applying requires Chromium and a desktop session, so keep
`APPLY=false` for Docker.
`.dockerignore` excludes `.env`, environments, and diagnostic artifacts from the
build context; Compose supplies runtime variables through `env_file: .env`.
