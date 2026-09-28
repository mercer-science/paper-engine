#!/usr/bin/env python3
"""
structure.py - keyless structural-biology engine: AlphaFold DB, UniProt,
RCSB PDB, PDBe and EMDB behind one normalized record and one CLI.

Why a second engine rather than a sixth source in scholar.py: scholar.py is a
*literature* engine. Its normalized record is a paper and its cascade exists to
answer "does this citation resolve to a real published work?" A protein
structure is not a paper and does not answer that question, so bolting AlphaFold
into the verify cascade would put a source that can never verify a reference
inside a loop whose whole job is verifying references. See
specs/external-services.md 3.

Same shape, second application: source registry, normalized record, --json on
every subcommand, one throttle, and never a bare exception - a failed source
degrades the record and says so in `notes` rather than aborting the command.

No API key, no email and no OAuth is required by any source here, which is what
keeps this engine consistent with the keyless-only rule of
specs/scholar-engine.md 2.

There is deliberately no skill. "Check AlphaFold for this protein" is one
command, reachable from a bare ask, from idea-generation Stage 5, or from
writing-engine's methods drafting, with no project directory required.

What structure data may be used for (specs/external-services.md 4.6): it is
evidence for FEASIBILITY and for METHODS. It is never evidence for a gap. "No
PDB entry exists, therefore this is unstudied" is the PubMed-found-nothing
fallacy with a different index - measured, a full-text search for the very
well-studied MOF HKUST-1 returns total_count: 1.

Usage:
  python structure.py alphafold --uniprot P00918
  python structure.py alphafold --uniprot P00918 --download models/ --format cif
  python structure.py alphafold --name "carbonic anhydrase 2" --organism 9606
  python structure.py resolve --name "carbonic anhydrase 2" --organism 9606
  python structure.py pdb --id 1CBS
  python structure.py pdb --search "HKUST-1" --limit 10
  python structure.py emdb --id EMD-3061
  python structure.py status
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from urllib.parse import quote

try:
    import requests
except ImportError:
    sys.exit("structure.py requires 'requests'. "
             "Install with: python -m pip install requests")

# pubmed.py sits beside this file and owns _load_env_file(). Imported rather
# than copied, exactly as scholar.py does it.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pubmed  # noqa: E402

# Same reason as pubmed.py and scholar.py: Windows consoles default to cp1252
# and would mangle or crash on the organism names and angstrom signs these APIs
# return.
for _stream in (sys.stdout, sys.stderr):
    # reconfigure() belongs to TextIOWrapper, not to every text stream: under a
    # pipe or a capturing harness sys.stdout may have no such method at all.
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except ValueError:      # detached or already-closed stream
            pass

pubmed._load_env_file()


# ---------------------------------------------------------------------------
# The User-Agent is load-bearing, and that is a measurement
# ---------------------------------------------------------------------------
#
# Measured 2026-09-06 and re-measured when this file was written: AlphaFold
# answers `User-Agent: probe` with 403 Forbidden, reproducibly, and answers a
# descriptive User-Agent with 200 and the same 14,887 bytes. It is deterministic
# UA filtering - not a rate limit, and not a key requirement.
#
# This is the Cu(111) trap of specs/scholar-engine.md 2 in a new costume: a
# failure that reads as "the service needs credentials" when it means "send a
# real User-Agent". So the header is defined once, sent on every request, and
# asserted by tests/structure.py rather than trusted.

USER_AGENT = ("paper-writing-aids-structure/1.0 "
              "(+research paper writing aids; keyless)")
UA = {"User-Agent": USER_AGENT, "Accept": "application/json"}

# A short UA is the failure above. Anything below this is not descriptive
# enough to be worth sending, and the suite pins the constant rather than the
# string so a future edit that shortens it fails loudly.
UA_MIN_LENGTH = 20


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

BASES = {
    "alphafold": "https://alphafold.ebi.ac.uk",
    "uniprot": "https://rest.uniprot.org",
    "rcsb": "https://data.rcsb.org",
    "rcsb_search": "https://search.rcsb.org",
    "pdbe": "https://www.ebi.ac.uk",
    "emdb": "https://www.ebi.ac.uk",
}

SOURCE_ROLE = {
    "alphafold": "predicted structures, per-residue pLDDT, model URLs",
    "uniprot": "name to accession resolution; sequence, length, organism",
    "rcsb": "experimental structures; metadata",
    "rcsb_search": "full-text search over the PDB",
    "pdbe": "entry summaries; the EBI-side view of the PDB",
    "emdb": "cryo-EM maps",
}

ALL_SOURCES = ["alphafold", "uniprot", "rcsb", "rcsb_search", "pdbe", "emdb"]

# Nothing here publishes a rate limit for anonymous use, so - exactly as in
# scholar.py - the honest move is a conservative fixed throttle rather than
# probing for a ceiling. AlphaFold measured at 136-212 ms and is the one called
# repeatedly, so it gets the shortest interval.
MIN_INTERVAL = {
    "alphafold": 0.34,
    "uniprot": 1.0,
    "rcsb": 0.5,
    "rcsb_search": 1.0,
    "pdbe": 0.5,
    "emdb": 0.5,
}
_last_call: dict[str, float] = {}

# Appended wherever a caller could read an empty or thin result as a gap in the
# literature. specs/external-services.md 4.6.
GAP_CAUTION = ("Absence here is not evidence of a gap: structural databases "
               "index depositions, not questions. A full-text search for "
               "HKUST-1, a very well studied material, returns one entry. A "
               "gap still needs a paper stating the limitation, or two papers "
               "that conflict.")

# pLDDT is a per-residue confidence produced by the model about its own output.
# Reporting it as an accuracy is wrong, and a methods section that implies
# otherwise is wrong in print. Every emission of a pLDDT number carries this.
PLDDT_STATEMENT = (
    "pLDDT is the model's own per-residue confidence on a 0-100 scale. It is "
    "not a measured accuracy and not a claim that the structure is correct. "
    "A predicted structure is always reported as predicted.")


class SourceUnreachable(Exception):
    """A source failed to answer. Never the same thing as a record being absent."""

    def __init__(self, source: str, reason: str):
        self.source, self.reason = source, reason
        super().__init__(f"{source} unreachable: {reason}")


class InvalidIdentifier(Exception):
    """The caller passed the wrong KIND of identifier.

    Kept distinct from "no such record" because the two have different
    remedies and because collapsing them is the trap this engine was written
    after. Measured: AlphaFold answers a well-formed accession with no model
    (titin, 34,350 aa; mucin-16, 14,507 aa) with 404 and an empty body, and
    answers a malformed one - or a UniProt ID such as CAH2_HUMAN, which is
    what people actually type - with 400 and an error message. 404 means
    "AlphaFold has no model for this protein", which is a fact about the world
    a user may need to report. 400 means "you typed the wrong kind of
    identifier".
    """

    def __init__(self, source: str, given: str, reason: str):
        self.source, self.given, self.reason = source, given, reason
        super().__init__(
            f"{source}: {given!r} is not a valid identifier: {reason}")


def _throttle(source: str) -> None:
    interval = MIN_INTERVAL.get(source, 1.0)
    delta = time.monotonic() - _last_call.get(source, 0.0)
    if delta < interval:
        time.sleep(interval - delta)
    _last_call[source] = time.monotonic()


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


def _forbidden(source: str, resp) -> "SourceUnreachable":
    return SourceUnreachable(
        source,
        f"HTTP 403 with User-Agent {USER_AGENT!r}. Measured on AlphaFold, a "
        f"403 here is User-Agent filtering rather than a credential "
        f"requirement - a short UA is refused and a descriptive one is not. "
        f"Do not add an API key in response to this; there is none to add. "
        f"Body: {resp.text[:160]}")


def _handle(source: str, resp, given: str):
    """The status-code contract, in one place because it is the whole trap.

    200 -> payload. 404 -> None, meaning the record genuinely does not exist.
    400 -> InvalidIdentifier. 403 -> the User-Agent trap, named as such.
    Everything else returns False so the caller retries, and a caller that
    runs out of retries raises SourceUnreachable - so a source that did not
    answer can never be reported as a record that is not there.
    """
    if resp.status_code == 200:
        try:
            return resp.json()
        except ValueError:
            raise SourceUnreachable(source, "returned a non-JSON body")
    if resp.status_code == 404:
        return None
    if resp.status_code == 400:
        detail = ""
        try:
            detail = (resp.json() or {}).get("error", "")
        except ValueError:
            detail = resp.text[:160]
        raise InvalidIdentifier(source, given, detail or "HTTP 400")
    if resp.status_code == 403:
        raise _forbidden(source, resp)
    return False        # caller retries


def _request(source: str, method: str, url: str, given: str = "",
             params: dict | None = None, payload: dict | None = None,
             timeout: int = 30, retries: int = 3):
    last = ""
    for attempt in range(retries):
        _throttle(source)
        headers = dict(UA)
        if payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            resp = requests.request(method, url, params=params, json=payload,
                                    headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(1.5 * (attempt + 1))
            continue
        out = _handle(source, resp, given or url)
        if out is not False:
            return out
        if resp.status_code in (429, 500, 502, 503, 504):
            wait = _retry_after(resp)
            if wait > RETRY_AFTER_BUDGET:
                raise SourceUnreachable(
                    source,
                    f"rate-limited: the server asked for {wait:.0f}s before "
                    f"the next request. That is a quota lockout, not an "
                    f"outage - re-run after it resets.")
            last = f"HTTP {resp.status_code}"
            time.sleep(wait if wait else 1.5 * (attempt + 1))
            continue
        raise SourceUnreachable(
            source, f"HTTP {resp.status_code}: {resp.text[:200]}")
    raise SourceUnreachable(source, f"failed after {retries} tries ({last})")


def _get(source: str, url: str, given: str = "", params: dict | None = None,
         timeout: int = 30, retries: int = 3):
    return _request(source, "GET", url, given, params=params,
                    timeout=timeout, retries=retries)


def _post(source: str, url: str, payload: dict, given: str = "",
          timeout: int = 30, retries: int = 3):
    return _request(source, "POST", url, given, payload=payload,
                    timeout=timeout, retries=retries)


# ---------------------------------------------------------------------------
# The normalized record
# ---------------------------------------------------------------------------
#
# One shape whether the structure was predicted or measured, because the
# caller's question - "what do we know about this structure?" - does not change.
# specs/external-services.md 4.5.

RECORD_DEFAULTS = {
    "identifier": "", "kind": "", "source": "",
    "uniprot": "", "title": "", "organism": "", "taxid": "",
    "method": "", "resolution_A": None,
    "confidence": None,          # predicted only
    "sequence_length": None, "model_date": "",
    "urls": {}, "fragments": 1, "fragment_index": 1,
    "isoform": "", "covers": "", "is_whole_sequence": None,
    "notes": [],
}


def blank_record(**kw) -> dict:
    """A record with every key present.

    Absent values stay ""/[]/None per the default, never a key that is simply
    missing - a caller reading `.get()` on a key that is sometimes there and
    sometimes not is how a silent None gets formatted into a methods section.
    """
    rec = {k: (v.copy() if isinstance(v, (list, dict)) else v)
           for k, v in RECORD_DEFAULTS.items()}
    rec.update(kw)
    return rec


# AF-P00918-F1          -> accession P00918, isoform "",  fragment 1
# AF-P00520-4-F1        -> accession P00520, isoform "4", fragment 1
# AF-Q8WZ42-F7          -> accession Q8WZ42, isoform "",  fragment 7
ENTRY_ID_RE = re.compile(
    r"^AF-(?P<acc>[A-Z0-9]+)(?:-(?P<iso>\d+))?-F(?P<frag>\d+)$", re.I)


def parse_entry_id(entry_id: str) -> dict:
    """Split an AlphaFold entry id into accession, isoform and fragment.

    This exists because of a measurement that contradicted the assumption the
    spec was written on. specs/external-services.md 4.5 recorded that P00520
    "returned four entries where P69905 returned one" and read that as
    AlphaFold splitting a long sequence into fragments. The four entries are

        AF-P00520-F1    1-1123
        AF-P00520-4-F1  1-1142
        AF-P00520-3-F1  1-1118
        AF-P00520-2-F1  1-1117

    - every one of them F1, and every one of them whole. They are four
    ISOFORMS, not four fragments of one chain. Both cases are real (AlphaFold
    does fragment very long sequences, as F1/F2/...), they need opposite
    handling, and the entry id is the only thing that tells them apart:
    several fragments must never be reported as a whole protein, while several
    isoforms must never be silently collapsed to the first one.
    """
    m = ENTRY_ID_RE.match((entry_id or "").strip())
    if not m:
        return {"accession": "", "isoform": "", "fragment": 1, "parsed": False}
    return {"accession": m.group("acc").upper(),
            "isoform": m.group("iso") or "",
            "fragment": int(m.group("frag")),
            "parsed": True}


def _confidence(entry: dict) -> dict:
    """The confidence block, which is never a bare number.

    fractionPlddtVeryHigh: 0.985 means 98.5% of residues are modelled at very
    high confidence. It is not an accuracy claim about the structure, and the
    statement travels with the numbers so that no caller has to remember it.
    """
    mean = entry.get("globalMetricValue")
    block = {
        "mean_plddt": mean,
        "fraction_very_high": entry.get("fractionPlddtVeryHigh"),
        "fraction_confident": entry.get("fractionPlddtConfident"),
        "fraction_low": entry.get("fractionPlddtLow"),
        "fraction_very_low": entry.get("fractionPlddtVeryLow"),
        "scale": "pLDDT, 0-100, per residue",
        "means": PLDDT_STATEMENT,
    }
    if mean is None:
        block["means"] = ("No mean pLDDT was returned for this model. "
                          + PLDDT_STATEMENT)
    return block


def _af_record(entry: dict, siblings: list[dict]) -> dict:
    """One AlphaFold entry as a normalized record."""
    ids = parse_entry_id(entry.get("entryId", ""))
    same_isoform = [e for e in siblings
                    if parse_entry_id(e.get("entryId", "")).get("isoform")
                    == ids.get("isoform")]
    n_frag = len(same_isoform) or 1
    start = entry.get("uniprotStart")
    end = entry.get("uniprotEnd")
    seq = entry.get("uniprotSequence") or entry.get("sequence") or ""
    whole = None
    if start is not None and end is not None and seq:
        whole = (int(start) == 1 and int(end) == len(seq))
    elif n_frag > 1:
        whole = False

    notes: list[str] = []
    if n_frag > 1:
        notes.append(
            f"This is fragment {ids['fragment']} of {n_frag} for this "
            f"sequence, covering residues {start}-{end}. It is NOT the whole "
            f"protein; the other fragments carry the rest.")
    elif whole is False:
        notes.append(
            f"This model covers residues {start}-{end} of a "
            f"{len(seq)}-residue sequence, not the whole of it.")
    version = entry.get("latestVersion")
    return blank_record(
        identifier=entry.get("entryId", ""),
        kind="predicted",
        source="alphafold",
        uniprot=(entry.get("uniprotAccession") or ids.get("accession") or ""),
        title=entry.get("uniprotDescription", ""),
        organism=entry.get("organismScientificName", ""),
        taxid=str(entry.get("taxId") or ""),
        method=(f"AlphaFold v{version}" if version else "AlphaFold"),
        confidence=_confidence(entry),
        sequence_length=(len(seq) or None),
        model_date=(entry.get("modelCreatedDate") or "")[:10],
        urls={"cif": entry.get("cifUrl", ""), "pdb": entry.get("pdbUrl", ""),
              "bcif": entry.get("bcifUrl", ""),
              "pae": entry.get("paeDocUrl", ""),
              "plddt": entry.get("plddtDocUrl", ""),
              "page": "https://alphafold.ebi.ac.uk/entry/"
                      + (entry.get("uniprotAccession") or "")},
        fragments=n_frag,
        fragment_index=ids.get("fragment", 1),
        isoform=ids.get("isoform", ""),
        covers=(f"{start}-{end}" if start is not None and end is not None
                else ""),
        is_whole_sequence=whole,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# UniProt - name to accession, and the rule that it never picks
# ---------------------------------------------------------------------------

def resolve(name: str, organism: str = "", limit: int = 5) -> dict:
    """Resolve a protein NAME to UniProt accessions. Never picks one.

    Measured 2026-09-06: searching human carbonic anhydrase 2 returns
    [P23280, P00918, P35219] and the correct accession, P00918, is SECOND.
    Silently taking the top hit would have fetched the wrong protein's
    structure and reported it confidently, which is the exact failure class
    this toolkit exists to prevent. So this returns candidates and a `chosen`
    of None, always, and the caller asks.

    Latency supports asking anyway: UniProt search was the slowest call
    measured here at 1.73 s, about 8x AlphaFold's own.
    """
    out = {"query": name, "organism": organism, "candidates": [],
           "chosen": None, "notes": [], "unreachable": []}
    if not (name or "").strip():
        out["notes"].append("No name given.")
        return out

    query = name.strip()
    if organism:
        # `organism_id:` takes a TAXONOMY NUMBER. Interpolating whatever the
        # caller typed sent `organism_id:human` to UniProt, which answers HTTP
        # 400 - and the 400 was raised as InvalidIdentifier carrying the
        # NAME, so the message blamed the one argument that was correct
        # (item 77). Measured 2026-09-16: `organism_id:human` is a 400, while
        # `organism_name:human` and `organism_id:9606` both return the wanted
        # accession first. So the field is chosen by what the value IS.
        org = str(organism).strip()
        field = "organism_id" if org.isdigit() else "organism_name"
        query += f' AND {field}:"{org}"' if not org.isdigit() \
            else f" AND organism_id:{org}"
        out["organism_field"] = field
    url = (f"{BASES['uniprot']}/uniprotkb/search"
           f"?query={quote(query)}"
           f"&fields=accession,id,protein_name,organism_name,length"
           f"&size={max(1, min(int(limit), 25))}")
    try:
        data = _get("uniprot", url, given=query)
    except SourceUnreachable as exc:
        out["unreachable"].append({"source": "uniprot", "reason": exc.reason})
        out["notes"].append(
            "UniProt did not answer, so no name could be resolved. This is "
            "not a statement that the protein does not exist.")
        return out
    except InvalidIdentifier as exc:
        # Reported, never raised out of here. A search that UniProt refuses is
        # a finding about the QUERY, and the caller who is scripting this
        # needs it as a payload rather than as a traceback - the same shape
        # as every other engine crash in this toolkit. The note names the
        # organism, because that is the argument that can be refused: the
        # protein name is free text and is never the thing a 400 is about.
        out["notes"].append(
            "UniProt refused this search: %s. The query sent was %r. Nothing "
            "here assumes which argument was wrong, because the first version "
            "of this message confidently blamed the one that was right: "
            "--organism must be an NCBI taxonomy number or a name UniProt "
            "knows (human is 9606, and `human` itself works), and a --name "
            "containing query syntax - AND, OR, a colon, an unbalanced "
            "parenthesis - is read as a query rather than as text."
            % (getattr(exc, "reason", "") or "HTTP 400", query))
        out["refused"] = True
        return out

    for row in (data or {}).get("results", []):
        desc = (row.get("proteinDescription") or {})
        full = ((desc.get("recommendedName") or {}).get("fullName") or {})
        if not full.get("value"):
            subs = desc.get("submissionNames") or []
            if subs:
                full = (subs[0].get("fullName") or {})
        org = row.get("organism") or {}
        out["candidates"].append({
            "accession": row.get("primaryAccession", ""),
            "id": row.get("uniProtkbId", ""),
            "protein_name": full.get("value", ""),
            "organism": org.get("scientificName", ""),
            "taxid": str(org.get("taxonId") or ""),
            "length": (row.get("sequence") or {}).get("length"),
            "reviewed": "reviewed" in (row.get("entryType", "").lower()),
        })

    if not out["candidates"]:
        out["notes"].append(
            f"UniProt returned no entry for {name!r}"
            + (f" in organism {organism}" if organism else "")
            + ". Check the spelling, or drop the organism filter.")
        return out

    out["notes"].append(
        f"{len(out['candidates'])} candidate(s). Nothing is chosen here on "
        f"purpose: the correct accession is not reliably the first one - "
        f"measured, human carbonic anhydrase 2 returns P23280 above P00918. "
        f"Pick one and re-run with --uniprot <accession>.")
    return out


# ---------------------------------------------------------------------------
# AlphaFold
# ---------------------------------------------------------------------------

def alphafold(accession: str) -> dict:
    """Predicted structures for one UniProt accession.

    Three outcomes, three HTTP codes, and they must never collapse:
      200 -> one or more models
      404 -> well-formed accession, NO model. AlphaFold excludes very large
             sequences, which is a real coverage gap and a fact the user may
             need to report.
      400 -> the wrong KIND of identifier, and the overwhelmingly likely
             mistake is a UniProt ID (CAH2_HUMAN) rather than an accession.
    """
    acc = (accession or "").strip()
    out = {"accession": acc.upper(), "status": "", "records": [],
           "isoforms": [], "notes": [], "unreachable": []}
    if not acc:
        out["status"] = "invalid_identifier"
        out["notes"].append("No accession given.")
        return out

    url = f"{BASES['alphafold']}/api/prediction/{quote(acc)}"
    try:
        data = _get("alphafold", url, given=acc)
    except InvalidIdentifier as exc:
        out["status"] = "invalid_identifier"
        hint = ""
        if re.match(r"^[A-Z0-9]+_[A-Z0-9]+$", acc.upper()):
            hint = (f" {acc!r} looks like a UniProt ID rather than an "
                    f"accession; run `structure.py resolve --name \"...\"` to "
                    f"get the accession.")
        out["notes"].append(
            f"AlphaFold refused this identifier: {exc.reason}.{hint} This is "
            f"NOT a statement that no model exists.")
        return out
    except SourceUnreachable as exc:
        out["status"] = "unreachable"
        out["unreachable"].append({"source": "alphafold",
                                   "reason": exc.reason})
        out["notes"].append(
            "AlphaFold did not answer. That is not the same as there being no "
            "model; nothing about this protein has been established.")
        return out

    if data is None or not data:
        out["status"] = "no_model"
        out["notes"].append(
            f"AlphaFold has no model for {acc.upper()}. The accession is "
            f"well-formed - the database genuinely does not cover it, which "
            f"it does not for very large sequences. That is a real coverage "
            f"gap and reportable as one.")
        out["notes"].append(GAP_CAUTION)
        return out

    entries = data if isinstance(data, list) else [data]
    out["status"] = "ok"
    out["records"] = [_af_record(e, entries) for e in entries]

    isoforms = sorted({r["isoform"] for r in out["records"]})
    out["isoforms"] = [i or "canonical" for i in isoforms]
    if len(isoforms) > 1:
        out["notes"].append(
            f"{len(entries)} models were returned, covering "
            f"{len(isoforms)} isoforms ({', '.join(out['isoforms'])}). They "
            f"are separate sequences, not fragments of one. Nothing is chosen "
            f"here: say which isoform the work is about.")
    fragmented = [r for r in out["records"] if r["fragments"] > 1]
    if fragmented:
        out["notes"].append(
            f"{len(fragmented)} of these records are fragments of a longer "
            f"sequence and do not individually cover the whole protein.")
    out["notes"].append(PLDDT_STATEMENT)
    return out


def alphafold_by_name(name: str, organism: str = "") -> dict:
    """Name -> candidates. Deliberately stops there.

    It does not fetch a structure, because doing so would mean picking an
    accession, and resolve() exists precisely because picking is what must not
    happen automatically.
    """
    res = resolve(name, organism)
    res["status"] = "choose" if res["candidates"] else "unresolved"
    res["next"] = ("structure.py alphafold --uniprot <accession>"
                   if res["candidates"] else "")
    return res


MODEL_FORMATS = ("cif", "pdb", "bcif")


def download(rec: dict, dest_dir: str, fmt: str = "cif") -> dict:
    """Fetch one model file. Returns what was written, or why it was not."""
    if fmt not in MODEL_FORMATS:
        return {"ok": False,
                "reason": f"unknown format {fmt!r}; "
                          f"choose from {', '.join(MODEL_FORMATS)}"}
    url = (rec.get("urls") or {}).get(fmt, "")
    if not url:
        return {"ok": False, "reason": f"this record carries no {fmt} URL"}
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, os.path.basename(url.split("?")[0]))
    try:
        _throttle("alphafold")
        resp = requests.get(url, headers={"User-Agent": USER_AGENT},
                            timeout=60)
    except requests.RequestException as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    if resp.status_code != 200:
        return {"ok": False, "reason": f"HTTP {resp.status_code}"}
    with open(path, "wb") as fh:
        fh.write(resp.content)
    return {"ok": True, "path": path, "bytes": len(resp.content),
            "note": "A predicted model. " + PLDDT_STATEMENT}


# ---------------------------------------------------------------------------
# RCSB PDB, PDBe and EMDB
# ---------------------------------------------------------------------------

def _rcsb_record(data: dict) -> dict:
    info = data.get("rcsb_entry_info") or {}
    res = info.get("resolution_combined") or []
    methods = [m.get("method", "") for m in (data.get("exptl") or [])]
    acc = data.get("rcsb_accession_info") or {}
    ident = (data.get("rcsb_id") or (data.get("entry") or {}).get("id", ""))
    return blank_record(
        identifier=ident,
        kind="experimental",
        source="rcsb",
        title=(data.get("struct") or {}).get("title", ""),
        method="; ".join([m for m in methods if m]),
        resolution_A=(res[0] if res else None),
        model_date=(acc.get("initial_release_date") or "")[:10],
        urls={"page": "https://www.rcsb.org/structure/" + ident,
              "cif": f"https://files.rcsb.org/download/{ident}.cif"},
    )


def _yyyymmdd(raw: str) -> str:
    raw = (raw or "").strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw[:10]


def _pdbe_record(pdb_id: str, data: dict) -> dict:
    rows = (data or {}).get(pdb_id.lower()) or []
    row = rows[0] if rows else {}
    return blank_record(
        identifier=pdb_id.upper(),
        kind="experimental",
        source="pdbe",
        title=row.get("title", ""),
        method="; ".join(row.get("experimental_method") or []),
        model_date=_yyyymmdd(row.get("release_date", "")),
        urls={"page": "https://www.ebi.ac.uk/pdbe/entry/pdb/"
                      + pdb_id.lower()},
        notes=["Read from PDBe rather than RCSB; PDBe's entry summary does "
               "not carry a resolution, so resolution_A is unset rather than "
               "guessed."],
    )


def pdb_entry(pdb_id: str) -> dict:
    """One experimental entry, from RCSB, degrading to PDBe.

    Two sources for one question is not redundancy for its own sake: they are
    two views of the same archive, and a caller who cannot get an answer from
    one of them should get the other plus a note, not an exception. The command
    still exits 0.
    """
    pid = (pdb_id or "").strip()
    out = {"id": pid.upper(), "status": "", "records": [], "notes": [],
           "unreachable": []}
    if not re.match(r"^[0-9][A-Za-z0-9]{3}$", pid):
        out["status"] = "invalid_identifier"
        out["notes"].append(
            f"{pid!r} is not a PDB id. A PDB id is four characters beginning "
            f"with a digit, such as 1CBS.")
        return out

    try:
        data = _get("rcsb", f"{BASES['rcsb']}/rest/v1/core/entry/{quote(pid)}",
                    given=pid)
        if data:
            out["status"] = "ok"
            out["records"].append(_rcsb_record(data))
            return out
        out["status"] = "not_found"
        out["notes"].append(f"RCSB has no entry {pid.upper()}.")
        out["notes"].append(GAP_CAUTION)
        return out
    except SourceUnreachable as exc:
        out["unreachable"].append({"source": "rcsb", "reason": exc.reason})

    try:
        data = _get("pdbe",
                    f"{BASES['pdbe']}/pdbe/api/pdb/entry/summary/"
                    f"{quote(pid.lower())}", given=pid)
    except SourceUnreachable as exc:
        out["unreachable"].append({"source": "pdbe", "reason": exc.reason})
        out["status"] = "unreachable"
        out["notes"].append(
            "Neither RCSB nor PDBe answered. Nothing has been established "
            "about this entry - this is not a statement that it does not "
            "exist.")
        return out

    if not data:
        out["status"] = "not_found"
        out["notes"].append(
            f"RCSB did not answer and PDBe has no entry {pid.upper()}.")
        return out
    out["status"] = "ok"
    out["records"].append(_pdbe_record(pid, data))
    out["notes"].append(
        "RCSB did not answer; this record came from PDBe instead and is "
        "thinner for it. The fields it does not carry are unset rather than "
        "filled in from somewhere else.")
    return out


def pdb_search(term: str, limit: int = 10) -> dict:
    """Full-text search over the PDB.

    total_count travels with the hits, and GAP_CAUTION travels with both,
    because the measured behaviour of this endpoint is exactly the thing that
    invites a wrong conclusion: HKUST-1, a MOF with a large literature,
    returns total_count 1.
    """
    q = (term or "").strip()
    out = {"query": q, "total_count": None, "hits": [], "notes": [],
           "unreachable": []}
    if not q:
        out["notes"].append("No search term given.")
        return out
    payload = {
        "query": {"type": "terminal", "service": "full_text",
                  "parameters": {"value": q}},
        "return_type": "entry",
        "request_options": {"paginate": {
            "start": 0, "rows": max(1, min(int(limit), 100))}},
    }
    try:
        data = _post("rcsb_search",
                     f"{BASES['rcsb_search']}/rcsbsearch/v2/query",
                     payload, given=q)
    except SourceUnreachable as exc:
        out["unreachable"].append({"source": "rcsb_search",
                                   "reason": exc.reason})
        out["notes"].append(
            "The PDB search did not answer. No conclusion about coverage "
            "follows from that.")
        return out

    if not data:
        out["total_count"] = 0
        out["notes"].append(f"No PDB entry matches {q!r}.")
        out["notes"].append(GAP_CAUTION)
        return out

    out["total_count"] = data.get("total_count", 0)
    for hit in data.get("result_set", []) or []:
        out["hits"].append({"identifier": hit.get("identifier", ""),
                            "score": hit.get("score")})
    out["notes"].append(GAP_CAUTION)
    return out


def _emdb_record(data: dict) -> dict:
    admin = data.get("admin") or {}
    dates = admin.get("key_dates") or {}
    sd = ((data.get("structure_determination_list") or {})
          .get("structure_determination") or [])
    sd0 = (sd[0] if isinstance(sd, list) and sd
           else (sd if isinstance(sd, dict) else {}))
    method = sd0.get("method", "")
    res = None
    ip = sd0.get("image_processing") or []
    ip0 = (ip[0] if isinstance(ip, list) and ip
           else (ip if isinstance(ip, dict) else {}))
    fr = ip0.get("final_reconstruction") or {}
    raw = (fr.get("resolution") or {}).get("valueOf_")
    if raw:
        try:
            res = float(raw)
        except (TypeError, ValueError):
            res = None
    return blank_record(
        identifier=data.get("emdb_id", ""),
        kind="map",
        source="emdb",
        title=admin.get("title", ""),
        method=("ELECTRON MICROSCOPY" + (f" ({method})" if method else "")),
        resolution_A=res,
        model_date=(dates.get("map_release") or dates.get("deposition")
                    or "")[:10],
        urls={"page": "https://www.ebi.ac.uk/emdb/"
                      + (data.get("emdb_id") or "")},
    )


def emdb_entry(emdb_id: str) -> dict:
    eid = (emdb_id or "").strip().upper()
    if eid and eid.isdigit():
        eid = "EMD-" + eid
    out = {"id": eid, "status": "", "records": [], "notes": [],
           "unreachable": []}
    if not re.match(r"^EMD-\d{4,5}$", eid):
        out["status"] = "invalid_identifier"
        out["notes"].append(
            f"{emdb_id!r} is not an EMDB id. An EMDB id looks like EMD-3061.")
        return out
    try:
        data = _get("emdb", f"{BASES['emdb']}/emdb/api/entry/{quote(eid)}",
                    given=eid)
    except SourceUnreachable as exc:
        out["unreachable"].append({"source": "emdb", "reason": exc.reason})
        out["status"] = "unreachable"
        out["notes"].append(
            "EMDB did not answer. That is not a statement that the map does "
            "not exist.")
        return out
    if not data:
        out["status"] = "not_found"
        out["notes"].append(f"EMDB has no entry {eid}.")
        out["notes"].append(GAP_CAUTION)
        return out
    out["status"] = "ok"
    out["records"].append(_emdb_record(data))
    return out


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

PROBES = {
    "alphafold": ("GET", "/api/prediction/P69905"),
    "uniprot": ("GET",
                "/uniprotkb/search?query=P00918&fields=accession&size=1"),
    "rcsb": ("GET", "/rest/v1/core/entry/1CBS"),
    "rcsb_search": ("POST", "/rcsbsearch/v2/query"),
    "pdbe": ("GET", "/pdbe/api/pdb/entry/summary/1cbs"),
    "emdb": ("GET", "/emdb/api/entry/EMD-3061"),
}


def status() -> dict:
    info = {"sources": [], "keys_required": "none",
            "user_agent": USER_AGENT,
            "note": ("Every source here is keyless. The User-Agent is not "
                     "optional: AlphaFold answers a short one with 403, which "
                     "reads like a credential requirement and is not one.")}
    for name in ALL_SOURCES:
        method, path = PROBES[name]
        url = BASES[name] + path
        try:
            if method == "POST":
                _post(name, url, {
                    "query": {"type": "terminal", "service": "full_text",
                              "parameters": {"value": "lysozyme"}},
                    "return_type": "entry",
                    "request_options": {"paginate": {"start": 0, "rows": 1}}},
                    retries=1)
            else:
                _get(name, url, retries=1)
            ok, detail = True, "ok"
        except (SourceUnreachable, InvalidIdentifier) as exc:
            ok, detail = False, getattr(exc, "reason", str(exc))
        info["sources"].append({"source": name, "ok": ok, "detail": detail,
                                "role": SOURCE_ROLE[name]})
    return info


# ---------------------------------------------------------------------------
# Human output
# ---------------------------------------------------------------------------

def _print_record(rec: dict) -> None:
    label = {"predicted": "PREDICTED", "experimental": "EXPERIMENTAL",
             "map": "MAP"}.get(rec["kind"], rec["kind"].upper())
    print(f"  [{label}] {rec['identifier']}   {rec['source']}")
    if rec["title"]:
        print(f"    {rec['title']}")
    bits = []
    if rec["organism"]:
        bits.append(rec["organism"]
                    + (f" ({rec['taxid']})" if rec["taxid"] else ""))
    if rec["method"]:
        bits.append(rec["method"])
    if rec["resolution_A"] is not None:
        bits.append(f"{rec['resolution_A']} A")
    if rec["sequence_length"]:
        bits.append(f"{rec['sequence_length']} aa")
    if rec["model_date"]:
        bits.append(rec["model_date"])
    if bits:
        print("    " + "  |  ".join(bits))
    if rec["fragments"] > 1:
        print(f"    fragment {rec['fragment_index']} of {rec['fragments']}"
              f"  covers {rec['covers']}")
    if rec["isoform"]:
        print(f"    isoform {rec['isoform']}")
    conf = rec.get("confidence")
    if conf:
        # Every pLDDT number printed here is printed with what pLDDT is. A
        # bare "97.4" in a terminal becomes "97% accurate" in a methods
        # section, and that sentence is wrong in print.
        mean = conf.get("mean_plddt")
        vhigh = conf.get("fraction_very_high")
        line = "    confidence: "
        if mean is not None:
            line += f"mean pLDDT {mean}"
        if vhigh is not None:
            line += (("; " if mean is not None else "")
                     + f"{vhigh * 100:.1f}% of residues at very high "
                       f"confidence")
        print(line)
        # The general statement is printed once per command, out of `notes`.
        # Printing it under every record on a four-isoform protein buries the
        # numbers it is meant to qualify, which is its own way of not being
        # read. A record-specific variant (no mean returned) still prints here.
        if conf["means"] != PLDDT_STATEMENT:
            print(f"      {conf['means']}")
    for note in rec.get("notes", []):
        print(f"    note: {note}")


def _print_notes(payload: dict) -> None:
    for u in payload.get("unreachable", []):
        print(f"  UNREACHABLE {u['source']}: {u['reason']}")
    for note in payload.get("notes", []):
        print(f"  note: {note}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _apply_api_base(base: str) -> None:
    """Point every source at one host. For tests, and for a local mirror."""
    for key in BASES:
        BASES[key] = base.rstrip("/")


def main() -> int:
    p = argparse.ArgumentParser(
        prog="structure.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true",
                   help="emit JSON instead of text")

    # --json is accepted on either side of the subcommand, the same contract
    # pubmed.py and scholar.py carry. SUPPRESS keeps the subparser from
    # resetting a flag already set before it, and defaults are applied to the
    # namespace AFTER parsing rather than with set_defaults: parents= shares
    # the action objects rather than copying them, so a set_defaults on the
    # top parser writes through to the action every subparser also holds. That
    # cost report.py a silently ignored --json once already.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true",
                        default=argparse.SUPPRESS,
                        help="emit JSON instead of text")
    common.add_argument("--api-base", default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)

    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("alphafold", parents=[common],
                       help="predicted structure for a UniProt accession")
    a.add_argument("--uniprot", default="")
    a.add_argument("--name", default="")
    a.add_argument("--organism", default="", help="NCBI taxon id, e.g. 9606")
    a.add_argument("--download", default="", metavar="DIR")
    a.add_argument("--format", dest="fmt", default="cif",
                   choices=list(MODEL_FORMATS))

    r = sub.add_parser("resolve", parents=[common],
                       help="protein name -> UniProt accession candidates")
    r.add_argument("--name", required=True)
    r.add_argument("--organism", default="")
    r.add_argument("--limit", type=int, default=5)

    d = sub.add_parser("pdb", parents=[common],
                       help="experimental structures (RCSB, degrading to PDBe)")
    d.add_argument("--id", dest="pdb_id", default="")
    d.add_argument("--search", default="")
    d.add_argument("--limit", type=int, default=10)

    e = sub.add_parser("emdb", parents=[common], help="a cryo-EM map entry")
    e.add_argument("--id", dest="emdb_id", required=True)

    sub.add_parser("status", parents=[common],
                   help="which sources answer right now")

    args = p.parse_args()
    for key, default in (("json", False), ("api_base", "")):
        if not hasattr(args, key):
            setattr(args, key, default)
    as_json = bool(args.json)
    if args.api_base:
        _apply_api_base(args.api_base)

    if args.cmd == "status":
        info = status()
        if as_json:
            print(json.dumps(info, indent=2))
        else:
            for row in info["sources"]:
                print(f"  {row['source']:14} "
                      f"{'ok' if row['ok'] else 'FAILED':8} {row['detail']}")
            print(f"\n{info['note']}")
        return 0 if all(s["ok"] for s in info["sources"]) else 1

    if args.cmd == "resolve":
        res = resolve(args.name, args.organism, args.limit)
        if as_json:
            print(json.dumps(res, indent=2))
        else:
            for c in res["candidates"]:
                print(f"  {c['accession']:10} {c['id']:16} "
                      f"{c['protein_name'][:44]:46} {c['organism']}"
                      + (f"  {c['length']} aa" if c["length"] else ""))
            _print_notes(res)
        return 0 if res["candidates"] else 1

    if args.cmd == "alphafold":
        if args.name and not args.uniprot:
            res = alphafold_by_name(args.name, args.organism)
            if as_json:
                print(json.dumps(res, indent=2))
            else:
                for c in res["candidates"]:
                    print(f"  {c['accession']:10} {c['id']:16} "
                          f"{c['protein_name'][:44]:46} {c['organism']}")
                _print_notes(res)
                if res.get("next"):
                    print(f"\n  next: {res['next']}")
            return 1        # nothing was fetched; a choice is owed
        if not args.uniprot:
            print("give --uniprot <accession> or --name \"<protein name>\"",
                  file=sys.stderr)
            return 2
        res = alphafold(args.uniprot)
        if res["status"] == "ok" and args.download:
            res["downloads"] = [download(rec, args.download, args.fmt)
                                for rec in res["records"]]
        if as_json:
            print(json.dumps(res, indent=2))
        else:
            for rec in res["records"]:
                _print_record(rec)
            for dl in res.get("downloads", []):
                print("  " + (f"wrote {dl['path']} ({dl['bytes']} B)"
                              if dl["ok"]
                              else f"NOT downloaded: {dl['reason']}"))
            _print_notes(res)
        return 0 if res["status"] == "ok" else 1

    if args.cmd == "pdb":
        if args.search:
            res = pdb_search(args.search, args.limit)
            if as_json:
                print(json.dumps(res, indent=2))
            else:
                print(f"  {res['total_count']} entr"
                      f"{'y' if res['total_count'] == 1 else 'ies'} match "
                      f"{res['query']!r}")
                for h in res["hits"]:
                    print(f"    {h['identifier']}   score {h['score']}")
                _print_notes(res)
            return 0 if res["hits"] else 1
        if not args.pdb_id:
            print("give --id <PDB id> or --search \"<term>\"", file=sys.stderr)
            return 2
        res = pdb_entry(args.pdb_id)
        if as_json:
            print(json.dumps(res, indent=2))
        else:
            for rec in res["records"]:
                _print_record(rec)
            _print_notes(res)
        return 0 if res["status"] == "ok" else 1

    if args.cmd == "emdb":
        res = emdb_entry(args.emdb_id)
        if as_json:
            print(json.dumps(res, indent=2))
        else:
            for rec in res["records"]:
                _print_record(rec)
            _print_notes(res)
        return 0 if res["status"] == "ok" else 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
