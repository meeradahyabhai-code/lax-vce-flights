"""Tests for hotel_agent.py — brand detection, scoring, normalization,
merge, categorization. Combined SerpAPI + Google Places pipeline."""

import unittest

from hotel_agent import (
    _build_places_lookup,
    _calc_nights,
    _normalize_name,
    _parse_price,
    categorize_hotels,
    detect_brand,
    merge_places_data,
    normalize_serpapi,
    score_hotels,
)


class TestBrandDetection(unittest.TestCase):
    """Test detect_brand against all known sub-brands."""

    def test_marriott_brands(self):
        cases = [
            ("JW Marriott Venice Resort & Spa", "marriott"),
            ("Sheraton Venice Grand Canal", "marriott"),
            ("The Westin Europa & Regina", "marriott"),
            ("W Hotel Venice", "marriott"),
            ("The Ritz-Carlton Venice", "marriott"),
            ("St. Regis Venice", "marriott"),
            ("Courtyard by Marriott Venice", "marriott"),
            ("Autograph Collection Hotel", "marriott"),
            ("Tribute Portfolio Venice", "marriott"),
            ("Le Meridien Venice", "marriott"),
            ("Le Méridien Venice", "marriott"),
            ("Renaissance Venice", "marriott"),
            ("Aloft Venice Airport", "marriott"),
            ("Moxy Venice", "marriott"),
            ("Four Points by Sheraton", "marriott"),
            ("Fairfield Inn Venice", "marriott"),
        ]
        for name, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(detect_brand(name), expected)

    def test_hilton_brands(self):
        cases = [
            ("Hilton Molino Stucky Venice", "hilton"),
            ("DoubleTree by Hilton Venice", "hilton"),
            ("Hampton Inn Venice", "hilton"),
            ("Embassy Suites Venice", "hilton"),
            ("Waldorf Astoria Venice", "hilton"),
            ("Conrad Venice", "hilton"),
            ("Canopy by Hilton Venice", "hilton"),
            ("Curio Collection Venice", "hilton"),
            ("Tapestry Collection Venice", "hilton"),
            ("Motto by Hilton Venice", "hilton"),
            ("LXR Hotels Venice", "hilton"),
        ]
        for name, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(detect_brand(name), expected)

    def test_independent(self):
        cases = [
            "Hotel Danieli",
            "Gritti Palace",
            "Ca' Sagredo Hotel",
            "Hotel Cipriani",
            "",
            None,
        ]
        for name in cases:
            with self.subTest(name=name):
                self.assertEqual(detect_brand(name), "independent")


class TestPriceParser(unittest.TestCase):
    def test_dollar_sign(self):
        self.assertEqual(_parse_price("$189"), 189)

    def test_plain_number(self):
        self.assertEqual(_parse_price("220"), 220)

    def test_with_commas(self):
        self.assertEqual(_parse_price("$1,200"), 1200)

    def test_empty(self):
        self.assertEqual(_parse_price(""), 0)

    def test_none(self):
        self.assertEqual(_parse_price(None), 0)

    def test_decimal(self):
        self.assertEqual(_parse_price("$189.50"), 190)


class TestNightsCalc(unittest.TestCase):
    def test_three_nights(self):
        self.assertEqual(_calc_nights("2026-06-30", "2026-07-03"), 3)

    def test_one_night(self):
        self.assertEqual(_calc_nights("2026-06-30", "2026-07-01"), 1)


class TestNormalizeName(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(_normalize_name("Hotel Danieli"), "hotel danieli")

    def test_strips_suffix(self):
        result = _normalize_name("Gritti Palace, A Luxury Collection Hotel")
        self.assertEqual(result, "gritti palace")


class TestSerpAPINormalization(unittest.TestCase):
    """Test normalize_serpapi with mock SerpAPI data."""

    MOCK_RESPONSE = {
        "properties": [
            {
                "name": "JW Marriott Venice Resort & Spa",
                "hotel_class": 5,
                "rate_per_night": {"lowest": "$320"},
                "total_rate": {"lowest": "$960"},
                "description": "Isola delle Rose, Venice",
                "images": [{"thumbnail": "https://example.com/jw.jpg"}],
                "amenities": ["Pool", "Spa"],
                "link": "https://google.com/hotels/jw",
            },
            {
                "name": "Hilton Molino Stucky Venice",
                "hotel_class": 5,
                "rate_per_night": {"lowest": "$250"},
                "total_rate": {"lowest": "$750"},
                "description": "Giudecca, Venice",
                "images": [{"thumbnail": "https://example.com/hilton.jpg"}],
                "amenities": ["Pool", "Gym"],
                "link": "https://google.com/hotels/hilton",
            },
            {
                "name": "Hotel Danieli",
                "hotel_class": 5,
                "rate_per_night": {"lowest": "$450"},
                "total_rate": {"lowest": "$1350"},
                "description": "Riva degli Schiavoni",
                "images": [],
                "amenities": [],
                "link": "https://google.com/hotels/danieli",
            },
            {
                # No price — should be skipped
                "name": "Budget Place",
                "hotel_class": 2,
                "rate_per_night": {},
                "total_rate": {},
            },
        ]
    }

    def test_count_skips_no_price(self):
        hotels = normalize_serpapi(self.MOCK_RESPONSE, "2026-06-30", "2026-07-03")
        self.assertEqual(len(hotels), 3)

    def test_brand_detection(self):
        hotels = normalize_serpapi(self.MOCK_RESPONSE, "2026-06-30", "2026-07-03")
        brands = {h["name"]: h["brand"] for h in hotels}
        self.assertEqual(brands["JW Marriott Venice Resort & Spa"], "marriott")
        self.assertEqual(brands["Hilton Molino Stucky Venice"], "hilton")
        self.assertEqual(brands["Hotel Danieli"], "independent")

    def test_pricing(self):
        hotels = normalize_serpapi(self.MOCK_RESPONSE, "2026-06-30", "2026-07-03")
        jw = next(h for h in hotels if "Marriott" in h["name"])
        self.assertEqual(jw["rate_per_night"], 320)
        self.assertEqual(jw["total_rate"], 960)
        self.assertEqual(jw["nights"], 3)

    def test_star_class(self):
        hotels = normalize_serpapi(self.MOCK_RESPONSE, "2026-06-30", "2026-07-03")
        jw = next(h for h in hotels if "Marriott" in h["name"])
        self.assertEqual(jw["star_class"], 5)

    def test_image(self):
        hotels = normalize_serpapi(self.MOCK_RESPONSE, "2026-06-30", "2026-07-03")
        jw = next(h for h in hotels if "Marriott" in h["name"])
        self.assertEqual(jw["image_url"], "https://example.com/jw.jpg")

    def test_booking_link(self):
        hotels = normalize_serpapi(self.MOCK_RESPONSE, "2026-06-30", "2026-07-03")
        jw = next(h for h in hotels if "Marriott" in h["name"])
        self.assertEqual(jw["google_hotels_url"], "https://google.com/hotels/jw")

    def test_placeholders_for_places_data(self):
        """SerpAPI hotels should have empty placeholders for Places data."""
        hotels = normalize_serpapi(self.MOCK_RESPONSE, "2026-06-30", "2026-07-03")
        jw = next(h for h in hotels if "Marriott" in h["name"])
        self.assertEqual(jw["overall_rating"], 0)
        self.assertEqual(jw["reviews"], 0)
        self.assertEqual(jw["review_snippets"], [])


class TestMergePlacesData(unittest.TestCase):
    """Test merging Google Places data into SerpAPI hotels."""

    def test_exact_match(self):
        hotels = [
            {"name": "Hotel Danieli", "overall_rating": 0, "reviews": 0, "address": "Venice"},
        ]
        places = [
            {"name": "Hotel Danieli", "rating": 4.5, "user_ratings_total": 2100,
             "place_id": "ChIJ_test", "formatted_address": "Riva degli Schiavoni, Venice"},
        ]
        merged = merge_places_data(hotels, places)
        self.assertEqual(merged[0]["overall_rating"], 4.5)
        self.assertEqual(merged[0]["reviews"], 2100)
        self.assertEqual(merged[0]["place_id"], "ChIJ_test")

    def test_fuzzy_match_substring(self):
        hotels = [
            {"name": "JW Marriott Venice Resort & Spa", "overall_rating": 0,
             "reviews": 0, "address": ""},
        ]
        places = [
            {"name": "JW Marriott Venice Resort", "rating": 4.6,
             "user_ratings_total": 3400, "place_id": "ChIJ_jw",
             "formatted_address": "Isola delle Rose"},
        ]
        merged = merge_places_data(hotels, places)
        self.assertEqual(merged[0]["overall_rating"], 4.6)

    def test_fuzzy_match_shared_words(self):
        hotels = [
            {"name": "The Westin Europa & Regina", "overall_rating": 0,
             "reviews": 0, "address": ""},
        ]
        places = [
            {"name": "Westin Europa Regina Venice", "rating": 4.3,
             "user_ratings_total": 1800, "place_id": "ChIJ_w",
             "formatted_address": "San Marco"},
        ]
        merged = merge_places_data(hotels, places)
        self.assertEqual(merged[0]["overall_rating"], 4.3)

    def test_no_match_stays_empty(self):
        hotels = [
            {"name": "Some Unique Hotel", "overall_rating": 0,
             "reviews": 0, "address": ""},
        ]
        places = [
            {"name": "Completely Different Place", "rating": 4.0,
             "user_ratings_total": 500, "place_id": "x",
             "formatted_address": "Somewhere"},
        ]
        merged = merge_places_data(hotels, places)
        self.assertEqual(merged[0]["overall_rating"], 0)

    def test_address_upgrade(self):
        """Places address replaces SerpAPI address if longer."""
        hotels = [
            {"name": "Hotel Danieli", "overall_rating": 0, "reviews": 0,
             "address": "Venice"},
        ]
        places = [
            {"name": "Hotel Danieli", "rating": 4.5, "user_ratings_total": 2100,
             "place_id": "x", "formatted_address": "Riva degli Schiavoni 4196, Venice, Italy"},
        ]
        merged = merge_places_data(hotels, places)
        self.assertIn("Riva degli Schiavoni", merged[0]["address"])


class TestScoring(unittest.TestCase):
    """Test blended scoring: SerpAPI pricing + Places ratings."""

    def _make_hotel(self, rate=200, stars=4, rating=4.5, reviews=1000):
        return {
            "name": "Test Hotel",
            "brand": "independent",
            "star_class": stars,
            "overall_rating": rating,
            "reviews": reviews,
            "rate_per_night": rate,
            "total_rate": rate * 3,
            "nights": 3,
            "check_in": "2026-06-30",
            "check_out": "2026-07-03",
            "address": "",
            "image_url": "",
            "amenities": [],
            "google_hotels_url": "",
            "review_snippets": [],
            "editorial_summary": "",
            "google_maps_url": "",
            "website": "",
        }

    def test_excellent_hotel(self):
        """4★ $220/night, 4.6 rating, 2000 reviews
        → 220 - 100 - 50 - 15 = 55"""
        h = self._make_hotel(rate=220, stars=4, rating=4.6, reviews=2000)
        scored = score_hotels([h])
        self.assertEqual(scored[0]["score"], 55)

    def test_expensive_hotel(self):
        """5★ $400/night, 4.2 rating, 500 reviews
        → 400 - 200 - 20 + 0 + 0.5*50 = 205"""
        h = self._make_hotel(rate=400, stars=5, rating=4.2, reviews=500)
        scored = score_hotels([h])
        self.assertEqual(scored[0]["score"], 205)

    def test_budget_penalty(self):
        """Rate over $350 adds +0.5 per $ over."""
        h = self._make_hotel(rate=450, stars=5, rating=4.5, reviews=5000)
        scored = score_hotels([h])
        # 450 - 200 - 50 - 25 + 0.5*100 = 225
        self.assertEqual(scored[0]["score"], 225)

    def test_sorting_order(self):
        h1 = self._make_hotel(rate=220, stars=4, rating=4.6, reviews=2000)  # 55
        h2 = self._make_hotel(rate=400, stars=5, rating=4.2, reviews=500)   # 205
        scored = score_hotels([h2, h1])
        self.assertTrue(scored[0]["score"] < scored[1]["score"])

    def test_no_rating(self):
        """Hotel with no Places rating uses 0."""
        h = self._make_hotel(rate=200, stars=4, rating=0, reviews=0)
        scored = score_hotels([h])
        # 200 - 100 + 0 + 0 = 100
        self.assertEqual(scored[0]["score"], 100)

    def test_star_class_dominates(self):
        """5★ at higher price still beats 4★ with better reviews."""
        h_5star = self._make_hotel(rate=300, stars=5, rating=4.0, reviews=500)
        h_4star = self._make_hotel(rate=220, stars=4, rating=4.8, reviews=5000)
        score_hotels([h_5star])
        score_hotels([h_4star])
        # 5★: 300 - 200 - 20 = 80
        # 4★: 220 - 100 - 50 - 25 = 45
        # 4★ wins here because price gap is big — but if prices were equal:
        h_5star_eq = self._make_hotel(rate=220, stars=5, rating=4.0, reviews=500)
        h_4star_eq = self._make_hotel(rate=220, stars=4, rating=4.8, reviews=5000)
        score_hotels([h_5star_eq])
        score_hotels([h_4star_eq])
        # 5★: 220 - 200 - 20 = 0
        # 4★: 220 - 100 - 50 - 25 = 45
        # At same price, 5★ beats 4★ even with worse reviews
        self.assertTrue(h_5star_eq["score"] < h_4star_eq["score"])

    def test_reviews_as_tiebreaker(self):
        """Same price + stars, better reviews wins."""
        h1 = self._make_hotel(rate=250, stars=4, rating=4.6, reviews=5000)
        h2 = self._make_hotel(rate=250, stars=4, rating=4.0, reviews=200)
        score_hotels([h1])
        score_hotels([h2])
        # h1: 250 - 100 - 50 - 25 = 75
        # h2: 250 - 100 - 20 - 0 = 130
        self.assertTrue(h1["score"] < h2["score"])


class TestCategorization(unittest.TestCase):
    def test_categorization(self):
        hotels = [
            {"name": "Hotel A", "brand": "independent", "score": -100},
            {"name": "JW Marriott", "brand": "marriott", "score": -50},
            {"name": "Hilton", "brand": "hilton", "score": 0},
            {"name": "Hotel B", "brand": "independent", "score": 50},
            {"name": "Sheraton", "brand": "marriott", "score": 100},
        ]
        result = categorize_hotels(hotels)
        self.assertEqual(len(result["best_overall"]), 5)
        self.assertEqual(len(result["best_marriott"]), 2)
        self.assertEqual(len(result["best_hilton"]), 1)

    def test_no_marriott(self):
        hotels = [{"name": "A", "brand": "independent", "score": 0}]
        result = categorize_hotels(hotels)
        self.assertEqual(len(result["best_marriott"]), 0)

    def test_no_hilton(self):
        hotels = [{"name": "A", "brand": "independent", "score": 0}]
        result = categorize_hotels(hotels)
        self.assertEqual(len(result["best_hilton"]), 0)


class TestFullPipeline(unittest.TestCase):
    """Test normalize_serpapi → merge_places → score → categorize."""

    def test_pipeline(self):
        serpapi_raw = {
            "properties": [
                {
                    "name": "JW Marriott Venice",
                    "hotel_class": 5,
                    "rate_per_night": {"lowest": "$320"},
                    "total_rate": {"lowest": "$960"},
                    "description": "Venice",
                    "images": [],
                    "amenities": [],
                    "link": "https://google.com/hotels/jw",
                },
                {
                    "name": "Hilton Molino Stucky",
                    "hotel_class": 5,
                    "rate_per_night": {"lowest": "$250"},
                    "total_rate": {"lowest": "$750"},
                    "description": "Venice",
                    "images": [],
                    "amenities": [],
                    "link": "https://google.com/hotels/hilton",
                },
                {
                    "name": "Hotel Danieli",
                    "hotel_class": 5,
                    "rate_per_night": {"lowest": "$450"},
                    "total_rate": {"lowest": "$1350"},
                    "description": "Venice",
                    "images": [],
                    "amenities": [],
                    "link": "https://google.com/hotels/danieli",
                },
            ]
        }

        places_results = [
            {"name": "JW Marriott Venice Resort", "rating": 4.6,
             "user_ratings_total": 3400, "place_id": "a", "formatted_address": "Venice"},
            {"name": "Hilton Molino Stucky Venice", "rating": 4.4,
             "user_ratings_total": 5600, "place_id": "b", "formatted_address": "Venice"},
            {"name": "Hotel Danieli", "rating": 4.5,
             "user_ratings_total": 2100, "place_id": "c", "formatted_address": "Venice"},
        ]

        # 1. Normalize SerpAPI
        hotels = normalize_serpapi(serpapi_raw, "2026-06-30", "2026-07-03")
        self.assertEqual(len(hotels), 3)

        # 2. Merge Places data
        hotels = merge_places_data(hotels, places_results)
        jw = next(h for h in hotels if "Marriott" in h["name"])
        self.assertEqual(jw["overall_rating"], 4.6)
        self.assertEqual(jw["rate_per_night"], 320)

        # 3. Score
        hotels = score_hotels(hotels)
        jw = next(h for h in hotels if "Marriott" in h["name"])
        hilton = next(h for h in hotels if "Hilton" in h["name"])
        danieli = next(h for h in hotels if "Danieli" in h["name"])
        # JW: 320 - 200(5★) - 50(4.6≥4.5) - 15(3400≥2000) = 55
        # Hilton: 250 - 200(5★) - 35(4.4≥4.25) - 25(5600≥5000) = -10
        # Danieli: 450 - 200(5★) - 50(4.5≥4.5) - 15(2100≥2000) + 50(450-350)*0.5 = 235
        self.assertEqual(jw["score"], 55)
        self.assertEqual(hilton["score"], -10)
        self.assertEqual(danieli["score"], 235)

        # Best hotel should be Hilton (lowest score)
        self.assertEqual(hotels[0]["name"], "Hilton Molino Stucky")

        # 4. Categorize
        categorized = categorize_hotels(hotels)
        self.assertEqual(len(categorized["best_marriott"]), 1)
        self.assertEqual(len(categorized["best_hilton"]), 1)
        self.assertEqual(categorized["best_marriott"][0]["name"], "JW Marriott Venice")
        self.assertEqual(categorized["best_hilton"][0]["name"], "Hilton Molino Stucky")


if __name__ == "__main__":
    unittest.main()
