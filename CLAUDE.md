# CLAUDE.md — lax-vce-flights

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
