import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from patchright.sync_api import Error, sync_playwright

from bcn_half import bot, browser
from bcn_half.marketplace import select_listing

URL = "https://inscripciones.rpmsports.es/en/event/test/transfer-registration"
HTML = """<html><body><table class="table-listados"><tbody>
<tr><td>REF1</td><td>RACE ENTRY</td><td>50,00 €</td><td>
<a href="#" onclick="document.querySelector('form').hidden=false; return false;">Apply for</a>
</td></tr></tbody></table>
<form hidden method="post"><p class="lead">Registration 1</p>
<input id="cphCuerpo_tbEmailSolicitarCambio" name="email">
<button id="cphCuerpo_btSolicitarTraspaso">Submit</button></form>
</body></html>"""
REPLY = """<html><body><div id="modal-mensaje"><div id="lblTituloMensaje">Warning</div>
<div id="lblCuerpoMensaje">REGISTRATION RESERVED FOR 24 HOURS</div></div></body></html>"""


@pytest.mark.parametrize(
    "failed_warmup,page_state",
    [(False, "healthy"), (True, "healthy"), (False, "closed"), (False, "crashed")],
)
def test_browser_reuses_page_and_recovers(
    tmp_path, monkeypatch, failed_warmup, page_state
):
    workflow = browser
    launches, browsers, contexts, posts = [], [], [], []

    @contextmanager
    def local_playwright():
        with sync_playwright() as playwright:

            def launch(**kwargs):
                launches.append(kwargs)
                if failed_warmup and len(launches) == 1:
                    raise Error("temporary launch failure")
                browser = playwright.chromium.launch(headless=True)
                browsers.append(browser)
                new_context = browser.new_context

                def local_context(**settings):
                    context = new_context(**settings)
                    contexts.append(context)

                    def respond(route):
                        if route.request.method == "POST":
                            posts.append(route.request.post_data)
                        route.fulfill(
                            content_type="text/html; charset=utf-8",
                            body=REPLY if route.request.method == "POST" else HTML,
                        )

                    context.route("**/*", respond)
                    return context

                monkeypatch.setattr(browser, "new_context", local_context)
                return browser

            yield SimpleNamespace(chromium=SimpleNamespace(launch=launch))

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(workflow, "sync_playwright", local_playwright)
    session = workflow.ApplicationBrowser(URL)
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        executor.submit(session.prepare).result(timeout=15)
        if page_state == "crashed":
            with pytest.raises(Error):
                executor.submit(
                    session.page.goto, "chrome://crash", timeout=1000
                ).result(timeout=5)
        first = executor.submit(session.apply, "REF1", "first@example.org").result(
            timeout=15
        )
        page = session.page
        if page_state == "closed":
            executor.submit(page.close).result(timeout=5)
        second = executor.submit(session.apply, "REF1", "second@example.org").result(
            timeout=15
        )
        assert first[1] is (page_state != "crashed")
        assert second[1] is True
        assert (session.page is page) is (page_state == "healthy")
        assert len(contexts) == 1 + (page_state != "healthy")
        assert len(launches) == 1 + failed_warmup + (page_state != "healthy")
        expected_posts = ["email=second%40example.org"]
        if page_state != "crashed":
            expected_posts.insert(0, "email=first%40example.org")
        assert posts == expected_posts
        assert len(list(tmp_path.glob("artifacts/*/result.json"))) == 2
    finally:
        executor.submit(session.close).result(timeout=5)
        executor.shutdown()
    assert all(not instance.is_connected() for instance in browsers)


def test_dispatch_precedes_all_poll_notifications():
    workflow = Mock(select_listing=select_listing)
    executor = Mock()
    telegram = Mock()
    with patch("bcn_half.bot.import_module", return_value=workflow):
        watcher = bot.Watcher(URL, telegram, ["runner@example.org"], True)
    watcher.executor.shutdown()
    watcher.executor = executor
    watcher.broken = True

    def notify(message):
        assert watcher.pending is not None
        return True

    telegram.notify.side_effect = notify
    with patch.object(watcher.client, "fetch", return_value=HTML):
        watcher._poll()
    executor.submit.assert_called_once()
    assert watcher.pending_reference == "REF1"
    watcher.client.close()


@pytest.mark.parametrize("interrupted", [False, True])
def test_polling_continues_and_cleanup_stays_on_browser_thread(interrupted):
    started, release = threading.Event(), threading.Event()
    calls, polls = [], []

    class Session:
        def __init__(self, url):
            pass

        def prepare(self):
            calls.append(("prepare", threading.get_ident()))

        def apply(self, reference, email):
            calls.append(("apply", threading.get_ident()))
            started.set()
            assert release.wait(5)
            return "accepted", True

        def close(self):
            calls.append(("close", threading.get_ident()))

    def sleep(seconds):
        assert started.wait(5)
        if len(polls) == 2:
            release.set()
            watcher.pending.result(timeout=5)
            if interrupted:
                raise KeyboardInterrupt

    with patch(
        "bcn_half.bot.import_module",
        return_value=SimpleNamespace(ApplicationBrowser=Session),
    ):
        watcher = bot.Watcher(URL, Mock(), ["runner@example.org"], True)
    with (
        patch.object(
            watcher.client, "fetch", side_effect=lambda: polls.append(1) or HTML
        ),
        patch.object(watcher.client, "close") as close_http,
        patch("bcn_half.bot.time.sleep", side_effect=sleep),
    ):
        try:
            if interrupted:
                with pytest.raises(KeyboardInterrupt):
                    watcher.run(1)
            else:
                assert watcher.run(1) == 0
        finally:
            release.set()
            watcher.executor.shutdown()
    assert len(polls) == 2
    assert [name for name, _ in calls] == ["prepare", "apply", "close"]
    assert len({owner for _, owner in calls}) == 1
    assert calls[0][1] != threading.get_ident()
    close_http.assert_called_once()
