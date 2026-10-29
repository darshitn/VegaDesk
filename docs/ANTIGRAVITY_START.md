# Antigravity: current run and resume guide

## Current checkpoint — 29 October 2026

Active work is voice reliability and the **Hey Vega** wake phrase; see [voice work](VOICE_QUALITY_AND_HEY_VEGA.md) and [recording assessment](VOICE_VALIDATION_2026-10-28.md). The Colab notebook is prepared but unexecuted, and no custom Vega wake model has been trained or accepted. Existing Jarvis remains the default until an actual evaluated artifact is configured.

The last full backend run was **445 passed, 13 failed**. The remaining failures also reproduced using the original voice module; this is not a release-ready or all-green checkpoint. Local Ollama work is deferred: HTTP redirects, launcher/readiness timeout alignment and hostname validation still need correction. Do not treat initial loopback URL validation as a complete local-only guarantee. GPU Whisper inference failed because `cublas64_12.dll` was missing/unloadable. Preserve recordings, credentials, personal data and existing work.

## Historical agent report — 5 October 2026

The report below records the earlier agent's implementation claims and targeted checks, not current blanket acceptance. The current checkpoint above takes precedence.
- **Strict Loopback Endpoint Security:** `validate_loopback_url()` in `backend/providers/ollama.py` validates that `OLLAMA_BASE_URL` targets a local loopback interface (`localhost`, `127.0.0.1`, `::1`). Non-loopback addresses (remote hosts, LAN IPs, cloud metadata endpoints) are rejected fail-closed.
- **Truthful Readiness Probes:** `check_ollama_readiness()` inspects local `/api/tags` with a bounded timeout without downloading models or generating tokens. Reports `ready` (model installed), `missing_model` (service online but model not downloaded), or `unavailable` (service offline).
- **Zero Cloud Fallback:** When running in beta mode (`VEGA_PROFILE=beta`), cloud API keys remain scrubbed from memory. If Ollama is offline or times out (bounded to 30.0s), VEGA returns a friendly offline explanation reminding the user that deterministic commands still work offline. No cloud fallback is ever attempted.
- **Deterministic Priority:** Deterministic natural language commands (tasks, timers, reminders, focus, workspaces) are evaluated and resolved first before consulting the local model, ensuring zero latency and zero model dependency for core productivity.
- **Action Execution via Standard Pipeline:** When Ollama proposes tools (e.g., `create_task`), proposals execute through `tool_registry` -> `policy.py` -> `executor` -> `ActionReceipt`, creating durable SQLite records and immediately triggering reactive UI refreshes via `publishProductivityUpdate`.
- **Owner-Reported Evidence (Desktop Voice Interruption):** The owner verified physical desktop voice interruption: speaking the wake phrase "Hey Jarvis" while VEGA was speaking cancelled TTS output immediately, un-ducked the audio stream, and successfully entered listening mode for the owner's fresh command.
- **Automated Verification:**
  - `npm test --prefix frontend`: **81 passed**, 0 failed.
  - Targeted backend suites (`pytest`): **62 passed**, 1 warning (34 parser, 11 voice, 10 beta profile, 7 beta ollama; corrected arithmetic from the original report's 72).
  - Frontend linter (`oxlint`): **0 errors**, 25 warnings.
  - `git diff --check`: passed (0 whitespace errors).

---

## Exact Owner Launch & Testing Commands

Execute from workspace root (`d:\Projects\Jarvis_Dashboard\jarvis-dashboard`) in PowerShell:

```powershell
# 1. Idempotently prepare isolated synthetic test database
npm run seed:beta

# 2. Option A — Ordinary Beta (Deterministic-only baseline, mic off)
npm run dev:beta

# 3. Option B — Voice Acceptance (Deterministic-only, local wake-word & Whisper mic active)
npm run dev:beta:voice

# 4. Option C — Local AI Beta (Loopback Ollama conversation & tool execution, mic off)
npm run dev:beta:ollama

# 5. Option D — Combined Local AI + Voice (Loopback Ollama + local wake-word & mic)
npm run dev:beta:ollama:voice
```

---

## Evidence Boundary: Automated Mocks vs Actual Host Desktop

1. **Automated Mocks (`node --test` & `pytest`):**
   - Verified loopback URL validation regex and IP parsing.
   - Verified that missing Ollama or connection errors fail closed without contacting cloud providers.
   - Verified that tool proposals from mocked Ollama responses generate SQLite receipts and execution records.
   - Verified that deterministic commands are intercepted before Ollama is called.
2. **Owner-Reported Desktop Evidence:**
   - Spoken "Hey Jarvis" interruption during Web Speech TTS playback verified working on owner's hardware.
3. **Actual Host Desktop Limitations (Unverified by Mocks):**
   - Real Ollama performance depends on the host machine having Ollama installed, running on `localhost:11434`, and an installed model (e.g. `ollama pull llama3`).
   - If Ollama is not installed or running, the UI reports truthful service unavailability with zero side effects.
   - Microphone wake-word detection latency and acoustic echo limits on laptop speakers remain subject to physical hardware.

---

## Current references

- [Project Specification](../PROJECT.md)
- [System Architecture](../ARCHITECTURE.md)
- [Development Roadmap](../ROADMAP.md)
- [Task Tracking](../TASKS.md)
- [Beta acceptance checklist](BETA_ACCEPTANCE_CHECKLIST.md)
- [Implementation log](IMPLEMENTATION_LOG.md)
- [Product reality report](VEGA_PROJECT_REALITY_REPORT_2026-10-01.md)
- [Architecture brief](VEGA_AGENTOS_BRIEF.md)
