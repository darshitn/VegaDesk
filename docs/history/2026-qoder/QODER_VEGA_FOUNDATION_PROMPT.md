# Qoder assignment: P1 shared intelligence boundary

Use Qwen 3.8 Max in Qoder as the implementation model already selected by the user. Keep one editing agent on this working tree. This is an execution prompt for one milestone, not permission to implement the entire platform roadmap.

Copy the following prompt into Qoder:

```text
Work in D:\Projects\Jarvis_Dashboard\jarvis-dashboard.

Read applicable AGENTS.md, docs/VEGA_PLATFORM_PLAN.md, docs/VEGA_TECH_RESEARCH.md,
docs/VEGA_POST_M1_HANDOFF.md, and docs/VEGA_M2A_HANDOFF.md. Verify current source:
historical handoffs are evidence leads, not proof. Preserve existing uncommitted
work, .env, jarvis.db, and backups. No commits, pushes, resets, deployments, or
mass rewrites. Use temporary DBs and mocked effects for tests. No automatic
model downloads, paid requests, new accounts, or API-key acquisition.

Implement ONLY P1: a shared intelligence boundary that lets VEGA change models
without rewriting its tools or losing current functionality. M2a already exists;
do not reimplement the voice pipeline. Keep the new dashboard scroll fix.

USER-VISIBLE OUTCOME
A paraphrased task/timer/reminder/focus request that the deterministic parser
cannot handle can be interpreted by the configured model, validated, and executed
through the same existing executor. Exact offline commands remain immediate.
If a model is missing, times out, or hits quota, no task/history is lost; VEGA
explains the limitation and offers a supported local command or clarification.
There is no automatic paid or private-data-to-cloud fallback.

1. BASELINE AND INTERFACES
Inspect main.py's Ollama/Gemini branches, command_parser.py, dispatcher.py,
tools.py, existing action receipts/idempotency, useChat, and API consumers.
Run a baseline before changing behavior. Add narrowly scoped modules such as
backend/providers/base.py, ollama.py, gemini.py, backend/tool_registry.py,
and backend/context_builder.py; adapt names to repository conventions.
Keep public /chat and /api/assistant/command contracts compatible.
Avoid introducing an orchestration framework, new server, or vector DB.

2. PROVIDER GATEWAY
Normalize text, proposed tool calls, usage (unknown if absent), and typed errors.
Keep the user's existing configured provider; make model IDs configurable and
publish clear capability flags. Normalize unavailable, timeout, auth, malformed
response, context-limit, and quota errors. At most one bounded inference retry,
within a total deadline; honor Retry-After or return retry_after without waiting
indefinitely. Do not fall back to cloud without an existing user setting allowing
that provider and data. If fallback is not allowed, retain the request and show
an honest result. Never retry executed mutations as part of provider recovery.
Maintain one active local generation with a bounded queue. Preserve cancellation
semantics where the transport supports them; do not claim cancelled server work
if only the client stopped waiting. Do not infer capabilities from brand names.

3. ONE TOOL REGISTRY AND EXECUTOR
Build provider schemas from one registry for existing safe productivity actions.
Use the existing executor and its date/parameter validation and receipts; do not
copy business logic into provider adapters. Inventory actual existing intent
names before coding. Keep app/site opening within their current bounded behavior.
Each entry includes stable ID/version, schema, read/write classification, bounds,
and handler. Reject unknown tools, extra properties, invalid dates, arbitrary
shell/program text, and ambiguous targets. Generate the same schema for each
provider from the registry. Runtime capability differences belong in adapters.
Avoid silently executing JSON-like assistant prose: require a supported structured
proposal and validate it. Add a tested compatibility path only if truly needed.

For P1 support one action per request. A multi-action proposal returns an honest
clarification without executing a partial list. Plans/jobs can come later.
Use one stable request/action key across provider retries and client replay.
Where date interpretation needs a timezone, pass the existing clock/timezone
context and let the executor remain authoritative. A provider cannot override
user-confirmation or scope requirements. Final action messages derive from the
executor receipt, never from an optimistic provider statement.

4. CONTEXT AND BUDGETS
Use a shared versioned system prompt plus bounded recent conversation and only
relevant tool schemas. Keep current model context configurable, reserve output
space, and retain tool-call/result pairs together. Prefer an available tokenizer;
otherwise use a conservative documented estimate and label it estimated.
Do not equate 20 messages with a token budget. Do not truncate the current user
request into a different command: ask for a shorter request when necessary.
Do not send all tasks, repositories, notes, or private files to a cloud model.
Task answers remain grounded in local database results. The source data stays
owned by VEGA, not a provider's conversation/session identifier.

5. TRUTHFUL USER FEEDBACK
Preserve chat history across errors and theme/hide transitions. Surface concise
provider-unavailable/quota messages and local command availability. Include a
small execution-mode field (deterministic/local/cloud) without cluttering chat
with raw tokens, JSON, or internal stacks. Redact credentials in errors/logs.
Unknown quota is unknown, not unlimited or zero. Core commands still work when
every model provider is disabled.

6. ACCEPTANCE TESTS
- Existing backend/frontend suites remain passing.
- Exact tasks/timers/reminders/focus commands cause zero provider calls.
- A mocked Ollama tool proposal and mocked Gemini proposal for the same request
  use the same executor and produce equivalent records and receipts.
- Replayed request/provider retry creates one entity and one logical action.
- Invalid/unknown/extra arguments, missing targets, past/ambiguous dates, malformed
  provider output, and multi-action proposals cause no unintended writes.
- 429/auth/timeout/connection failure is bounded, retains conversation, and does
  not disrupt an immediately following deterministic command.
- Oversized history obeys the input/output budget; the current request and tool
  pairing are preserved. Oversized current requests ask for clarification.
- Switching provider/model configuration requires no edits to tool handlers.
- App/site effects are mocked; tests never open real apps or modify real data.

Run backend tests, frontend tests, lint, build, and git diff --check. If a local
model is already installed, run a small live paraphrase evaluation against a temp
DB and report exact model/runtime/cases/latency. Otherwise record live inference
as pending; mocks do not establish model accuracy. Inspect the actual dashboard
at laptop and narrow window sizes if browser access works; record genuine limits.

STOP AND HANDOFF
Do not start project/subject schemas, PDF RAG, MCP, broad desktop automation,
new themes, cloud subscriptions, or a new agent framework in this milestone.
If provider capability blocks progress, finish the gateway/registry offline
contracts and report the precise limitation. Do not fabricate an unavailable
model's results. Do not weaken acceptance criteria to call the milestone done.
Write docs/VEGA_P1_HANDOFF.md with files changed, before/after behavior, commands
and exact results, compatibility/migration notes, resource measurements if any,
unverified paths, and the next smallest step. Leave a reviewable working tree.
```

## Morning review

Review the diff before accepting the result. The key demonstration is the same task arriving through three paths—deterministic, Ollama proposal, Gemini proposal—using one validator/executor and producing one authoritative receipt. A provider failure must not prevent the next offline timer command.

This milestone should not be called complete just because new abstractions exist. It needs the behavior above. After P1, implement P2's “Resume my work” and session closure with a registered project, persisted next action, and a compact Today view.
