#!/usr/bin/env python3
"""
sequence.py - keyless sequence and genomic-context engine: NCBI BLAST, the
genomic neighbourhood of a gene, and what to do when AlphaFold has no model.

Why a third engine rather than a sixth subcommand on structure.py:
specs/external-services.md 3 set the test and it answers this cleanly - a
second engine exists when its normalized record answers a different question.
A BLAST hit - subject accession, query coverage, percent identity, E-value,
alignment span - is not a structure record, and a genomic neighbour - contig,
strand, locus tag, product, distance from the query - is not either.

Same shape as every engine here: source registry, one normalized record per
result kind, --json on either side of every subcommand, one throttle, and
never a bare exception - a failed source degrades the record and says so in
`notes` rather than aborting the command.

It reaches structure.py BY SUBPROCESS, the way review.py reaches scholar.py.
It does not import it.

No key, no email and no OAuth on the default path, which is what keeps this
engine inside the keyless-only rule of specs/scholar-engine.md 2. `fold
--submit` is the one exception: it is opt-in, explicit, goes through the same
credential resolver learn.py uses (specs/distribution.md 5.6),
and refuses cleanly when unconfigured rather than failing at the network.

WHAT SEQUENCE DATA MAY BE USED FOR, and this is the paragraph to read twice.
specs/external-services.md 4.6, extended to sequence data and not loosened:

    Sequence evidence is evidence for FEASIBILITY and for METHODS.
    It is NEVER evidence for a gap.

"No homolog was found, therefore this is unstudied" is the
PubMed-found-nothing fallacy with a different index in front of it - and it is
already measured in this toolkit: a full-text search for the very well-studied
MOF HKUST-1 returns total_count: 1. A BLAST miss means this query did not
match this database on this day. It does not mean nobody has looked.

And it SEEDS QUESTIONS rather than answering them. `neighbors` returning
eleven genes is eleven things to ask the user about, not eleven findings.

Usage:
  python sequence.py blast --accession P58335 --program blastp --db nr
  python sequence.py blast --sequence query.fasta --submit
  python sequence.py blast --check <RID>
  python sequence.py neighbors --accession WP_000123456.1 --window 10
  python sequence.py fold --accession P58335
  python sequence.py fold --accession P58335 --submit
  python sequence.py fold --check <handle>
  python sequence.py status
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time

try:
    import requests
except ImportError:
    sys.exit("sequence.py requires 'requests'. "
             "Install with: python -m pip install requests")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pubmed  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    # reconfigure() belongs to TextIOWrapper, not to every text stream: under
    # a pipe or a capturing harness sys.stdout may have no such method at all.
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except ValueError:
            pass

pubmed._load_env_file()


# ---------------------------------------------------------------------------
# The User-Agent, and the same measurement structure.py records
# ---------------------------------------------------------------------------
#
# A short or absent User-Agent is refused by several of these hosts with 403,
# which reads as "this service needs credentials" when it means "send a real
# User-Agent". Defined once, sent on every request, asserted by the suite.

USER_AGENT = ("paper-writing-aids-sequence/1.0 "
              "(+research paper writing aids; keyless)")
UA = {"User-Agent": USER_AGENT}
UA_MIN_LENGTH = 20


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

BASES = {
    "blast": "https://blast.ncbi.nlm.nih.gov/Blast.cgi",
    "eutils": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
    "fold": "https://health.api.nvidia.com/v1/biology/nvidia/esmfold",
}

SOURCE_ROLE = {
    "blast": "sequence similarity - submit, poll, and read hits",
    "eutils": "accession to contig and coordinates; the feature table of a "
              "genomic window (the operon question)",
    "fold": "folding a sequence AlphaFold DB has no model for. OPT-IN, "
            "credentialled, and never automatic",
}

ALL_SOURCES = ["blast", "eutils", "fold"]

# --- THE ETIQUETTE, ENCODED RATHER THAN ASSUMED ----------------------------
#
# Re-read from NCBI's published developer guidance on 2026-09-18 rather than
# copied from the spec, because a throttle taken on trust is a throttle that
# has already drifted. The guidance for the BLAST URL API, verbatim in
# substance:
#
#   - do not contact the server more often than once every 10 SECONDS
#   - do not poll for any single RID more often than once a MINUTE
#   - use the URL parameters `tool` and `email` so NCBI can make contact
#   - run large jobs (more than 50 searches) at weekends or between
#     9 pm and 5 am Eastern on weekdays
#
# The last one is a fact this engine reports rather than enforces: refusing to
# run at 3 pm would be a refusal people work around, and a workaround looks
# like output.
#
# E-utilities is a different service with a different ceiling: 3 requests per
# second without a key, which is what 0.34 s is.
MIN_INTERVAL = {
    "blast": 10.0,          # NCBI: one search every ten seconds
    "blast_poll": 60.0,     # NCBI: one poll per RID per minute
    "eutils": 0.34,         # E-utilities: 3/s anonymous
    "fold": 1.0,
}
BLAST_ETIQUETTE = {
    "submit_interval_s": 10.0,
    "poll_interval_s": 60.0,
    "bulk_advice": "more than 50 searches: weekends, or 9 pm to 5 am Eastern "
                   "on a weekday. Reported, never enforced",
    "source": "NCBI BLAST URL API developer guidance, re-read 2026-09-18",
}

_last_call: dict[str, float] = {}

# Appended wherever a caller could read an empty or thin result as a gap.
# specs/external-services.md 4.6, extended to sequence data (spec 4.7).
GAP_CAUTION = (
    "Absence here is not evidence of a gap. A BLAST miss means this query did "
    "not match this database on this day; it does not mean nobody has looked, "
    "and it is not evidence that the idea is novel. Sequence evidence is "
    "evidence for FEASIBILITY and for METHODS. A gap still needs a paper "
    "stating the limitation, or two papers that conflict.")

SEEDS_QUESTIONS = (
    "These are questions to put to the user, not findings. Eleven genes in a "
    "neighbourhood is eleven things to ask about - the feasibility dialogue "
    "turning into a lookup is the failure this rule exists to prevent.")


class SourceUnreachable(Exception):
    """A source failed to answer. Never the same thing as a record being absent."""

    def __init__(self, source: str, reason: str):
        self.source, self.reason = source, reason
        super().__init__("%s unreachable: %s" % (source, reason))


class InvalidIdentifier(Exception):
    """The caller passed the wrong KIND of identifier.

    Kept distinct from "no such record" for the reason structure.py keeps them
    distinct: the two have different remedies, and collapsing them turns "this
    database has nothing for this protein" - a fact about the world - into
    "you typed the wrong thing".
    """

    def __init__(self, source: str, given: str, reason: str):
        self.source, self.given, self.reason = source, given, reason
        super().__init__("%s: %r is not a valid identifier: %s"
                         % (source, given, reason))


def _throttle(key: str) -> None:
    interval = MIN_INTERVAL.get(key, 1.0)
    delta = time.monotonic() - _last_call.get(key, 0.0)
    if delta < interval:
        time.sleep(interval - delta)
    _last_call[key] = time.monotonic()


def _request(source: str, method: str, url: str, given: str = "",
             params: dict | None = None, data: object = None,
             timeout: int = 60, retries: int = 3,
             throttle_key: str = "",
             headers: dict | None = None) -> object:
    """One HTTP call with the throttle, the User-Agent and the status contract.

    Returns the response object. A source that did not answer raises rather
    than returning an empty result, so "the service was down" can never be
    reported as "there is nothing there".
    """
    last = ""
    for _attempt in range(retries):
        _throttle(throttle_key or source)
        sent = dict(UA)
        sent.update(headers or {})
        if isinstance(data, (dict, list)):
            sent["Content-Type"] = "application/json"
        try:
            resp = requests.request(
                method, url, params=params, headers=sent, timeout=timeout,
                json=data if isinstance(data, (dict, list)) else None)
        except requests.RequestException as exc:
            last = str(exc)
            continue
        if resp.status_code == 200:
            return resp
        if resp.status_code == 400:
            raise InvalidIdentifier(source, given, resp.text[:200])
        if resp.status_code == 403:
            raise SourceUnreachable(
                source,
                "HTTP 403 with User-Agent %r. Measured across this toolkit, a "
                "403 here is User-Agent filtering rather than a credential "
                "requirement. Do not add an API key in response to this; "
                "there is none to add. Body: %s"
                % (USER_AGENT, resp.text[:160]))
        last = "HTTP %d: %s" % (resp.status_code, resp.text[:160])
    raise SourceUnreachable(source, last or "no response")


# ---------------------------------------------------------------------------
# The normalized records
# ---------------------------------------------------------------------------

def blast_hit(accession: str = "", title: str = "", organism: str = "",
              identity_pct: float = 0.0, query_coverage_pct: float = 0.0,
              evalue: float = 0.0, bit_score: float = 0.0,
              align_length: int = 0, query_span: tuple = (0, 0),
              subject_span: tuple = (0, 0), rank: int = 0) -> dict:
    """One BLAST hit, in the one shape every caller here reads.

    `rank` is the rank the SERVICE returned it at, not a re-scoring of ours -
    a recorded search that cannot be replayed in the order it arrived is not
    reproducible.
    """
    return {"accession": accession, "title": title, "organism": organism,
            "identity_pct": round(float(identity_pct), 2),
            "query_coverage_pct": round(float(query_coverage_pct), 2),
            "evalue": float(evalue), "bit_score": float(bit_score),
            "align_length": int(align_length),
            "query_from": query_span[0], "query_to": query_span[1],
            "subject_from": subject_span[0], "subject_to": subject_span[1],
            "rank": rank}


def neighbour(contig: str = "", locus_tag: str = "", product: str = "",
              strand: str = "", start: int = 0, end: int = 0,
              distance_nt: int = 0, protein_id: str = "",
              same_strand: bool | None = None) -> dict:
    """One genomic neighbour. `distance_nt` is signed: negative is upstream."""
    return {"contig": contig, "locus_tag": locus_tag, "product": product,
            "strand": strand, "start": start, "end": end,
            "distance_nt": distance_nt, "protein_id": protein_id,
            "same_strand": same_strand}


# ---------------------------------------------------------------------------
# The recorded search - specs/user-asks-2026-09-18-second.md 4.5
# ---------------------------------------------------------------------------
#
# An idea that cannot be reproduced is not evidence. review.py already holds
# this line for literature searches - the recorded search, the screening log,
# one record per source - and sequence evidence gets the same treatment.

EVIDENCE_REL = "plan/evidence/sequence"


def evidence_dir(project: str) -> str:
    return os.path.join(project, "plan", "evidence", "sequence")


def query_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def record_search(project: str, record: dict) -> str:
    """Write one recorded search, and return the path.

    The DATABASE VERSION STRING the service returned is part of the record and
    not decoration: `nr` last month and `nr` today are different databases, and
    a hit count without one is unreproducible. Where the service did not say,
    the field is the empty string and the record says `unknown` rather than
    inventing one.
    """
    d = evidence_dir(project)
    os.makedirs(d, exist_ok=True)
    stamp = record.get("date") or time.strftime("%Y-%m-%d")
    stem = "%s_%s_%s" % (stamp, record.get("kind", "search"),
                         record.get("query_id", "query"))
    path = os.path.join(d, stem + ".json")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)
    return path


def read_jobs(project: str) -> list[dict]:
    """Every recorded search in this project, newest first.

    A slow search can be picked up in a later session, which is the whole
    reason the RID or job handle is written down rather than printed once.
    """
    d = evidence_dir(project)
    out: list[dict] = []
    try:
        names = sorted(os.listdir(d), reverse=True)
    except OSError:
        return out
    for n in names:
        if not n.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, n), encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        rec["_file"] = EVIDENCE_REL + "/" + n
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# BLAST - submit, poll, read
# ---------------------------------------------------------------------------
#
# The NCBI URL API is submit-then-poll: a Put returns a Request ID and results
# arrive minutes later. The CLI MUST NOT HIDE THAT. `--submit` returns the RID
# at once, `--check` reads it, and the plain form is submit-then-poll with an
# explicit `--timeout`. A subcommand that blocks for ten minutes with no
# output is a subcommand people kill.

_RID_RE = re.compile(r"RID\s*=\s*([A-Za-z0-9_-]+)")
_RTOE_RE = re.compile(r"RTOE\s*=\s*(\d+)")
_STATUS_RE = re.compile(r"Status\s*=\s*(\w+)")
_HITS_RE = re.compile(r"ThereAreHits\s*=\s*(\w+)")

BLAST_PROGRAMS = ("blastp", "blastn", "blastx", "tblastn", "tblastx")


def blast_submit(query: str, program: str = "blastp", db: str = "nr",
                 limit: int = 50) -> dict:
    """Put one search and return its RID. Returns at once, by design."""
    if program not in BLAST_PROGRAMS:
        raise InvalidIdentifier("blast", program,
                                "program must be one of %s"
                                % ", ".join(BLAST_PROGRAMS))
    resp = _request("blast", "PUT", BASES["blast"], given=query[:40], params={
        "CMD": "Put", "PROGRAM": program, "DATABASE": db, "QUERY": query,
        "HITLIST_SIZE": str(limit),
        # NCBI asks for these by name so it can make contact about a problem.
        "tool": "paper-writing-aids-sequence", "email": "",
    })
    body = getattr(resp, "text", "") or ""
    rid = _RID_RE.search(body)
    if not rid:
        raise SourceUnreachable(
            "blast", "the Put returned no RID. Body: %s" % body[:200])
    rtoe = _RTOE_RE.search(body)
    return {"rid": rid.group(1),
            "estimated_seconds": int(rtoe.group(1)) if rtoe else 0,
            "program": program, "database": db, "limit": limit,
            "submitted": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "notes": [GAP_CAUTION]}


def blast_status(rid: str) -> dict:
    """WAITING, READY, FAILED or UNKNOWN - the service's word, not ours."""
    resp = _request("blast", "GET", BASES["blast"], given=rid,
                    throttle_key="blast_poll",
                    params={"CMD": "Get", "FORMAT_OBJECT": "SearchInfo",
                            "RID": rid})
    body = getattr(resp, "text", "") or ""
    m = _STATUS_RE.search(body)
    h = _HITS_RE.search(body)
    status = (m.group(1) if m else "UNKNOWN").upper()
    return {"rid": rid, "status": status,
            "has_hits": (h.group(1).lower() == "yes") if h else None}


def _hit_records(payload: dict, query_len: int, limit: int) -> list[dict]:
    """JSON2 -> the normalized record, at the rank the service returned."""
    out: list[dict] = []
    report = (((payload or {}).get("BlastOutput2") or [{}])[0]
              .get("report") or {})
    results = ((report.get("results") or {}).get("search") or {})
    qlen = int(results.get("query_len") or query_len or 0)
    for rank, hit in enumerate(results.get("hits") or [], start=1):
        desc = (hit.get("description") or [{}])[0]
        hsps = hit.get("hsps") or [{}]
        best = hsps[0]
        align_len = int(best.get("align_len") or 0)
        ident = int(best.get("identity") or 0)
        qfrom, qto = int(best.get("query_from") or 0), int(best.get("query_to") or 0)
        cover = (100.0 * (abs(qto - qfrom) + 1) / qlen) if qlen else 0.0
        out.append(blast_hit(
            accession=desc.get("accession", ""),
            title=desc.get("title", ""),
            organism=desc.get("sciname", ""),
            identity_pct=(100.0 * ident / align_len) if align_len else 0.0,
            query_coverage_pct=cover,
            evalue=float(best.get("evalue") or 0.0),
            bit_score=float(best.get("bit_score") or 0.0),
            align_length=align_len,
            query_span=(qfrom, qto),
            subject_span=(int(best.get("hit_from") or 0),
                          int(best.get("hit_to") or 0)),
            rank=rank))
        if len(out) >= limit:
            break
    return out


def blast_fetch(rid: str, limit: int = 50) -> dict:
    """The hits for a finished RID, normalized, with the database version."""
    resp = _request("blast", "GET", BASES["blast"], given=rid,
                    throttle_key="blast_poll",
                    params={"CMD": "Get", "FORMAT_TYPE": "JSON2_S",
                            "RID": rid})
    try:
        payload = resp.json()            # type: ignore[union-attr]
    except ValueError:
        raise SourceUnreachable("blast", "results were not JSON")
    report = (((payload or {}).get("BlastOutput2") or [{}])[0]
              .get("report") or {})
    search = (report.get("results") or {}).get("search") or {}
    db = (report.get("search_target") or {}).get("db", "")
    version = report.get("version", "")
    hits = _hit_records(payload, int(search.get("query_len") or 0), limit)
    return {
        "rid": rid, "hits": hits, "hit_count": len(hits),
        "query_len": int(search.get("query_len") or 0),
        "query_title": search.get("query_title", ""),
        # `nr` last month and `nr` today are different databases.
        "database": db or "unknown",
        "database_version": version or "",
        "program_version": report.get("version", ""),
        "notes": [GAP_CAUTION] + ([] if version else [
            "the service did not return a database version string, so this "
            "search is dated but not versioned"]),
    }


def blast(query: str, program: str = "blastp", db: str = "nr",
          limit: int = 50, submit_only: bool = False, rid: str = "",
          timeout: int = 600, project: str = "") -> dict:
    """Submit, or check, or submit-then-poll. Never silently blocking."""
    out: dict = {"program": program, "database": db, "notes": [GAP_CAUTION],
                 "recorded": ""}
    try:
        if rid:
            st = blast_status(rid)
            out.update(st)
            if st["status"] != "READY":
                out["hits"] = []
                out["note"] = ("not finished. Poll again - NCBI asks for no "
                               "more than one poll per RID per minute")
                return out
            out.update(blast_fetch(rid, limit))
            out["status"] = "READY"
        else:
            put = blast_submit(query, program, db, limit)
            out.update(put)
            if submit_only:
                out["status"] = "SUBMITTED"
                out["note"] = ("the RID is yours to check with `blast --check "
                               "%s`. Results usually arrive in %s seconds"
                               % (put["rid"], put["estimated_seconds"] or "a "
                                  "few hundred"))
                return out
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                st = blast_status(put["rid"])
                if st["status"] == "READY":
                    out.update(blast_fetch(put["rid"], limit))
                    out["status"] = "READY"
                    break
                if st["status"] == "FAILED":
                    out["status"] = "FAILED"
                    out["hits"] = []
                    break
            else:
                out["status"] = "WAITING"
                out["hits"] = []
                out["note"] = ("still running after %ds. The RID is recorded; "
                               "`blast --check %s` picks it up"
                               % (timeout, put["rid"]))
    except (SourceUnreachable, InvalidIdentifier) as exc:
        # A failed source DEGRADES the record and says so. It never aborts,
        # and it never comes back looking like an empty result.
        out.setdefault("hits", [])
        out["status"] = "ERROR"
        out["error"] = str(exc)
        out["notes"].append(
            "this is a source failure, not an absence of homologs")
        return out

    if project:
        out["recorded"] = record_search(project, {
            "kind": "blast", "date": time.strftime("%Y-%m-%d"),
            "query": query[:2000], "query_id": query_hash(query or out.get("rid", "")),
            "program": program, "database": out.get("database", db),
            "database_version": out.get("database_version", ""),
            "rid": out.get("rid", ""), "status": out.get("status", ""),
            "hit_count": len(out.get("hits", [])),
            "hits": out.get("hits", []),
            "notes": out.get("notes", []),
        })
    return out


# ---------------------------------------------------------------------------
# neighbors - the operon question
# ---------------------------------------------------------------------------
#
# Bacteriophage work is a worked EXAMPLE of this, not a subcommand of its own:
# the engine stays general, and a phage genome is a contig like any other.

_CODED_BY_RE = re.compile(
    r"coded_by=\"?(?:complement\()?([A-Za-z0-9_.]+):(?:<)?(\d+)\.\.(?:>)?(\d+)")

# How wide a nucleotide window to fetch per gene of `--window`. A bacterial
# CDS plus its spacer runs around a kilobase; five is deliberately generous,
# because a window that is too small returns fewer genes than were asked for
# and says nothing about why.
NT_PER_GENE = 5000


def _efetch(db: str, ident: str, rettype: str, retmode: str = "text",
            extra: dict | None = None) -> str:
    params = {"db": db, "id": ident, "rettype": rettype, "retmode": retmode,
              "tool": "paper-writing-aids-sequence"}
    params.update(extra or {})
    resp = _request("eutils", "GET", BASES["eutils"] + "/efetch.fcgi",
                    given=ident, params=params)
    return getattr(resp, "text", "") or ""


def locate(accession: str) -> dict:
    """Where on which contig this accession lives.

    A protein accession is resolved through its GenPept record's `coded_by`,
    which names the contig and the coordinates in one string. A nucleotide
    accession is already the contig, and is returned as itself with no
    coordinates - which the caller has to handle rather than guess at.
    """
    out = {"accession": accession, "contig": "", "start": 0, "end": 0,
           "kind": "", "notes": []}
    gp = _efetch("protein", accession, "gp")
    m = _CODED_BY_RE.search(gp or "")
    if m:
        out.update({"kind": "protein", "contig": m.group(1),
                    "start": int(m.group(2)), "end": int(m.group(3))})
        return out
    if gp.strip():
        out["kind"] = "protein"
        out["notes"].append(
            "this protein record carries no `coded_by`, so nothing here knows "
            "where on a contig it sits - a neighbourhood cannot be walked "
            "from it. This is a fact about the record, not a failure")
        return out
    out["kind"] = "nucleotide"
    out["contig"] = accession
    out["notes"].append(
        "read as a nucleotide accession. Pass --start and --end to centre the "
        "window, or the window is taken from the start of the record")
    return out


_FT_FEATURE_RE = re.compile(r"^(<?\d+)\t(>?\d+)\t(\w+)$")
_FT_QUAL_RE = re.compile(r"^\t\t\t(\w+)\t(.*)$")


def parse_feature_table(text: str, contig: str, centre: int) -> list[dict]:
    """The CDS features of a fetched window, as neighbour records.

    NCBI's `rettype=ft` is a three-column tab file with qualifier lines
    indented by three tabs. Parsed rather than regex-swept for the reason
    every XML path in this toolkit is anchored to its true parent: a sweep
    picks up the qualifiers of whichever feature happened to be nearest.
    """
    out: list[dict] = []
    cur: dict | None = None
    for line in (text or "").splitlines():
        m = _FT_FEATURE_RE.match(line)
        if m:
            if cur:
                out.append(cur)
                cur = None
            a, b, kind = m.group(1), m.group(2), m.group(3)
            if kind != "CDS":
                continue
            start, end = int(a.lstrip("<")), int(b.lstrip(">"))
            strand = "+" if end >= start else "-"
            lo, hi = min(start, end), max(start, end)
            mid = (lo + hi) // 2
            cur = neighbour(contig=contig, strand=strand, start=lo, end=hi,
                            distance_nt=mid - centre)
            continue
        q = _FT_QUAL_RE.match(line)
        if q and cur is not None:
            key, val = q.group(1), q.group(2)
            if key == "locus_tag":
                cur["locus_tag"] = val
            elif key == "product":
                cur["product"] = val
            elif key == "protein_id":
                cur["protein_id"] = val
    if cur:
        out.append(cur)
    return out


def neighbors(accession: str, window: int = 10, project: str = "",
              start: int = 0, end: int = 0) -> dict:
    """What else is in this region. The operon ask, and it answers no question.

    Returns the nearest `window` coding sequences either side, each with its
    locus tag, product, strand and signed distance. It reports the nucleotide
    span it actually read, so the same call can be made again and compared.
    """
    out: dict = {"accession": accession, "window": window, "neighbors": [],
                 "notes": [GAP_CAUTION, SEEDS_QUESTIONS], "recorded": ""}
    try:
        loc = locate(accession) if not (start and end) else {
            "accession": accession, "contig": accession, "start": start,
            "end": end, "kind": "nucleotide", "notes": []}
        out["contig"] = loc["contig"]
        out["notes"] = loc["notes"] + out["notes"]
        if not loc["contig"] or not (loc["start"] or start):
            out["status"] = "no_coordinates"
            out["note"] = ("nothing here knows where this accession sits on a "
                           "contig, so no neighbourhood was walked")
            return out
        lo = max(1, int(loc["start"]) - window * NT_PER_GENE)
        hi = int(loc["end"] or loc["start"]) + window * NT_PER_GENE
        centre = (int(loc["start"]) + int(loc["end"] or loc["start"])) // 2
        ft = _efetch("nuccore", loc["contig"], "ft", "text",
                     {"seq_start": str(lo), "seq_stop": str(hi)})
        found = parse_feature_table(ft, loc["contig"], centre - lo + 1)
        upstream = sorted([n for n in found if n["distance_nt"] < 0],
                          key=lambda n: -n["distance_nt"])[:window]
        downstream = sorted([n for n in found if n["distance_nt"] > 0],
                            key=lambda n: n["distance_nt"])[:window]
        out["neighbors"] = sorted(upstream + downstream,
                                  key=lambda n: n["distance_nt"])
        out["span"] = {"contig": loc["contig"], "from": lo, "to": hi,
                       "nt_per_gene": NT_PER_GENE}
        out["status"] = "OK"
    except (SourceUnreachable, InvalidIdentifier) as exc:
        out["status"] = "ERROR"
        out["error"] = str(exc)
        out["notes"].append(
            "this is a source failure, not an empty neighbourhood")
        return out

    if project:
        out["recorded"] = record_search(project, {
            "kind": "neighbors", "date": time.strftime("%Y-%m-%d"),
            "query": accession, "query_id": query_hash(accession),
            "database": "nuccore", "database_version": "",
            "span": out.get("span", {}),
            "hit_count": len(out["neighbors"]),
            "neighbors": out["neighbors"], "notes": out["notes"],
        })
    return out


# ---------------------------------------------------------------------------
# fold - report the gap, THEN offer
# ---------------------------------------------------------------------------
#
# structure.py alphafold answers "is there a model". This answers "and if not,
# what now", and the user's decision on 2026-09-18 was: report the gap, then
# OFFER to submit and wait. Not silence, and not automatic folding.

FOLD_ROUTES = [
    {"name": "AlphaFold DB", "what": "a model that already exists",
     "url": "https://alphafold.ebi.ac.uk", "credential": "none",
     "wait": "seconds"},
    {"name": "AlphaFold Server", "what": "fold it yourself, in a browser",
     "url": "https://alphafoldserver.com", "credential": "an account",
     "wait": "minutes to hours"},
    {"name": "ColabFold", "what": "fold it yourself, in a notebook",
     "url": "https://github.com/sokrypton/ColabFold",
     "credential": "a Google account", "wait": "minutes to hours"},
    {"name": "an ESMFold API endpoint",
     "what": "fold it through this engine, on `--submit`",
     "url": "https://build.nvidia.com/meta/esmfold",
     "credential": "$PWA_FOLD_TOKEN", "wait": "minutes"},
]


def fold_token() -> tuple[str, str]:
    """(token, source). An empty token is a NORMAL state, never an error.

    Resolved the way learn.py resolves its own, and for the reason
    specs/distribution.md 5.6 states once: nothing a user supplies ever lives
    in a directory an update may replace. `tools/.env` stays last so a source
    checkout behaves as it always has.
    """
    env = os.environ.get("PWA_FOLD_TOKEN")
    if env:
        return env, "env"
    data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if data:
        path = os.path.join(data, "credentials.env")
        tok = _token_in(path)
        if tok:
            return tok, "plugin_data"
    tok = _token_in(os.path.join(HERE, ".env"))
    if tok:
        return tok, "toolkit"
    return "", "unset"


def _token_in(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip().startswith("PWA_FOLD_TOKEN="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


def _structure_alphafold(accession: str) -> dict:
    """structure.py, by subprocess - the way review.py reaches scholar.py.

    Not imported. The engines stay independent programs, and a caller with no
    Python of its own can reproduce exactly this call.
    """
    engine = os.path.join(HERE, "structure.py")
    run = subprocess.run(
        [sys.executable, engine, "alphafold", "--uniprot", accession,
         "--json"], capture_output=True, text=True, encoding="utf-8",
        errors="replace")
    if not (run.stdout or "").strip():
        return {"error": "structure.py alphafold: %s"
                         % ((run.stderr or "no output").strip()[:200])}
    try:
        return json.loads(run.stdout)
    except ValueError as exc:
        return {"error": "structure.py alphafold returned unparseable JSON: %s"
                         % exc}


def fold(accession: str = "", sequence: str = "", submit: bool = False,
         handle: str = "", project: str = "") -> dict:
    """Is there a model, and if not, what now.

    In order, and the order is the answer:

      1. Look AlphaFold DB up through structure.py. A hit returns the model
         and stops.
      2. A miss returns status `no_model` with the sequence, its length and
         the routes that could produce one. THIS IS A REAL ANSWER and the
         command exits 0.
      3. Then it OFFERS. The offer names what submission costs - a service, a
         credential, and a wait measured in minutes to hours - and submits
         only on an explicit --submit. Nothing folds because a lookup missed.
    """
    out: dict = {"accession": accession, "status": "", "notes": [GAP_CAUTION],
                 "offer": None, "refused": "", "recorded": ""}
    if handle:
        return fold_check(handle, project)

    if accession:
        af = _structure_alphafold(accession)
        # structure.py's OWN contract, read rather than assumed. It answers
        # with a `status` of ok | no_model | unreachable | invalid_identifier
        # and a `records` list - and the whole reason it separates those four
        # is that collapsing them turns "the service was down" and "you typed
        # a UniProt ID" into "there is no model".
        st = af.get("status", "")
        if af.get("error") or st in ("unreachable", "invalid_identifier"):
            out["status"] = "ERROR"
            out["error"] = af.get("error") or "; ".join(af.get("notes", []))
            out["lookup_status"] = st
            out["notes"].append(
                "the lookup did not run or the identifier was refused, so "
                "`no_model` was never established - this is a source or input "
                "failure and not an absence of a model")
            return out
        records = af.get("records") or []
        if st == "ok" and records:
            out["status"] = "model_exists"
            out["models"] = records
            out["isoforms"] = af.get("isoforms", [])
            out["note"] = ("AlphaFold DB already has a model. A predicted "
                           "structure is always reported as predicted")
            out["notes"] += af.get("notes", [])
            return out
        # A miss. structure.py's record carries a LENGTH but never the
        # sequence string, and on a miss it carries no record at all - so an
        # accession alone cannot be folded and this says so rather than
        # inventing a sequence.
        out["notes"] += [n for n in af.get("notes", []) if n != GAP_CAUTION]

    seq = re.sub(r"^>.*$", "", sequence or "", flags=re.M)
    seq = re.sub(r"\s+", "", seq)
    out["sequence"] = seq
    out["length"] = len(seq)
    out["status"] = "no_model"
    out["routes"] = FOLD_ROUTES
    # The OFFER. Named costs, because an offer whose price is unstated is a
    # decision the user cannot make.
    out["offer"] = {
        "what": "submit this sequence to a folding service and wait",
        "costs": ["a third-party service", "a credential",
                  "a wait measured in minutes to hours"],
        "how": "re-run with --submit. Nothing folds because a lookup missed",
        "credential": "PWA_FOLD_TOKEN",
    }
    out["notes"].append(
        "no model is a REAL ANSWER about AlphaFold DB's coverage. It is not "
        "evidence that the protein is unstudied, and it is not a gap")

    if not submit:
        return out

    if not seq:
        # An accession is not a sequence. `structure.py alphafold` carries a
        # residue count in its record and never the residues, and on a miss it
        # carries no record at all - so there is nothing here to fold, and
        # fetching one would make this engine a second UniProt client beside
        # the one that already exists.
        out["refused"] = (
            "nothing was submitted: an accession is not a sequence, and "
            "AlphaFold DB has no record to take one from. Pass "
            "--sequence <file.fasta> with the residues to fold")
        return out

    token, source = fold_token()
    if not token:
        # Refuses by SAYING the credential is not configured and how to
        # configure it, and never by failing at the network.
        out["refused"] = (
            "no folding credential is configured, so nothing was submitted. "
            "Set PWA_FOLD_TOKEN in the environment, or put a "
            "`PWA_FOLD_TOKEN=...` line in ${CLAUDE_PLUGIN_DATA}/"
            "credentials.env. The sequence and the routes above are the whole "
            "answer until then")
        out["credential_source"] = source
        return out

    out["credential_source"] = source
    out.update(fold_submit(seq, token, project))
    return out


# This engine has never been run against a live folding endpoint, because
# every route found on 2026-09-18 needs an account and this machine has none.
# The request is written to the documented shape and the payload SAYS it is
# unverified - a wrong answer at exit 0 is worse than the refusal it replaced,
# and "we submitted it" when nothing was submitted is exactly that.
FOLD_VERIFIED = False


def fold_submit(sequence: str, token: str, project: str = "") -> dict:
    """Submit one sequence and return a job handle at once.

    Same shape as `blast`: long-running work returns a handle, `--check` reads
    it, and the handle is recorded in plan/evidence/sequence/ so a session
    that ends does not lose the job.
    """
    out: dict = {"status": "submitted", "verified_against_live_service":
                 FOLD_VERIFIED}
    handle = "fold-" + query_hash(sequence + str(time.time()))
    try:
        resp = _request("fold", "POST", BASES["fold"], given=sequence[:40],
                        data={"sequence": sequence},
                        headers={"Authorization": "Bearer " + token})
        body = getattr(resp, "text", "")[:400]
        out["response"] = body
    except (SourceUnreachable, InvalidIdentifier) as exc:
        out["status"] = "ERROR"
        out["error"] = str(exc)
        out["note"] = ("the submission did not reach the service. Nothing was "
                       "folded, and no job is outstanding")
        return out
    out["handle"] = handle
    if project:
        out["recorded"] = record_search(project, {
            "kind": "fold", "date": time.strftime("%Y-%m-%d"),
            "query": sequence[:2000], "query_id": handle,
            "handle": handle, "database": "esmfold", "database_version": "",
            "status": out["status"],
            "verified_against_live_service": FOLD_VERIFIED,
            "notes": [GAP_CAUTION],
        })
    return out


def fold_check(handle: str, project: str = "") -> dict:
    """Read back a recorded fold job. Reads the project, never the service.

    A handle this project never wrote is reported as unknown rather than
    guessed at: a job nobody recorded is a job nobody can reproduce.
    """
    for rec in read_jobs(project) if project else []:
        if rec.get("handle") == handle or rec.get("query_id") == handle:
            return {"status": rec.get("status", "unknown"), "handle": handle,
                    "record": rec["_file"], "job": rec,
                    "notes": [GAP_CAUTION]}
    return {"status": "unknown_handle", "handle": handle,
            "note": ("no recorded job in plan/evidence/sequence/ carries this "
                     "handle. Pass --project, or the handle was never "
                     "recorded"),
            "notes": [GAP_CAUTION]}


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def status(project: str = "") -> dict:
    token, source = fold_token()
    return {
        "engine": "sequence.py",
        "sources": [{"name": n, "base": BASES[n], "role": SOURCE_ROLE[n],
                     "min_interval_s": MIN_INTERVAL.get(n, 1.0)}
                    for n in ALL_SOURCES],
        "etiquette": BLAST_ETIQUETTE,
        "user_agent": USER_AGENT,
        "keyless": True,
        "credential": {
            "name": "PWA_FOLD_TOKEN", "configured": bool(token),
            "source": source,
            "used_by": "fold --submit only. Every other subcommand here is "
                       "keyless, and an unconfigured credential is a normal "
                       "state rather than an error",
            "verified_against_live_service": FOLD_VERIFIED,
        },
        "evidence_dir": EVIDENCE_REL,
        "recorded_searches": len(read_jobs(project)) if project else 0,
        "rules": [GAP_CAUTION, SEEDS_QUESTIONS],
    }


# ---------------------------------------------------------------------------
# printing
# ---------------------------------------------------------------------------

def print_blast(res: dict) -> None:
    print("BLAST %s against %s - %s"
          % (res.get("program", ""), res.get("database", ""),
             res.get("status", "")))
    if res.get("error"):
        print("  DID NOT RUN: %s" % res["error"])
    if res.get("rid"):
        print("  RID %s" % res["rid"])
    if res.get("note"):
        print("  %s" % res["note"])
    for h in res.get("hits", []):
        print("  %2d  %-16s %5.1f%% id  %5.1f%% cov  E=%-9.2g  %s"
              % (h["rank"], h["accession"], h["identity_pct"],
                 h["query_coverage_pct"], h["evalue"], h["title"][:48]))
    if res.get("database_version"):
        print("  database version: %s" % res["database_version"])
    if res.get("recorded"):
        print("  recorded: %s" % res["recorded"])
    print("\n%s" % GAP_CAUTION)


def print_neighbors(res: dict) -> None:
    print("Neighbourhood of %s on %s - %s"
          % (res.get("accession", ""), res.get("contig", "?"),
             res.get("status", "")))
    if res.get("error"):
        print("  DID NOT RUN: %s" % res["error"])
    if res.get("note"):
        print("  %s" % res["note"])
    for n in res.get("neighbors", []):
        print("  %+8d nt  %s  %-16s %s"
              % (n["distance_nt"], n["strand"], n["locus_tag"] or "-",
                 (n["product"] or "-")[:52]))
    if res.get("span"):
        s = res["span"]
        print("  read %s:%d-%d" % (s["contig"], s["from"], s["to"]))
    print("\n%s\n%s" % (SEEDS_QUESTIONS, GAP_CAUTION))


def print_fold(res: dict) -> None:
    print("fold %s - %s" % (res.get("accession", ""), res.get("status", "")))
    if res.get("error"):
        print("  DID NOT RUN: %s" % res["error"])
    if res.get("status") == "model_exists":
        print("  AlphaFold DB already has a model; nothing to fold.")
    if res.get("status") == "no_model":
        print("  %d residues, and AlphaFold DB has no model for it."
              % res.get("length", 0))
        for r in res.get("routes", []):
            print("    %-24s %-34s credential: %-16s wait: %s"
                  % (r["name"], r["what"], r["credential"], r["wait"]))
        offer = res.get("offer") or {}
        if offer:
            print("\n  OFFER: %s" % offer["what"])
            print("    costs: %s" % "; ".join(offer["costs"]))
            print("    %s" % offer["how"])
    if res.get("refused"):
        print("\n  REFUSED: %s" % res["refused"])
    if res.get("handle"):
        print("  handle %s" % res["handle"])
    if not res.get("verified_against_live_service", True):
        print("  NOTE: no folding endpoint has been verified from this "
              "machine; the request is written to the documented shape and "
              "says so rather than claiming a result.")
    print("\n%s" % GAP_CAUTION)


def print_status(res: dict) -> None:
    print("%s - keyless: %s" % (res["engine"], res["keyless"]))
    for s in res["sources"]:
        print("  %-8s %-46s %.2fs  %s"
              % (s["name"], s["base"], s["min_interval_s"], s["role"][:46]))
    e = res["etiquette"]
    print("\n  BLAST etiquette (%s):" % e["source"])
    print("    one search every %.0fs; one poll per RID every %.0fs"
          % (e["submit_interval_s"], e["poll_interval_s"]))
    print("    %s" % e["bulk_advice"])
    c = res["credential"]
    print("\n  credential %s: %s (%s)"
          % (c["name"], "configured" if c["configured"] else "NOT configured",
             c["source"]))
    print("    %s" % c["used_by"])
    print("\n  evidence: %s (%d recorded search(es))"
          % (res["evidence_dir"], res["recorded_searches"]))
    for r in res["rules"]:
        print("\n  %s" % r)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _read_query(accession: str, path: str) -> str:
    if accession:
        return accession
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except OSError as exc:
            sys.exit("cannot read %s: %s" % (path, exc))
    return ""


def main() -> int:
    p = argparse.ArgumentParser(
        prog="sequence.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true",
                   help="emit JSON instead of text")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true",
                        default=argparse.SUPPRESS,
                        help="emit JSON instead of text")
    common.add_argument("--project", default="",
                        help="the project directory to record this search "
                             "into, under plan/evidence/sequence/. An idea "
                             "that cannot be reproduced is not evidence")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("blast", parents=[common],
                       help="sequence similarity - submit-then-poll, and the "
                            "CLI does not hide that")
    b.add_argument("--accession", default="")
    b.add_argument("--sequence", default="", metavar="FASTA",
                   help="a FASTA file to read the query from")
    b.add_argument("--program", default="blastp", choices=list(BLAST_PROGRAMS))
    b.add_argument("--db", default="nr")
    b.add_argument("--limit", type=int, default=50)
    b.add_argument("--submit", action="store_true",
                   help="return the RID at once and stop. A subcommand that "
                        "blocks for ten minutes with no output is one people "
                        "kill")
    b.add_argument("--check", default="", metavar="RID",
                   help="read a submitted search back")
    b.add_argument("--timeout", type=int, default=600)

    n = sub.add_parser("neighbors", parents=[common],
                       help="what else is in this region - the operon "
                            "question. It seeds questions, never findings")
    n.add_argument("--accession", required=True)
    n.add_argument("--window", type=int, default=10,
                   help="how many coding sequences either side")
    n.add_argument("--start", type=int, default=0)
    n.add_argument("--end", type=int, default=0)

    f = sub.add_parser("fold", parents=[common],
                       help="is there a model, and if not what now - reports "
                            "the gap, then OFFERS")
    f.add_argument("--accession", default="")
    f.add_argument("--sequence", default="", metavar="FASTA")
    f.add_argument("--submit", action="store_true",
                   help="submit to a folding service. Never automatic, and "
                        "refused with an explanation when no credential is "
                        "configured")
    f.add_argument("--check", default="", metavar="HANDLE")

    sub.add_parser("status", parents=[common],
                   help="sources, throttles, the credential, the rules")

    args = p.parse_args()
    as_json = getattr(args, "json", False)

    if args.cmd == "blast":
        query = _read_query(args.accession, args.sequence)
        if not query and not args.check:
            print("blast needs --accession, --sequence or --check",
                  file=sys.stderr)
            return 2
        res = blast(query, args.program, args.db, args.limit, args.submit,
                    args.check, args.timeout, args.project)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_blast(res)
        # Always 0. A search that found nothing is an answer, and a source
        # that was down is reported in the record rather than as a failure the
        # caller has to interpret.
        return 0

    if args.cmd == "neighbors":
        res = neighbors(args.accession, args.window, args.project,
                        args.start, args.end)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_neighbors(res)
        return 0

    if args.cmd == "fold":
        sequence = ""
        if args.sequence:
            sequence = _read_query("", args.sequence)
        res = fold(args.accession, sequence, args.submit, args.check,
                   args.project)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_fold(res)
        # `no_model` is a REAL ANSWER and exits 0 (4.6 step 2), and so does a
        # refusal to submit: an unconfigured credential is a state of the
        # machine, not bad input.
        return 0

    if args.cmd == "status":
        res = status(args.project)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_status(res)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
