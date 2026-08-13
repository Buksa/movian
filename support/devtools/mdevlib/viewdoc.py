"""viewdoc -- drift detector between the GLW C source tables and the
movian-view-design skill's reference docs (issue #88).

Compares, by name only (no C parsing beyond the two tables' regular
`{"name", ...}` shape):

- attribute names in glw_view_attrib.c's `attribtab[]`
  vs names documented in the widget catalog's "Global attributes" table
  (.claude/skills/movian-view-design/references/glw-widget-catalog.md);
- expression-function names in glw_view_eval.c's `funcvec[]`
  vs names documented in the language reference's function table
  (.claude/skills/movian-view-design/references/glw-view-language.md).

Reports two drift directions per table:
- missing-from-doc: in the source table, absent from the doc
  (someone added an attribute/function without documenting it);
- gone-from-source: documented, absent from the source table
  (the doc claims something this tree does not implement).

Exit 0 only when both directions are empty for both tables.
"""

from __future__ import annotations

import re
from pathlib import Path

from .harness import REPO_ROOT, MdevError

ATTRIB_C = REPO_ROOT / "src" / "ui" / "glw" / "glw_view_attrib.c"
EVAL_C = REPO_ROOT / "src" / "ui" / "glw" / "glw_view_eval.c"

REFS_DIR = (REPO_ROOT / ".claude" / "skills" / "movian-view-design"
            / "references")
LANG_DOC = REFS_DIR / "glw-view-language.md"
CATALOG_DOC = REFS_DIR / "glw-widget-catalog.md"

ATTRIB_TABLE_DECL = "token_attrib_t attribtab[] = {"
FUNC_TABLE_DECL = "token_func_t funcvec[] = {"

# `{"name", ...}` entry inside a C table initializer.
C_NAME_RE = re.compile(r'\{\s*"([A-Za-z0-9_]+)"')

BACKTICK_RE = re.compile(r"`([^`]+)`")
NAME_TOKEN_RE = re.compile(r"^[A-Za-z0-9_]+$")

# Markdown cell split: '|' that is not escaped as '\|'.
CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")


def source_table_names(path: Path, table_decl: str) -> list[str]:
    """Names from one C table: scan from the line containing `table_decl`
    to the next line that closes the initializer (`};`)."""
    if not path.is_file():
        raise MdevError("source file not found: %s" % path)
    names: list[str] = []
    in_table = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not in_table:
            if table_decl in line:
                in_table = True
            continue
        if line.strip() == "};":
            return names
        names.extend(C_NAME_RE.findall(line))
    raise MdevError("table %r not found (or unterminated) in %s"
                    % (table_decl, path))


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
    attrib_src = source_table_names(ATTRIB_C, ATTRIB_TABLE_DECL)
    func_src = source_table_names(EVAL_C, FUNC_TABLE_DECL)

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
    """Source-side name inventories (no doc comparison)."""
    return {
        "attributes": sorted(set(source_table_names(ATTRIB_C,
                                                    ATTRIB_TABLE_DECL))),
        "functions": sorted(set(source_table_names(EVAL_C,
                                                   FUNC_TABLE_DECL))),
    }
