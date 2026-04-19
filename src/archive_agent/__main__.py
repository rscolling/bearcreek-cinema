"""CLI entry point for archive-agent.

The ``app`` Typer instance is the target of the ``archive-agent`` console
script (see pyproject.toml). Every subcommand in this scaffold is a stub
that prints ``not yet implemented`` and exits 1 — real implementations
land in later phase1/2/3 cards, one command group at a time.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import typer

if TYPE_CHECKING:
    from archive_agent.jellyfin.client import JellyfinClient

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
) -> None:
    """Download a single Archive.org item into its appropriate /media zone."""
    _not_implemented("download")


# --- recommend ---
@app.command()
def recommend(
    kind: str = typer.Option("any", "--type", help="movie | show | any"),
    n: int = typer.Option(5, "--n", help="Shortlist size"),
    provider: str = typer.Option("ollama", "--provider", help="ollama | claude | tfidf"),
) -> None:
    """Produce the nightly shortlist of ranked candidates."""
    _not_implemented("recommend")


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
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Apply eviction policies to bring usage under budget."""
    _not_implemented("librarian evict")


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
    host: str = typer.Option("0.0.0.0", help="Bind address"),
    port: int = typer.Option(8787, help="Bind port"),
) -> None:
    """Run the FastAPI HTTP service for the Roku app."""
    _not_implemented("serve")


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
