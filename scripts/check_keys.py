#!/usr/bin/env python3
"""Preflight: verify every external credential the app depends on actually WORKS.

Catches the failure mode that bit us — a rotated/dead key that's present but
returns 401 — instead of discovering it by accident mid-build. Each check makes
the cheapest possible authenticated call (no tokens / free tier) and reports
PASS / FAIL / WARN. Exits non-zero if any REQUIRED key is broken, so it can gate
deploys and run in CI.

  python3 scripts/check_keys.py            # all checks
  python3 scripts/check_keys.py --offline  # presence only, no network
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hotel_agent  # noqa: F401,E402  triggers load_dotenv()

TIMEOUT = 15


def _key(name: str) -> str:
    return os.environ.get(name, "").strip()


def check_openai() -> tuple[str, str]:
    """GET /v1/models — costs 0 tokens; 401 means the key is dead."""
    k = _key("OPENAI_API_KEY")
    if not k:
        return "FAIL", "missing"
    try:
        r = requests.get("https://api.openai.com/v1/models",
                         headers={"Authorization": f"Bearer {k}"}, timeout=TIMEOUT)
        if r.status_code == 200:
            return "PASS", f"valid ({len(r.json().get('data', []))} models)"
        if r.status_code == 401:
            return "FAIL", "401 invalid_api_key (rotated/revoked — regenerate)"
        return "FAIL", f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return "FAIL", f"error: {e}"


def check_google_places() -> tuple[str, str]:
    """Minimal Text Search — free tier; bad key returns 400/403."""
    k = _key("GOOGLE_PLACES_API_KEY")
    if not k:
        return "FAIL", "missing"
    try:
        r = requests.post(
            "https://places.googleapis.com/v1/places:searchText",
            headers={"Content-Type": "application/json", "X-Goog-Api-Key": k,
                     "X-Goog-FieldMask": "places.id"},
            json={"textQuery": "coffee", "maxResultCount": 1}, timeout=TIMEOUT)
        if r.status_code == 200:
            return "PASS", "valid"
        return "FAIL", f"HTTP {r.status_code}: {r.json().get('error', {}).get('status', '')}"
    except Exception as e:  # noqa: BLE001
        return "FAIL", f"error: {e}"


def check_serpapi() -> tuple[str, str]:
    k = _key("SERPAPI_KEY")
    if not k:
        return "WARN", "missing (only needed for flight/hotel refresh)"
    try:
        r = requests.get("https://serpapi.com/account", params={"api_key": k}, timeout=TIMEOUT)
        if r.status_code == 200:
            left = r.json().get("total_searches_left", "?")
            return "PASS", f"valid ({left} searches left)"
        return "FAIL", f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return "FAIL", f"error: {e}"


# (name, fn, required)
CHECKS = [
    ("OPENAI_API_KEY", check_openai, True),
    ("GOOGLE_PLACES_API_KEY", check_google_places, True),
    ("SERPAPI_KEY", check_serpapi, False),
]


def run(offline: bool = False) -> int:
    print("Credential preflight\n" + "-" * 52)
    failed_required = 0
    for name, fn, required in CHECKS:
        if offline:
            present = bool(_key(name))
            status = "PASS" if present else ("FAIL" if required else "WARN")
            detail = "present" if present else "missing"
        else:
            status, detail = fn()
        if status == "FAIL" and required:
            failed_required += 1
        mark = {"PASS": "ok  ", "WARN": "warn", "FAIL": "FAIL"}[status]
        print(f"  [{mark}] {name:24} {detail}")
    print("-" * 52)
    if failed_required:
        print(f"{failed_required} required credential(s) BROKEN — fix before deploying.")
        return 1
    print("All required credentials OK.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="presence only, no network")
    args = ap.parse_args()
    return run(offline=args.offline)


if __name__ == "__main__":
    raise SystemExit(main())
