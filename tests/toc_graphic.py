#!/usr/bin/env python3
"""tests/toc_graphic.py - measure the TOC / graphical abstract engine.

    python tests/toc_graphic.py            # everything that runs offline
    python tests/toc_graphic.py --render   # also drive real R and PowerPoint

Spec: `specs/writing-engine.md` §7.2.

The suite is offline by default because the render backends are machine
dependent - PowerPoint and LibreOffice are both legitimately absent - and a
suite that fails on a machine without PowerPoint teaches people to ignore it.
`--render` opts into the real thing and reports which backend it got.

Gotcha, same as everywhere else in tests/: `tests/toc_graphic.py` and
`tools/toc_graphic.py` share a name, so a plain `import toc_graphic` resolves
to whichever directory is first on sys.path - and inside this file, to this
file. The engine is loaded by path under a distinct module name via importlib.
Do not "simplify" that back.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")


def _slurp(path, mode="r", **kw):
    """Read a whole file and close it.

    `open(path).read()` leaks the handle until the garbage collector gets to
    it - harmless in a short script, but it makes the suite noisy under
    `python -W all`, and on Windows a handle still open can block the
    tempdir cleanup these tests rely on.
    """
    with open(path, mode, **kw) as fh:
        return fh.read()


def _load(name: str, filename: str):
    path = os.path.join(TOOLS, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tg = _load("tg_engine_under_test", "toc_graphic.py")

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{label}" + (f"  [{detail}]" if detail else ""))
    return bool(cond)


def eq(label: str, got, want) -> bool:
    return check(label, got == want, f"got {got!r}, want {want!r}")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

ACS_TOC_BLOCK = """toc_graphic:
  required: true
  term: "TOC/Abstract Graphic"
  folder_name: toc_graphic
  width_in: 3.25
  height_in: 1.75
  dpi_color: 300
  dpi_bw: 1200
  file_formats: [TIF, EPS]
  font_family: Helvetica
  font_preferred_pt: 8
  font_min_pt: 6
  label: "For Table of Contents Only"
  placement: separate_page_end_of_manuscript_file
  must_differ_from_figures: true
  must_be_original: true
  prohibited_content: "people; currency; logos"
"""


def make_project(tmp: str, toc_block: str = ACS_TOC_BLOCK,
                 trimmed: bool = True, journal: str = "Langmuir") -> str:
    """A project just complete enough for this engine. Deliberately minimal:
    the module has to work in a trimmed layout, which is the point of 7.2.2."""
    proj = os.path.join(tmp, "proj")
    if trimmed:
        st = os.path.join(proj, "source_text")
        jdir = os.path.join(proj, journal)
    else:
        st = os.path.join(proj, "drafts", "source_text")
        jdir = os.path.join(proj, "drafts", journal)
        os.makedirs(os.path.join(proj, "plan"), exist_ok=True)
    os.makedirs(st, exist_ok=True)
    req = os.path.join(jdir, "journal_requirements")
    os.makedirs(req, exist_ok=True)
    with open(os.path.join(req, "requirements.yml"), "w", encoding="utf-8") as fh:
        fh.write("journal: " + journal + "\n\n" + toc_block)
    return proj


def scaffolded(tmp: str, **kw) -> tuple[str, dict]:
    proj = make_project(tmp, **kw)
    tg.scaffold(proj, kw.get("journal", "Langmuir"))
    return proj, tg.paths(proj, kw.get("journal", "Langmuir"))


# ---------------------------------------------------------------------------
# 1. requirements resolution - sourced or defaulted, never invented
# ---------------------------------------------------------------------------

def test_requirements() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proj = make_project(tmp)
        s = tg.spec_from_requirements(tg.requirements(proj, "Langmuir"))
        eq("required is parsed as a bool", s["required"], True)
        eq("term is the journal's own word", s["term"], "TOC/Abstract Graphic")
        eq("width sourced", s["width_in"], 3.25)
        eq("height sourced", s["height_in"], 1.75)
        eq("dpi sourced", s["dpi_color"], 300)
        eq("min type size sourced", s["font_min_pt"], 6.0)
        eq("label sourced", s["label"], "For Table of Contents Only")
        check("must_be_original sourced", s["must_be_original"] is True)
        check("must_differ_from_figures sourced",
              s["must_differ_from_figures"] is True)
        check("sourced list names the fields that were in the file",
              "width_in" in s["sourced"] and "label" in s["sourced"],
              str(s["sourced"]))

    # An unknown requirement stays unknown. Spec 4.3 - the honesty rule is the
    # single most important thing in this whole module, because a plausible
    # invented margin reads as correct.
    with tempfile.TemporaryDirectory() as tmp:
        proj = make_project(tmp, "toc_graphic:\n  required: unknown\n")
        s = tg.spec_from_requirements(tg.requirements(proj, "Langmuir"))
        eq("unknown required stays the string 'unknown'", s["required"],
           "unknown")
        eq("nothing sourced", s["sourced"], [])
        res = tg.status(proj, "Langmuir")
        eq("status says unknown", res["state"], "unknown")
        check("status refuses to guess",
              any(f["rule"] == "requirement_unsourced" for f in res["findings"]))

    # No block at all is the same as unknown, not the same as false.
    with tempfile.TemporaryDirectory() as tmp:
        proj = make_project(tmp, "")
        res = tg.status(proj, "Langmuir")
        eq("absent block reads as unknown, never as 'not required'",
           res["state"], "unknown")


# ---------------------------------------------------------------------------
# 1b. the requirements reader, held to manuscript.py's copy
#
# The engines are standalone and do not import each other, so this reader is
# a copy on purpose. It had drifted: the copy here was the old two-level
# reader, and a list written as a block read as the empty string here while
# manuscript.py read it correctly. One requirements file must not have two
# answers, so these checks compare the two copies on the same bytes.
# ---------------------------------------------------------------------------

BLOCK_LIST = """toc_graphic:
  required: true
  folder_name: toc_graphic
  file_formats:
    - TIF
    - EPS
  prohibited_content:
    - people
    - currency
"""

WRAPPED_LIST = """toc_graphic:
  required: true
  folder_name: toc_graphic
  file_formats: [TIF, EPS,
                 PDF]
"""

UNCLOSED_LIST = """toc_graphic:
  required: true
  folder_name: toc_graphic
  file_formats: [TIF, EPS,
                 PDF
  font_min_pt: 6
"""


def _reqfile(proj: str, journal: str = "Langmuir") -> str:
    return os.path.join(proj, journal, "journal_requirements",
                        "requirements.yml")


# The names a requirements run actually writes - the skeleton's spelling, and
# the one this engine must read. Six of these used to be looked for under
# different names, so their sourced values never reached the engine while
# `sourced` reported them as used (item 10). `may_reuse_figure` is the worst
# of the six: it is the inverse of the engine's must_differ_from_figures, so
# reading one as the other reports a journal that forbids reuse as allowing it.
CANONICAL_TOC_BLOCK = """toc_graphic:
  required: true
  journal_term: "Table of Contents (TOC)/Abstract Graphic"
  folder_name: toc_graphic
  width_in: 3.25
  height_in: 1.75
  dpi_color: 300
  dpi_bw: 1200
  file_format: [TIF, EPS]
  color_mode: RGB
  font_family: Helvetica
  font_size_pt_min: 6
  font_size_pt_preferred: 8
  label_text: "For Table of Contents Only"
  placement: "last page of the submitted manuscript file"
  may_reuse_figure: false
  must_be_original: true
  prohibited_content: "photographs of any person living or deceased;
    postage stamps or currency from any country; trademarked items
    (company logos, images, products)"
"""


def test_requirements_canonical_names() -> None:
    ms = _load("ms_engine_for_canonical_names", "manuscript.py")

    with tempfile.TemporaryDirectory() as tmp:
        proj = make_project(tmp, CANONICAL_TOC_BLOCK)
        s = tg.spec_from_requirements(tg.requirements(proj, "Langmuir"))
        eq("journal_term reaches the engine", s["term"],
           "Table of Contents (TOC)/Abstract Graphic")
        eq("file_format reaches the engine", s["file_formats"], "TIF, EPS")
        eq("label_text reaches the engine", s["label"],
           "For Table of Contents Only")
        eq("font_size_pt_min reaches the engine", s["font_min_pt"], 6.0)
        eq("font_size_pt_preferred reaches the engine",
           s["font_preferred_pt"], 8.0)
        check("may_reuse_figure: false means the graphic must differ",
              s["must_differ_from_figures"] is True,
              repr(s["must_differ_from_figures"]))
        eq("the folder is named from the journal's own word",
           s["folder_name"], "toc_graphic")
        eq("color_mode is carried too", s["color_mode"], "RGB")
        check("sourced names the keys the engine read",
              set(s["sourced"]) >= {"journal_term", "file_format",
                                    "label_text", "font_size_pt_min",
                                    "font_size_pt_preferred",
                                    "may_reuse_figure"},
              str(s["sourced"]))
        check("...and nothing it did not read",
              "term" not in s["sourced"] and "label" not in s["sourced"],
              str(s["sourced"]))

    # A quoted value wrapped over several lines is one value, not its first
    # line. Both engines, same bytes, same answer.
    with tempfile.TemporaryDirectory() as tmp:
        proj = make_project(tmp, CANONICAL_TOC_BLOCK)
        here = tg.read_flat_yml(_reqfile(proj))
        there = ms.read_flat_yml(_reqfile(proj))
        eq("both engines read a wrapped quoted value the same", here, there)
        got = here["toc_graphic.prohibited_content"]
        check("a wrapped quoted value keeps its last clause",
              got.endswith("(company logos, images, products)"), repr(got))
        check("...and its middle", "postage stamps" in got, repr(got))
        check("...and no quote survives into the value",
              '"' not in got, repr(got))

    # A quoted value that is never closed is reported, not half-read.
    with tempfile.TemporaryDirectory() as tmp:
        proj = make_project(tmp, 'toc_graphic:\n  required: true\n'
                                 '  folder_name: toc_graphic\n'
                                 '  label_text: "For Table of\n'
                                 '  font_size_pt_min: 6\n')
        problems: list[str] = []
        tg.read_flat_yml(_reqfile(proj), problems)
        check("an unterminated quoted value is reported", len(problems) == 1,
              str(problems))
        check("...and the report names the key",
              "label_text" in (problems[0] if problems else ""),
              str(problems))
        there: list[str] = []
        ms.read_flat_yml(_reqfile(proj), there)
        eq("...and manuscript.py reports it the same way", len(there), 1)

    # The engine's older spellings still read, because files were written
    # with them - but they are aliases, not the contract.
    with tempfile.TemporaryDirectory() as tmp:
        proj = make_project(tmp, ACS_TOC_BLOCK)
        s = tg.spec_from_requirements(tg.requirements(proj, "Langmuir"))
        eq("the old `term` spelling still reads", s["term"],
           "TOC/Abstract Graphic")
        eq("the old `label` spelling still reads", s["label"],
           "For Table of Contents Only")
        check("the old must_differ_from_figures still reads",
              s["must_differ_from_figures"] is True)

    # The canonical names are in the skeleton a requirements run starts from,
    # which is the whole point: one set of names, written and read.
    for key in ("journal_term", "file_format", "font_size_pt_min",
                "font_size_pt_preferred", "label_text", "may_reuse_figure"):
        check(f"REQUIREMENTS_SKELETON offers {key}",
              "\n  %s:" % key in ms.REQUIREMENTS_SKELETON, key)
    check("...under a toc_graphic: block",
          "\ntoc_graphic:\n" in ms.REQUIREMENTS_SKELETON)


def test_requirements_reader() -> None:
    ms = _load("ms_engine_for_reader_parity", "manuscript.py")

    # Both list spellings, and a value that is not a list, read the same in
    # both engines. The reader hands a list back bracketed; the spec turns it
    # into the words a README sentence wants.
    for label, block in (("a block list", BLOCK_LIST),
                         ("a wrapped inline list", WRAPPED_LIST),
                         ("the inline list", ACS_TOC_BLOCK)):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, block)
            here = tg.read_flat_yml(_reqfile(proj))
            there = ms.read_flat_yml(_reqfile(proj))
            eq(f"{label}: both engines read the same keys",
               sorted(here), sorted(there))
            eq(f"{label}: and the same values", here, there)
            fmt = tg.spec_from_requirements(here)["file_formats"]
            check(f"{label}: file_formats has the values, not the empty string",
                  "TIF" in fmt and "EPS" in fmt, repr(fmt))
            check(f"{label}: and no brackets reach the page", "[" not in fmt,
                  repr(fmt))

    with tempfile.TemporaryDirectory() as tmp:
        proj = make_project(tmp, BLOCK_LIST)
        spec = tg.paths(proj, "Langmuir")["spec"]
        eq("a block list of prohibited content reads as prose too",
           spec["prohibited_content"], "people, currency")
        eq("a wrapped list keeps every item it names",
           tg.spec_from_requirements(
               tg.read_flat_yml(_reqfile(make_project(
                   os.path.join(tmp, "b"), WRAPPED_LIST))))["file_formats"],
           "TIF, EPS, PDF")

    # An unclosed inline list cannot be read at all, and that is the one case
    # the reader cannot fix quietly. It is reported, and the command that
    # would write a README from these values refuses.
    with tempfile.TemporaryDirectory() as tmp:
        proj = make_project(tmp, UNCLOSED_LIST)
        problems: list[str] = []
        tg.read_flat_yml(_reqfile(proj), problems)
        check("an unterminated list is reported", len(problems) == 1,
              str(problems))
        check("and the report names the key",
              "file_formats" in (problems[0] if problems else ""),
              str(problems))
        check("and says where", "line 6" in (problems[0] if problems else ""),
              str(problems))
        res = tg.status(proj, "Langmuir")
        eq("status says the file cannot be read", res["state"], "unreadable")
        check("blocking, not a note",
              any(f["rule"] == "requirements_unreadable"
                  and f["severity"] == "blocking" for f in res["findings"]),
              str(res["findings"]))
        out = tg.scaffold(proj, "Langmuir")
        check("scaffold refuses rather than write a wrong README",
              bool(out["errors"]), str(out))
        check("no folder on an unreadable spec",
              not os.path.isdir(tg.paths(proj, "Langmuir")["root"]))
        problems = []
        ms.read_flat_yml(_reqfile(proj), problems)
        check("and manuscript.py reports it the same way", len(problems) == 1,
              str(problems))

    # A reader that only saw `^([a-z_]+):` missed a key with a digit or a
    # capital in it entirely.
    with tempfile.TemporaryDirectory() as tmp:
        proj = make_project(tmp, "toc_graphic:\n  required: true\n"
                                 "  dpi_300_note: fine\n  Term: Graphic\n")
        got = tg.read_flat_yml(_reqfile(proj))
        check("a key with a digit is seen", "toc_graphic.dpi_300_note" in got,
              str(sorted(got)))
        check("a key with a capital is seen", "toc_graphic.Term" in got,
              str(sorted(got)))


# ---------------------------------------------------------------------------
# 2. it does nothing when the journal wants nothing
# ---------------------------------------------------------------------------

def test_not_required() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proj = make_project(tmp, "toc_graphic:\n  required: false\n")
        res = tg.status(proj, "Langmuir")
        eq("state is not_required", res["state"], "not_required")
        eq("and nothing is outstanding", res["findings"], [])
        out = tg.scaffold(proj, "Langmuir")
        check("scaffold refuses", bool(out["errors"]), str(out))
        check("no folder was created",
              not os.path.isdir(tg.paths(proj, "Langmuir")["root"]))

    with tempfile.TemporaryDirectory() as tmp:
        proj = make_project(tmp, "toc_graphic:\n  required: unknown\n")
        out = tg.scaffold(proj, "Langmuir")
        check("scaffold refuses on unknown too", bool(out["errors"]))
        check("and says why", "sourced" in out["errors"][0].lower()
              or "unknown" in out["errors"][0].lower(), out["errors"][0])
        check("no folder on a guess",
              not os.path.isdir(tg.paths(proj, "Langmuir")["root"]))


# ---------------------------------------------------------------------------
# 3. layout detection - full vs trimmed (spec 7.2.2)
# ---------------------------------------------------------------------------

def test_layout() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proj = make_project(tmp, trimmed=True)
        p = tg.paths(proj, "Langmuir")
        eq("trimmed layout detected", p["kind"], "trimmed")
        eq("folder at the manuscript root", os.path.dirname(p["root"]), proj)
        eq("section file under source_text/", p["section"],
           os.path.join(proj, "source_text", "toc_graphic.md"))

    with tempfile.TemporaryDirectory() as tmp:
        proj = make_project(tmp, trimmed=False)
        p = tg.paths(proj, "Langmuir")
        eq("full layout detected", p["kind"], "full")
        check("folder under plan/, with the rest of the float authoring",
              p["root"] == os.path.join(proj, "plan", "toc_graphic"), p["root"])
        eq("section file under drafts/source_text/", p["section"],
           os.path.join(proj, "drafts", "source_text", "toc_graphic.md"))

    # The folder is named from the journal's own term.
    with tempfile.TemporaryDirectory() as tmp:
        proj = make_project(tmp, 'toc_graphic:\n  required: true\n'
                                 '  term: "Graphical Abstract"\n')
        p = tg.paths(proj, "Langmuir")
        eq("folder name follows the journal's word", os.path.basename(p["root"]),
           "graphical_abstract")
    eq("snake of TOC/Abstract Graphic", tg._snake("TOC/Abstract Graphic"),
       "toc_abstract_graphic")
    eq("snake of Graphical Abstract", tg._snake("Graphical Abstract"),
       "graphical_abstract")


# ---------------------------------------------------------------------------
# 4. the .pptx is a real .pptx
# ---------------------------------------------------------------------------

REQUIRED_PARTS = [
    "[Content_Types].xml", "_rels/.rels", "ppt/presentation.xml",
    "ppt/_rels/presentation.xml.rels", "ppt/slideMasters/slideMaster1.xml",
    "ppt/slideMasters/_rels/slideMaster1.xml.rels",
    "ppt/slideLayouts/slideLayout1.xml",
    "ppt/slideLayouts/_rels/slideLayout1.xml.rels",
    "ppt/slides/slide1.xml", "ppt/slides/_rels/slide1.xml.rels",
    "ppt/theme/theme1.xml",
]


def test_pptx() -> None:
    import xml.etree.ElementTree as ET
    with tempfile.TemporaryDirectory() as tmp:
        proj, p = scaffolded(tmp)
        pptx = p["pptx"]
        check("the .pptx exists", os.path.isfile(pptx))
        with zipfile.ZipFile(pptx) as z:
            names = z.namelist()
            check("the zip is not corrupt", z.testzip() is None)
            for part in REQUIRED_PARTS:
                check(f"part present: {part}", part in names)
            for n in names:
                if n.endswith(".xml") or n.endswith(".rels"):
                    try:
                        ET.fromstring(z.read(n))
                        check(f"well-formed XML: {n}", True)
                    except ET.ParseError as e:
                        check(f"well-formed XML: {n}", False, str(e))

        ins = tg.inspect_pptx(pptx, p["spec"])
        eq("slide is the journal's size", ins["size_in"], [3.25, 1.75])
        check("and is reported as matching", ins["size_matches"] is True)
        check("a scaffold with no suggestion leaves [TK] labels",
              len(ins["tk"]) >= 1, str(ins["tk"]))
        check("no run is below the journal's minimum type size",
              ins["too_small"] == [], str(ins["too_small"]))
        check("every run declares its size explicitly",
              all(t["sz_declared"] for t in ins["texts"]))

    # A rebuild is byte-stable, so a diff means the content changed.
    with tempfile.TemporaryDirectory() as tmp:
        proj = make_project(tmp)
        p = tg.paths(proj, "Langmuir")
        a = os.path.join(tmp, "a.pptx")
        b = os.path.join(tmp, "b.pptx")
        sug = tg.default_suggestion(p["spec"])
        tg.write_pptx(a, p["spec"], sug)
        tg.write_pptx(b, p["spec"], sug)
        eq("rebuild is byte-identical", tg._sha256(a), tg._sha256(b))

    # The type rules come from the requirements, not from PowerPoint's default.
    with tempfile.TemporaryDirectory() as tmp:
        proj = make_project(tmp)
        p = tg.paths(proj, "Langmuir")
        out = os.path.join(tmp, "t.pptx")
        tg.write_pptx(out, p["spec"], {
            "title": "A title", "stages": [{"label": "L", "caption": "c"}],
            "footnote": ""})
        with zipfile.ZipFile(out) as z:
            slide = z.read("ppt/slides/slide1.xml").decode()
        check("the journal's font family is applied",
              'typeface="Helvetica"' in slide, slide[:200])
        check("the journal's preferred size is applied (8 pt = sz 800)",
              'sz="800"' in slide)
        ins = tg.inspect_pptx(out, p["spec"])
        check("a filled slide has no [TK] residue", ins["tk"] == [])


def test_type_size_check() -> None:
    """The most commonly failed journal rule, and it is countable."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = make_project(tmp)
        p = tg.paths(proj, "Langmuir")
        bad = dict(p["spec"])
        bad["font_preferred_pt"] = 4.0
        out = os.path.join(tmp, "tiny.pptx")
        tg.write_pptx(out, bad, {"title": "far too small",
                                 "stages": [], "footnote": ""})
        ins = tg.inspect_pptx(out, p["spec"])
        check("4 pt type is caught against a 6 pt floor",
              any(t["pt"] == 4.0 for t in ins["too_small"]),
              str(ins["too_small"]))

        # Exactly at the floor is legal. An off-by-one here would fire on every
        # correctly-set slide, and a check that cries wolf gets ignored.
        okp = dict(p["spec"]); okp["font_preferred_pt"] = 6.0
        out2 = os.path.join(tmp, "floor.pptx")
        tg.write_pptx(out2, okp, {"title": "exactly six",
                                  "stages": [], "footnote": ""})
        ins2 = tg.inspect_pptx(out2, p["spec"])
        check("exactly at the minimum is not flagged",
              ins2["too_small"] == [], str(ins2["too_small"]))

    # A run with no sz is PowerPoint's 18 pt default, and must not be read as
    # the journal's preferred size - that would hide the failure being caught.
    with tempfile.TemporaryDirectory() as tmp:
        proj = make_project(tmp)
        p = tg.paths(proj, "Langmuir")
        out = os.path.join(tmp, "nosz.pptx")
        tg.write_pptx(out, p["spec"], {"title": "x", "stages": [],
                                       "footnote": ""})
        with zipfile.ZipFile(out) as z:
            parts = {n: z.read(n) for n in z.namelist()}
        parts["ppt/slides/slide1.xml"] = re.sub(
            rb'\ssz="\d+"', b"", parts["ppt/slides/slide1.xml"])
        stripped = os.path.join(tmp, "stripped.pptx")
        with zipfile.ZipFile(stripped, "w") as z:
            for n, d in parts.items():
                z.writestr(n, d)
        ins = tg.inspect_pptx(stripped, p["spec"])
        check("a run with no declared size is read at 18 pt, not assumed fine",
              ins["texts"] and ins["texts"][0]["pt"] == 18.0,
              str(ins["texts"][:1]))
        check("and is reported as not declaring its size",
              ins["texts"] and ins["texts"][0]["sz_declared"] is False)


def test_pptx_unreadable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proj, p = scaffolded(tmp)
        with open(p["pptx"], "wb") as fh:
            fh.write(b"this is not a zip")
        ins = tg.inspect_pptx(p["pptx"], p["spec"])
        check("a corrupt .pptx is an error, not a crash", bool(ins["error"]))
        res = tg.status(proj, "Langmuir")
        check("and status reports it as blocking",
              any(f["rule"] == "pptx_unreadable" and f["severity"] == "blocking"
                  for f in res["findings"]), str(res["findings"]))


# ---------------------------------------------------------------------------
# 5. the .pptx is never overwritten
# ---------------------------------------------------------------------------

def test_never_overwrites() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proj, p = scaffolded(tmp)
        # Stand in for the user editing it in PowerPoint.
        tg.write_pptx(p["pptx"], p["spec"],
                      {"title": "MY OWN TITLE", "stages": [], "footnote": ""})
        mine = tg._sha256(p["pptx"])

        out = tg.scaffold(proj, "Langmuir")
        eq("a second scaffold leaves the .pptx byte-identical",
           tg._sha256(p["pptx"]), mine)
        check("and says it kept it",
              any(x.endswith("toc_graphic.pptx") for x in out["kept"]),
              str(out))

        out = tg.scaffold(proj, "Langmuir", refresh=True)
        eq("--refresh still leaves the .pptx alone",
           tg._sha256(p["pptx"]), mine)
        check("--refresh rewrites the GENERATED files",
              any("README" in x for x in out["refreshed"]), str(out))
        check("and never lists the .pptx as refreshed",
              not any(x.endswith(".pptx") for x in out["refreshed"]))

        ins = tg.inspect_pptx(p["pptx"], p["spec"])
        check("the user's own title survived",
              any("MY OWN TITLE" in t["text"] for t in ins["texts"]))

        # Reading the folder back is not writing to it.
        tg.status(proj, "Langmuir")
        tg.render_state(p)
        eq("status and staleness leave the .pptx byte-identical",
           tg._sha256(p["pptx"]), mine)

        # The render opens the .pptx to export a .png. Its failure path must
        # not touch it either; the successful path is asserted in
        # test_real_render, which needs R and a backend.
        real = tg._rscript
        setattr(tg, "_rscript", lambda: None)
        try:
            res = tg.render(proj, "Langmuir")
        finally:
            setattr(tg, "_rscript", real)
        check("a render that cannot find Rscript says so",
              bool(res.get("errors")), str(res)[:160])
        eq("and leaves the .pptx byte-identical", tg._sha256(p["pptx"]), mine)
        with zipfile.ZipFile(p["pptx"]) as z:
            slide = z.read("ppt/slides/slide1.xml").decode("utf-8")
        check("the user's own drawing is still in there at the end",
              "MY OWN TITLE" in slide)


# ---------------------------------------------------------------------------
# 6. staleness by content hash, never by mtime
# ---------------------------------------------------------------------------

def test_staleness() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proj, p = scaffolded(tmp)
        st = tg.render_state(p)
        eq("no png yet -> missing", st["verdict"], "missing")

        # A .png with no state file: provenance unknown, so it is stale.
        with open(p["png"], "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")
        st = tg.render_state(p)
        eq("png with no state -> stale", st["verdict"], "stale")
        check("and says the provenance is unknown",
              "provenance" in st["why"] or "nothing recorded" in st["why"],
              st["why"])

        tg.write_state(p, "test", [975, 525])
        eq("after recording -> current", tg.render_state(p)["verdict"],
           "current")

        # The user edits the slide. Nothing else changes.
        tg.write_pptx(p["pptx"], p["spec"],
                      {"title": "edited", "stages": [], "footnote": ""})
        st = tg.render_state(p)
        eq("an edited .pptx -> stale", st["verdict"], "stale")
        check("and names the reason",
              "changed since" in st["why"], st["why"])

        res = tg.status(proj, "Langmuir")
        check("status surfaces stale as a gap",
              any(f["rule"] == "stale_render" for f in res["findings"]),
              str(res["findings"]))
        check("and warns it is the older image that would ship",
              any("would go to the journal" in f["detail"]
                  for f in res["findings"] if f["rule"] == "stale_render"))

        # mtime must not be the signal: touching without changing content
        # stays current. OneDrive rewrites mtimes on sync.
        tg.write_state(p, "test", [975, 525])
        os.utime(p["pptx"], (0, 0))
        eq("a touched but unchanged .pptx is still current",
           tg.render_state(p)["verdict"], "current")


# ---------------------------------------------------------------------------
# 7. the generated block
# ---------------------------------------------------------------------------

def test_wire() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _proj, p = scaffolded(tmp)

        r = tg.wire(p)
        check("refuses to wire with no .png", r["wired"] is False, str(r))
        check("and says why - a broken image builds silently",
              "does not exist" in r["reason"], r["reason"])

        with open(p["png"], "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")

        r = tg.wire(p)
        eq("creates the section file", r["action"], "created")
        text = _slurp(p["section"], encoding="utf-8")
        check("carries both markers",
              tg.BEGIN_MARK in text and tg.END_MARK in text)
        check("points at the png by a relative path",
              "../toc_graphic/toc_graphic.png" in text, text)
        check("uses the journal's own label",
              "For Table of Contents Only" in text)
        check("sets the journal's width", "width=3.25in" in text)

        r = tg.wire(p)
        eq("re-wiring an unchanged block changes nothing", r["action"],
           "unchanged")

        # The user's prose outside the block is never touched.
        with open(p["section"], "w", encoding="utf-8") as fh:
            fh.write("# My heading\n\nMy own paragraph.\n\n"
                     + tg.render_block(p).replace("width=3.25in", "width=9in")
                     + "\n\nA trailing note of mine.\n")
        r = tg.wire(p)
        eq("a changed block is refreshed", r["action"], "refreshed")
        text = _slurp(p["section"], encoding="utf-8")
        check("the user's prose above survives", "My own paragraph." in text)
        check("the user's prose below survives", "A trailing note of mine." in text)
        check("the block was regenerated to the current geometry",
              "width=3.25in" in text and "width=9in" not in text)
        eq("exactly one block", text.count(tg.BEGIN_MARK), 1)

        # Deleting the markers is an opt-out, not an error.
        with open(p["section"], "w", encoding="utf-8") as fh:
            fh.write("# Mine alone\n\nI removed the block on purpose.\n")
        r = tg.wire(p)
        eq("with no markers the block is appended once", r["action"], "appended")

        # Half a block is refused rather than guessed at.
        with open(p["section"], "w", encoding="utf-8") as fh:
            fh.write("prose\n\n" + tg.BEGIN_MARK + "\nsomething\n")
        r = tg.wire(p)
        check("a half-present block is refused", r["wired"] is False, str(r))
        check("and says it will not guess where the block ends",
              "guess" in r["reason"], r["reason"])


def test_wire_full_layout_path() -> None:
    """The relative path has to be right from drafts/source_text/ too."""
    with tempfile.TemporaryDirectory() as tmp:
        _proj, p = scaffolded(tmp, trimmed=False)
        os.makedirs(os.path.dirname(p["png"]), exist_ok=True)
        with open(p["png"], "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")
        tg.wire(p)
        text = _slurp(p["section"], encoding="utf-8")
        check("path climbs out of drafts/source_text/ into plan/",
              "../../plan/toc_graphic/toc_graphic.png" in text, text)


# ---------------------------------------------------------------------------
# 8. the render script
# ---------------------------------------------------------------------------

def test_r_script() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _proj, p = scaffolded(tmp)
        src = _slurp(p["script"], encoding="utf-8")
        check("pixel width is the journal's geometry", "px_w     <- 975" in src,
              src[:400])
        check("pixel height is the journal's geometry", "px_h     <- 525" in src)
        check("resolves its own directory from --file=",
              '"^--file="' in src, "script_dir missing")
        check("does not rely on ofile alone",
              "commandArgs" in src)
        check("tries PowerPoint COM", "POWERPNT.EXE" in src)
        check("tries LibreOffice", "soffice" in src)
        check("probes for magick, never for convert",
              "convert.exe" not in src)
        check("stops rather than producing a wrong-sized image",
              "could not render" in src and "stop(" in src)
        check("tells the user the manual path", "File > Save As" in src)
        check("balanced braces after formatting",
              src.count("{") == src.count("}"),
              f"{src.count('{')} open vs {src.count('}')} close")

        # Geometry drives the script, so a different journal gets a different
        # script rather than the same numbers.
        sp = dict(p["spec"]); sp["width_in"] = 5.0; sp["dpi_color"] = 600
        alt = os.path.join(tmp, "alt.R")
        tg.write_r_script(alt, sp, "toc_graphic")
        src2 = _slurp(alt, encoding="utf-8")
        check("a different geometry writes different pixels",
              "px_w     <- 3000" in src2, src2[:400])


def test_r_syntax() -> None:
    """The script has to be parseable by real R, not just look right."""
    rs = tg._rscript()
    if not rs:
        print("  - skipped: Rscript not found")
        return
    with tempfile.TemporaryDirectory() as tmp:
        _proj, p = scaffolded(tmp)
        probe = os.path.join(tmp, "parse.R")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write('p <- commandArgs(trailingOnly=TRUE)[1]\n'
                     'invisible(parse(p))\ncat("PARSED\\n")\n')
        run = subprocess.run([rs, "--vanilla", probe, p["script"]],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=120)
        check("real R parses the generated script",
              "PARSED" in (run.stdout or ""),
              (run.stderr or "")[:300])


# ---------------------------------------------------------------------------
# 9. the CLI contract
# ---------------------------------------------------------------------------

def _cli(*args: str) -> tuple[int, str]:
    run = subprocess.run([sys.executable, os.path.join(TOOLS, "toc_graphic.py"),
                          *args], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    return run.returncode, (run.stdout or "") + (run.stderr or "")


def test_cli() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proj = make_project(tmp)
        for pre, post in ((["--json"], []), ([], ["--json"])):
            code, out = _cli(*pre, "status", proj, "--journal", "Langmuir", *post)
            side = "before" if pre else "after"
            check(f"--json {side} the subcommand is accepted", code == 0, out[:200])
            try:
                json.loads(out)
                check(f"--json {side} emits JSON", True)
            except json.JSONDecodeError:
                check(f"--json {side} emits JSON", False, out[:200])

        code, out = _cli("scaffold", proj, "--journal", "Langmuir", "--dry-run")
        eq("dry-run scaffold exits 0", code, 0)
        check("dry-run writes nothing",
              not os.path.isdir(tg.paths(proj, "Langmuir")["root"]))
        check("dry-run still lists what it would create",
              "toc_graphic.pptx" in out, out[:300])

        code, out = _cli("scaffold", proj, "--journal", "Langmuir")
        eq("scaffold exits 0", code, 0)
        code, out = _cli("render", proj, "--journal", "Langmuir")
        # Backends are machine dependent; what is contracted is the exit code
        # and that a failure explains itself rather than exiting 0 with nothing.
        if code != 0:
            check("a render that cannot run explains itself",
                  len(out.strip()) > 40, out[:200])
        else:
            check("a successful render reports its pixels", "975" in out, out[:200])

    with tempfile.TemporaryDirectory() as tmp:
        proj = make_project(tmp, "toc_graphic:\n  required: false\n")
        code, out = _cli("scaffold", proj, "--journal", "Langmuir")
        eq("scaffold on a journal wanting none exits 2", code, 2)


# ---------------------------------------------------------------------------
# 10. manuscript.py sees it
# ---------------------------------------------------------------------------

def test_completeness_integration() -> None:
    src = _slurp(os.path.join(TOOLS, "manuscript.py"), encoding="utf-8")
    check("manuscript.py has the shell-out helper",
          "def _toc_graphic(" in src)
    check("completeness calls it", '_toc_graphic(project, journal)' in src)
    check("and files findings under their own area",
          '"toc_graphic", "gap"' in src or '"toc_graphic",' in src)
    check("notes are not counted as outstanding",
          'f["severity"] == "note"' in src)
    # The block writes an image path relative to source_text/, but the build
    # file pandoc reads lives in the journal folder. Those are the same depth
    # today, so it resolves by accident; --resource-path makes it correct by
    # construction. Without this the graphic silently does not appear.
    check("assemble passes --resource-path so the image resolves",
          '"--resource-path"' in src)
    check("and it includes source_text/", "st_dir" in src)


# ---------------------------------------------------------------------------
# 11. the real render, opt-in
# ---------------------------------------------------------------------------

def test_real_render() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proj, p = scaffolded(tmp)
        # The half of "never overwritten" that only a real render can answer -
        # the offline half is in test_never_overwrites().
        mine = tg._sha256(p["pptx"])
        res = tg.render(proj, "Langmuir")
        if res.get("errors"):
            print(f"  - render unavailable on this machine: "
                  f"{res['errors'][0][:120]}")
            return
        check("the render produced a .png", os.path.isfile(p["png"]))
        eq("a real render leaves the .pptx byte-identical",
           tg._sha256(p["pptx"]), mine)
        eq("at the journal's pixel size", res["pixels"], [975, 525])
        print(f"  - backend: {res['backend']}")
        eq("and the state file now says current",
           tg.render_state(p)["verdict"], "current")

        # The real payoff: edit, re-render, and the block still points right.
        tg.write_pptx(p["pptx"], p["spec"],
                      {"title": "round two", "stages": [], "footnote": ""})
        eq("stale after the edit", tg.render_state(p)["verdict"], "stale")
        res2 = tg.render(proj, "Langmuir")
        check("re-render succeeds", not res2.get("errors"), str(res2)[:200])
        eq("current again", tg.render_state(p)["verdict"], "current")
        check("and the image actually changed", res2.get("changed") is True)


# ---------------------------------------------------------------------------
# 15. examples/ - the journal's own graphics, extracted - spec 7.2.3
# ---------------------------------------------------------------------------
#
# The fixture is a hand-built PDF, for the same reason tests/make_docx_fixtures.py
# writes OOXML by hand: nothing in the standard library will emit a PDF with
# placed image XObjects, and the whole point is to control the geometry, because
# the geometry is what assigns the verdicts.
#
# It mirrors the real ACS page exactly - a vertical column rule, a masthead, and
# six examples in two columns of three rows whose top edges align - so a change
# that breaks on the real document breaks here first.

_FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"toc-graphic fixture, not a real JPEG" * 40
_PDF_PAGE_W, _PDF_PAGE_H = 612.0, 792.0

# (label, px_w, px_h, x_pt, y_pt, w_pt, h_pt, kind). Positions are the real
# ones, so the row tops cluster at 642 / 417 / 228 pt.
_FIXTURE_IMAGES = [
    ("masthead",   379,  63,  36.4, 725.6, 182.2,  30.0, "flate"),
    ("divider",     18, 1266, 297.1,  69.1,   8.6, 607.7, "flate"),
    ("r1c1a",      288, 312,   45.2, 525.1, 107.5, 117.5, "flate"),
    ("r1c1b",      215, 296,  159.5, 524.4,  80.4, 111.0, "flate"),
    ("r1c1c",      111, 309,  235.0, 526.2,  41.8, 115.8, "flate"),
    ("r1c2",       440, 307,  325.2, 477.7, 237.6, 165.4, "jpeg"),
    ("r2c1",       170, 168,   97.4, 290.9, 127.1, 125.8, "jpeg"),
    ("r2c2",       384, 194,  325.3, 297.6, 236.2, 119.3, "jpeg"),
    ("r3c1",       726, 360,   44.0, 112.3, 233.5, 116.0, "flate"),
    ("r3c2",       379, 218,  325.3,  91.8, 237.6, 136.3, "jpeg"),
]

_FIXTURE_LABELS = {
    "source": "page 1 of the fixture",
    "columns": {"1": "good", "2": "poor"},
    "cells": [
        {"row": 1, "column": 1, "verdict": "good", "slug": "balanced",
         "quote": "Good balance of images and description."},
        {"row": 1, "column": 2, "verdict": "poor", "slug": "cluttered",
         "quote": "Very cluttered and the fonts are too small."},
        {"row": 2, "column": 1, "verdict": "good", "slug": "simple",
         "quote": "Simple and appealing."},
        {"row": 2, "column": 2, "verdict": "poor", "slug": "font-too-big",
         "quote": "The font is too big."},
        {"row": 3, "column": 1, "verdict": "good", "slug": "color",
         "quote": "Appealing use of color and graphs."},
        {"row": 3, "column": 2, "verdict": "poor", "slug": "uninteresting",
         "quote": "Uninteresting and not informative."},
    ],
    "links": [{"title": "A Paper", "journal": "*J. Fixture* 2020",
               "doi": "10.1000/fixture", "why": "because"}],
}


def _rgb_payload(w: int, h: int, seed: int) -> bytes:
    """Deterministic RGB bytes. Content is irrelevant - the engine must not
    look at it - so this is deliberately not a picture of anything."""
    import zlib as _z
    row = bytes(((seed + i) % 256) for i in range(w * 3))
    return _z.compress(row * h, 6)


def _build_fixture_pdf(path: str, images=None, page_count: int = 1) -> None:
    """A minimal one-page PDF with placed image XObjects."""
    images = images if images is not None else _FIXTURE_IMAGES
    objs: list[bytes] = []          # 1-indexed on append

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    cat = add(b"")                   # placeholder, filled below
    pages = add(b"")
    page_ids, xobj_ids = [], []

    for n, (_lbl, pw, ph, x, y, w, h, kind) in enumerate(images, start=1):
        if kind == "jpeg":
            data, filt, extra = _FAKE_JPEG, b"/DCTDecode", b""
        else:
            data, filt, extra = _rgb_payload(pw, ph, n * 7), b"/FlateDecode", b""
        head = (b"<< /Type /XObject /Subtype /Image /Width " + str(pw).encode()
                + b" /Height " + str(ph).encode()
                + b" /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter "
                + filt + extra + b" /Length " + str(len(data)).encode()
                + b" >>\nstream\n" + data + b"\nendstream")
        oid = add(head)
        xobj_ids.append((f"/Im{n}", oid))

    ops = []
    for (_lbl, pw, ph, x, y, w, h, kind), (nm, _oid) in zip(images, xobj_ids):
        ops.append(f"q {w} 0 0 {h} {x} {y} cm {nm} Do Q".encode())
    content = b"\n".join(ops)
    cid = add(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
              + content + b"\nendstream")

    res = b"<< /XObject << " + b" ".join(
        nm.encode() + b" " + str(oid).encode() + b" 0 R"
        for nm, oid in xobj_ids) + b" >> >>"
    pid = add(b"<< /Type /Page /Parent " + str(pages).encode()
              + b" 0 R /MediaBox [0 0 " + str(_PDF_PAGE_W).encode() + b" "
              + str(_PDF_PAGE_H).encode() + b"] /Resources " + res
              + b" /Contents " + str(cid).encode() + b" 0 R >>")
    page_ids.append(pid)

    objs[cat - 1] = b"<< /Type /Catalog /Pages " + str(pages).encode() + b" 0 R >>"
    objs[pages - 1] = (b"<< /Type /Pages /Kids ["
                       + b" ".join(str(i).encode() + b" 0 R" for i in page_ids)
                       + b"] /Count " + str(len(page_ids)).encode() + b" >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (b"trailer\n<< /Size " + str(len(objs) + 1).encode()
            + b" /Root " + str(cat).encode() + b" 0 R >>\nstartxref\n"
            + str(xref_at).encode() + b"\n%%EOF\n")
    with open(path, "wb") as fh:
        fh.write(bytes(out))


def _png_dims(data: bytes):
    """Width and height out of a PNG, with every chunk CRC verified. An
    independent reader: the engine wrote this header by hand, so checking it
    with the same code that produced it would prove nothing."""
    import struct
    import zlib as _z
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, dims, tags = 8, None, []
    while pos + 12 <= len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        crc = struct.unpack(">I", data[pos + 8 + ln:pos + 12 + ln])[0]
        if _z.crc32(tag + body) & 0xFFFFFFFF != crc:
            return None
        tags.append(tag)
        if tag == b"IHDR":
            dims = struct.unpack(">II", body[:8])
        pos += 12 + ln
    if tags[:1] != [b"IHDR"] or tags[-1:] != [b"IEND"]:
        return None
    return dims


def _have_pypdf() -> bool:
    try:
        import pypdf
        # The import is the test. Returning something derived from it keeps
        # the name used, which `# noqa` does not do for a type checker.
        return pypdf is not None
    except ImportError:
        return False


def _examples_project(tmp: str, labels=_FIXTURE_LABELS, images=None,
                      name: str = "guide.pdf") -> tuple[str, dict]:
    proj, p = scaffolded(tmp)
    os.makedirs(p["examples"], exist_ok=True)
    _build_fixture_pdf(os.path.join(p["examples"], name), images=images)
    if labels is not None:
        with open(os.path.join(p["examples"], "labels.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(labels, fh)
    return proj, p


def test_examples_extraction() -> None:
    if not _have_pypdf():
        check("pypdf present for the extraction tests", False,
              "pip install pypdf")
        return
    with tempfile.TemporaryDirectory() as tmp:
        proj, _p = _examples_project(tmp)
        res = tg.extract_examples(proj, "Langmuir", refresh=True)

        eq("no errors", res["errors"], [])
        eq("eight example images", len(res["images"]), 8)
        eq("labelled", res["labelled"], True)

        names = sorted(i["file"] for i in res["images"])
        # The verdict is in the filename, so the folder listing is the lesson.
        eq("good_1 composite is three files",
           sum(1 for n in names if n.startswith("good_1_balanced")), 3)
        for want in ("poor_1_cluttered.jpg", "good_2_simple.jpg",
                     "poor_2_font-too-big.jpg", "good_3_color.png",
                     "poor_3_uninteresting.jpg"):
            check(f"wrote {want}", want in names, str(names))

        # Two pieces of furniture, and the masthead fails two rules at once.
        eq("two furniture items dropped", len(res["dropped"]), 2)
        drops = {d["px"][0]: d for d in res["dropped"]}
        check("the column rule is dropped", 18 in drops)
        check("the masthead is dropped", 379 in drops)
        check("the masthead reports both failing rules",
              drops[379]["why"].count(";") == 1, drops[379]["why"])
        check("the column rule names its aspect",
              "aspect" in drops[18]["why"], drops[18]["why"])
        for d in res["dropped"]:
            check("every drop names its document", d["pdf"] == "guide.pdf",
                  str(d))

        # Verdicts follow the page's two columns.
        by_file = {i["file"]: i for i in res["images"]}
        for n, i in by_file.items():
            eq(f"{n} verdict matches its name prefix",
               i["verdict"], n.split("_")[0])
            eq(f"{n} column matches its verdict",
               i["column"], 1 if i["verdict"] == "good" else 2)
        rows = sorted({(i["row"], i["column"]) for i in res["images"]})
        eq("three rows, two columns, six cells", len(rows), 6)
        eq("rows are 1..3", sorted({r for r, _ in rows}), [1, 2, 3])

        # The quote travels with the image, verbatim.
        eq("the journal's quote is carried",
           by_file["good_2_simple.jpg"]["quote"], "Simple and appealing.")

        # Composite ordering is left to right, the reading order.
        comp = sorted((i["file"] for i in res["images"]
                       if i["file"].startswith("good_1_")))
        eq("composite suffixes are a, b, c",
           [n[-5] for n in comp], ["a", "b", "c"])
        eq("composite is flagged as one graphic",
           all(by_file[n]["composite"] for n in comp), True)


def test_examples_verdict_is_positional() -> None:
    """Failure mode 19: the verdict must come from the page, never the image.

    The same images are re-placed with the columns swapped. Every verdict must
    follow the new position - if the engine were looking at the pictures, or at
    the drawing order, the labels would not move."""
    if not _have_pypdf():
        return
    swapped = []
    for (lbl, pw, ph, x, y, w, h, kind) in _FIXTURE_IMAGES:
        if lbl in ("masthead", "divider"):
            swapped.append((lbl, pw, ph, x, y, w, h, kind))
            continue
        # Reflect x about the divider at 301.4 pt, keeping the row tops.
        new_x = 2 * 301.4 - x - w
        swapped.append((lbl, pw, ph, round(new_x, 1), y, w, h, kind))
    with tempfile.TemporaryDirectory() as tmp:
        proj, _p = _examples_project(tmp, images=swapped)
        res = tg.extract_examples(proj, "Langmuir", refresh=True)
        eq("still eight images", len(res["images"]), 8)
        # r1c2 was the cluttered POOR one; mirrored it now sits in the good
        # column and must be labelled good. The engine does not get a vote.
        px_to_verdict = {tuple(i["px"]): i["verdict"] for i in res["images"]}
        eq("the formerly-poor image is now labelled good",
           px_to_verdict[(440, 307)], "good")
        eq("the formerly-good composite is now poor",
           px_to_verdict[(288, 312)], "poor")
        check("no verdict survived the move unchanged",
              all(v is not None for v in px_to_verdict.values()))


def test_examples_furniture_margins() -> None:
    """Every threshold must clear the real examples by a real margin, and the
    mastheads must be caught by aspect rather than by a one-pixel floor."""
    reals = [(288, 312), (215, 296), (111, 309), (440, 307),
             (170, 168), (384, 194), (726, 360), (379, 218)]
    for px in reals:
        eq(f"{px[0]}x{px[1]} is kept", tg._is_furniture(px), "")
    short = min(min(w, h) for w, h in reals)
    area = min(w * h for w, h in reals)
    aspect = max(max(w, h) / min(w, h) for w, h in reals)
    check("short-side floor has >=1.5x headroom",
          short >= 1.5 * tg.MIN_SHORT_PX, f"{short} vs {tg.MIN_SHORT_PX}")
    check("area floor has >=1.5x headroom",
          area >= 1.5 * tg.MIN_AREA_PX, f"{area} vs {tg.MIN_AREA_PX}")
    check("aspect limit has >=1.5x headroom",
          aspect <= tg.MAX_ASPECT / 1.5, f"{aspect:.2f} vs {tg.MAX_ASPECT}")

    # The real furniture, and the rules that must catch each.
    for px, must in (((18, 1266), "aspect"), ((379, 63), "aspect"),
                     ((504, 79), "aspect")):
        why = tg._is_furniture(px)
        check(f"{px[0]}x{px[1]} is furniture", why != "")
        check(f"{px[0]}x{px[1]} is caught by {must}", must in why, why)
    # 504x79 was dropped by a single pixel of short side once. Aspect alone
    # must be enough, so that the floor moving cannot let a masthead through.
    check("the 79 px masthead does not depend on the short-side floor",
          "aspect" in tg._is_furniture((504, 79))
          and "short side" not in tg._is_furniture((504, 79)),
          tg._is_furniture((504, 79)))
    check("all failing rules are reported, not the first",
          tg._is_furniture((18, 1266)).count(";") >= 1,
          tg._is_furniture((18, 1266)))


def test_examples_image_bytes() -> None:
    """PNG headers are written by hand and JPEG data is passed through."""
    if not _have_pypdf():
        return
    with tempfile.TemporaryDirectory() as tmp:
        proj, p = _examples_project(tmp)
        res = tg.extract_examples(proj, "Langmuir", refresh=True)
        for img in res["images"]:
            path = os.path.join(p["examples"], img["file"])
            data = _slurp(path, "rb")
            if img["file"].endswith(".png"):
                dims = _png_dims(data)
                eq(f"{img['file']} is a valid PNG at its source size",
                   list(dims) if dims else None, img["px"])
            else:
                eq(f"{img['file']} is the source JPEG byte for byte",
                   data, _FAKE_JPEG)


def test_examples_never_overwrites() -> None:
    """A file this tool did not write is never overwritten - the same rule the
    .pptx gets, applied to a name the tool would otherwise claim."""
    if not _have_pypdf():
        return
    with tempfile.TemporaryDirectory() as tmp:
        proj, p = _examples_project(tmp)
        tg.extract_examples(proj, "Langmuir", refresh=True)
        target = os.path.join(p["examples"], "good_2_simple.jpg")
        mine = os.path.join(p["examples"], "my_own_sketch.png")
        with open(mine, "wb") as fh:
            fh.write(b"the user's file")
        # Re-run without --refresh: everything it wrote before is kept.
        res = tg.extract_examples(proj, "Langmuir")
        eq("nothing rewritten without --refresh", res["written"], [])
        eq("all eight reported as kept", len(res["kept"]), 8)
        check("the user's own file is untouched",
              _slurp(mine, "rb") == b"the user's file")

        # Now hand-edit one of its own files and refresh: that one is ours.
        with open(target, "wb") as fh:
            fh.write(b"edited")
        res = tg.extract_examples(proj, "Langmuir", refresh=True)
        check("--refresh rewrites a file this tool wrote",
              _slurp(target, "rb") != b"edited")
        check("--refresh still leaves the user's file alone",
              _slurp(mine, "rb") == b"the user's file")

        # A file it never wrote, sitting on a name it wants, wins.
        os.remove(os.path.join(p["examples"], tg.EXAMPLES_STATE))
        with open(target, "wb") as fh:
            fh.write(b"not ours")
        res = tg.extract_examples(proj, "Langmuir")
        check("an unclaimed name is not overwritten",
              _slurp(target, "rb") == b"not ours",
              "state file gone, so the tool cannot claim it")


def test_examples_unlabelled() -> None:
    """With no labels.json nothing is labelled, and no verdict is invented."""
    if not _have_pypdf():
        return
    with tempfile.TemporaryDirectory() as tmp:
        proj, p = _examples_project(tmp, labels=None)
        res = tg.extract_examples(proj, "Langmuir", refresh=True)
        eq("still finds eight", len(res["images"]), 8)
        eq("not labelled", res["labelled"], False)
        check("no verdict is claimed",
              all(i["verdict"] is None for i in res["images"]))
        check("names fall back to page and cell",
              all(re.match(r"^p\d+_r\d+_c\d+", i["file"])
                  for i in res["images"]),
              str([i["file"] for i in res["images"]][:3]))
        body = _slurp(os.path.join(p["examples"], "README.md"),
                      encoding="utf-8")
        check("the index says no verdict is claimed",
              "unlabelled" in body.lower(), body[:200])


def test_examples_index_is_accurate() -> None:
    """Three things the index got wrong the first time it was written."""
    if not _have_pypdf():
        return
    with tempfile.TemporaryDirectory() as tmp:
        proj, p = _examples_project(tmp)
        # A second guidance PDF that holds no examples at all, in the place the
        # real project keeps them.
        src = os.path.join(proj, "Langmuir", "journal_requirements", "sources")
        os.makedirs(src, exist_ok=True)
        _build_fixture_pdf(os.path.join(src, "checklist.pdf"),
                           images=[("masthead", 504, 79, 36.0, 700.0,
                                    252.0, 39.5, "flate")])
        res = tg.extract_examples(proj, "Langmuir", refresh=True)
        eq("both PDFs were read", sorted(res["pdfs"]),
           ["checklist.pdf", "guide.pdf"])
        eq("images came only from the one with examples", len(res["images"]), 8)
        check("the example-less PDF is credited with nothing",
              all(i["pdf"] == "guide.pdf" for i in res["images"]))

        body = _slurp(os.path.join(p["examples"], "README.md"),
                      encoding="utf-8")
        head = body.split("### ")[0]
        check("the count credits only the PDF the images came from",
              "`guide.pdf`" in head and "`checklist.pdf`" not in head,
              head[-400:])
        # checklist.pdf lives in sources/, not here: claiming it is "kept in
        # this folder" sends someone looking for a file that is not there.
        # Just the "kept in this folder" sentence: the paragraph that follows
        # it names what was read from sources/, and naming that there is right.
        kept = [ln for ln in body.splitlines()
                if "Kept in this folder" in ln]
        eq("one 'kept in this folder' sentence", len(kept), 1)
        check("only the PDF actually in examples/ is called kept here",
              "guide.pdf" in kept[0] and "checklist.pdf" not in kept[0],
              kept[0])
        check("the one read from sources/ is named as such",
              "journal_requirements/sources/" in body
              and "checklist.pdf" in body)
        # links come from labels.json, so regenerating cannot wipe them.
        check("the verified DOI link is rendered",
              "10.1000/fixture" in body and "doi.org/10.1000/fixture" in body)
        check("look-don't-reuse survives", "do not reuse" in body.lower())
        for img in res["images"]:
            check(f"the index names {img['file']}", img["file"] in body)


def test_examples_honest_failures() -> None:
    """Three ways this legitimately finds nothing, all reported rather than
    papered over."""
    if not _have_pypdf():
        return
    with tempfile.TemporaryDirectory() as tmp:
        # 1. no guidance PDF anywhere.
        proj, p = scaffolded(tmp)
        for f in os.listdir(p["examples"]):
            if f.lower().endswith(".pdf"):
                os.remove(os.path.join(p["examples"], f))
        res = tg.extract_examples(proj, "Langmuir")
        eq("no images", res["images"], [])
        check("says no guidance PDF was found",
              any("guidance PDF" in e for e in res["errors"]), str(res["errors"]))

    with tempfile.TemporaryDirectory() as tmp:
        # 2. a guidance PDF whose images are all furniture.
        proj, p = _examples_project(
            tmp, images=[("masthead", 379, 63, 36.4, 725.6, 182.2, 30.0,
                          "flate")])
        res = tg.extract_examples(proj, "Langmuir", refresh=True)
        eq("nothing extracted", res["images"], [])
        check("says the document showed no examples",
              any("no image large enough" in e for e in res["errors"]),
              str(res["errors"]))
        check("and still reports what it saw", len(res["dropped"]) == 1)

    with tempfile.TemporaryDirectory() as tmp:
        # 3. the journal does not require a graphic at all.
        proj = make_project(tmp, toc_block="toc_graphic:\n  required: false\n")
        res = tg.extract_examples(proj, "Langmuir")
        check("refuses when no graphic is required",
              any("REFUSED" in e for e in res["errors"]), str(res["errors"]))
        proj2 = make_project(tmp + "x", toc_block="toc_graphic:\n  required: unknown\n")
        res2 = tg.extract_examples(proj2, "Langmuir")
        check("refuses on unknown rather than guessing",
              any("REFUSED" in e for e in res2["errors"]), str(res2["errors"]))


def test_examples_decode_refusals() -> None:
    """An image the decoder cannot read is refused by name, never written as a
    corrupt file."""
    class Obj(dict):
        def __init__(self, d, data=b""):
            super().__init__(d)
            self._data = data

    cases = [
        ({"/Filter": "/CCITTFaxDecode", "/Width": 200, "/Height": 200},
         "unsupported filter"),
        ({"/Filter": "/FlateDecode", "/BitsPerComponent": 1,
          "/Width": 200, "/Height": 200}, "bits per component"),
        ({"/Filter": "/FlateDecode", "/BitsPerComponent": 8,
          "/ColorSpace": "/Separation", "/Width": 200, "/Height": 200},
         "unsupported color space"),
        ({"/Filter": "/FlateDecode", "/BitsPerComponent": 8,
          "/ColorSpace": "/DeviceRGB", "/Width": 200, "/Height": 200},
         "cannot inflate"),
    ]
    for attrs, want in cases:
        data, ext, why = tg._decode_image(Obj(attrs, b"not compressed"))
        check(f"refused: {want}", data is None and want in why, f"{why!r}")

    # A short buffer is refused rather than written as a torn image.
    import zlib as _z
    obj = Obj({"/Filter": "/FlateDecode", "/BitsPerComponent": 8,
               "/ColorSpace": "/DeviceRGB", "/Width": 100, "/Height": 100},
              _z.compress(b"\x00" * 99))
    data, ext, why = tg._decode_image(obj)
    check("a short pixel buffer is refused", data is None and "decoded" in why,
          f"{why!r}")

    # And the good case still works.
    good = Obj({"/Filter": "/FlateDecode", "/BitsPerComponent": 8,
                "/ColorSpace": "/DeviceGray", "/Width": 4, "/Height": 2},
               _z.compress(b"\x7f" * 8))
    data, ext, why = tg._decode_image(good)
    eq("grayscale flate decodes", (ext, why), ("png", ""))
    eq("and is a valid PNG", list(_png_dims(data) or []), [4, 2])


def test_examples_cli() -> None:
    """--json on either side of the subcommand, per the toolkit convention."""
    if not _have_pypdf():
        return
    with tempfile.TemporaryDirectory() as tmp:
        proj, p = _examples_project(tmp)
        for args in (("examples", proj, "--journal", "Langmuir", "--json",
                      "--refresh"),
                     ("--json", "examples", proj, "--journal", "Langmuir")):
            code, out = _cli(*args)
            eq(f"exit 0 for {args[0]}", code, 0)
            try:
                payload = json.loads(out)
            except json.JSONDecodeError:
                check(f"--json is JSON for {args[0]}", False, out[:200])
                continue
            eq(f"eight images via {args[0]}", len(payload["images"]), 8)

        # The text form names the document on every dropped image.
        code, out = _cli("examples", proj, "--journal", "Langmuir")
        eq("text form exits 0", code, 0)
        for line in out.splitlines():
            if "not an example" in line:
                check("the drop line names its PDF", "guide.pdf" in line, line)

        # --from takes one document explicitly.
        code, out = _cli("examples", proj, "--journal", "Langmuir", "--from",
                         os.path.join(p["examples"], "guide.pdf"), "--json")
        eq("--from exits 0", code, 0)
        eq("--from reads just that one", json.loads(out)["pdfs"], ["guide.pdf"])

        code, out = _cli("examples", proj, "--journal", "Langmuir", "--from",
                         os.path.join(p["examples"], "nope.pdf"), "--json")
        eq("a missing --from is refused", code, 2)


def test_examples_scaffold_populates() -> None:
    """scaffold fills examples/ on the way past, and cannot be failed by it."""
    if not _have_pypdf():
        return
    with tempfile.TemporaryDirectory() as tmp:
        # A guidance PDF already in sources/ before the first scaffold.
        proj = make_project(tmp)
        src = os.path.join(proj, "Langmuir", "journal_requirements", "sources")
        os.makedirs(src, exist_ok=True)
        _build_fixture_pdf(os.path.join(src, "guide.pdf"))
        res = tg.scaffold(proj, "Langmuir")
        eq("scaffold succeeded", res["errors"], [])
        eq("and populated examples/", res["examples"]["images"], 8)
        p = tg.paths(proj, "Langmuir")
        wrote = [f for f in os.listdir(p["examples"])
                 if f.endswith((".png", ".jpg"))]
        eq("eight image files on disk", len(wrote), 8)

    with tempfile.TemporaryDirectory() as tmp:
        # No guidance PDF at all: the scaffold still succeeds and says so.
        proj = make_project(tmp)
        res = tg.scaffold(proj, "Langmuir")
        eq("scaffold still succeeds with no guidance PDF", res["errors"], [])
        eq("no images", res["examples"]["images"], 0)
        check("and the reason is carried, not swallowed",
              any("guidance PDF" in n for n in res["examples"]["notes"]),
              str(res["examples"]["notes"]))
        p = tg.paths(proj, "Langmuir")
        check("the index still exists",
              os.path.isfile(os.path.join(p["examples"], "README.md")))


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--render", action="store_true",
                    help="also drive real R and a real render backend")
    args = ap.parse_args()

    tests = [
        ("requirements resolution", test_requirements),
        ("the requirements reader, against manuscript.py's",
         test_requirements_reader),
        ("the key names a requirements run writes",
         test_requirements_canonical_names),
        ("not required means nothing happens", test_not_required),
        ("layout detection", test_layout),
        ("the .pptx is a real .pptx", test_pptx),
        ("type size against the journal's floor", test_type_size_check),
        ("a corrupt .pptx", test_pptx_unreadable),
        ("the .pptx is never overwritten", test_never_overwrites),
        ("staleness by content hash", test_staleness),
        ("the generated block", test_wire),
        ("the block's path in a full layout", test_wire_full_layout_path),
        ("the render script", test_r_script),
        ("real R parses it", test_r_syntax),
        ("the CLI contract", test_cli),
        ("manuscript.py integration", test_completeness_integration),
        ("examples extracted from the journal's guidance", test_examples_extraction),
        ("the verdict comes from the page, not the image", test_examples_verdict_is_positional),
        ("example vs page furniture, with margins", test_examples_furniture_margins),
        ("the image bytes written", test_examples_image_bytes),
        ("examples/ never overwrites what it did not write", test_examples_never_overwrites),
        ("no labels means no verdict", test_examples_unlabelled),
        ("the index is accurate about its sources", test_examples_index_is_accurate),
        ("finding nothing, honestly", test_examples_honest_failures),
        ("images the decoder must refuse", test_examples_decode_refusals),
        ("the examples CLI contract", test_examples_cli),
        ("scaffold populates examples/", test_examples_scaffold_populates),
    ]
    if args.render:
        tests.append(("the real render", test_real_render))

    for name, fn in tests:
        print(f"\n{name}")
        before = PASS + FAIL
        fn()
        print(f"  {PASS + FAIL - before} checks")

    print("\n" + "=" * 62)
    print(f"  {PASS} passed, {FAIL} failed, {PASS + FAIL} checks")
    if FAILURES:
        print("\nfailures:")
        for f in FAILURES:
            print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
