#!/usr/bin/env python3
"""
review.py - the corpus a review paper is checked against.

For a research paper, `data/` holds what was measured, and every number in the
prose is checked against it. For a review, almost nothing was measured - but
every sentence is a claim about somebody else's work, and those claims are
exactly as checkable, against exactly the same kind of record. This engine
owns that record: what was searched, what was screened, what was included,
what each paper actually says, and - the field the whole thing turns on - HOW
MUCH of each paper was actually read.

    data/corpus/
      protocol.md          the scope contract. Written once, by you
      searches.jsonl       append-only: one line per query, per page
      screened.csv         every candidate ever seen, decided or not
      papers/<key>.md      one record per included paper, named by citekey
      pdfs/                what was dropped in or retrieved
      synthesis.md         GENERATED from papers/ - the evidence table
      .corpus_state.json   GENERATED - content hashes, and the read_tier guard

The rule that makes it worth having (specs/review-paper.md 3):

    No sentence may characterize a source beyond the tier at which that
    source was read, and no source may be characterized at all unless
    data/corpus/papers/<citekey>.md exists and carries a `verified:` line.

A plausible false attribution has the property that made an invented methods
number the worst output this toolkit could produce: nothing in the folder
contradicts it, and it reads like scholarship.

  python review.py protocol  <project> [--init]
  python review.py search    <project> [--query "..."] [--pages 2]
  python review.py screen    <project> [--key K --decide include]
  python review.py adopt     <project> --bib refs.bib | --pdfs DIR | --ideas DIR
  python review.py record    <project> --key K --tier abstract [...]
  python review.py verify    <project> [--all]
  python review.py synthesis <project>
  python review.py tier      <project> [--sections DIR]
  python review.py coverage  <project>
  python review.py prisma    <project>
  python review.py echo      <project> [--min-ngram 8]
  python review.py status    <project>

Every command takes --json on either side of the subcommand. Exit codes are
meaningful: 0 clean, 1 findings, 2 refused or unusable input.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import io
import json
import os
import re
import subprocess
import sys

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
TODAY = datetime.date.today().isoformat()


# ---------------------------------------------------------------------------
# The tier ladder - layer 0 for a review (spec 3)
# ---------------------------------------------------------------------------
#
# `read_tier` is the load-bearing field in this whole engine, and it is the
# one a hurried hand is most tempted to raise. Measured on a real chemistry
# topic: of 25 candidates, 24 carry an abstract, 3 are open access, and ZERO
# carry a PMC identifier - so `pubmed.py sections`, the toolkit's only tiered
# full-text route, can read none of them. A chemistry review built with this
# engine is an ABSTRACT-TIER review unless the user supplies PDFs, and the
# engine will therefore be asked, a couple of hundred times, to say something
# specific about a paper it has read 180 words of. The specific thing will
# usually be true. That is what makes the rule necessary rather than pedantic.

TIERS = ("title", "figure-digitized", "abstract", "fulltext", "pdf")

# What each tier LICENSES, in order. `figure-digitized` sits BELOW `abstract`
# deliberately (spec 9): a value read off a published plot licenses saying
# that a value was read off a published plot, and not that the source
# reported it.
TIER_RANK = {"title": 0, "figure-digitized": 1, "abstract": 2,
             "fulltext": 3, "pdf": 3}

# Written into every record by the engine, never by a person, so that whoever
# opens the file sees what it does not license before they see what it says.
TIER_LIMITS = {
    "title": "Only the title was read. This record licenses saying that the "
             "work exists and what its subject is. It licenses nothing about "
             "what the work found.",
    "abstract": "Only the abstract was read. This record licenses the "
                "paper's own stated claim and its headline result, in the "
                "source's terms. It does NOT license its n, its conditions, "
                "its controls, its limitations, what its figures show, or "
                "that it failed to consider anything.",
    "fulltext": "The sections listed under `retrieved:` were read. This "
                "record licenses whatever those sections support, and "
                "nothing from a section that was not retrieved.",
    "pdf": "The full text was read as a PDF. This record licenses whatever "
           "the paper supports.",
    "figure-digitized": "A value was read off a published figure. This "
                        "record licenses saying that a value was read off a "
                        "published figure, and saying so in the sentence. It "
                        "does not license reporting the value as what the "
                        "source itself reported.",
}

# Which tiers carry numbers at all (spec 4.2). A number quoted from an
# abstract is fine when the abstract carries it and is a tier violation when
# it does not - so `abstract` is conditional and is checked against the
# retrieved text, while `title` cannot carry one under any circumstances.
TIERS_WITH_NUMBERS = ("abstract", "fulltext", "pdf", "figure-digitized")

# scholar.py's SOURCE_CHOICES, repeated rather than imported: this engine is
# standard-library only and reaches scholar.py through a subprocess, so it
# cannot ask. A contract duplicated on purpose is only safe while something
# compares the copies - tests/review.py reads SOURCE_CHOICES out of
# tools/scholar.py and holds it against this tuple.
SOURCE_VOCABULARY = ("auto", "all", "pubmed", "crossref", "openalex",
                     "europepmc", "arxiv", "semanticscholar")

SOURCE_KINDS = ("primary", "review", "preprint", "erratum", "conference",
                "thesis", "dataset")
FOUND_BY = ("query", "backward", "forward", "handpicked")
DECISIONS = ("include", "exclude", "maybe")
SEARCH_KINDS = ("query", "backward", "forward", "handpicked")

# The record's own section headings, in order. `## Limits of this record` is
# last because it is the engine's sentence about the four above it.
RECORD_SECTIONS = ["What it claims", "What it reports", "Quotes",
                   "What this review uses it for", "Limits of this record"]

SCREENED_FIELDS = ["date", "key", "doi", "title", "year", "source",
                   "found_by", "query", "decision", "reason"]


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def _read(path: str) -> str:
    try:
        with io.open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _append(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with io.open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _hash(text: str) -> str:
    """A CONTENT hash, never a modification time.

    OneDrive rewrites mtimes on sync, so a ledger that believed the clock
    would report every record stale after every sync - and a ledger believed
    over the folder is worse than no ledger, because it is believed.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def corpus_dir(project: str) -> str:
    return os.path.join(project, "data", "corpus")


def papers_dir(project: str) -> str:
    return os.path.join(corpus_dir(project), "papers")


def protocol_path(project: str) -> str:
    return os.path.join(corpus_dir(project), "protocol.md")


def searches_path(project: str) -> str:
    return os.path.join(corpus_dir(project), "searches.jsonl")


def screened_path(project: str) -> str:
    return os.path.join(corpus_dir(project), "screened.csv")


def state_path(project: str) -> str:
    return os.path.join(corpus_dir(project), ".corpus_state.json")


def synthesis_path(project: str) -> str:
    return os.path.join(corpus_dir(project), "synthesis.md")


def is_review(project: str) -> bool:
    """project.yml:paper_kind. Absent reads as research (review-paper 1)."""
    for line in _read(os.path.join(project, "project.yml")).splitlines():
        m = re.match(r"^paper_kind\s*:\s*(.*)$", line)
        if m:
            return m.group(1).split("#", 1)[0].strip().strip("\"'") == "review"
    return False


# ---------------------------------------------------------------------------
# protocol.md - the scope contract
# ---------------------------------------------------------------------------
#
# Written once, by the user, and NEVER generated over. Same rule as
# plan/outline.md and the TOC .pptx: the moment a tool overwrites the artifact
# its owner has been editing, nobody trusts it again. The skill PROPOSES a
# protocol from the topic and the user corrects it.
#
# Every instruction in the template is an HTML comment, because template
# instruction prose under a heading has already become a paper's Funding
# statement once. "Is this written yet" is answered by comparing against the
# pristine template, never by a heuristic.

PROTOCOL_SECTIONS = ["The Question", "Inclusion Criteria",
                     "Exclusion Criteria", "Date Window", "Venues",
                     "Languages", "Sources", "Queries", "Corpus Floor",
                     "Verification Window"]

# The three the engine refuses to build a corpus without (spec 10). Not a
# taste: *everything about X* is not a scope, it produces four thousand hits,
# the engine keeps whichever came back first, and the review written from
# that is a summary of one index's ranking.
PROTOCOL_REQUIRED = ["The Question", "Inclusion Criteria",
                     "Exclusion Criteria"]

PROTOCOL_TEMPLATE = """\
# Review Protocol

<!-- THE SCOPE CONTRACT. Written once, with the user, and never generated
     over. Nothing in this engine builds a corpus until the three starred
     sections below say something.

     Every instruction in this file is an HTML comment, so that nothing here
     can be mistaken for an answer. A section still holding only its comment
     reads as UNWRITTEN, because "is this written yet" is answered by
     comparing against the pristine template. -->

## The Question *

<!-- One sentence. What this review answers, narrowly enough that a paper is
     either in or out of it. "Everything about self-assembled monolayers" is
     not a question; "what limits the thermal stability of alkanethiol SAMs
     on Au(111) in solution" is. -->

## Inclusion Criteria *

<!-- One per line, starting with `- `. A candidate has to meet all of them. -->

## Exclusion Criteria *

<!-- One per line, as `- code: what it means`. The CODE is what screened.csv
     records and what `prisma` counts by, so it has to be short and stable:

       - no-au: not on gold
       - no-data: no measured stability data
       - lang: not in a language anyone here reads

     PRISMA counts exclusions BY REASON, and a free-text reason cannot be
     counted. This list is the closed set; `screen` refuses a reason that is
     not on it. -->

## Date Window

<!-- `from: 1983` and `to: 2026` on their own lines, or leave it open. -->

## Venues

<!-- Journals or conferences, one per line, if the scope names any. An empty
     section means no venue restriction, which is normal. -->

## Languages

<!-- One per line. Empty means no restriction - and note that an unstated
     language restriction is still a restriction, it is just one nobody can
     report. -->

## Sources

<!-- Which indexes were queried, one per line: pubmed, crossref, openalex,
     europepmc, arxiv. Empty means all of them, which is the right default
     for a review: Europe PMC is deliberately OUT of the default merge as a
     PubMed superset, which is right for a research-paper search and wrong
     here, where its preprints and non-PubMed tail are precisely what a
     review must not miss. -->

## Queries

<!-- The search strings, one per line. Each one is run against every source
     in `## Sources` and every page is appended to searches.jsonl. Write the
     ones you actually ran, including the ones that found nothing: a query
     that returned nothing is how a negative claim is earned. -->

## Corpus Floor

<!-- `floor: N` - the number of included papers below which this review
     should not be drafted. YOUR number, not the engine's. "The twelve papers
     that have ever used this technique" is a complete and correct scope, and
     a toolkit that refuses it has invented a threshold. Leave it out and the
     engine reports the size without a verdict. -->

## Verification Window

<!-- `days: 180` - how old a verification verdict may be before `verify`
     re-checks it. Retraction status is NEVER cached and is re-checked for
     every entry regardless of this. -->
"""


def _split_sections(text: str) -> dict:
    """`## Heading` -> its body, with the leading `# Title` dropped."""
    out: dict = {}
    current = None
    buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.*?)\s*\*?\s*$", line)
        if m:
            if current is not None:
                out[current] = "\n".join(buf).strip()
            current = m.group(1).strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf).strip()
    return out


def _strip_comments(body: str) -> str:
    return re.sub(r"<!--.*?-->", "", body, flags=re.S).strip()


def _bullets(body: str) -> list[str]:
    return [re.sub(r"^[-*]\s+", "", l).strip()
            for l in _strip_comments(body).splitlines()
            if l.strip().startswith(("-", "*"))]


def _kv_lines(body: str) -> dict:
    out = {}
    for line in _strip_comments(body).splitlines():
        m = re.match(r"^([A-Za-z_]+)\s*:\s*(.*)$", line.strip())
        if m:
            out[m.group(1).lower()] = m.group(2).strip()
    return out


def read_protocol(project: str) -> dict:
    """The scope contract, parsed, plus which sections are still unwritten.

    A section is unwritten when everything in it is an HTML comment - which
    is what the pristine template is made of. This is the comparison rule,
    not a heuristic about length: a scaffolded stub reported as filled in is
    a failure this toolkit has already paid for.
    """
    path = protocol_path(project)
    raw = _read(path)
    out: dict = {"path": path, "exists": bool(raw), "written": [],
                 "unwritten": [], "unknown_sections": []}
    if not raw:
        out["unwritten"] = list(PROTOCOL_SECTIONS)
        out["question"] = ""
        out["inclusion"] = []
        out["exclusion"] = {}
        out["queries"] = []
        out["sources"] = []
        out["venues"] = []
        out["languages"] = []
        out["year_from"] = out["year_to"] = ""
        out["floor"] = None
        out["verify_days"] = None
        return out

    secs = _split_sections(raw)
    for name in PROTOCOL_SECTIONS:
        body = secs.get(name, "")
        (out["written"] if _strip_comments(body) else
         out["unwritten"]).append(name)
    out["unknown_sections"] = [k for k in secs if k not in PROTOCOL_SECTIONS]

    out["question"] = " ".join(_strip_comments(secs.get("The Question", ""))
                               .split())
    out["inclusion"] = _bullets(secs.get("Inclusion Criteria", ""))

    # The exclusion list is a MAPPING of code -> meaning, because PRISMA
    # counts by reason and a reason nobody can count is a reason nobody can
    # report. A bullet with no code is kept, keyed by itself, rather than
    # dropped - losing a criterion silently is worse than an ugly code.
    exclusion: dict = {}
    for b in _bullets(secs.get("Exclusion Criteria", "")):
        m = re.match(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$", b)
        if m:
            exclusion[m.group(1)] = m.group(2).strip()
        elif b:
            exclusion[re.sub(r"[^a-z0-9]+", "-", b.lower())[:24]] = b
    out["exclusion"] = exclusion

    window = _kv_lines(secs.get("Date Window", ""))
    out["year_from"] = window.get("from", "")
    out["year_to"] = window.get("to", "")
    out["venues"] = [l.strip() for l in
                     _strip_comments(secs.get("Venues", "")).splitlines()
                     if l.strip()]
    out["languages"] = [l.strip() for l in
                        _strip_comments(secs.get("Languages", "")).splitlines()
                        if l.strip()]
    out["sources"] = [re.sub(r"^[-*]\s+", "", l).strip().lower()
                      for l in
                      _strip_comments(secs.get("Sources", "")).splitlines()
                      if l.strip()]
    out["queries"] = [re.sub(r"^[-*]\s+", "", l).strip()
                      for l in
                      _strip_comments(secs.get("Queries", "")).splitlines()
                      if l.strip()]

    floor = _kv_lines(secs.get("Corpus Floor", "")).get("floor", "")
    out["floor"] = int(floor) if floor.isdigit() else None
    days = _kv_lines(secs.get("Verification Window", "")).get("days", "")
    out["verify_days"] = int(days) if days.isdigit() else None
    return out


def protocol(project: str, init: bool = False) -> dict:
    """Report the scope contract, or scaffold one. The gate on `search`."""
    res = read_protocol(project)
    res["created"] = False
    if init:
        if res["exists"]:
            res["refused"] = (
                "protocol.md already exists and is never generated over. It "
                "is the one file in the corpus that is yours: the moment a "
                "tool overwrites the artifact its owner has been editing, "
                "nobody trusts it again. Edit it, or delete it deliberately "
                "and run --init again.")
            return res
        _write(res["path"], PROTOCOL_TEMPLATE)
        _write(os.path.join(corpus_dir(project), "README.md"), CORPUS_README)
        os.makedirs(papers_dir(project), exist_ok=True)
        os.makedirs(os.path.join(corpus_dir(project), "pdfs"), exist_ok=True)
        res = read_protocol(project)
        res["created"] = True

    missing = [s for s in PROTOCOL_REQUIRED if s in res["unwritten"]]
    res["missing_required"] = missing
    res["can_build"] = not missing
    if missing:
        res["refused"] = (
            "a topic with no protocol does not build a corpus. "
            + ", ".join(missing) + " " +
            ("is" if len(missing) == 1 else "are") + " still unwritten, and "
            "without them the sweep keeps whichever four thousand hits came "
            "back first - which makes the review a summary of one index's "
            "ranking. Write those three, or ask for a draft of them from the "
            "topic and correct it; a refusal that gets worked around is "
            "worse than a missing feature.")
    elif not res["queries"]:
        res["can_search"] = False
        res["note"] = ("the scope is settled but `## Queries` is empty, so "
                       "there is nothing to run. Every query goes in that "
                       "section, including the ones that found nothing: a "
                       "query that returned nothing is how a negative claim "
                       "is earned.")
    if "can_search" not in res:
        res["can_search"] = bool(res["can_build"] and res["queries"])
    return res


CORPUS_README = """\
# data/corpus/ - the review's data

GENERATED. For a research paper `data/` holds what was measured; for a review
it holds what the literature says, and every characterization in the text is
checked against it exactly as every number in a research paper is checked
against `analysis.md`.

| file | what it is | who writes it |
|---|---|---|
| `protocol.md` | the scope contract: the question, the criteria, the window, the queries, the floor | **you**, once. Never generated over |
| `searches.jsonl` | append-only, one line per query per page | `review.py search` |
| `screened.csv` | every candidate ever seen, and why each was excluded | `review.py search` / `screen` |
| `papers/<key>.md` | one record per included paper, named by citekey | `review.py record` / `adopt` |
| `pdfs/` | what you dropped in, or `pubmed.py pdf` retrieved | you, and the engine |
| `synthesis.md` | the evidence table | GENERATED by `review.py synthesis` |
| `.corpus_state.json` | content hashes; staleness, and the `read_tier` guard | GENERATED |

**What does not belong here:** prose. Nothing in this folder is drafted from.
`papers/` records what a source says; `plan/outline.md` records what this
review argues; and `attribution-check` is blind to the second while it reads
the first.

**No row is ever deleted from `screened.csv`.** The exclusions are the audit -
"we looked at it and said no" is the thing a reviewer asks about - and a
candidate excluded at screening and included later keeps both entries with
their dates.
"""


# ---------------------------------------------------------------------------
# screened.csv - every candidate ever seen
# ---------------------------------------------------------------------------

def read_screened(project: str) -> list[dict]:
    """Every row, in the order written. Append-only: this is a LOG."""
    path = screened_path(project)
    if not os.path.isfile(path):
        return []
    with io.open(path, "r", encoding="utf-8", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def screened_state(rows: list[dict]) -> dict:
    """The CURRENT decision per key - the last row that decided one.

    The log keeps every decision ever made; this is what the corpus looks
    like now. Include-after-exclude keeps both entries and this returns the
    include, which is why the log is read forward rather than reversed.
    """
    out: dict = {}
    for r in rows:
        key = (r.get("key") or "").strip()
        if not key:
            continue
        if key not in out:
            out[key] = dict(r)
        elif (r.get("decision") or "").strip():
            out[key] = dict(r)
    return out


def append_screened(project: str, rows: list[dict]) -> int:
    """Add rows. Never rewrites, never deduplicates an existing key away."""
    if not rows:
        return 0
    path = screened_path(project)
    fresh = not os.path.isfile(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SCREENED_FIELDS,
                           extrasaction="ignore", lineterminator="\n")
        if fresh:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in SCREENED_FIELDS})
    return len(rows)


# ---------------------------------------------------------------------------
# Citekeys, and the deduplication the merge already does
# ---------------------------------------------------------------------------

def clean_doi(doi: str) -> str:
    """scholar.py's own cleaner, in one line.

    A SECOND normalizer that disagrees with the first by one edge case
    produces a corpus with a duplicate in it and a count that says there is
    not, so this is deliberately the same shape as the one in scholar.py and
    `tests/review.py` holds the two against each other.
    """
    d = (doi or "").strip().lower()
    d = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:\s*", "", d)
    return d.rstrip(".").strip()


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def citekey(record: dict) -> str:
    """`ulman1996formation` - first author, year, first content word.

    The citekey is the FILENAME because the citekey is what the prose
    carries, so a claim in the draft resolves to a path without a lookup
    table.
    """
    authors = record.get("authors") or []
    first = ""
    if authors:
        first = re.sub(r"[^A-Za-z]", "", str(authors[0]).split()[0]).lower()
    year = re.sub(r"[^0-9]", "", str(record.get("year") or ""))[:4]
    stop = {"the", "a", "an", "of", "on", "in", "and", "for", "to", "with",
            "from", "at", "by", "is", "are"}
    word = ""
    for w in re.findall(r"[A-Za-z]+", record.get("title") or ""):
        if w.lower() not in stop and len(w) > 2:
            word = w.lower()
            break
    key = f"{first or 'anon'}{year or '0000'}{word or 'untitled'}"
    return re.sub(r"[^a-z0-9]", "", key)


# ---------------------------------------------------------------------------
# The paper record
# ---------------------------------------------------------------------------

RECORD_FIELDS = ["doi", "pmid", "arxiv", "title", "authors", "year",
                 "journal", "verified", "retracted", "retraction_checked",
                 "source_kind", "read_tier", "read_on", "retrieved",
                 "found_by", "pdf"]


def record_path(project: str, key: str) -> str:
    return os.path.join(papers_dir(project), f"{key}.md")


def parse_record(text: str) -> dict:
    """`key: value` lines under the `# citekey` heading, then the sections."""
    out: dict = {"sections": {}}
    m = re.match(r"^#\s+(\S+)", text.strip())
    if m:
        out["key"] = m.group(1)
    head = text.split("\n##", 1)[0]
    for line in head.splitlines():
        km = re.match(r"^([a-z_]+)\s*:\s*(.*)$", line)
        if km:
            out[km.group(1)] = km.group(2).split("#", 1)[0].strip()
    out["sections"] = _split_sections(text)
    return out


def read_record(project: str, key: str) -> dict | None:
    raw = _read(record_path(project, key))
    if not raw:
        return None
    rec = parse_record(raw)
    rec["key"] = rec.get("key") or key
    rec["path"] = record_path(project, key)
    rec["raw"] = raw
    return rec


def all_records(project: str) -> list[dict]:
    d = papers_dir(project)
    if not os.path.isdir(d):
        return []
    out = []
    for name in sorted(os.listdir(d)):
        if not name.endswith(".md"):
            continue
        rec = read_record(project, name[:-3])
        if rec:
            out.append(rec)
    return out


def retrieved_text(rec: dict) -> str:
    """Everything this record actually holds from the source.

    This is what `attribution-check` is GIVEN and what the tier rule is
    checked against. It is the record's own sections and nothing else: the
    review's thesis is not in here, and must not be.
    """
    secs = rec.get("sections") or {}
    return "\n".join(secs.get(name, "") for name in
                     ("What it claims", "What it reports", "Quotes"))


def render_record(rec: dict) -> str:
    """One record, written the way every record is written."""
    key = rec.get("key") or "unknown"
    lines = [f"# {key}", ""]
    for field in RECORD_FIELDS:
        val = rec.get(field)
        if val in (None, "", []):
            continue
        if isinstance(val, list):
            val = ", ".join(str(v) for v in val)
        lines.append(f"{field}: {val}")
    lines.append("")
    secs = rec.get("sections") or {}
    for name in RECORD_SECTIONS:
        lines.append(f"## {name}")
        lines.append("")
        if name == "Limits of this record":
            # Written by the ENGINE, not by a person: it is the tier's own
            # sentence, so whoever opens the file sees what the record does
            # not license before they see what it says.
            lines.append(TIER_LIMITS.get(str(rec.get("read_tier") or "title"),
                                         TIER_LIMITS["title"]))
        else:
            body = (secs.get(name) or "").strip()
            lines.append(body if body else
                         "<!-- not written yet -->")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def record_cmd(project: str, key: str, tier: str = "", **fields) -> dict:
    """Create or update one paper record. THE ONLY WRITER OF read_tier.

    One writer, because the tier is the field the layer-0 rule turns on and
    a field two callers can raise is a field nobody can audit. Every raise
    is stamped into .corpus_state.json with the retrieval event that earned
    it, and `coverage` reports a tier that rose without one.
    """
    res: dict = {"key": key, "changes": [], "errors": []}
    if not key:
        res["errors"].append("a record needs a citekey; it is the filename")
        return res
    if tier and tier not in TIERS:
        res["errors"].append(
            f"{tier!r} is not a read tier. They are {', '.join(TIERS)}, and "
            f"they are ranked: what a sentence may say about this source is "
            f"decided by this field.")
        return res
    kind = fields.get("source_kind")
    if kind and kind not in SOURCE_KINDS:
        res["errors"].append(f"{kind!r} is not a source kind. They are "
                             f"{', '.join(SOURCE_KINDS)}.")
        return res
    fb = fields.get("found_by")
    if fb and fb not in FOUND_BY:
        res["errors"].append(f"{fb!r} is not a `found_by`. They are "
                             f"{', '.join(FOUND_BY)}.")
        return res

    existing = read_record(project, key)
    rec: dict = dict(existing) if existing else {"key": key, "sections": {}}
    rec.pop("raw", None)
    rec.pop("path", None)
    before_tier = str(rec.get("read_tier") or "")

    for name, val in fields.items():
        if val in (None, ""):
            continue
        if name in ("claims", "reports", "quotes", "uses"):
            heading = {"claims": "What it claims", "reports": "What it reports",
                       "quotes": "Quotes",
                       "uses": "What this review uses it for"}[name]
            rec.setdefault("sections", {})[heading] = str(val)
            res["changes"].append({"field": heading, "action": "set"})
        else:
            rec[name] = val
            res["changes"].append({"field": name, "action": "set"})

    if tier:
        rec["read_tier"] = tier
        rec["read_on"] = fields.get("read_on") or TODAY
        if before_tier and TIER_RANK.get(tier, 0) > TIER_RANK.get(before_tier,
                                                                  0):
            res["changes"].append({
                "field": "read_tier", "action": "raise",
                "detail": f"{before_tier} -> {tier}; what the prose may say "
                          f"about this source widens with it"})
        elif before_tier != tier:
            res["changes"].append({"field": "read_tier", "action": "set",
                                   "detail": f"{before_tier or '(none)'} -> "
                                             f"{tier}"})
    rec.setdefault("read_tier", "title")
    rec.setdefault("source_kind", "primary")
    rec.setdefault("found_by", "query")

    text = render_record(rec)
    _write(record_path(project, key), text)
    res["path"] = record_path(project, key)
    res["read_tier"] = rec["read_tier"]

    # The tier guard. A tier that rose is stamped with what earned it; a
    # record whose tier rose with no retrieval event behind it is reported
    # by `coverage`, because read_tier inflated by hand is the one way this
    # whole ledger becomes decorative.
    # `--tier` PASSED is the retrieval event, whether or not the value
    # changed. Re-asserting a tier through this command is how a record
    # whose tier was raised by hand gets put right, and a guard that only
    # cleared on a CHANGE left that record reported forever with no way to
    # answer it - which is a refusal that has to be worked around, and those
    # are worse than a missing feature.
    stamp_state(project, key, text, rec["read_tier"], bool(tier))
    return res


# ---------------------------------------------------------------------------
# .corpus_state.json - staleness, and the read_tier guard
# ---------------------------------------------------------------------------

def read_state(project: str) -> dict:
    raw = _read(state_path(project))
    if not raw:
        return {"records": {}}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # A state file that cannot be read is not an empty state file. It is
        # reported, and the folder is believed over it.
        return {"records": {}, "unreadable": True}
    data.setdefault("records", {})
    return data


def stamp_state(project: str, key: str, text: str, tier: str,
                by_retrieval: bool) -> None:
    state = read_state(project)
    entry = state["records"].get(key) or {}
    entry["hash"] = _hash(text)
    entry["tier"] = tier
    entry["stamped"] = TODAY
    if by_retrieval:
        entry["tier_event"] = {"tier": tier, "on": TODAY, "by": "record"}
    state["records"][key] = entry
    state["updated"] = TODAY
    _write(state_path(project),
           json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def stale_records(project: str) -> list[dict]:
    """Records whose content no longer matches the hash that was stamped.

    A CONTENT hash, never a modification time: OneDrive rewrites mtimes on
    sync, so a touched file with identical content is not stale.
    """
    state = read_state(project)
    out = []
    for rec in all_records(project):
        key = rec["key"]
        stamped = (state["records"].get(key) or {})
        if not stamped:
            out.append({"key": key, "why": "never stamped"})
            continue
        if stamped.get("hash") != _hash(rec["raw"]):
            out.append({"key": key, "why": "edited since it was stamped"})
        elif (stamped.get("tier") and
              stamped["tier"] != str(rec.get("read_tier") or "")):
            out.append({"key": key, "why": "tier changed outside `record`"})
    return out


def hand_raised_tiers(project: str) -> list[dict]:
    """A tier that rose with no retrieval event behind it (spec 16.8)."""
    state = read_state(project)
    out = []
    for rec in all_records(project):
        entry = state["records"].get(rec["key"]) or {}
        tier = str(rec.get("read_tier") or "title")
        if TIER_RANK.get(tier, 0) <= TIER_RANK["title"]:
            continue
        event = entry.get("tier_event") or {}
        if event.get("tier") != tier:
            out.append({
                "key": rec["key"], "tier": tier,
                "detail": "this record claims a tier that `review.py record` "
                          "never wrote. read_tier is the field the layer-0 "
                          "rule turns on, so a tier raised by hand licenses "
                          "sentences nobody retrieved anything for."})
    return out


# ---------------------------------------------------------------------------
# search - the sweep, recorded as it runs
# ---------------------------------------------------------------------------

def _scholar(args: list[str], timeout: int = 900) -> tuple[dict | None, str]:
    """Run scholar.py and read its JSON back.

    Through a subprocess rather than an import, so this engine stays
    standard-library only and a machine with no `requests` gets a sentence
    about `requests` instead of an ImportError at startup.
    """
    engine = os.path.join(HERE, "scholar.py")
    if not os.path.isfile(engine):
        return None, "tools/scholar.py is missing"
    try:
        run = subprocess.run([sys.executable, engine, *args, "--json"],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"scholar.py did not finish within {timeout}s"
    if not (run.stdout or "").strip():
        return None, (run.stderr or "scholar.py wrote nothing").strip()[:300]
    try:
        return json.loads(run.stdout), ""
    except json.JSONDecodeError as exc:
        return None, f"scholar.py returned unparseable JSON: {exc}"


def log_search(project: str, entry: dict) -> None:
    """APPEND-ONLY, and never rewritten.

    Same reason reports/rN/run_log.md is: a state file is rewritten, and
    *what did we actually search, and when* needs an answer that nothing
    later overwrites. The log is written on every review kind, whatever
    `review_kind` says, because a narrative review a reviewer later asks to
    make systematic cannot reconstruct the searches it did not log - and the
    log is free at the moment the search runs and impossible afterwards.
    """
    _append(searches_path(project),
            json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def read_searches(project: str) -> list[dict]:
    out = []
    for line in _read(searches_path(project)).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"unparseable": line[:120]})
    return out


def search(project: str, queries: list[str] | None = None, pages: int = 2,
           limit: int = 50, kind: str = "query",
           sources: list[str] | None = None, dry_run: bool = False) -> dict:
    """Run the protocol's queries and record every page of every one."""
    proto = protocol(project)
    res: dict = {"project": project, "queries": [], "errors": [],
                 "added": 0, "seen": 0}
    if not proto["can_build"]:
        res["refused"] = proto["refused"]
        return res
    terms = queries or proto["queries"]
    if not terms:
        res["refused"] = proto.get("note") or "no queries to run"
        return res

    # A corpus sweep uses every source (spec 6). Europe PMC is out of the
    # default merge as a PubMed superset - right for a research-paper search,
    # wrong here, where its preprints and non-PubMed biomedical tail are
    # precisely what a review must not miss.
    # ...and what the author typed under `## Sources` is prose until it is
    # checked against the vocabulary scholar.py actually has. An index it does
    # not know - a name with a parenthetical after it, or an index this engine
    # cannot reach - used to be passed through and took every query down with
    # it. An unrecognised name is reported and dropped; if that leaves
    # nothing, the sweep is the whole-vocabulary one it would have been had
    # the section been empty, which is the floor the section can never fall
    # below.
    asked = [s for s in (sources or proto["sources"] or []) if s.strip()]
    use = [s for s in asked if s.lower() in SOURCE_VOCABULARY]
    unknown = [s for s in asked if s.lower() not in SOURCE_VOCABULARY]
    if unknown:
        res["unknown_sources"] = unknown
        res["errors"].append(
            "protocol.md ## Sources names "
            + ", ".join(unknown)
            + " which scholar.py does not have; searched "
            + (", ".join(use) if use else "every index")
            + " instead. The vocabulary is "
            + ", ".join(sorted(SOURCE_VOCABULARY)))
    use = use or ["all"]
    res["sources_used"] = list(use)
    known = screened_state(read_screened(project))
    new_rows: list[dict] = []
    fresh_keys: set[str] = set()

    for term in terms:
        argv = ["search", term, "--limit", str(limit), "--all",
                "--pages", str(pages), "--source", ",".join(use)]
        if proto["year_from"]:
            argv += ["--from", proto["year_from"]]
        if proto["year_to"]:
            argv += ["--to", proto["year_to"]]
        payload, err = _scholar(argv)
        if payload is None:
            res["errors"].append(f"{term}: {err}")
            res["queries"].append({"query": term, "error": err})
            continue

        hits = payload.get("results") or []
        added = 0
        for row in hits:
            key = citekey(row)
            dedup = clean_doi(row.get("doi", "")) or normalize_title(
                row.get("title", ""))
            if not dedup:
                continue
            if key in known or key in fresh_keys:
                continue
            fresh_keys.add(key)
            added += 1
            new_rows.append({
                "date": TODAY, "key": key,
                "doi": clean_doi(row.get("doi", "")),
                "title": (row.get("title") or "").replace("\n", " ").strip(),
                "year": row.get("year") or "",
                "source": ",".join(row.get("found_in") or []),
                "found_by": kind, "query": term,
                "decision": "", "reason": ""})

        entry = {"date": TODAY, "kind": kind, "terms": term,
                 "sources": payload.get("sources_tried") or [],
                 "pages_read": payload.get("pages_read") or {},
                 "filters": {"from": proto["year_from"],
                             "to": proto["year_to"]},
                 "per_source_limit": limit,
                 "examined": payload.get("returned") or 0,
                 "new": added,
                 "unreachable": payload.get("unreachable") or []}
        res["queries"].append(entry)
        res["seen"] += entry["examined"]
        res["added"] += added
        if not dry_run:
            log_search(project, entry)

    if not dry_run:
        append_screened(project, new_rows)
    res["undecided"] = len([r for r in
                            screened_state(read_screened(project)).values()
                            if not (r.get("decision") or "").strip()])
    # The ceiling is STATED rather than implied. Recall through keyless
    # indexes is not exhaustive, and a review that implies a completeness it
    # did not achieve is the review-shaped version of a wrong answer at
    # exit 0.
    res["ceiling"] = (
        "recall through keyless indexes is not exhaustive. protocol.md "
        "records which sources were queried and when; a systematic review's "
        "Methods reports both. Snowballing is where the rest of the tail is "
        "- `scholar.py references` and `cited-by` on this corpus's most "
        "central members, recorded here with --kind backward / forward.")
    return res


# ---------------------------------------------------------------------------
# screen - a decision, with a record
# ---------------------------------------------------------------------------

def screen(project: str, key: str = "", decision: str = "", reason: str = "",
           ) -> dict:
    """Report what is undecided, or record one decision."""
    proto = read_protocol(project)
    rows = read_screened(project)
    state = screened_state(rows)
    res: dict = {"project": project, "errors": [], "changes": []}

    if key:
        if decision not in DECISIONS:
            res["errors"].append(
                f"a decision is one of {', '.join(DECISIONS)}. `maybe` is a "
                f"real state and is reported as outstanding until it "
                f"resolves - a screening pass that leaves forty maybes has "
                f"not screened.")
            return res
        if key not in state:
            res["errors"].append(
                f"{key} is not in screened.csv. A candidate gets there by "
                f"being found: `review.py search`, or `adopt`.")
            return res
        if decision == "exclude":
            if not reason:
                res["errors"].append(
                    "an exclusion needs a reason code from protocol.md's own "
                    "`## Exclusion Criteria` list. PRISMA counts exclusions "
                    "BY REASON, and a free-text reason cannot be counted.")
                return res
            if reason not in proto["exclusion"]:
                res["errors"].append(
                    f"{reason!r} is not in protocol.md's exclusion list. It "
                    f"holds: {', '.join(sorted(proto['exclusion'])) or '(nothing)'}. "
                    f"Add the reason to the protocol first - the list is the "
                    f"closed set because the counts are derived from it.")
                return res
        prior = state[key]
        # No row is ever deleted, and include-after-exclude keeps BOTH
        # entries with their dates. The exclusions are the audit.
        append_screened(project, [{**prior, "date": TODAY, "key": key,
                                   "decision": decision, "reason": reason}])
        res["changes"].append({
            "key": key, "action": decision,
            "detail": (f"{prior.get('decision') or 'undecided'} -> "
                       f"{decision}" + (f" ({reason})" if reason else ""))})
        state = screened_state(read_screened(project))

    by = {d: [] for d in DECISIONS}
    undecided = []
    for k, row in sorted(state.items()):
        d = (row.get("decision") or "").strip()
        (by[d] if d in by else undecided).append(
            {"key": k, "title": row.get("title", ""),
             "year": row.get("year", ""), "reason": row.get("reason", "")})
    res["counts"] = {d: len(v) for d, v in by.items()}
    res["counts"]["undecided"] = len(undecided)
    res["undecided"] = undecided
    res["maybe"] = by["maybe"]
    res["included"] = by["include"]
    res["exclusion_codes"] = proto["exclusion"]
    res["outstanding"] = len(undecided) + len(by["maybe"])
    return res


# ---------------------------------------------------------------------------
# adopt - the input ladder's thin new rung (spec 10)
# ---------------------------------------------------------------------------

_BIB_ENTRY = re.compile(r"@(\w+)\s*\{\s*([^,]+),(.*?)\n\}", re.S)
_BIB_FIELD = re.compile(r"(\w+)\s*=\s*[{\"](.*?)[}\"]\s*,?\s*$", re.M | re.S)


def _parse_bib(text: str) -> list[dict]:
    out = []
    for m in _BIB_ENTRY.finditer(text):
        body = m.group(3)
        fields = {k.lower(): " ".join(v.split())
                  for k, v in _BIB_FIELD.findall(body)}
        authors = [a.strip() for a in
                   re.split(r"\s+and\s+", fields.get("author", ""))
                   if a.strip()]
        out.append({"key": m.group(2).strip(), "type": m.group(1).lower(),
                    "title": fields.get("title", ""),
                    "authors": authors, "year": fields.get("year", ""),
                    "doi": clean_doi(fields.get("doi", "")),
                    "journal": fields.get("journal", "")})
    return out


def adopt(project: str, bib: str = "", pdfs: str = "", ideas: str = "",
          found_by: str = "handpicked") -> dict:
    """A .bib, a folder of PDFs, or an `Ideas/` folder becomes records.

    Every rung of the input ladder is a mechanism that already exists; this
    is the thin part that turns one into corpus rows. Nothing here VERIFIES
    anything - `review.py verify` does that, and a record with no `verified:`
    line licenses no sentence at all.
    """
    res: dict = {"project": project, "adopted": [], "errors": [],
                 "skipped": []}
    candidates: list[dict] = []

    if bib:
        if not os.path.isfile(bib):
            res["errors"].append(f"no such file: {bib}")
            return res
        for e in _parse_bib(_read(bib)):
            candidates.append({**e, "tier": "title", "found_by": found_by,
                               "origin": os.path.basename(bib)})

    if ideas:
        # An `Ideas/` folder is a corpus SEED: idea.py's explore-mode summary
        # and its refs.bib are exactly the shape this wants, and the moment
        # the user says "write a review of X" the answer is a project
        # directory rather than a second scratch folder. A corpus built twice
        # is a corpus whose second build silently drops the rows the first
        # one had.
        if not os.path.isdir(ideas):
            res["errors"].append(f"no such folder: {ideas}")
            return res
        found = False
        for root, _, names in os.walk(ideas):
            for name in names:
                if name.endswith(".bib"):
                    found = True
                    for e in _parse_bib(_read(os.path.join(root, name))):
                        candidates.append({**e, "tier": "title",
                                           "found_by": found_by,
                                           "origin": name})
        if not found:
            res["errors"].append(
                f"{ideas} holds no .bib, so there is nothing verified in it "
                f"to adopt. idea.py write --mode explore produces one; a "
                f"summary with no reference list is a recollection.")

    if pdfs:
        if not os.path.isdir(pdfs):
            res["errors"].append(f"no such folder: {pdfs}")
            return res
        dest = os.path.join(corpus_dir(project), "pdfs")
        os.makedirs(dest, exist_ok=True)
        for name in sorted(os.listdir(pdfs)):
            if not name.lower().endswith(".pdf"):
                continue
            stem = re.sub(r"[^a-z0-9]", "", os.path.splitext(name)[0].lower())
            candidates.append({
                "key": stem or "unknownpdf", "title": os.path.splitext(name)[0],
                "authors": [], "year": "", "doi": "",
                # A PDF in hand is the one case where the highest tier is
                # honest on arrival: the full text is sitting there. What it
                # does NOT do is verify the paper is real, which is a
                # different question and still has to be answered.
                "tier": "pdf", "found_by": found_by,
                "pdf": os.path.join("pdfs", name), "origin": name})

    seen = screened_state(read_screened(project))
    rows = []
    for cand in candidates:
        key = cand.get("key") or citekey(cand)
        key = re.sub(r"[^A-Za-z0-9_-]", "", key) or "unknown"
        if read_record(project, key):
            res["skipped"].append({"key": key, "why": "already a record"})
            continue
        out = record_cmd(
            project, key, tier=cand["tier"], title=cand.get("title", ""),
            authors=", ".join(cand.get("authors") or []),
            year=cand.get("year", ""), doi=cand.get("doi", ""),
            journal=cand.get("journal", ""),
            found_by=cand.get("found_by", found_by),
            pdf=cand.get("pdf", ""))
        if out["errors"]:
            res["errors"] += out["errors"]
            continue
        res["adopted"].append({"key": key, "tier": cand["tier"],
                               "from": cand.get("origin", "")})
        if key not in seen:
            rows.append({"date": TODAY, "key": key,
                         "doi": cand.get("doi", ""),
                         "title": cand.get("title", ""),
                         "year": cand.get("year", ""),
                         "source": "adopted", "found_by": found_by,
                         "query": cand.get("origin", ""),
                         "decision": "include", "reason": ""})
    append_screened(project, rows)
    res["note"] = ("adopted records carry no `verified:` line, and a record "
                   "with no verdict licenses no sentence. Run "
                   "`review.py verify` next.")
    return res


# ---------------------------------------------------------------------------
# verify - cached, incremental, and never cached for retraction
# ---------------------------------------------------------------------------

def _verdict_age(verified: str) -> int | None:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", verified or "")
    if not m:
        return None
    try:
        then = datetime.date.fromisoformat(m.group(1))
    except ValueError:
        return None
    return (datetime.date.today() - then).days


def verify(project: str, recheck_all: bool = False,
           window: int | None = None, limit: int = 0) -> dict:
    """Re-verify what needs it, and re-check retraction for everything.

    Measured: verification runs 5.9 s per reference end to end, so a
    200-reference bibliography is about 20 minutes of wall clock for one
    full pass. Twenty minutes every round is what makes people stop running
    it, so the verdict is cached with the record and this pass is
    incremental - and it REPORTS how many verdicts it reused and how old the
    oldest one is, because *checked* against a six-month-old cache is a
    different claim from *checked today*, and the difference is a retraction
    nobody saw.

    Retraction status is never cached. A retraction appears AFTER the
    verdict; it is one field per record and it is the one that cannot wait.
    """
    proto = read_protocol(project)
    days = window if window is not None else (proto["verify_days"] or 180)
    recs = all_records(project)
    res: dict = {"project": project, "checked": [], "reused": 0,
                 "errors": [], "retracted": [], "window_days": days}
    if not recs:
        res["note"] = "no records to verify"
        return res

    ages = []
    todo = []
    for rec in recs:
        age = _verdict_age(rec.get("verified", ""))
        if age is not None:
            ages.append(age)
        stale = (recheck_all or not rec.get("verified")
                 or age is None or age > days)
        if stale:
            todo.append(rec)
        else:
            res["reused"] += 1
    res["oldest_reused_days"] = max(ages) if ages else None

    if limit:
        res["deferred"] = max(0, len(todo) - limit)
        todo = todo[:limit]

    for rec in todo:
        ident = rec.get("doi") or rec.get("pmid") or ""
        argv = ["verify"]
        if rec.get("doi"):
            argv += ["--doi", rec["doi"]]
        elif rec.get("pmid"):
            argv += ["--pmid", rec["pmid"]]
        elif rec.get("title"):
            argv += ["--title", rec["title"]]
        else:
            res["errors"].append(
                f"{rec['key']}: no DOI, PMID or title to verify against. A "
                f"record with nothing to look up is a recollection.")
            continue
        payload, err = _scholar(argv, timeout=180)
        if payload is None:
            # A source that did not answer is not a paper that does not
            # exist, and the two must never be written into the same field.
            res["errors"].append(f"{rec['key']}: {err}")
            continue
        status = payload.get("status", "unknown")
        src = payload.get("source", "")
        conf = payload.get("confidence")
        match = payload.get("match") or {}
        fields: dict = {
            "verified": f"{TODAY} via {src or 'unknown'} "
                        f"(confidence {conf if conf is not None else '?'}, "
                        f"{status})",
            "retraction_checked": TODAY,
            "retracted": "true" if match.get("is_retracted") else "false"}
        if match.get("doi") and not rec.get("doi"):
            fields["doi"] = match["doi"]
        if match.get("pmid") and not rec.get("pmid"):
            fields["pmid"] = match["pmid"]
        record_cmd(project, rec["key"], **fields)
        res["checked"].append({"key": rec["key"], "status": status,
                               "source": src, "id": ident})
        if match.get("is_retracted"):
            res["retracted"].append({"key": rec["key"], "doi": ident})

    res["reuse_note"] = (
        f"{res['reused']} verdict(s) reused"
        + (f", oldest {res['oldest_reused_days']} days"
           if res["oldest_reused_days"] is not None else "")
        + f"; {len(res['checked'])} re-checked against the sources. "
          f"`--all` re-checks everything, which is the pre-submission pass.")
    return res


# ---------------------------------------------------------------------------
# synthesis - GENERATED from papers/
# ---------------------------------------------------------------------------

def synthesis(project: str, write: bool = True) -> dict:
    """papers/ -> synthesis.md, the evidence table.

    Generated, and said to be, for the reason analysis.md stopped being
    prose: a hand-written evidence table drifts from papers/ inside two
    rounds and nothing says which is current. A corrected record fixes the
    table on the next render, which is the property that makes a 40-row
    comparison table maintainable at all.
    """
    recs = all_records(project)
    rows = []
    for rec in recs:
        rows.append({
            "key": rec["key"],
            "year": rec.get("year", ""),
            "source_kind": rec.get("source_kind", ""),
            "read_tier": rec.get("read_tier", "title"),
            "found_by": rec.get("found_by", ""),
            "verified": bool(rec.get("verified")),
            "retracted": str(rec.get("retracted", "")).lower() == "true",
            "claims": " ".join((rec.get("sections") or {})
                               .get("What it claims", "").split())[:240],
            "reports": " ".join((rec.get("sections") or {})
                                .get("What it reports", "").split())[:240],
        })
    rows.sort(key=lambda r: (r["year"] or "0000", r["key"]))

    lines = ["# Synthesis", "",
             "GENERATED by `review.py synthesis` from `papers/`. Do not edit:",
             "the next render overwrites it, and a corrected paper record is",
             "how a row in here changes.", "",
             f"{len(rows)} record(s), regenerated {TODAY}.", "",
             "| citekey | year | kind | tier | found by | verified | "
             "what it claims |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        mark = "yes" if r["verified"] else "**NO**"
        if r["retracted"]:
            mark = "**RETRACTED**"
        lines.append(
            f"| {r['key']} | {r['year']} | {r['source_kind']} | "
            f"{r['read_tier']} | {r['found_by']} | {mark} | "
            f"{r['claims'] or '-'} |")
    lines += ["", "## What each tier licenses", ""]
    for tier in TIERS:
        n = len([r for r in rows if r["read_tier"] == tier])
        if n:
            lines.append(f"- **{tier}** ({n}): {TIER_LIMITS[tier]}")
    lines.append("")

    text = "\n".join(lines)
    out = {"project": project, "rows": rows, "count": len(rows),
           "path": synthesis_path(project)}
    if write:
        _write(synthesis_path(project), text)
        out["written"] = synthesis_path(project)
    out["text"] = text
    return out


# ---------------------------------------------------------------------------
# tier - the deterministic half of the layer-0 rule (spec 3, 4.2)
# ---------------------------------------------------------------------------

_CITEKEY_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_:.#$%&+?<>~/-]*)")
# --- SENTENCE BOUNDARY CONTRACT (verbatim in prose.py, review.py, docx_edits.py) ---
#
# A CITATION MAY SIT BETWEEN THE TERMINAL PUNCTUATION AND THE SPACE. ACS and
# Nature house style put the superscript after the period, and that is what an
# assembled .docx carries:
#
#     "...extends to residue 318.2,6 Within this extracellular region..."
#
# Every splitter in this toolkit required whitespace immediately after the
# period, so none of them broke there. Measured 2026-09-19 on a real r2 draft
# (specs/flow-and-conclusion-2026-09-19.md 4): prose.py saw 1 sentence where
# there are 3, review.py 1, docx_edits.py 2 of 3. The damage runs BOTH ways -
# merged paragraphs offer fewer joints, so the bridge and hand-off findings
# were UNDER-reported (given_new_gap 3 where it should be 9, tail_rank_last 0
# where it should be 3), while merged sentences are longer, so subject-verb
# gaps were OVER-reported at impossible values: a 68-word gap inside a draft
# whose longest real sentence is 40 words.
#
# A PLAIN SINGLE DIGIT AFTER A DIGIT IS NOT A CITATION, and that is the only
# case this pattern deliberately declines. `0.5 M NaCl` and `318.2 Within` are
# the same six characters; one is a decimal and one is a reference, and
# nothing in the text separates them. So a run of plain digits is read as a
# citation only when it carries a separator (`2,6`, `2-4`) or when the
# character before the period is not itself a digit (`as shown.4 The`).
# Superscripts are unambiguous and are always a citation.
#
# Three engines, one pattern, REPEATED RATHER THAN IMPORTED because each is
# standalone by design. Repetition is only safe while something compares the
# copies: tests/prose.py test_sentence_boundary_contract does, and names all
# three, so a fourth private copy fails rather than passes silently. What each
# engine keeps for itself is its ACCEPTANCE rule - which abbreviations end a
# sentence, what may open the next one - because those differed before this
# block existed and differ for reasons of their own.
_SUPERSCRIPT = "\u00b9\u00b2\u00b3\u2070\u2074\u2075\u2076\u2077\u2078\u2079"
_CITE_SEP = ",;\u2013\u2012-"
CITATION_RUN = (
    "(?:"
    "[" + _SUPERSCRIPT + "]+(?:[" + _CITE_SEP + "][" + _SUPERSCRIPT + "]+)*"
    "|[0-9]+(?:[" + _CITE_SEP + "][0-9]+)+"
    "|(?<![0-9][.!?])[0-9]+"
    ")?"
)
# The `cite` group always participates, empty or not, so `m.end("cite")` is
# always where the sentence's own text ends - a split taken on these offsets
# deletes nothing.
SENTENCE_BOUNDARY_RE = re.compile(
    "[.!?][\"\'\u201d\u2019)\\]]*(?P<cite>" + CITATION_RUN + ")\\s+")
# --- END SENTENCE BOUNDARY CONTRACT ------------------------------------------


# Kept for callers that only need "is there a boundary here". The SPLIT
# runs off SENTENCE_BOUNDARY_RE above, which permits the citation run
# this pattern cannot see.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[(])")
# What may open a sentence in a corpus record or a review's prose.
_OPENS_A_SENTENCE = re.compile(r"[A-Z\[(]")
_FLAG_RE = re.compile(r"\*\*\[FLAG[^\]]*\]\*\*")

# A number in prose. Deliberately not a bare integer inside a citation or a
# cross-reference: `Figure 2` and `[12]` are not claims about a source.
# The unit is optional and must be a WHOLE short token: `[A-Za-z]{1,4}`
# without a trailing guard turned `n = 6 replicates` into the number
# "6 repl", which is not a quantity anybody wrote and cannot be matched
# against a record. A lookahead that refuses a longer word is the
# difference.
_NUMBER_RE = re.compile(
    r"(?<![\w.])\d+(?:\.\d+)?"
    r"(?:\s*(?:%|°[CFK]?|[A-Za-zµΩ]{1,3}(?![A-Za-z])))?")

# What a sentence does when it goes past a title. These are the verbs that
# characterize somebody else's work, and a sentence with one plus a citekey
# is making a claim the record has to license.
_CHARACTERIZING = re.compile(
    r"\b(report(?:s|ed)?|observ(?:e|es|ed)|find(?:s)?|found|show(?:s|ed|n)?|"
    r"demonstrat(?:e|es|ed)|measur(?:e|es|ed)|conclud(?:e|es|ed)|"
    r"argu(?:e|es|ed)|propos(?:e|es|ed)|attribut(?:e|es|ed)|"
    r"determin(?:e|es|ed)|establish(?:es|ed)?|reveal(?:s|ed)?|"
    r"fail(?:s|ed)? to (?:consider|account|address)|"
    r"did not (?:consider|account for|address)|"
    r"limitation|control|sample size)\b", re.I)

# A claim about the CORPUS rather than about a paper (spec 3). This is the
# single most common sentence in a review's introduction and the one least
# often earned.
_NEGATIVE = re.compile(
    r"\b(?:no (?:study|studies|work|report|one|group)\b"
    r"|has not been (?:studied|examined|reported|investigated|measured)"
    r"|have not been (?:studied|examined|reported|investigated|measured)"
    r"|remains? (?:unexplored|unstudied|unknown|uncharacterized)"
    r"|to (?:our|the) knowledge,? no"
    r"|never been (?:studied|reported|examined))", re.I)


# What an abstract cannot license, even when the abstract was read: the
# source's n, its conditions, its controls, its limitations, what its figures
# show, or that it failed to consider anything (spec 3).
#
# Written as alternatives with their OWN boundaries rather than one group
# wrapped in \b...\b - a trailing \b after `n\s*=` needs a word character
# on the other side of the `=`, and `n = 6` has a space there, so the whole
# probe silently matched nothing. A detector wired to fire on nothing is the
# shape this toolkit exists to catch.
# Every stem needs NOUN CONTEXT, and that is item 75 rather than fussiness:
# `controls?` on its own fires on the ordinary verb - "hydrogen bonding
# controls the packing" is a sentence about chemistry, not about somebody's
# experimental controls - and `replicates?` does the same ("the finding
# replicates"). A determiner, a possessive, a number or an adjective in front
# of the noun is what separates the two, and a check that cries wolf on clean
# work is the one an author learns to skim past the true finding of.
_BEYOND_ABSTRACT = re.compile(
    r"(?:\bn\s*="
    r"|\bsample size\b"
    r"|(?:\b(?:the|their|its|a|an|no|positive|negative|vehicle|untreated|"
    r"matched|internal|experimental|appropriate)\s+)controls?\b"
    r"|\b(?:the|their|its|study|these|those|stated|acknowledged)\s+"
    r"limitations?\b"
    r"|\bfailed to\b|\bdid not (?:consider|account|address)\b"
    r"|\btheir (?:figure|table)\b|\bFigure \d+ of\b"
    r"|(?:\b(?:\d+|the|their|technical|biological|independent)\s+)"
    r"replicates?\b"
    r"|\b(?:the|their|a|this|that|\d+)\s+cohort\b)", re.I)

# Inline markup and float cross-references, removed before any number is read
# out of a sentence. `Mg<sup>2+</sup>` contributed the number 2 and "Figure 1"
# contributed the number 1, and both were then demanded of the cited record -
# so a sentence could not name a figure or a charge state without being
# charged with a number nobody wrote (item 75).
# The CONTENT of a superscript or a subscript goes with the tag: the 2 in
# `Mg<sup>2+</sup>` and in `H<sub>2</sub>O` is a charge state and a
# stoichiometry, not a quantity anybody is attributing to a source.
_SUPSUB_RE = re.compile(r"<su[pb]>.*?</su[pb]>", re.I | re.S)
_INLINE_MARKUP_RE = re.compile(r"<[^>]{1,40}>")
_FLOAT_XREF_RE = re.compile(
    r"\b(?:Fig\.?|Figure|Tab\.?|Table|Scheme|Eq\.?|Equation|Section|Sec\.?)"
    r"\s*S?\d+[A-Za-z]?", re.I)
_BRACKET_CITE_RE = re.compile(r"\[\s*\d+(?:\s*[-,]\s*\d+)*\s*\]")


def strip_non_claim_numbers(text: str) -> str:
    """A sentence with the digits that are not claims about a source removed."""
    out = _SUPSUB_RE.sub(" ", text or "")
    out = _INLINE_MARKUP_RE.sub(" ", out)
    out = _FLOAT_XREF_RE.sub(" ", out)
    out = _BRACKET_CITE_RE.sub(" ", out)
    return out


def number_values(text: str) -> list[str]:
    """The NUMERALS in a text, normalized, with the unit held separately.

    Both sides of the comparison go through this. They did not before: the
    record was written by one hand and the prose by another, so a record
    holding `1.5 A` and prose written `1.5 angstrom` produced `1.5 A` and
    `1.5`, which do not compare equal - and a number that was verbatim in the
    record was reported as absent from it. Comparing numerals is deliberately
    weaker than comparing quantities, and weak in the safe direction: this
    check exists to catch a number that is NOWHERE in the record.
    """
    out: list[str] = []
    for m in _NUMBER_RE.finditer(text or ""):
        num = re.match(r"\d+(?:\.\d+)?", m.group(0).strip())
        if not num:
            continue
        val = num.group(0)
        if "." in val:
            val = val.rstrip("0").rstrip(".") or "0"
        if val not in out:
            out.append(val)
    return out

# Words that carry no subject matter, for deciding whether a recorded search
# is plausibly about a given negative claim.
_STOPWORDS = {
    "the", "a", "an", "of", "on", "in", "and", "or", "for", "to", "with",
    "from", "at", "by", "is", "are", "was", "were", "be", "been", "has",
    "have", "had", "no", "not", "this", "that", "these", "those", "it",
    "its", "as", "but", "than", "then", "there", "their", "we", "our",
    "study", "studies", "work", "report", "reported", "above", "below",
    "been", "examined", "known", "knowledge", "one", "group", "never",
    "remains", "remain", "unknown", "unexplored", "unstudied", "system",
}


def _content_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z]{3,}", (text or "").lower())
            if w not in _STOPWORDS}


def _sentences(text: str) -> list[str]:
    body = _FLAG_RE.sub(" ", text)
    body = re.sub(r"^#.*$", "", body, flags=re.M)          # headings
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)    # instructions
    out = []
    for para in re.split(r"\n\s*\n", body):
        para = " ".join(para.split())
        if not para:
            continue
        start = 0
        for m in SENTENCE_BOUNDARY_RE.finditer(para):
            nxt = para[m.end():m.end() + 1]
            if not nxt or not _OPENS_A_SENTENCE.match(nxt):
                continue
            piece = para[start:m.end("cite")].strip()
            if piece:
                out.append(piece)
            start = m.end()
        tail = para[start:].strip()
        if tail:
            out.append(tail)
    return out


def _numbers_in(text: str) -> list[str]:
    return [m.group(0).strip() for m in _NUMBER_RE.finditer(text)]


def _source_text_dir(project: str, sections: str = "") -> str:
    if sections:
        return sections
    base = os.path.join(project, "drafts")
    if not os.path.isdir(base):
        return ""
    # The round-labelled folder, newest first, then the unlabelled one.
    cands = sorted((n for n in os.listdir(base)
                    if n.startswith("source_text")), reverse=True)
    for name in cands:
        path = os.path.join(base, name)
        if os.path.isdir(path):
            return path
    return ""


def tier_check(project: str, sections: str = "") -> dict:
    """Every characterizing sentence against the tier of the record it cites.

    The countable half of spec 3, and the repointing of spec 4.2: a review is
    allowed MORE numbers than a research paper and is held to a STRICTER
    account for each one. That is not a trade; it is the same principle in
    both places. The budget was always about the reader's attention, and the
    ledger was always about the truth.
    """
    res: dict = {"project": project, "findings": [], "sentences": 0,
                 "citing_sentences": 0, "errors": []}
    src = _source_text_dir(project, sections)
    if not src or not os.path.isdir(src):
        res["errors"].append(
            "no drafted sections to check. This runs over "
            "drafts/source_text*/; pass --sections to point it elsewhere.")
        return res
    res["source_text"] = src

    records = {r["key"]: r for r in all_records(project)}
    searches = read_searches(project)
    res["records"] = len(records)
    missing: dict = {}
    unverified: dict = {}

    for name in sorted(os.listdir(src)):
        if not name.endswith(".md"):
            continue
        text = _read(os.path.join(src, name))
        for sentence in _sentences(text):
            res["sentences"] += 1
            keys = _CITEKEY_RE.findall(sentence)

            if _NEGATIVE.search(sentence):
                # A negative claim is a claim about the CORPUS, not about a
                # paper: it is licensed by protocol.md plus a recorded
                # search that WOULD HAVE FOUND the thing, never by a gap in
                # the drafter's memory.
                #
                # "would have found it" is approximated by subject-matter
                # overlap with a query that was actually run - two content
                # words, which is enough to separate *nobody has measured
                # the desorption temperature* from a sweep about something
                # else entirely. It is an approximation and it is stated as
                # one in the finding: what it cannot do is pass a negative
                # claim that no search in this project goes anywhere near.
                want = _content_words(sentence)
                support = None
                for entry in searches:
                    terms = str(entry.get("terms") or "")
                    if len(want & _content_words(terms)) >= 2:
                        support = terms
                        break
                if support is None:
                    res["findings"].append({
                        "kind": "corpus", "section": name,
                        "sentence": sentence[:200],
                        "detail": (
                            "a negative claim with no recorded search behind "
                            "it. " + (
                                "searches.jsonl is empty, so nothing in this "
                                "project can say the literature was looked "
                                "at."
                                if not searches else
                                f"{len(searches)} search(es) are logged and "
                                f"none of them is about this: the claim is "
                                f"about the CORPUS, so what licenses it is a "
                                f"query that would have found the thing.")),
                        "flag": "**[FLAG: corpus - no recorded search "
                                "supports this]**"})

            if not keys:
                continue
            res["citing_sentences"] += 1
            characterizing = bool(_CHARACTERIZING.search(sentence))
            # Markup and float cross-references go FIRST: a superscript
            # charge state and a "Figure 1" are not numbers this sentence is
            # attributing to anybody (item 75).
            claim_text = strip_non_claim_numbers(sentence)
            numbers = _numbers_in(claim_text)
            values = number_values(claim_text)

            for key in keys:
                rec = records.get(key)
                if rec is None:
                    # One finding per KEY, not per sentence: "there is no
                    # record for this citekey" is one fact about the corpus,
                    # and printing it once per citing sentence is how a
                    # 200-reference review produces a report nobody reads.
                    # How many sentences lean on it is the useful number.
                    missing[key] = missing.get(key, 0) + 1
                    continue
                if not rec.get("verified"):
                    unverified[key] = unverified.get(key, 0) + 1
                tier = str(rec.get("read_tier") or "title")
                rank = TIER_RANK.get(tier, 0)

                if characterizing and rank <= TIER_RANK["title"]:
                    res["findings"].append({
                        "kind": "out-of-tier", "section": name, "key": key,
                        "tier": tier, "sentence": sentence[:200],
                        "detail": "this sentence says what the work found, "
                                  "and only its title was read. "
                                  + TIER_LIMITS[tier],
                        "flag": "**[FLAG: source]**"})
                    continue

            # --- the sentence's numbers and triggers, attributed ONCE ----
            #
            # Per SENTENCE rather than per key, and that is item 75's fourth
            # mechanism: a sentence citing two sources used to be charged
            # with its trigger word and its numbers against BOTH, regardless
            # of which clause they sat in - so a fact properly carried by a
            # fulltext record was reported as out of tier against the
            # abstract-tier record beside it. A trigger or a number is
            # charged only when NO cited record could hold it; where one
            # could, the ambiguity is reported and nothing is charged.
            present = [(k, records[k]) for k in keys if k in records]
            tiers = {k: str(r.get("read_tier") or "title") for k, r in present}
            if present and values:
                # Three classes, because they are checkable in three
                # different ways: a record read in FULL may hold the number
                # in text this project does not store, an ABSTRACT-tier
                # record is checkable against the text it does store, and a
                # title-tier record licenses no number at all.
                deep = [k for k, _ in present
                        if TIER_RANK.get(tiers[k], 0) > TIER_RANK["abstract"]]
                thin = [k for k, _ in present if tiers[k] == "abstract"]
                numbered = deep + thin
                if not numbered:
                    for key, _rec in present:
                        res["findings"].append({
                            "kind": "number-out-of-tier", "section": name,
                            "key": key, "tier": tiers[key],
                            "numbers": numbers[:4],
                            "sentence": sentence[:200],
                            "detail": "a number attributed to a source read "
                                      "only at " + tiers[key] + " tier. "
                                      + TIER_LIMITS[tiers[key]],
                            "flag": "**[FLAG: source]**"})
                elif deep and len(present) > 1:
                    res["findings"].append({
                        "kind": "ambiguous-attribution", "section": name,
                        "key": deep[0], "keys": [k for k, _ in present],
                        "tier": tiers[deep[0]], "numbers": numbers[:4],
                        "sentence": sentence[:200],
                        "detail": "this sentence cites a record read in "
                                  "full, whose own text this project does "
                                  "not hold, so a number here cannot be "
                                  "traced to one source from inside the "
                                  "folder. Nothing is charged; split the "
                                  "sentence, or check it by hand.",
                        "flag": ""})
                elif thin and not deep:
                    # Fine when an abstract carries it, a tier violation when
                    # NONE of the cited abstracts does. Both sides of the
                    # comparison are normalized the same way, numeral only,
                    # with the unit held separately - the record and the prose
                    # are written by different hands, and `1.5 A` against
                    # `1.5 angstrom` used to report a number that was
                    # verbatim in the record as absent from it.
                    held: set = set()
                    for _k, rec_i in present:
                        held |= set(number_values(retrieved_text(rec_i)))

                    # Reported as the reader wrote them - `412 K`, not
                    # `412` - while the COMPARISON is on the numeral alone.
                    shown = {}
                    for tok in numbers:
                        for v in number_values(tok):
                            shown.setdefault(v, tok)
                    absent = [shown.get(n, n) for n in values if n not in held]
                    if absent:
                        res["findings"].append({
                            "kind": "number-not-in-record",
                            "section": name, "key": present[0][0],
                            "keys": [k for k, _ in present],
                            "tier": "abstract",
                            "numbers": absent[:4],
                            "sentence": sentence[:200],
                            "detail": "these numbers are in no cited "
                                      "record's retrieved text, and every "
                                      "record cited here is an abstract. "
                                      "Every number in a review's prose must "
                                      "appear in the record of the source "
                                      "its sentence cites.",
                            "flag": "**[FLAG: source]**"})

            # Deliberately NOT gated on `characterizing`: "the study used
            # n = 6 replicates" carries no verb from that list and is exactly
            # the sentence an abstract does not license. Charged only when
            # EVERY cited record is an abstract or thinner - a deeper record
            # beside it may be where the detail came from.
            if present and _BEYOND_ABSTRACT.search(claim_text):
                deep = [k for k, _ in present
                        if TIER_RANK.get(tiers[k], 0)
                        > TIER_RANK["abstract"]]
                thin = [k for k, _ in present if tiers[k] == "abstract"]
                if thin and not deep:
                    for key in thin:
                        res["findings"].append({
                            "kind": "out-of-tier", "section": name,
                            "key": key, "tier": "abstract",
                            "sentence": sentence[:200],
                            "detail": "this sentence states the source's n, "
                                      "its controls, its limitations or what "
                                      "it failed to consider, and only its "
                                      "abstract was read. "
                                      + TIER_LIMITS["abstract"],
                            "flag": "**[FLAG: source]**"})
                elif thin and deep:
                    res["findings"].append({
                        "kind": "ambiguous-attribution", "section": name,
                        "key": deep[0], "keys": [k for k, _ in present],
                        "tier": tiers[deep[0]], "sentence": sentence[:200],
                        "detail": "this sentence states an experimental "
                                  "detail and cites both a record read in "
                                  "full and one read only at abstract tier, "
                                  "so which source the detail belongs to "
                                  "cannot be decided from here. Nothing is "
                                  "charged; split the sentence, or check it "
                                  "by hand.",
                        "flag": ""})

    for key, n in sorted(missing.items()):
        res["findings"].append({
            "kind": "no-record", "key": key, "section": "-",
            "sentence": f"{n} sentence(s) cite @{key}",
            "detail": f"no data/corpus/papers/{key}.md. No source may be "
                      f"characterized at all unless its record exists and "
                      f"carries a `verified:` line.",
            "flag": "**[FLAG: source]**"})
    for key, n in sorted(unverified.items()):
        res["findings"].append({
            "kind": "unverified", "key": key, "section": "-",
            "sentence": f"{n} sentence(s) cite @{key}",
            "detail": f"{key}'s record carries no `verified:` line, and a "
                      f"record with no verdict licenses no sentence. Run "
                      f"`review.py verify`.",
            "flag": "**[FLAG: source]**"})

    # A sentence citing the same key three times is ONE finding about that
    # sentence. Three copies of the same line is how a real finding gets
    # skimmed past, and the `density`-on-Methods failure is exactly what
    # happens when a check learns to be ignored.
    seen: set = set()
    unique = []
    for f in res["findings"]:
        sig = (f["kind"], f.get("section"), f.get("key"), f["sentence"])
        if sig in seen:
            continue
        seen.add(sig)
        unique.append(f)
    res["duplicates_collapsed"] = len(res["findings"]) - len(unique)
    res["findings"] = unique

    res["counts"] = {}
    for f in res["findings"]:
        res["counts"][f["kind"]] = res["counts"].get(f["kind"], 0) + 1
    return res


# ---------------------------------------------------------------------------
# coverage - what this corpus is, and what it is not
# ---------------------------------------------------------------------------

def coverage(project: str) -> dict:
    """Per-source counts, snowballing yield, secondary share, staleness."""
    proto = read_protocol(project)
    recs = all_records(project)
    searches = read_searches(project)
    rows = read_screened(project)
    state = screened_state(rows)

    by_found: dict = {}
    by_kind: dict = {}
    by_tier: dict = {}
    unverified = []
    for rec in recs:
        by_found[rec.get("found_by", "?")] = \
            by_found.get(rec.get("found_by", "?"), 0) + 1
        by_kind[rec.get("source_kind", "?")] = \
            by_kind.get(rec.get("source_kind", "?"), 0) + 1
        tier = str(rec.get("read_tier") or "title")
        by_tier[tier] = by_tier.get(tier, 0) + 1
        if not rec.get("verified"):
            unverified.append(rec["key"])

    searched_sources: set = set()
    for s in searches:
        for name in s.get("sources") or []:
            searched_sources.add(name)
    queried = sorted({s.get("terms", "") for s in searches if s.get("terms")})
    not_run = [q for q in proto["queries"] if q not in queried]

    secondary = by_kind.get("review", 0)
    res: dict = {
        "project": project,
        "records": len(recs),
        "by_found_by": by_found,
        "by_source_kind": by_kind,
        "by_read_tier": by_tier,
        "unverified": unverified,
        "sources_searched": sorted(searched_sources),
        "sources_declared": proto["sources"],
        "queries_declared": len(proto["queries"]),
        "queries_run": len(queried),
        "queries_not_run": not_run,
        "snowball_yield": by_found.get("backward", 0)
                          + by_found.get("forward", 0),
        "secondary_share": (round(secondary / len(recs), 3) if recs else None),
        "stale": stale_records(project),
        "hand_raised_tiers": hand_raised_tiers(project),
        "screened_rows": len(rows),
        "candidates": len(state),
        "floor": proto["floor"],
        "findings": [],
    }

    included = len([r for r in state.values()
                    if (r.get("decision") or "") == "include"])
    res["included"] = included
    if proto["floor"] is not None:
        res["below_floor"] = included < proto["floor"]
        if res["below_floor"]:
            res["findings"].append({
                "kind": "floor",
                "detail": f"{included} included against a floor of "
                          f"{proto['floor']} written in protocol.md. The "
                          f"floor is YOUR number, and this reports against "
                          f"it rather than inventing a threshold."})
    if res["secondary_share"] is not None and res["secondary_share"] > 0.25:
        # A review cited as primary evidence is a review's most common
        # citation error, and no eye catches it at 200 references.
        res["findings"].append({
            "kind": "secondary",
            "detail": f"{secondary} of {len(recs)} records "
                      f"({res['secondary_share']:.0%}) are themselves "
                      f"reviews. That is a real reviewer objection; check "
                      f"that each is cited as a review and not as primary "
                      f"evidence."})
    if not res["snowball_yield"] and recs:
        res["findings"].append({
            "kind": "no-snowball",
            "detail": "nothing in this corpus was found by chasing "
                      "citations. A query-only corpus in a field with "
                      "idiosyncratic vocabulary is a corpus of the words you "
                      "happened to type - `scholar.py references` and "
                      "`cited-by` on the most central members is where the "
                      "rest of the tail is."})
    if not_run:
        res["findings"].append({
            "kind": "queries-not-run",
            "detail": f"{len(not_run)} quer{'y' if len(not_run) == 1 else 'ies'} "
                      f"in protocol.md has no line in searches.jsonl."})
    for entry in res["hand_raised_tiers"]:
        res["findings"].append({"kind": "tier-guard", **entry})
    for entry in res["stale"]:
        res["findings"].append({
            "kind": "stale", "key": entry["key"],
            "detail": f"{entry['key']}: {entry['why']}"})
    if unverified:
        res["findings"].append({
            "kind": "unverified",
            "detail": f"{len(unverified)} record(s) carry no `verified:` "
                      f"line, and a record with no verdict licenses no "
                      f"sentence: {', '.join(unverified[:8])}"
                      + (" ..." if len(unverified) > 8 else "")})
    res["ceiling"] = (
        "recall through keyless indexes is not exhaustive. This reports what "
        "was searched and when; it does not claim the field was covered.")
    return res


# ---------------------------------------------------------------------------
# prisma - arithmetic about a process, or nothing
# ---------------------------------------------------------------------------

def prisma(project: str) -> dict:
    """Counts derived from screened.csv, refusing any it cannot derive.

    A flow diagram is a set of arithmetic claims about a process, and typing
    one in is the review-paper form of hand-editing a citation number. So
    every number here comes out of the log, and a number the log cannot
    support is reported as `undetermined` with the reason - never as a
    plausible one.
    """
    proto = read_protocol(project)
    rows = read_screened(project)
    state = screened_state(rows)
    searches = read_searches(project)

    res: dict = {"project": project, "counts": {}, "undetermined": [],
                 "by_reason": {}, "errors": []}
    if not rows:
        res["undetermined"].append({
            "count": "everything",
            "why": "screened.csv is empty. Every PRISMA number is derived "
                   "from it, and there is nothing to derive from."})
        return res

    identified = len(state)
    res["counts"]["identified"] = identified

    # Records EXAMINED across all searches, which is not the same number and
    # must not be reported as if it were: it counts a paper once per query
    # that returned it.
    examined = sum(int(s.get("examined") or 0) for s in searches)
    if searches:
        res["counts"]["records_examined_across_searches"] = examined
    else:
        res["undetermined"].append({
            "count": "records examined",
            "why": "searches.jsonl is empty, so nothing records how many "
                   "records the sweep looked at."})

    # Duplicates removed: the difference between rows written and distinct
    # keys is NOT that number - dedup happens before a row is written. This
    # is the honest answer.
    res["undetermined"].append({
        "count": "duplicates removed",
        "why": "deduplication happens in the merge, before a candidate row "
               "is written, so screened.csv never saw the duplicates. "
               "searches.jsonl records `examined` and `new` per query; the "
               "difference spans queries and is not a duplicate count."})

    decided = {k: v for k, v in state.items()
               if (v.get("decision") or "").strip()}
    res["counts"]["screened"] = len(decided)
    res["counts"]["undecided"] = identified - len(decided)
    res["counts"]["included"] = len([v for v in state.values()
                                     if v.get("decision") == "include"])
    res["counts"]["excluded"] = len([v for v in state.values()
                                     if v.get("decision") == "exclude"])
    res["counts"]["maybe"] = len([v for v in state.values()
                                  if v.get("decision") == "maybe"])

    for v in state.values():
        if v.get("decision") != "exclude":
            continue
        reason = (v.get("reason") or "").strip() or "(no reason recorded)"
        res["by_reason"][reason] = res["by_reason"].get(reason, 0) + 1
    unknown = [r for r in res["by_reason"] if r not in proto["exclusion"]]
    if unknown:
        res["errors"].append(
            f"exclusion reasons not in protocol.md's list: "
            f"{', '.join(unknown)}. The list is the closed set because these "
            f"counts are derived from it.")

    records = all_records(project)
    res["counts"]["records_written"] = len(records)
    if res["counts"]["included"] != len(records):
        res["undetermined"].append({
            "count": "included in synthesis",
            "why": f"{res['counts']['included']} rows are marked include and "
                   f"{len(records)} paper records exist. Those are two "
                   f"different claims about the same set, and this will not "
                   f"pick one."})
    else:
        res["counts"]["included_in_synthesis"] = len(records)

    if res["counts"]["undecided"] or res["counts"]["maybe"]:
        res["errors"].append(
            f"{res['counts']['undecided']} undecided and "
            f"{res['counts']['maybe']} maybe. A screening pass that leaves "
            f"maybes has not screened, and a flow diagram drawn over them "
            f"claims a process that has not finished.")
    return res


# ---------------------------------------------------------------------------
# echo - close paraphrase, as an instrument (spec 16.6)
# ---------------------------------------------------------------------------
#
# Two hundred abstracts in a drafter's context is the highest-risk plagiarism
# setting this toolkit has ever created. This reports the longest common
# n-gram between each drafted sentence and every retrieved source text, with
# the source and the span.
#
# IT ENTERS AS AN INSTRUMENT AND IS NOT CALIBRATED. Technical phrases
# legitimately repeat - `self-assembled monolayers on gold` is not plagiarism
# - so it reports, always exits 0 on its own findings, and says in its own
# output that nobody has yet chosen its threshold. A measurement instrument
# may not become a gate.

_ECHO_DEFAULT_N = 8


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _ngrams(words: list[str], n: int) -> set:
    return {tuple(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def echo(project: str, min_ngram: int = _ECHO_DEFAULT_N,
         sections: str = "") -> dict:
    res: dict = {"project": project, "min_ngram": min_ngram, "hits": [],
                 "calibrated": False, "advisory": True, "errors": []}
    src = _source_text_dir(project, sections)
    if not src or not os.path.isdir(src):
        res["errors"].append("no drafted sections to check")
        return res
    res["source_text"] = src

    sources = {}
    for rec in all_records(project):
        words = _words(retrieved_text(rec))
        if len(words) >= min_ngram:
            sources[rec["key"]] = (words, _ngrams(words, min_ngram))
    res["sources_compared"] = len(sources)
    if not sources:
        res["note"] = ("no record holds enough retrieved text to compare "
                       "against. An abstract-tier corpus with empty `What it "
                       "claims` sections is nothing to echo.")
        return res

    checked = 0
    for name in sorted(os.listdir(src)):
        if not name.endswith(".md"):
            continue
        for sentence in _sentences(_read(os.path.join(src, name))):
            words = _words(sentence)
            if len(words) < min_ngram:
                continue
            checked += 1
            grams = _ngrams(words, min_ngram)
            for key, (_, sgrams) in sources.items():
                shared = grams & sgrams
                if not shared:
                    continue
                longest = max(shared, key=len)
                res["hits"].append({
                    "section": name, "key": key,
                    "span": " ".join(longest),
                    "length": len(longest),
                    "sentence": sentence[:200]})
    res["sentences_checked"] = checked
    res["note"] = (
        f"{len(res['hits'])} span(s) of {min_ngram}+ words shared with a "
        f"retrieved source. This is an INSTRUMENT, not a gate: technical "
        f"phrases legitimately repeat, no threshold has been calibrated "
        f"against real drafted prose, and nothing here fails a build. Read "
        f"the spans and decide.")
    return res


# ---------------------------------------------------------------------------
# status - one screen
# ---------------------------------------------------------------------------

def status(project: str) -> dict:
    proto = read_protocol(project)
    recs = all_records(project)
    rows = read_screened(project)
    state = screened_state(rows)
    searches = read_searches(project)

    ages = [a for a in (_verdict_age(r.get("verified", "")) for r in recs)
            if a is not None]
    by_tier: dict = {}
    for r in recs:
        t = str(r.get("read_tier") or "title")
        by_tier[t] = by_tier.get(t, 0) + 1

    return {
        "project": os.path.abspath(project),
        "paper_kind": "review" if is_review(project) else "research",
        "protocol": {"exists": proto["exists"],
                     "written": proto["written"],
                     "unwritten": proto["unwritten"],
                     "question": proto["question"][:160],
                     "floor": proto["floor"]},
        "corpus": {"records": len(recs), "candidates": len(state),
                   "screened_rows": len(rows),
                   "included": len([v for v in state.values()
                                    if v.get("decision") == "include"]),
                   "excluded": len([v for v in state.values()
                                    if v.get("decision") == "exclude"]),
                   "maybe": len([v for v in state.values()
                                 if v.get("decision") == "maybe"]),
                   "undecided": len([v for v in state.values()
                                     if not (v.get("decision") or "").strip()])},
        "tiers": by_tier,
        "searches": {"lines": len(searches),
                     "queries_declared": len(proto["queries"])},
        "verification": {"with_verdict": len(ages),
                         "without": len(recs) - len(ages),
                         "oldest_days": max(ages) if ages else None},
        "stale": stale_records(project),
        "hand_raised_tiers": hand_raised_tiers(project),
        "synthesis_written": os.path.isfile(synthesis_path(project)),
    }


# ---------------------------------------------------------------------------
# Printers
# ---------------------------------------------------------------------------

def print_protocol(res: dict) -> None:
    if res.get("created"):
        print(f"  wrote {res['path']}")
    print(f"  protocol.md  {'present' if res['exists'] else 'MISSING'}")
    if res["written"]:
        print(f"    written:   {', '.join(res['written'])}")
    if res["unwritten"]:
        print(f"    unwritten: {', '.join(res['unwritten'])}")
    if res.get("question"):
        print(f"    question:  {res['question'][:100]}")
    print(f"    can build a corpus: {'yes' if res.get('can_build') else 'NO'}")
    if res.get("refused"):
        print(f"\n  REFUSED  {res['refused']}")
    if res.get("note"):
        print(f"\n  note  {res['note']}")


def print_search(res: dict) -> None:
    if res.get("refused"):
        print(f"  REFUSED  {res['refused']}")
        return
    for q in res["queries"]:
        if q.get("error"):
            print(f"  !  {q['query'][:60]}: {q['error']}")
            continue
        print(f"  {q['examined']:>4} examined, {q['new']:>3} new  "
              f"{q['terms'][:60]}")
        print(f"       sources {', '.join(q['sources'])}  pages "
              f"{q['pages_read']}")
        for u in q.get("unreachable") or []:
            print(f"       !  {u['source']} did not answer ({u['reason']})")
    print(f"\n  {res['added']} new candidate(s); {res.get('undecided', 0)} "
          f"undecided in screened.csv")
    print(f"  {res['ceiling']}")


def print_screen(res: dict) -> None:
    if res.get("errors"):
        # A refusal prints the refusal and nothing else. A summary printed
        # over an error reads as a result, and the counts under it are the
        # counts from BEFORE the thing that was refused.
        for e in res["errors"]:
            print(f"  !  {e}")
        return
    for c in res.get("changes", []):
        print(f"  {c['key']}  {c['detail']}")
    counts = res.get("counts") or {}
    print(f"  included {counts.get('include', 0)}  "
          f"excluded {counts.get('exclude', 0)}  "
          f"maybe {counts.get('maybe', 0)}  "
          f"undecided {counts.get('undecided', 0)}")
    for row in res.get("undecided", [])[:20]:
        print(f"    ?  {row['key']:28} {row['title'][:60]}")
    if len(res.get("undecided", [])) > 20:
        print(f"    ... {len(res['undecided']) - 20} more")
    if res.get("outstanding"):
        print(f"\n  {res['outstanding']} outstanding. `maybe` is a real "
              f"state: a screening pass that leaves forty maybes has not "
              f"screened.")


def print_tier(res: dict) -> None:
    print(f"  {res['sentences']} sentence(s), {res['citing_sentences']} "
          f"citing, against {res.get('records', 0)} record(s)")
    for kind, n in sorted((res.get("counts") or {}).items()):
        print(f"    {kind:22} {n}")
    for f in res["findings"][:40]:
        where = f.get("key") or "-"
        print(f"\n  {f['kind']}  {f['section']}  [{where}]")
        print(f"    {f['sentence']}")
        if f.get("numbers"):
            print(f"    numbers: {', '.join(f['numbers'])}")
        print(f"    {f['detail']}")
    if len(res["findings"]) > 40:
        print(f"\n  ... {len(res['findings']) - 40} more")
    if not res["findings"]:
        print("\n  nothing out of tier.")


def print_coverage(res: dict) -> None:
    print(f"  {res['records']} record(s), {res['included']} included of "
          f"{res['candidates']} candidates")
    print(f"    by tier      {res['by_read_tier']}")
    print(f"    by found_by  {res['by_found_by']}")
    print(f"    by kind      {res['by_source_kind']}")
    if res["secondary_share"] is not None:
        print(f"    secondary    {res['secondary_share']:.0%} of the "
              f"evidence base is itself a review")
    print(f"    searched     {', '.join(res['sources_searched']) or '(nothing)'}")
    print(f"    queries      {res['queries_run']} run of "
          f"{res['queries_declared']} declared")
    for f in res["findings"]:
        print(f"\n  {f['kind']}  {f['detail']}")
    print(f"\n  {res['ceiling']}")


def print_prisma(res: dict) -> None:
    for k, v in res["counts"].items():
        print(f"  {k:36} {v}")
    if res["by_reason"]:
        print("\n  excluded, by reason:")
        for k, v in sorted(res["by_reason"].items(), key=lambda x: -x[1]):
            print(f"    {k:34} {v}")
    for u in res["undetermined"]:
        print(f"\n  UNDETERMINED  {u['count']}\n    {u['why']}")
    for e in res["errors"]:
        print(f"\n  !  {e}")


def print_status(res: dict) -> None:
    p = res["protocol"]
    print(f"  {res['project']}  ({res['paper_kind']})")
    print(f"  protocol     {'present' if p['exists'] else 'MISSING'}"
          + (f", unwritten: {', '.join(p['unwritten'])}"
             if p["unwritten"] else ", complete"))
    if p["question"]:
        print(f"               {p['question']}")
    c = res["corpus"]
    print(f"  corpus       {c['records']} record(s); {c['included']} "
          f"included, {c['excluded']} excluded, {c['maybe']} maybe, "
          f"{c['undecided']} undecided")
    print(f"  tiers        {res['tiers'] or '(none)'}")
    v = res["verification"]
    print(f"  verified     {v['with_verdict']} with a verdict, "
          f"{v['without']} without"
          + (f"; oldest {v['oldest_days']} days" if v["oldest_days"] is not None
             else ""))
    print(f"  searches     {res['searches']['lines']} line(s) logged, "
          f"{res['searches']['queries_declared']} quer(y/ies) declared")
    print(f"  synthesis    {'written' if res['synthesis_written'] else 'not written'}")
    for s in res["stale"]:
        print(f"    stale  {s['key']}: {s['why']}")
    for h in res["hand_raised_tiers"]:
        print(f"    TIER   {h['key']}: {h['detail']}")


def print_echo(res: dict) -> None:
    if res["errors"]:
        for e in res["errors"]:
            print(f"  !  {e}")
        return
    print(f"  {res.get('sentences_checked', 0)} sentence(s) against "
          f"{res.get('sources_compared', 0)} retrieved source(s), "
          f"n-gram {res['min_ngram']}")
    for h in sorted(res["hits"], key=lambda x: -x["length"])[:25]:
        print(f"    {h['length']:>3}w  {h['section']}  [{h['key']}]  "
              f"\"{h['span'][:90]}\"")
    print(f"\n  {res['note']}")


def print_record(res: dict) -> None:
    for e in res.get("errors", []):
        print(f"  !  {e}")
    for c in res.get("changes", []):
        detail = f" - {c['detail']}" if c.get("detail") else ""
        print(f"  {c['action']:6} {c['field']}{detail}")
    if res.get("path"):
        print(f"  wrote {res['path']}  (tier {res.get('read_tier')})")


def print_verify(res: dict) -> None:
    for c in res.get("checked", []):
        print(f"  {c['status']:12} {c['key']:28} via {c['source']}")
    for r in res.get("retracted", []):
        print(f"  RETRACTED    {r['key']}  {r['doi']}")
    for e in res.get("errors", []):
        print(f"  !  {e}")
    if res.get("reuse_note"):
        print(f"\n  {res['reuse_note']}")


def print_adopt(res: dict) -> None:
    for a in res["adopted"]:
        print(f"  + {a['key']:28} tier {a['tier']:16} from {a['from']}")
    for s in res["skipped"]:
        print(f"  = {s['key']:28} {s['why']}")
    for e in res["errors"]:
        print(f"  !  {e}")
    if res["adopted"]:
        print(f"\n  {res['note']}")


def print_synthesis(res: dict) -> None:
    print(f"  {res['count']} record(s) -> {res['path']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        prog="review.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true",
                   help="emit JSON instead of text")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true",
                        default=argparse.SUPPRESS,
                        help="emit JSON instead of text")

    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("protocol", parents=[common],
                        help="the scope contract, and whether it can build")
    pr.add_argument("project", nargs="?", default=".")
    pr.add_argument("--init", action="store_true",
                    help="write the template, once. It is never generated "
                         "over: protocol.md is yours")

    se = sub.add_parser("search", parents=[common],
                        help="run the protocol's queries, recording every page")
    se.add_argument("project", nargs="?", default=".")
    se.add_argument("--query", action="append", default=[],
                    help="run this query instead of the protocol's")
    se.add_argument("--pages", type=int, default=2)
    se.add_argument("--limit", type=int, default=50,
                    help="per-source depth per page")
    se.add_argument("--kind", default="query", choices=list(SEARCH_KINDS),
                    help="how these candidates were found. `backward` and "
                         "`forward` are snowballing, and a systematic review "
                         "must report them separately")
    se.add_argument("--source", action="append", default=[])
    se.add_argument("--dry-run", action="store_true")

    sc = sub.add_parser("screen", parents=[common],
                        help="what is undecided; record one decision")
    sc.add_argument("project", nargs="?", default=".")
    sc.add_argument("--key", default="")
    sc.add_argument("--decide", dest="decision", default="",
                    choices=["", *DECISIONS])
    sc.add_argument("--reason", default="",
                    help="an exclusion reason CODE from protocol.md's list")

    ad = sub.add_parser("adopt", parents=[common],
                        help="a .bib, a folder of PDFs or an Ideas/ folder")
    ad.add_argument("project", nargs="?", default=".")
    ad.add_argument("--bib", default="")
    ad.add_argument("--pdfs", default="")
    ad.add_argument("--ideas", default="")
    ad.add_argument("--found-by", default="handpicked", choices=list(FOUND_BY))

    rc = sub.add_parser("record", parents=[common],
                        help="create or update one paper record")
    rc.add_argument("project", nargs="?", default=".")
    rc.add_argument("--key", required=True)
    rc.add_argument("--tier", default="", choices=["", *TIERS],
                    help="THE field the layer-0 rule turns on, and the only "
                         "writer of it is this command")
    rc.add_argument("--doi", default="")
    rc.add_argument("--pmid", default="")
    rc.add_argument("--title", default="")
    rc.add_argument("--authors", default="")
    rc.add_argument("--year", default="")
    rc.add_argument("--journal", default="")
    rc.add_argument("--source-kind", dest="source_kind", default="",
                    choices=["", *SOURCE_KINDS])
    rc.add_argument("--found-by", dest="found_by", default="",
                    choices=["", *FOUND_BY])
    rc.add_argument("--retrieved", default="",
                    help="which sections were retrieved, for a fulltext tier")
    rc.add_argument("--claims", default="")
    rc.add_argument("--reports", default="")
    rc.add_argument("--quotes", default="")
    rc.add_argument("--uses", default="")

    vf = sub.add_parser("verify", parents=[common],
                        help="incremental verification; retraction always")
    vf.add_argument("project", nargs="?", default=".")
    vf.add_argument("--all", dest="recheck_all", action="store_true",
                    help="re-check every record; the pre-submission pass")
    vf.add_argument("--window", type=int, default=None,
                    help="days a verdict may be reused; protocol.md's "
                         "`## Verification Window` otherwise")
    vf.add_argument("--limit", type=int, default=0,
                    help="stop after N records, and say how many were left")

    sy = sub.add_parser("synthesis", parents=[common],
                        help="papers/ -> synthesis.md, the evidence table")
    sy.add_argument("project", nargs="?", default=".")
    sy.add_argument("--dry-run", action="store_true")

    ti = sub.add_parser("tier", parents=[common],
                        help="every characterizing sentence against its "
                             "record's tier")
    ti.add_argument("project", nargs="?", default=".")
    ti.add_argument("--sections", default="",
                    help="the folder of drafted sections; "
                         "drafts/source_text*/ otherwise")

    cv = sub.add_parser("coverage", parents=[common],
                        help="what this corpus is, and what it is not")
    cv.add_argument("project", nargs="?", default=".")

    pm = sub.add_parser("prisma", parents=[common],
                        help="counts from screened.csv, refusing what it "
                             "cannot derive")
    pm.add_argument("project", nargs="?", default=".")

    ec = sub.add_parser("echo", parents=[common],
                        help="shared n-grams between the draft and every "
                             "retrieved source. An instrument, not a gate")
    ec.add_argument("project", nargs="?", default=".")
    ec.add_argument("--min-ngram", type=int, default=_ECHO_DEFAULT_N)
    ec.add_argument("--sections", default="")

    st = sub.add_parser("status", parents=[common],
                        help="protocol, corpus, tiers, verdicts, outstanding")
    st.add_argument("project", nargs="?", default=".")

    args = p.parse_args()
    as_json = getattr(args, "json", False)

    def emit(res: dict, printer) -> None:
        if as_json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            printer(res)

    if args.cmd == "protocol":
        res = protocol(args.project, args.init)
        emit(res, print_protocol)
        return 2 if res.get("refused") else 0

    if args.cmd == "search":
        res = search(args.project, args.query or None, pages=args.pages,
                     limit=args.limit, kind=args.kind,
                     sources=args.source or None, dry_run=args.dry_run)
        emit(res, print_search)
        if res.get("refused"):
            return 2
        return 1 if res.get("errors") else 0

    if args.cmd == "screen":
        res = screen(args.project, args.key, args.decision, args.reason)
        emit(res, print_screen)
        if res["errors"]:
            return 2
        return 1 if res["outstanding"] else 0

    if args.cmd == "adopt":
        if not (args.bib or args.pdfs or args.ideas):
            print("adopt needs one of --bib, --pdfs or --ideas",
                  file=sys.stderr)
            return 2
        res = adopt(args.project, args.bib, args.pdfs, args.ideas,
                    args.found_by)
        emit(res, print_adopt)
        return 2 if res["errors"] and not res["adopted"] else 0

    if args.cmd == "record":
        res = record_cmd(
            args.project, args.key, tier=args.tier, doi=args.doi,
            pmid=args.pmid, title=args.title, authors=args.authors,
            year=args.year, journal=args.journal,
            source_kind=args.source_kind, found_by=args.found_by,
            retrieved=args.retrieved, claims=args.claims,
            reports=args.reports, quotes=args.quotes, uses=args.uses)
        emit(res, print_record)
        return 2 if res["errors"] else 0

    if args.cmd == "verify":
        res = verify(args.project, args.recheck_all, args.window, args.limit)
        emit(res, print_verify)
        if res["retracted"]:
            return 1
        return 1 if res["errors"] else 0

    if args.cmd == "synthesis":
        res = synthesis(args.project, write=not args.dry_run)
        emit(res, print_synthesis)
        return 0

    if args.cmd == "tier":
        res = tier_check(args.project, args.sections)
        emit(res, print_tier)
        if res["errors"]:
            return 2
        return 1 if res["findings"] else 0

    if args.cmd == "coverage":
        res = coverage(args.project)
        emit(res, print_coverage)
        return 1 if res["findings"] else 0

    if args.cmd == "prisma":
        res = prisma(args.project)
        emit(res, print_prisma)
        return 1 if (res["undetermined"] or res["errors"]) else 0

    if args.cmd == "echo":
        res = echo(args.project, args.min_ngram, args.sections)
        emit(res, print_echo)
        # Always 0 on findings. A measurement instrument may not become a
        # gate, and this one is not even calibrated yet.
        return 2 if res["errors"] else 0

    if args.cmd == "status":
        res = status(args.project)
        emit(res, print_status)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
