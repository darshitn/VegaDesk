# Next run: prepare an isolated Windows acceptance build

Prepared 3 October 2026. Use Gemini 3.8 Flash with High reasoning for profile
isolation, then normal reasoning for the checklist and routine edits.

## Current evidence

BETA-UI layout, tabs, task filters and accessibility styles are present. Codex
independently ran `npm test --prefix frontend`: **45 passed**; lint completed
with **0 errors and 18 warnings**. Those tests exercise helpers/state with mocks,
not real layout, Windows scaling, microphone or OS notification rendering.
The full **415-backend** and production build results remain agent-reported.
The working tree has modified and untracked source files; preserve it.

Acceptance needs its own profile first. Current Electron settings/localStorage
use normal userData; backend startup reads backend/.env and normally binds 8000.
A JARVIS_DB_PATH override alone does not isolate these other resources.

## Paste this prompt

```text
Continue VEGA in D:\Projects\Jarvis_Dashboard\jarvis-dashboard. Read AGENTS.md, README.md, docs/POLISHED_BETA_ROADMAP.md, docs/IMPLEMENTATION_LOG.md and this guide. Recheck branch, HEAD and staged/unstaged/untracked baseline. This run is BETA-ACCEPTANCE preparation: make an isolated synthetic test profile, give the owner an exact launch command and acceptance checklist, then stop. Do not start the GUI, microphone, installer or a supervised desktop session in this preparation run.

Preserve all existing source changes, .env, personal database/backups/WAL files, normal Electron profile and previous build outputs. No reset, clean, stash, stage, commit or push. Keep one editing agent; use bounded read-only reviewers for profile/package isolation and the acceptance checklist if coding subagents exist, otherwise review sequentially. Do not call the dirty source tree clean or confuse no runtime artifacts with no untracked source.

1. Inspect frontend/electron/main.js startup, userData/settings/session/single-instance lock, backend spawning/shutdown, root/frontend package scripts, PyInstaller spec and electron-builder config, backend db.py/main.py dotenv/run.py, frontend API/WebSocket origins, onboarding, notifications and layout. Reuse existing settings/DB overrides. Avoid an architecture rewrite.
2. Implement one explicit opt-in synthetic beta profile with a dedicated root. Set Electron userData/session storage before settings reads and the single-instance lock. Route backend DB, logs and acceptance-only outputs under this profile, keeping the same profile across test restarts. Ensure dev and packaged backend launch inherit the profile correctly. Disable loading the personal backend/.env in beta mode before its values can enter configuration; do not copy or print credentials. Prevent inherited cloud credentials/settings from causing inference or network jobs. Default the beta to deterministic-only, voice/radar/camera off and no auto-launch OS settings changes. Keep normal-mode behavior intact.
3. Protect process/network ownership. Never kill an existing VEGA/backend process. Use a coherent isolated API/WebSocket port configuration, or refuse launch if required ports are occupied; do not reuse an arbitrary existing service. Give beta /health a non-secret profile/run identity and verify it before allowing renderer writes. Do not reconnect beta UI to the personal backend or vice versa. Spawn only owned processes, bound startup waits, and shut down only owned children on exit; avoid orphaned backend or global hotkey changes affecting another instance. Print safe beta-mode identity/path information so the user can verify isolation.
4. Add a deterministic idempotent seed command creating a few synthetic tasks, completed/open filter examples, a project with goal/blocker/next action, session note and coursework. Never seed the production database or schedule surprise notifications at startup. Document optional sample timer/reminder creation during the later manual session. Add a visible beta profile indicator and simple reset/reseed instructions confined to the verified beta root. Do not delete the beta profile automatically; it is needed for restart tests.
5. Audit notification claims before testing: show-notification currently returns delivered:true immediately after notification.show(), and notificationService returns status:delivered. Request acceptance is not observed Windows rendering. Make status/evidence distinguish request accepted, API shown/failed event if available, and user-observed toast. Do not claim Windows notification permission is granted merely because the preload API exists. Validate the IPC payload/sender for the beta path where needed; preserve deduplication and in-app fallback without broad permission grants. Test accepted/failure paths using fake Electron APIs only in this run.
6. Package/read-write audit: identify where dev and packaged databases, settings and logs live, and whether installing/upgrading can overwrite them or write into a protected resources directory. No installer execution or personal-data migration. If packaging can embed personal databases, .env, backups or beta fixtures, fix narrowly and test the file selection with synthetic artifacts. Use a separate acceptance output directory if producing artifacts; preserve existing dist/release outputs. Report dev-vs-unpacked-vs-installed evidence separately. Do not promise installer readiness based on Vite build success.
7. Add focused tests proving profile propagation, normal-mode compatibility, no personal dotenv loading in beta, deterministic-only default, occupied-port refusal/identity mismatch, seed/reseed idempotency and owned-child cleanup. Mock spawn/GUI/notification APIs and use temporary DB/profile fixtures; no live models or microphone. Run relevant frontend/backend tests, lint and git diff --check. Run broader suites only for concrete integration changes. If a required tool or dependency is unavailable, document it and keep the safe launcher/checklist reviewable.
8. Create docs/BETA_ACCEPTANCE_CHECKLIST.md with exact verified commands for preparation, launch, clean exit and same-profile restart. Include prerequisites and known limitations. Every test starts NOT RUN; passing unit tests must not pre-fill manual passes. Give a compact results table with expected behavior, PASS/FAIL/UNVERIFIED, environment/build type, evidence and notes. Include:
   - Beta identity and data paths; first-run without credentials; deterministic command and unfamiliar request refusal.
   - Workspace tabs/Alt+1/Alt+2; task All/Open/Done; Today/Focus/Resume; long lists and bottom controls; chat retained across theme/view/hide/restore changes; failed write retains content and stale reads recover.
   - Timer/reminder with window visible, hidden and minimized; toast click restores Workspace; reconnect/deduplication; notification suppression/failure; app asleep/offline expectations and expired alert behavior. Do not change Windows notification settings globally; owner may test suppression when ready.
   - Owner-opted-in push-to-talk/wake phrase using already installed local speech assets. No automatic download. Missing hardware/assets are UNVERIFIED with a reason, not failures claimed solved by mocks.
   - Tray/close/quit/hotkey behavior; restart persistence for tasks/projects/notes/settings/chat; owned backend shutdown.
   - Actual Windows 100/125/150% scaling where available, 1366x768 and larger/narrower windows, every theme, keyboard focus, reduced motion. Browser viewport emulation is labelled as such.
   - Measured idle/hidden CPU/RAM and startup/typed/voice result latency with method and repetitions; no invented performance thresholds.

Update docs/IMPLEMENTATION_LOG.md with changes, exact tests, output paths, launch commands, isolation proof and remaining unverified desktop checks. Correct earlier clean-tree/layout/notification overclaims through appended evidence notes. Update active README/start pointers. Stop with an acceptance-ready launcher and checklist; the owner will initiate the supervised Windows session when ready. No new product features or autonomous coding/shell tools.
```

## What comes after preparation

The owner runs the documented synthetic-profile command and records real Windows
results. Fix failures in bounded follow-up runs; keep all unobserved cases marked
UNVERIFIED. After the daily loop is dependable, use it for a week before adding
the registered-project launch routine or selected-document retrieval.
