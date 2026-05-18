"""Tests for api/ask.py — rate limiting, daily cap, and message building.

The OpenAI HTTP call is not exercised here; we test the pure logic that
shapes prompts and gates traffic.
"""

import importlib
import json
import os
import sys
import time
import unittest
from collections import deque

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "api"))


def _fresh_module():
    """Reimport ask.py so each test starts with empty rate-limit logs."""
    if "ask" in sys.modules:
        del sys.modules["ask"]
    return importlib.import_module("ask")


class TestRateLimit(unittest.TestCase):
    def setUp(self):
        self.ask = _fresh_module()

    def test_first_request_allowed(self):
        self.assertTrue(self.ask._check_rate_limit("1.1.1.1"))

    def test_under_limit_allowed(self):
        for _ in range(self.ask.RATE_LIMIT_MAX):
            self.assertTrue(self.ask._check_rate_limit("1.1.1.1"))

    def test_over_limit_blocked(self):
        for _ in range(self.ask.RATE_LIMIT_MAX):
            self.ask._check_rate_limit("1.1.1.1")
        self.assertFalse(self.ask._check_rate_limit("1.1.1.1"))

    def test_per_ip_isolation(self):
        for _ in range(self.ask.RATE_LIMIT_MAX):
            self.ask._check_rate_limit("1.1.1.1")
        self.assertFalse(self.ask._check_rate_limit("1.1.1.1"))
        self.assertTrue(self.ask._check_rate_limit("2.2.2.2"))

    def test_window_expiry_resets_quota(self):
        ip = "1.1.1.1"
        old = time.time() - self.ask.RATE_LIMIT_WINDOW - 1
        self.ask._rate_log[ip] = deque([old] * self.ask.RATE_LIMIT_MAX)
        self.assertTrue(self.ask._check_rate_limit(ip))


class TestDailyCap(unittest.TestCase):
    def setUp(self):
        self.ask = _fresh_module()

    def test_under_cap_allowed(self):
        for _ in range(self.ask.DAILY_CAP):
            self.assertTrue(self.ask._check_daily_cap())

    def test_over_cap_blocked(self):
        for _ in range(self.ask.DAILY_CAP):
            self.ask._check_daily_cap()
        self.assertFalse(self.ask._check_daily_cap())

    def test_old_entries_drop_off(self):
        old = time.time() - self.ask.DAY_SECONDS - 1
        for _ in range(self.ask.DAILY_CAP):
            self.ask._daily_log.append(old)
        self.assertTrue(self.ask._check_daily_cap())


class TestBuildMessagesFlights(unittest.TestCase):
    def setUp(self):
        self.ask = _fresh_module()

    def test_flights_uses_flight_system_prompt(self):
        sys_p, _ = self.ask.build_messages("anything", {"context": "flights", "flights": []})
        self.assertEqual(sys_p, self.ask.SYSTEM_PROMPT_FLIGHTS)

    def test_flights_default_when_context_missing(self):
        sys_p, _ = self.ask.build_messages("q", {"flights": []})
        self.assertEqual(sys_p, self.ask.SYSTEM_PROMPT_FLIGHTS)

    def test_flights_payload_includes_question_and_origin(self):
        _, user_msg = self.ask.build_messages(
            "cheapest morning flight?",
            {
                "context": "flights",
                "origin": "LAX",
                "direction": "outbound",
                "active_date": "2026-06-29",
                "flights": [
                    {
                        "primary_airline": "Delta",
                        "search_date": "2026-06-29",
                        "departure_time": "2026-06-29T08:00",
                        "arrival_time": "2026-06-30T10:00",
                        "stops": 1,
                        "price": 872,
                    }
                ],
            },
        )
        self.assertIn("LAX", user_msg)
        self.assertIn("outbound", user_msg)
        self.assertIn("2026-06-29", user_msg)
        self.assertIn("cheapest morning flight?", user_msg)
        self.assertIn("Delta", user_msg)

    def test_flights_compacted_to_expected_keys(self):
        _, user_msg = self.ask.build_messages(
            "q",
            {
                "context": "flights",
                "flights": [
                    {
                        "primary_airline": "United",
                        "search_date": "2026-06-30",
                        "departure_time": "2026-06-30T22:00",
                        "arrival_time": "2026-07-01T20:00",
                        "stops": 0,
                        "price": 1140,
                        "extra": "should be dropped",
                    }
                ],
            },
        )
        # Find the JSON portion
        json_blob = user_msg.split("Flights JSON:\n", 1)[1]
        compact = json.loads(json_blob)
        self.assertEqual(len(compact), 1)
        self.assertEqual(set(compact[0].keys()), {"airline", "date", "dep", "arr", "stops", "price"})
        self.assertEqual(compact[0]["airline"], "United")
        self.assertEqual(compact[0]["price"], 1140)

    def test_flights_caps_at_40(self):
        flights = [
            {
                "primary_airline": "AA",
                "search_date": "2026-06-29",
                "departure_time": "2026-06-29T10:00",
                "arrival_time": "2026-06-30T12:00",
                "stops": 0,
                "price": 900,
            }
        ] * 100
        _, user_msg = self.ask.build_messages("q", {"context": "flights", "flights": flights})
        compact = json.loads(user_msg.split("Flights JSON:\n", 1)[1])
        self.assertEqual(len(compact), 40)

    def test_flights_multi_city_includes_return_leg(self):
        _, user_msg = self.ask.build_messages(
            "q",
            {
                "context": "flights",
                "flights": [
                    {
                        "type": "multi_city",
                        "primary_airline": "Air France",
                        "search_date": "2026-06-29",
                        "departure_time": "2026-06-29T15:00",
                        "arrival_time": "2026-06-30T11:00",
                        "stops": 1,
                        "price": 1850,
                        "return_airline": "Turkish",
                        "return_date": "2026-07-14",
                        "return_stops": 0,
                    }
                ],
            },
        )
        compact = json.loads(user_msg.split("Flights JSON:\n", 1)[1])
        self.assertIn("mc_return", compact[0])
        self.assertEqual(compact[0]["mc_return"]["airline"], "Turkish")


class TestBuildMessagesHotels(unittest.TestCase):
    def setUp(self):
        self.ask = _fresh_module()

    def test_hotels_uses_hotel_system_prompt(self):
        sys_p, _ = self.ask.build_messages("q", {"context": "hotels", "hotels": []})
        self.assertEqual(sys_p, self.ask.SYSTEM_PROMPT_HOTELS)

    def test_hotels_payload_includes_question_and_city(self):
        _, user_msg = self.ask.build_messages(
            "highest rated?",
            {
                "context": "hotels",
                "city": "venice",
                "check_in": "2026-06-30",
                "check_out": "2026-07-03",
                "hotels": [
                    {
                        "name": "Hotel Danieli",
                        "brand": "marriott",
                        "star_class": 5,
                        "overall_rating": 4.7,
                        "reviews": 2100,
                        "rate_per_night": 820,
                        "total_rate": 2460,
                        "nights": 3,
                        "distance_mi": 0.2,
                        "landmark_name": "St Marks Square",
                    }
                ],
            },
        )
        self.assertIn("venice", user_msg)
        self.assertIn("2026-06-30", user_msg)
        self.assertIn("highest rated?", user_msg)
        self.assertIn("Hotel Danieli", user_msg)

    def test_hotels_compacted_to_expected_keys(self):
        _, user_msg = self.ask.build_messages(
            "q",
            {
                "context": "hotels",
                "hotels": [
                    {
                        "name": "Hilton Molino Stucky",
                        "brand": "hilton",
                        "star_class": 5,
                        "overall_rating": 4.5,
                        "reviews": 3400,
                        "rate_per_night": 410,
                        "total_rate": 1230,
                        "nights": 3,
                        "distance_mi": 1.1,
                        "landmark_name": "St Marks Square",
                        "cc_programs": ["fhr"],
                        "should_be_dropped": "yes",
                    }
                ],
            },
        )
        compact = json.loads(user_msg.split("Hotels JSON:\n", 1)[1])
        self.assertEqual(len(compact), 1)
        expected_keys = {
            "name", "brand", "stars", "rating", "reviews", "rate_per_night",
            "total_rate", "nights", "distance_mi", "landmark", "cc_programs",
        }
        self.assertEqual(set(compact[0].keys()), expected_keys)
        self.assertEqual(compact[0]["brand"], "hilton")
        self.assertEqual(compact[0]["cc_programs"], ["fhr"])

    def test_hotels_brand_defaults_to_independent(self):
        _, user_msg = self.ask.build_messages(
            "q",
            {
                "context": "hotels",
                "hotels": [{"name": "Some B&B"}],
            },
        )
        compact = json.loads(user_msg.split("Hotels JSON:\n", 1)[1])
        self.assertEqual(compact[0]["brand"], "independent")

    def test_hotels_caps_at_40(self):
        hotels = [{"name": f"h{i}"} for i in range(80)]
        _, user_msg = self.ask.build_messages("q", {"context": "hotels", "hotels": hotels})
        compact = json.loads(user_msg.split("Hotels JSON:\n", 1)[1])
        self.assertEqual(len(compact), 40)


class TestSystemPrompts(unittest.TestCase):
    def setUp(self):
        self.ask = _fresh_module()

    def test_flight_prompt_grounds_in_data(self):
        self.assertIn("ONLY", self.ask.SYSTEM_PROMPT_FLIGHTS)
        self.assertIn("invent", self.ask.SYSTEM_PROMPT_FLIGHTS)

    def test_hotel_prompt_grounds_in_data(self):
        self.assertIn("ONLY", self.ask.SYSTEM_PROMPT_HOTELS)
        self.assertIn("invent", self.ask.SYSTEM_PROMPT_HOTELS)

    def test_prompts_distinct(self):
        self.assertNotEqual(self.ask.SYSTEM_PROMPT_FLIGHTS, self.ask.SYSTEM_PROMPT_HOTELS)


if __name__ == "__main__":
    unittest.main()
