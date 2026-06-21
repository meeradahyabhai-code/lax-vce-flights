"""Unit tests for the dynamic restaurant search logic (restaurant_finder.py).

Pure/offline — the network calls (_geocode, _search_circle) are monkeypatched with
canned Google Places payloads, so this runs in the normal suite and locks in the
rules that make the hybrid trustworthy: Indian is always kept (below the rating
floor, but not 2-review noise), general keeps the quality bar, everything is capped
to the radius, results dedupe, and rows normalize to the client card shape.
"""
import restaurant_finder as rf


# Piazza San Marco-ish center; build places at known offsets.
CENTER = {"lat": 45.4341, "lng": 12.3388, "name": "Piazza San Marco"}


def _place(pid, name, lat, lng, rating, reviews, types=("restaurant",), primary="Restaurant"):
    return {
        "id": pid, "displayName": {"text": name},
        "location": {"latitude": lat, "longitude": lng},
        "rating": rating, "userRatingCount": reviews,
        "types": list(types), "primaryTypeDisplayName": {"text": primary},
        "photos": [{"name": f"places/{pid}/photos/x"}],
        "formattedAddress": "somewhere", "websiteUri": "https://x.example",
    }


def _patch(monkeypatch, indian_results, general_results):
    monkeypatch.setattr(rf, "_geocode", lambda q, k: dict(CENTER))

    def fake_search(query, center, radius_m, key, sleep=None, max_pages=None):
        return indian_results if query.lower().startswith("indian") else general_results

    monkeypatch.setattr(rf, "_search_circle", fake_search)


def test_indian_kept_below_rating_floor(monkeypatch):
    # 3.4 rating, 60 reviews — below the 4.0 general floor, but Indian must still be kept.
    indian = [_place("i1", "Bombay Spice", 45.50, 12.30, 3.4, 60, ("indian_restaurant",), "Indian Restaurant")]
    _patch(monkeypatch, indian, [])
    out = rf.search_area("venice", radius_mi=10, key="k")["restaurants"]
    names = [r["name"] for r in out]
    assert "Bombay Spice" in names
    spice = next(r for r in out if r["name"] == "Bombay Spice")
    assert spice["cuisine"] == "Indian"
    assert spice["dynamic"] is True


def test_indian_noise_dropped(monkeypatch):
    # 2-review listing is noise even for Indian (below INDIAN_MIN_REVIEWS).
    indian = [_place("i2", "Curry Hut", 45.44, 12.33, 2.3, 3, ("indian_restaurant",), "Indian Restaurant")]
    _patch(monkeypatch, indian, [])
    out = rf.search_area("venice", radius_mi=10, key="k")["restaurants"]
    assert "Curry Hut" not in [r["name"] for r in out]


def test_general_quality_floor(monkeypatch):
    # general spot under 4.0/100 must be dropped; a strong one kept.
    general = [
        _place("g1", "Weak Trattoria", 45.44, 12.33, 3.9, 500),   # rating too low
        _place("g2", "Thin Osteria", 45.44, 12.33, 4.6, 40),      # too few reviews
        _place("g3", "Great Bacaro", 45.44, 12.33, 4.5, 1200),    # keeps
    ]
    _patch(monkeypatch, [], general)
    names = [r["name"] for r in rf.search_area("venice", radius_mi=10, key="k")["restaurants"]]
    assert names == ["Great Bacaro"]


def test_radius_cap_enforced(monkeypatch):
    # ~0.7 mi away keeps; far one (~35 mi) is dropped even though Places biased it in.
    general = [
        _place("near", "Close Place", 45.4441, 12.3388, 4.5, 800),
        _place("far", "Padova Place", 45.94, 12.3388, 4.9, 5000),  # ~35 mi north
    ]
    _patch(monkeypatch, [], general)
    names = [r["name"] for r in rf.search_area("venice", radius_mi=10, key="k")["restaurants"]]
    assert "Close Place" in names
    assert "Padova Place" not in names


def test_dedupe_by_id_and_sorted_by_distance(monkeypatch):
    indian = [_place("dup", "Shared Spot", 45.50, 12.30, 4.4, 300, ("indian_restaurant",), "Indian Restaurant")]
    general = [
        _place("dup", "Shared Spot", 45.50, 12.30, 4.4, 300),         # same id -> deduped
        _place("g9", "Right Here", 45.4351, 12.3388, 4.6, 900),       # ~0.07 mi, should sort first
    ]
    _patch(monkeypatch, indian, general)
    out = rf.search_area("venice", radius_mi=10, key="k")["restaurants"]
    ids = [r["id"] for r in out]
    assert ids.count("dup") == 1
    # nearest first
    assert out[0]["name"] == "Right Here"
    assert out[0]["distance_mi"] <= out[-1]["distance_mi"]


def test_enrich_rows_noop_without_key():
    # No OpenAI key -> rows pass through untouched (no network, safe in CI).
    rows = [{"name": "X", "rating": 4.5, "reviews": 200, "vibe": "", "profile": None}]
    out = rf.enrich_rows(rows, openai_key=None)
    assert out is rows
    assert out[0]["profile"] is None and out[0]["vibe"] == ""


def test_normalize_shape(monkeypatch):
    general = [_place("g1", "Test Bacaro", 45.44, 12.33, 4.5, 1200)]
    _patch(monkeypatch, [], general)
    r = rf.search_area("venice", radius_mi=10, key="k")["restaurants"][0]
    for field in ("id", "name", "port_key", "cuisine", "rating", "reviews",
                  "distance_mi", "nearest_landmark", "photo_ref", "dynamic", "veg_options"):
        assert field in r, field
    assert r["port_key"] == "venice"
    assert r["photo_ref"].startswith("places/")
    assert r["profile"] is None  # dynamic rows carry no AI narrative
