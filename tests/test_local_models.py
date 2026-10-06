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
from backend.local_models import (CompatibilityEngine, HardwareInventory, LocalModels,
                                  ModelError, ModelRegistry, make_router, runtime_url)


class Config:
    def __init__(self):
        self.profiles = []
        self.active_profile_id = None
        self.MODEL_ROUTING_MODE = "cloud"
        self.LOCAL_MODEL = ""
        self.LOCAL_FAST_MODEL = ""
        self.LOCAL_GENERAL_MODEL = ""
        self.LOCAL_STRONG_MODEL = ""

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
        for key, attribute in (("local_fast_model", "LOCAL_FAST_MODEL"),
                               ("local_general_model", "LOCAL_GENERAL_MODEL"),
                               ("local_strong_model", "LOCAL_STRONG_MODEL")):
            if key in values:
                setattr(self, attribute, values[key])


class LocalModelsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.calls = []
        self.events = []
        self.remote = False
        self.bad_pull = False
        self.empty_answer = False
        self.running = []
        self.installed = {"qwen3:4b", "qwen3.5:2b", "qwen3.5:4b"}
        self.service = LocalModels(Path(self.temp.name) / "local_ai", "http://ollama:11434", self.temp.name,
                                   transport=httpx.MockTransport(self.runtime), audit=lambda *args: self.events.append(args),
                                   role_models={"fast": "qwen3.5:2b", "general": "qwen3.5:4b", "strong": ""})
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
            models = [
                {"name": "qwen3:4b", "size": 2_500_000_000, "digest": "sha256:test"},
                {"name": "qwen3.5:2b", "size": 2_700_000_000},
                {"name": "qwen3.5:4b", "size": 3_400_000_000},
            ]
            return httpx.Response(200, json={"models": [model for model in models if model["name"] in self.installed]})
        if path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": name} for name in self.running]})
        if path == "/api/show":
            return httpx.Response(200, json={"remote_host": "cloud.example"} if self.remote else {"capabilities": ["completion", "tools"], "system": "not public"})
        if path == "/api/chat":
            return httpx.Response(200, json={"done": True, "message": {"content": "" if self.empty_answer else "OK"}, "eval_count": 1, "prompt_eval_count": 5})
        if path == "/api/generate":
            body = json.loads(request.content)
            if body.get("keep_alive") == 0:
                self.running = [name for name in self.running if name != body["model"]]
            elif body["model"] not in self.running:
                self.running.append(body["model"])
            return httpx.Response(200, json={"done": True})
        if path == "/api/pull":
            items = [{"status": "pulling manifest"}, {"status": "pulling layer", "total": 100, "completed": 30}, {"status": "verifying sha256 digest"}]
            if not self.bad_pull:
                items.append({"status": "success"})
                self.installed.add(json.loads(request.content)["model"])
            return httpx.Response(200, content="\n".join(json.dumps(item) for item in items))
        if path == "/api/delete":
            return httpx.Response(200)
        return httpx.Response(404)

    async def test_status_and_details(self):
        status = await self.service.status()
        self.assertTrue(status["online"])
        self.assertEqual(status["models"][0]["name"], "qwen3:4b")
        self.assertGreater(status["hardware"]["ram_total"], 0)
        self.assertIn(status["hardware"]["gpu"]["type"], {"none", "intel", "amd", "nvidia"})
        self.assertEqual(status["runtime"], "ollama")
        self.assertEqual(status["version"], "test-fixture")
        self.assertNotIn("system", await self.service.details("qwen3:4b"))

    async def test_offline_honest(self):
        self.service.transport = httpx.MockTransport(lambda request: httpx.Response(503))
        self.assertFalse((await self.service.status())["online"])

    async def test_pull_persists_and_tests(self):
        self.installed.remove("qwen3:4b")
        with patch.object(self.service, "hardware", return_value={"disk": {"free": 10_000_000_000}}):
            job = await self.service.pull("qwen3:4b", "admin")
        await asyncio.gather(*tuple(self.service.tasks.values()))
        row = self.service.jobs()[0]
        self.assertEqual(row["id"], job["id"])
        self.assertEqual(row["status"], "COMPLETED")
        self.assertEqual(self.events[-1], ("admin", "MODEL_DOWNLOAD_COMPLETE", "qwen3:4b"))
        reopened = LocalModels(self.service.directory, self.service.url)
        reopened.start()
        self.assertEqual(reopened.jobs()[0]["status"], "COMPLETED")
        self.assertIn("/api/show", [request.url.path for request in self.calls])

    async def test_incomplete_stream_is_failed(self):
        self.bad_pull = True
        self.installed.remove("qwen3:4b")
        with patch.object(self.service, "hardware", return_value={"disk": {"free": 10_000_000_000}}):
            await self.service.pull("qwen3:4b", "admin")
        await asyncio.gather(*tuple(self.service.tasks.values()))
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
        self.installed.remove("qwen3:4b")
        for hardware in ({"disk": None}, {"disk": {"total": 100, "available": 1, "free": 1}}):
            with patch.object(self.service, "hardware", return_value=hardware), self.assertRaises(ModelError):
                await self.service.pull("qwen3:4b", "admin")
        for model in ("qwen3:cloud", "../../etc/passwd", "model; rm -rf /", "example.com/model"):
            with self.assertRaises(ModelError):
                await self.service.pull(model, "admin")
        self.assertFalse(any(request.url.path == "/api/pull" for request in self.calls))

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
        self.assertEqual(self.calls[-1].method, "DELETE")
        self.assertEqual(self.events[-1][1], "MODEL_DELETE")

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
            for path in ("status", "hardware", "installed", "running", "downloads", "catalog"):
                self.assertEqual((await client.get("/api/local-ai/" + path)).status_code, 403)
            for path in ("details", "pull", "test", "load", "unload", "delete", "activate"):
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
            tiered = await client.post("/api/local-ai/routing", json={
                "mode": "smart", "local_fast_model": "qwen3:4b",
                "local_general_model": "qwen3:4b", "local_strong_model": "qwen3:4b",
                "smart_local_complexity_limit": 0.75,
                "smart_tool_cloud_complexity": 0.45,
            }, headers={"Authorization": "Bearer admin-fixture"})
            self.assertEqual(tiered.status_code, 200, tiered.text)
            self.assertEqual(config.LOCAL_GENERAL_MODEL, "qwen3:4b")

    def test_url_constraints(self):
        for url in ("https://api.openai.com", "http://169.254.169.254", "http://0.0.0.0", "http://user:password@localhost", "http://localhost/api"):
            with self.assertRaises(ModelError):
                runtime_url(url)
        self.assertEqual(runtime_url("http://ollama:11434/"), "http://ollama:11434")

    async def test_lifecycle_and_single_model_switches(self):
        hardware = {"memory": {"total": 8_000_000_000, "available": 7_500_000_000}}
        with patch.object(self.service, "hardware", return_value=hardware):
            await self.service.load_model("qwen3.5:2b")
            self.assertEqual(self.running, ["qwen3.5:2b"])
            await self.service.load_model("qwen3.5:4b")
            self.assertEqual(self.running, ["qwen3.5:4b"])
            await self.service.load_model("qwen3.5:2b")
            self.assertEqual(self.running, ["qwen3.5:2b"])
            await self.service.unload_model("qwen3.5:2b")
        self.assertEqual(self.running, [])
        self.assertTrue(self.service.health["qwen3.5:2b"]["healthy"])

    async def test_parallel_lifecycle_requests_are_serialized(self):
        hardware = {"memory": {"total": 8_000_000_000, "available": 7_500_000_000}}
        with patch.object(self.service, "hardware", return_value=hardware):
            await asyncio.gather(self.service.load_model("qwen3.5:2b"),
                                 self.service.load_model("qwen3.5:4b"))
        self.assertEqual(len(self.running), 1)

    async def test_memory_and_disk_safety(self):
        with patch.object(self.service, "hardware", return_value={
                "memory": {"total": 8_000_000_000, "available": 7_000_000_000},
                "disk": {"total": 100_000_000_000, "available": 50_000_000_000}}):
            self.assertTrue(self.service.ensure_memory_capacity("qwen3.5:2b"))
            self.assertTrue(self.service.ensure_disk_capacity(3_000_000_000))
        with patch.object(self.service, "hardware", return_value={
                "memory": {"total": 8_000_000_000, "available": 4_000_000_000},
                "disk": {"total": 10_000_000_000, "available": 3_000_000_000}}):
            with self.assertRaises(ModelError) as memory_error:
                self.service.ensure_memory_capacity("qwen3.5:4b")
            self.assertEqual(memory_error.exception.code, "MODEL_INSUFFICIENT_MEMORY")
            with self.assertRaises(ModelError) as disk_error:
                self.service.ensure_disk_capacity(2_000_000_000)
            self.assertEqual(disk_error.exception.code, "MODEL_INSUFFICIENT_DISK")

    async def test_timeout_and_runtime_error_are_safe(self):
        def timeout(request):
            raise httpx.ReadTimeout("private runtime detail", request=request)
        self.service.transport = httpx.MockTransport(timeout)
        with self.assertRaisesRegex(ModelError, "Lokale Runtime nicht erreichbar"):
            await self.service.health_check()
        self.service.transport = httpx.MockTransport(lambda request: httpx.Response(500, json={"error": "private"}))
        with self.assertRaisesRegex(ModelError, "Lokale Runtime nicht erreichbar"):
            await self.service.health_check()

    async def test_admin_lifecycle_actions(self):
        async def require_admin(authorization: str = Header(default="")):
            if authorization != "Bearer admin-fixture":
                raise HTTPException(403)
            return "admin"
        app = FastAPI()
        app.include_router(make_router(self.service, require_admin, Config()))
        hardware = {"memory": {"total": 8_000_000_000, "available": 7_500_000_000}}
        with patch.object(self.service, "hardware", return_value=hardware):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                headers = {"Authorization": "Bearer admin-fixture"}
                loaded = await client.post("/api/local-ai/load", json={"model": "qwen3.5:2b"}, headers=headers)
                self.assertEqual(loaded.status_code, 200, loaded.text)
                unloaded = await client.post("/api/local-ai/unload", json={"model": "qwen3.5:2b"}, headers=headers)
                self.assertEqual(unloaded.status_code, 200, unloaded.text)
        self.assertEqual([event[1] for event in self.events[-2:]], ["MODEL_LOAD", "MODEL_UNLOAD"])

    async def test_invalid_lifecycle_model_never_reaches_runtime(self):
        for value in ("../../etc/passwd", "model;touch /tmp/pwned", "https://example.com/model"):
            with self.assertRaises(ModelError):
                await self.service.load_model(value)
        self.assertEqual(self.calls, [])

    async def test_cancel_is_idempotent(self):
        now = 1.0
        job_id = "a" * 32
        with self.service._db() as db:
            db.execute("INSERT INTO downloads(id,model,owner,status,detail,created,updated) VALUES(?,?,?,'DOWNLOADING','pull',?,?)",
                       (job_id, "qwen3.5:2b", "admin", now, now))
        self.service.tasks[job_id] = asyncio.create_task(asyncio.sleep(60))
        cancelled = await self.service.cancel(job_id, "admin")
        self.assertEqual(cancelled["state"], "CANCELLED")
        again = await self.service.cancel(job_id, "admin")
        self.assertEqual(again["state"], "CANCELLED")

    async def test_duplicate_pull_and_mutation_conflict(self):
        self.installed.remove("qwen3:4b")
        blocker = asyncio.Event()

        async def blocked_pull(*args):
            await blocker.wait()

        hardware = {"disk": {"total": 100_000_000_000, "available": 80_000_000_000}}
        with patch.object(self.service, "hardware", return_value=hardware), \
                patch.object(self.service, "_pull", side_effect=blocked_pull):
            job = await self.service.pull("qwen3:4b", "admin")
            with self.assertRaises(ModelError) as duplicate:
                await self.service.pull("qwen3:4b", "admin")
            self.assertEqual(duplicate.exception.code, "MODEL_DOWNLOAD_ACTIVE")
            await self.service.cancel(job["id"], "admin")

    async def test_delete_not_installed_running_and_active(self):
        self.installed.remove("qwen3:4b")
        with self.assertRaises(ModelError) as missing:
            await self.service.delete("qwen3:4b", "admin", lambda name: False)
        self.assertEqual(missing.exception.code, "MODEL_NOT_FOUND")
        self.installed.add("qwen3:4b")
        with self.service._db() as db:
            db.execute("INSERT INTO downloads(id,model,owner,status,detail,created,updated) VALUES(?,?,?,'DOWNLOADING','pull',1,1)",
                       ("b" * 32, "qwen3:4b", "admin"))
        with self.assertRaises(ModelError) as conflict:
            await self.service.delete("qwen3:4b", "admin", lambda name: False)
        self.assertEqual(conflict.exception.code, "MODEL_JOB_CONFLICT")
        self.service.update("b" * 32, "FAILED", "test cleanup")
        self.running.append("qwen3:4b")
        await self.service.delete("qwen3:4b", "admin", lambda name: False)
        self.assertNotIn("qwen3:4b", self.running)

    async def test_role_assignment_and_cloud_strong(self):
        async def require_admin(authorization: str = Header(default="")):
            if authorization != "Bearer admin-fixture":
                raise HTTPException(403)
            return "admin"
        config = Config()
        app = FastAPI()
        app.include_router(make_router(self.service, require_admin, config))
        hardware = {"architecture": "x86_64", "gpu": {"type": "none"},
                    "memory": {"total": 8_000_000_000, "available": 7_500_000_000},
                    "disk": {"total": 100_000_000_000, "available": 80_000_000_000}}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = {"Authorization": "Bearer admin-fixture"}
            with patch.object(self.service, "hardware", return_value=hardware):
                for role, model in (("fast", "qwen3.5:2b"), ("general", "qwen3.5:4b"), ("strong", "")):
                    response = await client.post("/api/local-ai/roles", json={"role": role, "model": model}, headers=headers)
                    self.assertEqual(response.status_code, 200, response.text)
                rejected = await client.post("/api/local-ai/roles", json={"role": "fast", "model": "qwen3:14b"}, headers=headers)
                self.assertEqual(rejected.status_code, 409)
        self.assertEqual(config.LOCAL_FAST_MODEL, "qwen3.5:2b")
        self.assertEqual(config.LOCAL_GENERAL_MODEL, "qwen3.5:4b")
        self.assertEqual(config.LOCAL_STRONG_MODEL, "")


class HardwareInventoryTests(unittest.TestCase):
    def test_linux_inventory_without_gpu(self):
        inventory = HardwareInventory(tempfile.gettempdir())
        with patch.object(HardwareInventory, "_cpu_info", return_value=("AMD EPYC", {"avx", "avx2"})), \
                patch.object(HardwareInventory, "_gpu_info", return_value={"type": "none", "devices": [], "vram_total": None}), \
                patch("backend.local_models.sys.platform", "linux"):
            result = inventory.collect()
        self.assertEqual(result["platform"], "linux")
        self.assertEqual(result["cpu"]["model"], "AMD EPYC")
        self.assertTrue(result["cpu"]["avx2"])
        self.assertEqual(result["gpu"]["type"], "none")
        self.assertGreater(result["memory"]["total"], 0)
        self.assertGreater(result["disk"]["total"], 0)


class RegistryCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.registry = ModelRegistry()
        self.hardware = {
            "architecture": "x86_64", "gpu": {"type": "none"},
            "memory": {"total": 8_000_000_000, "available": 7_000_000_000},
            "disk": {"total": 100_000_000_000, "available": 80_000_000_000},
        }

    def test_registry_normalization_and_unknown_metadata(self):
        model = self.registry.require("qwen3.5:2b")
        self.assertEqual(model["runtime_id"], "qwen3.5:2b")
        self.assertEqual(model["runtime"], "ollama")
        self.assertIsNone(model["quantization"])
        self.assertIsNone(model["capabilities"]["vision"])
        with self.assertRaises(ValueError):
            ModelRegistry([{"name": "same"}, {"name": "same"}])

    def test_compatibility_states_and_reasons(self):
        engine = CompatibilityEngine()
        fast = self.registry.require("qwen3.5:2b")
        self.assertEqual(engine.evaluate(fast, self.hardware)["state"], "compatible")
        warning_hw = {**self.hardware, "memory": {"total": 8_000_000_000, "available": 6_000_000_000}}
        self.assertEqual(engine.evaluate(self.registry.require("qwen3.5:4b"), warning_hw)["state"], "warning")
        tiny = {**self.hardware, "memory": {"total": 4_000_000_000, "available": 4_000_000_000}}
        self.assertEqual(engine.evaluate(self.registry.require("qwen3:14b"), tiny)["state"], "incompatible")
        no_disk = {**self.hardware, "disk": {"total": 10_000_000_000, "available": 2_000_000_000}}
        self.assertIn("INSUFFICIENT_DISK", engine.evaluate(fast, no_disk)["reasons"])
        gpu_model = {**fast, "gpu_required": True}
        self.assertIn("GPU_REQUIRED", engine.evaluate(gpu_model, self.hardware)["reasons"])
        wrong_arch = {**fast, "architecture_support": ["arm64"]}
        self.assertIn("UNSUPPORTED_ARCHITECTURE", engine.evaluate(wrong_arch, self.hardware)["reasons"])
        self.assertIn("RUNTIME_UNAVAILABLE", engine.evaluate(fast, self.hardware, runtime_online=False)["reasons"])


if __name__ == "__main__":
    unittest.main()
