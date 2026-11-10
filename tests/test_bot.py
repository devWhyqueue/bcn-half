import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from bcn_half import bot
from bcn_half.bot import parse_listings

HEAD = """<html><body><table class="other"><tbody><tr><td>x</td></tr></tbody></table>
<table class="table table-listados air-top"><thead><tr><th>Ref.</th><th>Tarifa</th>
<th>Importe</th><th>Requiere validación</th></tr></thead><tbody>"""
TAIL = "</tbody></table></body></html>"
ROW = "<tr><td>{}</td><td>{}</td><td>{}</td><td>No</td></tr>"


class ParseTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(parse_listings(HEAD + TAIL), [])

    def test_rows(self):
        html = (
            HEAD
            + ROW.format("A1", "Media", "45 €")
            + ROW.format("B2", "Media", "50 €")
            + TAIL
        )
        rows = parse_listings(html)
        self.assertEqual(len(rows), 2)
        self.assertIn("A1", rows[0])
        self.assertIn("Media", rows[0])
        self.assertIn("45 €", rows[0])

    def test_missing_table(self):
        self.assertIsNone(parse_listings("<html><body>nothing</body></html>"))


class PollingTest(unittest.TestCase):
    def test_english_default(self):
        self.assertIn("/en/event/", bot.DEFAULT_URL)

    def test_fetch_retries_rate_limit(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"available"
        error = urllib.error.HTTPError(
            "https://example.org", 429, "rate limit", {}, None
        )
        with (
            patch(
                "bcn_half.bot.urllib.request.urlopen",
                side_effect=[error, error, response],
            ) as request,
            patch("bcn_half.bot.time.sleep") as sleep,
        ):
            self.assertEqual(bot.fetch("https://example.org"), "available")
        self.assertEqual(request.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [10, 20])

    def test_fetch_retries_are_bounded(self):
        error = urllib.error.HTTPError(
            "https://example.org", 503, "unavailable", {}, None
        )
        with patch("bcn_half.bot.urllib.request.urlopen", side_effect=error) as request:
            with (
                patch("bcn_half.bot.time.sleep"),
                self.assertRaises(urllib.error.HTTPError),
            ):
                bot.fetch("https://example.org")
        self.assertEqual(request.call_count, 3)

    def test_env_preserves_process_values(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"POLL_SECONDS": "12"}),
        ):
            path = Path(directory) / ".env"
            path.write_text(
                'POLL_SECONDS=10\nWATCH_URL="https://example.org"\n', encoding="utf-8"
            )
            bot.load_env(path)
            self.assertEqual(os.environ["POLL_SECONDS"], "12")
            self.assertEqual(os.environ["WATCH_URL"], "https://example.org")


class WorkflowPollingTest(unittest.TestCase):
    def test_five_poll_failures_alert_once_then_recover(self):
        with patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "fake", "TELEGRAM_CHAT_ID": "1"},
            clear=True,
        ):
            with (
                patch("bcn_half.bot.load_env"),
                patch("sys.argv", ["bcn_half.bot.py"]),
                patch("bcn_half.bot.send") as send,
            ):
                with patch(
                    "bcn_half.bot.fetch",
                    side_effect=[OSError("offline")] * 5 + [HEAD + TAIL],
                ):
                    with patch(
                        "bcn_half.bot.time.sleep",
                        side_effect=[None] * 5 + [KeyboardInterrupt],
                    ):
                        with self.assertRaises(KeyboardInterrupt):
                            bot.main()
        messages = [call.args[2] for call in send.call_args_list]
        self.assertEqual(sum("checker BROKEN" in message for message in messages), 1)
        self.assertTrue(any("checker recovered" in message for message in messages))
        self.assertIn("every 10s", messages[0])

    def test_application_failure_does_not_break_checker_or_repeat_attempt(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "fake",
            "TELEGRAM_CHAT_ID": "1",
            "APPLICATION_ENABLED": "true",
        }
        workflow = Mock()
        workflow.select_listing.side_effect = lambda rows: (
            Mock(reference="REF2", price="70 €") if rows else None
        )
        future = Mock()
        future.done.return_value = True
        future.exception.return_value = RuntimeError("browser failed")
        future.result.return_value = "Bib workflow failed: verification"
        executor = Mock()
        executor.submit.return_value = future
        with (
            patch.dict(os.environ, env, clear=True),
            patch("bcn_half.bot.load_env"),
            patch("sys.argv", ["bcn_half.bot.py"]),
        ):
            with (
                patch("bcn_half.bot.import_module", return_value=workflow),
                patch("bcn_half.bot.send") as send,
            ):
                with patch("bcn_half.bot.ThreadPoolExecutor", return_value=executor):
                    with patch(
                        "bcn_half.bot.fetch",
                        return_value=HEAD
                        + ROW.format("REF2", "Half marathon", "70 €")
                        + TAIL,
                    ):
                        with patch(
                            "bcn_half.bot.time.sleep",
                            side_effect=[None, KeyboardInterrupt],
                        ):
                            with self.assertRaises(KeyboardInterrupt):
                                bot.main()
        executor.submit.assert_called_once()
        messages = [call.args[2] for call in send.call_args_list]
        self.assertTrue(
            any(
                "workflow failed for REF2: RuntimeError" in message
                for message in messages
            )
        )
        self.assertFalse(any("checker BROKEN" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
