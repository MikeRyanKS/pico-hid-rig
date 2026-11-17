# Pico HID Rig

A Raspberry Pi **Pico 2 W** (RP2350, WiFi) that acts as a real USB **keyboard + absolute mouse**
on a target Mac, controlled over WiFi from a second machine — and able to run **randomized,
human‑like click/type patterns autonomously**, with nothing installed on the target.

```
Controller (Mac mini) ──WiFi──► Pico 2 W ──USB──► Target Mac (sees a plain keyboard + mouse)
```

The Pico appears to the target as an ordinary USB input device. A controller talks to it over WiFi
(JSON over TCP) for setup, calibration and live control. Once a profile is saved, the Pico runs it
**on its own** the moment it is plugged in — no controller required.

> **Responsible use.** This is an input‑automation / HID tool intended for authorized work only:
> QA and user‑testing of systems you own or are permitted to test, accessibility automation,
> research, and demos. Do not use it against devices or accounts without the owner's permission.

---

## Features

- **Real USB HID** — keyboard, relative mouse, and an **absolute pointer** (report ID 4, range
  0–32767) so clicks land on exact screen positions.
- **WiFi control** — JSON‑lines TCP server on port 8765, shared‑token auth, mDNS name
  `pico-hid.local`.
- **On‑device autonomy** — loads a profile from its own flash and runs it on boot. No Mac, no
  WiFi needed to run a saved pattern; WiFi is only for configuring or switching patterns.
- **Zones** — every generated move/click is hard‑clamped to a rectangle (one display). The device
  can never stray onto another screen.
- **Human‑like motion** — eased, slightly curved, jittered multi‑step moves with randomized
  targets and timing; no two paths alike.
- **Per‑session keyboard toggle** — disable typing for a mouse‑only session (the keyboard device
  stays present, just silent).
- **Stealth** — hide the `CIRCUITPY` drive and serial port from the target so it looks like a plain
  mouse; the Pico can still save profiles to its own flash and is still reachable over WiFi.
- **USB disguise** — rename the drive and the USB manufacturer/product strings (e.g.
  "Logitech / USB Optical Mouse").
- **Local web UI** — calibrate, pick a display/zone, edit click/type/timing patterns, and
  push/start/stop — all from a browser on the controller.

---

## Hardware

- **Raspberry Pi Pico 2 W** (RP2350, WiFi; "WH" = pre‑soldered headers).
- A **USB data cable** (micro‑USB on the Pico side) — must carry data, not charge‑only.
- A controller machine on the **same 2.4 GHz network** as the Pico (the examples use a Mac mini;
  macOS point calibration is Mac‑specific, but the firmware/protocol are OS‑agnostic).
- Optional: a USB hub/adapter to reach the target's port.

> **WiFi is 2.4 GHz only.** The Pico 2 W cannot join 5 GHz. The controller and the Pico must be able
> to reach each other on the LAN — beware **AP/client isolation** (common on ISP routers and guest
> networks) and phone‑hotspot power‑saving. See [Networking notes](#networking-notes).

---

## Repository layout

| Path | Runs on | Purpose |
|---|---|---|
| `pico/boot.py` | Pico (`CIRCUITPY`) | USB HID device setup; stealth; USB disguise; filesystem remount |
| `pico/code.py` | Pico | Autonomous engine + WiFi control server |
| `pico/profile.example.json` | reference | The automation‑profile schema |
| `pico/settings.toml.example` | → copy to `settings.toml` | WiFi, token, stealth and disguise settings |
| `pico/lib/adafruit_hid/` | Pico | Adafruit HID library (MIT; third‑party) |
| `macmini/pico_client.py` | controller | Stdlib Python client + CLI |
| `macmini/ui_server.py` + `macmini/ui/index.html` | controller | Local web UI |
| `macmini/calibrate.py` | controller | Map the absolute range to on‑screen points |
| `macmini/pico_config.example.json` | → copy to `pico_config.json` | Host, token, screen calibration |
| `macmini/example_test.py` | controller | Sample scripted test |

**Secrets are not committed.** Copy the `.example` files and fill in your own values:

```bash
cp pico/settings.toml.example      pico/settings.toml
cp macmini/pico_config.example.json macmini/pico_config.json
```

`settings.toml` and `pico_config.json` are git‑ignored.

---

## Setup

### 1. Generate a token (on the controller)
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```
Put the same value in `pico/settings.toml` (`PICO_TOKEN`) and `macmini/pico_config.json` (`token`).

### 2. Flash CircuitPython on the Pico (plug it into the controller)
1. Download the latest stable `.uf2` for **Raspberry Pi Pico 2 W**:
   <https://circuitpython.org/board/raspberry_pi_pico2_w/>
2. Hold **BOOTSEL**, plug in — a drive `RP2350` appears. Drag the `.uf2` on; it reboots as `CIRCUITPY`.

### 3. Copy the files to `CIRCUITPY`
1. Copy `pico/lib/adafruit_hid/` into `CIRCUITPY/lib/` (or get it from the matching
   [Adafruit CircuitPython Library Bundle](https://circuitpython.org/libraries)).
2. Copy `boot.py`, `code.py`, and your filled‑in `settings.toml` to the root of `CIRCUITPY`.
3. **Unplug and replug** the Pico — `boot.py` only runs at power‑up.
4. Watch the serial log at `/dev/tty.usbmodem*` to confirm it joins WiFi and prints
   `Listening on port 8765`.

### 4. Verify from the controller
```bash
python3 macmini/pico_client.py ping
python3 macmini/pico_client.py blink
```

---

## Calibration

The absolute pointer maps to one display (on macOS, the **main** display). Calibration records the
macOS‑point rectangle that the 0–32767 range covers, so `click(x, y)` uses real points.

- **CLI:** `python3 macmini/calibrate.py` — drives the cursor to the corners; you read the
  coordinate (⌘⇧4 on the target) and it saves `screen` to `pico_config.json`.
- **Web UI:** the **Calibration** tab does the same with drive‑to‑corner helpers.

To drive a *different* display, make it the main display (System Settings → Displays) and recalibrate.

---

## The web UI

```bash
python3 macmini/ui_server.py          # opens http://localhost:8800
```
A stdlib server (no installs) that serves the UI and relays to the Pico; the token stays on the
server and is never sent to the browser. Tabs:

- **Calibration** — map the absolute range to screen points.
- **Zones** — define per‑display rectangles (in points); the active zone clamps all automation.
- **Pattern** — click counts, targets, human‑like motion (steps/duration/jitter/curve), timing.
- **Typing** — phrases, type chance and speed (optional).
- **Session** — autorun on boot, loop, max actions, start delay, action gap.
- **Profile** — live JSON preview, **Push to device**, **Push & Start**, local save/load.

---

## Profiles

A profile is JSON (`/profile.json` on the device). See `pico/profile.example.json`. Coordinates in a
profile are **raw HID units (0–32767)**; the UI converts a display's point‑rectangle into these using
the calibration. Key fields:

- `autorun` — start automatically on boot.
- `keyboard_enabled` — allow typing this session.
- `zone` — `{x0,y0,x1,y1}` HID bounds; nothing moves/clicks outside it.
- `mouse` — `clicks_per_session`, optional `targets`, `move` (steps/duration/jitter/curve),
  `dwell_ms`, `hold_ms`, `double_click_chance`.
- `keyboard` — `phrases`, `chance`, `type_delay_ms`.
- `session` — `loop`, `max_actions`, `start_delay_ms`, `action_gap_ms`.

**The Pico can't see the screen** — automation is open‑loop (coordinates + timing), not reactive to
what's on screen. Zones keep it confined to a safe area.

---

## WiFi protocol

One JSON object per line over TCP (default 8765). Every request includes `"token"`. Replies are one
JSON line. The CLI (`python3 macmini/pico_client.py <cmd>`) wraps all of these:

| Command | Purpose |
|---|---|
| `ping` | reachability |
| `move` / `raw` / `rel` | move absolute (points) / absolute (raw) / relative |
| `click` / `down` / `up` / `scroll` | mouse buttons and wheel |
| `type` / `key` / `key_down` / `key_up` / `release_all` | keyboard |
| `get_status` | running?, actions, keyboard on?, IP |
| `get_profile` / `set_profile` | read / push‑and‑save a profile |
| `start` / `stop` | run / halt the automation session |
| `set_keyboard` | per‑session keyboard on/off |
| `set_stealth` | rewrite the stealth flag (WiFi escape from stealth) |
| `blink` / `reload` / `reset` | LED / soft reload / reboot |

---

## Stealth & USB disguise

Set in `settings.toml` (or via the GP22→GND jumper for stealth):

```toml
PICO_STEALTH = "true"                       # hide CIRCUITPY drive + serial from the target
PICO_DRIVE_LABEL = "MOUSE2"                  # rename the drive (non‑stealth only)
PICO_USB_MANUFACTURER = "Logitech"           # USB identity strings
PICO_USB_PRODUCT = "USB Optical Mouse"
# PICO_USB_VID = "0x046D"                     # optional VID/PID spoof (can re‑trigger
# PICO_USB_PID = "0xC077"                     #   the macOS Keyboard Setup Assistant)
```

- In **stealth**, the drive/serial are hidden but the Pico's own code can write `/profile.json`, so
  you can push profiles over WiFi and they persist on flash. WiFi still works.
- **Recovering from stealth:** because editing `settings.toml` needs the (hidden) drive, use the
  WiFi escape — `set_stealth(false)` then `reset` — to reboot with the drive/serial back:
  ```python
  from pico_client import Pico
  with Pico() as p: p.set_stealth(False); p.reset()
  ```
  (Or trigger CircuitPython **safe mode**, which skips `boot.py`.)

### Deploying a hands‑free device
1. Calibrate and build the profile you want (UI or CLI), `autorun = true`.
2. With the Pico on the controller, write the profile and enable stealth/disguise, **then** move the
   Pico to the target. It boots stealthed, loads the profile from flash, and runs on its own.
3. Pushing a profile while in stealth saves it to flash over WiFi (no drive needed).

---

## Networking notes

- **2.4 GHz only.** SSIDs are case‑sensitive.
- **AP / client isolation** (ISP routers, guest networks) blocks device‑to‑device traffic even on the
  same subnet — symptom: the controller reaches the gateway but ARP to the Pico stays `incomplete`.
  Fix: disable isolation, use a wired controller, or a dedicated/hotspot network.
- **Phone hotspots** work but power‑save: join while the hotspot screen is open, prefer the mDNS name
  over a hardcoded IP (DHCP reassigns), and keep the connection warm. The client retries and warms the
  link automatically.

---

## License / usage

© MikeRyanKS. Published for reference and authorized testing/research use. All rights reserved unless a
`LICENSE` file states otherwise. `pico/lib/adafruit_hid/` is Adafruit's library, used under its MIT
license.
