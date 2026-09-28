#!/usr/bin/env python3
"""
tests/cite.py - measure the citation writers in tools/pubmed.py.

Offline for the formatting itself; the `--render` half drives real pandoc over
a written .bib, which is the only thing that can answer "does BibTeX read the
author the way we meant it". That distinction is the reason this suite exists:
the .bib looked fine to read and the rendered document said `(N and S 2012)`.

  python tests/cite.py
  python tests/cite.py -v
  python tests/cite.py --render     # + the pandoc round trip
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile

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
ROOT = os.path.dirname(HERE)

# Loaded by path under a distinct module name, the same way every other suite
# here loads its engine. Do not "simplify" this back.
_spec = importlib.util.spec_from_file_location(
    "pubmed_engine", os.path.join(ROOT, "tools", "pubmed.py"))
if _spec is None or _spec.loader is None:
    raise ImportError("cannot load tools/pubmed.py")
pubmed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pubmed)

VERBOSE = "-v" in sys.argv
RENDER = "--render" in sys.argv
PASS = FAIL = SKIP = 0


def _spit(path, data, mode="w", **kw):
    """Write a whole file and close it - same reason as _slurp()."""
    with open(path, mode, **kw) as fh:
        return fh.write(data)


def check(name: str, got, want, note: str = "") -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
        if VERBOSE:
            print(f"  ok    {name}  = {got!r}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}\n          got  {got!r}\n          want {want!r}"
              + (f"\n          {note}" if note else ""))


def skip(name: str, why: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  skip  {name}  ({why})")


def section(title: str) -> None:
    print(f"\n{title}\n" + "-" * len(title))


# The record that produced the bug, field for field as PubMed returns it.
STOCK = {
    "pmid": "22098087",
    "title": "Synthesis of metal-organic frameworks (MOFs): routes to various "
             "MOF topologies, morphologies, and composites",
    "authors": ["Stock N", "Biswas S"],
    "journal": "Chemical reviews",
    "journal_abbrev": "Chem Rev",
    "year": "2012",
    "volume": "112",
    "issue": "2",
    "pages": "933-69",
    "doi": "10.1021/cr200304e",
}


def field(entry: str, key: str) -> str:
    m = re.search(r"^  %s = \{(.*)\}[,]?$" % key, entry, re.M)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------

def test_author_names() -> None:
    section("PubMed's `Surname Initials`, in the shape BibTeX parses")

    # The measured failure: `Stock N` read as given-name Stock, surname N.
    check("one initial", pubmed._bibtex_name("Stock N"), "Stock, N.")
    check("two", pubmed._bibtex_name("Smith JD"), "Smith, J. D.")
    check("three", pubmed._bibtex_name("Ho JKL"), "Ho, J. K. L.")
    check("a particle stays with the surname",
          pubmed._bibtex_name("van der Berg JD"), "van der Berg, J. D.",
          "BibTeX reads `van der` as the von part, which is correct")

    # A name that does not match the shape is braced, not guessed at: the
    # braces are how BibTeX is told "one literal name, no comma to find".
    check("a collective name is braced",
          pubmed._bibtex_name("World Health Organization"),
          "{World Health Organization}")
    check("a bare surname is braced", pubmed._bibtex_name("Ng"), "{Ng}")
    check("an empty name stays empty", pubmed._bibtex_name(""), "")

    entry = pubmed.format_citation(STOCK, "bibtex")
    check("both authors, joined the way BibTeX wants",
          field(entry, "author"), "Stock, N. and Biswas, S.",
          "the build rendered `(N and S 2012)` from `Stock N and Biswas S`")


def test_tex_escapes() -> None:
    section("A .bib that is ASCII and still says the same thing")

    check("an umlaut", pubmed._tex_escape("Gröger"), r"Gr{\"o}ger")
    check("a caron", pubmed._tex_escape("Řežáč"),
          r"{\v R}e{\v z}{\'a}{\v c}")
    check("a cedilla", pubmed._tex_escape("François"), r"Fran{\c c}ois")
    check("an acute over an i loses the dot with it",
          pubmed._tex_escape("García"), r"Garc{\'\i}a")
    check("a letter with no accent is named",
          pubmed._tex_escape("Małecki"), r"Ma{\l}ecki")
    check("...and so is eszett", pubmed._tex_escape("Weiß"), r"Wei{\ss}")
    check("ASCII is left alone", pubmed._tex_escape("Hoffmann M"),
          "Hoffmann M")

    # Measured through pandoc: `$^\circ$` renders as `^(∘)`, `\textdegree`
    # renders as the degree sign. The table only holds escapes that survive.
    check("the degree sign is text, not math",
          pubmed._tex_escape("25 °C"), r"25 \textdegree{}C")
    check("a Greek letter is math, which does survive",
          pubmed._tex_escape("α-phase"), r"$\alpha$-phase")
    check("an en dash is two hyphens",
          pubmed._tex_escape("933–969"), "933--969")

    # The one case that stays raw. Dropping a letter out of an author's name
    # is worse than a wide byte, and `{\dh}` drops it: pandoc renders that
    # macro as nothing at all.
    check("a letter with no escape that works is kept as itself",
          pubmed._tex_escape("ð"), "ð")

    entry = pubmed.format_citation(
        dict(STOCK, authors=["Gröger H", "Małecki JW"],
             title="α-phase at 25 °C"), "bibtex")
    check("the whole entry is ASCII", entry.isascii(), True,
          "a raw wide byte in a .bib is a live corruption risk on Windows")


def test_page_ranges() -> None:
    section("The closing page PubMed abbreviated, written out")

    check("933-69 is 933 to 969", pubmed._full_page_range("933-69"),
          "933--969", "ACS wants the full range")
    check("1234-8 is 1234 to 1238", pubmed._full_page_range("1234-8"),
          "1234--1238")
    check("a full range only gains the en dash",
          pubmed._full_page_range("211-230"), "211--230")
    check("a single page is left alone", pubmed._full_page_range("933"), "933")
    check("an electronic locator is left alone",
          pubmed._full_page_range("e1234"), "e1234")
    check("a prefixed range is not second-guessed",
          pubmed._full_page_range("S12-S20"), "S12--S20")
    check("nothing stays nothing", pubmed._full_page_range(""), "")

    entry = pubmed.format_citation(STOCK, "bibtex")
    check("the entry carries the full range", field(entry, "pages"),
          "933--969")


# Full title as PubMed returns it, the ISOAbbreviation beside it, and the
# CASSI form Langmuir's checklist asks for.
CASSI_CASES = [
    ("Chemical reviews", "Chem Rev", "Chem. Rev."),
    ("Chemical Society reviews", "Chem Soc Rev", "Chem. Soc. Rev."),
    ("Journal of the American Chemical Society", "J Am Chem Soc",
     "J. Am. Chem. Soc."),
    ("The journal of physical chemistry. C, Nanomaterials and interfaces",
     "J Phys Chem C", "J. Phys. Chem. C"),
    ("Advanced materials (Deerfield Beach, Fla.)", "Adv Mater", "Adv. Mater."),
    ("Nature communications", "Nat Commun", "Nat. Commun."),
    ("Journal of materials chemistry. A", "J Mater Chem A",
     "J. Mater. Chem. A"),
    ("ACS nano", "ACS Nano", "ACS Nano"),
    ("RSC advances", "RSC Adv", "RSC Adv."),
    # The place qualifier is PubMed's disambiguation, not part of the name.
    ("Chemical communications (Cambridge, England)", "Chem Commun (Camb)",
     "Chem. Commun."),
    # A single-word title cannot be abbreviated and must be left alone.
    ("Langmuir", "Langmuir", "Langmuir"),
    ("Science (New York, N.Y.)", "Science", "Science"),
    # The two the rule gets wrong, and gets wrong about the arrangement
    # rather than the periods - which is why they are a table.
    ("Angewandte Chemie (International ed. in English)",
     "Angew Chem Int Ed Engl", "Angew. Chem., Int. Ed."),
    ("Proceedings of the National Academy of Sciences of the United States "
     "of America", "Proc Natl Acad Sci U S A", "Proc. Natl. Acad. Sci. U.S.A."),
]


def test_cassi_abbreviations() -> None:
    section("CASSI journal abbreviations, which is what ACS screens on")

    for full, abbrev, want in CASSI_CASES:
        got, note = pubmed.cassi_journal({"journal": full,
                                          "journal_abbrev": abbrev,
                                          "pmid": "0"})
        check(f"{abbrev!r}", got, want, note)

    entry = pubmed.format_citation(STOCK, "bibtex")
    check("the .bib carries the CASSI form, not the full title",
          field(entry, "journal"), "Chem. Rev.",
          "it wrote `Chemical reviews`, which no ACS journal accepts; 15 of "
          "17 entries in a real project were corrected by hand afterwards")

    # Reported rather than guessed at. PubMed sometimes returns no
    # ISOAbbreviation at all, and there is no rule that recovers one.
    notes: list[str] = []
    got, note = pubmed.cassi_journal(
        {"journal": "Some Very Long Journal Name", "journal_abbrev": "",
         "pmid": "999"})
    check("no ISOAbbreviation writes the full title", got,
          "Some Very Long Journal Name")
    check("...and says so rather than inventing an abbreviation",
          "999" in note and "by hand" in note, True, note)
    pubmed.format_citation(
        dict(STOCK, journal_abbrev="", journal="Some Very Long Journal Name"),
        "bibtex", notes)
    check("...through format_citation's own note list", len(notes), 1,
          str(notes))


def test_article_numbers() -> None:
    section("An entry with no page range, against the same checklist line")

    check("a page range is used as it is",
          pubmed._pages_field({"pages": "933-69"})[0], "933--969")
    check("an article number stands in for one",
          pubmed._pages_field({"pages": "", "elocation": "1701526"})[0],
          "1701526",
          "`Adv Mater 2017, 29 (30)` reached the reference list with no "
          "pages at all")
    check("a DOI is not an article number",
          pubmed._pages_field({"pages": "", "pmid": "3",
                               "elocation": "doi: 10.1002/adma.201701526"})[0],
          "")
    check("...and that case is reported",
          "no page range and no article number" in pubmed._pages_field(
              {"pages": "", "pmid": "3",
               "elocation": "doi: 10.1002/adma.201701526"})[1], True)

    entry = pubmed.format_citation(
        dict(STOCK, pages="", elocation="1701526"), "bibtex")
    check("the entry carries the article number", field(entry, "pages"),
          "1701526")


def test_the_other_styles_are_unchanged() -> None:
    section("ama and apa are not touched by any of this")

    ama = pubmed.format_citation(STOCK, "ama")
    check("ama keeps PubMed's own author spelling",
          "Stock N, Biswas S" in ama, True)
    check("...and PubMed's own page range", "933-69" in ama, True,
          "AMA abbreviates by design; only the .bib is a data file")
    apa = pubmed.format_citation(STOCK, "apa")
    check("apa inverts the name its own way",
          "Stock, N." in apa and "Biswas, S." in apa, True)


def test_pandoc_round_trip() -> None:
    section("The rendered document, which is the only thing that can say")

    if not RENDER:
        skip("the pandoc round trip", "pass --render to run it")
        return
    if not shutil.which("pandoc"):
        skip("the pandoc round trip", "pandoc is not on PATH")
        return

    tmp = tempfile.mkdtemp(prefix="cite_")
    try:
        rec = dict(STOCK, authors=["Stock N", "Biswas S", "Gröger H"])
        bib = os.path.join(tmp, "references.bib")
        doc = os.path.join(tmp, "doc.md")
        _spit(bib, pubmed.format_citation(rec, "bibtex") + "\n",
              encoding="utf-8", newline="\n")
        _spit(doc, "As reported [@stock2012synthesis].\n\n::: {#refs}\n:::\n",
              encoding="utf-8", newline="\n")
        run = subprocess.run(
            ["pandoc", doc, "-t", "plain", "--citeproc", "--bibliography",
             bib], capture_output=True, text=True, encoding="utf-8",
            errors="replace")
        out = run.stdout or ""
        check("citeproc resolves the key", "stock2012synthesis" in out, False)
        check("the in-text citation is the surname",
              "Stock et al. 2012" in out, True,
              "it was `(N and S 2012)` before the writer was fixed")
        check("...and so is the reference list",
              "Stock, N." in out, True)
        check("the escaped name comes back as itself",
              "Gröger" in out, True)
        check("nothing was written to stderr", run.stderr.strip(), "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("cite.py - the citation writers, and what BibTeX makes of them")
    test_author_names()
    test_tex_escapes()
    test_page_ranges()
    test_cassi_abbreviations()
    test_article_numbers()
    test_the_other_styles_are_unchanged()
    test_pandoc_round_trip()
    print(f"\n{PASS + FAIL} checks: {PASS} passed, {FAIL} failed"
          + (f", {SKIP} skipped" if SKIP else ""))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
