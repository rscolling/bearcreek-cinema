# phase5-01: Roku SceneGraph scaffold

## Goal

Stand up the ``roku/bear-creek-cinema/`` BrightScript + SceneGraph
project so every follow-up card can add a screen. Boot sequence: splash
→ home grid (empty placeholder until phase5-03). Ships a ``manifest``,
a root ``MainScene``, and a minimal ``source/Main.brs`` that reads
config and builds the screen.

## Prerequisites

- Phase 4 API complete (the app talks to it, but this card only
  renders a placeholder; HTTP arrives in phase5-03).

## Inputs

- Roku SceneGraph docs
- ADR-006 (deep-link to official Jellyfin Roku app for playback)
- ADR-011 (LAN-only, no auth) — the Roku just POSTs to the configured
  base URL

## Deliverables

1. ``roku/bear-creek-cinema/`` directory with:
   - ``manifest`` — app title "Bear Creek Cinema", major/minor/build,
     splash image references, ``rsg_version=1.2`` or newer.
   - ``source/Main.brs`` — entry point. Reads a persisted agent base
     URL from registry (``section="BearCreekCinema"``, ``key="agentUrl"``),
     builds the screen, routes remote events.
   - ``components/MainScene.xml`` — root scene. Stacks a ``Poster``
     (splash), a ``Label`` (status), and a ``Group`` placeholder for
     phase5-03's grid.
   - ``components/common/`` — shared BrightScript utils (logging,
     JSON helpers, registry I/O).
   - ``images/`` — placeholder splash + icons (HD + FHD variants).

2. ``roku/bear-creek-cinema/README.md`` — "how to sideload + how the
   app finds the agent" — short.

3. Registry keys (persisted across launches):
   - ``agentUrl`` — base URL, default empty ("" triggers the settings
     screen in phase5-02).

4. Remote event handling in ``MainScene``:
   - ``OK`` / ``back`` / ``up/down/left/right`` stubbed with logging —
     real navigation lands as scenes arrive.

## Done when

- [ ] Sideloaded app shows the splash → empty home screen
- [ ] App reads a pre-populated ``agentUrl`` from registry on launch
- [ ] Structure matches Roku SceneGraph conventions (``manifest``,
  ``components/``, ``source/``, ``images/``)

## Verification

```bash
cd roku/bear-creek-cinema
zip -r /tmp/bear.zip . -x "*.DS_Store"
# Sideload via Roku dev mode in a browser at http://<roku-ip>/
# Confirm splash + empty home appear.
```

## Out of scope

- Anything that talks to the HTTP API (phase5-03 onward)
- Settings screen (phase5-02)
- Voice search (phase5-07)
- Build / deploy script (phase5-09)

## Notes

- BrightScript + SceneGraph feels alien next to modern web UI; keep
  the scaffold minimal and let later cards add shape.
- Use ``HD`` (1280x720) as the design resolution. FHD is 1.5× of HD
  so assets scale cleanly.
- Registry entries are per-channel and persist across reboots. Avoid
  storing anything sensitive — the Roku is on the LAN anyway.
- No unit tests here; SceneGraph has no first-class test framework.
  "Done" = visual confirmation on a dev-mode Roku (see phase5-09).
