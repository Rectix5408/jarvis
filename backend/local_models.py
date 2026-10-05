# SPDX-License-Identifier: Apache-2.0
"""Local Ollama management. No shell commands, provider secrets or browser-owned jobs."""
import asyncio
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
from urllib.parse import urlsplit
import uuid

import httpx
import psutil


# Download size is an estimate from the publisher, not a RAM/VRAM requirement.
CATALOG = [
    {"name": "qwen3:4b", "description": "Qwen 3, 4B, guter Local-General-Default",
     "size_estimate": 2_500_000_000, "estimated_ram": 5_000_000_000,
     "context": 8192, "tier": "local_general",
     "capabilities": ["chat", "tools", "structured_output", "code", "reasoning"],
     "recommended_usage": ["chat", "tool_selection", "writing", "small_code"],
     "license": "Apache-2.0", "source": "https://ollama.com/library/qwen3:4b",
     "verified": "2026-10-05"},
    {"name": "qwen3:1.7b", "description": "Sehr schnelles kleines Modell fuer Klassifikation und einfache Antworten",
     "size_estimate": 1_200_000_000, "estimated_ram": 3_000_000_000,
     "context": 8192, "tier": "local_fast",
     "capabilities": ["chat", "classification", "extraction"],
     "recommended_usage": ["classification", "short_chat", "routing_assist"],
     "license": "Apache-2.0", "source": "https://ollama.com/library/qwen3:1.7b",
     "verified": "2026-10-05"},
    {"name": "llama3.2:3b", "description": "Kompaktes Alltagsmodell fuer schnelle lokale Chats",
     "size_estimate": 2_100_000_000, "estimated_ram": 4_500_000_000,
     "context": 8192, "tier": "local_fast",
     "capabilities": ["chat", "tools", "structured_output"],
     "recommended_usage": ["short_chat", "tool_selection", "summaries"],
     "license": "Llama 3.2 Community License", "source": "https://ollama.com/library/llama3.2:3b",
     "verified": "2026-10-05"},
    {"name": "qwen3:14b", "description": "Staerkeres lokales Modell fuer Code und mehrstufiges Reasoning",
     "size_estimate": 9_300_000_000, "estimated_ram": 18_000_000_000,
     "context": 8192, "tier": "local_strong",
     "capabilities": ["chat", "tools", "structured_output", "code", "reasoning"],
     "recommended_usage": ["code", "reasoning", "agent_tasks"],
     "license": "Apache-2.0", "source": "https://ollama.com/library/qwen3:14b",
     "verified": "2026-10-05"},
]
ACTIVE = ("QUEUED", "DOWNLOADING", "VERIFYING", "TESTING")


class ModelError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def runtime_url(value):
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    local = host in {"localhost", "ollama", "host.docker.internal"}
    try:
        address = ipaddress.ip_address(host)
        local = address.is_loopback or (address.is_private and not address.is_unspecified and not address.is_link_local)
    except ValueError:
        pass
    if not local or parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ModelError("Lokale Runtime-URL ungueltig", 503)
    return value.rstrip("/")


def model_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}", value) or ".." in value or "//" in value:
        raise ModelError("Ungueltiger Modellname")
    return value


class LocalModels:
    def __init__(self, directory, url, disk_path="", *, transport=None, audit=None):
        self.directory = Path(directory)
        self.url = runtime_url(url)
        self.disk_path = disk_path
        self.transport = transport
        self.audit = audit or (lambda *args: None)
        self.lock = asyncio.Lock()
        self.tasks = set()
        self.started = False

    def _db(self):
        connection = sqlite3.connect(self.directory / "local-models.sqlite3", timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def start(self):
        if self.started:
            return
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS downloads (id TEXT PRIMARY KEY, model TEXT NOT NULL, owner TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL)")
            # A process restart is not proof that the runtime finished installing.
            db.execute("UPDATE downloads SET status='FAILED', detail='Backend neu gestartet; Installation erneut starten', updated=? WHERE status IN ('QUEUED','DOWNLOADING','VERIFYING','TESTING')", (time.time(),))
            db.execute("PRAGMA user_version=1")
        self.started = True

    def client(self, timeout=15):
        return httpx.AsyncClient(base_url=self.url, timeout=httpx.Timeout(timeout, connect=5),
                                 follow_redirects=False, trust_env=False, transport=self.transport)

    async def request(self, method, path, **kwargs):
        try:
            async with self.client(kwargs.pop("timeout", 15)) as client:
                response = await client.request(method, path, **kwargs)
                response.raise_for_status()
                data = response.json() if response.content else {}
                if not isinstance(data, dict) or data.get("error"):
                    raise ModelError("Ollama hat die Anfrage abgelehnt", 502)
                return data
        except (httpx.HTTPError, ValueError) as error:
            raise ModelError("Lokale Runtime nicht erreichbar oder Antwort ungueltig", 502) from error

    def hardware(self):
        memory = psutil.virtual_memory()
        disk = None
        if self.disk_path:
            try:
                usage = shutil.disk_usage(self.disk_path)
                disk = {"total": usage.total, "free": usage.free, "used": usage.used}
            except OSError:
                pass
        return {"cpu_percent": psutil.cpu_percent(), "cpu_count": psutil.cpu_count(),
                "ram_total": memory.total, "ram_available": memory.available,
                "disk": disk, "gpu": None, "scope": "jarvis_host",
                "disk_error": None if disk else "Modell-Volume nicht messbar; JARVIS_MODEL_DISK_PATH konfigurieren"}

    def catalog(self):
        hw = self.hardware()
        disk_free = ((hw.get("disk") or {}).get("free") if isinstance(hw.get("disk"), dict) else None)
        ram_available = int(hw.get("ram_available") or 0)
        items = []
        for item in CATALOG:
            state = "supported"
            reasons = []
            size = int(item.get("size_estimate") or 0)
            ram = int(item.get("estimated_ram") or 0)
            if disk_free is not None and disk_free < size + 1_000_000_000:
                state = "not_recommended"
                reasons.append("Zu wenig freier Modell-Speicher")
            if ram and ram_available and ram_available < ram:
                state = "high_load" if state != "not_recommended" else state
                reasons.append("Voraussichtlich hohe RAM-Last")
            if state == "supported" and item.get("tier") in {"local_fast", "local_general"}:
                state = "recommended"
            enriched = dict(item)
            enriched["hardware_state"] = state
            enriched["hardware_reasons"] = reasons
            items.append(enriched)
        return items

    async def status(self):
        hardware = self.hardware()
        try:
            version = await self.request("GET", "/api/version")
            tags = await self.request("GET", "/api/tags")
            return {"online": True, "version": version.get("version"), "models": tags.get("models", []), "hardware": hardware}
        except ModelError as error:
            return {"online": False, "error": str(error), "models": [], "hardware": hardware}

    async def details(self, name):
        data = await self.request("POST", "/api/show", json={"model": model_name(name)})
        if data.get("remote_host") or data.get("remote_model"):
            raise ModelError("Cloud-Modelle sind im Local AI Center nicht zulaessig")
        # Do not expose system prompts/templates or incidental filesystem paths.
        return {key: data.get(key) for key in ("details", "capabilities", "model_info", "license")}

    async def test(self, name):
        await self.details(name)
        start = time.monotonic()
        data = await self.request("POST", "/api/chat", timeout=180, json={"model": name, "stream": False,
                                  "think": False, "messages": [{"role": "user", "content": "Antworte nur mit OK."}],
                                  "options": {"num_predict": 16, "num_ctx": 2048}})
        answer = data.get("message", {}).get("content", "").strip()
        if not answer or data.get("done") is not True:
            raise ModelError("Modelltest lieferte keine vollstaendige Textantwort", 502)
        return {"success": True, "response": answer[:200], "latency_ms": round((time.monotonic() - start) * 1000),
                "input_tokens": data.get("prompt_eval_count"), "output_tokens": data.get("eval_count"), "route": "local"}

    def jobs(self):
        with self._db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM downloads ORDER BY created DESC LIMIT 100")]

    def update(self, identifier, status, detail, completed=0, total=0):
        with self._db() as db:
            db.execute("UPDATE downloads SET status=?,detail=?,completed=?,total=?,updated=? WHERE id=?",
                       (status, detail, completed, total, time.time(), identifier))

    async def pull(self, name, owner):
        entry = next((item for item in CATALOG if item["name"] == name), None)
        if not entry:
            raise ModelError("Modell nicht im geprueften Installationskatalog")
        async with self.lock:
            with self._db() as db:
                if db.execute("SELECT 1 FROM downloads WHERE status IN ('QUEUED','DOWNLOADING','VERIFYING','TESTING')").fetchone():
                    raise ModelError("Eine Modellinstallation laeuft bereits", 409)
                disk = self.hardware()["disk"]
                if not disk:
                    raise ModelError("Freier Platz auf dem Modell-Volume kann nicht geprueft werden", 409)
                if disk["free"] < entry["size_estimate"] + 1_000_000_000:
                    raise ModelError("Zu wenig freier Speicher auf dem Modell-Volume", 409)
                identifier = uuid.uuid4().hex
                now = time.time()
                db.execute("INSERT INTO downloads(id,model,owner,status,detail,created,updated) VALUES(?,?,?,'QUEUED','Wartet auf Runtime',?,?)", (identifier, name, owner, now, now))
            task = asyncio.create_task(self._pull(identifier, name, owner))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            return {"id": identifier, "status": "QUEUED"}

    async def _pull(self, identifier, name, owner):
        success = False
        try:
            async with self.client(300) as client:
                async with client.stream("POST", "/api/pull", json={"model": name, "stream": True}) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if len(line) > 32768:
                            raise ModelError("Ungueltige Download-Antwort")
                        item = json.loads(line)
                        if item.get("error"):
                            raise ModelError("Download von Ollama abgelehnt")
                        status = str(item.get("status", ""))
                        completed = max(0, int(item.get("completed", 0)))
                        total = max(0, int(item.get("total", 0)))
                        if completed > total:
                            raise ModelError("Ungueltiger Download-Fortschritt")
                        # Ollama reports individual layers, not the overall manifest size.
                        phase = "VERIFYING" if status.startswith(("verifying", "writing", "removing")) else "DOWNLOADING"
                        self.update(identifier, phase, status[:160], completed, total)
                        if status == "success":
                            success = True
                            break
            if not success:
                raise ModelError("Download vor Abschluss unterbrochen")
            self.update(identifier, "TESTING", "Lokaler Antworttest")
            await self.test(name)
            self.update(identifier, "READY", "Installiert und getestet")
            self.audit(owner, "model_installed", name)
        except asyncio.CancelledError:
            self.update(identifier, "FAILED", "Installation unterbrochen; erneut starten")
            raise
        except Exception:
            # Runtime errors can contain credentials or paths; persist a bounded safe message.
            self.update(identifier, "FAILED", "Installation oder Antworttest fehlgeschlagen; Runtime pruefen")
            self.audit(owner, "model_failed", name)

    async def delete(self, name, owner, referenced):
        model_name(name)
        async with self.lock:
            if referenced(name):
                raise ModelError("Modell wird von einem Profil verwendet; Profil zuerst umstellen", 409)
            if any(job["status"] in ACTIVE for job in self.jobs()):
                raise ModelError("Waehren einer Installation kann kein Modell entfernt werden", 409)
            await self.request("DELETE", "/api/delete", json={"model": name})
            self.audit(owner, "model_deleted", name)

    async def close(self):
        for task in tuple(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)


def make_router(service, require_admin, config):
    from fastapi import APIRouter, Depends, HTTPException
    from pydantic import BaseModel, Field

    class ModelRequest(BaseModel):
        model: str = Field(min_length=1, max_length=160)

    router = APIRouter(prefix="/api/local-ai", dependencies=[Depends(require_admin)])

    async def guarded(operation):
        try:
            return await operation
        except ModelError as error:
            raise HTTPException(error.status, str(error)) from error

    @router.get("/status")
    async def status():
        return await service.status()

    @router.get("/catalog")
    async def catalog():
        return service.catalog()

    @router.get("/downloads")
    async def downloads():
        return service.jobs()

    @router.get("/routing")
    async def routing():
        return {"mode": config.MODEL_ROUTING_MODE, "local_model": config.LOCAL_MODEL,
                "smart_local_complexity_limit": getattr(config, "SMART_LOCAL_COMPLEXITY_LIMIT", 0.86),
                "smart_tool_cloud_complexity": getattr(config, "SMART_TOOL_CLOUD_COMPLEXITY", 0.55),
                "modes": ["local_only", "local_first", "smart", "cloud"]}

    class RoutingRequest(BaseModel):
        mode: str = Field(min_length=5, max_length=20)
        local_model: str = Field(default="", max_length=160)
        smart_local_complexity_limit: float | None = None
        smart_tool_cloud_complexity: float | None = None

    @router.post("/routing")
    async def set_routing(body: RoutingRequest, user=Depends(require_admin)):
        mode = body.mode.lower()
        if mode not in {"local_only", "local_first", "smart", "cloud"}:
            raise HTTPException(400, "Unbekannter Routing-Modus")
        selected = model_name(body.local_model) if body.local_model else config.LOCAL_MODEL
        if mode != "cloud":
            if not selected:
                raise HTTPException(409, "Zuerst ein lokales Modell auswaehlen")
            await guarded(service.details(selected))
        settings = {"model_routing_mode": mode, "local_model": selected}
        if body.smart_local_complexity_limit is not None:
            settings["smart_local_complexity_limit"] = max(0.05, min(float(body.smart_local_complexity_limit), 1.0))
        if body.smart_tool_cloud_complexity is not None:
            settings["smart_tool_cloud_complexity"] = max(0.05, min(float(body.smart_tool_cloud_complexity), 1.0))
        config.save_global_settings(settings)
        service.audit(user, "model_routing_changed", selected or mode)
        return {"success": True, "mode": config.MODEL_ROUTING_MODE, "local_model": config.LOCAL_MODEL,
                "smart_local_complexity_limit": getattr(config, "SMART_LOCAL_COMPLEXITY_LIMIT", 0.86),
                "smart_tool_cloud_complexity": getattr(config, "SMART_TOOL_CLOUD_COMPLEXITY", 0.55)}

    @router.post("/details")
    async def details(body: ModelRequest):
        return await guarded(service.details(body.model))

    @router.post("/test")
    async def test(body: ModelRequest):
        return await guarded(service.test(body.model))

    @router.post("/pull", status_code=202)
    async def pull(body: ModelRequest, user=Depends(require_admin)):
        return await guarded(service.pull(body.model, user))

    @router.post("/activate")
    async def activate(body: ModelRequest, user=Depends(require_admin)):
        async with service.lock:
            await guarded(service.test(body.model))
            config.save_global_settings({"local_model": body.model})
            service.audit(user, "model_activated", body.model)
            return {"success": True, "local_model": body.model, "scope": "model_router"}

    @router.post("/delete")
    async def delete(body: ModelRequest, user=Depends(require_admin)):
        await guarded(service.delete(body.model, user, lambda name: config.LOCAL_MODEL == name or any(p.get("model") == name for p in config.profiles)))
        return {"success": True}

    return router
