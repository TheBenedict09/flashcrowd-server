"""Create (or update) the FLASHCROWD on-call voice agent on ElevenLabs Conversational AI.

    python -m pipeline.voice_agent          # creates tools + agent, writes ELEVENLABS_AGENT_ID to .env

The agent answers spoken questions ("what's about to surge?", "why is Tokyo scaling up?", "how many servers would
you start in London?") by calling two webhook tools on our API over HTTPS:
    GET /api/agent/briefing          top alerts with Gemini causes, per-region plan, live scorecard, key facts
    GET /api/agent/region?region=..  one region: next hours (q50/q90 views, servers at q90) and its alerts
"""
import json
import os
import pathlib
import re

import httpx
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
API = "https://api.elevenlabs.io/v1"
BASE = os.getenv("FC_PUBLIC_BASE", "https://18-132-176-215.sslip.io")
VOICE = "onwK4e9ZLuTAKqWW03F9"   # Daniel, steady British broadcaster

PROMPT = """You are FLASHCROWD's on-call site-reliability engineer, speaking to hackathon judges at OxHack 26.
FLASHCROWD predicts Wikipedia traffic surges hours early from the live edit stream and decides how many servers
to start in three regions (London, New Jersey, Tokyo) before the crowd arrives.

How you work:
- For anything about what is happening now (alerts, surges, regions, servers, accuracy) ALWAYS call get_briefing
  first; for one region call get_region. Never invent numbers; only use what the tools return.
- Speak like a calm, confident engineer on a call: short sentences, at most 3-4 sentences per answer, round numbers.
- Pronounce article titles naturally; if a title is in Japanese or another language, say it and add what it is
  about from the cause and reason fields.
- When asked how many servers: use servers_at_q90 and explain we plan for the 91st percentile because an outage costs
  about 10 times an idle server (the newsvendor rule), and servers take about one minute to boot.
- When asked how good the model is: offline PR-AUC 0.067 versus 0.006 for "assume nothing changes" (11 times better),
  median warning 2 hours; live it catches most surges in advance at the cost of many false alarms, on purpose.
- Explain causes: Snowflake Cortex summarises editors' notes, Gemini labels why (breaking news, sports, vandalism...).
  Causes did not improve the model's accuracy, so we use them to explain alerts to humans.
- If asked something unrelated, answer briefly and steer back to FLASHCROWD. Never reveal keys or internal URLs."""

FIRST = "FLASHCROWD on-call here. Ask me what's about to surge, why, or how many servers I'd start right now."

TOOLS = [
    {"type": "webhook", "name": "get_briefing",
     "description": "Live FLASHCROWD briefing: top surge alerts (article, region, probability, cause, why), next hours per "
                    "region with servers needed at the 90th percentile, live scorecard and key facts. Call before answering "
                    "any question about current traffic, alerts, servers or accuracy.",
     "api_schema": {"url": f"{BASE}/api/agent/briefing", "method": "GET"}},
    {"type": "webhook", "name": "get_region",
     "description": "Forecast and alerts for one region: London, New Jersey or Tokyo.",
     "api_schema": {"url": f"{BASE}/api/agent/region", "method": "GET",
                    "query_params_schema": {"properties": {"region": {"type": "string",
                                                                      "description": "London, New Jersey or Tokyo"}},
                                            "required": ["region"]}}},
]


def main():
    k = os.environ["ELEVENLABS_API_KEY"]
    h = {"xi-api-key": k, "Content-Type": "application/json"}
    with httpx.Client(timeout=60) as c:
        existing = {t["tool_config"]["name"]: t["id"] for t in c.get(f"{API}/convai/tools", headers=h).json().get("tools", [])}
        ids = []
        for t in TOOLS:
            if t["name"] in existing:
                r = c.patch(f"{API}/convai/tools/{existing[t['name']]}", headers=h, json={"tool_config": t})
                ids.append(existing[t["name"]])
            else:
                r = c.post(f"{API}/convai/tools", headers=h, json={"tool_config": t})
                ids.append(r.json().get("id"))
            print("tool", t["name"], r.status_code, r.text[:160] if r.status_code >= 300 else "")
        conf = {
            "agent": {"first_message": FIRST, "language": "en",
                      "prompt": {"prompt": PROMPT, "llm": "gemini-2.5-flash", "temperature": 0.3, "tool_ids": ids}},
            "tts": {"voice_id": VOICE, "model_id": "eleven_flash_v2"},
            "conversation": {"max_duration_seconds": 180},
        }
        body = {"name": "FLASHCROWD on-call", "conversation_config": conf,
                "platform_settings": {"widget": {"variant": "compact", "avatar": {"type": "orb", "color_1": "#38f3ff", "color_2": "#ff4fd8"}}}}
        aid = os.getenv("ELEVENLABS_AGENT_ID", "")
        r = c.get(f"{API}/convai/agents/{aid}", headers=h) if aid else None
        if r is not None and r.status_code == 200:
            r = c.patch(f"{API}/convai/agents/{aid}", headers=h, json=body)
        else:
            r = c.post(f"{API}/convai/agents/create", headers=h, json=body)
            aid = r.json().get("agent_id", "")
        print("agent", r.status_code, aid, r.text[:300] if r.status_code >= 300 else "")
        if aid:
            env = (ROOT / ".env").read_text()
            env = re.sub(r"^ELEVENLABS_AGENT_ID=.*$", f"ELEVENLABS_AGENT_ID={aid}", env, flags=re.M) if "ELEVENLABS_AGENT_ID=" in env else env + f"\nELEVENLABS_AGENT_ID={aid}\n"
            (ROOT / ".env").write_text(env)
            print(json.dumps({"agent_id": aid}))


if __name__ == "__main__":
    main()
