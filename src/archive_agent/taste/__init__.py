"""Household taste profile — the unified signal layer.

``aggregator`` rolls episode watches into show-level ``TasteEvent``s
(ADR-004). ``ratings`` exposes the ADR-013 3-thumb show ratings as a
latest-wins reader. Profile bootstrap / incremental update land in
phase3-04 / phase3-05.
"""

from archive_agent.taste.aggregator import (
    BingeAction,
    BingeOutcome,
    aggregate_all_shows,
    evaluate_show,
    refresh_show_state,
)
from archive_agent.taste.bootstrap import (
    BootstrapInput,
    NoSignalError,
    ProfileExistsError,
    bootstrap_profile,
    empty_profile,
    gather_bootstrap_input,
)
from archive_agent.taste.ratings import (
    RATING_KINDS,
    latest_for_all_shows,
    latest_for_show,
)

__all__ = [
    "RATING_KINDS",
    "BingeAction",
    "BingeOutcome",
    "BootstrapInput",
    "NoSignalError",
    "ProfileExistsError",
    "aggregate_all_shows",
    "bootstrap_profile",
    "empty_profile",
    "evaluate_show",
    "gather_bootstrap_input",
    "latest_for_all_shows",
    "latest_for_show",
    "refresh_show_state",
]
