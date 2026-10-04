# Jarvis Workspace

## Changes

The shared theme now uses neutral charcoal surfaces and restrained teal accents.
Existing branding overrides remain authoritative. `frontend/css/workspace.css`
is loaded after legacy page styles by portal, login/settings, chat, user chat,
knowledge, support, email, SAP and documentation pages. The Outlook add-in only
inherits the updated theme tokens; its host-specific layout is unchanged.

Settings use a vertically scrollable section navigation on desktop and a
horizontally scrollable tab strip on mobile. Existing tab IDs, inline visibility
rules and event handlers are retained. Dashboard model names use compact,
wrapping monospace text. Its theme switch shares `jarvis_theme` with the other
pages and updates the Three.js scene background and file node colors.

No backend routes, authentication, API keys or server configuration were changed.
The model status remains the actual response from the existing model probe;
a design update does not repair a missing or unavailable model.

## Preview And Tests

Start a static preview (no real backend or login):

```bash
python tests/preview_dashboard.py --port 8767
```

The dashboard at `http://127.0.0.1:8767/dashboard` shows a disconnected state
without a real authenticated backend. The test fixtures below only exist inside
Playwright and are not inserted into the application.

Install test-only tools outside the repository:

```bash
npm install --prefix /tmp/jarvis-dashboard-test playwright pngjs
PLAYWRIGHT_BROWSERS_PATH=/tmp/jarvis-playwright-browsers /tmp/jarvis-dashboard-test/node_modules/.bin/playwright install chromium
```

Run with the static server still running:

```bash
PLAYWRIGHT_BROWSERS_PATH=/tmp/jarvis-playwright-browsers DASHBOARD_NODE_MODULES=/tmp/jarvis-dashboard-test/node_modules DASHBOARD_URL=http://127.0.0.1:8767 node tests/test_dashboard_ui.cjs
PLAYWRIGHT_BROWSERS_PATH=/tmp/jarvis-playwright-browsers DASHBOARD_NODE_MODULES=/tmp/jarvis-dashboard-test/node_modules DASHBOARD_URL=http://127.0.0.1:8767 node tests/test_workspace_ui.cjs
python tests/test_dashboard_route.py
python tests/test_license.py
```

Dashboard tests exercise its real JavaScript with mocked, authenticated API
responses: long names, profiles, 401/403 states, graph pixel content, movement,
pause, search, selection and light/dark preferences. Workspace tests use actual
page markup and CSS, with external legacy scripts disabled and portal API
fixtures, to verify 1440/390/320-pixel layouts, form navigation, retained controls
and branding. They do not constitute end-to-end voice or model integration tests.
Screenshots are generated under `/tmp/jarvis-dashboard-*` and
`/tmp/jarvis-workspace-*`.

## Deployment

Changes are local until committed and pushed to your fork and deployed on the
server. Rebuild the existing Jarvis source image using the same Compose files
as the installed service. Theme resource URLs were versioned to invalidate old
browser caches. Custom branding may intentionally override the new defaults.
