# CV / Resume Bullets — Bear Creek Cinema

Drafts at different lengths and emphases. Pick, edit, and
personalize. All content is true as of the current design; update
"status" language once you ship.

---

## One-line summary (for headers, taglines)

> Local-first agentic recommendation system for self-hosted media servers,
> built on Ollama with Claude API as optional cloud tier.

Alternative angles:

> Self-hosted film and TV discovery agent: Ollama, Jellyfin, Roku, and a
> two-stage LLM ranking pipeline with graceful TF-IDF fallback.

> Production-shaped personal project demonstrating local-first AI patterns:
> privacy-preserving, degradation-tolerant, observable, small.

---

## Short (3-4 bullets, for a résumé project entry)

**Bear Creek Cinema & claude-docs-rag** — Paired reference implementations *(2026, personal projects)*

- **Bear Creek Cinema:** Designed and built an agent that curates
  public-domain films and classic TV from the Internet Archive,
  learning household taste from Jellyfin playback and surfacing
  recommendations on a custom Roku app. Deliberately uses SQLite FTS5
  and in-memory TF-IDF rather than a vector database — corpus size and
  structured features made heavier infrastructure wrong for the
  problem.
- **claude-docs-rag:** Companion project demonstrating the opposite
  architectural call. On-prem retrieval-augmented generation over
  Anthropic documentation using Qdrant vector database, hybrid dense +
  sparse retrieval with reciprocal rank fusion, cross-encoder
  reranking, and answer synthesis with citation validation. Includes an
  evaluation harness with published latency, cost, and faithfulness
  numbers.
- Implemented a local-first LLM architecture across both projects with
  Ollama (`qwen2.5:7b`) and optional Claude API tier; Pydantic-enforced
  structured outputs and `LLMProvider` abstraction enable per-workflow
  provider selection
- Produced full architecture specifications including Architectural
  Decision Records, frozen interface contracts, and task breakdowns
  designed for execution with Claude Code

---

## Medium (5-6 bullets, for a portfolio section)

**Bear Creek Cinema** — *Architect and sole developer, 2026*

A self-hosted home cinema system that discovers public-domain films and
classic TV on the Internet Archive, learns taste from Jellyfin playback
history, and delivers recommendations through a custom Roku app that
deep-links to the Jellyfin player.

- **Architecture.** Designed a local-first agent on Python 3.11+ with
  SQLite state, FastAPI service, and a pluggable `LLMProvider` protocol
  supporting Ollama, Claude, and TF-IDF implementations behind one
  interface. Default deployment runs entirely on a home server with no
  cloud dependencies.
- **Ranking.** Implemented a two-stage pipeline — scikit-learn TF-IDF
  prefilter reducing ~500 candidates to ~50, then a local LLM
  (`qwen2.5:7b` via Ollama) reranking with natural-language reasoning —
  with Pydantic schema enforcement on all LLM output and graceful
  fallback to pure TF-IDF if the LLM is unavailable or malformed.
- **Signal engineering.** Designed a signal-weighting scheme where
  individual episode playback is not a taste event; only show-level
  binge thresholds (75% episodes in 60 days, full season completion)
  emit events, keeping TV viewing volume from drowning out film
  preferences in a unified household profile.
- **Storage.** Built a disk-budget-aware "librarian" subsystem with
  tiered storage zones, time-based eviction, bounded-parallelism
  download management, and a sampler-first policy for TV that downloads
  3 episodes before committing to a full season.
- **Roku integration.** Specified a custom SceneGraph app for browsing
  recommendations that deep-links into the official Jellyfin Roku
  client for playback via ECP, avoiding a full client fork.
- **Engineering discipline.** Documented every major decision as an ADR,
  froze interface contracts before implementation, and produced a
  detailed task breakdown designed for execution with Claude Code.

---

## Long (resume project page / detailed portfolio)

**Bear Creek Cinema** — Local-first media recommendation agent
*Personal project / Agentic Agent internal R&D, 2026*

### Problem

The Internet Archive hosts tens of thousands of public-domain films and
classic television episodes with no serious discovery layer. A household
with classic-film taste and a Jellyfin server had no good way to
systematically surface "you finished *The Third Man* last night; *The
Stranger* is on Archive.org and you'd probably like it next."

### Solution

A self-hosted agent that runs on a home server, watches Jellyfin
playback to learn taste, discovers candidates from Archive.org's
movie and TV collections, ranks them with a local LLM, and delivers
recommendations through a custom Roku app that hands off to Jellyfin
for playback.

### Technical highlights

*LLM infrastructure.* Pluggable `LLMProvider` protocol with three
concrete implementations: `OllamaProvider` (default, using `qwen2.5:7b`
for ranking and profile updates, `llama3.2:3b` for lightweight NL
search parsing), `ClaudeProvider` (optional, for higher-quality
ranking), and `TFIDFProvider` (last-resort fallback using
scikit-learn cosine similarity). Per-workflow provider selection in
config. Every LLM call persists to an audit table with latency, tokens,
and outcome for observability.

*Structured output.* Used the `instructor` library to enforce Pydantic
schemas on all LLM responses. Retry-with-fallback logic catches
malformed output before it reaches downstream code. Every prompt
template has an accompanying token-budget test to prevent context
overruns on local models.

*Two-stage ranking.* A scikit-learn TF-IDF vectorizer over genre,
director, decade, and content-type features prefilters the candidate
pool from ~500 to ~50 items. The LLM receives the prefiltered set plus
a 300-word prose taste profile and returns a ranked shortlist of 5-10
with reasoning. This fits comfortably within 7B-model context budgets
while preserving recommendation quality.

*Taste profile design.* Unified profile across movies and TV, with
explicit signal-weighting rules: movie playback events emit taste
events directly; episode playback events go to a raw-watch table
that feeds a nightly aggregator, which only emits show-level binge
events when threshold conditions are met. Prevents TV playback volume
from dominating the profile.

*Storage management.* A `librarian` subsystem owns all filesystem
writes under `/media`, enforces a configured disk budget across tiered
storage zones, and implements a sampler-first TV policy: download 3
episodes to a sampler zone, promote to committed TV storage only if
the user watches 2+ within 14 days. Eviction respects TTLs and never
touches user-curated content.

*Roku integration.* Custom SceneGraph app for browsing and selection;
playback hands off to the official Jellyfin Roku client via External
Control Protocol deep-linking. Kept the custom surface area small
(~600 lines of BrightScript + XML) while benefiting from the Jellyfin
team's work on playback, subtitles, and transcoding.

*Engineering process.* Frozen interface contracts (Pydantic schemas,
HTTP endpoints, CLI signatures) documented in `CONTRACTS.md` before
implementation. Architectural Decision Records (`DECISIONS.md`) for
every non-trivial choice: why Ollama, why unified taste, why no
vector DB, why deep-link instead of fork. Task breakdown with explicit
"done when" criteria designed for execution with Claude Code, with
each card independently verifiable.

### Stack

Python 3.11+, Pydantic, FastAPI, SQLite, Ollama, scikit-learn,
`instructor`, `internetarchive`, TMDb, Jellyfin REST API, BrightScript
+ SceneGraph.

### Outcomes

*(Fill in with measurable outcomes once the system ships — e.g.,
"average time from unmet interest to played film: X hours," or
"reduced manual Archive.org browsing from ~2h/week to zero.")*

### What this demonstrates

The project is a reference implementation of patterns applicable to
privacy-conscious enterprise AI deployments: local-first inference,
cloud augmentation as opt-in, graceful degradation under provider
failure, interface-frozen contracts, and observability built in from
the start.

---

## LinkedIn "Projects" section

**Bear Creek Cinema** | 2026 — Present | [GitHub](https://github.com/<you>/bearcreek-cinema)

Self-hosted recommendation agent for public-domain films and classic
TV. Built on Ollama for local LLM inference, Jellyfin for media
serving, and a custom Roku app for the couch UX. Demonstrates
local-first AI patterns — privacy-preserving, cloud-optional,
degradation-tolerant — with a deliberate choice *not* to use a vector
database (SQLite FTS5 + TF-IDF is the right tool at this corpus size).

Paired with `claude-docs-rag` below as a two-project statement on
knowing when RAG is and isn't the right architecture.

Tech: Python, Pydantic, FastAPI, SQLite, Ollama, scikit-learn,
BrightScript (Roku), Claude API.

Open source (MIT). Under active development.

---

**claude-docs-rag** | 2026 — Present | [GitHub](https://github.com/<you>/claude-docs-rag)

On-prem retrieval-augmented generation over Anthropic's Claude
documentation. Qdrant vector database, hybrid dense + sparse retrieval
with reciprocal rank fusion, cross-encoder reranking, and answer
synthesis with citation validation. Includes an evaluation harness
measuring retrieval hit rate, answer faithfulness, latency, and cost
per query.

Companion to Bear Creek Cinema — the two projects together demonstrate
deliberate architectural judgment about when a vector database earns
its operational weight and when it doesn't.

Tech: Python, Qdrant, sentence-transformers, cross-encoder reranking,
hybrid BM25 + dense retrieval, Claude API, Ollama, FastAPI.

Open source (MIT). Under active development.

---

## Agentic Agent landing page — project cards

Under "Recent Work" or similar:

> **Bear Creek Cinema** — Local-first agentic recommendation system
>
> A self-hosted home cinema built around the principle that personal
> data shouldn't leave the server. Ollama for LLM inference, Jellyfin
> for media delivery, custom Roku app for the UX. Demonstrates the
> same patterns we apply to privacy-conscious enterprise AI work:
> local inference by default, cloud augmentation as opt-in, observable
> systems, and small-system simplicity.
>
> [Case study →](link) [Repo →](link)

> **claude-docs-rag** — On-prem retrieval-augmented generation
>
> A reference implementation of RAG over technical documentation with
> Qdrant, hybrid retrieval, cross-encoder reranking, and a published
> evaluation harness. Paired deliberately with Bear Creek Cinema — one
> project uses a vector database, the other deliberately doesn't, and
> the contrast is the point. Both are the kind of call you're paying us
> to make well.
>
> [Repo →](link)

---

## Notes on positioning

The strongest angle for these projects in a consulting context is not
"I built a cool personal thing" but "I built reference implementations
of the patterns I'll use on your engagement." The local-first,
graceful-fallback, observable-by-default, small-system stance is
exactly what mid-market enterprises want when they get past the
demo-driven phase of AI adoption.

The **paired-project framing** matters. Bear Creek Cinema alone can
read as "you don't know RAG" to a surface-level reviewer.
claude-docs-rag alone reads as "another RAG chatbot." Together they
read as "this person has genuine judgment about when each is
appropriate." That framing is the thing that differentiates you from
the flood of generic AI portfolios.

**When speaking about the paired projects in interviews or pitches:**

Open with the two-project framing: "I have two reference projects that
are designed to be read together. Bear Creek Cinema is a
recommendation agent that deliberately uses SQLite FTS5 and TF-IDF
because its corpus is small and structured — reaching for a vector
database there would be adding operational weight for no quality gain.
claude-docs-rag is a RAG system over technical documentation where
those tradeoffs invert — the corpus is unstructured prose, semantic
paraphrases matter, and Qdrant earns its keep. The point of having
both is to show I make these calls deliberately."

That's the elevator version. It lands the keyword signal (RAG, vector
database, Qdrant) in a context that also demonstrates the judgment
layer that senior roles actually screen for.

Avoid these framings:

- "I built a Netflix clone." Untrue and sells the interesting parts
  short.
- "Using the latest cutting-edge AI." Vague and signals hype-chasing.
- "Solves the recommendation problem." It solves *a* recommendation
  problem in a specific shape.
- "I also know RAG" (as an afterthought). Weak. The paired-project
  framing is stronger.

Prefer these framings:

- "Paired reference implementations — one with a vector DB, one
  without, by design."
- "Reference implementation of [specific pattern] for [specific
  audience]."
- "Demonstrates [specific engineering discipline] applied to
  [concrete problem]."
- "Small system, deliberate scope, production-shaped."

Honesty about status matters. "Designing and building in public" is a
legitimate status; "shipped to production" is a different and stronger
status; conflating them is fatal when a client reads the repo.
