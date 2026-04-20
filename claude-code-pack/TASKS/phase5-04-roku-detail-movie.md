# phase5-04: Movie detail scene

## Goal

When the user selects a movie from the home grid, show a detail page
with the poster, title, year, runtime, genres, description, and the
ranker's reasoning. Primary action: Watch. Secondary: Not interested
(reject) / Later (defer).

## Prerequisites

- phase5-03 (home grid; provides the push)
- phase4-03 (/recommendations/{id}/reject + /defer)
- phase4-04 (/recommendations/{id}/select)
- phase5-08 (deep-link — called on Watch success)

## Inputs

- ``RecommendationItem`` from the grid — already has everything we
  need; no extra GET.

## Deliverables

1. ``components/MovieDetailScene.xml``:
   - Left column: poster (fixed size, 400×560).
   - Right column: title (h1), year + runtime + genres (one line),
     description (wrapped paragraph), LLM reasoning (italic,
     smaller, prefaced "Why this?").
   - Button row: ``Watch`` / ``Not interested`` / ``Later``.

2. Watch button:
   - Fires a ``POST /recommendations/{archive_id}/select`` via a
     ``Task`` node.
   - On ``status=ready``: hands the ``jellyfin_item_id`` to
     phase5-08's deep-link helper.
   - On ``status=queued``: renders "Downloading — try again in a few
     minutes" and disables the button.
   - On ``status=failed`` or HTTP 5xx: renders the error and keeps
     the button enabled.

3. Reject + defer buttons:
   - Fire ``POST .../reject`` or ``POST .../defer`` (fire-and-forget).
   - After success, pop back to the home grid so the user doesn't
     stare at a rejected item.

4. Focus + remote behavior:
   - Default focus = Watch button.
   - Back = pop to grid.

## Done when

- [ ] Detail scene renders all the item fields correctly
- [ ] Watch happy path deep-links into Jellyfin (via phase5-08)
- [ ] Watch failure path renders the error, doesn't crash
- [ ] Reject / defer pop back to home after success

## Verification

Manual: browse grid → OK on a movie → inspect layout → try each
button against a running API.

## Out of scope

- Any alternative playback path — ADR-006 says Jellyfin Roku app
  owns playback.
- Resume logic — movies are single-file; resume position lives in
  Jellyfin's own app.

## Notes

- The LLM reasoning is the Roku's only text-heavy surface today.
  Keep the font big enough that 10-ft viewing works (24+ pt).
- The detail scene is allocated memory until back is pressed; avoid
  re-fetching anything on focus changes.
- If ``jellyfin_item_id`` comes back null (queued or scan-timeout
  case), surface that cleanly — "Downloaded, but Jellyfin hasn't
  indexed it yet. Try again soon."
