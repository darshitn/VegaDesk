# VEGA AgentOS implementation log

This is a restart point for Antigravity, not proof that historical handoff claims are current. Add dated entries after actual assessment or implementation. Do not copy secrets or personal data here.

## 2026-09-24 — preparation by Codex

- Reframed the active project as VEGA AgentOS in the new brief and agent instructions. Application source and package identifiers have not been renamed.
- Initial inspection found a dirty `main` checkout ahead of `origin/main` by three commits, with modified tracked files and many untracked backend/frontend modules, tests, and docs. These changes were preserved. Recheck status at the start of Antigravity work.
- Existing source appears to include FastAPI, SQLite, scheduler, deterministic dispatcher, tool registry, model providers, Electron/React, and tests. This is source inventory only; current behavior and desktop operation still require verification.
- Moved Qoder-era docs and the original parent-folder build plan to `docs/history/2026-qoder/`. Removed only two old parent-folder console logs and an orphan parent-folder `package-lock.json`.
- Next: execute P1-A assessment, then P1-B vertical slice as specified in `docs/ANTIGRAVITY_START.md`.

## Entry template

### YYYY-MM-DD — stage and result

- Baseline: branch, HEAD, dirty files relevant to this stage.
- Assessment/decision: facts with source paths; why this approach.
- Changed: files and behavior.
- Verification: exact commands, outcomes, mocks/fixtures, real desktop checks, skipped checks.
- Risks/blockers: evidence and safe mitigation.
- Next: one bounded action.

---

## 2026-09-24 — P1-A: Repository-grounded architecture and gap assessment

### 1. Baseline and Environment
- **Branch**: `main`
- **HEAD**: `e7178db207e08400f8f0e167b44503160f20a3da`
- **Dirty-file baseline**:
  - Tracked modified: `.gitignore`, `README.md`, `backend/db.py`, `backend/main.py`, `backend/requirements.txt`, `backend/voice_service.py`, `frontend/package.json`, `frontend/src/App.jsx`, `frontend/src/components/AIBrain.jsx`, `frontend/src/components/LiveFeeds.jsx`, `frontend/src/components/ProductivityHub.jsx`, `frontend/src/index.css`.
  - Untracked: `AGENTS.md`, `backend/ai_radar.py`, `backend/command_parser.py`, `backend/context_builder.py`, `backend/dispatcher.py`, `backend/migrations.py`, `backend/model_lane.py`, `backend/providers/`, `backend/radar_scheduler.py`, `backend/scheduler.py`, `backend/tests/`, `backend/timeutil.py`, `backend/tool_registry.py`, `backend/tools.py`, `backend/workspaces.py`, `docs/`, `frontend/src/components/AIRadarPanel.jsx`, `frontend/src/hooks/`, `frontend/src/lib/`, `frontend/tests/`.
- **Pre-existing test verification**:
  - Backend: `python -m pytest` -> 291 passed, 1 warning (Starlette testclient deprecation) in 19.29s on Python 3.14.3.
  - Frontend: `npm test --prefix frontend` -> 19 passed in 164ms.
- **Subagent Limitation Note**:
  - Autonomous code subagents are unavailable in this environment (tool declarations provide `browser_subagent` for web interaction only). As mandated by `AGENTS.md` (lines 14–18), two bounded read-only reviews were conducted sequentially.

### 2. Sequential Review 1: Actual Request Flow and Persistence Mapping
- **Entry points**:
  - `/chat` (`backend/main.py:147–181`): Takes `ChatRequest` (message, history, provider, userName, source, idempotencyKey).
    1. Line 154: Calls `dispatcher.dispatch_command(...)`. If handled, returns deterministic response with `executionMode: "deterministic"`.
    2. Line 166: Fast-path bypass check `system_actions.check_fast_path(req.message)`. If matched, executes `open_app` or `open_website` directly, bypassing registry, policy, verification, and audit.
    3. Line 174: Calls `model_lane.run_model_turn(...)`.
  - `/api/assistant/command` (`backend/main.py:598–606`): Takes `CommandRequest` and calls `dispatcher.dispatch_command(...)`. If handled, returns structured result (`response`, `receipt`, `clarification`).
  - `/ws/voice` (`backend/main.py:233–248`, `backend/voice_service.py`): Voice service streams mic PCM, runs openWakeWord ("Hey Jarvis") and local Whisper, and pushes transcript JSON to connected clients. The frontend client (`useChat.js:82`) then posts transcripts back to `/chat`.
- **Dispatcher & Parsing**:
  - `backend/dispatcher.py:15–35`: Pure entry point calling `command_parser.parse_command(text, clock)`. If matched, forwards to `tools.execute_intent(db, clock, parsed["intent"], parsed["params"], ...)`.
  - `backend/command_parser.py`: Regex and fuzzy parser for offline commands: task creation/listing/completion, timers, reminders, focus sessions, AI radar digest, and durable workspaces. Contains capability boundary checks (`unsupported_destination`, `is_open_request`).
- **Model Lane & Tool Registry**:
  - `backend/model_lane.py:131–212`: Builds bounded context (`context_builder.py`), requests structured function calls from Ollama/Gemini (`providers/`), enforces 1 proposal per turn, validates via `tool_registry.validate_proposal(proposal, clock)` (`tool_registry.py:474`), and executes via `tool_registry.execute_proposal` (`tool_registry.py:578`).
  - In `tool_registry.py:586–601`: Tools with `kind == "system"` (`open_app`, `open_website`) invoke `system_actions.py` directly, producing `receipt: None` and no audit record.
- **Persistence & Database**:
  - `backend/db.py`: SQLite engine with connection pool (`DB_PATH` or `jarvis.db`). Models: `Task`, `Note`, `Timer`, `Reminder`, `FocusSession`, `ScheduledAlert`, `ActionReceipt`, `AIRadarItem`, `AIRadarRun`, `Workspace`, `Coursework`, `SessionNote`.
  - `backend/migrations.py`: Idempotent schema migrations (`CURRENT_SCHEMA_VERSION = 4`) that back up `jarvis.db` to `.bak-<timestamp>` before any ALTER operation.
  - `ActionReceipt` table (`backend/db.py:209–237`): Stores `idempotency_key`, `action`, `success`, `entity_type`, `entity_id`, `message`, `command_text`, `source`, `created_utc`. Currently only written by `tools.execute_intent` (`backend/tools.py:412, 436`).
- **Alert Scheduling & Delivery**:
  - `backend/scheduler.py`: Single-owner `AlertScheduler` running as an asyncio task in FastAPI lifespan. Uses atomic SQL claims (`UPDATE scheduled_alerts SET status='delivered' WHERE status='pending' AND id=:id`) and broadcasts over `/ws/alerts`.

### 3. Sequential Review 2: Policy, Security, and Verification Review
- **Current Gaps in Policy and Risk**:
  - No explicit Risk Level classification (0..3) exists in runtime code. `tool_registry.py` only defines `classification: "read" | "write" | "launch"` and `kind: "intent" | "system"`.
  - There is no central `PolicyEngine` or policy evaluation step between parsing/proposal and execution.
  - The fast-path in `main.py:166` (`check_fast_path`) executes OS processes or opens URLs with no policy check and no audit event.
- **Current Gaps in Verification**:
  - `system_actions.open_website` (`system_actions.py:382–390`) calls `webbrowser.open_new_tab(matched_url)`. It ignores the return value, catches exceptions, but otherwise unconditionally reports `"Opening <name>..."` even if browser launch failed.
  - `open_app` (`system_actions.py:309–324`) invokes `os.startfile` or `subprocess.Popen` without verifying target execution.
  - `ActionReceipt` is completely bypassed for `kind == "system"`.
- **Answers to Design Questions from docs/PHASE1_PLAN.md**:
  1. *Does `tool_registry.py` validate only model proposals, or all commands? Where can an action bypass it?*
     - Currently, `tool_registry.validate_proposal` validates ONLY model proposals.
     - Direct bypasses:
       - `main.py:166` (`check_fast_path`) bypasses the registry completely.
       - `dispatcher.py` routes deterministic commands directly to `tools.execute_intent` without consulting `tool_registry.py`.
       - REST endpoints (`/api/tasks`, `/api/timers`) call `tools.execute_intent` directly.
  2. *Which operations currently have a risk level, explicit policy decision, and denial audit? Are confirmations bound to the exact target/action and expiration?*
     - None currently implement explicit Risk 0..3 levels. Refusals for unsupported destinations occur in `command_parser.unsupported_destination`, but there is no audit record for denials. Confirmations with target/action/expiration do not exist yet.
  3. *What proves `open_website`/`open_app` actually opened, and how should failures be reported?*
     - Currently nothing verifies actual desktop window opening. `webbrowser.open_new_tab` returns a boolean indicating launch initiation, but the current code discards it.
     - Hardening requirement: the launcher return value and OS process state must be verified; if launch fails or is unverified, `success=False` must be recorded in the receipt and reported truthfully.
  4. *Are retries and idempotency safe across HTTP, voice, scheduler, and provider paths?*
     - For mutating intents handled by `tools.execute_intent`, `idempotency_key` guarantees safe replay via `ActionReceipt` unique constraint.
     - For `open_website`/`open_app` and the fast path, no idempotency exists.
  5. *Which SQLite records are authoritative for a reminder after restart? Is there a single scheduler owner?*
     - `reminders` table and `scheduled_alerts` table are authoritative.
     - Single owner is enforced via atomic status transitions and `AlertScheduler` inside the FastAPI event loop.
  6. *Can `/health` truthfully distinguish a live service from optional model/voice/scheduler readiness?*
     - Currently `/health` only returns basic status and does not differentiate provider availability, voice readiness, or scheduler state.

### 4. Code to Reuse, Modify, and Defer
- **Reuse without modification**:
  - `backend/timeutil.py`: Authoritative clock, parsing, and timezone conversions.
  - `backend/db.py`: Existing declarative models and `ActionReceipt`.
  - `backend/migrations.py`: Schema versioning and non-destructive backups.
  - `backend/providers/`: Provider base contracts and adapters.
- **Modify / Reconcile**:
  - `backend/tool_registry.py`: Add risk levels (Risk 0..3), link tools to policy and verifiers, and reconcile `open_website` / `system.open_url` to produce verified receipts.
  - `backend/tools.py`: Connect execution through verification and policy.
  - `backend/system_actions.py`: Return verified outcome from OS/browser launch rather than unconditional strings.
- **Defer to Later Phases**:
  - Windows UI automation, Playwright DOM automation, camera/MediaPipe gestures, speech wake-word model training, cloud embeddings.

### 5. Implementation Order for Phase 1
- **P1-A** (Current): Grounded architecture, sequential reviews, gap assessment in log.
- **P1-B** (Next): Smallest working vertical slice for `open_website` / `system.open_url` through registry, policy, executor, verification, audit (`ActionReceipt`), and truthful response.
- **P1-C**: Generalize risk policy (Risk 0..3) and confirmation contracts across existing tools.
- **P1-D**: Persistence recovery and scheduler idempotency tests.
- **P1-E**: Service health truthful inspection and provider error isolation.

### 6. Major Security Risks and Mitigations
- **Shell Injection**: Prevented by forbidding shell execution (`shell=True` avoided for variable input; injection regex reject in `system_actions.py`).
- **Unverified Success / Receipt Hallucination**: Receipt must never record `success=True` unless the verifier confirms launch.
- **Arbitrary URL / Scheme Execution**: Must restrict URLs strictly to `http://` and `https://` (blocking `file://`, `javascript:`, `data:`, `ms-settings:` from unverified paths).
- **Silent Action Bypass**: Fast-path must be reconciled to pass through policy, verifier, and audit.

---

## 2026-09-24 — P1-B: Working vertical slice (Registry -> Policy -> Executor -> Verifier -> Audit -> Response)

### 1. Baseline and Preservation
- **Branch**: `main`, HEAD: `e7178db207e08400f8f0e167b44503160f20a3da`.
- Dirty baseline preserved: no resets, no stashes, no git commits, `.env` and local database preserved.

### 2. Architecture Decisions and Reasons
- Reconciled existing safe URL tool (`open_website` / `system.open_url`) through the unified 6-stage request pipeline:
  `Registry -> Policy Decision -> Executor -> Verifier -> ActionReceipt Audit -> Response`.
- **Why this approach**: Avoids parallel frameworks; unifies deterministic fast-path, model proposals, and tool execution under identical security and truthfulness guarantees.
- **Policy Engine (`backend/policy.py`)**: Introduced Risk 0..3 tiers. Evaluates URL navigation to permit only `http` and `https` schemes; fails closed on `file:`, `javascript:`, `data:`, `ms-settings:`, shell characters, and forbidden interpreters (`powershell`, `cmd`, `bash`, `python`).
- **Verifier (`backend/verifier.py`)**: Evaluates launcher outcome. Enforces that `success=True` is recorded only when the launcher confirms initiation. Errors, exceptions, or `False` return produce `verified=False` and a `success=False` receipt.
- **Truthful Receipts (`ActionReceipt`)**: Persists receipt records in SQLite for permitted executions, policy denials, and launch failures. Idempotency keys prevent duplicate execution.

### 3. Files Changed
- `backend/policy.py`: Created risk classification (Risk 0..3) and policy evaluation for URLs and tools.
- `backend/verifier.py`: Created verification result types and URL launch verification logic.
- `backend/system_actions.py`: Added `execute_open_url`, reconciled `open_website` and `check_fast_path` to pass through policy, verifier, and audit. Added scheme detection for fast-path.
- `backend/tool_registry.py`: Added `risk_level` to `_entry`, registered `system.open_url`, updated `open_website` and `execute_proposal`.
- `backend/tools.py`: Added `handle_open_website` for `open_website` and `system.open_url` in `HANDLERS`.
- `backend/main.py`: Updated `/chat` fast-path to pass `db`, `clock`, `source`, `idempotency_key` and return receipt.
- `backend/tests/test_p1_b_vertical_slice.py`: Created 18 unit/integration tests.
- `docs/IMPLEMENTATION_LOG.md`: Documented P1-A assessment and P1-B implementation.

### 4. Verification and Exact Results
- **P1-B Suite**: `python -m pytest tests/test_p1_b_vertical_slice.py` -> 18 passed in 1.49s.
- **Full Backend Suite**: `python -m pytest` -> 309 passed, 1 warning (Starlette testclient deprecation) in 18.69s.
- **Frontend Suite**: `npm test --prefix frontend` -> 19 passed in 119ms.
- **Formatting & Secrets Check**: `git diff --check` passed with 0 errors. Diff inspected; no secrets or unrelated files modified.

### 5. Real vs Mocked vs Unverified Behavior
- **Automated & Mocked**:
  - Browser launches in test suites are mocked using injectable `browser_launcher` or `monkeypatch` over `webbrowser.open_new_tab` to ensure test hermeticity and prevent uncontrolled desktop window opening.
  - Temporary SQLite databases (`test.db`) and synthetic clocks (`FakeClock`) used for all test suites.
- **Real Backend Execution**:
  - Real SQLite persistence and transactions; real FastAPI request lifecycle, schema validation, and error handling.
- **Unverified Desktop Behavior**:
  - Real OS default browser launching on Windows desktop, window focus switching, and electron notification toasts remain desktop-only verification checks.

### 6. Remaining Risks and Blockers
- `open_app` remains to be reconciled with verification and receipts (planned for P1-C).
- General confirmation handshake for Risk 2 and Risk 3 operations is not yet implemented (planned for P1-C).
- No blockers: repository remains in a runnable, fully passing state.

### 7. Next Step
- **P1-B Corrections** (Completed below).
- **P1-C**: Implement consistent risk policy and confirmation contract across remaining tools (`open_app`, tasks, timers, workspaces), including scoped approvals and fail-closed handling for untrusted proposals.

---

## 2026-09-24 — P1-B Correction Milestone: Source-review findings remediation

### 1. Baseline and Preservation
- **Branch**: `main`, HEAD: `e7178db207e08400f8f0e167b44503160f20a3da`.
- Dirty baseline preserved: no resets, no stashes, no git commits, `.env` and local database preserved.

### 2. Findings Reproduction, Root Causes, and Remediation
1. **Finding 1 — Model Lane Idempotency for URL Actions (`backend/model_lane.py`)**:
   - *Reproduction*: Previously, `model_lane.py` checked `if entry["kind"] == "intent" and entry["produces_receipt"]:`. Because `open_website` was classified with `kind == "system"`, `action_key` was `None`, leaving model-assisted URL retries without an action key.
   - *Fix*: Changed condition to `if entry.get("produces_receipt"):`. URL actions (`open_website`, `system.open_url`) declare `produces_receipt=True`, so they receive `action_key = f"{base}:{validated['tool']}"`. Read-only tools (`produces_receipt=False`) receive `None`.
   - *Evidence*: `test_model_lane_open_website_idempotency_and_replay` verifies that replaying a model turn with the same client key returns `replayed=True`, does not invoke the launcher again, and preserves 1 receipt. `test_model_lane_read_only_tool_has_no_action_key_and_no_receipt` verifies read-only tools create no receipt.

2. **Finding 2 — Tool Registry Production Bypass Removal (`backend/tool_registry.py`)**:
   - *Reproduction*: `execute_proposal` previously inspected `hasattr(orig_open, "_is_reconciled")` with an `else:` branch that invoked monkeypatched `open_website` and derived `opened` from `"Opening "` prefix, returning `receipt: None` without policy, verifier, or audit.
   - *Fix*: Completely removed the `_is_reconciled` check and the `else:` bypass. Routed `open_website` and `system.open_url` through `tools.execute_intent`. Introduced `get_browser_launcher()`, `set_browser_launcher()`, and `reset_browser_launcher()` at the executor boundary in `backend/system_actions.py`. Adapted old tests in `test_model_lane.py` to inject the launcher at `system_actions.get_browser_launcher` and assert `db.query(ActionReceipt).count() == 1`.
   - *Evidence*: `test_tool_registry_proposal_enforces_policy_and_produces_receipt` confirms full pipeline execution without bypass.

3. **Finding 3 — Direct Executor Route Single Receipt (`backend/tools.py`)**:
   - *Reproduction*: `handle_open_website` previously called `system_actions.execute_open_url(db=db)`, committing a receipt in `execute_open_url`, followed by `execute_intent` committing a second receipt. This produced duplicate receipts and caused UNIQUE constraint violations on replay.
   - *Fix*: `handle_open_website` now calls `system_actions.execute_open_url(..., db=None, write_receipt=False)`. If execution fails (policy denial, unverified launch, invalid target), `handle_open_website` raises `ToolError(res["message"])`, allowing `tools.execute_intent` to be the sole authoritative writer of exactly ONE `ActionReceipt` (`success=False` on failure, `success=True` on launch acceptance). Also updated `execute_intent` capability boundary check to allow legitimate open requests (`is_open_request`) for external destinations.
   - *Evidence*: `test_direct_executor_single_receipt_on_success`, `test_direct_executor_single_receipt_on_policy_denial`, `test_direct_executor_single_receipt_on_launcher_failure`, and `test_direct_executor_replay_preserves_single_receipt` confirm exactly 1 receipt in all cases.

4. **Finding 4 — Truthful Status and Desktop Observation Evidence (`backend/verifier.py`)**:
   - *Boundary Definition*: `webbrowser.open_new_tab()` returning `True` only proves OS process launch acceptance. `verifier.verify_url_launch` returns `status="launch_accepted"`, `verified_initiation=True`, `desktop_verified=False`, with `evidence["desktop_observation"] = "unobserved"` and explicit limitation notes. The user-facing response retains `"Opening {display_name}..."` with `opened=True` to maintain full compatibility with Electron's window ducking.
   - *Evidence*: `test_truthful_verifier_status_and_evidence` confirms truthful status and unobserved desktop evidence for success, refusal, and exceptions.

### 3. Changed Files
- `backend/system_actions.py`: Added `get_browser_launcher`, `set_browser_launcher`, `reset_browser_launcher`. Added `write_receipt=True` parameter to `execute_open_url`. Added `"google calendar"` and `"calendar"` to `SITE_ALIASES`. Removed `_is_reconciled` flag.
- `backend/tools.py`: Updated `handle_open_website` to pass `db=None, write_receipt=False`. Made `execute_intent` sole authoritative receipt writer for both success and failure. Updated capability boundary check for open requests.
- `backend/tool_registry.py`: Removed bypass in `execute_proposal`; routed `open_website` and `system.open_url` through `tools.execute_intent`.
- `backend/model_lane.py`: Verified `produces_receipt` check allocates `action_key` to receipt-producing tools (including URL actions) while keeping read-only tools key-free.
- `backend/tests/test_model_lane.py`: Adapted `test_open_website_via_registry` and `test_open_request_for_the_same_destination_still_launches` to inject launcher at executor boundary and assert authoritative receipts.
- `backend/tests/test_p1_b_vertical_slice.py`: Added 8 focused regression tests covering findings 1 through 4.
- `docs/IMPLEMENTATION_LOG.md`: Updated with finding remediation and exact test verification evidence.

### 4. Verification and Exact Results
- **P1-B Vertical Slice Suite**: `python -m pytest tests/test_p1_b_vertical_slice.py` -> 26 passed, 1 warning in 1.45s.
- **Model Lane Suite**: `python -m pytest tests/test_model_lane.py` -> 70 passed, 1 warning in 4.00s.
- **Full Backend Suite**: `python -m pytest` -> 317 passed, 1 warning (Starlette testclient deprecation) in 19.01s on Python 3.14.3.
- **Frontend Suite**: `npm test --prefix frontend` -> 19 passed in 121ms.
- **Formatting & Secrets Check**: `git diff --check` -> 0 errors.

### 5. Real vs Mocked Behavior
- **Hermetic Automated Tests**: OS browser launching is mocked via `system_actions.set_browser_launcher` or `browser_launcher` argument; temporary SQLite databases and synthetic clocks (`FakeClock`) ensure hermetic isolation.
- **Real Backend Execution**: Real SQLite database engine, real transaction lifecycle, real policy engine validation, and real verifier status reporting.
- **Unverified Desktop State**: Without Windows desktop UI automation (Phase 2), real browser window existence, focus switching, and rendered tab DOM remain unobserved and are truthfully declared as `desktop_verified=False` (`desktop_observation="unobserved"`).

### 6. Remaining Risks and Blockers
- `open_app` remains a system launch tool with unverified process execution (scheduled for P1-C).
- General confirmation handshake for Risk 2 and Risk 3 operations is scheduled for P1-C.
- No blockers: all 317 backend tests and 19 frontend tests pass.

### 7. Next Step
- **P1-C**: Implement consistent risk policy and confirmation contract across remaining tools (`open_app`, tasks, timers, workspaces), including scoped approvals and fail-closed handling for untrusted proposals.

---

## 2026-09-29 — P1-C1: safe app-launch initiation

- **Baseline:** `main` at `e7178db207e08400f8f0e167b44503160f20a3da`, ahead of `origin/main` by three commits. All pre-existing tracked modifications and untracked modules were preserved. `.env`, personal SQLite data, and builds were untouched. One read-only subagent reviewed app paths and tests; one agent edited.
- **Behavior:** `open_app` now passes Risk 1 name policy, registered catalog resolution with ambiguous fuzzy matches refused, a launcher that uses `os.startfile` for Windows file targets or `subprocess.Popen(argv, shell=False)`, initiation verification, and one receipt. Legacy `start ...` commands and shell/interpreter launch targets fail closed. The fast path and model proposal route both use this execution path. A fake app launcher is injectable for tests.
- **Replay:** Receipt schema version 5 adds nullable `target_key`, a hash of the requested target/payload. Replays check both action and target; mismatches return a conflict without execution. A v4 database migration backs up the database before adding the column and preserves old receipts. Old receipts without a target hash remain visible but cannot authorize a target-specific replay.
- **Files:** `backend/policy.py`, `verifier.py`, `system_actions.py`, `tool_registry.py`, `tools.py`, `dispatcher.py`, `db.py`, `migrations.py`, new `idempotency.py` and `errors.py`, new `tests/test_p1_c1_app_launch.py`, and updated `tests/test_model_lane.py` / `tests/test_migrations.py`.
- **Checks:** Focused `python3.14.exe -m pytest tests/test_p1_c1_app_launch.py tests/test_p1_b_vertical_slice.py tests/test_model_lane.py -q -p no:cacheprovider` passed 113 tests before the final generic payload replay extension. Final P1-C1 suite passed **20 tests**. Final full backend command `python3.14.exe -m pytest -q -p no:cacheprovider --basetemp <workspace test temp>` passed **338 tests, 1 Starlette deprecation warning in 29.06 s**. The first full attempt failed only because the system temp directory was inaccessible; rerunning with a workspace-owned temp directory exposed four migration tests expecting schema version 4, which were updated. `git diff --check` passed. The workspace-owned test temp directory was removed after the run.
- **Evidence boundary:** Tests used synthetic catalog entries, fake launchers, and temporary SQLite databases. No real Windows app or desktop window was launched or observed. `launch_accepted` means only that the launcher accepted the request; `desktop_verified` stays false.
- **Remaining risks:** Two truly concurrent requests with the same key can both reach the OS launcher before either receipt commits; serial replay is covered, concurrent launch claims are not. Windows `.lnk` shortcuts are registered OS targets whose embedded command is not inspected here. Risk 2/3 confirmation UI/API remains out of scope.
- **Next:** P1-C2 should first inventory current tools that actually require Risk 2/3 approval, then design and test a target-bound, expiring confirmation contract. Do not infer implementation from this note; inspect current source.

---

## 2026-09-29 — P1-C2: Enforce policy consistently across existing tools

- **Baseline:** `main` at `e7178db207e08400f8f0e167b44503160f20a3da`, ahead of `origin/main` by 3 commits. All pre-existing tracked modifications and untracked modules were preserved. `.env`, personal SQLite database (`jarvis.db`), and builds were untouched. Sequential read-only mapping of entry paths and source review was conducted; one agent edited.
- **Tool Inventory & Risk Classification:**
  - Total active tools: 26 tools.
  - **Risk 0 (Read-Only, 8 tools):** `list_tasks`, `get_ai_radar_digest`, `list_workspaces`, `resume_workspace`, `build_session_draft`, `get_today`, `list_coursework`, `suggest_study`. Strictly read-only, mutate zero tables, produce zero `ActionReceipt` rows.
  - **Risk 1 (Bounded Safe Mutating/Launch, 18 tools):**
    - Mutating productivity (15 tools): `create_task`, `set_task_completed`, `start_timer`, `cancel_timer`, `create_reminder`, `snooze_reminder`, `start_focus_session`, `end_focus_session`, `register_workspace`, `update_workspace`, `add_session_note`, `link_task_to_workspace`, `add_coursework`, `complete_coursework`, `refresh_ai_radar`.
    - Launch actions (3 tools): `open_website`, `system.open_url`, `open_app`.
  - **Risk 2 / Risk 3 (0 tools):** No current assistant tool genuinely requires Risk 2 or Risk 3 confirmation today. Unknown or unregistered actions fail closed immediately as Risk 3 Critical refusals.
  - **Future Confirmation Contract (Documented):** When future phases introduce Risk 2 (file modification) or Risk 3 (deletions, external messaging, credentials, financial actions) tools, proposals must yield an expiring token cryptographically bound to `(action, target_hash, expires_utc)`. Execution without a valid confirmation token fails closed.
- **Authoritative Policy Enforcement:**
  - Centralized single authoritative execution choke point in `backend/tools.py:execute_intent`, invoked by deterministic commands (`dispatcher.py`), model proposals (`tool_registry.execute_proposal`), and REST endpoints (`main.py`).
  - Fast-path launches in `backend/system_actions.py` (`execute_open_url`, `execute_open_app`) route through `policy.evaluate_policy`.
  - Unknown tools, malformed arguments, and policy denials fail closed before handler execution with zero entity side effects.
  - Read-only tools never write action receipts on success or denial; mutating tools record exactly one failure `ActionReceipt` on denial for auditability.
- **Files Changed:**
  - `backend/policy.py`: Added `refresh_ai_radar` to Risk 1 mutating tools; defined explicit tool classification sets (`RISK_0_READ_ONLY_TOOLS`, `RISK_1_MUTATING_TOOLS`, `RISK_1_LAUNCH_TOOLS`, `RISK_2_MODIFICATION_TOOLS`, `RISK_3_CRITICAL_TOOLS`); implemented `evaluate_policy` and `get_tool_risk_level`.
  - `backend/tools.py`: Imported `policy`; updated `execute_intent` to run authoritative policy evaluation before handler execution; attached `policy_decision` to returned dict.
  - `backend/system_actions.py`: Routed `execute_open_app` through central `policy.evaluate_policy("open_app", {"name": name})`.
  - `backend/tool_registry.py`: Updated `_entry` default risk level to match classification (0 for read, 1 for write/launch); updated `validate_proposal` to evaluate policy for unknown tools.
  - `backend/tests/test_p1_c2_policy_enforcement.py`: Created 15 focused tests covering inventory, route equivalence, zero-side-effect denials, read-only invariance, and confirmation refusal.
- **Verification & Exact Results:**
  - **P1-C2 Suite**: `python -m pytest tests/test_p1_c2_policy_enforcement.py` -> **15 passed**, 1 warning in 1.37s.
  - **Targeted Suite (173 tests)**: `python -m pytest tests/test_p1_c2_policy_enforcement.py tests/test_p1_c1_app_launch.py tests/test_p1_b_vertical_slice.py tests/test_model_lane.py tests/test_dispatcher_tools.py tests/test_api.py` -> **173 passed**, 1 warning in 11.64s.
  - **Full Backend Suite (353 tests)**: `python -m pytest` -> **353 passed**, 1 Starlette deprecation warning in 19.36s on Python 3.14.3.
  - **Frontend Suite (19 tests)**: `npm test --prefix frontend` -> **19 passed** in 153ms.
  - **Formatting & Secrets Check**: `git diff --check` -> 0 errors.
- **Evidence Boundary:**
  - Tests used temporary SQLite databases (`test.db`), synthetic clocks (`FakeClock`), mocked launchers, and in-memory mock providers. No real desktop apps were launched, no browser windows opened, and no personal data touched.
- **Remaining Risks:**
  - Real Windows desktop UI rendering and window focus remain unobserved and declared as `desktop_verified=False`.
  - Alert scheduler stale entity reconciliation (identified during Phase 1 audit) remains to be hardened in P1-D.
- **Next Step:**
  - **P1-D1**: Scheduler restart recovery, stale alert entity state, and serial idempotency (Completed below).

---

## 2026-09-29 — P1-D1: Scheduler restart recovery, stale alert entity state, and serial idempotency

- **Baseline:** `main` at `e7178db207e08400f8f0e167b44503160f20a3da`, ahead of `origin/main` by 3 commits. All pre-existing tracked modifications and untracked modules were preserved. `.env`, personal SQLite database (`jarvis.db`), and builds were untouched. One editing agent executed sequential analysis, implementation, and hermetic testing.
- **Problem & Root Cause:**
  - Previously, `backend/scheduler.py` marked a stale `ScheduledAlert` (older than grace window, default 30 min) as `status="missed"` and recorded a failure delivery receipt, but failed to update the underlying `Timer` or `Reminder` row.
  - Consequently, `Timer` remained `status="active"` (polluting active timers and `hub_state`), and `Reminder` remained `status="pending"` (polluting "Next Reminder" and countdowns with negative/overdue values).
  - Additionally, if an alert delivery receipt was written, it lacked an `idempotency_key`, leaving delivery receipts reliant solely on alert claim locking without a DB-level uniqueness constraint.
  - Initial orphan healing relied on `not pending_alert`, which was overly broad: it would also heal entities whose alerts were cancelled, delivered, or nonexistent.
- **Terminal Status Semantics & Implementation:**
  - **Timer Lifecycle**: Extended to `active -> fired | cancelled | missed`. When its scheduled alert ages out beyond the grace window, `timer.status` transitions to `"missed"` and `timer.ended_at` is set to `now`. It is excluded from active timer listings and hub cards.
  - **Reminder Lifecycle**: Extended to `pending -> fired | cancelled | missed (snooze revives to pending)`. When its scheduled alert ages out beyond grace, `reminder.status` transitions to `"missed"`, and `reminder.fired_at` truthfully remains `None` (truthful semantics: never claiming desktop notification when an alert only aged out).
  - **Exact Due-Time Linkage for Orphan Healing**: In `scheduler.tick()`, active timers and pending reminders older than grace are reconciled to `"missed"` ONLY when an exact matching `ScheduledAlert` with `status="missed"` exists for their current due time (`abs(alert.due_utc - entity.due_utc) < 1s`). Entities with cancelled alerts, delivered alerts, or no alert at all remain unaltered.
  - **Snooze Support & Snoozed Reminder Protection**: Updated `tools.py:snooze_reminder` so users can snooze a missed reminder both explicitly (by ID) and implicitly. In both `_apply_stale_entity_state` and orphan healing, exact due-time linkage ensures an old missed alert from a prior due time cannot mutate a reminder that has been snoozed to a later due time.
  - **Cancellation & Snooze Guards**: `scheduler._apply_entity_state` and `_apply_stale_entity_state` verify `abs(alert.due_utc - entity.due_utc) < 1s`. If a reminder was snoozed into the future or a timer cancelled, older alerts cannot alter the active/pending entity.
  - **Task & Focus Session Invariance**: Task deadline alerts and focus session end alerts transition to `missed`/`delivered` without altering `Task.completed` (`False`) or `FocusSession.status` (`active`).
  - **Receipt Idempotency**: Alert delivery receipts carry `idempotency_key=f"alert_delivery:{alert.id}"`, guaranteeing at-most-once delivery receipt creation across process restarts or overlapping ticks.
- **Files Changed:**
  - `backend/db.py`: Updated `Timer` and `Reminder` class docstrings to document `missed` status.
  - `backend/scheduler.py`: Implemented `_apply_stale_entity_state`, exact due-time linkage in `_apply_entity_state` and `_apply_stale_entity_state`, exact matching missed alert verification for orphan reconciliation in `tick()`, and `idempotency_key=f"alert_delivery:{alert.id}"` on delivery receipts.
  - `backend/tools.py`: Updated `snooze_reminder` to support implicit and explicit snoozing of missed reminders.
  - `backend/tests/test_p1_d1_scheduler_recovery.py`: Created 18 focused unit/integration tests covering due alert with/without listener, expiry beyond grace, restart recovery, repeated tick idempotency, cancellation/snooze guards, orphan healing boundaries (matching missed heals, cancelled/delivered/missing alerts do not heal, snoozed reminders protected), genuine SQLite database reopen with fresh engine/sessionmaker, and task/focus invariance.
- **Verification & Exact Results:**
  - **P1-D1 Focused Suite**: `python -m pytest tests/test_p1_d1_scheduler_recovery.py` -> **18 passed** in 1.76s.
  - **Scheduler Combined Suite**: `python -m pytest tests/test_p1_d1_scheduler_recovery.py tests/test_scheduler.py` -> **27 passed** in 1.70s.
  - **Targeted Integration Suite (124 tests)**: `python -m pytest tests/test_p1_d1_scheduler_recovery.py tests/test_scheduler.py tests/test_p1_c2_policy_enforcement.py tests/test_p1_c1_app_launch.py tests/test_p1_b_vertical_slice.py tests/test_dispatcher_tools.py tests/test_api.py` -> **124 passed**, 1 warning in 10.89s.
  - **Full Backend Suite (371 tests)**: `python -m pytest` -> **371 passed**, 1 Starlette deprecation warning in 20.81s on Python 3.14.3.
  - **Frontend Suite (19 tests)**: `npm test --prefix frontend` -> **19 passed** in 126ms.
  - **Formatting & Secrets Check**: `git diff --check` -> 0 errors.
- **Evidence Boundary:**
  - **Automated & Mocked**: Tests used temporary SQLite databases (`test.db`), synthetic clocks (`FakeClock`), and fake WebSocket listeners (`_FakeWS`). Zero real OS notifications or electron toasts were triggered.
  - **Real Backend Execution**: Real SQLite queries, atomic SQL row updates, transactions, genuine multi-engine database reopen, and receipt idempotency verification.
  - **Unverified Desktop Behavior**: Real Windows toast notifications, notification center history, and Electron window focus remains unobserved in hermetic test execution.
- **Remaining Risks:**
  - Multiple concurrent processes attempting atomic claims on SQLite rely on write lock acquisition; serial idempotency is fully covered.
  - Real OS desktop notification banner display remains a desktop-only verification check.
- **Next Step:**
  - **P1-D2**: Additive schema migration verification (testing v1->v5 migration path from temporary older SQLite schemas, database backup creation, and recovery under partial/corrupted conditions).

---

## 2026-10-01 — Repository cleanup, roadmap consolidation, and test suite disambiguation

### 1. Baseline and Protection
- **Branch**: `main`, HEAD: `f5f4bcd`.
- **User Data Protection**: `.env`, `backend/jarvis.db`, `backend/jarvis.db.bak-*`, credentials, installed models, and personal files were strictly untouched. No resets, clean, stash, or git commits/pushes were performed.
- **Editing Agent**: Single editing agent; no concurrent file modifications.

### 2. Inventory and Cleanup Decisions
- **Duplicate Document Removal**:
  - `BUILDPLAN.md` in repository root was an exact byte-for-byte duplicate of `docs/history/2026-qoder/JARVIS_ORIGINAL_BUILDPLAN.md` left behind from the initial repository commit. Removed using `git rm` (safe deletion: preserved identically in `docs/history/2026-qoder/`).
- **Generated Boilerplate Removal**:
  - `frontend/README.md` contained only default Vite template scaffolding text with zero project-specific content. Removed using `git rm`.
- **Historical Test Suite Disambiguation**:
  - Renamed 3 test files in `backend/tests/` using `git mv` to resolve naming collisions with the current AgentOS Phase 1 roadmap:
    - `backend/tests/test_p1_stage1.py` -> `backend/tests/test_duration_and_completion.py` (describes word-number duration parsing and deterministic task completion routing).
    - `backend/tests/test_p2_workspaces.py` -> `backend/tests/test_workspaces.py` (directly matches `backend/workspaces.py`).
    - `backend/tests/test_p3_academic.py` -> `backend/tests/test_academic.py` (describes coursework and study suggestions).
  - Updated docstring headers in all three files to remove stale stage/phase prefixes.
- **Historical Antigravity Prompts Archival**:
  - Created `docs/history/2026-antigravity/` and moved completed milestone prompts via `git mv`:
    - `docs/ANTIGRAVITY_P1B_REVIEW_FIX.md` -> `docs/history/2026-antigravity/ANTIGRAVITY_P1B_REVIEW_FIX.md`
    - `docs/ANTIGRAVITY_P1C1_APP_LAUNCH.md` -> `docs/history/2026-antigravity/ANTIGRAVITY_P1C1_APP_LAUNCH.md`
    - `docs/ANTIGRAVITY_P1D1_SCHEDULER_RECOVERY.md` -> `docs/history/2026-antigravity/ANTIGRAVITY_P1D1_SCHEDULER_RECOVERY.md`
    - `docs/ANTIGRAVITY_P1D1_CORRECTION.md` -> `docs/history/2026-antigravity/ANTIGRAVITY_P1D1_CORRECTION.md`
  - Retained `docs/ANTIGRAVITY_START.md` (initial run guide) and `docs/ANTIGRAVITY_P1D2_MIGRATION_VERIFICATION.md` (active prompt for the next uncompleted milestone).
- **Authoritative Product Reality Report**:
  - Tracked `docs/VEGA_PROJECT_REALITY_REPORT_2026-10-01.md` in Git (`git add`), making it the single authoritative product status report.
- **Roadmap & Documentation Consolidation**:
  - Updated `README.md` to prominently link the product reality report, active plan (`docs/PHASE1_PLAN.md`), and implementation log.
  - Added an explicit "Roadmap & Architecture Clarity" section distinguishing historical Qoder-era P1/P2/P3 feature prototypes (chat, workspaces, coursework) from active AgentOS Phase 1 execution hardening (policy, verification, receipts, scheduler recovery).
  - Corrected Whisper model defaults in `README.md` to `small.en` (configurable via `WHISPER_MODEL`) and corrected the frontend test runner description to Node test runner (`node --test`).
  - Added a clarifying header to `docs/PHASE1_PLAN.md` referencing the reality report and defining the boundary of AgentOS Phase 1.

### 3. Verification and Validation Results
- **Backend Renamed Suites**: `python -m pytest tests/test_duration_and_completion.py tests/test_workspaces.py tests/test_academic.py` -> **61 passed**, 1 warning in 4.55s.
- **Full Backend Suite**: `python -m pytest` -> **371 passed**, 1 Starlette deprecation warning in 27.88s on Python 3.14.3.
- **Frontend Suite**: `npm test --prefix frontend` -> **19 passed** in 230ms (`node --test`).
- **Frontend Linter**: `npm run lint --prefix frontend` -> **0 errors**, 17 warnings (Oxlint).
- **Frontend Production Build**: `npm run build --prefix frontend` -> Vite build successful (all bundles and Electron main/preload compiled cleanly).
- **Whitespace & Formatting**: `git diff --check` -> **0 errors**.

### 4. Deliberately Retained Items
- `AGENTS.md`: Retained intact as required by system rules.
- `backend/jarvis-backend.spec`: Retained for PyInstaller desktop packaging (`datas` is configured to prevent bundling personal data).
- `docs/history/2026-qoder/`: 15 historical reference documents retained for historical provenance.
- `docs/ANTIGRAVITY_START.md` & `docs/ANTIGRAVITY_P1D2_MIGRATION_VERIFICATION.md`: Retained in `docs/` to guide execution of the next active milestone.

### 5. Remaining Uncertainty
- Real Windows desktop toast notifications, microphone hardware latency, and electron-builder NSIS distribution remain desktop verification checks.

### 6. Next Step
- Resume AgentOS Phase 1 with **P1-D2**: Additive schema migration verification (testing v1->v5 migration path from temporary older SQLite schemas, database backup creation, and recovery under partial/corrupted conditions) as defined in `docs/ANTIGRAVITY_P1D2_MIGRATION_VERIFICATION.md` and `docs/PHASE1_PLAN.md`.

## 2026-10-01 — Current Antigravity guide after cleanup

- **Scope:** Documentation handoff only. Current `main` HEAD is `f5f4bcd`; existing staged cleanup changes and unstaged edits were preserved. No application code, personal database, credentials, or build output was changed.
- **Source findings:** P1-D2 remains uncompleted in the log and reality report. `migrations.py` still copies database files with `shutil.copy2`, uses second-resolution backup names, and stamps versions before the final `create_all`. These are investigation targets for the implementation run, not new runtime results.
- **Guide:** Expanded `ANTIGRAVITY_P1D2_MIGRATION_VERIFICATION.md` with scope, a paste-ready prompt, supported fixture matrix, WAL backup checks, backup failure handling, interrupted upgrade checks, acceptance criteria, and handoff format. One editing agent and bounded read-only reviewers are specified.
- **Documentation corrections:** Replaced the active start guide's P1-A/P1-B instructions with current P1-D2 start/resume instructions, preserving the original in `history/2026-antigravity/ANTIGRAVITY_START_ORIGINAL_2026-10-01.md`. Added README links and corrected the reality report's stale README statement. A read-only reviewer independently checked roadmap and documentation consistency.
- **Evidence:** Documentation and migration source were inspected. Application test suites were not rerun for this documentation-only change; the cleanup report's 371 backend / 19 frontend results remain agent-reported.
- **Checks:** `git diff --check` and `git diff --cached --check` passed. A PowerShell check resolved every local Markdown link in the two active Antigravity guides.
- **Next:** Antigravity implements and verifies P1-D2, then stops. Review the resulting code/report before preparing P1-E service/provider readiness.

---

## 2026-10-01 — P1-D2: SQLite additive migration and backup reliability

### 1. Baseline and Preservation
- **Branch**: `main`, HEAD: `f5f4bcd0a86e4745db7243873811421b10bbd77e`.
- **Preserved state**:
  - Staged cleanup items (deleted `BUILDPLAN.md`, deleted `frontend/README.md`, renamed test files `test_academic.py`, `test_duration_and_completion.py`, `test_workspaces.py`, tracked `docs/VEGA_PROJECT_REALITY_REPORT_2026-10-01.md`, and archived prompts in `docs/history/2026-antigravity/`) were strictly preserved.
  - `.env`, personal database `backend/jarvis.db`, its `.bak-*` files, WAL files, credentials, and build artifacts were strictly untouched. No resets, clean, stash, stage, or git commits/pushes were performed.
- **Editing Agent**: Single editing agent; no concurrent file modifications.
- **Subagent Limitation Note**: Autonomous coding subagents are unavailable in this environment (declarations only provide `browser_subagent` for web interaction). Sequential read-only reviews were conducted:
  1. *Review 1 (Failure modes)*: Inspected `backend/migrations.py`, `backend/db.py`, and `backend/main.py`. Found that `shutil.copy2` loses uncheckpointed WAL commits, backup filename allocation can collide in the same second, `_sync_indexes` failed on legacy tables lacking newer columns, and `schema_version` was stamped before `create_all`.
  2. *Review 2 (Historical fixtures & coverage)*: Traced historical migration contracts (legacy unversioned v0, v1, v2, v3, v4, v5) from migration history, ensuring fixtures match true historical schemas rather than current schemas with altered stamps.

### 2. Migration and Backup Hardening Decisions
- **Consistent Backup via SQLite Online Backup API**:
  - Replaced `shutil.copy2` with `sqlite3.Connection.backup()`. In WAL mode, `shutil.copy2` copies only the main database file, missing uncheckpointed pages residing in the `-wal` file. `sqlite3.Connection.backup()` cleanly captures committed WAL data even with open connections.
  - Backup creation is executed *before* acquiring migration write locks, preventing self-deadlocks.
  - Backups are immediately validated via `PRAGMA integrity_check` on a separate connection.
  - Timestamp collisions within the same second are resolved by appending collision-safe monotonic counters (`.bak-{timestamp}_{counter}`).
- **Pre-flight Safety and Corruption Checks**:
  - Added pre-migration `PRAGMA quick_check(1)` check. If a database file is corrupted, migration aborts immediately with `MigrationError` before any writes occur.
  - Added `_read_and_validate_version()`: validates `schema_version` table structure and fails closed if the version table is malformed or if `version > CURRENT_SCHEMA_VERSION` (5).
  - Added `_check_schema_consistency()`: detects and refuses unexplained schema inconsistencies (where `schema_version` claims a version ahead of actual columns present), while safely resuming known additive partial upgrades (e.g. columns already added prior to an interrupted migration).
- **Atomic Transaction Ordering**:
  - Table creation (`Base.metadata.create_all(bind=conn)`) and final schema validation (`_validate_final_schema()`) now execute inside the atomic `engine.begin()` transaction *before* updating `schema_version`. If table creation fails, the transaction rolls back, leaving the previous version stamp intact and the pre-migration backup unharmed.
- **Robust Index Synchronization**:
  - Hardened `_sync_indexes()` to inspect existing table columns and skip indexes whose columns do not yet exist, preventing `no such column` errors on older partial schemas.
  - Added explicit `UNIQUE INDEX` creation when `idx.unique` is set on the model.

### 3. Files Changed
- `backend/migrations.py`: Implemented `MigrationError`, `sqlite3.Connection.backup()` with integrity validation and collision-safe naming, pre-migration corruption checks, schema consistency verification, safe index creation, and atomic transaction ordering.
- `backend/tests/test_p1_d2_migration_verification.py`: New comprehensive test suite with 17 tests covering:
  - Supported starting points: legacy unversioned v0, v1, v2, v3, v4, v5 (no-op), fresh DB.
  - Sentinel rows (tasks, notes, receipts) preservation across migrations.
  - WAL mode backup consistency with uncheckpointed commits and active connection.
  - Same-second backup filename collision avoidance.
  - Backup failure aborting without mutation.
  - Corrupt database file rejection without mutation.
  - Unsupported newer version (v6) rejection.
  - Malformed `schema_version` rejection.
  - Version stamp ahead of columns rejection (unexplained inconsistency).
  - Known additive partial upgrade safe resumption.
  - Mid-migration failure transaction rollback and backup preservation.
  - Index uniqueness synchronization.
- `docs/IMPLEMENTATION_LOG.md`: Documented P1-D2 implementation, decisions, test evidence, boundaries, and next step.

### 4. Verification and Exact Results
- **Focused Migration Suites**:
  - `python -m pytest tests/test_migrations.py` -> **6 passed** in 3.01s.
  - `python -m pytest tests/test_p1_d2_migration_verification.py` -> **17 passed** in 5.21s.
  - Combined migration tests (23 tests): `python -m pytest tests/test_migrations.py tests/test_p1_d2_migration_verification.py` -> **23 passed** in 7.07s.
- **Full Backend Suite (388 tests)**:
  - `python -m pytest` -> **388 passed**, 1 Starlette deprecation warning in 47.07s on Python 3.14.3.
- **Formatting & Whitespace Check**:
  - `git diff --check` -> **0 errors**.

### 5. Evidence Boundary
- **Hermetic Automated Tests**: All verification ran against synthetic, temporary file-backed SQLite databases (`tmp_path`) and synthetic fixtures.
- **Untested Personal Database**: The personal database (`backend/jarvis.db`, `.bak-*`, WAL files, `.env`) remains completely untouched and untested.

### 6. Remaining Risks and Limitations
- **External Multi-process Locks**: SQLite acquires a file-level write lock during backup and migration. If an external process locks the database exclusively for longer than the 10-second timeout, `MigrationError` will be raised safely without data loss.
- **Disk Space**: While backup creation fails closed if disk space is exhausted before schema mutation, running migrations on a nearly full disk could fail during the SQLite vacuum/checkpoint stage.

### 7. Next Step
- **P1-E**: Service and model provider readiness and fault isolation (truthful `/health` reporting of provider, voice, and scheduler availability; ensuring model provider outages do not degrade deterministic local commands). Do not start P1-E in this run.

