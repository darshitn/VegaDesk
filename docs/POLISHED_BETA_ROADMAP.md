# VEGA: path to a polished personal beta

**Current review, 3 October 2026:** E2A/E2B and BETA-UI changes are present.
Codex reran 45 frontend tests and lint (0 errors, 18 warnings). Next is
[isolated acceptance preparation](ANTIGRAVITY_BETA_ACCEPTANCE.md), then an
owner-initiated Windows session. Layout/scaling, native notification rendering,
microphone and installed-package behavior remain unverified. The working tree
is dirty and must be preserved; earlier snapshots below are historical evidence.

**Review update, 2 October 2026:** P1-D2 corrections and P1-E1 are present;
42 combined targeted tests passed independently. Full 407-backend results remain
agent-reported. Next is [P1-E2A](ANTIGRAVITY_P1E2A_INTERACTION.md): honest
interaction/onboarding and readiness feedback. P1-E2B covers notifications in a
separate session, then BETA-UI and supervised Windows acceptance. The original
review findings below record the earlier snapshot.

Prepared 1 October 2026 from the current source, the P1-D2 report, independent
migration checks, bounded read-only reviews, and primary documentation research.
This is a plan for later implementation sessions. No application redesign or
desktop acceptance was performed for this document.

## Product target

Make one daily routine dependable: see Today, capture a commitment, start focus,
receive a reminder in another app, resume a project, save the next action, and
recover the same context tomorrow. Keep ordinary commands usable without a
cloud provider. Reuse existing task, timer, project and academic modules.

## Current review outcome

P1-D2 contains worthwhile changes: SQLite online backup, corruption/version
checks, backup validation, and version stamping after final creation checks.
The 23 existing migration tests passed independently; 388 full backend passes
remain agent-reported. Three temporary probes showed incomplete schema
validation and partial DDL persistence. See the
[next correction prompt](ANTIGRAVITY_P1D2_CORRECTION.md).

Other source findings are release planning leads, not desktop observations:

- `/health` reports only `ok` and voice; frontend health is fetched once at mount.
- SetupWizard selects Gemini by default, even though App can honor the backend default.
- Some task deletion/timer cancellation paths ignore HTTP status; UI can look successful after failure.
- Renderer notifications request permission while Electron's permission handler permits only media; hidden-window notifications need investigation and Windows evidence.
- Provider deadline checks surround synchronous calls; Gemini request-level timeout handling needs verification.
- Today/Resume support exists deeper in ProductivityHub while telemetry and feeds have prominent space.

## Milestones: implement one per session

| Order | Scope | Exit evidence |
| --- | --- | --- |
| 1: P1-D2 correction | Required schema/index validation, actual DDL rollback, realistic fixtures, bounded backups | Negative cases refused; upgraded ORM reads/writes work; failure snapshots and fresh tests |
| 2: P1-E1 readiness | Service liveness vs core readiness; optional provider/voice/scheduler state; fault isolation | Deterministic commands work during mocked model failures; recovering UI status; no inference on health checks |
| 3: P1-E2 interaction correctness | HTTP error feedback, stale-data labels, pending controls, onboarding provider choice, notification channel | Failed writes retain user inputs/state; cloud opt-in; controlled notification path; repeated actions do not duplicate writes |
| 4: BETA-UI | Today/Focus/Resume primary view; consistent spacing/type, keyboard access, useful empty/loading/error states | Daily workflow reachable; long lists scroll; chat survives theme changes; layout checked at target sizes/scaling |
| 5: BETA-ACCEPTANCE | Separate test profile, supervised Windows workflow, package/user-data inspection | Manual evidence for microphone/hidden alerts/tray/restart; no secrets/data embedded; known failures documented |

After each gate, inspect the diff and update the implementation log. Do not
automatically implement later rows in the same run. The owner will initiate
desktop acceptance when ready. Plan it now; do not start an installer or the
personal database merely to satisfy this roadmap.

## P1-E1: future implementation brief

Activate only after the P1-D2 correction passes. Reuse `/health`; preserve its
existing contract for Electron clients. Add a lightweight readiness contract
only where needed, with separate required DB/core state and optional services.
Report disabled, configured, unknown, checking, ready, degraded or unavailable
truthfully. Include last check/tick and safe failure reason, without credentials
or personal paths. Track scheduler task state rather than assuming an object
means the loop is running.

Health checks must not load Whisper, acquire microphone input, generate model
tokens, download models, or make unsolicited cloud requests. Use bounded local
model metadata probes only when enabled; configured credentials are not proof
of cloud reachability. Cache checks and separate active diagnostics if needed.
Make frontend status recover after a backend startup race/outage with bounded
refresh and cleanup. Verify provider unavailable/rate-limit/timeout behavior
using fake clients and temporary databases. Apply real SDK deadlines where
necessary; a check after a hung synchronous call is not a timeout.

## What polish should deliver

- **Primary screen:** Today, active focus/timer, next commitment, Resume project, quick capture. Keep telemetry, feeds, 3D and gestures in secondary views.
- **Clear action outcomes:** show saving, saved, failed, offline or stale. Keep typed content after failure. Distinguish launch accepted from observed app/window state.
- **Local onboarding:** deterministic-only use when no model is installed; choose an already installed local model; cloud requires explicit selection. No automatic downloads or credential requests.
- **Input choices:** typed commands always available; push-to-talk and wake-word modes should explain microphone state. Describe the actual pretrained wake phrase.
- **Visual consistency:** one typography/spacing system, readable contrast, visible keyboard focus, labels for icon controls, predictable scrolling and reduced-motion support. Preserve chat and feature access across themes.
- **Resource discipline:** measure idle/hidden CPU and RAM, voice-to-result latency, startup and request duration. Then suspend unnecessary hidden visuals/polling while preserving voice and alert delivery. Set numerical targets from the measured baseline.

## Acceptance session to run later

Use a synthetic beta profile with a separate database and Electron user-data
directory. Document how it is isolated before starting. Package validation must
inspect where production writable data lives and how upgrade preservation works;
do not silently relocate the personal database. A production build is not proof
of successful installer behavior.

Record pass/fail/unverified with evidence for: typed task creation/list/completion;
provider unavailable; setup without credentials; push-to-talk/wake-word; timer
and reminder while visible, hidden and minimized; Windows notifications disabled;
tray/hotkey; long task list; failed write; Today/project resume; edited session
note; restart persistence; theme switching with chat retained. Check 1366x768,
a larger display, narrower windows and Windows 125-150% scaling when available.
Preview size emulation is not real Windows scaling or microphone evidence.

## Improvements after a week of use

| Idea | Why it earns its scope | Dependency |
| --- | --- | --- |
| Registered project launch routine | Open approved editor/folder/links and show the next action | Per-step results, cancellation and existing policy/receipt reuse |
| Search saved tasks/projects/session notes | Find work already captured without repeating context | Scoped local queries and clear filters |
| Weekly review and study plan | Summarize overdue commitments and saved progress | Reliable local records; editable suggestions |
| Selected course PDF retrieval with citations | Answer study questions grounded in supplied files | Explicit selected-folder import, citation evidence, resource budget |
| Supported browser workflow | Automate one repeated real task | Structured controls, exact permissions and verification |

Choose the first addition from observed personal-use friction. Do not implement
all these ideas at once or introduce unrestricted app/shell control.

## Research behind the recommendations

- [Electron notifications](https://www.electronjs.org/docs/latest/tutorial/notifications): Windows notification registration and development/packaging differ; verify the actual channel.
- [Ollama model listing](https://docs.ollama.com/api/tags): installed-model metadata can support lightweight local readiness without generation.
- [W3C status messages](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html) and [visible focus](https://www.w3.org/WAI/WCAG22/Understanding/focus-visible): readable feedback and keyboard navigation are concrete polish criteria.
- [Electron performance guidance](https://www.electronjs.org/docs/latest/tutorial/performance): measure bottlenecks and protect responsiveness before adding expensive UI work.

These sources inform recommendations; they do not prove current VEGA behavior.
