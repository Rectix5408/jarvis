# SPDX-License-Identifier: Apache-2.0
"""Deterministic zero-token answers for safe status requests."""

from __future__ import annotations

import os
import re


_RAM_RE = re.compile(r"\b(ram|arbeitsspeicher|speicher frei|memory)\b", re.I)
_CPU_RE = re.compile(r"\b(cpu|prozessor|auslastung)\b", re.I)
_MODEL_RE = re.compile(r"\b(modelle|lokale modelle|installierte modelle|ollama)\b", re.I)
_AGENTS_RE = re.compile(r"\b(agenten laufen|aktive agenten|wie viele agenten|agents running)\b", re.I)


def _fmt_bytes(value: int | float | None) -> str:
    if value is None:
        return "unbekannt"
    units = ("B", "KB", "MB", "GB", "TB")
    number = float(value)
    for unit in units:
        if number < 1024 or unit == units[-1]:
            return f"{number:.1f} {unit}" if unit != "B" else f"{int(number)} B"
        number /= 1024
    return f"{number:.1f} TB"


async def answer(text: str, *, agent_manager=None) -> str | None:
    query = (text or "").strip()
    if not query:
        return None
    if _RAM_RE.search(query) or _CPU_RE.search(query):
        try:
            import psutil
            mem = psutil.virtual_memory()
            cpu = psutil.cpu_percent(interval=0)
            parts = []
            if _CPU_RE.search(query):
                parts.append(f"CPU-Auslastung: {cpu:.0f} %")
            if _RAM_RE.search(query):
                parts.append(
                    "RAM: "
                    f"{_fmt_bytes(mem.available)} frei von {_fmt_bytes(mem.total)} "
                    f"({mem.percent:.0f} % belegt)"
                )
            return "\n".join(parts) if parts else None
        except Exception:
            return "Systemstatus konnte lokal nicht gelesen werden."
    if _AGENTS_RE.search(query):
        if not agent_manager:
            return None
        try:
            agents = list(getattr(agent_manager, "agents", {}).values())
            running = [a for a in agents if str(getattr(getattr(a, "state", ""), "value", getattr(a, "state", ""))).lower() == "running"]
            return f"Aktive Agenten: {len(running)} von {len(agents)} registriert."
        except Exception:
            return None
    if _MODEL_RE.search(query):
        try:
            from backend.config import config
            local_model = getattr(config, "LOCAL_MODEL", "") or "nicht gesetzt"
            mode = getattr(config, "MODEL_ROUTING_MODE", "cloud")
            try:
                from backend.local_models import runtime_url
                import httpx
                base = runtime_url(os.environ.get("JARVIS_OLLAMA_URL", "http://127.0.0.1:11434"))
                async with httpx.AsyncClient(base_url=base, timeout=5, trust_env=False) as client:
                    response = await client.get("/api/tags")
                    response.raise_for_status()
                    models = [m.get("name", "") for m in response.json().get("models", []) if m.get("name")]
                installed = ", ".join(sorted(models)[:20]) if models else "keine lokalen Modelle gefunden"
            except Exception:
                installed = "Ollama nicht erreichbar"
            return f"Routing: {mode}\nAktives lokales Modell: {local_model}\nInstalliert: {installed}"
        except Exception:
            return None
    return None

