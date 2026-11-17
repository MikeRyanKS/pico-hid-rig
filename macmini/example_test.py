#!/usr/bin/env python3
"""
example_test.py — a sample user-test flow driven through the Pico.

Opens Safari on the target Mac via Spotlight, goes to a URL, and clicks.
Copy this and adapt it for your own tests.
"""

from pico_client import Pico

URL = "https://example.com"

with Pico() as p:
    p.ping()

    # Open Safari through Spotlight
    p.hotkey("cmd", "space")
    p.wait(0.6)
    p.type("Safari")
    p.wait(0.4)
    p.hotkey("enter")
    p.wait(2.0)

    # New tab, go to the URL
    p.hotkey("cmd", "t")
    p.wait(0.5)
    p.hotkey("cmd", "l")
    p.type(URL)
    p.hotkey("enter")
    p.wait(3.0)

    # Click somewhere on the page (macOS points), then scroll down
    p.click(756, 400)
    p.wait(0.5)
    p.scroll(-10)

    print("Done.")
