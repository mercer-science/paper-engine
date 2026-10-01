#!/usr/bin/env python3
"""Measure idea-generation's engine: directory resolution, what it reports as
already recorded, the citation-integrity refusals, and whether a settled idea
actually populates a scaffolded project end to end.

Fourth suite, alongside reliability.py (citation verification), fulltext.py
(tiered reading), and scaffold.py (the project structure). This one covers
specs/idea-generation.md, and its acceptance criterion is step 3's: one topic
in, a populated project out - carried far enough that a figure script written
against the generated data contract renders through the real R pipeline.

The R section skips with a printed notice when Rscript is not installed.

Run:  python tests/idea.py
"""

import atexit
import copy
import glob
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

for _stream in (sys.stdout, sys.stderr):
    # A redirected stream (a pipe, a StringIO under a harness) has no
    # reconfigure at all; asking for it by name keeps that case quiet.
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except ValueError:
            pass

# This file and the engine share a name, so `import idea` would resolve by
# sys.path order - and inside this file, to this file. Same trap as
# tests/scaffold.py; same fix.
_HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(_HERE, "..", "tools")
BUNDLE_PATH = os.path.join(_HERE, "idea_bundle.json")


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(TOOLS, filename))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {filename}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


idea = _load("_idea_engine", "idea.py")
sc = _load("_scaffold_engine", "scaffold.py")

PASSED = 0
FAILED = 0
SKIPPED = 0


# ---------------------------------------------------------------------------
# A pinned environment, for the whole suite.
# ---------------------------------------------------------------------------
#
# `idea.py resources` now reports the drop-zone ladder alongside the lab's
# OneDrive folder (specs/lab-resource-pack.md 4.5), and two of those rungs are
# REAL per-user directories: `~/.paper-engine/resources/` and the toolkit's own
# `resources/`. Left alone, this suite reads whatever the person running it
# happens to have dropped in, which makes its result a property of the machine
# rather than of the engine.
#
# `tests/scaffold.py` carries the same six lines for the same reason, and the
# same reason it is copied rather than shared: every suite here is standalone
# and loads its engine by path. If this block changes, change that one.
_ENV_SANDBOX = tempfile.mkdtemp(prefix="idea_env_")
os.environ["PAPER_ENGINE_HOME"] = os.path.join(_ENV_SANDBOX, "home")
os.environ["PAPER_ENGINE_PLUGINS_DIR"] = os.path.join(_ENV_SANDBOX, "plugins")
os.environ["PAPER_ENGINE_CODEX_PLUGINS_DIR"] = os.path.join(_ENV_SANDBOX,
                                                            "codex", "plugins")
os.environ["PAPER_ENGINE_TOOLKIT"] = os.path.join(_ENV_SANDBOX, "toolkit")
for _k in ("PAPER_ENGINE_LAB_PACK", "PAPER_ENGINE_RESOURCES",
           "CLAUDE_PLUGIN_ROOT", "CODEX_HOME"):
    os.environ.pop(_k, None)
os.makedirs(os.path.join(_ENV_SANDBOX, "plugins"), exist_ok=True)
os.makedirs(os.path.join(_ENV_SANDBOX, "toolkit", "resources"), exist_ok=True)
atexit.register(shutil.rmtree, _ENV_SANDBOX, True)


def check(label, passed, detail: object = ""):
    global PASSED, FAILED
    if passed:
        PASSED += 1
    else:
        FAILED += 1
    print(f"[{'pass' if passed else 'FAIL'}] {label}")
    if detail:
        for line in str(detail).rstrip().splitlines():
            print(f"         {line}")
    return passed


def skip(label, why):
    global SKIPPED
    SKIPPED += 1
    print(f"[skip] {label}")
    print(f"         {why}")


def section(title):
    print(f"\n--- {title} " + "-" * max(0, 58 - len(title)))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def bundle():
    with open(BUNDLE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


FIG_SCRIPT = '''source(here::here("plan", "setup.R"))

# Figure 2 - array order by species

d <- load_data("data/mock_data/array_symmetry_mock.csv")

p <- ggplot(d, aes(x = species, y = symmetry_index,
                   colour = species, shape = species)) +
  geom_point(size = 1.6, position = position_jitter(width = 0.12)) +
  labs(x = NULL, y = "Symmetry index", colour = NULL, shape = NULL)

save_float(p)

message("fig02: n = ", nrow(d))
'''

TAB_SCRIPT = '''source(here::here("plan", "setup.R"))

# Table 1 - tomogram summary

d <- load_data("data/mock_data/array_symmetry_mock.csv")

tab <- d |>
  dplyr::group_by(species) |>
  dplyr::summarise(
    n = dplyr::n(),
    `Symmetry index` = sprintf("%.2f +/- %.2f", mean(symmetry_index), sd(symmetry_index)),
    `Spacing (nm)` = sprintf("%.1f +/- %.1f", mean(lattice_spacing_nm), sd(lattice_spacing_nm)),
    .groups = "drop")

save_table(style_float_ft(flextable::flextable(tab)))

message("tab01: ", nrow(tab), " rows")
'''

CAPTION_PROBE = '''source(here::here("plan", "setup.R"))
entries <- read_captions()
cat("PARSED=", length(entries), "\\n", sep = "")
for (e in entries) {
  cat("ENTRY|", e$label, "|", e$file, "|", e$subtitle, "\\n", sep = "")
}
'''


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_rscript():
    for name in ("Rscript", "Rscript.exe"):
        p = shutil.which(name)
        if p:
            return p
    for pattern in (r"C:\Program Files\R\*\bin\Rscript.exe",
                    r"C:\Program Files\R\*\bin\x64\Rscript.exe",
                    "/usr/lib/R/bin/Rscript", "/usr/local/bin/Rscript"):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return None


def read(root, rel):
    try:
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def make_lab():
    """A lab folder shaped like the real ones: <PI> - <Field>/ with Projects/,
    Resources/ and an SOP in it. Short prefix on purpose - the full artifact
    path has to stay under Windows' 260-character limit."""
    root = tempfile.mkdtemp(prefix="ig_")
    lab = os.path.join(root, "Jensen - CryoEm")
    os.makedirs(os.path.join(lab, "Projects"))
    sop = os.path.join(lab, "Resources", "Standard Operating Procedures")
    os.makedirs(sop)
    os.makedirs(os.path.join(lab, "Resources", "Literature"))
    with open(os.path.join(sop, "negative_stain.md"), "w", encoding="utf-8") as fh:
        fh.write("Negative stain screening: ~2 h per grid.\n")
    return root, lab


def scaffold_project(lab, name="array_symmetry", **kw):
    path = os.path.join(lab, "Projects", name)
    values = sc.build_values(project_name=name, field="biochemistry",
                             journal="Nature", **kw)
    sc.scaffold(path, values)
    return path


def csv_rows(root, rel):
    import csv as _csv
    with open(os.path.join(root, rel), encoding="utf-8", newline="") as fh:
        return list(_csv.DictReader(fh))


# ---------------------------------------------------------------------------
# A. Directory resolution - §1.1
# ---------------------------------------------------------------------------

def test_resolve():
    section("directory resolution")
    root, lab = make_lab()
    try:
        proj = scaffold_project(lab)

        r = idea.resolve(proj)
        check("an exact scaffolded path resolves to itself",
              r["status"] == "scaffolded" and r["project"].get("field") == "biochemistry",
              r["status"])

        r = idea.resolve(os.path.join(lab, "Projects"))
        check("naming the Projects/ parent offers the project inside it",
              r["status"] == "close_match"
              and [c["name"] for c in r["candidates"]] == ["array_symmetry"],
              r)

        r = idea.resolve(os.path.join(lab, "Projects", "array_symetry"))
        check("a typo resolves to the nearest scaffolded candidate",
              r["status"] == "close_match"
              and r["candidates"] and r["candidates"][0]["name"] == "array_symmetry"
              and r["candidates"][0]["scaffolded"],
              [c["name"] for c in r.get("candidates", [])])

        r = idea.resolve(os.path.join(lab, "Projects", "Array Symmetry"))
        check("a case-and-spacing difference still resolves",
              r["status"] == "close_match"
              and r["candidates"][0]["name"] == "array_symmetry",
              [c["name"] for c in r.get("candidates", [])])

        bare = os.path.join(lab, "Projects", "not_a_project")
        os.makedirs(bare)
        with open(os.path.join(bare, "notes.txt"), "w") as fh:
            fh.write("x")
        r = idea.resolve(bare)
        check("an existing unscaffolded directory says so rather than erroring",
              r["status"] == "unscaffolded" and r["populated"]
              and "setup-project-directory" in r["action"], r)

        r = idea.resolve(os.path.join(root, "totally_unrelated_xyz"))
        check("a path with nothing near it reports missing",
              r["status"] == "missing" and not r["candidates"], r)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# B. What is already recorded - so nothing gets re-asked
# ---------------------------------------------------------------------------

def test_context():
    section("reading what is already recorded")
    root, lab = make_lab()
    try:
        proj = scaffold_project(lab, title="Array symmetry")
        ctx = idea.context(proj)

        check("project.yml is read back",
              ctx["project"].get("field") == "biochemistry"
              and ctx["project"].get("target_journal") == "Nature",
              ctx["project"])

        # The scaffolded Study design and float-set sections are tables of empty
        # cells with row labels. Reporting those as written is what would make
        # the skill skip asking for a study design.
        written = [k for k, v in ctx["readme_sections"].items() if v]
        check("a freshly scaffolded README reports only Status as written",
              written == ["status"], f"reported written: {written}")
        check("every outline section reports as unwritten",
              not any(ctx["outline_sections"].values()), ctx["outline_sections"])

        text = read(proj, "plan/README.md").replace(
            "## Background\n", "## Background\n\nArrays are conserved.\n", 1)
        write(proj, "plan/README.md", text)
        ctx = idea.context(proj)
        check("a section someone has written is reported as written",
              ctx["readme_sections"].get("background") is True)

        lab_info = ctx["lab"]
        check("the lab folder is found by its Resources/ marker",
              os.path.basename(lab_info["lab"]) == "Jensen - CryoEm", lab_info["lab"])
        check("Resources/ is inventoried for the feasibility dialogue",
              "Standard Operating Procedures" in lab_info["subfolders"]
              and any("negative_stain" in f for f in lab_info["files"]),
              lab_info["subfolders"])
        check("the inventory carries names only, not file contents",
              not any("2 h per grid" in json.dumps(lab_info) for _ in [0]),
              "reading every SOP into context would defeat the point of an index")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# C. Refusals - §4 citation integrity, and structural integrity
# ---------------------------------------------------------------------------

def test_refusals():
    section("refusals")

    errors, warnings = idea.validate(bundle(), "project")
    check("the reference bundle validates clean", not errors, errors)

    b = bundle()
    b["gap"] += " A further study reports the opposite [PMID 99999999]."
    errors, _ = idea.validate(b, "project")
    check("a PMID that was never retrieved is refused",
          any("99999999" in e for e in errors), errors)

    b = bundle()
    b["literature"][0].pop("status")
    errors, _ = idea.validate(b, "project")
    check("literature that did not come through pubmed.py verify is refused",
          any("status" in e for e in errors), errors)

    b = bundle()
    b["literature"][0]["status"] = "not_found"
    errors, _ = idea.validate(b, "project")
    check("a not_found record is refused",
          any("not_found" in e for e in errors), errors)

    b = bundle()
    b["floats"][0]["folder"] = "Fig01 representative tomograms"
    errors, _ = idea.validate(b, "project")
    check("a float whose heading would not parse is refused",
          any("unparseable" in e for e in errors),
          "a space in the folder name makes the block invisible to "
          "read_captions()")

    b = bundle()
    b["floats"][0]["folder"] = "tomograms"
    errors, _ = idea.validate(b, "project")
    check("a folder name that is not a float folder is refused",
          any("not a float folder name" in e for e in errors),
          "the preview and the Word floats resolve a caption to its folder; "
          "one they cannot parse is invisible to both")

    b = bundle()
    check("a float set with no folders derives them from its labels",
          [idea.float_folder(f) for f in b["floats"]]
          == ["Fig01_representative_tomograms", "Fig02_symmetry_by_species",
              "Fig03_spacing_invariant", "Table01_tomogram_summary"],
          [idea.float_folder(f) for f in b["floats"]])

    b = bundle()
    b["floats"][0]["claim"] = ""
    errors, _ = idea.validate(b, "project")
    check("a float with no claim is refused",
          any("no claim" in e for e in errors), errors)

    b = bundle()
    b["floats"][1]["folder"] = idea.float_folder(b["floats"][0])
    errors, _ = idea.validate(b, "project")
    check("two floats claiming the same folder is refused",
          any("repeats the folder" in e for e in errors), errors)

    # item 82: HOW a float is made is a second question from what it shows,
    # and it was never asked - so a float the user asked for as a graphic got
    # a bare figure.R and was hand-drawn in annotate() calls, which renders
    # once and is then editable only by somebody who will edit R.
    b = bundle()
    b["floats"][0]["art"] = "drawn"
    errors, _ = idea.validate(b, "project")
    check("a float may say it is drawn rather than plotted", not errors,
          errors)
    b["floats"][0]["art"] = "hand-drawn"
    errors, _ = idea.validate(b, "project")
    check("...and a word outside the vocabulary is refused, by name",
          any("art 'hand-drawn'" in e for e in errors), errors)
    b = bundle()
    errors, _ = idea.validate(b, "project")
    check("...while saying nothing is still fine, and means plotted",
          not errors, errors)
    check("the art vocabulary is scaffold.py's own",
          list(idea.FLOAT_ART_KINDS) == list(sc.ART_KINDS),
          "duplicated on purpose - every engine here is standalone - and "
          "only safe while something compares the copies")
    check("...and so is the marker it writes into a drawn slot",
          idea.FLOAT_DRAWN_MARKER == sc.DRAWN_MARKER)

    b = bundle()
    b["data_files"][0]["columns"][2]["kind"] = "number"
    errors, _ = idea.validate(b, "project")
    check("an unrecognised column kind is refused",
          any("kind 'number'" in e for e in errors),
          "kind decides the geom and the test, so a wrong one is not cosmetic")

    b = bundle()
    b["data_files"][0]["group_column"] = "genus"
    errors, _ = idea.validate(b, "project")
    check("a group_column that is not a column is refused",
          any("group_column" in e for e in errors), errors)

    b = bundle()
    b["literature"] = b["literature"] * 4       # 12 papers
    errors, warnings = idea.validate(b, "project")
    check("an oversized literature set warns but does not refuse",
          not errors and any("literature" in w for w in warnings), warnings)

    b = bundle()
    b["literature"][0]["flags"] = ["RETRACTED PUBLICATION - do not cite."]
    errors, warnings = idea.validate(b, "project")
    check("a retraction flag is surfaced, and the paper is still writable "
          "with the flag shown",
          not errors and any("RETRACT" in w for w in warnings), warnings)


# ---------------------------------------------------------------------------
# D. Writing a settled idea into a scaffolded project
# ---------------------------------------------------------------------------

def test_write():
    section("populating a scaffolded project")
    root, lab = make_lab()
    try:
        proj = scaffold_project(lab)
        b = bundle()

        before = sorted(os.listdir(os.path.join(proj, "plan")))
        res = idea.write(proj, b, "project", dry_run=True)
        check("--dry-run writes nothing",
              not res["errors"] and res["actions"]
              and sorted(os.listdir(os.path.join(proj, "plan"))) == before
              and not os.path.exists(os.path.join(proj, "plan", "ideas.md")))

        res = idea.write(proj, b, "project", dry_run=False)
        check("the write reports no errors", not res["errors"], res["errors"])

        made = ["plan/ideas.md",
                "plan/relevant_literature/relevant_literature_summary.md",
                "data/templates/array_symmetry.csv",
                "data/mock_data/generate_mock_data.py",
                "data/mock_data/array_symmetry_mock.csv"]
        gaps = [m for m in made if not os.path.isfile(os.path.join(proj, m))]
        check("every artifact lands", not gaps, gaps)

        readme = read(proj, "plan/README.md")
        check("plan/README.md carries the gap, the hypothesis and the float set",
              "assumed rather than shown" in readme
              and "**Hypothesis:** Arrays in thermophiles" in readme
              and "Array order is higher in the thermophile" in readme,
              "background/gap/question/float-set all fill in one pass")
        check("the study-design table is filled, not left as empty cells",
              "| Design | Cross-species comparison" in readme
              and "**[FLAG: not decided]**" not in readme,
              [l for l in readme.splitlines() if l.startswith("| Design")])

        outline = read(proj, "plan/outline.md")
        check("every outline line carries an evidence bracket",
              all(re.search(r"\[[^\]]+\]\s*$", l)
                  for l in outline.splitlines()
                  if l.startswith("- ") and "ONE LINE" not in l),
              [l for l in outline.splitlines()
               if l.startswith("- ") and not re.search(r"\[[^\]]+\]\s*$", l)])
        check("the outline is a paragraph plan, not a section outline",
              outline.count("\n- ") == len(b["outline"]),
              f"{outline.count(chr(10) + '- ')} lines for "
              f"{len(b['outline'])} planned paragraphs")

        captions = read(proj, "plan/captions.md")
        headings = [l for l in captions.splitlines()
                    if idea.CAPTION_HEADING_RE.match(l)]
        check("every float gets a caption block the parser's grammar accepts",
              len(headings) == len(b["floats"]), headings)
        check("each block states its claim in bold and leaves the rest TODO",
              captions.count("**Array order is higher") == 1
              and captions.count("[TODO:") == len(b["floats"]),
              "the claim is settled at idea time; the panel keys are not")

        contract = read(proj, "data/data_contract.md")
        check("the data contract types every column",
              "| `symmetry_index` | continuous |" in contract
              and "| `species` | categorical |" in contract
              and "| `tomogram_id` | identifier |" in contract)
        check("the contract records which float consumes each column",
              "feeds Figure 2" in contract and "feeds Figure 3" in contract)
        check("the placeholder <file> section is replaced, not appended to",
              "<file>" not in contract,
              "the scaffolded placeholder should be consumed by the real table")

        header = read(proj, "data/templates/array_symmetry.csv").strip()
        cols = [c["name"] for c in b["data_files"][0]["columns"]]
        check("the header-only template matches the contract exactly",
              header == ",".join(cols), f"{header!r}")

        rows = csv_rows(proj, "data/mock_data/array_symmetry_mock.csv")
        thermo = [float(r["symmetry_index"]) for r in rows
                  if r["species"] == "thermophile"]
        meso = [float(r["symmetry_index"]) for r in rows
                if r["species"] == "mesophile"]
        check("the mock generator runs and produces both groups",
              len(thermo) == 12 and len(meso) == 12,
              f"{len(thermo)} thermophile, {len(meso)} mesophile rows")
        check("mock rows are hypothesis-shaped, not uniform noise",
              sum(thermo) / len(thermo) - sum(meso) / len(meso) > 0.05,
              f"thermophile mean {sum(thermo)/len(thermo):.3f} vs "
              f"mesophile {sum(meso)/len(meso):.3f} - the hypothesis says higher")
        spacing_t = [float(r["lattice_spacing_nm"]) for r in rows
                     if r["species"] == "thermophile"]
        spacing_m = [float(r["lattice_spacing_nm"]) for r in rows
                     if r["species"] == "mesophile"]
        check("the internal negative control shows no effect",
              abs(sum(spacing_t) / len(spacing_t)
                  - sum(spacing_m) / len(spacing_m)) < 0.5,
              "lattice spacing is specified as unchanged and must come out that way")
        check("a column the hypothesis said nothing about is left blank, "
              "not invented",
              all(r["defocus_um"] == "" for r in rows)
              and "array_symmetry.csv:defocus_um" in
              (res.get("report", {}).get("mock_unspecified") or []))

        gen = read(proj, "data/mock_data/generate_mock_data.py")
        check("the generator records the hypothesis it encodes",
              "more ordered in the thermophile" in gen
              and "internal negative control" in gen,
              "the numbers are only meaningful next to the claim they encode")

        yml = idea.read_project_yml(proj)
        check("project.yml gains the title",
              yml.get("title") == b["project"]["title"], yml.get("title"))

        ideas_md = read(proj, "plan/ideas.md")
        check("ideas.md records the dropped candidates and why",
              "why it was dropped" in ideas_md.lower()
              and "Only publishable if the correlation is strong" in ideas_md,
              "the rejected candidates are what stop the ground being re-covered")
        check("ideas.md records the 'worth finding out' unknowns verbatim",
              "Worth finding out" in ideas_md
              and "never grown in this lab" in ideas_md
              and "Unknown is not infeasible" in ideas_md)
        check("ideas.md records what was searched",
              "chemoreceptor array cryo-electron tomography" in ideas_md
              and "25 hits" in ideas_md,
              "report the search, not just the conclusions")

        summary = read(proj, "plan/relevant_literature/relevant_literature_summary.md")
        check("the literature summary carries PMIDs and why each paper is there",
              summary.count("PMID ") >= 3 and "That absence is the gap" in summary)
        check("a flagged paper carries its flag into the summary",
              "**FLAG:**" in summary and "Year mismatch" in summary,
              "a partial match must not be written as though it were clean")
        return root, lab, proj, res
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise


# ---------------------------------------------------------------------------
# E. Never destroying work
# ---------------------------------------------------------------------------

def test_non_destruction(proj):
    section("non-destruction")
    b = bundle()

    captions_before = read(proj, "plan/captions.md")
    res = idea.write(proj, b, "project", dry_run=False)
    captions_after = read(proj, "plan/captions.md")
    check("re-running does not duplicate caption blocks",
          captions_after.count("## Figure 2 —") == 1
          and captions_after == captions_before,
          f"{captions_after.count('## Figure 2 —')} blocks for Figure 2")
    check("an existing generate_mock_data.py is not regenerated",
          any(a["path"].endswith("generate_mock_data.py") and a["action"] == "skip"
              for a in res["actions"]),
          "it is the user's to edit once written")

    marker = "\n\nHAND-WRITTEN: the real narrative, decided at the bench.\n"
    text = read(proj, "plan/README.md")
    text = text.replace("## Narrative\n", "## Narrative\n" + marker, 1)
    write(proj, "plan/README.md", text)

    b2 = copy.deepcopy(b)
    b2["narrative"] = "A second, different narrative."
    idea.write(proj, b2, "project", dry_run=False)
    after = read(proj, "plan/README.md")
    check("a hand-written section is preserved and appended under, never replaced",
          "HAND-WRITTEN: the real narrative" in after
          and "A second, different narrative." in after
          and after.index("HAND-WRITTEN") < after.index("A second, different"),
          "the new block goes below the existing prose with a dated marker")
    check("the append is dated and attributed",
          idea.MARKER.format(idea.TODAY) in after)

    yml_before = idea.read_project_yml(proj)
    b3 = copy.deepcopy(b)
    b3["project"]["title"] = "A completely different title"
    res = idea.write(proj, b3, "project", dry_run=False)
    check("a bundle cannot overwrite a title project.yml already records",
          idea.read_project_yml(proj).get("title") == yml_before.get("title")
          and any("title" in i for i in
                  res["report"].get("project_yml_ignored", [])),
          res["report"].get("project_yml_ignored"))


# ---------------------------------------------------------------------------
# F. End to end through the real R pipeline - step 3's "done when"
# ---------------------------------------------------------------------------

def test_pipeline(rscript, proj):
    section("end to end through the R pipeline")

    probe = os.path.join(proj, "caption_probe.R")
    with open(probe, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(CAPTION_PROBE)
    p = subprocess.run([rscript, "caption_probe.R"], cwd=proj, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    parsed = re.search(r"PARSED=(\d+)", out)
    entries = re.findall(r"ENTRY\|([^|]*)\|([^|]*)\|([^\n]*)", out)
    check("read_captions() parses every block idea.py wrote",
          parsed and int(parsed.group(1)) == 4, out[:400])
    check("each parsed block carries its claim as the subtitle",
          any("Array order is higher in the thermophile" in e[2] for e in entries),
          [e[0] + " -> " + e[2][:50] for e in entries])
    os.remove(probe)

    # idea.write() made the folders when it settled the float set, so this
    # fills one in rather than creating it - which is the check that it did.
    fig2 = os.path.join(proj, "plan", "figures", "Fig02_symmetry_by_species")
    tab1 = os.path.join(proj, "plan", "tables", "Table01_tomogram_summary")
    check("the write created a folder for every float it named",
          os.path.isdir(fig2) and os.path.isdir(tab1)
          and os.path.isfile(os.path.join(fig2, "figure.R")),
          sorted(os.listdir(os.path.join(proj, "plan", "figures"))))
    write(proj, "plan/figures/Fig02_symmetry_by_species/figure.R", FIG_SCRIPT)
    write(proj, "plan/tables/Table01_tomogram_summary/table.R", TAB_SCRIPT)

    env = dict(os.environ)
    env.pop("FORCE_MOCK", None)
    p = subprocess.run([rscript, os.path.join("plan", "render_all.R")], cwd=proj,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    out = (p.stdout or "") + (p.stderr or "")
    check("a figure script written against the generated contract renders clean",
          p.returncode == 0 and "FAILED" not in out, out)

    preview = read(proj, "plan/preview.html")
    check("the preview carries the caption claim idea-generation wrote",
          "Array order is higher in the thermophile" in preview
          and "Acquisition parameters are matched" in preview)
    check("floats named in captions.md but not yet built are shown as pending",
          preview.count("has no rendered output") == 2,
          "Figures 1 and 3 are planned but unbuilt; the preview must say so "
          "rather than omitting them")

    # The safeguard chain, armed by idea-generation's own output: the figure
    # reads data/mock_data/, so everything downstream must know it.
    prov = json.loads(read(proj, "plan/floats/float_provenance.json"))
    # Keyed by the float's LABEL, not by a file stem: under the folder layout
    # every figure writes "figure.png", so stems collide across floats.
    check("mock provenance is recorded for a float built from generated rows",
          prov.get("Figure 2", {}).get("mock") is True, prov)
    check("the preview banners the mock data",
          "MOCK DATA" in preview and "Nothing here is a result" in preview)
    check("the co-author .docx is refused, because the data is not real yet",
          "REFUSED" in out and "NOT WRITTEN" in out,
          "this is the whole point of §5.5's third safeguard")


# ---------------------------------------------------------------------------
# G. Explore-only mode
# ---------------------------------------------------------------------------

def test_explore():
    section("explore-only mode")
    root = tempfile.mkdtemp(prefix="ig_")
    try:
        folder = os.path.join(root, "Array Symmetry")
        os.makedirs(folder)
        res = idea.write(folder, bundle(), "explore", dry_run=False)
        check("explore mode writes the record and the literature",
              not res["errors"]
              and os.path.isfile(os.path.join(folder, "ideas.md"))
              and os.path.isfile(os.path.join(
                  folder, "relevant_literature_summary.md")), res["errors"])
        check("explore mode creates no project tree",
              not os.path.exists(os.path.join(folder, "plan"))
              and not os.path.exists(os.path.join(folder, "data")),
              sorted(os.listdir(folder)))
        check("the record is the same one project mode writes",
              "Worth finding out" in read(folder, "ideas.md")
              and "why it was dropped" in read(folder, "ideas.md").lower())
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# F. The standalone mock generator
# ---------------------------------------------------------------------------
# `write` produces the generator from a settled idea bundle. `mock` produces
# the same generator from the data_files half alone, because
# setup-project-directory needs rows on day one and the hypothesis is not
# settled yet. One engine, two doors: what is checked here is that the second
# door reaches the same generator and honours the same output contract.

MOCK_ONLY_BUNDLE = {
    "mock": {"hypothesis": "Symmetry differs between species.",
             "n": 10, "seed": 3},
    "data_files": [{
        "file": "arrays",
        "group_column": "species",
        "columns": [
            {"name": "species", "kind": "categorical",
             "levels": ["coli", "subtilis"]},
            {"name": "symmetry_order", "kind": "count",
             "mock": {"coli": 6, "subtilis": 4}},
            {"name": "diameter_nm", "kind": "continuous",
             "mock": {"coli": [22, 2], "subtilis": [31, 3]}}]}]}


def test_mock_only():
    section("the standalone mock generator")

    root, lab = make_lab()
    try:
        proj = scaffold_project(lab, name="mock_only")

        res = idea.mock_only(proj, MOCK_ONLY_BUNDLE)
        check("the generator is written and run",
              res["action"] == "create" and not res["errors"]
              and res.get("mock", {}).get("ran"), res.get("errors") or res)

        rel = "data/mock_data/generate_mock_data.py"
        check("it lands where write() puts it",
              os.path.isfile(os.path.join(proj, rel)))

        # The _mock suffix is the whole safety mechanism: save_float() keys its
        # watermark on the path, and create_floats.R keys its refusal on the
        # same. A generator that wrote arrays.csv would be silently unsafe.
        files = res["mock"]["files"]
        check("every CSV carries the _mock suffix that arms the safeguards",
              files == ["arrays_mock.csv"], files)

        rows = csv_rows(proj, "data/mock_data/arrays_mock.csv")
        check("the rows are hypothesis-shaped, not uniform noise",
              len(rows) == 20
              and float(rows[0]["diameter_nm"]) != float(rows[-1]["diameter_nm"]),
              f"{len(rows)} rows")
        coli = [float(r["diameter_nm"]) for r in rows if r["species"] == "coli"]
        sub = [float(r["diameter_nm"]) for r in rows
               if r["species"] == "subtilis"]
        check("the group means separate the way the bundle asked",
              coli and sub and sum(coli) / len(coli) < sum(sub) / len(sub),
              f"coli={sum(coli)/len(coli):.1f} subtilis={sum(sub)/len(sub):.1f}")

        # Never overwrite: once the generator exists it has been edited, and
        # the edits ARE the hypothesis.
        again = idea.mock_only(proj, MOCK_ONLY_BUNDLE)
        check("a re-run refuses rather than overwriting",
              again["action"] == "skip" and not again["errors"], again)

        with open(os.path.join(proj, rel), "a", encoding="utf-8") as fh:
            fh.write("\n# my edit\n")
        idea.mock_only(proj, MOCK_ONLY_BUNDLE)
        with open(os.path.join(proj, rel), encoding="utf-8") as fh:
            check("a hand edit survives a re-run", "# my edit" in fh.read())

        forced = idea.mock_only(proj, MOCK_ONLY_BUNDLE, force=True)
        check("--force overwrites deliberately",
              forced["action"] == "overwrite", forced["action"])

        # A bundle with nothing to generate from is an error, not an empty run.
        empty = idea.mock_only(proj, {"data_files": []})
        check("a bundle with no data_files is refused",
              empty["errors"] and empty["action"] == "", empty["errors"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_mock_only_cli():
    section("the mock generator as a CLI")

    root, lab = make_lab()
    try:
        proj = scaffold_project(lab, name="mock_cli")
        bundle = os.path.join(root, "bundle.json")
        with open(bundle, "w", encoding="utf-8") as fh:
            json.dump(MOCK_ONLY_BUNDLE, fh)

        engine = os.path.join(TOOLS, "idea.py")
        proc = subprocess.run([sys.executable, engine, "mock", proj,
                               "--bundle", bundle, "--json"],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        check("`idea.py mock` exits 0", proc.returncode == 0,
              (proc.stderr or "")[:300])
        try:
            got = json.loads(proc.stdout)
        except json.JSONDecodeError:
            check("--json emits JSON", False, proc.stdout[:200])
            return
        check("--json emits the same report the function returns",
              got.get("action") == "create" and got.get("mock", {}).get("ran"),
              got)

        # --dry-run has to write nothing at all, or a caller cannot use it to
        # preview against a folder that already has work in it.
        proj2 = scaffold_project(lab, name="mock_dry")
        proc = subprocess.run([sys.executable, engine, "mock", proj2,
                               "--bundle", bundle, "--dry-run", "--json"],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        check("--dry-run writes nothing",
              proc.returncode == 0
              and not os.path.exists(os.path.join(
                  proj2, "data", "mock_data", "generate_mock_data.py")),
              (proc.stderr or "")[:200])
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# G. The Ideas/ folder - specs/idea-generation.md 15
# ---------------------------------------------------------------------------
# The one worth writing first is the last check in this section: an explore
# write whose summary claims no file the write did not produce. It was false
# for the whole life of the file - the header said the PDFs "are in this
# folder, and refs.bib covers the same set" and NEITHER mode wrote a refs.bib
# at all - and a record that names a file which does not exist is the most
# corrosive kind of wrong to leave, because the next reader assumes the
# citations were captured somewhere.

def test_ideas_folder():
    section("the Ideas/ folder - summary.md, refs.bib, and the caps")

    root = tempfile.mkdtemp(prefix="ig_")
    try:
        folder = os.path.join(root, "Array Symmetry")
        os.makedirs(folder)
        b = bundle()
        b["query_name"] = "Array symmetry across species"
        b["takeaway"] = ("Nobody has compared array spacing across species. "
                         "A four-species tomography series would answer it. "
                         "The datasets are collectable on our own scope.")
        res = idea.write(folder, b, "explore", dry_run=False)
        check("the explore write succeeds", not res["errors"], res["errors"])

        wrote = sorted(os.listdir(folder))
        check("summary.md is written", "summary.md" in wrote, wrote)
        check("refs.bib is written", "refs.bib" in wrote, wrote)
        check("ideas.md is still written", "ideas.md" in wrote, wrote)

        summ = read(folder, "summary.md")
        check("summary.md leads with the folder's name",
              summ.startswith("# Array symmetry across species"),
              summ.splitlines()[:1])
        check("summary.md carries the takeaway",
              "four-species tomography series" in summ)
        check("summary.md has the three sections 15.2 asks for",
              "## Guiding papers" in summ and "## Citations" in summ)
        check("every retrieved paper has a block",
              all((p.get("why") or "")[:24] in summ for p in b["literature"]),
              [p.get("pmid") or p.get("doi") for p in b["literature"]])
        check("the citation list carries the identifier each paper has",
              all((f"PMID {p['pmid']}" in summ if p.get("pmid")
                   else f"doi:{p['doi']}" in summ)
                  for p in b["literature"]))
        check("summary.md does not duplicate ideas.md",
              "Worth finding out" not in summ and "dropped" not in summ,
              "the candidates and the searches stay in ideas.md")

        bib = read(folder, "refs.bib")
        check("refs.bib holds one entry per retrieved paper",
              bib.count("@article{") == len(b["literature"]),
              bib.count("@article{"))
        check("refs.bib citekeys are unique",
              len(set(re.findall(r"@article\{([^,]+),", bib)))
              == bib.count("@article{"),
              re.findall(r"@article\{([^,]+),", bib))
        check("refs.bib is ASCII, as tests/cite.py requires of a .bib",
              all(ord(ch) < 128 for ch in bib))
        check("refs.bib says it is generated",
              bib.lstrip().startswith("%"), bib.splitlines()[:1])

        # THE ONE THAT MATTERS: every file the summary names must exist.
        lit = read(folder, "relevant_literature_summary.md")
        named = set(re.findall(r"`([A-Za-z0-9_.-]+\.(?:bib|md|csv|pdf))`",
                               lit + summ))
        missing = sorted(n for n in named
                         if not os.path.exists(os.path.join(folder, n)))
        check("the summary names no file the write did not produce",
              missing == [],
              missing or "specs/idea-generation.md 15.1; item 52")
        check("and it does claim the .bib, now that one is written",
              "refs.bib" in named, sorted(named))
        check("explore mode promises no metadata stubs (15.4)",
              "metadata stub" not in lit)

        # The header must be honest when the .bib could NOT be written.
        no_bib = idea.render_literature_summary(b, "explore", has_bib=False)
        check("with no .bib the header does not mention one",
              "refs.bib" not in no_bib)
        check("the default is the safe direction",
              "refs.bib" not in idea.render_literature_summary(b, "explore"),
              "a caller that forgets to say produces a summary that "
              "under-claims")
        proj_bib = idea.render_literature_summary(b, "project", has_bib=True)
        check("project mode names its own folder",
              "plan/relevant_literature/" in proj_bib)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_sentence_caps():
    section("the 3-4 sentence caps - reported, never refused")

    one = "Only one sentence here."
    two = "First sentence. Second sentence."
    four = "One. Two things happened. Three followed. Four closed it."
    six = " ".join("Sentence number %d ran." % i for i in range(1, 7))
    seven = " ".join("Sentence number %d ran." % i for i in range(1, 8))

    check("one sentence warns", bool(idea._sentence_warnings("t", one)))
    check("two sentences pass", idea._sentence_warnings("t", two) == [])
    check("four sentences pass", idea._sentence_warnings("t", four) == [])
    check("six sentences pass", idea._sentence_warnings("t", six) == [])
    check("seven sentences warn", bool(idea._sentence_warnings("t", seven)),
          idea._sentence_warnings("t", seven))
    check("empty warns, and says what the cap is",
          "3-4" in " ".join(idea._sentence_warnings("t", "")),
          idea._sentence_warnings("t", ""))
    check("a warning names what it is about",
          "the takeaway" in " ".join(
              idea._sentence_warnings("the takeaway", one)))
    check("`et al.` does not end a sentence",
          idea._sentence_count("Briegel et al. showed it. So did we.") == 2,
          "the splitter is prose.py's, not a fourth copy of the "
          "abbreviation list")

    root = tempfile.mkdtemp(prefix="ig_")
    try:
        folder = os.path.join(root, "Short")
        os.makedirs(folder)
        b = bundle()
        b["takeaway"] = one
        res = idea.write(folder, b, "explore", dry_run=True)
        check("a one-sentence takeaway is a warning, not an error",
              not res["errors"]
              and any("takeaway" in w for w in res["warnings"]),
              res["warnings"])
        check("and the write still happens",
              idea.write(folder, b, "explore", dry_run=False)["wrote"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# H. merge - destination 2, specs/idea-generation.md 12
# ---------------------------------------------------------------------------
# Five cases, and they are written first because they are the destructive
# ones. Every rule in `merge` exists because a merge that overwrote one of
# these files would destroy work that cannot be recovered from the folder.

def merge_bundle():
    b = bundle()
    b["query_name"] = "Array symmetry across species"
    b["methods_proposed"] = {
        "acquisition": {
            "total_dose_e_per_A2": {
                "value": 50,
                "source": "PMID %s - Methods" % b["literature"][0]["pmid"],
                "why": "Dose used for the closest published tomography"}},
        "sample_prep": {
            "glow_discharge": {
                "value": "25 mA, 30 s, air",
                "source": "Resources/Standard Operating Procedures/"
                          "negative_stain.md",
                "why": "The lab's own documented setting"}}}
    return b


def _merge_kinds(res):
    return sorted({a["action"] for a in res["actions"]})


def test_merge_additive():
    section("merge - nothing is overwritten, and that is enforced")

    root, lab = make_lab()
    try:
        proj = scaffold_project(lab, name="merge_into")
        b = merge_bundle()

        res = idea.merge(proj, b, dry_run=True)
        check("a merge is a dry run by default at the API too",
              res["dry_run"] and not res["wrote"])
        check("the dry run wrote nothing",
              not os.path.exists(os.path.join(proj, "data",
                                              "methods_proposed.yml")))
        check("every action is additive",
              all(k in idea.MERGE_KINDS for k in _merge_kinds(res)),
              _merge_kinds(res))
        check("there is no MODIFY row",
              [k for k in _merge_kinds(res)
               if k not in ("add", "append", "new", "propose",
                            "skip")] == [])

        res = idea.merge(proj, b, dry_run=False)
        check("the applied merge succeeded", not res["errors"], res["errors"])
        wrote_paths = [a["path"] for a in res["actions"]]
        check("the guiding papers went into plan/",
              os.path.isfile(os.path.join(
                  proj, "plan", "relevant_literature", "refs.bib")))
        check("and the merge touched nothing under drafts/",
              not [x for x in wrote_paths if x.startswith("drafts/")],
              "12.3: the submission bibliography is derived from what the "
              "manuscript cites and drops the rest, so a motivating paper "
              "written into it is either dropped or pushed into a paper that "
              "does not cite it")
        check("the merge names the check-refs it does not run",
              any("check-refs" in n for n in res.get("next") or []),
              res.get("next"))
        readme = read(proj, "plan/README.md")
        check("the takeaway was appended to plan/README.md",
              "## Idea, " in readme and "**The claim.**" in readme)
        check("the scaffolded README survived the append",
              "## Status" in readme or "## The question" in readme,
              "5.6: appends if the file has content; never overwrites")
        ideas_md = read(proj, "plan/ideas.md")
        check("the rejected candidates went to plan/ideas.md",
              "## Idea, " in ideas_md)
        check("proposed methods is a new file",
              os.path.isfile(os.path.join(proj, "data",
                                          "methods_proposed.yml")))
        prop = read(proj, "data/methods_proposed.yml")
        check("and it says nothing in it reaches a draft",
              "methods_facts.yml" in prop and "PROPOSED" in prop)
        check("every proposed entry carries its source",
              prop.count("source:") == 2, prop.count("source:"))

        # A second merge has to be legible rather than mysterious (12.3).
        res2 = idea.merge(proj, b, dry_run=False)
        check("a second merge adds no duplicate paper",
              len(res2.get("already_present") or []) == len(b["literature"]),
              [p["why"] for p in res2.get("already_present") or []])
        check("and says so rather than silently skipping",
              all("already present" in p["why"]
                  for p in res2["already_present"]))
        bib = read(proj, "plan/relevant_literature/refs.bib")
        check("refs.bib holds each paper once",
              bib.count("@article{") == len(b["literature"]),
              bib.count("@article{"))
        summary = read(proj,
                       "plan/relevant_literature/"
                       "relevant_literature_summary.md")
        check("the guiding-paper region carries the date and the idea",
              "idea-generation: " in summary
              and "Array symmetry across species" in summary,
              "an unmarked append becomes indistinguishable from the "
              "original set")
        # The skip is only reachable while the plan half is still being
        # applied - once the outline has lines, everything deferrable goes to
        # the proposal note instead. So it gets its own project.
        other = scaffold_project(lab, name="already_proposed")
        os.makedirs(os.path.join(other, "data"), exist_ok=True)
        with io.open(os.path.join(other, "data", "methods_proposed.yml"),
                     "w", encoding="utf-8") as fh:
            fh.write("# hand-written, and not to be regenerated over\n")
        res3 = idea.merge(other, b, dry_run=True)
        check("an existing methods_proposed.yml is skipped, not regenerated",
              any(a["action"] == "skip"
                  and a["path"].endswith("methods_proposed.yml")
                  for a in res3["actions"]),
              [(a["action"], a["path"]) for a in res3["actions"]])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_merge_settled_outline():
    section("merge - a settled outline is proposed against, never rewritten")

    root, lab = make_lab()
    try:
        proj = scaffold_project(lab, name="settled")
        b = merge_bundle()

        # Empty outline: the proposal IS the outline (12.5's empty case).
        res = idea.merge(proj, b, dry_run=False)
        outline = read(proj, "plan/outline.md")
        check("an empty outline is filled in place",
              res["outline"]["path"] == "plan/outline.md", res["outline"])
        check("with the PROPOSED banner on it",
              idea.prose.OUTLINE_PROPOSED_MARK in outline)
        check("and the template's own region rules survive",
              "# Notes" in outline,
              "the `# Notes` guarantee is written nowhere else")
        check("the proposed lines are there",
              any(idea.prose.OUTLINE_LINE_RE.match(ln)
                  for ln in outline.splitlines()))

        # Now it has lines. A second merge must not touch it.
        before = read(proj, "plan/outline.md")
        res2 = idea.merge(proj, b, dry_run=False)
        check("a settled outline is not modified",
              read(proj, "plan/outline.md") == before)
        check("the proposal goes beside it",
              res2["outline"]["path"].startswith("plan/idea_proposal_"),
              res2["outline"])
        note = read(proj, res2["outline"]["path"])
        check("the note says it was applied nowhere",
              "not applied anywhere" in note)
        check("and gives the command that would apply it",
              "manuscript.py outline" in note)
        check("and explains why it is beside the file",
              "file-wide" in note,
              "prose.py reads the proposed banner file-wide, so appending "
              "would mislabel a settled outline")
        check("the settled outline is still the user's, not proposed twice",
              read(proj, "plan/outline.md").count(
                  idea.prose.OUTLINE_PROPOSED_MARK) == 1)

        # Captions: a number that would have to be inserted is reported.
        b2 = merge_bundle()
        b2["floats"] = [{"label": "Figure 1", "slug": "early",
                         "claim": "An early figure.", "description": "x"}]
        res3 = idea.merge(proj, b2, dry_run=True)
        left = " ".join((res3.get("captions") or {}).get("left_alone") or [])
        check("a caption that already exists is left alone",
              "untouched" in left or "out of manuscript order" in left, left)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_merge_drafted_project():
    section("merge - the hard gate: a project that is being drafted")

    root, lab = make_lab()
    try:
        proj = scaffold_project(lab, name="drafted")
        b = merge_bundle()
        idea.merge(proj, b, dry_run=False)          # first, additive merge

        jdir = os.path.join(proj, "drafts", "LANGMUIR")
        os.makedirs(jdir, exist_ok=True)
        with open(os.path.join(jdir, "manuscript_r2.docx"), "wb") as fh:
            fh.write(b"not really a docx")

        check("the revision is found",
              idea.manuscript_revisions(proj)
              == ["drafts/LANGMUIR/manuscript_r2.docx"],
              idea.manuscript_revisions(proj))

        outline_before = read(proj, "plan/outline.md")
        captions_before = read(proj, "plan/captions.md")

        b2 = merge_bundle()
        b2["query_name"] = "A second idea"
        # The literature stays - the bundle's own prose cites those PMIDs, and
        # validate() correctly refuses a bundle that cites what it does not
        # carry. One new paper is added so the additive half has something to
        # do.
        b2["literature"] = b2["literature"] + [{
            "pmid": "31111111", "title": "A paper not yet in this project",
            "authors": ["New A"], "journal_abbrev": "J New", "year": "2021",
            "status": "verified", "flags": [],
            "why": "New to the project. It is here to prove the additive half "
                   "still runs on a drafted paper. Nothing more."}]
        res = idea.merge(proj, b2, dry_run=False, cap_ack=True)

        check("the merge still runs", not res["errors"], res["errors"])
        check("and says the project is being drafted",
              any("being drafted" in n for n in res["notes"]), res["notes"])
        check("it names the revision it found",
              any("manuscript_r2.docx" in n for n in res["notes"]))

        paths = [a["path"] for a in res["actions"]]
        check("the outline was NOT written",
              read(proj, "plan/outline.md") == outline_before)
        check("the captions were NOT written",
              read(proj, "plan/captions.md") == captions_before)
        check("plan/outline.md is not in the action list at all",
              "plan/outline.md" not in paths, paths)
        check("nor plan/captions.md", "plan/captions.md" not in paths)

        check("the guiding papers still went in",
              any(x.endswith("refs.bib") for x in paths), paths)
        check("the takeaway still went in",
              "plan/README.md" in paths, paths)
        check("everything deferred is in one note",
              res.get("proposal_note", "").startswith("plan/idea_proposal_"),
              res.get("proposal_note"))
        note = read(proj, res["proposal_note"])
        check("the note carries the outline it did not write",
              "The outline this idea implies" in note)
        check("and the methods it did not write",
              "NOT written to" in note and "methods_proposed.yml" in note)
        check("and says why, naming the revision",
              "manuscript_r2.docx" in note)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_merge_cap_and_dedup():
    section("merge - the cap is enforced by asking, and dedup reuses "
            "pubmed.py")

    root, lab = make_lab()
    try:
        proj = scaffold_project(lab, name="capped")
        b = merge_bundle()
        idea.merge(proj, b, dry_run=False)

        big = merge_bundle()
        big["literature"] = b["literature"] + [
            {"pmid": str(30000000 + i), "title": "Filler paper number %d" % i,
             "authors": ["Filler %d" % i], "journal_abbrev": "J Test",
             "year": "2020", "status": "verified", "flags": [],
             "why": "Filler. It exists to push the count past the cap. "
                    "Nothing else."}
            for i in range(7)]
        res = idea.merge(proj, big, dry_run=False)
        check("past the cap, nothing is written",
              res["actions"] == [], res["actions"])
        check("and it is blocked rather than warned",
              bool(res["blocked"]), res["blocked"])
        check("the block names the combined count",
              res["guiding_paper_count"]["combined"] > 8,
              res["guiding_paper_count"])
        check("and says how to answer it",
              any("--cap-ack" in x for x in res["blocked"]))
        check("nothing was dropped silently",
              all("silently" in x for x in res["blocked"]))

        res = idea.merge(proj, big, dry_run=False, cap_ack=True)
        check("with the user's answer it proceeds",
              not res["blocked"] and bool(res["actions"]))
        check("and records that the cap was passed on purpose",
              any("--cap-ack" in w for w in res["warnings"]), res["warnings"])

        # 12.3: matched on DOI, then PMID, then normalized title - and the
        # matcher is pubmed.py's, not a third one written here.
        # The bundle keeps its own literature - its prose cites those
        # PMIDs, and validate() refuses a bundle that cites what it does not
        # carry. The duplicate is ADDED, carrying no DOI and no PMID, so the
        # only thing that can catch it is the title.
        title_only = merge_bundle()
        first = title_only["literature"][0]
        title_only["literature"] = title_only["literature"] + [{
            "doi": "", "pmid": "",
            "arxiv": "0000.00001",
            "title": first["title"].upper() + ".",
            "authors": first.get("authors") or ["X"],
            "journal_abbrev": "J Other", "year": first.get("year", "2012"),
            "status": "verified", "flags": [],
            "why": "The same paper in a different title form. It must be "
                   "recognised as already present. Nothing else."}]
        res = idea.merge(proj, title_only, dry_run=True, cap_ack=True)
        present = " ".join(p["why"] for p in res.get("already_present") or [])
        check("a paper already present under a different title form is caught",
              "already present by title" in present, present)
        check("and the matcher came from pubmed.py",
              bool(idea._title_matcher()),
              "12.3: writing a third title matcher is how three matchers "
              "disagree")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_merge_refusals():
    section("merge - what it refuses, and the proposed-methods source rule")

    root, lab = make_lab()
    try:
        bare = os.path.join(lab, "Projects", "not_a_project")
        os.makedirs(bare)
        res = idea.merge(bare, merge_bundle(), dry_run=True)
        check("an unscaffolded folder is refused",
              any("not a scaffolded project" in e for e in res["errors"]),
              res["errors"])
        check("and it says which command to use instead",
              any("setup-project-directory" in e for e in res["errors"]))

        proj = scaffold_project(lab, name="sources")

        no_src = merge_bundle()
        no_src["methods_proposed"]["acquisition"][
            "total_dose_e_per_A2"].pop("source")
        res = idea.merge(proj, no_src, dry_run=True)
        check("a proposed value with no source is refused",
              any("has no source" in e for e in res["errors"]), res["errors"])
        check("and the refusal says why",
              any("it is a guess" in e for e in res["errors"]))
        check("nothing was written",
              res["actions"] == [], res["actions"])

        bad_src = merge_bundle()
        bad_src["methods_proposed"]["acquisition"][
            "total_dose_e_per_A2"]["source"] = "I remember reading it"
        res = idea.merge(proj, bad_src, dry_run=True)
        check("a source that is neither kind is refused",
              any("neither kind" in e for e in res["errors"]), res["errors"])
        check("and the refusal names both legal kinds",
              any("Resources/" in e and "section it came from" in e
                  for e in res["errors"]))

        bare_cite = merge_bundle()
        bare_cite["methods_proposed"]["acquisition"][
            "total_dose_e_per_A2"]["source"] = "PMID 22267509"
        res = idea.merge(proj, bare_cite, dry_run=True)
        check("a citation with no section is refused",
              any("neither kind" in e for e in res["errors"]), res["errors"])

        unretrieved = merge_bundle()
        unretrieved["methods_proposed"]["acquisition"][
            "total_dose_e_per_A2"]["source"] = "PMID 99999999 - Methods"
        res = idea.merge(proj, unretrieved, dry_run=True)
        check("a proposal sourcing an unretrieved PMID is refused",
              any("not in the retrieved literature set" in e
                  for e in res["errors"]), res["errors"])

        scalar = merge_bundle()
        scalar["methods_proposed"]["acquisition"]["total_dose_e_per_A2"] = 50
        res = idea.merge(proj, scalar, dry_run=True)
        check("a bare value is refused",
              any("bare value" in e for e in res["errors"]), res["errors"])
        check("and the refusal says why a bare number is dangerous",
              any("indistinguishable from a measured one" in e
                  for e in res["errors"]))

        no_why = merge_bundle()
        no_why["methods_proposed"]["acquisition"][
            "total_dose_e_per_A2"].pop("why")
        res = idea.merge(proj, no_why, dry_run=True)
        check("a missing `why` warns rather than refusing",
              not res["errors"] and any("`why`" in w for w in res["warnings"]),
              res["warnings"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _fixture_pack(root):
    """A pack on disk, resolved through $PAPER_ENGINE_LAB_PACK."""
    pack = os.path.join(root, "fixture-pack")
    os.makedirs(os.path.join(pack, ".claude-plugin"))
    with open(os.path.join(pack, ".claude-plugin", "plugin.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"name": "fixture-pack", "version": "2026.9.9",
                   "labPack": True}, fh)
    with open(os.path.join(pack, "lab.yml"), "w", encoding="utf-8") as fh:
        fh.write('pack: fixture-pack\ndisplay_name: "Fixture Lab"\n'
                 'curated_on: "2026-09-09"\n')
    with open(os.path.join(pack, "sops.md"), "w", encoding="utf-8") as fh:
        fh.write("# SOPs\n\n## Glow Discharge on the Fixture Coater\n\n"
                 "Negative, 15 mA, 30 s.\n")
    with open(os.path.join(pack, "software.md"), "w", encoding="utf-8") as fh:
        fh.write("# Software\n\n## Fixture Aligner\n\nRefines.\n")
    return pack


def test_pack_source_kind():
    """specs/methods-notebook-2026-09-28.md 1 - a pack section is a source.

    Before this, a proposal taken from the pack's SOP file was refused: the
    pack is installed as a plugin, not under a `Resources/` folder, so the
    lab's own documented setting could not be proposed from the one place a
    pack-only lab keeps it.
    """
    section("merge - a pack section is a legal, CHECKED source (A)")

    root, lab = make_lab()
    saved = os.environ.get("PAPER_ENGINE_LAB_PACK")
    try:
        proj = scaffold_project(lab, name="pack_sources")

        def with_source(src):
            b = merge_bundle()
            b["methods_proposed"]["sample_prep"]["glow_discharge"][
                "source"] = src
            return idea.merge(proj, b, dry_run=True)

        res = with_source("pack:sops.md - Glow Discharge on the Fixture "
                          "Coater")
        check("with no pack installed, a pack source is refused",
              any("no lab pack" in e.lower() for e in res["errors"]),
              res["errors"])

        os.environ["PAPER_ENGINE_LAB_PACK"] = _fixture_pack(root)
        res = with_source("pack:sops.md - Glow Discharge on the Fixture "
                          "Coater")
        check("a pack section that exists is accepted",
              not res["errors"], res["errors"])
        res = with_source("pack: software.md - fixture ALIGNER")
        check("the heading matches on case and padding, like the brief",
              not res["errors"], res["errors"])
        res = with_source("pack:sops.md - A Step the Pack Does Not Carry")
        check("a heading the pack does not carry is refused",
              any("does not carry" in e for e in res["errors"]),
              res["errors"])
        check("and the refusal lists the headings it does carry",
              any("Glow Discharge on the Fixture Coater" in e
                  for e in res["errors"]), res["errors"])
        res = with_source("pack:nowhere.md - Anything")
        check("a file the pack does not have is refused",
              any("nowhere.md" in e for e in res["errors"]), res["errors"])
        res = with_source("pack:sops.md")
        check("a pack source with no heading is refused",
              bool(res["errors"]), res["errors"])
        res = with_source("I remember reading it")
        check("and the neither-kind refusal now names all three kinds",
              any("pack:" in e and "Resources/" in e for e in res["errors"]),
              res["errors"])
    finally:
        if saved is None:
            os.environ.pop("PAPER_ENGINE_LAB_PACK", None)
        else:
            os.environ["PAPER_ENGINE_LAB_PACK"] = saved
        shutil.rmtree(root, ignore_errors=True)


def test_resources_command():
    section("resources - read at Stage 2, from any path")

    root, lab = make_lab()
    try:
        sop = os.path.join(lab, "Resources", "Standard Operating Procedures",
                           "tpd.md")
        with open(sop, "w", encoding="utf-8") as fh:
            fh.write("# Temperature-programmed desorption\n\nRamp 2 K/s.\n")

        from_lab = idea.lab_resources(lab)
        check("the lab folder itself resolves",
              from_lab["resolved_from"] == "the lab folder itself",
              from_lab["resolved_from"])
        check("it finds the SOPs", from_lab["file_count"] >= 2,
              from_lab["files"])
        check("and reads their titles, which are the search vocabulary",
              any("Temperature-programmed desorption" in t
                  for t in from_lab["titles"].values()),
              from_lab["titles"])
        check("a file with no heading gets no invented title",
              all(t for t in from_lab["titles"].values()),
              "empty rather than a fallback on the filename")
        check("no document body is in the output",
              "Ramp 2 K/s" not in json.dumps(from_lab),
              "it has to stay cheap enough to call before the first query")

        proj = scaffold_project(lab, name="from_project")
        from_proj = idea.lab_resources(proj)
        check("a project inside the lab resolves upward",
              from_proj["resolved_from"] == "walked up from the given path",
              from_proj["resolved_from"])
        check("and reaches the same folder",
              from_proj["resources"] == from_lab["resources"])

        from_res = idea.lab_resources(os.path.join(lab, "Resources"))
        check("the Resources folder itself resolves",
              from_res["resolved_from"] == "the Resources folder itself",
              from_res["resolved_from"])

        nowhere = tempfile.mkdtemp(prefix="ig_")
        try:
            res = idea.lab_resources(nowhere)
            check("no lab folder is not an error", not res.get("resources")
                  and "note" in res)
            check("and the note says what happens instead",
                  "search vocabulary then comes from the conversation"
                  in res["note"])
        finally:
            shutil.rmtree(nowhere, ignore_errors=True)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_resources_carries_the_pack():
    """Item 106 - a skill's table promised a payload the engine never emitted.

    `idea-generation`'s Stage 2 table tells the reader that one engine
    command returns three sources of search vocabulary and names the lab
    resource pack as the third. The command returned two: the engine
    imported the pack module for exactly two things - the drop-zone roots
    and the title reader - and its payload carried no pack key at all, so
    nothing in the output named the pack, its freshness or its headings.

    The cost lands hardest in the case the pack was BUILT for: a lab that
    keeps no `Resources/` folder has the pack as its only written
    vocabulary, and there the command returned an empty inventory and two
    empty drop-zones while a pack sat resolved on disk. Same shape as item
    96 inverted, and every suite passed because each half was correct on its
    own.
    """
    section("the resources payload carries the resolved pack (item 106)")

    root = tempfile.mkdtemp(prefix="idea_pack_")
    try:
        # The configuration the pack half of the spec was written for.
        os.makedirs(os.path.join(root, "Ideas"))
        res = idea.lab_resources(root)

        check("the payload has a `pack` key at all", "pack" in res, sorted(res))
        pack = res.get("pack") or {}
        check("...and it says whether one resolved", "resolved" in pack, pack)

        if pack.get("resolved"):
            check("it names the pack", bool(pack.get("display_name")), pack)
            check("...its freshness line", bool(pack.get("freshness")), pack)
            check("...and the headings, which ARE the vocabulary",
                  len(pack.get("headings") or {}) > 0, pack.get("files"))
            check("no document body is in the payload",
                  all(len(v) < 200 for v in (pack.get("headings") or {}).values()),
                  "this runs before the first literature query")
        else:
            check("an absent pack says so rather than looking like an empty "
                  "one", bool(pack.get("note")), pack)

        # The drop-zones must not have been traded away for the pack.
        check("the drop-zone roots are still there",
              "drop_zones" in res and "drop_zones_at_risk" in res,
              sorted(res))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    print("Idea-generation engine suite - specs/idea-generation.md")

    test_resolve()
    test_context()
    test_refusals()
    test_mock_only()
    test_mock_only_cli()

    root = None
    try:
        root, _lab, proj, _ = test_write()
        test_non_destruction(proj)

        rscript = find_rscript()
        if not rscript:
            skip("end to end through the R pipeline",
                 "Rscript not found on PATH or in Program Files")
        else:
            print(f"\nRscript: {rscript}")
            test_pipeline(rscript, proj)
    finally:
        if root:
            shutil.rmtree(root, ignore_errors=True)

    test_explore()

    test_ideas_folder()
    test_sentence_caps()
    test_merge_additive()
    test_merge_settled_outline()
    test_merge_drafted_project()
    test_merge_cap_and_dedup()
    test_merge_refusals()
    test_pack_source_kind()
    test_resources_command()
    test_resources_carries_the_pack()

    print(f"\n{PASSED}/{PASSED + FAILED} passed"
          + (f", {FAILED} FAILED" if FAILED else "")
          + (f", {SKIPPED} skipped" if SKIPPED else ""))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
