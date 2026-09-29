# VEGA: local-first assistant roadmap

Status: implementation plan, 22 September 2026. No feature in this document is considered implemented merely because it appears here. See [the overnight implementation specification](VEGA_OVERNIGHT_SPEC.md) and [the Qoder prompt](QODER_OVERNIGHT_PROMPT.md).

## Product goal and constraints

VEGA should help one user control their Windows PC, stay on top of academics, and resume personal projects. The useful experience is: say or type a request, get a verifiable action, and see that action still present after a restart. English is the initial language. Routine commands, timers, tasks, reminders, and study sessions must work without internet, API credit, or Ollama. Recurring software/API spending target: ₹0. Existing electricity and internet are outside that target.

Keep Electron/React, FastAPI, SQLite, openWakeWord, faster-whisper, and the current Ollama/Gemini choices. Preserve all user data and current interactions. Cloud models are optional for open-ended reasoning, with a visible provider choice and no automatic move to a paid API.

The development PC has a Ryzen 7 7435HS, 24 GiB RAM, and an RTX 3050 A laptop GPU with 4 GiB VRAM (read-only system check on 22 September). Model size is not runtime memory. Benchmark Qwen3.5 2B and 4B locally before selecting a default; start with a modest context size. Ollama is not a dependency for deterministic commands.

## Verified current state

| Area | Current source | Consequence |
| --- | --- | --- |
| Assistant chat | `backend/main.py` exposes only `open_app` and `open_website` as AI tools | Voice cannot actually add tasks or schedule anything. |
| Tasks | `backend/db.py` Task has `id`, `text`, `completed` | No due dates, priority, subject, or reminder linkage. |
| Notes | One local Note record in SQLite | No document-level knowledge store. |
| Voice | `backend/voice_service.py` uses `hey_jarvis`, VAD, `small.en` on CPU by default | Keep this path while testing a VEGA keyword. |
| Manual transcription | `backend/main.py` creates another Whisper instance | Potential extra memory and a blocking request. |
| Renderer | `AIBrain.jsx` holds message history in component state | History is lost when the component unmounts. |
| Focus | `NeuralCosmos.jsx` advertises Focus Mode | Real focus workflow is not present. |
| Packaged data | `backend/db.py` puts SQLite next to the frozen executable | Move new persistent state to a stable writable user-data location, with migration. |

`BUILDPLAN.md` contains historical audit findings that may be stale. Check current source before treating one as an open defect. At this audit, WebSocket origin checks, persisted theme settings, and empty PyInstaller `datas` were present. Read-only frontend lint exited successfully with warnings; this was not a voice or installer test.

## Architecture

```text
wake / hotkey / typed input
          ↓
 one assistant command dispatcher
          ├── deterministic, validated intents → local tools → action receipt
          └── ambiguous or open-ended request → selected model → validated tools/result
          ↓
      SQLite state + action log
          ↓
 voice reply / desktop notification / dashboard

persistent scheduler reads SQLite → due event → notification + receipt
```

The backend owns command interpretation and execution. The renderer shows state and sends requests; it does not own the only copy of assistant memory or timing. Tool inputs have schemas and bounded values. Tool results report success/failure, IDs, and timestamps. Give mutating requests an idempotency key so retries cannot create duplicate tasks or reminders. Separate `set_task_completed(id, true)` from the existing UI toggle to avoid reversing state on retry. For ambiguous dates, ask a specific follow-up before persisting.

First tools: `create_task`, `list_tasks`, `set_task_completed`, `start_timer`, `cancel_timer`, `create_reminder`, `snooze_reminder`, `start_focus_session`, `end_focus_session`, plus existing `open_app` and `open_website`. The same executor serves typed and spoken input and optional model calls. Do not expose a general shell, file deletion, email sending, browser form submission, or arbitrary Python execution as assistant tools.

Before any broader PC control or remote access, add real local authentication/authorization for the HTTP API. Loopback binding and CORS/origin checks alone are not authentication. Keep new low-risk productivity tools local and avoid listening on a network interface during early milestones. Later actions that write outside VEGA, submit something, or change the system need a preview and explicit approval.

## Milestones and completion criteria

### M1 — Dependable daily assistant: first overnight assignment

Implement the scoped behavior in [VEGA_OVERNIGHT_SPEC.md](VEGA_OVERNIGHT_SPEC.md). A typed or spoken command can create a task with an optional deadline, start/cancel a timer, schedule/snooze a reminder, and start/end a focus session. All records survive restart. Deadline and timer alerts work while the dashboard overlay is hidden. Offline deterministic commands do not call Ollama or Gemini. Existing tasks and notes remain intact. Unit/integration tests cover parsing, time zones, restart recovery, idempotency, and API contracts. Build/lint checks run. Tests involving the real microphone, desktop notifications, and installer are reported separately if they cannot be performed.

### M2 — Voice and assistant reliability

Unify model loading and transcription behind one worker, measure English `base.en` versus `small.en`, add speech playback cancellation and prevent self-triggering. Keep the existing Jarvis wake phrase while trialing sherpa-onnx keyword spotting for `HEY VEGA`; compare recordings at several distances and false wake events during normal PC use. Ship a VEGA setting only after it meets a measured threshold. Prepare required model assets for an actual offline run. Never claim offline support from source inspection alone.

### M3 — Academic planner and tutor

Add subjects, exams, assignments, estimated effort, planned study blocks, and a daily briefing. A due-soon suggestion reads real database dates and shows its reason. Ingest user-selected notes/PDFs in a background job; start with SQLite FTS5 and source/page citations. Add Docling for layout/OCR where needed. Quiz from cited content, save missed concepts, and integrate with Anki/FSRS only if that workflow helps the user. Test against one real subject before broadening.

### M4 — Personal project companion

Add a project registry with repository path, safe named commands, last session note, blocker, next action, and observed Git state. `Resume project` opens configured tools and shows an evidence-based summary. Run build/test commands only from an allowlist, capture exit status, and preserve existing uncommitted work. No automatic push, merge, reset, stash, release, or deployment.

### M5 — Screen assistance, repeatable workflows, optional remote access

Begin with user-invoked screen inspection and narrow Windows accessibility/browser actions; verify the target and outcome of each step. Keep screenshots transient by default. Save successful routines with named steps and rollback guidance. Add authenticated phone access over a private network only after local API authentication and permission boundaries are reviewed. A sleeping or powered-off PC cannot execute desktop actions; phone reminders require a separate scheduling path.

## Priority decisions

1. Complete M1 before adding more themes, feeds, or gesture controls. It directly addresses the user's missing daily value.
2. Use deterministic parsing for common commands; route unclear wording to a model only when necessary. A local model can be slow and still useful for tutoring and planning.
3. Treat wake-word naming as an experiment, not a blocker. Preserve the current working path.
4. Start retrieval with full-text search; add embeddings only after real misses are measured.
5. Keep background work bounded. The assistant should avoid noisy notifications, support quiet hours/snooze, and explain why it suggested something.

## Current research references

- [Qoder model selector](https://docs.qoder.com/qoder/model-selector): Qwen3.8-Max and Qwen3.8-Flash appear as selectable models; current credit rates and availability depend on the account.
- [Qoder Goal mode](https://docs.qoder.com/qoder/goal-driven) and [scheduled tasks](https://docs.qoder.com/user-guide/quest/scheduled-tasks): options for longer bounded work, subject to Qoder's permission mode and the IDE remaining open for scheduled tasks.
- [Ollama tool calling](https://docs.ollama.com/capabilities/tool-calling) and [Qwen3.5 model catalog](https://ollama.com/library/qwen3.5).
- [sherpa-onnx keyword spotting](https://k2-fsa.github.io/sherpa/onnx/kws/index.html), [openWakeWord](https://github.com/dscripka/openWakeWord), and [faster-whisper](https://github.com/SYSTRAN/faster-whisper).
- [APScheduler missed jobs](https://apscheduler.readthedocs.io/en/3.x/userguide.html), [SQLite FTS5](https://www.sqlite.org/fts5.html), [Docling offline options](https://docling-project.github.io/docling/usage/advanced_options/), and [Anki FSRS](https://docs.ankiweb.net/deck-options.html#fsrs).
- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing): the current optional Gemini model has a free tier, with limits and different data handling from paid tier. Do not silently send private academic/project content to it.
