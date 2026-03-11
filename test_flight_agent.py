"""Unit tests for flight_agent.py pricing, scoring, fare labeling, and return flights."""

import unittest

from flight_agent import (
    AIRLINE_BONUSES,
    AUTO_TOP_PICK_NONSTOP,
    BASIC_ECONOMY_CARRIERS,
    BASIC_TO_MAIN_ADDER,
    RETURN_AIRLINE_BONUSES,
    RETURN_AUTO_TOP_PICK_NONSTOP,
    RETURN_DATES,
    _normalize_skyscanner,
    label_fare_types,
    normalize,
    score_flights,
)


def _make_flight(airline="Delta", price=500, stops=1, layover=60,
                 duration=900, dep="2026-06-29 17:55", search_date="2026-06-29"):
    """Helper to create a minimal flight dict for testing."""
    return {
        "primary_airline": airline,
        "airlines": [airline],
        "departure_time": dep,
        "arrival_time": "2026-06-30 12:00",
        "stops": stops,
        "total_layover_min": layover,
        "total_duration_min": duration,
        "price": price,
        "search_date": search_date,
        "google_flights_url": "",
        "raw": {},
    }


class TestFareLabeling(unittest.TestCase):
    """Tests for label_fare_types()."""

    def test_big3_labeled_economy_main(self):
        """All Big 3 flights should be labeled Economy Main."""
        for carrier in ("Delta", "United", "American"):
            flights = label_fare_types([_make_flight(airline=carrier)])
            self.assertEqual(flights[0]["fare_type"], "Economy Main")

    def test_big3_has_basic_economy_price(self):
        """Big 3 flights should store the original price as basic_economy_price."""
        f = _make_flight(airline="Delta", price=427)
        flights = label_fare_types([f])
        self.assertEqual(flights[0]["basic_economy_price"], 427)

    def test_big3_economy_main_price_is_base_plus_adder(self):
        """Economy Main estimate = Basic Economy + flat adder."""
        for price in (419, 427, 512, 595, 800, 1200):
            f = _make_flight(airline="United", price=price)
            flights = label_fare_types([f])
            expected = price + BASIC_TO_MAIN_ADDER
            self.assertEqual(
                flights[0]["economy_main_price"], expected,
                f"price={price}: expected Main={expected}, "
                f"got {flights[0]['economy_main_price']}"
            )

    def test_big3_main_always_greater_than_basic(self):
        """Economy Main price must always be greater than Basic Economy."""
        for price in (100, 427, 512, 999, 2000):
            f = _make_flight(airline="American", price=price)
            flights = label_fare_types([f])
            self.assertGreater(
                flights[0]["economy_main_price"],
                flights[0]["basic_economy_price"],
                f"Main must be > Basic for price={price}"
            )

    def test_non_big3_no_basic_economy(self):
        """Non-Big-3 carriers should not have basic_economy_price."""
        for carrier in ("British Airways", "Air France", "Lufthansa", "KLM"):
            flights = label_fare_types([_make_flight(airline=carrier, price=595)])
            self.assertIsNone(flights[0]["basic_economy_price"])
            self.assertIsNone(flights[0]["economy_main_price"])
            self.assertEqual(flights[0]["fare_type"], "Economy Main")

    def test_non_big3_price_unchanged(self):
        """Non-Big-3 carriers' price field should not be modified."""
        f = _make_flight(airline="British Airways", price=595)
        flights = label_fare_types([f])
        self.assertEqual(flights[0]["price"], 595)

    def test_basic_economy_carriers_set(self):
        """Verify the Big 3 set is correct."""
        self.assertEqual(BASIC_ECONOMY_CARRIERS, {"american", "delta", "united"})

    def test_adder_is_positive(self):
        """The Main Economy adder must be a positive number."""
        self.assertGreater(BASIC_TO_MAIN_ADDER, 0)
        self.assertLessEqual(BASIC_TO_MAIN_ADDER, 200,
                             "Adder seems unreasonably high for intl flights")


class TestScoring(unittest.TestCase):
    """Tests for score_flights()."""

    def test_lower_price_scores_better(self):
        """Cheaper flight should score lower (better) all else equal."""
        cheap = _make_flight(airline="Delta", price=400)
        expensive = _make_flight(airline="Delta", price=600)
        scored = score_flights([cheap, expensive])
        self.assertLess(scored[0]["score"], scored[1]["score"])

    def test_delta_beats_american_same_price(self):
        """Delta (bonus -300) should score better than American (-180) at same price."""
        delta = _make_flight(airline="Delta", price=500)
        american = _make_flight(airline="American", price=500)
        scored = score_flights([delta, american])
        self.assertEqual(scored[0]["primary_airline"], "Delta")

    def test_nonstop_bonus_applied(self):
        """Nonstop flights should get a -100 bonus."""
        nonstop = _make_flight(stops=0, layover=0)
        one_stop = _make_flight(stops=1, layover=60)
        scored = score_flights([nonstop, one_stop])
        # Nonstop should score significantly better
        self.assertLess(scored[0]["score"], scored[1]["score"])

    def test_evening_time_bonus(self):
        """5-9 PM departure should get -80 time bonus."""
        evening = _make_flight(dep="2026-06-29 17:55")
        midday = _make_flight(dep="2026-06-29 13:00")
        scored = score_flights([evening, midday])
        self.assertLess(scored[0]["score"], scored[1]["score"])

    def test_morning_time_bonus(self):
        """6-10 AM departure should get -80 time bonus."""
        morning = _make_flight(dep="2026-06-29 08:00")
        midday = _make_flight(dep="2026-06-29 13:00")
        scored = score_flights([morning, midday])
        self.assertLess(scored[0]["score"], scored[1]["score"])

    def test_speed_bonus_under_16h(self):
        """Under 16h (960min) should get -60 speed bonus."""
        fast = _make_flight(duration=800)
        slow = _make_flight(duration=1300)
        scored = score_flights([fast, slow])
        self.assertLess(scored[0]["score"], scored[1]["score"])

    def test_flights_sorted_by_score(self):
        """score_flights should return flights sorted by score ascending."""
        flights = [
            _make_flight(airline="British Airways", price=900),
            _make_flight(airline="Delta", price=400),
            _make_flight(airline="United", price=600),
        ]
        scored = score_flights(flights)
        scores = [f["score"] for f in scored]
        self.assertEqual(scores, sorted(scores))

    def test_score_is_numeric(self):
        """Every flight must have a numeric score."""
        flights = [_make_flight()]
        scored = score_flights(flights)
        self.assertIsInstance(scored[0]["score"], (int, float))

    def test_airline_bonuses_are_negative(self):
        """All airline bonuses should be negative (bonuses, not penalties)."""
        for airline, bonus in AIRLINE_BONUSES.items():
            self.assertLess(bonus, 0, f"{airline} bonus should be negative")


class TestPriceGuardrails(unittest.TestCase):
    """Guardrail tests to catch pricing bugs."""

    def test_economy_main_price_within_bounds(self):
        """Economy Main price should be $50-$200 more than Basic Economy."""
        for price in (300, 427, 512, 800, 1500):
            f = _make_flight(airline="Delta", price=price)
            flights = label_fare_types([f])
            diff = flights[0]["economy_main_price"] - flights[0]["basic_economy_price"]
            self.assertGreaterEqual(diff, 50,
                                    f"Main-Basic diff too small: ${diff} for base ${price}")
            self.assertLessEqual(diff, 200,
                                 f"Main-Basic diff too large: ${diff} for base ${price}")

    def test_basic_economy_equals_original_price(self):
        """basic_economy_price must always equal the original price for Big 3."""
        for price in (419, 427, 512):
            f = _make_flight(airline="United", price=price)
            flights = label_fare_types([f])
            self.assertEqual(flights[0]["basic_economy_price"], price)

    def test_no_negative_prices(self):
        """No price field should ever be negative."""
        f = _make_flight(airline="Delta", price=100)
        flights = label_fare_types([f])
        self.assertGreater(flights[0]["price"], 0)
        self.assertGreater(flights[0]["basic_economy_price"], 0)
        self.assertGreater(flights[0]["economy_main_price"], 0)

    def test_display_price_never_below_actual(self):
        """Economy Main estimate must never be below the actual fare."""
        for price in (200, 427, 512, 1000):
            f = _make_flight(airline="American", price=price)
            flights = label_fare_types([f])
            self.assertGreaterEqual(flights[0]["economy_main_price"], price)


class TestReturnScoring(unittest.TestCase):
    """Tests for return-flight scoring (IST → LAX)."""

    def test_return_bonuses_are_negative(self):
        """All return airline bonuses should be negative."""
        for airline, bonus in RETURN_AIRLINE_BONUSES.items():
            self.assertLess(bonus, 0, f"{airline} return bonus should be negative")

    def test_turkish_nonstop_auto_top_pick(self):
        """Turkish Airlines nonstop should get score forced to 0."""
        f = _make_flight(airline="Turkish Airlines", price=700, stops=0,
                         layover=0, search_date="2026-07-13")
        scored = score_flights(
            [f],
            airline_bonuses=RETURN_AIRLINE_BONUSES,
            auto_top_picks=RETURN_AUTO_TOP_PICK_NONSTOP,
        )
        self.assertEqual(scored[0]["score"], 0)

    def test_turkish_1stop_not_auto_top(self):
        """Turkish Airlines with 1 stop should NOT get score 0."""
        f = _make_flight(airline="Turkish Airlines", price=700, stops=1,
                         layover=120, search_date="2026-07-13")
        scored = score_flights(
            [f],
            airline_bonuses=RETURN_AIRLINE_BONUSES,
            auto_top_picks=RETURN_AUTO_TOP_PICK_NONSTOP,
        )
        self.assertNotEqual(scored[0]["score"], 0)

    def test_turkish_1stop_gets_return_bonus(self):
        """Turkish Airlines 1-stop should use the -250 return bonus."""
        tk = _make_flight(airline="Turkish Airlines", price=600, stops=1,
                          layover=90, search_date="2026-07-13")
        generic = _make_flight(airline="SomeAirline", price=600, stops=1,
                               layover=90, search_date="2026-07-13")
        scored = score_flights(
            [tk, generic],
            airline_bonuses=RETURN_AIRLINE_BONUSES,
            auto_top_picks=RETURN_AUTO_TOP_PICK_NONSTOP,
        )
        tk_score = next(f["score"] for f in scored if f["primary_airline"] == "Turkish Airlines")
        gen_score = next(f["score"] for f in scored if f["primary_airline"] == "SomeAirline")
        self.assertLess(tk_score, gen_score)

    def test_turkish_nonstop_beats_everything(self):
        """Turkish nonstop (score=0) should beat any other flight."""
        tk_nonstop = _make_flight(airline="Turkish Airlines", price=900, stops=0,
                                  layover=0, search_date="2026-07-13")
        cheap = _make_flight(airline="Lufthansa", price=400, stops=1,
                             layover=60, search_date="2026-07-13")
        scored = score_flights(
            [tk_nonstop, cheap],
            airline_bonuses=RETURN_AIRLINE_BONUSES,
            auto_top_picks=RETURN_AUTO_TOP_PICK_NONSTOP,
        )
        self.assertEqual(scored[0]["primary_airline"], "Turkish Airlines")
        self.assertEqual(scored[0]["score"], 0)

    def test_outbound_has_no_auto_top_picks(self):
        """Outbound scoring should have no auto-top-pick airlines."""
        self.assertEqual(len(AUTO_TOP_PICK_NONSTOP), 0)

    def test_return_dates_are_july(self):
        """Return dates should be July 13-15, 2026."""
        self.assertEqual(RETURN_DATES, ["2026-07-13", "2026-07-14", "2026-07-15"])

    def test_return_scoring_uses_custom_bonuses(self):
        """Lufthansa should get -200 bonus in return scoring (not -160 outbound)."""
        lh = _make_flight(airline="Lufthansa", price=600, stops=1,
                          layover=60, search_date="2026-07-13")
        scored_return = score_flights(
            [lh],
            airline_bonuses=RETURN_AIRLINE_BONUSES,
            auto_top_picks=RETURN_AUTO_TOP_PICK_NONSTOP,
        )
        scored_outbound = score_flights(
            [_make_flight(airline="Lufthansa", price=600, stops=1,
                          layover=60, search_date="2026-06-29")],
        )
        # Return bonus (-200) is bigger than outbound (-160), so return score is lower
        self.assertLess(scored_return[0]["score"], scored_outbound[0]["score"])


class TestSkyscannerNormalize(unittest.TestCase):
    """Tests for Skyscanner result normalization."""

    def _make_skyscanner_itin(self, **overrides):
        """Build a minimal Skyscanner itinerary dict."""
        itin = {
            "_source": "skyscanner",
            "_search_date": "2026-07-13",
            "price": {"raw": 650, "formatted": "$650"},
            "legs": [{
                "origin": {"id": "IST", "name": "Istanbul Airport"},
                "destination": {"id": "LAX", "name": "Los Angeles"},
                "departure": "2026-07-13T22:00:00",
                "arrival": "2026-07-14T03:30:00",
                "durationInMinutes": 810,
                "stopCount": 0,
                "carriers": {"marketing": [{"name": "Turkish Airlines"}]},
                "segments": [{
                    "origin": {"flightPlaceId": "IST"},
                    "destination": {"flightPlaceId": "LAX"},
                    "departure": "2026-07-13T22:00:00",
                    "arrival": "2026-07-14T03:30:00",
                    "marketingCarrier": {"name": "Turkish Airlines", "alternateId": "TK"},
                    "flightNumber": "9",
                }],
            }],
        }
        itin.update(overrides)
        return itin

    def test_basic_normalization(self):
        """Skyscanner itinerary should normalize to common schema."""
        result = _normalize_skyscanner(self._make_skyscanner_itin())
        self.assertIsNotNone(result)
        self.assertEqual(result["primary_airline"], "Turkish Airlines")
        self.assertEqual(result["price"], 650)
        self.assertEqual(result["stops"], 0)
        self.assertEqual(result["search_date"], "2026-07-13")
        self.assertEqual(result["source"], "skyscanner")

    def test_no_legs_returns_none(self):
        """Itinerary with no legs should return None."""
        result = _normalize_skyscanner({"legs": [], "_search_date": "2026-07-13",
                                         "price": {"raw": 500}})
        self.assertIsNone(result)

    def test_zero_price_returns_none(self):
        """Itinerary with price 0 should return None."""
        itin = self._make_skyscanner_itin()
        itin["price"] = {"raw": 0}
        result = _normalize_skyscanner(itin)
        self.assertIsNone(result)

    def test_1stop_layover_computed(self):
        """1-stop itinerary should compute layover duration from segments."""
        itin = self._make_skyscanner_itin()
        itin["legs"][0]["stopCount"] = 1
        itin["legs"][0]["segments"] = [
            {
                "origin": {"flightPlaceId": "IST"},
                "destination": {"flightPlaceId": "LHR", "name": "London Heathrow"},
                "departure": "2026-07-13T22:00:00",
                "arrival": "2026-07-14T01:00:00",
            },
            {
                "origin": {"flightPlaceId": "LHR"},
                "destination": {"flightPlaceId": "LAX"},
                "departure": "2026-07-14T03:00:00",
                "arrival": "2026-07-14T06:30:00",
            },
        ]
        result = _normalize_skyscanner(itin)
        self.assertEqual(result["stops"], 1)
        self.assertEqual(result["total_layover_min"], 120)  # 2h layover

    def test_normalize_routes_skyscanner_source(self):
        """normalize() should detect _source=skyscanner and use Skyscanner normalizer."""
        itin = self._make_skyscanner_itin()
        results = normalize([itin])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], "skyscanner")
        self.assertEqual(results[0]["primary_airline"], "Turkish Airlines")

    def test_duration_preserved(self):
        """Duration in minutes should be preserved from legs."""
        result = _normalize_skyscanner(self._make_skyscanner_itin())
        self.assertEqual(result["total_duration_min"], 810)

    def test_google_flights_url_empty(self):
        """Skyscanner results should have empty google_flights_url."""
        result = _normalize_skyscanner(self._make_skyscanner_itin())
        self.assertEqual(result["google_flights_url"], "")


if __name__ == "__main__":
    unittest.main()
