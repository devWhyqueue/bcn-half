import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from playwright.sync_api import Error

from bcn_half import application


class SelectionTest(unittest.TestCase):
    def test_final_inspection_preserves_automatic_failure(self):
        page = Mock()
        result = application.ApplicationResult(
            None,
            "https://example.org",
            "demo-opened",
            "a@example.org",
            "manual-required",
        )
        page.is_closed.return_value = False
        page.evaluate.return_value = True
        application._inspect_result(page, result)
        page.pause.assert_called_once()
        self.assertEqual(result.captcha, "verified-manually")

    def test_final_inspection_keeps_provider_token_distinct_from_manual_verification(
        self,
    ):
        page = Mock()
        page.is_closed.return_value = False
        page.evaluate.return_value = True
        result = application.ApplicationResult(
            None,
            "https://example.org",
            "application-ready",
            "a@example.org",
            "token-ready",
        )
        application._inspect_result(page, result)
        self.assertEqual(result.captcha, "token-ready")

    def test_browser_start_failure_is_reported_and_logged(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("bcn_half.artifacts.Path", return_value=Path(directory)):
                with patch("bcn_half.application.sync_playwright") as playwright:
                    playwright.return_value.__enter__.return_value.chromium.launch.side_effect = Error(
                        "no browser"
                    )
                    result = application.run_application(
                        "https://inscripciones.rpmsports.es/en/event/test/transfer-registration",
                        email="a@example.org",
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

    def test_max_price_skips_expensive_listings(self):
        rows = ["REF1 · Half marathon · 1.120,00 €", "REF2 · Half marathon · 103,07 €"]
        self.assertEqual(
            application.select_listing(rows, max_price=103.07).reference, "REF2"
        )
        self.assertIsNone(application.select_listing(rows, max_price=100))

    def test_notification_keeps_event_and_reference(self):
        listing = application.select_listing(["REF2 · Half marathon · 70 €"])
        result = application.ApplicationResult(
            listing,
            "https://example.org/event",
            "ready",
            "a@example.org",
            "not-present",
        )
        self.assertIn("REF2", result.message())
        self.assertIn("70 €", result.message())
        self.assertIn("https://example.org/event", result.message())
        self.assertIn("ready", result.message())

    def test_submit_email_classifies_site_reply(self):
        replies = (
            ("We could not verify that you are not a robot.", "email-rejected"),
            (
                "REGISTRATION RESERVED FOR 24 HOURS.\nWe have sent an email",
                "email-submitted",
            ),
        )
        for body, status in replies:
            page = MagicMock()
            page.locator.return_value.inner_text.side_effect = ["Warning", body]
            result = application.ApplicationResult(
                None, "https://example.org", "application-ready", "a@example.org"
            )
            with patch("bcn_half.application._record_reply"):
                application._submit_email(page, result, Path("."))
            self.assertEqual(result.status, status)
            self.assertIn("a@example.org", result.message())
            self.assertIn(f"Warning: {body}", result.message())
