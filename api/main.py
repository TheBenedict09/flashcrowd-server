"""FLASHCROWD API (CONTRACTS.md §3.5).

Game sessions are REAL: PC3's flashcrowd_engine.GameSession over level packages in api/levels/ (real Wikipedia
demand + PC1's measured constants, built by api/build_levels.py). GET /levels serves api/levels.json.
PC2's synthetic level id (api/mocks/) still works for UI testing but is not listed.
Live endpoints read Snowflake (FORECASTS, scorecard job output) and fall back to api/mocks/ when empty.

Run: uvicorn api.main:app --host 127.0.0.1 --port 8000
"""
import asyncio, hashlib, json, os, pathlib, secrets, time
import httpx
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from flashcrowd_engine import GameSession

CONTRACTS_VERSION = "1.1"
ROOT = pathlib.Path(__file__).resolve().parent
MOCKS = ROOT / "mocks"
LEVELS_DIR = ROOT / "levels"
MOCK_LEVEL = "oct-19-evening"          # PC2's synthetic replay, kept for UI testing
MOCK = False
REGIONS = {"LDN", "NJ", "TYO"}
CACHE = pathlib.Path(os.getenv("FC_TTS_CACHE", "/tmp/fc_tts_cache"))

app = FastAPI(title="FLASHCROWD API", version=CONTRACTS_VERSION)
origins = [o.strip() for o in os.getenv("FRONTEND_ORIGIN", "*").split(",")]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])


# ---------- errors: {"error": "snake_case_code", "message": "human text"} ----------
class ApiError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


@app.exception_handler(ApiError)
async def _api_error(_, e: ApiError):
    return JSONResponse({"error": e.code, "message": e.message}, status_code=e.status)


@app.exception_handler(HTTPException)
async def _http_error(_, e: HTTPException):
    return JSONResponse({"error": "http_" + str(e.status_code), "message": str(e.detail)}, status_code=e.status_code)


def mock(name):
    return json.loads((MOCKS / name).read_text(encoding="utf-8"))


TICKS = [json.loads(l) for l in (MOCKS / "ticks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


# ---------- sessions (in memory) ----------
class Session:
    def __init__(self, level_id, speed):
        self.id = secrets.token_urlsafe(8)
        self.level_id, self.speed = level_id, max(1, min(int(speed), 600))
        self.paused, self.m = False, 0
        self.game: GameSession | None = None   # None = PC2's mock replay
        self.streaming = False                 # one WebSocket drives the clock at a time
        self.done = False
        self.created = time.time()
        if level_id != MOCK_LEVEL:
            self.game = GameSession.from_level_file(LEVELS_DIR / f"{level_id}.json", session_id=self.id)


SESSIONS: dict[str, Session] = {}
LAST_GAME: list = [None]                       # latest real session, for /agent/reasons


def levels_index():
    f = ROOT / "levels.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else mock("levels.json")


def get_session(sid) -> Session:
    s = SESSIONS.get(sid)
    if not s:
        raise ApiError(404, "session_not_found", f"No session '{sid}'.")
    return s


class NewSession(BaseModel):
    level_id: str
    speed: int = 60


class Action(BaseModel):
    type: str
    region: str
    delta: int


class Control(BaseModel):
    cmd: str
    value: int | None = None


class TTS(BaseModel):
    text: str


# ---------- routes ----------
@app.get("/api/health")
def health():
    return {"ok": True, "version": CONTRACTS_VERSION, "mock": MOCK}


@app.get("/api/levels")
def levels():
    return levels_index()


@app.post("/api/sessions")
def create_session(body: NewSession):
    if body.level_id == MOCK_LEVEL:
        s = Session(body.level_id, body.speed); SESSIONS[s.id] = s
        lv = mock("levels.json")[0]
        return {**mock("session.json"), "session_id": s.id, "start": lv["start"]}
    if not (LEVELS_DIR / f"{body.level_id}.json").exists():
        raise ApiError(404, "level_not_found", f"No level '{body.level_id}'.")
    s = Session(body.level_id, body.speed); SESSIONS[s.id] = s
    LAST_GAME[0] = s.game
    return s.game.info()


@app.post("/api/sessions/{sid}/action")
def action(sid: str, a: Action):
    s = get_session(sid)
    if a.region not in REGIONS:
        return {"ok": False, "error": "bad_region"}
    if not -5 <= a.delta <= 5 or a.delta == 0:
        return {"ok": False, "error": "bad_delta"}
    if s.game is None:
        return {"ok": True} if a.type == "scale" else {"ok": False, "error": "unknown_action"}
    return s.game.action(a.model_dump())      # queued; applied on the next sim minute (engine validates min_servers etc.)


@app.post("/api/sessions/{sid}/control")
def control(sid: str, c: Control):
    s = get_session(sid)
    if c.cmd == "pause":
        s.paused = True
    elif c.cmd == "resume":
        s.paused = False
    elif c.cmd == "speed" and c.value:
        s.speed = max(1, min(int(c.value), 600))
    else:
        return {"ok": False, "error": "bad_command"}
    return {"ok": True}


@app.get("/api/sessions/{sid}/review")
def review(sid: str):
    s = get_session(sid)
    if s.game is None:
        return mock("review.json")
    if not s.done:
        raise ApiError(409, "session_not_finished", "The review is available once the shift has ended.")
    return clean_review(s.game.review())


CAUSES = {"breaking_news", "death", "sports", "scheduled_event", "entertainment", "vandalism", "edit_war",
          "bot_maintenance", "other"}


def clean_forecast(f: dict) -> dict:
    """Make an article forecast satisfy CONTRACTS §3.4 exactly (PC2 validates it):
    cause must be in the enum (PC3's model writes null until the Gemini labeller runs -> "other"),
    size_q90 >= size_q50, top_reasons = non-empty strings, issued_at ends in Z."""
    f = dict(f)
    if f.get("cause") not in CAUSES:
        f["cause"] = "other"
    q50 = max(0.0, float(f.get("size_q50") or 0)); q90 = max(q50, float(f.get("size_q90") or 0))
    f["size_q50"], f["size_q90"] = q50, q90
    f["p_surge"] = min(1.0, max(0.0, float(f.get("p_surge") or 0)))
    f["top_reasons"] = [str(r) for r in (f.get("top_reasons") or []) if str(r).strip()]
    ia = str(f.get("issued_at", ""))
    if ia and not ia.endswith("Z"):
        f["issued_at"] = ia.replace(" ", "T").split("+")[0] + "Z"
    return f


def clean_score(v: dict) -> dict:
    """Engine floats can give served_pct = 100.00000000000001; PC2 (rightly) rejects > 100."""
    v = dict(v)
    if "served_pct" in v:
        v["served_pct"] = round(min(100.0, max(0.0, float(v["served_pct"]))), 3)
    if "server_hours" in v:
        v["server_hours"] = round(max(0.0, float(v["server_hours"])), 3)
    if "breach_minutes" in v:
        v["breach_minutes"] = int(v["breach_minutes"])
    return v


def clean_tick(msg: dict) -> dict:
    msg = {**msg, "players": {p: clean_score(v) for p, v in msg.get("players", {}).items()}}
    if msg.get("radar"):
        msg["radar"] = [clean_forecast(f) for f in msg["radar"]]
    return msg


def clean_review(rv: dict) -> dict:
    """Scores clamped; surge rows kept only when they match CONTRACTS §3.5 (an onset time and both you + autopilot).
    PC3's engine also emits 'No surge' rows with onset=null for overspend; those go to `overspend` instead."""
    rv = {k: (clean_score(v) if k in ("you", "autopilot", "reactive", "scheduled") else v) for k, v in rv.items()}
    keep, extra = [], []
    for sg in rv.get("surges", []):
        ok = sg.get("onset") and all(isinstance(sg.get(p), dict) and "verdict" in sg[p] for p in ("you", "autopilot"))
        if ok:
            sg = dict(sg)
            for p in ("you", "autopilot", "reactive", "scheduled"):
                if isinstance(sg.get(p), dict):
                    sg[p] = {**sg[p], "ready_at_onset": int(sg[p].get("ready_at_onset") or 0)}
            keep.append(sg)
        else:
            extra.append(sg)
    rv["surges"], rv["overspend"] = keep, extra
    return rv


def next_message(s: Session) -> dict:
    if s.game is None:
        if s.m < len(TICKS):
            msg = TICKS[s.m]; s.m += 1; return msg
        return {"type": "end", "review_url": f"/api/sessions/{s.id}/review"}
    msg = s.game.tick()                                         # advances every player one sim minute
    s.m += 1
    if msg.get("type") == "tick":
        msg = clean_tick(msg)
    return msg


@app.websocket("/api/sessions/{sid}/stream")
async def stream(ws: WebSocket, sid: str):
    await ws.accept()
    s = SESSIONS.get(sid)
    if not s:
        await ws.send_json({"type": "error", "message": f"No session '{sid}'."}); await ws.close(); return
    if s.streaming:
        await ws.send_json({"type": "error", "message": "This session is already streaming in another connection."})
        await ws.close(); return
    s.streaming = True
    try:
        while not s.done:
            if s.paused:
                await asyncio.sleep(0.1); continue
            msg = next_message(s)
            if msg.get("type") == "end":
                s.done = True
                msg = {**msg, "review_url": f"/api/sessions/{sid}/review"}
            await ws.send_text(json.dumps(msg, ensure_ascii=False))
            if not s.done:
                await asyncio.sleep(1 / s.speed)                # speed = sim minutes per real second
        await ws.close()
    except WebSocketDisconnect:
        pass                                                    # reconnect resumes from the current minute
    finally:
        s.streaming = False


LIVE_CACHE: dict = {}
SCORECARD_FILE = pathlib.Path(os.getenv("FC_SCORECARD", "/home/ubuntu/flashcrowd-model/artifacts/live/scorecard.json"))


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ") if hasattr(t, "strftime") else t


def latest_forecasts(limit):
    """Latest batch from PC3's FORECASTS table (cached 60 s). Empty list if none yet."""
    hit = LIVE_CACHE.get("fc")
    if hit and time.time() - hit[0] < 60:
        return hit[1][:limit]
    import sys
    sys.path.insert(0, str(ROOT.parent))
    from pipeline.sf import connect
    with connect() as conn:
        cur = conn.cursor()
        rows = cur.execute("""
            SELECT issued_at, wiki, title, region, horizon_h, p_surge, size_q50, size_q90, cause, top_reasons
            FROM FORECASTS WHERE issued_at = (SELECT MAX(issued_at) FROM FORECASTS)
            QUALIFY ROW_NUMBER() OVER (PARTITION BY wiki, title ORDER BY p_surge DESC) = 1
            ORDER BY p_surge DESC LIMIT 200""").fetchall()
    out = [{"issued_at": _iso(r[0]), "wiki": r[1], "title": r[2], "region": r[3], "horizon_h": int(r[4]),
            "p_surge": round(float(r[5]), 4), "size_q50": float(r[6] or 0), "size_q90": float(r[7] or 0), "cause": r[8],
            "top_reasons": json.loads(r[9]) if isinstance(r[9], str) else (r[9] or [])} for r in rows]
    LIVE_CACHE["fc"] = (time.time(), out)
    return out[:limit]


@app.get("/api/live/forecasts")
def live_forecasts(limit: int = Query(20, ge=1, le=200)):
    try:
        rows = latest_forecasts(limit)
        if rows:
            return [clean_forecast(r) for r in rows]
    except Exception as e:                                        # Snowflake hiccup: keep the screen alive
        print("live_forecasts fallback:", e)
    return mock("forecasts.json")[:limit]


@app.get("/api/live/scorecard")
def live_scorecard():
    if SCORECARD_FILE.exists():                                   # written every 15 min by PC3's `flashcrowd scorecard`
        try:
            return json.loads(SCORECARD_FILE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return mock("scorecard.json")


@app.get("/api/agent/reasons")
def agent_reasons(region: str = Query(...)):
    if region not in REGIONS:
        raise ApiError(400, "bad_region", "region must be LDN, NJ or TYO")
    if LAST_GAME[0] is not None:
        return LAST_GAME[0].reasons(region)              # the autopilot's latest decision in the newest real session
    r = mock("reasons.json")
    return {**r, "region": region}


@app.post("/api/voice/tts")
async def tts(body: TTS):
    text = body.text.strip()[:200]
    if not text:
        raise ApiError(400, "empty_text", "text is required")
    key, voice = os.getenv("ELEVENLABS_API_KEY", ""), os.getenv("ELEVENLABS_VOICE_ID", "")
    if not key or key == "..." or not voice or voice == "...":
        raise ApiError(503, "tts_not_configured", "ElevenLabs key/voice not set on the server yet.")
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / (hashlib.sha256(f"{voice}:{text}".encode()).hexdigest() + ".mp3")
    if not f.exists():
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                             headers={"xi-api-key": key, "accept": "audio/mpeg"},
                             json={"text": text, "model_id": "eleven_flash_v2_5"})
        if r.status_code != 200:
            raise ApiError(502, "tts_upstream_error", f"ElevenLabs returned {r.status_code}")
        f.write_bytes(r.content)
    return Response(f.read_bytes(), media_type="audio/mpeg")
