# SPDX-License-Identifier: Apache-2.0
"""Model metadata and local tier selection."""

from __future__ import annotations

from dataclasses import dataclass, field

from .policy import Capability


@dataclass(frozen=True)
class ModelSpec:
    id: str
    provider: str
    runtime: str
    route: str
    tier: str
    context_length: int = 8192
    capabilities: set[Capability] = field(default_factory=set)
    priority: int = 100
    enabled: bool = True

    def supports(self, required: set[Capability], text_size: int = 0) -> bool:
        if not self.enabled:
            return False
        if text_size and text_size > self.context_length * 4:
            return False
        return required.issubset(self.capabilities)


def infer_local_spec(model_id: str, tier: str = "local_general") -> ModelSpec:
    name = (model_id or "").lower()
    caps = {Capability.CHAT}
    context = 8192
    priority = 50
    if any(key in name for key in ("qwen", "llama", "mistral", "gemma", "phi")):
        caps.update({Capability.REASONING, Capability.CODE, Capability.STRUCTURED_OUTPUT})
    if any(key in name for key in ("tool", "qwen3", "llama3.1", "llama3.2", "mistral")):
        caps.add(Capability.TOOLS)
    if any(key in name for key in ("vision", "llava", "bakllava")):
        caps.add(Capability.VISION)
    if any(key in name for key in ("32k", "128k", "long")):
        caps.add(Capability.LONG_CONTEXT)
        context = 32768 if "32k" in name else 131072
    if any(key in name for key in ("0.5b", "1.5b", "3b", "4b", "mini")):
        tier, priority = "local_fast", 10
    elif any(key in name for key in ("14b", "27b", "32b", "70b")):
        tier, priority = "local_strong", 80
    return ModelSpec(
        id=model_id,
        provider="openai_compatible",
        runtime="ollama",
        route="local",
        tier=tier,
        context_length=context,
        capabilities=caps,
        priority=priority,
    )


def choose_local_model(
    configured_model: str,
    required: set[Capability],
    complexity: float,
    text_size: int,
) -> ModelSpec | None:
    if not configured_model:
        return None
    spec = infer_local_spec(configured_model)
    if not spec.supports(required, text_size):
        # The configured local model is still allowed for simple chat and
        # translation-like work. Missing advanced caps block local-only safely.
        simple = required.issubset({Capability.CHAT, Capability.STRUCTURED_OUTPUT})
        if not simple:
            return None
    if complexity >= 0.85 and spec.tier == "local_fast":
        return None
    return spec

