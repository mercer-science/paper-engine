#!/usr/bin/env python3
"""
scholar.py - source-agnostic literature engine: Crossref, OpenAlex, Europe PMC,
PubMed, arXiv, Semantic Scholar and PubChem behind one normalized record and one
verification cascade.

Why it exists: PubMed is a biomedical index. Measured on 2026-09-03, five queries
drawn from real surface-science work returned zero PubMed hits on three of five -
Surface Science, Applied Surface Science, J. Vac. Sci. Technol. and Vacuum are not
indexed there at all. The toolkit's central rule ("never cite from memory - every
citation goes through verify") was therefore unsatisfiable for chemistry papers:
`not_found` meant "out of scope", not "not real". This engine closes that hole.

pubmed.py is unchanged and becomes the first backend of the cascade. Its matching,
citation-formatting and CASSI helpers are imported rather than reimplemented, so a
Crossref record cites identically to a PubMed one.

No API key is required by any source here. See specs/scholar-engine.md.

Usage:
  python scholar.py search "Cu(111) oxygen adsorption kinetics" --limit 10
  python scholar.py verify --title "The adsorption and incorporation of oxygen on Cu(100)"
  python scholar.py verify --doi 10.1016/j.susc.2005.01.038
  python scholar.py check-refs drafts/references.bib --fail-on-problem
  python scholar.py cite 10.1016/j.susc.2005.01.038 --style bibtex
  python scholar.py related 10.1016/j.susc.2005.01.038
  python scholar.py cited-by 10.1016/j.susc.2005.01.038 --limit 5
  python scholar.py references 10.1016/j.susc.2005.01.038
  python scholar.py compound "trimesic acid"
  python scholar.py status
"""

from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import re
import sys
import time
from urllib.parse import quote
from xml.etree import ElementTree

try:
    import requests
except ImportError:
    sys.exit("scholar.py requires 'requests'. Install with: python -m pip install requests")

# pubmed.py sits beside this file and is imported, never copied. Adding the
# directory to sys.path lets `python tools/scholar.py` work from anywhere.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pubmed  # noqa: E402

# Same reason as pubmed.py: Windows consoles default to cp1252 and would mangle
# or crash on the accented author names and thin spaces these APIs return.
for _stream in (sys.stdout, sys.stderr):
    # reconfigure() belongs to TextIOWrapper, not to every text stream: under a
    # pipe or a capturing harness sys.stdout may have no such method at all.
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except ValueError:  # detached or already-closed stream
            pass

pubmed._load_env_file()

# Reused verbatim from pubmed.py - one matching implementation, many sources.
normalize_title = pubmed.normalize_title
title_similarity = pubmed.title_similarity
clean_doi = pubmed.clean_doi
format_citation = pubmed.format_citation
cassi_journal = pubmed.cassi_journal
load_refs = pubmed.load_refs
_year_of = pubmed._year_of
_surnames = pubmed._surnames

# ---------------------------------------------------------------------------
# Verification thresholds
# ---------------------------------------------------------------------------
#
# DERIVED, not guessed. Crossref returns a top hit for ANY string, and its
# `score` does not separate real papers from fabricated ones - measured over the
# corpus, REAL scored 30.8-104.3 and FAKE 34.2-47.3, so the ranges overlap and
# fabrications outscore genuine papers. `score` is a Lucene relevance number
# normalized to nothing and is never a gate here.
#
# Title similarity does separate, cleanly. Re-derived by tests/scholar.py over
# 58 known-real and 22 fabricated titles:
#
#     REAL   min 0.92   median 1.00
#     FAKE   max 0.70
#
# That leaves an empty band from 0.70 to 0.92. VERIFIED_MIN sits inside it, with
# 0.15 of headroom above the best fabrication: 0 fabricated verified, 0 real
# missed. Moving it within that band changes nothing; moving it outside costs
# immediately, so it is not a free parameter.
#
# PARTIAL_MIN stays at 0.60 and is NOT derived from that corpus, deliberately.
# The corpus looks papers up by exact title, so it cannot measure the case the
# partial band exists for - a real paper whose title the user misremembered,
# which would score well below 0.92. Raising it to sit above the fabrications
# would be over-fitting to a condition never tested. The cost of leaving it low
# is display noise (7 of 22 fabrications surface as `partial`), not a bad
# citation: a partial is shown, never adopted, and never emits a DOI.
#
# Change these in tests/scholar.py, with the measurement, not here by feel.

VERIFIED_MIN = 0.85
PARTIAL_MIN = 0.60

# arXiv sits LAST on purpose. A paper that reached a journal is matched by an
# earlier index first, so arXiv only ever wins when nothing else holds the work
# - which is exactly the case it is here for, a genuine unpublished preprint.
# Reaching it earlier would let a preprint outrank its own version of record.
CASCADE = ["pubmed", "crossref", "openalex", "europepmc", "arxiv"]
# Europe PMC is a PubMed superset, so merging it into the default search would
# mostly duplicate rows already contributed by PubMed. It stays in the verify
# cascade (where preprints matter) and is reachable explicitly.
SEARCH_MERGE = ["pubmed", "crossref", "openalex", "arxiv"]

# Semantic Scholar is registered and explicitly reachable, but is deliberately
# absent from CASCADE. Measured: its keyless tier answered 429 on three of four
# consecutive calls at 4 s spacing. A source that intermittently fails to answer
# would land in `unreachable` on most runs, and `check-refs` reports any
# unreachable source as an INCOMPLETE result rather than a verdict - so putting
# it in the default order would turn nearly every reference report into "re-run
# before concluding anything". Its value is citations and recommendations, which
# `related` and `cited-by` take from it, where an occasional miss costs nothing.
ALL_SOURCES = ["pubmed", "crossref", "openalex", "europepmc", "arxiv",
               "semanticscholar"]

UA = {"User-Agent": "paper-writing-aids-scholar/1.0"}


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------
#
# Crossref advertised NO rate-limit headers in the anonymous pool (measured:
# `x-api-pool: public-array`, no x-ratelimit-* at all), so there is no published
# budget to read. With no documented limit the honest move is a conservative
# fixed throttle rather than probing for the ceiling.

MIN_INTERVAL = {
    "crossref": 1.0,
    "openalex": 1.0,
    "europepmc": 1.0,
    "pubchem": 0.2,          # PubChem publishes 5 req/s
    "nlmcatalog": 0.34,      # NCBI anonymous
    "arxiv": 3.0,            # arXiv's published courtesy rate: 1 request / 3 s
    # Measured, not chosen: at 4 s spacing Semantic Scholar still answered
    # 429 on three of four consecutive keyless calls. There is no interval that
    # makes the anonymous tier reliable, which is why this source is kept out
    # of the default cascade (see CASCADE) rather than merely slowed down.
    "semanticscholar": 3.0,
}
_last_call: dict[str, float] = {}


class SourceUnreachable(Exception):
    """A source failed to answer. Never the same thing as a paper being fake."""

    def __init__(self, source: str, reason: str):
        self.source, self.reason = source, reason
        super().__init__(f"{source} unreachable: {reason}")


def _throttle(source: str) -> None:
    interval = MIN_INTERVAL.get(source, 1.0)
    delta = time.monotonic() - _last_call.get(source, 0.0)
    if delta < interval:
        time.sleep(interval - delta)
    _last_call[source] = time.monotonic()


# A 429 that carries a long `Retry-After` is a quota lockout, not a busy server.
# Measured 2026-09-06: OpenAlex's anonymous pool answered 0 of 8 calls with
# `Retry-After: 2167` counting down to a fixed reset. The shipped backoff -
# 1.5 s, 3 s, 4.5 s - could not have succeeded, spent three more requests
# against an exhausted quota, and then reported "failed after 3 tries (HTTP
# 429)", which reads as an outage. The engine already refuses to report a
# missing paper as a dead index; this is the same distinction one level down,
# because the remedy differs completely: wait until the reset, or investigate.
RETRY_AFTER_BUDGET = 60.0


def _retry_after(resp) -> float:
    """Seconds the server asked us to wait, or 0.0 if it did not say."""
    raw = (resp.headers.get("Retry-After") or "").strip()
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        # The header also allows an HTTP-date. Not worth parsing for a hint.
        return 0.0


def _lockout(source: str, wait: float) -> "SourceUnreachable":
    mins = wait / 60.0
    return SourceUnreachable(
        source,
        f"rate-limited: the server asked for {wait:.0f}s "
        f"({mins:.0f} min) before the next request. This is a quota lockout, "
        f"not an outage - the index is up and will answer again after it "
        f"resets. Re-run then; do not treat these references as unverifiable.")


def _get(source: str, url: str, params: dict | None = None,
         timeout: int = 30, retries: int = 3):
    """GET and parse JSON.

    Returns the payload, or None for a clean 404 (the record does not exist).
    Raises SourceUnreachable when the source itself failed - a caller must be
    able to tell "this index has no such paper" from "this index did not
    answer", because reporting the second as the first is how a real citation
    gets called fake.
    """
    last = ""
    for attempt in range(retries):
        _throttle(source)
        try:
            resp = requests.get(url, params=params, headers=UA, timeout=timeout)
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(1.5 * (attempt + 1))
            continue
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                raise SourceUnreachable(source, "returned a non-JSON body")
        if resp.status_code == 404:
            return None
        if resp.status_code in (429, 500, 502, 503, 504):
            wait = _retry_after(resp)
            if wait > RETRY_AFTER_BUDGET:
                raise _lockout(source, wait)
            last = f"HTTP {resp.status_code}"
            time.sleep(wait if wait else 1.5 * (attempt + 1))
            continue
        raise SourceUnreachable(source, f"HTTP {resp.status_code}: {resp.text[:200]}")
    raise SourceUnreachable(source, f"failed after {retries} tries ({last})")


def _get_xml(source: str, url: str, params: dict | None = None,
             timeout: int = 45, retries: int = 3):
    """GET and parse an Atom/XML body, under `_get`'s exact contract.

    arXiv is the only source here that does not speak JSON, so it needs its own
    fetch. Everything else is deliberately identical: a clean 404 returns None
    (no such record) and every other failure raises SourceUnreachable, because
    a source that did not answer must never be reported as a paper that does
    not exist.
    """
    last = ""
    for attempt in range(retries):
        _throttle(source)
        try:
            resp = requests.get(url, params=params, headers=UA, timeout=timeout)
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(1.5 * (attempt + 1))
            continue
        if resp.status_code == 200:
            try:
                return ElementTree.fromstring(resp.content)
            except ElementTree.ParseError as exc:
                raise SourceUnreachable(source, f"returned unparseable XML: {exc}")
        if resp.status_code == 404:
            return None
        if resp.status_code in (429, 500, 502, 503, 504):
            wait = _retry_after(resp)
            if wait > RETRY_AFTER_BUDGET:
                raise _lockout(source, wait)
            last = f"HTTP {resp.status_code}"
            time.sleep(wait if wait else 1.5 * (attempt + 1))
            continue
        raise SourceUnreachable(source, f"HTTP {resp.status_code}: {resp.text[:200]}")
    raise SourceUnreachable(source, f"failed after {retries} tries ({last})")


# ---------------------------------------------------------------------------
# The normalized record
# ---------------------------------------------------------------------------

RECORD_DEFAULTS = {
    "source": "", "source_id": "", "title": "", "authors": [],
    "journal": "", "journal_abbrev": "", "issn": "", "year": "",
    "volume": "", "issue": "", "pages": "", "elocation": "",
    "doi": "", "pmid": "", "pmc": "", "abstract": "",
    "is_retracted": False, "notices": [], "pubtypes": [],
    "cited_by_count": None, "is_oa": None, "url": "",
}


def blank_record(**kw) -> dict:
    """A record with every key present. Absent values stay ""/[] - never None,
    never guessed, and never filled in from a different record."""
    rec = {k: (list(v) if isinstance(v, list) else v)
           for k, v in RECORD_DEFAULTS.items()}
    rec.update(kw)
    return rec


def _initials(given: str) -> str:
    """'Daniel G.' -> 'DG', 'A.' -> 'A', 'Yann' -> 'Y'. PubMed's author shape."""
    return "".join(part[0].upper() for part in re.split(r"[\s.\-]+", given or "")
                   if part and part[0].isalpha())


def _name_to_pubmed_style(display: str) -> str:
    """'Aloysius Soon' -> 'Soon A'.

    Best effort only, and deliberately so: a source that hands over a display
    name has already thrown away the surname boundary, and no rule recovers
    'van der Waals' from it reliably. Callers are told where the name came from
    rather than being handed a guess that looks authoritative.
    """
    display = (display or "").strip()
    if not display or "," in display:
        return display
    parts = display.split()
    if len(parts) < 2:
        return display
    return f"{parts[-1]} {_initials(' '.join(parts[:-1]))}".strip()


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_jats(raw: str) -> str:
    """Crossref abstracts arrive as JATS XML fragments."""
    if not raw:
        return ""
    text = re.sub(r"<jats:title>.*?</jats:title>", " ", raw, flags=re.S | re.I)
    text = _TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


# ---------------------------------------------------------------------------
# Journal abbreviation resolution
# ---------------------------------------------------------------------------
#
# Measured, and the reason this function exists rather than a one-line mapping:
# Crossref's `short-container-title` is NOT an ISO abbreviation. For DOI
# 10.1016/j.susc.2005.01.038 it returns "Surface Science" - the full title - and
# for 10.1021/jacs.9b09323 it returns nothing at all. Writing that into
# `journal_abbrev` would hand cassi_journal() a full title labelled as an
# abbreviation, and the citation would read "Surface Science. 2005;579" where
# the PubMed path writes "Surf. Sci. 2005, 579". A wrong abbreviation looks
# exactly like a right one, which is the failure this toolkit exists to stop.
#
# The NLM Catalog answers by ISSN, keylessly, with the same `medlineta` string
# PubMed's own ISOAbbreviation comes from - so both paths converge instead of
# drifting. Measured: 0039-6028 -> "Surf Sci", 0002-7863 -> "J Am Chem Soc",
# 0169-4332 -> "Appl Surf Sci", 0734-2101 -> "J Vac Sci Technol A". The catalog
# covers journals whose articles PubMed does not index, which is exactly the
# case that needed solving.
#
# It costs two NCBI round-trips per journal, so it is lazy and cached: applied
# where a citation is actually being written, not to every row of a search.

_ABBREV_CACHE: dict[str, str] = {}


def iso_abbrev_for_issn(issn: str) -> str:
    """The NLM/ISO abbreviation for an ISSN, or "" if the catalog has none."""
    issn = (issn or "").strip()
    if not issn:
        return ""
    if issn in _ABBREV_CACHE:
        return _ABBREV_CACHE[issn]
    abbrev = ""
    try:
        hits = pubmed._request("esearch.fcgi",
                               {"db": "nlmcatalog", "term": f'"{issn}"[ISSN]',
                                "retmode": "json", "retmax": 5}).json()
        ids = hits.get("esearchresult", {}).get("idlist", [])
        if ids:
            raw = pubmed._request("esummary.fcgi",
                                  {"db": "nlmcatalog", "id": ",".join(ids),
                                   "retmode": "json"}).json().get("result", {})
            for uid in ids:
                ta = (raw.get(uid, {}) or {}).get("medlineta", "").strip()
                if ta:
                    abbrev = ta
                    break
    except Exception:  # noqa: BLE001 - an absent abbreviation is reported, not fatal
        abbrev = ""
    _ABBREV_CACHE[issn] = abbrev
    return abbrev


def enrich_abbrev(rec: dict) -> dict:
    """Fill journal_abbrev from the NLM catalog when the source had none.

    Mutates and returns rec. A journal the catalog does not list keeps
    journal_abbrev "", and cassi_journal() then emits its existing
    "replace it by hand before submission" note rather than inventing a form.
    """
    if rec.get("journal_abbrev") or not rec.get("issn"):
        return rec
    rec["journal_abbrev"] = iso_abbrev_for_issn(rec["issn"])
    return rec


# ---------------------------------------------------------------------------
# Source adapters
# ---------------------------------------------------------------------------
#
# Each adapter is uniform: search / by_doi / by_id / by_title / related /
# cited_by / available. Adding a source later is one adapter plus one registry
# line - no change to the cascade, the CLI, or any skill.


class Source:
    """The routes every index answers, in normalized-record form.

    A stub here is the honest default for a route a source does not offer -
    an empty result, never an exception - so a subclass overrides only what
    its API actually has. The annotations are the contract: they say a lookup
    yields a record or nothing, which is what makes `if not rec` a real
    narrowing rather than a dead branch.
    """

    name = ""
    needs_key = False
    # Whether this source offers a usable free-text search route. False is a
    # statement about the API, not about this code: Semantic Scholar's search
    # endpoint is closed to keyless callers, so the honest thing is to declare
    # that and skip it, rather than spend a request to be told 429 again.
    has_search = True
    # The endpoint the subclass talks to, declared here so a caller can swap
    # it on any source (the degradation suite points one at an invalid host).
    # Empty on PubMed, which reaches NCBI through pubmed.py, not a URL.
    BASE: str = ""

    # `offset` is how many rows to skip, in the caller's own row counting -
    # every source translates it into whatever its API actually does (an
    # offset, a page number, a start index). It is declared on the base
    # class rather than sniffed with **kwargs so that a source which cannot
    # page says so by ignoring it in one visible place (review-paper 6).
    paging = False

    def search(self, term, limit=10, year_from="", year_to="",
               offset=0) -> list[dict]:
        return []

    def search_pages(self, term, limit=10, year_from="", year_to="",
                     pages=1):
        """Yield consecutive pages of results, in this source's own idiom.

        A generator rather than a list, so that a source failing on page 3
        still hands back pages 1 and 2: a deep sweep must never be worse
        than a shallow one because the last request timed out.

        The default is the row-offset loop every source but Europe PMC can
        use. Europe PMC overrides it, because ITS paging parameter is a
        cursor and `page` is silently ignored - measured 2026-09-10, where
        `page=2` returned the identical 25 rows and the sweep reported two
        pages read. A paging parameter the server discards is this
        toolkit's founding failure shape: the request succeeded, the count
        went up, and the corpus stopped at 25.
        """
        for page in range(max(1, int(pages))):
            got = self.search(term, limit=limit, year_from=year_from,
                              year_to=year_to, offset=page * limit)
            yield got
            # A short page means the index is out of results; asking again
            # spends a request to be told the same thing.
            if len(got) < limit:
                return

    def by_doi(self, doi) -> dict | None:
        return None

    def by_id(self, ident) -> dict | None:
        return None

    def related(self, ident, limit=10) -> list[dict]:
        return []

    def cited_by(self, ident, limit=10) -> list[dict]:
        return []

    def by_title(self, title, limit=5) -> list[dict]:
        return self.search(title, limit=limit)

    def references(self, ident, limit=10) -> list[dict]:
        """What this paper cites. Only Semantic Scholar answers it today; an
        empty list is the honest default for every source that does not."""
        return []

    def available(self) -> tuple[bool, str]:
        return (False, "not implemented")


# --- Crossref --------------------------------------------------------------

class Crossref(Source):
    name = "crossref"
    BASE = "https://api.crossref.org/works"
    paging = True

    # Not citable as a paper, and dangerous to leave in a result list.
    #
    # Measured: searching Crossref for the exact title of a real Langmuir paper
    # returns `10.1021/acs.langmuir.8b03150.s001` - the supplementary-material
    # file - ABOVE `10.1021/acs.langmuir.8b03150`, the article itself. A
    # `component` inherits its parent's title verbatim, so title similarity
    # cannot separate them and the SI file wins the sort. Left unfiltered,
    # verify() adopts the .s001 DOI and reports it `verified`: a real-looking,
    # confidently wrong identifier in the bibliography, which is the exact
    # failure this engine exists to prevent.
    NOT_CITABLE = {"component", "peer-review"}

    # What a free-text search ASKS FOR, as opposed to what it throws away
    # afterwards. Measured 2026-09-10 on `self-assembled monolayers on gold
    # surfaces`, rows=25:
    #
    #   no filter                 25 rows: 24 component, 1 book-chapter
    #   filter=type:journal-article
    #                             25 rows: 25 journal-article
    #
    # So the unfiltered route spent a whole page of quota on a paper's
    # figures and supplementary files and handed back one citable record -
    # reported, at exit 0, as a field with one paper in it. Reading rows
    # AFTER the filter is the same defect shape as anchoring an XML path
    # with `.//`: the request was for the wrong thing and the filtering hid
    # it (specs/review-paper.md 6).
    #
    # It is journal-article alone rather than an OR over every citable type,
    # and that is measured too: asking for
    # journal-article,proceedings-article,book-chapter,posted-content
    # returned 17 journal articles, 7 book chapters and 1 preprint for the
    # same 25 rows - the wider ask DISPLACES journal articles rather than
    # adding to them. Preprints come from arXiv and Europe PMC, which are in
    # the merge for exactly that reason.
    SEARCH_TYPES = ("journal-article",)

    def _record(self, item: dict) -> dict:
        titles = item.get("title") or []
        containers = item.get("container-title") or []
        issns = item.get("ISSN") or []
        authors = []
        for a in item.get("author") or []:
            fam, given = (a.get("family") or "").strip(), a.get("given") or ""
            if fam:
                authors.append(f"{fam} {_initials(given)}".strip())
            elif a.get("name"):
                authors.append(a["name"].strip())
        issued = (item.get("issued") or {}).get("date-parts") or [[]]
        year = str(issued[0][0]) if issued and issued[0] and issued[0][0] else ""
        if not year:
            for key in ("published-print", "published-online", "created"):
                parts = (item.get(key) or {}).get("date-parts") or [[]]
                if parts and parts[0] and parts[0][0]:
                    year = str(parts[0][0])
                    break
        doi = clean_doi(item.get("DOI", ""))

        # Measured: the retracted paper carries updated-by=[('retraction', ...)]
        # and the notice itself carries update-to pointing back. So updated-by
        # is what says "this paper was retracted"; update-to says "this paper IS
        # the notice". Crossref repeats each entry, hence the dedupe.
        notices, retracted = [], False
        seen = set()
        for upd in item.get("updated-by") or []:
            utype = (upd.get("type") or "").lower()
            key = (utype, upd.get("DOI", ""))
            if key in seen:
                continue
            seen.add(key)
            # Crossref's label is often the bare word "Retraction", which says
            # nothing a reader can follow up. The notice's own DOI is the part
            # worth carrying.
            ref = " ".join(x for x in (upd.get("label", ""), upd.get("DOI", "")) if x)
            if "retraction" in utype:
                retracted = True
                notices.append({"type": "RetractionIn", "ref": ref,
                                "doi": upd.get("DOI", ""), "pmid": ""})
            elif "concern" in utype:
                notices.append({"type": "ExpressionOfConcernIn", "ref": ref,
                                "doi": upd.get("DOI", ""), "pmid": ""})
            elif "correction" in utype or "erratum" in utype:
                notices.append({"type": "ErratumIn", "ref": ref,
                                "doi": upd.get("DOI", ""), "pmid": ""})
        if (item.get("type") or "") == "retraction":
            retracted = True

        return blank_record(
            source=self.name, source_id=doi,
            title=(titles[0] if titles else "").strip().rstrip("."),
            authors=authors,
            journal=(containers[0] if containers else "").strip(),
            # short-container-title is NOT an abbreviation (see above) and is
            # deliberately not read. enrich_abbrev() resolves it by ISSN.
            journal_abbrev="",
            issn=(issns[0] if issns else ""),
            year=year,
            volume=str(item.get("volume") or ""),
            issue=str(item.get("issue") or ""),
            pages=str(item.get("page") or ""),
            elocation=str(item.get("article-number") or ""),
            doi=doi,
            abstract=_strip_jats(item.get("abstract") or ""),
            is_retracted=retracted, notices=notices,
            pubtypes=[item.get("type")] if item.get("type") else [],
            cited_by_count=item.get("is-referenced-by-count"),
            url=f"https://doi.org/{doi}" if doi else "",
        )

    def search(self, term, limit=10, year_from="", year_to="", offset=0):
        rows = max(1, min(int(limit), 100))      # Crossref caps `rows` at 100
        params = {"query.bibliographic": term, "rows": rows}
        if offset:
            params["offset"] = int(offset)
        filt = [f"type:{t}" for t in self.SEARCH_TYPES]
        if year_from:
            filt.append(f"from-pub-date:{_year_of(year_from) or year_from}-01-01")
        if year_to:
            filt.append(f"until-pub-date:{_year_of(year_to) or year_to}-12-31")
        params["filter"] = ",".join(filt)
        data = _get(self.name, self.BASE, params)
        items = ((data or {}).get("message") or {}).get("items") or []
        # The post-filter STAYS. It is no longer doing the work, but a filter
        # the server ignores must not silently put components back in the
        # corpus, and a request parameter is a claim about somebody else's
        # server rather than a fact about this one.
        return [self._record(i) for i in items
                if (i.get("type") or "") not in self.NOT_CITABLE]

    def by_doi(self, doi):
        doi = clean_doi(doi)
        if not doi:
            return None
        data = _get(self.name, f"{self.BASE}/{doi}")
        msg = (data or {}).get("message")
        return self._record(msg) if msg else None

    def by_id(self, ident) -> dict | None:
        # Crossref's identifier for a work *is* its DOI.
        return self.by_doi(ident)

    def available(self):
        try:
            _get(self.name, self.BASE, {"query.bibliographic": "graphene", "rows": 1})
            return (True, "ok")
        except SourceUnreachable as exc:
            return (False, exc.reason)


# --- OpenAlex --------------------------------------------------------------

class OpenAlex(Source):
    name = "openalex"
    BASE = "https://api.openalex.org/works"
    paging = True

    @staticmethod
    def _abstract(inverted: dict | None) -> str:
        if not inverted:
            return ""
        slots: list[tuple[int, str]] = []
        for word, positions in inverted.items():
            for pos in positions or []:
                slots.append((pos, word))
        return " ".join(w for _, w in sorted(slots))

    def _record(self, w: dict) -> dict:
        loc = w.get("primary_location") or {}
        src = loc.get("source") or {}
        biblio = w.get("biblio") or {}
        first, last = biblio.get("first_page") or "", biblio.get("last_page") or ""
        pages = f"{first}-{last}" if first and last else (first or "")
        ids = w.get("ids") or {}
        pmid = (ids.get("pmid") or "").rsplit("/", 1)[-1] if ids.get("pmid") else ""
        pmc = (ids.get("pmcid") or "").rsplit("/", 1)[-1] if ids.get("pmcid") else ""
        oa_id = (w.get("id") or "").rsplit("/", 1)[-1]
        authors = [_name_to_pubmed_style((a.get("author") or {}).get("display_name", ""))
                   for a in w.get("authorships") or []]
        issn_list = src.get("issn") or []
        return blank_record(
            source=self.name, source_id=oa_id,
            title=(w.get("title") or "").strip().rstrip("."),
            authors=[a for a in authors if a],
            journal=src.get("display_name") or "",
            journal_abbrev=(src.get("abbreviated_title") or ""),
            issn=src.get("issn_l") or (issn_list[0] if issn_list else ""),
            year=str(w.get("publication_year") or ""),
            volume=str(biblio.get("volume") or ""),
            issue=str(biblio.get("issue") or ""),
            pages=pages,
            doi=clean_doi(w.get("doi") or ""),
            pmid=pmid, pmc=pmc,
            abstract=self._abstract(w.get("abstract_inverted_index")),
            is_retracted=bool(w.get("is_retracted")),
            notices=([{"type": "RetractionIn", "ref": "OpenAlex is_retracted flag",
                       "doi": "", "pmid": ""}] if w.get("is_retracted") else []),
            pubtypes=[w.get("type")] if w.get("type") else [],
            cited_by_count=w.get("cited_by_count"),
            is_oa=(w.get("open_access") or {}).get("is_oa"),
            url=(w.get("doi") or w.get("id") or ""),
        )

    def search(self, term, limit=10, year_from="", year_to="", offset=0):
        per = max(1, min(int(limit), 200))       # OpenAlex caps per-page at 200
        params = {"per-page": per, "search": term}
        if offset:
            # OpenAlex pages by NUMBER, not by row offset, so the caller's
            # offset only lands on a page boundary when it is a multiple of
            # the page size - which it is, because search() computes it as
            # one. Anything else is rounded down rather than silently
            # returning page 1 again.
            params["page"] = (int(offset) // per) + 1
        filt = []
        if year_from:
            filt.append(f"from_publication_date:{_year_of(year_from) or year_from}-01-01")
        if year_to:
            filt.append(f"to_publication_date:{_year_of(year_to) or year_to}-12-31")
        if filt:
            params["filter"] = ",".join(filt)
        data = _get(self.name, self.BASE, params)
        return [self._record(w) for w in (data or {}).get("results") or []]

    def by_title(self, title, limit=5):
        # title.search is a field-restricted route and finds the paper an
        # all-fields `search` buries under its citing literature.
        clean = re.sub(r"[,:]", " ", title)
        params = {"per-page": max(1, min(int(limit), 200)),
                  "filter": f"title.search:{clean}"}
        data = _get(self.name, self.BASE, params)
        return [self._record(w) for w in (data or {}).get("results") or []]

    def _raw(self, ident):
        ident = (ident or "").strip()
        if ident.lower().startswith("10.") or "doi.org" in ident.lower():
            return _get(self.name, f"{self.BASE}/doi:{clean_doi(ident)}")
        return _get(self.name, f"{self.BASE}/{ident.rsplit('/', 1)[-1]}")

    def by_doi(self, doi):
        doi = clean_doi(doi)
        if not doi:
            return None
        data = _get(self.name, f"{self.BASE}/doi:{doi}")
        return self._record(data) if data else None

    def by_id(self, ident):
        ident = (ident or "").strip()
        if not ident:
            return None
        data = self._raw(ident)
        return self._record(data) if data else None

    def _batch(self, oa_ids, limit):
        oa_ids = [i.rsplit("/", 1)[-1] for i in oa_ids][:limit]
        if not oa_ids:
            return []
        data = _get(self.name, self.BASE,
                    {"filter": "openalex_id:" + "|".join(oa_ids),
                     "per-page": max(1, min(len(oa_ids), 200))})
        return [self._record(w) for w in (data or {}).get("results") or []]

    def related(self, ident, limit=10):
        raw = self._raw(ident)
        if not raw:
            return []
        return self._batch(raw.get("related_works") or [], limit)

    def cited_by(self, ident, limit=10):
        raw = self._raw(ident)
        if not raw:
            return []
        # cited_by_api_url measured as null on real records; the cites: filter
        # is the route that actually answers.
        oa_id = (raw.get("id") or "").rsplit("/", 1)[-1]
        data = _get(self.name, self.BASE,
                    {"filter": f"cites:{oa_id}", "per-page": max(1, min(int(limit), 200)),
                     "sort": "cited_by_count:desc"})
        return [self._record(w) for w in (data or {}).get("results") or []]

    def available(self):
        try:
            _get(self.name, self.BASE, {"search": "graphene", "per-page": 1})
            return (True, "ok")
        except SourceUnreachable as exc:
            return (False, exc.reason)


# --- Europe PMC ------------------------------------------------------------

class EuropePMC(Source):
    name = "europepmc"
    BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    paging = True

    def _record(self, r: dict) -> dict:
        info = r.get("journalInfo") or {}
        journal = info.get("journal") or {}
        authors = [a.get("fullName", "").strip()
                   for a in ((r.get("authorList") or {}).get("author") or [])]
        if not authors and r.get("authorString"):
            authors = [a.strip() for a in r["authorString"].rstrip(".").split(",")
                       if a.strip()]
        notices, retracted = [], False
        for cc in ((r.get("commentCorrectionList") or {}).get("commentCorrection") or []):
            ctype = (cc.get("type") or "")
            low = ctype.lower()
            ref = cc.get("title") or ctype
            pmid = str(cc.get("id") or "")
            if "retraction in" in low:
                retracted = True
                notices.append({"type": "RetractionIn", "ref": ref, "doi": "", "pmid": pmid})
            elif "expression of concern" in low:
                notices.append({"type": "ExpressionOfConcernIn", "ref": ref,
                                "doi": "", "pmid": pmid})
            elif "erratum" in low or "correction" in low:
                notices.append({"type": "ErratumIn", "ref": ref, "doi": "", "pmid": pmid})
        pubtypes = (r.get("pubTypeList") or {}).get("pubType") or []
        if any("retract" in str(p).lower() for p in pubtypes):
            retracted = True
        is_oa = str(r.get("isOpenAccess") or "").upper()
        return blank_record(
            source=self.name, source_id=str(r.get("id") or ""),
            title=(r.get("title") or "").strip().rstrip("."),
            authors=authors,
            journal=journal.get("title") or "",
            journal_abbrev=(journal.get("isoabbreviation")
                            or journal.get("medlineAbbreviation") or ""),
            issn=journal.get("issn") or journal.get("essn") or "",
            year=str(r.get("pubYear") or info.get("yearOfPublication") or ""),
            volume=str(info.get("volume") or ""),
            issue=str(info.get("issue") or ""),
            pages=str(r.get("pageInfo") or ""),
            doi=clean_doi(r.get("doi") or ""),
            pmid=str(r.get("pmid") or ""),
            pmc=str(r.get("pmcid") or ""),
            abstract=(r.get("abstractText") or ""),
            is_retracted=retracted, notices=notices,
            pubtypes=[str(p) for p in pubtypes],
            cited_by_count=r.get("citedByCount"),
            is_oa=(True if is_oa == "Y" else (False if is_oa == "N" else None)),
            url=(f"https://europepmc.org/article/{r.get('source')}/{r.get('id')}"
                 if r.get("source") and r.get("id") else ""),
        )

    def _raw_query(self, query, limit, cursor="*"):
        """One page, plus the cursor for the next one."""
        per = max(1, min(int(limit), 100))       # Europe PMC caps it at 100
        params = {"query": query, "format": "json", "resultType": "core",
                  "pageSize": per, "cursorMark": cursor}
        data = _get(self.name, self.BASE, params) or {}
        recs = [self._record(r) for r in
                (data.get("resultList") or {}).get("result") or []]
        return recs, str(data.get("nextCursorMark") or "")

    def _query(self, query, limit, offset=0):
        recs, _ = self._raw_query(query, limit)
        return recs

    def _with_years(self, term, year_from, year_to):
        query = term
        yf, yt = _year_of(year_from) or year_from, _year_of(year_to) or year_to
        if yf or yt:
            query += f' AND (PUB_YEAR:[{yf or "1800"} TO {yt or "3000"}])'
        return query

    def search(self, term, limit=10, year_from="", year_to="", offset=0):
        # `offset` cannot be honoured here at all: Europe PMC pages by
        # cursor, and there is no way to jump to row N without walking to
        # it. Rather than accept the argument and ignore it - which is how
        # the whole sweep came to report pages it never read - a non-zero
        # offset walks the cursor, and search_pages() below does that once
        # for the whole sweep instead of once per page.
        query = self._with_years(term, year_from, year_to)
        if not offset:
            return self._query(query, limit)
        cursor, recs = "*", []
        for _ in range((int(offset) // max(1, int(limit))) + 1):
            recs, nxt = self._raw_query(query, limit, cursor)
            if not nxt or nxt == cursor:
                break
            cursor = nxt
        return recs

    def search_pages(self, term, limit=10, year_from="", year_to="",
                     pages=1):
        query = self._with_years(term, year_from, year_to)
        cursor = "*"
        for _ in range(max(1, int(pages))):
            recs, nxt = self._raw_query(query, limit, cursor)
            yield recs
            # No next cursor, or one that has stopped moving, is the end of
            # the result set. Europe PMC repeats the last page forever
            # otherwise, which reads as a corpus twice the size it is.
            if len(recs) < limit or not nxt or nxt == cursor:
                return
            cursor = nxt

    def by_title(self, title, limit=5):
        safe = title.replace('"', " ").strip()
        return self._query(f'TITLE:"{safe}"', limit)

    def by_doi(self, doi):
        doi = clean_doi(doi)
        if not doi:
            return None
        hits = self._query(f'DOI:"{doi}"', 1)
        return hits[0] if hits else None

    def by_id(self, ident):
        ident = (ident or "").strip()
        if ident.lower().startswith("10."):
            return self.by_doi(ident)
        hits = self._query(f'EXT_ID:"{ident}"', 1)
        return hits[0] if hits else None

    def available(self):
        try:
            _get(self.name, self.BASE,
                 {"query": "graphene", "format": "json", "pageSize": 1})
            return (True, "ok")
        except SourceUnreachable as exc:
            return (False, exc.reason)


# --- PubMed (the existing engine, wrapped) ---------------------------------

class PubMed(Source):
    name = "pubmed"
    paging = True

    def _record(self, rec: dict) -> dict:
        return blank_record(
            source=self.name, source_id=rec.get("pmid", ""),
            title=rec.get("title", ""), authors=rec.get("authors", []),
            journal=rec.get("journal", ""), journal_abbrev=rec.get("journal_abbrev", ""),
            year=rec.get("year", ""), volume=rec.get("volume", ""),
            issue=rec.get("issue", ""), pages=rec.get("pages", ""),
            elocation=rec.get("elocation", ""), doi=clean_doi(rec.get("doi", "")),
            pmid=rec.get("pmid", ""), pmc=rec.get("pmc", ""),
            abstract=rec.get("abstract", ""),
            is_retracted=bool(rec.get("is_retracted")),
            notices=rec.get("notices", []), pubtypes=rec.get("pubtypes", []),
            url=rec.get("url", ""),
        )

    def _wrap(self, fn, *a, **kw):
        try:
            return [self._record(r) for r in fn(*a, **kw)]
        except Exception as exc:  # noqa: BLE001 - NCBI failure, not a missing paper
            raise SourceUnreachable(self.name, str(exc)[:200])

    def search(self, term, limit=10, year_from="", year_to="", offset=0):
        try:
            # NCBI pages with retstart, which pubmed.py does not expose; the
            # honest route is to ask for one page deeper and take the tail,
            # because esearch's ordering is stable for a given sort. A page
            # that costs a longer request is still one request.
            hits = pubmed.esearch(term, limit=int(limit) + int(offset),
                                  mindate=_year_of(year_from) or year_from or None,
                                  maxdate=_year_of(year_to) or year_to or None)
        except Exception as exc:  # noqa: BLE001
            raise SourceUnreachable(self.name, str(exc)[:200])
        return self._wrap(pubmed.esummary, hits["pmids"][int(offset):])

    def by_title(self, title, limit=5):
        return (self.search(f'"{title}"[Title]', limit=limit)
                or self.search(title, limit=limit))

    def by_doi(self, doi):
        doi = clean_doi(doi)
        if not doi:
            return None
        try:
            hits = pubmed.esearch(f'"{doi}"[AID]', limit=3)
            if not hits["pmids"]:
                hits = pubmed.esearch(f'"{doi}"', limit=3)
        except Exception as exc:  # noqa: BLE001
            raise SourceUnreachable(self.name, str(exc)[:200])
        recs = self._wrap(pubmed.efetch, hits["pmids"]) if hits["pmids"] else []
        return recs[0] if recs else None

    def by_id(self, ident):
        ident = (ident or "").strip()
        if ident.lower().startswith("10."):
            return self.by_doi(ident)
        # efetch takes PMIDs and nothing else. Handing it a PMCID, an arXiv id
        # or a typo makes NCBI answer HTTP 400 "ID list is empty", which _wrap
        # turns into SourceUnreachable - and SourceUnreachable means "the index
        # was down", which would mark a whole verify run incomplete over what is
        # really a malformed argument. An id PubMed cannot hold is a miss, not
        # an outage, so it returns None here without spending the request.
        if not ident.isdigit():
            return None
        recs = self._wrap(pubmed.efetch, [ident])
        return recs[0] if recs else None

    def related(self, ident, limit=10):
        # elink needs a PMID. Falling back to the raw identifier when by_id
        # found nothing hands it a string it cannot parse, and NCBI answers
        # with its own default records rather than an error: measured,
        # `related 10.1016/j.susc.2005.01.038 --source pubmed` returned PMID 10,
        # "Digitoxin metabolism by rat liver microsomes" - unrelated work
        # presented as related. A DOI PubMed does not index has no PubMed
        # neighbours, so the honest answer is an empty list, and the fallback is
        # narrowed to the one case it was meant for: a bare PMID.
        ident = (ident or "").strip()
        rec = self.by_id(ident)
        pmid = rec["pmid"] if rec else (ident if ident.isdigit() else "")
        if not pmid:
            return []
        return self._wrap(pubmed.esummary, pubmed.elink_related(pmid, limit))

    def available(self):
        try:
            pubmed.esearch("crispr", limit=1)
            return (True, "ok")
        except Exception as exc:  # noqa: BLE001
            return (False, str(exc)[:120])



# --- arXiv -----------------------------------------------------------------
#
# Added after re-measurement. The engine's first spec excluded arXiv on two
# failed probes and said, in as many words, "Re-measure before adding".
# Re-probed 2026-09-06: three of four query forms answered, one timed out, and
# `cat:cond-mat.mtrl-sci` alone reported 290 hits for a surface-science phrase.
# It is in for the reason the engine exists - cond-mat.mtrl-sci and
# physics.chem-ph carry this group's preprints, and nothing else here indexes
# them before a journal does.

class ArXiv(Source):
    name = "arxiv"
    BASE = "http://export.arxiv.org/api/query"
    paging = True
    NS = {"a": "http://www.w3.org/2005/Atom",
          "arxiv": "http://arxiv.org/schemas/atom"}
    # 1706.03762v7 (2007 onwards) or cond-mat/0501001v2 (the old scheme).
    _ID_RE = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Za-z]{2})?/\d{7})"
                        r"(?:v\d+)?", re.I)

    @staticmethod
    def _sanitize(text: str) -> str:
        """Strip what arXiv's query parser cannot take, keeping words intact.

        Measured: `Cu(111)` inside a query silently returns ZERO results rather
        than an error, which is the worst possible failure - it reads as "no
        such paper". Punctuation becomes whitespace; `ti:"Chlorine adsorption
        on the Cu 111 surface"` finds the same single paper the raw title does.
        """
        return re.sub(r"\s+", " ", re.sub(r"[^0-9A-Za-z ]+", " ", text or "")).strip()

    def _record(self, entry) -> dict:
        def txt(path, default=""):
            return (entry.findtext(path, default=default,
                                   namespaces=self.NS) or "").strip()

        raw_id = txt("a:id")                      # http://arxiv.org/abs/1706.03762v7
        arx_id = raw_id.rsplit("/", 1)[-1] if raw_id else ""
        authors = [_name_to_pubmed_style(
            (a.findtext("a:name", default="", namespaces=self.NS) or "").strip())
            for a in entry.findall("a:author", self.NS)]
        cats = [c.get("term") for c in entry.findall("a:category", self.NS)
                if c.get("term")]
        title = re.sub(r"\s+", " ", txt("a:title")).strip().rstrip(".")
        # `arxiv:journal_ref` is a free-text citation string ("Phys. Rev. B 85,
        # 045432 (2012)"), NOT a journal name, so it is never written into
        # `journal` - that would bury a whole citation inside the journal field
        # of another citation. The venue of an arXiv record is arXiv.
        return blank_record(
            source=self.name, source_id=arx_id, title=title,
            authors=[a for a in authors if a],
            journal="arXiv",
            year=txt("a:published")[:4],
            # A preprint that has since been published states the journal's DOI
            # here. Keeping it lets the retraction sweep and the caller reach
            # the version of record.
            doi=clean_doi(txt("arxiv:doi")),
            elocation=(f"arXiv:{arx_id}" if arx_id else ""),
            abstract=re.sub(r"\s+", " ", txt("a:summary")),
            pubtypes=["Preprint"] + cats,
            url=raw_id,
            is_oa=True,     # every arXiv record is free to read, by definition
        )

    def _query(self, search_query: str, limit: int,
               offset: int = 0) -> list[dict]:
        params = {"search_query": search_query,
                  "max_results": max(1, min(int(limit), 100))}
        if offset:
            params["start"] = int(offset)        # arXiv pages by row index
        root = _get_xml(self.name, self.BASE, params)
        if root is None:
            return []
        return [self._record(e) for e in root.findall("a:entry", self.NS)]

    def search(self, term, limit=10, year_from="", year_to="", offset=0):
        # An AND of terms, not a quoted phrase. Measured: a quoted phrase on
        # `all:` returned 0 for every query tried, the AND form returned 6
        # relevant hits for the same words, and the unquoted form returned
        # 210,339 because it ORs. arXiv has no date filter in search_query, so
        # the year range is applied here rather than pretended at.
        words = self._sanitize(term).split()
        if not words:
            return []
        recs = self._query(" AND ".join(f"all:{w}" for w in words), limit,
                           offset)
        yf, yt = _year_of(year_from) or year_from, _year_of(year_to) or year_to
        if yf or yt:
            recs = [r for r in recs if r["year"].isdigit()
                    and (not yf or int(r["year"]) >= int(yf))
                    and (not yt or int(r["year"]) <= int(yt))]
        return recs

    def by_title(self, title, limit=5):
        # `ti:` DOES honour a quoted phrase even though `all:` does not - an
        # arXiv quirk, measured rather than assumed. This is the route that
        # matters: it returns the exact paper for a real title, and 0 for a
        # fabricated one.
        safe = self._sanitize(title)
        return self._query(f'ti:"{safe}"', limit) if safe else []

    @classmethod
    def _clean_id(cls, ident: str) -> str | None:
        """An arXiv identifier, or None if this string is not one.

        Checked before the request, not after. arXiv answers a malformed
        `id_list` with HTTP 400, which `_get_xml` would - correctly, for a real
        HTTP 400 - raise as SourceUnreachable, and SourceUnreachable means "the
        index was down". A typo is not an outage: reporting it as one would mark
        a whole check-refs run incomplete over a bad argument. So a string that
        cannot be an arXiv id is a miss, answered without spending a request.

        Both id schemes are accepted: the post-2007 `1706.03762v7` and the old
        `cond-mat/0501001`, whose slash a naive basename split would eat.
        """
        ident = (ident or "").strip()
        if not ident:
            return None
        if ident.lower().startswith("10."):
            return ident                              # a DOI, handled by by_doi
        ident = re.sub(r"^(https?://)?(www\.)?arxiv\.org/(abs|pdf)/", "", ident,
                       flags=re.I)
        ident = re.sub(r"^arxiv:", "", ident, flags=re.I).strip("/")
        return ident if cls._ID_RE.fullmatch(ident) else None

    def by_id(self, ident):
        ident = self._clean_id(ident)
        if ident is None:
            return None
        if ident.lower().startswith("10."):
            return self.by_doi(ident)
        root = _get_xml(self.name, self.BASE, {"id_list": ident, "max_results": 1})
        if root is None:
            return None
        recs = [self._record(e) for e in root.findall("a:entry", self.NS)]
        # A bad-but-well-formed id yields an entry whose title is literally
        # "Error"; arXiv does not use a 404 for it.
        return recs[0] if recs and recs[0]["title"].lower() != "error" else None

    def by_doi(self, doi):
        # arXiv's API has no DOI field to query. Saying so by returning None is
        # correct and costs nothing: a DOI-bearing paper is matched by Crossref,
        # OpenAlex or PubMed long before the cascade reaches this source.
        return None

    def available(self):
        try:
            self._query("all:graphene", 1)
            return (True, "ok")
        except SourceUnreachable as exc:
            return (False, exc.reason)


# --- Semantic Scholar ------------------------------------------------------
#
# Keyless, but only in part, and the split is measured. `/paper/search` answered
# 429 on 4 of 4 keyless attempts spaced 6 s apart, so this adapter declares
# has_search = False rather than burning a request to be refused. The
# DOI-addressed routes are a different story: /paper/DOI:, /citations and
# /references each answered 200 on 4 of 5 real DOIs, the fifth a genuine 404.
# Those are what it is registered for.

class SemanticScholar(Source):
    name = "semanticscholar"
    BASE = "https://api.semanticscholar.org/graph/v1/paper"
    RECS = "https://api.semanticscholar.org/recommendations/v1/papers/forpaper"
    FIELDS = ("title,abstract,year,venue,journal,authors,externalIds,"
              "citationCount,isOpenAccess,publicationTypes")
    has_search = False

    def _record(self, d: dict) -> dict:
        if not d:
            return blank_record(source=self.name)
        ext = d.get("externalIds") or {}
        jr = d.get("journal") or {}
        # Measured: `journal.volume` arrives as '35 3' - volume and issue mashed
        # into one string with nothing saying which is which. Splitting it would
        # be a guess about someone else's citation, so a value that is not a
        # clean single token is dropped rather than divided. Crossref, PubMed
        # and OpenAlex all carry volume and issue separately and are what `cite`
        # uses; this source is here for citations, not for locators.
        vol = str(jr.get("volume") or "").strip()
        if vol and not re.fullmatch(r"[0-9A-Za-z\-]+", vol):
            vol = ""
        authors = [_name_to_pubmed_style((a.get("name") or "").strip())
                   for a in d.get("authors") or []]
        return blank_record(
            source=self.name,
            source_id=str(ext.get("CorpusId") or d.get("paperId") or ""),
            title=(d.get("title") or "").strip().rstrip("."),
            authors=[a for a in authors if a],
            journal=(jr.get("name") or d.get("venue") or ""),
            year=str(d.get("year") or ""),
            volume=vol,
            pages=re.sub(r"\s+", " ", str(jr.get("pages") or "")).strip(),
            doi=clean_doi(ext.get("DOI") or ""),
            pmid=str(ext.get("PubMed") or ""),
            pmc=str(ext.get("PubMedCentral") or ""),
            abstract=(d.get("abstract") or ""),
            pubtypes=[str(t) for t in (d.get("publicationTypes") or [])],
            cited_by_count=d.get("citationCount"),
            is_oa=d.get("isOpenAccess"),
            url=(f"https://www.semanticscholar.org/paper/{d.get('paperId')}"
                 if d.get("paperId") else ""),
        )

    def _paper(self, ident: str):
        return _get(self.name, f"{self.BASE}/{quote(ident, safe=':/')}",
                    {"fields": self.FIELDS})

    @staticmethod
    def _qualify(ident: str) -> str:
        """Semantic Scholar addresses papers by a prefixed identifier."""
        ident = (ident or "").strip()
        if not ident:
            return ""
        if ident.lower().startswith("10."):
            return f"DOI:{clean_doi(ident)}"
        if ":" in ident:
            return ident                    # already DOI:/PMID:/arXiv:/CorpusId:
        return f"PMID:{ident}" if ident.isdigit() else f"arXiv:{ident}"

    def by_doi(self, doi):
        doi = clean_doi(doi)
        if not doi:
            return None
        data = self._paper(f"DOI:{doi}")
        return self._record(data) if data else None

    def by_id(self, ident):
        qualified = self._qualify(ident)
        if not qualified:
            return None
        data = self._paper(qualified)
        return self._record(data) if data else None

    def cited_by(self, ident, limit=10):
        qualified = self._qualify(ident)
        if not qualified:
            return []
        data = _get(self.name,
                    f"{self.BASE}/{quote(qualified, safe=':/')}/citations",
                    {"fields": self.FIELDS, "limit": max(1, min(int(limit), 100))})
        return [self._record(row.get("citingPaper"))
                for row in (data or {}).get("data") or [] if row.get("citingPaper")]

    def related(self, ident, limit=10):
        """Semantic Scholar's recommendations.

        Measured coverage, so nobody reads an empty list as "nothing is
        related": this endpoint answers HTTP 200 with `{"recommendedPapers":
        []}` outside biomedicine. Of three probes it returned rows for a Science
        paper and nothing at all for a Langmuir one or for arXiv's own
        best-known CS paper. It is kept because it costs nothing and sometimes
        answers, but OpenAlex is tried first for a reason, and this is NOT a
        dependable second opinion for surface science. `cited_by` and
        `references`, by contrast, answered for every DOI tried.
        """
        qualified = self._qualify(ident)
        if not qualified:
            return []
        data = _get(self.name, f"{self.RECS}/{quote(qualified, safe=':/')}",
                    {"fields": self.FIELDS, "limit": max(1, min(int(limit), 100))})
        return [self._record(p) for p in (data or {}).get("recommendedPapers") or []]

    def references(self, ident, limit=10) -> list[dict]:
        """What this paper cites. Not part of the Source contract - no other
        source here offers it - so it is reached by name, not by the cascade."""
        qualified = self._qualify(ident)
        if not qualified:
            return []
        data = _get(self.name,
                    f"{self.BASE}/{quote(qualified, safe=':/')}/references",
                    {"fields": self.FIELDS, "limit": max(1, min(int(limit), 100))})
        return [self._record(row.get("citedPaper"))
                for row in (data or {}).get("data") or [] if row.get("citedPaper")]

    def available(self):
        try:
            self._paper("DOI:10.1126/science.1225829")
            return (True, "ok (DOI routes only - keyless search answers 429)")
        except SourceUnreachable as exc:
            return (False, exc.reason)


SOURCES: dict[str, Source] = {
    "pubmed": PubMed(), "crossref": Crossref(),
    "openalex": OpenAlex(), "europepmc": EuropePMC(),
    "arxiv": ArXiv(), "semanticscholar": SemanticScholar(),
}


# ---------------------------------------------------------------------------
# Verification cascade
# ---------------------------------------------------------------------------

def _score(claim: dict, rec: dict) -> tuple[bool, float]:
    sim = title_similarity(claim["title"], rec["title"]) if claim["title"] else 0.0
    id_hit = bool(
        (claim["doi"] and rec["doi"] and clean_doi(rec["doi"]) == claim["doi"]) or
        (claim["pmid"] and rec["pmid"] and claim["pmid"] == rec["pmid"])
    )
    return id_hit, sim


def _candidate(rec: dict, sim: float, id_hit: bool) -> dict:
    return {"source": rec["source"], "source_id": rec["source_id"],
            "title": rec["title"], "year": rec["year"], "doi": rec["doi"],
            "pmid": rec["pmid"], "similarity": round(sim, 3),
            "identifier_match": id_hit,
            "note": "" if id_hit else "candidate only - not confirmed as this citation"}


def _probe_source(src: Source, claim: dict) -> list[dict]:
    """Everything one source can offer for this claim.

    Identifiers before title, in every source: a DOI is an identifier and a
    title is a guess.
    """
    recs: list[dict] = []
    if claim["pmid"] and src.name in ("pubmed", "europepmc"):
        rec = src.by_id(claim["pmid"])
        if rec:
            recs.append(rec)
    if not recs and claim["doi"]:
        rec = src.by_doi(claim["doi"])
        if rec:
            recs.append(rec)
    if not recs and claim["title"]:
        recs = src.by_title(claim["title"], limit=5) or []
    return recs


def _retraction_sweep(rec: dict, result: dict) -> None:
    """Ask Crossref and OpenAlex about a retraction even when PubMed already
    verified the paper. It is cheap, and the sources do not agree on timing."""
    doi = clean_doi(rec.get("doi", ""))
    if not doi:
        return
    for name in ("crossref", "openalex"):
        if rec.get("source") == name:
            continue
        try:
            other = SOURCES[name].by_doi(doi)
        except SourceUnreachable as exc:
            result["notes"].append(f"Retraction cross-check against {name} "
                                   f"was skipped: {exc.reason}")
            continue
        if not other:
            continue
        if other.get("is_retracted") and not rec.get("is_retracted"):
            rec["is_retracted"] = True
            result["flags"].append(
                f"RETRACTED PUBLICATION - flagged by {name}, not by {rec['source']}.")
        for n in other.get("notices", []):
            if n not in rec["notices"]:
                rec["notices"].append({**n, "found_by": name})
        if rec.get("cited_by_count") is None and other.get("cited_by_count") is not None:
            rec["cited_by_count"] = other["cited_by_count"]
        if rec.get("is_oa") is None and other.get("is_oa") is not None:
            rec["is_oa"] = other["is_oa"]


# Name particles, folded away before two bylines are compared, so that
# `van der Goot` and `Goot` are one person rather than a fabricated author.
# The list is short on purpose: it holds the particles that appear in front
# of a surname in the literatures this toolkit is used on, and a particle
# that is somebody's WHOLE surname is kept by the last-resort branch below.
_NAME_PARTICLES = {
    "van", "von", "der", "den", "de", "del", "della", "dello", "di", "da",
    "do", "dos", "das", "du", "la", "le", "les", "el", "al", "bin", "ibn",
    "ter", "ten", "af", "av", "zu", "vander", "vande",
}


def _surname_of(author: str) -> str:
    """One best-effort surname for one cited author.

    `_surnames()` in pubmed.py answers a different question - it returns
    BOTH end tokens of every name into one set, because its job is to decide
    whether any part of a byline appears on a record. That set cannot say
    who came first, and the first author is the half of a byline a reader
    needs in order to find the paper at all. This returns one name per
    author, in the order the citation gives them.
    """
    raw = (author or "").strip()
    if not raw:
        return ""
    # "Goot, F. G. van der" - everything before the first comma is the
    # surname, particles and all. Done before normalization, because
    # normalization is what removes the comma.
    if "," in raw:
        raw = raw.split(",", 1)[0]
    tokens = [t for t in normalize_title(raw).split() if len(t) > 2]
    if not tokens:
        tokens = normalize_title(raw).split()
    if not tokens:
        return ""
    body = [t for t in tokens if t not in _NAME_PARTICLES]
    # Every token was a particle: somebody's surname really is "De" or
    # "Le", and dropping it would invent a missing author.
    return (body or tokens)[-1]


def _surname_list(authors: list[str]) -> list[str]:
    """The cited byline as an ordered list of surnames, particles folded."""
    out = []
    for a in authors or []:
        s = _surname_of(a)
        if s:
            out.append(s)
    return out


def _show_names(surnames: list[str], limit: int = 8) -> str:
    shown = [s.title() for s in surnames[:limit]]
    if len(surnames) > limit:
        shown.append("and %d more" % (len(surnames) - limit))
    return ", ".join(shown) or "(none)"


def _review_source_note(result: dict, claim_sentence: str = "") -> None:
    """Right fact, wrong source - citation-integrity 7.

    The failure this names is not a byline error and not a false claim: the
    sentence is true, the source says it, and the source is not the paper
    that SHOWED it. A review restating a finding verifies perfectly, and two
    references on the measured bibliography were exactly this.

    A NOTE, never a flag and never a substitution. A review is a legitimate
    citation for a synthesis, for a definition and for a field's state; only
    the writer knows which of those the sentence is doing, and the engine's
    job here ends at naming what kind of paper it is.
    """
    best = result.get("match") or {}
    kinds = {str(t).lower() for t in best.get("pubtypes", [])}
    if not (kinds & {"review", "systematic review", "review-article"}):
        return
    where = best.get("source") or "the index"
    result.setdefault("integrity", {})["review_source"] = True
    result["notes"].append(
        f"{where} types this as a review. If the sentence attributes a "
        f"FINDING, the primary source may be wanted instead; if it "
        f"attributes a synthesis, a definition or the state of the field, a "
        f"review is the right citation.")
    if not claim_sentence:
        return
    # The reference list of the review itself, which is where the primary
    # source is if it is anywhere. Semantic Scholar alone answers this, and
    # its keyless tier is intermittent - so a failure is reported as a
    # failure, never as "the review cites nothing".
    doi = clean_doi(best.get("doi", "")) or best.get("pmid", "")
    if not doi:
        return
    try:
        cited = SOURCES["semanticscholar"].references(doi, 100)
    except SourceUnreachable as exc:
        result["notes"].append(
            f"Its own reference list could not be read ({exc.reason}), so no "
            f"primary-source candidates are named. That is a source failure, "
            f"not evidence that there are none.")
        return
    terms = set(pubmed._title_terms(claim_sentence, limit=12))
    if not terms or not cited:
        return
    scored = []
    for rec in cited:
        overlap = terms & set(pubmed._title_terms(rec.get("title", ""), limit=12))
        if overlap:
            scored.append((len(overlap), rec))
    scored.sort(key=lambda x: (-x[0], str(x[1].get("year") or "")))
    for _, rec in scored[:3]:
        result["notes"].append(
            f"  candidate primary source, from the review's own reference "
            f"list: {rec.get('title', '')[:90]} ({rec.get('year') or 'n.d.'}) "
            f"{rec.get('doi') or ''}".rstrip())


def _integrity_notes(res: dict) -> bool:
    """Did this entry come back carrying a byline or year note.

    One reader for the two lists `_cross_checks()` fills, so `clean` and
    `verified_with_flags` cannot drift apart from the summary keys beside
    them.
    """
    integ = res.get("integrity") or {}
    return bool(integ.get("byline_differences") or integ.get("year_notes"))


def _byline_checks(claim: dict, best: dict, result: dict) -> None:
    """Three named differences between two bylines, not one emptiness test.

    citation-integrity 3, closing item 107's first half. The check this
    replaced compared surnames as SETS and fired only when the intersection
    was empty, so one correct surname carried any byline - measured
    2026-09-20 on a real three-author PNAS paper cited with four authors,
    one of whom does not exist: `[OK]`, confidence 1.0, silent.

    A flag for a name that is not on the paper and for a wrong first author;
    a NOTE for an abbreviated byline, which is ordinary practice in several
    styles and must never read as an accusation. Nothing here refuses,
    removes or rewrites an entry: the record can be the wrong record, and
    the decision stays the user's.
    """
    cited = _surname_list(claim.get("authors") or [])
    actual = _surname_list(best.get("authors") or [])
    if not cited:
        return
    if not actual:
        result["notes"].append(
            f"{best['source']} returned no author list, so authors were not "
            f"checked.")
        return
    cset, aset = set(cited), set(actual)
    diffs = result.setdefault("integrity", {}).setdefault(
        "byline_differences", [])

    # 1. A cited surname the record does not carry. The signature of
    #    generated text, and the one difference here that is a flag on its
    #    own evidence.
    for s in [x for x in cited if x not in aset]:
        diffs.append({"kind": "not_on_record", "surname": s.title()})
        result["flags"].append(
            f'Cited author "{s.title()}" is not on the record: it lists '
            f'{_show_names(actual)}.')

    # 2. The record carries names the citation omits. A note.
    omitted = [x for x in actual if x not in cset]
    if omitted:
        diffs.append({"kind": "abbreviated", "omitted": [x.title() for x in omitted]})
        result["notes"].append(
            f"The record also lists {_show_names(omitted)}; the citation "
            f"gives {len(cited)} of {len(actual)} authors.")

    # 3. The wrong first author, which is what breaks a reader's ability to
    #    find the paper at all.
    if cited[0] != actual[0]:
        diffs.append({"kind": "first_author",
                      "cited": cited[0].title(), "record": actual[0].title()})
        result["flags"].append(
            "First author differs: cited \"%s\", the record's first "
            "author is \"%s\"." % (cited[0].title(), actual[0].title()))
    else:
        shared_c = [x for x in cited if x in aset]
        shared_a = [x for x in actual if x in cset]
        if shared_c != shared_a:
            diffs.append({"kind": "order",
                          "cited": [x.title() for x in shared_c],
                          "record": [x.title() for x in shared_a]})
            result["notes"].append(
                f"Author order differs: the citation gives "
                f"{_show_names(shared_c)}; {best['source']} lists "
                f"{_show_names(shared_a)}.")


def _cross_checks(claim: dict, best: dict, sim: float, result: dict) -> None:
    claim_year = _year_of(claim["year"])
    if claim_year and best["year"]:
        gap = abs(int(claim_year) - int(best["year"]))
        if gap > 1:
            result["flags"].append(
                f"Year mismatch: cited {claim_year}, {best['source']} says "
                f"{best['year']}.")
        elif gap == 1:
            # citation-integrity 4, closing item 107's second half. The
            # tolerance stays - it exists to absorb electronic-ahead-of-print
            # dating - and the SILENCE goes. An ordinary wrong year was being
            # swallowed by it, and on the measured bibliography the wrong year
            # is what made two entries render as the same citation.
            result.setdefault("integrity", {}).setdefault(
                "year_notes", []).append(
                    {"cited": str(claim_year), "record": str(best["year"]),
                     "source": best["source"]})
            result["notes"].append(
                f"Year differs by one: the entry says {claim_year}, "
                f"{best['source']} says {best['year']}. Electronic-ahead-of-"
                f"print dating explains most of these; a reference list that "
                f"mixes both is still inconsistent.")
    if claim["doi"] and best["doi"] and clean_doi(best["doi"]) != claim["doi"]:
        result["flags"].append(
            f"DOI mismatch: cited {claim['doi']}, {best['source']} says {best['doi']}.")
    if claim["pmid"] and best["pmid"] and claim["pmid"] != best["pmid"]:
        result["flags"].append(
            f"PMID mismatch: cited {claim['pmid']}, this paper is PMID {best['pmid']}.")
    # Not in pubmed.py, and it belongs here: an identifier route accepts the
    # record on the strength of the DOI alone, so a real DOI pasted under an
    # invented title would otherwise pass silently. That is a live failure mode
    # of generated text, and the mismatch has to be said out loud.
    if (claim["title"] and best["title"] and sim < PARTIAL_MIN
            and (claim["doi"] or claim["pmid"])):
        result["flags"].append(
            f'Title does not match the identifier: it resolves to '
            f'"{best["title"][:70]}" (similarity {sim:.2f}). Check which is wrong.')
    # The byline is a LIST, not an intersection (citation-integrity 3). The
    # emptiness test this replaced fired only when NO cited surname appeared
    # on the record, so one correct surname carried any byline; the three
    # named differences below subsume it, and the wholly-wrong byline now
    # arrives as one flag per invented name rather than as one sentence that
    # names none of them.
    _byline_checks(claim, best, result)
    # The list routes drop these already; this catches the other door, where a
    # caller hands over a supplementary-file DOI directly. It resolves fine and
    # would otherwise read as a verified paper.
    kinds = {str(t).lower() for t in best.get("pubtypes", [])}
    if kinds & {"component", "peer-review"}:
        result["flags"].append(
            f"This identifier is a '{', '.join(sorted(kinds & {'component', 'peer-review'}))}' "
            f"record, not the article itself - supplementary material and peer "
            f"reviews carry their parent's title. Cite the parent DOI instead.")
    if best.get("is_retracted"):
        result["flags"].append("RETRACTED PUBLICATION - do not cite as valid evidence.")
    for n in best.get("notices", []):
        if n["type"] == "RetractionIn":
            result["flags"].append(f"Retraction notice exists: {n.get('ref', '')}")
        elif n["type"] == "ExpressionOfConcernIn":
            result["flags"].append(f"Expression of concern: {n.get('ref', '')}")
        elif n["type"] in ("ErratumIn", "CorrectedAndRepublishedIn"):
            result["flags"].append(f"Erratum/correction published: {n.get('ref', '')}")


# ---------------------------------------------------------------------------
# What KIND of source is this - specs/user-asks-2026-09-18.md 1
# ---------------------------------------------------------------------------
#
# The third axis. `status` answers "does it exist" and flags answer "should you
# cite it" (spec 5.5); neither of them can answer "is this a paper at all", and
# a reference list is full of things that are real, citable and not papers.
#
# It was a measured failure rather than a tidiness argument: a UniProt entry in
# a real bibliography went through the cascade, missed in four indexes BECAUSE
# it is not a paper, and came back `not_found` - the word this engine reserves
# for invented citations. The classification runs first so that record never
# reaches a cascade that cannot answer a question about it.

SOURCE_CLASSES = ("journal-article", "preprint", "database", "book",
                  "chapter", "thesis", "software", "dataset", "web",
                  "unknown")

# (needle, what it is). Matched as a substring against the fields that say
# WHERE a record lives, and - under the guard in classify_source - the title.
DATABASE_SOURCES = (
    ("uniprot", "UniProt - a sequence and annotation database"),
    ("rcsb.org", "RCSB PDB - a structure database"),
    ("pdbe", "PDBe - a structure database"),
    ("wwpdb", "wwPDB - a structure database"),
    ("alphafold", "AlphaFold DB - predicted structures"),
    ("emdataresource", "EMDB - a map database"),
    ("ebi.ac.uk/emdb", "EMDB - a map database"),
    ("pubchem", "PubChem - a substance database"),
    ("ebi.ac.uk/chembl", "ChEMBL - a bioactivity database"),
    ("drugbank", "DrugBank - a drug database"),
    ("ccdc.cam.ac.uk", "CCDC/CSD - a crystal structure database"),
    ("ncbi.nlm.nih.gov/nuccore", "GenBank - a sequence database"),
    ("ncbi.nlm.nih.gov/protein", "NCBI Protein - a sequence database"),
    ("ncbi.nlm.nih.gov/gene", "NCBI Gene - a gene database"),
    ("ncbi.nlm.nih.gov/clinvar", "ClinVar - a variant database"),
    ("genbank", "GenBank - a sequence database"),
    ("ensembl", "Ensembl - a genome database"),
    ("interpro", "InterPro - a domain database"),
    ("pfam", "Pfam - a domain database"),
    ("omim", "OMIM - a phenotype database"),
    ("gnomad", "gnomAD - a variant database"),
    ("proteinatlas", "Human Protein Atlas"),
    ("reactome", "Reactome - a pathway database"),
    ("kegg", "KEGG - a pathway database"),
    ("string-db", "STRING - an interaction database"),
    ("nist.gov/srd", "NIST Standard Reference Data"),
    ("webbook.nist.gov", "the NIST Chemistry WebBook"),
    ("materialsproject", "the Materials Project"),
    ("atcc.org", "ATCC - a cell line and strain repository"),
)

PREPRINT_SOURCES = (
    ("arxiv", "arXiv"),
    ("biorxiv", "bioRxiv"),
    ("medrxiv", "medRxiv"),
    ("chemrxiv", "ChemRxiv"),
    ("researchsquare", "Research Square"),
    ("research square", "Research Square"),
    ("ssrn", "SSRN"),
    ("preprint", "a preprint server"),
)

DATASET_SOURCES = (
    ("zenodo", "Zenodo - a data repository"),
    ("figshare", "figshare - a data repository"),
    ("dryad", "Dryad - a data repository"),
    ("osf.io", "OSF - a project and data repository"),
    ("github.com", "GitHub - a code repository"),
    ("zenodo.org", "Zenodo - a data repository"),
)

# The entry type, where it settles the question by itself.
ENTRY_TYPE_CLASS = {
    "book": "book", "inbook": "chapter", "incollection": "chapter",
    "phdthesis": "thesis", "mastersthesis": "thesis", "thesis": "thesis",
    "software": "software", "dataset": "dataset",
    "proceedings": "chapter", "inproceedings": "chapter",
    "conference": "chapter",
}

# Which classes are worth handing to a cascade that indexes PAPERS. A record
# outside this set is not "unverifiable" - it is a question the indexes do not
# answer, which is a different sentence and has to read as one.
VERIFIABLE_CLASSES = ("journal-article", "preprint", "chapter", "unknown")


def _first_hit(text: str, table: tuple) -> tuple[str, str]:
    low = (text or "").lower()
    for needle, label in table:
        if needle in low:
            return needle, label
    return "", ""


def classify_source(ref: dict) -> dict:
    """What kind of thing this reference is, from the .bib entry alone.

    Offline, and deliberately so: the question is a fact about the entry the
    user wrote, not about what an index knows. Returns `class`, `peer_reviewed`
    (True / False / **None**), `why`, `evidence` - the field the answer came
    from - and `verify`, whether it is worth sending to the cascade.

    **`None` is a real answer and the common one.** A bare `@misc` with a title
    and nothing else is not evidence that something was not peer reviewed; it
    is evidence that the entry does not say. Collapsing those two into `False`
    would put a confident negative on entries the user never described, which
    is the same class of mistake as reporting a database record `not_found`.
    """
    et = (ref.get("entry_type") or "").lower()
    doi = (ref.get("doi") or "").strip()
    pmid = str(ref.get("pmid") or "").strip()
    journal = (ref.get("journal") or "").strip()

    def out(kind: str, peer, why: str, evidence: str) -> dict:
        return {"class": kind, "peer_reviewed": peer, "why": why,
                "evidence": evidence, "verify": kind in VERIFIABLE_CLASSES}

    # 1. A preprint server named anywhere structural. First, because a
    #    bioRxiv posting is routinely typed `@article` with `journal =
    #    {bioRxiv}`, and the entry type would otherwise call it peer reviewed.
    if (ref.get("archiveprefix") or "").strip() or (ref.get("eprint") or "").strip():
        name = (ref.get("archiveprefix") or "an eprint archive").strip()
        return out("preprint", False,
                   f"{name} eprint - posted, not peer reviewed",
                   "archiveprefix")
    for field in ("url", "howpublished", "journal", "publisher", "note"):
        needle, label = _first_hit(ref.get(field, ""), PREPRINT_SOURCES)
        if needle:
            return out("preprint", False,
                       f"{label} - a preprint, not a peer-reviewed version of "
                       f"record", field)

    # 2. A database, from the fields that say where the record lives.
    for field in ("url", "howpublished", "note", "publisher", "organization"):
        needle, label = _first_hit(ref.get(field, ""), DATABASE_SOURCES)
        if needle:
            return out("database", False,
                       f"{label} - a database record, not a peer-reviewed "
                       f"paper", field)

    # 3. A repository, same shape, different word for it.
    for field in ("url", "howpublished", "note", "publisher"):
        needle, label = _first_hit(ref.get(field, ""), DATASET_SOURCES)
        if needle:
            return out("dataset", False,
                       f"{label} - a deposit, not a peer-reviewed paper",
                       field)

    # 4. The title, and ONLY under this guard. `The UniProt Knowledgebase in
    #    2023` is a real Nucleic Acids Research paper, and matching a database
    #    name in a title would classify the paper ABOUT the database as the
    #    database. So the title is read only when the entry has no journal, no
    #    DOI and no PMID - that is, when nothing else describes it at all.
    if not (journal or doi or pmid):
        needle, label = _first_hit(ref.get("title", ""), DATABASE_SOURCES)
        if needle:
            return out("database", False,
                       f"{label} - the title names it and the entry carries "
                       f"no journal or DOI", "title")

    # 5. The entry type, where it settles the question.
    if et in ENTRY_TYPE_CLASS:
        kind = ENTRY_TYPE_CLASS[et]
        peer = None if kind == "chapter" else False
        why = {"book": "a book - not peer reviewed in the journal sense",
               "chapter": "a book or proceedings chapter - review practice "
                          "varies, so this is not a claim either way",
               "thesis": "a thesis - examined, not peer reviewed",
               "software": "software - not a paper",
               "dataset": "a data deposit - not a paper"}[kind]
        return out(kind, peer, why, "entry_type")

    if et == "article" and journal:
        return out("journal-article", True,
                   f"an article in {journal}", "journal")
    if et == "article" or doi or pmid:
        # A DOI is an identifier, not a kind. It goes to the cascade, and what
        # comes back is what decides.
        return out("unknown", None,
                   "an identifier but nothing that says what kind of record "
                   "it is - the index it resolves in will say",
                   "doi" if doi else "pmid" if pmid else "entry_type")

    if (ref.get("url") or "").strip():
        return out("web", None,
                   "a web page - the entry gives a URL and nothing that says "
                   "it was published or reviewed", "url")

    return out("unknown", None,
               "the entry says only a title, so what kind of source it is "
               "cannot be read off it", "")


# ---------------------------------------------------------------------------
# Has a later paper disagreed with this one - user-asks-2026-09-18.md 4
# ---------------------------------------------------------------------------
#
# A RETRACTION is the publisher withdrawing a paper, and this engine has
# checked for one since it was written. A CONTRADICTION is a later paper
# disagreeing with an earlier one, and no index has a field for it. The two
# are different events and only the first was ever checked.
#
# What is reachable keyless is the citation graph plus the TITLES of the
# citing works, and a paper that overturns an earlier one very often says so
# in its title. That is a SIGNAL and not a verdict, and every line of output
# here says so, because the failure mode of a check like this is a user
# deleting a good citation on the strength of a word match.
#
# Tiered exactly as prose.py ai_voice is, and for the same reason: `comment
# on` is a title that exists to argue, `revisited` is also the normal title of
# a review that agrees.

# (phrase, tier, why, anchored). `anchored` means the phrase only asserts
# anything when the title STARTS with it, and this was measured rather than
# guessed: the first live run of this command, on the arsenic-life paper,
# returned "Response to Arsenic in Rhodococcus aetherivorans BCP1" as an
# `asserted` signal. That is a bacterium responding to arsenic, not a
# published Response. A journal Comment or Reply names its target and
# therefore leads with the phrase; a sentence about biology puts it in the
# middle. An anchored phrase found mid-title is demoted to `contextual`
# rather than dropped - it is still a title worth a glance, and it now claims
# nothing.
DISPUTE_PHRASES = (
    ("comment on", "asserted", "a Comment is published to argue with a paper",
     True),
    ("reply to", "asserted", "a Reply is one side of a published disagreement",
     True),
    ("response to", "asserted", "a Response is one side of a disagreement",
     True),
    ("rebuttal", "asserted", "states that it is rebutting something", False),
    ("refut", "asserted", "states that it is refuting something", False),
    ("disproof", "asserted", "states that it disproves something", False),
    ("disproves", "asserted", "states that it disproves something", False),
    ("failure to replicate", "asserted", "a replication that failed", False),
    ("fails to replicate", "asserted", "a replication that failed", False),
    ("failed replication", "asserted", "a replication that failed", False),
    ("non-replication", "asserted", "a replication that failed", False),
    ("does not replicate", "asserted", "a replication that failed", False),
    ("contrary to", "asserted", "names a claim it is contradicting", True),
    ("no evidence for", "asserted", "a negative finding, stated as the title",
     False),
    ("no evidence of", "asserted", "a negative finding, stated as the title",
     False),
    ("lack of evidence", "asserted", "a negative finding, stated as the title",
     False),
    ("reassessment", "asserted", "a reassessment is a second verdict", False),
    ("reassessing", "asserted", "a reassessment is a second verdict", False),
    ("corrigendum", "asserted", "a correction to the published record", False),
    ("erratum", "asserted", "a correction to the published record", False),
    ("retraction of", "asserted", "a retraction notice", True),
    ("expression of concern", "asserted", "the publisher has flagged it",
     False),
    ("challenge to", "asserted", "names what it is challenging", True),
    ("misinterpretation", "asserted", "says an earlier reading was wrong",
     False),
    ("misidentification", "asserted", "says an earlier assignment was wrong",
     False),
    ("revisited", "contextual",
     "a revisit sometimes overturns and sometimes confirms", False),
    ("re-examin", "contextual", "a re-examination may go either way", False),
    ("reexamin", "contextual", "a re-examination may go either way", False),
    ("re-evaluat", "contextual", "a re-evaluation may go either way", False),
    ("reevaluat", "contextual", "a re-evaluation may go either way", False),
    ("reconsider", "contextual", "a reconsideration may go either way", False),
    ("revised", "contextual", "a revision of a value or a model", False),
    ("limitations of", "contextual", "may be a caveat rather than a rebuttal",
     False),
    ("critical appraisal", "contextual", "an appraisal may be favourable",
     False),
    ("critical evaluation", "contextual", "an evaluation may be favourable",
     False),
    ("alternative explanation", "contextual", "offers another reading", False),
    ("artefact", "contextual", "may be attributing a result to an artefact",
     False),
    ("artifact of", "contextual",
     "may be attributing a result to an artefact", False),
    ("is not", "contextual", "a negation, which may be about anything", False),
    ("does not", "contextual", "a negation, which may be about anything",
     False),
    ("cannot", "contextual", "a negation, which may be about anything", False),
    ("myth", "contextual", "may be calling a received view a myth", False),
)

# A leading quote, bracket or article, so `"Comment on ..."` and
# `[Comment on ...]` still count as leading with the phrase.
_TITLE_LEAD_RE = re.compile(r'^[\s"\'“‘\[(]+')

# What a published Comment or Reply says next: the thing it is arguing with.
# Either a quoted title, or an author, or the word for a piece of writing.
_TARGET_RE = re.compile(
    r'^\s*(?:'
    r'["\'“‘]'                      # Comment on "A bacterium ..."
    r'|[A-Z][\w-]+\s+(?:et al|and\b|&|\()'    # Reply to Smith et al.
    # Case matters for the surname alternative above - under IGNORECASE
    # `arsenic and phosphorus` matches the author pattern - so the flag is
    # scoped to the word list that needs it rather than applied to the whole.
    r'|(?i:(?:the\s+)?(?:comment|reply|response|critique|letter|paper'
    r'|article|study|report|work|manuscript|hypothesis|claim|analysis'
    r'|review)\b)'
    r')')


def _names_a_target(rest: str) -> bool:
    """Does what follows the phrase name the thing being argued with?

    The discriminator that the leading-position test alone could not make.
    Measured: *Response to Arsenic in Rhodococcus aetherivorans BCP1* leads
    with `response to` and is a bacterium responding to arsenic. A published
    Response leads with the phrase AND then names its target - a quoted
    title, an author with `et al.`, or the word for a piece of writing. A
    biological response is followed by a substance.
    """
    return bool(_TARGET_RE.match(rest or ""))


def dispute_signals(title: str) -> list[dict]:
    """Every dispute phrase in one citing title, with its tier."""
    raw = title or ""
    low = raw.lower()
    lead = _TITLE_LEAD_RE.match(raw)
    start = lead.end() if lead else 0
    out = []
    for phrase, tier, why, anchored in DISPUTE_PHRASES:
        if phrase not in low:
            continue
        if not anchored:
            out.append({"phrase": phrase, "tier": tier, "why": why})
            continue
        if not low[start:].startswith(phrase):
            out.append({"phrase": phrase, "tier": "contextual",
                        "why": f"{why} - but the title does not lead with "
                               f"the phrase, so it is probably about "
                               f"something else"})
        elif not _names_a_target(raw[start + len(phrase):]):
            out.append({"phrase": phrase, "tier": "contextual",
                        "why": f"{why} - but what follows is not a paper or "
                               f"an author, so this is probably a different "
                               f"sense of the word"})
        else:
            out.append({"phrase": phrase, "tier": tier, "why": why})
    return out


def disputed(ident: str, limit: int = 50, since: str = "",
             sources: list[str] | None = None) -> dict:
    """Later papers whose titles suggest they disagree with this one.

    A reading list, never a verdict. `signals: []` means "nothing in the N
    citing titles that were read said so" - which is not a clean bill of
    health, and the payload says that in words rather than leaving a caller
    to infer it. Same rule as `is_retracted: false` (spec 9.7).
    """
    out: dict = {
        "id": ident, "checked": False, "source": "", "sources_tried": [],
        "failures": [], "searched": 0, "signals": [], "retraction": {},
        # `review.py echo`'s precedent: something that ships uncalibrated says
        # so in its own payload rather than in a document nobody opens.
        "calibrated": False, "advisory": True,
        "means": "titles of the citing papers, matched against a phrase list. "
                 "A hit is a paper worth reading, not a fault in your "
                 "citation. An empty list means nothing in the titles that "
                 "were read said so - it is not evidence that nobody "
                 "disagrees.",
    }

    # The strong form of the same question, and it arrives with it.
    ident_is_doi = bool(clean_doi(ident))
    seen = verify(doi=ident if ident_is_doi else "",
                  pmid="" if ident_is_doi else str(ident))
    match = seen.get("match") or {}
    out["cited_paper"] = {"title": match.get("title", ""),
                          "year": match.get("year", ""),
                          "journal": match.get("journal", ""),
                          "doi": match.get("doi", ""),
                          "source": seen.get("source", "")}
    out["retraction"] = {
        "is_retracted": bool(match.get("is_retracted")),
        "notices": match.get("notices", []),
        "flags": [f for f in seen.get("flags", [])
                  if "RETRACT" in f.upper() or "CONCERN" in f.upper()
                  or "ERRATUM" in f.upper()],
    }

    order = [n for n in (sources or ["openalex", "semanticscholar"])
             if n in SOURCES]
    recs: list[dict] = []
    for name in order:
        out["sources_tried"].append(name)
        try:
            recs = SOURCES[name].cited_by(ident, limit) or []
        except SourceUnreachable as exc:
            out["failures"].append({"source": name, "reason": exc.reason})
            continue
        if recs:
            out["source"] = name
            break
    if not recs and len(out["failures"]) == len(out["sources_tried"]):
        # Not "nothing disagrees". The citation graph was unreachable, and
        # those are different sentences.
        out["means"] = ("no source answered, so nothing was read - this is a "
                        "source failure, not evidence about the citation")
        return out

    out["checked"] = True
    out["searched"] = len(recs)
    year_floor = str(since or "").strip()
    for rec in recs:
        year = str(rec.get("year") or "")
        if year_floor and year and year < year_floor:
            continue
        hits = dispute_signals(rec.get("title", ""))
        if not hits:
            continue
        out["signals"].append({
            "title": rec.get("title", ""),
            "year": year,
            "journal": rec.get("journal", ""),
            "doi": rec.get("doi", ""),
            "source": rec.get("source", out["source"]),
            "tier": ("asserted" if any(h["tier"] == "asserted" for h in hits)
                     else "contextual"),
            "matched": hits,
        })
    out["signals"].sort(key=lambda s: (s["tier"] != "asserted",
                                       -int(s["year"] or 0)))
    out["counts"] = {
        "searched": out["searched"],
        "signals": len(out["signals"]),
        "asserted": sum(1 for s in out["signals"] if s["tier"] == "asserted"),
        "contextual": sum(1 for s in out["signals"]
                          if s["tier"] == "contextual"),
    }
    return out


def print_disputed(res: dict) -> None:
    cited = res.get("cited_paper") or {}
    if cited.get("title"):
        print(f"{cited['title']}  ({cited.get('year','')}, "
              f"{cited.get('journal','')})")
    r = res.get("retraction") or {}
    for flag in r.get("flags", []):
        print(f"  !!  {flag}")
    if not res.get("checked"):
        print(f"  not checked - {res.get('means','')}")
        for f in res.get("failures", []):
            print(f"  !  {f['source']} did not answer ({f['reason']})")
        return
    n = len(res.get("signals", []))
    print(f"\n{res['searched']} citing paper(s) read from "
          f"{res.get('source','')}; {n} whose title suggests disagreement.\n")
    for s in res["signals"]:
        print(f"  [{s['tier']}]  {s.get('year','')}  {s['title']}")
        print(f"            {s.get('doi','') or '(no DOI)'}  - "
              f"{', '.join(h['phrase'] for h in s['matched'])}")
    print("\nSignals, not verdicts. A hit is a paper to read; an empty list "
          "means nothing in the titles read said so, which is not the same "
          "as nobody disagreeing.")


# ---------------------------------------------------------------------------
# corpus-fill - the minimal record any project can have
# (specs/citation-integrity-2026-09-20.md 6.3)
# ---------------------------------------------------------------------------
#
# attribution-check reads `data/corpus/papers/<citekey>.md`, and the only
# writer of those files was `review.py`. A folder this engine did not
# scaffold has no `data/corpus/` at all, so every (sentence, source) pair
# came back `not retrieved` - and a table of forty rows saying so was
# counted among the modules that ran. That is what happened to a member's
# paper on 2026-09-20.
#
# This writes the floor: one record per bibliography entry, in the format
# review.py writes, at the tier retrieval actually reached. It adds NO
# retrieval machinery - the cascade above resolves the entry, `pubmed.py
# sections` fetches open-access full text where PMC has it, and the
# abstract is the floor.

CORPUS_TIER_FLOOR = "title"


def project_bib(project: str) -> str:
    """The bibliography of a project, trimmed layout or not.

    `drafts/references.bib` in a scaffolded project; the project root in a
    trimmed one. Named here rather than guessed at the call site, because a
    path that is wrong in a diagnostic is the diagnostic the user trusts.
    """
    for rel in (os.path.join("drafts", "references.bib"), "references.bib"):
        path = os.path.join(project, rel)
        if os.path.isfile(path):
            return path
    return os.path.join(project, "drafts", "references.bib")


def _source_kind_of(rec: dict) -> str:
    kinds = {str(t).lower() for t in (rec or {}).get("pubtypes", [])}
    if kinds & {"review", "systematic review", "review-article"}:
        return "review"
    if "preprint" in kinds:
        return "preprint"
    return "primary"


def corpus_fill(project: str, bib: str = "", limit: int = 0,
                overwrite: bool = False, sources: list[str] | None = None,
                full_text: bool = True) -> dict:
    """One corpus record per bibliography entry that does not have one.

    Refuses on a project that carries a corpus protocol. A systematic
    review's tiers are a claim about a screening procedure, and a
    convenience fetch must not be able to raise one: on those projects
    `review.py` stays the only writer of `read_tier`.
    """
    import review  # stdlib-only sibling; imported here so the CLI stays light

    res: dict = {"project": project, "bib": "", "written": [], "skipped": [],
                 "by_tier": {}, "errors": [], "notes": []}

    protocol = review.protocol_path(project)
    if os.path.isfile(protocol):
        res["errors"].append(
            "%s exists, so this project's corpus is a REVIEW's corpus and "
            "`review.py` is the only writer of its read tiers. A systematic "
            "review's tier is a claim about a screening procedure, and a "
            "convenience fetch must not be able to raise one. Use "
            "`review.py screen` and `review.py record` instead."
            % protocol.replace(os.sep, "/"))
        return res

    path = bib or project_bib(project)
    res["bib"] = path
    if not os.path.isfile(path):
        res["errors"].append(
            "no bibliography at %s - there is nothing to write records from."
            % path.replace(os.sep, "/"))
        return res

    refs = load_refs(path)
    order = [s for s in (sources or CASCADE) if s in SOURCES]
    for ref in refs:
        key = ref.get("key", "")
        if not key:
            res["skipped"].append({"key": "(no citekey)", "why":
                                   "the entry has no citekey, and the "
                                   "citekey is the record's filename"})
            continue
        if not overwrite and os.path.isfile(review.record_path(project, key)):
            res["skipped"].append({"key": key, "why": "a record already "
                                                      "exists"})
            continue
        if limit and len(res["written"]) >= limit:
            res["skipped"].append({"key": key, "why": "--limit reached"})
            continue

        kind = classify_source(ref)
        if not kind["verify"]:
            # The same rule check-refs follows: a database record is not a
            # paper and never reaches the cascade. It still gets a record,
            # at `title`, so the pass can say what it is rather than
            # reporting it as unretrieved.
            review.record_cmd(
                project, key, tier=CORPUS_TIER_FLOOR,
                title=ref.get("title", ""), doi=ref.get("doi", ""),
                year=str(ref.get("year", "") or ""),
                authors=ref.get("authors", []),
                source_kind="primary", found_by="handpicked",
                verified="not-a-paper",
                claims="<!-- %s. No literature index answers questions "
                       "about it, so nothing was retrieved. -->"
                       % kind["why"])
            res["written"].append({"key": key, "read_tier": CORPUS_TIER_FLOOR,
                                   "why": "not a paper: %s" % kind["why"]})
            res["by_tier"][CORPUS_TIER_FLOOR] = \
                res["by_tier"].get(CORPUS_TIER_FLOOR, 0) + 1
            continue

        try:
            ver = verify(title=ref.get("title", ""), doi=ref.get("doi", ""),
                         pmid=str(ref.get("pmid", "") or ""),
                         authors=ref.get("authors", []),
                         year=str(ref.get("year", "") or ""), sources=order)
        except Exception as exc:  # a source failure is not a verdict
            res["errors"].append("%s: %s" % (key, exc))
            continue

        match = ver.get("match") or {}
        tier = CORPUS_TIER_FLOOR
        retrieved: list[str] = []
        claims = reports = ""
        if ver["status"] != "verified":
            res["skipped"].append({
                "key": key,
                "why": "%s - nothing was retrieved, so no record was written. "
                       "A record asserting a tier it did not reach is worse "
                       "than no record." % ver["status"]})
            continue

        abstract = (match.get("abstract") or "").strip()
        if abstract:
            tier = "abstract"
            retrieved.append("abstract")
            claims = abstract
        # Full text only where PMC actually has it. A miss here is ordinary:
        # measured on a real chemistry topic, ZERO of 25 candidates carried a
        # PMC identifier, so an abstract-tier record is the normal outcome in
        # half of this user's field and not a failure.
        if full_text and match.get("pmid"):
            try:
                got = pubmed.fetch_sections(
                    [str(match["pmid"])],
                    ["introduction", "methods", "results", "discussion",
                     "conclusions"])
            except Exception as exc:
                res["notes"].append(
                    "%s: full text was not reachable (%s), so the record "
                    "stays at %s." % (key, exc, tier))
                got = []
            secs = (got[0]["sections"] if got else {}) or {}
            body = [(name, text) for name, text in secs.items() if text.strip()]
            if body:
                tier = "fulltext"
                retrieved.extend(name for name, _ in body)
                reports = "\n\n".join("### %s\n\n%s" % (name, text)
                                      for name, text in body)

        review.record_cmd(
            project, key, tier=tier,
            title=match.get("title", "") or ref.get("title", ""),
            doi=match.get("doi", "") or ref.get("doi", ""),
            pmid=str(match.get("pmid", "") or ""),
            year=str(match.get("year", "") or ref.get("year", "") or ""),
            journal=match.get("journal", ""),
            authors=match.get("authors", []),
            verified="yes",
            retracted="yes" if match.get("is_retracted") else "no",
            retraction_checked=datetime.date.today().isoformat(),
            source_kind=_source_kind_of(match),
            found_by="handpicked",
            retrieved=", ".join(retrieved) or "nothing",
            claims=claims or "<!-- no abstract was retrievable -->",
            reports=reports)
        res["written"].append({"key": key, "read_tier": tier,
                               "retrieved": retrieved})
        res["by_tier"][tier] = res["by_tier"].get(tier, 0) + 1

    res["notes"].append(
        "Every record here was written by a CONVENIENCE fetch, not by a "
        "screening procedure. `read_tier` says what was actually retrieved "
        "and the `## Limits of this record` section says what that does not "
        "license - an abstract-tier record licenses the paper's own stated "
        "claim and nothing about its n, its conditions or its figures.")
    return res


def print_corpus_fill(res: dict) -> None:
    for e in res["errors"]:
        print("REFUSED  %s" % e)
    if res["errors"]:
        return
    print("%s" % res["bib"])
    for w in res["written"]:
        print("  wrote  %-28s tier %s" % (w["key"], w["read_tier"]))
    for sk in res["skipped"]:
        print("  -      %-28s %s" % (sk["key"], sk["why"]))
    print("\n%d record(s) written, %d skipped."
          % (len(res["written"]), len(res["skipped"])))
    if res["by_tier"]:
        print("by tier: " + ", ".join("%s %d" % (k, v) for k, v
                                      in sorted(res["by_tier"].items())))
    for n in res["notes"]:
        print("\n%s" % n)


def classify_refs(refs: list[dict]) -> list[dict]:
    """`classify_source` over a list, each answer beside its entry."""
    return [{"key": r.get("key", ""), "title": r.get("title", ""),
             "entry_type": r.get("entry_type", ""),
             **classify_source(r)} for r in refs]


def verify(title: str = "", doi: str = "", pmid: str = "",
           authors: list[str] | None = None, year: str = "",
           sources: list[str] | None = None) -> dict:
    """Decide whether a claimed citation corresponds to a real paper, in any of
    the configured indexes.

    Superset of pubmed.verify(): same keys and the same status values, plus
    `source`, `sources_tried` and `unreachable`. Stops at the first source that
    verifies; a `partial` never stops the cascade, because a later index may
    confirm the paper outright.
    """
    order = [s for s in (sources or CASCADE) if s in SOURCES]
    claim = {"title": title, "doi": clean_doi(doi), "pmid": str(pmid or "").strip(),
             "authors": authors or [], "year": str(year or "")}
    result = {"claim": claim, "status": "not_found", "confidence": 0.0,
              "route": "", "source": "", "sources_tried": [], "unreachable": [],
              "match": None, "candidates": [], "flags": [], "notes": [],
              # citation-integrity 4. The notes below are also prose in
              # `notes`, and they are counted HERE as well because a
              # forty-entry list is read through its summary: seven quiet
              # notes inside forty printed records is exactly how the byline
              # and year defects were missed the first time.
              "integrity": {"byline_differences": [], "year_notes": []}}

    best_partial: tuple[float, dict] | None = None

    for name in order:
        src = SOURCES[name]
        result["sources_tried"].append(name)
        try:
            recs = _probe_source(src, claim)
        except SourceUnreachable as exc:
            result["unreachable"].append({"source": name, "reason": exc.reason})
            result["notes"].append(
                f"{name} did not answer ({exc.reason}) - that is a source failure, "
                f"not evidence about the citation.")
            continue

        if not recs:
            continue

        scored = []
        for rec in recs:
            id_hit, sim = _score(claim, rec)
            scored.append((id_hit, sim, rec))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        result["candidates"].extend(_candidate(r, s, h) for h, s, r in scored[:3])

        id_hit, sim, best = scored[0]
        route = ("pmid" if (id_hit and claim["pmid"] and best["pmid"] == claim["pmid"])
                 else "doi" if (id_hit and claim["doi"]) else "title")

        if id_hit or sim >= VERIFIED_MIN:
            result.update(status="verified", confidence=1.0 if id_hit else round(sim, 3),
                          route=route, source=name, match=best)
            # A preprint is a real document, so it verifies - but it is not the
            # version of record, and it has not been peer reviewed. Saying so is
            # the whole point: the cascade reaches arXiv only when no journal
            # index held the paper, which is precisely when a writer is most
            # likely to cite it without noticing what it is.
            if any(t.lower() == "preprint" for t in best.get("pubtypes", [])):
                # Says only what was observed. arXiv is not the only source that
                # reports preprints - OpenAlex types them too - so this names the
                # source that said so rather than claiming no journal has it.
                result["flags"].append(
                    f"PREPRINT - {name} reports this as a preprint, not a "
                    f"peer-reviewed version of record. It is real, but it may "
                    f"differ from any published version. Cite it as a preprint, "
                    f"or find the version of record.")
            enrich_abbrev(best)
            _retraction_sweep(best, result)
            _cross_checks(claim, best, sim, result)
            result["candidates"] = [c for c in result["candidates"]
                                    if not (c["source"] == name
                                            and c["title"] == best["title"])]
            return result

        if sim >= PARTIAL_MIN and (best_partial is None or sim > best_partial[0]):
            best_partial = (sim, best)

    if best_partial:
        sim, best = best_partial
        # Shown, not adopted. `match` stays null so no caller can mistake a
        # near-miss for a confirmed citation, and no DOI is emitted below the
        # verified threshold.
        result.update(status="partial", confidence=round(sim, 3),
                      route="title", source=best["source"])
        result["flags"].append(
            f"Closest match scored {sim:.2f} on title similarity - below the "
            f"{VERIFIED_MIN} threshold, so it was NOT adopted. Check the wording "
            f"against the candidates before citing it.")
        result["notes"].append(
            "No DOI is reported for a partial match: attaching an unconfirmed "
            "identifier to a citation is the failure this engine exists to prevent.")
        return result

    tried = ", ".join(result["sources_tried"]) or "no sources"
    if result["unreachable"]:
        down = ", ".join(u["source"] for u in result["unreachable"])
        result["notes"].append(
            f"Searched {tried}, but {down} did not answer. This is an incomplete "
            f"search, not a verdict - re-run before concluding anything.")
    else:
        result["notes"].append(f"No record matched this citation in: {tried}.")
        result["notes"].append(
            "Several indexes were asked, so this is stronger than a PubMed-only "
            "miss - but a very new paper, a book chapter, or a thesis can still "
            "be real and absent from all of them.")
    return result


# ---------------------------------------------------------------------------
# Search: a merge, not a cascade
# ---------------------------------------------------------------------------

def search(term: str, limit: int = 10, year_from: str = "", year_to: str = "",
           sources: list[str] | None = None, pages: int = 1,
           want_all: bool = False) -> dict:
    """Query several indexes and merge them.

    Ranking never uses Crossref's `score`: it does not separate real papers from
    fabricated ones and is normalized to nothing. Agreement between indexes
    first, then citation count, then recency.

    `limit` is PER SOURCE and always has been - it is the depth each index is
    asked to go to. What changed (specs/review-paper.md 6) is what happens to
    the surplus: the union of four sources at `--limit 25` examines up to 100
    records, and returning `rows[:limit]` threw away three quarters of the
    work that had already been paid for. `want_all` returns the whole merged
    union; the payload's `returned` field has always reported its size and
    nothing read it. A corpus builder must never be handed a truncated union.

    `pages` asks each source for that many consecutive pages of `limit` rows,
    through whatever paging its own API offers. A review needs 200-600
    candidates and every index caps a single request well below that -
    Crossref and Europe PMC and arXiv at 100 rows, OpenAlex at 200.
    """
    pages = max(1, int(pages))
    order = [s for s in (sources or SEARCH_MERGE) if s in SOURCES]
    # A source that declares no free-text search route is dropped here rather
    # than queried and reported as returning nothing: "this index has no search
    # API" and "this index found no papers" are different facts, and only the
    # second one is about the literature.
    skipped = [s for s in order if not SOURCES[s].has_search]
    order = [s for s in order if SOURCES[s].has_search]
    merged: dict[str, dict] = {}
    tried, unreachable = [], []

    pages_read: dict[str, int] = {}
    for name in order:
        tried.append(name)
        src = SOURCES[name]
        recs: list[dict] = []
        want_pages = pages if src.paging else 1
        stream = src.search_pages(term, limit=limit, year_from=year_from,
                                  year_to=year_to, pages=want_pages)
        page = 0
        while True:
            try:
                got = next(stream)
            except StopIteration:
                break
            except SourceUnreachable as exc:
                # A source that dies on page 3 has still given two pages of
                # real literature, and throwing them away to report an
                # outage would make a deep sweep strictly worse than a
                # shallow one. The failure is reported either way.
                unreachable.append({"source": name, "reason": exc.reason,
                                    "page": page + 1})
                break
            page += 1
            pages_read[name] = page
            recs.extend(got)
        for rec in recs:
            key = clean_doi(rec["doi"]) or normalize_title(rec["title"])
            if not key:
                continue
            if key in merged:
                found = merged[key]["found_in"]
                if name not in found:
                    found.append(name)
                # Fill a gap from a second index, never blend two sources'
                # values for the same field into one.
                for field in ("abstract", "pmid", "pmc", "journal_abbrev", "issn",
                              "volume", "issue", "pages", "elocation"):
                    if not merged[key].get(field) and rec.get(field):
                        merged[key][field] = rec[field]
                for field in ("cited_by_count", "is_oa"):
                    if merged[key].get(field) is None and rec.get(field) is not None:
                        merged[key][field] = rec[field]
                if rec.get("is_retracted"):
                    merged[key]["is_retracted"] = True
            else:
                merged[key] = {**rec, "found_in": [name]}

    rows = sorted(merged.values(),
                  key=lambda r: (len(r["found_in"]),
                                 r.get("cited_by_count") or 0,
                                 int(r["year"]) if r["year"].isdigit() else 0),
                  reverse=True)
    out = rows if want_all else rows[:limit]
    return {"query": term, "sources_tried": tried, "unreachable": unreachable,
            "no_search_route": skipped,
            "pages_requested": pages, "pages_read": pages_read,
            "per_source_limit": limit,
            # Deliberately the number examined, not a hit count: Crossref
            # reported 2.6-7.6 million "hits" for every measured query because
            # query.bibliographic is a loose OR across the whole corpus.
            # Printing that would be actively misleading.
            "returned": len(rows),
            # What the caller is actually holding, and whether anything was
            # cut off getting there. `returned` has always been the union
            # size; a caller that reads only `results` and reports "26
            # candidates" is reporting a number it did not receive.
            "shown": len(out), "truncated": len(out) < len(rows),
            "results": out}


# ---------------------------------------------------------------------------
# PubChem - the substance side
# ---------------------------------------------------------------------------
#
# The closest legitimate stand-in for a SciFinder lookup. It is not SciFinder:
# no free source indexes CAS reaction data, and this does not pretend to.

PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound"
_CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


def _cas_check_digit_ok(rn: str) -> bool:
    """A CAS registry number's last digit is a checksum of the others.

    Arithmetic, not a guess - so it can say '209-077-7 is not a CAS number'
    without having to decide which of the remaining ones is canonical.
    """
    if not _CAS_RE.match(rn or ""):
        return False
    digits = rn.replace("-", "")
    body, check = digits[:-1], int(digits[-1])
    return sum(int(d) * i for i, d in enumerate(reversed(body), start=1)) % 10 == check


def _looks_like_smiles(q: str) -> bool:
    return (bool(re.fullmatch(r"[A-Za-z0-9@+\-\[\]()=#$%/\\.]{4,}", q or ""))
            and bool(re.search(r"[=#\[\]()]", q or "")))


def compound(query: str) -> dict:
    out = {"query": query, "cid": None, "iupac_name": "", "formula": "",
           "molecular_weight": "", "smiles": "", "inchikey": "", "synonyms": [],
           "registry_numbers": [], "url": "", "notes": []}
    ident = quote(query, safe="")
    routes = ["smiles", "name"] if _looks_like_smiles(query) else ["name", "smiles"]

    cids = []
    for route in routes:
        data = _get("pubchem", f"{PUBCHEM}/{route}/{ident}/cids/JSON")
        cids = ((data or {}).get("IdentifierList") or {}).get("CID") or []
        if cids:
            break
    if not cids:
        out["notes"].append(f"PubChem has no compound matching '{query}'.")
        return out
    cid = cids[0]
    out["cid"] = cid
    out["url"] = f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}"
    if len(cids) > 1:
        out["notes"].append(
            f"{len(cids)} CIDs matched; this is the first. Others: "
            f"{', '.join(str(c) for c in cids[1:6])}.")

    # Measured 2026-09-03: PubChem now answers with `SMILES` and
    # `ConnectivitySMILES` and SILENTLY DROPS the older CanonicalSMILES /
    # IsomericSMILES names from the response rather than erroring. Both
    # spellings are read so a rename does not quietly empty the field.
    props = ("MolecularFormula,MolecularWeight,SMILES,ConnectivitySMILES,"
             "CanonicalSMILES,IsomericSMILES,InChIKey,IUPACName")
    data = _get("pubchem", f"{PUBCHEM}/cid/{cid}/property/{props}/JSON")
    rows = ((data or {}).get("PropertyTable") or {}).get("Properties") or [{}]
    p = rows[0]
    out["formula"] = str(p.get("MolecularFormula") or "")
    out["molecular_weight"] = str(p.get("MolecularWeight") or "")
    out["smiles"] = str(p.get("IsomericSMILES") or p.get("SMILES")
                        or p.get("CanonicalSMILES") or p.get("ConnectivitySMILES") or "")
    out["inchikey"] = str(p.get("InChIKey") or "")
    out["iupac_name"] = str(p.get("IUPACName") or "")

    data = _get("pubchem", f"{PUBCHEM}/cid/{cid}/synonyms/JSON")
    info = ((data or {}).get("InformationList") or {}).get("Information") or [{}]
    out["synonyms"] = (info[0].get("Synonym") or [])[:10]

    data = _get("pubchem", f"{PUBCHEM}/cid/{cid}/xrefs/RN/JSON")
    info = ((data or {}).get("InformationList") or {}).get("Information") or [{}]
    for rn in info[0].get("RN") or []:
        out["registry_numbers"].append({
            "value": rn,
            "cas_format": bool(_CAS_RE.match(rn)),
            "cas_check_digit_ok": _cas_check_digit_ok(rn),
        })
    if out["registry_numbers"]:
        # Measured: trimesic acid returns four numbers - a real CAS RN, two
        # further registrations, and 209-077-7, which is an EINECS number.
        # Picking one silently would put a wrong CAS number in a manuscript, so
        # the list is returned whole and unranked. Authoritative disambiguation
        # is what CAS Common Chemistry would give, and it needs a key.
        out["notes"].append(
            "PubChem returns several registry numbers and does not say which is "
            "the canonical CAS RN. The list is unranked; `cas_check_digit_ok` is "
            "arithmetic on the number itself, not a claim about which to cite. "
            "Confirm against the supplier or CAS before it goes in a manuscript.")
    return out


# ---------------------------------------------------------------------------
# Text output
# ---------------------------------------------------------------------------

def _print_records(records: list[dict]) -> None:
    if not records:
        print("No results.")
        return
    for i, r in enumerate(records, 1):
        authors = r.get("authors", [])
        who = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        print(f"\n[{i}] {r.get('title', '')}")
        print(f"    {who or '(no authors listed)'}")
        bits = [f"{r.get('journal_abbrev') or r.get('journal', '')} "
                f"{r.get('year', '')}".strip()]
        if r.get("doi"):
            bits.append(f"doi:{r['doi']}")
        if r.get("pmid"):
            bits.append(f"PMID {r['pmid']}")
        if r.get("cited_by_count") is not None:
            bits.append(f"cited {r['cited_by_count']}x")
        if r.get("found_in"):
            bits.append("in " + "+".join(r["found_in"]))
        elif r.get("source"):
            bits.append(f"via {r['source']}")
        print("    " + " | ".join(b for b in bits if b))
        if r.get("is_retracted"):
            print("    ** RETRACTED PUBLICATION **")
        for n in r.get("notices", []):
            print(f"    ** {n['type']}: {n.get('ref', '')} **")
        if r.get("abstract"):
            body = r["abstract"]
            print(f"    {body[:400]}{'...' if len(body) > 400 else ''}")
    print()


def _print_verify(res: dict) -> None:
    label = (res["claim"].get("title") or res["claim"].get("doi")
             or res["claim"].get("pmid") or "(empty citation)")
    # `.get` with a default rather than a subscript, and the default is the
    # status itself. A subscript here crashed the whole command the first time
    # a new status reached it - `not_a_paper`, found by running check-refs over
    # a real bibliography rather than over the suite, which exercises the JSON
    # path. A printer that cannot render an unfamiliar value should say the
    # value, not take the program down; this is the same call-site shape as
    # item 80, one layer out.
    icon = {"verified": "OK", "partial": "PARTIAL", "not_found": "NOT FOUND",
            "not_a_paper": "NOT A PAPER"}.get(res["status"],
                                              res["status"].upper())
    flags = res.get("flags", [])
    # Status answers "does it exist", flags answer "should you cite it". A
    # record that exists but is retracted must not read as [OK] at a glance.
    if res["status"] == "verified" and flags:
        icon = "RETRACTED" if any(
            "RETRACT" in f.upper() or "EXPRESSION OF CONCERN" in f.upper() for f in flags
        ) else "CHECK"
    print(f"[{icon}] {label}")
    if res.get("match"):
        m = res["match"]
        print(f"  -> {m['title']}")
        line = f"     {m.get('journal_abbrev') or m.get('journal')} {m['year']}"
        if m.get("doi"):
            line += f" | doi:{m['doi']}"
        if m.get("pmid"):
            line += f" | PMID {m['pmid']}"
        print(line)
        print(f"     confidence {res['confidence']} "
              f"(matched via {res['route']} in {res['source']}; "
              f"asked {', '.join(res['sources_tried'])})")
    for f in flags:
        print(f"  !  {f}")
    for n in res.get("notes", []):
        print(f"  -  {n}")
    if res["status"] != "verified" and res.get("candidates"):
        print("     closest candidates (not adopted):")
        for c in res["candidates"][:3]:
            ident = c.get("doi") or c.get("pmid") or c.get("source_id")
            print(f"       {c['similarity']:.2f}  {c['title'][:66]} "
                  f"[{c['source']} {ident}]")


def _print_compound(c: dict) -> None:
    if not c["cid"]:
        print(f"[NOT FOUND] {c['query']}")
        for n in c["notes"]:
            print(f"  -  {n}")
        return
    print(f"[OK] {c['query']}  ->  CID {c['cid']}")
    for key, label in (("iupac_name", "IUPAC name"), ("formula", "formula"),
                       ("molecular_weight", "MW"), ("smiles", "SMILES"),
                       ("inchikey", "InChIKey")):
        if c[key]:
            print(f"  {label:12} {c[key]}")
    if c["registry_numbers"]:
        print("  registry no. " + ", ".join(
            f"{r['value']}{'' if r['cas_check_digit_ok'] else ' (not a valid CAS RN)'}"
            for r in c["registry_numbers"]))
    if c["synonyms"]:
        print(f"  synonyms     {', '.join(c['synonyms'][:5])}")
    print(f"  url          {c['url']}")
    for n in c["notes"]:
        print(f"  -  {n}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

SOURCE_CHOICES = ["auto", "all"] + ALL_SOURCES


def _source_arg(value: str) -> str:
    """`--source` as the vocabulary's owner validates it.

    A comma-joined list is accepted, because a corpus sweep asks several
    indexes at once and every caller that wanted that wrote the join itself -
    `review.py search` did, and argparse `choices=` rejected the whole string,
    so every query in a protocol naming two indexes failed with a usage
    message and the sweep returned nothing. The names are checked here rather
    than at the caller so the error says which name is wrong and what the
    vocabulary is, instead of reprinting a usage block.
    """
    names = [n.strip().lower() for n in str(value).split(",") if n.strip()]
    if not names:
        raise argparse.ArgumentTypeError(
            "--source is empty; give one of " + ", ".join(SOURCE_CHOICES))
    bad = [n for n in names if n not in SOURCE_CHOICES]
    if bad:
        raise argparse.ArgumentTypeError(
            f"unknown index {', '.join(bad)} - "
            f"choose from {', '.join(SOURCE_CHOICES)}")
    if "auto" in names and len(names) > 1:
        raise argparse.ArgumentTypeError(
            "auto means 'the default order for this command' and cannot be "
            "combined with a named index; name the indexes you want, or drop "
            "--source")
    seen: list[str] = []
    for n in names:
        if n not in seen:
            seen.append(n)
    return ",".join(seen)


def _resolve_sources(choice: str, default: list[str]) -> list[str]:
    names = [n.strip().lower() for n in str(choice).split(",") if n.strip()]
    if not names or names == ["auto"]:
        return default
    # "all" means every registered index, including the ones the default order
    # leaves out on purpose. Asking for all of them is a deliberate act, and
    # it wins over anything named beside it.
    if "all" in names:
        return ALL_SOURCES
    return [n for n in names if n in ALL_SOURCES]


def main() -> int:
    p = argparse.ArgumentParser(prog="scholar.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")

    # --json is accepted on either side of the subcommand. SUPPRESS keeps the
    # subparser from resetting a flag already set before it. Skills depend on
    # this and it silently errored in the trailing position once already.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="emit JSON instead of text")
    common.add_argument("--source", default="auto", type=_source_arg,
                        metavar="NAME[,NAME...]",
                        help="which index to ask, or several comma-joined: "
                             + ", ".join(SOURCE_CHOICES) + " (default: auto)")

    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="search several indexes and merge", parents=[common])
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10,
                   help="how deep to go PER SOURCE, not how many rows come "
                        "back. Four sources at --limit 25 examine up to 100 "
                        "records")
    s.add_argument("--all", dest="want_all", action="store_true",
                   help="return the whole merged union instead of the first "
                        "--limit of it. A corpus builder wants this: the "
                        "surplus has already been paid for, and throwing it "
                        "away is how a sweep four indexes deep gets reported "
                        "as one index's ranking")
    s.add_argument("--pages", type=int, default=1, metavar="N",
                   help="ask each source for N consecutive pages of --limit "
                        "rows. Every index caps one request well below what "
                        "a review needs - Crossref, Europe PMC and arXiv at "
                        "100 rows, OpenAlex at 200")
    s.add_argument("--from", dest="year_from", default="", help="earliest publication year")
    s.add_argument("--to", dest="year_to", default="", help="latest publication year")

    v = sub.add_parser("verify", help="verify one citation is a real paper", parents=[common])
    v.add_argument("--title", default="")
    v.add_argument("--doi", default="")
    v.add_argument("--pmid", default="")
    v.add_argument("--author", action="append", default=[], dest="authors")
    v.add_argument("--year", default="")
    v.add_argument("--claim", default="", metavar="SENTENCE",
                   help="the sentence this citation is attached to. When the "
                        "record turns out to be a REVIEW, its own reference "
                        "list is read and up to three primary-source "
                        "candidates sharing this sentence's distinctive terms "
                        "are named (citation-integrity 7). Candidates, never "
                        "a substitution")

    c = sub.add_parser("check-refs", parents=[common],
                       help="verify a reference list (.json, .bib, or one title per line)")
    c.add_argument("path")
    c.add_argument("--fail-on-problem", action="store_true",
                   help="exit 1 if any reference is unverified, mismatched, or retracted")
    # Item 105. The default is unchanged and stays unchanged: classify,
    # report, refuse nothing. A paper may legitimately cite a preprint or a
    # PDB entry, and deciding that is the user's. What was missing is any way
    # for a project that HAS decided to make the decision stick, so the rule
    # stopped living in the project and started living in whoever remembered
    # it.
    c.add_argument("--peer-reviewed-only", action="store_true",
                   help="this project's reference list is peer-reviewed "
                        "papers only: an entry the record says is NOT peer "
                        "reviewed becomes a blocking defect and exits 1. An "
                        "entry whose record does not say is reported as "
                        "unknown and asked about, never counted as a failure")

    cfl = sub.add_parser("corpus-fill", parents=[common],
                         help="write the minimal corpus record for every "
                              "bibliography entry that has none, so "
                              "attribution-check has something to read")
    cfl.add_argument("project")
    cfl.add_argument("--bib", default="",
                     help="the bibliography to read; by default "
                          "drafts/references.bib, or references.bib in a "
                          "trimmed project")
    cfl.add_argument("--limit", type=int, default=0,
                     help="write at most N records this run (0 = no limit)")
    cfl.add_argument("--overwrite", action="store_true",
                     help="rewrite records that already exist. Off by "
                          "default: a record somebody read a paper into is "
                          "worth more than one this command can fetch")
    cfl.add_argument("--no-full-text", dest="full_text", action="store_false",
                     help="abstract tier only - skip the PMC full-text read")

    dp = sub.add_parser("disputed", parents=[common],
                        help="later papers whose titles suggest they disagree "
                             "with this one - signals, never verdicts")
    dp.add_argument("id", help="a DOI or a PMID")
    dp.add_argument("--limit", type=int, default=50,
                    help="how many citing papers to read (default 50)")
    dp.add_argument("--since", default="",
                    help="only citing papers from this year on")

    stp = sub.add_parser("source-types", parents=[common],
                         help="what KIND each reference is - offline, no "
                              "network, no index")
    stp.add_argument("path")

    ct = sub.add_parser("cite", help="format DOIs or PMIDs as citations", parents=[common])
    ct.add_argument("ids", nargs="+")
    ct.add_argument("--style", default="ama", choices=["ama", "acs", "apa", "bibtex"])

    r = sub.add_parser("related", help="papers related to a DOI or id", parents=[common])
    r.add_argument("id")
    r.add_argument("--limit", type=int, default=10)

    cb = sub.add_parser("cited-by", help="papers citing a DOI (OpenAlex)", parents=[common])
    cb.add_argument("id")
    cb.add_argument("--limit", type=int, default=10)

    rf = sub.add_parser("references", parents=[common],
                        help="papers a DOI cites (Semantic Scholar)")
    rf.add_argument("id")
    rf.add_argument("--limit", type=int, default=20)

    cp = sub.add_parser("compound", help="substance data from PubChem", parents=[common])
    cp.add_argument("query")

    sub.add_parser("status", help="which sources answer right now", parents=[common])

    args = p.parse_args()
    as_json = getattr(args, "json", False)

    if args.cmd == "status":
        info = {"sources": [], "keys_required": "none",
                "note": "All sources here are keyless. Crossref and OpenAlex run in "
                        "the anonymous pool; set SCHOLAR_EMAIL in the credentials file "
                        "`pubmed.py status` names - never a file "
                        "inside the toolkit, which an update "
                        "replaces - only if "
                        "throttling ever bites. Semantic Scholar is keyless for its "
                        "DOI-addressed routes (used by related/cited-by/references) "
                        "but its search route needs a free key, so it is not in the "
                        "default verify cascade."}
        for name in ALL_SOURCES:
            ok, why = SOURCES[name].available()
            info["sources"].append({
                "source": name, "ok": ok, "detail": why,
                "in_default_cascade": name in CASCADE,
                "searchable": SOURCES[name].has_search})
        try:
            probe = compound("water")
            info["sources"].append({"source": "pubchem", "ok": bool(probe["cid"]),
                                    "detail": "ok" if probe["cid"] else "no answer"})
        except SourceUnreachable as exc:
            info["sources"].append({"source": "pubchem", "ok": False, "detail": exc.reason})
        if as_json:
            print(json.dumps(info, indent=2))
        else:
            for row in info["sources"]:
                extra = "" if row.get("in_default_cascade", True) else "  [not in default cascade]"
                print(f"  {row['source']:16} "
                      f"{'ok' if row['ok'] else 'FAILED':8} {row['detail']}{extra}")
            print(f"\n{info['note']}")
        return 0 if all(row["ok"] for row in info["sources"]) else 2

    if args.cmd == "search":
        res = search(args.query, limit=args.limit, year_from=args.year_from,
                     year_to=args.year_to,
                     sources=_resolve_sources(args.source, SEARCH_MERGE),
                     pages=args.pages, want_all=args.want_all)
        if as_json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"{res['returned']} records examined across "
                  f"{', '.join(res['sources_tried'])} for: {args.query}")
            for u in res["unreachable"]:
                page = f" on page {u['page']}" if u.get("page") else ""
                print(f"  !  {u['source']} did not answer{page} "
                      f"({u['reason']})")
            # What was CUT is said out loud. "26 candidates" printed over a
            # list of 25 is how a truncated union gets read as a field.
            if res.get("truncated"):
                print(f"  showing {res['shown']} of {res['returned']} - "
                      f"--all returns the whole union")
            _print_records(res["results"])
        return 0

    if args.cmd == "verify":
        if not (args.title or args.doi or args.pmid):
            p.error("verify needs at least one of --title, --doi, or --pmid")
        res = verify(title=args.title, doi=args.doi, pmid=args.pmid,
                     authors=args.authors, year=args.year,
                     sources=_resolve_sources(args.source, CASCADE))
        if res["status"] == "verified":
            _review_source_note(res, args.claim)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else _print_verify(res)
        return 0 if (res["status"] == "verified" and not res["flags"]) else 1

    if args.cmd == "corpus-fill":
        res = corpus_fill(args.project, bib=args.bib, limit=args.limit,
                          overwrite=args.overwrite,
                          sources=_resolve_sources(args.source, CASCADE),
                          full_text=args.full_text)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json             else print_corpus_fill(res)
        # 2 on a refusal, 0 otherwise - including on a run that wrote
        # nothing because every entry already had a record, which is the
        # ordinary second run and not a failure.
        return 2 if res["errors"] else 0

    if args.cmd == "disputed":
        res = disputed(args.id, args.limit, args.since,
                       _resolve_sources(args.source, ["openalex",
                                                      "semanticscholar"]))
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_disputed(res)
        # 0 on a hit as well as on none. This command finds READING, and
        # exiting non-zero on "there is a paper you should look at" would put
        # it in the same class as a failed check - which is exactly the
        # reading a signal must not be given (user-asks 4.3). 2 only when no
        # source answered, which is bad input to the caller, not a finding.
        return 2 if (not res["checked"] and res["failures"]) else 0

    if args.cmd == "source-types":
        rows = classify_refs(load_refs(args.path))
        by_class: dict[str, int] = {}
        for r in rows:
            by_class[r["class"]] = by_class.get(r["class"], 0) + 1
        not_peer = [r for r in rows if r["peer_reviewed"] is False]
        payload = {"total": len(rows), "by_class": by_class,
                   "non_peer_reviewed": len(not_peer), "references": rows}
        if as_json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(f"{len(rows)} references, classified offline:")
            for k, v in sorted(by_class.items(), key=lambda kv: (-kv[1], kv[0])):
                print(f"  {v:>4}  {k}")
            if not_peer:
                print(f"\n{len(not_peer)} of them have not been peer "
                      f"reviewed:")
                for r in not_peer:
                    print(f"  {r['key'] or '(no key)':<24} {r['class']:<16} "
                          f"{r['why']}")
                print("\nThat is a statement about what they are, not a "
                      "reason to remove them.")
        # 0 always. What kind of source something is has no wrong answer to
        # exit on: a database record in a methods section is correct.
        return 0

    if args.cmd == "check-refs":
        refs = load_refs(args.path)
        order = _resolve_sources(args.source, CASCADE)
        results = []
        for ref in refs:
            kind = classify_source(ref)
            if not kind["verify"]:
                # It does not reach the network, and that is the whole fix.
                # Four indexes missing a UniProt record is not evidence about
                # the record - it is the indexes being asked a question they
                # do not answer - and `not_found` is the word this engine
                # reserves for invented citations (user-asks 1.1).
                results.append({
                    # The citekey travels with the result. Without it a
                    # finding can only name a title, and a title is what the
                    # user has to search the .bib for by hand (item 105).
                    "key": ref.get("key", ""),
                    "claim": {"title": ref.get("title", ""),
                              "doi": ref.get("doi", ""),
                              "pmid": str(ref.get("pmid", "") or ""),
                              "authors": ref.get("authors", []),
                              "year": str(ref.get("year", "") or "")},
                    "status": "not_a_paper", "confidence": 0.0, "route": "",
                    "source": "", "sources_tried": [], "unreachable": [],
                    "match": None, "candidates": [],
                    "source_class": kind,
                    "flags": [
                        f"NOT A PEER-REVIEWED PAPER - {kind['why']}. It was "
                        f"not sent to any index, because no literature index "
                        f"answers questions about it. Citing it may be "
                        f"exactly what you meant; the engine only says what "
                        f"it is."],
                    "notes": [f"classified from the `{kind['evidence']}` "
                              f"field of the bibliography entry"
                              if kind["evidence"] else
                              "classified from the bibliography entry"],
                })
                continue
            res = verify(title=ref.get("title", ""), doi=ref.get("doi", ""),
                         pmid=str(ref.get("pmid", "") or ""),
                         authors=ref.get("authors", []),
                         year=str(ref.get("year", "") or ""), sources=order)
            res["source_class"] = kind
            res["key"] = ref.get("key", "")
            _review_source_note(res)
            if kind["peer_reviewed"] is False and res["status"] == "verified":
                res["flags"].append(
                    f"NOT PEER REVIEWED - {kind['why']}. It is real and it "
                    f"verified; it has not been through peer review.")
            results.append(res)
        summary = {
            "total": len(results),
            # citation-integrity 4. An entry carrying only NOTES stops being
            # counted clean and is counted with the flagged ones: the key's
            # name stops being exact and its meaning becomes right. A
            # forty-entry list is read through this summary, and a note that
            # only ever appears inside one of forty printed records is a note
            # nobody reads.
            "clean": sum(1 for r in results
                         if r["status"] == "verified" and not r["flags"]
                         and not _integrity_notes(r)),
            "verified_with_flags": sum(1 for r in results
                                       if r["status"] == "verified"
                                       and (r["flags"] or _integrity_notes(r))),
            "byline_differences": sum(
                1 for r in results
                if (r.get("integrity") or {}).get("byline_differences")),
            "year_notes": sum(
                1 for r in results
                if (r.get("integrity") or {}).get("year_notes")),
            "partial": sum(1 for r in results if r["status"] == "partial"),
            "not_found": sum(1 for r in results if r["status"] == "not_found"),
            # Counted separately from `not_found` on purpose: one of them
            # means "four indexes do not have this" and the other means "this
            # was never a question for an index".
            "not_a_paper": sum(1 for r in results
                               if r["status"] == "not_a_paper"),
            "non_peer_reviewed": sum(
                1 for r in results
                if (r.get("source_class") or {}).get("peer_reviewed") is False),
            # Item 105, and the three-valued flag is the whole of the care
            # here. `False` is the record SAYING it was not peer reviewed;
            # `None` is the entry not saying, which is the common case for a
            # bare @misc and is not evidence of anything. Only the first can
            # block. Collapsing them would put a confident negative on
            # entries the user never described - the same mistake as reading
            # a database record as `not_found`.
            "policy": "peer_reviewed_only" if args.peer_reviewed_only
                      else "any",
            "blocking": [
                {"key": (r.get("key")
                         or (r.get("claim") or {}).get("title", "")[:60]
                         or "(no citekey)"),
                 "class": (r.get("source_class") or {}).get("class",
                                                            "unknown"),
                 "why": (r.get("source_class") or {}).get("why", "")}
                for r in results
                if args.peer_reviewed_only
                and (r.get("source_class") or {}).get("peer_reviewed") is False
            ],
            "peer_review_unknown": [
                {"key": (r.get("key")
                         or (r.get("claim") or {}).get("title", "")[:60]
                         or "(no citekey)"),
                 "class": (r.get("source_class") or {}).get("class",
                                                            "unknown")}
                for r in results
                if args.peer_reviewed_only
                and (r.get("source_class") or {}).get("peer_reviewed") is None
            ],
            "by_class": {},
            "retracted": sum(1 for r in results
                             if (r.get("match") or {}).get("is_retracted")),
            "incomplete": sum(1 for r in results if r["unreachable"]),
            "by_source": {},
        }
        for r in results:
            kind = (r.get("source_class") or {}).get("class", "unknown")
            summary["by_class"][kind] = summary["by_class"].get(kind, 0) + 1
        for r in results:
            if r["status"] == "verified":
                summary["by_source"][r["source"]] = summary["by_source"].get(r["source"], 0) + 1
        if as_json:
            print(json.dumps({"summary": summary, "results": results},
                             indent=2, ensure_ascii=False))
        else:
            for res in results:
                _print_verify(res)
                print()
            print(f"{summary['total']} references: {summary['clean']} clean, "
                  f"{summary['verified_with_flags']} verified with flags, "
                  f"{summary['partial']} partial, {summary['not_found']} not found, "
                  f"{summary['not_a_paper']} not a paper, "
                  f"{summary['retracted']} retracted.")
            if summary["byline_differences"] or summary["year_notes"]:
                print(f"!  {summary['byline_differences']} reference(s) whose "
                      f"byline differs from the record, "
                      f"{summary['year_notes']} whose year is one out. "
                      f"Each one is a note on its own entry above; none of "
                      f"them is a refusal, and none of them was corrected "
                      f"for you.")
            if summary["non_peer_reviewed"]:
                print(f"!  {summary['non_peer_reviewed']} reference(s) have "
                      f"not been peer reviewed: " + ", ".join(
                          f"{k} {v}" for k, v in sorted(
                              summary["by_class"].items())
                          if k not in ("journal-article", "unknown")) +
                      ".\n   That may be exactly what you meant - a methods "
                      "section citing a PDB entry is doing its job.")
            if summary["blocking"]:
                print(f"\nNOT SENDABLE: this project's reference list is "
                      f"declared peer-reviewed only, and "
                      f"{len(summary['blocking'])} entr"
                      f"{'y is' if len(summary['blocking']) == 1 else 'ies are'}"
                      f" not:")
                for b in summary["blocking"]:
                    print(f"   {b['key']}  -  {b['class']}: {b['why']}")
                print("   Dropping one moves every number the entry supplied. "
                      "Say so before swapping it.")
            if summary["peer_review_unknown"]:
                print(f"\n?  {len(summary['peer_review_unknown'])} entr"
                      f"{'y' if len(summary['peer_review_unknown']) == 1 else 'ies'}"
                      f" the record does not describe either way: " +
                      ", ".join(b["key"] for b in
                                summary["peer_review_unknown"][:8]) +
                      ".\n   Not a failure and not counted as one - the entry "
                      "does not say, which is a question for you and not a "
                      "verdict.")
            if summary["by_source"]:
                print("verified by: " + ", ".join(
                    f"{k} {v}" for k, v in sorted(summary["by_source"].items())))
            if summary["incomplete"]:
                print(f"!  {summary['incomplete']} reference(s) had a source fail - "
                      f"those results are incomplete, not verdicts.")
        # A blocking entry exits 1 whether or not --fail-on-problem was
        # asked for. The policy IS the request to fail on this one thing;
        # requiring a second flag to make the first one act would be a
        # setting that reports success for the configuration in which it does
        # nothing.
        if summary["blocking"]:
            return 1
        return 1 if (args.fail_on_problem and summary["total"] != summary["clean"]) else 0

    if args.cmd == "cite":
        order = _resolve_sources(args.source, CASCADE)
        records, notes, missing = [], [], []
        for ident in args.ids:
            rec = None
            for name in order:
                try:
                    rec = SOURCES[name].by_id(ident)
                except SourceUnreachable as exc:
                    notes.append(f"{name} did not answer for {ident}: {exc.reason}")
                    continue
                if rec:
                    break
            if rec:
                records.append(enrich_abbrev(rec))
            else:
                missing.append(ident)
                notes.append(f"{ident}: no record in {', '.join(order)} - nothing written.")
        # ACS and AMA differ in punctuation, not in the metadata they need.
        # pubmed.format_citation owns the styles it owns; asking it for one it
        # does not implement must say so rather than emit a different style
        # under an ACS label.
        style = args.style
        if style == "acs":
            style = "bibtex"
            notes.append("--style acs is written as BibTeX for an ACS .bst; "
                         "pubmed.py has no standalone ACS string formatter.")
        if as_json:
            print(json.dumps([{**r, "citation": format_citation(r, style, notes),
                               "notes": list(notes)} for r in records],
                             indent=2, ensure_ascii=False))
        else:
            for r in records:
                print(format_citation(r, style, notes) + "\n")
            # Said rather than guessed at: an abbreviation this writer could not
            # derive, or an entry with no locator, is a hand edit the user has
            # to know about before the file reaches a portal.
            for n in notes:
                print("  note  " + n, file=sys.stderr)
        return 1 if missing else 0

    if args.cmd in ("related", "cited-by"):
        # Two sources answer these routes, so try both rather than hard-coding
        # one. OpenAlex first because it is the reliable one; Semantic Scholar
        # second because its keyless tier answers intermittently (measured) but
        # covers papers OpenAlex's related_works leaves empty. An empty result
        # from the first is a reason to ask the second, not an answer.
        # `all` is not a narrowing here and never was: these two routes are
        # answered by two indexes, and asking the other four for a related-
        # works list is asking a question they do not have.
        named = [] if "all" in args.source.split(",") else \
            [n for n in _resolve_sources(args.source, []) if n in SOURCES]
        order = named or ["openalex", "semanticscholar"]
        recs, tried, failures = [], [], []
        for name in order:
            tried.append(name)
            try:
                recs = (SOURCES[name].cited_by(args.id, args.limit)
                        if args.cmd == "cited-by"
                        else SOURCES[name].related(args.id, args.limit))
            except SourceUnreachable as exc:
                failures.append({"source": name, "reason": exc.reason})
                continue
            if recs:
                break
        source = tried[-1] if not recs else next(
            n for n in tried if n not in [f["source"] for f in failures])
        if not recs and failures and len(failures) == len(tried):
            print(json.dumps({"error": failures[0]["reason"],
                              "sources_tried": tried, "failures": failures})
                  if as_json else
                  f"no source answered: "
                  + "; ".join(f"{f['source']} ({f['reason']})" for f in failures),
                  file=sys.stderr)
            return 2
        if as_json:
            print(json.dumps({"id": args.id, "source": source,
                              "sources_tried": tried, "failures": failures,
                              "returned": len(recs), "results": recs},
                             indent=2, ensure_ascii=False))
        else:
            _print_records(recs)
            for f in failures:
                print(f"  !  {f['source']} did not answer ({f['reason']})",
                      file=sys.stderr)
        return 0 if recs else 1

    if args.cmd == "references":
        # Semantic Scholar alone offers this. Its keyless tier is intermittent,
        # so a failure here is reported as a failure, never as "this paper cites
        # nothing" - the same rule the whole engine runs on.
        try:
            recs = SOURCES["semanticscholar"].references(args.id, args.limit)
        except SourceUnreachable as exc:
            print(json.dumps({"error": exc.reason, "source": "semanticscholar"})
                  if as_json else
                  f"semanticscholar did not answer: {exc.reason}", file=sys.stderr)
            return 2
        if as_json:
            print(json.dumps({"id": args.id, "source": "semanticscholar",
                              "returned": len(recs), "results": recs},
                             indent=2, ensure_ascii=False))
        else:
            _print_records(recs)
        return 0 if recs else 1

    if args.cmd == "compound":
        try:
            res = compound(args.query)
        except SourceUnreachable as exc:
            print(f"pubchem did not answer: {exc.reason}", file=sys.stderr)
            return 2
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else _print_compound(res)
        return 0 if res["cid"] else 1

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except SourceUnreachable as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
