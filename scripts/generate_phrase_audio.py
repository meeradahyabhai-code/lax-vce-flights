#!/usr/bin/env python3
"""One-time generator for phrase pronunciation audio (Google Cloud TTS).

Reads the API key from .gcp_tts_key (gitignored), synthesizes each phrase's
`native` text with the best available NATIVE voice for its locale, and writes
MP3s to public/audio/ plus public/audio/manifest.json (native text -> filename).

The key is used only here, locally. Nothing secret ships in the app or git.

Phrase data MIRRORS web/index.html (GREEK_PHRASES / TURKISH_PHRASES /
SOUTH_SLAVIC_PHRASES / PORT_GUIDE.venice.phrases). If you add/edit phrases
there, update this list and re-run.

    python3 scripts/generate_phrase_audio.py
"""
import base64
import hashlib
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY_FILE = os.path.join(ROOT, ".gcp_tts_key")
OUT_DIR = os.path.join(ROOT, "public", "audio")
BASE = "https://texttospeech.googleapis.com/v1"

# native strings to speak, grouped by locale (must match web/index.html)
PHRASES = {
    "it-IT": ["Buongiorno", "Grazie", "Prego", "Sì / No", "Quanto costa?", "Salute!"],
    "hr-HR": ["Dobar dan", "Hvala", "Molim", "Da / Ne", "Koliko košta?", "Živjeli!"],
    "el-GR": ["Γεια σας", "Ευχαριστώ", "Παρακαλώ", "Ναι / Όχι", "Πόσο κάνει;", "Στην υγειά μας"],
    "tr-TR": ["Merhaba", "Teşekkürler", "Lütfen", "Evet / Hayır", "Ne kadar?", "Şerefe!"],
}

# best voice tier first
TIER = {"Studio": 4, "Neural2": 3, "Wavenet": 2, "News": 2, "Standard": 1}


def load_key():
    with open(KEY_FILE) as f:
        key = f.read().strip()
    if not key:
        sys.exit("ERROR: .gcp_tts_key is empty")
    return key


def post(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def pick_voice(key, lang):
    """Best available voice for a locale: highest tier, prefer FEMALE."""
    data = get("%s/voices?languageCode=%s&key=%s" % (BASE, lang, key))
    voices = data.get("voices", [])
    if not voices:
        sys.exit("ERROR: no voices for %s" % lang)

    def score(v):
        name = v["name"]
        tier = max((t for t in TIER if t in name), key=lambda t: TIER[t], default=None)
        tier_score = TIER.get(tier, 0)
        female = 1 if v.get("ssmlGender") == "FEMALE" else 0
        return (tier_score, female)

    best = max(voices, key=score)
    return best["name"], best.get("ssmlGender", "FEMALE")


def say(text):
    """Make a slash read as a natural pause, not a literal 'slash'."""
    return text.replace(" / ", ", ").replace("/", ", ")


def fname(lang, native):
    h = hashlib.sha1(native.encode("utf-8")).hexdigest()[:10]
    return "%s-%s.mp3" % (lang, h)


def main():
    key = load_key()
    os.makedirs(OUT_DIR, exist_ok=True)
    manifest = {}
    total = 0
    for lang, items in PHRASES.items():
        voice_name, gender = pick_voice(key, lang)
        print("%s -> %s (%s)" % (lang, voice_name, gender))
        for native in items:
            payload = {
                "input": {"text": say(native)},
                "voice": {"languageCode": lang, "name": voice_name},
                "audioConfig": {"audioEncoding": "MP3", "speakingRate": 0.92},
            }
            resp = post("%s/text:synthesize?key=%s" % (BASE, key), payload)
            audio = resp.get("audioContent")
            if not audio:
                sys.exit("ERROR: no audio for %r: %s" % (native, json.dumps(resp)[:300]))
            fn = fname(lang, native)
            with open(os.path.join(OUT_DIR, fn), "wb") as f:
                f.write(base64.b64decode(audio))
            manifest[native] = fn
            total += 1
            print("  ok  %-16s -> %s" % (native, fn))
    with open(os.path.join(OUT_DIR, "manifest.json"), "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print("\nWrote %d clips + manifest.json to public/audio/" % total)


if __name__ == "__main__":
    main()
