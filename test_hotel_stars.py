"""Tests for hotel star rating integration — official Veneto open data.

Covers: data file integrity, star lookup loading, name normalization,
exact/fuzzy/reorder matching in apply_official_stars, and API integration.
"""

import json
import os
import unittest

import hotel_agent
from hotel_agent import (
    _normalize_star_name,
    apply_official_stars,
    load_star_lookup,
)

DATA_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "hotel_stars_venice.json"
)


def _load_raw_data() -> dict:
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Star Data Tests — data/hotel_stars_venice.json integrity
# ---------------------------------------------------------------------------


class TestStarDataFile(unittest.TestCase):
    """Tests 1-7: validate the static JSON data file."""

    @classmethod
    def setUpClass(cls):
        cls.data = _load_raw_data()

    def test_star_data_file_exists(self):
        """data/hotel_stars_venice.json exists and is valid JSON."""
        self.assertTrue(os.path.isfile(DATA_PATH))
        # If we got here, setUpClass already parsed it successfully
        self.assertIsInstance(self.data, dict)

    def test_star_data_has_source_metadata(self):
        """Has source, license, url, fetched fields."""
        for field in ("source", "license", "url", "fetched"):
            with self.subTest(field=field):
                self.assertIn(field, self.data)
                self.assertTrue(self.data[field], f"{field} should be non-empty")

    def test_star_data_has_hotels(self):
        """Hotels array is non-empty (should be ~457)."""
        hotels = self.data.get("hotels", [])
        self.assertGreater(len(hotels), 400, f"Expected ~457 hotels, got {len(hotels)}")

    def test_star_data_hotel_structure(self):
        """Each hotel has name, stars, classification, address."""
        required_keys = {"name", "stars", "classification", "address"}
        for i, h in enumerate(self.data["hotels"]):
            with self.subTest(i=i, name=h.get("name", "?")):
                for key in required_keys:
                    self.assertIn(key, h, f"Hotel #{i} missing '{key}'")

    def test_star_data_stars_range(self):
        """All stars values are 1-5."""
        for h in self.data["hotels"]:
            with self.subTest(name=h["name"]):
                self.assertIn(h["stars"], (1, 2, 3, 4, 5))

    def test_star_data_classification_matches_stars(self):
        """Classification text matches stars number."""
        for h in self.data["hotels"]:
            classification = h["classification"].lower()
            stars = h["stars"]
            with self.subTest(name=h["name"], classification=h["classification"]):
                self.assertIn(str(stars), classification,
                              f"Classification '{h['classification']}' should contain '{stars}'")

    def test_star_data_known_hotels(self):
        """Verify specific known hotels and their star ratings."""
        hotels_by_name = {h["name"]: h["stars"] for h in self.data["hotels"]}
        known = {
            "J. W. MARRIOTT VENICE RESORT & SPA": 5,
            "MOLINO STUCKY HILTON VENICE": 5,
            "THE GRITTI PALACE": 5,
            "THE ST. REGIS VENICE": 5,
            "HILTON GARDEN INN VENICE MESTRE SAN GIULIANO": 4,
        }
        for name, expected_stars in known.items():
            with self.subTest(name=name):
                self.assertIn(name, hotels_by_name,
                              f"'{name}' not found in data file")
                self.assertEqual(hotels_by_name[name], expected_stars)


# ---------------------------------------------------------------------------
# Star Lookup Tests — loading and normalization
# ---------------------------------------------------------------------------


class TestStarLookup(unittest.TestCase):
    """Tests 8-9: load_star_lookup and _normalize_star_name."""

    def setUp(self):
        # Reset cached lookup so each test gets a fresh load
        hotel_agent._star_lookups = {}

    def tearDown(self):
        hotel_agent._star_lookups = {}

    def test_load_star_lookup_returns_dict(self):
        """load_star_lookup() returns a dict with 400+ entries."""
        lookup = load_star_lookup()
        self.assertIsInstance(lookup, dict)
        self.assertGreater(len(lookup), 400,
                           f"Expected 400+ entries, got {len(lookup)}")

    def test_normalize_star_name(self):
        """Strips punctuation, collapses whitespace, lowercases."""
        cases = [
            ("J. W. MARRIOTT VENICE RESORT & SPA", "j w marriott venice resort spa"),
            ("THE ST. REGIS VENICE", "the st regis venice"),
            ("  Hotel   Danieli  ", "hotel danieli"),
            ("Ca' Sagredo", "ca sagredo"),
            ("", ""),
            (None, ""),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(_normalize_star_name(raw), expected)


# ---------------------------------------------------------------------------
# apply_official_stars Tests — matching logic
# ---------------------------------------------------------------------------


class TestApplyOfficialStars(unittest.TestCase):
    """Tests 10-14: exact, fuzzy, reorder matching + skip/no-false-positive."""

    def setUp(self):
        hotel_agent._star_lookups = {}

    def tearDown(self):
        hotel_agent._star_lookups = {}

    def _make_hotel(self, name, star_class=0):
        return {
            "name": name,
            "star_class": star_class,
            "brand": "independent",
        }

    def test_apply_official_stars_exact_match(self):
        """Hotel with name matching a lookup entry gets stars filled in."""
        # "THE GRITTI PALACE" is in the data as 5 stars
        h = self._make_hotel("THE GRITTI PALACE")
        result = apply_official_stars([h])
        self.assertEqual(result[0]["star_class"], 5)

    def test_apply_official_stars_fuzzy_match(self):
        """'JW Marriott Venice Resort & Spa' matches 'J. W. MARRIOTT VENICE RESORT & SPA'."""
        h = self._make_hotel("JW Marriott Venice Resort & Spa")
        result = apply_official_stars([h])
        self.assertEqual(result[0]["star_class"], 5)

    def test_apply_official_stars_hilton_reorder(self):
        """'Hilton Molino Stucky Venice' matches 'MOLINO STUCKY HILTON VENICE'."""
        h = self._make_hotel("Hilton Molino Stucky Venice")
        result = apply_official_stars([h])
        self.assertEqual(result[0]["star_class"], 5)

    def test_apply_official_stars_skips_nonzero(self):
        """Hotels with existing star_class != 0 are not overwritten."""
        h = self._make_hotel("THE GRITTI PALACE", star_class=3)
        result = apply_official_stars([h])
        self.assertEqual(result[0]["star_class"], 3,
                         "star_class should remain 3, not be overwritten to 5")

    def test_apply_official_stars_no_false_positive(self):
        """A completely unrelated hotel name stays at 0 stars."""
        h = self._make_hotel("Fake Hotel XYZ")
        result = apply_official_stars([h])
        self.assertEqual(result[0]["star_class"], 0)


# ---------------------------------------------------------------------------
# API Integration Test — verify api/hotels.py calls apply_official_stars
# ---------------------------------------------------------------------------


class TestHotelsAPIIntegration(unittest.TestCase):
    """Test 15: verify api/hotels.py imports and calls apply_official_stars."""

    def test_hotels_api_calls_apply_official_stars(self):
        """Read api/hotels.py source and verify it imports and calls apply_official_stars."""
        api_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "api", "hotels.py"
        )
        with open(api_path, "r", encoding="utf-8") as f:
            source = f.read()

        # Check import
        self.assertIn("apply_official_stars", source,
                       "api/hotels.py should import apply_official_stars")
        self.assertIn("from hotel_agent import", source)

        # Check it's actually called (not just imported)
        # Look for the call pattern: apply_official_stars(hotels) or similar
        import re
        call_pattern = re.compile(r"apply_official_stars\s*\(")
        self.assertTrue(call_pattern.search(source),
                        "api/hotels.py should call apply_official_stars()")


if __name__ == "__main__":
    unittest.main()
