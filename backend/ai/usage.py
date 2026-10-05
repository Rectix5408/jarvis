# SPDX-License-Identifier: Apache-2.0
"""Prompt-free, persistent AI routing and usage metrics."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import threading
import time


class UsageTracker:
    def __init__(self, path: str | Path | None = None):
        data_dir = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
        self.path = Path(path) if path else data_dir / "ai-usage.sqlite3"
        self._lock = threading.Lock()
        self._ready = False

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        if not self._ready:
            with self._lock:
                if not self._ready:
                    connection.execute("PRAGMA journal_mode=WAL")
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS ai_usage ("
                        "id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, route TEXT NOT NULL, "
                        "provider TEXT NOT NULL, model TEXT NOT NULL, task_type TEXT NOT NULL, "
                        "input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, "
                        "latency_ms INTEGER NOT NULL, success INTEGER NOT NULL, fallback INTEGER NOT NULL, "
                        "estimated_cost REAL NOT NULL, metadata_json TEXT NOT NULL)"
                    )
                    connection.execute(
                        "CREATE INDEX IF NOT EXISTS idx_ai_usage_timestamp ON ai_usage(timestamp)"
                    )
                    connection.commit()
                    self._ready = True
        return connection

    def record(self, route: str, usage: dict | None = None, **metadata) -> None:
        usage = usage or {}
        safe_metadata = {
            key: value for key, value in {**usage, **metadata}.items()
            if key in {
                "reason", "tier", "complexity", "system_tokens_est",
                "conversation_tokens_est", "memory_tokens_est", "knowledge_tokens_est",
                "tool_schema_tokens_est", "tool_result_tokens_est", "tool_schema_count",
                "cached_input_tokens", "total_tokens", "llm_calls", "tool_calls", "agent_steps",
                "subagent_calls", "fallback_count", "escalation_reason",
                "token_source", "context_budget_tokens", "context_tokens_est",
            } and isinstance(value, (str, int, float, bool))
        }
        with self._connect() as db:
            db.execute(
                "INSERT INTO ai_usage(timestamp,route,provider,model,task_type,input_tokens,"
                "output_tokens,latency_ms,success,fallback,estimated_cost,metadata_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    time.time(), str(route or "unknown")[:32], str(usage.get("provider") or "")[:80],
                    str(usage.get("model") or "")[:160], str(usage.get("task_type") or "")[:40],
                    max(0, int(usage.get("input_tokens") or 0)),
                    max(0, int(usage.get("output_tokens") or 0)),
                    max(0, int(usage.get("latency_ms") or 0)), int(bool(usage.get("success", True))),
                    int(bool(usage.get("fallback", False))), max(0.0, float(usage.get("cost") or 0.0)),
                    json.dumps(safe_metadata, separators=(",", ":")),
                ),
            )

    @staticmethod
    def _period_start(period: str) -> float:
        now = datetime.now(timezone.utc)
        if period == "month":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.timestamp()

    def snapshot(self, period: str = "today") -> dict:
        start = self._period_start("month" if period == "month" else "today")
        with self._connect() as db:
            rows = db.execute(
                "SELECT route,COUNT(*) requests,SUM(input_tokens) input_tokens,"
                "SUM(output_tokens) output_tokens,SUM(estimated_cost) cost,"
                "SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) errors "
                "FROM ai_usage WHERE timestamp>=? GROUP BY route", (start,),
            ).fetchall()
        routes = {row["route"]: row["requests"] for row in rows}
        requests = sum(routes.values())
        local_avoided = routes.get("local", 0) + routes.get("deterministic", 0)
        return {
            "period": period,
            "requests": requests,
            "routes": routes,
            "input_tokens": sum(row["input_tokens"] or 0 for row in rows),
            "output_tokens": sum(row["output_tokens"] or 0 for row in rows),
            "cloud_cost": round(sum(row["cost"] or 0 for row in rows), 8),
            "errors": sum(row["errors"] or 0 for row in rows),
            "local_rate": round((local_avoided / requests * 100) if requests else 0.0, 1),
            "estimated_cloud_calls_avoided": local_avoided,
        }

    def cloud_totals(self, period: str) -> tuple[int, float]:
        start = self._period_start(period)
        with self._connect() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(input_tokens+output_tokens),0) tokens,"
                "COALESCE(SUM(estimated_cost),0) cost FROM ai_usage "
                "WHERE timestamp>=? AND route='cloud'", (start,),
            ).fetchone()
        return int(row["tokens"]), float(row["cost"])


usage_tracker = UsageTracker()
