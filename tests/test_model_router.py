# SPDX-License-Identifier: Apache-2.0
import asyncio
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
stub = ModuleType("backend.config")
stub.config = NS(MODEL_ROUTING_MODE="cloud", LOCAL_MODEL="qwen3:4b",
                 LLM_MAX_TOKENS=8192, LLM_REASONING_EFFORT="", LLM_TIMEOUT=180)
sys.modules["backend.config"] = stub
from backend import llm


def active_config():
    return importlib.import_module("backend.config").config


class Provider:
    def __init__(self, response=None, error=None):
        self.response = response or llm.LLMResponse([llm.LLMPart(text="OK")], None, {"input_tokens": 1})
        self.error = error
        self.calls = []

    async def generate_response(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return self.response

    async def generate_image(self, model, prompt):
        self.calls.append((model, prompt))
        return b"image"


class RouterTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, mode, *, tools=None, text="short", local_error=None, primary_name="anthropic", primary_url=""):
        cfg = active_config()
        cfg.MODEL_ROUTING_MODE = mode
        cfg.LOCAL_MODEL = "qwen3:4b"
        cloud, local = Provider(), Provider(error=local_error)
        router = llm.ModelRouterProvider(cloud, primary_name, primary_url)
        contents = [NS(parts=[NS(text=text, inline_data=None)])]
        with patch.object(router, "_local", return_value=local):
            response = await router.generate_response("cloud-model", "system", contents, tools or [])
        return response, cloud, local

    async def test_local_only_never_calls_cloud(self):
        response, cloud, local = await self.call("local_only")
        self.assertEqual(response.usage["route"], "local")
        self.assertEqual(response.usage["model"], "qwen3:4b")
        self.assertEqual(len(local.calls), 1)
        self.assertEqual(cloud.calls, [])
        with self.assertRaises(RuntimeError):
            await self.call("local_only", local_error=RuntimeError("offline"))

    async def test_local_only_without_model_fails_closed(self):
        cfg = active_config()
        cfg.MODEL_ROUTING_MODE = "local_only"
        cfg.LOCAL_MODEL = ""
        cloud = Provider()
        router = llm.ModelRouterProvider(cloud, "anthropic")
        with self.assertRaisesRegex(RuntimeError, "Lokales Modell"):
            await router.generate_response("cloud", "system", [], [])
        self.assertEqual(cloud.calls, [])

    async def test_local_first_falls_back_after_local_failure(self):
        response, cloud, local = await self.call("local_first", local_error=RuntimeError("offline"))
        self.assertEqual(len(local.calls), 1)
        self.assertEqual(len(cloud.calls), 1)
        self.assertEqual(response.usage["route"], "cloud")

    async def test_smart_routes_simple_text_local(self):
        response, cloud, local = await self.call("smart")
        self.assertEqual(response.usage["route"], "local")
        self.assertEqual(cloud.calls, [])

    async def test_smart_routes_tools_and_large_context_to_primary(self):
        response, cloud, local = await self.call("smart", tools=[{"name": "read"}])
        self.assertEqual(response.usage["route"], "local")
        self.assertEqual(cloud.calls, [])
        response, cloud, local = await self.call(
            "smart",
            tools=[{"name": "read"}],
            text="Plane eine komplexe mehrstufige Architekturstrategie mit Agenten und Tools.",
        )
        self.assertEqual(response.usage["route"], "cloud")
        self.assertEqual(local.calls, [])
        response, cloud, local = await self.call("smart", text="x" * 6001)
        self.assertEqual(response.usage["route"], "cloud")
        self.assertEqual(local.calls, [])

    async def test_deterministic_status_uses_no_model(self):
        response, cloud, local = await self.call("cloud", text="Wie viel RAM ist frei?")
        self.assertEqual(response.usage["route"], "deterministic")
        self.assertEqual(response.usage["input_tokens"], 0)
        self.assertEqual(cloud.calls, [])
        self.assertEqual(local.calls, [])

    async def test_cloud_mode_preserves_local_primary_attribution(self):
        response, cloud, local = await self.call("cloud", primary_name="openai_compatible", primary_url="http://127.0.0.1:11434/v1")
        self.assertEqual(response.usage["route"], "local")
        self.assertEqual(len(cloud.calls), 1)
        self.assertEqual(local.calls, [])

    async def test_local_only_blocks_cloud_image(self):
        active_config().MODEL_ROUTING_MODE = "local_only"
        primary = Provider()
        with self.assertRaises(llm.ImageGenNotSupported):
            await llm.ModelRouterProvider(primary, "google").generate_image("imagen", "prompt")
        self.assertEqual(primary.calls, [])

    async def test_cost_budget_fails_closed_without_rates(self):
        active_config().MODEL_ROUTING_MODE = "cloud"
        router = llm.ModelRouterProvider(Provider(), "anthropic")
        router.cost_budget = 1
        with patch.dict("os.environ", {
                "JARVIS_CLOUD_INPUT_COST_PER_MILLION": "0",
                "JARVIS_CLOUD_OUTPUT_COST_PER_MILLION": "0"}):
            with self.assertRaisesRegex(RuntimeError, "Preisraten"):
                await router.generate_response("cloud", "system", [], [])

    async def test_cost_budget_reserves_output_and_tracks_actual_cost(self):
        active_config().MODEL_ROUTING_MODE = "cloud"
        primary = Provider(llm.LLMResponse([llm.LLMPart(text="OK")], None,
                                           {"input_tokens": 100, "output_tokens": 50}))
        router = llm.ModelRouterProvider(primary, "anthropic")
        router.cost_budget = 0.01
        rates = {"JARVIS_CLOUD_INPUT_COST_PER_MILLION": "1",
                 "JARVIS_CLOUD_OUTPUT_COST_PER_MILLION": "2",
                 "JARVIS_CLOUD_OUTPUT_TOKEN_RESERVE": "100"}
        with patch.dict("os.environ", rates):
            response = await router.generate_response("cloud", "system", [], [])
        self.assertAlmostEqual(response.usage["cost"], 0.0002)
        self.assertAlmostEqual(router.total_cost, 0.0002)

        blocked = llm.ModelRouterProvider(Provider(), "anthropic")
        blocked.cost_budget = 0.0001
        with patch.dict("os.environ", rates):
            with self.assertRaisesRegex(RuntimeError, "Kostenbudget"):
                await blocked.generate_response("cloud", "system", [], [])


if __name__ == "__main__":
    unittest.main()
