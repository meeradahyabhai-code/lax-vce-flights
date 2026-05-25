"""Tests for scripts/parse_shorex.py — HTML parsing, port mapping, price parsing."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

import parse_shorex  # noqa: E402


class TestPortKey(unittest.TestCase):
    def test_dubrovnik(self):
        self.assertEqual(parse_shorex.port_key("Dubrovnik, Croatia"), "dubrovnik")

    def test_kotor(self):
        self.assertEqual(parse_shorex.port_key("Kotor, Montenegro"), "kotor")

    def test_bar(self):
        self.assertEqual(parse_shorex.port_key("Bar, Montenegro"), "bar")

    def test_athens_variants(self):
        self.assertEqual(parse_shorex.port_key("Athens, Greece"), "athens")
        self.assertEqual(parse_shorex.port_key("Piraeus (Athens), Greece"), "athens")
        self.assertEqual(parse_shorex.port_key("Piraeus, Greece"), "athens")

    def test_kusadasi(self):
        self.assertEqual(parse_shorex.port_key("Kusadasi, Turkey"), "kusadasi")

    def test_rhodes(self):
        self.assertEqual(parse_shorex.port_key("Rhodes, Greece"), "rhodes")

    def test_santorini(self):
        self.assertEqual(parse_shorex.port_key("Santorini, Greece"), "santorini")

    def test_istanbul(self):
        self.assertEqual(parse_shorex.port_key("Istanbul, Turkey"), "istanbul")

    def test_ravenna_to_venice(self):
        self.assertEqual(parse_shorex.port_key("Venice (Ravenna), Italy"), "venice")
        self.assertEqual(parse_shorex.port_key("Ravenna, Italy"), "venice")

    def test_unknown(self):
        self.assertEqual(parse_shorex.port_key("Reykjavik, Iceland"), "other")

    def test_case_insensitive(self):
        self.assertEqual(parse_shorex.port_key("DUBROVNIK, CROATIA"), "dubrovnik")


class TestParsePrice(unittest.TestCase):
    def _mk(self, html):
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "html.parser")

    def test_simple_dollar(self):
        el = self._mk("<li><strong>$189.99</strong></li>")
        self.assertEqual(parse_shorex.parse_price(el), 189.99)

    def test_integer_price(self):
        el = self._mk("<li><strong>$50</strong></li>")
        self.assertEqual(parse_shorex.parse_price(el), 50.0)

    def test_thousands_comma(self):
        el = self._mk("<li><strong>$1,249.00</strong></li>")
        self.assertEqual(parse_shorex.parse_price(el), 1249.0)

    def test_embedded_label(self):
        el = self._mk("<li><span>Adult From</span><strong>$139.99</strong></li>")
        self.assertEqual(parse_shorex.parse_price(el), 139.99)

    def test_none_element(self):
        self.assertIsNone(parse_shorex.parse_price(None))

    def test_no_price(self):
        el = self._mk("<li>Free</li>")
        self.assertIsNone(parse_shorex.parse_price(el))


class TestFullParse(unittest.TestCase):
    """End-to-end: parse the real saved NCL shorex HTML into excursions.json."""

    @classmethod
    def setUpClass(cls):
        html_path = ROOT / "data" / "ncl_shorex.html"
        if not html_path.exists():
            raise unittest.SkipTest(f"Fixture missing: {html_path}")
        parse_shorex.main()
        out_path = ROOT / "data" / "excursions.json"
        cls.data = json.loads(out_path.read_text())

    def test_has_excursions(self):
        self.assertGreater(self.data["count"], 0)
        self.assertEqual(self.data["count"], len(self.data["excursions"]))

    def test_all_have_required_fields(self):
        for e in self.data["excursions"]:
            self.assertIn("code", e)
            self.assertIn("title", e)
            self.assertIn("port_key", e)
            self.assertIn("detail_url", e)
            self.assertTrue(e["title"], f"Empty title: {e}")

    def test_codes_are_unique(self):
        codes = [e["code"] for e in self.data["excursions"] if e["code"]]
        self.assertEqual(len(codes), len(set(codes)), "Duplicate excursion codes found")

    def test_all_port_keys_are_mapped(self):
        unmapped = [e for e in self.data["excursions"] if e["port_key"] == "other"]
        self.assertEqual(unmapped, [], f"Unmapped ports: {[(e['port'], e['title']) for e in unmapped]}")

    def test_expected_ports_present(self):
        port_keys = {e["port_key"] for e in self.data["excursions"]}
        required = {"dubrovnik", "bar", "athens", "kusadasi", "rhodes", "santorini", "istanbul"}
        missing = required - port_keys
        self.assertEqual(missing, set(), f"Missing ports: {missing}")

    def test_detail_urls_are_ncl(self):
        for e in self.data["excursions"]:
            if e["detail_url"]:
                self.assertIn("ncl.com/shorex", e["detail_url"])

    def test_prices_are_numeric_or_none(self):
        for e in self.data["excursions"]:
            for k in ("adult_from", "child_from"):
                v = e[k]
                self.assertTrue(v is None or isinstance(v, (int, float)), f"{k}={v} for {e['title']}")

    def test_activity_level_range(self):
        for e in self.data["excursions"]:
            if e["activity_level"] is not None:
                self.assertIn(e["activity_level"], range(1, 6))

    def test_all_json_locations_in_sync(self):
        """data/, public/, and web/ copies must be identical after parse."""
        data_path = ROOT / "data" / "excursions.json"
        public_path = ROOT / "public" / "excursions.json"
        web_path = ROOT / "web" / "excursions.json"
        self.assertTrue(public_path.exists(), "parser should write public/excursions.json")
        self.assertTrue(web_path.exists(), "parser should write web/excursions.json")
        self.assertEqual(data_path.read_text(), public_path.read_text(),
                         "data/ and public/ excursions.json must be identical after parse")
        self.assertEqual(data_path.read_text(), web_path.read_text(),
                         "data/ and web/ excursions.json must be identical after parse")

    def test_minimum_counts_per_port(self):
        by_port = {}
        for e in self.data["excursions"]:
            by_port.setdefault(e["port_key"], 0)
            by_port[e["port_key"]] += 1
        for port in ("dubrovnik", "bar", "athens", "kusadasi", "rhodes", "santorini", "istanbul"):
            self.assertGreaterEqual(by_port.get(port, 0), 5, f"Too few excursions for {port}")


if __name__ == "__main__":
    unittest.main()
