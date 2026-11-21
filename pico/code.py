# code.py v2 - Pico 2 W HID rig with on-device autonomous automation.
#
# What's new vs v1:
#   * Loads /profile.json and runs a randomized, human-like click/type pattern
#     on boot - NO Mac and NO WiFi required once configured.
#   * Every generated coordinate is hard-clamped to the profile's "zone"
#     (an HID-unit rectangle = one display), so it never strays off-screen.
#   * WiFi server still runs (cooperatively) to push new profiles, start/stop,
#     toggle the keyboard, query status, and for live control / calibration.
#   * Keyboard can be disabled per session (device stays present, just silent).
#   * LED status: slow pulse = idle, steady-ish = running, flurry = command.
#
# Protocol: one JSON object per line over TCP (default 8765), token-authed.
# Coordinates are RAW absolute HID units 0..32767.

import json
import os
import struct
import time
from random import random, uniform, randint, choice

import board
import digitalio
import microcontroller
import socketpool
import supervisor
import usb_hid
import wifi

from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keyboard_layout_us import KeyboardLayoutUS
from adafruit_hid.keycode import Keycode
from adafruit_hid.mouse import Mouse

# ---------------------------------------------------------------- settings
SSID = os.getenv("PICO_WIFI_SSID")
PASSWORD = os.getenv("PICO_WIFI_PASSWORD")
TOKEN = os.getenv("PICO_TOKEN")
HOSTNAME = os.getenv("PICO_HOSTNAME") or "pico-hid"
PORT = int(os.getenv("PICO_PORT") or 8765)
WIFI_OFF = (os.getenv("PICO_WIFI_OFF") or "").strip().lower() in ("1", "true", "yes", "on")
AUTORUN_ALWAYS = (os.getenv("PICO_AUTORUN_ALWAYS") or "").strip().lower() in ("1", "true", "yes", "on")

HID_MAX = 32767
PROFILE_PATH = "/profile.json"

# ---------------------------------------------------------------- LED
led = digitalio.DigitalInOut(board.LED)
led.switch_to_output(False)


def blink(times=3, period=0.1):
    for _ in range(times):
        led.value = True
        time.sleep(period)
        led.value = False
        time.sleep(period)


def deployed():
    """True when the GP22 jumper is installed (stealth/deploy mode). Autorun only
    fires when deployed, so the device never auto-clicks a config host."""
    try:
        pin = digitalio.DigitalInOut(board.GP22)
        pin.switch_to_input(pull=digitalio.Pull.UP)
        val = not pin.value   # jumper GP22->GND pulls it low
        pin.deinit()
        return val
    except Exception:
        return False


# ---------------------------------------------------------------- HID devices
mouse_devices = [d for d in usb_hid.devices if d.usage_page == 0x01 and d.usage == 0x02]
rel_mouse = Mouse(mouse_devices[:1])
abs_dev = mouse_devices[-1]
kbd = Keyboard(usb_hid.devices)
layout = KeyboardLayoutUS(kbd)

state = {"x": HID_MAX // 2, "y": HID_MAX // 2, "buttons": 0}
BUTTONS = {"left": 1, "right": 2, "middle": 4, "back": 8, "forward": 16}


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def abs_report(wheel=0):
    abs_dev.send_report(
        struct.pack("<BHHb", state["buttons"], state["x"], state["y"], wheel)
    )


def abs_move(x, y):
    state["x"] = clamp(int(x), 0, HID_MAX)
    state["y"] = clamp(int(y), 0, HID_MAX)
    abs_report()


def button_mask(name):
    mask = BUTTONS.get(str(name or "left").lower())
    if mask is None:
        raise ValueError("unknown button: " + str(name))
    return mask


# ---------------------------------------------------------------- keys
KEY_ALIASES = {
    "cmd": "GUI", "command": "GUI", "win": "GUI", "super": "GUI", "meta": "GUI",
    "rcmd": "RIGHT_GUI",
    "option": "ALT", "opt": "ALT", "alt": "ALT", "roption": "RIGHT_ALT",
    "ctrl": "CONTROL", "control": "CONTROL", "rctrl": "RIGHT_CONTROL",
    "shift": "SHIFT", "rshift": "RIGHT_SHIFT",
    "enter": "ENTER", "return": "ENTER",
    "esc": "ESCAPE", "escape": "ESCAPE",
    "tab": "TAB", "space": "SPACEBAR",
    "backspace": "BACKSPACE", "delete": "BACKSPACE",
    "fwddelete": "DELETE", "forwarddelete": "DELETE",
    "up": "UP_ARROW", "down": "DOWN_ARROW", "left": "LEFT_ARROW", "right": "RIGHT_ARROW",
    "pageup": "PAGE_UP", "pagedown": "PAGE_DOWN", "home": "HOME", "end": "END",
    "capslock": "CAPS_LOCK",
    "-": "MINUS", "=": "EQUALS", "[": "LEFT_BRACKET", "]": "RIGHT_BRACKET",
    "\\": "BACKSLASH", ";": "SEMICOLON", "'": "QUOTE", "`": "GRAVE_ACCENT",
    ",": "COMMA", ".": "PERIOD", "/": "FORWARD_SLASH",
}
DIGITS = ("ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE")


def keycode(name):
    n = str(name).lower()
    attr = KEY_ALIASES.get(n)
    if attr is None:
        if len(n) == 1 and n.isdigit():
            attr = DIGITS[int(n)]
        else:
            attr = n.upper()
    code = getattr(Keycode, attr, None)
    if code is None:
        raise ValueError("unknown key: " + str(name))
    return code


def release_everything():
    try:
        kbd.release_all()
    except Exception:
        pass
    try:
        state["buttons"] = 0
        abs_report()
    except Exception:
        pass


# ---------------------------------------------------------------- profile
DEFAULT_PROFILE = {
    "name": "default",
    "autorun": False,
    "keyboard_enabled": False,
    "zone": {"x0": 0, "y0": 0, "x1": HID_MAX, "y1": HID_MAX},
    "mouse": {
        "enabled": True, "button": "left", "double_click_chance": 0.08,
        "clicks_per_session": {"min": 20, "max": 60}, "targets": [],
        "move": {"steps": {"min": 18, "max": 55},
                 "duration_ms": {"min": 300, "max": 1300}, "jitter": 40, "curve": 0.18},
        "dwell_ms": {"min": 40, "max": 220}, "hold_ms": {"min": 40, "max": 110},
    },
    "keyboard": {"enabled": False, "chance": 0.2,
                 "phrases": ["hello"], "type_delay_ms": {"min": 40, "max": 170}},
    "session": {"loop": True, "max_actions": 0, "start_delay_ms": 4000,
                "action_gap_ms": {"min": 500, "max": 3500}},
}


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_profile():
    try:
        with open(PROFILE_PATH) as f:
            data = json.load(f)
        return _merge(DEFAULT_PROFILE, data)
    except Exception as e:  # noqa: BLE001
        print("no/invalid profile, using defaults:", e)
        return dict(DEFAULT_PROFILE)


def save_profile(prof):
    """Persist to flash. Only works when the FS is writable to code (stealth mode,
    where boot.py did storage.remount). In dev (drive mounted on host) this fails."""
    try:
        with open(PROFILE_PATH, "w") as f:
            json.dump(prof, f)
        return True, ""
    except OSError as e:
        return False, "filesystem read-only (not in stealth mode): %s" % e


# ---------------------------------------------------------------- automation engine
auto = {"running": False, "profile": DEFAULT_PROFILE, "keyboard_on": False,
        "actions": 0, "session_target": 0, "next_t": 0.0}


def _rng(spec, lo_default=0, hi_default=0):
    lo = spec.get("min", lo_default)
    hi = spec.get("max", hi_default)
    return uniform(lo, hi)


def _irng(spec):
    return randint(int(spec.get("min", 0)), int(spec.get("max", 0)))


def zone_clamp(prof, x, y):
    z = prof["zone"]
    return (clamp(x, z["x0"], z["x1"]), clamp(y, z["y0"], z["y1"]))


def pick_target(prof):
    targets = prof["mouse"].get("targets") or []
    if targets:
        r = choice(targets)
        x = randint(int(r["x0"]), int(r["x1"]))
        y = randint(int(r["y0"]), int(r["y1"]))
    else:
        z = prof["zone"]
        x = randint(int(z["x0"]), int(z["x1"]))
        y = randint(int(z["y0"]), int(z["y1"]))
    return zone_clamp(prof, x, y)


def human_move(prof, tx, ty):
    """Eased, slightly-curved, jittered multi-step move -> never repeats, stays in zone."""
    mv = prof["mouse"]["move"]
    steps = max(1, _irng(mv["steps"]))
    dur = _rng(mv["duration_ms"], 300, 300) / 1000.0
    jit = mv.get("jitter", 0)
    curve = mv.get("curve", 0.0)
    sx, sy = state["x"], state["y"]
    # Quadratic Bezier control point offset to one side -> a gentle arc, not a ruler line.
    cx = (sx + tx) / 2 + uniform(-1, 1) * abs(tx - sx) * curve
    cy = (sy + ty) / 2 + uniform(-1, 1) * abs(ty - sy) * curve
    for i in range(1, steps + 1):
        t = i / steps
        e = t * t * (3 - 2 * t)  # smoothstep ease-in-out
        mx = (1 - e) ** 2 * sx + 2 * (1 - e) * e * cx + e * e * tx
        my = (1 - e) ** 2 * sy + 2 * (1 - e) * e * cy + e * e * ty
        if i < steps and jit:
            mx += uniform(-jit, jit)
            my += uniform(-jit, jit)
        mx, my = zone_clamp(prof, mx, my)
        abs_move(mx, my)
        time.sleep(dur / steps)


def _click_here(prof):
    """Press/release at the current pointer position."""
    time.sleep(_rng(prof["mouse"]["dwell_ms"], 40, 120) / 1000.0)
    mask = button_mask(prof["mouse"].get("button", "left"))
    count = 2 if random() < prof["mouse"].get("double_click_chance", 0) else 1
    for i in range(count):
        state["buttons"] |= mask
        abs_report()
        time.sleep(_rng(prof["mouse"]["hold_ms"], 40, 90) / 1000.0)
        state["buttons"] &= ~mask
        abs_report()
        if i < count - 1:
            time.sleep(uniform(0.05, 0.12))


def do_mouse(prof):
    """Move to a random in-zone target, then click only some of the time, so the
    pointer also just wanders like a human. click_chance 1.0 = always click (default)."""
    tx, ty = pick_target(prof)
    human_move(prof, tx, ty)
    if random() < prof["mouse"].get("click_chance", 1.0):
        _click_here(prof)


def do_type(prof):
    phrases = prof["keyboard"].get("phrases") or []
    if not phrases:
        return
    text = str(choice(phrases))
    d = prof["keyboard"]["type_delay_ms"]
    for ch in text:
        layout.write(ch)
        time.sleep(_rng(d, 40, 120) / 1000.0)


def automation_step(prof):
    kb = prof["keyboard"].get("enabled") and auto["keyboard_on"]
    if kb and random() < prof["keyboard"].get("chance", 0):
        do_type(prof)
    elif prof["mouse"].get("enabled", True):
        do_mouse(prof)


def start_session(prof):
    auto["profile"] = prof
    auto["keyboard_on"] = bool(prof.get("keyboard_enabled"))
    auto["actions"] = 0
    cps = prof["mouse"].get("clicks_per_session", {})
    auto["session_target"] = _irng(cps) if cps else 0
    auto["running"] = True
    auto["next_t"] = time.monotonic() + prof["session"].get("start_delay_ms", 0) / 1000.0


def stop_session():
    auto["running"] = False
    release_everything()


# ---------------------------------------------------------------- command dispatch
def handle(msg):
    if not TOKEN or msg.get("token") != TOKEN:
        return {"ok": False, "error": "bad token"}
    cmd = msg.get("cmd")
    led.value = not led.value
    prof = auto["profile"]

    if cmd == "ping":
        pass
    # ---- live / calibration control (unchanged from v1) ----
    elif cmd == "move":
        abs_move(msg["x"], msg["y"])
    elif cmd == "down":
        state["buttons"] |= button_mask(msg.get("button"))
        abs_report()
    elif cmd == "up":
        state["buttons"] &= ~button_mask(msg.get("button"))
        abs_report()
    elif cmd == "click":
        if "x" in msg and "y" in msg:
            abs_move(msg["x"], msg["y"])
            time.sleep(0.02)
        mask = button_mask(msg.get("button"))
        for i in range(int(msg.get("count", 1))):
            state["buttons"] |= mask
            abs_report()
            time.sleep(float(msg.get("hold", 0.05)))
            state["buttons"] &= ~mask
            abs_report()
            if i < int(msg.get("count", 1)) - 1:
                time.sleep(float(msg.get("interval", 0.08)))
    elif cmd == "scroll":
        amount = int(msg.get("amount", 0))
        while amount != 0:
            chunk = clamp(amount, -10, 10)
            abs_report(wheel=chunk)
            amount -= chunk
            time.sleep(float(msg.get("delay", 0.02)))
    elif cmd == "rel":
        rel_mouse.move(int(msg.get("dx", 0)), int(msg.get("dy", 0)))
    elif cmd == "type":
        for ch in str(msg.get("text", "")):
            layout.write(ch)
            if msg.get("delay"):
                time.sleep(float(msg["delay"]))
    elif cmd == "key":
        codes = [keycode(k) for k in msg.get("keys", [])]
        if not codes:
            return {"ok": False, "error": "no keys"}
        kbd.press(*codes)
        time.sleep(float(msg.get("hold", 0.05)))
        kbd.release_all()
    elif cmd == "key_down":
        kbd.press(*[keycode(k) for k in msg.get("keys", [])])
    elif cmd == "key_up":
        kbd.release(*[keycode(k) for k in msg.get("keys", [])])
    elif cmd == "release_all":
        release_everything()
    elif cmd == "blink":
        blink(int(msg.get("times", 5)))
    # ---- automation control (new in v2) ----
    elif cmd == "get_status":
        return {"ok": True, "running": auto["running"], "actions": auto["actions"],
                "session_target": auto["session_target"],
                "keyboard_on": auto["keyboard_on"], "profile": prof.get("name"),
                "ip": str(wifi.radio.ipv4_address), "x": state["x"], "y": state["y"]}
    elif cmd == "get_profile":
        return {"ok": True, "profile": prof}
    elif cmd == "set_profile":
        newp = _merge(DEFAULT_PROFILE, msg.get("profile") or {})
        auto["profile"] = newp
        saved, why = save_profile(newp)
        if msg.get("start") or newp.get("autorun"):
            start_session(newp)
        return {"ok": True, "saved": saved, "note": why, "running": auto["running"]}
    elif cmd == "start":
        start_session(auto["profile"])
        return {"ok": True, "running": True, "session_target": auto["session_target"]}
    elif cmd == "stop":
        stop_session()
        return {"ok": True, "running": False}
    elif cmd == "set_keyboard":
        auto["keyboard_on"] = bool(msg.get("enabled"))
        return {"ok": True, "keyboard_on": auto["keyboard_on"]}
    elif cmd == "set_stealth":
        # Rewrite PICO_STEALTH in settings.toml (only works when FS is writable to
        # code == already in stealth). This is the WiFi escape from stealth: set
        # enabled=false then send cmd "reset" to reboot with the drive/serial back.
        val = "true" if bool(msg.get("enabled")) else "false"
        try:
            try:
                with open("/settings.toml") as f:
                    lines = f.read().split("\n")
            except OSError:
                lines = []
            found = False
            for i in range(len(lines)):
                if lines[i].strip().startswith("PICO_STEALTH"):
                    lines[i] = 'PICO_STEALTH = "%s"' % val
                    found = True
            if not found:
                lines.append('PICO_STEALTH = "%s"' % val)
            with open("/settings.toml", "w") as f:
                f.write("\n".join(lines))
            return {"ok": True, "stealth_next_boot": val, "note": "send cmd 'reset' to apply"}
        except OSError as e:
            return {"ok": False, "error": "settings read-only (only works in stealth): %s" % e}
    elif cmd == "reload":
        supervisor.reload()
    elif cmd == "reset":
        microcontroller.reset()
    else:
        return {"ok": False, "error": "unknown cmd: " + str(cmd)}

    led.value = True
    return {"ok": True, "x": state["x"], "y": state["y"], "buttons": state["buttons"]}


# ---------------------------------------------------------------- network (cooperative)
def connect_wifi(timeout=20):
    if not SSID:
        print("PICO_WIFI_SSID missing")
        return False
    try:
        wifi.radio.hostname = HOSTNAME
    except Exception:
        pass
    deadline = time.monotonic() + timeout
    while not wifi.radio.connected and time.monotonic() < deadline:
        try:
            print("WiFi: connecting to", SSID)
            wifi.radio.connect(SSID, PASSWORD)
        except Exception as e:  # noqa: BLE001
            print("WiFi error:", e)
            time.sleep(1.5)
    if wifi.radio.connected:
        print("WiFi OK. IP:", wifi.radio.ipv4_address)
        return True
    print("WiFi not connected (continuing offline)")
    return False


def send_all(conn, data):
    view = memoryview(data)
    while len(view):
        view = view[conn.send(view):]


def service_conn(conn, cstate, buf):
    """Process any immediately-available data on a NON-BLOCKING controller socket,
    then return. Automation keeps running between calls, so a connected controller
    (e.g. the web UI polling status) no longer pauses a live session.
    Returns the socket if still open, else None."""
    try:
        n = conn.recv_into(buf)
    except OSError:
        # EAGAIN (no data yet) or a dead socket -> close only after an idle timeout.
        if time.monotonic() > cstate["deadline"]:
            try:
                conn.close()
            except Exception:
                pass
            return None
        return conn
    if n == 0:
        try:
            conn.close()
        except Exception:
            pass
        return None
    cstate["deadline"] = time.monotonic() + 10
    cstate["pending"] += bytes(buf[:n])
    while b"\n" in cstate["pending"]:
        line, cstate["pending"] = cstate["pending"].split(b"\n", 1)
        if not line.strip():
            continue
        try:
            reply = handle(json.loads(line.decode()))
        except Exception as e:  # noqa: BLE001
            reply = {"ok": False, "error": str(e)}
        try:
            send_all(conn, (json.dumps(reply) + "\n").encode())
        except OSError:
            try:
                conn.close()
            except Exception:
                pass
            return None
    return conn


def main():
    if not WIFI_OFF and not TOKEN:
        print("PICO_TOKEN missing - refusing to start")
        while True:
            blink(2, 0.3)
            time.sleep(1)

    auto["profile"] = load_profile()
    print("profile:", auto["profile"].get("name"),
          "autorun:", auto["profile"].get("autorun"))

    server = None
    if WIFI_OFF:
        print("WiFi disabled (PICO_WIFI_OFF) - pure autonomous mode")
    else:
        connect_wifi()
        try:
            import mdns
            m = mdns.Server(wifi.radio)
            m.hostname = HOSTNAME
            m.advertise_service(service_type="_pico-hid", protocol="_tcp", port=PORT)
        except Exception as e:  # noqa: BLE001
            print("mDNS unavailable:", e)
        if wifi.radio.connected:
            pool = socketpool.SocketPool(wifi.radio)
            server = pool.socket(pool.AF_INET, pool.SOCK_STREAM)
            server.setsockopt(pool.SOL_SOCKET, pool.SO_REUSEADDR, 1)
            server.bind(("0.0.0.0", PORT))
            server.listen(1)
            server.settimeout(0)   # non-blocking accept; automation runs every loop
            print("Listening on port", PORT)

    buf = bytearray(2048)
    if auto["profile"].get("autorun") and (AUTORUN_ALWAYS or deployed()):
        start_session(auto["profile"])
        print("autorun: session started, target =", auto["session_target"])
    elif auto["profile"].get("autorun"):
        print("autorun armed but gated off (no jumper / AUTORUN_ALWAYS off) - idle")

    last_wifi_try = time.monotonic()
    conn = None
    cstate = {"pending": b"", "deadline": 0.0}
    while True:
        # 1) Accept a controller (non-blocking) if none is connected.
        if server is not None and conn is None:
            try:
                conn, addr = server.accept()
                conn.settimeout(0)
                cstate["pending"] = b""
                cstate["deadline"] = time.monotonic() + 10
                print("controller:", addr)
            except OSError:
                pass  # no one knocking - normal

        # 2) Service the controller's pending data (non-blocking, returns fast).
        if conn is not None:
            conn = service_conn(conn, cstate, buf)

        # 3) Run one automation step if due - regardless of any connection.
        if auto["running"] and time.monotonic() >= auto["next_t"]:
            try:
                automation_step(auto["profile"])
            except Exception as e:  # noqa: BLE001
                print("automation step error:", e)
            auto["actions"] += 1
            led.value = True
            gap = _rng(auto["profile"]["session"].get("action_gap_ms", {}), 500, 1500) / 1000.0
            auto["next_t"] = time.monotonic() + gap
            tgt = auto["session_target"]
            if tgt and auto["actions"] >= tgt:
                if auto["profile"]["session"].get("loop"):
                    start_session(auto["profile"])  # reshuffle a fresh session
                else:
                    stop_session()
                    print("session complete")

        # 4) Idle LED pulse + quiet WiFi recovery when not running.
        if not auto["running"]:
            led.value = (time.monotonic() % 2) < 1  # slow pulse
            if not WIFI_OFF and server is None and time.monotonic() - last_wifi_try > 15:
                last_wifi_try = time.monotonic()
                if connect_wifi(timeout=8):
                    return supervisor.reload()  # rebuild sockets cleanly
        time.sleep(0.005)


main()
