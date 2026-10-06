# Local Model Runtime

## Architecture

`backend/local_models.py` manages the existing Ollama runtime through its HTTP
API. It never starts shell commands from web requests. The configured runtime
URL is validated as a local or private host, redirects are disabled, and every
endpoint uses the existing administrator dependency.

`HardwareInventory` reads CPU, memory, swap, disk, and best-effort GPU data
without installing tools. Unsupported values remain `null` or `unknown`; other
platforms do not fail when Linux `/proc` or `/sys` files are absent.

## Lifecycle And Safety

The runtime exposes installed and running models and supports load, unload,
health, and bounded response tests. Loading uses Ollama `keep_alive`; it never
kills or restarts the Ollama process. Lifecycle operations share one async lock.
With `LOCAL_MAX_LOADED_MODELS=1`, Jarvis unloads the current model before loading
the requested model.

Before loading, Jarvis reserves at least 20% of total RAM (25% by default).
Insufficient capacity returns `MODEL_INSUFFICIENT_MEMORY`. Download primitives
reserve at least 15% of the model filesystem (20% by default) and return
`MODEL_INSUFFICIENT_DISK`. Runtime measurements are dynamic.

```dotenv
JARVIS_OLLAMA_URL=http://127.0.0.1:11434
JARVIS_MODEL_DISK_PATH=/root/.ollama/models
JARVIS_LOCAL_FAST_MODEL=qwen3.5:2b
JARVIS_LOCAL_GENERAL_MODEL=qwen3.5:4b
JARVIS_LOCAL_STRONG_MODEL=
LOCAL_MAX_LOADED_MODELS=1
LOCAL_MEMORY_RESERVE_PERCENT=25
LOCAL_DISK_RESERVE_PERCENT=20
```

An empty Strong role means cloud fallback is required. Model roles come from
configuration, not the runtime implementation.

## API And Security

Admin-only read endpoints are `/api/local-ai/status`, `/hardware`, `/installed`,
and `/running`. Admin actions are `/load`, `/unload`, and `/test`. Model IDs are
strictly validated; arbitrary URLs, path traversal, runtime-host overrides, and
runtime credentials are not accepted. Error responses do not expose provider
payloads, filesystem internals, or network exception details.

## Registry And Compatibility

The curated registry normalizes runtime IDs, family, parameter and quantization
metadata, context size, disk/RAM estimates, architecture support, license,
source, capabilities, and recommended roles. Unknown metadata remains `null`;
it is not inferred from a model name. Compatibility combines this metadata with
the live hardware inventory and reports `compatible`, `warning`, `incompatible`,
or `unknown` plus stable reason codes such as `INSUFFICIENT_RAM`,
`INSUFFICIENT_DISK`, `GPU_REQUIRED`, and `RUNTIME_UNAVAILABLE`.

## Download Jobs And Recovery

Downloads use the existing SQLite database and the states `QUEUED`,
`VALIDATING`, `DOWNLOADING`, `VERIFYING`, `COMPLETED`, `FAILED`,
`CANCELLING`, and `CANCELLED`. Progress retains Ollama's layer byte counts;
when a total is unknown, percentage and byte total remain `null`. A completed
stream is verified through Ollama's model list and show APIs before the job is
marked complete. Digest storage records Ollama's reported value and is not an
independent cryptographic attestation.

On backend startup, interrupted mutating jobs become `FAILED` with
`RECOVERY_INTERRUPTED`; Jarvis never displays a guessed 100%. Cancel closes the
backend HTTP stream. Ollama has no per-pull cancellation endpoint, so a partial
runtime download may remain cached and the next status refresh is authoritative.
Cancel is idempotent and never stops the Ollama service.

## Delete, Roles, And Audit

Delete first confirms that Ollama manages the model, blocks assigned models and
active downloads, and unloads a running model through Ollama before deletion.
It never removes filesystem paths supplied by a client. FAST, GENERAL, and
STRONG assignments reuse persistent Jarvis settings and are serialized with
other lifecycle mutations. STRONG may explicitly be CLOUD (an empty local
assignment). Assignments require an installed, reachable, chat-capable model
that is not hardware-incompatible.

Download start/cancel/complete/failure, load, unload, test, delete, and role
changes emit bounded audit events containing only the actor, action, and model
or role identifier. Credentials, runtime URLs, tokens, and local paths are not
included.

## Deployment Profile

The current production profile is 4 AMD EPYC vCPUs, 7.8 GiB RAM, no swap, and
CPU-only execution. These values document one deployment and are not hardcoded.
For this profile use `qwen3.5:2b` as Fast, `qwen3.5:4b` as General, and no local
Strong model. Keep only one model loaded. Existing native Ollama at
`127.0.0.1:11434` remains the primary runtime.

## Web Model Manager

Administrators open `/models` to inspect live Ollama and host state, assign
FAST/GENERAL/STRONG roles, manage installed models, browse the curated registry,
and monitor recent download jobs. Hardware values and compatibility decisions
come from the backend; the browser does not infer capacity or invent progress.

Installed rows expose load, unload, bounded test, and safe delete actions.
Actions are enabled from the latest backend snapshot, while the backend remains
the final authorization and safety boundary. Role changes are only displayed
after confirmation; failed updates restore the previous selection. STRONG shows
Cloud when no local model is assigned.

Registry entries display `compatible`, `warning`, `incompatible`, or `unknown`
with translated reason codes. Downloads require confirmation. Known byte totals
use a determinate progress bar; unknown totals use an indeterminate bar and the
reported phase. Cancel requests wait for the reconciled backend state instead
of claiming immediate success.

Polling runs about every 1.5 seconds while a job is active and every 15 seconds
while idle. It stops when the page is hidden or left, aborts outstanding fetches,
and resumes with a full refresh. API error codes are mapped to bounded user
messages; raw runtime exceptions and secrets are never rendered. Native dialogs,
keyboard focus styles, text-plus-color status badges, ARIA labels, and responsive
card/row layouts support keyboard, desktop, tablet, and mobile operation.
