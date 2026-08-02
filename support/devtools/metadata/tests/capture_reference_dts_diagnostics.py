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
    source_diagnostics: tuple[str, ...] = ()
    typescript_diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        evidence_kinds = sum(bool(diagnostics) for diagnostics in (
            self.source_diagnostics, self.typescript_diagnostics))
        if evidence_kinds != 1:
            raise ValueError(
                "%s: declare exactly one diagnostic evidence kind" % self.name)


def mutation(relative: str, old: str, new: str) -> Mutation:
    return Mutation(ROOT / relative, old, new)


CASES = (
    Case("xml nested missing+phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/movian-xml.d.ts",
            "        dump(): void;",
            "        dumpRenamed(): void;"),
    ), source_diagnostics=(
        "reference-dts: movian/xml: XmlProxy.dump missing declaration",
        "reference-dts: movian/xml: phantom declaration XmlProxy.dumpRenamed",
    )),
    Case("itemhook returned object missing+phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/movian-itemhook.d.ts",
            "        destroy(): void;",
            "        destroyRenamed(): void;"),
    ), source_diagnostics=(
        "reference-dts: movian/itemhook: ItemHookHandle.destroy missing "
        "declaration",
        "reference-dts: movian/itemhook: phantom declaration "
        "ItemHookHandle.destroyRenamed",
    )),
    Case("itemhook local object literal missing+phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/movian-itemhook.d.ts",
            "        openURL(url: string): void;",
            "        openURLRenamed(url: string): void;"),
    ), source_diagnostics=(
        "reference-dts: movian/itemhook: NavigationObject.openURL missing "
        "declaration",
        "reference-dts: movian/itemhook: phantom declaration "
        "NavigationObject.openURLRenamed",
    )),
    Case("itemhook consumed options missing+phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/movian-itemhook.d.ts",
            "        title: string;",
            "        titleRenamed: string;"),
    ), source_diagnostics=(
        "reference-dts: movian/itemhook: ItemHookConfig.title missing "
        "declaration",
        "reference-dts: movian/itemhook: phantom declaration "
        "ItemHookConfig.titleRenamed",
    )),
    Case("subtitles local object missing+phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/movian-subtitles.d.ts",
            "        addSubtitle(\n",
            "        addSubtitleRenamed(\n"),
    ), source_diagnostics=(
        "reference-dts: movian/subtitles: SubtitleRequest.addSubtitle "
        "missing declaration",
        "reference-dts: movian/subtitles: phantom declaration "
        "SubtitleRequest.addSubtitleRenamed",
    )),
    Case("subtitles inherited native missing+phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/movian-subtitles.d.ts",
            "        readonly title?: string;",
            "        readonly titleRenamed?: string;"),
    ), source_diagnostics=(
        "reference-dts: movian/subtitles: SubtitleRequest.title missing "
        "declaration",
        "reference-dts: movian/subtitles: phantom declaration "
        "SubtitleRequest.titleRenamed",
    )),
    Case("videoscrobbler prototype phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/"
            "movian-videoscrobbler.d.ts",
            "        destroy(): void;",
            "        destroy(): void;\n\n"
            "        zzPhantomMethod(): void;"),
    ), source_diagnostics=(
        "reference-dts: movian/videoscrobbler: phantom declaration "
        "VideoScrobbler.zzPhantomMethod",
    )),
    Case("settings shared object missing+phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/"
            "movian-settings.d.ts",
            "        dump(): void;",
            "        dumpRenamed(): void;"),
    ), source_diagnostics=(
        "reference-dts: movian/settings: SettingsMethods.dump missing "
        "declaration",
        "reference-dts: movian/settings: phantom declaration "
        "SettingsMethods.dumpRenamed",
    )),
    Case("settings exported instance missing+phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/"
            "movian-settings.d.ts",
            "        readonly id: string;",
            "        readonly idRenamed: string;"),
    ), source_diagnostics=(
        "reference-dts: movian/settings: globalSettings.id missing "
        "declaration",
        "reference-dts: movian/settings: phantom declaration "
        "globalSettings.idRenamed",
    )),
    Case("html object constructor missing+phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/movian-html.d.ts",
            "        readonly nodeName: string;",
            "        readonly nodeNameRenamed: string;"),
    ), source_diagnostics=(
        "reference-dts: movian/html: Node.nodeName missing declaration",
        "reference-dts: movian/html: phantom declaration Node.nodeNameRenamed",
    )),
    Case("videoscrobbler instance references missing+phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/"
            "movian-videoscrobbler.d.ts",
            "        onpause?: (data: ScrobbleData, prop: Property, "
            "origin: Property) => void;",
            "        onpauseRenamed?: (data: ScrobbleData, prop: Property, "
            "origin: Property) => void;"),
    ), source_diagnostics=(
        "reference-dts: movian/videoscrobbler: VideoScrobbler.onpause "
        "missing declaration",
        "reference-dts: movian/videoscrobbler: phantom declaration "
        "VideoScrobbler.onpauseRenamed",
    )),
    Case("url native returned object missing+phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/url.d.ts",
            "    interface ParsedUrl {\n        protocol: string;",
            "    interface ParsedUrl {\n        protocolRenamed: string;"),
    ), source_diagnostics=(
        "reference-dts: url: ParsedUrl.protocol missing declaration",
        "reference-dts: url: phantom declaration ParsedUrl.protocolRenamed",
    )),
    Case("http request constructor missing+phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/http.d.ts",
            "        end(): void;",
            "        endRenamed(): void;"),
    ), source_diagnostics=(
        "reference-dts: http: HttpRequest.end missing declaration",
        "reference-dts: http: phantom declaration HttpRequest.endRenamed",
    )),
    Case("http response constructor missing+phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/http.d.ts",
            "        setEncoding(enc: string): void;",
            "        setEncodingRenamed(enc: string): void;"),
    ), source_diagnostics=(
        "reference-dts: http: HttpResponse.setEncoding missing declaration",
        "reference-dts: http: phantom declaration "
        "HttpResponse.setEncodingRenamed",
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
    ), source_diagnostics=(
        "reference-dts: movian/sqlite: DB source constructor must declare "
        "an exported class",
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
    ), source_diagnostics=(
        "reference-dts: movian/popup: requires native/string, which is not "
        "among its declared dependencies (native/popup)",
        "reference-dts: movian/popup: notify alias target is "
        "native/string.parseURL vs required native/popup.notify",
    )),
    Case("fs payload loosened", (
        mutation(
            "support/devtools/metadata/tests/reference/fs.d.ts",
            "        data: string | DuktapeBuffer,",
            "        data: unknown,"),
    ), typescript_diagnostics=(
        "reference-dts: negative fixture missing diagnostics: "
        "[('reference-negative.ts', 175, 2345)]",
    )),
    Case("http options object loosened", (
        mutation(
            "support/devtools/metadata/tests/reference/http.d.ts",
            "    export function request(\n"
            "        opts: string | RequestOptions,",
            "    export function request(\n"
            "        opts: unknown,"),
    ), typescript_diagnostics=(
        "reference-dts: negative fixture missing diagnostics: "
        "[('reference-negative.ts', 136, 2345), "
        "('reference-negative.ts', 184, 2353)]",
    )),
    Case("https options object loosened", (
        mutation(
            "support/devtools/metadata/tests/reference/https.d.ts",
            "    export function request(\n"
            "        opts: string | RequestOptions,",
            "    export function request(\n"
            "        opts: unknown,"),
    ), typescript_diagnostics=(
        "reference-dts: negative fixture missing diagnostics: "
        "[('reference-negative.ts', 139, 2345)]",
    )),
    Case("http response callback loosened", (
        mutation(
            "support/devtools/metadata/tests/reference/http.d.ts",
            "            callback: (response: HttpResponse) => void",
            "            callback: (response: unknown) => void"),
    ), typescript_diagnostics=(
        "support/devtools/metadata/tests/fixtures/reference-positive.ts"
        "(355,28): error TS18046: 'response' is of type 'unknown'.",
    )),
    Case("http error callback loosened", (
        mutation(
            "support/devtools/metadata/tests/reference/http.d.ts",
            "        on(event: 'error', callback: (error: string) => void): void;",
            "        on(event: 'error', callback: (error: unknown) => void): void;"),
    ), typescript_diagnostics=(
        "support/devtools/metadata/tests/fixtures/reference-positive.ts"
        "(367,31): error TS18046: 'error' is of type 'unknown'.",
    )),
    Case("http data callback loosened", (
        mutation(
            "support/devtools/metadata/tests/reference/http.d.ts",
            "        on(event: 'data', callback: (chunk: string) => void): void;",
            "        on(event: 'data', callback: (chunk: unknown) => void): void;"),
    ), typescript_diagnostics=(
        "support/devtools/metadata/tests/fixtures/reference-positive.ts"
        "(358,15): error TS2322: Type 'unknown' is not assignable to type "
        "'string'.",
    )),
    Case("websocket prototype phantom", (
        mutation(
            "support/devtools/metadata/tests/reference/websocket.d.ts",
            "        close(d?: unknown): void;",
            "        close(d?: unknown): void;\n\n"
            "        zzPhantomMethod(): void;"),
    ), source_diagnostics=(
        "reference-dts: websocket: phantom declaration "
        "w3cwebsocket.zzPhantomMethod",
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
            kind, expected = (
                ("source-shape", case.source_diagnostics)
                if case.source_diagnostics else
                ("TypeScript", case.typescript_diagnostics)
            )
            output_lines = set(result.stdout.splitlines())
            missing = tuple(
                diagnostic for diagnostic in expected
                if diagnostic not in output_lines)
            if missing:
                raise RuntimeError(
                    "%s: missing expected %s diagnostic(s): %s" %
                    (case.name, kind, "; ".join(missing)))
            if result.returncode == 0:
                raise RuntimeError(
                    "%s: checker emitted the expected %s diagnostic(s) "
                    "but exited successfully" % (case.name, kind))
        finally:
            for path, text in originals.items():
                path.write_text(text, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
