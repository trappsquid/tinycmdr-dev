# computer_use: grants, platform pitfalls and image routing

`tools/computer_use.py` sees and drives the local GUI from a text tool call: a capture
returns a numbered accessibility tree or a screenshot file, then click/type/scroll act by
element number or point. The module docstring carries the design (one coordinate space in
points, element handles, the focus refusal); this file is the host-facing half - what the
machine must allow, what a failure means, and where screenshots go.

`action=doctor` is the first call on any host that refuses something. It is read-only, it
takes one throwaway screenshot to answer the Screen Recording question honestly, and its
payload carries machine-readable fields (`accessibility_flag`, `ax_read`, `stale_grant`,
`screen_recording`, `grant_target`) beside the summary text.

## macOS: the two TCC grants

| grant | what needs it | where |
| :--- | :--- | :--- |
| Accessibility | the AX tree (`capture mode=ax`), element clicks, `type`/`key`, `set_value` | System Settings > Privacy & Security > Accessibility |
| Screen Recording | screenshots (`capture mode=vision`, `mode=both`) | same pane, Screen Recording |

Both attach to the process that ASKS - the binary that runs the bot, not this file.
`doctor` prints that path (`grant_target`); grant it that binary, then restart the bot: a
grant does not apply to an already-running process.

## The stale grant

A recorded grant can stop applying without disappearing from System Settings. That is what
an entry becomes when the binary it names is replaced - an update that lands a new venv
python, a moved or re-copied install, a rebuilt interpreter - and it is why `doctor` does
not stop at the checkbox:

- **Accessibility**: `doctor` prints the OS flag AND a real AX read, because they answer
  different questions. The flag answers for the process that asked (the `osascript`
  child); the read is what the tool actually needs. Measured 2026-10-05 on macOS 27:

  | flag | read | meaning |
  | :--- | :--- | :--- |
  | false | false | never granted (this host: read refused with AX error -25204) |
  | true | false | **stale**: the entry is listed and does not apply - remove it and re-add `grant_target`, then restart |
  | false | true | only the `osascript` child is granted; the tool still works |

  When the flag is true and the read fails, the diagnosis is the disagreement itself;
  `stale_grant: true` says it in the payload.
- **Screen Recording**: macOS voids the grant when the binary it named changes, so a
  screenshot that worked before an update and fails after it
  (`could not create image from display`) is the same stale case. Remove the entry in
  Screen Recording and re-add `grant_target`. A stale Screen Recording grant can also
  succeed while returning desktop-only pixels; when a capture looks empty on a host whose
  grant was just reset, that is why.

## Windows

- **Session**: the tool drives the interactive session. Session 0 (a service) has no
  desktop; `doctor` names it, and names a locked or detached foreground window
  (`LockApp`/`LogonUI`) instead of pretending input would land.
- **UIPI**: a non-elevated process cannot send input to an elevated window. When the
  target app runs as administrator, start the bot elevated; if clicks refuse on one app
  while others work, that is the first thing to check.
- **DPI**: the PowerShell engine is per-monitor DPI-aware, so screenshot pixels, UIA
  rectangles and click coordinates are one space (measured 2026-09-28 in a 200% VM).

## Linux

- **X11**: `xdotool` is how this backend clicks and types (`apt-get install xdotool`);
  screen capture, the window list and `doctor` work without it. There is no AT-SPI walk,
  so `capture` reports OS windows (clickable by number) and not buttons or fields, and
  `set_value` is refused - click the field, `key ctrl+a`, then `type`.
- **Wayland**: open work. With `WAYLAND_DISPLAY` set and no `DISPLAY`, the tool refuses
  up front (`doctor` prints the BLOCKED reason) rather than half-working: the X11 tooling
  cannot drive a Wayland session. `XWayland` helps only for windows that are actually
  X11 clients.

## Image routing: one endpoint today, aux vision as the design

Today screenshots travel to the SAME endpoint as the conversation, in the same request as
the text:

- `agent.vision: true` is the operator's assertion that the endpoint accepts images
  (`tinycmdr config set agent.vision true`, then restart). It is off by default.
- With it on, the endpoint gets the last word: `tinycmdr doctor` probes `/props` and only
  a positive `modalities.vision=false` vetoes - an endpoint that says nothing (a cloud
  API, an older llama.cpp) does not override the config.
- With it off, or vetoed, a tool that produced images says so in its result text ("the
  tool produced N image(s); agent.vision is off, so they were NOT attached") instead of
  sending pixels nowhere. Per call, the frames that survive dedup are capped; the dedup
  digest is computed before any of this.

The gap: a text-only main model cannot use screenshots at all, because the images are
attached to a request that model cannot see. The intended shape, not implemented:

1. **Config**: keep `agent.vision: true` as the switch, add
   `agent.vision_endpoint: <base_url>` (and optional `agent.vision_model`). The existing
   `_endpoint_vision(url)` already takes a URL, so the aux endpoint is probed with the
   same veto rules.
2. **Routing**: when a tool returns images and the aux endpoint is configured (or the
   main endpoint is known text-only), send the images to the aux endpoint with a fixed
   describe-the-screen instruction, and append that description to the tool result. The
   main model receives text only; the raw image never rides a text-only request.
3. **Failure semantics**: aux endpoint unreachable or vetoing images -> the existing
   "NOT attached" note names why, and the tool result says the description is missing.
   Never fall back to attaching the image to the main endpoint.
4. **Economy**: one extra request per capture, after dedup and the per-call cap; cache
   descriptions by the frame digest the dedup already computes, so an unchanged screen
   costs nothing.
5. **Open questions**: whether the aux model gets the element list alongside the pixels;
   whether the description should be capped in tokens like other injected text.

## Known gaps

- Wayland driving (Linux) - refused today, above.
- AT-SPI (Linux) - `set_value` needs it; the click + select-all + type path is the
  workaround.
- macOS 27 AX through `osascript`: the read can fail while the grant is present (the
  stale-grant diagnosis above); the vision path does not need AX, so screenshots keep
  working on such a host.
