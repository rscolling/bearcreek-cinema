# phase5-07: Voice search screen (Bear Creek Cinema Roku app)

## Goal

Build the search scene in the Roku app with voice + keyboard input,
debounced queries to the agent's search API, and live result rendering
with type-aware "Ready to watch" / "Download and watch" cards.

## Prerequisites

- phase5-01 (Roku app scaffold)
- phase5-03 (home grid for shared patterns)
- phase4-08 (query router endpoint on the agent)

## Inputs

- `docs/search-and-retrieval.md` §"Voice search integration (Roku)"
- `SearchResultItem` contract from CONTRACTS.md
- Roku `VoiceTextEditBox` SceneGraph node documentation:
  https://developer.roku.com/docs/references/scenegraph/dynamic-voice-keyboard-nodes/voice-text-edit-box.md

## Deliverables

1. `components/scenes/SearchScene.xml`:
   - `VoiceTextEditBox` for input (supports both voice and keyboard)
   - Results container (RowList or PosterGrid — pick one, be consistent
     with home grid)
   - Empty-state panel when no query
   - Loading spinner during request in flight

2. `components/scenes/SearchScene.brs`:
   - Observer on `VoiceTextEditBox.text` changes
   - 300ms debounce before issuing API request
   - Cancel in-flight request if a new query arrives
   - Keyboard key handler: Back exits search, OK on a result row selects it

3. `components/search/SearchResultRow.xml` + `.brs`:
   - Poster + title + year + status badge
   - Status badges:
     - `ready` → small green checkmark icon + "Ready to watch"
     - `downloadable` → small download icon + "Download and watch"
     - `discoverable` → small search icon + "Get from Internet Archive"
   - TV-specific: if `next_episode` present, show
     "S01E05 — The Something Episode"

4. `components/search/SearchApi.brs`:
   - `SearchQuery(query, onResult, onError)` — async HTTP
   - Handles 3 endpoints: `/search`, `/search/similar`, `/search/autocomplete`
   - Exponential backoff on transient errors, max 2 retries

5. Voice-first UX polish:
   - On scene entry, focus is already on the VoiceTextEditBox
   - Hint text in the input: "Press 🎤 or type to search"
   - Placeholder results panel shows current recommendations as
     "While you're here..." when no query

6. Selection flow from search result:
   - If `status=ready`: POST `/recommendations/{archive_id}/select`,
     then ECP deep-link to Jellyfin
   - If `status=downloadable`: POST `/recommendations/{archive_id}/select`
     (with `play=false`), show "Downloading..." state, poll
     `/recommendations/{id}` until status changes, then deep-link
   - If `status=discoverable`: POST select (triggers add-to-catalog +
     download), show "Finding..." state, poll, then deep-link

7. Error/empty states:
   - No results → "Nothing matched. Try different words."
   - Network failure → "Can't reach the agent. Check your connection."
     with retry button
   - Download timeout → "Download is taking longer than expected. It'll
     be ready later in your Recommendations."

## Done when

- [ ] 🎤 button on the remote triggers voice input in this scene
- [ ] Keyboard typing triggers the same search flow
- [ ] Typing "third man" (via voice or keyboard) returns *The Third Man*
  within 1 second on a warm system
- [ ] Descriptive queries return a ranked list
- [ ] Selecting a `ready` result plays the film in Jellyfin
- [ ] Selecting a `downloadable` result initiates download and then plays
  when ready (or shows clean deferred state)
- [ ] Debouncing works (rapid typing doesn't send 10 requests)
- [ ] Back button returns to home grid

## Verification

Manual (on a Roku in dev mode):
1. Sideload app
2. Open Bear Creek Cinema, navigate to Search
3. Press 🎤, say "the third man" — result appears, select, it plays
4. Back to search, type via keyboard "thrid man" (typo) — still finds it
5. Voice: "something noir and short" — descriptive list appears
6. Select a downloadable result — observes download state, plays when ready

Log capture:
```
# On don-quixote
journalctl --user -u archive-agent-api -f
# Confirm POST /search, then POST /select for the right archive_id
```

## Notes

- `VoiceTextEditBox` delivers Roku's built-in ASR; no custom speech
  handling needed. The transcribed text appears in the `text` field.
- Keep the UI simple. Voice interaction is already a lot of visual
  movement; don't compound it with busy layouts.
- Polling for download completion: use a 2-second interval with a
  60-second ceiling. If not done by then, show deferred state and stop
  polling; the download continues in the background.
- Don't rely on `HideWhenEmpty` for the results container — hide/show
  containers explicitly in the observer based on state (`empty`,
  `loading`, `results`, `error`). SceneGraph state management is fragile;
  explicit is safer.
- Test voice input quality with common failure modes: background noise,
  multiple speakers, mumbled input. The agent's FTS handles ASR drift;
  no special handling needed in the app.
