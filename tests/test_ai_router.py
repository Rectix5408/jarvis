# SPDX-License-Identifier: Apache-2.0
import asyncio
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.ai.complexity import analyze
from backend.ai.deterministic import answer
from backend.ai.policy import ExecutionRoute, TaskType
from backend.ai.router import decide
from backend.ai.models import choose_local_model
from backend.ai.usage import UsageTracker


def content(text):
    return [NS(parts=[NS(text=text, inline_data=None)])]


def test_complexity_classifies_deterministic_and_code():
    status = analyze(content("Wie viel RAM ist frei?"), [])
    assert status.task_type == TaskType.DETERMINISTIC
    assert status.complexity < 0.1

    code = analyze(content("Analysiere diesen Python Stacktrace und plane eine Loesung"), [])
    assert code.task_type in {TaskType.CODE, TaskType.REASONING}
    assert code.complexity >= 0.4


def test_router_blocks_cloud_in_local_only_when_capability_missing():
    decision = decide(
        mode="local_only",
        local_model="qwen3:4b",
        primary_route="cloud",
        contents=content("Beschreibe was auf diesem Bild zu sehen ist"),
        tools=[],
    )
    assert decision.route == ExecutionRoute.BLOCKED
    assert decision.cloud_allowed is False


def test_router_routes_deterministic_without_model():
    decision = decide(
        mode="cloud",
        local_model="",
        primary_route="cloud",
        contents=content("Welche Modelle sind installiert?"),
        tools=[],
    )
    assert decision.route == ExecutionRoute.DETERMINISTIC
    assert decision.task_type == TaskType.DETERMINISTIC


def test_deterministic_hardware_answer():
    result = asyncio.run(answer("Wie viel RAM ist frei und wie ist die CPU?"))
    assert result
    assert "RAM:" in result
    assert "CPU-Auslastung" in result


def test_local_tiers_choose_smallest_capable_model():
    models = {
        "local_fast": "qwen3:1.7b",
        "local_general": "qwen3:4b",
        "local_strong": "qwen3:14b",
    }
    simple = choose_local_model(models, set(), 0.1, 20)
    normal = choose_local_model(models, set(), 0.4, 200)
    complex_task = choose_local_model(models, set(), 0.8, 200)
    assert (simple.id, simple.tier) == ("qwen3:1.7b", "local_fast")
    assert (normal.id, normal.tier) == ("qwen3:4b", "local_general")
    assert (complex_task.id, complex_task.tier) == ("qwen3:14b", "local_strong")
    assert choose_local_model({"local_fast": "qwen3:1.7b"}, set(), 0.8, 200) is None


def test_usage_tracking_persists_without_prompt_content():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "usage.sqlite3"
        tracker = UsageTracker(path)
        tracker.record("local", {"model": "qwen3:4b", "input_tokens": 12,
                                  "output_tokens": 4, "task_type": "chat"},
                       reason="smart_smallest_capable_local", prompt="SECRET PROMPT")
        tracker.record("cloud", {"model": "claude", "input_tokens": 20,
                                  "output_tokens": 5, "cost": 0.01})
        reopened = UsageTracker(path)
        snapshot = reopened.snapshot("today")
        assert snapshot["requests"] == 2
        assert snapshot["routes"] == {"cloud": 1, "local": 1}
        assert snapshot["estimated_cloud_calls_avoided"] == 1
        raw = sqlite3.connect(path).execute("SELECT metadata_json FROM ai_usage").fetchall()
        assert "SECRET PROMPT" not in repr(raw)
