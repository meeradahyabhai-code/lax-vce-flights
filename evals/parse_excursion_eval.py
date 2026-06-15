"""Eval harness for the excursion screenshot parser (LLM feature).

Unit tests cover the deterministic logic; THIS measures extraction accuracy of
the vision model against golden examples. It is run on demand (it makes real
OpenAI calls via the deployed endpoint), not in the normal test run.

How to use:
  1. Drop real excursion booking screenshots into evals/fixtures/  (e.g. dubrovnik_walk.png)
  2. Add a case below: the filename + the fields you expect extracted.
  3. Run:  python evals/parse_excursion_eval.py [--url https://<deployment>/api/parse_excursion]
     (defaults to the production endpoint)

Scoring: for each case, every expected field must match (case-insensitive,
whitespace-trimmed). Prints per-field diffs and an overall pass rate.
"""

import argparse
import base64
import json
import os
import sys
import urllib.request

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
DEFAULT_URL = "https://dfc-2026.vercel.app/api/parse_excursion"

# Golden cases: filename in evals/fixtures/ -> expected extracted fields.
# Only list the fields you want to assert; others are ignored.
CASES = [
    # {
    #   "image": "dubrovnik_walk.png",
    #   "port_date": "2026-07-04",
    #   "expect": {
    #     "title": "Old Town Walking Tour",
    #     "start_time": "09:00",
    #     "duration": "3 hours",
    #     "price": "45",
    #     "currency": "EUR",
    #   },
    # },
]


def _norm(v):
    return ("" if v is None else str(v)).strip().lower()


def run_case(url, case):
    path = os.path.join(FIXTURES, case["image"])
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    payload = json.dumps({"image": "data:image/png;base64," + b64,
                          "port_date": case.get("port_date")}).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        # The endpoint enforces an Origin allowlist; send the production origin.
        "Origin": "https://dfc-2026.vercel.app",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    parsed = data.get("parsed", {})
    misses = {k: (parsed.get(k), v) for k, v in case["expect"].items() if _norm(parsed.get(k)) != _norm(v)}
    return parsed, misses


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    args = ap.parse_args()

    if not CASES:
        print("No eval cases yet. Add screenshots to evals/fixtures/ and cases to CASES[].")
        return 0

    passed = 0
    for case in CASES:
        try:
            parsed, misses = run_case(args.url, case)
        except Exception as exc:
            print(f"FAIL {case['image']}: error {exc}")
            continue
        if not misses:
            passed += 1
            print(f"PASS {case['image']}")
        else:
            print(f"FAIL {case['image']}")
            for field, (got, want) in misses.items():
                print(f"   {field}: got {got!r}  want {want!r}")
    print(f"\n{passed}/{len(CASES)} cases passed")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
