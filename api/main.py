"""FLASHCROWD API (CONTRACTS.md §3.5).

MOCK=1 (default for now): every route answers from api/mocks/ (PC2's levels.json + ticks.jsonl, plus
contract-shaped placeholders). Real sessions (flashcrowd_engine + Snowflake) replace the mock pieces in P2
without changing any route or JSON shape.

Run: uvicorn api.main:app --host 127.0.0.1 --port 8000
"""
import asyncio, hashlib, json, os, pathlib, secrets, time
import httpx
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

CONTRACTS_VERSION = "1.1"
ROOT = pathlib.Path(__file__).resolve().parent
MOCKS = ROOT / "mocks"
MOCK = os.getenv("MOCK", "1") == "1"
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
        self.actions: list[dict] = []          # queued for the next tick (applied by the real sim in P2)
        self.created = time.time()


SESSIONS: dict[str, Session] = {}


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
    return mock("levels.json")


@app.post("/api/sessions")
def create_session(body: NewSession):
    lv = next((l for l in mock("levels.json") if l["id"] == body.level_id), None)
    if not lv:
        raise ApiError(404, "level_not_found", f"No level '{body.level_id}'.")
    s = Session(body.level_id, body.speed); SESSIONS[s.id] = s
    base = mock("session.json")
    return {**base, "session_id": s.id, "start": lv["start"]}


@app.post("/api/sessions/{sid}/action")
def action(sid: str, a: Action):
    s = get_session(sid)
    if a.type != "scale":
        return {"ok": False, "error": "unknown_action"}
    if a.region not in REGIONS:
        return {"ok": False, "error": "bad_region"}
    if not -5 <= a.delta <= 5 or a.delta == 0:
        return {"ok": False, "error": "bad_delta"}
    s.actions.append(a.model_dump())
    return {"ok": True}


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
    get_session(sid)
    return mock("review.json")


@app.websocket("/api/sessions/{sid}/stream")
async def stream(ws: WebSocket, sid: str):
    await ws.accept()
    s = SESSIONS.get(sid)
    if not s:
        await ws.send_json({"type": "error", "message": f"No session '{sid}'."}); await ws.close(); return
    try:
        while s.m < len(TICKS):
            if s.paused:
                await asyncio.sleep(0.1); continue
            s.actions.clear()                                   # mock mode: actions accepted but not simulated
            await ws.send_text(json.dumps(TICKS[s.m], ensure_ascii=False))
            s.m += 1
            await asyncio.sleep(1 / s.speed)                    # speed = sim minutes per real second
        await ws.send_json({"type": "end", "review_url": f"/api/sessions/{sid}/review"})
        await ws.close()
    except WebSocketDisconnect:
        pass                                                    # reconnect resumes from s.m


@app.get("/api/live/forecasts")
def live_forecasts(limit: int = Query(20, ge=1, le=200)):
    return mock("forecasts.json")[:limit]


@app.get("/api/live/scorecard")
def live_scorecard():
    return mock("scorecard.json")


@app.get("/api/agent/reasons")
def agent_reasons(region: str = Query(...)):
    if region not in REGIONS:
        raise ApiError(400, "bad_region", "region must be LDN, NJ or TYO")
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
