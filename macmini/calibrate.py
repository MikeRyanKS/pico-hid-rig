#!/usr/bin/env python3
"""
calibrate.py — work out where the Pico's absolute pointer lands on the target Mac.

Why: with a laptop screen + an external monitor, macOS may map the absolute
pointer to just the main display or to the whole desktop. This finds out and
saves the answer to pico_config.json so click(x, y) uses real macOS points.

Run it on the Mac mini while you can SEE the target Mac (in person or via
Screen Sharing):
    python3 calibrate.py

Tip: on the target Mac, Cmd+Shift+4 shows a crosshair with the current
coordinates in points. Press Esc to cancel it.
"""

import json
import os
import time

from pico_client import CONFIG_PATH, HID_MAX, Pico, load_config

CORNERS = [
    ("TOP-LEFT", 0, 0),
    ("TOP-RIGHT", HID_MAX, 0),
    ("BOTTOM-RIGHT", HID_MAX, HID_MAX),
    ("BOTTOM-LEFT", 0, HID_MAX),
    ("CENTRE", HID_MAX // 2, HID_MAX // 2),
]


def ask_int(prompt, default):
    raw = input("%s [%s]: " % (prompt, default)).strip()
    return int(raw) if raw else int(default)


def main():
    cfg = load_config()
    p = Pico()
    print("Connected:", p.ping())
    print("\nWatch the TARGET Mac's screen. I'll move the pointer to each extreme.\n")

    for name, hx, hy in CORNERS:
        p.move_raw(hx, hy)
        input("  Pointer sent to raw %-12s - note where it is, then press Enter " % name)

    print("""
Where did the four corners land?
  1) The corners of ONE display (e.g. just the MacBook screen)
  2) The corners of the WHOLE desktop (both monitors together)
""")
    choice = input("Enter 1 or 2: ").strip()

    s = cfg["screen"]
    if choice == "2":
        print("\nEnter the bounding box of both displays in macOS points.")
        print("(System Settings > Displays shows the arrangement; Cmd+Shift+4 shows coordinates.)")
        s["offset_x"] = ask_int("Left edge X (can be negative)", s.get("offset_x", 0))
        s["offset_y"] = ask_int("Top edge Y (can be negative)", s.get("offset_y", 0))
        s["width"] = ask_int("Total width", s["width"])
        s["height"] = ask_int("Total height", s["height"])
    else:
        print("\nEnter that display's size in points ('Looks like' in System Settings > Displays).")
        print("If it's the external monitor, enter its top-left position too.")
        s["width"] = ask_int("Width", s["width"])
        s["height"] = ask_int("Height", s["height"])
        s["offset_x"] = ask_int("Left edge X (0 for the main display)", s.get("offset_x", 0))
        s["offset_y"] = ask_int("Top edge Y (0 for the main display)", s.get("offset_y", 0))

    p.screen = s
    print("\nCheck: moving to the centre of that area, then 100pt in from the top-left.")
    p.move(s["offset_x"] + s["width"] / 2, s["offset_y"] + s["height"] / 2)
    time.sleep(1.5)
    p.move(s["offset_x"] + 100, s["offset_y"] + 100)

    if input("Did both land where expected? (y/n): ").strip().lower().startswith("y"):
        saved = {}
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH) as f:
                saved = json.load(f)
        saved["screen"] = s
        with open(CONFIG_PATH, "w") as f:
            json.dump(saved, f, indent=2)
        print("Saved to", CONFIG_PATH)
    else:
        print("Not saved. Re-run and double-check the sizes, or send me what you saw.")
    p.close()


if __name__ == "__main__":
    main()
