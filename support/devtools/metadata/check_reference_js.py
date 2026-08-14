#!/usr/bin/env python3
"""Check the ES5.1 JavaScript API contract fixtures."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "tests" / "fixtures"
POSITIVE = FIXTURES / "api-positive.js"
NEGATIVE = FIXTURES / "api-negative.js"
REQUIRED = (
    "require('fs')",
    "readdirSync",
    "unlinkSync",
    "mkdirSync",
    "rmdirSync",
    "require('native/fs')",
    "require('movian/settings')",
    "require('movian/page')",
    "Plugin",
    "Core",
    "Duktape",
)


def check_node(path: Path, expect_success: bool) -> str | None:
    result = subprocess.run(
        ["node", "--check", str(path)],
        cwd=ROOT.parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if (result.returncode == 0) != expect_success:
        return "%s: expected node --check %s, got %d\n%s" % (
            path.relative_to(ROOT.parents[2]),
            "success" if expect_success else "failure",
            result.returncode,
            result.stdout.rstrip(),
        )
    return None


def main() -> int:
    errors = []
    source = POSITIVE.read_text(encoding="utf-8")
    errors.extend(
        "%s: required API token missing" % token
        for token in REQUIRED if token not in source
    )
    for forbidden in ("let ", "const ", "=>"):
        if forbidden in source:
            errors.append("%s: ES5.1 fixture contains %r" % (POSITIVE, forbidden))
    error = check_node(POSITIVE, True)
    if error:
        errors.append(error)
    error = check_node(NEGATIVE, False)
    if error:
        errors.append(error)
    if errors:
        for error in errors:
            print("reference-js: " + error, file=sys.stderr)
        return 1
    print("reference-js: positive ES5.1 fixture OK")
    print("reference-js: intentional invalid fixture rejected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
