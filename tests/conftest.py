"""Keep all tests independent of live HTTP services."""

import http.client
import urllib.request

import pytest


@pytest.fixture(autouse=True)
def block_live_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """Require tests to replace HTTP calls explicitly instead of using live APIs."""

    def unexpected_request(*args: object, **kwargs: object) -> None:
        raise AssertionError("Live HTTP requests are disabled in tests")

    monkeypatch.setattr(urllib.request, "urlopen", unexpected_request)
    original_request = http.client.HTTPConnection.request

    def local_request(connection, *args, **kwargs):
        if connection.host not in ("127.0.0.1", "localhost", "::1"):
            raise AssertionError("Live HTTP requests are disabled in tests")
        return original_request(connection, *args, **kwargs)

    monkeypatch.setattr(http.client.HTTPConnection, "request", local_request)
