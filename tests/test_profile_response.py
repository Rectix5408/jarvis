"""Mocked HTTP tests: never consume Anthropic credits or read live settings."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import sys
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.profile_response_test import test_anthropic_response as anthropic_response_test

NativeClient = httpx.AsyncClient
KEY = "sk-ant-test-not-a-real-key"


class ResponseTests(unittest.TestCase):
    def invoke(self, status=200, body=None, key=KEY, model="claude-haiku-4-5-20251001", error=None):
        requests = []
        def handler(request):
            requests.append(request)
            if error:
                raise error
            return httpx.Response(status, json=body or {"content": [{"type": "text", "text": "OK"}],
                                                       "usage": {"input_tokens": 8, "output_tokens": 1}})
        def client(**kwargs):
            return NativeClient(transport=httpx.MockTransport(handler), **kwargs)
        with patch("backend.profile_response_test.httpx.AsyncClient", side_effect=client):
            result = asyncio.run(anthropic_response_test(key, model))
        return result, requests

    def test_bounded_real_response_protocol(self):
        import json
        result, requests = self.invoke()
        self.assertTrue(result["success"])
        request = requests[0]
        self.assertEqual(str(request.url), "https://api.anthropic.com/v1/messages")
        self.assertEqual(request.headers["x-api-key"], KEY)
        payload = json.loads(request.content)
        self.assertEqual(payload["max_tokens"], 32)
        self.assertNotIn("tools", payload)
        self.assertNotIn("thinking", payload)
        self.assertEqual(result["usage"]["output_tokens"], 1)

    def test_missing_inputs_do_not_send_requests(self):
        for key, model in [("", "model"), ("sk-***", "model"), (KEY, "")]:
            result, requests = self.invoke(key=key, model=model)
            self.assertFalse(result["success"])
            self.assertEqual(requests, [])

    def test_provider_errors_do_not_disclose_keys(self):
        for status in (400, 401, 403, 404, 429, 500, 302):
            result, requests = self.invoke(status=status, body={"error": {"message": KEY}})
            self.assertFalse(result["success"])
            self.assertNotIn(KEY, str(result))
            self.assertEqual(len(requests), 1)

    def test_response_and_transport_redaction(self):
        result, _ = self.invoke(body={"content": [{"type": "text", "text": KEY}]})
        self.assertNotIn(KEY, str(result))
        result, _ = self.invoke(error=httpx.ConnectError(KEY))
        self.assertFalse(result["success"])
        self.assertNotIn(KEY, str(result))

    def test_timeout_and_empty_response(self):
        result, _ = self.invoke(error=httpx.ReadTimeout("timeout"))
        self.assertFalse(result["success"])
        result, _ = self.invoke(body={"content": []})
        self.assertFalse(result["success"])

    def test_route_guard_and_explicit_confirmation(self):
        tree = ast.parse((ROOT / "backend/main.py").read_text())
        names = {"test_profile_response", "_caps_key", "_llm_models_url"}
        nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
        app = FastAPI()
        async def admin(request: Request):
            if request.headers.get("Authorization") != "Bearer test-admin":
                raise HTTPException(403)
            return "jarvis"
        namespace = dict(app=app, Request=Request, Depends=Depends, require_local_auth=admin,
                         JSONResponse=JSONResponse, httpx=httpx,
                         config=SimpleNamespace(profiles=[{"id": "p1", "provider": "anthropic", "api_key": KEY},
                                                          {"id": "p2", "provider": "openrouter", "api_key": "other-key"}]))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "profile-test-routes", "exec"), namespace)
        client = TestClient(app)
        payload = {"provider": "anthropic", "model": "test-model", "profile_id": "p1", "confirm_billable": True}
        with patch("backend.profile_response_test.test_anthropic_response", return_value={"success": True}) as test:
            self.assertEqual(client.post("/api/profiles/test-response", json=payload).status_code, 403)
            self.assertFalse(test.called)
            headers = {"Authorization": "Bearer test-admin"}
            self.assertEqual(client.post("/api/profiles/test-response", headers=headers, json={**payload, "confirm_billable": False}).status_code, 400)
            self.assertEqual(client.post("/api/profiles/test-response", headers=headers, json={**payload, "profile_id": "p2"}).status_code, 400)
            self.assertEqual(client.post("/api/profiles/test-response", headers=headers, json={**payload, "auth_method": "session"}).status_code, 400)
            self.assertFalse(test.called)
            response = client.post("/api/profiles/test-response", headers=headers, json=payload)
            self.assertTrue(response.json()["success"])
            test.assert_awaited_once_with(KEY, "test-model")
            self.assertNotIn(KEY, response.text)
        url = namespace["_llm_models_url"]
        for source in ("http://ollama:11434/v1", "http://ollama:11434/v1/", "http://ollama:11434/v1/chat/completions"):
            self.assertEqual(url(source), "http://ollama:11434/v1/models")
        self.assertEqual(url("http://host/v1/chat/completions?x=1"), "http://host/v1/models?x=1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
