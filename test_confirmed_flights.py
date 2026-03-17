"""Tests for Confirmed Flights feature — screenshot parsing API + frontend."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class TestParseScreenshotAPI(unittest.TestCase):
    """Tests for api/parse_screenshot.py endpoint."""

    def test_system_prompt_extracts_required_fields(self):
        """Parse prompt should ask for all required flight fields."""
        from api.parse_screenshot import PARSE_PROMPT
        for field in ["airline", "flight_number", "departure_airport",
                      "departure_date", "departure_time", "arrival_airport",
                      "arrival_date", "arrival_time", "stopovers", "direction"]:
            self.assertIn(field, PARSE_PROMPT)

    def test_prompt_returns_json(self):
        """Parse prompt should instruct to return JSON only."""
        from api.parse_screenshot import PARSE_PROMPT
        self.assertIn("JSON", PARSE_PROMPT)
        self.assertIn("null", PARSE_PROMPT)

    def test_prompt_no_guessing(self):
        """Parse prompt should instruct not to guess."""
        from api.parse_screenshot import PARSE_PROMPT
        self.assertIn("Do not guess", PARSE_PROMPT)

    def test_uses_gpt4o_vision(self):
        """Should use gpt-4o for vision capabilities."""
        import inspect
        from api.parse_screenshot import handler
        source = inspect.getsource(handler)
        self.assertIn("gpt-4o", source)
        self.assertIn("image_url", source)

    def test_cors_headers(self):
        """Should include CORS headers."""
        import inspect
        from api.parse_screenshot import handler
        source = inspect.getsource(handler)
        self.assertIn("Access-Control-Allow-Origin", source)
        self.assertIn("do_OPTIONS", source)

    def test_handles_missing_image(self):
        """Should return error when no image provided."""
        import inspect
        from api.parse_screenshot import handler
        source = inspect.getsource(handler)
        self.assertIn("No image data provided", source)

    def test_strips_markdown_fences(self):
        """Should handle GPT responses wrapped in markdown code fences."""
        import inspect
        from api.parse_screenshot import handler
        source = inspect.getsource(handler)
        self.assertIn("```", source)

    def test_registered_in_vercel(self):
        """parse_screenshot should be in vercel.json functions."""
        with open("vercel.json", "r") as f:
            config = json.load(f)
        self.assertIn("api/parse_screenshot.py", config.get("functions", {}))


class TestConfirmedFlightsFrontend(unittest.TestCase):
    """Tests for confirmed flights UI in the frontend."""

    def setUp(self):
        with open("public/index.html", "r") as fh:
            self.html = fh.read()

    def test_travelers_list_exists(self):
        """TRAVELERS constant should have all 25 family members."""
        self.assertIn("var TRAVELERS = [", self.html)
        self.assertIn("Bhula Joshila P.", self.html)
        self.assertIn("Dahyabhai Meera C.", self.html)
        self.assertIn("Madhav Shakeel A.", self.html)
        # Count entries
        count = self.html.count('"Bhula') + self.html.count('"Dinesh') + \
                self.html.count('"Dahyabhai') + self.html.count('"Patel') + \
                self.html.count('"Kaur') + self.html.count('"Dharna') + \
                self.html.count('"Chatha') + self.html.count('"Aceves') + \
                self.html.count('"Paredes') + self.html.count('"Madhav')
        self.assertGreaterEqual(count, 25)

    def test_confirmed_section_html(self):
        """Confirmed flights section should exist in HTML."""
        self.assertIn('id="confirmed-outbound"', self.html)
        self.assertIn('id="confirmed-return"', self.html)

    def test_add_flight_modal_function(self):
        """openAddFlightModal function should exist."""
        self.assertIn("function openAddFlightModal(", self.html)

    def test_three_step_modal(self):
        """Modal should have 3 steps: Upload, Review, Travelers."""
        self.assertIn("1 Upload", self.html)
        self.assertIn("2 Review", self.html)
        self.assertIn("3 Travelers", self.html)

    def test_screenshot_upload(self):
        """Modal should support file upload and drag-drop."""
        self.assertIn("afm-dropzone", self.html)
        self.assertIn("afm-file-input", self.html)
        self.assertIn("dragover", self.html)

    def test_calls_parse_api(self):
        """Should POST to /api/parse_screenshot."""
        self.assertIn("/api/parse_screenshot", self.html)

    def test_image_compression(self):
        """Should compress image before sending to API."""
        self.assertIn("canvas.toDataURL", self.html)

    def test_review_step_editable_fields(self):
        """Review step should show editable fields with data-key attributes."""
        # These are generated dynamically via fieldHTML() function
        self.assertIn("data-key", self.html)
        self.assertIn("'airline'", self.html)
        self.assertIn("'departure_airport'", self.html)
        self.assertIn("'arrival_airport'", self.html)

    def test_direction_toggle(self):
        """Review step should have outbound/return direction toggle."""
        self.assertIn("Outbound (to Venice)", self.html)
        self.assertIn("Return (from Istanbul)", self.html)

    def test_traveler_checkboxes(self):
        """Step 3 should render travelers as checkboxes."""
        self.assertIn("afm-travelers", self.html)
        self.assertIn("TRAVELERS.map", self.html)

    def test_save_writes_to_sheets(self):
        """Save should send data to Google Sheets."""
        start = self.html.index("function saveConfirmedFlight(")
        end = self.html.index("\n  function ", start + 1) if "\n  function " in self.html[start + 1:] else len(self.html)
        fn = self.html[start:start + 1200]
        self.assertIn("add_confirmed_flight", fn)
        self.assertIn("SHEETS_URL", fn)

    def test_fetch_confirmed_flights(self):
        """Should fetch confirmed flights on page load."""
        self.assertIn("fetchConfirmedFlights()", self.html)
        self.assertIn("action=read", self.html)

    def test_refresh_interval(self):
        """Should refresh confirmed flights every 60 seconds."""
        self.assertIn("setInterval(fetchConfirmedFlights, 60000)", self.html)

    def test_join_flight_action(self):
        """Join button should send join_flight action."""
        self.assertIn("join_flight", self.html)
        self.assertIn("cf-join-btn", self.html)

    def test_delete_flight_action(self):
        """Delete should send delete_confirmed_flight action."""
        self.assertIn("delete_confirmed_flight", self.html)
        self.assertIn("cf-delete-btn", self.html)

    def test_confirmed_table_styling(self):
        """Table should have proper CSS classes."""
        self.assertIn(".cf-table", self.html)
        self.assertIn(".cf-time", self.html)
        self.assertIn(".cf-you-pill", self.html)
        self.assertIn(".cf-day-divider", self.html)

    def test_mobile_responsive(self):
        """Route and stopover columns should hide on mobile."""
        self.assertIn("cf-col-route", self.html)
        self.assertIn("cf-col-stop", self.html)
        # Check media query hides them
        self.assertIn(".cf-col-route, .cf-table .cf-col-stop { display: none; }", self.html)

    def test_add_button_per_table(self):
        """Each table section should have an Add My Flight button."""
        self.assertIn("cf-add-btn", self.html)
        self.assertIn('data-dir="outbound"', self.html)

    def test_flight_id_generation(self):
        """generateFlightId should create a consistent ID."""
        self.assertIn("function generateFlightId(", self.html)


class TestAppsScriptUpdate(unittest.TestCase):
    """Tests for the Google Apps Script update file."""

    def setUp(self):
        with open("apps_script_update.js", "r") as fh:
            self.script = fh.read()

    def test_add_confirmed_flight_handler(self):
        """Script should handle add_confirmed_flight action."""
        self.assertIn("add_confirmed_flight", self.script)

    def test_get_confirmed_flights_handler(self):
        """Script should handle get_confirmed_flights action."""
        self.assertIn("get_confirmed_flights", self.script)

    def test_delete_handler_with_email(self):
        """Delete should email mdahya@gmail.com when adder doesn't match."""
        self.assertIn("delete_confirmed_flight", self.script)
        self.assertIn("mdahya@gmail.com", self.script)
        self.assertIn("MailApp.sendEmail", self.script)

    def test_join_flight_handler(self):
        """Script should handle join_flight action."""
        self.assertIn("join_flight", self.script)

    def test_arriving_venice_tab(self):
        """Script should reference Arriving Venice tab."""
        self.assertIn("Arriving Venice", self.script)

    def test_departing_istanbul_tab(self):
        """Script should reference Departing Istanbul tab."""
        self.assertIn("Departing Istanbul", self.script)

    def test_one_row_per_traveler(self):
        """Should write one row per traveler for the same flight."""
        self.assertIn("travelers.forEach", self.script)
        self.assertIn("appendRow", self.script)

    def test_pending_approval_status(self):
        """Non-matching delete should return pending_approval."""
        self.assertIn("pending_approval", self.script)

    def test_join_copies_flight_data(self):
        """Join should copy flight data from existing row."""
        self.assertIn("templateRow", self.script)


if __name__ == "__main__":
    unittest.main()
