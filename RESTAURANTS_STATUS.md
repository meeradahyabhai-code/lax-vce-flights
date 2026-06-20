# Restaurants feature — requirements tracker

Single source of truth so requirements aren't lost. Update on every change.
RULE: all UI goes through the design system (`:root` tokens + existing component
classes) — never invent ad-hoc styles. When reporting "ready," always list "Still to build."

## DONE (built + verified, LOCAL only — not deployed)
- [x] Shop → Restaurants finder (port pills, browse)
- [x] Data: 435 restaurants, bar = rating ≥4.0 AND ≥100 reviews, no cap
- [x] Ratings pulled live from Google Places (verified accurate)
- [x] Real cuisine per card, separate from formality level
- [x] Indian cuisine included (+ filter)
- [x] "Best in {port}" crowned + "why" reason
- [x] Michelin badge (red MICHELIN ★ / Bib)
- [x] Collapsed → expand card
- [x] Conversational chat search (answer + real picks)
- [x] Veg = filled green leaf
- [x] Name SEO-suffix trim
- [x] Perf: lazy card images + deferred home video
- [x] Chat input clears on port switch
- [x] Add-restaurant in Our Trip: modal (from-list / screenshot / manual), required fields,
      who's-going, join/delete, Supabase (kind='restaurant' + meal); Supabase migration run
- [x] TikTok deep-link (search restaurant)
- [x] Removed per-card map button

## STILL TO BUILD (outstanding requirements)
- [x] **Recommended** — DONE. Heart-less badge + "Recommended to you?" → "{by} recommended this to {to}".
      Optimistic + Supabase `restaurant_recs` (run SQL in this file's notes to persist/share).
- [x] **Save / favorite** — DONE. Heart on card (localStorage), gold when saved, + Saved filter chip.
- [x] **AI-generated day summary** — DONE. `ask.py` day_summary context; client sends the deterministic
      per-meal SITUATION (booked/tour/free/aboard/pre) + ship schedule, AI phrases it naturally with options
      (e.g. "tour starts 8:15am, light breakfast on the ship first"). Reads restaurant bookings as meals
      (Barolo = dinner). Instant deterministic fallback, upgrades to AI in background, cached per port+bookings.
- [x] **Tests & evals** — DONE.
      - `test_restaurants_data.py` (12 tests, in the normal suite, offline): catalog size, no dup ids,
        required fields, ratings in range, known ports, cuisine-is-not-a-level, boolean veg flags, valid
        michelin tiers, profile/vibe present, photos exist on disk, every port ≥8, Indian ≥5.
        (Caught + fixed 2 "Fine Dining"-as-cuisine entries on first run.)
      - `evals/restaurants_eval.py` (on-demand, network): rating freshness vs live Google Places by
        place_id; AI day-summary respects each per-meal SITUATION; chat picks resolve to real ids in the
        asked port + honor hard constraints (Indian/veg/Michelin). Run: `python3 evals/restaurants_eval.py
        --url http://localhost:8099/api/ask`. Last run: freshness 15/15 within ±0.3, day_summary + chat all PASS.
- [x] **Generic "Restaurant" cuisines** — DONE. `scripts/refine_generic_cuisine.py` re-derived all 83
      from review material first, local cuisine as fallback (taverna in Athens = Greek, osteria in Venice =
      Venetian). 0 generic remaining; 35 distinct cuisines. Test `test_cuisine_is_not_a_level` guards it.

## DONE since last update
- [x] **MAP VIEW** — List/Map toggle; pins + names-on-zoom + popups (Leaflet); refined pin/label design
- [x] Design-system fixes: add button = dashed excursion style; browse = outline button; filters = pill component
- [x] **Add-restaurant SAVE fixed** — duration/price/currency NOT NULL → send empty; persists to Supabase
- [x] Fixed double-modal bug (add button fired both excursion + restaurant handlers); modal closes after save
- [x] Manual entry: time required + red-field validation
- [x] Our Trip section headers ("Excursions / Tours", "Where to eat") prominent (serif + divider)
- [x] Restaurant Join/Delete buttons styled to match excursions (no raw browser buttons)
- [x] Map pin popup → "See details & book" jumps to that card in the List view, expanded

## STILL TO BUILD (added from latest feedback)
- [x] **Filters UX redesign** — DONE. Calm "Filters" bar + animated "+" that smoothly reveals a grouped
      panel (Cuisine / Price / Dietary & more), active count, design-system pills. Ritz "+" feel.
- [x] **Shop chooser rework** — DONE. Editorial Aman/Belmond treatment (gold eyebrow, serif italic title,
      hairline-divided rows, monoline icons, sliding arrow) instead of boxy centered cards.
- [ ] (LATER) Upgrade Flights + Hotels tabs (and their internal screens) to match this vibe
- [ ] AI day summary must give OPTIONS when a meal is ambiguous, e.g. "Your Ancient Lindos tour starts at
      8:15am. Grab a light breakfast on the ship, then ask the guide for recommendations if still hungry."
- [ ] (LATER) Upgrade Flights + Hotels tabs to match the restaurants quality

## NOTES / DECISIONS
- Curation bar: rating ≥4.0 AND ≥100 reviews, no cap; Michelin + Indian always included.
- Storage: restaurants live in the `excursions` table tagged `kind='restaurant'` + `meal`; share `excursion_joins`.
- Design tokens: --azure #1a3a6b, --gold #b8953a, --lagoon #0d6e8a, --ink, --paper #faf8f3;
  serif=Cormorant, sans=DM Sans; weights 400/500/600; type scale --fs-*; spacing --space-*.
- NOTHING is deployed. Prod still has the original 96-restaurant finder. Everything else is local.
