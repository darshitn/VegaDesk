"""Exercise a built backend against a disposable beta database, without models or GUI."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request
import uuid

from websockets.sync.client import connect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backend", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    backend = args.backend.resolve(strict=True)
    report_path = args.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="packaged-smoke-", dir=report_path.parent))
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    run_id = "release-smoke-" + uuid.uuid4().hex
    env = os.environ.copy()
    for key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                "GROQ_API_KEY", "MISTRAL_API_KEY", "VEGA_BETA_OLLAMA",
                "VEGA_BETA_VOICE", "VEGA_ENABLE_VOICE"):
        env.pop(key, None)
    env.update(
        VEGA_PROFILE="beta", VEGA_PROFILE_ROOT=str(scratch),
        VEGA_PORT=str(port), VEGA_RUN_ID=run_id, VEGA_PACKAGED="1",
        JARVIS_DB_PATH=str(scratch / "db" / "jarvis-beta.db"),
        VEGA_DISABLE_VOICE="1", VEGA_DISABLE_RADAR="1",
        LLM_PROVIDER="none", PYTHON_DOTENV_DISABLED="1",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = f"http://127.0.0.1:{port}"
    checks = []
    process = None
    log_file = (scratch / "backend.log").open("ab")

    def request(route, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(base + route, data=data,
                                     headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=3) as response:
            return json.load(response)

    def start():
        child = subprocess.Popen(
            [str(backend)], env=env, cwd=scratch,
            stdout=log_file, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError(f"Packaged backend exited {child.returncode}; inspect {scratch / 'backend.log'}")
            try:
                health = request("/health")
                if (health.get("profile") == "beta" and health.get("run_id") == run_id
                        and health.get("database", {}).get("status") == "ready"):
                    return child
            except (OSError, ValueError):
                pass
            time.sleep(0.2)
        stop(child)
        raise RuntimeError("Packaged backend readiness exceeded 60 seconds")

    def stop(child):
        if child is None or child.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                           capture_output=True, timeout=15, check=False)
        else:
            child.terminate()
        child.wait(timeout=15)

    result = {
        "backend_sha256": hashlib.sha256(backend.read_bytes()).hexdigest(),
        "checks": checks,
        "boundary": "Packaged backend with synthetic state. Desktop, native notifications and microphone are not exercised.",
    }
    try:
        process = start()
        checks.append("fresh profile readiness and matching run identity")
        payload = {"message": "add task: packaged release persistence check",
                   "idempotencyKey": "release-task"}
        first = request("/chat", payload)
        replay = request("/chat", payload)
        assert first["receipt"]["success"] is True, first
        assert replay["receipt"].get("replayed") is True, replay
        tasks = request("/api/tasks")
        assert len(tasks) == 1, tasks
        checks.append("chat task commit and idempotent replay")
        with connect(f"ws://127.0.0.1:{port}/ws/alerts",
                     origin="http://localhost:5173", open_timeout=5) as alerts:
            timer = request("/chat", {"message": "timer 2s", "idempotencyKey": "release-timer"})
            assert timer["receipt"]["success"] is True, timer
            event = json.loads(alerts.recv(timeout=10))
            assert event.get("type") == "alert", event
            assert event["alert"]["kind"] == "timer_due", event
            assert event["alert"]["entity_id"] == timer["receipt"]["entity_id"], event
        checks.append("real timer scheduler and WebSocket alert")
        disabled = request("/api/voice/enabled", {"enabled": True})
        assert disabled["enabled"] is False and "--voice" in disabled["error"], disabled
        checks.append("packaged voice opt-in refusal and actionable guidance")
        stop(process)
        process = start()
        assert len(request("/api/tasks")) == 1
        checks.append("persisted task after packaged backend restart")
        result["status"] = "passed"
    except Exception as error:
        result["status"] = "failed"
        result["error"] = str(error)
    finally:
        stop(process)
        log_file.close()
        with socket.socket() as probe:
            free = probe.connect_ex(("127.0.0.1", port)) != 0
        result["port_released"] = free
        if not free:
            result["status"] = "failed"
        report_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
