#!/usr/bin/env python3
"""Capture the reference-dts checker's accepted falsification diagnostics."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
CHECKER = ROOT / "support/devtools/metadata/check_reference_dts.py"


@dataclass(frozen=True)
class Mutation:
    path: Path
    old: str
    new: str


@dataclass(frozen=True)
class Case:
    name: str
    mutations: tuple[Mutation, ...]


def mutation(relative: str, old: str, new: str) -> Mutation:
    return Mutation(ROOT / relative, old, new)


CASES = (
    Case("xml nested missing+phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/movian-xml.d.ts",
            "        dump(): void;",
            "        dumpRenamed(): void;"),
    )),
    Case("itemhook returned object missing+phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/movian-itemhook.d.ts",
            "        destroy(): void;",
            "        destroyRenamed(): void;"),
    )),
    Case("itemhook local object literal missing+phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/movian-itemhook.d.ts",
            "        openURL(url: string): void;",
            "        openURLRenamed(url: string): void;"),
    )),
    Case("itemhook consumed options missing+phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/movian-itemhook.d.ts",
            "        title: string;",
            "        titleRenamed: string;"),
    )),
    Case("subtitles local object missing+phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/movian-subtitles.d.ts",
            "        addSubtitle(\n",
            "        addSubtitleRenamed(\n"),
    )),
    Case("subtitles inherited native missing+phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/movian-subtitles.d.ts",
            "        readonly title?: string;",
            "        readonly titleRenamed?: string;"),
    )),
    Case("videoscrobbler prototype phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/"
            "movian-videoscrobbler.d.ts",
            "        destroy(): void;",
            "        destroy(): void;\n\n"
            "        zzPhantomMethod(): void;"),
    )),
    Case("http request constructor missing+phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/http.d.ts",
            "        end(): void;",
            "        endRenamed(): void;"),
    )),
    Case("http response constructor missing+phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/http.d.ts",
            "        setEncoding(enc: string): void;",
            "        setEncodingRenamed(enc: string): void;"),
    )),
    Case("sqlite constructor changed from class to function", (
        mutation(
            "support/devtools/metadata/tests/reference/movian-sqlite.d.ts",
            "    export class DB {\n",
            "    export function DB(dbname: string): DB;\n\n"
            "    interface DB {\n"),
        mutation(
            "support/devtools/metadata/tests/reference/movian-sqlite.d.ts",
            "        constructor(dbname: string);\n",
            ""),
    )),
    Case("alias repointed to existing wrong target", (
        mutation(
            "res/ecmascript/modules/movian/popup.js",
            "var popup = require('native/popup');",
            "var popup = require('native/string');"),
        mutation(
            "res/ecmascript/modules/movian/popup.js",
            "exports.notify = popup.notify;",
            "exports.notify = popup.parseURL;"),
    )),
    Case("fs payload loosened", (
        mutation(
            "support/devtools/metadata/tests/reference/fs.d.ts",
            "        data: string | DuktapeBuffer,",
            "        data: unknown,"),
    )),
    Case("http options object loosened", (
        mutation(
            "support/devtools/metadata/tests/reference/http.d.ts",
            "    export function request(\n"
            "        opts: string | RequestOptions,",
            "    export function request(\n"
            "        opts: unknown,"),
    )),
    Case("https options object loosened", (
        mutation(
            "support/devtools/metadata/tests/reference/https.d.ts",
            "    export function request(\n"
            "        opts: string | RequestOptions,",
            "    export function request(\n"
            "        opts: unknown,"),
    )),
    Case("http response callback loosened", (
        mutation(
            "support/devtools/metadata/tests/reference/http.d.ts",
            "            callback: (response: HttpResponse) => void",
            "            callback: (response: unknown) => void"),
    )),
    Case("http error callback loosened", (
        mutation(
            "support/devtools/metadata/tests/reference/http.d.ts",
            "        on(event: 'error', callback: (error: string) => void): void;",
            "        on(event: 'error', callback: (error: unknown) => void): void;"),
    )),
    Case("http data callback loosened", (
        mutation(
            "support/devtools/metadata/tests/reference/http.d.ts",
            "        on(event: 'data', callback: (chunk: string) => void): void;",
            "        on(event: 'data', callback: (chunk: unknown) => void): void;"),
    )),
    Case("websocket prototype phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/websocket.d.ts",
            "        close(d?: unknown): void;",
            "        close(d?: unknown): void;\n\n"
            "        zzPhantomMethod(): void;"),
    )),
)


def run_checker(arguments: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (sys.executable, str(CHECKER), *arguments),
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )


def print_result(name: str, result: subprocess.CompletedProcess[str]) -> None:
    print("=== %s (exit %d) ===" % (name, result.returncode))
    print(result.stdout, end="")


def apply_case(case: Case, originals: dict[Path, str]) -> None:
    for item in case.mutations:
        if item.path not in originals:
            originals[item.path] = item.path.read_text(encoding="utf-8")
        text = item.path.read_text(encoding="utf-8")
        if text.count(item.old) != 1:
            raise RuntimeError(
                "%s: expected one occurrence in %s, found %d" %
                (case.name, item.path.relative_to(ROOT), text.count(item.old)))
        item.path.write_text(text.replace(item.old, item.new, 1),
                             encoding="utf-8")


def main() -> int:
    clean = run_checker()
    print_result("clean default", clean)
    commonjs = run_checker(("--commonjs",))
    print_result("clean commonjs", commonjs)
    if clean.returncode or commonjs.returncode:
        return 1

    for case in CASES:
        originals: dict[Path, str] = {}
        try:
            apply_case(case, originals)
            result = run_checker()
            print_result(case.name, result)
            if result.returncode == 0:
                raise RuntimeError("%s did not falsify the checker" % case.name)
        finally:
            for path, text in originals.items():
                path.write_text(text, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
