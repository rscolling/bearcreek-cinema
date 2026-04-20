# Task Roadmap

Task cards are the unit of work for Claude Code. Each card is
independently executable with explicit "done when" criteria.

Work phases in order. Within a phase, cards are ordered but some may be
parallelizable (noted in the card).

## Phase 1: Plumbing

- [x] `phase1-01-scaffold.md` — project skeleton + CLI stubs
- [x] `phase1-02-config.md` — typed TOML config + env interpolation
- [x] `phase1-03-state-schema.md` — SQLite schema + migrations
- [x] `phase1-04-jellyfin-client.md` — REST client + history ingestion
- [x] `phase1-05-ollama-smoke.md` — Ollama + LLMProvider skeleton
- [x] `phase1-06-logging-observability.md` — structlog config, redaction,
  llm_calls persistence wiring
- [x] `phase1-07-ollama-stack.md` — Ollama Docker stack on don-quixote
  (prerequisite for phase1-05 — run it first)

**Phase 1 done when:** `archive-agent health all` reports green; watch
history is ingested into state DB; Ollama round-trip works.

## Phase 2: Downloader + Librarian

- [x] `phase2-01-archive-discovery.md` — Archive.org search for both
  collections
- [x] `phase2-02-tmdb-enrichment.md` — TMDb lookups with caching
- [x] `phase2-03-tv-grouping.md` — episode→show association heuristics
- [x] `phase2-04-ia-get-downloader.md` — subprocess wrapper around ia-get
  with fallback to `internetarchive`
- [x] `phase2-05-librarian-core.md` — zone management, budget tracking
- [x] `phase2-06-librarian-placement.md` — `place()` + file move logic
- [x] `phase2-07-librarian-eviction.md` — eviction policies
- [x] `phase2-08-librarian-tv-sampler.md` — sampler-first TV policy
- [x] `phase2-09-jellyfin-placement.md` — file naming + library scan
  triggering

**Phase 2 done when:** `archive-agent download <movie-id>` results in a
playable file in Jellyfin; librarian correctly enforces budget and evicts
ephemeral content; TV sampler flow works for one show.

## Phase 3: Taste Profile + Ranking

- [x] `phase3-01-show-state-aggregator.md` — episode watches → binge events
- [x] `phase3-02-tfidf-prefilter.md` — cosine similarity over candidate
  features
- [x] `phase3-03-ollama-rank.md` — LLM reranker
- [x] `phase3-04-profile-bootstrap.md` — initial profile from history
- [x] `phase3-05-profile-update.md` — incremental profile updates
- [x] `phase3-06-tfidf-provider.md` — TFIDFProvider full implementation
- [x] `phase3-07-claude-rank.md` — ClaudeProvider full implementation
- [x] `phase3-08-recommend-command.md` — `archive-agent recommend` wires
  it all together
- [x] `phase3-09-fts5-indexing.md` — SQLite FTS5 virtual table + triggers
  for catalog title/description search with trigram tokenizer

**Phase 3 done when:** `archive-agent recommend` returns a mixed
movie+TV shortlist with LLM-generated reasoning, and
`archive-agent search fts "..."` returns typo-tolerant matches.

## Phase 4: HTTP API

- [x] `phase4-01-fastapi-scaffold.md` — service skeleton, logging middleware
- [x] `phase4-02-health-endpoint.md` — /health with subsystem status
- [x] `phase4-03-recommendations-endpoints.md` — /recommendations* routes
- [x] `phase4-04-select-flow.md` — POST /select triggers download flow
- [x] `phase4-05-search-endpoint.md` — NL search via small Ollama model
- [x] `phase4-06-poster-proxy.md` — /poster/{id}
- [x] `phase4-07-disk-endpoint.md` — /disk usage view
- [x] `phase4-08-query-router.md` — title/descriptive/play-command
  routing + /search, /search/similar, /search/autocomplete endpoints

**Phase 4 done when:** `curl` the API end-to-end and a film lands in
Jellyfin ready to play.

## Phase 5: Bear Creek Cinema (Roku app)

- [x] `phase5-01-roku-scaffold.md` — SceneGraph project, manifest, build
  script
- [ ] `phase5-02-roku-settings.md` — agent URL config screen
- [ ] `phase5-03-roku-home-grid.md` — poster wall with type badges
- [ ] `phase5-04-roku-detail-movie.md` — movie detail scene
- [ ] `phase5-05-roku-detail-show.md` — show detail + resume logic
- [ ] `phase5-06-roku-episode-browser.md` — season/episode navigation
- [ ] `phase5-07-roku-voice-search.md` — voice-first search scene with
  VoiceTextEditBox, debounced queries, type-aware result cards
- [ ] `phase5-08-roku-deep-link.md` — ECP deep-link to Jellyfin Roku app
- [ ] `phase5-09-roku-sideload.md` — build + deploy script for dev-mode
  Roku

**Phase 5 done when:** Rob sits on couch, opens Bear Creek Cinema,
selects a film, it plays via Jellyfin without returning to a terminal.

## Phase 6: Polish (post-MVP)

- [ ] `phase6-01-claude-provider-wiring.md` — complete ClaudeProvider +
  per-workflow selection in config
- [ ] `phase6-02-per-viewer-tagging.md` — "who watched this?" prompt
- [ ] `phase6-03-web-dashboard.md` — simple status UI on don-quixote
- [ ] `phase6-04-ntfy-notifications.md` — push notifications on new
  recommendations
- [ ] `phase6-05-review-queue.md` — low-confidence TMDb match review UI
- [ ] `phase6-06-systemd-units.md` — production deploy configuration

---

## Working a card

1. Read the card in full. Ask if anything is unclear *before* starting.
2. Skim the referenced sections of `CONTRACTS.md`, `GUARDRAILS.md`, and
   relevant ADRs.
3. Implement. Test as you go.
4. Verify each "done when" bullet is satisfied, with commands and output.
5. Commit: `[phase1-NN] short description`.
6. Check the card's box in this README.

## Adding a new card

If you find a unit of work that isn't captured:

1. Propose it — don't just write it. Open a comment or brief note
   describing: goal, prerequisites, deliverables, done-when criteria.
2. If accepted, add it as `phaseN-NN-short-name.md` using the template
   below.
3. Link it from this README in the right phase.

## Card template

```markdown
# phaseN-NN: Short name

## Goal
(1-3 sentences on what this accomplishes)

## Prerequisites
- phaseX-YY (if any)

## Inputs
- References to CONTRACTS.md sections, fixtures, etc.

## Deliverables
1. Specific files/classes/functions with signatures

## Done when
- [ ] Bulleted criteria, each independently verifiable

## Verification commands
(shell commands and expected output)

## Out of scope
- What this task doesn't cover, for disambiguation

## Notes
- Implementation hints, pitfalls, library quirks
```
