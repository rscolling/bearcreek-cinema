"""CLI entry point for archive-agent.

The ``app`` Typer instance is the target of the ``archive-agent`` console
script (see pyproject.toml). Every subcommand in this scaffold is a stub
that prints ``not yet implemented`` and exits 1 — real implementations
land in later phase1/2/3 cards, one command group at a time.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import typer

if TYPE_CHECKING:
    from archive_agent.jellyfin.client import JellyfinClient
    from archive_agent.librarian.tv_sampler import Downloader

app = typer.Typer(
    name="archive-agent",
    help="Bear Creek Cinema: Archive.org to Jellyfin recommendation agent.",
    no_args_is_help=True,
    add_completion=False,
)


def _not_implemented(name: str) -> NoReturn:
    typer.echo(f"not yet implemented: {name}", err=True)
    raise typer.Exit(code=1)


@app.callback()
def _configure_logging_once() -> None:
    """Initialize structlog before any command runs.

    Reads ``[logging]`` from config.toml when it's loadable; falls back
    to a sane default (INFO, JSON) otherwise so even ``archive-agent
    --help`` produces consistent stderr lines if the logging layer is
    ever invoked there.
    """
    import os
    from typing import Literal

    from archive_agent.logging import configure_logging

    level = os.environ.get("ARCHIVE_AGENT_LOG_LEVEL", "INFO")
    fmt: Literal["json", "console"] = "json"
    try:
        from archive_agent.config import load_config

        cfg = load_config()
        level = cfg.logging.level
        fmt = cfg.logging.format
    except Exception:
        pass
    configure_logging(level=level, fmt=fmt)


# --- config ---
config_app = typer.Typer(no_args_is_help=True, help="Inspect and validate configuration.")
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show() -> None:
    """Print the parsed config to stdout (secrets redacted)."""
    from archive_agent.config import ConfigError, load_config

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(cfg.model_dump_json(indent=2))


@config_app.command("validate")
def config_validate() -> None:
    """Validate config.toml and environment interpolation.

    Exit codes: 0 = clean or warnings-only, 2 = errors.
    """
    from archive_agent.config import ConfigError, load_config, validate_config

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    warnings, errors = validate_config(cfg)
    for w in warnings:
        typer.echo(f"WARN:  {w}")
    for e in errors:
        typer.echo(f"ERROR: {e}", err=True)
    if errors:
        typer.echo(f"\n{len(errors)} error(s), {len(warnings)} warning(s).", err=True)
        raise typer.Exit(code=2)
    summary = f"Config OK ({len(warnings)} warning(s))." if warnings else "Config OK."
    typer.echo(summary)


# --- history ---
history_app = typer.Typer(no_args_is_help=True, help="Jellyfin watch history operations.")
app.add_typer(history_app, name="history")


@history_app.command("dump")
def history_dump(
    kind: str = typer.Option("any", "--type", help="movie | show | any"),
    since: str = typer.Option("", "--since", help="YYYY-MM-DD lower bound"),
) -> None:
    """Print watch history rows to stdout (one per line)."""
    import asyncio
    from datetime import datetime

    from archive_agent.config import ConfigError, load_config
    from archive_agent.jellyfin.client import JellyfinClient
    from archive_agent.jellyfin.history import fetch_episode_history, fetch_movie_history

    if kind not in {"movie", "show", "any"}:
        typer.echo(f"invalid --type={kind!r}; expected movie|show|any", err=True)
        raise typer.Exit(code=1)

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    since_dt: datetime | None = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError as exc:
            typer.echo(f"invalid --since={since!r}; expected YYYY-MM-DD", err=True)
            raise typer.Exit(code=1) from exc

    async def _run() -> None:
        async with JellyfinClient(
            cfg.jellyfin.url, cfg.jellyfin.api_key, cfg.jellyfin.user_id
        ) as client:
            if kind in ("movie", "any"):
                for m in await fetch_movie_history(client):
                    if since_dt and m.last_played_date and m.last_played_date < since_dt:
                        continue
                    typer.echo(
                        f"MOVIE  {m.jellyfin_item_id:<32}  "
                        f"plays={m.play_count} pct={m.played_percentage:5.1f}  "
                        f"{m.title[:60]}"
                    )
            if kind in ("show", "any"):
                for e in await fetch_episode_history(client):
                    if since_dt and e.last_played_date and e.last_played_date < since_dt:
                        continue
                    series = e.series_name or e.series_id
                    typer.echo(
                        f"EP     {e.jellyfin_item_id:<32}  "
                        f"plays={e.play_count} pct={e.played_percentage:5.1f}  "
                        f"{series} S{e.season:02d}E{e.episode:02d}"
                    )

    asyncio.run(_run())


@history_app.command("sync")
def history_sync(
    dry_run: bool = typer.Option(False, "--dry-run", help="Classify + count without writing"),
) -> None:
    """Ingest Jellyfin watch history into the state DB (idempotent)."""
    import asyncio

    from archive_agent.config import ConfigError, load_config
    from archive_agent.jellyfin.client import JellyfinClient
    from archive_agent.jellyfin.history import ingest_all_history
    from archive_agent.state.db import get_db, init_db

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    init_db(cfg.paths.state_db)
    conn = get_db()

    async def _run() -> object:
        async with JellyfinClient(
            cfg.jellyfin.url, cfg.jellyfin.api_key, cfg.jellyfin.user_id
        ) as client:
            return await ingest_all_history(client, conn, dry_run=dry_run)

    result = asyncio.run(_run())
    typer.echo(result.model_dump_json(indent=2))  # type: ignore[attr-defined]


# --- discover ---
@app.command()
def discover(
    collection: str = typer.Option("both", help="moviesandfilms | television | both"),
    limit: int = typer.Option(100, help="Max candidates per collection"),
) -> None:
    """Discover candidate items from Archive.org and upsert them into the state DB."""
    import asyncio

    from archive_agent.archive.discovery import discover as _run_discover
    from archive_agent.config import ConfigError, load_config
    from archive_agent.state.db import get_db, init_db

    if collection not in {"moviesandfilms", "television", "both"}:
        typer.echo(
            f"invalid --collection={collection!r}; expected moviesandfilms|television|both",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    init_db(cfg.paths.state_db)
    conn = get_db()

    result = asyncio.run(_run_discover(conn, cfg, collection=collection, limit=limit))  # type: ignore[arg-type]
    typer.echo(result.model_dump_json(indent=2))


# --- download ---
@app.command()
def download(
    archive_id: str = typer.Argument(..., help="Archive.org item identifier"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only, don't transfer"),
    dest: str = typer.Option(
        "/tmp/archive-agent/staging",
        "--dest",
        help="Staging directory (files are moved to /media/* by phase2-06)",
    ),
    zone: str = typer.Option(
        "recommendations",
        "--zone",
        help="Intended /media zone after placement (movies/tv/recommendations/tv-sampler)",
    ),
) -> None:
    """Download a single Archive.org item into the staging area."""
    import asyncio

    from archive_agent.archive.downloader import DownloadRequest, download_one
    from archive_agent.config import ConfigError, load_config
    from archive_agent.librarian.zones import Zone
    from archive_agent.state.db import get_db, init_db

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    try:
        zone_enum = Zone(zone)
    except ValueError as exc:
        typer.echo(f"invalid --zone={zone!r}; expected one of {[z.value for z in Zone]}", err=True)
        raise typer.Exit(code=1) from exc

    init_db(cfg.paths.state_db)
    conn = get_db()
    req = DownloadRequest(
        archive_id=archive_id,
        zone=zone_enum,
        dest_dir=Path(dest),
        dry_run=dry_run,
    )
    result = asyncio.run(
        download_one(req, conn, max_concurrent=cfg.librarian.max_concurrent_downloads)
    )
    typer.echo(result.model_dump_json(indent=2))
    if result.status == "failed":
        raise typer.Exit(code=2)


# --- recommend ---
@app.command()
def recommend(
    n: int = typer.Option(0, "--n", help="Shortlist size (0 = use config default)"),
    kind: str = typer.Option("any", "--type", help="movie | show | any"),
    provider: str = typer.Option(
        "", "--provider", help="ollama | claude | tfidf (blank = config)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Don't insert into ranked_candidates"
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Produce a shortlist of ranked candidates (two-stage pipeline)."""
    import asyncio

    from archive_agent.commands.recommend import (
        NoProfileError,
        RecommendResult,
    )
    from archive_agent.commands.recommend import recommend as _run_recommend
    from archive_agent.config import ConfigError, load_config
    from archive_agent.state.db import get_db, init_db
    from archive_agent.state.models import ContentType

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    if kind not in {"movie", "show", "any"}:
        typer.echo(f"invalid --type={kind!r}", err=True)
        raise typer.Exit(code=1)
    content_types: list[ContentType] | None = None
    if kind == "movie":
        content_types = [ContentType.MOVIE]
    elif kind == "show":
        content_types = [ContentType.SHOW]

    force_provider: str | None = None
    if provider:
        if provider not in {"ollama", "claude", "tfidf"}:
            typer.echo(f"invalid --provider={provider!r}", err=True)
            raise typer.Exit(code=1)
        force_provider = provider

    init_db(cfg.paths.state_db)
    conn = get_db()

    async def _run() -> RecommendResult:
        return await _run_recommend(
            conn,
            cfg,
            n=n or None,
            content_types=content_types,
            force_provider=force_provider,  # type: ignore[arg-type]
            dry_run=dry_run,
        )

    try:
        result = asyncio.run(_run())
    except NoProfileError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return

    if not result.items:
        typer.echo(
            f"No picks (provider={result.provider}, "
            f"prefilter={result.prefilter_size}, "
            f"excluded={result.excluded_count})."
        )
        return

    typer.echo(
        f"Provider: {result.provider}  "
        f"profile_version={result.profile_version}  "
        f"elapsed={result.elapsed_ms}ms  "
        f"batch={result.batch_id or '-'}"
    )
    typer.echo("")
    typer.echo(f"{'#':>3}  {'score':>5}  {'type':<6} {'year':<5}  title")
    typer.echo("-" * 80)
    for r in result.items:
        year = str(r.candidate.year) if r.candidate.year else "????"
        typer.echo(
            f"{r.rank:>3}  {r.score:>5.2f}  "
            f"{r.candidate.content_type.value:<6} {year:<5}  "
            f"{r.candidate.title[:55]}"
        )
        typer.echo(f"       {r.reasoning}")


# --- search (FTS5 catalog) ---
search_app = typer.Typer(no_args_is_help=True, help="Typo-tolerant catalog search.")
app.add_typer(search_app, name="search")


@search_app.command("fts")
def search_fts(
    query: str = typer.Argument(..., help="Query text"),
    type_filter: str = typer.Option("any", "--type", help="movie | show | any"),
    limit: int = typer.Option(20, "--limit", "-l", help="Max results"),
) -> None:
    """Run a typo-tolerant trigram FTS5 query over titles + descriptions."""
    from archive_agent.config import ConfigError, load_config
    from archive_agent.state.db import get_db, init_db
    from archive_agent.state.models import ContentType
    from archive_agent.state.queries import search as q_search

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    if type_filter not in {"movie", "show", "any"}:
        typer.echo(f"invalid --type={type_filter!r}", err=True)
        raise typer.Exit(code=1)
    ct: ContentType | None = None
    if type_filter == "movie":
        ct = ContentType.MOVIE
    elif type_filter == "show":
        ct = ContentType.SHOW

    init_db(cfg.paths.state_db)
    conn = get_db()

    results = q_search.fts_search(conn, query, limit=limit, content_type=ct)
    if not results:
        typer.echo("No matches.")
        return
    for cand, score in results:
        year = str(cand.year) if cand.year else "????"
        typer.echo(
            f"  {score:.3f}  {cand.content_type.value:<7} {year:<5}  "
            f"{cand.archive_id:<32}  {cand.title[:55]}"
        )


@search_app.command("autocomplete")
def search_autocomplete(
    prefix: str = typer.Argument(..., help="Title prefix"),
    limit: int = typer.Option(10, "--limit", "-l", help="Max suggestions"),
) -> None:
    """Prefix type-ahead over candidate titles."""
    from archive_agent.config import ConfigError, load_config
    from archive_agent.state.db import get_db, init_db
    from archive_agent.state.queries import search as q_search

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    init_db(cfg.paths.state_db)
    conn = get_db()

    suggestions = q_search.fts_autocomplete(conn, prefix, limit=limit)
    if not suggestions:
        typer.echo("No matches.")
        return
    for s in suggestions:
        typer.echo(f"  {s['archive_id']:<32}  {s['title']}")


# --- taste (show-state aggregator + rating reader) ---
taste_app = typer.Typer(no_args_is_help=True, help="Show-state aggregator + explicit ratings.")
app.add_typer(taste_app, name="taste")


@taste_app.command("aggregate")
def taste_aggregate() -> None:
    """Roll up episode watches into show-level binge events (idempotent)."""
    from archive_agent.config import ConfigError, load_config
    from archive_agent.state.db import get_db, init_db
    from archive_agent.taste import aggregate_all_shows

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    init_db(cfg.paths.state_db)
    conn = get_db()
    events = aggregate_all_shows(conn, cfg.taste)
    if not events:
        typer.echo("No new binge events.")
        return
    typer.echo(f"Emitted {len(events)} event(s):")
    for event in events:
        typer.echo(
            f"  {event.kind.value:<16} {event.show_id:<12} "
            f"strength={event.strength:.2f}  {event.timestamp.isoformat()}"
        )


@taste_app.command("bootstrap")
def taste_bootstrap(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Generate the profile without inserting it"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip y/N confirmation"),
    force: bool = typer.Option(
        False, "--force", help="Insert a new version even if one exists"
    ),
) -> None:
    """Synthesize the first TasteProfile from existing watch history."""
    import asyncio

    from archive_agent.config import ConfigError, load_config
    from archive_agent.ranking.factory import make_provider_for_workflow
    from archive_agent.state.db import get_db, init_db
    from archive_agent.taste import (
        NoSignalError,
        ProfileExistsError,
        bootstrap_profile,
    )

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    init_db(cfg.paths.state_db)
    conn = get_db()
    provider = make_provider_for_workflow("profile_update", cfg, conn=conn)

    async def _run() -> object:
        return await bootstrap_profile(conn, provider, dry_run=True, force=force)

    try:
        generated = asyncio.run(_run())
    except ProfileExistsError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except NoSignalError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(generated.model_dump_json(indent=2))  # type: ignore[attr-defined]

    if dry_run:
        typer.echo("\n(dry run — not inserted)")
        return

    if not yes:
        typer.echo("")
        confirm = typer.prompt("Insert this profile? [y/N]", default="n", show_default=False)
        if confirm.strip().lower() not in {"y", "yes"}:
            typer.echo("Aborted.")
            raise typer.Exit(code=1)

    async def _insert() -> object:
        return await bootstrap_profile(conn, provider, dry_run=False, force=force)

    final = asyncio.run(_insert())
    typer.echo(f"\nInserted version {final.version}.")  # type: ignore[attr-defined]


@taste_app.command("update")
def taste_update_profile(
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only, don't insert"),
    force: bool = typer.Option(False, "--force", help="Bypass rate-limit + min-events gates"),
) -> None:
    """Evolve the taste profile from events since the last version."""
    import asyncio

    from archive_agent.config import ConfigError, load_config
    from archive_agent.ranking.factory import make_provider_for_workflow
    from archive_agent.state.db import get_db, init_db
    from archive_agent.taste import apply_update, plan_update

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    init_db(cfg.paths.state_db)
    conn = get_db()

    plan = plan_update(conn, cfg.taste, force=force)

    typer.echo(f"current_version:    {plan.current_version}")
    typer.echo(f"events_since_last:  {plan.events_since_last}")
    typer.echo(f"events_to_send:     {len(plan.events_to_send)}")
    if plan.truncated:
        typer.echo(f"truncated:          {plan.truncated} (newest {len(plan.events_to_send)} kept)")
    typer.echo(f"should_run:         {plan.should_run}")
    if plan.skip_reason:
        typer.echo(f"skip_reason:        {plan.skip_reason}")

    if not plan.should_run or dry_run:
        return

    provider = make_provider_for_workflow("profile_update", cfg, conn=conn)

    async def _run() -> object:
        return await apply_update(conn, provider, plan)

    updated = asyncio.run(_run())
    typer.echo(f"\nInserted version {updated.version}.")  # type: ignore[attr-defined]


@taste_app.command("history")
def taste_history(
    limit: int = typer.Option(10, "--limit", help="Max versions to show"),
) -> None:
    """List recent ``taste_profile_versions`` entries (newest first)."""
    from archive_agent.config import ConfigError, load_config
    from archive_agent.state.db import get_db, init_db
    from archive_agent.state.queries import taste_profile_versions as q_profiles

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    init_db(cfg.paths.state_db)
    conn = get_db()

    versions = q_profiles.list_versions(conn, limit=limit)
    if not versions:
        typer.echo("No profile versions.")
        return
    for version, updated_at, snippet in versions:
        typer.echo(f"  v{version:<4} {updated_at.isoformat()}  {snippet}")


@taste_app.command("show-profile")
def taste_show_profile() -> None:
    """Print the latest TasteProfile (version, summary, lists)."""
    from archive_agent.config import ConfigError, load_config
    from archive_agent.state.db import get_db, init_db
    from archive_agent.state.queries import taste_profile_versions as q_profiles

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    init_db(cfg.paths.state_db)
    conn = get_db()

    profile = q_profiles.get_latest_profile(conn)
    if profile is None:
        typer.echo("No profile yet — run `archive-agent taste bootstrap`.")
        raise typer.Exit(code=1)

    typer.echo(f"version:       {profile.version}")
    typer.echo(f"updated_at:    {profile.updated_at.isoformat()}")
    typer.echo(f"\nsummary:\n  {profile.summary}\n")
    typer.echo(f"liked_genres:     {', '.join(profile.liked_genres) or '-'}")
    typer.echo(f"disliked_genres:  {', '.join(profile.disliked_genres) or '-'}")
    if profile.era_preferences:
        eras = ", ".join(
            f"{e.decade}s({e.weight:+.1f})" for e in profile.era_preferences
        )
        typer.echo(f"era_preferences:  {eras}")
    typer.echo(f"liked_ids:        {len(profile.liked_archive_ids)} movies / "
               f"{len(profile.liked_show_ids)} shows")
    typer.echo(f"disliked_ids:     {len(profile.disliked_archive_ids)} movies / "
               f"{len(profile.disliked_show_ids)} shows")


@taste_app.command("show")
def taste_show(
    show_id: str = typer.Argument(..., help="Show identifier (as in candidates.show_id)"),
) -> None:
    """Print the current ShowState + latest rating + next aggregator decision."""
    from datetime import UTC, datetime

    from archive_agent.config import ConfigError, load_config
    from archive_agent.state.db import get_db, init_db
    from archive_agent.state.queries import show_state as q_show_state
    from archive_agent.taste import (
        evaluate_show,
        latest_for_show,
        refresh_show_state,
    )

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    init_db(cfg.paths.state_db)
    conn = get_db()

    state = refresh_show_state(conn, show_id)
    if state is None:
        # refresh wrote nothing — fall back to existing row if any.
        state = q_show_state.get(conn, show_id)
    if state is None:
        typer.echo(f"No data for show_id={show_id!r} (no watches or episode candidates).")
        raise typer.Exit(code=1)

    rating = latest_for_show(conn, show_id)
    outcome = evaluate_show(state, cfg.taste, datetime.now(UTC))

    typer.echo(f"show_id:              {state.show_id}")
    typer.echo(f"episodes_finished:    {state.episodes_finished}")
    typer.echo(f"episodes_abandoned:   {state.episodes_abandoned}")
    typer.echo(f"episodes_available:   {state.episodes_available}")
    typer.echo(f"started_at:           {state.started_at.isoformat()}")
    last = state.last_playback_at.isoformat() if state.last_playback_at else "-"
    typer.echo(f"last_playback_at:     {last}")
    emitted = state.last_emitted_event.value if state.last_emitted_event else "-"
    typer.echo(f"last_emitted_event:   {emitted}")
    if rating is not None:
        typer.echo(f"latest_rating:        {rating.kind.value} ({rating.strength:.2f})")
    else:
        typer.echo("latest_rating:        -")
    typer.echo(f"next_action:          {outcome.action}")
    typer.echo(f"reason:               {outcome.reason}")


# --- rank (prefilter + index ops) ---
rank_app = typer.Typer(no_args_is_help=True, help="TF-IDF prefilter + index operations.")
app.add_typer(rank_app, name="rank")


def _tfidf_index_path(state_db: Path) -> Path:
    """Pickle sits next to state.db so backups scoop both up."""
    return state_db.parent / "tfidf_index.pkl"


@rank_app.command("rebuild-index")
def rank_rebuild_index() -> None:
    """Fit a fresh TF-IDF matrix from the candidates table and save it."""
    from archive_agent.config import ConfigError, load_config
    from archive_agent.ranking.tfidf import TFIDFIndex
    from archive_agent.state.db import get_db, init_db

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    init_db(cfg.paths.state_db)
    conn = get_db()
    index = TFIDFIndex.build(conn)
    path = _tfidf_index_path(cfg.paths.state_db)
    index.save(path)
    typer.echo(f"Indexed {index.size} candidates -> {path}")


@rank_app.command("ollama")
def rank_ollama(
    n: int = typer.Option(5, "--n", help="Number of picks"),
    k: int = typer.Option(50, "--k", help="Prefilter shortlist size before rerank"),
    type_filter: str = typer.Option("any", "--type", help="movie | show | any"),
    genres: str = typer.Option(
        "",
        "--genres",
        help="Comma-separated liked genres (smoke-test profile)",
    ),
) -> None:
    """Run the full two-stage pipeline against the local Ollama model."""
    import asyncio
    from datetime import UTC, datetime

    from archive_agent.config import ConfigError, load_config
    from archive_agent.ranking.ollama_provider import OllamaProvider
    from archive_agent.ranking.tfidf import TFIDFIndex, prefilter
    from archive_agent.state.db import get_db, init_db
    from archive_agent.state.models import ContentType, TasteProfile
    from archive_agent.taste import latest_for_all_shows

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    if type_filter not in {"movie", "show", "any"}:
        typer.echo(f"invalid --type={type_filter!r}", err=True)
        raise typer.Exit(code=1)

    init_db(cfg.paths.state_db)
    conn = get_db()

    index = TFIDFIndex.build(conn)
    if index.size == 0:
        typer.echo("No candidates — run `archive-agent discover` first.", err=True)
        raise typer.Exit(code=1)

    profile = TasteProfile(
        version=0,
        updated_at=datetime.now(UTC),
        liked_genres=[g.strip() for g in genres.split(",") if g.strip()],
    )
    content_types: list[ContentType] | None
    if type_filter == "movie":
        content_types = [ContentType.MOVIE]
    elif type_filter == "show":
        content_types = [ContentType.SHOW]
    else:
        content_types = None

    shortlist = prefilter(index, conn, profile, k=k, content_types=content_types)
    if not shortlist:
        typer.echo("Prefilter produced no candidates.")
        return

    candidates = [c for c, _ in shortlist]
    ratings = latest_for_all_shows(conn)
    provider = OllamaProvider(cfg.llm.ollama, conn=conn)

    picks = asyncio.run(provider.rank(profile, candidates, n=n, ratings=ratings))
    if not picks:
        typer.echo("No picks returned.")
        return
    for r in picks:
        cand = r.candidate
        year = str(cand.year) if cand.year else "????"
        typer.echo(
            f"  #{r.rank}  {r.score:.2f}  {cand.content_type.value:<7} {year}  "
            f"{cand.title[:55]}"
        )
        typer.echo(f"        {r.reasoning}")


@rank_app.command("claude")
def rank_claude(
    n: int = typer.Option(5, "--n", help="Number of picks"),
    k: int = typer.Option(50, "--k", help="Prefilter shortlist size"),
    type_filter: str = typer.Option("any", "--type", help="movie | show | any"),
    genres: str = typer.Option("", "--genres", help="Smoke-test liked genres"),
) -> None:
    """Run the two-stage pipeline with ClaudeProvider (costs real money)."""
    import asyncio
    from datetime import UTC, datetime

    from archive_agent.config import ConfigError, load_config
    from archive_agent.ranking.claude_provider import ClaudeProvider
    from archive_agent.ranking.tfidf import TFIDFIndex, prefilter
    from archive_agent.state.db import get_db, init_db
    from archive_agent.state.models import ContentType, TasteProfile
    from archive_agent.taste import latest_for_all_shows

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    if cfg.llm.claude.api_key is None:
        typer.echo(
            "Claude is not configured — set ANTHROPIC_API_KEY and "
            "[llm.claude].api_key in config.toml.",
            err=True,
        )
        raise typer.Exit(code=2)

    if type_filter not in {"movie", "show", "any"}:
        typer.echo(f"invalid --type={type_filter!r}", err=True)
        raise typer.Exit(code=1)

    init_db(cfg.paths.state_db)
    conn = get_db()

    index = TFIDFIndex.build(conn)
    if index.size == 0:
        typer.echo("No candidates — run `archive-agent discover` first.", err=True)
        raise typer.Exit(code=1)

    profile = TasteProfile(
        version=0,
        updated_at=datetime.now(UTC),
        liked_genres=[g.strip() for g in genres.split(",") if g.strip()],
    )
    content_types: list[ContentType] | None
    if type_filter == "movie":
        content_types = [ContentType.MOVIE]
    elif type_filter == "show":
        content_types = [ContentType.SHOW]
    else:
        content_types = None

    shortlist = prefilter(index, conn, profile, k=k, content_types=content_types)
    if not shortlist:
        typer.echo("Prefilter produced no candidates.")
        return

    candidates = [c for c, _ in shortlist]
    ratings = latest_for_all_shows(conn)
    provider = ClaudeProvider(cfg.llm.claude, conn=conn)

    picks = asyncio.run(provider.rank(profile, candidates, n=n, ratings=ratings))
    if not picks:
        typer.echo("No picks returned.")
        return
    for r in picks:
        cand = r.candidate
        year = str(cand.year) if cand.year else "????"
        typer.echo(
            f"  #{r.rank}  {r.score:.2f}  {cand.content_type.value:<7} {year}  "
            f"{cand.title[:55]}"
        )
        typer.echo(f"        {r.reasoning}")


@rank_app.command("prefilter")
def rank_prefilter(
    k: int = typer.Option(50, "--k", help="Shortlist size"),
    type_filter: str = typer.Option("any", "--type", help="movie | show | any"),
    genres: str = typer.Option(
        "",
        "--genres",
        help="Comma-separated liked genres (used when no profile exists yet)",
    ),
) -> None:
    """Rank candidates by cosine similarity to a taste profile.

    Until phase3-04 lands, passes ``--genres noir,western`` to build a
    smoke-test profile on the fly.
    """
    from datetime import UTC, datetime

    from archive_agent.config import ConfigError, load_config
    from archive_agent.ranking.tfidf import TFIDFIndex, prefilter
    from archive_agent.state.db import get_db, init_db
    from archive_agent.state.models import ContentType, TasteProfile

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    if type_filter not in {"movie", "show", "any"}:
        typer.echo(f"invalid --type={type_filter!r}; expected movie|show|any", err=True)
        raise typer.Exit(code=1)

    init_db(cfg.paths.state_db)
    conn = get_db()

    index = TFIDFIndex.build(conn)
    if index.size == 0:
        typer.echo("No candidates in DB — run `archive-agent discover` first.", err=True)
        raise typer.Exit(code=1)

    profile = TasteProfile(
        version=0,
        updated_at=datetime.now(UTC),
        liked_genres=[g.strip() for g in genres.split(",") if g.strip()],
    )

    content_types: list[ContentType] | None
    if type_filter == "movie":
        content_types = [ContentType.MOVIE]
    elif type_filter == "show":
        content_types = [ContentType.SHOW]
    else:
        content_types = None

    picks = prefilter(index, conn, profile, k=k, content_types=content_types)
    if not picks:
        typer.echo("No matches.")
        return
    for cand, score in picks:
        year = str(cand.year) if cand.year else "????"
        typer.echo(
            f"  {score:.3f}  {cand.content_type.value:<7} {year}  "
            f"{cand.archive_id:<40}  {cand.title[:60]}"
        )


# --- profile ---
profile_app = typer.Typer(no_args_is_help=True, help="Taste profile management.")
app.add_typer(profile_app, name="profile")


@profile_app.command("show")
def profile_show() -> None:
    """Print the current taste profile."""
    _not_implemented("profile show")


@profile_app.command("bootstrap")
def profile_bootstrap(
    provider: str = typer.Option("ollama", "--provider", help="ollama | claude"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Build the initial taste profile from existing watch history."""
    _not_implemented("profile bootstrap")


@profile_app.command("update")
def profile_update(
    provider: str = typer.Option("ollama", "--provider", help="ollama | claude"),
) -> None:
    """Incrementally update the taste profile from recent events."""
    _not_implemented("profile update")


# --- librarian ---
librarian_app = typer.Typer(no_args_is_help=True, help="Disk budget and eviction.")
app.add_typer(librarian_app, name="librarian")


@librarian_app.command("status")
def librarian_status() -> None:
    """Print disk usage per zone + budget headroom (JSON + one-line summary)."""
    from archive_agent.config import ConfigError, load_config
    from archive_agent.librarian import AGENT_MANAGED, Zone, budget_report

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    report = budget_report(cfg)
    typer.echo(report.model_dump_json(indent=2))

    def _gb(n: int) -> str:
        return f"{n / 1_000_000_000:,.1f} GB"

    pct = int(report.agent_used_bytes * 100 / report.budget_bytes) if report.budget_bytes else 0
    typer.echo(
        f"\nAgent-managed: {_gb(report.agent_used_bytes)} / "
        f"{_gb(report.budget_bytes)} ({pct}%), "
        f"{_gb(report.headroom_bytes)} headroom."
    )
    for z in Zone:
        u = next(u for u in report.zones if u.zone == z)
        tag = "" if z in AGENT_MANAGED else "  (user-owned, outside budget)"
        typer.echo(f"  {z.value:<16} {u.file_count:>6} files, {_gb(u.used_bytes):>10}{tag}")


@librarian_app.command("evict")
def librarian_evict(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without deleting"),
) -> None:
    """Plan + execute eviction against agent-managed zones.

    Walks /media/recommendations + /media/tv-sampler, picks oldest-stale
    items past their TTL, stops when cumulative free meets the overage.
    Without --dry-run the deletions also run.
    """
    from archive_agent.config import ConfigError, load_config
    from archive_agent.librarian import execute_eviction, plan_eviction
    from archive_agent.state.db import get_db, init_db

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    init_db(cfg.paths.state_db)
    conn = get_db()
    plan = plan_eviction(conn, cfg)
    typer.echo(plan.model_dump_json(indent=2))

    if dry_run:
        return

    result = execute_eviction(plan, conn)
    typer.echo("---")
    typer.echo(result.model_dump_json(indent=2))
    if plan.still_over_budget:
        raise typer.Exit(code=2)


@librarian_app.command("place")
def librarian_place(
    archive_id: str = typer.Argument(..., help="Archive.org item identifier"),
    zone: str = typer.Option(
        "recommendations",
        "--zone",
        help="Target zone (recommendations/tv/tv-sampler; not movies)",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report plan without moving"),
) -> None:
    """Move a completed download from staging into a /media zone."""
    from archive_agent.config import ConfigError, load_config
    from archive_agent.librarian import BudgetExceededError, PlacementError, Zone, place
    from archive_agent.state.db import get_db, init_db
    from archive_agent.state.queries import candidates as q_candidates

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    try:
        zone_enum = Zone(zone)
    except ValueError as exc:
        typer.echo(f"invalid --zone={zone!r}", err=True)
        raise typer.Exit(code=1) from exc

    init_db(cfg.paths.state_db)
    conn = get_db()

    candidate = q_candidates.get_by_archive_id(conn, archive_id)
    if candidate is None:
        typer.echo(f"no candidate with archive_id={archive_id!r}", err=True)
        raise typer.Exit(code=1)

    row = conn.execute(
        "SELECT path FROM downloads WHERE archive_id = ? AND status = 'done' "
        "ORDER BY id DESC LIMIT 1",
        (archive_id,),
    ).fetchone()
    if row is None or not row["path"]:
        typer.echo(
            f"no completed download on disk for {archive_id!r} — run "
            f"`archive-agent download {archive_id}` first",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        result = place(
            conn,
            cfg,
            candidate=candidate,
            source_path=Path(row["path"]),
            zone=zone_enum,
            dry_run=dry_run,
        )
    except BudgetExceededError as exc:
        typer.echo(f"BUDGET EXCEEDED: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except PlacementError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(result.model_dump_json(indent=2))


@librarian_app.command("promote")
def librarian_promote(
    archive_id: str = typer.Argument(..., help="Archive.org item identifier"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report plan without moving"),
) -> None:
    """Promote a candidate's folder from recommendations/tv-sampler to
    movies/tv. Content-type on the candidate picks the right variant."""
    from archive_agent.config import ConfigError, load_config
    from archive_agent.librarian import PlacementError, promote_movie, promote_show
    from archive_agent.state.db import get_db, init_db
    from archive_agent.state.models import ContentType
    from archive_agent.state.queries import candidates as q_candidates

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    init_db(cfg.paths.state_db)
    conn = get_db()

    candidate = q_candidates.get_by_archive_id(conn, archive_id)
    if candidate is None:
        typer.echo(f"no candidate with archive_id={archive_id!r}", err=True)
        raise typer.Exit(code=1)

    try:
        if candidate.content_type == ContentType.MOVIE:
            result = promote_movie(conn, cfg, candidate, dry_run=dry_run)
        else:
            result = promote_show(conn, cfg, candidate, dry_run=dry_run)
    except PlacementError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(result.model_dump_json(indent=2))


# --- jellyfin ---
jellyfin_app = typer.Typer(no_args_is_help=True, help="Inspect the configured Jellyfin server.")
app.add_typer(jellyfin_app, name="jellyfin")


def _open_jellyfin_client() -> JellyfinClient:
    """Build a ``JellyfinClient`` from the current config. Caller must
    enter the returned client as an async context manager."""
    from archive_agent.config import ConfigError, load_config
    from archive_agent.jellyfin.client import JellyfinClient

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    return JellyfinClient(cfg.jellyfin.url, cfg.jellyfin.api_key, cfg.jellyfin.user_id)


@jellyfin_app.command("users")
def jellyfin_users() -> None:
    """List users visible to the API key (with their GUIDs)."""
    import asyncio

    async def _run() -> None:
        async with _open_jellyfin_client() as client:
            for u in await client.list_users():
                admin = " (admin)" if u.policy.is_administrator else ""
                typer.echo(f"{u.id}  {u.name}{admin}")

    asyncio.run(_run())


@jellyfin_app.command("libraries")
def jellyfin_libraries() -> None:
    """List libraries visible to the configured user."""
    import asyncio

    async def _run() -> None:
        async with _open_jellyfin_client() as client:
            for lib in await client.list_libraries():
                typer.echo(f"{lib.id}  {lib.collection_type or '?':<10}  {lib.name}")

    asyncio.run(_run())


@jellyfin_app.command("scan")
def jellyfin_scan(
    zone: str = typer.Option(
        "all",
        "--zone",
        help="Zone to scan (movies/tv/recommendations/tv-sampler/all)",
    ),
) -> None:
    """Trigger a Jellyfin library scan on one or all zones."""
    import asyncio

    from archive_agent.jellyfin import MissingLibraryError, scan_zones
    from archive_agent.librarian.zones import Zone

    zones: list[Zone]
    if zone == "all":
        zones = list(Zone)
    else:
        try:
            zones = [Zone(zone)]
        except ValueError as exc:
            typer.echo(f"invalid --zone={zone!r}", err=True)
            raise typer.Exit(code=1) from exc

    async def _run() -> None:
        async with _open_jellyfin_client() as client:
            try:
                await scan_zones(client, zones)
            except MissingLibraryError as exc:
                typer.echo(f"ERROR: {exc}", err=True)
                raise typer.Exit(code=2) from exc

    asyncio.run(_run())
    typer.echo(f"Triggered scan on {len(zones)} zone(s): {[z.value for z in zones]}")


@jellyfin_app.command("resolve")
def jellyfin_resolve(
    archive_id: str = typer.Argument(..., help="Archive.org item identifier"),
    zone: str = typer.Option(
        "recommendations",
        "--zone",
        help="Zone the file was placed in (defaults to recommendations)",
    ),
    timeout: int = typer.Option(90, "--timeout", help="Seconds to wait for scan"),
) -> None:
    """Look up a placed candidate's Jellyfin ItemId and persist it."""
    import asyncio

    from archive_agent.config import ConfigError, load_config
    from archive_agent.jellyfin import MissingLibraryError, scan_and_resolve
    from archive_agent.librarian.zones import Zone
    from archive_agent.state.db import get_db, init_db

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    try:
        zone_enum = Zone(zone)
    except ValueError as exc:
        typer.echo(f"invalid --zone={zone!r}", err=True)
        raise typer.Exit(code=1) from exc

    init_db(cfg.paths.state_db)
    conn = get_db()

    async def _run() -> str | None:
        async with _open_jellyfin_client() as client:
            return await scan_and_resolve(
                client,
                conn,
                archive_id=archive_id,
                zone=zone_enum,
                timeout_s=timeout,
            )

    try:
        item_id = asyncio.run(_run())
    except MissingLibraryError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except ValueError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if item_id is None:
        typer.echo(
            f"TIMEOUT: Jellyfin didn't index {archive_id!r} within {timeout}s. "
            f"Try `archive-agent jellyfin resolve {archive_id}` again shortly.",
            err=True,
        )
        raise typer.Exit(code=2)
    typer.echo(f"{archive_id}  ->  jellyfin_item_id={item_id}")


# --- logs ---
logs_app = typer.Typer(no_args_is_help=True, help="Tail and inspect agent logs.")
app.add_typer(logs_app, name="logs")


@logs_app.command("tail")
def logs_tail(
    lines: int = typer.Option(50, "--lines", "-n", help="How many recent lines to show"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Stream new lines"),
) -> None:
    """Tail the agent's logs via `docker compose logs` (or journalctl)."""
    import shutil
    import subprocess

    candidates: list[list[str]] = []
    if shutil.which("docker"):
        cmd = ["docker", "compose", "logs", "--tail", str(lines)]
        if follow:
            cmd.append("-f")
        cmd.append("archive-agent")
        candidates.append(cmd)
    if shutil.which("journalctl"):
        cmd = ["journalctl", "--user", "-u", "archive-agent-daemon", "-n", str(lines)]
        if follow:
            cmd.append("-f")
        candidates.append(cmd)

    if not candidates:
        typer.echo(
            "neither `docker` nor `journalctl` is on PATH; nothing to tail. "
            "On don-quixote, run `cd /home/blueridge/archive-agent && "
            "docker compose logs -f archive-agent` manually.",
            err=True,
        )
        raise typer.Exit(code=1)

    for cmd in candidates:
        try:
            subprocess.run(cmd, check=False)
            return
        except FileNotFoundError:
            continue


# --- llm-calls ---
llm_calls_app = typer.Typer(no_args_is_help=True, help="Inspect the llm_calls audit log.")
app.add_typer(llm_calls_app, name="llm-calls")


@llm_calls_app.command("cost")
def llm_calls_cost(
    since: str = typer.Option(
        "", "--since", help="YYYY-MM-DD lower bound (inclusive)"
    ),
) -> None:
    """Sum Claude spend from ``llm_calls`` rows in the window."""
    from archive_agent.config import ConfigError, load_config
    from archive_agent.ranking.claude_provider import estimate_cost_cents
    from archive_agent.state.db import get_db, init_db

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    init_db(cfg.paths.state_db)
    conn = get_db()

    sql = (
        "SELECT model, input_tokens, output_tokens FROM llm_calls "
        "WHERE provider = 'claude'"
    )
    params: tuple[object, ...] = ()
    if since:
        sql += " AND timestamp >= ?"
        params = (since,)
    rows = conn.execute(sql, params).fetchall()

    by_model: dict[str, dict[str, float]] = {}
    total_cents = 0.0
    for row in rows:
        cents = estimate_cost_cents(
            row["model"], row["input_tokens"], row["output_tokens"]
        )
        agg = by_model.setdefault(
            row["model"], {"calls": 0, "input": 0, "output": 0, "cents": 0.0}
        )
        agg["calls"] += 1
        agg["input"] += int(row["input_tokens"] or 0)
        agg["output"] += int(row["output_tokens"] or 0)
        agg["cents"] += cents
        total_cents += cents

    if not rows:
        typer.echo("No Claude calls in window.")
        return
    typer.echo(f"{'model':<28} {'calls':>6} {'in_tok':>9} {'out_tok':>9} {'cost':>8}")
    for model, agg in sorted(by_model.items()):
        typer.echo(
            f"{model:<28} {int(agg['calls']):>6} "
            f"{int(agg['input']):>9} {int(agg['output']):>9} "
            f"${agg['cents'] / 100.0:>7.4f}"
        )
    typer.echo(f"\nTotal: ${total_cents / 100.0:.4f}")


@llm_calls_app.command("stats")
def llm_calls_stats(
    limit_recent: int = typer.Option(10, "--recent", help="Recent rows to list"),
) -> None:
    """Print call counts, percentile latencies, outcomes, and recent rows."""
    from archive_agent.config import ConfigError, load_config
    from archive_agent.state.db import get_db, init_db

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    init_db(cfg.paths.state_db)
    conn = get_db()

    total = conn.execute("SELECT COUNT(*) AS c FROM llm_calls").fetchone()["c"]
    if total == 0:
        typer.echo("No llm_calls rows yet. Run `archive-agent health ollama` first.")
        return

    typer.echo(f"Total calls: {total}\n")

    typer.echo("By provider:")
    for row in conn.execute(
        "SELECT provider, COUNT(*) AS c FROM llm_calls GROUP BY provider ORDER BY c DESC"
    ):
        typer.echo(f"  {row['provider']:<10} {row['c']}")

    typer.echo("\nBy outcome:")
    for row in conn.execute(
        "SELECT outcome, COUNT(*) AS c FROM llm_calls GROUP BY outcome ORDER BY c DESC"
    ):
        typer.echo(f"  {row['outcome']:<10} {row['c']}")

    typer.echo("\nLatency by (provider, workflow):")
    groups: dict[tuple[str, str], list[int]] = {}
    for row in conn.execute("SELECT provider, workflow, latency_ms FROM llm_calls"):
        groups.setdefault((row["provider"], row["workflow"]), []).append(row["latency_ms"])
    for (provider, workflow), latencies in sorted(groups.items()):
        latencies.sort()
        n = len(latencies)
        p50 = latencies[n // 2]
        p95 = latencies[min(n - 1, int(n * 0.95))]
        p99 = latencies[min(n - 1, int(n * 0.99))]
        typer.echo(f"  {provider:<8} {workflow:<18} n={n:<4} p50={p50}ms p95={p95}ms p99={p99}ms")

    typer.echo(f"\nLast {limit_recent} calls:")
    for row in conn.execute(
        "SELECT timestamp, provider, model, workflow, latency_ms, outcome "
        "FROM llm_calls ORDER BY id DESC LIMIT ?",
        (limit_recent,),
    ):
        typer.echo(
            f"  {row['timestamp']}  {row['provider']:<8} {row['workflow']:<16} "
            f"{row['latency_ms']:>6}ms  {row['outcome']:<10} {row['model']}"
        )


# --- tv-grouping ---
tv_grouping_app = typer.Typer(
    no_args_is_help=True,
    help="Classify TV episode candidates into shows (phase2-03).",
)
app.add_typer(tv_grouping_app, name="tv-grouping")


@tv_grouping_app.command("run")
def tv_grouping_run(
    limit: int = typer.Option(50, "--limit", "-l", help="Max episodes to classify"),
) -> None:
    """Classify ungrouped EPISODE candidates and persist matches."""
    import asyncio

    from archive_agent.archive.tv_grouping import group_unassigned_episodes
    from archive_agent.config import ConfigError, load_config
    from archive_agent.metadata import TmdbClient
    from archive_agent.state.db import get_db, init_db

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    init_db(cfg.paths.state_db)
    conn = get_db()

    async def _run() -> object:
        async with TmdbClient(cfg.tmdb.api_key, conn) as tmdb:
            return await group_unassigned_episodes(conn, tmdb, limit=limit)

    import json as _json

    result = asyncio.run(_run())
    typer.echo(_json.dumps(result.model_dump_for_cli(), indent=2))  # type: ignore[attr-defined]


@tv_grouping_app.command("review")
def tv_grouping_review(
    limit: int = typer.Option(20, "--limit", "-l", help="Max review rows to print"),
) -> None:
    """List unresolved entries in the TV grouping review queue."""
    from archive_agent.config import ConfigError, load_config
    from archive_agent.state.db import get_db, init_db

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    init_db(cfg.paths.state_db)
    conn = get_db()
    rows = conn.execute(
        "SELECT archive_id, confidence, suggested_show_id, reason, created_at "
        "FROM tv_grouping_review WHERE reviewed_at IS NULL "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    if not rows:
        typer.echo("No unresolved TV grouping reviews.")
        return
    for row in rows:
        typer.echo(
            f"  {row['archive_id']:<40}  {row['confidence']:<6}  "
            f"suggest={row['suggested_show_id'] or '-':<8}  {row['reason']}"
        )


# --- metadata ---
metadata_app = typer.Typer(no_args_is_help=True, help="TMDb metadata enrichment.")
app.add_typer(metadata_app, name="metadata")


@metadata_app.command("enrich")
def metadata_enrich(
    limit: int = typer.Option(50, "--limit", "-l", help="Max candidates to enrich"),
) -> None:
    """Fill missing genres / runtime / poster / description from TMDb."""
    import asyncio

    from archive_agent.config import ConfigError, load_config
    from archive_agent.metadata import TmdbClient, enrich_new_candidates
    from archive_agent.state.db import get_db, init_db

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    init_db(cfg.paths.state_db)
    conn = get_db()

    async def _run() -> object:
        async with TmdbClient(cfg.tmdb.api_key, conn) as client:
            return await enrich_new_candidates(conn, client, limit=limit)

    result = asyncio.run(_run())
    typer.echo(result.model_dump_json(indent=2))  # type: ignore[attr-defined]


# --- tv (sampler) ---
tv_app = typer.Typer(no_args_is_help=True, help="TV sampler-first flow (phase2-08).")
app.add_typer(tv_app, name="tv")


def _resolve_downloader() -> Downloader:
    """Returns a downloader callable bound to the module's semaphore."""
    from archive_agent.archive.downloader import (
        DownloadRequest,
        DownloadResult,
        download_one,
    )

    async def _dl(req: DownloadRequest, conn: sqlite3.Connection) -> DownloadResult:
        from archive_agent.config import load_config

        cfg = load_config()
        return await download_one(req, conn, max_concurrent=cfg.librarian.max_concurrent_downloads)

    return _dl


@tv_app.command("step")
def tv_step(
    show_id: str = typer.Argument(..., help="TMDb show id (as stored in candidates.show_id)"),
    show_title: str = typer.Option("", "--show-title", help="Human-readable show name for folders"),
) -> None:
    """Run one pass of the sampler state machine for a show."""
    import asyncio

    from archive_agent.config import ConfigError, load_config
    from archive_agent.librarian import step_show
    from archive_agent.state.db import get_db, init_db

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    init_db(cfg.paths.state_db)
    conn = get_db()
    downloader = _resolve_downloader()

    result = asyncio.run(
        step_show(
            conn,
            cfg,
            show_id,
            downloader,
            show_title=show_title or None,
        )
    )
    typer.echo(result.model_dump_json(indent=2))


@tv_app.command("sample")
def tv_sample(
    show_id: str = typer.Argument(..., help="TMDb show id (as stored in candidates.show_id)"),
    show_title: str = typer.Option("", "--show-title", help="Human-readable show name for folders"),
) -> None:
    """Alias for ``tv step`` — kicks off sampling if this show hasn't
    been sampled yet, otherwise runs a normal step. Useful for the
    Roku "force-commit this show" flow and for manual testing."""
    tv_step(show_id=show_id, show_title=show_title)


@tv_app.command("status")
def tv_status() -> None:
    """Print per-show sampler state: current phase + next decision."""
    import asyncio

    from archive_agent.config import ConfigError, load_config
    from archive_agent.librarian import decide_for_show
    from archive_agent.state.db import get_db, init_db

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    init_db(cfg.paths.state_db)
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT show_id FROM candidates "
        "WHERE content_type = 'episode' AND show_id IS NOT NULL ORDER BY show_id"
    ).fetchall()
    if not rows:
        typer.echo("No shows with episode candidates.")
        return

    async def _run() -> None:
        for row in rows:
            show_id = row["show_id"]
            decision = decide_for_show(conn, cfg, show_id)
            typer.echo(f"  {show_id:<12}  {decision.action:<14}  {decision.reason}")

    asyncio.run(_run())


# --- state ---
state_app = typer.Typer(no_args_is_help=True, help="State DB management.")
app.add_typer(state_app, name="state")


@state_app.command("init")
def state_init(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report pending migrations without writing"
    ),
) -> None:
    """Create the state DB and apply any pending migrations."""
    from archive_agent.config import ConfigError, load_config
    from archive_agent.state.db import init_db

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    applied = init_db(cfg.paths.state_db, dry_run=dry_run)
    verb = "Would apply" if dry_run else "Applied"
    if applied:
        typer.echo(f"{verb} migrations: {applied} at {cfg.paths.state_db}")
    else:
        typer.echo(f"No pending migrations at {cfg.paths.state_db}")


@state_app.command("info")
def state_info() -> None:
    """Print schema version and per-table row counts."""
    from archive_agent.state.db import get_db
    from archive_agent.state.migrations import current_version

    conn = get_db()
    typer.echo(f"Schema version: {current_version(conn)}")
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    typer.echo("Table row counts:")
    for row in rows:
        table = row["name"]
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        typer.echo(f"  {table:<28} {count}")


@state_app.command("backup")
def state_backup(
    dest: Path = typer.Argument(..., help="Destination file path"),  # noqa: B008
) -> None:
    """Copy the state DB to ``dest`` (parent dirs auto-created)."""
    import shutil

    from archive_agent.config import ConfigError, load_config

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    src = cfg.paths.state_db
    if not src.exists():
        typer.echo(f"no DB to back up at {src}", err=True)
        raise typer.Exit(code=2)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    typer.echo(f"Backed up {src} -> {dest}")


# --- serve / daemon ---
@app.command()
def serve(
    host: str = typer.Option("", help="Bind address (default: config.api.host)"),
    port: int = typer.Option(0, help="Bind port (default: config.api.port)"),
) -> None:
    """Run the FastAPI HTTP service for the Roku app."""
    import uvicorn

    from archive_agent.api.app import create_app
    from archive_agent.config import ConfigError, load_config

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    bind_host = host or cfg.api.host
    bind_port = port or cfg.api.port
    fastapi_app = create_app(cfg)

    uvicorn.run(
        fastapi_app,
        host=bind_host,
        port=bind_port,
        log_level=cfg.logging.level.lower(),
        access_log=False,  # our middleware logs every request already
    )


@app.command()
def daemon() -> None:
    """Run the async job loop: discovery, aggregation, ranking, librarian."""
    _not_implemented("daemon")


# --- health ---
health_app = typer.Typer(no_args_is_help=True, help="Subsystem health checks.")
app.add_typer(health_app, name="health")


@health_app.command("ollama")
def health_ollama() -> None:
    """Verify Ollama is reachable and the configured model is pulled."""
    import asyncio

    from archive_agent.config import ConfigError, load_config
    from archive_agent.ranking.factory import make_provider
    from archive_agent.state.db import get_db, init_db

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    init_db(cfg.paths.state_db)
    provider = make_provider("ollama", cfg, conn=get_db())
    status = asyncio.run(provider.health_check())
    typer.echo(status.model_dump_json(indent=2))
    if status.status != "ok":
        raise typer.Exit(code=2)


@health_app.command("claude")
def health_claude() -> None:
    """Verify the Anthropic API key is valid."""
    import asyncio

    from archive_agent.config import ConfigError, load_config
    from archive_agent.ranking.factory import make_provider
    from archive_agent.state.db import get_db, init_db

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    init_db(cfg.paths.state_db)
    provider = make_provider("claude", cfg, conn=get_db())
    status = asyncio.run(provider.health_check())
    typer.echo(status.model_dump_json(indent=2))
    if status.status != "ok":
        raise typer.Exit(code=2)


@health_app.command("jellyfin")
def health_jellyfin() -> None:
    """Verify Jellyfin is reachable and the API key works."""
    import asyncio
    import json as _json

    from archive_agent.config import ConfigError, load_config
    from archive_agent.jellyfin.client import JellyfinClient

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    async def _run() -> dict[str, str]:
        async with JellyfinClient(
            cfg.jellyfin.url, cfg.jellyfin.api_key, cfg.jellyfin.user_id
        ) as client:
            info = await client.ping()
            await client.authenticate()
            return {
                "status": "ok",
                "server_name": info.server_name,
                "version": info.version,
            }

    try:
        report = asyncio.run(_run())
    except Exception as exc:
        typer.echo(_json.dumps({"status": "down", "error": str(exc)}), err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(_json.dumps(report, indent=2))


@health_app.command("all")
def health_all() -> None:
    """Consolidated health report: Ollama, Claude (if configured),
    Jellyfin, state DB, and disk. Exits 2 if any component is down."""
    import asyncio
    import json as _json
    import shutil

    from archive_agent.config import ConfigError, load_config
    from archive_agent.jellyfin.client import JellyfinClient
    from archive_agent.ranking.factory import make_provider
    from archive_agent.state.db import get_db, init_db
    from archive_agent.state.migrations import current_version

    try:
        cfg = load_config()
    except ConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    init_db(cfg.paths.state_db)
    conn = get_db()

    async def _jellyfin() -> dict[str, object]:
        try:
            async with JellyfinClient(
                cfg.jellyfin.url, cfg.jellyfin.api_key, cfg.jellyfin.user_id
            ) as client:
                info = await client.ping()
                await client.authenticate()
                return {"status": "ok", "version": info.version, "server_name": info.server_name}
        except Exception as exc:
            return {"status": "down", "detail": f"{type(exc).__name__}: {exc}"}

    async def _ollama() -> dict[str, object]:
        provider = make_provider("ollama", cfg, conn=conn)
        status = await provider.health_check()
        return status.model_dump()

    async def _claude() -> dict[str, object] | None:
        if cfg.llm.claude.api_key is None:
            return None  # not configured; omit from report
        provider = make_provider("claude", cfg, conn=conn)
        status = await provider.health_check()
        return status.model_dump()

    async def _gather() -> dict[str, object]:
        jelly, ollama_s, claude_s = await asyncio.gather(_jellyfin(), _ollama(), _claude())
        out: dict[str, object] = {
            "ollama": ollama_s,
            "jellyfin": jelly,
            "state_db": {"status": "ok", "schema_version": current_version(conn)},
        }
        if claude_s is not None:
            out["claude"] = claude_s
        return out

    report = asyncio.run(_gather())

    # Disk check: total used across the four agent-managed zones vs. budget
    used_bytes = 0
    for path in (
        cfg.paths.media_movies,
        cfg.paths.media_tv,
        cfg.paths.media_recommendations,
        cfg.paths.media_tv_sampler,
    ):
        if path.exists():
            usage = shutil.disk_usage(path)
            used_bytes = max(used_bytes, usage.used)
    budget_gb = cfg.librarian.max_disk_gb
    report["disk"] = {
        "status": "ok",
        "used_gb": round(used_bytes / 1e9, 2),
        "budget_gb": budget_gb,
    }

    down = [
        name
        for name, sub in report.items()
        if isinstance(sub, dict) and sub.get("status") == "down"
    ]
    report["status"] = "ok" if not down else "down"

    typer.echo(_json.dumps(report, indent=2, default=str))
    if down:
        raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
