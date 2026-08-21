#!/usr/bin/env python3
"""Behavior tests for smoke discovery outside the core repo (movian#164).

Build-free on purpose. Everything here is path resolution and JSON loading,
so it runs in CI beside the metadata gate; its sibling
`test_http_401_inspector.py` drives a real Movian and cannot.

What these pin is mostly what discovery must NOT do. A plugin repository
could not ship a regression smoke at all: the search was one hardcoded
directory inside the core, and a relative `needs.plugin` resolved against the
core's root — so an author had to write JSON into somebody else's checkout
and name their own plugin by absolute path. Neither survives a clone.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from support.devtools.mdevlib import harness, smoke  # noqa: E402


def write_smoke(directory: Path, name: str, plugin: str | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("%s.json" % name)
    path.write_text(json.dumps({
        "name": name,
        "describe": "fixture",
        "needs": {"binary": "build.debug/movian", "plugin": plugin},
        "steps": [{"do": "health"}],
    }), encoding="utf-8")
    return path


class SearchPath(unittest.TestCase):
    def test_the_core_directory_is_always_first(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(smoke.SMOKES_PATH_ENV, None)
            self.assertEqual(smoke.smoke_search_path()[0],
                             smoke.CORE_SMOKES_DIR.resolve())

    def test_the_environment_variable_adds_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a", Path(tmp) / "b"
            a.mkdir(); b.mkdir()
            with mock.patch.dict(
                    os.environ,
                    {smoke.SMOKES_PATH_ENV: os.pathsep.join([str(a), str(b)])}):
                path = smoke.smoke_search_path()
            self.assertIn(a.resolve(), path)
            self.assertIn(b.resolve(), path)

    def test_the_current_repo_is_searched_without_configuration(self) -> None:
        """The point of the whole change: no env var, no flag, no core edit."""
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / smoke.LOCAL_SMOKES_SUBDIR
            local.mkdir(parents=True)
            with mock.patch.object(Path, "cwd", staticmethod(lambda: Path(tmp))):
                self.assertIn(local.resolve(), smoke.smoke_search_path())

    def test_a_directory_named_twice_is_searched_once(self) -> None:
        with mock.patch.dict(
                os.environ,
                {smoke.SMOKES_PATH_ENV: os.pathsep.join(
                    [str(smoke.CORE_SMOKES_DIR)] * 2)}):
            path = smoke.smoke_search_path()
        self.assertEqual(path.count(smoke.CORE_SMOKES_DIR.resolve()), 1)


class RelativePluginPaths(unittest.TestCase):
    def test_a_plugin_smoke_resolves_against_its_own_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            source = write_smoke(root / ".movian" / "smokes", "mine",
                                 plugin="../../src")
            definition = {"name": "mine", "source": source,
                          "needs": {"binary": "build.debug/movian",
                                    "plugin": "../../src"}}
            self.assertEqual(smoke._plugins_for([definition])[0],
                             str((root / "src").resolve()))

    def test_a_core_smoke_still_resolves_against_the_repo_root(self) -> None:
        definition = {"name": "core-one",
                      "source": smoke.CORE_SMOKES_DIR / "core-one.json",
                      "needs": {"binary": "build.debug/movian",
                                "plugin": "support/devtools/viewpreview"}}
        self.assertEqual(
            smoke._plugins_for([definition])[0],
            str((harness.REPO_ROOT / "support/devtools/viewpreview").resolve()))

    def test_an_absolute_path_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            definition = {"name": "abs", "source": Path(tmp) / "s.json",
                          "needs": {"binary": "build.debug/movian",
                                    "plugin": tmp}}
            self.assertEqual(smoke._plugins_for([definition])[0],
                             str(Path(tmp).resolve()))


class Discovery(unittest.TestCase):
    def test_a_plugin_definition_is_found_and_carries_its_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = write_smoke(Path(tmp) / "smokes", "plugin-side")
            with mock.patch.dict(os.environ,
                                 {smoke.SMOKES_PATH_ENV: str(Path(tmp) / "smokes")}):
                definitions = smoke.load_definitions()
        found = [d for d in definitions if d["name"] == "plugin-side"]
        self.assertEqual(len(found), 1, "plugin smoke was not discovered")
        self.assertEqual(Path(found[0]["source"]), source)

    def test_the_core_set_is_still_discovered_and_health_is_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_smoke(Path(tmp) / "smokes", "zzz-last")
            with mock.patch.dict(os.environ,
                                 {smoke.SMOKES_PATH_ENV: str(Path(tmp) / "smokes")}):
                definitions = smoke.load_definitions()
        names = [d["name"] for d in definitions]
        self.assertEqual(names[0], "health",
                         "health must stay first so `run all` keeps its contract")
        for known in smoke.SMOKE_ORDER:
            self.assertIn(known, names)
        self.assertIn("zzz-last", names)

    def test_two_directories_claiming_one_name_name_both_files(self) -> None:
        """A bare 'duplicate smoke name' stopped being actionable at two dirs."""
        with tempfile.TemporaryDirectory() as tmp:
            first = write_smoke(Path(tmp) / "one", "health")
            with mock.patch.dict(os.environ,
                                 {smoke.SMOKES_PATH_ENV: str(Path(tmp) / "one")}):
                with self.assertRaises(harness.MdevError) as caught:
                    smoke.load_definitions()
        message = str(caught.exception)
        self.assertIn("health", message)
        self.assertIn(str(first), message)
        self.assertIn(str(smoke.CORE_SMOKES_DIR), message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
