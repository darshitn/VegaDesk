# Antigravity next milestone — P1-C1 app launch safety

Open `D:\Projects\Jarvis_Dashboard\jarvis-dashboard` in Antigravity and paste:

```text
Continue VEGA AgentOS with Gemini 3.8 Flash. Read AGENTS.md, docs/VEGA_AGENTOS_BRIEF.md, docs/PHASE1_PLAN.md, and the latest docs/IMPLEMENTATION_LOG.md. Recheck Git status and preserve every existing change, .env, local database, and build artifact. Work on P1-C1 only: safe, truthful open_app execution. Do not build the full Risk 2/3 confirmation UI/API in this run.

Use a read-only subagent, if available, to inspect the Windows app catalog and launch paths for unsafe commands and false success reports. Keep one editing agent. Verify its claims against current code.

Trace open_app through /chat fast path, model proposals, system_actions.open_app, catalog discovery, and any direct callers. Source-review leads: backend/system_actions.py currently has a subprocess.Popen(..., shell=True) fallback for catalog commands, returns an "Opening ..." string immediately after launch calls, and backend/tool_registry.py still treats that string as success with receipt=None. First reproduce or disprove these issues using synthetic catalog entries and injected launchers. Do not execute real unknown apps or shell commands in tests.

Implement the smallest cohesive fix:
- Give app launch a typed target resolution, explicit Risk 1 policy decision, bounded executor, truthful initiation result, and one authoritative action receipt for success, denial, and failure. Preserve the UI's opened behavior only when launch initiation was accepted; label actual window/process observation as unverified unless checked.
- Remove user-influenced shell=True execution. For legacy catalog entries that require a shell or cannot be represented safely as an executable path plus arguments, refuse them with a useful explanation rather than trying to sanitize a shell string. Keep only allowlisted/registered launch targets; deny interpreters and ambiguous fuzzy matches.
- Route both fast-path and model open_app requests through the same policy/executor/audit path. No result may be considered successful because its text starts with "Opening ". Handle retries with a key bound to the exact action and resolved target; a reused key for a different action or target must not replay the wrong receipt. If this reveals a shared idempotency defect, fix it narrowly and add a regression test.
- Keep production user data untouched. Test with a temporary SQLite database, fake catalog, fake launcher/process outcomes, and no real desktop launches. Cover success, denial, launch failure, ambiguous match, replay, and the fast/model routes. Explain which evidence is launch initiation versus actual desktop verification.

Run targeted tests, then the full backend suite. Run frontend tests only if its contract changes. Run git diff --check and review the final diff for unrelated edits, secrets, and generated files. Update docs/IMPLEMENTATION_LOG.md with exact commands/results, mocked versus desktop evidence, remaining risks, and one next step. Stop after P1-C1; propose P1-C2 for a scoped confirmation contract only after mapping which current tools truly need Risk 2/3 approval. If a source-review lead is false, document evidence instead of changing it merely to match this prompt.
```
