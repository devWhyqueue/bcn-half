"""Keep all tests independent of live HTTP services."""

import urllib.request

import pytest


@pytest.fixture(autouse=True)
def block_live_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """Require tests to replace HTTP calls explicitly instead of using live APIs."""

    def unexpected_request(*args: object, **kwargs: object) -> None:
        raise AssertionError("Live HTTP requests are disabled in tests")

    monkeypatch.setattr(urllib.request, "urlopen", unexpected_request)
