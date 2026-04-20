# phase5-02: Settings screen

## Goal

Let Rob type the agent's base URL into a Roku keyboard screen. Without
this, first launch can't reach don-quixote and the rest of the app
falls flat. The screen also surfaces ``/health`` output as a
connectivity indicator so "did I fat-finger the URL" is immediately
visible.

## Prerequisites

- phase5-01 (scaffold)

## Inputs

- SceneGraph ``KeyboardDialog`` / ``Keyboard`` reference
- ``GET /health`` (phase4-02) response shape

## Deliverables

1. ``components/SettingsScene.xml``:
   - Text label: "Agent URL" + current value.
   - Pressing OK opens a ``KeyboardDialog`` prefilled with the
     current ``agentUrl`` registry value.
   - After Save: writes the registry, closes the dialog, kicks off a
     health probe in the background.
   - Health probe result renders as colored text (green "connected",
     red "unreachable — <error>").

2. ``components/common/HealthProbe.brs`` — fires a ``GET {base}/health``
   via ``roUrlTransfer`` on a ``Task`` node so the UI doesn't block.
   10-second timeout.

3. First-launch behavior (in ``MainScene``):
   - If ``agentUrl`` is blank, push ``SettingsScene`` onto the stack
     immediately.
   - After a successful save + health probe = ok, replace the
     settings scene with the home grid (phase5-03 placeholder).

4. A back-door menu to reach settings post-first-launch: on the home
   grid, ``*`` (info) key opens ``SettingsScene``.

## Done when

- [ ] Fresh sideload with empty registry pushes SettingsScene
- [ ] Entering a valid URL + Save returns to home
- [ ] Entering an unreachable URL shows a visible error
- [ ] ``*`` key reopens settings from home

## Verification

Manual: sideload, wipe the channel's registry, relaunch. Confirm
each flow.

## Out of scope

- Multiple agent URLs — single-household use (ADR-007); one URL is
  enough.
- Credentials — ADR-011 says no auth on v1.
- URL validation beyond "the /health endpoint responded".

## Notes

- Roku keyboards are slow to type on. Keep the default URL sensible
  (``http://don-quixote.local:8787``) so Rob can accept it with a
  single OK press on first boot.
- Use a ``Task`` node for the HTTP call, not an inline
  ``roUrlTransfer`` on the render thread — that freezes the scene.
- Log every health probe's timing + status to the debug console so
  sideload-to-working-UI is easy to diagnose when something's off.
