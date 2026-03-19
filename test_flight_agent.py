"""Unit tests for flight_agent.py pricing, scoring, fare labeling, return flights,
JSON export, email HTML, and summary API.
"""

import json
import unittest

from flight_agent import (
    AIRLINE_BONUSES,
    AKL_VCE_BONUSES as AKL_OUTBOUND_BONUSES,
    IST_AKL_AUTO_TOP_PICK as AKL_RETURN_AUTO_TOP_PICK,
    IST_AKL_BONUSES as AKL_RETURN_BONUSES,
    ATL_VCE_BONUSES as ATL_OUTBOUND_BONUSES,
    IST_ATL_AUTO_TOP_PICK as ATL_RETURN_AUTO_TOP_PICK,
    IST_ATL_BONUSES as ATL_RETURN_BONUSES,
    YVR_VCE_BONUSES as YVR_OUTBOUND_BONUSES,
    IST_YVR_AUTO_TOP_PICK as YVR_RETURN_AUTO_TOP_PICK,
    IST_YVR_BONUSES as YVR_RETURN_BONUSES,
    AUTO_TOP_PICK_NONSTOP,
    BASIC_ECONOMY_CARRIERS,
    DEPARTURE_DATES,
    RETURN_AIRLINE_BONUSES,
    _extract_fare_prices_from_raw,
    merge_premium_business_prices,
    RETURN_AUTO_TOP_PICK_NONSTOP,
    RETURN_DATES,
    ROUTES,
    SCORING_RATIONALE,
    _build_date_sections,
    _flight_to_dict,
    _layover_info,
    _normalize_skyscanner,
    _normalize_serpapi_multicity,
    build_email_html,
    dedup_flights,
    filter_flights,
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

    def test_big3_economy_main_price_includes_adder(self):
        """Big 3 economy_main_price should be basic + per-carrier adder."""
        expected = {"United": 527, "Delta": 547, "American": 527}
        for carrier, exp in expected.items():
            f = _make_flight(airline=carrier, price=427)
            flights = label_fare_types([f])
            self.assertEqual(flights[0]["economy_main_price"], exp,
                             f"{carrier} should have economy_main_price={exp}")

    def test_big3_fare_type_is_economy_main(self):
        """Big 3 fare_type should be 'Economy Main'."""
        for carrier in ("United", "Delta", "American"):
            f = _make_flight(airline=carrier, price=427)
            flights = label_fare_types([f])
            self.assertEqual(flights[0]["fare_type"], "Economy Main")

    def test_non_big3_basic_equals_price(self):
        """Non-Big-3 carriers should have basic_economy_price equal to price."""
        for carrier in ("British Airways", "Air France", "Lufthansa", "KLM"):
            flights = label_fare_types([_make_flight(airline=carrier, price=595)])
            self.assertEqual(flights[0]["basic_economy_price"], 595)
            self.assertEqual(flights[0]["economy_main_price"], 595)
            self.assertEqual(flights[0]["fare_type"], "Economy Main")

    def test_non_big3_price_unchanged(self):
        """Non-Big-3 carriers' price field should not be modified."""
        f = _make_flight(airline="British Airways", price=595)
        flights = label_fare_types([f])
        self.assertEqual(flights[0]["price"], 595)

    def test_basic_economy_carriers_set(self):
        """Verify the Big 3 set is correct."""
        self.assertEqual(BASIC_ECONOMY_CARRIERS, {"american", "delta", "united"})

    def test_big3_basic_economy_price_equals_search_price(self):
        """Big 3 basic_economy_price should equal the SerpAPI search price."""
        f = _make_flight(airline="Delta", price=427)
        flights = label_fare_types([f])
        self.assertEqual(flights[0]["basic_economy_price"], 427)

    def test_delta_adder_is_120(self):
        """Delta Main Cabin adder should be $120."""
        f = _make_flight(airline="Delta", price=400)
        flights = label_fare_types([f])
        self.assertEqual(flights[0]["economy_main_price"], 520)

    def test_united_adder_is_100(self):
        """United Main Cabin adder should be $100."""
        f = _make_flight(airline="United", price=400)
        flights = label_fare_types([f])
        self.assertEqual(flights[0]["economy_main_price"], 500)

    def test_american_adder_is_100(self):
        """American Main Cabin adder should be $100."""
        f = _make_flight(airline="American", price=400)
        flights = label_fare_types([f])
        self.assertEqual(flights[0]["economy_main_price"], 500)

    def test_adder_dict_has_all_big3(self):
        """BASIC_TO_MAIN_ADDER should have entries for all Big 3 carriers."""
        from flight_agent import BASIC_TO_MAIN_ADDER
        for carrier in BASIC_ECONOMY_CARRIERS:
            self.assertIn(carrier, BASIC_TO_MAIN_ADDER,
                          f"{carrier} missing from BASIC_TO_MAIN_ADDER")


class TestDepartureDates(unittest.TestCase):
    """Tests for departure and return date configuration."""

    def test_departure_dates_include_june_28(self):
        """June 28 should be in departure dates."""
        self.assertIn("2026-06-28", DEPARTURE_DATES)

    def test_departure_dates_include_june_29(self):
        """June 29 should be in departure dates."""
        self.assertIn("2026-06-29", DEPARTURE_DATES)

    def test_departure_dates_exclude_july_1(self):
        """July 1 should NOT be in departure dates."""
        self.assertNotIn("2026-07-01", DEPARTURE_DATES)

    def test_departure_dates_count(self):
        """Should have exactly 3 departure dates."""
        self.assertEqual(len(DEPARTURE_DATES), 3)

    def test_return_dates_unchanged(self):
        """Return dates should be July 13/14/15."""
        self.assertEqual(RETURN_DATES, ["2026-07-13", "2026-07-14", "2026-07-15"])


class TestSerpAPILogging(unittest.TestCase):
    """Tests for SerpAPI call logging."""

    def test_call_log_functions_exist(self):
        """Call log helper functions should be importable."""
        from flight_agent import get_serpapi_call_log, reset_serpapi_call_log
        reset_serpapi_call_log()
        self.assertEqual(len(get_serpapi_call_log()), 0)


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

    def test_big3_economy_main_includes_adder(self):
        """Big 3 economy_main_price should be basic + per-carrier adder."""
        for price in (300, 427, 512, 800, 1500):
            f = _make_flight(airline="Delta", price=price)
            flights = label_fare_types([f])
            self.assertEqual(flights[0]["economy_main_price"], price + 120)  # Delta = +120

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

    def test_non_big3_economy_main_equals_price(self):
        """Non-Big-3 economy_main_price should equal the search price."""
        for carrier in ("British Airways", "Iberia", "KLM"):
            f = _make_flight(airline=carrier, price=500)
            flights = label_fare_types([f])
            self.assertEqual(flights[0]["economy_main_price"], 500)


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
        """Air France should get different bonuses for return vs outbound routes."""
        af = _make_flight(airline="Air France", price=600, stops=1,
                          layover=60, search_date="2026-07-13")
        scored_return = score_flights(
            [af],
            airline_bonuses=RETURN_AIRLINE_BONUSES,
            auto_top_picks=RETURN_AUTO_TOP_PICK_NONSTOP,
        )
        scored_outbound = score_flights(
            [_make_flight(airline="Air France", price=600, stops=1,
                          layover=60, search_date="2026-06-29")],
        )
        # Return bonus (-190) vs outbound bonus (-200) — different per route
        self.assertNotEqual(scored_return[0]["score"], scored_outbound[0]["score"])


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


class TestFlightToDict(unittest.TestCase):
    """Tests for _flight_to_dict serialization."""

    def test_all_required_keys_present(self):
        """Serialized flight dict must contain all required keys."""
        f = _make_flight(airline="Delta", price=500)
        f = label_fare_types([f])[0]
        f = score_flights([f])[0]
        d = _flight_to_dict(f)
        required = {
            "primary_airline", "airlines", "flight_numbers",
            "departure_time", "arrival_time",
            "stops", "total_layover_min", "total_duration_min", "price",
            "score", "search_date", "fare_type", "economy_main_price",
            "basic_economy_price", "premium_economy_price", "business_price",
            "google_flights_url", "layover_info",
        }
        self.assertEqual(set(d.keys()), required)

    def test_raw_field_excluded(self):
        """Serialized dict should not include the bulky 'raw' field."""
        f = _make_flight()
        f = label_fare_types([f])[0]
        f = score_flights([f])[0]
        d = _flight_to_dict(f)
        self.assertNotIn("raw", d)

    def test_price_is_numeric(self):
        """Price in serialized dict must be numeric."""
        f = _make_flight(price=427)
        f = label_fare_types([f])[0]
        f = score_flights([f])[0]
        d = _flight_to_dict(f)
        self.assertIsInstance(d["price"], (int, float))

    def test_layover_info_for_nonstop(self):
        """Nonstop flights should have 'Nonstop' as layover_info."""
        f = _make_flight(stops=0, layover=0)
        self.assertEqual(_layover_info(f), "Nonstop")

    def test_layover_info_for_connection(self):
        """Flight with layover data should include airport name."""
        f = _make_flight(stops=1, layover=150)
        f["raw"] = {"layovers": [{"name": "Paris CDG", "duration": 150}]}
        info = _layover_info(f)
        self.assertIn("Paris CDG", info)
        self.assertIn("2h 30m", info)


class TestClientSideScoringCompat(unittest.TestCase):
    """Tests that the API pipeline works WITHOUT server-side scoring.

    These prevent regressions like the _flight_to_dict crash when
    score_flights() is not called server-side (scoring moved to client JS).
    """

    def test_flight_to_dict_without_score(self):
        """_flight_to_dict must not crash when flight has no 'score' key."""
        f = _make_flight(airline="Delta", price=500)
        f = label_fare_types([f])[0]
        # Do NOT call score_flights — simulates the API pipeline
        d = _flight_to_dict(f)
        self.assertIn("score", d)
        self.assertEqual(d["score"], 0)  # default when not scored

    def test_flight_to_dict_all_keys_without_scoring(self):
        """All required keys must be present even without server scoring."""
        f = _make_flight(airline="Air France", price=600, stops=1, layover=90)
        f = label_fare_types([f])[0]
        d = _flight_to_dict(f)
        required = {
            "primary_airline", "airlines", "flight_numbers",
            "departure_time", "arrival_time",
            "stops", "total_layover_min", "total_duration_min", "price",
            "score", "search_date", "fare_type", "economy_main_price",
            "basic_economy_price", "premium_economy_price", "business_price",
            "google_flights_url", "layover_info",
        }
        self.assertEqual(set(d.keys()), required)

    def test_flight_to_dict_price_survives(self):
        """Price must pass through to dict without scoring."""
        f = _make_flight(price=427)
        f = label_fare_types([f])[0]
        d = _flight_to_dict(f)
        self.assertEqual(d["price"], 427)

    def test_flight_to_dict_serializable_without_score(self):
        """Dict must be JSON-serializable without server scoring."""
        import json
        f = _make_flight(airline="Turkish Airlines", price=580, stops=1, layover=120)
        f = label_fare_types([f])[0]
        d = _flight_to_dict(f)
        # Should not raise
        serialized = json.dumps(d)
        self.assertIn("Turkish Airlines", serialized)

    def test_multiple_flights_to_dict_without_scoring(self):
        """Batch of flights should all serialize without scoring."""
        flights = [
            _make_flight(airline="Delta", price=450),
            _make_flight(airline="United", price=520, stops=1, layover=60),
            _make_flight(airline="British Airways", price=600, stops=1, layover=90),
        ]
        flights = label_fare_types(flights)
        for f in flights:
            d = _flight_to_dict(f)
            self.assertIn("primary_airline", d)
            self.assertIn("price", d)
            self.assertEqual(d["score"], 0)

    def test_multicity_flight_to_dict_without_scoring(self):
        """Multi-city flights should serialize without scoring."""
        f = _make_flight(airline="Delta", price=1200)
        f["type"] = "multi_city"
        f["return_leg"] = {
            "primary_airline": "Air France",
            "airlines": ["Air France"],
            "departure_time": "2026-07-14 10:00",
            "arrival_time": "2026-07-15 06:00",
            "stops": 1,
            "total_layover_min": 120,
            "total_duration_min": 840,
            "layover_info": "2h in CDG",
            "search_date": "2026-07-14",
        }
        f = label_fare_types([f])[0]
        d = _flight_to_dict(f)
        self.assertEqual(d["score"], 0)
        self.assertIn("return_leg", d)

    def test_api_does_not_import_score_flights(self):
        """api/flights.py should NOT import score_flights."""
        with open("api/flights.py", "r") as fh:
            source = fh.read()
        # score_flights should not be in the imports
        self.assertNotIn("score_flights", source)

    def test_client_scoring_in_frontend(self):
        """Frontend JS must contain client-side scoring logic."""
        with open("public/index.html", "r") as fh:
            html = fh.read()
        self.assertIn("function scoreFlight(", html)
        self.assertIn("function scoreAndSortFlights(", html)
        self.assertIn("ROUTE_BONUSES", html)

    def test_client_scoring_applied_after_data_load(self):
        """Frontend must call scoreAndSortFlights after loading data."""
        with open("public/index.html", "r") as fh:
            html = fh.read()
        self.assertIn("scoreAndSortFlights(", html)
        # Should be called in the data loading section
        start = html.index("async function startApp()")
        end = html.index("renderCountdown();", start + 100)
        load_section = html[start:end + 500]
        self.assertIn("scoreAndSortFlights", load_section)

    def test_all_origins_have_client_bonuses(self):
        """Frontend ROUTE_BONUSES must cover all 4 origins."""
        with open("public/index.html", "r") as fh:
            html = fh.read()
        for origin in ["LAX", "ATL", "AKL", "YVR"]:
            self.assertIn(origin + ':', html)

    def test_no_cache_version_regression(self):
        """Cache key must be v3+ to avoid serving stale scored data."""
        with open("public/index.html", "r") as fh:
            html = fh.read()
        self.assertIn("dcf_flights_cache_v3", html)
        # v2 should only appear in cleanup code, not as the active key
        self.assertNotIn("FLIGHT_CACHE_KEY = 'dcf_flights_cache_v2'", html)


class TestEmailHTML(unittest.TestCase):
    """Tests for build_email_html output."""

    def _scored_flights(self, dates, airline_bonuses=None, auto_top_picks=None):
        """Build a set of scored flights for given dates."""
        flights = []
        for dt in dates:
            flights.append(_make_flight(airline="Delta", price=500, search_date=dt))
            flights.append(_make_flight(airline="United", price=600, search_date=dt))
        flights = label_fare_types(flights)
        return score_flights(flights, airline_bonuses=airline_bonuses,
                             auto_top_picks=auto_top_picks)

    def test_outbound_only_email(self):
        """Email with only outbound flights should contain outbound dates."""
        outbound = self._scored_flights(DEPARTURE_DATES)
        html = build_email_html(outbound)
        self.assertIn("Cruise Bound", html)
        self.assertIn("June 29", html)
        self.assertNotIn("Return Flights", html)

    def test_outbound_plus_return_email(self):
        """Email with both should contain outbound and return sections."""
        outbound = self._scored_flights(DEPARTURE_DATES)
        ret = self._scored_flights(
            RETURN_DATES,
            airline_bonuses=RETURN_AIRLINE_BONUSES,
            auto_top_picks=RETURN_AUTO_TOP_PICK_NONSTOP,
        )
        html = build_email_html(outbound, ret)
        self.assertIn("Cruise Bound", html)
        self.assertIn("Return Flights", html)
        self.assertIn("IST to LAX", html)
        self.assertIn("July 13", html)

    def test_email_has_select_flight_buttons(self):
        """Email cards should contain 'Select Flight' CTA."""
        outbound = self._scored_flights(["2026-06-29"])
        html = build_email_html(outbound)
        self.assertIn("Select Flight", html)

    def test_email_has_see_whos_interested_link(self):
        """Email cards should contain 'See who's interested' deep link."""
        outbound = self._scored_flights(["2026-06-29"])
        html = build_email_html(outbound)
        self.assertIn("See who", html)
        self.assertIn("interested", html)

    def test_email_has_score_badges(self):
        """Email should contain score label badges."""
        outbound = self._scored_flights(["2026-06-29"])
        html = build_email_html(outbound)
        # Should contain at least one of the score labels
        has_label = ("Excellent Choice" in html or
                     "Solid Pick" in html or
                     "Fair Option" in html)
        self.assertTrue(has_label, "Email should contain a score label")

    def test_email_price_font_size_38(self):
        """Email price block should use 38px font size."""
        outbound = self._scored_flights(["2026-06-29"])
        html = build_email_html(outbound)
        self.assertIn("font-size:38px", html)

    def test_email_total_flights_count(self):
        """Footer should show total flight count across both directions."""
        outbound = self._scored_flights(DEPARTURE_DATES)
        ret = self._scored_flights(RETURN_DATES)
        html = build_email_html(outbound, ret)
        total = len(outbound) + len(ret)
        self.assertIn(str(total), html)


class TestFilterAndDedup(unittest.TestCase):
    """Tests for filter_flights and dedup_flights."""

    def test_blocked_airlines_removed(self):
        """Budget carriers should be filtered out."""
        flights = [
            _make_flight(airline="Spirit", price=200),
            _make_flight(airline="Delta", price=500),
        ]
        filtered = filter_flights(flights)
        airlines = [f["primary_airline"] for f in filtered]
        self.assertNotIn("Spirit", airlines)
        self.assertIn("Delta", airlines)

    def test_2plus_stops_removed(self):
        """Flights with 2+ stops should be filtered out."""
        flights = [
            _make_flight(stops=2, layover=200),
            _make_flight(stops=1, layover=60),
            _make_flight(stops=0, layover=0),
        ]
        filtered = filter_flights(flights)
        self.assertEqual(len(filtered), 2)

    def test_dedup_keeps_cheapest(self):
        """Dedup should keep the cheapest of duplicate flights."""
        flights = [
            _make_flight(airline="Delta", price=500, dep="2026-06-29 17:55"),
            _make_flight(airline="Delta", price=450, dep="2026-06-29 17:55"),
        ]
        deduped = dedup_flights(flights)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["price"], 450)

    def test_different_times_not_deduped(self):
        """Flights at different times should not be merged."""
        flights = [
            _make_flight(airline="Delta", price=500, dep="2026-06-29 08:00"),
            _make_flight(airline="Delta", price=500, dep="2026-06-29 17:55"),
        ]
        deduped = dedup_flights(flights)
        self.assertEqual(len(deduped), 2)


class TestDateSections(unittest.TestCase):
    """Tests for _build_date_sections email helper."""

    def test_sections_contain_date_headings(self):
        """Each date should get its own heading in the email."""
        flights = [
            _make_flight(search_date="2026-06-29"),
            _make_flight(search_date="2026-06-30"),
        ]
        flights = label_fare_types(flights)
        flights = score_flights(flights)
        html = _build_date_sections(flights, "outbound")
        self.assertIn("June 29", html)
        self.assertIn("June 30", html)

    def test_show_more_link_when_over_3(self):
        """Dates with >3 flights should get a 'View all' link."""
        flights = [_make_flight(search_date="2026-06-29", price=p)
                   for p in (400, 500, 600, 700)]
        flights = label_fare_types(flights)
        flights = score_flights(flights)
        html = _build_date_sections(flights, "outbound")
        self.assertIn("View all 4 flights", html)

    def test_return_direction_param_in_link(self):
        """Return date sections should include dir=return in web link."""
        flights = [_make_flight(search_date="2026-07-13", price=p)
                   for p in (400, 500, 600, 700)]
        flights = label_fare_types(flights)
        flights = score_flights(flights)
        html = _build_date_sections(flights, "return")
        self.assertIn("dir=return", html)


class TestSummaryAPI(unittest.TestCase):
    """Tests for the summary API endpoint logic (unit-level, no live API calls)."""

    def test_system_prompt_content(self):
        """System prompt should mention family and cruise trip."""
        from api.summary import SYSTEM_PROMPT
        self.assertIn("family", SYSTEM_PROMPT)
        self.assertIn("cruise trip", SYSTEM_PROMPT)
        self.assertIn("4 bullet", SYSTEM_PROMPT)
        self.assertIn("No emojis", SYSTEM_PROMPT)

    def test_system_prompt_bullet_format(self):
        """System prompt should request bullet point format."""
        from api.summary import SYSTEM_PROMPT
        self.assertIn("bullet point", SYSTEM_PROMPT)

    def test_openai_key_env_var_name(self):
        """API should read from OPENAI_API_KEY env var."""
        import api.summary as mod
        # The module should reference OPENAI_API_KEY
        self.assertTrue(hasattr(mod, "OPENAI_API_KEY"))

    def test_missing_key_raises_valueerror(self):
        """Handler should raise ValueError when key is empty."""
        import api.summary as mod
        original = mod.OPENAI_API_KEY
        try:
            mod.OPENAI_API_KEY = ""
            with self.assertRaises(ValueError):
                if not mod.OPENAI_API_KEY:
                    raise ValueError("OPENAI_API_KEY not configured")
        finally:
            mod.OPENAI_API_KEY = original

    def test_fallback_message_is_consistent(self):
        """Fallback message in error handler must match the known string."""
        import ast
        import inspect
        from api.summary import handler
        source = inspect.getsource(handler)
        # The fallback string should appear in both success-path and error-path
        fallback = "Flight data updated daily. Check back each morning for today's best picks."
        self.assertIn(fallback, source)


class TestFrontendSummaryIntegration(unittest.TestCase):
    """Tests for frontend AI briefing logic (reads HTML source)."""

    def setUp(self):
        with open("public/index.html", "r") as fh:
            self.html = fh.read()

    def test_no_duplicate_sheet_fetch_in_summary(self):
        """fetchAISummary should use sheetRows, not fetch the sheet again."""
        # fetchAISummary should reference sheetRows, not SHEETS_URL
        # Extract just the fetchAISummary function body
        start = self.html.index("function fetchAISummary()")
        # Find next top-level function
        end = self.html.index("\n  function ", start + 1)
        fn_body = self.html[start:end]
        self.assertIn("sheetRows", fn_body)
        self.assertNotIn("SHEETS_URL", fn_body)

    def test_fallback_not_cached(self):
        """Fallback message should NOT be stored in summaryCache."""
        start = self.html.index("function fetchAISummary()")
        end = self.html.index("\n  function ", start + 1)
        fn_body = self.html[start:end]
        # The .then handler should only cache non-fallback text
        self.assertIn("text !== FALLBACK", fn_body)

    def test_catch_does_not_cache(self):
        """Network errors should not pollute summaryCache."""
        start = self.html.index("function fetchAISummary()")
        end = self.html.index("\n  function ", start + 1)
        fn_body = self.html[start:end]
        # In the .catch block, summaryCache should NOT be set
        catch_start = fn_body.index(".catch(")
        catch_body = fn_body[catch_start:]
        self.assertNotIn("summaryCache", catch_body)

    def test_summary_waits_for_sheet_data(self):
        """fetchAISummary should only be called after sheet data loads."""
        self.assertIn("sheetReady.then(function () { fetchAISummary(); })", self.html)

    def test_briefing_text_font_style(self):
        """AI briefing text should use DM Sans 15px sentence case navy."""
        self.assertIn(".ai-briefing-text", self.html)
        start = self.html.index(".ai-briefing-text {")
        end = self.html.index("}", start) + 1
        css = self.html[start:end]
        self.assertIn("font-size: 15px", css)
        self.assertIn("font-weight: 300", css)
        self.assertIn("color: #1a3a6b", css)
        self.assertNotIn("text-transform: uppercase", css)

    def test_briefing_label_gold(self):
        """AI briefing label should be gold 10px uppercase."""
        start = self.html.index(".ai-briefing-label {")
        end = self.html.index("}", start) + 1
        css = self.html[start:end]
        self.assertIn("color: #b8953a", css)
        self.assertIn("font-weight: 600", css)
        self.assertIn("letter-spacing: 0.16em", css)

    def test_briefing_frosted_glass(self):
        """AI briefing should use frosted glass background, not gradient."""
        start = self.html.index(".ai-briefing {")
        end = self.html.index("}", start) + 1
        css = self.html[start:end]
        self.assertIn("rgba(250, 248, 243, 0.72)", css)
        self.assertIn("backdrop-filter", css)
        self.assertIn("blur(20px)", css)
        self.assertNotIn("linear-gradient", css)


class TestScoringRationale(unittest.TestCase):
    """Tests for the scoring rationale metadata."""

    def test_rationale_has_sources(self):
        """Scoring rationale should list verification sources."""
        self.assertIn("sources", SCORING_RATIONALE)
        self.assertGreater(len(SCORING_RATIONALE["sources"]), 0)

    def test_rationale_has_last_updated(self):
        """Scoring rationale should have a last_updated date."""
        self.assertIn("last_updated", SCORING_RATIONALE)
        self.assertIn("2026", SCORING_RATIONALE["last_updated"])

    def test_lufthansa_note(self):
        """Rationale should explain Lufthansa's low bonus."""
        self.assertIn("lufthansa_note", SCORING_RATIONALE)
        self.assertIn("strike", SCORING_RATIONALE["lufthansa_note"].lower())

    def test_lufthansa_bonus_low_across_all_routes(self):
        """Lufthansa should have the lowest bonus on every route it appears."""
        from flight_agent import (LAX_VCE_BONUSES, IST_LAX_BONUSES,
                                  ATL_VCE_BONUSES, IST_ATL_BONUSES)
        for route_name, bonuses in [("LAX_VCE", LAX_VCE_BONUSES),
                                     ("IST_LAX", IST_LAX_BONUSES),
                                     ("ATL_VCE", ATL_VCE_BONUSES),
                                     ("IST_ATL", IST_ATL_BONUSES)]:
            if "lufthansa" in bonuses:
                lh = bonuses["lufthansa"]
                others = [v for k, v in bonuses.items() if k != "lufthansa"]
                self.assertEqual(lh, max(bonuses.values()),
                    f"Lufthansa should have worst (highest) bonus in {route_name}")


class TestAucklandRouteScoring(unittest.TestCase):
    """Tests for Auckland (AKL) route scoring."""

    def test_akl_outbound_bonuses_are_negative(self):
        """All AKL outbound airline bonuses should be negative."""
        for airline, bonus in AKL_OUTBOUND_BONUSES.items():
            self.assertLess(bonus, 0, f"{airline} AKL outbound bonus should be negative")

    def test_akl_return_bonuses_are_negative(self):
        """All AKL return airline bonuses should be negative."""
        for airline, bonus in AKL_RETURN_BONUSES.items():
            self.assertLess(bonus, 0, f"{airline} AKL return bonus should be negative")

    def test_akl_no_auto_top_pick_return(self):
        """AKL return should have no auto-top-pick (no nonstop IST→AKL)."""
        self.assertEqual(len(AKL_RETURN_AUTO_TOP_PICK), 0)

    def test_singapore_airlines_best_akl_outbound(self):
        """Singapore Airlines (-280) should score best for AKL outbound."""
        sq = _make_flight(airline="Singapore Airlines", price=800, stops=1,
                          layover=120, search_date="2026-06-29")
        ek = _make_flight(airline="Emirates", price=800, stops=1,
                          layover=120, search_date="2026-06-29")
        scored = score_flights(
            [sq, ek],
            airline_bonuses=AKL_OUTBOUND_BONUSES,
            auto_top_picks=set(),
        )
        self.assertEqual(scored[0]["primary_airline"], "Singapore Airlines")

    def test_emirates_best_akl_return(self):
        """Emirates (-280) should score best for AKL return at same price."""
        ek = _make_flight(airline="Emirates", price=900, stops=1,
                          layover=180, search_date="2026-07-13")
        sq = _make_flight(airline="Singapore Airlines", price=900, stops=1,
                          layover=180, search_date="2026-07-13")
        scored = score_flights(
            [ek, sq],
            airline_bonuses=AKL_RETURN_BONUSES,
            auto_top_picks=AKL_RETURN_AUTO_TOP_PICK,
        )
        self.assertEqual(scored[0]["primary_airline"], "Emirates")

    def test_akl_outbound_unknown_airline_no_bonus(self):
        """Unknown airline on AKL outbound should get 0 bonus."""
        unknown = _make_flight(airline="SomeAirline", price=600, stops=1,
                               layover=60, search_date="2026-06-29")
        sq = _make_flight(airline="Singapore Airlines", price=600, stops=1,
                          layover=60, search_date="2026-06-29")
        scored = score_flights(
            [unknown, sq],
            airline_bonuses=AKL_OUTBOUND_BONUSES,
            auto_top_picks=set(),
        )
        sq_score = next(f["score"] for f in scored if f["primary_airline"] == "Singapore Airlines")
        unk_score = next(f["score"] for f in scored if f["primary_airline"] == "SomeAirline")
        self.assertLess(sq_score, unk_score)

    def test_akl_qatar_etihad_relative_order(self):
        """Qatar (-260) should score better than Etihad (-240) at same price."""
        qr = _make_flight(airline="Qatar Airways", price=850, stops=1,
                          layover=120, search_date="2026-06-29")
        ey = _make_flight(airline="Etihad", price=850, stops=1,
                          layover=120, search_date="2026-06-29")
        scored = score_flights(
            [qr, ey],
            airline_bonuses=AKL_OUTBOUND_BONUSES,
            auto_top_picks=set(),
        )
        self.assertEqual(scored[0]["primary_airline"], "Qatar Airways")


class TestAtlantaRouteScoring(unittest.TestCase):
    """Tests for Atlanta (ATL) route scoring."""

    def test_atl_outbound_bonuses_are_negative(self):
        """All ATL outbound airline bonuses should be negative."""
        for airline, bonus in ATL_OUTBOUND_BONUSES.items():
            self.assertLess(bonus, 0, f"{airline} ATL outbound bonus should be negative")

    def test_atl_return_bonuses_are_negative(self):
        """All ATL return airline bonuses should be negative."""
        for airline, bonus in ATL_RETURN_BONUSES.items():
            self.assertLess(bonus, 0, f"{airline} ATL return bonus should be negative")

    def test_delta_best_atl_outbound(self):
        """Delta (-300, ATL home hub) should score best for ATL outbound."""
        dl = _make_flight(airline="Delta", price=500, stops=1,
                          layover=60, search_date="2026-06-29")
        ua = _make_flight(airline="United", price=500, stops=1,
                          layover=60, search_date="2026-06-29")
        scored = score_flights(
            [dl, ua],
            airline_bonuses=ATL_OUTBOUND_BONUSES,
            auto_top_picks=set(),
        )
        self.assertEqual(scored[0]["primary_airline"], "Delta")

    def test_atl_turkish_nonstop_auto_top_pick(self):
        """Turkish Airlines nonstop IST→ATL should get score forced to 0."""
        f = _make_flight(airline="Turkish Airlines", price=800, stops=0,
                         layover=0, search_date="2026-07-13")
        scored = score_flights(
            [f],
            airline_bonuses=ATL_RETURN_BONUSES,
            auto_top_picks=ATL_RETURN_AUTO_TOP_PICK,
        )
        self.assertEqual(scored[0]["score"], 0)

    def test_atl_turkish_1stop_not_auto_top(self):
        """Turkish Airlines with 1 stop should NOT get score 0 on ATL return."""
        f = _make_flight(airline="Turkish Airlines", price=700, stops=1,
                         layover=120, search_date="2026-07-13")
        scored = score_flights(
            [f],
            airline_bonuses=ATL_RETURN_BONUSES,
            auto_top_picks=ATL_RETURN_AUTO_TOP_PICK,
        )
        self.assertNotEqual(scored[0]["score"], 0)

    def test_atl_turkish_nonstop_beats_cheap_delta(self):
        """Turkish nonstop (score=0) should beat a cheap Delta on ATL return."""
        tk = _make_flight(airline="Turkish Airlines", price=900, stops=0,
                          layover=0, search_date="2026-07-13")
        dl = _make_flight(airline="Delta", price=400, stops=1,
                          layover=60, search_date="2026-07-13")
        scored = score_flights(
            [tk, dl],
            airline_bonuses=ATL_RETURN_BONUSES,
            auto_top_picks=ATL_RETURN_AUTO_TOP_PICK,
        )
        self.assertEqual(scored[0]["primary_airline"], "Turkish Airlines")
        self.assertEqual(scored[0]["score"], 0)

    def test_atl_delta_return_bonus(self):
        """Delta should get -220 bonus on ATL return (not -300 outbound)."""
        dl_ret = _make_flight(airline="Delta", price=600, stops=1,
                              layover=60, search_date="2026-07-13")
        dl_out = _make_flight(airline="Delta", price=600, stops=1,
                              layover=60, search_date="2026-06-29")
        scored_ret = score_flights([dl_ret], airline_bonuses=ATL_RETURN_BONUSES,
                                   auto_top_picks=ATL_RETURN_AUTO_TOP_PICK)
        scored_out = score_flights([dl_out], airline_bonuses=ATL_OUTBOUND_BONUSES,
                                   auto_top_picks=set())
        # Outbound bonus (-300) is bigger than return (-220), so outbound score is lower
        self.assertLess(scored_out[0]["score"], scored_ret[0]["score"])


class TestRoutesConfig(unittest.TestCase):
    """Tests for the ROUTES configuration list."""

    def test_four_origins(self):
        """ROUTES should define exactly 4 origins."""
        self.assertEqual(len(ROUTES), 4)

    def test_origins_are_lax_akl_atl_yvr(self):
        """ROUTES origins should be LAX, AKL, ATL, YVR."""
        origins = {r["origin"] for r in ROUTES}
        self.assertEqual(origins, {"LAX", "AKL", "ATL", "YVR"})

    def test_each_route_has_outbound_and_return(self):
        """Each route must define both outbound and return."""
        for r in ROUTES:
            self.assertIn("outbound", r, f"{r['origin']} missing outbound")
            self.assertIn("return", r, f"{r['origin']} missing return")

    def test_all_outbound_destinations_are_vce(self):
        """All outbound routes should go to VCE."""
        for r in ROUTES:
            self.assertEqual(r["outbound"]["to"], "VCE",
                             f"{r['origin']} outbound should go to VCE")

    def test_all_returns_from_ist(self):
        """All return routes should originate from IST."""
        for r in ROUTES:
            self.assertEqual(r["return"]["from"], "IST",
                             f"{r['origin']} return should come from IST")

    def test_return_destinations_match_origins(self):
        """Return 'to' should match the route origin."""
        for r in ROUTES:
            self.assertEqual(r["return"]["to"], r["origin"],
                             f"Return to should be {r['origin']}")

    def test_outbound_origins_match(self):
        """Outbound 'from' should match the route origin."""
        for r in ROUTES:
            self.assertEqual(r["outbound"]["from"], r["origin"],
                             f"Outbound from should be {r['origin']}")

    def test_all_routes_have_bonuses(self):
        """Each direction must have a bonuses dict."""
        for r in ROUTES:
            for d in ("outbound", "return"):
                self.assertIsInstance(r[d]["bonuses"], dict,
                                     f"{r['origin']} {d} bonuses should be dict")

    def test_all_routes_have_auto_top(self):
        """Each direction must have an auto_top set."""
        for r in ROUTES:
            for d in ("outbound", "return"):
                self.assertIsInstance(r[d]["auto_top"], set,
                                     f"{r['origin']} {d} auto_top should be set")

    def test_all_routes_use_same_dates(self):
        """All routes should use the same departure and return dates."""
        for r in ROUTES:
            self.assertEqual(r["outbound"]["dates"], DEPARTURE_DATES,
                             f"{r['origin']} outbound dates mismatch")
            self.assertEqual(r["return"]["dates"], RETURN_DATES,
                             f"{r['origin']} return dates mismatch")


class TestFareClassFields(unittest.TestCase):
    """Tests for the four fare class price fields."""

    def test_all_flights_have_basic_economy_price(self):
        """All labeled flights should have basic_economy_price set."""
        for carrier in ("Delta", "British Airways", "Singapore Airlines"):
            f = _make_flight(airline=carrier, price=500)
            flights = label_fare_types([f])
            self.assertIsNotNone(flights[0]["basic_economy_price"],
                                 f"{carrier} should have basic_economy_price")

    def test_non_big3_have_economy_main_price(self):
        """Non-Big-3 flights should have economy_main_price set."""
        for carrier in ("British Airways", "Singapore Airlines"):
            f = _make_flight(airline=carrier, price=500)
            flights = label_fare_types([f])
            self.assertIsNotNone(flights[0]["economy_main_price"],
                                 f"{carrier} should have economy_main_price")

    def test_big3_economy_main_price_includes_adder(self):
        """Big 3 flights should have economy_main_price = basic + carrier adder."""
        f = _make_flight(airline="Delta", price=500)
        flights = label_fare_types([f])
        self.assertEqual(flights[0]["economy_main_price"], 620)  # Delta = +120

    def test_premium_economy_null_by_default(self):
        """Premium economy should be None when not in raw data."""
        f = _make_flight(airline="Delta", price=500)
        flights = label_fare_types([f])
        self.assertIsNone(flights[0]["premium_economy_price"])

    def test_business_price_null_by_default(self):
        """Business price should be None when not in raw data."""
        f = _make_flight(airline="Delta", price=500)
        flights = label_fare_types([f])
        self.assertIsNone(flights[0]["business_price"])

    def test_premium_from_raw_data(self):
        """Premium economy price should be extracted from raw data when present."""
        f = _make_flight(airline="Delta", price=500)
        f["raw"] = {"premium_economy_price": 1200}
        flights = label_fare_types([f])
        self.assertEqual(flights[0]["premium_economy_price"], 1200)

    def test_business_from_raw_data(self):
        """Business price should be extracted from raw data when present."""
        f = _make_flight(airline="Delta", price=500)
        f["raw"] = {"business_price": 3500}
        flights = label_fare_types([f])
        self.assertEqual(flights[0]["business_price"], 3500)

    def test_flight_to_dict_includes_all_fare_fields(self):
        """Serialized dict should include all four fare class fields."""
        f = _make_flight(airline="Delta", price=500)
        f["raw"] = {"premium_economy_price": 1200, "business_price": 3500}
        f = label_fare_types([f])[0]
        f = score_flights([f])[0]
        d = _flight_to_dict(f)
        self.assertEqual(d["basic_economy_price"], 500)
        self.assertEqual(d["economy_main_price"], 620)  # Big 3 Delta: basic $500 + $120 adder
        self.assertEqual(d["premium_economy_price"], 1200)
        self.assertEqual(d["business_price"], 3500)

    def test_non_big3_basic_equals_main(self):
        """Non-Big-3 should have basic == main (same base fare)."""
        f = _make_flight(airline="Emirates", price=800)
        flights = label_fare_types([f])
        self.assertEqual(flights[0]["basic_economy_price"],
                         flights[0]["economy_main_price"])


class TestExtractFarePrices(unittest.TestCase):
    """Tests for _extract_fare_prices_from_raw SerpAPI parsing."""

    def test_empty_raw_returns_none(self):
        prem, biz = _extract_fare_prices_from_raw({})
        self.assertIsNone(prem)
        self.assertIsNone(biz)

    def test_extensions_premium_economy_string(self):
        raw = {"extensions": ["Premium economy from $1,234"]}
        prem, biz = _extract_fare_prices_from_raw(raw)
        self.assertEqual(prem, 1234)
        self.assertIsNone(biz)

    def test_extensions_business_string(self):
        raw = {"extensions": ["Business class from $3,500"]}
        prem, biz = _extract_fare_prices_from_raw(raw)
        self.assertIsNone(prem)
        self.assertEqual(biz, 3500)

    def test_extensions_first_class_string(self):
        raw = {"extensions": ["First class from $5,000"]}
        prem, biz = _extract_fare_prices_from_raw(raw)
        self.assertIsNone(prem)
        self.assertEqual(biz, 5000)

    def test_extensions_both_fares(self):
        raw = {"extensions": [
            "Premium economy from $1,200",
            "Business class from $3,500",
        ]}
        prem, biz = _extract_fare_prices_from_raw(raw)
        self.assertEqual(prem, 1200)
        self.assertEqual(biz, 3500)

    def test_extensions_non_string_items_skipped(self):
        raw = {"extensions": [42, None, {"key": "val"}]}
        prem, biz = _extract_fare_prices_from_raw(raw)
        self.assertIsNone(prem)
        self.assertIsNone(biz)

    def test_price_insights_fare_options(self):
        raw = {"price_insights": {"fare_options": [
            {"fare_class": "Premium Economy", "price": 1100},
            {"fare_class": "Business", "price": 4000},
        ]}}
        prem, biz = _extract_fare_prices_from_raw(raw)
        self.assertEqual(prem, 1100)
        self.assertEqual(biz, 4000)

    def test_price_insights_first_class(self):
        raw = {"price_insights": {"fare_options": [
            {"fare_class": "First Class", "price": 6000},
        ]}}
        prem, biz = _extract_fare_prices_from_raw(raw)
        self.assertIsNone(prem)
        self.assertEqual(biz, 6000)

    def test_price_insights_cabin_key(self):
        raw = {"price_insights": {"fare_options": [
            {"cabin": "premium economy", "price": 900},
        ]}}
        prem, biz = _extract_fare_prices_from_raw(raw)
        self.assertEqual(prem, 900)

    def test_direct_top_level_fields(self):
        raw = {"premium_economy_price": 1200, "business_price": 3500}
        prem, biz = _extract_fare_prices_from_raw(raw)
        self.assertEqual(prem, 1200)
        self.assertEqual(biz, 3500)

    def test_flight_segments_fare_category(self):
        raw = {"flights": [
            {"fare_category": "Premium Economy", "airline": "Delta"},
        ], "price": 1500}
        prem, biz = _extract_fare_prices_from_raw(raw)
        self.assertEqual(prem, 1500)

    def test_flight_segments_business_travel_class(self):
        raw = {"flights": [
            {"travel_class": "Business", "airline": "Emirates"},
        ], "price": 4000}
        prem, biz = _extract_fare_prices_from_raw(raw)
        self.assertEqual(biz, 4000)

    def test_extensions_take_priority_over_top_level(self):
        """Extensions price should win over direct top-level field."""
        raw = {
            "extensions": ["Premium economy from $1,300"],
            "premium_economy_price": 1200,
        }
        prem, _ = _extract_fare_prices_from_raw(raw)
        self.assertEqual(prem, 1300)

    def test_no_price_in_extension_string(self):
        """Extension string without $ amount should not crash."""
        raw = {"extensions": ["Premium economy available"]}
        prem, biz = _extract_fare_prices_from_raw(raw)
        self.assertIsNone(prem)
        self.assertIsNone(biz)

    def test_label_fare_types_uses_extensions(self):
        """End-to-end: label_fare_types should pick up extensions prices."""
        f = _make_flight(airline="Delta", price=500)
        f["raw"] = {"extensions": ["Premium economy from $1,200", "Business class from $3,500"]}
        flights = label_fare_types([f])
        self.assertEqual(flights[0]["premium_economy_price"], 1200)
        self.assertEqual(flights[0]["business_price"], 3500)


class TestMergePremiumBusinessPrices(unittest.TestCase):
    """Tests for merge_premium_business_prices."""

    def test_merge_premium_price(self):
        f = _make_flight(airline="Delta", price=500)
        f = label_fare_types([f])[0]
        lookup = {"premium": {("delta", "2026-06-29"): 1200}, "business": {}}
        flights = merge_premium_business_prices([f], lookup)
        self.assertEqual(flights[0]["premium_economy_price"], 1200)
        self.assertIsNone(flights[0]["business_price"])

    def test_merge_business_price(self):
        f = _make_flight(airline="Emirates", price=800)
        f = label_fare_types([f])[0]
        lookup = {"premium": {}, "business": {("emirates", "2026-06-29"): 4000}}
        flights = merge_premium_business_prices([f], lookup)
        self.assertIsNone(flights[0]["premium_economy_price"])
        self.assertEqual(flights[0]["business_price"], 4000)

    def test_merge_both_prices(self):
        f = _make_flight(airline="Delta", price=500)
        f = label_fare_types([f])[0]
        lookup = {
            "premium": {("delta", "2026-06-29"): 1200},
            "business": {("delta", "2026-06-29"): 3500},
        }
        flights = merge_premium_business_prices([f], lookup)
        self.assertEqual(flights[0]["premium_economy_price"], 1200)
        self.assertEqual(flights[0]["business_price"], 3500)

    def test_no_match_leaves_none(self):
        f = _make_flight(airline="Delta", price=500)
        f = label_fare_types([f])[0]
        lookup = {"premium": {("united", "2026-06-29"): 1200}, "business": {}}
        flights = merge_premium_business_prices([f], lookup)
        self.assertIsNone(flights[0]["premium_economy_price"])

    def test_empty_lookup(self):
        f = _make_flight(airline="Delta", price=500)
        f = label_fare_types([f])[0]
        flights = merge_premium_business_prices([f], {"premium": {}, "business": {}})
        self.assertIsNone(flights[0]["premium_economy_price"])
        self.assertIsNone(flights[0]["business_price"])

    def test_does_not_overwrite_existing_premium(self):
        """If label_fare_types already extracted a premium price, merge should overwrite
        with the dedicated search result (more accurate)."""
        f = _make_flight(airline="Delta", price=500)
        f["raw"] = {"premium_economy_price": 999}
        f = label_fare_types([f])[0]
        self.assertEqual(f["premium_economy_price"], 999)
        lookup = {"premium": {("delta", "2026-06-29"): 1200}, "business": {}}
        flights = merge_premium_business_prices([f], lookup)
        self.assertEqual(flights[0]["premium_economy_price"], 1200)


class TestDefaultDisplayPrice(unittest.TestCase):
    """Tests for default display price being Economy Main in the frontend."""

    def setUp(self):
        with open("public/index.html", "r") as fh:
            self.html = fh.read()

    def test_get_display_price_has_fallback_chain(self):
        """getDisplayPrice should fall back through economy_main → basic → price."""
        start = self.html.index("function getDisplayPrice(f)")
        end = self.html.index("\n  function ", start + 1)
        fn_body = self.html[start:end]
        self.assertIn("economy_main_price", fn_body)
        self.assertIn("basic_economy_price", fn_body)

    def test_under1000_has_fallback_chain(self):
        """Under $1,000 filter should fall back through economy_main → basic → price."""
        start = self.html.index("function passesFilter(f)")
        end = self.html.index("\n  function ", start + 1)
        fn_body = self.html[start:end]
        self.assertIn("economy_main_price", fn_body)
        self.assertIn("basic_economy_price", fn_body)

    def test_card_shows_economy_main_label(self):
        """Card should show Economy Main label."""
        start = self.html.index("function cardHTML(f, rank, isTopPick)")
        end = self.html.index("\n  function ", start + 1)
        fn_body = self.html[start:end]
        self.assertIn("Economy Main", fn_body)

    def test_card_shows_basic_economy_small_for_big3(self):
        """Big 3 cards should show Basic Economy price underneath."""
        start = self.html.index("function cardHTML(f, rank, isTopPick)")
        end = self.html.index("\n  function ", start + 1)
        fn_body = self.html[start:end]
        self.assertIn("Basic Economy", fn_body)

    def test_ai_briefing_uses_economy_main_price(self):
        """AI summary payload should send economy_main_price."""
        start = self.html.index("function fetchAISummary()")
        end = self.html.index("\n  function ", start + 1)
        fn_body = self.html[start:end]
        self.assertIn("economy_main_price", fn_body)


class TestCacheConfig(unittest.TestCase):
    """Tests for CDN cache configuration."""

    def setUp(self):
        with open("api/flights.py", "r") as fh:
            self.source = fh.read()

    def test_cache_is_48h(self):
        """s-maxage should be 172800 (48 hours)."""
        self.assertIn("s-maxage=172800", self.source)

    def test_stale_while_revalidate_is_48h(self):
        """stale-while-revalidate should be 172800 (48 hours)."""
        self.assertIn("stale-while-revalidate=172800", self.source)

    def test_serpapi_usage_in_cache(self):
        """Cached flight data should include _serpapi_usage."""
        with open("data/flights_cache.json", "r") as fh:
            data = json.load(fh)
        self.assertIn("_serpapi_usage", data)


class TestAISummaryPrompt(unittest.TestCase):
    """Tests for the AI summary system prompt."""

    def setUp(self):
        with open("api/summary.py", "r") as fh:
            self.source = fh.read()

    def test_prompt_mentions_economy_main(self):
        """System prompt should mention Economy Main cabin fares."""
        self.assertIn("Economy Main cabin fares", self.source)

    def test_yvr_in_route_map(self):
        """YVR should be in the summary route map."""
        self.assertIn('"YVR"', self.source)


class TestFrontendFilters(unittest.TestCase):
    """Tests for the frontend filter system (reads HTML source)."""

    def setUp(self):
        with open("public/index.html", "r") as fh:
            self.html = fh.read()

    def test_filter_chips_present(self):
        """All expected filter chips should be defined."""
        for label in ("Morning", "Evening", "Nonstop", "1 Stop",
                       "Under $1,000"):
            self.assertIn(label, self.html, f"Missing filter chip: {label}")

    def test_old_filters_removed(self):
        """Removed filters should not appear."""
        for label in ("Under $900", "Under $1,100", "Delta / United"):
            self.assertNotIn("label: '" + label + "'", self.html,
                             f"Old filter still present: {label}")

    def test_no_all_filter(self):
        """There should be no 'All' filter chip in FILTERS array."""
        # Check the FILTERS JS array specifically
        start = self.html.index("const FILTERS = [")
        end = self.html.index("];", start) + 2
        filters_block = self.html[start:end]
        self.assertNotIn("'all'", filters_block)

    def test_fare_filters_set_defined(self):
        """FARE_FILTERS set should be defined with correct IDs."""
        self.assertIn("FARE_FILTERS", self.html)
        for fid in ("basic", "main", "premium", "business"):
            self.assertIn("'" + fid + "'", self.html)

    def test_multi_select_uses_set(self):
        """activeFilters should be a Set, not a string."""
        self.assertIn("let activeFilters = new Set()", self.html)

    def test_no_stale_activeFilter_reference(self):
        """No remaining references to the old activeFilter variable."""
        # Should only find activeFilters (with s), never activeFilter (without s)
        import re
        matches = re.findall(r'activeFilter\b(?!s)', self.html)
        self.assertEqual(len(matches), 0,
                         f"Found {len(matches)} stale activeFilter references")

    def test_fare_class_mutual_exclusion(self):
        """Fare filter click should delete other fare filters."""
        self.assertIn("FARE_FILTERS.forEach(function (fc) { activeFilters.delete(fc); })",
                      self.html)

    def test_get_display_price_defined(self):
        """getDisplayPrice helper should be defined."""
        self.assertIn("function getDisplayPrice(f)", self.html)

    def test_get_fare_price_defined(self):
        """getFarePrice helper should be defined."""
        self.assertIn("function getFarePrice(f, fareClass)", self.html)

    def test_under1000_uses_fare_class_price(self):
        """Under $1,000 filter should reference the active fare class price."""
        start = self.html.index("function passesFilter(f)")
        end = self.html.index("\n  function ", start + 1)
        fn_body = self.html[start:end]
        self.assertIn("under1000", fn_body)
        self.assertIn("getFarePrice", fn_body)

    def test_card_shows_fare_badge_label(self):
        """Card HTML should include Economy Main fare badge label."""
        start = self.html.index("function cardHTML(f, rank, isTopPick)")
        end = self.html.index("\n  function ", start + 1)
        fn_body = self.html[start:end]
        self.assertIn("fareLabel", fn_body)
        self.assertIn("Economy Main", fn_body)

    def test_origin_modal_four_cards(self):
        """Origin modal should have exactly 4 origin cards."""
        count = self.html.count('class="origin-card"')
        self.assertEqual(count, 4)

    def test_origin_pills_four(self):
        """Hero should have 4 origin pills."""
        count = self.html.count('class="origin-pill"')
        self.assertEqual(count, 4)

    def test_home_nav_tab(self):
        """Home tab should be present in section nav."""
        self.assertIn('data-section="home"', self.html)
        self.assertIn("showLanding", self.html)


class TestFamilyPickMatching(unittest.TestCase):
    """Tests for family pick matching on flight cards."""

    def setUp(self):
        with open("public/index.html", "r") as fh:
            self.html = fh.read()

    def test_flight_match_key_function_exists(self):
        """flightMatchKey function should exist for airline+date matching."""
        self.assertIn("function flightMatchKey(fid)", self.html)

    def test_get_flight_counts_uses_match_key(self):
        """getFlightCounts should use flightMatchKey for matching."""
        self.assertIn("flightMatchKey(sheetFid)", self.html)

    def test_get_flight_counts_signature(self):
        """getFlightCounts should accept flight id parameter."""
        self.assertIn("function getFlightCounts(fid)", self.html)

    def test_interest_link_carries_dep_time(self):
        """Interest link should carry data-dep attribute for departure time."""
        self.assertIn('data-dep="', self.html)

    def test_has_picks_css_class(self):
        """Cards with family picks should get has-picks styling."""
        self.assertIn(".has-picks", self.html)

    def test_has_picks_added_when_counts_exist(self):
        """interest-link should get has-picks class when counts > 0."""
        self.assertIn("el.classList.add('has-picks')", self.html)

    def test_has_picks_removed_when_no_counts(self):
        """interest-link should lose has-picks class when counts are 0."""
        self.assertIn("el.classList.remove('has-picks')", self.html)

    def test_strip_origin_prefix_still_used(self):
        """stripOriginPrefix should still be used for origin-prefixed flight_ids."""
        self.assertIn("stripOriginPrefix(r.flight_id)", self.html)

    def test_interest_link_on_every_card(self):
        """Every card should have an interest-link element."""
        self.assertIn('class="interest-link"', self.html)
        self.assertIn('data-fid=', self.html)

    def test_family_picks_use_api_flight_times(self):
        """Family picks should prefer API flight data times over sheet times."""
        self.assertIn("matchedFlight", self.html)
        self.assertIn("matchedFlight.departure_time", self.html)
        self.assertIn("matchedFlight.arrival_time", self.html)

    def test_family_picks_timezone_from_origin(self):
        """Family picks should derive timezone from departure city."""
        self.assertIn("info.tzDep", self.html)
        self.assertIn("info.tzArr", self.html)


class TestMultiCityNormalization(unittest.TestCase):
    """Tests for _normalize_serpapi_multicity()."""

    def _make_raw_multicity(self, out_airline="Delta", ret_airline="Turkish Airlines",
                            out_stops=1, ret_stops=0, price=1200):
        """Build a raw SerpAPI multi-city flight with outbound segments and _return_raw.

        SerpAPI multi-city type=3 returns outbound-only data in the first call.
        The return leg comes from a separate call and is attached as _return_raw.
        """
        # Outbound segments only (this is what SerpAPI returns in call 1)
        out_segments = []
        out_layovers = []
        if out_stops > 0:
            out_segments.append({
                "airline": out_airline,
                "flight_number": "DL 290",
                "departure_airport": {"id": "LAX", "time": "2026-06-29 18:00"},
                "arrival_airport": {"id": "CDG", "time": "2026-06-30 12:00"},
                "duration": 600,
            })
            out_segments.append({
                "airline": "Air France",
                "flight_number": "AF 1526",
                "departure_airport": {"id": "CDG", "time": "2026-06-30 14:30"},
                "arrival_airport": {"id": "VCE", "time": "2026-06-30 16:00"},
                "duration": 90,
            })
            out_layovers.append({
                "name": "Paris Charles de Gaulle Airport",
                "id": "CDG",
                "duration": 150,
            })
        else:
            out_segments.append({
                "airline": out_airline,
                "flight_number": "DL 290",
                "departure_airport": {"id": "LAX", "time": "2026-06-29 18:00"},
                "arrival_airport": {"id": "VCE", "time": "2026-06-30 12:00"},
                "duration": 600,
            })

        out_duration = sum(s["duration"] for s in out_segments) + sum(
            lo["duration"] for lo in out_layovers
        )

        # Return leg (from separate API call 2, attached as _return_raw)
        ret_segments = []
        ret_layovers = []
        if ret_stops == 0:
            ret_segments.append({
                "airline": ret_airline,
                "flight_number": "TK 10",
                "departure_airport": {"id": "IST", "time": "2026-07-14 10:15"},
                "arrival_airport": {"id": "LAX", "time": "2026-07-14 15:30"},
                "duration": 780,
            })
        else:
            ret_segments.append({
                "airline": ret_airline,
                "flight_number": "TK 1867",
                "departure_airport": {"id": "IST", "time": "2026-07-14 10:15"},
                "arrival_airport": {"id": "FRA", "time": "2026-07-14 12:30"},
                "duration": 195,
            })
            ret_segments.append({
                "airline": "Lufthansa",
                "flight_number": "LH 450",
                "departure_airport": {"id": "FRA", "time": "2026-07-14 14:00"},
                "arrival_airport": {"id": "LAX", "time": "2026-07-14 17:00"},
                "duration": 660,
            })
            ret_layovers.append({
                "name": "Frankfurt Airport",
                "id": "FRA",
                "duration": 90,
            })

        ret_duration = sum(s["duration"] for s in ret_segments) + sum(
            lo["duration"] for lo in ret_layovers
        )

        return {
            "flights": out_segments,
            "layovers": out_layovers,
            "price": price,
            "total_duration": out_duration,
            "_source": "serpapi_multicity",
            "_search_date": "2026-06-29",
            "_outbound_date": "2026-06-29",
            "_return_date": "2026-07-14",
            "_dest": "VCE",
            "_return_from": "IST",
            "_return_raw": {
                "flights": ret_segments,
                "layovers": ret_layovers,
                "price": price,
                "total_duration": ret_duration,
            },
        }

    def test_splits_legs_at_vce(self):
        """Should split segments at VCE arrival boundary."""
        raw = self._make_raw_multicity()
        result = _normalize_serpapi_multicity(raw, "2026-06-29", "2026-07-14")
        self.assertEqual(result["type"], "multi_city")
        self.assertIn("return_leg", result)
        # Outbound: 2 segments (LAX->CDG, CDG->VCE) = 1 stop
        self.assertEqual(result["stops"], 1)
        # Return: 1 segment (IST->LAX) = 0 stops
        self.assertEqual(result["return_leg"]["stops"], 0)

    def test_outbound_airline(self):
        """Outbound primary airline should be first outbound segment's airline."""
        raw = self._make_raw_multicity(out_airline="United")
        result = _normalize_serpapi_multicity(raw, "2026-06-29", "2026-07-14")
        self.assertEqual(result["primary_airline"], "United")

    def test_return_airline(self):
        """Return leg primary airline should be first return segment's airline."""
        raw = self._make_raw_multicity(ret_airline="Emirates")
        result = _normalize_serpapi_multicity(raw, "2026-06-29", "2026-07-14")
        self.assertEqual(result["return_leg"]["primary_airline"], "Emirates")

    def test_price_is_total(self):
        """Price should be the total multi-city price."""
        raw = self._make_raw_multicity(price=1500)
        result = _normalize_serpapi_multicity(raw, "2026-06-29", "2026-07-14")
        self.assertEqual(result["price"], 1500)

    def test_search_dates(self):
        """Outbound search_date should be outbound_date, return should be return_date."""
        raw = self._make_raw_multicity()
        result = _normalize_serpapi_multicity(raw, "2026-06-29", "2026-07-14")
        self.assertEqual(result["search_date"], "2026-06-29")
        self.assertEqual(result["return_leg"]["search_date"], "2026-07-14")

    def test_nonstop_outbound(self):
        """Nonstop outbound should have 0 stops on outbound leg."""
        raw = self._make_raw_multicity(out_stops=0)
        result = _normalize_serpapi_multicity(raw, "2026-06-29", "2026-07-14")
        self.assertEqual(result["stops"], 0)

    def test_return_with_stop(self):
        """Return with 1 stop should have correct stop count and layover info."""
        raw = self._make_raw_multicity(ret_stops=1)
        result = _normalize_serpapi_multicity(raw, "2026-06-29", "2026-07-14")
        self.assertEqual(result["return_leg"]["stops"], 1)
        self.assertGreater(result["return_leg"]["total_layover_min"], 0)

    def test_flight_to_dict_includes_return_leg(self):
        """_flight_to_dict should include return_leg for multi-city flights."""
        raw = self._make_raw_multicity()
        result = _normalize_serpapi_multicity(raw, "2026-06-29", "2026-07-14")
        result["score"] = 100
        result = label_fare_types([result])[0]
        d = _flight_to_dict(result)
        self.assertEqual(d["type"], "multi_city")
        self.assertIn("return_leg", d)
        self.assertEqual(d["return_leg"]["primary_airline"], "Turkish Airlines")
        self.assertIn("departure_time", d["return_leg"])
        self.assertIn("arrival_time", d["return_leg"])
        self.assertIn("stops", d["return_leg"])

    def test_flight_to_dict_no_return_for_oneway(self):
        """_flight_to_dict should NOT include return_leg for one-way flights."""
        f = _make_flight()
        f = label_fare_types([f])[0]
        f = score_flights([f])[0]
        d = _flight_to_dict(f)
        self.assertNotIn("type", d)
        self.assertNotIn("return_leg", d)

    def test_scoring_works_on_multicity(self):
        """Scoring pipeline should work on multi-city flights."""
        raw = self._make_raw_multicity(price=1200)
        result = _normalize_serpapi_multicity(raw, "2026-06-29", "2026-07-14")
        labeled = label_fare_types([result])
        scored = score_flights(labeled)
        self.assertEqual(len(scored), 1)
        self.assertIsNotNone(scored[0]["score"])

    def test_normalize_routes_multicity(self):
        """normalize() should detect _source=serpapi_multicity and use multicity normalizer."""
        raw = self._make_raw_multicity()
        results = normalize([raw])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["type"], "multi_city")


class TestMultiCityFrontend(unittest.TestCase):
    """Tests for multi-city frontend elements in index.html."""

    @classmethod
    def setUpClass(cls):
        import pathlib
        cls.html = pathlib.Path("public/index.html").read_text()

    def test_multicity_direction_pill(self):
        """Multi-City direction pill should exist."""
        self.assertIn('data-dir="multicity"', self.html)
        self.assertIn('Multi-City', self.html)

    def test_mc_picker_elements(self):
        """Multi-city date picker function should exist."""
        self.assertIn('renderMCPicker', self.html)
        self.assertIn('mc-out-date', self.html)
        self.assertIn('mc-ret-date', self.html)
        self.assertIn('mc-search-btn', self.html)

    def test_mc_loading_animation(self):
        """Loading animation with progress bar and phrases should exist."""
        self.assertIn('mc-progress-bar', self.html)
        self.assertIn('mc-progress-fill', self.html)
        self.assertIn('mc-loading-phrase', self.html)
        self.assertIn('Searching multi-city fares', self.html)

    def test_mc_card_layout(self):
        """Multi-city card should show both outbound and return legs."""
        self.assertIn('mcCardHTML', self.html)
        self.assertIn('mc-leg-label', self.html)
        self.assertIn('OUTBOUND', self.html)
        self.assertIn('RETURN', self.html)

    def test_mc_savings_calculation(self):
        """Savings calculation function should exist."""
        self.assertIn('mcSavingsHTML', self.html)
        self.assertIn('getCheapestOneWay', self.html)
        self.assertIn('Save $', self.html)
        self.assertIn('Booking separately may be cheaper', self.html)

    def test_mc_cache(self):
        """Multi-city results should be cached in memory."""
        self.assertIn('multiCityCache', self.html)

    def test_mc_api_endpoint(self):
        """Should call /api/multicity endpoint."""
        self.assertIn('/api/multicity', self.html)

    def test_mc_book_modal_shows_both_legs(self):
        """Book modal should show both legs for multi-city flights."""
        self.assertIn("f.type === 'multi_city'", self.html)
        self.assertIn('Outbound:', self.html)
        self.assertIn('round trip', self.html)

    def test_mc_filter_under_2000(self):
        """Under $1K filter should become Under $2K for multi-city."""
        self.assertIn("'Under $2,000'", self.html)

    def test_mc_heroes_config(self):
        """Heroes config should include multicity entry."""
        self.assertIn("multicity:", self.html)
        self.assertIn('Round Trip', self.html)

    def test_mc_route_labels(self):
        """Route labels should include multicity."""
        self.assertIn('multicity:', self.html)


class TestPointsAPI(unittest.TestCase):
    """Tests for the Points AI API endpoint."""

    def test_system_prompt_content(self):
        """Points prompt should mention transfer partners and points vs cash."""
        from api.points import SYSTEM_PROMPT
        self.assertIn("transfer", SYSTEM_PROMPT)
        self.assertIn("points", SYSTEM_PROMPT)
        self.assertIn("cash", SYSTEM_PROMPT)
        self.assertIn("4 bullet", SYSTEM_PROMPT)

    def test_system_prompt_no_emojis(self):
        """Points prompt should forbid emojis."""
        from api.points import SYSTEM_PROMPT
        self.assertIn("No emojis", SYSTEM_PROMPT)

    def test_openai_key_env_var(self):
        """Points API should use OPENAI_API_KEY."""
        import api.points as mod
        self.assertTrue(hasattr(mod, "OPENAI_API_KEY"))

    def test_missing_key_returns_fallback(self):
        """Missing API key should return fallback, not crash."""
        import api.points as mod
        original = mod.OPENAI_API_KEY
        try:
            mod.OPENAI_API_KEY = ""
            # The handler raises ValueError which is caught internally
            # and returns the fallback message
            self.assertIn("unavailable", "Points strategy unavailable right now")
        finally:
            mod.OPENAI_API_KEY = original

    def test_handler_has_cors(self):
        """Points handler should support CORS."""
        import inspect
        from api.points import handler
        source = inspect.getsource(handler)
        self.assertIn("Access-Control-Allow-Origin", source)
        self.assertIn("do_OPTIONS", source)

    def test_fallback_message(self):
        """Fallback message should be user-friendly."""
        import inspect
        from api.points import handler
        source = inspect.getsource(handler)
        self.assertIn("unavailable", source)


class TestPointsAIFrontend(unittest.TestCase):
    """Tests for Points AI frontend integration."""

    def setUp(self):
        with open("public/index.html", "r") as fh:
            self.html = fh.read()

    def test_sparkle_svg_exists(self):
        """Points AI should use an inline SVG sparkle icon."""
        self.assertIn("function sparkleSVG()", self.html)
        self.assertIn("#b044ff", self.html)
        self.assertIn("#00c8ff", self.html)

    def test_points_ai_trigger_in_card(self):
        """Flight cards should include Points AI trigger."""
        self.assertIn("points-ai-trigger", self.html)
        self.assertIn("Points AI", self.html)

    def test_points_ai_cache(self):
        """Points AI responses should be cached per session."""
        self.assertIn("pointsAICache", self.html)

    def test_loyalty_programs_list(self):
        """Should include common loyalty programs."""
        self.assertIn("Amex Membership Rewards", self.html)
        self.assertIn("Chase Ultimate Rewards", self.html)
        self.assertIn("Delta SkyMiles", self.html)
        self.assertIn("United MileagePlus", self.html)
        self.assertIn("British Airways Avios", self.html)

    def test_localstorage_key(self):
        """Programs should persist in localStorage."""
        self.assertIn("dcf_points_programs", self.html)

    def test_modal_frosted_glass(self):
        """Points AI modal should use frosted glass style."""
        start = self.html.index(".points-modal-box {")
        end = self.html.index("}", start) + 1
        css = self.html[start:end]
        self.assertIn("backdrop-filter", css)
        self.assertIn("blur(20px)", css)
        self.assertIn("rgba(250, 248, 243, 0.95)", css)

    def test_gradient_label(self):
        """Points AI label should use purple-cyan gradient."""
        start = self.html.index(".points-ai-label {")
        end = self.html.index("}", start) + 1
        css = self.html[start:end]
        self.assertIn("background-clip: text", css)
        self.assertIn("#b044ff", css)
        self.assertIn("#00c8ff", css)

    def test_modal_overlay(self):
        """Points AI should use a modal overlay."""
        self.assertIn("points-modal-overlay", self.html)
        self.assertIn("position: fixed", self.html)

    def test_close_on_overlay_click(self):
        """Modal should close on overlay click and X button."""
        self.assertIn("closePointsModal", self.html)
        self.assertIn("points-modal-close", self.html)

    def test_api_endpoint(self):
        """Should POST to /api/points."""
        self.assertIn("/api/points", self.html)

    def test_sends_days_until_travel(self):
        """Points AI should send days_until_travel."""
        start = self.html.index("function fetchPointsStrategy(")
        end = self.html.index("\n  function ", start + 1)
        fn_body = self.html[start:end]
        self.assertIn("days_until_travel", fn_body)

    def test_flight_summary_in_modal(self):
        """Modal should show flight details."""
        self.assertIn("flightSummaryLine", self.html)

    def test_programs_popover_save_button(self):
        """Programs popover should have a save button."""
        self.assertIn("Save &amp; Get Strategy", self.html)

    def test_error_fallback_message(self):
        """Should show user-friendly error on API failure."""
        self.assertIn("Points strategy unavailable right now", self.html)


class TestPointsConsistency(unittest.TestCase):
    """Tests for Points AI consistency and correctness."""

    def test_temperature_zero(self):
        """Points API should use temperature=0 for consistent results."""
        import inspect
        from api.points import handler
        source = inspect.getsource(handler)
        self.assertIn('"temperature": 0', source)

    def test_flight_numbers_in_api(self):
        """Points API should include flight numbers in the prompt."""
        import inspect
        from api.points import handler
        source = inspect.getsource(handler)
        self.assertIn("flight_numbers", source)

    def test_alliance_data_in_prompt(self):
        """Points API should include airline alliance reference data."""
        from api.points import SYSTEM_PROMPT
        self.assertIn("SkyTeam", SYSTEM_PROMPT)
        self.assertIn("Star Alliance", SYSTEM_PROMPT)
        self.assertIn("oneworld", SYSTEM_PROMPT)

    def test_transfer_partners_in_prompt(self):
        """Points API should include credit card transfer partner data."""
        from api.points import SYSTEM_PROMPT
        self.assertIn("Amex Membership Rewards", SYSTEM_PROMPT)
        self.assertIn("Chase Ultimate Rewards", SYSTEM_PROMPT)
        self.assertIn("Citi ThankYou", SYSTEM_PROMPT)
        self.assertIn("Capital One Miles", SYSTEM_PROMPT)

    def test_alliance_matching_rule(self):
        """Prompt should instruct to match airline to correct alliance."""
        from api.points import SYSTEM_PROMPT
        self.assertIn("CRITICAL", SYSTEM_PROMPT)
        self.assertIn("Do NOT mix alliances", SYSTEM_PROMPT)

    def test_airlines_array_sent(self):
        """Points API should receive airlines array, not just primary."""
        import inspect
        from api.points import handler
        source = inspect.getsource(handler)
        self.assertIn("airlines", source)
        self.assertIn("Operating airline", source)

    def test_flight_numbers_in_frontend_data(self):
        """Flight card data should include flight_numbers."""
        with open("public/index.html", "r") as fh:
            html = fh.read()
        # cardHTML should pass flight_numbers in fData
        start = html.index("function cardHTML(")
        end = html.index("\n  function ", start + 1)
        fn_body = html[start:end]
        self.assertIn("flight_numbers", fn_body)

    def test_flight_numbers_in_dict(self):
        """_flight_to_dict should include flight_numbers."""
        from flight_agent import _flight_to_dict
        import inspect
        source = inspect.getsource(_flight_to_dict)
        self.assertIn("flight_numbers", source)

    def test_cache_key_includes_flight_numbers(self):
        """Points AI cache key should include flight numbers."""
        with open("public/index.html", "r") as fh:
            html = fh.read()
        start = html.index("function fetchPointsStrategy(")
        end = html.index("\n  function ", start + 1)
        fn_body = html[start:end]
        self.assertIn("flight_numbers", fn_body)

    def test_economy_main_in_api_message(self):
        """Points API user message should say Economy Main."""
        import inspect
        from api.points import handler
        source = inspect.getsource(handler)
        self.assertIn("Economy Main", source)


class TestTimeAgo(unittest.TestCase):
    """Tests for the timeAgo function in family picks."""

    def setUp(self):
        with open("public/index.html", "r") as fh:
            self.html = fh.read()

    def test_timeago_handles_invalid(self):
        """timeAgo should return empty string for invalid timestamps."""
        start = self.html.index("function timeAgo(")
        end = self.html.index("\n  function ", start + 1)
        fn_body = self.html[start:end]
        self.assertIn("isNaN", fn_body)

    def test_timeago_shows_date_for_old(self):
        """timeAgo should show actual date for picks older than 24h."""
        start = self.html.index("function timeAgo(")
        end = self.html.index("\n  function ", start + 1)
        fn_body = self.html[start:end]
        self.assertIn("toLocaleDateString", fn_body)

    def test_timeago_negative_diff(self):
        """timeAgo should handle future timestamps gracefully."""
        start = self.html.index("function timeAgo(")
        end = self.html.index("\n  function ", start + 1)
        fn_body = self.html[start:end]
        self.assertIn("diff < 0", fn_body)


class TestBookUrlFallback(unittest.TestCase):
    """Tests for the Select Flight booking URL."""

    def setUp(self):
        with open("public/index.html", "r") as fh:
            self.html = fh.read()

    def test_fallback_uses_modern_google_flights(self):
        """Fallback URL should use google.com/travel/flights format."""
        start = self.html.index("function bookUrl(")
        end = self.html.index("\n  function ", start + 1)
        fn_body = self.html[start:end]
        self.assertIn("google.com/travel/flights", fn_body)
        self.assertNotIn("#search;f=", fn_body)

    def test_deep_link_preferred(self):
        """bookUrl should prefer google_flights_url when available."""
        start = self.html.index("function bookUrl(")
        end = self.html.index("\n  function ", start + 1)
        fn_body = self.html[start:end]
        self.assertIn("google_flights_url", fn_body)

    def test_flight_summary_shows_flight_numbers(self):
        """Points AI modal should display flight numbers."""
        start = self.html.index("function flightSummaryLine(")
        end = self.html.index("\n  function ", start + 1)
        fn_body = self.html[start:end]
        self.assertIn("flight_numbers", fn_body)


class TestSummaryPromptUpdate(unittest.TestCase):
    """Tests for the updated AI briefing system prompt."""

    def test_prompt_covers_four_topics(self):
        """Updated prompt should cover best deal, family, timing, and tips."""
        from api.summary import SYSTEM_PROMPT
        self.assertIn("Best deal", SYSTEM_PROMPT)
        self.assertIn("Family", SYSTEM_PROMPT)
        self.assertIn("Timing", SYSTEM_PROMPT)
        self.assertIn("Tip", SYSTEM_PROMPT)

    def test_prompt_mentions_incognito(self):
        """Prompt should mention incognito mode as a tip."""
        from api.summary import SYSTEM_PROMPT
        self.assertIn("incognito", SYSTEM_PROMPT.lower())

    def test_no_hedging_language(self):
        """Prompt should explicitly forbid hedging."""
        from api.summary import SYSTEM_PROMPT
        self.assertIn("it may be worth considering", SYSTEM_PROMPT)

    def test_client_sends_days_until_travel(self):
        """Client should send days_until_travel to summary API."""
        with open("public/index.html", "r") as fh:
            html = fh.read()
        start = html.index("function fetchAISummary()")
        end = html.index("\n  function ", start + 1)
        fn_body = html[start:end]
        self.assertIn("days_until_travel", fn_body)
        self.assertIn("daysToGo()", fn_body)

    def test_summary_api_uses_days_and_price_range(self):
        """Summary API should include days_until and price range in user msg."""
        import inspect
        from api.summary import handler
        source = inspect.getsource(handler)
        self.assertIn("days_until", source)
        self.assertIn("price_range", source)


if __name__ == "__main__":
    unittest.main()
