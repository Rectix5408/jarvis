# SPDX-License-Identifier: Apache-2.0
"""Persistent, validated workflow graphs and cancellable executions."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from backend.operations import OperationsError, _owner


NODE_TYPES = {"TRIGGER", "AGENT", "LLM", "TOOL", "SKILL", "MCP", "KNOWLEDGE",
              "CONDITION", "PARALLEL", "LOOP", "APPROVAL", "TRANSFORM", "HTTP",
              "DELAY", "NOTIFICATION", "RESULT"}
EXECUTABLE_TYPES = {"TRIGGER", "AGENT", "LLM", "TOOL", "SKILL", "MCP", "KNOWLEDGE",
                    "CONDITION", "TRANSFORM", "DELAY", "NOTIFICATION", "RESULT"}
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}


class WorkflowStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.started = False

    def _db(self):
        db = sqlite3.connect(self.directory / "workflows.sqlite3", timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def start(self):
        if self.started:
            return
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS workflows(
              id TEXT PRIMARY KEY,owner TEXT NOT NULL,name TEXT NOT NULL,description TEXT NOT NULL,
              nodes_json TEXT NOT NULL,edges_json TEXT NOT NULL,enabled INTEGER NOT NULL,
              created REAL NOT NULL,updated REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS workflows_owner_idx ON workflows(owner,updated DESC);
            CREATE TABLE IF NOT EXISTS workflow_runs(
              id TEXT PRIMARY KEY,workflow_id TEXT NOT NULL,owner TEXT NOT NULL,input TEXT NOT NULL,
              status TEXT NOT NULL,current_node TEXT NOT NULL,result TEXT NOT NULL,error TEXT NOT NULL,
              started REAL NOT NULL,finished REAL,cancel_requested INTEGER NOT NULL DEFAULT 0,
              FOREIGN KEY(workflow_id) REFERENCES workflows(id) ON DELETE RESTRICT);
            CREATE INDEX IF NOT EXISTS workflow_runs_owner_idx ON workflow_runs(owner,started DESC);
            CREATE TABLE IF NOT EXISTS workflow_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,ts REAL NOT NULL,
              node_id TEXT NOT NULL,kind TEXT NOT NULL,message TEXT NOT NULL,data_json TEXT NOT NULL,
              FOREIGN KEY(run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE);
            """)
            now = time.time()
            db.execute("UPDATE workflow_runs SET status='INTERRUPTED',finished=?,error='Backend neu gestartet' WHERE status IN ('QUEUED','RUNNING')", (now,))
        self.started = True

    @staticmethod
    def validate(spec, agent_ids=None):
        name = str(spec.get("name") or "").strip()
        nodes, edges = spec.get("nodes") or [], spec.get("edges") or []
        if not 2 <= len(name) <= 80 or not isinstance(nodes, list) or not isinstance(edges, list):
            raise OperationsError("Workflow-Name oder Graph ungueltig")
        ids = [str(node.get("id") or "") for node in nodes if isinstance(node, dict)]
        if len(ids) != len(nodes) or len(set(ids)) != len(ids) or any(not value for value in ids):
            raise OperationsError("Node-IDs fehlen oder sind doppelt")
        by_id = {node["id"]: node for node in nodes}
        unknown = {str(node.get("type") or "").upper() for node in nodes} - NODE_TYPES
        if unknown:
            raise OperationsError("Unbekannte Node-Typen: " + ", ".join(sorted(unknown)))
        if sum(str(node.get("type")).upper() == "TRIGGER" for node in nodes) != 1:
            raise OperationsError("Genau ein TRIGGER ist erforderlich")
        if not any(str(node.get("type")).upper() == "RESULT" for node in nodes):
            raise OperationsError("Ein RESULT ist erforderlich")
        adjacency = {identifier: [] for identifier in ids}
        indegree = {identifier: 0 for identifier in ids}
        for edge in edges:
            source, target = str(edge.get("source") or ""), str(edge.get("target") or "")
            if source not in by_id or target not in by_id or source == target:
                raise OperationsError("Kante verweist auf ungueltige Nodes")
            adjacency[source].append(target); indegree[target] += 1
        trigger = next(node["id"] for node in nodes if str(node.get("type")).upper() == "TRIGGER")
        if indegree[trigger]:
            raise OperationsError("TRIGGER darf keinen Eingang haben")
        queue = [key for key, value in indegree.items() if value == 0]
        ordered = []
        while queue:
            current = queue.pop(0); ordered.append(current)
            for target in adjacency[current]:
                indegree[target] -= 1
                if indegree[target] == 0: queue.append(target)
        if len(ordered) != len(nodes):
            raise OperationsError("Workflow enthaelt einen Zyklus")
        reachable, stack = set(), [trigger]
        while stack:
            current = stack.pop()
            if current in reachable: continue
            reachable.add(current); stack.extend(adjacency[current])
        if reachable != set(ids):
            raise OperationsError("Nicht alle Nodes sind mit dem TRIGGER verbunden")
        allowed_agents = set(agent_ids or [])
        for node in nodes:
            kind, config = str(node.get("type")).upper(), node.get("config") or {}
            if kind in {"AGENT", "LLM", "TOOL", "SKILL", "MCP", "KNOWLEDGE"} and str(config.get("agent_id") or "") not in allowed_agents:
                raise OperationsError(f"Node {node['id']}: Agent fehlt oder ist nicht erlaubt")
            if kind == "DELAY" and not 0 <= float(config.get("seconds", 0)) <= 3600:
                raise OperationsError(f"Node {node['id']}: Delay ungueltig")
        return {"name": name, "description": str(spec.get("description") or "")[:1000],
                "nodes": nodes, "edges": edges, "enabled": spec.get("enabled") is not False,
                "order": ordered}

    @staticmethod
    def _workflow(row):
        value = dict(row); value["nodes"] = json.loads(value.pop("nodes_json")); value["edges"] = json.loads(value.pop("edges_json")); value["enabled"] = bool(value["enabled"]); return value

    def list(self, user, admin=False):
        with self._db() as db:
            rows = db.execute("SELECT * FROM workflows ORDER BY updated DESC" if admin else "SELECT * FROM workflows WHERE owner=? ORDER BY updated DESC", () if admin else (_owner(user),)).fetchall()
        return [self._workflow(row) for row in rows]

    def get(self, identifier, user, admin=False):
        with self._db() as db: row = db.execute("SELECT * FROM workflows WHERE id=?", (identifier,)).fetchone()
        if not row or (not admin and row["owner"] != _owner(user)): raise OperationsError("Workflow nicht gefunden", 404)
        return self._workflow(row)

    def save(self, spec, user, agent_ids, identifier="", admin=False):
        data = self.validate(spec, agent_ids); now = time.time()
        if identifier:
            self.get(identifier, user, admin)
            with self._db() as db: db.execute("UPDATE workflows SET name=?,description=?,nodes_json=?,edges_json=?,enabled=?,updated=? WHERE id=?", (data["name"],data["description"],json.dumps(data["nodes"]),json.dumps(data["edges"]),int(data["enabled"]),now,identifier))
        else:
            identifier = uuid.uuid4().hex
            with self._db() as db: db.execute("INSERT INTO workflows VALUES(?,?,?,?,?,?,?,?,?)", (identifier,_owner(user),data["name"],data["description"],json.dumps(data["nodes"]),json.dumps(data["edges"]),int(data["enabled"]),now,now))
        return self.get(identifier, user, admin)

    def create_run(self, workflow, value, user):
        identifier, now = uuid.uuid4().hex, time.time()
        with self._db() as db:
            db.execute("INSERT INTO workflow_runs VALUES(?,?,?,?, 'QUEUED','','','',?,NULL,0)", (identifier,workflow["id"],_owner(user),str(value)[:20000],now))
            db.execute("INSERT INTO workflow_events(run_id,ts,node_id,kind,message,data_json) VALUES(?,?,?,?,?,?)", (identifier,now,"","RUN_QUEUED","Workflow eingeplant","{}"))
        return self.get_run(identifier,user)

    def update_run(self, identifier, **values):
        allowed={"status","current_node","result","error","finished","cancel_requested"}; values={k:v for k,v in values.items() if k in allowed}
        with self._db() as db: db.execute("UPDATE workflow_runs SET "+",".join(f"{k}=?" for k in values)+" WHERE id=?", (*values.values(),identifier))

    def event(self, run_id, node_id, kind, message, data=None):
        with self._db() as db: db.execute("INSERT INTO workflow_events(run_id,ts,node_id,kind,message,data_json) VALUES(?,?,?,?,?,?)", (run_id,time.time(),node_id,kind,str(message)[:1000],json.dumps(data or {})))

    def get_run(self, identifier, user, admin=False):
        with self._db() as db:
            row=db.execute("SELECT * FROM workflow_runs WHERE id=?",(identifier,)).fetchone(); events=db.execute("SELECT * FROM workflow_events WHERE run_id=? ORDER BY id",(identifier,)).fetchall() if row else []
        if not row or (not admin and row["owner"] != _owner(user)): raise OperationsError("Workflow-Lauf nicht gefunden",404)
        value=dict(row); value["cancel_requested"]=bool(value["cancel_requested"]); value["events"]=[{**dict(e),"data":json.loads(e["data_json"])} for e in events]
        for event in value["events"]: event.pop("data_json",None)
        return value

    def list_runs(self,user,admin=False):
        with self._db() as db: rows=db.execute("SELECT * FROM workflow_runs ORDER BY started DESC LIMIT 100" if admin else "SELECT * FROM workflow_runs WHERE owner=? ORDER BY started DESC LIMIT 100",() if admin else (_owner(user),)).fetchall()
        return [dict(row) for row in rows]


class WorkflowRuntime:
    def __init__(self, store, operation_store, agent_runtime, is_admin):
        self.store,self.operation_store,self.agent_runtime,self.is_admin=store,operation_store,agent_runtime,is_admin; self.tasks={}

    async def start(self, workflow, value, user):
        if not workflow["enabled"]: raise OperationsError("Workflow ist deaktiviert",409)
        run=self.store.create_run(workflow,value,user); task=asyncio.create_task(self._execute(run,workflow,value,user)); self.tasks[run["id"]]=task; task.add_done_callback(lambda _:self.tasks.pop(run["id"],None)); return run

    async def _execute(self, run, workflow, value, user):
        try:
            data={"input":value,"value":value}; validated=self.store.validate(workflow,[a["id"] for a in self.operation_store.list_agents(user,self.is_admin(user))]); by_id={n["id"]:n for n in workflow["nodes"]}
            self.store.update_run(run["id"],status="RUNNING")
            for node_id in validated["order"]:
                node=by_id[node_id]; kind=str(node["type"]).upper(); config=node.get("config") or {}
                self.store.update_run(run["id"],current_node=node_id); self.store.event(run["id"],node_id,"NODE_STARTED",kind)
                if kind in {"AGENT","LLM","TOOL","SKILL","MCP","KNOWLEDGE"}:
                    definition=self.operation_store.get_agent(config["agent_id"],user,self.is_admin(user)); child=await self.agent_runtime.start_run(definition,str(data["value"]),user); await self.agent_runtime.tasks[child["id"]]; child=self.operation_store.get_run(child["id"],user,self.is_admin(user))
                    if child["status"]!="COMPLETED": raise RuntimeError(child["error"] or "Agent-Node fehlgeschlagen")
                    data["value"]=child["result"]
                elif kind=="DELAY": await asyncio.sleep(float(config.get("seconds",0)))
                elif kind=="TRANSFORM": data["value"]=str(config.get("template","{value}")).replace("{value}",str(data["value"]))
                elif kind=="NOTIFICATION": self.operation_store.notify(user,"workflow",str(config.get("title","Workflow")),str(data["value"])[:500],run["id"])
                elif kind in NODE_TYPES-EXECUTABLE_TYPES: raise RuntimeError(f"Node-Typ {kind} ist noch nicht ausfuehrbar")
                self.store.event(run["id"],node_id,"NODE_COMPLETED",kind)
            self.store.update_run(run["id"],status="COMPLETED",result=str(data["value"])[:100000],finished=time.time(),current_node="")
        except asyncio.CancelledError: self.store.update_run(run["id"],status="CANCELLED",error="Abgebrochen",finished=time.time())
        except Exception as error: self.store.update_run(run["id"],status="FAILED",error=str(error)[:4000],finished=time.time())

    async def close(self):
        for task in self.tasks.values(): task.cancel()
        if self.tasks: await asyncio.gather(*self.tasks.values(),return_exceptions=True)


def make_router(store, runtime, operation_store, require_auth, is_admin):
    from fastapi import APIRouter, Depends, HTTPException
    from pydantic import BaseModel, Field
    router = APIRouter(prefix="/api/workflows", dependencies=[Depends(require_auth)])

    class WorkflowBody(BaseModel):
        name: str = Field(min_length=2,max_length=80)
        description: str = Field(default="",max_length=1000)
        nodes: list[dict]
        edges: list[dict]
        enabled: bool = True

    class RunBody(BaseModel):
        input: str = Field(default="",max_length=20000)

    def guarded(call):
        try: return call()
        except OperationsError as error: raise HTTPException(error.status,str(error)) from error

    def agent_ids(user): return [item["id"] for item in operation_store.list_agents(user,is_admin(user))]

    @router.get("")
    async def workflows(user=Depends(require_auth)): return guarded(lambda:store.list(user,is_admin(user)))

    @router.post("",status_code=201)
    async def create(body:WorkflowBody,user=Depends(require_auth)): return guarded(lambda:store.save(body.model_dump(),user,agent_ids(user)))

    @router.put("/{identifier}")
    async def update(identifier:str,body:WorkflowBody,user=Depends(require_auth)): return guarded(lambda:store.save(body.model_dump(),user,agent_ids(user),identifier,is_admin(user)))

    @router.get("/runs")
    async def runs(user=Depends(require_auth)): return store.list_runs(user,is_admin(user))

    @router.get("/runs/{identifier}")
    async def run(identifier:str,user=Depends(require_auth)): return guarded(lambda:store.get_run(identifier,user,is_admin(user)))

    @router.post("/{identifier}/runs",status_code=202)
    async def start(identifier:str,body:RunBody,user=Depends(require_auth)):
        workflow=guarded(lambda:store.get(identifier,user,is_admin(user)))
        try: return await runtime.start(workflow,body.input,user)
        except OperationsError as error: raise HTTPException(error.status,str(error)) from error
    return router
