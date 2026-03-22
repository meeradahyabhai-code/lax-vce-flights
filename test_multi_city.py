"""Tests for multi-city hotel support — Venice, Ravenna, Istanbul.

Covers: landmarks, CC programs, star data files, star lookup per city,
apply_official_stars with city_key, loyalty URLs, images array,
CITY_MAP/CITY_DEFAULTS in api/hotels.py, and frontend city navigation.
"""

import json
import os
import re
import unittest

import hotel_agent
from hotel_agent import (
    LANDMARKS,
    CC_PROGRAMS,
    _extract_all_images,
    _match_cc_program,
    _normalize_star_name,
    apply_official_stars,
    compute_distances,
    load_star_lookup,
    loyalty_url,
    normalize_serpapi,
    tag_cc_programs,
)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


# ---------------------------------------------------------------------------
# Landmarks
# ---------------------------------------------------------------------------


class TestLandmarks(unittest.TestCase):
    """All three cities have landmarks with valid coordinates."""

    def test_venice_landmark(self):
        lm = LANDMARKS["venice"]
        self.assertEqual(lm["name"], "Piazza San Marco")
        self.assertAlmostEqual(lm["lat"], 45.4341, places=3)
        self.assertAlmostEqual(lm["lng"], 12.3388, places=3)

    def test_ravenna_landmark(self):
        lm = LANDMARKS["ravenna"]
        self.assertEqual(lm["name"], "Piazza del Popolo")
        self.assertAlmostEqual(lm["lat"], 44.4169, places=3)
        self.assertAlmostEqual(lm["lng"], 12.1990, places=3)

    def test_istanbul_landmark(self):
        lm = LANDMARKS["istanbul"]
        self.assertEqual(lm["name"], "Galata Tower")
        self.assertAlmostEqual(lm["lat"], 41.0256, places=3)
        self.assertAlmostEqual(lm["lng"], 28.9741, places=3)


# ---------------------------------------------------------------------------
# CC Programs
# ---------------------------------------------------------------------------


class TestCCPrograms(unittest.TestCase):
    """CC_PROGRAMS has entries for all cities."""

    def test_venice_has_fhr_and_thc(self):
        self.assertIn("fhr", CC_PROGRAMS["venice"])
        self.assertIn("thc", CC_PROGRAMS["venice"])
        self.assertGreater(len(CC_PROGRAMS["venice"]["fhr"]), 0)

    def test_ravenna_empty(self):
        self.assertEqual(CC_PROGRAMS["ravenna"], {})

    def test_istanbul_has_fhr(self):
        self.assertIn("fhr", CC_PROGRAMS["istanbul"])
        self.assertGreater(len(CC_PROGRAMS["istanbul"]["fhr"]), 5)

    def test_istanbul_fhr_known_hotels(self):
        fhr = CC_PROGRAMS["istanbul"]["fhr"]
        expected = [
            "the ritz-carlton, istanbul",
            "the st. regis istanbul",
            "four seasons hotel istanbul at sultanahmet",
            "raffles istanbul",
            "the peninsula istanbul",
        ]
        for name in expected:
            with self.subTest(name=name):
                self.assertIn(name, fhr)

    def test_match_cc_program_istanbul_fhr(self):
        matched = _match_cc_program("The Ritz-Carlton, Istanbul", "istanbul")
        self.assertIn("fhr", matched)

    def test_match_cc_program_ravenna_returns_empty(self):
        matched = _match_cc_program("Grand Hotel Mattei", "ravenna")
        self.assertEqual(matched, [])

    def test_match_cc_program_unknown_city_returns_empty(self):
        matched = _match_cc_program("Some Hotel", "paris")
        self.assertEqual(matched, [])


# ---------------------------------------------------------------------------
# Star Data Files
# ---------------------------------------------------------------------------


class TestStarDataFiles(unittest.TestCase):
    """Star data files exist for Venice, Ravenna, Istanbul."""

    def _load(self, city):
        path = os.path.join(DATA_DIR, f"hotel_stars_{city}.json")
        self.assertTrue(os.path.isfile(path), f"Missing {path}")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_venice_star_file(self):
        data = self._load("venice")
        self.assertGreater(len(data["hotels"]), 400)

    def test_ravenna_star_file(self):
        data = self._load("ravenna")
        self.assertGreater(len(data["hotels"]), 90)
        # Check structure
        for h in data["hotels"]:
            self.assertIn("name", h)
            self.assertIn("stars", h)
            self.assertIn(h["stars"], (1, 2, 3, 4, 5))

    def test_istanbul_star_file(self):
        data = self._load("istanbul")
        self.assertGreater(len(data["hotels"]), 700)
        for h in data["hotels"][:50]:
            self.assertIn("name", h)
            self.assertIn("stars", h)
            self.assertIn(h["stars"], (1, 2, 3, 4, 5))

    def test_ravenna_known_hotels(self):
        data = self._load("ravenna")
        names = {h["name"] for h in data["hotels"]}
        self.assertIn("Hotel Bisanzio", names)
        self.assertIn("NH Ravenna", names)

    def test_istanbul_known_hotels(self):
        data = self._load("istanbul")
        names = {h["name"] for h in data["hotels"]}
        # These are in Turkish ministry data
        self.assertIn("HILTON OTELİ", names)
        self.assertIn("DİVAN İSTANBUL", names)


# ---------------------------------------------------------------------------
# Star Lookup — per-city loading
# ---------------------------------------------------------------------------


class TestStarLookupMultiCity(unittest.TestCase):
    """load_star_lookup(city_key) loads per-city data."""

    def setUp(self):
        hotel_agent._star_lookups = {}

    def tearDown(self):
        hotel_agent._star_lookups = {}

    def test_venice_lookup(self):
        lookup = load_star_lookup("venice")
        self.assertGreater(len(lookup), 400)

    def test_ravenna_lookup(self):
        lookup = load_star_lookup("ravenna")
        self.assertGreater(len(lookup), 50)

    def test_istanbul_lookup(self):
        lookup = load_star_lookup("istanbul")
        self.assertGreater(len(lookup), 500)

    def test_unknown_city_returns_empty(self):
        lookup = load_star_lookup("paris")
        self.assertEqual(lookup, {})

    def test_caching_per_city(self):
        """Each city is cached independently."""
        v = load_star_lookup("venice")
        r = load_star_lookup("ravenna")
        self.assertIn("venice", hotel_agent._star_lookups)
        self.assertIn("ravenna", hotel_agent._star_lookups)
        self.assertNotEqual(len(v), len(r))


# ---------------------------------------------------------------------------
# apply_official_stars with city_key
# ---------------------------------------------------------------------------


class TestApplyOfficialStarsMultiCity(unittest.TestCase):

    def setUp(self):
        hotel_agent._star_lookups = {}

    def tearDown(self):
        hotel_agent._star_lookups = {}

    def _make_hotel(self, name, star_class=0):
        return {"name": name, "star_class": star_class, "brand": "independent"}

    def test_venice_still_works(self):
        h = self._make_hotel("THE GRITTI PALACE")
        result = apply_official_stars([h], "venice")
        self.assertEqual(result[0]["star_class"], 5)

    def test_ravenna_match(self):
        h = self._make_hotel("Hotel Bisanzio")
        result = apply_official_stars([h], "ravenna")
        self.assertGreater(result[0]["star_class"], 0)

    def test_istanbul_match(self):
        h = self._make_hotel("HILTON OTELİ")
        result = apply_official_stars([h], "istanbul")
        self.assertEqual(result[0]["star_class"], 5)

    def test_unknown_city_no_crash(self):
        """apply_official_stars with unknown city returns hotels unchanged."""
        h = self._make_hotel("Some Hotel")
        result = apply_official_stars([h], "paris")
        self.assertEqual(result[0]["star_class"], 0)

    def test_default_city_is_venice(self):
        h = self._make_hotel("THE GRITTI PALACE")
        result = apply_official_stars([h])
        self.assertEqual(result[0]["star_class"], 5)


# ---------------------------------------------------------------------------
# Compute distances — per-city landmark
# ---------------------------------------------------------------------------


class TestComputeDistancesMultiCity(unittest.TestCase):

    def _make_hotel(self, lat, lng):
        return {"name": "Test", "latitude": lat, "longitude": lng}

    def test_venice_distance(self):
        h = self._make_hotel(45.44, 12.34)
        result = compute_distances([h], "venice")
        self.assertEqual(result[0]["landmark_name"], "Piazza San Marco")
        self.assertIsNotNone(result[0]["distance_km"])

    def test_ravenna_distance(self):
        h = self._make_hotel(44.42, 12.20)
        result = compute_distances([h], "ravenna")
        self.assertEqual(result[0]["landmark_name"], "Piazza del Popolo")
        self.assertIsNotNone(result[0]["distance_km"])

    def test_istanbul_distance(self):
        h = self._make_hotel(41.03, 28.97)
        result = compute_distances([h], "istanbul")
        self.assertEqual(result[0]["landmark_name"], "Galata Tower")
        self.assertIsNotNone(result[0]["distance_km"])

    def test_unknown_city_no_crash(self):
        h = self._make_hotel(48.85, 2.35)
        result = compute_distances([h], "paris")
        self.assertIsNone(result[0].get("distance_km"))


# ---------------------------------------------------------------------------
# Loyalty URLs — city-aware
# ---------------------------------------------------------------------------


class TestLoyaltyURLMultiCity(unittest.TestCase):

    def test_marriott_venice(self):
        h = {"brand": "marriott", "check_in": "2026-06-30",
             "check_out": "2026-07-03", "city": "Venice, Italy"}
        url = loyalty_url(h)
        self.assertIn("Venice", url)
        self.assertIn("marriott.com", url)

    def test_marriott_istanbul(self):
        h = {"brand": "marriott", "check_in": "2026-07-13",
             "check_out": "2026-07-14", "city": "Istanbul, Turkey"}
        url = loyalty_url(h)
        self.assertIn("Istanbul", url)
        self.assertIn("marriott.com", url)
        self.assertNotIn("Venice", url)

    def test_hilton_ravenna(self):
        h = {"brand": "hilton", "check_in": "2026-07-02",
             "check_out": "2026-07-03", "city": "Ravenna, Italy"}
        url = loyalty_url(h)
        self.assertIn("Ravenna", url)
        self.assertIn("hilton.com", url)

    def test_independent_returns_empty(self):
        h = {"brand": "independent", "check_in": "2026-07-02",
             "check_out": "2026-07-03", "city": "Ravenna, Italy"}
        self.assertEqual(loyalty_url(h), "")

    def test_default_city_fallback(self):
        """If city key missing, defaults to Venice."""
        h = {"brand": "marriott", "check_in": "2026-06-30",
             "check_out": "2026-07-03"}
        url = loyalty_url(h)
        self.assertIn("Venice", url)


# ---------------------------------------------------------------------------
# Images array extraction
# ---------------------------------------------------------------------------


class TestExtractAllImages(unittest.TestCase):

    def test_dict_images(self):
        prop = {"images": [
            {"thumbnail": "thumb1.jpg", "original_image": "orig1.jpg"},
            {"thumbnail": "thumb2.jpg"},
        ]}
        result = _extract_all_images(prop)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["thumbnail"], "thumb1.jpg")
        self.assertEqual(result[0]["original"], "orig1.jpg")
        self.assertNotIn("original", result[1])

    def test_string_images(self):
        prop = {"images": ["url1.jpg", "url2.jpg"]}
        result = _extract_all_images(prop)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["thumbnail"], "url1.jpg")

    def test_empty_images(self):
        self.assertEqual(_extract_all_images({}), [])
        self.assertEqual(_extract_all_images({"images": []}), [])
        self.assertEqual(_extract_all_images({"images": None}), [])

    def test_normalize_serpapi_includes_images(self):
        """normalize_serpapi includes images array in output."""
        raw = {"properties": [{
            "name": "Test Hotel",
            "rate_per_night": {"lowest": "$200"},
            "images": [
                {"thumbnail": "t1.jpg", "original_image": "o1.jpg"},
                {"thumbnail": "t2.jpg"},
            ],
        }]}
        hotels = normalize_serpapi(raw, "2026-07-01", "2026-07-02")
        self.assertEqual(len(hotels), 1)
        self.assertEqual(len(hotels[0]["images"]), 2)
        self.assertEqual(hotels[0]["image_url"], "t1.jpg")


# ---------------------------------------------------------------------------
# API: CITY_MAP and CITY_DEFAULTS
# ---------------------------------------------------------------------------


class TestHotelsAPIMultiCity(unittest.TestCase):
    """Verify api/hotels.py has correct multi-city configuration."""

    @classmethod
    def setUpClass(cls):
        api_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "api", "hotels.py"
        )
        with open(api_path, "r", encoding="utf-8") as f:
            cls.source = f.read()

    def test_city_map_has_all_cities(self):
        for city in ("venice", "ravenna", "istanbul"):
            with self.subTest(city=city):
                self.assertIn(f'"{city}"', self.source)

    def test_city_defaults_exist(self):
        self.assertIn("CITY_DEFAULTS", self.source)

    def test_city_defaults_ravenna_dates(self):
        self.assertIn("2026-07-02", self.source)
        self.assertIn("2026-07-03", self.source)

    def test_city_defaults_istanbul_dates(self):
        self.assertIn("2026-07-13", self.source)
        self.assertIn("2026-07-14", self.source)

    def test_apply_official_stars_has_city_key(self):
        """apply_official_stars is called with city_key parameter."""
        self.assertRegex(self.source, r"apply_official_stars\(hotels,\s*city_key\)")

    def test_hotels_tagged_with_city(self):
        """Hotels get city field for loyalty URLs."""
        self.assertIn('h["city"]', self.source)


# ---------------------------------------------------------------------------
# Frontend — city navigation
# ---------------------------------------------------------------------------


class TestFrontendMultiCity(unittest.TestCase):
    """Verify web/index.html has multi-city support."""

    @classmethod
    def setUpClass(cls):
        html_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "web", "index.html"
        )
        with open(html_path, "r", encoding="utf-8") as f:
            cls.html = f.read()

    def test_city_pills_defined(self):
        """City pill array includes all three cities."""
        self.assertIn("'venice', 'ravenna', 'istanbul'", self.html)

    def test_no_disabled_pills(self):
        self.assertNotIn('disabled', self.html.split('hotel-city-pill')[1].split('</div>')[0]
                         if 'hotel-city-pill' in self.html else '')

    def test_city_defaults_in_js(self):
        self.assertIn("CITY_DEFAULTS", self.html)
        self.assertIn("2026-07-02", self.html)  # Ravenna check-in
        self.assertIn("2026-07-13", self.html)  # Istanbul check-in

    def test_city_landmarks_in_js(self):
        self.assertIn("CITY_LANDMARKS", self.html)
        self.assertIn("Piazza del Popolo", self.html)
        self.assertIn("Galata Tower", self.html)

    def test_landmark_icon_renamed(self):
        self.assertIn("landmarkIcon", self.html)
        self.assertNotIn("sanMarcoIcon", self.html)

    def test_hotel_city_name_function(self):
        self.assertIn("hotelCityName()", self.html)
        self.assertIn("CITY_DISPLAY_NAMES", self.html)

    def test_hero_uses_city_name(self):
        self.assertIn("hotelCityName() + ' Hotels'", self.html)

    def test_city_placeholder_images(self):
        self.assertIn("CITY_PLACEHOLDER_IMGS", self.html)

    def test_fallback_json_uses_city(self):
        self.assertIn("hotels_' + activeHotelCity + '.json", self.html)

    def test_venice_keeps_date_picker(self):
        self.assertIn("hotel-ci", self.html)
        self.assertIn("hotel-co", self.html)
        self.assertIn("Search Hotels", self.html)

    def test_public_index_matches_web(self):
        public_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "public", "index.html"
        )
        with open(public_path, "r", encoding="utf-8") as f:
            public_html = f.read()
        self.assertEqual(self.html, public_html,
                         "public/index.html must match web/index.html")


if __name__ == "__main__":
    unittest.main()
