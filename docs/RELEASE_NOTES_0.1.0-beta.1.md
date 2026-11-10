# VEGA Desk 0.1.0-beta.1

A personal-use Windows beta for tasks, timers, reminders, focus sessions, notes and projects, with a sci-fi workspace and optional existing AI/voice integrations.

## Included

- A pinned first-run window, compact Productivity / Assistant navigation, readable Glass theme and explicit Quit control.
- Local SQLite state, timer/reminder scheduling, action receipts and safe registered app/site launch.
- Session-verified frontend writes, isolated beta profile and truthful backend readiness.
- Server-confirmed voice settings, ordinary-wake chunk retention, interruption acknowledgement and stale-result rejection.
- Optional Ollama and Gemini adapters behind the existing tool/schema/policy contracts.

## Three-minute walkthrough

1. Start VEGA Desk and complete the setup wizard with Offline Only.
2. Type `add task: try my daily workspace` and check the task appears.
3. Type `timer 15s` and keep the window visible until the alert appears.
4. Open Settings, use Quit VEGA, relaunch and check your task persisted.

## Optional voice

Background microphone capture is disabled on a normal packaged launch. The explicit `--voice` launch argument requests it. Existing local openWakeWord and faster-whisper assets are required; models and personal recordings are excluded from this package. The current pretrained wake phrase is Hey Jarvis. Hey Vega training remains paused. Hardware accuracy and native voice latency need an owner-run check on the target machine.

## Current limits

- This is an unsigned personal beta. Windows may display an unknown-publisher notice.
- AI services are optional. Ordinary beta mode is deterministic-only; paid cloud calls require an explicit provider choice and credentials.
- An app-launch receipt records accepted initiation. It does not establish that the requested window rendered.
- Stopping speech cancels playback. It does not undo an action or cancel a backend job.
- Existing development data is not automatically imported into a newly installed profile.
- Physical tray behavior, hotkey restoration, Windows scaling and native notification clicks require target-machine acceptance.

The study card, adaptive daily planning, recall system and broader autonomous jobs are follow-up milestones. The React interface, provider boundary and typed action/tool contracts remain their extension points.

## Build evidence

Source tests at release preparation: 480 backend tests passed, 86 frontend tests passed. The build, artifact checksums and packaged smoke outcomes are recorded in the release verification record before publication.
