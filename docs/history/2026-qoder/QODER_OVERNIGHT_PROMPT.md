# Qoder overnight handoff for VEGA

Use this in Qoder with `D:\Projects\Jarvis_Dashboard\jarvis-dashboard` open as the workspace. The project plan is [VEGA_NEXT_LEVEL_PLAN.md](VEGA_NEXT_LEVEL_PLAN.md); the binding implementation scope and acceptance criteria are [VEGA_OVERNIGHT_SPEC.md](VEGA_OVERNIGHT_SPEC.md). Read both before coding.

## Recommended Qoder setup

- Choose **Qwen3.8-Max, High thinking** if your available credits comfortably cover a long run. Choose **Qwen3.8-Flash, High thinking** for the lower-credit option. Qoder's [model selector](https://docs.qoder.com/qoder/model-selector) currently lists Max at 0.5× and Flash at 0.1× credit consumption. This is a credit-rate comparison, not a guarantee of zero cost or uninterrupted execution; check your account's current balance and offers.
- Start a **Quest Agent** task in this workspace and enable **Goal** for the bounded M1 outcome. [Qoder's Goal documentation](https://docs.qoder.com/qoder/goal-driven) describes how it continues toward a measurable result. If you want it to start later, [Quest scheduling](https://docs.qoder.com/user-guide/quest/scheduled-tasks) can schedule the same task with Goal enabled; Qoder IDE must still be running at the scheduled time.
- Use **Auto approve** only if you are comfortable with routine edits and tests in this trusted workspace. Qoder says it still asks on detected risks; a prompt cannot override its permission checks. Avoid Full access for this run. If it pauses for approval while you sleep, review that exact operation in the morning. [Qoder access permissions](https://docs.qoder.com/qoder/approval-and-sandbox)
- Keep the PC awake, plugged in, and Qoder running. An overnight prompt cannot make a sleeping PC execute code. No release, upload, or publication is part of this assignment.

## Paste this into Qoder

```text
Work in D:\Projects\Jarvis_Dashboard\jarvis-dashboard. Read docs/VEGA_NEXT_LEVEL_PLAN.md and docs/VEGA_OVERNIGHT_SPEC.md first. Implement ONLY milestone M1 from that specification. The goal is a reliable, zero-recurring-cost daily VEGA assistant, not an entire product rewrite.

You are authorized to edit source, add tests and documentation, run local checks, and iterate until M1's acceptance criteria pass. Work in measured, reviewable steps. Begin by checking Git status, current source, and build/test commands. Preserve all existing uncommitted work. Read existing behavior before changing it. Never overwrite or delete backend/.env, jarvis.db, other user files, or real notes/tasks. Use a temporary synthetic database for tests. Add explicit, non-destructive schema migration logic; SQLAlchemy create_all does not alter existing tables. If migration moves data, keep a backup and rollback path.

Implement one backend command dispatcher and validated tool executor so typed commands and wake-word transcripts use the same path. Common commands must work offline without Ollama or Gemini: create/list/complete tasks, set/cancel timers, create/snooze reminders, and start/end focus sessions. Store stable IDs, UTC times, user time-zone context, lifecycle status, and action receipts. Use idempotency keys so retries do not duplicate actions. Clarify ambiguous dates instead of guessing. Reconcile due events after restart without repeating already delivered alerts. Make alerts visible while the overlay is hidden. Extend the existing Productivity Hub to display and manage these records. Keep current chat, wake word, app opening, notes, themes, and system monitor working.

Follow the full behavioral examples and acceptance checks in docs/VEGA_OVERNIGHT_SPEC.md. Keep the actual implementation simple; use existing FastAPI, Electron, React, and SQLite. Add a dependency only when it solves a concrete need. First make typed flows work, then wire voice through the same dispatcher, then add background alert behavior. Use a single scheduler owner. Integrate model tool calling only after deterministic flows work. Do not expose general shell execution, arbitrary file deletion, or unrestricted PC control.

Do not push, merge, tag, publish, deploy, change Git remotes, reset/revert/stash, or make purchases. Do not modify files outside this repository. Do not read or print secret values. Do not commit a database, backup, audio recording, key, or .env file. Do not perform destructive tests on real data. If a permission prompt appears, leave the operation pending and continue independent work that stays within these boundaries. Do not try to bypass Qoder permission checks.

After each coherent implementation slice, run its focused checks and inspect the diff. Fix failures caused by your changes. Run frontend lint/build and backend tests at the end. For tests involving real microphone input, Windows notification delivery, or packaged installer behavior, perform a safe manual check if feasible and clearly distinguish that from automated results. Record any checks you cannot run; do not claim them as passing. If a technical blocker remains, finish safe independent parts, preserve the working state, and document the blocker rather than repeating the same failing action or broadening scope.

Stop when M1 is complete or you have exhausted safe, useful progress. Write docs/VEGA_M1_HANDOFF.md with the exact files changed, implemented examples, checks run and their results, migration/rollback steps, unresolved issues, and the next smallest milestone. Do not mark M2-M5 complete. Leave all changes reviewable in the working tree. Give a concise final report with truthful completion status.
```

If Qoder's Goal toggle is unavailable in your installed version, use the same prompt in Quest Agent mode. If a scheduled run starts from a different checkout, verify that the workspace contains these three documents before it edits anything.
