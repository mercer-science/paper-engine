#!/usr/bin/env python3
"""
tests/manuscript.py - measure tools/manuscript.py against a real project.

Scaffolds a project, fills it with a manuscript that contains one of every
failure the gates exist to catch, and then drives the whole pipeline: journal
init, the module plan, a real pandoc build, the three assemble gates, round
snapshots, the edit ledger, and the response letter.

The pandoc and R sections skip cleanly when those are absent, and say so - a
skipped check is reported as skipped, never as a pass.

  python tests/manuscript.py
  python tests/manuscript.py -v
"""

from __future__ import annotations

import contextlib
import glob
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import struct
import sys
import tempfile
import zipfile

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
ROOT = os.path.dirname(HERE)
# The defect ledger is local to each machine and gitignored, so the suites
# read a scrubbed sample of it: real item numbers, codes and statuses, and no
# project text. Deterministic too - the live one changes with every build.
LEDGER_SOURCE = os.path.join(HERE, "fixtures", "system-changes.sample.md")
FIXTURES = os.path.join(HERE, "fixtures")

# Same name collision as scaffold.py, idea.py and docx_edits.py: load the
# engine by path under a distinct module name. Do not "simplify" this back.
_spec = importlib.util.spec_from_file_location(
    "manuscript_engine", os.path.join(ROOT, "tools", "manuscript.py"))
if _spec is None or _spec.loader is None:
    raise ImportError("cannot load tools/manuscript.py")
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)

# The scaffold manifest is a contract this suite checks against: init creates
# drafts/ by calling scaffold.py, and "the files it created" has to be compared
# with the list scaffold.py actually keeps, not a copy of it typed out here.
_spec_sc = importlib.util.spec_from_file_location(
    "scaffold_engine_m", os.path.join(ROOT, "tools", "scaffold.py"))
if _spec_sc is None or _spec_sc.loader is None:
    raise ImportError("cannot load tools/scaffold.py")
sc = importlib.util.module_from_spec(_spec_sc)
_spec_sc.loader.exec_module(sc)

VERBOSE = "-v" in sys.argv
PASS = FAIL = SKIP = 0


def _slurp(path, mode="r", **kw):
    """Read a whole file and close it.

    `open(path).read()` leaks the handle until the garbage collector gets to
    it - harmless in a short script, but it makes the suite noisy under
    `python -W all`, and on Windows a handle still open can block the
    tempdir cleanup these tests rely on.
    """
    with open(path, mode, **kw) as fh:
        return fh.read()


def pandoc_bin() -> str:
    """The pandoc on PATH, for a test that has already checked it is there.

    shutil.which returns None when it is not, and handing that to
    subprocess.run reports a TypeError about the argument list rather than
    the thing that is actually wrong.
    """
    exe = shutil.which("pandoc")
    if exe is None:
        raise AssertionError("pandoc is not on PATH")
    return exe


def group(pattern: str, text: str, n: int = 0, flags: int = 0) -> str:
    """The n-th group of a match that has to be there.

    Every caller reads a file this suite just built, so a miss is a broken
    build rather than a test outcome - raise it where it happens instead of
    letting `.group` fail on None three lines later.
    """
    m = re.search(pattern, text, flags)
    if m is None:
        raise AssertionError(f"no match for {pattern!r}")
    return m.group(n)


def check(name: str, got, want, note: object = "") -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
        if VERBOSE:
            print(f"  ok    {name}  = {got!r}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}\n          got  {got!r}\n          want {want!r}"
              + (f"\n          {note}" if note else ""))


def skip(name: str, why: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  skip  {name}  ({why})")


def section(title: str) -> None:
    print(f"\n{title}\n" + "-" * len(title))


def stdir(root: str) -> str:
    """The project's live source_text folder, whatever round it is labelled.

    `round` and `init` rename it forward - `source_text_r1` becomes
    `source_text_r2` when a round closes - so a test that hard-coded
    `drafts/source_text/` would be writing into a folder nothing builds from,
    which is precisely the failure the label exists to make visible.
    """
    return ms.source_text_dir(root)


def stfile(root: str, name: str) -> str:
    return os.path.join(stdir(root), name)


def write(root: str, rel: str, body: str) -> str:
    # `drafts/source_text/x.md` in a test body means "the drafting folder's
    # own source text", not that literal path. A deliberately built SECOND
    # folder is written with the bare `source_text/` prefix and is never
    # redirected - that case is what the both-layouts refusal is about.
    live = os.path.basename(stdir(root))
    if rel.startswith("drafts/source_text/") and live != "source_text":
        rel = "drafts/" + live + "/" + rel[len("drafts/source_text/"):]
    elif (rel.startswith("source_text/") and live != "source_text"
            and ms.is_trimmed(root)):
        rel = live + "/" + rel[len("source_text/"):]
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    return path


def read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


_PROSE_ENGINE = None


def write_raw(path: str, body: str) -> None:
    """Write an absolute path verbatim - no source_text redirect."""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)


def prose_engine():
    """tools/prose.py, loaded once by path under a distinct module name.

    Same name collision as everywhere else in this suite; several tests want
    the engine in-process rather than as a subprocess, and loading it more
    than once per run is wasted startup.
    """
    global _PROSE_ENGINE
    if _PROSE_ENGINE is None:
        spec = importlib.util.spec_from_file_location(
            "prose_engine_shared", os.path.join(ROOT, "tools", "prose.py"))
        if spec is None or spec.loader is None:
            raise ImportError("cannot load tools/prose.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _PROSE_ENGINE = mod
    return _PROSE_ENGINE


def prose_outline(project: str, adherence=3) -> dict:
    """`prose.py outline` at one adherence level, in process."""
    return prose_engine().outline(project, adherence)


def paragraphs(document_xml: str) -> list[tuple[str, str]]:
    """(style, text) for every paragraph, in document order.

    ms._headings() keeps only the headings, and "is the References heading
    immediately above the first reference" is a question about what sits
    between two paragraphs.
    """
    root = ms.ET.fromstring(document_xml)
    body = root.find(ms.W + "body")
    out: list[tuple[str, str]] = []
    for p in (body.findall(ms.W + "p") if body is not None else []):
        ppr = p.find(ms.W + "pPr")
        style = ""
        if ppr is not None:
            ps = ppr.find(ms.W + "pStyle")
            if ps is not None:
                style = ps.get(ms.W + "val") or ""
        out.append((style, "".join(t.text or "" for t in p.iter(ms.W + "t"))
                    .strip()))
    return out


# ---------------------------------------------------------------------------
# The fixture project
# ---------------------------------------------------------------------------

BIB = """\
@article{jensen2019arrays,
  title = {Cryo-electron tomography of chemoreceptor arrays in situ},
  author = {Jensen, Grant J. and Briegel, Ariane},
  journal = {Annu Rev Microbiol},
  year = {2019},
  volume = {73},
  pages = {211--230},
  doi = {10.1146/annurev-micro-020518-115908},
  pmid = {31234567}
}
"""

RESULTS = """\
# Results

Arrays from both species kept the hexagonal packing reported in every mesophile
examined so far (Figure 1), and the lattice spacing did not shift with growth
temperature [@jensen2019arrays].

Arrays from the thermophile were markedly more ordered than those from the
mesophile — a 15% higher symmetry index (0.82 ± 0.05 vs. 0.71 ± 0.05,
p = 0.003) — which is the difference between a lattice that resolves and one
that does not (Figure 2).
"""

METHODS = """\
# Methods

Cells were grown to mid-log phase and plunge-frozen
**[FLAG: author — growth.medium not recorded in data/methods_facts.yml]**.
"""


def ms_scaffold(root: str, journal: str = "", field: str = "chemistry") -> str:
    """A bare scaffolded project - no manuscript in it.

    build_project() below fills one with a whole paper, which is what most of
    this suite needs. These three tests are about the shape of the folders, so
    they want the shape and nothing else.
    """
    cmd = [sys.executable, os.path.join(ROOT, "tools", "scaffold.py"),
           "scaffold", root, "--field", field]
    if journal:
        cmd += ["--journal", journal]
    run = subprocess.run(cmd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if run.returncode != 0:
        print(run.stdout, run.stderr)
        raise SystemExit("scaffold.py failed; nothing downstream can be tested")
    return root


def build_project(tmp: str) -> str:
    root = os.path.join(tmp, "arrsym")
    run = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "scaffold.py"),
         "scaffold", root, "--title", "Array symmetry",
         "--field", "biochemistry"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if run.returncode != 0:
        print(run.stdout, run.stderr)
        raise SystemExit("scaffold.py failed; nothing downstream can be tested")
    write(root, "drafts/references.bib", BIB)
    write(root, "drafts/source_text/results.md", RESULTS)
    write(root, "drafts/source_text/methods.md", METHODS)
    write(root, "drafts/source_text/introduction.md",
          "# Introduction\n\nArrays have been resolved in mesophiles but not "
          "above 60 C [@jensen2019arrays].\n")
    write(root, "drafts/source_text/discussion.md",
          "# Discussion\n\nOrder tracks growth temperature rather than "
          "phylogeny (Figure 2).\n")
    write(root, "drafts/source_text/title_abstract.md",
          "# Title\n\nChemoreceptor arrays are more ordered in thermophiles\n"
          "\n## Running title\n\nArray order and growth temperature\n"
          "\n## Abstract\n\nWe compared chemoreceptor array symmetry across "
          "species spanning 30 C of growth temperature and found the "
          "thermophilic arrays more ordered.\n"
          "\n## Keywords\n\ncryo-ET, chemoreceptor, thermophile\n")
    write(root, "drafts/source_text/live_captions.md",
          "## Figure 1 — fig01_geometry.png\n"
          "**Arrays keep hexagonal packing at every growth temperature.**\n"
          "(A) Central sections. n = 12 per species.\n\n"
          "## Figure 2 — fig02_symmetry.png\n"
          "**Thermophilic arrays are more ordered than mesophilic ones.**\n"
          "Symmetry index against growth temperature.\n")
    write(root, "drafts/source_text/authors.md",
          "# Authors\n\n| # | name | affiliation(s) | ORCID | email |\n"
          "|---|---|---|---|---|\n| 1 | Rowan Wills | 1 | | rw@x.edu |\n\n"
          "## RW — Rowan Wills\norder: 1\n\n"
          "## JV — June Vale\norder: 2\n\n"
          "## GJ — Grant Jensen\norder: 3\n")
    return root


# ---------------------------------------------------------------------------

def test_inbox_location(tmp: str) -> None:
    section("The inbox is the project's, beside source_text/ (2026-09-24)")

    sub = os.path.join(tmp, "inbox_new")
    os.makedirs(sub, exist_ok=True)
    root = build_project(sub)
    ms.init(root, "Langmuir")
    jdir = ms.journal_dir(root, "Langmuir")

    check("init creates drafts/edits/, not the journal's",
          (os.path.isdir(os.path.join(root, "drafts", "edits")),
           os.path.isdir(os.path.join(jdir, "edits"))),
          (True, False))
    check("...and the README lands in it",
          os.path.isfile(os.path.join(root, "drafts", "edits", "README.md")),
          True)
    check("edits_dir resolves there",
          os.path.relpath(ms.edits_dir(jdir), root).replace(os.sep, "/"),
          "drafts/edits")
    check("...and edits_pfx is the path a message should name",
          ms.edits_pfx(root, jdir), "drafts/edits/")
    check("the inbox is not mistaken for a journal folder",
          ms.existing_journals(root), ["Langmuir"],
          "a sibling of the journal folders now, not a child of one")

    # The inbox belongs to the PAPER: the same folder serves a second journal,
    # which is the failure the move exists to stop. A return dropped in after
    # the paper moved on used to be a file `ingest` did not look for.
    ms.init(root, "JPCC")
    check("a second journal reads the same inbox",
          os.path.abspath(ms.edits_dir(ms.journal_dir(root, "JPCC"))),
          os.path.abspath(ms.edits_dir(jdir)))

    # --- a journal may not be called `edits` ------------------------------
    refusal = ms.journal_name_refusal("edits")
    check("`edits` is refused as a journal name", bool(refusal), True)
    check("...naming the case-insensitive collision that makes it unsafe",
          "same directory" in refusal, True, refusal)

    # --- the per-journal location is still read, and is moved for nobody ---
    sub_old = os.path.join(tmp, "inbox_legacy")
    os.makedirs(sub_old, exist_ok=True)
    root_o = build_project(sub_old)
    ms.init(root_o, "Langmuir")
    jdir_o = ms.journal_dir(root_o, "Langmuir")
    legacy = os.path.join(jdir_o, "edits")
    os.makedirs(legacy, exist_ok=True)
    wr(os.path.join(legacy, "edits_status.md"), coauthor_ledger())
    check("a project that already has drafts/<J>/edits/ keeps using it",
          os.path.abspath(ms.edits_dir(jdir_o)), os.path.abspath(legacy),
          "ingest matches on a fingerprint the last round wrote; an inbox "
          "that relocates mid-round loses that match")
    check("...and the messages name the place the file actually is",
          ms.edits_pfx(root_o, jdir_o), "drafts/Langmuir/edits/")
    check("...and the ledger there is read",
          [it["id"] for it in ms.standing_coauthor_rows(jdir_o)], ["JV-1"])

    # --- retire-journal does not rebuild the old layout -------------------
    #
    # With the inbox outside the folder being retired the ledger follows the
    # paper by standing still, so the refusal and the copy are both moot. Left
    # ungated, the copy writes drafts/<NEW>/edits/edits_status.md - and
    # edits_dir prefers that copy from then on, putting the project silently
    # back on the layout it had left.
    sub_r = os.path.join(tmp, "inbox_retire")
    os.makedirs(sub_r, exist_ok=True)
    root_r = build_project(sub_r)
    ms.init(root_r, "Langmuir")
    wr(os.path.join(root_r, "drafts", "edits", "edits_status.md"),
       coauthor_ledger())
    res_r = ms.retire_journal(root_r, "Langmuir")
    check("a standing coauthor row does NOT refuse the retirement",
          res_r["errors"], [],
          "the ledger is not in the folder being retired")
    check("...the inbox stays where it is",
          os.path.isfile(os.path.join(root_r, "drafts", "edits",
                                      "edits_status.md")), True)
    check("...and no per-journal inbox is created on the way out",
          os.path.isdir(os.path.join(root_r, "drafts", "Langmuir", "edits")),
          False)


def test_author_information(tmp: str) -> None:
    section("plan/author_information/ and the signed COI forms (2026-09-24)")

    # `_scaffold`, not `build_project`: the latter writes a FILLED author list
    # into the round folder, which correctly outranks the blank scaffolded one
    # (`test_front_matter_paths` is where that ranking is measured). A test
    # asking where scaffold.py PUTS things starts from what it wrote.
    sub = os.path.join(tmp, "author_info")
    os.makedirs(sub, exist_ok=True)
    root = _scaffold(sub, "authorinfo")

    ai = os.path.join(root, "plan", "author_information")
    for rel in ("authors.md", "affiliations.md",
                "conflict_statements/README.md"):
        check(f"scaffolded plan/author_information/{rel}",
              os.path.isfile(os.path.join(ai, *rel.split("/"))), True)
    check("the author list is resolved there",
          ms.fm_rel(root, "authors"), "plan/author_information/authors.md")
    check("...and the affiliations",
          ms.fm_rel(root, "affiliations"),
          "plan/author_information/affiliations.md")

    # Both ship with their tables already drawn: the point is that they are
    # fillable on day one, not that they are present.
    authors_md = read(ms.front_matter_path(root, "authors"))
    check("authors.md ships with the author table",
          "| # | name | affiliations | ORCID | email |" in authors_md, True)
    check("...and the CRediT contribution matrix under it",
          "## Contributions (CRediT)" in authors_md, True)
    aff_md = read(ms.front_matter_path(root, "affiliations"))
    check("affiliations.md ships with the affiliation and grant tables",
          ("| key | affiliation |" in aff_md
           and "| funder | award | to |" in aff_md), True)

    # The 2026-09-08 location is still read, and still loses to a filled file
    # rather than to a present one - the rule measured that day.
    sub_old = os.path.join(tmp, "author_info_legacy")
    os.makedirs(sub_old, exist_ok=True)
    root_o = _scaffold(sub_old, "authorinfo_legacy")
    os.remove(os.path.join(root_o, "plan", "author_information", "authors.md"))
    write(root_o, "plan/authors.md",
          "# Authors\n\n| # | name | affiliations | ORCID | email |\n"
          "|---|---|---|---|---|\n| 1 | Ada Bell | A |  | abell@example.edu |\n")
    check("plan/authors.md is still read where it is",
          ms.fm_rel(root_o, "authors"), "plan/authors.md")
    check("...and the author list comes out of it",
          [a["name"] for a in ms.author_record(root_o)["authors"]],
          ["Ada Bell"])

    # --- the forms are counted, never read --------------------------------
    write(root, "plan/author_information/authors.md",
          read(os.path.join(ai, "authors.md")).replace(
              "| 1 |  |  |  |  |",
              "| 1 | Ada Bell | A |  | abell@example.edu |\n"
              "| 2 | Cyd Doyle | A |  | cdoyle@example.edu |"))
    coi_dir = ms.conflict_dir(root)
    check("no forms yet: both authors are outstanding",
          ms.conflict_statements(root)["missing"], ["AB", "CD"])

    wr(os.path.join(coi_dir, "AB.pdf"), "not a pdf")
    wr(os.path.join(coi_dir, "doyle-icmje-2026.pdf"), "not a pdf")
    wr(os.path.join(coi_dir, "notes from the lab.pdf"), "not a pdf")
    coi = ms.conflict_statements(root)
    check("a form named for the author's initials is matched",
          sorted(coi["returned"].get("AB") or []), ["AB.pdf"])
    check("...and one named for their surname is too",
          sorted(coi["returned"].get("CD") or []), ["doyle-icmje-2026.pdf"])
    check("...so nobody is outstanding", coi["missing"], [])
    check("a file that resolves to nobody is reported, never dropped",
          coi["unmatched"], ["notes from the lab.pdf"],
          "a form filed under a name the author list does not know is, to a "
          "submission, the same thing as a form that never arrived")
    check("the README is not counted as a returned form",
          "README.md" in coi["forms"], False)

    # --- warnings, never flags --------------------------------------------
    os.remove(os.path.join(coi_dir, "AB.pdf"))
    warns = ms.conflict_warnings(ms.conflict_statements(root))
    check("an outstanding form produces a warning",
          any("competing-interest form" in w for w in warns), True, warns)
    check("...that names who it is waiting on",
          any("AB (Ada Bell)" in w for w in warns), True, warns)
    check("...and no warning claims the package is unsendable",
          any("[FLAG" in w or "NOT SENDABLE" in w for w in warns), False,
          warns)

    # Silent on a project that has never seen the folder - an engine that
    # nagged about a directory the user has never seen would be reporting on
    # its own upgrade.
    quiet = dict(ms.conflict_statements(root))
    quiet["present"] = False
    check("silent on a project with no conflict_statements/ folder",
          ms.conflict_warnings(quiet), [])
    quiet = dict(ms.conflict_statements(root))
    quiet["authors"] = {}
    check("...and on one with no author list yet",
          ms.conflict_warnings(quiet), [],
          "nothing to be missing from")


def test_submission_names(tmp: str) -> None:
    section("submission-names - the file an editor actually receives")

    sub = os.path.join(tmp, "subnames")
    os.makedirs(sub, exist_ok=True)
    root = _scaffold(sub, "example-study")
    ms.init(root, "Langmuir")
    jdir = ms.journal_dir(root, "Langmuir")
    sdir = ms.submission_dir(root, "Langmuir")

    check("a stem is uppercased and separator-normalised",
          ms.submission_stem("Example-Trial study"), "EXAMPLE_TRIAL_STUDY",
          "a filename an editor sees is a label, and a case-only difference "
          "between two of them is invisible on Windows")
    check("short_name straight off the folder name is recognised as unchosen",
          ms._auto_short_name(root), True,
          "scaffold.py fills it that way when nobody supplies one")

    # (1) Nothing built yet. The refusal names the short_name problem first,
    # because that is the one the user has to answer.
    res = ms.submission_names(root, "Langmuir")
    check("it refuses an unchosen short_name", bool(res["errors"]), True)
    check("...naming the flag that answers it",
          "--name" in res["errors"][0], True, res["errors"])

    # (2) With a name, but nothing on disk for this round.
    wr(os.path.join(sdir, "README.md"), "# submission\n")
    res = ms.submission_names(root, "Langmuir", name="Example Study",
                              force=True)
    check("nothing carrying this round's suffix is a refusal",
          any("carries r" in e for e in res["errors"]), True, res["errors"])
    check("...and it creates nothing", os.path.isdir(
        ms.upload_dir(root, "Langmuir")), False,
        "this command renames what is there")

    # (3) A round's worth of files, with an OLDER round's manuscript beside
    # them. Older and not newer: `current_round` reads the highest manuscript
    # on disk, so planting an r8 file in an r1 folder does not make a stray -
    # it moves the round, and the test would then be measuring r8.
    ms.update_round_state(jdir, round=3)
    rnd = ms.build_round(jdir)
    check("the round moved for the fixture", rnd, 3)
    for name in ("manuscript_r%d.docx" % rnd,
                 "cover_letter_r%d.docx" % rnd,
                 "cover_letter_r%d.md" % rnd,
                 "title_page_r%d.docx" % rnd,
                 "checklist_r%d.md" % rnd):
        wr(os.path.join(sdir, name), "x")
    wr(os.path.join(sdir, "manuscript_r1.docx"), "x")

    res = ms.submission_names(root, "Langmuir", name="Example Study",
                              force=True)
    check("it succeeds", res["errors"], [], res)
    got = sorted(it["to"] for it in res["written"])
    check("every package file is named for the paper",
          got, ["EXAMPLE_STUDY_checklist.md", "EXAMPLE_STUDY_cover_letter.docx",
                "EXAMPLE_STUDY_manuscript.docx", "EXAMPLE_STUDY_title_page.docx"])
    check("...and the .md a .docx was built from is NOT in the set",
          any("cover_letter.md" in g for g in got), False,
          "it is the file you edit; a folder called upload/ holding both "
          "answers 'which one do I send' with two files")
    check("...which is said rather than done silently",
          any("EXAMPLE_STUDY_cover_letter.md" in w for w in res["warnings"]),
          True, res["warnings"])
    check("an earlier round's manuscript is not swept in",
          any(it["from"] == "manuscript_r1.docx" for it in res["written"]),
          False, res["written"])

    udir = ms.upload_dir(root, "Langmuir")
    check("the copies are on disk",
          sorted(f for f in os.listdir(udir) if f != "README.md"), got)
    check("...and the round-suffixed originals are untouched",
          os.path.isfile(os.path.join(sdir, "manuscript_r%d.docx" % rnd)),
          True,
          "the suffix is load-bearing in ten other places; this copies")
    check("...with a README saying which is which",
          "do not edit these" in read(os.path.join(udir, "README.md")), True)

    # (4) A second run under a different name clears the first set. Two sets
    # in the folder named for what is uploaded is item 17 all over again.
    res2 = ms.submission_names(root, "Langmuir", name="EXAMPLE_TRIAL", force=True)
    check("a rename under a new stem removes the old set",
          sorted(res2["removed"]), got)
    check("...and says so rather than leaving both",
          sorted(f for f in os.listdir(udir) if f != "README.md"),
          ["EXAMPLE_TRIAL_checklist.md", "EXAMPLE_TRIAL_cover_letter.docx",
           "EXAMPLE_TRIAL_manuscript.docx", "EXAMPLE_TRIAL_title_page.docx"])

    # (5) A mock-data build is refused, because this command's whole job is
    # making files that look ready to send.
    sub_m = os.path.join(tmp, "subnames_mock")
    os.makedirs(sub_m, exist_ok=True)
    root_m = _scaffold(sub_m, "mockpaper")
    ms.init(root_m, "Langmuir")
    rm = ms.build_round(ms.journal_dir(root_m, "Langmuir"))
    wr(os.path.join(ms.submission_dir(root_m, "Langmuir"),
                    "manuscript_r%d_MOCK.docx" % rm), "x")
    res_m = ms.submission_names(root_m, "Langmuir", name="MOCK PAPER")
    check("a mock-data build is refused",
          any("MOCK" in e for e in res_m["errors"]), True, res_m["errors"])
    # 'clear your flags' is advice you act on and come back, and acting on it
    # would produce a tidy set of files built from invented data. The worse
    # error is named first.
    check("...ahead of the flag gate, which is the lesser error",
          "MOCK" in res_m["errors"][0], True, res_m["errors"])
    res_m = ms.submission_names(root_m, "Langmuir", name="MOCK PAPER",
                                force=True)
    check("...and --force does not get past it",
          any("MOCK" in e for e in res_m["errors"]), True, res_m["errors"])


def test_reviewer_placement(tmp: str) -> None:
    section("Suggested reviewers go where the journal wants them")

    sub = os.path.join(tmp, "reviewers")
    os.makedirs(sub, exist_ok=True)
    root = _scaffold(sub, "revplace")
    ms.init(root, "Langmuir")
    jdir = ms.journal_dir(root, "Langmuir")
    reqfile = os.path.join(jdir, "journal_requirements", "requirements.yml")

    check("unknown is the default and writes the standalone file",
          ms.reviewer_placement(ms.read_flat_yml(reqfile)), "unknown",
          "a list the journal did not want is a file nobody opens; a list "
          "folded into a letter the journal wanted separately never arrives")

    def set_req(**kv):
        text = read(reqfile)
        for k, v in kv.items():
            text = re.sub(r"^  %s:.*$" % k, "  %s: %s" % (k, v), text,
                          flags=re.M)
        write_raw(reqfile, text)
        return ms.read_flat_yml(reqfile)

    req = set_req(suggested_reviewers="true",
                  suggested_reviewers_in="cover_letter")
    check("the placement is read back", ms.reviewer_placement(req),
          "cover_letter")

    pkg = ms.submission_package(root, "Langmuir")
    names = [os.path.basename(w["path"]) for w in pkg["written"]]
    check("no standalone reviewer file when the letter carries them",
          any("suggested_reviewers" in n for n in names), False, names)
    check("...and the source file is created for the user to fill in",
          os.path.isfile(ms.reviewers_source_path(root)), True)

    # The stub's worked example is inside an HTML comment. A table reader
    # that does not strip comments first reads it as data - measured, it
    # returned the example reviewer as a real one.
    check("the commented-out worked example is not read as a row",
          ms.reviewer_rows(root), [])

    src = ms.reviewers_source_path(root)
    write_raw(src, read(src).replace(
        "| **[FLAG: author]** | | | | |",
        "| Dana Reyes | Rice University | dreyes@example.edu | the only other "
        "UHV study of this surface | yes |"))
    check("a real row is read", [r["name"] for r in ms.reviewer_rows(root)],
          ["Dana Reyes"])

    ms.submission_package(root, "Langmuir")
    letter = read(os.path.join(ms.submission_dir(root, "Langmuir"),
                               "cover_letter_r%d.md" % ms.build_round(jdir)))
    check("the name is in the letter", "Dana Reyes" in letter, True)
    check("...and the letter does not also point at a file",
          "accompany this submission" in letter, False,
          "two files that must agree is how they come to disagree")

    # `separate` writes the standalone file, rendered from the same source.
    set_req(suggested_reviewers_in="separate")
    pkg = ms.submission_package(root, "Langmuir")
    names = [os.path.basename(w["path"]) for w in pkg["written"]]
    check("`separate` writes the standalone file",
          any("suggested_reviewers" in n for n in names), True, names)
    stand = read(os.path.join(ms.submission_dir(root, "Langmuir"),
                              "suggested_reviewers_r%d.md"
                              % ms.build_round(jdir)))
    check("...rendered from the same one list",
          "Dana Reyes" in stand, True,
          "the standalone file is a VIEW of the source, not a second list")

    # `portal` writes neither, and does not promise a file in the letter.
    set_req(suggested_reviewers_in="portal")
    pkg = ms.submission_package(root, "Langmuir")
    names = [os.path.basename(w["path"]) for w in pkg["written"]]
    check("`portal` writes no standalone file",
          any("suggested_reviewers" in n for n in names), False, names)
    letter = read(os.path.join(ms.submission_dir(root, "Langmuir"),
                               "cover_letter_r%d.md" % ms.build_round(jdir)))
    check("...and the letter promises nothing that is not coming",
          "accompany this submission" in letter, False)


def test_blank_coi_form(tmp: str) -> None:
    section("The journal's blank COI form, and who goes and gets it")

    sub = os.path.join(tmp, "coiform")
    os.makedirs(sub, exist_ok=True)
    root = _scaffold(sub, "coiform")
    ms.init(root, "Langmuir")
    jdir = ms.journal_dir(root, "Langmuir")
    reqfile = os.path.join(jdir, "journal_requirements", "requirements.yml")
    req = ms.read_flat_yml(reqfile)

    write(root, "plan/author_information/authors.md",
          read(os.path.join(ms.author_info_dir(root), "authors.md")).replace(
              "| 1 |  |  |  |  |",
              "| 1 | Ada Bell | A |  | abell@example.edu |"))

    coi = ms.conflict_statements(root)
    check("the blank form has a folder of its own",
          coi["blank_form_dir"],
          "plan/author_information/conflict_statements/blank_form/")
    check("...which starts empty", coi["blank_form"], [])
    check("nobody has anything to sign, and it says so",
          any("nothing for the authors to sign" in w
              for w in ms.conflict_form_warnings(coi, req)), True)

    # Kept apart from the who-has-not-returned-one warning ON PURPOSE. On a
    # first round they look identical from the ledger and the fix is the
    # opposite: chase four people, or go and find one PDF.
    check("...and that is a different warning from the missing-signature one",
          any("nothing for the authors to sign" in w
              for w in ms.conflict_warnings(coi)), False)

    wr(os.path.join(ms.blank_coi_dir(root), "acs_disclosure.pdf"), "x")
    coi = ms.conflict_statements(root)
    check("a downloaded form is reported", coi["blank_form"],
          ["acs_disclosure.pdf"])
    check("...and is NOT counted as a returned one", coi["forms"], [],
          "a blank among the signed ones is an author marked as having "
          "signed because somebody downloaded the form")
    check("...so Ada is still outstanding", coi["missing"], ["AB"])
    check("...and nothing asks for a form again",
          ms.conflict_form_warnings(coi, req), [])

    # A journal that collects disclosures in its portal publishes no form,
    # and that is a different state from nobody having looked.
    sub_n = os.path.join(tmp, "coiform_none")
    os.makedirs(sub_n, exist_ok=True)
    root_n = _scaffold(sub_n, "coinone")
    ms.init(root_n, "Langmuir")
    reqfile_n = os.path.join(ms.journal_dir(root_n, "Langmuir"),
                             "journal_requirements", "requirements.yml")
    write_raw(reqfile_n, re.sub(r"^  coi_form:.*$", "  coi_form: not_required",
                                read(reqfile_n), flags=re.M))
    write(root_n, "plan/author_information/authors.md",
          read(os.path.join(ms.author_info_dir(root_n), "authors.md")).replace(
              "| 1 |  |  |  |  |",
              "| 1 | Ada Bell | A |  | abell@example.edu |"))
    check("`not_required` is silent, and is not the same as unknown",
          ms.conflict_form_warnings(ms.conflict_statements(root_n),
                                    ms.read_flat_yml(reqfile_n)), [])


def test_folder_naming() -> None:
    section("Journal folder naming (spec 2.1)")

    check("a lowercase abbreviation is uppercased",
          ms.journal_folder("ijrobp"), "IJROBP")
    check("an abbreviation the user capitalised is kept",
          ms.journal_folder("JACS"), "JACS")
    check("spaces become underscores",
          ms.journal_folder("Nat Commun"), "Nat_Commun")
    check("slashes are stripped",
          ms.journal_folder("Acta Cryst. D/F"), "Acta_Cryst._DF")
    check("mixed case is not flattened - the folder is a proper noun",
          ms.journal_folder("PLoS_Biology"), "PLoS_Biology")


def test_init(root: str) -> None:
    section("init: the journal folder, and never overwriting (spec 2, 3, 4)")

    res = ms.init(root, "IJROBP")
    check("init succeeds on a scaffolded project", res["errors"], [])
    created = {a["path"] for a in res["actions"] if a["action"] == "create"}
    for want in ("drafts/IJROBP/writing_config.yml",
                 "drafts/IJROBP/journal_requirements/requirements.yml",
                 "drafts/log.md", "drafts/edits/README.md",
                 "plan/theme/journal_target.yml"):
        check(f"created {want}", want in created, True)

    proj = ms.read_project_yml(root)
    check("project.yml records the journal folder",
          "IJROBP" in proj.get("journals", ""), True)
    check("...and target_journal, which drives the figure geometry",
          proj.get("target_journal"), "IJROBP")

    # Hand-edit the config, then re-run. Nothing the user has touched may be
    # replaced - that guarantee is what makes init safe to re-run.
    cfg = os.path.join(root, "drafts", "IJROBP", "writing_config.yml")
    write(root, "drafts/IJROBP/writing_config.yml",
          read(cfg).replace("  results:        medium",
                            "  results:        high")
          + "\nnumber_density_waivers:\n  - results ¶2   # dose series\n")
    res = ms.init(root, "IJROBP")
    check("a second init creates nothing",
          [a for a in res["actions"] if a["action"] == "create"], [])
    check("...and does not touch the edited config",
          "  results:        high" in read(cfg), True)
    check("...and does not add the journal twice",
          ms.read_project_yml(root).get("journals"), "[IJROBP]")

    req = ms.read_flat_yml(os.path.join(root, "drafts", "IJROBP",
                                        "journal_requirements",
                                        "requirements.yml"))
    unknown = [k for k, v in req.items() if v == "unknown"]
    check("every requirement starts unknown, never inferred",
          len(unknown) > 30, True, f"{len(unknown)} unknown fields")
    check("...including the ones most tempting to guess",
          all(k in req and req[k] == "unknown"
              for k in ("text.font_size_pt", "text.margins_in",
                        "figures.dpi_raster", "references.max_count")), True)

    res = ms.init(os.path.join(root, "plan"), "JACS")
    check("init refuses a directory that is not a project",
          bool(res["errors"]), True)


def test_config_reading(root: str) -> None:
    section("writing_config.yml drives the checkers")

    _spec2 = importlib.util.spec_from_file_location(
        "prose_engine_m", os.path.join(ROOT, "tools", "prose.py"))
    if _spec2 is None or _spec2.loader is None:
        raise ImportError("cannot load tools/prose.py")
    prose = importlib.util.module_from_spec(_spec2)
    _spec2.loader.exec_module(prose)

    cfg = os.path.join(root, "drafts", "IJROBP", "writing_config.yml")
    level, waivers = prose.load_waivers(cfg)
    check("a per-section mapping leaves the scalar level unset - the two "
          "spellings mean different things and are never mixed", level, None)
    check("a waiver is read with its trailing comment stripped",
          waivers, ["results ¶2"])

    # prose 5.9. One budget over five sections that are not alike put 8 of 11
    # findings on Methods while the abstract sat at nearly 4x the same budget
    # in the same list. Per section is the fix, and the defaults were measured.
    den = ms.load_density(cfg)
    check("the per-section mapping is read", den["sections"]["results"],
          "high")
    check("...methods is unenforced, which is what the prose instruction "
          "always said", den["sections"]["methods"], "high")
    check("...and the abstract is a whole-section count, not a rate - a rate "
          "over 250 words is noise",
          den["sections"]["title_abstract"].get("basis"), "absolute")
    check("...with nothing ambiguous about the spelling", den["problems"], [])

    # The scalar spelling cannot change meaning under a new version of the
    # tool: an existing project's config still resolves for every section.
    write(root, "drafts/IJROBP/old_style_config.yml",
          "number_density: low\nnumber_density_waivers: []\n")
    old = os.path.join(root, "drafts", "IJROBP", "old_style_config.yml")
    check("the scalar spelling still resolves",
          prose.load_waivers(old), ("low", []))
    check("...and manuscript.py reads it the same way",
          (ms.load_density(old)["level"], ms.load_density(old)["sections"]),
          ("low", {}))


def test_custom_instructions(root: str) -> None:
    section("Telling the drafter what you want (spec 8.2)")

    res = ms.configure(root, "IJROBP",
                       instruct=["Keep the discussion under 900 words"])
    check("an instruction is recorded", res["errors"], [])
    items = res["config"]["lists"]["custom_instructions"]
    check("...once", len(items), 1)
    check("...with the date it was given", items[0].startswith(ms.TODAY), True,
          "a standing instruction has to be traceable to the round it started")

    res = ms.configure(root, "IJROBP",
                       instruct=["Keep the discussion under 900 words"])
    check("re-giving the same instruction is a no-op",
          len(res["config"]["lists"]["custom_instructions"]), 1)
    check("...and says so rather than silently doing nothing",
          any(c["action"] == "keep" for c in res["changes"]), True)

    # The user's own words survive the round trip. Quietly turning their
    # "novel" into 'novel', or truncating at a '#', is an edit nobody asked for.
    tricky = 'Do not use the word "novel"; the PI hates it # she really does'
    res = ms.configure(root, "IJROBP", instruct=[tricky])
    stored = res["config"]["lists"]["custom_instructions"][-1]
    check("a quote, an apostrophe and a hash all survive",
          stored.split(": ", 1)[1], tricky)

    res = ms.configure(root, "IJROBP", sets=["outline_adherence=loose"])
    check("a dial is set", res["config"]["dials"]["outline_adherence"], "loose")
    check("...keeping the file's comments",
          "# 1 none | 2 loose | 3 medium | 4 tight | 5 strict" in read(
              os.path.join(root, "drafts", "IJROBP", "writing_config.yml")),
          True, "this file is explicitly the user's to hand-edit")

    # A typo in a dial does not fail - it silently reverts that dial and biases
    # the run in a way nobody asked for. So it is refused on the way in.
    res = ms.configure(root, "IJROBP", sets=["hedging=aggressive"])
    check("a value that is not one of the options is refused",
          bool(res["errors"]), True)
    check("...naming the options", "minimal, moderate, heavy" in res["errors"][0],
          True)
    check("...and the dial is untouched",
          res["config"]["dials"]["hedging"], "moderate")

    res = ms.configure(root, "IJROBP", sets=["not_a_dial=x"])
    check("an unknown key is refused", bool(res["errors"]), True)

    res = ms.configure(root, "IJROBP", forget=["novel"])
    check("--forget drops the matching instruction",
          any('novel' in c["detail"] for c in res["changes"]
              if c["action"] == "drop"), True)
    check("...and leaves the others",
          len(res["config"]["lists"]["custom_instructions"]), 1)

    # The instructions have to reach the run, not just the file. plan carries
    # them so a caller cannot start a pipeline without them in hand.
    p = ms.plan(root, "IJROBP", "coauthor")
    check("plan carries the standing instructions",
          len(p["custom_instructions"]), 1)
    check("plan carries the dials", p["config"]["outline_adherence"], "loose")

    res = ms.configure(root, "IJROBP", style_ref=["10.1021/jacs.3c01234"])
    check("a style reference is recorded",
          res["config"]["lists"]["style_refs"], ["10.1021/jacs.3c01234"])

    # A dial edited by hand to something invalid is caught on read, not
    # ignored, and the warning travels with the plan.
    cfg = os.path.join(root, "drafts", "IJROBP", "writing_config.yml")
    write(root, "drafts/IJROBP/writing_config.yml",
          read(cfg).replace("voice: active_first_person", "voice: breezy"))
    check("a hand-edited dial with a bad value is reported",
          any("breezy" in w for w in ms.plan(root, "IJROBP", "draft")["warnings"]),
          True)
    write(root, "drafts/IJROBP/writing_config.yml",
          read(cfg).replace("voice: breezy", "voice: active_first_person"))

    # The two engines read this file separately; they must not disagree.
    _spec3 = importlib.util.spec_from_file_location(
        "prose_engine_c", os.path.join(ROOT, "tools", "prose.py"))
    if _spec3 is None or _spec3.loader is None:
        raise ImportError("cannot load tools/prose.py")
    prose = importlib.util.module_from_spec(_spec3)
    _spec3.loader.exec_module(prose)
    ms.configure(root, "IJROBP", waive=["results ¶2 # dose series, intentional"])
    mine = ms.read_config(cfg)
    theirs_level, theirs_waivers = prose.load_waivers(cfg)
    check("a per-section number_density is not read as an unset dial - it "
          "would be reported missing and the run biased by a default nobody "
          "asked for", mine["dials"]["number_density"], "per-section")
    check("manuscript.py and prose.py agree on the density level, because "
          "there is one parser and manuscript.py borrows it",
          ms.load_density(cfg)["level"], theirs_level)
    check("...and on the per-section mapping, for the same reason",
          ms.load_density(cfg)["sections"],
          prose.load_density_config(cfg)["sections"])
    check("...and on the waivers, hash and all",
          mine["lists"]["number_density_waivers"], theirs_waivers,
          "a waiver truncated at its '#' stops matching its paragraph id, and "
          "then silently stops waiving")


def test_recommend_preset(tmp: str) -> None:
    section("plan recommends a preset off the round, and never selects one")

    # The shape the user asked for on 2026-09-24: everything on the first
    # round, less in the middle, and the expensive checks back on when
    # submission is close. What is tested here is that it RECOMMENDS - a
    # selection would be a different feature and a worse one, because the
    # engine cannot see that the paper is going to a PI on Friday.
    root = build_project(tmp)

    r1 = ms.plan(root, "IJROBP", "coauthor")
    check("r1 recommends the full first pass",
          r1["recommended"]["preset"], "coauthor",
          "nothing has been drafted or checked yet, so every check is a "
          "first reading rather than a re-reading")
    check("...and says nothing when it agrees with what was asked for",
          r1["recommended_differs"], False,
          "a line saying 'I would have chosen this too' on every round is "
          "noise on the one line the user has to read before saying Go")
    check("...and the line carries no recommendation sentence",
          "looks like a" in r1["line"], False)

    # A middle round: prose exists and no edit is waiting.
    _bump_revision(root, 4)
    mid = ms.plan(root, "IJROBP", "coauthor")
    check("a later round with prose written recommends the lighter preset",
          mid["recommended"]["preset"], "draft",
          "re-running evidence, stats, attribution and reviewer over a "
          "paper that has not materially changed re-reports what they "
          "reported last round, at four agent calls")
    check("...and that is four fewer agent calls than what was asked for",
          len(ms.PRESETS["coauthor"]) - len(ms.PRESETS["draft"]), 4)
    check("...so the difference is surfaced", mid["recommended_differs"], True)
    check("...in the line, naming the word that switches it",
          "Say `draft` to switch" in mid["line"], True,
          "the user must not have to know the preset vocabulary to reach it")

    # An edit waiting outranks the round number: applying it is the round.
    edits = ms.edits_dir(ms.journal_dir(root, "IJROBP"))
    os.makedirs(edits, exist_ok=True)
    with io.open(os.path.join(edits, "from_pi.md"), "w",
                 encoding="utf-8", newline="\n") as fh:
        fh.write("# From the PI\n\nTighten the second paragraph.\n")
    rev = ms.plan(root, "IJROBP", "coauthor")
    check("an edit waiting in edits/ recommends the revision preset",
          rev["recommended"]["preset"], "revision",
          "whatever the round number says, a round with a coauthor's "
          "return in it is a round for applying it")
    check("...and the signals say which file decided it",
          "from_pi.md" in rev["recommended"]["signals"]["edits_inbox"], True)

    # The half that is NOT inferred, and the reason it is not.
    check("nothing is ever recommended as `submission`",
          {ms.recommend_preset(root, "IJROBP")["preset"]} <= {
              "draft", "coauthor", "revision"}, True,
          "a paper is ready to send when a person says it is; there is no "
          "count of finished sections that may promote a round to the one "
          "that builds a submission package")
    check("...but the escalation is named on every round anyway",
          "ready to submit" in rev["recommended"]["escalate"], True)

    # `recommend_preset` counts drafted sections itself rather than calling
    # `status`, because `status` builds the float provenance, the journal
    # requirements, the run ledger and a prose pass to answer one question
    # about five files - and this runs on every `plan`. That makes the
    # emptiness test a contract duplicated on purpose, which in this repo
    # means something has to compare the copies: a section that is a heading
    # and nothing else must not count as drafted in one and a stub in the
    # other.
    st = ms.source_text_dir(root)
    write(root, os.path.relpath(os.path.join(st, "discussion.md"), root),
          "# Discussion\n\n<!-- a comment, and no prose at all -->\n")
    from_status = sum(1 for s in ms.status(root, "IJROBP")["sections"]
                      if s["written"])
    from_rec = ms.recommend_preset(root, "IJROBP")["signals"]["sections_written"]
    check("the drafted-section count agrees with status', to the section",
          from_rec, from_status,
          "a heading plus an HTML comment is not drafted prose, and both "
          "copies of that rule have to say so")

    # The rule the whole toolkit is built on: it reports, it does not act.
    check("the recommendation changes no module in the plan",
          mid["modules"], ms.plan(root, "IJROBP", "coauthor")["modules"],
          "`--preset` has already won by the time this is computed")
    check("...and an explicit preset is still the one that runs",
          ms.plan(root, "IJROBP", "draft")["preset"], "draft")


def _bump_revision(root: str, n: int) -> None:
    """Set project.yml:revision without depending on a writer that may not
    exist - the test is about the recommendation, not about the setter."""
    p = os.path.join(root, "project.yml")
    with io.open(p, encoding="utf-8") as fh:
        body = fh.read()
    if re.search(r"(?m)^revision:", body):
        body = re.sub(r"(?m)^revision:.*$", "revision: %d" % n, body)
    else:
        body += "\nrevision: %d\n" % n
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)


def test_plan(root: str) -> None:
    section("plan: the module list, printed before anything runs (spec 2.3)")

    res = ms.plan(root, "IJROBP", "draft")
    check("the draft preset is nine modules",
          res["modules"],
          ["outline", "literature-landscape", "learn-from-edits",
           "draft-sections", "revise-prose", "comprehension-check",
           "quality-check",
           "assemble", "citation-check"],
          "outline completes an incomplete outline and "
          "literature-landscape hands draft-sections a view of the field; "
          "both run before it, which is what makes them worth having, and "
          "comprehension-check reads what 1c produced")
    check("...and `draft` carries no attribution-check, on any paper kind",
          "attribution-check" in res["modules"], False,
          "it is the prose preset; the checking half starts at `coauthor`")
    check("...nor is it silently dropped from a preset that never had it",
          res["kind_dropped"], [],
          "`draft` has no attribution-check to take out, and reporting one "
          "as dropped would say a check was skipped that was never offered")
    # The preset that DOES carry it is where the report has to appear: a
    # plan that lists a check tells the user it ran, so a plan that drops
    # one has to say so (review-paper 8).
    co = ms.plan(root, "IJROBP", "coauthor")
    # citation-integrity 6.2, 2026-09-20. These three checks used to assert
    # the opposite - that a research paper's plan DROPPED attribution-check
    # and said so - and that gate is what let a member's paper ship with
    # citation errors an outside reader then found.
    check("a research paper's plan carries it too, as of 2026-09-20",
          "attribution-check" in co["modules"], True,
          "a research paper's Introduction and Discussion are made of cited "
          "claims, and evidence-check only asks whether a citation is there")
    check("...and nothing is dropped for the paper kind",
          [d["module"] for d in co["kind_dropped"]], [])
    check("...and it runs BESIDE stats-check, never instead of it",
          "stats-check" in co["modules"], True,
          "one asks whether this project's numbers are stated correctly, "
          "the other whether somebody else's paper says what this sentence "
          "claims it says")
    check("the plan carries the paper kind", co["paper_kind"], "research")
    check("citation-check is in every preset that writes prose, without "
          "being asked for",
          [p for p in ms.PRESETS
           if "citation-check" not in ms.PRESETS[p]], [],
          "it is mechanical - keys resolve, entries are cited, no "
          "duplicates, count under the cap - so offering it as an --add is a "
          "yes/no question with one right answer (item 36)")
    check("learn-from-edits runs BEFORE draft-sections, so a section drafted "
          "this round is drafted with the lesson in its brief rather than "
          "patched afterwards",
          res["modules"].index("learn-from-edits")
          < res["modules"].index("draft-sections"), True)
    check("revise-prose runs after drafting and before assemble, so "
          "everything downstream sees revised prose and the built .docx is "
          "never orphaned",
          res["modules"].index("draft-sections")
          < res["modules"].index("revise-prose")
          < res["modules"].index("assemble"), True)

    res = ms.plan(root, "IJROBP", "coauthor")
    check("coauthor is thirteen", len(res["modules"]), 13,
          "twelve until 2026-09-20, when attribution-check stopped being "
          "a review's module and joined every paper kind")
    check("...ending on reviewer-check", res["modules"][-1],
          "reviewer-check",
          "a draft a coauthor is about to read has been held to the "
          "journal's own rules first")
    check("the round is the next one, not the current", res["round"], 1)

    res = ms.plan(root, "IJROBP", "draft", add=["reviewer-check"],
                  drop=["stats-check"])
    check("+reviewer-check adds it", "reviewer-check" in res["modules"], True)
    res = ms.plan(root, "IJROBP", "coauthor", drop=["stats-check"])
    check("-stats-check removes it", "stats-check" in res["modules"], False)
    check("...and the list stays in pipeline order",
          res["modules"].index("assemble")
          < res["modules"].index("citation-check"), True)

    res = ms.plan(root, "IJROBP", "submission", drop=["citation-check"])
    check("citation-check cannot be dropped from a submission run",
          "citation-check" in res["modules"], True)
    check("...and says why", bool(res["warnings"]), True)
    check("...and is put back in pipeline order, not appended",
          res["modules"].index("citation-check")
          < res["modules"].index("reviewer-check"), True)

    res = ms.plan(root, "IJROBP", "coauthor", add=["nonsense"])
    check("an unknown module is reported, not silently added",
          "nonsense" in res["modules"], False)


def test_assemble_gates(root: str) -> None:
    section("assemble: the three gates (spec 5.4, 9.0)")

    if not shutil.which("pandoc"):
        skip("every assemble check", "pandoc is not on PATH")
        return

    jdir = os.path.join(root, "drafts", "IJROBP")

    # --- gate 1: mock data -----------------------------------------------
    write(root, "plan/floats/float_provenance.json", json.dumps({
        "fig01_geometry": {"kind": "figure", "mock": True, "files": []},
        "fig02_symmetry": {"kind": "figure", "mock": False, "files": []}}))
    res = ms.assemble(root, "IJROBP")
    check("a mock float refuses the build", bool(res["errors"]), True)
    check("...and names the float", "fig01_geometry" in res["errors"][0], True)
    check("...and writes nothing", glob.glob(os.path.join(jdir, "*.docx")), [])

    res = ms.assemble(root, "IJROBP", allow_mock=True)
    check("--allow-mock builds", res["errors"], [])
    check("...and stamps the filename, so it cannot be mistaken for a real one",
          os.path.basename(res["output"]), "manuscript_r1_MOCK.docx")
    # It used to be enough that a missing reference.docx was *reported*. It
    # is not: pandoc's own default sets every heading in Word's theme, and
    # the first real project shipped a manuscript whose headings were 20pt
    # Aptos Display in accent blue over a Times New Roman body, past every
    # gate there was. The first build of a journal folder makes one.
    check("the first build generates the missing reference.docx",
          any("built journal_requirements/reference.docx" in w
              for w in res["warnings"]), True)
    check("...and it is on disk afterwards",
          os.path.isfile(os.path.join(jdir, "journal_requirements",
                                      "reference.docx")), True)
    os.remove(res["output"])
    os.remove(os.path.join(root, "plan", "floats", "float_provenance.json"))

    # --- a clean build ----------------------------------------------------
    res = ms.assemble(root, "IJROBP")
    check("a clean project builds", res["errors"], [])
    check("...to manuscript_r1.docx",
          os.path.basename(res["output"]), "manuscript_r1.docx")
    check("...that really exists", os.path.isfile(res["output"]), True)
    check("...with no build file left behind",
          glob.glob(os.path.join(jdir, ".build_*")), [])
    check("...and writes the .ris escape hatch",
          os.path.isfile(os.path.join(root, "drafts", "references.ris")), True)

    rendered = subprocess.run(
        [pandoc_bin(), res["output"], "-t", "plain"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace").stdout
    check("citekeys are rendered, not shipped",
          "jensen2019arrays" in rendered, False)
    check("...as the journal's own formatted text",
          "Jensen" in rendered and "2019" in rendered, True)
    check("...with a reference list built from the .bib",
          "Annu Rev Microbiol" in rendered, True)
    check("a flag survives into the .docx, where a coauthor cannot miss it",
          "FLAG: author" in rendered, True)
    check("...and the build says how many", res["flags_in_build"], 1)
    check("a missing style.csl is reported, not silently defaulted",
          any("style.csl" in w for w in res["warnings"]), True)
    check("a reference.docx that already exists is reused, not rebuilt",
          any("built journal_requirements/reference.docx" in w
              for w in res["warnings"]), False,
          "rebuilding every round would discard a reference doc the user "
          "has edited by hand")
    check("unknown requirements are reported on every build",
          any("still unknown" in w for w in res["warnings"]), True)

    # --- gate 2: an unresolved citekey, caught on citeproc's warning ------
    before = set(glob.glob(os.path.join(jdir, "*.docx")))
    disc = stfile(root, "discussion.md")
    original = read(disc)
    write(root, "drafts/source_text/discussion.md",
          original + "\nReported elsewhere [@nobody2021missing].\n")
    res = ms.assemble(root, "IJROBP")
    check("an unresolved citekey fails the build", bool(res["errors"]), True)
    check("...naming the key", res["missing_keys"], ["nobody2021missing"])
    check("...and the partial output is deleted",
          set(glob.glob(os.path.join(jdir, "*.docx"))), before)

    # --- gate 3: residue citeproc never warned about ----------------------
    # Measured, not assumed: a key inside a code span reaches the .docx with
    # no citeproc warning at all, so gate 2 cannot see it.
    write(root, "drafts/source_text/discussion.md",
          original + "\nA verbatim key: `[@jensen2019arrays]`.\n")
    res = ms.assemble(root, "IJROBP")
    check("citeproc raises no warning on a verbatim key",
          res.get("missing_keys"), None)
    check("...but gate 3 fails the build anyway", bool(res["errors"]), True)
    check("...naming what it found",
          any("[@jensen2019arrays]" in r["found"] for r in res["residue"]),
          True)
    check("...and deleting the partial output",
          set(glob.glob(os.path.join(jdir, "*.docx"))), before)

    # --- item 26: a `~n~` subscript run butted against an at-sign ---------
    # Measured against real pandoc, not assumed: handed over unescaped,
    # `SiO~2~@Cu~2~GaBO~5~@TiO~2~` renders as `SiO₂(Cu~2~GaBO~5?)_((TiO?))2~`
    # and pandoc invents two citekeys out of the formula, which is how a real
    # reference was dropped from LANGMUIR r1. The engine escapes the at-sign
    # now, so the build has to survive this outright.
    write(root, "drafts/source_text/discussion.md",
          original + "\nThe SiO~2~@Cu~2~GaBO~5~@TiO~2~ shell was stable "
                     "[@jensen2019arrays].\n")
    res = ms.assemble(root, "IJROBP")
    check("a core@shell formula does not fail the build", res["errors"], [])
    core_shell = subprocess.run(
        [pandoc_bin(), res["output"], "-t", "plain"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace").stdout
    check("...the formula reads as written",
          "SiO₂@Cu₂GaBO₅@TiO₂" in core_shell, True, core_shell[-400:])
    check("...pandoc invented no citekey out of it",
          res.get("missing_keys"), None)
    check("...and the real citation beside it still resolved",
          "Jensen" in core_shell, True)

    write(root, "drafts/source_text/discussion.md", original)


def test_escape_bare_at() -> None:
    section("Escaping the at-sign pandoc cannot read (item 26)")

    check("a subscript run beside an at-sign is escaped",
          ms._escape_bare_at("SiO~2~@Cu~2~"), "SiO~2~\\@Cu~2~")
    check("a bracketed citekey is left alone",
          ms._escape_bare_at("as shown [@stock2012]"), "as shown [@stock2012]")
    check("...and so is a bare one after a space",
          ms._escape_bare_at("see @stock2012"), "see @stock2012")
    check("...and one opening a line",
          ms._escape_bare_at("@stock2012 says"), "@stock2012 says")
    check("a suppressed-author citekey is left alone",
          ms._escape_bare_at("[-@stock2012]"), "[-@stock2012]")
    check("an email is escaped, which is what renders it verbatim",
          ms._escape_bare_at("a@b.edu"), "a\\@b.edu")


def test_rebuild_stays_in_its_round(tmp: str) -> None:
    section("A rebuild overwrites its round; only `round` advances it")

    if not shutil.which("pandoc"):
        skip("every rebuild-round check", "pandoc is not on PATH")
        return

    # Its own project: this test calls `round`, which snapshots and bumps
    # project.yml:revision, and test_rounds below measures both on the shared
    # fixture.
    sub_tmp = os.path.join(tmp, "rounds")
    os.makedirs(sub_tmp, exist_ok=True)
    root = build_project(sub_tmp)
    ms.init(root, "IJROBP")
    jdir = os.path.join(root, "drafts", "IJROBP")
    # The live round's artefacts live in submission/, not in the journal
    # folder root (item 17). The root holds the standing files only.
    sdir = os.path.join(jdir, "submission")

    outs = []
    for _ in range(3):
        res = ms.assemble(root, "IJROBP")
        if res["errors"]:
            skip("every rebuild-round check", "; ".join(res["errors"]))
            return
        outs.append(os.path.basename(res["output"]))
    check("three assembles in a row build one file", set(outs),
          {"manuscript_r1.docx"})
    check("...and the folder holds exactly that",
          sorted(os.path.basename(f) for f in
                 glob.glob(os.path.join(sdir, "manuscript_r*.docx"))),
          ["manuscript_r1.docx"],
          "every rebuild used to open a new round, leaving r1 through r5 "
          "beside a run_state.json and a reports/r1/ that still said r1")
    check("...and no manuscript is left in the journal folder root",
          glob.glob(os.path.join(jdir, "manuscript_r*.docx")), [],
          "the root mixes bookkeeping and inputs; what is sent lives in "
          "submission/ (item 17)")
    check("...and status still reports round 1",
          ms.status(root, "IJROBP")["round"], 1)
    check("...as does the module plan",
          ms.plan(root, "IJROBP", "draft")["round"], 1)

    # The counter moves when `round` says so, and not before.
    res = ms.open_round(root, "IJROBP")
    check("round retires r1 and opens r2",
          (res["retired"], res["round"]), (1, 2))
    check("...recording it in the journal folder, since nothing else can",
          ms.round_state(jdir).get("round"), 2)
    res = ms.assemble(root, "IJROBP")
    check("...and the next build is r2",
          os.path.basename(res["output"]), "manuscript_r2.docx")
    check("...having retired r1 into its own record",
          os.path.isfile(os.path.join(
              root, "obsolete/drafts/r1/submission/manuscript_r1.docx")), True,
          "a round's record retires whole; the journal folder holds the live "
          "round only (item 17)")
    check("...so submission/ holds only the live round",
          sorted(os.path.basename(f) for f in
                 glob.glob(os.path.join(sdir, "manuscript_r*.docx"))),
          ["manuscript_r2.docx"])

    # Item 8: a rebuild refuses to overwrite a build somebody has edited.
    live = os.path.join(sdir, "manuscript_r2.docx")
    keep = _slurp(live, "rb")
    with open(live, "ab") as fh:
        fh.write(b"\x00edited in Word")
    edited = ms.assemble(root, "IJROBP")
    check("a rebuild over an edited .docx is refused",
          any("REFUSED" in e for e in edited["errors"]), True,
          str(edited["errors"]))
    check("...naming the file",
          any("manuscript_r2.docx" in e for e in edited["errors"]), True)
    check("...and saying where the change has to go instead",
          any("edits/" in e and "--force" in e for e in edited["errors"]),
          True, str(edited["errors"]))
    check("...and the edit is still there afterwards",
          _slurp(live, "rb").endswith(b"edited in Word"), True)
    forced = ms.assemble(root, "IJROBP", force=True)
    check("--force overwrites it, as the refusal said",
          forced["errors"], [])
    check("...and the rebuilt file is this engine's again",
          ms.assemble(root, "IJROBP")["errors"], [],
          "the build records its own hash, so the next one is silent")
    with open(live, "wb") as fh:
        fh.write(keep)
    ms.assemble(root, "IJROBP", force=True)

    # Overwriting in place makes the gates' cleanup dangerous: the partial
    # output they delete now has the same name as the last good build.
    disc = stfile(root, "discussion.md")
    original = read(disc)
    write(root, "drafts/source_text/discussion.md",
          original + "\nReported elsewhere [@nobody2021missing].\n")
    bad = ms.assemble(root, "IJROBP")
    check("a failed rebuild still fails", bool(bad["errors"]), True)
    check("...without taking the round's last good build with it",
          os.path.isfile(os.path.join(sdir, "manuscript_r2.docx")), True)
    check("...and leaves no staged build behind",
          glob.glob(os.path.join(jdir, ".build_*")), [])


def test_references_heading(tmp: str) -> None:
    section("The bibliography gets a heading, wherever the order puts it")

    if not shutil.which("pandoc"):
        skip("every references-heading check", "pandoc is not on PATH")
        return

    sub_tmp = os.path.join(tmp, "refshead")
    os.makedirs(sub_tmp, exist_ok=True)
    root = build_project(sub_tmp)
    ms.init(root, "Langmuir")
    reqp = os.path.join(root, "drafts", "Langmuir", "journal_requirements",
                        "requirements.yml")
    DEFAULT = ("section_order: [title_abstract, introduction, methods, "
               "results, discussion]")

    def bib_after_heading(out: str, want_heading: str) -> None:
        paras = paragraphs(ms._docx_part(out, "word/document.xml"))
        heads = [i for i, (st, tx) in enumerate(paras)
                 if st.startswith("Heading") and tx == want_heading]
        check("%r is a heading in the built file" % want_heading,
              bool(heads), True)
        if not heads:
            return
        i = heads[0]
        check("...at level 1", paras[i][0], "Heading1")
        after = [st for st, tx in paras[i + 1:] if st or tx]
        check("...and the reference list is what follows it",
              after[0] if after else "", "Bibliography",
              "17 correctly numbered entries in Bibliography style once sat "
              "under whatever heading the previous section carried")

    # (a) the order does not name it - the scaffold's default - so the build
    #     supplies the heading itself rather than letting citeproc drop the
    #     list under the last body section.
    res = ms.assemble(root, "Langmuir")
    if res["errors"]:
        skip("every references-heading check", "; ".join(res["errors"]))
        return
    bib_after_heading(res["output"], "References")

    # (b) the order names it, in the journal's own words, which is the string
    #     that has to reach the page.
    write(root, os.path.relpath(reqp, root), read(reqp).replace(
        DEFAULT,
        "section_order: [Title, Author list, Abstract, Introduction, "
        "Experimental Section, Results and Discussion, Literature Cited]"))
    res = ms.assemble(root, "Langmuir")
    if res["errors"]:
        skip("the named references section", "; ".join(res["errors"]))
        return
    check("a named references section is reported as included",
          "Literature Cited" in res["sections"], True)
    bib_after_heading(res["output"], "Literature Cited")


def test_incomplete_paper(tmp: str) -> None:
    section("A half-written paper still builds, and says so")

    # A project with a half-finished outline, three sections still stubs, and
    # no analysis. This is the normal state for most of a project's life, and
    # being refused a draft here would make the pipeline useless exactly when
    # it is most useful.
    root = os.path.join(tmp, "halfdone")
    run = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "scaffold.py"),
         "scaffold", root, "--title", "Half done", "--field", "biochemistry"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if run.returncode != 0:
        skip("every incomplete-paper check", "scaffold failed")
        return
    ms.init(root, "JACS")

    write(root, "plan/outline.md",
          "## Introduction\n\n"
          "- Arrays have been resolved in mesophiles. [jensen2019arrays]\n\n"
          "## Results\n\n"
          "- Arrays keep hexagonal packing across the range. [Fig 1]\n"
          "- Order is lost when the adaptor is deleted.\n\n"
          "## Discussion\n\n-\n")
    write(root, "plan/captions.md",
          "## Figure 1 — fig01_geometry.png\n"
          "**Arrays keep hexagonal packing at every growth temperature.**\n"
          "(A) Central sections.\n")
    write(root, "drafts/references.bib", BIB.replace("jensen2019arrays",
                                                     "jensen2019arrays"))
    write(root, "drafts/source_text/introduction.md",
          "# Introduction\n\nArrays have been resolved in a dozen mesophilic "
          "species [@jensen2019arrays].\n")
    write(root, "drafts/source_text/results.md",
          "# Results\n\nArrays from both species kept the hexagonal packing "
          "reported so far (Figure 1).\n")
    # methods and discussion stay as scaffolded stubs. The abstract does NOT:
    # item 19's rule is that whatever exists gets written and what is missing
    # is one **[FLAG: data]** at the end of it, so this is what a half-written
    # paper's abstract is supposed to look like. An empty box fails the build.
    write(root, "drafts/source_text/title_abstract.md",
          "# Title\n\nArrays keep hexagonal packing across the range\n"
          "\n## Abstract\n\nWe asked whether chemoreceptor array packing "
          "changes across growth temperature, and imaged arrays from two "
          "species by cryo-ET. **[FLAG: data - the symmetry analysis has not "
          "been run, so the abstract states no result yet.]**\n")

    res = ms.completeness(root, "JACS")
    check("the paper is reported as not complete", res["complete"], False)
    areas = set(res["by_area"])
    check("an outline line with no evidence is listed", "outline" in areas, True)
    check("sections still stubs are listed", "draft" in areas, True)
    check("the missing analysis is listed", "analysis" in areas, True)
    check("unknown journal requirements are listed", "journal" in areas, True)
    check("the summary leads with the verdict",
          res["summary"].startswith("PAPER NOT COMPLETE"), True)
    check("every item says which file to open",
          all(m["where"] for m in res["missing"]), True)

    if not shutil.which("pandoc"):
        skip("the incomplete build checks", "pandoc is not on PATH")
        return

    # ...and an empty abstract box is the one gap that does not build, because
    # it is not an unfinished paper - it is a paper whose sections were
    # written and whose abstract was skipped (item 19).
    keep = read(stfile(root, "title_abstract.md"))
    write(root, "drafts/source_text/title_abstract.md",
          "# Title\n\nArrays keep hexagonal packing across the range\n"
          "\n## Abstract\n\n")
    empty = ms.assemble(root, "JACS")
    check("an empty abstract box fails the build",
          any("REFUSED" in e and "Abstract" in e for e in empty["errors"]),
          True, str(empty["errors"]))
    check("...and the refusal says the abstract is written last",
          any("drafted LAST" in e for e in empty["errors"]), True)
    write(root, "drafts/source_text/title_abstract.md", keep)

    built = ms.assemble(root, "JACS")
    check("an unfinished paper still builds", built["errors"], [])
    check("...with a half-abstract and one flag in it, not an empty box",
          "FLAG: data" in read(stfile(root, "title_abstract.md")), True)
    check("...and the build knows it is unfinished", built["complete"], False)
    check("...and says so in its warnings",
          any("PAPER NOT COMPLETE" in w for w in built["warnings"]), True)

    rendered = subprocess.run(
        [pandoc_bin(), built["output"], "-t", "plain"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace").stdout
    check("the .docx itself carries the verdict",
          "PAPER NOT COMPLETE" in rendered, True,
          "the reader who most needs it is the coauthor who never saw the "
          "terminal")
    check("...as a flag, so it reads like every other unresolved item",
          "[FLAG: incomplete" in rendered, True)
    check("...listing what is outstanding",
          "still a stub" in rendered, True)
    check("...and where to fix it",
          ms.stpfx(root) + "methods.md" in rendered, True)

    # The block is generated, so it has to disappear on its own once the work
    # is done - a stale PAPER NOT COMPLETE on a finished paper would be worse
    # than none at all.
    check("the block says it is generated and self-removing",
          "disappears from the build by itself" in rendered, True)
    empty = {"complete": True, "missing": []}
    check("a complete paper gets no block at all",
          ms.render_incomplete_block(empty, "JACS", 1), "")

    # Completeness is not a refusal: it must not use the exit code that means
    # "I will not do this".
    cli = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "manuscript.py"),
         "completeness", root, "--journal", "JACS"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("the CLI exits 1 for unfinished, not 2 for refused",
          cli.returncode, 1)


def test_ris(root: str) -> None:
    section("The EndNote escape hatch (spec 9)")

    entries = ms.parse_bib(BIB)
    check("the .bib parses", len(entries), 1)
    check("...with its key", entries[0]["key"], "jensen2019arrays")

    # A nested brace in a title is normal, and a non-greedy regex over the
    # whole entry stops at the wrong closing brace.
    nested = ("@article{x2020y,\n  title = {A {Cryo-EM} study},\n"
              "  author = {A, B},\n  year = {2020}\n}\n")
    check("a nested brace in a title survives",
          ms.parse_bib(nested)[0]["fields"]["title"], "A {Cryo-EM} study")

    ris = ms.to_ris(entries)
    check("each author gets its own AU line", ris.count("AU  - "), 2)
    check("a page range splits into SP and EP",
          ("SP  - 211" in ris and "EP  - 230" in ris), True)
    check("the citekey travels as the record ID",
          "ID  - jensen2019arrays" in ris, True)
    check("every record is terminated", ris.count("ER  - "), 1)


def test_rounds(root: str) -> None:
    section("Round snapshots, unconditional by design (spec 10.2, 10.4)")

    jdir = os.path.join(root, "drafts", "IJROBP")
    if not glob.glob(os.path.join(jdir, "submission", "manuscript_r*.docx")):
        write(root, "drafts/IJROBP/submission/manuscript_r1.docx",
              "not really a docx")

    write(root, "plan/figures/fig01_geometry.png", "png-1")
    write(root, "plan/figures/fig01_geometry.R", "# the script that made it")
    write(root, "plan/figures/fig02_symmetry.png", "png-2")
    write(root, "plan/tables/tab01_strains.rds", "rds-1")

    res = ms.open_round(root, "IJROBP")
    check("r1 is retired and r2 opens", (res["retired"], res["round"]), (1, 2))
    for rel in ("obsolete/figures/r1/fig01_geometry.png",
                "obsolete/figures/r1/fig01_geometry.R",
                "obsolete/tables/r1/tab01_strains.rds",
                # The prose goes into a folder named for the live folder it
                # came out of, so it does not mix with the submission/,
                # reports/ and edits/ that retired beside it
                # (round-archive 3.1).
                "obsolete/drafts/r1/source_text_r1/results.md"):
        check(f"snapshotted {rel}",
              os.path.isfile(os.path.join(root, rel)), True)
    check("the generating script is snapshotted, not just the output",
          os.path.isfile(os.path.join(root, "obsolete/figures/r1/"
                                            "fig01_geometry.R")), True)
    # This reverses the old spec 10.2 rule, on the user's instruction after
    # seeing the folder: the journal folder holds the live round and the
    # standing files, and a round's record retires whole (item 17).
    check("manuscript_r1.docx retires with its round",
          os.path.isfile(os.path.join(root, "obsolete/drafts/r1/submission/"
                                            "manuscript_r1.docx")), True)
    check("...leaving no manuscript in the journal folder",
          glob.glob(os.path.join(jdir, "submission", "manuscript_r*.docx"))
          + glob.glob(os.path.join(jdir, "manuscript_r*.docx")), [])
    check("...and the manifest lists what moved",
          "submission/manuscript_r1.docx" in
          read(os.path.join(root, "obsolete/drafts/r1/manifest.md")), True)
    check("log.md does not retire - it is the history",
          os.path.isfile(ms.log_path(root)), True)
    check("...and it is the project's, not this journal folder's (item 42)",
          os.path.isfile(os.path.join(jdir, "log.md")), False)
    check("...nor is there a run_log.md left in the journal folder root",
          os.path.isfile(os.path.join(jdir, "run_log.md")), False)
    check("...nor does edits_status.md, which final-check reads every round",
          os.path.isfile(os.path.join(ms.edits_dir(jdir), "edits_status.md"))
          or not os.path.isfile(os.path.join(
              root, "obsolete/drafts/r1/edits/edits_status.md")), True)

    manifest = read(os.path.join(root, "obsolete/figures/r1/manifest.md"))
    check("the manifest lists every file, scripts included",
          manifest.count("| fig"), 3,
          "two .png plus the .R that made one of them")
    check("...with a hash", bool(re.search(r"\| [0-9a-f]{12}\.\.\. \|",
                                           manifest)), True)

    # The instinct to skip unchanged files defeats the whole point: the
    # guarantee is that obsolete/figures/r1/ IS what r1 contained.
    write(root, "drafts/IJROBP/submission/manuscript_r2.docx",
          "still not a docx")
    write(root, "plan/figures/fig02_symmetry.png", "png-2-CHANGED")
    res = ms.open_round(root, "IJROBP")
    check("a second round snapshots again", res["retired"], 2)
    check("...including the byte-identical file",
          os.path.isfile(os.path.join(root, "obsolete/figures/r2/"
                                            "fig01_geometry.png")), True)
    manifest = read(os.path.join(root, "obsolete/figures/r2/manifest.md"))
    check("the manifest says which file changed",
          re.search(r"fig02_symmetry\.png \| [0-9a-f]+\.\.\. \| YES",
                    manifest) is not None, True)
    check("...and which did not",
          re.search(r"fig01_geometry\.png \| [0-9a-f]+\.\.\. \| no",
                    manifest) is not None, True)

    check("project.yml:revision tracks the global round",
          ms.read_project_yml(root).get("revision"), "3")


def test_ingest(root: str) -> None:
    section("The edits inbox and the ledger (spec 10.3)")

    fixture = os.path.join(FIXTURES, "tracked_two_authors.docx")
    if not os.path.isfile(fixture):
        skip("every ingest check", "tests/fixtures/ are missing; run "
                                   "tests/make_docx_fixtures.py")
        return

    edits = os.path.join(root, "drafts", "edits")
    os.makedirs(edits, exist_ok=True)
    shutil.copy2(fixture, os.path.join(edits, "manuscript_r2_JV.docx"))
    # Observed on a real project: OneDrive drops a conflict copy beside the real
    # file, and reading it is one coauthor's edits counted twice.
    shutil.copy2(fixture,
                 os.path.join(edits, "manuscript_r2_JV-DESKTOP-A4B6PBU.docx"))
    write(root, "drafts/edits/reviewer_report_r2.md",
          "Reviewer #1\n\n"
          "1. The authors never state n per condition in the results text.\n"
          "2. The claim rests on two species; please temper it.\n\n"
          "Reviewer #2\n\n"
          "1. The introduction repeats the abstract; cut the second "
          "paragraph.\n\n"
          "Editor\n\n"
          "1. Please supply a data availability statement.\n")

    res = ms.ingest(root, "IJROBP")
    check("the OneDrive conflict copy is excluded",
          res["conflict_copies"], ["manuscript_r2_JV-DESKTOP-A4B6PBU.docx"])
    check("...so only the real file is read", len(res["files_read"]), 2)

    sources = set(res["rollup"])
    check("reviewers are separate sources",
          {"R1", "R2", "Editor"} <= sources, True)
    check("R1's two points are both found", res["rollup"]["R1"]["items"], 2)
    check("the editor's paragraph is attributed to the Editor",
          res["rollup"]["Editor"]["items"], 1)

    # The filename says whose return it is; the OOXML says who made each
    # revision, and crediting them all to the filename is a silent
    # misattribution of authorship.
    check("a forwarded file's other author is credited to themselves",
          "GJ" in sources, True)
    check("...and the forwarding is reported",
          any("forwarded file" in w for w in res["warnings"]), True)

    check("everything starts pending",
          all(i["state"] == "pending" for i in res["items"]), True)

    # --- the guarantee: an applied edit never falls off the list ----------
    status = os.path.join(edits, "edits_status.md")
    text = read(status)
    text = re.sub(r"(\| R1-01 \|.*?\| )pending( \| )( \|)",
                  r"\1applied\2n added to results 2\3", text)
    text = re.sub(r"(\| GJ-01 \|.*?\| )pending( \|)", r"\1applied\2", text)
    write(root, "drafts/edits/edits_status.md", text)

    res = ms.ingest(root, "IJROBP")
    check("a regeneration finds no new items", res["new_items"], 0)
    check("...and the hand-marked states survive",
          (res["rollup"]["R1"]["applied"], res["rollup"]["GJ"]["applied"]),
          (1, 1))
    check("...with their notes",
          any(i["note"].strip() == "n added to results 2"
              for i in res["items"]), True)

    n_items = len(res["items"])
    os.remove(os.path.join(edits, "manuscript_r2_JV.docx"))
    res = ms.ingest(root, "IJROBP")
    check("a returned file leaving the inbox does not delete its history",
          len(res["items"]), n_items)
    check("...and the retained rows say so",
          any("no longer in edits/" in read(status) for _ in [0]), True)

    write(root, "drafts/edits/manuscript_r2_XX.docx", "not a docx")
    res = ms.ingest(root, "IJROBP")
    check("an initial that is not in authors.md is asked about, never guessed",
          any("not an author" in w for w in res["warnings"]), True)
    os.remove(os.path.join(edits, "manuscript_r2_XX.docx"))

    write(root, "drafts/edits/notes from jane.docx", "not a docx")
    res = ms.ingest(root, "IJROBP")
    check("an unparseable filename is asked about, never guessed",
          any("does not parse" in w for w in res["warnings"]), True)
    os.remove(os.path.join(edits, "notes from jane.docx"))


def test_response(root: str) -> None:
    section("The response letter, rendered from the ledger (spec 5.11)")

    status = os.path.join(root, "drafts", "edits", "edits_status.md")
    if not os.path.isfile(status):
        skip("every response check", "no ledger; ingest was skipped")
        return

    res = ms.response(root, "IJROBP")
    check("it refuses while any point is pending", bool(res["errors"]), True)
    check("...and says why for each",
          all("pending" in e for e in res["errors"]
              if "still pending" in e), True)
    check("...writing nothing", res["output"], None)

    text = read(status)
    text = re.sub(r"(\| R1-02 \|.*?\| )pending( \| )( \|)",
                  r"\1declined\2\3", text)
    write(root, "drafts/edits/edits_status.md", text)
    res = ms.response(root, "IJROBP")
    check("a declined point with no recorded reason is refused",
          any("declined with no recorded reason" in e for e in res["errors"]),
          True, "for a reviewer point the reason IS the letter's argument")

    text = read(status)
    text = re.sub(r"(\| R1-02 \|.*?\| )declined( \| )( \|)",
                  r"\1declined\2a third species is a separate study\3", text)
    text = re.sub(r"(\| R2-01 \|.*?\| )pending( \| )( \|)",
                  r"\1applied\2second paragraph cut\3", text)
    text = re.sub(r"(\| Editor-01 \|.*?\| )pending( \| )( \|)",
                  r"\1applied\2statement added\3", text)
    write(root, "drafts/edits/edits_status.md", text)

    res = ms.response(root, "IJROBP")
    check("with every point resolved it renders", res["errors"], [])
    letter = read(res["output"])
    check("every reviewer point is quoted",
          letter.count("**R1-") + letter.count("**R2-")
          + letter.count("**Editor-"), 4)
    check("an applied point says the change was made",
          "We have made this change. n added to results 2" in letter, True)
    check("a declined point states the recorded reason as the argument",
          "We have not made this change. a third species is a separate study"
          in letter, True)
    check("line numbers are flagged for the built revision, never invented",
          "[FLAG: author" in letter, True)

    res = ms.response(root, "IJROBP", tone="neutral")
    check("the tone dial changes the opening",
          "We thank the reviewers" in read(res["output"]), False)


def test_status(root: str) -> None:
    section("status: what a session should never have to re-ask")

    res = ms.status(root, "IJROBP")
    check("it reports the journals targeted", res["journals"], ["IJROBP"])
    check("...the round", res["round"] >= 1, True)
    check("...which sections are written",
          {s["section"] for s in res["sections"] if s["written"]},
          {"title_abstract", "introduction", "methods", "results",
           "discussion"})
    check("...the bibliography size", res["bib_entries"], 1)
    check("...whether the analysis has been run", res["stats_output"], False)
    check("...and how many requirements are still unknown",
          res["requirements_unknown_count"] > 30, True)


def test_r_reads_journal_target(root: str) -> None:
    section("Sourced figure geometry reaches R (spec 4.2)")

    rscript = ms.rscript()
    if not rscript:
        skip("the journal_target.yml check", "Rscript was not found")
        return

    write(root, "plan/theme/journal_target.yml",
          'journal: "IJROBP"\nsingle: 3.35\ndouble: 6.9\ndpi: 600\n'
          'dpi_line:\nbase_size:\n')
    # The project path goes through the environment rather than --args: with
    # `Rscript -e`, commandArgs(TRUE) does not carry them the way a script file
    # does, and the failure then looks like a broken setwd() rather than a
    # broken test.
    run = subprocess.run(
        [rscript, "-e",
         'setwd(Sys.getenv("PROJECT_ROOT")); source("plan/setup.R"); '
         'cat(COL_SINGLE, COL_DOUBLE, DPI, BASE_SIZE, JOURNAL$preset, "\\n")'],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=dict(os.environ, PROJECT_ROOT=root))
    out = (run.stdout or "").strip().splitlines()
    line = out[-1] if out else ""
    check("theme_journal.R prefers the sourced geometry",
          line.startswith("3.35 6.9 600"), True, line or run.stderr[-300:])
    check("...and leaves an unsourced field on the preset",
          " 9 " in line, True,
          "base_size was blank in the file, so the generic preset's 9 stands")




# ---------------------------------------------------------------------------
# The outline, including the case where there is not one yet
# ---------------------------------------------------------------------------

def _scaffold(tmp: str, name: str) -> str:
    root = os.path.join(tmp, name)
    run = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "scaffold.py"),
         "scaffold", root, "--title", "Array symmetry",
         "--field", "biochemistry"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if run.returncode != 0:
        return ""
    write(root, "drafts/source_text/title_abstract.md", TITLE_MD)
    return root


def _bundle(tmp: str, name: str, data: dict) -> str:
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return path


GOOD_BUNDLE = {
    "sections": [
        {"section": "Introduction", "lines": [
            {"claim": "Cryo-ET has resolved arrays in mesophiles but not "
                      "above 60 C.", "evidence": "@jensen2019arrays"},
            {"claim": "Nobody has measured whether thermophilic arrays are "
                      "more ordered.", "evidence": "background"}]},
        {"section": "Results", "lines": [
            {"claim": "Arrays keep hexagonal packing at every growth "
                      "temperature.", "evidence": "Fig 1"},
            {"claim": "Thermophilic arrays are more ordered than mesophilic "
                      "ones.",
             "evidence": "Fig 2; stats: symmetry_index ~ species"}]},
        {"section": "Discussion", "lines": [
            {"claim": "Order tracks growth temperature rather than "
                      "phylogeny.", "evidence": ""}]}],
    "notes": ["Ask GJ whether the 60 C cut-off is the right one.",
              "Figure 3 is planned but not made."],
}


def test_outline(tmp: str) -> None:
    section("An outline that does not exist yet (and one that needs changing)")

    root = _scaffold(tmp, "outline_case")
    if not root:
        skip("every outline check", "scaffold failed")
        return

    # --- the three states -------------------------------------------------
    res = ms.outline_cmd(root)
    check("a scaffolded project has an outline file with no lines in it",
          res["state"], "empty")
    os.remove(os.path.join(root, "plan", "outline.md"))
    check("...and no file at all is a different state, not an error",
          ms.outline_cmd(root)["state"], "missing")

    # --- the source inventory --------------------------------------------
    src = ms.outline_cmd(root)["sources"]
    check("nothing to build an outline from on a bare scaffold",
          src["can_draft"], False,
          "a proposal built out of nothing is a fabricated paper")
    check("...and the README's own Status line does not count as material",
          any("README" in n for n in src["lack"]), True)

    write(root, "plan/captions.md",
          "## Figure 1 - fig01.png\n**Arrays keep hexagonal packing.**\nn = 12.\n\n"
          "## Figure 2 - fig02.png\n**Thermophilic arrays are more ordered.**\n"
          "Symmetry index against temperature.\n")
    write(root, "drafts/references.bib", BIB)
    write(root, "data/analysis/analysis_stats_output.md",
          "## symmetry_index ~ species\n\nt = 4.1, p = 0.003\n")
    src = ms.outline_cmd(root)["sources"]
    check("captions, stats and a bibliography are enough to propose from",
          src["can_draft"], True)
    check("...and the inventory names the floats it would build lines out of",
          any("Figure 2" in s["detail"] for s in src["sources"]), True)

    # --- writing one ------------------------------------------------------
    good = _bundle(tmp, "good.json", GOOD_BUNDLE)
    dry = ms.outline_cmd(root, good, dry_run=True)
    check("a dry run validates without writing", dry["errors"], [])
    check("...and writes nothing",
          os.path.isfile(os.path.join(root, "plan", "outline.md")), False,
          "the dry run is what the user is shown before being asked")
    check("...while still reporting exactly what would change",
          dry["diff"]["counts"], {"added": 5, "removed": 0, "changed": 0,
                                  "kept": 0})

    res = ms.outline_cmd(root, good)
    check("writing it succeeds", res["errors"], [])
    check("...and the file parses back to the lines that went in",
          res["counts"]["outline_lines"], 5)
    check("...with the notes block kept out of the claims",
          res["counts"]["notes_lines"], 2,
          "notes parsed as claims become paragraphs the paper never has")

    body = read(os.path.join(root, "plan", "outline.md"))
    check("the notes go under their own heading", "## Notes" in body, True)

    # The regression pin for the line-number bug: outline.md opens with an
    # instruction comment the user is told they may leave in place, and every
    # location the pipeline reports is counted after that comment is stripped.
    lines = body.splitlines()
    want = next(i for i, line in enumerate(lines, 1)
                if line.startswith("- Order tracks"))
    empty = [f for f in res["findings"] if f["rule"] == "empty_bracket"]
    check("an empty bracket is reported at its real line in the file",
          empty[0]["location"] if empty else "none", f"outline.md:{want}",
          "the instruction comment must not shift the count")

    # --- what it refuses --------------------------------------------------
    def refuse(name: str, bundle: dict, fragment: str,
               replace: bool = True) -> None:
        r = ms.outline_cmd(root, _bundle(tmp, "refuse.json", bundle),
                           replace=replace)
        check(name, any(fragment in e for e in r["errors"]), True,
              f"errors were {r['errors']}")

    # Length and one-sentence-per-line are STYLE, and style is the user's
    # call: they are reported and then written, not refused. Everything below
    # them is grammar - a line the reader cannot parse - and stays a refusal.
    def report(name: str, bundle: dict, fragment: str) -> None:
        r = ms.outline_cmd(root, _bundle(tmp, "warn.json", bundle),
                           replace=True, dry_run=True)
        check(name, (r["errors"], any(fragment in w for w in r["warnings"])),
              ([], True), f"warnings were {r['warnings']}")

    report("an outline line longer than the cap is reported, not refused",
           {"sections": [{"section": "Results", "lines": [{
               "claim": "In this paragraph we will show that arrays isolated "
                        "from the thermophile are markedly more symmetric "
                        "than those from the mesophile, which is the "
                        "difference between resolving and not",
               "evidence": "Fig 1"}]}]},
           "reads best as the paragraph's idea")
    report("two sentences on one line is reported, not refused",
           {"sections": [{"section": "Results", "lines": [{
               "claim": "Arrays are ordered. Order tracks temperature.",
               "evidence": "Fig 1"}]}]},
           "more than one sentence")
    refuse("a heading that is not a section of the paper is refused",
           {"sections": [{"section": "Preamble", "lines": [{
               "claim": "Arrays are ordered.", "evidence": "Fig 1"}]}]},
           "not a section of the paper")
    refuse("notes smuggled in as a section are refused",
           {"sections": [{"section": "Notes", "lines": [{
               "claim": "Ask GJ.", "evidence": ""}]}]},
           "Notes go in the bundle's `notes` list")
    refuse("the evidence written into the claim text is refused",
           {"sections": [{"section": "Results", "lines": [{
               "claim": "Arrays are ordered [Fig 1]", "evidence": "Fig 1"}]}]},
           "ends in a bracket")
    refuse("writing over an outline that has lines needs --replace",
           GOOD_BUNDLE, "needs --replace", replace=False)

    check("...and a refused write leaves the outline exactly as it was",
          read(os.path.join(root, "plan", "outline.md")), body)

    # --- revising one -----------------------------------------------------
    revised = {"sections": [
        {"section": "Introduction",
         "lines": GOOD_BUNDLE["sections"][0]["lines"]},
        {"section": "Results", "lines": [
            {"claim": "Arrays keep hexagonal packing at every growth "
                      "temperature.", "evidence": "Fig 1"},
            {"claim": "Order is lost when the adaptor is deleted.",
             "evidence": "Fig 2"}]}],
        "notes": []}
    r = ms.outline_cmd(root, _bundle(tmp, "rev.json", revised),
                       replace=True, dry_run=True)
    d = r["diff"]
    check("a revision reports what would be added",
          [x["claim"] for x in d["added"]],
          ["Order is lost when the adaptor is deleted."])
    check("...what would be dropped",
          sorted(x["claim"] for x in d["removed"]),
          ["Order tracks growth temperature rather than phylogeny.",
           "Thermophilic arrays are more ordered than mesophilic ones."])
    check("the unchanged lines are counted, not re-reported",
          d["counts"]["kept"], 3)

    r = ms.outline_cmd(root, _bundle(tmp, "rev.json", revised), replace=True)
    check("applying the revision succeeds", r["errors"], [])
    check("...and the outline it replaced is kept, not destroyed",
          os.path.isfile(r["backup"]), True)
    check("...with the dropped claim still readable in the copy",
          "phylogeny" in read(r["backup"]), True)

    # --- an evidence change is a change, not a drop and an add ------------
    moved = {"sections": [{"section": "Results", "lines": [
        {"claim": "Arrays keep hexagonal packing at every growth "
                  "temperature.", "evidence": "Fig 1; stats: symmetry"}]}]}
    d = ms.outline_cmd(root, _bundle(tmp, "moved.json", moved),
                       replace=True, dry_run=True)["diff"]
    check("re-evidencing a line reports it as changed",
          [x["evidence"] for x in d["changed"]],
          ["[Fig 1] -> [Fig 1; stats: symmetry]"])

    # --- item 37: the diff again, in the terms the change is actually in --
    #
    # 2 added lines and 6 filled brackets was shown to the user as a raw
    # added/removed/changed/unchanged diff and the reply was 'I dont
    # understand what this means'. The diff was accurate. Adding a line adds
    # a CLAIM the paper will now make; filling a bracket only attaches
    # evidence to a claim the author already wrote, and the engine's
    # vocabulary collapses both.
    base = {"sections": [{"section": "Results", "lines": [
        {"claim": "Arrays keep hexagonal packing at every growth "
                  "temperature.", "evidence": ""},
        {"claim": "Order tracks growth temperature rather than species.",
         "evidence": "Fig 3"}]}]}
    ms.outline_cmd(root, _bundle(tmp, "base37.json", base), replace=True)

    both = {"sections": [{"section": "Results", "lines": [
        # an empty bracket filled - no new claim
        {"claim": "Arrays keep hexagonal packing at every growth "
                  "temperature.", "evidence": "Fig 2b"},
        # untouched
        {"claim": "Order tracks growth temperature rather than species.",
         "evidence": "Fig 3"},
        # a claim the paper did not previously plan to make
        {"claim": "Lattice spacing is unchanged between species.",
         "evidence": "Fig 2c",
         "why": "the order result is unreadable without a packing control",
         "source": "plan/captions.md, Fig 2c"}]}]}
    cl = ms.outline_cmd(root, _bundle(tmp, "both37.json", both),
                        replace=True, dry_run=True)["diff"]["classified"]
    check("a filled bracket and a new line are not the same kind of change",
          (cl["counts"].get("new_claims"),
           cl["counts"].get("evidence_attached")), (1, 1),
          "shown as 'added' and 'changed' they read as one event")
    check("...and the new claim is the one that decides something",
          (cl["decides_the_paper"], cl["bookkeeping"]), (1, 1))
    check("...with the reason it is proposed and where it came from",
          (cl["new_claims"][0]["why"][:22],
           cl["new_claims"][0]["source"]),
          ("the order result is un", "plan/captions.md, Fig 2c"))
    check("a new claim arriving with no reason is named, not passed on",
          ms.classify_outline_diff(
              {"added": [{"section": "Results", "claim": "A new thing.",
                          "evidence": "Fig 9"}],
               "removed": [], "changed": [], "kept": []}
          )["new_claims_without_reason"], ["A new thing."],
          "a new claim with nothing beside it is not a question the user "
          "can answer")
    check("the one-line summary leads with the class that changes the paper",
          cl["line"].startswith("1 new claim"), True, cl["line"])

    # A reworded claim arrives as one removed line and one added line,
    # because identity in the diff is the claim text. 'you are adding a
    # claim' and 'you are rephrasing the claim you wrote' are the two
    # answers furthest apart in the whole report.
    reword = {"sections": [{"section": "Results", "lines": [
        {"claim": "Arrays keep hexagonal packing at every growth "
                  "temperature.", "evidence": ""},
        {"claim": "Order tracks growth temperature and not species.",
         "evidence": "Fig 3"}]}]}
    cl = ms.outline_cmd(root, _bundle(tmp, "rw37.json", reword),
                        replace=True, dry_run=True)["diff"]["classified"]
    check("a rephrased claim is a rewording, not a new claim and a deletion",
          (cl["counts"].get("new_claims", 0),
           cl["counts"].get("claim_removed", 0),
           cl["counts"].get("claim_reworded", 0)), (0, 0, 1))
    check("...and the user is shown what it used to say",
          cl["claim_reworded"][0]["was"],
          "Order tracks growth temperature rather than species.")
    check("a claim sharing only a subject is still a new claim",
          ms.classify_outline_diff(
              {"added": [{"section": "Results",
                          "claim": "Order collapses above 80 C."}],
               "removed": [{"section": "Results",
                            "claim": "Order tracks growth temperature "
                                     "rather than species."}],
               "changed": [], "kept": []})["counts"].get("new_claims"), 1,
          "calling it a reword would hide the decision")

    # Put the outline back the way this test found it - the checks below read
    # the project's state, and item 37's fixtures were only ever scratch.
    ms.outline_cmd(root, _bundle(tmp, "rev.json", revised), replace=True)

    # --- status carries it ------------------------------------------------
    st = ms.status(root)
    check("status reports the outline state without being asked",
          st["outline"]["state"], "present")
    check("...and how many lines have nothing behind them yet",
          st["outline"]["empty_brackets"], 0)
    check("...and an outline the user wrote is not flagged as proposed",
          st["outline"]["proposed"], False)


def test_outline_absent(tmp: str) -> None:
    section("An outline that is somewhere else, and one nobody has agreed to")

    root = _scaffold(tmp, "outline_absent")
    if not root:
        skip("every absent-outline check", "scaffold failed")
        return
    write(root, "plan/captions.md",
          "## Figure 1 - fig01.png\n**Arrays keep hexagonal packing.**\nn = 12.\n")
    write(root, "drafts/references.bib", BIB)

    # --- the user points at an outline they keep somewhere else -----------
    # "No plan/outline.md" and "the outline is in my notes" look identical on
    # disk. Only one of them wants a proposal, and the engine has to be able
    # to take the other answer.
    theirs = os.path.join(tmp, "from_the_whiteboard.md")
    write(tmp, "from_the_whiteboard.md",
          "Some preamble that is not an outline line.\n\n"
          "## Introduction\n"
          "- Arrays have not been imaged above 60 C. [background]\n\n"
          "## Results\n"
          "- Arrays keep hexagonal packing at every temperature. [Fig 1]\n\n"
          "## Notes\n"
          "- Off the whiteboard on Tuesday.\n")

    dry = ms.outline_cmd(root, source=theirs, dry_run=True)
    check("an outline the user points at is read before anything is written",
          dry["adopting"]["lines"], 2)
    check("...by the same grammar, so its sections resolve",
          dry["adopting"]["sections"], ["introduction", "results"])
    check("...and nothing is written on the dry run",
          ms.outline_cmd(root)["state"], "empty")

    res = ms.outline_cmd(root, source=theirs)
    check("adopting it writes the lines into plan/outline.md",
          res["counts"]["outline_lines"], 2)
    check("...with the notes block carried across, not re-bulleted",
          "- Off the whiteboard on Tuesday." in
          read(os.path.join(root, "plan", "outline.md")), True)
    check("...and an outline the user already owned is never marked proposed",
          res["proposed"], False,
          "the banner says Claude guessed these lines; this one it did not")

    # --- a file that is not an outline is refused, and the refusal helps ---
    notes = os.path.join(tmp, "meeting.md")
    write(tmp, "meeting.md", "We talked about the 60 C cut-off for an hour.\n")
    r = ms.outline_cmd(root, source=notes, replace=True)
    check("a file with no outline lines in it is refused",
          bool(r["errors"]), True)
    check("...and the refusal points at proposing FROM it instead",
          "build one FROM" in r["errors"][0], True,
          "a meeting note is often the right material, not a dead end")
    check("...leaving the outline that was already there alone",
          ms.outline_cmd(root)["counts"]["outline_lines"], 2)
    check("a path that does not exist is its own error",
          "no such file" in ms.outline_cmd(
              root, source=os.path.join(tmp, "nope.md"))["errors"][0], True)

    # --- an outline Claude wrote says so on its face ----------------------
    prop = _bundle(tmp, "prop.json", GOOD_BUNDLE)
    res = ms.outline_cmd(root, prop, replace=True, proposed=True)
    check("an outline the skill proposed is written with its banner",
          res["proposed"], True)
    body = read(os.path.join(root, "plan", "outline.md"))
    check("...saying in the file itself that nobody has agreed to it",
          "PROPOSED OUTLINE" in body, True,
          "a guessed ledger reads exactly like an authored one")
    check("...and the banner is invisible to the parser",
          res["counts"]["outline_lines"], 5,
          "a blockquote is neither a section heading nor a claim line")
    check("status carries the proposal without being asked",
          ms.status(root)["outline"]["proposed"], True)

    comp = ms.completeness(root)
    prop_items = [i for i in comp["missing"]
                  if i["area"] == "outline" and "PROPOSAL" in i["detail"]]
    check("completeness reports a proposed ledger", len(prop_items), 1)
    check("...as a gap, never as a block",
          prop_items[0]["severity"] if prop_items else "", "gap",
          "the proposal is a usable ledger; what it is not is agreed to")

    # --- accepting is the user's act, and it edits nothing else -----------
    edited = body.replace("Order tracks growth temperature rather than "
                          "phylogeny.", "Order tracks growth temperature.")
    write(root, "plan/outline.md", edited)
    acc = ms.outline_cmd(root, accept=True)
    check("accepting drops the banner", acc["proposed"], False)
    after = read(os.path.join(root, "plan", "outline.md"))
    check("...and nothing else in the file", "PROPOSED OUTLINE" in after, False)
    check("...including a line the user corrected in the meantime",
          "Order tracks growth temperature. [" in after, True,
          "accept must not rewrite the outline back to what was proposed")
    check("...leaving every claim line where it was",
          ms.outline_cmd(root)["counts"]["outline_lines"], 5)
    check("accepting an outline nobody proposed is an error, not a no-op",
          "nothing to accept" in ms.outline_cmd(root, accept=True)["errors"][0],
          True)


def test_section_order(tmp: str) -> None:
    section("assemble refuses to build a manuscript with no manuscript in it")

    root = _scaffold(tmp, "order_case")
    if not root:
        skip("every section_order check", "scaffold failed")
        return
    write(root, "drafts/source_text/results.md", "# Results\n\nOrdered.\n")
    ms.init(root, "IJROBP")
    req = os.path.join(root, "drafts", "IJROBP", "journal_requirements",
                       "requirements.yml")
    original = read(req)
    scaffolded = ("section_order: [title_abstract, introduction, methods, "
                  "results, discussion]")

    def with_order(value: str) -> dict:
        write(root, os.path.relpath(req, root),
              original.replace(scaffolded, f"section_order: {value}"))
        return ms.assemble(root, "IJROBP", dry_run=True)

    res = with_order("unknown")
    # `sections` is the DOCUMENT's headings in the document's order, not the
    # order file's contents (item 9), so the references section the build
    # appends when nothing names one is in the list - it is in the .docx.
    # Named as the DOCUMENT names them: `introduction.md` writes
    # `# Introduction`, so that is the heading in the .docx and that is what
    # the summary says. The STEMS are what resolved; the HEADINGS are what a
    # reader sees, and reporting the stem is how the summary disagreed with
    # the document on every section of a review (item 9).
    SHOWN = [s if s == "title_abstract" else s.title()
             for s in ms.SECTION_FILES]
    check("an unsourced order falls back to the scaffold's own",
          res["sections"][:len(ms.SECTION_FILES)], SHOWN,
          "read as a list, `unknown` is one section name that matches no file")
    check("...with the references section the build adds behind it",
          res["sections"][len(ms.SECTION_FILES):], ["References"],
          "the scaffold's order names no references section, so assemble "
          "appends one - and never used to report it")
    check("...and says so rather than doing it quietly",
          any("still unknown" in w for w in res["warnings"]), True)
    check("`[unknown]` is the same case",
          with_order("[unknown]")["sections"][:len(ms.SECTION_FILES)],
          SHOWN)

    res = with_order("[abstract, intro, body]")
    check("an order naming no file that exists is refused",
          any("no section of the manuscript resolved" in e
              for e in res["errors"]), True,
          "otherwise pandoc builds a valid .docx with the captions and the "
          "incomplete block and no manuscript at all")
    check("...and nothing is written", res["output"], None)

    write(root, os.path.relpath(req, root), original)


# ---------------------------------------------------------------------------
# The run ledger
# ---------------------------------------------------------------------------

def test_run_ledger(tmp: str) -> None:
    section("A run that stops half way can be picked up (the run ledger)")

    root = _scaffold(tmp, "run_case")
    if not root:
        skip("every run-ledger check", "scaffold failed")
        return
    ms.init(root, "IJROBP")
    J = "IJROBP"

    res = ms.run_ledger(root, J)
    check("with no run recorded, the report says how to open one",
          "run --start" in res["resume_line"], True)
    check("...and reports no open run", res["open"], False)

    res = ms.run_ledger(root, J, start=True, preset="coauthor")
    # The RESOLVED list, not the raw preset: the plan takes out whatever
    # this paper kind has no use for, and the ledger has to record the work
    # that will actually happen or it records work the plan said would not
    # (review-paper 8).
    check("--start resolves the preset into the module list",
          [m["module"] for m in res["run"]["modules"]],
          ms.plan(root, J, "coauthor")["modules"])
    check("...which on a research paper is now the whole preset",
          [m for m in ms.PRESETS["coauthor"]
           if m not in [x["module"] for x in res["run"]["modules"]]],
          [],
          "attribution-check was taken out here until 2026-09-20; it runs "
          "on every paper kind now (citation-integrity 6.2)")
    check("...all of them pending",
          {m["state"] for m in res["run"]["modules"]}, {"pending"})

    res = ms.run_ledger(root, J, start=True)
    check("a second run cannot be opened over an open one",
          any("already open" in e for e in res["errors"]), True,
          "that would lose the record of where the first one got to")

    # The three steps that now run before drafting (items 21 and 23, and
    # prose 5's learn-from-edits). Recorded done so the rest of this test
    # measures draft-sections, which is what it is about.
    for before in ("outline", "literature-landscape", "learn-from-edits"):
        ms.run_ledger(root, J, step=before, state="done")

    results = write(root, "drafts/source_text/results.md",
                    "# Results\n\nOrdered.\n")
    ms.run_ledger(root, J, step="draft-sections", item="introduction",
                  state="done")
    res = ms.run_ledger(root, J, step="draft-sections", item="results",
                        state="done",
                        wrote=[ms.stpfx(root) + "results.md"])
    mod = ms._module_entry(res["run"], "draft-sections")
    check("a module with some items done is running, not done",
          mod["state"], "running")
    check("...and the items it finished are named",
          [i["item"] for i in mod["items"]], ["introduction", "results"])
    check("the resume line says where to pick up",
          "Pick up at draft-sections" in res["resume_line"], True)
    check("...and which items are already behind it",
          "introduction, results" in res["resume_line"], True)

    # The ledger is checked against the folder, never believed over it.
    check("a step's outputs verify clean while the file is untouched",
          res["stale_outputs"], [])
    write(root, "drafts/source_text/results.md", "# Results\n\nEdited.\n")
    res = ms.run_ledger(root, J)
    check("a recorded output that has changed since is reported",
          [(s["path"], s["verdict"]) for s in res["stale_outputs"]],
          [(ms.stpfx(root) + "results.md", "changed")])
    os.remove(results)
    res = ms.run_ledger(root, J)
    check("...and one that has gone is reported as missing",
          [s["verdict"] for s in res["stale_outputs"]], ["missing"],
          "a ledger believed over the folder is worse than no ledger")

    res = ms.run_ledger(root, J, finish=True)
    check("a run cannot be finished while a module never ran",
          any("never reached a terminal state" in e for e in res["errors"]),
          True)
    check("...and the modules that did not run are named",
          "assemble" in res["errors"][0], True)

    ms.run_ledger(root, J, step="draft-sections", state="done")
    for m in ("revise-prose", "comprehension-check", "quality-check",
              "evidence-check",
              "stats-check", "attribution-check", "assemble",
              "citation-check", "reviewer-check"):
        ms.run_ledger(root, J, step=m, state="skipped", note="not this round")
    res = ms.run_ledger(root, J, finish=True)
    check("with every module terminal, the run closes",
          res["run"]["state"], "finished")

    log = read(ms._run_paths(os.path.join(root, "drafts", "IJROBP"))[1])
    check("every step is in the append-only log",
          log.count("draft-sections"), 4,
          "the state file is rewritten; this file is the trail that is not")
    check("...including the close", "finished" in log, True)

    check("a new run can then be opened",
          ms.run_ledger(root, J, start=True)["errors"], [])
    st = ms.status(root, "IJROBP")
    check("status reports an open run without being asked",
          st["run"]["open"], True)
    check("...and where it stopped", st["run"]["next"], "outline",
          "a fresh run picks up at the first module of the preset, which is "
          "the outline step now")

    res = ms.run_ledger(root, J, abandon=True, note="usage ran out")
    check("a run can be abandoned rather than finished",
          res["run"]["state"], "abandoned")
    check("...and the reason is recorded", res["run"]["note"], "usage ran out")

    res = ms.run_ledger(root, J, step="draft-sections", state="done")
    check("a step outside a run is refused, not silently recorded",
          any("no open run" in e for e in res["errors"]), True)


PRUNE_BIB = """\
@article{cited2019,
  title = {The one the results cite},
  author = {Jensen, Grant J.},
  year = {2019}
}

@article{orphan2020,
  title = {An entry nothing anywhere cites},
  author = {Orphan, Victoria},
  year = {2020}
}

@article{noted2021,
  title = {Cited only from a planning note},
  author = {Note, A.},
  year = {2021}
}

@article{braced2022,
  title = {Arrays in {E. coli} and {\\emph{Salmonella}}},
  author = {Brace, B.},
  year = {2022}
}

@article{kept2023,
  title = {The last entry in the file},
  author = {Last, L.},
  year = {2023}
}
"""


def test_bib(tmp: str) -> None:
    section("An entry nobody cites does not belong in the bibliography")

    root = _scaffold(tmp, "bib_case")
    if not root:
        skip("every bibliography check", "scaffold failed")
        return
    write(root, "drafts/references.bib", PRUNE_BIB)
    write(root, "drafts/source_text/results.md",
          "# Results\n\nArrays keep their packing [@cited2019].\n")
    write(root, "plan/relevant_literature/relevant_literature_summary.md",
          "The method comes from [@noted2021]; read it before drafting.\n")

    res = ms.bib_cmd(root)
    check("every entry no section cites is reported",
          res["uncited"], ["orphan2020", "noted2021", "braced2022", "kept2023"])
    check("...but only the ones nothing in the project cites can be removed",
          res["removable"], ["orphan2020", "braced2022", "kept2023"],
          "a citekey in a planning note is still a citation, and deleting the "
          "entry behind it is data loss")
    check("...and the one that is kept says where it is cited from",
          res["cited_elsewhere"].get("noted2021"),
          ["plan/relevant_literature/relevant_literature_summary.md"])
    check("reporting writes nothing", res["written"], None)

    dry = ms.bib_cmd(root, prune=True, dry_run=True)
    check("a dry run names what would go", dry["removable"],
          ["orphan2020", "braced2022", "kept2023"])
    check("...and still writes nothing", dry["written"], None,
          "this is what the user is shown before being asked")
    check("...leaving the file untouched",
          len(ms.parse_bib(read(os.path.join(root, "drafts",
                                             "references.bib")))), 5)

    done = ms.bib_cmd(root, prune=True)
    after = ms.parse_bib(read(os.path.join(root, "drafts", "references.bib")))
    check("pruning removes exactly those entries", done["removed"],
          ["orphan2020", "braced2022", "kept2023"])
    check("...and leaves the rest", [e["key"] for e in after],
          ["cited2019", "noted2021"])
    check("...with their fields intact",
          after[1]["fields"]["title"], "Cited only from a planning note",
          "a brace-counting cut, not a regex - the entry above it has a "
          "nested {} in its title")
    check("the bibliography it replaced is kept, not destroyed",
          os.path.isfile(done["backup"]), True)
    check("...with every dropped entry still readable in the copy",
          all(k in read(done["backup"])
              for k in ("orphan2020", "braced2022", "kept2023")), True)
    check("...and the EndNote export is regenerated to match",
          read(done.get("ris") or "").count("TY  - "), 2)

    check("a second prune has nothing left to do",
          ms.bib_cmd(root, prune=True)["removed"], [])


def test_journal_budget(tmp: str) -> None:
    section("Callout form, float order and length come from the journal")

    root = _scaffold(tmp, "budget_case")
    if not root:
        skip("every journal-budget check", "scaffold failed")
        return
    ms.init(root, "Cell")
    write(root, "plan/captions.md",
          "## Figure 1 — fig01.png\n**Arrays keep hexagonal packing.**\n"
          "(A) Central sections. (B) Radial profiles.\n\n"
          "## Figure 2 — fig02.png\n**Order tracks temperature.**\n"
          "Symmetry index by species.\n")
    write(root, "drafts/source_text/results.md",
          "# Results\n\nOrder tracked temperature (Figure 2).\n\n"
          "Arrays kept their packing (Figure 1A).\n")

    req = os.path.join(root, "drafts", "Cell", "journal_requirements",
                       "requirements.yml")
    body = read(req)
    check("the callout style is a sourced requirement like any other",
          "citation_style: unknown" in body, True,
          "it varies by journal and is exactly the sort of formulaic value a "
          "plausible guess would get wrong")
    write(root, "drafts/Cell/journal_requirements/requirements.yml",
          body.replace("word_limit_total: unknown", "word_limit_total: 5")
              .replace("citation_style: unknown", "citation_style: "
                       "parenthetical"))

    done = ms.completeness(root, "Cell")
    details = [m["detail"] for m in done["missing"]]
    check("a float mentioned out of turn reaches the outstanding list",
          any("first mentioned" in d and "Figure 2" in d for d in details),
          True)
    check("...as a must-fix, because a renumber is not optional",
          any(m["severity"] == "blocking" and "first mentioned" in m["detail"]
              for m in done["missing"]), True)
    check("a panel the legend defines and nothing cites is on it too",
          any("panel B" in d for d in details), True)
    check("running past the journal's word limit is on it as a question",
          any("word limit" in d and "ask the user" in d for d in details),
          True, "\n".join(details))

    ms.configure(root, "Cell", sets=["length_policy=over"])
    details = [m["detail"] for m in ms.completeness(root, "Cell")["missing"]]
    check("...and once the user says carry it, it says that instead",
          any("chose to carry the overage" in d for d in details), True)
    check("...while still being outstanding",
          any("word limit" in d for d in details), True,
          "an accepted overage is a thing to resolve, not a thing to forget")
    check("a dial only accepts its documented values",
          any("not one of" in e for e in ms.configure(
              root, "Cell", sets=["length_policy=whatever"])["errors"]), True)


def test_intensity(tmp: str) -> None:
    section("How heavy a run is: presets, and a standing preference that lasts")

    root = _scaffold(tmp, "intensity_case")
    if not root:
        skip("every intensity check", "scaffold failed")
        return
    ms.init(root, "Cell")
    J = "Cell"

    # Each of these is one higher than it was before 20.8, and the reason is
    # that the estimate had been wrong in the same direction the whole time:
    # `abstract` (module 1b) is draft-sections' last step rather than an
    # entry in `mods`, so `plan` listed it under `isolated_modules` as a
    # thing that would run and then did not count the agent call it costs.
    # 2.3's rule is that a plan which lists a check tells the user it ran and
    # the estimate has to be true.
    calls = {p: ms.plan(root, J, p)["agent_calls"] for p in ms.PRESETS}
    check("the presets really are different amounts of work",
          calls, {"draft": 9, "coauthor": 13, "submission": 16, "revision": 11,
                  "sections": 9},
          "if they cost the same, choosing between them is theatre. Each "
          "went up by one when quality-check joined every drafting preset, "
          "and the three checking presets went up by one again on "
          "2026-09-20 when attribution-check joined every paper kind")
    check("...and every preset that drafts pays for the abstract agent",
          sorted(p for p in ms.PRESETS
                 if "abstract" in ms.plan(root, J, p)["isolated_modules"]),
          sorted(p for p in ms.PRESETS
                 if "draft-sections" in ms.PRESETS[p]))
    # `sections` and `draft` are close in agent calls and far apart in what
    # they produce, and that is the difference the user's sentence "only do
    # the introduction and methods" is about (item 59). Agent calls do not
    # measure it - assemble is not an agent call - so what separates them is
    # pinned directly.
    check("`sections` is the preset that writes prose and builds nothing",
          [p for p in ms.PRESETS if "assemble" not in ms.PRESETS[p]],
          ["sections"],
          "a request for prose is not a request for a .docx, and until this "
          "existed that sentence had no spelling")

    # --- the standing preference -----------------------------------------
    res = ms.configure(root, J, skip=["citation-check"])
    check("a module can be skipped from now on, not just this round",
          res["config"]["lists"]["skip_modules"], ["citation-check"])
    check("...and it is written to the file, not held in memory",
          "citation-check" in read(os.path.join(root, "drafts", J,
                                                "writing_config.yml")), True)

    res = ms.configure(root, J, skip=["citation-shmeck"])
    check("a module name that does not exist is refused, not recorded",
          (any("is not a module" in e for e in res["errors"]),
           res["config"]["lists"]["skip_modules"]),
          (True, ["citation-check"]),
          "a typo written into the config is a preference that silently "
          "never applies, which is worse than being told no")

    res = ms.plan(root, J, "coauthor")
    check("the standing skip survives into the next round's plan",
          ("citation-check" in res["modules"], res["agent_calls"]),
          (False, 12),
          "the whole point is not having to retype -citation-check, and not "
          "having it run the round you forget")
    check("...and the plan says the config did it, not the preset",
          (res["standing_skips"],
           "standing config" in res["line"]), (["citation-check"], True))

    res = ms.plan(root, J, "draft")
    check("the standing skip reaches draft too, now that draft has it",
          (res["standing_skips"], "citation-check" in res["modules"]),
          (["citation-check"], False),
          "citation-check is no longer offered per round, so --skip is the "
          "only way to say no to it - and it has to work everywhere it runs "
          "(item 36)")

    res = ms.plan(root, J, "coauthor", add=["citation-check"])
    check("asking for it this round beats the standing skip",
          ("citation-check" in res["modules"],
           any("asked for it this round" in w for w in res["warnings"])),
          (True, True),
          "the config is what you want most rounds, not a lock")

    res = ms.plan(root, J, "submission")
    check("a standing skip cannot remove the reconciliation pass either",
          ("citation-check" in res["modules"],
           any("non-skippable" in w for w in res["warnings"])), (True, True))

    res = ms.run_ledger(root, J, start=True, preset="coauthor")
    check("a run opened from a preset carries the standing skip too",
          [m["module"] for m in res["run"]["modules"]],
          ["outline", "literature-landscape", "learn-from-edits",
           "draft-sections", "revise-prose", "comprehension-check",
           "quality-check",
           "evidence-check", "stats-check", "attribution-check", "assemble",
           "reviewer-check"],
          "otherwise the ledger records work the plan said would not happen")
    ms.run_ledger(root, J, abandon=True)

    res = ms.configure(root, J, forget=["citation-check"])
    check("and it can be taken back",
          (res["config"]["lists"]["skip_modules"],
           ms.plan(root, J, "coauthor")["agent_calls"]), ([], 13))

    # --- the dial that used to be ignored --------------------------------
    ms.configure(root, J, sets=["reviewer_check=off"])
    res = ms.plan(root, J, "submission")
    check("reviewer_check: off takes the module out of the plan",
          ("reviewer-check" in res["modules"], res["agent_calls"]), (False, 15),
          "the dial governs how the module reads the paper; leaving it in "
          "spends a call to do nothing and prints that a check ran")
    check("...and says the config did it", res["standing_skips"],
          ["reviewer-check"])

    res = ms.plan(root, J, "submission", add=["reviewer-check"])
    check("...unless this round you asked for it",
          "reviewer-check" in res["modules"], True)

    ms.configure(root, J, sets=["reviewer_check=light"])
    check("light puts it back", "reviewer-check" in
          ms.plan(root, J, "submission")["modules"], True)

    # prose_polish: off is the same shape - a dial that governs how a module
    # reads the paper, so off has to mean the module does not run.
    ms.configure(root, J, sets=["prose_polish=off"])
    res = ms.plan(root, J, "submission")
    check("prose_polish: off takes module 1c out of the plan",
          ("revise-prose" in res["modules"],
           "revise-prose" in res["standing_skips"]), (False, True))
    check("...unless this round you asked for it",
          "revise-prose" in
          ms.plan(root, J, "submission", add=["revise-prose"])["modules"],
          True)
    ms.configure(root, J, sets=["prose_polish=standard"])
    check("standard puts it back", "revise-prose" in
          ms.plan(root, J, "submission")["modules"], True)
    res = ms.configure(root, J, sets=["prose_polish=vigorous"])
    check("a polish level nobody defined is refused, not written",
          any("not one of" in e for e in res["errors"]), True)


def test_journal_folder_shape(tmp: str) -> None:
    section("The journal folder holds what the journal asks for (spec 3)")

    root = os.path.join(tmp, "shape")
    ms_scaffold(root, journal="Langmuir")
    res = ms.init(root, "Langmuir")
    check("init succeeds", res["errors"], [])
    created = {a["path"] for a in res["actions"] if a["action"] == "create"}

    check("submission/ is created - spec 5.8 had nowhere to write to",
          "drafts/Langmuir/submission/" in created, True)
    check("...with a README, so it does not read as clutter",
          os.path.isfile(os.path.join(root, "drafts", "Langmuir",
                                      "submission", "README.md")), True)
    readme = read(os.path.join(root, "drafts", "Langmuir", "submission",
                               "README.md"))
    check("...that names the cover letter", "cover_letter" in readme, True)
    check("...and says the manuscript is in here with it",
          "manuscript_rN.docx" in readme and "live" in readme, True,
          "this reverses the old rule, on the user's instruction after "
          "seeing the folder: what is in submission/ is what is live "
          "(item 17)")

    # reports/ IS created, and this check exists because it was dropped once
    # on the strength of a grep that found no reader. The grep was truncated
    # before it reached skills/writing-engine/SKILL.md, which names
    # four modules writing into it and final-check reading it back. The lesson is the same
    # one the toc_graphic examples taught: a conclusion drawn from a partial
    # read is confident and wrong in exactly the same way as one drawn from a
    # complete one.
    check("reports/ IS created - four modules write into it (spec 5.2-5.7)",
          os.path.isdir(os.path.join(root, "drafts", "Langmuir", "reports")),
          True)
    reports_readme = os.path.join(root, "drafts", "Langmuir", "reports",
                                  "README.md")
    check("...with a README, so it is not mystery clutter",
          os.path.isfile(reports_readme), True)
    check("...saying final-check reads them back",
          "final-check" in read(reports_readme), True)
    check("...and that nothing in it is submitted",
          "nothing here is uploaded" in read(reports_readme), True)

    # The claim in the README has to match the skill that actually writes
    # there. A README naming a path no module uses is the drift this suite
    # exists to catch.
    skill = read(os.path.join(ROOT, "skills", "writing-engine", "SKILL.md"))
    check("the skill really does write into reports/rN/",
          "reports/rN/" in skill, True)

    # figures.placement is `unknown` on a fresh init, and unknown creates
    # nothing: the same rule as every other unsourced requirement (spec 4.3).
    check("figures/ is not created while placement is unknown",
          os.path.isdir(os.path.join(root, "drafts", "Langmuir", "submission", "figures")),
          False)

    reqp = os.path.join(root, "drafts", "Langmuir", "journal_requirements",
                        "requirements.yml")

    def set_placement(value: str) -> None:
        body = read(reqp)
        body = re.sub(r"^  placement: .*$", "  placement: " + value, body,
                      count=1, flags=re.M)
        with open(reqp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)

    set_placement("embedded")
    ms.init(root, "Langmuir")
    check("...nor when the journal embeds them (Langmuir's real answer)",
          os.path.isdir(os.path.join(root, "drafts", "Langmuir", "submission", "figures")),
          False)

    set_placement("separate")
    res = ms.init(root, "Langmuir")
    check("...and IS created once the journal asks for separate uploads",
          os.path.isdir(os.path.join(root, "drafts", "Langmuir", "submission", "figures")),
          True)
    detail = next((a["detail"] for a in res["actions"]
                   if a["path"].endswith("submission/figures/")), "")
    check("...naming the requirement it came from",
          "figures.placement" in detail, True)

    res = ms.init(root, "Langmuir")
    check("a folder already there is kept, never re-made",
          [a for a in res["actions"] if a["action"] == "create"], [])

    # assemble re-checks, because placement is sourced after init has run and
    # nobody should have to think to re-run init for the folder to appear.
    figdir = os.path.join(root, "drafts", "Langmuir", "submission", "figures")
    shutil.rmtree(figdir)
    res = ms.assemble(root, "Langmuir", dry_run=True)
    check("a dry-run assemble writes no folder", os.path.isdir(figdir), False)
    # The verb has to match what happened: "created" on a run that created
    # nothing is a report of work nobody did.
    check("...and says it WOULD create it, not that it did",
          any("would create" in w and "figures/" in w
              for w in res["warnings"]), True)

    if shutil.which("pandoc"):
        res = ms.assemble(root, "Langmuir")
        check("a real assemble creates it - placement is sourced after init",
              os.path.isdir(figdir), True)
        check("...and reports it in the past tense",
              any(w.startswith("created") and "figures/" in w
                  for w in res["warnings"]), True)
    else:
        skip("assemble creates figures/ on a real run", "pandoc not installed")
        skip("...and reports it in the past tense", "pandoc not installed")


REVIEW_PROTOCOL = """\
# Review Protocol

## The Question *

What limits the thermal stability of alkanethiol monolayers on gold.

## Inclusion Criteria *

- on a gold surface

## Exclusion Criteria *

- no-au: not on gold

## Sources

crossref

## Queries

alkanethiol monolayer thermal stability

## Corpus Floor

floor: 2
"""

REVIEW_README = """\
# The Review

## The question

What limits the thermal stability of alkanethiol monolayers on gold.

## The thesis

The reported ceiling is an artifact of heating rate.

## Scope

Gold only, 1983 onwards.

## The section plan

Formation, defects, desorption, outlook.
"""


def _review_project(tmp: str, name: str) -> str:
    """A review with a built corpus: records, tiers, synthesis, protocol."""
    root = os.path.join(tmp, name)
    write(root, "project.yml",
          'title: "A review"\npaper_kind: review\nreview_kind: narrative\n')
    write(root, "plan/README.md", REVIEW_README)
    write(root, "plan/outline.md", "# Structure\n\n# Notes\n")
    write(root, "plan/captions.md", "# Captions\n")
    write(root, "data/corpus/protocol.md", REVIEW_PROTOCOL)
    for key, tier in (("a2001x", "fulltext"), ("b2004y", "abstract"),
                      ("c2009z", "fulltext")):
        write(root, "data/corpus/papers/%s.md" % key,
              "# %s\nread_tier: %s\nverified: 2026-09-10 via crossref\n"
              "source_kind: primary\nfound_by: query\n"
              "\n## What it reports\n\nA number.\n" % (key, tier))
    write(root, "data/corpus/synthesis.md",
          "# Synthesis\n\n| key | tier |\n|---|---|\n")
    write(root, "drafts/source_text_r1/title_abstract.md", TITLE_MD)
    return root


def test_review_outline_and_brief(tmp: str) -> None:
    section("A review's own sections, its corpus, and the tier rule "
            "(items 71, 72, 73)")

    root = _review_project(tmp, "revkind")

    # --- item 71: the inventory reads the corpus, not analysis.md --------
    #
    # A review has no analysis.R and never will: data/corpus/ IS its data.
    # Holding it against the research-paper artifact list reported every
    # source empty on a corpus of 47 verified records, and `can_draft` false -
    # a refusal indistinguishable from the real case it exists to catch.
    inv = ms.outline_sources(root)
    names = [x["name"] for x in inv["sources"]]
    check("the corpus is a source of the outline on a review",
          ("data/corpus/" in names,
           "data/analysis/analysis.md" in names), (True, False))
    corpus = [x for x in inv["sources"] if x["name"] == "data/corpus/"][0]
    check("...present, with its record and tier counts",
          (corpus["present"], "3 records" in corpus["detail"],
           "fulltext 2" in corpus["detail"], "abstract 1" in corpus["detail"]),
          (True, True, True, True))
    check("...and the floor the author wrote is reported against",
          "floor of 2" in corpus["detail"], True)
    check("...so a review with a built corpus can draft",
          inv["can_draft"], True,
          "the one paper kind whose evidence base is most completely "
          "machine-checkable was the one this step refused")
    readme = [x for x in inv["sources"] if x["name"] == "plan/README.md"][0]
    check("a review's README is read for a review's headings",
          (readme["present"], "the question" in readme["detail"],
           "the thesis" in readme["detail"]), (True, True, True))

    # --- item 72: the author names the sections, and the engine accepts --
    bundle = _bundle(tmp, "review_bundle.json", {"sections": [
        {"section": "Introduction",
         "lines": [{"claim": "Why the ceiling matters", "evidence": ""}]},
        {"section": "Disease Pathology",
         "lines": [{"claim": "Loss of function drives the phenotype",
                    "evidence": ""}]},
        {"section": "Therapeutic Directions and Open Questions",
         "lines": [{"claim": "Two routes are in trials", "evidence": ""}]}]})
    dry = ms.outline_cmd(root, bundle, dry_run=True, proposed=True)
    check("a review's thematic section names are accepted, not refused",
          dry["errors"], [],
          "the scaffolded template tells the author to invent them")
    res = ms.outline_cmd(root, bundle, proposed=True)
    check("...and writing one works", bool(res.get("written")), True)
    check("...and the written file keeps the `# Structure` region the stem "
          "list is read out of",
          ms.section_files(root),
          ["title_abstract", "01_introduction", "02_disease_pathology",
           "03_therapeutic_directions_and_open_questions"],
          "the writer never emitted `# Structure`, so outline --write left a "
          "review whose section list came back as title_abstract alone")
    check("...and the notes block closes that region rather than becoming "
          "one more section of the paper",
          "\n# Notes\n" in read(os.path.join(root, "plan", "outline.md")),
          True)

    # A research paper is unmoved: its vocabulary is still fixed.
    fixed = ms.validate_outline(
        {"sections": [{"section": "Disease Pathology",
                       "lines": [{"claim": "x", "evidence": ""}]}]},
        {"sections": {"introduction": "introduction"},
         "notes_headings": ["notes"], "max_words": 25})
    check("a research paper still refuses a section it does not have",
          bool(fixed[0]), True)

    # ...and prose.py compares those lines against the drafted files.
    write(root, "drafts/source_text_r1/02_disease_pathology.md",
          "# Disease Pathology\n\nLoss of function drives the phenotype in "
          "every reported family.\n")
    ol = prose_outline(root)
    matched = [x for x in ol["sections"]
               if x["section"] == "02_disease_pathology"]
    check("an author-named section's lines are compared against its "
          "drafted paragraphs",
          [(x["outline_lines"], x["paragraphs"]) for x in matched],
          [(1, 1)],
          "an unrecognised heading used to be parsed and then compared "
          "against nothing, so adherence silently stopped being checked")

    # --- item 73: the drafter is given the corpus and the tier rule ------
    brief = ms.agent_brief(root, "draft-sections")
    given = [g["name"] for g in brief["given"]]
    check("a review's drafter is given the corpus the citekeys stand for",
          ("corpus_records" in given, "corpus_synthesis" in given),
          (True, True))
    check("...and not the landscape a review never writes",
          "landscape" in given, False,
          "the corpus IS the landscape; the file handed over was a "
          "scaffolded stub")
    check("...and not an analysis.md this review does not manage",
          "analysis" in given, False)
    check("...and the tier rule is in the prompt, at the point the sentence "
          "is written",
          ("OUTRUN THE TIER" in brief["prompt"],
           "read_tier" in brief["prompt"]), (True, True))

    research = ms.agent_brief(build_project(tmp), "draft-sections")
    check("a research paper's brief is untouched by any of it",
          [g["name"] for g in research["given"]],
          [g for g in ms.AGENT_MODULES["draft-sections"]["given"]
           if g in [x["name"] for x in research["given"]]])
    check("...and carries no tier rule",
          "OUTRUN THE TIER" in research["prompt"], False)


def test_paper_kind(tmp: str) -> None:
    section("What the document IS, and absent reading as research "
            "(review-paper 1)")

    root = os.path.join(tmp, "kind")
    ms_scaffold(root, journal="Langmuir")
    ms.init(root, "Langmuir")
    ypath = os.path.join(root, "project.yml")

    # --- absent reads as research, and changes NOTHING ------------------
    #
    # Every project that exists today has no such key. Defaulting a missing
    # key to something consequential silently converts every one of them,
    # which is the same absent-versus-empty rule `manages` needed.
    raw = read(ypath)
    stripped = re.sub(r"^paper_kind:.*$", "", raw, flags=re.M)
    write(root, "project.yml", stripped)
    check("a project.yml with no paper_kind reads as research",
          ms.paper_kind(root), "research")
    check("...and gets the research scope set, not every scope",
          ms.read_scope(root, "Langmuir"),
          list(ms.SCOPE_DEFAULTS["research"]))
    check("...and `corpus` is not reported as out of scope",
          [x["area"] for x in ms.completeness(root, "Langmuir")["out_of_scope"]
           if x["area"] == "corpus"], [])
    before = ms.completeness(root, "Langmuir")

    # An empty value is not a third state either.
    write(root, "project.yml", stripped.rstrip("\n") + "\npaper_kind:\n")
    check("paper_kind: with no value is not a third state",
          ms.paper_kind(root), "research")
    # A typo must not put a project into the mode that RELEASES checks.
    write(root, "project.yml", stripped.rstrip("\n") + "\npaper_kind: reveiw\n")
    check("a misspelled kind reads as research, not as review",
          ms.paper_kind(root), "research")
    write(root, "project.yml", raw)

    check("...and none of that changed what completeness reports",
          [m["area"] for m in ms.completeness(root, "Langmuir")["missing"]],
          [m["area"] for m in before["missing"]])

    # --- setting it is a real event -------------------------------------
    res = ms.set_paper_kind(root, "review", "narrative")
    check("--paper-kind review is accepted", res["errors"], [])
    check("...and recorded", ms.paper_kind(root), "review")
    check("...with its review kind", ms.review_kind(root), "narrative")
    check("...and reported as a change, not done quietly",
          [c["key"] for c in res["changes"] if c["action"] == "set"],
          ["paper_kind", "review_kind"])
    check("...naming what changes with it",
          "layer-0" in [c for c in res["changes"]
                        if c["key"] == "paper_kind"][0]["detail"], True)

    check("saying it twice is a no-op",
          [c["action"] for c in ms.set_paper_kind(root, "review")["changes"]],
          ["keep"])

    bad = ms.set_paper_kind(root, "monograph")
    check("an unknown paper kind is refused", bool(bad["errors"]), True)
    check("...and nothing is written", ms.paper_kind(root), "review")
    bad = ms.set_paper_kind(root, "review", "meta")
    check("an unknown review kind is refused too", bool(bad["errors"]), True)

    # --- the review kind changes what is REPORTED, never what is RECORDED
    res = ms.set_paper_kind(root, "review", "systematic")
    check("a systematic review is told about registration NOW",
          any("PROSPERO" in n for n in res.get("notes") or []), True)
    check("...as a note and not an error - it does not refuse the change",
          res["errors"], [])
    res = ms.set_paper_kind(root, "review", "narrative")
    check("a narrative review is not",
          any("PROSPERO" in n for n in res.get("notes") or []), False)

    # --- and it moves the scope set --------------------------------------
    check("a review's default scope holds the corpus and not the analysis",
          list(ms.SCOPE_DEFAULTS["review"]), ["outline", "floats", "corpus"])
    check("a meta-analytic review may hold both, and nothing special-cases it",
          ms.scopes_for(root, ["outline", "floats", "analysis", "corpus"]),
          list(ms.MANAGED_SCOPES))
    check("...while a plain review is never asked about its analysis",
          ms.scopes_for(root, ["outline", "floats", "corpus"]),
          ["outline", "floats", "corpus"])


def test_manages_scope(tmp: str) -> None:
    section("What a project keeps in-tree, and what that stops reporting")

    root = os.path.join(tmp, "scope")
    ms_scaffold(root, journal="Langmuir")
    ms.init(root, "Langmuir")
    cfgp = os.path.join(root, "drafts", "Langmuir", "writing_config.yml")

    # "Everything" is everything this paper KIND scaffolds with, not every
    # member of MANAGED_SCOPES: `analysis` and `corpus` belong to different
    # kinds, and a research project told it manages a corpus reports a
    # missing data/corpus/ to its coauthors (review-paper 2).
    research = list(ms.SCOPE_DEFAULTS["research"])
    cfg = ms.read_config(cfgp)
    check("a fresh config manages everything this paper kind has",
          cfg["lists"]["manages"], research)
    check("...and the key is really in the template, not just defaulted",
          "manages:" in read(cfgp), True)
    check("...and a review's fresh config names the corpus instead",
          [l.strip() for l in ms.default_config("review").splitlines()
           if l.startswith("manages:")],
          ["manages: [outline, floats, corpus]"])

    # Absent and empty are different facts. Defaulting a missing key to []
    # would silently turn every project written before this key existed into
    # a manuscript-only one - and defaulting it to every scope would turn
    # every one of them into a review with a corpus it never had.
    parsed = ms.parse_config("outline_adherence: medium\n")
    check("a config with no manages: key manages everything",
          parsed["lists"]["manages"], research)
    parsed = ms.parse_config("manages: []\n")
    check("...while manages: [] means nothing, and stays nothing",
          parsed["lists"]["manages"], [])

    res = ms.configure(root, "Langmuir", manages=["outline,floats"])
    check("--manages takes a comma list", res["errors"], [])
    check("...and REPLACES rather than appends",
          ms.read_config(cfgp)["lists"]["manages"], ["outline", "floats"])

    res = ms.configure(root, "Langmuir", manages=["floats", "outline"])
    check("...recording it in the canonical order, not the typed one",
          ms.read_config(cfgp)["lists"]["manages"], ["outline", "floats"])
    check("...and saying so was a no-op",
          [c["action"] for c in res["changes"]], ["keep"])

    res = ms.configure(root, "Langmuir", manages=["flotas"])
    check("a typo is refused", bool(res["errors"]), True)
    check("...naming the real scopes",
          all(x in res["errors"][0] for x in ms.MANAGED_SCOPES), True)
    check("...and nothing is written on a refusal",
          ms.read_config(cfgp)["lists"]["manages"], ["outline", "floats"])

    ms.configure(root, "Langmuir", manages=["none"])
    check("`none` is the spelling for a manuscript-only project",
          ms.read_config(cfgp)["lists"]["manages"], [])

    # A hand-edited nonsense member is reported rather than passed through:
    # a member nothing recognises would read as a scope nobody manages.
    parsed = ms.parse_config("manages: [outline, flotas]\n")
    check("a hand-edited bad member is dropped",
          parsed["lists"]["manages"], ["outline"])
    check("...and reported, not silently",
          any("flotas" in i for i in parsed["invalid"]), True)

    # --- what it changes about the verdict -----------------------------
    ms.configure(root, "Langmuir", manages=["outline,floats,analysis"])
    full = ms.completeness(root, "Langmuir")
    ms.configure(root, "Langmuir", manages=["none"])
    trimmed = ms.completeness(root, "Langmuir")

    check("out_of_scope names every dropped scope",
          [s["area"] for s in trimmed["out_of_scope"]], research)
    check("...and never `corpus`, which is not a question for a research "
          "paper",
          [s["area"] for s in trimmed["out_of_scope"] if s["area"] == "corpus"],
          [])
    check("...and each says which files it owns",
          all(s["owns"] for s in trimmed["out_of_scope"]), True)
    check("scope travels with the verdict", trimmed["scope"], [])
    check("dropping the outline drops its blocking finding",
          [m for m in trimmed["missing"] if m["area"] == "outline"], [])
    check("...and the analysis findings",
          [m for m in trimmed["missing"] if m["area"] == "analysis"], [])
    check("...and fewer things are outstanding than on a full project",
          len(trimmed["missing"]) < len(full["missing"]), True,
          str(len(trimmed["missing"])) + " vs " + str(len(full["missing"])))
    check("the section stubs are STILL reported - those are in scope",
          any(m["area"] == "draft" for m in trimmed["missing"]), True)

    # What was not checked has to travel with the verdict. A clean result on
    # a trimmed project is a much narrower claim than a clean one on a full
    # project, and nothing else would say so.
    check("the summary says what was not checked",
          "Not checked here" in trimmed["summary"], True)
    check("...naming the scopes",
          all(s in trimmed["summary"] for s in research), True)
    check("a full project's summary says no such thing",
          "Not checked here" in full["summary"], False)

    block = ms.render_incomplete_block(trimmed, "Langmuir", 1)
    check("the .docx block names them too - the coauthor never saw a terminal",
          "Not checked in this build" in block, True)
    check("...and points at the key that decided it",
          "writing_config.yml" in block, True)

    # The one check that is NOT scoped away. Mock data reaching a coauthor is
    # the failure this pipeline is most careful about, and a provenance
    # register only exists because this project's own scripts wrote one.
    write(root, "plan/floats/float_provenance.json",
          json.dumps({"fig01_x.png": {"mock": True}}))
    guarded = ms.completeness(root, "Langmuir")
    check("mock data still blocks with every scope dropped",
          [m["severity"] for m in guarded["missing"] if "mock" in m["detail"]],
          ["blocking"])
    os.remove(os.path.join(root, "plan", "floats", "float_provenance.json"))

    # plan carries it, for the same reason it carries the dials (spec 2.2):
    # the line the user approves has to say what the run will not look at.
    pl = ms.plan(root, "Langmuir", "coauthor")
    check("plan reports the scope", pl["manages"], [])
    check("...and what is out of it", pl["out_of_scope"], research)
    check("...in the line the user approves",
          "not checked" in pl["line"], True)


def test_init_adapts(tmp: str) -> None:
    section("init adapts to the folder it is given, and always ends at drafts/")

    # --- a completely empty folder ---------------------------------------
    bare = os.path.join(tmp, "bare_folder")
    os.makedirs(bare)
    res = ms.init(bare, "Langmuir")
    check("an empty folder is scaffolded, not refused", res["errors"], [])
    check("drafts/source_text/ exists afterwards",
          os.path.isdir(stdir(bare)), True)
    check("...and references.bib beside it",
          os.path.isfile(os.path.join(bare, "drafts", "references.bib")), True)
    check("...and the journal folder",
          os.path.isdir(os.path.join(bare, "drafts", "Langmuir")), True)
    check("project.yml is created rather than demanded",
          os.path.isfile(os.path.join(bare, "project.yml")), True)
    check("...and it records the journal",
          ms.read_project_yml(bare).get("target_journal"), "Langmuir")

    # The stubs must come from scaffold.py's manifest. A second copy of the
    # drafts layout inside manuscript.py is the CAPTION_HEADING_RE problem.
    want = sorted(os.path.basename(d) for d, _ in sc.FILES
                  if d.startswith("drafts/source_text/"))
    got = sorted(os.listdir(stdir(bare)))
    check("the section files are exactly the manifest's", got, want)

    # No plan/, so nothing that only plan/ reads gets conjured into being.
    check("plan/ is not created - the floats are managed elsewhere",
          os.path.isdir(os.path.join(bare, "plan")), False)
    check("...and neither is data/",
          os.path.isdir(os.path.join(bare, "data")), False)
    skipped = [a for a in res["actions"] if a["action"] == "skip"]
    check("journal_target.yml is skipped, and reported as skipped",
          [a["path"] for a in skipped], ["plan/theme/journal_target.yml"])
    check("...saying why", "managed outside" in skipped[0]["detail"], True)

    res = ms.init(bare, "Langmuir")
    check("a second init on it creates nothing",
          [a for a in res["actions"] if a["action"] == "create"], [])

    # --- a dry run on an empty folder ------------------------------------
    dry = os.path.join(tmp, "dry_folder")
    os.makedirs(dry)
    res = ms.init(dry, "JACS", dry_run=True)
    check("a dry run reports no errors on a bare folder", res["errors"], [])
    check("...and writes nothing", os.listdir(dry), [])
    # The bug this pins: `raw = _read(ypath)` on a project.yml this same call
    # had only offered to create reported "project.yml has no journals: key",
    # a defect in a file that did not exist yet.
    check("...and does not report a defect in the file it would have written",
          [e for e in res["errors"] if "journals" in e], [])

    # --- the trimmed shape: source_text/ at the top level ----------------
    # This used to be refused and the user told to move it into drafts/. It is
    # the trimmed layout of spec 7.2.2 - what a manuscript-only project looks
    # like when the floats and data are managed elsewhere - and refusing it is
    # what got worked around by hand the first time this engine met a real
    # project.
    stray = os.path.join(tmp, "stray_folder")
    os.makedirs(os.path.join(stray, "source_text"))
    write(stray, "source_text/introduction.md", "# Introduction\n\nReal prose.")
    write(stray, "source_text/methods.md", "# Methods\n\nMore real prose.")
    res = ms.init(stray, "Langmuir")
    check("a populated top-level source_text/ is accepted, not refused",
          res["errors"], [])
    check("...recognised as trimmed", ms.is_trimmed(stray), True)
    check("...so the journal folder goes beside it, not under drafts/",
          os.path.isdir(os.path.join(stray, "Langmuir")), True)
    # The whole point: no second drafting folder is created, because the one
    # that exists is the real one.
    check("...and no drafts/ is created beside the writing",
          os.path.isdir(os.path.join(stray, "drafts")), False)
    check("...and the existing prose is what the engine reads",
          os.path.isfile(stfile(stray, "introduction.md")), True)

    # The bug this whole change exists for: `status` reported five empty
    # sections while the writing sat one directory up, at exit code 0.
    st = ms.status(stray)
    words = {x["section"]: x["words"] for x in st["sections"]}
    check("status counts the trimmed project's real words",
          words.get("introduction", 0) > 0, True)
    check("...and finds the journal folder", st["journals"], ["Langmuir"])

    # --- the clean slate: prose deleted so the skill can redraft it -------
    # A trimmed project whose sections are wiped must NOT flip back to
    # expecting drafts/. The journal folder is what still marks it, and
    # without that mark the redraft would land somewhere else.
    for f in os.listdir(stdir(stray)):
        os.remove(stfile(stray, f))
    check("an emptied trimmed project is still trimmed",
          ms.is_trimmed(stray), True)
    check("...because the journal folder still marks it",
          ms._is_drafting_folder(stray), True)
    res = ms.init(stray, "Langmuir")
    check("...so re-init still creates no drafts/",
          os.path.isdir(os.path.join(stray, "drafts")), False)

    # --- both layouts at once is the case that is still refused -----------
    both = os.path.join(tmp, "both_layouts")
    os.makedirs(os.path.join(both, "drafts", "source_text"))
    os.makedirs(os.path.join(both, "source_text"))
    write(both, "drafts/source_text/introduction.md", "# Intro\n\nIn drafts.")
    write(both, "source_text/introduction.md", "# Intro\n\nAt the top.")
    res = ms.init(both, "Langmuir")
    check("a project holding BOTH layouts is refused",
          bool(res["errors"]), True)
    check("...naming the files that would never be built from",
          "introduction.md" in res["errors"][0], True)
    check("...and saying to merge rather than to move",
          "Merge" in res["errors"][0], True)

    # An EMPTY top-level source_text/ with no journal beside it is not a
    # layout at all - nothing has been created yet, so it is scaffolded like
    # any other bare folder.
    empty_st = os.path.join(tmp, "empty_st")
    os.makedirs(os.path.join(empty_st, "source_text"))
    res = ms.init(empty_st, "Langmuir")
    check("an EMPTY top-level source_text/ is not refused", res["errors"], [])
    check("...and is scaffolded into drafts/ like a bare folder",
          os.path.isdir(stdir(empty_st)), True)

    # --- pointed inside a project ----------------------------------------
    res = ms.init(os.path.join(bare, "drafts"), "JACS")
    check("init inside a project is refused", bool(res["errors"]), True)
    check("...and names the real root",
          os.path.abspath(bare) in res["errors"][0], True)
    check("...and creates no nested drafts/",
          os.path.isdir(os.path.join(bare, "drafts", "drafts")), False)

    # The ancestor walk must terminate. On Windows dirname of a drive root
    # returns the drive root, so a naive loop spins forever rather than
    # failing.
    check("the ancestor walk terminates at the drive root",
          ms._enclosing_project(os.path.abspath(os.sep)), None)



# ---------------------------------------------------------------------------
# The reference document, and holding the built file to the requirements
# ---------------------------------------------------------------------------

def test_reference_doc(tmp: str) -> None:
    section("What the .docx looks like, and whether it matches the journal")

    root = _scaffold(tmp, "refdoc_case")
    if not root:
        skip("every reference-doc check", "scaffold failed")
        return
    ms.init(root, "Langmuir")
    jdir = os.path.join(root, "drafts", "Langmuir")
    reqp = os.path.join(jdir, "journal_requirements", "requirements.yml")

    # --- the patch has to actually land ----------------------------------
    res = ms.reference_doc(root, "Langmuir")
    check("a reference document is generated", res["errors"], [])
    check("...at journal_requirements/reference.docx",
          os.path.isfile(res["output"]), True)
    check("...and every style this module knows about was patched",
          res["styles_missing"], [],
          "a style silently skipped is how the headings stayed blue")
    check("the alternate spelling of line_numbering is read too",
          ms._req({"text.line_numbering": "continuous"},
                  *ms.LINE_NUMBER_FIELDS), "continuous")

    styles = ms._docx_part(res["output"], "word/styles.xml")

    # The two attributes that win over the value beside them. Both were
    # found by opening the result in Word, not by reading the XML: a
    # styles.xml can name Times New Roman and #000000 and still render in
    # Aptos Display and accent blue.
    check("no theme font survives anywhere in the reference document",
          "asciiTheme" in styles, False)
    themed = [m.group(1) for m in re.finditer(
        r'<w:style [^>]*w:styleId="([^"]+)">(?:(?!</w:style>).)*?themeColor',
        styles, re.S)]
    # Hyperlink is the one style that should keep a colour of its own - a DOI
    # that looks like body text is a worse document, not a plainer one.
    check("no style but Hyperlink takes its colour from the theme",
          [s for s in themed if s != "Hyperlink"], [])

    # OOXML complex types are sequences. Word rejects a bad order by
    # refusing to open the file and naming neither element nor reason.
    bad = []
    for m in re.finditer(r'<w:style [^>]*w:styleId="([^"]+)">(.*?)</w:style>',
                         styles, re.S):
        kids = re.findall(r"<w:(\w+)[ />]", m.group(2))
        if "pPr" in kids and "rPr" in kids and \
                kids.index("rPr") < kids.index("pPr"):
            bad.append(m.group(1))
    check("w:pPr precedes w:rPr in every style", bad, [],
          "Word will not open the file otherwise, and will not say why")

    idx = ms._style_index(styles)
    for sid in ("Normal", "BodyText", "Heading1", "Heading2", "Title",
                "Bibliography"):
        check(f"{sid} is the body font", idx[sid]["font"], "Times New Roman")
        check(f"...at body size", idx[sid]["size_pt"], 12.0)
        check(f"...double spaced", str(idx[sid]["line"]), "480")
    check("the reference list is spaced like the body, not like the default",
          idx["Bibliography"]["line"], idx["BodyText"]["line"],
          "Bibliography basedOn Normal is how a single-spaced reference "
          "list hid under a double-spaced body")

    # --- line numbering is opt-in ----------------------------------------
    doc = ms._docx_part(res["output"], "word/document.xml")
    check("line numbering is off when the journal never asked",
          "lnNumType" in doc, False,
          "Langmuir's guidelines do not mention line numbers, and the "
          "group's own accepted Langmuir manuscript carries none")
    check("...and the build does not claim the journal wanted it",
          res["line_numbers"], False)

    forced = ms.reference_doc(root, "Langmuir", line_numbers=True)
    check("--line-numbers turns them on",
          "lnNumType" in ms._docx_part(forced["output"], "word/document.xml"),
          True)
    check("...and says the journal did not ask for them",
          any("do not ask for it" in w for w in forced["warnings"]), True)

    original = read(reqp)
    check("the scaffold spells the field line_numbers",
          "line_numbers: unknown" in original, True,
          "a requirements.yml written by hand said line_numbering, and "
          "reading only one spelling makes a real requirement invisible")
    write(root, os.path.relpath(reqp, root),
          original.replace("line_numbers: unknown",
                           "line_numbers: continuous"))
    sourced = ms.reference_doc(root, "Langmuir")
    check("a journal that DOES require them gets them",
          "lnNumType" in ms._docx_part(sourced["output"],
                                       "word/document.xml"), True)
    check("...with nothing to apologise for",
          any("do not ask" in w for w in sourced["warnings"]), False)
    write(root, os.path.relpath(reqp, root), original)
    ms.reference_doc(root, "Langmuir")

    # --- a real build, read back through Word's own model ----------------
    res = ms.assemble(root, "Langmuir")
    if res["errors"]:
        skip("format-check against a real build", "; ".join(res["errors"]))
    else:
        fc = ms.format_check(root, "Langmuir", res["output"])
        check("a build through the generated reference doc is clean",
              [f for f in fc["findings"] if f["severity"] == "error"], [],
              "these are the errors an editor would see first")
        check("...and format-check runs as part of assemble",
              "format" in res, True)


def test_format_check_catches_the_default_theme(tmp: str) -> None:
    section("format-check against a document built with no reference doc")

    root = _scaffold(tmp, "themed_case")
    if not root:
        skip("every format-check check", "scaffold failed")
        return
    ms.init(root, "Langmuir")
    jdir = os.path.join(root, "drafts", "Langmuir")

    # Pandoc's default is the failure being guarded against, so build one on
    # purpose. This is what shipped: Aptos Display headings at 20pt in
    # accent blue over a Times New Roman body, and every existing gate
    # passed it.
    src = os.path.join(jdir, "_themed.md")
    with open(src, "w", encoding="utf8") as fh:
        fh.write("% A Title\n\n# Abstract\n\nText.\n\n"
                 "# Introduction\n\nText.\n\n# Conclusions\n\nText.\n")
    out = os.path.join(jdir, "themed.docx")
    r = subprocess.run([pandoc_bin(), src, "-o", out],
                       capture_output=True, text=True)
    if r.returncode != 0:
        skip("format-check on a themed build", "pandoc failed")
        return

    fc = ms.format_check(root, "Langmuir", out)
    checks = " | ".join(f["check"] for f in fc["findings"])
    check("a theme font is an error, not a warning",
          any(f["severity"] == "error" and "theme font" in f["check"]
              for f in fc["findings"]), True, checks)
    check("so is a theme colour",
          any(f["severity"] == "error" and "colour from the theme" in f["check"]
              for f in fc["findings"]), True, checks)
    check("an oversized heading is reported",
          any("Heading1 size" in f["check"] for f in fc["findings"]), True,
          checks)
    check("...and the document is not called clean", fc["clean"], False)
    check("errors are counted separately from warnings",
          fc["error_count"] > 0 and fc["warning_count"] > 0, True)

    # A theme font is wrong when it resolves to the wrong face - not because
    # it is a theme reference. Point the requirements at the face this
    # document's theme actually resolves to and the same file stops erroring
    # on its fonts. Reporting every theme reference as an error flagged a
    # real IJROBP submission whose theme major font is the right one.
    theme = ms._theme_fonts(out)
    check("the theme's major font is readable", bool(theme["major"]), True,
          str(theme))
    reqp = os.path.join(jdir, "journal_requirements", "requirements.yml")
    original = read(reqp)
    write(root, os.path.relpath(reqp, root),
          original.replace("font_family: unknown",
                           "font_family: %s" % theme["major"]))
    fc3 = ms.format_check(root, "Langmuir", out)
    check("a theme font resolving to the right face is a note, not an error",
          [f for f in fc3["findings"]
           if f["severity"] == "error" and "font" in f["check"]], [],
          " | ".join("%s:%s" % (f["severity"], f["check"])
                     for f in fc3["findings"]))
    check("...and it is still said out loud, because a theme travels",
          any(f["severity"] == "note" and "from the theme" in f["check"]
              for f in fc3["findings"]), True)

    # A requirement the journal states as a choice is not a defect. Langmuir
    # says a manuscript "may be single or double spaced"; recording the pick
    # as double must not fail a single-spaced file.
    write(root, os.path.relpath(reqp, root),
          original.replace("line_spacing: unknown",
                           "line_spacing: double\n  "
                           "line_spacing_source: G_optional"))
    fc4 = ms.format_check(root, "Langmuir", out)
    check("an optional requirement is a note, never an error",
          [f for f in fc4["findings"]
           if f["severity"] == "error" and "spacing" in f["check"]], [])
    write(root, os.path.relpath(reqp, root), original)

    # --- the noise floor --------------------------------------------------
    # Run against the group's own accepted Langmuir manuscript, the first
    # version of this check returned two errors: `Times` vs `Times New Roman`
    # (the same face spelled two ways) and single vs double spacing (which
    # Langmuir's own guidelines call a choice). Both were true readings and
    # neither was a defect. A check that fires on a document nobody has a
    # complaint about gets deleted rather than fixed.
    check("the same typeface spelled two ways is one typeface",
          ms._font_key("Times"), ms._font_key("Times New Roman"))
    check("...and TimesNewRomanPSMT is still the same face",
          ms._font_key("TimesNewRomanPSMT"), ms._font_key("Times New Roman"),
          "macOS Word writes the PostScript name")
    check("a spacing finding names the spacing, not the twips",
          ms._spacing_name(480), "double (480 twips)")

    good = ms.reference_doc(root, "Langmuir")
    out2 = os.path.join(jdir, "plain.docx")
    subprocess.run([pandoc_bin(), src, "-o", out2,
                    "--reference-doc", good["output"]],
                   capture_output=True, text=True)
    fc2 = ms.format_check(root, "Langmuir", out2)
    check("the same document through the generated reference doc is clean",
          [f for f in fc2["findings"] if f["severity"] == "error"], [],
          " | ".join(f["check"] for f in fc2["findings"]))


def test_block_list_requirements(tmp: str) -> None:
    section("A requirement written as a YAML block list is still a list")

    root = _scaffold(tmp, "blocklist_case")
    if not root:
        skip("every block-list check", "scaffold failed")
        return
    ms.init(root, "Langmuir")
    reqp = os.path.join(root, "drafts", "Langmuir", "journal_requirements",
                        "requirements.yml")
    original = read(reqp)

    # requirements.yml is written by hand from a journal's guidelines, and an
    # eleven-section order is written as a block list by anyone doing that.
    # Measured on a real Langmuir folder: the inline form was the only one
    # understood, the block form read as the empty string, and section_order
    # fell back to the scaffold's five files without a word.
    write(root, os.path.relpath(reqp, root), original.replace(
        "section_order: [title_abstract, introduction, methods, results, "
        "discussion]",
        "section_order:\n"
        "    - title_abstract\n"
        "    - introduction\n"
        "    - methods\n"
        "    - results\n"
        "    - discussion\n"))
    req = ms.read_flat_yml(reqp)
    check("a block list is read back bracketed",
          req["structure.section_order"],
          "[title_abstract, introduction, methods, results, discussion]")
    order, note = ms.section_order(req)
    check("...so the journal's order is what gets used", order,
          ["title_abstract", "introduction", "methods", "results",
           "discussion"])
    check("...with nothing to warn about", note, "")

    # The silent case is the one that mattered: no value at all must warn.
    write(root, os.path.relpath(reqp, root), original.replace(
        "section_order: [title_abstract, introduction, methods, results, "
        "discussion]", "section_order:"))
    order, note = ms.section_order(ms.read_flat_yml(reqp))
    check("an order with no value falls back to the scaffold's",
          order, ms.SECTION_FILES)
    check("...and says so, where before it did not", bool(note), True,
          "a silent fallback is the whole family of bug this guards")

    # The third door into the same bug: an inline list too long for one line.
    # Read as far as the first line's `]`-less fragment, a real Langmuir order
    # silently lost Experimental Section, Results and Discussion and
    # Conclusions, and the build exited 0 at 2763 words.
    write(root, os.path.relpath(reqp, root), original.replace(
        "section_order: [title_abstract, introduction, methods, results, "
        "discussion]",
        "section_order: [Title, Author List, Abstract, Introduction,\n"
        "                  Experimental Section, Results and Discussion,\n"
        "                  Conclusions]"))
    order, note = ms.section_order(ms.read_flat_yml(reqp))
    check("a wrapped inline list is read to its closing bracket", order,
          ["Title", "Author List", "Abstract", "Introduction",
           "Experimental Section", "Results and Discussion", "Conclusions"])
    check("...and the keys after it are still read",
          ms.read_flat_yml(reqp).get("text.font_family"), "unknown")

    # Unclosed is the one case the reader cannot fix quietly, so it says so
    # to any caller that asks - and does not eat the rest of the file.
    write(root, os.path.relpath(reqp, root), original.replace(
        "section_order: [title_abstract, introduction, methods, results, "
        "discussion]",
        "section_order: [Title, Author List, Abstract, Introduction,"))
    problems: list[str] = []
    req = ms.read_flat_yml(reqp, problems)
    check("an unterminated inline list is reported", len(problems), 1)
    check("...naming the key", "section_order" in problems[0], True)
    check("...and it does not swallow the rest of the file",
          req.get("text.font_family"), "unknown")
    check("...while a caller that does not ask is unaffected",
          ms.read_flat_yml(reqp).get("structure.section_order"),
          "[Title, Author List, Abstract, Introduction,")

    write(root, os.path.relpath(reqp, root), original)


def test_alias_spelling_is_not_a_heading(tmp: str) -> None:
    section("An alias spelling in section_order does not reach the page")

    if not shutil.which("pandoc"):
        skip("every alias-heading check", "pandoc is not on PATH")
        return

    sub_tmp = os.path.join(tmp, "aliashead")
    os.makedirs(sub_tmp, exist_ok=True)
    root = build_project(sub_tmp)
    ms.init(root, "Langmuir")
    reqp = os.path.join(root, "drafts", "Langmuir", "journal_requirements",
                        "requirements.yml")
    write(root, os.path.relpath(reqp, root), read(reqp).replace(
        "section_order: [title_abstract, introduction, methods, results, "
        "discussion]",
        "section_order: [Title, Author list, Abstract, Introduction,\n"
        "                  experimental_section, results_and_discussion,\n"
        "                  references]"))
    res = ms.assemble(root, "Langmuir")
    if res["errors"]:
        skip("every alias-heading check", "; ".join(res["errors"]))
        return
    check("the build warns about the alias spelling",
          len([w for w in res["warnings"] if "alias spelling" in w]), 3)
    heads = [h["text"] for h in
             ms._headings(ms._docx_part(res["output"], "word/document.xml"))]
    check("...and no slug reached the page",
          [h for h in heads if h in ("experimental_section",
                                     "results_and_discussion", "references")],
          [], "the engine understood the alias perfectly and printed it")
    check("...the journal's own words did",
          [x for x in ("Experimental Section", "Results and Discussion",
                       "References") if x not in heads], [])


def test_specific_example_is_the_one_place(tmp: str) -> None:
    section("A defect item is generic, and specifics live in one field "
            "(defect-item-shape 2, 3)")

    spec = {"title": "A probe filed by the test suite",
            "where": "tests/manuscript.py",
            "wanted": "never to appear in the real file",
            "verify": "this suite writes to a temporary copy"}

    # 1. THE REGRESSION. No example renders exactly today's item, byte for
    #    byte - which is what makes the field safe to add to a file with 68
    #    items already in it and an unknown number written by hand.
    plain = ms._sc_item(7, "probe", spec, "2026-09-11", "it went wrong")
    check("an item with no example carries no Specific Example bullet",
          "Specific Example" in plain, False)
    check("...and its bullets are the seven the file's rules ask for",
          re.findall(r"^- \*\*(.+?):\*\*", plain, re.M),
          ["Status", "Code", "First seen", "Last seen", "Where",
           "Wanted behavior", "Verify by"])

    # 2. With one, the bullet sits between the detail paragraph and Wanted
    #    behavior - so the generic half reads first and the specific is
    #    visibly an aside rather than the description.
    withex = ms._sc_item(7, "probe", spec, "2026-09-11", "it went wrong",
                         example="sample ZQx7 came back empty")
    check("an example renders as a Specific Example bullet",
          "- **Specific Example:** sample ZQx7 came back empty" in withex,
          True)
    order = re.findall(r"^- \*\*(Specific Example|Wanted behavior):\*\*",
                       withex, re.M)
    check("...between the detail and Wanted behavior",
          order, ["Specific Example", "Wanted behavior"])
    check("...and the detail paragraph is still above it",
          withex.index("it went wrong") < withex.index("Specific Example"),
          True)

    # 6. An item is a description, not a log. Seeing it again bumps the count
    #    and leaves everything that describes it - including the example -
    #    exactly as it was.
    bumped = ms._sc_bump(withex, "2026-09-12")
    check("a second sighting leaves the example alone",
          "- **Specific Example:** sample ZQx7 came back empty" in bumped,
          True)
    check("...and bumps the run count", "(2 runs)" in bumped, True)

    # 3 + 4. The two tiers, at the writer. A refusal costs one retry; the same
    #        token in --example is never linted, because that is what it is
    #        for.
    work = os.path.join(tmp, "shapes")
    os.makedirs(work, exist_ok=True)
    scfile = os.path.join(work, "system-changes.md")
    source = LEDGER_SOURCE
    if not os.path.isfile(source):
        skip("the log-issue lint", "no system-changes.md in the repo")
        return
    shutil.copyfile(source, scfile)
    proj = os.path.join(work, "Proj")
    os.makedirs(proj, exist_ok=True)

    refused = ms.log_engine_issues(
        [dict(spec, code="probe_refuse",
              detail="the drafter dropped sample ZQx7 from the table")],
        proj, "Langmuir", 1, scfile, origin=ms.HAND_ORIGIN)
    check("a sample-id-shaped token in --detail is refused",
          refused["refused"], ["probe_refuse"])
    check("...the refusal names the field", "--detail" in refused["note"],
          True)
    check("...and quotes the token", "ZQx7" in refused["note"], True)
    check("...and says where it belongs",
          "--example" in refused["note"], True)
    check("...and nothing was written",
          "probe_refuse" in read(scfile), False)

    ok = ms.log_engine_issues(
        [dict(spec, code="probe_example",
              detail="the drafter dropped a sample from the table",
              example="sample ZQx7 came back empty at 1:3")],
        proj, "Langmuir", 1, scfile, origin=ms.HAND_ORIGIN)
    check("the same token in --example is accepted",
          [x["code"] for x in ok["logged"]], ["probe_example"])
    check("...and reaches the file intact",
          "sample ZQx7 came back empty at 1:3" in read(scfile), True)

    # 5. REPORT tier: a measurement is linted and the item is still filed.
    #    Blocking a defect from being FILED because its explanation contains a
    #    number would be worse than the leak it prevents.
    lint = ms.log_engine_issues(
        [dict(spec, code="probe_report",
              detail="the caption said 40 nm and the axis said otherwise")],
        proj, "Langmuir", 1, scfile, origin=ms.HAND_ORIGIN)
    check("a measurement in --detail does not refuse", lint["refused"], [])
    check("...the item is filed anyway",
          [x["code"] for x in lint["logged"]], ["probe_report"])
    check("...and it is reported as a lint",
          [h["token"] for h in lint["lint"]], ["40 nm"])

    # The gate that keeps a build from dying over its own defect list. An
    # engine-origin item lints but never refuses - spec 3's argument for
    # refusing is that the writer is an agent that can comply immediately,
    # which is true of log-issue and false of a build mid-run.
    eng = ms.log_engine_issues(
        [dict(spec, code="probe_engine",
              detail="the drafter dropped sample ZQx7 from the table")],
        proj, "Langmuir", 1, scfile)
    check("an engine-origin item with the same token is not refused",
          eng["refused"], [])
    check("...but it is still reported",
          any(h["token"] == "ZQx7" for h in eng["lint"]), True)


def test_engine_keeps_its_own_list(tmp: str) -> None:
    section("The engine writes its own defects into system-changes.md")

    # Measured against the real file, copied. Its shape is the contract - if
    # the headings this reads for are renamed, these checks are how anyone
    # finds out, rather than a build quietly logging nothing for a month.
    source = LEDGER_SOURCE
    if not os.path.isfile(source):
        skip("every system-changes check", "no system-changes.md in the repo")
        return
    work = os.path.join(tmp, "syschanges")
    os.makedirs(work, exist_ok=True)
    scfile = os.path.join(work, "system-changes.md")
    shutil.copyfile(source, scfile)
    before = read(scfile)
    proj = os.path.join(work, "Some Project")
    os.makedirs(proj, exist_ok=True)
    highest = max(int(n) for n in re.findall(r"^### (\d+)\.", before, re.M))

    # Which codes are still a *first* sighting depends on what the engine has
    # already written about itself. This block used to name two codes
    # outright; then a real run logged `citekey_on_the_page` as item 26, that
    # code stopped being fresh, and the append path it was meant to measure
    # went untested while the suite read as broken. So pick codes with no item
    # in the file, and invent any shortfall in the shape `log-issue` accepts -
    # the test stays about appending however full the engine's list gets.
    used = set(re.findall(r"^- \*\*Code:\*\* `([^`]+)`", before, re.M))
    free = [c for c in ms.ENGINE_ISSUES if c not in used]
    issues = []
    for i, detail in enumerate(("detail one", "detail two")):
        if i < len(free):
            issues.append({"code": free[i], "detail": detail})
        else:
            issues.append({"code": "test_probe_%d" % i, "detail": detail,
                           "title": "A probe filed by the test suite",
                           "where": "tests/manuscript.py",
                           "wanted": "never to appear in the real file",
                           "verify": "this suite writes to a copy in a "
                                     "temporary directory"})
    codes = [i["code"] for i in issues]
    res = ms.log_engine_issues(issues, proj, "Langmuir", 1, scfile)
    check("both issues are logged", [x["code"] for x in res["logged"]], codes)
    check("...numbered after the highest item already there",
          [x["number"] for x in res["logged"]], [highest + 1, highest + 2])
    text = read(scfile)
    item = text[text.index("### %d." % (highest + 1)):
                text.index("### %d." % (highest + 2))]
    for field in ("- **Status:** OPEN", "- **Code:**", "- **First seen:**",
                  "- **Last seen:**", "- **Where:**",
                  "- **Wanted behavior:**", "- **Verify by:**"):
        check(f"the item carries {field.strip('- *:')}", field in item, True,
              item)
    check("...the run that saw it, by name",
          "Some Project" in item and ", r1" in item, True, item)
    check("...and what happened, in the run's own words",
          "detail one" in item, True, item)
    check("the open list still ends where it did",
          "## Closed items" in text and "## Run history" in text, True)
    check("nothing above the new items was rewritten",
          text[:text.index("### %d." % (highest + 1))],
          before[:before.index("---\n\n## Closed items")])

    # Idempotence. A build repeated five times inside one round must not turn
    # a defect list into a log.
    again = ms.log_engine_issues(issues[:1], proj, "Langmuir", 2, scfile)
    check("a second sighting logs no new item", again["logged"], [])
    check("...it updates the one that is there",
          [x["number"] for x in again["updated"]], [highest + 1])
    text = read(scfile)
    check("...and there is still one item per code",
          text.count("- **Code:** `%s`" % codes[0]), 1)
    check("...whose last seen is this run",
          "- **Last seen:** %s, Some Project, Langmuir, r2 (2 runs)" % ms.TODAY
          in text, True)
    check("...while first seen still says the first one",
          "- **First seen:** %s, Some Project, Langmuir, r1" % ms.TODAY
          in text, True)
    third = ms.log_engine_issues(issues[:1], proj, "Langmuir", 3, scfile)
    check("a third sighting counts three runs",
          "(3 runs)" in read(scfile), True, str(third))

    # The verify-by is not optional, and that is enforced rather than asked
    # for: a code the engine does not define needs one written by hand.
    was = read(scfile)
    bad = ms.log_engine_issues([{"code": "something_new",
                                 "detail": "it looked wrong"}],
                               proj, "Langmuir", 3, scfile)
    check("an unknown code with no verify-by is refused", bad["refused"],
          ["something_new"])
    check("...and says which fields are missing",
          all(w in bad["note"] for w in ("title", "wanted", "verify")), True,
          bad["note"])
    check("...and the file is untouched", read(scfile), was)
    good = ms.log_engine_issues(
        [{"code": "something_new", "detail": "it looked wrong",
          "title": "A thing the engine did", "where": "somewhere",
          "wanted": "not that", "verify": "run it and look"}],
        proj, "Langmuir", 3, scfile, origin=ms.HAND_ORIGIN)
    check("...one written in full is accepted",
          [x["code"] for x in good["logged"]], ["something_new"])
    check("...and says a person filed it, not the engine",
          "log-issue" in read(scfile), True)

    # Only engine misbehavior. There is no path in for anything else: the
    # code has to be one the engine defines, or come with its own verify-by.
    check("the codes are engine faults, not manuscript ones",
          sorted(ms.ENGINE_ISSUES),
          ["built_with_no_body", "citekey_on_the_page",
           "heading_not_the_journals", "markdown_on_the_page",
           "pandoc_failed", "quality_report_leaks_densities",
           "round_disagrees_with_disk",
           "summary_not_the_document", "word_counts_unexplained"])

    # Every code assemble EMITS must be one log_engine_issues will ACCEPT.
    # `markdown_on_the_page` was emitted by gate 3b and never registered, so
    # each time that defect fired the item was refused and the reason went
    # into a note nobody reads - a detector wired to a rejecting logger.
    src = read(os.path.join(ROOT, "tools", "manuscript.py"))
    emitted = set(re.findall(r'"code": "([a-z_]+)"', src)) - {"software"}
    check("every code the engine emits is one it defines",
          sorted(emitted - set(ms.ENGINE_ISSUES)), [],
          "an unregistered code is refused, so the finding is thrown away")
    for code, spec in ms.ENGINE_ISSUES.items():
        check(f"{code} says how to verify it",
              bool(spec["verify"].strip()) and bool(spec["wanted"].strip()),
              True)

    # Run history, which is what makes VERIFIED FIXED judgeable later.
    rows = read(scfile).count("\n| ")
    ms.record_run(proj, "Langmuir", 1, "built manuscript_r1.docx", "draft",
                  scfile)
    check("a run is recorded", read(scfile).count("\n| "), rows + 1)
    ms.record_run(proj, "Langmuir", 1, "built manuscript_r1.docx, 12 words",
                  "draft", scfile)
    check("...the same round again rewrites its row, not adds one",
          read(scfile).count("\n| "), rows + 1)
    check("...with the newer result", "12 words" in read(scfile), True)
    ms.record_run(proj, "Langmuir", 2, "REFUSED - pandoc exited 1", "draft",
                  scfile)
    check("...a different round is its own row",
          read(scfile).count("\n| "), rows + 2)

    # The whole way out of a build, refused or not.
    kept = read(scfile)
    out = ms.maintain_system_changes(
        {"errors": ["BUILD FAILED - citekey syntax survived into the "
                    "rendered document: [@x]"],
         "round": 4, "engine_issues": [{"code": "citekey_on_the_page",
                                        "detail": "gate 3 caught [@x]"}]},
        proj, "Langmuir", "draft", scfile)
    check("a refused build logs and records too", out["run_recorded"], True)
    check("...acting on the item it had already logged, rather than "
          "filing a second one",
          sorted(x["code"] for x in out["updated"] + out["reopened"]),
          ["citekey_on_the_page"],
          "`updated` while the item is open, `reopened` once a run has "
          "closed it (item 13) - both are the code being recognised, and "
          "which one you get depends on what the engine has since written "
          "about itself")
    check("...and it is not logged as a new item either way",
          [x["code"] for x in out["logged"]], [])
    check("...and the row says it refused",
          "r4: REFUSED" in read(scfile), True)
    check("a build that never started its round records no row",
          ms.maintain_system_changes({"errors": ["no journal folder"]},
                                     proj, "Langmuir", "draft",
                                     scfile)["run_recorded"], False)
    check("...and that leaves the run history as it was",
          read(scfile).count("\n| "), kept.count("\n| ") + 1)

    # The line the user is told, whether or not the build succeeded.
    line = ms.system_changes_line(out)
    check("the user is told, in one line",
          "system-changes.md" in line and "1 engine issue" in line, True, line)
    check("...and told nothing when there is nothing",
          ms.system_changes_line({"logged": [], "updated": []}), "")

    # A file this cannot recognize is not written to, and a missing one is
    # never created: an empty defect list appearing beside a project would be
    # a thing nobody asked for.
    stray = os.path.join(work, "elsewhere", "system-changes.md")
    miss = ms.log_engine_issues(issues[:1], proj, "Langmuir", 1, stray)
    check("a missing file is not created", os.path.exists(stray), False)
    check("...and the run is told why", "never creates it" in miss["note"],
          True, miss["note"])
    shapeless = os.path.join(work, "shapeless.md")
    write(work, "shapeless.md", "# notes\n\nnothing like the real file\n")
    odd = ms.log_engine_issues(issues[:1], proj, "Langmuir", 1, shapeless)
    check("a file of the wrong shape is refused", odd["refused"],
          ["heading_not_the_journals"])
    check("...and left exactly as it was", read(shapeless),
          "# notes\n\nnothing like the real file\n")
    # And the user is TOLD, in the line a successful log uses. A refusal that
    # only reaches a JSON field is how the engine stopped keeping its own
    # defect list for a week without anybody noticing (item 44).
    said = ms.system_changes_line(odd)
    check("...and the refusal is announced, not left in a field",
          said.startswith("NOTHING was logged to system-changes.md"), True,
          said)

    # A hand edit that leaves a stray blank line above `## Closed items` is
    # exactly what happened to the real file when items 39-43 were written
    # in by hand, and it silently disabled every write for as long as it
    # stood. The rule above the heading is what the anchor is really keyed
    # on; the whitespace between them is not the contract.
    loose = os.path.join(work, "loose.md")
    shutil.copyfile(source, loose)
    body = read(loose).replace("\n---\n\n## Closed items",
                               "\n---\n\n\n## Closed items")
    write(work, "loose.md", body)
    slack = ms.log_engine_issues(
        [{"code": free[0] if free else "test_probe_0",
          "detail": "a stray blank line must not silence this",
          "title": "A probe filed by the test suite",
          "where": "tests/manuscript.py",
          "wanted": "never to appear in the real file",
          "verify": "this suite writes to a copy in a temporary directory"}],
        proj, "Langmuir", 1, loose)
    check("a stray blank line above the heading does not silence the log",
          (slack["refused"], len(slack["logged"])), ([], 1), slack["note"])
    check("...and the item still went above Closed items, not into it",
          read(loose).index("### %d." % (highest + 1))
          < read(loose).rindex("## Closed items"), True)


def test_the_ladders_middle_rung(tmp: str) -> None:
    section("The status ladder has a writer for every rung it defines "
            "(engine-self-report 1)")

    # Against a copy of the real file, deliberately - this file's whitespace
    # is load-bearing (item 44) and a fixture is exactly where that stops
    # being true.
    source = LEDGER_SOURCE
    if not os.path.isfile(source):
        skip("every middle-rung check", "no system-changes.md in the repo")
        return
    work = os.path.join(tmp, "middlerung")
    os.makedirs(work, exist_ok=True)
    scfile = os.path.join(work, "system-changes.md")
    shutil.copyfile(source, scfile)
    proj = os.path.join(work, "Some Project")
    os.makedirs(proj, exist_ok=True)

    CODE = "a_probe_the_suite_marks_fixed"
    FILED = {"code": CODE, "detail": "what the run saw",
             "title": "A probe filed by the test suite",
             "where": "tests/manuscript.py",
             "wanted": "never to appear in the real file",
             "verify": "this suite writes to a copy in a temp directory"}
    filed = ms.log_engine_issues([FILED], proj, "Langmuir", 1, scfile,
                                 origin=ms.HAND_ORIGIN)
    number = filed["logged"][0]["number"]
    before = read(scfile)

    # 1. The rung the file's convention defines and nothing wrote.
    out = ms.mark_fix_attempted(CODE, "moved agent_calls in plan()",
                                path=scfile)
    check("--fixed moves an OPEN item to FIX ATTEMPTED",
          [m["number"] for m in out["marked"]], [number], out["note"])
    text = read(scfile)
    span = ms._sc_span(text, CODE)
    body = text[span[0]:span[1]] if span else ""
    check("...with today's date",
          "- **Status:** FIX ATTEMPTED %s" % ms.TODAY in body, True, body)
    check("...saying it is not verified, and what it was",
          "not verified. Was OPEN." in body, True, body)
    check("...and recording what was changed",
          "Changed: moved agent_calls in plan()" in body, True, body)
    check("...and it stays in the open list, above Closed items",
          text.index("### %d." % number) < text.rindex("## Closed items"),
          True)
    old_span = ms._sc_span(before, CODE)
    old_body = before[old_span[0]:old_span[1]] if old_span else ""
    check("...and only the status line was rewritten",
          [ln for ln in body.split("\n") if not ln.startswith("- **Status:")],
          [ln for ln in old_body.split("\n")
           if not ln.startswith("- **Status:")])
    check("...and nothing above the item was touched",
          text[:text.index("### %d." % number)],
          before[:before.index("### %d." % number)])

    # 2. Twice would destroy the one field that keeps a re-fix honest.
    was = read(scfile)
    twice = ms.mark_fix_attempted(CODE, "a second fix", path=scfile)
    check("a second --fixed is refused", (twice["marked"], twice["refused"]),
          ([], [CODE]))
    check("...naming the date it would have overwritten",
          ms.TODAY in twice["note"] and "Last seen" in twice["note"], True,
          twice["note"])
    check("...and why that matters - promote_verified reads it",
          "promote_verified" in twice["note"], True, twice["note"])
    check("...and the file is untouched", read(scfile), was)

    # 3. A closed item is reopened by the run that sees it, never by a flag.
    ctext = read(scfile)
    at = ms._sc_closed_at(ctext)
    closed_code = None
    for m in re.finditer(r"^- \*\*Code:\*\* `([^`]+)`", ctext, re.M):
        if at >= 0 and m.start() > at:
            closed_code = m.group(1)
            break
    if closed_code is None:
        skip("the refusal on a closed item", "nothing in Closed carries a code")
    else:
        was = read(scfile)
        shut = ms.mark_fix_attempted(closed_code, "a fix for a closed item",
                                     path=scfile)
        check("--fixed on an item in Closed is refused",
              (shut["marked"], shut["refused"]), ([], [closed_code]),
              shut["note"])
        check("...and it says the run that sees it reopens it, not a flag",
              "reopened by the run" in shut["note"], True, shut["note"])
        check("...and the item was NOT reopened", read(scfile), was)

    # 4. A rung that records a claim with no evidence cannot be audited.
    was = read(scfile)
    bare = ms.mark_fix_attempted(CODE, "   ", path=scfile)
    check("--fixed with no detail is refused", bare["refused"], [CODE])
    check("...and says the detail is what was changed",
          "what was changed" in bare["note"], True, bare["note"])
    check("...and the file is untouched", read(scfile), was)

    # 5. The third rung is a run's, and asking for it here is refused with
    #    the rule rather than quietly ignored.
    top = ms.mark_fix_attempted(CODE, "a fix", path=scfile,
                                rung="VERIFIED FIXED")
    check("--fixed cannot write VERIFIED FIXED", top["refused"], [CODE])
    check("...and the refusal quotes the only-a-completed-run rule",
          all(w in top["note"] for w in
              ("only a completed engine run", "promote_verified")), True,
          top["note"])
    check("...and the file is untouched", read(scfile), was)

    # 6. One gesture, two functions: the session that finds a defect in
    #    shipped code usually fixes it before it moves on.
    one = os.path.join(work, "one.md")
    two = os.path.join(work, "two.md")
    for dest in (one, two):
        shutil.copyfile(source, dest)
    NEW = "a_probe_filed_and_marked_at_once"
    SAID = "the fix and the finding, one gesture"
    fields = (("code", NEW), ("detail", SAID),
              ("title", "A probe filed by the test suite"),
              ("where", "tests/manuscript.py"),
              ("wanted", "never to appear in the real file"),
              ("verify", "this suite writes to a copy"))
    argv = []
    for key, value in fields:
        argv += ["--" + key, value]
    cli = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "manuscript.py"),
         "log-issue", proj, "--journal", "Langmuir", "--round", "1",
         "--fixed"] + argv,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=dict(os.environ, SYSTEM_CHANGES_MD=one, PYTHONIOENCODING="utf-8"))
    check("--code with --fixed files and marks in one invocation",
          cli.returncode, 0, cli.stdout + cli.stderr)
    check("...and says which rung the item is on now",
          "FIX ATTEMPTED" in cli.stdout, True, cli.stdout)
    ms.log_engine_issues([dict(fields)], proj, "Langmuir", 1, two,
                         origin=ms.HAND_ORIGIN)
    ms.mark_fix_attempted(NEW, SAID, path=two)
    check("...and the item is byte-identical to filing then marking",
          read(one), read(two))

    # 7. The rule the whole section writes down. Derived from the file's own
    #    convention table, so a rung added to it without a writer fails here
    #    rather than being reached by hand for a year.
    declared = re.findall(r"^\| `([A-Z][A-Z ]+)` \|", before, re.M)
    check("the file's convention declares three rungs", sorted(set(declared)),
          ["FIX ATTEMPTED", "OPEN", "VERIFIED FIXED"], declared)
    probe = ms._sc_item(1, "c", {"title": "t", "where": "w", "wanted": "x",
                                 "verify": "v"}, "seen", "d")
    writers = {
        "OPEN": ("_sc_item", probe),
        "FIX ATTEMPTED": ("mark_fix_attempted", body),
        "VERIFIED FIXED": ("_sc_promote",
                           ms._sc_promote(probe, "a run", ms.TODAY)),
    }
    check("every rung the convention defines has an engine writer",
          sorted(writers), sorted(set(declared)))
    for rung, (name, produced) in sorted(writers.items()):
        check("%s is written by %s()" % (rung, name),
              (hasattr(ms, name), "- **Status:** %s" % rung in produced),
              (True, True), produced)

    # 8. The existing guard, unchanged - and now reachable, because until
    #    --fixed existed nothing could put an item on the rung it guards.
    later = "2026-12-31"
    ms.log_engine_issues([FILED], proj, "Langmuir", 3, scfile, today=later)
    seen_again = read(scfile)
    span = ms._sc_span(seen_again, CODE)
    marked_body = seen_again[span[0]:span[1]] if span else ""
    check("a marked item reproduced later is bumped, not re-marked",
          "- **Last seen:** %s" % later in marked_body
          and "FIX ATTEMPTED %s" % ms.TODAY in marked_body, True, marked_body)
    held = ms.promote_verified(seen_again, [CODE], "a later run", later)
    check("...and promote_verified refuses to close it",
          (held[1], held[2]), ([], [CODE]))


def test_what_the_engine_writes_it_can_read_back(tmp: str) -> None:
    section("Where the engine writes a file it will later parse, the test is "
            "a round trip (engine-self-report 2)")

    # The failure this exists for: `RW-F1` rows were written correctly and
    # read back by NOTHING, so `flag_answers` returned an empty list about a
    # file that held every answer and `completeness` reported none
    # outstanding. A substring assertion cannot see that - the row WAS in the
    # file. Only a field-by-field round trip can.
    ROLLUP = {"RW": {"received": "r2", "items": 2, "applied": 0,
                     "pending": 2, "conflict": 0, "declined": 0}}
    # Every awkward field at once, because each one is a different way for a
    # generate-and-reparse pair to disagree: an id carrying a letter, a `|`
    # inside the request (the column separator itself), a `#` inside the note,
    # and a location that is not ASCII.
    rows = [
        {"id": "RW-F1", "round": "r2", "source": "RW",
         "location": "Results — paragraph 3, second sentence",
         "type": "flag",
         "request": "data - use the 320 °C run | not the 280 °C one",
         "state": "pending", "note": "flag RW-F1 # answered 2026-09-07",
         "fingerprint": "0123456789"},
        {"id": "RW-2", "round": "r2", "source": "RW", "location": "Methods",
         "type": "wording", "request": "say which detector", "state":
         "applied", "note": "", "fingerprint": "abcdef0123"},
    ]
    work = os.path.join(tmp, "roundtrip")
    os.makedirs(work, exist_ok=True)
    ledger = os.path.join(work, "edits_status.md")
    write_raw(ledger, ms._render_status("LANGMUIR", 2, ROLLUP, rows,
                                        {"RW": "Russell Mercer"}))
    back = ms._read_status(ledger)
    check("every row written is read back", sorted(back), sorted(
        r["fingerprint"] for r in rows))
    for want in rows:
        got = back.get(want["fingerprint"], {})
        for field in ("id", "round", "source", "location", "type", "request",
                      "state", "note"):
            check("%s: %s survives render -> parse" % (want["id"], field),
                  got.get(field), want[field])

    # And it is stable, which is the same property said the other way round:
    # a ledger regenerated every round must not accumulate escapes in a
    # coauthor's own words.
    round_tripped = [{**back[r["fingerprint"]],
                      "fingerprint": r["fingerprint"]} for r in rows]
    second = ms._render_status("LANGMUIR", 2, ROLLUP, round_tripped,
                               {"RW": "Russell Mercer"})
    check("render -> parse -> render is stable", second, read(ledger))

    # The retained marker is a render-side addition, so it is the one field
    # that must NOT accumulate: an inbox that stays empty regenerates this
    # row every round.
    marked = ms._render_status("LANGMUIR", 3, ROLLUP,
                               [{**rows[1], "retained": True}], {})
    write_raw(ledger, marked)
    again = ms._render_status(
        "LANGMUIR", 4, ROLLUP,
        [{**it, "fingerprint": fp, "retained": True}
         for fp, it in ms._read_status(ledger).items()], {})
    check("the retained marker is written once, not once per round",
          again.count("[source file no longer in edits/]"), 1, again)

    # A row the reader cannot parse is REPORTED. Silently dropping one is the
    # exact shape of the failure: the reader said nothing and the file said
    # everything.
    rows_text = ms._render_status("LANGMUIR", 2, ROLLUP, rows, {})
    broken = rows_text.replace("| RW-F1 |", "| RW F1 |")
    write_raw(ledger, broken)
    parsed, unreadable = ms._status_rows(ledger)
    check("a row whose id will not parse is not silently dropped",
          len(unreadable), 1, unreadable)
    check("...and the rows that do parse still do", len(parsed), 1)
    check("...and the unreadable line is quoted, so it can be found",
          "RW F1" in unreadable[0], True, unreadable)

    root = _scaffold(tmp, "roundtrip_project")
    if not root:
        skip("ingest surfaces the count", "scaffold failed")
    else:
        ms.init(root, "Langmuir")
        jdir = ms.journal_dir(root, "Langmuir")
        wr(os.path.join(ms.edits_dir(jdir), "edits_status.md"), broken)
        got = ms.ingest(root, "Langmuir", dry_run=True)
        check("ingest surfaces the count rather than merging over it",
              got.get("unreadable_rows"), 1, got.get("warnings"))
        check("...in a warning that says what it could not read",
              any("could not read" in w for w in got.get("warnings") or []),
              True, got.get("warnings"))

    # ------------------------------------------------------------------
    # The config. A dial that writes and does not read back is a bias
    # nobody asked for, silently - so every accepted value of every dial,
    # not one value of one dial.
    if not root:
        skip("every config round-trip check", "scaffold failed")
        return
    cfg = os.path.join(ms.journal_dir(root, "Langmuir"), "writing_config.yml")
    bad = []
    for key, values in sorted(ms.CONFIG_DIALS.items()):
        for value in values:
            res = ms.configure(root, "Langmuir", sets=["%s=%s" % (key, value)])
            if res.get("errors"):
                bad.append("%s=%s: %s" % (key, value, res["errors"]))
                continue
            readback = ms.read_config(cfg)
            if readback["dials"].get(key) != value:
                bad.append("%s=%s read back as %r"
                           % (key, value, readback["dials"].get(key)))
            if readback["invalid"]:
                bad.append("%s=%s reported invalid: %s"
                           % (key, value, readback["invalid"]))
    check("every accepted value of every dial reads back as itself", bad, [])

    # A list entry is free text a human typed, so the round trip has to hold
    # for the characters a human types: a colon, a `#`, a quote.
    said = ['keep the 320 °C run: it is the only one with a duplicate',
            'do not write "novel" anywhere',
            'the # of scans is 512, say so']
    ms.configure(root, "Langmuir", instruct=said, dated=False)
    got_list = ms.read_config(cfg)["lists"]["custom_instructions"]
    check("every instruction reads back as itself",
          [i for i in said if i not in got_list], [], got_list)
    ms.configure(root, "Langmuir", style_ref=["Smith 2019, JACS 141, 1"])
    check("...and so does a style ref",
          "Smith 2019, JACS 141, 1"
          in ms.read_config(cfg)["lists"]["style_refs"], True)

    # And the map, whose whole failure mode was that a stale value survived
    # underneath the new one and won the parse.
    weights = []
    for name in sorted(ms.WEIGHTABLE_SOURCES):
        allowed = ms.RESTRICTED_SOURCE_WEIGHTS.get(name, (None, ""))[0] \
            or ms.SOURCE_WEIGHT_LEVELS
        for level in allowed:
            ms.configure(root, "Langmuir", weight=["%s=%s" % (name, level)])
            got_map = ms.read_config(cfg)
            if got_map["maps"]["source_weights"].get(name) != level:
                weights.append("%s=%s read back as %r" % (
                    name, level, got_map["maps"]["source_weights"].get(name)))
            if got_map["invalid"]:
                weights.append("%s=%s reported invalid: %s"
                               % (name, level, got_map["invalid"]))
    check("every accepted weight of every weightable source reads back",
          weights, [])

    # ------------------------------------------------------------------
    # The outline: written by render_outline, parsed by prose.py, and the
    # only ledger of claims either check reads.
    bundle = {"sections": [{"section": "Introduction", "lines": [
        {"claim": "Cu(111) reconstructs above 320 °C - and the "
                  "reconstruction is what the decomposition follows",
         "evidence": "Fig01; @smith2019cu; Table01"},
        {"claim": "Nobody has measured the barrier under UHV",
         "evidence": ""}]}], "notes": ["not a paragraph of the paper"]}
    op = os.path.join(root, "plan", "outline.md")
    write_raw(op, ms.render_outline(bundle))
    parsed_lines = ms._prose(root, "outline").get("lines") or []
    check("every outline line written is read back",
          len(parsed_lines), 2, parsed_lines)
    for i, ln in enumerate(bundle["sections"][0]["lines"]):
        got_line = parsed_lines[i] if i < len(parsed_lines) else {}
        check("outline line %d: the claim survives" % (i + 1),
              got_line.get("claim"), ln["claim"])
        check("...and its evidence, unsplit",
              got_line.get("evidence_raw"), ln["evidence"])
        check("...under the section it was written under",
              got_line.get("section"), "Introduction")

    # ------------------------------------------------------------------
    # And the one this section demands an answer about rather than a test:
    # nothing re-reads the retention report, so its shape is not a contract.
    engine = read(os.path.join(ROOT, "tools", "manuscript.py"))
    readers = [ln for ln in engine.split("\n")
               if "retention_report" in ln
               and re.search(r"_read\(|open\(|_prose\(", ln)]
    check("nothing in the engine re-reads the retention report", readers, [])
    check("...and the writer says so in as many words",
          "not re-read" in (ms.render_retention_report.__doc__ or ""), True,
          ms.render_retention_report.__doc__)


def test_detectors_for_9_11_14(tmp: str) -> None:
    section("Items 9, 11 and 14 are detected on every build, so a run can "
            "close them")

    root = _scaffold(tmp, "detectors")
    if not root:
        skip("every detector check", "scaffold failed")
        return
    for stem, body in (("introduction", "A."), ("methods", "B."),
                       ("results", "C."), ("discussion", "D.")):
        write(root, "drafts/source_text/%s.md" % stem,
              "# %s\n\n%s\n" % (stem.title(), body))
    write(root, "drafts/source_text/authors.md", MATRIX_AUTHORS)
    write(root, "drafts/source_text/affiliations_and_funding.md",
          AFFILIATIONS_MD)
    ms.init(root, "Langmuir")

    res = ms.assemble(root, "Langmuir")
    if res["errors"]:
        skip("every detector check", "; ".join(res["errors"]))
        return
    codes = [i["code"] for i in res["engine_issues"]]

    # --- a clean build finds none of the three ---------------------------
    check("a clean build reports no markdown residue",
          "markdown_on_the_page" in codes, False, str(codes))
    check("...no summary/document mismatch",
          "summary_not_the_document" in codes, False, str(codes))
    check("...and no unexplained word count",
          "word_counts_unexplained" in codes, False, str(codes))
    check("both numbers are reported, each saying what it counted",
          (bool(res["words"]), bool(res["words_counted"]),
           res["prose_words"] is not None, bool(res["prose_words_counted"])),
          (True, True, True, True))

    # --- item 11 present: a flag written across a blank line -------------
    write(root, "drafts/source_text/results.md",
          "# Results\n\nC.\n\n**[FLAG: author] the barrier was not measured\n\n"
          "and nobody recorded which sample it came from.**\n")
    broke = ms.assemble(root, "Langmuir", force=True)
    if not broke["errors"]:
        check("a flag spanning a blank line is caught",
              "markdown_on_the_page" in
              [i["code"] for i in broke["engine_issues"]], True,
              str([i["code"] for i in broke["engine_issues"]]))
        check("...and it is a code the logger will accept",
              "markdown_on_the_page" in ms.ENGINE_ISSUES, True)
    else:
        skip("the markdown detector", "; ".join(broke["errors"]))
    write(root, "drafts/source_text/results.md", "# Results\n\nC.\n")

    # --- and a clean run closes all three ---------------------------------
    work = os.path.join(tmp, "detect_sc")
    os.makedirs(work, exist_ok=True)
    scfile = os.path.join(work, "system-changes.md")
    shutil.copyfile(LEDGER_SOURCE, scfile)
    before_body = read(scfile)
    clean = ms.assemble(root, "Langmuir", force=True)
    if clean["errors"]:
        skip("the promotion pass", "; ".join(clean["errors"]))
        return
    out = ms.maintain_system_changes(clean, root, "Langmuir", "draft", scfile)
    closed = {c["code"] for c in out["closed"]}
    body = read(scfile)
    # An item a previous real run already promoted cannot be promoted again,
    # and that is the loop working rather than a regression - so the check is
    # that each code ENDS UP settled, not that this particular run is the one
    # that settled it.
    settled = body[body.rindex("## Closed items"):]
    # WHAT IS ASSERTED HERE IS THE LADDER'S RULE, NOT ONE FIXED OUTCOME, and
    # that is a correction rather than a loosening. The version before this
    # one asserted that a clean run leaves all three closed - true of the
    # file on the day it was written, and untrue the moment a real run
    # REOPENED two of them, which is what happened on 2026-09-15 to items 9
    # and 14. The engine was right, `promote_verified` refused an OPEN item
    # exactly as its docstring says it must, and the suite went red for the
    # loop working. A defect suite that goes red when the defect loop works
    # is worse than no suite, because the next person reads four failures
    # and stops trusting the other 1796.
    #
    # So each code is held to the rule that governs it, in both directions:
    # an item at FIX ATTEMPTED whose `Last seen` does not postdate the fix
    # must be closed by a clean run, an item already in Closed must stay
    # there, and an OPEN or seen-since item must be HELD and named in
    # `not_judged` - a run that did not see an unfixed defect says only that
    # this run did not see it.
    for code, n in (("markdown_on_the_page", 11),
                    ("summary_not_the_document", 9),
                    ("word_counts_unexplained", 14)):
        span = ms._sc_span(before_body, code)
        check("%s is an item in the file at all" % code, span is not None,
              True)
        if span is None:
            continue
        start, end, was_closed = span
        item = before_body[start:end]
        status = group(r"^- \*\*Status:\*\* (.*)$", item, 1, re.M)
        fixed_on = re.search(r"FIX ATTEMPTED (\d{4}-\d{2}-\d{2})", status)
        last = re.search(r"^- \*\*Last seen:\*\* (\d{4}-\d{2}-\d{2})", item,
                         re.M)
        seen_since = bool(fixed_on and last and last.group(1) > fixed_on.group(1))
        promotable = status.startswith("FIX ATTEMPTED") and not seen_since
        in_closed = body.index("### %d." % n) > body.rindex("## Closed items")
        if was_closed:
            check("%s was closed by an earlier run and stays closed" % code,
                  (("`%s`" % code) in settled, in_closed), (True, True))
        elif promotable:
            check("a clean build closes %s" % code, code in closed, True,
                  "not judged: %s" % out["not_judged"])
            check("...and item %d is in the Closed section now" % n,
                  in_closed, True)
        else:
            check("a clean build does NOT close %s, which is %s"
                  % (code, status.split(" -")[0]),
                  code in out["not_judged"], True,
                  "closed by this run: %s" % sorted(closed))
            check("...and item %d stays in the open list" % n, in_closed,
                  False)


def test_a_run_closes_what_it_did_not_find(tmp: str) -> None:
    section("A run that looks for a defect and does not find it closes it")

    source = LEDGER_SOURCE
    if not os.path.isfile(source):
        skip("every promotion check", "no system-changes.md in the repo")
        return
    work = os.path.join(tmp, "promote")
    os.makedirs(work, exist_ok=True)
    scfile = os.path.join(work, "system-changes.md")
    shutil.copyfile(source, scfile)
    proj = os.path.join(work, "Some Project")
    os.makedirs(proj, exist_ok=True)

    code = "pandoc_failed"
    spec = ms.ENGINE_ISSUES[code]
    ms.log_engine_issues([{"code": code, "detail": "it failed once"}],
                         proj, "Langmuir", 1, scfile, today="2026-01-01")
    body = read(scfile)
    hit = re.search(r"### (\d+)\. %s" % re.escape(spec["title"][:30]), body)
    if hit is None:
        skip("every promotion check", "the probe item was not written")
        return
    num = int(hit.group(1))

    # Freshly logged, so OPEN: nothing has been attempted, and a run that
    # does not see it says only that the run did not see it.
    quiet = ms.log_engine_issues([], proj, "Langmuir", 2, scfile,
                                 today="2026-01-02", verified=True)
    check("an OPEN item is not closed by a quiet run",
          [c for c in quiet["closed"] if c["number"] == num], [],
          "other items in the real file may legitimately close here; this "
          "check is about the one just filed as OPEN")
    check("...and it says the item was not judged, rather than nothing",
          code in quiet["not_judged"], True, str(quiet["not_judged"]))

    # Mark it fixed, the way a person does.
    text = read(scfile)
    i = text.index("### %d." % num)
    j = text.index("- **Status:** OPEN", i)
    write(work, "system-changes.md",
          text[:j] + "- **Status:** FIX ATTEMPTED 2026-01-03 - not verified"
          + text[j + len("- **Status:** OPEN"):])

    done = ms.log_engine_issues([], proj, "Langmuir", 3, scfile,
                                today="2026-01-04", verified=True)
    check("a run that did not find it closes it",
          [c["number"] for c in done["closed"]], [num])
    after = read(scfile)
    item_at = after.index("### %d." % num)
    check("...the item is in the Closed section now",
          item_at > after.rindex("## Closed items"), True)
    check("...marked VERIFIED FIXED, with the run that judged it",
          ("VERIFIED FIXED 2026-01-04" in after
           and "Some Project, Langmuir, r3" in after[item_at:]), True)
    check("...and the date of the attempt it closes is kept",
          "was FIX ATTEMPTED 2026-01-03" in after[item_at:], True)
    closed_item = after[item_at:after.index("\n### ", item_at + 1)
                        if "\n### " in after[item_at + 1:] else None]
    check("...while everything else about it is cleared, examples included",
          [ln.split(":**")[0] for ln in closed_item.splitlines()
           if ln.startswith("- **")
           and not ln.startswith(("- **Code", "- **Status", "- **Last seen"))],
          [])
    check("the user is told, and told how narrow the claim is",
          all(x in ms.system_changes_line(done)
              for x in ("VERIFIED FIXED", "still need their own")), True)

    # It does not close twice, and a recurrence still reopens it (item 13).
    twice = ms.log_engine_issues([], proj, "Langmuir", 4, scfile,
                                 today="2026-01-05", verified=True)
    check("a closed item is not closed again", twice["closed"], [])
    again = ms.log_engine_issues([{"code": code, "detail": "back"}],
                                 proj, "Langmuir", 5, scfile,
                                 today="2026-01-06", verified=True)
    check("...and seeing it again reopens it",
          [r["number"] for r in again["reopened"]], [num])

    # A fix already seen to fail is not closed by one quiet run afterwards.
    text = read(scfile)
    i = text.index("### %d." % num)
    j = text.index("- **Status:**", i)
    k = text.index("\n", j)
    write(work, "system-changes.md",
          text[:j] + "- **Status:** FIX ATTEMPTED 2026-01-03 - not verified"
          + text[k:])
    stale = ms.log_engine_issues([], proj, "Langmuir", 6, scfile,
                                 today="2026-01-07", verified=True)
    check("a fix whose defect was seen AFTER it is not closed",
          ([c["number"] for c in stale["closed"]], code in
           stale["not_judged"]), ([], True),
          "last seen 2026-01-06 is after FIX ATTEMPTED 2026-01-03")

    # A refused build closes nothing: it stopped before the detectors ran.
    ok = ms.maintain_system_changes(
        {"errors": ["REFUSED - pandoc exited 1"], "round": 7},
        proj, "Langmuir", "draft", scfile)
    check("a refused build promotes nothing", ok["closed"], [])


def test_engine_logs_only_engine_faults(tmp: str) -> None:
    section("A manuscript problem is never logged as an engine problem")

    if not shutil.which("pandoc"):
        skip("every engine-fault check", "pandoc is not on PATH")
        return

    sub_tmp = os.path.join(tmp, "onlyengine")
    os.makedirs(sub_tmp, exist_ok=True)
    root = build_project(sub_tmp)
    ms.init(root, "Langmuir")

    # This project is deliberately unfinished: flags, gaps, an incomplete
    # paper. Every one of those is the draft's problem and has somewhere to
    # live already, so none of them may reach the toolkit's defect list.
    res = ms.assemble(root, "Langmuir")
    if res["errors"]:
        skip("every engine-fault check", "; ".join(res["errors"]))
        return
    check("the build has plenty to say about the manuscript",
          len(res["warnings"]) > 0, True)
    check("...and nothing to say about the engine",
          res.get("engine_issues"), [])
    check("...so a maintained file gains no item",
          ms.log_engine_issues(res.get("engine_issues") or [], root,
                               "Langmuir", 1,
                               os.path.join(sub_tmp, "nofile.md"))["logged"],
          [])

    # Now one the engine really is responsible for: an order in its own alias
    # spelling, where the heading on the page is the engine's word.
    reqp = os.path.join(root, "drafts", "Langmuir", "journal_requirements",
                        "requirements.yml")
    write(root, os.path.relpath(reqp, root), read(reqp).replace(
        "section_order: [title_abstract, introduction, methods, results, "
        "discussion]",
        "section_order: [Title, Abstract, Introduction, "
        "experimental_section,\n                  results_and_discussion]"))
    res = ms.assemble(root, "Langmuir")
    if res["errors"]:
        skip("the alias-heading engine issue", "; ".join(res["errors"]))
        return
    codes = [i["code"] for i in res.get("engine_issues") or []]
    check("the engine files the heading it supplied itself",
          codes, ["heading_not_the_journals"])
    detail = (res["engine_issues"][0]["detail"] if codes else "")
    check("...naming the slug and the heading it became",
          "experimental_section -> Experimental Section" in detail, True,
          detail)

    # And the CLI is what writes it: importing this module and building must
    # never write into the toolkit behind the caller's back.
    scfile = os.path.join(sub_tmp, "system-changes.md")
    shutil.copyfile(LEDGER_SOURCE, scfile)
    # Everything below counts as a delta against the file as copied. The real
    # system-changes.md carries what real runs put there - including a `draft`
    # r1 row that built a manuscript_r1.docx - so an absolute count reads
    # whichever of those happens to look like this build's own work.
    base = read(scfile)
    CODE = "- **Code:** `heading_not_the_journals`"
    ROW = "| `draft` | r1: built"
    env = dict(os.environ, SYSTEM_CHANGES_MD=scfile, PYTHONIOENCODING="utf-8")
    cli = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "manuscript.py"),
         "assemble", root, "--journal", "Langmuir", "--preset", "draft"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env)
    check("the CLI build succeeds", cli.returncode, 0, cli.stdout + cli.stderr)
    check("...and tells the user, in the run's own output",
          "engine issue" in cli.stdout and "system-changes.md" in cli.stdout,
          True, cli.stdout)
    logged = read(scfile)
    check("...having written the item", logged.count(CODE), base.count(CODE) + 1)
    check("...and a run history row for the round it built",
          logged.count(ROW + " manuscript_r1.docx"),
          base.count(ROW + " manuscript_r1.docx") + 1)
    again = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "manuscript.py"),
         "assemble", root, "--journal", "Langmuir", "--preset", "draft"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env)
    check("a rebuild adds no second item", again.returncode, 0)
    check("...it is the same item, seen twice",
          read(scfile).count(CODE), base.count(CODE) + 1)
    check("...and the same row",
          read(scfile).count(ROW), base.count(ROW) + 1)
    check("...and the user is told it was seen before",
          "seen again" in again.stdout, True, again.stdout)

    # Item 13: the same defect, but the item has since been closed. A
    # regression must come back to the open list, not be bumped where it lies
    # under a heading that reads "Closed".
    closed = read(scfile)
    span = ms._sc_span(closed, "heading_not_the_journals")
    check("the closed-item span reports which section it is in",
          span[2], False)
    body = closed[span[0]:span[1]].replace("- **Status:** OPEN",
                                           "- **Status:** VERIFIED FIXED")
    moved = closed[:span[0]] + closed[span[1]:]
    at = moved.index(ms.RUN_HISTORY_HEAD)
    moved = moved[:at] + body.strip("\n") + "\n\n" + moved[at:]
    write(sub_tmp, "system-changes.md", moved)
    check("...and finds it again once it is below Closed",
          ms._sc_span(read(scfile), "heading_not_the_journals")[2], True)
    back = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "manuscript.py"),
         "assemble", root, "--journal", "Langmuir", "--preset", "draft"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env)
    check("a build that sees a closed defect again succeeds", back.returncode,
          0, back.stdout + back.stderr)
    check("...and says REOPENED, not seen again",
          "REOPENED" in back.stdout, True, back.stdout)
    reopened = read(scfile)
    check("...leaving exactly one copy of the item",
          reopened.count(CODE), base.count(CODE) + 1)
    span = ms._sc_span(reopened, "heading_not_the_journals")
    check("...back above ## Closed items", span[2], False)
    item = reopened[span[0]:span[1]]
    check("...with Status OPEN again",
          item.split("\n- **Status:**")[1].lstrip().startswith("OPEN"), True,
          item[:400])
    check("...and the failed fix still on the record",
          "VERIFIED FIXED" in item, True, item[:400])

    # log-issue: Claude's own findings, in the same shape and under the same
    # rule about the verify-by.
    hand = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "manuscript.py"),
         "log-issue", root, "--journal", "Langmuir", "--round", "1",
         "--code", "a_thing_claude_saw", "--detail", "what happened"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env)
    check("log-issue refuses an item with no verify-by", hand.returncode, 2)
    hand = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "manuscript.py"),
         "log-issue", root, "--journal", "Langmuir", "--round", "1",
         "--code", "a_thing_claude_saw", "--detail", "what happened",
         "--title", "A thing Claude saw", "--wanted", "not that",
         "--verify", "look again next round"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env)
    check("...and accepts one written in full", hand.returncode, 0,
          hand.stdout + hand.stderr)
    check("...filed as a person's finding, in the same shape",
          "- **Verify by:** look again next round" in read(scfile), True)
    codes = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "manuscript.py"),
         "log-issue", "--list-codes"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env)
    check("--list-codes names every code the engine logs itself",
          all(c in codes.stdout for c in ms.ENGINE_ISSUES), True)


def test_truncated_order(tmp: str) -> None:
    section("An order that wraps, one that never closes, and one that drops "
            "a section")

    if not shutil.which("pandoc"):
        skip("every truncated-order check", "pandoc is not on PATH")
        return

    sub_tmp = os.path.join(tmp, "wrapped")
    os.makedirs(sub_tmp, exist_ok=True)
    root = build_project(sub_tmp)
    ms.init(root, "Langmuir")
    reqp = os.path.join(root, "drafts", "Langmuir", "journal_requirements",
                        "requirements.yml")
    original = read(reqp)
    rel = os.path.relpath(reqp, root)
    DEFAULT = ("section_order: [title_abstract, introduction, methods, "
               "results, discussion]")

    write(root, rel, original.replace(
        DEFAULT,
        "section_order: [Title, Author list, Abstract, Introduction,\n"
        "                  Experimental Section, Results and Discussion,\n"
        "                  References]"))
    res = ms.assemble(root, "Langmuir")
    check("a wrapped order builds", res["errors"], [])
    check("...with every section it names",
          [x for x in ("Experimental Section", "Results and Discussion",
                       "References") if x not in res["sections"]], [])

    # The other half of gate 0. A section that fell out of the order is not
    # reported as missing, because nothing is looking for it.
    write(root, rel, original.replace(
        DEFAULT,
        "section_order: [Title, Author list, Abstract, Introduction,\n"
        "                  Experimental Section, References]"))
    res = ms.assemble(root, "Langmuir")
    check("an order that drops a written section still builds",
          res["errors"], [])
    check("...and says which files it left out",
          sorted(st for st in ("results", "discussion")
                 if any(ms.stpfx(root) + "%s.md is written" % st in w
                        for w in res["warnings"])),
          ["discussion", "results"],
          "the truncated order built 2763 words with three sections gone and "
          "warned about none of them")

    # Unterminated: the inputs are not the ones on the page, so nothing is
    # written from them.
    write(root, rel, original.replace(
        DEFAULT, "section_order: [Title, Author list, Abstract, Introduction,"))
    res = ms.assemble(root, "Langmuir")
    check("an unterminated order refuses the build", bool(res["errors"]), True)
    check("...naming the key", "section_order" in res["errors"][0], True)
    check("...and writing nothing", res["output"], None)

    write(root, rel, original)


# ---------------------------------------------------------------------------
# The journal names its own sections, and the build has to answer to them
# ---------------------------------------------------------------------------

# The order Langmuir's guidelines actually give, captured verbatim. Nine of
# these ten names matched no file on disk, and the build came out holding the
# introduction alone at exit code 0.
LANGMUIR_ORDER = ["Title", "Author list", "Abstract", "Introduction",
                  "Experimental Section", "Results and Discussion",
                  "Conclusions", "Supporting Information", "Acknowledgment",
                  "References", "TOC/Abstract graphic"]


def test_section_names(tmp: str) -> None:
    section("A journal's own section names, and the files behind them")

    root = _scaffold(tmp, "names_case")
    if not root:
        skip("every section-name check", "scaffold failed")
        return
    write(root, "drafts/source_text/introduction.md", "# Introduction\n\nA.\n")
    write(root, "drafts/source_text/methods.md", "# Methods\n\nB.\n")
    write(root, "drafts/source_text/results.md", "# Results\n\nC.\n")
    write(root, "drafts/source_text/discussion.md", "# Discussion\n\nD.\n")
    write(root, "drafts/source_text/conclusions.md", "# Conclusions\n\nE.\n")

    entries, warns = ms.resolve_order(LANGMUIR_ORDER, root)
    by_name = {e["name"]: e for e in entries}
    check("every name in a real journal's order is known",
          warns, [],
          "an unrecognised name is read as a file stem, which is how nine of "
          "these ten resolved to nothing")
    check("all eleven resolve", len(entries), len(LANGMUIR_ORDER))
    check("the title block is metadata, not a file",
          by_name["Title"]["kind"], "front")
    check("ACS's Experimental Section is the scaffold's methods.md",
          by_name["Experimental Section"]["files"], ["methods"])
    check("...and is retitled to the journal's word for it",
          by_name["Experimental Section"]["retitle"], True)
    check("one journal heading over two scaffold files",
          by_name["Results and Discussion"]["files"],
          ["results", "discussion"],
          "ACS asks for them combined; the outline keeps them apart")
    check("end matter resolves to a statement, not to a file",
          (by_name["Supporting Information"]["kind"],
           by_name["Supporting Information"]["target"]),
          ("statement", "supporting_information"))
    check("Acknowledgment is one too",
          by_name["Acknowledgment"]["kind"], "statement")
    check("the bibliography is placed, not concatenated",
          by_name["References"]["kind"], "refs")
    check("a graphic named in the order is not prose",
          by_name["TOC/Abstract graphic"]["target"], "toc_graphic")

    # The alias spelling resolves, and used to print. `_sec_key()` normalizes
    # a name for resolution; the name was then put on the page verbatim, so a
    # section_order written in slugs gave a document with a literal
    # `experimental_section` heading and no complaint.
    got, w = ms.resolve_order(["experimental_section"], root)
    check("the alias spelling still resolves to the file",
          got[0]["files"], ["methods"])
    check("...but the heading is the canonical display name",
          got[0]["name"], "Experimental Section")
    check("...and it is warned about, not silently corrected",
          [x for x in w if "experimental_section" in x] != [], True)
    got, w = ms.resolve_order(["conclusions_and_outlook"], root)
    check("a lowercase alias is title-cased with its small words lowered",
          got[0]["name"], "Conclusions and Outlook")
    got, w = ms.resolve_order(["Experimental Section"], root)
    check("a display name is left exactly as the journal writes it",
          (got[0]["name"], w), ("Experimental Section", []))
    got, w = ms.resolve_order(["data_availability_statement"], root)
    check("an end-matter alias gets a display name too",
          (got[0]["name"], got[0]["kind"]),
          ("Data Availability Statement", "statement"))
    got, w = ms.resolve_order(["bibliography"], root)
    check("...and so does the bibliography",
          (got[0]["name"], got[0]["kind"]), ("Bibliography", "refs"))

    # The scaffold's own order is written in file stems, and none of them is a
    # heading - the file's own `# Methods` is. Warning on those would fire on
    # every default build.
    _, w = ms.resolve_order(list(ms.SECTION_FILES), root)
    check("the scaffold's own order is not an alias-spelling complaint", w, [],
          "its names resolve to their own files, so none of them is printed")

    # The same journal name written half a dozen ways.
    for spelling, want in (("Materials and Methods", "methods"),
                           ("EXPERIMENTAL PROCEDURES", "methods"),
                           ("Declaration of Competing Interest",
                            "conflict_of_interest"),
                           ("Funding Sources", "funding"),
                           ("Literature Cited", "references"),
                           ("Graphical Abstract", "toc_graphic")):
        got, w = ms.resolve_order([spelling], root)
        check("%r resolves to %s" % (spelling, want),
              (got[0]["target"], w), (want, []))

    ms.init(root, "Langmuir")
    reqp = os.path.join(root, "drafts", "Langmuir", "journal_requirements",
                        "requirements.yml")
    original = read(reqp)
    scaffolded = ("section_order: [title_abstract, introduction, methods, "
                  "results, discussion]")

    def with_order(names: list) -> dict:
        write(root, os.path.relpath(reqp, root),
              original.replace(scaffolded,
                               "section_order: [%s]" % ", ".join(names)))
        return ms.assemble(root, "Langmuir", dry_run=True)

    res = with_order(LANGMUIR_ORDER)
    check("a build against the journal's own order carries the body",
          [n for n in ("Introduction", "Experimental Section",
                       "Results and Discussion", "Conclusions")
           if n not in res["sections"]], [])
    check("...and reports the one section it could not fill",
          res["sections_missing"], ["TOC/Abstract graphic"],
          "the summary line used to name all ten whether or not they were "
          "in the file")

    # --- the partial-resolution refusal ----------------------------------
    os.remove(stfile(root, "methods.md"))
    res = with_order(LANGMUIR_ORDER)
    check("a journal section with no file behind it is refused",
          any("REFUSED" in e and "methods.md" in e for e in res["errors"]),
          True,
          "nine of ten names resolving to nothing cleared gate 0 because one "
          "of them did not")
    check("...and nothing is written", res["output"], None)
    check("...and the refusal names the section, not just the file",
          any("Experimental Section" in e for e in res["errors"]), True)

    write(root, "drafts/source_text/methods.md", "# Methods\n\nB.\n")
    write(root, os.path.relpath(reqp, root), original)


# ---------------------------------------------------------------------------
# The title block and the end matter
# ---------------------------------------------------------------------------

AUTHORS_MD = """\
# Authors and contributions

| # | name | affiliation(s) | ORCID | email |
|---|---|---|---|---|
| 1 | Ada Bell | Department of Chemistry, BYU, Provo, Utah 84602 | 0000-0002-1825-0097 | ab@example.edu |
| 2 | Cyd Doyle | Department of Chemistry, BYU, Provo, Utah 84602 |  | cd@example.edu |

Corresponding author: Cyd Doyle, cd@example.edu

## Competing interests

The authors declare no competing financial interest.

## Acknowledgements

We thank the microscopy facility for instrument time.
"""

TITLE_MD = """\
# Title

Tin doping halves the NO desorption barrier on Pt(100)

## Running title

Sn doping and NO desorption

## Abstract

The barrier falls from 90 to 45 kJ per mole on doping.

## Keywords

alloy, platinum, desorption
"""


def test_literature_landscape(tmp: str) -> None:
    section("draft-sections is given a view of the field before it writes")

    sub_tmp = os.path.join(tmp, "landscape")
    os.makedirs(sub_tmp, exist_ok=True)
    root = build_project(sub_tmp)
    ms.init(root, "Langmuir")

    path = os.path.join(root, "plan", "literature_landscape.md")
    check("init scaffolds the landscape file", os.path.isfile(path), True,
          "the sweep already happens, but it happens in idea-generation and "
          "nothing carried it into the writing engine")

    res = ms.landscape(root)
    check("the subject terms come from the paper, not from a person retyping",
          [t for t in ("chemoreceptor", "arrays", "thermophiles")
           if t not in res["terms"]], [], str(res["terms"]))
    check("an empty landscape cannot be drafted from", res["can_draft"], False)
    check("...and every question is reported unsourced",
          sorted({f["rule"] for f in res["findings"]}),
          ["landscape_question_unsourced"], str(res["findings"]))

    # Two of three answered, and one PMID not marked verified. Written from
    # the question list rather than by editing the scaffold's comments, so
    # the check does not depend on the template's line wrapping.
    heads = [h for _k, h, _w in ms.LANDSCAPE_QUESTIONS]

    def landscape_file(*answers: str) -> None:
        out = ["# Literature landscape", ""]
        for h, a in zip(heads, list(answers) + [""] * len(heads)):
            out += ["## " + h, "", a, ""]
        write(root, "plan/literature_landscape.md", "\n".join(out))

    landscape_file(
        "- Cryo-ET is the method of choice for in-situ arrays. "
        "[PMID 31234567, verified 2026-09-03]",
        "- Nobody has compared array order across growth temperature. "
        "[PMID 28901234]")
    res = ms.landscape(root)
    check("a PMID that is not marked verified is reported",
          any(f["rule"] == "landscape_unverified" for f in res["findings"]),
          True, str(res["findings"]))
    check("...and the third question is still unsourced",
          [f["rule"] for f in res["findings"]].count(
              "landscape_question_unsourced"), 1)
    check("...so the landscape still cannot be drafted from",
          res["can_draft"], False)

    landscape_file(
        "- Cryo-ET is the method of choice for in-situ arrays. "
        "[PMID 31234567, verified 2026-09-03]",
        "- Nobody has compared array order across growth temperature. "
        "[PMID 28901234, verified 2026-09-03]",
        "- A 2025 survey extends the range to 78 C. "
        "[PMID 39000001, verified 2026-09-03]")
    res = ms.landscape(root)
    check("three questions, all with verified PMIDs behind them",
          (res["state"], res["can_draft"], res["findings"]),
          ("present", True, []), str(res["findings"]))
    check("...and every PMID is collected", res["pmids"],
          ["28901234", "31234567", "39000001"])

    # `background` alone is no longer a citation.
    write(root, "plan/outline.md",
          "# Structure\n\n## Introduction\n\n"
          "- Arrays have been resolved in mesophiles. [background]\n"
          "- Cryo-ET is the method of choice. "
          "[background; PMID 31234567]\n\n# Notes\n\n-\n")
    ol = ms._prose(root, "outline")
    rules = [f["rule"] for f in ol["findings"]]
    check("`background` on its own is reported",
          rules.count("background_uncited"), 1,
          "it used to mean 'no citation needed', so a claim about the state "
          "of the field resolved clean with nothing behind it")
    check("...and `background` beside a PMID is not",
          [f["claim"] for f in ol["findings"]
           if f["rule"] == "background_uncited"],
          ["Arrays have been resolved in mesophiles."])


# The marker analysis.R writes into analysis.md. Built from manuscript.py's
# own constant rather than retyped: a test that hard-codes the marker passes
# while the engine and the R script disagree about it, which is the one way
# this contract can break silently.
MARKER_BLOCK = (
    "<!-- ------------------------------------------------------------------\n"
    "     " + ms.ANALYSIS_MARKER_HEAD + "\n"
    "     Rscript data/analysis/analysis.R rewrites everything ABOVE this\n"
    "     line on every run, and never touches anything below it.\n"
    "     --------------------------------------------------------------- -->\n")


def test_analysis_source(tmp: str) -> None:
    """analysis.md as the generated number source (spec
    analysis-and-front-matter 1).

    REWRITTEN, NOT EXTENDED. Every check in the old version of this section
    asserted that analysis.md was a hand-written lab note DENIED to
    stats-check. It is now generated numbers and stats-check is GIVEN it, so
    the old checks would have gone on passing while testing the opposite of
    what the engine does - and the denial they were really guarding moved to
    plan/README.md, which is where the checks moved with it.
    """
    section("data/analysis/analysis.md as the number source")

    sub = os.path.join(tmp, "analysis_src")
    os.makedirs(sub, exist_ok=True)
    root = build_project(sub)
    ms.init(root, "Langmuir")

    inv = ms.outline_sources(root)
    names = [s["name"] for s in inv["sources"]]
    check("it is in the inventory",
          "data/analysis/analysis.md" in names, True, names)
    row = [s for s in inv["sources"]
           if s["name"] == "data/analysis/analysis.md"][0]
    check("as a PRIMARY source", row["weight"], "primary")
    check("the two files are one file now - stats_output is gone",
          "data/analysis/analysis_stats_output.md" in names, False, names)

    # can_draft on a project whose ONLY content is this file. Numbers are
    # material: a project that has measured something and written nothing else
    # down still has something to draft from.
    bare = os.path.join(tmp, "analysis_only")
    os.makedirs(bare, exist_ok=True)
    bare_root = os.path.join(bare, "bare")
    subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "scaffold.py"),
         "scaffold", bare_root, "--title", "Bare", "--field", "chemistry"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    inv = ms.outline_sources(bare_root)
    check("a bare project cannot draft", inv["can_draft"], False,
          [s["name"] for s in inv["sources"] if s["present"]])

    apath = os.path.join(bare_root, "data", "analysis", "analysis.md")
    with open(apath, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# Analysis - Bare\n\n"
                 "## diameter_nm by species\n\n"
                 "| group | n | mean +/- SD |\n|---|---|---|\n"
                 "| thermophile | 12 | 14.2 +/- 1.1 |\n"
                 "| mesophile | 12 | 18.6 +/- 2.4 |\n\n"
                 "Test chosen: **Welch t-test** - Shapiro-Wilk not rejected.\n\n"
                 "| comparison | test | p (adj) |\n|---|---|---|\n"
                 "| thermophile vs mesophile | Welch t-test | 0.002 |\n\n"
                 + MARKER_BLOCK)
    inv = ms.outline_sources(bare_root)
    check("a project whose only content is analysis.md CAN draft",
          inv["can_draft"], True,
          [s["name"] for s in inv["sources"] if s["present"]])

    # The modules that may read it. stats-check is now among them: the file it
    # used to be denied held the hope in prose, and this one holds numbers.
    for mod in ("outline", "draft-sections"):
        res = ms.agent_brief(root, mod, "Langmuir")
        given = [g["name"] for g in res["given"]]
        check(f"{mod} is given analysis", "analysis" in given, True, given)
        check(f"{mod}'s prompt names the file",
              "analysis.md" in res["prompt"].split("YOU ARE DENIED")[0],
              True)

    res = ms.agent_brief(root, "stats-check", "Langmuir")
    given = [g["name"] for g in res["given"]]
    check("stats-check IS given analysis now", "analysis" in given, True,
          given)
    check("...and is not given a source name that no longer exists",
          "stats_output" in given, False, given)

    # THE DENIAL MOVED, IT DID NOT GO AWAY. plan/README.md is where "the
    # thermophile looks tighter, as expected" gets written down now, and a
    # blind check that reads it is not blind.
    denied = " ".join(d["path"] for d in res["denied"])
    check("plan/README.md is denied to stats-check",
          "plan/README.md" in denied, True, denied)
    check("and analysis.md is no longer in the denials",
          "analysis.md" in denied, False, denied)

    # THE LEAK THAT WAS THERE, AND STILL MATTERS: stats-check used to be given
    # `data/` whole. The reason changed - it is the raw measurements now, not
    # analysis.md - but a blind module browsing data/ is still not blind.
    check("stats-check is not given data/ as a directory",
          [g for g in res["given"] if g["path"].rstrip("/").endswith("data")],
          [])
    for g in res["given"]:
        inside = os.path.join(g["abs"], "README.md")
        if not os.path.isdir(g["abs"]):
            continue
        check(f"plan/README.md is not reachable under {g['name']}",
              os.path.abspath(g["abs"]) == os.path.abspath(
                  os.path.join(root, "plan")) and os.path.exists(inside),
              False)
    check("stats-check still gets what it needs to do its job",
          sorted(g["name"] for g in res["given"])
          == ["analysis", "data_contract", "raw_dir", "source_text"],
          True, sorted(g["name"] for g in res["given"]))


def test_analysis_regions(tmp: str) -> None:
    """The two regions of analysis.md, and `[must appear]` (spec 1.4, 1.8).

    THE UNMARKED CASE IS THE ONE THAT MATTERS. Every project that existed
    before this change carries a hand-written analysis.md with no marker in
    it, and reading such a file as "generated" would let a lab note satisfy a
    number check. It reads as ALL TAIL and NO generated region instead.
    """
    section("analysis.md regions and [must appear]")

    sub = os.path.join(tmp, "analysis_regions")
    os.makedirs(sub, exist_ok=True)
    root = build_project(sub)
    ms.init(root, "Langmuir")
    apath = os.path.join(root, "data", "analysis", "analysis.md")
    os.makedirs(os.path.dirname(apath), exist_ok=True)

    # (1) No file at all.
    if os.path.exists(apath):
        os.remove(apath)
    a = ms.analysis_md(root)
    check("no file: it says so", a["exists"], False)
    check("no file: nothing is claimed to be generated", a["generated"], "")
    check("no file: nothing is claimed to be kept", a["kept"], "")

    # (2) A generated file with a tail below the marker.
    with open(apath, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# Analysis - X\n\n## yield by catalyst\n\n"
                 "| comparison | p (adj) |\n|---|---|\n"
                 "| A vs B | 0.002 |\n\n"
                 + MARKER_BLOCK
                 + "\n## Added (writing-engine r2)\n"
                   "- mean particle diameter 14.2 nm (n = 40)  [must appear]\n")
    a = ms.analysis_md(root)
    check("marked file: it reads as run", a["run"], True)
    check("marked file: the generated half holds the tables",
          "0.002" in a["generated"] and "yield by catalyst" in a["generated"],
          True)
    check("marked file: the marker's own closing --> is not in the tail",
          "-->" in a["kept"], False, a["kept"][:120])
    check("marked file: the tail holds what was appended",
          "14.2 nm" in a["kept"], True, a["kept"][:120])
    check("marked file: the generated half does NOT hold the tail",
          "14.2 nm" in a["generated"], False)
    check("marked file: [must appear] is found in the tail",
          len(a["must_appear"]), 1)

    # (3) The migration case: a hand-written file with no marker.
    with open(apath, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# Analysis notes\n\n## What was measured\n\n"
                 "40 particles across three grids, diameters in ImageJ.\n")
    a = ms.analysis_md(root)
    check("unmarked file: it reads as NOT run", a["run"], False)
    check("unmarked file: nothing is treated as generated", a["generated"], "")
    check("unmarked file: every word of it is kept",
          "40 particles" in a["kept"] and "What was measured" in a["kept"],
          True)
    inv = ms.outline_sources(root)
    detail = " ".join(
        s["detail"] for s in inv["sources"]
        if s["name"] == "data/analysis/analysis.md")
    check("unmarked file: the inventory says nothing in it is lost",
          "Nothing in it is lost" in detail, True, detail)

    # (4) [must appear] against a draft. The VALUES are matched, never the
    # line: a marked table row is pipes and headings no draft will contain.
    with open(apath, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# Analysis - X\n\n"
                 "| A vs B | Welch t-test | p = 0.002 | g = 1.8 |  [must appear]\n"
                 "| A vs C | Welch t-test | p = 0.31 | g = 0.2 |\n\n"
                 + MARKER_BLOCK)
    vals = ms.must_appear_values(root)
    check("one line is marked", len(vals), 1)
    check("and its values are pulled out of the row",
          "0.002" in vals[0]["values"], True, vals[0]["values"])

    st = ms.source_text_dir(root)
    os.makedirs(st, exist_ok=True)
    with open(os.path.join(st, "results.md"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write("# Results\n\nThe difference was significant.\n")
    comp = ms.completeness(root, "Langmuir")
    outstanding = " ".join(
        i["detail"] for i in comp["missing"] if i["area"] == "analysis")
    check("a marked value the draft does not carry is reported",
          "must appear" in outstanding, True, outstanding)
    check("...as a gap and never as a block",
          [i for i in comp["missing"]
           if i["area"] == "analysis" and "must appear" in i["detail"]
           and i["severity"] != "gap"],
          [])

    with open(os.path.join(st, "results.md"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write("# Results\n\nA differed from B (p = 0.002, g = 1.8).\n")
    comp = ms.completeness(root, "Langmuir")
    outstanding = " ".join(
        i["detail"] for i in comp["missing"] if i["area"] == "analysis")
    check("...and is not reported once the draft carries it",
          "must appear" in outstanding, False, outstanding)


def test_front_matter_paths(tmp: str) -> None:
    """authors.md and affiliations.md, in all three places they may be.

    `plan/author_information/` first, `plan/` second, the round folder third.
    Both fallbacks are READ-ONLY: a project scaffolded before either move
    keeps working, the path that was READ is the path REPORTED, and nothing
    here moves a user's file for them.
    """
    section("front matter resolves to author_information/, plan/, the round")

    sub = os.path.join(tmp, "front_matter")
    os.makedirs(sub, exist_ok=True)
    root = build_project(sub)
    ms.init(root, "Langmuir")

    plan = os.path.join(root, "plan")
    os.makedirs(plan, exist_ok=True)
    info = ms.author_info_dir(root)
    os.makedirs(info, exist_ok=True)
    st = ms.source_text_dir(root)
    os.makedirs(st, exist_ok=True)

    for f in (os.path.join(info, "authors.md"),
              os.path.join(info, "affiliations.md"),
              os.path.join(plan, "authors.md"),
              os.path.join(plan, "affiliations.md"),
              os.path.join(st, "authors.md"),
              os.path.join(st, "affiliations_and_funding.md")):
        if os.path.exists(f):
            os.remove(f)

    # (1) None exists: the canonical path is named, so a message about a
    # missing file points at where to create it.
    check("with no file anywhere, author_information/ is named",
          ms.fm_rel(root, "authors"), "plan/author_information/authors.md")
    check("...and for affiliations too",
          ms.fm_rel(root, "affiliations"),
          "plan/author_information/affiliations.md")

    # (2) Only the old location: it is found, and it is what gets reported.
    with open(os.path.join(st, "authors.md"), "w", encoding="utf-8") as fh:
        fh.write("# Authors\n\n## AB - Ada Bell\n")
    rel = ms.fm_rel(root, "authors")
    check("a pre-move project is still read", "authors.md" in rel, True, rel)
    check("...from the round folder, and reported from there",
          rel.endswith("/authors.md") and "plan/" not in rel, True, rel)
    check("...and the content actually arrives",
          ms._authors_initials(root).get("AB"), "Ada Bell")

    # (3) Both exist: plan/ wins over the round folder, and nothing was moved
    # or deleted.
    with open(os.path.join(plan, "authors.md"), "w", encoding="utf-8") as fh:
        fh.write("# Authors\n\n## CD - Cyd Doyle\n")
    check("plan/ wins over the round folder",
          ms.fm_rel(root, "authors"), "plan/authors.md")
    check("...and the old file is still on disk, untouched",
          os.path.isfile(os.path.join(st, "authors.md")), True,
          "the fallback is read-only; nothing moves a user's file")
    check("...and the content read is plan/'s",
          ms._authors_initials(root).get("CD"), "Cyd Doyle")

    # (3b) All three exist: author_information/ wins over both, and both
    # older files stay where their owner put them.
    with open(os.path.join(info, "authors.md"), "w", encoding="utf-8") as fh:
        fh.write("# Authors\n\n## EF - Eli Frost\n")
    check("author_information/ wins over all of them",
          ms.fm_rel(root, "authors"), "plan/author_information/authors.md")
    check("...and the content read is the folder's",
          ms._authors_initials(root).get("EF"), "Eli Frost")
    check("...with both older files still on disk",
          (os.path.isfile(os.path.join(plan, "authors.md")),
           os.path.isfile(os.path.join(st, "authors.md"))), (True, True),
          "two moves deep, and neither one moves a user's file")

    # (3c) A BLANK file in the best place does not outrank a filled one in a
    # worse place. This is the 2026-09-08 rule, and it now has to survive one
    # more layer: re-running setup-project-directory on an old project writes
    # a blank stub into the folder, and a rule of "the folder wins because it
    # exists" would put that stub in front of an author list somebody filled
    # in three rounds ago.
    stub = read(os.path.join(ROOT, "tools", "project_template", "plan",
                             "author_information", "authors.md"))
    with open(os.path.join(info, "authors.md"), "w", encoding="utf-8") as fh:
        fh.write(stub)
    check("a blank stub in the best place loses to a filled file",
          ms.fm_rel(root, "authors"), "plan/authors.md")
    check("...and the filled file is what is read",
          ms._authors_initials(root).get("CD"), "Cyd Doyle")
    os.remove(os.path.join(info, "authors.md"))

    # (4) affiliations.md has been renamed twice, so the fallback is two deep.
    with open(os.path.join(st, "affiliations_and_funding.md"), "w",
              encoding="utf-8") as fh:
        fh.write("# Affiliations\n\n## Affiliations\n\n"
                 "| key | affiliation |\n|---|---|\n| A | BYU, Provo UT |\n")
    rel = ms.fm_rel(root, "affiliations")
    check("the pre-split name is still found",
          rel.endswith("affiliations_and_funding.md"), True, rel)
    check("...and its table parses",
          ms.author_record(root)["affiliations"].get("A"), "BYU, Provo UT")


def test_source_weights(tmp: str) -> None:
    section("source_weights - what the round drafted from (spec 16)")

    sub = os.path.join(tmp, "weights")
    os.makedirs(sub, exist_ok=True)
    root = build_project(sub)
    ms.init(root, "Langmuir")

    # (1) A weight round-trips through config and appears in plan.
    res = ms.configure(root, "Langmuir", weight=["captions=primary"])
    check("a weight is written", res["errors"], [])
    check("and recorded as a change",
          any(c["key"] == "source_weights" and c["action"] == "set"
              for c in res["changes"]), True, res["changes"])
    cfg = ms.read_config(os.path.join(ms.journal_dir(root, "Langmuir"),
                                      "writing_config.yml"))
    check("it round-trips through the parser",
          cfg["maps"]["source_weights"].get("captions"), "primary")
    pl = ms.plan(root, "Langmuir", "draft")
    row = [r for r in pl["sources"]["rows"] if r["key"] == "captions"][0]
    check("and reaches the plan line", row["weight"], "primary")

    # (2) An unknown source name is reported and does not fail the write.
    res = ms.configure(root, "Langmuir", weight=["captoins=primary"])
    check("a typo in a source name is refused",
          any("not a drafting source" in e for e in res["errors"]), True,
          res["errors"])
    check("and the config is still readable", bool(res.get("config")), True)
    hand = os.path.join(ms.journal_dir(root, "Langmuir"),
                        "writing_config.yml")
    text = ms._read(hand)
    ms._write(hand, text.replace("source_weights:",
                                 "source_weights:\n  flotas: primary"))
    cfg = ms.read_config(hand)
    check("a hand-edited unknown name is reported by the reader",
          any("flotas" in i for i in cfg["invalid"]), True, cfg["invalid"])
    check("and dropped rather than passed through",
          "flotas" in cfg["maps"]["source_weights"], False)
    ms._write(hand, text)

    # A bad LEVEL is reported the same way. The EXISTING entry is edited
    # rather than a second one added: duplicate keys mean the later value wins
    # the parse, so adding one leaves the bad value gone before validation
    # sees it - which is what the first version of this case did.
    ms._write(hand, re.sub(r"^  captions:.*$", "  captions: hugely", text,
                           count=1, flags=re.M))
    cfg = ms.read_config(hand)
    check("a bad level is reported",
          any("hugely" in i for i in cfg["invalid"]), True, cfg["invalid"])
    check("and that source is back at normal",
          ms.source_weights(cfg)["captions"], "normal")
    ms._write(hand, text)

    # (3) `ignore` removes the file from agent-brief's given list, and says so.
    ms.configure(root, "Langmuir", weight=["captions=ignore"])
    br = ms.agent_brief(root, "draft-sections", "Langmuir")
    check("ignore removes the file from the given list",
          "captions" in [g["name"] for g in br["given"]], False,
          [g["name"] for g in br["given"]])
    check("and says so in the denied list, so it is never silent",
          any("captions.md" in d["path"] for d in br["denied"]), True,
          [d["path"] for d in br["denied"]])
    check("naming the config rather than a bias rule",
          any("your config" in d["why"] for d in br["denied"]), True)
    pl = ms.plan(root, "Langmuir", "draft")
    check("and the plan line lists it as NOT READ",
          any("captions.md" in i for i in pl["sources"]["ignored"]), True,
          pl["sources"]["ignored"])

    # (4) A WEIGHT CANNOT ADD A FILE TO A DENIED LIST. The isolation contract
    # is not a dial, and this is the assertion that says so for every level.
    for level in ms.SOURCE_WEIGHT_LEVELS:
        ms.configure(root, "Langmuir", weight=[f"readme={level}"])
        br = ms.agent_brief(root, "stats-check", "Langmuir")
        given = [g["name"] for g in br["given"]]
        check(f"readme={level}: stats-check still does not get the README",
              "readme" in given, False, given)
        check(f"readme={level}: its denials still name the README",
              any("README" in d["path"] for d in br["denied"]), True)
    ms.configure(root, "Langmuir", forget_weight=["readme"])
    ms.configure(root, "Langmuir", forget_weight=["captions"])

    # (5) EMPTY, MISSING and out-of-scope are three different words.
    #
    # `rough_draft` is the exemplar for EMPTY rather than `analysis`, which
    # used to be it: analysis.md is generated now and is NOT scaffolded, so it
    # is legitimately MISSING on a fresh project. Losing this distinction
    # would be losing the whole point of the check - EMPTY needs writing in,
    # MISSING needs creating first, and they are different jobs.
    pl = ms.plan(root, "Langmuir", "draft")
    states = {r["key"]: r["state"] for r in pl["sources"]["rows"]}
    check("a file that exists and says nothing is EMPTY",
          states.get("rough_draft"), "EMPTY")
    check("one that was never created is MISSING",
          states.get("provenance"), "MISSING")
    check("and they are different words",
          states.get("rough_draft") != states.get("provenance"), True)
    check("an ungenerated analysis.md is MISSING, not EMPTY",
          states.get("analysis"), "MISSING",
          "it is generated by analysis.R and never scaffolded")

    ms.configure(root, "Langmuir", manages=["none"])
    pl = ms.plan(root, "Langmuir", "draft")
    states = {r["key"]: r["state"] for r in pl["sources"]["rows"]}
    check("an out-of-scope source is neither EMPTY nor MISSING",
          states.get("analysis"), "out of scope")
    check("and is not counted as a gap",
          [g["key"] for g in pl["sources"]["primary_gaps"]
           if g["key"] == "analysis"], [])
    ms.configure(root, "Langmuir",
                 manages=["outline", "floats", "analysis"])

    # (6) THE COMMON CASE MUST NOT GET NOISIER. Everything filled, no weight
    # set: one line, not eleven.
    full = os.path.join(tmp, "weights_full")
    os.makedirs(full, exist_ok=True)
    froot = build_project(full)
    ms.init(froot, "Langmuir")
    for rel, body in (
            ("plan/README.md", "# Plan\n\nThe question is real.\n"),
            ("data/analysis/analysis.md", "# Analysis\n\nMeasured 40.\n"),
            ("plan/literature_landscape.md", "# Landscape\n\nField does X.\n"),
            ("plan/floats/float_provenance.json", "{}\n")):
        p = os.path.join(froot, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
    block = ms.drafting_sources(
        froot, ms.read_config(os.path.join(ms.journal_dir(froot, "Langmuir"),
                                           "writing_config.yml")),
        list(ms.MANAGED_SCOPES), ["draft-sections"])
    check("with nothing weighted, no row is marked as set by the user",
          [r["key"] for r in block["rows"] if r["set"]], [])
    # The effective weight is a second fact and they came apart when
    # `rough_draft` arrived: it is `primary` by default (20.5), so the row
    # carries a weight nobody configured, and only `set` can say so.
    check("...while rough_draft is primary anyway, unconfigured",
          [r["weight"] for r in block["rows"] if r["key"] == "rough_draft"],
          ["primary"])
    check("...and every other row is still normal",
          [r["key"] for r in block["rows"]
           if r["weight"] != "normal" and r["key"] != "rough_draft"], [])

    # And a module that writes no prose gets no sources block at all - the
    # block is about what the PROSE was built from.
    empty = ms.drafting_sources(froot, {"maps": {}},
                                list(ms.MANAGED_SCOPES), ["citation-check"])
    check("a non-drafting round gets no sources block", empty["rows"], [])


def _set_ai(root: str, journal: str, **fields) -> None:
    """Rewrite the ai_disclosure block of requirements.yml.

    SCOPED TO THE BLOCK, and that is not fussiness: `requirements.yml` carries
    two `required:` lines - one in another block entirely and
    `ai_disclosure.required` further down - so a `count=1` substitution over
    the whole file edits the wrong one, and every case here that thought it was
    setting `required: no` was still testing `unknown`.
    """
    path = os.path.join(ms.journal_dir(root, journal),
                        "journal_requirements", "requirements.yml")
    text = ms._read(path)
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if ln.startswith("ai_disclosure:")), -1)
    if start < 0:
        raise AssertionError("no ai_disclosure: block in requirements.yml")
    end = start + 1
    while end < len(lines) and (not lines[end].strip()
                                or lines[end].startswith((" ", "\t"))):
        end += 1
    block = lines[start:end]
    for key, value in fields.items():
        pat = re.compile(r"^(\s*)%s:.*$" % re.escape(key))
        for i, ln in enumerate(block):
            m = pat.match(ln)
            if m:
                block[i] = "%s%s: %s" % (m.group(1), key, value)
                break
        else:
            raise AssertionError("no %s: in the ai_disclosure block" % key)
    ms._write(path, "\n".join(lines[:start] + block + lines[end:]) + "\n")


def test_ai_disclosure(tmp: str) -> None:
    section("AI-use disclosure (spec 17)")

    sub = os.path.join(tmp, "aidisc")
    os.makedirs(sub, exist_ok=True)
    root = build_project(sub)
    ms.init(root, "Langmuir")

    # (1) Every alias resolves to the ONE canonical statement.
    aliases = ["declaration_of_generative_ai",
               "declaration_of_generative_ai_and_ai_assisted_technologies",
               "declaration_of_ai_use", "ai_disclosure", "use_of_ai",
               "generative_ai_statement",
               "artificial_intelligence_disclosure",
               "ai_assisted_technologies"]
    for alias in aliases:
        check(f"{alias} resolves to the canonical statement",
              ms.SECTION_ALIASES.get(alias), ("statement", "ai_disclosure"))
    check("all eight resolve to exactly one pair",
          len({ms.SECTION_ALIASES[a] for a in aliases}), 1)

    # (2) The heading printed is the journal's own, verbatim; the canonical
    # name never is. Journals are specific about this one.
    block = ms.ai_disclosure({
        "ai_disclosure.required": "yes",
        "ai_disclosure.in_paper": "statement",
        "ai_disclosure.in_paper_heading":
            "Declaration of generative AI and AI-assisted technologies in "
            "the writing process",
        "ai_disclosure.scope": "any use of generative AI in the writing",
        "ai_disclosure.source": "https://example.org/guide",
        "ai_disclosure.read_on": "2026-09-07"})
    stub = ms.ai_disclosure_stub("Langmuir", block)
    check("the stub's heading is the journal's, verbatim",
          stub.splitlines()[0],
          "# Declaration of generative AI and AI-assisted technologies in "
          "the writing process")
    check("and the canonical key is never printed as the heading",
          "ai_disclosure" in stub.splitlines()[0], False,
          stub.splitlines()[0])
    check("the journal's own scope text is quoted above the blanks",
          "any use of generative AI in the writing" in stub, True)

    # (3) The stub is ALL flags - that is the gate.
    body = re.sub(r"<!--.*?-->", "", stub, flags=re.DOTALL)
    prose = [ln for ln in body.splitlines()
             if ln.strip() and not ln.startswith(("#", ">", "<!--"))]
    check("every prose line in the stub is a flag",
          [ln for ln in prose if "**[FLAG:" not in ln], [])
    check("and there are three of them", len(prose), 3)
    check("...which are AI_STUB_BLANKS, and not a second copy of them",
          [" ".join(ln.split()) for ln in prose],
          [" ".join(b.split()) for b in ms.AI_STUB_BLANKS])
    check("the blank stub says WHY it is blank - nobody read the policy",
          "NOBODY HAS READ" in stub, True)
    check("and the default statement is NOT in it",
          ms.AI_DEFAULT_STATEMENT in stub, False,
          "17.2: no plausible default against an unread requirement")

    # (4) required: unknown reaches format-check's undetermined list and
    #     fails the checklist row. "nobody looked" is not "not required".
    res = ms.ai_disclosure_cmd(root, "Langmuir")
    check("unknown is reported as unknown", res["block"]["required"],
          "unknown")
    check("and warned about",
          any("nobody has read" in w for w in res["warnings"]), True,
          res["warnings"])
    rows = ms._ai_checklist_rows("Langmuir", res["block"])
    check("the checklist row is UNTICKED on unknown",
          rows[0].startswith("- [ ]"), True, rows)
    check("and carries a flag, so the package refuses",
          "**[FLAG:" in rows[0], True, rows)
    check("and says unknown is not the same as not required",
          "not the same" in rows[0], True, rows)

    ck = ms._checklist_md(root, "Langmuir",
                          {"ai_disclosure.required": "unknown"},
                          os.path.join(ms.journal_dir(root, "Langmuir"),
                                       "journal_requirements",
                                       "requirements.yml"),
                          "")
    check("the checklist file carries the ai disclosure section",
          "## ai disclosure" in ck, True)

    # (5) required: no writes the negative row with its date and source, and
    #     writes no section and no submission file.
    _set_ai(root, "Langmuir", required="no", in_paper="no",
            in_submission="no", source='"https://example.org/policy"',
            read_on="2026-09-07")
    res = ms.ai_disclosure_cmd(root, "Langmuir")
    check("required: no is determined", res["block"]["determined"], True)
    check("no section is created",
          [a for a in res["actions"] if a["action"] == "create"], [])
    check("and it says why",
          any("requires no declaration" in a["detail"]
              for a in res["actions"]), True, res["actions"])
    rows = ms._ai_checklist_rows("Langmuir", res["block"])
    check("the negative row is ticked", rows[0].startswith("- [x]"), True,
          rows)
    check("and carries the date it was read", "2026-09-07" in rows[0], True,
          rows)
    check("and the source", "example.org/policy" in rows[0], True, rows)
    check("and no flag", "**[FLAG:" in rows[0], False, rows)

    pk = ms.submission_package(root, "Langmuir", dry_run=True)
    names = [w["path"] for w in pk["written"]]
    check("no ai_disclosure file is in the package",
          [n for n in names if "ai_disclosure" in n], [])
    check("and the package is still the five files", len(names), 5, names)

    # (6) required: yes, in a separate form -> the sixth file appears.
    _set_ai(root, "Langmuir", required="yes", in_paper="statement",
            in_submission="separate_form")
    pk = ms.submission_package(root, "Langmuir", dry_run=True)
    names = [w["path"] for w in pk["written"]]
    check("the sixth file appears", len(names), 6, names)
    check("named for the round like every other one",
          any(re.search(r"ai_disclosure_r\d+\.md$", n) for n in names), True,
          names)

    # (7) THE GATE. The stub is flags, so submission-package is NOT sendable.
    res = ms.ai_disclosure_cmd(root, "Langmuir")
    stub_path = os.path.join(ms.source_text_dir(root), "ai_disclosure.md")
    check("the stub was written", os.path.isfile(stub_path), True)
    pk = ms.submission_package(root, "Langmuir", dry_run=True)
    check("the package counts the flags it cannot send",
          pk["flags"] > 0, True, pk["flags"])
    ai_row = [w for w in pk["written"] if "ai_disclosure" in w["path"]][0]
    check("and the ai file is one of the flagged ones", ai_row["flags"] > 0,
          True, ai_row)

    # And once the author has written it, the file carries THEIR words.
    ms._write(stub_path,
              "# Declaration of Generative AI Use\n\n"
              "The authors used an AI assistant for language editing of the "
              "Discussion. All content is the authors' own and the authors "
              "take full responsibility for the published work.\n")
    pk = ms.submission_package(root, "Langmuir", dry_run=True)
    ai_row = [w for w in pk["written"] if "ai_disclosure" in w["path"]][0]
    check("with a written statement the ai file carries no flag",
          ai_row["flags"], 0, ai_row)
    body = ms._ai_submission_md(root, "Langmuir",
                                ms.ai_disclosure_cmd(root, "Langmuir")["block"],
                                1)
    check("the submission file quotes the author's own words",
          "language editing of the Discussion" in body, True)
    check("and says the engine composed none of it",
          "Nothing here is composed by the engine" in body, True)

    # (8) cover_letter puts it IN the letter and writes no separate file.
    _set_ai(root, "Langmuir", in_submission="cover_letter")
    pk = ms.submission_package(root, "Langmuir", dry_run=True)
    names = [w["path"] for w in pk["written"]]
    check("no separate file when the journal wants it in the letter",
          [n for n in names if "ai_disclosure" in n], [],
          "two files that must agree is how they come to disagree")
    letter = ms._cover_letter_md(
        root, "Langmuir",
        {"ai_disclosure.required": "yes",
         "ai_disclosure.in_submission": "cover_letter"},
        ms.parse_title_abstract(ms.source_text(root, "title_abstract.md")),
        ms.author_record(root), 1, "")
    check("the letter carries the declaration",
          "language editing of the Discussion" in letter, True)

    # (9) --from-ledger writes NOTHING that is not in the ledger. The
    #     fabrication case, and the one worth writing first.
    fresh = os.path.join(tmp, "aidisc_ledger")
    os.makedirs(fresh, exist_ok=True)
    froot = build_project(fresh)
    ms.init(froot, "Langmuir")
    led = ms.ai_disclosure_from_ledger(froot, "Langmuir")
    check("with no ledger, no lines are written", led["lines"], [])
    check("and it says so rather than summarising a typical run",
          any("not a record of what happened here" in n
              for n in led["notes"]), True, led["notes"])
    res = ms.ai_disclosure_cmd(froot, "Langmuir", from_ledger=True)
    written = ms._read(os.path.join(ms.source_text_dir(froot),
                                    "ai_disclosure.md"))
    # The MARKER, not the phrase: the stub's own comment tells the reader that
    # `--from-ledger` "fills the blanks below from the run ledger", so
    # searching for that phrase matches the stub whether a block was appended
    # or not.
    check("and the stub gains no ledger block",
          "<!-- from the run ledger," in written, False)
    check("the refusal is reported as a warning",
          any("the ledger has nothing in it" in w for w in res["warnings"]),
          True, res["warnings"])

    # Now give it a real ledger and check every line names its round.
    jdir = ms.journal_dir(froot, "Langmuir")
    ms._log_run(jdir, "**run r1-x opened** - draft on LANGMUIR r1")
    ms._log_run(jdir, "`draft-sections` / **results** -> done")
    ms._log_run(jdir, "`draft-sections` -> **done**")
    ms._log_run(jdir, "`citation-check` -> **done**")
    ms._log_run(jdir, "`stats-check` -> **skipped**")
    led = ms.ai_disclosure_from_ledger(froot, "Langmuir")
    check("a real ledger yields lines", bool(led["lines"]), True, led)
    check("every line names its round",
          [ln for ln in led["lines"]
           if not re.match(r"^r\d+:", ln)
           and not ln.startswith("Every reference")], [])
    joined = " ".join(led["lines"])
    check("a module that ran is named", "draft-sections" in joined, True,
          joined)
    check("a module that was SKIPPED is not claimed",
          "stats-check" in joined, False, joined)
    check("the citation claim appears only because the check ran",
          "verified against the literature indexes" in joined, True)

    res = ms.ai_disclosure_cmd(froot, "Langmuir", from_ledger=True)
    written = ms._read(os.path.join(ms.source_text_dir(froot),
                                    "ai_disclosure.md"))
    check("the ledger block is appended",
          "<!-- from the run ledger," in written, True)
    check("and it says these are facts about modules, not a declaration",
          "what to declare from them is yours" in written.lower(), True)
    check("the author's flags are still there, so the gate still holds",
          "**[FLAG: author]**" in written, True)
    again = ms.ai_disclosure_cmd(froot, "Langmuir", from_ledger=True)
    check("a second --from-ledger does not duplicate the block",
          ms._read(os.path.join(ms.source_text_dir(froot),
                                "ai_disclosure.md")).count(
              "<!-- from the run ledger,"), 1, again["actions"])
    check("...and says the block is already there",
          any(a["detail"] == "the ledger block is already there"
              for a in again["actions"]), True, again["actions"])

    # ---------------------------------------------------------------- 17.4
    # THE DRAFT. Decided 2026-09-21: the engine drafts the statement and the
    # user edits it. The order below is 17.7's own, and it starts with the
    # fabrication case above because that is the one that must not regress
    # when a default statement is introduced beside it.

    # (10) --from-ledger APPENDS to the default and never replaces it. The
    #      same fabrication rule, now with something to fabricate over.
    dr = os.path.join(tmp, "aidisc_draft")
    os.makedirs(dr, exist_ok=True)
    droot = build_project(dr)
    ms.init(droot, "Langmuir")
    dpath = os.path.join(ms.source_text_dir(droot), "ai_disclosure.md")

    # (11) --draft REFUSES while required is unknown (17.2). A drafted
    #      declaration under an unread requirement is a statement answering a
    #      question nobody asked.
    ms.ai_disclosure_cmd(droot, "Langmuir")      # the blanks, as ever
    before = ms._read(dpath)
    check("the blanks were scaffolded", len(ms.ai_stub_blank_lines(before)), 3)
    check("an unknown journal scaffolds the BLANKS, not the draft",
          ms.AI_DEFAULT_STATEMENT in before, False)
    res = ms.ai_disclosure_cmd(droot, "Langmuir", draft=True)
    check("--draft refuses on required: unknown", bool(res["errors"]), True,
          res["errors"])
    check("and says what to read", "Read the journal's policy" in
          " ".join(res["errors"]), True, res["errors"])
    check("and writes nothing", ms._read(dpath), before)

    # Now the journal has actually been read.
    _set_ai(droot, "Langmuir", required="yes", in_paper="statement",
            in_paper_heading='"Declaration of generative AI in the writing '
                             'process"',
            in_submission="cover_letter",
            scope='"any use of generative AI in the writing"',
            source='"https://example.org/ai"', read_on="2026-09-21")

    res = ms.ai_disclosure_cmd(droot, "Langmuir", draft=True)
    check("--draft upgrades the untouched blanks",
          [a["action"] for a in res["actions"] if a["action"] == "update"],
          ["update"], res["actions"])
    body = ms._read(dpath)

    # (12) The default statement, VERBATIM, under ONE **[FLAG: author]**.
    check("the default statement is written verbatim",
          ms.AI_DEFAULT_STATEMENT in body, True)
    check("wrapped in exactly one **[FLAG: author]** of its own",
          [ln for ln in body.splitlines()
           if ln.strip().startswith("**[FLAG: author]**")],
          [ms.AI_DRAFT_FLAG])
    check("and none of the three blanks survive",
          ms.ai_stub_blank_lines(body), [])
    check("the journal's own question is still quoted above it",
          "any use of generative AI in the writing" in body, True)
    check("and the heading is the journal's, verbatim",
          body.splitlines()[0],
          "# Declaration of generative AI in the writing process")

    # (13) THE GATE IS UNCHANGED. A filled draft refuses exactly as a blank
    #      stub did - the flag gates on its own presence, not on whether the
    #      text inside it is a blank.
    pk = ms.submission_package(droot, "Langmuir", dry_run=True)
    check("submission-package still refuses on a filled draft",
          pk["flags"] > 0, True, pk["flags"])
    letter = [w for w in pk["written"] if "cover_letter" in w["path"]][0]
    check("and the cover letter is one of the flagged files",
          letter["flags"] > 0, True, letter)

    # ...and stops refusing the moment the author deletes the flag line.
    # Counted against the package's OWN before-count rather than against zero:
    # a project this bare has no abstract, no suggested reviewers and an
    # unfilled checklist, and each of those is a flag of its own. What this
    # case is about is the one flag the declaration contributes.
    flags_before = pk["flags"]
    letter_before = letter["flags"]
    ms._write(dpath, body.replace(ms.AI_DRAFT_FLAG, "").rstrip() + "\n")
    confirmed = ms._read(dpath)
    pk = ms.submission_package(droot, "Langmuir", dry_run=True)
    check("deleting the flag removes exactly one from the package",
          pk["flags"], flags_before - 1, pk["written"])
    letter_now = [w for w in pk["written"] if "cover_letter" in w["path"]][0]
    check("and it is the letter's",
          letter_now["flags"], letter_before - 1, letter_now)
    lm = ms._cover_letter_md(
        droot, "Langmuir",
        {"ai_disclosure.required": "yes",
         "ai_disclosure.in_submission": "cover_letter"},
        ms.parse_title_abstract(ms.source_text(droot, "title_abstract.md")),
        ms.author_record(droot), 1, "")
    check("and the letter carries the confirmed statement",
          "authors take full responsibility" in lm, True)
    check("and no longer says the declaration is missing",
          "has no statement in it yet" in lm, False)

    # (14) --draft NEVER overwrites a stub whose flag is gone. The
    #      user-edited-and-confirmed case.
    res = ms.ai_disclosure_cmd(droot, "Langmuir", draft=True)
    check("--draft leaves a confirmed statement alone",
          ms._read(dpath), confirmed)
    check("and says it is the author's",
          any(a["action"] == "keep" and "it is yours" in a["detail"]
              for a in res["actions"]), True, res["actions"])

    # ...nor one the author has written into with the flag still on it.
    ms._write(dpath, "# Declaration\n\n**[FLAG: author]** confirm\n\n"
                     "We used a spelling checker and nothing else.\n")
    theirs = ms._read(dpath)
    ms.ai_disclosure_cmd(droot, "Langmuir", draft=True)
    check("--draft leaves a half-written one alone too",
          ms._read(dpath), theirs,
          "a stub typed into is the author's, flag or no flag")

    # (15) --from-ledger APPENDS to the draft rather than replacing it, and
    #      still writes nothing the ledger does not contain.
    ms._write(dpath, ms.ai_disclosure_stub(
        "Langmuir", ms.ai_disclosure_cmd(droot, "Langmuir")["block"],
        draft=True))
    jd = ms.journal_dir(droot, "Langmuir")
    ms._log_run(jd, "**run r1-x opened** - draft on LANGMUIR r1")
    ms._log_run(jd, "`draft-sections` -> **done**")
    res = ms.ai_disclosure_cmd(droot, "Langmuir", from_ledger=True)
    body = ms._read(dpath)
    check("the default statement is still there after --from-ledger",
          ms.AI_DEFAULT_STATEMENT in body, True,
          "17.4: an append, never a replacement")
    check("and the ledger block sits after it",
          body.index(ms.AI_LEDGER_MARK)
          > body.index(ms.AI_DEFAULT_STATEMENT), True)
    check("the ledger names only what ran", "draft-sections" in body, True)
    check("and invents no module", "stats-check" in body, False)

    # (16) THE UPGRADE KEEPS THE LEDGER. A project that ran --from-ledger
    #      against the blanks and only then read the journal's policy must not
    #      lose the record on the way to a draft.
    blanks = ms.ai_disclosure_stub(
        "Langmuir", {"in_paper_heading": "", "scope": "", "exempt": "",
                     "source": "", "read_on": ""})
    ms._write(dpath, blanks.rstrip() + "\n\n" + ms.AI_LEDGER_MARK
              + " 2026-09-21 -->\n\n- r1: draft-sections ran.\n")
    ms.ai_disclosure_cmd(droot, "Langmuir", draft=True)
    body = ms._read(dpath)
    check("the upgrade writes the default statement",
          ms.AI_DEFAULT_STATEMENT in body, True)
    check("and carries the ledger block across",
          "- r1: draft-sections ran." in body, True,
          "the one thing in this file the engine appended, not the user")

    # (17) ITEM 115. The in-paper section is built from the file 17.4 names,
    #      and not from authors.md, which never held it and never will.
    ms._write(dpath, "# Declaration\n\n" + ms.AI_DEFAULT_STATEMENT + "\n")
    md, gap = ms.statement_block(droot, "ai_disclosure",
                                 "Declaration of Generative AI Use", True)
    check("the in-paper section carries the author's statement",
          "authors take full responsibility" in md, True, md)
    check("and leaves no gap", gap, "")
    ms._write(dpath, "")
    md, gap = ms.statement_block(droot, "ai_disclosure",
                                 "Declaration of Generative AI Use", True)
    check("an empty one still flags", "**[FLAG: author]**" in md, True, md)
    check("and the flag names ai_disclosure.md, not authors.md",
          "ai_disclosure.md" in md and "authors.md" not in md, True, md)


def test_agent_brief(tmp: str) -> None:
    section("agent-brief - the allow-lists, resolved into real prompts")

    sub_tmp = os.path.join(tmp, "briefs")
    os.makedirs(sub_tmp, exist_ok=True)
    root = build_project(sub_tmp)
    ms.configure(root, "Langmuir")

    # (1) Every module that claims to run as its own agent builds a prompt.
    for name in ms.AGENT_MODULES:
        res = ms.agent_brief(root, name, "Langmuir")
        check(f"{name}: builds without refusing", res["errors"], [])
        check(f"{name}: runs as an agent", res["runs_as_agent"], True)
        check(f"{name}: has a prompt", len(res["prompt"]) > 500, True,
              res["prompt"][:200])
        check(f"{name}: names at least one file to read",
              len(res["given"]) > 0, True)

    # (2) THE CONTRACT. A denied file may never arrive through the given
    # list. This is the check the toolkit did not have: the allow-lists were
    # prose, and prose cannot be violated by an implementation that simply
    # passes a different file. Every denial is matched against the resolved
    # paths AND against the prompt text, because a path that reaches the
    # agent as a string is a path the agent can open.
    #
    # `data/analysis/analysis.md` USED TO BE THE ENTRY THAT MATTERED HERE and
    # it is deliberately gone from every row below. It was a human lab note -
    # the place "the thermophile looks tighter, as expected" got written down,
    # and so the richest source of exactly the bias these modules exclude. It
    # is generated numbers now (spec analysis-and-front-matter 1.7), and
    # stats-check is GIVEN it: denying a statistics checker the statistics
    # would leave it nothing to check.
    #
    # The denial did not disappear, it MOVED. `plan/README.md` is where the
    # hope gets written down now, it was already on every row, and it is now
    # the load-bearing one. That is why it stays first in each list.
    denied_files = {
        "abstract": ["plan/README.md", "plan/outline.md"],
        "revise-prose": ["plan/README.md", "plan/outline.md",
                         "plan/captions.md", "references.bib"],
        "stats-check": ["plan/README.md", "plan/outline.md",
                        "plan/captions.md"],
        "citation-check": ["plan/README.md", "plan/outline.md",
                           "plan/captions.md"],
        "reviewer-check": ["plan/README.md", "plan/outline.md",
                           "analysis.md"],
    }
    for name, forbidden in denied_files.items():
        res = ms.agent_brief(root, name, "Langmuir")
        given_paths = [g["path"] for g in res["given"]]
        prompt = res["prompt"]
        for f in forbidden:
            leaked_via_list = any(f in p for p in given_paths)
            # The prompt's READ list only. The denial section names these
            # files on purpose - that is the point of it - so the check is
            # scoped to the half of the prompt that grants access.
            read_half = prompt.split("YOU ARE DENIED")[0]
            check(f"{name}: {f} is not in the given list",
                  leaked_via_list, False, str(given_paths))
            check(f"{name}: {f} is not readable from the prompt",
                  f.split("/")[-1] in read_half, False)

    # (3) The blind modules deny the spawning conversation itself. A module
    # given every right file and the transcript is not isolated - the
    # transcript is where the hypothesis has been sitting since the plan line.
    for name, spec in ms.AGENT_MODULES.items():
        if spec["isolation"] != "blind":
            continue
        res = ms.agent_brief(root, name, "Langmuir")
        denied = " ".join(d["path"] for d in res["denied"]).lower()
        check(f"{name}: denies the spawning conversation",
              "conversation" in denied, True, denied)
        check(f"{name}: the prompt says so too",
              "spawned you" in res["prompt"], True)
        check(f"{name}: says to skip rather than run contaminated",
              "SKIP" in res["fallback"].upper(), True, res["fallback"])

    # (4) A fork inherits the whole context, so it is named as forbidden
    # rather than merely not recommended. This is the one harness detail that
    # would silently undo every denial above while reporting success.
    res = ms.agent_brief(root, "stats-check", "Langmuir")
    check("fork is refused by name", "fork" in res["spawn"]["never"], True)
    check("...and a real type is offered instead",
          res["spawn"]["subagent_type"], "general-purpose")

    # (5) Paths are absolute and use one separator. A brief that emits
    # `C:/x/y\\drafts\\source_text_r1` is one an agent can fail to open, and
    # it will report the failure as a fact about the paper.
    res = ms.agent_brief(root, "draft-sections", "Langmuir")
    for g in res["given"]:
        check(f"absolute: {g['name']}", os.path.isabs(g["abs"]), True, g["abs"])
    mixed = [g["abs"] for g in res["given"]
             if "/" in g["abs"] and "\\" in g["abs"]]
    check("no path mixes both separators", mixed, [])

    # (6) The round reaches the write path. A literal `reports/rN/` sends the
    # agent to write at a path that does not exist.
    res = ms.agent_brief(root, "stats-check", "Langmuir")
    check("the write path resolved its round",
          any("rN" in w for w in res["writes"]), False, str(res["writes"]))
    check("...to the journal's reports folder",
          any("reports/r" in w for w in res["writes"]), True,
          str(res["writes"]))

    # ...and it is refused rather than guessed when no journal is named.
    res = ms.agent_brief(root, "stats-check", None)
    check("no journal is a refusal, not a default",
          bool(res["errors"]), True)
    check("...and the refusal says why", "--journal" in " ".join(res["errors"]),
          True)

    # (7) A module that does not run as an agent says so, with the reason.
    # Absence would read as an oversight and invite someone to "fix" it.
    res = ms.agent_brief(root, "assemble", "Langmuir")
    check("assemble does not run as an agent", res["runs_as_agent"], False)
    check("...and says why", bool(res["reason"]), True)
    res = ms.agent_brief(root, "not-a-module", "Langmuir")
    check("an unknown module refuses", bool(res["errors"]), True)

    # (8) A missing input is declared, never silently dropped. An agent that
    # goes looking for a file the project does not have reports its absence
    # as a finding about the manuscript.
    os.remove(os.path.join(root, "plan", "captions.md"))
    res = ms.agent_brief(root, "outline", "Langmuir")
    check("a missing input is listed as missing",
          any("captions" in m for m in res["missing"]), True,
          str(res["missing"]))
    check("...and the prompt tells the agent not to hunt for it",
          "NOT PRESENT" in res["prompt"], True)

    # (9) The plan the user approves says which modules will be isolated.
    # Otherwise there is nothing to approve: whether a module ran blind is
    # not visible in its report afterwards.
    pl = ms.plan(root, "Langmuir", "coauthor")
    check("the plan names its isolated modules",
          "stats-check" in pl["isolated_modules"], True,
          str(pl["isolated_modules"]))
    check("...and abstract rides with draft-sections",
          "abstract" in pl["isolated_modules"], True,
          str(pl["isolated_modules"]))
    check("...and marks which of them are blind",
          "stats-check" in pl["blind_modules"], True, str(pl["blind_modules"]))
    check("...while assemble is not among them",
          "assemble" in pl["isolated_modules"], False)

    # (10) The listing covers every module, both halves.
    listing = ms.agent_brief_list()
    listed = {r["module"] for r in listing["modules"]}
    missing = [m for m in ms.MODULES if m not in listed]
    check("every module in MODULES is accounted for", missing, [])
    check("...plus abstract, which is not one", "abstract" in listed, True)


# ---------------------------------------------------------------------------
# drafts/rough_draft.md - the denials, asserted BEFORE agent-brief knows the
# file exists. specs/writing-engine.md 20.4, and it is the build-order rule:
# write down what must not happen before writing the thing that could do it.
# A denial added after the source is wired in is a denial that was never
# observed to fail, and a contaminated stats report still looks fine.
# ---------------------------------------------------------------------------

# Deliberately unlike anything else in the fixture, and deliberately the
# sentence a person actually types into a rough draft: the hoped-for result,
# in the paper's own words, weeks before the test that would support it.
ROUGH_DRAFT = """# Rough draft

## Introduction

Chemoreceptor arrays have been resolved in mesophiles but nobody has looked
above 60 C, and we expected the thermophilic arrays to be the ordered ones.

## Results

The thermophilic arrays were clearly more ordered, with a symmetry index
around 0.87 against 0.61 for the mesophiles.

## Discussion

This is the result we predicted from the lipid-packing argument.
"""

# Phrases that must not reach a denied module by any route. Each is unique to
# the rough draft in this fixture, so a hit is a leak and not a coincidence.
ROUGH_DRAFT_PHRASES = [
    "we expected the thermophilic arrays to be the ordered ones",
    "clearly more ordered",
    "0.87",
    "the result we predicted",
    "lipid-packing",
]

# specs/writing-engine.md 20.4's denied column, in full. stats-check is the
# load-bearing one and is written first for that reason: a rough draft is
# where "the thermophilic arrays were clearly more ordered" gets written
# down, and an agent that has read it cannot un-read it.
ROUGH_DRAFT_DENIED = ["stats-check", "abstract", "revise-prose",
                      "comprehension-check", "attribution-check",
                      "evidence-check", "reviewer-check", "learn-from-edits"]


def test_rough_draft_denials(tmp: str) -> None:
    section("drafts/rough_draft.md - who may never read it (spec 20.4)")

    sub_tmp = os.path.join(tmp, "rough_denials")
    os.makedirs(sub_tmp, exist_ok=True)
    root = build_project(sub_tmp)
    ms.configure(root, "Langmuir")
    write(root, "drafts/rough_draft.md", ROUGH_DRAFT)
    check("the fixture's rough draft has content",
          bool(ms._read(os.path.join(root, "drafts",
                                     "rough_draft.md")).strip()), True)

    for name in ROUGH_DRAFT_DENIED:
        # Two of the six run in session rather than as their own agent
        # (`evidence-check`, `learn-from-edits`), so there is no given list
        # for the file to arrive through - and that is the assertion, not a
        # reason to skip them. It has to stay true: the day one of them is
        # promoted to an agent, the branch below takes over and the leak
        # checks bite. A module in neither table is an oversight.
        if name not in ms.AGENT_MODULES:
            check(f"{name}: is a module the engine accounts for",
                  name in ms.IN_SESSION_MODULES, True,
                  sorted(ms.AGENT_MODULES) + sorted(ms.IN_SESSION_MODULES))
            res = ms.agent_brief(root, name, "Langmuir")
            check(f"{name}: runs in session, so it is handed no files",
                  (res["runs_as_agent"], res["given"], res["prompt"]),
                  (False, [], ""))
            check(f"{name}: ...and says why, so absence is not an oversight",
                  bool(res["reason"]), True)
            continue
        res = ms.agent_brief(root, name, "Langmuir")
        given_paths = [g["path"] for g in res["given"]]
        prompt = res["prompt"]
        # The prompt's READ half only. The denial section names the file on
        # purpose - that is what a denial is - so the check is scoped to the
        # half of the prompt that grants access, exactly as the analysis.md
        # assertions above are.
        read_half = prompt.split("YOU ARE DENIED")[0]

        check(f"{name}: rough_draft.md is not in the given list",
              any("rough_draft" in pth for pth in given_paths), False,
              str(given_paths))
        check(f"{name}: rough_draft.md is not readable from the prompt",
              "rough_draft.md" in read_half, False)
        # The path is the obvious leak; the content is the one that would
        # survive a rename. A module handed the sentences does not need the
        # filename.
        leaked = [ph for ph in ROUGH_DRAFT_PHRASES
                  if ph.lower() in prompt.lower()]
        check(f"{name}: no rough-draft content reaches the prompt",
              leaked, [])


def test_comprehension_denials(tmp: str) -> None:
    section("comprehension-check reads the paper, and nothing that says "
            "what it means (comprehension-check 1)")

    sub = os.path.join(tmp, "comp_denials")
    os.makedirs(sub, exist_ok=True)
    root = build_project(sub)
    ms.configure(root, "Langmuir")
    write(root, "drafts/rough_draft.md", ROUGH_DRAFT)
    write(root, "plan/literature_landscape.md",
          "# The field\n\nNobody has resolved arrays above 60 C.\n")

    br = ms.agent_brief(root, "comprehension-check", "Langmuir")
    check("it spawns as its own agent", br["runs_as_agent"], True)
    check("...blind, because a reader who has read the plan can follow "
          "anything", br["isolation"], "blind")

    given = [g["path"] for g in br["given"]]
    read_half = br["prompt"].split("YOU ARE DENIED")[0]
    for name in ("outline.md", "README.md", "literature_landscape.md",
                 "rough_draft.md", "analysis.md"):
        check(f"{name} is not in the given list",
              any(name in g for g in given), False, str(given))
        check(f"...nor readable from the prompt", name in read_half, False)
    check("but the source text IS given - it is the thing being read",
          any("source_text" in g for g in given), True, str(given))
    check("...and the captions, because a reader has the figures",
          any("captions" in g for g in given), True, str(given))

    # The restatement is compared BY THE CALLER. A module that could see
    # what it is being compared against would produce a restatement that
    # matches, and the check would report clean while measuring nothing.
    check("the brief never hands it the declared take-home",
          "take-home" in br["prompt"].lower(), False)
    check("the four floors are prohibitions in the prompt, not advice",
          all(x in br["prompt"] for x in
              ("MAY NOT ASK FOR", "never that a technical term be REMOVED",
               "no reading level")), True)
    check("the closed reason set is in the prompt, all eight of it",
          all(r in br["prompt"] for r in
              ("term_undefined", "referent_unclear", "order", "overloaded",
               "connection", "jargon_density", "unexplained_step",
               "contradiction")), True)
    check("...and so is the part that keeps it honest",
          "WHAT YOU DID NOT NEED EXPLAINED" in br["prompt"], True)
    check("it is told not to rewrite", "not rewrite" in br["prompt"], True)

    # `audience` is the yardstick, and it is the dial that until now was
    # validated, written, echoed and read by nothing.
    check("the config reaches the brief, which is where `audience` lives",
          any("writing_config" in g for g in given), True, str(given))


def test_attribution_denials(tmp: str) -> None:
    section("attribution-check is blind to the thesis (review-paper 5)")

    sub = os.path.join(tmp, "attr_denials")
    os.makedirs(sub, exist_ok=True)
    root = build_project(sub)
    ms.set_paper_kind(root, "review", "narrative")
    ms.configure(root, "Langmuir")
    write(root, "data/corpus/protocol.md",
          "# Review Protocol\n\n## The Question *\n\n"
          "Whether thermophilic arrays are the ordered ones.\n")
    write(root, "data/corpus/synthesis.md", "# Synthesis\n\ngenerated\n")
    write(root, "data/corpus/papers/jensen2019arrays.md",
          "# jensen2019arrays\nread_tier: abstract\n\n"
          "## What it claims\n\nArrays were resolved in mesophiles.\n")

    br = ms.agent_brief(root, "attribution-check", "Langmuir")
    check("it spawns as its own agent", br["runs_as_agent"], True)
    check("...blind", br["isolation"], "blind")

    given = [g["path"] for g in br["given"]]
    read_half = br["prompt"].split("YOU ARE DENIED")[0]
    check("the paper RECORDS are given - that is the whole input",
          any("papers" in g for g in given), True, str(given))
    check("...and the source text it is checking",
          any("source_text" in g for g in given), True, str(given))

    # The thesis is the load-bearing denial. An agent that knows what the
    # review argues reads an ambiguous abstract as agreeing with it, and it
    # does so sincerely.
    for name in ("README.md", "outline.md", "protocol.md", "synthesis.md"):
        check(f"{name} is not in the given list",
              any(name in g for g in given), False, str(given))
        check(f"...nor readable from the prompt", name in read_half, False)

    # data/corpus/ as a DIRECTORY would hand over protocol.md and
    # synthesis.md while the brief said they were denied - the same way
    # `data_dir` once reversed stats-check's one load-bearing denial.
    check("data/corpus/ is never given whole, only papers/ inside it",
          [g for g in given if g.rstrip("/").endswith("data/corpus")], [])

    check("`out of tier` is named in the prompt as its own verdict",
          "out of tier" in br["prompt"], True)
    check("...and the tier contract is stated, not implied",
          "Limits of this record" in br["prompt"], True)
    check("it is told it has not been told what the paper argues",
          "NOT been told what this paper argues" in br["prompt"], True,
          "it said `this review` until 2026-09-20, when the module stopped "
          "being a review's alone")
    check("...and told to report what came back supported too",
          "supports" in br["prompt"], True)
    check("it does not rewrite", "not rewrite" in br["prompt"], True)


# ---------------------------------------------------------------------------
# Outline adherence on a 1-5 scale, asked every round - writing-engine 19
# ---------------------------------------------------------------------------

def test_outline_adherence(tmp: str) -> None:
    section("outline adherence, asked every round on a scale of 1-5 (spec 19)")

    # The engine by path under a distinct module name, the same way every
    # other test in this file that needs prose.py loads it.
    _spec_p = importlib.util.spec_from_file_location(
        "prose_engine_adh", os.path.join(ROOT, "tools", "prose.py"))
    if _spec_p is None or _spec_p.loader is None:
        raise ImportError("cannot load tools/prose.py")
    pr = importlib.util.module_from_spec(_spec_p)
    _spec_p.loader.exec_module(pr)

    def capture(fn, *a):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn(*a)
        return buf.getvalue()

    sub_tmp = os.path.join(tmp, "adherence")
    os.makedirs(sub_tmp, exist_ok=True)
    root = build_project(sub_tmp)
    ms.init(root, "Langmuir")
    jdir = ms.journal_dir(root, "Langmuir")
    cfgp = os.path.join(jdir, "writing_config.yml")

    # (1) Every level round-trips through `config` in BOTH spellings, and the
    # three words the dial used to accept are not retired - they are names
    # for three of the five levels, so an existing config saying `strict`
    # reads back as 5 and is never rewritten.
    for n, name in sorted(ms.ADHERENCE_LEVELS.items()):
        res = ms.configure(root, "Langmuir", sets=[f"outline_adherence={n}"])
        check(f"level {n} is accepted as a number", res.get("errors"), [])
        check(f"...and reads back as {n}",
              ms.adherence_level(res["config"]["dials"]["outline_adherence"]),
              n)
        res = ms.configure(root, "Langmuir",
                           sets=[f"outline_adherence={name}"])
        check(f"'{name}' is accepted as a word", res.get("errors"), [])
        check(f"...and is the same value as {n}",
              ms.adherence_level(res["config"]["dials"]["outline_adherence"]),
              n)
    res = ms.configure(root, "Langmuir", sets=["outline_adherence=6"])
    check("a level outside 1-5 is refused", bool(res["errors"]), True)
    res = ms.configure(root, "Langmuir", sets=["outline_adherence=tightish"])
    check("...and so is a near-miss spelling", bool(res["errors"]), True)

    # An existing config is not rewritten. `strict` stays the word `strict`
    # in the file and means 5 everywhere downstream.
    ms.configure(root, "Langmuir", sets=["outline_adherence=strict"])
    check("the file keeps the spelling the user wrote",
          "outline_adherence: strict" in read(cfgp), True)
    check("...and it reads as 5",
          ms.adherence_level(
              ms.read_config(cfgp)["dials"]["outline_adherence"]), 5)

    # (2) A per-round answer lands in round_state.json and does NOT change
    # writing_config.yml. This is the ratchet 19.3 exists to prevent: one
    # round drafted at 1 while the argument was being re-found would
    # otherwise turn the claims ledger off for every round after it.
    ms.configure(root, "Langmuir", sets=["outline_adherence=3"])
    before = read(cfgp)
    pl = ms.plan(root, "Langmuir", "draft", adherence="1")
    check("the round's answer is the round's",
          pl["adherence"]["level"], 1, pl["adherence"])
    check("...and it is recorded in round_state.json",
          ms.round_state(jdir).get("adherence"), 1)
    check("...and writing_config.yml is byte-identical",
          read(cfgp), before,
          "a per-round answer that rewrote the standing dial would ratchet")
    check("...while the standing preference is still reported",
          pl["adherence"]["standing"], 3)

    # `always` is the one answer that moves both.
    pl = ms.plan(root, "Langmuir", "draft", adherence="4 always")
    check("`4 always` sets the round", pl["adherence"]["level"], 4)
    check("...and says it was an always", pl["adherence"]["always"], True)
    check("...and moves the standing preference too",
          ms.adherence_level(
              ms.read_config(cfgp)["dials"]["outline_adherence"]), 4)

    # (3) --adherence supplies the answer with no prompt, and the absent case
    # is labelled `(not asked)` in the plan output AND in the log entry. A
    # round whose adherence was assumed has to be recognisable a month later.
    fresh = build_project(os.path.join(sub_tmp, "notasked"))
    ms.init(fresh, "Langmuir")
    fjdir = ms.journal_dir(fresh, "Langmuir")
    # The COMMON CASE is the first run: r1, nothing written yet, standing
    # preference kept. The fixture ships prose in every section, which is a
    # legitimately noisier situation - 21.2 requires the block to say
    # ADVISORY there, and that line is information rather than friction.
    for _s in ms.SECTION_FILES:
        with open(os.path.join(ms.source_text_dir(fresh), f"{_s}.md"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write("")
    pl = ms.plan(fresh, "Langmuir", "draft")
    check("with no answer the standing preference is used",
          pl["adherence"]["level"], 3)
    check("...and it is NOT recorded as an answer",
          "adherence" in ms.round_state(fjdir), False)
    check("...and the plan says nobody was asked",
          pl["adherence"]["asked"], False)
    out = capture(ms.print_plan, pl)
    check("the plan OUTPUT says (not asked)", "(not asked)" in out, True, out)
    check("...and names the level in one line",
          "Outline adherence for this round:  3 (medium)" in out, True, out)

    ms.log_round_heading(fjdir, "Langmuir", 1, [],
                         ms.round_adherence(fjdir, rnd=1))
    log = read(ms.log_path(fresh))
    check("the log entry names the level and says (not asked)",
          "Outline adherence 3 (medium) (not asked)" in log, True, log[:400])

    # (4) The plan block gains EXACTLY ONE line on a project keeping its
    # standing preference. The common case must not get noisier - the same
    # rule 16.5 applies to the sources block, and the mitigation for "asking
    # every round trains the user to press enter" is that the question only
    # says more when it has more to say.
    # Counted against the SAME plan printed without the block, so what is
    # measured is what this block added rather than every line that happens
    # to say the word - the dials echo has carried `outline_adherence=` since
    # long before this section, and it is the standing preference, not the
    # round's.
    # Content lines, not raw ones: every block in this output opens with a
    # blank separator, so counting those would measure the house style rather
    # than what the block says.
    bare = dict(pl)
    bare.pop("adherence", None)

    def said(text):
        return [ln for ln in text.splitlines() if ln.strip()]

    grew = len(said(out)) - len(said(capture(ms.print_plan, bare)))
    check("the plan block gains exactly one line in the common case", grew, 1,
          [ln for ln in out.splitlines() if "Outline adherence" in ln])

    # (5) Level 1 softens the adherence findings to advisory and leaves every
    # truth check an error - the same softening prose 4.3 gives a frozen
    # section.
    write(fresh, "plan/outline.md",
          "# Structure\n\n## Results\n\n- Arrays are more ordered in "
          "thermophiles [Figure 9]\n- Order tracks temperature "
          "[stats: nothing_here]\n- A claim nobody drafted [@nosuchkey]\n")
    at3 = pr.outline(fresh, 3)
    at1 = pr.outline(fresh, 1)
    adherence_rules = set(at3["contract"]["adherence_findings"])

    def sev(res, rules):
        return sorted({f["severity"] for f in res["findings"]
                       if f["rule"] in rules})

    truth = {"float_missing", "stats_term_missing", "citekey_missing",
             "stats_output_missing", "empty_bracket", "unreadable_evidence"}
    check("at 3 the adherence findings carry their normal severity",
          "advisory" in sev(at3, adherence_rules), False, at3["findings"])
    check("at 1 every adherence finding is advisory",
          sev(at1, adherence_rules) in ([], ["advisory"]), True,
          [f for f in at1["findings"] if f["rule"] in adherence_rules])
    check("...and the truth checks are untouched",
          sev(at1, truth), sev(at3, truth),
          "level 1 softens the ledger, never the facts")
    check("...and they are still errors",
          sev(at1, truth), ["error"], at1["findings"])
    check("level 1 says so in the payload", at1["level"], 1)
    check("...and the note explains what stops working",
          "advisory" in at1["note"], True, at1["note"])

    # (6) Three consecutive level-1 rounds raise a `completeness` gap, and
    # two do not. Never a block: level 1 is a legitimate answer.
    streaked = build_project(os.path.join(sub_tmp, "streak"))
    ms.init(streaked, "Langmuir")
    sjdir = ms.journal_dir(streaked, "Langmuir")
    ms.update_round_state(sjdir, round=2,
                          adherence_rounds={"1": 1, "2": 1})
    comp = ms.completeness(streaked, "Langmuir")
    check("two level-1 rounds raise nothing",
          [m for m in comp["missing"] if "adherence 1" in m["detail"]], [])
    ms.update_round_state(sjdir, round=3,
                          adherence_rounds={"1": 1, "2": 1, "3": 1})
    comp = ms.completeness(streaked, "Langmuir")
    hits = [m for m in comp["missing"] if "adherence 1" in m["detail"]]
    check("three in a row raise one", len(hits), 1, comp["missing"])
    check("...as a gap and never a block",
          hits[0]["severity"] if hits else "", "gap")
    ms.update_round_state(sjdir, round=4,
                          adherence_rounds={"1": 1, "2": 1, "3": 1, "4": 3})
    comp = ms.completeness(streaked, "Langmuir")
    check("a round that breaks the streak clears it",
          [m for m in comp["missing"] if "adherence 1" in m["detail"]], [])

    # (7) The engine's copy of the levels table is the same table prose.py
    # reads with. A second table of what `medium` means is the whole failure
    # 19.2 forbids.
    contract = at3["contract"]
    check("prose.py and manuscript.py agree on the five levels",
          contract["adherence_levels"],
          {str(n): name for n, name in ms.ADHERENCE_LEVELS.items()})
    for spelling in (ms.ADHERENCE_VALUES + ["", "nonsense", "STRICT", 3, 9]):
        check(f"...and on what {spelling!r} normalizes to",
              ms.adherence_level(spelling), pr.adherence_level(spelling))


# ---------------------------------------------------------------------------
# drafts/rough_draft.md as a source, and the plan block - writing-engine 20.5,
# 20.1 and 16.3
# ---------------------------------------------------------------------------

def test_rough_draft_source(tmp: str) -> None:
    section("drafts/rough_draft.md as a drafting source (spec 20.5, 20.1)")

    sub_tmp = os.path.join(tmp, "rough_source")
    os.makedirs(sub_tmp, exist_ok=True)

    def capture(fn, *a):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn(*a)
        return buf.getvalue()

    # (1) An untouched stub is NOT a rough draft. Content-tested, never
    # existence-tested - the stub is scaffolded into every project, so
    # existence-testing it would report a rough draft on every project the
    # day it was created.
    stub = build_project(os.path.join(sub_tmp, "stub"))
    ms.init(stub, "Langmuir")
    check("a scaffolded project HAS the file",
          os.path.isfile(ms.rough_draft_path(stub)), True)
    check("...and it reports EMPTY, not filled",
          ms.rough_draft_sections(stub)["state"], "EMPTY")
    check("...and covers no section",
          ms.rough_draft_covers(stub), [])
    src = [s for s in ms.outline_sources(stub)["sources"]
           if s["name"] == "rough_draft"]
    check("outline_sources lists it", len(src), 1,
          [s["name"] for s in ms.outline_sources(stub)["sources"]])
    check("...as a primary source", src[0]["weight"] if src else "", "primary")
    check("...and reports it absent", src[0]["present"] if src else True,
          False)
    check("...saying an empty one is normal rather than nagging",
          "empty is normal" in (src[0]["detail"] if src else ""), True,
          src[0]["detail"] if src else "")
    # The fixture already carries drafted prose, so its sections are `edit`
    # for the OTHER reason - 21.3's general rule, which is that any round
    # after the one that wrote a section edits it. What has to be true of an
    # untouched stub is that NONE of it comes from the rough draft.
    modes = ms.draft_modes(stub)
    check("no section is in edit mode because of an untouched stub",
          [s for s, m in modes.items() if m["from_rough_draft"]], [], modes)
    check("...and none of them reports rough-draft words",
          sorted({m["rough_draft_words"] for m in modes.values()}), [0])
    # Emptied, the same fixture composes: an empty section is `compose`
    # whatever the round, which is what keeps the documented
    # empty-the-folder gesture working (21.6).
    for s in ms.SECTION_FILES:
        with open(os.path.join(ms.source_text_dir(stub), f"{s}.md"),
                  "w", encoding="utf-8", newline="\n") as fh:
            fh.write("")
    check("every section of an emptied project with a stub is `compose`",
          sorted({m["mode"] for m in ms.draft_modes(stub).values()}),
          ["compose"])

    # An empty rough draft is never a GAP. `primary` is its default weight,
    # so without the optional-source rule every project ever scaffolded
    # would report a primary source with no content in it.
    cfg = ms.read_config(os.path.join(ms.journal_dir(stub, "Langmuir"),
                                      "writing_config.yml"))
    block = ms.drafting_sources(stub, cfg, list(ms.MANAGED_SCOPES),
                                ["draft-sections"])
    check("an empty rough draft is not a primary gap",
          [g["key"] for g in block["primary_gaps"] if g["key"] == "rough_draft"],
          [])

    # (2) A project whose ONLY content is a rough draft can draft. That is
    # the whole point of the file: the paper is in it.
    only = build_project(os.path.join(sub_tmp, "only"))
    ms.init(only, "Langmuir")
    for rel in ("plan/README.md", "plan/outline.md", "plan/captions.md",
                "data/analysis/analysis.md"):
        p = os.path.join(only, *rel.split("/"))
        if os.path.isfile(p):
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("")
    with open(ms.bib_path(only), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("")
    for s in ms.SECTION_FILES:
        p = os.path.join(ms.source_text_dir(only), f"{s}.md")
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("")
    write(only, "drafts/rough_draft.md", ROUGH_DRAFT)
    res = ms.outline_sources(only)
    check("a project whose only content is a rough draft CAN draft",
          res["can_draft"], True, res["note"])
    check("...and the rough draft is what it names",
          "rough_draft" in res["have"], True, res["have"])

    # (3) Partial coverage is the EXPECTED shape, not a degenerate one: three
    # covered sections are `edit` and the rest `compose`, in one project.
    covers = ms.rough_draft_covers(
        only, {"introduction": "introduction", "results": "results",
               "discussion": "discussion", "methods": "methods",
               "title and abstract": "title_abstract"})
    check("the fixture covers exactly three sections",
          covers, ["introduction", "results", "discussion"], covers)
    modes = ms.draft_modes(
        only, None, {"introduction": "introduction", "results": "results",
                     "discussion": "discussion"})
    check("the covered sections are `edit`",
          sorted(s for s, m in modes.items() if m["mode"] == "edit"),
          ["discussion", "introduction", "results"])
    check("...and the rest are `compose` in the same project",
          sorted(s for s, m in modes.items() if m["mode"] == "compose"),
          ["methods", "title_abstract"])
    check("...and an edit-mode section says the prose is the user's",
          modes["introduction"]["from_rough_draft"], True)

    # (4) The plan block names the coverage, per section (20.1).
    pl = ms.plan(only, "Langmuir", "draft")
    out = capture(ms.print_plan, pl)
    check("the plan block names the rough draft",
          "Rough draft: drafts/rough_draft.md" in out, True, out)
    check("...with a per-section line for every section",
          all(s in out for s in ms.SECTION_FILES), True, out)
    check("...marking the covered ones EDIT",
          "introduction" in out and "EDIT" in out, True, out)
    check("...and saying revise-prose will skip them",
          "revise-prose will skip it" in out, True, out)
    check("a project with no rough draft gets NO block at all",
          "Rough draft:" in capture(ms.print_plan,
                                    ms.plan(stub, "Langmuir", "draft")),
          False, "an empty one is a normal project - nothing nags about it")

    # (5) The weight has exactly two settings, and `low` is refused WITH the
    # reason. A rough draft that is *sort of* consulted is the worst of both
    # modes: the engine writes its own section while borrowing the phrasing.
    for bad in ("low", "normal"):
        res = ms.configure(only, "Langmuir", weight=[f"rough_draft={bad}"])
        check(f"source_weights rough_draft={bad} is refused",
              bool(res["errors"]), True)
        check(f"...and the refusal says why",
              "borrowing your phrasing" in " ".join(res["errors"]), True,
              res["errors"])
    for good in ("primary", "ignore"):
        res = ms.configure(only, "Langmuir", weight=[f"rough_draft={good}"])
        check(f"...while {good} is accepted", res["errors"], [])

    # `ignore` takes it out of the brief and says so in the plan block.
    ms.configure(only, "Langmuir", weight=["rough_draft=ignore"])
    brief = ms.agent_brief(only, "draft-sections", "Langmuir")
    check("`ignore` removes it from the given list",
          [g for g in brief["given"] if g["name"] == "rough_draft"], [])
    check("...and says so in the denied list, never silently",
          [d for d in brief["denied"] if "rough_draft" in d["path"]] != [],
          True, brief["denied"])
    out = capture(ms.print_plan, ms.plan(only, "Langmuir", "draft"))
    check("...and the plan block reports the ignore",
          "ignore: not read this round" in out, True, out)
    check("...and every section composes fresh under it",
          [m["mode"] for m in ms.plan(only, "Langmuir",
                                      "draft")["draft_modes"]],
          ["compose"] * len(ms.SECTION_FILES))
    ms.configure(only, "Langmuir", weight=["rough_draft=primary"])

    # (6) It reaches the two modules that may have it, and no others - the
    # denial half is asserted in full by test_rough_draft_denials, written
    # before agent-brief had ever heard of the file.
    for module in ("outline", "draft-sections"):
        brief = ms.agent_brief(only, module, "Langmuir")
        paths = [g["path"] for g in brief["given"]]
        check(f"{module} is given the rough draft",
              any("rough_draft" in p for p in paths), True, paths)
        check(f"...and told it outranks the other content sources",
              "IT WINS" in brief["prompt"], True)

    # An EMPTY rough draft reaches the drafter as NOT PRESENT rather than as
    # a file of bare headings.
    brief = ms.agent_brief(stub, "draft-sections", "Langmuir")
    check("an untouched stub is declared missing, not handed over",
          any("rough_draft" in m for m in brief["missing"]), True,
          brief["missing"])
    check("...and the prompt tells the agent not to hunt for it",
          "NOT PRESENT" in brief["prompt"], True)


# ---------------------------------------------------------------------------
# The outline is generative in r1 and advisory afterwards - writing-engine 21
# ---------------------------------------------------------------------------
#
# THE REGRESSION THIS SECTION EXISTS FOR, and it is written first because the
# defect it prevents ships at exit code 0. Built literally, 19.2's level table
# says a deviation at level 4 or 5 is *fixed*, and 20.1's mode table derives
# `edit` from the rough draft alone. Either one, and an r3 run rebuilds r2's
# sections from plan/outline.md - discarding every correction r2's checks
# earned, with a valid .docx and clean checks to show for it.
#
# The sentence in the fixture is the specific thing that gets lost: a `data`
# flag that r2 resolved into a limitation. It is real prose, no outline line
# implies it, and `prose.py outline` correctly reports it as `added_content`.
# The assertion is on THE SENTENCE STILL BEING IN THE FILE - not on anything
# the report says, because the report was never the part that was wrong.

RESOLVED_LIMITATION = (
    "Coating thickness was not measured for the annealed series, so the "
    "percolation argument is made for the as-deposited films only.")


def test_outline_advisory_after_r1(tmp: str) -> None:
    section("the outline generates in r1 and CHECKS afterwards (spec 21)")

    sub_tmp = os.path.join(tmp, "advisory")
    os.makedirs(sub_tmp, exist_ok=True)
    root = build_project(sub_tmp)
    ms.init(root, "Langmuir")
    jdir = ms.journal_dir(root, "Langmuir")

    # An outline that says nothing about the limitation, so no level can
    # claim the sentence was implied by it.
    write(root, "plan/outline.md",
          "# Structure\n\n## Results\n\n"
          "- Thermophilic arrays are more ordered [Figure 2]\n"
          "- Order tracks growth temperature [stats: symmetry_lm]\n")

    # r2's results.md: the drafted paragraphs, plus the sentence r2's checks
    # earned. This is what an r3 at adherence 5 must not silently discard.
    write(root, "drafts/source_text/results.md",
          "# Results\n\n"
          "Thermophilic arrays are more ordered than mesophilic ones "
          "(Figure 2).\n\n"
          "Order tracks growth temperature rather than phylogeny.\n\n"
          + RESOLVED_LIMITATION + "\n")
    ms.update_round_state(jdir, round=2)
    with open(os.path.join(root, "project.yml"), "a", encoding="utf-8",
              newline="\n") as fh:
        fh.write("\nrevision: 2\n")

    results = os.path.join(ms.source_text_dir(root), "results.md")
    check("the fixture carries a sentence no outline line implies",
          RESOLVED_LIMITATION in read(results), True)

    # `prose.py outline` should SEE it as a deviation. That was never the
    # bug - the bug was what happened next.
    ol = prose_outline(root, 5)
    added = [f for f in ol["findings"] if f["rule"] == "added_content"]
    check("...and the outline check reports it as a deviation",
          bool(added), True, ol["findings"])

    # THE ASSERTION. Every level, because a level is a REPORTING strength
    # from r2 on and no value of it may be raised into a rewrite (21.5) -
    # the same shape as 16.5's assertion that no weight can add a file to a
    # denied list.
    for level in sorted(ms.ADHERENCE_LEVELS):
        pl = ms.plan(root, "Langmuir", "draft", adherence=str(level))
        modes = {m["section"]: m["mode"] for m in pl["draft_modes"]}
        check("adherence %d does not recompose results at r3" % level,
              modes["results"], "edit",
              "engine-owned is not the same as disposable")
        check("...and the sentence is still in the file after planning it",
              RESOLVED_LIMITATION in read(results), True)
    check("no level recomposes ANY written section",
          sorted({m["mode"] for level in ms.ADHERENCE_LEVELS
                  for m in ms.plan(root, "Langmuir", "draft",
                                   adherence=str(level))["draft_modes"]
                  if m["words"]}),
          ["edit"])

    # `--redraft` is the ONE escape hatch, and it is explicit and named.
    pl = ms.plan(root, "Langmuir", "draft", redraft=["results"])
    modes = {m["section"]: m["mode"] for m in pl["draft_modes"]}
    check("--redraft results makes that one section compose",
          modes["results"], "compose")
    check("...and leaves every other written section in edit",
          sorted({s for s, m in modes.items()
                  if m == "compose"}) == ["results"]
          or all(m == "edit" for s, m in modes.items() if s != "results"),
          True, modes)

    # An EMPTY section is `compose` whatever the round: emptying
    # source_text/ to force a redraft is documented in 3 and has to keep
    # meaning what it means (21.6).
    with open(os.path.join(ms.source_text_dir(root), "discussion.md"),
              "w", encoding="utf-8", newline="\n") as fh:
        fh.write("")
    pl = ms.plan(root, "Langmuir", "draft")
    modes = {m["section"]: m["mode"] for m in pl["draft_modes"]}
    check("a wiped section is compose at r3", modes["discussion"], "compose")
    check("...while its neighbours stay edit", modes["results"], "edit")


# ---------------------------------------------------------------------------
# The retention invariant, the brief's modes, and the abstract - 20.3, 20.7,
# 20.8, 21.2, 21.4, 21.5
# ---------------------------------------------------------------------------

def test_retention_invariant(tmp: str) -> None:
    section("edit mode is a checked postcondition, not an instruction (20.3)")

    sub_tmp = os.path.join(tmp, "retention")
    os.makedirs(sub_tmp, exist_ok=True)

    root = build_project(sub_tmp)
    ms.init(root, "Langmuir")
    jdir = ms.journal_dir(root, "Langmuir")
    st = ms.source_text_dir(root)

    # --- the rough-draft baseline, at r1 ------------------------------
    write(root, "drafts/rough_draft.md",
          "# Rough draft\n\n## Methods\n\n"
          "Films were grown at 450 C for 20 min under 5 mTorr of argon.\n\n"
          "Thickness was measured by SEM on three cross-sections "
          "[@jensen2019arrays].\n")
    aliases = {"methods": "methods", "introduction": "introduction",
               "results": "results", "discussion": "discussion",
               "title and abstract": "title_abstract"}

    # A pass that keeps everything, fixes a mechanic and adds a flag: the
    # normal, successful shape of edit mode.
    write(root, "drafts/source_text/methods.md",
          "# Methods\n\nFilms were grown at 450 C for 20 min under 5 mTorr "
          "of argon.\n\nThickness was measured by SEM on three "
          "cross-sections [@jensen2019arrays] **[FLAG: author - which "
          "microscope?]**.\n")
    res = ms.retention_check(root, "methods", read(
        os.path.join(st, "methods.md")), 1, aliases)
    check("a pass that keeps every sentence is accepted", res["ok"], True,
          res["findings"])
    check("...and the baseline is the rough draft at r1",
          res["baseline"], "rough_draft")
    check("...with every sentence accounted for",
          len(res["kept"]) + len(res["edited"]), 2,
          (res["kept"], res["edited"]))
    check("...and an ADDED flag is not a violation",
          [f["rule"] for f in res["findings"]], [])

    # --- the three gates, one at a time -------------------------------
    write(root, "drafts/source_text/methods.md",
          "# Methods\n\nFilms were grown at 450 C for 20 min under 5 mTorr "
          "of argon.\n")
    res = ms.retention_check(root, "methods", read(
        os.path.join(st, "methods.md")), 1, aliases)
    check("a dropped sentence with no Removed entry REJECTS the pass",
          res["ok"], False)
    check("...and the finding names the sentence",
          [f["rule"] for f in res["findings"]], ["sentence_dropped",
                                                 "citekey_dropped"],
          res["findings"])

    write(root, "drafts/source_text/methods.md",
          "# Methods\n\nFilms were grown at 450 C for 20 min under 5 mTorr "
          "of argon.\n\nThickness was measured by SEM on three "
          "cross-sections [@jensen2019arrays].\n\n"
          "## Removed\n\n- nothing\n")
    # A number LEAVING, rather than a unit changing: `450 C` -> `450
    # degrees` keeps the token 450, which is the point - the gate is about
    # values, not about how they are spelled.
    write(root, "drafts/source_text/methods.md", read(
        os.path.join(st, "methods.md")).replace("for 20 min ", ""))
    res = ms.retention_check(root, "methods", read(
        os.path.join(st, "methods.md")), 1, aliases)
    check("a number the user wrote may not quietly leave",
          "number_dropped" in [f["rule"] for f in res["findings"]], True,
          res["findings"])

    write(root, "drafts/source_text/methods.md",
          "# Methods\n\nFilms were grown at 450 C for 20 min under 5 mTorr "
          "of argon.\n\nThickness was measured by SEM on three "
          "cross-sections.\n")
    res = ms.retention_check(root, "methods", read(
        os.path.join(st, "methods.md")), 1, aliases)
    check("citekeys are additive only",
          "citekey_dropped" in [f["rule"] for f in res["findings"]], True,
          res["findings"])

    # --- growth is an INSTRUMENT, not a gate (prose 0.2) ---------------
    grown = ("# Methods\n\nFilms were grown at 450 C for 20 min under 5 "
             "mTorr of argon.\n\nThickness was measured by SEM on three "
             "cross-sections [@jensen2019arrays].\n\n"
             + " ".join(["Additional detail about the chamber."] * 40) + "\n")
    write(root, "drafts/source_text/methods.md", grown)
    res = ms.retention_check(root, "methods", grown, 1, aliases)
    check("growth past +25% is REPORTED",
          "grew_past_budget" in [f["rule"] for f in res["findings"]], True,
          res["findings"])
    check("...and does not reject the pass", res["ok"], True,
          "a new check with teeth and no track record breaks working rounds")
    check("...as a warning, not an error",
          [f["severity"] for f in res["findings"]
           if f["rule"] == "grew_past_budget"], ["warning"])

    # --- the fallback is the user's text, and it is a usable section ---
    write(root, "drafts/source_text/methods.md",
          "# Methods\n\nFilms were grown at 450 C.\n\n"
          "**[FLAG: author - which microscope?]**\n")
    res = ms.retention(root, "Langmuir", restore=True)
    check("a rejected section is restored", res["rejected"], ["methods"],
          res)
    body = read(os.path.join(st, "methods.md"))
    check("...from the baseline, verbatim",
          "Thickness was measured by SEM on three cross-sections" in body,
          True, body)
    check("...with the pass's flags appended",
          "**[FLAG: author" in body, True, body)
    check("...which is a usable section, not a failed round",
          res["errors"], [])
    check("the report is written where the round can find it",
          res["report"].replace(os.sep, "/").endswith(
              "reports/r1/retention_report.md"), True, res["report"])
    report = read(os.path.join(root, res["report"].replace("/", os.sep)))
    for heading in ("### Kept", "### Edited", "### Removed"):
        check("the report carries %r" % heading, heading in report, True)
    check("...and it is named for what it protects, not for the rough draft",
          "rough_draft_report" in res["report"], False, res["report"])

    # --- the r2+ baseline is the previous round's snapshot (21.3) ------
    snap = os.path.join(root, "obsolete", "drafts", "r2")
    os.makedirs(snap, exist_ok=True)
    with open(os.path.join(snap, "results.md"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write("# Results\n\nArrays are more ordered in thermophiles.\n\n"
                 "Coating thickness was not measured for the annealed "
                 "series.\n")
    ms.update_round_state(jdir, round=3)
    with open(os.path.join(root, "project.yml"), "a", encoding="utf-8",
              newline="\n") as fh:
        fh.write("\nrevision: 3\n")
    base = ms.retention_baseline(root, "results", 3, aliases)
    check("at r3 the baseline is r2's snapshot", base["kind"], "snapshot")
    check("...and it names the folder round already writes",
          base["where"], "obsolete/drafts/r2/results.md")
    write(root, "drafts/source_text/results.md",
          "# Results\n\nArrays are more ordered in thermophiles.\n")
    res = ms.retention_check(root, "results", read(
        os.path.join(st, "results.md")), 3, aliases)
    check("r3 cannot silently drop a sentence r2 had", res["ok"], False,
          res["findings"])
    check("...and that is the postcondition, not an instruction",
          "sentence_dropped" in [f["rule"] for f in res["findings"]], True)


def test_brief_modes_and_abstract(tmp: str) -> None:
    section("the brief carries the mode, and the abstract that already "
            "exists (20.4, 20.8, 21.2)")

    sub_tmp = os.path.join(tmp, "briefmodes")
    os.makedirs(sub_tmp, exist_ok=True)

    def capture(fn, *a):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn(*a)
        return buf.getvalue()

    root = build_project(sub_tmp)
    ms.init(root, "Langmuir")

    # --- revise-prose SKIPS an edit-mode section and reports the skip ---
    brief = ms.agent_brief(root, "revise-prose", "Langmuir")
    check("revise-prose skips every edit-mode section",
          sorted(brief["skipped_sections"]), sorted(ms.SECTION_FILES),
          "the fixture has prose in all five")
    check("...and the prompt says to REPORT the skip",
          "SAY IN YOUR REPORT that you skipped it" in brief["prompt"], True)
    check("...naming them one by one rather than counting them",
          all("%-15s SKIP" % s in brief["prompt"] or (s + " ") in
              brief["prompt"] for s in ms.SECTION_FILES), True)

    # --- draft-sections is told the mode per section -------------------
    brief = ms.agent_brief(root, "draft-sections", "Langmuir")
    check("draft-sections gets the per-section mode",
          "THIS ROUND'S MODE, PER SECTION" in brief["prompt"], True)
    check("...and is told edit mode changes nothing else",
          "CHANGE NOTHING ELSE" in brief["prompt"], True)
    check("...and that an uncovered outline line is ADDITIVE (21.4)",
          "added from outline line N" in brief["prompt"], True)
    check("...leaving its neighbours untouched",
          "leaving its neighbours untouched" in brief["prompt"], True)
    check("...and that a dropped sentence rejects the pass",
          "rejects the whole pass" in brief["prompt"], True)

    # --redraft reaches the brief, so an isolated agent can act on it.
    brief = ms.agent_brief(root, "draft-sections", "Langmuir",
                           redraft=["results"])
    modes = {m["section"]: m["mode"] for m in brief["draft_modes"]}
    check("--redraft results reaches the brief", modes["results"], "compose")
    check("...and every other section stays edit",
          sorted({m for s, m in modes.items() if s != "results"}), ["edit"])

    # --- 20.8: a rough-draft abstract stands, and the estimate drops ---
    for s in ms.SECTION_FILES:
        with open(os.path.join(ms.source_text_dir(root), f"{s}.md"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write("")
    empty = ms.plan(root, "Langmuir", "draft")
    check("with no abstract anywhere, the blind agent is in the plan",
          "abstract" in empty["isolated_modules"], True)
    write(root, "drafts/rough_draft.md",
          "# Rough draft\n\n## Title and abstract\n\n"
          "Chemoreceptor arrays are more ordered in thermophiles. We compared "
          "array symmetry across species spanning 30 C of growth "
          "temperature.\n")
    after = ms.plan(root, "Langmuir", "draft")
    check("a rough-draft abstract takes module 1b out of the plan",
          "abstract" in after["isolated_modules"], False,
          after["isolated_modules"])
    check("...and out of the blind list with it",
          "abstract" in after["blind_modules"], False)
    check("...and the estimate is exactly one lower",
          empty["agent_calls"] - after["agent_calls"], 1,
          (empty["agent_calls"], after["agent_calls"]))
    check("...and the plan says where the abstract came from",
          after["abstract_from_rough_draft"], True)
    check("...and says --redraft hands it back",
          any("--redraft title_abstract" in w for w in after["warnings"]),
          True, after["warnings"])
    check("the estimate in the printed line matches the payload",
          "Est. %d agent calls" % after["agent_calls"] in after["line"], True,
          after["line"])

    # --- 21.2: ADVISORY at r2+, and not at r1 --------------------------
    fresh = build_project(os.path.join(sub_tmp, "r1"))
    ms.init(fresh, "Langmuir")
    for s in ms.SECTION_FILES:
        with open(os.path.join(ms.source_text_dir(fresh), f"{s}.md"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write("")
    out = capture(ms.print_plan, ms.plan(fresh, "Langmuir", "draft"))
    check("r1 with nothing written does NOT print ADVISORY",
          "ADVISORY this round" in out, False, out)
    written = build_project(os.path.join(sub_tmp, "r3"))
    ms.init(written, "Langmuir")
    out = capture(ms.print_plan, ms.plan(written, "Langmuir", "draft"))
    check("a project with prose prints ADVISORY this round",
          "ADVISORY this round" in out, True, out)
    check("...and says --redraft is the only thing that rebuilds",
          "only thing that rebuilds a section" in out, True)

    # --- 21.5: --redraft all NAMES what it discards --------------------
    pl = ms.plan(written, "Langmuir", "draft", redraft=["all"])
    check("--redraft all on an unedited project says nothing of yours goes",
          "nothing of yours is being discarded" in pl["freeze_note"], True,
          pl["freeze_note"])

    # --- 20.7: the question at levels 4-5, and silence below -----------
    write(root, "drafts/rough_draft.md",
          "# Rough draft\n\n## Introduction\n\nArrays have been resolved "
          "in mesophiles but not above 60 C.\n")
    for level in (1, 2, 3):
        pl = ms.plan(root, "Langmuir", "draft", adherence=str(level))
        check("adherence %d asks nothing about the order" % level,
              pl["order_conflicts"], [],
              "at 1-3 the outline is a checklist and the draft's order stands")
    for level in (4, 5):
        pl = ms.plan(root, "Langmuir", "draft", adherence=str(level))
        rows = [c for c in pl["order_conflicts"]
                if c["section"] == "introduction"]
        check("adherence %d asks whose order wins" % level, len(rows), 1,
              pl["order_conflicts"])
        check("...with three options", len(rows[0]["options"]) if rows else 0,
              3)
        check("...the third being update the outline",
              "update plan/outline.md" in rows[0]["options"][2] if rows
              else False, True,
              "the one that is usually right and the one nobody thinks of")
    out = capture(ms.print_plan, ms.plan(root, "Langmuir", "draft",
                                         adherence="4"))
    check("...and the block prints it", "would reorder your paragraphs" in out,
          True, out)


# ---------------------------------------------------------------------------
# What is this round for - writing-engine 22
# ---------------------------------------------------------------------------

def _hash_tree(root: str) -> dict:
    """Every file under `root`, by content. What `plan` may not change."""
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in sorted(files):
            p = os.path.join(dirpath, f)
            try:
                with open(p, "rb") as fh:
                    out[os.path.relpath(p, root)] = hashlib.sha256(
                        fh.read()).hexdigest()
            except OSError:
                pass
    return out


def test_round_intent(tmp: str) -> None:
    section("what is this round for - the question a rerun opens with "
            "(spec 22)")

    sub_tmp = os.path.join(tmp, "intent")
    os.makedirs(sub_tmp, exist_ok=True)

    def capture(fn, *a):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn(*a)
        return buf.getvalue()

    root = build_project(sub_tmp)
    ms.init(root, "Langmuir")
    jdir = ms.journal_dir(root, "Langmuir")

    # (1) r1 DOES NOT ASK, and the r1 plan block is byte-identical to what
    # it was before this step. There is no *why are you running this again*
    # on the round that has nothing to run again, and the common first run
    # must not get noisier.
    pl = ms.plan(root, "Langmuir", "draft")
    check("r1 does not ask what the round is for",
          pl["intent"]["asked"], False, pl["intent"])
    out = capture(ms.print_plan, pl)
    check("...and prints no intent block at all",
          "What is this round for?" in out, False, out)
    bare = dict(pl)
    bare.pop("intent", None)
    check("...so the r1 block is byte-identical without it",
          out, capture(ms.print_plan, bare))

    # (2) r2 and later DO ask.
    with open(os.path.join(root, "project.yml"), "a", encoding="utf-8",
              newline="\n") as fh:
        fh.write("\nrevision: 4\n")
    ms.update_round_state(jdir, round=4)
    pl = ms.plan(root, "Langmuir", "draft")
    check("r4 asks", pl["intent"]["asked"], True)
    out = capture(ms.print_plan, pl)
    check("...and the block names the round",
          "This is r4. What is this round for?" in out, True, out)

    # (3) EVERY COUNT IS DERIVED. Nothing is stored, and a fixture that
    # gains a flag moves one number and not the other.
    def option(res, key):
        return [o for o in res["intent"]["options"] if o["intent"] == key][0]

    before_flags = option(pl, "flags")["count"]
    before_cites = option(pl, "citations")["count"]
    results = os.path.join(ms.source_text_dir(root), "results.md")
    write(root, "drafts/source_text/results.md",
          read(results) + "\nThe coating was thick **[FLAG: data - not in "
          "the stats output]**.\n")
    after = ms.plan(root, "Langmuir", "draft")
    check("adding a flag moves the flag count",
          option(after, "flags")["count"], before_flags + 1)
    check("...and does not move the citation count",
          option(after, "citations")["count"], before_cites,
          "nothing here is stored; each count is read from its own place")
    write(root, "drafts/source_text/results.md",
          read(results) + "\nAnd another claim **[FLAG: citation - nothing "
          "found]** stands.\n")
    after2 = ms.plan(root, "Langmuir", "draft")
    check("a citation flag moves the citation count too",
          option(after2, "citations")["count"], before_cites + 1)

    # (4) A ZERO-COUNT OPTION IS PRESENT, LAST, AND MARKED - never dropped.
    # Hiding it makes "there is nothing to apply" indistinguishable from
    # "this engine does not do that", and the first is a fact about their
    # project that they need.
    opts = after2["intent"]["options"]
    check("every intent is on the menu, whatever its count",
          sorted(o["intent"] for o in opts), sorted(ms.ROUND_INTENTS))
    zeros = [o["intent"] for o in opts if o["nothing_found"]]
    check("the fixture has at least one empty option", bool(zeros), True,
          [(o["intent"], o["count"]) for o in opts])
    positions = {o["intent"]: i for i, o in enumerate(opts)}
    check("...and every empty one is after every non-empty one",
          max(positions[z] for z in zeros)
          > max([positions[o["intent"]] for o in opts
                 if not o["nothing_found"]] or [-1]), True,
          [(o["intent"], o["nothing_found"]) for o in opts])
    out = capture(ms.print_plan, after2)
    check("...and the block marks it (nothing found)",
          "(nothing found)" in out, True, out)
    check("...and says it is still selectable",
          "still selectable" in out, True, out)

    # (5) The intent SETS the round: the preset, and the module list.
    picked = ms.plan(root, "Langmuir", "draft", intent=["citations"])
    check("`citations` narrows the round rather than adding a sentence",
          sorted(picked["modules"]), ["assemble", "citation-check"],
          "5's module-isolation argument at the granularity of a round")
    _order = {m: i for i, m in enumerate(["ingest"] + ms.MODULES)}
    check("...and in the pipeline's order, not a typed one",
          picked["modules"],
          sorted(picked["modules"], key=lambda m: _order.get(m, 99)))
    check("...and says what it narrowed away",
          bool(picked["intent"].get("narrowed_away")), True)
    check("...and states what it must not do",
          picked["intent"]["must_not"],
          ["change a sentence that is not a citation or its flag"])
    check("...and the estimate follows the module list",
          picked["agent_calls"], 1, picked["modules"])

    # (6) NO INTENT BUT `rearrange` MAY PRODUCE `compose` FOR A SECTION THAT
    # HAS PROSE, at every adherence level. This is 21's assertion restated
    # per intent, and it is what keeps step 5's work from being undone
    # through a new door.
    for key in ms.ROUND_INTENTS:
        for level in sorted(ms.ADHERENCE_LEVELS):
            res = ms.plan(root, "Langmuir", "draft", intent=[key],
                          adherence=str(level))
            recomposed = [m["section"] for m in res["draft_modes"]
                          if m["mode"] == "compose" and m["words"]]
            check("%s at adherence %d recomposes nothing that has prose"
                  % (key, level), recomposed, [])

    # `rearrange` still needs --redraft to name what it rebuilds, and says so.
    res = ms.plan(root, "Langmuir", "draft", intent=["rearrange"])
    check("`rearrange` alone recomposes nothing until sections are named",
          [m["section"] for m in res["draft_modes"]
           if m["mode"] == "compose" and m["words"]], [])
    check("...and says --redraft is how to name them",
          any("--redraft" in w for w in res["warnings"]), True,
          res["warnings"])
    res = ms.plan(root, "Langmuir", "draft", intent=["rearrange"],
                  redraft=["results"])
    check("named, it does recompose that one section",
          [m["section"] for m in res["draft_modes"]
           if m["mode"] == "compose" and m["words"]], ["results"])

    # (7) More than one intent: the union, in PIPELINE order, never typed
    # order. And rearrange plus anything else warns once and does not refuse.
    res = ms.plan(root, "Langmuir", "draft", intent=["citations", "apply"])
    order = {m: i for i, m in enumerate(["ingest"] + ms.MODULES)}
    check("two intents run in pipeline order, not the order typed",
          res["modules"], sorted(res["modules"],
                                 key=lambda m: order.get(m, 99)),
          "citations verified against prose a tracked change is about to "
          "replace is work thrown away")
    res = ms.plan(root, "Langmuir", "draft", intent=["rearrange", "apply"])
    check("rearrange plus apply warns", len(res["intent"]["warnings"]), 1,
          res["intent"]["warnings"])
    check("...and does not refuse", res["errors"], [])
    check("...naming the overlap",
          "about to discard" in res["intent"]["warnings"][0], True)

    # (8) The intent is RECORDED, and `something else` is verbatim.
    ms.plan(root, "Langmuir", "draft", intent=["apply"])
    check("the intent lands in round_state.json",
          (ms.round_state(jdir).get("intent") or {}).get("intents"),
          ["apply"])
    ms.plan(root, "Langmuir", "draft",
            intent=["cut the discussion to fit the limit"])
    rec = ms.round_state(jdir).get("intent") or {}
    check("an unlisted answer is `something else`", rec.get("intents"),
          ["something-else"])
    check("...and the user's own sentence is kept verbatim",
          rec.get("custom"), "cut the discussion to fit the limit")
    ms.log_round_heading(jdir, "Langmuir", 41, [], None, rec)
    log = read(ms.log_path(root))
    check("...and the log entry opens with it",
          '"cut the discussion to fit the limit"' in log, True, log[:600])
    ms.plan(root, "Langmuir", "draft", intent=["apply", "citations"])
    ms.log_round_heading(jdir, "Langmuir", 42, [], None,
                         ms.round_state(jdir).get("intent"))
    log = read(ms.log_path(root))
    check("a menu answer is written in the menu's own words",
          "Apply the changes, finish citations." in log, True, log[:600])

    # (9) THE ROUND BUMP IS OFFERED AND `plan` MOVES NO FILE. Measured by
    # hashing the journal folder before and after a plan that made the
    # offer and was not answered - the counter has exactly one mover, and
    # a `plan` that silently retired a round would be a gesture with a file
    # move hidden in it.
    subm = os.path.join(jdir, "submission")
    os.makedirs(subm, exist_ok=True)
    with open(os.path.join(subm, "manuscript_r4.docx"), "wb") as fh:
        fh.write(b"PK\x03\x04 not a real docx, but a built one on disk")
    offer = ms.round_bump_offer(root, "Langmuir")
    check("a built manuscript in submission/ is offered a round bump",
          offer["offer"], True, offer)
    check("...naming the round it would open", offer["next"], 5)
    check("...and the command that would do it, which is `round`",
          "round" in offer["command"], True, offer["command"])
    check("...and that declining is a real case",
          "--same-round" in offer["declining"], True)

    before_tree = _hash_tree(jdir)
    pl = ms.plan(root, "Langmuir", "draft")
    check("the offer reaches the plan block",
          pl["intent"]["bump"]["offer"], True)
    out = capture(ms.print_plan, pl)
    # Before the module list, because it decides it. The preset in that
    # line is whatever the recorded intent resolved to - a second `plan`
    # in the same round reuses the answer rather than re-asking, the same
    # way the adherence answer is reused.
    check("...and is printed before the module list",
          out.index("Opening r5 first") < out.index("Running "), True,
          out[:400])
    after_tree = _hash_tree(jdir)
    moved = sorted(set(before_tree) ^ set(after_tree))
    changed = sorted(k for k in set(before_tree) & set(after_tree)
                     if before_tree[k] != after_tree[k])
    check("PLAN MOVES NO FILE, even when it offers to", moved, [],
          "the counter has one mover and it is `round`")
    check("...and changes none either, except the state it is allowed to",
          [c for c in changed if not c.endswith("round_state.json")], [],
          changed)


def test_edit_authority(tmp: str) -> None:
    section("Whose sentence wins, and when the engine fixes things itself "
            "(specs/edit-authority.md)")

    # The word "edits" was doing two jobs with opposite lifetimes. The user's
    # own tracked changes are applied and SPENT - "by the time we get to r5,
    # there is no history of r2" - and a coauthor's return STANDS, because it
    # came from somebody who is not in this loop and will not see the paper
    # again for weeks. The engine saw both and had no rule that ranked them.
    fixture = os.path.join(FIXTURES, "tracked_two_authors.docx")
    if not os.path.isfile(fixture):
        skip("every edit-authority check", "tests/fixtures/ are missing; run "
                                           "tests/make_docx_fixtures.py")
        return

    root = build_project(os.path.join(tmp, "authority"))
    ms.init(root, "Langmuir")
    J = "Langmuir"
    jdir = ms.journal_dir(root, J)
    with open(os.path.join(root, "project.yml"), "a", encoding="utf-8",
              newline="\n") as fh:
        fh.write("\nrevision: 3\n")
    ms.update_round_state(jdir, round=3)

    edits = ms.edits_dir(jdir)
    subm = os.path.join(jdir, "submission")
    os.makedirs(edits, exist_ok=True)
    os.makedirs(subm, exist_ok=True)
    shutil.copy2(fixture, os.path.join(edits, "manuscript_r3_JV.docx"))
    shutil.copy2(fixture, os.path.join(subm, "manuscript_r3.docx"))

    # --- 1. the class survives ingest, and comes from WHERE THE FILE WAS --
    res = ms.ingest(root, J)
    coauthor = [i for i in res["items"] if i["authority"] == "coauthor"]
    own = [i for i in res["items"] if i["authority"] == "own"]
    check("a manuscript_rN_XX.docx in edits/ produces coauthor items",
          bool(coauthor), True)
    check("...and every one of them is credited to a person, not to the file",
          sorted({i["source"] for i in coauthor}), ["GJ", "JV"])
    check("the tracked changes in submission/manuscript_r3.docx are `own`",
          bool(own), True)
    check("...and are filed under the user rather than under an initial",
          sorted({i["source"] for i in own}), [ms.OWN_SOURCE])
    check("...with the file named in what was read",
          any(f["kind"] == "own" for f in res["files_read"]), True)
    check("the ladder is counted for the caller",
          (res["authority"]["coauthor"] > 0, res["authority"]["own"] > 0),
          (True, True))

    status = os.path.join(edits, "edits_status.md")
    text = read(status)
    check("the ledger carries the column, next to who sent it",
          "| id | rd | source | authority |" in text, True, text[:400])
    check("...and says what the two classes mean, in the file people read",
          "STANDS until the paper is submitted" in text, True)

    # A ledger written before the column existed still parses, and its rows
    # get the PROTECTED class rather than the cheap one.
    old = re.sub(r"^\| ([A-Za-z0-9]+-[A-Za-z]?\d+) \| (r\d+) \| ([^|]*) \| "
                 r"(own|coauthor) \|", r"| \1 | \2 | \3 |", text,
                 flags=re.M)
    write_raw(status, old)
    rows = ms._read_status(status)
    check("a ledger with no authority column still reads",
          len(rows) == len(res["items"]), True,
          (len(rows), len(res["items"])))
    check("...and a row with no class reads as coauthor, the protected one",
          sorted({r["authority"] for r in rows.values()
                  if r["source"] != ms.OWN_SOURCE}), ["coauthor"])
    ms.ingest(root, J)

    # --- 2. an `own` item is spent; a coauthor item never falls off -------
    text = read(status)
    text = text.replace("| pending |", "| applied |")
    write_raw(status, text)
    os.remove(os.path.join(subm, "manuscript_r3.docx"))
    os.remove(os.path.join(edits, "manuscript_r3_JV.docx"))
    ms.update_round_state(jdir, round=4)

    res4 = ms.ingest(root, J)
    check("r3's own markup is not carried into r4 - it was applied and is "
          "spent", [i for i in res4["items"] if i["authority"] == "own"], [])
    check("...and the round says so rather than dropping them silently",
          any("spent" in w for w in res4["warnings"]), True, res4["warnings"])
    check("a coauthor's item survives its file leaving the inbox",
          sorted({i["source"] for i in res4["items"]}), ["GJ", "JV"],
          "the ledger is the record, not the folder")
    check("...and none of r2's wording is retained anywhere",
          "r2" in read(status), False)

    # --- 3. a finding on coauthor text is a flag, never a rewrite ---------
    guard = ms.coauthor_guard(root, J)
    check("the guard names every standing coauthor item",
          guard["counts"]["items"], len(res4["items"]))
    check("...and resolves the initials against plan/authors.md",
          guard["authors"]["JV"]["name"], "June Vale")
    for mod in ms.COAUTHOR_GUARDED_MODULES:
        br = ms.agent_brief(root, mod, journal=J)
        p = br["prompt"]
        check("%s is told whose sentences outrank its findings" % mod,
              "WHOSE SENTENCES OUTRANK YOURS" in p, True)
        check("...and to raise [FLAG: edit-override] rather than rewrite",
              "**[FLAG: edit-override]**" in p, True)
        for part in ("who", "what", "instead", "why", "context"):
            check("...carrying the `%s` part of the question" % part,
                  re.search(r"^ {4}%s\s" % part, p, re.M) is not None, True)
        check("...and that the engine's version does not land while it is "
              "open", "DOES NOT LAND WHILE THE FLAG IS OPEN" in p, True)
        check("...and that a rule refused three times is waived",
              "three times over" in p, True)
    check("a module that cannot reach a coauthor's sentence is not given "
          "the guard",
          "WHOSE SENTENCES OUTRANK YOURS"
          in ms.agent_brief(root, "stats-check", journal=J)["prompt"], False)

    # --- 4. a replacement is kept; a request is carried out ---------------
    check("a marginal note licenses a rewrite of its anchor",
          "A COMMENT LICENSES A REWRITE OF WHAT IT IS ANCHORED TO AND "
          "NOTHING ELSE"
          in ms.agent_brief(root, "revise-prose", journal=J)["prompt"], True)
    for prose in ("Cryo-electron tomography has resolved this lattice.",
                  "Add 5 mL of buffer and incubate for 10 min.",
                  "Tilt series were collected from -60 to +60 degrees."):
        check("a tracked insertion that is prose is not read as an "
              "instruction", ms.reads_as_instruction(prose), False, prose)
    for order in ("reword this sentence", "Please clarify",
                  "shorten this paragraph", "Make this flow better"):
        check("...and one that is an instruction is",
              ms.reads_as_instruction(order), True, order)

    # --- 5. the fix passes run, and the ROUND no longer decides -----------
    # prose 12.2 removed the round from this. What was here asserted r1-runs
    # and r2-offers; the r2 offer was a per-ROUND guard sitting on top of the
    # per-SENTENCE coauthor guard this same spec built, so it bought nothing
    # and cost the readability of every section nobody had touched.
    r1 = os.path.join(tmp, "authority_r1")
    os.makedirs(r1, exist_ok=True)
    fresh = build_project(r1)
    ms.init(fresh, "Langmuir")
    res = ms.plan(fresh, "Langmuir", "draft")
    check("at r1 both fix passes RUN - the job of the first round is a draft "
          "that needs fewer rounds",
          (res["comprehension_fix"], res["quality_fix"]), ("auto", "auto"))
    check("...and the plan line says the AI-voice fix runs",
          "AI-voice fix runs in this round" in res["line"], True,
          res["line"][-500:])
    with open(os.path.join(fresh, "project.yml"), "a", encoding="utf-8",
              newline="\n") as fh:
        fh.write("\nrevision: 2\n")
    res = ms.plan(fresh, "Langmuir", "draft")
    check("at r2 both STILL run - a readability finding is fixed in the "
          "round that found it, and whose prose it is is answered per "
          "sentence by the coauthor guard, not per round by suppressing "
          "the fix",
          (res["comprehension_fix"], res["quality_fix"]), ("auto", "auto"))
    check("...and the plan still says which way it went",
          "acted on inside the round" in res["line"], True,
          res["line"][-500:])
    # The guard that replaced the round offer is the one to assert here,
    # because the reason the offer could go is that this exists.
    check("...and a coauthor's sentence is still never overwritten in "
          "silence, which is what makes running it safe",
          "revise-prose" in ms.COAUTHOR_GUARDED_MODULES, True)
    ms.configure(fresh, "Langmuir", sets=["comprehension_fix=off"])
    res = ms.plan(fresh, "Langmuir", "draft")
    check("a pass that does not run is NAMED in the plan",
          "comprehension_fix is off" in res["line"], True, res["line"][-500:])

    # --- 6. the offer block says what each step may overwrite -------------
    steps = res["intent"]["steps"]
    check("the r2 block lists the steps the round would run", bool(steps),
          True)
    check("...every one with an override line",
          all(s["overwrites"] for s in steps), True)
    check("...and a step that rewrites nothing says so rather than being "
          "left off",
          [s["step"] for s in steps if s["overwrites"] == "nothing - it "
           "reports"] != [], True, [(s["step"], s["overwrites"])
                                    for s in steps])
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        ms.print_plan(res)
    printed = out.getvalue()
    check("...and it is printed, not merely carried",
          "may overwrite:" in printed, True, printed[-800:])

    # THE LINE THAT MAKES IT WORTH BUILDING: the override names the ACTUAL
    # collision, with the initials and the count read off this project's own
    # ledger - not "may rewrite prose" in the abstract.
    res = ms.plan(root, J, "revision")
    steps = res["intent"]["steps"]
    rp = [s for s in steps if s["step"] == "revise-prose"][0]
    check("the override line names the coauthor and the count",
          ("Never a coauthor edit without asking" in rp["overwrites"]
           and "JV" in rp["overwrites"]), True, rp["overwrites"])
    fresh_steps = ms.plan(fresh, "Langmuir", "draft")["intent"]["steps"]
    fresh_rp = [s for s in fresh_steps if s["step"] == "revise-prose"][0]
    check("...and a project with no coauthor edits does not claim one",
          "coauthor edit" in fresh_rp["overwrites"], False,
          fresh_rp["overwrites"])


def test_apply_round_must_not_reaches_the_brief(tmp: str) -> None:
    section("A constraint the round block prints binds the modules the "
            "round runs (item 89)")

    # The defect: `must_not` was read in exactly one place, the printer. An
    # r3 coauthor round printed "It must not recompose any section" to the
    # terminal and then handed revise-prose, comprehension-check and
    # quality-check the whole paper. What is measured here is that the
    # sentence the user approved is the sentence the agent receives.
    root = build_project(os.path.join(tmp, "mustnot"))
    ms.init(root, "Langmuir")
    J = "Langmuir"
    jdir = ms.journal_dir(root, J)
    with open(os.path.join(root, "project.yml"), "a", encoding="utf-8",
              newline="\n") as fh:
        fh.write("\nrevision: 3\n")
    ms.update_round_state(jdir, round=3)

    # 2.1: the wording itself was wrong, not merely unenforced. A coauthor
    # note asking a paragraph to flow better IS a request to recompose, and
    # a round forbidden to recompose cannot honour the edits it exists for.
    check("`apply` forbids recomposing what no edit asked about, not "
          "recomposing at all",
          ms.ROUND_INTENTS["apply"]["must_not"],
          "recompose a section no edit asked it to touch")

    pl = ms.plan(root, J, "revision", intent=["apply"])
    must = pl["intent"]["must_not"]
    check("the plan still carries the sentence", must,
          ["recompose a section no edit asked it to touch"])
    printed = ms.round_constraints(jdir)["must_not"]
    check("...and it is read back from where the round recorded it",
          printed, must,
          "a module spawned in a later invocation reads the same sentence")

    # The verify-by, in one line: every generating module the round runs.
    generating = [m for m in pl["modules"]
                  if m in ms.AGENT_MODULES
                  and any("source_text" in (w[0] if isinstance(w, tuple)
                                            else w)
                          for w in ms.AGENT_MODULES[m]["writes"])]
    check("the round runs more than one module that writes prose",
          len(generating) > 1, True, generating)
    for mod in generating:
        br = ms.agent_brief(root, mod, journal=J)
        check("%s is handed the sentence, not only the terminal" % mod,
              must[0] in br["prompt"], True)
        check("...and told it is what the user approved",
              "what they approved" in br["prompt"], True)
        check("...and carried beside the prompt too",
              br["round_intent"]["must_not"], must)

    # A rule its reader cannot act on teaches the reader to skim the rules.
    br = ms.agent_brief(root, "stats-check", journal=J)
    check("a module that writes no prose is not handed a prose constraint",
          must[0] in br["prompt"], False)

    # And a round with no stated intent carries no constraint at all - the
    # block is silent rather than inventing one.
    ms.update_round_state(jdir, intent={})
    br = ms.agent_brief(root, "revise-prose", journal=J)
    check("a round that stated no intent adds no constraint block",
          "WHAT THIS ROUND IS FOR" in br["prompt"], False)


# ---------------------------------------------------------------------------
# flag answers - specs/flag-resolver.md
# ---------------------------------------------------------------------------

def test_flag_answers(tmp: str) -> None:
    section("answers to flags, and where they have to survive to "
            "(specs/flag-resolver.md)")

    sub_tmp = os.path.join(tmp, "flaganswers")
    os.makedirs(sub_tmp, exist_ok=True)

    root = build_project(sub_tmp)
    ms.init(root, "Langmuir")
    jdir = ms.journal_dir(root, "Langmuir")
    write(root, "drafts/source_text/results.md",
          "# Results\n\nThe coating was about 40 nm thick **[FLAG: data - "
          "40 nm is your figure from drafts/rough_draft.md; it is not in "
          "analysis_stats_output.md]**, which places it above the "
          "percolation threshold.\n\nThe IRB number is missing "
          "**[FLAG: author - which protocol number?]**.\n")

    live = prose_engine().flags(prose_engine().source_files(root))["flags"]
    # Picked by TYPE and not by a dict keyed on it: the fixture already
    # carries an `author` flag in methods.md, so two flags of one type
    # is the normal case - which is exactly why the id is hashed from
    # the body and the section rather than from the type.
    data_id = [f["id"] for f in live if f["type"] == "data"][0]
    check("the fixture carries three flags", len(live), 3,
          [(f["type"], f["location"]) for f in live])
    check("...each with an id", all(f.get("id") for f in live), True)
    check("...and the two author flags have DIFFERENT ids",
          len({f["id"] for f in live if f["type"] == "author"}), 2,
          "the hash is over the body and the section, not the type")

    # (1) The answers land in edits_status.md with type `flag`, state
    # pending - never in reports/, for the mechanical reason that reports/
    # retires in the same gesture that opens the round meant to consume them.
    res = ms.record_flag_answers(
        root, "Langmuir",
        [{"id": data_id,
          "answer": "coating thickness is 40 nm, SEM 2026-03-14, notebook "
                    "p.72"}],
        source="RW")
    check("an answer is recorded", len(res["recorded"]), 1, res)
    row = res["recorded"][0]
    check("...with type flag", row["type"], "flag")
    check("...as pending, which is what \"applied next round\" already means",
          row["state"], "pending")
    check("...carrying the flag id, so r5 can find it after the prose moves",
          data_id in row["note"], True, row["note"])
    check("...in edits_status.md",
          res["path"].replace(os.sep, "/").endswith(
              "edits/edits_status.md"), True, res["path"])
    ledger = read(res["path"])
    check("the row is in the file", "| flag |" in ledger, True, ledger[-900:])
    check("...and the rollup counts it",
          "RW" in ledger, True)
    check("nothing under reports/ was written",
          os.path.isdir(os.path.join(jdir, "reports"))
          and any("flag" in f.lower()
                  for _d, _s, fs in os.walk(os.path.join(jdir, "reports"))
                  for f in fs), False,
          "reports/rN/ retires in the gesture that opens the round meant to "
          "apply the answers")

    # (2) THE RETIREMENT TEST. This is the assertion that would have caught
    # the obvious wrong answer, and it is the whole reason the answers go
    # where they go: a `round` between the resolver run and the writer run
    # has to leave them readable.
    before = ms.flag_answers(root, "Langmuir")
    check("the answer is readable before the round", before["unapplied"], 1)
    ms.open_round(root, "Langmuir")
    after = ms.flag_answers(root, "Langmuir")
    check("A ROUND BETWEEN THE RESOLVER AND THE WRITER LEAVES THE ANSWERS "
          "READABLE", after["unapplied"], 1,
          "edits_status.md does not retire; reports/rN/ does")
    check("...and the row is the same row",
          [a["id"] for a in after["answers"]],
          [a["id"] for a in before["answers"]])

    # (3) An answer whose flag is gone is ORPHANED - reported, never dropped
    # and never guessed onto the nearest flag.
    res = ms.record_flag_answers(
        root, "Langmuir",
        [{"id": "deadbe", "answer": "this one no longer exists"}],
        source="RW")
    check("an answer for a flag that is not there is orphaned",
          [o["id"] for o in res["orphaned"]], ["deadbe"])
    check("...and is not recorded against any other flag",
          res["recorded"], [])

    # (4) `completeness` and the plan block report unapplied answers, and
    # report NOTHING when there are none.
    comp = ms.completeness(root, "Langmuir")
    hits = [m for m in comp["missing"] if "not yet applied" in m["detail"]]
    check("completeness reports the unapplied answer", len(hits), 1,
          comp["missing"])
    check("...as a gap rather than a block",
          hits[0]["severity"] if hits else "", "gap")
    pl = ms.plan(root, "Langmuir", "draft")
    check("the plan block reports it too",
          "not yet applied" in (pl["flag_answers"] or ""), True,
          pl["flag_answers"])
    check("...and says an answered flag is still a flag",
          "still a flag" in (pl["flag_answers"] or ""), True)
    clean = build_project(os.path.join(sub_tmp, "clean"))
    ms.init(clean, "Langmuir")
    check("a project with no answers says nothing at all",
          ms.plan(clean, "Langmuir", "draft")["flag_answers"], "",
          "the common case does not get noisier")

    # (5) final-check stops on an item marked `applied` whose flag is still
    # in the prose. The check is on THE MANUSCRIPT, not on the ledger.
    path = os.path.join(ms.edits_dir(jdir), "edits_status.md")
    # The ROW, not the first "| pending |" in the file - the rollup table
    # at the top of edits_status.md carries the word too.
    write_raw(path, "\n".join(
        ln.replace("| pending |", "| applied |")
        if ln.startswith("| RW-F1 ") else ln
        for ln in read(path).splitlines()) + "\n")
    fa = ms.flag_answers(root, "Langmuir")
    check("an applied answer whose flag is still in the prose is a conflict",
          len(fa["conflicts"]), 1, fa)
    comp = ms.completeness(root, "Langmuir")
    blocking = [m for m in comp["missing"]
                if m["severity"] == "blocking" and "applied" in m["detail"]]
    check("...and it BLOCKS", len(blocking), 1, comp["missing"])
    check("...saying the paper is the one to believe",
          "the one to believe" in blocking[0]["detail"] if blocking else False,
          True)

    # (6) A project with no journal folder gets the walk-through and an
    # explicit "nowhere to record this yet", and creates no folder.
    bare = build_project(os.path.join(sub_tmp, "bare"))
    before_tree = sorted(os.listdir(os.path.join(bare, "drafts")))
    res = ms.record_flag_answers(bare, "Nowhere",
                                 [{"id": "abc123", "answer": "x"}])
    check("no journal folder is refused, not invented", bool(res["errors"]),
          True)
    check("...and says the walk-through still works",
          "walk-through still works" in " ".join(res["errors"]), True,
          res["errors"])
    check("...and no folder was created",
          sorted(os.listdir(os.path.join(bare, "drafts"))), before_tree)


def test_outline_regions(tmp: str) -> None:
    section("outline.md's two regions, and the notes as a source")

    sub_tmp = os.path.join(tmp, "regions")
    os.makedirs(sub_tmp, exist_ok=True)
    root = build_project(sub_tmp)

    # (1) both regions are visible on opening, without reading a comment.
    text = read(os.path.join(root, "plan", "outline.md"))
    # The first screenful. Both regions have to be visible on opening with
    # no HTML comment needed to tell them apart, and the scaffolded Structure
    # region carries three empty sections between them.
    head = "\n".join(text.splitlines()[:20])
    check("the file opens with two headed regions",
          ("# Structure" in head, "# Notes" in head), (True, True), head)
    check("...each with its rule in plain text, not in a comment",
          ("one line per paragraph" in head.lower(),
           "not organized yet" in head.lower()), (True, True), head)

    # (2) the notes region is a primary source, and names its bullets.
    write(root, "plan/outline.md",
          "# Structure\n\n## Introduction\n\n-\n\n## Results\n\n-\n\n"
          "# Notes — anything, in any order\n\n"
          "*Anything you have not organized yet; nothing here is ever "
          "treated as a paragraph.*\n\n"
          "- the low-salt condition gave n = 1 after several failed replicates\n"
          "- oxide peak at 41.7 deg in every high-salt sample\n"
          "- the 620 K hold is where the framework collapses\n"
          "- BET surface area drops 3x on decomposition\n"
          "- the reduced metal appears before the framework is gone\n"
          "- the high- and low-salt series diverge above 550 K\n")
    ol = ms._prose(root, "outline")
    check("the notes bullets are read as notes, not as paragraphs",
          (ol["counts"]["outline_lines"], ol["counts"]["note_bullets"]),
          (0, 6))
    src = ms.outline_sources(root)
    notes = [s for s in src["sources"]
             if s["name"] == "plan/outline.md ## Notes"]
    check("the notes region is in the inventory", len(notes), 1,
          "a user who did exactly what the template invites had written into "
          "the one file the outline builder did not read")
    check("...as a primary source", notes and notes[0]["weight"], "primary")
    check("...naming the bullets it found",
          notes and "oxide peak at 41.7 deg" in notes[0]["detail"], True,
          notes[0]["detail"] if notes else "the notes region was not found")
    check("...so a project whose only content is notes can be outlined from",
          src["can_draft"], True,
          "it reported 'Nothing to build an outline from'")

    # (3) a round can be scoped to sections.
    ms.init(root, "Langmuir")
    jdir = os.path.join(root, "drafts", "Langmuir")
    res = ms.plan(root, "Langmuir", "draft",
                  sections=["introduction", "methods"])
    check("--sections scopes the round",
          res["sections_scope"], ["introduction", "methods"])
    check("...and what is left out is out of scope, not missing",
          res["sections_out_of_scope"],
          ["title_abstract", "results", "discussion"])
    check("...and the plan line says so",
          "out of this round's scope" in res["line"], True, res["line"])
    check("an unknown section name is reported, not silently accepted",
          any("is not a section file" in w for w in
              ms.plan(root, "Langmuir", "draft",
                      sections=["intro"])["warnings"]), True)

    scope = ms.record_round_scope(jdir, ["introduction", "methods"])
    check("the scope is recorded where every later module reads it",
          (scope, ms.round_scope(jdir)),
          (["introduction", "methods"], ["introduction", "methods"]))

    if shutil.which("pandoc"):
        # results.md and discussion.md exist in this fixture; scoping the
        # round must not make them gaps, and must not refuse the build.
        os.remove(stfile(root, "results.md"))
        built = ms.assemble(root, "Langmuir")
        check("a scoped round does not refuse over a section outside it",
              built["errors"], [], str(built["errors"]))
        check("...and says what it is scoped to",
              any("out of this round's scope" in w.lower()
                  for w in built["warnings"]), True, str(built["warnings"]))
        check("...and log.md's entry names the round, the journal and the scope",
              [ln for ln in read(ms.log_path(root)).splitlines()
               if ln.startswith("## r")][:1],
              ["## r1 - %s - %s" % (ms.journal_folder("Langmuir"), ms.TODAY)])
        check("...with the scope under it",
              "Scoped to introduction, methods" in read(
                  ms.log_path(root)), True)


def test_submission_package(tmp: str) -> None:
    section("The submission package is written, not merely promised")

    sub_tmp = os.path.join(tmp, "package")
    os.makedirs(sub_tmp, exist_ok=True)
    root = build_project(sub_tmp)
    ms.init(root, "Langmuir")
    jdir = os.path.join(root, "drafts", "Langmuir")
    sdir = os.path.join(jdir, "submission")
    reqp = os.path.join(jdir, "journal_requirements", "requirements.yml")
    write(root, os.path.relpath(reqp, root),
          read(reqp).replace("  cover_letter: unknown",
                             "  cover_letter: required"))

    res = ms.submission_package(root, "Langmuir")
    check("submission-package runs at all", res["errors"], [],
          "it was named in MODULES, was the last step of the submission "
          "preset, had a README describing its five files, and had no "
          "subparser and no function")
    names = sorted(os.path.basename(w["path"]) for w in res["written"])
    check("all five files are written",
          [n for n in ("cover_letter_r1.docx", "title_page_r1.docx",
                       "statements_r1.md", "suggested_reviewers_r1.md",
                       "checklist_r1.md") if n not in names], [],
          str(names))
    check("...into submission/, round-suffixed, beside the manuscript",
          all(os.path.isfile(os.path.join(root, w["path"]))
              for w in res["written"]), True, str(res["written"]))
    check("...and none of them is empty",
          [w["path"] for w in res["written"]
           if os.path.getsize(os.path.join(root, w["path"])) < 40], [])

    letter = read(os.path.join(sdir, "cover_letter_r1.md"))
    check("a required cover letter names the requirement it was written to",
          letter.splitlines()[0],
          "# Cover letter - %s: required" % ms.journal_folder("Langmuir"))
    check("...and is a letter, not a stub",
          "Dear " in letter and "Sincerely," in letter, True)
    revs = read(os.path.join(sdir, "suggested_reviewers_r1.md"))
    check("a file the journal did not ask for is written anyway, as a template",
          "template" in revs.splitlines()[0], True, revs.splitlines()[0])
    check("...and still lists the conflicts the engine can check",
          "coauthor: Rowan Wills" in revs, True)
    checklist = read(os.path.join(sdir, "checklist_r1.md"))
    check("the checklist ticks what is sourced",
          "- [x] " in checklist, True)
    check("...and flags what is unknown",
          "- [ ] " in checklist and "**[FLAG: journal]**" in checklist, True)
    # Item 22's rule is "not one flag per row", and it still holds: the
    # aggregate flag stands in for however many unsourced fields there are.
    # The AI-disclosure row is the one exception, and it is a different ask
    # rather than one more row - spec 17.6 requires `unknown` to FAIL that row
    # on its own, because "nobody looked" and "not required" are exactly what
    # it exists to keep apart. Two, never ninety-three.
    check("...with an aggregate flag rather than one per row",
          checklist.count("**[FLAG:") <= 2, True,
          "ninety flags saying the same thing devalue the ones that do not")
    check("...and one of them is the AI-disclosure row",
          "AI disclosure - **[FLAG: journal]**" in checklist, True)
    check("a package with outstanding flags says it is not sendable",
          (res["flags"] > 0, res["sendable"]), (True, False))

    # And the round follows the manuscript's, so `round` retires the package
    # with the round it belonged to (item 17).
    if shutil.which("pandoc"):
        built = ms.assemble(root, "Langmuir")
        if not built["errors"]:
            ms.open_round(root, "Langmuir")
            check("the package retires with its round",
                  os.path.isfile(os.path.join(
                      root, "obsolete/drafts/r1/submission/checklist_r1.md")),
                  True)
            check("...leaving submission/ empty for the next one",
                  [f for f in os.listdir(sdir)
                   if not f.startswith(".") and f != "README.md"], [],
                  "the README explains the folder and is not a round's "
                  "artefact, so it stays")
            again = ms.submission_package(root, "Langmuir")
            # By NAME, not by position: `ai_disclosure_rN.md` sorts before
            # `checklist_rN.md`, so indexing the sorted list broke the moment
            # the package gained a sixth file.
            check("...and the next package is r2",
                  next(os.path.basename(w["path"]) for w in again["written"]
                       if "checklist" in w["path"]),
                  "checklist_r2.md")


def test_round_counter_is_the_projects(tmp: str) -> None:
    section("The round counter is the project's, not the journal folder's")

    sub_tmp = os.path.join(tmp, "counter")
    os.makedirs(sub_tmp, exist_ok=True)
    root = build_project(sub_tmp)
    ms.init(root, "Langmuir")
    jdir = os.path.join(root, "drafts", "Langmuir")

    # Walk Langmuir up to r7 without building: `round` is what moves it.
    write(root, "drafts/Langmuir/submission/manuscript_r1.docx", "not a docx")
    for _ in range(6):
        ms.open_round(root, "Langmuir")
        rnd = ms.current_round(jdir)
        write(root, "drafts/Langmuir/submission/manuscript_r%d.docx" % rnd,
              "not a docx")
    check("Langmuir is at r7", ms.current_round(jdir), 7)
    check("...and project.yml's counter agrees",
          ms.project_revision(root), 7,
          "it counted globally already and nothing ever read it back")

    res = ms.init(root, "JPCC")
    jpcc = os.path.join(root, "drafts", "JPCC")
    check("a new journal opens at the next round, not at r1",
          ms.current_round(jpcc), 8,
          "a folder-local counter gave one project two files called "
          "manuscript_r1.docx meaning different things")
    check("...and init says so", res["round"], 8)
    check("target_journal follows the open round",
          ms.read_project_yml(root).get("target_journal"), "JPCC",
          "it used to stay behind, which is wrong once the engine has to "
          "know which journal r8 belongs to without being told")
    check("...and says it moved",
          any("target_journal" in a["detail"] and "was" in a["detail"]
              for a in res["actions"]), True,
          str([a["detail"] for a in res["actions"]]))
    ledger = ms.rounds_ledger(root)
    was = ms.journal_folder("Langmuir")
    check("project.yml records which journal each round was",
          [(a, b, j) for a, b, j in ledger if j == "JPCC"], [(8, 8, "JPCC")],
          str(ledger))
    check("...and where the boundary is",
          [(a, b) for a, b, j in ledger if j == was], [(1, 7)],
          str(ledger))
    check("no manuscript_r1.docx appears in the new journal",
          glob.glob(os.path.join(jpcc, "submission", "manuscript_r1.docx")),
          [])

    if shutil.which("pandoc"):
        built = ms.assemble(root, "JPCC")
        if not built["errors"]:
            check("...and the build is r8",
                  os.path.basename(built["output"]), "manuscript_r8.docx")


def test_revision_is_scoped_to_what_came_back(tmp: str) -> None:
    section("A revision round redrafts what was asked for, not the paper")

    sub_tmp = os.path.join(tmp, "revscope")
    os.makedirs(sub_tmp, exist_ok=True)
    root = build_project(sub_tmp)
    ms.init(root, "IJROBP")

    # No ledger at all: nothing says what the revision is for, and the plan
    # has to say that rather than redraft five sections on nobody's request.
    res = ms.plan(root, "IJROBP", "revision")
    check("with no edits_status.md the plan says so",
          "nothing says what this revision is for" in res["scope_note"], True,
          res["scope_note"])

    # A ledger with nothing pending. The coauthor round is closed; there is
    # no redraft to do.
    header = ("# Edit status\n\n"
              "| id | rd | source | location | type | request | state | note "
              "| fp |\n|---|---|---|---|---|---|---|---|---|\n")
    write(root, "drafts/edits/edits_status.md",
          header + "| JV-1 | r1 | JV | Order tracks growth temperature | "
                   "edit | tighten | applied |  | 0123456789 |\n")
    res = ms.plan(root, "IJROBP", "revision")
    check("nothing pending takes the redraft out of the round",
          "draft-sections" in res["modules"], False,
          "every section was rewritten on every revision round regardless of "
          "what ingest found")
    check("...and says why", "nothing is pending" in res["scope_note"], True,
          res["scope_note"])
    check("...and the line offered is the one it would run",
          "draft-sections" in res["line"].split("Skipping")[0], False,
          res["line"])

    # One pending item, and its recorded sentence is in the discussion.
    write(root, "drafts/edits/edits_status.md",
          header + "| JV-1 | r1 | JV | Order tracks growth temperature rather "
                   "than phylogeny | edit | temper this | pending |  | "
                   "0123456789 |\n")
    pend = ms.pending_edits(root, "IJROBP")
    check("the pending row is traced to its section", pend["sections"],
          ["discussion"], str(pend))
    res = ms.plan(root, "IJROBP", "revision")
    check("draft-sections is scoped to that section",
          res["sections_scope"], ["discussion"])
    check("...and the plan line names it and no other",
          "draft-sections(discussion)" in res["line"], True, res["line"])
    check("...and draft-sections is still in the round",
          "draft-sections" in res["modules"], True)

    # A row whose sentence matches nothing. Narrowing on incomplete
    # information is how a request goes missing, so the round is not scoped.
    write(root, "drafts/edits/edits_status.md",
          header + "| R1-1 | r1 | R1 | a sentence that is in no source file | "
                   "request | do something | pending |  | 0123456789 |\n")
    res = ms.plan(root, "IJROBP", "revision")
    check("an untraceable pending item leaves the round unscoped",
          res["sections_scope"], [])
    check("...and says how many it could not place",
          "1 of 1 pending edits could not be traced" in res["scope_note"],
          True, res["scope_note"])
    check("...with the redraft still in it",
          "draft-sections" in res["modules"], True)

    # Other presets are untouched: this is a revision-round rule.
    res = ms.plan(root, "IJROBP", "draft")
    check("a draft round is not scoped by the edits ledger",
          (res["scope_note"], res["sections_scope"]), ("", []))


def test_a_review_builds(tmp: str) -> None:
    section("A review builds, and its summary is the document (items 9, 14, "
            "90)")

    if not shutil.which("pandoc"):
        skip("every review-build check", "pandoc is not on PATH")
        return

    root = os.path.join(tmp, "review_build")
    run = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "scaffold.py"),
         "scaffold", root, "--title", "Monolayer stability",
         "--field", "chemistry", "--paper-kind", "review"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if run.returncode != 0:
        skip("every review-build check", "scaffold failed")
        return

    write(root, "plan/outline.md",
          "# Structure\n\n## Introduction\n\n- Why the ceiling matters "
          "[]\n\n## Monolayer Formation\n\n- How they form []\n\n"
          "## Defects and Ordering\n\n- What goes wrong []\n\n# Notes\n")
    ms.init(root, "Langmuir")

    # Item 90: the skeleton init wrote must not name sections this paper kind
    # does not have. The IMRaD five resolve to no file on a review, so gate 0
    # refused every review at its first build - correctly, and the remedy it
    # named was editing a file the engine had just written wrongly.
    req = read(os.path.join(root, "drafts", "Langmuir",
                            "journal_requirements", "requirements.yml"))
    check("a review is not scaffolded with an IMRaD section order",
          "section_order: [title_abstract, introduction" in req, False)
    check("...it is unsourced, which is the honest value",
          "section_order: unknown" in req, True)

    write(root, "drafts/source_text_r1/title_abstract.md",
          "# The thermal ceiling on alkanethiol monolayers\n\n## Abstract"
          "\n\nNobody can say which monolayers survive above 400 K, and "
          "every device built on one inherits the uncertainty.\n\n"
          "## Keywords\n\nmonolayers, desorption\n")
    for stem, head, body in (
            ("01_introduction", "Introduction",
             "Nobody can say which monolayers survive above 400 K. Three "
             "groups disagree by a factor of two."),
            ("02_monolayer_formation", "Monolayer Formation",
             "Formation proceeds in two stages, and the second is slow."),
            ("03_defects_and_ordering", "Defects and Ordering",
             "Domain boundaries dominate the defect population.")):
        write(root, "drafts/source_text_r1/%s.md" % stem,
              "# %s\n\n%s\n" % (head, body))

    res = ms.assemble(root, "Langmuir")
    check("a review builds with no edit to requirements.yml",
          res["errors"], [])
    if res["errors"]:
        return

    heads = [h["text"] for h in ms._headings(
        ms._docx_part(res["output"], "word/document.xml"))
        if h["level"] == "1"]
    reported = [x for x in res["sections"]
                if ms._sec_key(x) not in ("title", "author_list", "abstract",
                                          "title_abstract")]
    # Item 9, by the route that reopened it. The summary used to name the
    # STEM - `02_monolayer_formation` - while the document carried the
    # heading the file itself wrote, so the two disagreed on every section of
    # every review, and the summary is the only place a user sees an order.
    check("the summary names the sections the document carries, in order",
          reported, heads)
    check("...by the name a reader sees, not the stem",
          "Monolayer Formation" in res["sections"], True, str(res["sections"]))
    check("...and the engine's own detector agrees",
          [i["code"] for i in res["engine_issues"]
           if i["code"] == "summary_not_the_document"], [])

    # Item 14, by the same route: prose.py length crashed on every review, so
    # the build's second word count was simply absent and the detector fired
    # on a build that was otherwise fine.
    check("both word counts survive a review",
          (isinstance(res["words"], int),
           isinstance(res["prose_words"], int)), (True, True))
    check("...and each says what it counted",
          ("rendered document" in (res["words_counted"] or ""),
           "source_text" in (res["prose_words_counted"] or "")),
          (True, True))
    check("...so that detector is quiet too",
          [i["code"] for i in res["engine_issues"]
           if i["code"] == "word_counts_unexplained"], [])


def test_summary_matches_the_document(tmp: str) -> None:
    section("The summary's section list is the document's own order")

    if not shutil.which("pandoc"):
        skip("every summary-vs-document check", "pandoc is not on PATH")
        return

    root = _scaffold(tmp, "summary_case")
    if not root:
        skip("every summary-vs-document check", "scaffold failed")
        return
    for stem, body in (("introduction", "A."), ("methods", "B."),
                       ("results", "C."), ("discussion", "D.")):
        write(root, "drafts/source_text/%s.md" % stem,
              "# %s\n\n%s\n" % (stem.title(), body))
    write(root, "drafts/source_text/title_abstract.md", TITLE_MD)
    write(root, "drafts/source_text/authors.md", AUTHORS_MD)
    ms.init(root, "Langmuir")
    reqp = os.path.join(root, "drafts", "Langmuir", "journal_requirements",
                        "requirements.yml")
    original = read(reqp)
    SCAFFOLDED = ("section_order: [title_abstract, introduction, methods, "
                  "results, discussion]")

    def build(order: str) -> dict:
        # Conflict of Interest required and NOT named in any order below:
        # that is the case where the build inserts a section the order does
        # not mention, which is the case the summary got wrong.
        write(root, os.path.relpath(reqp, root),
              original.replace(SCAFFOLDED, order)
                      .replace("conflict_of_interest: unknown",
                               "conflict_of_interest: required"))
        return ms.assemble(root, "Langmuir")

    # Item 9, measured in the direction that made it visible: Conflict of
    # Interest is required and is NOT in the order, so it is inserted before
    # the references. The summary used to report it appended after everything.
    res = build("section_order: [Title, Author list, Abstract, Introduction, "
                "Experimental Section, Results and Discussion, "
                "Supporting Information, References, Author Information, "
                "Funding, Acknowledgment]")
    if res["errors"]:
        skip("every summary-vs-document check", "; ".join(res["errors"]))
        write(root, os.path.relpath(reqp, root), original)
        return
    heads = [h["text"] for h in ms._headings(
        ms._docx_part(res["output"], "word/document.xml"))
        if h["level"] == "1"]
    reported = [s for s in res["sections"]
                if ms._sec_key(s) not in ("title", "author_list", "abstract",
                                          "title_abstract")]
    # Item 149 reversed the old answer here. It was inserted as a heading of
    # its own before the references; a heading the journal's order does not
    # list is a section an editor screens out, so it is left out and NAMED -
    # in the build result, and in completeness, which is what puts it in the
    # PAPER NOT COMPLETE block.
    check("149: a statement the journal's order has no slot for gets no "
          "heading", ("Conflict of Interest" in heads,
                      res.get("statements_unplaced")),
          (False, ["Conflict of Interest"]), str(heads))
    check("149: ...and completeness names it, so it cannot vanish quietly",
          any("Conflict of Interest is required" in m["detail"]
              for m in ms.completeness(root, "Langmuir")["missing"]), True)
    check("...and the summary puts it where the document does",
          reported, heads,
          "the summary reported inserted statements appended after "
          "everything, on every build that had one")
    if "Conflict of Interest" in heads and "References" in heads:
        check("...which is before the references, as ACS lays it out",
              heads.index("Conflict of Interest") < heads.index("References"),
              True, str(heads))

    # And in the opposite direction: an order that ends at References, with
    # no statement named at all, puts every statement before the references.
    res = build("section_order: [Title, Author list, Abstract, Introduction, "
                "Experimental Section, Results and Discussion, References]")
    if not res["errors"]:
        heads = [h["text"] for h in ms._headings(
            ms._docx_part(res["output"], "word/document.xml"))
            if h["level"] == "1"]
        reported = [s for s in res["sections"]
                    if ms._sec_key(s) not in ("title", "author_list",
                                              "abstract", "title_abstract")]
        check("an order naming no statement reports the document's order too",
              reported, heads)

    # Item 14: the build's word count and prose.py length's are different
    # numbers counting different things, and the build line now says which.
    check("the build says what its own word count counted",
          "rendered document" in (res.get("words_counted") or ""), True,
          repr(res.get("words_counted")))
    check("...and carries prose.py length's number beside it",
          isinstance(res.get("prose_words"), int), True,
          repr(res.get("prose_words")))
    check("...with that number's own scope stated",
          "source_text" in (res.get("prose_words_counted") or ""), True,
          repr(res.get("prose_words_counted")))
    check("...and they really are different counts of the same build",
          res["prose_words"] < res["words"], True,
          "%s vs %s" % (res.get("prose_words"), res.get("words")))

    # Item 11: a flag written across two paragraphs is not bold - pandoc
    # passes the ** through as text. No paragraph of the .docx may carry it.
    check("a clean build has no literal markdown in it",
          res.get("markdown_residue"), [])
    write(root, "drafts/source_text/discussion.md",
          "# Discussion\n\n**[FLAG: data — the replicate failed, so this\n\n"
          "type has n = 1. See the Results.]**\n\nD.\n")
    res = build("section_order: [Title, Author list, Abstract, Introduction, "
                "Experimental Section, Results and Discussion, References]")
    if not res["errors"]:
        text = "\n".join(t for _, t in paragraphs(
            ms._docx_part(res["output"], "word/document.xml")))
        check("a flag spanning paragraphs leaves literal ** in the document",
              "**" in text, True,
              "if pandoc has started rendering this, the check below is the "
              "one that matters and this one can go")
        check("...and the build says so, naming how many paragraphs",
              any("literal markdown" in w for w in res["warnings"]), True,
              str(res["warnings"]))
        check("...and files it against the engine, not the draft",
              "markdown_on_the_page" in
              [i["code"] for i in res.get("engine_issues") or []], True,
              str(res.get("engine_issues")))
        check("...and reports the paragraphs it found",
              len(res.get("markdown_residue") or []) >= 1, True)
    write(root, "drafts/source_text/discussion.md", "# Discussion\n\nD.\n")
    write(root, os.path.relpath(reqp, root), original)


def test_title_block_and_end_matter(tmp: str) -> None:
    section("The title block is metadata, and the end matter is generated")

    root = _scaffold(tmp, "front_case")
    if not root:
        skip("every front/end matter check", "scaffold failed")
        return
    for stem, body in (("introduction", "A."), ("methods", "B."),
                       ("results", "C."), ("discussion", "D.")):
        write(root, "drafts/source_text/%s.md" % stem,
              "# %s\n\n%s\n" % (stem.title(), body))
    write(root, "drafts/source_text/title_abstract.md", TITLE_MD)
    write(root, "drafts/source_text/authors.md", AUTHORS_MD)

    meta, info, gaps = ms.front_matter(root)
    check("the title is the text under the label, not the label",
          info["title"],
          "Tin doping halves the NO desorption barrier on Pt(100)",
          "`# Title` came through as a Heading1 with the real title as body "
          "text under it")
    check("...and it reaches pandoc as metadata",
          "title: " in meta, True)
    check("both authors are in the byline",
          [n for n in ("Ada Bell", "Cyd Doyle") if n not in meta], [])
    check("...with the corresponding author marked",
          "*" in meta and "cd@example.edu" in meta, True)
    check("nothing is missing from a complete front matter", gaps, [])

    ms.init(root, "Langmuir")
    reqp = os.path.join(root, "drafts", "Langmuir", "journal_requirements",
                        "requirements.yml")
    original = read(reqp)
    write(root, os.path.relpath(reqp, root),
          original.replace(
              "section_order: [title_abstract, introduction, methods, "
              "results, discussion]",
              "section_order: [Title, Author list, Abstract, Introduction, "
              "Experimental Section, Results and Discussion, "
              "Acknowledgment, Conflict of Interest, Funding, References]"))
    res = ms.assemble(root, "Langmuir")
    if res["errors"]:
        skip("the built title block", "; ".join(res["errors"]))
        write(root, os.path.relpath(reqp, root), original)
        return

    doc = ms._docx_part(res["output"], "word/document.xml")
    styles = ms._para_styles(doc)
    heads = ms._headings(doc)
    check("the title is set in Title", "Title" in styles, True,
          "format-check asked for a style no build could produce")
    check("the byline is set in Author", "Author" in styles, True)
    check("the abstract gets its own style", "Abstract" in styles, True)
    check("no label from the form is a heading",
          [h["text"] for h in heads
           if ms._sec_key(h["text"]) in ("title", "running_title",
                                         "keywords")], [])
    check("the journal's name for the section is the heading",
          [h["text"] for h in heads if h["text"] == "Experimental Section"],
          ["Experimental Section"])

    # --- the end matter ---------------------------------------------------
    h1 = [h["text"] for h in heads if h["level"] == "1"]
    check("the bibliography gets a heading, like every other section",
          "References" in h1, True,
          "the refs branch appended the placeholder and nothing else, so a "
          "correctly numbered reference list sat under whatever heading the "
          "previous section carried")
    check("a statement the author wrote is carried through",
          "Conflict of Interest" in h1, True)
    check("...under the journal's own word for it, not authors.md's",
          ("Acknowledgment" in h1, "Acknowledgements" in h1), (True, False),
          "the section order says Acknowledgment; authors.md collects it "
          "under Acknowledgements")
    check("end matter is a top-level section, not a subsection",
          [h["text"] for h in heads
           if h["text"] == "Acknowledgment" and h["level"] != "1"], [],
          "it came out as Heading2 under the last body section")
    check("a required statement with nothing behind it is flagged",
          any("Funding is required" in w for w in res["warnings"]), True,
          "authors.md has no Funding block and the journal's order names one")
    body_text = doc
    check("...and the flag is in the document, not only in the log",
          "FLAG: author" in body_text, True)

    fc = ms.format_check(root, "Langmuir", res["output"])
    check("a build with a real title block clears format-check",
          [f["check"] for f in fc["findings"] if f["severity"] == "error"],
          [])
    write(root, os.path.relpath(reqp, root), original)


# ---------------------------------------------------------------------------
# The author files: the list, the contribution matrix, the affiliations and
# the funding table
# ---------------------------------------------------------------------------

MATRIX_AUTHORS = """\
# Authors and contributions

## Author list

| # | name | affiliations | ORCID | email |
|---|---|---|---|---|
| 2 | Cyd Doyle | A,B |  | cd@example.edu |
| 1 | Ada Bell | A | 0000-0002-1825-0097 | ab@example.edu |
| 3 | Russell W. Mercer | B |  | rm@example.edu |

Corresponding author: Cyd Doyle, cd@example.edu

## Contributions (CRediT)

| author | concept | methods | software | validation | analysis | experiments | resources | data | draft | revision | figures | supervision | admin | funding |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Ada Bell |  |  |  |  | x | x |  | x | x |  | x |  |  |  |
| Cyd Doyle | x | x |  | x |  |  | x |  |  | x |  | x | x | x |
| RWM |  |  | x |  | x |  |  |  |  | x | x |  |  |  |

## Initials

## RWM - Russell W. Mercer

## Data and code availability

The tomograms are in EMPIAR under EMPIAR-00000.
"""

AFFILIATIONS_MD = """\
# Affiliations and funding

## Affiliations

| key | affiliation |
|---|---|
| A | Department of Chemistry, BYU, Provo, UT 84602, United States |
| B | Department of Physics, BYU, Provo, UT 84602, United States |

## Funding

| funder | award | to |
|---|---|---|
| National Science Foundation | CHE-0000000 | C.D. |
| Example Center |  | A.B. |

## Acknowledgements

We thank the microscopy facility for instrument time.

## Competing interests

The authors declare no competing financial interest.
"""


def test_author_matrix(tmp: str) -> None:
    section("The author list, and the contribution matrix beside it")

    root = _scaffold(tmp, "matrix_case")
    if not root:
        skip("every author-file check", "scaffold failed")
        return

    # --- the scaffolded pair, before anyone has typed in them -------------
    # The template is a contract with this parser: its column headings have to
    # be the ones the aliases know, or a user filling in the matrix as shipped
    # gets a statement with their roles written out as our own vocabulary.
    blank = ms.authors_report(root)
    cols = blank["contributions"]["columns"]
    check("the scaffolded matrix has a column for every CRediT role",
          sorted(c["role"] for c in cols),
          sorted(k for k, _ in ms.CREDIT_ROLES),
          "a column heading in the template that the engine cannot map")
    check("...and nothing is ticked in it", blank["contributions"]["rows"], [])
    check("a blank scaffold generates no statements",
          (blank["contributions_statement"], blank["funding_statement"]),
          ("", ""),
          "the form's own instructions were emitted as the statement")
    check("...and says the one thing that is actually missing",
          [f["detail"] for f in blank["findings"]],
          ["the author list is empty - the build has no byline"])

    write(root, "drafts/source_text/authors.md", MATRIX_AUTHORS)
    write(root, "drafts/source_text/affiliations_and_funding.md",
          AFFILIATIONS_MD)

    res = ms.authors_report(root)
    names = [a["name"] for a in res["authors"]]

    # This is the whole reason the parser was rewritten: the old rule was
    # "five cells or more is an author row", and a fifteen-column matrix row
    # is nineteen cells wide.
    check("a matrix row is not read as an author", names,
          ["Ada Bell", "Cyd Doyle", "Russell W. Mercer"],
          "the contribution matrix was parsed as the author list, so the "
          "byline was a row of x's")
    check("the byline follows the # column, not the row order",
          names[0], "Ada Bell",
          "Cyd Doyle is written first in the file and numbered 2")

    byline = ms.author_block(dict(res, affiliations=res["affiliations"]))
    check("an author with two affiliations carries both numbers",
          "Cyd Doyle^1,2,\\*^" in byline[0], True, byline[0])
    check("...and each affiliation is numbered once",
          [b for b in byline if b.startswith("^")],
          ["^1^Department of Chemistry, BYU, Provo, UT 84602, United States",
           "^2^Department of Physics, BYU, Provo, UT 84602, United States"])

    st = res["contributions_statement"]
    check("the statement is in author order, not matrix order",
          st.index("Ada Bell") < st.index("Cyd Doyle") < st.index("Mercer"),
          True)
    check("a column heading becomes CRediT's own wording",
          "Writing - original draft" in st and "Formal analysis" in st, True,
          "the journal asks for the CRediT vocabulary, not ours")
    check("an unticked column is not claimed",
          "Supervision" in st.split("Cyd Doyle")[1]
          and "Supervision" not in st.split("Cyd Doyle")[0], True)
    check("a matrix row keyed by initials resolves to the author",
          "Russell W. Mercer:" in st, True,
          "RWM in the matrix has to find Russell W. Mercer in the list")

    check("the funding statement is built from the grant table",
          res["funding_statement"],
          "This work was supported by National Science Foundation "
          "(CHE-0000000, to C.D.) and Example Center (to A.B.).")
    check("a complete pair of author files has nothing outstanding",
          res["findings"], [])

    # --- what it catches --------------------------------------------------
    broken = MATRIX_AUTHORS.replace("| 3 | Russell W. Mercer | B |  | rm@example.edu |",
                                    "| 3 | Russell W. Mercer | C |  | rm@example.edu |")
    write(root, "drafts/source_text/authors.md", broken)
    found = ms.authors_report(root)["findings"]
    check("an affiliation key nothing defines is blocking",
          [f["severity"] for f in found
           if "does not define" in f["detail"]], ["blocking"],
          "the byline would print the letter C as the address")

    # The state every existing project passes through on its way to the split:
    # keys written into authors.md, affiliations_and_funding.md not filled in
    # yet. The byline would print the letter A where the department goes.
    write(root, "drafts/source_text/authors.md", MATRIX_AUTHORS)
    write(root, "drafts/source_text/affiliations_and_funding.md",
          "# Affiliations and funding\n\n## Affiliations\n\n"
          "| key | affiliation |\n|---|---|\n| A |  |\n")
    found = ms.authors_report(root)["findings"]
    check("keys with nothing behind them are caught, table empty or not",
          len([f for f in found if "does not define" in f["detail"]]), 3,
          "an empty affiliations table read every key as an address")
    write(root, "drafts/source_text/affiliations_and_funding.md",
          AFFILIATIONS_MD)

    broken = MATRIX_AUTHORS.replace("| RWM |", "| Grant Jensen |")
    write(root, "drafts/source_text/authors.md", broken)
    found = ms.authors_report(root)["findings"]
    check("a contribution credited to a non-author is blocking",
          [f["severity"] for f in found
           if "not in the author list" in f["detail"]], ["blocking"])
    check("...and the author with no row is reported too",
          any("Russell W. Mercer has no row" in f["detail"] for f in found),
          True)

    broken = MATRIX_AUTHORS.replace("0000-0002-1825-0097", "0000-0002-1825")
    write(root, "drafts/source_text/authors.md", broken)
    check("an ORCID that is not an ORCID is blocking",
          [f["severity"] for f in ms.authors_report(root)["findings"]
           if "ORCID" in f["detail"]], ["blocking"],
          "a mistyped ORCID points at a different human being")

    # A cell that is prose rather than a note is not a tick. Reading it as one
    # would put a role in the submitted statement that nobody claimed.
    broken = MATRIX_AUTHORS.replace("| Ada Bell |  |  |  |  | x |",
                                    "| Ada Bell | see below |  |  |  | x |")
    write(root, "drafts/source_text/authors.md", broken)
    ada = [r for r in ms.authors_report(root)["contributions"]["rows"]
           if r["author"] == "Ada Bell"][0]
    check("a cell holding a note is not a tick",
          "Conceptualization" in ada["roles"], False)

    write(root, "drafts/source_text/authors.md", MATRIX_AUTHORS)


def test_author_files_back_compat(tmp: str) -> None:
    section("A project written before the two files were split still builds")

    root = _scaffold(tmp, "legacy_authors")
    if not root:
        skip("every back-compat check", "scaffold failed")
        return
    # The pre-split layout: everything in authors.md, and no affiliations file
    # at all. Both places it could be scaffolded to are cleared, because this
    # test is about a project that predates the split AND predates the move to
    # plan/ - which is the same project, since they shipped together.
    for gone in (os.path.join(root, "plan", "affiliations.md"),
                 stfile(root, "affiliations.md"),
                 stfile(root, "affiliations_and_funding.md"),
                 os.path.join(root, "plan", "authors.md")):
        if os.path.isfile(gone):
            os.remove(gone)
    write(root, "drafts/source_text/authors.md", AUTHORS_MD + """
## Contributions (CRediT)

| author | roles |
|---|---|
| Ada Bell | investigation, writing - original draft |
| Cyd Doyle | supervision, funding acquisition |

## Funding

Supported by the National Science Foundation under CHE-0000000.
""")
    res = ms.authors_report(root)
    check("the free-text affiliation column still reads",
          [a["affiliation"] for a in res["authors"]][0],
          "Department of Chemistry, BYU, Provo, Utah 84602")
    check("...and the byline numbers the two authors' shared address once",
          len([b for b in ms.author_block(res) if b.startswith("^")]), 1)
    check("the two-column roles list still makes a statement",
          res["contributions_statement"],
          "**Ada Bell:** Investigation, Writing - original draft. "
          "**Cyd Doyle:** Supervision, Funding acquisition.",
          "a project scaffolded before the matrix existed has this shape")
    check("funding prose in authors.md is still found",
          res["funding_statement"],
          "Supported by the National Science Foundation under CHE-0000000.")


def test_author_statements_reach_the_docx(tmp: str) -> None:
    section("The generated statements reach the document")

    root = _scaffold(tmp, "statement_case")
    if not root:
        skip("every generated-statement check", "scaffold failed")
        return
    for stem, body in (("introduction", "A."), ("methods", "B."),
                       ("results", "C."), ("discussion", "D.")):
        write(root, "drafts/source_text/%s.md" % stem,
              "# %s\n\n%s\n" % (stem.title(), body))
    write(root, "drafts/source_text/title_abstract.md", TITLE_MD)
    write(root, "drafts/source_text/authors.md", MATRIX_AUTHORS)
    write(root, "drafts/source_text/affiliations_and_funding.md",
          AFFILIATIONS_MD)

    ms.init(root, "Langmuir")
    reqp = os.path.join(root, "drafts", "Langmuir", "journal_requirements",
                        "requirements.yml")
    write(root, os.path.relpath(reqp, root), read(reqp).replace(
        "section_order: [title_abstract, introduction, methods, "
        "results, discussion]",
        "section_order: [Title, Author list, Abstract, Introduction, "
        "Experimental Section, Results and Discussion, Author Contributions, "
        "Funding, Acknowledgment, Data Availability, References]"))
    res = ms.assemble(root, "Langmuir")
    if res["errors"]:
        skip("the built statements", "; ".join(res["errors"]))
        return
    doc = ms._docx_part(res["output"], "word/document.xml")
    text = re.sub(r"<[^>]+>", "", doc)

    check("the contribution statement is in the .docx",
          "Ada Bell: Formal analysis, Investigation" in text, True,
          "the matrix was filled in and the statement never reached the file")
    check("the funding statement is in the .docx",
          "This work was supported by National Science Foundation" in text,
          True)
    check("a statement that lives in the other file is found",
          "microscopy facility for instrument time" in text, True,
          "Acknowledgements moved to affiliations_and_funding.md")
    check("...and so is one that stayed in authors.md",
          "EMPIAR-00000" in text, True)
    check("no form heading reached the document",
          [h["text"] for h in ms._headings(doc)
           if h["text"] in ("Author list", "Initials",
                            "Contributions (CRediT)")], [])
    check("nothing is flagged when both files are filled in",
          [w for w in res["warnings"] if "FLAG" in w or "requires a" in w], [])


def test_scope_is_sticky_and_has_an_inverse(tmp: str) -> None:
    section("A scope the user gives sticks, has an inverse, and outranks "
            "the outline (item 41)")

    root = _scaffold(tmp, "scope_flags")
    if not root:
        skip("every scope-flag check", "scaffold failed")
        return
    ms.init(root, "Langmuir")
    jdir = os.path.join(root, "drafts", "Langmuir")

    # --- the inverse ------------------------------------------------------
    res = ms.run_ledger(root, "Langmuir", start=True,
                        except_sections=["introduction", "methods"])
    check("--except records the complement", res["errors"], [])
    check("...which is every other section, in document order",
          ms.round_scope(jdir), ["title_abstract", "results", "discussion"])
    check("...and the generating modules will touch only those",
          ms.plan(root, "Langmuir")["sections_writable"],
          ["title_abstract", "results", "discussion"],
          "what draft-sections is told it may write")
    ms.run_ledger(root, "Langmuir", abandon=True)

    # --- stickiness -------------------------------------------------------
    ms.run_ledger(root, "Langmuir", start=True)
    check("a --start that names no scope keeps the one recorded",
          ms.round_scope(jdir), ["title_abstract", "results", "discussion"],
          "the scope used to be wiped by every --start that did not repeat "
          "it, which works on the run it is typed on and fails on the next")
    pl = ms.plan(root, "Langmuir")
    check("...and plan reads it back rather than starting from nothing",
          pl["sections_scope"], ["title_abstract", "results", "discussion"])
    check("...and says it is standing from an earlier command",
          "still standing" in pl["scope_note"], True, pl["scope_note"])
    ms.run_ledger(root, "Langmuir", abandon=True)

    # --- the two flags together are refused, not resolved -----------------
    res = ms.run_ledger(root, "Langmuir", start=True, sections=["results"],
                        except_sections=["methods"])
    check("--sections and --except together are refused",
          res["errors"][0].startswith("REFUSED"), True, str(res["errors"]))
    check("...and the scope is left exactly as it was",
          ms.round_scope(jdir), ["title_abstract", "results", "discussion"])
    check("...and no run was opened by the refused command",
          ms.run_ledger(root, "Langmuir")["open"], False)

    # --- clearing is something you say out loud ---------------------------
    ms.run_ledger(root, "Langmuir", start=True, sections=["all"])
    check("`--sections all` is how a scope is cleared",
          ms.round_scope(jdir), [])
    ms.run_ledger(root, "Langmuir", abandon=True)

    # --- the flags exist on both commands ---------------------------------
    parser_help = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "manuscript.py"), "run",
         "--help"], capture_output=True, text=True, encoding="utf-8",
        errors="replace").stdout
    for flag in ("--sections", "--except", "--redraft"):
        check("run --start documents %s" % flag, flag in parser_help, True)
    plan_help = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "manuscript.py"), "plan",
         "--help"], capture_output=True, text=True, encoding="utf-8",
        errors="replace").stdout
    for flag in ("--sections", "--except", "--redraft"):
        check("plan documents %s" % flag, flag in plan_help, True)

    # --- and the precedence rule is written down where it is read ---------
    skill = read(os.path.join(ROOT, "skills", "writing-engine", "SKILL.md"))
    check("SKILL.md states that a user-given scope outranks the outline",
          "outranks" in skill and "--except" in skill, True)


def test_one_log_one_state(tmp: str) -> None:
    section("Five bookkeeping files in a journal folder become three "
            "(item 42)")

    root = _scaffold(tmp, "consolidate")
    if not root:
        skip("every consolidation check", "scaffold failed")
        return
    ms.init(root, "Langmuir")
    jdir = os.path.join(root, "drafts", "Langmuir")

    check("init writes no log.md into the journal folder",
          os.path.isfile(os.path.join(jdir, "log.md")), False)
    check("...it writes the project's one history instead",
          os.path.isfile(ms.log_path(root)), True)
    check("...at drafts/log.md",
          os.path.relpath(ms.log_path(root), root).replace("\\", "/"),
          "drafts/log.md")

    # --- the run ledger lives in round_state.json ------------------------
    LM = ms.journal_folder("Langmuir")
    ms.run_ledger(root, "Langmuir", start=True, preset="draft")
    write(root, "drafts/source_text/results.md", "# Results\n\nSomething.\n")
    ms.run_ledger(root, "Langmuir", step="draft-sections", state="done",
                  item="results", wrote=["drafts/source_text/results.md"])

    check("no run_state.json is left in the journal folder",
          os.path.isfile(os.path.join(jdir, "run_state.json")), False)
    check("no run_log.md is left in the journal folder root",
          os.path.isfile(os.path.join(jdir, "run_log.md")), False)
    state = json.loads(read(os.path.join(jdir, "round_state.json")))
    check("the run is inside round_state.json", state["run"]["state"], "open")
    check("...beside the round it belongs to",
          (state["round"], state["run"]["round"]), (1, 1))
    trace = ms._run_paths(jdir)[1]
    check("the append-only trace is in this round's reports folder",
          os.path.relpath(trace, jdir).replace("\\", "/"),
          "reports/r1/run_log.md")
    check("...and it holds the step", "draft-sections" in read(trace), True)

    # The audit property survives the merge: what a step claims is re-checked
    # against the folder, not believed.
    open_run = ms.run_ledger(root, "Langmuir")
    check("an interrupted run is still reported open", open_run["open"], True)
    check("...with the module to pick up at", open_run["next"], "outline",
          "the first module of the preset that has not run")
    check("...and the item that finished inside the one that started",
          "results" in open_run["resume_line"]
          or any(i["item"] == "results"
                 for m in open_run["run"]["modules"]
                 for i in m.get("items", [])), True)
    os.remove(stfile(root, "results.md"))
    stale = ms.run_ledger(root, "Langmuir")["stale_outputs"]
    check("a file deleted underneath the ledger is reported",
          [(s["path"], s["verdict"]) for s in stale],
          [("drafts/source_text/results.md", "missing")])

    # --- and the round's record retires whole ----------------------------
    sub = os.path.join(jdir, "submission")
    os.makedirs(sub, exist_ok=True)
    open(os.path.join(sub, "manuscript_r1.docx"), "wb").close()
    res = ms.open_round(root, "Langmuir")
    check("the round retires", res["retired"], 1)
    check("...taking the run log with it", os.path.isfile(trace), False)
    check("...into that round's record",
          os.path.isfile(os.path.join(root, "obsolete", "drafts", "r1",
                                      "reports", "run_log.md")), True)
    check("...while the project's history stays put",
          os.path.isfile(ms.log_path(root)), True)

    # --- one sequence, two journals, one file ----------------------------
    ms.log_round_heading(jdir, "Langmuir", 1, ["introduction", "methods"])
    ms.log_round_heading(jdir, "Langmuir", 2)
    ms.init(root, "JPCC")
    jdir2 = os.path.join(root, "drafts", "JPCC")
    third = ms.current_round(jdir2)
    JP = ms.journal_folder("JPCC")
    ms.log_round_heading(jdir2, "JPCC", third)

    log = read(ms.log_path(root))
    heads = [ln for ln in log.splitlines() if ln.startswith("## r")]
    check("all three rounds are in one file, newest first",
          heads,
          ["## r%d - %s - %s" % (third, JP, ms.TODAY),
           "## r2 - %s - %s" % (LM, ms.TODAY),
           "## r1 - %s - %s" % (LM, ms.TODAY)],
          "a journal change has to read as one sequence, not two files")
    check("...and the round's scope is under its own heading",
          "Scoped to introduction, methods" in log, True)
    check("no journal folder holds a log.md",
          [d for d in (jdir, jdir2)
           if os.path.isfile(os.path.join(d, "log.md"))], [])

    # --- a per-journal log.md left by an older engine is merged in -------
    old = os.path.join(jdir, "log.md")
    write(root, "drafts/Langmuir/log.md",
          "# LANGMUIR - round history\n\n## r0 - %s - 2026-01-01\n\n"
          "The round before any of this.\n" % LM)
    actions = ms.hoist_logs(root)
    check("a per-journal log.md is merged into the project's",
          "The round before any of this." in read(ms.log_path(root)), True)
    check("...and only then is it deleted", os.path.isfile(old), False)
    check("...and the merge says what it moved",
          [a["action"] for a in actions], ["merge"])
    check("...into the right place in the sequence",
          [ln for ln in read(ms.log_path(root)).splitlines()
           if ln.startswith("## r")][-1],
          "## r0 - %s - 2026-01-01" % LM)


SCOPED_OUTLINE = """\
# Structure

## Introduction

- Arrays have been resolved in mesophiles but not above 60 C. [@jensen2019arrays]

## Methods

- Cells were grown and plunge-frozen. [background; @jensen2019arrays]

## Results

- Arrays keep hexagonal packing at every growth temperature. [Fig 1]

## Discussion

- Order tracks growth temperature rather than phylogeny.
- The mechanism is thermal rather than phylogenetic. [@lateref2020]
"""

SCOPED_BIB = """\
@article{jensen2019arrays,
  title = {Cryo-electron tomography of chemoreceptor arrays in situ},
  author = {Jensen, Grant J.},
  journal = {Annu Rev Microbiol},
  year = {2019}
}

@article{lateref2020,
  title = {A paper the discussion will cite},
  author = {Doe, Jane},
  journal = {J Later},
  year = {2020}
}
"""


def test_round_scope_reaches_completeness(tmp: str) -> None:
    section("A scoped round reports the rest of the paper as deferred, "
            "not as gaps")

    root = _scaffold(tmp, "scope_case")
    if not root:
        skip("every round-scope check", "scaffold failed")
        return
    write(root, "plan/outline.md", SCOPED_OUTLINE)
    write(root, "drafts/references.bib", SCOPED_BIB)
    write(root, "drafts/source_text/introduction.md",
          "# Introduction\n\nArrays have been resolved in mesophiles but not "
          "above 60 C [@jensen2019arrays].\n")
    write(root, "drafts/source_text/methods.md",
          "# Methods\n\nCells were grown and plunge-frozen "
          "[@jensen2019arrays].\n")
    ms.init(root, "Langmuir")
    jdir = os.path.join(root, "drafts", "Langmuir")

    # --- with no scope recorded, every one of them is a gap ---------------
    wide = ms.completeness(root, "Langmuir")
    check("with no scope, the undrafted sections are gaps",
          sorted(m["detail"] for m in wide["missing"]
                 if m["detail"].endswith("is still a stub")),
          ["discussion is still a stub", "results is still a stub"])
    check("...and the discussion's outline line is a gap",
          any("Order tracks growth temperature" in m["detail"]
              for m in wide["missing"]), True)
    check("...and the reference only the discussion would cite is a gap",
          any("@lateref2020" in m["detail"] for m in wide["missing"]), True)
    check("...and nothing is deferred", wide["deferred"], [])

    # --- now scope the round ---------------------------------------------
    ms.record_round_scope(jdir, ["introduction", "methods"])
    res = ms.completeness(root, "Langmuir")
    check("the scope is read back", res["round_scope"],
          ["introduction", "methods"])
    check("...and names what it defers", res["sections_out_of_scope"],
          ["title_abstract", "results", "discussion"])
    check("no item names a deferred section as a gap",
          [m["detail"] for m in res["missing"]
           if any(s in m["detail"] for s in ("results", "discussion"))], [])
    deferred = [d["detail"] for d in res["deferred"]]
    check("the stubs are deferred instead",
          sorted(d for d in deferred if d.endswith("is still a stub")),
          ["discussion is still a stub", "results is still a stub"])
    check("...and so is the outline line belonging to one of them",
          any("Order tracks growth temperature" in d for d in deferred), True)
    check("...and so is the reference only its paragraphs would cite",
          any("@lateref2020" in d for d in deferred), True)
    check("a reference the drafted sections cite is neither",
          any("@jensen2019arrays" in x for x in
              deferred + [m["detail"] for m in res["missing"]]), False)
    check("the deferred items are still counted",
          len(res["deferred"]) >= 4, True, str(deferred))
    check("...and named in out_of_scope, with their own kind",
          sorted(s["area"] for s in res["out_of_scope"]
                 if s["kind"] == "round_scope"),
          ["discussion", "results", "title_abstract"])
    check("the summary says what was not checked",
          "out of this round's scope" in res["summary"], True,
          res["summary"])
    check("the managed-scope caveat is untouched by it",
          [s["area"] for s in res["out_of_scope"] if s["kind"] == "manages"],
          [])

    # --- a clean scoped round must not read as a finished paper ----------
    block = ms.render_incomplete_block(
        {"complete": True, "missing": [], "deferred": res["deferred"],
         "round_scope": res["round_scope"],
         "sections_out_of_scope": res["sections_out_of_scope"],
         "out_of_scope": []}, "LANGMUIR", 1)
    check("a clean scoped round still carries a block in the .docx",
          "Deferred, not missing" in block, True)
    check("...and it says nothing this round was for is outstanding",
          "Nothing this round was for is outstanding" in block, True)

    # --- clearing the scope puts them back --------------------------------
    ms.record_round_scope(jdir, [])
    again = ms.completeness(root, "Langmuir")
    check("clearing the scope reports every one of them as a gap again",
          (sorted(m["detail"] for m in again["missing"]
                  if m["detail"].endswith("is still a stub")),
           again["deferred"]),
          (["discussion is still a stub", "results is still a stub"], []))


ACS_ORDER = ("section_order: [Title, Abstract, Introduction, "
             "Experimental Section, Results and Discussion, "
             "ACKNOWLEDGMENT, ASSOCIATED CONTENT, "
             '"> Supporting Information", AUTHOR INFORMATION, '
             '"> Corresponding Author", "> Funding Sources", "> Notes", '
             "REFERENCES]")


def test_nested_end_matter(tmp: str) -> None:
    section("ACS end matter is two levels, and section_order can say so "
            "(item 33)")

    # --- the parse, on its own -------------------------------------------
    check("a plain name is level 1", ms.split_level("Funding"),
          (1, "Funding"))
    check("a marked name is level 2", ms.split_level("> Funding Sources"),
          (2, "Funding Sources"))
    check("...and quoting survives the YAML reader",
          ms.split_level('"> Notes"'), (2, "Notes"))
    check("strip_levels gives the names back",
          ms.strip_levels(["A", "> B", "C"]), ["A", "B", "C"])

    root = _scaffold(tmp, "acs_case")
    if not root:
        skip("every nested end-matter check", "scaffold failed")
        return
    for stem, body in (("introduction", "A."), ("methods", "B."),
                       ("results", "C."), ("discussion", "D.")):
        write(root, "drafts/source_text/%s.md" % stem,
              "# %s\n\n%s\n" % (stem.title(), body))
    write(root, "drafts/source_text/authors.md", MATRIX_AUTHORS)
    write(root, "drafts/source_text/affiliations_and_funding.md",
          AFFILIATIONS_MD)
    ms.init(root, "Langmuir")
    rel = "drafts/Langmuir/journal_requirements/requirements.yml"
    reqp = os.path.join(root, rel)
    flat = read(reqp)
    write(root, rel, flat.replace(
        "section_order: [title_abstract, introduction, methods, "
        "results, discussion]", ACS_ORDER))
    req = ms.read_flat_yml(reqp)
    order, note = ms.section_order(req)
    check("the order reads with its markers intact", note, "")

    entries, warns = ms.resolve_order(order, root)
    by_name = {e["name"]: e for e in entries}
    check("a heading with children is a container, not a statement",
          [(by_name[n]["kind"], by_name[n]["level"])
           for n in ("ASSOCIATED CONTENT", "AUTHOR INFORMATION")],
          [("group", 1), ("group", 1)])
    check("...and its children are level-2 statements",
          [(by_name[n]["kind"], by_name[n]["target"], by_name[n]["level"])
           for n in ("Supporting Information", "Corresponding Author",
                     "Funding Sources", "Notes")],
          [("statement", "supporting_information", 2),
           ("statement", "author_information", 2),
           ("statement", "funding", 2),
           ("statement", "conflict_of_interest", 2)],
          "ACS calls the competing-interests statement Notes")
    check("a level-1 section is untouched",
          (by_name["ACKNOWLEDGMENT"]["kind"],
           by_name["ACKNOWLEDGMENT"]["level"]), ("statement", 1))
    check("nothing is warned about a well-formed nested order",
          [w for w in warns if "not a section name" in w], [])

    # --- and in the document ---------------------------------------------
    res = ms.assemble(root, "Langmuir")
    if res["errors"]:
        skip("the built end matter", "; ".join(res["errors"]))
        return
    doc = ms._docx_part(res["output"], "word/document.xml")
    heads = [(h["text"], h["level"]) for h in ms._headings(doc)]
    got = [h for h in heads if h[0] in
           ("ASSOCIATED CONTENT", "Supporting Information",
            "AUTHOR INFORMATION", "Corresponding Author", "Funding Sources",
            "Notes", "ACKNOWLEDGMENT")]
    check("the .docx carries ACS's two levels, in order", got,
          [("ACKNOWLEDGMENT", "1"),
           ("ASSOCIATED CONTENT", "1"), ("Supporting Information", "2"),
           ("AUTHOR INFORMATION", "1"), ("Corresponding Author", "2"),
           ("Funding Sources", "2"), ("Notes", "2")])
    text = re.sub(r"<[^>]+>", "", doc)
    check("no `Conflict of Interest` heading reaches the page",
          "Conflict of Interest" in [h[0] for h in heads], False,
          "ACS's own word for it is Notes, and it comes from the order")
    check("the container prints once and carries no body of its own",
          text.count("Cyd Doyle - Department of Chemistry"), 1,
          "AUTHOR INFORMATION must not emit the corresponding-author block "
          "and then have its child emit it again")
    check("the funding statement is under it, not beside it",
          text.index("Funding Sources") > text.index("AUTHOR INFORMATION"),
          True)

    # --- a flat order keeps building exactly as it did --------------------
    write(root, rel, flat)
    flat_res = ms.assemble(root, "Langmuir", force=True)
    if flat_res["errors"]:
        skip("the flat rebuild", "; ".join(flat_res["errors"]))
        return
    flat_doc = ms._docx_part(flat_res["output"], "word/document.xml")
    # Every SECTION heading, not every heading: `live_captions.md` writes its
    # float captions as Heading2 and always has, which is not what this is
    # about.
    flat_heads = [(h["text"], h["level"]) for h in ms._headings(flat_doc)
                  if not h["text"].startswith(("Figure", "Table", "[FLAG"))]
    check("a flat section_order still emits one level throughout",
          sorted({lv for _t, lv in flat_heads}), ["1"], str(flat_heads))
    check("...and the end matter is back to siblings",
          [t for t, _lv in flat_heads if t in
           ("ASSOCIATED CONTENT", "AUTHOR INFORMATION", "Notes")], [])


def test_required_if_used(tmp: str) -> None:
    section("required_if_used is a conditional, not a synonym for required")

    root = _scaffold(tmp, "si_case")
    if not root:
        skip("every required_if_used check", "scaffold failed")
        return
    for stem, body in (("introduction", "A."), ("methods", "B."),
                       ("results", "C."), ("discussion", "D.")):
        write(root, "drafts/source_text/%s.md" % stem,
              "# %s\n\n%s\n" % (stem.title(), body))
    ms.init(root, "Langmuir")
    rel = "drafts/Langmuir/journal_requirements/requirements.yml"
    reqp = os.path.join(root, rel)
    write(root, rel, read(reqp)
          .replace("    supporting_information: unknown",
                   "    supporting_information: required_if_used")
          .replace("    author_information: unknown",
                   "    author_information: required")
          .replace("    funding: unknown", "    funding: required")
          .replace("    acknowledgment: unknown",
                   "    acknowledgment: required"))
    req = ms.read_flat_yml(reqp)

    check("the journal's own three words survive the read",
          ms.statement_status(req, "supporting_information"),
          "required_if_used")

    # --- the predicate, on its own ---------------------------------------
    used, why = ms.statement_applies(root, "supporting_information",
                                     "Langmuir")
    check("no supplementary float and nothing staged answers 'not used'",
          used, False, why)
    check("...and it says what it looked for",
          "no supplementary float" in why, True, why)
    check("a statement only a human can answer comes back unknown",
          ms.statement_applies(root, "ethics", "Langmuir")[0], None,
          "whether a study had human subjects is not in the folder")

    # A project whose floats live elsewhere cannot answer it either way, and
    # "cannot tell" must not collapse into "not used" any more than
    # `required_if_used` collapses into `required`.
    ms.configure(root, "Langmuir", manages=["outline,analysis"])
    used, why = ms.statement_applies(root, "supporting_information",
                                     "Langmuir")
    check("floats managed elsewhere makes the answer unknown, not false",
          used, None, why)
    owed, note = ms.statement_required(root, req, "supporting_information",
                                       "Langmuir")
    check("an unknown is not owed", owed, False)
    check("...and says so in one line rather than in the manuscript",
          "cannot tell whether there is" in note, True, note)
    ms.configure(root, "Langmuir", manages=["outline,floats,analysis"])

    owed, note = ms.statement_required(root, req, "supporting_information",
                                       "Langmuir")
    check("an unused required_if_used is not owed", (owed, note), (False, ""))
    check("a plain `required` is owed exactly as before",
          ms.statement_required(root, req, "funding", "Langmuir"),
          (True, ""))

    # --- and in the document ---------------------------------------------
    res = ms.assemble(root, "Langmuir")
    if res["errors"]:
        skip("the built statements", "; ".join(res["errors"]))
        return
    text = re.sub(r"<[^>]+>", "",
                  ms._docx_part(res["output"], "word/document.xml"))
    check("no supporting-information FLAG reaches the .docx",
          "supporting information statement" in text.lower(), False,
          "the paper has no supporting information, so nothing is owed")
    check("...and no Supporting Information heading either",
          "Supporting Information" in text, False)
    check("the flags for what this journal really does require still fire",
          sorted(s for s in ("Funding", "Acknowledgements",
                             "Author Information") if s in text),
          ["Acknowledgements", "Author Information", "Funding"])
    done = ms.completeness(root, "Langmuir")
    check("completeness carries no supporting-information item",
          [m["detail"] for m in done["missing"]
           if "supporting information" in m["detail"].lower()], [])

    # --- give it supporting information, and it comes back ---------------
    os.makedirs(os.path.join(root, "plan", "figures", "FigS01"),
                exist_ok=True)
    used, why = ms.statement_applies(root, "supporting_information",
                                     "Langmuir")
    check("a supplementary float folder answers 'used'", used, True, why)
    res = ms.assemble(root, "Langmuir", force=True)
    text = re.sub(r"<[^>]+>", "",
                  ms._docx_part(res["output"], "word/document.xml")) \
        if not res["errors"] else ""
    check("...and the statement, or its flag, comes back",
          "supporting information statement" in text.lower(), True,
          "; ".join(res["errors"])[:200])


# ---------------------------------------------------------------------------
# Page setup: justification, four margins, and a page number
# ---------------------------------------------------------------------------

def test_page_setup(tmp: str) -> None:
    section("Justification, margins and page numbers, sourced or defaulted")

    check("justified is read", ms._req_justification(
        {"text.justification": "justified"})[0], "both")
    check("so is the word the ACS template uses", ms._req_justification(
        {"text.alignment": "full"})[0], "both")
    check("ragged right is left alone", ms._req_justification(
        {"text.justification": "ragged right"})[0], "left")
    check("unknown means pandoc's own default, and no claim otherwise",
          ms._req_justification({"text.justification": "unknown"}),
          (None, ""))
    check("a value that is neither is reported rather than guessed at",
          ms._req_justification({"text.justification": "middle"})[1] != "",
          True)

    m, note = ms._req_margins({"text.margins_in": "0.75"})
    check("one number sets four margins", sorted(set(m.values())), [1080])
    check("...with nothing to report", note, "")
    m, _ = ms._req_margins({"text.margins_in": "1.0",
                            "text.margin_left_in": "1.5"})
    check("a per-side override changes that side only",
          (m["left"], m["top"]), (2160, 1440),
          "a journal asking for a wide binding edge is asking about left")
    check("page numbers are on unless the journal says otherwise",
          ms._req_page_numbers({})[0], True)
    check("...and off when it does",
          ms._req_page_numbers({"text.page_numbers": "none"})[0], False)

    root = _scaffold(tmp, "page_case")
    if not root:
        skip("the rest of the page-setup checks", "scaffold failed")
        return
    ms.init(root, "Langmuir")
    reqp = os.path.join(root, "drafts", "Langmuir", "journal_requirements",
                        "requirements.yml")
    original = read(reqp)

    res = ms.reference_doc(root, "Langmuir")
    idx = ms._style_index(ms._docx_part(res["output"], "word/styles.xml"))
    check("an unstated justification leaves the document ragged right",
          idx["Normal"]["jc"], None)
    doc = ms._docx_part(res["output"], "word/document.xml")
    check("a page number is there without being asked for",
          "footerReference" in doc, True,
          "every publisher's own Word template has one; a submission with no "
          "page numbers reads as a document nobody set up")
    check("...and the footer part is in the zip",
          ms._docx_part(res["output"], ms.FOOTER_PART) != "", True)
    check("...as a PAGE field rather than a typed 1",
          "PAGE" in ms._docx_part(res["output"], ms.FOOTER_PART), True,
          "a literal 1 stays 1 for the whole document")

    write(root, os.path.relpath(reqp, root),
          original.replace("  margins_in: unknown",
                           "  justification: justified\n"
                           "  margin_top_in: 0.5\n"
                           "  margin_bottom_in: 0.5\n"
                           "  margin_left_in: 0.76\n"
                           "  margin_right_in: 0.76\n"
                           "  margins_in: unknown"))
    res = ms.reference_doc(root, "Langmuir")
    styles_xml = ms._docx_part(res["output"], "word/styles.xml")
    idx = ms._style_index(styles_xml)
    check("a sourced justification reaches the running text",
          idx["Normal"]["jc"], "both")
    check("...and the reference list with it",
          idx["Bibliography"]["jc"], "both")
    h1_style = group(r'<w:style [^>]*w:styleId="Heading1">.*?</w:style>',
                     styles_xml, 0, re.S)
    check("...and is not written onto the headings",
          "<w:jc" in h1_style, False,
          "they inherit it from Normal, which is what the accepted paper's "
          "own bold-Normal headings do")
    doc = ms._docx_part(res["output"], "word/document.xml")
    mar = group(r"<w:pgMar[^>]*>", doc)
    check("each margin is set on its own side",
          [group('w:%s="(\\d+)"' % s, mar, 1) for s in ms.MARGIN_SIDES],
          ["720", "1094", "720", "1094"])

    write(root, os.path.relpath(reqp, root),
          read(reqp).replace("  page_numbers: unknown",
                             "  page_numbers: none"))
    res = ms.reference_doc(root, "Langmuir")
    check("a journal that asks for no page numbers gets none",
          "footerReference" in ms._docx_part(res["output"],
                                             "word/document.xml"), False)

    # --- and format-check reports all of it ------------------------------
    write(root, os.path.relpath(reqp, root), original)
    os.remove(os.path.join(root, "drafts", "Langmuir",
                           "journal_requirements", "reference.docx"))
    for stem, body in (("introduction", "A."), ("methods", "B."),
                       ("results", "C."), ("discussion", "D.")):
        write(root, "drafts/source_text/%s.md" % stem,
              "# %s\n\n%s\n" % (stem.title(), body))
    write(root, "drafts/source_text/title_abstract.md", TITLE_MD)
    write(root, "drafts/source_text/authors.md", AUTHORS_MD)
    built = ms.assemble(root, "Langmuir")
    if built["errors"]:
        skip("format-check on page setup", "; ".join(built["errors"]))
        return
    write(root, os.path.relpath(reqp, root),
          original.replace("  margins_in: unknown",
                           "  justification: justified\n"
                           "  margin_left_in: 0.76\n"
                           "  margins_in: unknown"))
    fc = ms.format_check(root, "Langmuir", built["output"])
    got = {f["check"] for f in fc["findings"]}
    check("a ragged document held to a justified journal is reported",
          "Normal justification" in got, True)
    check("...and so is the margin that differs",
          "left margin" in got, True)
    check("...and only that margin",
          [c for c in got if c.endswith(" margin")], ["left margin"])

    # Spec 5.4.1 keeps a hand-edited reference document, so an edit to
    # requirements.yml does not reach the build. That is only defensible if
    # the build says so - otherwise format-check reports the margins as wrong
    # and nothing says which of the two files is the stale one.
    drift = ms.reference_doc_drift(
        os.path.join(root, "drafts", "Langmuir", "journal_requirements",
                     "reference.docx"),
        ms.read_flat_yml(reqp))
    check("the drift is measured out of the file, not from its mtime",
          drift, ["text.justification", "text.margin_left_in"],
          "OneDrive rewrites mtimes on sync (spec 7.2.7)")
    again = ms.assemble(root, "Langmuir")
    check("a reference document behind the requirements is called out",
          any("reference.docx does not carry" in w
              for w in again["warnings"]), True)
    check("...and it is not silently rebuilt over",
          ms._style_index(ms._docx_part(
              os.path.join(root, "drafts", "Langmuir",
                           "journal_requirements", "reference.docx"),
              "word/styles.xml"))["Normal"]["jc"], None,
          "a hand-edited reference document has to survive the next build")
    write(root, os.path.relpath(reqp, root), original)


def _prose_json(cmd: str, project: str, *args: str) -> dict:
    """One prose.py command's JSON, the way manuscript.py itself gets it."""
    run = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "prose.py"), cmd,
         project, *args, "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return json.loads(run.stdout or "{}")


def test_the_freeze(tmp: str) -> None:
    section("Generating modules run once; a section you took over stays "
            "yours (prose 4)")

    root = build_project(os.path.join(tmp, "freeze"))
    ms.init(root, "Langmuir")
    st = stdir(root)

    def frozen_now() -> list:
        return ms.load_frozen(root)["frozen"]

    def plan(**kw) -> dict:
        return ms.plan(root, "Langmuir", "draft", **kw)

    check("nothing the engine has not claimed is frozen", frozen_now(), [])
    # Item 88. A project whose prose the engine has no hash of record for is
    # reported as exactly that. Silence here is what let a freeze that had
    # never fired look like a freeze with nothing to do.
    check("...and the plan says the prose has no hash of record rather than "
          "saying nothing about a freeze",
          ("no hash of record" in plan()["freeze_note"],
           plan()["freeze_counts"]["frozen"]), (True, 0))

    run = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "learn.py"), "frozen",
         root, "--record", "all", "--by", "draft-sections", "--round", "r1"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("the engine can record what it wrote", run.returncode, 0,
          run.stderr[:200])
    check("...and its own prose is not frozen against it", frozen_now(), [])

    # The whole point: a hand edit is a takeover, and one byte is enough.
    write(root, "drafts/source_text/methods.md",
          read(os.path.join(st, "methods.md")) + "\nA turbo pump was used.\n")
    check("one hand-edited section freezes, and only it",
          frozen_now(), ["methods"])

    res = plan()
    check("a frozen section is not handed to either generating module",
          "methods" in res["sections_writable"], False)
    check("...and the other four still are",
          len(res["sections_writable"]), 4)
    check("...the plan line says so before anything runs",
          "methods has been edited" in res["line"], True)
    check("...and names --redraft as the way out",
          "--redraft" in res["line"], True)

    # Whitespace is not an edit. Otherwise every editor that trims trailing
    # spaces on save freezes the whole paper.
    body = read(os.path.join(st, "results.md"))
    write(root, "drafts/source_text/results.md",
          body.replace("\n", "\r\n") + "   \n\n\n\n")
    check("CRLF and trailing blank lines are not an edit",
          frozen_now(), ["methods"])

    # The existing "empty source_text/ to force a redraft" gesture has to
    # keep working - a deleted file is drafted, not treated as frozen.
    os.remove(os.path.join(st, "discussion.md"))
    states = {r["section"]: r["state"] for r in ms.load_frozen(root)["sections"]}
    check("a deleted file is wiped, not frozen", states["discussion"], "wiped")
    check("...so it is still writable",
          "discussion" in plan()["sections_writable"], True)

    # --redraft: explicit, per invocation, never sticky.
    res = plan(redraft=["methods"])
    check("--redraft hands exactly one section back",
          (res["redrafted"], res["frozen"]), (["methods"], []))
    check("...and says what it will discard",
          "DISCARDED" in res["line"], True)
    check("...it is not sticky - the next plan freezes it again",
          plan()["frozen"], ["methods"])
    check("--redraft on a section that is not frozen is refused, not "
          "silently honoured",
          any("not frozen" in w for w in plan(redraft=["results"])["warnings"]),
          True)
    check("--redraft all hands back everything frozen",
          plan(redraft=["all"])["redrafted"], ["methods"])

    # 5: the lesson is banked even when the prose is discarded, and the only
    # thing that makes that true is the module ORDER.
    mods = plan(redraft=["methods"])["modules"]
    check("learn-from-edits still runs before draft-sections on a redraft, "
          "so the edits are learned from first and thrown away second",
          mods.index("learn-from-edits") < mods.index("draft-sections"), True)


def test_provenance_is_recorded_when_a_module_writes(tmp: str) -> None:
    section("A module that writes a section records the hash of record "
            "(item 88)")

    # THE REGRESSION TEST FOR THE DEFECT THE WHOLE FREEZE SAT ON. The
    # machinery below was built, tested and documented, and the only thing
    # that wrote its ledger was a subcommand nothing called - so `frozen()`
    # took its `no entry -> fresh` branch for every section of every project,
    # `load_frozen` returned an empty list, and every guard that says a frozen
    # section is skipped subtracted nothing. What is measured here is not that
    # the hash comparison works - test_the_freeze does that - but that a
    # NORMAL ROUND leaves a ledger behind without anybody invoking one.
    root = build_project(os.path.join(tmp, "prov"))
    ms.init(root, "Langmuir")
    J = "Langmuir"
    ledger = os.path.join(stdir(root), ".provenance", "sections.json")

    check("a project nothing has recorded has no ledger",
          os.path.isfile(ledger), False)
    fresh = ms.plan(root, J, "draft")
    check("...and the plan says the prose on disk has no hash of record, "
          "rather than saying nothing",
          "no hash of record" in fresh["freeze_note"], True,
          "a silence and a zero are different facts about the paper")
    check("...and the count is carried, not only the sentence",
          fresh["freeze_counts"]["written_but_unrecorded"] > 0, True)
    check("...with nothing frozen, because nothing CAN be",
          fresh["freeze_counts"]["frozen"], 0)

    ms.run_ledger(root, J, start=True, preset="draft")
    for before in ("outline", "literature-landscape", "learn-from-edits"):
        ms.run_ledger(root, J, step=before, state="done")

    # The one gesture the skill already documents: a module says what it
    # wrote as it finishes. Nothing here mentions provenance.
    live = os.path.basename(stdir(root))
    res = ms.run_ledger(root, J, step="draft-sections", item="methods",
                        state="done",
                        wrote=["drafts/%s/methods.md" % live])
    check("recording a written section writes the ledger",
          os.path.isfile(ledger), True)
    check("...and says which sections it claimed",
          res["provenance_recorded"], ["methods"])

    states = {r["section"]: r["state"]
              for r in ms.load_frozen(root)["sections"]}
    check("the section the module wrote reads engine-owned, not fresh",
          states["methods"], "engine-owned",
          "item 88's verify-by, in one line")
    check("...and a section nobody recorded still reads fresh",
          states["results"], "fresh")
    check("...so nothing is frozen yet - the engine owns what it wrote",
          ms.load_frozen(root)["frozen"], [])

    # And now the half the freeze exists for, reached without the subcommand.
    write(root, "drafts/source_text/methods.md",
          read(os.path.join(stdir(root), "methods.md"))
          + "\nA turbo pump was used.\n")
    check("one hand edit to a recorded section freezes it",
          ms.load_frozen(root)["frozen"], ["methods"])
    res = ms.plan(root, J, "draft")
    check("...and the plan takes it away from the generating modules",
          "methods" in res["sections_writable"], False)
    check("...and counts it",
          res["freeze_counts"]["frozen"], 1)

    # A step that has not finished has not written. Recording a hash for
    # prose still being written would claim the engine owns half a section.
    ms.run_ledger(root, J, step="revise-prose", item="results",
                  state="running", wrote=["drafts/%s/results.md" % live])
    check("a step still running records nothing",
          {r["section"]: r["state"]
           for r in ms.load_frozen(root)["sections"]}["results"], "fresh")

    # 1c is a REVISION of a section, never a first writing of one.
    ms.run_ledger(root, J, step="revise-prose --from-comprehension",
                  item="results", state="done",
                  wrote=["drafts/%s/results.md" % live])
    entry = json.loads(read(ledger))["results"]
    check("every spelling of 1c records as a revision",
          entry["revised_by"], "revise-prose --from-comprehension")
    check("...over a first writing it does not claim to have done",
          entry["written_by"], "draft-sections")

    # A file that is not a section of this project is not provenance.
    res = ms.run_ledger(root, J, step="assemble", state="done",
                        wrote=["drafts/log.md"])
    check("a written file that is not a section records nothing",
          res["provenance_recorded"], [])


def test_outline_waiver(tmp: str) -> None:
    section("Settling an outline line for good (prose 4.4)")

    root = build_project(os.path.join(tmp, "waiver"))
    ms.init(root, "Langmuir")
    write(root, "plan/outline.md",
          "# Structure\n\n## Methods\n\n"
          "- Report the chamber base pressure [methods_facts]\n"
          "- Report the sputter-clean sequence [methods_facts]\n\n"
          "## Results\n\n"
          "- Pd decomposes below the reduction onset [Fig 1]\n")

    res = ms.waive_outline_line(root, "methods:6", "")
    check("a waiver with no reason is refused",
          any("needs --because" in e for e in res["errors"]), True,
          "an unexplained waiver is indistinguishable from an accidental "
          "deletion six months later")

    res = ms.waive_outline_line(root, "results:6", "wrong section")
    check("a line number under a different section is refused",
          any("is under" in e for e in res["errors"]), True,
          "a line number off by a few settles a point you meant to keep")

    res = ms.waive_outline_line(root, "methods:6",
                                "superseded by the XPS control")
    check("a waiver with a reason is written", res["errors"], [])
    check("...into the outline itself, where the reason stays readable",
          "[waived: superseded by the XPS control]"
          in read(os.path.join(root, "plan", "outline.md")), True)

    res = ms.waive_outline_line(root, "methods:6", "again")
    check("waiving twice is a no-op, not a doubled marker",
          (res["errors"], res["changes"]), ([], []))

    res = ms.waive_outline_line(root, "methods:1", "not an outline line")
    check("a heading cannot be waived - the marker would be read by nothing",
          any("is not an outline line" in e for e in res["errors"]), True)

    out = _prose_json("outline", root)
    check("a waived line is out of the outline the drafter is handed",
          [ln["claim"] for ln in out["lines"]],
          ["Report the chamber base pressure",
           "Pd decomposes below the reduction onset"])
    check("...and out of the adherence count",
          out["counts"]["outline_lines"], 2)
    check("...but still reported, with its reason",
          [(w["line"], w["because"]) for w in out["waived"]],
          [(6, "superseded by the XPS control")])
    check("...and it is not reported as uncovered",
          [f for f in out["findings"]
           if f["rule"] == "dropped_point"
           and "sputter" in f.get("claim", "")], [])


def test_research_type(tmp: str) -> None:
    section("research_type: asked once, cached, changed only on purpose "
            "(prose 5.5)")

    root = build_project(os.path.join(tmp, "rtype"))
    ms.init(root, "Langmuir")
    check("a fresh project has none - it is left blank rather than guessed",
          ms.read_project_yml(root).get("research_type", ""), "")

    res = ms.set_research_type(root, "quantum_vibes")
    check("a type outside the vocabulary is refused",
          any("not in the research-type vocabulary" in e
              for e in res["errors"]), True,
          "a type invented per project scopes every rule to a population of "
          "one, and nothing errors while nothing is learned")
    check("...and the vocabulary is listed in the refusal",
          any("surface_science" in e for e in res["errors"]), True)
    check("...and nothing was written",
          ms.read_project_yml(root).get("research_type", ""), "")

    res = ms.set_research_type(root, "surface_science")
    check("a type in the vocabulary is cached in project.yml",
          ms.read_project_yml(root).get("research_type"), "surface_science")
    check("...and the change is reported, because the layer-3 brief changes "
          "with it", len(res["changes"]), 1)

    res = ms.set_research_type(root, "surface_science")
    check("setting it to what it already is changes nothing",
          res["changes"], [])

    res = ms.set_research_type(root, "biochemistry")
    check("changing it is allowed and is a real event",
          (ms.read_project_yml(root).get("research_type"),
           "surface_science" in res["changes"][0]["detail"]),
          ("biochemistry", True))


def test_source_text_carries_its_round(tmp: str) -> None:
    section("The source text folder carries the round it holds (item 31)")

    sub_tmp = os.path.join(tmp, "stlabel")
    os.makedirs(sub_tmp, exist_ok=True)
    root = build_project(sub_tmp)
    drafts = os.path.join(root, "drafts")

    # `build_project` scaffolds, so the label is there before any journal is.
    check("a scaffolded project's source text is labelled r1",
          os.path.basename(ms.source_text_dir(root)), "source_text_r1",
          "the label is project.yml:revision, which a fresh project sets to 1")
    check("...and the resolver finds it",
          os.path.isdir(ms.source_text_dir(root)), True)
    check("...and stpfx names the folder a user can actually open",
          ms.stpfx(root), "drafts/source_text_r1/")

    ms.init(root, "Langmuir")
    jdir = os.path.join(drafts, "Langmuir")

    # --- round renames it forward, AFTER the snapshot --------------------
    write(root, "drafts/Langmuir/submission/manuscript_r1.docx", "not a docx")
    write(root, "drafts/source_text/results.md", "# Results\n\nr1 text.\n")
    res = ms.open_round(root, "Langmuir")
    check("closing a round renames the source text forward",
          os.path.basename(ms.source_text_dir(root)), "source_text_r2")
    check("...and says it did",
          any(a["action"] == "rename" and "source_text_r2" in a["path"]
              for a in res["actions"]), True,
          str([(a["action"], a["path"]) for a in res["actions"]]))
    check("...and the round that closed is snapshotted as it shipped",
          "r1 text." in read(os.path.join(root, "obsolete", "drafts", "r1",
                                          "source_text_r1", "results.md")),
          True,
          "the rename has to happen after the snapshot, or the snapshot is "
          "of the next round's folder - and since round-archive 3 the prose "
          "lands in a folder named for the one it came out of")
    check("...and the live folder still holds the prose",
          "r1 text." in read(stfile(root, "results.md")), True)

    # --- a second journal opening at r8 takes the folder with it ---------
    for _ in range(6):
        rnd = ms.current_round(jdir)
        write(root, "drafts/Langmuir/submission/manuscript_r%d.docx" % rnd,
              "not a docx")
        ms.open_round(root, "Langmuir")
    check("Langmuir walked up to r8", ms.current_round(jdir), 8)
    check("...and the source text is labelled with it",
          os.path.basename(ms.source_text_dir(root)), "source_text_r8")

    res = ms.init(root, "JPCC")
    check("a new journal opening at r9 moves the label with it",
          os.path.basename(ms.source_text_dir(root)), "source_text_r9",
          "manuscript_r8.docx in Langmuir/ beside source_text_r9/ is the "
          "whole point: what was sent is not what would build today")
    check("...and init reports the rename",
          any(a["action"] == "rename" for a in res["actions"]), True)

    # --- an older folder still holding prose is named, never silent ------
    stale = os.path.join(drafts, "source_text_r5")
    os.makedirs(stale, exist_ok=True)
    write(root, "drafts/source_text_r5/results.md", "# Results\n\nOld text.\n")
    check("the live folder is still the newest label",
          os.path.basename(ms.source_text_dir(root)), "source_text_r9",
          "the labels give a total order, so there IS an answer here - "
          "unlike drafts/ versus the project root")
    check("...and the older one is reported as stale",
          ms.stale_source_text(root), ["source_text_r5"])
    st = ms.status(root)
    check("...by status, in its own field", st["stale_source_text"],
          ["source_text_r5"])
    check("...and status names the live folder", st["source_text"],
          "source_text_r9")
    res = ms.init(root, "JPCC")
    check("...and by init, as a warning rather than a refusal",
          (res["errors"],
           any(a["action"] == "warn" and "source_text_r5" in a["path"]
               for a in res["actions"])),
          ([], True), str(res["actions"]))
    shutil.rmtree(stale)

    # --- an empty older folder is not prose and is not reported ----------
    os.makedirs(os.path.join(drafts, "source_text_r4"), exist_ok=True)
    check("an EMPTY older folder is not reported - there is nothing in it",
          ms.stale_source_text(root), [])

    # --- a folder made by hand at a higher label takes over, loudly ------
    # The rule is "the newest label is live", with no exception for an empty
    # one - an exception would need a second rule about which emptiness
    # counts, and a wiped folder (the redraft gesture) is empty on purpose.
    # So a hand-made r10 becomes live, and the r9 holding the prose is
    # reported as NOT BUILT FROM rather than quietly built from.
    os.makedirs(os.path.join(drafts, "source_text_r10"), exist_ok=True)
    check("a hand-made higher label becomes the live folder",
          os.path.basename(ms.source_text_dir(root)), "source_text_r10")
    check("...and the folder holding the prose is reported, not ignored",
          ms.stale_source_text(root), ["source_text_r9"],
          "prose in a folder nothing builds from is the exit-code-0 failure "
          "this engine keeps having to be taught not to produce")
    check("...and label_source_text has nothing left to do",
          ms.label_source_text(root, 10), None)
    shutil.rmtree(os.path.join(drafts, "source_text_r10"))

    # --- a destination that exists is left alone, never merged -----------
    # Only reachable backwards - a round number behind a folder already on
    # disk, from a hand-edited round_state.json or an interrupted run. There
    # `os.rename` would raise; this says something useful instead.
    kept = ms.label_source_text(root, 4)
    check("a rename onto an existing folder is refused, not merged",
          (kept or {}).get("action"), "keep", str(kept))
    check("...and says which folder builds from now on",
          "source_text_r4" in (kept or {}).get("detail", ""), True, str(kept))
    check("...and nothing moved",
          sorted(d for d in os.listdir(drafts)
                 if d.startswith("source_text")),
          ["source_text_r4", "source_text_r9"])


def test_source_text_migrates_from_the_unlabelled_layout(tmp: str) -> None:
    section("An unlabelled source_text/ reads as r0 and is renamed forward")

    old = os.path.join(tmp, "old_layout")
    os.makedirs(os.path.join(old, "drafts", "source_text"))
    write(old, "drafts/source_text/introduction.md", "# Introduction\n\nA.\n")
    write(old, "drafts/source_text/methods.md", "# Methods\n\nB.\n")
    check("an unlabelled folder still resolves",
          os.path.basename(ms.source_text_dir(old)), "source_text",
          "a project written before the label must keep working")

    res = ms.init(old, "Langmuir")
    check("init renames it forward", res["errors"], [])
    check("...to the round the project is at",
          os.path.basename(ms.source_text_dir(old)), "source_text_r1")
    check("...carrying the prose with it",
          sorted(x for x in os.listdir(ms.source_text_dir(old))
                 if x in ("introduction.md", "methods.md")),
          ["introduction.md", "methods.md"])
    check("...and nothing is left at the old name",
          os.path.isdir(os.path.join(old, "drafts", "source_text")), False,
          "two folders holding a manuscript is the case this label exists "
          "to prevent, not to create")

    # A trimmed project migrates the same way, at its own root.
    trim = os.path.join(tmp, "trim_layout")
    os.makedirs(os.path.join(trim, "source_text"))
    write(trim, "source_text/introduction.md", "# Introduction\n\nReal.\n")
    ms.init(trim, "Langmuir")
    check("a trimmed project is renamed at its own root",
          os.path.basename(ms.source_text_dir(trim)), "source_text_r1")
    check("...and is still trimmed afterwards", ms.is_trimmed(trim), True,
          "the resolver has to recognise the labelled folder as the mark")
    check("...so no drafts/ appears beside the writing",
          os.path.isdir(os.path.join(trim, "drafts")), False)


def test_front_matter_is_sourced(tmp: str) -> None:
    section("A running title and keywords are sourced, or not written (item 32)")

    if not shutil.which("pandoc"):
        skip("the unsourced front-matter checks", "pandoc is not on PATH")
        return

    sub_tmp = os.path.join(tmp, "frontmatter")
    os.makedirs(sub_tmp, exist_ok=True)
    root = build_project(sub_tmp)
    ms.init(root, "Langmuir")
    write(root, "drafts/source_text/title_abstract.md",
          "# Title\n\nHexagonal packing survives the range\n"
          "\n## Running title\n\nHexagonal packing\n"
          "\n## Keywords\n\nspin coating, sintering\n"
          "\n## Abstract\n\nArrays keep hexagonal packing across the range "
          "measured, and the spacing does not change with temperature.\n")

    def rendered(res: dict) -> str:
        return subprocess.run(
            [pandoc_bin(), res["output"], "-t", "plain"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace").stdout

    built = ms.assemble(root, "Langmuir")
    check("an unsourced build carries neither", built["errors"], [],
          str(built["errors"]))
    text = rendered(built)
    check("...no running title in the document", "Running title:" in text,
          False, "Langmuir asks for one nowhere, and the group's own "
                 "accepted Langmuir manuscript has none")
    check("...and no keyword list", "Keywords:" in text, False)
    check("...but the build SAYS it left them out",
          sorted(w.split(" is written")[0] for w in built["warnings"]
                 if " is written in " in w and "does not ask for one" in w),
          ["keywords", "running title"],
          "silently dropping it is the same defect as silently including it")

    # Sourced by the journal's own section order.
    rel = "drafts/Langmuir/journal_requirements/requirements.yml"
    body = read(os.path.join(root, rel))
    order = re.search(r"^  section_order:.*$", body, re.M)
    check("the skeleton has a section_order to edit", bool(order), True)
    assert order is not None
    write(root, rel, body.replace(
        order.group(0),
        "  section_order: [Title, Author List, Abstract, Keywords, "
        "Introduction, Methods, Results, Discussion]"))
    built = ms.assemble(root, "Langmuir")
    text = rendered(built)
    check("a section order that names Keywords brings the list back",
          "Keywords:" in text, True, str(built["warnings"]))
    check("...and the running title is still left out",
          "Running title:" in text, False)

    # Sourced by the field instead.
    write(root, rel, body.replace("  keyword_count: unknown",
                                  "  keyword_count: 5"))
    built = ms.assemble(root, "Langmuir")
    check("a sourced keyword_count brings it back too",
          "Keywords:" in rendered(built), True)


def test_figure_files(tmp: str) -> None:
    section("Every captioned float drawn, and the uploads named the "
            "journal's way (item 34)")

    # The naming pattern is the journal's own example, so the digits in it are
    # the placeholder and nothing else is.
    check("the pattern's number is replaced, its words are not",
          ms.expected_figure_name("Figure 1.tif", "3", "tif"), "Figure 3.tif")
    check("...zero padding the journal used is kept",
          ms.expected_figure_name("Figure 01.tif", "3", "tif"),
          "Figure 03.tif")
    check("...a supplementary figure keeps its S",
          ms.expected_figure_name("Figure 1.tif", "S2", "eps"),
          "Figure S2.eps")
    check("...and a pattern with no number in it still gets one",
          ms.expected_figure_name("figure.tif", "4", "tif"), "figure 4.tif")

    check("a bracketed format list parses",
          ms._format_list("[TIF, EPS]"), ["tif", "eps"])
    check("...and so does the journal's own prose",
          ms._format_list("TIFF or EPS preferred"), ["tiff", "eps"])
    check("...a word that is not an extension is dropped, not enforced",
          ms._format_list("high-quality"), [])
    check("unknown is no requirement at all", ms._format_list("unknown"), [])

    sub_tmp = os.path.join(tmp, "figurefiles")
    os.makedirs(sub_tmp, exist_ok=True)
    root = build_project(sub_tmp)
    ms.init(root, "Langmuir")
    write(root, "plan/captions.md",
          "## Figure 1 — fig01_geometry.png\n"
          "**Arrays keep hexagonal packing at every growth temperature.**\n"
          "Central sections.\n\n"
          "## Figure 2 — fig02_symmetry.png\n"
          "**Thermophilic arrays are more ordered than mesophilic ones.**\n"
          "Symmetry index against growth temperature.\n\n"
          "## Figure 9 — fig09_absent.png\n"
          "**Nobody has drawn this one.**\n"
          "It has a caption and no folder.\n")

    def rules(res: dict, rule: str) -> list[str]:
        """The float each finding of one rule is about. Every message here
        opens with the label, because a finding that does not say which float
        is one nobody can act on."""
        return sorted(" ".join(f["detail"].split()[:2])
                      for f in res["findings"] if f["rule"] == rule)

    res = ms.figure_files(root, "Langmuir")
    check("the check runs on a project that manages its floats",
          res["checked"], True, str(res.get("reason")))
    check("a caption with no folder is named", rules(res, "float_not_drawn"),
          ["Figure 9"])
    check("...and a folder with no caption is named too",
          rules(res, "float_not_captioned"),
          ["Figure 3", "Figure 4", "Table 1", "Table 2"],
          "the scaffold makes four figure slots and two table slots")
    check("a captioned float that rendered nothing is named",
          rules(res, "float_not_rendered"), ["Figure 1", "Figure 2"])

    # Rendering one of them settles it, and nothing else moves.
    open(os.path.join(root, "plan", "figures", "Fig01", "figure.png"),
         "wb").close()
    res = ms.figure_files(root, "Langmuir")
    check("...and rendering it settles that one only",
          rules(res, "float_not_rendered"), ["Figure 2"])

    # --- what the journal asks for the upload to be called ----------------
    rel = "drafts/Langmuir/journal_requirements/requirements.yml"
    body = read(os.path.join(root, rel))
    check("figures.file_naming is in the skeleton to be sourced",
          "file_naming: unknown" in body, True)

    check("an embedded-figures journal expects no uploads at all",
          [f["rule"] for f in res["findings"]
           if f["rule"].startswith(("figure_", "figures_"))],
          ["figures_not_uploaded_separately"],
          "figures.placement is unknown until somebody reads the guidelines")

    write(root, rel, body
          .replace("  placement: unknown", "  placement: separate", 1)
          .replace("  file_naming: unknown", '  file_naming: "Figure 1.tif"')
          .replace("  file_format: unknown", "  file_format: [TIF, EPS]", 1))
    fdir = os.path.join(root, "drafts", "Langmuir", "submission", "figures")
    os.makedirs(fdir, exist_ok=True)
    open(os.path.join(fdir, "Figure 1.tif"), "wb").close()
    open(os.path.join(fdir, "Fig2.png"), "wb").close()

    res = ms.figure_files(root, "Langmuir")
    got = sorted((f["rule"], f["location"]) for f in res["findings"]
                 if f["rule"].startswith("figure_"))
    check("the correctly named upload is not a finding",
          [g for g in got if "Figure 1.tif" in g[1]], [])
    check("a file named this project's way, not the journal's, is a finding",
          [g[0] for g in got if g[1].endswith("Fig2.png")],
          ["figure_file_misnamed", "figure_format_not_accepted"],
          "the name and the file type are two separate rules")
    check("...and the message says both names",
          [f["detail"] for f in res["findings"]
           if f["rule"] == "figure_file_misnamed"],
          ["Figure 2 is uploaded as Fig2.png; this journal asks for "
           "Figure 2.tif (figures.file_naming)"])
    check("a captioned figure with nothing uploaded is an error",
          [f["detail"] for f in res["findings"]
           if f["rule"] == "figure_file_missing"],
          ["this journal wants Figure 9 uploaded as Figure 9.tif and there is "
           "no such file in drafts/Langmuir/submission/figures/"])

    # 4.3: unsourced is reported as unchecked, never guessed at.
    write(root, rel, read(os.path.join(root, rel))
          .replace('  file_naming: "Figure 1.tif"', "  file_naming: unknown"))
    res = ms.figure_files(root, "Langmuir")
    check("an unsourced pattern is reported as not checked",
          [f["severity"] for f in res["findings"]
           if f["rule"] == "figure_naming_unsourced"], ["info"])
    check("...and Fig2.png stops being misnamed, because nothing says it is",
          [f["rule"] for f in res["findings"]
           if f["rule"] == "figure_file_misnamed"], [])
    check("...while the figure nobody uploaded is still missing",
          [f["rule"] for f in res["findings"]
           if f["rule"] == "figure_file_missing"], ["figure_file_missing"])

    # Where completeness picks it up, and what it drops.
    done = ms.completeness(root, "Langmuir")
    floats = [m["detail"] for m in done["missing"] if m["area"] == "floats"]
    check("completeness carries the file findings under floats",
          any("has a caption block and no folder" in d for d in floats), True,
          str(floats)[:400])
    check("...and drops the info findings, which are unknowns not defects",
          any("not sourced" in d for d in floats), False, str(floats)[:400])

    # A project whose floats live somewhere else still uploads its figures
    # (item 135): the captions and folders are not checked, the uploads are,
    # against the figures the text cites.
    ms.configure(root, "Langmuir", manages=["outline,analysis"])
    res = ms.figure_files(root, "Langmuir")
    check("floats managed elsewhere still has its uploads checked",
          res["checked"], True, str(res.get("reason")))
    check("...and says only the uploads were checked",
          [f["rule"] for f in res["findings"]
           if f["rule"] == "float_set_unmanaged"], ["float_set_unmanaged"])
    check("...and checks no caption against a folder it does not own",
          [f["rule"] for f in res["findings"]
           if f["rule"].startswith("float_not_")], [])
    write(root, rel, read(os.path.join(root, rel))
          .replace("  placement: separate", "  placement: embedded", 1))
    res = ms.figure_files(root, "Langmuir")
    check("...while on an embedding journal the whole check is skipped",
          (res["checked"], res["findings"]), (False, []))
    check("...and it says why", "does not manage its floats" in res["reason"],
          True, res["reason"])


def test_a_scope_narrows_the_round_not_just_the_drafter(tmp: str) -> None:
    section("A section scope narrows the ROUND, and the build is a second "
            "question (item 59)")

    root = _scaffold(tmp, "scope_narrows")
    if not root:
        skip("every round-scope check", "scaffold failed")
        return
    ms.init(root, "Langmuir")

    # The user's sentence was "only do the introduction and methods". Before
    # this, --sections reached two of the seven modules a draft preset
    # resolves to, and the other five ran at full width - a re-source of the
    # journal requirements, a fetched stylesheet, a reference.docx, a built
    # manuscript and a citation pass, around two sections of prose.
    pl = ms.plan(root, "Langmuir", "draft",
                 sections=["introduction", "methods"])
    check("a scoped round does not assemble",
          "assemble" in pl["modules"], False, str(pl["modules"]))
    check("...nor run citation-check against a paper it did not finish",
          "citation-check" in pl["modules"], False, str(pl["modules"]))
    check("...and the drafting half is untouched",
          pl["modules"],
          ["outline", "literature-landscape", "learn-from-edits",
           "draft-sections", "revise-prose", "comprehension-check",
           "quality-check"])
    check("what was held is named, not dropped",
          pl["held_for_decision"], ["assemble", "citation-check"],
          "'do not build it yet' and 'these modules do not exist' are "
          "different facts")
    check("...and held is not reported as skipped as well",
          [m for m in pl["skipped"] if m in pl["held_for_decision"]], [],
          "printing 'Skipping assemble' beside a question asking whether to "
          "run assemble is the plan line arguing with itself")

    # The old line stated the modules and asked only "Go?", which reads as
    # one decision when it is two.
    check("the line asks the build question separately",
          "SECOND QUESTION" in pl["line"], True, pl["line"])
    check("...and says how to answer yes",
          "--build" in pl["build_question"], True, pl["build_question"])

    # --build is that yes.
    pl = ms.plan(root, "Langmuir", "draft",
                 sections=["introduction", "methods"], build=True)
    check("--build puts the submission half back",
          "assemble" in pl["modules"], True, str(pl["modules"]))
    check("...and there is then nothing held to ask about",
          pl["held_for_decision"], [])

    # Naming one by hand is a narrower yes.
    pl = ms.plan(root, "Langmuir", "draft",
                 sections=["introduction", "methods"], add=["assemble"])
    check("+assemble is a yes for that module alone",
          ("assemble" in pl["modules"],
           "citation-check" in pl["held_for_decision"]), (True, True))

    # A whole-paper round is not scoped, so nothing is held.
    pl = ms.plan(root, "Langmuir", "draft")
    check("an unscoped round holds nothing", pl["held_for_decision"], [])
    check("...and still assembles", "assemble" in pl["modules"], True)

    # A submission preset is a yes already given.
    pl = ms.plan(root, "Langmuir", "submission",
                 sections=["introduction", "methods"])
    check("a submission preset is never held - it IS the request to build",
          pl["held_for_decision"], [], str(pl["modules"]))

    # A `revision` round DERIVES a scope from the pending rows of
    # edits_status.md. That is the engine's answer to "what should be
    # redrafted", not the user's answer to "what is this round", and a
    # coauthor asking for one sentence in two sections has not asked the
    # round to stop before the document they are going to read.
    pl = ms.plan(root, "Langmuir", "revision")
    check("a derived revision scope holds nothing",
          pl["held_for_decision"], [], str(pl.get("scope_note")))

    # --- --stop-after -----------------------------------------------------
    pl = ms.plan(root, "Langmuir", "coauthor", stop_after="revise-prose")
    check("--stop-after ends the round where it says",
          pl["modules"][-1], "revise-prose")
    check("...and what is not reached is named rather than dropped",
          pl["not_reached"],
          ["comprehension-check", "quality-check", "evidence-check",
           "stats-check", "attribution-check", "assemble", "citation-check",
           "reviewer-check"])
    check("...and the line says so", "not reached" in pl["line"], True,
          pl["line"])
    pl = ms.plan(root, "Langmuir", "coauthor", stop_after="toc-graphic")
    check("--stop-after naming a module not in the round truncates nothing",
          len(pl["modules"]), 13, str(pl["modules"]))
    check("...and warns rather than failing silently",
          any("--stop-after" in w for w in pl["warnings"]), True,
          str(pl["warnings"]))

    # --- the preset that says what it is ----------------------------------
    check("`sections` is a preset", "sections" in ms.PRESETS, True)
    pl = ms.plan(root, "Langmuir", "sections")
    check("...that writes prose and builds nothing",
          "assemble" in pl["modules"], False, str(pl["modules"]))
    check("...and still runs the mechanical citation pass (item 36 has no "
          "exception)", "citation-check" in pl["modules"], True)

    # --- the flags exist on both commands ---------------------------------
    for cmd in ("plan", "run"):
        text = subprocess.run(
            [sys.executable, os.path.join(ROOT, "tools", "manuscript.py"),
             cmd, "--help"], capture_output=True, text=True,
            encoding="utf-8", errors="replace").stdout
        for flag in ("--stop-after", "--build"):
            check("%s documents %s" % (cmd, flag), flag in text, True)


def test_agent_brief_carries_the_scope(tmp: str) -> None:
    section("agent-brief names the scope it was already being told to obey "
            "(items 56, 57, 58)")

    root = _scaffold(tmp, "brief_scope")
    if not root:
        skip("every brief-scope check", "scaffold failed")
        return
    ms.init(root, "Langmuir")
    ms.run_ledger(root, "Langmuir", start=True, preset="sections",
                  sections=["introduction", "methods"])

    # --- item 56 ----------------------------------------------------------
    res = ms.agent_brief(root, "draft-sections", "Langmuir")
    check("the brief reads the round's scope off round_state.json",
          res["sections_scope"], ["introduction", "methods"])
    check("...and says where it got it", res["scope_source"],
          "round_state.json")
    check("the prompt NAMES the sections to draft",
          ("introduction" in res["prompt"] and "methods" in res["prompt"]),
          True)
    check("...and names the ones that are not this round's",
          "OUT OF SCOPE" in res["prompt"], True)
    check("...and says they are deferred rather than missing",
          "deferred, not missing" in res["prompt"], True,
          "an agent told a section is missing writes it")
    check("the WRITE list is the in-scope files and no others",
          sorted(os.path.basename(w) for w in res["writes"]),
          ["introduction.md", "methods.md"],
          "it used to read 'source_text/*.md - the four body files', which "
          "is an instruction to write four on a round scoped to two")
    check("...so results.md is not offered to the drafter",
          any("results.md" in w for w in res["writes"]), False,
          str(res["writes"]))

    # The flags override the recorded scope for one invocation.
    res = ms.agent_brief(root, "draft-sections", "Langmuir",
                         except_sections=["results", "discussion"])
    check("--except resolves in the brief too",
          res["sections_scope"],
          ["title_abstract", "introduction", "methods"])
    check("...and title_abstract is still not the drafter's",
          any("title_abstract" in w for w in res["writes"]), False,
          "the abstract agent writes it, and it is written last")
    res = ms.agent_brief(root, "draft-sections", "Langmuir",
                         sections=["results"], except_sections=["methods"])
    check("both flags at once are refused here as they are everywhere else",
          any("REFUSED" in e for e in res["errors"]), True, str(res["errors"]))

    # A module the scope does not govern is not handed a scope block. The
    # abstract agent writes title_abstract.md, so telling it title_abstract
    # is out of this round would be a prompt contradicting itself - and
    # whether a scoped round runs the abstract at all is the orchestrator's
    # decision, not a hint to bury in a brief.
    res = ms.agent_brief(root, "abstract", "Langmuir")
    check("a module the scope does not govern gets no scope block",
          "THIS ROUND'S SCOPE" in res["prompt"], False)
    check("...and is not told the file it writes is out of scope",
          "OUT OF SCOPE" in res["prompt"], False)
    res = ms.agent_brief(root, "revise-prose", "Langmuir")
    check("revise-prose is governed by it, and gets one",
          "THIS ROUND'S SCOPE" in res["prompt"], True)
    check("...and polishes only the sections in the round",
          sorted(os.path.basename(w) for w in res["writes"]
                 if w.endswith(".md") and "reports" not in w),
          ["introduction.md", "methods.md"])

    # --- item 57 ----------------------------------------------------------
    res = ms.agent_brief(root, "abstract", "Langmuir")
    for w in res["writes"]:
        check("a write target is a path, not a sentence: %r" % w[:40],
              " " in os.path.basename(w), False, w)
        check("...and it is resolved, not a literal rN: %r" % w[:40],
              "rN" in w, False, w)
    check("the prose report's real path is emitted",
          any(w.endswith("reports/r1/prose_report.md") for w in res["writes"]),
          True, str(res["writes"]))
    notes = [w["note"] for w in res["writes_detail"] if w["note"]]
    check("...and WHICH PART of it to write is a note beside the path",
          any("Take-home" in n for n in notes), True, str(notes))
    check("the prompt carries the note under its path",
          "Take-home" in res["prompt"], True)

    # Both entries of one list are in one convention. They used to be in two -
    # one absolute, one relative - and the relative one had English in it.
    check("every write path is absolute",
          [w for w in res["writes"] if not os.path.isabs(w)], [])

    # --- item 58 ----------------------------------------------------------
    for module in sorted(ms.AGENT_MODULES):
        res = ms.agent_brief(root, module, "Langmuir")
        check("%s: the prompt disowns anything the harness auto-loads"
              % module, "AUTO-LOADED" in res["prompt"], True)
        check("%s: ...and names the file that actually arrives" % module,
              "CLAUDE.md" in res["prompt"], True,
              "an agent that is told to disregard 'project instruction "
              "files' and receives one called CLAUDE.md has to make the "
              "connection itself")

    ms.run_ledger(root, "Langmuir", abandon=True)


def test_status_reports_the_cached_journal(tmp: str) -> None:
    section("A journal this project already settled on is reported, folder "
            "or no folder (item 55)")

    root = _scaffold(tmp, "cached_journal")
    if not root:
        skip("every cached-journal check", "scaffold failed")
        return

    # A project that has never had a journal. The two states below are only
    # useful if this one is distinguishable from them.
    st = ms.status(root)
    check("a project with no journal says so",
          (st["journals"], st["journal"], st["cached_journal"]), ([], "", ""))
    check("...and names no source", st["journal_source"], "")

    ms.init(root, "Langmuir")
    st = ms.status(root)
    check("a journal with a folder is reported from the folder",
          (st["journal"], st["journal_source"]), ("Langmuir", "folder"))
    check("...and the folder is there", st["journal_folder_exists"], True)

    # The user deletes drafts/ on purpose - to see whether the skill asks.
    shutil.rmtree(os.path.join(root, "drafts"))
    st = ms.status(root)
    check("the cached journal survives a deleted drafts/",
          st["journal"], "Langmuir",
          "status derived the journal from drafts/*/ alone, so it came back "
          "empty while project.yml two levels up still held target_journal")
    check("...and says it is cached rather than found",
          st["journal_source"], "project.yml")
    check("...and that the folder is gone",
          st["journal_folder_exists"], False,
          "'this project has never had a journal' and 'this project has one "
          "and its folder is missing' lead to opposite actions")
    check("the journals project.yml recorded are reported too",
          st["cached_journals"], ["Langmuir"])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ms.print_status(st)
    line = buf.getvalue()
    check("the printed line says do not ask", "do not ask" in line, True, line)
def test_harvest_reads_what_the_instrument_wrote(tmp: str) -> None:
    section("The acquisition settings the instrument wrote are recorded "
            "fact, not a question for the author (item 46)")

    root = _scaffold(tmp, "harvest_case")
    if not root:
        skip("every harvest check", "scaffold failed")
        return

    # Two synthetic Thermo/FEI TIFFs, written the way the instrument writes
    # them: the ini block in private tag 34682 of IFD0. They differ in the
    # one field whose real answer on the project that found this defect was
    # two answers - which microscope.
    images = os.path.join(root, "data", "raw", "images")
    os.makedirs(images, exist_ok=True)
    for i, (system, mode) in enumerate([("Verios G4 UC", "SE"),
                                        ("Verios G4 UC", "SE"),
                                        ("Helios NanoLab 600", "DHV")]):
        _write_fei_tiff(os.path.join(images, "img_%03d.tif" % i), system, mode,
                        wd=0.00391943 + i * 1e-5)

    res = sc.harvest(root, os.path.join("data", "raw", "images"),
                     dry_run=True)
    check("every file is read", res["files_read"], 3)
    check("...and every one carries an acquisition block",
          res["files_with_metadata"], 3)
    check("the microscope is not unrecorded",
          [v["value"] for v in res["facts"]["instrument"]["values"]],
          ["Verios G4 UC", "Helios NanoLab 600"])
    check("...and the counts travel with it",
          [v["files"] for v in res["facts"]["instrument"]["values"]], [2, 1])
    check("a field whose files disagree is NOT unanimous",
          res["facts"]["instrument"]["unanimous"], False,
          "a methods section naming one microscope on a project that used "
          "two is false, and the majority is the most confident way to be "
          "false")
    check("the voltage is settled outright",
          res["facts"]["voltage_kV"]["values"][0]["value"], "5 kV")
    check("...unanimously", res["facts"]["voltage_kV"]["unanimous"], True)
    check("the detector mode is read as well as the detector",
          sorted(v["value"]
                 for v in res["facts"]["detector_mode"]["values"]),
          ["DHV", "SE"])

    # A value measured afresh for every exposure is a range, not a list of
    # eighty-five distinct strings.
    check("a per-image value is summarised as a range",
          res["facts"]["working_distance_mm"]["kind"], "per_image")
    check("...with a low, a high and a median",
          sorted(res["facts"]["working_distance_mm"]["range"]),
          ["high", "low", "median", "n", "unit"])

    check("a dry run writes nothing",
          os.path.exists(os.path.join(root, "data",
                                      "instrument_metadata.json")), False)

    res = sc.harvest(root, os.path.join("data", "raw", "images"))
    prov = os.path.join(root, "data", "instrument_metadata.json")
    check("the per-file provenance is written", os.path.isfile(prov), True)
    rec = json.loads(read(prov))
    check("...and it is per file, so every count is checkable",
          len(rec["per_file"]), 3)
    check("...naming the file each value came from",
          all("file" in r for r in rec["per_file"]), True)

    facts = read(os.path.join(root, "data", "methods_facts.yml"))
    check("the harvest reaches methods_facts.yml", "instrument_metadata:"
          in facts, True)
    check("...uncommented, because the instrument recorded it",
          any(ln.strip().startswith("instrument:") for ln in facts.splitlines()),
          True,
          "a prefill default is somebody else's typical number and is "
          "commented out; this is what this microscope wrote about these "
          "exposures")
    check("...and it names both microscopes",
          ("Verios G4 UC" in facts and "Helios NanoLab 600" in facts), True)
    check("...and points at the provenance",
          "data/instrument_metadata.json" in facts, True)

    # A value a person recorded outranks anything derived - the same rule
    # prefill follows, for the same reason.
    with open(os.path.join(root, "data", "methods_facts.yml"), "a",
              encoding="utf-8") as fh:
        fh.write("\n\nlens_mode: Immersion\n")
    res = sc.harvest(root, os.path.join("data", "raw", "images"),
                     dry_run=True)
    check("a hand-recorded value is left alone",
          "lens_mode" in res["left_alone"], True, str(res["left_alone"]))

    # An empty quoted string is a person saying they do not know, and is the
    # exact shape the question takes on disk.
    with open(os.path.join(root, "data", "methods_facts.yml"), "a",
              encoding="utf-8") as fh:
        fh.write('\ndetector: ""   # not stated in the outline\n')
    res = sc.harvest(root, os.path.join("data", "raw", "images"),
                     dry_run=True)
    check('...but `key: ""` is not a record',
          "detector" in res["left_alone"], False, str(res["left_alone"]))

    # Re-harvesting is normal - more images arrive - and the block is
    # regenerated rather than stacked. A file that says "all 148 file(s)"
    # after a run that read 158 is worse than no line at all, because it is a
    # specific number and will be believed.
    sc.harvest(root, os.path.join("data", "raw", "images"))
    facts = read(os.path.join(root, "data", "methods_facts.yml"))
    check("a second harvest replaces its own block rather than stacking one",
          facts.count("instrument_metadata:"), 1)
    check("...and the hand-written line outside it survives",
          facts.count("lens_mode: Immersion"), 1)
    check("...and the block says where a correction goes",
          "ANYWHERE ELSE" in facts, True)

    # A folder of images with no acquisition block settles nothing, and says
    # so rather than reporting a clean harvest of nothing.
    empty = _scaffold(tmp, "harvest_none")
    if empty:
        os.makedirs(os.path.join(empty, "data", "raw", "images"),
                    exist_ok=True)
        with open(os.path.join(empty, "data", "raw", "images", "plain.tif"),
                  "wb") as fh:
            fh.write(b"II" + struct.pack("<HI", 42, 8)
                     + struct.pack("<H", 0) + b"\x00" * 4)
        res = sc.harvest(empty, os.path.join("data", "raw", "images"),
                         dry_run=True)
        check("a file with no acquisition block is read and reported",
              (res["files_read"], res["files_with_metadata"]), (1, 0))
        check("...and the report says nothing is settled by that",
              any("Nothing was harvested" in n for n in res["notes"]), True,
              str(res["notes"]))

    # The drafter is given the file, or none of the above changes a manuscript.
    ms.init(root, "Langmuir")
    brief = ms.agent_brief(root, "draft-sections", "Langmuir")
    check("draft-sections is given the harvested metadata",
          any(g["name"] == "instrument_metadata" for g in brief["given"]),
          True, str([g["name"] for g in brief["given"]]))
    check("...and its brief says a value in it is never a flag",
          "instrument_metadata.json" in brief["prompt"], True)


def _write_fei_tiff(path: str, system: str, mode: str, wd: float) -> None:
    """One synthetic Thermo/FEI TIFF: a 1x1 image and a real ini block.

    Written by hand rather than with an imaging library for the same reason
    tests/make_docx_fixtures.py writes OOXML by hand - no library will emit
    the private tag this reader exists to read, and a fixture produced by
    something other than the thing under test is the only kind worth having.
    """
    ini = (
        "[User]\r\nDate=11/06/2025\r\nTime=12:00:25 PM\r\n\r\n"
        "[System]\r\nType=SEM\r\nSystemType=%s\r\n\r\n"
        "[Beam]\r\nHV=5000\r\n\r\n"
        "[EBeam]\r\nBeamCurrent=5e-011\r\nWD=%.8f\r\n"
        "LensMode=Immersion\r\n\r\n"
        "[Scan]\r\nDwelltime=3e-006\r\nPixelWidth=1.34792e-009\r\n"
        "HorFieldsize=4.14082e-006\r\n\r\n"
        "[Image]\r\nResolutionX=3072\r\nResolutionY=2048\r\n\r\n"
        "[Vacuum]\r\nChPressure=0.00095\r\nUserMode=High vacuum\r\n\r\n"
        "[Detectors]\r\nNumber=1\r\nName=TLD\r\nMode=%s\r\n"
        % (system, wd, mode)).encode("latin-1") + b"\x00"

    pixel = b"\x80"
    tags = [(256, 3, 1, 1), (257, 3, 1, 1), (258, 3, 1, 8), (259, 3, 1, 1),
            (262, 3, 1, 1), (277, 3, 1, 1)]
    n = len(tags) + 3            # + strip offset, strip byte count, the ini
    head = 8
    ifd = head + len(pixel)
    ini_at = ifd + 2 + n * 12 + 4

    out = bytearray()
    out += b"II" + struct.pack("<HI", 42, ifd)
    out += pixel
    entries = bytearray()
    for tag, typ, count, val in tags:
        entries += struct.pack("<HHI", tag, typ, count)
        entries += struct.pack("<HH", val, 0)
    entries += struct.pack("<HHII", 273, 4, 1, head)          # StripOffsets
    entries += struct.pack("<HHII", 279, 4, 1, len(pixel))    # StripByteCounts
    entries += struct.pack("<HHII", 34682, 2, len(ini), ini_at)
    out += struct.pack("<H", n) + entries + struct.pack("<I", 0)
    out += ini
    with open(path, "wb") as fh:
        fh.write(bytes(out))


def test_a_superseded_manuscript_is_named(tmp: str) -> None:
    """Item 102, verified exactly as its `verify by` words it.

    Retiring a closed round into `obsolete/drafts/rN/` is carried out only by
    `writing-engine`'s `round`, which needs `project.yml` and a journal
    folder. On a folder that has `drafts/` and neither, a newer
    `manuscript_rN.docx` could be written beside the previous one and nothing
    objected - `round` could not run and no other command checked the
    invariant. `drafts/` then stopped answering "what is being sent right
    now" with one file, while the skill and `specs/round-archive.md` 1.1
    state the rule unconditionally, so it read as enforced when it was not.

    Measured before the fix: `status` on exactly this folder finished without
    naming either manuscript.
    """
    section("a superseded manuscript is named on an unscaffolded folder "
            "(item 102)")

    root = os.path.join(tmp, "unscaffolded")
    os.makedirs(os.path.join(root, "drafts"), exist_ok=True)
    for n in (1, 2):
        with open(os.path.join(root, "drafts", "manuscript_r%d.docx" % n),
                  "wb") as fh:
            fh.write(b"x")
    check("the fixture really has no project.yml",
          os.path.exists(os.path.join(root, "project.yml")), False)

    res = ms.status(root)
    owed = res.get("unretired") or []
    check("status names one un-retired round", len(owed), 1, owed)
    if owed:
        check("...and it is r1, not the live one", owed[0]["round"], 1)
        check("...naming what superseded it",
              "manuscript_r2.docx" in owed[0]["superseded_by"], True, owed[0])
        check("...and where it belongs",
              owed[0]["retire_to"].replace("\\", "/"), "obsolete/drafts/r1")

    # It has to reach the TEXT, not just the payload. The whole defect was
    # that nothing was emitted anywhere a person would see it.
    out = io.StringIO()
    keep, sys.stdout = sys.stdout, out
    try:
        ms.print_status(res)
    finally:
        sys.stdout = keep
    printed = out.getvalue()
    check("the line is printed, not only in the JSON",
          "NOT RETIRED" in printed, True, printed[-300:])
    check("...and it says who has to do it on an unscaffolded folder",
          "yours to move by hand" in printed, True)

    # One manuscript is not a finding. A folder mid-round must stay quiet.
    solo = os.path.join(tmp, "one_only")
    os.makedirs(os.path.join(solo, "drafts"), exist_ok=True)
    with open(os.path.join(solo, "drafts", "manuscript_r1.docx"), "wb") as fh:
        fh.write(b"x")
    check("one manuscript alone is not a finding",
          ms.status(solo).get("unretired"), [])


def isolate_the_defect_ledger(tmp: str) -> None:
    """Send this suite's defect records to a ledger of its own, not the real one.

    This replaces `isolate_the_spool`, and the reason it has to exist is the
    reason that one did. `log-issue` and every build that logs an engine
    fault write a record, and this suite calls `log-issue` with deliberately
    fictional codes dozens of times per run. Measured 2026-09-14, when the
    records went to a spool: 28 pending items, of which eight were this
    suite's fixtures and three more were real codes whose run counts it had
    inflated - one to `runs: 697`.

    Upstream reporting was removed on 2026-09-21, so the spool is gone and
    with it the worst of that leak: nothing gets pushed anywhere any more.
    What is left is not cosmetic either. `system-changes.md` is now the ONLY
    record, and a fictional code written into it - or a real item's run count
    bumped by a suite that never saw the defect - corrupts the one file whose
    whole value is that its history is true.

    Set here, in `main`, so that every subprocess this suite spawns inherits
    it. Two tests below override it with a file of their own; an inherited
    default does not stop them.
    """
    os.environ["SYSTEM_CHANGES_MD"] = os.path.join(tmp, "system-changes.md")
    # The ledger is local to each machine and gitignored, so a fresh clone
    # has none: the suite works from the template the engine creates it from.
    shutil.copyfile(LEDGER_SOURCE, os.environ["SYSTEM_CHANGES_MD"])


def live_ledger_items() -> int:
    """How many items the SHIPPING system-changes.md carries."""
    live = os.path.join(ROOT, "system-changes.md")
    if not os.path.isfile(live):
        return 0
    text = read(live)
    return len(re.findall(r"^### \d+\.", text, re.M))


def test_comprehension_fix_runs(tmp: str) -> None:
    """prose 11.3 - 1d's findings are acted on, not offered.

    The reasoning that once made this `ask` was backwards. A comprehension
    finding is close to self-validating: the reader stopping IS the
    evidence, and there is nothing to adjudicate. What this section pins is
    that the dial exists, that it resolves, that the plan the user approves
    SAYS which way it went, and that a level with no findings at all
    cannot produce a fix pass however the dial is set.

    prose 12.2 REMOVED the round from this decision entirely. edit-authority 5
    had narrowed `auto` to r1 and made it an offer from r2, to protect prose
    that by then has the user's and their coauthors' work in it - a real
    reason, served since by a better mechanism. The coauthor guard is per
    SENTENCE and by AUTHOR; frozen sections are skipped outright. The round
    offer was per ROUND and for everyone, so it suppressed the fix across the
    whole draft nobody had touched in order to protect the part that already
    had its own guard.

    What is left is the dial, which is the user's ceiling and always was.
    """
    section("the fix runs, at every round (prose 12.2)")

    check("the dial is a dial, with three values",
          ms.CONFIG_DIALS.get("comprehension_fix"), ["auto", "ask", "off"])
    check("...and `auto` is what a fresh project gets",
          "comprehension_fix: auto" in ms.DEFAULT_CONFIG, True)

    # The resolver, and edit-authority 5 REVERSED what it used to say. `light`
    # was read as "nothing to act on", which made the automatic pass off at r1
    # and on from r2 - out of the draft that was still the engine's, and over
    # the draft with the user's and their coauthors' work in it. `off` is the
    # only level with genuinely no findings, and it is still off.
    for dial in ("auto", "ask", "off"):
        check(f"`off` leaves nothing to act on, so `{dial}` is off",
              ms.comprehension_fix_mode(dial, "off", 1), "off")
    # prose 12.5 test 1-2, and the RANGE is the point. The bug this replaces
    # passed a test that checked r1 and r2 and agreed with itself; asserting
    # every round from 1 to 6 is what makes "the round does not decide"
    # checkable rather than a sentence in a docstring.
    for level in ("light", "standard"):
        for rnd in range(1, 7):
            check(f"at r{rnd} and `{level}`, `auto` RUNS the pass - a "
                  f"readability finding is fixed in the round that found it",
                  ms.comprehension_fix_mode("auto", level, rnd), "auto")
    for rnd in range(1, 7):
        check(f"`ask` is the ceiling and outranks the round, at r{rnd}",
              ms.comprehension_fix_mode("ask", "standard", rnd), "ask")
        check(f"...and `off` means off at r{rnd}",
              ms.comprehension_fix_mode("off", "standard", rnd), "off")
    check("an unreadable value falls back to the default rather than to "
          "nothing - a dial nobody can parse must not silently disable a "
          "pass the user believes is running",
          ms.comprehension_fix_mode("sometimes", "standard", 1), "auto")
    check("both fix passes resolve through ONE function, so they cannot "
          "drift apart by being configured in different places",
          (ms.fix_mode("auto", 1), ms.fix_mode("auto", 4)), ("auto", "auto"))

    # prose 12.5 test 6. `quality_fix` was declared dead by edit-authority and
    # is a real dial now. 9.1's rule - a dial that is declared and never read
    # is a defect - applies to it from its first commit, so the read is
    # asserted here and not only the declaration.
    check("`quality_fix` is a dial, with the same three values",
          ms.CONFIG_DIALS.get("quality_fix"), ["auto", "ask", "off"])
    check("...and `auto` is what a fresh project gets, because a finding 1e "
          "already adjudicated does not need a second person to agree",
          "quality_fix: auto" in ms.DEFAULT_CONFIG, True)
    check("...and it resolves through the same one function",
          (ms.fix_mode("auto", 3), ms.fix_mode("off", 3)), ("auto", "off"))

    sub = os.path.join(tmp, "comp_fix")
    os.makedirs(sub, exist_ok=True)
    root = build_project(sub)
    ms.init(root, "Langmuir")

    # `submission` resolves the level to `standard` at any round, which is
    # the only state in which the fix mode is answerable.
    res = ms.plan(root, "Langmuir", "submission")
    check("the level is the full blind read", res["comprehension_level"],
          "standard")
    check("the plan carries the fix mode", res["comprehension_fix"], "auto")
    check("...and the line the user approves says the pass will RUN, "
          "because a pass that rewrites prose has to be visible before it "
          "runs even when nobody is asked to approve it",
          "acted on inside the round" in res["line"], True,
          res["line"][-400:])
    check("...and says they are told rather than asked",
          "told what changed rather than asked first" in res["line"], True)

    ms.configure(root, "Langmuir", sets=["comprehension_fix=ask"])
    res = ms.plan(root, "Langmuir", "submission")
    check("`ask` is kept for a user who wants it",
          res["comprehension_fix"], "ask")
    check("...and the line says nothing acts without an answer",
          "without your answer" in res["line"], True, res["line"][-400:])

    ms.configure(root, "Langmuir", sets=["comprehension_fix=off"])
    res = ms.plan(root, "Langmuir", "submission")
    check("`off` turns the pass off", res["comprehension_fix"], "off")
    check("...and the plan SAYS it was skipped - a plan that lists a pass "
          "tells the user it ran, so one that drops a pass has to say so",
          "comprehension_fix is off" in res["line"], True,
          res["line"][-400:])

    # r1 of a drafting preset reads LIGHT and still fixes. The cost decision
    # and the policy decision are separate now: the reading check may stay
    # cheap while the pass runs over whatever it found, and SKILL.md carries
    # the step that makes that true - at `light`, write the report from the
    # countable half rather than leaving the fix pass a file nobody wrote.
    ms.configure(root, "Langmuir", sets=["comprehension_fix=auto"])
    res = ms.plan(root, "Langmuir", "draft")
    check("r1 reads light to keep the cost down, and STILL fixes",
          (res["comprehension_level"], res["comprehension_fix"]),
          ("light", "auto"))
    check("...and the line says so before the round starts",
          "acted on inside the round" in res["line"], True,
          res["line"][-400:])

    # The guards are the same ones, and the brief is where they live. None
    # of them relaxes because the pass stopped being optional.
    br = ms.agent_brief(root, "revise-prose --from-comprehension", "Langmuir")
    check("the fix pass is still blind", br["isolation"], "blind")
    given = [g["path"] for g in br["given"]]
    for name in ("outline.md", "README.md", "captions.md"):
        check(f"...still denied {name}",
              any(name in g for g in given), False, str(given))
    check("...and the comprehension report is still its one report",
          any("comprehension_report" in g for g in given), True, str(given))

    # --- prose 12.5, tests 4 and 5: 1c gets the findings and not the -------
    # numbers. This is the change the user asked for - the fix lands in 1c,
    # which runs BEFORE every checker, so the draft that comes back is the
    # fixed one rather than a report about an unfixed one.
    br = ms.agent_brief(root, "revise-prose", "Langmuir")
    given = [g["path"] for g in br["given"]]
    check("1c is handed the reading findings, which is what makes the fix "
          "happen before anything reviews the draft",
          any("reading.json" in g for g in given), True, str(given))
    check("...and is still blind", br["isolation"], "blind")
    for name in ("outline.md", "README.md", "captions.md", "analysis.md",
                 "references.bib"):
        check(f"...still denied {name} - being given a measurement widens "
              f"nothing else",
              any(name in g for g in given), False, str(given))
    check("...and ai_voice.json is NOT among them: a pattern match reaches a "
          "rewrite through 1e's verdict or not at all (10.11)",
          any("ai_voice" in g for g in given), False, str(given))
    denied = " ".join(d[0] + " " + d[1]
                      for d in ms.AGENT_MODULES["revise-prose"]["denied"])
    check("the densities are named in the DENIAL list rather than merely "
          "left out - an agent told what it has infers nothing about what "
          "it does not",
          "density" in denied.lower() and "passive share" in denied.lower(),
          True, denied[:300])

    # The payload itself, which is where the withholding is real. Asserting
    # it on the file and not on the prose is the point: a promise about what
    # an agent will not look at is worth nothing next to a file that does
    # not contain it.
    #
    # THE PROSE BELOW IS PLANTED AND THE FINDING COUNT IS ASSERTED NON-ZERO,
    # and the first draft of this test did neither. It wrote to the wrong
    # directory - source text lives in drafts/source_text_rN/, not under the
    # journal folder - so readability measured nothing, the findings list
    # came back empty, and "no finding is from a withheld check" passed
    # vacuously over a check that had not run. That is item 80's shape
    # exactly, inside the test written to prevent it. A withholding test
    # MUST first prove there was something to withhold.
    #
    # Every sentence here carries a known defect: a bare `This shows`, a
    # paragraph opening that shares no content word with the one before it, a
    # closing sentence that ranks last for what the paragraph is about, a
    # 12-word subject-verb gap, and a clause whose subject is the document.
    # It also carries `delving` and `testament to`, which are AI-voice and
    # must NOT arrive.
    write(root, "drafts/source_text_r1/results.md",
          "# Results\n\n"
          "The XRD data was collected at 300 K. This shows a clear shift in "
          "the peak position. This section will discuss the implications of "
          "these measurements in detail below.\n\n"
          "The SEM images, which were acquired after the samples had been "
          "annealed for twelve hours under flowing argon at atmospheric "
          "pressure in the tube furnace described above, revealed a granular "
          "surface. It serves as evidence for the proposed mechanism, and "
          "delving into the microstructure was a testament to the utility of "
          "the approach.\n")
    res = ms.write_reading_findings(root, "Langmuir")
    check("the reading payload is written where the brief points",
          bool(res["written"]) and res["written"].endswith("reading.json"),
          True, str(res)[:200])
    if res["written"]:
        with open(res["written"], encoding="utf-8") as fh:
            payload = json.load(fh)
        # FIRST, and everything below is worthless without it.
        check("prose with known defects produces findings - a withholding "
              "test over an empty list proves nothing",
              payload["counts"]["findings"] > 0, True, str(payload["counts"]))
        checks_in = {f["check"] for f in payload["findings"]}
        check("...only from the checks that name a sentence 1c can act on",
              checks_in <= {"readability", "metaprose", "entity_forms",
                            "abbreviations"}, True, str(checks_in))
        check("...and never from `voice`, `ai_voice`, `spelling` or "
              "`italics` - two of those name only a number, and two are "
              "actions this pass already took",
              checks_in & {"voice", "ai_voice", "spelling", "italics"}, set())
        check("...each one naming a location, because a finding 1c cannot "
              "find is a finding 1c cannot fix",
              all(f["location"] for f in payload["findings"]), True)
        for absent in ("voice", "densities", "ai_voice_densities",
                       "ai_voice"):
            check(f"...and the payload carries no `{absent}` key - a pass "
                  f"that cannot see the number cannot chase it",
                  absent in payload, False, str(sorted(payload)))
        # The planted prose contains both of these. They are AI-voice, 1e
        # adjudicates them, and 3.64 acts on the verdict - so their ABSENCE
        # from this file is 10.11 holding structurally rather than by
        # instruction.
        blob = json.dumps(payload).lower()
        for leaked in ("delving", "testament"):
            check(f"...and `{leaked}` is in the prose and NOT in this "
                  f"payload: an AI-voice finding reaches a rewrite through "
                  f"1e's verdict or not at all",
                  leaked in blob, False)
        check("...and says what it withheld rather than leaving a reader to "
              "infer that this is the whole picture",
              bool(payload.get("withheld")) and payload.get("advisory"),
              True, str(payload.get("withheld")))

    # --- italics, beside spelling and for the same reason (user-asks-2 1) --
    #
    # Three things in one file, and they have to come apart three ways: a
    # Latin phrase that IS a lookup and is written here; a gene symbol that
    # is a judgment about the sentence and goes to 1c; and a journal-set term
    # that is a house's typography and goes to the user.
    ital_src = write(root, "drafts/source_text_r1/discussion.md",
                     "# Discussion\n\n"
                     "The construct was tested in vivo and again in vitro, "
                     "and low CMG2 mRNA tracked the outcome.\n\n"
                     "Delivery was via endocytosis [@a2020b].\n")
    res = ms.write_reading_findings(root, "Langmuir")
    after = read(ital_src)
    check("1c is handed a draft whose Latin phrases are already italic",
          "*in vivo*" in after and "*in vitro*" in after, True, after[:200])
    check("...and `via` is untouched, because the house fires on nothing in "
          "the journal set", "was via endocytosis" in after, True)
    check("...and the citekey survived the pass", "[@a2020b]" in after, True)
    if res["written"]:
        with open(res["written"], encoding="utf-8") as fh:
            payload = json.load(fh)
        it = payload.get("italics") or {}
        check("the payload reports the italics as DONE, not as to-do",
              it.get("italicised"), 2, str(it))
        check("...naming the terms it applied",
              sorted(it.get("terms", [])), ["in vitro", "in vivo"])
        check("...and which rule it applied them under",
              it.get("source"), "house default")
        genes = [f for f in payload["findings"]
                 if f["rule"] == "gene_symbol_roman"]
        check("a gene symbol reaches 1c, because deciding gene from protein "
              "is a judgment about the sentence", len(genes) > 0, True,
              str(payload["by_rule"]))
        check("...under the italics check", sorted({f["check"] for f in genes}),
              ["italics"])

    # The journal dial, in the file the journal's own rules live in. It
    # supersedes the house in BOTH directions, and the report says which.
    write(root, "drafts/Langmuir/journal_requirements/requirements.yml",
          'journal: "Langmuir"\nchecked: 2026-09-18\n\n'
          'typography:\n  italic_terms: [via]\n  roman_terms: [in vitro]\n')
    dials = ms.italic_dials(root, "Langmuir")
    check("requirements.yml is where the dial lives",
          [dials["journal_terms"], dials["roman_terms"], dials["source"]],
          [["via"], ["in vitro"], "journal requirements.yml"])
    write(root, "drafts/source_text_r1/discussion.md",
          "# Discussion\n\nTested in vivo and again in vitro, delivered "
          "via endocytosis.\n")
    ms.write_reading_findings(root, "Langmuir")
    after = read(os.path.join(root, "drafts", "source_text_r1",
                              "discussion.md"))
    check("a term the journal sets roman is left alone, and the journal wins",
          "again in vitro" in after, True, after[:200])
    check("...while the house's own always-italic term is still applied",
          "*in vivo*" in after, True)
    check("...and a journal-set term is REPORTED and never written - italic "
          "in one house and roman in the next is not a lookup",
          "delivered via endocytosis" in after, True)

    # --- §2 and §3 reach 1c, and the two thresholds do not (step 5) ------
    write(root, "drafts/source_text_r1/title_abstract.md",
          "# Title\n\nA Study of CMG2\n\n## Abstract\n\nCMG2 is a "
          "489-residue protein whose von Willebrand factor A domain binds "
          "collagen.\n")
    write(root, "drafts/source_text_r1/discussion.md",
          "# Discussion\n\nThe von Willebrand factor A (vWA) domain of human "
          "CMG2 was resolved, and the vWA fold is conserved. Protective "
          "antigen binds there. Protective antigen was added. Protective "
          "antigen was washed. Protective antigen eluted.\n")
    res = ms.write_reading_findings(root, "Langmuir")
    if res["written"]:
        with open(res["written"], encoding="utf-8") as fh:
            payload = json.load(fh)
        rules = {f["rule"] for f in payload["findings"]}
        check("the abstract-versus-body findings reach 1c, because each one "
              "names a sentence and an exact token",
              {"abstract_qualifier_dropped",
               "abstract_missing_definition"} <= rules, True,
              str(sorted(rules)))
        check("...under their own check names",
              {"entity_forms", "abbreviations"}
              <= {f["check"] for f in payload["findings"]}, True,
              str(sorted({f["check"] for f in payload["findings"]})))
        for withheld in ("never_abbreviated", "defined_then_spelled_out"):
            check(f"...and `{withheld}` does NOT, because it rests on an "
                  f"uncalibrated threshold a rewrite pass would chase",
                  withheld in rules, False)
            check(f"...and is named in `withheld` rather than merely left "
                  f"out", withheld in payload["withheld"], True,
                  str(payload["withheld"]))
        ab = payload["abbreviations"]
        check("the two that were withheld reach the USER instead, with the "
              "rule that produced them",
              sorted({f["rule"] for f in ab["yours"]}),
              ["never_abbreviated"], str(ab))
        check("...and the payload says which house rule was applied AND why "
              "- `the journal file is silent` is a different sentence from "
              "`the journal asked for the house rule`",
              [ab["rule"], ab["source"]],
              ["define", "house default (journal file is silent)"])
        check("the species reading is carried whether or not it fired",
              payload["species"]["abstract"], [])

    # The journal's own rule supersedes the house without argument.
    write(root, "drafts/Langmuir/journal_requirements/requirements.yml",
          'journal: "Langmuir"\nchecked: 2026-09-18\n\n'
          'typography:\n  abbreviations_in_abstract: avoid\n')
    check("requirements.yml is where the abbreviation rule lives",
          ms.abbrev_dial(root, "Langmuir"), "avoid")
    res = ms.write_reading_findings(root, "Langmuir")
    if res["written"]:
        with open(res["written"], encoding="utf-8") as fh:
            payload = json.load(fh)
        check("...and it reaches the engine, which says so",
              [payload["abbreviations"]["rule"],
               payload["abbreviations"]["source"]],
              ["avoid", "journal requirements.yml"])
    write(root, "drafts/Langmuir/journal_requirements/requirements.yml",
          'journal: "Langmuir"\nchecked: 2026-09-18\n')
    check("a file that is silent reads as `unknown`, not as the house rule "
          "having been asked for", ms.abbrev_dial(root, "Langmuir"),
          "unknown")

    # And unknown means the house applies, which is not the same as the
    # journal having been read and having said none.
    write(root, "drafts/Langmuir/journal_requirements/requirements.yml",
          'journal: "Langmuir"\nchecked: 2026-09-18\n\n'
          'typography:\n  italic_terms: unknown\n  roman_terms: unknown\n')
    dials = ms.italic_dials(root, "Langmuir")
    check("`unknown` means nobody looked, so the house default applies",
          [dials["journal_terms"], dials["roman_terms"], dials["source"]],
          [[], [], "house default"])


def test_quality_check_adjudicates(tmp: str) -> None:
    """Module 1e and its rewrite pass (prose 10.4, 10.10, 10.11, 11.1).

    The invariant this section exists for is 10.11's: NO FINDING REACHES THE
    REWRITE UNADJUDICATED. `prose.py ai_voice` reports to 1e and to the user;
    it reaches 3.64 through 1e or not at all. The second is 10.10's: the four
    DENSITIES are unreachable from the rewrite pass, because a pass that
    cannot see the number cannot optimize it, and that is a stronger
    guarantee than telling it not to.
    """
    section("1e adjudicates, and the rewrite pass sees only verdicts "
            "(prose 10.11)")

    sub = os.path.join(tmp, "quality")
    os.makedirs(sub, exist_ok=True)
    root = build_project(sub)
    ms.init(root, "Langmuir")

    # --- the orders, and the stretch they sit in -------------------------
    orders = {k: v["order"] for k, v in ms.AGENT_MODULES.items()
              if k in ("comprehension-check", "revise-prose --from-comprehension",
                       "quality-check", "revise-prose --from-quality",
                       "attribution-check")}
    check("3.5 read the paper, 3.6 act on it, 3.62 audit, 3.64 act, 3.7 "
          "attribution - and 3.6 was already taken, which is why 1e is 3.62",
          [k for k, _ in sorted(orders.items(), key=lambda kv: kv[1])],
          ["comprehension-check", "revise-prose --from-comprehension",
           "quality-check", "revise-prose --from-quality",
           "attribution-check"])

    check("1e is in every preset that drafts",
          [p for p in ms.PRESETS if "quality-check" not in ms.PRESETS[p]], [])

    # --- 1e is NOT blind, and that is the design ------------------------
    br = ms.agent_brief(root, "quality-check", "Langmuir")
    check("1e spawns as its own agent", br["runs_as_agent"], True)
    check("...with a fresh context, NOT blind - you cannot tell significance "
          "inflation from a significant finding without knowing what was "
          "found", br["isolation"], "fresh")
    given = [g["path"] for g in br["given"]]
    check("it is GIVEN the outline, which comprehension-check is denied",
          any("outline.md" in g for g in given), True, str(given))
    check("...and the analysis, because #24 grades the claim verb against a "
          "RECORDED evidence tier rather than against a feeling",
          any("analysis" in g for g in given), True, str(given))
    check("...and the raw audit it is there to adjudicate",
          any("ai_voice.json" in g for g in given), True, str(given))
    check("...and the house digest, because where the audit disagrees with "
          "it the digest wins",
          any("writing_rules" in g for g in given), True, str(given))

    prompt = br["prompt"]
    check("every finding gets one verdict from a closed set",
          all(v in prompt for v in ("apply", "leave", "ask")), True)
    check("...and a run that verdicts everything `apply` is named as a "
          "defect", "IS A DEFECT" in prompt.upper(), True)
    check("the five `leave` cases are in the prompt, so the implementation "
          "cannot verdict everything apply for want of an example",
          all(x in prompt for x in ("proton source", "em dashes", "XPS",
                                    "high-throughput", "hedge stack")), True)
    check("rule 7 is audited as the POINT and never as the SHAPE",
          "NEVER THE SHAPE" in prompt.upper(), True)
    check("...and a declarative results paragraph is explicitly not a "
          "finding", "catechism" in prompt, True)
    check("layer 2's three unauditable rules are named as not its job",
          "Rules 5, 9 and 10 are NOT yours" in prompt, True)
    check("a judgment finding must name the deflated claim",
          "cannot name the deflated claim is not a finding" in prompt, True)

    # --- 10.10 / 10.11: what the rewrite pass may see -------------------
    rb = ms.agent_brief(root, "revise-prose --from-quality", "Langmuir")
    check("the rewrite pass is blind", rb["isolation"], "blind")
    rgiven = [g["path"] for g in rb["given"]]
    check("it is given 1e's verdicts",
          any("quality_report" in g for g in rgiven), True, str(rgiven))
    # TEST 7 and TEST 10, the metric-optimization guard and the 10.11
    # invariant. These two are the reason the section exists.
    check("_agent_paths resolves NO path to the raw ai_voice output for the "
          "rewrite pass - so the densities are unreachable, not merely "
          "forbidden", any("ai_voice" in g for g in rgiven), False,
          str(rgiven))
    read_half = rb["prompt"].split("YOU ARE DENIED")[0]
    for rule in ("rule_of_three", "em_dash_density", "boldface_density",
                 "paragraph_opener_filler"):
        check("...and the brief names no density rule (%s)" % rule,
              rule in read_half, False)
    check("the denial is stated, so a reader of the brief knows why",
          "ai_voice.json" in rb["prompt"], True)
    check("...and says the densities are what is behind it",
          "densities" in rb["prompt"], True)

    # TEST 9: `Left alone` is mandatory, and here it means something narrow.
    check("a Left alone list is mandatory", "Left alone" in rb["prompt"], True)
    check("...and means `1e said apply and I still disagreed`",
          "still disagreed" in rb["prompt"], True)
    # TEST 8: the claim is unchanged, inherited from 1c.
    check("the preservation invariant is re-asserted against this brief",
          all(x in rb["prompt"] for x in ("multiset", "citekey", "snapshot")),
          True)
    check("...and the four register floors bind it too",
          "register floors" in rb["prompt"].lower()
          or "no reading level" in rb["prompt"], True)
    check("a `leave` may not be overturned by the pass",
          "do not revisit" in rb["prompt"], True)

    # --- the findings file, and item 80 applied to the check added after it
    res = ms.write_ai_voice_findings(root, "Langmuir")
    check("the payload is written where 1e will look for it",
          os.path.basename(res["written"]), "ai_voice.json", res.get("error"))
    payload = json.loads(read(res["written"]))
    check("...carrying both halves - 1e is the one component that sees both",
          sorted(payload)[:3], ["by_rule", "counts", "densities"])

    keep = ms._prose

    def broken(project, cmd, *args):
        if cmd == "ai_voice":
            return {"error": "prose.py ai_voice exploded"}
        return keep(project, cmd, *args)

    setattr(ms, "_prose", broken)
    try:
        dead = ms.write_ai_voice_findings(root, "Langmuir")
    finally:
        setattr(ms, "_prose", keep)
    check("a crashed audit writes no file at all, rather than an empty one - "
          "an empty findings list and a crashed engine must not render alike",
          (dead["written"], bool(dead["error"])), ("", True))

    # --- 11.5's guarantees, binding on 1e, 3.6 and 3.64 identically -----
    #
    # 11.3 made one rewrite pass automatic and 11.1 added a second that can
    # be, so the number of ways a user's edit can be silently undone went
    # from one offered pass to two. These are what keeps that safe, and each
    # pass checks the freeze ITSELF: a guarantee the orchestrator makes on an
    # agent's behalf is a guarantee nothing verifies.
    for module in ("revise-prose --from-comprehension", "quality-check",
                   "revise-prose --from-quality"):
        task = ms.AGENT_MODULES[module]["task"]
        check("%s is told the user's edits outrank every finding" % module,
              "OUTRANK" in task.upper(), True)
        check("...and that a frozen section is not rewritten",
              "FROZEN" in task.upper(), True)
        check("...and that reversing a lesson from the user's own edits is a "
              "FLAG, not an edit - the engine has found the user disagreeing "
              "with a rule, which is not a conflict for an agent to settle",
              "REVERSE A LESSON" in task.upper(), True)
    check("1e may audit a frozen section, and its verdicts there are `leave` "
          "- reporting costs nothing, and an `apply` there would be an "
          "instruction no pass is allowed to follow",
          "`leave`" in ms.AGENT_MODULES["quality-check"]["task"]
          and "FROZEN SECTION" in
          ms.AGENT_MODULES["quality-check"]["task"].upper(), True)
    check("1c needs no restatement: it is GIVEN layer 1 rather than handed a "
          "report that could contradict it, and it already skips frozen "
          "sections",
          "frozen" in ms.AGENT_MODULES["revise-prose"]["task"], True)


def test_prose_report_shape_round_trips(tmp: str) -> None:
    """What the briefs ask for is what learn.py can read (prose 11.5).

    This is CLAUDE.md's duplicated-contract trap in its purest form: the
    shape is declared in `manuscript.py` as an instruction to an agent, and
    parsed in `learn.py` as a regex, and neither file imports the other. The
    two are only safe while something compares them - and the failure mode if
    they drift is the quiet one, because a report the parser cannot read
    looks exactly like a round in which the user reverted nothing.
    """
    section("The Changed shape: what the brief asks for, learn.py can read "
            "(11.5)")

    # Loaded by path under its own module name: tests/x.py and tools/x.py
    # share a name, and that is deliberate (CLAUDE.md).
    _sp = importlib.util.spec_from_file_location(
        "learn_engine_shape", os.path.join(ROOT, "tools", "learn.py"))
    if _sp is None or _sp.loader is None:
        raise ImportError("cannot load tools/learn.py")
    learn = importlib.util.module_from_spec(_sp)
    _sp.loader.exec_module(learn)

    shape = ms.PROSE_REPORT_SHAPE
    for field in ("pass:", "rule:", "before:", "after:", "why:"):
        check("the brief names the `%s` field" % field.rstrip(":"),
              field in shape, True)
    check("...and says why it matters, so an agent does not economize on it",
          "learn.py reads it back" in shape, True)
    check("...and that before/after may not be summarized",
          "NEITHER MAY BE SUMMARIZED" in shape, True)

    # Every brief that writes a prose report carries it. A pass that writes
    # the file without the shape is the drift this test exists to catch.
    for module in ("revise-prose", "revise-prose --from-comprehension",
                   "revise-prose --from-quality"):
        writes = [w[0] for w in ms.AGENT_MODULES[module]["writes"]]
        check("%s writes a prose report" % module,
              any("prose_report" in w for w in writes), True, str(writes))
        check("...so its brief carries the shape",
              ms.AGENT_MODULES[module]["task"].endswith(shape), True)

    # THE ROUND TRIP. Build a report the way the brief describes, and read it
    # with the parser that has to understand it.
    NL = chr(10)
    lines = ["# Prose report", "", "## Changed", ""]
    for ln in shape.splitlines():
        stripped = ln.strip()
        if not stripped.startswith("- "):
            continue
        key = stripped[2:].split(":", 1)[0]
        lines.append("- %s: sentence %s as written here" % (key, key))
    body = NL.join(lines + [""])
    parsed = learn.read_pass_changes(body)
    check("a report written exactly as the brief describes parses",
          len(parsed), 1, body)
    check("...with every field the brief asked for",
          sorted(parsed[0]), ["after", "before", "pass", "rule", "why"])

    # And the guard that matters more than the happy path: Left alone is not
    # read, because a change nobody made is not a change anyone can revert.
    with_left = NL.join([
        "## Changed", "",
        "- before: The buffer serves as the proton source.",
        "- after: The buffer is the proton source.",
        "- why: copula avoidance", "",
        "## Left alone", "",
        "- before: XPS, TEM and XRD confirmed it.",
        "- after: XPS, TEM and XRD confirmed it.",
        "- why: three instruments, not padding", ""])
    parsed = learn.read_pass_changes(with_left)
    check("`## Left alone` contributes nothing to the attribution set",
          len(parsed), 1, [c["before"][:40] for c in parsed])
    check("...and it is the Changed entry that survived",
          "serves as" in parsed[0]["before"], True)


def test_the_live_defect_ledger_is_untouched(before: int, tmp: str) -> None:
    section("This suite's records never reach the ledger that ships")

    # Asked in both directions, and the positive half matters as much as the
    # negative one: a redirect that silently stopped receiving anything would
    # satisfy "the real file did not grow" perfectly while proving nothing.
    mine = os.environ.get("SYSTEM_CHANGES_MD", "")
    check("this suite wrote to a ledger of its own",
          bool(mine) and os.path.isfile(mine), True, mine)
    # The positive half, and it is not "a fixture code landed in this file".
    # Several tests below set SYSTEM_CHANGES_MD to a copy of their own, which
    # is stricter than the inherited default and is why their records are not
    # here. What this has to prove is that the ENGINE resolves the redirect
    # rather than the shipping file - a redirect nothing reads would satisfy
    # the negative half perfectly.
    check("...and the engine resolves it, not the shipping file",
          ms.system_changes_path(), mine,
          "an unread redirect passes the check below for the wrong reason")
    check("...and the shipping ledger gained no items",
          live_ledger_items(), before,
          "a fictional code in system-changes.md is a defect nobody can "
          "reproduce, in the file whose value is that its history is true")


READS_INTRO = """\
# Introduction

Chemoreceptor arrays have been resolved in a dozen mesophilic species by
cryo-electron tomography [@jensen2019arrays]. The hexagonal lattice they form
is the same in every one of them.

Growth temperature, which spans thirty degrees across the species sampled here
and has never been varied within a single imaging study, remains an untested
variable. Nobody has asked the question.

Those are the reasons the present work was undertaken.
"""

READS_DISCUSSION = """\
# Discussion

Order tracks growth temperature rather than phylogeny (Figure 2).

This section is deliberately without numbers until the symmetry analysis has
been run.
"""


def test_how_it_reads_reaches_the_build(tmp: str) -> None:
    """item 81 - readability, voice and metaprose, run by a build at last.

    The three engines that measure how the prose READS were built, were
    tested, and were called by nothing a build does. So every check a build
    ran asked whether the paper was correct and none asked whether it was
    readable, while the completeness report read as a full verdict on the
    draft.

    What this section holds is the wiring AND its limit. RUN IS NOT GATE:
    the findings reach the report, and they never reach `missing`,
    `complete`, or the PAPER NOT COMPLETE block a coauthor reads. The
    500-finding check below is the one that would catch a future change
    turning a measurement into a verdict.
    """
    section("How it reads - readability, voice and metaprose in the build")

    # Nothing drafted yet is not the same fact as nothing found, and the two
    # must not render alike.
    bare = os.path.join(tmp, "reads_bare")
    os.makedirs(bare, exist_ok=True)
    bare_root = ms_scaffold(os.path.join(bare, "bare"))
    rd = ms.reading_report(bare_root)
    check("a project with no prose in it is not measured", rd["checked"], False)
    check("...and says why", "still a stub" in rd["reason"], True, rd["reason"])
    check("...and invents no findings", rd["counts"]["findings"], 0)

    sub = os.path.join(tmp, "reads")
    os.makedirs(sub, exist_ok=True)
    root = build_project(sub)
    ms.init(root, "Langmuir")
    # Three planted faults, one per engine's territory: a paragraph that
    # dead-ends, a paragraph with no bridge into it, and pipeline text that
    # reached the manuscript.
    write(root, "drafts/source_text/introduction.md", READS_INTRO)
    write(root, "drafts/source_text/discussion.md", READS_DISCUSSION)

    rd = ms.reading_report(root, "Langmuir")
    check("all three engines ran", rd["checked"], True, rd["reason"])
    rules = sorted(rd["by_rule"])
    check("a paragraph that hands off to nothing is reported",
          "tail_rank_last" in rules, True, rules)
    check("a paragraph with no bridge into it is reported",
          "given_new_gap" in rules, True, rules)
    check("pipeline text that reached the manuscript is reported",
          "meta_prose" in rules, True, rules)
    check("every finding says where it is",
          all(f["location"] for f in rd["findings"]), True)
    check("...and which engine found it",
          {f["check"] for f in rd["findings"]}
          <= {"ai_voice", "metaprose", "readability", "entity_forms",
              "abbreviations"}, True,
          "ai_voice joined the reading block when prose 10.3 was built, and "
          "entity_forms and abbreviations when user-asks-2 was. `voice` is "
          "never in this list because it returns no findings at all - it is "
          "the instrument, not the verdict, and `spelling` and `italics` are "
          "never in it because their findings are ACTIONS this pass already "
          "took")
    check("...and `voice`, `spelling` and `italics` are not among them",
          {"voice", "spelling", "italics"}
          & {f["check"] for f in rd["findings"]}, set())
    check("the reader it was measured for is named", rd["audience"],
          "specialist")
    check("and the whole block says it is advisory", rd["advisory"], True)

    intro = [r for r in rd["voice"] if r["section"] == "introduction"]
    check("voice measures the drafted sections", len(intro), 1,
          [r["section"] for r in rd["voice"]])
    check("...and reports the spread, not only the mean",
          intro[0]["sentence_sd"] > 0, True, intro[0])
    check("...and the opener lengths the digest's rule 9 is about",
          intro[0]["opener_mean"] > 0, True, intro[0])
    keep_results = read(stfile(root, "results.md"))
    write(root, "drafts/source_text/results.md", "# Results\n")
    stubbed = ms.reading_report(root, "Langmuir")
    check("a section still a stub is not a row of zeros",
          [r["section"] for r in stubbed["voice"] if r["section"] == "results"],
          [])
    check("...and is not counted among the drafted sections",
          "results" in stubbed["sections"], False)
    write(root, "drafts/source_text/results.md", keep_results)

    # metaprose MEASURED the paragraph. A build does not write flags into the
    # user's prose; `--insert` is the skill's gesture, on request.
    check("metaprose did not write into the manuscript",
          "meta-prose" in read(stfile(root, "discussion.md")), False)

    done = ms.completeness(root, "Langmuir")
    check("the completeness report carries it",
          bool(done.get("reading", {}).get("voice")), True)
    check("...with the findings in it", done["reading"]["counts"]["findings"]
          == len(rd["findings"]), True)
    details = {f["detail"] for f in done["reading"]["findings"]}
    check("...and not one of them in the outstanding list",
          [m for m in done["missing"] if m["detail"] in details], [])
    check("...nor as an area of its own",
          "reading" in done["by_area"], False, sorted(done["by_area"]))

    # RUN IS NOT GATE, measured rather than asserted: 500 findings must move
    # nothing. A future change that folds these into the verdict fails here.
    loud = dict(done["reading"])
    loud["findings"] = [{"check": "readability", "rule": "tail_rank_last",
                         "severity": "advisory",
                         "location": "introduction ¶%d" % i,
                         "detail": "planted finding %d" % i}
                        for i in range(500)]
    loud["by_rule"] = {"tail_rank_last": 500}
    loud["counts"] = dict(loud["counts"], findings=500)
    keep = ms.reading_report
    setattr(ms, "reading_report", lambda project, journal=None: loud)
    try:
        again = ms.completeness(root, "Langmuir")
    finally:
        setattr(ms, "reading_report", keep)
    check("500 reading findings do not change the verdict",
          (again["complete"], len(again["missing"]), again["blocking"]),
          (done["complete"], len(done["missing"]), done["blocking"]))
    check("...and do not reach the coauthor's block",
          ms.render_incomplete_block(again, "Langmuir", 1),
          ms.render_incomplete_block(done, "Langmuir", 1))

    # The same invariant for the 15 AI-voice rules (prose 10.8 test 1). Not
    # one of the 25 is a layer-0 invariant, so not one may block - and the
    # densities are planted too, because the block they travel in is new and
    # a printer is exactly where a number quietly becomes a verdict.
    av_rules = ["filler_phrase", "copula_avoidance", "ai_vocabulary",
                "ai_vocabulary_contextual", "hyphen_compound",
                "hyphen_compound_contextual", "negative_parallelism",
                "false_range", "generic_conclusion", "hedge_stack",
                "inline_header_list", "synonym_cycling",
                "structural_predictability", "rule_of_three",
                "em_dash_density"]
    louder = dict(done["reading"])
    louder["findings"] = [
        {"check": "ai_voice", "rule": av_rules[i % len(av_rules)],
         "severity": "advisory", "location": "discussion ¶%d" % i,
         "detail": "planted ai_voice finding %d" % i}
        for i in range(500)]
    louder["by_rule"] = {r: 500 // len(av_rules) for r in av_rules}
    louder["counts"] = dict(louder["counts"], findings=500)
    louder["ai_voice_densities"] = [
        {"file": "x", "section": "discussion", "words": 100,
         "em_dashes": 99, "em_dashes_per_1000": 990.0,
         "en_dashes_not_counted": 0, "boldface": 99, "boldface_per_1000": 990.0,
         "paragraph_opener_filler": 99, "openers": [],
         "three_item_lists": 99, "triples": []}]
    setattr(ms, "reading_report", lambda project, journal=None: louder)
    try:
        shouty = ms.completeness(root, "Langmuir")
    finally:
        setattr(ms, "reading_report", keep)
    check("500 AI-voice findings across all 15 rules move nothing either",
          (shouty["complete"], len(shouty["missing"]), shouty["blocking"]),
          (done["complete"], len(done["missing"]), done["blocking"]))
    check("...nor does a density of 990 em dashes per thousand words",
          ms.render_incomplete_block(shouty, "Langmuir", 1),
          ms.render_incomplete_block(done, "Langmuir", 1))
    check("...and ai_voice is not an area of its own",
          "ai_voice" in shouty["by_area"], False, sorted(shouty["by_area"]))

    # prose 10.8 test 6, at the level that matters: the subcommand exits 0
    # however many findings it produced. A gate on a prose measurement makes
    # the drafter optimize the metric.
    loud_file = os.path.join(tmp, "loud_ai_voice.md")
    with open(loud_file, "w", encoding="utf-8") as fh:
        fh.write("# Discussion\n\nIn order to delve into the landscape, "
                 "this serves as a testament to the state-of-the-art, "
                 "data-driven showcase. More research is needed.\n")
    run = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "prose.py"),
         "ai_voice", loud_file, "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("ai_voice exits 0 on a file full of findings", run.returncode, 0,
          run.stderr[-200:])
    payload = json.loads(run.stdout)
    check("...and it found them", payload["counts"]["findings"] > 5, True,
          payload["counts"])

    # A check that crashed is a check that did not run (item 80). The block
    # added to carry measurements is the last place that may read as clean.
    keep_prose = ms._prose

    def broken(project, cmd, *args):
        if cmd in ms.READING_CHECKS:
            return {"error": "prose.py %s exploded" % cmd}
        return keep_prose(project, cmd, *args)

    setattr(ms, "_prose", broken)
    try:
        crashed = ms.reading_report(root, "Langmuir")
    finally:
        setattr(ms, "_prose", keep_prose)
    check("a crashed engine is not reported as clean", crashed["checked"],
          False)
    # Pinned against READING_CHECKS itself rather than against a literal: the
    # list grew by one on 2026-09-18 (`spelling`), and a hand-written copy of
    # it here is a second place to remember. What is being asserted is that
    # EVERY reading check that crashed is named, which is the item-80 rule -
    # not which checks there happen to be.
    check("...and every one that failed is named",
          sorted(e["check"] for e in crashed["errors"]),
          sorted(ms.READING_CHECKS))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ms.print_reading(crashed)
    out = buf.getvalue()
    check("...loudly", "DID NOT RUN" in out, True, out)
    check("...naming the module and the subcommand",
          "prose.py readability" in out, True, out)

    cli = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "manuscript.py"),
         "completeness", root, "--journal", "Langmuir"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("the report prints it unasked", "how it reads" in cli.stdout, True)
    check("...and says it blocks nothing",
          "none of it blocks a build" in cli.stdout, True)
    check("...with the sentence-length table under it",
          "length mean/sd" in cli.stdout, True)
    cli_json = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "manuscript.py"),
         "completeness", root, "--journal", "Langmuir", "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    payload = json.loads(cli_json.stdout)
    check("--json carries it too", bool(payload["reading"]["voice"]), True)
    check("...marked advisory in the payload itself",
          payload["reading"]["advisory"], True)

    # --- item 80: a crashed check is never a clean pass -------------------
    #
    # The reading engines are the EASY half - they already said DID NOT RUN.
    # What item 80 is about is the other 24 call sites, which read the key
    # they came for, get an absent key on an error dict, default to empty,
    # and add nothing. The build then exits as though the check had run.
    def break_cmd(which: str):
        def broken(project, cmd, *args):
            if cmd == which:
                return ms._prose_failed(cmd, args, "exploded on purpose")
            return keep_prose(project, cmd, *args)
        return broken

    # A correctness check that did not run BLOCKS, because its findings could
    # have. `crossrefs` is the case in point: it reads `findings` and nothing
    # else, so its crash used to be silent in every build.
    setattr(ms, "_prose", break_cmd("crossrefs"))
    try:
        crashed_xref = ms.completeness(root, "Langmuir")
    finally:
        setattr(ms, "_prose", keep_prose)
    named = [m for m in crashed_xref["missing"]
             if "prose.py crossrefs" in m["detail"]]
    check("a crashed correctness check reaches the outstanding list",
          len(named), 1, [m["detail"] for m in crashed_xref["missing"]][:4])
    check("...as blocking", named and named[0]["severity"], "blocking")
    check("...so the build cannot report a clean pass",
          crashed_xref["complete"], False)
    check("...and it names the subcommand and the error",
          "exploded on purpose" in named[0]["detail"], True, named[0])
    check("...and the payload carries it as a check that did not run",
          [f["cmd"] for f in crashed_xref["checks_not_run"]], ["crossrefs"])
    check("...and the coauthor's own block says so",
          "prose.py crossrefs" in
          ms.render_incomplete_block(crashed_xref, "Langmuir", 1), True)

    # A reading check that did not run does NOT block - nothing about how the
    # prose reads ever may (item 81) - but it is still named, in the summary
    # line itself, because "did not run" and "found nothing" must not render
    # alike. This is the half of item 80 that RUN IS NOT GATE constrains.
    setattr(ms, "_prose", break_cmd("voice"))
    try:
        crashed_voice = ms.completeness(root, "Langmuir")
    finally:
        setattr(ms, "_prose", keep_prose)
    check("a crashed reading check blocks nothing",
          (crashed_voice["complete"], crashed_voice["blocking"]),
          (done["complete"], done["blocking"]))
    check("...but is named in the summary",
          "DID NOT RUN: prose.py voice" in crashed_voice["summary"], True,
          crashed_voice["summary"])
    check("...and is carried as a non-blocking check that did not run",
          [(f["cmd"], f["blocking"]) for f in
           crashed_voice["checks_not_run"]], [("voice", False)])
    check("a clean build lists no check as having failed",
          done["checks_not_run"], [])

    # The ledger is what makes this general. A call site written next year is
    # covered without anybody remembering to cover it, so the guarantee is
    # asserted against the helper rather than against the 25 callers.
    ms.clear_prose_failures()
    ms._prose_failed("citekeys", (), "boom")
    ms._prose_failed("voice", (), "boom")
    check("the ledger records every failed call, in order",
          [(f["cmd"], f["blocking"]) for f in ms.prose_failures()],
          [("citekeys", True), ("voice", False)])
    check("...and a fresh report starts from an empty one",
          (ms.completeness(root, "Langmuir")["checks_not_run"]), [])

    # --- item 87: the same hole, in the engines that are not prose.py -----
    #
    # Worse than the prose case, because it does not merely omit a check: it
    # asserts that the user's written plan is EMPTY, which is the one input
    # the outline module is told to trust. The fix is item 80's - record the
    # failure where the subprocess dies - so what is asserted here is the
    # helper's guarantee, not one caller's handling of it.
    ms.clear_prose_failures()
    ms._engine_failed("idea.py", "context", (), "died")
    check("the ledger is every engine's now, not just prose.py's",
          [(f["engine"], f["cmd"], f["blocking"])
           for f in ms.prose_failures()],
          [("idea.py", "context", True)],
          "only prose.py has a reading half; every other engine answers a "
          "question with a right answer, so its silence blocks")

    keep_idea = ms._idea

    def dead_idea(project, cmd, *args):
        return ms._engine_failed("idea.py", cmd, args, "exploded on purpose")

    ms.clear_prose_failures()
    setattr(ms, "_idea", dead_idea)
    try:
        blind = ms.outline_sources(root)
    finally:
        setattr(ms, "_idea", keep_idea)
    ms.clear_prose_failures()
    seeing = ms.outline_sources(root)

    readme = [x for x in blind["sources"] if x["name"] == "plan/README.md"][0]
    check("a dead engine is reported as UNREAD, never as an empty file",
          ("NOT READ" in readme["detail"],
           "says anything yet" in readme["detail"]),
          (True, False),
          "the inventory used to assert the opposite of the truth about the "
          "one file the outline module is told to trust")
    check("...and the failure is named in the payload",
          [e.split(":")[0] for e in blind["engine_errors"]],
          ["idea.py context"])
    check("...and the note says the inventory is incomplete rather than that "
          "the project is empty",
          ("INCOMPLETE" in blind["note"], "are all empty" in blind["note"]),
          (True, False))
    check("...and unknown is not the same answer as no",
          (blind["unknown"], blind["can_draft"]), (True, False))
    check("a healthy inventory says nothing about engines at all",
          (seeing["engine_errors"], seeing["unknown"]), ([], False))

    if not shutil.which("pandoc"):
        skip("the build-level reading checks", "pandoc is not on PATH")
        return
    built = ms.assemble(root, "Langmuir")
    check("the build carries it at the top level", built["errors"], [])
    check("...as `reading`", bool(built.get("reading", {}).get("voice")), True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ms.print_assemble(built)
    check("...and a build prints it without being asked",
          "how it reads" in buf.getvalue(), True, buf.getvalue())

def test_round_archive_layout(tmp: str) -> None:
    section("A closed round archives into its own folder (round-archive 3)")

    sub_tmp = os.path.join(tmp, "archive_layout")
    os.makedirs(sub_tmp, exist_ok=True)
    root = build_project(sub_tmp)
    ms.init(root, "Langmuir")

    # --- 1. the snapshot nests, and what retired beside it does not ------
    write(root, "drafts/source_text/results.md", "# Results\n\nr1 prose.\n")
    write(root, "drafts/Langmuir/submission/manuscript_r1.docx", "not a docx")
    write(root, "drafts/Langmuir/reports/r1/citation_report.md", "# Cites\n")
    write(root, "drafts/edits/JV_r1.docx", "not a docx")
    ms.open_round(root, "Langmuir")

    r1 = os.path.join(root, "obsolete", "drafts", "r1")
    check("a closed round's prose is in a folder of its own",
          os.path.isfile(os.path.join(r1, "source_text_r1", "results.md")),
          True, sorted(os.listdir(r1)))
    check("...carrying what that round actually said",
          "r1 prose." in read(os.path.join(r1, "source_text_r1",
                                           "results.md")), True)
    check("...and the section files are NOT loose in the round folder",
          os.path.isfile(os.path.join(r1, "results.md")), False,
          "flat is the layout every project archived before 2026-09-16 has, "
          "and it is what this step moves away from")
    check("...while submission/, reports/ and edits/ retired beside it",
          [os.path.isdir(os.path.join(r1, d))
           for d in ("submission", "reports", "edits")],
          [True, True, True], sorted(os.listdir(r1)))
    check("...and the manifest is at the round folder's root",
          os.path.isfile(os.path.join(r1, "manifest.md")), True)

    # --- 2. the archive says what the folder was CALLED -------------------
    sub2 = os.path.join(tmp, "archive_unlabelled")
    os.makedirs(sub2, exist_ok=True)
    root2 = build_project(sub2)
    ms.init(root2, "Langmuir")
    drafts2 = os.path.join(root2, "drafts")
    # A project that predates the label: rename the live folder back by hand.
    os.rename(os.path.join(drafts2, "source_text_r1"),
              os.path.join(drafts2, "source_text"))
    check("the live folder is the unlabelled one",
          os.path.basename(ms.source_text_dir(root2)), "source_text")
    write_raw(os.path.join(drafts2, "source_text", "results.md"),
              "# Results\n\nUnlabelled prose.\n")
    write(root2, "drafts/Langmuir/submission/manuscript_r1.docx", "not a docx")
    ms.open_round(root2, "Langmuir")
    check("an unlabelled live folder archives under its own name",
          os.path.isfile(os.path.join(root2, "obsolete", "drafts", "r1",
                                      "source_text", "results.md")), True,
          sorted(os.listdir(os.path.join(root2, "obsolete", "drafts", "r1"))))
    check("...not under a label it never had",
          os.path.isdir(os.path.join(root2, "obsolete", "drafts", "r1",
                                     "source_text_r1")), False,
          "the archive says what the folder was called, not what it should "
          "have been called")

    # --- 3. the baseline resolves through BOTH layouts -------------------
    sub3 = os.path.join(tmp, "archive_both")
    os.makedirs(sub3, exist_ok=True)
    flat = build_project(sub3)
    os.makedirs(os.path.join(flat, "obsolete", "drafts", "r1"), exist_ok=True)
    write_raw(os.path.join(flat, "obsolete", "drafts", "r1", "methods.md"),
              "# Methods\n\nFilms were grown at 450 C for 20 min.\n")
    base = ms.retention_baseline(flat, "methods", 2)
    check("a FLAT r1 archive is still a baseline at r2", base["kind"],
          "snapshot", base)
    check("...and it names the file it read",
          base["where"], "obsolete/drafts/r1/methods.md")
    check("...and carries the text", "450 C" in base["text"], True)

    nested = os.path.join(sub3, "nested_project")
    shutil.copytree(flat, nested)
    shutil.rmtree(os.path.join(nested, "obsolete", "drafts", "r1"))
    os.makedirs(os.path.join(nested, "obsolete", "drafts", "r1",
                             "source_text_r1"), exist_ok=True)
    write_raw(os.path.join(nested, "obsolete", "drafts", "r1",
                           "source_text_r1", "methods.md"),
              "# Methods\n\nFilms were grown at 450 C for 20 min.\n\n"
              "Thickness was measured by SEM on three cross-sections.\n")
    base = ms.retention_baseline(nested, "methods", 2)
    check("a NESTED r1 archive is a baseline at r2", base["kind"],
          "snapshot", base)
    check("...and names the nested path",
          base["where"], "obsolete/drafts/r1/source_text_r1/methods.md")
    check("neither layout resolves to `none`",
          [ms.retention_baseline(p, "methods", 2)["kind"]
           for p in (flat, nested)], ["snapshot", "snapshot"],
          "a reader that handles one layout and not the other reports "
          "nothing and exits 0, which is the failure this toolkit exists "
          "to catch arriving through a folder rename")

    # An unlabelled archived folder, and a label that disagrees with the
    # round - the two cases resolution order 2 and 3 exist for.
    odd = os.path.join(sub3, "odd_label")
    shutil.copytree(flat, odd)
    shutil.rmtree(os.path.join(odd, "obsolete", "drafts", "r1"))
    os.makedirs(os.path.join(odd, "obsolete", "drafts", "r1", "source_text"),
                exist_ok=True)
    write_raw(os.path.join(odd, "obsolete", "drafts", "r1", "source_text",
                           "methods.md"), "# Methods\n\nAt 450 C.\n")
    check("an unlabelled archived folder resolves",
          ms.retention_baseline(odd, "methods", 2)["kind"], "snapshot")
    shutil.rmtree(os.path.join(odd, "obsolete", "drafts", "r1"))
    os.makedirs(os.path.join(odd, "obsolete", "drafts", "r1",
                             "source_text_r4"), exist_ok=True)
    write_raw(os.path.join(odd, "obsolete", "drafts", "r1", "source_text_r4",
                           "methods.md"), "# Methods\n\nAt 450 C.\n")
    check("a label that disagrees with the round still resolves",
          ms.retention_baseline(odd, "methods", 2)["kind"], "snapshot",
          "a hand-edited round_state.json or an interrupted run")
    check("a round with no archive at all resolves to nothing",
          ms.retired_section_path(odd, 3, "methods"), "")
    check("...as does a section that was never archived",
          ms.retired_section_path(odd, 1, "discussion"), "")

    # --- 4. the invariant FIRES through the nested layout -----------------
    # Resolving a path and using the text it points at are two different
    # claims, so they get two checks.
    aliases = {"methods": "methods"}
    dropped = "# Methods\n\nFilms were grown at 450 C for 20 min.\n"
    res = ms.retention_check(nested, "methods", dropped, 2, aliases)
    check("a sentence r1 had and r2 drops is REPORTED through the nest",
          res["ok"], False, res["findings"])
    check("...naming the rule",
          "sentence_dropped" in [f["rule"] for f in res["findings"]], True,
          res["findings"])
    check("...and the finding points at the nested archive",
          "source_text_r1" in res["baseline_path"], True,
          res["baseline_path"])
    kept = ("# Methods\n\nFilms were grown at 450 C for 20 min.\n\n"
            "Thickness was measured by SEM on three cross-sections.\n")
    check("...while a pass that keeps it is accepted",
          ms.retention_check(nested, "methods", kept, 2, aliases)["ok"],
          True, ms.retention_check(nested, "methods", kept, 2,
                                   aliases)["findings"])

    # --- 5. `changed since` survives r2-flat -> r3-nested -----------------
    sub5 = os.path.join(tmp, "archive_changed")
    os.makedirs(sub5, exist_ok=True)
    root5 = build_project(sub5)
    ms.init(root5, "Langmuir")
    # r2's archive written by hand in the FLAT layout, as an older engine
    # left it, with a manifest keyed by bare basename.
    live = os.path.basename(ms.source_text_dir(root5))
    sections = sorted(f for f in os.listdir(ms.source_text_dir(root5))
                      if f.endswith(".md"))
    flat_r2 = os.path.join(root5, "obsolete", "drafts", "r2")
    os.makedirs(flat_r2, exist_ok=True)
    items = []
    for name in sections:
        body = "# %s\n\nr2 said this.\n" % name[:-3]
        write_raw(os.path.join(flat_r2, name), body)
        items.append({"file": name,
                      "sha256": ms._sha256(os.path.join(flat_r2, name))})
    write_raw(os.path.join(flat_r2, "manifest.json"), json.dumps(
        {"round": 2, "date": "2026-01-01", "files": items}, indent=2))
    # The live folder becomes r3 and holds the same text but for one edit.
    os.rename(os.path.join(root5, "drafts", live),
              os.path.join(root5, "drafts", "source_text_r3"))
    for name in sections:
        body = "# %s\n\nr2 said this.\n" % name[:-3]
        if name == "results.md":
            body = "# results\n\nr3 says something else.\n"
        write_raw(os.path.join(root5, "drafts", "source_text_r3", name), body)
    ms.set_project_revision(root5, 3)
    ms.update_round_state(ms.journal_dir(root5, "Langmuir"), round=3)
    write(root5, "drafts/Langmuir/submission/manuscript_r3.docx", "not a docx")
    ms.open_round(root5, "Langmuir")

    man = read(os.path.join(root5, "obsolete", "drafts", "r3", "manifest.md"))
    rows = [ln for ln in man.splitlines()
            if ln.startswith("| ") and ".md " in ln]
    yes = [r for r in rows if r.rstrip().endswith("| YES |")]
    check("exactly one section is marked changed since r2", len(yes), 1, man)
    check("...and it is the one that was edited",
          yes[0].split("|")[1].strip() if yes else "",
          "source_text_r3/results.md", man)
    check("...the rest read `no`",
          len([r for r in rows if r.rstrip().endswith("| no |")]),
          len(sections) - 1, man)
    check("...and none reads `-`",
          [r for r in rows if r.rstrip().endswith("| - |")], [],
          "a column of dashes still renders, still builds and still exits 0 "
          "- which is exactly why the comparison is on the basename")
    data = json.loads(read(os.path.join(root5, "obsolete", "drafts", "r3",
                                        "manifest.json")))
    check("the manifest carries both the path and the basename",
          sorted({k for f in data["files"] for k in f}),
          ["file", "name", "sha256"], data["files"][:1])

    # --- 14. a round-shaped journal name is refused -----------------------
    for bad in ("r2", "R2", "r12"):
        try:
            ms.journal_folder(bad)
            got = "no refusal"
        except ValueError as exc:
            got = "refused" if "round" in str(exc).lower() else str(exc)
        check("a journal abbreviated %r is refused" % bad, got, "refused",
              "it case-folds onto obsolete/drafts/rN/ on Windows, and the "
              "retired journal and the round snapshot would land on top of "
              "each other")
    check("a name that merely starts with r is fine",
          ms.journal_folder("rsc"), "RSC")
    check("...and so is one with digits elsewhere",
          ms.journal_folder("JPCC2"), "JPCC2")


def wr(path: str, body: str) -> None:
    """write_raw, but making the directories on the way.

    A retirement test builds folders that only a real run would
    otherwise create - a round's reports/, a journal's edits/.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_raw(path, body)


def _walk_to_r(root: str, journal: str, upto: int) -> None:
    """Close rounds in `journal` until the project's counter reaches `upto`."""
    jdir = ms.journal_dir(root, journal)
    while ms.current_round(jdir) < upto:
        rnd = ms.current_round(jdir)
        wr(os.path.join(jdir, "submission",
                               "manuscript_r%d.docx" % rnd), "not a docx")
        ms.open_round(root, journal)


def coauthor_ledger(state: str = "pending") -> str:
    """One coauthor row, written by the engine's own renderer.

    Hand-writing the table would pin a row shape `_render_status` is free to
    change; the contract is the renderer/parser pair, which is what this file
    already round-trips elsewhere.
    """
    rollup = {"JV": {"received": "r2", "items": 1,
                     "applied": 1 if state == "applied" else 0,
                     "pending": 1 if state == "pending" else 0,
                     "conflict": 0, "declined": 0}}
    row = {"id": "JV-1", "round": "r2", "source": "JV_r2.docx",
           "authority": "coauthor",
           "location": "The films were grown at 450 C.",
           "type": "tracked", "request": "grown at 450 C in argon",
           "state": state, "note": "", "fingerprint": "0123456789"}
    return ms._render_status("Langmuir", 2, rollup, [row],
                             {"JV": "June Vale"})


def test_retire_journal(tmp: str) -> None:
    section("A spent journal folder retires whole (round-archive 4)")

    # --- 6. the whole folder moves, and nothing else does -----------------
    sub = os.path.join(tmp, "retire_move")
    os.makedirs(sub, exist_ok=True)
    root = build_project(sub)
    ms.init(root, "Langmuir")
    _walk_to_r(root, "Langmuir", 3)
    jdir = ms.journal_dir(root, "Langmuir")
    wr(os.path.join(jdir, "reports", "r3", "run_log.md"),
              "# Run log\n\n- r3 did things\n")
    wr(os.path.join(jdir, "journal_requirements", "requirements.yml"),
              "checked: 2026-01-01\n")
    live_text = os.path.basename(ms.source_text_dir(root))
    bib_before = read(ms.bib_path(root))

    res = ms.retire_journal(root, "Langmuir", dry_run=True)
    check("--dry-run reports the move", res["errors"], [])
    check("...names every file it would move",
          len(res["moved"]) > 3, True, res["moved"])
    check("...and moves nothing", os.path.isdir(jdir), True)
    check("...and writes no manifest",
          os.path.exists(os.path.join(root, "obsolete", "drafts",
                                      "Langmuir")), False)

    res = ms.retire_journal(root, "Langmuir")
    dest = os.path.join(root, "obsolete", "drafts", "Langmuir")
    check("the journal folder is gone from drafts/",
          os.path.isdir(jdir), False, res["errors"])
    check("...and is whole in obsolete/drafts/",
          os.path.isfile(os.path.join(dest, "journal_requirements",
                                      "requirements.yml")), True,
          res["errors"])
    check("...with the round's run log at its own depth",
          os.path.isfile(os.path.join(dest, "reports", "r3", "run_log.md")),
          True)
    check("...and a manifest beside it",
          os.path.isfile(os.path.join(dest, "manifest.md")), True)
    check("...naming the journal and every round it covers",
          ("# Langmuir" in read(os.path.join(dest, "manifest.md")),
           "r1" in read(os.path.join(dest, "manifest.md"))), (True, True))
    check("the live source text is untouched",
          os.path.basename(ms.source_text_dir(root)), live_text)
    check("...and so is the bibliography", read(ms.bib_path(root)),
          bib_before)
    check("...and plan/ and data/ are where they were",
          [os.path.isdir(os.path.join(root, d)) for d in ("plan", "data")],
          [True, True])
    check("drafts/ holds no journal folder now",
          ms.existing_journals(root), [])
    check("...but project.yml still says the paper went there",
          ms.retired_journals(root), ["Langmuir"])
    check("...and status reports it in its own field",
          ms.status(root)["retired_journals"], ["Langmuir"],
          "a user must never be told a paper has never been to a journal it "
          "was rejected from")
    check("...while `journals:` is not edited - it is history and stays true",
          "Langmuir" in ms.status(root)["cached_journals"], True,
          ms.status(root)["cached_journals"])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ms.print_status(ms.status(root))
    printed = buf.getvalue()
    check("...and a text-mode status says it out loud",
          ("retired:" in printed, "Langmuir" in printed), (True, True),
          printed[:300])

    # --- 13. a retired journal's run logs are still findable --------------
    found = ms.run_log_paths(root, "JPCC")
    check("a retired journal's run log is still found",
          [r for r, _p in found], [3],
          "RUN_LOG_ROUND_RE matches the full path, so the extra depth is "
          "not a problem - recorded because it looks like one")
    check("...at its new home",
          all("obsolete" in p for _r, p in found), True, found)

    # --- 7. the counter survives the folder move, in BOTH orders ----------
    res = ms.init(root, "JPCC")
    check("a new journal after a retirement opens at the next round",
          res["round"], 4,
          "existing_journals cannot see LANGMUIR any more, so without the "
          "project.yml:rounds ledger this falls back to project_revision - "
          "which is 3, not 4, and two manuscripts are both manuscript_r3")

    sub_b = os.path.join(tmp, "retire_order")
    os.makedirs(sub_b, exist_ok=True)
    root_b = build_project(sub_b)
    ms.init(root_b, "Langmuir")
    _walk_to_r(root_b, "Langmuir", 3)
    res_b = ms.init(root_b, "JPCC")
    check("init-then-retire opens at the next round too", res_b["round"], 4)
    check("...and init OFFERS the retirement rather than running it",
          [(a["action"], a["path"]) for a in res_b["actions"]
           if a["action"] == "offer"],
          [("offer", "drafts/Langmuir/")],
          str([(a["action"], a["path"]) for a in res_b["actions"]]))
    check("...naming the command and saying nothing has moved",
          all(s in [a["detail"] for a in res_b["actions"]
                    if a["action"] == "offer"][0]
              for s in ("retire-journal", "--to JPCC",
                        "Nothing is moved until you run it")), True,
          [a["detail"] for a in res_b["actions"] if a["action"] == "offer"])
    check("...and LANGMUIR is still there",
          os.path.isdir(ms.journal_dir(root_b, "Langmuir")), True)
    ms.retire_journal(root_b, "Langmuir", to="JPCC")
    check("retiring after the switch leaves one live journal",
          ms.existing_journals(root_b), ["JPCC"])
    check("...and the round JPCC opened at did not move",
          ms.current_round(ms.journal_dir(root_b, "JPCC")), 4)

    # A THIRD journal, with both earlier ones retired - which is the case the
    # ledger exists for. With neither folder in drafts/, `existing_journals`
    # sees nothing at all and project_revision is the round JPCC is sitting
    # on; only `project.yml:rounds` still knows r1-r4 are spent.
    _walk_to_r(root_b, "JPCC", 6)
    ms.retire_journal(root_b, "JPCC", to="ACSNano")
    check("both earlier journals are retired",
          (ms.existing_journals(root_b), sorted(ms.retired_journals(root_b))),
          ([], ["JPCC", "Langmuir"]))
    third = ms.init(root_b, "ACSNano")
    check("a third journal still opens at the next round", third["round"], 7,
          "project.yml:rounds is the only one of the three counter sources a "
          "directory move cannot invalidate")
    check("...and drafts/ holds exactly one journal folder again",
          ms.existing_journals(root_b), ["ACSNano"],
          "mixed case is a proper noun and is kept (spec 2.1)")
    check("...beside one source_text folder, the inbox, the bib and the log",
          sorted(f for f in os.listdir(os.path.join(root_b, "drafts"))
                 if not f.startswith(".")),
          ["ACSNano", "edits", "log.md", "references.bib", "rough_draft.md",
           "source_text_r7"],
          "the user's stated goal: one working journal directory in drafts/ - "
          "and `edits/` is not one, which is why two journal retirements in a "
          "row leave it untouched")

    # --- 8. standing coauthor edits refuse the move -----------------------
    sub_c = os.path.join(tmp, "retire_edits")
    os.makedirs(sub_c, exist_ok=True)
    root_c = build_project(sub_c)
    ms.init(root_c, "Langmuir")
    jdir_c = ms.journal_dir(root_c, "Langmuir")
    wr(os.path.join(jdir_c, "edits", "edits_status.md"), coauthor_ledger())
    check("the ledger holds one standing coauthor row",
          [it["id"] for it in ms.standing_coauthor_rows(jdir_c)], ["JV-1"])
    res_c = ms.retire_journal(root_c, "Langmuir")
    check("a retirement with no --to is REFUSED",
          bool(res_c["errors"]), True)
    check("...and the refusal names the author's file and the row's state",
          all(s in res_c["errors"][0]
              for s in ("JV_r2.docx", "pending", "JV-1", "--to")), True,
          res_c["errors"])
    check("...and nothing moved",
          os.path.isdir(jdir_c), True)
    check("...because a coauthor's edit stands until SUBMISSION",
          "rejection is not a submission" in res_c["errors"][0], True)

    # --- 9. --to carries the ledger forward -------------------------------
    ms.init(root_c, "JPCC")
    res_c = ms.retire_journal(root_c, "Langmuir", to="JPCC")
    check("with --to the retirement goes ahead", res_c["errors"], [])
    carried = os.path.join(root_c, "drafts", "JPCC", "edits",
                           "edits_status.md")
    check("...the new journal has the ledger",
          "JV-1" in read(carried), True)
    check("...and the archive keeps a copy",
          "JV-1" in read(os.path.join(root_c, "obsolete", "drafts",
                                      "Langmuir", "edits",
                                      "edits_status.md")), True)

    sub_d = os.path.join(tmp, "retire_two_ledgers")
    os.makedirs(sub_d, exist_ok=True)
    root_d = build_project(sub_d)
    ms.init(root_d, "Langmuir")
    ms.init(root_d, "JPCC")
    wr(os.path.join(ms.journal_dir(root_d, "Langmuir"), "edits",
                           "edits_status.md"), coauthor_ledger())
    wr(os.path.join(ms.journal_dir(root_d, "JPCC"), "edits",
                           "edits_status.md"), coauthor_ledger())
    res_d = ms.retire_journal(root_d, "Langmuir", to="JPCC")
    check("a destination that already has a ledger is REFUSED",
          bool(res_d["errors"]), True, res_d)
    check("...saying merging two ledgers is not this command's call",
          "Merging two ledgers" in res_d["errors"][0], True, res_d["errors"])
    check("...and nothing moved",
          os.path.isdir(ms.journal_dir(root_d, "Langmuir")), True)

    # An APPLIED coauthor row is not standing, so it retires with its folder.
    sub_e = os.path.join(tmp, "retire_applied")
    os.makedirs(sub_e, exist_ok=True)
    root_e = build_project(sub_e)
    ms.init(root_e, "Langmuir")
    wr(os.path.join(ms.journal_dir(root_e, "Langmuir"), "edits",
                           "edits_status.md"), coauthor_ledger("applied"))
    res_e = ms.retire_journal(root_e, "Langmuir")
    check("an applied coauthor row does not block the retirement",
          res_e["errors"], [], res_e)

    # --- 10. an open run refuses ------------------------------------------
    sub_f = os.path.join(tmp, "retire_open_run")
    os.makedirs(sub_f, exist_ok=True)
    root_f = build_project(sub_f)
    ms.init(root_f, "Langmuir")
    ms.run_ledger(root_f, "Langmuir", start=True, preset="sections")
    res_f = ms.retire_journal(root_f, "Langmuir")
    check("an open run REFUSES the retirement", bool(res_f["errors"]), True,
          res_f)
    check("...and says how to close it",
          all(s in res_f["errors"][0]
              for s in ("--finish", "--abandon")), True, res_f["errors"])
    check("...and nothing moved",
          os.path.isdir(ms.journal_dir(root_f, "Langmuir")), True)
    ms.run_ledger(root_f, "Langmuir", abandon=True, note="switching journals")
    res_f = ms.retire_journal(root_f, "Langmuir")
    check("...and an abandoned run does not", res_f["errors"], [], res_f)

    # --- 11. the log is hoisted first, and an unmergeable one refuses -----
    sub_g = os.path.join(tmp, "retire_log")
    os.makedirs(sub_g, exist_ok=True)
    root_g = build_project(sub_g)
    ms.init(root_g, "Langmuir")
    wr(os.path.join(ms.journal_dir(root_g, "Langmuir"), "log.md"),
              "# Round history\n\n## r1 - LANGMUIR - 2026-01-01\n\n"
              "We sent it and they said no.\n")
    res_g = ms.retire_journal(root_g, "Langmuir")
    check("a per-journal log.md is hoisted before the move",
          res_g["errors"], [], res_g)
    check("...into the project's one history",
          "they said no" in read(ms.log_path(root_g)), True)
    check("...and it is not in the archive",
          os.path.isfile(os.path.join(root_g, "obsolete", "drafts",
                                      "Langmuir", "log.md")), False)

    sub_h = os.path.join(tmp, "retire_log_stuck")
    os.makedirs(sub_h, exist_ok=True)
    root_h = build_project(sub_h)
    ms.init(root_h, "Langmuir")
    wr(os.path.join(ms.journal_dir(root_h, "Langmuir"), "log.md"),
              "# Round history\n\n## r1 - LANGMUIR - 2026-01-01\n\nText.\n")
    # A destination that cannot be written is a merge that cannot be
    # verified, which is the case the refusal exists for.
    dest_log = ms.log_path(root_h)
    real_hoist = ms.hoist_logs
    try:
        setattr(ms, "hoist_logs", lambda _p: [])
        res_h = ms.retire_journal(root_h, "Langmuir")
    finally:
        setattr(ms, "hoist_logs", real_hoist)
    check("a log.md that survives the hoist REFUSES the move",
          bool(res_h["errors"]), True, res_h)
    check("...saying the merge could not be verified",
          "could not be verified" in res_h["errors"][0], True,
          res_h["errors"])
    check("...and nothing moved",
          os.path.isdir(ms.journal_dir(root_h, "Langmuir")), True)
    check("...and the log is still where it was",
          os.path.isfile(os.path.join(ms.journal_dir(root_h, "Langmuir"),
                                      "log.md")), True, dest_log)

    # --- 12. the hashes are re-taken after the move -----------------------
    sub_i = os.path.join(tmp, "retire_hash")
    os.makedirs(sub_i, exist_ok=True)
    root_i = build_project(sub_i)
    ms.init(root_i, "Langmuir")
    real_move = shutil.move

    def _tamper(src, dst, *a, **kw):
        out = real_move(src, dst, *a, **kw)
        # One file rewritten in the moment between the two digests.
        victim = os.path.join(out if isinstance(out, str) else dst,
                              "writing_config.yml")
        if os.path.isfile(victim):
            with open(victim, "a", encoding="utf-8") as fh:
                fh.write("\n# altered between the two digests\n")
        return out

    try:
        shutil.move = _tamper
        ms.shutil.move = _tamper
        res_i = ms.retire_journal(root_i, "Langmuir")
    finally:
        shutil.move = real_move
        ms.shutil.move = real_move
    check("a file whose digest changed across the move FAILS the command",
          bool(res_i["errors"]), True, res_i)
    check("...naming the file",
          "writing_config.yml" in res_i["errors"][0], True, res_i["errors"])
    check("...and saying where the folder actually is",
          "obsolete/drafts/Langmuir" in res_i["errors"][0], True,
          res_i["errors"])

    # --- the refusals that are about this command's own preconditions -----
    sub_j = os.path.join(tmp, "retire_edges")
    os.makedirs(sub_j, exist_ok=True)
    root_j = build_project(sub_j)
    ms.init(root_j, "Langmuir")
    res_j = ms.retire_journal(root_j, "JPCC")
    check("retiring a journal that was never here is refused",
          "nothing to retire" in (res_j["errors"] or [""])[0], True, res_j)
    res_j = ms.retire_journal(root_j, "Langmuir", to="Langmuir")
    check("--to naming the journal being retired is refused",
          bool(res_j["errors"]), True, res_j)
    ms.retire_journal(root_j, "Langmuir")
    ms.init(root_j, "Langmuir")
    res_j = ms.retire_journal(root_j, "Langmuir")
    check("a second retirement onto an existing archive is refused",
          "already exists" in (res_j["errors"] or [""])[0], True, res_j)
    check("...and nothing moved",
          os.path.isdir(ms.journal_dir(root_j, "Langmuir")), True)


# Wording that would amount to a verdict about the transcript. `handoff`
# cannot see the transcript, so any of these in its output is a confident
# wrong answer at exit 0 - the exact failure this toolkit exists to catch
# (context-compaction 1.2).
BLESSINGS = ("safe to compact", "safe to clear", "ok to clear", "ok to compact",
             "you can clear", "you may clear", "nothing will be lost",
             "nothing would be lost", "everything is recorded",
             "everything is on disk", "no risk", "clear now")


def _flatten(value) -> str:
    if isinstance(value, dict):
        return " ".join(_flatten(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten(v) for v in value)
    return str(value)


def test_handoff(tmp: str) -> None:
    section("handoff - the re-entry payload, built from disk (compaction 4)")

    sub = os.path.join(tmp, "handoff")
    os.makedirs(sub, exist_ok=True)
    root = build_project(sub)
    ms.init(root, "Langmuir")

    # --- 1. every field traces to a file, in four run states --------------
    no_run = ms.handoff(root, "Langmuir")
    check("a project with no run still answers", no_run["errors"], [], no_run)
    check("...and says so in the resume line",
          "No run has been recorded" in no_run["resume_line"], True,
          no_run["resume_line"])
    check("...naming the live source text off disk",
          no_run["source_text"], ms.stpfx(root))
    check("...and the sections it actually holds",
          [s["stem"] for s in no_run["sections"]], ms.section_files(root))
    check("...with a state per section, not a guess",
          sorted({s["state"] for s in no_run["sections"]})[:1], ["drafted"],
          no_run["sections"][:2])
    check("...and no stage, because nothing says which one",
          (no_run["stage"], no_run["read"]), ("", ["SKILL.md"]))

    ms.run_ledger(root, "Langmuir", start=True, preset="submission")
    open_run = ms.handoff(root, "Langmuir")
    check("an open run gives a stage", open_run["stage"], "outline",
          open_run.get("next"))
    check("...and the stage names the part of the skill it needs",
          open_run["read"], ["SKILL.md", "reference/outline.md"])
    check("...and this round's boundaries",
          [b["label"] for b in open_run["boundaries"]],
          ["prose/submission", "after assemble",
           "before submission-package"], open_run["boundaries"])

    ms.run_ledger(root, "Langmuir", step="outline", state="done")
    ms.run_ledger(root, "Langmuir", step="draft-sections", item="methods",
                  state="done")
    mid = ms.handoff(root, "Langmuir")
    check("a run stopped mid-module reports the module, not the next one",
          mid["next"], "literature-landscape", mid["remaining"][:2])
    check("...and what is already finished", mid["done"], ["outline"])

    # --- 4. and every one of those is `run --json`'s, not a second copy ---
    rl = ms.run_ledger(root, "Langmuir")
    ho = ms.handoff(root, "Langmuir")
    for field in ("resume_line", "next", "done", "remaining", "failed",
                  "stale_outputs"):
        check("handoff's `%s` is run's, byte for byte" % field,
              ho[field], rl[field],
              "a re-entry payload built from a second source of truth is a "
              "second source of truth")

    ms.run_ledger(root, "Langmuir", finish=True, force=True)
    done = ms.handoff(root, "Langmuir")
    check("a finished run reports itself closed",
          (done["open"], "is finished" in done["resume_line"]),
          (False, True), done["resume_line"])
    check("...and `next` still names the first module that never ran",
          done["next"], ms.run_ledger(root, "Langmuir")["next"],
          "--force closes a run over modules that never reached a terminal "
          "state; what they are is a fact the payload must not round off")

    # --- 2. it always names what it cannot see ---------------------------
    for label, res in (("no run", no_run), ("open", open_run),
                       ("mid-module", mid), ("finished", done)):
        check("`not_recorded` is non-empty on a %s project" % label,
              len(res["not_recorded"]), 3, res["not_recorded"])
    check("...and it names the transcript first",
          "transcript" in _flatten(no_run["not_recorded"]).lower()
          or "this session" in _flatten(no_run["not_recorded"]).lower(),
          True, no_run["not_recorded"])
    check("it is a standing warning, not a finding",
          (no_run["errors"], no_run["warnings"]), ([], []),
          "a clean project has the same three classes as a dirty one; they "
          "are the kinds of thing about to be lost, not a count of them")

    # --- 3. and it never blesses -----------------------------------------
    for label, res in (("no run", no_run), ("open", open_run),
                       ("finished", done)):
        blob = _flatten(res).lower()
        hit = [b for b in BLESSINGS if b in blob]
        check("handoff on a %s project never says it is safe" % label, hit,
              [], "it cannot see the conversation, so a verdict about the "
                  "conversation would be a confident wrong answer at exit 0")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ms.print_handoff(no_run)
    printed = buf.getvalue()
    check("...and neither does what it prints",
          [b for b in BLESSINGS if b in printed.lower()], [], printed[:300])
    check("what it prints DOES name what is about to be lost",
          "NOT on disk" in printed, True, printed[:300])

    # --- the stage is a closed list --------------------------------------
    bad = ms.handoff(root, "Langmuir", "drafting")
    check("a stage that is not a stage is refused", bool(bad["errors"]), True)
    check("...and the refusal names the ones that are",
          all(s in bad["errors"][0] for s in sorted(ms.HANDOFF_STAGES)), True,
          bad["errors"])
    for stage in sorted(ms.HANDOFF_STAGES):
        res = ms.handoff(root, "Langmuir", stage)
        check("--stage %s resolves" % stage, res["errors"], [], res)
        check("...and names SKILL.md first", res["read"][0], "SKILL.md",
              "the spine is read on every invocation; the stage file is the "
              "part that is not")

    # --- an engine that died is not a count of zero ----------------------
    real = ms._prose
    try:
        setattr(ms, "_prose", lambda *a, **k: {"error": "prose.py died"})
        broke = ms.handoff(root, "Langmuir")
    finally:
        setattr(ms, "_prose", real)
    check("a dead prose.py makes the flag count UNKNOWN, not zero",
          broke["flags_outstanding"], -1)
    check("...and says so", any("DID NOT RUN" in w
                                for w in broke["warnings"]), True,
          broke["warnings"])


def test_the_skill_is_enterable_in_parts(tmp: str) -> None:
    section("The skill splits into a spine plus one file per stage "
            "(compaction 3)")

    skill_dir = os.path.join(ROOT, "skills", "writing-engine")
    refdir = os.path.join(skill_dir, "reference")
    on_disk = sorted("reference/" + f for f in os.listdir(refdir)
                     if f.endswith(".md")) if os.path.isdir(refdir) else []
    check("the reference files exist", bool(on_disk), True, refdir)

    # --- 6. a rule nothing routes to is a deleted rule -------------------
    named = sorted({r for spec in ms.HANDOFF_STAGES.values()
                    for r in spec["read"]})
    check("every reference file is named by at least one stage",
          sorted(set(on_disk) - set(named)), [],
          "a section moved out of SKILL.md and named by nothing is a rule "
          "that has been deleted with extra steps")
    check("...and every stage names a file that is there",
          sorted(set(named) - set(on_disk)), [],
          "a read list naming a file that is not there sends a fresh context "
          "to read nothing and tells it that it has read everything")
    check("SKILL.md is named by every stage as well",
          sorted({ms.handoff.__globals__["SKILL_SPINE"]}), ["SKILL.md"])

    # --- 5. and every heading it names is really there -------------------
    # The CAPTION_HEADING_RE pattern: a contract duplicated on purpose, safe
    # only while something compares the copies. Without this the read list
    # drifts into naming headings that were renamed a month ago.
    for stage, spec in sorted(ms.HANDOFF_STAGES.items()):
        heads = set()
        for rel in spec["read"]:
            body = read(os.path.join(skill_dir, *rel.split("/")))
            heads |= {m.group(1).strip() for m in
                      re.finditer(r"^#{1,4} (.+)$", body, re.M)}
        for want in spec["headings"]:
            check("%s: the skill really has a heading %r" % (stage, want[:46]),
                  want in heads, True,
                  "a heading named here and renamed there sends a fresh "
                  "context to read nothing")
        check("%s names at least four headings" % stage,
              len(spec["headings"]) >= 4, True, spec["headings"])

    # --- the spine is what a stage-scoped re-entry actually pays ---------
    spine = len(read(os.path.join(skill_dir, "SKILL.md")).encode("utf-8"))
    whole = spine + sum(
        len(read(os.path.join(skill_dir, *r.split("/"))).encode("utf-8"))
        for r in on_disk)
    worst = max(
        spine + sum(len(read(os.path.join(skill_dir, *r.split("/")))
                        .encode("utf-8")) for r in spec["read"])
        for spec in ms.HANDOFF_STAGES.values())
    check("entering for one stage costs less than reading the whole skill",
          worst < whole, True, (worst, whole))
    check("...and the spine alone is under half of it", spine * 2 < whole,
          True, (spine, whole))

    # The spine keeps the parts every invocation needs regardless of stage.
    #
    # `Rounds` and `Picking up an interrupted run` LEFT this list on
    # 2026-09-24 and went to `reference/rounds.md`. Neither is needed every
    # invocation: one fires when a round opens or closes, the other only when
    # `status` reports `run.open`, and the spine reads that field on every
    # invocation anyway. What every invocation needs is the TRIGGER, not the
    # procedure - so the pointer is asserted below instead, which is the real
    # invariant and the one that was never checked.
    #
    # `Clearing the context mid-run` did NOT leave, and the distinction is the
    # point: it fires during any run rather than at a named boundary, and a
    # context that has just been cleared is the one case in this pipeline that
    # cannot be told to go and read a stage file first, because being told is
    # what it just lost. Moving it out was tried in the same session and this
    # check is what refused it.
    body = read(os.path.join(skill_dir, "SKILL.md"))
    for want in ("Start every invocation here", "Then state the plan and wait",
                 "The modules", "How a module is spawned",
                 "Clearing the context mid-run",
                 "Things that are wrong to do here",
                 "What to read, and when"):
        check("the spine keeps %r" % want,
              bool(re.search(r"^#{2,3} %s" % re.escape(want), body, re.M)),
              True)

    # A section that moved out is reachable or it is deleted. The stage table
    # at the top is one route and the condition that fires it is the other,
    # and BOTH have to be in the spine: a table nobody consults when
    # `run.open` is true is a table that did not route anything.
    for want, why in (
            ("reference/rounds.md", "the file the round sections moved to"),
            ("reference/review.md", "the file review mode moved to")):
        check("the spine routes to %r - %s" % (want, why),
              want in body, True,
              "a section moved out of the spine and named by nothing is a "
              "rule that has been deleted with extra steps")
    check("...and the interrupted-run trigger still names it where it fires",
          bool(re.search(r"run\.open.*\n(?:.*\n){0,6}?.*reference/rounds\.md",
                         body)), True,
          "the spine reads run.open on every invocation; that is the moment "
          "it has to say where the procedure went")
    check("...and the <tools> resolution, which every file below it uses",
          bool(re.search(r"Resolve\s+`<tools>`", body)), True)


def test_the_compaction_dial(tmp: str) -> None:
    section("compaction: auto, and the clears actually happen (4.5)")

    sub = os.path.join(tmp, "compaction_dial")
    os.makedirs(sub, exist_ok=True)
    root = build_project(sub)
    ms.init(root, "Langmuir")
    cfg_path = os.path.join(ms.journal_dir(root, "Langmuir"),
                            "writing_config.yml")

    check("the dial is in the written config", "compaction:" in read(cfg_path),
          True, "a dial that exists in the table and not in the file is one "
                "no user ever sees")
    check("...and it defaults to auto",
          ms.compaction_mode(ms.read_config(cfg_path)), "auto",
          "a deliberate reversal of offer-never-run: a compaction writes "
          "nothing, so the cost of a wrong auto is tokens and the cost of a "
          "wrong ask is the feature being declined forever")
    check("an absent config still reads auto", ms.compaction_mode(None),
          "auto")
    check("a value that is not a value reads auto",
          ms.compaction_mode({"dials": {"compaction": "sometimes"}}), "auto")
    res = ms.configure(root, "Langmuir", ["compaction=off"])
    check("the dial is settable", res["errors"], [], res)
    check("...and reads back", ms.compaction_mode(ms.read_config(cfg_path)),
          "off")

    # --- 7. the plan marks the seam, and marks nothing without one -------
    ms.configure(root, "Langmuir", ["compaction=auto"])
    pl = ms.plan(root, "Langmuir", "submission")
    check("a submission round marks the prose/submission seam",
          "--- context cleared here (prose/submission)" in pl["line"], True,
          pl["line"][:400])
    check("...and the mark names the stage the next half enters",
          "handoff --stage submission" in pl["line"], True)
    check("...and the payload carries the boundaries as data",
          [b["label"] for b in pl["boundaries"]],
          ["prose/submission", "after assemble", "before submission-package"],
          pl["boundaries"])
    check("...and the dial that governs them", pl["compaction"], "auto")
    # Against the PLAN's order, not the preset's: `plan` sorts by each
    # module's own order field, which puts toc-graphic (10) after the checks
    # the preset lists it before. The boundary follows what will actually run.
    seam = [b for b in pl["boundaries"] if b["tier"] == "B"]
    mods = pl["modules"]
    check("the seam falls between the last prose module and the first "
          "submission one",
          [(b["after"] in ms.PROSE_MODULES,
            b["before"] in ms.SUBMISSION_MODULES) for b in seam],
          [(True, True)], pl["boundaries"])
    check("...at exactly the index where the halves meet",
          [mods.index(b["before"]) - mods.index(b["after"]) for b in seam],
          [1], mods)
    check("...and every module before it is prose",
          [m for m in mods[:mods.index(seam[0]["before"])]
           if m in ms.SUBMISSION_MODULES], [], mods)

    sec = ms.plan(root, "Langmuir", "sections")
    check("a `sections` round marks nothing",
          "context cleared here" in sec["line"], False, sec["line"][:300])
    check("...because it has no submission half to cross into",
          sec["boundaries"], [],
          "its only module past the prose stage is citation-check, which is "
          "in every preset that writes prose because it is mechanical - "
          "arriving at it is not entering a stage")

    ms.configure(root, "Langmuir", ["compaction=ask"])
    asked = ms.plan(root, "Langmuir", "submission")
    check("at `ask` the mark says CAN rather than IS",
          ("context can be cleared here" in asked["line"],
           "context cleared here" in asked["line"]), (True, False),
          asked["line"][:300])

    ms.configure(root, "Langmuir", ["compaction=off"])
    off = ms.plan(root, "Langmuir", "submission")
    check("at `off` the chain renders exactly as it did before this existed",
          "cleared here" in off["line"] or "can be cleared" in off["line"],
          False, off["line"][:300])
    check("...and the modules are all still in it, in order",
          " -> ".join(off["modules"]) in off["line"].replace("\n", ""), True,
          off["line"][:300])
    check("...while the payload still reports the dial", off["compaction"],
          "off")

    # --- the boundary table is anchored to what comes NEXT ---------------
    # Anchored to what precedes it, `before respond-to-reviewers` landed on
    # reviewer-check in the submission preset - which is simply what follows
    # citation-check there.
    rev = ms.compaction_boundaries(ms.PRESETS["revision"])
    check("a revision round marks the ingest boundary",
          [b["label"] for b in rev][0], "after ingest", rev)
    check("...and the respond-to-reviewers one, before the right module",
          [(b["label"], b["before"]) for b in rev
           if b["label"].endswith("respond-to-reviewers")],
          [("before respond-to-reviewers", "respond-to-reviewers")], rev)
    draft = ms.compaction_boundaries(ms.PRESETS["draft"])
    check("a round with no ingest does not mark one",
          [b for b in draft if b["label"] == "after ingest"], [],
          "a mark on a payload that was never paid for is a promise about "
          "work that is not happening")

    # --- and every module lands in a stage that exists -------------------
    for module in ms.MODULES:
        stage = ms.module_stage(module)
        check("%s belongs to a real stage" % module,
              stage in ms.HANDOFF_STAGES, True, stage)
    check("the prose/submission seam has one definition",
          [m for m in ms.MODULES
           if (ms.module_stage(m) == "submission") != (m in ms.SUBMISSION_MODULES)],
          [], "module_stage derives from SUBMISSION_MODULES rather than "
              "listing the seam again")


def test_the_joint_findings_are_described_as_joints(tmp: str) -> None:
    """The brief's own words against what `reading.json` ships (flow 2.1).

    Three of the six readability findings name no sentence, and the brief's
    third EXAMPLE of a finding that names a sentence was one of them - the
    sentence was inside the description of the rule that contradicted it.
    """
    section("A finding names a PLACE, and the brief says which (flow 2.3)")

    sub = os.path.join(tmp, "joint_desc")
    os.makedirs(sub, exist_ok=True)
    root = build_project(sub)
    ms.configure(root, "Langmuir")
    br = ms.agent_brief(root, "revise-prose", "Langmuir")
    why = " ".join(g["why"] for g in br["given"]
                   if g["name"] == "reading_findings")
    check("the brief still describes reading.json at all", bool(why), True,
          "a test that asserts something is ABSENT must first prove "
          "something was PRESENT")
    check("it no longer claims every finding names a sentence",
          "each naming a sentence and a rule" in why, False)
    check("...it says a PLACE", "naming a PLACE" in why, True)
    check("...and names the joint as one of the two",
          "joint between two paragraphs" in why, True)
    check("...and says the joint findings carry a remedy",
          "remedy" in why, True,
          "the repair there is a sentence that does not exist yet")


def test_no_argument_is_not_review_only(tmp: str) -> None:
    """flow-and-conclusion 3.3, change 1 and change 2."""
    section("no_argument, and the conclusion as a bridge (flow 3.3)")

    task = ms.AGENT_MODULES["comprehension-check"]["task"]
    check("`no_argument` is in the closed set", "no_argument" in task, True)
    check("...and the set is still closed",
          "fits none of them is not a comprehension finding" in task, True,
          "this removes a paper_kind condition on one member; it does not "
          "widen the fence")
    check("...with no paper_kind condition left on it",
          "on a review also" in task or "review only" in task, False)
    check("part C names the conclusion",
          "THE CONCLUSION IS A BRIDGE TOO" in task, True)
    check("...and asks the one question that separates a synthesis from a "
          "recap",
          "walked you back through them" in task, True)

    # It reaches both kinds of paper, which is the whole change.
    for kind in ("research", "review"):
        sub = os.path.join(tmp, "noarg_%s" % kind)
        os.makedirs(sub, exist_ok=True)
        root = build_project(sub)
        if kind == "review":
            write(root, "project.yml", "paper_kind: review\n")
        ms.configure(root, "Langmuir")
        br = ms.agent_brief(root, "comprehension-check", "Langmuir")
        check("a %s paper's reader may report no_argument" % kind,
              "no_argument" in br["prompt"], True)
        check("...and is asked about the conclusion" ,
              "CONCLUSION IS A BRIDGE" in br["prompt"], True)


def test_strays(tmp: str) -> None:
    """Which .docx in this folder is the paper (user-asks 2).

    The measured case: a project with `manuscript_r1.docx` where the engine
    put it, `paper.docx` at the root from a session doing something else, and
    an example document the user dropped in. `scaffold.py survey` named all
    three and nothing anywhere said which was the manuscript.
    """
    section("Which document is the paper, and what the others are (user-asks 2)")

    root = os.path.join(tmp, "strays")
    run = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "scaffold.py"),
         "scaffold", root, "--title", "Strays", "--field", "biochemistry"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("the scaffold runs", run.returncode, 0, run.stderr[-400:])
    check("...and it now makes a sandbox",
          os.path.isdir(os.path.join(root, "sandbox")), True)

    jdir = os.path.join(root, "drafts", "LANGMUIR")
    os.makedirs(os.path.join(jdir, "submission"), exist_ok=True)
    os.makedirs(os.path.join(jdir, "journal_requirements"), exist_ok=True)
    write(root, "drafts/LANGMUIR/writing_config.yml", "journal: LANGMUIR\n")
    for rel in ("drafts/LANGMUIR/submission/manuscript_r1.docx",
                "drafts/LANGMUIR/journal_requirements/reference.docx",
                "paper.docx",
                "Some Example Paper.docx",
                "sandbox/class assignment.docx",
                "obsolete/drafts/old_manuscript.docx"):
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with io.open(p, "wb") as fh:
            fh.write(b"PK\x03\x04")

    res = ms.strays(root)
    check("the authoritative manuscript is named, and it is the engine's",
          os.path.basename(res["manuscript"]["path"] or ""),
          "manuscript_r1.docx")
    check("...and it says which round and journal that is",
          (res["manuscript"]["journal"], res["manuscript"]["round"]),
          ("LANGMUIR", 1))

    by_name = {os.path.basename(f["path"]): f for f in res["findings"]}
    check("a document at the project root is unexplained",
          by_name["paper.docx"]["kind"], "unexplained")
    check("...and so is the example beside it",
          by_name["Some Example Paper.docx"]["kind"], "unexplained")
    check("the journal's own reference doc is engine-written",
          by_name["reference.docx"]["kind"], "engine-written")
    check("sandbox/ is not walked at all",
          "class assignment.docx" in by_name, False)
    check("obsolete/ is a record, never an input, and is not walked either",
          "old_manuscript.docx" in by_name, False)
    check("the manuscript itself is not reported as a stray",
          "manuscript_r1.docx" in by_name, False)
    check("every unexplained finding names where it could go",
          all("sandbox" in f["proposal"] for f in res["findings"]
              if f["kind"] == "unexplained"), True)
    check("the count is the unexplained ones, not every document",
          res["counts"]["unexplained"], 2)

    # The exit code is the convention every other check here follows.
    run = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "manuscript.py"),
         "strays", root], capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    check("an unexplained document exits 1", run.returncode, 1,
          run.stdout[-300:])
    check("...and the printed line names the real manuscript",
          "manuscript_r1.docx" in run.stdout, True)

    for name in ("paper.docx", "Some Example Paper.docx"):
        os.remove(os.path.join(root, name))
    run = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "manuscript.py"),
         "strays", root], capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    check("a clean project exits 0", run.returncode, 0, run.stdout[-300:])


# ---------------------------------------------------------------------------
# The 2026-09-24 IJROBP round: items 9 (a third time), 126, 127, 128, 129
# ---------------------------------------------------------------------------

def test_caption_section_is_not_a_caption() -> None:
    section("A caption carries a number; a caption SECTION does not (item 9)")

    # Item 9 arriving by a third route. The summary/document comparison drops
    # every heading beginning `Figure` or `Table` so that float captions are
    # not compared as sections - and that swallowed the `# Figure Captions`
    # heading a journal's own section_order names along with them. The
    # summary then named a section the document WAS carrying, the comparison
    # could not see it, and the detector fired on every build of every paper
    # whose captions go in the manuscript.
    for text in ("Figure Captions", "Table Captions", "Figures",
                 "Tables and Figures", "Introduction"):
        check("a section heading is not a caption: %r" % text,
              bool(ms.FLOAT_CAPTION_HEAD_RE.match(text)), False)
    for text in ("Figure 1. The apparatus", "Table 2", "Fig. 3a",
                 "Figure S1. Supplementary", "Table E1", "Scheme 1"):
        check("a caption is: %r" % text,
              bool(ms.FLOAT_CAPTION_HEAD_RE.match(text)), True)


def test_every_source_in_the_bracket_is_a_pair(tmp: str) -> None:
    section("A citation naming two sources is two pairs (item 126)")

    root = _scaffold(tmp, "pairs_case")
    if not root:
        skip("every attribution pair check", "scaffold failed")
        return
    ms.init(root, "Langmuir")
    write(root, "drafts/source_text/introduction.md",
          "# Introduction\n\n"
          "One source here [@Alpha2020]. Two in one bracket "
          "[@Beta2021; @Gamma2022]. One in text @Delta2023 [p. 4]. "
          "A suppressed author [-@Epsilon2024]. "
          "Not a citation: the SiO~2~@Cu~2~GaBO~5~ core-shell.\n")
    res = ms.attribution_inputs(root, "Langmuir")
    # The measured failure: `[@Smith2025; @Jones2025]` is one bracket
    # and two sources; the pattern anchored on `[@`, so the brief handed the
    # agent a denominator one short of the table it was filling. The agent
    # counted 15 where the brief said 14 and reported the engine to itself.
    check("every source in a group is counted", res["pairs"], 5)
    check("...and a core-shell formula is not one", res["distinct_keys"], 5)


def test_densities_do_not_reach_the_rewrite(tmp: str) -> None:
    section("1e's densities go to the user, not to 3.64 (item 127)")

    # 3.64's whole guarantee is that a pass which cannot SEE a density cannot
    # optimize it. 1e is told to report the four densities; its only write
    # target was the one report 3.64 is given; so the two instructions
    # pointed at the same file and the guarantee was decorative.
    writes = ms.AGENT_MODULES["quality-check"]["writes"]
    paths = [w[0] if isinstance(w, tuple) else w for w in writes]
    check("1e has a file of its own for them",
          any("quality_densities.md" in p for p in paths), True, str(paths))
    task = " ".join(ms.AGENT_MODULES["quality-check"]["task"].split())
    check("...and its task says they may not go in the report",
          "NEVER IN `quality_report.md`" in task, True)
    denied = [p for p, _w
              in ms.AGENT_MODULES["revise-prose --from-quality"]["denied"]]
    check("3.64 is denied the densities file",
          "reports/rN/quality_densities.md" in denied, True, str(denied))
    check("...and is still denied ai_voice.json",
          "reports/rN/ai_voice.json" in denied, True)

    # And the guard, because a denial resting on another component's memory
    # is a request. `assemble` reads the report back afterwards.
    leaky = os.path.join(tmp, "leaky_quality_report.md")
    with open(leaky, "w", encoding="utf-8") as fh:
        fh.write("# Quality check\n\n"
                 "| section | words | em dashes /1000 | boldface /1000 | "
                 "filler openers | three-item lists |\n"
                 "|---|---|---|---|---|---|\n"
                 "| introduction | 663 | 0.0 | 0.0 | 0 | 3 |\n")
    check("a density table in the report is found",
          len(ms.quality_report_densities(leaky)), 1)
    clean = os.path.join(tmp, "clean_quality_report.md")
    with open(clean, "w", encoding="utf-8") as fh:
        fh.write("# Quality check\n\n"
                 "The densities are in quality_densities.md, not here. One "
                 "of them sent me to a sentence and the finding names it.\n\n"
                 "| # | location | verdict |\n|---|---|---|\n"
                 "| A1 | intro | leave |\n")
    check("...and prose ABOUT a density is not a density table",
          ms.quality_report_densities(clean), [])
    check("a report that is not there is not a leak",
          ms.quality_report_densities(os.path.join(tmp, "nope.md")), [])
    check("the code is registered, so a run can close it as well as file it",
          "quality_report_leaks_densities" in ms.ENGINE_ISSUES, True)


def test_a_count_of_an_input_is_computed(tmp: str) -> None:
    section("1e is told how many findings fired, not how many rules exist")

    root = _scaffold(tmp, "count_case")
    if not root:
        skip("every 1e input check", "scaffold failed")
        return
    ms.init(root, "Langmuir")
    jdir = ms.journal_dir(root, "Langmuir")
    rnd = ms.build_round(jdir)
    rdir = os.path.join(jdir, "reports", "r%d" % rnd)
    os.makedirs(rdir, exist_ok=True)

    # The brief said `THE 15 MECHANICAL AI-VOICE FINDINGS`. 15 is the number
    # of RULES. A real run opened a file holding 7 and spent a numbered
    # paragraph of its own report telling the engine that eight findings had
    # been dropped - none had.
    task = " ".join(ms.AGENT_MODULES["quality-check"]["task"].split())
    check("the template no longer states a count of findings",
          "THE 15 MECHANICAL AI-VOICE FINDINGS" in task, False)

    missing = ms.quality_check_inputs(root, "Langmuir", rnd)
    check("an absent audit is said, not reported as a clean one",
          bool(missing["part_a_missing"]), True)
    check("...and it is NOT a module-wide did-not-run",
          "did_not_run" in missing, False, str(sorted(missing)))

    with open(os.path.join(rdir, "ai_voice.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"findings": [], "densities": {},
                   "counts": {"findings": 7, "asserted": 5,
                              "contextual": 2, "sections": 5}}, fh)
    got = ms.quality_check_inputs(root, "Langmuir", rnd)
    check("the count handed over is the file's own", got["findings"], 7)
    check("...and the header says so", "7 mechanical" in got["header"], True,
          got["header"])
    brief = ms.agent_brief(root, "quality-check", "Langmuir")
    check("...and it reaches the prompt",
          "7 mechanical AI-voice findings" in brief["prompt"], True)


def test_imported_prose_can_be_polished(tmp: str) -> None:
    section("A polish round reaches prose the engine did not write (item 128)")

    root = _scaffold(tmp, "polish_case")
    if not root:
        skip("every polish check", "scaffold failed")
        return
    ms.init(root, "Langmuir")
    for stem in ("introduction", "methods", "results", "discussion"):
        write(root, "drafts/source_text/%s.md" % stem,
              "# %s\n\nA paragraph the author brought with them, imported "
              "from a manuscript this engine never wrote. It says what it "
              "says, and the point of the round is to read it better.\n"
              % stem.title())
    # One section left empty on purpose: the documented empty-the-folder
    # gesture (21.6) has to keep meaning what it means whatever this round's
    # answer was.
    write(root, "drafts/source_text/title_abstract.md", "# Title\n")

    plain = ms.draft_modes(root)
    check("imported prose is `edit` by default",
          sorted({m["mode"] for m in plain.values()}), ["compose", "edit"])
    polished = ms.draft_modes(root, polish=["all"])
    check("...and `polish` once the round has said so",
          sorted({m["mode"] for m in polished.values()}),
          ["compose", "polish"])
    check("an EMPTY section is compose whatever the answer",
          polished["title_abstract"]["mode"], "compose")
    one = ms.draft_modes(root, polish=["methods"])
    check("one named section only", one["methods"]["mode"], "polish")
    check("...and its neighbour is untouched", one["results"]["mode"], "edit")

    # It is an ANSWER on the round menu, not a flag somebody passes: the
    # sentence the terminal printed has to be the sentence the agent is
    # bound by (item 89).
    check("`polish` is an answer the menu offers",
          "polish" in ms.ROUND_INTENTS, True)
    check("...and it forbids a redraft",
          "redraft" in ms.ROUND_INTENTS["polish"]["must_not"], True)
    check("`rearrange` no longer claims to be the only one",
          "ONLY ANSWER" in ms.ROUND_INTENTS["rearrange"]["what"], False)
    opts = {r["intent"]: r for r in ms.round_intent_options(root, "Langmuir")}
    check("the menu lists it with a live count", opts["polish"]["count"], 4)

    brief = ms.agent_brief(root, "revise-prose", "Langmuir")
    check("first-pass revise-prose skips written prose by default",
          sorted(brief["skipped_sections"]),
          ["discussion", "introduction", "methods", "results"])
    jdir = ms.journal_dir(root, "Langmuir")
    prev = ms.round_state(jdir).get("intent") or {}
    ms.update_round_state(jdir, intent=dict(prev, intents=["polish"]))
    brief = ms.agent_brief(root, "revise-prose", "Langmuir")
    check("...and does not, on a polish round", brief["skipped_sections"], [])
    check("...and the prompt says it is not a redraft",
          "NOT A REDRAFT" in brief["prompt"], True)

    # The other half: the provenance of prose nothing here composed. Without
    # it the gesture people reach for is writing `written_by: draft-sections`
    # into the ledger by hand, which is a lie the next reader cannot detect.
    before = {r["section"]: r for r in ms.load_frozen(root)["sections"]}
    check("imported prose has no hash of record, so it reads fresh",
          before["methods"]["state"], "fresh")
    rec = ms.import_prose(root, "Langmuir")
    check("`import-prose` records the ones with prose in them",
          sorted(rec["recorded"]),
          ["discussion", "introduction", "methods", "results"])
    check("...and says so about the empty one", rec["skipped"],
          ["title_abstract"])
    after = {r["section"]: r for r in ms.load_frozen(root)["sections"]}
    check("...they are engine-owned now, so a polish pass may reach them",
          after["methods"]["state"], "engine-owned")
    check("...and the ledger says no module composed them",
          after["methods"]["written_by"], "import")
    write(root, "drafts/source_text/methods.md",
          "# Methods\n\nThe author edited this by hand afterwards.\n")
    check("...and a hand edit freezes it again, as engine prose does",
          {r["section"]: r["state"]
           for r in ms.load_frozen(root)["sections"]}["methods"], "frozen")
    bad = ms.import_prose(root, "Langmuir", ["not_a_section"])
    check("a section this project does not have is refused",
          bool(bad["errors"]), True, str(bad["errors"]))


def test_anonymized_build(tmp: str) -> None:
    section("A double-blind article type gets a blinded build (item 129)")

    check("the anonymization block is read at all",
          ms.anonymized_review({"anonymization.required": "true"}), True)
    check("...and false is not unknown is not true",
          [ms.anonymized_review({"anonymization.required": v})
           for v in ("false", "unknown", "", "yes")],
          [False, False, False, True])
    check("the filler is the journal's own first choice",
          ms._blind_filler({"anonymization.filler":
                            "XXXX, Anonymized for Review, or ****"}), "XXXX")
    check("...and a journal that did not say gets a default",
          ms._blind_filler({}), "XXXX")

    # The PAPER NOT COMPLETE block is GENERATED, which makes it the one part
    # of a blinded build nobody proof-reads. It named an author out loud.
    blk = ("Kendra Westwood is corresponding and has no email. "
           "Westwood also wrote the methods. Westwoodly is a word.")
    out = ms.blind_text(blk, ["Kendra Westwood", "Westwood"], "XXXX")
    check("a byline name is redacted out of generated text",
          "Westwood is corresponding" in out, False, out)
    check("...in both spellings of it", out.count("XXXX"), 2, out)
    check("...and a longer word that merely contains it is left alone",
          "Westwoodly" in out, True, out)

    root = _scaffold(tmp, "blind_case")
    if not root:
        skip("every blinded-build check", "scaffold failed")
        return
    write(root, "plan/author_information/authors.md",
          "| name | email | affiliation |\n|---|---|---|\n"
          "| A Person | a@b.c | Somewhere |\n")
    plain, _ta, _gaps = ms.front_matter(root)
    blind, _ta2, _gaps2 = ms.front_matter(root, blind=True)
    check("the byline is metadata, so it reaches the body AND core.xml",
          "author:" in plain, True)
    check("...and a blinded build emits neither", "author:" in blind, False)
    check("...while keeping the title", "title:" in blind, True)
    check("...and does not flag the byline it was told to leave out",
          "FLAG: author" in blind, False)

    # The journal's own title-page list, with a comma INSIDE one of its
    # items. The bracketed string a block list is read back as is LOSSY, and
    # a reader splitting it on commas cannot know: a nine-element list came
    # out with sixteen headings on the page.
    key = "structure.title_page_statements"
    items = ["title and short running title (46 characters or fewer, "
             "including spaces)", "conflict of interest statement"]
    req = {key: "[" + ", ".join(items) + "]", ms.LIST_ITEMS_PREFIX + key: items}
    check("a block-list item keeps its own commas",
          len(ms._req_list(req, key)), 2)
    check("...and the bracketed string alone cannot - two items read as three",
          len(ms._req_list({key: req[key]}, key)), 3)

    info = {"authors": [{"name": "A Person", "email": "a@b.c",
                         "affiliation": ""}],
            "affiliations": {}, "corresponding": "a@b.c"}
    rows = ms.title_page_checklist(
        root, "Langmuir", req,
        {"title": "A title", "running_title": "A short one"}, info)
    by = {r["want"]: r for r in rows}
    check("every element the journal names is a row", len(rows), 2)
    check("...one the byline already carries is ticked off, not flagged",
          by[items[0]]["state"], "carried")
    check("...and one nobody wrote is missing rather than silent",
          by[items[1]]["state"], "missing")

    # `author` in `author(s) responsible for statistical analyses` is not
    # the byline, and matching it there ticked off an element nobody wrote.
    stats_key = ms.LIST_ITEMS_PREFIX + key
    stats = ms.title_page_checklist(
        root, "Langmuir",
        {stats_key: ["author(s) responsible for statistical analyses - "
                     "name, e-mail"]},
        {"title": "A title"}, info)
    check("the statistics author is not answered by the author list",
          stats[0]["state"], "missing")
    check("a journal that did not source the list gets no invented one",
          ms.title_page_checklist(root, "Langmuir", {}, {"title": "A"}, info),
          [])

    # A shared surname is a CANDIDATE, never a verdict: masking a stranger's
    # paper destroys a citation as surely as leaving one's own unmasked
    # breaks the blinding, so this names what to look at and stops.
    write(root, "drafts/references.bib",
          "@article{Mine2024,\n  author = {Person, A. and Other, B.},\n"
          "  title = {A paper},\n  year = {2024},\n}\n\n"
          "@article{Theirs2019,\n  author = {Stranger, C.},\n"
          "  title = {Another},\n  year = {2019},\n}\n")
    mine = {m["key"] for m in ms.self_citations(root)}
    check("a reference sharing a surname with the byline is named",
          "Mine2024" in mine, True, str(mine))
    check("...and one that does not is left out", "Theirs2019" in mine, False)


def test_a_quoted_hash_is_not_a_comment(tmp: str) -> None:
    section("A `#` inside quotes is a character, not a comment (item 122)")

    # A journal's footnote-marker sequence is `* † ‡ § ¶ #`, and an inline
    # list holding `"#"` was cut at that element - after which the reader saw
    # a list that never closes and refused the whole file. The requirements
    # template this toolkit ships carries a comment telling the author never
    # to write a `#` in a value, which is a workaround documented in the data
    # because the reader could not do it.
    for line, want in (
            ('  markers: ["*", "#", "**"]   # AI',
             '  markers: ["*", "#", "**"]   '),
            ("# a whole line", ""),
            ("  # an indented whole line", "  "),
            ("key: a#b   # the real one", "key: a#b   "),
            ('key: "say \\"hi\\" #now"  # cut here',
             'key: "say \\"hi\\" #now"  '),
            ("key: plain value", "key: plain value")):
        check("comment strip: %r" % line,
              ms._strip_yaml_comment(line), want)

    # The two engines carry their own copy of the reader and are held to the
    # same values by tests/toc_graphic.py, so the change belongs in both.
    path = os.path.join(tmp, "hash_requirements.yml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('journal: "IJROBP"\n'
                 'tables:\n'
                 '  footnote_markers: ["*", "†", "#", "**"]   # AI\n'
                 '  format: editable\n'
                 'text:\n'
                 '  word_limit_total: 5000          # RJ\n')
    problems: list[str] = []
    req = ms.read_flat_yml(path, problems)
    check("the file is not refused as an unclosed list", problems, [])
    check("...the list keeps the marker", '"#"' in
          (req.get("tables.footnote_markers") or ""), True,
          req.get("tables.footnote_markers"))
    check("...and the keys after it still read",
          [req.get("tables.format"), req.get("text.word_limit_total")],
          ["editable", "5000"])


def test_a_locked_empty_folder_does_not_end_the_round(tmp: str) -> None:
    section("A tidy-up may not end the round half done (item 124)")

    root = _scaffold(tmp, "rmdir_case")
    if not root:
        skip("every locked-folder check", "scaffold failed")
        return
    ms.init(root, "Langmuir")
    jdir = ms.journal_dir(root, "Langmuir")
    sub = os.path.join(jdir, "submission")
    os.makedirs(sub, exist_ok=True)
    with open(os.path.join(sub, "manuscript_r1.docx"), "wb") as fh:
        fh.write(b"PK\x03\x04 a built round")
    os.makedirs(os.path.join(jdir, "reports", "r1"), exist_ok=True)
    write(root, os.path.relpath(
        os.path.join(jdir, "reports", "r1", "prose_report.md"), root), "hi")
    write(root, os.path.relpath(
        os.path.join(ms.source_text_dir(root), "introduction.md"), root),
        "# Introduction\n\nWords.\n")

    # On a OneDrive-synced path the rmdir of the emptied reports folder
    # raises PermissionError while the sync client still holds it, and the
    # exception went uncaught - so the round stopped AFTER every file had
    # moved into obsolete/ and BEFORE round_state.json, project.yml:revision
    # and the source_text rename. A half-retired round, with nothing saying
    # so. An empty folder left behind costs nothing; a counter that did not
    # bump costs the next build.
    real_rmdir = os.rmdir

    def denied(path: str, *a, **k):
        if path.replace(os.sep, "/").endswith("reports/r1"):
            raise PermissionError(5, "Access is denied")
        return real_rmdir(path, *a, **k)

    os.rmdir = denied
    try:
        res = ms.open_round(root, "Langmuir")
    finally:
        os.rmdir = real_rmdir

    check("the round still opens", res.get("errors"), [])
    check("...the counter bumps", res.get("round"), 2)
    check("...project.yml agrees",
          ms.read_project_yml(root).get("revision"), "2")
    check("...the source text is renamed forward",
          [n for n in sorted(os.listdir(os.path.join(root, "drafts")))
           if n.startswith("source_text")], ["source_text_r2"])
    left = [a for a in res.get("actions") or [] if a.get("action") == "left"]
    check("...and the folder it could not remove is NAMED, not swallowed",
          [a["path"] for a in left], ["reports/r1"])
    check("...the emptied folder is simply still there",
          os.path.isdir(os.path.join(jdir, "reports", "r1")), True)


def test_the_run_carries_its_own_work_order(tmp: str) -> None:
    section("A redraft the run recorded reaches the brief (item 130)")

    root = _scaffold(tmp, "workorder_case")
    if not root:
        skip("every work-order check", "scaffold failed")
        return
    ms.init(root, "Langmuir")
    st = ms.source_text_dir(root)
    for stem in ("introduction", "methods"):
        write(root, os.path.relpath(os.path.join(st, "%s.md" % stem), root),
              "# %s\n\nProse from the round before.\n" % stem.title())

    # `run --start --redraft X` passed the list to plan() and recorded it
    # nowhere, so it lived only in the argv of the command that opened the
    # run. agent-brief then read every section with prose in it as EDIT -
    # keep the prose, fix the mechanics - and a round the user approved as a
    # from-scratch rewrite handed the drafter the prose it was replacing.
    before = ms.agent_brief(root, "draft-sections", "Langmuir")
    check("without a run, written prose is edit-mode",
          sorted({m["mode"] for m in before["draft_modes"]
                  if m["section"] in ("introduction", "methods")}), ["edit"])
    res = ms.run_ledger(root, "Langmuir", start=True, preset="draft",
                        redraft=["introduction", "methods"])
    check("the run opens", res.get("errors"), [])
    check("...and records the work order it was opened with",
          sorted((res.get("run") or {}).get("redraft") or []),
          ["introduction", "methods"])
    after = ms.agent_brief(root, "draft-sections", "Langmuir")
    modes = {m["section"]: m["mode"] for m in after["draft_modes"]}
    check("...which the brief now reads, with no flag repeated to it",
          [modes["introduction"], modes["methods"]], ["compose", "compose"])
    check("...and a section the order did not name is untouched",
          modes["results"], "compose")


def test_a_style_reference_reaches_the_brief(tmp: str) -> None:
    section("A configured style reference is named to the drafter (item 130)")

    root = _scaffold(tmp, "styleref_case")
    if not root:
        skip("every style-reference check", "scaffold failed")
        return
    ms.init(root, "Langmuir")
    brief = ms.agent_brief(root, "draft-sections", "Langmuir")
    check("no reference, no block", "STYLE REFERENCE" in brief["prompt"],
          False)
    check("...and the caller can see that too", brief["style_refs"], [])

    # The skill says the fingerprint is measured and added to the brief;
    # nothing in the brief said a reference existed, so a paper written to
    # match another one was written to match nothing and the config read as
    # honoured.
    ms.configure(root, "Langmuir", style_ref=["Bimetallic surfaces 2024"])
    brief = ms.agent_brief(root, "draft-sections", "Langmuir")
    check("a configured reference is named", brief["style_refs"],
          ["Bimetallic surfaces 2024"])
    prompt = " ".join(brief["prompt"].split())
    check("...in a block of its own", "STYLE REFERENCE" in prompt, True)
    check("...saying measurements, never text",
          "PLAGIARISM" in prompt, True)
    check("...and that a fingerprint nobody supplied is reported, not guessed",
          "IF IT IS NOT THERE" in prompt, True)
    check("...the digest still wins a conflict",
          "the digest wins" in prompt, True)

    # It binds the passes that WRITE PROSE and no others: a prompt carrying a
    # rule its reader cannot act on teaches the reader to skim the rules.
    other = ms.agent_brief(root, "stats-check", "Langmuir")
    check("a checking module is not told about it",
          "STYLE REFERENCE" in other["prompt"], False)
    check("...and its record says so too", other["style_refs"], [])


# ---------------------------------------------------------------------------
# Items 135 and 136: the figure uploads and the supplement are written
# ---------------------------------------------------------------------------

def make_png(path: str, w: int = 24, h: int = 16, dpi: int = 600) -> str:
    """A real PNG with a pHYs chunk, from the standard library alone."""
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    ppm = int(round(dpi / 0.0254))
    rows = b"".join(
        b"\x00" + b"".join(bytes(((x * 9) % 256, (y * 13) % 256, 120))
                           for x in range(w)) for y in range(h))
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"pHYs", struct.pack(">IIB", ppm, ppm, 1))
           + chunk(b"IDAT", zlib.compress(rows))
           + chunk(b"IEND", b""))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(png)
    return path


def test_figure_uploads_and_supplement(tmp: str) -> None:
    section("Figure uploads are exported and the supplement is built "
            "(items 135, 136)")

    sub_tmp = os.path.join(tmp, "uploads")
    os.makedirs(sub_tmp, exist_ok=True)
    root = build_project(sub_tmp)
    jdir = os.path.join(root, "drafts", "Langmuir")

    # --- the leftover folder from before the move -------------------------
    os.makedirs(os.path.join(jdir, "figures"), exist_ok=True)
    res = ms.init(root, "Langmuir")
    check("an EMPTY figures/ beside submission/ is taken away",
          os.path.isdir(os.path.join(jdir, "figures")), False)
    check("...and the action says so",
          [a["action"] for a in res["actions"]
           if a["path"].endswith("Langmuir/figures/")], ["remove"])
    write(root, "drafts/Langmuir/figures/Figure 1.tif", "someone's file")
    res = ms.init(root, "Langmuir")
    check("one with a file in it is left where it is",
          os.path.isfile(os.path.join(jdir, "figures", "Figure 1.tif")), True)
    check("...and named, so the file is not silently orphaned",
          [a["action"] for a in res["actions"]
           if a["path"].endswith("Langmuir/figures/")], ["skip"])
    shutil.rmtree(os.path.join(jdir, "figures"))

    rel = "drafts/Langmuir/journal_requirements/requirements.yml"
    base_req = read(os.path.join(root, rel))

    def set_req(**kv: str) -> None:
        body = base_req
        for key, val in kv.items():
            body = re.sub(r"^(  %s:) .*$" % key, r"\g<1> " + val, body,
                          count=1, flags=re.M)
        write(root, rel, body)

    set_req(placement="separate", dpi_raster="300", dpi_combination="500")
    exp = ms.export_figures(root, "Langmuir")
    check("nothing rendered means nothing exported, and it says so",
          (exp["checked"], exp["exported"],
           [f["rule"] for f in exp["findings"]]),
          (True, [], ["figure_format_defaulted", "nothing_to_export"]))

    make_png(os.path.join(root, "plan", "figures", "Fig01", "figure.png"))
    make_png(os.path.join(root, "plan", "figures", "FigS01", "figure.png"))
    make_png(os.path.join(root, "plan", "figures", "Fig02", "figure.png"),
             dpi=150)
    check("the DPI is read from the PNG with the standard library",
          round(ms._png_dpi(os.path.join(root, "plan", "figures", "Fig01",
                                         "figure.png")) or 0), 600)

    fdir = os.path.join(jdir, "submission", "figures")
    have_pil = ms._pillow() is not None
    real_pillow = ms._pillow
    try:
        setattr(ms, "_pillow", lambda: None)
        exp = ms.export_figures(root, "Langmuir")
        check("without Pillow a conversion is DID NOT RUN, per figure",
              sorted(f["rule"] for f in exp["findings"]
                     if f["rule"] == "figure_export_did_not_run"),
              ["figure_export_did_not_run"] * 3)
        check("...with the pip line in it",
              all("pip install pillow" in f["detail"] for f in exp["findings"]
                  if f["rule"] == "figure_export_did_not_run"), True)
        check("...and nothing written in place of the figure",
              sorted(os.listdir(fdir)) if os.path.isdir(fdir) else [],
              ["README.md"] if os.path.isdir(fdir) else [])
    finally:
        setattr(ms, "_pillow", real_pillow)

    if have_pil:
        from PIL import Image
        exp = ms.export_figures(root, "Langmuir")
        check("every rendered figure, main and supplementary, is exported",
              sorted(os.path.basename(r["to"]) for r in exp["exported"]),
              ["Figure_1.tif", "Figure_2.tif", "Figure_S1.tif"])
        check("...into submission/figures/, where it retires with the round",
              sorted(n for n in os.listdir(fdir) if n.endswith(".tif")),
              ["Figure_1.tif", "Figure_2.tif", "Figure_S1.tif"])
        with Image.open(os.path.join(fdir, "Figure_1.tif")) as im:
            got = (im.format, im.size, tuple(round(d) for d in im.info["dpi"]))
        check("...as TIFF, at the render's own size and DPI - never resampled",
              got, ("TIFF", (24, 16), (600, 600)))
        check("an unsourced format is reported as the default it is",
              [f["severity"] for f in exp["findings"]
               if f["rule"] == "figure_format_defaulted"], ["info"])
        check("a render below the journal's minimum is an error, not upscaled",
              [f["detail"].split(" is ")[0] for f in exp["findings"]
               if f["rule"] == "figure_below_min_dpi"], ["Figure 2"])
        with Image.open(os.path.join(fdir, "Figure_2.tif")) as im:
            check("...and it is still exported at the DPI it has",
                  round(im.info["dpi"][0]), 150)

        # The journal's own format and name, when it gives them.
        set_req(placement="separate", file_format="[PNG]",
                file_naming='"Fig. 1.png"')
        exp = ms.export_figures(root, "Langmuir")
        check("a render already in the journal's format is copied, not "
              "converted",
              sorted((os.path.basename(r["to"]), r["converted"])
                     for r in exp["exported"]),
              [("Fig. 1.png", False), ("Fig. 2.png", False),
               ("Fig. S1.png", False)])
        check("...byte for byte",
              _slurp(os.path.join(fdir, "Fig. 1.png"), "rb")
              == _slurp(os.path.join(root, "plan", "figures", "Fig01",
                                     "figure.png"), "rb"), True)
        set_req(placement="separate", file_format="[EPS]")
        exp = ms.export_figures(root, "Langmuir")
        check("a format the export cannot make is said, per figure",
              sorted(f["rule"] for f in exp["findings"]
                     if f["rule"] == "figure_not_exportable"),
              ["figure_not_exportable"] * 3)
        for n in os.listdir(fdir):
            if n != "README.md":
                os.remove(os.path.join(fdir, n))
        set_req(placement="separate", dpi_raster="300")

        res = ms.submission_package(root, "Langmuir")
        check("submission-package writes the figure files with the package",
              sorted(os.path.basename(w["path"]) for w in res["written"]
                     if w["path"].endswith(".tif")),
              ["Figure_1.tif", "Figure_2.tif", "Figure_S1.tif"])
        ff = ms.figure_files(root, "Langmuir")
        check("...and figure-files finds each one the caption file names",
              [f["detail"] for f in ff["findings"]
               if f["rule"] == "figure_file_missing"], [])
        names = ms.submission_names(root, "Langmuir", name="ARRSYM",
                                    force=True)
        check("submission-names carries the figures into upload/, as named",
              sorted(w["to"] for w in names["written"]
                     if w["to"].endswith(".tif")),
              ["Figure_1.tif", "Figure_2.tif", "Figure_S1.tif"],
              "; ".join(names["errors"])[:300])
    else:
        skip("the export itself", "Pillow is not installed")

    # --- a float set managed elsewhere is still uploaded (the managed-elsewhere case) --
    un_tmp = os.path.join(tmp, "uploads_unmanaged")
    os.makedirs(un_tmp, exist_ok=True)
    root2 = build_project(un_tmp)
    ms.init(root2, "Langmuir")
    rel2 = os.path.join(root2, rel)
    write(root2, rel, read(rel2).replace("  placement: unknown",
                                         "  placement: separate", 1))
    ms.configure(root2, "Langmuir", manages=["outline,analysis"])
    write(root2, "drafts/source_text/discussion.md",
          "# Discussion\n\nOrder tracks growth temperature (Figure 2; "
          "Fig. S1).\n")
    exp = ms.export_figures(root2, "Langmuir")
    check("there is nothing to export from somebody else's float set",
          (exp["checked"], "yourself" in exp["reason"]), (False, True))
    ff = ms.figure_files(root2, "Langmuir")
    check("...and every figure the text cites is missing until it is put there",
          sorted(f["detail"].split("looks like ")[-1] for f in ff["findings"]
                 if f["rule"] == "figure_file_missing"),
          ["Figure 1", "Figure 2", "Figure S1"])
    done = ms.completeness(root2, "Langmuir")
    check("completeness carries it, under uploads, even with floats unmanaged",
          sum(1 for m in done["missing"] if m["area"] == "uploads"), 3)
    for n in ("Figure_1.tif", "Figure_2.tif", "Figure_S1.tif"):
        write(root2, "drafts/Langmuir/submission/figures/" + n, "x")
    ff = ms.figure_files(root2, "Langmuir")
    check("...and the files, once there, settle it",
          [f["rule"] for f in ff["findings"]
           if f["rule"] == "figure_file_missing"], [])

    # --- the caption file splits, and is untouched without a supplement ---
    head, blocks = ms.split_caption_blocks(
        "# Figure Captions\n\n**Figure 1. A claim.** Detail.\n\n"
        "## Figure S1 — FigS01\n**Another claim.**\nMore.\n\n"
        "**Table S2. Third.** Rows.\n")
    check("both caption forms are read, in order",
          [(b["label"], b["supplementary"]) for b in blocks],
          [("Figure 1", False), ("Figure S1", True), ("Table S2", True)])
    check("...and the heading before them is kept", head.strip(),
          "# Figure Captions")
    plain = "## Figure 1 — Fig01\n**Claim.**\nText.\n"
    check("a caption file with no supplementary block is returned as it is",
          ms.main_captions(plain), plain)

    # --- the supplement ---------------------------------------------------
    if not shutil.which("pandoc"):
        skip("the supplement is built", "pandoc not installed")
        return
    sp_tmp = os.path.join(tmp, "supplement")
    os.makedirs(sp_tmp, exist_ok=True)
    root3 = build_project(sp_tmp)
    ms.init(root3, "Langmuir")
    sdir = os.path.join(root3, "drafts", "Langmuir", "submission")

    res = ms.assemble(root3, "Langmuir")
    check("a paper with no supplement builds none, and says nothing about it",
          (res["supplementary"],
           [w for w in res["warnings"] if "supplement" in w.lower()]),
          ("", []), "; ".join(res["errors"])[:300])

    write(root3, "drafts/source_text/results.md",
          read(stfile(root3, "results.md"))
          + "\nThe arrays at 30 C are shown in Figure S1.\n")
    res = ms.assemble(root3, "Langmuir", force=True)
    check("the text citing Figure S1 with nothing to build it from is said",
          any("NO supplementary file" in w and "Figure S1" in w
              for w in res["warnings"]), True)

    make_png(os.path.join(root3, "plan", "figures", "FigS01", "figure.png"))
    write(root3, "drafts/source_text/live_captions.md",
          read(stfile(root3, "live_captions.md"))
          + "\n## Figure S1 — FigS01\n"
            "**Arrays at 30 C keep the same lattice.**\n"
            "Supplementary central sections.\n")
    write(root3, "drafts/source_text/supplementary.md",
          "# Supplementary Information\n\n## Supplementary Methods\n\n"
          "Grids were prepared as before [@jensen2019arrays].\n")
    res = ms.assemble(root3, "Langmuir", force=True)
    out = os.path.join(sdir, "supplementary_info_r1.docx")
    check("assemble builds supplementary_info_rN.docx beside the manuscript",
          (os.path.isfile(out), res["supplementary"] == out), (True, True),
          "; ".join(res["errors"] + res["warnings"])[:400])
    check("...with the supplementary figure embedded",
          res["supplementary_embedded"], ["Figure S1"])
    with zipfile.ZipFile(out) as z:
        media = [n for n in z.namelist() if n.startswith("word/media/")]
        core = z.read("docProps/core.xml").decode("utf-8")
    check("...as an image inside the .docx", len(media), 1)
    si_text = subprocess.run([pandoc_bin(), out, "-t", "plain"],
                             capture_output=True, text=True,
                             encoding="utf-8").stdout
    check("...under its caption", "Arrays at 30 C keep the same lattice"
          in si_text, True)
    check("...with its citation resolved into a reference list",
          ("@jensen" not in si_text, "References" in si_text), (True, True))
    check("...and no author in its file properties",
          "<dc:creator>" not in core or "<dc:creator></dc:creator>" in core
          or "<dc:creator/>" in core, True)
    main_text = subprocess.run([pandoc_bin(), res["output"], "-t", "plain"],
                               capture_output=True, text=True,
                               encoding="utf-8").stdout
    check("the supplementary caption leaves the main manuscript",
          ("Arrays at 30 C keep the same lattice" in main_text,
           "Thermophilic arrays are more ordered" in main_text),
          (False, True))

    res = ms.assemble(root3, "Langmuir", force=True)
    check("a rebuild replaces the supplement it wrote itself",
          res["supplementary"] == out, True)
    with open(out, "ab") as fh:
        fh.write(b"edited in Word")
    edited = _slurp(out, "rb")
    res = ms.assemble(root3, "Langmuir")
    check("a supplement edited since it was built is NOT overwritten",
          (_slurp(out, "rb") == edited,
           any("was NOT rebuilt" in w for w in res["warnings"])),
          (True, True), "; ".join(res["errors"])[:300])
    check("...and the manuscript beside it still builds",
          (res["errors"], bool(res["output"])), ([], True))
    only = ms.supplementary(root3, "Langmuir", force=True)
    check("`supplementary --force` rebuilds it alone, manuscript untouched",
          (only["built"], only["embedded"]), (True, ["Figure S1"]))

    write(root3, "plan/floats/float_provenance.json",
          json.dumps({"FigS01": {"mock": True}}))
    only = ms.supplementary(root3, "Langmuir")
    check("...and holds the mock-data gate as assemble does",
          bool(only["errors"]) and "mock" in only["errors"][0], True)


def _doc_xml(path: str) -> str:
    with zipfile.ZipFile(path) as z:
        return z.read("word/document.xml").decode("utf-8")


def _para_seq(doc: str) -> list[tuple[str, str]]:
    """(style, text) for every paragraph of a document.xml, in order."""
    out = []
    for m in re.finditer(r"<w:p(?:\s[^>]*)?>.*?</w:p>", doc, re.S):
        st = re.search(r'<w:pStyle w:val="([^"]+)"', m.group(0))
        text = "".join(re.findall(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>",
                                  m.group(0)))
        brk = '<w:br w:type="page"/>' in m.group(0)
        out.append((st.group(1) if st else ("BREAK" if brk else ""),
                    text.strip()))
    return out


def test_items_138_to_157(tmp: str) -> None:
    section("The 2026-09-25 clinical defect round (items 138-157)")

    # --- 140: the drafter's heading one level too deep --------------------
    head, rest, note = ms.normalize_section_headings(
        "## Introduction\n\nArrays are ordered.", {"introduction"}, 1)
    check("140: a level-2 copy of the section heading is the section heading",
          (head, rest, bool(note)), ("Introduction", "Arrays are ordered.",
                                     True))
    head, rest, note = ms.normalize_section_headings(
        "## Experimental Section\n\n### Materials\n\nA.\n\n### Instruments"
        "\n\nB.", {"experimental_section", "methods"}, 1)
    check("140: ...and its subsections are lifted to level 2",
          (head, [ln for ln in rest.split("\n") if ln.startswith("#")]),
          ("Experimental Section", ["## Materials", "## Instruments"]))
    head, rest, note = ms.normalize_section_headings(
        "## Materials\n\nA.\n\n## Instruments\n\nB.",
        ms._section_names("Methods", "methods"), 1)
    check("140: a methods file opening on its first SUBSECTION is untouched",
          (head, rest.startswith("## Materials"), note), ("", True, ""))

    # --- 139/151: page breaks, read from the requirements -----------------
    check("151: unknown is the engine's default, not `none`",
          ms._req_page_breaks({"text.page_break_before": "unknown"})[0],
          "default")
    check("139: a sourced list is followed exactly",
          ms._req_page_breaks({"text.page_break_before":
                               "[abstract, References]"})[:2],
          ("list", ["abstract", "References"]))
    check("139: none is none",
          ms._req_page_breaks({"text.page_break_before": "none"})[0], "none")

    # --- 152/157: indentation ----------------------------------------------
    check("152: lengths in any of the usual units",
          [ms._parse_length_twips(x) for x in ("0.5in", "1.27cm", "36pt",
                                               "12.7mm", "0.5 in")],
          [720, 720, 720, 720, 720])
    check("152/157: unknown is the 0.5 in default, and says it is",
          ms._req_indent({"text.first_line_indent": "unknown"})[:2],
          (720, False))
    check("152: none is sourced and zero",
          ms._req_indent({"text.first_line_indent": "none"})[:2], (0, True))

    # --- 146: emphasized callouts -------------------------------------------
    md, found = ms.strip_callout_emphasis(
        "Order rises (**Fig. 1D**) and holds (*Table 2*; __Figure S1__).\n"
        "**Figure 1.** Arrays keep hexagonal packing.\n", {})
    check("146: bold, italic and underscore callouts are set plain",
          (found, "(Fig. 1D)" in md, "(Table 2; Figure S1)" in md),
          (["Fig. 1D", "Table 2", "Figure S1"], True, True))
    check("146: ...and a caption's own bold lead is not a callout",
          "**Figure 1.** Arrays" in md, True)
    check("146: the journal can keep them",
          ms.strip_callout_emphasis("(**Fig. 1**)", {
              "typography.callout_emphasis": "bold"})[1], [])
    check("146: prose.py and manuscript.py hold one callout pattern",
          ms._prose_module().CALLOUT_LABEL == ms.CALLOUT_LABEL, True)

    # --- 154: a FLAG that asks for an identifier ----------------------------
    check("154: an IRB protocol number is an identifier",
          len(ms.identifier_flags(
              "approval. [FLAG: author - XXXX IRB protocol number, and "
              "whether a waiver of informed consent was granted.]")), 1)
    check("154: whether consent was waived is not",
          ms.identifier_flags("[FLAG: author - whether a waiver of "
                              "informed consent was granted.]"), [])
    check("154: names inside a FLAG are redacted, names outside it are not",
          ms.blind_flags("Wilson et al. [FLAG: ask Rowan Wills]",
                         ["Rowan Wills"], "XXXX"),
          "Wilson et al. [FLAG: ask XXXX]")

    # --- 149: a statement goes to its home ----------------------------------
    got = ms.inline_statement(
        "# Methods and Materials\n\n## Patients\n\nSeventeen patients.\n\n"
        "## Planning\n\nPlans were made.",
        "# Ethics\n\nThis study had IRB approval.\n")
    check("149: ethics goes at the end of the first Methods subsection",
          got.split("\n\n")[:4],
          ["# Methods and Materials", "## Patients", "Seventeen patients.",
           "This study had IRB approval."])
    check("149: an unsourced order is not the journal's",
          (ms.order_is_sourced({"structure.section_order":
                                ms.SKELETON_ORDER}),
           ms.order_is_sourced({"structure.section_order":
                                "[title_abstract, Introduction, Methods]"})),
          (False, True))

    # --- 141: the citation form -----------------------------------------------
    cdir = os.path.join(tmp, "csl_forms")
    os.makedirs(cdir, exist_ok=True)
    paren = write_raw_csl(cdir, "paren.csl", 'prefix="(" suffix=")"', "")
    sup = write_raw_csl(cdir, "sup.csl", "", ' vertical-align="sup"')
    ama = {"references.style_name": "AMA"}
    cf = ms.citation_form_check(ama, paren)
    check("141: a (1) CSL disagrees with AMA, and names the canonical CSL",
          (cf["csl_form"], cf["agrees"], "american-medical-association"
           in cf["detail"]), ("parenthesis", False, True))
    check("141: a superscript CSL agrees",
          ms.citation_form_check(ama, sup)["agrees"], True)
    cf = ms.citation_form_check(ama, os.path.join(cdir, "absent.csl"))
    check("141: no CSL for a known style is said, with the canonical file",
          (cf["csl_present"], "canonical CSL" in cf["detail"]),
          (False, True))
    check("141: a style name the table does not know is not judged",
          ms.citation_form_check({"references.style_name": "House"},
                                 paren)["agrees"], None)

    # --- 156/150: the supplement against the journal's rules ----------------
    got = ms.supplement_content_findings(
        "**Interpretation.** Taken together, these data suggest that "
        "smearing is unlikely.\n\nTable S2 is intended as a lookup.",
        {"supplementary.content_restrictions":
         "should not include discussion or key analysis"})
    check("156: interpretation and usage advice are named under the rule",
          sorted({g["rule"] for g in got}),
          ["interpretive_paragraph", "interpretive_sentence"])
    check("156: nothing is judged when the journal sets no rule",
          ms.supplement_content_findings("Taken together, x.", {}), [])

    # --- the build ------------------------------------------------------------
    if not shutil.which("pandoc"):
        skip("the build half of items 138-157", "pandoc not installed")
        return
    b_tmp = os.path.join(tmp, "items_138_157")
    os.makedirs(b_tmp, exist_ok=True)
    root = build_project(b_tmp)
    ms.init(root, "Langmuir")
    jdir = os.path.join(root, "drafts", "Langmuir")
    rel = "drafts/Langmuir/journal_requirements/requirements.yml"
    base = read(os.path.join(root, rel))
    base = base.replace(
        "section_order: [title_abstract, introduction, methods, results, "
        "discussion]",
        "section_order: [title_abstract, running_title, Introduction, "
        "Experimental Section, Results, Discussion, References]", 1)
    write(root, rel, base)
    write(root, "drafts/source_text/introduction.md",
          "## Introduction\n\nArrays have been resolved in mesophiles but "
          "not above 60 C [@jensen2019arrays] (**Figure 1**).\n")

    res = ms.assemble(root, "Langmuir", force=True)
    out = res.get("output") or ""
    check("the build runs", (res["errors"], bool(out)), ([], True),
          "; ".join(res["errors"])[:300])
    if not out:
        return
    doc = _doc_xml(out)
    seq = [p for p in _para_seq(doc) if p[0] or p[1]]
    styles = [s for s, _t in seq]
    check("138: the running title sits directly under the title",
          styles[:2] == ["Title", "Subtitle"]
          and "Array order and growth temperature" in seq[1][1], True,
          " > ".join(styles[:6]))
    at = styles.index("AbstractTitle") if "AbstractTitle" in styles else -1
    check("138: ...then the abstract, with nothing between it and the "
          "Introduction heading but a page break",
          [s for s in styles[at:] if s not in ("Abstract", "BREAK")][:2],
          ["AbstractTitle", "Heading1"])
    intro = [t for s, t in seq if s == "Heading1" and t == "Introduction"]
    check("140: the Introduction heading appears once, at level 1",
          (len(intro), [t for s, t in seq if s == "Heading2"
                        and t == "Introduction"]), (1, []))
    heads = [t for s, t in seq if s in ("Heading1", "AbstractTitle")]
    check("151: a page break before every top-level section but the first "
          "block", (res["page_breaks"]["mode"], doc.count(
              '<w:br w:type="page"/>')), ("default", len(heads)))
    check("151: ...and requirements.md says the breaks are the engine's",
          "Engine Defaults Applied" in read(os.path.join(
              jdir, "journal_requirements", "requirements.md")), True)
    check("146: the bold callout is plain in the .docx",
          ("Figure 1" in res["callouts_set_plain"],
           [f for f in res["format"] if "callouts are emphasized"
            in f["check"]]), (True, []))
    ref = os.path.join(jdir, "journal_requirements", "reference.docx")
    with zipfile.ZipFile(ref) as z:
        idx = ms._style_index(z.read("word/styles.xml").decode("utf-8"))
    check("152: BodyText is indented 0.5 in by the style, FirstParagraph "
          "is not", (idx["BodyText"]["first_line"],
                     idx["FirstParagraph"]["first_line"]), ("720", "0"))

    base2 = base.replace("  page_break_before: unknown",
                         "  page_break_before: [References]", 1)
    write(root, rel, base2)
    res = ms.assemble(root, "Langmuir", force=True)
    doc = _doc_xml(res["output"])
    check("139: a sourced list puts exactly one break, before References",
          (doc.count('<w:br w:type="page"/>'), res["page_breaks"]["before"]),
          (1, ["References"]))

    # 149: ethics in Methods, no heading of its own, on a sourced order
    base3 = base2.replace("    ethics: unknown", "    ethics: required", 1)
    write(root, rel, base3)
    aff = ms.fm_rel(root, "affiliations")
    # Under the template's own `## Ethics`, which is the one the reader
    # finds first - a second heading appended below it would never be read.
    write(root, aff, read(os.path.join(root, aff)).replace(
        "Delete the heading if none of it does. -->",
        "Delete the heading if none of it does. -->\n\nThe study was "
        "approved by the institutional review board.", 1))
    res = ms.assemble(root, "Langmuir", force=True)
    seq = _para_seq(_doc_xml(res["output"]))
    check("149: no Ethics heading, on a journal whose order has none",
          [t for s, t in seq if s.startswith("Heading") and t == "Ethics"],
          [])
    ex = [i for i, (_s, t) in enumerate(seq)
          if "approved by the institutional review board" in t]
    first_after = next((t for s, t in seq[ex[0]:] if s == "Heading1"), "") \
        if ex else ""
    check("149: ...the sentence is in the build, inside the methods section",
          (len(ex), first_after), (1, "Results"))

    # 144/147: an imported .docx carries its comments into the ledger
    fixture = os.path.join(FIXTURES, "tracked_two_authors.docx")
    if not os.path.isfile(fixture):
        skip("import routes the docx", "tests/fixtures/ missing")
    else:
        got = ms.import_prose(root, "Langmuir", None, fixture)
        dx = got.get("docx") or {}
        check("144: import --from-docx routes the file through ingest",
              (got["errors"], bool(dx.get("routed_as")),
               len(dx.get("comments", []))), ([], True, 2),
              "; ".join(got["errors"])[:300])
        rows = list(ms._read_status(os.path.join(
            root, "drafts", "edits", "edits_status.md")).values())
        check("144: ...every comment is a pending coauthor request",
              sorted((r["type"], r["state"], r["authority"]) for r in rows
                     if r["type"] == "comment"),
              [("comment", "pending", "coauthor")] * 2)
        done = ms.completeness(root, "Langmuir")
        named = [m for m in done["missing"] if m["area"] == "edits"
                 and "apply it, decline it with a reason" in m["detail"]]
        check("145: completeness names each unanswered request by id",
              len(named) >= 2, True)
        applied = [r for r in rows if r["type"] == "tracked"
                   and r["state"] == "applied"]
        check("147: tracked changes already in the text are recorded "
              "applied, the rest named", len(applied)
              + len(dx.get("not_in_text", [])) >= 1, True)

    # 145: a move request is carried out verbatim
    write(root, "drafts/source_text/methods.md",
          read(stfile(root, "methods.md"))
          + "\n\nEach n is stated wherever a result is reported.\n")
    mv = ms.move_passage(root, "Each n is stated wherever a result is "
                         "reported.", "results")
    check("145: move-passage moves the sentence verbatim",
          (mv["errors"], mv["from"],
           "Each n is stated" in read(stfile(root, "results.md")),
           "Each n is stated" in read(stfile(root, "methods.md"))),
          ([], "methods", True, False))
    mv = ms.move_passage(root, "not in any section at all", "results")
    check("145: ...and refuses a passage it cannot find exactly once",
          bool(mv["errors"]), True)


def write_raw_csl(d: str, name: str, layout_attrs: str, sup: str) -> str:
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0"?>\n<style><info><category '
                 'citation-format="numeric"/></info>\n<citation><layout %s%s>'
                 '<text variable="citation-number"/></layout></citation>'
                 '</style>\n' % (layout_attrs, sup))
    return path


def main() -> int:
    print("manuscript.py - driving the whole pipeline against a real project")
    tmp = tempfile.mkdtemp(prefix="ms_")
    before = live_ledger_items()
    isolate_the_defect_ledger(tmp)
    try:
        test_folder_naming()
        root = build_project(tmp)
        test_init(root)
        test_inbox_location(tmp)
        test_author_information(tmp)
        test_submission_names(tmp)
        test_reviewer_placement(tmp)
        test_blank_coi_form(tmp)
        test_config_reading(root)
        test_custom_instructions(root)
        test_plan(root)
        # Its own project: it moves `revision` and drops a file in `edits/`,
        # and every test after it in this file reads the shared one.
        test_recommend_preset(tempfile.mkdtemp(prefix="ms_rec_", dir=tmp))
        test_assemble_gates(root)
        test_escape_bare_at()
        test_rebuild_stays_in_its_round(tmp)
        test_references_heading(tmp)
        test_incomplete_paper(tmp)
        test_ris(root)
        test_rounds(root)
        test_ingest(root)
        test_response(root)
        test_status(root)
        test_r_reads_journal_target(root)
        test_outline(tmp)
        test_outline_absent(tmp)
        test_section_order(tmp)
        test_run_ledger(tmp)
        test_bib(tmp)
        test_journal_budget(tmp)
        test_intensity(tmp)
        test_journal_folder_shape(tmp)
        test_figure_uploads_and_supplement(tmp)
        test_items_138_to_157(tmp)
        test_manages_scope(tmp)
        test_paper_kind(tmp)
        test_review_outline_and_brief(tmp)
        test_init_adapts(tmp)
        test_reference_doc(tmp)
        test_format_check_catches_the_default_theme(tmp)
        test_block_list_requirements(tmp)
        test_truncated_order(tmp)
        test_alias_spelling_is_not_a_heading(tmp)
        test_specific_example_is_the_one_place(tmp)
        test_engine_keeps_its_own_list(tmp)
        test_the_ladders_middle_rung(tmp)
        test_what_the_engine_writes_it_can_read_back(tmp)
        test_detectors_for_9_11_14(tmp)
        test_caption_section_is_not_a_caption()
        test_every_source_in_the_bracket_is_a_pair(tmp)
        test_densities_do_not_reach_the_rewrite(tmp)
        test_a_count_of_an_input_is_computed(tmp)
        test_imported_prose_can_be_polished(tmp)
        test_anonymized_build(tmp)
        test_a_quoted_hash_is_not_a_comment(tmp)
        test_a_locked_empty_folder_does_not_end_the_round(tmp)
        test_the_run_carries_its_own_work_order(tmp)
        test_a_style_reference_reaches_the_brief(tmp)
        test_a_run_closes_what_it_did_not_find(tmp)
        test_engine_logs_only_engine_faults(tmp)
        test_section_names(tmp)
        test_literature_landscape(tmp)
        test_agent_brief(tmp)
        test_outline_adherence(tmp)
        test_rough_draft_denials(tmp)
        test_comprehension_denials(tmp)
        test_attribution_denials(tmp)
        test_rough_draft_source(tmp)
        test_outline_advisory_after_r1(tmp)
        test_retention_invariant(tmp)
        test_round_archive_layout(tmp)
        test_retire_journal(tmp)
        test_handoff(tmp)
        test_the_skill_is_enterable_in_parts(tmp)
        test_the_compaction_dial(tmp)
        test_brief_modes_and_abstract(tmp)
        test_round_intent(tmp)
        test_apply_round_must_not_reaches_the_brief(tmp)
        test_edit_authority(tmp)
        test_flag_answers(tmp)
        test_analysis_source(tmp)
        test_analysis_regions(tmp)
        test_front_matter_paths(tmp)
        test_source_weights(tmp)
        test_ai_disclosure(tmp)
        test_outline_regions(tmp)
        test_submission_package(tmp)
        test_round_counter_is_the_projects(tmp)
        test_revision_is_scoped_to_what_came_back(tmp)
        test_summary_matches_the_document(tmp)
        test_a_review_builds(tmp)
        test_title_block_and_end_matter(tmp)
        test_page_setup(tmp)
        test_author_matrix(tmp)
        test_author_files_back_compat(tmp)
        test_author_statements_reach_the_docx(tmp)
        test_nested_end_matter(tmp)
        test_required_if_used(tmp)
        test_round_scope_reaches_completeness(tmp)
        test_scope_is_sticky_and_has_an_inverse(tmp)
        test_one_log_one_state(tmp)
        test_the_freeze(tmp)
        test_provenance_is_recorded_when_a_module_writes(tmp)
        test_outline_waiver(tmp)
        test_research_type(tmp)
        test_source_text_carries_its_round(tmp)
        test_source_text_migrates_from_the_unlabelled_layout(tmp)
        test_front_matter_is_sourced(tmp)
        test_figure_files(tmp)
        test_a_scope_narrows_the_round_not_just_the_drafter(tmp)
        test_agent_brief_carries_the_scope(tmp)
        test_status_reports_the_cached_journal(tmp)
        test_harvest_reads_what_the_instrument_wrote(tmp)
        test_how_it_reads_reaches_the_build(tmp)
        test_comprehension_fix_runs(tmp)
        test_quality_check_adjudicates(tmp)
        test_prose_report_shape_round_trips(tmp)
        test_strays(tmp)
        test_the_joint_findings_are_described_as_joints(tmp)
        test_no_argument_is_not_review_only(tmp)
        test_a_superseded_manuscript_is_named(tmp)
        test_the_live_defect_ledger_is_untouched(before, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASS + FAIL} checks: {PASS} passed, {FAIL} failed"
          + (f", {SKIP} skipped" if SKIP else ""))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
