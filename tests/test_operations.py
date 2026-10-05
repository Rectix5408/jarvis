# SPDX-License-Identifier: Apache-2.0
import asyncio
import sys
import types

import pytest

from backend.operations import AgentRuntime, OperationsError, OperationsStore


def spec(**changes):
    value = {
        "name": "Research Agent", "description": "", "goal": "Pruefe Fakten",
        "instructions": "Arbeite nachvollziehbar.", "profile_id": "", "tools": ["read_file"],
        "knowledge": [], "autonomy": "supervised", "max_steps": 4,
        "timeout_seconds": 30, "token_budget": 500, "cost_budget": 0,
        "tool_call_limit": 2, "permissions": {"read": True}, "enabled": True,
    }
    value.update(changes)
    return value


@pytest.fixture()
def store(tmp_path):
    result = OperationsStore(tmp_path / "operations")
    result.start()
    return result


def test_agents_are_owner_scoped_and_validated(store):
    agent = store.create_agent(spec(), "Alice@example.org", ["read_file"], [])
    assert agent["owner"] == "alice"
    assert agent["tool_call_limit"] == 2
    assert [item["id"] for item in store.list_agents("alice")] == [agent["id"]]
    assert store.list_agents("bob") == []
    with pytest.raises(OperationsError) as denied:
        store.get_agent(agent["id"], "bob")
    assert denied.value.status == 404
    with pytest.raises(OperationsError):
        store.create_agent(spec(tools=["shell_execute"]), "alice", ["read_file"], [])


def test_run_history_cannot_be_silently_deleted(store):
    agent = store.create_agent(spec(), "alice", ["read_file"], [])
    run = store.create_run(agent["id"], "Aufgabe", "alice")
    store.finish_run(run["id"], "alice", "COMPLETED", result="fertig")
    with pytest.raises(OperationsError) as error:
        store.delete_agent(agent["id"], "alice")
    assert error.value.status == 409
    assert store.get_run(run["id"], "alice")["result"] == "fertig"


def test_restart_marks_orphaned_runs_interrupted(tmp_path):
    first = OperationsStore(tmp_path / "operations")
    first.start()
    agent = first.create_agent(spec(), "alice", ["read_file"], [])
    run = first.create_run(agent["id"], "Aufgabe", "alice")
    second = OperationsStore(tmp_path / "operations")
    second.start()
    recovered = second.get_run(run["id"], "alice")
    assert recovered["status"] == "INTERRUPTED"
    assert recovered["events"][-1]["kind"] == "RUN_INTERRUPTED"
    assert second.notifications("alice", unread=True)[0]["resource_id"] == run["id"]


@pytest.mark.asyncio
async def test_runtime_persists_success_and_enforces_definition(store, monkeypatch):
    instances = []

    class Provider:
        last_route = "local"
        last_model = "qwen3:4b"
        total_input_tokens = 12
        total_output_tokens = 8

    class FakeAgent:
        def __init__(self, **kwargs):
            self.provider = Provider()
            self.stopped = False
            instances.append(self)

        async def run_task_headless(self, task, actor):
            assert actor["user"] == "alice"
            assert self._role_tools == {"read_file"}
            assert self._role_permissions == {"read": True}
            assert self._role_tool_call_limit == 2
            assert self._role_token_budget == 500
            self._operations_event("KNOWLEDGE_SEARCH", "Lokale Suche")
            return "Ergebnis"

        def stop(self):
            self.stopped = True

    monkeypatch.setitem(sys.modules, "backend.agent", types.SimpleNamespace(JarvisAgent=FakeAgent))
    definition = store.create_agent(spec(), "alice", ["read_file"], [])
    runtime = AgentRuntime(store, lambda user: False, lambda user: False, lambda user: False)
    run = await runtime.start_run(definition, "Untersuche dies", "alice")
    await runtime.tasks[run["id"]]
    finished = store.get_run(run["id"], "alice")
    assert finished["status"] == "COMPLETED"
    assert finished["route"] == "local"
    assert finished["input_tokens"] + finished["output_tokens"] == 20
    assert any(event["kind"] == "KNOWLEDGE_SEARCH" for event in finished["events"])


@pytest.mark.asyncio
async def test_runtime_stop_cancels_the_actual_task(store, monkeypatch):
    started = asyncio.Event()

    class FakeAgent:
        provider = types.SimpleNamespace(last_route="", last_model="", total_input_tokens=0, total_output_tokens=0)
        def __init__(self, **kwargs): self.stopped = False
        async def run_task_headless(self, task, actor):
            started.set()
            await asyncio.sleep(60)
        def stop(self): self.stopped = True

    monkeypatch.setitem(sys.modules, "backend.agent", types.SimpleNamespace(JarvisAgent=FakeAgent))
    definition = store.create_agent(spec(), "alice", ["read_file"], [])
    runtime = AgentRuntime(store, lambda user: False, lambda user: False, lambda user: False)
    run = await runtime.start_run(definition, "Warte", "alice")
    await started.wait()
    stopped = await runtime.cancel(store.get_run(run["id"], "alice"), "alice")
    assert stopped["status"] == "CANCELLED"
    assert stopped["cancel_requested"] is True


@pytest.mark.asyncio
async def test_supervised_sensitive_tool_waits_for_owner_approval(store, monkeypatch):
    entered = asyncio.Event()

    class FakeAgent:
        provider = types.SimpleNamespace(last_route="local", last_model="qwen3:4b", total_input_tokens=1, total_output_tokens=1, total_cost=0)
        def __init__(self, **kwargs): pass
        async def run_task_headless(self, task, actor):
            entered.set()
            allowed = await self._approval_handler("send_email", {"to": "hidden@example.org"}, "send")
            return "sent" if allowed else "not sent"
        def stop(self): pass

    monkeypatch.setitem(sys.modules, "backend.agent", types.SimpleNamespace(JarvisAgent=FakeAgent))
    definition = store.create_agent(spec(tools=["send_email"], permissions={"send": True}),
                                    "alice", ["send_email"], [])
    runtime = AgentRuntime(store, lambda user: False, lambda user: False, lambda user: False)
    run = await runtime.start_run(definition, "Sende", "alice")
    await entered.wait()
    for _ in range(20):
        pending = store.list_approvals("alice", pending=True)
        if pending:
            break
        await asyncio.sleep(0)
    assert store.get_run(run["id"], "alice")["status"] == "WAITING_FOR_APPROVAL"
    with pytest.raises(OperationsError):
        store.get_approval(pending[0]["id"], "bob")
    decision = store.decide_approval(pending[0]["id"], "alice", True)
    runtime.decide_approval(decision, True)
    await runtime.tasks[run["id"]]
    finished = store.get_run(run["id"], "alice")
    assert finished["status"] == "COMPLETED"
    assert finished["result"] == "sent"
    assert [event["kind"] for event in finished["events"]][-2:] == ["APPROVAL_APPROVED", "RUN_COMPLETED"]


def test_validation_clamps_hard_limits(store):
    value = store.validate(spec(max_steps=999, timeout_seconds=2, tool_call_limit=9999))
    assert value["max_steps"] == 50
    assert value["timeout_seconds"] == 10
    assert value["tool_call_limit"] == 1000
