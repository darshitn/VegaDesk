from fastapi import FastAPI, Depends, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager
import asyncio
import sys
import os

# Support both `python -m backend.main` and `python backend/run.py` / PyInstaller
try:
    from db import engine, Base, SessionLocal, Task, Note  # when run as `python run.py`
    import voice_service
    import system_actions
except ImportError:
    from .db import engine, Base, SessionLocal, Task, Note
    from . import voice_service
    from . import system_actions

Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    voice_service.start(loop)
    yield
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
from google import genai
from google.genai import types

# Load .env explicitly from the backend directory
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    history: List[Message] = Field(default_factory=list, max_length=100)
    userName: Optional[str] = Field(default=None, max_length=50)
    provider: Optional[str] = None


def _get_genai_client():
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY not configured. Set it in backend/.env or switch provider to 'ollama'.")
    return genai.Client(api_key=GEMINI_API_KEY)


def _chat_action_response(message: str) -> dict:
    """Chat response for an action attempt. open_app/open_website signal
    success by the 'Opening ...' prefix; the renderer uses `opened` to hide
    the dashboard so the launched app/website appears on top of it."""
    return {"response": message, "opened": isinstance(message, str) and message.startswith("Opening ")}


def _try_parse_tool_json(content: str):
    """Weak local models sometimes echo the raw tool-call spec as chat text
    (e.g. {"type":"function","function":{...},"function_input":{...}}). If the
    content parses as one of OUR tools, return (name, args) so it can be
    executed instead of being shown to the user and read aloud."""
    s = (content or "").strip()
    if not (s.startswith("{") and s.endswith("}")):
        return None
    if '"function"' not in s and '"open_app"' not in s and '"open_website"' not in s:
        return None
    try:
        obj = json.loads(s)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    fn = obj.get("function") if isinstance(obj.get("function"), dict) else obj
    name = fn.get("name") or (obj.get("function_input") or {}).get("name")
    args = obj.get("arguments") or obj.get("function_input") or fn.get("parameters") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {}
    if name in ("open_app", "open_website") and isinstance(args, dict):
        clean_args = {k: v for k, v in args.items() if isinstance(v, (str, int, float))}
        required = "name" if name == "open_app" else "site_or_url"
        if str(clean_args.get(required, "")).strip():
            return name, clean_args
    return None


def _sanitize_content(text: str) -> str:
    """Final guard: never show raw JSON blobs to the user (or let TTS read them)."""
    t = (text or "").strip()
    if t.startswith("{") and (t.endswith("}") or '"function"' in t[:200]):
        return "I couldn't complete that request. Please try rephrasing it."
    return t


@app.post("/chat")
def chat_endpoint(req: ChatRequest):
    # 1. Fast Path Check — bypass LLM for obvious open/launch intents
    is_fast, action_resp = system_actions.check_fast_path(req.message)
    if is_fast:
        return _chat_action_response(action_resp)

    # 2. LLM Fallback
    user_name = req.userName.strip() if req.userName and req.userName.strip() else "Sir"
    system_prompt = (
        f"You are V.E.G.A., a highly advanced AI assistant. Be concise, dry-witted, helpful, "
        f"and address the user as {user_name}. For ANY request that means 'access/open/launch/show me/pull up X', "
        f"you MUST use the provided open_app or open_website tools. Do not attempt to answer conversationally for these requests. "
        f"Never print tool definitions or JSON — call the tool instead."
    )

    active_provider = req.provider.lower() if req.provider else LLM_PROVIDER

    if active_provider == "ollama":
        messages = [{"role": "system", "content": system_prompt}]
        for msg in req.history[-20:]:  # cap history to last 20 to avoid token blow-up
            role = "assistant" if msg.role == "assistant" else "user"
            messages.append({"role": role, "content": msg.content})
        messages.append({"role": "user", "content": req.message})

        ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        ollama_model = os.getenv("OLLAMA_MODEL", "llama3")

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "open_app",
                    "description": "Opens a local application by name",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Name of the application (e.g., 'calculator')"}
                        },
                        "required": ["name"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "open_website",
                    "description": "Opens a website by name or URL",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "site_or_url": {"type": "string", "description": "Name of the site or a URL"}
                        },
                        "required": ["site_or_url"]
                    }
                }
            }
        ]

        try:
            resp = requests.post(f"{ollama_base_url}/api/chat", json={
                "model": ollama_model,
                "messages": messages,
                "stream": False,
                "tools": tools
            }, timeout=60)
            resp.raise_for_status()
            data = resp.json()

            msg_data = data.get("message", {})

            # Handle tool calls if any — support multiple and string-encoded args
            if msg_data.get("tool_calls"):
                import json
                for tool_call in msg_data["tool_calls"]:
                    func = tool_call.get("function", {})
                    name = func.get("name")
                    args = func.get("arguments", {})

                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}

                    if not isinstance(args, dict):
                        args = {}

                    if name == "open_app":
                        return _chat_action_response(system_actions.open_app(**args))
                    elif name == "open_website":
                        return _chat_action_response(system_actions.open_website(**args))

            # Weak models sometimes emit the tool call as raw JSON *text* —
            # detect and execute it instead of displaying it.
            content = msg_data.get("content", "") or ""
            parsed = _try_parse_tool_json(content)
            if parsed:
                tool_name, tool_args = parsed
                if tool_name == "open_app":
                    return _chat_action_response(system_actions.open_app(**tool_args))
                return _chat_action_response(system_actions.open_website(**tool_args))
            return {"response": _sanitize_content(content)}
        except requests.exceptions.ConnectionError:
            return {"error": "Ollama is not reachable. Is Ollama running? Check OLLAMA_BASE_URL in .env."}
        except requests.exceptions.Timeout:
            return {"error": "Ollama request timed out. Try a smaller model or increase timeout."}
        except Exception as e:
            return {"error": f"Ollama Error: {str(e)}"}

    elif active_provider == "gemini":
        try:
            client = _get_genai_client()

            contents = []
            for msg in req.history[-20:]:
                role = "model" if msg.role == "assistant" else "user"
                contents.append({"role": role, "parts": [{"text": msg.content}]})

            contents.append({"role": "user", "parts": [{"text": req.message}]})

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    tools=[system_actions.open_app, system_actions.open_website]
                )
            )

            # Handle function calls — support both dict and string arg formats
            if response.function_calls:
                import json
                fc = response.function_calls[0]
                raw_args = getattr(fc, 'args', {}) or {}
                # fc.args may be a string (LLM hallucination) or dict
                if isinstance(raw_args, str):
                    try:
                        args_dict = json.loads(raw_args)
                    except json.JSONDecodeError:
                        args_dict = {}
                elif isinstance(raw_args, dict):
                    args_dict = dict(raw_args)
                else:
                    try:
                        args_dict = dict(raw_args)
                    except Exception:
                        args_dict = {}

                if fc.name == "open_app":
                    # Validate required arg
                    if "name" not in args_dict:
                        return {"response": "I couldn't determine which application you want to open. Please specify the app name."}
                    return _chat_action_response(system_actions.open_app(**args_dict))
                elif fc.name == "open_website":
                    if "site_or_url" not in args_dict:
                        return {"response": "I couldn't determine which website you want to open. Please specify the site name or URL."}
                    return _chat_action_response(system_actions.open_website(**args_dict))

            # response.text may be None if blocked by safety filters
            text = getattr(response, 'text', None)
            if not text:
                # Try to extract from candidates
                try:
                    text = response.candidates[0].content.parts[0].text
                except Exception:
                    text = "I couldn't generate a response (possibly blocked by safety filters). Please rephrase."
            return {"response": _sanitize_content(text)}
        except HTTPException:
            raise
        except Exception as e:
            # Don't leak full stack to frontend, but log it
            print(f"[CHAT] Gemini error: {e}", file=sys.stderr)
            return {"error": f"Gemini Error: {str(e)}"}
    else:
        return {"error": f"Invalid LLM provider '{active_provider}'. Use 'gemini' or 'ollama'."}


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


@app.get("/api/voice/enabled")
def get_voice_enabled():
    return {"enabled": voice_service.is_enabled()}


@app.post("/api/voice/enabled")
def set_voice_enabled(req: VoiceEnabledRequest):
    voice_service.set_voice_enabled(bool(req.enabled))
    return {"enabled": bool(req.enabled), "status": voice_service.is_voice_active()}


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

class TaskCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)


@app.get("/api/tasks")
def get_tasks(db: Session = Depends(get_db)):
    tasks = db.query(Task).all()
    return tasks


@app.post("/api/tasks")
def create_task(task: TaskCreate, db: Session = Depends(get_db)):
    db_task = Task(text=task.text.strip())
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    return db_task


@app.put("/api/tasks/{task_id}")
def toggle_task(task_id: int, db: Session = Depends(get_db)):
    db_task = db.query(Task).filter(Task.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    db_task.completed = not db_task.completed
    db.commit()
    db.refresh(db_task)
    return db_task


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    db_task = db.query(Task).filter(Task.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    db.delete(db_task)
    db.commit()
    return {"status": "deleted"}


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


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        # Keep in sync with voice_service (WHISPER_MODEL env, default small.en)
        _whisper_model = WhisperModel(os.getenv("WHISPER_MODEL", "small.en"), device="cpu", compute_type="int8")
    return _whisper_model


@app.post("/api/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """
    Receives a webm/ogg audio blob from the frontend MediaRecorder,
    transcribes it locally using faster-whisper (no network, no API key).
    """
    tmp_path = None
    try:
        # Validate file size (max 10MB)
        if file.size and file.size > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Audio file too large (max 10MB)")

        model = get_whisper_model()

        suffix = ".webm"
        if file.filename and file.filename.endswith(".ogg"):
            suffix = ".ogg"

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name

        segments, info = model.transcribe(tmp_path, beam_size=5)
        transcript = " ".join(segment.text for segment in segments).strip()

        return {"transcript": transcript}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
