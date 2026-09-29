#!/usr/bin/env python3
"""Measure tools/docx_edits.py against real .docx packages: does a marked-up
Word file round-trip to the insertions, deletions, comments and authors that
were actually put in it?

Fifth suite, alongside reliability.py (citation verification), fulltext.py
(tiered reading), scaffold.py (the project structure) and idea.py (the idea
bundle). This one covers specs/writing-engine.md 11, and its acceptance
criterion is step 4's: a fixture .docx round-trips insertions, deletions,
comments, and authors.

The fixtures in tests/fixtures/ are committed and are built by
tests/make_docx_fixtures.py. This suite also rebuilds them into a temp
directory and compares bytes, so a committed fixture cannot drift from the
builder that documents it.

Run:  python tests/docx_edits.py
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

for _stream in (sys.stdout, sys.stderr):
    # A redirected stream (a pipe, a StringIO under a harness) has no
    # reconfigure at all; asking for it by name keeps that case quiet.
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except ValueError:
            pass

# This file and the engine share a name, so `import docx_edits` would resolve by
# sys.path order - and inside this file, to this file. Same trap as
# tests/scaffold.py and tests/idea.py; same fix.
_HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(_HERE, "..", "tools")
FIXTURES = os.path.join(_HERE, "fixtures")
ENGINE = os.path.join(TOOLS, "docx_edits.py")

TRACKED = os.path.join(FIXTURES, "tracked_two_authors.docx")
FIELDS = os.path.join(FIXTURES, "field_codes.docx")
CLEAN = os.path.join(FIXTURES, "clean.docx")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dx = _load("_docx_edits_engine", ENGINE)
mk = _load("_docx_fixture_builder", os.path.join(_HERE, "make_docx_fixtures.py"))

PASSED = 0
FAILED = 0
SKIPPED = 0


def check(label, passed, detail: object = ""):
    global PASSED, FAILED
    if passed:
        PASSED += 1
    else:
        FAILED += 1
    print(f"[{'pass' if passed else 'FAIL'}] {label}")
    if detail:
        for line in str(detail).rstrip().splitlines():
            print(f"         {line}")
    return passed


def skip(label, why):
    global SKIPPED
    SKIPPED += 1
    print(f"[skip] {label}")
    print(f"         {why}")


def section(title):
    print(f"\n--- {title} " + "-" * max(0, 58 - len(title)))


def run(*args):
    """Invoke the engine as the CLI a skill would call."""
    proc = subprocess.run([sys.executable, ENGINE, *args],
                          capture_output=True, text=True, encoding="utf-8")
    return proc.returncode, proc.stdout, proc.stderr


def texts(entries):
    return [e["text"] for e in entries]


# ---------------------------------------------------------------------------

def test_fixtures_are_real_docx():
    section("the fixtures are real Word packages")

    for path in (TRACKED, FIELDS, CLEAN):
        check(f"{os.path.basename(path)} exists and is a zip",
              os.path.isfile(path) and zipfile.is_zipfile(path))

    with zipfile.ZipFile(TRACKED) as z:
        names = set(z.namelist())
    for member in ("[Content_Types].xml", "_rels/.rels", "word/document.xml",
                   "word/_rels/document.xml.rels", "word/comments.xml",
                   "word/commentsExtended.xml"):
        check(f"tracked fixture carries {member}", member in names)

    with zipfile.ZipFile(CLEAN) as z:
        check("clean fixture has no comments part at all",
              "word/comments.xml" not in set(z.namelist()))

    # An independent reader is the real proof the package is well-formed: our
    # own parser could be wrong in the same direction as our own writer.
    try:
        import docx  # python-docx
    except ImportError:
        skip("python-docx opens the fixtures",
             "python-docx not importable in this interpreter")
    else:
        for path, n in ((TRACKED, 7), (FIELDS, 3), (CLEAN, 2)):
            doc = docx.Document(path)
            check(f"python-docx opens {os.path.basename(path)} "
                  f"and sees {n} paragraphs",
                  len(doc.paragraphs) == n, f"saw {len(doc.paragraphs)}")

    # The committed fixture must be exactly what the builder makes, or the
    # builder stops being the document of record for what is being tested.
    # The comparison is part by part, not byte by byte: the zip header records
    # the OS that wrote it, and the deflate stream differs between zlib builds
    # (Python 3.14 on Windows ships zlib-ng), so the same build is never
    # byte-identical across machines. The parts themselves always are.
    def parts(path):
        with zipfile.ZipFile(path) as z:
            return [(i.filename, z.read(i)) for i in z.infolist()]

    tmp = tempfile.mkdtemp(prefix="dxfix_")
    try:
        mk.build(tmp)
        for name in ("tracked_two_authors.docx", "field_codes.docx", "clean.docx"):
            fresh = parts(os.path.join(tmp, name))
            committed = parts(os.path.join(FIXTURES, name))
            differ = [n for (n, a), (_, b) in zip(fresh, committed) if a != b]
            check(f"committed {name} has the same parts as a fresh build",
                  fresh == committed,
                  f"parts differ: {differ}" if differ else
                  f"part lists differ: {[n for n, _ in fresh]} vs "
                  f"{[n for n, _ in committed]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_round_trip():
    section("tracked changes round-trip with their authors")
    res = dx.extract(TRACKED)

    check("both authors reported, and only those two",
          res["authors"] == ["G Jensen", "J Vale"], res["authors"])
    check("file is the basename, path is absolute",
          res["file"] == "tracked_two_authors.docx"
          and os.path.isabs(res["path"]))

    ins, dele = res["insertions"], res["deletions"]
    check("5 insertions found", len(ins) == 5, texts(ins))
    check("4 deletions found", len(dele) == 4, texts(dele))

    check("the inserted sentence comes back whole",
          "Cryo-electron tomography has resolved this lattice in situ. "
          in texts(ins))
    check("the hedge swap comes back as del 'may indicate' + ins 'indicates'",
          "may indicate" in texts(dele) and "indicates" in texts(ins))
    check("the deleted trend sentence comes back whole",
          "We observed a trend toward significance (p = 0.07). " in texts(dele))

    by_author = {}
    for r in ins + dele:
        by_author.setdefault(r["author"], []).append(r["text"])
    check("J Vale's edits are attributed to J Vale",
          "indicates" in by_author.get("J Vale", []))
    check("G Jensen's edits are attributed to G Jensen",
          " (Figure 2B)" in by_author.get("G Jensen", []))
    check("no edit is attributed to the wrong author",
          "indicates" not in by_author.get("G Jensen", [])
          and " (Figure 2B)" not in by_author.get("J Vale", []))

    check("dates survive",
          all(r["date"].startswith("2026-08-1") for r in ins + dele),
          [r["date"] for r in ins + dele])
    check("every revision carries a paragraph index",
          all(isinstance(r["paragraph"], int) for r in ins + dele))


def test_the_slash_slash_trap():
    section("the //w:ins trap: property subtrees are not body text")
    res = dx.extract(TRACKED)
    revisions = res["insertions"] + res["deletions"]

    check("no textless revision is reported",
          all(r["text"].strip() for r in revisions),
          [r for r in revisions if not r["text"].strip()])
    check("the inserted paragraph MARK (w:pPr/w:rPr/w:ins) is not an insertion",
          len(res["insertions"]) == 5, texts(res["insertions"]))
    check("the w:rPrChange is counted as formatting, not as text",
          res["formatting_changes"] == 1, res["formatting_changes"])
    check("the italic run under the rPrChange is not reported as changed text",
          not any("Arrays were imaged" in r["text"] for r in revisions))
    check("a note says formatting-only revisions exist",
          any("formatting-only" in n for n in res["notes"]), res["notes"])


def test_nested_revision():
    section("a deletion nested inside an insertion")
    res = dx.extract(TRACKED)

    nested = [r for r in res["insertions"] + res["deletions"] if r["nested"]]
    check("exactly one nested revision", len(nested) == 1, nested)
    check("it is classified as the deletion, by the author who deleted it",
          nested and nested[0]["text"] == "entirely "
          and nested[0]["author"] == "G Jensen", nested)
    check("it is in the deletions list, not the insertions list",
          "entirely " in texts(res["deletions"])
          and "entirely " not in texts(res["insertions"]))

    accepted = dx.plain(TRACKED, "accept")
    rejected = dx.plain(TRACKED, "reject")
    check("accepted text: inserted-then-deleted run is gone",
          "entirely" not in accepted)
    check("rejected text: inserted-then-deleted run is gone too",
          "entirely" not in rejected)
    check("accepted text keeps the surviving insertion",
          "remains poorly understood" in accepted)
    check("rejected text drops the whole insertion",
          "remains understood" in rejected)
    check("a note explains the nested run",
          any("neither the accepted nor the rejected" in n for n in res["notes"]),
          res["notes"])


def test_moves():
    section("moveFrom / moveTo")
    res = dx.extract(TRACKED)
    moved = [r for r in res["insertions"] + res["deletions"] if r["moved"]]
    sentence = "Tilt series were collected from -60 to +60 degrees. "

    check("a move is reported as one deletion and one insertion",
          len(moved) == 2
          and sentence in texts(res["insertions"])
          and sentence in texts(res["deletions"]), moved)
    check("moved text is flagged as moved, not as a plain rewrite",
          all(r["moved"] for r in moved))

    accepted = dx.plain(TRACKED, "accept")
    rejected = dx.plain(TRACKED, "reject")
    check("accepted text has the moved sentence exactly once, at its destination",
          accepted.count(sentence.strip()) == 1
          and "degrees. Alignment used gold fiducials" in accepted)
    check("rejected text has it exactly once, at its origin",
          rejected.count(sentence.strip()) == 1
          and "degrees. Data were processed in IMOD" in rejected)


def test_context():
    section("context: the sentence an edit sits in")
    res = dx.extract(TRACKED)
    by_text = {r["text"]: r for r in res["insertions"] + res["deletions"]}

    check("a deletion's context is the original sentence around it",
          by_text["may indicate"]["context"]
          == "The array may indicate a cooperative signalling mechanism.",
          by_text["may indicate"]["context"])
    check("an insertion's context anchors to the sentence it was typed into",
          "cooperative signalling mechanism"
          in by_text["Cryo-electron tomography has resolved this lattice in situ. "]["context"])
    check("context is the ORIGINAL text, so it matches source_text/ markdown",
          "indicates" not in by_text["may indicate"]["context"])

    spans = dx._sentence_spans(
        "Arrays are hexagonal (Briegel et al. 2009). The array indicates order.")
    check("'et al.' does not split a sentence", len(spans) == 2, spans)

    spans = dx._sentence_spans("Imaged at 300 kV. Aligned in IMOD. Done.")
    check("ordinary sentences do split", len(spans) == 3, spans)

    spans = dx._sentence_spans("R. W. Smith reported the same effect. We agree.")
    check("initials do not split a sentence", len(spans) == 2, spans)

    fields = dx.extract(FIELDS)
    check("a wholly new paragraph still gets a context",
          fields["insertions"][0]["context"].strip() != "",
          fields["insertions"][0]["context"])


def test_comments():
    section("comments, anchors, and resolved state")
    res = dx.extract(TRACKED)
    comments = res["comments"]

    check("both comments found", len(comments) == 2, comments)
    by_author = {c["author"]: c for c in comments}
    check("comment authors and initials survive",
          set(by_author) == {"G Jensen", "J Vale"}
          and by_author["G Jensen"]["initials"] == "GJ"
          and by_author["J Vale"]["initials"] == "JV",
          [(c["author"], c["initials"]) for c in comments])
    check("comment text comes back whole",
          by_author["G Jensen"]["text"]
          == "A p of 0.07 is not a trend. Cut this or report it as null.",
          by_author["G Jensen"]["text"])
    check("a comment anchored over deleted text still reports its anchor",
          "trend toward significance" in by_author["G Jensen"]["anchor"],
          by_author["G Jensen"]["anchor"])
    check("an anchor spanning a revision shows what the commenter saw",
          "entirely poorly" in by_author["J Vale"]["anchor"],
          by_author["J Vale"]["anchor"])
    check("commentsExtended's done flag is read: JV's is resolved",
          by_author["J Vale"]["resolved"] is True)
    check("and GJ's is not",
          by_author["G Jensen"]["resolved"] is False)
    check("comments carry their id and paragraph, for the ledger",
          all(c["id"] is not None and c["paragraph"] is not None
              for c in comments), comments)
    check("comment authors are in the authors list",
          "G Jensen" in res["authors"] and "J Vale" in res["authors"])


def test_field_codes():
    section("reference-manager field codes")
    fields = dx.extract(FIELDS)
    tracked = dx.extract(TRACKED)
    clean = dx.extract(CLEAN)

    check("field codes are detected", fields["field_codes_present"] is True)
    check("both managers are named",
          fields["citation_managers"] == ["EndNote", "Zotero"],
          fields["citation_managers"])
    check("CSL_CITATION is not double-reported next to Zotero",
          "CSL" not in fields["citation_managers"])
    check("a note says citations read as plain text",
          any("citations read as plain text" in n for n in fields["notes"]),
          fields["notes"])
    check("a file without field codes is not flagged",
          tracked["field_codes_present"] is False
          and clean["field_codes_present"] is False)

    text = dx.plain(FIELDS, "accept")
    check("the field INSTRUCTION never leaks into the text",
          "ADDIN" not in text and "EndNote>" not in text, text)
    check("the field RESULT does appear, as plain text",
          "(1)" in text and "(2)" in text, text)


def test_clean_and_errors():
    section("a clean file, and files that are not this")
    res = dx.extract(CLEAN)

    check("no changes gives empty lists, not an error",
          res["insertions"] == [] and res["deletions"] == []
          and res["comments"] == [])
    check("and no authors", res["authors"] == [])
    check("and says so in a note",
          any("no tracked changes and no comments" in n for n in res["notes"]),
          res["notes"])
    check("a missing comments part is not an error",
          res["comments"] == [] and res["paragraphs"] == 2)

    tmp = tempfile.mkdtemp(prefix="dxerr_")
    try:
        not_a_zip = os.path.join(tmp, "notes.docx")
        with open(not_a_zip, "w", encoding="utf-8") as fh:
            fh.write("I renamed a text file and hoped for the best.")
        code, _out, err = run("extract", not_a_zip)
        check("a non-zip .docx exits 2 with a plain message",
              code == 2 and "not a .docx" in err, err.strip())

        zip_not_docx = os.path.join(tmp, "figures.docx")
        with zipfile.ZipFile(zip_not_docx, "w") as z:
            z.writestr("fig1.png", b"not really a png")
        code, _out, err = run("extract", zip_not_docx)
        check("a zip that is not a Word file exits 2 and says why",
              code == 2 and "no word/document.xml" in err, err.strip())

        code, _out, err = run("extract", os.path.join(tmp, "absent.docx"))
        check("a missing file exits 2 and names it",
              code == 2 and "no such file" in err, err.strip())

        # OneDrive drops sync-conflict copies straight into edits/. Reading one
        # is reading a coauthor's markup twice.
        conflict = os.path.join(tmp, "manuscript_r5_JV-DESKTOP-A4B6PBU.docx")
        shutil.copyfile(TRACKED, conflict)
        res = dx.extract(conflict)
        check("a OneDrive conflict-copy filename is called out in the notes",
              any("sync conflict copy" in n for n in res["notes"]), res["notes"])
        check("but it is still parsed, rather than refused",
              len(res["insertions"]) == 5)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cli():
    section("CLI conventions, as a skill will call it")

    code, out, err = run("extract", TRACKED, "--json")
    check("extract --json exits 0", code == 0, err)
    trailing = json.loads(out)
    check("--json after the subcommand parses as JSON",
          trailing["file"] == "tracked_two_authors.docx")

    code, out, err = run("--json", "extract", TRACKED)
    leading = json.loads(out) if code == 0 else None
    check("--json BEFORE the subcommand works too (the pubmed.py gotcha)",
          code == 0 and leading == trailing, err)

    code, out, err = run("extract", TRACKED)
    check("text output names both authors and counts the edits",
          code == 0 and "G Jensen" in out and "J Vale" in out
          and "5 insertions, 4 deletions" in out, out[:200])
    check("text output singularises a count of one",
          "1 formatting change\n" in out or "1 formatting change," in out
          or "1 formatting change" in out.split("\n")[2], out.split("\n")[2])

    code, out, err = run("plain", TRACKED, "--reject")
    check("plain --reject exits 0 and gives the pre-edit text",
          code == 0 and "may indicate" in out, err)
    code, out, err = run("plain", TRACKED, "--accept")
    check("plain --accept gives the post-edit text",
          code == 0 and "indicates a cooperative" in out, err)
    code, out, err = run("plain", TRACKED)
    check("plain defaults to --accept",
          code == 0 and "indicates a cooperative" in out, err)

    code, out, err = run("plain", TRACKED, "--accept", "--reject")
    check("--accept and --reject together are refused, not silently ordered",
          code == 2, out or err)

    code, out, err = run("--json", "plain", TRACKED, "--reject")
    payload = json.loads(out)
    check("plain --json carries the mode it used",
          payload["mode"] == "reject" and "may indicate" in payload["text"])


def test_json_shape():
    section("the JSON shape specs/writing-engine.md 11 promises")
    res = dx.extract(TRACKED)

    for key in ("file", "authors", "insertions", "deletions", "comments",
                "field_codes_present", "notes"):
        check(f"top-level key '{key}' present", key in res)
    for key in ("author", "date", "context", "text"):
        check(f"every insertion carries '{key}'",
              all(key in r for r in res["insertions"]))
        check(f"every deletion carries '{key}'",
              all(key in r for r in res["deletions"]))
    for key in ("author", "anchor", "text", "resolved"):
        check(f"every comment carries '{key}'",
              all(key in c for c in res["comments"]))
    check("the whole payload is JSON-serialisable",
          isinstance(json.dumps(res), str))


# ---------------------------------------------------------------------------

def main():
    print("docx_edits engine suite - specs/writing-engine.md 11")

    if not os.path.isdir(FIXTURES):
        print(f"\nfixtures missing: {FIXTURES}\n"
              f"build them with: python tests/make_docx_fixtures.py")
        return 1

    test_fixtures_are_real_docx()
    test_round_trip()
    test_the_slash_slash_trap()
    test_nested_revision()
    test_moves()
    test_context()
    test_comments()
    test_field_codes()
    test_clean_and_errors()
    test_cli()
    test_json_shape()

    print(f"\n{PASSED}/{PASSED + FAILED} passed"
          + (f", {FAILED} FAILED" if FAILED else "")
          + (f", {SKIPPED} skipped" if SKIPPED else ""))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
