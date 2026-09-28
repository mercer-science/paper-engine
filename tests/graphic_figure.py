#!/usr/bin/env python3
"""
tests/graphic_figure.py - PowerPoint panel art inside a figure folder.

Offline by default: the .pptx writer, the folder rules, the refusals, and the
staleness digest. `--render` adds the end-to-end pass that drives real
PowerPoint and composes a figure mixing graphic panels with a plotted one.

    python tests/graphic_figure.py
    python tests/graphic_figure.py --render
"""

import glob
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
TEMPLATE = os.path.join(TOOLS, "project_template")


def _engine(name, filename):
    """Load an engine by path under a distinct module name.

    tests/graphic_figure.py and tools/graphic_figure.py share a name, so a
    plain import resolves to whichever directory is first on sys.path - and
    inside this file, to this file. Same gotcha, same fix, as every other
    suite here. Do not "simplify" it back.
    """
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(TOOLS, filename))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {filename}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gf = _engine("_gf_engine", "graphic_figure.py")
sc = _engine("_sc_engine", "scaffold.py")

PASSED = FAILED = SKIPPED = 0


def section(title):
    print(f"\n--- {title} " + "-" * max(0, 58 - len(title)))


def check(label, ok, detail: object = ""):
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"[pass] {label}")
    else:
        FAILED += 1
        print(f"[FAIL] {label}")
    if detail and not ok:
        print(f"         {detail}")


def skip(label, why):
    global SKIPPED
    SKIPPED += 1
    print(f"[skip] {label} - {why}")


def new_project(**kw):
    root = tempfile.mkdtemp(prefix="gf_")
    proj = os.path.join(root, "Demo")
    values = sc.build_values(project_name="Demo", journal=kw.get("journal", "JACS"))
    sc.scaffold(proj, values)
    return root, proj


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def find_rscript():
    if shutil.which("Rscript"):
        return shutil.which("Rscript")
    for p in sorted(glob.glob(r"C:\Program Files\R\R-*\bin\Rscript.exe"),
                    reverse=True):
        return p
    return None


# ---------------------------------------------------------------------------
# A. Contracts with the engines it builds on
# ---------------------------------------------------------------------------

def test_contracts():
    section("contracts")

    # The folder grammar is scaffold.py's. Reused, not copied - a second copy
    # is the one nobody re-reads, and a float folder this engine could not
    # parse would be silently invisible to `add`.
    # Identity, not equality of behaviour: gf must be calling scaffold.py's
    # functions rather than defining lookalikes. (gf loads its own instance of
    # the module, so it is compared against THAT, not against this suite's.)
    check("the float folder grammar comes from scaffold.py, not a copy",
          gf.parse_float_dir is gf._scaffold.parse_float_dir
          and gf.list_float_dirs is gf._scaffold.list_float_dirs)
    names = ["Fig01", "Fig1", "Fig 1", "FigS01", "Table02",
             "Fig01_yield", "_template", "notes"]
    check("and it resolves every name identically to scaffold.py",
          [gf.parse_float_dir(n) for n in names]
          == [sc.parse_float_dir(n) for n in names])
    src = read(os.path.join(TOOLS, "graphic_figure.py"))
    check("it does not carry its own float-folder regex",
          "FLOAT_DIR_RE = re.compile" not in src,
          "reuse scaffold.py's rather than declaring a second one")

    # The .pptx writer is toc_graphic.py's, for the same reason.
    check("the .pptx writer comes from toc_graphic.py",
          gf._toc.write_pptx.__module__ == "_gf_toc")

    # The backend chain genuinely does exist twice - the TOC graphic lives in a
    # journal folder that may have no plan/ tree to source from. Two copies are
    # allowed; two copies that disagree are not.
    r_src = read(os.path.join(TEMPLATE, "plan", "theme", "render_pptx.R"))
    toc_src = read(os.path.join(TOOLS, "toc_graphic.py"))
    order = ["powerpoint-com", "libreoffice"]
    check("both copies of the backend chain try the same backends in order",
          [b for b in order if b in r_src] == order
          and [b for b in order if b in toc_src] == order)
    for probe, why in [
            ("POWERPNT.EXE", "the PowerPoint executable glob"),
            ("Slides.Item(1).Export", "the COM export call"),
            ("impress_png_Export", "the LibreOffice filter"),
            ("File > Save As", "the do-it-by-hand fallback")]:
        check(f"both copies carry {why}", probe in r_src and probe in toc_src)
    check("render_pptx.R says why the second copy exists",
          "toc_graphic.py carries a second copy" in r_src,
          "a deliberate duplicate has to say it is deliberate")

    # Staleness is a content hash on both sides, and they must be the same one.
    check("R and Python both answer staleness with sha256",
          "tools::sha256sum" in r_src and "hashlib.sha256" in src)
    # The prose in both files says "never mtime"; this checks the code does
    # not, which is a different claim. Comment lines are stripped first, or
    # the check passes on the strength of its own documentation.
    def code_only(text, comment):
        return "\n".join(l for l in text.splitlines()
                          if not l.lstrip().startswith(comment))
    check("neither side actually reads a modification time",
          not re.search(r"file[.]mtime|file[.]info\s*[(]",
                        code_only(r_src, "#"))
          and not re.search(r"getmtime|st_mtime", code_only(src, "#")),
          "OneDrive rewrites mtimes on sync - see CLAUDE.md")

    # render_all.R has to run panels.R before figure.R or the figure composes
    # last run's art, which looks entirely fine.
    ra = read(os.path.join(TEMPLATE, "plan", "render_all.R"))
    check("render_all.R forces figure.R/table.R last in a folder",
          'LAST_IN_FOLDER <- c("figure.R", "table.R")' in ra
          and "last <- basename(f) %in% LAST_IN_FOLDER" in ra)


# ---------------------------------------------------------------------------
# B. add
# ---------------------------------------------------------------------------

def test_add():
    section("add")
    root, proj = new_project()
    try:
        res = gf.add(proj, 1, panel="A")
        check("a panel source lands in the figure's own folder",
              res.get("pptx") == "plan/figures/Fig01/panelA_source.pptx"
              and os.path.isfile(os.path.join(proj, "plan", "figures", "Fig01",
                                              "panelA_source.pptx")), res)
        check("panels.R is written beside it",
              os.path.isfile(os.path.join(proj, "plan", "figures", "Fig01",
                                          "panels.R")))

        fig_r = os.path.join(proj, "plan", "figures", "Fig01", "figure.R")
        body = read(fig_r)
        # The slot held nothing but the untouched template, so writing a
        # starter costs nobody anything. This is asked BEFORE panels.R is
        # written - writing it first makes the folder permanently non-pristine
        # and the starter is then never written at all. That was a real bug.
        check("an untouched slot gets a starter figure.R that composes the panel",
              res["figure_written"] is True
              and 'graphic_panel("panelA_source.png"' in body, body[:200])
        # The starter's own comments mention image_panel() when explaining
        # placeholders, so only the CODE lines are checked - otherwise this
        # passes or fails on the documentation rather than on what runs.
        code = [l for l in body.splitlines() if not l.lstrip().startswith("#")]
        check("the starter uses graphic_panel(), not a bare image_panel()",
              any("graphic_panel(" in l for l in code)
              and not any("image_panel(" in l for l in code),
              "an aspect-locked image with a title inside it overlaps its "
              "neighbour - see helpers.R")

        # --- the .pptx itself -------------------------------------------
        pptx = os.path.join(proj, "plan", "figures", "Fig01",
                            "panelA_source.pptx")
        with zipfile.ZipFile(pptx) as z:
            names = set(z.namelist())
            pres = z.read("ppt/presentation.xml").decode("utf-8")
            slide = z.read("ppt/slides/slide1.xml").decode("utf-8")
        check("it is a real package PowerPoint will open",
              {"[Content_Types].xml", "ppt/presentation.xml",
               "ppt/slides/slide1.xml", "ppt/theme/theme1.xml"} <= names,
              sorted(names))
        m_cx = re.search(r'cx="(\d+)"', pres)
        m_cy = re.search(r'cy="(\d+)"', pres)
        assert m_cx is not None and m_cy is not None, "no slide size in the deck"
        cx, cy = int(m_cx.group(1)), int(m_cy.group(1))
        check("the slide is the size that was asked for",
              abs(cx / 914400 - res["width_in"]) < 0.01
              and abs(cy / 914400 - res["height_in"]) < 0.01,
              f"{cx / 914400:.3f} x {cy / 914400:.3f} in")
        # Nothing here knows what the panel shows, so every string a human has
        # to choose is a visible [TK] rather than an invented label.
        check("every string the engine could not know is a [TK]",
              slide.count("[TK") >= 3, slide.count("[TK"))

        # --- the pixel size is not claimed here --------------------------
        check("add does not predict a pixel size it does not decide",
              "px" not in res,
              "dpi is resolved by theme_journal.R plus a sourced "
              "journal_target.yml; a table here would be a second copy, and "
              "it was one - 999x749 claimed for a panel that rendered 1998x1499")

        # --- refusals -----------------------------------------------------
        again = gf.add(proj, 1, panel="A")
        check("an existing .pptx is never overwritten",
              again.get("error") and "yours" in again["error"], again)
        check("the .pptx on disk is untouched by the refused call",
              os.path.isfile(pptx))

        mixed = gf.add(proj, 1)
        check("a whole-figure source is refused once there are lettered panels",
              mixed.get("error") and "lettered panels" in mixed["error"], mixed)

        bad = gf.add(proj, 1, panel="AB")
        check("a panel that is not a single letter is refused",
              bad.get("error") and "single letter" in bad["error"], bad)

        gone = gf.add(proj, 9, panel="A")
        check("adding to a figure that does not exist says how to make one",
              gone.get("error") and "scaffold.py float new" in gone["error"],
              gone)

        # The named command has to make the float that was asked for. Told to
        # run `float new --figure` for a MISSING FigS01, you get Fig05 - a main
        # figure - and are no closer. An error that names the wrong command is
        # worse than one that names none.
        gone_s = gf.add(proj, 9, panel="A", supplementary=True)
        check("the command a missing supplementary figure names makes one",
              gone_s.get("error") and "--supplementary" in gone_s["error"],
              gone_s)

        # --- the whole-figure shape --------------------------------------
        whole = gf.add(proj, 2)
        check("a figure with no panels gets figure_source.pptx",
              whole.get("source") == "figure_source.pptx", whole)
        panel_after = gf.add(proj, 2, panel="A")
        check("a lettered panel is refused once the figure is one whole graphic",
              panel_after.get("error")
              and "whole-figure" in panel_after["error"], panel_after)

        # --- a folder that already holds work -----------------------------
        with open(os.path.join(proj, "plan", "figures", "Fig03", "figure.R"),
                  "w", encoding="utf-8") as fh:
            fh.write("# my own work\nsource(here::here('plan','setup.R'))\n")
        res3 = gf.add(proj, 3, panel="A")
        check("a figure.R somebody has written is never rewritten",
              res3["figure_written"] is False
              and "my own work" in read(os.path.join(
                  proj, "plan", "figures", "Fig03", "figure.R")),
              "the generator must not destroy the work it was called to help")
        check("and it says what line to add instead",
              "graphic_panel" in res3["compose_line"], res3["compose_line"])

        # --- dry run -------------------------------------------------------
        n_before = len(os.listdir(os.path.join(proj, "plan", "figures", "Fig04")))
        dry = gf.add(proj, 4, panel="A", dry_run=True)
        check("a dry run writes nothing",
              not dry.get("error")
              and len(os.listdir(os.path.join(proj, "plan", "figures",
                                              "Fig04"))) == n_before, dry)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# C. Discovery and staleness
# ---------------------------------------------------------------------------

def test_state():
    section("discovery and staleness")
    root, proj = new_project()
    try:
        gf.add(proj, 1, panel="A")
        gf.add(proj, 1, panel="B")
        d = os.path.join(proj, "plan", "figures", "Fig01")

        check("sources come back in panel order",
              gf.sources_in(d) == ["panelA_source.pptx", "panelB_source.pptx"],
              gf.sources_in(d))

        # PowerPoint writes ~$name.pptx beside a file it has open. Rendering
        # that lock stub yields a blank slide rather than an error, and a blank
        # panel looks like a panel.
        with open(os.path.join(d, "~$panelA_source.pptx"), "wb") as fh:
            fh.write(b"lock")
        check("PowerPoint's open-file lock stub is not a source",
              gf.sources_in(d) == ["panelA_source.pptx", "panelB_source.pptx"],
              gf.sources_in(d))

        st = gf.source_state(d, "panelA_source.pptx")
        check("an unrendered source is stale, not assumed good",
              st["stale"] is True and st["rendered"] is False, st)

        # Fake a render, then confirm the digest is what answers the question.
        with open(os.path.join(d, "panelA_source.png"), "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")
        digest = gf._sha256(os.path.join(d, "panelA_source.pptx"))
        with open(os.path.join(d, gf.STATE_FILE), "w", encoding="utf-8") as fh:
            json.dump({"panelA_source.pptx": {"sha256": digest,
                                              "png": "panelA_source.png",
                                              "backend": "powerpoint-com",
                                              "px": [999, 749]}}, fh)
        check("a recorded digest that matches is current",
              gf.source_state(d, "panelA_source.pptx")["stale"] is False)

        with open(os.path.join(d, "panelA_source.pptx"), "ab") as fh:
            fh.write(b"edited")
        check("editing the .pptx makes it stale",
              gf.source_state(d, "panelA_source.pptx")["stale"] is True,
              "the content decides, so an edit cannot be missed")

        res = gf.status(proj)
        check("status reports only folders that hold panel art",
              [f["label"] for f in res["floats"]] == ["Figure 1"], res["floats"])
        check("status counts what needs re-rendering",
              res["stale"] == 2, res["stale"])
        check("status reports the pixel size that was actually produced",
              res["floats"][0]["sources"][0]["px"] == [999, 749],
              "read back from the state file, never predicted")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# D. The .pptx is the user's, byte for byte
# ---------------------------------------------------------------------------

MARKER = "MARKER-a7f3-the-user-drew-this"


def mark_source(proj, path):
    """Stand in for the user opening the file in PowerPoint and drawing.

    A real .pptx, written by the engine's own writer, carrying a string that
    exists nowhere else - so "did this survive" is answered by bytes rather
    than by a timestamp.
    """
    gf._toc.write_pptx(path, gf.panel_spec(proj),
                       {"title": MARKER, "stages": [], "footnote": ""})
    return gf._sha256(path)


def test_never_overwritten():
    """The load-bearing promise of the whole PowerPoint route.

    The .pptx is the only file in a float folder nothing else can reproduce.
    Every other path here is asserted to leave it alone; `add` was already
    checked, but nothing pinned the generators that run *beside* it, and
    nothing pinned the render.
    """
    section("the .pptx is never overwritten")
    root, proj = new_project()
    try:
        gf.add(proj, 1, panel="A")
        d = os.path.join(proj, "plan", "figures", "Fig01")
        pptx = os.path.join(d, "panelA_source.pptx")
        mine = mark_source(proj, pptx)
        raw = open(pptx, "rb").read()
        # Prove the marker is PRESENT before asserting anything survives it -
        # a survival test over a file that never carried the marker passes
        # vacuously.
        with zipfile.ZipFile(pptx) as z:
            slide = z.read("ppt/slides/slide1.xml").decode("utf-8")
        check("the marker is in the .pptx to begin with", MARKER in slide)

        again = gf.add(proj, 1, panel="A")
        check("add refuses a second time", bool(again.get("error")), again)
        check("and the drawing is byte-identical afterwards",
              gf._sha256(pptx) == mine and open(pptx, "rb").read() == raw)

        # The generated files beside it are rewritten freely; the source is not.
        gf.write_panels_script(d)
        check("re-writing panels.R leaves the .pptx byte-identical",
              gf._sha256(pptx) == mine)

        gf.status(proj)
        gf.source_state(d, "panelA_source.pptx")
        check("reading the folder's state leaves the .pptx byte-identical",
              gf._sha256(pptx) == mine)

        # Render, with R deliberately out of reach: the failure path must not
        # touch the source either. The real render is asserted in test_pipeline.
        real = gf._toc._rscript
        gf._toc._rscript = lambda: None
        try:
            res = gf.render(proj, 1)
        finally:
            gf._toc._rscript = real
        check("a render that cannot find Rscript says so", bool(res.get("error")),
              res)
        check("and leaves the .pptx byte-identical", gf._sha256(pptx) == mine)

        with zipfile.ZipFile(pptx) as z:
            slide = z.read("ppt/slides/slide1.xml").decode("utf-8")
        check("the user's own drawing is still in there at the end",
              MARKER in slide)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# E. End to end through real PowerPoint and real R
# ---------------------------------------------------------------------------

MIXED_FIGURE = '''source(here::here("plan", "setup.R"))

p_a <- graphic_panel("panelA_source.png", "Scheme", "A")
p_b <- graphic_panel("panelB_source.png", "Apparatus", "B")

d <- load_data("data/raw/yield.csv")
p_c <- panel_title(
  ggplot(d, aes(x = temp_c, y = yield_pct, colour = catalyst, shape = catalyst)) +
    geom_point(size = 1.6) + geom_line() +
    labs(x = "Temperature (C)", y = "Yield (%)", colour = NULL, shape = NULL),
  title = "Yield", tag = "C")

save_float((p_a | p_b) / p_c, height = 5.2)
'''

YIELD_CSV = """catalyst,temp_c,yield_pct
A,60,41.2
A,80,48.9
A,100,52.1
B,60,79.4
B,80,88.2
B,100,91.0
"""


def test_pipeline(rscript):
    section("end to end - real PowerPoint, real R")
    root, proj = new_project()
    try:
        gf.add(proj, 1, panel="A")
        gf.add(proj, 1, panel="B")
        d = os.path.join(proj, "plan", "figures", "Fig01")
        # The offline half of this promise is test_never_overwritten(); this
        # is the half only a real render can answer.
        marked = mark_source(proj, os.path.join(d, "panelA_source.pptx"))
        os.makedirs(os.path.join(proj, "data", "raw"), exist_ok=True)
        with open(os.path.join(proj, "data", "raw", "yield.csv"), "w",
                  encoding="utf-8") as fh:
            fh.write(YIELD_CSV)
        with open(os.path.join(d, "figure.R"), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(MIXED_FIGURE)
        with open(os.path.join(proj, "plan", "captions.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("## Figure 1 \u2014 Fig01\n**A claim.**\n"
                     "(A) Scheme. (B) Apparatus. (C) Yield.\n")

        p = subprocess.run([rscript, os.path.join("plan", "render_all.R")],
                           cwd=proj, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=900)
        out = (p.stdout or "") + (p.stderr or "")
        check("a figure mixing PowerPoint art and a plot renders clean",
              p.returncode == 0 and "FAILED" not in out, out[-800:])
        check("panels.R ran before figure.R",
              out.index("Fig01/panels.R") < out.index("Fig01/figure.R"),
              "the art has to exist before the script that composes it reads "
              "it; sorted order alone puts figure.R first")
        check("both panel sources were rendered",
              os.path.isfile(os.path.join(d, "panelA_source.png"))
              and os.path.isfile(os.path.join(d, "panelB_source.png")))
        check("a real render leaves the user's .pptx byte-identical",
              gf._sha256(os.path.join(d, "panelA_source.pptx")) == marked,
              "PowerPoint opened it, exported a .png, and must have closed it "
              "without saving")
        check("the figure itself was written into its folder",
              os.path.isfile(os.path.join(d, "figure.png"))
              and os.path.isfile(os.path.join(d, "figure.pdf")))

        state = json.load(open(os.path.join(d, gf.STATE_FILE), encoding="utf-8"))
        check("the render recorded the digest R computed",
              state["panelA_source.pptx"]["sha256"]
              == gf._sha256(os.path.join(d, "panelA_source.pptx")),
              "R's tools::sha256sum and Python's hashlib must agree, or the "
              "two sides disagree about what is stale")
        check("nothing is stale immediately after a render",
              gf.status(proj)["stale"] == 0, gf.status(proj))

        # A second run must not pay for a PowerPoint launch per panel.
        p2 = subprocess.run([rscript, os.path.join("plan", "render_all.R")],
                            cwd=proj, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=900)
        out2 = (p2.stdout or "") + (p2.stderr or "")
        check("an unchanged source is not re-rendered",
              p2.returncode == 0 and "rendered panelA_source.png" not in out2,
              out2[-400:])

        prov = json.load(open(os.path.join(proj, "plan", "floats",
                                           "float_provenance.json"),
                              encoding="utf-8"))
        check("the float is recorded under its label",
              "Figure 1" in prov, list(prov))

        preview = read(os.path.join(proj, "plan", "preview.html"))
        check("the preview embeds the composed figure, not the panel art",
              preview.count("data:image/png;base64,") == 1,
              "panel sources are inputs to the figure, not floats themselves")

        # The placeholder path: art that has not been drawn yet must not stop
        # a render, because a figure is composed before it is finished.
        os.remove(os.path.join(d, "panelB_source.png"))
        os.remove(os.path.join(d, "panelB_source.pptx"))
        p3 = subprocess.run([rscript, os.path.join("plan", "render_all.R")],
                            cwd=proj, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=900)
        out3 = (p3.stdout or "") + (p3.stderr or "")
        check("a panel whose art does not exist yet renders as a placeholder",
              p3.returncode == 0 and "FAILED" not in out3, out3[-600:])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _geometry_slide(path, leader):
    """A two-label drawing with one leader line, drawn where `leader` says.

    `leader` is (x0, y0, x1, y1) in inches. The labels sit one above the
    other on the right, which is the layout the real defect had.
    """
    from pptx import Presentation
    from pptx.enum.shapes import MSO_CONNECTOR
    from pptx.util import Inches

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(6), Inches(4)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    for text, top in (("S-palmitoylation", 1.0), ("ubiquitination", 2.0)):
        box = slide.shapes.add_textbox(Inches(3.0), Inches(top),
                                       Inches(2.0), Inches(0.3))
        box.text_frame.text = text
    slide.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT, Inches(leader[0]), Inches(leader[1]),
        Inches(leader[2]), Inches(leader[3]))
    prs.save(path)


def test_geometry():
    """Item 103 - a drawn float's leaders and labels can overlap.

    Nothing anywhere checked that the slide's own elements do not collide. A
    leader line could run from a marker to its label straight through a
    DIFFERENT label's text and the render still succeeded: the .png was
    written, the staleness record was written, the float was composed into
    the manuscript, and no command reported anything. Measured on a real
    figure, where two leader lines put a vertical rule through the middle of
    the words S-palmitoylation and ubiquitination; the user caught it by
    looking at the picture.
    """
    section("geometry - does the drawing collide with itself (item 103)")
    if importlib.util.find_spec("pptx") is None:
        skip("geometry", "python-pptx is not installed")
        return

    root = tempfile.mkdtemp(prefix="gf_geom_")
    try:
        fdir = os.path.join(root, "plan", "figures", "Fig01")
        os.makedirs(fdir)
        src = os.path.join(fdir, "figure_source.pptx")

        # --- the defect, in its own geometry --------------------------
        _geometry_slide(src, (3.6, 3.5, 3.6, 1.15))
        res = gf.geometry(root)
        check("the check ran rather than reporting a slide it did not read",
              res["ran"] is True, res["note"])
        check("one collision, not none", len(res["collisions"]) == 1,
              res["collisions"])
        col = res["collisions"][0] if res["collisions"] else {}
        check("...named as a leader through a label",
              col.get("kind") == "leader_through_label", col)
        check("...naming BOTH elements, which is what makes it actionable",
              bool(col.get("a")) and "ubiquitination" in col.get("b", ""),
              col)

        # --- and the render refuses rather than writing the .png ------
        out = gf.render(root, 1)
        check("render refuses", bool(out.get("error")), out.get("error"))
        check("...and rendered nothing at all", out.get("rendered") == [],
              out.get("rendered"))
        check("...and the .png was never written",
              not os.path.exists(os.path.join(fdir, "figure_source.png")))
        check("...and the override is offered by name",
              "--allow-overlap" in (out.get("error") or ""))

        # --- a leader line touching its OWN label is not a collision ---
        # This is the false positive that would make the check worthless: a
        # leader is SUPPOSED to end on the thing it points at.
        _geometry_slide(src, (2.8, 3.5, 2.8, 1.15))
        clear = gf.geometry(root)
        check("a leader routed clear of the other label passes",
              clear["collisions"] == [], clear["collisions"])
        check("...and it read the slide to say so",
              clear["sources"][0]["labels"] == 2
              and clear["sources"][0]["lines"] == 1, clear["sources"])

        _geometry_slide(src, (3.6, 3.5, 3.6, 1.15))
        allowed = gf.render(root, 1, allow_overlap=True)
        check("--allow-overlap gets past the refusal",
              not allowed.get("error"), allowed.get("error"))
        check("...and the collision is still in the result rather than "
              "forgotten",
              len((allowed.get("geometry") or {}).get("collisions", [])) == 1)

        # --- two labels on top of each other ---------------------------
        from pptx import Presentation
        from pptx.util import Inches
        prs = Presentation()
        prs.slide_width, prs.slide_height = Inches(6), Inches(4)
        sl = prs.slides.add_slide(prs.slide_layouts[6])
        for text, left, top in (("S-palmitoylation", 3.0, 1.0),
                                ("ubiquitination", 3.05, 1.02)):
            box = sl.shapes.add_textbox(Inches(left), Inches(top),
                                        Inches(2.0), Inches(0.3))
            box.text_frame.text = text
        prs.save(src)
        stacked = gf.geometry(root)
        check("two labels on top of each other is a collision",
              len(stacked["collisions"]) == 1, stacked["collisions"])
        check("...named as such",
              (stacked["collisions"] or [{}])[0].get("kind")
              == "labels_overlap")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    want_render = "--render" in sys.argv
    print("Graphic figure suite - PowerPoint panel art in a float folder")

    test_contracts()
    test_add()
    test_state()
    test_never_overwritten()
    test_geometry()

    rscript = find_rscript()
    if not want_render:
        skip("end to end - real PowerPoint, real R", "pass --render to run it")
    elif not rscript:
        skip("end to end - real PowerPoint, real R",
             "Rscript not found on PATH or in Program Files")
    else:
        print(f"\nRscript: {rscript}")
        test_pipeline(rscript)

    print(f"\n{PASSED}/{PASSED + FAILED} passed"
          + (f", {FAILED} FAILED" if FAILED else "")
          + (f", {SKIPPED} skipped" if SKIPPED else ""))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
