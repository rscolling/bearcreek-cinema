# phase1-01: Repository scaffold

## Goal

Set up the project skeleton with all directories, tooling, and a working
`archive-agent --help` command.

## Prerequisites

- Python 3.11+ available
- `uv` or `pip` available

## Inputs

- Repository layout from `CLAUDE.md`
- Stack choices from `ARCHITECTURE.md` (Stack table)
- Dependency list from `CONTRACTS.md`

## Deliverables

1. `pyproject.toml` with:
   - Project metadata (name `archive-agent`, version `0.1.0`)
   - Python 3.11+ requirement
   - Dependencies: `pydantic>=2`, `httpx`, `fastapi`, `uvicorn`,
     `structlog`, `typer` (or `click` — use `typer` for type-first CLI),
     `instructor`, `ollama`, `anthropic`, `internetarchive`,
     `scikit-learn`, `numpy`, `tomli`/`tomllib` (stdlib in 3.11),
     `python-dotenv`
   - Dev dependencies: `pytest`, `pytest-asyncio`, `pytest-cov`, `mypy`,
     `ruff`, `pre-commit`, `types-*` as needed
   - Console script `archive-agent = archive_agent.__main__:app`
   - `[tool.mypy]` strict config
   - `[tool.ruff]` config with line length 100, Python 3.11 target

2. Directory skeleton under `src/archive_agent/`:
   ```
   __init__.py              (version string)
   __main__.py              (Typer app with --help working)
   config.py                (empty module docstring)
   state/__init__.py
   archive/__init__.py
   jellyfin/__init__.py
   taste/__init__.py
   ranking/__init__.py
   librarian/__init__.py
   api/__init__.py
   metadata/__init__.py
   loop.py                  (stub)
   ```

3. `tests/` directory:
   ```
   tests/__init__.py
   tests/conftest.py        (pytest fixtures stub)
   tests/unit/__init__.py
   tests/integration/__init__.py
   tests/fixtures/          (empty)
   ```

4. Root files:
   - `.gitignore` (Python + IDE + `.env` + `dev-media/`)
   - `.env.example` (fields per ENVIRONMENT.md)
   - `.pre-commit-config.yaml` (ruff + mypy + pytest unit)
   - `README.md` (brief — points to `claude-code-pack/`)
   - `config.example.toml` (skeleton matching CONTRACTS.md section 5)

5. `archive-agent --help` runs successfully and shows subcommand
   placeholders for: `config`, `history`, `discover`, `download`,
   `recommend`, `profile`, `librarian`, `serve`, `daemon`, `health`.

## Done when

- [ ] `pip install -e .` succeeds in a fresh venv
- [ ] `archive-agent --help` prints help with all subcommand groups listed
- [ ] `archive-agent config --help` and other subcommand help all work
  (subcommands can be stubs that print "not yet implemented")
- [ ] `pytest tests/` runs (0 tests, 0 failures — just confirms collection
  works)
- [ ] `mypy --strict src/archive_agent` passes
- [ ] `ruff check src/ tests/` passes
- [ ] `pre-commit run --all-files` passes
- [ ] `SESSION.md` updated: current status reflects "scaffold complete,
  ready for phase1-02"; new Recent Sessions entry added with outcome

## Verification commands (paste output into commit message)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
archive-agent --help
pytest tests/
mypy --strict src/archive_agent
ruff check src/ tests/
pre-commit run --all-files
```

## Out of scope

- Any real command implementations (stubs only)
- Config loading (that's phase1-02)
- Database (phase1-03)

## Estimated effort

30-60 minutes.

## Notes

- Use Typer for the CLI. It's first-class with Pydantic and gives us
  typed arguments for free.
- Typer app name must be `app` in `__main__.py` (matches the console
  script target).
- Every stub subcommand should print `"not yet implemented"` and exit 1
  (not 0) so no one mistakes a stub for working code.
