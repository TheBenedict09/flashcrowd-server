"""Generate the arcade's ElevenLabs audio once and cache it on disk (served by nginx at /audio/).

    python -m pipeline.audio_assets              # generate whatever is missing (idempotent)
    python -m pipeline.audio_assets --only sfx   # one group: sfx | kind | caster | ai | alert | music

Groups (all ElevenLabs):
  sfx    Sound Effects API: game sounds (server boot, shield shatter, crowd roar, boss siren, fanfare...)
  kind   Sound Effects API: one sting per Gemini headline kind (sports -> stadium roar, weather -> thunder...)
  caster Text to Speech: an arcade commentator ("Flash spike inbound!", "Combo times five!")
  ai     Text to Speech: the autopilot's voice in Human vs AI (the browser adds a robot filter)
  alert  Text to Speech, multilingual: city alerts in the region's language (London en, New Jersey es, Tokyo ja)
  loop   Sound Effects v2 (loop=true): adaptive soundtrack layers faded in with traffic and danger
  music  Music API: menu and boss loops (paid plans only; skipped on the free plan)
Writes /var/www/fc-audio/<group>/<id>.mp3 and manifest.json. Existing files are never regenerated, so the free
plan's monthly credits are spent once.
"""
import argparse
import json
import os
import pathlib
import time

import httpx
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
OUT = pathlib.Path(os.getenv("FC_AUDIO_DIR", "/var/www/fc-audio"))
API = "https://api.elevenlabs.io/v1"

V_CASTER = "TX3LPaxmHKxFdv7VOQHJ"     # Liam: energetic
V_AI = "nPczCjzI2devNBz1zQrb"         # Brian: deep, resonant (robotised in the browser)
V_ALERT = {"en": "onwK4e9ZLuTAKqWW03F9", "es": "XrExE9yKIg1WjnnlVkGX", "ja": "XrExE9yKIg1WjnnlVkGX"}  # Daniel / Matilda

SFX = {
    "boot": ("sci-fi server rack powering up, short rising digital whirr and click", 1.0),
    "shatter": ("energy shield shattering into glass shards with an electric zap, arcade game", 1.5),
    "crowd": ("huge crowd roar surging in a stadium", 2.0),
    "siren": ("retro arcade boss warning siren, two alternating tones", 2.5),
    "fanfare": ("triumphant 8-bit chiptune victory fanfare", 3.0),
    "levelup": ("short bright arcade level complete jingle, chiptune", 1.6),
    "breach": ("server overload alarm buzzer with electrical crackle", 1.2),
    "cash": ("cash register cha-ching with coins", 1.0),
    "powerup": ("magical arcade power-up chime rising", 1.0),
    "radar": ("sonar radar ping, sci-fi", 0.8),
    "crit": ("punchy critical hit impact with deep bass boom, arcade", 0.8),
    "gameover": ("sad descending arcade game over jingle", 2.0),
}
KIND = {
    "sports": "stadium crowd cheering with referee whistle",
    "gaming": "video game console startup sound with excited crowd cheer",
    "politics": "election night crowd cheering and applause",
    "tech": "futuristic product launch whoosh followed by applause",
    "entertainment": "red carpet premiere, camera flashes and fans screaming",
    "world": "urgent news broadcast sting with low drums",
    "weather": "sudden thunderclap with wind and heavy rain",
    "economy": "stock exchange trading floor bell and busy chatter",
    "breaking": "breaking news television sting, urgent synth hits",
    "funny": "cartoon boing and a crowd laughing",
}
CASTER = {
    "lv1": "Morning rush! Here they come!", "lv2": "Lunch break! Everyone's online!",
    "lv3": "Afternoon storm! Stay sharp!", "lv4": "Prime time! The whole world is watching!",
    "boss": "Final boss incoming! Hold the line!",
    "surge": "Surge on the way!", "flash": "Flash spike! Scale up now!", "double": "Double tap! Don't scale down!",
    "wobble": "Wobbly one! Hold steady!", "tsunami": "Tsunami wave! Build big!", "rolling": "Rolling wave! Follow the sun!",
    "fakeout": "Fake-out! Don't fall for the dip!",
    "blocked1": "Blocked!", "blocked2": "What a save!", "blocked3": "The wall holds!",
    "combo3": "Combo times three!", "combo5": "Times five! Unstoppable!",
    "breach": "Overflow! We're dropping requests!", "budget": "Too many idle servers! Scale down!",
    "overbudget": "Over budget! That one hurt!", "powerup": "Power-up ready! Hit space!",
    "bossdown": "The boss is down!", "victory": "Surge defeated! Incredible run!", "gameover": "System down... the crowd won.",
    "shield": "Shield down!", "double_trouble": "Double trouble!", "critical": "Critical hit!", "aidown": "The AI is out of shields!",
}
AI = {
    "detected": "Detected. Reinforcing.", "predictable": "Predictable, human.", "forecast": "My forecast saw that coming.",
    "scaling": "Scaling up. Resistance is inefficient.", "damaged": "Shield integrity compromised.",
    "recalc": "Recalculating.", "intro": "I am Autopilot Prime. Your memes cannot hurt me.",
    "win": "Uptime: one hundred percent. Goodbye, human.", "lose": "Error. Error. Crowd exceeds capacity.",
}
ALERT = {  # (language, text)
    "LDN_warn": ("en", "Traffic surge incoming in London!"), "LDN_over": ("en", "London is overflowing!"),
    "NJ_warn": ("es", "¡Se acerca un pico de tráfico en Nueva Jersey!"), "NJ_over": ("es", "¡Nueva Jersey está desbordada!"),
    "TYO_warn": ("ja", "東京でアクセスが急増しています！"), "TYO_over": ("ja", "東京のサーバーがあふれています！"),
}
LOOP = {   # adaptive soundtrack layers (Sound Effects v2 with loop=true); the game fades them with traffic and danger
    "tension": ("tense pulsing electronic drum and bass percussion loop, 140 bpm, no melody", 12),
    "heartbeat": ("deep heavy heartbeat with low rumbling synth drone, ominous, seamless loop", 8),
    "crowdbed": ("large distant crowd murmur rising and falling, seamless loop", 10),
}
MUSIC = {  # Music API: needs a paid plan (free keys get 402); kept for when the plan allows it
    "menu": ("upbeat synthwave arcade menu loop, retro 80s synths, steady drums, no vocals", 30000),
    "boss": ("intense fast chiptune boss battle music, driving bass, dramatic, no vocals", 30000),
}


def key():
    return os.environ["ELEVENLABS_API_KEY"]


def used(c):
    try:
        d = c.get(f"{API}/user/subscription", headers={"xi-api-key": key()}).json()
        return d.get("character_count"), d.get("character_limit")
    except Exception:
        return None, None


def post(c, url, body, path, params=None):
    for attempt in range(4):
        r = c.post(url, headers={"xi-api-key": key(), "accept": "audio/mpeg"}, json=body, params=params)
        if r.status_code == 200:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
            return True
        if r.status_code == 429:
            time.sleep(3 * (attempt + 1)); continue
        print("  !", path.name, r.status_code, r.text[:200]); return False
    return False


def tts(c, voice, text, path, lang=None, stability=0.4, style=0.35):
    body = {"text": text, "model_id": "eleven_flash_v2_5",
            "voice_settings": {"stability": stability, "similarity_boost": 0.8, "style": style}}
    if lang:
        body["language_code"] = lang
    return post(c, f"{API}/text-to-speech/{voice}", body, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    a = ap.parse_args()
    groups = {
        "sfx": [(k, lambda c, p, v=v: post(c, f"{API}/sound-generation", {"text": v[0], "duration_seconds": v[1], "prompt_influence": 0.5}, p)) for k, v in SFX.items()],
        "kind": [(k, lambda c, p, v=v: post(c, f"{API}/sound-generation", {"text": v, "duration_seconds": 1.6, "prompt_influence": 0.5}, p)) for k, v in KIND.items()],
        "caster": [(k, lambda c, p, v=v: tts(c, V_CASTER, v, p, stability=0.3, style=0.6)) for k, v in CASTER.items()],
        "ai": [(k, lambda c, p, v=v: tts(c, V_AI, v, p, stability=0.85, style=0.0)) for k, v in AI.items()],
        "alert": [(k, lambda c, p, v=v: tts(c, V_ALERT[v[0]], v[1], p, lang=v[0], stability=0.5, style=0.3)) for k, v in ALERT.items()],
        "loop": [(k, lambda c, p, v=v: post(c, f"{API}/sound-generation", {"text": v[0], "duration_seconds": v[1], "prompt_influence": 0.6, "loop": True, "model_id": "eleven_text_to_sound_v2"}, p)) for k, v in LOOP.items()],
        "music": [(k, lambda c, p, v=v: post(c, f"{API}/music", {"prompt": v[0], "music_length_ms": v[1]}, p)) for k, v in MUSIC.items()],
    }
    with httpx.Client(timeout=120) as c:
        start, limit = used(c)
        print(f"credits used {start}/{limit}")
        for g, jobs in groups.items():
            if a.only and g != a.only:
                continue
            before = used(c)[0]
            made = 0
            for k, fn in jobs:
                p = OUT / g / f"{k}.mp3"
                if p.exists() and p.stat().st_size > 1000:
                    continue
                made += bool(fn(c, p))
            after = used(c)[0]
            print(f"{g}: {made} generated, credits {before} -> {after}")
        manifest = {g: sorted(p.stem for p in (OUT / g).glob("*.mp3")) for g in groups if (OUT / g).exists()}
        (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1))
        print("manifest:", {g: len(v) for g, v in manifest.items()}, "| credits used", used(c))


if __name__ == "__main__":
    main()
