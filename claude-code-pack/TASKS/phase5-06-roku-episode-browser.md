# phase5-06: Episode browser

## Goal

Let the user pick a specific season + episode when they don't want
the default "next up" target. Single list per show, grouped by
season. Each row deep-links into the Jellyfin app.

## Prerequisites

- phase5-05 (show detail — pushes here)
- phase4-05 or phase4-08 (``/search`` + similar — not required, but
  we do need something that returns "all episodes for show X")

## Inputs

- A new endpoint: ``GET /shows/{show_id}/episodes`` returning
  ``list[RecommendationItem]`` restricted to episodes for that show
  (or extend ``/recommendations`` with a ``show_id`` filter).

## Deliverables

1. New API endpoint ``GET /shows/{show_id}/episodes``:
   - Lists every candidate ``content_type=EPISODE`` with matching
     ``show_id``.
   - Ordered by ``(season, episode)`` with ``NULLS LAST``.
   - ``SearchResultItem`` or a minimal ``EpisodeBrowserItem`` wire
     shape — whichever keeps the serializer work small.

2. ``components/EpisodeBrowserScene.xml``:
   - ``RowList`` grouped by season. Row header = "Season N".
   - Each episode cell: thumbnail (from the episode's ``poster_url``
     if present, else the show's), "SnnEmm — {title}", availability
     badge (``Ready`` / ``Queued`` / ``Available``).

3. Selection:
   - OK on an episode = same Watch flow as movie detail (phase5-04)
     but with the episode's ``jellyfin_item_id``.
   - If the episode isn't downloaded yet → ``/select`` triggers the
     pipeline, UI transitions to "Downloading...".

4. Back = pop to show detail.

## Done when

- [ ] New ``/shows/{id}/episodes`` endpoint returns episodes ordered
  by (season, episode)
- [ ] Roku browser renders one row per season, each a horizontal
  strip of episode cells
- [ ] OK on a ready episode deep-links; on a queued one shows the
  spinner
- [ ] Back pops cleanly to the show detail

## Verification

Manual: open a show → Browse episodes → try both a downloaded and
an undownloaded episode.

## Out of scope

- Re-ordering seasons (e.g., "watch in production order") — out of
  scope.
- Subtitle / audio track selection — Jellyfin app owns that.

## Notes

- Episode thumbnails are nice-to-have. If the poster cache (/poster)
  returns 404 for episodes lacking a ``poster_url``, the Roku
  ``Poster`` will just show a blank — acceptable for MVP.
- Memory: RowList with 1000+ episodes stays snappy on Roku. If a
  show has more than a handful of seasons, test scroll performance
  explicitly.
- The new endpoint is a few lines — consider folding it into
  phase5-05's PR if the line count stays small.
