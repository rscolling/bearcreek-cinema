# phase5-03: Home grid

## Goal

Render the latest recommendation batch as a poster wall. Tile size,
layout, and badge ("movie" vs "show") follow the mixed-type contract
from ADR-003 — the household's taste spans film and TV, so does the
grid.

## Prerequisites

- phase5-02 (settings; the grid needs a valid agent URL)
- phase4-03 (``GET /recommendations``)
- phase4-06 (``GET /poster/{id}``)

## Inputs

- ``CONTRACTS.md`` §3 ``RecommendationItem``
- ``GET /recommendations?type=any&limit=20``
- ``GET /poster/{archive_id}`` — always; never call archive.org directly

## Deliverables

1. ``components/HomeScene.xml``:
   - ``PosterGrid`` component from SceneGraph — 4 columns, square-ish
     cells sized for HD (200×280).
   - Each cell: poster + title + year + a small type badge in the
     bottom-right corner (``MOVIE`` / ``SHOW``).

2. Data pipe:
   - ``components/tasks/FetchRecommendations.brs`` — Task node that
     GETs ``/recommendations?type=any&limit=20`` and emits a content
     node array the grid binds to.
   - Runs on launch + on a 60s idle timer (cheap — the daemon writes
     new batches, the endpoint just reads ``latest_batch``).

3. Poster loading:
   - ``Poster.uri = "{agentUrl}/poster/{archive_id}"``
   - Roku handles caching + resize internally; no extra work.

4. Selection:
   - Pressing OK on a cell pushes either the movie detail scene
     (phase5-04) or the show detail scene (phase5-05) based on
     ``content_type``.

5. Empty state:
   - No batch yet → centered label "First batch brewing...".
   - Pressing OK in the empty state re-triggers the fetch.

## Done when

- [ ] Grid shows N items with posters loaded from ``/poster/{id}``
- [ ] Type badge is visible and correct per item
- [ ] OK on a cell pushes the right detail scene based on type
- [ ] Empty state renders cleanly (no frozen grid, no crash)
- [ ] ``*`` still reaches settings

## Verification

Manual: sideload, confirm posters appear and navigation works.

## Out of scope

- "For tonight" shelf — a follow-up could surface
  ``/recommendations/for-tonight`` as a second row; not needed for
  MVP.
- Long-press gestures — ``*`` is enough for MVP.

## Notes

- ``PosterGrid`` is finicky about focus behavior. Consider
  ``RowList`` if the grid feels wrong after a day of use. Either
  way, one focusable row at a time.
- Cell title can wrap onto 2 lines; year goes on a separate label
  below. Don't try to fit everything into the poster overlay — it
  looks cramped on 720p.
- Keep the idle refresh to 60s. Any shorter and you churn
  ``/recommendations`` calls for no user benefit.
