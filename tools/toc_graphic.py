#!/usr/bin/env python3
"""toc_graphic.py - the TOC / graphical abstract as an authored float.

Spec: `specs/writing-engine.md` §7.2. Read it before changing behaviour here.

Most journals require a graphical abstract, they all size it differently, and it
is the one float nobody has a script for - so it gets made at midnight in
PowerPoint and pasted in at the wrong dimensions. This engine makes it an
artifact like any other: a `.pptx` the user edits, an `.R` script that renders it
at the journal's exact geometry, a content hash that says whether the render is
current, and a delimited block in `source_text/` that later rounds regenerate.

    toc_graphic.py status   <project> --journal LANGMUIR
    toc_graphic.py scaffold <project> --journal LANGMUIR [--suggestion s.json]
    toc_graphic.py examples <project> --journal LANGMUIR [--from guide.pdf]
    toc_graphic.py render   <project> --journal LANGMUIR
    toc_graphic.py wire     <project> --journal LANGMUIR

Four rules this engine turns on, all of them from §7.2:

  * It does nothing unless the journal asks for a graphic. A project targeting a
    journal that wants none must not acquire a folder it will never use.
  * The `.pptx` is written once and NEVER overwritten. It is the user's file the
    moment it exists, the same rule `plan/outline.md` gets.
  * Staleness is a content hash, never a modification time. OneDrive rewrites
    mtimes on sync, and a graphic wrongly declared current is the whole failure
    being designed against.
  * The engine never invents content. A scaffold with no suggestion writes the
    layout with `[TK: ...]` labels, like any other unrecorded value.

Standard library only, with one optional exception: `examples` reads the
journal's guidance PDF through `pypdf` when it is installed, and says so plainly
when it is not. `python-pptx` would be a dependency for what is a zip of nine
XML parts, and Pillow one for a PNG header; both are written by hand here.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from typing import TypeVar

for _stream in (sys.stdout, sys.stderr):
    # A redirected stream (a pipe, a StringIO under a harness) has no
    # reconfigure at all; asking for it by name keeps that case quiet.
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except ValueError:
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
TODAY = datetime.date.today().isoformat()

EMU_PER_INCH = 914400
DEFAULT_FOLDER = "toc_graphic"

# The generated block's delimiters. Everything between them is derived and is
# rewritten every round; everything outside is the user's and is never touched.
BEGIN_MARK = "<!-- BEGIN GENERATED: toc-graphic -->"
END_MARK = "<!-- END GENERATED: toc-graphic -->"

STATE_FILE = ".render_state.json"


# ---------------------------------------------------------------------------
# small shared helpers, kept identical to manuscript.py's
# ---------------------------------------------------------------------------

def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return ""


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _quote_closed(val: str) -> bool:
    """True when a value opening with a quote also closes with one. Kept
    identical to manuscript.py's."""
    q = val[:1]
    return len(val) > 1 and val.rstrip().endswith(q)


# Where a block list's ITEMS are filed, beside the bracketed string.
# Identical to manuscript.py's, and the two are held to the same values
# by tests/toc_graphic.py.
def _strip_yaml_comment(line: str) -> str:
    """The line with its trailing comment cut off, HONOURING QUOTES.

    Item 122. A `#` inside quotes is a character, and a journal's
    footnote-marker sequence is exactly that: an inline list containing
    `"#"` was cut at that element and the file then refused as a list that
    is never closed - so a sourced requirement made the whole file
    unreadable. The requirements.yml this toolkit ships even carries a
    comment telling the author never to write a `#` in a value, which is a
    workaround documented in the data because the reader could not do it.

    A `#` is also only a comment when it opens the line or follows
    whitespace, which is YAML's own rule and the reason `a#b` is a value.
    """
    out: list[str] = []
    quote = ""
    escaped = False
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if escaped:
                escaped = False
            elif quote == '"' and ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " 	"):
            break
        out.append(ch)
    return "".join(out)


LIST_ITEMS_PREFIX = "[]"


def read_flat_yml(path: str, problems: list[str] | None = None) -> dict:
    """Nested `key: value` out of a flat file, as
    {"toc_graphic.width_in": "3.25"}. requirements.yml is flat by design.

    This is a copy of `manuscript.py`'s reader, and copying it is deliberate -
    the engines are standalone and do not import each other. Keeping it in
    step is not: the copy here was the old two-level reader long after
    manuscript.py had learned both list spellings, so a
    `toc_graphic.file_formats` written as a block list

        file_formats:
          - TIF
          - EPS

    read as the empty string here and as `[TIF, EPS]` there. One requirements
    file, two answers, and the weaker answer silent. Any change to either copy
    belongs in both; `tests/toc_graphic.py` holds them to the same values.

    A list is accepted in either spelling and always read back bracketed. An
    inline list may run over several lines, and continuation lines are read to
    the closing `]`. One that is never closed cannot be read at all: pass a
    `problems` list to be told, and the callers that write a file from these
    values refuse rather than write the wrong one.

    A quoted scalar may wrap the same way and is read the same way, to its
    closing quote - `toc_graphic.prohibited_content` is five lines of a
    journal's own sentence and reached the engine as its first line only.

    Nesting deeper than two levels is kept as its full dotted path, and also
    filed under `<group>.<leaf>`.
    """
    out: dict = {}
    stack: list[tuple[int, str]] = []
    pending: tuple[str, list[str]] | None = None

    def flush() -> None:
        nonlocal pending
        if pending:
            key, items = pending
            if items:
                out[key] = "[" + ", ".join(items) + "]"
                # AND the items themselves. Kept identical to
                # manuscript.py's, and for its reason: the bracketed string
                # is LOSSY, so an item that itself contains a comma cannot
                # be split back out of it (item 129).
                out[LIST_ITEMS_PREFIX + key] = list(items)
            pending = None

    lines = _read(path).splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        opened_at = i + 1
        i += 1
        line = _strip_yaml_comment(raw).rstrip()
        if not line.strip():
            continue
        m = re.match(r"^(\s*)-\s+(.*)$", line)
        if m and pending:
            item = m.group(2).strip().strip('"').strip("'")
            if item:
                pending[1].append(item)
            continue
        flush()
        m = re.match(r"^(\s*)([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not m:
            continue
        indent = len(m.group(1))
        name, val = m.group(2), m.group(3).strip()
        # An inline list wrapped over several lines. Read to the closing
        # bracket; an unclosed one is reported rather than half-read.
        if val.startswith("[") and "]" not in val:
            while i < len(lines) and "]" not in val:
                cont = _strip_yaml_comment(lines[i]).strip()
                # A line that opens a key of its own is not a continuation,
                # which is what keeps an unclosed list from swallowing the
                # rest of the file.
                if re.match(r"^([A-Za-z_][\w-]*:(\s|$)|-\s)", cont):
                    break
                i += 1
                if cont:
                    val += " " + cont
            if "]" not in val and problems is not None:
                problems.append(
                    "%s line %d: the inline list for %r is never closed with "
                    "]. Everything after the line break cannot be read, so "
                    "the value is not the one written. Close the bracket, or "
                    "write it as a block list one `- item` per line."
                    % (os.path.basename(path), opened_at, name))
        # A quoted scalar wrapped over several lines, read to its closing
        # quote on the same rule as the list above.
        elif val[:1] in ('"', "'") and not _quote_closed(val):
            while i < len(lines) and not _quote_closed(val):
                cont = _strip_yaml_comment(lines[i]).strip()
                if re.match(r"^([A-Za-z_][\w-]*:(\s|$)|-\s)", cont):
                    break
                i += 1
                if cont:
                    val += " " + cont
            if not _quote_closed(val) and problems is not None:
                problems.append(
                    "%s line %d: the quoted value for %r is never closed. "
                    "Everything after the line break cannot be read, so the "
                    "value is not the one written."
                    % (os.path.basename(path), opened_at, name))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        key = ".".join([k for _, k in stack] + [name])
        out[key] = val.strip('"').strip("'")
        parts = key.split(".")
        if len(parts) > 2:
            out.setdefault(parts[0] + "." + parts[-1], out[key])
        stack.append((indent, name))
        if not val:
            pending = (key, [])
    flush()
    # A key that opened a block and got no items is still empty, not "[]".
    for k, v in list(out.items()):
        if v == "[]":
            out[k] = ""
    return out


def _prose_list(value: str) -> str:
    """`[TIF, EPS]` -> `TIF, EPS`. The reader hands a list back bracketed so
    both spellings read alike; a README sentence wants the words."""
    v = (value or "").strip()
    if v.startswith("[") and v.endswith("]"):
        return ", ".join(x.strip() for x in v[1:-1].split(",") if x.strip())
    return v


def journal_folder(name: str) -> str:
    """The journal's own abbreviation, uppercase - a deliberate exception to
    snake_case (spec 2.1). Slashes and spaces are still stripped."""
    cleaned = re.sub(r"[^\w\s-]", "", name).strip()
    return re.sub(r"[\s-]+", "_", cleaned)


def _snake(term: str) -> str:
    s = re.sub(r"[^\w\s-]", " ", term).strip().lower()
    s = re.sub(r"[\s\-/]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_") or DEFAULT_FOLDER


# ---------------------------------------------------------------------------
# where things are
# ---------------------------------------------------------------------------

def _project_kind(project: str) -> str:
    """`full` when the project has plan/ and drafts/; `trimmed` when it is a
    manuscript folder on its own.

    A trimmed project - source_text/ plus a journal folder, with the data and
    figures managed elsewhere - is a real and supported layout (spec 7.2.2). A
    module that hard-required plan/ would simply not run there.
    """
    if os.path.isdir(os.path.join(project, "plan")):
        return "full"
    return "trimmed"


# --- LAYOUT CONTRACT (verbatim in manuscript.py, prose.py, toc_graphic.py) ---
# Every command in this toolkit is standalone by design, so this resolver is
# repeated rather than imported, exactly like CAPTION_HEADING_RE.
# `tests/prose.py` compares the three byte for byte. Edit one, edit all three.

# What makes a folder a journal folder rather than a sibling of one. Any of
# these is enough: writing_config.yml is written by `init`, and the other two
# survive a project whose config was deleted or never committed.
JOURNAL_MARKERS = ("writing_config.yml", "journal_requirements", "edits")

# The live source text carries the round it holds: `source_text_r8`.
#
# One counter runs across every journal in a project (item 18), so a LANGMUIR
# folder that stopped at r5 can sit beside source text that has moved on to
# r8, and nothing on disk said so: `source_text/` looked the same on the day
# r5 was sent and on the day r8 was drafted. The label is the whole answer -
# `manuscript_r5.docx` next to `source_text_r8/` is visibly not what would
# build today.
#
# An unsuffixed `source_text/` is the layout that predates the label and is
# still read. `init` and `round` rename it forward rather than leaving two,
# because two folders holding a manuscript is the one thing this resolver has
# never been able to answer.
SOURCE_TEXT_RE = re.compile(r"^source_text(?:_r(\d+))?$")


def source_text_label(rnd: int) -> str:
    """The folder name for a given round; 0 means no round is known yet."""
    return "source_text_r%d" % rnd if rnd and rnd > 0 else "source_text"


def source_text_folders(d: str) -> list[tuple[int, str]]:
    """(round, name) for every source_text folder in `d`, oldest first.

    The unsuffixed one sorts as round 0: it predates the label, so any folder
    carrying one is newer than it. Callers take the LAST, never the first - a
    project part-way through the rename holds both, and the newest is live.
    """
    out: list[tuple[int, str]] = []
    if not os.path.isdir(d):
        return out
    for name in os.listdir(d):
        m = SOURCE_TEXT_RE.match(name)
        if m and os.path.isdir(os.path.join(d, name)):
            out.append((int(m.group(1) or 0), name))
    return sorted(out)


def _looks_like_journal(path: str) -> bool:
    return any(os.path.exists(os.path.join(path, m)) for m in JOURNAL_MARKERS)


def _is_drafting_folder(d: str) -> bool:
    """Does this directory hold a manuscript, rather than merely a folder?

    Two independent marks, because either can outlive the other. Written
    sections are the obvious one. A journal folder is the one that matters
    after a clean slate: a trimmed project whose prose has been deleted so the
    skill can redraft it still has `Langmuir/`, and without this second mark
    the project would silently flip back to expecting `drafts/` at the exact
    moment it was emptied - and the redraft would land somewhere else.

    An empty `source_text*/` with no journal beside it is deliberately NOT a
    drafting folder. Nothing has been created there yet, so it is scaffolded
    like any other bare directory.
    """
    if not os.path.isdir(d):
        return False
    for _rnd, name in source_text_folders(d):
        st = os.path.join(d, name)
        if any(f.endswith(".md") for f in os.listdir(st)):
            return True
    return any(_looks_like_journal(os.path.join(d, x))
               for x in os.listdir(d) if os.path.isdir(os.path.join(d, x)))


def drafts_dir(project: str) -> str:
    """The folder holding source_text/, the journal folders and the .bib.

    In a full project that is `drafts/`. In a **trimmed** project - source_text/
    plus a journal folder, with data and figures managed elsewhere (spec 7.2.2)
    - the project root IS the drafting folder and there is no drafts/ wrapper.
    That layout was supported here and in toc_graphic.py but hard-refused by
    manuscript.py and prose.py at 33 sites, which is why `status` on a real
    trimmed project reported five empty sections while 5262 words sat one
    directory up. A wrong answer at exit 0 is worse than the refusal it
    replaced.

    Resolution is by content, never by the folder name: the wrapper can be
    called anything (`manuscript/` is what the first real project used). When
    neither location holds a manuscript nothing has been created yet, so
    `drafts/` is returned as the layout `init` will build.
    """
    full = os.path.join(project, "drafts")
    if _is_drafting_folder(full):
        return full
    if _is_drafting_folder(project):
        return project
    return full


def is_trimmed(project: str) -> bool:
    """True when the project root is its own drafting folder (no drafts/)."""
    return os.path.abspath(drafts_dir(project)) == os.path.abspath(project)


def dpfx(project: str) -> str:
    """The `drafts/` prefix as it should appear in a message, or "".

    Every message naming a file the user is meant to open has to name the path
    they will actually find it at. A trimmed project told to edit
    `drafts/source_text/results.md` is sent looking for a folder that is not
    there, and a path that is wrong in a diagnostic is worse than no path at
    all - it is the diagnostic they will trust.
    """
    return "" if is_trimmed(project) else "drafts/"


def source_text_name(project: str) -> str:
    """The live source_text folder's own name, round label and all.

    The newest label wins. Nothing at all means nothing has been created here
    yet, and the unsuffixed name is what a caller would build - the same rule
    `drafts_dir` applies when neither location holds a manuscript.
    """
    found = source_text_folders(drafts_dir(project))
    return found[-1][1] if found else "source_text"


def source_text_dir(project: str) -> str:
    return os.path.join(drafts_dir(project), source_text_name(project))


def stpfx(project: str) -> str:
    """The live source_text folder as it should appear in a message.

    `drafts/source_text_r8/` in a full project, `source_text_r8/` in a trimmed
    one. Every diagnostic naming a file inside it runs through this, for the
    reason `dpfx` exists one line up: once the folder carries a round label
    there is no `source_text/` to open, and a path that is wrong in a
    diagnostic is worse than no path at all.
    """
    return dpfx(project) + source_text_name(project) + "/"


# The inbox is the PROJECT's, and since 2026-09-24 it sits beside source_text/
# rather than inside the journal folder: `drafts/edits/`.
#
# It moved because of what a coauthor actually does. They are sent a manuscript
# and they send one back, into whatever folder they were pointed at last time.
# Nobody outside the project knows or cares which journal this round is aimed
# at, and a return dropped in `drafts/IJROBP/edits/` after the paper moved to
# Langmuir is a file `ingest` does not read and nothing reports as missing.
# `retire-journal` carried a whole refusal path to walk the ledger across that
# boundary by hand; one inbox a level up makes the boundary disappear.
#
# THE PER-JOURNAL LOCATION IS STILL READ, AND IS MOVED FOR NOBODY. A project
# that already has `drafts/<Journal>/edits/` keeps using it - the same
# read-only rule the front-matter move follows (`front_matter_candidates`).
# `ingest` matches a return against the fingerprint the last round wrote, and
# an inbox that relocates under a round part-way through ingest loses that
# match. New projects and new journal folders get the new place.
EDITS_FOLDER = "edits"


def edits_dir(jdir: str) -> str:
    """The inbox for the project this journal folder belongs to.

    Takes the JOURNAL folder rather than the project, because that is what
    every caller already has in hand and because the older location is a child
    of it - one resolves from the other with no second argument to get wrong.
    """
    old = os.path.join(jdir, EDITS_FOLDER)
    if os.path.isdir(old):
        return old
    return os.path.join(os.path.dirname(os.path.normpath(jdir)), EDITS_FOLDER)


def edits_pfx(project: str, jdir: str) -> str:
    """The inbox as it should appear in a message, project-relative.

    `drafts/edits/` in a new project, `drafts/IJROBP/edits/` in one that
    predates the move, `edits/` in a trimmed one. Every diagnostic naming a
    file in the inbox runs through this, for the reason `dpfx` and `stpfx`
    exist above: a path that is wrong in a diagnostic is worse than no path at
    all - it is the diagnostic they will trust.
    """
    rel = os.path.relpath(edits_dir(jdir), project).replace(os.sep, "/")
    return rel + "/"


def bib_path(project: str) -> str:
    return os.path.join(drafts_dir(project), "references.bib")
# --- END LAYOUT CONTRACT ----------------------------------------------------


def journal_dir(project: str, journal: str) -> str:
    return os.path.join(drafts_dir(project), journal_folder(journal))


def requirements(project: str, journal: str,
                 problems: list[str] | None = None) -> dict:
    return read_flat_yml(os.path.join(journal_dir(project, journal),
                                      "journal_requirements",
                                      "requirements.yml"), problems)


# The `toc_graphic:` keys, canonical spelling first and the older engine-side
# spellings after it. The canonical names are the ones in
# manuscript.py:REQUIREMENTS_SKELETON, which is where a requirements run gets
# them; the aliases are read so a file already written keeps working.
#
# This table exists because the two halves disagreed: the file said
# `label_text` and the engine looked for `label`, so six sourced values were
# dropped on the floor while `sourced` still listed them as used (item 10).
TOC_KEYS: dict[str, tuple[str, ...]] = {
    "required": (),
    "journal_term": ("term",),
    "folder_name": (),
    "width_in": (),
    "height_in": (),
    "dpi_color": (),
    "dpi_bw": (),
    "file_format": ("file_formats",),
    "color_mode": (),
    "font_family": (),
    "font_size_pt_min": ("font_min_pt",),
    "font_size_pt_preferred": ("font_preferred_pt",),
    "label_text": ("label",),
    "placement": (),
    # Opposite senses, deliberately kept apart: `may_reuse_figure: false` and
    # `must_differ_from_figures: false` are contradictory statements, and
    # reading one as the other is how a journal that forbids reuse was
    # reported as permitting it.
    "may_reuse_figure": (),
    "must_differ_from_figures": (),
    "must_be_original": (),
    "prohibited_content": (),
}

_TOC_EMPTY = ("", "unknown", "null", "none", "~")


_Default = TypeVar("_Default")


class _TocReader:
    """Reads the `toc_graphic:` block and remembers which keys it used.

    `sourced` is what the engine actually read, not what the file happens to
    hold. Those were different things, and the report said the second.
    """

    def __init__(self, req: dict) -> None:
        self.req = req
        self.sourced: list[str] = []

    def raw(self, canonical: str) -> str | None:
        for name in (canonical,) + TOC_KEYS.get(canonical, ()):
            if ("toc_graphic." + name) not in self.req:
                continue
            value = str(self.req["toc_graphic." + name]).strip()
            if value.lower() in _TOC_EMPTY:
                continue
            self.sourced.append(name)
            return value
        return None

    def text(self, canonical: str, default: str = "") -> str:
        raw = self.raw(canonical)
        return default if raw is None else raw

    def number(self, canonical: str, default: float | None) -> float | None:
        raw = self.raw(canonical)
        if raw is None:
            return default
        m = re.search(r"-?\d+(?:\.\d+)?", raw)
        return float(m.group(0)) if m else default

    def flag(self, canonical: str, default: _Default = False) -> bool | _Default:
        """A yes/no field, or `default` where the journal did not say.

        `default` is deliberately not confined to a bool: a caller that needs
        to tell "the journal said no" from "the journal was silent" passes
        None or "unknown" and gets it back untouched.
        """
        raw = self.raw(canonical)
        if raw is None:
            return default
        return {"true": True, "yes": True,
                "false": False, "no": False}.get(raw.lower(), default)


def spec_from_requirements(req: dict) -> dict:
    """The `toc_graphic:` block of requirements.yml, resolved.

    Every field is sourced or falls back to a stated default, and `sourced`
    lists the keys the engine read - spec 4.3's rule, applied here.
    """
    r = _TocReader(req)
    term = r.text("journal_term") or "TOC/Abstract Graphic"
    folder = r.text("folder_name") or _snake(term)

    # `may_reuse_figure` is the journal's framing and wins where both are
    # present; `must_differ_from_figures` is the engine's and is its inverse.
    may_reuse = r.flag("may_reuse_figure", None)
    if may_reuse is None:
        must_differ = r.flag("must_differ_from_figures", False)
    else:
        must_differ = not may_reuse

    return {
        "required": r.flag("required", "unknown"),
        "term": term,
        "folder_name": _snake(folder),
        "width_in": r.number("width_in", 3.25),
        "height_in": r.number("height_in", 1.75),
        "dpi_color": int(r.number("dpi_color", 300) or 300),
        "dpi_bw": int(r.number("dpi_bw", 1200) or 1200),
        "file_formats": _prose_list(r.text("file_format")),
        "color_mode": r.text("color_mode"),
        "font_family": r.text("font_family") or "Arial",
        "font_min_pt": r.number("font_size_pt_min", 6.0),
        "font_preferred_pt": r.number("font_size_pt_preferred", 8.0),
        "label": r.text("label_text"),
        "placement": r.text("placement"),
        "must_differ_from_figures": must_differ,
        "must_be_original": r.flag("must_be_original", False),
        "prohibited_content": _prose_list(r.text("prohibited_content")),
        "sourced": sorted(set(r.sourced)),
    }


def graphic_dir(project: str, folder_name: str) -> str:
    """Under plan/ with the rest of the float authoring when there is a plan/,
    at the manuscript root otherwise (spec 7.2.2)."""
    if _project_kind(project) == "full":
        return os.path.join(project, "plan", folder_name)
    return os.path.join(project, folder_name)


def paths(project: str, journal: str) -> dict:
    unreadable: list[str] = []
    sp = spec_from_requirements(requirements(project, journal, unreadable))
    root = graphic_dir(project, sp["folder_name"])
    stem = sp["folder_name"]
    return {
        "spec": sp,
        "unreadable": unreadable,
        "root": root,
        "pptx": os.path.join(root, f"{stem}.pptx"),
        "png": os.path.join(root, f"{stem}.png"),
        "script": os.path.join(root, f"render_{stem}.R"),
        "readme": os.path.join(root, "README.md"),
        "examples": os.path.join(root, "examples"),
        "state": os.path.join(root, STATE_FILE),
        "section": os.path.join(source_text_dir(project), "toc_graphic.md"),
        "kind": _project_kind(project),
    }


# ---------------------------------------------------------------------------
# the .pptx writer - spec 7.2.4
# ---------------------------------------------------------------------------

REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PML = "http://schemas.openxmlformats.org/presentationml/2006/main"
DML = "http://schemas.openxmlformats.org/drawingml/2006/main"
_CT = "application/vnd.openxmlformats-officedocument"

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="png" ContentType="image/png"/>
<Override PartName="/ppt/presentation.xml" ContentType="{ct}.presentationml.presentation.main+xml"/>
<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="{ct}.presentationml.slideMaster+xml"/>
<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="{ct}.presentationml.slideLayout+xml"/>
<Override PartName="/ppt/slides/slide1.xml" ContentType="{ct}.presentationml.slide+xml"/>
<Override PartName="/ppt/theme/theme1.xml" ContentType="{ct}.theme+xml"/>
</Types>""".format(ct=_CT)

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="{rel}/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>""".format(rel=REL)

_PRESENTATION = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="{dml}" xmlns:r="{rel}" xmlns:p="{pml}">
<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>
<p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst>
<p:sldSz cx="{{cx}}" cy="{{cy}}"/>
<p:notesSz cx="{{cy}}" cy="{{cx}}"/>
</p:presentation>""".format(dml=DML, rel=REL, pml=PML)

_PRESENTATION_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="{rel}/slideMaster" Target="slideMasters/slideMaster1.xml"/>
<Relationship Id="rId2" Type="{rel}/slide" Target="slides/slide1.xml"/>
<Relationship Id="rId3" Type="{rel}/theme" Target="theme/theme1.xml"/>
</Relationships>""".format(rel=REL)

_EMPTY_TREE = (
    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
    '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
)

_SLIDE_MASTER = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldMaster xmlns:a="{dml}" xmlns:r="{rel}" xmlns:p="{pml}">
<p:cSld><p:spTree>{tree}</p:spTree></p:cSld>
<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2"
 accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6"
 hlink="hlink" folHlink="folHlink"/>
<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>
</p:sldMaster>""".format(dml=DML, rel=REL, pml=PML, tree=_EMPTY_TREE)

_SLIDE_MASTER_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="{rel}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
<Relationship Id="rId2" Type="{rel}/theme" Target="../theme/theme1.xml"/>
</Relationships>""".format(rel=REL)

_SLIDE_LAYOUT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:a="{dml}" xmlns:r="{rel}" xmlns:p="{pml}" type="blank" preserve="1">
<p:cSld name="Blank"><p:spTree>{tree}</p:spTree></p:cSld>
</p:sldLayout>""".format(dml=DML, rel=REL, pml=PML, tree=_EMPTY_TREE)

_SLIDE_LAYOUT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="{rel}/slideMaster" Target="../slideMasters/slideMaster1.xml"/>
</Relationships>""".format(rel=REL)

_SLIDE_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="{rel}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
</Relationships>""".format(rel=REL)

_SCHEME = [("dk1", "000000"), ("lt1", "FFFFFF"), ("dk2", "44546A"),
           ("lt2", "E7E6E6"), ("accent1", "4472C4"), ("accent2", "ED7D31"),
           ("accent3", "A5A5A5"), ("accent4", "FFC000"), ("accent5", "5B9BD5"),
           ("accent6", "70AD47"), ("hlink", "0563C1"), ("folHlink", "954F72")]

_THEME = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="{dml}" name="Office">
<a:themeElements>
<a:clrScheme name="Office">{colors}</a:clrScheme>
<a:fontScheme name="Office">
<a:majorFont><a:latin typeface="{{font}}"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>
<a:minorFont><a:latin typeface="{{font}}"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>
</a:fontScheme>
<a:fmtScheme name="Office">
<a:fillStyleLst>
<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
</a:fillStyleLst>
<a:lnStyleLst>
<a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>
<a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>
<a:ln w="19050"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>
</a:lnStyleLst>
<a:effectStyleLst>
<a:effectStyle><a:effectLst/></a:effectStyle>
<a:effectStyle><a:effectLst/></a:effectStyle>
<a:effectStyle><a:effectLst/></a:effectStyle>
</a:effectStyleLst>
<a:bgFillStyleLst>
<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
</a:bgFillStyleLst>
</a:fmtScheme>
</a:themeElements>
</a:theme>""".format(dml=DML,
                    colors="".join('<a:{n}><a:srgbClr val="{v}"/></a:{n}>'.format(n=n, v=v)
                                   for n, v in _SCHEME))


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _emu(inches: float) -> int:
    return int(round(float(inches) * EMU_PER_INCH))


def _sp_text(sid: int, name: str, box: dict, font: str, pt: float) -> str:
    """A text box. `pt` is points; OOXML wants hundredths of a point."""
    sz = int(round(float(pt) * 100))
    align = box.get("align", "ctr")
    bold = 1 if box.get("bold") else 0
    lines = box.get("lines") or [box.get("text", "")]
    paras = "".join(
        '<a:p><a:pPr algn="{al}"/><a:r>'
        '<a:rPr lang="en-US" sz="{sz}" b="{b}" dirty="0">'
        '<a:latin typeface="{f}"/></a:rPr><a:t>{t}</a:t></a:r></a:p>'.format(
            al=align, sz=sz, b=bold, f=_esc(font), t=_esc(line))
        for line in lines) or '<a:p><a:endParaRPr lang="en-US"/></a:p>'
    return (
        '<p:sp><p:nvSpPr><p:cNvPr id="{i}" name="{n}"/>'
        '<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>'
        '<p:txBody><a:bodyPr wrap="square" rtlCol="0" anchor="ctr" lIns="18000" '
        'rIns="18000" tIns="9000" bIns="9000"><a:normAutofit/></a:bodyPr>'
        '<a:lstStyle/>{p}</p:txBody></p:sp>'
    ).format(i=sid, n=_esc(name), x=_emu(box["x"]), y=_emu(box["y"]),
             cx=_emu(box["w"]), cy=_emu(box["h"]), p=paras)


def _sp_shape(sid: int, name: str, box: dict, prst: str, fill: str,
              line: str, font: str, pt: float) -> str:
    caption = box.get("text", "")
    if caption:
        body = ('<a:p><a:pPr algn="ctr"/><a:r>'
                '<a:rPr lang="en-US" sz="{sz}" dirty="0">'
                '<a:latin typeface="{f}"/></a:rPr><a:t>{t}</a:t></a:r></a:p>'
                ).format(sz=int(round(float(pt) * 100)), f=_esc(font),
                         t=_esc(caption))
    else:
        body = '<a:p><a:endParaRPr lang="en-US"/></a:p>'
    ln = ('<a:ln w="12700"><a:solidFill><a:srgbClr val="{l}"/></a:solidFill></a:ln>'
          .format(l=line)) if line else "<a:ln><a:noFill/></a:ln>"
    return (
        '<p:sp><p:nvSpPr><p:cNvPr id="{i}" name="{n}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="{prst}"><a:avLst/></a:prstGeom>'
        '<a:solidFill><a:srgbClr val="{f}"/></a:solidFill>{ln}</p:spPr>'
        '<p:txBody><a:bodyPr anchor="ctr" lIns="18000" rIns="18000"/><a:lstStyle/>'
        '{body}</p:txBody></p:sp>'
    ).format(i=sid, n=_esc(name), x=_emu(box["x"]), y=_emu(box["y"]),
             cx=_emu(box["w"]), cy=_emu(box["h"]), prst=prst, f=fill,
             ln=ln, body=body)


PALETTE = [("DDE7F5", "4472C4"), ("FBE3D6", "ED7D31"), ("E2EFD9", "70AD47"),
           ("FFF2CC", "FFC000"), ("EDEDED", "A5A5A5")]


def default_suggestion(spec: dict) -> dict:
    """The layout skeleton, with its labels marked [TK].

    The engine never invents content (spec 7.2.4). What it can lay out without
    knowing anything about the paper is the shape almost every graphical
    abstract has - a left state, an arrow, a right state, a title - and every
    string a human has to choose is a [TK] so it is visibly unfilled.
    """
    return {
        "title": "[TK: the one-line claim this graphic makes]",
        "stages": [
            {"label": "[TK: starting material]", "caption": "[TK: what it is]"},
            {"label": "[TK: what you did]", "caption": "[TK: the result]"},
        ],
        "footnote": "",
    }


def layout_shapes(sug: dict, spec: dict) -> tuple[str, list[dict]]:
    """Turn a suggestion into shapes. Returns the XML and the text inventory,
    so the type-size check reads the same numbers the slide was built with."""
    W = float(spec["width_in"])
    H = float(spec["height_in"])
    font = spec["font_family"]
    pt = float(spec["font_preferred_pt"] or 8)
    small = max(float(spec["font_min_pt"] or 6), pt - 1)

    pad = 0.08
    title_h = 0.26 if sug.get("title") else 0.0
    foot_h = 0.20 if sug.get("footnote") else 0.0
    band_y = pad + title_h
    band_h = max(H - band_y - foot_h - pad - 0.20, 0.35)
    cap_y = band_y + band_h + 0.02

    stages = sug.get("stages") or []
    n = max(len(stages), 1)
    arrow_w = 0.28 if n > 1 else 0.0
    total_arrows = arrow_w * (n - 1)
    box_w = max((W - 2 * pad - total_arrows) / n, 0.30)

    shapes: list[str] = []
    texts: list[dict] = []
    sid = 2

    if sug.get("title"):
        box = {"x": pad, "y": pad * 0.5, "w": W - 2 * pad, "h": title_h,
               "lines": [sug["title"]], "bold": True, "align": "ctr"}
        shapes.append(_sp_text(sid, "Title", box, font, pt))
        texts.append({"shape": "Title", "text": sug["title"], "pt": pt})
        sid += 1

    x = pad
    for i, stage in enumerate(stages):
        fill, line = PALETTE[i % len(PALETTE)]
        box = {"x": x, "y": band_y, "w": box_w, "h": band_h,
               "text": stage.get("label", "")}
        shapes.append(_sp_shape(sid, f"Stage {i + 1}", box, "roundRect",
                                fill, line, font, pt))
        if stage.get("label"):
            texts.append({"shape": f"Stage {i + 1}", "text": stage["label"],
                          "pt": pt})
        sid += 1
        if stage.get("caption"):
            cbox = {"x": x, "y": cap_y, "w": box_w, "h": 0.20,
                    "lines": [stage["caption"]], "align": "ctr"}
            shapes.append(_sp_text(sid, f"Caption {i + 1}", cbox, font, small))
            texts.append({"shape": f"Caption {i + 1}",
                          "text": stage["caption"], "pt": small})
            sid += 1
        x += box_w
        if i < len(stages) - 1:
            abox = {"x": x + 0.02, "y": band_y + band_h / 2 - 0.09,
                    "w": arrow_w - 0.04, "h": 0.18}
            shapes.append(_sp_shape(sid, f"Arrow {i + 1}", abox, "rightArrow",
                                    "4472C4", "", font, pt))
            sid += 1
            x += arrow_w

    if sug.get("footnote"):
        fbox = {"x": pad, "y": H - foot_h - pad * 0.5, "w": W - 2 * pad,
                "h": foot_h, "lines": [sug["footnote"]], "align": "ctr"}
        shapes.append(_sp_text(sid, "Footnote", fbox, font, small))
        texts.append({"shape": "Footnote", "text": sug["footnote"],
                      "pt": small})
        sid += 1

    return "".join(shapes), texts


def write_pptx(path: str, spec: dict, suggestion: dict) -> dict:
    shapes, texts = layout_shapes(suggestion, spec)
    slide = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<p:sld xmlns:a="{dml}" xmlns:r="{rel}" xmlns:p="{pml}">'
        '<p:cSld><p:spTree>{tree}{shapes}</p:spTree></p:cSld>'
        '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
    ).format(dml=DML, rel=REL, pml=PML, tree=_EMPTY_TREE, shapes=shapes)

    parts = {
        "[Content_Types].xml": _CONTENT_TYPES,
        "_rels/.rels": _ROOT_RELS,
        "ppt/presentation.xml": _PRESENTATION.format(
            cx=_emu(spec["width_in"]), cy=_emu(spec["height_in"])),
        "ppt/_rels/presentation.xml.rels": _PRESENTATION_RELS,
        "ppt/slideMasters/slideMaster1.xml": _SLIDE_MASTER,
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": _SLIDE_MASTER_RELS,
        "ppt/slideLayouts/slideLayout1.xml": _SLIDE_LAYOUT,
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": _SLIDE_LAYOUT_RELS,
        "ppt/slides/slide1.xml": slide,
        "ppt/slides/_rels/slide1.xml.rels": _SLIDE_RELS,
        "ppt/theme/theme1.xml": _THEME.format(font=_esc(spec["font_family"])),
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            # Fixed timestamps so a rebuild is byte-stable, the same reason
            # tests/make_docx_fixtures.py pins them.
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return {"path": path, "shapes": len(texts), "texts": texts}


# ---------------------------------------------------------------------------
# reading a .pptx back - the type-size check of spec 7.2.8
# ---------------------------------------------------------------------------

def inspect_pptx(path: str, spec: dict) -> dict:
    """Slide size, every text run with its point size, and the [TK] residue.

    Reads the file the user has been editing, not the one we wrote, which is
    the only reading worth having.
    """
    out: dict = {"exists": False, "texts": [], "too_small": [], "tk": [],
                 "size_in": None, "size_matches": None, "error": ""}
    if not os.path.isfile(path):
        return out
    out["exists"] = True
    try:
        with zipfile.ZipFile(path) as z:
            pres = z.read("ppt/presentation.xml").decode("utf-8", "replace")
            slides = sorted(n for n in z.namelist()
                            if re.match(r"ppt/slides/slide\d+\.xml$", n))
            body = "".join(z.read(n).decode("utf-8", "replace") for n in slides)
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        out["error"] = f"cannot read {os.path.basename(path)}: {exc}"
        return out

    m = re.search(r'<p:sldSz[^>]*cx="(\d+)"[^>]*cy="(\d+)"', pres)
    if m:
        w = int(m.group(1)) / EMU_PER_INCH
        h = int(m.group(2)) / EMU_PER_INCH
        out["size_in"] = [round(w, 3), round(h, 3)]
        out["size_matches"] = (abs(w - float(spec["width_in"])) < 0.02 and
                               abs(h - float(spec["height_in"])) < 0.02)

    # Default run size when a run carries no sz. PowerPoint's own default for a
    # text box is 18 pt; assuming the journal's preferred size instead would
    # hide exactly the failure this check exists to catch.
    min_pt = float(spec["font_min_pt"] or 6)
    for rpr, text in re.findall(r"<a:rPr\b([^>]*)/?>(?:(?!<a:t>).)*?<a:t>(.*?)</a:t>",
                                body, re.S):
        sz = re.search(r'\bsz="(\d+)"', rpr)
        pt = (int(sz.group(1)) / 100.0) if sz else 18.0
        txt = re.sub(r"&lt;", "<", re.sub(r"&amp;", "&", text)).strip()
        if not txt:
            continue
        entry = {"text": txt[:80], "pt": pt, "sz_declared": bool(sz)}
        out["texts"].append(entry)
        if pt < min_pt - 1e-9:
            out["too_small"].append(entry)
        if "[TK" in txt:
            out["tk"].append(entry)
    return out


# ---------------------------------------------------------------------------
# the render script - spec 7.2.5
# ---------------------------------------------------------------------------

R_SCRIPT = r'''# ---------------------------------------------------------------------------
# render_{stem}.R - {term} .pptx -> .png at the journal's exact geometry
#
# GENERATED by tools/toc_graphic.py, and yours to edit from here.
#
#   {width_in} x {height_in} in at {dpi} dpi  ->  {px_w} x {px_h} px
#
# Three backends, tried in order, and the script says which one it used:
#
#   1. PowerPoint via COM (Windows). Slide.Export takes explicit pixel width
#      and height, so the output lands on the requested size exactly rather
#      than approximately. Driven through PowerShell because R has no COM
#      bridge.
#   2. LibreOffice headless.
#   3. Nothing - and then it STOPS AND SAYS SO rather than producing a
#      wrong-sized image. Open the .pptx, File > Save As > PNG, and save it
#      next to this script as {stem}.png. Everything downstream is identical.
#
# A render script that silently produces nothing, or quietly produces the
# wrong size, is worse than one that asks you for thirty seconds of work:
# the wrong-sized image is the one that reaches the editor.
# ---------------------------------------------------------------------------

# Where am I? `sys.frame(1)$ofile` is only set under source(); under
# `Rscript this.R` it is NULL and the fallback silently becomes the working
# directory, which is wherever the caller happened to stand. Measured: that
# resolved to the toolkit folder and the script reported the .pptx missing
# when it was sitting right next to it. --file= covers Rscript, ofile covers
# source() from render_all.R, and getwd() is the last resort.
script_dir <- function() {{
  a <- commandArgs(trailingOnly = FALSE)
  hit <- grep("^--file=", a)
  if (length(hit)) return(dirname(normalizePath(sub("^--file=", "", a[hit[1]]))))
  of <- tryCatch(sys.frame(1)$ofile, error = function(e) NULL)
  if (!is.null(of)) return(dirname(normalizePath(of)))
  getwd()
}}

here     <- script_dir()
pptx     <- file.path(here, "{stem}.pptx")
png_out  <- file.path(here, "{stem}.png")
px_w     <- {px_w}
px_h     <- {px_h}

if (!file.exists(pptx)) stop("no {stem}.pptx in ", here, " - run toc_graphic.py scaffold first")

backend <- NA_character_

# --- 1. PowerPoint via COM --------------------------------------------------
pp_exe <- Sys.glob(c("C:/Program Files/Microsoft Office/root/Office*/POWERPNT.EXE",
                     "C:/Program Files (x86)/Microsoft Office/root/Office*/POWERPNT.EXE",
                     "C:/Program Files/Microsoft Office/Office*/POWERPNT.EXE"))
ps <- Sys.which("powershell")
if (length(pp_exe) > 0 && nzchar(ps)) {{
  script <- sprintf(paste(
    '$ErrorActionPreference="Stop";',
    '$pp = New-Object -ComObject PowerPoint.Application;',
    '$pres = $pp.Presentations.Open("%s", $true, $false, $false);',
    '$pres.Slides.Item(1).Export("%s", "PNG", %d, %d);',
    '$pres.Close(); $pp.Quit();'),
    normalizePath(pptx, winslash = "\\", mustWork = TRUE),
    normalizePath(png_out, winslash = "\\", mustWork = FALSE), px_w, px_h)
  ok <- suppressWarnings(system2(ps, c("-NoProfile", "-NonInteractive", "-Command", shQuote(script)),
                                 stdout = TRUE, stderr = TRUE))
  if (file.exists(png_out)) backend <- "powerpoint-com"
}}

# --- 2. LibreOffice headless ------------------------------------------------
if (is.na(backend)) {{
  soffice <- Sys.which("soffice")
  if (!nzchar(soffice)) {{
    cand <- Sys.glob(c("C:/Program Files/LibreOffice/program/soffice.exe",
                       "C:/Program Files (x86)/LibreOffice/program/soffice.exe",
                       "/usr/bin/soffice", "/usr/local/bin/soffice",
                       "/Applications/LibreOffice.app/Contents/MacOS/soffice"))
    if (length(cand) > 0) soffice <- cand[1]
  }}
  if (nzchar(soffice)) {{
    filt <- sprintf('png:impress_png_Export:{{"PixelWidth":{{"type":"long","value":%d}},"PixelHeight":{{"type":"long","value":%d}}}}', px_w, px_h)
    suppressWarnings(system2(soffice, c("--headless", "--convert-to", shQuote(filt),
                                        "--outdir", shQuote(here), shQuote(pptx)),
                             stdout = TRUE, stderr = TRUE))
    if (file.exists(png_out)) backend <- "libreoffice"
  }}
}}

# --- 3. no backend ----------------------------------------------------------
if (is.na(backend)) {{
  stop(paste0(
    "could not render {stem}.pptx automatically - neither PowerPoint nor ",
    "LibreOffice was found.\n",
    "  Do this instead, it takes about thirty seconds:\n",
    "    1. open ", pptx, "\n",
    "    2. File > Save As, choose PNG\n",
    "    3. save it as ", png_out, "\n",
    "  Then re-run this script's caller - everything downstream is identical.\n",
    "  The slide is already ", {width_in}, " x ", {height_in}, " in, so PowerPoint's own ",
    "export is the right size."))
}}

message("rendered {stem}.png via ", backend, "  (", px_w, " x ", px_h, " px)")
'''


def write_r_script(path: str, spec: dict, stem: str) -> str:
    dpi = int(spec["dpi_color"])
    body = R_SCRIPT.format(
        stem=stem, term=spec["term"],
        width_in=spec["width_in"], height_in=spec["height_in"], dpi=dpi,
        px_w=int(round(float(spec["width_in"]) * dpi)),
        px_h=int(round(float(spec["height_in"]) * dpi)))
    _write(path, body)
    return path


# ---------------------------------------------------------------------------
# staleness - spec 7.2.6
# ---------------------------------------------------------------------------

def read_state(p: dict) -> dict:
    try:
        return json.loads(_read(p["state"]) or "{}")
    except json.JSONDecodeError:
        return {}


def render_state(p: dict) -> dict:
    """Is the .png current for the .pptx on disk right now?

    Content hash, never mtime: OneDrive rewrites modification times on sync,
    and a graphic wrongly declared current is the failure being designed
    against.
    """
    has_pptx = os.path.isfile(p["pptx"])
    has_png = os.path.isfile(p["png"])
    state = read_state(p)
    now = _sha256(p["pptx"]) if has_pptx else ""
    recorded = state.get("pptx_sha256", "")
    if not has_png:
        verdict, why = "missing", "no .png has been rendered yet"
    elif not recorded:
        verdict, why = ("stale",
                        "a .png exists but nothing recorded which .pptx made "
                        "it - provenance unknown, so it is treated as stale")
    elif recorded != now:
        verdict, why = ("stale",
                        "the .pptx has changed since the .png was rendered")
    else:
        verdict, why = "current", "the .png matches the .pptx that made it"
    return {"verdict": verdict, "why": why, "pptx_sha256": now,
            "recorded_sha256": recorded, "rendered": state.get("rendered", ""),
            "backend": state.get("backend", ""),
            "has_pptx": has_pptx, "has_png": has_png}


def write_state(p: dict, backend: str, px: list[int]) -> None:
    _write(p["state"], json.dumps({
        "pptx_sha256": _sha256(p["pptx"]),
        "rendered": datetime.datetime.now().isoformat(timespec="seconds"),
        "backend": backend,
        "pixels": px,
    }, indent=2) + "\n")


# ---------------------------------------------------------------------------
# the generated block in source_text/ - spec 7.2.7
# ---------------------------------------------------------------------------

def _rel_from_section(section_path: str, png_path: str) -> str:
    rel = os.path.relpath(png_path, os.path.dirname(section_path))
    return rel.replace(os.sep, "/")


def render_block(p: dict) -> str:
    spec = p["spec"]
    label = spec["label"] or spec["term"]
    rel = _rel_from_section(p["section"], p["png"])
    return (
        f"{BEGIN_MARK}\n"
        f"<!-- Derived. Regenerated every round from the current geometry and "
        f"the current render.\n"
        f"     Edit the slide, not this block: "
        f"{os.path.basename(p['pptx'])} -->\n\n"
        f"![{label}]({rel}){{width={spec['width_in']}in}}\n\n"
        f"{END_MARK}"
    )


def wire(p: dict, force: bool = False) -> dict:
    """Write or refresh the generated block. Never touches anything outside it."""
    if not os.path.isfile(p["png"]) and not force:
        return {"wired": False, "reason":
                "no .png to point at yet - a block referencing a file that "
                "does not exist builds a .docx with a broken image in it, and "
                "pandoc does that without complaining"}
    text = _read(p["section"])
    block = render_block(p)
    if BEGIN_MARK in text and END_MARK in text:
        head, rest = text.split(BEGIN_MARK, 1)
        _, tail = rest.split(END_MARK, 1)
        new = head + block + tail
        action = "refreshed"
    elif text.strip() and (BEGIN_MARK in text) != (END_MARK in text):
        return {"wired": False, "reason":
                "one of the two generated-block markers is present without "
                "the other; refusing to guess where the block ends"}
    elif not text.strip():
        new = f"# {p['spec']['term']}\n\n{block}\n"
        action = "created"
    else:
        # The user has prose here and no markers. Two readings: they never had
        # a block, or they deleted it. Both mean the same thing - append once,
        # and if they remove it again that is an opt-out (spec 7.2.7).
        new = text.rstrip() + "\n\n" + block + "\n"
        action = "appended"
    if new == text:
        return {"wired": True, "action": "unchanged", "section": p["section"]}
    _write(p["section"], new)
    return {"wired": True, "action": action, "section": p["section"]}


def block_present(p: dict) -> bool:
    text = _read(p["section"])
    return BEGIN_MARK in text and END_MARK in text


# ---------------------------------------------------------------------------
# example images out of the journal's own guidance - spec 7.2.3
# ---------------------------------------------------------------------------

# Measured across three real author-guidance PDFs. The six labelled example
# graphics run 111-360 px on the short side, up to 2.8:1 aspect, and no smaller
# than 28,560 px2. The page furniture is nothing like that shape: an 18 x 1266
# px column rule (70:1) and two mastheads, 379 x 63 (6.0:1) and 504 x 79
# (6.4:1). Aspect is what actually separates them and it does so by a factor of
# 1.8; the short-side and area floors are the backstops.
#
# The floor sat at 80 px first, and that dropped the 79 px Langmuir masthead by
# a single pixel - the right answer reached by luck, which is not a rule. Every
# threshold now clears the real examples by at least 1.7x, and every rejected
# image is reported with *all* the rules it failed rather than the first, so a
# decision that rests on one thin margin is visible as one.
MIN_SHORT_PX = 64
MIN_AREA_PX = 15000
MAX_ASPECT = 5.0

# Two images belong to the same row of the journal's good/poor table when their
# TOP edges agree. Measured on that page: the four images across row 1 have
# tops spanning 7.7 pt while the gap to row 2 is 218 pt, so the tolerance is
# not delicate. Top edge and not bottom, because the cells are of unequal
# height and it is the tops that the layout aligns.
ROW_TOL_PT = 24.0

EXAMPLES_STATE = ".examples_state.json"
LABELS_FILE = "labels.json"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str, words: int = 5) -> str:
    parts = _SLUG_RE.sub("-", text.lower()).strip("-").split("-")
    return "-".join([w for w in parts if w][:words]) or "example"


def _png_bytes(width: int, height: int, raw: bytes, channels: int) -> bytes:
    """A minimal PNG encoder. `zlib` and `struct` are standard library; Pillow
    is not a dependency of this toolkit and one image writer is not worth
    making it one."""
    import struct
    import zlib
    ctype = {1: 0, 3: 2}[channels]
    stride = width * channels
    rows = b"".join(b"\x00" + raw[y * stride:(y + 1) * stride]
                    for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR",
                    struct.pack(">IIBBBBB", width, height, 8, ctype, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9))
            + chunk(b"IEND", b""))


def _matmul(a: list, b: list) -> list:
    """Compose two PDF 3x2 transformation matrices."""
    return [a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4],
            a[4] * b[1] + a[5] * b[3] + b[5]]


def _decode_image(obj) -> tuple[bytes | None, str, str]:
    """(bytes, extension, why-not). JPEG data is already a file and is written
    through untouched; Flate RGB/Gray is wrapped in a PNG header. Anything else
    is refused by name rather than written as a corrupt file."""
    filt = obj.get("/Filter")
    if isinstance(filt, list):
        filt = filt[-1] if filt else None
    filt = str(filt) if filt is not None else ""
    data = obj._data
    if filt == "/DCTDecode":
        return data, "jpg", ""
    if filt == "/JPXDecode":
        return data, "jp2", ""
    if filt not in ("/FlateDecode", "/Fl"):
        return None, "", f"unsupported filter {filt or 'none'}"
    if int(obj.get("/BitsPerComponent", 8)) != 8:
        return None, "", f"{obj.get('/BitsPerComponent')} bits per component"
    parms = obj.get("/DecodeParms")
    if isinstance(parms, list):
        parms = next((x for x in parms if x), None)
    if parms is not None:
        try:
            pred = int(parms.get_object().get("/Predictor", 1))
        except Exception:
            pred = 1
        if pred > 1:
            return None, "", f"PNG predictor {pred} on the image data"
    space = obj.get("/ColorSpace")
    space = str(space) if not isinstance(space, list) else "/Indexed"
    channels = {"/DeviceRGB": 3, "/DeviceGray": 1}.get(space)
    if channels is None:
        return None, "", f"unsupported color space {space}"
    import zlib
    try:
        raw = zlib.decompress(data)
    except zlib.error as exc:
        return None, "", f"cannot inflate: {exc}"
    w, h = int(obj["/Width"]), int(obj["/Height"])
    if len(raw) != w * h * channels:
        return None, "", (f"decoded {len(raw)} bytes for a {w}x{h} image "
                          f"needing {w * h * channels}")
    return _png_bytes(w, h, raw, channels), "png", ""


def placed_images(page, reader) -> list[dict]:
    """Every image drawn on the page, with the size and position it is drawn
    at. Position is what assigns a verdict later - the journal's examples sit
    in a good column and a poor column - so it is read from the content
    stream's CTM and never guessed from the drawing order."""
    from pypdf.generic import ContentStream

    res = page.get("/Resources")
    res = res.get_object() if res is not None else {}
    xobj = res.get("/XObject")
    xobj = xobj.get_object() if xobj is not None else {}
    if not xobj:
        return []

    out: list[dict] = []
    ctm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    stack: list[list] = []
    stream = ContentStream(page.get_contents(), reader)
    for operands, op in stream.operations:
        if op == b"q":
            stack.append(list(ctm))
        elif op == b"Q":
            ctm = stack.pop() if stack else [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        elif op == b"cm":
            try:
                ctm = _matmul([float(v) for v in operands], ctm)
            except (TypeError, ValueError):
                pass
        elif op == b"Do" and operands:
            name = str(operands[0])
            ref = xobj.get(name)
            if ref is None:
                continue
            obj = ref.get_object()
            if str(obj.get("/Subtype")) != "/Image":
                continue
            w_pt, h_pt = abs(ctm[0]), abs(ctm[3])
            out.append({
                "xobject": name,
                "px": (int(obj["/Width"]), int(obj["/Height"])),
                "x_pt": round(ctm[4], 1),
                "y_pt": round(ctm[5], 1),
                "w_pt": round(w_pt, 1),
                "h_pt": round(h_pt, 1),
                "top_pt": round(ctm[5] + h_pt, 1),
                "obj": obj,
            })
    return out


def _is_furniture(px: tuple[int, int]) -> str:
    """Every rule this image failed, or "" if it is a plausible example.

    All of them and not just the first: a rejection that holds by one rule with
    a thin margin should not read the same as one that fails three ways."""
    w, h = px
    short, area = min(w, h), w * h
    aspect = max(w, h) / max(1, min(w, h))
    why = []
    if short < MIN_SHORT_PX:
        why.append(f"short side {short} px is under the {MIN_SHORT_PX} px floor")
    if area < MIN_AREA_PX:
        why.append(f"area {area} px2 is under the {MIN_AREA_PX} px2 floor")
    if aspect > MAX_ASPECT:
        why.append(f"aspect {aspect:.1f}:1 is past the {MAX_ASPECT:g}:1 limit")
    return "; ".join(why)


def _assign_cells(kept: list[dict], dropped: list[dict],
                  page_width: float, page_height: float) -> None:
    """Give each image a (row, column) in the guidance page's table.

    The column boundary is the page's own vertical rule where it has one - a
    tall, thin graphic is a divider, not an example, so it is looked for among
    the images the size filter already rejected. Failing that, the page's
    midpoint. Rows come from clustering the top edges."""
    divider = None
    for d in dropped:
        if d["h_pt"] > 0.5 * page_height and d["w_pt"] < 20:
            if divider is None or d["h_pt"] > divider["h_pt"]:
                divider = d
    split = (divider["x_pt"] + divider["w_pt"] / 2) if divider \
        else page_width / 2
    for item in kept:
        centre = item["x_pt"] + item["w_pt"] / 2
        item["column"] = 1 if centre < split else 2
        item["divider"] = bool(divider)

    row = 0
    anchor = None
    for item in sorted(kept, key=lambda i: -i["top_pt"]):
        if anchor is None or abs(item["top_pt"] - anchor) > ROW_TOL_PT:
            row += 1
            anchor = item["top_pt"]
        item["row"] = row


def _guidance_pdfs(project: str, journal: str, p: dict) -> list[str]:
    """Where a saved author-guidance PDF plausibly lives. The examples folder
    first, because that is where the toc-graphic module puts one."""
    seen: list[str] = []
    for folder in (p["examples"],
                   os.path.join(project, journal, "journal_requirements",
                                "sources"),
                   os.path.join(project, "journal_requirements", "sources")):
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if name.lower().endswith(".pdf"):
                seen.append(os.path.join(folder, name))
    return seen


def _read_labels(path: str) -> dict:
    try:
        data = json.loads(_read(path))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def extract_examples(project: str, journal: str, from_pdf: str | None = None,
                     labels_path: str | None = None,
                     refresh: bool = False) -> dict:
    """Pull the journal's own example graphics out of its guidance PDF into
    examples/, and index them.

    The images are the journal showing what it wants. They are for looking at,
    never for reusing - see the note the index carries."""
    p = paths(project, journal)
    spec = p["spec"]
    res: dict = {"errors": [], "folder": os.path.relpath(p["root"], project)
                 .replace(os.sep, "/"), "images": [], "dropped": [],
                 "written": [], "kept": [], "pdfs": [], "labelled": False}

    if spec["required"] is False:
        res["errors"].append(
            f"REFUSED - {journal} does not require a {spec['term']}.")
        return res
    if spec["required"] == "unknown":
        res["errors"].append(
            f"REFUSED - toc_graphic.required is unknown for {journal}. "
            f"Sourced or unknown, never inferred.")
        return res

    try:
        from pypdf import PdfReader
    except ImportError:
        res["errors"].append(
            "pypdf is not installed, so the guidance PDF cannot be read: "
            "pip install pypdf. Everything else in this folder still works - "
            "the index is written without the images.")
        res["dependency"] = "pypdf"
        return res

    pdfs = [from_pdf] if from_pdf else _guidance_pdfs(project, journal, p)
    pdfs = [f for f in pdfs if f and os.path.isfile(f)]
    if not pdfs:
        res["errors"].append(
            "no author-guidance PDF found in examples/ or "
            "journal_requirements/sources/. Save the journal's own guidance "
            "document there first - it is the one source of examples that is "
            "unambiguously the journal's to distribute.")
        return res

    labels = {}
    lp = labels_path or os.path.join(p["examples"], LABELS_FILE)
    if os.path.isfile(lp):
        labels = _read_labels(lp)
    cells = {(int(c.get("row", 0)), int(c.get("column", 0))): c
             for c in labels.get("cells", []) if isinstance(c, dict)}
    res["labelled"] = bool(cells)
    res["labels_file"] = os.path.relpath(lp, project).replace(os.sep, "/") \
        if os.path.isfile(lp) else None

    os.makedirs(p["examples"], exist_ok=True)
    state_path = os.path.join(p["examples"], EXAMPLES_STATE)
    previous = _read_labels(state_path).get("images", [])
    prior_files = {i.get("file") for i in previous if isinstance(i, dict)}

    collected: list[dict] = []
    for pdf in pdfs:
        try:
            reader = PdfReader(pdf)
        except Exception as exc:
            res["errors"].append(f"cannot read {os.path.basename(pdf)}: {exc}")
            continue
        res["pdfs"].append(os.path.basename(pdf))
        for page_no, page in enumerate(reader.pages, start=1):
            items = placed_images(page, reader)
            if not items:
                continue
            keep, drop = [], []
            for item in items:
                why = _is_furniture(item["px"])
                (drop if why else keep).append(item)
                if why:
                    item["why"] = why
            try:
                width = float(page.mediabox.width)
                height = float(page.mediabox.height)
            except Exception:
                width, height = 612.0, 792.0
            _assign_cells(keep, drop, width, height)
            for item in drop:
                res["dropped"].append({
                    "pdf": os.path.basename(pdf), "page": page_no,
                    "xobject": item["xobject"], "px": list(item["px"]),
                    "why": item["why"]})
            for item in keep:
                item["pdf"] = os.path.basename(pdf)
                item["page"] = page_no
                collected.append(item)

    if not collected:
        res["errors"].append(
            "the guidance PDF holds no image large enough to be an example "
            "graphic. Some journals state the rules in text and show nothing; "
            "that is a real answer, and the dropped list says what was seen.")
        return res

    # Order within a cell is left to right, which is the reading order of a
    # composite graphic assembled from several images.
    by_cell: dict[tuple, list[dict]] = {}
    for item in collected:
        by_cell.setdefault(
            (item["page"], item["row"], item["column"]), []).append(item)
    for group in by_cell.values():
        group.sort(key=lambda i: i["x_pt"])

    written, kept_files = [], []
    for (page_no, row, col), group in sorted(by_cell.items()):
        label = cells.get((row, col), {})
        verdict = str(label.get("verdict", "") or "")
        slug = label.get("slug") or (_slug(label.get("quote", "")) if
                                     label.get("quote") else "")
        for n, item in enumerate(group):
            data, ext, why = _decode_image(item["obj"])
            if data is None:
                res["dropped"].append({
                    "pdf": item["pdf"], "page": page_no,
                    "xobject": item["xobject"], "px": list(item["px"]),
                    "why": why})
                continue
            if verdict:
                stem = f"{verdict}_{row}_{slug}" if slug else f"{verdict}_{row}"
            else:
                stem = f"p{page_no}_r{row}_c{col}"
            suffix = f"_{chr(ord('a') + n)}" if len(group) > 1 else ""
            name = f"{stem}{suffix}.{ext}"
            target = os.path.join(p["examples"], name)
            is_ours = name in prior_files
            if os.path.exists(target) and not is_ours:
                # Someone else's file of the same name. Theirs wins; the
                # toolkit does not overwrite what it did not write.
                kept_files.append(name)
            elif os.path.exists(target) and not refresh:
                kept_files.append(name)
            else:
                with open(target, "wb") as fh:
                    fh.write(data)
                written.append(name)
            entry = {"file": name, "pdf": item["pdf"], "page": page_no,
                     "row": row, "column": col, "verdict": verdict or None,
                     "px": list(item["px"]),
                     "quote": label.get("quote") or None,
                     "note": label.get("note") or None,
                     "composite": len(group) > 1}
            res["images"].append(entry)

    res["written"] = written
    res["kept"] = kept_files
    res["columns"] = labels.get("columns") or {}
    res["source_note"] = labels.get("source") or None
    _write(state_path, json.dumps(
        {"generated": TODAY, "pdfs": res["pdfs"], "images": res["images"],
         "dropped": res["dropped"], "columns": res["columns"],
         "source_note": res["source_note"]},
        indent=2, ensure_ascii=False))
    _write(os.path.join(p["examples"], "README.md"), _examples_readme(p))
    return res


# ---------------------------------------------------------------------------
# the folder's own README and examples index
# ---------------------------------------------------------------------------

def _readme(p: dict) -> str:
    spec = p["spec"]
    stem = spec["folder_name"]
    dpi = spec["dpi_color"]
    px = (int(round(float(spec["width_in"]) * dpi)),
          int(round(float(spec["height_in"]) * dpi)))
    unknown = [k for k in ("width_in", "height_in", "dpi_color", "font_min_pt",
                           "font_family", "label")
               if k not in spec["sourced"]]
    lines = [
        f"# {spec['term']}",
        "",
        f"The graphical abstract for this paper, as **{spec['term']}** is what "
        f"this journal calls it.",
        "",
        "```",
        f"{stem}.pptx        <- EDIT THIS. It is yours; nothing here overwrites it.",
        f"render_{stem}.R    <- turns it into the .png, at the journal's exact size",
        f"{stem}.png         <- GENERATED",
        "examples/            <- the journal's own example graphics, good and poor",
        "```",
        "",
        "## How to work here",
        "",
        f"1. Open `{stem}.pptx` in PowerPoint. The slide is already "
        f"{spec['width_in']} × {spec['height_in']} in, so what you see is what "
        f"gets printed — do not resize it.",
        "2. Replace the `[TK: …]` labels with the real thing. They are marked so "
        "that an unfilled one is visible rather than plausible.",
        f"3. Run `render_{stem}.R` (or `plan/render_all.R`, which calls it).",
        "4. The manuscript picks the `.png` up automatically on the next build.",
        "",
        "## What this journal requires",
        "",
        f"- **Size** — no larger than {spec['width_in']} × {spec['height_in']} in.",
        f"- **Resolution** — {dpi} dpi color, {spec['dpi_bw']} dpi black and "
        f"white. At {dpi} dpi that is {px[0]} × {px[1]} px.",
        f"- **Type** — {spec['font_family']}, preferably "
        f"{spec['font_preferred_pt']} pt, never below {spec['font_min_pt']} pt. "
        f"This is the most commonly failed rule, and the render check counts it.",
    ]
    if spec["file_formats"]:
        lines.append(f"- **Formats** — {spec['file_formats']}.")
    if spec["label"]:
        lines.append(f"- **Label** — the graphic must be labelled "
                     f"\u201c{spec['label']}\u201d.")
    if spec["placement"]:
        lines.append(f"- **Placement** — {spec['placement']}.")
    if spec["must_be_original"]:
        lines.append("- **Original artwork only** — it must be unpublished and "
                     "made by one of the coauthors. The graphics in "
                     "`examples/` are the journal showing what it wants; none "
                     "of them can go into the paper.")
    if spec["must_differ_from_figures"]:
        lines.append("- **Must not repeat a manuscript figure.** Whether a new "
                     "drawing counts as a copy is your call, not the tool's; "
                     "the rule is stated here so the question is in front of you.")
    if spec["prohibited_content"]:
        lines.append(f"- **Not allowed** — {spec['prohibited_content']}.")
    if unknown:
        lines += [
            "",
            "## Not sourced from the journal",
            "",
            "These were not stated in the guidelines that were read, so the "
            "values above are conservative defaults. Verify before submission:",
            "",
        ] + [f"- `{k}`" for k in unknown]
    lines += [
        "",
        "## Rendering",
        "",
        f"`render_{stem}.R` tries PowerPoint via COM, then LibreOffice, and if "
        "neither is present it stops and tells you to export the PNG from "
        "PowerPoint yourself. That last path is not a failure — it is the "
        "honest answer, and it takes about thirty seconds. What it will never "
        "do is quietly hand you a wrong-sized image.",
        "",
        f"_Generated by `tools/toc_graphic.py` on {TODAY}. The `.pptx` is "
        "written once and never overwritten._",
    ]
    return "\n".join(lines) + "\n"


def _examples_readme(p: dict) -> str:
    spec = p["spec"]
    state = _read_labels(os.path.join(p["examples"], EXAMPLES_STATE))
    images = [i for i in state.get("images", []) if isinstance(i, dict)]
    pdfs = state.get("pdfs", [])
    lines = [
        f"# Examples — {spec['term']}",
        "",
        "**Pictures of what this journal expects, taken from the journal's own "
        "guidance.** Look at them; do not reuse them.",
        "",
    ]
    if spec["must_be_original"]:
        lines += [
            "This journal requires the submitted graphic to be *entirely "
            "original, unpublished artwork created by one of the coauthors*, "
            "so nothing in this folder can go into the paper. What these are "
            "for is calibration — the type sizes, the density, and how much "
            "the picture is expected to say.",
            "",
        ]
    if not images:
        lines += [
            "## Nothing extracted yet",
            "",
            "Save the journal's own author-guidance PDF in this folder (or in "
            "`journal_requirements/sources/`) and run:",
            "",
            "```bash",
            "python <toolkit>/tools/toc_graphic.py examples <project> "
            f"--journal <journal>",
            "```",
            "",
            "If the guidance document shows no example graphics, that is a "
            "real answer and the run says so rather than inventing some.",
            "",
        ]
    else:
        good = [i for i in images if i.get("verdict") == "good"]
        poor = [i for i in images if i.get("verdict") == "poor"]
        plain = [i for i in images if not i.get("verdict")]
        src = state.get("source_note")
        # The documents the images actually came from, not every document that
        # was read. Several guidance PDFs are opened per run and most of them
        # show no examples at all; crediting those would be a false citation.
        from_pdfs = sorted({i["pdf"] for i in images if i.get("pdf")})
        lines += [
            f"## The journal's own examples",
            "",
            f"{len(images)} image{'s' if len(images) != 1 else ''} extracted "
            f"from {', '.join(f'`{n}`' for n in from_pdfs)}"
            + (f", {src}." if src else "."),
            "",
        ]
        if good or poor:
            lines += [
                "The verdicts are the journal's, not this toolkit's, and they "
                "come from the position of each graphic in the guidance "
                "document's own good/poor table — read from the page geometry "
                "rather than by eye, because eyeballing which one is the good "
                "one is exactly the mistake that would teach you backwards.",
                "",
            ]
        for heading, group, gloss in (
                ("What it counts as good", good,
                 "Look at these first. Note how little text each one carries."),
                ("What it counts as poor", poor,
                 "Every failure here is legibility, or the picture not saying "
                 "what the paper is about. None is about artistic quality."),
                ("Extracted, unlabelled", plain,
                 "The guidance document did not label these, so no verdict is "
                 "claimed for them."),
        ):
            if not group:
                continue
            lines += [f"### {heading}", "", gloss, ""]
            seen_cells: set = set()
            for img in group:
                cell = (img.get("row"), img.get("column"))
                if img.get("composite") and cell in seen_cells:
                    continue
                seen_cells.add(cell)
                mates = [i for i in group
                         if (i.get("row"), i.get("column")) == cell]
                files = ", ".join(f"`{i['file']}`" for i in mates)
                lines.append(f"- {files}")
                if len(mates) > 1:
                    lines.append(
                        f"  — one graphic assembled from {len(mates)} images, "
                        f"which is itself the point of this example.")
                if img.get("quote"):
                    lines.append(f"  > {img['quote']}")
                if img.get("note"):
                    lines.append(f"  {img['note']}")
            lines.append("")
    # Only the documents actually sitting here. The others were read from
    # journal_requirements/sources/ and saying they are "kept in this folder"
    # would send someone looking for a file that is not there.
    here = sorted(n for n in pdfs
                  if os.path.isfile(os.path.join(p["examples"], n)))
    if here:
        lines += [
            "## The guidance document itself",
            "",
            "Kept in this folder because it is the journal's own, it is meant "
            "to be distributed, and it is the thing to re-read before "
            "submitting: "
            + ", ".join(f"`{n}`" for n in here) + ".",
            "",
        ]
    elsewhere = [n for n in pdfs if n not in here]
    if elsewhere:
        lines += [
            "Also read, from `journal_requirements/sources/`: "
            + ", ".join(f"`{n}`" for n in elsewhere) + ".",
            "",
        ]
    links = [l for l in _read_labels(
        os.path.join(p["examples"], LABELS_FILE)).get("links", [])
        if isinstance(l, dict)]
    lines += [
        "## Published graphics worth a look",
        "",
        "Links, not copies — a published figure is someone's copyrighted "
        "asset, and a link costs nothing. These are chosen by the skill and "
        "recorded in `labels.json`, because picking them is judgment; every "
        "DOI goes through `pubmed.py verify` before it is written there.",
        "",
        "| graphic | journal | DOI | why it is worth a look |",
        "|---|---|---|---|",
    ]
    if links:
        for l in links:
            doi = str(l.get("doi", "") or "")
            cell = f"[{doi}](https://doi.org/{doi})" if doi else ""
            lines.append(f"| {l.get('title', '')} | {l.get('journal', '')} "
                         f"| {cell} | {l.get('why', '')} |")
    else:
        lines.append("| _[TK: add a `links` list to `labels.json`]_ | | | |")
    lines += [
        "",
        "## Your own references",
        "",
        "Anything you drop in here is yours: a file this tool did not write is "
        "never overwritten, and the index names only what it extracted.",
        "",
        f"_Index generated by `tools/toc_graphic.py` on {TODAY}._",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# the four commands
# ---------------------------------------------------------------------------

def status(project: str, journal: str) -> dict:
    p = paths(project, journal)
    spec = p["spec"]
    res: dict = {
        "project": project, "journal": journal, "layout": p["kind"],
        "required": spec["required"], "term": spec["term"],
        "folder": os.path.relpath(p["root"], project).replace(os.sep, "/"),
        "geometry": {"width_in": spec["width_in"], "height_in": spec["height_in"],
                     "dpi_color": spec["dpi_color"],
                     "pixels": [int(round(float(spec["width_in"]) * spec["dpi_color"])),
                                int(round(float(spec["height_in"]) * spec["dpi_color"]))]},
        "sourced_fields": spec["sourced"],
        "findings": [],
    }

    def add(severity: str, rule: str, detail: str, where: str = "") -> None:
        res["findings"].append({"severity": severity, "rule": rule,
                                "detail": detail, "where": where})

    # A requirements.yml the reader cannot read is not a graphic question, it
    # is a spec that is not the one written down. Said first, and blocking,
    # because every field below it is then a guess.
    if p["unreadable"]:
        for prob in p["unreadable"]:
            add("blocking", "requirements_unreadable", prob,
                "journal_requirements/requirements.yml")
        res["state"] = "unreadable"
        res["summary"] = (
            f"requirements.yml cannot be read for {journal}, so nothing here "
            f"is the journal's own answer. Fix the file first.")
        return res

    if spec["required"] is False:
        res["state"] = "not_required"
        res["summary"] = (f"{journal} does not ask for a {spec['term']}; "
                          "nothing to do.")
        return res
    if spec["required"] == "unknown":
        add("gap", "requirement_unsourced",
            f"whether {journal} requires a graphical abstract was never "
            f"sourced. Nothing is scaffolded on a guess - fill "
            f"toc_graphic.required in requirements.yml.",
            "journal_requirements/requirements.yml")
        res["state"] = "unknown"
        res["summary"] = (f"toc_graphic.required is unknown for {journal}. "
                          "Sourced or unknown, never inferred.")
        return res

    have_dir = os.path.isdir(p["root"])
    res["exists"] = {"folder": have_dir,
                     "pptx": os.path.isfile(p["pptx"]),
                     "png": os.path.isfile(p["png"]),
                     "script": os.path.isfile(p["script"]),
                     "examples": os.path.isdir(p["examples"])}
    st = render_state(p)
    res["render"] = st
    res["wired"] = block_present(p)

    if not have_dir:
        add("gap", "no_folder",
            f"{journal} requires a {spec['term']} and there is no "
            f"{res['folder']}/ - run `toc_graphic.py scaffold`", res["folder"])
        res["state"] = "absent"
        res["summary"] = f"required, and not started."
        return res

    if not res["exists"]["pptx"]:
        add("gap", "no_pptx", f"no {os.path.basename(p['pptx'])} to edit",
            res["folder"])
    if not res["exists"]["script"]:
        add("gap", "no_script",
            f"no render_{spec['folder_name']}.R", res["folder"])

    insp = inspect_pptx(p["pptx"], spec)
    res["pptx"] = {k: insp[k] for k in
                   ("exists", "size_in", "size_matches", "error")}
    res["pptx"]["runs"] = len(insp["texts"])
    if insp["error"]:
        add("blocking", "pptx_unreadable", insp["error"], res["folder"])
    if insp["exists"] and insp["size_matches"] is False:
        add("gap", "wrong_slide_size",
            f"the slide is {insp['size_in'][0]} x {insp['size_in'][1]} in but "
            f"{journal} wants {spec['width_in']} x {spec['height_in']} in - "
            f"what you draw will be scaled on export", res["folder"])
    for t in insp["tk"]:
        add("gap", "unfilled_label",
            f"the slide still says {t['text'][:60]!r}", res["folder"])
    for t in insp["too_small"]:
        add("gap", "type_too_small",
            f"{t['text'][:40]!r} is set at {t['pt']:g} pt; "
            f"{journal}'s minimum is {spec['font_min_pt']:g} pt"
            + ("" if t["sz_declared"] else
               " (no size on the run, so PowerPoint's 18 pt default applies "
               "- this may be a false positive; set the size explicitly)"),
            res["folder"])

    if st["verdict"] == "missing":
        add("gap", "not_rendered", st["why"], res["folder"])
    elif st["verdict"] == "stale":
        add("gap", "stale_render",
            st["why"] + " - the older image is what would go to the journal",
            res["folder"])
    if st["verdict"] == "current" and not res["wired"]:
        add("gap", "not_wired",
            "the render is current but %stoc_graphic.md does not carry the "
            "generated block - run `toc_graphic.py wire`" % stpfx(project),
            stpfx(project) + "toc_graphic.md")
    if spec["must_differ_from_figures"]:
        add("note", "must_differ_from_figures",
            f"{journal} does not allow the graphic to repeat a manuscript "
            f"figure. Reported, not enforced - whether a drawing is a copy is "
            f"your call.", res["folder"])

    blocking = sum(1 for f in res["findings"] if f["severity"] == "blocking")
    gaps = sum(1 for f in res["findings"] if f["severity"] == "gap")
    res["state"] = ("ready" if not blocking and not gaps else "in_progress")
    res["summary"] = (
        f"{spec['term']} for {journal}: " +
        ("ready." if res["state"] == "ready"
         else f"{gaps} thing{'s' if gaps != 1 else ''} outstanding"
              + (f", {blocking} blocking" if blocking else "") + "."))
    return res


def scaffold(project: str, journal: str, suggestion_path: str | None = None,
             dry_run: bool = False, refresh: bool = False) -> dict:
    p = paths(project, journal)
    spec = p["spec"]
    if p["unreadable"]:
        return {"errors": ["REFUSED - " + prob for prob in p["unreadable"]]
                + ["The README would state a requirement the file does not."],
                "created": [], "kept": []}
    if spec["required"] is False:
        return {"errors": [
            f"REFUSED - {journal} does not require a {spec['term']}. Nothing "
            f"is scaffolded for a graphic the journal will not print."],
            "created": [], "kept": []}
    if spec["required"] == "unknown":
        return {"errors": [
            f"REFUSED - toc_graphic.required is unknown for {journal}. Every "
            f"requirement is sourced or unknown and never inferred, and a "
            f"folder scaffolded on a guess is a guess the user then trusts. "
            f"Fill it in journal_requirements/requirements.yml first."],
            "created": [], "kept": []}

    sug = default_suggestion(spec)
    if suggestion_path:
        try:
            sug = json.loads(_read(suggestion_path))
        except json.JSONDecodeError as exc:
            return {"errors": [f"cannot read suggestion {suggestion_path}: {exc}"],
                    "created": [], "kept": []}

    created: list[str] = []
    kept: list[str] = []
    refreshed: list[str] = []
    stem = spec["folder_name"]

    def rel(path: str) -> str:
        return os.path.relpath(path, project).replace(os.sep, "/")

    # README.md and the .R script are GENERATED and may be refreshed. The
    # .pptx never is: it is the user's editing surface the moment it exists,
    # the same rule plan/outline.md gets (spec 7.2.2).
    generated = [p["readme"], os.path.join(p["examples"], "README.md"),
                 p["script"]]
    for target in generated:
        if not os.path.exists(target):
            created.append(rel(target))
        elif refresh:
            refreshed.append(rel(target))
        else:
            kept.append(rel(target))
    (created if not os.path.exists(p["pptx"]) else kept).append(rel(p["pptx"]))

    if dry_run:
        return {"errors": [], "created": created, "kept": kept,
                "refreshed": refreshed, "folder": rel(p["root"]),
                "dry_run": True,
                "note": "the .pptx is written once and never overwritten"}

    os.makedirs(p["examples"], exist_ok=True)
    ex = os.path.join(p["examples"], "README.md")
    for target, body in ((p["readme"], _readme(p)),
                         (ex, _examples_readme(p))):
        if refresh or not os.path.exists(target):
            _write(target, body)
    if refresh or not os.path.exists(p["script"]):
        write_r_script(p["script"], spec, stem)
    pptx_written = None
    if not os.path.exists(p["pptx"]):
        pptx_written = write_pptx(p["pptx"], spec, sug)

    # Populate examples/ on the way past. A folder that arrives with the
    # journal's own good and poor graphics already in it is worth having on the
    # first run rather than the second, and this cannot fail the scaffold: no
    # guidance PDF and no pypdf are both reported, not raised.
    ex_res = extract_examples(project, journal, refresh=refresh)
    created += [f"{rel(p['examples'])}/{n}" for n in ex_res["written"]]

    return {"errors": [], "created": created, "kept": kept,
            "refreshed": refreshed, "folder": rel(p["root"]),
            "shapes": (pptx_written or {}).get("shapes", 0),
            "examples": {"images": len(ex_res["images"]),
                         "labelled": ex_res["labelled"],
                         "notes": ex_res["errors"]},
            "note": ("the .pptx is yours from here; nothing overwrites it"
                     if pptx_written else
                     "the .pptx already existed and was left alone")}


def _rscript() -> str | None:
    """R is installed on the machine this was built on but is not on PATH, so
    `which Rscript` finds nothing. Same finder as manuscript.py's `rscript()`."""
    found = shutil.which("Rscript")
    if found:
        return found
    import glob
    for pat in (r"C:\Program Files\R\R-*\bin\Rscript.exe",
                r"C:\Program Files\R\R-*\bin\x64\Rscript.exe"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


def render(project: str, journal: str, timeout: int = 240) -> dict:
    p = paths(project, journal)
    spec = p["spec"]
    if not os.path.isfile(p["pptx"]):
        return {"errors": [f"no {os.path.basename(p['pptx'])} - run scaffold first"],
                "rendered": False}
    if not os.path.isfile(p["script"]):
        return {"errors": [f"no {os.path.basename(p['script'])} - run scaffold first"],
                "rendered": False}
    rs = _rscript()
    if not rs:
        return {"errors": [
            "Rscript not found. The render script is R because every other "
            "float script is; export the PNG from PowerPoint instead and the "
            "pipeline picks it up identically."], "rendered": False}

    before = _sha256(p["png"]) if os.path.isfile(p["png"]) else ""
    try:
        run = subprocess.run([rs, "--vanilla", p["script"]],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"errors": [f"the render did not finish within {timeout}s"],
                "rendered": False}
    out = (run.stdout or "") + (run.stderr or "")
    m = re.search(r"rendered .*? via (\S+)", out)
    backend = m.group(1) if m else "unknown"

    if not os.path.isfile(p["png"]):
        return {"errors": [out.strip() or "the render produced no .png"],
                "rendered": False, "backend": backend}
    px = [int(round(float(spec["width_in"]) * spec["dpi_color"])),
          int(round(float(spec["height_in"]) * spec["dpi_color"]))]
    write_state(p, backend, px)
    return {"errors": [], "rendered": True, "backend": backend,
            "changed": _sha256(p["png"]) != before,
            "png": os.path.relpath(p["png"], project).replace(os.sep, "/"),
            "pixels": px, "output": out.strip()}


# ---------------------------------------------------------------------------
# printing
# ---------------------------------------------------------------------------

def print_status(res: dict) -> None:
    print(f"  {res['summary']}")
    if res.get("state") in ("not_required",):
        return
    g = res.get("geometry", {})
    if g:
        print(f"  geometry  {g['width_in']} x {g['height_in']} in @ "
              f"{g['dpi_color']} dpi = {g['pixels'][0]} x {g['pixels'][1]} px")
    if res.get("folder"):
        print(f"  folder    {res['folder']}  ({res.get('layout', '')} layout)")
    r = res.get("render")
    if r:
        print(f"  render    {r['verdict']} - {r['why']}")
    if res.get("wired") is not None:
        print(f"  wired     {'yes' if res['wired'] else 'no'}")
    for f in res.get("findings", []):
        print(f"  {f['severity']:8s} {f['detail']}")


def main() -> int:
    p = argparse.ArgumentParser(
        prog="toc_graphic.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true",
                   help="emit JSON instead of text")

    # --json on either side of the subcommand, per the convention in CLAUDE.md.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true",
                        default=argparse.SUPPRESS,
                        help="emit JSON instead of text")

    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", parents=[common],
                       help="is one required, is it made, is it current")
    s.add_argument("project")
    s.add_argument("--journal", required=True)

    sc = sub.add_parser("scaffold", parents=[common],
                        help="create the folder, the .pptx and the render script")
    sc.add_argument("project")
    sc.add_argument("--journal", required=True)
    sc.add_argument("--suggestion", default=None, metavar="JSON",
                    help="the skill's proposed layout: title, stages, footnote. "
                         "Without one the slide is written with [TK] labels.")
    sc.add_argument("--refresh", action="store_true",
                    help="rewrite the GENERATED files (README, examples index, "
                         "the .R script) from the current requirements. Never "
                         "touches the .pptx.")
    sc.add_argument("--dry-run", action="store_true")

    ex = sub.add_parser("examples", parents=[common],
                        help="extract the journal's own example graphics out "
                             "of its guidance PDF into examples/")
    ex.add_argument("project")
    ex.add_argument("--journal", required=True)
    ex.add_argument("--from", dest="from_pdf", default=None, metavar="PDF",
                    help="the guidance PDF to read. Without one, every PDF in "
                         "examples/ and journal_requirements/sources/ is read.")
    ex.add_argument("--labels", default=None, metavar="JSON",
                    help="the good/poor verdicts and their quotes, keyed by "
                         "(row, column). Defaults to examples/labels.json. "
                         "Without it the images are extracted unlabelled.")
    ex.add_argument("--refresh", action="store_true",
                    help="rewrite images this tool wrote before. A file it did "
                         "not write is never overwritten.")

    rd = sub.add_parser("render", parents=[common],
                        help="run the R script: .pptx -> .png")
    rd.add_argument("project")
    rd.add_argument("--journal", required=True)
    rd.add_argument("--timeout", type=int, default=240)

    w = sub.add_parser("wire", parents=[common],
                       help="write or refresh the block in source_text/")
    w.add_argument("project")
    w.add_argument("--journal", required=True)
    w.add_argument("--force", action="store_true",
                   help="wire even with no .png rendered yet")

    args = p.parse_args()
    as_json = getattr(args, "json", False)

    if args.cmd == "status":
        res = status(args.project, args.journal)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_status(res)
        return 0

    if args.cmd == "scaffold":
        res = scaffold(args.project, args.journal, args.suggestion,
                       args.dry_run, args.refresh)
        if as_json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        elif res["errors"]:
            for e in res["errors"]:
                print(f"  {e}")
        else:
            for c in res["created"]:
                print(f"  created    {c}")
            for c in res.get("refreshed", []):
                print(f"  refreshed  {c}")
            for k in res["kept"]:
                print(f"  kept       {k}")
            print(f"  {res['note']}")
        return 2 if res["errors"] else 0

    if args.cmd == "examples":
        res = extract_examples(args.project, args.journal, args.from_pdf,
                               args.labels, args.refresh)
        if as_json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            for e in res["errors"]:
                print(f"  {e}")
            for name in res["written"]:
                print(f"  wrote      examples/{name}")
            for name in res["kept"]:
                print(f"  kept       examples/{name}")
            for d in res["dropped"]:
                # Name the PDF. Several guidance documents are read in one run
                # and "p1" alone reads as a phantom when three of them have one.
                print(f"  not an example  {d['pdf']} p{d['page']} "
                      f"{d['xobject']} {d['px'][0]}x{d['px'][1]} - {d['why']}")
            if res["images"]:
                verdicts = sorted({i["verdict"] for i in res["images"]
                                   if i["verdict"]})
                print(f"  {len(res['images'])} example image(s)"
                      + (f", labelled {'/'.join(verdicts)}" if verdicts
                         else ", unlabelled"))
        return 2 if res["errors"] and not res["images"] else 0

    if args.cmd == "render":
        res = render(args.project, args.journal, args.timeout)
        if as_json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        elif res["errors"]:
            for e in res["errors"]:
                print(f"  {e}")
        else:
            print(f"  {res['png']}  {res['pixels'][0]} x {res['pixels'][1]} px "
                  f"via {res['backend']}"
                  + ("" if res["changed"] else "  (unchanged)"))
        return 2 if res["errors"] else 0

    if args.cmd == "wire":
        res = wire(paths(args.project, args.journal), args.force)
        if as_json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        elif res["wired"]:
            print(f"  {res['action']}  {res.get('section', '')}")
        else:
            print(f"  not wired - {res['reason']}")
        return 0 if res["wired"] else 2

    return 1


if __name__ == "__main__":
    sys.exit(main())
