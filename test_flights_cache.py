"""Tests for dataset-first flight architecture — ensures api/flights.py ONLY reads
from data/flights_cache.json and never calls SerpAPI directly."""

import json
import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(PROJECT_ROOT, "data", "flights_cache.json")
API_FLIGHTS = os.path.join(PROJECT_ROOT, "api", "flights.py")
REFRESH_SCRIPT = os.path.join(PROJECT_ROOT, "scripts", "refresh_flights.py")

REQUIRED_ORIGINS = ["LAX", "AKL", "ATL", "YVR"]
REQUIRED_FLIGHT_FIELDS = [
    "primary_airline",
    "price",
    "departure_time",
    "search_date",
    "stops",
]


class TestFlightsCacheFile(unittest.TestCase):
    """Verify data/flights_cache.json exists, is valid, and has expected structure."""

    @classmethod
    def setUpClass(cls):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cls.cache = json.load(f)

    def test_flights_cache_file_exists(self):
        """data/flights_cache.json exists and is valid JSON."""
        self.assertTrue(os.path.exists(CACHE_FILE), "flights_cache.json not found")
        # If setUpClass succeeded, the file is valid JSON
        self.assertIsInstance(self.cache, dict)

    def test_flights_cache_has_all_origins(self):
        """Cache contains LAX, AKL, ATL, YVR keys."""
        for origin in REQUIRED_ORIGINS:
            self.assertIn(origin, self.cache, f"Missing origin {origin} in cache")

    def test_flights_cache_has_both_directions(self):
        """Each origin has outbound and return."""
        for origin in REQUIRED_ORIGINS:
            origin_data = self.cache[origin]
            self.assertIn("outbound", origin_data, f"{origin} missing outbound")
            self.assertIn("return", origin_data, f"{origin} missing return")

    def test_flights_cache_has_flights(self):
        """Each direction has a non-empty flights array."""
        for origin in REQUIRED_ORIGINS:
            for direction in ("outbound", "return"):
                flights = self.cache[origin][direction].get("flights", [])
                self.assertIsInstance(flights, list)
                self.assertGreater(
                    len(flights),
                    0,
                    f"{origin}/{direction} has no flights",
                )

    def test_flights_cache_flight_structure(self):
        """Each flight has required fields: primary_airline, price, departure_time, search_date, stops."""
        for origin in REQUIRED_ORIGINS:
            for direction in ("outbound", "return"):
                flights = self.cache[origin][direction]["flights"]
                for i, flight in enumerate(flights):
                    for field in REQUIRED_FLIGHT_FIELDS:
                        self.assertIn(
                            field,
                            flight,
                            f"{origin}/{direction} flight[{i}] missing '{field}'",
                        )

    def test_flights_cache_generated_date(self):
        """Generated date is within 7 days (not stale)."""
        today = date.today()
        for origin in REQUIRED_ORIGINS:
            for direction in ("outbound", "return"):
                generated = self.cache[origin][direction].get("generated")
                self.assertIsNotNone(
                    generated, f"{origin}/{direction} missing 'generated'"
                )
                gen_date = date.fromisoformat(generated)
                age = (today - gen_date).days
                self.assertLessEqual(
                    age,
                    7,
                    f"{origin}/{direction} cache is {age} days old (max 7)",
                )


class TestApiFlightsNoSerpapi(unittest.TestCase):
    """Verify api/flights.py cannot call SerpAPI — it only reads the cache file."""

    @classmethod
    def setUpClass(cls):
        with open(API_FLIGHTS, "r", encoding="utf-8") as f:
            cls.source = f.read()

    def test_api_flights_no_serpapi_import(self):
        """api/flights.py does NOT import search_serpapi, does NOT import from
        flight_agent (except maybe _today_pst), and does NOT contain 'serpapi'
        in executable code (comments/docstrings about the architecture are OK)."""
        self.assertNotIn("search_serpapi", self.source)
        self.assertNotIn("from flight_agent", self.source)
        self.assertNotIn("import flight_agent", self.source)
        # Strip comments and docstrings, then check no serpapi reference remains
        # in executable code lines
        executable_lines = []
        in_docstring = False
        for line in self.source.splitlines():
            stripped = line.strip()
            if stripped.startswith('"""') or stripped.startswith("'''"):
                # Toggle docstring state; if opening and closing on same line, skip it
                if stripped.count('"""') == 2 or stripped.count("'''") == 2:
                    continue
                in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            if stripped.startswith("#"):
                continue
            executable_lines.append(stripped)
        executable_source = "\n".join(executable_lines).lower()
        self.assertNotIn(
            "serpapi",
            executable_source,
            "api/flights.py has 'serpapi' in executable code (not just comments)",
        )

    def test_api_flights_reads_cache_file(self):
        """api/flights.py source references flights_cache.json."""
        self.assertIn("flights_cache.json", self.source)


class TestRefreshScript(unittest.TestCase):
    """Verify refresh script exists and warns about SerpAPI cost."""

    def test_refresh_script_exists(self):
        """scripts/refresh_flights.py exists."""
        self.assertTrue(
            os.path.exists(REFRESH_SCRIPT),
            "scripts/refresh_flights.py not found",
        )

    def test_refresh_script_has_cost_warning(self):
        """scripts/refresh_flights.py contains '42' in a warning about SerpAPI calls."""
        with open(REFRESH_SCRIPT, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn("42", source)
        # Verify it's in a warning context (either docstring or print)
        self.assertTrue(
            "42 SerpAPI calls" in source or "~42 SerpAPI" in source,
            "Refresh script should warn about 42 SerpAPI calls",
        )


if __name__ == "__main__":
    unittest.main()
