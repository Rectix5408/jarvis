# SPDX-License-Identifier: Apache-2.0
"""Central local-first routing decision for LLM calls."""

from __future__ import annotations

from .complexity import TaskAnalysis, analyze
from .models import choose_local_model
from .policy import ExecutionRoute, RouteDecision, RoutingMode, TaskType


def _mode(value: str) -> RoutingMode:
    try:
        return RoutingMode((value or "cloud").lower())
    except ValueError:
        return RoutingMode.CLOUD


def decide(
    *,
    mode: str,
    local_model: str | dict[str, str],
    primary_route: str,
    contents: list,
    tools: list | None,
) -> RouteDecision:
    policy = _mode(mode)
    analysis: TaskAnalysis = analyze(contents, tools)
    local = choose_local_model(
        local_model,
        analysis.required_capabilities,
        analysis.complexity,
        analysis.text_size,
    )

    if analysis.task_type == TaskType.DETERMINISTIC:
        return RouteDecision(
            route=ExecutionRoute.DETERMINISTIC,
            task_type=analysis.task_type,
            complexity=analysis.complexity,
            reason=analysis.deterministic_hint or "deterministic_intent",
            cloud_allowed=False,
        )

    if policy == RoutingMode.LOCAL_ONLY:
        if local:
            return RouteDecision(ExecutionRoute.LOCAL, local.id, local.tier, analysis.task_type,
                                 analysis.complexity, "local_only", False)
        return RouteDecision(ExecutionRoute.BLOCKED, task_type=analysis.task_type,
                             complexity=analysis.complexity,
                             reason="Lokales Modell fehlt oder kann die benoetigte Faehigkeit nicht abdecken",
                             cloud_allowed=False)

    if policy == RoutingMode.LOCAL_FIRST:
        if local:
            return RouteDecision(ExecutionRoute.LOCAL, local.id, local.tier, analysis.task_type,
                                 analysis.complexity, "local_first", True)
        return RouteDecision(ExecutionRoute.CLOUD, task_type=analysis.task_type,
                             complexity=analysis.complexity,
                             reason="local_capability_missing", cloud_allowed=True)

    if policy == RoutingMode.SMART:
        try:
            from backend.config import config
            local_limit = max(0.05, min(float(getattr(config, "SMART_LOCAL_COMPLEXITY_LIMIT", 0.86)), 1.0))
            tool_cloud_limit = max(0.05, min(float(getattr(config, "SMART_TOOL_CLOUD_COMPLEXITY", 0.55)), 1.0))
        except Exception:
            local_limit, tool_cloud_limit = 0.86, 0.55
        cloud_needed = (
            analysis.has_media
            or analysis.complexity >= local_limit
            or (analysis.has_tools and primary_route == "cloud" and analysis.complexity >= tool_cloud_limit)
        )
        if local and not cloud_needed:
            return RouteDecision(ExecutionRoute.LOCAL, local.id, local.tier, analysis.task_type,
                                 analysis.complexity, "smart_smallest_capable_local", True)
        return RouteDecision(ExecutionRoute.CLOUD, task_type=analysis.task_type,
                             complexity=analysis.complexity,
                             reason="smart_cloud_escalation", cloud_allowed=True)

    # CLOUD still preserves local primary attribution and deterministic shortcuts;
    # it only means cloud is permitted as the main LLM route.
    return RouteDecision(ExecutionRoute.CLOUD, task_type=analysis.task_type,
                         complexity=analysis.complexity, reason="cloud_mode", cloud_allowed=True)
