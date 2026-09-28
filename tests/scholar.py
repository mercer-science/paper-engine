#!/usr/bin/env python3
"""Measure tools/scholar.py against the live APIs. No mocks, no fixtures.

The suite this one exists for is THRESHOLD DERIVATION. scholar.py ships with
provisional verification boundaries taken from six measured titles; the spec
says they must not be called final until they are re-derived from a real corpus
(at least 40 known-real and 20 fabricated titles). This suite does that, and the
single most important assertion in it is that ZERO fabricated titles resolve to
`verified`.

Requires network, as tests/reliability.py already does.

Run:  python tests/scholar.py
      python tests/scholar.py --quick            sample the corpus, for iteration
      python tests/scholar.py --no-regression    skip re-running the pubmed suites
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import scholar  # noqa: E402

try:
    import requests
except ImportError:
    sys.exit("tests/scholar.py requires 'requests'.")

for _stream in (sys.stdout, sys.stderr):
    # reconfigure() belongs to TextIOWrapper, not to every text stream: under a
    # pipe or a capturing harness sys.stdout may have no such method at all.
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except ValueError:  # detached or already-closed stream
            pass

CASES = os.path.join(HERE, "scholar_cases.json")

# The five queries from spec §1 that returned zero or near-zero from PubMed.
# This engine exists to make them answerable, so they are a regression test.
GAP_QUERIES = [
    "Auger electron spectroscopy Cu(111) oxygen adsorption kinetics",
    "temperature programmed desorption ultrahigh vacuum copper oxide reduction",
    "HKUST-1 thin film thermal decomposition X-ray photoelectron spectroscopy",
    "quartz crystal microbalance sticking coefficient metal organic precursor",
    "density functional theory adsorption energy Cu2O surface reconstruction",
]

RETRACTED_DOI = "10.1177/1758835919874651"
CLEAN_DOI = "10.1016/j.susc.2005.01.038"
IDENTITY_DOI = "10.1021/acs.langmuir.8b03150"   # in PubMed, Crossref and EPMC

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append((name, detail))
    print(f"  [{'pass' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    return ok


def head(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


# ---------------------------------------------------------------------------
# 0. What KIND of thing is this reference - offline (user-asks 1)
# ---------------------------------------------------------------------------

def suite_source_class():
    """A database record is real, is not a paper, and must not read as either.

    The measured failure this suite pins: a UniProt entry in a real .bib went
    through the verify cascade, missed in four indexes because it is not a
    paper, and came back `not_found` - the word this engine reserves for
    invented citations. Both halves are asserted here: the classification
    itself, and that the classification keeps it OUT of the cascade.
    """
    head("0. SOURCE CLASSIFICATION - offline, no network")

    # A UniProt entry as a references.bib carries it.
    uniprot = {
        "title": "UniProtKB P58335 (ANTR2_HUMAN), Anthrax toxin receptor 2, "
                 "Homo sapiens",
        "authors": ["UniProt Consortium"], "year": "2026",
        "doi": "", "pmid": "", "entry_type": "misc", "key": "uniprot2026antxr2",
        "howpublished": "UniProt Knowledgebase (reviewed, Swiss-Prot), "
                        "entry P58335",
        "url": "https://www.uniprot.org/uniprotkb/P58335",
    }
    got = scholar.classify_source(uniprot)
    check("a UniProt entry classifies as a database record",
          got["class"] == "database", got["class"])
    check("...and is reported NOT peer reviewed",
          got["peer_reviewed"] is False, repr(got["peer_reviewed"]))
    check("...with a reason naming the evidence",
          "uniprot" in got["why"].lower() and got["evidence"] in
          ("url", "howpublished", "title"), f"{got['evidence']}: {got['why']}")

    article = {"title": "Crystal structure of the anthrax toxin protective "
                        "antigen", "authors": ["Petosa C"], "year": "1997",
               "doi": "10.1038/385833a0", "pmid": "9039918",
               "entry_type": "article", "journal": "Nature", "url": ""}
    got = scholar.classify_source(article)
    check("a journal article classifies as one", got["class"] == "journal-article",
          got["class"])
    check("...and is peer reviewed", got["peer_reviewed"] is True, "")

    # The honest third answer. A bare @misc says nothing about itself, and
    # "we cannot tell" is not the same claim as "it is not peer reviewed".
    bare = {"title": "Some note", "authors": [], "year": "2020", "doi": "",
            "pmid": "", "entry_type": "misc", "url": ""}
    got = scholar.classify_source(bare)
    check("a bare @misc is unknown, not `web` and not `database`",
          got["class"] == "unknown", got["class"])
    check("...and its peer-review status is null rather than False",
          got["peer_reviewed"] is None, repr(got["peer_reviewed"]))

    for entry, want, label in (
        ({"title": "A model", "entry_type": "misc", "doi": "",
          "url": "https://www.rcsb.org/structure/1ACC"}, "database", "RCSB PDB"),
        ({"title": "AF-P58335-F1", "entry_type": "misc", "doi": "",
          "url": "https://alphafold.ebi.ac.uk/entry/P58335"}, "database",
         "AlphaFold"),
        ({"title": "Compound", "entry_type": "misc", "doi": "",
          "url": "https://pubchem.ncbi.nlm.nih.gov/compound/2244"}, "database",
         "PubChem"),
        ({"title": "A preprint", "entry_type": "article", "doi": "",
          "eprint": "2401.00001", "archiveprefix": "arXiv"}, "preprint",
         "arXiv eprint"),
        ({"title": "Another preprint", "entry_type": "misc", "doi": "",
          "url": "https://www.biorxiv.org/content/10.1101/2024.01.01.000000v1"},
         "preprint", "bioRxiv"),
        ({"title": "A book", "entry_type": "book", "doi": "",
          "publisher": "Wiley"}, "book", "@book"),
        ({"title": "A chapter", "entry_type": "incollection", "doi": "",
          "booktitle": "Methods in Enzymology"}, "chapter", "@incollection"),
        ({"title": "A thesis", "entry_type": "phdthesis", "doi": "",
          "school": "BYU"}, "thesis", "@phdthesis"),
        ({"title": "A blog post", "entry_type": "misc", "doi": "",
          "url": "https://example.org/notes/why"}, "web", "an unrecognised URL"),
    ):
        got = scholar.classify_source(entry)
        check(f"{label} classifies as {want}", got["class"] == want,
              got["class"])

    # The list will go stale, and the failure mode when it does must be `web`
    # - a thing the user is asked about - rather than `journal-article`.
    unseen = {"title": "Entry", "entry_type": "misc", "doi": "",
              "url": "https://some-database-invented-tomorrow.org/x/1"}
    check("a database this list has never heard of degrades to `web`, never "
          "to a paper",
          scholar.classify_source(unseen)["class"] == "web", "")

    # Classification does not overrule an index. A DOI-bearing @misc that
    # Crossref knows about is whatever Crossref says it is.
    with_doi = {"title": "Entry", "entry_type": "misc",
                "doi": "10.1038/385833a0", "url": ""}
    got = scholar.classify_source(with_doi)
    check("a DOI-bearing entry stays verifiable rather than being ruled out",
          got["class"] in ("journal-article", "unknown")
          and got.get("verify") is not False, got["class"])

    kinds = {c["class"] for c in
             (scholar.classify_source(e) for e in
              (uniprot, article, bare, with_doi))}
    check("every class returned is in the declared vocabulary",
          kinds <= set(scholar.SOURCE_CLASSES), str(sorted(kinds)))

    # The text printer, which the JSON-path suites never touch. Measured: a
    # subscript on the status->icon map took the whole command down the first
    # time `not_a_paper` reached it, on a real bibliography - the suite was
    # green because it reads `--json`. A printer that meets an unfamiliar
    # status prints the status; it does not end the program.
    import io as _io
    import contextlib as _ctx
    for status in ("not_a_paper", "verified", "a_status_invented_in_2027"):
        buf = _io.StringIO()
        try:
            with _ctx.redirect_stdout(buf):
                scholar._print_verify(
                    {"claim": {"title": "T", "doi": "", "pmid": ""},
                     "status": status, "confidence": 0.0, "route": "",
                     "source": "", "sources_tried": [], "match": None,
                     "flags": [], "notes": []})
            ok = bool(buf.getvalue().strip())
        except Exception as exc:
            ok = False
            buf.write(repr(exc))
        check(f"_print_verify renders status {status!r} instead of crashing",
              ok, buf.getvalue().strip()[:120])


def suite_dispute_tiers():
    """The phrase list that says a later paper may disagree (user-asks 4).

    Offline: it measures the tiering, not the citation graph. Every case here
    came out of a live run or out of fixing one - the first row is the false
    positive the first live run produced, and the rule that separates it from
    the second row is the whole of this check.
    """
    head("0b. DISPUTE SIGNALS - tiering, offline")

    cases = [
        # (title, expected tier, what it is)
        ("Response to Arsenic in Rhodococcus aetherivorans BCP1", "contextual",
         "a bacterium responding to arsenic - the live false positive"),
        ("Response to arsenic and phosphorus starvation in yeast", "contextual",
         "`and` after a lowercase word is not an author list"),
        ("Arsenic and the response to stress", "contextual",
         "the phrase is mid-title"),
        ('Comment on "A bacterium that can grow by using arsenic"', "asserted",
         "a published Comment, naming a quoted title"),
        ("Reply to Smith et al.", "asserted", "a published Reply, naming authors"),
        ("Response to the comment by Jones and Brown", "asserted",
         "a published Response, naming the piece it answers"),
        ("Comment on the paper by Wolfe-Simon", "asserted", "names the paper"),
        ("Contrary to Smith et al., the film is stable", "asserted",
         "names what it contradicts"),
        ("Failure to replicate the Cu(111) oxidation result", "asserted",
         "a failed replication says so outright"),
        ("Cu(111) oxidation revisited", "contextual",
         "a revisit may confirm as easily as overturn"),
        ("A normal paper about copper surfaces", "none", "nothing at all"),
        ("Corrigendum: oxygen uptake on Cu(100)", "asserted",
         "a correction to the record"),
    ]
    for title, want, why in cases:
        hits = scholar.dispute_signals(title)
        got = ("asserted" if any(h["tier"] == "asserted" for h in hits)
               else "contextual" if hits else "none")
        check(f"{want:<10} {title[:52]}", got == want, f"got {got} - {why}")

    check("every phrase declares a tier the vocabulary knows",
          {p[1] for p in scholar.DISPUTE_PHRASES} == {"asserted", "contextual"},
          "")
    anchored = [p[0] for p in scholar.DISPUTE_PHRASES if p[3]]
    check("the phrases that need a leading position declare it",
          set(anchored) >= {"comment on", "reply to", "response to"},
          ", ".join(anchored))


# ---------------------------------------------------------------------------
# 1. Threshold derivation - blocks implementation sign-off
# ---------------------------------------------------------------------------

def top_similarity(res):
    """The best title score this citation reached anywhere in the cascade.

    Read from candidates as well as the match, so a `not_found` still reports
    how close the nearest real paper came - which is the number the boundary is
    derived from.
    """
    sims = [c["similarity"] for c in res.get("candidates", [])]
    if res.get("match"):
        sims.append(scholar.title_similarity(res["claim"]["title"], res["match"]["title"]))
    return max(sims) if sims else 0.0


def suite_thresholds(cases):
    head("1. THRESHOLD DERIVATION  (the boundaries scholar.py ships with)")
    rows = []
    t0 = time.time()
    for i, case in enumerate(cases, 1):
        res = scholar.verify(title=case["title"])
        rows.append({
            "expect": case["expect"], "field": case["field"], "title": case["title"],
            "status": res["status"], "confidence": res["confidence"],
            "source": res["source"], "sim": top_similarity(res),
            "unreachable": bool(res["unreachable"]),
            "matched_title": (res.get("match") or {}).get("title", ""),
            "matched_doi": (res.get("match") or {}).get("doi", ""),
        })
        if i % 10 == 0:
            print(f"    ... {i}/{len(cases)} cases, {time.time() - t0:.0f}s elapsed")

    real = [r for r in rows if r["expect"] == "verified"]
    fake = [r for r in rows if r["expect"] == "not_found"]

    def spread(rs):
        s = sorted(r["sim"] for r in rs)
        if not s:
            return "n/a"
        return (f"min {s[0]:.2f}  p10 {s[max(0, len(s) // 10)]:.2f}  "
                f"median {statistics.median(s):.2f}  max {s[-1]:.2f}")

    print(f"\n  REAL (n={len(real)}):  {spread(real)}")
    print(f"  FAKE (n={len(fake)}):  {spread(fake)}")

    # What would a boundary drawn anywhere actually cost? Sweep it rather than
    # asserting the shipped number is right.
    candidates = []
    for step in range(40, 100):
        t = step / 100
        false_ver = sum(1 for r in fake if r["sim"] >= t)
        missed = sum(1 for r in real if r["sim"] < t)
        # A fabricated citation adopted as real is the failure that matters;
        # a missed real paper only sends the user to look it up by hand.
        candidates.append((false_ver * 10 + missed, t, false_ver, missed))
    _, t_best, fv, ms = min(candidates)
    print(f"\n  Sweep over candidate boundaries (false-verification weighted 10x):")
    print(f"    lowest-cost boundary  {t_best:.2f}  "
          f"-> {fv} fabricated verified, {ms} real missed")
    print(f"    scholar.VERIFIED_MIN  {scholar.VERIFIED_MIN:.2f}  "
          f"-> {sum(1 for r in fake if r['sim'] >= scholar.VERIFIED_MIN)} fabricated "
          f"verified, {sum(1 for r in real if r['sim'] < scholar.VERIFIED_MIN)} real missed")
    print(f"    scholar.PARTIAL_MIN   {scholar.PARTIAL_MIN:.2f}  "
          f"-> {sum(1 for r in fake if r['sim'] >= scholar.PARTIAL_MIN)} fabricated "
          f"reached 'partial' (shown but not adopted)")

    highest_fake = max((r["sim"] for r in fake), default=0.0)
    print(f"\n  Highest-scoring fabrication: {highest_fake:.2f}")
    print(f"  Headroom to VERIFIED_MIN:    {scholar.VERIFIED_MIN - highest_fake:+.2f}")

    check("VERIFIED_MIN sits above every fabricated title's best score",
          highest_fake < scholar.VERIFIED_MIN,
          f"highest fake {highest_fake:.2f} < {scholar.VERIFIED_MIN}")

    for r in sorted(fake, key=lambda x: -x["sim"])[:3]:
        print(f"      {r['sim']:.2f}  {r['title'][:62]}")
    return rows, real, fake


# ---------------------------------------------------------------------------
# 2. No false verification - the assertion that matters most
# ---------------------------------------------------------------------------

def suite_no_false_verification(fake):
    head("2. NO FALSE VERIFICATION  (zero fabrications may resolve to 'verified')")
    verified = [r for r in fake if r["status"] == "verified"]
    check("no fabricated title resolved to 'verified'", not verified,
          f"{len(verified)} of {len(fake)} wrongly verified")
    for r in verified:
        print(f"      !! {r['title'][:60]}")
        print(f"         -> {r['matched_title'][:60]} ({r['matched_doi']})")

    partial = [r for r in fake if r["status"] == "partial"]
    print(f"\n  {len(fake) - len(partial) - len(verified)} not_found, "
          f"{len(partial)} partial (shown, not adopted), {len(verified)} verified")
    check("every partial fabrication withheld its DOI",
          all(not r["matched_doi"] for r in partial),
          "a partial must never emit an identifier")


def suite_real_found(real):
    head("3. REAL PAPERS RESOLVE  (and the gap this engine was built to close)")
    found = [r for r in real if r["status"] == "verified"]
    print(f"  {len(found)}/{len(real)} real papers verified")
    by_source = {}
    for r in found:
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1
    print(f"  verified by: {', '.join(f'{k} {v}' for k, v in sorted(by_source.items()))}")

    gap = [r for r in real if r["field"] in ("surface", "vacuum")]
    gap_found = [r for r in gap if r["status"] == "verified"]
    non_pubmed = [r for r in gap_found if r["source"] != "pubmed"]
    print(f"  surface/vacuum papers: {len(gap_found)}/{len(gap)} verified, "
          f"{len(non_pubmed)} of them by a non-PubMed source")
    check("most real papers resolve", len(found) >= 0.85 * len(real),
          f"{len(found)}/{len(real)}")
    check("the surface/vacuum literature is reachable at all",
          len(non_pubmed) > 0,
          f"{len(non_pubmed)} verified outside PubMed")
    misses = [r for r in real if r["status"] != "verified"]
    if misses:
        print(f"\n  {len(misses)} real papers not verified (informational):")
        for r in misses[:8]:
            print(f"      {r['status']:10} sim={r['sim']:.2f}  {r['title'][:56]}")


# ---------------------------------------------------------------------------
# 4. Crossref score is not a truth signal - confirm the spec's finding
# ---------------------------------------------------------------------------

def suite_score_is_not_a_gate(cases):
    head("4. CROSSREF `score` IS NOT A TRUTH SIGNAL  (confirming spec §5.1)")
    sample_real = [c for c in cases if c["expect"] == "verified"][:8]
    sample_fake = [c for c in cases if c["expect"] == "not_found"][:8]
    scores = {"REAL": [], "FAKE": []}
    for kind, sample in (("REAL", sample_real), ("FAKE", sample_fake)):
        for c in sample:
            try:
                r = requests.get("https://api.crossref.org/works",
                                 params={"query.bibliographic": c["title"], "rows": 1,
                                         "select": "DOI,title,score"},
                                 headers=scholar.UA, timeout=30)
                items = r.json()["message"]["items"]
                if items:
                    scores[kind].append(items[0].get("score") or 0.0)
            except Exception as exc:  # noqa: BLE001 - informational suite
                print(f"    (crossref probe failed: {exc})")
            time.sleep(1.0)
    for kind in ("REAL", "FAKE"):
        s = scores[kind]
        if s:
            print(f"  {kind}: n={len(s)}  min {min(s):.1f}  max {max(s):.1f}")
    if scores["REAL"] and scores["FAKE"]:
        overlaps = (min(scores["REAL"]) <= max(scores["FAKE"])
                    and min(scores["FAKE"]) <= max(scores["REAL"]))
        check("crossref `score` ranges overlap, so it cannot gate verification",
              overlaps, "confirmed: score must never be used as a truth signal")


# ---------------------------------------------------------------------------
# 5. Coverage - the five queries PubMed could not answer
# ---------------------------------------------------------------------------

def suite_coverage():
    head("5. COVERAGE  (the five spec §1 queries, which PubMed mostly could not answer)")
    for q in GAP_QUERIES:
        res = scholar.search(q, limit=5)
        pm = scholar.SOURCES["pubmed"]
        try:
            pm_n = len(pm.search(q, limit=5))
        except scholar.SourceUnreachable:
            pm_n = -1
        outside = [r for r in res["results"] if "pubmed" not in r["found_in"]]
        print(f"\n  {q[:66]}")
        print(f"    pubmed alone {pm_n:>2} | cascade {res['returned']:>2} "
              f"({len(outside)} that PubMed did not have)")
        for r in res["results"][:2]:
            print(f"      - {(r['journal'] or '?')[:30]:30s} {r['title'][:44]}")
        check(f"cascade returns results for: {q[:44]}", res["returned"] > 0,
              f"{res['returned']} records")


# ---------------------------------------------------------------------------
# 6. Retraction parity
# ---------------------------------------------------------------------------

def suite_retraction():
    head("6. RETRACTION PARITY  (per-source, on a known retracted/clean pair)")
    for name in ("crossref", "openalex", "europepmc"):
        try:
            bad = scholar.SOURCES[name].by_doi(RETRACTED_DOI)
            good = scholar.SOURCES[name].by_doi(CLEAN_DOI)
        except scholar.SourceUnreachable as exc:
            check(f"{name} answered for the retraction pair", False, exc.reason)
            continue
        flagged = bool(bad and (bad["is_retracted"] or bad["notices"]))
        check(f"{name} flags the retracted DOI", flagged)
        check(f"{name} leaves the clean control unflagged",
              good is None or not good["is_retracted"])

    res = scholar.verify(doi=RETRACTED_DOI)
    check("verify() reports the retraction as a flag",
          any("RETRACT" in f.upper() for f in res["flags"]))
    check("verify() keeps status 'verified' for a retracted paper",
          res["status"] == "verified",
          "status answers 'does it exist'; flags answer 'should you cite it'")
    res = scholar.verify(doi=CLEAN_DOI)
    check("the clean control verifies with no flags",
          res["status"] == "verified" and not res["flags"],
          f"{res['status']}, {len(res['flags'])} flags")


# ---------------------------------------------------------------------------
# 7. Schema conformance
# ---------------------------------------------------------------------------

def suite_schema():
    head("7. SCHEMA CONFORMANCE  (every adapter emits every key, absent = ''/[])")
    for name, src in scholar.SOURCES.items():
        # A source that declares no free-text search route is probed by DOI
        # instead. Calling its search and failing it for returning nothing would
        # be testing a route it never claimed to have.
        try:
            if src.has_search:
                recs = src.search("copper oxide surface", limit=3)
            else:
                one = src.by_doi(IDENTITY_DOI)
                recs = [one] if one else []
        except scholar.SourceUnreachable as exc:
            check(f"{name} answered for the schema probe", False, exc.reason)
            continue
        if not recs:
            check(f"{name} returned a record to check", False, "empty result")
            continue
        rec = recs[0]
        missing = [k for k in scholar.RECORD_DEFAULTS if k not in rec]
        check(f"{name} emits every record key", not missing, f"missing {missing}")
        wrong = [k for k, v in scholar.RECORD_DEFAULTS.items()
                 if isinstance(v, str) and rec.get(k) is None]
        check(f"{name} uses '' rather than None for absent strings", not wrong,
              f"None in {wrong}")
        listy = [k for k, v in scholar.RECORD_DEFAULTS.items()
                 if isinstance(v, list) and not isinstance(rec.get(k), list)]
        check(f"{name} uses [] rather than None for absent lists", not listy,
              f"not a list: {listy}")
        check(f"{name} stamps its own name on the record", rec["source"] == name)
        check(f"{name} never invents a PMID",
              rec["pmid"] == "" or rec["pmid"].isdigit(), f"pmid={rec['pmid']!r}")


# ---------------------------------------------------------------------------
# 8. Citation identity
# ---------------------------------------------------------------------------

def suite_citation_identity():
    head("8. CITATION IDENTITY  (one DOI, four sources, one citation string)")
    built = {}
    for name in ("pubmed", "crossref", "openalex", "europepmc"):
        try:
            rec = scholar.SOURCES[name].by_id(IDENTITY_DOI)
        except scholar.SourceUnreachable as exc:
            print(f"  {name}: unreachable ({exc.reason})")
            continue
        if not rec:
            print(f"  {name}: no record")
            continue
        scholar.enrich_abbrev(rec)
        built[name] = {"cite": scholar.format_citation(rec, "ama"), "rec": rec}
        print(f"  {name:10} {built[name]['cite'][:96]}")

    if "pubmed" not in built:
        check("pubmed returned the identity record", False)
        return
    ref = built["pubmed"]

    for name in ("crossref", "europepmc"):
        if name in built:
            check(f"{name} cites identically to pubmed",
                  built[name]["cite"] == ref["cite"])

    # OpenAlex hands over display names, not 'Surname Initials', so initials are
    # re-derived and can differ from PubMed's ("Chen Y" vs "Chen YL"). That is
    # the documented degradation, not a defect - so the bibliographic half is
    # asserted and the author half is reported.
    if "openalex" in built:
        a, b = built["openalex"]["rec"], ref["rec"]
        same = all(a[k] == b[k] for k in ("journal_abbrev", "year", "volume",
                                          "issue", "pages", "doi"))
        check("openalex agrees on journal, year, volume, issue, pages, doi", same)
        if built["openalex"]["cite"] != ref["cite"]:
            print("    note: openalex author initials differ from PubMed's "
                  "(display-name derivation, documented in spec §9.2)")

    # The abbreviation is the part most likely to drift, and the reason
    # enrich_abbrev() exists.
    for name, b in built.items():
        check(f"{name} carries a journal abbreviation",
              bool(b["rec"]["journal_abbrev"]), b["rec"]["journal_abbrev"])


# ---------------------------------------------------------------------------
# 9. Honest degradation
# ---------------------------------------------------------------------------

def suite_degradation():
    head("9. DEGRADATION  (a source that is down must be named, never silently empty)")
    src = scholar.SOURCES["crossref"]
    real_base = src.BASE
    src.BASE = "https://crossref.invalid.example/works"
    try:
        res = scholar.verify(title="The adsorption and incorporation of oxygen on Cu(100)")
        named = [u["source"] for u in res["unreachable"]]
        check("a downed source is named in `unreachable`", "crossref" in named, str(named))
        check("a downed source produces an explanatory note",
              any("did not answer" in n for n in res["notes"]))
        if res["status"] == "not_found":
            check("a not_found with a downed source says the search was incomplete",
                  any("incomplete" in n for n in res["notes"]))
        else:
            check("the cascade still answered from a surviving source",
                  res["status"] == "verified", f"{res['status']} via {res['source']}")

        s = scholar.search("copper oxide surface", limit=3)
        check("search names the downed source rather than returning a silent empty",
              any(u["source"] == "crossref" for u in s["unreachable"]))
        check("search still returns what the surviving sources had",
              s["returned"] > 0, f"{s['returned']} records")
    finally:
        src.BASE = real_base

    # A source failure must never be reported as evidence about the citation.
    check("SourceUnreachable is distinct from 'no such paper'",
          scholar._get("crossref", "https://api.crossref.org/works/10.9999/nope") is None,
          "a clean 404 returns None, it does not raise")


# ---------------------------------------------------------------------------
# 11. The corpus sweep  (specs/review-paper.md 6)
# ---------------------------------------------------------------------------

def suite_corpus_sweep():
    head("11. THE CORPUS SWEEP  (a review's validity is its recall)")
    term = "self-assembled monolayers on gold surfaces"

    # Measured 2026-09-10, before the fix: the same query returned 25 rows
    # from Crossref of which 24 were `component` records - a paper's figures
    # and supplementary files - leaving ONE citable article. The adapter was
    # right to refuse them and the request was wrong to ask for them.
    cr = scholar.SOURCES["crossref"]
    recs = cr.search(term, limit=25)
    check("Crossref asks for its type rather than filtering a spent page",
          len(recs) >= 15, f"{len(recs)} citable of 25 rows")
    check("...and what comes back is journal articles",
          all("component" not in (r.get("pubtypes") or [""])[0]
              for r in recs), "")

    # The union is computed and then thrown away. `--limit` is per-source
    # depth, so four sources at 25 examine up to 100 records; returning
    # rows[:limit] discarded the surplus AND reported its size in a field
    # nothing read.
    part = scholar.search(term, limit=25)
    whole = scholar.search(term, limit=25, want_all=True)
    check("--all returns the union rather than rows[:limit]",
          len(whole["results"]) == whole["returned"]
          and len(whole["results"]) > len(part["results"]),
          f"{len(part['results'])} shown of {whole['returned']} examined")
    check("...and the truncated call says it was truncated",
          part["truncated"] is True and part["shown"] == len(part["results"]),
          "")
    check("...while the whole union says it was not",
          not whole["truncated"], "")

    # Paging, per source, with each source's own idiom. Europe PMC is the
    # one that matters: `page` is silently IGNORED there, so a sweep that
    # trusted it reported two pages read and stopped at 25 records.
    epmc = scholar.SOURCES["europepmc"]
    pages = list(epmc.search_pages(term, limit=25, pages=2))
    if len(pages) < 2:
        check("Europe PMC returned a second page to compare", False,
              f"{len(pages)} page(s)")
    else:
        a = {r["title"].lower() for r in pages[0]}
        b = {r["title"].lower() for r in pages[1]}
        check("Europe PMC's second page is a DIFFERENT page",
              len(a & b) == 0, f"{len(a & b)} titles repeated")

    deep = scholar.search(term, limit=25, pages=2, want_all=True)
    check("--pages 2 deepens the union rather than repeating it",
          deep["returned"] > whole["returned"],
          f"{whole['returned']} -> {deep['returned']} candidates")
    check("...and reports how many pages each source actually answered",
          all(v >= 1 for v in deep["pages_read"].values()),
          str(deep["pages_read"]))


# ---------------------------------------------------------------------------
# 10. Supplementary-material guard
# ---------------------------------------------------------------------------

def suite_component_guard():
    head("10. SUPPLEMENTARY-MATERIAL GUARD  (a component must not become the citation)")
    # Measured while building: Crossref ranks 10.1021/acs.langmuir.8b03150.s001
    # (the SI file) ABOVE the article for the article's own exact title, and the
    # two carry identical titles - so title similarity cannot separate them.
    recs = scholar.SOURCES["crossref"].search(
        "Two-Step Adsorption of a Switchable Tertiary Amine Surfactant "
        "Measured by Quartz Crystal Microbalance", limit=5)
    dois = [r["doi"] for r in recs]
    check("no `component` DOI survives a Crossref search",
          not any(d.endswith(".s001") for d in dois), str(dois[:3]))

    res = scholar.verify(title="Two-Step Adsorption of a Switchable Tertiary Amine "
                               "Surfactant Measured Using a Quartz Crystal Microbalance "
                               "with Dissipation")
    got = (res.get("match") or {}).get("doi", "")
    check("verify adopts the article DOI, not its supplementary file",
          got == IDENTITY_DOI, got)

    res = scholar.verify(doi=IDENTITY_DOI + ".s001")
    check("a component DOI asked for directly is flagged as not the article",
          any("component" in f for f in res["flags"]))


# ---------------------------------------------------------------------------
# 11. The sources added after the first spec
# ---------------------------------------------------------------------------

def suite_new_sources():
    head("11. arXiv AND SEMANTIC SCHOLAR  (added on re-measurement)")

    ax = scholar.SOURCES["arxiv"]

    # The reason arXiv is in at all: a real cond-mat title resolves.
    real = ax.by_title("Chlorine adsorption on the Cu(111) surface", limit=3)
    check("arxiv finds a real cond-mat paper by title", bool(real),
          real[0]["title"][:60] if real else "no rows")
    if real:
        check("arxiv marks its records as preprints",
              "Preprint" in real[0]["pubtypes"], str(real[0]["pubtypes"][:3]))
        check("arxiv does not put a journal_ref in the journal field",
              real[0]["journal"] == "arXiv", real[0]["journal"])

    # The assertion that matters, same as for every other source.
    fake = ax.by_title("Holographic quantum entanglement of copper oxide "
                       "adsorption lattices", limit=3)
    check("arxiv returns nothing for a fabricated title", not fake,
          f"{len(fake)} rows")

    # Measured defect this guards: `Cu(111)` inside an arXiv query returns ZERO
    # rather than an error, which reads exactly like "no such paper".
    check("arxiv sanitizes parentheses out of a query",
          scholar.ArXiv._sanitize("oxygen adsorption Cu(111)") == "oxygen adsorption Cu 111",
          scholar.ArXiv._sanitize("oxygen adsorption Cu(111)"))

    # A malformed identifier is a miss, not an outage. arXiv answers HTTP 400
    # for one, which would otherwise surface as SourceUnreachable and mark a
    # whole check-refs run incomplete over a typo.
    try:
        check("arxiv treats a junk id as a miss, not a source failure",
              ax.by_id("not-an-arxiv-id-zz") is None)
    except scholar.SourceUnreachable as exc:
        check("arxiv treats a junk id as a miss, not a source failure", False,
              exc.reason)
    check("arxiv keeps the slash in an old-style id",
          scholar.ArXiv._clean_id("cond-mat/0501001") == "cond-mat/0501001",
          str(scholar.ArXiv._clean_id("cond-mat/0501001")))

    # arXiv sits last so a published paper is never cited as its own preprint.
    check("arxiv is last in the cascade", scholar.CASCADE[-1] == "arxiv",
          str(scholar.CASCADE))

    s2 = scholar.SOURCES["semanticscholar"]
    check("semanticscholar declares it has no search route", not s2.has_search)
    check("semanticscholar's search costs nothing rather than 429-ing",
          s2.search("anything at all", limit=3) == [])
    check("semanticscholar is NOT in the default cascade",
          "semanticscholar" not in scholar.CASCADE, str(scholar.CASCADE))
    check("semanticscholar is still reachable explicitly",
          "semanticscholar" in scholar.ALL_SOURCES
          and "semanticscholar" in scholar.SOURCE_CHOICES)
    check("semanticscholar qualifies identifiers by kind",
          [scholar.SemanticScholar._qualify(x) for x in ("10.1/a", "22745249", "1706.03762")]
          == ["DOI:10.1/a", "PMID:22745249", "arXiv:1706.03762"],
          str([scholar.SemanticScholar._qualify(x) for x in ("10.1/a", "22745249", "1706.03762")]))

    try:
        rec = s2.by_doi(IDENTITY_DOI)
        check("semanticscholar resolves a DOI keylessly", bool(rec),
              (rec or {}).get("title", "")[:50])
        if rec:
            # Measured: journal.volume arrives as '35 3' - volume and issue with
            # nothing saying which is which. Splitting it would be a guess.
            check("semanticscholar drops an ambiguous volume rather than splitting it",
                  " " not in rec["volume"], repr(rec["volume"]))
    except scholar.SourceUnreachable as exc:
        # Its keyless tier is genuinely intermittent; that is why it is out of
        # the cascade. An outage here is reported, not asserted against.
        print(f"  note: semanticscholar did not answer ({exc.reason}) - "
              f"expected occasionally on the keyless tier")

    # A 429 carrying a long Retry-After is a quota lockout, not an outage.
    # Measured 2026-09-06: OpenAlex answered 0 of 8 with Retry-After ~2167.
    # This is checked offline because a lockout cannot be summoned on demand,
    # and the old code could not have passed it: it slept 1.5 s and retried.
    class _Resp:
        def __init__(self, secs):
            self.headers = {"Retry-After": secs} if secs is not None else {}

    check("a long Retry-After is read, not ignored",
          scholar._retry_after(_Resp("2167")) == 2167.0,
          str(scholar._retry_after(_Resp("2167"))))
    check("a missing Retry-After yields no wait, not a crash",
          scholar._retry_after(_Resp(None)) == 0.0)
    check("an HTTP-date Retry-After degrades to 0 rather than raising",
          scholar._retry_after(_Resp("Wed, 21 Oct 2026 07:28:00 GMT")) == 0.0)
    check("a lockout exceeds the retry budget",
          2167.0 > scholar.RETRY_AFTER_BUDGET, str(scholar.RETRY_AFTER_BUDGET))
    lock = scholar._lockout("openalex", 2167.0)
    check("a lockout is reported as rate-limiting, not as an outage",
          "not an outage" in lock.reason and "rate-limited" in lock.reason,
          lock.reason[:60])

    # The search merge must skip a source that has no search route, and say so
    # rather than reporting it as having found nothing.
    res = scholar.search("copper surface oxidation", limit=5,
                         sources=["crossref", "semanticscholar"])
    check("search skips a source with no search route",
          res["no_search_route"] == ["semanticscholar"], str(res))
    check("search does not claim it tried one",
          "semanticscholar" not in res["sources_tried"], str(res["sources_tried"]))


# ---------------------------------------------------------------------------
# 12. No regression in the shipped engine
# ---------------------------------------------------------------------------

def suite_no_regression():
    head("12. NO REGRESSION  (pubmed.py's own suites must pass unmodified)")
    for name in ("reliability.py", "fulltext.py"):
        path = os.path.join(HERE, name)
        if not os.path.isfile(path):
            check(f"{name} exists", False)
            continue
        proc = subprocess.run([sys.executable, path], capture_output=True, text=True)
        check(f"tests/{name} still passes", proc.returncode == 0,
              f"exit {proc.returncode}")
        if proc.returncode != 0:
            print("      " + (proc.stdout or "")[-500:])


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="sample the corpus instead of running all of it")
    ap.add_argument("--no-regression", action="store_true",
                    help="skip re-running the pubmed.py suites")
    args = ap.parse_args()

    with open(CASES, encoding="utf-8") as fh:
        cases = json.load(fh)
    if args.quick:
        real = [c for c in cases if c["expect"] == "verified"][:10]
        fake = [c for c in cases if c["expect"] == "not_found"][:6]
        cases = real + fake

    n_real = sum(1 for c in cases if c["expect"] == "verified")
    n_fake = len(cases) - n_real
    print(f"scholar.py reliability suite - {n_real} real, {n_fake} fabricated titles")
    print(f"thresholds under test: VERIFIED_MIN={scholar.VERIFIED_MIN} "
          f"PARTIAL_MIN={scholar.PARTIAL_MIN}")
    if not args.quick and n_real < 40:
        print("!  spec §10 requires at least 40 real titles for threshold derivation")
    t0 = time.time()

    # First, and offline: it costs nothing and it is the one suite that can
    # run on a machine with no network at all.
    suite_source_class()
    suite_dispute_tiers()

    _rows, real, fake = suite_thresholds(cases)
    suite_no_false_verification(fake)
    suite_real_found(real)
    suite_score_is_not_a_gate(cases)
    suite_coverage()
    suite_retraction()
    suite_schema()
    suite_citation_identity()
    suite_degradation()
    suite_component_guard()
    suite_corpus_sweep()
    suite_new_sources()
    if not args.no_regression:
        suite_no_regression()

    head("SUMMARY")
    print(f"  {len(PASS)} passed, {len(FAIL)} failed, "
          f"{len(PASS) + len(FAIL)} checks in {time.time() - t0:.0f}s")
    for name, detail in FAIL:
        print(f"    FAIL  {name}" + (f"  ({detail})" if detail else ""))
    # The corpus is partly harvested from the same indexes the cascade queries,
    # so "real papers found" is an optimistic number and is not asserted tightly.
    # The fabricated half is independent of every index and is where the hard
    # assertion sits.
    print("\n  Note: the real half of the corpus was harvested from OpenAlex and "
          "Crossref,\n  so its retrieval rate is optimistic by construction. The "
          "fabricated half is\n  independent of every index, and carries the "
          "assertion that matters.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
