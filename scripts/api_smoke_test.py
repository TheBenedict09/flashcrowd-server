"""End-to-end check of the deployed API: real level, player actions, WS to the end, review."""
import asyncio, json, sys, time, httpx, websockets
HOST = sys.argv[1] if len(sys.argv) > 1 else "18.132.176.215"
B = f"http://{HOST}/api"; c = httpx.Client(timeout=20)
def show(name, r, n=120): print(f"{r.status_code} {name:30s} {r.text[:n]}")
show("GET /health", c.get(f"{B}/health"))
lv = c.get(f"{B}/levels").json(); print("levels:", [(l["id"], l["title"]) for l in lv])
r = c.post(f"{B}/sessions", json={"level_id": lv[0]["id"], "speed": 200}); show("POST /sessions", r, 200); sid = r.json()["session_id"]
show("review before end (409)", c.get(f"{B}/sessions/{sid}/review"))
async def ws():
    t0 = time.time(); n = 0; sent = False
    async with websockets.connect(f"ws://{HOST}/api/sessions/{sid}/stream", max_size=None) as w:
        async for msg in w:
            m = json.loads(msg)
            if m["type"] == "tick":
                n += 1
                if n == 30 and not sent:           # the player reacts: +3 servers in every region
                    for reg in ("LDN", "NJ", "TYO"):
                        print("  action", reg, c.post(f"{B}/sessions/{sid}/action", json={"type": "scale", "region": reg, "delta": 3}).json())
                    sent = True
                if n in (1, 31, 120):
                    y = m["players"]["you"]["regions"]; print(f"  m={m['m']} you LDN running={y['LDN']['running']} booting={y['LDN']['booting']} demand_rps={y['LDN']['demand_rps']:.0f} | alerts={len(m.get('alerts', []))} radar={len(m.get('radar', []))}")
            else:
                print(f"WS: {n} ticks in {time.time()-t0:.1f}s, final={m}"); break
asyncio.run(ws())
rv = c.get(f"{B}/sessions/{sid}/review").json()
print("review:", {p: (rv[p]["served_pct"].__round__(1), rv[p]["server_hours"].__round__(1), rv[p].get("grade")) for p in ("you", "autopilot", "reactive", "scheduled")})
print("surges:", [(s["region"], s["title"], (s.get("onset") or "")[11:16], s.get("you", {}).get("verdict"), s.get("autopilot", {}).get("verdict")) for s in rv["surges"]])
show("GET /agent/reasons?region=LDN", c.get(f"{B}/agent/reasons?region=LDN"), 200)
show("POST action bad region", c.post(f"{B}/sessions/{sid}/action", json={"type": "scale", "region": "XX", "delta": 1}))
