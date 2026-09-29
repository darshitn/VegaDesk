# VEGA technology research and decisions

Checked 2026-09-22 against primary documentation and publisher model cards. Vendor features and quotas can change. Architectural choices below are recommendations, not benchmark results. Source labels are referenced by [VEGA_PLATFORM_PLAN.md](VEGA_PLATFORM_PLAN.md).

## R1 — Local runtime: retain Ollama; evaluate llama.cpp only for a concrete need

Ollama documents tool calling, structured outputs, and model residency controls. Use its existing integration first, with per-model capability tests. JSON-schema output still needs application validation; valid JSON does not establish a valid or authorized action.

- [Ollama tool calling](https://docs.ollama.com/capabilities/tool-calling)
- [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
- [Ollama FAQ: context, residency, concurrency](https://docs.ollama.com/faq)
- [llama.cpp server](https://github.com/ggml-org/llama.cpp/tree/master/tools/server)

Decision: no runtime rewrite now. Use one generation at a time and explicit context budgets. Consider llama.cpp later if measured memory/CPU behavior or packaging requirements justify managing a lower-level runtime. API similarity does not imply identical tool templates or stream formats.

## R2 — Qwen small local candidates

The Qwen publisher provides Qwen3.5 2B and 4B model cards, and Ollama lists the family. The 4B card specifies Apache-2.0. These are verified available candidates, not an assertion that they are the latest or best models in September 2026.

- [Qwen3.5 2B publisher model card](https://huggingface.co/Qwen/Qwen3.5-2B)
- [Qwen3.5 4B publisher model card](https://huggingface.co/Qwen/Qwen3.5-4B)
- [Ollama Qwen3.5 catalog](https://ollama.com/library/qwen3.5)

Decision: test a quantized 2B first, compare 4B with the same English task suite. Start with short context and text-only work. Choose an exact supported tag/quantization during installation and record it. Do not automatically pull the family's unqualified default tag, which may select a larger variant. Download size is not total inference memory. Published benchmark scores do not establish speed or reliability on the user's 4 GiB GPU.

## R3 — LFM2.5 as an efficiency candidate

Liquid AI's 1.2B Instruct card lists tool-use formats and quantized/ONNX distributions. It recommends extraction, RAG, and agentic tasks but explicitly discourages knowledge-intensive tasks and programming. Its license is LFM1.0, so do not describe every open-weight model as Apache/MIT or assume unrestricted redistribution.

- [LFM2.5 1.2B Instruct model card](https://huggingface.co/LiquidAI/LFM2.5-1.2B-Instruct)
- [Ollama LFM2.5 catalog](https://ollama.com/library/lfm2.5)

Decision: optional comparison for short structured tasks. Validate its runtime's tool format; do not execute Python-looking output as Python. Do not make it the main coding agent just because it is small.

## R4 — MCP for integration, stable internal contracts for ownership

MCP describes a host/client/server structure for connecting models to tools/resources. It is a useful interoperability boundary, not a replacement for VEGA's identity, persistent memory, executor, permissions, or job state.

- [MCP architecture](https://modelcontextprotocol.io/docs/learn/architecture)
- [MCP security guidance entry point](https://github.com/modelcontextprotocol/modelcontextprotocol/security)

Decision: build a small internal registry first; later add an MCP client adapter and one deliberately chosen read-only server. Discover only relevant tools for each request to avoid an enormous prompt. Pin the supported protocol/SDK version, enforce local scopes, and treat remote tool descriptions/results as data. Disabling an integration must leave the core functional. Never install arbitrary servers from model suggestions automatically.

## R5 — Personal knowledge with SQLite FTS5 first

FTS5 supplies full-text indexing and search within SQLite.

- [SQLite FTS5 documentation](https://www.sqlite.org/fts5.html)

Decision: start with exact names, phrase search, and ranked text retrieval over selected notes and project records. Check FTS5 availability in the packaged Python/SQLite build. Add embeddings only when a labeled set of semantic queries demonstrates missed retrieval; combine search methods instead of discarding exact matching. A vector store alone is not a reliable personal memory system.

## R6 — Document ingestion as a background capability

Docling documents parsing/conversion for varied document formats and support for layout and OCR.

- [Docling documentation](https://docling-project.github.io/docling/)

Decision: use straightforward text extraction for text PDFs and documents; invoke heavier layout/OCR processing only when needed. Keep selected-file hashes, page references, import progress, and cancellation. Benchmark one actual syllabus/notes set. Do not add a permanently resident OCR model or continuously rescan all folders.

## R7 — Desktop efficiency before a framework migration

Electron provides performance guidance covering profiling, expensive work, and loading strategies.

- [Electron performance guidance](https://www.electronjs.org/docs/latest/tutorial/performance)

Decision: measure the current process tree, then lazy-load optional 3D/camera modules, reduce idle work, and use a compact everyday surface. A framework migration does not remove the resource cost of transcription, inference, OCR, and animation. Reconsider the shell only after a profiler attributes an unacceptable share of the budget to it.

## R8 — Windows integration using existing system capabilities

Windows UI Automation exposes programmatic access to UI elements. PowerToys Command Palette demonstrates an extensible launcher supporting apps, commands, and search.

- [Microsoft UI Automation](https://learn.microsoft.com/en-us/windows/win32/winauto/entry-uiauto-win32)
- [PowerToys Command Palette](https://learn.microsoft.com/en-us/windows/powertoys/command-palette/overview)

Decision: use a command-palette interaction pattern, registered apps, and explicit targets. Later prefer application APIs or accessibility controls over screenshot coordinates. PowerToys integration is optional; do not require it to use VEGA or duplicate every Windows utility.

## R9 — Free cloud access is a limited enhancement

OpenRouter documents account/model rate limits, remaining free-request counters, and quota/credit error handling. Its catalog being publicly readable does not make inference keyless or unlimited. Cloud fallback must account for provider availability and the user's data-sharing choices.

- [OpenRouter limits and error handling](https://openrouter.ai/docs/api_reference/limits)

Decision: query account quota where supported; otherwise display remaining quota as unknown. Avoid hardcoded free-quota numbers: the browsed documentation rendered some numeric table cells incompletely, so this plan does not invent them. Handle 429, auth errors, and timeouts distinctly; honor Retry-After and stop within a request budget. Default paid budget is zero. Use a configured cloud provider only with permission to send that content. No account/key rotation to evade limits, no paid fallback, and no promise of uninterrupted frontier inference.

## R10 — Custom wake phrase later, measured rather than assumed

Sherpa-onnx documents customizable keyword spotting.

- [Sherpa-onnx keyword spotting](https://k2-fsa.github.io/sherpa/onnx/kws/index.html)

Decision: preserve the current working phrase and voice pipeline. Trial “Hey VEGA” on recordings at multiple distances with normal music/PC audio, measuring missed wakes and false activations per hour. This is an optional experiment after daily workflows are useful; renaming the wake phrase does not establish a smarter assistant.

## Research conclusions

The useful combination is modular tools, app-owned memory, a small local inference lane, bounded jobs, and measurable personal workflows. None requires a permanently running swarm of agents or paid orchestration. Avoid chasing announcements until a candidate beats the current choice on VEGA's own tests. The architecture preserves the user's records and permissions even when a model/provider is replaced.

Not performed: local model benchmark/download, cloud inference/account verification, license legal review, MCP server installation, CPU/RAM hardware refresh, or full Electron acceptance. These are implementation/validation work, not research claims.
