# SPDX-License-Identifier: Apache-2.0
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.ai.complexity import analyze
from backend.ai.deterministic import answer
from backend.ai.policy import ExecutionRoute, TaskType
from backend.ai.router import decide


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
