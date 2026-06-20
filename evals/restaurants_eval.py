"""Network/AI quality evals for the restaurants feature.

The pure data-integrity checks live in test_restaurants_data.py and run in the
normal suite. THIS makes real network calls (Google Places + the /api/ask LLM
endpoint), so it is run on demand, not in CI. It measures the three things that
make the feature smart instead of dumb:

  1. RATING FRESHNESS  — our stored rating/review count still matches live Google
     Places (catalog drifts over months). Free: Google Places Details by place_id.
  2. DAY-SUMMARY FACTS — the AI day plan respects the deterministic per-meal
     SITUATION (booked names the place + time; aboard says the ship; never invents
     eating ashore when aboard; tour uses the real start time). Cheap: gpt-4.1-nano.
  3. CHAT PICKS RESOLVE — a real concierge query returns picks that (a) resolve to
     real ids, (b) belong to the asked port, (c) honor a hard constraint (Indian,
     vegetarian, Michelin). Cheap: gpt-4.1-nano.

Usage:
  python3 evals/restaurants_eval.py                 # AI checks vs PROD, freshness on a sample
  python3 evals/restaurants_eval.py --url http://localhost:8099/api/ask
  python3 evals/restaurants_eval.py --sample 20     # freshness sample size (Places lookups)
  python3 evals/restaurants_eval.py --skip-freshness # only the AI checks

Exit code is non-zero if any check fails, so it can gate a release.
"""
import argparse
import json
import os
import sys
import urllib.request
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "api"))
import hotel_agent  # noqa: F401,E402  (loads .env)

import requests  # noqa: E402

CATALOG = os.path.join(ROOT, "data", "restaurants.json")
DEFAULT_URL = "https://dfc-2026.vercel.app/api/ask"
PLACES_DETAILS = "https://places.googleapis.com/v1/places/"

# A drift bigger than this between our stored value and live Google is worth a look.
RATING_TOLERANCE = 0.3
# Reviews only grow; a big DROP means we matched the wrong place or it closed.
REVIEW_DROP_FRAC = 0.5


def load_catalog():
    with open(CATALOG) as f:
        return json.load(f)["restaurants"]


def _places_key():
    return (os.environ.get("GOOGLE_PLACES_API_KEY")
            or os.environ.get("GOOGLE_MAPS_API_KEY") or "").strip()


# ---------------------------------------------------------------- freshness ----
def check_freshness(restaurants, sample):
    """Spot-check stored rating/reviews against live Google Places."""
    key = _places_key()
    if not key:
        print("SKIP freshness: no Google Places key in env")
        return True

    # Deterministic, spread-across-ports sample (no randomness — repeatable runs).
    by_port = defaultdict(list)
    for r in restaurants:
        by_port[r["port_key"]].append(r)
    picks = []
    ports = sorted(by_port)
    i = 0
    while len(picks) < min(sample, len(restaurants)):
        port = ports[i % len(ports)]
        bucket = by_port[port]
        idx = i // len(ports)
        if idx < len(bucket):
            picks.append(bucket[idx])
        i += 1
        if i > len(restaurants) * 2:
            break

    drift, gone, ok = [], [], 0
    for r in picks:
        pid = r["id"]
        try:
            resp = requests.get(
                PLACES_DETAILS + pid,
                headers={"X-Goog-Api-Key": key,
                         "X-Goog-FieldMask": "rating,userRatingCount,displayName"},
                timeout=15,
            )
            if resp.status_code == 404:
                gone.append((r["name"], "place_id 404 — closed or wrong id"))
                continue
            resp.raise_for_status()
            live = resp.json()
        except Exception as exc:  # noqa: BLE001
            print(f"   ! lookup failed {r['name']}: {str(exc)[:80]}")
            continue

        live_rating = live.get("rating")
        live_reviews = live.get("userRatingCount")
        if live_rating is None:
            continue
        d = abs(float(live_rating) - float(r["rating"]))
        if d > RATING_TOLERANCE:
            drift.append((r["name"], r["rating"], live_rating))
        elif (live_reviews is not None and r.get("reviews")
              and live_reviews < r["reviews"] * REVIEW_DROP_FRAC):
            gone.append((r["name"], f"reviews {r['reviews']}→{live_reviews} (likely wrong match)"))
        else:
            ok += 1

    print(f"\nFRESHNESS  ({len(picks)} sampled across {len(ports)} ports)")
    print(f"   {ok} within ±{RATING_TOLERANCE}")
    for name, mine, live in drift:
        print(f"   DRIFT  {name}: stored {mine} vs live {live}")
    for name, why in gone:
        print(f"   STALE  {name}: {why}")
    # Drift is informational (Google moves); a 404 / collapsed reviews is a real defect.
    return not gone


# --------------------------------------------------------------- day summary ----
def _post(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Origin": "https://dfc-2026.vercel.app"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


DAY_CASES = [
    {
        "name": "booked dinner + morning tour",
        "day": {
            "port": "Santorini", "date": "2026-07-08",
            "schedule": "Docks 07:00, all aboard 18:30",
            "meals": [
                {"meal": "breakfast", "situation": "tour", "tour": "Oia Sunset Villages", "tour_start": "08:15"},
                {"meal": "lunch", "situation": "free"},
                {"meal": "dinner", "situation": "booked", "restaurant": "Metaxi Mas", "time": "19:30"},
            ],
        },
        # (substring that MUST appear, list of substrings that must NOT) per meal
        "expect": {
            "breakfast": {"any": ["8:15", "08:15"], "none": []},
            "dinner": {"any": ["Metaxi Mas"], "none": []},
        },
    },
    {
        "name": "aboard before docking",
        "day": {
            "port": "Kusadasi", "date": "2026-07-09",
            "schedule": "Docks 11:00, all aboard 19:00",
            "meals": [
                {"meal": "breakfast", "situation": "aboard"},
                {"meal": "lunch", "situation": "free"},
                {"meal": "dinner", "situation": "aboard"},
            ],
        },
        "expect": {
            # breakfast is before docking → must point at the ship, must NOT send them ashore
            "breakfast": {"any": ["ship", "aboard", "board", "onboard"],
                          "none": ["in port", "in town", "ashore", "restaurant in"]},
        },
    },
]


def check_day_summary(url):
    print("\nDAY SUMMARY")
    ok = True
    for case in DAY_CASES:
        try:
            data = _post(url, {"context": "day_summary", "day": case["day"]})
        except Exception as exc:  # noqa: BLE001
            print(f"   FAIL {case['name']}: request error {str(exc)[:80]}")
            ok = False
            continue
        summary = data.get("summary") or {}
        for meal, rule in case["expect"].items():
            text = (summary.get(meal) or "").lower()
            if not text:
                print(f"   FAIL {case['name']} / {meal}: empty")
                ok = False
                continue
            if rule["any"] and not any(s.lower() in text for s in rule["any"]):
                print(f"   FAIL {case['name']} / {meal}: missing any of {rule['any']}\n        got: {text!r}")
                ok = False
                continue
            bad = [s for s in rule["none"] if s.lower() in text]
            if bad:
                print(f"   FAIL {case['name']} / {meal}: contains forbidden {bad}\n        got: {text!r}")
                ok = False
                continue
            print(f"   PASS {case['name']} / {meal}")
    return ok


# ----------------------------------------------------------------- chat picks ----
def check_chat(url, restaurants):
    print("\nCHAT PICKS")
    by_port = defaultdict(list)
    for r in restaurants:
        by_port[r["port_key"]].append(r)
    ids_by_port = {p: {x["id"] for x in rs} for p, rs in by_port.items()}

    cases = [
        {"port": "venice", "q": "Where can we get good Indian food?",
         "constraint": lambda r: (r.get("cuisine") or "").lower() == "indian"},
        {"port": "athens", "q": "Somewhere with solid vegetarian options for dinner",
         "constraint": lambda r: r.get("veg_options") or r.get("fully_veg")},
        {"port": "venice", "q": "A special Michelin dinner to celebrate",
         "constraint": lambda r: bool(r.get("michelin"))},
    ]
    ok = True
    for c in cases:
        pool = by_port.get(c["port"], [])
        if not pool:
            continue
        payload = {"context": "restaurants", "port": c["port"], "port_label": c["port"].title(),
                   "question": c["q"], "restaurants": pool}
        try:
            data = _post(url, payload)
        except Exception as exc:  # noqa: BLE001
            print(f"   FAIL [{c['port']}] {c['q']!r}: request error {str(exc)[:80]}")
            ok = False
            continue
        picks = data.get("picks") or []
        answer = (data.get("answer") or "").strip()
        if not picks:
            print(f"   FAIL [{c['port']}] {c['q']!r}: no picks returned")
            ok = False
            continue
        valid_ids = ids_by_port[c["port"]]
        bad_ids = [p for p in picks if p not in valid_ids]
        if bad_ids:
            print(f"   FAIL [{c['port']}] {c['q']!r}: picks not in port: {bad_ids[:3]}")
            ok = False
            continue
        by_id = {x["id"]: x for x in pool}
        honored = sum(1 for p in picks if c["constraint"](by_id[p]))
        if honored == 0:
            names = [by_id[p]["name"] for p in picks]
            print(f"   FAIL [{c['port']}] {c['q']!r}: no pick honors the constraint; got {names}")
            ok = False
            continue
        if not answer:
            print(f"   FAIL [{c['port']}] {c['q']!r}: empty answer")
            ok = False
            continue
        print(f"   PASS [{c['port']}] {c['q']!r}: {honored}/{len(picks)} picks honor constraint")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL, help="ask endpoint (prod by default)")
    ap.add_argument("--sample", type=int, default=15, help="freshness sample size")
    ap.add_argument("--skip-freshness", action="store_true")
    ap.add_argument("--skip-ai", action="store_true")
    args = ap.parse_args()

    restaurants = load_catalog()
    results = {}
    if not args.skip_freshness:
        results["freshness"] = check_freshness(restaurants, args.sample)
    if not args.skip_ai:
        results["day_summary"] = check_day_summary(args.url)
        results["chat"] = check_chat(args.url, restaurants)

    print("\n" + "=" * 40)
    for k, v in results.items():
        print(f"   {'PASS' if v else 'FAIL'}  {k}")
    ok = all(results.values())
    print(f"{'ALL CHECKS PASSED' if ok else 'SOME CHECKS FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
