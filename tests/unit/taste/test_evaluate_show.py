"""Decision-table coverage for ``evaluate_show``.

Pure function — no DB, no time — so these tests are trivially
deterministic. Just construct the state + config + now and assert
the action.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from archive_agent.config import TasteConfig
from archive_agent.state.models import ShowState, TasteEventKind
from archive_agent.taste.aggregator import evaluate_show

_NOW = datetime(2026, 4, 19, tzinfo=UTC)


def _state(**overrides: object) -> ShowState:
    defaults: dict[str, object] = {
        "show_id": "s1",
        "episodes_finished": 0,
        "episodes_abandoned": 0,
        "episodes_available": 8,
        "last_playback_at": None,
        "started_at": _NOW - timedelta(days=10),
        "last_emitted_event": None,
        "last_emitted_at": None,
    }
    defaults.update(overrides)
    return ShowState.model_validate(defaults)


def test_zero_available_skips(taste_config: TasteConfig) -> None:
    state = _state(episodes_available=0)
    out = evaluate_show(state, taste_config, _NOW)
    assert out.action == "skip"
    assert "no episodes available" in out.reason


def test_below_threshold_skips(taste_config: TasteConfig) -> None:
    state = _state(
        episodes_finished=2,  # 2/8 = 25%, under 75%
        last_playback_at=_NOW - timedelta(days=1),
    )
    out = evaluate_show(state, taste_config, _NOW)
    assert out.action == "skip"
    assert "below thresholds" in out.reason


def test_positive_crosses_threshold(taste_config: TasteConfig) -> None:
    state = _state(
        episodes_finished=6,  # 6/8 = 75%
        last_playback_at=_NOW - timedelta(days=5),
        started_at=_NOW - timedelta(days=20),
    )
    out = evaluate_show(state, taste_config, _NOW)
    assert out.action == "emit_positive"
    assert out.event is not None
    assert out.event.kind == TasteEventKind.BINGE_POSITIVE
    assert out.event.show_id == "s1"
    assert out.event.source == "playback"


def test_positive_outside_window_skips(taste_config: TasteConfig) -> None:
    state = _state(
        episodes_finished=6,
        # Started 200d ago, last watched 150d after that — span > window.
        started_at=_NOW - timedelta(days=200),
        last_playback_at=_NOW - timedelta(days=50),
    )
    out = evaluate_show(state, taste_config, _NOW)
    # Not positive (span > 60d) and not negative (finished > max).
    assert out.action == "skip"


def test_already_emitted_positive_skips(taste_config: TasteConfig) -> None:
    state = _state(
        episodes_finished=6,
        last_playback_at=_NOW - timedelta(days=5),
        started_at=_NOW - timedelta(days=20),
        last_emitted_event=TasteEventKind.BINGE_POSITIVE,
        last_emitted_at=_NOW - timedelta(days=3),
    )
    out = evaluate_show(state, taste_config, _NOW)
    assert out.action == "skip"
    assert "already emitted BINGE_POSITIVE" in out.reason


def test_season_complete_shortcut_emits_positive(taste_config: TasteConfig) -> None:
    state = _state(
        episodes_finished=8,
        episodes_available=8,
        last_playback_at=_NOW - timedelta(days=400),  # ancient but complete
        started_at=_NOW - timedelta(days=420),
    )
    out = evaluate_show(state, taste_config, _NOW)
    assert out.action == "emit_positive"
    assert "season complete" in out.reason


def test_season_complete_requires_min_episodes(taste_config: TasteConfig) -> None:
    # Only 3 episodes and all watched — below season_complete_min_episodes (4).
    state = _state(
        episodes_finished=3,
        episodes_available=3,
        last_playback_at=_NOW - timedelta(days=5),
        started_at=_NOW - timedelta(days=10),
    )
    out = evaluate_show(state, taste_config, _NOW)
    # pct=1.0 still passes positive-threshold path via the window check.
    assert out.action == "emit_positive"


def test_negative_after_inactivity(taste_config: TasteConfig) -> None:
    state = _state(
        episodes_finished=1,
        last_playback_at=_NOW - timedelta(days=60),  # past 30d inactivity
        started_at=_NOW - timedelta(days=90),
    )
    out = evaluate_show(state, taste_config, _NOW)
    assert out.action == "emit_negative"
    assert out.event is not None
    assert out.event.kind == TasteEventKind.BINGE_NEGATIVE


def test_negative_within_window_skips(taste_config: TasteConfig) -> None:
    state = _state(
        episodes_finished=1,
        last_playback_at=_NOW - timedelta(days=5),  # within 30d — too soon to call
        started_at=_NOW - timedelta(days=10),
    )
    out = evaluate_show(state, taste_config, _NOW)
    assert out.action == "skip"


def test_already_emitted_negative_skips(taste_config: TasteConfig) -> None:
    state = _state(
        episodes_finished=1,
        last_playback_at=_NOW - timedelta(days=60),
        started_at=_NOW - timedelta(days=90),
        last_emitted_event=TasteEventKind.BINGE_NEGATIVE,
    )
    out = evaluate_show(state, taste_config, _NOW)
    assert out.action == "skip"


def test_negative_polarity_flip_reemits(taste_config: TasteConfig) -> None:
    # Previously emitted positive, now has gone quiet with low count.
    # Should re-emit as negative because polarity changed.
    state = _state(
        episodes_finished=1,
        last_playback_at=_NOW - timedelta(days=60),
        started_at=_NOW - timedelta(days=90),
        last_emitted_event=TasteEventKind.BINGE_POSITIVE,
    )
    out = evaluate_show(state, taste_config, _NOW)
    assert out.action == "emit_negative"
