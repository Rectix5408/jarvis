"""Exercise the real message handler without importing startup integrations."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace


class Socket:
    def __init__(self):
        self.state = SimpleNamespace()
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)


class Agent:
    def __init__(self, name, owner="", sub=False):
        self.agent_id = name
        self._owner_username = owner
        self.is_sub_agent = sub
        self.stops = []

    def stop(self, **kwargs):
        self.stops.append(kwargs)

    def get_info(self):
        return {"agent_id": self.agent_id}


def fixture():
    tree = ast.parse(Path("backend/main.py").read_text())
    names = {"handle_ws_message", "_ws_may_access_agent", "cpu_broadcast"}
    functions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    main, own, foreign = Agent("main"), Agent("own", "alice", True), Agent("foreign", "bob", True)
    agents = {a.agent_id: a for a in (main, own, foreign)}
    manager = SimpleNamespace(agents=agents, main_agent=main, get_agent=agents.get, stop_all=lambda: [a.stop() for a in agents.values()])
    users = {}
    ns = {"WebSocket": Socket, "asyncio": asyncio, "agent_instance": main, "agent_manager": manager,
          "_ws_usernames": users, "verify_token": lambda t: t if t in {"alice", "bob", "admin"} else None,
          "_is_valid_agent_key": lambda t: t == "service", "_get_ws_username": lambda w: users.get(id(w), ""),
          "_is_admin_user": lambda u: u == "admin", "_user_must_change": lambda u: False,
          "_login_still_allowed": lambda u: True, "security_guard": SimpleNamespace(is_blocked=lambda u: False),
          "psutil": SimpleNamespace(cpu_percent=lambda **kw: 12)}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "ws-handlers", "exec"), ns)
    return ns, main, own, foreign


async def checks():
    ns, main, own, foreign = fixture()
    ws = Socket()
    async def send(token="alice", **data):
        await ns["handle_ws_message"](ws, {"token": token, **data})
    await send(type="control", action="stop_all")
    assert not main.stops and ws.messages[-1]["type"] == "error"
    await send(type="control", action="stop", agent_id="foreign")
    assert not foreign.stops
    await send(type="control", action="stop", agent_id="own")
    assert len(own.stops) == 1
    await send(type="control", action="stop")
    assert main.stops == [{"username": "alice"}]
    await send(type="get_agents")
    assert [a["agent_id"] for a in ws.messages[-1]["agents"]] == ["main", "own"]
    await send(type="spawn_agent", text="Denied")
    assert ws.messages[-1]["type"] == "error"
    await send(token="admin", type="control", action="stop_all")
    assert len(foreign.stops) == 1
    await send(token="service", type="control", action="stop_all")
    assert ws.messages[-1]["type"] == "error" and not ns["_get_ws_username"](ws)
    await send(token="bad", type="hello")
    assert ws.state.jarvis_credential == ""
    async def one_tick(seconds):
        raise asyncio.CancelledError()
    ns["asyncio"] = SimpleNamespace(sleep=one_tick, CancelledError=asyncio.CancelledError)
    ws.messages.clear()
    await ns["cpu_broadcast"](ws)
    assert ws.messages == []
    ws.state.jarvis_credential = "alice"
    await ns["cpu_broadcast"](ws)
    assert ws.messages == [{"type": "cpu", "value": 12}]


if __name__ == "__main__":
    asyncio.run(checks())
    print("WebSocket authorization: 11 behavioral checks passed")
