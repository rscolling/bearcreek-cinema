# Security Policy

## Reporting a vulnerability

**Please do not open a public issue** for security vulnerabilities.

Email: `security@agenticagent.example` (replace with real address before
publishing)

Include:

- A description of the issue
- Steps to reproduce
- What an attacker could do with it
- Whether you've disclosed it anywhere else

You'll get a response within 7 days.

## What this project does with sensitive data

Bear Creek Cinema processes personal data in a few places:

- **Jellyfin watch history** — stored in a local SQLite DB on your
  server. Not transmitted anywhere unless you explicitly configure the
  Claude provider, in which case ranking prompts include watch history
  context.
- **API keys** — Jellyfin, TMDb, and optionally Anthropic. These are
  read from environment variables, never logged, and redacted from any
  log output that might include them.
- **No analytics, no telemetry.** The agent does not phone home to
  anyone.

## What counts as a vulnerability

- Arbitrary code execution, path traversal, or command injection in the
  agent or HTTP API
- Authentication or authorization bypass in the HTTP API
- Secret exposure in logs, the `llm_calls` table, or any other persisted
  state
- A way to exceed the configured `max_disk_gb` budget (data-integrity
  boundary)
- A way to delete `/media/movies` content without the documented opt-in
  (data-integrity boundary)
- Supply-chain issues in the dependency tree that affect this project

## What doesn't count

- The HTTP API is LAN-bound by default and has no auth in v1. This is
  documented behavior, not a vulnerability. Exposing it to the public
  internet is your call; don't.
- Ollama and Jellyfin have their own security postures. Report issues in
  those projects to those projects.
- Recommendations you find offensive, boring, or weird are not security
  issues. They might be bugs worth filing.

## Disclosure

Once a fix ships, the vulnerability will be disclosed in the changelog
and, if significant, a GitHub security advisory. You'll be credited by
name unless you prefer otherwise.
