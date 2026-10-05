# SPDX-License-Identifier: Apache-2.0
import contextvars
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.ai.execution import AgentLevel, plan_execution
from backend.ai.context import compact_lines, estimate_tokens, fit_text
from backend.llm import LLMPart, LLMResponse, ModelRouterProvider
from backend.llm import _context_budget_for, _request_token_components


AVAILABLE = {
    "knowledge_search", "memory_manage", "google_gmail", "google_calendar",
    "filesystem", "shell_execute", "browser_control", "browser_cdp",
    "spawn_agent", "reflection", "generate_image", "create_chart",
}


def test_simple_chat_has_zero_tools_and_memory():
    plan = plan_execution("Hallo, wie geht es dir?", AVAILABLE)
    assert plan.level == AgentLevel.SIMPLE
    assert plan.tool_names == frozenset()
    assert plan.memory_needed is False
    assert plan.reasoning_effort == "off"
    assert plan.max_steps == 1


def test_domain_tasks_select_only_relevant_tools():
    mail = plan_execution("Lies meine E-Mails im Posteingang", AVAILABLE)
    assert mail.tool_names == {"google_gmail"}
    assert mail.level == AgentLevel.TOOL

    knowledge = plan_execution("Suche im Produkthandbuch nach dem LDT Import", AVAILABLE)
    assert knowledge.tool_names == {"knowledge_search"}
    assert knowledge.knowledge_needed is True
    assert knowledge.memory_needed is False

    memory = plan_execution("Erinnerst du dich an meine Präferenz?", AVAILABLE)
    assert memory.tool_names == {"memory_manage"}
    assert memory.memory_needed is True


def test_complex_task_gets_bounded_autonomous_path():
    plan = plan_execution(
        "Analysiere mehrstufig die Dokumentation und erstelle parallel eine Strategie",
        AVAILABLE,
    )
    assert plan.level == AgentLevel.AUTONOMOUS
    assert plan.max_steps == 12
    assert "knowledge_search" in plan.tool_names
    assert "spawn_agent" in plan.tool_names


def test_unknown_action_uses_compatibility_fallback():
    plan = plan_execution("Führe die Spezialaktion aus", AVAILABLE)
    assert plan.tool_names == AVAILABLE
    assert plan.reason == "unknown_action_conservative_toolset"


def test_known_but_unavailable_domain_does_not_expose_all_tools():
    available = AVAILABLE - {"google_gmail", "google_calendar"}
    plan = plan_execution("Lies meine E-Mails und zeige Termine heute", available)
    assert plan.tool_names == frozenset()
    assert plan.reason == "email+calendar:unavailable"


def test_routing_safety_matrix():
    cases = {
        "Hallo Jarvis": (AgentLevel.SIMPLE, set()),
        "Erkläre Rekursion": (AgentLevel.SIMPLE, set()),
        "Was weißt du über mich?": (AgentLevel.TOOL, {"memory_manage"}),
        "Was habe ich dir gestern über Projekt X gesagt?":
            (AgentLevel.TOOL, {"memory_manage"}),
        "Suche in meinem Wissen nach X": (AgentLevel.TOOL, {"knowledge_search"}),
        "Welche Termine habe ich morgen?": (AgentLevel.TOOL, {"google_calendar"}),
        "Lies meine neuen Emails und fasse sie zusammen":
            (AgentLevel.TOOL, {"google_gmail"}),
        "Schreibe Peter eine Email": (AgentLevel.TOOL, {"google_gmail"}),
        "Prüfe Emails und Kalender und priorisiere meine Aufgaben":
            (AgentLevel.TOOL, {"google_gmail", "google_calendar"}),
    }
    for text, (level, required) in cases.items():
        plan = plan_execution(text, AVAILABLE)
        assert plan.level == level, text
        assert required <= plan.tool_names, text

    autonomous = plan_execution(
        "Analysiere dieses komplexe Problem selbstständig und benutze bei Bedarf Subagenten",
        AVAILABLE,
    )
    assert autonomous.level == AgentLevel.AUTONOMOUS
    assert "spawn_agent" in autonomous.tool_names


def test_agent_tool_filter_is_context_local_and_reduces_schema():
    from backend.agent import JarvisAgent, _execution_plan_cv

    class FakeTool:
        def __init__(self, name): self.name = name
        @property
        def description(self): return "tool " + self.name
        def parameters_schema(self): return {"type": "object", "properties": {}}

    agent = object.__new__(JarvisAgent)
    agent._tool_instances = [FakeTool(name) for name in sorted(AVAILABLE)]
    agent._role_tools = None
    all_tools = list(agent._llm_tools)
    before = json.dumps([
        {"name": tool.name, "description": tool.description,
         "parameters": tool.parameters_schema()} for tool in all_tools
    ], ensure_ascii=False)
    plan, token = agent._prepare_execution_plan("Hallo, wie geht es dir?")
    try:
        assert agent._llm_tools == []
        copied = contextvars.copy_context()
        assert copied.run(lambda: _execution_plan_cv.get()) == plan
    finally:
        _execution_plan_cv.reset(token)
    after = json.dumps([], ensure_ascii=False)
    assert len(before) > 1000
    assert len(after) < len(before)
    assert len(agent._llm_tools) == len(all_tools)


def test_parallel_execution_plans_do_not_mix():
    import asyncio
    from backend.agent import _execution_plan_cv

    async def worker(text):
        plan = plan_execution(text, AVAILABLE)
        token = _execution_plan_cv.set(plan)
        try:
            await asyncio.sleep(0)
            return _execution_plan_cv.get().tool_names
        finally:
            _execution_plan_cv.reset(token)

    async def run():
        return await asyncio.gather(
            worker("Hallo Jarvis"), worker("Lies meine Emails"),
            worker("Suche in meinem Wissen nach X"))

    simple, mail, knowledge = asyncio.run(run())
    assert simple == frozenset()
    assert mail == {"google_gmail"}
    assert knowledge == {"knowledge_search"}


def test_context_budget_is_enforced_and_marked():
    original = "heading\n" + ("complete line of context\n" * 1000)
    compact = fit_text(original, 100, "knowledge")
    assert estimate_tokens(compact) <= 100
    assert "knowledge compacted" in compact
    assert compact.startswith("heading\n")


def test_local_history_summary_is_deduplicated_and_bounded():
    summary = compact_lines(["same fact", "same   fact", "other " * 500], 80)
    assert summary.count("same fact") == 1
    assert estimate_tokens(summary) <= 80


def test_rag_selection_deduplicates_filters_and_budgets():
    from backend.tools.knowledge import _dynamic_rag_limit, _select_rag_results
    repeated = "alpha beta gamma " * 100
    results = _select_rag_results([
        (1.0, "a.md", repeated),
        (0.9, "copy.md", repeated),
        (0.05, "weak.md", "unrelated weak result"),
        (0.8, "b.md", "distinct useful context " * 100),
    ], token_budget=100)
    assert [item[1] for item in results] == ["a.md", "b.md"]
    assert sum(estimate_tokens(item[2]) for item in results) <= 100
    assert _dynamic_rag_limit("kurze frage", 8) == 3


def test_token_components_store_counts_not_content():
    part = SimpleNamespace(text="hello", function_response=SimpleNamespace(response={"secret": "value"}))
    contents = [SimpleNamespace(parts=[part])]
    values = _request_token_components("secure system", contents, [{"name": "read"}])
    assert values["system_tokens_est"] > 0
    assert values["conversation_tokens_est"] > 0
    assert values["tool_result_tokens_est"] > 0
    assert values["tool_schema_count"] == 1
    assert "secret" not in repr(values)


def test_security_prompt_keeps_critical_guarantees():
    from backend.agent import JarvisAgent
    prompt = JarvisAgent.SIMPLE_SYSTEM_PROMPT.casefold()
    for guarantee in ("rechte", "secrets", "api-keys", "kodierten", "untrusted_context"):
        assert guarantee in prompt
    assert "werkzeug-ergebnis" in prompt
    assert estimate_tokens(prompt) < 200


def test_context_budgets_follow_execution_breadth():
    assert _context_budget_for([]) == 8000
    assert _context_budget_for([1]) == 16000
    assert _context_budget_for([1, 2, 3]) == 16000
    assert _context_budget_for([1, 2, 3, 4]) == 32000


def test_context_budget_blocks_before_provider_call():
    import asyncio
    from unittest.mock import AsyncMock

    router = object.__new__(ModelRouterProvider)
    router.token_budget = router.cost_budget = 0
    router.total_input_tokens = router.total_output_tokens = 0
    router.total_cost = 0
    router.primary_route = "cloud"
    router.primary_name = "test"
    router.primary = SimpleNamespace(generate_response=AsyncMock())
    router._policy = lambda: ("cloud", {})

    async def run():
        await router.generate_response("model", "x" * 40_000, [], tools=[])

    try:
        asyncio.run(run())
        assert False, "oversized context was accepted"
    except RuntimeError as error:
        assert "Kontextbudget ueberschritten" in str(error)
    router.primary.generate_response.assert_not_awaited()


def test_provider_usage_source_and_fallback_do_not_double_count():
    router = object.__new__(ModelRouterProvider)
    router.total_input_tokens = router.total_output_tokens = 0
    router.total_cost = 0
    router.last_route = router.last_model = ""
    router._cloud_rates = lambda: (0, 0, 1)
    measured = router._tag(LLMResponse([LLMPart(text="ok")], None,
                                      {"input_tokens": 10, "output_tokens": 2}),
                           "local", "m", {"context_tokens_est": 999})
    assert measured.usage["token_source"] == "measured"
    assert measured.usage["input_tokens"] == 10
    estimated = router._tag(LLMResponse([LLMPart(text="12345678")], None, None),
                            "local", "m", {"context_tokens_est": 20})
    assert estimated.usage == {"input_tokens": 20, "output_tokens": 2, "total_tokens": 22,
                               "token_source": "estimated", "route": "local", "model": "m"}
    assert router.total_input_tokens == 30
