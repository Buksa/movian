#!/usr/bin/env python3
"""Send real X11 keypresses to the Movian window via XTest."""

import argparse
import ctypes
import ctypes.util
import os
import sys
import time


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


def load_x11():
    x11_path = ctypes.util.find_library("X11")
    xtst_path = ctypes.util.find_library("Xtst")
    if not x11_path or not xtst_path:
        raise RuntimeError("libX11/libXtst are required")

    x11 = ctypes.CDLL(x11_path)
    xtst = ctypes.CDLL(xtst_path)

    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XQueryTree.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
        ctypes.POINTER(ctypes.c_uint),
    ]
    x11.XQueryTree.restype = ctypes.c_int
    x11.XFetchName.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_char_p),
    ]
    x11.XFetchName.restype = ctypes.c_int
    x11.XFree.argtypes = [ctypes.c_void_p]
    x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XKeysymToKeycode.restype = ctypes.c_uint
    x11.XSetInputFocus.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_ulong,
    ]
    x11.XRaiseWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XFlush.argtypes = [ctypes.c_void_p]
    xtst.XTestFakeKeyEvent.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_ulong,
    ]
    return x11, xtst


def window_name(x11, display, win):
    raw = ctypes.c_char_p()
    if x11.XFetchName(display, win, ctypes.byref(raw)) and raw.value:
        value = raw.value.decode(errors="replace")
        x11.XFree(raw)
        return value
    return ""


def child_windows(x11, display, win):
    root_ret = ctypes.c_ulong()
    parent_ret = ctypes.c_ulong()
    children = ctypes.POINTER(ctypes.c_ulong)()
    count = ctypes.c_uint()
    ok = x11.XQueryTree(
        display,
        win,
        ctypes.byref(root_ret),
        ctypes.byref(parent_ret),
        ctypes.byref(children),
        ctypes.byref(count),
    )
    if not ok:
        return []
    try:
        return [children[i] for i in range(count.value)] if children else []
    finally:
        if children:
            x11.XFree(children)


def find_window(x11, display, root, target):
    queue = [root]
    while queue:
        win = queue.pop(0)
        if window_name(x11, display, win) == target:
            return win
        queue.extend(child_windows(x11, display, win))
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("keys", nargs="+", help="Key names, e.g. Top Down Down Activate")
    parser.add_argument("--window", default="Movian")
    parser.add_argument("--display", default=os.environ.get("DISPLAY", ":0"))
    parser.add_argument("--delay", type=float, default=0.35)
    args = parser.parse_args()

    x11, xtst = load_x11()
    display = x11.XOpenDisplay(args.display.encode())
    if not display:
        raise SystemExit("Unable to open X display " + args.display)

    root = x11.XDefaultRootWindow(display)
    win = find_window(x11, display, root, args.window)
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
