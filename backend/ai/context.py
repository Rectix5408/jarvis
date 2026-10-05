# SPDX-License-Identifier: Apache-2.0
"""Deterministic context accounting and bounded text compaction."""

from __future__ import annotations

import re


def estimate_tokens(value: str) -> int:
    """Conservative dependency-free estimate used for budgets, not billing."""
    return (len(value or "") + 3) // 4


def fit_text(value: str, token_budget: int, label: str = "context") -> str:
    """Keep complete leading lines within a token budget and mark truncation."""
    text = value or ""
    max_chars = max(0, int(token_budget)) * 4
    if len(text) <= max_chars:
        return text
    marker = f"\n[{label} compacted: {estimate_tokens(text)} -> <= {token_budget} estimated tokens]"
    room = max(0, max_chars - len(marker))
    prefix = text[:room]
    if "\n" in prefix:
        prefix = prefix.rsplit("\n", 1)[0]
    return prefix.rstrip() + marker


def compact_lines(lines: list[str], token_budget: int, label: str = "history") -> str:
    """Deduplicate and bound compact context without another model call."""
    seen: set[str] = set()
    useful: list[str] = []
    for raw in lines:
        line = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not line:
            continue
        key = line.casefold()[:240]
        if key in seen:
            continue
        seen.add(key)
        useful.append(line[:600])
    return fit_text("\n".join(useful), token_budget, label)
