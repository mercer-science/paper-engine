#!/usr/bin/env python3
"""
tests/structure.py - measure tools/structure.py against the cases
specs/external-services.md 4.7 names.

Fully offline, ~6s. Every payload in tests/fixtures/structure/ is a real
captured response, saved with the status code the live service actually
returned - including the 400 body AlphaFold sends for a malformed accession,
the empty 404 body it sends for titin, and the 403 it sends for a short
User-Agent. They are served back by a stub HTTP server on localhost rather
than mocked, for the same reason tests/report.py runs a real server: a mock
asserts that the code calls the functions the test expects it to call, which
is the one thing never in doubt.

The five that matter most, because each fails quietly:

  - 404 and 400 must not collapse into one "not found" (spec 4.2, trap 2).
    This failing means "AlphaFold has no model for this protein" - a real
    coverage gap, and a reportable finding - and "you typed a UniProt ID
    instead of an accession" become the same sentence.
  - the descriptive User-Agent must be sent on every request (trap 1). This
    failing means a 403 that reads as "the service needs credentials" when
    there is no credential to add.
  - name resolution must never auto-pick (trap 3). Measured, human carbonic
    anhydrase 2 returns P23280 above the correct P00918, so a top-hit pick
    fetches the wrong protein and reports it confidently.
  - a multi-entry AlphaFold answer must not claim to be whole. Four entries
    for P00520 are four ISOFORMS, each complete; several F-numbered entries
    are fragments of one chain, each partial. Same shape, opposite meaning.
  - pLDDT must never be emitted as a bare accuracy claim, because the
    sentence it turns into in a methods section is wrong in print.

  python tests/structure.py
  python tests/structure.py -v
"""

from __future__ import annotations

import http.server
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except ValueError:
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIX = os.path.join(HERE, "fixtures", "structure")


# tests/structure.py and tools/structure.py share a name, so a plain
# `import structure` resolves to this file. Load the engine by path under a
# distinct module name, exactly as every other suite here does. Do not
# "simplify" this back.
def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load " + path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


st = _load("structure_engine", os.path.join(ROOT, "tools", "structure.py"))

VERBOSE = "-v" in sys.argv
PASS = FAIL = 0


def check(label: str, got, want: object = True, op: str = "eq") -> bool:
    global PASS, FAIL
    if op == "eq":
        ok = got == want
    elif op == "in":
        ok = want in got
    elif op == "not in":
        ok = want not in got
    elif op == "ge":
        ok = got >= want
    elif op == "is":
        ok = got is want
    else:
        raise ValueError(op)
    if ok:
        PASS += 1
        if VERBOSE:
            print("  ok   %s" % label)
    else:
        FAIL += 1
        print("  FAIL %s\n         got:  %r\n         want: %s %r"
              % (label, got, op, want))
    return ok


def section(title: str) -> None:
    print("\n%s\n%s" % (title, "-" * len(title)))


def fixture(name: str) -> bytes:
    with open(os.path.join(FIX, name), "rb") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# The stub: real payloads, real status codes, and the User-Agent filter
# ---------------------------------------------------------------------------
#
# The UA filter is not decoration. AlphaFold answers `User-Agent: probe` with
# 403 and a descriptive one with 200, reproducibly, and the stub reproduces
# exactly that so the guard is measured rather than asserted.

class Stub(http.server.BaseHTTPRequestHandler):
    down: set = set()          # source keys currently "unreachable"
    seen: list = []            # (method, path, user-agent)

    def log_message(self, format, *args):   # keep the suite's output clean
        pass

    # --- which source does this path belong to -----------------------------
    @staticmethod
    def source_of(path: str) -> str:
        if path.startswith("/api/prediction/"):
            return "alphafold"
        if path.startswith("/uniprotkb/"):
            return "uniprot"
        if path.startswith("/rest/v1/core/entry/"):
            return "rcsb"
        if path.startswith("/rcsbsearch/"):
            return "rcsb_search"
        if path.startswith("/pdbe/"):
            return "pdbe"
        if path.startswith("/emdb/"):
            return "emdb"
        return ""

    def _raw(self, code: int, raw: bytes,
             ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, code: int, obj) -> None:
        self._raw(code, json.dumps(obj).encode("utf-8"))

    def _guard(self) -> bool:
        """True when the request may proceed."""
        ua = self.headers.get("User-Agent") or ""
        Stub.seen.append((self.command, self.path, ua))
        if len(ua) < st.UA_MIN_LENGTH:
            self._raw(403, fixture("af_short_ua_403.txt"), "text/html")
            return False
        if self.source_of(self.path) in Stub.down:
            self._json(503, {"message": "temporarily unavailable"})
            return False
        return True

    def do_GET(self):
        if not self._guard():
            return
        path = self.path

        m = re.match(r"^/api/prediction/([^/?]+)", path)
        if m:
            acc = urllib.parse.unquote(m.group(1)).upper()
            if acc in ("P00918", "P00520", "P69905"):
                return self._raw(200, fixture("af_%s.json" % acc))
            if acc == "FRAGMENTED":
                return self._raw(200, FRAGMENT_PAYLOAD)
            if acc in ("Q8WZ42", "Q8WXI7"):
                return self._raw(404, fixture("af_Q8WZ42_404.json"))
            return self._raw(400, fixture("af_NOTREAL9_400.json"))

        if path.startswith("/uniprotkb/search"):
            q = urllib.parse.parse_qs(
                urllib.parse.urlparse(path).query).get("query", [""])[0]
            # Measured against the live API 2026-09-16: `organism_id:` takes
            # a taxonomy NUMBER and answers 400 for anything else, while
            # `organism_name:human` and `organism_id:9606` both answer 200.
            # An unbalanced parenthesis in the free-text half is a 400 too.
            # Both are replayed because item 77 is about which of them the
            # message blames.
            m_org = re.search(r'organism_id:"?([^\s"]+)', q)
            if (m_org and not m_org.group(1).isdigit()) \
                    or q.count("(") != q.count(")"):
                return self._json(400, {"url": path, "messages": [
                    "the query is not valid: %s" % q[:80]]})
            if "nothing-like-this" in q:
                return self._json(200, {"results": []})
            return self._raw(200, fixture("uniprot_ca2_human.json"))

        m = re.match(r"^/rest/v1/core/entry/([^/?]+)", path)
        if m:
            if m.group(1).upper() == "1CBS":
                return self._raw(200, fixture("rcsb_1CBS.json"))
            return self._json(404, {"message": "no entry"})

        m = re.match(r"^/pdbe/api/pdb/entry/summary/([^/?]+)", path)
        if m:
            if m.group(1).lower() == "1cbs":
                return self._raw(200, fixture("pdbe_1cbs.json"))
            return self._json(404, {})

        m = re.match(r"^/emdb/api/entry/([^/?]+)", path)
        if m:
            if m.group(1).upper() == "EMD-3061":
                return self._raw(200, fixture("emdb_EMD-3061.json"))
            return self._json(404, {})

        return self._json(404, {"message": "unrouted"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if not self._guard():
            return
        if self.path.startswith("/rcsbsearch/"):
            term = (((body.get("query") or {}).get("parameters") or {})
                    .get("value", ""))
            if term == "no-such-material":
                return self._json(200, {"total_count": 0, "result_set": []})
            return self._raw(200, fixture("rcsb_search_HKUST1.json"))
        return self._json(404, {"message": "unrouted"})


def _fragment_payload() -> bytes:
    """A genuine two-fragment answer, built from a real one.

    AlphaFold really does split very long sequences into F1/F2/..., and no
    accession in the captured set exercises it - the four P00520 entries are
    isoforms. So the fragment case is synthesised from the real P69905 record
    by changing exactly the three fields that make it a fragment, which keeps
    every other field's shape honest.
    """
    base = json.loads(fixture("af_P69905.json").decode("utf-8"))[0]
    seq = "M" * 2700
    out = []
    for idx, (lo, hi) in enumerate(((1, 1400), (1401, 2700)), start=1):
        e = dict(base)
        e["entryId"] = "AF-Q8WZ99-F%d" % idx
        e["uniprotAccession"] = "Q8WZ99"
        e["uniprotStart"], e["uniprotEnd"] = lo, hi
        e["uniprotSequence"] = seq
        e["sequenceStart"], e["sequenceEnd"] = lo, hi
        out.append(e)
    return json.dumps(out).encode("utf-8")


FRAGMENT_PAYLOAD = _fragment_payload()


def start_stub() -> tuple[str, http.server.HTTPServer]:
    srv = http.server.HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d" % srv.server_address[1], srv


BASE, SERVER = start_stub()
st._apply_api_base(BASE)


def cli(*args: str) -> tuple[int, str]:
    """Run the real CLI against the stub."""
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "structure.py")]
        + list(args) + ["--api-base", BASE],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# ---------------------------------------------------------------------------
# 1. Record parsing, all five sources, from real payloads
# ---------------------------------------------------------------------------

def test_records() -> None:
    section("Record parsing - five sources, real captured payloads")

    res = st.alphafold("P00918")
    check("alphafold P00918 status", res["status"], "ok")
    check("one record", len(res["records"]), 1)
    rec = res["records"][0]
    check("identifier", rec["identifier"], "AF-P00918-F1")
    check("kind is predicted", rec["kind"], "predicted")
    check("source", rec["source"], "alphafold")
    check("uniprot", rec["uniprot"], "P00918")
    check("title", rec["title"], "Carbonic anhydrase 2")
    check("organism", rec["organism"], "Homo sapiens")
    check("taxid", rec["taxid"], "9606")
    check("method names the version", rec["method"], "AlphaFold v6")
    check("sequence length", rec["sequence_length"], 260)
    check("model date is a date, not a timestamp",
          rec["model_date"], "2025-08-01")
    check("cif URL", rec["urls"]["cif"], "https://alphafold.ebi.ac.uk/files/"
          "AF-P00918-F1-model_v6.cif")
    check("pae URL is carried", rec["urls"]["pae"], "predicted_aligned_error",
          op="in")
    check("a predicted record has no resolution",
          rec["resolution_A"], None, op="is")
    check("every default key is present",
          sorted(rec.keys()) == sorted(st.RECORD_DEFAULTS.keys()))

    ent = st.pdb_entry("1CBS")
    check("pdb 1CBS status", ent["status"], "ok")
    prec = ent["records"][0]
    check("pdb identifier", prec["identifier"], "1CBS")
    check("pdb kind", prec["kind"], "experimental")
    check("pdb source is rcsb", prec["source"], "rcsb")
    check("pdb method", prec["method"], "X-RAY DIFFRACTION")
    check("pdb resolution", prec["resolution_A"], 1.8)
    check("pdb release date", prec["model_date"], "1995-01-26")
    check("pdb title", prec["title"], "CELLULAR RETINOIC-ACID", op="in")
    check("an experimental record has no confidence block",
          prec["confidence"], None, op="is")

    srch = st.pdb_search("HKUST-1")
    check("search total_count", srch["total_count"], 1)
    check("search hit", srch["hits"][0]["identifier"], "8WXB")

    emd = st.emdb_entry("EMD-3061")
    check("emdb status", emd["status"], "ok")
    erec = emd["records"][0]
    check("emdb identifier", erec["identifier"], "EMD-3061")
    check("emdb kind is map", erec["kind"], "map")
    check("emdb method", erec["method"],
          "ELECTRON MICROSCOPY (singleParticle)")
    check("emdb resolution is read out of final_reconstruction",
          erec["resolution_A"], 3.4)
    check("emdb release date", erec["model_date"], "2015-08-12")
    check("emdb title", erec["title"], "gamma-secretase", op="in")

    # PDBe is reachable only through the degradation path, so it is parsed
    # directly here as well - a record shape nothing exercises is a record
    # shape nobody has read.
    data = json.loads(fixture("pdbe_1cbs.json").decode("utf-8"))
    pd = st._pdbe_record("1cbs", data)
    check("pdbe identifier is upper-cased", pd["identifier"], "1CBS")
    check("pdbe source", pd["source"], "pdbe")
    check("pdbe method", pd["method"], "X-ray diffraction")
    check("pdbe date is normalised from YYYYMMDD",
          pd["model_date"], "1995-01-26")
    check("pdbe says what it cannot carry",
          " ".join(pd["notes"]), "resolution", op="in")


# ---------------------------------------------------------------------------
# 2. Isoforms and fragments - the measurement that corrected the spec
# ---------------------------------------------------------------------------

def test_fragments_and_isoforms() -> None:
    section("Isoforms are not fragments, and a fragment never claims to be "
            "whole")

    ids = st.parse_entry_id("AF-P00520-4-F1")
    check("isoform is parsed off the id", ids["isoform"], "4")
    check("fragment index", ids["fragment"], 1)
    check("accession", ids["accession"], "P00520")
    check("canonical has no isoform",
          st.parse_entry_id("AF-P00918-F1")["isoform"], "")
    check("a real fragment number is parsed",
          st.parse_entry_id("AF-Q8WZ42-F7")["fragment"], 7)
    check("an unparseable id says so",
          st.parse_entry_id("garbage")["parsed"], False)

    res = st.alphafold("P00520")
    check("P00520 returns four entries", len(res["records"]), 4)
    check("four isoforms, named", res["isoforms"],
          ["canonical", "2", "3", "4"])
    check("no entry is reported as a fragment",
          [r["fragments"] for r in res["records"]], [1, 1, 1, 1])
    check("each isoform is whole",
          [r["is_whole_sequence"] for r in res["records"]],
          [True, True, True, True])
    check("the payload says these are isoforms rather than fragments",
          " ".join(res["notes"]), "not fragments of one", op="in")
    check("and refuses to choose one", " ".join(res["notes"]),
          "Nothing is chosen here", op="in")
    check("no record claims a fragment note",
          any("NOT the whole protein" in n
              for r in res["records"] for n in r["notes"]), False)

    frag = st.alphafold("FRAGMENTED")
    check("a fragmented sequence returns its fragments",
          len(frag["records"]), 2)
    check("fragment count is on the record",
          [r["fragments"] for r in frag["records"]], [2, 2])
    check("fragment index is on the record",
          [r["fragment_index"] for r in frag["records"]], [1, 2])
    check("NEITHER fragment claims to be the whole sequence",
          [r["is_whole_sequence"] for r in frag["records"]], [False, False])
    check("and each says so in its own notes",
          all("NOT the whole protein" in " ".join(r["notes"])
              for r in frag["records"]))
    check("the covered range is stated",
          [r["covers"] for r in frag["records"]], ["1-1400", "1401-2700"])
    check("the payload counts the fragmented records",
          " ".join(frag["notes"]), "are fragments of a longer", op="in")


# ---------------------------------------------------------------------------
# 3. Three status codes, three outcomes
# ---------------------------------------------------------------------------

def test_three_codes() -> None:
    section("200, 404 and 400 are three different answers")

    ok = st.alphafold("P69905")
    no = st.alphafold("Q8WZ42")
    bad = st.alphafold("NOTREAL9")

    check("200 -> ok", ok["status"], "ok")
    check("404 -> no_model", no["status"], "no_model")
    check("400 -> invalid_identifier", bad["status"], "invalid_identifier")
    check("the three statuses are distinct",
          len({ok["status"], no["status"], bad["status"]}), 3)

    no_text = " ".join(no["notes"])
    bad_text = " ".join(bad["notes"])
    check("404 says the accession is well-formed", no_text,
          "well-formed", op="in")
    check("404 says AlphaFold has no model", no_text, "has no model", op="in")
    check("404 never says the input was invalid", no_text.lower(),
          "invalid", op="not in")
    check("404 is reportable as a coverage gap", no_text,
          "coverage gap", op="in")
    check("404 still carries the gap caution", no_text,
          "not evidence of a gap", op="in")

    check("400 quotes the server's own reason", bad_text,
          "Invalid identifier format", op="in")
    check("400 does not claim no model exists", bad_text,
          "NOT a statement that no model exists", op="in")
    check("400 never says coverage gap", bad_text, "coverage gap",
          op="not in")

    uid = st.alphafold("CAH2_HUMAN")
    check("a UniProt ID is a 400, not a 404", uid["status"],
          "invalid_identifier")
    check("and the hint names the actual mistake",
          " ".join(uid["notes"]), "looks like a UniProt ID", op="in")
    check("and points at resolve", " ".join(uid["notes"]),
          "resolve --name", op="in")

    check("no model exits 1, not 0", cli("alphafold", "--uniprot",
                                         "Q8WZ42")[0], 1)
    check("the CLI's no-model text does not say invalid",
          cli("alphafold", "--uniprot", "Q8WZ42")[1].lower(),
          "invalid", op="not in")

    empty = st.alphafold("")
    check("an empty accession is refused without a request",
          empty["status"], "invalid_identifier")

    check("a malformed PDB id is refused locally",
          st.pdb_entry("nope")["status"], "invalid_identifier")
    check("a real-shaped PDB id that does not exist is not_found",
          st.pdb_entry("9ZZZ")["status"], "not_found")
    check("not_found and invalid_identifier are different words",
          st.pdb_entry("9ZZZ")["status"] != st.pdb_entry("nope")["status"])
    check("a malformed EMDB id is refused locally",
          st.emdb_entry("3061x")["status"], "invalid_identifier")
    check("a bare number is accepted as an EMDB id",
          st.emdb_entry("3061")["id"], "EMD-3061")


# ---------------------------------------------------------------------------
# 4. The User-Agent guard
# ---------------------------------------------------------------------------

def test_user_agent() -> None:
    section("The User-Agent is sent on every request, and a 403 says so")

    check("the constant is descriptive enough to pass the filter",
          len(st.USER_AGENT) >= st.UA_MIN_LENGTH)
    check("it names the toolkit", st.USER_AGENT, "paper-writing-aids",
          op="in")
    check("it is defined once and reused in the header dict",
          st.UA["User-Agent"], st.USER_AGENT)

    Stub.seen.clear()
    st.alphafold("P00918")
    st.resolve("carbonic anhydrase 2", "9606")
    st.pdb_entry("1CBS")
    st.pdb_search("HKUST-1")
    st.emdb_entry("EMD-3061")
    check("five sources were actually called", len(Stub.seen) >= 5)
    check("EVERY request carried the descriptive User-Agent",
          all(ua == st.USER_AGENT for _, _, ua in Stub.seen))
    check("including the POST", any(m == "POST" for m, _, _ in Stub.seen))

    # The regression guard: with a short UA the stub answers exactly what
    # AlphaFold answered, and the engine must report it as UA filtering rather
    # than as a credential requirement.
    real = st.UA
    try:
        setattr(st, "UA",
                {"User-Agent": "probe", "Accept": "application/json"})
        res = st.alphafold("P00918")
    finally:
        setattr(st, "UA", real)
    check("a short UA fails rather than silently working",
          res["status"], "unreachable")
    reason = " ".join(u["reason"] for u in res["unreachable"])
    check("the reason names the User-Agent", reason, "User-Agent", op="in")
    check("the reason names the 403", reason, "403", op="in")
    check("and says not to add an API key", reason,
          "there is none to add", op="in")
    check("the engine works again once the header is restored",
          st.alphafold("P00918")["status"], "ok")


# ---------------------------------------------------------------------------
# 5. Name resolution never auto-picks
# ---------------------------------------------------------------------------

def test_resolution_never_picks() -> None:
    section("Name resolution shows candidates and never chooses")

    res = st.resolve("carbonic anhydrase 2", "9606")
    accs = [c["accession"] for c in res["candidates"]]
    check("the measured order is preserved", accs[:3],
          ["P23280", "P00918", "P35219"])
    check("the correct accession is NOT first", accs[0] != "P00918")
    check("nothing is chosen", res["chosen"], None, op="is")
    check("no key holds an answer", "accession" in res, False)
    check("the note says why nothing was chosen", " ".join(res["notes"]),
          "not reliably the first one", op="in")
    check("and names the measurement", " ".join(res["notes"]),
          "P23280 above P00918", op="in")
    check("candidates carry the UniProt ID people actually type",
          res["candidates"][1]["id"], "CAH2_HUMAN")
    check("candidates carry the length", res["candidates"][1]["length"], 260)
    check("candidates carry the organism",
          res["candidates"][1]["organism"], "Homo sapiens")
    check("reviewed status is read, not assumed",
          res["candidates"][1]["reviewed"], True)

    by_name = st.alphafold_by_name("carbonic anhydrase 2", "9606")
    check("alphafold --name stops at a choice", by_name["status"], "choose")
    check("it fetched no structure", "records" in by_name, False)
    check("it names the next command", by_name["next"],
          "--uniprot", op="in")
    check("P23280 is never returned as the answer",
          by_name.get("chosen"), None, op="is")

    rc, out = cli("alphafold", "--name", "carbonic anhydrase 2")
    check("the CLI exits nonzero because a choice is owed", rc, 1)
    check("the CLI lists the candidates", out, "P00918", op="in")
    check("the CLI does not print a structure record", out,
          "PREDICTED", op="not in")

    none = st.resolve("nothing-like-this-protein")
    check("no candidates is not an error", none["candidates"], [])
    check("and says what to try", " ".join(none["notes"]),
          "Check the spelling", op="in")
    check("an empty name makes no request", st.resolve("")["candidates"], [])

    # --- item 77: --organism is a taxonomy number OR a name ---------------
    #
    # Interpolating whatever was typed into `organism_id:` sent
    # `organism_id:human` to UniProt, which answers 400 - and the 400 was
    # raised as InvalidIdentifier carrying the protein NAME, so the command
    # died with a traceback blaming the one argument that was correct. The
    # identical name succeeded the moment the organism was given as a number.
    common = st.resolve("carbonic anhydrase 2", "human")
    check("a common-name organism resolves rather than crashing",
          [c["accession"] for c in common["candidates"]][:3],
          ["P23280", "P00918", "P35219"])
    check("...because the field is chosen by what the value IS",
          (common["organism_field"],
           st.resolve("carbonic anhydrase 2", "9606")["organism_field"]),
          ("organism_name", "organism_id"))

    # A search the server refuses is REPORTED, not raised: a caller scripting
    # this needs a payload, the same as every other engine failure here.
    refused = st.resolve("carbonic anhydrase 2 AND (", "human")
    note = " ".join(refused["notes"])
    check("a refused search comes back as a payload, not a traceback",
          (refused["refused"], refused["candidates"]), (True, []))
    check("...quoting the query that was actually sent",
          "organism_name" in note)
    # An error that names the wrong culprit is worse than a bare stack trace,
    # because it is confidently actionable and the action is wrong.
    check("...and naming BOTH arguments as possibilities rather than "
          "confidently blaming one",
          ("--organism" in note, "--name" in note), (True, True))


# ---------------------------------------------------------------------------
# 6. confidence is never a bare accuracy claim
# ---------------------------------------------------------------------------

def test_confidence() -> None:
    section("pLDDT is never emitted as an accuracy")

    rec = st.alphafold("P00918")["records"][0]
    conf = rec["confidence"]
    check("mean pLDDT is carried", conf["mean_plddt"], 97.38)
    check("the very-high fraction is carried",
          conf["fraction_very_high"], 0.985)
    check("the low fraction is carried too", conf["fraction_low"], 0.008)
    check("the scale travels with the numbers", conf["scale"],
          "per residue", op="in")
    check("so does what it means", conf["means"],
          "not a measured accuracy", op="in")
    check("and that it is a prediction", conf["means"],
          "reported as predicted", op="in")
    check("no key is called accuracy",
          [k for k in conf if "accur" in k.lower()], [])

    missing = st._confidence({"fractionPlddtVeryHigh": 0.5})
    check("an absent mean is None, not zero",
          missing["mean_plddt"], None, op="is")
    check("and the record says the mean is absent", missing["means"],
          "No mean pLDDT was returned", op="in")

    rc, out = cli("alphafold", "--uniprot", "P00918")
    check("the CLI prints the number", out, "97.38", op="in")
    check("labelled as pLDDT", out, "mean pLDDT", op="in")
    check("as a per-residue confidence", out, "per-residue confidence",
          op="in")
    check("and as a prediction", out, "[PREDICTED]", op="in")
    check("the word accuracy appears only inside the disclaimer",
          re.sub(r"not a measured accuracy", "", out).lower(),
          "accura", op="not in")

    rc, jout = cli("--json", "alphafold", "--uniprot", "P00918")
    payload = json.loads(jout)
    check("--json before the subcommand is honoured", rc, 0)
    check("the JSON confidence block carries the statement",
          payload["records"][0]["confidence"]["means"],
          "not a measured accuracy", op="in")


# ---------------------------------------------------------------------------
# 7. Throttle and degradation
# ---------------------------------------------------------------------------

def test_throttle_and_degradation() -> None:
    section("A source that is down degrades the record and still exits 0")

    real = st.MIN_INTERVAL["emdb"]
    try:
        st.MIN_INTERVAL["emdb"] = 0.4
        st._last_call.pop("emdb", None)
        t0 = time.monotonic()
        st.emdb_entry("EMD-3061")
        st.emdb_entry("EMD-3061")
        elapsed = time.monotonic() - t0
    finally:
        st.MIN_INTERVAL["emdb"] = real
        st._last_call.pop("emdb", None)
    check("two calls to one source are spaced by the throttle",
          elapsed >= 0.4)
    check("every source has an interval",
          sorted(st.MIN_INTERVAL) == sorted(st.ALL_SOURCES))

    Stub.down = {"rcsb"}
    try:
        ent = st.pdb_entry("1CBS")
    finally:
        Stub.down = set()
    check("RCSB down still answers", ent["status"], "ok")
    check("from PDBe instead", ent["records"][0]["source"], "pdbe")
    check("the failure is recorded rather than hidden",
          [u["source"] for u in ent["unreachable"]], ["rcsb"])
    check("and named in the notes", " ".join(ent["notes"]),
          "RCSB did not answer", op="in")
    check("the degraded record does not invent a resolution",
          ent["records"][0]["resolution_A"], None, op="is")

    Stub.down = {"rcsb"}
    try:
        rc, out = cli("pdb", "--id", "1CBS")
    finally:
        Stub.down = set()
    check("a degraded answer still exits 0", rc, 0)
    check("and says which source it came from", out, "pdbe", op="in")

    Stub.down = {"rcsb", "pdbe"}
    try:
        ent = st.pdb_entry("1CBS")
    finally:
        Stub.down = set()
    check("both down is unreachable, not not_found",
          ent["status"], "unreachable")
    check("both failures are recorded",
          sorted(u["source"] for u in ent["unreachable"]), ["pdbe", "rcsb"])
    check("and it refuses to conclude the entry does not exist",
          " ".join(ent["notes"]), "not a statement that it does not", op="in")

    Stub.down = {"alphafold"}
    try:
        res = st.alphafold("P00918")
    finally:
        Stub.down = set()
    check("AlphaFold down is unreachable, never no_model",
          res["status"], "unreachable")
    check("and says nothing has been established",
          " ".join(res["notes"]), "not the same as there being no model",
          op="in")

    Stub.down = {"uniprot"}
    try:
        res = st.resolve("carbonic anhydrase 2")
    finally:
        Stub.down = set()
    check("UniProt down yields no candidates", res["candidates"], [])
    check("and does not claim the protein is absent",
          " ".join(res["notes"]),
          "not a statement that the protein does not exist", op="in")

    Stub.down = {"rcsb_search"}
    try:
        res = st.pdb_search("HKUST-1")
    finally:
        Stub.down = set()
    check("a dead search returns no count rather than zero",
          res["total_count"], None, op="is")
    check("and draws no conclusion about coverage",
          " ".join(res["notes"]), "No conclusion about coverage", op="in")


# ---------------------------------------------------------------------------
# 8. What structure data may not be used for
# ---------------------------------------------------------------------------

def test_gap_caution() -> None:
    section("Absence is never evidence of a gap")

    empty = st.pdb_search("no-such-material")
    check("an empty search is a real zero", empty["total_count"], 0)
    check("and carries the caution", " ".join(empty["notes"]),
          "not evidence of a gap", op="in")
    check("the caution names the measured case",
          st.GAP_CAUTION, "HKUST-1", op="in")
    check("and says what a gap does need", st.GAP_CAUTION,
          "two papers that conflict", op="in")
    check("a thin but nonzero result carries it too",
          " ".join(st.pdb_search("HKUST-1")["notes"]),
          "not evidence of a gap", op="in")
    check("a missing PDB entry carries it",
          " ".join(st.pdb_entry("9ZZZ")["notes"]),
          "not evidence of a gap", op="in")
    check("a missing EMDB entry carries it",
          " ".join(st.emdb_entry("EMD-9999")["notes"]),
          "not evidence of a gap", op="in")


# ---------------------------------------------------------------------------
# 9. The CLI contract
# ---------------------------------------------------------------------------

def test_cli() -> None:
    section("The CLI answers every command with --json")

    cases = [
        ("alphafold", ("alphafold", "--uniprot", "P00918")),
        ("resolve", ("resolve", "--name", "carbonic anhydrase 2")),
        ("pdb --id", ("pdb", "--id", "1CBS")),
        ("pdb --search", ("pdb", "--search", "HKUST-1")),
        ("emdb", ("emdb", "--id", "EMD-3061")),
        ("status", ("status",)),
    ]
    for label, argv in cases:
        rc, out = cli(*argv, "--json")
        check("%s --json exits 0" % label, rc, 0)
        try:
            payload = json.loads(out)
            ok = isinstance(payload, dict)
        except ValueError:
            ok = False
        check("%s --json emits one JSON object" % label, ok)
        _, out2 = cli("--json", *argv)
        check("%s honours --json before the subcommand" % label,
              out2.strip().startswith("{"))

    rc, out = cli("alphafold")
    check("alphafold with neither flag is a usage error", rc, 2)
    check("and says what to give", out, "--uniprot", op="in")
    rc, out = cli("pdb")
    check("pdb with neither flag is a usage error", rc, 2)

    info = st.status()
    check("status reports every source", len(info["sources"]),
          len(st.ALL_SOURCES))
    check("status says no key is required", info["keys_required"], "none")
    check("status names the User-Agent it sends",
          info["user_agent"], st.USER_AGENT)
    check("status explains the 403", info["note"], "403", op="in")
    check("every source is ok against the stub",
          all(s["ok"] for s in info["sources"]))
    check("every source states its role",
          all(s["role"] for s in info["sources"]))

    Stub.down = {"emdb"}
    try:
        rc, out = cli("status")
    finally:
        Stub.down = set()
    check("status exits nonzero when a source is down", rc, 1)
    check("and names it", out, "FAILED", op="in")


# ---------------------------------------------------------------------------
# 10. The engine's own shape
# ---------------------------------------------------------------------------

def test_shape() -> None:
    section("Reuse rather than a second copy")

    src = io.open(os.path.join(ROOT, "tools", "structure.py"),
                  encoding="utf-8").read()
    check("it imports pubmed rather than copying _load_env_file",
          src, "import pubmed", op="in")
    check("it does not define its own _load_env_file",
          src, "def _load_env_file", op="not in")
    check("it reconfigures both streams to UTF-8", src, "reconfigure",
          op="in")
    check("no source here needs a key",
          re.search(r"api[_-]?key", src, re.I), None, op="is")
    check("there is no skill directory for this engine",
          os.path.isdir(os.path.join(ROOT, "structure")), False)
    check("SOURCE_ROLE covers every registered source",
          sorted(st.SOURCE_ROLE) == sorted(st.ALL_SOURCES))
    check("PROBES covers every registered source",
          sorted(st.PROBES) == sorted(st.ALL_SOURCES))
    check("BASES covers every registered source",
          sorted(st.BASES) == sorted(st.ALL_SOURCES))
    check("a structure record is not a paper record",
          "doi" in st.RECORD_DEFAULTS, False)


def main() -> int:
    print("tools/structure.py - specs/external-services.md 4")
    print("stub at %s, fixtures in tests/fixtures/structure/" % BASE)
    test_records()
    test_fragments_and_isoforms()
    test_three_codes()
    test_user_agent()
    test_resolution_never_picks()
    test_confidence()
    test_throttle_and_degradation()
    test_gap_caution()
    test_cli()
    test_shape()
    print("\n%d checks: %d passed, %d failed" % (PASS + FAIL, PASS, FAIL))
    SERVER.shutdown()
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
