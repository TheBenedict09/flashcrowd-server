import asyncio, json, time, httpx, websockets
B = "http://18.132.176.215/api"
c = httpx.Client(timeout=15)
def show(name, r, n=110):
    print(f"{r.status_code} {name:28s} {r.text[:n]!s}")
show("GET /health", c.get(f"{B}/health"))
show("GET /levels", c.get(f"{B}/levels"))
r = c.post(f"{B}/sessions", json={"level_id": "oct-19-evening", "speed": 120}); show("POST /sessions", r); sid = r.json()["session_id"]
show("POST /sessions bad level", c.post(f"{B}/sessions", json={"level_id": "nope"}))
show("POST action ok", c.post(f"{B}/sessions/{sid}/action", json={"type": "scale", "region": "TYO", "delta": 1}))
show("POST action bad delta", c.post(f"{B}/sessions/{sid}/action", json={"type": "scale", "region": "TYO", "delta": 9}))
show("POST control speed", c.post(f"{B}/sessions/{sid}/control", json={"cmd": "speed", "value": 300}))
show("GET review", c.get(f"{B}/sessions/{sid}/review"))
show("GET review unknown", c.get(f"{B}/sessions/xxx/review"))
show("GET live/forecasts?limit=2", c.get(f"{B}/live/forecasts?limit=2"))
show("GET live/scorecard", c.get(f"{B}/live/scorecard"))
show("GET agent/reasons?region=NJ", c.get(f"{B}/agent/reasons?region=NJ"))
show("POST voice/tts (no key yet)", c.post(f"{B}/voice/tts", json={"text": "Surge likely in Tokyo"}))
async def ws():
    t0 = time.time(); n = 0; last = None
    async with websockets.connect(f"ws://18.132.176.215/api/sessions/{sid}/stream", max_size=None) as w:
        async for msg in w:
            m = json.loads(msg); last = m
            if m["type"] == "tick": n += 1
            else: break
    print(f"WS: {n} ticks in {time.time()-t0:.1f}s, final={last}")
asyncio.run(ws())
