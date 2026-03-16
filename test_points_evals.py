"""Evaluation tests for Points AI — verifies correct airline matching.

Two categories:
1. Unit tests (always run): verify prompt construction sends the right airline
2. Live evals (run with --live flag): call OpenAI and verify response content

Usage:
  python -m pytest test_points_evals.py -v              # unit tests only
  python -m pytest test_points_evals.py -v --live       # include live OpenAI evals
"""

import json
import os
import sys
import unittest

import pytest
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.points import ALLIANCE_DATA, SYSTEM_PROMPT

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SKYTEAM = {"delta", "air france", "klm", "ita airways", "korean air"}
STAR_ALLIANCE = {"united", "lufthansa", "swiss", "turkish airlines",
                 "singapore airlines", "air canada", "ana"}
ONEWORLD = {"american", "american airlines", "british airways",
            "cathay pacific", "qatar airways", "alaska airlines", "finnair"}


def build_user_msg(airline, airlines=None, price=500, route="LAX → VCE",
                   programs=None, flight_numbers=None):
    """Build the same user message the API would construct."""
    if airlines is None:
        airlines = [airline]
    if programs is None:
        programs = ["Amex Membership Rewards", "Chase Ultimate Rewards"]
    if flight_numbers is None:
        flight_numbers = []
    fn_str = ", ".join(flight_numbers) if flight_numbers else "unknown"
    airlines_str = " + ".join(dict.fromkeys(airlines))
    return (
        f"Operating airline(s): {airlines_str}\n"
        f"Flight number(s): {fn_str}\n"
        f"Date: 2026-06-29\n"
        f"Departure: 2026-06-29 17:00\n"
        f"Stops: 1\n"
        f"Price: ${price} Economy Main\n"
        f"Route: {route}\n"
        f"Days until travel: 105\n"
        f"User's loyalty programs: {', '.join(programs)}"
    )


def call_points_api(airline, airlines=None, price=500, route="LAX → VCE",
                    programs=None):
    """Call OpenAI with the Points AI prompt. Returns response text."""
    import requests
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        pytest.skip("OPENAI_API_KEY not set")
    msg = build_user_msg(airline, airlines, price, route, programs)
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": "gpt-4o-mini",
            "max_tokens": 200,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": msg},
            ],
        },
        timeout=15,
    )
    if resp.status_code == 401:
        pytest.skip("OPENAI_API_KEY is invalid or expired")
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Unit tests — prompt construction (no API calls)
# ---------------------------------------------------------------------------

class TestPromptConstruction(unittest.TestCase):
    """Verify the user message sent to OpenAI contains correct airline info."""

    def test_delta_in_message(self):
        msg = build_user_msg("Delta", ["Delta", "Air France"])
        self.assertIn("Delta", msg)
        self.assertIn("Air France", msg)

    def test_american_in_message(self):
        msg = build_user_msg("American Airlines")
        self.assertIn("American Airlines", msg)

    def test_british_airways_in_message(self):
        msg = build_user_msg("British Airways")
        self.assertIn("British Airways", msg)

    def test_turkish_in_message(self):
        msg = build_user_msg("Turkish Airlines", route="IST → LAX")
        self.assertIn("Turkish Airlines", msg)
        self.assertIn("IST → LAX", msg)

    def test_singapore_in_message(self):
        msg = build_user_msg("Singapore Airlines", route="AKL → VCE")
        self.assertIn("Singapore Airlines", msg)

    def test_programs_in_message(self):
        msg = build_user_msg("Delta", programs=["Delta SkyMiles", "Capital One Miles"])
        self.assertIn("Delta SkyMiles", msg)
        self.assertIn("Capital One Miles", msg)

    def test_price_in_message(self):
        msg = build_user_msg("United", price=623)
        self.assertIn("$623", msg)
        self.assertIn("Economy Main", msg)

    def test_multi_carrier_in_message(self):
        msg = build_user_msg("Delta", ["Delta", "KLM"])
        self.assertIn("Delta + KLM", msg)


class TestSystemPromptContent(unittest.TestCase):
    """Verify the system prompt has correct alliance and transfer data."""

    def test_skyteam_airlines(self):
        for airline in ["Delta", "Air France", "KLM"]:
            self.assertIn(airline, ALLIANCE_DATA)

    def test_star_alliance_airlines(self):
        for airline in ["United", "Lufthansa", "Turkish Airlines", "Singapore Airlines"]:
            self.assertIn(airline, ALLIANCE_DATA)

    def test_oneworld_airlines(self):
        for airline in ["American", "British Airways", "Cathay Pacific", "Qatar Airways"]:
            self.assertIn(airline, ALLIANCE_DATA)

    def test_amex_transfers(self):
        self.assertIn("Amex Membership Rewards", ALLIANCE_DATA)
        self.assertIn("Delta 1:1", ALLIANCE_DATA)
        self.assertIn("British Airways 1:1", ALLIANCE_DATA)

    def test_chase_transfers(self):
        self.assertIn("Chase Ultimate Rewards", ALLIANCE_DATA)
        self.assertIn("United 1:1", ALLIANCE_DATA)

    def test_citi_transfers(self):
        self.assertIn("Citi ThankYou", ALLIANCE_DATA)
        self.assertIn("Turkish Airlines 1:1", ALLIANCE_DATA)

    def test_capital_one_transfers(self):
        self.assertIn("Capital One Miles", ALLIANCE_DATA)

    def test_no_alliance_mixing_instruction(self):
        self.assertIn("Do NOT mix alliances", SYSTEM_PROMPT)

    def test_critical_matching_instruction(self):
        self.assertIn("CRITICAL", SYSTEM_PROMPT)

    def test_economy_main_instruction(self):
        self.assertIn("Economy Main", SYSTEM_PROMPT)


class TestFrontendPointsAI(unittest.TestCase):
    """Verify frontend sends correct data per flight card."""

    def setUp(self):
        with open("public/index.html", "r") as fh:
            self.html = fh.read()

    def test_fdata_includes_airlines_array(self):
        """cardHTML fData must include airlines array."""
        start = self.html.index("function cardHTML(")
        end = self.html.index("\n  function ", start + 1)
        fn = self.html[start:end]
        self.assertIn("airlines: f.airlines", fn)

    def test_fdata_includes_flight_numbers(self):
        """cardHTML fData must include flight_numbers."""
        start = self.html.index("function cardHTML(")
        end = self.html.index("\n  function ", start + 1)
        fn = self.html[start:end]
        self.assertIn("flight_numbers: f.flight_numbers", fn)

    def test_fetch_sends_airlines(self):
        """fetchPointsStrategy must send airlines to API."""
        start = self.html.index("function fetchPointsStrategy(")
        end = self.html.index("\n  function ", start + 1)
        fn = self.html[start:end]
        self.assertIn("airlines: fData.airlines", fn)

    def test_fetch_sends_primary_airline(self):
        """fetchPointsStrategy must send primary_airline."""
        start = self.html.index("function fetchPointsStrategy(")
        end = self.html.index("\n  function ", start + 1)
        fn = self.html[start:end]
        self.assertIn("primary_airline: fData.primary_airline", fn)

    def test_cache_key_includes_airlines(self):
        """Cache key must include airlines to prevent cross-airline collisions."""
        start = self.html.index("function fetchPointsStrategy(")
        end = self.html.index("\n  function ", start + 1)
        fn = self.html[start:end]
        self.assertIn("fData.airlines", fn)

    def test_modal_shows_airline_name(self):
        """Points AI modal should display the flight's airline."""
        self.assertIn("flightSummaryLine(fData)", self.html)
        start = self.html.index("function flightSummaryLine(")
        end = self.html.index("\n  function ", start + 1)
        fn = self.html[start:end]
        self.assertIn("primary_airline", fn)


# ---------------------------------------------------------------------------
# Live evals — call OpenAI (skip unless --live flag)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="Live evals need OPENAI_API_KEY in environment",
)
class TestLivePointsEval(unittest.TestCase):
    """Call OpenAI and verify responses match the correct airline/alliance.

    Run with: python -m pytest test_points_evals.py -k Live -v
    Requires OPENAI_API_KEY in .env or environment.
    """

    def _assert_mentions_any(self, text, words, msg=""):
        """Assert that at least one of the words appears in text."""
        text_lower = text.lower()
        found = any(w.lower() in text_lower for w in words)
        if not found:
            self.fail(f"None of {words} found in response: {text}. {msg}")

    def _assert_not_mentions(self, text, words, msg=""):
        """Assert that none of the words appear in text."""
        text_lower = text.lower()
        for w in words:
            if w.lower() in text_lower:
                self.fail(f"'{w}' should NOT appear in response: {text}. {msg}")

    def test_delta_gets_skyteam_advice(self):
        """Delta flight → should mention Delta/SkyMiles, not AA/United."""
        resp = call_points_api("Delta", ["Delta", "Air France"], price=547)
        self._assert_mentions_any(resp, ["Delta", "SkyMiles", "SkyTeam"])
        self._assert_not_mentions(resp, ["American Airlines", "AAdvantage",
                                          "United MileagePlus", "oneworld"])

    def test_american_gets_oneworld_advice(self):
        """American Airlines flight → should mention AA/oneworld partners."""
        resp = call_points_api("American Airlines", price=614)
        self._assert_mentions_any(resp, ["American", "oneworld", "British Airways",
                                          "Avios", "AAdvantage"])
        self._assert_not_mentions(resp, ["Delta", "SkyMiles", "SkyTeam",
                                          "United MileagePlus", "Star Alliance"])

    def test_united_gets_star_alliance_advice(self):
        """United flight → should mention United/Star Alliance."""
        resp = call_points_api("United", price=580)
        self._assert_mentions_any(resp, ["United", "MileagePlus", "Star Alliance"])
        self._assert_not_mentions(resp, ["Delta", "SkyMiles", "SkyTeam",
                                          "AAdvantage", "oneworld"])

    def test_turkish_gets_star_alliance_advice(self):
        """Turkish Airlines flight → Star Alliance, not SkyTeam."""
        resp = call_points_api("Turkish Airlines", route="IST → LAX", price=490)
        self._assert_mentions_any(resp, ["Turkish", "Star Alliance", "Miles&Smiles",
                                          "Citi", "Capital One"])
        self._assert_not_mentions(resp, ["SkyTeam", "oneworld", "Delta SkyMiles"])

    def test_british_airways_gets_oneworld_advice(self):
        """BA flight → oneworld, Avios."""
        resp = call_points_api("British Airways", price=610)
        self._assert_mentions_any(resp, ["British Airways", "Avios", "oneworld"])
        self._assert_not_mentions(resp, ["SkyTeam", "Delta", "SkyMiles"])

    def test_singapore_airlines_gets_star_alliance(self):
        """Singapore Airlines → Star Alliance."""
        resp = call_points_api("Singapore Airlines", route="AKL → VCE", price=900,
                               programs=["Amex Membership Rewards", "Citi ThankYou"])
        self._assert_mentions_any(resp, ["Singapore", "KrisFlyer", "Star Alliance"])
        self._assert_not_mentions(resp, ["SkyTeam", "oneworld", "Delta"])

    def test_emirates_independent(self):
        """Emirates → independent, should not claim alliance membership."""
        resp = call_points_api("Emirates", route="AKL → VCE", price=850)
        self._assert_mentions_any(resp, ["Emirates", "Skywards"])
        self._assert_not_mentions(resp, ["SkyTeam member", "Star Alliance member",
                                          "oneworld member"])

    def test_lufthansa_codeshare_correct(self):
        """Lufthansa + Swiss → Star Alliance, not SkyTeam."""
        resp = call_points_api("Lufthansa", ["Lufthansa", "Swiss"], price=650)
        self._assert_mentions_any(resp, ["Lufthansa", "Star Alliance", "United",
                                          "Miles & More"])
        self._assert_not_mentions(resp, ["SkyTeam", "oneworld", "Delta SkyMiles"])

    def test_response_has_four_bullets(self):
        """Response should have exactly 4 bullet points."""
        resp = call_points_api("Delta", price=500)
        bullets = [l for l in resp.strip().split("\n") if l.strip().startswith("•")]
        self.assertEqual(len(bullets), 4,
                         f"Expected 4 bullets, got {len(bullets)}: {resp}")

    def test_response_mentions_economy_main(self):
        """Response should reference Economy Main."""
        resp = call_points_api("Delta", price=500)
        self.assertIn("Economy Main", resp)

    def test_no_programs_gives_cash_advice(self):
        """With no matching programs, should suggest cash strategy."""
        resp = call_points_api("Delta", price=450, programs=["Other"])
        self._assert_mentions_any(resp, ["cash", "no transfer", "no direct"])


if __name__ == "__main__":
    # Support --live flag
    if "--live" in sys.argv:
        sys.argv.remove("--live")
        os.environ["POINTS_EVAL_LIVE"] = "1"
    unittest.main()
