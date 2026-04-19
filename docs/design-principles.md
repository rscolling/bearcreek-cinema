# Design Principles

The stance behind the design. Read these if you want to understand why
Bear Creek Cinema is shaped the way it is.

---

## Local-first, cloud-optional

Watch history is personal data. A recommendation system doesn't need to
send it to a third party to be useful.

The default deployment uses Ollama on the home server for every LLM
workflow. A compatible Claude API provider is available as an opt-in
"premium" tier, configured per workflow. Never a silent fallback.

Privacy and cost are both solved by this stance. So is offline operation.

## Graceful degradation, never silent failure

When the LLM is down, recommendations fall through to TF-IDF content
similarity. When Archive.org is throttling, downloads back off and retry.
When TMDb can't match a title, the item ships with lower-confidence
metadata and a flag for review. The system degrades in legible ways and
keeps operating.

If an error is a real problem, it gets logged visibly and surfaced in
the health endpoint. If it's a recoverable degradation, it's recorded
and the system continues.

## The right size of infrastructure

At O(10⁴) candidates and low hundreds of taste events per year, in-memory
TF-IDF with scikit-learn beats any vector database on latency, operational
cost, and explainability. SQLite beats Postgres at this scale. A single
FastAPI process beats multi-worker Gunicorn.

Reach for bigger infrastructure when the data demands it, not when the
buzzword justifies it.

## Interface-frozen contracts

Schemas, HTTP endpoints, CLI signatures, and the LLM provider protocol
are written down once and treated as frozen. Changes require an explicit
Architectural Decision Record. This slows down the kind of casual drift
that turns a small system into a tangled one after six months.

When something crosses the interface boundary, the question is "does the
contract already cover this?" — not "what's the quickest way to make it
work?"

## Observability before you need it

Every LLM call is persisted to an audit table: provider, model, workflow,
latency, input/output tokens, outcome. The librarian writes to its own
audit table. Every downloaded file's path, size, and zone is recorded.

Reasons for this: debugging model regressions, tuning disk budgets,
catching silent quality drops. All cheap to build in from the start, all
expensive to retrofit.

## Decisions are documented

`DECISIONS.md` holds the architectural decision log: context, decision,
consequences for every non-trivial choice. When future-me (or a
contributor) asks "why is this built this way?", the answer is two
clicks away.

Decisions that don't make it into the log are decisions we'll end up
rediscussing.

## Scope the LLM's job narrowly

A 7B local model is asked to rerank 50 pre-filtered candidates against
a 300-word prose profile. It is not asked to read 500 candidates and a
2000-word context and produce a sophisticated ranking. The former is a
job local models do well; the latter is a job you'll spend weeks
engineering around.

Structured output via Pydantic schemas. Explicit instructions. Small
prompts. Retries with fallback. Don't ask the LLM to do what ordinary
code can do well.

## Real files over virtual state

The recommendations library is real video files in a real Jellyfin
library. When a recommendation is approved and watched, the file moves
to a different real library. No stubs, no placeholder metadata, no
.nfo gymnastics. Jellyfin scans files the way it always does; Roku
sees them the way it always does.

## Deep-link, don't rebuild

The custom Roku app handles browsing and selection. It does not handle
playback. Playback hands off to the official Jellyfin Roku client via
ECP deep-linking. The Jellyfin team has already solved playback; we
benefit from their work rather than duplicating it.

Same principle applies elsewhere: use the `internetarchive` library
rather than writing a scraper; use `ia-get` for resumable downloads
rather than a custom transfer manager; use Ollama's native structured
output rather than prompt-engineering JSON.

## Respect the user's intent

The agent never surprise-deletes anything the user has interacted with.
Any destructive action on committed content is logged before execution
with a grace period. The librarian will refuse a download that would
exceed the configured budget rather than silently delete committed
content to make room.

If the user rejects a recommendation, the agent remembers. Same title,
same rejection — not recommended again without a clear intervening
positive signal.

## Small is a feature

One Python package. One SQLite file. Two systemd units. One Roku app.
No container orchestration, no message queues, no service mesh, no
observability stack beyond structured logs. If this project ever needs
those things, something has gone wrong.

Systems that stay small stay maintainable. The number of moving parts
in Bear Creek Cinema is a design target, not an accident.
