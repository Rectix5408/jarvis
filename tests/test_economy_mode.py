"""Economy mode regression tests; no live settings or external requests."""
import ast
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
stub = ModuleType("backend.config")
stub.config = NS(LLM_MAX_TOKENS=8192, LLM_REASONING_EFFORT="", LLM_TIMEOUT=180)
sys.modules["backend.config"] = stub
from backend import llm
from google.genai import types


def method(path, owner, name, namespace):
    tree = ast.parse((ROOT / path).read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == owner)
    node = next(n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), namespace)
    return namespace[name]


class EconomyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cfg = importlib.import_module("backend.config").config
        cfg.MODEL_ROUTING_MODE = "cloud"
        cfg.LOCAL_MODEL = "qwen3:4b"
        cfg.LLM_MAX_TOKENS = 8192
        self.usage_record = patch("backend.ai.usage.usage_tracker.record")
        self.usage_totals = patch("backend.ai.usage.usage_tracker.cloud_totals", return_value=(0, 0.0))
        self.usage_record.start()
        self.usage_totals.start()
        self.addCleanup(self.usage_record.stop)
        self.addCleanup(self.usage_totals.stop)

    async def test_original_role_prompt_remains(self):
        prompt = method("backend/agent.py", "JarvisAgent", "_base_system_prompt", {})
        agent = NS(_role_prompt="Original security and role rules", _zeit_hinweis=lambda: " time",
                   _eff_profile={"economy_mode": True})
        self.assertTrue(prompt(agent).startswith(agent._role_prompt + " time"))
        self.assertIn("Sicherheitspruefungen", prompt(agent))
        agent._eff_profile = {}
        self.assertEqual(prompt(agent), agent._role_prompt + " time")

    async def test_local_request_budget_native_and_prompt(self):
        client = NS(post=AsyncMock(return_value=NS(is_success=True, json=lambda: {
            "choices": [{"message": {"role": "assistant", "content": "OK"}}]})))
        contents = [types.Content(role="user", parts=[types.Part.from_text(text="Hello")])]
        with patch.object(llm, "_get_shared_client", AsyncMock(return_value=client)):
            for prompt in (False, True):
                provider = llm.get_provider("openai_compatible", "", "http://ollama:11434/v1/chat/completions",
                                            prompt_tool_calling=prompt, economy_mode=True)
                await provider.generate_response("installed-local-model", "system", contents, [])
                args = client.post.call_args
                self.assertEqual(args.args[0], "http://ollama:11434/v1/chat/completions")
                self.assertEqual(args.kwargs["json"]["max_tokens"], 2048)
                self.assertNotIn("Authorization", args.kwargs["headers"])
            self.assertEqual(client.post.await_count, 2)
        active_config = importlib.import_module("backend.config").config
        with patch.object(active_config, "LLM_MAX_TOKENS", 1024):
            self.assertEqual(llm._llm_max_tokens(True), 1024)
        self.assertEqual(llm._llm_max_tokens(), 8192)

    async def test_local_failure_never_calls_cloud(self):
        client = NS(post=AsyncMock(side_effect=RuntimeError("local offline")))
        provider = llm.get_provider("openai_compatible", "", "http://ollama:11434/v1/chat/completions", economy_mode=True)
        with patch.object(llm, "_get_shared_client", AsyncMock(return_value=client)):
            with self.assertRaises(RuntimeError):
                await provider.generate_response("local", "system", [], [])
        self.assertEqual(client.post.await_count, 1)
        self.assertEqual(client.post.call_args.args[0], "http://ollama:11434/v1/chat/completions")

    async def test_anthropic_high_effort_does_not_raise_budget(self):
        provider = object.__new__(llm.AnthropicProvider)
        provider.economy_mode = True
        create = AsyncMock(return_value=NS(content=[NS(type="text", text="OK")], usage=NS(input_tokens=1, output_tokens=1)))
        provider.client = NS(messages=NS(create=create))
        await provider.generate_response("test", "system", [], [], reasoning_effort="high")
        self.assertLessEqual(create.call_args.kwargs["max_tokens"], 2048)

    async def test_anthropic_tool_use_ids_are_unique_for_repeated_tool_names(self):
        provider = object.__new__(llm.AnthropicProvider)
        provider.economy_mode = True
        create = AsyncMock(return_value=NS(content=[NS(type="text", text="OK")], usage=NS(input_tokens=1, output_tokens=1)))
        provider.client = NS(messages=NS(create=create))
        history = [
            types.Content(role="user", parts=[types.Part.from_text(text="search twice")]),
            types.Content(role="model", parts=[
                types.Part(function_call=types.FunctionCall(name="search", args={"q": "a"})),
                types.Part(function_call=types.FunctionCall(name="search", args={"q": "b"})),
            ]),
            types.Content(role="user", parts=[
                types.Part(function_response=types.FunctionResponse(name="search", response={"result": "A" * 1500})),
                types.Part(function_response=types.FunctionResponse(name="search", response={"result": "B"})),
            ]),
        ]
        await provider.generate_response("test", "system", history, [])
        messages = create.call_args.kwargs["messages"]
        tool_uses = [b for b in messages[1]["content"] if b["type"] == "tool_use"]
        tool_results = [b for b in messages[2]["content"] if b["type"] == "tool_result"]
        ids = [b["id"] for b in tool_uses]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual([b["tool_use_id"] for b in tool_results], ids)
        self.assertLess(len(tool_results[0]["content"]), 1400)
        self.assertIn("gekuerzt", tool_results[0]["content"])

    async def test_compression_preserves_tool_turns_and_failure_history(self):
        compress = method("backend/agent.py", "JarvisAgent", "_compress_history", {"json": json, "types": types})
        history = []
        for i in range(8):
            history.extend([
                types.Content(role="user", parts=[types.Part.from_text(text=f"Task {i}")]),
                types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name="read", args={"path": str(i)}))]),
                types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(name="read", response={"result": "done"}))]),
                types.Content(role="model", parts=[types.Part.from_text(text="Done")]),
            ])
        agent = NS(_eff_profile={"economy_mode": True}, _compress_threshold=30,
                   agent_id="test", current_model="local",
                   provider=NS(generate_response=AsyncMock(return_value=NS(parts=[NS(text="Summary")]))))
        result = await compress(agent, history, "system")
        self.assertEqual(result[1:], history[-8:])
        self.assertIsNone(result[1].parts[0].function_response)
        agent.provider.generate_response.assert_not_awaited()
        agent.provider.generate_response.side_effect = RuntimeError("offline")
        offline_result = await compress(agent, history, "system")
        self.assertEqual(offline_result[1:], history[-8:])
        agent.provider.generate_response.assert_not_awaited()
        agent._eff_profile = {}
        short = history[:20]
        self.assertIs(await compress(agent, short, "system"), short)

    async def test_profile_persistence_opt_in(self):
        import uuid
        ns = {"uuid": uuid, "_clean_profile_str": lambda x: x, "_valid_effort": lambda x: x,
              "_valid_temperature": lambda x: x, "TEMPERATURE_AUTO": "auto"}
        create = method("backend/config.py", "Config", "create_profile", ns)
        update = method("backend/config.py", "Config", "update_profile", ns)
        cfg = NS(profiles=[], active_profile_id="", DEFAULT_PROVIDERS={}, _save_to_file=lambda: None)
        profile = create(cfg, {"provider": "openai_compatible", "economy_mode": True})
        self.assertTrue(profile["economy_mode"])
        update(cfg, profile["id"], {"economy_mode": False})
        self.assertFalse(profile["economy_mode"])
        self.assertFalse(create(cfg, {})["economy_mode"])
        self.assertFalse(create(cfg, {"economy_mode": "false"})["economy_mode"])


if __name__ == "__main__":
    unittest.main()
