"""Exercise the real dashboard route without starting agent integrations."""
import ast
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient


def test_dashboard_shell():
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "backend/main.py").read_text())
    route = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                 and node.name == "dashboard_page")
    app = FastAPI()
    namespace = {"app": app, "HTMLResponse": HTMLResponse, "FRONTEND_DIR": root / "frontend"}
    exec(compile(ast.Module(body=[route], type_ignores=[]), "main-dashboard-route", "exec"), namespace)
    response = TestClient(app).get("/dashboard")
    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["content-type"].startswith("text/html")
    assert 'src="/static/js/dashboard.js"' in response.text
    assert 'id="chat-frame"' in response.text
    assert "SECRET_KEY" not in response.text


if __name__ == "__main__":
    test_dashboard_shell()
    print("Dashboard shell route: passed")
