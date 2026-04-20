# phase5-08: Jellyfin ECP deep-link

## Goal

One helper that turns ``jellyfin_item_id`` into a
``launch-the-Jellyfin-Roku-app-at-this-item`` ECP call. Per ADR-006,
we don't play anything ourselves — the Jellyfin Roku app owns
playback, resume, transcoding, subtitles.

## Prerequisites

- phase5-04 (movie detail; calls the helper on Watch)
- phase5-05 (show detail; ditto)
- phase5-06 (episode browser; ditto)
- The official Jellyfin Roku app installed on the same device. We
  verify with a registry/runtime check before attempting the launch.

## Inputs

- Jellyfin Roku ECP contract — confirmed via their PR #423
  (deep-link support). Channel id: ``592369`` (Jellyfin).
- ECP: ``POST http://<roku-ip>:8060/launch/592369?contentId=<itemId>&
  mediaType=<movie|episode>``.

## Deliverables

1. ``components/common/JellyfinDeepLink.brs``:

   ```brightscript
   function deepLinkToJellyfin(itemId as String, mediaType as String) as Boolean
       ' Returns false if the Jellyfin app isn't installed or the
       ' launch fails; caller renders an error.
   end function

   function jellyfinAppInstalled() as Boolean
       ' Queries roAppManager for the 592369 channel id.
   end function
   ```

2. Helper runs the launch on the local Roku via ECP — no network
   call (ECP is loopback on the device).

3. Error paths:
   - Jellyfin app not installed → return false, caller shows:
     "Install the Jellyfin app to play." + optional "Open
     Channel Store" button (``ecp /launch/channelstore/592369``).
   - ECP rejects the request → log + surface a vague "Couldn't
     start playback" error; ADR-006 says playback issues are the
     Jellyfin app's problem, we only need to fail cleanly.

4. Content-type mapping:
   - ``ContentType.MOVIE`` → ``mediaType=movie``.
   - ``ContentType.SHOW`` / ``ContentType.EPISODE`` →
     ``mediaType=episode`` (Jellyfin resolves the item).

## Done when

- [ ] ``deepLinkToJellyfin`` launches the Jellyfin app at the item
- [ ] Missing Jellyfin app surfaces the install prompt
- [ ] Phase5-04 / 05 / 06 all route Watch through this helper

## Verification

Manual:

```bash
# From a laptop on the same LAN as the Roku:
curl -X POST "http://<roku-ip>:8060/launch/592369?contentId=<jf-id>&mediaType=movie"
```

Confirm the Jellyfin app opens on the selected item.

## Out of scope

- Resume points — Jellyfin tracks those per-item on its server
  side; the launch targets the item and Jellyfin resumes where the
  user left off.
- Subtitle / audio track pre-selection — same reason.

## Notes

- ADR-006 is explicit that our app is a recommendation browser, not
  a player. Don't talk yourself into embedding ``Video`` nodes.
- The ECP endpoint lives at ``http://127.0.0.1:8060/`` from the
  Roku itself; no network. Use ``roUrlTransfer`` with a short
  timeout (2s is plenty).
- Consider a secondary ``mediaType=series`` if Jellyfin's Roku
  app supports it — their PR #423 mentions it. For MVP, episode-
  level is sufficient since phase5-05 always resolves to the
  resume-point episode before deep-linking.
