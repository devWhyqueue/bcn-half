"""Exercise delayed replacement images in a local Chromium fixture."""

import time
import unittest

from playwright.sync_api import sync_playwright

from bcn_half import captcha


class ReplacementImageTest(unittest.TestCase):
    def test_tile_indices_match_row_order(self):
        with sync_playwright() as playwright, playwright.chromium.launch() as browser:
            page = browser.new_page()
            cells = "".join(
                f"<tr>{''.join(f'<td class=rc-imageselect-tile data-index={3 * row + col}>tile</td>' for col in range(3))}</tr>"
                for row in range(3)
            )
            html = f"""<table>{cells}</table><button id="recaptcha-verify-button">Verify</button>
            <script>window.clicked=[]; document.querySelectorAll('td').forEach(td => td.onclick=()=>window.clicked.push(Number(td.dataset.index)));</script>"""
            page.set_content("<iframe></iframe>")
            page.locator("iframe").evaluate(
                "(frame, html) => frame.srcdoc = html", html
            )
            challenge = page.frame_locator("iframe")
            captcha._click_answer(
                challenge,
                [i in (0, 4, 8) for i in range(9)],
                time.monotonic() + 10,
                dynamic=False,
            )
            self.assertEqual(
                challenge.locator("body").evaluate("() => window.clicked"), [0, 4, 8]
            )

    def test_click_waits_for_delayed_replacement_and_fade(self):
        image = "<img src=\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='20' height='20'%3E%3C/svg%3E\">"
        cells = "".join(
            f'<td class="rc-imageselect-tile">{image}</td>' for _ in range(9)
        )
        html = f"""<table class="rc-imageselect-table-33"><tr>{cells}</tr></table>
        <button id="recaptcha-verify-button">Verify</button>
        <script>
        const tile = document.querySelector('td');
        tile.onclick = () => setTimeout(() => {{
            const img = tile.querySelector('img');
            img.style.transition = 'opacity 0.4s';
            img.style.opacity = '0.2';
            img.onload = () => {{ img.style.opacity = '1'; }};
            img.src = 'https://fixture.test/replacement.svg';
        }}, 400);
        </script>"""
        with sync_playwright() as playwright, playwright.chromium.launch() as browser:
            page = browser.new_page()

            def delayed_image(route):
                time.sleep(0.5)
                route.fulfill(
                    content_type="image/svg+xml",
                    body='<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"/>',
                )

            page.route("https://fixture.test/replacement.svg", delayed_image)
            page.set_content("<iframe></iframe>")
            page.locator("iframe").evaluate(
                "(frame, html) => frame.srcdoc = html", html
            )
            challenge = page.frame_locator("iframe")
            challenge.locator("img").first.wait_for()
            started = time.monotonic()
            captcha._click_answer(
                challenge, [True] + [False] * 8, started + 10, dynamic=True
            )
            ready = challenge.locator("img").first.evaluate(
                "img => img.complete && img.currentSrc.includes('replacement.svg') && getComputedStyle(img).opacity === '1'"
            )
            self.assertTrue(
                ready,
                "The selected replacement image must finish loading and fading before recognition",
            )
            self.assertGreater(time.monotonic() - started, 1)
