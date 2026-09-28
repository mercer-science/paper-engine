#!/usr/bin/env python3
"""
tests/review.py - measure tools/review.py against the corpus rules.

Offline, against fixtures. Nothing here touches a network index: the sweep
itself is measured in tests/scholar.py, which is where the network suites
live. What is measured here is everything that decides what a review is
ALLOWED TO SAY - the tier ladder, the screening log, the PRISMA arithmetic,
the staleness hash - because those are the parts that fail quietly.

  python tests/review.py
  python tests/review.py -v
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except ValueError:
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Same name collision as every other suite here: tests/review.py and
# tools/review.py share a name, so the engine is loaded by path under a
# distinct module name. Do not "simplify" this back.
_spec = importlib.util.spec_from_file_location(
    "review_engine", os.path.join(ROOT, "tools", "review.py"))
if _spec is None or _spec.loader is None:
    raise ImportError("cannot load tools/review.py")
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)

_spec_sc = importlib.util.spec_from_file_location(
    "scholar_engine_r", os.path.join(ROOT, "tools", "scholar.py"))
SCHOLAR = None
if _spec_sc is not None and _spec_sc.loader is not None:
    try:
        SCHOLAR = importlib.util.module_from_spec(_spec_sc)
        _spec_sc.loader.exec_module(SCHOLAR)
    except Exception:          # requests absent - the dedup check skips
        SCHOLAR = None

VERBOSE = "-v" in sys.argv
PASS = FAIL = SKIP = 0


def check(name: str, got, want, note: object = "") -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
        if VERBOSE:
            print(f"  pass  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")
        print(f"          got  {got!r}")
        print(f"          want {want!r}")
        if note:
            print(f"          {note}")


def skip(name: str, why: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  skip  {name} - {why}")


def section(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def write(root: str, rel: str, body: str) -> str:
    path = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    return path


def read(path: str) -> str:
    with io.open(path, "r", encoding="utf-8") as fh:
        return fh.read()


PROTOCOL = """\
# Review Protocol

## The Question *

What limits the thermal stability of alkanethiol monolayers on gold.

## Inclusion Criteria *

- alkanethiol monolayers on a gold surface
- reports a measured stability observable

## Exclusion Criteria *

- no-au: not on gold
- no-data: no measured stability data

## Date Window

from: 1983
to: 2026

## Venues

## Languages

## Sources

crossref
openalex

## Queries

alkanethiol monolayer thermal stability gold

## Corpus Floor

floor: 12

## Verification Window

days: 90
"""


def make_review(tmp: str, name: str = "rev", protocol: str = PROTOCOL) -> str:
    root = os.path.join(tmp, name)
    write(root, "project.yml",
          'title: "A review"\npaper_kind: review\nreview_kind: narrative\n')
    if protocol:
        write(root, "data/corpus/protocol.md", protocol)
    os.makedirs(os.path.join(root, "data", "corpus", "papers"),
                exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# The scope contract
# ---------------------------------------------------------------------------

def test_protocol(tmp: str) -> None:
    section("The scope contract gates the corpus build (review-paper 10)")

    root = make_review(tmp, "proto_empty", protocol="")
    res = rv.protocol(root)
    check("no protocol.md means no corpus build", res["can_build"], False)
    check("...and it is a talked-through refusal, not a throw",
          "worked around" in res["refused"], True)

    res = rv.protocol(root, init=True)
    check("--init writes the template", res["created"], True)
    check("...and the template is still UNWRITTEN, every section of it",
          sorted(res["unwritten"]), sorted(rv.PROTOCOL_SECTIONS))
    check("...because every instruction in it is an HTML comment",
          "<!--" in read(rv.protocol_path(root)), True)
    check("...so a scaffolded stub is never reported as filled in",
          res["can_build"], False)

    # protocol.md is never generated over. The moment a tool overwrites the
    # artifact its owner has been editing, nobody trusts it again.
    write(root, "data/corpus/protocol.md",
          PROTOCOL + "\n<!-- my own note -->\n")
    again = rv.protocol(root, init=True)
    check("--init on an existing protocol refuses", bool(again.get("refused")),
          True)
    check("...and does not touch a word of it",
          "my own note" in read(rv.protocol_path(root)), True)

    root = make_review(tmp, "proto_full")
    res = rv.protocol(root)
    check("a filled protocol can build", res["can_build"], True)
    check("...and can search, because it names queries", res["can_search"],
          True)
    check("the exclusion list is a MAPPING of code to meaning",
          res["exclusion"], {"no-au": "not on gold",
                             "no-data": "no measured stability data"})
    check("the floor is the user's number, read as one", res["floor"], 12)
    check("the verification window too", res["verify_days"], 90)
    check("the date window is read from its own section",
          (res["year_from"], res["year_to"]), ("1983", "2026"))
    check("the sources it declares are the ones a sweep uses",
          res["sources"], ["crossref", "openalex"])

    # The three required ones, individually.
    for missing in rv.PROTOCOL_REQUIRED:
        text = PROTOCOL
        start = text.index(f"## {missing}")
        end = text.index("\n## ", start + 4)
        text = text[:start] + f"## {missing} *\n\n" + text[end + 1:]
        r2 = make_review(tmp, "proto_" + missing.split()[-1].lower(),
                         protocol=text)
        res = rv.protocol(r2)
        check(f"an unwritten `{missing}` refuses the build",
              res["can_build"], False)
        check(f"...and the refusal names {missing}",
              missing in res["refused"], True)

    # A scope that is settled but has no queries is a different state and
    # says so rather than refusing.
    text = PROTOCOL.replace(
        "alkanethiol monolayer thermal stability gold\n", "")
    r3 = make_review(tmp, "proto_noq", protocol=text)
    res = rv.protocol(r3)
    check("a protocol with no queries can build but not search",
          (res["can_build"], res["can_search"]), (True, False))
    check("...and says a query that found nothing is how a negative claim "
          "is earned", "negative claim" in res["note"], True)


# ---------------------------------------------------------------------------
# The tier ladder - layer 0
# ---------------------------------------------------------------------------

def test_tier_rule(tmp: str) -> None:
    section("No sentence outruns the tier of the record it cites "
            "(review-paper 3)")

    root = make_review(tmp, "tier")
    rv.record_cmd(root, "smith2001gold", tier="abstract",
                  doi="10.1000/a", title="Gold monolayers",
                  claims="Monolayers desorb on heating.",
                  reports="Desorption begins near 350 K.")
    rv.record_cmd(root, "jones2005order", tier="fulltext",
                  doi="10.1000/b", title="Ordering",
                  claims="Order improves with chain length.",
                  reports="n = 6 samples; coverage 0.42 ML at 300 K.")
    rv.record_cmd(root, "brown2010title", tier="title",
                  doi="10.1000/c", title="A title only")
    for key in ("smith2001gold", "jones2005order", "brown2010title"):
        rv.record_cmd(root, key, verified=f"{rv.TODAY} via crossref "
                                          f"(confidence 1.0, verified)")

    check("the record writes its own limits sentence, not a person",
          rv.TIER_LIMITS["abstract"] in
          read(rv.record_path(root, "smith2001gold")), True)
    check("...and figure-digitized licenses LESS than abstract",
          rv.TIER_RANK["figure-digitized"] < rv.TIER_RANK["abstract"], True)

    # The load-bearing case, and the one the whole spec is written around.
    write(root, "drafts/source_text_r1/01_intro.md",
          "# Intro\n\n"
          "Monolayers desorb on heating [@smith2001gold].\n\n"
          "The study used n = 6 replicates [@smith2001gold].\n")
    res = rv.tier_check(root)
    kinds = {f["kind"] for f in res["findings"]}
    check("a sentence stating an abstract-tier source's n is refused",
          "out-of-tier" in kinds, True, str(res["counts"]))
    check("...and the clean sentence beside it is not flagged",
          len([f for f in res["findings"]
               if f["kind"] == "out-of-tier"]), 1)

    # The same sentence against a fulltext record is clean.
    write(root, "drafts/source_text_r1/01_intro.md",
          "# Intro\n\nThe study used n = 6 samples [@jones2005order].\n")
    res = rv.tier_check(root)
    check("the same sentence against a fulltext record is clean",
          [f["kind"] for f in res["findings"]], [])

    # Title tier licenses that the work exists, and nothing about what it
    # found.
    write(root, "drafts/source_text_r1/01_intro.md",
          "# Intro\n\nEarlier work reported a sharper transition "
          "[@brown2010title].\n")
    res = rv.tier_check(root)
    check("a characterization of a title-tier record is out of tier",
          [f["kind"] for f in res["findings"]], ["out-of-tier"])
    write(root, "drafts/source_text_r1/01_intro.md",
          "# Intro\n\nMonolayer stability has been studied on gold "
          "[@brown2010title].\n")
    res = rv.tier_check(root)
    check("...while saying the work exists is not",
          [f["kind"] for f in res["findings"]], [])

    # Numbers: the released budget does not reach layer 0.
    write(root, "drafts/source_text_r1/01_intro.md",
          "# Intro\n\nDesorption begins near 350 K [@smith2001gold].\n")
    res = rv.tier_check(root)
    check("a number the abstract carries is fine",
          [f["kind"] for f in res["findings"]], [])
    write(root, "drafts/source_text_r1/01_intro.md",
          "# Intro\n\nDesorption begins near 412 K [@smith2001gold].\n")
    res = rv.tier_check(root)
    check("a number the abstract does NOT carry is a finding",
          [f["kind"] for f in res["findings"]], ["number-not-in-record"])
    check("...and it says which number",
          [f["numbers"] for f in res["findings"]], [["412 K"]])
    write(root, "drafts/source_text_r1/01_intro.md",
          "# Intro\n\nStability rose to 412 K [@brown2010title].\n")
    res = rv.tier_check(root)
    check("a number on a title-tier record cannot be right at all",
          "number-out-of-tier" in {f["kind"] for f in res["findings"]}, True)

    # A citekey with no record at all.
    write(root, "drafts/source_text_r1/01_intro.md",
          "# Intro\n\nOthers disagree [@nobody1999missing].\n")
    res = rv.tier_check(root)
    check("a citekey with no record is refused",
          [f["kind"] for f in res["findings"]], ["no-record"])
    check("...reported once, with how many sentences lean on it",
          res["findings"][0]["sentence"], "1 sentence(s) cite @nobody1999missing")

    # An unverified record licenses nothing.
    rv.record_cmd(root, "fresh2020new", tier="abstract", title="New",
                  claims="Something happens.")
    write(root, "drafts/source_text_r1/01_intro.md",
          "# Intro\n\nSomething happens [@fresh2020new].\n")
    res = rv.tier_check(root)
    check("a record with no `verified:` line licenses no sentence",
          "unverified" in {f["kind"] for f in res["findings"]}, True)

    # One finding per sentence per key, however many times it is cited.
    write(root, "drafts/source_text_r1/01_intro.md",
          "# Intro\n\nStability rose to 412 K [@brown2010title] and again "
          "to 500 K [@brown2010title].\n")
    res = rv.tier_check(root)
    check("a key cited twice in one sentence is one finding",
          len([f for f in res["findings"]
               if f["kind"] == "number-out-of-tier"]), 1)


def test_tier_false_positives(tmp: str) -> None:
    section("The tier check does not cry wolf on clean prose (item 75)")

    # Six findings were raised on a real r1 draft and ALL SIX were false. A
    # draft written strictly inside its tiers that still reports findings
    # trains the author to skim them, and the one true finding is skimmed
    # with the rest - which is worse than the check not existing.
    root = make_review(tmp, "falsepos")
    write(root, "data/corpus/papers/full2010.md",
          "# full2010\nread_tier: fulltext\nverified: 2026-09-10 via "
          "crossref\n\n## What it reports\n\nA cohort of 240 patients; "
          "n = 240; spacing 1.5 A and 1.8 A.\n")
    write(root, "data/corpus/papers/abs2004.md",
          "# abs2004\nread_tier: abstract\nverified: 2026-09-10 via "
          "crossref\n\n## What it reports\n\nLattice spacings of 1.5 A "
          "and 1.8 A were resolved.\n")

    def only(text):
        write(root, "drafts/source_text_r1/01_intro.md",
              "# Intro\n\n" + text + "\n")
        return [f["kind"] for f in rv.tier_check(root)["findings"]]

    # 1. a float cross-reference is not a number about a source
    check("`Figure 1` is not charged as the number 1",
          only("The spacing is resolved in Figure 1 [@abs2004]."), [])
    # 2. a superscript charge state is not one either
    check("`Mg<sup>2+</sup>` is not charged as the number 2",
          only("Binding needs Mg<sup>2+</sup> [@abs2004]."), [])
    # 3. both sides of the comparison are normalized the same way
    check("a number the record holds as `1.5 A` is not reported absent "
          "because the prose wrote `1.5 angstrom`",
          only("Spacings of 1.5 angstrom and 1.8 angstrom are reported "
               "[@abs2004]."), [],
          "the record and the prose are written by different hands, so the "
          "comparison has to be on the numeral with the unit held apart")
    # 4. the ordinary verb `control`
    check("`controls` as a verb is not read as experimental controls",
          only("Hydrogen bonding controls the packing density [@abs2004]."),
          [])
    check("...while the noun still fires",
          only("The study used no controls [@abs2004]."), ["out-of-tier"])
    # 5. two sources, one clause each
    check("a detail carried by the fulltext record is not charged against "
          "the abstract-tier record beside it",
          only("The cohort of 240 patients showed the same spacing "
               "[@full2010; @abs2004]."),
          ["ambiguous-attribution"],
          "the ambiguity is reported; nothing is charged")
    check("...and the same holds for a number",
          only("Spacing reached 2.7 A in that series [@full2010; @abs2004]."),
          ["ambiguous-attribution"])

    # ...and the check still catches what it is for.
    check("a number in no cited abstract is still a finding",
          only("Desorption begins near 412 K [@abs2004]."),
          ["number-not-in-record"])
    check("...and an experimental detail against an abstract-only citation "
          "still is too",
          sorted(only("They used n = 240 [@abs2004].")),
          ["number-not-in-record", "out-of-tier"],
          "both are true of that sentence: 240 is in no cited record, and "
          "an abstract does not license a sample size either way")


def test_negative_claim(tmp: str) -> None:
    section("A negative claim is a claim about the corpus (review-paper 3)")

    root = make_review(tmp, "negative")
    write(root, "drafts/source_text_r1/01_intro.md",
          "# Intro\n\nNo study has examined alkanethiol desorption above "
          "500 K.\n")
    res = rv.tier_check(root)
    check("with no recorded search at all, the claim is flagged",
          [f["kind"] for f in res["findings"]], ["corpus"])
    check("...and the flag names the corpus, not a source",
          "FLAG: corpus" in res["findings"][0]["flag"], True)

    rv.log_search(root, {"date": rv.TODAY, "kind": "query",
                         "terms": "alkanethiol desorption temperature gold",
                         "sources": ["crossref"], "examined": 40, "new": 40})
    res = rv.tier_check(root)
    check("with a recorded search that would have found it, it is clean",
          [f["kind"] for f in res["findings"]], [])

    # A search about something else does not license it. This is the half
    # that makes the check worth having: "we searched" is not the claim.
    root2 = make_review(tmp, "negative2")
    rv.log_search(root2, {"date": rv.TODAY, "kind": "query",
                          "terms": "protein crystallography refinement",
                          "sources": ["crossref"], "examined": 40, "new": 40})
    write(root2, "drafts/source_text_r1/01_intro.md",
          "# Intro\n\nNo study has examined alkanethiol desorption above "
          "500 K.\n")
    res = rv.tier_check(root2)
    check("a recorded search about something else does not license it",
          [f["kind"] for f in res["findings"]], ["corpus"])
    check("...and says so rather than just counting the log",
          "none of them is about this" in res["findings"][0]["detail"], True)


# ---------------------------------------------------------------------------
# read_tier has exactly one writer
# ---------------------------------------------------------------------------

def test_tier_guard(tmp: str) -> None:
    section("read_tier inflating by hand is reported (review-paper 16.8)")

    root = make_review(tmp, "guard")
    rv.record_cmd(root, "a2001x", tier="abstract", title="A")
    check("a tier written by `record` is not reported",
          rv.hand_raised_tiers(root), [])

    path = rv.record_path(root, "a2001x")
    text = read(path).replace("read_tier: abstract", "read_tier: fulltext")
    write(root, os.path.relpath(path, root), text)
    raised = rv.hand_raised_tiers(root)
    check("a tier raised by hand is reported",
          [r["key"] for r in raised], ["a2001x"])
    check("...and the reason says what it licenses",
          "layer-0" in raised[0]["detail"], True)

    rv.record_cmd(root, "a2001x", tier="fulltext")
    check("...and going back through `record` clears it",
          rv.hand_raised_tiers(root), [])

    # Staleness is a CONTENT hash, never an mtime: OneDrive rewrites mtimes
    # on sync, and a ledger believed over the folder is worse than none.
    check("a record matching its stamp is not stale",
          rv.stale_records(root), [])
    same = read(path)
    os.utime(path, (0, 0))
    write(root, os.path.relpath(path, root), same)
    check("...and a touched file with identical content is still not stale",
          rv.stale_records(root), [])
    write(root, os.path.relpath(path, root), same + "\nedited by hand\n")
    check("...while an edited one is",
          [r["key"] for r in rv.stale_records(root)], ["a2001x"])

    bad = rv.record_cmd(root, "b2002y", tier="skimmed")
    check("an invented tier is refused", bool(bad["errors"]), True)
    check("...naming the ladder", "abstract" in bad["errors"][0], True)
    check("...and writing nothing", os.path.isfile(rv.record_path(root,
                                                                  "b2002y")),
          False)


# ---------------------------------------------------------------------------
# The screening log
# ---------------------------------------------------------------------------

def test_screening(tmp: str) -> None:
    section("Screening is a decision with a record (review-paper 13)")

    root = make_review(tmp, "screen")
    rv.append_screened(root, [
        {"date": "2026-09-01", "key": "a2001x", "title": "A", "year": "2001",
         "decision": "", "reason": ""},
        {"date": "2026-09-01", "key": "b2002y", "title": "B", "year": "2002",
         "decision": "", "reason": ""}])

    res = rv.screen(root)
    check("both candidates start undecided", res["counts"]["undecided"], 2)

    bad = rv.screen(root, "a2001x", "exclude")
    check("an exclusion with no reason is refused", bool(bad["errors"]), True)
    check("...because PRISMA counts by reason",
          "BY REASON" in bad["errors"][0], True)

    bad = rv.screen(root, "a2001x", "exclude", "it looked wrong")
    check("a free-text reason is refused", bool(bad["errors"]), True)
    check("...naming the protocol's own closed set",
          "no-au" in bad["errors"][0], True)

    res = rv.screen(root, "a2001x", "exclude", "no-au")
    check("a coded reason is accepted", res["errors"], [])
    check("...and counted", res["counts"]["exclude"], 1)

    rows_before = len(rv.read_screened(root))
    res = rv.screen(root, "a2001x", "include")
    check("include-after-exclude is allowed", res["counts"]["include"], 1)
    check("...and KEEPS both entries - no row is ever deleted",
          len(rv.read_screened(root)), rows_before + 1)
    check("...with the exclusion still readable in the log",
          [r["decision"] for r in rv.read_screened(root)
           if r["key"] == "a2001x"], ["", "exclude", "include"])

    res = rv.screen(root, "b2002y", "maybe")
    check("`maybe` is a real state", res["counts"]["maybe"], 1)
    check("...and is reported as outstanding until it resolves",
          res["outstanding"], 1)

    bad = rv.screen(root, "nosuchkey", "include")
    check("a key that was never a candidate is refused",
          bool(bad["errors"]), True)


# ---------------------------------------------------------------------------
# PRISMA
# ---------------------------------------------------------------------------

def test_prisma(tmp: str) -> None:
    section("PRISMA refuses a number it cannot derive (review-paper 13)")

    root = make_review(tmp, "prisma")
    res = rv.prisma(root)
    check("an empty log derives nothing, and says so",
          [u["count"] for u in res["undetermined"]], ["everything"])

    rv.append_screened(root, [
        {"date": "2026-09-01", "key": f"k{i}", "title": f"T{i}",
         "decision": "", "reason": ""} for i in range(5)])
    rv.screen(root, "k0", "include")
    rv.screen(root, "k1", "include")
    rv.screen(root, "k2", "exclude", "no-au")
    rv.screen(root, "k3", "exclude", "no-data")
    rv.screen(root, "k4", "exclude", "no-au")
    rv.record_cmd(root, "k0", tier="abstract", title="T0")
    rv.record_cmd(root, "k1", tier="abstract", title="T1")

    res = rv.prisma(root)
    check("identified counts distinct candidates", res["counts"]["identified"],
          5)
    check("included and excluded come out of the log",
          (res["counts"]["included"], res["counts"]["excluded"]), (2, 3))
    check("exclusions are counted BY REASON",
          res["by_reason"], {"no-au": 2, "no-data": 1})
    check("included and the records written agree, so it reports the number",
          res["counts"].get("included_in_synthesis"), 2)
    check("duplicates removed is UNDETERMINED, never a plausible number",
          any(u["count"] == "duplicates removed"
              for u in res["undetermined"]), True)

    # Two claims about the same set that disagree produce neither.
    rv.record_cmd(root, "k9", tier="abstract", title="T9")
    res = rv.prisma(root)
    check("a disagreement between the log and papers/ refuses the number",
          "included_in_synthesis" in res["counts"], False)
    check("...and says which two numbers disagree",
          any("two\n" in u["why"] or "two different claims" in u["why"]
              for u in res["undetermined"]), True)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def test_verification_cache(tmp: str) -> None:
    section("Verification is cached; retraction status never is "
            "(review-paper 12)")

    root = make_review(tmp, "verify")
    rv.record_cmd(root, "old2001x", tier="abstract", title="Old", doi="10.1/a",
                  verified="2020-01-01 via crossref (confidence 1.0, "
                           "verified)")
    rv.record_cmd(root, "new2026y", tier="abstract", title="New", doi="10.1/b",
                  verified=f"{rv.TODAY} via crossref (confidence 1.0, "
                           f"verified)")
    rv.record_cmd(root, "none2026z", tier="abstract", title="None",
                  doi="10.1/c")

    # No network here: the pass is measured by WHICH records it decides to
    # re-check, which is the whole of the caching contract.
    calls: list = []

    def fake(argv, timeout=180):
        calls.append([list(argv), timeout])
        return ({"status": "verified", "source": "crossref",
                 "confidence": 1.0, "match": {"is_retracted": False}}, "")

    real = getattr(rv, "_scholar")
    setattr(rv, "_scholar", fake)
    try:
        res = rv.verify(root, window=90)
        rechecked = {c["key"] for c in res["checked"]}
        check("a verdict inside the window is reused",
              "new2026y" in rechecked, False)
        check("...and one outside it is re-checked",
              "old2001x" in rechecked, True)
        check("...and one with no verdict at all always is",
              "none2026z" in rechecked, True)
        check("the reuse count is reported", res["reused"], 1)
        check("...and how old the oldest reused verdict is",
              res["oldest_reused_days"] is not None, True)
        check("...in a sentence, because `checked today` is a different "
              "claim", "reused" in res["reuse_note"], True)

        calls.clear()
        res = rv.verify(root, recheck_all=True)
        check("--all re-checks everything", len(res["checked"]), 3)
        check("...and retraction is re-checked for every one of them",
              all("retraction_checked" in read(rv.record_path(root, k))
                  for k in ("old2001x", "new2026y", "none2026z")), True)
        check("...and every one of those was a real call, not a cache read",
              len(calls), 3)
    finally:
        setattr(rv, "_scholar", real)

    check("a retracted paper is reported as a finding, not a field",
          "retracted: false" in read(rv.record_path(root, "new2026y")), True)


def test_verify_reports_a_dead_index_as_one(tmp: str) -> None:
    section("A source that did not answer is not a paper that is not real")

    root = make_review(tmp, "verify_down")
    rv.record_cmd(root, "a2001x", tier="abstract", title="A", doi="10.1/a")

    def down(_argv, timeout=180):
        assert timeout > 0
        return (None, "crossref unreachable: connection refused")

    real = getattr(rv, "_scholar")
    setattr(rv, "_scholar", down)
    try:
        res = rv.verify(root)
    finally:
        setattr(rv, "_scholar", real)
    check("the outage is an error, not a verdict", len(res["errors"]), 1)
    check("...and nothing was written into the record",
          "verified:" in read(rv.record_path(root, "a2001x")), False)


# ---------------------------------------------------------------------------
# Synthesis, coverage, echo
# ---------------------------------------------------------------------------

def test_synthesis(tmp: str) -> None:
    section("The evidence table is generated, and says it is")

    root = make_review(tmp, "synth")
    rv.record_cmd(root, "a2001x", tier="abstract", title="A", year="2001",
                  claims="A happens.")
    rv.record_cmd(root, "b1995y", tier="title", title="B", year="1995")
    res = rv.synthesis(root)
    text = read(res["path"])
    check("every record is a row", res["count"], 2)
    check("...in year order", [r["key"] for r in res["rows"]],
          ["b1995y", "a2001x"])
    check("the file says it is generated", "GENERATED" in text, True)
    check("...and that editing it is pointless", "Do not edit" in text, True)
    check("an unverified record is marked in the table, not omitted",
          "**NO**" in text, True)
    check("and each tier present carries its licence sentence",
          rv.TIER_LIMITS["title"] in text, True)


def test_coverage(tmp: str) -> None:
    section("Coverage reports what the corpus is, and what it is not")

    root = make_review(tmp, "cover")
    rv.record_cmd(root, "a2001x", tier="abstract", title="A",
                  source_kind="review", found_by="query")
    rv.record_cmd(root, "b2002y", tier="abstract", title="B",
                  source_kind="primary", found_by="query")
    rv.record_cmd(root, "c2003z", tier="abstract", title="C",
                  source_kind="primary", found_by="query")
    res = rv.coverage(root)
    check("the secondary share is counted, because no eye catches it at 200",
          res["secondary_share"], round(1 / 3, 3))
    check("...and reported as a finding above a quarter",
          any(f["kind"] == "secondary" for f in res["findings"]), True)
    check("a query-only corpus is told it has not snowballed",
          any(f["kind"] == "no-snowball" for f in res["findings"]), True)
    check("every record with no verdict is named",
          any(f["kind"] == "unverified" for f in res["findings"]), True)
    check("the ceiling is stated rather than implied",
          "not exhaustive" in res["ceiling"], True)

    rv.record_cmd(root, "d2004w", tier="abstract", title="D",
                  found_by="backward")
    res = rv.coverage(root)
    check("snowballing yield is counted separately", res["snowball_yield"], 1)


def test_echo_is_an_instrument(tmp: str) -> None:
    section("echo reports and never gates (review-paper 16.6)")

    root = make_review(tmp, "echo")
    rv.record_cmd(root, "a2001x", tier="abstract", title="A",
                  claims="The monolayer desorbs in a single sharp step at "
                         "elevated temperature.")
    write(root, "drafts/source_text_r1/01_intro.md",
          "# Intro\n\nThe monolayer desorbs in a single sharp step at "
          "elevated temperature [@a2001x].\n")
    res = rv.echo(root, min_ngram=8)
    check("a copied sentence is found", len(res["hits"]) >= 1, True)
    check("...with the span quoted so it can be judged",
          bool(res["hits"][0]["span"]), True)
    check("it says it is not calibrated", res["calibrated"], False)
    check("...and that it is advisory", res["advisory"], True)
    check("...in its own output, not only in a spec",
          "not a gate" in res["note"], True)

    write(root, "drafts/source_text_r1/01_intro.md",
          "# Intro\n\nDesorption is abrupt rather than gradual [@a2001x].\n")
    res = rv.echo(root, min_ngram=8)
    check("prose that shares no long span reports none", res["hits"], [])


# ---------------------------------------------------------------------------
# The contracts that are duplicated on purpose
# ---------------------------------------------------------------------------

def test_dedup_agrees_with_scholar() -> None:
    section("One normalizer, not two (review-paper 13)")

    if SCHOLAR is None:
        skip("the dedup pair agrees", "scholar.py needs `requests`")
        return
    cases = ["10.1021/LA100245A", "https://doi.org/10.1021/la100245a",
             "doi: 10.1021/la100245a", "  10.1021/la100245a.  ", ""]
    check("clean_doi agrees with scholar.py's on every spelling",
          [rv.clean_doi(c) for c in cases],
          [SCHOLAR.clean_doi(c) for c in cases])
    titles = ["Self-Assembled Monolayers on Gold!",
              "  self assembled   monolayers on gold  ", ""]
    check("normalize_title does too",
          [rv.normalize_title(t) for t in titles],
          [SCHOLAR.normalize_title(t) for t in titles])


def test_multi_source_sweep(tmp: str) -> None:
    section("A protocol naming several indexes searches all of them "
            "(item 70)")

    # The sweep itself is network and lives in tests/scholar.py. What is
    # measured here is the argv this engine builds and what it does with a
    # name scholar.py has never heard of - which is where every query in a
    # four-source protocol used to die with a usage message while the run
    # reported itself complete with 0 candidates.
    calls: list[list[str]] = []

    def fake_scholar(args, timeout=900):
        calls.append(list(args))
        return ({"results": [{"doi": "10.1/a", "title": "A paper",
                              "year": "2001", "found_in": ["crossref"]}],
                 "sources_tried": ["crossref", "openalex"],
                 "pages_read": {}, "returned": 1, "unreachable": []}, "")

    real = rv._scholar
    setattr(rv, "_scholar", fake_scholar)
    try:
        root = make_review(tmp, "sweep")
        res = rv.search(root, pages=1, dry_run=True)
        argv = calls[-1] if calls else []
        joined = argv[argv.index("--source") + 1] if "--source" in argv else ""
        check("every named index reaches scholar.py in one --source",
              joined, "crossref,openalex")
        check("...with no error raised against the protocol",
              res["errors"], [])
        check("...and the sweep returns candidates rather than nothing",
              res["added"], 1)
        check("...and what was searched is reported back",
              res["sources_used"], ["crossref", "openalex"])

        calls.clear()
        bad = PROTOCOL.replace("crossref\nopenalex",
                               "crossref\nGoogle Scholar")
        root2 = make_review(tmp, "sweep_unknown", protocol=bad)
        res2 = rv.search(root2, pages=1, dry_run=True)
        argv2 = calls[-1]
        check("an index scholar.py does not have is dropped, not passed on",
              argv2[argv2.index("--source") + 1], "crossref")
        check("...and is named, with the vocabulary, rather than swallowed",
              (res2["unknown_sources"],
               "google scholar" in res2["errors"][0],
               "openalex" in res2["errors"][0]),
              (["google scholar"], True, True))
        check("...while the queries still run",
              (res2["added"], len(res2["queries"])), (1, 1))

        calls.clear()
        worse = PROTOCOL.replace("crossref\nopenalex", "Google Scholar")
        root3 = make_review(tmp, "sweep_none", protocol=worse)
        rv.search(root3, pages=1, dry_run=True)
        argv3 = calls[-1]
        check("a section naming nothing the engine has falls to every index, "
              "never to no index",
              argv3[argv3.index("--source") + 1], "all",
              "a protocol naming sources must never return fewer candidates "
              "than one naming none")
    finally:
        setattr(rv, "_scholar", real)

    if SCHOLAR is None:
        skip("scholar.py accepts the joined list", "scholar.py needs "
             "`requests`")
        return
    check("scholar.py resolves the joined list to those indexes",
          SCHOLAR._resolve_sources("crossref,openalex", ["pubmed"]),
          ["crossref", "openalex"])
    check("...and the argument validator accepts it unchanged",
          SCHOLAR._source_arg(" Crossref , openalex "), "crossref,openalex")
    check("...and auto alone still means the command's own default",
          SCHOLAR._resolve_sources("auto", ["pubmed"]), ["pubmed"])
    check("...and all beside a name still means all",
          SCHOLAR._resolve_sources("crossref,all", ["pubmed"]),
          SCHOLAR.ALL_SOURCES)
    bad_name = ""
    try:
        SCHOLAR._source_arg("crossref,googlescholar")
    except Exception as exc:
        bad_name = str(exc)
    check("...and a name it does not have is refused by name",
          ("googlescholar" in bad_name, "crossref" in bad_name.split(" - ")[0]),
          (True, False))
    combined = ""
    try:
        SCHOLAR._source_arg("auto,crossref")
    except Exception as exc:
        combined = str(exc)
    check("...and auto combined with a named index is refused, since the two "
          "mean different things", "auto means" in combined, True)
    check("the vocabulary this engine validates against is scholar.py's own",
          list(rv.SOURCE_VOCABULARY), SCHOLAR.SOURCE_CHOICES,
          "duplicated on purpose - review.py is stdlib-only and reaches "
          "scholar.py through a subprocess - and only safe while this "
          "compares the copies")


def test_record_round_trip(tmp: str) -> None:
    section("What the engine writes it can read back")

    root = make_review(tmp, "trip")
    rv.record_cmd(root, "a2001x", tier="fulltext", doi="10.1/a", pmid="123",
                  title="A paper", authors="Smith J, Jones K", year="2001",
                  journal="Langmuir", source_kind="primary",
                  found_by="backward", retrieved="methods, results",
                  claims="It claims a thing.", reports="It reports 42 K.",
                  quotes="\"a quote\"", uses="the stability section")
    rec = rv.read_record(root, "a2001x")
    check("every field comes back", rec["doi"], "10.1/a")
    check("...including the ones the tier rule reads",
          (rec["read_tier"], rec["source_kind"], rec["found_by"]),
          ("fulltext", "primary", "backward"))
    check("...and the sections", rec["sections"]["What it reports"],
          "It reports 42 K.")
    check("retrieved_text is the record's own text and nothing else",
          "the stability section" in rv.retrieved_text(rec), False)
    check("...but does carry what the source said",
          "It reports 42 K." in rv.retrieved_text(rec), True)

    # An update does not lose what it did not touch.
    rv.record_cmd(root, "a2001x", quotes="another quote")
    rec = rv.read_record(root, "a2001x")
    check("an update keeps the fields it was not given", rec["doi"], "10.1/a")
    check("...and the sections it was not given",
          rec["sections"]["What it claims"], "It claims a thing.")
    check("...and the tier", rec["read_tier"], "fulltext")


def test_adopt(tmp: str) -> None:
    section("The input ladder's thin rung (review-paper 10)")

    root = make_review(tmp, "adopt")
    bib = os.path.join(tmp, "refs.bib")
    with io.open(bib, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("@article{ulman1996formation,\n"
                 "  title = {Formation and structure of self-assembled "
                 "monolayers},\n"
                 "  author = {Ulman, Abraham},\n"
                 "  year = {1996},\n"
                 "  journal = {Chemical Reviews},\n"
                 "  doi = {10.1021/cr9502357},\n}\n")
    res = rv.adopt(root, bib=bib)
    check("a .bib entry becomes a record",
          [a["key"] for a in res["adopted"]], ["ulman1996formation"])
    check("...at TITLE tier, because a .bib is a title",
          res["adopted"][0]["tier"], "title")
    check("...and it is told it is not verified yet",
          "verified" in res["note"], True)
    rec = rv.read_record(root, "ulman1996formation")
    check("the DOI came across", rec["doi"], "10.1021/cr9502357")

    res = rv.adopt(root, bib=bib)
    check("adopting the same file twice does not overwrite a record",
          [s["key"] for s in res["skipped"]], ["ulman1996formation"])

    pdfs = os.path.join(tmp, "pdfdrop")
    os.makedirs(pdfs, exist_ok=True)
    with io.open(os.path.join(pdfs, "smith2001.pdf"), "w",
                 encoding="utf-8") as fh:
        fh.write("%PDF-1.4\n")
    res = rv.adopt(root, pdfs=pdfs)
    check("a dropped PDF becomes a record at pdf tier",
          [a["tier"] for a in res["adopted"]], ["pdf"])

    barren = os.path.join(tmp, "ideas_no_bib")
    os.makedirs(barren, exist_ok=True)
    with io.open(os.path.join(barren, "summary.md"), "w",
                 encoding="utf-8") as fh:
        fh.write("# An idea\n\nSome papers exist.\n")
    res = rv.adopt(root, ideas=barren)
    check("an Ideas folder with no .bib is refused, and says why",
          any("recollection" in e for e in res["errors"]), True)


def test_cli(tmp: str) -> None:
    section("Every command answers --json on both sides, and exits sanely")

    import subprocess
    engine = os.path.join(ROOT, "tools", "review.py")
    root = make_review(tmp, "cli")
    rv.record_cmd(root, "a2001x", tier="abstract", title="A")

    for cmd in ("protocol", "screen", "synthesis", "coverage", "prisma",
                "status", "echo"):
        for argv in ([cmd, root, "--json"], ["--json", cmd, root]):
            run = subprocess.run([sys.executable, engine, *argv],
                                 capture_output=True, text=True,
                                 encoding="utf-8", errors="replace")
            ok = False
            try:
                json.loads(run.stdout)
                ok = True
            except json.JSONDecodeError:
                pass
            check(f"{' '.join(argv[:2])} emits JSON", ok, True,
                  (run.stdout or run.stderr)[:160])

    run = subprocess.run([sys.executable, engine, "record", root,
                          "--key", "x", "--tier", "invented", "--json"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    check("an invented tier exits 2", run.returncode, 2)

    run = subprocess.run([sys.executable, engine, "tier", root, "--json"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    check("tier with no drafted sections exits 2, not 0", run.returncode, 2)


def main() -> int:
    print("review.py - the corpus a review is checked against")
    tmp = tempfile.mkdtemp(prefix="review_")
    try:
        test_protocol(tmp)
        test_tier_rule(tmp)
        test_tier_false_positives(tmp)
        test_negative_claim(tmp)
        test_tier_guard(tmp)
        test_screening(tmp)
        test_prisma(tmp)
        test_verification_cache(tmp)
        test_verify_reports_a_dead_index_as_one(tmp)
        test_synthesis(tmp)
        test_coverage(tmp)
        test_echo_is_an_instrument(tmp)
        test_dedup_agrees_with_scholar()
        test_multi_source_sweep(tmp)
        test_record_round_trip(tmp)
        test_adopt(tmp)
        test_cli(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASS + FAIL} checks: {PASS} passed, {FAIL} failed"
          + (f", {SKIP} skipped" if SKIP else ""))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
