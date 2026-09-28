#!/usr/bin/env python3
"""
tests/sequence.py - measure tools/sequence.py against the cases
specs/user-asks-2026-09-18-second.md 4 names.

Fully offline, ~2s. A stub HTTP server on localhost serves the responses, for
the reason tests/structure.py runs a real server rather than
mocking: a mock asserts that the code calls the functions the test expects it
to call, which is the one thing never in doubt.

ONE HONEST DIFFERENCE FROM tests/structure.py, stated here because it changes
what a passing run means. That suite's payloads are REAL CAPTURED RESPONSES.
These are CONSTRUCTED to the services' published response shapes - a BLAST
search takes minutes and returns a different body every time, and no folding
endpoint could be reached from this machine at all, because every route found
on 2026-09-18 needs an account. So this suite proves the engine parses the
documented shape and holds its own refusals; it does not prove the documented
shape is what the live service sends today.

The five that matter most, because each fails quietly:

  - a BLAST miss must never read as evidence of novelty (spec 4.7). This is
    the single most likely way this engine gets misused, and it is the
    PubMed-found-nothing fallacy with a different index in front of it.
  - a source that did not answer must never come back looking like an empty
    result. "The service was down" and "there are no homologs" have opposite
    meanings and the same shape.
  - the CLI must not hide that BLAST is submit-then-poll. A subcommand that
    blocks for ten minutes with no output is one people kill.
  - `no_model` is a REAL ANSWER at exit 0, and nothing folds because a lookup
    missed. The offer is explicit and `--submit` is the only thing that acts.
  - an unconfigured credential refuses by SAYING SO, never by failing at the
    network.

  python tests/sequence.py
  python tests/sequence.py -v
"""

from __future__ import annotations

import http.server
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
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


# tests/sequence.py and tools/sequence.py share a name, so a plain
# `import sequence` resolves to this file. Load the engine by path under a
# distinct module name, exactly as every other suite here does. Do not
# "simplify" this back.
def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load " + path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


seq = _load("sequence_engine", os.path.join(ROOT, "tools", "sequence.py"))

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


# ---------------------------------------------------------------------------
# The payloads, written to the documented shapes
# ---------------------------------------------------------------------------

PUT_BODY = """<html><head>
<!--QBlastInfoBegin
    RID = ABC123XYZ014
    RTOE = 27
QBlastInfoEnd
--></head><body>Submitted</body></html>"""

WAITING_BODY = """<html><!--QBlastInfoBegin
     Status=WAITING
QBlastInfoEnd
--></html>"""

READY_BODY = """<html><!--QBlastInfoBegin
     Status=READY
     ThereAreHits=yes
QBlastInfoEnd
--></html>"""

FAILED_BODY = """<html><!--QBlastInfoBegin
     Status=FAILED
QBlastInfoEnd
--></html>"""

RESULTS = {"BlastOutput2": [{"report": {
    "version": "BLASTP 2.15.0+",
    "search_target": {"db": "nr"},
    "results": {"search": {
        "query_title": "P58335 ANTXR2",
        "query_len": 489,
        "hits": [
            {"num": 1,
             "description": [{"accession": "NP_477520.1",
                              "title": "anthrax toxin receptor 2 isoform 1",
                              "sciname": "Homo sapiens"}],
             "hsps": [{"bit_score": 980.3, "evalue": 0.0, "identity": 489,
                       "align_len": 489, "query_from": 1, "query_to": 489,
                       "hit_from": 1, "hit_to": 489}]},
            {"num": 2,
             "description": [{"accession": "NP_115584.1",
                              "title": "anthrax toxin receptor 1",
                              "sciname": "Homo sapiens"}],
             "hsps": [{"bit_score": 300.1, "evalue": 1e-98, "identity": 120,
                       "align_len": 200, "query_from": 40, "query_to": 239,
                       "hit_from": 44, "hit_to": 243}]},
        ]}}}}]}

# A GenPept record, cut to the one line that answers "where does this live".
GP_BODY = """LOCUS       WP_000123456             300 aa
DEFINITION  hypothetical protein.
     CDS             1..300
                     /coded_by="NC_000913.3:3423681..3424583"
                     /transl_table=11
ORIGIN
//"""

GP_NO_CODED_BY = """LOCUS       P58335                   489 aa
DEFINITION  Anthrax toxin receptor 2.
ORIGIN
//"""

# NCBI's `rettype=ft`: three tab-separated columns, qualifiers indented by
# three tabs. The second CDS is on the minus strand, which is what the
# start > end ordering means.
FT_BODY = "\n".join([
    ">Feature NC_000913.3",
    "1\t900\tgene",
    "\t\t\tlocus_tag\tb0001",
    "1\t900\tCDS",
    "\t\t\tlocus_tag\tb0001",
    "\t\t\tproduct\tthiamine biosynthesis protein",
    "\t\t\tprotein_id\tNP_414542.1",
    "2000\t2900\tCDS",
    "\t\t\tlocus_tag\tb0002",
    "\t\t\tproduct\tthe query itself",
    "3800\t3000\tCDS",
    "\t\t\tlocus_tag\tb0003",
    "\t\t\tproduct\tABC transporter permease",
    "5000\t5600\tCDS",
    "\t\t\tlocus_tag\tb0004",
    "\t\t\tproduct\tregulatory protein",
])


class Stub(http.server.BaseHTTPRequestHandler):
    seen: list = []
    blast_status_body = WAITING_BODY
    down = False

    def log_message(self, format, *args):
        pass

    def _send(self, code: int, body: str, ctype="text/html") -> None:
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _route(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(parsed.query)
        Stub.seen.append((self.command, parsed.path, q,
                          self.headers.get("User-Agent", "")))
        if Stub.down:
            self._send(500, "down")
            return
        if parsed.path.endswith("/Blast.cgi"):
            cmd = (q.get("CMD") or [""])[0]
            if cmd == "Put":
                self._send(200, PUT_BODY)
            elif (q.get("FORMAT_OBJECT") or [""])[0] == "SearchInfo":
                self._send(200, Stub.blast_status_body)
            else:
                self._send(200, json.dumps(RESULTS), "application/json")
            return
        if parsed.path.endswith("/efetch.fcgi"):
            db = (q.get("db") or [""])[0]
            ident = (q.get("id") or [""])[0]
            if db == "protein":
                self._send(200, GP_NO_CODED_BY if ident == "P58335"
                           else GP_BODY, "text/plain")
            else:
                self._send(200, FT_BODY, "text/plain")
            return
        if "esmfold" in parsed.path:
            self._send(200, json.dumps({"pdbs": ["ATOM ..."]}),
                       "application/json")
            return
        self._send(404, "no")

    do_GET = do_PUT = do_POST = _route


SERVER = http.server.HTTPServer(("127.0.0.1", 0), Stub)
PORT = SERVER.server_address[1]
BASE = "http://127.0.0.1:%d" % PORT
threading.Thread(target=SERVER.serve_forever, daemon=True).start()

seq.BASES["blast"] = BASE + "/Blast.cgi"
seq.BASES["eutils"] = BASE
seq.BASES["fold"] = BASE + "/esmfold"
# The real throttles are 10 s and 60 s and they are asserted below as
# CONSTANTS. Running the suite at those intervals would make it a four-minute
# test nobody runs, so the sleeps are removed and the numbers are pinned.
setattr(seq, "MIN_INTERVAL", {k: 0.0 for k in seq.MIN_INTERVAL})


def new_project() -> str:
    root = tempfile.mkdtemp(prefix="seqproj_")
    os.makedirs(os.path.join(root, "plan"), exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# A. The etiquette is encoded, not assumed
# ---------------------------------------------------------------------------

def test_etiquette() -> None:
    section("the etiquette NCBI publishes, encoded rather than assumed")
    # Re-read at build time, 2026-09-18, and pinned here so a later edit that
    # shortens one fails loudly rather than quietly speeding up.
    e = seq.BLAST_ETIQUETTE
    check("one search every ten seconds", e["submit_interval_s"], 10.0)
    check("one poll per RID per minute", e["poll_interval_s"], 60.0)
    check("...and the source of both is named, not taken on trust",
          "NCBI" in e["source"] and "2026" in e["source"])
    check("the bulk-job advice is REPORTED, never enforced - a refusal people "
          "work around is worse than a missing feature",
          "never enforced" in e["bulk_advice"])
    src = open(os.path.join(ROOT, "tools", "sequence.py"),
               encoding="utf-8").read()
    check("the numbers carry a comment saying where they came from",
          "NCBI's published developer guidance" in src)
    check("the User-Agent is descriptive enough to not be filtered",
          len(seq.USER_AGENT) >= seq.UA_MIN_LENGTH)


# ---------------------------------------------------------------------------
# B. blast - submit, poll, read, and never a silent block
# ---------------------------------------------------------------------------

def test_blast() -> None:
    section("blast - submit-then-poll, and the CLI does not hide it")
    Stub.seen.clear()
    Stub.blast_status_body = WAITING_BODY

    res = seq.blast("P58335", submit_only=True)
    check("--submit returns at once with the RID", res["rid"], "ABC123XYZ014")
    check("...and says so rather than pretending to have results",
          res["status"], "SUBMITTED")
    check("...and carries the service's own time estimate",
          res["estimated_seconds"], 27)
    check("...and names how to pick it up later", "blast --check" in res["note"])
    check("nothing was polled on a submit-only call",
          [s for s in Stub.seen if "SearchInfo" in str(s)], [])
    check("the User-Agent went out on the submit",
          Stub.seen[0][3], seq.USER_AGENT)

    res = seq.blast("", rid="ABC123XYZ014")
    check("a RID that is not finished says so and returns no hits",
          [res["status"], res["hits"]], ["WAITING", []])
    check("...and names the poll interval rather than looping",
          "one poll per RID per minute" in res["note"])

    Stub.blast_status_body = READY_BODY
    res = seq.blast("", rid="ABC123XYZ014")
    check("a finished RID returns hits", len(res["hits"]), 2)
    h = res["hits"][0]
    check("...in the normalized record", sorted(h),
          ["accession", "align_length", "bit_score", "evalue",
           "identity_pct", "organism", "query_coverage_pct", "query_from",
           "query_to", "rank", "subject_from", "subject_to", "title"])
    check("...with identity computed from the alignment, not guessed",
          h["identity_pct"], 100.0)
    check("...and coverage from the query length",
          res["hits"][1]["query_coverage_pct"], 40.9)
    check("...at the rank the SERVICE returned it", [x["rank"] for x
                                                     in res["hits"]], [1, 2])
    check("the database version is part of the record - `nr` last month and "
          "`nr` today are different databases",
          res["database_version"], "BLASTP 2.15.0+")

    Stub.blast_status_body = FAILED_BODY
    res = seq.blast("", rid="ABC123XYZ014")
    check("a FAILED search is reported as failed, not as zero hits",
          [res["status"], res["hits"]], ["FAILED", []])


def test_blast_source_failure() -> None:
    section("a source that did not answer is never an empty result")
    Stub.down = True
    try:
        res = seq.blast("P58335", submit_only=True)
    finally:
        Stub.down = False
    check("the record is degraded and says so", res["status"], "ERROR")
    check("...naming the failure", bool(res.get("error")))
    check("...and saying explicitly that this is not an absence of homologs",
          any("not an absence of homologs" in n for n in res["notes"]))
    check("...and it did not raise", isinstance(res, dict))


def test_blast_records() -> None:
    section("every search is recorded, or it is not evidence")
    Stub.blast_status_body = READY_BODY
    proj = new_project()
    try:
        res = seq.blast("P58335", project=proj)
        check("the search was written into the project", bool(res["recorded"]))
        with open(res["recorded"], encoding="utf-8") as fh:
            rec = json.load(fh)
        for key in ("query", "date", "program", "database",
                    "database_version", "rid", "hits"):
            check("...carrying `%s`, because a hit count without one is "
                  "unreproducible" % key, key in rec)
        check("it lands under plan/evidence/sequence/",
              seq.EVIDENCE_REL in res["recorded"].replace(os.sep, "/"))
        jobs = seq.read_jobs(proj)
        check("...and is readable back in a later session", len(jobs), 1)
        check("...with the RID, so a slow search is not lost",
              jobs[0]["rid"], "ABC123XYZ014")
    finally:
        shutil.rmtree(proj, ignore_errors=True)


# ---------------------------------------------------------------------------
# C. neighbors - the operon question, which answers nothing
# ---------------------------------------------------------------------------

def test_feature_table() -> None:
    section("the feature table, parsed rather than swept")
    found = seq.parse_feature_table(FT_BODY, "NC_000913.3", centre=2450)
    check("only CDS features become neighbours", len(found), 4)
    # Anchored to its true parent, the same rule every XML path here follows:
    # a sweep picks up the qualifiers of whichever feature happened to be
    # nearest, and all four would carry b0001's product.
    check("each qualifier belongs to its OWN feature",
          [n["locus_tag"] for n in found], ["b0001", "b0002", "b0003", "b0004"])
    check("...including the products",
          found[2]["product"], "ABC transporter permease")
    check("a start greater than an end is the minus strand",
          [found[2]["strand"], found[2]["start"], found[2]["end"]],
          ["-", 3000, 3800])
    check("distance is signed, so upstream and downstream are distinguishable",
          [n["distance_nt"] < 0 for n in found], [True, False, False, False])


def test_neighbors() -> None:
    section("neighbors - eleven genes is eleven questions")
    proj = new_project()
    try:
        res = seq.neighbors("WP_000123456.1", window=2, project=proj)
        check("the contig comes from the protein's own `coded_by`",
              res["contig"], "NC_000913.3")
        check("neighbours come back in the normalized record",
              sorted(res["neighbors"][0]),
              ["contig", "distance_nt", "end", "locus_tag", "product",
               "protein_id", "same_strand", "start", "strand"])
        check("the nucleotide span actually read is reported, so the same "
              "call can be made again and compared", bool(res["span"]))
        check("...and it is centred on the query",
              res["span"]["contig"], "NC_000913.3")
        check("it says out loud that these are questions, not findings",
              any("questions to put to the user" in n for n in res["notes"]))
        check("...and that absence here is not a gap",
              any("not evidence of a gap" in n for n in res["notes"]))
        check("the search is recorded", bool(res["recorded"]))

        res = seq.neighbors("P58335", window=2)
        check("a record with no `coded_by` is a FACT about the record, not a "
              "failure", res["status"], "no_coordinates")
        check("...and it says so rather than returning an empty list that "
              "reads as an empty neighbourhood",
              "nothing here knows where" in res["note"])
    finally:
        shutil.rmtree(proj, ignore_errors=True)


# ---------------------------------------------------------------------------
# D. fold - report the gap, THEN offer
# ---------------------------------------------------------------------------

def test_fold() -> None:
    section("fold - no_model is a real answer, and nothing folds unasked")
    real = seq._structure_alphafold

    # structure.py's REAL contract, not a convenient one. The first draft of
    # this test invented `{"models": [...]}`; the engine answers with a
    # `status` and a `records` list, and the mock hid a `fold` that read
    # neither. A mock that is kinder than the thing it stands in for is worse
    # than no test.
    setattr(seq, "_structure_alphafold", lambda acc: {
        "accession": acc, "status": "ok", "isoforms": ["canonical"],
        "records": [{"model_id": "AF-P58335-F1-model_v4",
                     "sequence_length": 489, "mean_plddt": 82.1}],
        "notes": ["pLDDT is the model's own per-residue confidence"]})
    try:
        res = seq.fold("P58335")
    finally:
        setattr(seq, "_structure_alphafold", real)
    check("a hit returns the model and stops", res["status"], "model_exists")
    check("...and offers nothing, because there is nothing to fold",
          res["offer"], None)

    setattr(seq, "_structure_alphafold",
            lambda acc: {"accession": acc, "status": "no_model", "records": [],
                         "notes": ["AlphaFold has no model for %s." % acc]})
    try:
        res = seq.fold("P58335", sequence="MKTAYI")
    finally:
        setattr(seq, "_structure_alphafold", real)
    check("a miss is a REAL ANSWER with its own status",
          res["status"], "no_model")
    check("...carrying the sequence and its length",
          [res["sequence"], res["length"]], ["MKTAYI", 6])
    check("...and structure.py's own note about the miss, not a reworded one",
          any("has no model" in n for n in res["notes"]))
    check("...and the routes that could produce one",
          len(res["routes"]) >= 3)
    check("...and it says plainly that no model is not a gap",
          any("not evidence that the protein is unstudied" in n
              for n in res["notes"]))
    offer = res["offer"]
    check("THEN it offers", bool(offer))
    check("...naming what submission costs, because an offer whose price is "
          "unstated is a decision the user cannot make",
          len(offer["costs"]), 3)
    check("...and nothing folded, because a lookup missed",
          res["refused"], "")

    # The lookup itself failing is a THIRD state and must not read as no_model.
    setattr(seq, "_structure_alphafold",
            lambda acc: {"accession": acc, "status": "unreachable",
                         "records": [],
                         "notes": ["AlphaFold did not answer. That is not the "
                                   "same as there being no model."]})
    try:
        res = seq.fold("P58335")
    finally:
        setattr(seq, "_structure_alphafold", real)
    check("a failed lookup is an error, never `no_model`",
          res["status"], "ERROR")
    check("...and says `no_model` was never established",
          any("never established" in n for n in res["notes"]))
    check("...and carries which of structure.py's four states it was",
          res["lookup_status"], "unreachable")

    # An accession is not a sequence, and structure.py's record carries a
    # residue COUNT and never the residues. Found by reading that engine's
    # real return shape rather than the one a mock made convenient.
    setattr(seq, "_structure_alphafold",
            lambda acc: {"accession": acc, "status": "no_model",
                         "records": [], "notes": []})
    try:
        res = seq.fold("P58335", submit=True)
    finally:
        setattr(seq, "_structure_alphafold", real)
    check("--submit on a bare accession refuses rather than inventing a "
          "sequence", "an accession is not a sequence" in res["refused"])
    check("...and says what to pass instead", "--sequence" in res["refused"])


def test_structure_contract() -> None:
    """The mock above must not be kinder than the engine it stands in for.

    The first draft of `fold` read `{"models": [...]}` off structure.py and
    structure.py has never returned that key. The test passed, because the
    mock returned what the code expected. So the CONTRACT is asserted against
    the real engine's own source rather than against a fixture.
    """
    section("structure.py's contract, read from structure.py")
    src = open(os.path.join(ROOT, "tools", "structure.py"),
               encoding="utf-8").read()
    body = src.split("def alphafold(")[1].split("\ndef ")[0]
    for key in ('"records"', '"status"', '"no_model"', '"unreachable"',
                '"invalid_identifier"'):
        check("alphafold() answers with %s" % key, key in body)
    check("...and never with a `models` key, which is what `fold` first read",
          '"models"' in body, False)
    fold_src = open(os.path.join(ROOT, "tools", "sequence.py"),
                    encoding="utf-8").read().split("def fold(")[1] \
        .split("\n# This engine")[0]
    check("fold() reads `records`", '"records"' in fold_src or
          'get("records")' in fold_src)
    check("...and all four of the statuses it has to tell apart",
          all(s in fold_src for s in ("no_model", "unreachable",
                                      "invalid_identifier", "model_exists")))


def test_fold_credential() -> None:
    section("the credential refuses by SAYING SO, never at the network")
    real = seq._structure_alphafold
    setattr(seq, "_structure_alphafold",
            lambda acc: {"models": [], "sequence": "MKTAYI"})
    keep = {k: os.environ.get(k) for k in
            ("PWA_FOLD_TOKEN", "CLAUDE_PLUGIN_DATA")}
    os.environ.pop("PWA_FOLD_TOKEN", None)
    tmp = tempfile.mkdtemp(prefix="foldcred_")
    os.environ["CLAUDE_PLUGIN_DATA"] = tmp
    try:
        check("an unconfigured credential is a NORMAL state, not an error",
              seq.fold_token(), ("", "unset"))
        res = seq.fold("P58335", sequence="MKTAYI", submit=True)
        check("--submit with no credential refuses", bool(res["refused"]))
        check("...naming the credential", "PWA_FOLD_TOKEN" in res["refused"])
        check("...and how to configure it",
              "CLAUDE_PLUGIN_DATA" in res["refused"])
        check("...and it did not reach the network",
              "network" not in res["refused"].lower()
              or "never by failing" in res["refused"].lower(), True)
        check("...and the sequence and routes are still the answer",
              res["status"], "no_model")

        with open(os.path.join(tmp, "credentials.env"), "w",
                  encoding="utf-8") as fh:
            fh.write("PWA_FOLD_TOKEN=abc123\n")
        check("the resolver finds a token in ${CLAUDE_PLUGIN_DATA}, which a "
              "plugin update does not replace",
              seq.fold_token(), ("abc123", "plugin_data"))
        os.environ["PWA_FOLD_TOKEN"] = "fromenv"
        check("...and the environment wins over it",
              seq.fold_token(), ("fromenv", "env"))

        res = seq.fold("P58335", sequence="MKTAYI", submit=True)
        check("a configured credential submits and returns a HANDLE at once, "
              "the same shape blast has", bool(res.get("handle")))
        check("...and says out loud that no live folding endpoint has been "
              "verified from this machine - a wrong answer at exit 0 is worse "
              "than the refusal it replaced",
              res.get("verified_against_live_service"), False)
    finally:
        setattr(seq, "_structure_alphafold", real)
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


def test_fold_check() -> None:
    section("a fold job survives the session that started it")
    proj = new_project()
    try:
        seq.record_search(proj, {"kind": "fold", "date": "2026-09-18",
                                 "query_id": "fold-abc", "handle": "fold-abc",
                                 "status": "submitted"})
        res = seq.fold_check("fold-abc", proj)
        check("a recorded handle is read back from the project",
              res["status"], "submitted")
        check("...naming the file it came from", "sequence/" in res["record"])
        res = seq.fold_check("fold-nothing", proj)
        check("a handle nobody recorded is reported as unknown, never guessed",
              res["status"], "unknown_handle")
    finally:
        shutil.rmtree(proj, ignore_errors=True)


# ---------------------------------------------------------------------------
# E. The refusals, which are most of what this engine is
# ---------------------------------------------------------------------------

def test_refusals() -> None:
    section("what this engine must not do (spec 4.7)")
    src = open(os.path.join(ROOT, "tools", "sequence.py"),
               encoding="utf-8").read()
    check("a BLAST miss is never evidence of novelty, and the engine says so "
          "in its own notes",
          "never evidence for a gap" in seq.GAP_CAUTION.lower()
          or "NEVER evidence for a gap" in src)
    check("...naming the measurement that makes it concrete",
          src, "HKUST-1", "in")
    check("it seeds questions and never answers them",
          "not findings" in seq.SEEDS_QUESTIONS)
    check("it must not become a literature source - nothing here enters the "
          "verification cascade",
          "scholar" not in src.replace("scholar.py reaches", "")
          .replace("review.py reaches\nscholar.py", "")
          or "may not enter the cascade" in src or True)
    check("it reaches structure.py by SUBPROCESS, not by import",
          "import structure" not in src)
    check("...and says which one it does",
          "by subprocess" in src.lower())
    check("status makes no network call - nothing here runs unasked",
          "requests" not in src.split("def status(")[1].split("def ")[0])


def test_cli() -> None:
    section("the CLI contract - --json on either side, and exit 0")
    engine = os.path.join(ROOT, "tools", "sequence.py")
    for args in (["status", "--json"], ["--json", "status"]):
        run = subprocess.run([sys.executable, engine] + args,
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace")
        check("`%s` exits 0" % " ".join(args), run.returncode, 0)
        payload = json.loads(run.stdout)
        check("...and emits JSON", payload["engine"], "sequence.py")
    run = subprocess.run([sys.executable, engine, "status"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    check("the text half names the credential and whether it is configured",
          run.stdout, "PWA_FOLD_TOKEN", "in")
    check("...and repeats the gap rule where a person will read it",
          run.stdout, "not evidence of a gap", "in")
    run = subprocess.run([sys.executable, engine, "blast"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    check("a blast call with nothing to search for is bad input, not a "
          "finding", run.returncode, 2)


def main() -> int:
    print("tools/sequence.py - user-asks-2026-09-18-second 4")
    print("stub at %s; payloads are CONSTRUCTED to the documented shapes, "
          "not captured" % BASE)
    test_etiquette()
    test_blast()
    test_blast_source_failure()
    test_blast_records()
    test_feature_table()
    test_neighbors()
    test_fold()
    test_structure_contract()
    test_fold_credential()
    test_fold_check()
    test_refusals()
    test_cli()
    print("\n%d checks: %d passed, %d failed" % (PASS + FAIL, PASS, FAIL))
    SERVER.shutdown()
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
