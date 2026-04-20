# phase5-09: Build + sideload script

## Goal

One script that packages ``roku/bear-creek-cinema/`` into a
sideload-ready zip and pushes it to a dev-mode Roku. Without this,
the iteration loop is "zip + open browser + upload by hand" on every
change; painful enough that it quietly kills productivity.

## Prerequisites

- phase5-01 (scaffold exists)
- A Roku running dev-mode firmware + credentials for its dev UI.

## Inputs

- Roku Developer HTTP API — ``POST {roku-ip}/plugin_install`` with
  basic-auth using the rootpw configured on the device.

## Deliverables

1. ``scripts/roku-sideload.sh`` (bash, not Python — tiny footprint):

   ```bash
   # Usage: scripts/roku-sideload.sh [<roku-ip>]
   # Reads ROKU_IP / ROKU_USER / ROKU_PASSWORD env vars (or an
   # optional override arg for the IP). Packages the Roku app,
   # POSTs to /plugin_install, tails the telnet debug console
   # (8085) until the user ctrl-c's.
   ```

   Behavior:
   - ``zip -rq /tmp/bear-creek-cinema.zip roku/bear-creek-cinema/``
     (exclude dotfiles + ``.DS_Store``).
   - ``curl --user "$ROKU_USER:$ROKU_PASSWORD" \
     -F "mysubmit=Install" -F "archive=@/tmp/bear-creek-cinema.zip"
     http://$ROKU_IP/plugin_install``.
   - If the response contains ``Application Received`` → success.
   - Tail the telnet debug log:
     ``nc $ROKU_IP 8085`` (or ``telnet``), ctrl-c to exit.

2. ``scripts/roku-deploy-windows.ps1`` — same behavior, PowerShell
   edition for blueridge. Uses ``Invoke-WebRequest`` + ``tnc`` for
   the telnet tail (or a simple ``[System.Net.Sockets.TcpClient]``).

3. Env vars documented in ``roku/bear-creek-cinema/README.md``:
   - ``ROKU_IP`` — the Roku's LAN IP.
   - ``ROKU_USER`` — default ``rokudev``.
   - ``ROKU_PASSWORD`` — the password set when dev mode was enabled.

4. ``Makefile`` targets at repo root:
   - ``make roku-sideload`` — runs the shell script.
   - ``make roku-tail`` — connects to the Roku telnet log only.

## Done when

- [ ] ``make roku-sideload`` builds + uploads + tails in one command
- [ ] First run on a new Roku succeeds (env vars in ``.env`` or
  shell)
- [ ] Failure paths print a useful error (wrong password, Roku not
  reachable, zip too large)
- [ ] Windows variant works on blueridge

## Verification

```bash
export ROKU_IP=192.168.1.50 ROKU_USER=rokudev ROKU_PASSWORD=sekret
make roku-sideload
# → "Application Received" + telnet tail
```

## Out of scope

- Roku "Deep Link Tester" automation — manual ECP curl is fine for
  phase5-08 verification.
- Automated screenshotting — nice-to-have for later.
- Signing / packaging for production channel submission — the app
  is LAN-only; no channel store plans.

## Notes

- The Roku dev UI requires basic auth even on LAN. Don't hard-code
  passwords — the ``.env`` pattern is already established for the
  Python side; reuse it.
- ``zip`` on Windows isn't in ``PATH`` by default; the PowerShell
  variant should use ``Compress-Archive``.
- Keep the zip small — don't bundle dotfiles, generated caches, or
  unused test fixtures.
- Telnet on 8085 is the "Debug Console 1" — there's also a
  per-channel log on 8080/8092 depending on firmware. 8085 is the
  common one and usually has the most useful output.
