#!/usr/bin/env python3
"""Measure tiered reading (`sections`) and OA retrieval (`pdf`) against reality.

Companion to reliability.py, which covers citation verification. This suite
covers the two additions from specs/pubmed-engine-changes.md and, per that
spec's §1, records the *actual* Tier 1 vs Tier 2 cost rather than estimating it.

Run:  python tests/fulltext.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import pubmed  # noqa: E402

# Fixed fixtures, chosen for what they exercise rather than for their content.
OA_PMID = "32142651"        # Hoffmann 2020, Cell - in the PMC OA subset
NO_PMC_PMID = "35245144"    # Yahya 2022, Orthopedics - no PMC record at all
AUTHOR_MS_PMID = "33301246"  # Polack 2020, NEJM - PMC record, full text present

# A 25-PMID set for the cost measurement. Any topical set works; this one is
# fixed so the numbers stay comparable between runs.
COST_QUERY = "SARS-CoV-2 spike protein receptor binding"


def _slurp(path, mode="r", **kw):
    """Read a whole file and close it.

    `open(path).read()` leaks the handle until the garbage collector gets to
    it - harmless in a short script, but it makes the suite noisy under
    `python -W all`, and on Windows a handle still open can block the
    tempdir cleanup these tests rely on.
    """
    with open(path, mode, **kw) as fh:
        return fh.read()


def _tokens(text: str) -> int:
    """~4 chars per token. Good enough to compare two tiers by an order of size."""
    return round(len(text) / 4)


def check(label: str, passed: bool, detail: str = "") -> bool:
    print(f"[{'pass' if passed else 'FAIL'}] {label}")
    if detail:
        print(f"         {detail}")
    return passed


def main() -> int:
    results = []

    print("=" * 78)
    print("TIERED READING")
    print("=" * 78)

    secs = {r["pmid"]: r for r in
            pubmed.fetch_sections([OA_PMID, NO_PMC_PMID, AUTHOR_MS_PMID],
                                  ["discussion", "methods"])}

    oa = secs[OA_PMID]
    disc = oa["sections"].get("discussion", "")
    results.append(check(
        "open-access PMID returns a non-empty discussion",
        len(disc) > 500,
        f"PMID {OA_PMID} -> {len(disc)} chars, sections present: {oa['available']}"))

    results.append(check(
        "alias matching reaches an oddly-titled methods section",
        bool(oa["sections"].get("methods")),
        f"matched 'STAR Methods' -> {len(oa['sections'].get('methods', ''))} chars"))

    # The regression guard. PMC full text carries a <ref-list> under <back> whose
    # entries have their own titles; a './/' descendant search would pull a cited
    # paper's words into this paper's discussion. Same bug shape as _parse_article.
    arts = pubmed._efetch_pmc([oa["pmc"]])
    art = arts.get(OA_PMID)
    ref_titles = []
    if art is not None and art.find("back") is not None:
        for rl in art.find("back").findall("ref-list"):
            for ref in rl.findall("ref"):
                for t in ref.iter("article-title"):
                    s = pubmed._text(t)
                    if len(s) > 40:
                        ref_titles.append(s)
    leaked = [t for t in ref_titles if t[:60] in disc]
    results.append(check(
        "<ref-list> does not leak into the discussion",
        bool(ref_titles) and not leaked,
        f"{len(ref_titles)} reference titles harvested, {len(leaked)} found in the "
        f"discussion text"))

    nopmc = secs[NO_PMC_PMID]
    results.append(check(
        "non-OA PMID returns available:[] with an abstract-only note, not an error",
        nopmc["available"] == [] and "abstract only" in nopmc["note"],
        f"PMID {NO_PMC_PMID} -> note: {nopmc['note']!r}"))

    ams = secs[AUTHOR_MS_PMID]
    results.append(check(
        "PMC record outside the OA subset still yields sections",
        bool(ams["sections"].get("discussion")),
        f"PMID {AUTHOR_MS_PMID} ({ams['pmc']}) -> {ams['available']}"))

    print()
    print("=" * 78)
    print("TIER 1 vs TIER 2 COST  (measured, per spec §1)")
    print("=" * 78)

    hits = pubmed.esearch(COST_QUERY, limit=25)
    pmids = hits["pmids"]
    records = pubmed.efetch(pmids)
    tier1 = sum(_tokens(r.get("abstract", "")) for r in records)
    print(f"Tier 1: {len(records)} abstracts            ~{tier1:>7,} tokens")

    escalate = [r["pmid"] for r in records if r.get("pmc")][:5]
    t2 = pubmed.fetch_sections(escalate, ["discussion", "conclusions"])
    tier2 = sum(_tokens(t) for r in t2 for t in r["sections"].values())
    got = sum(1 for r in t2 if r["sections"])
    print(f"Tier 2: {got}/{len(escalate)} discussions escalated  ~{tier2:>7,} tokens")
    if got:
        print(f"        mean per escalated paper    ~{round(tier2 / got):>7,} tokens")
    full = tier1 + tier2
    print(f"Working point (25 abstracts + {got} discussions)  ~{full:,} tokens")
    print("Reading all 25 at Tier 2 would cost roughly "
          f"~{round(tier2 / got * 25):,} tokens." if got else "")

    print()
    print("=" * 78)
    print("OPEN-ACCESS RETRIEVAL")
    print("=" * 78)

    out = tempfile.mkdtemp(prefix="pubmed-pdf-")
    try:
        pdfs = {r["pmid"]: r for r in
                pubmed.fetch_pdfs([OA_PMID, NO_PMC_PMID], out)}

        got_pdf = pdfs[OA_PMID]
        head = b""
        if got_pdf["ok"]:
            with open(got_pdf["path"], "rb") as fh:
                head = fh.read(5)
        results.append(check(
            "known-OA PMID returns a real PDF",
            got_pdf["ok"] and head.startswith(b"%PDF"),
            f"{os.path.basename(got_pdf['path'])} via {got_pdf['source']}, "
            f"magic {head!r}"))

        stub = pdfs[NO_PMC_PMID]
        results.append(check(
            "paywalled PMID returns a stub, not an error",
            stub["source"] == "stub" and stub["path"].endswith(".md")
            and os.path.isfile(stub["path"]),
            f"{os.path.basename(stub['path'])} - {stub['note']}"))

        body = _slurp(stub["path"], encoding="utf-8")
        results.append(check(
            "stub carries the metadata needed to find the paper by hand",
            all(k in body for k in ("**DOI:**", "**PMID:**", "## Abstract")),
            f"{len(body)} chars"))

        bib = os.path.join(out, "refs.bib")
        results.append(check(
            "refs.bib is emitted alongside",
            os.path.isfile(bib) and "@article{" in _slurp(bib, encoding="utf-8"),
            bib))
    finally:
        shutil.rmtree(out, ignore_errors=True)

    print()
    print("=" * 78)
    failed = len(results) - sum(results)
    print(f"{sum(results)}/{len(results)} checks passed, {failed} failed.")
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
