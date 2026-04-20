"""LLMProvider interface + implementations (phase1-05 skeleton).

Module boundary (GUARDRAILS.md): providers are the only place the agent
talks to Ollama / Claude / TF-IDF. Other modules get a provider via
``make_provider_for_workflow`` and use the Protocol; they don't import
the concrete classes.
"""

from archive_agent.ranking.claude_provider import ClaudeProvider
from archive_agent.ranking.factory import (
    FallbackProvider,
    make_fallback_provider,
    make_provider,
    make_provider_for_workflow,
)
from archive_agent.ranking.ollama_provider import OllamaProvider
from archive_agent.ranking.provider import HealthStatus, LLMProvider
from archive_agent.ranking.tfidf_provider import TFIDFProvider

__all__ = [
    "ClaudeProvider",
    "FallbackProvider",
    "HealthStatus",
    "LLMProvider",
    "OllamaProvider",
    "TFIDFProvider",
    "make_fallback_provider",
    "make_provider",
    "make_provider_for_workflow",
]
