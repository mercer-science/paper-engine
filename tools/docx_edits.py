#!/usr/bin/env python3
"""
docx_edits.py - read tracked changes and comments out of a Word .docx.

The ingest half of writing-engine: a coauthor return or a reviewer's marked-up
file goes in, and every insertion, deletion, and comment comes out as JSON with
its author, its date, and the sentence it sits in. No .docx is ever written.

Why not pandoc: --track-changes handles insertions and deletions but drops
comment text and author attribution, and attribution is exactly what the edit
ledger needs. So the OOXML is parsed directly.

Usage:
  python docx_edits.py extract manuscript_r5_JV.docx --json
  python docx_edits.py plain manuscript_r5_JV.docx --accept
  python docx_edits.py plain manuscript_r5_JV.docx --reject > baseline.txt
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile

try:
    from lxml import etree
except ImportError:
    sys.exit("docx_edits.py requires 'lxml'. Install with: python -m pip install lxml")

# Author names and quoted manuscript text routinely carry non-ASCII characters;
# the Windows console defaults to cp1252 and would mangle or crash on them.
for _stream in (sys.stdout, sys.stderr):
    # A redirected stream (a pipe, a StringIO under a harness) has no
    # reconfigure at all; asking for it by name keeps that case quiet.
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# OOXML names
# ---------------------------------------------------------------------------

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "w15": "http://schemas.microsoft.com/office/word/2012/wordml",
}


def q(name: str) -> str:
    """'w:ins' -> '{...main}ins'."""
    prefix, local = name.split(":", 1)
    return "{%s}%s" % (NS[prefix], local)


P = q("w:p")
INS, DEL = q("w:ins"), q("w:del")
MOVE_TO, MOVE_FROM = q("w:moveTo"), q("w:moveFrom")
T, DEL_TEXT, INSTR_TEXT = q("w:t"), q("w:delText"), q("w:instrText")
TAB, BR, CR = q("w:tab"), q("w:br"), q("w:cr")
FLD_SIMPLE = q("w:fldSimple")
RANGE_START, RANGE_END = q("w:commentRangeStart"), q("w:commentRangeEnd")
COMMENT_REF = q("w:commentReference")
W_ID, W_AUTHOR, W_DATE = q("w:id"), q("w:author"), q("w:date")

# Properties, not body text. <w:pPr><w:rPr><w:ins/></w:rPr></w:pPr> marks an
# INSERTED PARAGRAPH MARK - it carries no text, and a `//w:ins` search reports
# it as a phantom empty insertion. <w:rPrChange>/<w:pPrChange> wrap a copy of
# the OLD properties, which can contain further revision marks. Skipping these
# two subtrees outright is what keeps every path anchored to its true parent.
PROPERTY_SUBTREES = {q("w:pPr"), q("w:rPr")}

FORMAT_CHANGES = {q("w:rPrChange"), q("w:pPrChange"), q("w:tblPrChange"),
                  q("w:trPrChange"), q("w:tcPrChange"), q("w:sectPrChange"),
                  q("w:tblGridChange")}

# Reference-manager field codes. Ordinary Word fields (PAGE, TOC, REF) are not
# listed: they do not touch citations, and flagging them would cry wolf.
CITATION_FIELDS = [
    ("EndNote", ("ADDIN EN.", "EN.CITE", "EN.REFLIST")),
    ("Zotero", ("ZOTERO_ITEM", "ZOTERO_BIBL")),
    ("Mendeley", ("MENDELEY_CITATION", "MENDELEY_BIBLIOGRAPHY")),
    ("Citavi", ("CITAVI",)),
    ("CSL", ("CSL_CITATION",)),   # generic fallback - see _field_codes
]

# OneDrive sync-conflict copies: manuscript_r5_JV-DESKTOP-A4B6PBU.docx. Same
# regex the R float pipeline uses. Reading one is reading a coauthor twice.
ONEDRIVE_CONFLICT_RE = re.compile(r"-DESKTOP-[A-Z0-9]+(-\d+)?")


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _join(items) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


# ---------------------------------------------------------------------------
# Package reading
# ---------------------------------------------------------------------------

def _read_parts(path: str) -> dict:
    """Return the parsed XML parts we care about. Missing optional parts are None."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no such file: {path}")
    if not zipfile.is_zipfile(path):
        raise ValueError(f"not a .docx (not a zip archive): {os.path.basename(path)}"
                         + ("; .doc is the old binary format - re-save as .docx"
                            if path.lower().endswith(".doc") else ""))
    parts = {}
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "word/document.xml" not in names:
            raise ValueError(f"not a Word document (no word/document.xml): "
                             f"{os.path.basename(path)}")
        for key, member in (("document", "word/document.xml"),
                            ("comments", "word/comments.xml"),
                            ("comments_ex", "word/commentsExtended.xml")):
            parts[key] = (etree.fromstring(z.read(member))
                          if member in names else None)
    return parts


# ---------------------------------------------------------------------------
# Token stream
# ---------------------------------------------------------------------------
# Each paragraph becomes a list of tokens in document order. A token is either
# text (with the revision state it inherited) or a comment marker. Nesting is
# handled by carrying the state down the walk instead of searching for revision
# elements, which is what makes a <w:del> inside a <w:ins> come out right.

def _token(text, state, para):
    in_ins, in_del = state["ins"], state["del"]
    return {
        "kind": "deletion" if in_del else ("insertion" if in_ins else "unchanged"),
        "text": text,
        "author": (in_del or in_ins or (None, None, False))[0],
        "date": (in_del or in_ins or (None, None, False))[1],
        "moved": bool((in_del or in_ins or (None, None, False))[2]),
        "nested": bool(in_ins and in_del),
        "in_ins": bool(in_ins),
        "in_del": bool(in_del),
        "para": para,
    }


def _revision(el, moved: bool):
    return (el.get(W_AUTHOR) or "", el.get(W_DATE) or "", moved)


def _walk(el, state, out, para):
    for child in el:
        tag = child.tag
        if tag in PROPERTY_SUBTREES or tag is etree.Comment:
            continue
        if tag == P:
            continue  # a nested paragraph (text box, table cell) - walked on its own
        if tag in (INS, MOVE_TO):
            _walk(child, {**state, "ins": _revision(child, tag == MOVE_TO)}, out, para)
        elif tag in (DEL, MOVE_FROM):
            _walk(child, {**state, "del": _revision(child, tag == MOVE_FROM)}, out, para)
        elif tag in (T, DEL_TEXT):
            if child.text:
                out.append(_token(child.text, state, para))
        elif tag == TAB:
            out.append(_token("\t", state, para))
        elif tag in (BR, CR):
            out.append(_token("\n", state, para))
        elif tag == INSTR_TEXT:
            continue  # field instructions are code, not manuscript text
        elif tag in (RANGE_START, RANGE_END, COMMENT_REF):
            out.append({"kind": "marker", "marker": tag, "id": child.get(W_ID),
                        "para": para})
        else:
            _walk(child, state, out, para)


def _paragraph_tokens(doc) -> list:
    """One token list per paragraph, in document order."""
    body = doc.find(q("w:body"))
    if body is None:
        return []
    # .iter() is the right tool here and not the `//` trap: every <w:p> in the
    # body IS a body paragraph, including those inside tables. Nested ones are
    # skipped by _walk so their text is not counted twice.
    paras = []
    for i, p in enumerate(body.iter(P)):
        tokens = []
        _walk(p, {"ins": None, "del": None}, tokens, i)
        paras.append(tokens)
    return paras


def _render(tokens, mode: str) -> str:
    """Text of one paragraph under 'accept' or 'reject'.

    accept - every insertion taken, every deletion applied: what the editor
             wants the document to say.
    reject - every change undone: what the document said before they touched
             it, which is what matches the source_text/ markdown we sent out.

    A run inside <w:ins><w:del> is absent from BOTH: it was typed by one author
    and cut by another, so accepting all changes drops it and rejecting all
    changes drops it too.
    """
    keep = (lambda t: not t["in_del"]) if mode == "accept" else (lambda t: not t["in_ins"])
    return "".join(t["text"] for t in tokens
                   if t["kind"] != "marker" and keep(t))


# ---------------------------------------------------------------------------
# Sentence context
# ---------------------------------------------------------------------------

# Lowercase tokens that end in a period without ending a sentence. "et al." is
# the one that matters - it is in every manuscript paragraph that cites anything.
ABBREVIATIONS = {"al", "et", "e.g", "i.e", "cf", "vs", "fig", "figs", "eq", "eqs",
                 "ref", "refs", "no", "approx", "ca", "etc", "dr", "prof", "st",
                 "mr", "ms", "sp", "spp", "min", "max", "avg", "temp"}

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


_BOUNDARY_RE = re.compile(r'[.!?][\"\'\)\]]*\s+')
_TRAILING_WORD_RE = re.compile(r'([A-Za-z][A-Za-z.]*)$')


def _sentence_spans(text: str) -> list:
    """(start, end) of each sentence. End includes the trailing whitespace."""
    spans, start = [], 0
    # SENTENCE_BOUNDARY_RE, not the local _BOUNDARY_RE: the citation run that
    # ACS and Nature put after the period is what a .docx carries, and this
    # reader is the one that reads a .docx. The abbreviation and initial
    # tests below are this engine's own and are unchanged.
    for m in SENTENCE_BOUNDARY_RE.finditer(text):
        nxt = text[m.end():m.end() + 1]
        if nxt and not (nxt.isupper() or nxt in "([\"'"):
            continue  # "et al. 2009" - lower case or a digit continues the sentence
        word = _TRAILING_WORD_RE.search(text[start:m.start()])
        if word and word.group(1).rstrip(".").lower() in ABBREVIATIONS:
            continue
        if word and len(word.group(1)) == 1 and word.group(1).isupper():
            continue  # an initial: "R. W. Smith"
        spans.append((start, m.end()))
        start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans or [(0, len(text))]


def _context(text: str, offset: int, width: int = 400) -> str:
    """The sentence around `offset`. A change sitting exactly on a sentence
    boundary belongs to the sentence that follows it."""
    for start, end in _sentence_spans(text):
        if start <= offset < end or (offset >= len(text) and end == len(text)):
            sentence = text[start:end].strip()
            if len(sentence) > width:
                rel = max(0, offset - start - width // 2)
                sentence = ("..." if rel else "") + sentence[rel:rel + width] + "..."
            return sentence
    return text.strip()[:width]


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------

def _revisions(paras) -> list:
    """Merge adjacent runs of one revision into one entry, with its context.

    Word splits a single typed phrase across many runs (a spell-check pass is
    enough to do it), so an unmerged list reports one insertion per run and the
    ledger fills with fragments.
    """
    entries = []
    for tokens in paras:
        # Offsets are measured in the ORIGINAL text, because that is what the
        # markdown in source_text/ still says. An insertion has no extent there,
        # so it takes the offset of the point it was typed at.
        original, cursor, here = [], 0, []
        current = None
        for tok in tokens:
            if tok["kind"] == "marker":
                continue  # a comment mark never splits a revision
            offset = cursor
            if not tok["in_ins"]:
                original.append(tok["text"])
                cursor += len(tok["text"])
            if tok["kind"] == "unchanged":
                current = None
                continue
            # Same author, same kind, same timestamp: Word split one typed
            # phrase across runs, so this run continues the open revision.
            if (current is not None
                    and current["kind"] == tok["kind"]
                    and current["author"] == tok["author"]
                    and current["date"] == tok["date"]
                    and current["moved"] == tok["moved"]
                    and current["nested"] == tok["nested"]):
                current["text"] += tok["text"]
                continue
            current = {"kind": tok["kind"], "author": tok["author"],
                       "date": tok["date"], "moved": tok["moved"],
                       "nested": tok["nested"], "text": tok["text"],
                       "paragraph": tok["para"], "_offset": offset}
            here.append(current)
        para_text = "".join(original)
        if para_text.strip():
            for entry in here:
                entry["context"] = _context(para_text, entry.pop("_offset"))
        else:
            # A wholly new paragraph has no original text to sit in, and an
            # empty context would anchor the ledger to nothing. Fall back to
            # the paragraph as the author left it.
            accepted = _render(tokens, "accept")
            for entry in here:
                entry.pop("_offset")
                entry["context"] = _context(accepted,
                                            max(0, accepted.find(entry["text"])))
        entries.extend(here)
    return entries


def _anchors(paras) -> dict:
    """Comment id -> the text it was anchored over, as the commenter saw it.

    Both inserted and deleted text count: markup view is what they highlighted.
    A comment with no range (just a reference mark) falls back to its paragraph.
    """
    spans, open_ids, ref_para = {}, {}, {}
    for tokens in paras:
        for tok in tokens:
            if tok["kind"] == "marker":
                cid = tok["id"]
                if tok["marker"] == RANGE_START:
                    open_ids[cid] = []
                elif tok["marker"] == RANGE_END:
                    if cid in open_ids:
                        spans[cid] = "".join(open_ids.pop(cid))
                elif tok["marker"] == COMMENT_REF:
                    ref_para[cid] = tok["para"]
            else:
                for buf in open_ids.values():
                    buf.append(tok["text"])
    for cid, buf in open_ids.items():  # unterminated range - take what we saw
        spans[cid] = "".join(buf)
    return {"spans": spans, "ref_para": ref_para}


def _comments(parts, paras) -> list:
    root = parts["comments"]
    if root is None:
        return []
    done = {}
    if parts["comments_ex"] is not None:
        # Direct children only: commentsExtended is flat, and a descendant
        # search would be one more unanchored path.
        for ex in parts["comments_ex"].findall(q("w15:commentEx")):
            done[ex.get(q("w15:paraId"))] = ex.get(q("w15:done")) in ("1", "true")

    anchors = _anchors(paras)
    para_text = {i: _render(t, "accept") for i, t in enumerate(paras)}
    out = []
    for c in root.findall(q("w:comment")):
        cid = c.get(W_ID)
        tokens = []
        for i, cp in enumerate(c.findall(q("w:p"))):
            _walk(cp, {"ins": None, "del": None}, tokens, i)
        by_para = {}
        for tok in tokens:
            if tok["kind"] != "marker" and not tok["in_del"]:
                by_para.setdefault(tok["para"], []).append(tok["text"])
        text = "\n".join("".join(v) for _, v in sorted(by_para.items())).strip()

        # commentsExtended keys off the paraId of the comment's LAST paragraph.
        para_ids = [cp.get(q("w14:paraId")) for cp in c.findall(q("w:p"))]
        resolved = done.get(para_ids[-1] if para_ids else None, False)

        anchor = anchors["spans"].get(cid)
        para = anchors["ref_para"].get(cid)
        if not anchor:
            anchor = para_text.get(para, "")
        out.append({
            "id": cid,
            "author": c.get(W_AUTHOR) or "",
            "initials": c.get(q("w:initials")) or "",
            "date": c.get(W_DATE) or "",
            "anchor": anchor.strip()[:400],
            "text": text,
            "resolved": bool(resolved),
            "paragraph": para,
        })
    return out


def _field_codes(doc) -> tuple:
    body = doc.find(q("w:body"))
    instructions = []
    if body is not None:
        for el in body.iter():
            if el.tag == INSTR_TEXT and el.text:
                instructions.append(el.text)
            elif el.tag == FLD_SIMPLE:
                instructions.append(el.get(q("w:instr")) or "")
    upper = [i.upper() for i in instructions]
    managers = [name for name, keys in CITATION_FIELDS
                if any(k in instr for instr in upper for k in keys)]
    # Zotero and Mendeley both wrap a CSL_CITATION payload, so a named manager
    # already accounts for it; reporting both names one field twice.
    if len(managers) > 1 and "CSL" in managers:
        managers.remove("CSL")
    return bool(managers), managers


def _formatting_changes(doc) -> int:
    body = doc.find(q("w:body"))
    if body is None:
        return 0
    return sum(1 for el in body.iter() if el.tag in FORMAT_CHANGES)


def extract(path: str) -> dict:
    parts = _read_parts(path)
    doc = parts["document"]
    paras = _paragraph_tokens(doc)

    revisions = _revisions(paras)
    insertions = [r for r in revisions if r["kind"] == "insertion"]
    deletions = [r for r in revisions if r["kind"] == "deletion"]
    for r in revisions:
        r.pop("kind", None)
    comments = _comments(parts, paras)
    fields_present, managers = _field_codes(doc)
    formatting = _formatting_changes(doc)

    authors = sorted({r["author"] for r in revisions if r["author"]}
                     | {c["author"] for c in comments if c["author"]})

    notes = []
    name = os.path.basename(path)
    if ONEDRIVE_CONFLICT_RE.search(name):
        notes.append("filename looks like a OneDrive sync conflict copy; "
                     "the original file carries the same edits")
    if managers:
        notes.append(f"contains {_join(managers)} field codes; "
                     "citations read as plain text")
    nested = sum(1 for r in revisions if r["nested"])
    if nested:
        notes.append(f"{_n(nested, 'run')} inserted by one author and deleted by "
                     "another; they appear in neither the accepted nor the "
                     "rejected text")
    moved = sum(1 for r in revisions if r["moved"])
    if moved:
        notes.append(f"{_n(moved, 'run')} are a move (Word's moveFrom/moveTo), "
                     "reported as a deletion and an insertion of the same text")
    if formatting:
        notes.append(f"{_n(formatting, 'formatting-only revision')} present; "
                     "these change no text and are not listed")
    if not revisions and not comments:
        notes.append("no tracked changes and no comments in this file")

    return {
        "file": name,
        "path": os.path.abspath(path),
        "authors": authors,
        "insertions": insertions,
        "deletions": deletions,
        "comments": comments,
        "formatting_changes": formatting,
        "field_codes_present": fields_present,
        "citation_managers": managers,
        "paragraphs": len(paras),
        "notes": notes,
    }


def plain(path: str, mode: str = "accept") -> str:
    paras = _paragraph_tokens(_read_parts(path)["document"])
    return "\n".join(_render(t, mode) for t in paras)


# ---------------------------------------------------------------------------
# Human-readable output
# ---------------------------------------------------------------------------

def _print_extract(res: dict) -> None:
    print(res["file"])
    print("authors: " + (", ".join(res["authors"]) or "(none)"))
    open_comments = sum(1 for c in res["comments"] if not c["resolved"])
    print(f"{_n(len(res['insertions']), 'insertion')}, "
          f"{_n(len(res['deletions']), 'deletion')}, "
          f"{_n(len(res['comments']), 'comment')} ({open_comments} unresolved), "
          f"{_n(res['formatting_changes'], 'formatting change')}")

    for label, key in (("INSERTIONS", "insertions"), ("DELETIONS", "deletions")):
        if not res[key]:
            continue
        print(f"\n{label}")
        sign = "+" if key == "insertions" else "-"
        for r in res[key]:
            tags = "".join(t for t, on in ((" [moved]", r["moved"]),
                                           (" [nested]", r["nested"])) if on)
            date = (r["date"] or "")[:10]
            print(f"  [{r['author']} {date}]{tags} {sign}\"{r['text']}\"")
            print(f"      ¶{r['paragraph']}  {r['context']}")

    if res["comments"]:
        print("\nCOMMENTS")
        for c in res["comments"]:
            state = "resolved" if c["resolved"] else "open"
            print(f"  [{c['author']}, {state}] on \"{c['anchor']}\"")
            for line in c["text"].splitlines():
                print(f"      {line}")

    if res["notes"]:
        print("\nNOTES")
        for n in res["notes"]:
            print(f"  - {n}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(prog="docx_edits.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")

    # --json is accepted on either side of the subcommand. SUPPRESS keeps the
    # subparser from resetting a flag already set before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="emit JSON instead of text")

    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", parents=[common],
                       help="tracked changes and comments, with authors and context")
    e.add_argument("path")

    pl = sub.add_parser("plain", parents=[common],
                        help="the document text, with changes accepted or rejected")
    pl.add_argument("path")
    mode = pl.add_mutually_exclusive_group()
    mode.add_argument("--accept", dest="mode", action="store_const", const="accept",
                      help="every change taken (default): what the editor wants")
    mode.add_argument("--reject", dest="mode", action="store_const", const="reject",
                      help="every change undone: what we sent them")
    pl.set_defaults(mode="accept")

    args = p.parse_args()

    if args.cmd == "extract":
        res = extract(args.path)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            _print_extract(res)
        return 0

    if args.cmd == "plain":
        text = plain(args.path, args.mode)
        if args.json:
            print(json.dumps({"file": os.path.basename(args.path),
                              "mode": args.mode, "text": text},
                             indent=2, ensure_ascii=False))
        else:
            print(text)
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
