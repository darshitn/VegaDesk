# VEGA P1 Handoff — Shared Intelligence Boundary

Scope: let VEGA change models **without rewriting its tools or losing current
functionality**. A paraphrased task / timer / reminder / focus request that the
deterministic parser cannot handle is now interpreted by the configured model,
**validated against one tool registry**, and executed through the **same existing
executor and receipts** the offline path uses. Exact offline commands stay
immediate and never touch a provider. One action per request. No automatic model
download, no paid request, no private-data-to-cloud fallback, no new server /
orchestration framework / vector DB. M2a voice pipeline was **not** reimplemented;
the dashboard scroll fix was **kept**.

Status: **complete and test-covered.** Backend **178 passed** (was 137 at M2a;
+41 new `test_model_lane.py` cases incl. parametrized expansions, and 2 migrated
`test_api.py` read-only-tool cases). Frontend **19 Node tests** pass (+1 new
execution-mode persistence case). Lint **0 errors** (17 pre-existing voice-hook
warnings, unchanged). Build **exit 0**. `git diff --check` **clean** (only
LF→CRLF notices). Live server `/health` 200; live `/chat` deterministic path
returns `executionMode:"deterministic"` grounded in the local DB.

**The key demonstration is covered by a test, not just prose:**
`test_same_task_via_three_paths_one_executor` sends the *same* task through the
deterministic path, a **mocked Ollama** proposal, and a **mocked Gemini**
proposal, and asserts all three run through **one validator/executor** and
produce equivalent records (3 tasks, identical text + deadline) with
authoritative receipts. `test_deterministic_command_works_right_after_provider_failure`
proves a provider failure does **not** block the next offline timer command.

---

## The P1 request path

```
POST /chat
  0. dispatcher.dispatch_command        -> deterministic, offline, ZERO provider calls
  1. system_actions.check_fast_path      -> deterministic (open/launch intents)
  2. model_lane.run_model_turn           -> shared intelligence boundary:
        provider gateway (Ollama | Gemini)   normalized text + ToolProposal[] + usage + typed errors
          -> context_builder                  versioned system prompt + bounded history + only exposed tool schemas
          -> tool_registry.validate_proposal  strict schema/date/duration/target validation (rejects unknown/extra/malformed)
          -> tools.execute_intent             the SAME existing executor, date validation, and ActionReceipt
        final user message derives from the executor receipt, never the model's own claim
```

`/api/assistant/command` is **unchanged** — it still calls only the deterministic
dispatcher (no model lane), so its contract is byte-for-byte compatible.

---

## Files changed

### New — backend
- **`providers/base.py`** (189 lines) — the gateway contract. `DEFAULT_DEADLINE_S=75`,
  `QUEUE_TIMEOUT_S=10`, `TRANSIENT_KINDS=("timeout","unavailable")`. Typed errors:
  `ProviderUnavailable / ProviderTimeout / ProviderAuth / ProviderQuota(retry_after) /
  ProviderContextLimit / ProviderMalformed`. Normalized `ToolProposal(name,args)`,
  `ProviderResponse(text, proposals, prompt_tokens, completion_tokens, model)`,
  `Capabilities(local, tools, structured_output, vision, context_window, reserved_output,
  data_policy)`. `redact_secrets(text)` strips `GEMINI_API_KEY` / `OPENROUTER_KEY` /
  `VEGA_RADAR_OPENROUTER_KEY` values plus `Bearer …` / `api_key=…` patterns.
  `BaseProvider._slot` is a **class-level `Semaphore(1)`** → one active generation
  process-wide with a bounded queue; `generate()` = one attempt + **at most one**
  retry for transient kinds while time remains, and **surfaces `retry_after` instead
  of sleeping** past the deadline. Executed mutations are never retried.
- **`providers/ollama.py`** (131) — `OllamaProvider` from `OLLAMA_MODEL` /
  `OLLAMA_BASE_URL` / `OLLAMA_CONTEXT_WINDOW` (8192) / `OLLAMA_RESERVED_OUTPUT` (1024).
  Sends `options:{num_predict, temperature}` with `temperature` from
  `OLLAMA_TEMPERATURE` (default **0** for the deterministic command lane). HTTP
  401/403→Auth, 429→Quota(retry_after from header), **404→Unavailable (model not
  installed — never auto-downloads)**, 5xx→Unavailable, context errors→ContextLimit.
  Parses `message.tool_calls` into `ToolProposal[]`.
- **`providers/gemini.py`** (145) — `GeminiProvider` (`gemini-2.5-flash`,
  `GEMINI_API_KEY`, ctx 32768, reserved 2048, `data_policy="user_configured_cloud"`).
  `_classify(exc)` maps SDK/HTTP errors to the same typed errors. Builds
  `contents` + `types.Tool(function_declarations=…)`, parses `response.function_calls`.
  Empty text + no calls → `ProviderMalformed`.
- **`providers/__init__.py`** (26) — re-exports the contract + both providers
  (flat-import / relative-import fallback).
- **`tool_registry.py`** (439) — **single source of truth.** `REGISTRY_VERSION="1.0"`,
  13 entries (`create_task, list_tasks, set_task_completed, start_timer, cancel_timer,
  create_reminder, snooze_reminder, start_focus_session, end_focus_session,
  get_ai_radar_digest, refresh_ai_radar(expose_to_model=False), open_app, open_website`),
  each with stable id/version, JSON schema, read|write classification, bounds, and a
  handler that calls the existing executor. `model_tool_ids()` = the 12 exposed.
  `schemas_for_provider(kind)` generates **"openai"** (full) and **"gemini"**
  (`_strip_for_gemini` removes `additionalProperties`/`min*`/`max*`) from the *same*
  registry. `_validate_against_schema` is strict and dependency-free (rejects unknown
  tools, extra properties, wrong types, out-of-enum/range). Dates/durations resolve
  through the authoritative `timeutil` clock (`_resolve_instant` / `_resolve_duration`);
  ambiguous or past instants return a clarification, never a guess.
  `request_fingerprint(message,source)=sha1[:24]`. `execute_proposal(...)` uses the
  **one stable request/action key** (client `idempotencyKey` OR fingerprint, suffixed
  with the tool name); a duplicate `ActionReceipt` key yields `replayed=True` and no
  second entity. Read-only intents (`list_tasks`) produce **no** ActionReceipt row.
- **`context_builder.py`** (116) — `SYSTEM_PROMPT_VERSION="vega-p1.1"`,
  `MAX_REQUEST_CHARS=3200`, `HISTORY_SHARE=0.5`, `estimate_tokens=ceil(chars/4)`
  (conservative, **labeled `estimated:True`** — no tokenizer dependency). Versioned
  identity prompt + current local time + 6 rules (one tool call; pass the user's own
  date phrase; ground `list_tasks`; never claim success; never print JSON; keep short).
  `build_messages(...)` trims history newest-first to the input/output budget and
  **never truncates the current request** — an oversized request raises
  `ValueError("request_too_large")` → honest clarification. Local DB content is never
  packed into a provider request.
- **`model_lane.py`** (178) — the orchestrator. `VALID_PROVIDERS=("ollama","gemini")`,
  cached `get_provider(name)` + `reset_providers()` (test hook). `_friendly_provider_error`
  maps each typed error to a concise honest message + `_LOCAL_COMMAND_HINT`
  ("…still work offline…"), with credentials redacted. `_result(...)` is one
  consistent return shape (`response`, `receipt`, `clarification`, `executionMode`).
  `_sanitize_text` refuses to show/speak raw JSON, fenced tool-call blocks, leaked
  `<|tool_call|>` / `<|im_start|>` template tokens, or python-call prose
  (`create_task({...})`) — a leaked proposal is a non-answer and is **never executed**.
- **`tests/test_model_lane.py`** (627, 41 cases) — see Acceptance tests below.

### Edited — backend
- **`main.py`** — removed the old inline Ollama/Gemini branches, `_get_genai_client`,
  `_chat_action_response`, `_try_parse_tool_json`, `_sanitize_content`, `_TASK_WINDOWS`,
  and `list_my_tasks` (≈327 lines of duplicated provider/business logic). `/chat` now
  runs dispatcher → fast-path → `model_lane.run_model_turn`. `_chat_response` adds the
  additive `opened` flag. `LLM_PROVIDER=os.getenv("LLM_PROVIDER","gemini")`.
  `ChatRequest` / `Message` / `CommandRequest` schemas unchanged.
- **`tests/test_api.py`** — the two read-only-task-tool tests migrated from the removed
  `main.list_my_tasks(...)` to `tools.execute_intent(db, clock, "list_tasks", {...},
  source="model")` (asserting `read_only is True` and no ActionReceipt row).

### Edited — frontend
- **`src/hooks/useChat.js`** — reads `data.executionMode` and keeps it only if it is one
  of `deterministic|local|cloud|none`.
- **`src/lib/chatStore.js`** — `VALID_MODES` whitelist; `sanitizeMessage` persists `mode`
  only when valid (drops anything else, alongside the existing role/field stripping).
- **`src/components/AIBrain.jsx`** — small `MODE_LABELS` badge
  (`offline · instant` / `local model` / `cloud model`) on assistant messages that carry
  a valid mode. No raw tokens/JSON/stacks in chat.
- **`tests/chatStore.test.js`** — +1 case: a valid mode is kept, `evil`/`42` are dropped.

---

## Before → after

| | Before P1 | After P1 |
|---|---|---|
| Paraphrased request the parser can't handle | Provider-specific ad-hoc code in `main.py`; JSON-ish prose could be shown/spoken | One gateway → registry validation → existing executor; leaked prose is refused, never executed |
| Switching model/provider | Edit provider branches in `main.py` | **Edit env only** (`LLM_PROVIDER` / `OLLAMA_MODEL` / `GEMINI_MODEL`); tool handlers untouched — covered by `test_switching_provider_needs_no_tool_handler_edits` |
| Same request via Ollama vs Gemini | Two separate code paths | One registry, one executor, equivalent receipts (three-path test) |
| Provider down / quota / timeout | Risk of lost turn or confusing error | Bounded, honest message + local-command hint, **history preserved**, next offline command unaffected |
| Replayed request / provider retry | Possible duplicate entity | One stable request/action key → one entity, one logical action (`replayed=True`) |
| Execution provenance in UI | None | `executionMode` badge (deterministic/local/cloud) |

---

## Commands and exact results

```
backend:  python -m pytest -q                     -> 178 passed, 1 warning in 22.57s
          python -m pytest tests/test_model_lane.py -q -> 41 passed
frontend: node --test                              -> tests 19  pass 19  fail 0
          npx oxlint .                             -> Found 17 warnings and 0 errors (19 files)
          npm run build                            -> built, EXIT=0
repo:     git diff --check                         -> EXIT=0 (LF→CRLF notices only)
live:     GET  /health                             -> 200
          POST /chat {"message":"list my tasks"}   -> executionMode=deterministic, opened=False,
                                                      response="2 task(s) open: #4 Scholarship, #5 Data Check"
```

### Live local-model paraphrase evaluation (temp DB, mocked effects, real Ollama)

6 genuine paraphrases the deterministic parser does **not** handle
(`parser_handles=False` for all 6 — verified). Nothing touched the real `jarvis.db`,
launched a real app, or downloaded a model.

**`qwen3.5:2b` @ temperature 0** (installed; the tech-research R2 recommended candidate):

```
ACCURACY (executed intent == expected & succeeded): 4/6
LATENCY  min=2.78s  max=20.28s  mean=7.51s
[OK ] create_task        20.28s  "submit lab record" -> deadline 2026-09-25T12:30:00Z (Fri 6pm IST)
[OK ] create_reminder     7.43s  "call the bank"     -> 2026-09-22T03:30:00Z (tomorrow 9am IST)
[MISS] start_timer        2.78s  honest clarification, NOTHING executed (see limitations)
[OK ] list_tasks          3.01s  read_only=True, grounded in local DB, no receipt row
[OK ] start_focus_session 5.70s  "thesis" 45 min -> planned end 2026-09-21T05:15Z
[MISS] set_task_completed 5.86s  model chose read-only list_tasks instead; NO mutation
```

**`phi4-mini:latest`** (the currently configured `OLLAMA_MODEL`), same harness:

```
ACCURACY: 0/6    LATENCY min=0.78s max=3.31s mean=1.81s
```
phi4-mini free-texts malformed tool calls (raw JSON / `<|tool_call|>` / python-call
prose) under the full P1 payload (system prompt + 12 tool schemas). Confirmed via
three probe scripts that it only emits clean tool calls with a bare/simple prompt —
this is a **model tool-calling limitation, not a gateway bug**. The gateway executed
**none** of the malformed output (safety verified) and `_sanitize_text` turned it into
an honest non-answer. At temp 0, `qwen2.5:3b-instruct` also produced clean native
tool calls (~47s) and `qwen3.5:2b` (~20s).

---

## Compatibility / migration notes

- **Public contracts preserved.** `/chat` and `/api/assistant/command` request/response
  shapes are backward compatible; `executionMode` and `opened` are **additive** fields.
  `/api/assistant/command` still never invokes a model.
- **No `.env` was edited.** The user's configured `OLLAMA_MODEL=phi4-mini:latest` is
  untouched. The eval forced `qwen3.5:2b` via an in-process env override only.
- **No schema/migration change** in P1 (the registry reuses M1 models + ActionReceipt).
- **No new runtime dependency**; schema validation and token estimation are
  dependency-free (estimates are explicitly labeled `estimated`).
- Removed dead code (`main.list_my_tasks` etc.) — the only in-repo callers were the two
  migrated tests.

---

## Resource measurements

- Single-generation semaphore: one active local generation, bounded queue
  (`QUEUE_TIMEOUT_S=10`); `test_one_active_generation_bounded_queue` covers it.
- Total inference deadline `DEFAULT_DEADLINE_S=75`; at most one bounded retry.
- Live latency (qwen3.5:2b, cold-ish): mean 7.51s, max 20.28s for a create_task with
  date resolution. phi4-mini: mean 1.81s (but 0/6 usable).
- Context budget: `estimate_tokens=ceil(chars/4)`, output reserved
  (`OLLAMA_RESERVED_OUTPUT=1024`, `GEMINI_RESERVED_OUTPUT=2048`), history trimmed
  newest-first to `HISTORY_SHARE=0.5` of the window.

---

## Unverified paths (honest limits)

- **Live cloud Gemini** was **not** exercised end-to-end (no paid request / no key
  acquisition in this milestone). It is covered by **mocked** proposals + typed-error
  tests only. Mocks do not establish live cloud accuracy.
- **Narrow-width / pixel dashboard inspection** was not possible in this environment:
  the in-app browser reported a hidden viewport (screenshot unavailable) and JS
  injection is blocked by the classifier (not bypassed). A **structural** snapshot at
  the running `:5173` confirmed both scroll regions ("Assistant and productivity" +
  "System and feeds"), all Productivity Hub panels, telemetry, feeds, and AI Radar
  render with no clipping. Visual responsive check at laptop vs narrow widths remains
  **pending** a human-attended browser.
- **Live microphone** wake-word path unchanged from M2a (still pending human speech).

---

## Known limitations / next smallest step

1. **Recommended (user action, one line in `.env`):** set `OLLAMA_MODEL=qwen3.5:2b`
   (already installed, clean native tool calls at temp 0, ~7.5s mean). phi4-mini cannot
   reliably emit structured tool calls under the P1 payload. **I did not edit `.env`.**
2. **Word-number durations** ("twenty minutes") are not parsed by `_resolve_duration`
   (numeric only) → honest clarification, nothing executed. Smallest follow-up: accept
   a small word→int map in the registry's duration resolver, or let the model emit
   `duration_seconds`. Out of strict P1 scope; safe as-is.
3. **Small-model tool selection**: qwen3.5:2b occasionally picks a read-only tool
   (`list_tasks`) when a write was intended (`set_task_completed`). Safe (no unintended
   mutation) but a larger model (qwen2.5:7b-instruct, installed) should reduce it.

**After P1 → P2 (explicitly out of current scope):** "Resume my work" + session closure
with a registered project, persisted next action, and a compact Today view.

---

## Acceptance tests (map to the milestone criteria)

- Exact commands cause zero provider calls — `test_exact_commands_never_touch_a_provider`.
- Mocked Ollama + mocked Gemini for the same request → same executor, equivalent
  records/receipts — `test_same_task_via_three_paths_one_executor`.
- Replayed request / provider retry → one entity, one logical action —
  idempotency tests (client key + fingerprint).
- Invalid/unknown/extra args, missing target, past/ambiguous date, malformed output,
  multi-action → no unintended writes — rejection parametrize +
  `test_multi_action_proposal_executes_nothing` + `test_json_like_prose_is_never_executed_or_shown`.
- 429/auth/timeout/connection failure bounded, conversation retained, next
  deterministic command unaffected — `test_provider_failures_are_honest_and_stateless`,
  `test_deterministic_command_works_right_after_provider_failure`.
- Oversized history obeys budget; current request + tool pairing preserved; oversized
  request → clarification — context-budget tests.
- Switching provider/model config needs no handler edits —
  `test_switching_provider_needs_no_tool_handler_edits`.
- Same schema generated per provider from one registry —
  `test_registry_schemas_are_equivalent_across_providers`.
- App/site effects mocked; tests never open real apps or modify real data —
  `open_app`/`open_website` monkeypatched; temp DBs throughout.
- Credentials redacted from errors/logs — `test_credentials_are_redacted_from_errors`,
  `redact_secrets` unit test.

Working tree left reviewable. No commits, pushes, resets, or deployments were made.
