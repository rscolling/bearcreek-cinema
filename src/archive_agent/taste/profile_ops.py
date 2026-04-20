"""Profile post-processing shared by bootstrap + incremental update.

The LLM's structured output is trusted for prose but NOT for ID lists
— it sometimes summarizes away concrete IDs, and explicit ratings
(ADR-013) must always win over the LLM's interpretation.
"""

from __future__ import annotations

from archive_agent.state.models import (
    TasteEvent,
    TasteEventKind,
    TasteProfile,
)

_POSITIVE_KINDS = {
    TasteEventKind.FINISHED,
    TasteEventKind.REWATCHED,
    TasteEventKind.APPROVED,
    TasteEventKind.BINGE_POSITIVE,
    TasteEventKind.RATED_UP,
    TasteEventKind.RATED_LOVE,
}
_NEGATIVE_KINDS = {
    TasteEventKind.ABANDONED,
    TasteEventKind.REJECTED,
    TasteEventKind.BINGE_NEGATIVE,
    TasteEventKind.RATED_DOWN,
}


def preserve_ids(
    old: TasteProfile,
    new: TasteProfile,
    events: list[TasteEvent],
) -> TasteProfile:
    """Return ``new`` with its ID lists reconciled against ``old`` + ``events``.

    - Base: union of ``old``'s and ``new``'s liked/disliked lists.
    - Each positive event moves its archive_id / show_id into the liked
      bucket and drops it from disliked.
    - Each negative event does the opposite.
    - Explicit ratings (RATED_DOWN / RATED_UP / RATED_LOVE) override
      everything — they're the most recent deliberate statement of
      taste and ADR-013 requires them to stick.
    """
    liked_archive: set[str] = set(old.liked_archive_ids) | set(new.liked_archive_ids)
    disliked_archive: set[str] = set(old.disliked_archive_ids) | set(
        new.disliked_archive_ids
    )
    liked_show: set[str] = set(old.liked_show_ids) | set(new.liked_show_ids)
    disliked_show: set[str] = set(old.disliked_show_ids) | set(new.disliked_show_ids)

    for event in events:
        if event.kind in _POSITIVE_KINDS:
            if event.archive_id is not None:
                liked_archive.add(event.archive_id)
                disliked_archive.discard(event.archive_id)
            if event.show_id is not None:
                liked_show.add(event.show_id)
                disliked_show.discard(event.show_id)
        elif event.kind in _NEGATIVE_KINDS:
            if event.archive_id is not None:
                disliked_archive.add(event.archive_id)
                liked_archive.discard(event.archive_id)
            if event.show_id is not None:
                disliked_show.add(event.show_id)
                liked_show.discard(event.show_id)

    return new.model_copy(
        update={
            "liked_archive_ids": sorted(liked_archive),
            "disliked_archive_ids": sorted(disliked_archive),
            "liked_show_ids": sorted(liked_show),
            "disliked_show_ids": sorted(disliked_show),
        }
    )


__all__ = ["preserve_ids"]
