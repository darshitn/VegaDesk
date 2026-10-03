# VEGA: what we are building and where it stands

Date: 1 October 2026. Source review of main at f5f4bcd. This is a product reset report, not a new implementation milestone.

**Later review update, 1 October:** P1-D2 implementation is now present at
`60dd2da`. Codex reran 23 migration tests successfully, but additional temporary
probes found incomplete schema validation and partial DDL persistence after an
injected failure. Run [the correction](ANTIGRAVITY_P1D2_CORRECTION.md) before
P1-E. The full 388-test result remains Antigravity-reported. See
[the polished beta roadmap](POLISHED_BETA_ROADMAP.md) for the current sequence;
the original report below records the earlier source snapshot.

## 1. The purpose, in plain English

VEGA should help you start useful work, remember commitments, stay focused, and return to a project without reconstructing everything from memory. You should be able to speak or type in English. Everyday actions should work without buying API tokens.

A useful definition is: **a local personal work assistant for your studies and projects, with voice as an input method.**

It is not currently an autonomous software engineer, a replacement for Windows, or an assistant that can control any application. The name AgentOS describes an architectural ambition; this remains a Windows desktop application.

Your original goal does not require a rewrite. Much of the necessary daily-work foundation already exists. The missing step is turning those parts into a reliable, understandable routine on your real PC.

## 2. Why the project feels off track

- Feature breadth grew faster than the core daily experience: themes, 3D visuals, gestures, telemetry, weather, crypto, news, voice, academics, and execution policy now coexist.
- Two roadmaps reuse the same labels. Historical P1/P2/P3 added model routing, project sessions, and academic planning. Current AgentOS Phase 1 strengthens execution and persistence. Returning to Phase 1 did not erase the earlier features.
- Long handoffs emphasize test counts and safety fixes. Those matter, but they do not show whether speaking a request actually saves you time.
- Documentation sometimes describes intended behavior or older defaults rather than current code.
- “The model can answer” and “VEGA can perform that action” have been treated too similarly. A model needs an implemented, permitted tool to act.

## 3. Current position

**Implemented productivity prototype, with foundation hardening still in progress and real desktop acceptance incomplete.**

| Current AgentOS milestone | Evidence in the current tree |
| --- | --- |
| P1-A: architecture assessment | Recorded in implementation log |
| P1-B: URL execution and receipts | Source and focused tests present |
| P1-C1: registered app launching | Source and focused tests present; reports launch acceptance, not observed window success |
| P1-C2: policy across current tools | Source and tests present; 26 tools classified in the log |
| P1-D1: scheduler restart recovery | Source and focused tests present; latest completed log entry |
| P1-D2: stronger migration/backup verification | A handoff prompt exists; the requested expanded coverage is not recorded as completed |
| P1-E: service/provider contracts | Existing adapters are implemented; this milestone is not recorded as closed |
| Broad desktop/browser automation | Future work |

The latest log reports 371 backend and 19 frontend passing tests. These are **previously reported results**, not tests rerun for this report. Existing migration tests cover six cases, including a v5 receipt-column upgrade; that is not the full pending D2 backup/corruption/WAL matrix.

Source review establishes implementation, not real microphone accuracy, hidden-window notifications, installer reliability, or current model quality. No app was started, no model loaded, and no personal database or configuration was inspected or modified for this report.

## 4. What VEGA can do now

| Capability | What is implemented | Important limit |
| --- | --- | --- |
| Tasks | Create, list, complete, deadlines, project links | Natural-language coverage is finite; unsupported phrasing may need a model or clarification |
| Timers/reminders | Create/cancel timers, reminders/snooze, persistent schedule | App must be running for live delivery; an asleep/off PC cannot deliver on time |
| Focus | Start/end focus sessions | Does not establish that you actually studied or block distractions |
| Today view | Upcoming work and saved next actions | Based on what you entered, not automatic knowledge of your life |
| Projects | Register a workspace, save its goal, next action and blocker | Registration is not indexing or understanding the entire codebase |
| Resume | Show project context, linked tasks, latest notes and read-only Git status | Does not restore every app/tab or perform the next coding step |
| Session closure | Generate a draft from recorded activity; edit and save it | Drafts are not a recording of everything you did outside VEGA |
| Academics | Coursework, due dates, estimated effort, suggestions fitting available minutes | Deadline/effort selection, not syllabus understanding or intelligent tutoring |
| App/website opening | Registered apps and supported web targets through policy checks | Launch accepted does not prove the correct window is visible or ready |
| Voice | Hey Jarvis detection, speech capture/transcription, shared command routing | VEGA branding does not change the pretrained wake phrase; microphone acceptance remains necessary |
| Spoken replies | Browser speech synthesis | Voice availability and offline behavior depend on installed voices/runtime |
| AI chat/tool selection | Local Ollama or optional Gemini; bounded history and tool proposals | One action per model request; no autonomous multi-step execution loop |
| AI Radar | Scheduled/manual collection from configured public sources, categorization and deduplication | A curated feed collector, not an open-ended research agent or unlimited-token guarantee |
| Dashboard extras | Telemetry, feeds, themes, 3D and gesture features | Secondary to productivity and potential resource consumers |

## 5. The technology, explained

| Technology | Role in VEGA |
| --- | --- |
| Electron | Makes the app a Windows desktop window with tray/hotkey integration |
| React + Vite | Builds and renders the dashboard; current components are primarily JavaScript/JSX |
| Tailwind/CSS, Framer Motion | Styling and animation |
| Python + FastAPI | Handles commands, APIs, integrations and backend services |
| SQLite + SQLAlchemy | Stores tasks, reminders, project/session records and action receipts locally |
| Parser/dispatcher | Recognizes supported commands using ordinary code, without spending model tokens |
| Tool registry, policy, executor, verifier | Defines available actions, checks inputs/permissions, executes and reports evidence |
| Ollama | Runs a separately installed language model locally; it is the runtime, not the model itself |
| Gemini adapter | Optional external model service; credentials, availability and quotas are separate concerns |
| openWakeWord | Detects the pretrained Hey Jarvis wake phrase |
| sounddevice, VAD, faster-whisper | Captures speech, detects speech boundaries, transcribes locally |
| WebSockets | Sends voice/alert/system updates to the UI |
| Three.js/React Three Fiber, MediaPipe | Optional 3D and camera gesture features |
| pytest; Node test runner; oxlint | Backend tests, frontend logic tests, frontend lint |
| PyInstaller + electron-builder | Packages Python backend and desktop application; release acceptance is separate |

The code currently defaults Whisper to `small.en`, running on CPU with int8 computation, configurable through `WHISPER_MODEL`. The repository cleanup corrected README to match this default and to name the frontend test runner accurately: package.json runs `node --test`.

The model used in your coding IDE to develop VEGA is separate from the model running inside VEGA. Having Qoder/Antigravity credits does not automatically provide VEGA with an inference API or those same capabilities.

## 6. How a request works

```text
Your voice -> wake detection -> capture -> transcription --+
Your typed message --------------------------------------+
                                                        v
                                             Supported command?
                                             /              \
                                           yes              no
                                            |                |
                                      ordinary code     chosen AI provider
                                            |                |
                                            +--- validated action proposal
                                                        |
                                             policy + executor
                                                        |
                                             result / receipt
                                                        |
                                             UI / spoken response
```

For a recognized timer command, the model is unnecessary. For an unfamiliar paraphrase, a model can propose an existing timer tool. It cannot create a calendar integration simply by saying it booked an appointment.

The model lane currently rejects multiple proposed actions and returns ordinary model text when no tool is proposed. Therefore the existence of receipt-based tools does not mean every free-text model statement is independently verified.

## 7. What “free” realistically means

The sustainable baseline is zero recurring API spend: ordinary commands plus local speech and a local model on hardware you own. Downloads, disk space, electricity, RAM/VRAM, and computation are still required.

There is no purchased token balance to exhaust for truly local inference, but there are limits on context length, output length, speed and model capability. VEGA already budgets conversation context; that does not create unlimited memory or intelligence.

Cloud free tiers should be optional. They cannot be the dependency that keeps your timer or task list working. Radar discovering a zero-priced catalog entry also does not grant credentials, remove limits, or integrate that provider.

The current source default for `LLM_PROVIDER` is Gemini, and the Ollama fallback model name is `llama3`. Those are defaults in code, not a claim about your private .env. A zero-cost onboarding milestone should explicitly select a working installed local model and explain availability. Do not pick a new default from old benchmark claims alone.

## 8. What it cannot currently do

- Reliably navigate arbitrary applications, click their controls, inspect the resulting screen and repair failed actions.
- Independently edit/build/debug/test your projects in an autonomous coding loop.
- Read all your files or course PDFs and answer with grounded document citations.
- Write calendar events or send email through an implemented account integration.
- Execute a long multi-step request with durable progress, cancellation and recovery.
- Load arbitrary future plugins/MCP servers through a finished extension platform.
- Promise unlimited frontier-model quality with no cost or hardware tradeoff.

These are possible future capabilities, not hidden features you have failed to discover. Playwright, Windows UI Automation, Piper, embeddings and an MCP adapter appear as possible future directions in the brief; they are not all current VEGA components.

## 9. Recommended direction: make one daily loop excellent

Freeze new cosmetic and feed features for the next milestone. Keep the existing architecture. Make the primary screen show Today, current focus, upcoming deadline and Resume; put telemetry/news/3D in secondary views.

The target daily experience is:

1. Ask what needs attention today; see real saved deadlines.
2. Choose a study task or resume a project with its last next action.
3. Start a focus session with one clear objective.
4. Receive a reminder while working in another app.
5. Save what you achieved, your blocker and the next action.
6. Restart VEGA tomorrow and recover that context.

Much of the data/backend support exists. The milestone is integrating and verifying the experience, not rebuilding those modules.

## 10. A bounded next plan

### Next: finish the reliability gate

Complete P1-D2 against temporary databases, then the small P1-E readiness checks. Show whether backend, local model, microphone and scheduler are actually available; current `/health` returns only a basic status and voice state.

Then run a supervised Windows acceptance session: voice create/list task, timer while hidden, reminder, app launch, theme change with chat retained, scroll to every task, project resume, editable session note, and restart persistence. Record each as pass/fail/unverified. Fix failures before expanding scope.

### After that: one week of personal use

Measure useful outcomes: completed workflows without repair, lost/duplicate records, missed alerts, voice-to-result delay, idle CPU/RAM, and failures needing exact phrasing. Do not optimize for test count. Establish a baseline before promising numerical performance targets.

Pause hidden 3D/gesture activity where appropriate, reduce unnecessary polling, and load expensive inference only as needed after measuring the actual resource cost. Faster-whisper and the LLM have separate resource budgets.

### First new capability: project launch routine

After basic acceptance, implement one explicit workflow: choose a registered project, show its next action, open its approved editor/folder and a small saved set of links, optionally start focus. Add cancellation and truthful per-step results. This makes VEGA more useful without promising control of every application.

### Later, choose one direction

- Study companion: selected-folder/PDF import and answers with source citations.
- Project companion: read-only selected-repository summary, then scoped code-editing jobs with review and tests.
- Desktop assistant: a few supported app/browser workflows using structured controls and verification.

Choose based on the real task you repeat most, rather than implementing all three in parallel. New tools should reuse the existing registry, policy and execution boundaries.

## 11. Definition of success

You should be able to use VEGA for a week to capture commitments, focus, resume projects and receive reminders without losing data, paying for API calls, or debugging the app every day. It should clearly explain unsupported requests and model outages.

That is the next meaningful release. Broader intelligence becomes worthwhile once it sits on a daily assistant you already trust.

## Source map and audit limits

- Current direction: `AGENTS.md`, `docs/VEGA_AGENTOS_BRIEF.md`, `docs/PHASE1_PLAN.md`, `docs/IMPLEMENTATION_LOG.md`.
- Request execution: `backend/main.py`, `dispatcher.py`, `model_lane.py`, `tool_registry.py`, `tools.py`, `policy.py`, `system_actions.py`, `verifier.py`.
- Projects/study: `backend/workspaces.py`, `frontend/src/components/ProductivityHub.jsx`.
- State/recovery: `backend/db.py`, `migrations.py`, `scheduler.py`, `tests/test_migrations.py`.
- Models/voice: `backend/providers/`, `context_builder.py`, `voice_service.py`, `frontend/src/hooks/useChat.js`.
- Actual dependencies/scripts: root/frontend `package.json`, `backend/requirements.txt`.

This report used current source inspection and a bounded read-only capability review. It is not an exhaustive security audit or a fresh runtime certification. No external model leaderboard or pricing research was needed to describe the installed architecture. No application code, personal data, configuration, commits or remote state was changed.
