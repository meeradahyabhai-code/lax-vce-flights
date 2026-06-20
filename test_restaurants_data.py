"""Data-integrity tests for the restaurant catalog (data/restaurants.json).

Pure + offline, so they run in the normal suite and catch data regressions before
they ship (bad ratings, missing fields, a price level masquerading as a cuisine,
missing photos, etc.). Network-based quality checks (rating freshness vs Google,
AI day-summary facts, chat picks) live in evals/restaurants_eval.py.
"""
import json
import os

import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(ROOT, "data", "restaurants.json")
PHOTO_DIR = os.path.join(ROOT, "public", "media", "restaurants")

PORTS = {"venice", "ravenna", "dubrovnik", "bar", "athens", "kusadasi", "rhodes", "santorini", "istanbul"}
# A cuisine must be a cuisine, never a price/formality level.
LEVEL_WORDS = {"fine dining", "casual", "smart casual", "fine dining restaurant", "upscale"}


@pytest.fixture(scope="module")
def restaurants():
    with open(CATALOG) as f:
        return json.load(f)["restaurants"]


def test_catalog_has_reasonable_size(restaurants):
    assert len(restaurants) >= 300, f"expected a full catalog, got {len(restaurants)}"


def test_no_duplicate_ids(restaurants):
    ids = [r["id"] for r in restaurants]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate restaurant ids: {list(dupes)[:5]}"


def test_required_fields_present(restaurants):
    missing = []
    for r in restaurants:
        for field in ("id", "name", "port_key", "lat", "lng", "rating", "reviews", "cuisine"):
            if r.get(field) in (None, ""):
                missing.append((r.get("name", "?"), field))
    assert not missing, f"missing required fields: {missing[:8]}"


def test_ratings_in_range(restaurants):
    bad = [(r["name"], r["rating"]) for r in restaurants if not (0 < float(r["rating"]) <= 5)]
    assert not bad, f"ratings out of range: {bad[:5]}"


def test_ports_are_known(restaurants):
    bad = {r["port_key"] for r in restaurants} - PORTS
    assert not bad, f"unknown port_keys: {bad}"


def test_cuisine_is_not_a_level(restaurants):
    # "Restaurant" is the allowed generic fallback; a price/formality level is NOT a cuisine.
    bad = [(r["name"], r["cuisine"]) for r in restaurants if (r.get("cuisine") or "").strip().lower() in LEVEL_WORDS]
    assert not bad, f"cuisine field holds a level, not a cuisine: {bad[:8]}"


def test_veg_flags_are_boolean(restaurants):
    bad = [r["name"] for r in restaurants if not isinstance(r.get("veg_options"), bool) or not isinstance(r.get("fully_veg"), bool)]
    assert not bad, f"non-boolean veg flags: {bad[:5]}"


def test_michelin_values_valid(restaurants):
    ok = {None, "", "star", "bib", "selected"}
    bad = [(r["name"], r["michelin"]) for r in restaurants if r.get("michelin") not in ok]
    assert not bad, f"invalid michelin tier values: {bad[:5]}"


def test_profile_and_vibe_present(restaurants):
    no_profile = [r["name"] for r in restaurants if not r.get("profile")]
    no_vibe = [r["name"] for r in restaurants if not (r.get("vibe") or "").strip()]
    # allow a tiny number of stragglers (a failed enrich call), but not wholesale gaps
    assert len(no_profile) <= 3, f"{len(no_profile)} restaurants missing AI profile"
    assert len(no_vibe) <= 3, f"{len(no_vibe)} restaurants missing vibe line"


def test_photos_exist_on_disk(restaurants):
    missing = [r["name"] for r in restaurants if r.get("photo_local")
               and not os.path.exists(os.path.join(ROOT, "public", r["photo_local"].lstrip("/")))]
    assert not missing, f"{len(missing)} restaurants reference a photo that isn't on disk: {missing[:5]}"


def test_every_port_has_options(restaurants):
    from collections import Counter
    by_port = Counter(r["port_key"] for r in restaurants)
    thin = {p: by_port.get(p, 0) for p in PORTS if by_port.get(p, 0) < 8}
    assert not thin, f"ports with too few restaurants: {thin}"


def test_indian_present_at_some_ports(restaurants):
    # Indian was a hard requirement: always included regardless of rating.
    indian = [r for r in restaurants if (r.get("cuisine") or "").lower() == "indian"]
    assert len(indian) >= 5, f"expected Indian options across ports, found {len(indian)}"
