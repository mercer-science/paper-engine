#!/usr/bin/env python3
"""
pubmed.py - NCBI E-utilities client for literature search and citation verification.

Primary purpose: prove that a citation refers to a real, indexed paper - and flag
retractions, errata, and metadata mismatches - before it goes into a manuscript.

Environment (all optional):
  NCBI_API_KEY   raises the rate limit from 3 to 10 requests/second
  NCBI_EMAIL     contact address sent to NCBI (they ask for it)
  NCBI_TOOL      tool name sent to NCBI (default: paper-writing-aids)

Usage:
  python pubmed.py search "crispr off-target effects" --limit 10 --from 2020
  python pubmed.py fetch 35245144
  python pubmed.py verify --title "Attention is all you need" --year 2017
  python pubmed.py verify --doi 10.1038/nature14539
  python pubmed.py check-refs refs.json --fail-on-problem
  python pubmed.py cite 35245144 --style ama
  python pubmed.py related 35245144 --limit 5
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET

try:
    import requests
except ImportError:
    sys.exit("pubmed.py requires 'requests'. Install with: python -m pip install requests")

# Author names and titles routinely contain non-ASCII characters; the Windows
# console defaults to cp1252 and would mangle or crash on them.
for _stream in (sys.stdout, sys.stderr):
    # reconfigure() belongs to TextIOWrapper, not to every text stream: under a
    # pipe or a capturing harness sys.stdout may have no such method at all.
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except ValueError:  # detached or already-closed stream
            pass

def env_home() -> tuple[str, str]:
    """Where the credentials file is KEPT - (path, source).

    The same order `learn.py rules_file()` uses,
    and for the same reason: **nothing a user supplies may live in a directory
    an update can replace.** This file held the NCBI key beside the script,
    inside the installed tree, so a plugin update destroyed it - silently, the
    tool simply dropping to the anonymous rate limit without saying it used to
    have a key (item 84). Gitignoring it protected it from being published and
    not at all from being overwritten: a directory-source install is a
    filesystem copy, and an update replaces the directory.

    `toolkit` stays last so a source checkout keeps reading the file it always
    read.
    """
    env = os.environ.get("PWA_ENV_FILE")
    if env:
        return env, "env"
    data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if data:
        return os.path.join(data, "credentials.env"), "plugin_data"
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        ".env"), "toolkit"


def stranded_env() -> str | None:
    """A toolkit .env that resolution is no longer pointing at.

    Same guard as `learn.py stranded_registry()`, and the same silent failure:
    a credentials file that exists, is not being read, and says nothing.
    """
    path, source = env_home()
    toolkit = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if source == "toolkit" or os.path.abspath(path) == os.path.abspath(toolkit):
        return None
    if os.path.isfile(toolkit):
        return toolkit
    return None


def _load_env_file() -> None:
    """Read KEY=value lines from the credentials file, then the fallbacks.

    Real environment variables always win, so a shell export or the harness's
    settings.json env block overrides the file. The resolved file is read
    FIRST, so a key kept outside the installable tree beats a stale copy left
    inside one.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (env_home()[0],
                 os.path.join(here, ".env"),
                 os.path.join(os.path.dirname(here), ".env")):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key, val = key.strip(), val.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = val
        except OSError:
            pass


_load_env_file()

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
API_KEY = os.environ.get("NCBI_API_KEY", "").strip()
EMAIL = os.environ.get("NCBI_EMAIL", "").strip()
TOOL = os.environ.get("NCBI_TOOL", "paper-writing-aids").strip()

# NCBI allows 3 req/s anonymously, 10 req/s with an API key.
MIN_INTERVAL = 0.11 if API_KEY else 0.34
_last_call = [0.0]

# Verification thresholds on normalized-title similarity.
STRONG_MATCH = 0.93
WEAK_MATCH = 0.80


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

def _throttle() -> None:
    delta = time.monotonic() - _last_call[0]
    if delta < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - delta)
    _last_call[0] = time.monotonic()


def _request(endpoint: str, params: dict, retries: int = 3) -> requests.Response:
    params = dict(params)
    params["tool"] = TOOL
    if EMAIL:
        params["email"] = EMAIL
    if API_KEY:
        params["api_key"] = API_KEY

    last_err = None
    for attempt in range(retries):
        _throttle()
        try:
            resp = requests.get(f"{BASE}/{endpoint}", params=params, timeout=30)
        except requests.RequestException as exc:
            last_err = exc
            time.sleep(1.5 * (attempt + 1))
            continue
        if resp.status_code == 200:
            return resp
        if resp.status_code in (429, 500, 502, 503, 504):
            last_err = RuntimeError(f"HTTP {resp.status_code} from NCBI")
            time.sleep(1.5 * (attempt + 1))
            continue
        raise RuntimeError(f"NCBI returned HTTP {resp.status_code}: {resp.text[:300]}")
    raise RuntimeError(f"NCBI request to {endpoint} failed after {retries} tries: {last_err}")


def esearch(term: str, limit: int = 20, sort: str = "relevance",
            mindate: str | None = None, maxdate: str | None = None) -> dict:
    params = {"db": "pubmed", "term": term, "retmax": limit,
              "retmode": "json", "sort": sort}
    if mindate or maxdate:
        params["datetype"] = "pdat"
        params["mindate"] = mindate or "1800"
        params["maxdate"] = maxdate or "3000"
    data = _request("esearch.fcgi", params).json().get("esearchresult", {})
    return {
        "count": int(data.get("count", 0)),
        "pmids": data.get("idlist", []),
        "translated_query": data.get("querytranslation", ""),
        "warnings": data.get("warninglist", {}),
    }


def esummary(pmids: list[str]) -> list[dict]:
    """Lightweight metadata for a list of PMIDs (no abstracts)."""
    if not pmids:
        return []
    raw = _request("esummary.fcgi",
                   {"db": "pubmed", "id": ",".join(pmids), "retmode": "json"}).json()
    result = raw.get("result", {})
    out = []
    for pmid in result.get("uids", []):
        rec = result.get(pmid, {})
        ids = {i.get("idtype"): i.get("value") for i in rec.get("articleids", [])}
        pubtypes = rec.get("pubtype") or []
        out.append({
            "pmid": pmid,
            "title": (rec.get("title") or "").rstrip("."),
            "authors": [a.get("name") for a in rec.get("authors", [])
                        if a.get("authtype") == "Author"],
            "journal": rec.get("fulljournalname") or rec.get("source", ""),
            "journal_abbrev": rec.get("source", ""),
            "year": _year_of(rec.get("pubdate") or rec.get("epubdate") or ""),
            "volume": rec.get("volume", ""),
            "issue": rec.get("issue", ""),
            "pages": rec.get("pages", ""),
            "elocation": rec.get("elocationid", ""),
            "doi": ids.get("doi", ""),
            "pmc": ids.get("pmc", ""),
            "pubtypes": pubtypes,
            "is_retracted": any("retracted publication" in p.lower() for p in pubtypes),
            "notices": [],
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        })
    return out


def efetch(pmids: list[str]) -> list[dict]:
    """Full records: adds abstract, MeSH terms, and retraction/erratum notices."""
    if not pmids:
        return []
    xml = _request("efetch.fcgi",
                   {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"}).text
    root = ET.fromstring(xml)
    return [_parse_article(a) for a in root.findall(".//PubmedArticle")]


def elink_related(pmid: str, limit: int = 10) -> list[str]:
    raw = _request("elink.fcgi", {"dbfrom": "pubmed", "db": "pubmed", "id": pmid,
                                  "cmd": "neighbor", "retmode": "json"}).json()
    pmids: list[str] = []
    for linkset in raw.get("linksets", []):
        for db in linkset.get("linksetdbs", []):
            if db.get("linkname") == "pubmed_pubmed":
                pmids = [p for p in db.get("links", []) if p != pmid]
    return pmids[:limit]


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------

def _text(elem) -> str:
    if elem is None:
        return ""
    return re.sub(r"\s+", " ", "".join(elem.itertext())).strip()


def _parse_article(node) -> dict:
    """Parse one <PubmedArticle>.

    Every path here is anchored to its real parent rather than using './/'.
    A PubmedArticle embeds a <ReferenceList> whose entries carry their own
    <ArticleIdList>, so a descendant search silently returns a *cited* paper's
    DOI instead of this paper's - which would break DOI verification.
    """
    citation = node.find("MedlineCitation")
    art = citation.find("Article") if citation is not None else None
    journal = art.find("Journal") if art is not None else None

    abstract_parts = []
    if art is not None:
        for ab in art.findall("Abstract/AbstractText"):
            label, body = ab.get("Label"), _text(ab)
            if body:
                abstract_parts.append(f"{label}: {body}" if label else body)

    authors = []
    if art is not None:
        for a in art.findall("AuthorList/Author"):
            last, initials = _text(a.find("LastName")), _text(a.find("Initials"))
            collective = _text(a.find("CollectiveName"))
            if last:
                authors.append(f"{last} {initials}".strip())
            elif collective:
                authors.append(collective)

    ids = {i.get("IdType"): _text(i)
           for i in node.findall("PubmedData/ArticleIdList/ArticleId")}
    pubtypes = ([_text(p) for p in art.findall("PublicationTypeList/PublicationType")]
                if art is not None else [])

    notices = []
    if citation is not None:
        for cc in citation.findall("CommentsCorrectionsList/CommentsCorrections"):
            ref_type = cc.get("RefType", "")
            if ref_type in ("RetractionIn", "ErratumIn", "ExpressionOfConcernIn",
                            "RetractionOf", "CorrectedAndRepublishedIn"):
                notices.append({"type": ref_type,
                                "ref": _text(cc.find("RefSource")),
                                "pmid": _text(cc.find("PMID"))})

    year = ""
    if journal is not None:
        year = (_text(journal.find("JournalIssue/PubDate/Year")) or
                _year_of(_text(journal.find("JournalIssue/PubDate/MedlineDate"))))

    pmid = _text(citation.find("PMID")) if citation is not None else ids.get("pubmed", "")

    return {
        "pmid": pmid,
        "title": _text(art.find("ArticleTitle")).rstrip(".") if art is not None else "",
        "authors": authors,
        "journal": _text(journal.find("Title")) if journal is not None else "",
        "journal_abbrev": _text(journal.find("ISOAbbreviation")) if journal is not None else "",
        "year": year,
        "volume": _text(journal.find("JournalIssue/Volume")) if journal is not None else "",
        "issue": _text(journal.find("JournalIssue/Issue")) if journal is not None else "",
        "pages": _text(art.find("Pagination/MedlinePgn")) if art is not None else "",
        # A journal that numbers articles rather than pages has no MedlinePgn
        # at all, and an entry with neither is one ACS's checklist bounces.
        "elocation": (_text(art.find("ELocationID")) if art is not None
                      else ""),
        "doi": ids.get("doi", ""),
        "pmc": ids.get("pmc", ""),
        "abstract": " ".join(abstract_parts),
        "mesh": ([_text(m) for m in
                  citation.findall("MeshHeadingList/MeshHeading/DescriptorName")]
                 if citation is not None else []),
        "pubtypes": pubtypes,
        "is_retracted": any("retracted publication" in p.lower() for p in pubtypes),
        "notices": notices,
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    }


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

def _year_of(s: str) -> str:
    m = re.search(r"\b(1[6-9]\d{2}|20\d{2})\b", s or "")
    return m.group(1) if m else ""


def normalize_title(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def title_similarity(a: str, b: str) -> float:
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    seq = difflib.SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    jaccard = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    return max(seq, (seq + jaccard) / 2)


def clean_doi(doi: str) -> str:
    doi = (doi or "").strip()
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.I)
    return doi.rstrip(" .").lower()


_STOPWORDS = {
    "the", "of", "a", "an", "and", "or", "in", "on", "for", "with", "to", "from",
    "by", "via", "using", "into", "its", "their", "our", "new", "novel", "study",
    "studies", "analysis", "role", "effects", "effect", "evidence", "toward",
    "towards", "between", "among", "during", "after", "before", "following",
    "based", "case", "report", "review", "article", "paper",
}


def _title_terms(title: str, limit: int = 6) -> list[str]:
    """Distinctive content words from a title, for field-restricted retrieval."""
    words = [w for w in normalize_title(title).split()
             if len(w) > 2 and w not in _STOPWORDS]
    seen, out = set(), []
    for w in words:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out[:limit]


def _surnames(authors: list[str]) -> set[str]:
    """Best-effort surname set; handles 'Smith JD', 'Smith, John', 'John Smith'."""
    out = set()
    for a in authors or []:
        norm = normalize_title(a)
        tokens = [t for t in norm.split() if len(t) > 2]
        if tokens:
            out.add(tokens[0])
            out.add(tokens[-1])
    return out


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify(title: str = "", doi: str = "", pmid: str = "",
           authors: list[str] | None = None, year: str = "") -> dict:
    """Decide whether a claimed citation corresponds to a real PubMed record.

    Returns a dict with status: verified | partial | not_found.
    A 'verified' result may still carry flags (retraction, year mismatch, ...).
    """
    claim = {"title": title, "doi": clean_doi(doi), "pmid": str(pmid or "").strip(),
             "authors": authors or [], "year": str(year or "")}
    result = {"claim": claim, "status": "not_found", "confidence": 0.0,
              "route": "", "match": None, "candidates": [], "flags": [], "notes": []}

    records: list[dict] = []

    if claim["pmid"]:
        result["route"] = "pmid"
        try:
            records = efetch([claim["pmid"]])
        except Exception as exc:  # noqa: BLE001 - network/XML issues are informational
            result["notes"].append(f"PMID lookup failed: {exc}")
        if not records:
            result["notes"].append(f"PMID {claim['pmid']} returned no record.")

    if not records and claim["doi"]:
        result["route"] = "doi"
        hits = esearch(f'"{claim["doi"]}"[AID]', limit=5)
        if not hits["pmids"]:
            hits = esearch(f'"{claim["doi"]}"', limit=5)
        if hits["pmids"]:
            records = efetch(hits["pmids"])
        else:
            result["notes"].append("DOI not indexed in PubMed. It may still be a real "
                                   "paper outside PubMed's scope - check doi.org.")

    if not records and title:
        result["route"] = "title"
        # Retrieval ladder, strictest first. The [Title]-restricted term searches
        # matter most: they still find a paper whose title was misremembered or
        # paraphrased, which an exact-phrase search silently misses.
        terms = _title_terms(title)
        attempts = [(f'"{title}"[Title]', 5), (f"{title}[Title]", 10)]
        if len(terms) >= 2:
            attempts.append((" AND ".join(f"{w}[Title]" for w in terms), 15))
            attempts.append((" AND ".join(f"{w}[Title]" for w in terms[:2]), 15))
        attempts.append((title, 10))

        for term, cap in attempts:
            hits = esearch(term, limit=cap)
            if hits["pmids"]:
                records = efetch(hits["pmids"])
                break

    if not records:
        result["notes"].append("No PubMed record matched this citation.")
        result["notes"].append("PubMed covers biomedical and life-sciences literature "
                               "only - a paper outside that scope will not be found here.")
        return result

    # Score every candidate against the claim.
    scored = []
    for rec in records:
        sim = title_similarity(title, rec["title"]) if title else 0.0
        id_hit = ((claim["doi"] and clean_doi(rec["doi"]) == claim["doi"]) or
                  (claim["pmid"] and claim["pmid"] == rec["pmid"]))
        scored.append((bool(id_hit), sim, rec))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)

    id_hit, sim, best = scored[0]
    result["candidates"] = [{"pmid": r["pmid"], "title": r["title"], "year": r["year"],
                             "similarity": round(s, 3), "identifier_match": h}
                            for h, s, r in scored[:5]]

    if id_hit:
        result["status"] = "verified"
        result["confidence"] = 1.0
    elif sim >= STRONG_MATCH:
        result["status"] = "verified"
        result["confidence"] = round(sim, 3)
    elif sim >= WEAK_MATCH:
        result["status"] = "partial"
        result["confidence"] = round(sim, 3)
        result["flags"].append("Title is close but not an exact match - check the wording.")
    else:
        result["confidence"] = round(sim, 3)
        result["notes"].append(f"Best candidate scored only {sim:.2f} on title similarity.")
        return result

    result["match"] = best

    # Cross-checks against the matched record.
    claim_year = _year_of(claim["year"])
    if claim_year and best["year"] and abs(int(claim_year) - int(best["year"])) > 1:
        result["flags"].append(
            f"Year mismatch: cited {claim_year}, PubMed says {best['year']}.")
    if claim["doi"] and best["doi"] and clean_doi(best["doi"]) != claim["doi"]:
        result["flags"].append(
            f"DOI mismatch: cited {claim['doi']}, PubMed says {best['doi']}.")
    if claim["pmid"] and claim["pmid"] != best["pmid"]:
        result["flags"].append(
            f"PMID mismatch: cited {claim['pmid']}, this paper is PMID {best['pmid']}.")
    if claim["authors"]:
        claimed, actual = _surnames(claim["authors"]), _surnames(best["authors"])
        if claimed and actual and not (claimed & actual):
            result["flags"].append("No cited author surname appears on the PubMed record.")
    if best.get("is_retracted"):
        result["flags"].append("RETRACTED PUBLICATION - do not cite as valid evidence.")
    for n in best.get("notices", []):
        if n["type"] == "RetractionIn":
            result["flags"].append(f"Retraction notice exists: {n['ref']}")
        elif n["type"] == "ExpressionOfConcernIn":
            result["flags"].append(f"Expression of concern: {n['ref']}")
        elif n["type"] in ("ErratumIn", "CorrectedAndRepublishedIn"):
            result["flags"].append(f"Erratum/correction published: {n['ref']}")

    return result


# ---------------------------------------------------------------------------
# Reference-list loading
# ---------------------------------------------------------------------------

def load_refs(path: str) -> list[dict]:
    """Accept a JSON array, a .bib file, or plain text with one title per line."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()

    stripped = raw.lstrip()
    if stripped.startswith("[") or stripped.startswith("{"):
        data = json.loads(raw)
        if isinstance(data, dict):
            data = data.get("references") or data.get("refs") or []
        return [r if isinstance(r, dict) else {"title": str(r)} for r in data]

    if re.search(r"@\w+\s*\{", raw):
        return _parse_bibtex(raw)

    return [{"title": line.strip()} for line in raw.splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


# Everything past the first five is carried for `scholar.classify_source`,
# which answers a question this parser used to make unanswerable: WHAT KIND OF
# THING is this entry. A UniProt record and a journal article differ in the
# entry type, the URL and the `howpublished` line and in nothing else the old
# five fields could see - so the classifier could not have been written above
# a parser that had already thrown its evidence away (user-asks 1.3).
#
# The original five keep their names and their meanings. Every existing
# consumer reads those and is untouched by the additions.
BIB_EXTRA_FIELDS = ("url", "howpublished", "publisher", "journal",
                    "booktitle", "note", "eprint", "archiveprefix",
                    "institution", "school", "organization", "series",
                    "urldate", "version")


def _parse_bibtex(raw: str) -> list[dict]:
    entries = []
    for block in re.split(r"(?=@\w+\s*\{)", raw):
        if not re.match(r"@\w+\s*\{", block.strip()):
            continue
        head = re.match(r"@(\w+)\s*\{\s*([^,\s}]*)", block.strip())
        fields = {}
        for name in ("title", "author", "year", "doi", "pmid",
                     *BIB_EXTRA_FIELDS):
            m = re.search(rf"\b{name}\s*=\s*", block, re.I)
            if m:
                fields[name] = _extract_bib_value(block, m.end())
        if fields.get("title"):
            entry = {
                "title": re.sub(r"[{}]", "", fields["title"]),
                "authors": [a.strip() for a in
                            re.split(r"\s+and\s+", fields.get("author", "")) if a.strip()],
                "year": _year_of(fields.get("year", "")),
                "doi": fields.get("doi", ""),
                "pmid": fields.get("pmid", ""),
                "entry_type": (head.group(1).lower() if head else ""),
                "key": (head.group(2) if head else ""),
            }
            for name in BIB_EXTRA_FIELDS:
                entry[name] = re.sub(r"[{}]", "", fields.get(name, ""))
            entries.append(entry)
    return entries


def _extract_bib_value(block: str, start: int) -> str:
    while start < len(block) and block[start].isspace():
        start += 1
    if start >= len(block):
        return ""
    if block[start] == "{":
        depth = 0
        for i in range(start, len(block)):
            if block[i] == "{":
                depth += 1
            elif block[i] == "}":
                depth -= 1
                if depth == 0:
                    return re.sub(r"\s+", " ", block[start + 1:i]).strip()
        return ""
    if block[start] == '"':
        end = block.find('"', start + 1)
        return block[start + 1:end].strip() if end > 0 else ""
    m = re.search(r"[,\n}]", block[start:])
    return block[start:start + m.start()].strip() if m else block[start:].strip()


# ---------------------------------------------------------------------------
# Citation formatting
# ---------------------------------------------------------------------------

# --- CASSI journal abbreviations -------------------------------------------
#
# Langmuir's author checklist asks for "CAS Source Index (CASSI) journal
# abbreviations, proper punctuation and arrangement". PubMed offers two forms
# and neither is CASSI: the full title in sentence case (`Chemical reviews`),
# and an ISOAbbreviation with the periods stripped (`Chem Rev`). The .bib
# writer took the first, so 15 of 17 entries in a real project were in a form
# the journal does not accept and every one was corrected by hand afterwards
# - which is the tell (item 12).
#
# The rule: a word of the ISO abbreviation that does NOT appear whole in the
# full title has been abbreviated, and takes a period. One that does appear
# whole - `Langmuir`, `Science`, the `C` of `J. Phys. Chem. C`, the `ACS` of
# `ACS Nano` - is not an abbreviation and takes none. That gets Chem. Rev.,
# Chem. Soc. Rev., J. Am. Chem. Soc., Adv. Mater. and Nat. Commun. right.
#
# Where it is wrong it is wrong about the arrangement rather than the periods,
# and no rule recovers that, so those are a table. Adding to it is the right
# move when a new one turns up; guessing at one is not.

CASSI_OVERRIDES = {
    "angew chem int ed engl": "Angew. Chem., Int. Ed.",
    "angew chem int ed": "Angew. Chem., Int. Ed.",
    "angew chem weinheim bergstr ger": "Angew. Chem.",
    "proc natl acad sci u s a": "Proc. Natl. Acad. Sci. U.S.A.",
    "chemistry": "Chem. - Eur. J.",
    "chem eur j": "Chem. - Eur. J.",
    "j am chem soc": "J. Am. Chem. Soc.",
    "acs appl mater interfaces": "ACS Appl. Mater. Interfaces",
}


def _cassi_key(s: str) -> str:
    return " ".join(re.sub(r"[.,\-]", " ", (s or "").lower()).split())


def cassi_journal(rec: dict) -> tuple[str, str]:
    """The journal name in CASSI form, and what could not be settled.

    Returns the name to write and a note - "" when there is nothing to say.
    Reporting rather than guessing is the rule everywhere else in this
    toolkit and it applies hardest here, because a wrong abbreviation looks
    exactly like a right one.
    """
    abbrev = (rec.get("journal_abbrev") or "").strip()
    # PubMed disambiguates a title with a place - `Chem Commun (Camb)`,
    # `Nanoscale (Lond)`. CASSI does not carry it, and it is not part of the
    # journal's name.
    abbrev = re.sub(r"\s*\([^)]*\)\s*$", "", abbrev).strip()
    full = (rec.get("journal") or "").strip()
    key = _cassi_key(abbrev)
    if key in CASSI_OVERRIDES:
        return CASSI_OVERRIDES[key], ""
    if not abbrev:
        if len(full.split()) <= 1:
            return full, ""
        return full, ("%s: PubMed returned no ISOAbbreviation, so the CASSI "
                      "form cannot be derived and the full title is written "
                      "instead. Replace it by hand before submission."
                      % (rec.get("pmid") or full))
    title_words = {w.strip(".,;:()[]").lower()
                   for w in re.split(r"[\s./]+", full) if w.strip(".,;:()[]")}
    out: list[str] = []
    for word in abbrev.split():
        bare = word.strip(".")
        if bare.lower() in title_words or not bare.isalnum():
            out.append(bare)
        else:
            out.append(bare + ".")
    return " ".join(out), ""


def _pages_field(rec: dict) -> tuple[str, str]:
    """The page range, or the article number that stands in for it.

    A journal that numbers articles rather than pages - `Adv Mater 2017, 29
    (30)` - reached the reference list with no pages at all, against the same
    checklist line as the abbreviation. The e-locator IS the locator in that
    case, so it is written; an entry with neither is reported rather than
    written incomplete and left to an editor to notice.
    """
    pages = _full_page_range(rec.get("pages", ""))
    if pages:
        return pages, ""
    eloc = (rec.get("elocation") or "").strip()
    eloc = re.sub(r"^\s*(doi|pii)\s*:\s*", "", eloc, flags=re.IGNORECASE)
    if eloc and not eloc.lower().startswith("10."):
        return eloc, ""
    return "", ("%s has no page range and no article number, so its entry "
                "carries neither. Journals that screen on 'proper punctuation "
                "and arrangement' bounce that; add the locator by hand."
                % (rec.get("pmid") or rec.get("title", "")[:40]))


def format_citation(rec: dict, style: str = "ama",
                    notes: list[str] | None = None) -> str:
    authors = rec.get("authors", [])
    year = rec.get("year") or "n.d."
    title = rec.get("title", "")
    journal = rec.get("journal_abbrev") or rec.get("journal", "")
    vol, issue, pages = rec.get("volume", ""), rec.get("issue", ""), rec.get("pages", "")
    doi = rec.get("doi", "")

    if style == "ama":
        names = ", ".join(authors[:6]) + (", et al" if len(authors) > 6 else "")
        loc = vol + (f"({issue})" if issue else "") + (f":{pages}" if pages else "")
        out = f"{names}. {title}. {journal}. {year}"
        out += f";{loc}." if loc else "."
        return out + (f" doi:{doi}" if doi else "")

    if style == "apa":
        names = ", ".join(_apa_name(a) for a in authors[:20])
        if len(authors) > 20:
            names += ", ... " + _apa_name(authors[-1])
        out = f"{names} ({year}). {title}. {rec.get('journal', journal)}"
        if vol:
            out += f", {vol}"
            if issue:
                out += f"({issue})"
        if pages:
            out += f", {pages}"
        return out + "." + (f" https://doi.org/{doi}" if doi else "")

    if style == "bibtex":
        first = normalize_title(authors[0]).split()[0] if authors else "anon"
        word = (normalize_title(title).split() or ["ref"])[0]
        names = " and ".join(_bibtex_name(a) for a in authors if a.strip())
        cassi, jnote = cassi_journal(rec)
        locator, pnote = _pages_field(rec)
        for n in (jnote, pnote):
            if n and notes is not None:
                notes.append(n)
        lines = [f"  title = {{{_tex_escape(title)}}}",
                 f"  author = {{{names}}}",
                 f"  journal = {{{_tex_escape(cassi)}}}",
                 f"  year = {{{year}}}"]
        for key, val in (("volume", vol), ("number", issue),
                         ("pages", locator),
                         ("doi", doi), ("pmid", rec.get("pmid", ""))):
            if val:
                lines.append(f"  {key} = {{{val}}}")
        return "@article{" + f"{first}{year}{word}" + ",\n" + ",\n".join(lines) + "\n}"

    raise ValueError(f"Unknown citation style: {style}")


# ---------------------------------------------------------------------------
# BibTeX
# ---------------------------------------------------------------------------
#
# Three things went wrong writing a real .bib, and every one of them was
# silent.
#
# PubMed returns an author as `Stock N`. Written into a .bib unchanged,
# BibTeX reads that as given-name "Stock", surname "N": the build rendered
# `(N and S 2012)` in the body text and `N, Stock, and Biswas S` in the
# reference list, and nothing said so at any point. So the writer emits the
# shape BibTeX actually parses - `Stock, N.` - and BRACES any name that does
# not match `Surname Initials` rather than guessing where it splits.
#
# Non-ASCII was emitted raw, which on a Windows toolchain is a live
# corruption risk. TeX escapes travel.
#
# PubMed abbreviates a closing page number - `933-69` for 933-969 - and ACS
# asks for the full range.

# A combining mark, and the TeX accent command that writes it.
_TEX_ACCENT = {
    0x0300: "`", 0x0301: "'", 0x0302: "^", 0x0303: "~", 0x0304: "=",
    0x0306: "u", 0x0307: ".", 0x0308: '"', 0x030A: "r", 0x030B: "H",
    0x030C: "v", 0x0323: "d", 0x0327: "c", 0x0328: "k", 0x0331: "b",
}

# Characters with no base letter to accent, so they are named outright.
# Every entry here was measured through real pandoc and comes back as the
# character it stands for. Four that did not are deliberately absent:
# `{\dh}`, `{\th}`, `{\dj}` and `{\DJ}` render as NOTHING - the letter is
# dropped out of the reference list - and so d-with-stroke and thorn take the
# raw-character fallback in _tex_accented() instead. `$^\circ$` came out as
# `^(∘)`, which is why the degree sign is `\textdegree` and not math.
_TEX_LITERAL = {
    "\u00df": r"{\ss}", "\u00e6": r"{\ae}", "\u00c6": r"{\AE}",
    "\u0153": r"{\oe}", "\u0152": r"{\OE}", "\u00f8": r"{\o}",
    "\u00d8": r"{\O}", "\u00e5": r"{\aa}", "\u00c5": r"{\AA}",
    "\u0142": r"{\l}", "\u0141": r"{\L}", "\u0131": r"{\i}",
    "\u00b0": r"\textdegree{}",
    "\u00b7": r"$\cdot$", "\u00d7": r"$\times$", "\u00b1": r"$\pm$",
    "\u2264": r"$\leq$", "\u2265": r"$\geq$", "\u2192": r"$\rightarrow$",
    "\u2248": r"$\sim$",
    "\u03b1": r"$\alpha$", "\u03b2": r"$\beta$", "\u03b3": r"$\gamma$",
    "\u03b4": r"$\delta$", "\u03ba": r"$\kappa$", "\u03bb": r"$\lambda$",
    "\u03bc": r"$\mu$", "\u03c0": r"$\pi$", "\u03c3": r"$\sigma$",
    "\u2013": "--", "\u2014": "---", "\u2018": "`", "\u2019": "'",
    "\u201c": "``", "\u201d": "''", "\u2032": "'", "\u2212": "-",
    "\u00a0": " ",
}


def _tex_accented(ch: str) -> str:
    """One non-ASCII character as a TeX accent command, or as itself.

    A character with no combining form and no name of its own falls back to
    its ASCII skeleton, and one with no skeleton either is kept as it is: a
    dropped letter in an author's name is worse than a wide byte, and that is
    the single case this writer leaves raw.
    """
    d = unicodedata.normalize("NFD", ch)
    if len(d) == 2 and ord(d[1]) in _TEX_ACCENT:
        base, accent = d[0], _TEX_ACCENT[ord(d[1])]
        if base in "ij":
            base = "\\" + base       # the dot goes with the accent, not under it
        return "{\\" + accent + (" " if accent.isalpha() else "") + base + "}"
    skeleton = "".join(c for c in unicodedata.normalize("NFKD", ch)
                       if ord(c) < 128 and not unicodedata.combining(c))
    return skeleton or ch


def _tex_escape(text: str) -> str:
    """A field written so the .bib is ASCII and still says the same thing."""
    out: list[str] = []
    for ch in unicodedata.normalize("NFC", text or ""):
        if ch in _TEX_LITERAL:
            out.append(_TEX_LITERAL[ch])
        elif ord(ch) < 128:
            out.append(ch)
        else:
            out.append(_tex_accented(ch))
    return "".join(out)


def _bibtex_name(name: str) -> str:
    """`Stock N` -> `Stock, N.`; anything else braced as one literal name."""
    n = " ".join((name or "").split())
    if not n:
        return ""
    parts = n.split()
    tail = parts[-1]
    if len(parts) >= 2 and tail.isalpha() and tail.isupper() and len(tail) <= 4:
        return "%s, %s" % (_tex_escape(" ".join(parts[:-1])),
                           " ".join(c + "." for c in tail))
    # A collective name, a surname already spelled out, anything unexpected:
    # the braces tell BibTeX "this is one name, do not look for the comma".
    return "{" + _tex_escape(n) + "}"


def _full_page_range(pages: str) -> str:
    """`933-69` -> `933--969`: the range PubMed abbreviated, written out."""
    p = (pages or "").strip()
    m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", p)
    if not m:
        return re.sub(r"\s*-\s*", "--", p)
    start, end = m.group(1), m.group(2)
    if len(end) < len(start):
        end = start[:len(start) - len(end)] + end
    return "%s--%s" % (start, end)


def _apa_name(name: str) -> str:
    """'Smith JD' -> 'Smith, J. D.'"""
    parts = name.split()
    if len(parts) < 2:
        return name
    last, initials = " ".join(parts[:-1]), parts[-1]
    if initials.isupper() and len(initials) <= 4:
        return f"{last}, " + " ".join(f"{c}." for c in initials)
    return f"{last}, {initials}"


# ---------------------------------------------------------------------------
# Full text (PMC) - tiered reading
# ---------------------------------------------------------------------------

# Tiers 2 and 3 need a PMC record, which exists only for the open-access subset
# and the author-manuscript collection. For anything else the abstract is all
# there is, and `sections` must say so rather than raise - a caller that got an
# empty result silently could go on to claim it read a discussion it never saw.

DEFAULT_SECTIONS = ["discussion", "conclusions"]

# Canonical name -> the titles journals actually use for it. Matching is on
# word sets, so "STAR Methods" and "Materials and Methods" both reach "methods".
SECTION_ALIASES = {
    "abstract": ["abstract"],
    "introduction": ["introduction", "background"],
    "methods": ["methods", "method", "methodology", "materials and methods",
                "experimental", "experimental section", "experimental procedures",
                "star methods", "patients and methods", "subjects and methods"],
    "results": ["results", "findings"],
    "discussion": ["discussion"],
    "conclusions": ["conclusion", "conclusions", "concluding remarks", "summary"],
}

# Non-prose blocks. Table and figure bodies read as noise inside a section and
# inflate exactly the token cost the tiering exists to control.
_SKIP_TAGS = {"table-wrap", "table", "fig", "fig-group", "graphic",
              "inline-graphic", "supplementary-material", "media",
              "disp-formula", "array", "table-wrap-foot"}


def _alias_match(title: str, wanted: list[str]) -> list[str]:
    """Which requested canonical names this <title> satisfies.

    A section titled "Results and Discussion" legitimately answers both, so this
    returns a list rather than a first hit.
    """
    words = set(normalize_title(title).split())
    if not words:
        return []
    hits = []
    for canon in wanted:
        for alias in SECTION_ALIASES.get(canon, [canon]):
            if set(normalize_title(alias).split()) <= words:
                hits.append(canon)
                break
    return hits


def _sec_text(node) -> str:
    """Prose of one <sec>, subsections included, non-prose blocks dropped."""
    chunks: list[str] = []

    def walk(elem) -> None:
        for child in elem:
            tag = child.tag.lower()
            if tag in _SKIP_TAGS:
                continue
            if tag in ("p", "title"):
                txt = _text(child)
                if txt:
                    chunks.append(txt)
            else:
                walk(child)

    walk(node)
    return "\n\n".join(chunks)


def _collect_sections(body, wanted: list[str]) -> tuple[dict, list[str]]:
    """Walk <body>'s section tree for the requested sections.

    Anchored to <body> and using findall("sec") at each level - never a './/'
    descendant search. A PMC article carries a <ref-list> under <back> whose
    entries have their own titles and text; a descendant search would pull a
    *cited* paper's words in as this paper's discussion. Same bug shape as the
    one already recorded in _parse_article.
    """
    found: dict[str, list[str]] = {}
    available: list[str] = []

    def visit(parent, depth: int = 0) -> None:
        for sec in parent.findall("sec"):
            title = _text(sec.find("title"))
            if depth == 0:
                available.append(title or "(untitled)")
            hits = _alias_match(title, wanted)
            if hits:
                text = _sec_text(sec)
                for canon in hits:
                    found.setdefault(canon, []).append(text)
                continue  # its subsections are already inside `text`
            visit(sec, depth + 1)

    visit(body)
    return {k: "\n\n".join(v) for k, v in found.items()}, available


def _efetch_pmc(pmc_ids: list[str]) -> dict:
    """PMC full-text XML for several PMC ids at once, keyed by PMID."""
    if not pmc_ids:
        return {}
    nums = [re.sub(r"^PMC", "", p, flags=re.I) for p in pmc_ids]
    xml = _request("efetch.fcgi", {"db": "pmc", "id": ",".join(nums),
                                   "rettype": "full", "retmode": "xml"}).text
    out = {}
    for art in ET.fromstring(xml).findall("article"):
        front = art.find("front")
        if front is None:
            continue
        ids = {i.get("pub-id-type"): (i.text or "")
               for i in front.findall("article-meta/article-id")}
        if ids.get("pmid"):
            out[ids["pmid"]] = art
    return out


def fetch_sections(pmids: list[str], wanted: list[str] | None = None,
                   max_chars: int = 20000) -> list[dict]:
    """Tier 2 read: named sections for each PMID that has retrievable full text."""
    wanted = wanted or list(DEFAULT_SECTIONS)
    records = {r["pmid"]: r for r in efetch(pmids)}
    articles = _efetch_pmc([r["pmc"] for r in records.values() if r.get("pmc")])

    out = []
    for pmid in pmids:
        rec = records.get(pmid)
        res = {"pmid": pmid, "pmc": (rec or {}).get("pmc", ""),
               "title": (rec or {}).get("title", ""),
               "sections": {}, "available": [], "truncated": False, "note": ""}
        if rec is None:
            res["note"] = "no PubMed record for this PMID"
            out.append(res)
            continue
        if not rec.get("pmc"):
            res["note"] = "no PMC record; abstract only"
            out.append(res)
            continue
        art = articles.get(pmid)
        body = art.find("body") if art is not None else None
        if body is None:
            res["note"] = "PMC record has no retrievable full text; abstract only"
            out.append(res)
            continue

        sections, available = _collect_sections(body, wanted)
        for name, text in sections.items():
            if len(text) > max_chars:
                sections[name] = text[:max_chars] + "\n\n[...truncated]"
                res["truncated"] = True
        res["sections"], res["available"] = sections, available
        missing = [w for w in wanted if w not in sections]
        if missing:
            res["note"] = "not present under that heading: " + ", ".join(missing)
        out.append(res)
    return out


# ---------------------------------------------------------------------------
# PDF retrieval - open access only
# ---------------------------------------------------------------------------

# PubMed hosts no PDFs. PMC hosts the open-access subset, but NCBI retired
# oa.fcgi and the FTP dataset directories in August 2026, and PMC's own
# /articles/<id>/pdf/ endpoint now answers with a proof-of-work challenge rather
# than a file. The AWS Open Data mirror is the current sanctioned no-login
# route; its keys are per-article and versioned:
#     PMC7102627.1/PMC7102627.1.pdf
# Unpaywall is the fallback, and only ever its best *OA* location. Nothing here
# touches paywalled content.
PMC_OA_BUCKET = "https://pmc-oa-opendata.s3.amazonaws.com"
UNPAYWALL_API = "https://api.unpaywall.org/v2"


def _user_agent() -> str:
    return f"{TOOL}/1.0" + (f" (mailto:{EMAIL})" if EMAIL else "")


def _http_get(url: str, params: dict | None = None, timeout: int = 60,
              retries: int = 3):
    """GET a non-NCBI host (the S3 mirror, Unpaywall, a publisher).

    The eutils throttle deliberately does not apply - it exists for NCBI's rate
    limit and these are different services. Returns None rather than raising, so
    one dead link degrades that PMID to a stub instead of failing the batch.
    """
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout,
                                headers={"User-Agent": _user_agent()})
        except requests.RequestException:
            time.sleep(1.5 * (attempt + 1))
            continue
        if resp.status_code == 200:
            return resp
        if resp.status_code in (429, 500, 502, 503, 504):
            time.sleep(1.5 * (attempt + 1))
            continue
        return None
    return None


def _pmc_oa_pdf_url(pmc: str) -> str:
    """Newest OA-subset PDF URL for a PMC id, or "" if it is not in the subset."""
    num = re.sub(r"^PMC", "", pmc or "", flags=re.I)
    if not num.isdigit():
        return ""
    resp = _http_get(PMC_OA_BUCKET, params={"list-type": "2",
                                            "prefix": f"PMC{num}.",
                                            "max-keys": "200"}, timeout=30)
    if resp is None:
        return ""
    best, best_ver = "", -1
    for key in re.findall(r"<Key>([^<]+)</Key>", resp.text):
        m = re.fullmatch(rf"PMC{num}\.(\d+)/PMC{num}\.\1\.pdf", key)
        if m and int(m.group(1)) > best_ver:
            best, best_ver = key, int(m.group(1))
    return f"{PMC_OA_BUCKET}/{best}" if best else ""


def _unpaywall_pdf_url(doi: str) -> str:
    """A legal OA PDF for this DOI, per Unpaywall's best OA location."""
    if not doi or not EMAIL:
        return ""
    resp = _http_get(f"{UNPAYWALL_API}/{clean_doi(doi)}",
                     params={"email": EMAIL}, timeout=30)
    if resp is None:
        return ""
    try:
        loc = resp.json().get("best_oa_location") or {}
    except ValueError:
        return ""
    return loc.get("url_for_pdf") or ""


def _surname_of(name: str) -> str:
    """'Hoffmann M' -> 'Hoffmann'; collective names collapse to their first word."""
    parts = (name or "").split()
    if not parts:
        return "Anon"
    if len(parts) > 1 and parts[-1].isupper() and len(parts[-1]) <= 4:
        parts = parts[:-1]
    return re.sub(r"[^A-Za-z]", "", "".join(parts[:1])) or "Anon"


def _pdf_stem(rec: dict) -> str:
    """{FirstAuthor}{Year}_{slug} - sortable, recognizable, close to a BibTeX key."""
    authors = rec.get("authors") or []
    surname = _surname_of(authors[0]) if authors else "Anon"
    year = rec.get("year") or "nd"
    slug = "-".join(_title_terms(rec.get("title", ""), limit=5)) or "untitled"
    return f"{surname}{year}_{slug}"


def _write_stub(path: str, rec: dict, reason: str, oa_url: str = "") -> None:
    """A .md placeholder so the folder is never silently incomplete."""
    authors = rec.get("authors") or []
    doi = rec.get("doi", "")
    lines = [
        f"# {rec.get('title', '(no title)')}",
        "",
        f"**Authors:** {', '.join(authors) or '(none listed)'}  ",
        f"**Journal:** {rec.get('journal') or '(unknown)'} {rec.get('year', '')}  ",
        f"**DOI:** {doi or '(none)'}  ",
        f"**PMID:** {rec.get('pmid', '')}  ",
        f"**PubMed:** {rec.get('url', '')}  ",
    ]
    if doi:
        lines.append(f"**Publisher:** https://doi.org/{doi}  ")
    if oa_url:
        lines.append(f"**Open-access PDF:** {oa_url}  ")
    lines += ["", f"> No PDF was retrieved: {reason}.",
              "> This stub stands in its place so the folder is not silently incomplete.",
              "", "## Abstract", "",
              rec.get("abstract") or "(no abstract available)", ""]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def fetch_pdfs(pmids: list[str], out_dir: str) -> list[dict]:
    """Retrieve an OA PDF per PMID, or write a stub. Sequential by design.

    BUILD-LOG.md records that this tool is latency-bound rather than rate-limited,
    so parallelising buys nothing at this scale.
    """
    os.makedirs(out_dir, exist_ok=True)
    records = {r["pmid"]: r for r in efetch(pmids)}

    results = []
    for pmid in pmids:
        rec = records.get(pmid)
        if rec is None:
            results.append({"pmid": pmid, "path": "", "source": "none", "ok": False,
                            "note": "no PubMed record for this PMID", "bibtex": ""})
            continue

        stem = _pdf_stem(rec)
        pdf_path = os.path.join(out_dir, stem + ".pdf")
        source, note, ok, path = "", "", False, ""
        blocked_url = ""

        for candidate, label in ((_pmc_oa_pdf_url(rec.get("pmc", "")), "pmc"),
                                 (_unpaywall_pdf_url(rec.get("doi", "")), "unpaywall")):
            if not candidate:
                continue
            resp = _http_get(candidate)
            # A refusal, a proof-of-work interstitial or a login page all arrive
            # as something that is not a PDF. Only real PDF bytes count as a hit.
            if resp is None or not resp.content.startswith(b"%PDF"):
                # An OA copy demonstrably exists; this host just won't hand it to
                # a script. Saying "not open access" here would be a lie, and the
                # user can open the link themselves.
                blocked_url = blocked_url or candidate
                continue
            with open(pdf_path, "wb") as fh:
                fh.write(resp.content)
            source, path, ok = label, pdf_path, True
            break

        if not ok:
            if blocked_url:
                host = re.sub(r"^https?://(www\.)?([^/]+).*", r"\2", blocked_url)
                note = (f"an open-access copy exists but {host} refused automated "
                        f"download; fetch it by hand: {blocked_url}")
            elif rec.get("pmc"):
                note = "PMC record exists but the article is not in the open-access subset"
            elif rec.get("doi"):
                note = "no open-access copy found for this DOI"
            else:
                note = "no PMC record and no DOI to resolve"
            if not EMAIL and not blocked_url:
                note += "; set NCBI_EMAIL to enable the Unpaywall fallback"
            path = os.path.join(out_dir, stem + ".md")
            _write_stub(path, rec, note, oa_url=blocked_url)
            source = "stub"

        results.append({"pmid": pmid, "path": path, "source": source, "ok": ok,
                        "note": note, "oa_url": blocked_url,
                        "bibtex": format_citation(rec, "bibtex")})

    # The caller gets refs.bib for free rather than building it separately.
    bib_path = os.path.join(out_dir, "refs.bib")
    with open(bib_path, "w", encoding="utf-8") as fh:
        fh.write("\n\n".join(r["bibtex"] for r in results if r["bibtex"]) + "\n")
    return results


# ---------------------------------------------------------------------------
# Setup / credentials
# ---------------------------------------------------------------------------

KEY_URL = "https://account.ncbi.nlm.nih.gov/settings/"
# Resolved, never beside the script: see env_home(). Kept as a module-level
# name because both this engine and its suite refer to "the credentials file",
# and computed at import for the same reason ROOT is.
ENV_PATH = env_home()[0]


def validate_key(key: str) -> tuple[bool, str]:
    """Ask NCBI whether a key is real. Invalid keys come back as HTTP 400/401/403."""
    try:
        resp = requests.get(f"{BASE}/esearch.fcgi",
                            params={"db": "pubmed", "term": "crispr", "retmax": 1,
                                    "retmode": "json", "tool": TOOL, "api_key": key},
                            timeout=30)
    except requests.RequestException as exc:
        return False, f"could not reach NCBI ({exc})"
    if resp.status_code == 200:
        return True, "NCBI accepted the key"
    if resp.status_code in (400, 401, 403):
        return False, "NCBI rejected the key - check for a typo or a stale key"
    return False, f"unexpected response from NCBI (HTTP {resp.status_code})"


def write_env(updates: dict) -> str:
    """Update the credentials file in place, preserving lines already there."""
    lines = []
    os.makedirs(os.path.dirname(os.path.abspath(ENV_PATH)) or ".",
                exist_ok=True)
    if os.path.isfile(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    if not lines:
        lines = ["# NCBI E-utilities credentials. Gitignored - do not commit.",
                 f"# Free API key: {KEY_URL} (API Key Management)"]
    for key, val in updates.items():
        for i, line in enumerate(lines):
            if line.strip().startswith(f"{key}="):
                lines[i] = f"{key}={val}"
                break
        else:
            lines.append(f"{key}={val}")
    with open(ENV_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return ENV_PATH


def run_setup(key: str | None, no_key: bool, email: str) -> int:
    print("PubMed API setup")
    print("-" * 60)
    where, source = env_home()
    print("Credentials file: %s  (%s)" % (where, source))
    if source == "toolkit":
        print("  This is inside the toolkit, which an update replaces. Set "
              "CLAUDE_PLUGIN_DATA")
        print("  (or PWA_ENV_FILE) and the key is kept outside the tree "
              "instead.")
    stranded = stranded_env()
    if stranded:
        # The silent failure this guard exists for: a key that IS on disk, is
        # not being read, and says nothing while every request drops to the
        # anonymous tier.
        print("  A key file also exists at %s and is NOT the one being read."
              % stranded)
        print("  Copy the key across, or point PWA_ENV_FILE at it.")
    print()
    print("The tool works right now with no account and no key: NCBI's E-utilities")
    print("are a public API. A free key only raises the rate limit.")
    print("  without a key:  3 requests/second  (fine for normal drafting)")
    print("  with a key:    10 requests/second  (noticeable on long bibliographies)")
    print()

    if not key and not no_key and sys.stdin.isatty():
        print(f"To get one: sign in at {KEY_URL}")
        print("then open 'API Key Management' and create a key.")
        print()
        key = input("Paste your API key (or press Enter to continue without one): ").strip()

    updates = {}
    if email:
        updates["NCBI_EMAIL"] = email

    if key:
        ok, msg = validate_key(key)
        if not ok:
            print(f"  FAILED: {msg}")
            print("  Nothing was saved. Re-run with a corrected key, or use --no-key.")
            return 2
        print(f"  {msg}.")
        updates["NCBI_API_KEY"] = key
        path = write_env(updates)
        print(f"  Saved to {path} (gitignored).")
        print("  Rate limit is now 10 requests/second.")
        return 0

    # Anonymous mode: confirm the public API is reachable and record the choice.
    try:
        esearch("crispr", limit=1)
    except Exception as exc:  # noqa: BLE001 - setup diagnostics
        print(f"  Could not reach NCBI: {exc}")
        print("  Check your network or proxy, then re-run.")
        return 2
    updates.setdefault("NCBI_API_KEY", "")
    path = write_env(updates)
    print("  Connected to PubMed anonymously - everything works.")
    print(f"  Wrote {path}; add a key there any time to lift the rate limit.")
    return 0


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
        line = (f"    {r.get('journal_abbrev') or r.get('journal', '')} "
                f"{r.get('year', '')} | PMID {r.get('pmid', '')}")
        if r.get("doi"):
            line += f" | doi:{r['doi']}"
        print(line)
        if r.get("is_retracted"):
            print("    ** RETRACTED PUBLICATION **")
        for n in r.get("notices", []):
            print(f"    ** {n['type']}: {n['ref']} **")
        if r.get("abstract"):
            body = r["abstract"]
            print(f"    {body[:400]}{'...' if len(body) > 400 else ''}")
    print()


def _print_sections(results: list[dict]) -> None:
    for r in results:
        print("=" * 78)
        print(f"PMID {r['pmid']}  {r.get('pmc') or '(no PMC)'}")
        if r.get("title"):
            print(f"  {r['title'][:72]}")
        if r["available"]:
            print(f"  sections present: {', '.join(r['available'])}")
        if r["note"]:
            print(f"  note: {r['note']}")
        print("=" * 78)
        for name, text in r["sections"].items():
            print(f"\n--- {name.upper()} ---\n{text}\n")
        if not r["sections"]:
            print("  (abstract only - nothing retrieved at this tier)\n")


def _print_verify(res: dict) -> None:
    label = (res["claim"].get("title") or res["claim"].get("doi")
             or res["claim"].get("pmid") or "(empty citation)")
    icon = {"verified": "OK", "partial": "PARTIAL", "not_found": "NOT FOUND"}[res["status"]]
    # Status answers "does it exist", flags answer "should you cite it". A record
    # that exists but is retracted must not read as [OK] at a glance.
    flags = res.get("flags", [])
    if res["status"] != "not_found" and flags:
        icon = "RETRACTED" if any(
            "RETRACT" in f.upper() or "EXPRESSION OF CONCERN" in f.upper() for f in flags
        ) else "CHECK"
    print(f"[{icon}] {label}")
    if res.get("match"):
        m = res["match"]
        print(f"  -> {m['title']}")
        print(f"     {m.get('journal_abbrev') or m.get('journal')} {m['year']} | "
              f"PMID {m['pmid']}" + (f" | doi:{m['doi']}" if m.get("doi") else ""))
        print(f"     confidence {res['confidence']} (matched via {res['route']})")
    for f in res.get("flags", []):
        print(f"  !  {f}")
    for n in res.get("notes", []):
        print(f"  -  {n}")
    if res["status"] == "not_found" and res.get("candidates"):
        print("     closest candidates:")
        for c in res["candidates"][:3]:
            print(f"       {c['similarity']:.2f}  {c['title'][:75]} (PMID {c['pmid']})")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(prog="pubmed.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")

    # --json is accepted on either side of the subcommand. SUPPRESS keeps the
    # subparser from resetting a flag already set before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="emit JSON instead of text")

    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="search PubMed", parents=[common])
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--sort", default="relevance",
                   choices=["relevance", "pub_date", "author", "journal"])
    s.add_argument("--from", dest="mindate", help="earliest publication year")
    s.add_argument("--to", dest="maxdate", help="latest publication year")
    s.add_argument("--filter", help='extra PubMed syntax appended, e.g. "AND review[pt]"')
    s.add_argument("--abstract", action="store_true", help="include abstracts")

    f = sub.add_parser("fetch", help="fetch full records by PMID", parents=[common])
    f.add_argument("pmids", nargs="+")

    v = sub.add_parser("verify", help="verify one citation is a real paper", parents=[common])
    v.add_argument("--title", default="")
    v.add_argument("--doi", default="")
    v.add_argument("--pmid", default="")
    v.add_argument("--author", action="append", default=[], dest="authors")
    v.add_argument("--year", default="")

    c = sub.add_parser("check-refs",
                       help="verify a reference list (.json, .bib, or one title per line)", parents=[common])
    c.add_argument("path")
    c.add_argument("--fail-on-problem", action="store_true",
                   help="exit 1 if any reference is unverified, mismatched, or retracted")

    ct = sub.add_parser("cite", help="format PMIDs as citations", parents=[common])
    ct.add_argument("pmids", nargs="+")
    ct.add_argument("--style", default="ama", choices=["ama", "apa", "bibtex"])

    r = sub.add_parser("related", help="papers related to a PMID", parents=[common])
    r.add_argument("pmid")
    r.add_argument("--limit", type=int, default=10)

    sc = sub.add_parser("sections",
                        help="named full-text sections for PMIDs (open-access only)",
                        parents=[common])
    sc.add_argument("pmids", nargs="+")
    sc.add_argument("--sections", default=",".join(DEFAULT_SECTIONS),
                    help="comma-separated, e.g. discussion,conclusions,methods")
    sc.add_argument("--max-chars", type=int, default=20000,
                    help="per-section character cap before truncation")

    pf = sub.add_parser("pdf", help="download open-access PDFs, or write a stub",
                        parents=[common])
    pf.add_argument("pmids", nargs="+")
    pf.add_argument("--out", required=True, help="directory to write into")

    sub.add_parser("status", help="check API key configuration and connectivity", parents=[common])

    st = sub.add_parser("setup", help="configure API access (a key is optional)", parents=[common])
    st.add_argument("--key", help="your NCBI API key; validated before it is saved")
    st.add_argument("--no-key", action="store_true",
                    help="use the public API anonymously (3 req/sec)")
    st.add_argument("--email", default="", help="contact address to send to NCBI")

    args = p.parse_args()

    if args.cmd == "setup":
        return run_setup(args.key, args.no_key, args.email)

    if args.cmd == "status":
        _where, _source = env_home()
        info = {
            "api_key": f"set (...{API_KEY[-4:]})" if API_KEY else "not set",
            "rate_limit": "10 req/sec" if API_KEY else "3 req/sec (anonymous)",
            "email": EMAIL or "not set",
            "tool": TOOL,
            # Where the key is KEPT, and whether one is sitting somewhere the
            # tool is no longer reading. A key destroyed by an update fails
            # silently otherwise - the tool just drops to the anonymous tier
            # (item 84).
            "credentials_file": _where,
            "credentials_from": _source,
        }
        _stranded = stranded_env()
        if _stranded:
            info["stranded_credentials"] = _stranded
        try:
            probe = esearch("crispr", limit=1)
            info["connectivity"] = "ok"
            info["probe_matches"] = probe["count"]
        except Exception as exc:  # noqa: BLE001 - status report
            info["connectivity"] = f"FAILED: {exc}"
        if args.json:
            print(json.dumps(info, indent=2))
        else:
            for k, v in info.items():
                print(f"{k:16} {v}")
            if info.get("stranded_credentials"):
                print(f"\nWARNING: a credentials file at "
                      f"{info['stranded_credentials']} is NOT being read.\n"
                      f"The one in effect is {info['credentials_file']}.")
            if not API_KEY:
                print("\nTo raise the rate limit, get a free key at "
                      "https://account.ncbi.nlm.nih.gov/settings/ (API Key Management)\n"
                      f"and put NCBI_API_KEY=... in {info['credentials_file']}\n"
                      "(`pubmed.py setup` writes it there for you).")
        return 0 if info["connectivity"] == "ok" else 2

    if args.cmd == "search":
        query = args.query + (f" {args.filter}" if args.filter else "")
        hits = esearch(query, limit=args.limit, sort=args.sort,
                       mindate=args.mindate, maxdate=args.maxdate)
        records = efetch(hits["pmids"]) if args.abstract else esummary(hits["pmids"])
        if args.json:
            print(json.dumps({"query": query,
                              "translated_query": hits["translated_query"],
                              "total_matches": hits["count"],
                              "returned": len(records),
                              "results": records}, indent=2, ensure_ascii=False))
        else:
            print(f'{hits["count"]} matches for: {query}  (showing {len(records)})')
            _print_records(records)
        return 0

    if args.cmd == "fetch":
        records = efetch(args.pmids)
        print(json.dumps(records, indent=2, ensure_ascii=False)) if args.json \
            else _print_records(records)
        return 0

    if args.cmd == "verify":
        if not (args.title or args.doi or args.pmid):
            p.error("verify needs at least one of --title, --doi, or --pmid")
        res = verify(title=args.title, doi=args.doi, pmid=args.pmid,
                     authors=args.authors, year=args.year)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if args.json \
            else _print_verify(res)
        return 0 if (res["status"] == "verified" and not res["flags"]) else 1

    if args.cmd == "check-refs":
        refs = load_refs(args.path)
        results = [verify(title=ref.get("title", ""), doi=ref.get("doi", ""),
                          pmid=str(ref.get("pmid", "") or ""),
                          authors=ref.get("authors", []),
                          year=str(ref.get("year", "") or ""))
                   for ref in refs]
        summary = {
            "total": len(results),
            "clean": sum(1 for r in results if r["status"] == "verified" and not r["flags"]),
            "verified_with_flags": sum(1 for r in results
                                       if r["status"] == "verified" and r["flags"]),
            "partial": sum(1 for r in results if r["status"] == "partial"),
            "not_found": sum(1 for r in results if r["status"] == "not_found"),
            "retracted": sum(1 for r in results
                             if (r.get("match") or {}).get("is_retracted")),
        }
        if args.json:
            print(json.dumps({"summary": summary, "results": results},
                             indent=2, ensure_ascii=False))
        else:
            for res in results:
                _print_verify(res)
                print()
            print(f"{summary['total']} references: {summary['clean']} clean, "
                  f"{summary['verified_with_flags']} verified with flags, "
                  f"{summary['partial']} partial, {summary['not_found']} not found, "
                  f"{summary['retracted']} retracted.")
        return 1 if (args.fail_on_problem and summary["total"] != summary["clean"]) else 0

    if args.cmd == "cite":
        records = esummary(args.pmids)
        notes: list[str] = []
        if args.json:
            print(json.dumps(
                [{**r, "citation": format_citation(r, args.style, notes),
                  "notes": list(notes)} for r in records],
                indent=2, ensure_ascii=False))
        else:
            for r in records:
                print(format_citation(r, args.style, notes) + "\n")
            # Said rather than guessed at: an abbreviation this writer could
            # not derive, or an entry with no locator, is a hand edit the
            # user has to know about before the file reaches a portal.
            for n in notes:
                print("  note  " + n, file=sys.stderr)
        return 0

    if args.cmd == "related":
        records = esummary(elink_related(args.pmid, args.limit))
        print(json.dumps(records, indent=2, ensure_ascii=False)) if args.json \
            else _print_records(records)
        return 0

    if args.cmd == "sections":
        wanted = [s.strip().lower() for s in args.sections.split(",") if s.strip()]
        results = fetch_sections(args.pmids, wanted, max_chars=args.max_chars)
        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            _print_sections(results)
        # Exit 1 when nothing at all came back, so a caller can branch on it.
        return 0 if any(r["sections"] for r in results) else 1

    if args.cmd == "pdf":
        results = fetch_pdfs(args.pmids, args.out)
        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            for r in results:
                tag = {"pmc": "PMC", "unpaywall": "OA", "stub": "STUB",
                       "none": "MISS"}[r["source"]]
                print(f"[{tag:4}] {os.path.basename(r['path']) or '(nothing)'}")
                if r["note"]:
                    print(f"         {r['note']}")
            got = sum(1 for r in results if r["ok"])
            print(f"\n{got}/{len(results)} PDFs retrieved; "
                  f"{len(results) - got} stubbed. refs.bib written to {args.out}")
        return 0

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
