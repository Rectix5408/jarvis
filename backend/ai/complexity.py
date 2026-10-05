# SPDX-License-Identifier: Apache-2.0
"""Cheap, deterministic task classification and complexity scoring."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .policy import Capability, TaskType


_TRANSLATE_RE = re.compile(r"\b(uebersetz|übersetz|translate|translation)\b", re.I)
_SUMMARY_RE = re.compile(r"\b(fass|zusammenfass|summary|summarize|tl;dr)\b", re.I)
_CODE_RE = re.compile(r"\b(code|python|javascript|typescript|docker|sql|regex|fehlermeldung|stacktrace|api)\b", re.I)
_REASON_RE = re.compile(r"\b(plane|analysiere|begründe|vergleiche|strategie|architektur|mehrstufig|komplex)\b", re.I)
_AGENT_RE = re.compile(r"\b(agent|workflow|task|aufgabe|delegier|automatisier|cron)\b", re.I)
_RAG_RE = re.compile(r"\b(wissen|dokument|datei|pdf|knowledge|rag|quelle|such)\b", re.I)
_HIGH_RISK_RE = re.compile(r"\b(lösche|loesche|delete|sende|send|kaufe|bezahle|zahlung|prod|produktion|server|sudo|rm -rf)\b", re.I)
_VISION_RE = re.compile(r"\b(bild|screenshot|foto|image|vision|erkenne auf)\b", re.I)
_DETERMINISTIC_RE = re.compile(
    r"\b("
    r"welche modelle|installierte modelle|modelle installiert|ollama status|runtime status|"
    r"wie viel ram|ram frei|cpu|speicher frei|festplatte|disk|"
    r"wie viele agenten|agenten laufen|aktive agenten|"
    r"welche tasks|offene tasks|heutige termine|termine heute|"
    r"stoppe agent|agent stoppen"
    r")\b",
    re.I,
)


@dataclass(frozen=True)
class TaskAnalysis:
    text_size: int
    has_media: bool
    has_tools: bool
    task_type: TaskType
    complexity: float
    required_capabilities: set[Capability] = field(default_factory=set)
    deterministic_hint: str = ""


def _text(contents: list) -> str:
    parts: list[str] = []
    for content in contents or []:
        for part in getattr(content, "parts", []) or []:
            value = getattr(part, "text", "") or ""
            if value:
                parts.append(value)
    return "\n".join(parts)


def analyze(contents: list, tools: list | None = None) -> TaskAnalysis:
    text = _text(contents)
    lowered = text.lower()
    text_size = len(text)
    has_media = any(
        getattr(part, "inline_data", None)
        for content in contents or []
        for part in getattr(content, "parts", []) or []
    )
    has_tools = bool(tools)
    caps: set[Capability] = {Capability.CHAT}
    task_type = TaskType.CHAT
    score = 0.10
    deterministic_hint = ""

    if has_media or _VISION_RE.search(text):
        task_type = TaskType.VISION
        caps.add(Capability.VISION)
        score += 0.35
    elif _DETERMINISTIC_RE.search(lowered):
        task_type = TaskType.DETERMINISTIC
        deterministic_hint = "direct_tool_or_status"
        score = 0.02
    elif _TRANSLATE_RE.search(text):
        task_type = TaskType.TRANSLATION
        score += 0.10
    elif _SUMMARY_RE.search(text):
        task_type = TaskType.SUMMARIZATION
        score += 0.20
    elif _CODE_RE.search(text):
        task_type = TaskType.CODE
        caps.add(Capability.CODE)
        score += 0.35
    elif _RAG_RE.search(text):
        task_type = TaskType.RAG
        caps.add(Capability.LONG_CONTEXT)
        score += 0.25
    elif _AGENT_RE.search(text):
        task_type = TaskType.AGENT
        caps.update({Capability.TOOLS, Capability.REASONING})
        score += 0.35
    elif _REASON_RE.search(text):
        task_type = TaskType.REASONING
        caps.add(Capability.REASONING)
        score += 0.30

    if has_tools:
        caps.add(Capability.TOOLS)
        score += 0.18
        if task_type == TaskType.CHAT:
            task_type = TaskType.TOOL_USE
    if _HIGH_RISK_RE.search(text):
        task_type = TaskType.HIGH_RISK
        caps.add(Capability.TOOLS)
        score += 0.25
    if text_size > 6000:
        task_type = TaskType.LONG_CONTEXT
        caps.add(Capability.LONG_CONTEXT)
        score += 0.25
    if text_size > 20000:
        score += 0.25
    if text.count("\n") > 60:
        score += 0.10

    return TaskAnalysis(
        text_size=text_size,
        has_media=has_media,
        has_tools=has_tools,
        task_type=task_type,
        complexity=max(0.0, min(score, 1.0)),
        required_capabilities=caps,
        deterministic_hint=deterministic_hint,
    )

