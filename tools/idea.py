#!/usr/bin/env python3
"""
idea.py - resolve a project directory, read what it already records, and write
the artifacts a settled idea produces.

The judgment half of idea generation lives in
skills/idea-generation/SKILL.md: what the gap is, which candidates are worth
taking forward, how the paper should be framed. This file is the half with
right answers - finding the directory the user meant, reporting what is
already recorded so nothing is re-asked, and turning a settled idea into files
without ever destroying work that is already there.

Six commands:

  resolve    an as-typed path -> the project directory that was meant
  context    what the project and its lab folder already record
  resources  the lab's own Resources/ folder, from any path - Stage 2 reads it
  write      a settled-idea bundle -> plan/, data/, and the literature summary
  merge      a settled-idea bundle -> a project that ALREADY has work in it
  mock       a data_files bundle -> data/mock_data/generate_mock_data.py, run

`write` creates; `merge` is the additive-only route into a project somebody has
already typed into. They are separate commands because they have opposite
defaults: `write` fills a blank section, `merge` never modifies anything.

Usage:
  python idea.py resolve "Projects/Array Symmetry" --json
  python idea.py context "Projects/array_symmetry" --json
  python idea.py resources "Grant Jensen - CryoEm Biochemistry" --json
  python idea.py write "Projects/array_symmetry" --bundle idea.json --dry-run
  python idea.py write "Ideas/Array Symmetry" --bundle idea.json --mode explore
  python idea.py merge "Projects/array_symmetry" --bundle idea.json --dry-run
"""

from __future__ import annotations

import argparse
import csv
import datetime
import difflib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys

for _stream in (sys.stdout, sys.stderr):
    # A redirected stream (a pipe, a StringIO under a harness) has no
    # reconfigure at all; asking for it by name keeps that case quiet.
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except ValueError:
            pass

# prose.py owns the sentence splitter, and the 3-4 sentence caps of
# specs/idea-generation.md 15.2 have to count sentences the same way every
# other check in this toolkit counts them. Reuse rather than a fourth copy of
# the abbreviation list: "et al." appears in every paper block written here.
# prose.py is standard library only, so this costs nothing at import.
_PROSE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "prose.py")
_spec = importlib.util.spec_from_file_location("prose_for_idea", _PROSE_PATH)
if _spec is None or _spec.loader is None:      # pragma: no cover - install bug
    raise ImportError(f"cannot load {_PROSE_PATH}")
prose = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prose)

# labpack.py owns both ladders of specs/lab-resource-pack.md 4.1, and the
# drop-zone one is read here: Stage 2 and Stage 5 have to see what a member
# dropped into `resources/` as well as what is in the lab's OneDrive folder.
# Loaded by path for the same reason prose.py is - standard library only, so
# it costs nothing at import, and one implementation of the first-heading
# reader rather than two that drift.
_LABPACK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "labpack.py")
_lp_spec = importlib.util.spec_from_file_location("labpack_for_idea",
                                                  _LABPACK_PATH)
if _lp_spec is None or _lp_spec.loader is None:  # pragma: no cover - install bug
    raise ImportError(f"cannot load {_LABPACK_PATH}")
labpack = importlib.util.module_from_spec(_lp_spec)
_lp_spec.loader.exec_module(labpack)

TODAY = datetime.date.today().isoformat()
MARKER = "<!-- idea-generation {} -->"

# The caption parser in plan/theme/read_captions.R is the consumer of every
# block this file writes, so its heading grammar is reproduced here rather than
# approximated. A block that does not match is invisible to the preview, the
# Word floats, and the manuscript - and nothing would report it.
CAPTION_HEADING_RE = re.compile(
    r"^##\s+(Figure|Table)\s+(S?\d+)\s*(?:—|–|-{1,2})\s*(\S+)\s*$")

# A float lives in its own numbered folder - plan/figures/Fig01/ - and the
# caption heading names that folder. The grammar is tools/scaffold.py's
# FLOAT_DIR_RE and plan/theme/read_captions.R's; this is the third copy, and
# tests/idea.py pins it against the engine that owns it.
FLOAT_DIR_RE = re.compile(
    r"^(Fig|Figure|Tab|Table)[ _-]?(S?)([0-9]+)([ _-].*)?$", re.IGNORECASE)

# How a float is MADE, which is not what it shows. scaffold.py owns the same
# vocabulary for `float new --art`; repeated rather than imported because every
# engine here is standalone, and pinned by tests/idea.py against scaffold.py's
# own ART_KINDS.
FLOAT_ART_KINDS = ("plotted", "drawn", "mixed")

# scaffold.py's DRAWN_MARKER and DRAWN_BANNER, repeated for the same reason and
# pinned the same way.
FLOAT_DRAWN_MARKER = "# --- drawn float (create-graphic-figure) ---"

FLOAT_DRAWN_BANNER = """{marker}
# This float's art is DRAWN, not plotted. The panels are authored as
# PowerPoint slides in this folder and rendered into the figure, so they stay
# editable by whoever drew them:
#
#     python <tools>/graphic_figure.py add <project> --figure {n} --panel A
#
# Composing them is this script's job; drawing them is not. A schematic
# hand-drawn in annotate() calls renders once and is then editable only by
# somebody who will edit R."""

# What a bundle may say instead of a folder, so a float set written against the
# old flat layout is converted rather than refused. Nothing writes these any
# more; the conversion exists because a refusal that gets worked around is
# worse than a missing feature.
FLAT_FLOAT_FILE_RE = re.compile(
    r"^(fig|tab)(S?)([0-9]{1,2})[_-]?([a-z0-9_]*)\.(png|html|rds|pdf)$",
    re.IGNORECASE)


def float_folder(f: dict) -> str:
    """The folder a bundle's float belongs in: Fig01, Fig01_slug, TableS02.

    Derived from the label, so the number in the folder and the number in the
    caption heading cannot disagree - they have one source. A `folder` given
    outright wins; a legacy flat `file` contributes only its slug.
    """
    given = str(f.get("folder", "")).strip()
    if given:
        return given
    label = str(f.get("label", "")).strip()
    m = re.match(r"^(Figure|Table)\s+(S?)([0-9]+)$", label, re.IGNORECASE)
    if not m:
        return ""
    prefix = "Fig" if m.group(1).lower() == "figure" else "Table"
    slug = str(f.get("slug", "")).strip()
    if not slug:
        fm = FLAT_FLOAT_FILE_RE.match(str(f.get("file", "")).strip())
        if fm:
            slug = fm.group(4)
    stem = "%s%s%02d" % (prefix, m.group(2).upper(), int(m.group(3)))
    return f"{stem}_{slug}" if slug else stem

PMID_IN_TEXT_RE = re.compile(r"PMID[:\s]*(\d{4,9})")

KINDS = ["continuous", "count", "proportion", "categorical", "ordinal",
         "identifier", "datetime"]

# Caps from the spec. They are design features, not arbitrary limits - past
# them the artifact stops being read - but they are judgment calls, so they
# warn rather than refuse.
CAPS = {
    "literature": (3, 8, "plan/relevant_literature/ stops being a reading aid "
                         "and becomes a dump"),
    "floats": (3, 6, "a float set larger than this is usually two papers"),
    "candidates": (3, 5, "past five candidates the user stops reading carefully"),
}

# specs/idea-generation.md 15.2: the takeaway and every paper block in an
# Ideas/ folder's summary.md are 3-4 sentences. Reported, never refused - it is
# a judgment cap like every other one here - but reported is what stops a
# one-line note or a page of prose being written and forgotten.
SENTENCE_CAP = (2, 6)

# specs/idea-generation.md 11.2: a proposed methods value has exactly two legal
# kinds of source. Anything else is a guess wearing a citation.
PROPOSED_SOURCE_CITATION_RE = re.compile(
    r"^(?:PMID\s*\d{4,9}|doi:\s*\S+|arXiv:\s*\S+)\s*(?:[.,;|·-]\s*\S+)",
    re.I)
PROPOSED_SOURCE_RESOURCES_RE = re.compile(
    r"(?:^|[/\\])Resources[/\\]", re.I)
# specs/methods-notebook-2026-09-28.md 1: the third kind, a section of the
# lab pack - `pack:<file>.md - <heading>`. Checked against the pack that
# resolves, never pattern-matched alone: a source that cannot be checked is
# the guess this rule exists to refuse.
PROPOSED_SOURCE_PACK_RE = re.compile(
    r"^pack:\s*([^\s/\\]+\.md)\s*(?:[-–—]\s*(.*?))?\s*$", re.I)


def pack_source_problem(src: str) -> str:
    """Why a `pack:` source cannot be accepted, or "" when it can."""
    m = PROPOSED_SOURCE_PACK_RE.match(src)
    if not m:
        return "is not a pack source"
    fname, heading = m.group(1), (m.group(2) or "").strip()
    if not heading:
        return (f"names {fname} but no section of it. A pack source is "
                f"`pack:{fname} - <the section heading>`")
    pack = labpack.find_pack()
    if not pack.get("path"):
        return ("names a pack section, and no lab pack is installed here, "
                "so the source cannot be checked")
    docs = labpack.pack_documents(pack["path"])
    doc = docs.get(fname)
    if doc is None:
        return (f"names {fname}, which the installed pack does not carry. "
                "Its files are: " + ", ".join(sorted(docs)))
    heads = [sec["heading"] for sec in doc["sections"]]
    if labpack._norm_heading(heading) not in {labpack._norm_heading(h)
                                              for h in heads}:
        return (f"names a section {fname} does not carry: \"{heading}\". "
                "Its sections are: " + "; ".join(heads))
    return ""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return s or "project"


def _strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "project_template")
_TEMPLATE_CACHE: dict[str, dict[str, str]] = {}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def template_sections(rel: str) -> dict[str, str]:
    """The pristine scaffolded body of each section of a template file.

    Deciding "has someone written here yet" by heuristic does not work: the
    scaffolded `## Study design` is a table of empty cells with row labels, and
    any rule loose enough to call that empty also calls a real half-filled table
    empty. Comparing against the template the scaffold actually wrote is exact,
    so that is what is done - the heuristic below is only the fallback for a
    section whose instruction comment someone has deleted.
    """
    if rel not in _TEMPLATE_CACHE:
        _, secs = _split_sections(_read(os.path.join(TEMPLATE_DIR, rel)))
        _TEMPLATE_CACHE[rel] = {_heading_name(h): b for h, b in secs}
    return _TEMPLATE_CACHE[rel]


def _is_blank_section(body: str, template_body: str | None = None) -> bool:
    """True when nothing but scaffolding is in this section."""
    if template_body is not None and _norm(body) == _norm(template_body):
        return True
    t = _strip_comments(body)
    t = re.sub(r"^\s*\|[\s|:-]*\|\s*$", "", t, flags=re.M)   # table rules
    t = re.sub(r"^\s*\|(\s*\|)+\s*$", "", t, flags=re.M)     # empty table rows
    t = re.sub(r"^\s*[-*]\s*$", "", t, flags=re.M)           # bare bullets
    t = re.sub(r"^\s*-\s*\[\s*\]\s*$", "", t, flags=re.M)    # empty checkboxes
    t = re.sub(r"^\s*\*\*[A-Za-z /]+:\*\*\s*$", "", t, flags=re.M)  # blank labels
    return not t.strip()


def _split_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Split markdown into (preamble, [(heading_line, body), ...]) on '## '."""
    lines = text.splitlines(keepends=True)
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    for line in lines:
        if line.startswith("## "):
            sections.append((line, []))
        elif sections:
            sections[-1][1].append(line)
        else:
            preamble.append(line)
    return "".join(preamble), [(h, "".join(b)) for h, b in sections]


def _join_sections(preamble: str, sections: list[tuple[str, str]]) -> str:
    return preamble + "".join(h + b for h, b in sections)


# A level-1 heading opens a new REGION, and `_split_sections` splits on `## `
# only - so a `# Notes` region sitting after the last `## ` section is inside
# that section's body as far as every caller is concerned.
#
# That was item 53, and it deleted work: `fill_or_append` replaces a body that
# is still scaffolding, so filling the last section of `plan/outline.md` took
# the whole `# Notes` region with it. Measured on a fresh scaffold - a project
# populated by `idea.py write --mode project` came back with `# Structure`,
# Introduction, Results, Discussion and no `# Notes` at all. That region is a
# **primary drafting source** and it is the only place the "write here freely,
# and nothing you write becomes a paragraph the paper is reported as having
# dropped" guarantee is written down.
#
# Fixed here rather than in `_split_sections`, deliberately: making `# ` a
# boundary would turn every file's own title into a section and change what
# `context` reports about three other files. What is wrong is not the split,
# it is that a body may carry a region that is not part of the section.
LEVEL1_RE = re.compile(r"^# \S", re.M)


def _split_off_region(body: str) -> tuple[str, str]:
    """(this section's body, any level-1 region that follows it)."""
    m = LEVEL1_RE.search(body)
    if not m:
        return body, ""
    return body[:m.start()], body[m.start():]


def _heading_name(heading_line: str) -> str:
    return heading_line[3:].strip().lower()


# ---------------------------------------------------------------------------
# resolve - §1.1
# ---------------------------------------------------------------------------

SCAFFOLD_MARKERS = (".here", "project.yml", "plan")


def looks_scaffolded(path: str) -> bool:
    return all(os.path.exists(os.path.join(path, m)) for m in SCAFFOLD_MARKERS)


def _candidates_near(path: str, limit: int = 5) -> list[dict]:
    """Directories that could plausibly be what the user typed.

    Looks in the named parent, in a 'Projects' folder beside it, and one level
    up - the three places a project actually is when the path as typed misses.
    """
    target = os.path.basename(os.path.normpath(path))
    parent = os.path.dirname(os.path.abspath(path)) or "."
    search_dirs = [parent, os.path.join(parent, "Projects"),
                   os.path.dirname(parent)]

    seen: dict[str, dict] = {}
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        try:
            names = [n for n in os.listdir(d) if os.path.isdir(os.path.join(d, n))]
        except OSError:
            continue
        for n in names:
            full = os.path.abspath(os.path.join(d, n))
            if full in seen:
                continue
            score = difflib.SequenceMatcher(
                None, target.lower(), n.lower()).ratio()
            # A scaffolded directory is a far better candidate than a similarly
            # named one that is not, so it takes precedence over raw similarity.
            scaffolded = looks_scaffolded(full)
            if score >= 0.55 or scaffolded and score >= 0.4:
                seen[full] = {"path": full, "name": n,
                              "similarity": round(score, 3),
                              "scaffolded": scaffolded}
    out = sorted(seen.values(),
                 key=lambda c: (c["scaffolded"], c["similarity"]), reverse=True)
    return out[:limit]


def resolve(path: str) -> dict:
    abspath = os.path.abspath(path)
    res = {"asked_for": path, "path": abspath, "candidates": []}

    if os.path.isdir(abspath):
        if looks_scaffolded(abspath):
            res["status"] = "scaffolded"
            res["action"] = "use it"
            res["project"] = read_project_yml(abspath)
            return res
        # A Projects/ folder holding exactly one scaffolded project is the
        # common near-miss: the user named the parent, not the project.
        children = [os.path.join(abspath, n) for n in sorted(os.listdir(abspath))]
        inner = [c for c in children if os.path.isdir(c) and looks_scaffolded(c)]
        if inner:
            res["status"] = "close_match"
            res["action"] = "confirm which one"
            res["candidates"] = [{"path": c, "name": os.path.basename(c),
                                  "similarity": 1.0, "scaffolded": True}
                                 for c in inner]
            return res
        res["status"] = "unscaffolded"
        res["action"] = "offer to run setup-project-directory here first"
        res["populated"] = bool(os.listdir(abspath))
        return res

    cands = _candidates_near(abspath)
    if cands:
        res["status"] = "close_match"
        res["action"] = "confirm the nearest candidate"
        res["candidates"] = cands
        return res

    res["status"] = "missing"
    res["action"] = "offer to create and scaffold it"
    return res


# ---------------------------------------------------------------------------
# context - what is already recorded, so nothing gets re-asked
# ---------------------------------------------------------------------------

def read_project_yml(root: str) -> dict:
    """Flat `key: value` pairs from project.yml. Deliberately a hand parser -
    the same one scaffold.py uses, and for the same reason: no dependency."""
    out: dict = {}
    path = os.path.join(root, "project.yml")
    if not os.path.isfile(path):
        return out
    for line in _read(path).splitlines():
        line = line.split("#", 1)[0].rstrip()
        m = re.match(r"^([a-z_]+):\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def dropped_dirs(root: str) -> list[str]:
    """project.yml's `dropped_dirs:` - the scaffold folders the author removed
    on purpose. `scaffold.py drop` is the only writer; this is a second
    reader, and tests/idea.py pins it to scaffold.py's so the two cannot
    disagree about what was dropped."""
    raw = read_project_yml(root).get("dropped_dirs", "").strip()
    if not (raw.startswith("[") and raw.endswith("]")):
        return []
    out: list[str] = []
    for part in raw[1:-1].split(","):
        d = part.strip().strip('"').strip("'").replace("\\", "/").strip("/")
        if d and d not in out:
            out.append(d)
    return out


def _dropped_note(rel_dir: str) -> str:
    return ("%s/ was removed on purpose (project.yml dropped_dirs) - "
            "`scaffold.py drop --restore %s` brings it back" % (rel_dir, rel_dir))


def find_lab_folder(root: str) -> str:
    """The `<PI> - <Field>/` directory above the project, found by the folders
    that actually mark it: a Resources/ or Ideas/ sibling. Walking up for a
    name pattern would break the moment a lab folder is named differently."""
    cur = os.path.abspath(root)
    for _ in range(6):
        parent = os.path.dirname(cur)
        if not parent or parent == cur:
            break
        for marker in ("Resources", "Ideas"):
            if os.path.isdir(os.path.join(parent, marker)):
                return parent
        cur = parent
    return ""


# A title is the first ATX heading of a text file, and only these extensions
# are opened for one. A .pdf or a .xlsx has no readable first line, and
# guessing at one is worse than using the filename.
TITLED_EXTS = (".md", ".markdown", ".txt", ".rst")


def _resource_title(path: str) -> str:
    """The first heading of an SOP, or "" - never the filename.

    Empty rather than a fallback on purpose: the caller already has the
    filename, and a "title" that is just the filename with the underscores
    taken out reads as though somebody wrote it.

    `labpack.first_heading` is the implementation. The drop-zone inventory and
    this one have to agree about what a title is, and two readers of the same
    thing is how they come to disagree.
    """
    return labpack.first_heading(path)


def inventory_resources(lab: str, max_files: int = 40) -> dict:
    """What the lab folder records about what the lab can actually do.

    Read at **Stage 2** as well as Stage 5 (specs/idea-generation.md 9.2), and
    that is the change rather than an extra call. The lab's SOPs name its
    actual instruments, techniques and materials, and those names are the
    search vocabulary: a lab whose procedures folder is full of QCM and TPD
    should not have its first gap sweep run on the words the user happened to
    type in Stage 1. Same argument that puts Stage 3 after Stage 2 - an
    uninformed query wastes the effort - applied one step earlier.

    Names, paths, and the first heading of each text file. **No document body
    enters this output**, so it stays cheap enough to call before the first
    query; a document is read when it is about to inform a question.
    """
    out = {"lab": lab, "resources": "", "subfolders": [], "files": [],
           "titles": {}, "truncated": False}
    if not lab:
        return out
    res = os.path.join(lab, "Resources")
    if not os.path.isdir(res):
        return out
    out["resources"] = res
    hits = []
    for dirpath, dirnames, filenames in os.walk(res):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        rel_dir = os.path.relpath(dirpath, res)
        if rel_dir != ".":
            out["subfolders"].append(rel_dir.replace(os.sep, "/"))
        for f in filenames:
            if f.startswith("."):
                continue
            hits.append(os.path.join(rel_dir, f).replace("./", "")
                        .replace(os.sep, "/"))
    out["subfolders"] = sorted(set(out["subfolders"]))
    out["files"] = sorted(hits)[:max_files]
    out["truncated"] = len(hits) > max_files
    out["file_count"] = len(hits)
    for rel in out["files"]:
        if rel.lower().endswith(TITLED_EXTS):
            title = _resource_title(os.path.join(res, rel))
            if title:
                out["titles"][rel] = title
    return out


def _pack_vocabulary() -> dict:
    """The resolved lab pack as SEARCH VOCABULARY, or an empty record.

    Item 106. What a caller needs is what the pack is called, whether it is
    stale, and the headings it carries - the instrument names, the SOP
    titles, the location names - because those are the words a literature
    search for this lab's work is built out of. The document bodies are not
    here and must not be: this runs before the first query, and the same
    no-body contract governs the OneDrive inventory beside it.

    Every failure is an empty record rather than an exception. A machine with
    no pack installed is the normal case, not an error, and a lab folder that
    resolves nothing must still return its drop-zones.
    """
    empty = {"resolved": False, "path": "", "display_name": "", "version": "",
             "freshness": "", "state": "", "headings": {}, "files": [],
             "note": "no lab resource pack is installed on this machine"}
    try:
        pack = labpack.find_pack()
        if not pack or not pack.get("path"):
            return empty
        fresh = labpack.freshness(pack)
        inv = labpack.inventory_dir(pack["path"])
        return {
            "resolved": True,
            "path": pack["path"],
            "source": pack.get("source", ""),
            "rung": pack.get("rung", 0),
            "display_name": pack.get("display_name", ""),
            "version": pack.get("version", ""),
            "curated_on": pack.get("curated_on", ""),
            "freshness": fresh.get("line", ""),
            "state": fresh.get("state", ""),
            # The vocabulary itself: one heading per file in the pack.
            "headings": inv.get("titles", {}),
            "files": inv.get("files", []),
            "note": "",
        }
    except Exception as exc:
        return {**empty, "note": "the pack could not be read: %s" % exc}


def lab_resources(path: str) -> dict:
    """`Resources/` from any path, because Stage 2 has no project directory.

    Stage 2 runs before a project folder exists - 10.2 builds it at save time -
    so the inventory has to be reachable from the lab folder, from a project
    inside it, or from the `Resources/` folder itself. Resolution is by what is
    on disk rather than by a name pattern, for the same reason
    `find_lab_folder` is: a lab folder named differently must not become an
    unsupported case.
    """
    p = os.path.abspath(path)
    res: dict = {"given": path, "resolved_from": ""}
    # specs/lab-resource-pack.md 4.5: the drop-zone roots are a SECOND
    # root, not a fallback, and every one of them is read. They are
    # attached before the OneDrive folder resolves, so a member whose lab
    # keeps no Resources/ folder still gets whatever they dropped in
    # themselves - which was the whole defect: nothing read that folder
    # except `scaffold.py prefill`, by exact filename, so a dropped
    # `Methods.docx` produced no error and no effect.
    res["drop_zones"] = labpack.resource_roots()
    res["drop_zones_at_risk"] = [r["path"] for r in res["drop_zones"]
                                 if r["at_risk"]]
    # Item 106: the THIRD source of search vocabulary, which the skill's
    # Stage 2 table has always promised this command returns and which this
    # payload did not carry at all. The engine imported labpack for exactly
    # two things - the drop-zone roots above and the title reader - and no
    # `pack` key existed, so nothing in the output named the pack, its
    # freshness or its headings. A reader who trusted the table had already
    # been told this command covered it.
    #
    # It bites hardest in the configuration the pack was built for: a lab
    # that keeps no `Resources/` folder has the pack as its ONLY written
    # vocabulary, and there this command returned an empty inventory and two
    # empty drop-zones while a pack sat resolved on disk.
    #
    # Fixed on the ENGINE side rather than by correcting the table, as the
    # item's `wanted behavior` prefers: the skill already runs this lookup
    # once at Stage 1 for the freshness line, so the vocabulary read is the
    # same resolution and asking twice would be the thing to explain.
    res["pack"] = _pack_vocabulary()
    if os.path.isdir(os.path.join(p, "Resources")):
        res["resolved_from"] = "the lab folder itself"
        res.update(inventory_resources(p))
        return res
    if os.path.basename(p).lower() == "resources" and os.path.isdir(p):
        res["resolved_from"] = "the Resources folder itself"
        res.update(inventory_resources(os.path.dirname(p)))
        return res
    lab = find_lab_folder(p)
    if lab:
        res["resolved_from"] = "walked up from the given path"
        res.update(inventory_resources(lab))
        return res
    res.update(inventory_resources(""))
    res["note"] = (
        "No lab folder with a Resources/ or Ideas/ sibling was found above "
        f"{p}. That is not an error - a lab that keeps no Resources/ folder is "
        "a lab whose SOPs are not written down anywhere this can read, and the "
        "search vocabulary then comes from the conversation instead.")
    return res


def _filled_sections(path: str, template_rel: str) -> dict:
    """Which '## ' sections of a scaffolded markdown file already say something.

    This is what stops the skill re-asking for a research question that is
    already written down.
    """
    text = _read(path)
    if not text:
        return {}
    tmpl = template_sections(template_rel)
    _, sections = _split_sections(text)
    return {_heading_name(h): not _is_blank_section(b, tmpl.get(_heading_name(h)))
            for h, b in sections}


def context(root: str) -> dict:
    root = os.path.abspath(root)
    lab = find_lab_folder(root)
    captions = _read(os.path.join(root, "plan", "captions.md"))
    float_blocks = [m.group(0).strip() for m in
                    re.finditer(r"^##\s+(Figure|Table).*$", captions, flags=re.M)]
    return {
        "path": root,
        "exists": os.path.isdir(root),
        "scaffolded": looks_scaffolded(root),
        "project": read_project_yml(root),
        "readme_sections": _filled_sections(
            os.path.join(root, "plan", "README.md"), "plan/README.md"),
        "outline_sections": _filled_sections(
            os.path.join(root, "plan", "outline.md"), "plan/outline.md"),
        "existing_floats": float_blocks,
        "has_ideas_md": os.path.isfile(os.path.join(root, "plan", "ideas.md")),
        "literature_files": sorted(
            os.listdir(os.path.join(root, "plan", "relevant_literature"))
        ) if os.path.isdir(os.path.join(root, "plan", "relevant_literature")) else [],
        "data_raw": sorted(os.listdir(os.path.join(root, "data", "raw")))
        if os.path.isdir(os.path.join(root, "data", "raw")) else [],
        "lab": inventory_resources(lab),
    }


# ---------------------------------------------------------------------------
# Bundle validation
# ---------------------------------------------------------------------------

def validate(bundle: dict, mode: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    lit = bundle.get("literature") or []
    known_pmids = set()
    for i, p in enumerate(lit):
        # A paper is identified by whatever identifier it actually has.
        #
        # This used to demand a PMID and reject the entry outright without one,
        # which quietly made the idea stage unusable for this group's own
        # subject: Surface Science, Applied Surface Science, J. Vac. Sci.
        # Technol. and Vacuum are not in PubMed at all, so their papers have no
        # PMID to give. `scholar.py` retrieves and verifies them through
        # Crossref, OpenAlex, Europe PMC and arXiv, and a record it verified is
        # exactly as retrieved as a PubMed one. What must never be missing is
        # *some* identifier - that is what "actually retrieved" means.
        pmid = str(p.get("pmid", "")).strip()
        doi = str(p.get("doi", "")).strip()
        arxiv = str(p.get("arxiv", "")).strip()
        ident = (f"PMID {pmid}" if pmid else
                 f"doi:{doi}" if doi else
                 f"arXiv:{arxiv}" if arxiv else "")
        if not ident:
            errors.append(f"literature[{i}] has no pmid, doi or arxiv id; every "
                          f"paper must be one that was actually retrieved")
            continue
        if not p.get("title"):
            errors.append(f"literature[{i}] ({ident}) has no title - a "
                          f"record that was retrieved has a title")
        # §4: every reference goes through `scholar.py verify` before it reaches
        # a written file. Carrying the verdict through the bundle is what makes
        # that checkable here rather than a promise - an entry with no status
        # is one nobody looked up.
        status = str(p.get("status", "")).strip()
        if status not in ("verified", "partial"):
            errors.append(
                f"literature[{i}] ({ident}) has status {status or 'none'!r}; "
                f"only records that came back verified or partial from "
                f"`scholar.py verify` may be written")
        for flag in p.get("flags") or []:
            if "RETRACT" in flag.upper() or "xpression of concern" in flag:
                warnings.append(
                    f"{ident} carries a flag and will be written with it "
                    f"shown: {flag}")
            elif "PREPRINT" in flag.upper():
                warnings.append(
                    f"{ident} is a preprint and will be written with its flag "
                    f"shown: {flag}")
        if pmid:
            known_pmids.add(pmid)

    # §4: the skill must never name a paper it has not retrieved. Every PMID
    # written into any prose field has to appear in the retrieved set, or it is
    # a citation from memory - which is exactly what this toolkit exists to
    # prevent, and the idea stage is where it is cheapest to catch.
    prose = []
    for key in ("background", "gap", "question", "hypothesis", "narrative",
                "prediction_if_true", "prediction_if_false"):
        prose.append(str(bundle.get(key, "")))
    for line in bundle.get("outline") or []:
        prose.append(str(line.get("line", "")) + " " + str(line.get("evidence", "")))
    for c in (bundle.get("ideas") or {}).get("candidates") or []:
        prose.append(json.dumps(c, ensure_ascii=False))
    for f in bundle.get("floats") or []:
        prose.append(str(f.get("claim", "")) + " " + str(f.get("description", "")))

    cited = set()
    for chunk in prose:
        cited.update(PMID_IN_TEXT_RE.findall(chunk))
    unknown = sorted(cited - known_pmids)
    if unknown:
        errors.append(
            "these PMIDs are cited but are not in the retrieved literature set: "
            + ", ".join(unknown)
            + "\n  Every citation must come from a record retrieved through "
              "`scholar.py`. Retrieve them and add them to `literature`, or "
              "remove the claim.")

    # Floats have to parse under read_captions.R or they are invisible
    # downstream, and nothing would report it.
    seen_dirs = set()
    for i, f in enumerate(bundle.get("floats") or []):
        label = str(f.get("label", "")).strip()
        folder = float_folder(f)
        claim = str(f.get("claim", "")).strip()
        if not claim:
            errors.append(f"floats[{i}] ({label or folder}) has no claim; a float "
                          f"without a claim is a chart, not a figure")
        heading = f"## {label} — {folder}"
        if not CAPTION_HEADING_RE.match(heading):
            errors.append(
                f"floats[{i}] would produce an unparseable caption heading: "
                f"{heading!r}\n  Needs 'Figure N' or 'Table N' (optionally SN) "
                f"and a folder name with no spaces.")
        # The folder is what the preview, the Word floats and the manuscript
        # resolve a caption block to. One the parser cannot read is invisible
        # to all three and nothing downstream reports it.
        if folder and not FLOAT_DIR_RE.match(folder):
            errors.append(
                f"floats[{i}] folder {folder!r} is not a float folder name; "
                f"expected FigNN, FigSNN, TableNN, optionally with a _slug")
        if folder in seen_dirs:
            errors.append(f"floats[{i}] repeats the folder {folder!r}")
        seen_dirs.add(folder)
        # HOW the float is made, which is a different question from what it
        # shows. A drawn float - a scheme, a mechanism, an apparatus, a
        # pathway - is authored in PowerPoint by create-graphic-figure and
        # stays editable by whoever drew it; unrecorded, it gets a bare
        # figure.R and is hand-drawn in annotate() calls, which renders once
        # and is then editable only by somebody who will edit R (item 82).
        art = str(f.get("art", "") or "").strip().lower()
        if art and art not in FLOAT_ART_KINDS:
            errors.append(
                f"floats[{i}] has art {art!r}; it is one of "
                + ", ".join(FLOAT_ART_KINDS)
                + " - plotted is a chart, drawn is a schematic, diagram, "
                  "scheme, mechanism or apparatus authored in PowerPoint")

    # Data contract: the kind is what decides the geom and the test, so an
    # unrecognised one is an error rather than a note.
    for i, df in enumerate(bundle.get("data_files") or []):
        fname = str(df.get("file", "")).strip()
        if not fname.endswith(".csv"):
            errors.append(f"data_files[{i}] file {fname!r} must be a .csv")
        cols = df.get("columns") or []
        if not cols:
            errors.append(f"data_files[{i}] ({fname}) has no columns")
        names = []
        for c in cols:
            name = str(c.get("name", "")).strip()
            kind = str(c.get("kind", "")).strip()
            if not name:
                errors.append(f"data_files[{i}] has a column with no name")
            if kind not in KINDS:
                errors.append(f"{fname}:{name} has kind {kind!r}; must be one of "
                              + ", ".join(KINDS))
            if name and name != _slug(name):
                warnings.append(f"{fname}:{name} is not snake_case; "
                                f"data_contract.md asks for snake_case columns")
            names.append(name)
        if len(names) != len(set(names)):
            errors.append(f"data_files[{i}] ({fname}) repeats a column name")
        gc = str(df.get("group_column", "")).strip()
        if gc and gc not in names:
            errors.append(f"{fname}: group_column {gc!r} is not one of its columns")

    for key, (lo, hi, why) in CAPS.items():
        n = len(bundle.get(key) or
                (bundle.get("ideas") or {}).get("candidates") or []
                if key == "candidates" else bundle.get(key) or [])
        if n and not (lo <= n <= hi):
            warnings.append(f"{n} {key} - the spec's range is {lo}-{hi}: {why}")

    # --- proposed methods: the source is what separates a proposal from a
    # --- guess, and it is mandatory (specs/idea-generation.md 11.2)
    for block, entries in (bundle.get("methods_proposed") or {}).items():
        if not isinstance(entries, dict):
            errors.append(f"methods_proposed[{block!r}] is not a block of "
                          f"keys; every entry is a block, never a bare scalar")
            continue
        for key, entry in entries.items():
            where = f"methods_proposed[{block}][{key}]"
            if not isinstance(entry, dict):
                errors.append(
                    f"{where} is a bare value. Every proposed entry is a "
                    f"block with `value`, `source` and `why` - a bare number "
                    f"is indistinguishable from a measured one")
                continue
            if entry.get("value") in (None, ""):
                errors.append(f"{where} has no value")
            src = str(entry.get("source", "")).strip()
            if not src:
                errors.append(
                    f"{where} has no source. A proposed value with no source "
                    f"is not a proposal, it is a guess, and it must not be "
                    f"written")
                continue
            is_cite = bool(PROPOSED_SOURCE_CITATION_RE.match(src))
            is_res = bool(PROPOSED_SOURCE_RESOURCES_RE.search(src))
            if PROPOSED_SOURCE_PACK_RE.match(src):
                why = pack_source_problem(src)
                if why:
                    errors.append(f"{where} source {src!r} {why}")
            elif not (is_cite or is_res):
                errors.append(
                    f"{where} source {src!r} is neither kind. There are "
                    f"three: a retrievable citation WITH the section it came "
                    f"from (\"PMID 22267509 - Methods\", \"doi:10.1/x - "
                    f"S2\"), a path under Resources/, or a section of the "
                    f"lab pack (\"pack:sops.md - <the section heading>\") - "
                    f"the section it came from")
            # A citation in a proposal is a citation, and 4 applies to it: it
            # may only name a paper that was actually retrieved and verified.
            # Without this, the one file in the project whose whole purpose is
            # "here is where this number came from" is the one place a citation
            # from memory can still land.
            for pmid in PMID_IN_TEXT_RE.findall(src):
                if pmid not in known_pmids:
                    errors.append(
                        f"{where} sources PMID {pmid}, which is not in the "
                        f"retrieved literature set. Retrieve and verify it "
                        f"through `scholar.py`, or drop the entry")
            if not str(entry.get("why", "")).strip():
                warnings.append(
                    f"{where} has no `why`. The value says what; `why` says "
                    f"whether it transfers to our instrument, which is the "
                    f"only question the reader actually has")

    if mode == "project":
        if not bundle.get("gap"):
            errors.append("no gap - the project document's whole point is to "
                          "make the gap unavoidable")
        if not bundle.get("floats"):
            warnings.append("no float set; the float set is the important "
                            "output of Stage 6")

    # --- the 3-4 sentence caps of 15.2, reported and never refused ----------
    #
    # Only in explore mode, because `summary.md` is what they are about and it
    # is destination 1's file. The per-paper `why` is checked in both, because
    # it is the same block in both files and it is the half a reader stops on.
    if mode == "explore":
        warnings.extend(_sentence_warnings("the takeaway",
                                           bundle.get("takeaway", "")))
    for p in lit:
        warnings.extend(_sentence_warnings(
            f"{_paper_label(p)}'s `why`", p.get("why", "")))

    return errors, warnings


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def render_background(b: dict) -> str:
    return "\n" + b.get("background", "").strip() + "\n\n"


def render_gap(b: dict) -> str:
    return "\n" + b.get("gap", "").strip() + "\n\n"


def render_question(b: dict) -> str:
    lines = ["", f"**Question:** {b.get('question', '').strip()}", "",
             f"**Hypothesis:** {b.get('hypothesis', '').strip()}", "",
             f"**Prediction if true:** {b.get('prediction_if_true', '').strip()}", "",
             f"**Prediction if false:** {b.get('prediction_if_false', '').strip()}",
             ""]
    return "\n".join(lines) + "\n"


def render_design(b: dict) -> str:
    d = b.get("design") or {}
    rows = [("Design", d.get("design", "")),
            ("Samples / n", d.get("samples_n", "")),
            ("Independent variable(s)", d.get("independent", "")),
            ("Outcome(s)", d.get("outcomes", "")),
            ("Controls", d.get("controls", "")),
            ("Statistical approach", d.get("stats", ""))]
    out = ["", "| | |", "|---|---|"]
    for k, v in rows:
        out.append(f"| {k} | {str(v).strip() or '**[FLAG: not decided]**'} |")
    out.append("")
    return "\n".join(out) + "\n"


def render_float_set(b: dict) -> str:
    out = ["", "| float | the claim it makes | status |", "|---|---|---|"]
    for f in b.get("floats") or []:
        out.append(f"| {f.get('label', '')} | {f.get('claim', '').strip()} | planned |")
    out.append("")
    out.append("Kept in step with `plan/captions.md`, which is the source of truth.")
    out.append("")
    return "\n".join(out) + "\n"


def render_narrative(b: dict) -> str:
    return "\n" + b.get("narrative", "").strip() + "\n\n"


def render_open_questions(b: dict) -> str:
    out = [""]
    for q in b.get("open_questions") or []:
        out.append(f"- [ ] {q}")
    unknowns = (b.get("ideas") or {}).get("unknowns") or []
    if unknowns:
        out.append("")
        out.append("Worth finding out (from the feasibility dialogue):")
        out.append("")
        for u in unknowns:
            out.append(f"- [ ] {u}")
    out.append("")
    return "\n".join(out) + "\n"


README_SECTIONS = {
    "background": render_background,
    "the gap": render_gap,
    "research question and hypothesis": render_question,
    "study design": render_design,
    "the float set": render_float_set,
    "narrative": render_narrative,
    "decisions and open questions": render_open_questions,
}


def render_outline(b: dict) -> dict:
    """Outline lines grouped by section heading, lowercased for matching."""
    grouped: dict[str, list[str]] = {}
    for row in b.get("outline") or []:
        sec = str(row.get("section", "")).strip()
        line = str(row.get("line", "")).strip()
        ev = str(row.get("evidence", "")).strip()
        if not sec or not line:
            continue
        # An empty bracket is a visible gap and is preferable to an invented
        # one; the drafter turns what it cannot resolve into a [FLAG: ...].
        text = f"- {line} [{ev}]" if ev else f"- {line} []"
        grouped.setdefault(sec.lower(), []).append(text)
    return grouped


def render_caption_block(f: dict) -> str:
    label = f.get("label", "").strip()
    folder = float_folder(f)
    claim = f.get("claim", "").strip()
    desc = f.get("description", "").strip()
    body = desc or ("[TODO: panel keys, n per group, error bars, and the test "
                    "used. The claim above is settled; this is not.]")
    return f"## {label} — {folder}\n**{claim}**\n{body}\n"


def render_data_contract(b: dict) -> str:
    out = []
    for df in b.get("data_files") or []:
        fname = df.get("file", "")
        out.append(f"## `data/raw/{fname}`")
        out.append("")
        note = str(df.get("note", "")).strip()
        if note:
            out.append(note)
            out.append("")
        out.append("| column | kind | units | allowed range / levels | notes |")
        out.append("|---|---|---|---|---|")
        for c in df.get("columns") or []:
            levels = c.get("levels")
            allowed = ", ".join(map(str, levels)) if levels else str(c.get("range", ""))
            consumed = str(c.get("float", "")).strip()
            notes = str(c.get("notes", "")).strip()
            if consumed:
                notes = (notes + " " if notes else "") + f"(feeds {consumed})"
            out.append(f"| `{c.get('name','')}` | {c.get('kind','')} | "
                       f"{c.get('units','')} | {allowed} | {notes} |")
        out.append("")
    return "\n".join(out) + "\n"


def render_literature_summary(b: dict, mode: str,
                              has_bib: bool = False) -> str:
    """The per-paper reading aid.

    `has_bib` exists because this header used to make two claims the writer
    could not keep. It said the PDFs "are in this folder, and `refs.bib` covers
    the same set" - and NEITHER mode wrote a `refs.bib` at all. The spec
    (15.1) recorded this as an explore-mode bug; measured while fixing it, the
    sentence was false in project mode too, and had been since the file was
    written. A record that names a file which does not exist is the cheapest
    kind of wrong to fix and the most corrosive to leave, because the next
    reader assumes the citations were captured somewhere.

    So: the caller writes the `.bib` and says whether it succeeded, and this
    function claims it only then. The default is False on purpose - a caller
    that forgets to say produces a summary that under-claims, which is the safe
    direction.
    """
    lit = b.get("literature") or []
    where = ("plan/relevant_literature/" if mode == "project" else "this folder")
    out = [f"# Relevant literature", "",
           f"The {len(lit)} papers that motivate this idea or establish the gap "
           f"- not everything the search returned. This is a reading aid, not a "
           f"literature review; it stops being useful the moment it becomes a "
           f"dump.", ""]
    if has_bib:
        out += [f"`refs.bib` in {where} covers this same set and was written "
                f"with this file.", ""]
    if mode == "project":
        out += [f"The PDFs - or metadata stubs, where no open-access copy "
                f"exists - belong in {where} beside this file. They are "
                f"retrieved by the skill, not by this writer.", ""]
    else:
        # specs/idea-generation.md 15.4: in an Ideas/ folder a metadata stub is
        # a file that looks like a paper and is not one, so a paper that was
        # not retrieved gets nothing.
        out += [f"A PDF is in {where} only where an open-access copy was "
                f"retrieved. A paper that was not retrieved gets no stub.", ""]
    out += [f"Written {TODAY} by `idea-generation`.", "", "---", ""]
    for p in lit:
        title = p.get("title", "").strip()
        authors = p.get("authors") or []
        first = authors[0] if authors else ""
        etal = " et al." if len(authors) > 1 else ""
        journal = p.get("journal_abbrev") or p.get("journal") or ""
        year = p.get("year", "")
        out.append(f"## {title}")
        out.append("")
        cite = f"{first}{etal} *{journal}* {year}.".strip()
        out.append(cite)
        # Only the identifiers this record actually carries. Printing an empty
        # "PMID " for a Surface Science paper reads as a missing lookup rather
        # than as a journal PubMed does not index.
        ids = []
        if p.get("pmid"):
            ids.append(f"PMID {p['pmid']}")
        if p.get("doi"):
            ids.append(f"doi:{p['doi']}")
        if p.get("arxiv"):
            ids.append(f"arXiv:{p['arxiv']}")
        if p.get("source"):
            ids.append(f"via {p['source']}")
        out.append(f"{' | '.join(ids)}")
        flags = p.get("flags") or []
        if flags:
            out.append("")
            for fl in flags:
                out.append(f"> **FLAG:** {fl}")
        out.append("")
        out.append(p.get("why", "").strip() or
                   "**[FLAG: no note on why this paper is here.]**")
        out.append("")
    return "\n".join(out)


def _sentence_count(text: str) -> int:
    return len(prose._sentences(_strip_comments(str(text or "")).strip()))


def _sentence_warnings(label: str, text: str) -> list[str]:
    """specs/idea-generation.md 15.2 - reported, never refused.

    A one-line takeaway and a page of prose fail the same way: the folder stops
    being the fifteen-second read it exists to be. The cap is judgment, so it
    warns; what it must not do is stay silent, because nobody re-reads a file
    they wrote from a conversation.
    """
    lo, hi = SENTENCE_CAP
    n = _sentence_count(text)
    if n == 0:
        return [f"{label} is empty - summary.md's whole job is the 3-4 "
                f"sentence version"]
    if n < lo:
        return [f"{label} is {n} sentence{'s' if n != 1 else ''}; the spec asks "
                f"for 3-4. Under two is a note, not a takeaway"]
    if n > hi:
        return [f"{label} is {n} sentences; the spec asks for 3-4. Past six it "
                f"stops being the short version of anything"]
    return []


def _paper_label(p: dict) -> str:
    """How a paper is named in a warning: whatever identifier it actually has."""
    for key, fmt in (("pmid", "PMID {}"), ("doi", "doi:{}"),
                     ("arxiv", "arXiv:{}")):
        if p.get(key):
            return fmt.format(p[key])
    return (p.get("title") or "untitled")[:48]


def render_summary_md(b: dict) -> str:
    """`Ideas/<name>/summary.md` - the file a human opens first.

    specs/idea-generation.md 15.2. Written before the other two so it sorts
    first in the folder listing, and deliberately NOT a second copy of
    `ideas.md`: the candidates that were dropped, the searches run and the
    feasibility answers stay there. This is the fifteen-second read.
    """
    name = str(b.get("query_name") or b.get("title") or "Idea").strip()
    takeaway = str(b.get("takeaway") or "").strip()
    lit = b.get("literature") or []
    out = [f"# {name}", "",
           f"Written {TODAY} by `idea-generation`.", ""]
    out.append(takeaway or
               "**[FLAG: author - the 3-4 sentence takeaway was not "
               "recorded.]**")
    out += ["", "## Guiding papers", ""]
    if not lit:
        out += ["**[FLAG: no papers were retrieved for this idea.]**", ""]
    for p in lit:
        title = str(p.get("title", "")).strip()
        authors = p.get("authors") or []
        first = authors[0] if authors else ""
        etal = " et al." if len(authors) > 1 else ""
        year = p.get("year", "")
        head = f"{first}{etal} {year}".strip() or title[:48]
        out += [f"### {head}", ""]
        out.append(str(p.get("why", "")).strip() or
                   "**[FLAG: no note on why this paper is here.]**")
        out.append("")
    out += ["## Citations", "",
            "For reading. `refs.bib` in this folder is the machine-readable "
            "copy.", ""]
    for p in lit:
        out.append(f"- {_plain_citation(p)}")
    if not lit:
        out.append("- none")
    out.append("")
    return "\n".join(out)


def _plain_citation(p: dict) -> str:
    """One line per paper, carrying the identifier it actually has.

    Not a bibliography - specs/idea-generation.md 15.2 is explicit that the
    machine-readable copy is `refs.bib`. So this is deliberately not routed
    through `format_citation`: it must render with nothing installed, and a
    second style would be a second thing to keep true.
    """
    authors = p.get("authors") or []
    first = authors[0] if authors else ""
    etal = " et al." if len(authors) > 1 else ""
    journal = p.get("journal_abbrev") or p.get("journal") or ""
    year = p.get("year", "")
    # "Briegel A et al." already ends in the period the join would add, and
    # `et al.` is exactly the case the sentence splitter has an abbreviation
    # list for - so the separator is applied to bits that do not already carry
    # one rather than blindly.
    bits = [x.rstrip(".") for x in (f"{first}{etal}".strip(),
                                    str(p.get("title", "")).strip(),
                                    f"*{journal}*" if journal else "",
                                    str(year)) if x]
    ident = _paper_label(p)
    return ". ".join(bits) + (f". {ident}" if ident else ".")


def render_refs_bib(b: dict) -> tuple[str, list[str]]:
    """A `.bib` for the retrieved set, or nothing and the reason why.

    The writer is `pubmed.format_citation(rec, "bibtex")` - the one measured
    through real pandoc by `tests/cite.py`, carrying the CASSI abbreviation and
    the article-number-for-page-range case. A second BibTeX writer here is the
    CAPTION_HEADING_RE problem with escapes in it, and TeX escaping is exactly
    the kind of thing that is wrong once and then wrong in every paper.

    It is imported lazily and by name because `pubmed.py` exits at import
    without `requests`, and `idea.py` must keep running on a bare Python
    install. When it cannot be imported there is no `.bib` and the caller is
    told - which is what keeps the summary's header honest rather than
    hopeful.
    """
    lit = b.get("literature") or []
    if not lit:
        return "", ["no literature in the bundle, so no refs.bib was written"]
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import pubmed as _pubmed         # noqa: PLC0415 - lazy on purpose
    except SystemExit:
        # pubmed.py exits with the pip line rather than raising. Catching
        # SystemExit is unusual and is the right thing here: a missing package
        # must cost one file, not the whole write.
        return "", ["refs.bib was NOT written: pubmed.py needs `requests` for "
                    "the BibTeX writer (python -m pip install requests). "
                    "Nothing else in this write is affected."]
    except ImportError as exc:
        return "", [f"refs.bib was NOT written: {exc}"]

    notes: list[str] = []
    entries, keys = [], []
    for p in lit:
        rec = {"authors": p.get("authors") or [],
               "year": p.get("year", ""),
               "title": p.get("title", ""),
               "journal": p.get("journal", ""),
               "journal_abbrev": p.get("journal_abbrev", ""),
               "issn": p.get("issn", ""),
               "volume": p.get("volume", ""), "issue": p.get("issue", ""),
               "pages": p.get("pages", ""),
               "elocation": p.get("elocation", ""),
               "doi": p.get("doi", ""), "pmid": p.get("pmid", "")}
        entry = _pubmed.format_citation(rec, "bibtex", notes)
        key = entry.split("{", 1)[1].split(",", 1)[0] if "{" in entry else ""
        if key in keys:
            # Two papers by the same first author in the same year whose titles
            # start with the same word. Rare, and a duplicate citekey makes
            # citeproc drop one silently, which is the failure this whole
            # toolkit is written against.
            suffix = "abcdefghijklmnopqrstuvwxyz"[keys.count(key) - 1]
            entry = entry.replace("{" + key + ",", "{" + key + suffix + ",", 1)
            notes.append(f"two entries wanted the citekey `{key}`; the second "
                         f"is `{key}{suffix}`")
        keys.append(key)
        entries.append(entry)
    header = (f"% Written {TODAY} by `idea-generation`. The retrieved set\n"
              f"% behind relevant_literature_summary.md - regenerate rather\n"
              f"% than hand-edit.\n")
    return header + "\n\n".join(entries) + "\n", notes


PROPOSED_HEADER = """\
# PROPOSED METHODS - none of this happened yet.
#
# Nothing here reaches a draft. writing-engine reads data/methods_facts.yml and
# only that file; a value missing there becomes a **[FLAG: author]** in the
# manuscript, which is the correct behaviour and is why this file needs no
# special handling anywhere downstream.
#
# When you actually do it: copy the `value` into the SAME key in
# methods_facts.yml, with whatever you really used, and delete the block here.
# A stale proposal sitting next to a real value is how the wrong one gets
# copied later - `scaffold.py check` reports that case.
#
# Every entry carries a `source`, and there are only two legal kinds: a
# retrievable citation with the section it came from, or a path under
# Resources/. A proposed value with no source is a guess.
#
# Written {today} by `idea-generation`.
"""


def render_methods_proposed(b: dict) -> str:
    """`data/methods_proposed.yml` - specs/idea-generation.md 11.2.

    Deliberately a separate file from `methods_facts.yml` rather than a
    `proposed:` block inside it. This is the mock-data problem again (5.5):
    separation plus a naming convention beats a distinction every future reader
    of one file has to honour. A borrowed `blot_time_s: 4` sitting in the field
    the drafter reads becomes a number in the methods section nobody measured,
    and it will look right because it came from a real paper.
    """
    proposed = b.get("methods_proposed") or {}
    out = [PROPOSED_HEADER.format(today=TODAY)]
    if not proposed:
        out.append("# Nothing was proposed for this idea.\n")
        return "".join(out)
    for block in sorted(proposed):
        entries = proposed[block] or {}
        out.append(f"{block}:\n")
        for key in sorted(entries):
            e = entries[key] or {}
            out.append(f"  {key}:\n")
            out.append(f"    value: {_yaml_scalar(e.get('value'))}\n")
            out.append(f"    source: {_yaml_scalar(e.get('source', ''))}\n")
            out.append(f"    why: {_yaml_scalar(e.get('why', ''))}\n")
        out.append("\n")
    return "".join(out)


def _yaml_scalar(value: object) -> str:
    """Quote what has to be quoted, and leave numbers alone.

    The comma case is the one that bit `learn.py`'s emitter: a free-text string
    containing a comma is harmless in block context and a separator in flow
    context. Nothing here is written in flow context, but a `why` line that
    starts with a character YAML reads as syntax would still break the file, so
    the rule is applied to the leading character rather than to commas.
    """
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if not text:
        return '""'
    if (text[0] in "-?:,[]{}#&*!|>'\"%@`" or text.strip() != text
            or "\n" in text or ": " in text or text.endswith(":")):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def render_ideas_md(b: dict) -> str:
    ideas = b.get("ideas") or {}
    out = [f"# Idea record", "",
           f"Every candidate considered, the feasibility answers as given "
           f"(including the ones that were 'idk'), and why the chosen idea won. "
           f"**The rejected candidates are the valuable part** - they are what "
           f"stops the same ground being re-covered in three months.", "",
           f"Written {TODAY} by `idea-generation`.", "", "---", ""]

    search = b.get("search") or {}
    if search:
        out += ["## What was searched", ""]
        for q in search.get("queries") or []:
            out.append(f"- `{q.get('query','')}` - {q.get('count','?')} hits"
                       + (f", {q['range']}" if q.get("range") else ""))
        if search.get("note"):
            out += ["", search["note"]]
        out.append("")

    out += ["## Candidates", ""]
    for c in ideas.get("candidates") or []:
        status = c.get("status", "")
        mark = {"chosen": " **- CHOSEN**", "dropped": " - dropped"}.get(status, "")
        out.append(f"### {c.get('gap','').strip()}{mark}")
        out.append("")
        if c.get("evidence"):
            out.append("**Evidence**")
            out.append("")
            for e in c["evidence"]:
                out.append(f"- {e}")
            out.append("")
        for key, label in (("why_open", "Why it is still open"),
                           ("what_it_takes", "What answering it would take"),
                           ("feasibility", "Feasibility, as answered"),
                           ("why_dropped", "Why it was dropped")):
            if c.get(key):
                out.append(f"**{label}.** {c[key]}")
                out.append("")

    if ideas.get("unknowns"):
        out += ["## Worth finding out", "",
                "The unknowns that would most change the ranking. Unknown is "
                "not infeasible - these are open, not closed.", ""]
        for u in ideas["unknowns"]:
            out.append(f"- {u}")
        out.append("")

    if ideas.get("chosen"):
        out += ["## Why the chosen idea won", "", str(ideas.get("why", "")).strip()
                or str(ideas["chosen"]), ""]

    if b.get("narratives"):
        out += ["## Narrative options considered", ""]
        for n in b["narratives"]:
            out.append(f"### {n.get('title','')}")
            out.append("")
            out.append(f"**The claim.** {n.get('claim','')}")
            out.append("")
            if n.get("framing"):
                out.append(f"**Framing.** {n['framing']}")
                out.append("")
            if n.get("journal"):
                out.append(f"**Target tier.** {n['journal']}")
                out.append("")
            if n.get("chosen"):
                out.append("Chosen.")
                out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# The mock-data generator - §5.5
# ---------------------------------------------------------------------------

MOCK_HEADER = '''#!/usr/bin/env python3
"""
generate_mock_data.py - hypothesis-shaped synthetic rows, one CSV per template.

Written {today} by `idea-generation`. Edit it freely; it is yours.

{hypothesis}

The rows below encode WHAT WE EXPECT TO SEE IF THE HYPOTHESIS HOLDS - not
uniform noise. That is the whole point: a figure built on these rows is a test
of the figure and of the claim, before a single sample is prepped. If the plot
does not visibly show what the caption asserts, either the figure design is
wrong or the claim is weaker than it sounded - and you learn that in an
afternoon instead of after a month of instrument time.

Every file it writes lands in data/mock_data/ with a _mock suffix, which is what
arms the safeguards: save_float() watermarks any figure downstream of one, and
create_floats.R refuses to build the co-author .docx from mock input at all.

  python data/mock_data/generate_mock_data.py            # default seed and n
  python data/mock_data/generate_mock_data.py --n 24     # power sanity check
  python data/mock_data/generate_mock_data.py --seed 7
"""

import argparse
import csv
import datetime
import math
import os
import random

OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# --- the hypothesis, as numbers ---------------------------------------------
# (group -> parameters). Continuous columns are (mean, sd); counts are a mean
# rate; proportions are (mean, sd) clipped to [0, 1]. Change these and the
# figures change with them - that is the intended way to ask "what effect size
# would this figure actually show?"

SPEC = {spec}

DEFAULT_N = {default_n}
DEFAULT_SEED = {default_seed}
'''

MOCK_BODY = '''

def poisson(rng, lam):
    """Knuth's method. Stdlib only, so this script needs nothing installed."""
    if lam <= 0:
        return 0
    target, k, p = math.exp(-lam), 0, 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= target:
            return k - 1


def draw(rng, kind, params):
    if params is None:
        # Not specified by the hypothesis. Left blank on purpose rather than
        # invented - a fabricated covariate is worse than a visible gap.
        return ""
    if kind == "continuous":
        return round(rng.gauss(params[0], params[1]), 4)
    if kind == "count":
        return poisson(rng, float(params))
    if kind == "proportion":
        return round(min(1.0, max(0.0, rng.gauss(params[0], params[1]))), 4)
    if kind in ("categorical", "ordinal"):
        return rng.choice(params)
    return ""


def build(name, table, n, seed):
    rng = random.Random(seed + sum(ord(c) for c in name))
    group_col = table["group_column"]
    groups = table["groups"]
    rows = []
    i = 0
    for group in groups:
        for _ in range(n):
            i += 1
            row = {}
            for col in table["columns"]:
                cname, kind = col["name"], col["kind"]
                if kind == "identifier":
                    row[cname] = f"{table['id_prefix']}{i:03d}"
                elif cname == group_col:
                    row[cname] = group
                elif kind == "datetime":
                    row[cname] = (datetime.date(2026, 1, 1) +
                                  datetime.timedelta(days=i)).isoformat()
                else:
                    row[cname] = draw(rng, kind, (col.get("by_group") or {}).get(group))
            rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=DEFAULT_N,
                    help="rows per group (default %(default)s)")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED,
                    help="fixed so a render is reproducible")
    args = ap.parse_args()

    for name, table in SPEC.items():
        rows = build(name, table, args.n, args.seed)
        stem = name[:-4] if name.endswith(".csv") else name
        out = os.path.join(OUT_DIR, stem + "_mock.csv")
        fields = [c["name"] for c in table["columns"]]
        with open(out, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        print(f"{os.path.basename(out)}  {len(rows)} rows x {len(fields)} cols")


if __name__ == "__main__":
    main()
'''


def build_mock_spec(bundle: dict) -> tuple[dict, list[str]]:
    """Turn the data contract plus per-column mock params into the SPEC dict
    the generated script carries. Also reports which columns had nothing said
    about them, so the gap is visible rather than silently filled."""
    spec: dict = {}
    unspecified: list[str] = []
    for df in bundle.get("data_files") or []:
        fname = df.get("file", "")
        cols = df.get("columns") or []
        group_col = df.get("group_column", "")
        groups: list = []
        for c in cols:
            if c.get("name") == group_col:
                groups = [str(x) for x in (c.get("levels") or [])]
        if not groups:
            groups = [""]
        entry = {"group_column": group_col, "groups": groups,
                 "id_prefix": df.get("id_prefix") or _slug(fname)[:3] + "_",
                 "columns": []}
        for c in cols:
            name, kind = c.get("name", ""), c.get("kind", "")
            col = {"name": name, "kind": kind}
            if kind in ("continuous", "count", "proportion"):
                by = c.get("mock")
                if isinstance(by, dict) and by:
                    col["by_group"] = {str(k): v for k, v in by.items()}
                else:
                    col["by_group"] = {}
                    unspecified.append(f"{fname}:{name}")
            elif kind in ("categorical", "ordinal") and name != group_col:
                levels = c.get("levels") or []
                col["by_group"] = {g: levels for g in groups} if levels else {}
                if not levels:
                    unspecified.append(f"{fname}:{name}")
            entry["columns"].append(col)
        spec[fname] = entry
    return spec, unspecified


def render_mock_script(bundle: dict) -> tuple[str, list[str]]:
    spec, unspecified = build_mock_spec(bundle)
    mock = bundle.get("mock") or {}
    hyp = str(mock.get("hypothesis", "")).strip()
    if not hyp:
        hyp = str(bundle.get("hypothesis", "")).strip()
    hyp_block = "\n".join("# " + line for line in
                          ("H1: " + hyp if hyp else
                           "**[FLAG: no hypothesis recorded]**").splitlines())
    if unspecified:
        hyp_block += ("\n#\n# Not specified by the hypothesis, written blank on "
                      "purpose:\n"
                      + "\n".join(f"#   {u}" for u in unspecified))
    body = MOCK_HEADER.format(
        today=TODAY, hypothesis=hyp_block,
        spec=json.dumps(spec, indent=4).replace("null", "None"),
        default_n=int(mock.get("n", 12)),
        default_seed=int(mock.get("seed", 1))) + MOCK_BODY
    return body, unspecified


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _existing_float_dirs(root: str) -> list[dict]:
    """Float folders already in the project, by canonical label.

    Matched on the label rather than the folder name: Fig02 and
    Fig02_symmetry_by_species are the same float, and creating the second
    beside the first would give one figure two folders and two caption blocks.
    """
    out = []
    for sub_dir, kind in (("figures", "Figure"), ("tables", "Table")):
        base = os.path.join(root, "plan", sub_dir)
        if not os.path.isdir(base):
            continue
        for nm in sorted(os.listdir(base)):
            if nm.startswith(("_", ".")) or not os.path.isdir(
                    os.path.join(base, nm)):
                continue
            m = FLOAT_DIR_RE.match(nm)
            if not m:
                continue
            out.append({"dir": nm,
                        "label": f"{kind} {m.group(2).upper()}{int(m.group(3))}"})
    return out


def _is_pristine_slot(root: str, sub_dir: str, folder: str, script: str,
                      template: str) -> bool:
    """Is this float folder still exactly what the scaffold put there?

    True only when it holds that one script and the script is byte-identical
    to the template. Anything else - an edited script, a rendered .png, a
    PowerPoint panel source - means somebody has worked in it, and the folder
    keeps the name they know it by.
    """
    path = os.path.join(root, "plan", sub_dir, folder)
    try:
        contents = os.listdir(path)
    except OSError:
        return False
    if contents != [script]:
        return False
    try:
        with open(os.path.join(path, script), "r", encoding="utf-8") as fh:
            return fh.read() == template
    except OSError:
        return False


def _float_template(sub_dir: str, script: str) -> str | None:
    """The copy-me script the scaffold ships, read from project_template/.

    Read rather than reproduced: a second copy of the starting point here is
    the copy nobody re-reads, and it would drift from the one the scaffold
    writes without anything reporting it.
    """
    path = os.path.join(TEMPLATE_DIR, "plan", sub_dir, "_template", script)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


class Writer:
    """Collects planned changes so --dry-run and the real run share one path.

    Nothing is written until every artifact has been rendered, so a failure
    half-way cannot leave a project in a state where some files describe the
    new idea and others still describe nothing.
    """

    def __init__(self, root: str, dry_run: bool = False):
        self.root = root
        self.dry_run = dry_run
        self.actions: list[dict] = []
        self._pending: list[tuple[str, str]] = []
        self._moves: list[tuple[str, str]] = []

    def _record(self, rel: str, kind: str, detail: str = "") -> None:
        self.actions.append({"path": rel, "action": kind, "detail": detail})

    def put(self, rel: str, content: str, kind: str, detail: str = "") -> None:
        self._pending.append((rel, content))
        self._record(rel, kind, detail)

    def skip(self, rel: str, why: str) -> None:
        self._record(rel, "skip", why)

    def move(self, src_rel: str, dst_rel: str, detail: str = "") -> None:
        """Stage a rename. Applied at commit, before any file is written.

        Staged rather than done on the spot for the reason the whole class
        exists: a failure half-way must not leave a project where some things
        describe the new idea and others still describe nothing. A rename is
        the one action here that is not a file write, and doing it eagerly
        would put it outside that guarantee.
        """
        self._moves.append((src_rel, dst_rel))
        self._record(src_rel, "move", detail or f"-> {dst_rel}")

    def commit(self) -> None:
        if self.dry_run:
            return
        for src_rel, dst_rel in self._moves:
            src = os.path.join(self.root, src_rel)
            dst = os.path.join(self.root, dst_rel)
            if os.path.exists(src) and not os.path.exists(dst):
                os.makedirs(os.path.dirname(dst) or self.root, exist_ok=True)
                os.rename(src, dst)
        for rel, content in self._pending:
            path = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(path) or self.root, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)


def fill_or_append(text: str, wanted: dict, marker: str,
                   template_rel: str) -> tuple[str, list[str]]:
    """Fill the sections that are still scaffolding; append under the rest.

    Never replaces content someone wrote. A section still identical to what the
    scaffold wrote gets filled in place; a section someone has typed into keeps
    every word of it and gains a dated block underneath.
    """
    tmpl = template_sections(template_rel)
    preamble, sections = _split_sections(text)
    filled, appended = [], []
    out: list[tuple[str, str]] = []
    for heading, body in sections:
        name = _heading_name(heading)
        if name in wanted:
            new = wanted[name]
            # Item 53: whatever level-1 region trails this section is not part
            # of it and must survive both branches.
            body, region = _split_off_region(body)
            tmpl_body, _ = _split_off_region(tmpl.get(name) or "")
            if _is_blank_section(body, tmpl_body if name in tmpl else None):
                body = new
                filled.append(name)
            else:
                body = body.rstrip("\n") + "\n\n" + marker + "\n" + new
                appended.append(name + " (appended)")
            if region:
                body = body.rstrip("\n") + "\n\n" + region
        out.append((heading, body))
    return _join_sections(preamble, out), filled + appended


def write_project(root: str, bundle: dict, w: Writer) -> dict:
    marker = MARKER.format(TODAY)
    report: dict = {}

    # --- project.yml ---------------------------------------------------------
    # Same precedence rule as scaffold.py: what the file already records wins,
    # and a contradiction is reported rather than applied. Otherwise the run and
    # the file disagree about the project's own title.
    yml_path = os.path.join(root, "project.yml")
    if os.path.isfile(yml_path):
        raw = _read(yml_path)
        existing = read_project_yml(root)
        proj = bundle.get("project") or {}
        ignored, set_keys = [], []
        for key in ("title", "short_name", "target_journal"):
            want = str(proj.get(key, "")).strip()
            if not want:
                continue
            have = existing.get(key, "").strip()
            if have and have != want:
                ignored.append(f"{key}: bundle says {want!r}, project.yml "
                               f"records {have!r}")
                continue
            if have == want:
                continue
            raw = re.sub(rf'^{key}:.*$', f'{key}: "{want}"', raw,
                         count=1, flags=re.M)
            set_keys.append(key)
        report["project_yml_set"] = set_keys
        report["project_yml_ignored"] = ignored
        if set_keys:
            w.put("project.yml", raw, "update", ", ".join(set_keys))
        else:
            w.skip("project.yml", "nothing to set")

    # --- plan/README.md ------------------------------------------------------
    readme_path = os.path.join(root, "plan", "README.md")
    if os.path.isfile(readme_path):
        wanted = {}
        for name, fn in README_SECTIONS.items():
            rendered = fn(bundle)
            if _strip_comments(rendered).strip():
                wanted[name] = rendered
        new_text, touched = fill_or_append(_read(readme_path), wanted, marker,
                                          "plan/README.md")
        if touched:
            w.put("plan/README.md", new_text, "fill", ", ".join(touched))
        else:
            w.skip("plan/README.md", "nothing to add")
        report["readme_sections"] = touched

    # --- plan/outline.md -----------------------------------------------------
    outline_path = os.path.join(root, "plan", "outline.md")
    grouped = render_outline(bundle)
    if os.path.isfile(outline_path) and grouped:
        text = _read(outline_path)
        preamble, sections = _split_sections(text)
        have = {_heading_name(h) for h, _ in sections}
        wanted = {k: "\n" + "\n".join(v) + "\n\n" for k, v in grouped.items()}
        new_text, touched = fill_or_append(text, wanted, marker, "plan/outline.md")
        # A narrative may need a section the scaffold did not ship (Methods,
        # Conclusions). Append those rather than dropping their lines silently.
        extra = [k for k in grouped if k not in have]
        if extra:
            tail = ""
            for k in extra:
                tail += f"\n## {k.title()}\n\n" + "\n".join(grouped[k]) + "\n"
            new_text = new_text.rstrip("\n") + "\n" + tail
            touched += [k + " (new section)" for k in extra]
        if touched:
            w.put("plan/outline.md", new_text, "fill", ", ".join(touched))
        else:
            w.skip("plan/outline.md", "nothing to add")
        report["outline_sections"] = touched

    # --- plan/captions.md ----------------------------------------------------
    captions_path = os.path.join(root, "plan", "captions.md")
    if os.path.isfile(captions_path):
        text = _read(captions_path)
        # Matched on the LABEL, not the folder reference: a block already
        # there for Figure 1 must not be written again because someone has
        # since renamed its folder to carry a slug.
        existing_labels = set()
        for line in text.splitlines():
            m = CAPTION_HEADING_RE.match(line)
            if m:
                existing_labels.add(f"{m.group(1)} {m.group(2)}")
        blocks, added = [], []
        for f in bundle.get("floats") or []:
            if str(f.get("label", "")).strip() in existing_labels:
                continue
            blocks.append(render_caption_block(f))
            added.append(f.get("label", float_folder(f)))
        if blocks:
            body = text.rstrip("\n") + "\n\n" + marker + "\n\n" + "\n".join(blocks)
            w.put("plan/captions.md", body, "append", ", ".join(added))
        else:
            w.skip("plan/captions.md", "every float already has a block")
        report["captions_added"] = added

    # --- the float folders themselves ---------------------------------------
    # A caption block naming Fig02 with no plan/figures/Fig02/ is a float the
    # preview reports as unrendered forever, because nothing ever builds it.
    # The float set is exactly what this bundle settles, so it makes the slots
    # to build them in, from the same _template the scaffold copies - one
    # starting point, so the two can never say different things.
    #
    # A folder that already holds work is never renamed - Fig02 and
    # Fig02_symmetry are the same float, and picking one up would move
    # somebody's work into a folder they did not name. An UNTOUCHED slot is
    # different: `scaffold` pre-creates Fig01..Fig04 holding nothing but the
    # copy-me script, and renaming one of those to carry the float's slug
    # costs nobody anything and is the whole reason the slug exists.
    #
    # "Untouched" is decided by comparing against the pristine template, not
    # by a heuristic - the same rule, and for the same reason, as deciding
    # whether a README section has been written in yet.
    made: list[str] = []
    renamed: list[dict] = []
    for f in bundle.get("floats") or []:
        folder = float_folder(f)
        parsed = FLOAT_DIR_RE.match(folder) if folder else None
        if not parsed:
            continue
        is_fig = parsed.group(1).lower().startswith("fig")
        sub_dir = "figures" if is_fig else "tables"
        script = "figure.R" if is_fig else "table.R"
        label = f"{'Figure' if is_fig else 'Table'} " \
                f"{parsed.group(2).upper()}{int(parsed.group(3))}"
        body = _float_template(sub_dir, script)

        here = [d for d in _existing_float_dirs(root) if d["label"] == label]
        if here:
            have = here[0]["dir"]
            if have != folder and body is not None and _is_pristine_slot(
                    root, sub_dir, have, script, body):
                if not os.path.exists(os.path.join(root, "plan", sub_dir,
                                                   folder)):
                    w.move(f"plan/{sub_dir}/{have}",
                           f"plan/{sub_dir}/{folder}", f"{label}, slug added")
                    renamed.append({"from": f"plan/{sub_dir}/{have}",
                                    "to": f"plan/{sub_dir}/{folder}"})
            continue

        rel = f"plan/{sub_dir}/{folder}/{script}"
        if os.path.exists(os.path.join(root, rel)) or body is None:
            continue
        art = str(f.get("art", "") or "").strip().lower()
        note = label
        if is_fig and art in ("drawn", "mixed"):
            # The marker goes in the script the slot is made from, so the ask
            # outlives the sentence it was said in and `float lint` can report
            # a drawn float that never got its art.
            body = (FLOAT_DRAWN_BANNER.format(
                marker=FLOAT_DRAWN_MARKER, n=int(parsed.group(3)))
                + "\n\n" + body)
            note = label + ", drawn - create-graphic-figure authors the art"
        w.put(rel, body, "create", note)
        made.append(f"plan/{sub_dir}/{folder}")
    report["float_folders"] = made
    report["float_folders_renamed"] = renamed

    # --- plan/ideas.md -------------------------------------------------------
    ideas_rel = "plan/ideas.md"
    ideas_path = os.path.join(root, ideas_rel)
    content = render_ideas_md(bundle)
    if os.path.isfile(ideas_path):
        w.put(ideas_rel, _read(ideas_path).rstrip("\n") + "\n\n---\n\n"
              + marker + "\n\n" + content, "append", "new round appended")
    else:
        w.put(ideas_rel, content, "create")

    # --- data/data_contract.md ----------------------------------------------
    contract_path = os.path.join(root, "data", "data_contract.md")
    if os.path.isfile(contract_path) and bundle.get("data_files"):
        text = _read(contract_path)
        preamble, sections = _split_sections(text)
        rendered = render_data_contract(bundle)
        out, replaced = [], False
        for heading, body in sections:
            # The scaffold ships one placeholder section, `data/raw/<file>.csv`
            # with an empty table. That is the one to replace; a real file's
            # section is content and is left alone.
            tmpl = template_sections("data/data_contract.md")
            if "<file>" in heading and _is_blank_section(
                    body, tmpl.get(_heading_name(heading))):
                out.append((rendered.rstrip("\n") + "\n\n", ""))
                replaced = True
            else:
                out.append((heading, body))
        if replaced:
            new_text = preamble + "".join(h + b for h, b in out)
        else:
            new_text = text.rstrip("\n") + "\n\n" + marker + "\n\n" + rendered
        w.put("data/data_contract.md", new_text, "fill" if replaced else "append",
              ", ".join(df.get("file", "") for df in bundle["data_files"]))

    # --- data/templates/*.csv ------------------------------------------------
    #
    # A folder the author dropped is skipped, out loud. Writing into it would
    # quietly undo the decision, and the next `scaffold.py check` would find
    # it back on disk with no record of why.
    dropped = set(dropped_dirs(root))
    made = []
    for df in bundle.get("data_files") or []:
        fname = df.get("file", "")
        rel = f"data/templates/{fname}"
        if "data/templates" in dropped:
            w.skip(rel, _dropped_note("data/templates"))
            continue
        if os.path.exists(os.path.join(root, rel)):
            w.skip(rel, "already exists")
            continue
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerow(
            [c.get("name", "") for c in df.get("columns") or []])
        w.put(rel, buf.getvalue(), "create",
              f"{len(df.get('columns') or [])} columns")
        made.append(fname)
    report["templates"] = made

    # --- data/mock_data/generate_mock_data.py --------------------------------
    if bundle.get("data_files"):
        rel = "data/mock_data/generate_mock_data.py"
        script, unspecified = render_mock_script(bundle)
        if "data/mock_data" in dropped:
            w.skip(rel, _dropped_note("data/mock_data"))
        elif os.path.exists(os.path.join(root, rel)):
            w.skip(rel, "already exists - edit it rather than regenerating")
        else:
            w.put(rel, script, "create",
                  f"{len(bundle['data_files'])} table(s)")
        report["mock_unspecified"] = unspecified

    # --- proposed methods ---------------------------------------------------
    #
    # A NEW file, always, so it is additive by construction (11.2). It is never
    # merged into methods_facts.yml, because a value in that file is a fact
    # about what we did, and a borrowed one becomes a number in the methods
    # section nobody measured - which will look right, because it came from a
    # real paper about a similar experiment.
    if bundle.get("methods_proposed"):
        prel = "data/methods_proposed.yml"
        n = sum(len(v or {}) for v in bundle["methods_proposed"].values())
        if os.path.exists(os.path.join(root, prel)):
            w.skip(prel, "already exists - proposals are copied out of it by "
                         "hand, so it is never regenerated over")
            report["methods_proposed_skipped"] = True
        else:
            w.put(prel, render_methods_proposed(bundle), "create",
                  f"{n} proposed value(s)")
        report["methods_proposed"] = n

    # --- the literature summary, and the .bib it now claims -----------------
    if bundle.get("literature"):
        bib, bib_notes = render_refs_bib(bundle)
        if bib:
            w.put("plan/relevant_literature/refs.bib", bib, "create",
                  f"{len(bundle['literature'])} entries")
        report["bib_notes"] = bib_notes
        rel = "plan/relevant_literature/relevant_literature_summary.md"
        content = render_literature_summary(bundle, "project",
                                            has_bib=bool(bib))
        if os.path.exists(os.path.join(root, rel)):
            w.put(rel, _read(os.path.join(root, rel)).rstrip("\n")
                  + "\n\n---\n\n" + marker + "\n\n" + content, "append")
        else:
            w.put(rel, content, "create", f"{len(bundle['literature'])} papers")

    return report


def write_explore(root: str, bundle: dict, w: Writer) -> dict:
    """Explore-only mode: the record and the literature, nothing else.

    No data/, no plan/, no float scripts - the folder exists to hold thinking
    that has not earned a project directory yet.

    Three files rather than two, per specs/idea-generation.md 15: `summary.md`
    (the fifteen-second read, written first so it sorts first in the folder
    listing), the per-paper summary, and `refs.bib`. The `.bib` is what makes
    the folder upgradable - 5.7 hands it to a new project by MOVING its
    literature rather than re-downloading it - and it is what makes the
    summary's header sentence true rather than hopeful.
    """
    report: dict = {}
    w.put("summary.md", render_summary_md(bundle), "create")
    if bundle.get("literature"):
        bib, bib_notes = render_refs_bib(bundle)
        if bib:
            w.put("refs.bib", bib, "create",
                  f"{len(bundle['literature'])} entries")
        report["bib_notes"] = bib_notes
        w.put("relevant_literature_summary.md",
              render_literature_summary(bundle, "explore", has_bib=bool(bib)),
              "create", f"{len(bundle['literature'])} papers")
    w.put("ideas.md", render_ideas_md(bundle), "create")
    return report


def run_mock_generator(root: str) -> dict:
    """Run the generator once so the project has usable rows immediately.

    This is also the end-to-end proof: if the script it just wrote cannot run,
    that is a problem now rather than the first time someone tries to build a
    figure.
    """
    rel = os.path.join("data", "mock_data", "generate_mock_data.py")
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        return {"ran": False, "note": "no generator"}
    proc = subprocess.run([sys.executable, path], cwd=root, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    written = sorted(f for f in os.listdir(os.path.join(root, "data", "mock_data"))
                     if f.endswith("_mock.csv"))
    return {"ran": proc.returncode == 0, "returncode": proc.returncode,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(), "files": written}


def write(root: str, bundle: dict, mode: str, dry_run: bool,
          run_mock: bool = True) -> dict:
    errors, warnings = validate(bundle, mode)
    res = {"path": os.path.abspath(root), "mode": mode, "dry_run": dry_run,
           "errors": errors, "warnings": warnings, "actions": []}
    if errors:
        res["wrote"] = False
        return res

    w = Writer(root, dry_run=dry_run)
    report = (write_project(root, bundle, w) if mode == "project"
              else write_explore(root, bundle, w))
    w.commit()
    res["actions"] = w.actions
    res["report"] = report
    res["wrote"] = not dry_run
    # Not run into a dropped folder: there is no generator to run, and
    # reporting its absence as a FAILED run is a wrong answer at exit 0.
    if (not dry_run and mode == "project" and run_mock
            and bundle.get("data_files")
            and "data/mock_data" not in dropped_dirs(root)):
        res["mock"] = run_mock_generator(root)
    return res


# ---------------------------------------------------------------------------
# merge - destination 2, a project that ALREADY has work in it
# ---------------------------------------------------------------------------
#
# specs/idea-generation.md 12.2-12.6. Every rule here exists because a merge
# that overwrote one of these files would destroy work that cannot be recovered
# from the folder: somebody has typed into `plan/outline.md`, filled in
# `methods_facts.yml` at an instrument, and possibly drafted.
#
# The invariant, and it is enforced rather than asserted: **there is no MODIFY
# row in a merge.** Every action is ADD, APPEND, NEW or PROPOSE, and
# `_check_merge_kinds` refuses to commit a plan containing anything else. "This
# overwrites nothing" has to be true, not reassuring.

MERGE_KINDS = ("add", "append", "new", "propose", "skip")

GUIDING_HEADING = "## Guiding papers"

MANUSCRIPT_REVISION_RE = re.compile(r"^manuscript_r\d+\.(docx|md)$", re.I)


def _check_merge_kinds(w: "Writer") -> None:
    bad = [a for a in w.actions if a["action"] not in MERGE_KINDS]
    if bad:
        raise AssertionError(
            "a merge produced a non-additive action, which the whole design "
            "forbids: " + ", ".join(f"{a['action']} {a['path']}" for a in bad))


def manuscript_revisions(root: str) -> list[str]:
    """Every built manuscript revision under `drafts/`, newest name last.

    This is 12.2's hard gate. Read off the folder rather than off
    `project.yml:revision`, because the counter moves when a round opens and
    what matters here is whether a document exists that somebody has read.
    Depth-limited to the journal folders, which is where `assemble` puts them.
    """
    drafts = os.path.join(root, "drafts")
    if not os.path.isdir(drafts):
        return []
    found = []
    for dirpath, dirnames, filenames in os.walk(drafts):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d != "obsolete"]
        if os.path.relpath(dirpath, drafts).count(os.sep) > 2:
            dirnames[:] = []
        for f in filenames:
            if MANUSCRIPT_REVISION_RE.match(f):
                found.append(os.path.relpath(os.path.join(dirpath, f), root)
                             .replace(os.sep, "/"))
    return sorted(found)


def _paper_keys(p: dict) -> tuple[str, str, str]:
    """(doi, pmid, normalized title) - the three things a paper is matched on."""
    matcher = _title_matcher()
    doi = str(p.get("doi", "")).strip()
    if matcher and matcher.get("clean_doi"):
        doi = matcher["clean_doi"](doi) or ""
    title = str(p.get("title", "")).strip()
    norm = matcher["normalize_title"](title) if matcher else ""
    return (doi.lower(), str(p.get("pmid", "")).strip(), norm)


_MATCHER_CACHE: dict = {}


def _title_matcher() -> dict:
    """`pubmed.py`'s matcher, or nothing.

    12.3: "Writing a third title matcher is how three matchers disagree."
    `clean_doi`, `normalize_title` and `title_similarity` are already tested,
    already shared by `scholar.py`, and are imported here rather than
    approximated. `pubmed.py` exits at import without `requests`, so when it
    cannot be loaded the answer is to dedup on identifiers only and SAY SO -
    not to fall back on a local title comparison, which would be the third
    matcher.
    """
    if _MATCHER_CACHE:
        return _MATCHER_CACHE.get("m") or {}
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import pubmed as _pubmed         # noqa: PLC0415 - lazy on purpose
    except (SystemExit, ImportError):
        _MATCHER_CACHE["m"] = None
        return {}
    _MATCHER_CACHE["m"] = {
        "clean_doi": _pubmed.clean_doi,
        "normalize_title": _pubmed.normalize_title,
        "title_similarity": _pubmed.title_similarity,
    }
    return _MATCHER_CACHE["m"]


BIB_DOI_RE = re.compile(r"doi\s*=\s*\{([^}]*)\}", re.I)
BIB_PMID_RE = re.compile(r"pmid\s*=\s*\{([^}]*)\}", re.I)
BIB_TITLE_RE = re.compile(r"title\s*=\s*\{(.*?)\}\s*,?\s*$",
                          re.I | re.M | re.S)


def existing_papers(root: str) -> dict:
    """What `plan/relevant_literature/` already holds, by identifier and title.

    Both halves are read - the `.bib` and the summary - because a project can
    have one without the other: the summary is written by this engine and the
    `.bib` only started being written on 2026-09-06.
    """
    lit_dir = os.path.join(root, "plan", "relevant_literature")
    out = {"dois": set(), "pmids": set(), "titles": [], "count": 0}
    bib = _read(os.path.join(lit_dir, "refs.bib"))
    summary = _read(os.path.join(lit_dir, "relevant_literature_summary.md"))
    matcher = _title_matcher()
    for raw in BIB_DOI_RE.findall(bib):
        doi = raw.strip()
        if matcher:
            doi = matcher["clean_doi"](doi) or doi
        out["dois"].add(doi.lower())
    out["pmids"].update(x.strip() for x in BIB_PMID_RE.findall(bib) if x.strip())
    titles = [t.strip() for t in BIB_TITLE_RE.findall(bib)]
    # The summary's own per-paper headings are `## <title>`.
    titles += [m.group(1).strip() for m in
               re.finditer(r"^##\s+(?!Guiding papers)(.+?)\s*$", summary, re.M)]
    for t in titles:
        if matcher:
            out["titles"].append((t, matcher["normalize_title"](t)))
        else:
            out["titles"].append((t, ""))
    for m in re.finditer(r"PMID[:\s]*(\d{4,9})", summary):
        out["pmids"].add(m.group(1))
    for m in re.finditer(r"doi:\s*(\S+)", summary):
        doi = m.group(1).strip().rstrip(".,)")
        if matcher:
            doi = matcher["clean_doi"](doi) or doi
        out["dois"].add(doi.lower())
    out["count"] = len({t for t, _ in out["titles"]})
    return out


def classify_papers(root: str, bundle: dict) -> dict:
    """Split the bundle's literature into new and already-present.

    Reported rather than silently skipped (12.3): "Briegel 2012 is already in
    refs.bib - adding a line to its summary block rather than a second entry."
    """
    have = existing_papers(root)
    matcher = _title_matcher()
    new, present, notes = [], [], []
    if not matcher:
        notes.append(
            "`pubmed.py` could not be imported (it needs `requests`), so "
            "papers were matched on DOI and PMID only. A paper already in the "
            "project under a differently-punctuated title would not be caught. "
            "No local title matcher was written for this: a third matcher is "
            "how three matchers disagree.")
    for p in bundle.get("literature") or []:
        doi, pmid, norm = _paper_keys(p)
        why = ""
        if doi and doi in have["dois"]:
            why = f"already present by DOI ({doi})"
        elif pmid and pmid in have["pmids"]:
            why = f"already present by PMID ({pmid})"
        elif norm and matcher:
            for raw, other in have["titles"]:
                if other and matcher["title_similarity"](norm, other) >= 0.85:
                    why = f"already present by title ({raw[:56]})"
                    break
        if why:
            present.append({"paper": p, "why": why,
                            "label": _paper_label(p)})
        else:
            new.append(p)
    return {"new": new, "present": present, "have": have, "notes": notes}


def render_merge_takeaway(b: dict) -> str:
    """One dated block for `plan/README.md` - 12.4's five parts."""
    name = str(b.get("query_name") or b.get("title") or "idea").strip()
    out = [f"## Idea, {TODAY} - {name}", ""]
    out += [f"**The claim.** {str(b.get('hypothesis') or b.get('claim') or '').strip() or '**[FLAG: author]**'}", ""]
    out += [f"**The gap, and what establishes it.** {str(b.get('gap','')).strip() or '**[FLAG: author]**'}", ""]
    measured = str(b.get("what_would_be_measured")
                   or b.get("question", "")).strip()
    out += [f"**What would need to be measured.** {measured or '**[FLAG: author]**'}", ""]
    out += ["**Proposed floats.**", ""]
    floats = b.get("floats") or []
    if floats:
        for f in floats:
            out.append(f"- {f.get('label','')} - {str(f.get('claim','')).strip()}")
    else:
        out.append("- none proposed")
    out.append("")
    unknowns = (b.get("ideas") or {}).get("unknowns") or []
    out += ["**Open questions.**", ""]
    if unknowns:
        for u in unknowns:
            out.append(f"- {u}")
    else:
        out.append("- none recorded")
    out.append("")
    return "\n".join(out)


def render_merge_candidates(b: dict) -> str:
    """The rejected candidates, for `plan/ideas.md`.

    5.6 argues these are the valuable part, and they are more valuable in a
    project that already exists: the ground they stop being re-covered is
    ground this project has already walked.
    """
    name = str(b.get("query_name") or b.get("title") or "idea").strip()
    ideas = b.get("ideas") or {}
    out = [f"## Idea, {TODAY} - {name}", "",
           "Candidates considered and not taken forward, with the feasibility "
           "answers as they were given.", ""]
    dropped = [c for c in (ideas.get("candidates") or [])
               if c.get("status") != "chosen"]
    if not dropped:
        out += ["No candidate was dropped in this round.", ""]
    for c in dropped:
        out += [f"### {str(c.get('gap','')).strip()}", ""]
        for key, label in (("why_open", "Why it is still open"),
                           ("what_it_takes", "What answering it would take"),
                           ("feasibility", "Feasibility, as answered"),
                           ("why_dropped", "Why it was dropped")):
            if c.get(key):
                out += [f"**{label}.** {c[key]}", ""]
    if ideas.get("unknowns"):
        out += ["### Worth finding out", "",
                "Unknown is not infeasible - these are open, not closed.", ""]
        out += [f"- {u}" for u in ideas["unknowns"]]
        out.append("")
    return "\n".join(out)


def render_guiding_block(b: dict, papers: list[dict]) -> str:
    """The `## Guiding papers` region of the summary, 12.3's shape.

    The HTML comment carries the date AND the idea that produced the block, so
    a second merge into the same project is legible rather than mysterious.
    Blocks accumulate; nothing is rewritten.
    """
    name = str(b.get("query_name") or b.get("title") or "idea").strip()
    out = [GUIDING_HEADING, "",
           f'<!-- idea-generation: {TODAY} - "{name}" -->', ""]
    for p in papers:
        title = str(p.get("title", "")).strip()
        ids = []
        if p.get("pmid"):
            ids.append(f"PMID {p['pmid']}")
        if p.get("doi"):
            ids.append(f"doi:{p['doi']}")
        if p.get("arxiv"):
            ids.append(f"arXiv:{p['arxiv']}")
        authors = p.get("authors") or []
        first = authors[0] if authors else ""
        etal = " et al." if len(authors) > 1 else ""
        head = " - ".join(x for x in
                          [f"{first}{etal} {p.get('year','')}".strip(),
                           " - ".join(ids)] if x)
        out += [f"### {head or title[:56]}", "", title, ""]
        for fl in p.get("flags") or []:
            out += [f"> **FLAG:** {fl}", ""]
        out.append(str(p.get("why", "")).strip()
                   or "**[FLAG: no note on why this paper is here.]**")
        out.append("")
    return "\n".join(out)


def caption_numbers(root: str) -> dict:
    """Which float numbers `plan/captions.md` already describes."""
    text = _strip_comments(_read(os.path.join(root, "plan", "captions.md")))
    out: dict = {"figure": set(), "table": set()}
    for line in text.splitlines():
        m = CAPTION_HEADING_RE.match(line.strip())
        if not m:
            continue
        lm = re.search(r"(figure|table)\s*(S?)(\d+)", line, re.I)
        if lm:
            kind = lm.group(1).lower()
            out[kind].add((lm.group(2).upper(), int(lm.group(3))))
    return out


def merge(root: str, bundle: dict, dry_run: bool = True,
          cap_ack: bool = False, check_refs: bool = False) -> dict:
    """Additively merge a settled idea into a project that already has work.

    Nothing is written until the caller has seen the whole plan; that is what
    `--dry-run` is for and it is the default in the CLI, because the one thing
    a merge must never be is a surprise.
    """
    errors, warnings = validate(bundle, "project")
    res: dict = {"path": os.path.abspath(root), "mode": "merge",
                 "dry_run": dry_run, "errors": errors, "warnings": warnings,
                 "actions": [], "blocked": [], "notes": [], "next": [],
                 "wrote": False}
    if not looks_scaffolded(root):
        res["errors"].append(
            f"{root} is not a scaffolded project (no project.yml and no "
            f"plan/). `merge` is for a project that already has work in it - "
            f"use `write` for a new one, or scaffold it first with "
            f"`setup-project-directory`.")
    if res["errors"]:
        return res

    # --- the hard gate, checked first --------------------------------------
    revisions = manuscript_revisions(root)
    drafting = bool(revisions)
    res["manuscript_revisions"] = revisions
    if drafting:
        res["notes"].append(
            f"{revisions[-1]} exists, so this project is being drafted. The "
            f"merge is restricted to the two purely additive operations - the "
            f"guiding papers and the README takeaway - and the outline and "
            f"methods proposals are written as a NOTE for you to apply, not "
            f"applied. An idea-stage merge that rewrote a drafted paper's "
            f"outline would be destructive in a way no preview makes safe.")

    split = classify_papers(root, bundle)
    res["notes"].extend(split["notes"])
    res["already_present"] = [{"label": p["label"], "why": p["why"]}
                              for p in split["present"]]

    # --- the cap, enforced by asking (12.3) --------------------------------
    combined = split["have"]["count"] + len(split["new"])
    hi = CAPS["literature"][1]
    res["guiding_paper_count"] = {"already_there": split["have"]["count"],
                                  "adding": len(split["new"]),
                                  "combined": combined, "cap": hi}
    if combined > hi and not cap_ack:
        res["blocked"].append(
            f"the merge would leave {combined} papers in "
            f"plan/relevant_literature/, past the cap of {hi}. That folder "
            f"answers \"why is this paper worth writing\", and it stops "
            f"answering it the moment it becomes a dump. Show the combined "
            f"list, ask which to keep, and re-run with --cap-ack once the "
            f"user has chosen. Nothing is dropped silently and nothing "
            f"exceeds the cap silently.")
        return res
    if combined > hi:
        warnings.append(
            f"{combined} guiding papers, past the cap of {hi} - kept because "
            f"--cap-ack says the user chose to")

    w = Writer(root, dry_run=dry_run)
    lit_rel = "plan/relevant_literature/relevant_literature_summary.md"
    bib_rel = "plan/relevant_literature/refs.bib"

    # --- 1. guiding papers -------------------------------------------------
    #
    # 12.3: into plan/, and NEVER drafts/references.bib. The submission
    # bibliography is derived from what the manuscript cites and drops
    # everything else, so motivating papers written into it are either silently
    # dropped or pushed into a paper that does not cite them.
    if split["new"]:
        block = render_guiding_block(bundle, split["new"])
        existing = _read(os.path.join(root, lit_rel))
        if existing.strip():
            w.put(lit_rel, existing.rstrip("\n") + "\n\n" + block, "append",
                  f"{len(split['new'])} guiding paper block(s)")
        else:
            w.put(lit_rel,
                  render_literature_summary(bundle, "project", has_bib=True)
                  + "\n" + block, "add",
                  f"{len(split['new'])} paper(s)")
        sub = {"literature": split["new"]}
        bib, bib_notes = render_refs_bib(sub)
        res["notes"].extend(bib_notes)
        if bib:
            old_bib = _read(os.path.join(root, bib_rel))
            if old_bib.strip():
                w.put(bib_rel, old_bib.rstrip("\n") + "\n\n"
                      + "\n".join(l for l in bib.splitlines()
                                  if not l.startswith("%")).strip() + "\n",
                      "append", f"{len(split['new'])} entries")
            else:
                w.put(bib_rel, bib, "add", f"{len(split['new'])} entries")
        res["next"].append(
            f"python <tools>/scholar.py check-refs {bib_rel} - a merge that "
            f"breaks the project's bibliography must not be discovered three "
            f"weeks later by the writing engine")

    # --- 2. the takeaway ---------------------------------------------------
    for rel, content, detail in (
            ("plan/README.md", render_merge_takeaway(bundle),
             "1 dated block"),
            ("plan/ideas.md", render_merge_candidates(bundle),
             "the rejected candidates")):
        existing = _read(os.path.join(root, rel))
        if existing.strip():
            w.put(rel, existing.rstrip("\n") + "\n\n" + content, "append",
                  detail)
        else:
            w.put(rel, content, "add", detail)

    # --- 3 and 4. the plan: outline, captions, proposed methods -----------
    #
    # All three are the project's plan, and _merge_plan_files decides per
    # file whether writing it can displace anything. On a drafted project
    # none of them is applied - they go into one dated proposal note, which
    # is what 12.2's gate means by "offered as a written note".
    res.update(_merge_plan_files(root, bundle, w, drafting,
                                 revisions[-1] if revisions else ""))

    _check_merge_kinds(w)
    w.commit()
    res["actions"] = w.actions
    res["wrote"] = not dry_run
    res["warnings"] = warnings

    if check_refs and not dry_run:
        res["check_refs"] = _run_check_refs(root, bib_rel)
    return res


PROPOSAL_NOTE_HEAD = """\
# Proposal from `idea-generation`, {today} - "{name}"

**PROPOSED - not approved, and not applied anywhere.**

Nothing in this file has been written into the project. It is here because
{why}

To apply the outline, or any part of it:

    python <tools>/manuscript.py outline <project> --replace --proposed

which snapshots the current outline into `obsolete/notes/` first. Reordering
floats is a renumbering job, not a rewrite: `scaffold.py float renumber` moves
the float folders and the caption blocks together, and correctly-numbered
captions in the wrong document order build a coauthor document with the figures
in the wrong order - the kind of wrong that survives a proofread.

---

"""

WHY_DRAFTED = """\
`{revision}` exists, so this project is being drafted, and 12.2 of the
idea-generation spec restricts an idea-stage merge on a drafted paper to the
two purely additive operations: the guiding papers and the README takeaway. An
outline, a caption set or a methods file arriving underneath a paper somebody is
already writing is destructive in a way no preview makes safe."""

WHY_SETTLED = """\
`plan/outline.md` already holds paragraph lines somebody thought about, and
12.5 says a merge proposes rather than writes in place.

It is beside the file rather than inside it for a measured reason: `prose.py`
reads the proposed-outline banner **file-wide**, so appending a proposal to a
settled outline would declare the whole approved paragraph plan unapproved - and
every verdict computed against it inherits that."""


def _merge_plan_files(root: str, bundle: dict, w: "Writer", drafting: bool,
                      revision: str = "") -> dict:
    """The outline, the captions and the methods proposals - 12.2 and 12.5.

    All three are the project's *plan*, and all three are written into the
    project only when doing so cannot displace something. Otherwise they go
    into one dated proposal note, which is what 12.2 means by "offered as a
    written note, not applied": one artifact rather than three half-applied
    ones, because a proposal split across three files is a proposal nobody can
    accept or discard as a unit.
    """
    out: dict = {"outline": {}, "captions": {}, "proposal_note": ""}
    lines = bundle.get("outline") or []
    floats = bundle.get("floats") or []
    proposed = bundle.get("methods_proposed") or {}

    rel = "plan/outline.md"
    raw = _read(os.path.join(root, rel))
    # `prose.OUTLINE_LINE_RE` is the contract for what counts as a paragraph
    # line, and it has to be matched PER LINE. A local `^\s*[-*]\s+\S` with
    # re.M reads the scaffolded template as an outline with content in it:
    # `\s` matches newlines, so a bare `-` placeholder followed by a blank
    # line and the next `## Results` heading satisfies it. Measured on a fresh
    # scaffold - every empty outline came back as "has lines", which sent the
    # proposal beside the file in the one case the spec says to write it in.
    has_lines = any(prose.OUTLINE_LINE_RE.match(ln)
                    for ln in _strip_comments(raw).splitlines())
    name = str(bundle.get("query_name") or bundle.get("title")
               or "idea").strip()

    grouped = render_outline(bundle)
    blocks = ["## " + sec.title() + "\n\n" + "\n".join(rows)
              for sec, rows in grouped.items()]
    body = ("# Structure\n\n" + "\n\n".join(blocks) + "\n") if blocks else ""

    # --- captions: which stubs could be appended, and which may not --------
    have = caption_numbers(root)
    stubs, left_alone = [], []
    for f in floats:
        label = str(f.get("label", "")).strip()
        lm = re.match(r"(figure|table)\s*(S?)(\d+)", label, re.I)
        if not lm:
            continue
        kind, s, num = (lm.group(1).lower(), lm.group(2).upper(),
                        int(lm.group(3)))
        if (s, num) in have.get(kind, set()):
            left_alone.append(f"{label} already has a caption block - "
                              f"untouched")
            continue
        highest = max((n for ss, n in have.get(kind, set()) if ss == s),
                      default=0)
        if num <= highest:
            # Document order in captions.md IS manuscript order:
            # read_captions() assigns `order` by position. Appending Figure 2
            # after Figure 5 writes a file whose headings read 1, 2, 3 in the
            # order 2, 3, 1 - every caption numbered correctly and the figures
            # in the wrong order. Reported, never written.
            left_alone.append(
                f"{label} would have to be inserted before "
                f"{kind.title()} {highest}, and appending it would put the "
                f"caption blocks out of manuscript order. Renumber with "
                f"`scaffold.py float renumber`, which moves the folders and "
                f"the blocks together.")
            continue
        stubs.append(f)

    defer_plan = drafting or has_lines
    if drafting:
        why = WHY_DRAFTED.format(revision=revision or "a manuscript revision")
    else:
        why = WHY_SETTLED

    if not defer_plan:
        # --- the empty case needs no ceremony, and must leave the template
        # --- alone. fill_or_append is what write_project already uses: it
        # --- fills a section that is still scaffolding IN PLACE and never
        # --- replaces content someone wrote, so the file keeps its two region
        # --- rules and its instruction comment. Writing `body` over the top
        # --- would throw those away, and they are the only place the
        # --- `# Notes` guarantee is written down.
        if lines:
            wanted = {k: "\n" + "\n".join(v) + "\n\n"
                      for k, v in grouped.items()}
            text, touched = fill_or_append(raw, wanted, MARKER.format(TODAY),
                                           "plan/outline.md")
            present = {_heading_name(h) for h, _ in _split_sections(text)[1]}
            for k in [k for k in grouped if k not in present]:
                text = (text.rstrip("\n") + f"\n\n## {k.title()}\n\n"
                        + "\n".join(grouped[k]) + "\n")
                touched.append(k + " (new section)")
            if not prose.outline_text_proposed(text):
                # Claude's lines, and nobody has accepted them. The banner
                # goes at the top because prose.py reads it file-wide, and
                # every line in this file is proposed.
                text = (prose.OUTLINE_PROPOSED_MARK + "\n\n"
                        + text.lstrip("\n"))
            w.put(rel, text, "add",
                  f"{len(lines)} paragraph line(s) under the PROPOSED "
                  f"banner: " + ", ".join(touched))
            out["outline"] = {
                "state": "written into the empty outline, with the PROPOSED "
                         "banner",
                "path": rel, "sections": touched}
        else:
            out["outline"] = {"state": "nothing proposed"}

        if stubs:
            crel = "plan/captions.md"
            existing_cap = _read(os.path.join(root, crel))
            block = "\n".join(render_caption_block(f) for f in stubs)
            labels = ", ".join(str(f.get("label", "")) for f in stubs)
            if existing_cap.strip():
                w.put(crel, existing_cap.rstrip("\n") + "\n\n" + block,
                      "append", f"{len(stubs)} stub(s): {labels}")
            else:
                w.put(crel, block, "add", f"{len(stubs)} stub(s): {labels}")
        out["captions"] = {"added": [str(f.get("label", "")) for f in stubs],
                           "left_alone": left_alone}

        prel = "data/methods_proposed.yml"
        if proposed:
            n = sum(len(v or {}) for v in proposed.values())
            if os.path.exists(os.path.join(root, prel)):
                w.skip(prel, "already exists - a merge never regenerates over "
                             "a file whose values are copied out by hand")
            else:
                w.put(prel, render_methods_proposed(bundle), "new",
                      f"{n} proposed value(s)")
        return out

    # --- deferred: one note, carrying everything that was not applied ------
    note = [PROPOSAL_NOTE_HEAD.format(today=TODAY, name=name, why=why)]
    if body:
        note.append("## The outline this idea implies\n\n"
                    + body.replace("# Structure", "### Structure", 1) + "\n")
    if stubs:
        note.append("## Caption stubs that were NOT added\n\n"
                    + "\n".join(render_caption_block(f) for f in stubs)
                    + "\n")
    if left_alone:
        note.append("## Floats left alone, and why\n\n"
                    + "\n".join("- " + x for x in left_alone) + "\n")
    if proposed:
        note.append("## Proposed methods, NOT written to "
                    "`data/methods_proposed.yml`\n\n"
                    "Copy this into that file if you want it. Nothing reads "
                    "it either way: `writing-engine` reads "
                    "`data/methods_facts.yml` and only that file, so a value "
                    "missing there becomes a `**[FLAG: author]**` in the "
                    "manuscript, which is the correct behaviour.\n\n"
                    "```yaml\n" + render_methods_proposed(bundle)
                    + "```\n")
    if len(note) == 1:
        out["outline"] = {"state": "nothing proposed"}
        out["captions"] = {"added": [], "left_alone": left_alone}
        return out

    note_rel = f"plan/idea_proposal_{TODAY}.md"
    # Two merges in one day would otherwise write the same path twice, and the
    # second write would be an overwrite - the one thing a merge may never do.
    # Appended under its own rule instead, the same treatment 12.3 gives the
    # guiding-paper region and for the same reason: a second merge has to be
    # legible rather than mysterious.
    prior = _read(os.path.join(root, note_rel))
    text = "\n".join(note)
    if prior.strip():
        w.put(note_rel, prior.rstrip("\n") + "\n\n---\n\n" + text, "append",
              "a second proposal today, under its own rule")
    else:
        w.put(note_rel, text, "propose",
              "everything the merge did NOT apply, in one file")
    out["proposal_note"] = note_rel
    out["outline"] = {
        "state": ("deferred - the project is being drafted" if drafting
                  else "proposed beside the settled outline"),
        "path": note_rel}
    out["captions"] = {"added": [], "left_alone": left_alone,
                       "deferred": [str(f.get("label", "")) for f in stubs]}
    return out


def _run_check_refs(root: str, bib_rel: str) -> dict:
    """Hand the merged `.bib` to `scholar.py check-refs` and report.

    Shelled out rather than imported: `scholar.py` needs the network and
    `requests`, and a merge must not fail because a verification could not run.
    """
    engine = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "scholar.py")
    path = os.path.join(root, bib_rel)
    if not os.path.isfile(path):
        return {"ran": False, "note": "no refs.bib to check"}
    try:
        proc = subprocess.run([sys.executable, engine, "check-refs", path,
                               "--json"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ran": False, "note": f"{type(exc).__name__}: {exc}"}
    try:
        payload = json.loads(proc.stdout or "")
    except ValueError:
        # Unparseable output is a check that did not run, not a check that
        # found nothing: an empty report read back as a clean bill of health
        # for every reference in the file (item 87).
        return {"ran": False, "returncode": proc.returncode,
                "note": f"scholar.py check-refs returned no readable JSON "
                        f"(rc={proc.returncode}): "
                        + ((proc.stderr or proc.stdout or "").strip()[:200]
                           or "nothing on either stream")}
    return {"ran": True, "returncode": proc.returncode, "report": payload,
            "stderr": (proc.stderr or "").strip()[:400]}


# ---------------------------------------------------------------------------
# Text output
# ---------------------------------------------------------------------------

def print_resolve(res: dict) -> None:
    print(f"{res['asked_for']}\n  {res['status']}: {res['action']}")
    for c in res.get("candidates", []):
        tag = "scaffolded" if c["scaffolded"] else "not scaffolded"
        print(f"    {c['path']}   ({tag}, similarity {c['similarity']})")
    if res.get("project"):
        for k in ("title", "field", "target_journal"):
            if res["project"].get(k):
                print(f"  {k:16} {res['project'][k]}")


def print_context(res: dict) -> None:
    print(res["path"])
    print(f"  scaffolded: {res['scaffolded']}")
    for k in ("title", "short_name", "field", "target_journal", "revision"):
        if res["project"].get(k):
            print(f"  {k:16} {res['project'][k]}")
    for label, key in (("plan/README.md", "readme_sections"),
                       ("plan/outline.md", "outline_sections")):
        secs = res.get(key) or {}
        filled = [k for k, v in secs.items() if v]
        print(f"  {label}: {len(filled)}/{len(secs)} sections already written"
              + (f" - {', '.join(filled)}" if filled else ""))
    if res["existing_floats"]:
        print(f"  captions.md: {len(res['existing_floats'])} float blocks")
        for f in res["existing_floats"]:
            print(f"    {f}")
    lab = res.get("lab") or {}
    if lab.get("resources"):
        print(f"  lab: {lab['lab']}")
        print(f"  Resources/: {lab.get('file_count', 0)} files in "
              f"{len(lab['subfolders'])} folders")
        for s in lab["subfolders"][:12]:
            print(f"    {s}/")
    elif lab.get("lab"):
        print(f"  lab: {lab['lab']} (no Resources/ folder)")


def _print_drop_zones(res: dict) -> None:
    """The drop-zone ladder, printed whether or not a lab folder
    resolved.

    4.5's detection half lives here: when the copy in use is a plugin
    install and its own `resources/` holds files, they are named ONE BY
    ONE. A count would be a warning; a list is something a member can act
    on.
    """
    zones = res.get("drop_zones") or []
    if not zones:
        return
    filled = [z for z in zones if z["files"]]
    print("")
    print("Drop-zones" + ("" if filled else " (all empty)") + ":")
    for z in zones:
        state = "" if z["exists"] else "  (not created yet)"
        print(f"  {z['rung']}. {z['source']:8} {z['path']}{state}")
        for rel in z["files"][:12]:
            title = z["titles"].get(rel)
            print(f"       {rel}" + (f"  -  {title}" if title else ""))
        if z["at_risk"]:
            print("       AT RISK - this folder is inside a plugin "
                  "install, so the next")
            print("       `/plugin update` DELETES these files:")
            for rel in z["at_risk_files"]:
                print(f"         {rel}")
            print("       Rescue them with: labpack.py config "
                  "--resources --adopt")


def print_resources(res: dict) -> None:
    if not res.get("resources"):
        print(f"No Resources/ folder found from {res.get('given', '')}")
        if res.get("note"):
            print("\n  " + res["note"])
        _print_drop_zones(res)
        return
    print(res["resources"])
    print(f"  found by: {res.get('resolved_from', '')}")
    print(f"  {res.get('file_count', 0)} file(s) in "
          f"{len(res.get('subfolders') or [])} subfolder(s)")
    if res.get("truncated"):
        print(f"  listing truncated to {len(res['files'])}")
    print()
    titles = res.get("titles") or {}
    for rel in res.get("files") or []:
        print("  " + rel)
        if titles.get(rel):
            print("      " + titles[rel])
    _print_drop_zones(res)
    print("\nRead at Stage 2 as well as Stage 5: these names ARE the search"
          "\nvocabulary. Read a document when it is about to inform a query or"
          "\na question, not before.")


def print_merge(res: dict) -> None:
    if res["errors"]:
        print("REFUSED:\n")
        for e in res["errors"]:
            print(f"  error  {e}")
        return
    print(f"Into: {res['path']}\n")
    if res["blocked"]:
        counts = res.get("guiding_paper_count") or {}
        print("NOTHING WAS WRITTEN - a decision is owed first:\n")
        for b in res["blocked"]:
            print(f"  ask  {b}")
        if counts:
            print(f"\n  {counts.get('already_there')} already there + "
                  f"{counts.get('adding')} new = {counts.get('combined')}, "
                  f"cap {counts.get('cap')}")
        return
    width = max((len(a["path"]) for a in res["actions"]), default=0)
    for a in res["actions"]:
        detail = f"  {a['detail']}" if a["detail"] else ""
        print(f"  {a['action'].upper():8} {a['path']:{width}}{detail}")
    for pres in res.get("already_present") or []:
        print(f"\n  {pres['label']} is {pres['why']} - not added twice")
    outline = res.get("outline") or {}
    if outline.get("state"):
        print(f"\n  outline: {outline['state']}"
              + (f" ({outline['path']})" if outline.get("path") else ""))
    caps = res.get("captions") or {}
    for line in caps.get("left_alone") or []:
        print(f"  captions: {line}")
    for note in res.get("notes") or []:
        print(f"\n  note  {note}")
    for wn in res.get("warnings") or []:
        print(f"  warning  {wn}")
    chk = res.get("check_refs")
    if chk:
        # "it ran" and "it did not run" must never render the same way. The
        # key is a dict either way, so testing it for truth printed
        # `check-refs ran: rc=None` for a verification that never happened,
        # and the note saying why was never shown (item 87).
        if chk.get("ran"):
            print(f"\n  check-refs ran: rc={chk.get('returncode')}")
        else:
            print(f"\n  check-refs DID NOT RUN - {chk.get('note') or 'no reason recorded'}"
                  f"\n    nothing in this merge has been verified against an "
                  f"index")
    for nxt in res.get("next") or []:
        print(f"\n  next  {nxt}")
    print("\nNothing was overwritten. Every row above is ADD, APPEND, NEW or"
          "\nPROPOSE; there is no MODIFY row in a merge, and the writer"
          "\nrefuses to commit one.")
    if res["dry_run"]:
        print("\nThis was a dry run. Re-run with --apply to write it.")


def print_write(res: dict) -> None:
    if res["errors"]:
        print("REFUSED - the bundle has problems that would put something wrong "
              "into the project:\n")
        for e in res["errors"]:
            print(f"  error  {e}")
        return
    verb = "Would write" if res["dry_run"] else "Wrote"
    print(f"{verb}: {res['path']}  [{res['mode']} mode]\n")
    width = max((len(a["path"]) for a in res["actions"]), default=0)
    for a in res["actions"]:
        detail = f"  {a['detail']}" if a["detail"] else ""
        print(f"  {a['action']:7} {a['path']:{width}}{detail}")
    report = res.get("report") or {}
    if report.get("project_yml_ignored"):
        print("\n  project.yml already records these - edit it there to change them:")
        for i in report["project_yml_ignored"]:
            print(f"    ignored {i}")
    if report.get("mock_unspecified"):
        print("\n  columns the hypothesis said nothing about, written blank "
              "rather than invented:")
        for u in report["mock_unspecified"]:
            print(f"    {u}")
    mock = res.get("mock")
    if mock:
        if mock.get("ran"):
            print(f"\n  mock data generated:")
            for line in (mock.get("stdout") or "").splitlines():
                print(f"    {line}")
        else:
            print(f"\n  mock generator FAILED (rc={mock.get('returncode')})")
            for line in (mock.get("stderr") or "").splitlines()[:10]:
                print(f"    {line}")
    if res["warnings"]:
        print()
        for wn in res["warnings"]:
            print(f"  warning  {wn}")
    if not res["dry_run"]:
        print("\nNext: check the float claims in plan/captions.md read as claims, "
              "\nthen `Rscript plan/render_all.R` once a figure script exists.")


def mock_only(root: str, bundle: dict, run: bool = True,
              force: bool = False, dry_run: bool = False) -> dict:
    """Write `data/mock_data/generate_mock_data.py` and run it, nothing else.

    The standalone door into the same generator `write` produces from a full
    idea bundle. It exists because the generator is wanted BEFORE there is an
    idea: `setup-project-directory` scaffolds a folder on day one and the
    figures want rows to draw long before the hypothesis is settled.

    One engine, two callers. The output contract is the part that must not be
    reimplemented - every file lands in data/mock_data/ with a `_mock` suffix,
    which is what arms save_float()'s watermark and create_floats.R's refusal
    to build the co-author .docx. A second generator written to a prompt would
    get that suffix right until the once it did not.

    The bundle is the `data_files` half of an idea bundle, and nothing else is
    read:

        {"mock": {"hypothesis": "...", "n": 12, "seed": 1},
         "data_files": [{"file": "measurements",
                         "group_column": "catalyst",
                         "columns": [
                           {"name": "catalyst", "kind": "categorical",
                            "levels": ["A", "B"]},
                           {"name": "yield_pct", "kind": "continuous",
                            "mock": {"A": [42, 3], "B": [71, 3]}}]}]}
    """
    rel = "data/mock_data/generate_mock_data.py"
    out: dict = {"path": rel, "action": "", "unspecified": [], "errors": [],
                 "dry_run": dry_run}

    if not bundle.get("data_files"):
        out["errors"].append(
            "bundle has no data_files - nothing to generate. Give at least one "
            "table with its columns and their kinds.")
        return out

    if "data/mock_data" in dropped_dirs(root):
        # Refused rather than written: this command's whole output lives in
        # the folder the author removed. The way back is one command.
        out["errors"].append(_dropped_note("data/mock_data"))
        return out

    script, unspecified = render_mock_script(bundle)
    out["unspecified"] = unspecified

    target = os.path.join(root, *rel.split("/"))
    exists = os.path.exists(target)
    if exists and not force:
        # Never overwrite: the generator is edited by hand once it exists, and
        # the edits ARE the hypothesis. Same rule as every other writer here.
        out["action"] = "skip"
        out["note"] = ("already exists - edit it rather than regenerating, "
                       "or pass --force")
        return out

    out["action"] = "overwrite" if exists else "create"
    if not dry_run:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(script)
        if run:
            out["mock"] = run_mock_generator(root)
            if not out["mock"].get("ran"):
                out["errors"].append(
                    "the generator was written but failed to run: "
                    + (out["mock"].get("stderr") or "")[:300])
    return out


def print_mock(res: dict) -> None:
    print(f"  {res['action']:9} {res['path']}")
    if res.get("note"):
        print(f"             {res['note']}")
    for u in res.get("unspecified") or []:
        print(f"  unspecified  {u} - written blank on purpose")
    m = res.get("mock")
    if m and m.get("ran"):
        print("")
        print("  mock data generated:")
        for line in (m.get("stdout") or "").splitlines():
            print(f"    {line}")
    for e in res.get("errors") or []:
        print(f"  ERROR  {e}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(prog="idea.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")

    # --json on either side of the subcommand, per the convention in CLAUDE.md.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="emit JSON instead of text")

    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("resolve", parents=[common],
                       help="find the project directory the user meant")
    r.add_argument("path")

    c = sub.add_parser("context", parents=[common],
                       help="what the project and its lab folder already record")
    c.add_argument("path")

    wcmd = sub.add_parser("write", parents=[common],
                          help="write the artifacts a settled idea produces")
    wcmd.add_argument("path")
    wcmd.add_argument("--bundle", required=True, help="the settled-idea JSON")
    wcmd.add_argument("--mode", default="project", choices=["project", "explore"])
    wcmd.add_argument("--dry-run", action="store_true",
                      help="report what would be written, write nothing")
    wcmd.add_argument("--no-mock-run", action="store_true",
                      help="write generate_mock_data.py but do not run it")

    rs = sub.add_parser("resources", parents=[common],
                        help="the lab's Resources/ folder, from any path")
    rs.add_argument("path")
    rs.add_argument("--max-files", type=int, default=40)

    mg = sub.add_parser("merge", parents=[common],
                        help="additively merge a settled idea into a project "
                             "that already has work in it")
    mg.add_argument("path")
    mg.add_argument("--bundle", required=True, help="the settled-idea JSON")
    mg.add_argument("--apply", action="store_true",
                    help="actually write. Without it this is a dry run, "
                         "because a merge must never be a surprise")
    mg.add_argument("--cap-ack", action="store_true",
                    help="the user has seen the combined guiding-paper list "
                         "and chosen to keep more than the cap")
    mg.add_argument("--check-refs", action="store_true",
                    help="run scholar.py check-refs over the merged .bib "
                         "afterwards (needs network)")

    m = sub.add_parser("mock", parents=[common],
                       help="write and run the mock-data generator, nothing else")
    m.add_argument("path")
    m.add_argument("--bundle", required=True,
                   help="JSON carrying data_files (and optionally mock:)")
    m.add_argument("--no-run", action="store_true",
                   help="write the generator but do not run it")
    m.add_argument("--force", action="store_true",
                   help="overwrite an existing generator")
    m.add_argument("--dry-run", action="store_true")

    args = p.parse_args()

    if args.cmd == "resolve":
        res = resolve(args.path)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if args.json \
            else print_resolve(res)
        return 0 if res["status"] == "scaffolded" else 1

    if args.cmd == "context":
        if not os.path.isdir(args.path):
            print(f"No such directory: {args.path}", file=sys.stderr)
            return 2
        res = context(args.path)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if args.json \
            else print_context(res)
        return 0

    if args.cmd == "resources":
        res = lab_resources(args.path)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print_resources(res)
        return 0 if res.get("resources") else 1

    if args.cmd == "merge":
        if not os.path.isdir(args.path):
            print(f"No such directory: {args.path}", file=sys.stderr)
            return 2
        try:
            with open(args.bundle, "r", encoding="utf-8") as fh:
                bundle = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            print(f"cannot read bundle: {e}", file=sys.stderr)
            return 2
        res = merge(args.path, bundle, dry_run=not args.apply,
                    cap_ack=args.cap_ack, check_refs=args.check_refs)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print_merge(res)
        if res["errors"]:
            return 2
        return 1 if res["blocked"] else 0

    if args.cmd == "mock":
        if not os.path.isdir(args.path):
            print(f"No such directory: {args.path}", file=sys.stderr)
            return 2
        try:
            with open(args.bundle, "r", encoding="utf-8") as fh:
                bundle = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            print(f"cannot read bundle: {e}", file=sys.stderr)
            return 2
        res = mock_only(args.path, bundle, run=not args.no_run,
                        force=args.force, dry_run=args.dry_run)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if args.json             else print_mock(res)
        return 2 if res["errors"] else 0

    if args.cmd == "write":
        if not os.path.isdir(args.path):
            print(f"No such directory: {args.path}", file=sys.stderr)
            return 2
        try:
            with open(args.bundle, "r", encoding="utf-8") as fh:
                bundle = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            print(f"cannot read bundle: {e}", file=sys.stderr)
            return 2
        res = write(args.path, bundle, args.mode, args.dry_run,
                    run_mock=not args.no_mock_run)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if args.json \
            else print_write(res)
        if res["errors"]:
            return 2
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
