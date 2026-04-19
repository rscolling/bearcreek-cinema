"""CLI entry point for archive-agent.

The ``app`` Typer instance is the target of the ``archive-agent`` console
script (see pyproject.toml). Every subcommand in this scaffold is a stub
that prints ``not yet implemented`` and exits 1 — real implementations
land in later phase1/2/3 cards, one command group at a time.
"""

from __future__ import annotations

from typing import NoReturn

import typer

app = typer.Typer(
    name="archive-agent",
    help="Bear Creek Cinema: Archive.org to Jellyfin recommendation agent.",
    no_args_is_help=True,
    add_completion=False,
)


def _not_implemented(name: str) -> NoReturn:
    typer.echo(f"not yet implemented: {name}", err=True)
    raise typer.Exit(code=1)


# --- config ---
config_app = typer.Typer(no_args_is_help=True, help="Inspect and validate configuration.")
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show() -> None:
    """Print the parsed config to stdout."""
    _not_implemented("config show")


@config_app.command("validate")
def config_validate() -> None:
    """Validate config.toml and environment interpolation."""
    _not_implemented("config validate")


# --- history ---
history_app = typer.Typer(no_args_is_help=True, help="Jellyfin watch history operations.")
app.add_typer(history_app, name="history")


@history_app.command("dump")
def history_dump(
    kind: str = typer.Option("any", "--type", help="movie | show | any"),
    since: str = typer.Option("", "--since", help="YYYY-MM-DD lower bound"),
) -> None:
    """Print watch history rows to stdout."""
    _not_implemented("history dump")


@history_app.command("sync")
def history_sync() -> None:
    """Pull the latest watch history from Jellyfin into the state DB."""
    _not_implemented("history sync")


# --- discover ---
@app.command()
def discover(
    collection: str = typer.Option("both", help="moviesandfilms | television | both"),
    limit: int = typer.Option(100, help="Max candidates to fetch"),
) -> None:
    """Discover candidate items from Archive.org."""
    _not_implemented("discover")


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
    """Print disk usage per zone and budget headroom."""
    _not_implemented("librarian status")


@librarian_app.command("evict")
def librarian_evict(
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Apply eviction policies to bring usage under budget."""
    _not_implemented("librarian evict")


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
    _not_implemented("health ollama")


@health_app.command("claude")
def health_claude() -> None:
    """Verify the Anthropic API key is valid."""
    _not_implemented("health claude")


@health_app.command("jellyfin")
def health_jellyfin() -> None:
    """Verify Jellyfin is reachable and the API key works."""
    _not_implemented("health jellyfin")


@health_app.command("all")
def health_all() -> None:
    """Run every health check and return a consolidated JSON report."""
    _not_implemented("health all")


if __name__ == "__main__":
    app()
