# Token Efficiency

## Request Flow

Jarvis follows the smallest reliable path:

`DIRECT -> SIMPLE -> TOOL -> AUTONOMOUS`

`backend.ai.deterministic` handles supported status queries without an LLM. The
zero-token planner in `backend.ai.execution` classifies all other requests and
selects agent level, tool domains, memory use, reasoning effort, and step limit.
The existing model router then chooses deterministic, local fast/general/strong,
or cloud execution. Authentication, actor scopes, tool permissions, and dispatch
guards remain authoritative.

## Dynamic Context

- SIMPLE requests receive no tool schemas, no memory, `off` reasoning, and one step.
- TOOL requests receive only matching domains, normally up to six steps.
- AUTONOMOUS requests may receive delegation/reflection tools and up to twelve steps.
- Unknown action verbs use the permitted full toolset for compatibility.
- Recognized but unavailable domains fail closed instead of exposing unrelated tools.
- Memory is injected only for memory-related intent and is capped at 2,000 estimated tokens.
- Browser and headless tool results use the same bounded compaction path.
- Conversation compression is local and deterministic; original persisted chat data is not deleted.

## Model Routing And Budgets

The existing `MODEL_ROUTING_MODE` and local Fast/General/Strong assignments remain
in force. Smart complexity thresholds, daily cloud-token budgets, and monthly
cloud-cost budgets are persisted in settings and enforced before cloud calls.
Usage telemetry stores route, model, token counts, latency, fallback, and estimated
cost in SQLite. It never stores prompts or tool-result contents.

## Reproducible Benchmark

Run:

```bash
python tests/token_efficiency_benchmark.py
```

Method: `ceil(UTF-8 characters / 4)` for static system prompt and tool schemas;
no provider call. The measured baseline was 22 tools, approximately 4,404 system
tokens plus 6,550 schema tokens (10,954 total).

| Scenario | Selected tools | Before | After | Reduction |
|---|---:|---:|---:|---:|
| Simple chat | 0 | 10,954 | 4,351 | 60.3% |
| Knowledge | 1 | 10,954 | 4,522 | 58.7% |
| Memory | 1 | 10,954 | 4,636 | 57.7% |
| File task | 2 | 10,954 | 4,671 | 57.4% |
| Email/calendar unavailable | 0 | 10,954 | 4,351 | 60.3% |
| Multi-tool | 6 | 10,954 | 6,498 | 40.7% |
| Subagent | 2 | 10,954 | 5,148 | 53.0% |

These are static estimates, not billing numbers. Live input/output tokens, latency,
cost, and success rate are recorded only from real provider responses and are not
invented by the benchmark.

Stage 2 verification adds the compact prompt and long-history scenario:

| Scenario | Original | Stage 1 | Stage 2 |
|---|---:|---:|---:|
| Simple chat | 10,954 | 4,351 | 222 |
| Knowledge | 10,954 | 4,522 | 4,522 |
| Memory | 10,954 | 4,636 | 4,636 |
| Single tool | 10,954 | 4,671 | 4,671 |
| Multi tool | 10,954 | 6,498 | 6,498 |
| Subagent | 10,954 | 5,148 | 5,148 |
| Long conversation | 17,452 | 10,849 | 6,720 |

All values are static character estimates produced by the benchmark. Provider
usage remains separate and is labelled `measured` only when returned by the
provider.

## Configuration

Relevant settings include `JARVIS_MODEL_ROUTING_MODE`, `JARVIS_LOCAL_FAST_MODEL`,
`JARVIS_LOCAL_GENERAL_MODEL`, `JARVIS_LOCAL_STRONG_MODEL`,
`JARVIS_DAILY_CLOUD_TOKEN_BUDGET`, and `JARVIS_MONTHLY_CLOUD_COST_BUDGET`.
Zero means unlimited for the two budgets.

## Stage 2

- Plain-language chat uses a small security-preserving system prompt, zero tool
  schemas, no memory lookup, and one model step.
- Long histories are compacted locally into a bounded 1,200-token session
  summary plus recent turns. Compression performs no additional provider call.
- Knowledge retrieval dynamically selects 3-5 results, removes weak and
  near-duplicate chunks, and caps retrieved text at 1,400 estimated tokens.
- Delegated tasks receive a bounded 1,200-token task package; returned text is
  capped before it enters the parent context.
- Every routed LLM request records component estimates and enforces a 32,000
  estimated-token ceiling. Provider token counters are marked `measured`;
  dependency-free fallbacks are marked `estimated`.

Estimates use `ceil(characters / 4)` and are capacity indicators, not billing
figures. Live provider latency, cache usage, and billed tokens must come from an
actual deployment and are never synthesized by the offline benchmark.
