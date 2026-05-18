# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Family vacation planner for a cruise trip (LAX/AKL/ATL/YVR to Venice, return from Istanbul). Single-page vanilla HTML/JS/CSS frontend with Python serverless backend on Vercel. Flight data is pre-generated offline; hotel data is cached on first search.

## Commands

```bash
# Run all tests (506 tests, ~0.1s)
python3 -m pytest

# Run a single test file
python3 -m pytest test_flight_agent.py

# Run a specific test
python3 -m pytest test_flight_agent.py::TestClassName::test_name -v

# Refresh flight data (42 SerpAPI calls, REQUIRES APPROVAL for --force)
python3 scripts/refresh_flights.py

# Fetch hotel star ratings (free, Veneto open data)
python3 scripts/fetch_hotel_stars.py

# Deploy (free, no API calls triggered)
vercel --prod

# Sync frontend after editing (MUST do after any web/index.html change)
cp web/index.html public/index.html
```

## Architecture

### Data flow

```
Flights:  scripts/refresh_flights.py → data/flights_cache.json → api/flights.py → browser
          (42 SerpAPI calls, 48h)       (static JSON, ~250KB)     (reads file, $0)

Hotels:   browser request → api/hotels.py → check data/hotels_cache_*.json
                                           → fresh? serve cached ($0)
                                           → stale? SerpAPI (1 call) + Google Places (free) → cache → serve
```

### Key modules

- **`flight_agent.py`** (1726 lines) — core pipeline: SerpAPI search, Skyscanner search, normalize, dedup, filter, score, fare labeling. All business logic lives here; API endpoints are thin wrappers.
- **`hotel_agent.py`** (893 lines) — hotel search pipeline: SerpAPI Hotels + Google Places for ratings/reviews. Multi-city support (Venice, Ravenna, Istanbul). Credit card program data (Amex FHR/THC, Chase LPB) for perks matching.
- **`serpapi_guard.py`** — rate limiter for ad-hoc SerpAPI calls. 24h rolling window, email alerts on threshold breach. All non-scheduled API calls must go through this.
- **`web/index.html`** (6973 lines) — entire frontend in one file. Always edit here, then copy to `public/index.html`.

### API endpoints (Vercel serverless, all in `api/`)

| Endpoint | Method | Cost | Purpose |
|----------|--------|------|---------|
| `flights.py` | GET | $0 | Serve cached flight data |
| `hotels.py` | GET | 0-1 SerpAPI | Hotel search with 48h cache |
| `hotel_search.py` | GET | 0-1 SerpAPI | Hotel search variant |
| `summary.py` | POST | OpenAI | AI flight briefing (4 bullets) |
| `points.py` | POST | OpenAI | Points/miles strategy for a flight |
| `hotel-points.py` | POST | OpenAI | Points strategy for a hotel |
| `multicity.py` | GET | 2 SerpAPI | Multi-city search (needs approval) |
| `parse_screenshot.py` | POST | OpenAI | Extract flight details from booking screenshot |
| `parse_hotel.py` | POST | OpenAI | Parse hotel property details |

### Conventions

- Vercel serverless handlers use `BaseHTTPRequestHandler` with a `handler` class
- All SerpAPI calls outside scheduled refresh must use `serpapi_guard.check_serpapi_budget()` before calling and `log_serpapi_calls()` after
- CDN caching: `s-maxage=172800, stale-while-revalidate=172800` for flights; 48h file-based cache for hotels
- Origins: LAX, AKL (allows 2 stops), ATL, YVR. Defined in `flight_agent.ROUTES`
- Trip dates: depart 2026-06-28/29/30, return 2026-07-13/14/15
- Tests are at project root (`test_*.py`), no test framework config needed

## Spending Rules (READ THIS FIRST)

There are three categories. Follow them exactly.

### FREE — no approval needed
- Vercel deploys (flight data is static, deploys cost $0 in API calls)
- Google Places API (free tier: 5,000 Text Search + 5,000 Place Details per month)
- Google Sheets / Apps Script (free within Google quotas)
- OpenAI API calls under $10/month total (Points AI, Summary, Screenshot parse)
- Reading/writing local files, running tests, git operations, editing code
- Veneto open data (`scripts/fetch_hotel_stars.py`)

### AUTO-APPROVED — runs on schedule, no approval needed
- `scripts/refresh_flights.py` — 42 SerpAPI calls, every 48 hours
  - Built-in 47-hour cooldown prevents accidental double-runs
  - Validates data before writing, atomic writes, call count alerts
- `/api/hotels` — 1 SerpAPI call per unique date combo, cached for 48 hours
  - First user search triggers SerpAPI + saves to `data/hotels_cache_*.json`
  - All subsequent requests within 48h serve the cached file (0 SerpAPI calls)
  - On error, serves stale cache as fallback (users always see data)

### REQUIRES APPROVAL — must alert user, show cost, wait for explicit OK
- Ad-hoc `scripts/refresh_flights.py --force` (42 SerpAPI calls = ~$0.63)
- `/api/multicity` (2 SerpAPI calls per search, no caching yet)
- Any NEW paid API integration
- Any action that could push OpenAI spend over $10/month
- Anything else that costs money not listed above

### How to alert for approval
- State exactly what will be called and how many times
- State the dollar cost
- If user is not present, email mdahya@gmail.com for approval
- Do NOT proceed until you receive explicit approval

## SerpAPI Budget

**Plan**: Developer — 5,000 searches/month at $75/month ($0.015/search).

| Endpoint | SerpAPI calls | Caching |
|----------|--------------|---------|
| `scripts/refresh_flights.py` | 42 | Every 48h, saves to `data/flights_cache.json` |
| `/api/flights` | 0 | Reads static file only |
| `/api/hotels` | 1 per unique date combo | Saves to `data/hotels_cache_*.json`, 48h TTL |
| `/api/multicity` | 2 | CDN only (needs approval per search) |

Monthly budget: ~630 flights + ~15 hotel searches = ~645 calls. Well within 5,000.

## Architecture

### Flights (dataset-first)
```
scripts/refresh_flights.py  →  data/flights_cache.json  →  api/flights.py  →  browser
(42 SerpAPI calls, 48h)        (static file, ~250KB)       (reads file, $0)
```
Visitors and deploys NEVER trigger SerpAPI.

### Hotels (cache-on-first-search)
```
User clicks Search Hotels
  → api/hotels.py checks data/hotels_cache_*.json
    → fresh (<48h)?  → serve cached file ($0)
    → stale/missing? → SerpAPI (1 call) + Google Places (free) → save to cache → serve
    → error?         → serve stale cache as fallback (users always see data)
```

### Hotel Stars (official government data)
```
scripts/fetch_hotel_stars.py  →  data/hotel_stars_venice.json  →  hotel_agent.py
(Veneto open data, free)         (457 hotels, 1-5★)               apply_official_stars()
```

### Safeguards
1. **Flight refresh cooldown** — 47h minimum between runs
2. **Hotel cache TTL** — 48h per date combo, stale fallback on error
3. **Payload validation** — checks all origins, min flight count before writing
4. **Atomic writes** — temp file then rename (no corrupt data on crash)
5. **Call count alert** — warns if SerpAPI calls exceed expected 42
