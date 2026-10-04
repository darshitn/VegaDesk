# V.E.G.A. (AgentOS) — AI Assistant Instructions

## 1. Operating Rules for Antigravity Agents
Before making changes to this codebase, you must:
1. Inspect existing code, active branches, and unstaged changes. Never reset, stash, or overwrite uncommitted files.
2. Read `AGENTS.md`, `PROJECT.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `TASKS.md`, and `docs/IMPLEMENTATION_LOG.md`.
3. Adhere to the single implementation milestone rule: implement only the first incomplete task in `TASKS.md`.
4. Run relevant test suites and linters after every modification.
5. Update `TASKS.md` and append an entry in `docs/IMPLEMENTATION_LOG.md` before concluding.
6. Commit the completed working milestone to Git.

## 2. Safety Invariants
- **Profile Isolation:**
  - Beta development must use `VEGA_PROFILE=beta` with data root `%APPDATA%\Jarvis_Dashboard_Beta`.
  - Personal `.env` and personal database (`backend/jarvis.db`) must **never** be loaded, modified, or used as test fixtures.
  - Beta processes bind strictly to port `8005`. Port clashes must cause launch refusal rather than killing external processes.
- **Fail-Closed Policy:**
  - Mutations (`POST`, `PATCH`, `DELETE`) require a verified session handshake (`verifyBackendSession`).
  - No unrestricted shell execution tools. All model proposals must be mapped through typed schemas and `policy.py`.
- **Zero Cloud Leakage in Beta:**
  - Inherited cloud API keys (`GEMINI_API_KEY`, `OPENAI_API_KEY`) must be scrubbed from process memory on beta launch.
  - Deterministic-only execution is the default.

## 3. Test & Verification Commands
Always execute from `d:\Projects\Jarvis_Dashboard\jarvis-dashboard`:

```powershell
# 1. Frontend unit tests (Node.js native test runner)
npm test --prefix frontend

# 2. Frontend linter (oxlint)
npm run lint --prefix frontend

# 3. Backend targeted regression tests (Pytest)
python -m pytest backend/tests/test_command_parser.py backend/tests/test_beta_voice.py

# 4. Whitespace and Git diff check
git diff --check
```

## 4. Distinction Between Mocks and Real Hardware
- Automated unit tests (`node --test`, `pytest`) verify state transitions, JSON schemas, regex parsing, and concurrency counters.
- Automated tests **do not prove** physical microphone acoustics, laptop speaker acoustic coupling, Windows Action Center toast display, or physical DPI scaling.
- Never mark items in `docs/BETA_ACCEPTANCE_CHECKLIST.md` as `PASS` based on unit tests; only the human owner running a supervised desktop session can evaluate hardware rows.
