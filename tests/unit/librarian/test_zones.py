"""Zone enum + path resolution."""

from __future__ import annotations

from archive_agent.config import Config
from archive_agent.librarian.zones import AGENT_MANAGED, USER_OWNED, Zone, zone_path


def test_zone_values_match_directory_names() -> None:
    """The enum values are the on-disk directory names; the schema's
    CHECK constraint on downloads.zone uses these exact strings."""
    assert Zone.MOVIES.value == "movies"
    assert Zone.TV.value == "tv"
    assert Zone.RECOMMENDATIONS.value == "recommendations"
    assert Zone.TV_SAMPLER.value == "tv-sampler"


def test_user_owned_contains_only_movies() -> None:
    assert {Zone.MOVIES} == USER_OWNED


def test_agent_managed_contains_every_non_user_zone() -> None:
    assert {Zone.TV, Zone.RECOMMENDATIONS, Zone.TV_SAMPLER} == AGENT_MANAGED


def test_user_owned_and_agent_managed_are_disjoint() -> None:
    assert USER_OWNED.isdisjoint(AGENT_MANAGED)


def test_every_zone_is_categorized() -> None:
    """Regression — if we add a zone we must put it in one of the two sets."""
    assert set(Zone) == USER_OWNED | AGENT_MANAGED


def test_zone_path_resolution(config: Config) -> None:
    assert zone_path(Zone.MOVIES, config) == config.paths.media_movies
    assert zone_path(Zone.TV, config) == config.paths.media_tv
    assert zone_path(Zone.RECOMMENDATIONS, config) == config.paths.media_recommendations
    assert zone_path(Zone.TV_SAMPLER, config) == config.paths.media_tv_sampler
