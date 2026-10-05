# Repository Guidelines

## Project Structure & Module Organization

`backend/` contains the FastAPI server, agent runtime, provider integrations, AI routing, skills, and tools. Keep provider-neutral routing in `backend/ai/`; place tool implementations in `backend/tools/` and skill lifecycle code in `backend/skills/`. The browser UI lives in `frontend/` with page-specific JavaScript under `frontend/js/` and styles under `frontend/css/`. Deployment files are in `docker/`, `deploy/`, and the root Compose files. Tests live in `tests/`; persistent runtime state belongs in `data/` and must not be committed unless it is an intentional fixture.

## Build, Test, and Development Commands

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python run_jarvis.py
docker compose up -d --build
pytest -q tests/test_model_router.py
python tests/test_llm_tool_schema.py
node --check frontend/js/models.js
```

Use `run_jarvis.py` for local development and Compose for the integrated stack. Some legacy tests are executable scripts with their own exit handling; run those directly instead of collecting them with Pytest. For local Ollama, layer `docker-compose.local-llm.yml` onto the normal Compose configuration.

## Coding Style & Naming Conventions

Use four-space indentation in Python and two spaces in frontend files. Follow existing type hints and `snake_case` for Python functions, variables, and tests. Classes use `PascalCase`; JavaScript variables and functions use `camelCase`. Prefer small, focused modules and existing abstractions. Use SPDX headers on new source and test files. Run `git diff --check` and Python/JavaScript syntax checks before handing off changes.

## Testing Guidelines

Name Pytest files `test_*.py` and browser tests `test_*.js` or `test_*.cjs`. Add focused regression coverage for routing, permissions, persistence, and provider payloads. Never weaken security assertions to make a feature pass. External APIs should be mocked; tests must not consume paid tokens or modify production data.

## Commit & Pull Request Guidelines

Recent history uses short messages such as `sync`; prefer a concise imperative summary that names the behavior changed. Pull requests should explain motivation, affected runtime paths, configuration changes, and test results. Include screenshots for visible UI changes and call out migrations, security impact, or untested platform-specific behavior.

## Security & Configuration

Keep API keys in `.env`, never `.env.example`, logs, fixtures, or frontend code. Preserve authentication, actor scopes, tool permissions, filesystem restrictions, and MCP guards. Never remove Docker volumes during routine updates.
