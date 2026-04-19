# The Curator

The running Bear Creek Cinema agent on `don-quixote`. One coordinator,
three jobs:

1. **Keep current** with what's available on the Internet Archive
2. **Recommend** what to watch tonight
3. **Search** on demand when you ask for something specific

This page is the one-paragraph view of how the whole running system
works. For the subsystem-level details, follow the links.

---

## Job 1: keep current

The Curator continuously learns what's available to curate from.

- Every hour, a **discovery worker** queries Archive.org's
  `moviesandfilms` and `television` collections for new and updated
  items. New candidates land in the SQLite `candidates` table.
- TMDb metadata enrichment happens on-demand as candidates are
  considered, caching results so we don't re-query for items we
  already know.
- The **librarian** manages what's downloaded vs. catalog-only,
  enforcing the disk budget across tiered zones (`/media/movies`,
  `/media/tv`, `/media/recommendations`, `/media/tv-sampler`). TV
  follows a sampler-first policy: three episodes before committing to
  a season.
- The **show-state aggregator** runs nightly, turning raw episode
  playback into show-level signal (binge-positive, binge-negative,
  neutral) so the taste profile reflects what the household actually
  watched-through, not what they casually started.

**What you see:** a steady flow of new candidates without surprises.
The disk budget stays within limits. Shows you watch get full-season
downloads after you engage; shows you ignore don't eat storage.

Details: `docs/ARCHITECTURE.md` §Discovery, §Librarian, §Show state.

## Job 2: recommend

Once a night, the Curator produces a short, opinionated list of what
the household should watch.

- Taste is synthesized into a **unified household profile** — a
  short structured JSON plus a ~300-word prose summary that the LLM
  maintains. Movies feed events directly; shows feed events only
  when they cross binge thresholds. Episodes are noise, binges are
  signal.
- Ranking runs in **two stages**: TF-IDF prefilter narrows ~500
  candidates to ~50, then Ollama (`qwen2.5:7b`) reranks the top 50
  against the prose profile and returns 5-10 picks with one-line
  reasoning each.
- Results land in the `Recommendations` Jellyfin library as real
  downloaded files, ready to play. The Roku home grid pulls from this
  set.
- If Ollama is unavailable or returns malformed output, ranking falls
  through to pure TF-IDF similarity. Recommendations never stop
  arriving.

**What you see:** open the Roku app in the evening, find five picks
you didn't have to search for, each with a one-line note explaining
why. Hit one. It plays.

Details: `docs/ARCHITECTURE.md` §Ranking, §Taste profile,
`docs/search-and-retrieval.md` §Recommendation retrieval.

## Job 3: search

When you want something specific, you ask — by voice through the
Roku remote, or by keyboard.

- Your voice hits Roku's `VoiceTextEditBox`, which transcribes to text
  and sends a query to the Curator's HTTP API.
- A **query router** classifies intent: *title lookup* ("the third
  man"), *descriptive* ("something noir and short"), *play command*
  ("play the beverly hillbillies"), or *similar-to* ("more like the
  thin man").
- Title lookups hit SQLite FTS5 with trigram tokenization — "thrid
  man" and "beverly hilbillies" both find their targets despite typos
  or ASR drift.
- Descriptive queries parse to a structured filter via a small Ollama
  model (`llama3.2:3b`), then rank the filtered set against your
  taste profile.
- Results come back tagged with status: *Ready to watch* (in
  Jellyfin now), *Download and watch* (in catalog, will pull), or
  *Get from Internet Archive* (live Archive.org lookup when the
  catalog misses).
- Selecting a result deep-links to the official Jellyfin Roku app
  for playback.

**What you see:** say what you want, get results in under a second,
pick one, watch it. Titles you already own start playing
immediately. Titles not yet downloaded start downloading and play
when ready.

Details: `docs/search-and-retrieval.md` (full design).

## Keeping the user in charge

Three things the Curator will never do:

- **Never surprise-delete content.** The librarian logs intent before
  any destructive action and respects grace periods on committed
  content.
- **Never exceed the disk budget.** Configurable, enforced at
  download time.
- **Never re-recommend rejected items** without a clear intervening
  positive signal.

When the Curator is uncertain — Ollama is down, a show's episodes
can't be grouped, a candidate looks promising but divisive — it
degrades to simpler behavior rather than guessing. Recommendations
always ship. Explanations stay honest. The system fails legibly.

## Runtime shape

The Curator is a single Python process managed by systemd. It exposes
a LAN-bound FastAPI service for the Roku app. Internally it runs an
asyncio scheduler that fires the discovery, aggregator, and
recommendation jobs on their cadences. Everything persists to one
SQLite file.

Three concerns, one coordinator, one process. No orchestration, no
service mesh. Small system by design.

Details: `docs/ARCHITECTURE.md` §Runtime, §System overview.
