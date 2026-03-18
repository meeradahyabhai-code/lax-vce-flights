"""Tests for SerpAPI rate limiter and budget guard."""

import json
import os
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))


class TestSerpAPIGuard(unittest.TestCase):
    """Test the rate limiting and budget guard system."""

    def setUp(self):
        """Use a temp file for the rate log so tests don't affect real data."""
        import serpapi_guard
        self.guard = serpapi_guard
        self.original_log = self.guard.RATE_LOG_FILE
        fd, self.tmp_log = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.tmp_log)  # start fresh
        self.guard.RATE_LOG_FILE = self.tmp_log
        # Disable real email sending during tests
        self.guard._send_alert = lambda *a, **k: None

    def tearDown(self):
        self.guard.RATE_LOG_FILE = self.original_log
        try:
            os.unlink(self.tmp_log)
        except OSError:
            pass

    def test_guard_module_exists(self):
        self.assertTrue(os.path.exists(os.path.join(ROOT, "serpapi_guard.py")))

    def test_threshold_is_100(self):
        self.assertEqual(self.guard.ADHOC_THRESHOLD_24H, 100)

    def test_window_is_24h(self):
        self.assertEqual(self.guard.WINDOW_SECONDS, 24 * 3600)

    def test_log_serpapi_calls_creates_file(self):
        self.guard.log_serpapi_calls(num_calls=1, source="test")
        self.assertTrue(os.path.exists(self.tmp_log))

    def test_log_serpapi_calls_records_entry(self):
        self.guard.log_serpapi_calls(num_calls=3, source="hotels")
        entries = self.guard._read_log()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["count"], 3)
        self.assertEqual(entries[0]["source"], "hotels")

    def test_count_recent_sums_calls(self):
        self.guard.log_serpapi_calls(num_calls=5, source="hotels")
        self.guard.log_serpapi_calls(num_calls=2, source="multicity")
        self.assertEqual(self.guard._count_recent(), 7)

    def test_check_budget_allows_under_threshold(self):
        self.guard.log_serpapi_calls(num_calls=50, source="test")
        # Should not raise — 50 + 1 = 51, under 100
        self.guard.check_serpapi_budget(num_calls=1, source="test")

    def test_check_budget_blocks_over_threshold(self):
        self.guard.log_serpapi_calls(num_calls=99, source="test")
        # Should raise — 99 + 2 = 101, over 100
        with self.assertRaises(self.guard.BudgetExceeded):
            self.guard.check_serpapi_budget(num_calls=2, source="test")

    def test_check_budget_blocks_at_exact_threshold(self):
        self.guard.log_serpapi_calls(num_calls=100, source="test")
        with self.assertRaises(self.guard.BudgetExceeded):
            self.guard.check_serpapi_budget(num_calls=1, source="test")

    def test_old_entries_pruned(self):
        """Entries older than 24h should not count."""
        entries = [
            {"ts": time.time() - 25 * 3600, "count": 99, "source": "old"},
        ]
        with open(self.tmp_log, "w") as f:
            json.dump(entries, f)
        # Old entry should be ignored, so budget should be fine
        self.guard.check_serpapi_budget(num_calls=1, source="test")
        self.assertEqual(self.guard._count_recent(), 0)

    def test_write_log_prunes_old(self):
        """Writing should remove entries older than 24h."""
        old_entry = {"ts": time.time() - 25 * 3600, "count": 50, "source": "old"}
        new_entry = {"ts": time.time(), "count": 5, "source": "new"}
        self.guard._write_log([old_entry, new_entry])
        entries = self.guard._read_log()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["source"], "new")

    def test_empty_log_returns_zero(self):
        self.assertEqual(self.guard._count_recent(), 0)

    def test_corrupt_log_returns_zero(self):
        with open(self.tmp_log, "w") as f:
            f.write("not json{{{")
        self.assertEqual(self.guard._count_recent(), 0)


class TestHotelsAPIUsesGuard(unittest.TestCase):
    """Verify hotels API integrates the budget guard."""

    def test_hotels_imports_guard(self):
        src = open(os.path.join(ROOT, "api", "hotels.py")).read()
        self.assertIn("from serpapi_guard import", src)
        self.assertIn("check_serpapi_budget", src)
        self.assertIn("log_serpapi_calls", src)

    def test_hotels_checks_before_search(self):
        src = open(os.path.join(ROOT, "api", "hotels.py")).read()
        # check_serpapi_budget must come before _fresh_search
        check_pos = src.index("check_serpapi_budget")
        search_pos = src.index("_fresh_search")
        self.assertLess(check_pos, search_pos)


class TestMulticityAPIUsesGuard(unittest.TestCase):
    """Verify multicity API integrates the budget guard."""

    def test_multicity_imports_guard(self):
        src = open(os.path.join(ROOT, "api", "multicity.py")).read()
        self.assertIn("from serpapi_guard import", src)
        self.assertIn("check_serpapi_budget", src)
        self.assertIn("log_serpapi_calls", src)

    def test_multicity_checks_before_search(self):
        src = open(os.path.join(ROOT, "api", "multicity.py")).read()
        # Check within the handler body (after do_GET), not in imports
        handler_src = src[src.index("def do_GET"):]
        check_pos = handler_src.index("check_serpapi_budget")
        search_pos = handler_src.index("search_serpapi_multicity")
        self.assertLess(check_pos, search_pos)

    def test_multicity_has_cache(self):
        src = open(os.path.join(ROOT, "api", "multicity.py")).read()
        self.assertIn("_read_cache", src)
        self.assertIn("_write_cache", src)
        self.assertIn("multicity_cache_", src)

    def test_multicity_has_stale_fallback(self):
        src = open(os.path.join(ROOT, "api", "multicity.py")).read()
        self.assertIn("_stale", src)
