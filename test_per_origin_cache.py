"""Tests for per-origin flight cache read/write/TTL and api/flights.py fallback.

We test the pure cache helpers without hitting SerpAPI. The _fresh_search path
is covered indirectly — these tests focus on the caching contract that prevents
cold-start spikes from burning budget.
"""

import importlib
import json
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "api"))


def _reload(mod_name: str):
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def _make_origin_payload(origin: str, age_seconds: float = 0.0) -> dict:
    return {
        "origin": origin,
        "outbound": {
            "generated": "2026-06-28",
            "days_to_go": 70,
            "trip_date": "2026-07-03",
            "flights": [
                {
                    "primary_airline": "Delta",
                    "price": 800,
                    "departure_time": "2026-06-29T10:00",
                    "search_date": "2026-06-29",
                    "stops": 1,
                }
            ] * 5,
        },
        "return": {
            "generated": "2026-06-28",
            "days_to_go": 70,
            "trip_date": "2026-07-03",
            "flights": [
                {
                    "primary_airline": "United",
                    "price": 900,
                    "departure_time": "2026-07-14T10:00",
                    "search_date": "2026-07-14",
                    "stops": 1,
                }
            ] * 5,
        },
        "_cached_at": time.time() - age_seconds,
        "_refreshed_at": "2026-04-19T16:00:00-07:00",
        "_serpapi_calls": 10,
    }


class TestRefreshFlightsCacheHelpers(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="refresh_test_")
        # Patch CACHE_DIR before importing so the module picks up the test dir
        os.environ["VERCEL"] = ""  # ensure non-Vercel path
        self.mod = _reload("refresh_flights")
        self._orig_cache_dir = self.mod.CACHE_DIR
        self._orig_fallback_dir = self.mod.FALLBACK_DIR
        self.mod.CACHE_DIR = self.tmpdir
        self.mod.FALLBACK_DIR = self.tmpdir

    def tearDown(self):
        self.mod.CACHE_DIR = self._orig_cache_dir
        self.mod.FALLBACK_DIR = self._orig_fallback_dir
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_write_then_read_returns_same_payload(self):
        payload = _make_origin_payload("LAX", age_seconds=0)
        self.mod._write_cache("LAX", payload)
        got = self.mod._read_cache("LAX")
        self.assertIsNotNone(got)
        self.assertEqual(got["origin"], "LAX")
        self.assertEqual(len(got["outbound"]["flights"]), 5)

    def test_read_cache_returns_none_when_missing(self):
        self.assertIsNone(self.mod._read_cache("LAX"))

    def test_read_cache_returns_none_when_stale(self):
        stale_payload = _make_origin_payload("LAX", age_seconds=self.mod.CACHE_TTL + 60)
        self.mod._write_cache("LAX", stale_payload)
        self.assertIsNone(self.mod._read_cache("LAX"))

    def test_read_stale_returns_expired_cache(self):
        stale_payload = _make_origin_payload("LAX", age_seconds=self.mod.CACHE_TTL + 60)
        self.mod._write_cache("LAX", stale_payload)
        got = self.mod._read_stale("LAX")
        self.assertIsNotNone(got)
        self.assertEqual(got["origin"], "LAX")

    def test_read_cache_survives_corrupt_file(self):
        path = self.mod._cache_path("LAX")
        with open(path, "w") as f:
            f.write("{not valid json")
        self.assertIsNone(self.mod._read_cache("LAX"))

    def test_cache_path_includes_origin(self):
        path = self.mod._cache_path("AKL")
        self.assertIn("flights_cache_AKL.json", path)

    def test_valid_origins_match_routes(self):
        self.assertEqual(set(self.mod.VALID_ORIGINS.keys()), {"LAX", "AKL", "ATL", "YVR"})


class TestApiFlightsMerge(unittest.TestCase):
    """api/flights.py merges per-origin caches + legacy fallback without SerpAPI."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="flights_merge_")
        self.fallback_dir = os.path.join(self.tmpdir, "data")
        os.makedirs(self.fallback_dir)
        self.tmp_sub = os.path.join(self.tmpdir, "tmp")
        os.makedirs(self.tmp_sub)
        self.mod = _reload("flights")
        self._orig_data = self.mod.DATA_DIR
        self._orig_tmp = self.mod.TMP_DIR
        self._orig_legacy = self.mod.LEGACY_CACHE
        self.mod.DATA_DIR = self.fallback_dir
        self.mod.TMP_DIR = self.tmp_sub
        self.mod.LEGACY_CACHE = os.path.join(self.fallback_dir, "flights_cache.json")

    def tearDown(self):
        self.mod.DATA_DIR = self._orig_data
        self.mod.TMP_DIR = self._orig_tmp
        self.mod.LEGACY_CACHE = self._orig_legacy
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_per_origin(self, base_dir: str, origin: str, payload: dict) -> None:
        path = os.path.join(base_dir, f"flights_cache_{origin}.json")
        with open(path, "w") as f:
            json.dump(payload, f)

    def _write_legacy(self, payload: dict) -> None:
        with open(self.mod.LEGACY_CACHE, "w") as f:
            json.dump(payload, f)

    def test_per_origin_takes_precedence_over_legacy(self):
        # Legacy has all four origins
        legacy = {o: {"outbound": {"flights": [{"primary_airline": "LEGACY"}]},
                     "return": {"flights": []}} for o in ("LAX", "AKL", "ATL", "YVR")}
        self._write_legacy(legacy)
        # LAX has a per-origin entry
        new_lax = _make_origin_payload("LAX")
        self._write_per_origin(self.fallback_dir, "LAX", new_lax)

        merged = self.mod._load_per_origin_cache()
        self.assertIn("LAX", merged)
        self.assertEqual(merged["LAX"]["outbound"]["flights"][0]["primary_airline"], "Delta")

    def test_legacy_fills_gaps_when_per_origin_missing(self):
        legacy = {"LAX": {"outbound": {"flights": [{"primary_airline": "LEGACY"}]},
                          "return": {"flights": []}}}
        self._write_legacy(legacy)
        per = self.mod._load_per_origin_cache()
        self.assertEqual(per, {})  # no per-origin files
        legacy_loaded = self.mod._load_legacy_cache()
        self.assertIn("LAX", legacy_loaded)

    def test_tmp_preferred_over_data_dir(self):
        tmp_payload = _make_origin_payload("LAX")
        tmp_payload["outbound"]["flights"][0]["primary_airline"] = "TMP"
        data_payload = _make_origin_payload("LAX")
        data_payload["outbound"]["flights"][0]["primary_airline"] = "DATA"
        self._write_per_origin(self.tmp_sub, "LAX", tmp_payload)
        self._write_per_origin(self.fallback_dir, "LAX", data_payload)
        merged = self.mod._load_per_origin_cache()
        self.assertEqual(merged["LAX"]["outbound"]["flights"][0]["primary_airline"], "TMP")

    def test_load_per_origin_cache_preserves_timestamps(self):
        payload = _make_origin_payload("LAX")
        self._write_per_origin(self.fallback_dir, "LAX", payload)
        merged = self.mod._load_per_origin_cache()
        self.assertIn("_cached_at", merged["LAX"])
        self.assertIn("_refreshed_at", merged["LAX"])


class TestApiFlightsNoSerpapiImport(unittest.TestCase):
    """api/flights.py must NOT import SerpAPI functions that make network calls."""

    def test_no_serpapi_search_imports(self):
        api_path = os.path.join(ROOT, "api", "flights.py")
        with open(api_path) as f:
            src = f.read()
        # These would indicate SerpAPI network calls being made from flights.py
        self.assertNotIn("search_serpapi", src)
        self.assertNotIn("search_skyscanner", src)
        self.assertNotIn("serpapi_guard", src)


if __name__ == "__main__":
    unittest.main()
