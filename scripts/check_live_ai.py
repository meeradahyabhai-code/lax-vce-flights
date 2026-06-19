#!/usr/bin/env python3
"""Prod health check: confirm the LIVE app's OpenAI features actually work.

The prod OpenAI key lives in Vercel (not in CI secrets), so the only way to
verify it from outside is to exercise a live endpoint. /api/summary SILENTLY
FALLS BACK to canned text on any OpenAI error (HTTP 200), so a dead key would
go unnoticed if we only checked the status code. This sends a distinctive
request and asserts the response is real AI output, not the fallback.

Exits non-zero on failure so a scheduled GitHub Action turns red (and emails)
the moment the prod key rotates or dies.

  python3 scripts/check_live_ai.py [--base https://dfc-2026.vercel.app]
"""
from __future__ import annotations

import argparse
import sys

import requests

DEFAULT_BASE = "https://dfc-2026.vercel.app"
FALLBACK_MARKER = "Flight data updated daily"  # summary.py's canned fallback
TIMEOUT = 30


def check_summary(base: str) -> tuple[bool, str]:
    payload = {
        "flights": [{"primary_airline": "Delta", "search_date": "2026-06-28",
                     "departure_time": "2026-06-28T10:00", "arrival_time": "2026-06-29T14:00",
                     "price": 950, "stops": 1, "score": 82}],
        "origin": "LAX", "direction": "outbound",
    }
    try:
        r = requests.post(f"{base}/api/summary", json=payload, timeout=TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return False, f"request error: {e}"
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}"
    summary = (r.json() or {}).get("summary", "")
    if not summary:
        return False, "empty summary"
    if FALLBACK_MARKER.lower() in summary.lower():
        return False, "FALLBACK text returned -> prod OpenAI key is dead/misconfigured"
    # positive signal: real generation reflects the input
    if "delta" not in summary.lower() and "950" not in summary:
        return False, f"unexpected output (no input echo): {summary[:80]!r}"
    return True, f"live AI OK: {summary[:70].strip()}..."


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    args = ap.parse_args()
    print(f"Prod AI health check -> {args.base}")
    ok, detail = check_summary(args.base)
    print(f"  [{'ok  ' if ok else 'FAIL'}] /api/summary  {detail}")
    if not ok:
        print("PROD AI IS DOWN — the OpenAI key in Vercel likely needs refreshing.")
        return 1
    print("Prod AI healthy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
