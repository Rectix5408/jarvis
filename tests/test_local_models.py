# SPDX-License-Identifier: Apache-2.0
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI, Header, HTTPException
from backend.local_models import LocalModels, ModelError, make_router, runtime_url


class Config:
    def __init__(self):
        self.profiles = []
        self.active_profile_id = None
        self.MODEL_ROUTING_MODE = "cloud"
        self.LOCAL_MODEL = ""

    def create_profile(self, data):
        profile = {**data, "id": str(len(self.profiles) + 1)}
        self.profiles.append(profile)
        return profile

    def activate_profile(self, identifier):
        self.active_profile_id = identifier
        return True

    def save_global_settings(self, values):
        if "model_routing_mode" in values:
            self.MODEL_ROUTING_MODE = values["model_routing_mode"]
        if "local_model" in values:
            self.LOCAL_MODEL = values["local_model"]


class LocalModelsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.calls = []
        self.events = []
        self.remote = False
        self.bad_pull = False
        self.empty_answer = False
        self.service = LocalModels(Path(self.temp.name) / "local_ai", "http://ollama:11434", self.temp.name,
                                   transport=httpx.MockTransport(self.runtime), audit=lambda *args: self.events.append(args))
        self.service.start()

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    def runtime(self, request):
        self.calls.append(request)
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "test-fixture"})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:4b", "size": 2500}]})
        if path == "/api/show":
            return httpx.Response(200, json={"remote_host": "cloud.example"} if self.remote else {"capabilities": ["completion", "tools"], "system": "not public"})
        if path == "/api/chat":
            return httpx.Response(200, json={"done": True, "message": {"content": "" if self.empty_answer else "OK"}, "eval_count": 1, "prompt_eval_count": 5})
        if path == "/api/pull":
            items = [{"status": "pulling manifest"}, {"status": "pulling layer", "total": 100, "completed": 30}, {"status": "verifying sha256 digest"}]
            if not self.bad_pull:
                items.append({"status": "success"})
            return httpx.Response(200, content="\n".join(json.dumps(item) for item in items))
        if path == "/api/delete":
            return httpx.Response(200)
        return httpx.Response(404)

    async def test_status_and_details(self):
        status = await self.service.status()
        self.assertTrue(status["online"])
        self.assertEqual(status["models"][0]["name"], "qwen3:4b")
        self.assertGreater(status["hardware"]["ram_total"], 0)
        self.assertIsNone(status["hardware"]["gpu"])
        self.assertNotIn("system", await self.service.details("qwen3:4b"))

    async def test_offline_honest(self):
        self.service.transport = httpx.MockTransport(lambda request: httpx.Response(503))
        self.assertFalse((await self.service.status())["online"])

    async def test_pull_persists_and_tests(self):
        with patch.object(self.service, "hardware", return_value={"disk": {"free": 10_000_000_000}}):
            job = await self.service.pull("qwen3:4b", "admin")
        await asyncio.gather(*self.service.tasks)
        row = self.service.jobs()[0]
        self.assertEqual(row["id"], job["id"])
        self.assertEqual(row["status"], "READY")
        self.assertEqual(self.events[-1], ("admin", "model_installed", "qwen3:4b"))
        reopened = LocalModels(self.service.directory, self.service.url)
        reopened.start()
        self.assertEqual(reopened.jobs()[0]["status"], "READY")
        self.assertIn("/api/chat", [request.url.path for request in self.calls])

    async def test_incomplete_stream_is_failed(self):
        self.bad_pull = True
        with patch.object(self.service, "hardware", return_value={"disk": {"free": 10_000_000_000}}):
            await self.service.pull("qwen3:4b", "admin")
        await asyncio.gather(*self.service.tasks)
        self.assertEqual(self.service.jobs()[0]["status"], "FAILED")
        self.assertNotIn("/api/chat", [request.url.path for request in self.calls])

    async def test_restart_recovery(self):
        with self.service._db() as db:
            db.execute("INSERT INTO downloads(id,model,owner,status,detail,created,updated) VALUES('old','qwen3:4b','admin','DOWNLOADING','download',0,0)")
        restarted = LocalModels(self.service.directory, self.service.url)
        restarted.start()
        self.assertEqual(restarted.jobs()[0]["status"], "FAILED")
        self.assertIn("neu gestartet", restarted.jobs()[0]["detail"])

    async def test_install_guards(self):
        for hardware in ({"disk": None}, {"disk": {"free": 1}}):
            with patch.object(self.service, "hardware", return_value=hardware), self.assertRaises(ModelError):
                await self.service.pull("qwen3:4b", "admin")
        for model in ("qwen3:cloud", "../../etc/passwd", "model; rm -rf /", "example.com/model"):
            with self.assertRaises(ModelError):
                await self.service.pull(model, "admin")
        self.assertEqual(self.calls, [])

    async def test_cloud_model_never_inferred(self):
        self.remote = True
        with self.assertRaises(ModelError):
            await self.service.test("qwen3:4b")
        self.assertEqual([request.url.path for request in self.calls], ["/api/show"])

    async def test_test_requires_actual_answer(self):
        self.empty_answer = True
        with self.assertRaises(ModelError):
            await self.service.test("qwen3:4b")

    async def test_delete_guards_and_audit(self):
        with self.assertRaises(ModelError):
            await self.service.delete("qwen3:4b", "admin", lambda name: True)
        self.assertEqual(self.calls, [])
        await self.service.delete("qwen3:4b", "admin", lambda name: False)
        self.assertEqual(self.calls[0].method, "DELETE")
        self.assertEqual(self.events[-1][1], "model_deleted")

    async def test_redirect_not_followed(self):
        self.service.transport = httpx.MockTransport(lambda request: httpx.Response(302, headers={"location": "https://cloud.example"}))
        with self.assertRaises(ModelError):
            await self.service.test("qwen3:4b")

    async def test_admin_endpoints_and_activation(self):
        async def require_admin(authorization: str = Header(default="")):
            if authorization != "Bearer admin-fixture":
                raise HTTPException(403)
            return "admin"
        config = Config()
        app = FastAPI()
        app.include_router(make_router(self.service, require_admin, config))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for path in ("status", "downloads", "catalog"):
                self.assertEqual((await client.get("/api/local-ai/" + path)).status_code, 403)
            for path in ("details", "pull", "test", "delete", "activate"):
                self.assertEqual((await client.post("/api/local-ai/" + path, json={"model": "qwen3:4b"})).status_code, 403)
            self.assertEqual(self.calls, [])
            response = await client.post("/api/local-ai/activate", json={"model": "qwen3:4b"}, headers={"Authorization": "Bearer admin-fixture"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(config.LOCAL_MODEL, "qwen3:4b")
            self.assertEqual(config.profiles, [])
            self.assertIsNone(config.active_profile_id)
            route = await client.post("/api/local-ai/routing", json={"mode": "local_only", "local_model": "qwen3:4b"}, headers={"Authorization": "Bearer admin-fixture"})
            self.assertEqual(route.status_code, 200, route.text)
            self.assertEqual(config.MODEL_ROUTING_MODE, "local_only")

    def test_url_constraints(self):
        for url in ("https://api.openai.com", "http://169.254.169.254", "http://0.0.0.0", "http://user:password@localhost", "http://localhost/api"):
            with self.assertRaises(ModelError):
                runtime_url(url)
        self.assertEqual(runtime_url("http://ollama:11434/"), "http://ollama:11434")


if __name__ == "__main__":
    unittest.main()
