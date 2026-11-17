#!/usr/bin/env python3
"""
pico_client.py — control the target Mac through the Pico WH (Mac mini side).

Pure standard library, Python 3.8+. No installs needed.

Use as a library:
    from pico_client import Pico
    with Pico() as p:
        p.click(640, 400)
        p.type("hello@test.com")
        p.hotkey("cmd", "a")

Or from the terminal:
    python3 pico_client.py ping
    python3 pico_client.py click 640 400
    python3 pico_client.py type "hello"
    python3 pico_client.py key cmd space

Settings come from pico_config.json (next to this file), overridden by the
environment variables PICO_HOST, PICO_PORT, PICO_TOKEN.

Coordinates are macOS *points* (what Cmd+Shift+4 shows), not Retina pixels.
"""

import json
import os
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "pico_config.json")
HID_MAX = 32767


class PicoError(RuntimeError):
    pass


def load_config(path=CONFIG_PATH):
    cfg = {
        "host": "pico-hid.local",
        "port": 8765,
        "token": "",
        # The rectangle (in macOS points) that the Pico's absolute range covers.
        # Set by calibrate.py. Default: a 1512x982 MacBook screen.
        "screen": {"width": 1512, "height": 982, "offset_x": 0, "offset_y": 0},
    }
    if os.path.exists(path):
        with open(path) as f:
            user = json.load(f)
        screen = dict(cfg["screen"], **user.get("screen", {}))
        cfg.update(user)
        cfg["screen"] = screen
    cfg["host"] = os.environ.get("PICO_HOST", cfg["host"])
    cfg["port"] = int(os.environ.get("PICO_PORT", cfg["port"]))
    cfg["token"] = os.environ.get("PICO_TOKEN", cfg["token"])
    return cfg


class Pico:
    def __init__(self, host=None, port=None, token=None, screen=None, config_path=CONFIG_PATH):
        cfg = load_config(config_path)
        self.host = host or cfg["host"]
        self.port = int(port or cfg["port"])
        self.token = token or cfg["token"]
        self.screen = screen or cfg["screen"]
        if not self.token:
            raise PicoError("No token. Put it in pico_config.json or set PICO_TOKEN.")
        # Resilience knobs. Power-saving Wi-Fi (phone hotspots especially) parks
        # idle clients and drops the first packet after a gap, so a cold socket's
        # first exchange often gets reset. These let the client ride through that.
        self.retries = int(os.environ.get("PICO_RETRIES", 6))   # attempts per call
        self.connect_retries = 3        # socket-open attempts (dropped SYNs)
        self.connect_timeout = 8        # seconds to establish a socket
        self.warm_timeout = 5           # seconds for the warm-up ping
        self.backoff = 0.3              # base backoff, grows per attempt
        self._sock = None
        self._file = None

    # ------------------------------------------------------------ plumbing
    def _open(self):
        """Open a raw socket (retrying dropped SYNs) with keepalive + nodelay."""
        self.close()
        last = None
        for attempt in range(self.connect_retries):
            try:
                self._sock = socket.create_connection(
                    (self.host, self.port), timeout=self.connect_timeout)
                break
            except OSError as e:
                last = e
                time.sleep(self.backoff * (attempt + 1))
        else:
            raise PicoError(
                "could not reach %s:%s (%s)" % (self.host, self.port, last))
        for level, opt in ((socket.SOL_SOCKET, socket.SO_KEEPALIVE),
                           (socket.IPPROTO_TCP, socket.TCP_NODELAY)):
            try:
                self._sock.setsockopt(level, opt, 1)
            except OSError:
                pass
        self._file = self._sock.makefile("rwb")

    def _send_once(self, cmd, timeout, **kwargs):
        """One request/response on the current socket. Raises on any transport problem."""
        line = (json.dumps(dict(kwargs, cmd=cmd, token=self.token)) + "\n").encode()
        self._sock.settimeout(timeout)
        self._file.write(line)
        self._file.flush()
        reply = self._file.readline()
        if not reply:
            raise ConnectionError("Pico closed the connection")
        return json.loads(reply)

    def _connect(self):
        """Open AND warm the link: spend the cold-connection reset on a ping, so a
        dozing AP is awake before the caller's real (maybe non-idempotent) command."""
        last = None
        for attempt in range(self.retries):
            try:
                self._open()
                if self._send_once("ping", self.warm_timeout).get("ok"):
                    return
                last = ConnectionError("warm-up ping not ok")
            except (OSError, ConnectionError, ValueError) as e:
                last = e
            self.close()
            time.sleep(self.backoff * (attempt + 1))
        raise PicoError(
            "could not establish a warm link to %s:%s (%s)"
            % (self.host, self.port, last))

    def close(self):
        for obj in (self._file, self._sock):
            try:
                if obj:
                    obj.close()
            except OSError:
                pass
        self._sock = self._file = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            self.release_all()
        except Exception:
            pass
        self.close()

    def _call(self, cmd, timeout=10, retries=None, **kwargs):
        if retries is None:
            retries = self.retries
        # _connect() warms the link, so the cold-reset lands on the warm-up ping,
        # not here -- a retry of the real command almost never fires, which keeps
        # non-idempotent commands (type/click/scroll) from doubling up in practice.
        last = None
        for attempt in range(retries):
            try:
                if self._sock is None:
                    self._connect()
                data = self._send_once(cmd, timeout, **kwargs)
                if not data.get("ok"):
                    raise PicoError(data.get("error", "unknown error"))
                return data
            except PicoError:
                raise  # a real reply saying ok:false -- don't retry it
            except (OSError, ConnectionError, ValueError) as e:
                last = e
                self.close()
                if attempt < retries - 1:
                    time.sleep(self.backoff * (attempt + 1))
        raise PicoError(
            "command %r failed after %d attempt(s): %s" % (cmd, retries, last))

    # ------------------------------------------------------------ coordinates
    def to_hid(self, x, y):
        """macOS points -> raw absolute HID units (0..32767)."""
        s = self.screen
        hx = (x - s["offset_x"]) / max(s["width"] - 1, 1) * HID_MAX
        hy = (y - s["offset_y"]) / max(s["height"] - 1, 1) * HID_MAX
        return (max(0, min(HID_MAX, round(hx))), max(0, min(HID_MAX, round(hy))))

    # ------------------------------------------------------------ mouse
    def ping(self):
        return self._call("ping")

    def move(self, x, y):
        hx, hy = self.to_hid(x, y)
        return self._call("move", x=hx, y=hy)

    def move_raw(self, hx, hy):
        return self._call("move", x=int(hx), y=int(hy))

    def move_rel(self, dx, dy):
        return self._call("rel", dx=int(dx), dy=int(dy))

    def click(self, x=None, y=None, button="left", count=1):
        kw = {"button": button, "count": count}
        if x is not None and y is not None:
            kw["x"], kw["y"] = self.to_hid(x, y)
        return self._call("click", **kw)

    def double_click(self, x=None, y=None):
        return self.click(x, y, count=2)

    def right_click(self, x=None, y=None):
        return self.click(x, y, button="right")

    def mouse_down(self, button="left"):
        return self._call("down", button=button)

    def mouse_up(self, button="left"):
        return self._call("up", button=button)

    def drag(self, x1, y1, x2, y2, steps=20, duration=0.4, button="left"):
        self.move(x1, y1)
        time.sleep(0.05)
        self.mouse_down(button)
        time.sleep(0.05)
        for i in range(1, steps + 1):
            t = i / steps
            self.move(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
            time.sleep(duration / steps)
        time.sleep(0.05)
        return self.mouse_up(button)

    def scroll(self, amount):
        """Positive = scroll up, negative = scroll down."""
        return self._call("scroll", amount=int(amount), timeout=30)

    # ------------------------------------------------------------ keyboard
    def type(self, text, delay=0.01, chunk=200):
        """Type text (US keyboard layout on the target Mac)."""
        for i in range(0, len(text), chunk):
            part = text[i:i + chunk]
            self._call("type", text=part, delay=delay, timeout=10 + len(part) * (delay + 0.02))

    def hotkey(self, *keys, hold=0.05):
        """Press keys together, e.g. hotkey('cmd', 'shift', '4')."""
        return self._call("key", keys=list(keys), hold=hold)

    press = hotkey

    def key_down(self, *keys):
        return self._call("key_down", keys=list(keys))

    def key_up(self, *keys):
        return self._call("key_up", keys=list(keys))

    def release_all(self):
        return self._call("release_all")

    # ------------------------------------------------------------ automation
    def status(self):
        """Device state: running?, actions done, session target, keyboard on?, IP."""
        return self._call("get_status")

    def get_profile(self):
        return self._call("get_profile").get("profile", {})

    def push_profile(self, profile, start=False):
        """Send a profile; the Pico saves it to flash (in stealth) and optionally starts."""
        return self._call("set_profile", profile=profile, start=bool(start), timeout=15)

    def start(self):
        return self._call("start")

    def stop(self):
        return self._call("stop")

    def set_keyboard(self, enabled):
        """Per-session keyboard toggle (device stays present, just silent when off)."""
        return self._call("set_keyboard", enabled=bool(enabled))

    def set_stealth(self, enabled):
        """Rewrite the stealth flag in the device's settings.toml. Only works while
        already in stealth (FS writable). Follow with reset() to apply. This is the
        WiFi escape to get the drive/serial back: set_stealth(False) then reset()."""
        return self._call("set_stealth", enabled=bool(enabled))

    # ------------------------------------------------------------ misc
    def blink(self, times=5):
        return self._call("blink", times=times)

    def reset(self):
        # The Pico reboots and never replies, so don't retry the no-answer.
        try:
            self._call("reset", timeout=2, retries=1)
        except (OSError, ConnectionError, PicoError):
            pass
        self.close()

    @staticmethod
    def wait(seconds):
        time.sleep(seconds)


# ---------------------------------------------------------------- CLI
USAGE = """usage: pico_client.py <command> [args]

  ping                       check the Pico is reachable
  move X Y                   move pointer to point (X, Y)
  raw HX HY                  move to raw HID units (0..32767)
  rel DX DY                  relative move
  click X Y | click          left click (at X, Y or where the pointer is)
  dclick X Y                 double click
  rclick X Y                 right click
  drag X1 Y1 X2 Y2           drag
  scroll N                   scroll (+up / -down)
  type "text"                type text
  key K1 [K2 ...]            key combo, e.g. key cmd space
  release                    release all keys and buttons
  blink                      blink the Pico's LED (find which board is which)
  reset                      reboot the Pico

  -- automation --
  status                     show device state (running, actions, keyboard, IP)
  start                      start the saved automation session
  stop                       stop the automation session
  keyboard on|off            enable/disable typing for this session
  get-profile                print the device's current profile JSON
  push-profile FILE [--start]  send a profile file to the device (saves in stealth)
"""


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    cmd, args = argv[0], argv[1:]
    nums = lambda: [float(a) for a in args]  # noqa: E731
    with Pico() as p:
        if cmd == "ping":
            print(p.ping())
        elif cmd == "move":
            p.move(*nums())
        elif cmd == "raw":
            p.move_raw(*nums())
        elif cmd == "rel":
            p.move_rel(*nums())
        elif cmd == "click":
            p.click(*nums())
        elif cmd == "dclick":
            p.double_click(*nums())
        elif cmd == "rclick":
            p.right_click(*nums())
        elif cmd == "drag":
            p.drag(*nums())
        elif cmd == "scroll":
            p.scroll(int(args[0]))
        elif cmd == "type":
            p.type(" ".join(args))
        elif cmd == "key":
            p.hotkey(*args)
        elif cmd == "release":
            p.release_all()
        elif cmd == "blink":
            p.blink()
        elif cmd == "reset":
            p.reset()
        elif cmd == "status":
            print(p.status())
        elif cmd == "start":
            print(p.start())
        elif cmd == "stop":
            print(p.stop())
        elif cmd == "keyboard":
            print(p.set_keyboard(args[0].lower() in ("on", "1", "true", "yes")))
        elif cmd == "get-profile":
            print(json.dumps(p.get_profile(), indent=2))
        elif cmd == "push-profile":
            with open(args[0]) as f:
                prof = json.load(f)
            print(p.push_profile(prof, start=("--start" in args)))
        else:
            print("unknown command:", cmd)
            print(USAGE)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
