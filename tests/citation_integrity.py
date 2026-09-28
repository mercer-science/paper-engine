#!/usr/bin/env python3
"""
tests/citation_integrity.py - the ten statements in
specs/citation-integrity-2026-09-20.md 10, enforced.

Every check here failed before that spec was built, and each one is a
statement about the engine rather than about a fixture:

  - a byline is compared as an ordered LIST, so a fabricated author and a
    dropped first author are named instead of carried by one correct surname
  - a year that is one out is a NOTE instead of silence
  - two entries that RENDER as the same citation are reported, from the entry
    fields and never from the key
  - a pass with no retrieved text for any pair says DID NOT RUN, in a
    different sentence from a pass that found nothing wrong
  - attribution-check runs on every paper kind, beside stats-check, over the
    sections that cite other people's work

Fully offline. The cascade is driven through a stub Source registered into
`scholar.SOURCES`, so `verify()` runs end to end - the same reason
tests/structure.py runs a stub HTTP server rather than calling the functions
underneath it. The stub's payloads are CONSTRUCTED to the documented record
shape, not captured, and `test_record_shape_is_the_engine_s` holds that shape
against `scholar.RECORD_DEFAULTS` so this suite cannot drift kinder than the
engine it stands in for.

  python tests/citation_integrity.py
  python tests/citation_integrity.py -v
"""

from __future__ import annotations

import importlib.util
import io
import os
import shutil
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
TOOLS = os.path.join(ROOT, "tools")


def _load(name: str, filename: str):
    """Load an engine by path under a distinct module name.

    `tests/x.py` and `tools/x.py` share a name, so every suite here does
    this. Do not "simplify" it back.
    """
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(TOOLS, filename))
    if spec is None or spec.loader is None:
        raise ImportError("cannot load tools/%s" % filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, TOOLS)
scholar = _load("scholar_engine", "scholar.py")
prose = _load("prose_engine", "prose.py")
manuscript = _load("manuscript_engine", "manuscript.py")
review = _load("review_engine", "review.py")

VERBOSE = "-v" in sys.argv
PASS = FAIL = 0


def check(name: str, got, want, note: str = "") -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
        if VERBOSE:
            print("  ok    %s" % name)
    else:
        FAIL += 1
        print("  FAIL  %s" % name)
        print("        got:  %r" % (got,))
        print("        want: %r" % (want,))
        if note:
            print("        %s" % note)


def section(title: str) -> None:
    print("\n%s\n%s" % (title, "-" * len(title)))


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


# ---------------------------------------------------------------------------
# The stub source
# ---------------------------------------------------------------------------

class StubSource(scholar.Source):
    """One record, answered on every route. Constructed, not captured."""

    name = "stub"

    def __init__(self, rec: dict):
        self.rec = rec

    def by_doi(self, _doi):
        return dict(self.rec) if self.rec.get("doi") else None

    def by_id(self, _ident):
        return dict(self.rec)

    def search(self, _term, limit=10, year_from="", year_to="", offset=0):
        return [dict(self.rec)]

    def by_title(self, _title, limit=5):
        return [dict(self.rec)]

    def available(self):
        return (True, "stub")


def stub_record(**kw) -> dict:
    return scholar.blank_record(source="stub", source_id="1", **kw)


def verify_against(rec: dict, **claim) -> dict:
    """`scholar.verify()` end to end, against one constructed record."""
    scholar.SOURCES["stub"] = StubSource(rec)
    try:
        return scholar.verify(sources=["stub"], **claim)
    finally:
        scholar.SOURCES.pop("stub", None)


PNAS = dict(
    doi="10.1073/pnas.0910782107",
    title="Capillary morphogenesis protein 2 is the anthrax toxin receptor",
    year="2010",
    authors=["Liu S", "Crown D", "Moayeri M"],
)


# ---------------------------------------------------------------------------
# 1-3. the byline and the year (spec 3, 4; item 107)
# ---------------------------------------------------------------------------

def test_byline_is_a_list() -> None:
    section("A byline is an ordered list, not a set intersection (spec 3)")

    rec = stub_record(**PNAS)

    # 1. a cited surname that is not on the record
    res = verify_against(
        rec, doi=PNAS["doi"],
        authors=["Liu S", "Crown D", "Miller-Randolph S", "Moayeri M"])
    flags = " | ".join(res["flags"])
    check("status is still verified - nothing here refuses",
          res["status"], "verified")
    check("the fabricated author is FLAGGED by name",
          "Randolph" in flags, True,
          "one correct surname used to carry any byline - item 107")
    check("...and the flag names what the record does list",
          "it lists Liu, Crown, Moayeri" in flags, True)
    check("...and it is counted in the payload",
          [d["kind"] for d in res["integrity"]["byline_differences"]],
          ["not_on_record"])

    # 2. a correct but abbreviated byline is a NOTE and no flag
    res = verify_against(rec, doi=PNAS["doi"], authors=["Liu S"])
    check("an abbreviated byline raises no flag", res["flags"], [])
    check("...it is a note, with the arithmetic",
          any("the citation gives 1 of 3 authors" in n for n in res["notes"]),
          True, "abbreviating is ordinary practice in several styles and "
                "must never read as an accusation")
    check("...and it is recorded as `abbreviated`",
          [d["kind"] for d in res["integrity"]["byline_differences"]],
          ["abbreviated"])

    # 3. the wrong first author
    res = verify_against(rec, doi=PNAS["doi"], authors=["Crown D", "Liu S"])
    check("a dropped first author is a FLAG",
          any(f.startswith("First author differs") for f in res["flags"]),
          True, "it is what breaks a reader's ability to find the paper")
    check("...naming both",
          any('cited "Crown"' in f and 'first author is "Liu"' in f
              for f in res["flags"]), True)

    # order beyond the first position is a note, not a flag
    res = verify_against(rec, doi=PNAS["doi"],
                         authors=["Liu S", "Moayeri M", "Crown D"])
    check("order differing beyond the first author is a note only",
          res["flags"], [])
    check("...and it names both orders",
          any("Author order differs" in n for n in res["notes"]), True)

    # a clean byline says nothing at all
    res = verify_against(rec, doi=PNAS["doi"], authors=PNAS["authors"])
    check("a correct byline produces no flag and no byline note",
          (res["flags"], res["integrity"]["byline_differences"]), ([], []))


def test_particles_are_folded() -> None:
    section("`van der Goot` and `Goot` are one person, not a fabrication")

    check("a particle-carrying surname, comma form",
          scholar._surname_of("Goot, F. G. van der"), "goot")
    check("...and the PubMed form", scholar._surname_of("van der Goot FG"),
          "goot")
    check("`Smith, John` is Smith", scholar._surname_of("Smith, John"),
          "smith")
    check("`John Smith` is also Smith", scholar._surname_of("John Smith"),
          "smith")
    check("an all-particle surname keeps its token - somebody's name really "
          "is De", scholar._surname_of("De"), "de")

    rec = stub_record(doi="10.1/x", title="T", year="2011",
                      authors=["van der Goot FG"])
    res = verify_against(rec, doi="10.1/x", authors=["Goot, F. G. van der"])
    check("so the byline check stays quiet",
          (res["flags"], res["notes"]), ([], []))


def test_one_year_out_is_a_note() -> None:
    section("An off-by-one year is reported, not swallowed (spec 4)")

    rec = stub_record(doi="10.1038/emboj.2011.442", title="T", year="2012",
                      authors=["Duval J"])

    res = verify_against(rec, doi=rec["doi"], year="2011",
                         authors=["Duval J"])
    check("one year out raises no flag - the tolerance stays", res["flags"],
          [])
    check("...and is a note carrying BOTH years",
          any("the entry says 2011" in n and "says 2012" in n
              for n in res["notes"]), True)
    check("...counted in the payload",
          res["integrity"]["year_notes"],
          [{"cited": "2011", "record": "2012", "source": "stub"}])

    res = verify_against(rec, doi=rec["doi"], year="2010",
                         authors=["Duval J"])
    check("two years out is still a flag",
          any(f.startswith("Year mismatch") for f in res["flags"]), True)
    check("...and is not counted as a note", res["integrity"]["year_notes"],
          [])

    res = verify_against(rec, doi=rec["doi"], year="2012",
                         authors=["Duval J"])
    check("the right year says nothing", (res["flags"], res["notes"]),
          ([], []))


# ---------------------------------------------------------------------------
# 4. the summary (spec 4)
# ---------------------------------------------------------------------------

def test_check_refs_summary_counts_the_notes() -> None:
    section("A note-carrying entry is not `clean` (spec 4)")

    tmp = tempfile.mkdtemp(prefix="citint_")
    try:
        bib = os.path.join(tmp, "references.bib")
        _write(bib, """@article{a,
  author = {Liu, S. and Crown, D. and Miller-Randolph, S. and Moayeri, M.},
  title = {Capillary morphogenesis protein 2},
  year = {2010},
  doi = {10.1073/pnas.0910782107},
}

@article{b,
  author = {Duval, Julie},
  title = {Hyaline fibromatosis syndrome},
  year = {2011},
  doi = {10.1038/emboj.2011.442},
}
""")
        # One entry with a clean byline and a one-year-out date; one with a
        # fabricated author. Both verify.
        recs = {
            "10.1073/pnas.0910782107": stub_record(**PNAS),
            "10.1038/emboj.2011.442": stub_record(
                doi="10.1038/emboj.2011.442", year="2012",
                title="Hyaline fibromatosis syndrome",
                authors=["Duval J"]),
        }

        class MultiStub(scholar.Source):
            name = "stub"

            def by_doi(self, doi):
                r = recs.get(scholar.clean_doi(doi))
                return dict(r) if r else None

            def by_id(self, _ident):
                return None

            def search(self, term, limit=10, year_from="", year_to="",
                       offset=0):
                return []

        # `--source stub` is refused by the vocabulary check, which is
        # correct: an unknown index name is bad input. The cascade itself is
        # what this test replaces.
        scholar.SOURCES["stub"] = MultiStub()
        argv, cascade = sys.argv, scholar.CASCADE
        setattr(scholar, "CASCADE", ["stub"])
        sys.argv = ["scholar.py", "check-refs", bib, "--json"]
        out = io.StringIO()
        keep, sys.stdout = sys.stdout, out
        try:
            scholar.main()
        finally:
            sys.stdout = keep
            sys.argv = argv
            setattr(scholar, "CASCADE", cascade)
            scholar.SOURCES.pop("stub", None)
        import json as _json
        payload = _json.loads(out.getvalue())
        summary = payload["summary"]

        check("the byline difference is counted",
              summary["byline_differences"], 1)
        check("the year note is counted", summary["year_notes"], 1)
        check("neither entry is counted clean", summary["clean"], 0,
              "a forty-entry list is read through its summary")
        check("both are counted with the flagged ones",
              summary["verified_with_flags"], 2,
              "the key's name stops being exact and its meaning becomes right")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_check_refs(bib: str, recs: dict, extra: list) -> dict:
    """`check-refs` over this .bib with the cascade replaced by a stub."""
    class MultiStub(scholar.Source):
        name = "stub"

        def by_doi(self, doi):
            r = recs.get(scholar.clean_doi(doi))
            return dict(r) if r else None

        def by_id(self, _ident):
            return None

        def search(self, term, limit=10, year_from="", year_to="", offset=0):
            return []

    scholar.SOURCES["stub"] = MultiStub()
    argv, cascade = sys.argv, scholar.CASCADE
    setattr(scholar, "CASCADE", ["stub"])
    sys.argv = ["scholar.py", "check-refs", bib, "--json"] + extra
    out = io.StringIO()
    keep, sys.stdout = sys.stdout, out
    code = 0
    try:
        code = scholar.main()
    finally:
        sys.stdout = keep
        sys.argv = argv
        setattr(scholar, "CASCADE", cascade)
        scholar.SOURCES.pop("stub", None)
    import json as _json
    payload = _json.loads(out.getvalue())
    payload["exit"] = code
    return payload


POLICY_BIB = """@article{real2020,
  author = {Smith, J. and Jones, A.},
  title = {A peer reviewed paper},
  journal = {Journal of Biological Chemistry},
  year = {2020},
  doi = {10.1074/jbc.M120.000000},
}

@article{pre2025,
  author = {Brown, K.},
  title = {A preprint about the same thing},
  journal = {bioRxiv},
  year = {2025},
  doi = {10.1101/2025.01.01.000000},
}

@misc{bare2019,
  author = {Nobody, A.},
  title = {Something the entry does not describe},
  year = {2019},
}
"""


def test_a_project_may_declare_its_list_peer_reviewed_only() -> None:
    """Item 105, verified exactly as its `verify by` words it.

    `check-refs` classifies every entry and reports what kind of source it
    is, and by design it never removes one and never refuses: a paper may
    legitimately cite a preprint. That default is right, and it is wrong for
    a project that has DECIDED its list is peer-reviewed papers only -
    because there was no per-project setting that turned the decision into a
    check. On such a project the rule lived in whoever remembered it, and a
    review carried a UniProt accession as a numbered reference through a full
    round before a reader challenged it.
    """
    section("a project may declare its reference list peer-reviewed only "
            "(item 105)")

    tmp = tempfile.mkdtemp(prefix="citint_policy_")
    try:
        bib = os.path.join(tmp, "references.bib")
        _write(bib, POLICY_BIB)
        recs = {"10.1074/jbc.M120.000000": stub_record(
            doi="10.1074/jbc.M120.000000", year="2020",
            title="A peer reviewed paper", authors=["Smith J", "Jones A"])}

        # --- policy set --------------------------------------------------
        on = _run_check_refs(bib, recs, ["--peer-reviewed-only"])
        s_on = on["summary"]
        check("the policy is reported", s_on["policy"], "peer_reviewed_only")
        check("the preprint is named as blocking",
              [b["key"] for b in s_on["blocking"]], ["pre2025"],
              "naming the citekey is what lets somebody act on it")
        check("...and the run exits 1", on["exit"], 1,
              "the policy IS the request to fail on this")

        # The three-valued flag has to survive the policy, and this is the
        # half that would be easy to get wrong: a bare @misc not describing
        # itself is not evidence that it was not peer reviewed.
        check("an entry whose record does not say is asked about",
              [b["key"] for b in s_on["peer_review_unknown"]], ["bare2019"])
        check("...and is NOT counted as a failure",
              [b["key"] for b in s_on["blocking"]], ["pre2025"],
              "unknown is a question, never a verdict")

        # --- policy unset -------------------------------------------------
        off = _run_check_refs(bib, recs, [])
        s_off = off["summary"]
        check("unset, the policy is `any`", s_off["policy"], "any")
        check("...and nothing blocks", s_off["blocking"], [],
              "a methods section citing a PDB entry is doing its job")
        check("...and the run exits 0", off["exit"], 0)
        check("...while the preprint is still REPORTED as what it is",
              s_off["non_peer_reviewed"], 1,
              "the default classifies and reports; it never refuses")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 5-6. the rendered collision (spec 5; item 108)
# ---------------------------------------------------------------------------

def _collision_project(tmp: str, year_b: str, key_b: str) -> str:
    proj = os.path.join(tmp, "proj")
    _write(os.path.join(proj, "drafts", "references.bib"),
           "@article{deuquet2011a,\n"
           "  author = {Duval, Julie and Laurent, Erik},\n"
           "  title = {Hyaline fibromatosis syndrome},\n"
           "  year = {2011},\n}\n\n"
           "@article{%s,\n"
           "  author = {Duval, J. and Others, A.},\n"
           "  title = {Capillary morphogenesis protein 2},\n"
           "  year = {%s},\n}\n" % (key_b, year_b))
    _write(os.path.join(proj, "drafts", "source_text", "introduction.md"),
           "# Introduction\n\nThe receptor was described [@deuquet2011a] and "
           "again [@%s].\n\nA later paragraph [@deuquet2011a].\n" % key_b)
    return proj


def test_author_year_collision() -> None:
    section("Two entries that RENDER as the same citation (spec 5)")

    tmp = tempfile.mkdtemp(prefix="citint_")
    try:
        proj = _collision_project(tmp, "2011", "deuquet2011b")
        res = prose.citekeys(proj)
        hits = [f for f in res["findings"]
                if f["rule"] == "author_year_collision"]
        check("the collision is reported", len(hits), 1)
        check("...as a warning, not an error", hits[0]["severity"], "warning")
        check("...naming both keys",
              "deuquet2011a" in hits[0]["key"] and "deuquet2011b"
              in hits[0]["key"], True)
        check("...and the citing paragraphs",
              hits[0]["location"], "introduction ¶1, introduction ¶2",
              "it names the paragraphs so the cost is visible rather than "
              "asserted")
        check("...and how it renders",
              "Duval et al., 2011" in hits[0]["detail"], True)

        # Changing one year removes it - which is why nobody went looking:
        # the wrong year and the collision were hiding each other.
        proj2 = _collision_project(os.path.join(tmp, "two"), "2012",
                                   "deuquet2011b")
        res2 = prose.citekeys(proj2)
        check("correcting the year dissolves the finding",
              [f for f in res2["findings"]
               if f["rule"] == "author_year_collision"], [])

        # 6. computed from FIELDS, not from keys
        proj3 = _collision_project(os.path.join(tmp, "three"), "2011",
                                   "xqz77")
        res3 = prose.citekeys(proj3)
        hits3 = [f for f in res3["findings"]
                 if f["rule"] == "author_year_collision"]
        check("wholly different keys still collide", len(hits3), 1,
              "the finding is computed from the entry fields; the key is "
              "something no reader ever sees")
        check("...and both are named", "xqz77" in hits3[0]["key"], True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 7, 8. DID NOT RUN, and the header (spec 6.1)
# ---------------------------------------------------------------------------

def _paper_project(tmp: str, kind: str = "research", records: int = 0,
                   citations: int = 3) -> str:
    proj = os.path.join(tmp, "p_%s_%d" % (kind, records))
    keys = ["ref%02d" % i for i in range(1, citations + 1)]
    _write(os.path.join(proj, "project.yml"), "paper_kind: %s\n" % kind)
    _write(os.path.join(proj, "drafts", "source_text", "introduction.md"),
           "# Introduction\n\n" + " ".join("Sentence [@%s]." % k
                                           for k in keys) + "\n")
    _write(os.path.join(proj, "drafts", "source_text", "methods.md"),
           "# Methods\n\nWe did the thing.\n")
    _write(os.path.join(proj, "drafts", "source_text", "results.md"),
           "# Results\n\nIt worked.\n")
    _write(os.path.join(proj, "drafts", "source_text", "discussion.md"),
           "# Discussion\n\nIt means something [@%s].\n" % keys[0])
    for key in keys[:records]:
        review.record_cmd(proj, key, tier="abstract",
                          title="A paper", year="2011",
                          claims="The source says the thing.")
    return proj


def test_did_not_run() -> None:
    section("A pass with no retrieved text DID NOT RUN (spec 6.1, item 80)")

    tmp = tempfile.mkdtemp(prefix="citint_")
    try:
        proj = _paper_project(tmp, records=0)
        res = manuscript.attribution_inputs(proj)
        check("no record means nothing is checkable", res["can_run"], False)
        check("...and the line names how many citations went unchecked",
              "no retrieved source text for any of 4 citations"
              in res["did_not_run"], True)
        check("...and how many records there are",
              "holds 0 records" in res["did_not_run"], True)
        check("...and what to run about it",
              "corpus-fill" in res["did_not_run"], True)

        # 8. a partial run says so in its FIRST line
        proj3 = _paper_project(tmp, records=3, citations=3)
        res3 = manuscript.attribution_inputs(proj3)
        check("a partial run can run", res3["can_run"], True)
        check("...and says n of N", res3["header"],
              "4 checked, 0 not retrieved, of 4 pairs")
        check("...with nothing to announce", res3["did_not_run"], "")

        proj1 = _paper_project(tmp, records=1, citations=3)
        res1 = manuscript.attribution_inputs(proj1)
        check("one record out of three is a partial header", res1["header"],
              "2 checked, 2 not retrieved, of 4 pairs",
              "ref01 is cited twice, so it is two pairs")

        # A record that retrieved NOTHING is not a checkable pair.
        proj_empty = _paper_project(os.path.join(tmp, "e"), records=0,
                                    citations=1)
        review.record_cmd(proj_empty, "ref01", tier="title",
                          title="A paper nobody read")
        res_e = manuscript.attribution_inputs(proj_empty)
        check("a title-tier record with no text is not checkable",
              res_e["can_run"], False,
              "`not retrieved` must never be written as a pass")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_brief_carries_the_verdict() -> None:
    section("The brief says it before the agent is spawned (spec 6.1)")

    tmp = tempfile.mkdtemp(prefix="citint_")
    try:
        proj = _paper_project(tmp, records=0)
        brief = manuscript.agent_brief(proj, "attribution-check")
        check("the brief carries the line", bool(brief["did_not_run"]), True)
        check("...and warns the caller not to record it as done",
              any("Do not record this module as done" in w
                  for w in brief["warnings"]), True)
        check("...and the prompt says to write no table",
              "print no table" in brief["prompt"], True)
        check("...and the prompt opens with the arithmetic",
              "HOW MUCH OF THIS CAN BE CHECKED AT ALL" in brief["prompt"],
              True)

        other = manuscript.agent_brief(proj, "stats-check")
        check("no other module carries an inputs block", other["inputs"], {})
        check("...or a did_not_run line", other["did_not_run"], "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 7a, 7b. every paper kind, scoped by section (spec 6.2)
# ---------------------------------------------------------------------------

def test_every_paper_kind() -> None:
    section("attribution-check is not a review's dial any more (spec 6.2)")

    check("it is no longer gated by paper kind",
          "attribution-check" in manuscript.KIND_ONLY_MODULES, False)
    check("stats-check still is - a review has no statistics of its own",
          manuscript.KIND_ONLY_MODULES["stats-check"][0], "research")

    for preset in ("coauthor", "submission", "revision"):
        mods = manuscript.PRESETS[preset]
        check("the %s preset schedules both passes" % preset,
              ("stats-check" in mods, "attribution-check" in mods),
              (True, True),
              "neither substitutes for the other")

    tmp = tempfile.mkdtemp(prefix="citint_")
    try:
        proj = _paper_project(tmp, records=3, citations=3)
        plan = manuscript.plan(proj, "JACS", preset="submission")
        check("a journal project at `submission` schedules it",
              "attribution-check" in plan["modules"], True)
        check("...beside stats-check", "stats-check" in plan["modules"], True)
        check("...and nothing dropped it for the paper kind",
              [d["module"] for d in plan["kind_dropped"]], [])
        check("...at `full`, because that is what the submission preset does",
              plan["attribution_level"], "full")

        # 7b. the sections it reads
        check("it reads the introduction and the discussion",
              manuscript.attribution_sections(proj),
              ["introduction", "discussion"])
        brief = manuscript.agent_brief(proj, "attribution-check")
        names = [g["name"] for g in brief["given"]]
        check("...and the brief enumerates only those",
              [n for n in names if n in ("introduction", "methods",
                                         "results", "discussion")],
              ["introduction", "discussion"],
              "Results and Methods cite this project's own data and are "
              "stats-check's ground")

        # A review declares its own structure and all of it is other
        # people's work.
        rev = _paper_project(os.path.join(tmp, "rev"), kind="review",
                             records=3, citations=3)
        check("a review's whole body is in scope",
              manuscript.attribution_sections(rev),
              manuscript.body_sections(rev))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_config_echoes_the_dial() -> None:
    section("A dial a user cannot see is a dial nobody turns (spec 6.2)")

    tmp = tempfile.mkdtemp(prefix="citint_")
    try:
        for kind in ("research", "review"):
            proj = _paper_project(tmp, kind=kind)
            manuscript.init(proj, "JACS")
            res = manuscript.configure(proj, "JACS")
            out = io.StringIO()
            keep, sys.stdout = sys.stdout, out
            try:
                manuscript.print_config(res)
            finally:
                sys.stdout = keep
            check("config echoes attribution_check on a %s paper" % kind,
                  "attribution_check" in out.getvalue(), True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 9. corpus-fill (spec 6.3)
# ---------------------------------------------------------------------------

def test_corpus_fill_refuses_a_review_corpus() -> None:
    section("A convenience fetch may not raise a review's tier (spec 6.3)")

    tmp = tempfile.mkdtemp(prefix="citint_")
    try:
        proj = _paper_project(tmp)
        _write(review.protocol_path(proj), "# Scope contract\n")
        res = scholar.corpus_fill(proj)
        check("it refuses", bool(res["errors"]), True)
        check("...naming the protocol",
              "protocol.md" in res["errors"][0], True)
        check("...and says why review.py owns the tier",
              "screening procedure" in res["errors"][0], True)
        check("...and wrote nothing", res["written"], [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_corpus_fill_writes_the_tier_it_reached() -> None:
    section("A record never claims a tier retrieval did not reach (spec 6.3)")

    tmp = tempfile.mkdtemp(prefix="citint_")
    try:
        proj = os.path.join(tmp, "unscaffolded")
        _write(os.path.join(proj, "drafts", "references.bib"),
               "@article{withabstract,\n  author = {Liu, S.},\n"
               "  title = {A paper with an abstract},\n  year = {2010},\n"
               "  doi = {10.1/abs},\n}\n\n"
               "@article{titleonly,\n  author = {Crown, D.},\n"
               "  title = {A paper with nothing retrievable},\n"
               "  year = {2011},\n  doi = {10.1/bare},\n}\n\n"
               "@misc{uniprot_p58335,\n"
               "  author = {{The UniProt Consortium}},\n"
               "  title = {ANTXR2},\n  year = {2024},\n"
               "  howpublished = {https://www.uniprot.org/uniprotkb/P58335},\n"
               "}\n")

        recs = {
            "10.1/abs": stub_record(
                doi="10.1/abs", title="A paper with an abstract",
                year="2010", authors=["Liu S"],
                abstract="The source states the thing plainly."),
            "10.1/bare": stub_record(
                doi="10.1/bare", title="A paper with nothing retrievable",
                year="2011", authors=["Crown D"]),
        }

        class MultiStub(scholar.Source):
            name = "stub"

            def by_doi(self, doi):
                r = recs.get(scholar.clean_doi(doi))
                return dict(r) if r else None

            def by_id(self, _ident):
                return None

            def search(self, term, limit=10, year_from="", year_to="",
                       offset=0):
                return []

        scholar.SOURCES["stub"] = MultiStub()
        try:
            res = scholar.corpus_fill(proj, sources=["stub"],
                                      full_text=False)
        finally:
            scholar.SOURCES.pop("stub", None)

        by_key = {w["key"]: w for w in res["written"]}
        check("an abstract makes an abstract-tier record",
              by_key["withabstract"]["read_tier"], "abstract")
        check("nothing retrievable stays at title",
              by_key["titleonly"]["read_tier"], "title",
              "a record asserting a tier it did not reach is worse than no "
              "record")
        check("a database entry is recorded as what it is, at title tier",
              by_key["uniprot_p58335"]["read_tier"], "title")
        check("...and never went to an index",
              "not a paper" in by_key["uniprot_p58335"]["why"], True)

        # The format is review.py's own, read back by review.py.
        rec = review.read_record(proj, "withabstract")
        check("the record parses as a review record", bool(rec), True)
        check("...carrying the tier review.py writes",
              rec.get("read_tier"), "abstract")
        check("...and the engine's own Limits section",
              "Only the abstract was read" in rec["raw"], True,
              "that section is what stops a checking agent treating an "
              "abstract as a paper")
        check("...and the abstract is retrievable text",
              "states the thing plainly" in review.retrieved_text(rec), True)

        # ...which means attribution-check now has an input where it had none.
        inputs = manuscript.attribution_inputs(proj)
        check("a second run skips what it already wrote",
              [sk["why"] for sk in
               scholar.corpus_fill(proj, sources=["stub"],
                                   full_text=False)["skipped"]],
              ["a record already exists"] * 3)
        check("and the project it wrote into is one any engine can read",
              isinstance(inputs, dict), True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 10. the verdict table (spec 6.4)
# ---------------------------------------------------------------------------

def test_verdict_table_carries_the_two_rules() -> None:
    section("`contradicts`, and entity substitution (spec 6.4)")

    task = manuscript.AGENT_MODULES["attribution-check"]["task"]
    check("`contradicts` is a verdict", "contradicts" in task, True)
    check("...separated from `does not` for a stated reason",
          "opposite problems for a writer" in task, True)
    check("entity substitution is named as never support",
          "ENTITY SUBSTITUTION IS NEVER SUPPORT" in task, True)
    check("...and the quoted line has to be the one that assigns it",
          "must be the one that assigns it" in task, True)

    why = manuscript.AGENT_MODULES["attribution-check"]["why"]
    check("the module says it runs on every paper kind",
          "EVERY paper kind" in why, True)
    check("...beside stats-check", "BESIDE" in why, True)

    for path, label in (
            (os.path.join(ROOT, "skills", "writing-engine", "reference",
                          "submission.md"), "submission.md"),):
        text = io.open(path, encoding="utf-8").read()
        check("%s carries the `contradicts` row" % label,
              "`contradicts`" in text, True)
        check("%s says the flag is OFFERED, never written" % label,
              "OFFERED as a flag" in text, True)
        check("%s says what each of the three passes proves" % label,
              "what it cannot see" in text, True)
        check("%s tells the caller to record a skipped module" % label,
              "--state skipped" in text, True)


def test_flag_type_exists() -> None:
    section("A `contradicts` verdict has somewhere to become blocking (6.5)")

    check("`citation-claim` is a known flag type",
          "citation-claim" in prose.FLAG_TYPES, True)
    tmp = tempfile.mkdtemp(prefix="citint_")
    try:
        proj = os.path.join(tmp, "p")
        _write(os.path.join(proj, "drafts", "source_text", "introduction.md"),
               "# Introduction\n\nCMG2 binds collagen IV [@zhao2015]. "
               "**[FLAG: citation-claim - the source shows it for TEM8]**\n")
        res = prose.flags(prose.source_files(proj))
        check("it parses as a known type",
              [f["known_type"] for f in res["flags"]], [True])
        check("...and needs a human, like every other whose-call-is-it flag",
              res["needs_a_human"], 1,
              "whether the sentence or the citation was wrong is not a "
              "question any pass here may settle")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# The contracts this suite duplicates
# ---------------------------------------------------------------------------

def test_record_shape_is_the_engine_s() -> None:
    section("The stub is not kinder than the engine it stands in for")

    rec = stub_record()
    check("every documented record key is present",
          sorted(rec), sorted(scholar.RECORD_DEFAULTS))
    check("manuscript.py reads the same record sections review.py writes",
          list(manuscript.CORPUS_TEXT_SECTIONS),
          ["What it claims", "What it reports", "Quotes"],
          "review.retrieved_text() reads these three; a copy is only safe "
          "while something compares it")
    check("...and they are review.py's own section names",
          [s for s in review.RECORD_SECTIONS
           if s in manuscript.CORPUS_TEXT_SECTIONS],
          list(manuscript.CORPUS_TEXT_SECTIONS))


def main() -> int:
    print("citation integrity - specs/citation-integrity-2026-09-20.md 10")
    test_byline_is_a_list()
    test_particles_are_folded()
    test_one_year_out_is_a_note()
    test_check_refs_summary_counts_the_notes()
    test_a_project_may_declare_its_list_peer_reviewed_only()
    test_author_year_collision()
    test_did_not_run()
    test_brief_carries_the_verdict()
    test_every_paper_kind()
    test_config_echoes_the_dial()
    test_corpus_fill_refuses_a_review_corpus()
    test_corpus_fill_writes_the_tier_it_reached()
    test_verdict_table_carries_the_two_rules()
    test_flag_type_exists()
    test_record_shape_is_the_engine_s()
    print("\n%d checks: %d passed, %d failed" % (PASS + FAIL, PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
