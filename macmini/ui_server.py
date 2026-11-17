#!/usr/bin/env python3
"""
ui_server.py - local web UI for the Pico HID rig (Mac mini side).

Serves a single-page control panel and proxies its actions to the Pico over the
existing JSON/TCP protocol. The browser talks only to this server on localhost;
the Pico's token stays here (never sent to the browser).

Run:
    python3 macmini/ui_server.py            # then open http://localhost:8800
    python3 macmini/ui_server.py --port 9000 --no-open

Pure standard library. No installs.
"""

import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pico_client import HID_MAX, Pico, PicoError, load_config  # noqa: E402

PROFILES_DIR = os.path.join(HERE, "profiles")
UI_HTML = os.path.join(HERE, "ui", "index.html")

# ------------------------------------------------------------ Pico singleton
_lock = threading.Lock()
_pico = None


def _get_pico():
    global _pico
    if _pico is None:
        _pico = Pico()
    return _pico


def _drop_pico():
    global _pico
    try:
        if _pico:
            _pico.close()
    except Exception:
        pass
    _pico = None


def device_call(method, **kwargs):
    """Call a Pico method under the lock; reconnect once on transport error."""
    with _lock:
        try:
            return getattr(_get_pico(), method)(**kwargs)
        except (OSError, ConnectionError, PicoError):
            _drop_pico()
            # one clean retry
            return getattr(_get_pico(), method)(**kwargs)


# ------------------------------------------------------------ local profiles
def list_local():
    try:
        return sorted(f[:-5] for f in os.listdir(PROFILES_DIR) if f.endswith(".json"))
    except FileNotFoundError:
        return []


def save_local(name, profile):
    os.makedirs(PROFILES_DIR, exist_ok=True)
    safe = "".join(c for c in name if c.isalnum() or c in "-_ ").strip() or "profile"
    with open(os.path.join(PROFILES_DIR, safe + ".json"), "w") as f:
        json.dump(profile, f, indent=2)
    return safe


def load_local(name):
    with open(os.path.join(PROFILES_DIR, name + ".json")) as f:
        return json.load(f)


# ------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # quiet

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                with open(UI_HTML, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(500, "UI not found at %s" % UI_HTML, "text/plain")
            return
        if self.path == "/api/config":
            cfg = load_config()
            self._send(200, {"host": cfg["host"], "port": cfg["port"],
                             "screen": cfg["screen"], "hid_max": HID_MAX})
            return
        if self.path == "/api/profiles":
            self._send(200, {"profiles": list_local()})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            self._send(400, {"error": "bad json"})
            return
        try:
            result = self._dispatch(self.path, body)
            self._send(200, {"ok": True, "result": result})
        except PicoError as e:
            self._send(200, {"ok": False, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._send(200, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})

    def _dispatch(self, path, body):
        if path == "/api/ping":
            return device_call("ping")
        if path == "/api/status":
            return device_call("status")
        if path == "/api/get_profile":
            return device_call("get_profile")
        if path == "/api/push_profile":
            return device_call("push_profile", profile=body["profile"],
                               start=bool(body.get("start")))
        if path == "/api/start":
            return device_call("start")
        if path == "/api/stop":
            return device_call("stop")
        if path == "/api/keyboard":
            return device_call("set_keyboard", enabled=bool(body.get("enabled")))
        if path == "/api/raw":
            return device_call("move_raw", hx=int(body["hx"]), hy=int(body["hy"]))
        if path == "/api/move_point":
            return device_call("move", x=float(body["x"]), y=float(body["y"]))
        if path == "/api/click_point":
            return device_call("click", x=float(body["x"]), y=float(body["y"]))
        # ---- local profile storage ----
        if path == "/api/save_local":
            return {"saved": save_local(body["name"], body["profile"])}
        if path == "/api/load_local":
            return {"profile": load_local(body["name"])}
        # ---- save screen calibration to pico_config.json ----
        if path == "/api/save_calibration":
            cfg_path = os.path.join(HERE, "pico_config.json")
            cfg = json.load(open(cfg_path)) if os.path.exists(cfg_path) else {}
            cfg["screen"] = body["screen"]
            json.dump(cfg, open(cfg_path, "w"), indent=2)
            return {"screen": cfg["screen"]}
        raise ValueError("unknown endpoint: " + path)


def main(argv):
    port = 8800
    do_open = True
    if "--port" in argv:
        port = int(argv[argv.index("--port") + 1])
    if "--no-open" in argv:
        do_open = False
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = "http://localhost:%d" % port
    print("Pico HID rig UI -> %s  (Ctrl-C to stop)" % url)
    if do_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
        _drop_pico()


if __name__ == "__main__":
    main(sys.argv[1:])
