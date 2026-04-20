"""First-profile bootstrap — phase3-04.

The very first ``TasteProfile`` has to come from somewhere. Bootstrap
reads everything the DB already knows about household taste (movie
events from Jellyfin history, show-level binge events from the
aggregator, explicit ratings from Roku) and asks the LLM to synthesize
a coherent profile.

Re-runnable: ``--force`` replaces an existing profile by inserting a
new version. Idempotent within a single conversation: running it back-
to-back inserts two rows but converges on the same content if nothing
in the underlying signal changed.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from archive_agent.ranking.provider import LLMProvider
from archive_agent.state.models import (
    Candidate,
    TasteEvent,
    TasteEventKind,
    TasteProfile,
)
from archive_agent.state.queries import (
    candidates as q_candidates,
)
from archive_agent.state.queries import (
    taste_events as q_taste_events,
)
from archive_agent.state.queries import (
    taste_profile_versions as q_profiles,
)
from archive_agent.taste.profile_ops import preserve_ids
from archive_agent.taste.ratings import RATING_KINDS

# Movie-level events that the bootstrap treats as taste signal. The
# aggregator emits show-level BINGE_POSITIVE/NEGATIVE from episode
# watches (ADR-004); those are captured separately.
_IMPLICIT_MOVIE_KINDS = {
    TasteEventKind.FINISHED,
    TasteEventKind.REWATCHED,
    TasteEventKind.ABANDONED,
    TasteEventKind.REJECTED,
    TasteEventKind.APPROVED,
    TasteEventKind.DEFERRED,
}


class NoSignalError(RuntimeError):
    """Raised when bootstrap has literally nothing to work with."""


class ProfileExistsError(RuntimeError):
    """Raised when a profile already exists and ``--force`` wasn't passed."""


class BootstrapInput(BaseModel):
    """Everything the LLM sees when bootstrapping a profile."""

    movie_events: list[TasteEvent] = Field(default_factory=list)
    show_events: list[TasteEvent] = Field(default_factory=list)
    ratings: dict[str, TasteEvent] = Field(default_factory=dict)
    candidates_by_id: dict[str, Candidate] = Field(default_factory=dict)

    @property
    def total_events(self) -> int:
        return len(self.movie_events) + len(self.show_events) + len(self.ratings)

    def as_event_list(self) -> list[TasteEvent]:
        """Flatten into the list the LLM prompt expects.

        Explicit ratings are appended after implicit events so the
        prompt renders them last — they're the strongest priors.
        """
        return [*self.movie_events, *self.show_events, *self.ratings.values()]


def empty_profile() -> TasteProfile:
    """The version=0 sentinel profile used as "current" for bootstrap."""
    return TasteProfile(
        version=0,
        updated_at=datetime.now(UTC),
        summary="",
    )


def gather_bootstrap_input(conn: sqlite3.Connection) -> BootstrapInput:
    """Read everything relevant from the DB.

    Movie events keep their ``archive_id`` even if it's a ``jellyfin:``
    phantom that isn't in ``candidates`` — the prompt renderer falls
    back to the ID string when it can't find a title.
    """
    all_events = q_taste_events.list_since(conn, datetime.fromtimestamp(0, tz=UTC))

    movie_events: list[TasteEvent] = []
    show_events: list[TasteEvent] = []
    rating_pool: list[TasteEvent] = []

    for event in all_events:
        if event.kind in RATING_KINDS and event.source == "roku_api":
            rating_pool.append(event)
        elif event.kind in _IMPLICIT_MOVIE_KINDS and event.archive_id is not None:
            movie_events.append(event)
        elif event.kind in {
            TasteEventKind.BINGE_POSITIVE,
            TasteEventKind.BINGE_NEGATIVE,
        }:
            show_events.append(event)

    # Latest-wins per show — ADR-013. ``list_since`` is timestamp-ordered
    # ascending, so a later insert for the same show overwrites.
    ratings: dict[str, TasteEvent] = {}
    for event in rating_pool:
        if event.show_id is None:
            continue
        existing = ratings.get(event.show_id)
        if existing is None or event.timestamp >= existing.timestamp:
            ratings[event.show_id] = event

    # Resolve titles for the prompt: both archive_id and show_id keys.
    id_set: set[str] = set()
    for event in movie_events:
        if event.archive_id is not None:
            id_set.add(event.archive_id)
    for event in [*show_events, *ratings.values()]:
        if event.show_id is not None:
            id_set.add(event.show_id)

    candidates_by_id: dict[str, Candidate] = {}
    for cid in id_set:
        cand = q_candidates.get_by_archive_id(conn, cid)
        if cand is not None:
            candidates_by_id[cid] = cand
            continue
        # Show-level events use show_id; candidates store show_id on
        # episode rows. Pull one representative episode row to surface
        # the show's title/genres in the prompt.
        ep_row = conn.execute(
            "SELECT * FROM candidates WHERE show_id = ? "
            "AND content_type = 'episode' LIMIT 1",
            (cid,),
        ).fetchone()
        if ep_row is not None:
            candidates_by_id[cid] = q_candidates.get_by_archive_id(conn, ep_row["archive_id"]) or cand  # type: ignore[assignment]

    return BootstrapInput(
        movie_events=movie_events,
        show_events=show_events,
        ratings=ratings,
        candidates_by_id=candidates_by_id,
    )


async def bootstrap_profile(
    conn: sqlite3.Connection,
    provider: LLMProvider,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> TasteProfile:
    """Build the first ``TasteProfile`` and (unless ``dry_run``) insert it.

    Raises ``ProfileExistsError`` if a profile is already present and
    ``force`` is not set. Raises ``NoSignalError`` when there is no
    signal at all — running bootstrap before Jellyfin history sync is
    a user error worth surfacing.
    """
    existing = q_profiles.get_latest_profile(conn)
    if existing is not None and not force:
        raise ProfileExistsError(
            f"profile version {existing.version} already exists; "
            "use --force to replace it"
        )

    inp = gather_bootstrap_input(conn)
    if inp.total_events == 0:
        raise NoSignalError(
            "no taste signal in DB — run `archive-agent history sync` "
            "and `archive-agent taste aggregate` first"
        )

    generated = await provider.update_profile(empty_profile(), inp.as_event_list())
    final = preserve_ids(empty_profile(), generated, inp.as_event_list())

    if dry_run:
        return final

    q_profiles.insert_profile(conn, final)
    return q_profiles.get_latest_profile(conn) or final


__all__ = [
    "BootstrapInput",
    "NoSignalError",
    "ProfileExistsError",
    "bootstrap_profile",
    "empty_profile",
    "gather_bootstrap_input",
]
