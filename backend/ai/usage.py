# SPDX-License-Identifier: Apache-2.0
"""In-process usage counters for route decisions."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class UsageTracker:
    routes: Counter = field(default_factory=Counter)
    input_tokens: int = 0
    output_tokens: int = 0
    cloud_cost: float = 0.0

    def record(self, route: str, usage: dict | None = None) -> None:
        usage = usage or {}
        self.routes[route] += 1
        self.input_tokens += int(usage.get("input_tokens") or 0)
        self.output_tokens += int(usage.get("output_tokens") or 0)
        self.cloud_cost += float(usage.get("cost") or 0.0)

    def snapshot(self) -> dict:
        return {
            "routes": dict(self.routes),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cloud_cost": self.cloud_cost,
        }


usage_tracker = UsageTracker()

