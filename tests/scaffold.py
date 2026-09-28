#!/usr/bin/env python3
"""Measure the project scaffold against reality: the manifest, the safety
guarantees, and the R float pipeline end to end.

Third suite alongside reliability.py (citation verification) and fulltext.py
(tiered reading). This one covers specs/setup-project-directory.md, and its
acceptance criterion is that spec's: a project scaffolds, and render_all.R runs
clean on the templates - not only on an empty project, but with a real figure
and a real table in it.

The R sections skip with a printed notice when Rscript is not installed; the
Python sections always run.

Run:  python tests/scaffold.py
"""

import atexit
import glob
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

# This file and the engine it tests share a name, so `import scaffold` would
# resolve to whichever directory happens to be first on sys.path - and inside
# this file, to this file. Load the engine from its path under a distinct
# module name instead, which cannot be ambiguous.
_HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(_HERE, "..", "tools", "scaffold.py")
_spec = importlib.util.spec_from_file_location("_scaffold_engine", ENGINE)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load {ENGINE}")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)

# manuscript.py is loaded too, because two of specs/setup-project-directory.md
# 10.5's cases are about what IT does with what scaffold.py writes: a commented
# prefilled key has to count as MISSING there, not only here. A second reader
# of the same file is exactly where this kind of contract drifts.
_ms_spec = importlib.util.spec_from_file_location(
    "_manuscript_engine",
    os.path.join(os.path.dirname(ENGINE), "manuscript.py"))
if _ms_spec is None or _ms_spec.loader is None:      # pragma: no cover
    raise ImportError("cannot load manuscript.py")
ms = importlib.util.module_from_spec(_ms_spec)
_ms_spec.loader.exec_module(ms)

for _stream in (sys.stdout, sys.stderr):
    # A redirected stream (a pipe, a StringIO under a harness) has no
    # reconfigure at all; asking for it by name keeps that case quiet.
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except ValueError:
            pass

PASSED = 0
FAILED = 0
SKIPPED = 0


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

DEMO_CSV = "\n".join(
    ["dose,response,group"]
    + [f"{d},{r},{g}"
       for g, base in (("WT", 10.0), ("mutant", 6.0))
       for d, r in zip((0, 1, 2, 4, 8, 16),
                       (base, base + 2.1, base + 3.8, base + 6.2,
                        base + 8.9, base + 9.4))]
) + "\n"

FIG_SCRIPT = '''source(here::here("plan", "setup.R"))

# Figure 1 - dose response by genotype

d <- load_data("{data}")

p <- ggplot(d, aes(x = dose, y = response, colour = group, shape = group)) +
  geom_point(size = 1.6) +
  labs(x = "Dose (mM)", y = "Response (AU)", colour = NULL, shape = NULL)

save_float(p)

message("{name}: n = ", nrow(d))
'''

TAB_SCRIPT = '''source(here::here("plan", "setup.R"))

# Table 1 - response summary

d <- load_data("data/raw/demo.csv")

tab <- d |>
  dplyr::group_by(group) |>
  dplyr::summarise(
    n = dplyr::n(),
    `Response (AU)` = sprintf("%.1f +/- %.1f", mean(response), sd(response)),
    .groups = "drop")

save_table(style_float_ft(flextable::flextable(tab)))

message("tab01: ", nrow(tab), " rows")
'''

CAPTIONS = """## Figure 1 \u2014 Fig01_dose_response
**Response rises with dose in both genotypes.**
Dose-response for WT and mutant. n = 6 doses per genotype; points are single measurements.

## Table 1 \u2014 Table01_summary
**Mutant responds less than WT across the range.**
Mean +/- SD of response, pooled over doses.
"""

MOCK_CAPTION_BLOCK = """
## Figure 2 \u2014 Fig02_from_mock
**Placeholder claim, built before the instrument time.**
Same shape as Figure 1, drawn from synthetic rows.
"""

LEGIBILITY_PROBE = '''source(here::here("plan", "setup.R"))

d <- data.frame(x = rep(1:6, 2), y = rep(1:6, 2) + rep(c(0, 2), each = 6),
                g = rep(c("WT", "mutant"), each = 6))

# Firebrick vs forest green: close in lightness, and deuteranopia collapses the
# hue difference. Colour is the only thing separating the series.
cols <- c(WT = "#B22222", mutant = "#228B22")

colour_only <- ggplot(d, aes(x, y, colour = g)) + geom_point() +
  scale_colour_manual(values = cols)

with_shape <- ggplot(d, aes(x, y, colour = g, shape = g)) + geom_point() +
  scale_colour_manual(values = cols)

cat("COLOUR_ONLY=", length(suppressWarnings(check_legibility(colour_only, "f"))), "\\n", sep = "")
cat("WITH_SHAPE=", length(suppressWarnings(check_legibility(with_shape, "f"))), "\\n", sep = "")
'''


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_rscript():
    for name in ("Rscript", "Rscript.exe"):
        p = shutil.which(name)
        if p:
            return p
    # R installs to Program Files on Windows and is routinely not on PATH.
    for pattern in (r"C:\Program Files\R\*\bin\Rscript.exe",
                    r"C:\Program Files\R\*\bin\x64\Rscript.exe",
                    "/usr/lib/R/bin/Rscript", "/usr/local/bin/Rscript"):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return None


def render(rscript, root, env_extra=None):
    env = dict(os.environ)
    env.pop("FORCE_MOCK", None)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run([rscript, os.path.join("plan", "render_all.R")],
                          cwd=root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _slurp(path, **kw):
    """Read a whole file and close it.

    On Windows a handle still open can block the tempdir cleanup this suite
    relies on, which is why nothing here uses `open(path).read()`.
    """
    kw.setdefault("encoding", "utf-8")
    with open(path, "r", **kw) as fh:
        return fh.read()


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


# ---------------------------------------------------------------------------
# A pinned environment, for the whole suite.
# ---------------------------------------------------------------------------
#
# `scaffold.py prefill` resolves a lab resource pack and a drop-zone ladder
# (specs/lab-resource-pack.md 4.1), and both of those reach the REAL machine:
# `~/.claude/plugins/` and `~/.paper-engine/`. Left alone, this suite measures
# which plugins the person running it happens to have installed - it passed on
# a machine with no pack and failed the moment one was installed, which is a
# suite reporting on the machine rather than on the engine.
#
# So every root is redirected into one temporary directory for the whole run.
# The same three variables `tests/labpack.py` uses, for the same reason, and
# `tests/idea.py` carries this same block - copied rather than shared, because
# every suite here is standalone. Change one, change all.
_ENV_SANDBOX = tempfile.mkdtemp(prefix="scaffold_env_")
os.environ["PAPER_ENGINE_HOME"] = os.path.join(_ENV_SANDBOX, "home")
os.environ["PAPER_ENGINE_PLUGINS_DIR"] = os.path.join(_ENV_SANDBOX, "plugins")
os.environ["PAPER_ENGINE_TOOLKIT"] = os.path.join(_ENV_SANDBOX, "toolkit")
for _k in ("PAPER_ENGINE_LAB_PACK", "PAPER_ENGINE_RESOURCES",
           "CLAUDE_PLUGIN_ROOT"):
    os.environ.pop(_k, None)
os.makedirs(os.environ["PAPER_ENGINE_PLUGINS_DIR"], exist_ok=True)
os.makedirs(os.path.join(_ENV_SANDBOX, "toolkit", "resources"), exist_ok=True)
atexit.register(shutil.rmtree, _ENV_SANDBOX, True)


def read(root, rel) -> str:
    path = os.path.join(root, rel)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def read_bytes(root, rel) -> bytes:
    with open(os.path.join(root, rel), "rb") as fh:
        return fh.read()


def sha(root, rel):
    return hashlib.sha256(read_bytes(root, rel)).hexdigest()


def docx_text(path):
    """The visible text of a .docx, plus the number of embedded images."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
        media = [n for n in z.namelist() if n.startswith("word/media/")]
    text = re.sub(r"<[^>]+>", "", xml)
    return text, len(media)


def new_project(**kw):
    root = tempfile.mkdtemp(prefix="scaffold_test_")
    proj = os.path.join(root, kw.pop("name", "demo_project"))
    values = sc.build_values(project_name=os.path.basename(proj), **kw)
    res = sc.scaffold(proj, values)
    return root, proj, res


# ---------------------------------------------------------------------------
# A. The manifest agrees with the template directory
# ---------------------------------------------------------------------------

def test_manifest():
    section("manifest")

    # The manifest carries two `@` placeholders now, each resolved per
    # project: the methods facts by `field`, and the outline by
    # `paper_kind` - a review's `##` headings ARE its section list, so its
    # outline template must not seed IMRaD ones (review-paper 7). Both
    # expand to every value they can take, because a template only one
    # branch reaches is a template nobody notices is missing.
    PLACEHOLDERS = {
        "@methods_facts": [sc.METHODS_TEMPLATES[f] for f in sc.FIELDS],
        "@outline": ["plan/outline.md", "plan/outline_review.md"],
    }
    missing = []
    for _dest, template in list(sc.FILES) + list(sc.REVIEW_ADDS):
        if template is None:
            continue
        names = PLACEHOLDERS.get(template, [template])
        for n in names:
            if not os.path.isfile(os.path.join(sc.TEMPLATE_DIR, n)):
                missing.append(n)
    check("every template the manifest names exists",
          not missing, "\n".join(missing))

    referenced = set()
    for _, template in list(sc.FILES) + list(sc.REVIEW_ADDS):
        if template in PLACEHOLDERS:
            referenced.update(PLACEHOLDERS[template])
        elif template:
            referenced.add(template)

    orphans = []
    for path in glob.glob(os.path.join(sc.TEMPLATE_DIR, "**", "*"), recursive=True):
        if not os.path.isfile(path):
            continue
        rel = os.path.relpath(path, sc.TEMPLATE_DIR).replace(os.sep, "/")
        if rel not in referenced:
            orphans.append(rel)
    check("no template file is unreachable from the manifest",
          not orphans,
          "unreferenced (would never be scaffolded):\n" + "\n".join(orphans))

    dests = [d for d, _ in list(sc.FILES) + list(sc.REVIEW_ADDS)]
    dupes = sorted({d for d in dests if dests.count(d) > 1})
    check("no destination is listed twice", not dupes, dupes)

    # --- what a review scaffolds instead (review-paper 2, 9) -------------
    drops = sc.REVIEW_DROPS | sc.REVIEW_EMPTY_DROPS
    unknown_drops = sorted(d for d in drops
                           if d not in dests and d not in sc.EMPTY_DIRS)
    check("every review drop names something the manifest actually has",
          not unknown_drops, unknown_drops)
    kept = sorted(d for d in drops if d.startswith(("plan/", "drafts/"))
                  and not d.startswith("drafts/source_text/"))
    check("a review keeps plan/ and drafts/ whole", not kept, kept)
    check("...and drops the analysis pipeline, not the data folder itself",
          "data/corpus/papers" in sc.REVIEW_EMPTY_ADDS
          and "data/analysis/analysis.R" in sc.REVIEW_DROPS, True)
    check("the corpus README is scaffolded and the protocol is NOT",
          [d for d, _ in sc.REVIEW_ADDS] == ["data/corpus/README.md"],
          "protocol.md is the scope contract and review.py is its one "
          "writer; two writers of a file that is never generated over "
          "would be one too many")


def test_review_scaffold():
    section("a review scaffolds the corpus, not the analysis "
            "(review-paper 2, 9)")

    root = tempfile.mkdtemp(prefix="scaffold_review_")
    proj = os.path.join(root, "a_review")
    values = sc.build_values(project_name="a_review", field="chemistry",
                             journal="Langmuir", paper_kind="review")
    res = sc.scaffold(proj, values, paper_kind="review")
    try:
        check("it says what kind of project it made",
              res["paper_kind"] == "review", res["paper_kind"])

        def here(rel):
            return os.path.exists(os.path.join(proj, *rel.split("/")))

        check("data/corpus/papers/ exists", here("data/corpus/papers"), True)
        check("...and data/corpus/pdfs/", here("data/corpus/pdfs"), True)
        check("...and the README that says what belongs in it",
              here("data/corpus/README.md"), True)
        check("...but NOT protocol.md - review.py is its one writer",
              not here("data/corpus/protocol.md"), True)

        # A review has no measurements of its own, so it has no analysis
        # pipeline, no raw folders and no methods facts. A meta-analytic
        # review adds `analysis` back through `manages`.
        for gone in ("data/analysis/analysis.R", "data/methods_facts.yml",
                     "data/data_contract.md", "data/raw", "data/raw_images",
                     "data/mock_data", "data/templates"):
            check(f"a review has no {gone}", not here(gone), True)

        # IMRaD is not scaffolded: a review's body sections are its
        # author's, and they come from plan/outline.md's `# Structure`.
        st = sc.source_text_dest(proj, "drafts/source_text/x.md")
        st = os.path.dirname(os.path.join(proj, st))
        present = sorted(os.listdir(st))
        check("the only section file scaffolded is the abstract",
              present == ["notes_to_self.md", "title_abstract.md"], present)

        outline = read(proj, os.path.join("plan", "outline.md"))
        check("the outline template is the review one",
              "THE SECTIONS OF YOUR REVIEW" in outline, True)
        imrad = [h for h in ("## Results", "## Methods", "## Discussion")
                 if h in outline]
        check("...and it seeds no IMRaD heading", not imrad, imrad)
        check("...and says a citekey is what a review's evidence looks like",
              "@ulman1996formation" in outline, True)

        # One slot each, not four and two. Four empty figure folders with a
        # script that has nothing to read teach nothing and get deleted.
        figs = sorted(n for n in os.listdir(os.path.join(proj, "plan",
                                                         "figures"))
                      if n != "_template")
        tabs = sorted(n for n in os.listdir(os.path.join(proj, "plan",
                                                         "tables"))
                      if n != "_template")
        check("one figure slot", figs == ["Fig01"], figs)
        check("one table slot", tabs == ["Table01"], tabs)

        yml = read(proj, "project.yml")
        check("project.yml records the paper kind",
              "paper_kind: review" in yml, True)

        # And the research default is untouched by all of it.
        proj2 = os.path.join(root, "a_paper")
        v2 = sc.build_values(project_name="a_paper", field="chemistry")
        sc.scaffold(proj2, v2)
        check("a research project still gets its analysis pipeline",
              os.path.isfile(os.path.join(proj2, "data", "analysis",
                                          "analysis.R")), True)
        check("...and no corpus",
              not os.path.isdir(os.path.join(proj2, "data", "corpus")), True)
        n = len([x for x in os.listdir(os.path.join(proj2, "plan",
                                                    "figures"))
                 if x != "_template"])
        check("...and four figure slots", n == sc.DEFAULT_FIGURES, n)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# B. A fresh scaffold is complete and fully substituted
# ---------------------------------------------------------------------------

def test_fresh_scaffold():
    section("fresh scaffold")
    root, proj, res = new_project(title="Thermophilic chemoreceptor arrays",
                                  field="biochemistry", journal="Nature",
                                  pi="Jensen")
    try:
        # The manifest names `drafts/source_text/`; a project's own folder
        # carries the round it holds (`drafts/source_text_r1/` on a fresh
        # one), so every destination is resolved before it is looked for.
        gaps = [sc.source_text_dest(proj, d) for d, _ in sc.FILES
                if not os.path.isfile(
                    os.path.join(proj, sc.source_text_dest(proj, d)))]
        check("every manifest file is written", not gaps, gaps)

        dir_gaps = [d for d in sc.EMPTY_DIRS
                    if not os.path.isfile(os.path.join(proj, d, ".gitkeep"))]
        check("every must-exist-empty directory carries a .gitkeep",
              not dir_gaps, dir_gaps)

        unsubstituted = []
        for dest, _ in sc.FILES:
            if not dest.endswith((".md", ".yml", ".bib")):
                continue
            dest = sc.source_text_dest(proj, dest)
            body = read(proj, dest)
            for m in re.findall(r"\{\{\w+\}\}", body):
                unsubstituted.append(f"{dest}: {m}")
        check("no {{placeholder}} survives into a scaffolded project",
              not unsubstituted, "\n".join(unsubstituted))

        check(".here exists and is empty",
              os.path.isfile(os.path.join(proj, ".here"))
              and os.path.getsize(os.path.join(proj, ".here")) == 0)

        yml = sc.read_project_yml(proj)
        check("project.yml records the values it was given",
              yml.get("field") == "biochemistry"
              and yml.get("target_journal") == "Nature"
              and yml.get("title") == "Thermophilic chemoreceptor arrays"
              and yml.get("pi") == "Jensen",
              json.dumps(yml))

        facts = read(proj, "data/methods_facts.yml")
        check("methods_facts.yml is the template for the declared field",
              "acquisition:" in facts and "vitrification" in facts,
              "the biochemistry template should carry the cryo-EM blocks")

        # The manifest plus one script per pre-created float slot. The slots
        # are deliberately NOT in FILES: how many floats a paper has is the
        # paper's business, and `check` must not report a deleted Fig03 as a
        # missing piece of the scaffold.
        n_slots = sc.DEFAULT_FIGURES + sc.DEFAULT_TABLES
        check("scaffold reports a clean create",
              res["already_set_up"] is False and not res["skipped"]
              and len(res["created"]) == len(sc.FILES) + len(sc.EMPTY_DIRS)
              + n_slots,
              f"created {len(res['created'])}, skipped {len(res['skipped'])}, "
              f"expected {len(sc.FILES) + len(sc.EMPTY_DIRS) + n_slots}")
        check("the default float slots are there, one folder each",
              [f["label"] for f in sc.list_float_dirs(proj)]
              == ["Figure 1", "Figure 2", "Figure 3", "Figure 4",
                  "Table 1", "Table 2"],
              [f["rel"] for f in sc.list_float_dirs(proj)])
        check("every slot holds its script, named for its role not its number",
              all(os.path.isfile(os.path.join(f["path"],
                                              "figure.R" if f["kind"] == "figure"
                                              else "table.R"))
                  for f in sc.list_float_dirs(proj)))
        check("a deleted slot is not reported as a missing scaffold piece",
              (shutil.rmtree(os.path.join(proj, "plan", "figures", "Fig04")),
               sc.check(proj)["scaffolded"])[1],
              "how many floats a paper has is the paper's business")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# C. Idempotence, and never overwriting work
# ---------------------------------------------------------------------------

def test_idempotence():
    section("idempotence and non-destruction")
    root, proj, _ = new_project(title="First title", field="chemistry")
    try:
        again = sc.scaffold(proj, sc.build_values(project_name="demo_project"))
        check("a second run creates nothing",
              again["already_set_up"] and not again["created"],
              f"created {again['created']}")

        marker = "\n\nHAND-WRITTEN: the actual research question.\n"
        with open(os.path.join(proj, "plan", "README.md"), "a",
                  encoding="utf-8") as fh:
            fh.write(marker)
        before = sha(proj, "plan/README.md")
        sc.scaffold(proj, sc.build_values(project_name="demo_project"))
        check("a hand edit survives a re-run",
              sha(proj, "plan/README.md") == before
              and marker.strip() in read(proj, "plan/README.md"))

        os.remove(os.path.join(proj, "plan", "theme", "helpers.R"))
        os.remove(os.path.join(proj, ".here"))
        restored = sc.scaffold(proj, sc.build_values(project_name="demo_project"))
        check("only the missing pieces come back",
              sorted(restored["created"]) == sorted([".here", "plan/theme/helpers.R"]),
              restored["created"])
        check("the restored file is the template again",
              os.path.getsize(os.path.join(proj, "plan", "theme", "helpers.R")) > 0)

        # project.yml is never rewritten, so a contradicting flag must lose to
        # what the project already records - otherwise the .yml and the rest of
        # the scaffold would disagree about the field.
        cmd = [sys.executable, ENGINE,
               "scaffold", proj, "--field", "other", "--json"]
        out = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        payload = json.loads(out.stdout) if out.stdout.strip().startswith("{") else {}
        check("an existing project.yml beats a contradicting flag",
              payload.get("field") == "chemistry"
              and sc.read_project_yml(proj).get("field") == "chemistry",
              f"reported field={payload.get('field')}, "
              f"yml field={sc.read_project_yml(proj).get('field')}")
        check("the ignored flag is reported, not swallowed",
              any("--field" in f for f in payload.get("ignored_flags", [])),
              f"ignored_flags={payload.get('ignored_flags')}")

        res = sc.check(proj)
        check("check() reports a complete project as scaffolded",
              res["scaffolded"] and not res["missing"], res["missing"])

        os.remove(os.path.join(proj, "plan", "captions.md"))
        res = sc.check(proj)
        check("check() names the exact gap",
              not res["scaffolded"] and res["missing"] == ["plan/captions.md"],
              res["missing"])

        dry = sc.scaffold(proj, sc.build_values(project_name="demo_project"),
                          dry_run=True)
        check("--dry-run writes nothing",
              dry["created"] == ["plan/captions.md"]
              and not os.path.exists(os.path.join(proj, "plan", "captions.md")))
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# D. The R float pipeline, end to end
# ---------------------------------------------------------------------------

def test_pipeline(rscript):
    section("R float pipeline")
    root, proj, _ = new_project(title="Dose response study", field="chemistry",
                                journal="JACS")
    try:
        code, out = render(rscript, proj)
        check("render_all.R runs clean on a bare scaffold",
              code == 0 and "FAILED" not in out, out)

        # The scaffold pre-creates empty slots (Fig01..Fig04, Table01..02).
        # A float is built by filling in the folder it already has, so these
        # write over the slot scripts rather than adding new folders.
        write(proj, "data/raw/demo.csv", DEMO_CSV)
        os.rename(os.path.join(proj, "plan", "figures", "Fig01"),
                  os.path.join(proj, "plan", "figures", "Fig01_dose_response"))
        os.rename(os.path.join(proj, "plan", "tables", "Table01"),
                  os.path.join(proj, "plan", "tables", "Table01_summary"))
        write(proj, "plan/figures/Fig01_dose_response/figure.R",
              FIG_SCRIPT.format(data="data/raw/demo.csv", name="fig01_dose_response"))
        write(proj, "plan/tables/Table01_summary/table.R", TAB_SCRIPT)
        write(proj, "plan/captions.md", CAPTIONS)

        # Three things that must never execute: the copy-me template folder,
        # a OneDrive conflict copy of a real script, and a conflict copy of a
        # whole float FOLDER - OneDrive makes both.
        write(proj, "plan/figures/Fig02/figure-DESKTOP-A4B6PBU.R",
              'stop("CONFLICT COPY EXECUTED")\n')
        write(proj, "plan/figures/Fig03-DESKTOP-A4B6PBU/figure.R",
              'stop("CONFLICT FOLDER EXECUTED")\n')

        code, out = render(rscript, proj)
        check("render_all.R runs clean with a real figure and table",
              code == 0 and "FAILED" not in out, out)
        check("_template/ is never executed",
              "_template" not in out and not os.path.exists(
                  os.path.join(proj, "plan", "figures", "_template", "figure.png")))
        check("OneDrive conflict copies are never executed",
              "CONFLICT COPY EXECUTED" not in out
              and "CONFLICT FOLDER EXECUTED" not in out
              and "DESKTOP" not in out, out)

        produced = {
            "plan/figures/Fig01_dose_response/figure.png": "raster at journal dpi",
            "plan/figures/Fig01_dose_response/figure.pdf": "vector",
            "plan/tables/Table01_summary/table.html": "preview",
            "plan/tables/Table01_summary/table.rds": "native Word table",
            "plan/preview.html": "preview",
            "plan/floats/figures_and_tables.docx": "co-author hand-off",
            "data/analysis/provenance.json": "free provenance",
        }
        gaps = [p for p in produced if not os.path.isfile(os.path.join(proj, p))]
        check("save_float() writes .png and .pdf; save_table() writes .html "
              "and .rds", not gaps, gaps)

        prov = json.loads(read(proj, "data/analysis/provenance.json"))
        check("setup.R stamps the R and package versions",
              prov.get("r_version", "").startswith("R version")
              and prov.get("packages", {}).get("ggplot2", "") not in ("", "not installed")
              and prov.get("seed") == 1,
              f"R={prov.get('r_version')} seed={prov.get('seed')}")

        preview = read(proj, "plan/preview.html")
        check("preview.html embeds the figure as base64",
              "data:image/png;base64," in preview)
        check("preview.html carries the caption from captions.md",
              "Response rises with dose in both genotypes." in preview
              and "Mutant responds less than WT" in preview)
        check("a captioned table is not also listed as uncaptioned",
              preview.count("no caption in captions.md") == 0,
              "captions.md names tables by their .rds, the preview lists .html "
              "files on disk; matching on the filename rather than the stem "
              "double-lists every table")
        check("the table renders as HTML, not as raw .rds bytes",
              "<table" in preview and "\u0000" not in preview
              and "Table01_summary.rds" not in preview)
        # The folder carries the number and the caption block names the folder,
        # so a slug added to a folder name must not orphan its caption. The
        # match is by number, and this is what asserts that.
        check("a caption block resolves to a folder that has grown a slug",
              "Response rises with dose in both genotypes." in preview
              and preview.count("no caption in captions.md") == 0,
              "captions.md says Fig01_dose_response; the folder is the same "
              "one the scaffold made as Fig01")
        check("empty float slots are not listed as uncaptioned floats",
              "empty slot" in out and preview.count("<section>") <= 3, out)

        docx = os.path.join(proj, "plan", "floats", "figures_and_tables.docx")
        text, images = docx_text(docx)
        check("figures_and_tables.docx carries both captions",
              "Figure 1." in text and "Table 1." in text, text[:300])
        check("the figure is embedded as an image", images >= 1,
              f"{images} embedded media files")
        # A picture of a table would give neither a <w:tbl> element nor the
        # cell text; a native table gives both, and is what a co-author can
        # actually edit.
        xml = zipfile.ZipFile(docx).read("word/document.xml").decode("utf-8", "replace")
        check("the table is a native, editable Word table, not a picture",
              re.search(r"<w:tbl[ >]", xml) is not None
              and "Response (AU)" in text and "mutant" in text,
              f"<w:tbl> elements: {len(re.findall(r'<w:tbl[ >]', xml))}")
        check("float order follows captions.md",
              text.index("Figure 1.") < text.index("Table 1."))
        return root, proj
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise


# ---------------------------------------------------------------------------
# E. Mock data cannot reach the co-author document
# ---------------------------------------------------------------------------

def test_mock(rscript, proj):
    section("mock-data containment")
    write(proj, "data/mock_data/synthetic.csv", DEMO_CSV)
    os.rename(os.path.join(proj, "plan", "figures", "Fig02"),
              os.path.join(proj, "plan", "figures", "Fig02_from_mock"))
    write(proj, "plan/figures/Fig02_from_mock/figure.R",
          FIG_SCRIPT.format(data="data/mock_data/synthetic.csv",
                            name="fig02_from_mock"))
    with open(os.path.join(proj, "plan", "captions.md"), "a",
              encoding="utf-8") as fh:
        fh.write(MOCK_CAPTION_BLOCK)

    docx_rel = "plan/floats/figures_and_tables.docx"
    before = sha(proj, docx_rel)

    code, out = render(rscript, proj)
    check("a mock-fed render still succeeds", code == 0 and "FAILED" not in out, out)

    prov = json.loads(read(proj, "plan/floats/float_provenance.json"))
    # The register is keyed by the float's LABEL, not by a file stem. Under
    # the folder layout every figure writes "figure.png", so stems collide and
    # a stem-keyed register would report one float's provenance for another.
    check("provenance marks the mock float and only the mock float",
          prov.get("Figure 2", {}).get("mock") is True
          and prov.get("Figure 1", {}).get("mock") is False,
          json.dumps({k: v.get("mock") for k, v in prov.items()}))
    check("save_float() announces the watermark", "[mock]" in out, out)

    check("create_floats.R refuses to build from mock data", "REFUSED" in out, out)
    check("the refusal leaves the previous .docx untouched",
          sha(proj, docx_rel) == before,
          "a refused build that still overwrites the hand-off is worse than no "
          "refusal at all")
    check("the summary does not name a document it did not write",
          "NOT WRITTEN" in out, out)

    preview = read(proj, "plan/preview.html")
    check("preview.html banners the mock data",
          "MOCK DATA" in preview and "Nothing here is a result" in preview)

    code, out = render(rscript, proj, {"FORCE_MOCK": "1"})
    check("FORCE_MOCK=1 builds it, and says so",
          code == 0 and "FORCED, MOCK DATA" in out, out)
    check("the forced document carries the warning inside it",
          "MOCK DATA" in docx_text(os.path.join(proj, docx_rel))[0])
    check("the forced .docx really was rewritten", sha(proj, docx_rel) != before)


# ---------------------------------------------------------------------------
# F. The legibility check
# ---------------------------------------------------------------------------

def test_legibility(rscript, proj):
    section("legibility check")
    write(proj, "legibility_probe.R", LEGIBILITY_PROBE)
    proc = subprocess.run([rscript, "legibility_probe.R"], cwd=proj,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    colour_only = re.search(r"COLOUR_ONLY=(\d+)", out)
    with_shape = re.search(r"WITH_SHAPE=(\d+)", out)
    if not (colour_only and with_shape):
        check("the legibility probe runs", False, out)
        return
    check("colour-only series that collapse under deuteranopia are flagged",
          int(colour_only.group(1)) == 1,
          f"{colour_only.group(1)} problems reported for the colour-only plot")
    check("a redundant shape aesthetic silences the warning",
          int(with_shape.group(1)) == 0,
          f"{with_shape.group(1)} problems reported once shape encodes the "
          f"same variable")
    os.remove(os.path.join(proj, "legibility_probe.R"))


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# C2. --only scaffolds one subtree, so a manuscript-only project can have
#     drafts/ without acquiring an empty float pipeline
# ---------------------------------------------------------------------------

def test_only_subtree():
    section("--only <subtree>")

    check("SUBTREES is derived from the manifest, not typed out again",
          "drafts" in sc.SUBTREES and "plan" in sc.SUBTREES,
          f"SUBTREES = {sc.SUBTREES}")

    root = tempfile.mkdtemp(prefix="scaffold_only_")
    try:
        proj = os.path.join(root, "manuscript_only")
        values = sc.build_values(project_name="manuscript_only",
                                 journal="Langmuir")
        res = sc.scaffold(proj, values, only="drafts")

        check("every created path is inside drafts/",
              all(p.startswith("drafts/") for p in res["created"]),
              "\n".join(p for p in res["created"]
                         if not p.startswith("drafts/")) or "all inside")

        # This is the point of the flag: manuscript.py init calls it on a
        # project whose floats and analysis are managed elsewhere, and that
        # project must not end up with an empty plan/ and data/ it will never
        # run.
        for unwanted in ("plan", "data", "obsolete"):
            check(f"{unwanted}/ is not created",
                  not os.path.isdir(os.path.join(proj, unwanted)))

        # ...and what it DOES create has to be the whole drafts subtree, or a
        # caller relying on it gets a half-built editing surface.
        want = sorted(sc.source_text_dest(proj, d) for d, _ in sc.FILES
                      if d.startswith("drafts/"))
        check("the whole drafts/ subtree is created",
              sorted(res["created"]) == want,
              f"missing {sorted(set(want) - set(res['created']))}")

        check("the result records which subtree it ran on",
              res["only"] == "drafts", res["only"])

        # Segment match, not prefix match: --only data must not sweep in a
        # future data_bench/ just because the string starts the same way.
        got = sc.scaffold(os.path.join(root, "seg"), values, only="data",
                          dry_run=True)["created"]
        check("--only matches a path segment, never a prefix",
              all(p.split("/", 1)[0] == "data" for p in got),
              "\n".join(p for p in got if p.split("/", 1)[0] != "data")
              or "all exact")

        # A second run is still a no-op, which is what makes init safe to
        # re-run on a folder someone has since worked in.
        again = sc.scaffold(proj, values, only="drafts")
        check("re-running --only creates nothing", again["created"] == [],
              again["created"])

        # And the flag composes: the full run afterwards fills in the rest
        # rather than refusing or duplicating.
        full = sc.scaffold(proj, values)
        check("a later full scaffold adds the rest and re-creates nothing",
              all(not p.startswith("drafts/") for p in full["created"])
              and os.path.isdir(os.path.join(proj, "plan")),
              "\n".join(p for p in full["created"]
                         if p.startswith("drafts/")) or "no drafts/ rewritten")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# G. Float folders: the identity contract, renumbering, and migration
# ---------------------------------------------------------------------------

def test_float_ops():
    section("float folders")

    # The folder-name grammar is a contract with plan/theme/read_captions.R,
    # the same way CAPTION_HEADING_RE is. A folder the R side cannot parse is
    # invisible to the preview and the Word floats, and nothing reports it -
    # so the two regexes are compared directly rather than trusted.
    r_src = os.path.join(sc.TEMPLATE_DIR, "plan", "theme", "read_captions.R")
    with open(r_src, encoding="utf-8") as fh:
        r_text = fh.read()
    m = re.search(r'FLOAT_DIR_RE <- "(.+)"', r_text)
    check("read_captions.R still declares FLOAT_DIR_RE", m is not None)
    if m:
        # R doubles its backslashes in a string literal; nothing else may differ.
        r_pattern = m.group(1).replace("\\\\", "\\")
        check("the R and Python float-folder regexes are the same pattern",
              r_pattern == sc.FLOAT_DIR_RE.pattern,
              f"R: {r_pattern!r}\n         Py: {sc.FLOAT_DIR_RE.pattern!r}")

    cases = [
        ("Fig01", "Figure 1"), ("Fig1", "Figure 1"), ("Fig 1", "Figure 1"),
        ("Fig_1", "Figure 1"), ("Figure01", "Figure 1"),
        ("Fig01_yield_by_catalyst", "Figure 1"),
        ("FigS01", "Figure S1"), ("FigS1", "Figure S1"),
        ("Table02", "Table 2"), ("Tab02", "Table 2"),
        ("Table02_conditions", "Table 2"),
    ]
    bad = [(n, sc.parse_float_dir(n)) for n, want in cases
           if (sc.parse_float_dir(n) or {}).get("label") != want]
    check("every spelling of a float folder resolves to one label", not bad, bad)
    check("a folder that is not a float is not parsed as one",
       all(sc.parse_float_dir(n) is None
           for n in ("_template", "figures", "notes", "Figment", "S01")),
       [n for n in ("_template", "figures", "notes", "Figment", "S01")
        if sc.parse_float_dir(n) is not None])

    # --- renumbering ------------------------------------------------------
    root, proj, _ = new_project(title="Renumber", journal="JACS")
    try:
        write(proj, "plan/captions.md",
              "<!-- keep me at the top -->\n\n"
              "## Figure 1 \u2014 Fig01\n**One.**\nBody one.\n\n"
              "## Table 1 \u2014 Table01\n**A table between them.**\nBody t.\n\n"
              "## Figure 2 \u2014 Fig02\n**Two.**\nBody two.\n\n"
              "## Figure 3 \u2014 Fig03\n**Three.**\nBody three.\n")
        before = read(proj, "plan/captions.md")

        dry = sc.float_renumber(proj, "figure", 3, 1, dry_run=True)
        check("a dry-run renumber writes nothing",
              read(proj, "plan/captions.md") == before
              and os.path.isdir(os.path.join(proj, "plan", "figures", "Fig03"))
              and len(dry["moves"]) == 3, dry["moves"])

        res = sc.float_renumber(proj, "figure", 3, 1)
        check("moving 3 to 1 walks the others down, none landing on another",
              sorted(f["label"] for f in sc.list_float_dirs(proj))
              == ["Figure 1", "Figure 2", "Figure 3", "Figure 4",
                  "Table 1", "Table 2"],
              [f["rel"] for f in sc.list_float_dirs(proj)])

        caps = read(proj, "plan/captions.md")
        order = re.findall(r"^## (Figure|Table) (S?[0-9]+)", caps, re.M)
        # Document order in captions.md IS manuscript order - read_captions()
        # assigns `order` by position and create_floats.R walks it that way.
        # Headings numbered 1,2,3 sitting in the order 2,3,1 build a co-author
        # document with the figures in the wrong order and every caption
        # numbered correctly, which is the kind of wrong that survives a read.
        check("the caption blocks are put back in numeric order",
              [n for k, n in order if k == "Figure"] == ["1", "2", "3"], order)
        check("each heading kept its own body",
              caps.index("Body three.") < caps.index("Body one.")
              < caps.index("Body two."),
              "the bodies must travel with their headings")
        check("the table keeps its slot between the figures",
              order[1] == ("Table", "1"), order)
        check("the preamble is not moved", caps.startswith("<!-- keep me"))
        check("renumbering reports both halves",
              len(res["moves"]) == 3 and len(res["caption_edits"]) == 3
              and res["reordered"] is True, res)

        sc.float_renumber(proj, "figure", 1, 3)
        check("renumbering back restores the file byte for byte",
              read(proj, "plan/captions.md") == before,
              "a move and its inverse must cancel, or repeated edits drift")

        miss = sc.float_renumber(proj, "figure", 9, 1)
        check("renumbering a float that is not there is refused, not guessed",
              miss.get("error") and not miss["moves"], miss)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # --- migration --------------------------------------------------------
    root, proj, _ = new_project(title="Migrate", journal="JACS")
    try:
        for rel, body in [
                ("plan/figures/fig01_yield.R", "x\n"),
                ("plan/figures/fig01_yield.png", "png\n"),
                ("plan/figures/fig01_yield.pdf", "pdf\n"),
                ("plan/figures/figS01_extra.R", "x\n"),
                ("plan/tables/tab01_conditions.R", "x\n"),
                ("plan/tables/tab01_conditions.html", "html\n"),
                ("plan/figures/oddly_named.R", "x\n")]:
            write(proj, rel, body)

        res = sc.float_migrate(proj, dry_run=True)
        check("a dry-run migration writes nothing",
              os.path.isfile(os.path.join(proj, "plan", "figures",
                                          "fig01_yield.R"))
              and len(res["moved"]) == 3, res["moved"])

        res = sc.float_migrate(proj)
        moved = {m["label"]: m["to"] for m in res["moved"]}
        check("a flat script becomes a folder, keeping its slug",
              moved.get("Figure 1") == "plan/figures/Fig01_yield/figure.R"
              and moved.get("Figure S1") == "plan/figures/FigS01_extra/figure.R"
              and moved.get("Table 1")
              == "plan/tables/Table01_conditions/table.R", moved)
        # A .png left behind in plan/figures/ is a stale image on disk under a
        # name nothing produces any more - the preview would still find it.
        check("the rendered outputs travel with the script",
              os.path.isfile(os.path.join(proj, "plan", "figures", "Fig01_yield",
                                          "figure.png"))
              and os.path.isfile(os.path.join(proj, "plan", "figures",
                                              "Fig01_yield", "figure.pdf"))
              and os.path.isfile(os.path.join(proj, "plan", "tables",
                                              "Table01_conditions", "table.html")))
        check("a script with no readable number is reported, not renamed",
              res["unresolved"] == ["plan/figures/oddly_named.R"]
              and os.path.isfile(os.path.join(proj, "plan", "figures",
                                              "oddly_named.R")),
              res["unresolved"])
        check("check() names what the runner would ignore",
              sc.check(proj)["unclaimed_scripts"] == ["plan/figures/oddly_named.R"],
              sc.check(proj)["unclaimed_scripts"])
        # What a journal uploads is the rendered image, so the float report
        # lists those beside the scripts - manuscript.py figure-files asks
        # "did this figure get drawn" of this field and of nothing else.
        fig01 = [f for f in sc.check(proj)["floats"]
                 if f["dir"] == "plan/figures/Fig01_yield"]
        check("the float report lists what rendered, not just what would",
              len(fig01) == 1 and fig01[0]["renders"] == ["figure.pdf",
                                                          "figure.png"],
              fig01)
        check("...and carries the identity manuscript.py matches captions on",
              len(fig01) == 1 and (fig01[0]["kind"], fig01[0]["number"],
                                   fig01[0]["supplementary"])
              == ("figure", "1", False),
              fig01)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# G. Mock floats
# ---------------------------------------------------------------------------
# Two halves, and they fail differently. The specs are pure functions of the
# column kinds, so they are checked without touching the disk. The scripts are
# only proven by running them: generated R that parses and produces nothing is
# the failure this whole section exists to catch.

MOCK_BUNDLE = {
    "mock": {"hypothesis": "Catalyst B outperforms catalyst A.",
             "n": 12, "seed": 1},
    "data_files": [{
        "file": "measurements",
        "group_column": "catalyst",
        "columns": [
            {"name": "catalyst", "kind": "categorical", "levels": ["A", "B"]},
            {"name": "temperature_c", "kind": "continuous",
             "mock": {"A": [70, 20], "B": [70, 20]}},
            {"name": "yield_pct", "kind": "continuous",
             "mock": {"A": [42, 4], "B": [71, 4]}},
            {"name": "turnover_number", "kind": "count",
             "mock": {"A": 180, "B": 320}},
            {"name": "selectivity", "kind": "proportion",
             "mock": {"A": [0.61, 0.07], "B": [0.88, 0.05]}}]}]}

NOGROUP_BUNDLE = {
    "mock": {"n": 20, "seed": 2},
    "data_files": [{
        "file": "solo",
        "columns": [
            {"name": "depth_um", "kind": "continuous", "mock": {"": [12, 3]}},
            {"name": "hardness_gpa", "kind": "continuous",
             "mock": {"": [4.2, 0.6]}}]}]}


def run_idea_mock(proj, bundle):
    """`idea.py mock` over a bundle - the shared generator, run as a CLI.

    Shelled out rather than imported: the point of the shared engine is that
    setup-project-directory calls the same command a user would, and an import
    would not prove the CLI exists.
    """
    engine = os.path.join(_HERE, "..", "tools", "idea.py")
    path = os.path.join(proj, "_bundle.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh)
    proc = subprocess.run([sys.executable, engine, "mock", proj,
                           "--bundle", path, "--json"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    try:
        return json.loads(proc.stdout), proc
    except json.JSONDecodeError:
        return {}, proc


def test_mock_float_specs():
    section("mock float specs")

    cols = sc._mock_columns(MOCK_BUNDLE)
    check("the group column is separated from the outcomes",
          cols["group"] == "catalyst" and "catalyst" not in cols["continuous"],
          f"group={cols['group']!r} continuous={cols['continuous']}")
    check("columns are bucketed by kind",
          cols["continuous"] == ["temperature_c", "yield_pct"]
          and cols["count"] == ["turnover_number"]
          and cols["proportion"] == ["selectivity"], cols)

    figs = sc.mock_figure_specs(cols)
    check("a four-column contract fills all four figure slots",
          len(figs) == 4, f"{len(figs)} specs")

    # Figure 1 is the composite, and it is the only reason the panel system is
    # discoverable in a fresh project.
    check("Figure 1 is a two-panel composite with explicit A/B tags",
          'tag = "A"' in figs[0]["body"] and 'tag = "B"' in figs[0]["body"]
          and "p <- p_a | p_b" in figs[0]["body"],
          figs[0]["body"].splitlines()[0])
    check("Figure 1 sets a height, because one row of two panels is not square",
          "height" in figs[0]["save_args"], figs[0]["save_args"])
    check("the later figures are single-panel and carry no tag",
          all("tag =" not in f["body"] for f in figs[1:]),
          [f["title"] for f in figs[1:]])

    # Every column with a value belongs to some slot: a contract column that
    # no float touches is a column nobody looks at until submission.
    covered = " ".join(f["body"] for f in figs)
    for col in ("temperature_c", "yield_pct", "turnover_number", "selectivity"):
        check(f"{col} appears in some figure", col in covered)

    check("every figure spec states a claim and a detail for its caption",
          all(f["claim"].strip() and f["detail"].strip() for f in figs))
    check("no spec plots the grouping variable against itself",
          not any("aes(x = catalyst, y = catalyst" in f["body"] for f in figs))

    tabs = sc.mock_table_specs(cols)
    check("two tables: the summary and the column inventory", len(tabs) == 2,
          [t["title"] for t in tabs])
    check("the summary groups by the group column",
          "d[[\"catalyst\"]]" in tabs[0]["body"], tabs[0]["body"].splitlines()[1])

    # The no-group case is the one that silently generates invalid R: there is
    # no x to put on a boxplot, so the shape has to change to a histogram.
    ncols = sc._mock_columns(NOGROUP_BUNDLE)
    nfigs = sc.mock_figure_specs(ncols)
    check("without a group column the comparison becomes a histogram",
          all("geom_boxplot" not in f["body"] for f in nfigs)
          and any("geom_histogram" in f["body"] for f in nfigs),
          [f["title"] for f in nfigs])
    check("without a group column the summary table still groups validly",
          'rep("all", nrow(d))' in sc.mock_table_specs(ncols)[0]["body"])


def test_mock_float_errors():
    section("mock float refusals")

    root, proj, _ = new_project(title="Errors", field="chemistry")
    try:
        res = sc.mock_floats(proj, {"data_files": []})
        check("a bundle with no data_files is refused, not guessed at",
              res["errors"] and not res["written"], res["errors"])

        res = sc.mock_floats(proj, {"data_files": [{
            "file": "m", "columns": [{"name": "note", "kind": "categorical",
                                      "levels": ["x"]}]}]})
        check("a contract with nothing numeric is refused",
              res["errors"] and not res["written"], res["errors"])

        # The generator has to have run first. Building floats over a CSV that
        # does not exist would write six scripts that all fail at load_data().
        res = sc.mock_floats(proj, MOCK_BUNDLE)
        check("floats are refused before the mock CSV exists",
              res["errors"] and not res["written"]
              and "measurements_mock.csv" in res["errors"][0], res["errors"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_mock_float_write():
    section("mock float writing")

    root, proj, _ = new_project(title="Writing", field="chemistry")
    try:
        got, proc = run_idea_mock(proj, MOCK_BUNDLE)
        check("idea.py mock writes and runs the generator",
              got.get("action") == "create" and got.get("mock", {}).get("ran"),
              (proc.stdout or proc.stderr)[:300])
        check("the CSV it wrote carries the _mock suffix that arms the guards",
              os.path.isfile(os.path.join(proj, "data", "mock_data",
                                          "measurements_mock.csv")))

        res = sc.mock_floats(proj, MOCK_BUNDLE)
        check("six slots are filled", len(res["written"]) == 6
              and not res["errors"],
              [w["path"] for w in res["written"]])
        check("the scripts land in the pre-made slots, not in new folders",
              all(w["path"].startswith(("plan/figures/Fig0", "plan/tables/Table0"))
                  for w in res["written"]))

        # Every mock script must be identifiable as one, or the never-overwrite
        # rule below has nothing to key on.
        for w in res["written"]:
            body = read(proj, w["path"])
            if not check(f"{w['path']} is marked as a mock float",
                         sc.MOCK_MARKER in body):
                continue
            check(f"{w['path']} reads through load_data from mock_data",
                  'load_data("data/mock_data/' in body)
            check(f"{w['path']} is not left behind a BUILT guard",
                  "BUILT <- TRUE" in body and "BUILT <- FALSE" not in body)

        caps = read(proj, "plan/captions.md")
        check("a caption block is appended for every float",
              len(res["captions"]) == 6, res["captions"])
        check("each caption block leads with a bold claim",
              caps.count("\n**") >= 6, caps.count("\n**"))
        check("each caption says on its face that it is mock",
              caps.count("Built from mock data") == 6,
              caps.count("Built from mock data"))
        check("the caption headings name the folder the float lives in",
              "— Fig01" in caps and "— Table01" in caps)

        # The instruction comment in captions.md carries example headings.
        # Counting those as existing floats would suppress the real blocks.
        nums = sc._caption_numbers(sc._read_caption_lines(proj))
        check("example headings inside the HTML comment are not read as floats",
              ("figure", "1") in nums and len(nums) == 6, sorted(nums))

        # --- never overwrite -------------------------------------------------
        again = sc.mock_floats(proj, MOCK_BUNDLE)
        check("a re-run writes nothing and skips everything",
              not again["written"] and len(again["skipped"]) == 6,
              again["skipped"][:1])
        # Counted the way the engine counts, skipping the HTML comment: the
        # scaffolded captions.md carries an example `## Figure 1 — Fig01`
        # inside its instruction block, so a raw string count reports two.
        check("a re-run does not duplicate the caption blocks",
              len(sc._caption_numbers(sc._read_caption_lines(proj))) == 6,
              sorted(sc._caption_numbers(sc._read_caption_lines(proj))))

        forced = sc.mock_floats(proj, MOCK_BUNDLE, force=True)
        check("--force rebuilds a slot that holds a mock float",
              len(forced["written"]) == 6, len(forced["written"]))

        # A script someone has worked on is never touched, force or not. This
        # is the guarantee the whole engine is built on.
        write(proj, "plan/figures/Fig02/figure.R",
              'source(here::here("plan", "setup.R"))\nBUILT <- TRUE\n# mine\n')
        kept = sc.mock_floats(proj, MOCK_BUNDLE, force=True)
        check("--force still refuses to overwrite a real script",
              "# mine" in read(proj, "plan/figures/Fig02/figure.R"),
              [s["why"] for s in kept["skipped"]])

        res = sc.mock_floats(proj, MOCK_BUNDLE, dry_run=True)
        check("--dry-run reports without writing",
              res["dry_run"] and "# mine" in
              read(proj, "plan/figures/Fig02/figure.R"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_mock_float_render(rscript):
    section("mock floats render")

    root, proj, _ = new_project(title="Rendering", field="chemistry",
                                journal="JACS")
    try:
        got, _proc = run_idea_mock(proj, MOCK_BUNDLE)
        if not got.get("mock", {}).get("ran"):
            skip("mock floats render", "the generator did not run")
            return
        res = sc.mock_floats(proj, MOCK_BUNDLE)
        if not check("six mock floats written", len(res["written"]) == 6):
            return

        rc, out = render(rscript, proj)
        check("render_all.R runs every mock float without error", rc == 0,
              out[-700:] if rc else "")
        check("no script warns", "warning:" not in out, out[-700:])
        check("all six scripts report ok", "6/6 scripts ok" in out,
              [l for l in out.splitlines() if "scripts ok" in l])

        # The images have to exist and be non-trivial: a script that runs and
        # writes an empty canvas passes every check above.
        for n in (1, 2, 3, 4):
            rel = f"plan/figures/Fig{n:02d}/figure.png"
            ok = os.path.isfile(os.path.join(proj, rel))
            size = os.path.getsize(os.path.join(proj, rel)) if ok else 0
            check(f"Fig{n:02d} produced a real .png", ok and size > 10000,
                  f"{size} bytes")
        for n in (1, 2):
            rel = f"plan/tables/Table{n:02d}/table.rds"
            check(f"Table{n:02d} produced the native-Word .rds",
                  os.path.isfile(os.path.join(proj, rel)))

        check("the preview shows all six floats",
              "preview.html   6 floats" in out,
              [l for l in out.splitlines() if "preview.html" in l])

        # The safeguards are the reason mock floats are allowed to exist at
        # all, so they are checked here rather than assumed from test_mock().
        check("the preview banners the mock data", "[MOCK DATA]" in out)
        check("the co-author .docx is refused", "REFUSED" in out
              and "NOT WRITTEN" in out,
              [l for l in out.splitlines() if "floats.docx" in l])

        prov = json.loads(read(proj, "plan/floats/float_provenance.json"))
        check("every float is recorded as mock in the provenance register",
              len(prov) == 6 and all(v.get("mock") for v in prov.values()),
              {k: v.get("mock") for k, v in prov.items()})

        # And the way out: a script pointed at data/raw/ stops being mock.
        os.makedirs(os.path.join(proj, "data", "raw"), exist_ok=True)
        shutil.copyfile(
            os.path.join(proj, "data", "mock_data", "measurements_mock.csv"),
            os.path.join(proj, "data", "raw", "measurements.csv"))
        body = read(proj, "plan/figures/Fig02/figure.R").replace(
            "data/mock_data/measurements_mock.csv", "data/raw/measurements.csv")
        write(proj, "plan/figures/Fig02/figure.R", body)
        rc, out = render(rscript, proj)
        prov = json.loads(read(proj, "plan/floats/float_provenance.json"))
        check("repointing a script at data/raw/ clears its mock flag",
              rc == 0 and prov.get("Figure 2", {}).get("mock") is False,
              {k: v.get("mock") for k, v in prov.items()})
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_mock_float_nogroup(rscript):
    section("mock floats without a group column")

    root, proj, _ = new_project(title="Solo", field="other")
    try:
        got, _proc = run_idea_mock(proj, NOGROUP_BUNDLE)
        if not got.get("mock", {}).get("ran"):
            skip("mock floats without a group column", "generator did not run")
            return
        res = sc.mock_floats(proj, NOGROUP_BUNDLE)
        check("a two-column contract still fills slots",
              len(res["written"]) >= 3 and not res["errors"],
              [w["path"] for w in res["written"]])
        rc, out = render(rscript, proj)
        check("the generated R is valid without a grouping variable", rc == 0,
              out[-700:] if rc else "")
        check("nothing warns", "warning:" not in out, out[-700:])
    finally:
        shutil.rmtree(root, ignore_errors=True)


# Prose written by hand into a pre-label project, so the check that the
# scaffold left it alone is a comparison and not an assumption.
METHODS_PROBE = "# Methods\n\nMine, written before the label existed.\n"


def test_source_text_round_label():
    section("the source text folder carries the round it holds")

    root = tempfile.mkdtemp(prefix="scaffold_stlabel_")
    try:
        # --- a fresh project is r1 ---------------------------------------
        proj = os.path.join(root, "fresh")
        sc.scaffold(proj, sc.build_values(project_name="fresh"),
                    only="drafts")
        drafts = os.path.join(proj, "drafts")
        check("a fresh scaffold writes drafts/source_text_r1/",
              os.path.isfile(os.path.join(drafts, "source_text_r1",
                                          "methods.md")),
              sorted(os.listdir(drafts)))
        check("...and no unlabelled folder beside it",
              not os.path.isdir(os.path.join(drafts, "source_text")),
              sorted(os.listdir(drafts)))
        check("...and the resolver finds exactly one",
              sc.source_text_folders(drafts) == [(1, "source_text_r1")],
              sc.source_text_folders(drafts))

        # --- --round writes the label it is given -------------------------
        eight = os.path.join(root, "at_r8")
        sc.scaffold(eight, sc.build_values(project_name="at_r8"),
                    only="drafts", rnd=8)
        check("--round 8 writes drafts/source_text_r8/",
              os.path.isfile(os.path.join(eight, "drafts", "source_text_r8",
                                          "methods.md")),
              sorted(os.listdir(os.path.join(eight, "drafts"))))

        # --- a project that already has one keeps it ----------------------
        # Writing source_text_r1/ into a project sitting at r8 would create
        # the second folder the label exists to make visible.
        res = sc.scaffold(eight, sc.build_values(project_name="at_r8"),
                          only="drafts", rnd=1)
        here = sorted(d for d in os.listdir(os.path.join(eight, "drafts"))
                      if d.startswith("source_text"))
        check("a re-run at a different round does not fork the folder",
              here == ["source_text_r8"], here)
        made = [p for p in res["created"] if "source_text" in p]
        check("...and creates nothing, because it is all there",
              not made, made)

        # --- an unlabelled folder is written into, never duplicated -------
        old = os.path.join(root, "unlabelled")
        os.makedirs(os.path.join(old, "drafts", "source_text"))
        write(old, "drafts/source_text/methods.md", METHODS_PROBE)
        sc.scaffold(old, sc.build_values(project_name="unlabelled"),
                    only="drafts")
        kept = sorted(d for d in os.listdir(os.path.join(old, "drafts"))
                      if d.startswith("source_text"))
        check("a pre-label project keeps its own folder",
              kept == ["source_text"],
              "%s - renaming it is manuscript.py init's job, not the "
              "scaffold's" % kept)
        check("...and the prose in it is untouched",
              read(old, "drafts/source_text/methods.md") == METHODS_PROBE)

        # --- check() reports the folder the project actually has ----------
        rep = sc.check(eight)
        gaps = [m for m in rep["missing"] if "source_text" in m]
        check("check() looks for the labelled folder", not gaps, gaps)
        check("...and names it in what is present",
              any(p.startswith("drafts/source_text_r8/")
                  for p in rep["present"]))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_source_text_resolver_matches_manuscript_py():
    section("the source text resolver is the same rule in both engines")

    # scaffold.py carries its own copy for the reason every engine here is
    # standalone. Repetition is only safe while something compares the copies,
    # which is the CAPTION_HEADING_RE rule applied to a second thing.
    with open(os.path.join(_HERE, "..", "tools", "manuscript.py"),
              encoding="utf-8") as fh:
        src = fh.read()
    for name in ("SOURCE_TEXT_RE", "def source_text_label",
                 "def source_text_folders"):
        check("manuscript.py still defines %s" % name, name in src)
    m = re.search(r'SOURCE_TEXT_RE = re\.compile\(r"([^"]+)"\)', src)
    theirs = m.group(1) if m else "(manuscript.py defines it another way)"
    check("the pattern is character for character the same",
          sc.SOURCE_TEXT_RE.pattern == theirs,
          "%s vs %s" % (sc.SOURCE_TEXT_RE.pattern, theirs))
    for rnd, want in ((0, "source_text"), (1, "source_text_r1"),
                      (12, "source_text_r12")):
        check("r%d labels as %s" % (rnd, want),
              sc.source_text_label(rnd) == want, sc.source_text_label(rnd))
    for name, want in (("source_text", True), ("source_text_r7", True),
                       ("source_text_old", False), ("source_textr7", False),
                       ("source_text_r", False), ("Langmuir", False)):
        check("%r is a source text folder: %s" % (name, want),
              bool(sc.SOURCE_TEXT_RE.match(name)) is want)


# ---------------------------------------------------------------------------
# L. data/analysis/ - specs/analysis-and-front-matter.md 1
# ---------------------------------------------------------------------------
#
# analysis.md USED to be a hand-written lab note that was scaffolded and never
# generated. It is now the OUTPUT of analysis.R, and this section was rewritten
# rather than extended: every check in it asserted the old contract, and a test
# that still passes after the thing it tested was inverted is worse than no
# test - it is a green tick over a claim nobody is making any more.
#
# THE R WRITE STEP IS NOT EXECUTED HERE and this suite does not pretend it is.
# R is not installed on every machine that runs these checks, so the three
# region cases are measured against manuscript.py's reader (tests/manuscript.py,
# section "analysis.md regions"), and what is asserted here is the one thing
# that would silently break if the two implementations drifted: they have to
# agree on the marker text, or R writes a file Python reads as unmarked and
# every number in it is reported as unrecorded.

def test_analysis_md():
    section("data/analysis/ - analysis.R and its generated output")

    root, proj, _ = new_project(field="chemistry")
    try:
        r_path = os.path.join(proj, "data", "analysis", "analysis.R")
        md_path = os.path.join(proj, "data", "analysis", "analysis.md")

        check("analysis.R is scaffolded", os.path.isfile(r_path))
        check("analysis.md is NOT scaffolded", not os.path.isfile(md_path),
              "it is generated by analysis.R; a stub carrying only the marker "
              "would report FILLED to a check that cannot tell a scaffold "
              "from a run")
        check("there is no analysis_stats.R left under the old name",
              not os.path.isfile(os.path.join(proj, "data", "analysis",
                                              "analysis_stats.R")))

        r_text = read(proj, "data/analysis/analysis.R")

        # The cross-language contract. R writes the marker; manuscript.py finds
        # it. One string, two languages, and nothing else holds them together.
        check("analysis.R carries the same marker text manuscript.py looks for",
              ms.ANALYSIS_MARKER_HEAD in r_text,
              ms.ANALYSIS_MARKER_HEAD)

        check("it writes analysis.md", 'here::here("data", "analysis", "analysis.md")'
              in r_text or '"analysis.md"' in r_text)
        # Asserted on the WRITE, not on the word. Both files still name the
        # .rds in the comment explaining why it went, and a check that fires
        # on the explanation would force the reasoning to be deleted to keep
        # the suite green - which is how a record gets tidied out.
        check("it no longer writes an .rds", "saveRDS" not in r_text,
              [ln for ln in r_text.splitlines() if "saveRDS" in ln])
        check("...and says why the second file went",
              "no reader" in r_text or "had one reader" in r_text)
        check("it preserves what is below the marker",
              "kept" in r_text and "writeLines(c(md, MARKER, kept)" in r_text)
        check("an unmarked file is kept WHOLE rather than overwritten",
              "kept <- c(" in r_text and "prev)" in r_text,
              "the migration path for a hand-written analysis.md")
        check("it says the numbers' meaning does not go in the file",
              "takeaways" in r_text.lower())
        check("it names where the meaning goes instead",
              "plan/README.md" in r_text)
        check("it states the contract in both directions",
              "Every number in the paper is in this file" in r_text
              and "NOT every number in this file" in r_text)
        check("it documents the [must appear] marker",
              "[must appear]" in r_text)
        check("it points an unfilled section at the analysis skill",
              "`analysis` skill" in r_text)

        # The two accessors went with the file. p_stars() stays, because it
        # takes a NUMBER rather than a file and still formats a p-value read
        # off analysis.md by hand.
        helpers = read(proj, "plan/theme/helpers.R")
        for gone in ("load_stats <- function", "stat_p <- function",
                     "readRDS(here"):
            check(f"helpers.R no longer defines {gone}",
                  gone not in helpers,
                  [ln for ln in helpers.splitlines() if gone in ln][:3])
        check("p_stars() survives - it formats a number, not a file",
              "p_stars <- function" in helpers)
        check("...and helpers.R says never to recompute a test in a figure",
              "never recompute a test" in helpers.lower())

        fig = read(proj, "plan/figures/_template/figure.R")
        for gone in ("load_stats(", "stat_p("):
            check(f"the figure template no longer calls {gone}",
                  gone not in fig,
                  [ln for ln in fig.splitlines() if gone in ln][:3])
        check("...and says a printed statistic is typed from analysis.md",
              "analysis.md" in fig)

        res = sc.check(proj)
        check("check() does not report the ungenerated analysis.md as missing",
              not [m for m in res["missing"] if "analysis.md" in m],
              res["missing"])
        check("analysis.md is listed as a generated file",
              "data/analysis/analysis.md" in sc.GENERATED)
        check("check() lists analysis.R as present",
              "data/analysis/analysis.R" in res["present"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# L1b. Front matter lives in plan/author_information/ -
#      specs/analysis-and-front-matter.md 3, moved one level deeper by
#      specs/author-info-and-inbox-2026-09-24.md 2
# ---------------------------------------------------------------------------

def test_front_matter_in_plan():
    section("plan/author_information/ and the COI inbox")

    root, proj, _ = new_project(field="chemistry")
    try:
        for rel in ("plan/author_information/authors.md",
                    "plan/author_information/affiliations.md",
                    "plan/author_information/conflict_statements/README.md"):
            check(f"{rel} is scaffolded",
                  os.path.isfile(os.path.join(proj, *rel.split("/"))))

        check("plan/ no longer carries them loose",
              not os.path.isfile(os.path.join(proj, "plan", "authors.md"))
              and not os.path.isfile(os.path.join(proj, "plan",
                                                  "affiliations.md")),
              "the folder is what the signed COI forms needed: one place "
              "holds everything about the people on the paper")

        st = ms.source_text_dir(proj)
        for name in ("authors.md", "affiliations_and_funding.md",
                     "affiliations.md"):
            check(f"source_text/ no longer carries {name}",
                  not os.path.isfile(os.path.join(st, name)),
                  "source_text_rN is renamed forward every round; front "
                  "matter belongs to the project, not to a round")

        # The reason the move happened, asserted as behaviour rather than as a
        # comment: manuscript.py has to find them where scaffold.py put them.
        check("manuscript.py resolves the author list to the folder",
              ms.fm_rel(proj, "authors")
              == "plan/author_information/authors.md",
              ms.fm_rel(proj, "authors"))
        check("manuscript.py resolves affiliations to the folder",
              ms.fm_rel(proj, "affiliations")
              == "plan/author_information/affiliations.md",
              ms.fm_rel(proj, "affiliations"))

        # The forms inbox ships with its instruction and nothing else. A file
        # in it means an author returned one, so a second scaffolded file
        # would read as a returned form for as long as nobody looked.
        coi = ms.conflict_dir(proj)
        check("conflict_statements/ ships holding no FILE but its README",
              sorted(f for f in os.listdir(coi)
                     if os.path.isfile(os.path.join(coi, f))) == ["README.md"],
              str(sorted(os.listdir(coi))))
        check("...and the blank form has a folder of its own beside it",
              os.path.isfile(os.path.join(ms.blank_coi_dir(proj),
                                          "README.md")),
              "a blank form among the signed ones is counted as a signed one")
        readme = read(proj,
                      "plan/author_information/conflict_statements/README.md")
        check("...which says to name each form with the author's initials",
              "initials" in readme)
        check("...and that the printed statement is NOT what goes there",
              "affiliations.md" in readme,
              "the Competing Interests sentence is prose and stays in the "
              "end matter; these files are the paperwork behind it")
        check("...and the engine counts the forms without opening one",
              ms.conflict_statements(proj)["forms"] == [],
              "a signed disclosure is a PDF from a publisher's portal; a "
              "wrong reading of one would be a claim about somebody's "
              "declared financial interests")

        text = read(proj, "plan/author_information/authors.md")
        # Blank is where the file STARTS, not where it should sit. The
        # template used to say "blank is a normal project", which reads as
        # permission to leave it - and being asked for a coauthor's ORCID
        # mid-round is the cost of that reading.
        check("the author list says blank is the starting state",
              "not where you stay" in text, text[:0])
        check("...and says to fill it in before the writing engine runs",
              "before you run the writing engine" in text)
        check("...and calls being asked a backstop, not the plan",
              "backstop, not the\nplan" in text or "backstop, not the plan"
              in text.replace("\n", " "))
        check("and says why it is not in the round folder",
              "renamed forward" in text)

        aff = read(proj, "plan/author_information/affiliations.md")
        check("affiliations.md points at authors.md by its new name",
              "affiliations_and_funding.md" not in aff and
              "affiliations_and_funding.md" not in text,
              "both files cross-reference each other")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# L2. drafts/rough_draft.md - specs/setup-project-directory.md 12
# ---------------------------------------------------------------------------

def test_rough_draft_md():
    section("drafts/rough_draft.md")

    root, proj, _ = new_project(field="chemistry")
    try:
        path = os.path.join(proj, "drafts", "rough_draft.md")
        check("it is scaffolded", os.path.isfile(path))
        check("it sits beside references.bib, not inside source_text_rN/",
              os.path.isfile(os.path.join(proj, "drafts", "references.bib"))
              and not [d for d in os.listdir(os.path.join(proj, "drafts"))
                       if d.startswith("source_text")
                       and os.path.isfile(os.path.join(proj, "drafts", d,
                                                       "rough_draft.md"))],
              sorted(os.listdir(os.path.join(proj, "drafts"))))

        # `source_text_dest()` rewrites `drafts/source_text/...` forward into
        # the round the project sits at. The rough draft belongs to the
        # project rather than to a round, so it must come through unchanged -
        # at r1 and at r8. A rough draft renamed to `rough_draft_r8.md` is a
        # file the engine reports as MISSING while the paper sits in it.
        for rnd in (1, 8):
            check("source_text_dest leaves the rough draft alone at r%d" % rnd,
                  sc.source_text_dest(proj, "drafts/rough_draft.md", rnd)
                  == "drafts/rough_draft.md",
                  sc.source_text_dest(proj, "drafts/rough_draft.md", rnd))
        moved = os.path.join(proj, "drafts", "source_text_r8")
        os.rename(os.path.join(proj, "drafts", "source_text_r1"), moved)
        check("...and still at r8 on a project whose folder says r8",
              sc.source_text_dest(proj, "drafts/rough_draft.md")
              == "drafts/rough_draft.md")
        check("while a source text entry IS rewritten forward",
              sc.source_text_dest(proj, "drafts/source_text/methods.md")
              == "drafts/source_text_r8/methods.md",
              sc.source_text_dest(proj, "drafts/source_text/methods.md"))
        os.rename(moved, os.path.join(proj, "drafts", "source_text_r1"))

        text = read(proj, "drafts/rough_draft.md")

        # The instruction block MUST be an HTML comment - analysis.md's
        # reason exactly, and here it is doubly load-bearing: this file's
        # whole purpose is that its prose gets copied into the paper.
        body = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
        check("every instruction is inside an HTML comment",
              not [ln for ln in body.splitlines()
                   if ln.strip() and not ln.startswith("#")],
              [ln for ln in body.splitlines()
               if ln.strip() and not ln.startswith("#")])

        # Content-tested emptiness, the analysis.md computation exactly:
        # comments stripped, headings stripped, what is left is the file. An
        # untouched stub has to be indistinguishable from no file at all.
        empty = re.sub(r"^#.*$", "", body, flags=re.M).strip()
        check("an untouched stub is EMPTY by content, not by existence",
              empty == "", empty[:120])

        heads = [ln[3:].strip() for ln in body.splitlines()
                 if ln.startswith("## ")]
        check("the six section headings survive outside the comment",
              len(heads) == 6, heads)
        for want in ("Title and abstract", "Introduction", "Methods",
                     "Results", "Discussion", "Conclusions"):
            check("...including %r" % want, want in heads, heads)
        check("it says an empty one is a normal project",
              "OPTIONAL" in text and "normal project" in text)
        check("it names the two modules that never read it",
              "statistics check" in text and "abstract" in text)
        check("it says unmeasured numbers are kept and flagged",
              "kept and flagged" in text)

        res = sc.check(proj)
        check("check() lists it as present",
              "drafts/rough_draft.md" in res["present"], res["missing"])
        check("and an empty one is never reported as unfilled",
              not [m for m in res["missing"] if "rough_draft" in m],
              res["missing"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# M. resources/ -> methods_facts.yml - specs/setup-project-directory.md 10.5
# ---------------------------------------------------------------------------

FIXTURE_INSTRUMENTS = '''# Instruments

## The documentation section
<!-- not-an-instrument -->

```yaml
example_key: this must never be offered as an instrument
```

## Helios 5 UX (dual-beam SEM/FIB)

- **What it is for.** Cross-sections and site-specific lift-out.
- **What it cannot do.** No cryo stage below 100 K.
- **What goes in a methods paragraph.** Manufacturer, model, the
  manufacturer's city and country, and every setting that changes the result.

```yaml
instrument_make: Thermo Fisher Scientific
instrument_model: Helios 5 UX
instrument_city: Waltham, MA, USA
accelerating_voltage_kV: 5
beam_current_nA: 0.1
detector: ETD (secondary electron)
working_distance_mm: 4
```

## Bruker D8 (powder XRD)

```yaml
xrd_make: Bruker
xrd_model: D8 Advance
```

## [TK: instrument 3]

- **What it is for.** [TK]

```yaml
some_key: [TK]
```

## Prose only (no yaml half)

- **Standard settings.** Whatever the operator usually uses.
'''


def _resources_fixture(root):
    d = os.path.join(root, "resources")
    os.makedirs(d, exist_ok=True)
    with io.open(os.path.join(d, "instruments.md"), "w",
                 encoding="utf-8", newline="\n") as fh:
        fh.write(FIXTURE_INSTRUMENTS)
    return d


def test_prefill():
    section("resources/ -> methods_facts.yml (offered, never automatic)")

    root, proj, _ = new_project(field="chemistry")
    try:
        res_dir = _resources_fixture(root)
        parsed = sc.parse_instrument_blocks(
            os.path.join(res_dir, "instruments.md"))
        names = [b["name"] for b in parsed["blocks"]]
        check("the two real instruments are found",
              names == ["Helios 5 UX (dual-beam SEM/FIB)",
                        "Bruker D8 (powder XRD)"], names)
        check("a [TK:] block is skipped",
              any("[TK: instrument 3]" in n for n in parsed["notes"]),
              parsed["notes"])
        check("a documentation section is never offered as an instrument",
              "The documentation section" not in names,
              "it says so itself with the not-an-instrument marker")
        check("a block with no yaml half is reported rather than guessed at",
              any("names no keys" in n for n in parsed["notes"]),
              parsed["notes"])
        check("and the note says a sentence about what to record is not a "
              "record",
              any("is not a" in n and "record" in n for n in parsed["notes"]))
        keys = parsed["blocks"][0]["keys"]
        check("a key carrying units is read, capitals and all",
              "accelerating_voltage_kV" in keys and "beam_current_nA" in keys,
              sorted(keys))
        check("all seven keys are read", len(keys) == 7, sorted(keys))

        facts = os.path.join(proj, "data", "methods_facts.yml")
        before = read(proj, "data/methods_facts.yml")

        dry = sc.prefill(proj, ["Helios 5 UX (dual-beam SEM/FIB)"],
                         dry_run=True, resources=res_dir)
        check("a dry run writes nothing", read(proj, "data/methods_facts.yml") == before)
        check("and still reports what it would write",
              dry["written"] and dry["written"][0]["keys"], dry["written"])

        res = sc.prefill(proj, ["Helios 5 UX (dual-beam SEM/FIB)"],
                         resources=res_dir)
        text = read(proj, "data/methods_facts.yml")
        check("the original file is untouched above the block",
              text.startswith(before.rstrip("\n")[:200]))
        check("the block was appended", len(text) > len(before))

        block_keys = [k for k in parsed["blocks"][0]["keys"]]
        check("every key arrives COMMENTED OUT",
              all(f"# {k}:" in text for k in block_keys),
              [k for k in block_keys if f"# {k}:" not in text])
        check("and no key arrives uncommented",
              not [k for k in block_keys
                   if re.search(r"^%s:" % re.escape(k), text, re.M)],
              [k for k in block_keys
               if re.search(r"^%s:" % re.escape(k), text, re.M)])
        check("the block says where it came from, AND which layer",
              "drop-zone:instruments.md" in text
              and "Helios 5 UX" in text,
              [ln for ln in text.splitlines() if "prefilled from" in ln])
        check("and the layer line is not mistakable for a methods key",
              "source" not in ms._unconfirmed_prefill(text).get(
                  "Helios 5 UX (dual-beam SEM/FIB)", []),
              "`# source: ...` under the marker reads as a prefilled key")
        check("and carries the date",
              re.search(r"\d{4}-\d{2}-\d{2}", text) is not None)
        check("and says a commented value counts as MISSING",
              "counts as MISSING" in text)

        # Nothing downstream may read a commented value as a value.
        check("scaffold's own reader does not count it",
              not (sc.confirmed_keys(text) & set(block_keys)),
              sorted(sc.confirmed_keys(text) & set(block_keys)))
        check("manuscript.py's reader does not count it either",
              not (set(ms._recorded_fields(text)) & set(block_keys)),
              sorted(set(ms._recorded_fields(text)) & set(block_keys)))
        check("and manuscript.py names them as unconfirmed",
              sorted(ms._unconfirmed_prefill(text).get(
                  "Helios 5 UX (dual-beam SEM/FIB)", [])) == sorted(block_keys),
              ms._unconfirmed_prefill(text))

        # A re-run must not write it twice. The marker line is the key, and it
        # has parentheses in it, which is what broke the first version.
        again = sc.prefill(proj, ["Helios 5 UX (dual-beam SEM/FIB)"],
                           resources=res_dir)
        check("a re-run does not write the block twice",
              again["already"] == ["Helios 5 UX (dual-beam SEM/FIB)"],
              again)
        check("and the file gained nothing", read(proj, "data/methods_facts.yml") == text)
        check("the marker survives a name containing parentheses",
              sc.prefilled_blocks(read(proj, "data/methods_facts.yml"))
              == ["Helios 5 UX (dual-beam SEM/FIB)"],
              sc.prefilled_blocks(read(proj, "data/methods_facts.yml")))

        # A confirmed value outranks a facility default, always.
        with io.open(facts, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("\nxrd_make: Rigaku\n")
        res = sc.prefill(proj, ["Bruker D8 (powder XRD)"], resources=res_dir)
        check("a key this project already records is left alone",
              res["written"][0]["left_alone"] == ["xrd_make"], res["written"])
        check("and the other key is still offered",
              res["written"][0]["keys"] == ["xrd_model"], res["written"])
        check("the confirmed value is untouched",
              "\nxrd_make: Rigaku\n" in read(proj, "data/methods_facts.yml"))
        check("and the block records that it did not overwrite it",
              "already records" in read(proj, "data/methods_facts.yml"))

        check("an unknown block name is reported, not ignored",
              sc.prefill(proj, ["No Such Instrument"],
                         resources=res_dir)["unknown"] == ["No Such Instrument"],
              "a typo would otherwise look like a facility record with "
              "nothing in it")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_prefill_absent_resources():
    section("an absent resources/ folder is not a scaffold defect")

    root, proj, _ = new_project(field="chemistry")
    root2, proj2, _ = new_project(field="chemistry")
    try:
        nowhere = os.path.join(root, "no_such_resources")
        res = sc.prefill(proj, ["Anything"], resources=nowhere)
        check("it refuses nothing and writes nothing",
              res["written"] == [] and res["available"] == [], res)
        check("and says the absence is not a defect",
              any("not a scaffold defect" in n for n in res["notes"]),
              res["notes"])

        a = sc.check(proj)
        b = sc.check(proj2)
        check("a project with no prefill scaffolds identically",
              a["missing"] == b["missing"] and a["present"] == b["present"])
        check("and check() reports nothing about resources/",
              not [m for m in a["missing"] if "resources" in m.lower()],
              a["missing"])
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(root2, ignore_errors=True)


def test_shipped_resources_is_a_drop_zone():
    """specs/lab-resource-pack.md 1.2 and 4.5: the engine ships resources/
    EMPTY, holding one README, because the only facility content the engine
    can ship safely is none. An `instruments.md` with a real detector model in
    it reaches every lab that installs the engine as if it were theirs, and
    prefill offers it with a straight face.

    This is the guarantee stated as a test rather than as a paragraph, because
    the failure mode is a file being added back with good intentions."""
    section("the shipped resources/ is an empty drop-zone")

    shipped = sc.resources_dir()
    check("the folder still ships", os.path.isdir(shipped), shipped)

    readme = os.path.join(shipped, "README.md")
    check("and holds a README saying what belongs in it",
          os.path.isfile(readme), readme)

    stray = sorted(f for f in os.listdir(shipped)
                   if f != "README.md" and not f.startswith("."))
    check("and NOTHING else - no facility content ships with the engine",
          stray == [],
          f"{stray} would reach every lab as if it were theirs")

    text = read(shipped, "README.md")
    check("the README names the two reserved filenames",
          "instruments.md" in text and "bootcamp.md" in text)
    check("and states the /plugin update hazard",
          "/plugin update" in text and "~/.paper-engine/resources" in text,
          "4.5: a file dropped into an installed plugin's resources/ is "
          "deleted by the next update, and nothing warns")
    check("and names the one-command rescue",
          "--adopt" in text)

    # 4.5's guarantee, stated where prefill can hear it: an empty folder is
    # not an error, and prefill on the SHIPPED path says so rather than
    # reporting a defect.
    parsed = sc.parse_instrument_blocks(os.path.join(shipped,
                                                     "instruments.md"))
    check("prefill reads the shipped folder and finds nothing",
          parsed["blocks"] == [], parsed["blocks"])
    check("and calls the absence not a defect",
          any("not a scaffold defect" in n for n in parsed["notes"]),
          parsed["notes"])


def test_proposed_methods_report():
    section("check() reports data/methods_proposed.yml")

    root, proj, _ = new_project(field="chemistry")
    try:
        res = sc.check(proj)
        check("absent is absent, and not a gap",
              res["proposed_methods"] == {"present": False, "entries": 0,
                                          "superseded": []},
              res["proposed_methods"])

        path = os.path.join(proj, "data", "methods_proposed.yml")
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("acquisition:\n"
                     "  total_dose_e_per_A2:\n"
                     "    value: 50\n"
                     "    source: \"PMID 22267509 - Methods\"\n"
                     "    why: \"closest published tomography\"\n"
                     "sample_prep:\n"
                     "  glow_discharge:\n"
                     "    value: \"25 mA, 30 s, air\"\n"
                     "    source: \"Resources/SOPs/stain.md\"\n"
                     "    why: \"the lab's own setting\"\n")
        res = sc.check(proj)
        check("it is reported as present with its entry count",
              res["proposed_methods"]["present"]
              and res["proposed_methods"]["entries"] == 2,
              res["proposed_methods"])
        check("nothing is superseded yet",
              res["proposed_methods"]["superseded"] == [])

        facts = os.path.join(proj, "data", "methods_facts.yml")
        with io.open(facts, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("\ntotal_dose_e_per_A2: 42\n")
        res = sc.check(proj)
        check("a key now recorded for real is named as probably un-deleted",
              res["proposed_methods"]["superseded"] == ["total_dose_e_per_A2"],
              res["proposed_methods"])
        check("and the project is still reported as scaffolded",
              res["scaffolded"],
              "a stale proposal is worth naming, not an error")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# N. float lint - specs/setup-project-directory.md 5.7.5
# ---------------------------------------------------------------------------

BAD_FIGURE_R = '''source(here::here("plan", "setup.R"))

BUILT <- FALSE

d <- readr::read_csv(here::here("data", "raw", "yields.csv"))
p <- ggplot(d, aes(x = bogus_column, y = yield_pct)) + geom_point()
ggsave("figure.png", p, width = 3.3, height = 2.5)
'''

GOOD_FIGURE_R = '''source(here::here("plan", "setup.R"))

d <- readr::read_csv(here::here("data", "raw", "yields.csv"))
p <- ggplot(d, aes(x = temperature_C, y = yield_pct)) + geom_point()
save_float(p, "Figure 1", panels = 1)
'''


def test_float_lint():
    section("float lint - three traps, reported and never gated")

    root, proj, _ = new_project(field="chemistry")
    try:
        # (0) A FRESH SCAFFOLD IS SILENT. The slots hold a runnable template
        # that references example columns on purpose, and a scaffold whose
        # first run is full of warnings teaches people to ignore warnings.
        res = sc.float_lint(proj)
        check("a fresh scaffold produces no findings",
              sum(len(f["findings"]) for f in res["floats"]) == 0,
              [(f["label"], f["findings"]) for f in res["floats"]])
        check("and says the slots are still the pristine template",
              all(f["pristine"] for f in res["floats"]),
              [(f["label"], f["pristine"]) for f in res["floats"]])
        check("every float folder is reported", len(res["floats"]) >= 4,
              len(res["floats"]))

        # A FRESH CONTRACT DEFINES NOTHING, and that is unchecked rather
        # than clean - the scaffolded data_contract.md is an empty table.
        check("a fresh contract defines no columns",
              res["contract_columns"] == [], res["contract_columns"])
        check("and the lint says so rather than reporting every column as "
              "undefined",
              res["contract_unchecked"] is True)
        check("in as many words",
              any("unchecked, not clean" in n for n in res["notes"]),
              res["notes"])

        # Filled in, it is read - and only the COLUMN table is read. The file
        # also ships a kind-vocabulary table (`continuous`, `count`,
        # `proportion`), and reading every first cell harvested those as
        # column names: measured on a fresh scaffold, eight "columns", none of
        # them a column, and every real column then reported undefined.
        contract = read(proj, "data/data_contract.md")
        rows = "\n".join(["| temperature_C | continuous | C | 20-400 | |",
                          "| yield_pct | proportion | % | 0-100 | |"])
        write(proj, "data/data_contract.md",
              contract.replace("| | | | | |", rows))
        cols = sc.contract_columns(proj)
        check("the column table is read",
              sorted(cols) == ["temperature_C", "yield_pct"], sorted(cols))
        check("and the kind vocabulary is not mistaken for columns",
              sorted(c for c in cols
                     if c in ("continuous", "count", "proportion",
                              "categorical", "ordinal", "identifier",
                              "datetime")) == [],
              sorted(cols))

        # (1) trap 2 - ggsave() where save_float() was required.
        write(proj, "plan/figures/Fig01/figure.R", BAD_FIGURE_R)
        res = sc.float_lint(proj, "Figure 1")
        rules = [f["rule"] for f in res["floats"][0]["findings"]]
        check("only the named float is linted", len(res["floats"]) == 1,
              [f["label"] for f in res["floats"]])
        check("ggsave without save_float is an error",
              "ggsave_instead_of_save_float" in rules, rules)
        sev = [f["severity"] for f in res["floats"][0]["findings"]
               if f["rule"] == "ggsave_instead_of_save_float"]
        check("and it is an error, not a warning", sev, ["error"])
        detail = " ".join(f["detail"] for f in res["floats"][0]["findings"])
        check("the finding says why it matters",
              "watermark never fires" in detail, detail)

        # (2) trap 8 - a column the contract does not define, BY NAME ONLY.
        check("an undefined column is a warning",
              "column_not_in_contract" in rules, rules)
        check("and it names the column", "bogus_column" in detail, detail)
        check("a column the contract DOES define is not flagged",
              "yield_pct" not in detail, detail)
        check("and the finding says it is a name comparison and nothing more",
              "name comparison and nothing more" in detail, detail)

        # (3) trap 7 - BUILT <- FALSE on a float whose caption has a claim.
        # It takes BOTH facts, so on a slot with no claim it is silent.
        check("BUILT <- FALSE alone is not a finding",
              "built_false_with_a_written_claim" not in rules,
              "there is no claim in captions.md yet, so the slot is honestly "
              "unbuilt")
        cap = read(proj, "plan/captions.md").rstrip("\n")
        write(proj, "plan/captions.md",
              cap + "\n\n## Figure 1 — Fig01\n\n"
                    "**Yield rises with growth temperature.**\n\n"
                    "Yield against temperature for four catalysts.\n")
        res = sc.float_lint(proj, "Figure 1")
        rules = [f["rule"] for f in res["floats"][0]["findings"]]
        check("with a written claim, BUILT <- FALSE becomes an error",
              "built_false_with_a_written_claim" in rules, rules)
        check("the claim is reported, because it is the specification",
              res["floats"][0]["claim"]
              == "Yield rises with growth temperature.",
              res["floats"][0]["claim"])

        # A clean script clears every finding.
        write(proj, "plan/figures/Fig01/figure.R", GOOD_FIGURE_R)
        res = sc.float_lint(proj, "Figure 1")
        check("a clean script has no findings",
              res["floats"][0]["findings"] == [],
              res["floats"][0]["findings"])

        # (4) IT IS NEVER A GATE. Every finding is fixed by editing a script
        # the user owns, and a lint that refuses teaches people to skip the
        # render - the same rule prose.py's `voice` and `metaprose` follow.
        write(proj, "plan/figures/Fig02/figure.R", BAD_FIGURE_R)
        run = subprocess.run(
            [sys.executable, ENGINE, "float", "lint", proj],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace")
        check("the CLI exits 0 even with findings", run.returncode == 0,
              run.stdout[-300:])
        check("and says so in as many words",
              "never a gate" in run.stdout or "lint that gates" in run.stdout,
              run.stdout[-200:])
        run = subprocess.run(
            [sys.executable, ENGINE, "float", "lint", proj, "--json"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace")
        check("--json emits one object", run.stdout.strip().startswith("{"),
              run.stdout[:80])
        payload = json.loads(run.stdout)
        check("and carries the findings",
              any(f["findings"] for f in payload["floats"]), True)

        # (5) An unknown float name is reported rather than silently empty.
        res = sc.float_lint(proj, "Figure 99")
        check("an unmatched --float is reported",
              res["floats"] == [], res["floats"])
        check("and says how to list them",
              any("float list" in n for n in res["notes"]), res["notes"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_float_lint_stale_art():
    section("float lint - stale panel art, by content hash and never mtime")

    root, proj, _ = new_project(field="chemistry")
    try:
        fdir = os.path.join(proj, "plan", "figures", "Fig01")
        pptx = os.path.join(fdir, "panelA.pptx")
        with open(pptx, "wb") as fh:
            fh.write(b"PK\x03\x04 not really a pptx, but it hashes")
        res = sc.float_lint(proj, "Figure 1")
        art = res["floats"][0]["stale_art"]
        check("art with no .png is reported as never rendered",
              [a["state"] for a in art] == ["never rendered"], art)

        with open(os.path.join(fdir, "panelA.png"), "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")
        res = sc.float_lint(proj, "Figure 1")
        art = res["floats"][0]["stale_art"]
        check("a .png with no render state is reported, not assumed good",
              [a["state"] for a in art] == ["unknown"], art)

        digest = hashlib.sha256(open(pptx, "rb").read()).hexdigest()
        write(proj, "plan/figures/Fig01/.render_state.json",
              json.dumps({"panelA.pptx": {"sha256": digest}}))
        res = sc.float_lint(proj, "Figure 1")
        check("a recorded hash that matches is not stale",
              res["floats"][0]["stale_art"] == [],
              res["floats"][0]["stale_art"])

        # The hash is the signal, and mtime is not: OneDrive rewrites mtimes
        # on sync, and a graphic wrongly declared current is the whole failure
        # this was written against.
        with open(pptx, "ab") as fh:
            fh.write(b" edited")
        res = sc.float_lint(proj, "Figure 1")
        art = res["floats"][0]["stale_art"]
        check("an edited .pptx is stale",
              [a["state"] for a in art] == ["stale"], art)
        os.utime(pptx, (0, 0))
        res = sc.float_lint(proj, "Figure 1")
        check("and still stale after its mtime is pushed backwards",
              [a["state"] for a in res["floats"][0]["stale_art"]]
              == ["stale"],
              "the signal is the content hash, never the modification time")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_drawn_floats():
    section("A float the user asked for as a graphic is recorded as one "
            "(item 82)")

    root, proj, _ = new_project(field="chemistry")
    try:
        made = sc.float_new(proj, "figure", slug="mechanism", art="drawn")
        check("a drawn float is created like any other",
              made["created"] and made["art"] == "drawn", made)
        body = read(proj, made["script"])
        check("...and its own script records that it is drawn",
              sc.DRAWN_MARKER in body, body[:120])
        check("...and the next step names the skill that draws it, not R",
              "graphic_figure.py add" in (made.get("next") or ""),
              made.get("next"))

        plotted = sc.float_new(proj, "figure", slug="yield")
        check("a plotted float is unchanged, and carries no marker",
              sc.DRAWN_MARKER not in read(proj, plotted["script"]))
        check("...and is offered no drawing step", plotted.get("next") is None,
              plotted.get("next"))

        # The cost is not a worse picture - it is a picture the user cannot
        # change, and it renders perfectly, so nothing but this reports it.
        res = sc.float_lint(proj, made["label"])
        rules = [f["rule"] for f in res["floats"][0]["findings"]]
        check("a float marked drawn with no PowerPoint source is reported",
              "drawn_float_has_no_art" in rules, rules)
        check("...as an error, because the float cannot be edited by whoever "
              "asked for it",
              [f["severity"] for f in res["floats"][0]["findings"]
               if f["rule"] == "drawn_float_has_no_art"] == ["error"], rules)

        fdir = os.path.join(proj, *made["dir"].split("/"))
        with open(os.path.join(fdir, "panelA_source.pptx"), "wb") as fh:
            fh.write(b"PK stand-in")
        res = sc.float_lint(proj, made["label"])
        rules = [f["rule"] for f in res["floats"][0]["findings"]]
        check("...and silent once the art is there",
              "drawn_float_has_no_art" not in rules, rules)
        check("...with the float itself reported as drawn",
              res["floats"][0]["art"] == "drawn", res["floats"][0]["art"])

        # ...and the other direction: art nobody recorded.
        odir = os.path.join(proj, *plotted["dir"].split("/"))
        with open(os.path.join(odir, "panelA_source.pptx"), "wb") as fh:
            fh.write(b"PK stand-in")
        res = sc.float_lint(proj, plotted["label"])
        check("panel art with no marker is reported too, as a warning",
              [(f["rule"], f["severity"])
               for f in res["floats"][0]["findings"]
               if f["rule"] == "art_without_the_drawn_marker"]
              == [("art_without_the_drawn_marker", "warning")],
              [f["rule"] for f in res["floats"][0]["findings"]])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_refine_figure_skill():
    section("the refine-figure skill")

    path = os.path.join(os.path.dirname(ENGINE), "..", "skills",
                        "refine-figure", "SKILL.md")
    check("it exists", os.path.isfile(path))
    text = io.open(path, encoding="utf-8").read()
    check("it has frontmatter", text.startswith("---\nname: refine-figure"))
    check("it names the engine command it leans on",
          "scaffold.py float lint" in text)
    check("it says the lint is never a gate", "never a gate" in text)
    check("it says to read the claim first", "Read the claim" in text)
    check("it carries all eight traps",
          len([ln for ln in text.splitlines()
               if ln.startswith("| ") and "|" in ln[2:]]) >= 9,
          "eight rows plus a header")
    check("it forbids tuning the plot to the mock generator",
          "the plot to the mock generator" in text)
    check("it forbids removing a mock-data safeguard",
          "removes a watermark" in text)
    check("it says a weak claim is not repaired in the plot",
          "not where a weak claim" in text)
    check("it does not claim to check meaning",
          "cannot check meaning" in text or "names and nothing else" in text)
    check("it says R is not on PATH here", "not* on `PATH`" in text
          or "not on `PATH`" in text)


# ---------------------------------------------------------------------------
# N. expects_data - the third axis (spec 13.1)
# ---------------------------------------------------------------------------

def test_expects_data():
    section("expects_data shapes the tree and the offers (spec 13.1)")

    root = tempfile.mkdtemp(prefix="scaffold_expects_")
    try:
        def build(name, expects, kind="research"):
            proj = os.path.join(root, name)
            values = sc.build_values(project_name=name, field="chemistry",
                                     paper_kind=kind, expects_data=expects)
            res = sc.scaffold(proj, values, paper_kind=kind)
            return proj, res

        def here(proj, rel):
            return os.path.exists(os.path.join(proj, *rel.split("/")))

        # --- the default is what the engine has always made ---------------
        base, res_base = build("rows", "measurements")
        check("measurements is the default vocabulary value",
              sc.EXPECTS_DATA_DEFAULT == "measurements",
              sc.EXPECTS_DATA_DEFAULT)
        for rel in ("data/mock_data", "data/raw", "data/raw_images",
                    "data/analysis/analysis.R", "data/methods_facts.yml",
                    "data/data_contract.md"):
            check(f"measurements keeps {rel}", here(base, rel), True)
        check("...and says so in the result",
              res_base["expects_data"] == "measurements",
              res_base["expects_data"])

        # --- images: no mock folder, everything else stays ------------------
        img, res_img = build("pictures", "images")
        check("images drops data/mock_data/", not here(img, "data/mock_data"),
              True)
        for rel in ("data/raw_images", "data/analysis/analysis.R",
                    "data/methods_facts.yml", "data/raw"):
            check(f"...but keeps {rel} - an imaging project still has numbers",
                  here(img, rel), True)

        # --- none: no data/ at all -----------------------------------------
        non, res_non = build("perspective", "none")
        check("none drops data/ entirely", not here(non, "data"), True)
        check("...and obsolete/analysis/ with it",
              not here(non, "obsolete/analysis"), True)
        check("...and keeps drafts/", here(non, "drafts"), True)
        check("...and keeps the float slots - a theory paper has figures",
              here(non, "plan/figures/Fig04"), True)

        # --- the offers, which the skill reads rather than re-deriving ------
        check("measurements offers mock data",
              res_base["offers"]["mock_data"] is True, res_base["offers"])
        check("...and mock floats",
              res_base["offers"]["mock_floats"] is True)
        check("...and not image placeholders",
              res_base["offers"]["image_placeholders"] is False)
        check("images offers image placeholders and NOT mock data",
              res_img["offers"] == {"mock_data": False, "mock_floats": False,
                                    "image_placeholders": True,
                                    "expects_data": "images"},
              res_img["offers"])
        check("none offers neither",
              not any(v for k, v in res_non["offers"].items()
                      if k != "expects_data"), res_non["offers"])
        check("a review is never offered mock data, whatever it expects",
              sc.offers_for("measurements", "review")["mock_data"] is False)

        # --- an unknown or absent value reads as the default ---------------
        for raw in ("", None, "MEASUREMENTS", "pictures", 7):
            got = sc.expects_data_value(raw)
            check(f"expects_data_value({raw!r}) is a vocabulary value",
                  got in sc.EXPECTS_DATA, got)
        check("an old project.yml with no key reads as measurements",
              sc.expects_data_value(None) == "measurements")
        check("...and the vocabulary is case-sensitive, so MEASUREMENTS "
              "normalises rather than failing",
              sc.expects_data_value("MEASUREMENTS") == "measurements")

        # --- project.yml records it, so nothing re-asks --------------------
        yml = read(img, "project.yml")
        check("project.yml records the expectation",
              "expects_data: images" in yml,
              [l for l in yml.splitlines() if "expects_data" in l])
        check("...and the flat parser reads it back",
              sc.read_project_yml(img).get("expects_data") == "images",
              sc.read_project_yml(img).get("expects_data"))

        # --- check() stops calling a declared absence a gap ----------------
        chk = sc.check(non)
        check("check() on a no-data project reports nothing missing",
              chk["missing"] == [], chk["missing"])
        check("...and NAMES what is out of scope rather than omitting it",
              "data/analysis/analysis.R" in chk["out_of_scope"],
              chk["out_of_scope"])
        check("...and still calls the project scaffolded",
              chk["scaffolded"] is True)
        check("...and echoes the expectation it judged by",
              chk["expects_data"] == "none", chk["expects_data"])
        chk_rows = sc.check(base)
        check("a measurements project reports nothing out of scope",
              chk_rows["out_of_scope"] == [], chk_rows["out_of_scope"])

        # The case that predates the key: a review was reporting its absent
        # analysis pipeline as MISSING on every single run.
        rev, _ = build("a_review", "measurements", kind="review")
        chk_rev = sc.check(rev)
        check("a review no longer reports its absent analysis.R as missing",
              "data/analysis/analysis.R" not in chk_rev["missing"],
              chk_rev["missing"])
        check("...it is out of scope instead",
              "data/analysis/analysis.R" in chk_rev["out_of_scope"])

        # --- the two axes compose by union ---------------------------------
        rn, _ = build("review_no_data", "none", kind="review")
        check("a review that expects no data has no data/ and still has "
              "its corpus", not here(rn, "data/data_contract.md")
              and here(rn, "data/corpus/README.md"), True)

        # --- re-running never deletes what an earlier answer made ----------
        again = sc.scaffold(img, sc.build_values(project_name="pictures",
                                                 expects_data="measurements"),
                            expects_data="measurements")
        check("switching to measurements ADDS the mock folder",
              here(img, "data/mock_data"), True)
        check("...and deletes nothing that was there",
              here(img, "data/raw_images") and here(img, "data/analysis"),
              True)
        del again
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# O. adopt - a folder that already has work in it (spec 13.3)
# ---------------------------------------------------------------------------

MESSY = {
    "run1.csv": "a,b\n1,2\n",
    "run2.csv": "a,b\n1,2\n",
    "overview.dm4": "binary-ish",
    "Smith2019.pdf": "%PDF-1.4",
    "shot.png": "not really a png",
    "thoughts.md": "# what I think\n",
    "refs.bib": "@article{x2020,}\n",
    "Thumbs.db": "",
    "~$draft.docx": "",
    "Raw Data/x.csv": "a\n1\n",
    "lab notes/2026-01.md": "notes\n",
    "lab notes/2026-02.md": "notes\n",
    "seminar slides/talk.pptx": "",
    "old_figs/a.png": "",
    "deep/nested/inner/buried.csv": "a\n1\n",
}


def make_messy(root, files=None):
    proj = os.path.join(root, "existing_work")
    for rel, body in (files or MESSY).items():
        path = os.path.join(proj, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
    return proj


def by_path(res):
    return {e["path"]: e for e in res["extras"]}


def test_adopt():
    section("adopt files an existing folder and proposes, never moves "
            "(spec 13.3)")

    root = tempfile.mkdtemp(prefix="scaffold_adopt_")
    try:
        proj = make_messy(root)
        before = sorted(os.listdir(proj))
        values = sc.build_values(project_name="existing_work",
                                 field="chemistry")
        res = sc.adopt(proj, values)
        got = by_path(res)

        # --- it scaffolds first, so a proposal can name a real destination -
        check("it scaffolds the structure",
              os.path.isdir(os.path.join(proj, "plan", "figures")), True)
        check("...and overwrote nothing that was there",
              all(os.path.exists(os.path.join(proj, b)) for b in before),
              before)

        # --- classification -------------------------------------------------
        check("a .csv is data, and the rules are certain",
              got["run1.csv"]["proposal"] == "data/raw"
              and got["run1.csv"]["action"] == "move",
              got["run1.csv"])
        check("an instrument format is an image, and certain",
              got["overview.dm4"]["proposal"] == "data/raw_images"
              and got["overview.dm4"]["action"] == "move",
              got["overview.dm4"])
        check("a .png is NOT certain - it is a micrograph or an export",
              got["shot.png"]["action"] == "ask",
              got["shot.png"])
        check("a .pdf is not certain either",
              got["Smith2019.pdf"]["action"] == "ask")
        check("a .bib is never moved - merging is a decision",
              got["refs.bib"]["action"] == "ask"
              and "MERGES" in got["refs.bib"]["why"],
              got["refs.bib"]["why"])
        check("prose at the root is a question",
              got["thoughts.md"]["action"] == "ask")

        # --- directory names: whole name, then word by word -----------------
        check("'Raw Data' matches rawdata on the whole name and is certain",
              got["Raw Data"]["proposal"] == "data/raw"
              and got["Raw Data"]["action"] == "move",
              got["Raw Data"])
        check("'lab notes' matches on the word 'notes'",
              got["lab notes"]["proposal"] == ""
              and got["lab notes"]["action"] == "ask",
              got["lab notes"])
        check("'seminar slides' matches on 'slides' and is left alone",
              got["seminar slides"]["action"] == "keep",
              got["seminar slides"])
        check("'old_figs' reads as retired work, not as figures",
              got["old_figs"]["proposal"] == "obsolete",
              got["old_figs"])
        check("...and retired work is a question, because obsolete/ has bins",
              got["old_figs"]["action"] == "ask")

        # A word match may never reach `clear`, because `images/` is the
        # images and `notes on images/` is not.
        word = sc._classify_dir("notes on images", {"files": 0, "roles": {}})
        check("a one-word match on a longer name never reaches clear",
              word["confidence"] != "clear", word)

        # --- noise is dropped, not reported ---------------------------------
        check("Thumbs.db is never classified", "Thumbs.db" not in got)
        check("a Word lock file is never classified", "~$draft.docx" not in got)

        # --- the manifest's own entries are never classified ----------------
        for own in ("README.md", "CLAUDE.md", "project.yml", "plan", "drafts",
                    "obsolete", ".here"):
            check(f"{own} is the scaffold's own and is not classified",
                  own not in got, got.get(own))

        # --- top level only -------------------------------------------------
        check("it classifies the top level and not what is under it",
              "deep" in got and "deep/nested" not in got, sorted(got))

        # --- and it moved NOTHING -------------------------------------------
        check("nothing was moved without --apply", res["moved"] == [],
              res["moved"])
        check("every file that was there is still where it was",
              all(os.path.exists(os.path.join(proj, b)) for b in before),
              before)
        check("...and the run says so", res["applied"] is False)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_adopt_apply():
    section("--apply moves the certain rows, and only those")

    root = tempfile.mkdtemp(prefix="scaffold_apply_")
    try:
        proj = make_messy(root)
        values = sc.build_values(project_name="existing_work")
        res = sc.adopt(proj, values, apply_moves=True)
        moved = {m["from"]: m["to"] for m in res["moved"]}

        check("the certain rows moved",
              moved.get("run1.csv") == "data/raw/run1.csv", moved)
        check("...including a whole directory",
              moved.get("Raw Data") == "data/raw/Raw Data", moved)
        check("...and the instrument image",
              moved.get("overview.dm4") == "data/raw_images/overview.dm4")
        check("they are really there now",
              os.path.isfile(os.path.join(proj, "data", "raw", "run1.csv")),
              True)
        check("...and really gone from the root",
              not os.path.exists(os.path.join(proj, "run1.csv")), True)

        for never in ("shot.png", "Smith2019.pdf", "refs.bib", "thoughts.md",
                      "lab notes", "old_figs", "seminar slides", "deep"):
            check(f"{never} was NOT moved - it was a question",
                  os.path.exists(os.path.join(proj, never)), never)

        # Nothing is ever deleted, by anything, at any confidence.
        walked = sum(len(f) for _, _, f in os.walk(proj))
        check("the folder still holds every file it did", walked > 0, walked)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_adopt_never_overwrites():
    section("a taken destination name is a conflict, never an overwrite")

    root = tempfile.mkdtemp(prefix="scaffold_conflict_")
    try:
        proj = make_messy(root, {"run1.csv": "NEW\n",
                                 "data/raw/run1.csv": "ORIGINAL\n"})
        values = sc.build_values(project_name="existing_work")
        res = sc.adopt(proj, values, apply_moves=True)
        got = by_path(res)

        check("the clash is reported as a conflict",
              got["run1.csv"]["action"] == "conflict", got["run1.csv"])
        check("...and named in its own list",
              [c["path"] for c in res["conflicts"]] == ["run1.csv"],
              res["conflicts"])
        check("the file that was already there is untouched",
              read(proj, os.path.join("data", "raw", "run1.csv")) ==
              "ORIGINAL\n")
        check("...and the incoming one is still at the root, not renamed",
              read(proj, "run1.csv") == "NEW\n")
        check("nothing was moved at all", res["moved"] == [], res["moved"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_adopt_contradiction():
    section("a proposal into a scope the project declared away is a question")

    root = tempfile.mkdtemp(prefix="scaffold_contra_")
    try:
        proj = make_messy(root, {"run1.csv": "a\n1\n"})
        values = sc.build_values(project_name="existing_work",
                                 expects_data="none")
        res = sc.adopt(proj, values, expects_data="none", apply_moves=True)
        got = by_path(res)
        check("the csv is no longer a certain move",
              got["run1.csv"]["action"] == "ask", got["run1.csv"])
        check("...and the reason names the contradiction",
              "expects_data: none" in got["run1.csv"]["why"],
              got["run1.csv"]["why"])
        check("...and the proposal is withdrawn, not left pointing at "
              "a folder that does not exist",
              got["run1.csv"]["proposal"] == "")
        check("nothing was moved", res["moved"] == [])
        check("...and data/ was never created",
              not os.path.isdir(os.path.join(proj, "data")), True)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# P. survey and extra_sources (spec 13.4)
# ---------------------------------------------------------------------------

def test_survey():
    section("survey finds what the tree does not explain (spec 13.4)")

    root = tempfile.mkdtemp(prefix="scaffold_survey_")
    try:
        proj = make_messy(root)
        sc.scaffold(proj, sc.build_values(project_name="existing_work"))
        res = sc.survey(proj)
        got = {e["path"]: e for e in res["unexpected"]}

        check("a notes folder is found", "lab notes" in got, sorted(got))
        check("...and is marked as something that could be drafted from",
              got["lab notes"]["useful_for"] == ["drafting"],
              got["lab notes"])
        check("...with its file count",
              got["lab notes"]["files"] == 2, got["lab notes"]["files"])
        check("a pdf could feed citations",
              got["Smith2019.pdf"]["useful_for"] == ["citations"],
              got["Smith2019.pdf"])
        check("a csv could feed data",
              got["run1.csv"]["useful_for"] == ["data"])
        check("noise is listed as ignored, never as a finding",
              "Thumbs.db" in res["ignored"] and "Thumbs.db" not in got,
              res["ignored"])
        check("the scaffold's own folders are not findings",
              not ({"plan", "drafts", "obsolete", "data"} & set(got)),
              sorted(got))

        # useful_for is the field the writing engine gates on: an empty one
        # means say nothing.
        with open(os.path.join(proj, "archive.zip"), "w") as fh:
            fh.write("")
        again = {e["path"]: e for e in sc.survey(proj)["unexpected"]}
        check("a .zip is listed but could feed nothing",
              again["archive.zip"]["useful_for"] == [],
              again["archive.zip"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_extra_sources():
    section("an answer is recorded once and never asked again")

    root = tempfile.mkdtemp(prefix="scaffold_extra_")
    try:
        proj = make_messy(root)
        sc.scaffold(proj, sc.build_values(project_name="existing_work"))
        check("a fresh project has recorded no answers",
              sc.read_extra_sources(proj) == [], sc.read_extra_sources(proj))

        out = sc.record_extra_source(proj, "lab notes", "drafting",
                                     "notebook exports")
        check("recording an answer reports what it did",
              out["action"] == "add" and not out["errors"], out)
        check("...and it reads back",
              sc.read_extra_sources(proj) ==
              [{"path": "lab notes", "use": "drafting",
                "note": "notebook exports"}],
              sc.read_extra_sources(proj))

        left = {e["path"] for e in sc.survey(proj)["unexpected"]}
        check("the answered folder stops being asked about",
              "lab notes" not in left, sorted(left))
        check("...and comes back under `recorded` instead",
              "lab notes" in {r["path"] for r in sc.survey(proj)["recorded"]})

        # Recording a NO matters as much as recording a yes.
        sc.record_extra_source(proj, "seminar slides", "ignore")
        left = {e["path"] for e in sc.survey(proj)["unexpected"]}
        check("an ignored folder is not asked about either",
              "seminar slides" not in left, sorted(left))

        # Re-answering replaces; it never leaves the file contradicting itself.
        out = sc.record_extra_source(proj, "lab notes", "citations")
        check("re-answering reports an update", out["action"] == "update", out)
        entries = sc.read_extra_sources(proj)
        check("...and there is exactly one entry for that folder",
              [e["path"] for e in entries].count("lab notes") == 1, entries)
        check("...carrying the new answer",
              [e for e in entries if e["path"] == "lab notes"][0]["use"]
              == "citations", entries)

        # The block must not disturb the flat parser every other reader uses.
        flat = sc.read_project_yml(proj)
        check("project.yml's flat keys still read correctly",
              flat.get("field") == "chemistry" and flat.get("seed") == "1",
              flat)
        check("...and the indented block did not become a flat key",
              "path" not in flat and "use" not in flat, sorted(flat))

        # A recorded source that has gone is reported, not read around.
        shutil.rmtree(os.path.join(proj, "lab notes"))
        rec = {r["path"]: r for r in sc.survey(proj)["recorded"]}
        check("a recorded folder that is gone is reported missing",
              rec["lab notes"]["exists"] is False, rec["lab notes"])
        check("...and one that is still there is not",
              rec["seminar slides"]["exists"] is True)

        bad = sc.record_extra_source(proj, "x", "not_a_role")
        check("an unknown role is refused, and says what is allowed",
              bad["errors"] and "drafting" in bad["errors"][0], bad)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_record_project_key():
    section("an absent project.yml key may be filled in once, never changed")

    root = tempfile.mkdtemp(prefix="scaffold_key_")
    try:
        proj = os.path.join(root, "old_project")
        sc.scaffold(proj, sc.build_values(project_name="old_project"))
        path = os.path.join(proj, "project.yml")

        # Simulate a project.yml written before the key existed.
        text = read(proj, "project.yml")
        text = "\n".join(l for l in text.splitlines()
                         if not l.startswith("expects_data:"))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        check("the key really is absent to start",
              not sc.read_project_yml(proj).get("expects_data"), True)

        out = sc.record_project_key(proj, "expects_data", "images")
        check("an absent key is added", out["action"] == "added", out)
        check("...and reads back",
              sc.read_project_yml(proj).get("expects_data") == "images")
        check("...and the other keys survived",
              sc.read_project_yml(proj).get("field") == "chemistry")

        out = sc.record_project_key(proj, "expects_data", "none")
        check("a key already recorded is NEVER changed",
              out["action"] == "already recorded", out)
        check("...and the recorded value still stands",
              sc.read_project_yml(proj).get("expects_data") == "images")

        missing = sc.record_project_key(os.path.join(root, "nowhere"),
                                        "expects_data", "images")
        check("a project with no project.yml is refused, not created",
              missing["errors"] and not os.path.exists(
                  os.path.join(root, "nowhere")), missing)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Q. Image placeholder floats (spec 13.2)
# ---------------------------------------------------------------------------

IMAGE_BUNDLE = {
    "figures": [
        {"n": 1, "title": "Sample quality",
         "claim": "Particles are monodisperse.",
         "panels": [
             {"file": "panelA_micrograph.png", "title": "Micrograph",
              "describe": "Motion-corrected micrograph, 1.2 um defocus",
              "aspect": 1.0},
             {"file": "panelB_classes", "title": "2D classes",
              "describe": "Top 20 class averages"}]},
        {"n": 2, "title": "Reconstruction",
         "claim": "The map resolves side chains.",
         "panels": [
             {"file": "panelA_map.png", "describe": "Sharpened map"},
             {"file": "panelB_fsc.png"},
             {"file": "panelC_fit.png", "describe": "Model docked"}]},
    ]
}


def test_image_float_specs():
    section("the image-float bundle normalises (spec 13.2)")

    specs = sc.image_float_specs(IMAGE_BUNDLE)
    check("both figures survive", len(specs) == 2, len(specs))
    a, b = specs[0]["panels"][0], specs[0]["panels"][1]
    check("a missing .png extension is added", b["file"] == "panelB_classes.png",
          b["file"])
    check("tags run A, B, C", [p["tag"] for p in specs[1]["panels"]]
          == ["A", "B", "C"], [p["tag"] for p in specs[1]["panels"]])
    check("the aspect survives", a["aspect"] == 1.0, a["aspect"])
    check("a figure with no panels still gets one",
          len(sc.image_float_specs({"figures": [{"n": 1}]})[0]["panels"]) == 1)
    check("a missing claim is a visible placeholder, not an invented claim",
          "<" in sc.image_float_specs({"figures": [{"n": 1}]})[0]["claim"],
          sc.image_float_specs({"figures": [{"n": 1}]})[0]["claim"])

    # Two panels per row: an image squeezed to a third of a column is a
    # thumbnail.
    check("one panel is not composed", sc._image_layout(1) == "p_a")
    check("two panels share a row", sc._image_layout(2) == "p_a | p_b")
    check("three panels are a pair over a single",
          sc._image_layout(3).startswith("(p_a | p_b) /"), sc._image_layout(3))
    check("four panels are two pairs",
          sc._image_layout(4).count("|") == 2, sc._image_layout(4))


def test_image_float_write():
    section("image placeholder floats are written, and are not mock")

    root = tempfile.mkdtemp(prefix="scaffold_imgfloat_")
    try:
        proj = os.path.join(root, "cryo")
        sc.scaffold(proj, sc.build_values(project_name="cryo",
                                          expects_data="images"),
                    expects_data="images")
        res = sc.image_floats(proj, IMAGE_BUNDLE)
        check("two figures written", len(res["written"]) == 2, res["written"])
        check("no errors", not res["errors"], res["errors"])

        fig2 = read(proj, os.path.join("plan", "figures", "Fig02", "figure.R"))
        check("it composes through graphic_panel(), not image_panel(title=)",
              "graphic_panel(" in fig2 and "image_panel(" not in fig2,
              [l for l in fig2.splitlines() if "panel(" in l])
        check("...because an aspect-locked panel's inside title collides",
              "p_a <- graphic_panel(" in fig2)
        check("the description reaches the script",
              'describe = "Sharpened map"' in fig2,
              [l for l in fig2.splitlines() if "describe" in l])
        # Counted on the argument form, not the words: the banner tells the
        # user to delete `describe =` when the image lands, and that mention
        # is prose rather than a third panel.
        check("a panel with no description emits no describe argument",
              'graphic_panel("panelB_fsc.png",' in fig2
              and fig2.count('describe = "') == 2,
              fig2.count('describe = "'))
        check("the height is computed, not left to the chart default",
              "save_float(p, height = image_fig_height(3, per_row = 2))"
              in fig2, [l for l in fig2.splitlines() if "save_float" in l])
        check("the slot is switched on", "BUILT <- TRUE" in fig2)

        # NOT mock, and the whole design rests on that.
        check("no mock marker anywhere", sc.MOCK_MARKER not in fig2)
        check("...and its own marker is there", sc.IMAGE_MARKER in fig2)
        check("the banner says the .docx will build it",
              "NOT mock" in fig2, True)

        fig1 = read(proj, os.path.join("plan", "figures", "Fig01", "figure.R"))
        check("an explicit aspect reaches the script",
              "aspect = 1.0" in fig1,
              [l for l in fig1.splitlines() if "aspect" in l])

        # Captions, claim first, one sentence per panel.
        caps = read(proj, os.path.join("plan", "captions.md"))
        check("a caption block per figure",
              "## Figure 1 \u2014 Fig01" in caps
              and "## Figure 2 \u2014 Fig02" in caps, True)
        check("...claim first",
              "**Particles are monodisperse.**" in caps, True)
        check("...and one sentence per panel, so the paragraph can be "
              "drafted before the image exists",
              "(A) Micrograph: Motion-corrected micrograph, 1.2 um defocus."
              in caps, [l for l in caps.splitlines() if "(A)" in l])
        check("...saying they are placeholders",
              "placeholders until the images land" in caps, True)

        # Every undescribed panel is surfaced rather than quietly rendered
        # as a bare "pending".
        check("the undescribed panel is reported",
              [u["file"] for u in res["undescribed"]] == ["panelB_fsc.png"],
              res["undescribed"])

        # Never overwrite: the rule the whole engine follows.
        again = sc.image_floats(proj, IMAGE_BUNDLE)
        check("a re-run refuses the slots it wrote", not again["written"],
              again["written"])
        check("...and says --force would rebuild them",
              all("--force" in s_["why"] for s_ in again["skipped"]),
              again["skipped"])
        forced = sc.image_floats(proj, IMAGE_BUNDLE, force=True)
        check("--force rebuilds its own", len(forced["written"]) == 2,
              forced["written"])

        # A real figure is never touched, with or without --force.
        real = os.path.join(proj, "plan", "figures", "Fig01", "figure.R")
        with open(real, "w", encoding="utf-8") as fh:
            fh.write("# hand-built\nBUILT <- TRUE\nsave_float(p)\n")
        guarded = sc.image_floats(proj, IMAGE_BUNDLE, force=True)
        check("a real figure is never overwritten, even with --force",
              read(proj, os.path.join("plan", "figures", "Fig01",
                                      "figure.R")) ==
              "# hand-built\nBUILT <- TRUE\nsave_float(p)\n", True)
        check("...and the skip says why",
              any("never overwritten" in s_["why"]
                  for s_ in guarded["skipped"]), guarded["skipped"])

        empty = sc.image_floats(proj, {"figures": []})
        check("an empty bundle errors rather than writing nothing quietly",
              empty["errors"] and "panels" in empty["errors"][0], empty)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_image_panel_helper():
    section("the R helper draws a described grey panel")

    path = os.path.join(_HERE, "..", "tools", "project_template", "plan",
                        "theme", "helpers.R")
    with open(path, "r", encoding="utf-8") as fh:
        txt = fh.read()
    check("image_panel takes a description",
          "image_panel <- function(path, title = NULL, tag = NULL, "
          "describe = NULL," in txt,
          [l for l in txt.splitlines() if "image_panel <- function" in l])
    check("...and an aspect, so the box is the shape the image will be",
          "aspect = 0.75)" in txt)
    check("graphic_panel passes both through",
          "describe = describe" in txt and "aspect = aspect" in txt)
    check("the placeholder is light grey",
          'fill = "grey95"' in txt,
          [l for l in txt.splitlines() if "grey95" in l])
    check("...and falls back to the filename when nothing describes it",
          'paste0("pending: ", filename)' in txt)
    check("image_fig_height exists - a chart grid is short by the title row",
          "image_fig_height <- function(" in txt)


def test_image_float_render(rscript):
    section("image placeholder floats render, and the .docx is NOT refused")

    root = tempfile.mkdtemp(prefix="scaffold_imgrender_")
    try:
        proj = os.path.join(root, "cryo")
        sc.scaffold(proj, sc.build_values(project_name="cryo",
                                          expects_data="images"),
                    expects_data="images")
        res = sc.image_floats(proj, IMAGE_BUNDLE)
        if not check("two figures written", len(res["written"]) == 2):
            return
        rc, out = render(rscript, proj)
        check("render_all.R runs clean", rc == 0, out[-700:] if rc else "")
        check("no script warns", "warning:" not in out, out[-700:])
        check("the preview holds both figures",
              "preview.html   2 floats" in out,
              [l for l in out.splitlines() if "preview.html" in l])

        for n in (1, 2):
            rel = os.path.join("plan", "figures", f"Fig{n:02d}", "figure.png")
            full = os.path.join(proj, rel)
            ok = os.path.isfile(full)
            size = os.path.getsize(full) if ok else 0
            check(f"Fig{n:02d} produced a real .png", ok and size > 10000,
                  f"{size} bytes")

        # The whole design rests on this: nothing is fabricated, so nothing
        # is watermarked and nothing is refused.
        check("no MOCK DATA watermark - nothing here is fabricated",
              "[MOCK DATA]" not in out, out[-500:])
        check("the co-author .docx is BUILT, not refused",
              "REFUSED" not in out and "NOT WRITTEN" not in out,
              [l for l in out.splitlines() if "docx" in l])
        check("...and it really exists",
              os.path.isfile(os.path.join(proj, "plan", "floats",
                                          "figures_and_tables.docx")), True)

        # And a panel whose image arrives stops being a placeholder.
        src = os.path.join(proj, "plan", "figures", "Fig02",
                           "panelA_map.png")
        shutil.copyfile(os.path.join(proj, "plan", "figures", "Fig01",
                                     "figure.png"), src)
        rc2, out2 = render(rscript, proj)
        check("a figure re-renders once a panel image lands", rc2 == 0,
              out2[-700:] if rc2 else "")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_spreadsheet_is_a_question(tmp=None):
    """Item 112: `--apply` moved a grant budget into `data/raw/`."""
    section("A spreadsheet is a question, not a certainty (item 112)")

    root = tempfile.mkdtemp(prefix="scaffold_xlsx_")
    try:
        proj = os.path.join(root, "existing_work")
        os.makedirs(proj, exist_ok=True)
        write(proj, "grant_budget.xlsx", "x")
        write(proj, "run1.csv", "dose,response" + chr(10) + "1,2" + chr(10))
        values = sc.build_values(project_name="existing_work")
        res = sc.adopt(proj, values, apply_moves=True)
        got = by_path(res)

        check("a spreadsheet classifies `likely`, not `clear`",
              got["grant_budget.xlsx"]["confidence"] == "likely",
              got["grant_budget.xlsx"]["confidence"])
        check("...and says why it is a question",
              "budget" in got["grant_budget.xlsx"]["why"],
              got["grant_budget.xlsx"]["why"])
        check("...so --apply did not move it",
              os.path.isfile(os.path.join(proj, "grant_budget.xlsx")))
        check("...and it is not in the moved list",
              not [m for m in res["moved"] if "xlsx" in m["from"]],
              res["moved"])
        check("a .csv is still certain and still moves",
              os.path.isfile(os.path.join(proj, "data", "raw", "run1.csv")))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_case_collision():
    """Item 113. Visible only where the filesystem folds case, so the
    assertions are written to hold on both kinds."""
    section("A case twin satisfies no manifest entry (item 113)")

    root = tempfile.mkdtemp(prefix="scaffold_case_")
    try:
        proj = os.path.join(root, "existing_work")
        os.makedirs(proj, exist_ok=True)
        write(proj, "readme.md", "the author's own one byte")
        values = sc.build_values(project_name="existing_work")
        scaf = sc.scaffold(proj, values)
        chk = sc.check(proj)
        seen = sc.survey(proj)

        folds = not os.path.isfile(os.path.join(proj, "README.md")) or \
            os.path.exists(os.path.join(proj, "README.MD"))
        if not folds and "README.md" in scaf["created"]:
            # A case-sensitive filesystem: both files exist and there is no
            # collision to report. Assert THAT, rather than skipping.
            check("on a case-sensitive filesystem both files exist",
                  os.path.isfile(os.path.join(proj, "README.md"))
                  and os.path.isfile(os.path.join(proj, "readme.md")))
            check("...and nothing is reported as a collision",
                  not scaf["collisions"], scaf["collisions"])
            return

        check("the template was not written over the author's file",
              _slurp(os.path.join(proj, "readme.md"))
              == "the author's own one byte")
        check("...and the collision is reported by the scaffold",
              [c["manifest"] for c in scaf["collisions"]] == ["README.md"],
              scaf["collisions"])
        check("...naming the file that is actually there",
              [c["on_disk"] for c in scaf["collisions"]] == ["readme.md"],
              scaf["collisions"])
        check("check() reports the entry MISSING rather than present",
              "README.md" in chk["missing"], chk["missing"][:5])
        check("...and never lists it as present",
              "README.md" not in chk["present"], chk["present"][:5])
        check("...and says why",
              [c["on_disk"] for c in chk["collisions"]] == ["readme.md"],
              chk["collisions"])
        check("the file appears in exactly one of manifest and extras",
              not [e for e in seen["unexpected"]
                   if e["path"] == "readme.md"],
              "one entry that is both the scaffold's own and something to "
              "move is how a file gets moved out from under a template")
        check("...and it is the manifest side that claims it",
              [c["on_disk"] for c in seen["collisions"]] == ["readme.md"],
              seen["collisions"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_depth():
    section("--depth, and the four fences that survive it (reorg 4)")

    root = tempfile.mkdtemp(prefix="scaffold_depth_")
    try:
        proj = os.path.join(root, "existing_work")
        os.makedirs(proj, exist_ok=True)
        write(proj, "misc/deep.qqq", "x")
        write(proj, "misc/inner/deeper.zzz", "x")
        write(proj, "Raw Data/run1.csv", "dose" + chr(10) + "1" + chr(10))
        values = sc.build_values(project_name="existing_work")
        sc.scaffold(proj, values)
        write(proj, "plan/should_not_be_classified.qqq", "x")
        write(proj, "data/raw/also_not.qqq", "x")

        one = {e["path"] for e in sc.survey(proj)["unexpected"]}
        check("depth 1 is the top level only, as it always was",
              "misc/deep.qqq" not in one, sorted(one))

        deep = sc.survey(proj, depth=3)
        paths = {e["path"] for e in deep["unexpected"]}
        check("depth 3 classifies inside a folder no rule matched",
              "misc/deep.qqq" in paths, sorted(paths))
        check("...and keeps going while nothing matches",
              "misc/inner/deeper.zzz" in paths, sorted(paths))
        check("it never descends into the structure's own folders",
              not [x for x in paths
                   if x.startswith(("plan/", "data/", "drafts/",
                                    "obsolete/", "sandbox/"))],
              sorted(paths))
        check("it never takes apart a folder whose NAME meant something",
              not [x for x in paths if x.startswith("Raw Data/")],
              "a folder that has a meaning is moved by that meaning")
        deeps = [e for e in deep["unexpected"] if e.get("depth", 1) > 1]
        check("every entry below the top level is at least one tier down "
              "from clear",
              not [e for e in deeps if e["confidence"] == "clear"],
              "--apply must not be able to reach a deep entry")
        check("...and the reason is written into the why",
              all("level" in e["why"] for e in deeps
                  if e["confidence"] == "likely"),
              [e["why"][-70:] for e in deeps])

        res = sc.adopt(proj, values, apply_moves=True, depth=3)
        moved = [m["from"] for m in res["moved"]]
        check("--apply --depth moves nothing that was not a top-level entry",
              not [m for m in moved if "/" in m], moved)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_sweep_unknown():
    section("The sweep takes one tier, and it is the shrug (reorg 3)")

    root = tempfile.mkdtemp(prefix="scaffold_sweep_")
    try:
        proj = os.path.join(root, "existing_work")
        os.makedirs(proj, exist_ok=True)
        write(proj, "mystery.xyz", "x")                    # unknown, no proposal
        write(proj, "shot.png", "x")                       # likely
        write(proj, "manuscript FINAL v3.docx", "x")       # ask, manuscript-shaped
        write(proj, "run1.csv", "dose" + chr(10) + "1" + chr(10))   # clear
        write(proj, "2026-02 microscope session/img.tif", "x")  # unknown WITH a proposal
        values = sc.build_values(project_name="existing_work")

        # 11. --apply alone sweeps nothing.
        plain = sc.adopt(proj, values, apply_moves=True)
        check("--apply without --sweep-unknown sweeps nothing",
              not plain["swept"], plain["swept"])
        check("...and writes no sandbox/unsorted/",
              not os.path.isdir(os.path.join(proj, "sandbox", "unsorted")))

        res = sc.adopt(proj, values, apply_moves=True, sweep_unknown=True)
        swept = {m["from"] for m in res["swept"]}
        not_swept = {r["path"]: r["why"] for r in res["not_swept"]}

        check("the shrug is swept", "mystery.xyz" in swept, sorted(swept))
        check("...to sandbox/unsorted/, not sandbox/",
              os.path.isfile(os.path.join(proj, "sandbox", "unsorted",
                                          "mystery.xyz")))
        check("a `likely` is never swept", "shot.png" not in swept, swept)
        check("a `clear` is never swept", "run1.csv" not in swept, swept)
        check("an `unknown` that PROPOSES something is never swept",
              "2026-02 microscope session" not in swept,
              "a tier is not enough; the gate is unknown AND no proposal")
        check("...and the reason is reported",
              "proposes" in not_swept.get("2026-02 microscope session", ""),
              not_swept)

        # 9. The test that exists because of 3.2.
        check("a manuscript-shaped .docx at `ask` is never swept",
              "manuscript FINAL v3.docx" not in swept,
              "a sweep of the uncertain tiers would move the paper into the "
              "one folder nothing reads and report success")
        check("...and the exemption appears in the report",
              bool(not_swept.get("manuscript FINAL v3.docx")), not_swept)

        # 10. The manifest.
        man = os.path.join(proj, "sandbox", "unsorted",
                           "WHERE-THESE-CAME-FROM.md")
        check("the manifest exists", os.path.isfile(man))
        body = _slurp(man, encoding="utf-8")
        check("...with a row carrying the original path",
              "`mystery.xyz`" in body, body[-300:])
        check("...and the engine's own reason",
              "no rule for .xyz" in body, body[-300:])

        write(proj, "second.qqq", "x")
        sc.adopt(proj, values, apply_moves=True, sweep_unknown=True)
        body2 = _slurp(man, encoding="utf-8")
        check("a second run APPENDS rather than overwriting",
              "`mystery.xyz`" in body2 and "`second.qqq`" in body2,
              body2[-300:])
        check("...and the header is written once",
              body2.count("# Where these came from") == 1)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_float_order():
    section("float-order reads names, proposes, and writes nothing (reorg 5)")

    check("a spaced name parses",
          sc.parse_float_name("Figure 2 final.png")["label"] == "Figure 2")
    check("...a compact one too",
          sc.parse_float_name("fig1_old.png")["label"] == "Figure 1")
    check("...and a versioned one",
          sc.parse_float_name("Figure_3_v2 (1).png")["label"] == "Figure 3")
    check("a table is a table",
          sc.parse_float_name("Table 1.xlsx")["kind"] == "table")
    check("a supplementary float is marked",
          sc.parse_float_name("Figure S1.png")["supplementary"])
    check("an instrument frame is NOT a float",
          sc.parse_float_name("image_004.tif") is None)
    check("...nor is a dated data file",
          sc.parse_float_name("run_data_2026_03_11.csv") is None)
    check("`old` in a name is reported as evidence",
          sc.parse_float_name("fig1_old.png")["version_marks"] == ["old"])

    root = tempfile.mkdtemp(prefix="scaffold_fo_")
    try:
        proj = os.path.join(root, "existing_work")
        os.makedirs(proj, exist_ok=True)
        for name in ("Figure 2 final.png", "Figure 2.png", "fig1_old.png",
                     "Figure_3_v2 (1).png", "image_004.tif"):
            write(proj, name, "x")
        res = sc.float_order(proj)
        labels = {c["label"] for c in res["candidates"]}
        check("it finds the float-named files",
              labels == {"Figure 1", "Figure 2", "Figure 3"}, sorted(labels))
        check("...and nothing else", len(res["candidates"]) == 4,
              len(res["candidates"]))
        groups = {g["label"]: g for g in res["version_groups"]}
        check("two files claiming one slot are ONE version group",
              sorted(groups) == ["Figure 2"], sorted(groups))
        check("...naming both files", len(groups["Figure 2"]["files"]) == 2,
              groups["Figure 2"]["files"])

        before = sorted(os.listdir(proj))
        sc.float_order(proj)
        check("it creates no folder and writes nothing",
              sorted(os.listdir(proj)) == before, before)
        check("...and writes no caption",
              not os.path.isfile(os.path.join(proj, "plan", "captions.md")))

        check("with no prose it says the order is a guess",
              "GUESS" in res["note"], res["note"])

        # 14. With prose, the prose wins and is compared against.
        values = sc.build_values(project_name="existing_work")
        sc.scaffold(proj, values)
        write(proj, "plan/captions.md",
              "## Figure 1 - fig01.png" + chr(10) * 2
              + "**The first claim.**" + chr(10) + "A caption." + chr(10) * 2
              + "## Figure 2 - fig02.png" + chr(10) * 2
              + "**The second claim.**" + chr(10) + "Another." + chr(10))
        write(proj, "drafts/source_text_r1/results.md",
              "# Results" + chr(10) * 2
              + "The second thing is shown (Figure 2)." + chr(10) * 2
              + "The first thing is shown (Figure 1)." + chr(10))
        res2 = sc.float_order(proj)
        check("with prose, there is no guess note", res2["note"] == "",
              res2["note"])
        check("...the prose order is read off crossrefs",
              res2["prose_order"] == ["Figure 2", "Figure 1"],
              res2["prose_order"])
        check("...and the disagreement with the filenames is reported",
              bool(res2["disagrees_with_prose"]),
              res2["disagrees_with_prose"])
        check("...saying which one is right",
              "PROSE is right" in (res2["disagrees_with_prose"] or [""])[0])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_reorganize_report():
    section("Every row says what it did or why it could not (reorg 6.3)")

    root = tempfile.mkdtemp(prefix="scaffold_report_")
    try:
        proj = os.path.join(root, "existing_work")
        os.makedirs(proj, exist_ok=True)
        write(proj, "mystery.xyz", "x")
        values = sc.build_values(project_name="existing_work")
        sc.scaffold(proj, values)

        res = sc.reorganize_report(proj)
        rows = {r["row"]: r for r in res["rows"]}
        check("every capability has a row",
              sorted(rows) == ["AI disclosure", "Citations", "Cover letter",
                               "Float order", "Sorted", "Structure"],
              sorted(rows))
        check("no row is empty",
              not [k for k, v in rows.items()
                   if not (v["did"] or v["could_not"])],
              sorted(rows))
        check("the AI-disclosure row says WHY it cannot run yet",
              "no journal" in rows["AI disclosure"]["could_not"],
              rows["AI disclosure"])
        check("...and names the command that would make it reachable",
              "init" in rows["AI disclosure"]["next"], rows["AI disclosure"])
        check("the cover-letter row gives the same reason",
              "no journal" in rows["Cover letter"]["could_not"],
              rows["Cover letter"])
        check("...and says it arrives with the package",
              "submission-package" in rows["Cover letter"]["next"],
              rows["Cover letter"])
        check("the standing note names the decision nobody here can make",
              any("disclosure wording" in n for n in res["standing"]),
              res["standing"])

        # And after `init` the two rows carry the command instead.
        os.makedirs(os.path.join(proj, "drafts", "Langmuir",
                                 "journal_requirements"), exist_ok=True)
        rows2 = {r["row"]: r for r in sc.reorganize_report(proj)["rows"]}
        check("with a journal initialised the disclosure row is reachable",
              rows2["AI disclosure"]["could_not"] == "",
              rows2["AI disclosure"])
        check("...and names the journal",
              "Langmuir" in rows2["AI disclosure"]["next"],
              rows2["AI disclosure"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# The GitHub sync: `github` connects a project, the hooks keep it in step,
# and large data never reaches the repository
# ---------------------------------------------------------------------------

def test_github_sync():
    section("GitHub sync")
    if shutil.which("git") is None or not sc._find_bash():
        skip("GitHub sync", "git or bash not found")
        return
    root, proj, _ = new_project(title="Sync", field="biochemistry")
    env_keep = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL",
                                              "GIT_CONFIG_NOSYSTEM")}
    try:
        os.environ["GIT_CONFIG_GLOBAL"] = os.path.join(root, "gitconfig")
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        with open(os.environ["GIT_CONFIG_GLOBAL"], "w") as fh:
            fh.write("[user]\n\tname = T\n\temail = t@example.org\n")
        bare = os.path.join(root, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)

        script = read(proj, sc.SYNC_SCRIPT)
        check("the scaffold writes the sync script with LF endings",
              b"\r" not in read_bytes(proj, sc.SYNC_SCRIPT))
        hooks = json.loads(read(proj, sc.SYNC_SETTINGS))["hooks"]
        check("the template carries the start, /clear and end hooks",
              [e.get("matcher") for e in hooks["SessionStart"]]
              == ["startup|resume", "clear"] and len(hooks["SessionEnd"]) == 1,
              hooks)

        movies = os.path.join(proj, "data", "raw", "movies")
        os.makedirs(movies)
        for i in range(5):
            with open(os.path.join(movies, f"m{i}.tif"), "wb") as fh:
                fh.truncate(60 * 1048576)
        with open(os.path.join(proj, "data", "big map.mrc"), "wb") as fh:
            fh.truncate(55 * 1048576)
        with open(os.path.join(proj, "data", "small.mrc"), "wb") as fh:
            fh.truncate(1048576)
        with open(os.path.join(proj, "data", "stack.mrcs"), "wb") as fh:
            fh.truncate(1024)

        proposed = sc.connect_github(proj)
        check("with no --repo, a repository is proposed and nothing created",
              proposed["needs_confirmation"] and not proposed["ok"]
              and proposed["proposed_repo"] == "demo_project"
              and not proposed["created_repository"], proposed)
        res = sc.connect_github(proj, url=bare)
        check("github connects, commits and pushes", res["ok"], res)
        pushed = subprocess.run(["git", "-C", bare, "ls-tree", "-r",
                                 "--name-only", "main"], capture_output=True,
                                text=True).stdout.splitlines()
        check("the project reached the remote",
              "project.yml" in pushed and sc.SYNC_SCRIPT in pushed, pushed[:5])
        check("a large file and a folder of large files stay off GitHub",
              not any(p.startswith("data/raw/movies/") or p == "data/big map.mrc"
                      for p in pushed), [p for p in pushed if "data/" in p])
        check("...a small data file does not",
              "data/small.mrc" in pushed, pushed)
        check("...and a raw cryo-EM format stays off whatever its size",
              "data/stack.mrcs" not in pushed)
        out = "\n".join(res["sync_output"])
        check("what was kept off is reported, folder collapsed to one line",
              "data/raw/movies/ " in out and "data/big map.mrc" in out
              and "m0.tif" not in out, out)

        again = sc.connect_github(proj)
        check("a second run changes nothing and stays connected",
              again["ok"] and not any("wrote" in s or "added" in s
                                      for s in again["steps"]), again)

        clone = os.path.join(root, "other_computer")
        subprocess.run(["git", "clone", "-q", "-b", "main", bare, clone],
                       check=True)
        write(clone, "plan/notes_from_laptop.md", "laptop\n")
        env = dict(os.environ, CLAUDE_PROJECT_DIR=clone)
        bash = sc._find_bash()
        subprocess.run([bash, os.path.join(clone, *sc.SYNC_SCRIPT.split("/")),
                        "end"], env=env, check=True)
        write(proj, "plan/left_unsaved.md", "unsaved\n")
        env = dict(os.environ, CLAUDE_PROJECT_DIR=proj)
        r = subprocess.run([bash, os.path.join(proj, *sc.SYNC_SCRIPT.split("/")),
                            "start"], env=env, capture_output=True, text=True)
        check("opening a session saves what the last one left unsaved",
              "closed before it could save" in r.stdout, r.stdout)
        check("...and brings in the other computer's work",
              os.path.exists(os.path.join(proj, "plan", "notes_from_laptop.md")))
    finally:
        for k, v in env_keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(root, ignore_errors=True)


def main():
    print("Scaffold and float-pipeline suite - specs/setup-project-directory.md")

    test_manifest()
    test_review_scaffold()
    test_fresh_scaffold()
    test_source_text_round_label()
    test_source_text_resolver_matches_manuscript_py()
    test_idempotence()
    test_only_subtree()
    test_float_ops()
    test_mock_float_specs()
    test_mock_float_errors()
    test_mock_float_write()
    test_analysis_md()
    test_front_matter_in_plan()
    test_rough_draft_md()
    test_prefill()
    test_prefill_absent_resources()
    test_shipped_resources_is_a_drop_zone()
    test_proposed_methods_report()
    test_float_lint()
    test_float_lint_stale_art()
    test_drawn_floats()
    test_refine_figure_skill()
    test_expects_data()
    test_adopt()
    test_adopt_apply()
    test_adopt_never_overwrites()
    test_adopt_contradiction()
    test_survey()
    test_extra_sources()
    test_record_project_key()
    test_image_float_specs()
    test_image_float_write()
    test_image_panel_helper()
    test_spreadsheet_is_a_question()
    test_case_collision()
    test_depth()
    test_sweep_unknown()
    test_float_order()
    test_reorganize_report()
    test_github_sync()

    rscript = find_rscript()
    if not rscript:
        for label in ("R float pipeline", "mock-data containment",
                      "legibility check", "mock floats render",
                      "mock floats without a group column",
                      "image placeholder floats render"):
            skip(label, "Rscript not found on PATH or in Program Files")
    else:
        print(f"\nRscript: {rscript}")
        root = None
        try:
            root, proj = test_pipeline(rscript)
            test_mock(rscript, proj)
            test_legibility(rscript, proj)
            test_mock_float_render(rscript)
            test_mock_float_nogroup(rscript)
            test_image_float_render(rscript)
        finally:
            if root:
                shutil.rmtree(root, ignore_errors=True)

    print(f"\n{PASSED}/{PASSED + FAILED} passed"
          + (f", {FAILED} FAILED" if FAILED else "")
          + (f", {SKIPPED} skipped" if SKIPPED else ""))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
