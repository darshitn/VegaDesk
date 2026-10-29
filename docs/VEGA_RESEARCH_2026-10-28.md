# VEGA: research and product decision

> Follow-up: the owner clarified a broader goal-aware desktop companion with proactive plans, widgets and learning support. [VEGA Personal OS plan](VEGA_PERSONAL_OS_PLAN.md) supersedes this report's product ordering and next-step recommendation. The model/runtime research remains reference material.

Research date: 28 October 2026. Status: proposal, not an implemented replacement.

## Decision in one paragraph

Build **VEGA Voice Pilot**: a voice-first Windows assistant that operates a defined set of apps, shows what it is doing, and verifies the result. Keep the existing Electron interface, local voice pipeline, productivity tools, SQLite receipts and policy boundary where they remain useful. Add a cancellable desktop job engine and evaluate an existing computer-control driver before writing Windows plumbing. Start by comparing **Cua Driver against a narrow pywinauto adapter**; use **Microsoft UFO** as the strongest Windows-specific reference and a candidate for broader orchestration later. Do not fork three complete assistants or train a foundation model. The recommendation is provisional until the same tasks run on this laptop.

The owner selected computer operation over a coding-first or study-first MVP. Software-building assistance remains a later application of the same system. Budget preference is free first, with optional INR100–300/month if demonstrably useful. This updates the earlier absolute-zero-cost preference; it does not enable paid calls automatically.

## What makes this worth building

A general chatbot clone has little advantage over the products the owner already uses. A personal assistant can offer something more specific: know the owner's approved workspaces, accept natural spoken requests, operate their actual apps, retain useful workflow preferences, and make progress observable and interruptible.

The distinctive interaction is: **say a goal → see the target and next action → watch the result → interrupt or correct it → reuse the successful workflow**. Example: “Open my project, put the documentation beside it, and start a focus session.” A later version could accept “Do my usual project setup,” using an owner-approved recipe with fresh target checks. This is a compelling personal product without claiming human-level general autonomy.

An attractive interface cannot compensate for unreliable actions. Conversely, adding another framework does not create intelligence by itself. The product should be evaluated on task success, time saved, interruptions handled, and false-success rate.

## Current foundation and gaps

The current checkout is `main`, HEAD `d13b6c3`, with existing uncommitted Ollama work. See [current status report](PROJECT_STATUS_2026-10-28.md) for the earlier source audit and exact test evidence. Preserve that work.

- Present: productivity tools, deterministic requests, app/site launch, local speech recognition, wake phrase, spoken replies, speech interruption, isolated beta profile, policy checks and receipts.
- Partial: optional local-model integration. The current audit identified redirect handling and readiness-timeout issues; these need correction before calling local-only behavior accepted.
- Missing for this vision: general Windows observation and interaction, reliable multi-step jobs, action cancellation, bounded browser control, permissions for broader writes, and native acceptance evidence for the new workflows.
- Important distinction: current voice interruption cancels speech playback. It does not establish that a running `/chat` request or computer action is cancelled. `frontend/src/hooks/useChat.js` and `frontend/src/lib/voiceInterruption.js` need a shared job cancellation contract before “stop” can mean stop operating the computer.
- Existing app launch verification truthfully reports accepted initiation, not a verified desktop window. New desktop actions need their own observations and postconditions.

Earlier historical documents contain inconsistent totals. Do not turn old test reports or the current research into release acceptance. No new model or third-party runtime was installed or benchmarked during this research.

## The “Jel, 100x faster” claim

The likely reference is **Jev**, developed by TypeSafe AI, whose founder Diogo Almeida describes prior work at OpenAI. Its “System One” approach returns typed decisions such as categories, scores and probabilities. It gives up free-form string generation. The dramatic speed and cost claims are vendor comparisons on those bounded decision workloads, not proof that Jev replaces a conversational or coding model. [TypeSafe introduction](https://typesafe.ai/blog/introducing-system-one-models-and-jev)

Potential VEGA use: classify a request or select a route. Not suitable as its sole conversational brain. A syntactically valid decision can still be wrong; model confidence cannot authorize an action. For the first MVP, deterministic rules are sufficient for simple routing, so Jev is interesting rather than required. [Typed API capabilities](https://docs.typesafe.ai/introduction)

## Open-source projects: what to reuse

These are distinct layers, not a shopping list to install together. Licenses describe the named component at research time; pin a revision and inspect bundled dependencies before distribution.

| Candidate | Useful contribution | Limits and decision |
|---|---|---|
| [Cua Driver](https://github.com/trycua/cua) | MIT desktop/browser driver, SDK and MCP routes; Windows support | First driver candidate. Its broader Spaces and Driver Bench products have different terms. Do not assume the whole ecosystem is MIT. No need for hosted Spaces in this MVP. |
| [Microsoft UFO](https://github.com/microsoft/UFO/blob/main/ufo/README.md) | MIT Windows-oriented host/app-agent architecture, UI Automation and native operations | Strong reference for extending across apps. A complete framework adds model calls, configuration and ownership complexity. Evaluate components before a full integration. |
| [pywinauto](https://github.com/pywinauto/pywinauto) | BSD-3-Clause Python Win32/UIA control | Small baseline for a few supported apps. VEGA must supply policies, identity checks, cancellation and verification. |
| [Playwright MCP](https://github.com/microsoft/playwright-mcp) | Apache-2.0 browser control using structured accessibility snapshots | Prefer browser structure to screenshot guessing. Start with a separate synthetic browser profile. MCP exposes tools; it is not a security boundary. |
| [Goose](https://github.com/aaif-goose/goose) | Apache-2.0 general agent, desktop/CLI/API and extensions | Best full-runtime alternative to evaluate if VEGA orchestration becomes excessive. Rust runtime adds another stack. Its [custom distribution guide](https://github.com/aaif-goose/goose/blob/main/CUSTOM_DISTROS.md) explicitly supports a custom interface. |
| [Hermes Agent desktop](https://github.com/NousResearch/hermes-agent/blob/main/apps/desktop/README.md) | Existing Windows desktop assistant with shared memory, skills, tools and voice | Strong full-product alternative if personal conversation and memory become the priority. A polished app does not itself prove dependable control of the owner's apps. |
| [OpenClaw](https://github.com/openclaw/openclaw) | Personal assistant gateway and messaging-channel ecosystem | Reconsider if cross-device messaging becomes central. Adds unnecessary surface for the current desktop-first slice. |
| [OpenHands SDK](https://github.com/OpenHands/software-agent-sdk) | MIT SDK for software-agent workers | Later specialist for building projects; not the first Windows voice controller. SDK license is not a blanket statement about all OpenHands products. |
| [Pipecat](https://github.com/pipecat-ai/pipecat), [LiveKit Agents](https://github.com/livekit/agents) | Established real-time voice orchestration | Consider if measured turn-taking problems justify replacing the present pipeline. Avoid rebuilding working voice and desktop control simultaneously. |
| [Graphiti](https://github.com/getzep/graphiti), [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | Temporal memory and resumable workflows respectively | Useful ideas, but begin with SQLite jobs and explicit preferences. A graph database and several orchestration frameworks are not MVP requirements. |

Cua's own platform matrix limits background operation for some controls and elevated targets. “Works while you keep working” is app-dependent. Prefer a driver that returns a refusal over one that silently sends input to the wrong window. [Cua Windows/browser support matrix](https://cua.ai/docs/cua-driver/concepts/platform-support)

UFO documentation is inconsistent about whether picture-in-picture desktop support is available or coming soon. Do not make it an MVP dependency. Its stable architectural lesson is combining structured Windows state with native and GUI operations. [UFO architecture](https://github.com/microsoft/UFO/blob/main/documents/docs/ufo2/overview.md)

## Models and architectures worth evaluating

Machine observed during this session: AMD Ryzen 7 7435HS, approximately 23.7 GB usable RAM, RTX 3050 A Laptop GPU with approximately 4 GB VRAM. These are hardware observations, not inference benchmarks.

| Direction | Why it matters | VEGA decision |
|---|---|---|
| [Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B) | Apache-2.0 small multimodal model | First local planning candidate, quantized with a short context. Benchmark structured tool selection and refusals, not only chat fluency. |
| [Gemma 4 E2B](https://huggingface.co/google/gemma-4-E2B-it) | Apache-2.0 edge-oriented model with multimodal inputs | Second local candidate. “Effective” parameters are not total resident weights; measure actual runtime memory. |
| [Nemotron 3 Nano 4B GGUF](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-GGUF) | Hybrid architecture, ready quantized artifacts | Reserve candidate if the first two miss latency goals. NVIDIA Open Model License requires artifact-specific review. |
| [Mercury 3](https://www.inceptionlabs.ai/blog/introducing-mercury-3) | Diffusion language generation rather than conventional token-by-token generation | Interesting inexpensive cloud text planner. Vendor throughput claims do not equal end-to-end desktop task latency or success. |
| [Gemini API](https://ai.google.dev/gemini-api/docs/pricing) | Free-tier and low-cost multimodal options | Optional difficult-task fallback after consent to transmit the specific context. Free quota is not guaranteed availability. |
| [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) | Small Apache-2.0 speech synthesis model | Candidate for a more consistent local voice after measuring current speech quality. Keep speech separate from action authorization. |
| [PersonaPlex-7B](https://huggingface.co/nvidia/personaplex-7b-v1), [Moshi](https://github.com/kyutai-labs/moshi) | Full-duplex speech research | Watchlist. Larger audio systems compete for this laptop's limited memory and do not solve desktop action correctness. |
| [V-JEPA 2](https://ai.meta.com/research/publications/v-jepa-2-self-supervised-video-models-enable-understanding-prediction-and-planning/) | Predictive visual representations and robotic planning research | Inspiring research direction, not a drop-in Windows assistant or general conversational model. |

Small models can plausibly parse bounded intents and produce short tool plans locally. That is an engineering hypothesis to test. Quantized weight size excludes context cache, vision encoders, runtime buffers, Windows graphics and speech workloads. Start with CPU speech recognition and one local language model; do not load three models concurrently. Large mixture-of-experts models can have low active parameter counts while still requiring large resident weights.

The most useful architecture change is **observe → choose a typed action → validate → execute → observe again**, with deterministic shortcuts for common commands. New model architectures improve parts of that loop, not the entire product automatically.

## Cost and existing subscriptions

Default mode has no metered API calls. Local compute still consumes electricity, memory and download bandwidth. No new subscription is required for the first driver proof.

Illustrative monthly text budget, using an assumed INR90/USD solely for planning, excluding tax, exchange spread, search, audio and hosting:

| Usage: 5 million input + 1 million output tokens | Published unit rates per million | Planning estimate |
|---|---|---|
| Mercury 3, regular price | USD0.20 input / USD0.70 output | USD1.70, about INR153 |
| Gemini 3.1 Flash-Lite | USD0.25 input / USD1.50 output | USD2.75, about INR248 |
| Gemini 3.5 Flash-Lite | USD0.30 input / USD2.50 output | USD4.00, about INR360 |

Sources: [Inception model pricing](https://docs.inceptionlabs.ai/get-started/models), [Google API pricing](https://ai.google.dev/gemini-api/docs/pricing). Mercury launch discounts are excluded. Gemini free-tier data handling differs from paid; evaluate that before uploading screen contents. Higher version numbers do not automatically offer the best budget fit.

These figures are scenarios, not usage forecasts. Long screen contexts, reasoning, retries, and continuous audio can exhaust INR300 quickly. Reserve a worst-case per-call allowance before dispatch, cap output and steps, and stop paid work before the ledger reaches the configured cap. Reconcile provider-reported usage; app estimates are not a substitute for provider billing controls.

Existing subscriptions may help through supported integrations. OpenAI now documents eligible ChatGPT-plan usage for open-source/local apps through an OAuth flow and [Codex app-server](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server); eligibility and quotas still need checking for this account and app. [Plan usage overview](https://developers.openai.com/siwc/token-sharing-open-source). Never extract session cookies or reuse secret tokens through unofficial endpoints.

“New Spark 1 billion tokens” is not enough to identify the exact offer or API entitlement. Antigravity app credits also cannot be assumed to be Gemini Developer API credit. Verify the named plan's supported interface before designing around either. No credentials were read for this research.

## A futuristic interface with a practical role

Two proposed surfaces:

1. **Pilot dock:** a compact summonable voice strip showing Listening, Planning, Acting, Needs you, or Stopped. Expands only when a target, result or decision needs attention.
2. **Control room:** current goal, selected app, short action sequence, result evidence and a visible Stop control. A small voice signature gives identity; useful space belongs to the work.

The active app and intended target should be visible before input is sent. Use subtle spatial layering, precise typography and restrained light, with distinct states and reduced-motion support. Avoid giant decorative orbs, fake activity, constant animation and unreadable glass. A screenshot must be labelled as an observation with a time, never implied to be a continuously live feed.

The accompanying interactive concept uses sample data and performs no microphone capture, model calls or computer actions. It is a direction to evaluate before changing the production interface.

## Next decision and stop condition

See [Voice Pilot MVP and first milestone](VEGA_VOICE_PILOT_MVP.md). Complete one bounded driver proof, then select the engine using native evidence. If a maintained runtime solves the chosen workflows with substantially less integration work and preserves the required controls, adopt it. Existing VEGA work is reusable material, not a reason to retain a worse architecture.

Do not keep adding features to avoid this comparison. The next valuable evidence is a spoken goal becoming a correctly verified Windows action, with cancellation working mid-job.
