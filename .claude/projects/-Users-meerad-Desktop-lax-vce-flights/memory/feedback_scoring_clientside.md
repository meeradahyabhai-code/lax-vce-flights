---
name: scoring_clientside
description: Scoring/sorting/filtering must happen client-side after API data is received, never baked into cached API responses
type: feedback
---

Scoring, sorting, and filtering of flight data should happen CLIENT-SIDE after receiving raw flight data from the API — not server-side before caching. This way, scoring logic changes don't require cache invalidation or new SerpAPI calls.

**Why:** SerpAPI calls are expensive (42 per cold fetch, 5K/month budget). If scores are baked into the cached API response, any scoring change requires cache invalidation → fresh API call → SerpAPI spend. Separating raw data from scoring means we can update ranking logic freely without touching the cache.

**How to apply:** When building any feature that ranks, sorts, or filters data — do it in the frontend JS after the API response arrives. The API should return raw data (prices, times, airlines, coordinates). The client applies scoring bonuses, filters, and sort order. Exception: hotels have a Google Places pagination constraint that requires server-side scoring.
