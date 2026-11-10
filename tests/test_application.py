import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from playwright.sync_api import Error

from bcn_half import application


class SelectionTest(unittest.TestCase):
    def test_final_inspection_preserves_automatic_failure(self):
        page = Mock()
        result = application.ApplicationResult(
            None, "https://example.org", "demo-opened", "manual-required"
        )
        page.is_closed.return_value = False
        page.evaluate.return_value = True
        application._inspect_result(page, result)
        page.pause.assert_called_once()
        self.assertEqual(result.captcha, "verified-manually")

    def test_browser_start_failure_is_reported_and_logged(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("bcn_half.application.Path", return_value=Path(directory)):
                with patch("bcn_half.application.sync_playwright") as playwright:
                    playwright.return_value.__enter__.return_value.chromium.launch.side_effect = Error(
                        "no browser"
                    )
                    result = application.run_application(
                        "https://inscripciones.rpmsports.es/en/event/test/transfer-registration"
                    )
            self.assertIn("application-failed", result.status)
            self.assertEqual(len(list(Path(directory).glob("*/result.json"))), 1)

    def test_selects_half_not_marathon(self):
        rows = [
            "REF1 · MARATÓN · 100 € · Apply for",
            "REF2 · MEDIO MARATÓN · 70 € Detail · Apply for",
        ]
        listing = application.select_listing(rows)
        self.assertEqual(listing.reference, "REF2")
        self.assertEqual(listing.price, "70 €")

    def test_no_half_marathon(self):
        self.assertIsNone(application.select_listing(["REF1 · MARATÓN · 100 €"]))

    def test_matches_reference_exactly(self):
        rows = ["REF21 · Half marathon · 80 €", "REF2 · Half marathon · 70 €"]
        self.assertEqual(application.select_listing(rows, "REF2").price, "70 €")
        self.assertIsNone(application.select_listing(rows, "REF3"))

    def test_notification_keeps_event_and_reference(self):
        listing = application.select_listing(["REF2 · Half marathon · 70 €"])
        result = application.ApplicationResult(
            listing, "https://example.org/event", "ready", "not-present"
        )
        self.assertIn("REF2", result.message())
        self.assertIn("70 €", result.message())
        self.assertIn("https://example.org/event", result.message())
        self.assertIn("ready", result.message())

    def test_guard_allows_binary_google_captcha_requests(self):
        route = Mock()
        route.request.url = "https://www.google.com/recaptcha/api2/reload"
        route.request.method = "POST"
        route.request.post_data_buffer = b"\xa4\x00binary"
        application._guard_request(route)
        route.continue_.assert_called_once()

    def test_guard_blocks_transfer_submission(self):
        route = Mock()
        route.request.url = (
            "https://inscripciones.rpmsports.es/en/event/madrid/transfer-registration"
        )
        route.request.method = "POST"
        route.request.post_data = (
            "ctl00%24cphCuerpo%24tbEmailSolicitarCambio=runner%40example.org"
        )
        route.request.post_data_buffer = route.request.post_data.encode()
        application._guard_request(route)
        route.abort.assert_called_once()
        route.continue_.assert_not_called()
