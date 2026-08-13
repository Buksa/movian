#!/usr/bin/env python3
"""Send pointer gestures to a named X11 window via XTest."""

import argparse
import ctypes
import ctypes.util
import os
import sys
import time


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
    x11.XDefaultScreen.argtypes = [ctypes.c_void_p]
    x11.XDefaultScreen.restype = ctypes.c_int
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
    x11.XGetGeometry.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(ctypes.c_uint),
    ]
    x11.XGetGeometry.restype = ctypes.c_int
    x11.XTranslateCoordinates.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_ulong),
    ]
    x11.XTranslateCoordinates.restype = ctypes.c_int
    x11.XRaiseWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XSetInputFocus.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_ulong,
    ]
    x11.XFlush.argtypes = [ctypes.c_void_p]
    xtst.XTestFakeMotionEvent.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_ulong,
    ]
    xtst.XTestFakeMotionEvent.restype = ctypes.c_int
    xtst.XTestFakeButtonEvent.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_ulong,
    ]
    xtst.XTestFakeButtonEvent.restype = ctypes.c_int
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


def window_geometry(x11, display, root, win):
    root_ret = ctypes.c_ulong()
    x = ctypes.c_int()
    y = ctypes.c_int()
    width = ctypes.c_uint()
    height = ctypes.c_uint()
    border = ctypes.c_uint()
    depth = ctypes.c_uint()
    if not x11.XGetGeometry(
        display,
        win,
        ctypes.byref(root_ret),
        ctypes.byref(x),
        ctypes.byref(y),
        ctypes.byref(width),
        ctypes.byref(height),
        ctypes.byref(border),
        ctypes.byref(depth),
    ):
        raise RuntimeError("Unable to read window geometry")

    root_x = ctypes.c_int()
    root_y = ctypes.c_int()
    child = ctypes.c_ulong()
    if not x11.XTranslateCoordinates(
        display,
        win,
        root,
        0,
        0,
        ctypes.byref(root_x),
        ctypes.byref(root_y),
        ctypes.byref(child),
    ):
        raise RuntimeError("Unable to translate window coordinates")

    return root_x.value, root_y.value, width.value, height.value


def absolute_point(origin_x, origin_y, x, y):
    return origin_x + x, origin_y + y


def move_pointer(xtst, display, screen, x, y):
    if not xtst.XTestFakeMotionEvent(display, screen, x, y, 0):
        raise RuntimeError("XTestFakeMotionEvent failed")


def click_button(xtst, display, button, pressed):
    if not xtst.XTestFakeButtonEvent(display, button, pressed, 0):
        raise RuntimeError("XTestFakeButtonEvent failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", default="Movian")
    parser.add_argument("--display", default=os.environ.get("DISPLAY", ":0"))
    parser.add_argument("--delay", type=float, default=0.1)
    subparsers = parser.add_subparsers(dest="command", required=True)

    move = subparsers.add_parser("move")
    move.add_argument("--x", type=int, required=True)
    move.add_argument("--y", type=int, required=True)

    drag = subparsers.add_parser("drag")
    drag.add_argument("--from-x", type=int, required=True)
    drag.add_argument("--from-y", type=int, required=True)
    drag.add_argument("--to-x", type=int, required=True)
    drag.add_argument("--to-y", type=int, required=True)
    drag.add_argument("--duration", type=float, default=0.25)
    drag.add_argument("--steps", type=int, default=8)
    drag.add_argument("--button", type=int, default=1)

    wheel = subparsers.add_parser("wheel")
    wheel.add_argument("--direction", choices=("up", "down"), required=True)
    wheel.add_argument("--count", type=int, default=1)
    wheel.add_argument("--x", type=int)
    wheel.add_argument("--y", type=int)
    wheel.add_argument("--interval", type=float, default=0.08)

    args = parser.parse_args()
    if args.command == "drag":
        if args.steps < 1:
            parser.error("--steps must be at least 1")
        if args.duration < 0:
            parser.error("--duration cannot be negative")
    if args.command == "wheel":
        if args.count < 1:
            parser.error("--count must be at least 1")
        if (args.x is None) != (args.y is None):
            parser.error("--x and --y must be provided together")

    x11, xtst = load_x11()
    display = x11.XOpenDisplay(args.display.encode())
    if not display:
        raise SystemExit("Unable to open X display " + args.display)

    root = x11.XDefaultRootWindow(display)
    screen = x11.XDefaultScreen(display)
    win = find_window(x11, display, root, args.window)
    if not win:
        raise SystemExit("Window not found: " + args.window)

    origin_x, origin_y, width, height = window_geometry(
        x11, display, root, win
    )
    x11.XRaiseWindow(display, win)
    x11.XSetInputFocus(display, win, 1, 0)
    x11.XFlush(display)
    time.sleep(args.delay)

    if args.command == "move":
        x, y = absolute_point(origin_x, origin_y, args.x, args.y)
        move_pointer(xtst, display, screen, x, y)

    elif args.command == "drag":
        start_x, start_y = absolute_point(
            origin_x, origin_y, args.from_x, args.from_y
        )
        end_x, end_y = absolute_point(
            origin_x, origin_y, args.to_x, args.to_y
        )
        move_pointer(xtst, display, screen, start_x, start_y)
        x11.XFlush(display)
        click_button(xtst, display, args.button, True)
        for step in range(1, args.steps + 1):
            ratio = step / args.steps
            x = round(start_x + (end_x - start_x) * ratio)
            y = round(start_y + (end_y - start_y) * ratio)
            move_pointer(xtst, display, screen, x, y)
            x11.XFlush(display)
            if args.duration:
                time.sleep(args.duration / args.steps)
        click_button(xtst, display, args.button, False)

    else:
        x = args.x if args.x is not None else width // 2
        y = args.y if args.y is not None else height // 2
        abs_x, abs_y = absolute_point(origin_x, origin_y, x, y)
        move_pointer(xtst, display, screen, abs_x, abs_y)
        button = 4 if args.direction == "up" else 5
        for _ in range(args.count):
            click_button(xtst, display, button, True)
            click_button(xtst, display, button, False)
            x11.XFlush(display)
            time.sleep(args.interval)

    x11.XFlush(display)
    time.sleep(args.delay)
    return 0


if __name__ == "__main__":
    sys.exit(main())
