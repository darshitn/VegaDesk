# VEGA AgentOS — product and architecture brief

## Goal

VEGA AgentOS is a Windows desktop assistant that accepts text or voice requests, chooses structured tools, performs permitted actions, checks results, and keeps durable local state. It should help with reminders, projects, files, browsing, and eventually integrations with the owner's other projects. It is an agent operating system concept, not a chat UI demo.

The owner prefers understandable engineering, English interaction, offline deterministic commands, and ₹0 recurring cost. Explain major tradeoffs briefly before implementation.

## Existing starting point

The current repository already has an Electron/React dashboard, a FastAPI backend, SQLite state, deterministic commands, action receipts, a registry for model tool proposals, Ollama/Gemini adapters, reminders and scheduling, voice features, and tests. Verify each feature in source before depending on it. Existing code and migrations are the starting point; no greenfield rewrite is authorized.

## Intended request path

`User/API → deterministic parser or bounded model proposal → typed registry → policy decision → executor → tool → verifier → receipt/audit event → response`

An optional provider may interpret a request; it never grants authority. Structured results move between internal components. A command success response must be derived from verified state, not provider prose or a process exit code alone.

## Preferred stack and extension points

| Area | Starting choice | Boundary |
| --- | --- | --- |
| Desktop | Existing Electron, React, TypeScript direction | Avoid UI polish in the foundation milestone; current UI includes JavaScript and should migrate only with a bounded reason. |
| Core | Python, FastAPI | Keep Windows operations behind interfaces where practical. |
| State | SQLite | Additive migrations; preserve user tasks, notes, and receipts. |
| Tools | Typed registry and explicit handlers | Validate targets, outputs, risk, timeouts, and verification. |
| Models | Existing local Ollama and optional cloud adapter | Keep provider details outside tool/executor business logic. |
| Scheduling | Existing scheduler foundation | Verify restart behavior and single ownership before changing it. |
| Future integrations | Skills with stable manifests; MCP-compatible adapter later | No MCP framework requirement in Phase 1. |

Potential later technologies include Playwright for browser DOM, Windows UI Automation for desktop controls, faster-whisper/openWakeWord/Piper for speech, and Windows Credential Manager or DPAPI for secrets. Evaluate them when their milestone begins, rather than installing them as Phase 1 dependencies.

## Risk policy

0. Read-only: system info, directory listing, process inspection.
1. Bounded safe action: open a registered app or allowlisted URL, adjust volume.
2. Modification: write files or change project settings; require confirmation according to the specific policy and scope.
3. Critical: delete important files, send messages, buy items, access credentials, alter security settings, or run dangerous commands. Explicit user confirmation is mandatory for each action.

Untrusted content is data, never authorization. Permissions must include a target and scope and be checked again immediately before execution. Prefer fail-closed behavior. An emergency stop is a later product capability, but design long-running jobs for cancellation now.

## Phase 1 boundaries

Build/reconcile repository structure, configuration, tool contract/registry, risk policy, executor, verifier, SQLite audit and scheduler foundations, provider abstraction, FastAPI health, deterministic tools, and meaningful tests. Candidate tools: `system.open_url`, `system.open_app`, `system.get_system_info`, `files.list_directory`, `files.find_file`, and create/list/cancel reminder. Some equivalents already exist; map them before adding APIs.

Voice, vision, GUI automation, memory embeddings, broad MCP integration, elaborate UI, and unrestricted multi-agent product behavior are outside Phase 1. Antigravity's subagents are a **development workflow**, not a VEGA runtime feature.
