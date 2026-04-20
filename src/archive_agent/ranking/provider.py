"""LLMProvider Protocol and supporting types.

Three implementations ship with the agent:

- ``OllamaProvider`` (default, local) — qwen2.5:7b via instructor.
- ``ClaudeProvider`` (optional, cloud) — Anthropic API. Activated by
  ``[llm.workflows]`` settings; never silently.
- ``TFIDFProvider`` (fallback) — no LLM at all; pure cosine similarity
  over the TF-IDF candidate features. Kept alive so the system still
  produces output when Ollama is down.

All three follow the same shape so the fallback chain (Ollama → TF-IDF,
never → Claude) is a trivial swap at the factory layer.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from archive_agent.state.models import (
    Candidate,
    RankedCandidate,
    SearchFilter,
    TasteEvent,
    TasteProfile,
)

__all__ = ["HealthStatus", "LLMProvider"]


class HealthStatus(BaseModel):
    """Shape returned by ``LLMProvider.health_check``."""

    status: Literal["ok", "degraded", "down"]
    detail: str = ""
    model: str | None = None
    latency_ms: int | None = None


@runtime_checkable
class LLMProvider(Protocol):
    """Interface implemented by OllamaProvider, ClaudeProvider, TFIDFProvider.

    Behavioral invariants (also enforced by CONTRACTS.md §2):

    - ``rank`` never raises for malformed model output. Catch and fall
      back to a trivial ordering (descending TF-IDF score).
    - ``update_profile`` preserves liked/disliked IDs even if the model
      drops them from its reply, and must increment version.
    - ``parse_search`` returns ``SearchFilter`` with unset fields
      rather than raising on unparseable queries.
    - Every call (including ``health_check``) writes one row to the
      ``llm_calls`` table via the shared state DB connection.
    """

    name: str  # "ollama" | "claude" | "tfidf"

    async def health_check(self) -> HealthStatus: ...

    async def rank(
        self,
        profile: TasteProfile,
        candidates: list[Candidate],
        n: int = 5,
        *,
        ratings: dict[str, TasteEvent] | None = None,
    ) -> list[RankedCandidate]: ...

    async def update_profile(
        self,
        current: TasteProfile,
        events: list[TasteEvent],
    ) -> TasteProfile: ...

    async def parse_search(self, query: str) -> SearchFilter: ...
