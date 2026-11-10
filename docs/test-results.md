# Local verification — 10 November 2026

## Madrid: navigation and manual CAPTCHA passed

Executed the visible Chromium workflow against the live Madrid 2027 marketplace.
The successful run parsed 81 entries, selected **REF33802**, **MEDIO MARATÓN**,
**103,07 €**, and clicked that row's **Apply for** action. The matching email and
CAPTCHA form opened. The user completed the CAPTCHA manually; the test detected
the client verification token and exited successfully.

- [Final screenshot](../artifacts/20261110T115831494910Z/result.png)
- [Workflow log](../artifacts/20261110T115831494910Z/workflow.log)
- [Machine-readable result](../artifacts/20261110T115831494910Z/result.json)

The email field remained empty and the transfer button was disabled. No email,
application, registration, or payment was submitted. Server-side acceptance and
later email-link steps were deliberately not tested.

The first manual attempt timed out while the user was solving. `--keep-open` was
added and used for the successful rerun so manual inspection ends with the user's
Resume action. One intermediate navigation attempt timed out; its failure screenshot
is retained under `artifacts/20261110T115729268706Z/`.

## Solvecha: standalone verification did not pass

Executed four bounded runs against Google's public reCAPTCHA demo. Solvecha returned
tile-recognition answers, but no client verification token was obtained. Runs used
3, 6, 10, and 6 recognition rounds respectively. The failed runs remain evidence of
failure, not a successful solver integration test.

The user reported **19 successful solves** in Solvecha after the first three runs.
This matches their **19 successful image-recognition rounds** (3 + 6 + 10). API recognition
success and final Google verification are recorded separately: the API returned
answers, but the browser did not obtain a completed-challenge token.

- [Latest demo screenshot](../artifacts/20261110T120657708509Z/result.png)
- [Latest demo log](../artifacts/20261110T120657708509Z/workflow.log)
- [Latest demo result](../artifacts/20261110T120657708509Z/result.json)

Inspection identified dynamic replacement images and informed the replacement wait
and no-premature-Verify safeguards. A subsequent review also identified an unbounded
JavaScript image-load promise; it was replaced with deadline-checked polling and a
regression test. The final six-round rerun included that fix and again returned
recognition answers without a Google verification token. These changes do not
establish a successful live Solvecha solve.

## Other checks and activation

Unit tests cover parsing, half-marathon/reference selection, transient HTTP retries,
five-failure alerts and recovery, duplicate attempts, failed browser startup,
background exception isolation, CAPTCHA success/failure handling, submission blocking,
and notification reference/price/URL contents. The notification payload was tested
locally; live Telegram delivery was not exercised.

The initial functional suite had 25 passing tests. Pyright and import/undefined-name
lint checks pass. The broader custom clean-code audit reports style findings
(including callback/test annotations and long functions); it is not a passing gate.
Docker was not built or run.

Barcelona remains the configured default. Application automation and unattended
Solvecha remain disabled. The platform's automation rules could not be retrieved;
permission for unattended solving has not been established. Full activation still
requires a successful standalone solver test and the platform permission check.

Diagnostic artifacts are local and ignored by Git; copy them separately if sharing
this report. No credentials were printed or added to source control.

## Follow-up: delayed images and final-state inspection

The user's observation of slowly loading replacement tiles exposed a timing gap:
the old image can still report `complete=true` before replacement loading starts.
A Chromium fixture reproduced the premature return after clicking a dynamic tile.
The fix now waits for each clicked tile's image identity to change, for all images
to finish loading, and for opacity/animations and grid identity to remain stable.
An answer is discarded if the grid changes during its recognition request.

The default limits are now 20 rounds / 180 seconds, configurable up to 50 rounds /
600 seconds. Previous 3/6/10-round tests cannot establish that the service was unable
to finish a full challenge. Both CLI test modes now keep their final state open
until the user resumes inspection, including when automation hits a limit.

The 30-round / 300-second follow-up run used the new replacement wait. Logs show
approximately nine seconds waiting for replacement images after each click group.
The user closed the browser during its seventh recognition round after observing
incorrect non-bus selections. This was a user-stopped run, not a time/round limit
or a completed verification test. Logs are under
`artifacts/20261110T121354978172Z/`. The per-round inputs were not captured in that
run, so its recognition errors cannot be reconstructed from the counts alone.

Per-round input PNGs, prompts, answers, and chosen tile numbers are now saved by the
workflow. A browser regression confirms chosen indices map to the expected row
positions. A separate single-request diagnostic made no answer clicks and left the
browser open for inspection: [input](../artifacts/recognition-20261110T121855Z/input.png),
[predictions](../artifacts/recognition-20261110T121855Z/predictions.png),
[prompt and answer](../artifacts/recognition-20261110T121855Z/recognition.json).
Its fire-hydrant prompt returned tiles 1, 3, and 8; this does not resolve the bus
misclassification observed in the stopped run.

The follow-up suite has 29 passing tests, including the delayed-replacement Chromium
fixture, stale-answer rejection, tile order, and manual result inspection. Automatic
CAPTCHA verification is still unproven and production solving remains disabled.

## CaptchaNo: public demo accepted the returned token

Executed `python captchano_demo.py` using credentials loaded privately from `.env`.
The assistant did not open or display `.env`. One task was accepted by CaptchaNo,
polled every five seconds, and returned a reCAPTCHA v2 token. The script submitted
that token to Google's exact public demo URL and observed **Verification Success...
Hooray!**. End-to-end elapsed time was approximately 52 seconds.

- [Success screenshot](../artifacts/captchano-20261110T124842110873Z/result.png)
- [Sanitized workflow log](../artifacts/captchano-20261110T124842110873Z/workflow.log)
- [Token-free result](../artifacts/captchano-20261110T124842110873Z/result.json)

The result records `provider_ready=true`, `google_accepted=true`, and
`status=google-verified`. The browser was left open for manual inspection. No API
key, solution token, provider balance, or real personal information was printed or
saved. The Google demo's illustrative fields stayed disabled, with synthetic values
shown locally; no marketplace registration or application was submitted.

Initial harness attempts stopped on disabled form fields and an HTTP 403 before an
accepted task was created. The harness now handles disabled demo fields and sends
the HTTP headers needed by the API endpoint. Seven focused tests cover immediate
and queued responses, failed and malformed solutions, disabled fields, URL submission
boundaries, and rejection despite token injection. The full suite now passes 38
tests; Pyright reports no errors for the CaptchaNo demo and CAPTCHA module.

This is one successful public-demo solve, not validation against the Madrid or
Barcelona application. CaptchaNo is currently an isolated demo script; the normal
bot provider and disabled production application settings were not changed.

## uv package reorganization

The runtime code now lives in `src/bcn_half/`, tests in `tests/`, and this report in
`docs/`. `pyproject.toml` and `uv.lock` define the runtime dependency and the clean-code
development dependency group. The installed commands are `uv run bcn-half` and the
explicit, separate `uv run captchano-demo`; the normal bot and `--test-captcha`
continue to use Solvecha.

Verification after the move: 43 tests pass, Ruff checks and formatting pass, Pyright
reports no errors, and Vulture finds no unused code at 80% confidence. Both wheel
and source distributions build. A fresh wheel-only environment runs both command
help screens and `python -m bcn_half --help`; distribution contents exclude `.env`,
local environments, and diagnostic artifacts. Docker configuration was updated for
a locked uv runtime install, but Docker is unavailable on this machine.

All Playwright Chromium windows were closed. Reorganization checks used mocks and
local headless browser fixtures only; **no new solver API requests were made**.
