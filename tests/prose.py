#!/usr/bin/env python3
"""
tests/prose.py - measure tools/prose.py against text with known answers.

Fully offline, ~1s. Every case is a piece of prose whose correct verdict was
worked out by hand, including the ones the spec itself holds up as right and
wrong, so a passing run means the checker agrees with the spec rather than with
whatever it happens to do.

  python tests/prose.py
  python tests/prose.py -v
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# tests/prose.py and tools/prose.py share a name, so a plain `import prose`
# resolves to whichever directory is first on sys.path - and inside this file,
# to this file. Load the engine by path under a distinct module name. Do not
# "simplify" this back; scaffold.py, idea.py and docx_edits.py all have it for
# the same reason.
_spec = importlib.util.spec_from_file_location(
    "prose_engine", os.path.join(ROOT, "tools", "prose.py"))
if _spec is None or _spec.loader is None:
    raise ImportError("cannot load tools/prose.py")
prose = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prose)

VERBOSE = "-v" in sys.argv
PASS = FAIL = 0


def _slurp(path, mode="r", **kw):
    """Read a whole file and close it.

    `open(path).read()` leaks the handle until the garbage collector gets to
    it - harmless in a short script, but it makes the suite noisy under
    `python -W all`, and on Windows a handle still open can block the
    tempdir cleanup these tests rely on.
    """
    with open(path, mode, **kw) as fh:
        return fh.read()


def _spit(path, data, mode="w", **kw):
    """Write a whole file and close it - same reason as _slurp()."""
    with open(path, mode, **kw) as fh:
        return fh.write(data)


def check(name: str, got, want, note: str = "") -> None:
    global PASS, FAIL
    ok = got == want
    if ok:
        PASS += 1
        if VERBOSE:
            print(f"  ok    {name}  = {got!r}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}\n          got  {got!r}\n          want {want!r}"
              + (f"\n          {note}" if note else ""))


def section(title: str) -> None:
    print(f"\n{title}\n" + "-" * len(title))


# ---------------------------------------------------------------------------
# Number counting - spec 6.1
# ---------------------------------------------------------------------------

def counts(text: str) -> tuple[int, int]:
    c = prose.count_numbers(text)
    return len(c["inline"]), len(c["clusters"])


def test_counting() -> None:
    section("Counting numbers the way a reader pays for them (spec 6.1)")

    # The spec's own pair. If the checker disagrees with these two it is
    # measuring something other than what the rule describes.
    bad = ("The mean symmetry index was 0.82 ± 0.05 versus 0.71 ± 0.05 "
           "(p = 0.003).")
    good = ("Arrays from the thermophile were markedly more symmetric than "
            "those from the mesophile — a 15% higher symmetry index "
            "(0.82 ± 0.05 vs. 0.71 ± 0.05, p = 0.003) — which is the "
            "difference between a lattice that resolves at 4 Å and one that "
            "does not.")
    check("spec 6 rule 1, the bad example", counts(bad), (4, 1))
    check("spec 6 rule 1, the good example", counts(good), (2, 1))

    # One cluster is one unit however many values it holds - that is the whole
    # reason the flat cap of three was wrong.
    check("a six-value cluster still costs one",
          counts("Order rose with temperature (0.82, 0.71, 0.64, 0.55, 0.41, "
                 "0.33)."), (0, 1))
    check("two clusters cost two",
          counts("Order rose (p = 0.003) and spacing did not (p = 0.44)."),
          (0, 2))
    check("a parenthesis with no number is not a cluster",
          counts("Order rose with temperature (as expected)."), (0, 0))

    # The exclusion list. Without it the check fires on every 16S rRNA and
    # becomes noise the user learns to ignore, which is worse than no check.
    check("cross-references do not count",
          counts("Order rose (Figure 2) as shown in Table 1 and Fig. 3B."),
          (0, 0))
    check("citations do not count",
          counts("Order rose [@jensen2019arrays] as reported [7]."), (0, 0))
    check("years do not count", counts("First reported in 2019, again in 1997."),
          (0, 0))
    check("nomenclature does not count",
          counts("The 16S rRNA gene, Cas9, H2O, p53 and T4 lysozyme."), (0, 0))
    check("catalogue and grant numbers do not count",
          counts("Supported by R01-CA123456 and catalog no. 55008711."), (0, 0))
    check("a FLAG marker does not count",
          counts("Arrays were more ordered **[FLAG: stats — output shows "
                 "p = 0.31 for this comparison]**, as expected."), (0, 0))

    # The unit list is case-sensitive, and that is the whole trick: 16S and
    # 50 s differ only in the case of one letter.
    check("a glued unit is a quantity", counts("Incubated at 50mM for 4h."),
          (2, 0))
    check("a glued capital is nomenclature", counts("The 16S and 23S genes."),
          (0, 0))
    check("a spaced unit is a quantity", counts("Incubated at 50 mM for 4 h."),
          (2, 0))

    check("markdown tables are not prose",
          len(prose.paragraphs("# R\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")), 0)
    check("headings alone are not paragraphs",
          len(prose.paragraphs("# Results\n\n## Sub\n")), 0)
    check("HTML comments are not paragraphs",
          len(prose.paragraphs("# R\n\n<!-- one\ntwo -->\n")), 0)


def test_density_rules() -> None:
    section("The density budget and the two absolute rules")

    tmp = tempfile.mkdtemp(prefix="pr_")
    try:
        def write(name: str, body: str) -> str:
            path = os.path.join(tmp, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
            return path

        # 47 words, two inline. Proration must not flag the sentence the spec
        # holds up as correct - that is what the floor at the base budget is
        # for.
        good = write("results.md", "# Results\n\n" + (
            "Arrays from the thermophile were markedly more symmetric than "
            "those from the mesophile — a 15% higher symmetry index "
            "(0.82 ± 0.05 vs. 0.71 ± 0.05, p = 0.003) — which is the "
            "difference between a lattice that resolves at 4 Å and one that "
            "does not.\n"))
        res = prose.density([good], "low")
        check("the spec's good paragraph passes at low",
              [f["rule"] for f in res["findings"]], [])

        vomit = write("results.md", "# Results\n\n" + (
            "The mean symmetry index was 0.82 versus 0.71, the mean spacing "
            "12.1 nm versus 12.3 nm, the mean defect count 4 versus 9, and the "
            "mean domain size 41 nm versus 22 nm.\n"))
        res = prose.density([vomit], "low")
        rules = sorted({f["rule"] for f in res["findings"]})
        check("number vomit trips the inline budget",
              "inline_budget" in rules, True)
        check("...and rule 6, opening on a measurement",
              "opens_with_measurement" in rules, True)

        res = prose.density([vomit], "high")
        check("high is report-only, no enforcement",
              [f for f in res["findings"] if f["rule"] == "inline_budget"], [])

        # The one-third cap survives any density setting.
        dense = write("results.md", "# Results\n\n" + (
            "Values were 1 2 3 4 5 6 7 8 and 9 across the runs.\n"))
        res = prose.density([dense], "high")
        check("the one-third cap survives `high`",
              "numeric_fraction" in [f["rule"] for f in res["findings"]], True)

        res = prose.density([vomit], "low", waivers=["results ¶1"])
        check("a waiver silences its paragraph", res["findings"], [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# A project fixture, built once for the file-level checks
# ---------------------------------------------------------------------------

CAPTIONS = """\
## Figure 1 — fig01_geometry.png
**Arrays keep hexagonal packing at every growth temperature.**
(A) Central sections. n = 12 per species.

## Figure 2 — fig02_symmetry.png
**Thermophilic arrays are more ordered than mesophilic ones.**
Symmetry index against growth temperature.

## Table 1 — tab01_strains.html
Comparison of the strains used in this study.
Mean of three cultures.
"""

OUTLINE = """\
## Introduction

- Arrays have been resolved in mesophiles but not above 60 C. [jensen2019arrays]
- Nobody has measured order against temperature. [background]

## Results

- Arrays keep their hexagonal packing across the range. [Fig 1]
- Thermophilic arrays are more ordered than mesophilic ones. [Fig 2; stats: symmetry_index ~ species]
- The strains differ only in growth temperature. [Table 1]
- Order is lost when the adaptor is deleted. [Fig 4B]
- This line has nothing behind it.
- This one names a table that does not exist. [Table 9]
- This one names a stats term nothing computed. [stats: lattice_energy ~ species]
- This bracket is not readable at all. [see the appendix]
"""

STATS = """\
# Analysis output

## symmetry_index ~ species

Welch two-sample t-test, t = 5.41, p = 0.003, n = 12 per species.
Means 0.82 and 0.71, difference 0.11.
"""

BIB = """\
@article{jensen2019arrays,
  title = {Cryo-electron tomography of chemoreceptor arrays},
  author = {Jensen, Grant J.},
  journal = {Annu Rev Microbiol},
  year = {2019},
  pages = {211--230},
  doi = {10.1146/x}
}

@article{orphan2020unused,
  title = {An entry nothing cites},
  author = {Orphan, Victoria},
  year = {2020}
}
"""

RESULTS = """\
# Results

Arrays from both species kept the hexagonal packing reported in every mesophile
examined so far (Figure 1).

Thermophilic arrays were more ordered than mesophilic ones, by 0.11 in symmetry
index (p = 0.003), and the difference held across every field of view.

The strains were matched in every respect except temperature (Table 1).

Deleting the adaptor abolished the ordering entirely (Figure 4), and the
residual signal was 0.09 by the same measure.
"""

DISCUSSION = """\
# Discussion

Order tracks growth temperature rather than phylogeny (Figure 2). The claim
rests on the adaptor deletion reported elsewhere [@vance2021adaptor], and on
**[FLAG: decision — two defensible framings here, mechanism or scope]** which
reading of the lattice model we take.

The study cannot say whether ordering causes thermal stability
**[FLAG: author — was the orientation control ever run?]**.
"""


def build_project(root: str) -> str:
    for rel, body in [
        ("plan/captions.md", CAPTIONS),
        ("plan/outline.md", OUTLINE),
        ("data/analysis/analysis.md", STATS),
        ("drafts/references.bib", BIB),
        ("drafts/source_text/results.md", RESULTS),
        ("drafts/source_text/discussion.md", DISCUSSION),
        ("drafts/source_text/introduction.md",
         "# Introduction\n\nArrays have been resolved in mesophiles but not "
         "above 60 C [@jensen2019arrays].\n\nNobody has measured order against "
         "temperature.\n"),
        ("drafts/source_text/live_captions.md",
         CAPTIONS.replace("Symmetry index against growth temperature.", "")),
    ]:
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
    return root


def test_outline(root: str) -> None:
    section("The outline as the claims ledger (spec 5.1, 12)")

    res = prose.outline(root, "medium")
    rules = [f["rule"] for f in res["findings"]]

    check("an empty bracket is a finding", rules.count("empty_bracket"), 1)
    check("a float that is not in captions.md is a finding",
          rules.count("float_missing"), 2,
          "Table 9 and Fig 4B are both absent from captions.md")
    check("a stats term the output never produced is a finding",
          rules.count("stats_term_missing"), 1)
    check("an unreadable bracket is a finding",
          rules.count("unreadable_evidence"), 1)

    check("a panel suffix resolves to its float",
          any(f["rule"] == "float_missing" and "Fig 4B" in f["detail"]
              for f in res["findings"]), True,
          "Fig 4B must be read as Figure 4, which really is missing here")

    check("a resolvable line raises nothing",
          [f for f in res["findings"]
           if f.get("claim", "").startswith("Thermophilic")], [])

    sections = {s["section"]: s for s in res["sections"]}
    check("outline lines are counted per section",
          sections["results"]["outline_lines"], 8)
    check("paragraphs are counted per section",
          sections["results"]["paragraphs"], 4)

    # Alignment is one-to-one WITHIN a section: one strong paragraph must not
    # win three outline lines and leave the rest reported as dropped. Across
    # sections the numbering restarts, so the pairs have to be compared per
    # section or introduction 1 collides with results 1.
    per_section: dict[str, list[int]] = {}
    for ln in res["lines"]:
        pnum = ln.get("match", {}).get("paragraph")
        if pnum:
            per_section.setdefault(ln["section"], []).append(pnum)
    check("no paragraph is claimed by two outline lines",
          {k: len(v) == len(set(v)) for k, v in per_section.items()},
          {k: True for k in per_section})


def test_citekeys(root: str) -> None:
    section("Citekeys against the bibliography (spec 9.1, 5.5)")

    res = prose.citekeys(root)
    check("in-text keys are found", res["cited"], 2)
    check("an unresolved key is an error", res["unresolved"],
          ["vance2021adaptor"])
    check("an uncited entry is reported", res["uncited"], ["orphan2020unused"])
    check("the unresolved key names its location",
          next(f["location"] for f in res["findings"]
               if f["rule"] == "unresolved_citekey"), "discussion ¶1")

    res = prose.citekeys(root, max_refs=1)
    check("the journal's reference cap is enforced",
          any(f["rule"] == "over_reference_cap" for f in res["findings"]), True)

    # A core@shell formula is chemistry, not a citation. `~` is legal inside a
    # citekey, so without a lookbehind `SiO~2~@Cu~2~GaBO~5~@TiO~2~` reads as
    # two keys; they were reported as missing from the .bib and then written
    # into the PAPER NOT COMPLETE block as `[@Cu~2~GaBO~5]`, which citeproc
    # could not resolve, and the build died on the engine's own invention.
    for text, want, why in (
            ("The SiO~2~@Cu~2~GaBO~5~@TiO~2~ shell [@jensen2019arrays].",
             ["jensen2019arrays"], "subscripted core@shell notation"),
            ("Plain SiO2@Cu2GaBO5@TiO2 as well [@a2020b].",
             ["a2020b"], "the same formula without subscripts"),
            ("Write to rmercer2@byu.edu about it.", [], "an email address"),
            ("As shown [@smith2020; @jones2019].",
             ["smith2020", "jones2019"], "a bracketed pair"),
            ("See @smith2020 for detail.", ["smith2020"], "a bare key"),
            ("@smith2020 opens the line.", ["smith2020"],
             "a key opening the line"),
            ("[-@smith2020] suppresses the author.", ["smith2020"],
             "a suppressed-author key")):
        check(f"{why} reads as {want}",
              prose.CITEKEY_RE.findall(text), want)


def test_captions(root: str) -> None:
    section("Claim-first legends and float/text pairing (spec 7)")

    res = prose.captions(root, cap=None)
    rules = [f["rule"] for f in res["findings"]]
    check("a legend with no bold claim is an error",
          rules.count("no_claim_subtitle"), 1, "Table 1 has none")
    check("a float named only in the discussion still counts as cited",
          [f["location"] for f in res["findings"]
           if f["rule"] == "float_never_cited"], [],
          "every source_text section is scanned, not just results.md")
    check("a text reference with no caption block is an error",
          any(f["rule"] == "reference_to_missing_float"
              and "Figure 4" in f["detail"] for f in res["findings"]), True)

    floats = {f["label"]: f for f in res["floats"]}
    check("Figure 1 is paired to its paragraph",
          floats["Figure 1"]["referenced_in"], ["results ¶1"])

    res = prose.captions(root, cap=5)
    check("the caption word cap is enforced",
          sum(1 for f in res["findings"] if f["rule"] == "over_caption_cap"), 3)

    # spec 7: every change from plan/captions.md is reported as a diff line.
    diffs = [f for f in prose.captions(root)["findings"]
             if f["rule"] == "live_caption_differs"]
    check("a trimmed live caption is reported as a diff", len(diffs), 1)
    check("...with its word counts", "13 -> 8 words" in diffs[0]["detail"],
          True, diffs[0]["detail"] if diffs else "")

    check("a label-lead subtitle is caught",
          prose.LABEL_LEAD_RE.match("Comparison of X and Y across conditions")
          is not None, True)
    check("...and a real claim is not",
          prose.LABEL_LEAD_RE.match(
              "Catalyst B doubles the yield of catalyst A") is None, True)


def test_flags(root: str) -> None:
    section("Flag collection (spec 5.10)")

    files = prose.source_files(root)
    res = prose.flags(files)
    check("both flags are found", res["counts"]["flags"], 2)
    check("types are read off the marker", sorted(res["by_type"]),
          ["author", "decision"])
    check("both are ones only a human can answer", res["needs_a_human"], 2)
    check("locations are paragraph ids",
          sorted(f["location"] for f in res["flags"]),
          ["discussion ¶1", "discussion ¶2"])
    check("an unknown flag type is named",
          prose.flags([f for f in files if f.endswith("results.md")])
          ["unknown_types"], [])

    # --- the stable id (specs/flag-resolver.md 5) ---------------------
    #
    # An answer recorded in r4 is applied in r5, by which time the prose has
    # moved. So the id is hashed from the section, the type and the body -
    # NEVER from the location, which is the one component guaranteed to have
    # changed.
    check("every flag carries an id",
          all(f.get("id") for f in res["flags"]), True, res["flags"])
    check("...and two flags in one section have different ones",
          len({f["id"] for f in res["flags"]}), 2,
          str([f["id"] for f in res["flags"]]))

    same = [f for f in res["flags"]]
    moved = prose.flag_id(same[0]["location"].split(" ")[0],
                          same[0]["type"], same[0]["message"])
    check("a moved paragraph keeps its id", moved, same[0]["id"],
          "location is not part of the hash")
    check("...and whitespace and case do not change it",
          prose.flag_id("RESULTS", "Data", "the  coating   was 40 nm"),
          prose.flag_id("results", "data", "the coating was 40 nm"))
    check("a REWORDED flag gets a new id",
          prose.flag_id("results", "data", "no thickness recorded")
          != prose.flag_id("results", "data",
                           "thickness for the annealed series is missing"),
          True,
          "two different questions; an answer carried across them silently "
          "is a lookup that was almost right")
    check("two IDENTICAL bodies in different sections differ",
          prose.flag_id("results", "author", "give the IRB number")
          != prose.flag_id("methods", "author", "give the IRB number"),
          True)
    check("...and so do two types with the same body",
          prose.flag_id("results", "data", "x")
          != prose.flag_id("results", "author", "x"), True)
    check("the id is short enough to quote in a table row",
          len(res["flags"][0]["id"]) <= 8, True, res["flags"][0]["id"])

    # item 43: nothing writes a flags.md any more. The flags are inline in
    # the source text and again in the PAPER NOT COMPLETE block, and those
    # two are the copies anybody reads.
    check("nothing renders a third copy of the flag list",
          hasattr(prose, "render_flags_md"), False)
    help_text = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "prose.py"), "flags",
         "--help"], capture_output=True, text=True, encoding="utf-8",
        errors="replace").stdout
    check("...and `flags` has no --out to write one with",
          "--out" in help_text, False)


def test_numbers(root: str) -> None:
    section("Every number against a recorded source (spec 6 rule 5)")

    res = prose.numbers(root)
    unrecorded = {(f["location"], f["value"]) for f in res["findings"]
                  if f["rule"] == "not_recorded"}
    check("a number the analysis produced passes",
          ("results ¶2", "0.11") in unrecorded, False)
    check("a p-value the analysis produced passes",
          ("results ¶2", "0.003") in unrecorded, False)
    check("a number nothing produced is caught",
          ("results ¶4", "0.09") in unrecorded, True)
    check("...and in the results it is an error",
          next(f["severity"] for f in res["findings"]
               if f["value"] == "0.09"), "error")

    # Rounding is allowed and has to be, or every value written to two
    # decimals is reported. Recomputation is not.
    check("a rounded value matches", prose._matches_a_stat("0.8", [0.82]), True)
    check("a differently rounded value matches",
          prose._matches_a_stat("5.4", [5.41]), True)
    check("a recomputed value does not",
          prose._matches_a_stat("15", [0.82, 0.71, 0.11]), False)

    # The template's own comment block must not become a source of truth.
    tmp = tempfile.mkdtemp(prefix="pr_")
    try:
        p = os.path.join(tmp, "methods_facts.yml")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("# incubate for 4 h at 37 C, see protocol 12345\n"
                     "temperature_K: 310\n")
        vals = prose._floats_in(
            prose._recorded_values(_slurp(p, encoding="utf-8")))
        check("a comment is not a recorded value", 4.0 in vals, False)
        check("a recorded value is", 310.0 in vals, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_caption_parser_agrees(root: str) -> None:
    section("The caption grammar is one contract, reproduced not approximated")

    r_regex = None
    r_path = os.path.join(ROOT, "tools", "project_template", "plan", "theme",
                          "read_captions.R")
    with open(r_path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("CAPTION_HEADING_RE"):
                r_regex = line.split("<-", 1)[1].strip().strip('"')
                break
    # R doubles every backslash in a string literal, and R's regex engine has
    # no \d shorthand in the default (TRE) mode the rest of that file relies
    # on - so the R side spells the digit class [0-9]. Those two spellings are
    # the ONLY difference allowed: normalising anything more would let a real
    # divergence pass, which defeats the point of pinning the contract.
    normalised = (r_regex or "").replace("\\\\", "\\").replace("[0-9]", r"\d")
    check("prose.py and read_captions.R carry the same heading grammar",
          normalised, prose.CAPTION_HEADING_RE.pattern)

    idea_path = os.path.join(ROOT, "tools", "idea.py")
    with open(idea_path, encoding="utf-8") as fh:
        idea_src = fh.read()
    check("...and so does idea.py",
          r"^##\s+(Figure|Table)\s+(S?\d+)\s*(?:—|–|-{1,2})\s*(\S+)\s*$"
          in idea_src, True)

    entries = prose.read_captions(root)
    check("three float blocks are parsed", len(entries), 3)
    check("a supplementary float is flagged as one",
          [e["supplementary"] for e in entries], [False, False, False])
    check("the subtitle is split off the caption body",
          entries[1]["subtitle"],
          "Thermophilic arrays are more ordered than mesophilic ones.")
    check("document order is manuscript order",
          [e["order"] for e in entries], [1, 2, 3])




# ---------------------------------------------------------------------------
# outline.md stays minimal, keeps its notes out of the ledger, and reports
# the line numbers the file actually has
# ---------------------------------------------------------------------------

MINIMAL_OUTLINE = """\
<!-- ONE LINE PER PARAGRAPH of the finished paper, in order.
     Each line: the claim that paragraph makes, then [the evidence for it].
     Keep every line short - the IDEA of the paragraph, never a draft of it.

     Evidence: [Fig N] [Table N] [stats: <term>] [PMID 12345678] [@citekey]
     Delete this comment once you have the hang of it. -->

## Results

- Arrays keep their hexagonal packing across the range. [Fig 1]
- In this paragraph we set out the observation that arrays which were isolated from the thermophile proved to be markedly more symmetric than those taken from the mesophile. [Fig 2]
- Ordered lattices resolve. Disordered ones do not. [Fig 2]

## Notes

- Ask GJ whether 60 C is the right cut-off.
- Figure 3 is planned but has not been made.
"""


def test_outline_minimality(tmp: str) -> None:
    section("outline.md is a paragraph plan, not a draft of the paragraphs")

    root = os.path.join(tmp, "minimal")
    path = os.path.join(root, "plan", "outline.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(MINIMAL_OUTLINE)

    lines = prose.read_outline(root)
    check("the notes block is not parsed as paragraphs",
          len(lines), 3,
          "a note read as a claim becomes a paragraph the paper never has")
    check("...and is readable on its own",
          len(prose.read_outline_notes(root)), 2)

    # The line-number pin. The scaffolded comment is six lines here and the
    # user is explicitly told they may leave it in place; every location the
    # pipeline reports is counted after it is stripped.
    body = MINIMAL_OUTLINE.splitlines()
    want = next(i for i, line in enumerate(body, 1)
                if line.startswith("- Arrays keep"))
    check("a line's reported number is the one it has in the file",
          lines[0]["line"], want,
          "an HTML comment is stripped, but not the lines it occupied")

    res = prose.outline(root, "medium")
    detail = [f for f in res["findings"] if f["rule"] == "over_detailed"]
    check("an over-long line is reported", len(detail), 2,
          "one is too many words, one is two sentences")
    check("...the long one by its word count",
          any("words" in f["detail"] for f in detail), True)
    check("...and the two-sentence one for being two paragraphs",
          any("more than one sentence" in f["detail"] for f in detail), True)
    check("a short line with one sentence is not reported",
          any(f["claim"].startswith("Arrays keep") for f in detail), False)
    check("the count travels in the payload",
          res["counts"]["over_detailed"], 2)

    # The contract travels with the read, so a caller that writes an outline
    # validates against this table rather than keeping a second copy of it.
    check("the accepted section names ship with the result",
          "introduction" in res["contract"]["sections"], True)
    check("...and so does the word cap",
          res["contract"]["max_words"], prose.OUTLINE_MAX_WORDS)


def test_outline_state(tmp: str) -> None:
    section("missing, empty and present are three different answers")

    root = os.path.join(tmp, "states")
    os.makedirs(os.path.join(root, "plan"), exist_ok=True)
    path = os.path.join(root, "plan", "outline.md")
    check("no file at all is 'missing'", prose.outline_state(root), "missing")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("## Results\n\n-\n")
    check("a scaffolded stub is 'empty', not an error",
          prose.outline_state(root), "empty",
          "the two want the same offer, but only one needs a file created")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("## Results\n\n- Arrays are ordered. [Fig 1]\n")
    check("one real line makes it 'present'",
          prose.outline_state(root), "present")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("## Notes\n\n- Ask GJ about the cut-off.\n")
    check("a file of nothing but notes is still empty",
          prose.outline_state(root), "empty",
          "notes are not paragraphs, so they cannot make an outline present")


# ---------------------------------------------------------------------------
# crossrefs - every float and panel called out, in order (spec 7.1)
# ---------------------------------------------------------------------------

XREF_CAPTIONS = """\
## Figure 1 — fig01.png
**Arrays keep hexagonal packing.**
(A) Central sections. (B) Radial profiles. n = 12.

## Figure 2 — fig02.png
**Thermophilic arrays are more ordered.**
(A) Symmetry index. (B) The same by tomogram.

## Figure 3 — fig03.png
**The adaptor sets the lattice spacing.**
Spacing per strain.

## Figure 5 — fig05.png
**The second dataset behaves the same way.**
Replication in a third species.

## Figure S1 — figS1.png
**Tilt series quality is uniform.**
Resolution per tomogram.

## Table 1 — tab01.html
**The strains differ only in growth temperature.**
Growth conditions.

## Table 2 — tab02.html
**Every tomogram passed the same cut-off.**
Acquisition parameters.
"""

XREF_RESULTS = """\
# Results

Arrays kept their hexagonal packing (Figure 1A, B).

The adaptor sets the lattice spacing (Figure 3).

Both species were ordered (Figure 2), and the strains were matched
(Tables 1 and 2).

Panel C carries the control (Fig. 1C).

Figure 5 shows the same trend in the second dataset.

Tilt series quality was uniform (Figure S1).
"""


def test_crossrefs(tmp: str) -> None:
    section("Every float and panel called out, in the right form and order")

    # The scanner first, on sentences whose answers are obvious by eye. The
    # plural cases are the regression pin: the scanner this replaced found
    # NOTHING in either of the first two, so a float cited only that way was
    # reported as never cited - inside the .docx, in the incomplete block.
    def seen(text: str) -> list[str]:
        return [m["label"] for m in prose.float_mentions(text)]

    check("a plural callout names both floats",
          seen("the same (Figures 1 and 2)"), ["Figure 1", "Figure 2"])
    check("a range names everything in it",
          seen("the trend holds (Figs. 1-3)"),
          ["Figure 1", "Figure 2", "Figure 3"],
          "a float cited only inside a range is still cited")
    check("a singular callout does not swallow the next number",
          seen("grown at 60 C (Figure 1 and 3 mM salt)"), ["Figure 1"],
          "the plural is what licenses reading a list")
    check("panels come with the number",
          [m["panels"] for m in prose.float_mentions("as in Fig. 2A-C")],
          [["A", "B", "C"]])
    check("...and a lowercase panel is the same panel",
          [m["panels"] for m in prose.float_mentions("as in Fig. 1a and b")],
          [["A", "B"]])
    check("...while a following word is not a panel",
          [m["panels"] for m in prose.float_mentions("Fig. 1A and the rest")],
          [["A"]])
    check("supplementary floats keep their own numbering",
          seen("quality was uniform (Table S2)"), ["Table S2"])

    root = os.path.join(tmp, "xrefs")
    for rel, body in [("plan/captions.md", XREF_CAPTIONS),
                      ("drafts/source_text/results.md", XREF_RESULTS)]:
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)

    res = prose.crossrefs(root)
    rules = [f["rule"] for f in res["findings"]]
    floats = {f["label"]: f for f in res["floats"]}

    check("every callout is counted, plural ones included",
          res["counts"]["mentions"], 8)
    check("a float cited only in a plural callout is not reported as uncited",
          floats["Table 2"]["first_mention"], "results ¶3",
          "this is the whole bug: (Tables 1 and 2) was invisible")

    check("a panel the legend defines and nothing cites is reported",
          floats["Figure 2"]["panels_missing"], ["A", "B"])
    check("...and shows up as a finding", rules.count("panel_never_cited"), 1)
    check("a panel the text cites and the legend never defines is reported",
          floats["Figure 1"]["panels_undeclared"], ["C"])
    check("...and both panels that ARE declared and cited are quiet",
          floats["Figure 1"]["panels_missing"], [])

    order = [f for f in res["findings"] if f["rule"] == "float_out_of_order"]
    check("a float first mentioned out of turn is an error", len(order), 1)
    check("...named with both floats and both paragraphs",
          "Figure 3" in order[0]["detail"] and "Figure 2" in order[0]["detail"]
          and "results ¶2" in order[0]["detail"], True,
          order[0]["detail"] if order else "")
    check("...and tables, in order, are not swept up in it",
          any("Table" in f["detail"] for f in order), False,
          "figures and tables are separately numbered sequences")

    gaps = [f for f in res["findings"] if f["rule"] == "float_number_gap"]
    check("a hole in the numbering is reported once", len(gaps), 1)
    check("...naming the number nothing is",
          "nothing is Figure 4" in gaps[0]["detail"], True,
          gaps[0]["detail"] if gaps else "")

    check("with no sourced callout style nothing is judged on form",
          rules.count("reference_not_parenthetical"), 0,
          "an unsourced requirement is never inferred from similar journals")
    check("...but the running-text callouts are counted and said out loud",
          any("1 of 8" in f["detail"] for f in res["findings"]
              if f["rule"] == "callout_style_unsourced"), True)

    strict = prose.crossrefs(root, style="parenthetical")
    loose = [f for f in strict["findings"]
             if f["rule"] == "reference_not_parenthetical"]
    check("a journal that wants parentheses gets them checked", len(loose), 1)
    check("...pointing at the sentence that runs the callout into the prose",
          loose[0]["location"], "results ¶5")
    check("...and the parenthetical ones are left alone",
          all("Figure 5" in f["detail"] for f in loose), True)
    check("a table style of its own is honoured separately",
          sum(1 for f in prose.crossrefs(root, table_style="parenthetical"
                                         )["findings"]
              if f["rule"] == "reference_not_parenthetical"), 0,
          "every table callout here is already parenthetical")


# ---------------------------------------------------------------------------
# length - the journal's budget (spec 4.4)
# ---------------------------------------------------------------------------

LENGTH_TA = """\
# Title

Arrays are ordered.

## Abstract

We measured order in arrays. It rose with temperature.

<!-- write this last -->

## Keywords

order, arrays
"""

LENGTH_RESULTS = """\
# Results

Arrays kept packing **[FLAG: stats — n not recorded]** at every temperature.
"""


def test_length(tmp: str) -> None:
    section("Word counts against the journal's budget, and whose call it is")

    root = os.path.join(tmp, "length")
    for rel, body in [
        ("drafts/source_text/title_abstract.md", LENGTH_TA),
        ("drafts/source_text/results.md", LENGTH_RESULTS),
        ("drafts/source_text/introduction.md", "# Introduction\n\nArrays exist.\n"),
        ("plan/captions.md", XREF_CAPTIONS),
    ]:
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)

    c = prose.section_words(root)
    check("the abstract is counted on its own", c["abstract"], 9,
          "it has its own cap in almost every journal")
    check("a heading is not part of the count", c["introduction"], 2)
    check("a **[FLAG: ...]** is not part of the count", c["results"], 6,
          "the flag is this pipeline's scaffolding and will not be submitted")
    check("the body is the four sections that are not the title page",
          c["body"], 8)
    check("...and the total takes the title page in", c["total"], 22)

    res = prose.length(root, {"abstract": 5, "total": 40})
    over = {o["scope"]: o for o in res["over"]}
    check("over the abstract cap is reported with the overage",
          (over["abstract"]["words"], over["abstract"]["over_by"]), (9, 4))
    check("...and a limit it is inside is not reported",
          "total" in over, False)

    check("going over is a question, not a failure, until it is answered",
          [f["severity"] for f in res["findings"]
           if f["rule"] == "over_word_limit"], ["decision"])
    carried = prose.length(root, {"abstract": 5}, policy="over")
    check("once the user says carry it, it is recorded rather than asked",
          [f["severity"] for f in carried["findings"]
           if f["rule"] == "over_word_limit"], ["info"])
    check("...and it still counts as outstanding",
          len(carried["over"]), 1,
          "an accepted overage still has to be resolved before submission")
    check("...with the same count either way",
          carried["counts"]["abstract"], res["counts"]["abstract"])
    cut = prose.length(root, {"abstract": 5}, policy="shorten")
    check("and once they say shorten, it is work to do",
          [f["severity"] for f in cut["findings"]
           if f["rule"] == "over_word_limit"], ["warning"])

    caps = prose.length(root, {"figures_max": 3, "tables_max": 1})
    check("main-text floats are counted, supplementary ones are not",
          (caps["counts"]["figures"], caps["counts"]["tables"]), (4, 2),
          "Figures 1, 2, 3 and 5 are main text; S1 is not - that is the SI's "
          "whole point as somewhere to move things to")
    check("...and a float cap over budget asks the same question",
          [f["rule"] for f in caps["findings"]
           if f["rule"].startswith("over_")],
          ["over_figure_cap", "over_table_cap"])
    check("...while a cap the paper is inside is quiet",
          [f["rule"] for f in prose.length(root, {"figures_max": 4,
                                                  "tables_max": 2})["findings"]
           if f["rule"].startswith("over_")], [])

    check("an unsourced limit is said out loud rather than assumed",
          any(f["rule"] == "no_word_limit_sourced"
              for f in prose.length(root, {})["findings"]), True)
    check("...and nothing is checked against it",
          prose.length(root, {})["over"], [])

    # --- item 76: a review has none of the four IMRaD stems ---------------
    # The body total used to be the sum of four fixed keys, so a review -
    # whose sections are the thematic ones its author named - raised
    # KeyError and took down every consumer of a word count with it.
    rev = os.path.join(tmp, "length_review")
    for rel, body in [
        ("project.yml", 'title: "r"\npaper_kind: review\n'),
        ("drafts/source_text_r1/title_abstract.md", LENGTH_TA),
        ("drafts/source_text_r1/01_structure.md",
         "# Structure\n\nThe fold is a beta sandwich.\n"),
        ("drafts/source_text_r1/05_disease_pathology.md",
         "# Disease Pathology\n\nLoss of function causes disease here.\n"),
        ("plan/captions.md", XREF_CAPTIONS),
    ]:
        path = os.path.join(rev, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)

    rc = prose.section_words(rev)
    check("a review's body is the sum of the sections it actually has",
          rc["body"], rc["01_structure"] + rc["05_disease_pathology"],
          "not four hard-coded IMRaD names - a review has none of them")
    check("...and the four it does not have raise nothing",
          [k for k in ("introduction", "methods", "results", "discussion")
           if k in rc], [])
    check("...and the total still takes the title page in",
          rc["total"], rc["body"] + 14)
    rres = prose.length(rev, {"total": 5})
    check("length itself runs on a review rather than crashing",
          [o["scope"] for o in rres["over"]], ["total"])
    check("...and the breakdown is the review's own sections, in order",
          [k for k in rres["counts"]
           if k not in ("title_abstract", "abstract", "body", "total",
                        "figures", "tables")],
          ["01_structure", "05_disease_pathology"])

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        prose.print_length(rres)
    printed = buf.getvalue()
    check("...and the printer names them too, rather than KeyError-ing on "
          "a section the paper does not have",
          ("05_disease_pathology" in printed, "methods" in printed),
          (True, False))


def test_layout_contract(tmp: str) -> None:
    section("The layout resolver is one contract, reproduced not approximated")

    # Same reasoning as CAPTION_HEADING_RE above: every engine here is
    # standalone by design, so this block is repeated rather than imported.
    # Repetition is only safe while something compares the copies.
    blocks = {}
    for name in ("manuscript", "prose", "toc_graphic"):
        src = _slurp(os.path.join(ROOT, "tools", f"{name}.py"),
                     encoding="utf-8")
        a = src.find("# --- LAYOUT CONTRACT")
        b = src.find("# --- END LAYOUT CONTRACT")
        check(f"{name}.py carries the layout contract block", a >= 0 and b > a,
              True)
        blocks[name] = src[a:src.index(chr(10), b) + 1] if a >= 0 else ""
    check("all three copies are byte for byte identical",
          len(set(blocks.values())), 1,
          "edit one, edit all three")

    # And it has to behave, not merely match. A trimmed project is the case
    # the whole block exists for.
    trimmed = os.path.join(tmp, "trimmed_proj")
    os.makedirs(os.path.join(trimmed, "source_text"))
    os.makedirs(os.path.join(trimmed, "Langmuir", "journal_requirements"))
    with io.open(os.path.join(trimmed, "source_text", "results.md"), "w",
                 encoding="utf-8") as fh:
        fh.write("# Results" + chr(10) * 2 + "Prose.")
    check("a trimmed project resolves to its own root",
          os.path.abspath(prose.drafts_dir(trimmed)),
          os.path.abspath(trimmed))
    check("...so source_text_dir points at the real folder",
          os.path.isdir(prose.source_text_dir(trimmed)), True)
    check("...and messages carry no drafts/ prefix", prose.dpfx(trimmed), "")

    full = os.path.join(tmp, "full_proj")
    os.makedirs(os.path.join(full, "drafts", "source_text"))
    with io.open(os.path.join(full, "drafts", "source_text", "results.md"),
                 "w", encoding="utf-8") as fh:
        fh.write("# Results" + chr(10) * 2 + "Prose.")
    check("a full project still resolves to drafts/",
          os.path.abspath(prose.drafts_dir(full)),
          os.path.abspath(os.path.join(full, "drafts")))
    check("...and messages keep the drafts/ prefix", prose.dpfx(full),
          "drafts/")

    # The clean-slate case: prose wiped so the skill can redraft it. The
    # journal folder is the mark that survives.
    os.remove(os.path.join(trimmed, "source_text", "results.md"))
    check("an emptied trimmed project is still trimmed",
          prose.is_trimmed(trimmed), True)

    bare = os.path.join(tmp, "bare_proj")
    os.makedirs(os.path.join(bare, "source_text"))
    check("an empty source_text/ with no journal is not a layout yet",
          os.path.abspath(prose.drafts_dir(bare)),
          os.path.abspath(os.path.join(bare, "drafts")))

    # --- the round label (item 31) ---------------------------------------
    # The live folder carries the round it holds. prose.py never renames it -
    # that is manuscript.py's `round` and `init` - but it has to resolve to
    # the same folder those two leave behind, or `length` and the density
    # checks read an empty directory and report a clean project.
    lab = os.path.join(tmp, "labelled_proj")
    os.makedirs(os.path.join(lab, "drafts", "source_text_r8"))
    with io.open(os.path.join(lab, "drafts", "source_text_r8", "results.md"),
                 "w", encoding="utf-8") as fh:
        fh.write("# Results" + chr(10) * 2 + "Prose.")
    check("a labelled folder is a drafting folder",
          os.path.abspath(prose.drafts_dir(lab)),
          os.path.abspath(os.path.join(lab, "drafts")))
    check("...and resolves as the live source text",
          prose.source_text_name(lab), "source_text_r8")
    check("...and messages name the folder a user can open",
          prose.stpfx(lab), "drafts/source_text_r8/")

    # An older label kept beside it as a record does not become the live one.
    os.makedirs(os.path.join(lab, "drafts", "source_text_r5"))
    os.makedirs(os.path.join(lab, "drafts", "source_text"))
    check("the newest label wins over an older one",
          prose.source_text_name(lab), "source_text_r8")
    check("...and over the unlabelled folder, which reads as round 0",
          [n for _r, n in prose.source_text_folders(
              os.path.join(lab, "drafts"))],
          ["source_text", "source_text_r5", "source_text_r8"])


# ---------------------------------------------------------------------------
# Per-section and learned budgets - prose spec 5.9
# ---------------------------------------------------------------------------

def test_section_budgets(tmp: str) -> None:
    section("A budget per section, and where each one came from (5.9)")

    root = os.path.join(tmp, "budgets")
    st = os.path.join(root, "source_text")
    os.makedirs(st)

    def write(name: str, body: str) -> str:
        path = os.path.join(st, f"{name}.md")
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return path

    # Nine loose numbers in a methods paragraph: over `low` four times over,
    # and exactly the shape that produced 8 of 11 findings on the one real run.
    methods = write("methods", "# Methods\n\n" + (
        "The chamber was baked at 420 K for 12 h, evacuated to 2 x 10-9 Torr, "
        "and the sample sputtered at 1.5 kV for 20 min with 25 mA emission "
        "and a 45 degree angle before annealing for 5 min.\n"))
    abstract = write("title_abstract", "# Title and abstract\n\n" + (
        "Particles of 12 nm fell to 4 nm as coverage dropped from 0.42 to "
        "0.11 over 30 min at 450 C, with binding energies of 2.1 eV and "
        "4.6 eV and a work function change of 0.6 V across 3 samples.\n"))

    res = prose.density([methods])
    check("methods defaults to high, so it produces no budget findings",
          [f for f in res["findings"]
           if f["rule"] in ("inline_budget", "cluster_budget")], [])
    check("...and says so in the budget table",
          res["budgets"]["methods"]["level"], "high")
    check("...sourced from the built-in default",
          res["budgets"]["methods"]["source"], "default")

    res = prose.density([methods], "low")
    check("the scalar spelling still forces every section",
          any(f["rule"] == "inline_budget" for f in res["findings"]), True)
    check("...and is marked as forced, not as a section's own budget",
          res["budgets"]["methods"]["source"], "forced")

    res = prose.density([abstract])
    rules = [f["rule"] for f in res["findings"]]
    check("the abstract's budget counts the section, not a rate",
          "section_inline_budget" in rules, True)
    check("...so no per-paragraph budget is quoted at it",
          res["paragraphs"][0]["budget"], None)
    check("...and the basis is on the record", res["budgets"]
          ["title_abstract"]["basis"], "absolute")

    # Resolution order: the project's config beats a learned budget, which
    # beats the built-in default. Order 1 above 2 is the whole point - a value
    # the user typed into THIS project is a statement about this paper.
    learned = {"methods": {"inline": 8.0, "clusters": 6.0, "basis": "per100",
                           "source": "learned:LR-031"}}
    res = prose.density([methods], learned=learned)
    check("a learned budget is used when the config is silent",
          res["budgets"]["methods"]["inline"], 8.0)
    check("...and names the rule it came from",
          res["budgets"]["methods"]["source"], "learned:LR-031")
    check("...and every finding carries the source with it",
          all("budget_source" in f for f in res["findings"]
              if f["rule"].endswith("budget")), True)

    res = prose.density([methods], config={"methods": "low"}, learned=learned)
    check("the project's own config beats the learned budget",
          res["budgets"]["methods"]["source"], "config")
    check("...and the level it named is the one enforced",
          res["budgets"]["methods"]["inline"], 2.0)

    res = prose.density(
        [methods], config={"methods": {"inline": 9, "clusters": 4}})
    check("an explicit rate in the config resolves",
          (res["budgets"]["methods"]["inline"],
           res["budgets"]["methods"]["basis"]), (9.0, "per100"))

    res = prose.density([methods], config={"methods": "enormous"})
    check("a level the config invented is reported, not obeyed",
          any(f["rule"] == "unreadable_budget" for f in res["findings"]), True)
    check("...and the built-in default is used instead",
          res["budgets"]["methods"]["level"], "high")

    # 4.3: what softens on a frozen section is every check whose remedy is
    # "write more prose". The truth checks never soften.
    dense = write("results", "# Results\n\n" + (
        "Values were 1 2 3 4 5 6 7 8 and 9 across the runs.\n"))
    res = prose.density([dense], "low", advisory=["results"])
    sev = {f["rule"]: f["severity"] for f in res["findings"]}
    check("a frozen section's budget finding is advisory",
          sev.get("inline_budget"), "advisory")
    check("...and its one-third cap is still an error",
          sev.get("numeric_fraction"), "error")


def test_density_config_reader(tmp: str) -> None:
    section("writing_config.yml, in both spellings")

    path = os.path.join(tmp, "writing_config.yml")

    def write(body: str) -> None:
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(body)

    write("number_density: low            # low | medium | high\n"
          "number_density_waivers:\n  - \"results ¶3 # kept\"\n")
    cfg = prose.load_density_config(path)
    check("the scalar spelling reads as a level", cfg["level"], "low")
    check("...with no section mapping beside it", cfg["sections"], {})
    check("a quoted waiver keeps its own '#'", cfg["waivers"],
          ["results ¶3 # kept"])
    check("the pair manuscript.py reads is unchanged",
          prose.load_waivers(path), ("low", ["results ¶3 # kept"]))

    write("number_density:\n"
          "  title_abstract: {inline: 6, clusters: 2, basis: absolute}\n"
          "  introduction:   low\n"
          "  methods:        high\n"
          "number_density_waivers: []\n")
    cfg = prose.load_density_config(path)
    check("a mapping reads as sections", sorted(cfg["sections"]),
          ["introduction", "methods", "title_abstract"])
    check("...a named level stays a name", cfg["sections"]["introduction"],
          "low")
    check("...and a rate reads as numbers",
          cfg["sections"]["title_abstract"],
          {"inline": 6.0, "clusters": 2.0, "basis": "absolute"})
    check("...with no scalar level to fight it", cfg["level"], None)

    write("number_density:\n"
          "  title_abstract: {inline: 6,\n"
          "                   clusters: 2, basis: absolute}\n")
    cfg = prose.load_density_config(path)
    check("a rate wrapped over two lines is read whole",
          cfg["sections"]["title_abstract"].get("basis"), "absolute")

    write("number_density: low\n  results: high\n")
    cfg = prose.load_density_config(path)
    check("a level AND a mapping is reported rather than half-read",
          bool(cfg["problems"]), True)


# ---------------------------------------------------------------------------
# voice - an instrument, never a gate (prose spec 3.4)
# ---------------------------------------------------------------------------

def test_voice(tmp: str) -> None:
    section("voice reports how the prose reads, and grades nothing (3.4)")

    st = os.path.join(tmp, "voice", "source_text")
    os.makedirs(st)
    path = os.path.join(st, "introduction.md")
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(
            "# Introduction\n\n"
            "Several factors influence film growth. Palladium films grown on "
            "titania are the standard model system for metal-support "
            "interaction, and their thermal stability governs every catalytic "
            "application of the pair [@smith2020; @jones2019]. Growth "
            "temperature sets the particle size distribution.\n\n"
            "The determination of the coverage was performed by ion "
            "scattering. It may indicate that the layer is continuous.\n")

    res = prose.voice([path])
    sec = res["sections"][0]
    check("it measures the section", sec["section"], "introduction")
    check("sentence length carries its spread, not just its mean",
          sorted(sec["sentence_words"]), ["max", "mean", "median", "sd"])
    check("the longest sentences come back with their text",
          sec["longest"][0]["words"] >= 25, True)
    check("citekeys per sentence are counted",
          sec["citekeys"]["sentences_with_two_plus"], 1)
    # "may indicate" is two hedges, not one: the count is of hedging words,
    # because "may" and "indicate" each soften the claim on their own.
    check("hedging is counted per section", sec["hedges"]["count"], 2)
    check("nominalizations beside a weak verb are counted separately",
          sec["nominalizations"]["with_weak_verb"] >= 1, True)
    check("the passive is measured", sec["passive"]["sentences"] >= 1, True)
    check("an opener length is recorded for every paragraph",
          len(sec["openers"]["each"]), 2)

    res = prose.voice([path], per_paragraph=True)
    para = res["sections"][0]["paragraph_detail"][0]
    check("a throat-clearing lead ranks last in its paragraph",
          (para["lead_rank"], para["lead_rank_of"]), (3, 3))
    check("...and the lead itself is quoted, not judged",
          para["lead"].startswith("Several factors"), True)
    check("nothing in voice is a finding", "findings" in res, False)


# ---------------------------------------------------------------------------
# metaprose - a flag, never a gate (prose spec 0.2)
# ---------------------------------------------------------------------------

def test_metaprose(tmp: str) -> None:
    section("Pipeline text that reached the manuscript (0.2)")

    st = os.path.join(tmp, "meta", "source_text")
    os.makedirs(st)
    path = os.path.join(st, "results.md")
    original = (
        "# Results\n\n"
        "This section is deliberately without numbers, because the stats "
        "output was not available when the outline was written.\n\n"
        "The film was deliberately over-oxidized to test the limit of the "
        "reduction pathway.\n\n"
        "TODO check the sputter sequence against the logbook.\n")
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(original)

    res = prose.metaprose([path])
    locs = [f["location"] for f in res["findings"]]
    check("prose about the document itself is flagged",
          "results ¶1" in locs, True)
    check("...and real science that happens to say 'deliberately' is not",
          "results ¶2" in locs, False)
    check("a bare TODO is flagged", "results ¶3" in locs, True)
    check("every finding is informational",
          {f["severity"] for f in res["findings"]}, {"info"})

    res = prose.metaprose([path], insert=True)
    after = _slurp(path, encoding="utf-8")
    check("the flag is written into the file", res["written"], [path])
    check("...with the wording the spec names",
          "**[FLAG: meta-prose — pipeline text, not manuscript text]**"
          in after, True)
    check("...and the paragraph's own words are untouched",
          "This section is deliberately without numbers, because the stats "
          "output was not available when the outline was written." in after,
          True)
    check("...and the paragraph it did not flag is byte-identical",
          "The film was deliberately over-oxidized to test the limit of the "
          "reduction pathway." in after, True)

    flags = prose.flags([path])
    check("the flag collector reads it as a known type",
          [f["type"] for f in flags["flags"] if f["type"] == "meta-prose"],
          ["meta-prose", "meta-prose"])
    check("...so it is not reported as an unknown type",
          flags["unknown_types"], [])

    res = prose.metaprose([path], insert=True)
    check("a second pass does not flag what is already flagged",
          res["written"], [])


def test_abstract_claim(tmp: str) -> None:
    section("The abstract names one take-home, and the paper carries it "
            "(1.3)")

    root = os.path.join(tmp, "claim")
    st = os.path.join(root, "drafts", "source_text")
    reports = os.path.join(root, "reports")
    os.makedirs(st, exist_ok=True)
    os.makedirs(reports, exist_ok=True)

    def put(rel: str, body: str) -> None:
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(body)

    put("drafts/source_text/results.md",
        "# Results\n\nPd nanoparticles on TiO2(110) decompose at 450 C, "
        "below the 620 C temperature at which the support reduces.\n")
    put("drafts/source_text/discussion.md",
        "# Discussion\n\nThe reduction is therefore a consequence of "
        "decomposition and not its cause, since Pd nanoparticles decompose "
        "below the temperature at which the support reduces.\n")
    put("drafts/source_text/title_abstract.md",
        "# Title\n\nPd decomposition on TiO2(110)\n\n# Abstract\n\n"
        "Pd nanoparticles on TiO2(110) decompose below the temperature at "
        "which the support reduces, so the reduction is a consequence and "
        "not the cause.\n")

    # Absent is a FINDING, not a build failure. What is missing is the
    # commitment, and saying so is the whole of the remedy.
    res = prose.claim(root)
    check("no take-home written anywhere is a finding, not a failure",
          [f["rule"] for f in res["findings"]], ["claim_absent"])
    check("...and it is a warning",
          {f["severity"] for f in res["findings"]}, {"warning"})

    # A round written before item 43 has the old file, and its record must
    # not go invisible because the engine moved.
    put("reports/abstract_claim.md",
        "## Take-home\nSomething happened.\n\n## Supported by\nresults\n")
    check("an abstract_claim.md from an earlier round is still read",
          (os.path.basename(prose.claim_report_path(root)),
           prose.claim(root)["claim"]),
          ("abstract_claim.md", "Something happened."))
    put("reports/prose_report.md", "## Take-home\nThe new one.\n")
    check("...and the prose report wins once there is one",
          prose.claim(root)["claim"], "The new one.")
    os.remove(os.path.join(root, "reports", "abstract_claim.md"))
    os.remove(os.path.join(root, "reports", "prose_report.md"))

    CLAIM = ("## Take-home\nPd nanoparticles on TiO2(110) decompose below "
             "the temperature at which the support reduces, so the reduction "
             "is a consequence and not the cause.\n\n## Supported by\n"
             "source_text/results.md \u00b64, source_text/discussion.md "
             "\u00b62\n")
    put("reports/prose_report.md", CLAIM)
    res = prose.claim(root)
    check("the take-home is read out of prose_report.md now (item 43)",
          os.path.basename(res["path"]), "prose_report.md")
    check("a claim whose words are in the sections it cites, carried by the "
          "abstract, reports nothing", res["findings"], [])
    check("...and the sections it names are resolved",
          res["sections"], ["results", "discussion"])
    check("...and the abstract sentence carrying it is named",
          "Pd nanoparticles" in res["abstract_sentence"], True,
          "the `# Abstract` label is not a sentence of the abstract")

    # The two mechanical conditions, one at a time.
    put("drafts/source_text/discussion.md",
        "# Discussion\n\nWavelength calibration drifted between runs.\n")
    res = prose.claim(root)
    check("a claim whose content words appear in no cited section is "
          "reported",
          [f["rule"] for f in res["findings"]], ["claim_unsupported"])
    check("...naming the section it cited", res["findings"][0]["location"],
          "discussion.md")

    put("drafts/source_text/discussion.md",
        "# Discussion\n\nThe reduction is therefore a consequence of "
        "decomposition and not its cause, since Pd nanoparticles decompose "
        "below the temperature at which the support reduces.\n")
    put("drafts/source_text/title_abstract.md",
        "# Title\n\nPd decomposition on TiO2(110)\n\n# Abstract\n\n"
        "Temperature-programmed desorption was used. Values were "
        "obtained.\n")
    res = prose.claim(root)
    check("an abstract carrying no sentence with the claim is reported",
          [f["rule"] for f in res["findings"]], ["claim_not_in_abstract"],
          "an agent that has not committed to one claim signals importance "
          "the only other way it can - by stating more results")

    put("reports/prose_report.md", "## Supported by\nresults\n")
    check("a file with no take-home sentence is reported",
          "claim_empty" in [f["rule"] for f in prose.claim(root)["findings"]],
          True)
    put("reports/prose_report.md", "## Take-home\nSomething happened.\n")
    check("a take-home with nothing behind it is reported",
          "claim_unsourced" in
          [f["rule"] for f in prose.claim(root)["findings"]], True)

    # Item 38. A person writing this report writes a one-line note under each
    # heading. The old parse read the note under Take-home as part of the
    # claim - diluting a 100% abstract overlap to 45% - and the note under
    # Supported by as citing the two files it was saying carry nothing.
    put("drafts/source_text/discussion.md", "")
    put("drafts/source_text/title_abstract.md",
        "# Title\n\nPd decomposition on TiO2(110)\n\n# Abstract\n\n"
        "Pd nanoparticles on TiO2(110) decompose below the temperature at "
        "which the support reduces, so the reduction is a consequence and "
        "not the cause.\n")
    NOTED = ("## Take-home\nPd nanoparticles on TiO2(110) decompose below "
             "the temperature at which the support reduces, so the reduction "
             "is a consequence and not the cause.\n\n"
             "Note: this is the r1 take-home; results.md and discussion.md "
             "are still empty stubs.\n\n"
             "## Supported by\nsource_text/results.md ¶4\n\n"
             "Note: results.md and discussion.md carry nothing yet, so "
             "nothing else is cited.\n")
    put("reports/prose_report.md", NOTED)
    res = prose.claim(root)
    check("a note under Take-home is not part of the claim",
          res["claim"].endswith("not the cause."), True,
          "the take-home is the first non-empty line and then stop")
    check("...so the abstract overlap is measured against the claim alone",
          res["abstract_overlap"], 1.0)
    check("a prose note under Supported by cites nothing",
          res["sections"], ["results"],
          "the note named discussion.md while saying it carries nothing")
    check("...and the file passes clean", [f["rule"] for f in res["findings"]],
          [])
    check("...with the ignored line reported, and why",
          [i["line"].startswith("Note:") for i in res["ignored"]], [True])

    # Moving the notes into HTML comments did not help either: the comment
    # was parsed too. It is invisible in every other consumer of these files.
    put("reports/prose_report.md",
        "## Take-home\nPd nanoparticles on TiO2(110) decompose below the "
        "temperature at which the support reduces, so the reduction is a "
        "consequence and not the cause.\n"
        "<!-- results.md and discussion.md are still empty stubs -->\n\n"
        "## Supported by\nsource_text/results.md ¶4\n"
        "<!-- discussion.md carries nothing yet -->\n")
    res = prose.claim(root)
    check("an HTML comment is stripped before the claim is parsed",
          (res["sections"], res["ignored"], [f["rule"] for f in
                                             res["findings"]]),
          (["results"], [], []))

    # The locator grammar, at its edges.
    put("reports/prose_report.md",
        "## Take-home\nSomething happened.\n\n## Supported by\n"
        "- source_text/results.md ¶4, discussion ¶2\n")
    check("a bulleted, comma-joined line of locators is still locators",
          prose.parse_abstract_claim(
              io.open(os.path.join(root, "reports", "prose_report.md"),
                      encoding="utf-8").read())["sections"],
          ["results", "discussion"])
    put("reports/prose_report.md",
        "## Take-home\nSomething happened.\n\n## Supported by\n"
        "see the r0 build for context\n")
    res = prose.claim(root)
    check("a Supported by holding only prose is unsourced, not miscited",
          (res["sections"], "claim_unsourced" in
           [f["rule"] for f in res["findings"]]), ([], True))
    check("...and the finding names the line it ignored",
          "see the r0 build" in
          [f for f in res["findings"]
           if f["rule"] == "claim_unsourced"][0]["detail"], True,
          "an author looking at three lines under the heading needs to be "
          "told the shape is wrong, not that the abstract is")
    put("reports/prose_report.md",
        "## Take-home\nSomething happened.\n\n## Supported by\n"
        "supporting_information.md\n")
    res = prose.claim(root)
    check("a locator naming no section file is ignored with a reason",
          [i["why"].startswith("names no section file")
           for i in res["ignored"]], [True])


def test_outline_waiver(tmp: str) -> None:
    section("A line settled for good is not drafted and not counted (4.4)")

    root = os.path.join(tmp, "owaive")
    os.makedirs(os.path.join(root, "plan"), exist_ok=True)
    os.makedirs(os.path.join(root, "drafts", "source_text"), exist_ok=True)
    path = os.path.join(root, "plan", "outline.md")

    def put(body: str) -> None:
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(body)

    put("# Structure\n\n## Methods\n\n"
        "- Report the chamber base pressure [methods_facts]\n"
        "- [waived: superseded by the XPS control] Report the sputter-clean "
        "sequence [methods_facts]\n")

    res = prose.outline(root)
    claims = [ln["claim"] for ln in res["lines"]]
    check("a waived line is not in the outline the drafter is handed",
          claims, ["Report the chamber base pressure"])
    check("...and not counted in adherence",
          res["counts"]["outline_lines"], 1)
    check("...but it IS reported, with its reason",
          [(w["line"], w["because"]) for w in res["waived"]],
          [(6, "superseded by the XPS control")])
    check("...and never as an uncovered point",
          [f for f in res["findings"]
           if "sputter" in str(f.get("claim", ""))], [])
    check("the contract tells a writer how to spell one",
          res["contract"]["waived_mark"], "[waived: reason]")

    # The marker cannot become a silent delete key.
    put("# Structure\n\n## Methods\n\n"
        "- [waived] Report the sputter-clean sequence [methods_facts]\n")
    res = prose.outline(root)
    check("[waived] with no reason is reported",
          [f["rule"] for f in res["findings"]
           if f["rule"] == "waiver_unexplained"], ["waiver_unexplained"],
          "an unexplained waiver is the thing nobody can audit in six months")
    check("...and it is a warning, not a stop",
          [f["severity"] for f in res["findings"]
           if f["rule"] == "waiver_unexplained"], ["warning"])

    put("# Structure\n\n## Methods\n\n- [waived: nothing follows]\n")
    check("a marker with no claim after it is not an outline line at all",
          (prose.outline(root)["counts"]["outline_lines"],
           prose.outline(root)["counts"]["waived"]), (0, 0))


def _put(root: str, rel: str, body: str) -> str:
    path = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def test_readability(tmp: str) -> None:
    section("readability - the countable half, and it never gates "
            "(comprehension-check 3)")

    root = os.path.join(tmp, "readable")
    _put(root, "drafts/source_text_r1/introduction.md",
         "# Introduction\n\n"
         "Self-assembled monolayers (SAMs) of alkanethiols on Au(111) form "
         "dense films. XPS and DFT place the S 2p binding energy near 162 "
         "eV, and CH3 termination lowers the surface energy.\n\n"
         "This shows that the headgroup is chemisorbed. Desorption begins "
         "near 350 K.\n")
    paths = prose.source_files(root)
    res = prose.readability(paths)

    check("a bare demonstrative opener is found",
          [f["rule"] for f in res["findings"]
           if f["rule"] == "bare_demonstrative"], ["bare_demonstrative"])
    check("...and the given-new gap between the two paragraphs",
          any(f["rule"] == "given_new_gap" for f in res["findings"]), True)
    check("every finding is advisory, at every dial value",
          sorted({f["severity"] for f in res["findings"]}), ["advisory"])
    check("...and the command says so of itself", res["advisory"], True)
    check("the reader it measured for is stated", res["audience"],
          "specialist")
    check("...and no reading level is computed",
          "no reading level" in res["register_note"], True)

    # The exemptions, which are the half the spec got wrong and the
    # measurement corrected: elements and SI units remove none of the real
    # firings; chemical FORMULAE remove almost all of them.
    tokens = [f.get("token") for f in
              prose.readability(paths, report_abbreviations=True)["findings"]
              if f["rule"] == "abbreviation_undefined"]
    check("a defined abbreviation does not fire", "SAMs" in tokens, False)
    check("a chemical formula is not an abbreviation",
          [t for t in tokens if t in ("CH3", "C6H4OH", "COOH", "NH2")], [])
    check("an element symbol is not either",
          [t for t in tokens if t in ("Au", "Fe")], [])
    check("...and the count of what was exempted is reported",
          prose.readability(paths, report_abbreviations=True)
          ["abbreviations"]["exempted"] > 0, True)

    check("abbreviation findings are OFF by default",
          [f for f in res["findings"]
           if f["rule"] == "abbreviation_undefined"], [])
    check("...and the report says why, with the measurement",
          "21 of" in res["abbreviations"]["note"]
          or "not calibrated" in res["abbreviations"]["note"]
          or "18 of" in res["abbreviations"]["note"], True,
          res["abbreviations"]["note"][:120])
    check("...while --abbreviations turns them on",
          any(f["rule"] == "abbreviation_undefined" for f in
              prose.readability(paths, report_abbreviations=True)["findings"]),
          True)

    known = prose.readability(paths, known_abbreviations=["XPS"],
                              report_abbreviations=True)
    check("a known_abbreviations entry produces no firing",
          [f for f in known["findings"] if f.get("token") == "XPS"], [])

    # given-new on a shuffled fixture against the same one in order.
    ordered = os.path.join(tmp, "ordered")
    _put(ordered, "drafts/source_text_r1/introduction.md",
         "# A\n\nThe monolayer forms on gold.\n\n"
         "The monolayer then orders over several hours.\n\n"
         "The monolayer reaches full order at room temperature.\n")
    res_o = prose.readability(prose.source_files(ordered))
    shuffled = os.path.join(tmp, "shuffled")
    _put(shuffled, "drafts/source_text_r1/introduction.md",
         "# A\n\nThe monolayer forms on gold.\n\n"
         "Buffer strength was varied.\n\n"
         "Crystal symmetry follows from packing.\n")
    res_s = prose.readability(prose.source_files(shuffled))
    check("paragraphs in order bridge",
          [f for f in res_o["findings"] if f["rule"] == "given_new_gap"], [])
    check("...and shuffled ones do not",
          len([f for f in res_s["findings"]
               if f["rule"] == "given_new_gap"]) >= 1, True)

    # The register floor: real published sentence lengths produce no
    # length-based finding, because there is no length-based finding.
    long_one = os.path.join(tmp, "longsent")
    _put(long_one, "drafts/source_text_r1/introduction.md",
         "# A\n\nProtein adsorption on the oligo(ethylene glycol) terminated "
         "alkanethiol monolayer surfaces prepared by solution deposition "
         "onto evaporated gold films, which has been studied extensively "
         "across a wide range of buffer conditions and surface chemistries "
         "over three decades, remains incompletely explained.\n")
    res_l = prose.readability(prose.source_files(long_one))
    check("a 40-word sentence produces no length finding",
          [f for f in res_l["findings"] if "length" in f["rule"]], [])
    check("...but its subject-verb gap is reported, which is the mechanic",
          any(f["rule"] == "subject_verb_gap" for f in res_l["findings"]),
          True)


def test_opening_sentence(tmp: str) -> None:
    section("The first sentence states the stake, not the provenance "
            "(item 85)")

    root = os.path.join(tmp, "opening")
    _put(root, "project.yml", 'title: "p"\n')
    _put(root, "drafts/source_text/title_abstract.md",
         "# Title\n\n## Abstract\n\nCapillary morphogenesis gene 2 was "
         "named for the screen that found it. We measured its ligand "
         "affinity.\n")
    _put(root, "drafts/source_text/introduction.md",
         "# Introduction\n\nThe receptor takes its name from the capillary "
         "morphogenesis screen. Nobody has measured its affinity.\n")
    _put(root, "drafts/source_text/methods.md",
         "# Methods\n\nThe protein was first identified by affinity "
         "chromatography, which is how we purified it here.\n")

    res = prose.readability(prose.source_files(root))
    fired = [f for f in res["findings"]
             if f["rule"] == "opening_states_provenance"]
    check("both openings are reported", sorted(f["location"] for f in fired),
          ["introduction", "the abstract"],
          "the abstract inherits the construction because it is drafted "
          "from the drafted sections")
    check("...with the clause that fired, so it can be judged",
          all(f.get("sentence") for f in fired), True)
    check("...as advisory, because nothing about how prose reads may gate",
          sorted({f["severity"] for f in fired}), ["advisory"])
    check("a naming clause anywhere else in the paper is ordinary and is "
          "not reported",
          [f["location"] for f in fired if f["location"] == "methods"], [],
          "this reads ONE sentence per section on purpose - the position "
          "is what makes it worth a rule")

    # The two directions that decide whether a check is worth having.
    fires = [
        "CMG2 was named for the screen that identified it.",
        "The receptor takes its name from that screen.",
        "This protein was first identified in 2001.",
        "The term ferroptosis was coined in 2012.",
        "Anthrax toxin receptor 2 is named after the screen that found it.",
    ]
    quiet = [
        "Nobody can predict which monolayers survive above 400 K, and "
        "process engineers pay for the gap in failed devices.",
        "Monolayer desorption sets the ceiling on every device built on one.",
        "We first identified the desorption onset at 430 K in this work.",
        "Three groups report coverages that differ by a factor of two.",
        "The structure was determined by X-ray crystallography.",
    ]
    check("it fires on an opening whose subject IS the naming",
          [bool(prose.opening_provenance(x)) for x in fires], [True] * 5)
    check("...and on nothing that merely mentions finding something",
          [bool(prose.opening_provenance(x)) for x in quiet], [False] * 5,
          "measured against 24 published chemistry openings in "
          "specs/probes/opening-2026-09-16/: it fires on 0 of them")

    clean = os.path.join(tmp, "opening_ok")
    _put(clean, "project.yml", 'title: "p"\n')
    _put(clean, "drafts/source_text/title_abstract.md",
         "# Title\n\n## Abstract\n\nThe ceiling on monolayer stability is "
         "not known, and every device built on one inherits the "
         "uncertainty.\n")
    _put(clean, "drafts/source_text/introduction.md",
         "# Introduction\n\nNobody can say which monolayers survive above "
         "400 K. Three groups disagree by a factor of two.\n")
    check("an opening that states the stake is silent",
          [f["rule"] for f in prose.readability(prose.source_files(clean))
           ["findings"] if f["rule"] == "opening_states_provenance"], [])


def test_spelling(tmp: str) -> None:
    """American spelling, corrected rather than reported (user-asks 3).

    The four exclusions are the whole of the risk here. A conversion that
    edits a quotation, a proper noun, a code span or a published title is a
    change to somebody else's words, and it arrives inside a command whose
    whole point is that it writes without asking. So each of the four gets a
    case, and each case is a sentence where the naive regex is wrong.
    """
    section("American spelling, fixed and not reported (user-asks 3)")

    st = os.path.join(tmp, "spell", "drafts", "source_text")
    os.makedirs(st)
    path = os.path.join(st, "discussion.md")
    original = (
        "# Discussion\n\n"
        "The colour of the film and its behaviour under vacuum were "
        "characterised at the centre of each 5 mm coupon, and the sulphur "
        "signal was analysed against a 10 micrometre standard.\n\n"
        "Work was carried out at the Medical Research Centre, which is not "
        "a spelling this pass may touch.\n\n"
        "Nobody disputed the finding: \"the colour change is reversible\", "
        "they wrote, and the quotation keeps their spelling.\n\n"
        "The parameter `colour_mode` is set by the renderer, and "
        "[@smith2020colour] is a citekey.\n")
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(original)

    res = prose.spelling([path])
    words = sorted({f["british"] for f in res["findings"]})
    check("every British form in running prose is found",
          words,
          ["analysed", "behaviour", "centre", "characterised", "colour",
           "micrometre", "sulphur"])
    check("...and each carries its American form",
          {f["british"]: f["american"] for f in res["findings"]
           if f["british"] in ("colour", "centre", "sulphur", "analysed")},
          {"colour": "color", "centre": "center", "sulphur": "sulfur",
           "analysed": "analyzed"})
    check("a proper noun keeps its own spelling",
          [f["location"] for f in res["findings"] if f["proper_noun"]], [])
    check("...and there were exactly two `centre`s, one of them the name",
          original.count("entre"), 2)
    check("...so only the running-prose one is a finding",
          len([f for f in res["findings"] if f["british"] == "centre"]), 1)
    check("a quotation keeps the other author's spelling",
          [f for f in res["findings"] if f.get("in_quote")], [])
    check("nothing is written without --fix", res["written"], [])
    check("...and the file is untouched", _slurp(path, encoding="utf-8"),
          original)

    res = prose.spelling([path], fix=True)
    after = _slurp(path, encoding="utf-8")
    check("--fix writes the file", res["written"], [path])
    check("the running prose is American now",
          "The color of the film and its behavior under vacuum were "
          "characterized at the center" in after, True)
    check("...including the ones that are not a rule",
          "sulfur signal was analyzed against a 10 micrometer" in after, True)
    check("the proper noun survived", "Medical Research Centre" in after, True)
    check("the quotation survived",
          '"the colour change is reversible"' in after, True)
    check("the code span survived", "`colour_mode`" in after, True)
    check("the citekey survived", "[@smith2020colour]" in after, True)

    check("a second pass has nothing left to do",
          prose.spelling([path], fix=True)["counts"]["findings"], 0)

    # Case is the user's, not the map's: a sentence-initial Colour is still a
    # sentence-initial word after the fix, and an all-caps heading stays one.
    cased = os.path.join(st, "methods.md")
    with io.open(cased, "w", encoding="utf-8") as fh:
        fh.write("# Methods\n\nColour was recorded. COLOUR was the label on "
                 "the dial.\n")
    prose.spelling([cased], fix=True)
    check("case is preserved in both directions",
          "Color was recorded. COLOR was" in _slurp(cased, encoding="utf-8"),
          True)

    # The words that look like the rule and are not. `analyses` is the plural
    # of `analysis` in both dialects, and converting it to `analyzes` turns a
    # noun into a verb - the one conversion in this class that changes what a
    # sentence means.
    safe = os.path.join(st, "results.md")
    with io.open(safe, "w", encoding="utf-8") as fh:
        fh.write("# Results\n\nThe analyses agree. Exercise and compromise "
                 "and surprise are not British. The dialogue continued and "
                 "the laboratory was closed.\n")
    res = prose.spelling([safe])
    check("the noun plural `analyses` is left alone",
          [f["british"] for f in res["findings"]], [])

    # The map itself, held against two lists rather than against a sample of
    # prose. The first list is the one that matters: every word in it would
    # either change what a sentence means or is already American, and a
    # suffix rule would have swept up most of them. `analyses` is the
    # sharpest - the plural of `analysis` in both dialects, and converting it
    # turns a noun into a verb.
    must_not_convert = [
        "analyses", "catalyses", "hydrolyses", "exercise", "surprise",
        "compromise", "comprise", "revise", "precise", "concise", "devise",
        "promise", "premise", "enterprise", "franchise", "advertise",
        "supervise", "televise", "despise", "improvise", "arise",
        "laboratory", "parameter", "diameter", "perimeter", "dialogue",
        "epilogue", "prologue", "archaeology", "gauge", "crystallize",
        "molecule", "molecular", "rigorous", "vigorous", "humorous",
    ]
    check("no word that would change meaning is in the map",
          [w for w in must_not_convert if w in prose.BRITISH_TO_AMERICAN], [])

    must_convert = [
        "colour", "centre", "analysed", "sulphur", "aluminium", "grey",
        "behaviour", "characterised", "optimisation", "signalling",
        "modelling", "labelled", "micrometre", "litre", "fibre", "defence",
        "licence", "programme", "catalogue", "haemoglobin", "oedema",
        "foetal", "paediatric", "tumour", "vapour", "ageing", "judgement",
        "whilst", "focussed", "fulfil", "enrolment", "skilful", "mould",
        "anaemia", "leukaemia", "diarrhoea", "ischaemic", "oestrogen",
        "caesium", "disulphide", "neutralisation", "internalised",
    ]
    check("every British form a chemistry or biochemistry paper reaches for "
          "is in it",
          [w for w in must_convert if w not in prose.BRITISH_TO_AMERICAN], [])
    check("...and `fulfil` converts the OTHER way, which a one-way rule "
          "gets wrong",
          prose.BRITISH_TO_AMERICAN["fulfil"], "fulfill")

    # A .bib is quoted material with a DOI attached, and the refusal is at the
    # door rather than at the word.
    bib = os.path.join(tmp, "spell", "drafts", "references.bib")
    with io.open(bib, "w", encoding="utf-8") as fh:
        fh.write("@article{a, title = {On the colour of copper}, year={2020}}\n")
    res = prose.spelling([bib], fix=True)
    check("a .bib path is refused rather than corrected",
          [p["path"] for p in res["refused"]], [bib])
    check("...and the published title is untouched",
          "colour of copper" in _slurp(bib, encoding="utf-8"), True)


def test_ai_voice(tmp: str) -> None:
    """The countable half of the AI-voice audit (prose 10.3, tests 10.8).

    The first four checks here are the ones that decide whether this ships at
    all. 10.1 draws the line the rest of the section rests on: a check that is
    WRONG WHEN IT FIRES ON GOOD PROSE is calibrated against published work as
    a floor, and a style principle the paper is trying to beat the average on
    is not. The two tiers inside #7 and #17 are that line drawn inside a
    single pattern, and the floor fixture below is how it is held.
    """
    section("The 25 AI-voice patterns, the countable 15 (prose 10.3)")
    NL = chr(10)

    st = os.path.join(tmp, "aivoice", "source_text")
    os.makedirs(st)

    # --- the floor: real published-register science ---------------------
    # Every one of these is a true sentence a chemist would write, and NOT
    # ONE may produce an asserted finding. A check that fires here is the
    # abbreviation check all over again (comprehension-check 3.1, item 75).
    floor = os.path.join(st, "results.md")
    with io.open(floor, "w", encoding="utf-8") as fh:
        fh.write(
            "# Results" + NL + NL
            + "The classifier was robust to noise across the high-throughput "
            + "screening set, and a comprehensive survey of 24 strains "
            + "confirmed the assignment." + NL + NL
            + "Lattice spacings of 2\u2013""3 nm were measured on pages "
            + "118\u2013""121 of the deposited record, and XPS, TEM and XRD "
            + "confirmed the assignment." + NL)

    res = prose.ai_voice([floor])
    asserted = [f for f in res["findings"] if f["tier"] == "asserted"]
    check("real science produces no ASSERTED finding - `robust to noise`, "
          "`high-throughput screening`, `comprehensive survey`", asserted, [],
          str([f["detail"] for f in asserted]))
    check("...and the contextual tier still reports them, with the sentence, "
          "for module 1e to judge",
          sorted({f["rule"] for f in res["findings"]}),
          ["ai_vocabulary_contextual", "hyphen_compound_contextual"])
    check("...each carrying the sentence it fired on",
          all(len(f["text"]) > 20 for f in res["findings"]), True)

    # --- the en-dash trap, measured before it was written ----------------
    # The Biochem Res Int 2022 paper in writing_guides/ carries 22 en dashes
    # and all 22 are number ranges. A check counting "em/en dashes" reports a
    # chemistry paper's `2-3 nm` as a style tell.
    d = res["densities"][0]
    check("a number range contributes nothing to the em-dash density",
          (d["em_dashes"], d["em_dashes_per_1000"]), (0, 0.0))
    check("...and the en dashes are counted only to say they were not counted",
          d["en_dashes_not_counted"], 2)
    check("`XPS, TEM and XRD` is reported as a three-item list with its "
          "items, never as a defect - three-ness is not the pattern, padding "
          "to three is", d["three_item_lists"], 1)
    check("...and the items come back, because judging whether the third "
          "earns its place is 1e's job and it needs them",
          d["triples"][0]["items"], ["XPS", "TEM", "XRD"])

    # --- the patterns, planted ------------------------------------------
    loud = os.path.join(st, "discussion.md")
    with io.open(loud, "w", encoding="utf-8") as fh:
        fh.write(
            "# Discussion" + NL + NL
            + "In order to resolve the arrays we delved into the landscape, "
            + "and this work serves as a testament to the pivotal role of "
            + "temperature." + NL + NL
            + "Furthermore, the result is not merely a technical advance, "
            + "but rather a paradigm shift \u2014 one that showcases a "
            + "state-of-the-art, data-driven methodology \u2014 and "
            + "applications range from drug discovery to climate modeling."
            + NL + NL
            + "**Key finding:** the results may possibly suggest that order "
            + "could potentially indicate a thermophilic origin. More "
            + "research is needed." + NL + NL
            + "The participants were imaged, the subjects were scored and "
            + "individuals were ranked, so further studies are warranted."
            + NL)

    res = prose.ai_voice([loud])
    rules = res["by_rule"]
    for rule, pattern in (("filler_phrase", 21), ("copula_avoidance", 8),
                          ("ai_vocabulary", 7), ("hyphen_compound", 17),
                          ("negative_parallelism", 9), ("false_range", 12),
                          ("generic_conclusion", 23), ("hedge_stack", 22),
                          ("inline_header_list", 15),
                          ("synonym_cycling", 11)):
        check("#%d is detected (%s)" % (pattern, rule),
              rules.get(rule, 0) > 0, True, str(sorted(rules)))
    check("every finding names its audit pattern number",
          all(isinstance(f["pattern"], int) for f in res["findings"]), True)
    check("...and its location", all(f["location"] for f in res["findings"]),
          True)

    d = res["densities"][0]
    check("#13 counts the em dashes that are there", d["em_dashes"], 2)
    check("#14 counts the boldface", d["boldface"] >= 1, True, d["boldface"])
    check("#16 reports the position of a filler opener, not a maximum",
          [o["word"] for o in d["openers"]], ["furthermore"])

    # --- what must NOT be here ------------------------------------------
    # #5 is stricter elsewhere, and a second copy is a defect not a feature.
    every = set()
    for path in (floor, loud):
        every |= {f["rule"] for f in prose.ai_voice([path])["findings"]}
    for taken in ("claim_unsourced", "background_uncited", "citekey_missing",
                  "vague_attribution"):
        check("#5 is not reimplemented: no %s rule here" % taken,
              taken in every, False)

    # #18-20 are not prose. They take the FLAG path through the metaprose
    # detector - one detector family per shape (item 7) - and ai_voice must
    # emit nothing for them.
    bot = os.path.join(st, "introduction.md")
    with io.open(bot, "w", encoding="utf-8") as fh:
        fh.write(
            "# Introduction" + NL + NL
            + "I hope this helps! The arrays were resolved at 4 angstrom."
            + NL + NL
            + "This is indeed an important area, and the authors have done "
            + "well to address it." + NL + NL
            + "As of my knowledge cutoff, no structure had been deposited."
            + NL)
    check("ai_voice emits no rule of its own for #18-20",
          prose.ai_voice([bot])["counts"]["findings"], 0)
    meta = prose.metaprose([bot])
    check("...the metaprose detector catches all three instead",
          len(meta["findings"]), 3,
          str([f["detail"] for f in meta["findings"]]))
    check("...and flags them with the existing wording, not a new one",
          {f["rule"] for f in meta["findings"]}, {"meta_prose"})
    # MEASURED 2026-09-16: the two constants that existed matched 0 of 8
    # sentences from the audit file's own trigger lists, which is why this is
    # a third constant rather than a widening of either.
    check("the two older constants do not match a chatbot artifact - which "
          "is why CHATBOT_ARTIFACT_RE exists",
          any(prose.META_SUBJECT_RE.search(s) or
              prose.META_PLACEHOLDER_RE.search(s)
              for s in ("I hope this helps!", "Great question!",
                        "As of my knowledge cutoff, nothing was deposited.",
                        "This is indeed an important area of research.")),
          False)

    # --- the contract every subcommand keeps -----------------------------
    run = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "prose.py"),
         "ai_voice", loud, "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("--json on one side", run.returncode, 0, run.stderr[-200:])
    payload = json.loads(run.stdout)
    check("...and it parses", sorted(payload),
          ["by_rule", "counts", "densities", "density_rules", "findings"])
    plain = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "prose.py"),
         "ai_voice", loud],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("--json on the other side too", plain.returncode, 0)
    check("...and the text half says it is not a gate",
          "no pass/fail" in plain.stdout, True)
    check("the four density rules are named in the payload, so a consumer "
          "cannot mistake one for a finding",
          payload["density_rules"],
          ["rule_of_three", "em_dash_density", "boldface_density",
           "paragraph_opener_filler"])


def test_italics(tmp: str) -> None:
    """Latin phrases that should be italic, and the ones that must not be.

    (user-asks-2026-09-18-second 1.)

    The exclusion regions are the whole check, and they are asserted before
    anything else: the real draft this came from carries a citekey on nearly
    every line, so a matcher that excludes after matching italicises something
    inside `[@scobie2003human]` on its first real run.
    """
    section("italics - a closed list, a per-journal dial, and one that writes "
            "(user-asks-2 1)")

    root = os.path.join(tmp, "ital")
    _put(root, "project.yml", 'title: "i"\npaper_kind: review\n')
    body = (
        "# Introduction\n\n"
        "The receptor was characterised in vivo and again IN VITRO, while "
        "the assay ran in situ [@scobie2003human].\n\n"
        "Delivery was via endocytosis, i.e. the clathrin route, per se.\n\n"
        "The *in vivo* half was already italic and stays one finding short.\n")
    path = _put(root, "drafts/source_text_r1/01_introduction.md", body)

    res = prose.italics([path])
    terms = sorted(f["term"] for f in res["findings"]
                   if f["rule"] == "latin_not_italic")
    check("every `always` term outside an italic span is found",
          terms, ["in situ", "in vitro", "in vivo"])
    check("a term already inside *...* is not a finding",
          len([f for f in res["findings"]
               if f["rule"] == "latin_not_italic" and f["term"] == "in vivo"]),
          1)
    check("`via` is in the journal set and fires on nothing by default",
          [f["term"] for f in res["findings"] if f["term"] == "via"], [],
          "the one term measured in the real draft; italicising it would be "
          "this check's first false positive")
    check("...and neither does anything else in the journal set",
          [f["term"] for f in res["findings"] if f["set"] == "journal"], [])
    check("the house default is named in the payload, so nobody has to "
          "guess which rule was applied",
          res["journal"], "")
    check("...and it says the terms came from the house list",
          res["source"], "house default")

    # The dial. A journal file turns a journal-set term on, and even then the
    # finding never writes: a term that is italic in one house and roman in
    # the next is not a lookup.
    res = prose.italics([path], journal_terms=["via", "i.e."])
    on = sorted({f["term"] for f in res["findings"] if f["set"] == "journal"})
    check("a journal may turn a journal-set term on", on, ["i.e.", "via"])
    check("...and every one of them is report-only",
          sorted({f["writes"] for f in res["findings"] if f["set"] == "journal"}),
          [False])
    check("...while the `always` set still writes",
          sorted({f["writes"] for f in res["findings"]
                  if f["rule"] == "latin_not_italic" and f["set"] == "always"}),
          [True])

    # And the dial runs the other way too, which is the half that keeps the
    # always set from being a hard-coded answer: ACS sets `in vivo` roman as
    # naturalised English, the user wants it italic, and both are right.
    res = prose.italics([path], roman_terms=["in vivo"])
    check("a journal may set an `always` term roman, and it wins",
          sorted({f["term"] for f in res["findings"]
                  if f["rule"] == "latin_not_italic"}), ["in situ", "in vitro"])
    check("...and the report names which rule it applied",
          res["source"], "journal requirements.yml")
    check("...and which terms this house sets roman",
          res["terms"]["roman_here"], ["in vivo"])
    before_roman = _slurp(path, encoding="utf-8")
    prose.italics([path], fix=True, roman_terms=["in vivo", "in vitro",
                                                 "in situ"])
    check("...so --fix writes nothing when the house sets them all roman",
          _slurp(path, encoding="utf-8"), before_roman)

    # --- the exclusion regions, one case each --------------------------
    excl = _put(root, "drafts/source_text_r1/02_structure.md",
                "# In Vivo Work\n\n"
                "<!-- the in vivo comparison is in situ here -->\n\n"
                "See `run --in vivo` and [the in vivo page](http://x.tld/"
                "in-vivo) and [@vivo2003in] and http://x.tld/in%20vivo.\n\n"
                "```yaml\nmode: in vivo\n```\n\n"
                "Only this in vivo is prose.\n")
    res = prose.italics([excl])
    check("exactly one finding survives every exclusion region",
          len(res["findings"]), 1)
    check("...and it is the one in running prose",
          res["findings"][0]["line"], 11)

    # --- --fix ---------------------------------------------------------
    before = _slurp(excl, encoding="utf-8")
    res = prose.italics([excl])
    check("nothing is written without --fix", res["written"], [])
    check("...and the file is untouched", _slurp(excl, encoding="utf-8"),
          before)

    res = prose.italics([excl], fix=True)
    after = _slurp(excl, encoding="utf-8")
    check("--fix writes the file", res["written"], [excl])
    check("the prose occurrence is italic now",
          "Only this *in vivo* is prose." in after, True)
    check("the heading was not touched", "# In Vivo Work" in after, True)
    check("the comment was not touched",
          "<!-- the in vivo comparison is in situ here -->" in after, True)
    check("the code span was not touched", "`run --in vivo`" in after, True)
    check("the link was not touched",
          "[the in vivo page](http://x.tld/in-vivo)" in after, True)
    check("the citekey was not touched", "[@vivo2003in]" in after, True)
    check("the bare URL was not touched", "http://x.tld/in%20vivo" in after,
          True)
    check("the fenced block was not touched", "mode: in vivo" in after, True)
    check("a second pass has nothing left to do",
          prose.italics([excl], fix=True)["counts"]["findings"], 0)

    # Case is the user's. `IN VITRO` stays shouting after the fix.
    cased = _put(root, "drafts/source_text_r1/03_case.md",
                 "# Case\n\nIt was IN VITRO, and In Vivo, and in situ.\n")
    prose.italics([cased], fix=True)
    check("case is the user's, not the list's",
          "*IN VITRO*, and *In Vivo*, and *in situ*"
          in _slurp(cased, encoding="utf-8"), True)

    # --- gene symbols: reported, never written -------------------------
    genes = _put(root, "drafts/source_text_r1/04_genes.md",
                 "# Genes\n\nBiallelic ANTXR2 mutation causes the syndrome, "
                 "and low CMG2 mRNA tracks survival.\n\n"
                 "The ANTXR2 ectodomain contains three disulfides.\n")
    res = prose.italics([genes])
    gs = sorted({f["token"] for f in res["findings"]
                 if f["rule"] == "gene_symbol_roman"})
    check("a symbol used as a gene is reported", gs, ["ANTXR2", "CMG2"])
    check("...and the same token used as the protein is not",
          len([f for f in res["findings"]
               if f["rule"] == "gene_symbol_roman" and f["line"] == 5]), 0,
          "`the ANTXR2 ectodomain` is the protein; deciding which is judgment")
    check("...and it never writes", sorted({f["writes"] for f in res["findings"]
                                            if f["rule"] == "gene_symbol_roman"}),
          [False])

    # The correction the first real run forced. `Capillary morphogenesis gene
    # 2 (CMG2, also ANTXR2)` is the receptor's NAME - the word `gene` in it is
    # part of the name, not a claim about a gene - and read whole it fired on
    # every definition of the protein.
    named = _put(root, "drafts/source_text_r1/06_named.md",
                 "# Named\n\nCapillary morphogenesis gene 2 (CMG2, also "
                 "ANTXR2) was named for the screen that found it.\n\n"
                 "Biallelic mutations in CMG2 cause the disorder.\n")
    res = prose.italics([named])
    check("a parenthetical definition is its own scope, so the `gene` in the "
          "protein's own name is not gene context",
          sorted({f["line"] for f in res["findings"]
                  if f["rule"] == "gene_symbol_roman"}), [5])
    kept = _slurp(genes, encoding="utf-8")
    prose.italics([genes], fix=True)
    check("--fix leaves every gene-symbol finding alone",
          _slurp(genes, encoding="utf-8"), kept)

    # --- the never list --------------------------------------------------
    check("the stop list exists so `always` can never grow into it",
          sorted(prose.ITALIC_NEVER & {"data", "media", "bacteria", "criteria",
                                       "status", "versus", "agenda"}),
          ["agenda", "bacteria", "criteria", "data", "media", "status",
           "versus"])
    check("...and no term in it is in either firing list",
          sorted(w for w in prose.ITALIC_NEVER
                 if w in prose.ITALIC_ALWAYS or w in prose.ITALIC_JOURNAL), [])
    check("an element symbol is never a Latin term",
          prose.italics([_put(root, "drafts/source_text_r1/05_el.md",
                              "# E\n\nNa and Fe and CO2 were used.\n")]
                        )["counts"]["findings"], 0)

    # --- a reference file is refused at the door -------------------------
    bib = _put(root, "drafts/references.bib",
               "@article{a, title = {Studies in vivo of copper}, year={2020}}\n")
    res = prose.italics([bib], fix=True)
    check("a .bib path is refused rather than italicised",
          [p["path"] for p in res["refused"]], [bib])
    check("...and the published title is untouched",
          "Studies in vivo of copper" in _slurp(bib, encoding="utf-8"), True)

    # --- it never gates --------------------------------------------------
    cli = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "prose.py"), "italics",
         root, "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("the CLI exits 0 with findings", cli.returncode, 0)
    payload = json.loads(cli.stdout)
    check("...and the payload carries the fields a caller reads",
          sorted(set(payload) & {"findings", "written", "refused", "counts",
                                 "journal", "source"}),
          ["counts", "findings", "journal", "refused", "source", "written"])


ABB_ABSTRACT = """\
# Title

A Receptor With a von Willebrand Factor A Domain

## Abstract

The receptor's von Willebrand factor A domain carries a metal-ion-dependent
adhesion site, and the enzyme-linked immunosorbent assay (ELISA) confirmed
the interaction. Signal was recorded by QQQ throughout. Capillary
morphogenesis gene 2 (CMG2) is the subject.
"""

ABB_INTRO = """\
# Introduction

Capillary morphogenesis gene 2 (CMG2) is the subject here too, which is what
the house rule asks for.

The von Willebrand factor A (vWA) domain carries a conserved
metal-ion-dependent adhesion site (MIDAS), and the vWA fold is common across
the family.

Protective antigen binds there. Protective antigen was added first.
Protective antigen was then washed away. Protective antigen eluted last.
"""

ABB_STRUCTURE = """\
# Structure

The von Willebrand factor A domain runs 44 to 213, and the vWA fold is
conserved. MIDAS coordinates the metal.
"""

ABB_DISEASE = """\
# Disease

Point mutations in the von Willebrand or transmembrane domain retained the
protein, and vWA folding is slow.
"""

ABB_CONCLUSION = """\
# Conclusion

The metal-ion-dependent adhesion site in the von Willebrand factor A domain
admits basement-membrane collagens, and the vWA fold explains it.
"""


ONE_CONTRACT_MS = """# Introduction

Capillary morphogenesis gene 2 (CMG2, also ANTXR2) is the receptor studied
here. The von Willebrand factor A (vWA) domain binds collagen IV. Later the
CMG2 receptor is named again, and the vWA domain is named again.

Messenger RNA (mRNA) levels were measured, and the mRNA was extracted first.
X-ray photoelectron spectroscopy (XPS) confirmed it, and XPS was repeated.
"""


def test_one_abbreviation_contract(tmp: str) -> None:
    """Items 100 and 101, each verified the way its own `verify by` asks.

    100 - `readability` required an upper-case FIRST character, so `vWA`,
          `mRNA` and `dsDNA` were invisible to it while `abbreviations` found
          them. Every count the regex fed - the per-section abbreviation
          count, `abbrev_per_100w`, and the `abbreviation_undefined` finding -
          was low by however many the manuscript held, and nothing reported
          the omission.
    101 - `readability` matched a parenthesis holding NOTHING but the
          abbreviation, so `(CMG2, also ANTXR2)` defined neither and the check
          fired on the paper's own subject in its own abstract.

    Both were one divergence: two regexes doing one job with nothing
    comparing them. The fix is one contract, and this is the test that keeps
    it one.
    """
    section("one abbreviation contract - items 100 and 101")

    root = os.path.join(tmp, "one_contract")
    path = os.path.join(root, "intro.md")
    _put(root, "intro.md", ONE_CONTRACT_MS)

    # --- item 100: the regex the two commands share ----------------------
    check("the weaker token regex is gone",
          hasattr(prose, "_ABBREV_RE"), False,
          "it required an upper-case first character")
    check("...and so is the single-token expansion regex",
          hasattr(prose, "_EXPANSION_RE"), False,
          "it matched a parenthesis holding nothing but the abbreviation")

    tokens = {m.group(0) for m in prose._ABBREV_TOKEN_RE.finditer(ONE_CONTRACT_MS)}
    for tok in ("vWA", "mRNA"):
        check(f"`{tok}` is visible to the shared regex", tok in tokens, True,
              "a lower-case prefix in front of the capitals is a real and "
              "common shape in biochemistry")
    check("...and an upper-case-initial one still is",
          "XPS" in tokens, True)
    check("a capitalised word is still not an abbreviation",
          "Capillary" in tokens, False,
          "counting one fires on every author's name in the text")

    # The verify-by's own comparison: the two commands must not differ by a
    # token whose only difference is a lower-case prefix.
    res = prose.readability([path], report_abbreviations=True)
    abb = prose.abbreviations([path])
    abb_tokens = {r.get("abbrev") for r in abb.get("abbreviations", [])}
    abb_tokens |= {f.get("token") for f in abb.get("findings", [])
                   if f.get("token")}
    lower_only = {t for t in abb_tokens if t and t[:1].islower()}
    check("no lower-case-prefixed token is known to one command and not the "
          "other", sorted(lower_only - tokens), [],
          "this is the comparison item 100 asks for by name")

    # --- item 101: the whole parenthesis is read -------------------------
    fired = {f.get("token") for f in res["findings"]
             if f["rule"] == "abbreviation_undefined"}
    for tok in ("CMG2", "ANTXR2"):
        check(f"`{tok}` is defined by a parenthesis carrying both",
              tok in fired, False,
              "firing on a correctly defined abbreviation is what teaches a "
              "reader to discount the whole class")
    check("`vWA` is defined by the words in front of it",
          "vWA" in fired, False)
    check("`mRNA` is too", "mRNA" in fired, False)

    # And the check still FIRES, or none of the above proves anything.
    undefined = prose.readability(
        [os.path.join(root, "bare.md")], report_abbreviations=True)
    _put(root, "bare.md", "# Introduction\n\nThe vWA domain and the QCM "
                          "response were measured against the mRNA level.\n")
    undefined = prose.readability(
        [os.path.join(root, "bare.md")], report_abbreviations=True)
    bare = {f.get("token") for f in undefined["findings"]
            if f["rule"] == "abbreviation_undefined"}
    check("an abbreviation defined nowhere still fires",
          "vWA" in bare and "QCM" in bare, True, str(sorted(bare)))


def test_abbreviations(tmp: str) -> None:
    """Defined nowhere, defined only in the body, then ignored (user-asks-2 3).

    §3.4's suppressions are written here BEFORE the detector because two of
    the three real firings measured on the paper this came from are cases
    the check must stay quiet on - a one-in-three false-positive rate before
    a line is written.
    """
    section("abbreviations - defined, never defined, and defined then spelled "
            "out (user-asks-2 3)")

    root = os.path.join(tmp, "abb")
    _put(root, "project.yml", 'title: "a"\npaper_kind: review\n')
    st = "drafts/source_text_r1/"
    _put(root, st + "title_abstract.md", ABB_ABSTRACT)
    _put(root, st + "01_introduction.md", ABB_INTRO)
    _put(root, st + "02_structure.md", ABB_STRUCTURE)
    _put(root, st + "06_disease.md", ABB_DISEASE)
    _put(root, st + "08_conclusion.md", ABB_CONCLUSION)
    paths = prose.source_files(root)
    check("the five sections are read in reading order",
          [os.path.basename(p)[:-3] for p in paths],
          ["title_abstract", "01_introduction", "02_structure", "06_disease",
           "08_conclusion"])

    # The distance is passed rather than defaulted, because this corpus is a
    # few hundred words and the default is 500. Here the three-way split lands
    # at 38, 67 and 80.
    res = prose.abbreviations(paths, reexpansion_distance=50)
    by = {}
    for f in res["findings"]:
        by.setdefault(f["rule"], []).append(f)

    # --- 1. the abstract spells it out and defines nothing --------------
    check("the abstract writing a term out and never abbreviating it is "
          "reported, for both terms measured on the real paper",
          sorted(f["term"] for f in by.get("abstract_missing_definition", [])),
          ["metal-ion-dependent adhesion site", "von willebrand factor a"])
    check("...naming the abbreviation the body then uses bare",
          sorted(f["abbreviation"] for f
                 in by.get("abstract_missing_definition", [])),
          ["MIDAS", "vWA"])

    # --- 2. defined in the abstract and nowhere else --------------------
    check("an abbreviation the abstract defines and the body never does is "
          "reported - the body is read alone too",
          [f["abbreviation"] for f in by.get("abstract_only_definition", [])],
          ["ELISA"])

    # --- 3. undefined at first use, per region --------------------------
    check("a bare abbreviation the abstract never expands is reported",
          [f["abbreviation"] for f in by.get("undefined_at_first_use", [])],
          ["QQQ"])
    check("...and it is on by default here, unlike inside `readability`",
          prose.readability(paths)["abbreviations"]["reported"], False)

    # --- 4. never abbreviated at all ------------------------------------
    check("a term written out four times and never given a short form is "
          "reported",
          [f["term"] for f in by.get("never_abbreviated", [])],
          ["protective antigen"])
    check("...with its count, and no invented abbreviation",
          [(f["count"], f.get("abbreviation", "")) for f
           in by.get("never_abbreviated", [])], [(4, "")])

    # --- 5. defined, then spelled out again -----------------------------
    # The whole of §3.4. Five expansions in this corpus and only ONE of them
    # is a finding.
    fired = sorted(f["location"] for f in by.get("defined_then_spelled_out", []))
    check("only the same-section lapse fires; the definition, the abstract, "
          "the title, the coordinated phrase and the distant reminder are "
          "all suppressed",
          [x.split(":")[0] for x in fired], ["02_structure"])
    check("...and it never writes", res["written"], [],
          "`but only as dictated by readability` is the instruction not to "
          "build this as a rewrite")

    # The suppression the first real run forced. The house rule is "define it
    # in the abstract and define it again at its first occurrence in the main
    # text" - so a second DEFINITION is what the rule asks for, and reporting
    # it as a lapse would be the check firing on compliance with itself.
    check("an expansion that is itself a definition is never a lapse, "
          "wherever it is",
          sorted({s["location"] for s in res["suppressed"]
                  if s["abbreviation"] == "CMG2"}),
          ["01_introduction:3", "title_abstract:4"])
    check("...and CMG2 produces no finding at all",
          [f for f in res["findings"] if f.get("abbreviation") == "CMG2"], [])

    # Each suppression named, so a later change that removes one is visible.
    supp = {s["why"] for s in res["suppressed"]}
    check("every suppression is recorded rather than silently applied",
          sorted(supp),
          ["a coordinated or partial phrase, where the abbreviation is not a "
           "drop-in", "in the abstract, which is its own scope and is read "
           "alone", "the definition itself", "too far from the definition to "
           "read as a lapse rather than a reminder"])

    # --- the two thresholds, and both ship advisory ---------------------
    th = res["thresholds"]
    check("both thresholds say they are uncalibrated",
          [th["calibrated"], th["advisory"]], [False, True])
    check("...and every finding that rests on one is advisory",
          sorted({f["severity"] for f in res["findings"]
                  if f["rule"] in ("never_abbreviated",
                                   "defined_then_spelled_out")}),
          ["advisory"])
    check("...while the three that rest on no threshold are not",
          sorted({f["severity"] for f in res["findings"]
                  if f["rule"] not in ("never_abbreviated",
                                       "defined_then_spelled_out")}),
          ["info"])
    check("the never-abbreviated default is the 4 the measurement suggested",
          th["never_abbreviated_n"], 4)

    # Turn the distance off and the distant reminder comes back, which proves
    # the suppression was the threshold rather than an accident of the text.
    far = prose.abbreviations(paths, reexpansion_distance=10 ** 6)
    check("with no distance limit the conclusion's re-expansion fires too",
          sorted({f["location"].split(":")[0] for f in far["findings"]
                  if f["rule"] == "defined_then_spelled_out"}),
          ["02_structure", "08_conclusion"])
    check("...and the coordinated phrase still does not, at any distance",
          [f for f in far["findings"]
           if f["rule"] == "defined_then_spelled_out"
           and f["location"].startswith("06_")], [])

    # --- the journal supersedes the house -------------------------------
    avoid = prose.abbreviations(paths, in_abstract="avoid",
                                reexpansion_distance=50)
    check("`avoid` inverts the finding: an abbreviation INTRODUCED in the "
          "abstract is what gets reported",
          sorted(f["abbreviation"] for f in avoid["findings"]
                 if f["rule"] == "abstract_defines_abbreviation"),
          ["CMG2", "ELISA"])
    check("...and the abstract-missing-definition finding is off",
          [f for f in avoid["findings"]
           if f["rule"] == "abstract_missing_definition"], [])
    check("...and the report says which rule it applied and why",
          [avoid["rule"], avoid["source"]],
          ["avoid", "journal requirements.yml"])
    check("the house default is `define`, and says so",
          [res["rule"], res["source"]], ["define", "house default"])
    unknown = prose.abbreviations(paths, in_abstract="unknown",
                                  reexpansion_distance=50)
    check("`unknown` means the journal file is silent, so the house applies "
          "and the report says so",
          [unknown["rule"], unknown["source"]],
          ["define", "house default (journal file is silent)"])

    # --- what it must not do ---------------------------------------------
    check("a term used once is never reported as needing an abbreviation",
          [f for f in res["findings"]
           if f["rule"] == "never_abbreviated" and f["count"] < 2], [])
    formula = _put(root, st + "09_chem.md",
                   "# Chem\n\nCH3 and COOH and NH2 and H2O2 were present; "
                   "CH3 and COOH again, and CH3 once more.\n")
    res2 = prose.abbreviations([formula])
    check("an element symbol or a formula is never an abbreviation",
          [f["abbreviation"] for f in res2["findings"]
           if f["rule"] == "undefined_at_first_use"], [])
    check("...and the paper's own title is not a region this reads",
          [f for f in res["findings"] if f["location"].startswith("title:")],
          [])

    # --- it never gates ---------------------------------------------------
    cli = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "prose.py"),
         "abbreviations", root, "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("the CLI exits 0 with findings", cli.returncode, 0)
    payload = json.loads(cli.stdout)
    check("...and the payload names its thresholds where a consumer will "
          "read them", sorted(payload["thresholds"]),
          ["advisory", "calibrated", "never_abbreviated_n", "note",
           "reexpansion_distance_words"])


def test_entity_forms(tmp: str) -> None:
    """The abstract names the protein the way the body does, or somebody said
    so (user-asks-2 2).

    The abstract is the part of the paper most likely to be read alone,
    quoted alone and indexed alone. A qualifier the body carries and the
    abstract drops is the abstract making a broader claim than the paper
    supports, in the one place where nothing around it can correct the
    record.
    """
    section("entity forms - the abstract against the body (user-asks-2 2)")

    root = os.path.join(tmp, "ent")
    _put(root, "project.yml", 'title: "e"\npaper_kind: review\n')
    st = "drafts/source_text_r1/"
    _put(root, st + "title_abstract.md",
         "# Title\n\nA Study of CMG2 and Its Partners\n\n## Abstract\n\n"
         "CMG2 is a 489-residue type I membrane protein, hTEM8 binds the "
         "same site, PA63 forms the pore, and mTOR is unrelated.\n")
    _put(root, st + "01_introduction.md",
         "# Introduction\n\nHuman CMG2 was identified in a screen, and "
         "TEM8 was reported months earlier. PA63 forms the heptamer, and "
         "mTOR signalling is not involved here.\n")
    _put(root, st + "02_structure.md",
         "# Structure\n\nThe human CMG2 ectodomain runs 34 to 318, murine "
         "CMG2 differs, and PA63 inserts. TEM8 is shorter.\n")
    paths = prose.source_files(root)

    res = prose.entity_forms(paths)
    by = {}
    for f in res["findings"]:
        by.setdefault(f["rule"], []).append(f)

    check("the body qualifies it and the abstract names it bare - the case "
          "the user asked for",
          [f["token"] for f in by.get("abstract_qualifier_dropped", [])],
          ["CMG2"])
    check("...naming both forms and both places",
          [(f["abstract_form"], f["body_form"]) for f
           in by.get("abstract_qualifier_dropped", [])],
          [("CMG2", "human CMG2")])
    check("...and every qualifier the body uses, so a reader can see it is "
          "not one stray adjective",
          sorted(by["abstract_qualifier_dropped"][0]["body_qualifiers"]),
          ["human", "murine"])

    check("a form that simply disagrees is the other finding",
          [f["token"] for f in by.get("abstract_form_disagrees", [])],
          ["TEM8"])
    check("...and it does not double-report the dropped-qualifier case",
          [f for f in by.get("abstract_form_disagrees", [])
           if f["token"] == "CMG2"], [])

    check("an entity the body never qualifies either is a consistent paper, "
          "not a defect",
          [f for f in res["findings"] if f["token"] == "PA63"], [])

    # §2.1's own measurement, carried whether or not anything fires on it -
    # and NEVER used to make a finding on its own. Made to create one it
    # fired on every bare token in the abstract, which is 2.4's forbidden
    # case: an unqualified name everywhere is a consistent paper.
    _put(root, st + "04_animals.md",
         "# Animals\n\nIn mice the receptor mediates lethality, and patients "
         "carry the variant.\n")
    _put(root, st + "05_quiet.md",
         "# Quiet\n\nThe pore forms at low pH and nothing here names an "
         "organism at all.\n")
    sp = prose.entity_forms(prose.source_files(root))["species"]
    check("the species reading is a fact about the manuscript - how many "
          "body sections name an organism, and whether the abstract does",
          [sp["sections_with"], sp["sections_total"], sp["abstract"]],
          [3, 4, []])
    check("...and it creates no finding of its own",
          sorted({f["token"] for f
                  in prose.entity_forms(prose.source_files(root))["findings"]}),
          ["CMG2", "TEM8"])

    # §7.2's prediction, answered by construction: a single-letter prefix is
    # a prefix only when the rest of it is an entity this manuscript
    # actually names. `mTOR` is not murine TOR.
    check("`mTOR` is not read as murine TOR",
          [f for f in res["findings"] if "TOR" in f["token"]], [])
    tor = next((e for e in res["entities"] if e["token"] == "mTOR"), None)
    check("...it is its own entity, bare",
          bool(tor) and tor["forms"] == ["mTOR"], True, str(tor))

    check("the title is not a region this reads - a title drops qualifiers "
          "on purpose, and only the `## Abstract` region of that file is "
          "read at all",
          [f for f in res["findings"] if "Its Partners" in f["sentence"]], [])

    # §2.1's measurement, which is the first thing a quick implementation
    # gets wrong: unanchored, `rat` finds three words in a 163-word abstract
    # and none of them is an animal.
    unanchored = _put(root, st + "03_words.md",
                      "# Words\n\nRather than literatures, strategies for "
                      "CMG2 were compared.\n")
    res2 = prose.entity_forms([unanchored])
    check("`rat` inside `rather`, `literatures` and `strategies` is not a "
          "species qualifier",
          [e["qualifiers"] for e in res2["entities"]
           if e["token"] == "CMG2"], [[]])

    check("it never writes", res["written"], [])
    cli = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "prose.py"),
         "entity_forms", root, "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("the CLI exits 0 with findings", cli.returncode, 0)
    payload = json.loads(cli.stdout)
    check("...and every finding is advisory - nothing here claims the "
          "abstract is WRONG, only that it differs",
          sorted({f["severity"] for f in payload["findings"]}), ["info"])
    check("...and says so, because it cannot know whether 489 residues is "
          "species-specific - that is a fact about the protein, not the text",
          all("nothing here decides" in f["detail"]
              for f in payload["findings"]), True)


def test_review_release_and_repointing(tmp: str) -> None:
    section("Review mode releases the budget and tightens the ledger "
            "(review-paper 4)")

    root = os.path.join(tmp, "revprose")
    _put(root, "project.yml", 'title: "r"\npaper_kind: review\n')
    _put(root, "drafts/source_text_r1/01_coverage.md",
         "# Coverage\n\nReported coverages are 0.33, 0.41, 0.44, 0.52 and "
         "0.61 ML across five groups, and the spread of 0.28 ML is larger "
         "than any single reported uncertainty of 0.05 ML.\n")
    _put(root, "data/corpus/papers/a2001x.md",
         "# a2001x\nread_tier: abstract\nverified: 2026-09-10 via crossref\n"
         "\n## What it reports\n\nCoverages of 0.33, 0.41, 0.44, 0.52 and "
         "0.61 ML; spread 0.28 ML; uncertainty 0.05 ML.\n")

    check("the paper kind is read the same way manuscript.py reads it",
          prose.paper_kind(root), "review")
    _put(root, "project.yml", 'title: "r"\npaper_kind: reveiw\n')
    check("...and a typo reads as research, never as the released mode",
          prose.paper_kind(root), "research")
    _put(root, "project.yml", 'title: "r"\n')
    check("...and absent does too", prose.paper_kind(root), "research")
    _put(root, "project.yml", 'title: "r"\npaper_kind: review\n')

    paths = prose.source_files(root)
    check("a review's numbered stems are the section files",
          [os.path.basename(x) for x in paths], ["01_coverage.md"])

    strict = prose.density(paths, level="low")
    soft = prose.density(paths, level="low", released=True)
    check("that paragraph is over budget", bool(strict["findings"]), True)
    check("...as a warning on a research paper",
          sorted({f["severity"] for f in strict["findings"]}), ["warning"])
    check("...and advisory in review mode",
          sorted({f["severity"] for f in soft["findings"]}), ["advisory"])
    check("the one-third absolute is released too, and says it is",
          [f["severity"] for f in soft["findings"]
           if f["rule"] == "numeric_fraction"] or ["(not over)"],
          ["advisory"] if any(f["rule"] == "numeric_fraction"
                              for f in strict["findings"]) else ["(not over)"])
    check("the release is a DEFAULT, not a lock - `low` set by hand is "
          "still measured",
          len(soft["findings"]) == len(strict["findings"]), True)

    # --- and the release does not reach layer 0 ------------------------
    res = prose.numbers(root)
    check("the ledger is repointed at the corpus, not at analysis.md",
          res["ledger"], "data/corpus/papers/")
    check("a number the corpus records is clean",
          [f["value"] for f in res["findings"]], [])

    _put(root, "drafts/source_text_r1/01_coverage.md",
         "# Coverage\n\nReported coverage reached 0.97 ML in the best "
         "case.\n")
    res = prose.numbers(root)
    check("a number that traces to no record is still flagged",
          [f["value"] for f in res["findings"]], ["0.97"])
    check("...as an ERROR, in every section, because a review has no "
          "results section and every section is making claims about "
          "somebody else's numbers",
          [f["severity"] for f in res["findings"]], ["error"])
    check("...and it points at the stricter question",
          "review.py tier" in res["findings"][0]["detail"], True)

    bare = os.path.join(tmp, "nocorpus")
    _put(bare, "project.yml", 'title: "r"\npaper_kind: review\n')
    _put(bare, "drafts/source_text_r1/01_x.md", "# X\n\nSomething.\n")
    res = prose.numbers(bare)
    check("a review with no corpus at all is told so, not left silent",
          [f["rule"] for f in res["findings"]], ["no_corpus"])


def test_sentence_boundary_contract(tmp: str) -> None:
    section("A citation after the period is still a sentence end (flow 4)")

    # The three engines, named in one test on purpose: a fourth private copy
    # of this pattern must fail here rather than pass silently.
    import importlib.util as _il

    def _engine(name):
        sp = _il.spec_from_file_location(
            "%s_engine_boundary" % name,
            os.path.join(ROOT, "tools", "%s.py" % name))
        if sp is None or sp.loader is None:
            raise ImportError(name)
        mod = _il.module_from_spec(sp)
        sp.loader.exec_module(mod)
        return mod

    review = _engine("review")
    docx = _engine("docx_edits")

    flat = ("The domain extends to residue 318.2,6 Within this region the "
            "fold is stable. A third sentence follows.")
    keyed = ("The domain extends to residue 318 [@a][@b]. Within this region "
             "the fold is stable. A third sentence follows.")
    check("prose sees three sentences in the flattened .docx form",
          len(prose._sentences(flat)), 3,
          "every splitter here required whitespace immediately after the "
          "period, so none of them broke there")
    check("...and three in the engine's own citekey form",
          len(prose._sentences(keyed)), 3,
          "the two spellings agree")
    check("review.py agrees", len(review._sentences(flat)), 3)
    check("docx_edits.py agrees", len(docx._sentence_spans(flat)), 3)

    check("nothing is deleted by the split",
          prose._sentences(flat)[0].endswith("318.2,6"), True,
          "the citation run belongs to the sentence before it")

    # The one case the pattern declines, and it declines it on purpose.
    check("a decimal is not a citation",
          len(prose._sentences("We added 0.5 M NaCl to the tube.")), 1,
          "`0.5 M NaCl` and `318.2 Within` are the same six characters")
    check("...but a citation after a word is",
          len(prose._sentences("As shown.4 The next claim follows.")), 2)
    check("a superscript run is unambiguous",
          len(prose._sentences("...residue 318.\u00b2,\u2076 Within this region.")),
          2)

    # All three copies are byte for byte identical - the same guard the
    # LAYOUT CONTRACT block has, for the same reason.
    blocks = {}
    for name in ("prose", "review", "docx_edits"):
        src = _slurp(os.path.join(ROOT, "tools", "%s.py" % name),
                     encoding="utf-8")
        a = src.find("# --- SENTENCE BOUNDARY CONTRACT")
        b = src.find("# --- END SENTENCE BOUNDARY CONTRACT")
        check("%s.py carries the boundary contract block" % name,
              a >= 0 and b > a, True)
        blocks[name] = src[a:src.index(chr(10), b) + 1] if a >= 0 else ""
    check("all three copies are byte for byte identical",
          len(set(blocks.values())), 1, "edit one, edit all three")


def test_joint_findings_carry_a_remedy(tmp: str) -> None:
    section("A finding that names a joint says what the repair is (flow 2.3)")

    root = os.path.join(tmp, "remedy_proj")
    st = os.path.join(root, "drafts", "source_text")
    os.makedirs(st, exist_ok=True)
    path = os.path.join(st, "discussion.md")
    _spit(path,
          "# Discussion" + chr(10) * 2
          + "The receptor binds collagen through its inserted domain. "
            "Affinity was measured by surface plasmon resonance. The "
            "dissociation constant sets the occupancy." + chr(10) * 2
          + "Anthrax toxin exploits an entirely separate surface. Protective "
            "antigen docks there. The complex is then endocytosed."
          + chr(10) * 2
          + "Angiogenesis assays were run in parallel. Sprouting was scored "
            "blind. The counts were pooled." + chr(10) * 2
          + "Collagen returns as the ligand of record here. Its domain is "
            "the same one. Occupancy again sets the rate." + chr(10),
          encoding="utf-8")

    res = prose.readability([path])
    joints = [f for f in res["findings"]
              if f["rule"] in ("given_new_gap", "tail_rank_last",
                               "topic_return")]
    check("the fixture produces joint findings at all", bool(joints), True,
          "a test that asserts something is ABSENT must first prove "
          "something was PRESENT")
    check("every joint finding carries a non-empty remedy",
          [f["rule"] for f in joints if not f.get("remedy")], [])
    bridges = [f for f in joints if f["rule"] == "given_new_gap"]
    if bridges:
        check("...and the bridge remedy refuses the cheap repair",
              "not a connective" in bridges[0]["remedy"], True,
              "a connective word at the front is the repair the user named "
              "as insufficient before anyone proposed it")

    # 12.3's guarantee, asserted rather than trusted.
    check("no readability finding carries a density",
          [f["rule"] for f in res["findings"]
           if {"passive_share", "mean_sentence", "density"} & set(f)], [])


def test_magnitude_words(tmp: str) -> None:
    section("A spelled-out magnitude is in the multiset (flow 5.4)")

    check("the -fold family is seen",
          prose.magnitude_tokens("roughly a thousandfold tighter"),
          ["thousandfold"])
    check("...hyphenated or not",
          prose.magnitude_tokens("approximately eleven-fold higher affinity"),
          ["elevenfold"])
    check("a fraction used as a quantity is seen",
          prose.magnitude_tokens("a third is charged"), ["third"])
    check("...and so is a plural one",
          prose.magnitude_tokens("two fifths of peer-reviewed work"),
          ["fifth"])

    # The three false positives 5.4 is written against, and one more.
    for text in ("a single mechanism", "one receptor", "the second cysteine",
                 "a third mechanism", "the problem is twofold",
                 "the manifold scaffold did not unfold"):
        check("silent on %r" % text, prose.magnitude_tokens(text), [],
              "a comparator that fires on `a single mechanism` is the "
              "density-on-Methods failure with a new face")

    # And the comparator itself, through learn.py, which is where it acts.
    import importlib.util as _il
    sp = _il.spec_from_file_location("learn_engine_magnitude",
                                     os.path.join(ROOT, "tools", "learn.py"))
    if sp is None or sp.loader is None:
        raise ImportError("learn.py")
    learn = _il.module_from_spec(sp)
    sp.loader.exec_module(learn)

    root = os.path.join(tmp, "magnitude_proj")
    st = os.path.join(root, "drafts", "source_text")
    os.makedirs(os.path.join(st, ".pre_revision"), exist_ok=True)
    before = ("# Discussion" + chr(10) * 2
              + "The variant binds roughly a thousandfold more tightly than "
                "the wild type, and a third of the tail is charged." + chr(10))
    after = before.replace("thousandfold", "hundredfold")
    _spit(os.path.join(st, ".pre_revision", "discussion.md"), before,
          encoding="utf-8")
    _spit(os.path.join(st, "discussion.md"), after, encoding="utf-8")

    res = learn.preserve(root, "discussion")
    check("every other invariant passes the swap", res["verdict"], "accepted",
          "zero numeric tokens, zero citekeys, zero flags, three words - "
          "which is the hole")
    check("the magnitude change is reported",
          [a["invariant"] for a in res["advisory"]], ["magnitudes"])
    check("...naming both values",
          (res["advisory"][0]["removed"], res["advisory"][0]["added"]),
          (["thousandfold"], ["hundredfold"]))
    check("...and it is advisory, so it rejects nothing",
          (res["advisory"][0]["advisory"],
           res["advisory"][0]["calibrated"], res["failed"]),
          (True, False, []),
          "a preservation invariant is the one place in the prose pipeline "
          "that CAN act, so it may not act uncalibrated")

    same = learn.preserve(root, "discussion")
    check("a second read is stable", len(same["advisory"]), 1)


def test_crossrefs_says_when_it_did_not_run(tmp: str) -> None:
    section("The float-order check says when it could not run (item 114)")

    # A folder with files named as figures 1, 2 and 3 and no prose. This is
    # the measured case: `0 floats, 0 callouts` at exit 0, identical before
    # and after a full `adopt --apply`, on a folder the user wants the order
    # checked on.
    root = os.path.join(tmp, "no_prose")
    os.makedirs(os.path.join(root, "drafts"), exist_ok=True)
    res = prose.crossrefs(root)
    check("it says it did not run", bool(res["did_not_run"]), True,
          "`0 floats` is what a paper whose floats are in perfect order also "
          "reports")
    check("...naming the reason", "no section text" in res["did_not_run"],
          True)
    check("...and the stems it looked for",
          "introduction" in res["did_not_run"], True)
    check("it read no sections", res["sections_read"], [])

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        prose.print_crossrefs(res)
    printed = out.getvalue()
    check("the printed FIRST line is not a count",
          printed.splitlines()[0].startswith("DID NOT RUN"), True)

    # 2. The honest pass has to be distinguishable in the PAYLOAD, not only
    #    in the printing.
    root2 = os.path.join(tmp, "with_prose")
    st = os.path.join(root2, "drafts", "source_text")
    os.makedirs(st, exist_ok=True)
    os.makedirs(os.path.join(root2, "plan"), exist_ok=True)
    _spit(os.path.join(root2, "plan", "captions.md"),
          "## Figure 1 - fig01.png" + chr(10) * 2
          + "**The first claim.**" + chr(10) + "A caption." + chr(10) * 2
          + "## Figure 2 - fig02.png" + chr(10) * 2
          + "**The second claim.**" + chr(10) + "Another caption." + chr(10),
          encoding="utf-8")
    _spit(os.path.join(st, "results.md"),
          "# Results" + chr(10) * 2
          + "The first thing is shown (Figure 1)." + chr(10) * 2
          + "The second thing is shown (Figure 2)." + chr(10),
          encoding="utf-8")
    res2 = prose.crossrefs(root2)
    check("a project with prose and floats in order did run",
          res2["did_not_run"], "")
    check("...and read its section", res2["sections_read"], ["results"])
    check("...with no order finding",
          [f for f in res2["findings"] if f["rule"] == "float_out_of_order"],
          [])


def main() -> int:
    print("prose.py - measuring the countable rules against known answers")
    test_counting()
    test_density_rules()

    tmp = tempfile.mkdtemp(prefix="prose_")
    try:
        root = build_project(tmp)
        test_outline(root)
        test_citekeys(root)
        test_captions(root)
        test_flags(root)
        test_numbers(root)
        test_caption_parser_agrees(root)
        test_outline_minimality(tmp)
        test_outline_state(tmp)
        test_crossrefs(tmp)
        test_length(tmp)
        test_layout_contract(tmp)
        test_section_budgets(tmp)
        test_density_config_reader(tmp)
        test_voice(tmp)
        test_metaprose(tmp)
        test_abstract_claim(tmp)
        test_outline_waiver(tmp)
        test_readability(tmp)
        test_opening_sentence(tmp)
        test_ai_voice(tmp)
        test_spelling(tmp)
        test_italics(tmp)
        test_abbreviations(tmp)
        test_one_abbreviation_contract(tmp)
        test_entity_forms(tmp)
        test_review_release_and_repointing(tmp)
        test_sentence_boundary_contract(tmp)
        test_joint_findings_carry_a_remedy(tmp)
        test_magnitude_words(tmp)
        test_crossrefs_says_when_it_did_not_run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASS + FAIL} checks: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
