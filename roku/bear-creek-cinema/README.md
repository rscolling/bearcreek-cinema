# Bear Creek Cinema — Roku app

Recommendation browser for the archive-agent HTTP service. All
playback goes to the official Jellyfin Roku app via ECP deep-link
(ADR-006); this app is just the lean UI on top.

## Status

Phase 5-01 scaffold only — boots, reads an agent URL from
registry, parks on an empty home screen with a status line. Real
screens land in subsequent cards:

- 5-02 settings (URL entry + /health probe)
- 5-03 home poster grid
- 5-04 movie detail
- 5-05 show detail + 3-thumb rating
- 5-06 episode browser
- 5-07 voice search
- 5-08 deep-link to Jellyfin
- 5-09 sideload script (arrives alongside 5-01 in the same PR
  ideally, but a manual-sideload fallback is documented below)

## Layout

```
bear-creek-cinema/
├── manifest                     # channel metadata + splash/icon refs
├── source/
│   └── Main.brs                 # entry point (reads registry, builds scene)
├── components/
│   ├── MainScene.xml            # root scene — placeholder UI
│   ├── MainScene.brs            # scene behavior + key handling
│   └── common/
│       ├── Logger.brs           # [bcc] LEVEL event kv=... formatter
│       ├── Registry.brs         # per-channel persistent storage
│       └── Json.brs             # parse/stringify with safe logging
├── images/                      # (empty until assets land — see below)
└── README.md                    # this file
```

## How the app finds the agent

On launch, ``Main.brs`` reads the ``agentUrl`` key from the
``BearCreekCinema`` registry section. If empty, the UI surfaces a
"press * to open settings" prompt; phase 5-02 will intercept that
and push the settings keyboard flow.

To pre-populate it by hand before 5-02 lands, use the Roku's dev
console or sideload with ``ECP_REGISTRY_AGENTURL`` set — or just
edit ``source/Main.brs:readAgentUrl`` temporarily.

## Sideload (manual, pre-5-09)

Roku developer mode must already be enabled on the target device.
On blueridge or any LAN host:

```powershell
# Windows
Compress-Archive -Path roku/bear-creek-cinema/* -DestinationPath $env:TEMP/bcc.zip -Force
# Upload the zip via the Roku dev UI at http://<roku-ip>/plugin_install
```

```bash
# Linux / macOS
cd roku/bear-creek-cinema && zip -r /tmp/bcc.zip . -x "*.DS_Store"
curl --user "$ROKU_USER:$ROKU_PASSWORD" \
    -F "mysubmit=Install" \
    -F "archive=@/tmp/bcc.zip" \
    http://$ROKU_IP/plugin_install
```

Phase 5-09 adds ``scripts/roku-sideload.sh`` + a PowerShell sibling
so this becomes a single ``make roku-sideload`` invocation.

## Placeholder images

``images/`` is intentionally empty in this commit. The manifest
references ``icon_focus_hd.png`` / ``icon_side_hd.png`` /
``splash_hd.png``; the Roku renders blank slots when they're
missing, which is fine for sideload but is a channel-store
submission blocker. Drop real PNGs in before submitting (we
probably never will — LAN-only per ADR-011).

Rough sizes:

| File                 | Dimensions | Notes                    |
|----------------------|------------|--------------------------|
| icon_focus_hd.png    | 336 × 210  | shown when selected      |
| icon_side_hd.png     | 108 × 69   | small sidebar icon       |
| splash_hd.png        | 1280 × 720 | boot splash              |

## Debug console

Sideload the app, then from a LAN host:

```bash
# Linux / macOS
telnet $ROKU_IP 8085      # or: nc $ROKU_IP 8085
```

```powershell
# Windows
Test-NetConnection $env:ROKU_IP -Port 8085 -InformationLevel Detailed
# Or a proper telnet client — built-in tnc only probes the port.
```

All our prints land here with a ``[bcc]`` prefix.
