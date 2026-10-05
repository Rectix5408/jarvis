# SPDX-License-Identifier: Apache-2.0
"""Persistent agent definitions and background runs without storing hidden reasoning."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}
AUTONOMY = {"manual", "supervised", "autonomous"}


class OperationsError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _owner(value):
    value = (value or "").split("@", 1)[0].split("\\")[-1].strip().lower()
    if not value:
        raise OperationsError("Benutzer fehlt", 401)
    return value


class OperationsStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.started = False

    def _db(self):
        connection = sqlite3.connect(self.directory / "operations.sqlite3", timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def start(self):
        if self.started:
            return
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS agents (
              id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
              description TEXT NOT NULL, goal TEXT NOT NULL, instructions TEXT NOT NULL,
              profile_id TEXT NOT NULL, tools_json TEXT NOT NULL, knowledge_json TEXT NOT NULL,
              autonomy TEXT NOT NULL, max_steps INTEGER NOT NULL, timeout_seconds INTEGER NOT NULL,
              token_budget INTEGER NOT NULL, cost_budget REAL NOT NULL,
              tool_call_limit INTEGER NOT NULL DEFAULT 0,
              permissions_json TEXT NOT NULL, enabled INTEGER NOT NULL,
              created REAL NOT NULL, updated REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS agents_owner_idx ON agents(owner, updated DESC);
            CREATE TABLE IF NOT EXISTS runs (
              id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, owner TEXT NOT NULL,
              task TEXT NOT NULL, status TEXT NOT NULL, route TEXT NOT NULL,
              model TEXT NOT NULL, started REAL NOT NULL, finished REAL,
              result TEXT NOT NULL, error TEXT NOT NULL,
              input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
              cost REAL NOT NULL, tools_json TEXT NOT NULL DEFAULT '[]',
              cancel_requested INTEGER NOT NULL DEFAULT 0,
              FOREIGN KEY(agent_id) REFERENCES agents(id) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS runs_owner_idx ON runs(owner, started DESC);
            CREATE TABLE IF NOT EXISTS run_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
              ts REAL NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL,
              data_json TEXT NOT NULL, FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS events_run_idx ON run_events(run_id, id);
            CREATE TABLE IF NOT EXISTS notifications (
              id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
              title TEXT NOT NULL, message TEXT NOT NULL, resource_id TEXT NOT NULL,
              read INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS notify_owner_idx ON notifications(owner, created DESC);
            CREATE TABLE IF NOT EXISTS approvals (
              id TEXT PRIMARY KEY, run_id TEXT NOT NULL, owner TEXT NOT NULL,
              tool TEXT NOT NULL, permission TEXT NOT NULL, summary TEXT NOT NULL,
              status TEXT NOT NULL, created REAL NOT NULL, decided REAL,
              decided_by TEXT NOT NULL DEFAULT '',
              FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS approvals_owner_idx ON approvals(owner, status, created DESC);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(agents)")}
            if "tool_call_limit" not in columns:
                db.execute("ALTER TABLE agents ADD COLUMN tool_call_limit INTEGER NOT NULL DEFAULT 0")
            run_columns = {row["name"] for row in db.execute("PRAGMA table_info(runs)")}
            if "tools_json" not in run_columns:
                db.execute("ALTER TABLE runs ADD COLUMN tools_json TEXT NOT NULL DEFAULT '[]'")
            now = time.time()
            rows = db.execute("SELECT id,owner FROM runs WHERE status IN ('QUEUED','RUNNING','WAITING_FOR_APPROVAL')").fetchall()
            for row in rows:
                db.execute("UPDATE runs SET status='INTERRUPTED',finished=?,error='Backend wurde waehrend des Laufs neu gestartet' WHERE id=?", (now, row["id"]))
                self._event(db, row["id"], "RUN_INTERRUPTED", "Backend neu gestartet")
                self._notify(db, row["owner"], "agent_failed", "Agentenlauf unterbrochen", "Der Backend-Prozess wurde neu gestartet.", row["id"])
            db.execute("UPDATE approvals SET status='CANCELLED',decided=? WHERE status='PENDING'", (now,))
            db.execute("PRAGMA user_version=1")
        self.started = True

    @staticmethod
    def _event(db, run_id, kind, message, data=None):
        db.execute("INSERT INTO run_events(run_id,ts,kind,message,data_json) VALUES(?,?,?,?,?)",
                   (run_id, time.time(), kind, str(message)[:1000], json.dumps(data or {}, ensure_ascii=True)))

    @staticmethod
    def _notify(db, owner, kind, title, message, resource_id):
        db.execute("INSERT INTO notifications(id,owner,kind,title,message,resource_id,created) VALUES(?,?,?,?,?,?,?)",
                   (uuid.uuid4().hex, owner, kind, title[:120], message[:500], resource_id, time.time()))

    @staticmethod
    def _agent(row):
        data = dict(row)
        for key in ("tools", "knowledge", "permissions"):
            data[key] = json.loads(data.pop(key + "_json"))
        data["enabled"] = bool(data["enabled"])
        return data

    @staticmethod
    def _run(row):
        data = dict(row)
        data["cancel_requested"] = bool(data["cancel_requested"])
        data["tools"] = json.loads(data.pop("tools_json", "[]"))
        return data

    def list_agents(self, user, admin=False):
        with self._db() as db:
            rows = db.execute("SELECT * FROM agents ORDER BY updated DESC" if admin else "SELECT * FROM agents WHERE owner=? ORDER BY updated DESC", () if admin else (_owner(user),)).fetchall()
        return [self._agent(row) for row in rows]

    def get_agent(self, identifier, user, admin=False):
        with self._db() as db:
            row = db.execute("SELECT * FROM agents WHERE id=?", (identifier,)).fetchone()
        if not row or (not admin and row["owner"] != _owner(user)):
            raise OperationsError("Agent nicht gefunden", 404)
        return self._agent(row)

    @staticmethod
    def validate(spec, available_tools=None, profile_ids=None):
        name = str(spec.get("name") or "").strip()
        goal = str(spec.get("goal") or "").strip()
        instructions = str(spec.get("instructions") or "").strip()
        if not 2 <= len(name) <= 80:
            raise OperationsError("Name muss 2 bis 80 Zeichen lang sein")
        if not goal or len(goal) > 1000 or len(instructions) > 12000:
            raise OperationsError("Ziel fehlt oder Text ist zu lang")
        tools = list(dict.fromkeys(str(item) for item in spec.get("tools", []) if item))
        if available_tools is not None:
            unknown = sorted(set(tools) - set(available_tools))
            if unknown:
                raise OperationsError("Unbekannte Werkzeuge: " + ", ".join(unknown))
        profile_id = str(spec.get("profile_id") or "")
        if profile_id and profile_ids is not None and profile_id not in set(profile_ids):
            raise OperationsError("LLM-Profil nicht gefunden")
        autonomy = str(spec.get("autonomy") or "supervised").lower()
        if autonomy not in AUTONOMY:
            raise OperationsError("Ungueltige Autonomie-Stufe")
        permissions = spec.get("permissions") or {}
        if not isinstance(permissions, dict) or any(value is not True and value is not False for value in permissions.values()):
            raise OperationsError("Berechtigungen muessen boolesch sein")
        return {
            "name": name, "description": str(spec.get("description") or "")[:1000],
            "goal": goal, "instructions": instructions, "profile_id": profile_id,
            "tools": tools, "knowledge": list(dict.fromkeys(str(x) for x in spec.get("knowledge", []) if x))[:100],
            "autonomy": autonomy, "max_steps": max(1, min(int(spec.get("max_steps") or 12), 50)),
            "timeout_seconds": max(10, min(int(spec.get("timeout_seconds") or 600), 3600)),
            "token_budget": max(0, min(int(spec.get("token_budget") or 0), 10_000_000)),
            "cost_budget": max(0.0, min(float(spec.get("cost_budget") or 0), 10000.0)),
            "tool_call_limit": max(0, min(int(spec.get("tool_call_limit") or 0), 1000)),
            "permissions": permissions, "enabled": spec.get("enabled") is not False,
        }

    def create_agent(self, spec, user, available_tools=None, profile_ids=None):
        data = self.validate(spec, available_tools, profile_ids)
        identifier, now, owner = uuid.uuid4().hex, time.time(), _owner(user)
        with self._db() as db:
            db.execute("""INSERT INTO agents(
                id,owner,name,description,goal,instructions,profile_id,tools_json,
                knowledge_json,autonomy,max_steps,timeout_seconds,token_budget,
                cost_budget,tool_call_limit,permissions_json,enabled,created,updated
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                identifier, owner, data["name"], data["description"], data["goal"], data["instructions"], data["profile_id"],
                json.dumps(data["tools"]), json.dumps(data["knowledge"]), data["autonomy"], data["max_steps"], data["timeout_seconds"],
                data["token_budget"], data["cost_budget"], data["tool_call_limit"],
                json.dumps(data["permissions"]), int(data["enabled"]), now, now))
        return self.get_agent(identifier, owner)

    def update_agent(self, identifier, spec, user, admin=False, available_tools=None, profile_ids=None):
        old = self.get_agent(identifier, user, admin)
        merged = {**old, **spec}
        data = self.validate(merged, available_tools, profile_ids)
        with self._db() as db:
            db.execute("UPDATE agents SET name=?,description=?,goal=?,instructions=?,profile_id=?,tools_json=?,knowledge_json=?,autonomy=?,max_steps=?,timeout_seconds=?,token_budget=?,cost_budget=?,tool_call_limit=?,permissions_json=?,enabled=?,updated=? WHERE id=?", (
                data["name"], data["description"], data["goal"], data["instructions"], data["profile_id"], json.dumps(data["tools"]), json.dumps(data["knowledge"]), data["autonomy"], data["max_steps"], data["timeout_seconds"], data["token_budget"], data["cost_budget"], data["tool_call_limit"], json.dumps(data["permissions"]), int(data["enabled"]), time.time(), identifier))
        return self.get_agent(identifier, user, admin)

    def delete_agent(self, identifier, user, admin=False):
        agent = self.get_agent(identifier, user, admin)
        with self._db() as db:
            if db.execute("SELECT 1 FROM runs WHERE agent_id=? AND status IN ('QUEUED','RUNNING','WAITING_FOR_APPROVAL')", (identifier,)).fetchone():
                raise OperationsError("Laufenden Agenten zuerst stoppen", 409)
            if db.execute("SELECT 1 FROM runs WHERE agent_id=?", (identifier,)).fetchone():
                raise OperationsError("Agenten mit Laufhistorie koennen nur deaktiviert werden", 409)
            db.execute("DELETE FROM agents WHERE id=?", (identifier,))
        return agent

    def create_run(self, agent_id, task, owner, tools=None):
        identifier, now = uuid.uuid4().hex, time.time()
        with self._db() as db:
            db.execute("INSERT INTO runs(id,agent_id,owner,task,status,route,model,started,result,error,input_tokens,output_tokens,cost,tools_json) VALUES(?,?,?,?,'QUEUED','','',?,'','',0,0,0,?)", (identifier, agent_id, _owner(owner), str(task)[:20000], now, json.dumps(tools or [])))
            self._event(db, identifier, "RUN_QUEUED", "Lauf eingeplant")
        return self.get_run(identifier, owner)

    def update_run(self, identifier, **values):
        allowed = {"status", "route", "model", "finished", "result", "error", "input_tokens", "output_tokens", "cost", "cancel_requested"}
        values = {key: value for key, value in values.items() if key in allowed}
        if not values:
            return
        with self._db() as db:
            db.execute("UPDATE runs SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=?", (*values.values(), identifier))

    def add_event(self, run_id, kind, message, data=None):
        with self._db() as db:
            self._event(db, run_id, kind, message, data)

    def finish_run(self, identifier, owner, status, result="", error="", **usage):
        with self._db() as db:
            db.execute("UPDATE runs SET status=?,finished=?,result=?,error=?,route=?,model=?,input_tokens=?,output_tokens=?,cost=? WHERE id=?", (
                status, time.time(), str(result)[:100000], str(error)[:4000], usage.get("route", ""), usage.get("model", ""),
                int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)), float(usage.get("cost", 0)), identifier))
            self._event(db, identifier, "RUN_" + status, error or "Lauf abgeschlossen")
            kind = "agent_completed" if status == "COMPLETED" else "agent_failed"
            self._notify(db, _owner(owner), kind, "Agent abgeschlossen" if status == "COMPLETED" else "Agent fehlgeschlagen", error or "Der Lauf ist abgeschlossen.", identifier)

    def list_runs(self, user, admin=False, agent_id=""):
        sql, args = ("SELECT * FROM runs", []) if admin else ("SELECT * FROM runs WHERE owner=?", [_owner(user)])
        if agent_id:
            sql += " WHERE agent_id=?" if admin else " AND agent_id=?"
            args.append(agent_id)
        with self._db() as db:
            rows = db.execute(sql + " ORDER BY started DESC LIMIT 200", args).fetchall()
        return [self._run(row) for row in rows]

    def get_run(self, identifier, user, admin=False):
        with self._db() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (identifier,)).fetchone()
            events = db.execute("SELECT id,ts,kind,message,data_json FROM run_events WHERE run_id=? ORDER BY id", (identifier,)).fetchall() if row else []
        if not row or (not admin and row["owner"] != _owner(user)):
            raise OperationsError("Lauf nicht gefunden", 404)
        data = self._run(row)
        data["events"] = [{**dict(event), "data": json.loads(event["data_json"])} for event in events]
        for event in data["events"]:
            event.pop("data_json", None)
        return data

    def notifications(self, user, unread=False):
        sql = "SELECT * FROM notifications WHERE owner=?" + (" AND read=0" if unread else "") + " ORDER BY created DESC LIMIT 100"
        with self._db() as db:
            return [dict(row) for row in db.execute(sql, (_owner(user),)).fetchall()]

    def mark_notification(self, identifier, user):
        with self._db() as db:
            changed = db.execute("UPDATE notifications SET read=1 WHERE id=? AND owner=?", (identifier, _owner(user))).rowcount
        if not changed:
            raise OperationsError("Benachrichtigung nicht gefunden", 404)

    def notify(self, owner, kind, title, message, resource_id):
        with self._db() as db:
            self._notify(db, _owner(owner), kind, title, message, resource_id)

    def create_approval(self, run_id, owner, tool, permission, summary):
        identifier, now, owner = uuid.uuid4().hex, time.time(), _owner(owner)
        with self._db() as db:
            db.execute("INSERT INTO approvals(id,run_id,owner,tool,permission,summary,status,created) VALUES(?,?,?,?,?,?,'PENDING',?)",
                       (identifier, run_id, owner, str(tool)[:120], str(permission)[:40], str(summary)[:500], now))
            self._event(db, run_id, "WAITING_APPROVAL", str(summary)[:500], {"approval_id": identifier, "tool": str(tool)[:120]})
            self._notify(db, owner, "approval", "Freigabe erforderlich", str(summary)[:500], identifier)
        return self.get_approval(identifier, owner)

    def list_approvals(self, user, admin=False, pending=False):
        sql, args = ("SELECT * FROM approvals", []) if admin else ("SELECT * FROM approvals WHERE owner=?", [_owner(user)])
        if pending:
            sql += " WHERE status='PENDING'" if admin else " AND status='PENDING'"
        with self._db() as db:
            return [dict(row) for row in db.execute(sql + " ORDER BY created DESC LIMIT 100", args).fetchall()]

    def get_approval(self, identifier, user, admin=False):
        with self._db() as db:
            row = db.execute("SELECT * FROM approvals WHERE id=?", (identifier,)).fetchone()
        if not row or (not admin and row["owner"] != _owner(user)):
            raise OperationsError("Freigabe nicht gefunden", 404)
        return dict(row)

    def decide_approval(self, identifier, user, approved, admin=False):
        approval = self.get_approval(identifier, user, admin)
        if approval["status"] != "PENDING":
            raise OperationsError("Freigabe wurde bereits entschieden", 409)
        decision = "APPROVED" if approved else "REJECTED"
        with self._db() as db:
            changed = db.execute("UPDATE approvals SET status=?,decided=?,decided_by=? WHERE id=? AND status='PENDING'",
                                 (decision, time.time(), _owner(user), identifier)).rowcount
            if not changed:
                raise OperationsError("Freigabe wurde bereits entschieden", 409)
            self._event(db, approval["run_id"], "APPROVAL_" + decision, approval["summary"], {"approval_id": identifier})
        return self.get_approval(identifier, user, admin)


class AgentRuntime:
    def __init__(self, store, is_admin, internet, sap, audit=None):
        self.store, self.is_admin, self.internet, self.sap = store, is_admin, internet, sap
        self.audit = audit or (lambda *args: None)
        self.tasks, self.agents, self.approval_waiters = {}, {}, {}

    async def start_run(self, definition, task, user):
        if not definition["enabled"]:
            raise OperationsError("Agent ist deaktiviert", 409)
        if not str(task).strip():
            raise OperationsError("Aufgabe fehlt")
        run = self.store.create_run(definition["id"], task, user, definition["tools"])
        background = asyncio.create_task(self._execute(run, definition, str(task), user))
        self.tasks[run["id"]] = background
        background.add_done_callback(lambda _: self.tasks.pop(run["id"], None))
        self.audit(user, "agent_run", run["id"])
        return run

    async def _execute(self, run, definition, task, user):
        from backend.agent import JarvisAgent
        agent = JarvisAgent(agent_id=run["id"][:8], label=definition["name"], is_sub_agent=True)
        agent._owner_username = _owner(user)
        agent._role_id = definition["id"]
        agent._role_prompt = (definition["instructions"] + "\n\nZIEL:\n" + definition["goal"]).strip()
        agent._role_profile_id = definition["profile_id"]
        agent._role_tools = set(definition["tools"])
        agent._role_max_steps = definition["max_steps"]
        agent._role_permissions = dict(definition["permissions"])
        agent._role_tool_call_limit = definition["tool_call_limit"]
        agent._role_tool_calls = 0
        agent._role_token_budget = definition["token_budget"]
        agent._role_cost_budget = definition["cost_budget"]
        agent._operations_event = lambda kind, message, data=None: self.store.add_event(
            run["id"], kind, message, data)
        agent._approval_handler = lambda tool, args, permission: self._request_approval(
            run, definition, user, tool, args, permission)
        self.agents[run["id"]] = agent
        self.store.update_run(run["id"], status="RUNNING")
        self.store.add_event(run["id"], "RUN_STARTED", "Agent gestartet", {"agent_id": definition["id"]})
        try:
            result = await asyncio.wait_for(agent.run_task_headless(task, actor={
                "user": user, "privileged": bool(self.is_admin(user)),
                "internet": bool(self.internet(user)), "sap": bool(self.sap(user)),
            }), timeout=definition["timeout_seconds"])
            if self.store.get_run(run["id"], user)["cancel_requested"]:
                self.store.finish_run(run["id"], user, "CANCELLED", error="Vom Benutzer abgebrochen")
            elif result.startswith("Fehler:"):
                self.store.finish_run(run["id"], user, "FAILED", error=result[:4000], **self._usage(agent))
            else:
                self.store.finish_run(run["id"], user, "COMPLETED", result=result, **self._usage(agent))
        except asyncio.TimeoutError:
            agent.stop()
            self.store.finish_run(run["id"], user, "FAILED", error="Zeitlimit erreicht", **self._usage(agent))
        except asyncio.CancelledError:
            agent.stop()
            current = self.store.get_run(run["id"], user)
            if current["status"] not in TERMINAL:
                self.store.finish_run(run["id"], user, "CANCELLED", error="Vom Benutzer abgebrochen", **self._usage(agent))
        except Exception:
            self.store.finish_run(run["id"], user, "FAILED", error="Interner Agentenfehler", **self._usage(agent))
        finally:
            self.agents.pop(run["id"], None)

    async def _request_approval(self, run, definition, user, tool, args, permission):
        requires = definition["autonomy"] == "manual" or (
            definition["autonomy"] == "supervised" and permission in {"send", "delete", "execute"})
        if not requires:
            return True
        summary = f"{definition['name']} moechte '{tool}' mit Berechtigung '{permission}' ausfuehren."
        approval = self.store.create_approval(run["id"], user, tool, permission, summary)
        self.store.update_run(run["id"], status="WAITING_FOR_APPROVAL")
        waiter = asyncio.get_running_loop().create_future()
        self.approval_waiters[approval["id"]] = waiter
        try:
            allowed = await waiter
            self.store.update_run(run["id"], status="RUNNING")
            return bool(allowed)
        finally:
            self.approval_waiters.pop(approval["id"], None)

    def decide_approval(self, approval, approved):
        waiter = self.approval_waiters.get(approval["id"])
        if waiter and not waiter.done():
            waiter.set_result(bool(approved))

    @staticmethod
    def _usage(agent):
        provider = getattr(agent, "provider", None)
        return {"route": getattr(provider, "last_route", ""), "model": getattr(provider, "last_model", ""),
                "input_tokens": getattr(provider, "total_input_tokens", 0), "output_tokens": getattr(provider, "total_output_tokens", 0),
                "cost": getattr(provider, "total_cost", 0)}

    async def cancel(self, run, user):
        if run["status"] in TERMINAL:
            return run
        self.store.update_run(run["id"], cancel_requested=1)
        self.store.add_event(run["id"], "CANCEL_REQUESTED", "Abbruch angefordert")
        agent = self.agents.get(run["id"])
        if agent:
            agent.stop()
        task = self.tasks.get(run["id"])
        for approval in self.store.list_approvals(user, self.is_admin(user), pending=True):
            if approval["run_id"] == run["id"]:
                self.decide_approval(approval, False)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return self.store.get_run(run["id"], user, self.is_admin(user))

    async def close(self):
        for task in tuple(self.tasks.values()):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks.values()), return_exceptions=True)


def make_router(store, runtime, require_auth, is_admin, available_tools, profiles, audit=None):
    from fastapi import APIRouter, Depends, HTTPException
    from pydantic import BaseModel, Field

    router = APIRouter(prefix="/api/operations", dependencies=[Depends(require_auth)])
    audit = audit or (lambda *args: None)

    class AgentBody(BaseModel):
        name: str = Field(min_length=2, max_length=80)
        description: str = Field(default="", max_length=1000)
        goal: str = Field(min_length=1, max_length=1000)
        instructions: str = Field(default="", max_length=12000)
        profile_id: str = Field(default="", max_length=80)
        tools: list[str] = Field(default_factory=list)
        knowledge: list[str] = Field(default_factory=list)
        autonomy: str = "supervised"
        max_steps: int = 12
        timeout_seconds: int = 600
        token_budget: int = 0
        cost_budget: float = 0
        tool_call_limit: int = 0
        permissions: dict[str, bool] = Field(default_factory=dict)
        enabled: bool = True

    class TaskBody(BaseModel):
        task: str = Field(min_length=1, max_length=20000)

    class ApprovalBody(BaseModel):
        approved: bool

    def guarded(call):
        try:
            return call()
        except OperationsError as error:
            raise HTTPException(error.status, str(error)) from error

    @router.get("/metadata")
    async def metadata(user=Depends(require_auth)):
        return {"tools": available_tools(user), "profiles": profiles(user),
                "autonomy": sorted(AUTONOMY)}

    @router.get("/agents")
    async def agents(user=Depends(require_auth)):
        return guarded(lambda: store.list_agents(user, is_admin(user)))

    @router.post("/agents", status_code=201)
    async def create_agent(body: AgentBody, user=Depends(require_auth)):
        result = guarded(lambda: store.create_agent(body.model_dump(), user, available_tools(user), [p["id"] for p in profiles(user)]))
        audit(user, "agent_created", result["id"])
        return result

    @router.get("/agents/{identifier}")
    async def get_agent(identifier: str, user=Depends(require_auth)):
        return guarded(lambda: store.get_agent(identifier, user, is_admin(user)))

    @router.put("/agents/{identifier}")
    async def update_agent(identifier: str, body: AgentBody, user=Depends(require_auth)):
        result = guarded(lambda: store.update_agent(identifier, body.model_dump(), user, is_admin(user), available_tools(user), [p["id"] for p in profiles(user)]))
        audit(user, "agent_modified", identifier)
        return result

    @router.post("/agents/{identifier}/duplicate", status_code=201)
    async def duplicate_agent(identifier: str, user=Depends(require_auth)):
        source = guarded(lambda: store.get_agent(identifier, user, is_admin(user)))
        source["name"] = (source["name"] + " Kopie")[:80]
        result = guarded(lambda: store.create_agent(source, user, available_tools(user), [p["id"] for p in profiles(user)]))
        audit(user, "agent_created", result["id"])
        return result

    @router.delete("/agents/{identifier}")
    async def delete_agent(identifier: str, user=Depends(require_auth)):
        guarded(lambda: store.delete_agent(identifier, user, is_admin(user)))
        audit(user, "agent_deleted", identifier)
        return {"success": True}

    @router.post("/agents/{identifier}/runs", status_code=202)
    async def run_agent(identifier: str, body: TaskBody, user=Depends(require_auth)):
        definition = guarded(lambda: store.get_agent(identifier, user, is_admin(user)))
        try:
            return await runtime.start_run(definition, body.task, user)
        except OperationsError as error:
            raise HTTPException(error.status, str(error)) from error

    @router.get("/runs")
    async def runs(agent_id: str = "", user=Depends(require_auth)):
        return guarded(lambda: store.list_runs(user, is_admin(user), agent_id))

    @router.get("/runs/{identifier}")
    async def get_run(identifier: str, user=Depends(require_auth)):
        return guarded(lambda: store.get_run(identifier, user, is_admin(user)))

    @router.post("/runs/{identifier}/stop")
    async def stop_run(identifier: str, user=Depends(require_auth)):
        run = guarded(lambda: store.get_run(identifier, user, is_admin(user)))
        result = await runtime.cancel(run, user)
        audit(user, "agent_run_stopped", identifier)
        return result

    @router.get("/notifications")
    async def notifications(unread: bool = False, user=Depends(require_auth)):
        return guarded(lambda: store.notifications(user, unread))

    @router.post("/notifications/{identifier}/read")
    async def read_notification(identifier: str, user=Depends(require_auth)):
        guarded(lambda: store.mark_notification(identifier, user))
        return {"success": True}

    @router.get("/approvals")
    async def approvals(pending: bool = False, user=Depends(require_auth)):
        return guarded(lambda: store.list_approvals(user, is_admin(user), pending))

    @router.post("/approvals/{identifier}/decision")
    async def decide_approval(identifier: str, body: ApprovalBody, user=Depends(require_auth)):
        result = guarded(lambda: store.decide_approval(identifier, user, body.approved, is_admin(user)))
        runtime.decide_approval(result, body.approved)
        audit(user, "approval_decided", identifier)
        return result

    return router
