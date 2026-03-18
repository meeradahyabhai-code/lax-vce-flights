"""Unit tests for the Hotel Points AI feature.

Tests cover:
- API endpoint structure (hotel-points.py)
- Frontend integration (index.html JS functions, markup, wiring)
- Vercel deployment config
"""

import os
import filecmp
import unittest

BASE = os.path.dirname(os.path.abspath(__file__))
HOTEL_POINTS_PY = os.path.join(BASE, "api", "hotel-points.py")
POINTS_PY = os.path.join(BASE, "api", "points.py")
VERCEL_JSON = os.path.join(BASE, "vercel.json")
WEB_INDEX = os.path.join(BASE, "web", "index.html")
PUBLIC_INDEX = os.path.join(BASE, "public", "index.html")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ── API Endpoint Tests ──────────────────────────────────────────────


class TestHotelPointsAPI(unittest.TestCase):
    """Tests against api/hotel-points.py source."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(HOTEL_POINTS_PY)

    def test_hotel_points_endpoint_exists(self):
        self.assertTrue(os.path.isfile(HOTEL_POINTS_PY))

    def test_hotel_points_has_handler_class(self):
        self.assertIn("class handler(BaseHTTPRequestHandler)", self.src)

    def test_hotel_points_has_do_POST(self):
        self.assertIn("def do_POST(self)", self.src)

    def test_hotel_points_has_do_OPTIONS(self):
        self.assertIn("def do_OPTIONS(self)", self.src)

    def test_hotel_points_has_hotel_transfer_data(self):
        self.assertIn("HOTEL_TRANSFER_DATA", self.src)
        for brand in ("Marriott", "Hilton", "Hyatt", "IHG"):
            self.assertIn(brand, self.src, f"Missing brand: {brand}")

    def test_hotel_points_has_system_prompt(self):
        self.assertIn("SYSTEM_PROMPT", self.src)
        self.assertIn("hotel", self.src.lower())

    def test_hotel_points_system_prompt_four_bullets(self):
        for keyword in ("Transfer", "Points price", "FHR/THC", "Verdict"):
            self.assertIn(keyword, self.src, f"SYSTEM_PROMPT missing bullet: {keyword}")

    def test_hotel_points_reads_hotel_object(self):
        self.assertIn('payload.get("hotel"', self.src)
        # Must NOT read a flight object
        self.assertNotIn('payload.get("flight"', self.src)

    def test_hotel_points_registered_in_vercel(self):
        vercel = _read(VERCEL_JSON)
        self.assertIn("api/hotel-points.py", vercel)


# ── Frontend Tests ──────────────────────────────────────────────────


class TestHotelPointsFrontend(unittest.TestCase):
    """Source-code assertions against web/index.html."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(WEB_INDEX)

    def test_loyalty_programs_has_hotel_programs(self):
        for prog in ("Marriott Bonvoy", "Hilton Honors", "World of Hyatt", "IHG One Rewards"):
            self.assertIn(prog, self.src, f"LOYALTY_PROGRAMS missing: {prog}")

    def test_show_programs_modal_refactored(self):
        self.assertIn("function showProgramsModal(summaryHtml, callback)", self.src)

    def test_show_points_result_refactored(self):
        self.assertIn("function showPointsResult(summaryHtml, text)", self.src)

    def test_hotel_summary_line_function_exists(self):
        self.assertIn("function hotelSummaryLine", self.src)

    def test_fetch_hotel_points_strategy_exists(self):
        self.assertIn("function fetchHotelPointsStrategy", self.src)
        self.assertIn("/api/hotel-points", self.src)

    def test_handle_hotel_points_ai_click_exists(self):
        self.assertIn("function handleHotelPointsAIClick", self.src)
        self.assertIn("data-hotel", self.src)

    def test_attach_hotel_points_handlers_exists(self):
        self.assertIn("function attachHotelPointsAIHandlers", self.src)
        self.assertIn(".hotel-points-ai-trigger", self.src)

    def test_hotel_card_has_points_ai_button(self):
        self.assertIn("hotel-points-ai-trigger", self.src)
        self.assertIn("data-hotel", self.src)

    def test_render_hotel_results_attaches_handlers(self):
        self.assertIn("attachHotelPointsAIHandlers()", self.src)

    def test_public_index_matches_web(self):
        self.assertTrue(
            filecmp.cmp(WEB_INDEX, PUBLIC_INDEX, shallow=False),
            "public/index.html differs from web/index.html — they must be identical",
        )


if __name__ == "__main__":
    unittest.main()
