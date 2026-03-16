# CLAUDE.md — lax-vce-flights

## SerpAPI Budget Rules (MANDATORY)

**Plan**: Developer — 5,000 searches/month at $75/month.

### Call counts per endpoint (cold cache)
- `/api/flights`: **42 SerpAPI calls** (4 origins x 2 directions x 3 dates x ~2 stop levels). Auto-triggers on page load.
- `/api/hotels`: **1 SerpAPI call**. User-triggered only.
- `/api/multicity`: **2 SerpAPI calls** per search. User-triggered only.

### Deployment rules
1. **Every Vercel deploy invalidates CDN cache.** Opening the site after deploy triggers a full 42-call flight search.
2. **BEFORE any deploy that could trigger SerpAPI calls, you MUST:**
   - Tell the user exactly how many SerpAPI calls will be used
   - Wait for explicit approval before proceeding
3. **For hotel-only or UI-only changes:** Warm only `/api/hotels` via curl (1 call). Do NOT open the browser (which triggers 42 flight calls).
4. **Never undercount.** Include flight auto-fetch in any estimate where the browser will be opened.
5. **Never warm `/api/flights` or open the site without user approval.**

### CDN Cache
- All API endpoints: `s-maxage=172800` (48h cache)
- Cache is busted on every production deploy
- Repeat visits within 48h = 0 SerpAPI calls (served from edge)
