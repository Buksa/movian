#!/usr/bin/env python3
"""Send real X11 keypresses to the Movian window via XTest."""

import argparse
import os
import sys
import time

try:
    from . import x11_common
except ImportError:  # run as a plain script, not as mdevlib.x11_keypress
    import x11_common


KEYSYMS = {
    "Top": 0xFF50,
    "Home": 0xFF50,
    "Bottom": 0xFF57,
    "End": 0xFF57,
    "Up": 0xFF52,
    "Down": 0xFF54,
    "Left": 0xFF51,
    "Right": 0xFF53,
    "Activate": 0xFF0D,
    "Enter": 0xFF0D,
    "Return": 0xFF0D,
    "Back": 0xFF1B,
    "Escape": 0xFF1B,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("keys", nargs="+", help="Key names, e.g. Top Down Down Activate")
    parser.add_argument("--window", default="Movian")
    parser.add_argument("--display", default=os.environ.get("DISPLAY", ":0"))
    parser.add_argument("--delay", type=float, default=0.35)
    args = parser.parse_args()

    x11, xtst = x11_common.load_x11()
    display = x11.XOpenDisplay(args.display.encode())
    if not display:
        raise SystemExit("Unable to open X display " + args.display)

    root = x11.XDefaultRootWindow(display)
    win = x11_common.find_window(x11, display, root, args.window)
    if not win:
        raise SystemExit("Window not found: " + args.window)

    x11.XRaiseWindow(display, win)
    x11.XSetInputFocus(display, win, 1, 0)
    x11.XFlush(display)
    time.sleep(args.delay)

    for key in args.keys:
        if key not in KEYSYMS:
            raise SystemExit("Unknown key: " + key)
        keycode = x11.XKeysymToKeycode(display, KEYSYMS[key])
        if not keycode:
            raise SystemExit("No X keycode for: " + key)
        xtst.XTestFakeKeyEvent(display, keycode, 1, 0)
        xtst.XTestFakeKeyEvent(display, keycode, 0, 0)
        x11.XFlush(display)
        time.sleep(args.delay)

    return 0


if __name__ == "__main__":
    sys.exit(main())
