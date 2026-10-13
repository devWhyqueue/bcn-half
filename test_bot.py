import unittest

from bot import parse_listings

HEAD = """<html><body><table class="other"><tbody><tr><td>x</td></tr></tbody></table>
<table class="table table-listados air-top"><thead><tr><th>Ref.</th><th>Tarifa</th>
<th>Importe</th><th>Requiere validación</th></tr></thead><tbody>"""
TAIL = "</tbody></table></body></html>"
ROW = "<tr><td>{}</td><td>{}</td><td>{}</td><td>No</td></tr>"


class ParseTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(parse_listings(HEAD + TAIL), [])

    def test_rows(self):
        html = HEAD + ROW.format("A1", "Media", "45 €") + ROW.format("B2", "Media", "50 €") + TAIL
        rows = parse_listings(html)
        self.assertEqual(len(rows), 2)
        self.assertIn("A1", rows[0])
        self.assertIn("Media", rows[0])
        self.assertIn("45 €", rows[0])

    def test_missing_table(self):
        self.assertIsNone(parse_listings("<html><body>nothing</body></html>"))


if __name__ == "__main__":
    unittest.main()
