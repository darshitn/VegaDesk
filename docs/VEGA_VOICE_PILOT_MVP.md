# VEGA Voice Pilot — proposed MVP and bounded next run

> Product direction revised on 28 October 2026: use [VEGA Personal OS plan](VEGA_PERSONAL_OS_PLAN.md) for the latest scope and D1 next step. The V-stage sequence and next implementation prompt below are historical proposals. Calculator/Notepad are internal driver tests; retain the technical cancellation/verification guidance where applicable.

Date: 28 October 2026. Status: proposed new direction, pending implementation. Research and interface concept are complete; none of the new desktop capabilities below are claimed implemented. The earlier roadmap and its acceptance records remain intact.

## Product promise

“Speak a goal. VEGA operates supported Windows apps, shows its progress, and tells you what actually happened.”

Primary user: the owner on their Windows laptop. First supported language: English, tested with the owner's actual accent and microphone. Default recurring API spend: zero. Optional paid mode is explicitly configured and capped; design target INR100–300/month for occasional use, not unlimited cloud operation.

## Five first workflows

| ID | Spoken request | Scope | Proof of success |
|---|---|---|---|
| W1 | “Open Calculator and calculate 18 times 24.” | Named app; visible UI operation | Read 432 from Calculator's own current control state, not from the model's arithmetic. |
| W2 | “Write a note saying: revise sorting algorithms.” | A new synthetic Notepad document first; explicit target binding | Read back exact text from the intended editor. Later saving is restricted to the approved folder and cannot overwrite silently. |
| W3 | “Open the documentation and find the installation section.” | Dedicated browser profile and approved documentation site | Observe final URL, page title, and matching heading through browser structure. Do not claim the entire article was read from a search snippet. |
| W4 | “Find my sorting notes and open them.” | Owner-approved fixture folder first; no whole-drive search | Resolve exact file; ask once on ambiguity; verify the opened target where observable. Never open a guessed match. |
| W5 | “Start my coding setup.” | Saved recipe: approved project folder/editor, approved documentation, existing focus timer | Verify each step independently and show partial completion if any step fails. Stop prevents remaining steps. |

These cover native apps, text entry, browser control, local files, and a cross-app recipe. Existing timers/tasks remain available. A later “explain this screen” feature can use an explicitly shared window; continuous screen recording is not a prerequisite.

## Architecture

```text
Opt-in microphone / push-to-talk / typed command
  -> local wake + speech recognition
  -> transcript visible to owner
  -> deterministic intent OR bounded planner
  -> typed job and scope validation
  -> existing policy boundary + approval when required
  -> one desktop executor at a time
       native/API tool | browser structure | Windows accessibility | limited vision
  -> fresh post-action observation
  -> receipt + concise spoken result + visible evidence
```

Keep the model outside the authority boundary. Candidate `DesktopAdapter` operations are observe, resolve target, invoke control, set text, verify, and cancel pending work. A generic coordinate click is a controlled fallback, not the main interface. Every observation carries a window/process identity and timestamp; resolve again after UI changes. A PID alone is insufficient for stable target identity.

Use one job coordinator. Parallel read-only research can come later; two agents must not simultaneously control the same desktop. Runtime subagents are optional, distinct from development subagents.

Job states: queued, planning, awaiting_approval, running, verifying, succeeded, failed, cancelled, interrupted. Persist job IDs and per-step outcomes. On restart, interrupted desktop writes require reconciliation, not automatic replay. Repeated messages must not type or save twice.

## Cancellation and permissions are core functionality

“Hey Jarvis” while speaking interrupts TTS and opens a fresh turn. “Stop” or the stop button during action execution cancels the active job. These are distinct operations. Cancel the request, mark the job generation invalid, and prevent new tool dispatches; late model responses cannot restart it. Report any action already completed. A submitted click or file write cannot always be undone.

The emergency stop path must not wait for language-model inference. Keyboard and UI stop remain available if wake detection fails. Permission to view an app is distinct from permission to type, save, delete or transmit its contents.

Use task-scoped grants for ordinary approved actions instead of requesting confirmation for every click. A grant binds to exact action class, app or folder, job and expiry. Revalidate before dispatch. Never elevate the app to administrator just to bypass a refused target. Browser page instructions and screen text are data, not permission.

The first slice operates only Calculator and synthetic Notepad/browser fixtures. Email sending, purchases, credentials, security settings and destructive file operations are out of scope. Later consequential actions require a concrete final preview and explicit approval. This is a product boundary, not a claim that prompts make automation safe.

## Models and voice

- Keep the current local voice stack initially; measure rather than assume it needs replacement.
- Current source uses a command-oriented silence cutoff and a 15-second recording limit. Longer dictation needs an explicit mode with a visible transcript, pause tolerance and finish/cancel controls; do not silently stretch command listening into indefinite recording.
- For common requests, use deterministic parsing and typed arguments. It should remain useful when every model service is down.
- Compare quantized Qwen3.5-4B and Gemma 4 E2B for structured planning after driver proof. Use installed models first; any new asset must have a recorded source, version, license, download size and chosen storage path.
- Measure speech and inference together on this hardware. Start with short context and CPU transcription to limit GPU contention.
- Optional cloud calls disclose the transmitted context and use a bounded budget. A stronger model may improve difficult interpretation, but it cannot repair a bad target binding or missing verifier.
- Before enabling the current Ollama path for this MVP, resolve the redirect and readiness-timeout findings in the status report and verify with an actually installed model.
- Spoken confirmations should be brief: “Note written,” “Timer set for 25 seconds,” or a precise failure. Evidence belongs in the expanded interface.

## Delivery stages

This is a proposed sequence, not a relabelling of the older six-stage guide. Exactly one implementation stage should be active after adoption.

| Stage | Deliverable | Exit gate | Current state |
|---|---|---|---|
| V0 | Research, scope, interface concept | Sources, hardware constraints, reuse decision and test plan | Complete as planning only |
| V1 | Desktop driver proof | W1/W2 typed-input trials and cancellation evidence; choose driver | Next, not started |
| V2 | Voice-to-job vertical slice | W1/W2 via real speech; stop cancels action dispatch | Not started |
| V3 | Browser, files and recipe tools | W3/W4/W5 with fresh observations, scoped permissions and receipts | Not started |
| V4 | Local planner and pilot interface | Model benchmark, responsive UI and honest state display | Not started |
| V5 | Owner pilot and packaging | Repeated native workflows, failure tests, clean shutdown and verified build | Not started |

Progress: 1 planning stage complete; 0 implementation stages started; 5 implementation stages remaining. Implementation effort is uncertain until V1 reveals driver and dependency compatibility. Plan a small proof over a few sessions, then estimate the remaining work from measurements.

## Acceptance gates

Use repeatable fixtures and independent result checks. Proposed thresholds are targets, not current measurements:

- Run each workflow ten times with specified initial state: at least 9/10 successful, with zero false-success claims. Report failures, latency, retries and owner interventions.
- Repeat the native smoke set at normal/restored window sizes and 100%, 125%, 150% Windows scaling. Test moved windows, duplicate titles, focus changes and stale controls.
- Test cancellation during planning, before dispatch, during a delayed tool and during TTS. No subsequent action dispatch after cancellation is acknowledged. Record in-flight outcomes accurately.
- Target visible acknowledgment within 300 ms after a completed transcript, and median simple-command result within 3 seconds after end of speech; measure each pipeline segment and report misses. Do not hide transcription delay behind token-throughput numbers.
- Inject instructions into a browser fixture; these must not expand permissions, read secrets or trigger unrelated tools. Reject an ambiguous app/document rather than trying the first result.
- Test backend disconnect, duplicate requests, restart, invalid model JSON and provider outage. The UI retains the user's request and labels partial completion.
- Free mode dispatches zero cloud requests. A paid-mode fixture reserves cost before each call and halts at budget boundaries, including concurrent requests and retries.
- Successful receipts contain evidence from the target app or browser. An accepted process launch must remain distinct from observed completion.

Mock tests are necessary for failure paths; they do not pass native desktop rows. Store only synthetic content in test traces. Stop after the requested stage and update the implementation log with exact results.

## Next implementation prompt

Copy the following into the implementation agent when ready. This is a handoff, not a claim the work ran during research.

> Read AGENTS.md, docs/VEGA_RESEARCH_2026-10-28.md, this MVP, docs/PROJECT_STATUS_2026-10-28.md and the current implementation log. Work on V1 only: a bounded Windows desktop-driver proof. Preserve all existing dirty changes, personal databases/WAL/backups, .env, credentials and builds. Do not commit, push, clean, reset or replace the app.
>
> First trace the current policy, receipt and voice-interruption paths. Record branch/HEAD and dirty files. Use bounded read-only subagents for adapter compatibility and cancellation/test review; keep one editing owner. Do not confuse cancelling speech with cancelling an action job.
>
> Define a small injectable desktop-adapter contract and a cancellable job runner in an isolated experimental module. Compare Cua Driver's documented Windows interface with a minimal pywinauto UIA baseline. Inspect exact package licenses, versions, Python compatibility, local-only operation and setup requirements. Select at most one external driver to integrate in this run after the comparison. Do not install remote-access/relay services or run a downloaded installer script blindly. If needed setup falls outside existing authorization, finish the reviewable code and setup manifest first and explain the specific remaining step.
>
> Support only Calculator arithmetic and typing into a new synthetic Notepad document. Start with typed commands, no LLM requirement. Bind each action to the observed process/window/control; reject ambiguity and stale observations. Use fresh app-owned state to verify arithmetic or exact text. Build no unrestricted shell or global type/click endpoint. No changes to real documents, browser profiles, settings or personal data.
>
> Add production-path synthetic tests for wrong-window refusal, stale control references, duplicate dispatch prevention, driver failure, stop before the next action, late responses after stop, and honest partial outcomes. Keep launch_accepted separate from desktop_verified. Audit how the existing authoritative receipt writer can be reused without duplicate receipts; do not bypass policy in a production route.
>
> Run relevant tests and git diff --check. For an owner-supervised native run, clearly identify the two fixture apps and actions first; record app/Windows/driver versions, actual observations, latency and failures. If native execution is unavailable, leave those rows NOT RUN and provide the exact walkthrough. Do not infer a native pass from mocks.
>
> Deliver the driver decision, changed files, exact test results, observed versus unobserved boundaries, and the V2 handoff. Update docs/IMPLEMENTATION_LOG.md. Stop after V1; do not redesign the full UI, download language models, enable cloud billing, or implement all five workflows in one run.

## Owner learning exercise

After V1, explain why a successful click call does not prove a successful action. Demonstrate this by moving or closing the target window between observation and execution. VEGA should re-observe or refuse, not type into whatever currently has focus. This distinction is central to building a dependable computer assistant.
