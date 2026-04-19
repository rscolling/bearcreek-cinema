# Contributing

Thanks for taking an interest. This is primarily a personal project, but
contributions are welcome — especially bug reports, design pushback, and
fixes for things you hit in your own deployment.

## How to contribute

**Report a bug.** Open an issue with the `bug` label. Include:
- What you were trying to do
- What actually happened
- The smallest reproducer you can produce
- Output of `archive-agent health all`
- Your config (redacted) if relevant

**Propose a feature or design change.** Open an issue with the
`proposal` label. If it touches existing architecture, please read
[claude-code-pack/DECISIONS.md](claude-code-pack/DECISIONS.md) first —
most major decisions have ADRs explaining why they're the way they are.
"I disagree with ADR-004" is a totally valid issue title; "can we add a
vector DB?" without reading ADR-009 is not.

**Submit a pull request.** For anything non-trivial, open an issue first
to sanity-check the approach. For trivial fixes (typos, obvious bugs,
doc improvements), just send the PR.

## Working with the code

The codebase is built to be worked with Claude Code. If you're
developing manually, you'll still find
[claude-code-pack/](claude-code-pack/) useful — it's where all the
decisions, contracts, and task cards live.

### Setup

```bash
git clone https://github.com/<you>/bearcreek-cinema.git
cd bearcreek-cinema
bash claude-code-pack/scripts/bootstrap-dev.sh
```

### Standards

- Python 3.11+, typed. `mypy --strict` passes on all new code.
- Tests accompany every non-trivial function. See
  [TESTING.md](claude-code-pack/TESTING.md).
- Commits follow `[phaseN-NN] component: short description` when the
  change maps to a task card; freeform otherwise but keep the subject
  under 72 characters.
- No commented-out code in merged PRs. Delete it or keep it.

### Before submitting a PR

```bash
pre-commit run --all-files
pytest tests/unit/
mypy --strict src/archive_agent
ruff check src/ tests/
```

All of these must pass.

## Things that get rejected

- PRs that silently change things listed in `CONTRACTS.md` without a
  corresponding ADR update
- PRs that add dependencies not in `ARCHITECTURE.md`'s stack table
  without a justification
- PRs that regress test coverage below current levels
- PRs that remove type hints or add `# type: ignore` without explanation

## Code of conduct

Be decent. Disagreement is fine; disrespect is not. See
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Security

Please do not open a public issue for security vulnerabilities. See
[SECURITY.md](SECURITY.md) for how to report them privately.

## License

By contributing, you agree that your contributions will be licensed
under the [MIT license](LICENSE).
