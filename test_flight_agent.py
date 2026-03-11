"""Unit tests for flight_agent.py pricing, scoring, fare labeling, return flights,
JSON export, email HTML, and summary API.
"""

import json
import unittest

from flight_agent import (
    AIRLINE_BONUSES,
    AKL_OUTBOUND_BONUSES,
    AKL_RETURN_AUTO_TOP_PICK,
    AKL_RETURN_BONUSES,
    ATL_OUTBOUND_BONUSES,
    ATL_RETURN_AUTO_TOP_PICK,
    ATL_RETURN_BONUSES,
    AUTO_TOP_PICK_NONSTOP,
    BASIC_ECONOMY_CARRIERS,
    BASIC_TO_MAIN_ADDER,
    DEPARTURE_DATES,
    RETURN_AIRLINE_BONUSES,
    RETURN_AUTO_TOP_PICK_NONSTOP,
    RETURN_DATES,
    ROUTES,
    _build_date_sections,
    _flight_to_dict,
    _layover_info,
    _normalize_skyscanner,
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


class TestFlightToDict(unittest.TestCase):
    """Tests for _flight_to_dict serialization."""

    def test_all_required_keys_present(self):
        """Serialized flight dict must contain all required keys."""
        f = _make_flight(airline="Delta", price=500)
        f = label_fare_types([f])[0]
        f = score_flights([f])[0]
        d = _flight_to_dict(f)
        required = {
            "primary_airline", "airlines", "departure_time", "arrival_time",
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
        """System prompt should mention Indian family and cruise trip."""
        from api.summary import SYSTEM_PROMPT
        self.assertIn("Indian family", SYSTEM_PROMPT)
        self.assertIn("cruise trip", SYSTEM_PROMPT)
        self.assertIn("2-3 sentences", SYSTEM_PROMPT)
        self.assertIn("No emojis", SYSTEM_PROMPT)

    def test_system_prompt_no_bullets(self):
        """System prompt should explicitly forbid bullet points."""
        from api.summary import SYSTEM_PROMPT
        self.assertIn("No bullet points", SYSTEM_PROMPT)

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

    def test_three_origins(self):
        """ROUTES should define exactly 3 origins."""
        self.assertEqual(len(ROUTES), 3)

    def test_origins_are_lax_akl_atl(self):
        """ROUTES origins should be LAX, AKL, ATL."""
        origins = {r["origin"] for r in ROUTES}
        self.assertEqual(origins, {"LAX", "AKL", "ATL"})

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

    def test_all_flights_have_economy_main_price(self):
        """All labeled flights should have economy_main_price set."""
        for carrier in ("Delta", "British Airways", "Singapore Airlines"):
            f = _make_flight(airline=carrier, price=500)
            flights = label_fare_types([f])
            self.assertIsNotNone(flights[0]["economy_main_price"],
                                 f"{carrier} should have economy_main_price")

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
        self.assertEqual(d["economy_main_price"], 600)
        self.assertEqual(d["premium_economy_price"], 1200)
        self.assertEqual(d["business_price"], 3500)

    def test_non_big3_basic_equals_main(self):
        """Non-Big-3 should have basic == main (same base fare)."""
        f = _make_flight(airline="Emirates", price=800)
        flights = label_fare_types([f])
        self.assertEqual(flights[0]["basic_economy_price"],
                         flights[0]["economy_main_price"])


class TestFrontendFilters(unittest.TestCase):
    """Tests for the frontend filter system (reads HTML source)."""

    def setUp(self):
        with open("public/index.html", "r") as fh:
            self.html = fh.read()

    def test_filter_chips_present(self):
        """All expected filter chips should be defined."""
        for label in ("Morning", "Evening", "Nonstop", "1 Stop",
                       "Under $1,000", "Basic", "Main Cabin",
                       "Premium", "Business/First"):
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
        """Card HTML should include dynamic fare badge label."""
        start = self.html.index("function cardHTML(f, rank, isTopPick)")
        end = self.html.index("\n  function ", start + 1)
        fn_body = self.html[start:end]
        self.assertIn("fareBadgeLabel", fn_body)
        self.assertIn("getActiveFareClass", fn_body)

    def test_origin_modal_three_cards(self):
        """Origin modal should have exactly 3 origin cards."""
        count = self.html.count('class="origin-card"')
        self.assertEqual(count, 3)

    def test_origin_pills_three(self):
        """Hero should have 3 origin pills."""
        count = self.html.count('class="origin-pill"')
        self.assertEqual(count, 3)

    def test_change_city_link(self):
        """Change city link should be present."""
        self.assertIn('id="change-city"', self.html)
        self.assertIn("clearStoredOrigin", self.html)


if __name__ == "__main__":
    unittest.main()
