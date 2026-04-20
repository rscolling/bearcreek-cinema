# phase5-05: Show detail scene + 3-thumb rating

## Goal

TV show version of the movie detail. Adds: the 3-thumb rating
control (ADR-013), a "Next up" episode readout, and a "Watch all"
commit button. Single Watch button plays the resume-point episode.

## Prerequisites

- phase5-04 (movie detail; shared layout)
- phase4-03 (reject / defer)
- phase4-04 (/select, /shows/{id}/commit)
- phase4-08 (deep-link)
- ADR-013 (explicit ratings)
- future follow-up: a POST /shows/{id}/rate endpoint (see notes)

## Inputs

- ``RecommendationItem`` for shows includes ``episodes_available``
  and (for episodes) ``next_episode``.

## Deliverables

1. ``components/ShowDetailScene.xml``:
   - Shared layout with MovieDetailScene — same title / reasoning /
     poster block.
   - Extra TV-specific row: "Next: S{season}E{episode} — {title}"
     (resolved from ``/select`` response or cached during push).
   - Button row: ``Watch`` / ``Browse episodes`` / ``Watch all`` /
     ``Not interested`` / ``Later``.
   - **3-thumb rating** bar below buttons: ``👎  👍  👍👍``, current
     selection highlighted. Presses fire
     ``POST /shows/{show_id}/rate`` (see notes — endpoint added as
     part of this card if missing).

2. Watch button:
   - Same as movie detail's, but resolves to the current resume
     episode (via ``/select`` response's ``next_episode``).

3. Browse episodes button:
   - Pushes the episode browser (phase5-06).

4. Watch all button:
   - ``POST /shows/{show_id}/commit`` — 202 response shows a modal:
     "Queued {n} episodes (~{gb} GB). They'll appear as they
     download." Dismiss → pop.

5. Rating row behavior:
   - Presses fire one POST each; local state flips immediately so
     the UI feels snappy. On HTTP failure, revert + show a toast.
   - Pressing the already-selected thumb clears the rating (sends
     whichever variant the API accepts as "unrated" — TBD in the
     follow-up endpoint spec).

## Done when

- [ ] Detail renders show fields including episodes_available
- [ ] Watch deep-links into the right episode
- [ ] Browse pushes the episode browser
- [ ] Watch all fires /commit and confirms the count
- [ ] 3-thumb ratings fire and persist across scene re-entry

## Verification

Manual: focus on a show tile, confirm each flow against a running
API.

## Out of scope

- Per-episode rating — deferred forever; ADR-013 is show-level only.
- A "skip this season" shortcut — not in scope.

## Notes

- The rating endpoint (``POST /shows/{show_id}/rate``) doesn't exist
  yet. Add it as part of this card: body ``{kind:
  "rated_down"|"rated_up"|"rated_love"|null}``, inserts the
  TasteEvent per ADR-013; ``null`` body clears by inserting a
  no-op or by writing a synthetic ``DEFERRED`` event. Keep the
  choice documented.
- Focus should land on Watch; the thumbs row is a side-channel, not
  the primary action.
- Don't let the rating row lose sync with the server — on scene
  entry, GET the current rating (or inline it in the /select
  response).
