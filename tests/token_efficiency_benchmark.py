#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Offline reproducible prompt/schema benchmark. No API calls or invented usage."""

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.ai.context import estimate_tokens


SCENARIOS = {
    "simple_chat": "Hallo, wie geht es dir?",
    "knowledge": "Suche im Produkthandbuch nach dem LDT Import.",
    "memory": "Erinnerst du dich an meine bevorzugte Ansprache?",
    "single_tool": "Lies die Datei /tmp/report.txt.",
    "email_calendar": "Lies meine E-Mails und zeige die Termine heute.",
    "multi_tool": "Analysiere mehrstufig eine Datei und recherchiere dazu online.",
    "subagent": "Delegiere eine komplexe Analyse parallel an einen Sub-Agenten.",
    "long_conversation": "Hallo, fasse bitte unser langes Gespräch kurz zusammen.",
}

# Recorded by this benchmark before Stage 1 and after Stage 1. These are static
# ceil(chars/4) measurements, not provider usage or billing data.
ORIGINAL_STATIC = 10954
STAGE1_STATIC = {
    "simple_chat": 4351, "knowledge": 4522, "memory": 4636,
    "single_tool": 4671, "email_calendar": 4351,
    "multi_tool": 6498, "subagent": 5148,
    "long_conversation": 4351,
}


def schema_text(tools) -> str:
    return json.dumps([
        {"name": tool.name, "description": tool.description,
         "parameters": tool.parameters_schema()} for tool in tools
    ], ensure_ascii=False, separators=(",", ":"))


def run() -> dict:
    with patch("backend.agent.get_provider", return_value=SimpleNamespace(token_budget=0, cost_budget=0)):
        from backend.agent import JarvisAgent, _execution_plan_cv
        agent = JarvisAgent()
    before_tools = list(agent._llm_tools)
    before_schema_tokens = estimate_tokens(schema_text(before_tools))
    before_system_tokens = estimate_tokens(agent._base_system_prompt())
    rows = []
    for name, task in SCENARIOS.items():
        plan, token = agent._prepare_execution_plan(task)
        try:
            after_tools = list(agent._llm_tools)
            after_schema_tokens = estimate_tokens(schema_text(after_tools))
            after_system_tokens = estimate_tokens(agent._base_system_prompt())
        finally:
            _execution_plan_cv.reset(token)
        conversation_tokens = (estimate_tokens(
            "\n".join(f"Turn {i}: " + ("context " * 80) for i in range(40)))
            if name == "long_conversation" else 0)
        original_total = ORIGINAL_STATIC + conversation_tokens
        stage1_total = STAGE1_STATIC[name] + conversation_tokens
        after_total = after_schema_tokens + after_system_tokens + conversation_tokens
        model_class = {"simple": "local_fast", "tool": "local_general",
                       "autonomous": "local_strong"}.get(plan.level.value, "none")
        rows.append({
            "scenario": name,
            "agent_level": plan.level.value,
            "selected_tools": [tool.name for tool in after_tools],
            "memory_enabled": plan.memory_needed,
            "knowledge_enabled": plan.knowledge_needed,
            "step_budget": plan.max_steps,
            "model_class": model_class,
            "original": {"static_input_tokens_est": original_total,
                         "source": "recorded pre-Stage-1 benchmark"},
            "stage1": {"static_input_tokens_est": stage1_total,
                       "source": "recorded Stage-1 benchmark"},
            "stage2": {"system_tokens_est": after_system_tokens,
                       "tool_schema_tokens_est": after_schema_tokens,
                       "conversation_tokens_est": conversation_tokens,
                       "static_input_tokens_est": after_total,
                       "source": "measured by this run"},
            "stage2_reduction_vs_original_percent": round(
                (original_total - after_total) / original_total * 100, 1),
            "live_metrics": None,
        })
    return {"method": "character estimate (ceil(chars/4)); no LLM call",
            "available_tool_count": len(before_tools), "scenarios": rows}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
