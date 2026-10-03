# VEGA AgentOS — Beta Acceptance Checklist

**Target Milestone:** BETA-ACCEPTANCE (Supervised Desktop Session)  
**Profile Identity:** Isolated Synthetic Beta Profile (`VEGA_PROFILE=beta`)  
**Network Port:** `8005` (Isolated from personal VEGA port `8000`)  
**Profile Root:** `%APPDATA%\Jarvis_Dashboard_Beta`  
**Database Path:** `%APPDATA%\Jarvis_Dashboard_Beta\db\jarvis-beta.db`  
**Execution Mode:** Deterministic Offline Only (Cloud Inference Disabled, Voice/Radar Inactive)  

---

## 1. Prerequisites & Safety Invariants

1. **Isolation Guarantee:**
   - Beta mode **never loads** personal `backend/.env`.
   - Inherited cloud keys (`GEMINI_API_KEY`, `OPENAI_API_KEY`, etc.) are scrubbed from process memory on startup.
   - Personal database (`backend/jarvis.db` and `%APPDATA%\Jarvis_Dashboard\jarvis.db`) is strictly isolated and never accessed.
2. **Process Ownership & Session Integrity:**
   - Launcher generates a fresh opaque `run_id` shared across backend, Electron, and renderer.
   - Backend binds to port `8005`. If port `8005` is occupied by another service or another profile, launch is **refused**.
   - VEGA never kills existing or foreign processes.
   - On exit, only children spawned by this specific instance are terminated via process-tree handling.
3. **No Automatic Network / Download Activity:**
   - No models, weights, or external speech assets will be downloaded.
   - Cloud inference is locked to `none`; all user prompts resolve deterministically offline at ₹0 cost.
   - Background microphone listener and AI radar research crawler are disabled in beta mode (`VEGA_DISABLE_VOICE=1`, `VEGA_DISABLE_RADAR=1`).
4. **Clean Baseline:**
   - All tests in this checklist start as **NOT RUN**. Passing unit tests must **never** pre-fill manual passes.

---

## 2. Exact Verified Execution Commands

### A. Environment Preparation & Database Seeding
Execute from workspace root (`D:\Projects\Jarvis_Dashboard\jarvis-dashboard`) in PowerShell:
```powershell
# 1. Deterministic synthetic seed (idempotent; creates 1 workspace, 4 tasks, 2 coursework, 1 session note, 1 general note, 0 alerts)
npm run seed:beta
```
*Verification output will confirm: `Target DB: ...\Jarvis_Dashboard_Beta\db\jarvis-beta.db`.*

---

### B. Launch Beta Desktop Session (Development Mode)
```powershell
# 2. Launch owned beta backend on port 8005 and Electron frontend with shared run_id
npm run dev:beta
```
*Visual indicators of success:*
- Window title: `V.E.G.A. [BETA PROFILE · PORT 8005]`
- Tray tooltip: `V.E.G.A. [BETA PROFILE · PORT 8005]`
- Header badge: `BETA PROFILE · PORT 8005`
- Global shortcut: `Ctrl+Alt+Space` (leaves `Ctrl+Space` for personal profile)

---

### C. Window Hide vs. Actual Application Quit
- **Hide on Close / Blur:** Clicking the title bar `X`, pressing `Ctrl+Alt+Space`, or clicking outside in `Hotkey Overlay` mode merely **hides** the window to the system tray (`hide-on-close`). The backend and Electron process stay active in the background.
- **Actual Application Quit:** To terminate the session cleanly, either:
  1. Right-click the cyan tray icon in the Windows taskbar overflow notification area and click **Quit**, OR
  2. Press `Ctrl+C` in the PowerShell terminal that launched `npm run dev:beta`.
- **Post-Teardown Verification:** Confirm in PowerShell that no orphaned processes remain on port 8005:
```powershell
Get-NetTCPConnection -LocalPort 8005 -ErrorAction SilentlyContinue
```

---

### D. Same-Profile Restart Verification
```powershell
# Relaunch without re-seeding to verify data persistence across sessions
npm run dev:beta
```
*Verify tasks, completed states, coursework, notes, and window geometry persisted.*

---

### E. Packaged Binary Launch (Conditional / Unverified Artifact)
> [!NOTE]
> This checks the previously unpacked binary in `frontend\release\win-unpacked\`. Because this executable was compiled in an earlier phase and not rebuilt from current source, testing it evaluates past artifact behavior, not the active source tree.
```powershell
# Use PowerShell call operator & for quoted executable path:
& ".\frontend\release\win-unpacked\Jarvis Dashboard.exe" --profile=beta
```

---

### F. Profile Reset & Reseed (Confined to Beta Root)
> [!CAUTION]
> **Safe Teardown Required:** Never delete or remove `jarvis-beta.db` while VEGA or Python is running! Doing so will cause SQLite locking errors or database corruption. Ensure the application is completely shut down before resetting.
```powershell
# 1. Ensure all processes on port 8005 are stopped
Get-NetTCPConnection -LocalPort 8005 -ErrorAction SilentlyContinue

# 2. Remove ONLY the synthetic beta database (never touches personal database)
Remove-Item "$env:APPDATA\Jarvis_Dashboard_Beta\db\jarvis-beta.db" -Force -ErrorAction SilentlyContinue

# 3. Reseed fresh baseline
npm run seed:beta
```

---

## 3. Supervised Acceptance Test Matrix

Every item must be evaluated and marked by the owner during the supervised Windows session.  
Status options: `NOT RUN` | `PASS` | `FAIL` | `DEFERRED` | `UNVERIFIED`.

| ID | Category | Test Case & Action | Target Build | Expected Behavior | Status | Evidence / Notes |
|---|---|---|---|---|---|---|
| **SEC-01** | Profile Isolation | Check active data paths and port | Dev / Packaged | Backend on 8005; DB at `Jarvis_Dashboard_Beta\db\jarvis-beta.db`; personal `jarvis.db` untouched | NOT RUN | |
| **SEC-02** | Credential Safety | First run without credentials | Dev / Packaged | UI operates in offline deterministic mode; `/health` reports `deterministic_only: true` | NOT RUN | |
| **SEC-03** | Cloud Refusal | Send query: *"What is quantum computing?"* | Dev / Packaged | Truthful refusal: *"VEGA is running in deterministic-only mode (no AI model selected)"*; zero cloud calls | NOT RUN | |
| **SEC-04** | Port Clash Refusal | Start mock service on port 8005, then launch | Dev | Refuses launch with clean error message; does not kill external process | NOT RUN | |
| **NAV-01** | Workspace Switcher | Press `Alt+1` then `Alt+2` | Dev / Packaged | `Alt+1` switches to Daily Workspace; `Alt+2` switches to Telemetry & Feeds | NOT RUN | |
| **NAV-02** | Tab Navigation | Click Workspace and Telemetry tabs | Dev / Packaged | Instant responsive switch; no layout jump or unmounted state corruption | NOT RUN | |
| **TSK-01** | Task Filters | Toggle `All` / `Open` / `Done` filters | Dev / Packaged | Filter updates task list instantly: 2 open tasks and 2 completed tasks seeded | NOT RUN | |
| **TSK-02** | Task Mutation | Check a task done, then uncheck | Dev / Packaged | Status updates optimistically; receipt returned; filter reflects state | NOT RUN | |
| **TSK-03** | Long List Overflow | Add 15 quick tasks | Dev / Packaged | List scrolls cleanly; bottom controls and input bar remain visible and pinned | NOT RUN | |
| **PRJ-01** | Project View | View "Compiler Optimization Engine" | Dev / Packaged | Goal, blocker, and next action display accurately from seeded workspace | NOT RUN | |
| **PRJ-02** | Coursework & Notes | Inspect Coursework and Session Note | Dev / Packaged | 2 coursework items (180m, 120m) and 1 session note display without error | NOT RUN | |
| **CHT-01** | Chat Persistence | Type message, change theme, switch views | Dev / Packaged | Messages retained across theme changes, tab switching, and window hide/restore | NOT RUN | |
| **CHT-02** | Command Distinction | Type mutating command vs read-only query | Dev / Packaged | Mutating intent (`add task:...`) produces action receipt; read-only query (`get today`) returns message without receipt | NOT RUN | |
| **RES-01** | Stale Read / Offline | Terminate backend process briefly while UI open | Dev / Packaged | Note: Wi-Fi disconnect does not sever loopback! Terminating backend surfaces amber stale warning without UI blanking; recovers upon backend restart | NOT RUN | |
| **NOT-01** | Timer & Toast (Visible) | Create 5s timer with window visible | Dev / Packaged | Timer counts down; in-app banner toast appears; audio plays if enabled | NOT RUN | |
| **NOT-02** | Timer & Toast (Hidden) | Create 5s timer, hide window (`Ctrl+Alt+Space`) | Dev / Packaged | Native Windows toast dispatched via Electron; clicking toast restores & focuses window | NOT RUN | |
| **NOT-03** | Toast Deduplication | Trigger repeated alert | Dev / Packaged | Deduplicator suppresses duplicate OS banner within TTL window | NOT RUN | |
| **NOT-04** | Truthful Claims Audit | Inspect notification event payload | Dev | Status reports `accepted` (`api_accepted`), never falsely claims user observed rendering until clicked | NOT RUN | |
| **PTT-01** | Voice / Wake Word | Inspect push-to-talk / speech status | Dev / Packaged | Background mic disabled in beta; speech transcription requires local faster-whisper. If assets absent, mark DEFERRED/UNVERIFIED | NOT RUN | |
| **WIN-01** | Window Hotkey | Press `Ctrl+Alt+Space` repeatedly | Dev / Packaged | Smoothly hides and shows overlay without stolen focus or flickering | NOT RUN | |
| **WIN-02** | Maximize / Restore | Click Maximize button (or `F11`) | Dev / Packaged | Expands to full screen / work-area; click again restores exact pre-maximized bounds | NOT RUN | |
| **WIN-03** | Actual Quit vs Hide | Test `X` (hides) vs Tray -> Quit (exits) | Dev / Packaged | `X` hides to tray; tray Quit terminates window and owned backend process | NOT RUN | |
| **RST-01** | Restart Persistence | Quit and relaunch `npm run dev:beta` | Dev / Packaged | All added tasks, completed states, window bounds, and theme survive restart intact | NOT RUN | |
| **SCL-01** | DPI Scaling (100%) | Test display at 100% Windows scaling | Dev / Packaged | Fonts crisp, borders aligned, no clipped buttons or overflowing cards | NOT RUN | |
| **SCL-02** | DPI Scaling (125%) | Test display at 125% Windows scaling | Dev / Packaged | Elements scale proportionally; no layout breakdown | NOT RUN | |
| **SCL-03** | DPI Scaling (150%) | Test display at 150% Windows scaling | Dev / Packaged | High-DPI layout readable; navigation and scrollbars accessible | NOT RUN | |
| **RES-02** | Min Resolution | Resize window to 1366x768 and 800x600 | Dev / Packaged | Minimum width/height constraints enforced; tabs and chat pane remain usable | NOT RUN | |
| **THM-01** | Theme Suite | Cycle sci-fi-hud, glass, terminal, cosmos | Dev / Packaged | Every theme applies cleanly; contrast compliant; no unreadable text | NOT RUN | |
| **PRF-01** | Idle Resource Usage | Measure CPU & RAM when idle (5 min) | Dev / Packaged | Record average CPU % and Private Working Set RAM with host hardware context | NOT RUN | |
| **PRF-02** | Hidden Resource Usage | Measure CPU & RAM when hidden (5 min) | Dev / Packaged | Verify background throttling reduces CPU load compared to visible rendering | NOT RUN | |
| **PRF-03** | Latency Measurement | Measure typed deterministic command latency | Dev / Packaged | Repeat 5 times; record min / avg / max execution latency in milliseconds | NOT RUN | |
| **PRC-01** | Process Teardown | Exit via tray Quit and check port 8005 | Dev | No orphaned processes remain; port 8005 freed immediately | NOT RUN | |

---

## 4. Known Environment Limitations & Supervised Guidance

1. **Local Audio / Wake Word Hardware:**
   - Background microphone listening is intentionally disabled under `VEGA_PROFILE=beta`.
   - Push-to-talk transcription uses local faster-whisper models if present. If hardware or models are unprovisioned, mark **DEFERRED: Hardware / assets not provisioned on host machine**.
2. **Windows Action Center Quiet Hours / Focus Assist:**
   - Windows 11 Focus Assist / "Do Not Disturb" may suppress native toast popups to Action Center.
   - If toast does not render on-screen, check Action Center (`Win+N`). The in-app toast banner will always surface.
3. **Packaging Artifact Read-Write Safety:**
   - Production installations on Windows reside in `C:\Program Files\Jarvis Dashboard\`.
   - The packaged backend receives its database path in `%APPDATA%`, preventing write-permission crashes in Program Files.
   - Packaged checks are labeled conditional because the binary in `frontend\release\win-unpacked\` has not been recompiled from the current source tree.
