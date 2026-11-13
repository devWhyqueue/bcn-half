import gzip
import http.client
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

import pytest

from bcn_half import marketplace


@pytest.fixture
def marketplace_server():
    requests = []
    responses = [(200, {}, b"available")]

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            requests.append((self.client_address, self.path, self.headers))
            status, headers, body = responses.pop(0)
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            f"http://127.0.0.1:{server.server_port}/listings?event=1",
            requests,
            responses,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_reuses_connection_and_decodes_gzip(marketplace_server):
    url, requests, responses = marketplace_server
    responses[:] = [
        (200, {"Content-Encoding": "gzip"}, gzip.compress("50 €".encode()))
    ] * 2
    client = marketplace.MarketplaceClient(url)
    try:
        assert client.fetch() == "50 €"
        assert client.fetch() == "50 €"
        assert len(requests) == 2
        assert requests[0][0] == requests[1][0]
        assert requests[0][1] == "/listings?event=1"
        assert requests[0][2]["Accept-Encoding"] == "gzip"
    finally:
        client.close()
    assert client.connection.sock is None


@pytest.mark.parametrize(
    "status,headers,delay", [(429, {"Retry-After": "25"}, 25), (503, {}, 10)]
)
def test_transient_http_errors_keep_backoff(marketplace_server, status, headers, delay):
    url, requests, responses = marketplace_server
    responses[:] = [(status, headers, b"busy"), (200, {}, b"available")]
    client = marketplace.MarketplaceClient(url)
    try:
        with patch("bcn_half.marketplace.time.sleep") as sleep:
            assert client.fetch() == "available"
        sleep.assert_called_once_with(delay)
        assert len(requests) == 2
    finally:
        client.close()


def test_http_retries_are_bounded(marketplace_server):
    url, requests, responses = marketplace_server
    responses[:] = [(503, {}, b"busy")] * 3
    client = marketplace.MarketplaceClient(url)
    try:
        with patch("bcn_half.marketplace.time.sleep") as sleep:
            with pytest.raises(urllib.error.HTTPError) as error:
                client.fetch()
        assert error.value.code == 503
        assert [call.args[0] for call in sleep.call_args_list] == [10, 20]
        assert len(requests) == 3
    finally:
        client.close()


def test_stale_socket_reconnects_once_without_backoff():
    response = Mock(status=200)
    response.read.return_value = b"available"
    response.getheader.return_value = ""
    with patch("bcn_half.marketplace.http.client.HTTPSConnection") as connection:
        connection.return_value.request.side_effect = [
            http.client.RemoteDisconnected(),
            None,
        ]
        connection.return_value.getresponse.return_value = response
        client = marketplace.MarketplaceClient("https://example.org/listings")
        try:
            with patch("bcn_half.marketplace.time.sleep") as sleep:
                assert client.fetch() == "available"
            assert connection.return_value.request.call_count == 2
            connection.return_value.close.assert_called_once()
            sleep.assert_not_called()
        finally:
            client.close()


def test_corrupt_gzip_is_reported_as_a_poll_failure(marketplace_server):
    url, requests, responses = marketplace_server
    responses[:] = [(200, {"Content-Encoding": "gzip"}, b"\x1f\x8b")] * 2
    client = marketplace.MarketplaceClient(url)
    try:
        with pytest.raises(OSError):
            client.fetch()
        assert len(requests) == 2
    finally:
        client.close()


def test_redirects_are_followed_on_each_poll(marketplace_server):
    url, requests, responses = marketplace_server
    responses[:] = [(302, {"Location": "/ready"}, b""), (200, {}, b"available")] * 2
    client = marketplace.MarketplaceClient(url)
    try:
        assert client.fetch() == "available"
        assert client.fetch() == "available"
        assert [request[1] for request in requests] == [
            "/listings?event=1",
            "/ready",
            "/listings?event=1",
            "/ready",
        ]
    finally:
        client.close()


def test_redirect_loops_are_bounded(marketplace_server):
    url, requests, responses = marketplace_server
    responses[:] = [(302, {"Location": url}, b"")] * 6
    client = marketplace.MarketplaceClient(url)
    try:
        with pytest.raises(urllib.error.HTTPError):
            client.fetch()
        assert len(requests) == 6
    finally:
        client.close()
