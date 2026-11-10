# bcn-half

Polls the Barcelona Half 2027 MarketPlace page and sends a Telegram message when a bib is listed.
The availability checker uses only Python's standard library. The optional application
workflow uses Playwright in a visible Chromium window.

**Solvecha is the normal bot provider**, including `--test-captcha`, using
`SOLVECHA_API_KEY`. CaptchaNo is available only through the separate demo command.

```text
src/bcn_half/       watcher, application workflow, CAPTCHA integration, demo
tests/             unit tests and local Chromium fixtures
docs/              recorded live-test results
pyproject.toml     package, commands, dependencies, and tool configuration
uv.lock            locked runtime and development dependencies
```

## Setup

```sh
cp .env.example .env   # fill TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
uv sync
uv run bcn-half
```

Local run: `uv run bcn-half`. Plain assignments in `.env` load automatically; existing
process environment values take precedence. Defaults are the English Barcelona page
and 10-second polling. `WATCH_URL` also controls the URL in Telegram messages.

HTTP 429 and transient server errors get at most two retries with exponential backoff.
The existing five-failed-polls checker alert and recovery message remain enabled.

The project requires Python 3.12 or newer and pins 3.12 for local development.
`uv sync` includes the `dev` dependency group: click, Pyright, pysonar, pytest,
pytest-cov, Ruff, truststore, and Vulture. Use `uv sync --no-dev` for runtime only.

```sh
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright
uv run vulture src --min-confidence 80
uv build
```

Tests block live HTTP requests from the application's clients; browser fixtures use
intercepted local content and do not consume solver credits. CLI `--help` also
avoids loading `.env`. Both `uv run bcn-half` and `uv run python -m bcn_half` start
the watcher. The former root-level Python scripts have moved into the package.

## Docker

```sh
docker compose up -d --build
docker compose logs -f
```

The image installs the locked package with uv and excludes dev dependencies.
`.dockerignore` keeps `.env`, local environments, and diagnostic artifacts out of the
build context. Compose continues to load runtime variables from `.env`.

## Application tests

Install Chromium on a machine with a desktop for the optional browser workflows:

```sh
uv sync
uv run playwright install chromium
uv run bcn-half --test-captcha
uv run bcn-half --test-application --keep-open --event-url "https://inscripciones.rpmsports.es/en/event/zurich-rock-n-roll-running-series-madrid-2027/transfer-registration"
```

`--test-captcha` uses `SOLVECHA_API_KEY` on Google's public demo, following the
[documented Python API integration](https://github.com/Solvecha/solvecha-playwright-example/blob/main/python/solve_with_api.py).
It does not submit the demo form. Recognition is bounded by
`CAPTCHA_TIMEOUT_SECONDS=180` and `CAPTCHA_MAX_ROUNDS=20` (maximum 50 rounds), with no
page reloads. Each round is one image-recognition request, not a complete CAPTCHA.
The solver waits for clicked dynamic tiles to change image source, finish loading,
and settle after their fade animations before capturing the next grid. It discards
answers if the grid changes while recognition is running. Logs distinguish reaching
the time or round limit from receiving Google's verification token.

The Madrid test selects only a half-marathon entry, clicks its exact `Apply for`
action, and checks the reference on the email/CAPTCHA form. Live tests always use
manual verification. Both test modes keep the **final state open by default**, on
success or failure, without a manual-inspection timeout. Inspect the page or complete
only the CAPTCHA, then press **Resume** in Playwright Inspector when finished.
The automated result is saved before inspection; any subsequent manual verification
is recorded separately in the final result. `automated-result.json` preserves the
original automatic outcome. `--no-keep-open` closes automatically for unattended tests.
`--keep-open` remains accepted as an explicit version of the default.

No email is entered. The transfer button is disabled, and its submission requests
are blocked. A client CAPTCHA token does not prove server acceptance; verifying
that would require submitting the form, which this workflow never does.

Screenshots, sanitized diagnostic logs, and `result.json` are saved in timestamped
`artifacts/` directories (ignored by Git). A failed verification returns a nonzero
test exit status. Tests do not send Telegram messages.
Each recognition round also saves its exact input PNG, prompt, Boolean answer, and
selected tile numbers in `captcha-NNN.png/json`. Numbers run left to right, top to
bottom, starting at 1, so incorrect predictions can be distinguished from click errors.

### Full diagnostic run (PowerShell)

From the repository directory, with `SOLVECHA_API_KEY` in `.env`:

```powershell
$env:CAPTCHA_MAX_ROUNDS = '50'
$env:CAPTCHA_TIMEOUT_SECONDS = '600'
uv run bcn-half --test-captcha
```

This uses Google's public demo and continues until Google provides a verification
token, 50 recognition rounds or 10 minutes are reached, or an error/safety stop
occurs. The higher budget allows slow image replacements and chained challenges to
finish; completion is not guaranteed. Each recognition request can count toward
the Solvecha quota. No demo form, registration, or payment is submitted.

Watch the run without clicking challenge tiles so its automatic outcome stays
interpretable. The browser stays open after automation stops. Before any manual
solving, inspect the saved evidence in the timestamped `artifacts/` folder:

- `captcha-NNN.png`: the exact grid sent to Solvecha in that round.
- `captcha-NNN.json`: the prompt and returned Boolean answer / selected tile numbers.
- `workflow.log`: actual clicked tile numbers, replacement waits, and the stop reason.
- `result.png` and `automated-result.json`: the final automatic state and outcome.

Review rounds in numerical order and find the first input PNG where the selected
tile numbers do not match the requested object. That isolates a recognition issue;
correct predictions paired with different click numbers point to an interaction
issue. The program does not itself know which objects are in an image, so a
successful API response is not an accuracy check.

When finished inspecting, click **Resume** in Playwright Inspector or close the
browser. Any later manual verification is recorded in `result.json` without
overwriting `automated-result.json`. Restore the normal limits for later runs:

```powershell
Remove-Item Env:CAPTCHA_MAX_ROUNDS
Remove-Item Env:CAPTCHA_TIMEOUT_SECONDS
```

## CaptchaNo public-demo test

Set `CAPTCHANO_API_KEY` (or `CAPTCHANO_KEY`) in `.env`, then run:

```powershell
uv run captchano-demo
```

The script loads the key privately and follows [CaptchaNo's Playwright integration](https://captchano.com/for/playwright).
It creates one remote reCAPTCHA v2 task, polls that task every five seconds, and
submits its returned token only to Google's exact public demo URL. The demo's
illustrative name/email fields stay disabled and use synthetic display values.
Success requires Google's **Verification Success** page; receiving a token alone
does not pass the test. No marketplace application is opened or submitted.

The browser stays open for inspection. Screenshots, a sanitized log, and a token-free
`result.json` are saved under `artifacts/captchano-.../`. Use `--timeout 600` for a
longer task deadline or `--no-keep-open` to close automatically. No credentials or
solution tokens are printed or saved. This standalone test does not switch the
Barcelona workflow's provider or enable production application handling.

## Optional Barcelona workflow

Keep `APPLICATION_ENABLED=false` until both local tests succeed. Then set it to
`true` on a desktop machine to open one matching bib at a time while HTTP polling
continues. Telegram reports the reference, price, watched event URL, workflow status,
and CAPTCHA status. Browser failures are separate from checker failures.

`SOLVECHA_LIVE_ENABLED` defaults to `false`. Enable it only after confirming the
platform permits unattended solving. Attempts to retrieve the platform rules during
implementation were unsuccessful, so permission has **not** been established.
An automatic failure offers a visible manual window. No workflow submits an email,
binding application, or payment, even when automatic solving is enabled.

The supplied Docker image runs the checker only; browser workflows require the
optional dependencies and a desktop. `APPLICATION_TEST_MODE=true` runs one test
instead of the normal checker. `.env.example` lists all settings.

Recorded live-test evidence and its limits: [test results](docs/test-results.md).

Seen listings and attempted references are kept in memory only. A restart re-alerts
and can reattempt bibs still listed; within one run, a reference is attempted once.
