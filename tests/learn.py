#!/usr/bin/env python3
"""
tests/learn.py - measure tools/learn.py against the cases specs/
writing-engine-prose.md 6 names, plus the ones that would silently eat a
layer-0 rule.

Fully offline, ~2s. The three that matter most, because they fail quietly:

  - a factual edit produces NO prose rule (5.2), and a layer-0 absolute is
    refused as a budget (5.9) - both of those failing means the loop is
    learning to write numbers nobody measured
  - one observation stays a candidate and is applied to nothing (5.4) - this
    failing means the registry learns from n=1, and the wrongness arrives as
    slightly worse first drafts on an unrelated paper months later
  - 1c updating the hash of record (4.1) - this failing means a revised
    section is frozen against itself and the engine stops touching prose it
    wrote

  python tests/learn.py
  python tests/learn.py -v
"""

from __future__ import annotations

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
ENGINE = os.path.join(ROOT, "tools", "learn.py")

# tests/learn.py and tools/learn.py share a name, so a plain `import learn`
# resolves to this file. Load the engine by path under a distinct module name,
# exactly as every other suite here does.
_spec = importlib.util.spec_from_file_location("learn_engine", ENGINE)
if _spec is None or _spec.loader is None:
    raise ImportError("cannot load tools/learn.py")
learn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(learn)

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


TYPES = learn.read_types()


def ev(**kw) -> dict:
    """One evidence entry, with the fields the threshold counts filled in."""
    base = {"project": "P", "round": "r1", "section": "results",
            "research_type": "surface_science", "source": "user_edit"}
    base.update(kw)
    return base


def scope(section_name: str = "results",
          research_type: str = "surface_science") -> dict:
    return {"research_type": research_type, "section": section_name}


# ---------------------------------------------------------------------------
# The YAML subset: round trip, and loud failure
# ---------------------------------------------------------------------------

def test_yaml() -> None:
    section("The registry's YAML subset reads what it wrote (5.8)")

    text = """\
- id: LR-001
  rule: "Open the abstract with the material system, not the technique."
  kind: stylistic
  scope:
    research_type: surface_science
    section: title_abstract
  status: active
  observations: 2
  evidence:
    - {project: "Surface Example", round: r1, section: title_abstract,
       before: "Temperature-programmed desorption was used to...",
       after:  "Pd nanoparticles on TiO2(110) decompose..."}
    - project: "Oxide Example"
      round: r1
      section: title_abstract
  supersedes: []
  note: Demoted 2026-09-04: contradicted by LR-014 in surface_science.
        Held for structural_bio, where both observations came from.
  learned: 2026-09-04
"""
    data = learn.parse_yaml(text)
    check("a record is a mapping in a list",
          isinstance(data, list) and isinstance(data[0], dict), True)
    rec = data[0]
    check("a nested block mapping reads", rec["scope"]["research_type"],
          "surface_science")
    check("an int stays an int", rec["observations"], 2)
    check("both evidence spellings read", len(rec["evidence"]), 2)
    check("a flow mapping wrapped over three lines is read whole",
          rec["evidence"][0]["after"], "Pd nanoparticles on TiO2(110) "
                                       "decompose...")
    check("an empty flow list reads as a list", rec["supersedes"], [])
    check("a plain scalar wrapped over two lines is joined",
          rec["note"].endswith("where both observations came from."), True)
    check("...and a key after the nested block is not swallowed by it",
          rec["learned"], "2026-09-04")

    again = learn.parse_yaml(learn.dump_yaml(data))
    check("write, read, and re-emit without loss",
          learn.dump_yaml(again), learn.dump_yaml(data))

    # An apostrophe in a rule written as a sentence is ordinary, and a reader
    # that treats it as an unclosed quote swallows the rest of the file.
    apostrophe = learn.parse_yaml(
        "- id: LR-002\n  rule: State the user's own preference first.\n"
        "  kind: stylistic\n  status: active\n")
    check("an apostrophe does not swallow the file",
          apostrophe[0]["rule"], "State the user's own preference first.")
    check("...and the keys after it still read",
          apostrophe[0]["status"], "active")

    for bad, why in (
            ("not a list at all\n", "a bare line"),
            ("- id: LR-1\n  rule: x\n     kind: stylistic\n", "a stray indent"),
            ("- {id: LR-1, rule: x\n", "an unclosed flow mapping")):
        try:
            learn.parse_yaml(bad)
            ok = False
        except learn.RegistryError:
            ok = True
        check(f"{why} fails loudly rather than reading as empty", ok, True)


def test_registry_validation(tmp: str) -> None:
    section("A registry that cannot be read is not an empty registry")

    path = os.path.join(tmp, "rules.yml")
    check("an absent registry is a cold start, not an error",
          learn.read_registry(path), [])

    for body, why in (
            ("- id: LR-001\n  rule: x\n  status: active\n", "no kind"),
            ("- id: LR-001\n  rule: x\n  kind: stylistic\n", "no status"),
            ("- rule: x\n  kind: stylistic\n  status: active\n", "no id"),
            ("- id: LR-001\n  rule: x\n  kind: vibes\n  status: active\n",
             "a kind nothing recognises"),
            ("- id: LR-001\n  rule: x\n  kind: stylistic\n  status: maybe\n",
             "a status nothing recognises")):
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        try:
            learn.read_registry(path)
            ok = False
        except learn.RegistryError:
            ok = True
        check(f"a record with {why} is refused", ok, True)

    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write("- id: LR-001\n  rule: x\n  kind: stylistic\n"
                 "  status: active\n")
    rec = learn.read_registry(path)[0]
    check("a record with no scope is universal, any kind and any-section",
          rec["scope"], {"research_type": "universal", "paper_kind": "any",
                         "section": "any"})


# ---------------------------------------------------------------------------
# Thresholds - 5.4
# ---------------------------------------------------------------------------

def test_thresholds() -> None:
    section("One observation is never a rule (5.4)")

    records: list[dict] = []
    verdict = learn.observe(records, "Split sentences over 35 words.",
                            "stylistic", ev(paragraph=1), scope())
    check("one observation stays a candidate", verdict["status"], "candidate")
    check("...and is applied to nothing",
          learn.brief(records, "surface_science", "results", TYPES)
          ["counts"]["learned"], 0)
    check("...but is listed so the user can see it",
          learn.brief(records, "surface_science", "results", TYPES)
          ["counts"]["candidates"], 1)

    learn.observe(records, "Split sentences over 35 words.", "stylistic",
                  ev(paragraph=1), scope())
    check("the same observation twice is once",
          records[0]["observations"], 1)

    learn.observe(records, "Split sentences over 35 words.", "stylistic",
                  ev(paragraph=4), scope())
    check("two paragraphs of one section in one round are two observations",
          records[0]["observations"], 2)
    check("...and one piece of independent evidence, so still a candidate",
          records[0]["status"], "candidate",
          "an afternoon spent editing one section is not two observations")

    verdict = learn.observe(records, "Split sentences over 35 words.",
                            "stylistic", ev(section="discussion", paragraph=1),
                            scope())
    check("a second section promotes it", verdict["status"], "active")
    check("...and the reason is on the record",
          "threshold 2" in verdict["reason"], True)

    # Formatting is the same mechanism at a higher bar.
    records = []
    for i, sec_name in enumerate(("results", "methods")):
        learn.observe(records, "Capitalize Torr.", "formatting",
                      ev(section=sec_name, paragraph=i), scope("any"))
    check("formatting needs three, not two", records[0]["status"], "candidate")
    learn.observe(records, "Capitalize Torr.", "formatting",
                  ev(section="introduction", paragraph=1), scope("any"))
    check("...and three promotes it", records[0]["status"], "active")
    check("...as one rule, not one per section", len(records), 1,
          "a rule scoped to `any` is the same rule wherever it was seen")

    # 5.4: universal is earned only across research types.
    records = []
    for r in ("r1", "r2"):
        learn.observe(records, "Cut the opening sentence.", "stylistic",
                      ev(round=r, paragraph=1), scope())
    check("twice in one type is active but not universal",
          (records[0]["status"], records[0]["scope"]["research_type"]),
          ("active", "surface_science"))
    learn.observe(records, "Cut the opening sentence.", "stylistic",
                  ev(round="r1", paragraph=1, project="Kinase",
                     research_type="biochemistry"), scope())
    check("a second research type earns universal",
          records[0]["scope"]["research_type"], "universal")

    # 7: an edit made to fit a word cap looks exactly like a preference for
    # shorter prose, so it is applied and never learned from.
    records = []
    for r in ("r1", "r2"):
        learn.observe(records, "Shorten every sentence.", "stylistic",
                      ev(round=r, paragraph=1, length_forced=True), scope())
    check("a length-forced round induces nothing",
          (records[0]["status"], records[0]["observations"]),
          ("candidate", 0))

    verdict = learn.promote(records, records[0]["id"],
                            "the user said so in review")
    check("the user can promote past the threshold deliberately",
          verdict["status"], "active")
    check("...and the reason is kept", records[0]["promoted_by"],
          "the user said so in review")
    try:
        learn.promote(records, records[0]["id"], "")
        ok = False
    except learn.RegistryError:
        ok = True
    check("an unexplained promotion is refused", ok, True)


# ---------------------------------------------------------------------------
# Contradictions - 5.6
# ---------------------------------------------------------------------------

def test_conflicts() -> None:
    section("Overlapping scope, and the three outcomes (5.6)")

    a = {"id": "LR-001", "rule": "x", "kind": "stylistic", "status": "active",
         "scope": scope("title_abstract", "universal"), "evidence": []}
    b = {"id": "LR-002", "rule": "y", "kind": "stylistic", "status": "active",
         "scope": scope("title_abstract", "surface_science"), "evidence": []}
    c = {"id": "LR-003", "rule": "z", "kind": "stylistic", "status": "active",
         "scope": scope("results", "surface_science"), "evidence": []}
    d = {"id": "LR-004", "rule": "w", "kind": "stylistic", "status": "active",
         "scope": scope("title_abstract", "biochemistry"), "evidence": []}
    e = {"id": "LR-005", "rule": "v", "kind": "stylistic", "status": "active",
         "scope": scope("title_abstract", "life_science"), "evidence": []}

    check("a universal rule overlaps a typed one in the same section",
          learn.scopes_overlap(a, b, TYPES), True)
    check("a different section does not overlap",
          learn.scopes_overlap(b, c, TYPES), False)
    check("two unrelated types do not overlap",
          learn.scopes_overlap(b, d, TYPES), False)
    check("a parent type overlaps its child",
          learn.scopes_overlap(e, d, TYPES), True)
    check("...and not a child of a different parent",
          learn.scopes_overlap(e, b, TYPES), False)

    records = [a, b, c, d, e]
    res = learn.conflicts(records, "LR-001", TYPES)
    check("a universal rule overlaps every type in its section",
          sorted(p["b"] for p in res["overlaps"]),
          ["LR-002", "LR-004", "LR-005"])
    check("...and never a rule in another section",
          "LR-003" in [p["b"] for p in res["overlaps"]], False)

    # Outcome 1: different research type, keep both, narrow both.
    old = {"id": "LR-006", "rule": "State the technique first.",
           "kind": "stylistic", "status": "active",
           "scope": scope("title_abstract", "universal"),
           "evidence": [ev(research_type="structural_bio", paragraph=1),
                        ev(research_type="structural_bio", round="r2",
                           paragraph=1)]}
    new = {"id": "LR-014", "rule": "Open on the material system.",
           "kind": "stylistic", "status": "active",
           "scope": scope("title_abstract", "surface_science"),
           "evidence": [ev(paragraph=1)]}
    records = [old, new]
    res = learn.resolve_conflict(records, "LR-014", "LR-006", "demote",
                                 "in cryo-EM the method is the contribution",
                                 TYPES)
    check("the universal rule is demoted to the type its evidence came from",
          old["scope"]["research_type"], "structural_bio")
    check("...and what it was is recorded", old["demoted_from"], "universal")
    check("...with the required note", bool(old["note"]), True)
    check("...it is retained, not deleted", old["status"], "active")
    check("...and the two now know about each other",
          (new["conflicts_with"], old["conflicts_with"]),
          (["LR-006"], ["LR-014"]))
    check("...so their scopes no longer overlap",
          res["scopes_still_overlap"], False)

    try:
        learn.resolve_conflict(records, "LR-014", "LR-006", "demote", "",
                               TYPES)
        ok = False
    except learn.RegistryError:
        ok = True
    check("a demotion with no reason is refused", ok, True,
          "the reason a rule narrowed is the first thing lost if it is not "
          "written at the moment of narrowing")

    # Outcome 2: same research type, the newer supersedes the older.
    old = {"id": "LR-007", "rule": "a", "kind": "stylistic",
           "status": "active", "scope": scope(), "evidence": [ev()]}
    new = {"id": "LR-008", "rule": "b", "kind": "stylistic",
           "status": "active", "scope": scope(), "evidence": [ev()]}
    records = [old, new]
    learn.resolve_conflict(records, "LR-008", "LR-007", "supersede",
                           "the user edited the other way in r3", TYPES)
    check("the older rule is superseded", old["status"], "superseded")
    check("...with a pointer to what replaced it",
          old["superseded_by"], "LR-008")
    check("...and it is retained so the decision is reversible",
          [r["id"] for r in records], ["LR-007", "LR-008"])
    brief = learn.brief(records, "surface_science", "results", TYPES)
    ids = [i.split()[0] for layer in brief["layers"]
           for i in layer.get("items", []) if i.startswith("LR-")]
    check("a superseded rule is never handed to the drafter",
          ids, ["LR-008"])

    # Outcome 3: genuinely ambiguous - neither applied, both surfaced.
    old = {"id": "LR-009", "rule": "a", "kind": "stylistic",
           "status": "active", "scope": scope(), "evidence": [ev()]}
    new = {"id": "LR-010", "rule": "b", "kind": "stylistic",
           "status": "active", "scope": scope(), "evidence": [ev()]}
    records = [old, new]
    learn.resolve_conflict(records, "LR-010", "LR-009", "conflict",
                           "no reason a scientist would recognise", TYPES)
    check("an ambiguous pair is left in conflict",
          [r["status"] for r in records], ["conflict", "conflict"])
    check("...and neither is applied",
          learn.brief(records, "surface_science", "results", TYPES)
          ["counts"]["learned"], 0)


# ---------------------------------------------------------------------------
# Research type - 5.5
# ---------------------------------------------------------------------------

def test_research_types(tmp: str) -> None:
    section("The vocabulary is controlled, and adding to it needs a reason")

    check("the seeded vocabulary covers the fields the user works in",
          {"surface_science", "biochemistry", "structural_bio", "clinical",
           "synthetic_chem", "methods_dev"} <= {t["id"] for t in TYPES}, True)
    check("surface_science climbs to physical_science and then universal",
          learn.type_ancestors("surface_science", TYPES),
          ["surface_science", "physical_science", "universal"])
    check("an unknown type still resolves to universal, never to nothing",
          learn.type_ancestors("quantum_gravity", TYPES),
          ["quantum_gravity", "universal"])

    types_file = os.path.join(tmp, "types.yml")
    learn.write_types([dict(t) for t in learn.SEED_TYPES], types_file)
    run = subprocess.run(
        [sys.executable, ENGINE, "types", "--add", "geochem",
         "--label", "Geochemistry", "--types-file", types_file],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("adding a type with no reason is refused", run.returncode, 2)
    check("...and says why the vocabulary is controlled",
          "population of one" in run.stderr, True)

    run = subprocess.run(
        [sys.executable, ENGINE, "types", "--add", "geochem",
         "--label", "Geochemistry", "--parents", "physical_science",
         "--because", "two papers now, and neither fits the others",
         "--types-file", types_file],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("...and with one it is added", run.returncode, 0)
    added = [t for t in learn.read_types(types_file) if t["id"] == "geochem"]
    check("the new type carries its reason", bool(added[0]["because"]), True)
    check("...and its parent, so it can inherit a rule",
          added[0]["parents"], ["physical_science"])

    run = subprocess.run(
        [sys.executable, ENGINE, "observe", "--rule", "x",
         "--research-type", "astrology", "--section", "results",
         "--registry", os.path.join(tmp, "r.yml"),
         "--types-file", types_file],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("an unknown research type is refused", run.returncode, 2)
    check("...with the vocabulary listed", "surface_science" in run.stderr,
          True)

    # project.yml is where the answer is cached, per 5.5.
    proj = os.path.join(tmp, "typed_project")
    os.makedirs(proj, exist_ok=True)
    with io.open(os.path.join(proj, "project.yml"), "w",
                 encoding="utf-8") as fh:
        fh.write('title: "x"\nresearch_type: surface_science   # asked once\n')
    check("the research type is read from project.yml",
          learn._project_research_type(proj), "surface_science")

    records = [{"id": "LR-001", "rule": "a surface science rule",
                "kind": "stylistic", "status": "active",
                "scope": scope("results", "surface_science"),
                "evidence": [ev()]}]
    check("the layer-3 brief follows the research type",
          learn.brief(records, "surface_science", "results", TYPES)
          ["counts"]["learned"], 1)
    check("...and a different type gets none of it",
          learn.brief(records, "biochemistry", "results", TYPES)
          ["counts"]["learned"], 0)


def test_paper_kind_scope(tmp: str) -> None:
    section("A review's lessons stay in the review (review-paper 1)")

    # The failure this axis exists to stop: the first review the user edits
    # teaches the registry that surface_science papers attribute every
    # sentence to somebody, hedge constantly and carry no methods - and
    # those rules are then applied, at layer 3, to the next research paper,
    # with nothing anywhere saying where they came from.
    reviewish = {"id": "LR-900",
                 "rule": "attribute every characterization to its source",
                 "kind": "stylistic", "status": "active",
                 "scope": {"research_type": "surface_science",
                           "paper_kind": "review", "section": "results"},
                 "evidence": [ev(paper_kind="review")]}
    both = {"id": "LR-901", "rule": "never use the word novel",
            "kind": "stylistic", "status": "active",
            "scope": {"research_type": "surface_science",
                      "paper_kind": "any", "section": "results"},
            "evidence": [ev(paper_kind="review"), ev(round="r2")]}
    records = [reviewish, both]

    check("a rule learned on a review reaches a review's brief",
          [r["id"] for r in records
           if learn.scope_applies(r, "surface_science", "results", TYPES,
                                  "review")],
          ["LR-900", "LR-901"])
    check("...and does NOT reach a research paper's",
          [r["id"] for r in records
           if learn.scope_applies(r, "surface_science", "results", TYPES,
                                  "research")],
          ["LR-901"])
    check("the layer-3 brief is what actually drops it",
          learn.brief(records, "surface_science", "results", TYPES,
                      paper_kind="research")["counts"]["learned"], 1)
    check("...and says which kind it was built for",
          learn.brief(records, "surface_science", "results", TYPES,
                      paper_kind="research")["paper_kind"], "research")

    # A caller that names no kind gets everything, because every caller that
    # predates the axis does exactly that, and a rule quietly ceasing to
    # apply is the failure mode this whole registry is written against.
    check("a caller naming no kind still gets every rule",
          learn.brief(records, "surface_science", "results", TYPES)
          ["counts"]["learned"], 2)

    # And the same for a record written before the axis existed.
    old_rec = {"id": "LR-010", "rule": "an old rule", "kind": "stylistic",
               "status": "active",
               "scope": {"research_type": "surface_science",
                         "section": "results"},
               "evidence": [ev()]}
    check("a rule recorded before the axis existed applies to both kinds",
          [learn.scope_applies(old_rec, "surface_science", "results", TYPES,
                               k) for k in ("research", "review")],
          [True, True])

    # --- induction: the scope follows the evidence ----------------------
    records2: list[dict] = []
    learn.observe(records2, "hedge every comparison", "stylistic",
                  ev(paper_kind="review"),
                  {"research_type": "surface_science", "paper_kind": "review",
                   "section": "results"})
    v = learn.observe(records2, "hedge every comparison", "stylistic",
                      ev(round="r2", paper_kind="review"),
                      {"research_type": "surface_science",
                       "paper_kind": "review", "section": "results"})
    check("two review observations promote the rule", v["status"], "active")
    check("...scoped to reviews, because that is the only kind seen",
          v["record"]["scope"]["paper_kind"], "review")

    v = learn.observe(records2, "hedge every comparison", "stylistic",
                      ev(round="r3", paper_kind="research"),
                      {"research_type": "surface_science",
                       "paper_kind": "review", "section": "results"})
    check("evidence from the other kind widens it to every kind",
          v["record"]["scope"]["paper_kind"], "any")
    check("...and says so, rather than changing the scope silently",
          "paper kind" in v["reason"], True)

    # --- and two rules that can never meet do not conflict --------------
    check("rules scoped to different paper kinds do not overlap",
          learn.scopes_overlap(
              {"scope": {"research_type": "surface_science",
                         "paper_kind": "review", "section": "results"}},
              {"scope": {"research_type": "surface_science",
                         "paper_kind": "research", "section": "results"}},
              TYPES), False)
    check("...while `any` on either side still can",
          learn.scopes_overlap(
              {"scope": {"research_type": "surface_science",
                         "paper_kind": "any", "section": "results"}},
              {"scope": {"research_type": "surface_science",
                         "paper_kind": "research", "section": "results"}},
              TYPES), True)

    # --- project.yml is where the answer lives --------------------------
    proj = os.path.join(tmp, "review_project")
    os.makedirs(proj, exist_ok=True)
    with io.open(os.path.join(proj, "project.yml"), "w",
                 encoding="utf-8") as fh:
        fh.write('title: "x"\npaper_kind: review\n')
    check("the paper kind is read from project.yml",
          learn._project_paper_kind(proj), "review")
    with io.open(os.path.join(proj, "project.yml"), "w",
                 encoding="utf-8") as fh:
        fh.write('title: "x"\n')
    check("...and absent reads as research, never as nothing",
          learn._project_paper_kind(proj), "research")


# ---------------------------------------------------------------------------
# The brief - 5.7, 5.8
# ---------------------------------------------------------------------------

def test_brief(tmp: str) -> None:
    section("The ladder, in order, with absent layers omitted (5.7)")

    cold = learn.brief([], None, "results", TYPES)
    check("cold start emits layers 0, 2 and 4 only",
          [l["layer"] for l in cold["layers"]], [0, 2, 4])
    check("...with the ten rules and the sentence-level one in layer 2",
          len([i for l in cold["layers"] if l["layer"] == 2
               for i in l["items"]]), 11)
    check("...and the invariants stated rather than referenced",
          any("**[FLAG" in i for l in cold["layers"] if l["layer"] == 0
              for i in l["items"]), True)
    check("...and the precedence instruction at the end",
          "lower-numbered layer wins" in cold["instruction"], True)

    layer1 = os.path.join(tmp, "layer1.md")
    with io.open(layer1, "w", encoding="utf-8") as fh:
        fh.write("- You split every sentence over ~35 words in `methods`. "
                 "Four instances.\n- You removed \"notably\" wherever it "
                 "opened a sentence.\n")
    records = [{"id": "LR-020", "rule": "Open on the material system.",
                "kind": "stylistic", "status": "active",
                "scope": scope("results", "surface_science"),
                "evidence": [ev(after="Pd nanoparticles decompose first.")]}]
    full = learn.brief(records, "surface_science", "results", TYPES,
                       layer1, "r2")
    check("with edits and a registry, all five layers appear in order",
          [l["layer"] for l in full["layers"]], [0, 1, 2, 3, 4])
    check("layer 1 quotes the user's own edits",
          "35 words" in full["layers"][1]["body"], True)
    rendered = learn.render_brief(full)
    check("the rendered brief keeps the layers in order",
          [rendered.index(f"## Layer {n}") for n in (0, 1, 2, 3, 4)],
          sorted(rendered.index(f"## Layer {n}") for n in (0, 1, 2, 3, 4)))
    check("...and never emits an empty heading",
          "## Layer 3" in learn.render_brief(cold), False)

    res = learn.explain(records, "Pd nanoparticles decompose first.",
                        "surface_science", "results", TYPES, layer1)
    check("a sentence is attributed to the rule whose evidence it matches",
          res["best"]["id"], "LR-020")
    check("...and to the right layer", res["best"]["layer"], 3)
    res = learn.explain(records, "The films were grown at 450 C.",
                        "surface_science", "results", TYPES, layer1)
    check("a sentence matching nothing lexically still names what applied",
          "LR-020" in [r["id"] for r in res["applies_anyway"]], True)
    check("...highest authority first, so layer 1 leads",
          [r["layer"] for r in res["applies_anyway"]][0], 1)


# ---------------------------------------------------------------------------
# Budgets - 5.9
# ---------------------------------------------------------------------------

def test_budgets(tmp: str) -> None:
    section("A budget is learned, and the two absolutes are not (5.9)")

    records: list[dict] = []
    learn.observe(records, "density budget", "numeric",
                  ev(section="methods", source="override", count=11,
                     budget=4), scope("methods"),
                  {"inline": 4.0, "clusters": 3.0, "basis": "per100"})
    check("one override does not make a budget", records[0]["status"],
          "candidate")
    check("...so nothing resolves to it",
          learn.learned_budgets(records, "surface_science", TYPES), {})

    learn.observe(records, "density budget", "numeric",
                  ev(section="methods", round="r2", source="override",
                     count=9, budget=4), scope("methods"))
    check("two overrides make one", records[0]["status"], "active")
    learn.observe(records, "density budget", "numeric",
                  ev(section="methods", round="r3", project="Other",
                     source="user_edit", count=12), scope("methods"))
    check("the value is the 75th percentile of the observed counts",
          records[0]["value"]["inline"], 11.5,
          "the maximum would learn from the single worst paragraph")
    check("...not the maximum", records[0]["value"]["inline"] != 12.0, True)

    resolved = learn.learned_budgets(records, "surface_science", TYPES)
    check("it resolves for the type it was learned in",
          resolved["methods"]["source"], "learned:" + records[0]["id"])
    check("...and for a child of that type only through the parent",
          learn.learned_budgets(records, "biochemistry", TYPES), {})

    parent = [{"id": "LR-018", "rule": "density budget", "kind": "numeric",
               "status": "active", "scope": scope("results",
                                                  "physical_science"),
               "value": {"inline": 7.0, "clusters": 5.0, "basis": "per100"},
               "evidence": [ev()]}]
    resolved = learn.learned_budgets(parent, "surface_science", TYPES)
    check("a parent type's budget reaches its child",
          resolved["results"]["source"], "learned:parent:LR-018")
    own = parent + [{"id": "LR-019", "rule": "density budget",
                     "kind": "numeric", "status": "active",
                     "scope": scope("results", "surface_science"),
                     "value": {"inline": 3.0, "clusters": 2.0,
                               "basis": "per100"},
                     "evidence": [ev()]}]
    resolved = learn.learned_budgets(own, "surface_science", TYPES)
    check("...and the type's own budget beats the parent's",
          (resolved["results"]["source"], resolved["results"]["inline"]),
          ("learned:LR-019", 3.0))

    # Both directions: a discussion edited to REMOVE numbers pulls the budget
    # down. A mechanism that only ever loosened would be a slow amnesty.
    down: list[dict] = []
    for r in ("r1", "r2"):
        learn.observe(down, "density budget", "numeric",
                      ev(section="discussion", round=r, source="user_edit",
                         count_before=5, count_after=1),
                      scope("discussion"),
                      {"inline": 2.0, "clusters": 3.0, "basis": "per100"})
    check("an edit that removes numbers lowers the budget",
          down[0]["value"]["inline"], 1.0)

    # The refusal that matters most: a fraction cap looks exactly like a
    # budget, and it is a layer-0 absolute.
    for text in ("no paragraph over one-third numeric tokens",
                 "every inline number carries its interpretation",
                 "keep the numeric fraction under 33%"):
        check(f"a numeric record for {text[:28]!r} is refused",
              bool(learn.refuses_numeric(text, {"inline": 8.0})), True)
    check("...and an ordinary budget is not",
          learn.refuses_numeric("density budget", {"inline": 8.0}), "")
    check("a value keyed on the fraction is refused too",
          bool(learn.refuses_numeric("density budget", {"fraction": 0.5})),
          True)
    try:
        learn.observe([], "no paragraph over one-third numeric tokens",
                      "numeric", ev(), scope(), {"inline": 8.0})
        ok = False
    except learn.RegistryError:
        ok = True
    check("...and observe refuses to write one", ok, True)

    # A factual edit contributes to a numeric record and to nothing else.
    edits = learn.classify([{
        "granularity": "sentence", "op": "replace", "shape": "rewritten",
        "before": "The dose was 20 e/A2.",
        "after": "The dose was 20 e/A2 at 300 kV with a 1.2 um defocus.",
        "numbers": {"removed": [], "added": ["300", "1.2"], "changed": True},
        "citekeys": {"removed": [], "added": [], "changed": False},
        "flags": {"removed": [], "added": [], "changed": False},
        "words_before": 5, "words_after": 12}])
    check("a factual edit is classed factual", edits[0]["class"], "factual")
    check("...and may inform only a numeric budget",
          edits[0]["may_inform"], "numeric budget only")

    rows = learn.budgets(records, "surface_science", TYPES)["sections"]
    by_section = {r["section"]: r for r in rows}
    check("budgets prints the learned value beside the built-in default",
          (by_section["methods"]["inline"],
           by_section["methods"]["default_inline"]), (11.5, None))
    check("...and calls out a budget that came into existence",
          by_section["methods"]["wants_a_look"], True)
    check("the abstract's default is absolute, not a rate",
          by_section["title_abstract"]["basis"], "absolute")

    cfg = os.path.join(tmp, "writing_config.yml")
    with io.open(cfg, "w", encoding="utf-8") as fh:
        fh.write("number_density:\n  methods: low\n")
    rows = learn.budgets(records, "surface_science", TYPES, cfg)["sections"]
    check("this project's config beats the learned budget",
          [r["source"] for r in rows if r["section"] == "methods"],
          ["config"])


# ---------------------------------------------------------------------------
# extract and classify - 5.1, 5.2
# ---------------------------------------------------------------------------

def test_extract() -> None:
    section("Aligned before and after, and what is mechanical about it (5.1)")

    before = ("# Methods\n\n"
              "The chamber was evacuated to 2 x 10-9 Torr using a turbo pump "
              "backed by a rotary vane pump which ran for 12 h before the "
              "sample was sputtered.\n\n"
              "Films were grown by electron-beam evaporation.\n\n"
              "The coverage was determined by ion scattering.\n")
    after = ("# Methods\n\n"
             "The chamber was evacuated to 2 x 10-9 Torr. A turbo pump backed "
             "by a rotary vane pump ran for 12 h. The sample was then "
             "sputtered.\n\n"
             "Films were grown by electron-beam evaporation.\n")
    res = learn.align(before, after)
    shapes = [e.get("shape") for e in res["edits"]]
    check("a sentence split in three is reported as a split",
          "split_sentences" in shapes, True)
    check("an untouched paragraph is not an edit",
          res["paragraphs_unchanged"] >= 1, True)
    check("a deleted paragraph is reported as a deletion",
          [e["op"] for e in res["edits"] if e["op"] == "delete"], ["delete"])

    moved = ("# Results\n\n"
             "The coverage was determined by ion scattering.\n\n"
             "The chamber was evacuated to 2 x 10-9 Torr.\n")
    moved_after = ("# Results\n\n"
                   "The chamber was evacuated to 2 x 10-9 Torr.\n\n"
                   "The coverage was determined by ion scattering.\n")
    res = learn.align(moved, moved_after)
    check("a paragraph that changed places is a move, not a delete and an "
          "insert", [e["op"] for e in res["edits"] if e["op"] == "move"],
          ["move"])

    # Presentation is normalized; the value is not (3.1).
    check("0.42 and 0.420 are the same token",
          learn._multiset_delta(learn._numbers_of("a 0.42 ratio"),
                                learn._numbers_of("a 0.420 ratio"))["changed"],
          False)
    check("...and 42% and 0.42 are not",
          learn._multiset_delta(learn._numbers_of("a 42% ratio"),
                                learn._numbers_of("a 0.42 ratio"))["changed"],
          True, "converting units is a claim about what was measured")


def test_classify() -> None:
    section("Only two classes can become rules (5.2)")

    def one(before: str, after: str, gran: str = "sentence") -> dict:
        edits = learn.align("# S\n\n" + before + "\n", "# S\n\n" + after + "\n")
        wanted = [e for e in edits["edits"] if e["granularity"] == gran] \
            or edits["edits"]
        return learn.classify(wanted)[0]

    rec = one("The sample was annealed at 400 C.",
              "The sample was annealed at 450 C.")
    check("a corrected number is factual", rec["class"], "factual")
    check("...and never becomes a prose rule",
          "Never a prose rule" in rec["route"], True)
    check("...it surfaces as a data discrepancy",
          "data discrepancy" in rec["route"], True)

    rec = one("The support reduces first [@smith2020].",
              "The support reduces first [@jones2019].")
    check("a swapped citekey is a citation edit", rec["class"], "citation")
    check("...and routes to the citation ingest",
          "citation ingest" in rec["route"], True)

    edits = learn.align("# S\n\nOne paragraph.\n\nA second paragraph.\n",
                        "# S\n\nOne paragraph.\n")
    rec = learn.classify([e for e in edits["edits"]
                          if e["op"] == "delete"])[0]
    check("a deleted paragraph is content", rec["class"], "content")
    check("...and offers a waiver rather than a rule",
          "waiver" in rec["route"], True)

    rec = one("The film was grown at 12 torr.",
              "The film was grown at 12 Torr.")
    check("a term's capitalization is formatting", rec["class"], "formatting")

    # Whitespace alone is not an edit anywhere in this toolkit: it does not
    # freeze a section (4.1) and it does not reach the aligner either, so the
    # two agree on what counts as a change.
    check("whitespace alone is not an edit at all",
          learn.align("# S\n\nThe film was grown.\n",
                      "# S\n\nThe  film was  grown.\n")["edits"], [])

    rec = one("It may be possible that the layer is continuous, and the "
              "determination of the coverage was performed by ion scattering "
              "which indicates the same conclusion.",
              "The layer is continuous. We measured coverage by ion "
              "scattering, which points the same way.")
    check("a rewrite that touches no number or citekey is left undecided",
          rec["class"], "undecided")
    check("...with the three classes it could be",
          rec["candidates"], ["stylistic", "structural", "formatting"])
    check("...a mechanical proposal to start from",
          rec["proposed"] in ("stylistic", "structural"), True)
    check("...and the hedges it removed, counted",
          rec["hedges_delta"] < 0, True)

    forced = learn.classify([{
        "granularity": "sentence", "op": "replace", "shape": "rewritten",
        "before": "a long sentence", "after": "short",
        "numbers": {"removed": [], "added": [], "changed": False},
        "citekeys": {"removed": [], "added": [], "changed": False},
        "flags": {"removed": [], "added": [], "changed": False},
        "words_before": 3, "words_after": 1}], length_forced=True)
    check("a round over the word cap is applied and not learned from",
          "not learned from" in forced[0]["route"], True)


# ---------------------------------------------------------------------------
# The freeze - 4.1
# ---------------------------------------------------------------------------

def test_frozen(tmp: str) -> None:
    section("Frozen sections, inferred by hash (4.1)")

    proj = os.path.join(tmp, "freeze_project")
    st = os.path.join(proj, "source_text")
    os.makedirs(st)
    os.makedirs(os.path.join(proj, "Langmuir", "journal_requirements"))

    def write(name: str, body: str) -> str:
        path = os.path.join(st, f"{name}.md")
        with io.open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(body)
        return path

    methods = write("methods", "# Methods\n\nThe chamber was evacuated.\n")
    results = write("results", "# Results\n\nThe particles decomposed.\n")
    write("discussion", "# Discussion\n\nThe support reduces.\n")

    res = learn.frozen(proj)
    check("with no provenance every section is fresh",
          {r["state"] for r in res["sections"] if r["hash_now"]}, {"fresh"})

    learn.record_provenance(proj, ["methods", "results", "discussion"],
                            "draft-sections", "r1")
    res = learn.frozen(proj)
    states = {r["section"]: r["state"] for r in res["sections"]}
    check("a section the engine wrote is engine-owned",
          states["methods"], "engine-owned")
    check("...and may be redrafted", res["frozen"], [])

    with io.open(methods, "a", encoding="utf-8", newline="") as fh:
        fh.write("\nOne sentence added by hand.\n")
    res = learn.frozen(proj)
    check("one hand-edited section freezes", res["frozen"], ["methods"])
    check("...and only that one",
          [r["state"] for r in res["sections"]
           if r["section"] == "results"], ["engine-owned"])

    # Whitespace and line endings are not edits.
    body = _slurp(results, encoding="utf-8", newline="")
    with io.open(results, "w", encoding="utf-8", newline="") as fh:
        fh.write(body.replace("\n", "\r\n").replace(
            "decomposed.", "decomposed.   ") + "\n\n\n")
    res = learn.frozen(proj)
    check("CRLF, trailing spaces and blank runs do not freeze a section",
          [r["state"] for r in res["sections"]
           if r["section"] == "results"], ["engine-owned"])
    check("...and a changed word does",
          learn.section_hash("a b\n") == learn.section_hash("a  b\n"), False)

    # The regression this design is most likely to have.
    with io.open(methods, "w", encoding="utf-8", newline="") as fh:
        fh.write("# Methods\n\nThe chamber was evacuated to 2 x 10-9 Torr.\n")
    learn.record_provenance(proj, ["methods"], "revise-prose", "r1")
    res = learn.frozen(proj)
    entry = [r for r in res["sections"] if r["section"] == "methods"][0]
    check("1c updates the hash, so a revised section is not frozen against "
          "itself", entry["state"], "engine-owned")
    check("...and both hands are on the record",
          (entry["written_by"], entry["revised_by"]),
          ("draft-sections", "revise-prose"))

    os.remove(results)
    res = learn.frozen(proj)
    check("a deleted file is wiped, not frozen",
          [r["state"] for r in res["sections"]
           if r["section"] == "results"], ["wiped"],
          "emptying source_text/ to force a redraft has to keep working")
    check("...and it is not in the frozen list", "results" in res["frozen"],
          False)

    with io.open(learn.provenance_path(proj), "w", encoding="utf-8") as fh:
        fh.write("{not json")
    try:
        learn.frozen(proj)
        ok = False
    except learn.RegistryError:
        ok = True
    check("unreadable provenance fails loudly rather than looking fresh",
          ok, True)


# ---------------------------------------------------------------------------
# merge and stats - 5.8, 7
# ---------------------------------------------------------------------------

def test_merge(tmp: str) -> None:
    section("Two people, one registry, no git (5.8)")

    mine = [{"id": "LR-001", "rule": "Split long sentences.",
             "kind": "stylistic", "status": "active", "scope": scope(),
             "evidence": [ev(paragraph=1)]}]
    theirs = [
        {"id": "LR-001", "rule": "Split long sentences.", "kind": "stylistic",
         "status": "candidate", "scope": scope(),
         "evidence": [ev(paragraph=1), ev(section="methods", paragraph=2)]},
        {"id": "LR-002", "rule": "A rule of their own.", "kind": "stylistic",
         "status": "candidate", "scope": scope(), "evidence": [ev()]},
    ]
    res = learn.merge(mine, theirs)
    check("the same id and the same rule merges", res["merged"], ["LR-001"])
    check("...and the same evidence twice is once",
          mine[0]["observations"], 2)
    check("...so the threshold is re-evaluated on the union",
          mine[0]["status"], "active")
    check("a rule they have and I do not is added", res["added"], ["LR-002"])

    collide = [{"id": "LR-002", "rule": "A different rule entirely.",
                "kind": "stylistic", "status": "active", "scope": scope(),
                "evidence": [ev()]}]
    res = learn.merge(mine, collide)
    check("the same id holding a different rule is renumbered, not dropped",
          [r["to"] for r in res["renumbered"]], ["LR-003"])
    check("...and why it moved is written on it",
          "renumbered from LR-002" in mine[-1]["note"], True)

    path = os.path.join(tmp, "merged.yml")
    learn.write_registry(mine, path)
    check("the merged registry reads back",
          [r["id"] for r in learn.read_registry(path)],
          ["LR-001", "LR-002", "LR-003"])

    # A retired/superseded status on one side only is the conflict; the same
    # status on both sides is agreement. All four combinations, because the
    # condition was once a chained comparison that collapsed to "mine is
    # retired" - it fired when both agreed and stayed silent when only
    # theirs had retired the rule.
    def merged_status_conflict(ours: str, theirs_status: str) -> list:
        base = {"rule": "Split long sentences.", "kind": "stylistic",
                "scope": scope(), "evidence": [ev(paragraph=1)]}
        a = [dict(base, id="LR-001", status=ours)]
        b = [dict(base, id="LR-001", status=theirs_status)]
        return learn.merge(a, b)["conflicted"]

    check("both sides active is no status conflict",
          merged_status_conflict("active", "active"), [])
    check("both sides retired is agreement, not a conflict",
          merged_status_conflict("retired", "retired"), [])
    check("they retired a rule I hold active, and that conflicts",
          merged_status_conflict("active", "retired"), ["LR-001"])
    check("I retired a rule they hold active, and that conflicts",
          merged_status_conflict("retired", "active"), ["LR-001"])
    check("superseded counts the same as retired",
          merged_status_conflict("active", "superseded"), ["LR-001"])


def test_stats(tmp: str) -> None:
    section("What the registry cannot tell you about itself (7)")

    records = [
        {"id": "LR-001", "rule": "Split long sentences.", "kind": "stylistic",
         "status": "active", "scope": scope(),
         "evidence": [ev(paragraph=1), ev(section="methods", paragraph=1)]},
        {"id": "LR-002", "rule": "Every paragraph leads with its main idea.",
         "kind": "stylistic", "status": "active",
         "scope": scope("results", "clinical"),
         "evidence": [ev(project="Trial", research_type="clinical"),
                      ev(project="Trial", research_type="clinical",
                         round="r2")]},
    ]
    for rec in records:
        learn.evaluate_status(rec)

    digest = os.path.join(tmp, "writing_rules.md")
    with io.open(digest, "w", encoding="utf-8") as fh:
        fh.write("# House writing rules\n\n"
                 "**Every paragraph leads with its main idea in the first "
                 "sentence.** One message per paragraph.\n")
    res = learn.stats(records, TYPES, digest)
    check("observations are reported per research type",
          res["per_type"]["surface_science"]["observations"], 2)
    check("a type with one project in it is called out",
          "clinical" in res["thin_types"], True)
    check("a registry rule that restates a digest rule is listed",
          [r["id"] for r in res["restates_the_digest"]], ["LR-002"])
    check("...and one that says something new is not",
          "LR-001" in [r["id"] for r in res["restates_the_digest"]], False)


# ---------------------------------------------------------------------------
# The registry's home, and the plugin boundary
# ---------------------------------------------------------------------------

def test_registry_home(tmp: str) -> None:
    section("Where rules.yml lives, and why types.yml lives somewhere else "
            "(5.8)")

    # A plugin directory is replaced wholesale on update. Before this split
    # `rules.yml` sat at one hard-wired path inside the toolkit, so the first
    # `/plugin update` would have deleted every user's entire learning with no
    # error and no warning. Every check below is that defect, pinned.
    keys = ("PWA_RULES_FILE", "PWA_TYPES_FILE", "CLAUDE_PLUGIN_DATA")
    saved = {k: os.environ.get(k) for k in keys}

    def setenv(**kw) -> None:
        for k in keys:
            os.environ.pop(k, None)
        for k, v in kw.items():
            if v is not None:
                os.environ[k] = v

    plugin_data = os.path.join(tmp, "plugin_data")
    os.makedirs(plugin_data, exist_ok=True)
    try:
        setenv()
        check("with nothing set, rules.yml is the toolkit's - a source "
              "checkout is unchanged",
              learn.rules_file(), learn.TOOLKIT_RULES_FILE)
        check("...and so is the vocabulary",
              learn.types_file(), learn.TOOLKIT_TYPES_FILE)
        check("...and the source is named", learn.rules_home()[1], "toolkit")

        setenv(CLAUDE_PLUGIN_DATA=plugin_data)
        check("CLAUDE_PLUGIN_DATA set: the registry moves out of the plugin",
              learn.rules_file(), os.path.join(plugin_data, "rules.yml"))
        check("...and the source is named", learn.rules_home()[1],
              "plugin_data")
        check("...but the vocabulary does NOT move - it ships, and 5.5's "
              "proliferation guard needs it common",
              learn.types_file(), learn.TOOLKIT_TYPES_FILE)

        mine = os.path.join(tmp, "mine.yml")
        types = os.path.join(tmp, "mytypes.yml")
        setenv(CLAUDE_PLUGIN_DATA=plugin_data, PWA_RULES_FILE=mine)
        check("$PWA_RULES_FILE beats CLAUDE_PLUGIN_DATA",
              learn.rules_file(), mine)
        check("...and does not drag the vocabulary with it",
              learn.types_file(), learn.TOOLKIT_TYPES_FILE)
        setenv(PWA_TYPES_FILE=types)
        check("$PWA_TYPES_FILE overrides independently",
              (learn.types_file(), learn.rules_file()),
              (types, learn.TOOLKIT_RULES_FILE))

        setenv(CLAUDE_PLUGIN_DATA="")
        check("an env var set to empty is unset, not a path of ''",
              learn.rules_file(), learn.TOOLKIT_RULES_FILE)

        # A registry written under one resolution reads back under the same
        # one - the round trip that a plugin update used to break.
        setenv(PWA_RULES_FILE=mine)
        records: list = []
        learn.observe(records, "Open on the material system.", "stylistic",
                      ev(section="title_abstract"), scope("title_abstract"))
        learn.write_registry(records)
        check("a registry written under a resolution reads back under it",
              [r["id"] for r in learn.read_registry()], ["LR-001"])
        check("...and it was written where resolution said",
              os.path.isfile(mine), True)

        setenv(CLAUDE_PLUGIN_DATA=plugin_data)
        check("...and is NOT visible under a different one - which is why "
              "the stranded check exists at all",
              learn.read_registry(), [])
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    check("a source checkout has nothing stranded",
          learn.stranded_registry(), None)


# ---------------------------------------------------------------------------
# Waivers, and demotion as negative evidence
# ---------------------------------------------------------------------------

def _waivable() -> list:
    """One `active` rule with two independent observations behind it."""
    records: list = []
    learn.observe(records, "Open the abstract with the material system.",
                  "stylistic", ev(section="title_abstract"),
                  scope("title_abstract"))
    learn.observe(records, "Open the abstract with the material system.",
                  "stylistic", ev(section="title_abstract", round="r2"),
                  scope("title_abstract"))
    assert records[0]["status"] == "active"
    return records


def test_waivers(tmp: str) -> None:
    section("A waiver is negative evidence, and three demote the rule (5.10)")

    records = _waivable()
    try:
        learn.waive(records, "LR-001", "")
        check("waive without a reason is refused", "no exception", "refused")
    except learn.RegistryError as exc:
        check("waive without a reason is refused",
              "reason" in str(exc), True, str(exc))
    check("...and nothing was recorded by the attempt",
          records[0].get("waived"), None)

    res = learn.waive(records, "LR-001", "the technique is the contribution",
                      "UHV", "r2", "title_abstract")
    check("one waiver does not change the status", res["status"], "active")
    check("...it is recorded on the rule", records[0]["waivers"], 1)
    check("...verbatim", records[0]["waived"][0]["because"],
          "the technique is the contribution")

    learn.waive(records, "LR-001", "second reason", "UHV", "r2", "results")
    check("two waivers still do not demote", records[0]["status"], "active")
    res = learn.waive(records, "LR-001", "third reason", "UHV", "r3",
                      "discussion")
    check("three independent waivers demote active -> candidate",
          (res["was"], res["status"]), ("active", "candidate"))
    check("...and the demotion says so", res["demoted"], True)
    check("...all three reasons are held verbatim",
          records[0]["demoted_because"],
          ["the technique is the contribution", "second reason",
           "third reason"])
    check("...the evidence is untouched: this is a status change, not a "
          "deletion", len(records[0]["evidence"]), 2)
    check("...and the observation count is unchanged",
          records[0]["observations"], 2)

    # Otherwise a single unlucky section demotes a good rule.
    records = _waivable()
    for i in range(3):
        learn.waive(records, "LR-001", f"reason {i}", "UHV", "r2", "methods")
    check("three waivers in one section of one round are one waiver",
          records[0]["status"], "active")
    check("...all three are still recorded", records[0]["waivers"], 3)

    records = _waivable()
    for i, sec in enumerate(("methods", "results", "discussion")):
        learn.waive(records, "LR-001", f"reason {i}", "UHV", "r2", sec)
    check("three sections of one round DO demote - independence is the "
          "section in the round, exactly as 5.4 counts an observation",
          records[0]["status"], "candidate")

    # A demoted rule is applied to nothing, and comes back in one command.
    res = learn.brief(records, "surface_science", "title_abstract", TYPES)
    layer3 = [l for l in res["layers"] if l["layer"] == 3]
    check("a demoted rule is not in layer 3", layer3, [])
    check("...it is listed as a candidate the user can see",
          [c["id"] for c in res["candidates"]], ["LR-001"])
    check("...carrying the reasons it stopped applying",
          len(res["candidates"][0]["demoted_because"]), 3)

    learn.promote(records, "LR-001", "all three were one journal's house "
                                     "style, not the rule")
    check("promote --because brings a wrongly demoted rule back",
          records[0]["status"], "active")
    check("...and clears the demotion", records[0].get("demoted_because"),
          None)
    check("...but keeps the waivers, which are evidence too",
          records[0]["waivers"], 3)

    # A candidate is never applied, waived or not.
    cand: list = []
    learn.observe(cand, "A thin rule.", "stylistic", ev(), scope())
    check("one observation is a candidate", cand[0]["status"], "candidate")
    res = learn.waive(cand, "LR-001", "not right here")
    check("a candidate waived stays a candidate", res["status"], "candidate")
    check("...and is applied to nothing either way",
          [l for l in learn.brief(cand, "surface_science", "results",
                                  TYPES)["layers"] if l["layer"] == 3], [])


def test_layer3_evidence(tmp: str) -> None:
    section("A layer-3 entry carries its evidence, not just its text (5.10a)")

    records: list = []
    learn.observe(records, "Prefer we measured to it was measured.",
                  "stylistic", ev(project="UHV", section="methods"),
                  scope("methods"))
    learn.observe(records, "Prefer we measured to it was measured.",
                  "stylistic", ev(project="Ceria", section="methods"),
                  scope("methods"))
    learn.waive(records, "LR-001", "the passive is right in this sentence",
                "UHV", "r2", "methods")

    res = learn.brief(records, "surface_science", "methods", TYPES)
    layer3 = next(l for l in res["layers"] if l["layer"] == 3)
    entry = layer3["rules"][0]
    check("the entry states its observation count", entry["observations"], 2)
    check("...how many projects it was seen in", entry["projects"], 2)
    check("...when it was last reinforced",
          bool(entry["reinforced"]), True)
    check("...and how often it has been waived", entry["waivers"], 1)

    rendered = learn.render_brief(res)
    check("the rendered brief shows all four beside the rule",
          all(x in rendered for x in ("2 observations", "2 projects",
                                      "reinforced", "1 waiver")), True,
          rendered)
    check("layer 3's instruction is advisory, in 3.2's idiom",
          "not a specification" in rendered, True)
    check("...and it names where both lists go",
          "learned_rules.md" in rendered, True)
    check("...it does not tell the drafter to prefer the rule",
          "prefer the rule" in rendered.lower(), False)

    # 5.10d: a rule waived as often as it was observed wants the user's eye
    # whether or not it has reached three.
    check("a rule waived as often as observed is marked",
          entry["thin"], False)
    learn.waive(records, "LR-001", "again", "UHV", "r3", "methods")
    entry = next(l for l in learn.brief(records, "surface_science", "methods",
                                        TYPES)["layers"]
                 if l["layer"] == 3)["rules"][0]
    check("...once they equal the observations", entry["thin"], True)

    digest = os.path.join(tmp, "empty_digest.md")
    with io.open(digest, "w", encoding="utf-8") as fh:
        fh.write("# nothing here\n")
    st = learn.stats(records, TYPES, digest)
    check("stats reports the waiver rate per rule",
          (st["waived"][0]["waivers"], st["waived"][0]["observations"]),
          (2, 2))
    check("...and flags the ones whose waivers outnumber their observations",
          st["waived"][0]["outnumbers"], False)
    check("stats names where the registry resolved to",
          st["registry"]["source"] in ("toolkit", "plugin_data", "env"), True)


def test_preserve(tmp: str) -> None:
    section("What module 1c may not change, checked after the fact (3.1)")

    root = os.path.join(tmp, "revise")
    st = os.path.join(root, "drafts", "source_text")
    os.makedirs(st, exist_ok=True)

    BEFORE = ("# Results\n\nPd nanoparticles decomposed at 450 \u00b0C, well "
              "below the 620 \u00b0C onset of support reduction "
              "[@smith2020]. The **[FLAG: TPD peak area not in stats "
              "output]** area was not recorded.\n")

    def put(text: str) -> None:
        with io.open(os.path.join(st, "results.md"), "w",
                     encoding="utf-8") as fh:
            fh.write(text)

    def check_one(restore: bool = False) -> dict:
        return learn.preserve(root, "results", restore)

    put(BEFORE)
    res = learn.snapshot(root, ["results"])
    check("the snapshot is taken before the pass", res["sections"],
          ["results"])

    # The permissive case, and it is the one that matters: rewrite every
    # sentence, keep all three multisets, and the pass is ACCEPTED. That is
    # what proves the box is a box and not a cage.
    put("# Results\n\nSupport reduction begins at 620 \u00b0C. Pd "
        "nanoparticles had already decomposed by 450 \u00b0C [@smith2020], so "
        "decomposition precedes it. **[FLAG: TPD peak area not in stats "
        "output]** No peak area was recorded here.\n")
    check("every sentence rewritten, all three multisets intact - accepted",
          check_one()["verdict"], "accepted")

    put(BEFORE.replace("450 \u00b0C, well below", "well below"))
    res = check_one()
    check("a dropped number rejects the pass", res["verdict"], "rejected")
    check("...and names the token", res["failed"][0]["removed"], ["450"])

    put(BEFORE.replace("450", "450.0"))
    check("0.42 -> 0.420 is presentation, not value - accepted",
          check_one()["verdict"], "accepted")

    put(BEFORE.replace("450 \u00b0C", "723 K"))
    check("a unit conversion is NOT presentation - rejected, because "
          "converting units is a claim about what was measured",
          check_one()["verdict"], "rejected")

    put(BEFORE.replace("[@smith2020]", ""))
    check("a dropped citekey rejects the pass", check_one()["verdict"],
          "rejected")

    put(BEFORE.replace("TPD peak area not in stats output",
                       "TPD peak area not in stats outputs"))
    res = check_one()
    check("a FLAG block altered by one character rejects the pass",
          [f["invariant"] for f in res["failed"]], ["flags"],
          "a reworded flag is a lost item on the user's work list")

    put("# Results\n\nPd decomposed at 450 \u00b0C below the 620 \u00b0C "
        "onset [@smith2020]. **[FLAG: TPD peak area not in stats output]**\n")
    res = check_one()
    check("cutting more than 15% of the words rejects the pass",
          [f["invariant"] for f in res["failed"]], ["length"],
          "the band exists to stop a pass deleting a third of a section "
          "under cover of 'cutting'")

    put("")
    check("a section rendered empty rejects the pass",
          "empty" in [f["invariant"] for f in check_one()["failed"]], True)

    # Rejection is per section, not per run, and --restore puts back exactly
    # what was there.
    put(BEFORE.replace("450", "451"))
    res = check_one(restore=True)
    check("a rejected section is restored from its snapshot when asked",
          (res["restored"], _slurp(os.path.join(st, "results.md"),
                                   encoding="utf-8")), (True, BEFORE))

    os.makedirs(st, exist_ok=True)
    with io.open(os.path.join(st, "methods.md"), "w", encoding="utf-8") as fh:
        fh.write("# Methods\n\nThe chamber was baked.\n")
    learn.snapshot(root, ["methods"])
    put(BEFORE.replace("450", "451"))
    res = learn.preserve_all(root)
    check("rejection is per section - one bad section does not throw away a "
          "good one", (res["rejected"], res["counts"]["checked"]),
          (["results"], 2))

    res = learn.preserve(root, "discussion")
    check("a section with no snapshot claims nothing about itself",
          res["verdict"], "no-snapshot")


# ---------------------------------------------------------------------------
# The CLI contract
# ---------------------------------------------------------------------------

def test_revert_loop(tmp: str) -> None:
    """A user taking an automatic change back out is evidence (prose 11.5).

    11.3 made one rewrite pass automatic and 11.1 added a second that can
    be, so the guarantee has to run in the other direction too. The failure
    this closes is specific: without attribution a user undoing an automatic
    change looks exactly like a user editing the drafter's prose, and the
    pass makes the same unwanted change again next round. That is what makes
    automatic rewriting unwelcome, and closing it is why 11.3 is safe to
    default to `auto`.
    """
    section("A reverted automatic change demotes the rule (11.5)")

    NL = chr(10)
    report = NL.join([
        "# Prose report r1",
        "",
        "## Changed",
        "",
        "- pass: revise-prose --from-comprehension",
        "- rule: referent_unclear",
        "- before: The buffer serves as both the proton source and the "
        "counter-ion.",
        "- after: The buffer is both the proton source and the counter-ion.",
        "- why: copula avoidance, verdicted apply",
        "",
        "- pass: revise-prose --from-quality",
        "- rule: hedge_stack",
        "- before: The arrays may possibly suggest a thermophilic origin.",
        "- after: The arrays suggest a thermophilic origin.",
        "- why: three hedges in one sentence",
        "",
        "## Left alone",
        "",
        "- before: XPS, TEM and XRD confirmed the assignment.",
        "- after: XPS, TEM and XRD confirmed the assignment.",
        "- why: three instruments, not padding",
        ""])

    changes = learn.read_pass_changes(report)
    check("every recorded change is read back", len(changes), 2)
    check("...with the pass that made it",
          [c["pass"] for c in changes],
          ["revise-prose --from-comprehension", "revise-prose --from-quality"])
    check("...and the rule behind it",
          [c["rule"] for c in changes], ["referent_unclear", "hedge_stack"])
    check("`Left alone` is NOT read - the pass declined to act there, so "
          "there is nothing for a user to revert and reading it would "
          "manufacture attributions for text the pass never wrote",
          any("XPS" in c["before"] for c in changes), False)
    check("a report with no Changed region yields nothing rather than "
          "guessing", learn.read_pass_changes("# Report" + NL + NL + "nope"),
          [])

    # The user opens the paper, puts the first change back, leaves the second.
    before = ("# Results" + NL + NL
              + "The buffer is both the proton source and the counter-ion. "
              + "The arrays suggest a thermophilic origin." + NL)
    after = ("# Results" + NL + NL
             + "The buffer serves as both the proton source and the "
             + "counter-ion. The arrays suggest a thermophilic origin." + NL)
    edits = learn.attribute(learn.align(before, after)["edits"], changes)
    sent = [e for e in edits if e["granularity"] == "sentence"]
    check("the reverted sentence is attributed to the pass that wrote it",
          (sent[0]["reverted"], sent[0]["pass_origin"]),
          (True, "revise-prose --from-comprehension"))
    check("...and to the rule that drove it", sent[0]["pass_rule"],
          "referent_unclear")
    check("the change the user LEFT ALONE is not evidence of anything - "
          "silence is never evidence in this loop",
          any(e.get("reverted") and e.get("pass_rule") == "hedge_stack"
              for e in edits), False)

    cls = learn.classify(edits)
    check("a revert is its own class, and it outranks the ladder below it",
          [c["class"] for c in cls if c["granularity"] == "sentence"],
          ["revert"])
    check("...routed to a waiver, not to a rule candidate",
          "WAIVER" in [c for c in cls
                       if c["granularity"] == "sentence"][0]["route"], True)
    check("...and three of them demote the rule",
          "demote" in [c for c in cls
                       if c["granularity"] == "sentence"][0]["route"], True)

    # One user action must not be evidence both for and against a rule.
    # `_sentence_edits` already refuses to emit a whole-paragraph rewrite
    # twice for this reason; the guard has to point the other way too.
    para = [c for c in cls if c["granularity"] == "paragraph"]
    check("the enclosing paragraph edit does not become a second, opposite "
          "lesson from the same user action",
          [(p["class"], p["may_inform"]) for p in para], [("revert", "")])
    check("...and says why rather than vanishing from the list",
          "already counted" in para[0]["route"], True)

    ev = learn.revert_evidence(edits, project="p", round_id="r1",
                               section="results")
    check("one user action, one waiver", len(ev), 1)
    check("the reason is the USER'S OWN SENTENCE, because nobody can audit "
          "a generated one in six months",
          "serves as both the proton source" in ev[0]["because"], True)
    check("...and what the pass had written is kept beside it",
          "The buffer is both" in ev[0]["pass_wrote"], True)
    check("the waiver carries where it happened",
          (ev[0]["project"], ev[0]["round"], ev[0]["section"]),
          ("p", "r1", "results"))

    # A revert is only a revert if the user went BACK. Rewriting a third way
    # is an ordinary edit plus a revert - 11.5's third row - and rewriting
    # further in the pass's own direction is not a revert at all.
    onward = ("# Results" + NL + NL
              + "The buffer is the proton source and the counter-ion. "
              + "The arrays suggest a thermophilic origin." + NL)
    further = learn.attribute(learn.align(before, onward)["edits"], changes)
    check("editing further in the pass's own direction is not a revert",
          any(e.get("reverted") for e in further), False,
          str([e.get("toward_pre_pass") for e in further]))

    # Without the report nothing is attributed, and the loop degrades to its
    # old behaviour rather than to a wrong one.
    bare = learn.attribute(learn.align(before, after)["edits"], [])
    check("no report means no attribution, never a guessed one",
          [e["reverted"] for e in bare], [False, False])
    check("...and the classification is exactly what it was before",
          [c["class"] for c in learn.classify(bare)],
          [c["class"] for c in learn.classify(
              learn.align(before, after)["edits"])])


def test_cli(tmp: str) -> None:
    section("Every command answers --json on both sides, and exits sanely")

    registry = os.path.join(tmp, "cli_rules.yml")
    types_file = os.path.join(tmp, "cli_types.yml")
    learn.write_types([dict(t) for t in learn.SEED_TYPES], types_file)

    run = subprocess.run(
        [sys.executable, ENGINE, "--json", "observe",
         "--rule", "Open on the material system.",
         "--kind", "stylistic", "--research-type", "surface_science",
         "--section", "title_abstract", "--project", "UHV", "--round", "r1",
         "--paragraph", "1", "--registry", registry,
         "--types-file", types_file],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("observe writes and reports as JSON", run.returncode, 0,
          run.stderr[:200])
    out = json.loads(run.stdout)
    check("...a first observation is a candidate", out["status"], "candidate")
    check("...and the registry it wrote is named",
          os.path.basename(out["written"]), "cli_rules.yml")

    run = subprocess.run(
        [sys.executable, ENGINE, "brief", "--research-type",
         "surface_science", "--section", "title_abstract",
         "--registry", registry, "--types-file", types_file, "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("brief takes --json on the right as well", run.returncode, 0)
    check("...and a candidate is not in layer 3",
          json.loads(run.stdout)["counts"]["learned"], 0)

    run = subprocess.run(
        [sys.executable, ENGINE, "--json", "waive", "LR-001",
         "--because", "the technique is the contribution here",
         "--project", "UHV", "--round", "r2", "--section", "title_abstract",
         "--registry", registry, "--types-file", types_file],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("waive records against a rule and reports as JSON",
          (run.returncode, json.loads(run.stdout or "{}").get("status")),
          (0, "candidate"), run.stderr[:200])

    run = subprocess.run(
        [sys.executable, ENGINE, "waive", "LR-001",
         "--registry", registry, "--types-file", types_file],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("waive without --because is refused by the parser, not silently "
          "recorded", run.returncode != 0, True)

    run = subprocess.run(
        [sys.executable, ENGINE, "stats", "--registry",
         os.path.join(tmp, "nope.yml"), "--types-file", types_file],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("a registry that is not there is a cold start, not a failure",
          run.returncode, 0)

    with io.open(os.path.join(tmp, "broken.yml"), "w",
                 encoding="utf-8") as fh:
        fh.write("- id: LR-001\n  rule: x\n")
    run = subprocess.run(
        [sys.executable, ENGINE, "stats", "--registry",
         os.path.join(tmp, "broken.yml"), "--types-file", types_file],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("a malformed registry exits 2 and says what is wrong",
          (run.returncode, "kind" in run.stderr), (2, True))


def main() -> int:
    print("learn.py - the learning loop, measured against the spec's own cases")
    tmp = tempfile.mkdtemp(prefix="learn_")
    try:
        test_yaml()
        test_registry_validation(tmp)
        test_thresholds()
        test_conflicts()
        test_research_types(tmp)
        test_paper_kind_scope(tmp)
        test_brief(tmp)
        test_budgets(tmp)
        test_extract()
        test_classify()
        test_frozen(tmp)
        test_merge(tmp)
        test_stats(tmp)
        test_registry_home(tmp)
        test_waivers(tmp)
        test_layer3_evidence(tmp)
        test_preserve(tmp)
        test_revert_loop(tmp)
        test_cli(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASS + FAIL} checks: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
