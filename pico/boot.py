# boot.py v2 - runs once at power-up, before USB connects to the target Mac.
#
# Sets up the USB devices the target sees:
#   1. Keyboard  2. Relative mouse  3. Absolute pointer (report ID 4, 0..32767)
#
# STEALTH MODE hides the CIRCUITPY drive + serial from the target so it looks
# like a plain keyboard/mouse. Enable it EITHER way:
#   * Hardware: jumper pin 28 (GND) to pin 29 (GP22) before plugging in, OR
#   * Software: set PICO_STEALTH = "true" in settings.toml.
# In stealth the filesystem is remounted writable to CODE, so the running
# program can persist /profile.json (pushed over WiFi). Pull the jumper (and
# clear the flag) to get the drive/serial back for editing firmware.

import os

import board
import digitalio
import storage
import usb_cdc
import usb_hid

# Absolute mouse: 5 buttons, X/Y 0..32767 (16-bit), vertical wheel. Report ID 4.
ABS_MOUSE_DESCRIPTOR = bytes((
    0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x85, 0x04,
    0x09, 0x01, 0xA1, 0x00,
    0x05, 0x09, 0x19, 0x01, 0x29, 0x05, 0x15, 0x00, 0x25, 0x01,
    0x95, 0x05, 0x75, 0x01, 0x81, 0x02,
    0x95, 0x01, 0x75, 0x03, 0x81, 0x01,
    0x05, 0x01, 0x09, 0x30, 0x09, 0x31, 0x15, 0x00, 0x26, 0xFF, 0x7F,
    0x75, 0x10, 0x95, 0x02, 0x81, 0x02,
    0x09, 0x38, 0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x01, 0x81, 0x06,
    0xC0, 0xC0,
))

abs_mouse = usb_hid.Device(
    report_descriptor=ABS_MOUSE_DESCRIPTOR,
    usage_page=0x01, usage=0x02, report_ids=(4,),
    in_report_lengths=(6,), out_report_lengths=(0,),
)

# Keyboard stays present always; it's silenced per-session in software.
usb_hid.enable((usb_hid.Device.KEYBOARD, usb_hid.Device.MOUSE, abs_mouse))

# ---- Optional USB identity disguise (set in settings.toml) ----
# Makes the device read like an ordinary mouse in the host's USB list instead of
# "Pico 2 W" / "Raspberry Pi". Spoofing VID/PID can re-trigger the macOS Keyboard
# Setup Assistant on first plug; leave them unset unless you need them.
_mfr = os.getenv("PICO_USB_MANUFACTURER")
_prod = os.getenv("PICO_USB_PRODUCT")
if _mfr or _prod:
    try:
        import supervisor
        kw = {}
        if _mfr:
            kw["manufacturer"] = _mfr
        if _prod:
            kw["product"] = _prod
        _vid = os.getenv("PICO_USB_VID")
        _pid = os.getenv("PICO_USB_PID")
        if _vid:
            kw["vid"] = int(_vid, 0)
        if _pid:
            kw["pid"] = int(_pid, 0)
        supervisor.set_usb_identification(**kw)
    except Exception:
        pass

# Decide stealth: hardware jumper OR software flag.
jumper = digitalio.DigitalInOut(board.GP22)
jumper.switch_to_input(pull=digitalio.Pull.UP)
jumper_stealth = not jumper.value
jumper.deinit()

flag = (os.getenv("PICO_STEALTH") or "").strip().lower()
flag_stealth = flag in ("1", "true", "yes", "on")

# ---- Optional drive rename (non-stealth only; stealth hides the drive entirely) ----
# FAT label: up to 11 chars, letters/numbers, stored uppercase.
_label = os.getenv("PICO_DRIVE_LABEL")
if _label and not (jumper_stealth or flag_stealth):
    try:
        storage.remount("/", readonly=False)          # code can write the label
        storage.getmount("/").label = _label.strip().upper()[:11]
        storage.remount("/", readonly=True)           # restore host-writable drive
    except Exception:
        pass

if jumper_stealth or flag_stealth:
    storage.disable_usb_drive()
    usb_cdc.disable()
    try:
        # Let code.py write /profile.json (host no longer owns the FS).
        storage.remount("/", readonly=False)
    except RuntimeError:
        pass
