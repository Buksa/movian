"""viewdoc -- drift detector between the movian-metadata artifact and the
movian-view-design skill's reference docs (issue #88, rewired onto the
artifact by issue #98).

Compares, by name only:

- attribute names in `generated/movian-metadata.json`'s `glw.attributes`
  (itself scanned from glw_view_attrib.c's `attribtab[]` by
  `support/devtools/metadata/gen.py`)
  vs names documented in the widget catalog's "Global attributes" table
  (.claude/skills/movian-view-design/references/glw-widget-catalog.md);
- expression-function names in the artifact's `glw.functions`
  (scanned from glw_view_eval.c's `funcvec[]`)
  vs names documented in the language reference's function table
  (.claude/skills/movian-view-design/references/glw-view-language.md).

Reports two drift directions per table:
- missing-from-doc: in the artifact, absent from the doc
  (someone added an attribute/function without documenting it);
- gone-from-source: documented, absent from the artifact
  (the doc claims something this tree does not implement, OR the
  artifact is stale -- run `support/devtools/metadata/gen.py --check`
  first to rule that out; this module trusts the committed artifact,
  it does not re-scan the C source itself).

Exit 0 only when both directions are empty for both tables.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .harness import REPO_ROOT, MdevError

METADATA_ARTIFACT = REPO_ROOT / "generated" / "movian-metadata.json"

REFS_DIR = (REPO_ROOT / ".claude" / "skills" / "movian-view-design"
            / "references")
LANG_DOC = REFS_DIR / "glw-view-language.md"
CATALOG_DOC = REFS_DIR / "glw-widget-catalog.md"

BACKTICK_RE = re.compile(r"`([^`]+)`")
NAME_TOKEN_RE = re.compile(r"^[A-Za-z0-9_]+$")

# Markdown cell split: '|' that is not escaped as '\|'.
CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")


def load_artifact() -> dict:
    if not METADATA_ARTIFACT.is_file():
        raise MdevError(
            "metadata artifact not found: %s (run "
            "support/devtools/metadata/gen.py first)" % METADATA_ARTIFACT
        )
    return json.loads(METADATA_ARTIFACT.read_text(encoding="utf-8"))


def artifact_names(artifact: dict, section: str) -> list[str]:
    """Names from one `glw.<section>` list in the metadata artifact."""
    try:
        records = artifact["glw"][section]
    except KeyError:
        raise MdevError("metadata artifact missing glw.%s" % section)
    return [r["name"] for r in records]


def doc_section(path: Path, heading: str) -> str:
    """The body of one `## `-level section: from the line starting with
    `heading` to the next `## ` heading (exclusive)."""
    if not path.is_file():
        raise MdevError("reference doc not found: %s" % path)
    lines = path.read_text(encoding="utf-8").splitlines()
    body: list[str] = []
    in_section = False
    for line in lines:
        if in_section:
            if line.startswith("## "):
                break
            body.append(line)
        elif line.startswith(heading):
            in_section = True
    if not in_section:
        raise MdevError("heading %r not found in %s" % (heading, path))
    return "\n".join(body)


def doc_table_names(section: str, cell_index: int) -> list[str]:
    """Backticked name tokens from column `cell_index` of every markdown
    table row in `section`. A backtick span may hold several names
    separated by commas, slashes or whitespace; tokens that are not plain
    identifiers (anchors, prose) are ignored."""
    names: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in
                 CELL_SPLIT_RE.split(stripped.strip("|"))]
        if cell_index >= len(cells):
            continue
        for span in BACKTICK_RE.findall(cells[cell_index]):
            for token in re.split(r"[,/\s]+", span):
                if NAME_TOKEN_RE.match(token):
                    names.append(token)
    return names


def diff_names(source: list[str], documented: list[str]) -> dict:
    src, doc = set(source), set(documented)
    return {
        "source_count": len(src),
        "documented_count": len(doc),
        "missing_from_doc": sorted(src - doc),
        "gone_from_source": sorted(doc - src),
    }


def run_check() -> dict:
    """Both diffs. Keys: attributes, functions; each a diff_names() dict."""
    artifact = load_artifact()
    attrib_src = artifact_names(artifact, "attributes")
    func_src = artifact_names(artifact, "functions")

    # Catalog's "Global attributes" table: names live in column 1
    # ("attributes"), one comma-separated backtick span per group row.
    attrib_doc = doc_table_names(
        doc_section(CATALOG_DOC, "## Global attributes"), 1)

    # Language doc's function table: names live in column 0 of every row
    # under "## 6. Expression-function table" (subsections included).
    func_doc = doc_table_names(
        doc_section(LANG_DOC, "## 6. Expression-function table"), 0)

    return {
        "attributes": diff_names(attrib_src, attrib_doc),
        "functions": diff_names(func_src, func_doc),
    }


def inventory() -> dict:
    """Artifact-side name inventories (no doc comparison)."""
    artifact = load_artifact()
    return {
        "attributes": sorted(set(artifact_names(artifact, "attributes"))),
        "functions": sorted(set(artifact_names(artifact, "functions"))),
    }


def attribute_enum_values() -> dict[str, list[str]]:
    """Attribute enum values from the artifact, in attribute/source order."""
    artifact = load_artifact()
    return {
        record["name"]: record["enumValues"]
        for record in artifact["glw"]["attributes"]
        if "enumValues" in record
    }
