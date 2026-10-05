# SPDX-License-Identifier: Apache-2.0
"""Zero-token execution planning for agent depth, context and tool schemas."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


class AgentLevel(str, Enum):
    DIRECT = "direct"
    SIMPLE = "simple"
    TOOL = "tool"
    AUTONOMOUS = "autonomous"


@dataclass(frozen=True)
class ExecutionPlan:
    level: AgentLevel
    tool_names: frozenset[str]
    memory_needed: bool
    knowledge_needed: bool
    reasoning_effort: str
    max_steps: int
    reason: str


_GROUPS = {
    "knowledge": {"knowledge_search"},
    "memory": {"memory_manage"},
    "email": {"google_gmail"},
    "calendar": {"google_calendar"},
    "drive": {"google_drive"},
    "browser": {"browser_control", "browser_cdp"},
    "files": {"filesystem", "shell_execute"},
    "desktop": {"desktop_control", "windows_desktop", "android_desktop", "screenshot",
                "wait_for_screen_change", "read_clipboard", "write_clipboard"},
    "image": {"generate_image", "search_image", "create_chart"},
    "cron": {"cron_create", "cron_list", "cron_delete"},
    "agent": {"delegate", "spawn_agent", "reflection"},
    "whatsapp": {"whatsapp_send", "whatsapp_contacts", "whatsapp_status"},
}

_PATTERNS = {
    "knowledge": r"(wissen|knowledge|dokument|handbuch|quelle|produkt|kunde|\brag\b|\bpdf\b|\bdocx\b|\bxlsx\b|\bpptx\b)",
    "memory": r"\b(merk|memory|erinnerst|praeferenz|präferenz|frueher|früher|damals|gestern|"
              r"über mich|ueber mich|dir gesagt|wir besprochen)\b",
    "email": r"\b(e-?mails?|gmail|posteingang|mailbox|nachricht senden|antwortmails?)\b",
    "calendar": r"\b(kalender|calendar|termine?|meetings?|verfuegbar|verfügbar|zeitfenster)\b",
    "drive": r"\b(google drive|drive-datei|drive ordner)\b",
    "browser": r"\b(browser|webseite|website|internet|online|url|https?://|recherchier)\b",
    "files": r"\b(datei|ordner|filesystem|shell|terminal|python|code|skript|docker|logdatei|csv|json)\b",
    "desktop": r"\b(desktop|bildschirm|screenshot|fenster|klick|clipboard|zwischenablage|windows|android)\b",
    "image": r"\b(bild|grafik|diagramm|chart|foto|illustration|visualisier)\b",
    "cron": r"\b(cron|zeitplan|regelmaessig|regelmäßig|taeglich|täglich|erinnerung)\b",
    "agent": r"\b(sub-?agent|delegier|workflow|mehrstufig|parallel|autonom|reflektier)\b",
    "whatsapp": r"\b(whatsapp|wa-nachricht|wa kontakt)\b",
}

_ACTION = re.compile(
    r"\b(sende|schreibe|erstelle|oeffne|öffne|suche|lies|loesche|lösche|aendere|ändere|"
    r"starte|stoppe|fuehre|führe|lade|speichere|plane|analysiere)\b", re.I)
_COMPLEX = re.compile(
    r"\b(komplex\w*|mehrstufig|strategie|architektur|parallel|vollstaendig|vollständig|"
    r"selbststaendig|selbstständig)\b", re.I)


def plan_execution(task: str, available_names: set[str]) -> ExecutionPlan:
    text = (task or "").strip()
    selected: set[str] = set()
    matched: list[str] = []
    for group, pattern in _PATTERNS.items():
        if re.search(pattern, text, re.I):
            selected.update(_GROUPS[group])
            matched.append(group)

    memory_needed = "memory" in matched
    knowledge_needed = "knowledge" in matched
    selected &= available_names
    explicit_action = bool(_ACTION.search(text))
    complex_task = bool(_COMPLEX.search(text)) or len(matched) >= 3
    if complex_task:
        level, effort, steps = AgentLevel.AUTONOMOUS, "medium", 12
        selected.update(_GROUPS["agent"] & available_names)
    elif selected or explicit_action:
        level, effort, steps = AgentLevel.TOOL, "low", 6
        # Specialized roles remain available for domain tasks. The role registry
        # and its permission intersection still decide whether delegation works.
        if selected and "delegate" in available_names:
            selected.add("delegate")
    else:
        level, effort, steps = AgentLevel.SIMPLE, "off", 1

    if explicit_action and not selected and not matched:
        selected = set(available_names)
        reason = "unknown_action_conservative_toolset"
    elif matched and not selected:
        reason = "+".join(matched) + ":unavailable"
    else:
        reason = "+".join(matched) if matched else "plain_language"
    return ExecutionPlan(level, frozenset(selected), memory_needed, knowledge_needed,
                         effort, steps, reason)
