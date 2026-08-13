#!/usr/bin/env python3
"""Stdlib STPP JSON/binary WebSocket probe for Movian."""

import argparse
import base64
import json
import os
import socket
import struct
import sys


def send_frame(sock, opcode, data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    mask = os.urandom(4)
    header = bytearray([0x80 | opcode])
    n = len(data)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", n))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", n))
    header.extend(mask)
    payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(data))
    sock.sendall(header + payload)


def send_text(sock, text):
    send_frame(sock, 1, text)


def send_binary(sock, data):
    send_frame(sock, 2, data)


def close(sock):
    try:
        send_frame(sock, 8, b"")
    except OSError:
        pass
    sock.close()


def recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise EOFError("socket closed")
        data += chunk
    return data


def recv_frame(sock):
    b1, b2 = recv_exact(sock, 2)
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack("!H", recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", recv_exact(sock, 8))[0]
    mask = recv_exact(sock, 4) if (b2 & 0x80) else b""
    data = recv_exact(sock, length)
    if mask:
        data = bytes(byte ^ mask[i % 4] for i, byte in enumerate(data))
    return b1 & 0x0F, data


def connect(host, port, path="/api/stpp"):
    sock = socket.create_connection((host, port), timeout=5)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        "GET %s HTTP/1.1\r\n"
        "Host: %s:%d\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: %s\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    ) % (path, host, port, key)
    sock.sendall(request.encode("ascii"))
    response = b""
    while b"\r\n\r\n" not in response:
        response += sock.recv(4096)
    status = response.split(b"\r\n", 1)[0].decode("ascii", "replace")
    if " 101 " not in status:
        raise RuntimeError("WebSocket handshake failed: " + status)
    return sock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--binary-hello",
        action="store_true",
        help="Send the required binary STPP v3 HELLO and print the reply.",
    )
    parser.add_argument(
        "--strict-json",
        action="store_true",
        help="Fail if a received text frame is not valid JSON.",
    )
    parser.add_argument(
        "--set",
        dest="sets",
        action="append",
        default=[],
        metavar="PATH=VALUE",
        help="Set a string value through JSON STPP before subscribing; may repeat.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Dot-separated STPP paths, e.g. navigators.current.currentpage.model.metadata.title",
    )
    args = parser.parse_args()

    set_pairs = []
    for item in args.sets:
        if "=" not in item:
            parser.error("--set expects PATH=VALUE")
        path, value = item.split("=", 1)
        if not path:
            parser.error("--set path cannot be empty")
        set_pairs.append((path, value))

    watch_paths = []
    for path in args.paths + [path for path, _value in set_pairs]:
        if path not in watch_paths:
            watch_paths.append(path)

    if not watch_paths and not set_pairs and not args.binary_hello:
        parser.error("provide at least one path or --set PATH=VALUE")

    sock = connect(args.host, args.port)
    sock.settimeout(args.timeout)

    if args.binary_hello:
        try:
            send_binary(sock, bytes((0, 3, 0)))
            opcode, data = recv_frame(sock)
            result = {
                "opcode": opcode,
                "length": len(data),
                "command": data[0] if data else None,
                "version": data[1] if len(data) > 1 else None,
                "running_instance": data[2:18].hex() if len(data) >= 18 else None,
                "flags": data[18] if len(data) > 18 else None,
            }
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            valid = opcode == 2 and len(data) == 19 and data[:2] == b"\x00\x03"
            return 0 if valid else 1
        finally:
            close(sock)

    for path, value in set_pairs:
        send_text(sock, json.dumps([3, 0, path, value]))

    for sid, path in enumerate(watch_paths, 1):
        send_text(sock, json.dumps([1, sid, 0, path]))

    seen = set()
    invalid_json = 0
    try:
        while True:
            if len(seen) == len(watch_paths):
                if not args.strict_json:
                    break
                sock.settimeout(min(0.25, args.timeout))
            opcode, data = recv_frame(sock)
            if opcode == 9:
                send_frame(sock, 10, data)
                continue
            if opcode != 1:
                continue
            text = data.decode("utf-8", "replace")
            print(text)
            try:
                msg = json.loads(text)
            except ValueError:
                invalid_json += 1
                continue
            if isinstance(msg, list) and len(msg) >= 2 and msg[0] == 4:
                seen.add(msg[1])
    except socket.timeout:
        pass
    finally:
        for sid in range(1, len(watch_paths) + 1):
            send_text(sock, json.dumps([2, sid]))
        close(sock)

    if args.strict_json and invalid_json:
        return 1
    return 0 if len(seen) == len(watch_paths) else 1


if __name__ == "__main__":
    sys.exit(main())
