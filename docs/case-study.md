# Case Study: Bear Creek Cinema

**An agent-driven home cinema, designed for clarity and built to be maintained.**

**Status:** Design complete, implementation in progress.
**Role:** Architect and sole developer.
**Timeframe:** Design Q1 2026, build ongoing.

---

## The situation

A classic public-domain film catalog — the Internet Archive's collections
of pre-1970 Hollywood, European cinema, and early television — exists in
public view but has no meaningful discovery layer. Browsing
`archive.org/details/moviesandfilms` is rewarding if you know what you're
looking for and punishing if you don't. Nothing bridges the gap between
"I finished *The Third Man* last night" and "*The Stranger* is on
Archive.org in a decent transfer and you'd probably like it next."

The household has a Jellyfin server, Roku devices, and classic-film
taste. It does not have time to spend each evening curating.

## The problem I actually wanted to solve

Three layered questions had to be answered together, not separately:

1. **Discovery.** How do we systematically find high-quality candidates
   from two messy Archive.org collections (movies and television), each
   with inconsistent metadata?
2. **Judgment.** How does the system learn what this household actually
   watches and finishes, as opposed to what they start and abandon?
3. **UX.** How does a recommendation become a watched film without
   leaving the couch?

Any one of these has a thousand half-solutions already. The interesting
problem is the system that answers all three at once without buckling
under its own complexity.

## What I chose not to build

Good design is often visible in the what-not list.

- **Not a custom Jellyfin plugin.** Two good ones already exist
  (`jellyfin-plugin-localrecs`, `SuggestArr`). Competing on the same
  surface didn't interest me.
- **Not a vector database.** At the data scale involved (~10⁴
  candidates, low hundreds of taste events per year), in-memory TF-IDF
  with scikit-learn is faster, simpler, and more explainable than any
  vector store. Reaching for Qdrant or Pinecone here would be dressing
  up a small problem in big-system clothes.
- **Not a cloud-first system.** Watch history is personal. Ranking a
  personal library shouldn't require sending that history to a third
  party. Local-first was non-negotiable; cloud was allowed only as an
  optional enhancement.
- **Not a custom Roku video player.** BrightScript playback is a world
  of pain the Jellyfin team has already solved. The custom Roku app
  handles browsing and deep-links into the official Jellyfin client
  for playback.
- **Not a real-time streaming front to Archive.org.** Archive.org is not
  a CDN. Everything downloads first.
- **Not a fine-tuned model.** Base Ollama models are adequate for the
  ranking task; a LoRA over a household's 500-film history would
  overfit badly and take more effort than the benefit justifies.

Each of these "no" decisions saved weeks of work without losing anything
real.

## What I chose to build

A single agent on the home server that:

- Continuously watches the Archive.org movie and TV collections for
  candidates meeting quality filters
- Ingests Jellyfin watch history and maintains a unified household
  taste profile, with explicit signal-weighting rules that keep TV
  playback volume from drowning out film preferences
- Ranks candidates via a two-stage pipeline — TF-IDF prefilter to reduce
  the pool, then a local LLM (Ollama running `qwen2.5:7b`) reranking
  the top 50 with natural-language reasoning
- Manages disk as a first-class concern, with tiered storage zones,
  time-based eviction, and a sampler-first policy for TV that downloads
  three episodes before committing to a full season
- Exposes a LAN-bound HTTP API consumed by a custom Roku app
  ("Bear Creek Cinema") that presents recommendations and, on
  selection, deep-links into the official Jellyfin Roku client for
  playback

The system has graceful degradation built in: if the local LLM is
unavailable or returning malformed output, recommendations fall through
to pure TF-IDF similarity. The pipeline never fails to produce output.

## Notable decisions

### Local LLM as default; cloud as optional

Ollama handles all LLM workflows by default. `qwen2.5:7b` covers
ranking, profile updates, and natural-language search parsing. A
compatible Claude API provider exists for workflows where ranking
quality matters more than cost and privacy — but it's opt-in per
workflow, not a silent fallback.

This matters beyond the project itself. The local-first, cloud-optional,
graceful-fallback pattern is exactly the shape of system most
privacy-conscious mid-market enterprises want when they finally get
serious about AI. Building it for a personal project is good practice
for shipping it for a client.

### Episodes are noise; show-binges are signal

The original naive design treated every episode playback as a taste
event. This would have broken the profile within weeks: a household
watching 40 episodes of one show produces 40 events, drowning out a
dozen movie finishes. The fix is a show-state aggregator that only
emits a taste event when a show crosses a binge threshold (75% episodes
finished in 60 days, full season completion, or prolonged neglect).
Movies produce events directly. Signal volume balances across content
types without artificial weighting.

### Real files, not stubs

The initial instinct was to populate Jellyfin's recommendation library
with stub MP4 placeholders that the agent later swapped for real
downloads. This would have required custom `.nfo` handling, library-
refresh gymnastics, and a brittle swap mechanism. The cleaner approach
— borrowed from `jellyfin-plugin-localrecs` — is to just download real
files into a dedicated Jellyfin library from the start, and move them
between libraries as lifecycle dictates. Simpler, more robust, zero
Jellyfin trickery.

### Deep-link, don't rebuild

The custom Roku app is ~600 lines of BrightScript and SceneGraph. It
could have been a full Jellyfin client fork, but that would have
doubled the project scope for no UX gain. Instead, it presents the
recommendation surface (grid, detail, voice search) and hands off to
the official Jellyfin Roku app for playback via ECP deep-linking.
This is the right-sized piece of custom work.

## What I learned worth sharing

**Build the observability before you need it.** Every LLM call is
persisted to an audit table with provider, model, workflow, latency,
and outcome. This was extra work up front; it paid for itself the
first time a model upgrade produced mysteriously worse rankings. You
want the data before you need it, not after.

**Interface-first design compounds.** The `LLMProvider` protocol sat at
the center of the system from day one, with three implementations
(Ollama, Claude, TF-IDF) behind one shape. This made the fallback path
free rather than an afterthought, made provider swaps a config change,
and made testing straightforward.

**Decisions logged are decisions defended.** The `DECISIONS.md` file
with architectural decision records is the single most important
document in the project. Every non-trivial choice has a three-section
ADR: context, decision, consequences. When somebody (including future
me) asks "why is this built this way?", the answer is two clicks away.

**Small, frozen contracts prevent drift.** Writing down the Pydantic
schemas, HTTP endpoints, and CLI signatures before implementing them —
and calling them "frozen" — kept the scope tight. When a task threatens
to break a contract, that's now a visible decision to make, not an
invisible one you discover at integration time.

**Scope the LLM's job narrowly.** The local 7B model isn't asked to
read 500 candidates and a 2000-word profile and produce a sophisticated
ranking. It's asked to rerank 50 pre-filtered candidates against a
300-word prose profile, with explicit Pydantic schema enforcement on
its output. That's a job small models can do well. Asking them to do
more than that is setting up failures you'll then need to engineer
around.

## Where I went wrong (so far)

**I initially designed the system around a stub-MP4 approval mechanism
on the existing Jellyfin Roku app.** Rob (the user, who is me) correctly
pushed back and asked for a custom Roku app. The redesign was clearly
better. It's a useful reminder that sometimes the "pragmatic" answer
is a worse user experience in disguise, and the "more work" answer
actually simplifies the whole system downstream.

**I sized the first phase plan too coarsely.** What was originally
"Phase 1: scaffold + Jellyfin + LLM smoke test" turned out to be six
distinct task cards once broken down. Smaller units of work with
explicit done-when criteria scale better to Claude Code execution and
to human-paced weekend development.

## When I would have used a vector database

A common question from reviewers of this project: "Why no RAG? No
Qdrant, no pgvector, no embeddings?" It's worth answering directly,
because the decision not to use a vector database here is a decision,
not an oversight.

Bear Creek Cinema's retrieval problem decomposes into three distinct
jobs: catalog search ("play *The Third Man*"), intent search
("something noir and short"), and recommendation retrieval (tonight's
picks). I looked at each and asked whether dense embeddings would
outperform the simpler tooling available.

**Catalog search isn't semantic.** Users type or say the title they
want. The real challenge is typo tolerance and ASR drift — "thrid man"
for *The Third Man*, "beverly hilbillies" missing a consonant. SQLite
FTS5 with trigram tokenization handles this natively at character
level. Embedding models don't — they understand meaning, not typos.
The right tool is trigram FTS.

**Intent search has a controllable structured form.** "Something noir
and short" parses cleanly to
`{genres: [film-noir], max_runtime_minutes: 100}`. That's a filter,
not a similarity query. A small LLM parses it reliably with structured
output; a SQL query applies it. Embedding the query and doing nearest-
neighbor adds latency and noise without adding capability.

**Recommendation ranking uses known-item signals.** The taste profile
holds liked and disliked item IDs, genres, era preferences, and a
300-word prose summary. TF-IDF over structured features
(genre + decade + cast + content-type) produces ranking signal that's
as good as or better than 768-dim plot embeddings at this corpus size,
and it's explainable — I can tell the user why something scored high.

**The corpus is small enough that in-memory beats a service.** 20,000
items at most. In-memory cosine similarity over a sparse TF-IDF matrix
runs in tens of milliseconds. Running Qdrant as a separate service
adds operational weight (installation, upgrades, backups, monitoring)
for latency improvements I can't measure.

**When I would reach for a vector database:**

- Corpus over ~100,000 items where in-memory sparse similarity stops
  fitting comfortably in RAM
- Unstructured text as the primary retrieval target, where the
  structured features available to Bear Creek Cinema (genre, decade,
  cast) don't exist — think: research papers, legal documents,
  customer support transcripts, product reviews
- Semantic queries that require understanding, not filtering
  ("bleak postwar European films about moral compromise" could work;
  on our 20K-film corpus the structured taste profile does this better)
- Multi-tenant systems where one embedding index serves many users,
  and index-sharing is cheaper than per-user feature computation

None of those apply here. Somewhere else they will, and I've built a
companion project — [`claude-docs-rag`](../../claude-docs-rag) — as a
reference implementation for that regime. It uses Qdrant on-prem,
sentence-transformers for embeddings, an evaluation harness, and
honest cost/latency numbers. That's the right shape of project for
demonstrating RAG patterns. Bear Creek Cinema is the right shape of
project for demonstrating when to not use them.

This distinction — knowing which regime you're in and building
accordingly — is the judgment call that matters most in real systems.
Reaching for infrastructure because it's impressive, or declining to
reach for it because it's trendy, are both failures of the same kind.

## What this demonstrates

The project serves as a reference implementation for a pattern I
increasingly see clients asking for: local-first AI systems with
cloud augmentation, built to be operable and maintainable by a small
team (or one person) over years rather than impressive for one demo.

The techniques translate directly:

- Privacy-conscious data flows (local inference by default)
- Graceful degradation under provider failure
- Observable systems (every model call audited)
- Interface-frozen contracts as a hedge against drift
- Decision records as organizational memory

Bear Creek Cinema is one specific application of these techniques. The
same shape applies to compliance workflows, customer-facing ranking
systems, internal knowledge bases, and most other places where
"sometimes use AI, sometimes don't, never silently break" is the
requirement.

---

*Bear Creek Cinema is a personal project of [Rob
Ross](https://github.com/<you>), who also runs
[Agentic Agent](https://agenticagent.example), an independent AI
consulting practice focused on legacy system modernization and
agentic workflow design for mid-market enterprises.*
