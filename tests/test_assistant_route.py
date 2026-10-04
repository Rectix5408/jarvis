"""Test real route bodies without importing integration/startup side effects."""
import ast
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient


def test_assistant_uses_existing_chat():
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "backend/main.py").read_text())
    route = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "chat_page")
    app = FastAPI()
    namespace = {"app": app, "HTMLResponse": HTMLResponse, "FRONTEND_DIR": root / "frontend"}
    exec(compile(ast.Module(body=[route], type_ignores=[]), "assistant-route", "exec"), namespace)
    client = TestClient(app)
    assistant = client.get("/assistant")
    assert assistant.status_code == 200
    assert assistant.text == client.get("/chat").text
    assert "no-store" in assistant.headers["cache-control"]
    for retained in ['id="msg-input"', 'id="chat-sidebar"', 'id="btn-mic"', 'id="ctx-indicator"', '/static/js/chat.js', '/static/js/shell.js', '/static/js/assistant.js']:
        assert retained in assistant.text
    assert "SECRET_KEY" not in assistant.text


if __name__ == "__main__":
    test_assistant_uses_existing_chat()
    print("Assistant route: shared chat template and no-store passed")
