"""LLM setup for the Strands PoC.

Strands Agents ships a native ``OllamaModel`` class via the
``strands.models.ollama`` module. This module is a thin factory so
tests can swap the model without touching the agent loop.

Note: import path may vary across Strands versions:
    - `strands.models.ollama.OllamaModel` (most common)
    - `strands.models.ollama.Ollama` (older)
    - fall back to `strands.models.ollama` (module) on AttributeError
"""

from __future__ import annotations

import importlib

from .config import Config


def build_ollama_model(config: Config):
    """Construct the Ollama model instance used by the agent.

    Args:
        config: PoC configuration holding ``ollama_*`` fields.

    Returns:
        A configured model instance ready to be passed to
        ``Agent(model=...)``.
    """
    mod = importlib.import_module("strands.models.ollama")
    cls = getattr(mod, "OllamaModel", None) or getattr(mod, "Ollama", None)
    if cls is None:
        raise RuntimeError(
            f"Could not find Ollama model class in strands.models.ollama "
            f"(available attributes: {[a for a in dir(mod) if not a.startswith('_')]})"
        )

    # Strip any /no_think suffix since we control thinking via additional_args
    model_id = config.ollama_model.replace(":no_think", "").replace("/no_think", "")

    return cls(
        host=config.ollama_base_url,
        model_id=model_id,
        # Enable Qwen3's thinking mode to improve reasoning
        # See: https://ollama.com/library/qwen3 - think param for thinking models
        additional_args={"think": True},
    )


def build_ollama_model_safe(config: Config):
    """Like ``build_ollama_model`` but with extra options passed through."""
    model = build_ollama_model(config)

    # Best-effort: tune keep_alive and temperature via attributes
    # (newer Strands versions expose them; older versions ignore unknown attrs).
    for attr, value in (("keep_alive", "10m"), ("temperature", 0.7)):
        try:
            setattr(model, attr, value)
        except (AttributeError, TypeError):
            pass

    return model