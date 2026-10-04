# Token economy and local work

## Enable

Settings -> LLM profiles -> Ollama-Profil creates an unsaved local template:

- Provider: OpenAI compatible
- URL: `http://ollama:11434/v1/chat/completions`
- API key: empty
- Sparmodus: enabled
- Model: select an actually installed model with model discovery

This URL requires the existing `docker-compose.local-llm.yml` deployment.
It does not install Ollama or download a model by itself. See `local-models.md`
for deployment and model installation. Standalone host installations use a
different reachable hostname; localhost inside Jarvis means the Jarvis container.

Test the connection, save, and activate the profile in the user's chat. Existing
profile permissions remain in effect. A failed local request is reported as a
failure rather than silently routed to a paid provider.

## Economy behavior

- Opt-in per profile; existing profiles are unchanged until enabled.
- Concise-answer guidance retains the original system prompt, tool definitions,
  safety rules, approvals and required research.
- At most 2048 generated tokens per API request for OpenAI-compatible native and
  prompt tools, OpenRouter, Anthropic API and Gemini. A smaller global output
  limit still wins. Native reasoning also consumes this budget on providers that
  count it as output; long code or document generation may need normal mode.
- Anthropic high/max reasoning cannot silently increase this mode's output cap.
- Automatic history summaries start after at most 12 history entries, including
  before the next chat request. The two most recent complete user turns and their
  tool calls/results remain unchanged. A long individual turn is not cut merely
  to meet this threshold. Failed summaries preserve the whole original history.
- Summaries use the same provider as the running agent, with no tools. They cost
  an additional inference, and compression is lossy for older facts; they are
  not a guarantee of accuracy or savings for every task.
- Claude browser-session providers do not expose an API output cap. Use API-key
  authentication for API budgeting, not a browser-session cookie.

## Scope and limits

This is an output cap and history optimization, not a hard monetary ceiling or
an exact input-token budget. Multiple tool steps each make a separate request.
System prompts, tool schemas, knowledge retrieval and current-turn tool results
remain present. No tool permissions or security checks are removed for savings.

Selecting a local main profile does not rewrite role-specific profiles. Assign
local models to roles as well when their work should run locally. External tools,
cloud security classifiers, embeddings, image generation, browser speech
recognition and server TTS have their own configurations; this is not a promise
that every application subsystem runs offline.

Local inference avoids that provider's cloud token charges, but still consumes
RAM, disk, CPU/GPU and power. Model suitability and speed depend on the server's
RAM/GPU and on tool-calling support. No remote model was installed or tested by
this change, and no live profile or API key was modified.

## Tests

```sh
python3 tests/test_economy_mode.py
python3 tests/test_llm_tool_schema.py
node tests/test_profile_response_ui.cjs
```

Tests stub config persistence and model transports; they never call a paid API
or read live secrets. They cover local payload budgets, lack of cloud fallback,
Anthropic reasoning caps, complete tool turns, failed-summary preservation,
opt-in profile persistence and the website's local template.
