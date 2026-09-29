from fastapi import FastAPI, Depends, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager
import asyncio
import sys
import os

# Support both `python -m backend.main` and `python backend/run.py` / PyInstaller
try:
    from db import engine, Base, SessionLocal, Task, Note, DB_PATH, Timer, Reminder, FocusSession, ScheduledAlert, ActionReceipt, Workspace, SessionNote, Coursework  # when run as `python run.py`
    import voice_service
    import system_actions
    import migrations
    import dispatcher
    import timeutil
    import tools
    import ai_radar
    import model_lane
    from scheduler import AlertScheduler
    from radar_scheduler import RadarScheduler
except ImportError:
    from .db import engine, Base, SessionLocal, Task, Note, DB_PATH, Timer, Reminder, FocusSession, ScheduledAlert, ActionReceipt, Workspace, SessionNote, Coursework
    from . import voice_service
    from . import system_actions
    from . import migrations
    from . import dispatcher
    from . import timeutil
    from . import tools
    from . import ai_radar
    from . import model_lane
    from .scheduler import AlertScheduler
    from .radar_scheduler import RadarScheduler

# Non-destructive versioned migrations (backs up the DB file before first ALTER;
# create_all alone would never add columns to the existing tasks table).
MIGRATION_INFO = migrations.run_migrations(engine, Base, DB_PATH)
if MIGRATION_INFO.get("backup_path"):
    print(f"[MIGRATION] Backed up existing DB to {MIGRATION_INFO['backup_path']}", flush=True)
print(f"[MIGRATION] Schema version {MIGRATION_INFO['version_before']} -> {MIGRATION_INFO['version_after']} "
      f"steps={MIGRATION_INFO['steps'] or 'none'}", flush=True)

# Single scheduler owner: one asyncio task in this app process. At-most-once
# delivery is guaranteed by atomic DB claims even if processes overlap.
SYSTEM_CLOCK = timeutil.SystemClock()
alert_scheduler = AlertScheduler(SessionLocal, clock=SYSTEM_CLOCK)
radar_scheduler = RadarScheduler(SessionLocal, clock=SYSTEM_CLOCK)


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    scheduler_task = loop.create_task(alert_scheduler.run())
    # VEGA_DISABLE_VOICE=1 keeps the mic/whisper threads off (tests, headless runs).
    if os.getenv("VEGA_DISABLE_VOICE", "").strip() not in ("1", "true", "yes"):
        voice_service.start(loop)
    # VEGA_DISABLE_RADAR=1 keeps the daily AI Radar network job off (tests).
    radar_task = None
    if os.getenv("VEGA_DISABLE_RADAR", "").strip() not in ("1", "true", "yes"):
        radar_task = loop.create_task(radar_scheduler.run())
    yield
    scheduler_task.cancel()
    if radar_task:
        radar_task.cancel()
    # Graceful shutdown: voice thread is daemon, will exit with process


app = FastAPI(title="Vega Backend", lifespan=lifespan)


# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Allow CORS for Electron/React frontend + dev servers
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "app://-",
        "file://",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    # Include voice status if available
    voice_ok = getattr(voice_service, "is_voice_active", lambda: None)()
    return {"status": "ok", "voice": voice_ok}


import requests
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from typing import List, Optional
from urllib.parse import quote

# Load .env explicitly from the backend directory
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path)

# Configured provider (env). A per-request `provider` field may override it;
# there is NO automatic fallback between providers — a failure on the
# configured provider is reported honestly instead of silently routing
# private data to another service.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    history: List[Message] = Field(default_factory=list, max_length=100)
    userName: Optional[str] = Field(default=None, max_length=50)
    provider: Optional[str] = None
    idempotencyKey: Optional[str] = Field(default=None, max_length=120)
    # "chat" (typed) or "voice" (wake-word transcript) — recorded on receipts so
    # voice-initiated actions are auditable. Anything else falls back to "chat".
    source: Optional[str] = Field(default="chat", max_length=10)


def _chat_response(out: dict) -> dict:
    """Normalize a chat result for the renderer. `opened` keeps the existing
    contract: open_app/open_website signal success with the 'Opening ...'
    prefix and the renderer hides the dashboard so the launched window shows."""
    resp = dict(out)
    msg = resp.get("response")
    resp["opened"] = isinstance(msg, str) and msg.startswith("Opening ")
    return resp


@app.post("/chat")
def chat_endpoint(req: ChatRequest, db: Session = Depends(get_db)):
    # 0. Deterministic assistant commands — tasks/timers/reminders/focus work
    #    offline (no Ollama/Gemini, zero provider calls) and typed + wake-word
    #    input share this path. Exact commands are answered immediately and are
    #    never affected by model availability.
    src = req.source if req.source in ("chat", "voice") else "chat"
    result = dispatcher.dispatch_command(
        db, SYSTEM_CLOCK, req.message, source=src,
        idempotency_key=req.idempotencyKey)
    if result.get("handled"):
        return _chat_response({
            "response": result["response"],
            "receipt": result.get("receipt"),
            "clarification": result.get("clarification", False),
            "executionMode": "deterministic",
        })

    # 1. Fast Path Check — bypass the model for obvious open/launch intents
    is_fast, action_resp, *extra = system_actions.check_fast_path(
        req.message, db=db, clock=SYSTEM_CLOCK, source=src,
        idempotency_key=req.idempotencyKey)
    if is_fast:
        receipt = extra[0] if extra else None
        return _chat_response({
            "response": action_resp,
            "receipt": receipt,
            "executionMode": "deterministic"
        })

    # 2. Model lane — shared intelligence boundary (P1): provider gateway ->
    #    bounded context -> registry-validated proposal -> the SAME executor
    #    and receipts the deterministic path uses. One action per request.
    user_name = req.userName.strip() if req.userName and req.userName.strip() else "Sir"
    out = model_lane.run_model_turn(
        SessionLocal, SYSTEM_CLOCK, req.message, req.history,
        user_name=user_name,
        provider_name=(req.provider.lower() if req.provider else LLM_PROVIDER),
        source=src,
        idempotency_key=req.idempotencyKey,
    )
    return _chat_response(out)


import psutil
import time
import json
import re

# Optional GPU — don't crash if GPUtil not installed
try:
    import GPUtil  # type: ignore
except ImportError:
    GPUtil = None

# ==========================================
# WEBSOCKET ORIGIN VALIDATION
# ==========================================
# Browsers always send an Origin header during a WebSocket handshake, but
# Starlette does not validate it. Without this check, any web page open in any
# browser on this machine could subscribe to live telemetry (/ws/system-stats)
# or wake-word events and voice transcripts (/ws/voice). The frontend only
# ever connects from the origins below; anything else is rejected during the
# handshake (uvicorn answers the upgrade request with HTTP 403).
ALLOWED_WS_ORIGINS = {
    "http://localhost:5173",   # Vite dev server
    "http://127.0.0.1:5173",
    "http://localhost:8000",   # same-origin
    "http://127.0.0.1:8000",
    "app://-",                 # kept for parity with the CORS allowlist
    "file://",                 # Electron loadFile (packaged app)
    "null",                    # Chromium serializes file/opaque origins as "null"
}


async def _ws_origin_allowed(websocket: WebSocket) -> bool:
    """Reject WebSocket connections from origins the app does not use."""
    origin = (websocket.headers.get("origin") or "").strip()
    if origin in ALLOWED_WS_ORIGINS:
        return True
    print(
        f"[WS SECURITY] Rejected {websocket.url.path} connection from "
        f"disallowed origin {origin!r}.",
        file=sys.stderr,
    )
    await websocket.close(code=1008)  # Policy Violation
    return False


# ==========================================
# VOICE WEBSOCKET
# ==========================================

@app.websocket("/ws/voice")
async def websocket_voice(websocket: WebSocket):
    if not await _ws_origin_allowed(websocket):
        return
    await websocket.accept()
    voice_service.register_client(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        voice_service.unregister_client(websocket)


@app.websocket("/ws/alerts")
async def websocket_alerts(websocket: WebSocket):
    """Alert delivery channel. The renderer keeps this connected while the
    overlay window is hidden, so timer/reminder/deadline alerts still surface
    as OS notifications and in-app toasts."""
    if not await _ws_origin_allowed(websocket):
        return
    await websocket.accept()
    alert_scheduler.subscribe(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        alert_scheduler.unsubscribe(websocket)


@app.websocket("/ws/system-stats")
async def websocket_system_stats(websocket: WebSocket):
    if not await _ws_origin_allowed(websocket):
        return
    await websocket.accept()

    last_net_io = psutil.net_io_counters()
    last_time = time.time()

    psutil.cpu_percent(interval=None)

    try:
        while True:
            cpu_percent = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory()

            # Cross-platform disk path
            disk_path = "C:\\" if sys.platform == "win32" else "/"
            try:
                disk = psutil.disk_usage(disk_path)
                disk_percent = disk.percent
            except Exception:
                disk_percent = 0

            current_net_io = psutil.net_io_counters()
            current_time = time.time()
            time_diff = current_time - last_time
            if time_diff > 0 and current_net_io and last_net_io:
                net_up = (current_net_io.bytes_sent - last_net_io.bytes_sent) / time_diff
                net_down = (current_net_io.bytes_recv - last_net_io.bytes_recv) / time_diff
            else:
                net_up, net_down = 0, 0

            last_net_io = current_net_io
            last_time = current_time

            battery = None
            try:
                battery = psutil.sensors_battery()  # type: ignore
            except Exception:
                pass

            gpu_stats = None
            if GPUtil is not None:
                try:
                    gpus = GPUtil.getGPUs()
                    if gpus:
                        gpu = gpus[0]
                        gpu_stats = {
                            "name": gpu.name,
                            "load": gpu.load * 100,
                            "memory": gpu.memoryUtil * 100,
                            "temp": gpu.temperature
                        }
                except Exception:
                    pass

            payload = {
                "cpu": cpu_percent,
                "ram": ram.percent,
                "disk": disk_percent,
                "net_up": max(0, net_up),
                "net_down": max(0, net_down),
                "battery": battery.percent if battery else None,
                "battery_plugged": battery.power_plugged if battery else None,
                "gpu": gpu_stats
            }

            await websocket.send_json(payload)
            await asyncio.sleep(1)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[WS system-stats] error: {e}", file=sys.stderr)


# ==========================================
# WAKE-WORD LISTENING TOGGLE
# ==========================================
# Keeping the mic stream open 24/7 forces classic-Bluetooth headsets into
# Hands-Free Profile, which degrades their audio playback to telephone
# quality. This endpoint lets the UI turn wake-word listening on/off —
# off = mic closed = normal (A2DP) audio quality returns.


class VoiceEnabledRequest(BaseModel):
    enabled: bool


class VoiceDuckRequest(BaseModel):
    active: bool


@app.post("/api/voice/duck")
def set_voice_duck(req: VoiceDuckRequest):
    """Frontend calls this while VEGA's own TTS is speaking so the always-on
    mic ignores wake words and never transcribes VEGA's voice back as a
    command. Safety-expired server-side if the client never clears it."""
    voice_service.set_duck(bool(req.active))
    return {"ducked": voice_service.is_ducked()}


@app.get("/api/voice/enabled")
def get_voice_enabled():
    return {"enabled": voice_service.is_enabled()}



@app.post("/api/voice/enabled")
def set_voice_enabled(req: VoiceEnabledRequest):
    voice_service.set_voice_enabled(bool(req.enabled))
    return {"enabled": bool(req.enabled), "status": voice_service.is_voice_active()}


class VoiceDeviceRequest(BaseModel):
    name: str = Field(default="", max_length=200)


@app.get("/api/voice/devices")
def get_voice_devices():
    """Return all available audio input devices on this machine."""
    devices = voice_service.list_input_devices()
    # Include which device is currently active
    current = (getattr(voice_service, "_device_override", "") or "").strip()
    return {"devices": devices, "current": current}


@app.post("/api/voice/device")
def set_voice_device(req: VoiceDeviceRequest):
    """Set the microphone to use for wake-word detection at runtime.
    Clears the override if name is empty (reverts to system default).
    The audio thread picks up the change on its next reconnect cycle."""
    name = (req.name or "").strip()
    voice_service.set_mic_device(name)
    return {"status": "ok", "device": name or "(system default)"}



# ==========================================
# LIVE FEEDS API
# ==========================================

feed_cache = {}
FEED_CACHE_MAX = 50


def get_cached_feed(key, ttl, fetch_func):
    now = time.time()
    if key in feed_cache:
        cached = feed_cache[key]
        if now - cached['time'] < ttl:
            return cached['data']
    try:
        data = fetch_func()
        # Evict oldest if over limit
        if len(feed_cache) >= FEED_CACHE_MAX:
            oldest = min(feed_cache, key=lambda k: feed_cache[k]['time'])
            feed_cache.pop(oldest, None)
        feed_cache[key] = {'time': now, 'data': data}
        return data
    except Exception as e:
        if key in feed_cache:
            return feed_cache[key]['data']
        return {"error": str(e)}


@app.get("/api/feeds/weather")
def get_weather(city: str = "London"):
    city = city.strip()[:100]  # prevent abuse

    def fetch_weather():
        geo_resp = requests.get(
            f"https://geocoding-api.open-meteo.com/v1/search?name={quote(city)}&count=1",
            timeout=10
        ).json()
        if not geo_resp.get("results"):
            return {"error": f"City '{city}' not found"}
        loc = geo_resp["results"][0]
        weather_resp = requests.get(
            f"https://api.open-meteo.com/v1/forecast?latitude={loc['latitude']}&longitude={loc['longitude']}&current_weather=true",
            timeout=10
        ).json()
        current = weather_resp.get("current_weather", {})
        return {
            "city": loc["name"],
            "temperature": current.get("temperature"),
            "weathercode": current.get("weathercode")
        }
    return get_cached_feed(f"weather_{city.lower()}", 900, fetch_weather)


@app.get("/api/feeds/headlines")
def get_headlines():
    def fetch_hn():
        top_ids = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=10).json()
        headlines = []
        for item_id in top_ids[:5]:
            try:
                item = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json", timeout=5).json()
                if item:
                    headlines.append({"title": item.get("title"), "url": item.get("url") or f"https://news.ycombinator.com/item?id={item_id}"})
            except Exception:
                continue
        return headlines
    return get_cached_feed("hn_headlines", 900, fetch_hn)


@app.get("/api/feeds/crypto")
def get_crypto(coins: str = "bitcoin,ethereum"):
    coins = coins.strip().lower()[:200]

    def fetch_crypto():
        resp = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={coins}&vs_currencies=usd&include_24hr_change=true",
            timeout=10
        ).json()
        if "error" in resp or not resp:
            return {"error": "Failed to fetch crypto data"}
        result = {}
        for coin, data in resp.items():
            if isinstance(data, dict):
                result[coin] = {
                    "price": data.get("usd"),
                    "change_24h": data.get("usd_24h_change")
                }
        return result
    return get_cached_feed(f"crypto_{coins}", 300, fetch_crypto)


# ==========================================
# PRODUCTIVITY HUB API
# ==========================================

from datetime import datetime, timedelta, timezone


def _parse_iso_utc(value: str):
    """ISO-8601 string -> naive UTC datetime (storage format)."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        # Naive input is interpreted as the user's local time.
        return timeutil.naive_utc(dt.astimezone(timezone.utc))
    return timeutil.naive_utc(dt)


class TaskCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)
    deadline_utc: Optional[str] = Field(default=None, max_length=64)
    subject: Optional[str] = Field(default=None, max_length=200)


@app.get("/api/tasks")
def get_tasks(db: Session = Depends(get_db)):
    tasks = db.query(Task).all()
    return [t.to_dict() for t in tasks]


@app.post("/api/tasks")
def create_task(task: TaskCreate, db: Session = Depends(get_db)):
    deadline = None
    if task.deadline_utc:
        try:
            deadline = _parse_iso_utc(task.deadline_utc)
        except ValueError:
            raise HTTPException(status_code=400, detail="deadline_utc must be ISO-8601")
    result = tools.execute_intent(
        db, SYSTEM_CLOCK, "create_task",
        {"text": task.text.strip(), "deadline_utc": deadline, "subject": task.subject},
        source="ui")
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message"))
    db_task = db.query(Task).filter(Task.id == result["entity_id"]).first()
    return db_task.to_dict()


@app.put("/api/tasks/{task_id}")
def toggle_task(task_id: int, db: Session = Depends(get_db)):
    # Legacy UI toggle — preserved for compatibility (spec: keep until migrated).
    db_task = db.query(Task).filter(Task.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    db_task.completed = not db_task.completed
    db_task.updated_at = SYSTEM_CLOCK.now_utc()
    if db_task.completed:
        tools._cancel_alerts(db, "task", db_task.id, kinds=["task_deadline"])
    db.commit()
    db.refresh(db_task)
    return db_task.to_dict()


class TaskCompleteRequest(BaseModel):
    completed: bool = True


@app.post("/api/tasks/{task_id}/complete")
def complete_task(task_id: int, req: TaskCompleteRequest, db: Session = Depends(get_db)):
    """Explicit set-completion endpoint for the assistant tool (idempotent —
    retries cannot reverse state like the legacy toggle can)."""
    result = tools.execute_intent(
        db, SYSTEM_CLOCK, "set_task_completed",
        {"task_id": task_id, "completed": req.completed}, source="api")
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("message"))
    return {"receipt": result}


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    db_task = db.query(Task).filter(Task.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    tools._cancel_alerts(db, "task", db_task.id)
    db.delete(db_task)
    db.commit()
    return {"status": "deleted"}


# ==========================================
# ASSISTANT COMMAND API (typed UI path — same dispatcher as /chat)
# ==========================================

class CommandRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=600)
    idempotency_key: Optional[str] = Field(default=None, max_length=120)
    source: str = Field(default="typed", max_length=20)


@app.post("/api/assistant/command")
def assistant_command(req: CommandRequest, db: Session = Depends(get_db)):
    result = dispatcher.dispatch_command(
        db, SYSTEM_CLOCK, req.message, source=req.source,
        idempotency_key=req.idempotency_key)
    if not result.get("handled"):
        return {"handled": False, "response": None}
    return result


# ==========================================
# TIMERS / REMINDERS / FOCUS / ALERTS API
# ==========================================

class TimerCreate(BaseModel):
    duration_seconds: int = Field(..., ge=1, le=24 * 3600)
    label: str = Field(default="", max_length=300)
    idempotency_key: Optional[str] = Field(default=None, max_length=120)


def _exec_or_400(result):
    if result.get("clarification"):
        raise HTTPException(status_code=409, detail=result.get("message"))
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result


@app.get("/api/timers")
def get_timers(status: str = "active", db: Session = Depends(get_db)):
    q = db.query(Timer)
    if status != "all":
        q = q.filter(Timer.status == status)
    return [t.to_dict() for t in q.order_by(Timer.end_utc).all()]


@app.post("/api/timers")
def create_timer(req: TimerCreate, db: Session = Depends(get_db)):
    result = _exec_or_400(tools.execute_intent(
        db, SYSTEM_CLOCK, "start_timer",
        {"duration_seconds": req.duration_seconds, "label": req.label},
        source="ui", idempotency_key=req.idempotency_key))
    timer = db.query(Timer).filter(Timer.id == result["entity_id"]).first()
    return {"receipt": result, "timer": timer.to_dict()}


@app.delete("/api/timers/{timer_id}")
def cancel_timer(timer_id: int, db: Session = Depends(get_db)):
    result = tools.execute_intent(db, SYSTEM_CLOCK, "cancel_timer",
                                  {"timer_id": timer_id}, source="ui")
    if result.get("clarification"):
        raise HTTPException(status_code=409, detail=result.get("message"))
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("message"))
    return {"receipt": result}


class ReminderCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)
    due_utc: str = Field(..., max_length=64)
    idempotency_key: Optional[str] = Field(default=None, max_length=120)


@app.get("/api/reminders")
def get_reminders(status: str = "pending", db: Session = Depends(get_db)):
    q = db.query(Reminder)
    if status != "all":
        q = q.filter(Reminder.status == status)
    return [r.to_dict() for r in q.order_by(Reminder.due_utc).all()]


@app.post("/api/reminders")
def create_reminder(req: ReminderCreate, db: Session = Depends(get_db)):
    try:
        due = _parse_iso_utc(req.due_utc)
    except ValueError:
        raise HTTPException(status_code=400, detail="due_utc must be ISO-8601")
    result = tools.execute_intent(
        db, SYSTEM_CLOCK, "create_reminder",
        {"text": req.text, "due_utc": due},
        source="ui", idempotency_key=req.idempotency_key)
    if result.get("clarification"):
        raise HTTPException(status_code=409, detail=result.get("message"))
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message"))
    reminder = db.query(Reminder).filter(Reminder.id == result["entity_id"]).first()
    return {"receipt": result, "reminder": reminder.to_dict()}


class SnoozeRequest(BaseModel):
    minutes: int = Field(default=10, ge=1, le=24 * 60)


@app.post("/api/reminders/{reminder_id}/snooze")
def snooze_reminder(reminder_id: int, req: SnoozeRequest, db: Session = Depends(get_db)):
    result = tools.execute_intent(
        db, SYSTEM_CLOCK, "snooze_reminder",
        {"reminder_id": reminder_id, "snooze_seconds": req.minutes * 60}, source="ui")
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("message"))
    return {"receipt": result}


class FocusStartRequest(BaseModel):
    duration_seconds: int = Field(default=25 * 60, ge=60, le=8 * 3600)
    objective: str = Field(default="", max_length=300)
    idempotency_key: Optional[str] = Field(default=None, max_length=120)


class FocusEndRequest(BaseModel):
    note: str = Field(default="", max_length=1000)


@app.get("/api/focus")
def get_focus(db: Session = Depends(get_db)):
    active = (db.query(FocusSession).filter(FocusSession.status == "active")
              .order_by(FocusSession.start_utc.desc()).first())
    recent = (db.query(FocusSession).filter(FocusSession.status != "active")
              .order_by(FocusSession.id.desc()).limit(5).all())
    return {"active": active.to_dict() if active else None,
            "recent": [s.to_dict() for s in recent]}


@app.post("/api/focus/start")
def start_focus(req: FocusStartRequest, db: Session = Depends(get_db)):
    result = _exec_or_400(tools.execute_intent(
        db, SYSTEM_CLOCK, "start_focus_session",
        {"duration_seconds": req.duration_seconds, "objective": req.objective},
        source="ui", idempotency_key=req.idempotency_key))
    session = db.query(FocusSession).filter(FocusSession.id == result["entity_id"]).first()
    return {"receipt": result, "session": session.to_dict()}


@app.post("/api/focus/end")
def end_focus(req: FocusEndRequest, db: Session = Depends(get_db)):
    result = tools.execute_intent(db, SYSTEM_CLOCK, "end_focus_session",
                                  {"outcome_note": req.note}, source="ui")
    if not result.get("success"):
        raise HTTPException(status_code=409, detail=result.get("message"))
    return {"receipt": result}


@app.get("/api/alerts")
def get_alerts(status: str = "pending", db: Session = Depends(get_db)):
    q = db.query(ScheduledAlert)
    if status != "all":
        q = q.filter(ScheduledAlert.status == status)
    return [a.to_dict() for a in q.order_by(ScheduledAlert.due_utc).limit(100).all()]


@app.get("/api/receipts")
def get_receipts(limit: int = 50, db: Session = Depends(get_db)):
    limit = max(1, min(limit, 200))
    rows = (db.query(ActionReceipt).order_by(ActionReceipt.id.desc())
            .limit(limit).all())
    return [r.to_dict() for r in rows]


# How far back the hub re-lists alerts that already fired (see hub_state).
RECENT_ALERT_WINDOW = timedelta(hours=6)


@app.get("/api/hub/state")
def hub_state(db: Session = Depends(get_db)):
    """One aggregated payload for the Productivity Hub."""
    now = SYSTEM_CLOCK.now_utc()
    open_tasks = (db.query(Task).filter(Task.completed.is_(False))
                  .order_by(Task.deadline_utc.is_(None), Task.deadline_utc, Task.id)
                  .limit(20).all())
    active_timers = (db.query(Timer).filter(Timer.status == "active")
                     .order_by(Timer.end_utc).all())
    next_reminder = (db.query(Reminder).filter(Reminder.status == "pending")
                     .order_by(Reminder.due_utc).first())
    active_focus = (db.query(FocusSession).filter(FocusSession.status == "active")
                    .order_by(FocusSession.start_utc.desc()).first())
    missed = (db.query(ScheduledAlert).filter(ScheduledAlert.status == "missed")
              .order_by(ScheduledAlert.due_utc.desc()).limit(10).all())
    # Delivered is not the same as SEEN: with the overlay hidden the renderer
    # only pushes an invisible in-app toast (measured 2026-09-22), so recently
    # delivered alerts are listed here for the next time the hub is opened.
    recent = (db.query(ScheduledAlert)
              .filter(ScheduledAlert.status == "delivered",
                      ScheduledAlert.delivered_at >= now - RECENT_ALERT_WINDOW)
              .order_by(ScheduledAlert.delivered_at.desc()).limit(10).all())
    return {
        "now_utc": timeutil.SystemClock().now_utc().isoformat() + "Z",
        "timezone": timeutil.tz_context_name(),
        "open_tasks": [t.to_dict() for t in open_tasks],
        "active_timers": [t.to_dict() for t in active_timers],
        "next_reminder": next_reminder.to_dict() if next_reminder else None,
        "active_focus": active_focus.to_dict() if active_focus else None,
        "missed_alerts": [a.to_dict() for a in missed],
        "recent_alerts": [a.to_dict() for a in recent],
    }


# ==========================================
# P2 WORKSPACES / TODAY / SESSION API  (durable "Resume my work")
# ==========================================
# These reuse the SAME executor (tools.execute_intent) + ActionReceipt
# idempotency as typed/voice commands — no second action framework. Reads
# (list/resume/today/draft) return no receipt; writes are idempotent.

class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    type: str = Field(default="personal", max_length=20)
    goal: Optional[str] = Field(default=None, max_length=500)
    next_action: Optional[str] = Field(default=None, max_length=500)
    path: Optional[str] = Field(default=None, max_length=600)
    idempotency_key: Optional[str] = Field(default=None, max_length=120)


class WorkspaceUpdate(BaseModel):
    goal: Optional[str] = Field(default=None, max_length=500)
    next_action: Optional[str] = Field(default=None, max_length=500)
    blocker: Optional[str] = Field(default=None, max_length=500)
    status: Optional[str] = Field(default=None, max_length=20)
    path: Optional[str] = Field(default=None, max_length=600)
    idempotency_key: Optional[str] = Field(default=None, max_length=120)


class SessionNoteCreate(BaseModel):
    outcome: Optional[str] = Field(default=None, max_length=2000)
    blocker: Optional[str] = Field(default=None, max_length=1000)
    next_action: Optional[str] = Field(default=None, max_length=1000)
    workspace_id: Optional[int] = Field(default=None, ge=1)
    name: Optional[str] = Field(default=None, max_length=200)
    idempotency_key: Optional[str] = Field(default=None, max_length=120)


@app.get("/api/workspaces")
def list_workspaces(status: str = "all", db: Session = Depends(get_db)):
    q = db.query(Workspace)
    if status != "all":
        q = q.filter(Workspace.status == status)
    return [w.to_dict() for w in q.order_by(Workspace.updated_utc.desc()).all()]


@app.post("/api/workspaces")
def create_workspace(req: WorkspaceCreate, db: Session = Depends(get_db)):
    params = {"name": req.name, "type": req.type, "goal": req.goal,
              "next_action": req.next_action, "path": req.path}
    result = _exec_or_400(tools.execute_intent(
        db, SYSTEM_CLOCK, "register_workspace", params, source="ui",
        idempotency_key=req.idempotency_key))
    ws = db.get(Workspace, result["entity_id"])
    return {"receipt": result, "workspace": ws.to_dict() if ws else None}


@app.put("/api/workspaces/{workspace_id}")
def modify_workspace(workspace_id: int, req: WorkspaceUpdate, db: Session = Depends(get_db)):
    params = {k: v for k, v in {
        "goal": req.goal, "next_action": req.next_action, "blocker": req.blocker,
        "status": req.status, "path": req.path}.items() if v is not None}
    params["workspace_id"] = workspace_id
    result = _exec_or_400(tools.execute_intent(
        db, SYSTEM_CLOCK, "update_workspace", params, source="ui",
        idempotency_key=req.idempotency_key))
    ws = db.get(Workspace, workspace_id)
    return {"receipt": result, "workspace": ws.to_dict() if ws else None}


@app.post("/api/workspaces/{workspace_id}/resume")
def resume_one_workspace(workspace_id: int, db: Session = Depends(get_db)):
    result = tools.execute_intent(
        db, SYSTEM_CLOCK, "resume_workspace", {"workspace_id": workspace_id}, source="ui")
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("message"))
    ws = db.get(Workspace, workspace_id)
    notes = (db.query(SessionNote).filter(SessionNote.workspace_id == workspace_id)
             .order_by(SessionNote.created_utc.desc()).limit(5).all())
    tasks = (db.query(Task).filter(Task.workspace_id == workspace_id,
             Task.completed.is_(False)).order_by(Task.deadline_utc.is_(None),
             Task.deadline_utc).all())
    return {"receipt": result, "workspace": ws.to_dict() if ws else None,
            "session_notes": [n.to_dict() for n in notes],
            "open_tasks": [t.to_dict() for t in tasks]}


@app.get("/api/workspaces/{workspace_id}/notes")
def workspace_notes(workspace_id: int, db: Session = Depends(get_db)):
    notes = (db.query(SessionNote).filter(SessionNote.workspace_id == workspace_id)
             .order_by(SessionNote.created_utc.desc()).all())
    return [n.to_dict() for n in notes]


@app.post("/api/session/notes")
def create_session_note(req: SessionNoteCreate, db: Session = Depends(get_db)):
    params = {k: v for k, v in {
        "outcome": req.outcome, "blocker": req.blocker, "next_action": req.next_action,
        "workspace_id": req.workspace_id, "name": req.name}.items() if v is not None}
    result = _exec_or_400(tools.execute_intent(
        db, SYSTEM_CLOCK, "add_session_note", params, source="ui",
        idempotency_key=req.idempotency_key))
    note = db.get(SessionNote, result["entity_id"])
    return {"receipt": result, "note": note.to_dict() if note else None}


@app.get("/api/today")
def today(db: Session = Depends(get_db)):
    result = tools.execute_intent(db, SYSTEM_CLOCK, "get_today", {}, source="ui")
    return {"message": result.get("message"), "items": result.get("items")}


@app.get("/api/session/draft")
def session_draft(db: Session = Depends(get_db)):
    result = tools.execute_intent(db, SYSTEM_CLOCK, "build_session_draft", {}, source="ui")
    return {"message": result.get("message"), "draft": result.get("draft")}


# ==========================================
# STAGE 3 API — smallest academic loop (subject / coursework / due / effort)
# ==========================================

class CourseworkCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    kind: str = Field(default="assignment", max_length=30)
    subject: Optional[str] = Field(default=None, max_length=200)   # registered academic project name
    workspace_id: Optional[int] = Field(default=None, ge=1)         # or the chosen subject id
    due: Optional[str] = Field(default=None, max_length=120)        # natural phrase; validated
    effort_text: Optional[str] = Field(default=None, max_length=120)
    effort_minutes: Optional[int] = Field(default=None, ge=1, le=100000)
    idempotency_key: Optional[str] = Field(default=None, max_length=120)


@app.get("/api/coursework")
def coursework_list(workspace_id: int = 0, include_done: bool = False,
                    db: Session = Depends(get_db)):
    q = db.query(Coursework)
    if not include_done:
        q = q.filter(Coursework.completed.is_(False))
    if workspace_id:
        q = q.filter(Coursework.workspace_id == workspace_id)
    rows = q.order_by(Coursework.due_utc.is_(None), Coursework.due_utc).all()
    return [c.to_dict() for c in rows]


@app.post("/api/coursework")
def coursework_create(req: CourseworkCreate, db: Session = Depends(get_db)):
    params = {k: v for k, v in {
        "title": req.title, "kind": req.kind, "subject": req.subject,
        "workspace_id": req.workspace_id, "due": req.due,
        "effort_text": req.effort_text, "effort_minutes": req.effort_minutes}.items()
        if v is not None}
    result = _exec_or_400(tools.execute_intent(
        db, SYSTEM_CLOCK, "add_coursework", params, source="ui",
        idempotency_key=req.idempotency_key))
    c = db.get(Coursework, result["entity_id"])
    return {"receipt": result, "coursework": c.to_dict() if c else None}


@app.post("/api/coursework/{coursework_id}/complete")
def coursework_complete(coursework_id: int, db: Session = Depends(get_db)):
    result = _exec_or_400(tools.execute_intent(
        db, SYSTEM_CLOCK, "complete_coursework", {"coursework_id": coursework_id},
        source="ui"))
    c = db.get(Coursework, coursework_id)
    return {"receipt": result, "coursework": c.to_dict() if c else None}


@app.get("/api/study/suggest")
def study_suggest(minutes: int = 25, db: Session = Depends(get_db)):
    minutes = max(5, min(1440, minutes))
    result = tools.execute_intent(db, SYSTEM_CLOCK, "suggest_study",
                                  {"minutes": minutes}, source="ui")
    return {"message": result.get("message"), "items": result.get("items")}


# ==========================================
# AI RADAR API (daily research digest — no paid API required)
# ==========================================

@app.get("/api/ai-radar")
def get_ai_radar(db: Session = Depends(get_db)):
    """Stored radar items + last-run/freshness. Read-only, never hits network."""
    return ai_radar.get_radar_state(db, SYSTEM_CLOCK)


@app.post("/api/ai-radar/refresh")
def refresh_ai_radar(db: Session = Depends(get_db)):
    """User-invoked 'Refresh now'. Sync def -> runs in a threadpool worker, so
    the blocking network pass never stalls the event loop."""
    result = ai_radar.run_radar(db, SYSTEM_CLOCK, trigger="manual")
    return {
        "status": result["status"],
        "new_items": result["new_items"],
        "per_source": result["per_source"],
        "state": ai_radar.get_radar_state(db, SYSTEM_CLOCK),
    }


@app.post("/api/ai-radar/items/{item_id}/read")
def mark_radar_item_read(item_id: int, db: Session = Depends(get_db)):
    item = db.query(ai_radar.AIRadarItem).filter(ai_radar.AIRadarItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    item.is_read = True
    db.commit()
    return {"status": "ok", "id": item_id}


class NoteUpdate(BaseModel):
    text: str = Field(..., max_length=10000)


@app.get("/api/notes")
def get_notes(db: Session = Depends(get_db)):
    note = db.query(Note).first()
    if not note:
        note = Note(text="")
        db.add(note)
        db.commit()
        db.refresh(note)
    return {"text": note.text}


@app.post("/api/notes")
def update_notes(note_update: NoteUpdate, db: Session = Depends(get_db)):
    note = db.query(Note).first()
    if not note:
        note = Note(text=note_update.text)
        db.add(note)
    else:
        note.text = note_update.text
    db.commit()
    return {"status": "updated"}


# ==========================================
# LOCAL SPEECH-TO-TEXT (Whisper)
# ==========================================

import tempfile
import shutil

_whisper_model = None


@app.post("/api/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """
    Receives an audio blob (webm/ogg/wav) from the frontend MediaRecorder and
    transcribes it locally with the same guarded faster-whisper pipeline the
    wake-word path uses (no network, no API key).
    """
    tmp_path = None
    try:
        # Validate file size (max 10MB)
        if file.size and file.size > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Audio file too large (max 10MB)")

        suffix = ".webm"
        for ext in (".webm", ".ogg", ".wav", ".mp3", ".m4a"):
            if file.filename and file.filename.lower().endswith(ext):
                suffix = ext
                break

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name

        # Shared pipeline with the wake-word path: one model instance, same
        # guards (beam/temperature pinned, VAD, hallucination filter).
        transcript = await asyncio.to_thread(voice_service.transcribe_source, tmp_path)

        return {"transcript": transcript}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)[:200]}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
