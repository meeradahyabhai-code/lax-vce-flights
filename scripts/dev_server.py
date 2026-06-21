#!/usr/bin/env python3
"""Local dev server: serves public/ AND runs /api/ask (the chat) locally.

A plain `python -m http.server` can't execute the Vercel Python serverless
functions, so the restaurant/flight chat is dead locally. This wires /api/ask
to the real ask.build_messages logic + a direct OpenAI call (needs a valid
local OPENAI_API_KEY — run scripts/check_keys.py first), mirroring prod.

  python3 scripts/dev_server.py   ->  http://localhost:8099
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))
import hotel_agent  # noqa: F401,E402  load_dotenv
import ask  # noqa: E402
import restaurant_finder as rfinder  # noqa: E402

PUBLIC = ROOT / "public"
PORT = 8099
KEY = os.environ.get("OPENAI_API_KEY", "").strip()
_REST_CACHE: dict = {}  # in-memory dynamic-search cache so local hits don't re-call Places


def _ask(payload: dict) -> dict:
    context = payload.get("context", "flights")
    is_rest = context == "restaurants"
    is_day = context == "day_summary"
    question = (payload.get("question") or "").strip()
    if not question and not is_day:
        return {"error": "Missing question"}
    system_prompt, user_msg = ask.build_messages(question, payload)
    body = {"model": ask.MODEL, "max_tokens": 320 if (is_rest or is_day) else 180,
            "temperature": 0.4 if is_day else 0.3,
            "messages": [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": user_msg}]}
    if is_rest or is_day:
        body["response_format"] = {"type": "json_object"}
    r = requests.post("https://api.openai.com/v1/chat/completions",
                      headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
                      json=body, timeout=20)
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"].strip()
    if is_day:
        try:
            parsed = json.loads(content)
            return {"summary": {"breakfast": (parsed.get("breakfast") or "").strip(),
                                "lunch": (parsed.get("lunch") or "").strip(),
                                "dinner": (parsed.get("dinner") or "").strip()}}
        except (ValueError, TypeError):
            return {"summary": {}}
    if is_rest:
        slice_ = payload.get("restaurants", [])[:80]
        ref_to_id = {i + 1: x.get("id") for i, x in enumerate(slice_)}
        try:
            parsed = json.loads(content)
            picks = []
            for ref in (parsed.get("picks") or [])[:5]:
                try:
                    rid = ref_to_id.get(int(ref))
                except (ValueError, TypeError):
                    rid = None
                if rid and rid not in picks:
                    picks.append(rid)
            return {"answer": (parsed.get("answer") or "").strip(), "picks": picks}
        except (ValueError, TypeError):
            return {"answer": content, "picks": []}
    return {"answer": content}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quieter
        pass

    def do_POST(self):
        if self.path.split("?")[0] == "/api/ask":
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n) or b"{}")
                out = _ask(payload)
                code = 200
            except Exception as e:  # noqa: BLE001
                out, code = {"error": str(e)[:200]}, 500
            self._send(code, out)
            return
        self._send(404, {"error": "not found"})

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse
        path = self.path.split("?")[0]

        # --- dynamic restaurant search + photo proxy (mirrors api/restaurants.py) ---
        if path == "/api/restaurants":
            q = parse_qs(urlparse(self.path).query)
            ref = (q.get("photo", [""])[0]).strip()
            if ref:  # photo proxy mode: /api/restaurants?photo=places/.../photos/...
                try:
                    w = max(80, min(1200, int(q.get("w", ["520"])[0])))
                except (ValueError, TypeError):
                    w = 520
                if not ref.startswith("places/") or "/photos/" not in ref:
                    self._send(400, {"error": "bad ref"}); return
                try:
                    r = requests.get(f"https://places.googleapis.com/v1/{ref}/media",
                                     params={"maxWidthPx": w, "key": os.environ.get("GOOGLE_PLACES_API_KEY")
                                             or os.environ.get("GOOGLE_MAPS_API_KEY")}, timeout=15)
                    self.send_response(r.status_code if r.status_code != 200 else 200)
                    self.send_header("Content-Type", r.headers.get("Content-Type", "image/jpeg"))
                    self.send_header("Content-Length", str(len(r.content)))
                    self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                    self.end_headers()
                    self.wfile.write(r.content)
                except Exception as e:  # noqa: BLE001
                    self._send(502, {"error": str(e)[:120]})
                return
            port = q.get("port", ["venice"])[0].lower()
            try:
                radius = int(float(q.get("radius_mi", ["10"])[0]))
            except (ValueError, TypeError):
                radius = 10
            ckey = f"{port}:{radius}"
            if ckey not in _REST_CACHE:
                _REST_CACHE[ckey] = rfinder.search_area(port, radius_mi=radius, sleep=time.sleep)
            self._send(200, _REST_CACHE[ckey])
            return

        rel = path.lstrip("/") or "index.html"
        f = PUBLIC / rel
        if f.is_dir():
            f = f / "index.html"
        if not f.exists() or not str(f.resolve()).startswith(str(PUBLIC.resolve())):
            # Data/API paths that don't exist locally must 404 cleanly (so loaders that
            # expect JSON don't choke on an HTML SPA-fallback). Only navigation routes fall back.
            if path.startswith("/api/") or "." in path.rsplit("/", 1)[-1]:
                self._send(404, {"error": "not found (local dev)"})
                return
            f = PUBLIC / "index.html"  # SPA fallback for routes
        ctype = {"html": "text/html", "js": "application/javascript", "css": "text/css",
                 "json": "application/json", "jpg": "image/jpeg", "png": "image/png",
                 "mp4": "video/mp4", "svg": "image/svg+xml", "webmanifest": "application/manifest+json"}.get(
                     f.suffix.lstrip("."), "application/octet-stream")
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send(self, code, obj):
        out = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


if __name__ == "__main__":
    print(f"Dev server (static + /api/ask) on http://localhost:{PORT}")
    if not KEY:
        print("WARNING: no OPENAI_API_KEY — chat will error.")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
