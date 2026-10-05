# SPDX-License-Identifier: Apache-2.0
"""Policy primitives for the local-first model router."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RoutingMode(str, Enum):
    LOCAL_ONLY = "local_only"
    LOCAL_FIRST = "local_first"
    SMART = "smart"
    CLOUD = "cloud"


class TaskType(str, Enum):
    DETERMINISTIC = "deterministic"
    CHAT = "chat"
    CLASSIFICATION = "classification"
    EXTRACTION = "extraction"
    SUMMARIZATION = "summarization"
    TRANSLATION = "translation"
    WRITING = "writing"
    RAG = "rag"
    TOOL_USE = "tool_use"
    AGENT = "agent"
    CODE = "code"
    REASONING = "reasoning"
    VISION = "vision"
    LONG_CONTEXT = "long_context"
    HIGH_RISK = "high_risk"


class Capability(str, Enum):
    CHAT = "chat"
    TOOLS = "tools"
    STRUCTURED_OUTPUT = "structured_output"
    VISION = "vision"
    EMBEDDINGS = "embeddings"
    LONG_CONTEXT = "long_context"
    CODE = "code"
    REASONING = "reasoning"


class ExecutionRoute(str, Enum):
    DETERMINISTIC = "deterministic"
    LOCAL = "local"
    CLOUD = "cloud"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class RouteDecision:
    route: ExecutionRoute
    model: str = ""
    tier: str = ""
    task_type: TaskType = TaskType.CHAT
    complexity: float = 0.0
    reason: str = ""
    cloud_allowed: bool = False

