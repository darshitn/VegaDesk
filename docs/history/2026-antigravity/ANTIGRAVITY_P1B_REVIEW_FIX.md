# Antigravity follow-up — reconcile P1-B before P1-C

Paste this into Gemini 3.8 Flash in Antigravity with `D:\Projects\Jarvis_Dashboard\jarvis-dashboard` open:

```text
Continue VEGA AgentOS. Read AGENTS.md, docs/VEGA_AGENTOS_BRIEF.md, docs/PHASE1_PLAN.md, and the latest docs/IMPLEMENTATION_LOG.md. Recheck Git status and preserve the current dirty baseline and all user data. Work on one bounded P1-B correction milestone; do not start the broad P1-C confirmation system yet.

First reproduce or disprove these source-review findings with focused tests:
1. backend/model_lane.py creates an idempotency action key only when entry.kind == "intent". open_website is a "system" tool with produces_receipt=True, so model-assisted retries appear to receive no key. Make URL actions obey the same replay contract without assigning keys to read-only tools. Test repeated model proposals with the same client key and a fake launcher.
2. backend/tool_registry.py has a fallback that invokes a monkeypatched open_website and derives success from an "Opening " string, returning no receipt. Remove or replace this production bypass; adapt old tests to inject the launch dependency at the executor boundary. Test that every system URL route uses policy, verification, and audit.
3. backend/tools.py handle_open_website calls execute_open_url (which writes a receipt) inside execute_intent (which writes another receipt). Test the direct executor route and make one logical request produce one authoritative receipt, including denial/failure, while preserving idempotency. Do not let one layer claim success based on a second layer's prose.
4. backend/verifier.py treats webbrowser.open_new_tab() returning True as "Browser successfully launched" and verified=True. That return confirms the launcher accepted the request, not that a page loaded or the expected window exists. Define truthful status/evidence: launch requested/accepted versus page/window verified. Do not report a real desktop success without a real desktop observation. Keep the Electron opened behavior compatible where possible and document any limitation.

Use a read-only reviewer subagent for the changed call paths and test gaps if available; keep one editing agent. Add focused tests for model and fast paths, failure/denial, replay, and receipt count. Use synthetic SQLite data and fake launchers; do not open real pages in automated tests. Run targeted tests, the full backend suite, frontend tests only if its contract changed, and git diff --check. Record exact outputs and any unavailable runtime in docs/IMPLEMENTATION_LOG.md. Distinguish mocked initiation from a manually observed browser page. Report changed files, decisions, test evidence, remaining risk, and one next step. If a finding is false, show source/test evidence instead of changing code merely to satisfy this prompt.
```
