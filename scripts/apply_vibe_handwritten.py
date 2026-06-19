#!/usr/bin/env python3
"""Apply hand-written, on-voice vibe lines to restaurants.json (all 3 copies).

These were authored from the real Google review snippets in the concierge
voice (Aman/Belmond): warm, quiet, grounded, no hype/emoji. The curate script
keeps its OpenAI path for future bulk regeneration once the local key is live;
this just fills the field now without depending on that key.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOGS = [ROOT / "data" / "restaurants.json",
            ROOT / "public" / "restaurants.json",
            ROOT / "web" / "restaurants.json"]

VIBE = {
    # Venice
    "ChIJ7Xgj8duxfkcRPg0h9g1v5DI": "Steps from Piazza San Marco, with quick, gracious service and a terrace for pizza, pasta and seafood; book ahead.",
    "ChIJPSXhncuxfkcRcUc_CheC_AU": "A tiny fresh-pasta counter beloved for its quality and value; pair a box with an Aperol Spritz canal-side.",
    "ChIJlfaQjuuvfkcRcd9od0yrL0E": "Warm, family-style Venetian dining where a welcome prosecco sets the tone and the service stays attentive.",
    "ChIJEw7bT8-xfkcRBdrCqYE8uAY": "A locals' standby for Venetian seafood, pasta and well-chosen wine; the meal hotel desks quietly recommend.",
    "ChIJd27KjdmxfkcRwotzoO-LGYE": "Rustic and lively, with generous meat and fish plates and hospitable service; come for the convivial buzz.",
    "ChIJDZCOczuxfkcRCt-tGyZkqCY": "An unfussy, busy spot for straightforward Venetian cooking; the heated terrace stays inviting even off-season.",
    "ChIJSZprC8ixfkcR4AuLYzIb53k": "Seafood and pasta on a sunny canal-side terrace; ask for a patio table and let the gondolas drift past.",
    "ChIJ8fKsjKaxfkcRV_jHLlQql3Y": "Refined plates on a terrace over the canal; book the outdoor table for gondolas gliding by at dusk.",
    "ChIJt62djN6xfkcRtBKtGbOFQI4": "Creative, authentic Italian in an elegant-rustic room with pavement tables; fresh cooking and genuinely warm service.",
    "ChIJrUlJPGixfkcRkUw1oAwemf4": "A by-chance discovery many call their best meal in Venice; lovely atmosphere and a standout mixed fried seafood.",
    "ChIJAYrmAtqxfkcRcn-5TVq_z34": "Elegant Venetian cooking with a historic streak and over sixty wines by the glass; ideal for a considered dinner.",
    "ChIJjU40vPa1fkcRxYna4FVWOLA": "Delicious, fresh Venetian plates and a quiet patio; the food outshines the understated setting.",
    # Dubrovnik
    "ChIJh3rhADMLTBMRdlb5Yrxn5JE": "A grand harbour-side room that rises above the Old Town's tourist tables; reliable quality, polished service.",
    "ChIJS3Ubws50TBMRGOniM6PKs9c": "Cable-car-top views over the walled city; book well ahead for a railing table at golden hour.",
    "ChIJLwsjbi0LTBMRAOndGfCUBpk": "A long-standing favourite by Pile Gate for fresh, well-made Dalmatian dishes and a view of the walls.",
    "ChIJpVMyNjILTBMRmJ26-nlGL2Q": "A hidden courtyard sheltered from the crowds; photogenic at dusk, with attentive service and assured cooking.",
    "ChIJSUouz-oLTBMREj_QuN1yOxs": "A hidden gem in the Old Town with a soulful atmosphere; creative, healthy plates and a generous host.",
    "ChIJ118j9jILTBMRqfI1nH-WkPU": "Seasonal Italian and local wine on a relaxed terrace; warm, smiling service that lightens the day.",
    "ChIJ_-nWiOV1TBMR04ncSCm7D8Q": "Modern, airy and right on the water with views of the walls; great music and confident grilling.",
    "ChIJl3WKb0oLTBMRXXdP0WIvHOI": "An Old-Town favourite for a lovely, unhurried dinner; attentive service and consistently delicious plates.",
    "ChIJsbpCiPN1TBMRV6k-TSUSSI0": "A table hoisted skyward for a playful tasting menu; equal parts thrill and view, best in fair weather.",
    "ChIJ_4a7ljILTBMRatkmgAgVM-U": "Reimagined Dalmatian small plates and cocktails on an old-world terrace; the smoked mussels are a quiet surprise.",
    "ChIJ2YNHbS0LTBMRFqrrsFW-vOc": "Dubrovnik's grande-dame fine dining by the fort; rare for delivering both the view and the plate.",
    "ChIJB72hSdILTBMRA5f4ECiu-iY": "Mediterranean and Dalmatian cooking in a rustic stone-and-beam room; genuinely excellent, beyond the hotel setting.",
    # Bar
    "ChIJ46U_YlTZTRMRv-WdyaEC6fE": "A peaceful table near Lake Skadar known for its carp and warm welcome; worth the short trip for the setting.",
    "ChIJsdbcL3l0ThMRBiC1aOasVj4": "Set against Stari Bar's fortress walls, with generous portions, fair prices and genuinely splendid service.",
    "ChIJL-8xA6V3ThMRabL9jT5piKc": "A rare beach-side table where the view and the food both deliver; come for a long, sunny lunch.",
    "ChIJu7IyFgZ0ThMRugQXtDujRXw": "Five-euro cevapi regulars call the best around; unpretentious, delicious and easy on the wallet.",
    "ChIJSckfUPZzThMRkhXdGBFIp68": "An easygoing cafe for good coffee and cake; come for breakfast or a sweet-or-savoury waffle between sights.",
    "ChIJWWSmOnl0ThMRWxLvZVI36Uw": "A local recommendation in the old town, with a terrace view and assured, satisfying cooking.",
    "ChIJ-9RSCIltThMRxISJxoD8_AM": "Atmospheric and a touch romantic, with large, beautifully presented plates and polished, smiling service.",
    "ChIJCfaTk6DZTRMRxwLCWKD0zZE": "Lake Skadar fish and Montenegrin classics with something for everyone; the mushroom dish is a quiet standout.",
    "ChIJ5VFnOgt1ThMRoxnsFQX0G9I": "A calm little gem on the old town's main street; cozy, affordable and easy to linger in.",
    "ChIJOStNP8ZyThMRbNJYXeEJcLk": "Fresh, friendly and well-priced seafood; the shared plate for two is the easy, generous choice.",
    "ChIJ5cyg6Xh0ThMR3BO3SRxtcGQ": "Near the fortress and kind to a budget; a soup-and-main set that eats far better than its price.",
    "ChIJlW-zIxp1ThMRDUi1JNOqp1Y": "No frills, just some of Montenegro's best grilled meat; come hungry and skip the white tablecloth.",
    # Athens
    "ChIJ7z1TkSS9oRQRJTycL4l8QlM": "A deli-meets-taverna roomier than it looks; authentic Greek plates and a short, fast-moving queue.",
    "ChIJ81VUvSO9oRQR8N4i16NWGKA": "Polished Athenian cooking with real flair; the chicken souvlaki arrives as beautiful as it tastes.",
    "ChIJi1P0wxe9oRQRp_ropSGHq3g": "A Plaka institution with a canopied terrace; the authentic Greek lunch local guides send you to.",
    "ChIJJ0_KAD69oRQRX32cZoNJdtM": "Generous portions and genuine warmth, indoors or out; the falafel and pork gyro are done properly.",
    "ChIJ08Hp8uO9oRQRY937fQWHgLM": "Outstanding cooking that quietly outshines its modest room; worth seeking out for the kitchen alone.",
    "ChIJk0Zc_he9oRQR2C7nlnZvv4w": "Romantic and atmospheric, with live music some nights; lovely for a long evening with family or two.",
    "ChIJkWQy_kW9oRQRsGQqCSbokM4": "An all-day eatery with thoughtful touches and fresh, satisfying plates; easy for brunch or an unhurried dinner.",
    "ChIJm0fsCkK9oRQRb_Mgt4VwR3Y": "Hearty, home-style cooking in big portions; the oven lamb and brisket are tender, sauce-soaked highlights.",
    "ChIJrex8Iha9oRQR5pc8mru7M9E": "A well-loved Plaka taverna that fills quickly after sundown; classic Greek cooking, so come a little early.",
    "ChIJdaToxBe9oRQRiHAST4VbyPg": "Warm and down-to-earth, with saganaki, dolmades and the regional staples done right; a reliable lunch.",
    "ChIJP4mPSRa9oRQRIXKiWBBWOAw": "An easy Plaka table for a meze spread and well-made gyros; a relaxed finale to a day of sightseeing.",
    "ChIJ9Vow4-a9oRQRXoz353RJED8": "A happened-upon Plaka find that becomes the meal you remember; attentive service and honest Greek plates.",
    # Kusadasi
    "ChIJZ-lDElWvvhQRahZTiDV4mx4": "A seaside table best booked ahead; relaxed and generous, lovely as the light fades over the bay.",
    "ChIJfxv1MuiovhQRyX-omSEl9EA": "Authentic, well-priced kebabs that win over even cautious eaters; simple, tasty Turkish cooking near the center.",
    "ChIJfRZmeDWpvhQRXc76sX6FZlc": "A true Aegean table: nothing overcomplicated, just impeccably fresh fish and faultless fried calamari.",
    "ChIJA8BbNTSpvhQRzaZ-jx6Wjqg": "Friendly service and a sweeping view; come for a lavish Turkish breakfast or a long, easy lunch.",
    "ChIJrcvGBi-pvhQRbgYxjluYnLU": "Good, fairly priced Turkish cooking in a central spot; gentle hospitality and a view over the town.",
    "ChIJLfl_Jm6pvhQRtd_0t1YyhS4": "A fine sunset perch for fresh seafood; reserve a deck table to watch the light fade over the water.",
    "ChIJW4N1-tipvhQRY8OngX3gugs": "Seaside fish done well, with very good mezes and calamari; friendly, unhurried, and recommended by locals.",
    "ChIJyRTvSS6pvhQRE7KVsHsaH0k": "A festive family favourite for gorgeous Turkish food and live entertainment; built for a celebratory evening.",
    "ChIJS6Z8kOqpvhQRsC1vHQwDOlg": "Let the kitchen decide and you'll eat beautifully; delicate, fresh seafood many call their best in Turkey.",
    "ChIJRRgjQSypvhQRTkMCNsxJlmU": "Reliably excellent kebabs and cool, easy service; they'll even arrange a lift by WhatsApp from your hotel.",
    "ChIJ3QKYSC6pvhQRuntYCPRGyo0": "A lively show-and-dinner spot geared to a big night out; go for the entertainment as much as the table.",
    "ChIJW0bTMeiovhQRa7KFjzjKTcY": "A central cafe-bar-restaurant with a warm streak; reviews run mixed, so keep it simple and order the classics.",
    # Rhodes
    "ChIJl_6ifOlhlRQRDdWegepp0HE": "A charming courtyard beneath great trees; the setting is memorable and lively, a lovely place to settle in.",
    "ChIJ6c5yCMJhlRQRRNyAXGk60Pk": "Authentic Greek cooking and real kindness near the foot of the Old Town; for many, the day's highlight.",
    "ChIJBWquqvphlRQR8hWrtmcb7jo": "Warm hospitality and some of the island's best cooking; an Old-Town gem worth planning your evening around.",
    "ChIJF5IX1lthlRQRnbm-_AVQR9Q": "Right in the Old Town for a relaxed pizza night; solid pies and an easy, central perch between the lanes.",
    "ChIJPYIYVOlhlRQRu3djatih41A": "A warm, welcoming table with a lovely garden; a fantastic dinner that anchors a day in Rhodes.",
    "ChIJGYcOIsFhlRQRqj0tJJsF_Eg": "Friendly hosts who share Greek dining traditions along with the meze; genuine, generous and worth booking.",
    "ChIJm6rCLMJhlRQRS5R74iw94vQ": "Fresh, beautifully presented Greek plates and ever-smiling service; many call it the best table in Rhodes.",
    "ChIJaT5tkvRhlRQRvnEb5kBtzaM": "A stylish, spotless room with standout mezze; the tzatziki and cheesy balls are reason enough to stop.",
    "ChIJKWQ09ulhlRQRAcjhk8h4buM": "A lovely, varied taverna; start with the mixed dips and warm bread, then the chicken kebab won't disappoint.",
    "ChIJzaLNzMNhlRQRU7__uwjKf_E": "A highlight in the Medieval Town: warm from the first moment, with cooking that lingers long after.",
    "ChIJgajLM4lhlRQRLrz2YCAl2Us": "Homemade comfort done well; the slow-cooked beef and lamb, with fresh salads, reward a leisurely meal.",
    "ChIJc0XML4thlRQRA5NZ2eNouGY": "Possibly the best souvlaki you'll eat, served with genuine warmth and personality; simple food, done with love.",
    # Santorini
    "ChIJBXVFhxzOmRQRkmHfaqSO1ik": "A hillside Santorini classic in Exo Gonia; book ahead for sea views and famously generous, soulful Greek cooking.",
    "ChIJJfowAM7NmRQROoWlRzyw9eo": "A reliable Fira table for an easy lunch; the whole sea bream, deboned tableside, is the order to make.",
    "ChIJl7anSMfNmRQRV8BPyFsAmps": "A traditional Greek gem that handles a big table with ease; the seafood grill platter is built for sharing.",
    "ChIJB2D9AoDLmRQRd5zE5RIeY4o": "Santorini's beloved souvlaki stop; quick, affordable and very good, with a covered terrace for in or takeaway.",
    "ChIJc7878ujRmRQRG7Yw_xzcr-E": "Warm hospitality and fresh, considered plates; the sea bream and feta with sesame and honey are standouts.",
    "ChIJe51lKNLNmRQRVHwWw1mWkvg": "Classic Greek seafood with a sea view; order the sea bream, octopus and tomato balls and settle in.",
    "ChIJ-aWFDujPmRQRcf1wUiTJLIw": "A breezy beach-club lunch where the whole fish, three ways, steals the show; stay on for the sunbeds.",
    "ChIJixTdKszNmRQRnluEyLRWzGA": "Cozy and colourful with sea views; locals point here for traditional Greek cooking at honest value.",
    "ChIJ99nb80LNmRQRNrUFwA51fMM": "Friendly and fairly priced; the lamb kleftiko, slow and tender, is the dish people come back for.",
    "ChIJf5z5ikrMmRQRSDOFpvbjqcE": "An unassuming room with quick, attentive service; dependable moussaka, seafood and grills, plus local wine.",
    "ChIJtQntaczNmRQRA5KhCa9HS7M": "Genuine, generous and warmly run; trust the waiters' recommendations and you'll understand the devoted regulars.",
    "ChIJIZKQgqjNmRQR8SjWkjpUIbw": "An easy, welcoming table that suits a relaxed evening for two; friendly service and satisfying Greek plates.",
    # Istanbul
    "ChIJnXNX4Ly5yhQR-QKvTThJM_w": "A charming little Sultanahmet find; warm, inviting, and quickly a favourite for proper Turkish kebabs.",
    "ChIJEYH7W9a5yhQRezfLJzGgnQI": "Traditional small plates and mains in a relaxed garden; big, tasty portions and a wonderful first taste of Istanbul.",
    "ChIJG2RjnWy5yhQRZj8hMugPem0": "A leafy hideaway with quick service and surprisingly good vegetarian options; start with the calamari.",
    "ChIJk91SbAq5yhQRqpOiUFll2Uk": "A cozy room of brick and firelight serving Turkish and global plates; the kind of place you return to.",
    "ChIJfaNXSry5yhQRCtwvS7IzYdM": "A rooftop with breathtaking Hagia Sophia views; come for the panorama and stay for the classic grills.",
    "ChIJWwLBWIO5yhQRPNmyXYLbyjg": "Memorable plates on a relaxed terrace; the fish soup and aubergine kebab are reason enough to book.",
    "ChIJ4f4FpOG5yhQRxfyzYaik1vM": "Wood-fired meat and seafood on a quiet patio; even the lentil soup lingers in the memory.",
    "ChIJhX0rzM-5yhQRY0dHbpzBl4I": "A rooftop with a five-star Blue Mosque view; the Adana kebab holds its own against the panorama.",
    "ChIJUQ31a5u5yhQRb3NRj0euDJI": "Stone walls, gracious owners and a clay-pot kebab for two; a warm refuge on a cool Istanbul night.",
    "ChIJJ6EeAZa5yhQRKCA71LPs7_w": "Ottoman palace cuisine in a stylish modern room; a rare chance to taste the recipes of the sultans' court.",
    "ChIJvW_uZvG5yhQRyvWsE6EN3Hc": "Varied, dependable and easy to love; the kind of Istanbul table guests return to two or three times.",
    "ChIJCW0u6Ly5yhQRhuRajZy8SH0": "A hotel-recommended favourite that lives up to the praise; a marvellous, characterful dinner near the sights.",
}


def main() -> int:
    catalog = json.loads(CATALOGS[0].read_text())
    missing = []
    for r in catalog["restaurants"]:
        v = VIBE.get(r["id"])
        if v:
            r["vibe"] = v
        elif not r.get("vibe"):
            missing.append(r["name"])
    for path in CATALOGS:
        path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2))
    filled = sum(1 for r in catalog["restaurants"] if r.get("vibe"))
    print(f"vibe filled: {filled}/{len(catalog['restaurants'])}")
    if missing:
        print("MISSING:", missing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
