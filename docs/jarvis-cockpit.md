# Jarvis Cockpit

The `/dashboard` workspace now starts in Jarvis mode. It reuses the existing
authenticated chat, agent, tool permissions, profile ACLs and knowledge APIs.
No new LLM provider, speech provider or background task engine is introduced.

## Conversation

- Open the assistant from the sidebar or Jarvis view. Desktop widths of at
  least 1100px use a non-modal conversation dock beside the core. Smaller
  screens use an accessible modal dialog. Close or Escape dismisses it.
- The existing chat microphone requires browser speech recognition support
  and microphone permission. HTTPS is needed on a remote deployment.
- Speech output remains opt-in using the existing chat speaker toggle and
  voice selector. Existing server TTS is not guaranteed to be offline.
- The core reflects actual chat events: listening, agent working, audio
  playing, idle and errors. It does not measure audio volume or imply that
  microphone recording is active without the recognition start event.
- The same-origin frame sends only an enumerated activity value, never
  conversation contents, tokens or commands. The dashboard checks origin,
  frame identity and authenticated state before accepting it.
- Existing chat tool approvals, stop controls and task execution are retained.
  There is no always-listening wake word or autonomous background recording.

## Knowledge

- Group nodes and edges represent actual file-group assignments from the
  permission-scoped `/api/wissen/files` response, not inferred semantic links.
- Files cluster around their first group. Additional group memberships remain
  connected. Group filtering affects both the list and graph; name search
  includes every accessible file, not just the rendered geometry sample.
- The graph renders at most 350 file nodes for performance, explicitly noted
  when applicable. This does not limit stored files or searchable results.
- Group labels can be toggled. Select a rendered node or list item and use
  the focus control to center it. Reset returns to the current mode's view.
- Unauthorized or expired responses clear previously displayed knowledge.
- Motion can be paused; reduced-motion preferences disable automatic motion.

## Verification

With a backend, open `/dashboard` after signing in. For static preview only:

```sh
python3 tests/preview_dashboard.py --port 8767
```

The static preview has no live API, speech synthesis or model connection.
Browser tests use fixtures only inside the test runner:

```sh
DASHBOARD_URL=http://127.0.0.1:8767 node tests/test_dashboard_ui.cjs
node tests/test_chat_release_ui.js
```

Dependencies: Playwright, Chromium, pngjs for the dashboard suite; jsdom for
the chat suite. Optional environment variables `DASHBOARD_NODE_MODULES`,
`PLAYWRIGHT_BROWSERS_PATH` and `JSDOM_PATH` point to external installations.

Real microphone capture, live TTS, external LLM calls and remote server
deployment still require an authenticated running backend and a working
model profile. Visual changes cannot repair an unreachable Anthropic API.
