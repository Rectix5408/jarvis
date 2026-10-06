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
import sys
import time
from urllib.parse import urlsplit
import uuid

import httpx
import psutil


# Download size is an estimate from the publisher, not a RAM/VRAM requirement.
CATALOG = [
    {"name": "qwen3.5:2b", "description": "Schnelles CPU-Modell fuer einfache lokale Aufgaben",
     "size_estimate": 2_700_000_000, "estimated_ram": 3_500_000_000,
     "context": 8192, "tier": "local_fast", "capabilities": ["chat", "classification", "extraction", "tools"],
     "recommended_usage": ["classification", "short_chat", "routing_assist"],
     "license": "Apache-2.0", "source": "https://ollama.com/library/qwen3.5:2b", "verified": "2026-10-06"},
    {"name": "qwen3.5:4b", "description": "Kompaktes General-Modell fuer den CPU-Betrieb",
     "size_estimate": 3_400_000_000, "estimated_ram": 5_000_000_000,
     "context": 8192, "tier": "local_general", "capabilities": ["chat", "tools", "structured_output", "code", "reasoning"],
     "recommended_usage": ["chat", "tool_selection", "writing", "small_code"],
     "license": "Apache-2.0", "source": "https://ollama.com/library/qwen3.5:4b", "verified": "2026-10-06"},
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
MUTATING = ("QUEUED", "VALIDATING", "DOWNLOADING", "VERIFYING", "CANCELLING")
REGISTRY_VERSION = 1
CAPABILITIES = {"chat", "reasoning", "tool_calling", "structured_output", "vision", "embedding"}


class ModelError(Exception):
    def __init__(self, message, status=400, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


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


class ModelRegistry:
    """Normalized, curated model metadata; no remote/user supplied sources."""

    def __init__(self, entries=None):
        self._entries = {}
        for raw in entries or CATALOG:
            item = self._normalize(raw)
            if item["id"] in self._entries:
                raise ValueError("duplicate registry model id")
            self._entries[item["id"]] = item

    @staticmethod
    def _normalize(raw):
        runtime_id = model_name(raw.get("runtime_id") or raw.get("name"))
        legacy = set(raw.get("capabilities") or [])
        capabilities = {
            "chat": True if "chat" in legacy else None,
            "reasoning": True if "reasoning" in legacy else None,
            "tool_calling": True if "tools" in legacy else None,
            "structured_output": True if "structured_output" in legacy else None,
            "vision": True if "vision" in legacy else None,
            "embedding": True if "embedding" in legacy else None,
        }
        item = {
            "id": runtime_id, "runtime_id": runtime_id,
            "display_name": raw.get("display_name") or runtime_id,
            "family": raw.get("family"), "parameters": raw.get("parameters"),
            "quantization": raw.get("quantization"),
            "context_length": raw.get("context_length") or raw.get("context"),
            "disk_size_bytes": raw.get("disk_size_bytes") or raw.get("size_estimate"),
            "estimated_ram_bytes": raw.get("estimated_ram_bytes") or raw.get("estimated_ram"),
            "estimated_vram_bytes": raw.get("estimated_vram_bytes"), "runtime": "ollama",
            "source": raw.get("source"), "license": raw.get("license"),
            "capabilities": capabilities,
            "recommended_roles": raw.get("recommended_roles") or [str(raw.get("tier", "")).removeprefix("local_")],
            "min_ram_bytes": raw.get("min_ram_bytes"),
            "recommended_ram_bytes": raw.get("recommended_ram_bytes") or raw.get("estimated_ram"),
            "gpu_required": bool(raw.get("gpu_required", False)),
            "architecture_support": raw.get("architecture_support") or ["x86_64", "amd64", "aarch64", "arm64"],
            "verified": raw.get("verified"), "registry_version": REGISTRY_VERSION,
            # Legacy fields keep Stage-4A UI/API consumers compatible.
            "name": runtime_id, "description": raw.get("description", ""),
            "size_estimate": raw.get("size_estimate"), "estimated_ram": raw.get("estimated_ram"),
            "context": raw.get("context"), "tier": raw.get("tier"),
            "recommended_usage": raw.get("recommended_usage", []),
        }
        return item

    def get(self, identifier):
        return self._entries.get(model_name(identifier))

    def require(self, identifier):
        item = self.get(identifier)
        if not item:
            raise ModelError("Modell ist nicht in der kuratierten Registry", 404, "MODEL_NOT_FOUND")
        return item

    def all(self):
        return [dict(item) for item in self._entries.values()]


class CompatibilityEngine:
    def __init__(self, memory_reserve_percent=25, disk_reserve_percent=20):
        self.memory_reserve_percent = memory_reserve_percent
        self.disk_reserve_percent = disk_reserve_percent

    def evaluate(self, model, hardware, *, runtime_online=True, role=None):
        reasons = []
        memory, disk = hardware.get("memory") or {}, hardware.get("disk")
        architecture = str(hardware.get("architecture") or "").lower()
        supported = {str(value).lower() for value in model.get("architecture_support") or []}
        if not runtime_online:
            reasons.append("RUNTIME_UNAVAILABLE")
        if supported and architecture and architecture not in supported:
            reasons.append("UNSUPPORTED_ARCHITECTURE")
        if model.get("gpu_required") and (hardware.get("gpu") or {}).get("type") == "none":
            reasons.append("GPU_REQUIRED")
        required_ram = model.get("estimated_ram_bytes") or model.get("min_ram_bytes")
        if required_ram and memory.get("total"):
            reserve = int(memory["total"] * self.memory_reserve_percent / 100)
            if required_ram > max(0, memory["total"] - reserve):
                reasons.append("INSUFFICIENT_RAM_HARD")
            elif required_ram > max(0, memory.get("available", 0) - reserve):
                reasons.append("INSUFFICIENT_RAM")
        elif not required_ram:
            reasons.append("UNKNOWN_REQUIREMENTS")
        required_disk = model.get("disk_size_bytes")
        if required_disk and disk:
            available_disk = int(disk.get("available", disk.get("free", 0)))
            reserve = int(disk.get("total", available_disk) * self.disk_reserve_percent / 100)
            if required_disk > max(0, available_disk - reserve):
                reasons.append("INSUFFICIENT_DISK")
        elif required_disk and not disk:
            reasons.append("UNKNOWN_REQUIREMENTS")
        if role and role not in (model.get("recommended_roles") or []):
            reasons.append("CAPABILITY_MISMATCH")
        hard = {"RUNTIME_UNAVAILABLE", "UNSUPPORTED_ARCHITECTURE", "GPU_REQUIRED",
                "INSUFFICIENT_RAM_HARD", "INSUFFICIENT_DISK"}
        state = "incompatible" if hard.intersection(reasons) else "warning" if reasons else "compatible"
        messages = {"RUNTIME_UNAVAILABLE": "Lokale Runtime ist nicht erreichbar.",
                    "UNSUPPORTED_ARCHITECTURE": "CPU-Architektur wird nicht unterstuetzt.",
                    "GPU_REQUIRED": "Das Modell benoetigt eine GPU.",
                    "INSUFFICIENT_RAM_HARD": "Das Modell passt nicht in den RAM dieses Systems.",
                    "INSUFFICIENT_RAM": "Aktuell ist nicht genug sicherer RAM verfuegbar.",
                    "INSUFFICIENT_DISK": "Nicht genug sicherer Modellspeicher verfuegbar.",
                    "UNKNOWN_REQUIREMENTS": "Hardwareanforderungen sind unvollstaendig.",
                    "CAPABILITY_MISMATCH": "Modell ist fuer diese Rolle nicht kuratiert."}
        return {"state": state, "reasons": reasons, "message": " ".join(messages[r] for r in reasons) or "Kompatibel."}


class HardwareInventory:
    """Best-effort host inventory without executing external commands."""

    def __init__(self, model_path=""):
        self.model_path = Path(model_path) if model_path else None

    @staticmethod
    def _cpu_info():
        model, flags = None, set()
        try:
            for line in Path("/proc/cpuinfo").read_text(errors="replace").splitlines():
                key, separator, value = line.partition(":")
                if not separator:
                    continue
                key = key.strip().lower()
                if key in {"model name", "hardware"} and not model:
                    model = value.strip()
                elif key in {"flags", "features"}:
                    flags.update(value.strip().lower().split())
        except OSError:
            pass
        return model, flags

    @staticmethod
    def _gpu_info():
        devices = []
        drm = Path("/sys/class/drm")
        if drm.is_dir():
            for vendor_file in drm.glob("card*/device/vendor"):
                try:
                    vendor = vendor_file.read_text().strip().lower()
                except OSError:
                    continue
                kind = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}.get(vendor)
                if kind and kind not in devices:
                    devices.append(kind)
        if Path("/proc/driver/nvidia/gpus").is_dir() and "nvidia" not in devices:
            devices.append("nvidia")
        return {"type": devices[0] if devices else "none", "devices": devices, "vram_total": None}

    def collect(self):
        memory, swap = psutil.virtual_memory(), psutil.swap_memory()
        model, flags = self._cpu_info()
        disk, disk_error = None, None
        if self.model_path:
            try:
                usage = shutil.disk_usage(self.model_path)
                disk = {"path": str(self.model_path), "total": usage.total,
                        "available": usage.free, "free": usage.free, "used": usage.used}
            except OSError:
                disk_error = "Modell-Volume nicht messbar; JARVIS_MODEL_DISK_PATH konfigurieren"
        else:
            disk_error = "JARVIS_MODEL_DISK_PATH ist nicht konfiguriert"
        return {"platform": sys.platform,
                "architecture": os.uname().machine if hasattr(os, "uname") else None,
                "cpu": {"model": model, "logical_cpus": psutil.cpu_count(),
                        "physical_cpus": psutil.cpu_count(logical=False),
                        "avx": "avx" in flags, "avx2": "avx2" in flags},
                "memory": {"total": memory.total, "available": memory.available, "used": memory.used,
                           "swap_total": swap.total, "swap_available": max(0, swap.total - swap.used)},
                "disk": disk, "disk_error": disk_error, "gpu": self._gpu_info(), "scope": "jarvis_host"}


class LocalModels:
    def __init__(self, directory, url, disk_path="", *, transport=None, audit=None,
                 memory_reserve_percent=25, disk_reserve_percent=20,
                 max_loaded_models=1, role_models=None, inventory=None):
        self.directory = Path(directory)
        self.url = runtime_url(url)
        self.disk_path = disk_path
        self.transport = transport
        self.audit = audit or (lambda *args: None)
        self.lock = asyncio.Lock()
        self.inventory = inventory or HardwareInventory(disk_path)
        self.memory_reserve_percent = max(20, min(int(memory_reserve_percent), 90))
        self.disk_reserve_percent = max(15, min(int(disk_reserve_percent), 90))
        self.max_loaded_models = max(1, int(max_loaded_models))
        self.role_models = dict(role_models or {})
        self.registry = ModelRegistry()
        self.compatibility_engine = CompatibilityEngine(self.memory_reserve_percent, self.disk_reserve_percent)
        self.health = {}
        self.tasks = {}
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
            columns = {row[1] for row in db.execute("PRAGMA table_info(downloads)")}
            additions = {"started": "REAL", "finished": "REAL", "error_code": "TEXT", "digest": "TEXT"}
            for column, kind in additions.items():
                if column not in columns:
                    db.execute(f"ALTER TABLE downloads ADD COLUMN {column} {kind}")
            # A process restart is not proof that the runtime finished installing.
            db.execute("UPDATE downloads SET status='FAILED', detail='Backend neu gestartet; Runtime-Job muss neu gestartet werden', error_code='RECOVERY_INTERRUPTED', finished=?, updated=? WHERE status IN ('QUEUED','VALIDATING','DOWNLOADING','VERIFYING','CANCELLING','TESTING')", (time.time(), time.time()))
            db.execute("PRAGMA user_version=2")
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
        inventory = self.inventory.collect()
        memory = inventory["memory"]
        return {**inventory, "cpu_percent": psutil.cpu_percent(),
                "cpu_count": inventory["cpu"]["logical_cpus"],
                "ram_total": memory["total"], "ram_available": memory["available"]}

    def _entry(self, name):
        return self.registry.get(name)

    def ensure_disk_capacity(self, required_bytes):
        disk = self.hardware().get("disk")
        if not disk:
            raise ModelError("Modell-Speicher kann nicht sicher geprueft werden", 409, "MODEL_INSUFFICIENT_DISK")
        available = int(disk.get("available", disk.get("free", 0)))
        total = int(disk.get("total", available))
        reserve = int(total * self.disk_reserve_percent / 100)
        if available - max(0, int(required_bytes)) < reserve:
            raise ModelError("Nicht genug sicherer Modell-Speicher verfuegbar", 409, "MODEL_INSUFFICIENT_DISK")
        return True

    def ensure_memory_capacity(self, name, installed=None):
        memory = self.hardware()["memory"]
        entry = self._entry(name)
        installed_model = next((item for item in (installed or []) if item.get("name") == name), None)
        estimate = int((entry or {}).get("estimated_ram") or ((installed_model or {}).get("size") or 0) * 1.25)
        reserve = int(memory["total"] * self.memory_reserve_percent / 100)
        safe_available = max(0, memory["available"] - reserve)
        if not estimate or estimate > safe_available:
            raise ModelError("Modell kann nicht mit ausreichender RAM-Reserve geladen werden", 409,
                             "MODEL_INSUFFICIENT_MEMORY")
        return {"estimated_ram": estimate, "safe_available": safe_available, "reserve": reserve}

    def catalog(self):
        hw = self.hardware()
        disk_free = ((hw.get("disk") or {}).get("free") if isinstance(hw.get("disk"), dict) else None)
        ram_available = int(hw.get("ram_available") or 0)
        items = []
        for item in self.registry.all():
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

    def registry_entries(self, runtime_online=True):
        hardware = self.hardware()
        return [{**item, "compatibility": self.compatibility_engine.evaluate(
            item, hardware, runtime_online=runtime_online)} for item in self.registry.all()]

    async def status(self):
        hardware = self.hardware()
        try:
            version = await self.request("GET", "/api/version")
            installed, running = await asyncio.gather(self.list_installed_models(), self.list_running_models())
            return {"runtime": "ollama", "online": True, "version": version.get("version"),
                    "host": urlsplit(self.url).hostname, "models": installed, "installed_models": installed,
                    "running_models": running, "roles": self.role_status(installed, running),
                    "health": self.health, "max_loaded_models": self.max_loaded_models,
                    "hardware": hardware}
        except ModelError as error:
            return {"runtime": "ollama", "online": False, "error": str(error), "models": [],
                    "installed_models": [], "running_models": [], "roles": self.role_status([], []),
                    "health": self.health, "max_loaded_models": self.max_loaded_models,
                    "hardware": hardware}

    async def health_check(self):
        data = await self.request("GET", "/api/version")
        return {"runtime": "ollama", "healthy": True, "version": data.get("version")}

    async def list_installed_models(self):
        data = await self.request("GET", "/api/tags")
        return data.get("models", []) if isinstance(data.get("models", []), list) else []

    async def list_running_models(self):
        data = await self.request("GET", "/api/ps")
        return data.get("models", []) if isinstance(data.get("models", []), list) else []

    def role_status(self, installed, running):
        installed_names = {item.get("name") for item in installed}
        running_names = {item.get("name") for item in running}
        result = {}
        for role in ("fast", "general", "strong"):
            name = self.role_models.get(role) or ""
            result[role] = {"model": name or None, "installed": bool(name and name in installed_names),
                            "running": bool(name and name in running_names),
                            "healthy": self.health.get(name, {}).get("healthy") if name else None,
                            "compatible": bool(name and self._entry(name)),
                            "cloud_fallback_required": role == "strong" and not name}
        return result

    def _record_health(self, name, success):
        now = time.time()
        state = self.health.setdefault(name, {"healthy": None, "last_success": None, "last_failure": None,
                                               "failure_count": 0, "cooldown_until": None})
        state["healthy"] = success
        if success:
            state.update(last_success=now, failure_count=0, cooldown_until=None)
        else:
            state["last_failure"] = now
            state["failure_count"] += 1
            state["cooldown_until"] = now + min(300, 15 * state["failure_count"])

    async def load_model(self, name):
        name = model_name(name)
        async with self.lock:
            try:
                installed = await self.list_installed_models()
                if name not in {item.get("name") for item in installed}:
                    raise ModelError("Modell ist nicht lokal installiert", 404)
                running = await self.list_running_models()
                if name in {item.get("name") for item in running}:
                    return {"success": True, "model": name, "running": True}
                if len(running) >= self.max_loaded_models:
                    for item in running:
                        running_name = item.get("name")
                        if running_name and running_name != name:
                            await self._unload_model(running_name)
                self.ensure_memory_capacity(name, installed)
                await self.request("POST", "/api/generate", timeout=180,
                                   json={"model": name, "prompt": "", "stream": False, "keep_alive": -1})
                self._record_health(name, True)
                return {"success": True, "model": name, "running": True}
            except ModelError:
                self._record_health(name, False)
                raise

    async def _unload_model(self, name):
        await self.request("POST", "/api/generate", timeout=60,
                           json={"model": model_name(name), "prompt": "", "stream": False, "keep_alive": 0})

    async def unload_model(self, name):
        name = model_name(name)
        async with self.lock:
            await self._unload_model(name)
            return {"success": True, "model": name, "running": False}

    async def details(self, name):
        data = await self.request("POST", "/api/show", json={"model": model_name(name)})
        if data.get("remote_host") or data.get("remote_model"):
            raise ModelError("Cloud-Modelle sind im Local AI Center nicht zulaessig")
        # Do not expose system prompts/templates or incidental filesystem paths.
        return {key: data.get(key) for key in ("details", "capabilities", "model_info", "license")}

    async def test(self, name):
        name = model_name(name)
        try:
            await self.details(name)
            start = time.monotonic()
            data = await self.request("POST", "/api/chat", timeout=180, json={"model": name, "stream": False,
                                      "think": False, "messages": [{"role": "user", "content": "Antworte nur mit OK."}],
                                      "options": {"num_predict": 16, "num_ctx": 2048}})
            answer = data.get("message", {}).get("content", "").strip()
            if not answer or data.get("done") is not True:
                raise ModelError("Modelltest lieferte keine vollstaendige Textantwort", 502)
            self._record_health(name, True)
            return {"success": True, "response": answer[:200], "latency_ms": round((time.monotonic() - start) * 1000),
                    "input_tokens": data.get("prompt_eval_count"), "output_tokens": data.get("eval_count"), "route": "local"}
        except ModelError:
            self._record_health(name, False)
            raise

    def jobs(self):
        with self._db() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM downloads ORDER BY created DESC LIMIT 100")]
        for item in rows:
            total = int(item.get("total") or 0)
            completed = int(item.get("completed") or 0)
            # Ollama reports per-layer bytes, not reliable whole-model progress.
            percent = 100.0 if item["status"] == "COMPLETED" else None
            item.update(job_id=item["id"], model_id=item["model"], runtime="ollama",
                        state=item["status"], phase=item["detail"],
                        progress_percent=percent,
                        bytes_completed=completed or None, bytes_total=total or None,
                        created_at=item["created"], started_at=item.get("started"),
                        updated_at=item["updated"], completed_at=item.get("finished"),
                        safe_error_message=item["detail"] if item.get("error_code") else None)
        return rows

    def job(self, identifier):
        if not re.fullmatch(r"[a-f0-9]{32}", identifier or ""):
            raise ModelError("Ungueltige Job-ID")
        result = next((item for item in self.jobs() if item["id"] == identifier), None)
        if not result:
            raise ModelError("Downloadjob nicht gefunden", 404, "MODEL_NOT_FOUND")
        return result

    def update(self, identifier, status, detail, completed=0, total=0, *, error_code=None, digest=None):
        finished = time.time() if status in {"COMPLETED", "FAILED", "CANCELLED"} else None
        with self._db() as db:
            db.execute("UPDATE downloads SET status=?,detail=?,completed=?,total=?,updated=?,finished=COALESCE(?,finished),error_code=?,digest=COALESCE(?,digest) WHERE id=?",
                       (status, detail, completed, total, time.time(), finished, error_code, digest, identifier))

    async def pull(self, name, owner):
        entry = self.registry.require(name)
        async with self.lock:
            try:
                await self.health_check()
            except ModelError as error:
                raise ModelError("Lokale Runtime ist nicht erreichbar", 503, "RUNTIME_UNAVAILABLE") from error
            installed = await self.list_installed_models()
            if name in {item.get("name") for item in installed}:
                raise ModelError("Modell ist bereits installiert", 409, "MODEL_ALREADY_INSTALLED")
            compatibility = self.compatibility_engine.evaluate(entry, self.hardware(), runtime_online=True)
            if compatibility["state"] == "incompatible":
                raise ModelError(compatibility["message"], 409, "MODEL_INCOMPATIBLE")
            with self._db() as db:
                active = db.execute("SELECT model FROM downloads WHERE status IN ('QUEUED','VALIDATING','DOWNLOADING','VERIFYING','CANCELLING')").fetchone()
                if active:
                    code = "MODEL_DOWNLOAD_ACTIVE" if active["model"] == name else "MODEL_JOB_CONFLICT"
                    raise ModelError("Ein mutierender Modelljob laeuft bereits", 409, code)
                self.ensure_disk_capacity(entry["disk_size_bytes"])
                identifier = uuid.uuid4().hex
                now = time.time()
                db.execute("INSERT INTO downloads(id,model,owner,status,detail,created,updated,started) VALUES(?,?,?,'QUEUED','Wartet auf Runtime',?,?,?)", (identifier, name, owner, now, now, now))
            task = asyncio.create_task(self._pull(identifier, name, owner))
            self.tasks[identifier] = task
            task.add_done_callback(lambda _task, job_id=identifier: self.tasks.pop(job_id, None))
            self.audit(owner, "MODEL_DOWNLOAD_START", name)
            return {"id": identifier, "status": "QUEUED"}

    async def _pull(self, identifier, name, owner):
        success = False
        try:
            self.update(identifier, "VALIDATING", "Runtime und Speicher validiert")
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
                        completed = max(0, int(item.get("completed", 0) or 0))
                        total = max(0, int(item.get("total", 0) or 0))
                        if total and completed > total:
                            raise ModelError("Ungueltiger Download-Fortschritt")
                        # Ollama reports individual layers, not the overall manifest size.
                        phase = "VERIFYING" if status.startswith(("verifying", "writing", "removing")) else "DOWNLOADING"
                        self.update(identifier, phase, status[:160], completed, total)
                        if status == "success":
                            success = True
                            break
            if not success:
                raise ModelError("Download vor Abschluss unterbrochen")
            self.update(identifier, "VERIFYING", "Installation wird mit der Runtime abgeglichen")
            installed = await self.list_installed_models()
            runtime = next((item for item in installed if item.get("name") == name), None)
            if not runtime:
                raise ModelError("Runtime meldet das Modell nach Download nicht als installiert")
            await self.details(name)
            self.update(identifier, "COMPLETED", "Installiert und durch Runtime verifiziert",
                        runtime.get("size", 0), runtime.get("size", 0), digest=runtime.get("digest"))
            self.audit(owner, "MODEL_DOWNLOAD_COMPLETE", name)
        except asyncio.CancelledError:
            self.update(identifier, "CANCELLED", "Downloadstream beendet; Runtime-Zustand erneut pruefen",
                        error_code="MODEL_DOWNLOAD_CANCELLED")
            self.audit(owner, "MODEL_DOWNLOAD_CANCEL", name)
        except Exception:
            # Runtime errors can contain credentials or paths; persist a bounded safe message.
            self.update(identifier, "FAILED", "Download oder Runtime-Verifikation fehlgeschlagen",
                        error_code="MODEL_DOWNLOAD_FAILED")
            self.audit(owner, "MODEL_DOWNLOAD_FAILED", name)

    async def cancel(self, identifier, owner):
        if not re.fullmatch(r"[a-f0-9]{32}", identifier or ""):
            raise ModelError("Ungueltige Job-ID")
        async with self.lock:
            job = next((item for item in self.jobs() if item["id"] == identifier), None)
            if not job:
                raise ModelError("Downloadjob nicht gefunden", 404, "MODEL_NOT_FOUND")
            if job["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                return job
            self.update(identifier, "CANCELLING", "Downloadstream wird beendet")
            task = self.tasks.get(identifier)
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            current = self.job(identifier)
            if current["status"] == "CANCELLING":
                self.update(identifier, "CANCELLED", "Kein aktiver Backend-Stream vorhanden",
                            error_code="MODEL_DOWNLOAD_CANCELLED")
                self.audit(owner, "MODEL_DOWNLOAD_CANCEL", job["model"])
            return next(item for item in self.jobs() if item["id"] == identifier)

    async def delete(self, name, owner, referenced):
        model_name(name)
        async with self.lock:
            if referenced(name):
                raise ModelError("Modell wird von einer Rolle oder einem Profil verwendet", 409, "MODEL_DELETE_BLOCKED")
            if any(job["status"] in MUTATING and job["model"] == name for job in self.jobs()):
                raise ModelError("Fuer dieses Modell laeuft ein Download", 409, "MODEL_JOB_CONFLICT")
            installed = await self.list_installed_models()
            if name not in {item.get("name") for item in installed}:
                raise ModelError("Modell ist nicht installiert", 404, "MODEL_NOT_FOUND")
            running = await self.list_running_models()
            if name in {item.get("name") for item in running}:
                await self._unload_model(name)
            await self.request("DELETE", "/api/delete", json={"model": name})
            self.audit(owner, "MODEL_DELETE", name)

    async def close(self):
        for task in tuple(self.tasks.values()):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks.values()), return_exceptions=True)


def make_router(service, require_admin, config):
    from fastapi import APIRouter, Depends, HTTPException
    from pydantic import BaseModel, Field

    class ModelRequest(BaseModel):
        model: str = Field(min_length=1, max_length=160)

    class JobRequest(BaseModel):
        job_id: str = Field(min_length=32, max_length=32)

    class RoleRequest(BaseModel):
        role: str = Field(min_length=4, max_length=7)
        model: str = Field(default="", max_length=160)

    router = APIRouter(prefix="/api/local-ai", dependencies=[Depends(require_admin)])

    async def guarded(operation):
        try:
            return await operation
        except ModelError as error:
            detail = {"code": error.code, "message": str(error)} if error.code else str(error)
            raise HTTPException(error.status, detail) from error

    @router.get("/status")
    async def status():
        return await service.status()

    @router.get("/hardware")
    async def hardware():
        return service.hardware()

    @router.get("/installed")
    async def installed():
        return await guarded(service.list_installed_models())

    @router.get("/running")
    async def running():
        return await guarded(service.list_running_models())

    @router.get("/catalog")
    async def catalog():
        return service.catalog()

    @router.get("/registry")
    async def registry():
        health = await service.status()
        return service.registry_entries(runtime_online=health["online"])

    @router.get("/compatibility/{identifier:path}")
    async def compatibility(identifier: str):
        item = service.registry.require(identifier)
        health = await service.status()
        return service.compatibility_engine.evaluate(item, service.hardware(), runtime_online=health["online"])

    @router.get("/downloads")
    async def downloads():
        return service.jobs()

    @router.get("/jobs/{identifier}")
    async def job(identifier: str):
        try:
            return service.job(identifier)
        except ModelError as error:
            detail = {"code": error.code, "message": str(error)} if error.code else str(error)
            raise HTTPException(error.status, detail) from error

    @router.get("/routing")
    async def routing():
        return {"mode": config.MODEL_ROUTING_MODE, "local_model": config.LOCAL_MODEL,
                "local_fast_model": getattr(config, "LOCAL_FAST_MODEL", ""),
                "local_general_model": getattr(config, "LOCAL_GENERAL_MODEL", "") or config.LOCAL_MODEL,
                "local_strong_model": getattr(config, "LOCAL_STRONG_MODEL", ""),
                "smart_local_complexity_limit": getattr(config, "SMART_LOCAL_COMPLEXITY_LIMIT", 0.86),
                "smart_tool_cloud_complexity": getattr(config, "SMART_TOOL_CLOUD_COMPLEXITY", 0.55),
                "daily_cloud_token_budget": getattr(config, "DAILY_CLOUD_TOKEN_BUDGET", 0),
                "monthly_cloud_cost_budget": getattr(config, "MONTHLY_CLOUD_COST_BUDGET", 0),
                "modes": ["local_only", "local_first", "smart", "cloud"]}

    @router.get("/usage")
    async def usage():
        from backend.ai.usage import usage_tracker
        return {"today": usage_tracker.snapshot("today"), "month": usage_tracker.snapshot("month")}

    class RoutingRequest(BaseModel):
        mode: str = Field(min_length=5, max_length=20)
        local_model: str = Field(default="", max_length=160)
        local_fast_model: str = Field(default="", max_length=160)
        local_general_model: str = Field(default="", max_length=160)
        local_strong_model: str = Field(default="", max_length=160)
        smart_local_complexity_limit: float | None = None
        smart_tool_cloud_complexity: float | None = None
        daily_cloud_token_budget: int | None = Field(default=None, ge=0, le=1_000_000_000)
        monthly_cloud_cost_budget: float | None = Field(default=None, ge=0, le=1_000_000)

    @router.post("/routing")
    async def set_routing(body: RoutingRequest, user=Depends(require_admin)):
        mode = body.mode.lower()
        if mode not in {"local_only", "local_first", "smart", "cloud"}:
            raise HTTPException(400, "Unbekannter Routing-Modus")
        selected = model_name(body.local_model) if body.local_model else config.LOCAL_MODEL
        tier_models = {
            "local_fast_model": model_name(body.local_fast_model) if body.local_fast_model else "",
            "local_general_model": model_name(body.local_general_model) if body.local_general_model else selected,
            "local_strong_model": model_name(body.local_strong_model) if body.local_strong_model else "",
        }
        if mode != "cloud":
            if not any(tier_models.values()):
                raise HTTPException(409, "Zuerst ein lokales Modell auswaehlen")
            for candidate in set(filter(None, tier_models.values())):
                await guarded(service.details(candidate))
        settings = {"model_routing_mode": mode, "local_model": tier_models["local_general_model"] or selected,
                    **tier_models}
        if body.smart_local_complexity_limit is not None:
            settings["smart_local_complexity_limit"] = max(0.05, min(float(body.smart_local_complexity_limit), 1.0))
        if body.smart_tool_cloud_complexity is not None:
            settings["smart_tool_cloud_complexity"] = max(0.05, min(float(body.smart_tool_cloud_complexity), 1.0))
        if body.daily_cloud_token_budget is not None:
            settings["daily_cloud_token_budget"] = body.daily_cloud_token_budget
        if body.monthly_cloud_cost_budget is not None:
            settings["monthly_cloud_cost_budget"] = body.monthly_cloud_cost_budget
        config.save_global_settings(settings)
        service.audit(user, "model_routing_changed", selected or mode)
        return {"success": True, "mode": config.MODEL_ROUTING_MODE, "local_model": config.LOCAL_MODEL,
                "local_fast_model": getattr(config, "LOCAL_FAST_MODEL", ""),
                "local_general_model": getattr(config, "LOCAL_GENERAL_MODEL", "") or config.LOCAL_MODEL,
                "local_strong_model": getattr(config, "LOCAL_STRONG_MODEL", ""),
                "smart_local_complexity_limit": getattr(config, "SMART_LOCAL_COMPLEXITY_LIMIT", 0.86),
                "smart_tool_cloud_complexity": getattr(config, "SMART_TOOL_CLOUD_COMPLEXITY", 0.55),
                "daily_cloud_token_budget": getattr(config, "DAILY_CLOUD_TOKEN_BUDGET", 0),
                "monthly_cloud_cost_budget": getattr(config, "MONTHLY_CLOUD_COST_BUDGET", 0)}

    @router.post("/details")
    async def details(body: ModelRequest):
        return await guarded(service.details(body.model))

    @router.post("/test")
    async def test(body: ModelRequest, user=Depends(require_admin)):
        result = await guarded(service.test(body.model))
        service.audit(user, "MODEL_TEST", body.model)
        return result

    @router.post("/load")
    async def load(body: ModelRequest, user=Depends(require_admin)):
        result = await guarded(service.load_model(body.model))
        service.audit(user, "MODEL_LOAD", body.model)
        return result

    @router.post("/unload")
    async def unload(body: ModelRequest, user=Depends(require_admin)):
        result = await guarded(service.unload_model(body.model))
        service.audit(user, "MODEL_UNLOAD", body.model)
        return result

    @router.post("/pull", status_code=202)
    async def pull(body: ModelRequest, user=Depends(require_admin)):
        return await guarded(service.pull(body.model, user))

    @router.post("/cancel")
    async def cancel(body: JobRequest, user=Depends(require_admin)):
        return await guarded(service.cancel(body.job_id, user))

    @router.post("/roles")
    async def assign_role(body: RoleRequest, user=Depends(require_admin)):
        role = body.role.lower()
        if role not in {"fast", "general", "strong"}:
            raise HTTPException(400, "Unbekannte Modellrolle")
        if role != "strong" and not body.model:
            raise HTTPException(409, "Fast und General benoetigen ein lokales Modell")
        async with service.lock:
            if body.model:
                item = service.registry.require(body.model)
                await guarded(service.health_check())
                installed = await guarded(service.list_installed_models())
                if body.model not in {model.get("name") for model in installed}:
                    raise HTTPException(409, {"code": "MODEL_NOT_FOUND", "message": "Modell ist nicht installiert"})
                capability = item["capabilities"].get("chat")
                compatibility = service.compatibility_engine.evaluate(item, service.hardware(), role=role)
                if capability is not True or compatibility["state"] == "incompatible":
                    raise HTTPException(409, {"code": "MODEL_INCOMPATIBLE", "message": compatibility["message"]})
            attribute = {"fast": "local_fast_model", "general": "local_general_model",
                         "strong": "local_strong_model"}[role]
            values = {attribute: body.model}
            if role == "general":
                values["local_model"] = body.model
            config.save_global_settings(values)
            service.role_models[role] = body.model
            service.audit(user, "MODEL_ROLE_CHANGE", f"{role}:{body.model or 'CLOUD'}")
        return {"success": True, "role": role, "model": body.model or None,
                "cloud": role == "strong" and not body.model}

    @router.post("/activate")
    async def activate(body: ModelRequest, user=Depends(require_admin)):
        async with service.lock:
            await guarded(service.test(body.model))
            config.save_global_settings({"local_model": body.model, "local_general_model": body.model})
            service.audit(user, "model_activated", body.model)
            return {"success": True, "local_model": body.model, "scope": "model_router"}

    @router.post("/delete")
    async def delete(body: ModelRequest, user=Depends(require_admin)):
        await guarded(service.delete(body.model, user, lambda name: name in {
            config.LOCAL_MODEL, getattr(config, "LOCAL_FAST_MODEL", ""),
            getattr(config, "LOCAL_GENERAL_MODEL", ""), getattr(config, "LOCAL_STRONG_MODEL", "")
        } or any(p.get("model") == name for p in config.profiles)))
        return {"success": True}

    return router
