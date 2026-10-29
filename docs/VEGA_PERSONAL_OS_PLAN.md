# VEGA personal desktop companion — revised product plan

Revision: 2, 28 October 2026. Status: researched proposal and interactive concept; new features are not implemented.

> Current owner priority: improve existing voice capture/transcription and train the selected “Hey Vega” wake phrase before D1. See [voice quality work](VOICE_QUALITY_AND_HEY_VEGA.md). The product proposal below remains intact; it is not the active implementation run.

## 1. Decision and correction

VEGA should help the owner turn intentions into sustained progress across learning, software projects, research and everyday computer work. Its interface lives on the desktop, accepts voice, and can prepare or execute approved routines. **The product is a goal-aware personal assistant; computer control is one capability inside it.**

The previous Voice Pilot proposal made Calculator and Notepad too prominent. They are useful internal driver smoke tests, not the product vision, release scope or required first user experience. This plan supersedes the product ordering and next-run recommendation in `VEGA_VOICE_PILOT_MVP.md`. Its cancellation, target verification and execution safeguards remain relevant engineering references.

The owner's first proving journey is preparation starting **1 November 2026**, combining LeetCode consistency and GSoC/open-source work. This is the first domain pack, not a restriction that VEGA can only handle study. Later packs can reuse the same core for project delivery, reading/research, applications and personal administration.

“Almost everything” is a direction, not a testable release promise. A useful general assistant has extensible capabilities and knows when it lacks information, access or a reliable action. It cannot infer every preference from screen activity, make unobserved claims, or guarantee GSoC selection. Breadth should grow through verified adapters and explicit routines, not an unrestricted mouse/shell loop.

## 2. What to combine from existing products

Primary sources checked on 28 October 2026. These are behavior references, not products the owner must purchase. No vendor performance claims were independently benchmarked.

| Reference | Useful behavior | What VEGA should adopt or change |
|---|---|---|
| [Zebar](https://github.com/glzr-io/zebar) | Custom desktop widgets built with web technology | Movable/pinnable task surfaces. Evaluate the host later; initially reuse VEGA's existing Electron runtime rather than install another runtime. |
| [Rainmeter](https://docs.rainmeter.net/manual/getting-started/index.html) | Independent desktop skins and persistent visual access | Desktop presence and personal layout. It is not itself an intelligent planner or a window manager. |
| [Raycast quicklinks](https://www.raycast.com/core-features/quicklinks) and [AI extensions](https://manual.raycast.com/ai/ai-extensions) | Fast navigation and discoverable actions across tools | One action opens the right problem, notes, repo or approved routine. Show capabilities and action results. Do not copy proprietary application code. |
| [Motion auto-scheduling](https://www.usemotion.com/help/time-management/auto-scheduling) | Schedule using duration, availability, priority and deadlines | Time-feasible planning with an explanation. Begin with the owner's stated availability, not invented calendar knowledge. |
| [Reclaim habit rules](https://help.reclaim.ai/en/articles/15280604-reclaim-2-0-faq) | Flexible windows, minimum durations, conflict and skip rules | Adapt after disruption without endlessly accumulating missed work. Preview meaningful changes and respect locked commitments. |
| [Super Productivity](https://github.com/super-productivity/super-productivity) | Offline-first planning, timeboxing, projects and reflection; MIT repository | Reference daily workflow and completion review. VEGA already has related tools: integrate them rather than create a competing second task database. |
| [Anki recall and spaced repetition](https://docs.ankiweb.net/background.html) | Attempt retrieval, then review according to learning history | Real flashcards, not decorative motivational tiles. Use a tested scheduler such as MIT [Py-FSRS](https://github.com/open-spaced-repetition/py-fsrs), with editable source-backed cards. |
| [Obsidian Canvas](https://obsidian.md/help/plugins/canvas) | Spatial arrangement of notes, links and related material | Let users curate their desktop and project context. Keep exportable records; a whole graph canvas is unnecessary for the first daily loop. |
| [ActivityWatch](https://github.com/ActivityWatch/activitywatch) | Optional local activity observations | Later opt-in context signal. Time in an app is not proof of focused work or a solved problem. No continuous screen recording requirement. |
| [Cua Driver](https://cua.ai/docs/cua-driver/concepts/platform-support) / [Microsoft UFO](https://github.com/microsoft/UFO/blob/main/ufo/README.md) | Structured desktop observation and execution | Computer-operation layer for later cross-app routines. Retain refusals and native verification; background control remains app-dependent. |

The synthesis is a shared loop:

**Goal → feasible daily plan → useful desktop cards → prepared workspace → actual work → honest evidence → adjusted next session.**

An LLM helps interpret goals, explain tradeoffs, recommend relevant material and handle unfamiliar requests. Scheduling, review dates, deduplication and permissions should not require an LLM call every minute. This is how the design remains affordable and usable offline.

## 3. The first daily experience

### Before November 1

The owner creates two goals: a 100-day practice habit and 100 distinct solved problems. They select a starting curriculum or their own problem links, their programming language, approximate available time, and at most two candidate open-source projects. Defaults can be suggested, but actual preferences and baseline ability must be recorded rather than guessed.

They choose which cards to pin and whether VEGA may show a daily briefing. They grant separate permissions for opening study links, starting focus sessions and reading selected project folders. Microphone, app observations, auto-start, cloud calls and proactive popups each have their own opt-in.

### A normal day

1. At an owner-selected time or first app launch, VEGA prepares a short daily plan. It shows what is based on recorded history and what is merely a starting assumption.
2. The desktop shelf displays **next session**, **recall due**, **next open-source step**, and the owner's shortcuts. No chat conversation is required to find them.
3. Clicking “Start session” or saying “Prepare my practice” opens the selected problem and notes, then starts the existing focus timer. Each action is represented by the same session ID.
4. The user attempts the problem. Hints are progressive: clarify the question, suggest a concept, then discuss an approach. Full solutions are available when explicitly requested and recorded as assisted learning.
5. At the end, VEGA asks for a brief result: attempted/solved, help used, what was difficult, and optionally a spoken explanation. It does not require a long journal to finish.
6. A difficult concept can become a draft recall card. The user edits or approves it; the source and any uncertainty stay attached.
7. Tomorrow's plan adapts to actual duration, mistakes and due reviews. Completed work remains completed; unresolved work is prioritized or deferred rather than copied endlessly.

### When the day changes

“I only have 45 minutes.” VEGA proposes a smaller session, showing what moved and why. It does not silently delete goals or pretend four difficult questions fit into twenty minutes. “Leave today alone” locks the plan. After missed days, offer a restart session instead of doubling tomorrow's workload.

## 4. LeetCode: distinguish habit, quantity and understanding

- **100 consecutive practice days** beginning November 1 end on **February 8, 2027**, if November 1 is day 1. This is VEGA's local calendar calculation, not a claim about a platform badge.
- **100 distinct solved problems** is a separate milestone. Three to four new problems every day implies roughly 300–400 over 100 days; reaching 100 at that rate takes around 25–34 days.
- The owner's requested 3–4 questions remains configurable. A suggested initial alternative is one new problem, one previous-problem review, and optional stretch work; choose intensity from available time and observed difficulty.
- Track new attempts, distinct accepted solves, independently solved, assisted solves, reviews, and practice days separately. A repeat submission must not increment distinct-problem totals.
- Do not label time spent, an opened URL, generated code, or a self-report as externally verified acceptance. Show evidence provenance: owner-reported, imported, or observed platform result. Assistance level is a user disclosure, not something the system can reliably infer.
- Track the local practice streak in Asia/Kolkata. If platform streak data is later integrated, preserve its own rules and timestamps; never equate the two automatically. A missed consecutive day really breaks that streak, while total work and weekly consistency remain visible.

[LeetCode 75](https://leetcode.com/studyplan/leetcode-75/) is an official starting curriculum option. Use links and metadata, not a bulk copy of paid/editorial content. This research did not establish a stable supported public account API. MVP completion entry therefore works manually. Optional import or permissioned browser observation can follow after checking the supported access route; no credential harvesting, brittle private GraphQL dependency, or automatic solution submission is required.

The tool must support learning rather than optimize badge counts. It should make starting easier and understanding visible; it cannot do the user's practice for them and count that as independent mastery.

## 5. GSoC: an actual contribution workflow

Provide a separate goal track: choose a project → run it → understand a small code path → reproduce an issue → attempt a bounded change → tests → reviewed contribution. Each next-action card links directly to the relevant repository, contribution guide, issue, file or saved note.

Useful proactive behavior: a selected issue changes, a maintainer replies, a documented prerequisite is missing, or a previously blocked task becomes possible. Summarize only selected repositories, with links and last-checked times. A beginner label alone does not prove an issue is a good fit; show requirements and uncertainty.

LeetCode is one preparation activity, not the definition of GSoC readiness. Repository comprehension, debugging, tests and communication need protected time too. Do not promise selection from a streak or a plan.

The official timeline reviewed still labels its concrete dates **2026**. Do not copy those dates into a 2027 application calendar. Record future dates only when the relevant official announcement is verified. [Official timeline](https://developers.google.com/open-source/gsoc/timeline)

The current official AI guidance says organizations set their own rules and contributors remain responsible for understanding and validating their work. VEGA should keep each selected organization's policy with its project context and respect it when helping draft code or proposals. Messages and submissions remain owner-reviewed. [Official AI guidance](https://developers.google.com/open-source/gsoc/resources/ai_guidance)

## 6. Creative features that justify their complexity

| Feature | User value | Priority / evidence needed |
|---|---|---|
| **Make this a desktop card** | Say or click to pin a link, goal, note, recall question or routine | MVP. Preview typed card content and its permission scope before saving. |
| **Prepare my workspace** | Open the exact problem, notes, editor folder and focus timer together | MVP using existing safe launchers; richer window arrangement later. Partial outcomes must remain visible. |
| **Return to where I stopped** | Restore the last useful context and unresolved question | MVP session note and links; later observed app state. Never claim unsaved editor buffers are safely restored without support. |
| **Explain it back** | Brief spoken explanation exposes gaps before declaring a topic understood | Later voice coaching; model feedback is advisory, with examples and uncertainty rather than an invented mastery percentage. |
| **Turn a mistake into practice** | Convert a recurring confusion into a small recall or counterexample task | First learning extension. Drafts cite the attempt or chosen source; the owner controls what enters their deck. |
| **Show me once, save a routine** | Turn an approved sequence into a reusable personal capability | Later. Store parameterized actions and preconditions, not fragile screen coordinates or unreviewed recorded clicks. |
| **Useful change alerts** | Surface a relevant maintainer reply, due review or blocked-task resolution | Later opt-in background checks. Deduplicate; no update means no notification. |
| **A realistic smaller day** | Shrink today's plan when time or capacity changes | MVP rule-based adaptation, then optional richer recommendations. Show deferred work and a reason. |
| **Prepare while I work** | Read approved docs or draft tomorrow's plan without interrupting focus | Later read-only worker, bounded resources and cost. No second agent competing for mouse/keyboard. |

Exclude from the first release: a universal agent marketplace, autonomous financial/email actions, always-on screen surveillance, automatic GSoC applications, self-modifying permission rules, a complicated 3D world, and an AI agent continuously generating new tasks. They add risk or distraction before the core daily loop proves useful.

## 7. Autonomy that grows with evidence

| Level | VEGA can do | Owner control |
|---|---|---|
| Suggest | Prepare plans, reminders and draft cards from authorized records | Explain the recommendation; edit, dismiss or disable it |
| Prepare | Open approved URLs/apps, arrange a known session and start its timer | Standing permission for the routine, visible progress and immediate stop |
| Maintain | Refresh cards, schedule reviews, reschedule flexible local blocks, summarize selected-source changes | Rules for time windows, quiet hours, maximum nudges and what stays locked |
| Execute broader work | Modify files, operate unfamiliar apps, use remote services | Explicit capability scope, verified outcomes, and review for consequential actions |

Autonomy is attached to a goal, resource scope and action class, not a single “allow everything” switch. A user-defined card cannot become arbitrary executable JavaScript. New skills declare inputs, tools, permissions, cancellation and completion evidence.

Triggers are concrete: day rollover, app launch after sleep, session finish, owner availability change, due review, or a relevant source update. The planner recomputes only affected work. No background LLM loop is necessary for an unchanged desktop card.

## 8. Interface direction

Use the previously preferred **Pilot dock** as the companion's voice entrance. Add a **desktop shelf** for useful cards and an expanded workspace for planning/history. One shared state drives every surface.

- **Desktop shelf:** movable, resizable cards for next action, recall and links; restrained layout, readable opaque surfaces, subtle depth and accent lighting. The primary action is obvious.
- **Focus dock:** one current session, a hint/voice entry and Pause/Stop. The main workspace stays visible; decorative animation does not compete with reading or coding.
- **Expanded workspace:** goals, weekly view, session history, card editor and permission settings. Details appear on demand rather than as permanently competing panels.
- **Card types:** shortcut, note, goal progress, current session, recall card and routine. An action card opens something; a flashcard tests recall. They must not be conflated.
- **Popup behavior:** show only when opted in; respect quiet hours, focus/fullscreen contexts where detectable, cooldowns, snooze and a visible disable control. Prefer a briefing or badge over stealing keyboard focus.
- **Truthful status:** show stale/offline data, no account connection, self-reported completion and unobserved action outcomes explicitly. No decorative “agents working” activity.

Current source has a frameless Electron window but an 800×600 minimum and always-on-top behavior. That is not already a widget system. Use a dedicated small widget host with the existing app lifecycle, secure preload and narrow IPC rather than shrinking the main dashboard. Electron provides window controls, but true desktop-layer placement, Show Desktop, full-screen apps, multiple monitors and DPI behavior need native testing. [Electron BrowserWindow](https://www.electronjs.org/docs/latest/api/browser-window)

Start with one widget-host window to avoid multiplying renderer memory. Measure resource use before adopting Zebar or a new shell. Widgets should sleep when idle; optional local inference should not occupy GPU resources continuously.

The new conversation concept shows two directions: Desktop shelf and Focus dock. Its time-budget changes, reveal interaction and card creation are simulations. It creates no real reminders, account records, desktop windows or study history.

## 9. Engineering plan

Keep Electron/React, FastAPI/SQLite and working productivity/voice components. Add a shared goal/session model and bridge rather than fork a task manager, launcher and flashcard app simultaneously.

Core records: Goal, Routine, PlanRevision, Session, ResourceLink, RecallCard, ReviewEvent, WidgetPlacement, PermissionGrant and ActionJob/Receipt. Map these to existing tables before introducing a duplicate concept. A review event is not a task completion; an app launch receipt is not learning evidence.

Frontend requests validated actions; backend owns persistence, planning and review scheduling. All surfaces subscribe to versioned events and re-fetch after reconnect. Durable idempotency keys prevent duplicate timers, sessions or reviews. Background work has a single owner and handles sleep/wake without replaying a backlog of notifications.

Daily plan generation starts with a deterministic priority/capacity scheduler: due reviews, goal priorities, estimated durations, prerequisites, locked blocks and breaks. Keep user edits locked unless explicitly changed. Optional model output supplies a constrained suggestion, not unchecked database writes or a schedule that exceeds available time.

Budget remains free first; optional INR100–300/month. Local planning and scheduling work without a provider. Use inference for goal interpretation, explanations and selective recommendations. Reuse earlier research for model candidates; do not download models or enable cloud billing just to display cards. Paid calls require opt-in, input disclosure and cost reservations.

Cross-app action jobs use the cancellation and verification contracts from the earlier research. Current speech interruption does not cancel a running action job. Solve that before broad background execution. Scope native adapters to capabilities and extend them as workflows demand; Calculator remains a smoke test, not a customer feature milestone.

## 10. Revised sequence and acceptance

Stable proposal IDs below replace the earlier V-stage product sequence. Existing accepted Phase 1 history and evidence are preserved, not relabelled as new progress.

| Stage | Deliverable | Exit criteria | State |
|---|---|---|---|
| D0 | Research, product synthesis and interactive concept | Sources, decisions, honest constraints and revised plan | Complete as planning |
| D1 | Goal-to-desktop vertical slice | Create a goal; pin next-session/shortcut card; launch selected study resource; save a session outcome; survives restart | Next, not started |
| D2 | Learning loop | Separate practice/solve/review metrics; editable source-backed recall cards; tested review scheduling | Not started |
| D3 | Adaptive daily companion | Time-feasible daily plans, lockable edits, quiet nudges, restart/sleep handling and explainable replanning | Not started |
| D4 | Voice routines and broader operation | Voice triggers the same session; cancellable jobs; verified multi-app routines and scoped adapters | Not started |
| D5 | Owner pilot and release | One week of actual use, native layout/notification tests, resource measurements and verified packaging | Not started |

Count: **1 planning stage complete, 0 implementation stages started, 5 remaining.** One implementation stage at a time. No deadline commitment is justified before D1 is measured.

Minimum useful release: D1–D3, with existing voice reused where verified. General desktop autonomy expands through D4; it is not a prerequisite for making daily preparation useful.

Cross-stage acceptance:

1. A changed 45/90/150-minute availability produces a plan that fits exactly within the selected budget including breaks, and explains deferred work.
2. A daily card, full workspace and voice action refer to the same session and update together without a manual extra task mutation.
3. An opened problem or running timer does not mark it solved. Repeated completion signals cannot inflate distinct solves or streak days.
4. Restart, midnight in Asia/Kolkata, sleep/resume and duplicate events preserve records and do not repeat popups or actions.
5. A missed day or difficult session yields a feasible next plan without falsifying a consecutive streak or discarding history.
6. Flashcard edits and deletions persist; repeated ratings are idempotent; due times use the chosen scheduler and timezone rules, not LLM guesses.
7. Permission revocation takes effect before the next action. Stop prevents subsequent job steps; already completed effects are reported honestly.
8. Quiet mode and popup opt-out work in real Windows tests. Cards remain accessible after monitor removal and DPI changes.
9. Free mode makes zero paid calls. No credentials, personal database or existing browser profile serves as a test fixture.
10. The owner can start a useful session in at most two interactions from the visible shelf, and the seven-day pilot records whether it actually reduced setup effort.

## 11. Next implementation handoff: D1 only

Read AGENTS.md, this plan, the current status report and implementation log. Preserve dirty files, .env, personal databases/WAL/backups and builds; no commit/push/reset/clean. Record branch/HEAD and the existing changes.

Trace existing goals/workspaces, focus timers, task persistence, event refresh, Electron window lifecycle and session verification before adding new concepts. Use bounded read-only subagents for data-model reuse and native-widget/test review; keep one editing owner.

Implement the smallest complete **goal → desktop card → selected resource → session outcome** journey using a synthetic beta profile. D1 may use a user-supplied link and available-minute setting; do not require inference or an external account. Use additive migrations only where necessary. Maintain authoritative backend state and reuse existing notification/event paths where appropriate.

Provide one secure widget host, visible hide/show/quit controls and a next-session card with a validated link. Persist visibility and position with monitor bounds checks. Never expose a universal shell command or arbitrary renderer execution through a card. Existing beta identity verification applies to every mutation. Record an attempted session separately from a solved problem.

Test reconnect/live refresh, persistence, duplicate action requests, rejected URL schemes, profile mismatch and window-position recovery. Run targeted tests and relevant existing integration checks, plus git diff --check. Native Windows tests must separately verify focus behavior, restore, DPI and real link launching; leave unobserved items NOT RUN.

Deliver a usable D1 walkthrough and exact evidence. Stop before automatic curriculum generation, paid providers, a general desktop agent, account scraping or a full flashcard system. Update the implementation log and propose D2 based on observed issues.

## 12. Start practicing even if the software is unfinished

November 1 is the start of the owner's habit, not a reason to claim the whole product can ship in four days. Use a saved problem list, a simple session note and an existing timer until D1 is usable. After the first week, compare planned time with actual time and revise the default load. Do not let constructing the perfect productivity tool become a prerequisite for doing the work it is meant to support.
