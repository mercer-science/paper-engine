#!/usr/bin/env python3
"""
prose.py - the checkable half of manuscript writing.

skills/writing-engine/SKILL.md holds the judgment: whether a claim is
overstated, whether a caption's trim dropped an assertion, whether a paragraph
earns its numbers. This file holds the parts with right answers - counting
numbers the way a reader pays for them, validating an outline's evidence
brackets against the files that would have to contain that evidence,
collecting flags, and proving every citekey resolves.

Every command is standalone and takes files or a project directory, so a
checking module can be run alone against the current source text - which is the
point of the module isolation in specs/writing-engine.md 2.3.

Sixteen commands:

  density       number budget per paragraph, per section       (spec 6, 6.1, prose 5.9)
  outline       outline lines vs. paragraphs, evidence         (spec 12, 5.1)
  flags         collect **[FLAG: ...]** with locations         (spec 5.10)
  citekeys      in-text citekeys vs. references.bib            (spec 9.1, 5.5)
  captions      claim-first legends, float/text pairing        (spec 7)
  crossrefs     every float and panel called out, in order     (spec 7.1)
  length        word counts against the journal's budget       (spec 4.4)
  numbers       every number in the text vs. the stats output  (spec 6 rule 5)
  voice         how the prose reads - an instrument, never a gate  (prose 3.4)
  readability   what voice does not measure                    (comprehension-check 3)
  claim         the abstract's one take-home, and its support  (prose 1.3)
  metaprose     pipeline text that reached the manuscript      (prose 0.2)
  ai_voice      the 15 countable AI-voice patterns             (prose 10.3)
  spelling      British spellings - CORRECTS, with --fix       (user-asks 3)
  italics       Latin phrases and gene symbols - CORRECTS half (user-asks-2 1)
  entity_forms  the abstract's name for a thing vs. the body's (user-asks-2 2)
  abbreviations defined nowhere, only in the body, or ignored  (user-asks-2 3)

`specs/writing-engine-prose.md` 0.2 governs all of them: a check about the
shape of prose REPORTS and exits 0, and only a layer-0 invariant - a number, a
citekey, a flag, a methods value - may block. `voice` therefore has no
threshold anywhere in it, and `metaprose` inserts a flag rather than failing a
build.

TWO of them write, and only under `--fix`: `spelling`, and the `always` half
of `italics`. Both earn it the same way - the finding names an exact substring,
the correction is a lookup rather than a judgment, there is no threshold, and
every false positive can be listed by name. Nothing else here has all four
properties, which is why nothing else here writes.

Usage:
  python prose.py density "proj/drafts/source_text_r8/results.md" --level low --json
  python prose.py density "path/to/project" --config "<journal>/writing_config.yml" \
      --rules "<toolkit>/writing_guides/learned/rules.yml" \
      --research-type surface_science --json
  python prose.py outline "path/to/project" --json
  python prose.py flags "path/to/project" --json
  python prose.py citekeys "path/to/project" --max-refs 50 --json
  python prose.py captions "path/to/project" --cap 350 --json
  python prose.py crossrefs "path/to/project" --style parenthetical --json
  python prose.py length "path/to/project" --limit total=3500 --json
  python prose.py numbers "path/to/project" --json
  python prose.py voice "path/to/project" --per-paragraph --json
  python prose.py metaprose "path/to/project" --insert --json
  python prose.py spelling "path/to/project" --fix --json
  python prose.py italics "path/to/project" --fix --journal JACS --json
  python prose.py entity_forms "path/to/project" --json
  python prose.py abbreviations "path/to/project" --in-abstract define --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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

# The caption grammar is a contract with plan/theme/read_captions.R and with
# tools/idea.py, both of which carry the same regex. Change one, change all
# three - a block that stops matching is invisible everywhere downstream and
# nothing reports it.
CAPTION_HEADING_RE = re.compile(
    r"^##\s+(Figure|Table)\s+(S?\d+)\s*(?:—|–|-{1,2})\s*(\S+)\s*$")

# The type may carry an internal hyphen - `meta-prose`, written by the scan in
# prose 0.2 - but a hyphen used as the SEPARATOR must still end the type, which
# is why the class cannot end on one: "FLAG: stats - value missing" has to read
# as type `stats`, not `stats -`.
FLAG_RE = re.compile(
    r"\*\*\[FLAG:\s*([A-Za-z](?:[A-Za-z-]*[A-Za-z])?)\s*(?:[—–:-]\s*)?(.*?)\]\*\*",
    re.DOTALL)

# `edit-override` and `edit-intent` are specs/edit-authority.md 3 and 4. Both
# are questions about whose sentence wins, and both go through the flag
# convention rather than through a channel of their own: flags survive to the
# next round, `flag-resolver` already walks them with the user, and
# `submission-package` already refuses to ship a paper still carrying one.
# Building a second question channel beside that is this toolkit's own
# recurring mistake.
# `citation-claim` is citation-integrity 6.5, and it is the ONE route by
# which an attribution verdict can block a build. The module reports and
# never rewrites, so the flag is OFFERED per pair and written only on a yes;
# once written it is an ordinary flag, which is the whole point - it goes
# through `flag-resolver` and `submission-package` refuses to ship it.
# `citation` is "is this reference real", `citation-claim` is "does the
# source say what this sentence says it says".
FLAG_TYPES = ["citation", "citation-claim", "stats", "data", "decision",
              "author", "conflict", "journal", "meta-prose", "edit-override",
              "edit-intent"]

# Semantic citekey, spec 9.1. Bracketed is the normal form; a bare @key is
# pandoc-legal and has to be found too, or the residue scan misses it.
# The lookbehind is the whole point. Pandoc opens a citation only where the
# `@` follows the start of a line, whitespace, or a bracket - never a word
# character, and never a `~`. Without it, and because `~` is legal *inside* a
# key, `SiO~2~@Cu~2~GaBO~5~@TiO~2~` reads as two citekeys `@Cu~2~GaBO~5` and
# `@TiO~2`; those were then reported as references missing from the .bib and
# written into the PAPER NOT COMPLETE block as `[@Cu~2~GaBO~5]`, where they
# became real citekeys that citeproc could not resolve and the build died on
# its own message. A core@shell formula is chemistry, not a citation.
CITEKEY_RE = re.compile(r"(?<![\w~])@([A-Za-z][\w:.#$%&+?<>~/-]*[\w])")
BIB_ENTRY_RE = re.compile(r"^\s*@\w+\s*\{\s*([^,\s}]+)\s*,", re.MULTILINE)

# `#` as well as `##`, because outline.md now opens with two REGION headings
# at level 1 - `# Structure` over the paragraph plan and `# Notes` over the
# working surface - and the sections of the paper are the `##` headings under
# the first of them. Both regions used to be described only inside a
# twenty-two-line HTML comment whose own last line said "delete this comment
# once you have the hang of it", so the file's structure was explained in the
# part of the file the file told you to throw away (item 23).
OUTLINE_SECTION_RE = re.compile(r"^#{1,2}\s+(.+?)\s*$")

# A level-1 region heading that introduces the paragraph plan. It names no
# section of the paper, so a line directly under it belongs to no section
# until a `##` says which.
STRUCTURE_SECTIONS = {"structure", "outline", "paragraph plan", "the paper"}
OUTLINE_LINE_RE = re.compile(r"^[-*]\s+(.*\S)\s*$")

# prose 4.4. The third state the toolkit lacked. `**[FLAG]**` can only say
# "absent, someone must fill it"; a deliberate deletion needs "absent on
# purpose, stop asking". Without it, advisory-once is still a report every
# round, and a line the user removed for a reason keeps coming back as an
# uncovered point forever.
#
#   - [waived: superseded by the XPS control] Report the sputter-clean sequence
#
# The reason is REQUIRED. An unexplained waiver is the thing nobody can audit
# in six months, and a marker that could be written empty would be written
# empty. A `[waived]` with nothing after it is read as a waiver with no reason
# and reported as one, rather than silently honoured.
OUTLINE_WAIVED_RE = re.compile(r"^\[waived\s*:?\s*([^\]]*)\]\s*",
                               re.IGNORECASE)

# A line that is nothing but italic or bold text - the one-line rule printed
# under each region heading in the scaffolded outline.md.
EMPHASIS_ONLY_RE = re.compile(r"^\*{1,2}[^*].*[^*]\*{1,2}$")
EVIDENCE_RE = re.compile(r"\[([^\]]*)\]\s*$")

# The one block of outline.md that is not a paragraph plan. Everything the
# file cannot hold in one line per paragraph goes here, at the bottom, and
# its bullets are NOT claims: parsed as claims they become paragraphs the
# paper is then reported as having dropped, every round, forever.
NOTES_SECTIONS = {"notes", "other notes", "open questions", "questions",
                  "to do", "todo", "parking lot", "scratch", "misc"}

# outline.md is a paragraph plan, not a draft of the paragraph: one line is
# the idea, not what the idea will say. Past this it stops being an outline,
# and it also breaks the alignment in outline() - a long line overlaps every
# paragraph lexically, so the hint that tells you which paragraph implements
# which line stops discriminating.
OUTLINE_MAX_WORDS = 25

# --- OUTLINE ADHERENCE (writing-engine 19.2) ------------------------------
# A scale of 1-5, asked every round rather than a dial set once. The three
# words the dial used to accept are not retired - they become the names of
# levels 2, 3 and 5 - so a config saying `outline_adherence: strict` reads
# back as 5 and is never rewritten.
#
# THIS IS THE ONLY TABLE OF WHAT A LEVEL MEANS. Everything downstream
# compares numbers. `manuscript.py` carries the same two tables verbatim so
# that it can validate a dial without spawning an interpreter, and
# `tests/prose.py` holds the two copies against each other - the same
# treatment CAPTION_HEADING_RE and the LAYOUT CONTRACT get, and for the same
# reason: repetition is only safe while something compares the copies.
ADHERENCE_LEVELS = {1: "none", 2: "loose", 3: "medium", 4: "tight",
                    5: "strict"}
ADHERENCE_DEFAULT = 3

ADHERENCE_NOTES = {
    1: "the outline does not steer this round; its findings are advisory "
       "and every truth check is untouched",
    2: "the outline is a checklist - every line must appear somewhere. "
       "Order is the drafter's",
    3: "report and ask; either the draft or the outline can be the one that "
       "is wrong",
    4: "one paragraph per line, in the outline's order; the only permitted "
       "addition is a bridging sentence",
    5: "every line becomes exactly one paragraph, in order, nothing added - "
       "predictable, and in past attempts painful to read",
}

# The three findings that measure the draft against the outline, as opposed
# to the ones that measure whether a line's evidence exists. Level 1 softens
# exactly these and leaves every truth check an error, which is the same
# softening prose 4.3 gives a frozen section.
ADHERENCE_FINDINGS = ("dropped_point", "added_content", "out_of_order")


def adherence_level(value: object, default: int = ADHERENCE_DEFAULT) -> int:
    """A number, from a number or from one of the five names.

    Unrecognised falls back to `default` rather than raising: this is read on
    every path that reads a config, and a hand-edited nonsense value is
    reported by the config reader rather than crashing a build.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value if value in ADHERENCE_LEVELS else default
    text = str(value or "").strip().lower()
    if text.isdigit() and int(text) in ADHERENCE_LEVELS:
        return int(text)
    for n, name in ADHERENCE_LEVELS.items():
        if text == name:
            return n
    return default


def adherence_label(level: int) -> str:
    """`4 (tight)` - the one spelling every printed line uses."""
    return "%d (%s)" % (level, ADHERENCE_LEVELS.get(level, "?"))


# What `--adherence` and `config --set outline_adherence=` accept. Both
# spellings of every level, because the user asked for a 1-5 scale and the
# config has been written in words since the day it existed.
ADHERENCE_VALUES = ([str(n) for n in sorted(ADHERENCE_LEVELS)]
                    + [ADHERENCE_LEVELS[n] for n in sorted(ADHERENCE_LEVELS)])
# --- end OUTLINE ADHERENCE ------------------------------------------------

DENSITY_BUDGETS = {
    "low": (2.0, 3.0),
    "medium": (4.0, 5.0),
    "high": (None, None),
}

# A paragraph past this fraction of numeric tokens is a table wearing a
# paragraph's clothes, whatever the density setting says (spec 6.1).
NUMERIC_FRACTION_CAP = 1.0 / 3.0

SECTION_FILES = ["title_abstract", "introduction", "methods", "results",
                 "discussion"]

# prose 5.9. One number applied to five sections that are not alike was, in the
# one measured run, too restrictive in Methods and too loose in the abstract at
# the same time: 8 of 11 findings landed on a section whose job is to be dull
# and complete, and the abstract sat at nearly 4x the same budget in the same
# list. Methods is `high` - unenforced, which is what the prose instruction
# always said and what the code should have said.
#
# `basis: absolute` exists for the abstract and only for the abstract. A rate
# over 250 words is noise - one more number moves a per-100-word figure by 0.4 -
# and what matters there is the total, because the reader meets all of them at
# once.
SECTION_DENSITY_DEFAULTS: dict[str, object] = {
    "title_abstract": {"inline": 6.0, "clusters": 2.0, "basis": "absolute"},
    "introduction": "low",
    "methods": "high",
    "results": "medium",
    "discussion": "low",
}

DENSITY_BASES = ("per100", "absolute")

# --- voice (prose 3.4) -----------------------------------------------------
# Every list here is REPORTED and never thresholded. Hedging is correct in a
# discussion and wrong in a result, so the count is per section and never
# summed; the same is true of every other measure in the command.

HEDGE_WORDS = ["may", "might", "could", "suggest", "suggests", "suggested",
               "indicate", "indicates", "indicated", "appear", "appears",
               "appeared", "likely", "possibly", "presumably", "seem",
               "seems", "seemed", "apparently", "perhaps", "potentially"]

TRANSITION_WORDS = ["however", "thus", "therefore", "moreover", "furthermore",
                    "additionally", "consequently", "nevertheless",
                    "nonetheless", "hence", "accordingly"]

# [GS]: a nominalization is a verb wearing a noun's clothes, and the tell is a
# weak verb holding it up - "an investigation of X was performed" for
# "we investigated X". The bare noun is counted too, because the ratio between
# the two is the interesting half.
NOMINALIZATION_RE = re.compile(
    r"\b[A-Za-z]{4,}(?:tions?|ments?|ances?|ences?|encies|ency)\b")
WEAK_VERBS = ["is", "are", "was", "were", "be", "been", "being", "make",
              "makes", "made", "perform", "performs", "performed", "conduct",
              "conducts", "conducted", "carry", "carries", "carried",
              "provide", "provides", "provided", "give", "gives", "given",
              "undertake", "undertaken", "occur", "occurs", "occurred",
              "take", "takes", "taken", "do", "does", "did"]

_BE_FORMS = r"(?:is|are|was|were|be|been|being|becomes?|became)"
# A participle by its regular ending, plus the irregulars a methods section
# actually uses. Reported as a fraction of sentences, never as a verdict: the
# passive is correct in a methods section and the command says so nowhere.
_PARTICIPLE = (r"(?:[A-Za-z]{3,}(?:ed|en)|done|made|held|kept|set|put|built|"
               r"found|shown|grown|drawn|taken|given|seen|known|left|lost|"
               r"sent|spent|split|read|run|cut|bound|ground|wound)")
PASSIVE_RE = re.compile(
    rf"\b{_BE_FORMS}\s+(?:\w+ly\s+)?(?:not\s+)?{_PARTICIPLE}\b",
    re.IGNORECASE)

# Words that carry no subject matter, so a lead sentence built only from them
# is the throat-clearing opener the digest's non-negotiable rule is about.
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "that",
    "this", "these", "those", "there", "here", "with", "without", "within",
    "from", "into", "onto", "over", "under", "between", "among", "for", "of",
    "in", "on", "at", "to", "by", "as", "is", "are", "was", "were", "be",
    "been", "being", "has", "have", "had", "not", "no", "also", "both",
    "each", "which", "while", "when", "where", "how", "why", "what", "who",
    "its", "their", "our", "we", "it", "they", "he", "she", "his", "her",
    "them", "us", "you", "can", "could", "may", "might", "will", "would",
    "should", "shall", "must", "do", "does", "did", "done", "more", "most",
    "less", "least", "very", "such", "same", "other", "others", "any", "all",
    "some", "one", "two", "three", "however", "thus", "therefore", "because",
    "several", "many", "various", "factors", "influence", "influenced",
    "important", "importantly", "notably", "interesting", "well", "known",
    "used", "using", "use", "shown", "show", "showed", "observed", "found",
    "results", "result", "study", "studies", "work", "present", "presented",
}

# --- metaprose (prose 0.2) -------------------------------------------------
# What generalizes is not a word but a grammatical shape: the SUBJECT of the
# clause is the document itself. A word list catches "the film was deliberately
# over-oxidized", which is real science, and a false positive that fails a
# build is exactly the class of prohibition that teaches the user to stop
# reading the report.
META_SUBJECT_RE = re.compile(
    r"\b(?:this|the)\s+(?:section|paragraph|outline|brief|draft|"
    r"subsection|passage)\b\s*(?:,[^,.;:]{0,40},)?\s*"
    r"(?:is|was|does|will|should|must|has|have|had|contains|holds|carries|"
    r"deliberately|intentionally|reads|serves|exists|remains|stays)\b",
    re.IGNORECASE)

META_PLACEHOLDER_RE = re.compile(r"\b(?:TODO|FIXME|XXX|lorem ipsum|lorem)\b")

# Patterns 18-20 of the AI-voice audit: chatbot artifacts, sycophantic framing
# and cutoff disclaimers. They take the FLAG path rather than the report path
# because they are not prose at all - they are chatbot text that reached a
# manuscript, which is the family this detector was built for (prose 10.2).
#
# MEASURED 2026-09-16, and the spec's own suspicion was wrong: over eight
# sentences from the audit file's trigger lists, META_SUBJECT_RE matched 0 and
# META_PLACEHOLDER_RE matched 0. The reason is structural rather than a gap -
# META_SUBJECT_RE matches a clause whose subject is the DOCUMENT ("this
# section deliberately..."), and a chatbot artifact's subject is the SPEAKER
# ("I hope...", "I may not have..."). Widening either one would make it match
# two unrelated shapes, so this is a third constant inside the SAME detector,
# emitting the same META_FLAG: one family per shape, never a second detector.
CHATBOT_ARTIFACT_RE = re.compile(
    r"\b(?:"
    r"I hope (?:this|that) helps"
    r"|(?:please )?feel free to (?:ask|reach out|let me know)"
    r"|let me know if you(?:'d| woul)d like"
    r"|great question"
    r"|certainly!"
    r"|as an AI(?: language model)?"
    r"|as of my (?:knowledge |last )?(?:cutoff|update)"
    r"|I (?:may|might|do) not have (?:access to )?"
    r"(?:current|real-time|up-to-date|the latest)"
    r"|my training data"
    r"|is indeed an important (?:area|topic|question|field|aspect)"
    r"|(?:have|has) done (?:well|a good job) (?:to|in|at)"
    r")",
    re.IGNORECASE)


META_FLAG = ("**[FLAG: meta-prose — pipeline text, not manuscript text]**")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

# --- PAPER KIND (specs/review-paper.md 1) ---------------------------------
# A verbatim-in-behaviour copy of manuscript.py's reader, for the reason
# every engine here is standalone: reading one key out of project.yml must
# not cost an interpreter spawn. Absent reads as `research`, and so does
# anything unrecognised - a typo must never put a project into the mode that
# RELEASES checks. tests/prose.py holds the two readers against each other.
PAPER_KINDS = ("research", "review")


def paper_kind(project: str) -> str:
    path = os.path.join(project, "project.yml")
    for line in _read(path).splitlines():
        m = re.match(r"^paper_kind\s*:\s*(.*)$", line)
        if m:
            value = m.group(1).split("#", 1)[0].strip().strip("\"'")
            return value if value in PAPER_KINDS else "research"
    return "research"
# --- end PAPER KIND -------------------------------------------------------


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _blank(match: re.Match) -> str:
    """Replace a span with spaces of the same length.

    Offsets and parenthesis balance both have to survive masking: the cluster
    finder runs on the masked text and would pair the wrong brackets if a
    masked span changed length.
    """
    return " " * (match.end() - match.start())


def _mask(text: str, pattern: re.Pattern) -> str:
    return pattern.sub(_blank, text)


def _strip_comments(text: str) -> str:
    """Drop HTML comments, keeping the line count intact.

    A comment is replaced by the newlines it spanned rather than by nothing.
    Every location this module reports - `outline.md:19`, a paragraph's
    `line` - is counted after this strip, and the scaffolded outline.md opens
    with a fourteen-line instruction comment the user is explicitly told they
    may leave in place. Deleting those lines shifted every reported line
    number by the length of a comment nobody thought of as content, so the
    file:line a coauthor was pointed at was the wrong one for the whole early
    life of a project. Same instinct as _blank(): a mask must not change
    what it is masking.
    """
    return re.sub(r"<!--.*?-->",
                  lambda m: "\n" * m.group(0).count("\n"),
                  text, flags=re.DOTALL)


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


# What may open a sentence here. Wider than review.py's, which is the
# difference this engine always had: a manuscript sentence may open on a
# number, a quotation mark or a bold marker.
OPENS_A_SENTENCE = r"[A-Z0-9\"'(\[*]"


def _sentences(text: str) -> list[str]:
    """Split into sentences, keeping "et al." and friends intact.

    Same abbreviation problem docx_edits.py has, same answer: a period after a
    known abbreviation is not a sentence end. "et al." appears in every
    paragraph that cites anything.
    """
    abbrev = (r"et al|e\.g|i\.e|cf|vs|approx|ca|Fig|Figs|Tab|Eq|Ref|Refs|"
              r"Dr|Prof|Mr|Ms|St|no|No|min|max|sp|spp|etc")
    protected = re.sub(r"\b(" + abbrev + r")\.", r"\1<DOT>", text)
    # Boundaries rather than a split, because the citation run between the
    # period and the space belongs to the sentence BEFORE it and a split
    # would drop it (SENTENCE BOUNDARY CONTRACT above). The acceptance rule -
    # what may open the next sentence - is this engine's own and unchanged.
    out: list[str] = []
    start = 0
    for m in SENTENCE_BOUNDARY_RE.finditer(protected):
        nxt = protected[m.end():m.end() + 1]
        if not nxt or not re.match(OPENS_A_SENTENCE, nxt):
            continue
        piece = protected[start:m.end("cite")].strip()
        if piece:
            out.append(piece.replace("<DOT>", "."))
        start = m.end()
    tail = protected[start:].strip()
    if tail:
        out.append(tail.replace("<DOT>", "."))
    return out


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"\s+", re.sub(r"[*_`#]", "", text)) if w]


# ---------------------------------------------------------------------------
# Paragraph extraction
# ---------------------------------------------------------------------------

def paragraphs(text: str) -> list[dict]:
    """Prose paragraphs of a section file, in order, numbered from 1.

    Headings, HTML comments, fenced code, tables and list blocks are not prose
    and are excluded - a markdown table is exactly the place the density rule
    wants numbers to go, so counting it would fire the check on the remedy.
    """
    text = _strip_comments(text)
    text = re.sub(r"^```.*?^```", "", text, flags=re.DOTALL | re.MULTILINE)
    out: list[dict] = []
    n = 0
    line_no = 1
    for block in re.split(r"\n\s*\n", text):
        start_line = line_no
        line_no += block.count("\n") + 1
        stripped = block.strip()
        if not stripped:
            continue
        lines = [l for l in stripped.splitlines() if l.strip()]
        if all(l.lstrip().startswith("#") for l in lines):
            continue
        if any(l.lstrip().startswith("|") for l in lines):
            continue
        if all(re.match(r"^\s*([-*+]|\d+\.)\s", l) for l in lines):
            continue
        body = "\n".join(l for l in lines if not l.lstrip().startswith("#"))
        if not body.strip():
            continue
        n += 1
        out.append({"n": n, "line": start_line, "text": body.strip()})
    return out


# ---------------------------------------------------------------------------
# Number counting - spec 6.1
# ---------------------------------------------------------------------------

# Units, so "50 mM" and "50mM" are quantities while "16S" and "Cas9" are not.
# Without this the check fires on every mention of 16S rRNA and becomes noise
# the user learns to ignore, which is worse than no check (spec 6.1).
#
# CASE-SENSITIVE, and that is the whole trick: "16S" and "50 s" differ only in
# the case of one letter, and a case-folded list makes the 16S rRNA gene a
# quantity again. Same for T4 vs. tesla and G2 vs. g-force - the nomenclature
# reading wins on the capital.
UNITS = {
    "h", "hr", "hrs", "min", "s", "sec", "ms", "us", "µs", "ns", "ps", "d",
    "wk", "yr", "nm", "µm", "um", "mm", "cm", "m", "km", "in", "ft",
    "mg", "g", "kg", "ng", "pg", "µg", "ug", "mL", "ml", "L", "l", "µL",
    "uL", "ul", "nL", "mol", "mmol", "µmol", "umol", "nmol", "pmol",
    "M", "mM", "µM", "uM", "nM", "pM", "K", "C", "F", "°C", "°F",
    "Da", "kDa", "MDa", "bp", "kb", "nt", "aa",
    "V", "mV", "kV", "Hz", "kHz", "MHz", "GHz", "ppm", "ppb", "rpm",
    "W", "mW", "kW", "eV", "keV", "MeV", "kcal", "kJ", "J", "N",
    "Pa", "kPa", "MPa", "bar", "atm", "Torr",
    "dpi", "px", "fps", "x", "fold", "%",
}

NUM_TOKEN_RE = re.compile(r"(?<![\w.])[<>≤≥~±+-]?\d+(?:[.,]\d+)*(?:\s*%)?")

_MASKS = [
    # Order matters: code spans and links first, so their contents cannot be
    # re-matched by a later pattern that would unbalance the parentheses.
    re.compile(r"`[^`]*`"),
    re.compile(r"!?\[[^\]\[]*\]\([^)]*\)"),
    re.compile(r"\*\*\[FLAG:.*?\]\*\*", re.DOTALL),
    re.compile(r"\[[^\]\[]*@[^\]\[]*\]"),                   # [@key], [@a; @b]
    re.compile(r"\[\d+(?:\s*[,–-]\s*\d+)*\]"),         # a rendered [7]
    re.compile(
        r"\b(?:Figures?|Figs?\.?|Tables?|Tabs?\.?|Schemes?|Panels?|Sections?|"
        r"Equations?|Eqs?\.?|Supplementary\s+(?:Figures?|Tables?)|"
        r"Movies?|Videos?|Appendix|Appendices)\s*S?\d+[A-Za-z]?"
        r"(?:\s*[,–-]\s*S?\d+[A-Za-z]?)*", re.IGNORECASE),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),                   # ISO date
    re.compile(r"\b(?:1[5-9]\d{2}|20\d{2})\b"),             # a year
    re.compile(
        r"\b(?:cat(?:alog|\.)?\s*(?:no\.?|#)|lot\s*(?:no\.?|#)?|"
        r"protocol\s*(?:no\.?|#)?|IRB\s*#?|grant\s*(?:no\.?|#)?|"
        r"accession\s*(?:no\.?|#)?)\s*\S+", re.IGNORECASE),
    re.compile(r"\b[A-Z]{1,4}\d{2,}(?:[-/][A-Z0-9]+)*\b"),  # R01-CA123456
]


def _mask_nomenclature(text: str) -> str:
    """Blank out chemical and biological names, and instrument models.

    A digit glued to letters is nomenclature (16S, Cas9, H2O, p53) unless the
    letters are a unit (50mM, 4h). The space-separated case ("50 mM") never
    reaches here, because there the digits already stand alone as a token.
    """
    def repl(m: re.Match) -> str:
        tok = m.group(0)
        letters = re.sub(r"[\d.]", "", tok)
        if letters in UNITS:
            return tok
        return " " * len(tok)
    return re.sub(r"\b(?:[A-Za-z]+\d+[A-Za-z\d]*|\d+[A-Za-z]+)\b", repl, text)


def mask_excluded(text: str) -> str:
    """Everything the density rule does not count, blanked to spaces."""
    for pat in _MASKS:
        text = _mask(text, pat)
    return _mask_nomenclature(text)


def count_numbers(paragraph: str) -> dict:
    """Two currencies: inline numbers, and parenthetical clusters.

    A grouped parenthetical is one object to the eye however many values it
    holds; a number in the sentence flow has to be read to parse the sentence.
    Counting them the same way is what made the old flat cap useless.
    """
    masked = mask_excluded(paragraph)

    clusters: list[dict] = []
    cluster_spans: list[tuple[int, int]] = []
    for m in re.finditer(r"\(([^()]*)\)", masked):
        nums = NUM_TOKEN_RE.findall(m.group(1))
        if nums:
            clusters.append({"text": paragraph[m.start():m.end()],
                             "values": [n.strip() for n in nums]})
            cluster_spans.append((m.start(), m.end()))

    inline: list[dict] = []
    for m in NUM_TOKEN_RE.finditer(masked):
        if any(s <= m.start() < e for s, e in cluster_spans):
            continue
        inline.append({"value": m.group(0).strip(), "at": m.start()})

    words = _words(_strip_comments(paragraph))
    numeric_tokens = len(inline) + sum(len(c["values"]) for c in clusters)
    # The exact fraction is kept alongside the rounded one: rounding 5/15 to
    # 0.333 puts it under a 1/3 cap it actually sits exactly on, and a cap
    # test has to be made on the number, not on its display form.
    exact = numeric_tokens / len(words) if words else 0.0
    return {
        "words": len(words),
        "inline": inline,
        "clusters": clusters,
        "numeric_tokens": numeric_tokens,
        "numeric_fraction": round(exact, 3),
        "numeric_fraction_exact": exact,
    }


def _leads_with_a_number(paragraph: str) -> bool:
    """Rule 6: a results paragraph opens with the finding, not the measurement.

    Checkable as: does the opening clause of the first sentence carry a number
    before it has said anything.
    """
    sents = _sentences(paragraph)
    if not sents:
        return False
    opening = " ".join(mask_excluded(sents[0]).split()[:6])
    return bool(NUM_TOKEN_RE.search(opening))


def resolve_density_budget(section: str, level: str | None = None,
                           config: dict | None = None,
                           learned: dict | None = None,
                           problems: list[str] | None = None) -> dict:
    """One section's number budget, and where it came from (prose 5.9).

    The four-step order, first that exists:

      1. the section's entry in this project's writing_config.yml
      2. an `active` learned budget for (research_type, section)
      3. an `active` learned budget for a PARENT research type
      4. the built-in default

    Steps 2 and 3 arrive pre-resolved in `learned`, keyed by section, each
    carrying the rule id it came from - `learn.py budgets --json` produces
    exactly that shape, and the id is what makes a finding's threshold
    auditable instead of merely applied.

    Order 1 above 2 is not decoration: a value the user typed into this
    project's config is a statement about THIS paper and has to beat a lesson
    learned from a different one.

    A forced `level` - `--level`, or the scalar `number_density: low` spelling -
    wins over all four, because that is what it meant before this existed and
    an existing project's config cannot change meaning under it.
    """
    spec: object | None = None
    source = "default"
    if level:
        spec, source = level, "forced"
    elif config and section in config:
        spec, source = config[section], "config"
    elif learned and section in learned:
        spec = learned[section]
        source = str(learned[section].get("source") or "learned") \
            if isinstance(learned[section], dict) else "learned"
    elif section in SECTION_DENSITY_DEFAULTS:
        spec = SECTION_DENSITY_DEFAULTS[section]
    else:
        # A file that is not one of the five known sections still gets counted;
        # `low` is the setting the toolkit shipped with.
        spec = "low"

    out = {"inline": None, "clusters": None, "basis": "per100",
           "level": None, "source": source}

    if isinstance(spec, str):
        if spec not in DENSITY_BUDGETS:
            if problems is not None:
                problems.append(
                    f"number_density for {section}: {spec!r} is not one of "
                    f"{', '.join(DENSITY_BUDGETS)} and is not a rate - the "
                    f"built-in default is being used instead, so the budget "
                    f"is not the one written")
            return resolve_density_budget(section, None, None, None, problems)
        inline, clusters = DENSITY_BUDGETS[spec]
        out.update({"inline": inline, "clusters": clusters, "level": spec})
        return out

    if isinstance(spec, dict):
        basis = str(spec.get("basis") or "per100")
        if basis not in DENSITY_BASES:
            if problems is not None:
                problems.append(
                    f"number_density for {section}: basis {basis!r} is not one "
                    f"of {', '.join(DENSITY_BASES)}; read as per100")
            basis = "per100"
        try:
            inline = (float(spec["inline"]) if spec.get("inline") is not None
                      else None)
            clusters = (float(spec["clusters"])
                        if spec.get("clusters") is not None else None)
        except (TypeError, ValueError):
            if problems is not None:
                problems.append(
                    f"number_density for {section}: inline and clusters have "
                    f"to be numbers; the built-in default is being used")
            return resolve_density_budget(section, None, None, None, problems)
        out.update({"inline": inline, "clusters": clusters, "basis": basis,
                    "level": spec.get("level")})
        return out

    if problems is not None:
        problems.append(f"number_density for {section}: cannot read "
                        f"{spec!r}; the built-in default is being used")
    return resolve_density_budget(section, None, None, None, problems)


def density(paths: list[str], level: str | None = None,
            waivers: list[str] | None = None,
            config: dict | None = None,
            learned: dict | None = None,
            advisory: list[str] | None = None,
            released: bool = False) -> dict:
    """The number budget, resolved per section (spec 6.1, prose 5.9).

    `level` forces one level on every section - the old signature, and the
    scalar `number_density:` spelling. Left None, each section resolves its
    own budget through resolve_density_budget().

    `advisory` names sections or paragraphs whose budget findings are reported
    without warning severity: a paragraph with a logged override (prose 0.1) or
    a section the user has taken over (prose 4.3). The two absolute rules of
    spec 6.1 are NOT softened by it - a hand-written paragraph that is half
    digits is still a table wearing a paragraph's clothes.

    `released` is the one thing that DOES soften them, and it is set by the
    paper kind rather than by a dial (specs/review-paper.md 4.1). Spec 6 and
    6.1 were written against a results section reporting its own
    measurements, where a wall of numbers means the author has not decided
    what the finding is. In a review, comparing quantities across studies IS
    the substance: a paragraph putting six groups' reported coverages side by
    side is the paragraph doing its job, and a table's worth of values is a
    table's worth of the review's contribution.

    Every rule this releases is layer 4 - presentation, overridable with a
    reason on the record - which is exactly why it can be released without
    touching anything that matters. What is NOT released, and cannot be by
    anything here, is where each number came from: that is layer 0, it lives
    in numbers() and in `review.py tier`, and it gets STRICTER in review
    mode, not looser.
    """
    waivers = waivers or []
    advisory = advisory or []
    problems: list[str] = []
    findings: list[dict] = []
    paras_out: list[dict] = []
    budgets: dict[str, dict] = {}

    def is_advisory(stem: str, loc: str) -> bool:
        return any(a.strip() in (stem, loc) or
                   a.strip().startswith(loc + " ") for a in advisory)

    for path in paths:
        stem = os.path.splitext(os.path.basename(path))[0]
        text = _read(path)
        if not text.strip():
            findings.append({"location": stem, "rule": "missing",
                             "severity": "error",
                             "detail": f"{path} is empty or unreadable"})
            continue
        budget = resolve_density_budget(stem, level, config, learned, problems)
        budgets[stem] = budget
        enforce = (budget["inline"], budget["clusters"])
        absolute = budget["basis"] == "absolute"
        totals = {"inline": 0, "clusters": 0, "words": 0, "paragraphs": 0}

        for p in paragraphs(text):
            loc = f"{stem} ¶{p['n']}"
            counts = count_numbers(p["text"])
            waived = any(w.strip() == loc or w.strip().startswith(loc + " ")
                         for w in waivers)
            soft = is_advisory(stem, loc)
            rec = {"location": loc, "line": p["line"], "waived": waived,
                   "advisory": soft,
                   "words": counts["words"],
                   "inline": [i["value"] for i in counts["inline"]],
                   "clusters": [c["text"] for c in counts["clusters"]],
                   "numeric_tokens": counts["numeric_tokens"],
                   "numeric_fraction": counts["numeric_fraction"],
                   "budget_source": budget["source"],
                   "budget": None}
            if not waived:
                totals["inline"] += len(counts["inline"])
                totals["clusters"] += len(counts["clusters"])
                totals["words"] += counts["words"]
                totals["paragraphs"] += 1
            if not absolute and enforce[0] is not None and counts["words"]:
                # Proration scales the budget UP for a long paragraph and
                # never down below the base. Measured: the spec's own model
                # sentence in 6 rule 1 is 47 words and carries two inline
                # numbers, so a strictly proportional budget of 0.94 flags the
                # example the spec holds up as correct. A paragraph shorter
                # than 100 words still gets to make one complete comparison.
                scale = max(1.0, counts["words"] / 100.0)
                rec["budget"] = {"inline": round(enforce[0] * scale, 2),
                                 "clusters": round(enforce[1] * scale, 2)}
            paras_out.append(rec)

            if waived:
                continue

            # The two absolute rules survive any density SETTING. They do
            # not survive the paper kind: a review comparing six groups'
            # reported values across one paragraph is over a third numeric
            # and is correct, and the remedy the rule offers - move it to a
            # table - is advice rather than a defect there.
            if counts["numeric_fraction_exact"] > NUMERIC_FRACTION_CAP:
                findings.append({
                    "location": loc, "rule": "numeric_fraction",
                    "severity": "advisory" if released else "error",
                    "detail": f"{counts['numeric_tokens']} numeric tokens in "
                              f"{counts['words']} words "
                              f"({counts['numeric_fraction']:.0%}); over the "
                              f"one-third cap"
                              + (" - reported, not enforced: comparing "
                                 "quantities across studies is a review's "
                                 "substance"
                                 if released else
                                 " regardless of density setting"),
                    "remedy": "move the series to a table"})

            named = budget["level"] or f"{budget['basis']}"
            sev = "advisory" if (soft or released) else "warning"
            if rec["budget"]:
                if len(counts["inline"]) > rec["budget"]["inline"]:
                    findings.append({
                        "location": loc, "rule": "inline_budget",
                        "severity": sev,
                        "detail": f"{len(counts['inline'])} inline numbers, "
                                  f"budget {rec['budget']['inline']} at "
                                  f"{named} over {counts['words']} words "
                                  f"({budget['source']})",
                        "values": [i["value"] for i in counts["inline"]],
                        "budget_source": budget["source"],
                        "remedy": "group loose numbers into one parenthetical, "
                                  "or move the series to a table"})
                if len(counts["clusters"]) > rec["budget"]["clusters"]:
                    findings.append({
                        "location": loc, "rule": "cluster_budget",
                        "severity": sev,
                        "detail": f"{len(counts['clusters'])} parenthetical "
                                  f"clusters, budget {rec['budget']['clusters']} "
                                  f"at {named} over {counts['words']} words "
                                  f"({budget['source']})",
                        "budget_source": budget["source"],
                        "remedy": "the full statistical apparatus belongs in "
                                  "the caption or the stats table"})

            if stem == "results" and _leads_with_a_number(p["text"]):
                findings.append({
                    "location": loc, "rule": "opens_with_measurement",
                    "severity": sev,
                    "detail": "the paragraph opens on a number rather than on "
                              "the finding (layer 2 rule 7)",
                    "remedy": "pose the question, give the data, answer it - "
                              "the finding is the answer, not the measurement"})

        # `basis: absolute` counts the section, not a rate. The abstract is the
        # one section this exists for: a rate over 250 words is noise, and the
        # total is what the reader meets all at once.
        if absolute and enforce[0] is not None:
            soft = is_advisory(stem, stem)
            sev = "advisory" if (soft or released) else "warning"
            if totals["inline"] > enforce[0]:
                findings.append({
                    "location": stem, "rule": "section_inline_budget",
                    "severity": sev,
                    "detail": f"{totals['inline']} inline numbers in the whole "
                              f"section, budget {enforce[0]:g} "
                              f"({budget['source']}, absolute)",
                    "budget_source": budget["source"],
                    "remedy": "the numbers that belong here are the ones the "
                              "take-home claim needs"})
            if enforce[1] is not None and totals["clusters"] > enforce[1]:
                findings.append({
                    "location": stem, "rule": "section_cluster_budget",
                    "severity": sev,
                    "detail": f"{totals['clusters']} parenthetical clusters in "
                              f"the whole section, budget {enforce[1]:g} "
                              f"({budget['source']}, absolute)",
                    "budget_source": budget["source"],
                    "remedy": "one cluster is an apparatus; three is a table"})
        budgets[stem]["observed"] = dict(totals)

    for problem in problems:
        findings.append({"location": "writing_config.yml",
                         "rule": "unreadable_budget", "severity": "warning",
                         "detail": problem})

    return {"level": level or "per-section", "forced": level,
            "waivers": waivers, "advisory": advisory,
            "budgets": budgets, "problems": problems,
            "paragraphs": paras_out, "findings": findings,
            "counts": {"paragraphs": len(paras_out),
                       "findings": len(findings)}}


# --- LAYOUT CONTRACT (verbatim in manuscript.py, prose.py, toc_graphic.py) ---
# Every command in this toolkit is standalone by design, so this resolver is
# repeated rather than imported, exactly like CAPTION_HEADING_RE.
# `tests/prose.py` compares the three byte for byte. Edit one, edit all three.

# What makes a folder a journal folder rather than a sibling of one. Any of
# these is enough: writing_config.yml is written by `init`, and the other two
# survive a project whose config was deleted or never committed.
JOURNAL_MARKERS = ("writing_config.yml", "journal_requirements", "edits")

# The live source text carries the round it holds: `source_text_r8`.
#
# One counter runs across every journal in a project (item 18), so a LANGMUIR
# folder that stopped at r5 can sit beside source text that has moved on to
# r8, and nothing on disk said so: `source_text/` looked the same on the day
# r5 was sent and on the day r8 was drafted. The label is the whole answer -
# `manuscript_r5.docx` next to `source_text_r8/` is visibly not what would
# build today.
#
# An unsuffixed `source_text/` is the layout that predates the label and is
# still read. `init` and `round` rename it forward rather than leaving two,
# because two folders holding a manuscript is the one thing this resolver has
# never been able to answer.
SOURCE_TEXT_RE = re.compile(r"^source_text(?:_r(\d+))?$")


def source_text_label(rnd: int) -> str:
    """The folder name for a given round; 0 means no round is known yet."""
    return "source_text_r%d" % rnd if rnd and rnd > 0 else "source_text"


def source_text_folders(d: str) -> list[tuple[int, str]]:
    """(round, name) for every source_text folder in `d`, oldest first.

    The unsuffixed one sorts as round 0: it predates the label, so any folder
    carrying one is newer than it. Callers take the LAST, never the first - a
    project part-way through the rename holds both, and the newest is live.
    """
    out: list[tuple[int, str]] = []
    if not os.path.isdir(d):
        return out
    for name in os.listdir(d):
        m = SOURCE_TEXT_RE.match(name)
        if m and os.path.isdir(os.path.join(d, name)):
            out.append((int(m.group(1) or 0), name))
    return sorted(out)


def _looks_like_journal(path: str) -> bool:
    return any(os.path.exists(os.path.join(path, m)) for m in JOURNAL_MARKERS)


def _is_drafting_folder(d: str) -> bool:
    """Does this directory hold a manuscript, rather than merely a folder?

    Two independent marks, because either can outlive the other. Written
    sections are the obvious one. A journal folder is the one that matters
    after a clean slate: a trimmed project whose prose has been deleted so the
    skill can redraft it still has `Langmuir/`, and without this second mark
    the project would silently flip back to expecting `drafts/` at the exact
    moment it was emptied - and the redraft would land somewhere else.

    An empty `source_text*/` with no journal beside it is deliberately NOT a
    drafting folder. Nothing has been created there yet, so it is scaffolded
    like any other bare directory.
    """
    if not os.path.isdir(d):
        return False
    for _rnd, name in source_text_folders(d):
        st = os.path.join(d, name)
        if any(f.endswith(".md") for f in os.listdir(st)):
            return True
    return any(_looks_like_journal(os.path.join(d, x))
               for x in os.listdir(d) if os.path.isdir(os.path.join(d, x)))


def drafts_dir(project: str) -> str:
    """The folder holding source_text/, the journal folders and the .bib.

    In a full project that is `drafts/`. In a **trimmed** project - source_text/
    plus a journal folder, with data and figures managed elsewhere (spec 7.2.2)
    - the project root IS the drafting folder and there is no drafts/ wrapper.
    That layout was supported here and in toc_graphic.py but hard-refused by
    manuscript.py and prose.py at 33 sites, which is why `status` on a real
    trimmed project reported five empty sections while 5262 words sat one
    directory up. A wrong answer at exit 0 is worse than the refusal it
    replaced.

    Resolution is by content, never by the folder name: the wrapper can be
    called anything (`manuscript/` is what the first real project used). When
    neither location holds a manuscript nothing has been created yet, so
    `drafts/` is returned as the layout `init` will build.
    """
    full = os.path.join(project, "drafts")
    if _is_drafting_folder(full):
        return full
    if _is_drafting_folder(project):
        return project
    return full


def is_trimmed(project: str) -> bool:
    """True when the project root is its own drafting folder (no drafts/)."""
    return os.path.abspath(drafts_dir(project)) == os.path.abspath(project)


def dpfx(project: str) -> str:
    """The `drafts/` prefix as it should appear in a message, or "".

    Every message naming a file the user is meant to open has to name the path
    they will actually find it at. A trimmed project told to edit
    `drafts/source_text/results.md` is sent looking for a folder that is not
    there, and a path that is wrong in a diagnostic is worse than no path at
    all - it is the diagnostic they will trust.
    """
    return "" if is_trimmed(project) else "drafts/"


def source_text_name(project: str) -> str:
    """The live source_text folder's own name, round label and all.

    The newest label wins. Nothing at all means nothing has been created here
    yet, and the unsuffixed name is what a caller would build - the same rule
    `drafts_dir` applies when neither location holds a manuscript.
    """
    found = source_text_folders(drafts_dir(project))
    return found[-1][1] if found else "source_text"


def source_text_dir(project: str) -> str:
    return os.path.join(drafts_dir(project), source_text_name(project))


def stpfx(project: str) -> str:
    """The live source_text folder as it should appear in a message.

    `drafts/source_text_r8/` in a full project, `source_text_r8/` in a trimmed
    one. Every diagnostic naming a file inside it runs through this, for the
    reason `dpfx` exists one line up: once the folder carries a round label
    there is no `source_text/` to open, and a path that is wrong in a
    diagnostic is worse than no path at all.
    """
    return dpfx(project) + source_text_name(project) + "/"


# The inbox is the PROJECT's, and since 2026-09-24 it sits beside source_text/
# rather than inside the journal folder: `drafts/edits/`.
#
# It moved because of what a coauthor actually does. They are sent a manuscript
# and they send one back, into whatever folder they were pointed at last time.
# Nobody outside the project knows or cares which journal this round is aimed
# at, and a return dropped in `drafts/IJROBP/edits/` after the paper moved to
# Langmuir is a file `ingest` does not read and nothing reports as missing.
# `retire-journal` carried a whole refusal path to walk the ledger across that
# boundary by hand; one inbox a level up makes the boundary disappear.
#
# THE PER-JOURNAL LOCATION IS STILL READ, AND IS MOVED FOR NOBODY. A project
# that already has `drafts/<Journal>/edits/` keeps using it - the same
# read-only rule the front-matter move follows (`front_matter_candidates`).
# `ingest` matches a return against the fingerprint the last round wrote, and
# an inbox that relocates under a round part-way through ingest loses that
# match. New projects and new journal folders get the new place.
EDITS_FOLDER = "edits"


def edits_dir(jdir: str) -> str:
    """The inbox for the project this journal folder belongs to.

    Takes the JOURNAL folder rather than the project, because that is what
    every caller already has in hand and because the older location is a child
    of it - one resolves from the other with no second argument to get wrong.
    """
    old = os.path.join(jdir, EDITS_FOLDER)
    if os.path.isdir(old):
        return old
    return os.path.join(os.path.dirname(os.path.normpath(jdir)), EDITS_FOLDER)


def edits_pfx(project: str, jdir: str) -> str:
    """The inbox as it should appear in a message, project-relative.

    `drafts/edits/` in a new project, `drafts/IJROBP/edits/` in one that
    predates the move, `edits/` in a trimmed one. Every diagnostic naming a
    file in the inbox runs through this, for the reason `dpfx` and `stpfx`
    exist above: a path that is wrong in a diagnostic is worse than no path at
    all - it is the diagnostic they will trust.
    """
    rel = os.path.relpath(edits_dir(jdir), project).replace(os.sep, "/")
    return rel + "/"


def bib_path(project: str) -> str:
    return os.path.join(drafts_dir(project), "references.bib")
# --- END LAYOUT CONTRACT ----------------------------------------------------


# A review's body stem: a two-digit order prefix and a slug
# (review-paper 7). The prefix carries document order and is the only thing
# that does.
REVIEW_STEM_RE = re.compile(r"^(\d{2})_[a-z0-9_]+$")


def section_slug(heading: str) -> str:
    """`Disease Pathology` -> `disease_pathology`.

    manuscript.py's copy is the WRITER - it is what names the file - and this
    one is the reader that has to arrive at the same name from the same
    heading, or an outline line is compared against nothing. Repeated rather
    than imported for the reason every engine here is standalone, and
    tests/prose.py holds the two copies against each other.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", heading.strip().lower()).strip("_")
    return slug[:48] or "section"


def section_stems(project: str) -> list[str]:
    """The stems this project's body is written in, in document order.

    For a research paper, SECTION_FILES. For a review, `title_abstract`
    plus every `NN_slug.md` on disk, sorted by its prefix.

    Read from the FOLDER rather than derived from plan/outline.md, and that
    is deliberate rather than lazy. manuscript.py derives the stems from the
    outline because it decides what SHOULD be drafted; this engine measures
    what IS drafted, and a second copy of that derivation here would be a
    contract duplicated with nothing comparing the copies. `prose.py
    outline` already holds the two against each other from the other
    direction - it is the command whose whole job is reporting an outline
    line no paragraph implements, and a heading with no file is exactly
    that.
    """
    st = source_text_dir(project)
    if paper_kind(project) != "review":
        return list(SECTION_FILES)
    stems = ["title_abstract"]
    try:
        names = sorted(os.listdir(st))
    except OSError:
        return stems
    body = [n[:-3] for n in names
            if n.endswith(".md") and REVIEW_STEM_RE.match(n[:-3])]
    return stems + sorted(body)


def body_stems(project: str) -> list[str]:
    """The stems a journal counts as body, in document order.

    Everything `section_stems` returns except `title_abstract`, which carries
    the title page and the abstract and is counted separately because both
    have their own caps. There is no third category to exclude: the reference
    list, the captions and the end matter are not section files.
    """
    return [s for s in section_stems(project) if s != "title_abstract"]


def source_files(project: str) -> list[str]:
    st = source_text_dir(project)
    return [os.path.join(st, f"{s}.md") for s in section_stems(project)
            if os.path.isfile(os.path.join(st, f"{s}.md"))]


def read_captions(project: str) -> list[dict]:
    """Parse plan/captions.md exactly as read_captions.R does.

    Reproduced rather than approximated, and reproduced from the same regex:
    a block the R parser cannot see must be invisible here too, or the two
    halves of the pipeline disagree about what the float set is.
    """
    path = os.path.join(project, "plan", "captions.md")
    text = _strip_comments(_read(path))
    entries: list[dict] = []
    current: dict | None = None
    body: list[str] = []

    def flush() -> None:
        nonlocal current, body
        if current is None:
            return
        txt = [l.strip() for l in body if l.strip()]
        subtitle = ""
        if txt and re.match(r"^\*\*.*\*\*$", txt[0]):
            subtitle = txt[0].strip("*").strip()
            txt = txt[1:]
        current["subtitle"] = subtitle
        current["caption"] = " ".join(txt)
        current["order"] = len(entries) + 1
        entries.append(current)
        current = None
        body = []

    for line in text.splitlines():
        m = CAPTION_HEADING_RE.match(line)
        if m:
            flush()
            num = m.group(2)
            current = {"type": m.group(1), "number": num,
                       "supplementary": num.startswith("S"),
                       "file": m.group(3),
                       "label": f"{m.group(1)} {num}"}
            body = []
        elif current is not None:
            body.append(line)
    flush()
    return entries


def _is_notes_heading(section: str) -> bool:
    """Is this heading the notes region?

    Matched on the first words rather than on the whole heading, because the
    scaffolded one is `# Notes - anything, in any order`: the region has to be
    labelled on screen for anyone to use it, and a label is not a slug.
    """
    clean = re.sub(r"[^a-z ]+", " ", section.lower())
    clean = " ".join(clean.split())
    if clean in NOTES_SECTIONS:
        return True
    return any(clean == n or clean.startswith(n + " ")
               for n in NOTES_SECTIONS)


def _is_structure_heading(section: str) -> bool:
    clean = " ".join(re.sub(r"[^a-z ]+", " ", section.lower()).split())
    return clean in STRUCTURE_SECTIONS


def parse_outline_notes(text: str) -> list[str]:
    """The notes block of any outline text, verbatim.

    Split from read_outline_notes so a file the user points at with
    `outline --from` is read by the same grammar as plan/outline.md. One
    parser, two callers: a file that reads as an outline in one place has to
    read as one in the other, or "point me at it" quietly means something
    different from "it is already in place".
    """
    out: list[str] = []
    keeping = False
    for line in _strip_comments(text).splitlines():
        sm = OUTLINE_SECTION_RE.match(line)
        if sm:
            keeping = _is_notes_heading(sm.group(1).strip())
            continue
        if not keeping or not line.strip():
            continue
        # The one-line rule the region is labelled with is the template's own
        # furniture, not a note somebody wrote. An emphasis-only line is that
        # label and nothing else, so it is not counted - otherwise every
        # fresh scaffold reports a note nobody has written.
        if EMPHASIS_ONLY_RE.match(line.strip()):
            continue
        out.append(line.rstrip())
    return out


def parse_outline_note_bullets(text: str) -> list[str]:
    """The notes region's bullets, without their markers.

    What a note actually is: one bullet, one finding. The engine names them
    back to the user when it proposes an outline from them, so "the six
    bullets under ## Notes" has to be a list and not a blob (item 23).
    """
    out: list[str] = []
    for line in parse_outline_notes(text):
        m = OUTLINE_LINE_RE.match(line)
        if m and m.group(1).strip() not in {"-", "*"}:
            out.append(m.group(1).strip())
    return out


def read_outline_notes(project: str) -> list[str]:
    """The notes block at the bottom of outline.md, verbatim.

    Read separately from the claims for the same reason it is skipped there:
    it is the place for everything that is not a paragraph, and it is what
    keeps the paragraph lines themselves short.
    """
    return parse_outline_notes(_read(os.path.join(project, "plan",
                                                  "outline.md")))


# A proposed outline is one nobody has agreed to yet. It is marked in the file
# itself rather than in a sidecar, because the file is what the user opens and
# what a coauthor sees, and a claims ledger that looks authored when it was
# guessed is the one failure this whole path exists to avoid. The mark is a
# blockquote: it matches neither OUTLINE_SECTION_RE nor OUTLINE_LINE_RE, so it
# is invisible to every parser and visible to every reader.
OUTLINE_PROPOSED_MARK = "**PROPOSED OUTLINE - not approved yet.**"


def outline_proposed(project: str) -> bool:
    """Is the outline in place one Claude proposed and nobody has accepted?

    Orthogonal to the state: a proposed outline is `present` and parses like
    any other. What it is not is the user's, and every verdict computed
    against it inherits that.
    """
    return outline_text_proposed(_read(os.path.join(project, "plan",
                                                    "outline.md")))


def outline_text_proposed(text: str) -> bool:
    return OUTLINE_PROPOSED_MARK in text


def outline_state(project: str) -> str:
    """missing | empty | present - the three cases a caller has to tell apart.

    "No outline yet" and "an outline with no lines in it yet" want the same
    offer from the skill, but only one of them means a file has to be
    created, and reporting either as an error made the difference invisible.
    """
    path = os.path.join(project, "plan", "outline.md")
    if not os.path.isfile(path):
        return "missing"
    return "present" if read_outline(project) else "empty"


def read_outline(project: str) -> list[dict]:
    """plan/outline.md is one line per paragraph, with its evidence bracket.

    The outline is the claims ledger (spec 5.1), so this parse is what
    evidence-check and the deviation check both read - neither re-derives
    claims from finished prose.
    """
    return parse_outline(_read(os.path.join(project, "plan", "outline.md")))


def parse_outline(text: str) -> list[dict]:
    """The outline grammar, applied to any text rather than to the project.

    `outline --from <path>` adopts an outline the user keeps somewhere else,
    and the only honest way to answer "is that an outline?" is to read it with
    exactly the parser every downstream check uses. Line numbers are counted
    after comments are stripped, the same as they are in the file.
    """
    text = _strip_comments(text)
    section = ""
    in_notes = False
    out: list[dict] = []
    for i, line in enumerate(text.splitlines(), 1):
        sm = OUTLINE_SECTION_RE.match(line)
        if sm:
            head = sm.group(1).strip()
            in_notes = _is_notes_heading(head)
            # `# Structure` opens the paragraph plan and names no section of
            # the paper; the `##` under it does that.
            section = "" if (in_notes or _is_structure_heading(head)) else head
            continue
        if in_notes:
            continue
        lm = OUTLINE_LINE_RE.match(line)
        if not lm:
            continue
        body = lm.group(1).strip()
        if not body or body in {"-", "*"}:
            continue
        waived = None
        wm = OUTLINE_WAIVED_RE.match(body)
        if wm:
            waived = wm.group(1).strip()
            body = body[wm.end():].strip()
            if not body:
                continue
        em = EVIDENCE_RE.search(body)
        evidence = em.group(1).strip() if em else ""
        claim = body[:em.start()].strip() if em else body
        out.append({"section": section, "line": i, "claim": claim,
                    "evidence_raw": evidence,
                    "waived": waived,
                    "evidence": [e.strip() for e in re.split(r";", evidence)
                                 if e.strip()] if evidence else []})
    return out


def bib_keys(path: str) -> list[str]:
    return BIB_ENTRY_RE.findall(_read(path))


# How a citation RENDERS, which is the only thing a reader ever sees
# (citation-integrity 5). Two entries whose keys differ in every character
# can still come out of citeproc as the same "Duval et al., 2011", and the
# four findings `citekeys()` already emits are all properties of the KEY.
_BIB_FIELD_RE = re.compile(
    r"^\s*(author|year|date|title)\s*=\s*[{\"]?(.*?)[}\",]*\s*$",
    re.IGNORECASE | re.MULTILINE)
_BIB_START_RE = re.compile(r"^\s*@(\w+)\s*\{\s*([^,\s}]+)\s*,", re.MULTILINE)

# The same particles scholar.py folds. Repeated rather than imported: prose.py
# is standard-library only and scholar.py needs `requests`, so importing it
# here would make every prose check depend on a pip install.
_NAME_PARTICLES = {
    "van", "von", "der", "den", "de", "del", "della", "dello", "di", "da",
    "do", "dos", "das", "du", "la", "le", "les", "el", "al", "bin", "ibn",
    "ter", "ten", "af", "av", "zu", "vander", "vande",
}


def _bib_surname(author_field: str) -> str:
    """The first author's surname, from a BibTeX `author` field."""
    first = re.split(r"\s+and\s+", (author_field or "").strip(), maxsplit=1)[0]
    if "," in first:
        first = first.split(",", 1)[0]
    first = first.replace("{", " ").replace("}", " ").replace("\\", " ")
    tokens = [t for t in re.sub(r"[^A-Za-z\s'-]", " ", first).lower().split()
              if len(t) > 2]
    if not tokens:
        return ""
    body = [t for t in tokens if t not in _NAME_PARTICLES]
    return (body or tokens)[-1]


def bib_entries(path: str) -> list[dict]:
    """key, first-author surname and year for every entry, in file order.

    A deliberately small parser: `citekeys()` needs three fields and nothing
    more, and prose.py may not import the .bib reader in pubmed.py because
    that module requires `requests`.
    """
    text = _read(path)
    starts = list(_BIB_START_RE.finditer(text))
    out: list[dict] = []
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        body = text[m.end():end]
        fields = {k.lower(): v.strip() for k, v in _BIB_FIELD_RE.findall(body)}
        year = re.sub(r"[^0-9]", "", fields.get("year") or
                      fields.get("date") or "")[:4]
        out.append({"key": m.group(2), "type": m.group(1).lower(),
                    "author": fields.get("author", ""),
                    "surname": _bib_surname(fields.get("author", "")),
                    "year": year, "title": fields.get("title", "")})
    return out


def author_year_label(entry: dict) -> str:
    """"Duval 2011" - what an author-year style puts on the page."""
    if not entry.get("surname") or not entry.get("year"):
        return ""
    return "%s %s" % (entry["surname"], entry["year"])


def stats_output(project: str) -> str:
    """`data/analysis/analysis.md` - the analysis output, whole.

    Both regions are read, not just the generated one. A number the writing
    engine appended below the marker is a recorded number: it is in the file
    the engine may take numbers from, and refusing it would report a value as
    invented while it sits in the file that records it.
    """
    return _read(os.path.join(project, "data", "analysis", "analysis.md"))


# ---------------------------------------------------------------------------
# outline - spec 12 and 5.1
# ---------------------------------------------------------------------------

# A panel suffix is legitimate and the scaffolded outline.md uses one: "[Fig
# 4B]" points at panel B of Figure 4. The float that has to exist is Figure 4.
EV_FLOAT_RE = re.compile(r"^(Fig|Figure|Tab|Table)\s+(S?\d+)[A-Za-z]?$",
                         re.IGNORECASE)
EV_STATS_RE = re.compile(r"^stats:\s*(.+)$", re.IGNORECASE)
EV_PMID_RE = re.compile(r"^PMID[:\s]*(\d{4,9})$", re.IGNORECASE)
EV_CITEKEY_RE = re.compile(r"^@?([A-Za-z][\w:.+-]*)$")

# The section a source_text file speaks for, so an outline heading resolves to
# the paragraphs that are supposed to implement it.
SECTION_ALIASES = {
    "introduction": "introduction", "intro": "introduction",
    "background": "introduction",
    "results": "results", "results and discussion": "results",
    "discussion": "discussion", "conclusions": "discussion",
    "conclusion": "discussion",
    "methods": "methods", "materials and methods": "methods",
    "abstract": "title_abstract", "title": "title_abstract",
    "title and abstract": "title_abstract",
}

# The form sections, which a review has too: whatever else it is, it carries a
# title page and an abstract.
FORM_ALIASES = {"abstract": "title_abstract", "title": "title_abstract",
                "title and abstract": "title_abstract"}


def outline_section_key(project: str, name: str) -> str:
    """The section stem an outline heading speaks for, or "".

    A research paper's headings come out of SECTION_ALIASES, a fixed IMRaD
    vocabulary. **A review's do not, and this is the whole of item 72**: the
    scaffolded outline tells the author that the `##` headings under
    `# Structure` ARE the section list and that they name them, and then
    every thematic name they chose was reported as "not a section of the
    paper" - after which its lines were parsed and compared against nothing,
    so outline adherence silently stopped being checked for the entire body
    of the review.

    So on a review the heading resolves by SLUG, the same way manuscript.py
    names the file it writes: `Disease Pathology` -> the `NN_disease_pathology`
    stem on disk, or that slug unprefixed when nothing has been drafted for it
    yet - which is a heading with no file, and reporting that is this
    command's job rather than a reason to drop the line.
    """
    norm = " ".join(name.strip().lower().split())
    if paper_kind(project) != "review":
        return SECTION_ALIASES.get(norm, "")
    if norm in FORM_ALIASES:
        return FORM_ALIASES[norm]
    slug = section_slug(name)
    for stem in section_stems(project):
        m = REVIEW_STEM_RE.match(stem)
        if stem == slug or (m and stem[3:] == slug):
            return stem
    return slug


def _tokens(text: str) -> set[str]:
    stop = {"the", "a", "an", "of", "and", "or", "is", "are", "was", "were",
            "in", "on", "to", "for", "with", "that", "this", "than", "then",
            "it", "its", "by", "as", "at", "be", "not", "but", "we", "our",
            "from", "which", "these", "those", "so", "more", "less"}
    return {w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in stop}


def outline(project: str, adherence: str | int = ADHERENCE_DEFAULT,
            source: str | None = None) -> dict:
    """The claims ledger, checked against the project it belongs to.

    `source` reads the lines out of some other file instead of
    plan/outline.md. That is what answers "the outline is over here, use
    that" honestly: the candidate is read by this grammar and checked against
    this project's floats, stats and bibliography, so the user sees what
    adopting it would actually give them - before anything is written.
    """
    level = adherence_level(adherence)
    if source is None:
        lines = read_outline(project)
    else:
        lines = parse_outline(_read(source))
    captions = read_captions(project)
    float_labels = {e["label"].lower() for e in captions}
    float_labels |= {f"fig {e['number'].lower()}" for e in captions
                     if e["type"] == "Figure"}
    float_labels |= {f"tab {e['number'].lower()}" for e in captions
                     if e["type"] == "Table"}
    stats = stats_output(project).lower()
    keys = set(bib_keys(bib_path(project)))

    findings: list[dict] = []

    # 4.4. A waived line is not drafted, not reported as uncovered, and not
    # counted in adherence. It is still READ - it stays in the file with its
    # reason, because the reason is the whole point - and a waiver written
    # without one is reported so that the marker cannot become a silent
    # delete key.
    waived_lines = [ln for ln in lines if ln.get("waived") is not None]
    for ln in waived_lines:
        if not ln["waived"]:
            findings.append({
                "location": f"outline.md:{ln['line']}", "rule": "waiver_unexplained",
                "severity": "warning", "claim": ln["claim"],
                "detail": "[waived] with no reason. The reason is the whole "
                          "point of the marker - an unexplained waiver is the "
                          "thing nobody can audit in six months. Write "
                          "`[waived: why]`, or delete the line"})
    lines = [ln for ln in lines if ln.get("waived") is None]

    # --- minimality -------------------------------------------------------
    # An outline line is the idea of a paragraph, not a draft of it. Detail
    # written here is detail written twice, and the second copy - the
    # paragraph - is the one anybody reads, so the two drift. Reported, never
    # rewritten: which half to cut is the author's call.
    for ln in lines:
        n = len(ln["claim"].split())
        if n > OUTLINE_MAX_WORDS:
            findings.append({
                "location": f"outline.md:{ln['line']}", "rule": "over_detailed",
                "severity": "warning", "claim": ln["claim"],
                "detail": f"{n} words; an outline line is the paragraph's idea, "
                          f"not its first draft. Keep it under "
                          f"{OUTLINE_MAX_WORDS} and move the detail into the "
                          f"paragraph or the notes block at the bottom"})
        elif len(_sentences(ln["claim"])) > 1:
            findings.append({
                "location": f"outline.md:{ln['line']}", "rule": "over_detailed",
                "severity": "warning", "claim": ln["claim"],
                "detail": "more than one sentence; one line is one paragraph "
                          "asserting one thing, so a second sentence is "
                          "either a second paragraph or detail that belongs "
                          "in the paragraph itself"})

    # --- evidence brackets ------------------------------------------------
    for ln in lines:
        loc = f"outline.md:{ln['line']}"
        if not ln["evidence"]:
            findings.append({"location": loc, "rule": "empty_bracket",
                             "severity": "error", "claim": ln["claim"],
                             "detail": "no evidence bracket; the claim has "
                                       "nothing behind it yet"})
            continue
        for ev in ln["evidence"]:
            if ev.lower() == "background":
                # `background` used to mean "uncontested framing that needs
                # NO citation", so an outline line that ought to read "here
                # is what the field currently does with this material, and
                # here are the four papers that say so" resolved clean with
                # nothing behind it - and the drafter was then correct, by
                # the engine's own rules, to write general framing from its
                # own knowledge. That is the least trustworthy prose in the
                # pipeline and the only prose in it no check looked at.
                #
                # It now means "cited from plan/literature_landscape.md,
                # rather than from a result in this paper". A warning and not
                # an error: the line is still draftable, and the gap is what
                # has to become visible (item 21).
                if not any(e.lower() != "background" for e in ln["evidence"]):
                    findings.append({
                        "location": loc, "rule": "background_uncited",
                        "severity": "warning", "claim": ln["claim"],
                        "detail": "`background` on its own is no longer a "
                                  "citation. Put the PMID or citekey from "
                                  "plan/literature_landscape.md beside it - "
                                  "`background` now means cited from the "
                                  "landscape, not cited from nothing"})
                continue
            m = EV_FLOAT_RE.match(ev)
            if m:
                want = f"{m.group(1).lower()} {m.group(2).lower()}"
                want_full = ("figure " if want.startswith("fig") else "table ") \
                    + m.group(2).lower()
                if want not in float_labels and want_full not in float_labels:
                    findings.append({
                        "location": loc, "rule": "float_missing",
                        "severity": "error", "claim": ln["claim"],
                        "detail": f"evidence names {ev}, which is not a block "
                                  f"in plan/captions.md"})
                continue
            m = EV_STATS_RE.match(ev)
            if m:
                term = m.group(1).strip().lower()
                if not stats:
                    findings.append({
                        "location": loc, "rule": "stats_output_missing",
                        "severity": "error", "claim": ln["claim"],
                        "detail": "evidence names a stats term but "
                                  "data/analysis/analysis.md does not exist "
                                  "yet"})
                elif term not in stats:
                    findings.append({
                        "location": loc, "rule": "stats_term_missing",
                        "severity": "error", "claim": ln["claim"],
                        "detail": f"'{term}' does not appear in "
                                  f"analysis.md"})
                continue
            if EV_PMID_RE.match(ev):
                continue
            m = EV_CITEKEY_RE.match(ev)
            if m:
                if keys and m.group(1) not in keys:
                    findings.append({
                        "location": loc, "rule": "citekey_missing",
                        "severity": "error", "claim": ln["claim"],
                        "detail": f"citekey '{m.group(1)}' is not in "
                                  f"{dpfx(project)}references.bib"})
                continue
            findings.append({
                "location": loc, "rule": "unreadable_evidence",
                "severity": "error", "claim": ln["claim"],
                "detail": f"'{ev}' is not Fig N, Table N, stats: <term>, a "
                          f"PMID, a citekey, or background (which now needs "
                          f"a PMID or citekey beside it)"})

    # --- outline vs. paragraphs ------------------------------------------
    by_section: dict[str, list[dict]] = {}
    for ln in lines:
        key = outline_section_key(project, ln["section"])
        if key:
            by_section.setdefault(key, []).append(ln)

    # The stems on disk, plus any the outline names that nothing has been
    # drafted for. The second half only ever adds anything on a review, where
    # the stem list is read off the folder - and a heading with no file is
    # precisely what this command exists to report, so it is listed with zero
    # paragraphs rather than dropped.
    stems = list(section_stems(project))
    stems += [k for k in by_section if k not in stems]

    sections: list[dict] = []
    for stem in stems:
        path = os.path.join(source_text_dir(project), f"{stem}.md")
        paras = paragraphs(_read(path)) if os.path.isfile(path) else []
        ol = by_section.get(stem, [])
        sections.append({"section": stem, "outline_lines": len(ol),
                         "paragraphs": len(paras)})

        if not ol and not paras:
            continue

        # Alignment is a hint, never a verdict: which paragraph implements
        # which line is a judgment the skill makes. What the engine can prove
        # is the count mismatch and the best lexical pairing to look at first.
        #
        # Assignment is one-to-one and greedy over the whole score matrix, not
        # best-match-per-line. Per-line matching lets one strong paragraph win
        # three outline lines and then reports the other three as dropped,
        # which is a worse hint than no hint.
        scores: list[tuple[float, int, int]] = []
        for i, ln in enumerate(ol):
            lt = _tokens(ln["claim"])
            for j, p in enumerate(paras):
                pt = _tokens(p["text"])
                if lt and pt:
                    scores.append((len(lt & pt) / len(lt), i, j))
        scores.sort(reverse=True)
        taken_l: set[int] = set()
        taken_p: set[int] = set()
        pairs: dict[int, tuple[int, float]] = {}
        for score, i, j in scores:
            if score < 0.2 or i in taken_l or j in taken_p:
                continue
            taken_l.add(i)
            taken_p.add(j)
            pairs[i] = (j, score)

        used = {paras[j]["n"] for j, _ in pairs.values()}
        for i, ln in enumerate(ol):
            if i in pairs:
                j, score = pairs[i]
                ln["match"] = {"paragraph": paras[j]["n"],
                               "score": round(score, 2)}
                continue
            best = max((s for s, li, _ in scores if li == i), default=0.0)
            ln["match"] = {"paragraph": None, "score": round(best, 2)}
            findings.append({
                "location": f"outline.md:{ln['line']}",
                "rule": "dropped_point", "severity": "warning",
                "claim": ln["claim"],
                "detail": f"no unclaimed paragraph in {stem}.md matches this "
                          f"line (best overlap {best:.0%})"})

        for p in paras:
            if p["n"] not in used and ol:
                findings.append({
                    "location": f"{stem} ¶{p['n']}",
                    "rule": "added_content", "severity": "warning",
                    "detail": "this paragraph does not correspond to an "
                              "outline line",
                    "claim": " ".join(p["text"].split()[:14]) + " ..."})

        order = [ln["match"]["paragraph"] for ln in ol
                 if ln.get("match", {}).get("paragraph")]
        if order != sorted(order):
            findings.append({
                "location": stem, "rule": "out_of_order",
                "severity": "warning",
                "detail": f"paragraphs implement the outline in the order "
                          f"{order}, not in outline order"})

    # Level 1 softens the three adherence findings to `advisory` and leaves
    # every truth check exactly where it was (19.2). It is the same softening
    # 4.3 gives a frozen section, and it is applied here rather than by each
    # caller so there is one place that knows which findings are which.
    #
    # The mapping above still runs at level 1. "No deviation report is
    # produced" is about the round's report, not about this module going
    # blind: the information is what makes level 1 a choice rather than a
    # blindfold, and a finding nobody can see cannot be marked advisory.
    if level == 1:
        for f in findings:
            if f["rule"] in ADHERENCE_FINDINGS:
                f["severity"] = "advisory"
                f["advisory"] = True

    severity_note = ADHERENCE_NOTES.get(level, "")

    if source is None:
        state = outline_state(project)
        notes = read_outline_notes(project)
        note_bullets = parse_outline_note_bullets(
            _read(os.path.join(project, "plan", "outline.md")))
        proposed = outline_proposed(project)
        where = os.path.join(project, "plan", "outline.md")
    else:
        text = _read(source)
        # A file the user points at is `missing` when it is not there and
        # `empty` when nothing in it parses - the same three answers, so a
        # caller does not need a second vocabulary for the same question.
        state = ("missing" if not os.path.isfile(source)
                 else "present" if lines else "empty")
        notes = parse_outline_notes(text)
        note_bullets = parse_outline_note_bullets(text)
        proposed = outline_text_proposed(text)
        where = source
    # The contract travels with the payload, so a caller that WRITES an
    # outline validates it against the same table this module reads it with
    # instead of keeping a second copy. Same problem CAPTION_HEADING_RE has in
    # three files, solved here by not making the second copy at all.
    contract = {
        "max_words": OUTLINE_MAX_WORDS,
        "sections": SECTION_ALIASES,
        # Who names the sections. On a research paper the vocabulary above is
        # the whole list. On a review the author names them - the scaffolded
        # outline says so in its own comment - so a caller that VALIDATES a
        # bundle must accept any heading that is not the notes block, and
        # resolve it by slug. A caller reading only `sections` refuses the
        # review it was told to invent (item 72).
        "section_naming": ("author" if paper_kind(project) == "review"
                           else "fixed"),
        "form_sections": FORM_ALIASES,
        "notes_headings": sorted(NOTES_SECTIONS),
        "proposed_mark": OUTLINE_PROPOSED_MARK,
        "evidence": ["Fig N", "Figure N", "Table N", "stats: <term>",
                     "PMID 12345678", "@citekey", "background", "(empty)"],
        "waived_mark": "[waived: reason]",
        # The five levels travel with the payload for the same reason the
        # section names do: a caller that ASKS the adherence question offers
        # the levels this module reads with, rather than keeping a list of
        # its own that drifts.
        "adherence_levels": {str(n): ADHERENCE_LEVELS[n]
                             for n in sorted(ADHERENCE_LEVELS)},
        "adherence_notes": {str(n): ADHERENCE_NOTES[n]
                            for n in sorted(ADHERENCE_NOTES)},
        "adherence_findings": list(ADHERENCE_FINDINGS),
    }
    return {"adherence": ADHERENCE_LEVELS.get(level, str(adherence)),
            "level": level,
            "label": adherence_label(level),
            "note": severity_note,
            "contract": contract,
            "state": state,
            "proposed": proposed,
            "source": source or "",
            "path": where,
            "waived": [{"line": ln["line"], "section": ln["section"],
                        "claim": ln["claim"], "because": ln["waived"]}
                       for ln in waived_lines],
            "sections": sections, "lines": lines, "notes": notes,
            "note_bullets": note_bullets,
            "findings": findings,
            "counts": {"outline_lines": len(lines), "notes_lines": len(notes),
                       "note_bullets": len(note_bullets),
                       # Waived lines are counted separately and are NOT in
                       # outline_lines: adherence is measured against what the
                       # paper is still meant to say (4.4).
                       "waived": len(waived_lines),
                       "over_detailed": sum(1 for f in findings
                                            if f["rule"] == "over_detailed"),
                       "findings": len(findings)}}


# ---------------------------------------------------------------------------
# flags - spec 5.10
# ---------------------------------------------------------------------------

def flag_id(section: str, kind: str, message: str) -> str:
    """A flag's identity across rounds - `specs/flag-resolver.md` 5.

    Hashed from the SECTION, the TYPE and the normalized BODY, and **never
    from the location**: an answer recorded in r4 is applied in r5, by which
    time two paragraphs have been merged and `results ¶3` is `results ¶4`.
    Location is the one component guaranteed to have moved.

    Normalization is whitespace and case only. A REWORDED flag is a different
    flag and gets a different id, which is correct rather than unfortunate:
    "no thickness recorded" and "thickness for the annealed series is not in
    the stats output" are not certainly the same question, and an answer
    carried silently across them is a lookup that was almost right.
    """
    body = " ".join(str(message or "").split()).lower()
    # A separator that cannot occur in any of the three parts, so
    # ("results", "data", "x") and ("results", "datax", "") cannot collide.
    raw = "\x00".join((str(section or "").lower(), str(kind or "").lower(),
                       body))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:6]


def flags(paths: list[str]) -> dict:
    found: list[dict] = []
    for path in paths:
        stem = os.path.splitext(os.path.basename(path))[0]
        text = _read(path)
        if not text:
            continue
        for p in paragraphs(text):
            for m in FLAG_RE.finditer(p["text"]):
                kind = m.group(1).lower()
                message = " ".join(m.group(2).split())
                found.append({
                    "file": path, "location": f"{stem} ¶{p['n']}",
                    "id": flag_id(stem, kind, message),
                    "line": p["line"], "type": kind, "message": message,
                    "known_type": kind in FLAG_TYPES,
                    "context": " ".join(
                        p["text"][max(0, m.start() - 90):m.start()].split())[-90:],
                })
    by_type: dict[str, int] = {}
    for f in found:
        by_type[f["type"]] = by_type.get(f["type"], 0) + 1
    # author, decision and conflict are the ones a human has to answer; the
    # rest are addressable by the self-resolution pass (spec 5.10).
    #
    # `edit-override` and `edit-intent` join them, and they are the clearest
    # cases in the list: one asks whether to overrule a named colleague, the
    # other asks what a colleague meant by what they typed. Neither is a
    # question any pass in this pipeline is entitled to settle on the user's
    # behalf (edit-authority 3, 4).
    # `citation-claim` joins them for the same reason: whether the sentence
    # or the citation was wrong is a question only the person who wrote the
    # sentence can answer, and no pass in this pipeline may settle it
    # (citation-integrity 6.5).
    unresolvable = [f for f in found
                    if f["type"] in ("author", "decision", "conflict",
                                     "edit-override", "edit-intent",
                                     "citation-claim")]
    return {"flags": found, "by_type": by_type,
            "needs_a_human": len(unresolvable),
            "unknown_types": sorted({f["type"] for f in found
                                     if not f["known_type"]}),
            "counts": {"flags": len(found)}}


# `render_flags_md` was here, and `flags --out` wrote `reports/rN/flags.md`
# with it. Both are gone (item 43). Every flag in that table was already in
# two places a person actually reads - inline in the source text, and again in
# the PAPER NOT COMPLETE block of the built .docx - and a third copy is a copy
# to keep in step rather than a record of anything. The `flags` command still
# reports them; nothing writes them to a file.
#
# This is the one file in `reports/` that went. The three that stayed hold the
# NEGATIVE evidence - what was checked and came back clean - which by
# construction cannot be in a manuscript, because a manuscript only carries
# what is wrong with it. A citation check that finds nothing writes nothing in
# the .docx, and that is indistinguishable from a check that never ran.


# ---------------------------------------------------------------------------
# citekeys - spec 9.1, 5.5
# ---------------------------------------------------------------------------

def citekeys(project: str, max_refs: int | None = None) -> dict:
    bib = bib_path(project)
    keys = bib_keys(bib)
    cited: dict[str, list[str]] = {}
    for path in source_files(project):
        stem = os.path.splitext(os.path.basename(path))[0]
        text = _strip_comments(_read(path))
        text = re.sub(r"`[^`]*`", " ", text)
        for p in paragraphs(text):
            for m in CITEKEY_RE.finditer(p["text"]):
                cited.setdefault(m.group(1), []).append(
                    f"{stem} ¶{p['n']}")

    findings: list[dict] = []
    unresolved = sorted(k for k in cited if k not in keys)
    for k in unresolved:
        findings.append({
            "rule": "unresolved_citekey", "severity": "error", "key": k,
            "location": ", ".join(sorted(set(cited[k]))),
            "detail": f"[@{k}] has no entry in {dpfx(project)}references.bib; citeproc "
                      f"would render the key itself into the .docx"})
    uncited = sorted(k for k in keys if k not in cited)
    for k in uncited:
        findings.append({
            "rule": "uncited_entry", "severity": "warning", "key": k,
            "location": "references.bib",
            "detail": f"@{k} is in the bibliography and no manuscript section "
                      f"cites it; drop it or re-cite it - `manuscript.py bib "
                      f"--prune` removes the ones nothing anywhere in the "
                      f"project cites"})
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    for k in duplicates:
        findings.append({"rule": "duplicate_entry", "severity": "error",
                         "key": k, "location": "references.bib",
                         "detail": "defined more than once"})
    if max_refs and len(keys) > max_refs:
        findings.append({
            "rule": "over_reference_cap", "severity": "error",
            "key": "", "location": "references.bib",
            "detail": f"{len(keys)} entries, journal cap {max_refs}"})

    # The fifth finding, and the only one computed from the ENTRY FIELDS
    # rather than from the key (citation-integrity 5, item 108). Two entries
    # can differ in every character of their keys and still render as the
    # same "Duval et al., 2011", and in an author-year style a reader
    # cannot tell which is meant. A WARNING, because in a numbered style it
    # changes nothing and the writer may have a style that disambiguates -
    # and it names the citing paragraphs so the cost is visible rather than
    # asserted.
    #
    # It has to survive a wrong year being CORRECTED first: fixing the year
    # often dissolves the collision as a side effect, which is exactly why
    # nobody went looking for this check. The two defects were hiding each
    # other.
    by_label: dict[str, list[dict]] = {}
    for e in bib_entries(bib):
        label = author_year_label(e)
        if label:
            by_label.setdefault(label, []).append(e)
    collisions: list[dict] = []
    for label, group in sorted(by_label.items()):
        seen_keys = sorted({e["key"] for e in group})
        if len(seen_keys) < 2:
            continue
        where = sorted({loc for k in seen_keys for loc in cited.get(k, [])})
        rendered = "%s et al., %s" % (group[0]["surname"].title(),
                                      group[0]["year"])
        collisions.append({"rendered": rendered, "keys": seen_keys,
                           "cited_from": where})
        findings.append({
            "rule": "author_year_collision", "severity": "warning",
            "key": ", ".join(seen_keys),
            "location": ", ".join(where) or "references.bib",
            "detail": "%s both render as \"%s\"; cited from %s. In an "
                      "author-year style a reader cannot tell which is "
                      "meant. citeproc adds the disambiguating letter only "
                      "if the style asks for it."
                      % (" and ".join("@" + k for k in seen_keys), rendered,
                         ", ".join(where) or "nowhere in the source text")})

    return {"bib": bib, "entries": len(keys), "cited": len(cited),
            "citations": {k: sorted(set(v)) for k, v in sorted(cited.items())},
            "unresolved": unresolved, "uncited": uncited,
            "duplicates": duplicates, "collisions": collisions,
            "findings": findings,
            "counts": {"findings": len(findings)}}


# ---------------------------------------------------------------------------
# captions - spec 7
# ---------------------------------------------------------------------------

# A legend that opens "Comparison of X and Y" is a label, not a claim. The
# rule is the user's former PI's, promoted to house style: tell the reader what
# to think.
LABEL_LEAD_RE = re.compile(
    r"^(Comparison|Plot|Summary|Overview|Analysis|Distribution|Schematic|"
    r"Diagram|Representative|Characteri[sz]ation|Quantification|Effects?|"
    r"Results|Data|Measurements?|Example|Illustration|Depiction|Workflow)\b"
    r"[^.]*\bof\b", re.IGNORECASE)


def captions(project: str, cap: int | None = None) -> dict:
    entries = read_captions(project)
    live_path = os.path.join(source_text_dir(project), "live_captions.md")
    live = {e["label"]: e for e in _parse_caption_text(_read(live_path))} \
        if os.path.isfile(live_path) else {}

    # Which floats the text actually mentions, so an unreferenced float and a
    # referenced-but-uncaptioned one both surface. The scanner is shared with
    # crossrefs() rather than written again here: the local copy this replaced
    # could not see a plural callout at all, so a float cited only as
    # "(Figures 1 and 2)" was reported as never cited.
    text_refs: dict[str, list[str]] = {}
    for path in source_files(project):
        stem = os.path.splitext(os.path.basename(path))[0]
        for p in paragraphs(_read(path)):
            for m in float_mentions(p["text"]):
                text_refs.setdefault(m["label"], []).append(f"{stem} ¶{p['n']}")

    findings: list[dict] = []
    out: list[dict] = []
    for e in entries:
        words = len(_words(e["caption"])) + len(_words(e["subtitle"]))
        rec = {"label": e["label"], "file": e["file"],
               "supplementary": e["supplementary"], "subtitle": e["subtitle"],
               "words": words,
               "referenced_in": sorted(set(text_refs.get(e["label"], [])))}
        if not e["subtitle"]:
            findings.append({
                "location": e["label"], "rule": "no_claim_subtitle",
                "severity": "error",
                "detail": "the legend has no bold opening sentence; every "
                          "legend states the claim the data supports"})
        elif LABEL_LEAD_RE.match(e["subtitle"]):
            findings.append({
                "location": e["label"], "rule": "subtitle_is_a_label",
                "severity": "warning", "text": e["subtitle"],
                "detail": "the opening sentence names the contents rather "
                          "than asserting anything"})
        if cap and words > cap:
            findings.append({
                "location": e["label"], "rule": "over_caption_cap",
                "severity": "warning",
                "detail": f"{words} words, journal cap {cap}"})
        if not rec["referenced_in"]:
            findings.append({
                "location": e["label"], "rule": "float_never_cited",
                "severity": "error",
                "detail": "no paragraph in %s refers to this float"
                          % stpfx(project)})

        # spec 7: every change from plan/captions.md is reported as a diff line
        lv = live.get(e["label"])
        if lv is not None:
            lw = len(_words(lv["caption"])) + len(_words(lv["subtitle"]))
            if lv["subtitle"] != e["subtitle"] or lv["caption"] != e["caption"]:
                changed = ["shortened" if lw < words else "lengthened"]
                if lv["subtitle"] != e["subtitle"]:
                    changed.append("claim sentence edited")
                rec["diff"] = (f"{e['label']} - {', '.join(changed)} "
                               f"{words} -> {lw} words")
                findings.append({
                    "location": e["label"], "rule": "live_caption_differs",
                    "severity": "claim_changed" if lv["subtitle"] != e["subtitle"]
                    else "info",
                    "detail": rec["diff"]})
        out.append(rec)

    known = {e["label"] for e in entries}
    for label, where in sorted(text_refs.items()):
        if label not in known:
            findings.append({
                "location": ", ".join(sorted(set(where))),
                "rule": "reference_to_missing_float", "severity": "error",
                "detail": f"the text refers to {label}, which has no block in "
                          f"plan/captions.md"})

    return {"floats": out, "findings": findings,
            "counts": {"floats": len(out), "findings": len(findings)}}


def _parse_caption_text(text: str) -> list[dict]:
    """The same block parse, over a string rather than the project file."""
    text = _strip_comments(text)
    entries: list[dict] = []
    current: dict | None = None
    body: list[str] = []

    def flush() -> None:
        nonlocal current, body
        if current is None:
            return
        txt = [l.strip() for l in body if l.strip()]
        subtitle = ""
        if txt and re.match(r"^\*\*.*\*\*$", txt[0]):
            subtitle = txt[0].strip("*").strip()
            txt = txt[1:]
        current["subtitle"] = subtitle
        current["caption"] = " ".join(txt)
        entries.append(current)
        current = None
        body = []

    for line in text.splitlines():
        m = CAPTION_HEADING_RE.match(line)
        if m:
            flush()
            current = {"type": m.group(1), "number": m.group(2),
                       "supplementary": m.group(2).startswith("S"),
                       "file": m.group(3),
                       "label": f"{m.group(1)} {m.group(2)}"}
            body = []
        elif current is not None:
            body.append(line)
    flush()
    return entries


# ---------------------------------------------------------------------------
# Float callouts - one scanner, because two would disagree
# ---------------------------------------------------------------------------

# The keyword that opens a callout. Whether it is PLURAL is tracked, and that
# is not cosmetic: the plural is what licenses reading a list of numbers after
# it. "Figures 1 and 2" names two floats; "Figure 1 and 3 mM" names one float
# and a concentration.
FLOAT_WORD_RE = re.compile(
    r"\b(Figures|Figure|Figs\.?|Fig\.?|Tables|Table|Tabs\.?|Tab\.?)\s*",
    re.IGNORECASE)

FLOAT_NUM_RE = re.compile(r"(S?)(\d+)", re.IGNORECASE)

# Panel letters, attached to the number: 1A, 2A-C, 3A and B, and Nature's
# lowercase 1a. They must be ATTACHED - "Figure 1 A" is not a panel - and the
# trailing lookahead is what stops "Fig. 1A and the rest" reading "the" as
# panel T.
PANEL_RE = re.compile(
    r"([A-Za-z](?:\s*(?:[-–—]|,|and|&)\s*[A-Za-z])*)(?![A-Za-z0-9])")

# A separator between two numbers in one callout. The `range` groups are the
# ones that mean "everything between", and they license a list even after a
# singular keyword, because "Fig. 1-3" is unambiguous.
LIST_SEP_RE = re.compile(
    r"(?:\s*(?P<dash>[-–—])\s*|\s+(?P<to>to)\s+"
    r"|\s*,\s*(?:and\s+|&\s*)?|\s+(?:and|&)\s+)", re.IGNORECASE)

# A panel declared in the legend, in the only form that can be read without
# guessing: a parenthesised letter, optionally a range. "A, Left panel" is
# how some journals set it and is deliberately NOT matched - a rule loose
# enough to catch it also catches every parenthetical aside in the caption.
PANEL_DECL_RE = re.compile(r"\(\s*([A-Za-z])\s*(?:[-–—]\s*([A-Za-z])\s*)?\)")


def _panel_letters(text: str) -> list[str]:
    """"A-C" -> [A, B, C]; "A and B" -> [A, B]. Uppercased both sides, so a
    journal that sets panels lowercase in the text and uppercase in the
    legend still compares equal."""
    # The word separators go first, or "1a and b" reads as panels A, N, D, B.
    text = re.sub(r"\s*(?:\band\b|&|,)\s*", " ", text, flags=re.IGNORECASE)
    toks = re.findall(r"[A-Za-z]|[-–—]", text)
    out: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.isalpha():
            if (i + 2 < len(toks) and not toks[i + 1].isalpha()
                    and toks[i + 2].isalpha()):
                a, b = ord(t.upper()), ord(toks[i + 2].upper())
                if a <= b:
                    out.extend(chr(c) for c in range(a, b + 1))
                    i += 3
                    continue
            out.append(t.upper())
        i += 1
    seen: list[str] = []
    for p in out:
        if p not in seen:
            seen.append(p)
    return seen


def _paren_spans(text: str) -> list[tuple[int, int]]:
    """Character spans covered by parentheses, outermost only."""
    spans: list[tuple[int, int]] = []
    depth = 0
    start = 0
    for i, ch in enumerate(text):
        if ch == "(":
            if depth == 0:
                start = i
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
            if depth == 0:
                spans.append((start, i + 1))
    return spans


def float_mentions(text: str) -> list[dict]:
    """Every float callout in a piece of prose, with panels and offset.

    Shared by captions() and crossrefs() on purpose. The plural form is
    exactly where a second hand-rolled copy goes wrong: the previous scanner
    was `(Figure|Fig\\.?|Table|Tab\\.?)\\s*(S?\\d+)`, and measured, it found
    nothing at all in "(Figures 1 and 2)" or "(Figs. 1-3)" - so a float cited
    only in a plural callout was reported as never cited, and that report
    shipped inside the .docx in the PAPER NOT COMPLETE block.
    """
    out: list[dict] = []
    pos = 0
    while True:
        w = FLOAT_WORD_RE.search(text, pos)
        if not w:
            return out
        word = w.group(1)
        kind = "Figure" if word.lower().startswith("fig") else "Table"
        plural = word.lower().rstrip(".").endswith("s")
        pos = w.end()

        num = FLOAT_NUM_RE.match(text, w.end())
        while num:
            supp = num.group(1).upper() == "S"
            n = int(num.group(2))
            end = num.end()
            panels: list[str] = []
            pm = PANEL_RE.match(text, end)
            if pm:
                panels = _panel_letters(pm.group(1))
                end = pm.end()
            rec = {"kind": kind, "supplementary": supp, "number": n,
                   "label": f"{kind} {'S' if supp else ''}{n}",
                   "panels": panels, "start": num.start(), "end": end,
                   "implied": False}
            out.append(rec)
            pos = end

            sep = LIST_SEP_RE.match(text, end)
            if sep is None:
                break
            nxt = FLOAT_NUM_RE.match(text, sep.end())
            if not nxt:
                break
            is_range = bool(sep.group("dash") or sep.group("to"))
            if not (plural or is_range):
                break
            # "Figures 1-3" cites Figure 2 as surely as it cites 1 and 3, and
            # a float cited only inside a range must not read as uncited.
            if is_range and not panels:
                lo, hi = n + 1, int(nxt.group(2))
                if (nxt.group(1).upper() == "S") == supp and hi - lo < 40:
                    for k in range(lo, hi):
                        out.append({"kind": kind, "supplementary": supp,
                                    "number": k,
                                    "label": f"{kind} {'S' if supp else ''}{k}",
                                    "panels": [], "start": num.start(),
                                    "end": end, "implied": True})
            num = nxt


# Item 146. A callout carrying bold, italic or underline - `**Fig. 1D**` -
# reached the .docx emphasized on every callout of two real builds, and this
# check read existence, order and label form and never emphasis. The same
# pattern is in manuscript.py, which strips it from the build; the suite
# holds the two copies to the same text. A caption's own bold lead
# (`**Figure 1.** The claim`) is not a callout and does not match: the label
# must be closed by the emphasis marker directly.
CALLOUT_LABEL = (r"(?:Supplementary\s+)?(?:Figs?\.?|Figures?|Tables?|"
                 r"Movies?|Videos?|Schemes?|Eqs?\.?)\s*\(?[SE]?\d+[A-Za-z]?"
                 r"(?:\s*(?:[-\u2013,]|and)\s*[SE]?\d*[A-Za-z]?)*\)?")
CALLOUT_EMPHASIS_RE = re.compile(
    r"(?<![\w*])(\*\*|\*|__|_)(%s)\1(?![\w*])|<u>(%s)</u>"
    r"|\[(%s)\]\{\.underline\}" % (CALLOUT_LABEL, CALLOUT_LABEL,
                                   CALLOUT_LABEL))


def crossrefs(project: str, style: str = "unknown",
              table_style: str = "unknown",
              order: list[str] | None = None,
              emphasis_ok: bool = False) -> dict:
    """Is every float and panel called out, in the required form and order?

    `float_never_cited` and `reference_to_missing_float` are deliberately NOT
    reported here - they are captions()' findings and completeness() already
    reads them there. A rule with two homes gets reported twice and fixed in
    one of them.
    """
    entries = read_captions(project)
    declared = {}
    for e in entries:
        panels = []
        for m in PANEL_DECL_RE.finditer(f"{e['subtitle']} {e['caption']}"):
            panels.extend(_panel_letters(
                m.group(1) + (f"-{m.group(2)}" if m.group(2) else "")))
        declared[e["label"]] = {
            "label": e["label"], "kind": e["type"],
            "supplementary": e["supplementary"],
            "number": int(re.sub(r"\D", "", e["number"])),
            "panels_declared": sorted(set(panels)),
            "mentions": [], "panels_referenced": [], "first_mention": None}

    stems = order or section_stems(project)
    st = source_text_dir(project)
    found: dict[str, dict] = {}
    mentions: list[dict] = []
    running = 0
    read_stems: list[str] = []
    for rank, stem in enumerate(stems):
        path = os.path.join(st, f"{stem}.md")
        if not os.path.isfile(path):
            continue
        read_stems.append(stem)
        text = re.sub(r"`[^`]*`", " ", _strip_comments(_read(path)))
        for p in paragraphs(text):
            spans = _paren_spans(p["text"])
            for m in float_mentions(p["text"]):
                running += 1
                inside = any(a <= m["start"] < b for a, b in spans)
                rec = {"label": m["label"], "location": f"{stem} ¶{p['n']}",
                       "section": stem, "panels": m["panels"],
                       "parenthetical": inside, "implied": m["implied"],
                       "position": (rank, p["n"], m["start"]), "seq": running}
                mentions.append(rec)
                slot = declared.get(m["label"]) or found.setdefault(
                    m["label"], {"label": m["label"], "kind": m["kind"],
                                 "supplementary": m["supplementary"],
                                 "number": m["number"], "panels_declared": [],
                                 "mentions": [], "panels_referenced": [],
                                 "first_mention": None, "uncaptioned": True})
                slot["mentions"].append(rec)

    findings: list[dict] = []
    # Item 146, and the supplement is read for it too (item 150): its
    # callouts are the same paper's.
    if not emphasis_ok:
        for stem in read_stems + ["supplementary"]:
            path = os.path.join(st, f"{stem}.md")
            if not os.path.isfile(path):
                continue
            for p in paragraphs(_strip_comments(_read(path))):
                if p["text"].lstrip().startswith("#"):
                    continue
                for m in CALLOUT_EMPHASIS_RE.finditer(p["text"]):
                    label = m.group(2) or m.group(3) or m.group(4)
                    findings.append({
                        "location": f"{stem} ¶{p['n']}",
                        "rule": "callout_emphasis", "severity": "warning",
                        "found": m.group(0),
                        "detail": f"the callout {m.group(0)} is set in "
                                  f"emphasis; journals print callouts plain "
                                  f"unless their guide says otherwise - "
                                  f"write it as {label}"})
    floats: list[dict] = []
    for slot in list(declared.values()) + list(found.values()):
        seen: list[str] = []
        for m in slot["mentions"]:
            for pl in m["panels"]:
                if pl not in seen:
                    seen.append(pl)
        slot["panels_referenced"] = sorted(seen)
        slot["panels_missing"] = [p for p in slot["panels_declared"]
                                  if p not in seen]
        slot["panels_undeclared"] = [p for p in seen
                                     if slot["panels_declared"]
                                     and p not in slot["panels_declared"]]
        real = [m for m in slot["mentions"] if not m["implied"]]
        if slot["mentions"]:
            first = min(slot["mentions"], key=lambda m: m["position"])
            slot["first_mention"] = first["location"]
            slot["_pos"] = first["position"]
        slot["mentions"] = [
            {k: v for k, v in m.items() if k != "position"}
            for m in slot["mentions"]]

        if slot["panels_missing"]:
            findings.append({
                "location": slot["label"], "rule": "panel_never_cited",
                "severity": "warning",
                "detail": f"the legend defines panel"
                          f"{'s' if len(slot['panels_missing']) > 1 else ''} "
                          f"{', '.join(slot['panels_missing'])}, which no "
                          f"sentence points the reader at"})
        if slot["panels_undeclared"]:
            findings.append({
                "location": slot["label"], "rule": "panel_not_declared",
                "severity": "warning",
                "detail": f"the text cites panel"
                          f"{'s' if len(slot['panels_undeclared']) > 1 else ''}"
                          f" {', '.join(slot['panels_undeclared'])}, which the "
                          f"legend never defines"})

        want = style if slot["kind"] == "Figure" else table_style
        running_text = [m for m in real if not m["parenthetical"]]
        if want == "parenthetical" and running_text:
            findings.append({
                "location": ", ".join(m["location"] for m in running_text),
                "rule": "reference_not_parenthetical", "severity": "warning",
                "detail": f"{slot['label']} is called out in running text "
                          f"{len(running_text)} time"
                          f"{'s' if len(running_text) > 1 else ''}; this "
                          f"journal wants the callout in parentheses"})
        floats.append({k: v for k, v in slot.items() if not k.startswith("_")})

    # The labels IN ORDER OF FIRST MENTION, which is the order a paper's
    # floats are numbered in. `floats` above is in declaration order, and
    # `_pos` is stripped from the payload, so a caller that wanted this had
    # to re-derive it from the location strings - which is how a second,
    # slightly different ordering gets written.
    mention_order = [s["label"] for s in sorted(
        (s for s in list(declared.values()) + list(found.values())
         if s.get("_pos")), key=lambda s: s["_pos"])]

    # --- numbered in order of first mention -------------------------------
    # Four independent sequences: figures and tables are numbered separately,
    # and supplementary floats have their own run.
    for kind in ("Figure", "Table"):
        for supp in (False, True):
            series = sorted(
                (s for s in list(declared.values()) + list(found.values())
                 if s["kind"] == kind and s["supplementary"] == supp
                 and s.get("_pos")),
                key=lambda s: s["number"])
            for a, b in zip(series, series[1:]):
                if b["_pos"] < a["_pos"]:
                    findings.append({
                        "location": b["first_mention"],
                        "rule": "float_out_of_order", "severity": "error",
                        "detail": f"{b['label']} is first mentioned at "
                                  f"{b['first_mention']}, before {a['label']} "
                                  f"at {a['first_mention']}. Floats are "
                                  f"numbered in order of first mention - "
                                  f"renumber them, which is usually the fix, "
                                  f"rather than moving the sentences"})
            nums = [s["number"] for s in sorted(
                (s for s in list(declared.values()) + list(found.values())
                 if s["kind"] == kind and s["supplementary"] == supp),
                key=lambda s: s["number"])]
            gaps = [n for n in range(1, max(nums) + 1) if n not in nums] \
                if nums else []
            if gaps:
                pre = "S" if supp else ""
                findings.append({
                    "location": "plan/captions.md",
                    "rule": "float_number_gap", "severity": "warning",
                    "detail": f"the {kind.lower()}s are numbered "
                              f"{', '.join(str(n) for n in nums)} - nothing is "
                              f"{kind} {pre}{gaps[0]}"})

    unknown_style = [k for k, v in (("figures", style), ("tables", table_style))
                     if v not in ("parenthetical", "running_text", "either")]
    if unknown_style:
        loose = [m for m in mentions if not m["parenthetical"]
                 and not m["implied"]]
        findings.append({
            "location": "journal_requirements/requirements.yml",
            "rule": "callout_style_unsourced", "severity": "info",
            "detail": f"{', '.join(unknown_style)}.citation_style is not "
                      f"sourced, so the callout form is not being checked. "
                      f"{len(loose)} of {len([m for m in mentions if not m['implied']])} "
                      f"callouts are in running text rather than parentheses"})

    # --- could this check run at all (reorganize-directory 5.1) -----------
    #
    # Item 80's shape in a module rather than in a subprocess. No section
    # file, no mentions; no mentions, and the float-order loop iterates an
    # empty series - so every arm of this check is correct and the whole
    # check is UNREACHABLE, and it reported its unreachability as `0 floats`,
    # which is what a paper whose floats are in perfect order also reports.
    # Measured 2026-09-20 on a folder holding three files named as figures 1,
    # 2 and 3: identical output before and after a full `adopt --apply`.
    #
    # Three reasons, because they are three different sentences to a user.
    # `did_not_run` is NOT an error and does not change the exit code - a
    # folder with no prose yet is not a broken folder. What it may never do
    # again is occupy the same output as a clean pass.
    did_not_run = ""
    if not read_stems and not declared:
        did_not_run = (
            "no section text in %s, so no callout and no float order has "
            "been checked - nothing here is a pass. The stems looked for "
            "were %s." % (stpfx(project) or "this project",
                          ", ".join(stems) or "(none)"))
    elif not read_stems:
        did_not_run = (
            "%d float%s declared in plan/captions.md and no section file "
            "matched a stem, so nothing has been checked against the prose. "
            "The stems looked for were %s, in %s."
            % (len(declared), "" if len(declared) == 1 else "s",
               ", ".join(stems) or "(none)", stpfx(project) or "this project"))
    elif not declared:
        did_not_run = (
            "%d section%s read and no float declared in plan/captions.md, so "
            "float ORDER has not been checked; every float mention found is "
            "uncaptioned." % (len(read_stems),
                              "" if len(read_stems) == 1 else "s"))

    return {
        "style": {"figures": style, "tables": table_style},
        "order": stems,
        "mention_order": mention_order,
        "sections_read": read_stems,
        # Empty string when the check ran. A caller that wants "did this
        # prove anything" reads THIS, never the float count.
        "did_not_run": did_not_run,
        "floats": floats,
        "findings": findings,
        "counts": {"floats": len(floats),
                   "mentions": len([m for m in mentions if not m["implied"]]),
                   "panels_declared": sum(len(f["panels_declared"])
                                          for f in floats),
                   "panels_missing": sum(len(f["panels_missing"])
                                         for f in floats),
                   "findings": len(findings)},
    }


# ---------------------------------------------------------------------------
# length - the journal's budget, and what to do about going over
# ---------------------------------------------------------------------------

# What a word limit counts varies by journal and is not guessable, so it is
# sourced like everything else (text.word_limit_counts in requirements.yml)
# and what IS counted here is stated in the payload rather than assumed.
LENGTH_SCOPES = ["abstract", "introduction", "methods", "results",
                 "discussion", "body", "total"]

LENGTH_POLICIES = ["ask", "over", "shorten"]


def _countable(text: str) -> str:
    """The prose a journal would count: no comments, no headings, no flags.

    A `**[FLAG: ...]**` is scaffolding this pipeline added and will not be in
    the submitted file, so counting it would make the paper look longer than
    it is and send the user cutting real sentences.
    """
    text = _strip_comments(text)
    text = re.sub(r"^```.*?^```", "", text, flags=re.DOTALL | re.MULTILINE)
    text = FLAG_RE.sub(" ", text)
    text = re.sub(r"^#.*$", "", text, flags=re.M)
    return text


def section_words(project: str) -> dict:
    """Words per section file, plus the abstract on its own."""
    st = source_text_dir(project)
    out: dict[str, int] = {}
    for stem in section_stems(project):
        path = os.path.join(st, f"{stem}.md")
        out[stem] = len(_words(_countable(_read(path)))) \
            if os.path.isfile(path) else 0

    # The abstract has its own cap in almost every journal, and it lives
    # inside title_abstract.md under its own heading.
    ta = _countable(_read(os.path.join(st, "title_abstract.md")))
    raw = _read(os.path.join(st, "title_abstract.md"))
    m = re.search(r"^##\s+Abstract\s*$(.*?)(?=^##\s|\Z)", raw,
                  re.M | re.DOTALL)
    out["abstract"] = len(_words(_countable(m.group(1)))) if m else 0
    # The body is whatever body sections this project HAS, never four fixed
    # names: a review's sections are the thematic ones its author named, so
    # summing the IMRaD stems raised KeyError and took the whole command down
    # - and every consumer of a word count with it.
    out["body"] = sum(out[s] for s in body_stems(project) if s in out)
    out["total"] = out["body"] + len(_words(ta))
    return out


def length(project: str, limits: dict | None = None,
           policy: str = "ask") -> dict:
    """Word counts against the journal's budget, and the decision it forces.

    Going over is not an error and is not silently accepted either: it is a
    question for the user - carry the length now and cut later, or cut and
    consolidate into the SI now - and `policy` is where their answer is
    recorded so the question is asked once and not every round.
    """
    limits = {k: v for k, v in (limits or {}).items() if v}
    counts = section_words(project)
    entries = read_captions(project)
    counts["figures"] = sum(1 for e in entries
                            if e["type"] == "Figure" and not e["supplementary"])
    counts["tables"] = sum(1 for e in entries
                           if e["type"] == "Table" and not e["supplementary"])

    if policy not in LENGTH_POLICIES:
        policy = "ask"
    sev = {"ask": "decision", "over": "info", "shorten": "warning"}[policy]
    note = {
        "ask": "ask the user: carry the overage for now and fix it before "
               "submission, or shorten and move material to the SI",
        "over": "the user chose to carry the overage for now; it is recorded, "
                "and it still has to be resolved before submission",
        "shorten": "the user chose to shorten; cut or consolidate into the "
                   "supplementary information",
    }[policy]

    findings: list[dict] = []
    over: list[dict] = []
    for scope in LENGTH_SCOPES:
        cap = limits.get(scope)
        if not cap or scope not in counts:
            continue
        have = counts[scope]
        if have > cap:
            over.append({"scope": scope, "words": have, "limit": cap,
                         "over_by": have - cap})
            findings.append({
                "location": f"{stpfx(project)}{scope}.md"
                            if scope in section_stems(project)
                            else f"{stpfx(project)}",
                "rule": "over_word_limit", "severity": sev,
                "detail": f"{scope} is {have} words against a {cap}-word "
                          f"limit - {have - cap} over ({have / cap - 1:.0%}). "
                          f"{note}"})

    for scope, key, what in (("figures", "figures_max", "figure"),
                             ("tables", "tables_max", "table")):
        cap = limits.get(key)
        if cap and counts[scope] > cap:
            over.append({"scope": scope, "words": counts[scope], "limit": cap,
                         "over_by": counts[scope] - cap})
            findings.append({
                "location": "plan/captions.md",
                "rule": f"over_{what}_cap", "severity": sev,
                "detail": f"{counts[scope]} main-text {what}s against a limit "
                          f"of {cap}. {note}"})

    missing = [s for s in ("total", "abstract") if not limits.get(s)]
    if missing:
        findings.append({
            "location": "journal_requirements/requirements.yml",
            "rule": "no_word_limit_sourced", "severity": "info",
            "detail": f"no {' or '.join(missing)} word limit has been sourced, "
                      f"so nothing is being checked against one"})

    return {
        "policy": policy,
        "counts": counts,
        "limits": limits,
        "over": over,
        "counted": (f"prose in {stpfx(project)}, with HTML comments, "
                    "headings, fenced code and **[FLAG: ...]** blocks removed. "
                    "A citekey counts as one word, and captions, the "
                    "reference list and the title page are not counted at all "
                    "- check text.word_limit_counts for what this journal "
                    "actually counts"),
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# numbers - spec 6 rule 5
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Spelled-out magnitudes - specs/flow-and-conclusion-2026-09-19.md 5
# ---------------------------------------------------------------------------
#
# `NUM_TOKEN_RE` sees `40%` and `30 hours` and does not see `a third`,
# `roughly a thousandfold` or `approximately eleven-fold`. All three forms are
# in real drafted prose, and the consequence is a hole in the preservation
# invariant: a rewrite that turns `a thousandfold` into `a hundredfold`
# changes zero numeric tokens, zero citekeys, zero flags and almost no words,
# so every guard passes and `learn.py preserve --restore` sees a clean pass.
#
# `NUM_TOKEN_RE` IS NOT WIDENED, and that is the decision rather than the
# implementation. Teaching the extractor English number words means teaching
# it `one`, `second`, `single` and `half` in ordinary prose, and a comparator
# that fires on "a single mechanism" is the `density`-on-Methods failure with
# a new face. So: a CLOSED list, beside the numeric tokens in the same
# multiset rather than in a second invariant.
#
# The `-fold` family is unambiguous. The fractional words are not - "a third
# mechanism" is an ordinal and "a third of the residues" is a quantity - so a
# fraction counts only when it is used as one: followed by `of`, or by a form
# of `be`, or already plural. `single`, `one` and `second` are not on the list
# at all, which is why the three false positives 5.4 names cannot fire.

MAGNITUDE_FOLD_RE = re.compile(
    r"\b(?:(?:[a-z]+)[\s-]?fold)\b", re.IGNORECASE)

# The fraction words, singular and plural. `half` is here and `halves` is not:
# nobody writes "two halves of the residues are charged" about a measurement.
FRACTION_WORDS = ("half", "third", "thirds", "quarter", "quarters",
                  "fifth", "fifths", "sixth", "sixths", "eighth", "eighths",
                  "tenth", "tenths")
FRACTION_RE = re.compile(
    r"\b(" + "|".join(FRACTION_WORDS) + r")\b((?:\W+\w+){0,3})",
    re.IGNORECASE)
_FRACTION_USE_RE = re.compile(
    r"^\W*(of|is|are|was|were|the)\b", re.IGNORECASE)


def magnitude_tokens(text: str) -> list[str]:
    """Spelled-out quantities, as tokens for the preservation multiset.

    Returns lower-cased, hyphen-folded tokens: `thousandfold`, `elevenfold`,
    `third`. Presentation is normalized the way `_norm_number` normalizes it -
    `eleven-fold` and `elevenfold` are the same magnitude - and the VALUE is
    not: `thousandfold` and `hundredfold` are different tokens, which is the
    whole point.
    """
    out: list[str] = []
    body = re.sub(r"`[^`]*`", " ", text or "")
    for m in MAGNITUDE_FOLD_RE.finditer(body):
        token = re.sub(r"[\s-]+", "", m.group(0)).lower()
        # `manifold`, `scaffold` and `unfold` end in the same four letters
        # and are not magnitudes. The list of what may precede `-fold` is
        # closed for the same reason the whole check is.
        if token in _NOT_A_MAGNITUDE:
            continue
        out.append(token)
    for m in FRACTION_RE.finditer(body):
        word = m.group(1).lower()
        after = m.group(2) or ""
        if word.endswith("s") or _FRACTION_USE_RE.match(after):
            out.append(word.rstrip("s"))
    return sorted(out)


_NOT_A_MAGNITUDE = {
    "manifold", "scaffold", "unfold", "refold", "misfold", "enfold",
    "billfold", "blindfold", "fold", "infold", "outfold", "twofold",
}
# `twofold` is on the NOT list deliberately and it is the one that will look
# wrong: it IS a magnitude, and it is also the commonest rhetorical use in
# this field - "the problem is twofold" - so it costs more than it buys.
# Recorded here rather than argued about again.


def _stats_values(text: str) -> list[str]:
    return [m.group(0).strip() for m in NUM_TOKEN_RE.finditer(text)]


def _matches_a_stat(value: str, stats: list[float]) -> bool:
    """A text number is verified if some recorded value rounds to it.

    Rounding is allowed and has to be, or every value written to two decimals
    would be reported. Recomputation is not: a percent change the output never
    produced stays unmatched, which is exactly the case worth surfacing.
    """
    raw = value.strip().lstrip("<>~≤≥±+-").rstrip("%").replace(",", "")
    try:
        x = float(raw)
    except ValueError:
        return False
    decimals = len(raw.split(".")[1]) if "." in raw else 0
    for y in stats:
        if round(y, decimals) == x:
            return True
        if decimals == 0 and abs(y - x) < 1e-9:
            return True
    return False


def _recorded_values(text: str) -> str:
    """The right-hand sides of a flat yml or json, comments dropped.

    Scanning a whole methods_facts.yml for digits matches the instructions in
    its own comment block, and then any number in the manuscript "verifies"
    against a template nobody filled in. Only a value someone actually
    recorded counts as a record.
    """
    out: list[str] = []
    for line in text.splitlines():
        line = re.sub(r"#.*$", "", line)
        if ":" in line:
            out.append(line.split(":", 1)[1])
    return "\n".join(out)


def _floats_in(text: str) -> list[float]:
    out: list[float] = []
    for v in _stats_values(text):
        try:
            out.append(float(v.lstrip("<>~≤≥±+-").rstrip("%").replace(",", "")))
        except ValueError:
            continue
    return out


def corpus_records(project: str) -> dict:
    """Every paper record's retrieved text, keyed by citekey.

    review.py owns the corpus layout and the record parser, so this loads it
    rather than re-reading the format: a second reader that disagrees with
    the first by one field produces a number reported as unrecorded that is
    sitting in a file. It is imported lazily and by name, so a project with
    no corpus and a machine with no review.py both come back empty rather
    than failing.
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import review as _review                   # local engine, stdlib only
    except Exception:                              # pragma: no cover - import
        return {}
    try:
        return {rec["key"]: _review.retrieved_text(rec)
                for rec in _review.all_records(project)}
    except Exception:                              # pragma: no cover
        return {}


def numbers(project: str, paths: list[str] | None = None,
            kind: str | None = None) -> dict:
    """Every number in the text against every file that could have recorded it.

    Rule 5 names analysis.md, and for the results section that is
    the whole list. But "nothing is written that was not recorded" is the wider
    rule, and a growth temperature or a field strength is recorded in
    methods_facts.yml, not in the stats output. Checking those against the
    stats output alone reports every methods number as invented, which is the
    same noise problem the 16S exclusion exists to avoid - so the sources are
    checked together and the one that matched is named.

    UNDER `paper_kind: review` THE CHECK IS REPOINTED, NOT DROPPED
    (specs/review-paper.md 4.2). A review has no analysis.md, so the check
    would either be silent or fire on every number in the paper - and
    silence is wrong while firing on everything is worse, because a check
    that fires 8 times in 11 teaches the user to discount the class and the
    one true finding goes unread in the same report. So the recorded source
    becomes `data/corpus/papers/`: every number in a review's prose must
    appear in the record of a source this review actually read.

    The result is that a review is allowed MORE numbers than a research
    paper - the density budget is released at layer 4 - and is held to a
    STRICTER account for each one. That is not a trade; it is the same
    principle in both places.

    This is the cheap, project-wide half: is this number recorded ANYWHERE
    in the corpus. `review.py tier` is the expensive half and the one that
    matters - is it in the record of the source THIS SENTENCE cites, at a
    tier that carries numbers at all.
    """
    kind = kind or paper_kind(project)
    sources: dict[str, list[float]] = {}
    findings: list[dict] = []
    checked = 0

    if kind == "review":
        records = corpus_records(project)
        joined = "\n".join(records.values())
        sources["data/corpus/papers/"] = _floats_in(joined)
        primary = "data/corpus/papers/"
        paths = paths or list(source_files(project))
        if not records:
            findings.append({
                "location": "data/corpus/papers/",
                "rule": "no_corpus", "severity": "error",
                "detail": "nothing to check this review's numbers against. "
                          "The corpus IS a review's ledger: build it with "
                          "`review.py search` and `review.py record`, or "
                          "drop `corpus` from `manages` if it lives "
                          "elsewhere"})
        return _numbers_over(paths, sources, primary, findings, checked,
                             kind)

    stats_text = stats_output(project)
    sources["analysis.md"] = _floats_in(stats_text)
    for rel in (("data", "methods_facts.yml"),
                ("data", "analysis", "provenance.json")):
        text = _read(os.path.join(project, *rel))
        if text:
            sources[rel[-1]] = _floats_in(_recorded_values(text))
    # plan/captions.md is deliberately NOT a source. A number in a caption
    # came out of the same stats output, so accepting it would let the
    # pipeline verify itself against its own earlier output.

    paths = paths or [p for p in source_files(project)
                      if os.path.basename(p) in ("results.md", "discussion.md",
                                                 "title_abstract.md")]
    if not stats_text:
        findings.append({
            "location": "data/analysis/analysis.md",
            "rule": "no_stats_output", "severity": "error",
            "detail": "nothing to check the results against; run "
                      "data/analysis/analysis.R first"})
    return _numbers_over(paths, sources, "analysis.md", findings, checked,
                         kind)


def _numbers_over(paths, sources: dict, primary: str, findings: list,
                  checked: int, kind: str) -> dict:
    """The scan itself, over whichever ledger this paper kind answers to.

    One loop for both kinds. Two copies of it would drift, and the half that
    drifts is always the one nobody is currently writing a paper with.
    """

    for path in paths:
        stem = os.path.splitext(os.path.basename(path))[0]
        for p in paragraphs(_read(path)):
            counts = count_numbers(p["text"])
            values = [i["value"] for i in counts["inline"]]
            values += [v for c in counts["clusters"] for v in c["values"]]
            for v in values:
                checked += 1
                matched = next((name for name, nums in sources.items()
                                if _matches_a_stat(v, nums)), None)
                if matched == primary:
                    continue
                if matched:
                    findings.append({
                        "location": f"{stem} ¶{p['n']}",
                        "rule": "recorded_elsewhere",
                        "severity": "warning", "value": v, "source": matched,
                        "detail": f"'{v}' is not in {primary} "
                                  f"but is recorded in {matched}"})
                    continue
                findings.append({
                    "location": f"{stem} ¶{p['n']}",
                    "rule": "not_recorded",
                    # Only the results section is held to rule 5 as an error.
                    # Elsewhere an unmatched number is a number nothing in the
                    # project produced, which still needs answering but is not
                    # by itself a contradiction of the analysis.
                    #
                    # In review mode EVERY section is held to it as an error:
                    # there is no results section, every section is making
                    # claims about somebody else's numbers, and a number
                    # tracing to no record is the review's invented methods
                    # value. The released density budget is layer 4 and does
                    # not reach this.
                    "severity": ("error" if (stem == "results"
                                             or kind == "review")
                                 else "warning"),
                    "value": v,
                    "detail": (f"'{v}' appears in no recorded source "
                               f"({', '.join(sources)}) at any rounding")
                              + (" - `review.py tier` says whether it is in "
                                 "the record of the source its own sentence "
                                 "cites, which is the stricter question"
                                 if kind == "review" else "")})
    return {"sources": {k: len(v) for k, v in sources.items()},
            "paper_kind": kind, "ledger": primary,
            "stats_values": len(sources.get(primary, [])),
            "checked": checked, "findings": findings,
            "counts": {"findings": len(findings)}}


# ---------------------------------------------------------------------------
# readability - the countable half of comprehension-check (spec 3)
# ---------------------------------------------------------------------------
#
# REPORTS AND ALWAYS EXITS 0. No threshold, no budget, no verdict, which is
# prose 3.4's rule for `voice` and is unchanged here. `voice` already covers
# sentence length and its distribution, opener length, lead rank, hedges,
# transitions, nominalizations and passives; this is what it does not have,
# and every measure in it is here because it is the countable shadow of a
# comprehension failure rather than because it was easy to count.
#
# The register is the FIELD'S, and it has been measured. 192 sentences of
# published chemistry abstracts in the user's own area: mean 22.7 words, sd
# 15.7, median 24, longest 93; 40% over 25 words, 18% over 35, 29% passive. A
# readability standard of fifteen to twenty words and an active voice would
# flag two fifths of published, peer-reviewed work in this field and would be
# wrong every time. So nothing here computes a grade level, and nothing here
# has an opinion about a long sentence.

# Element symbols, which are not abbreviations however much they look like
# one. The first 103, because a paper in this field uses them as nouns.
_ELEMENTS = set("""H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V
Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In
Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re
Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md
No Lr""".split())

# SI and the units a chemistry or biochemistry paper writes without defining.
_UNITS = set("""m g s A K mol cd Hz N Pa J W C V F S Wb T H lm lx Bq Gy Sv
kat L nm um mm cm km ug mg kg ns us ms ps fs min h d eV keV MeV meV kJ kcal
mM uM nM pM M rpm rcf bar Torr atm Da kDa MDa ppm ppb wt vol
mL uL nL pL cps CPS dpi DPI""".split())

def _is_formula(token: str) -> bool:
    """True when a token decomposes entirely into element symbols and counts.

    MEASURED, and this is the exemption that actually does the work. Across
    24 real published abstracts in the user's own field the naive check
    fires 112 times, and the element-and-unit list the obvious design
    reaches for removes NONE of them - because what fires is not `Au` and
    not `nm`, it is `CH3`, `COOH`, `C6H4OH`, `NH2`, `H2O2`, `HS`, `OCH`.
    Chemical species, written as formulae, which no chemist expands and no
    reader needs expanded.

    Parsed rather than listed: a list of formulae is a list somebody has to
    keep adding to, and the one they forget is the one that fires. Greedy on
    the two-letter symbol first, because `Co` is cobalt and `CO` is carbon
    monoxide and both decompose cleanly either way.
    """
    body = token
    i = 0
    saw = False
    while i < len(body):
        if body[i].isdigit():
            i += 1
            continue
        two = body[i:i + 2]
        if len(two) == 2 and two.capitalize() in _ELEMENTS:
            i += 2
            saw = True
            continue
        one = body[i].upper()
        if one in _ELEMENTS:
            i += 1
            saw = True
            continue
        return False
    return saw


# --- ABBREVIATION CONTRACT ---
# ONE token regex and ONE parenthesis reader, used by `readability` and by the
# `abbreviations` subcommand alike. They had one each until 2026-09-21, and
# both of the differences were defects (items 100 and 101).
#
# 100: readability's regex required an upper-case FIRST character, so `vWA`,
# `mRNA` and `dsDNA` did not match it AT ALL - and on the paper this was
# measured against, `vWA` is one of the two terms the whole check exists to
# find. Every count the regex fed was low by however many of those the
# manuscript held, and nothing reported the omission. It was found by building
# a second command that had to match the same tokens and comparing them, which
# is the only way a silent undercount is ever found.
#
# 101: the definition test matched a parenthesis holding NOTHING but the
# abbreviation. `Capillary morphogenesis gene 2 (CMG2, also ANTXR2)` defines
# two and matched neither, so the paper's own subject was reported undefined
# in its own abstract - a check firing on a correctly defined term, which is
# what teaches a reader to discount the whole class.
#
# Two to eight characters, at least two of them capitals or digits, with up to
# two lower-case letters allowed in front: `XPS`, `DFT`, `Fe3O4`, `vWA`,
# `mRNA`, `dsDNA`. A single capitalised word is a proper noun, not an
# abbreviation, and counting it fires on every author's name in the text.
_ABBREV_TOKEN_RE = re.compile(
    r"(?<![\w-])(?=[A-Za-z0-9]{2,8}(?![\w-]))"
    r"[a-z]{0,2}[A-Z][A-Za-z0-9]*[A-Z0-9][A-Za-z0-9]*(?![\w-])")

# The whole parenthesis, and the separators a definition actually uses inside
# one. `(CMG2, also ANTXR2)` and `(SAM/ODT)` each introduce two.
_ABBREV_PAREN_RE = re.compile(r"\(([^()\n]{1,90})\)")
_PAREN_SPLIT_RE = re.compile(r"\s*(?:,|;|/|\bor\b|\band\b|\balso\b)\s*")


def _paren_abbrevs(inside: str) -> set[str]:
    """Every abbreviation one parenthesis introduces, plural stripped.

    Takes what is BETWEEN the brackets. A fragment with a space in it is a
    phrase rather than an abbreviation, and one over ten characters is prose;
    everything else is held to the same token regex a bare use is held to, so
    a parenthesis cannot define something that would never be recognised when
    it was used.
    """
    out: set[str] = set()
    for frag in _PAREN_SPLIT_RE.split(inside):
        abbrev = frag.strip().rstrip(".")
        if not abbrev or " " in abbrev or len(abbrev) > 10:
            continue
        if not _ABBREV_TOKEN_RE.fullmatch(abbrev):
            continue
        out.add(abbrev.rstrip("s") or abbrev)
    return out
# --- END ABBREVIATION CONTRACT ---

# A sentence opening on a bare demonstrative with no head noun: "This shows",
# "These indicate", "It follows". MEASURED: this fires on 1.6% of sentences
# and 3 of 24 published abstracts, so a firing carries information - which is
# the whole reason it is reported by default while the abbreviation check is
# not.
_BARE_DEMONSTRATIVE_RE = re.compile(
    r"^\s*(This|These|Those|It|They)\s+"
    r"(?:also\s+|then\s+|thus\s+|therefore\s+|however,?\s+)?"
    r"([a-z]+(?:s|ed|es)?)\b")

# The verbs a bare demonstrative opener runs into. Restricted rather than
# "any lowercase word" so that `This monolayer shows` - a demonstrative WITH
# a head noun, which is the fix - does not fire.
_DEMONSTRATIVE_VERBS = {
    "is", "are", "was", "were", "shows", "show", "showed", "suggests",
    "suggest", "suggested", "indicates", "indicate", "indicated", "means",
    "meant", "implies", "imply", "implied", "gives", "give", "gave",
    "allows", "allow", "allowed", "makes", "make", "made", "leads", "lead",
    "led", "results", "result", "resulted", "explains", "explain",
    "explained", "follows", "follow", "followed", "requires", "require",
    "required", "produces", "produce", "produced", "confirms", "confirm",
    "confirmed", "demonstrates", "demonstrate", "demonstrated", "reflects",
    "reflect", "reflected", "supports", "support", "supported", "can",
    "could", "may", "might", "would", "will", "has", "have", "had", "does",
    "do", "did",
}


def _subject_verb_gap(sentence: str) -> int:
    """Words between the sentence's subject and its verb - Gopen and Swan's
    actual mechanic.

    A long sentence is only sometimes guilty; this is what the guilty ones
    do. Approximated, and deliberately so: the subject is taken as the first
    noun-ish token and the verb as the first finite verb after it, so an
    opening subordinate clause is measured as part of the gap - which is
    correct, because that is exactly what the reader had to hold.
    """
    words = _words(sentence)
    if len(words) < 4:
        return 0
    verbs = {"is", "are", "was", "were", "be", "been", "has", "have", "had",
             "shows", "showed", "show", "suggests", "suggest", "indicates",
             "indicate", "reveals", "reveal", "gives", "give", "produces",
             "produce", "increases", "increase", "decreases", "decrease",
             "remains", "remain", "appears", "appear", "requires", "require",
             "can", "could", "may", "might", "would", "will", "does", "do",
             "did", "occurs", "occur", "forms", "form", "leads", "lead",
             "yields", "yield", "results", "result", "becomes", "become"}
    for i, w in enumerate(words):
        if w.lower() in verbs:
            return max(0, i - 1)
    return 0


def _tail_rank(paragraph: str) -> dict:
    """lead_rank's mirror: where the LAST sentence falls.

    Rule 3 constrains both ends of a paragraph. `voice` has the lead, which
    is half of Context-Content-Conclusion; the closing sentence is where a
    paragraph either hands off or dead-ends, and it is the same computation
    already written.
    """
    sents = _sentences(paragraph)
    if len(sents) < 2:
        return {"rank": None, "of": len(sents), "share": 0.0}
    counts: dict[str, int] = {}
    for sent in sents:
        for w in set(_content_words(sent)):
            counts[w] = counts.get(w, 0) + 1
    recurring = sorted(w for w, n in counts.items() if n > 1)
    distinctive = recurring if len(recurring) > 1 else sorted(counts)
    if not distinctive:
        return {"rank": None, "of": len(sents), "share": 0.0}
    shares = [len(set(_content_words(x)) & set(distinctive)) / len(distinctive)
              for x in sents]
    tail = shares[-1]
    return {"rank": 1 + sum(1 for sh in shares if sh > tail),
            "of": len(sents), "share": round(tail, 3)}


# --- the opening sentence, and the one job it has (item 85) ----------------
#
# The first sentence of a paper is the only one every reader reads, and the
# engine treated it exactly like the rest: same correctness rules, same
# citation rules, no requirement of its own. With no instruction to state what
# is at stake, the drafter reaches for the most citable true fact about the
# subject, which is almost always its PROVENANCE - who named it, what screen
# found it, what it was first called. That fact is true, it cites cleanly, it
# passes every check in the pipeline, and it tells the reader nothing about
# why to keep reading. The construction then propagates into the abstract,
# because the abstract is drafted from the drafted sections.
#
# Deliberately narrow. It reads ONE sentence per section - the first of the
# Introduction and the first of the abstract - because that is the position
# where the wrong sentence costs the most, and because a naming clause
# anywhere else in a paper is ordinary and correct.
_PROVENANCE_OPEN_RE = re.compile(
    r"(?ix)\b(?:"
    r"(?:was|were|is|are)\s+(?:first\s+|originally\s+|initially\s+)?"
    r"(?:named|called|termed|designated|dubbed|coined|christened)\b"
    r"|(?:takes|took|derives|derived|owes|owed|gets|got)\s+its\s+name\b"
    r"|named\s+(?:for|after)\b"
    r"|(?:was|were)\s+(?:first|originally|initially)\s+"
    r"(?:identified|discovered|described|isolated|cloned|reported|"
    r"characteri[sz]ed|observed|detected|recogni[sz]ed)\b"
    r"|(?:first|originally)\s+(?:identified|discovered|described|isolated|"
    r"cloned|reported)\s+(?:in|by|from|as)\b"
    r"|the\s+(?:name|term)\s+[^.]{0,40}?\b(?:derives|comes|refers)\b"
    r")")


def opening_provenance(sentence: str) -> str:
    """The naming clause an opening sentence leads with, or ""."""
    m = _PROVENANCE_OPEN_RE.search(sentence or "")
    return m.group(0) if m else ""


def _abstract_text(text: str) -> str:
    m = re.search(r"^##\s+Abstract\s*$(.*?)(?=^##\s|\Z)", text,
                  re.M | re.DOTALL)
    return m.group(1) if m else ""


def readability(paths: list[str], known_abbreviations: list[str] | None = None,
                audience: str = "specialist",
                report_abbreviations: bool = False) -> dict:
    """What `voice` does not measure, reported and never enforced.

    The abbreviation check is OFF by default and that is measured, not
    cautious. Re-measured 2026-09-10 on 24 real published abstracts in the
    user's own field (mean 190 words), written the obvious way: 112
    firings, 4.7 per abstract, on 21 of the 24. A check that fires on seven
    eighths of peer-reviewed work in the user's own field teaches the
    reader to discount the whole class, and writing-engine-prose 0.2 is
    explicit about what that costs: `density` firing 8 times in 11 on
    Methods is why the genuinely over-budget abstract went unnoticed IN THE
    SAME REPORT.

    The same measurement corrected the exemption the spec assumed. Elements
    and SI units remove NONE of those 112 - what fires is `CH3`, `COOH`,
    `C6H4OH`, `NH2`, `H2O2`, `HS`, `OCH`: chemical FORMULAE, which are
    parsed rather than listed (_is_formula above). With that in, and with
    the scraped publisher boilerplate that rides along in a third of
    Crossref abstracts set aside, what is left is the field's own nouns -
    `XPS`, `FTIR`, `STM`, `DNA`, `PEG` - which is exactly what
    `known_abbreviations` is for.

    So it ships exempted (elements, SI units, and the project's own
    `known_abbreviations`), it runs over the WHOLE MANUSCRIPT in reading
    order rather than per section - an abstract legitimately uses a term the
    introduction defines, which makes the check meaningless at section scope
    - and it reports how many firings it exempted, the same rule `length`
    follows when it states what it counted. `--abbreviations` turns the
    findings on once somebody has calibrated it against real drafted prose.
    """
    known = {k.strip() for k in (known_abbreviations or []) if k.strip()}
    exempt = _ELEMENTS | _UNITS | known
    findings: list[dict] = []
    sections: list[dict] = []

    # Manuscript-wide, in reading order: `paths` arrives in document order
    # and the first occurrence of an abbreviation is a fact about the
    # manuscript, not about the file it happened to land in.
    defined: set = set()
    exempted = 0
    abbrev_firings: list[dict] = []
    prev_tail = ""

    # --- the two openings, before anything else (item 85) -----------------
    # The abstract's first sentence, and the first sentence of the first body
    # section - whatever that section is called, so a review's own first
    # section is read the same way an Introduction is.
    openings: list[tuple[str, str]] = []
    body_paths = [p for p in paths
                  if os.path.splitext(os.path.basename(p))[0]
                  not in ("title_abstract", "live_captions", "toc_graphic")]
    for path in paths:
        stem = os.path.splitext(os.path.basename(path))[0]
        if stem == "title_abstract":
            sents = _sentences(_strip_comments(_abstract_text(_read(path))))
            if sents:
                openings.append(("the abstract", sents[0]))
        elif body_paths and path == body_paths[0]:
            paras0 = paragraphs(_strip_comments(_read(path)))
            sents = _sentences(paras0[0]["text"]) if paras0 else []
            if sents:
                openings.append((stem, sents[0]))
    for where, sent in openings:
        hit = opening_provenance(sent)
        if hit:
            findings.append({
                "location": where, "rule": "opening_states_provenance",
                "severity": "advisory", "sentence": sent[:200],
                "detail": f"the opening sentence's subject is the naming or "
                          f"discovery of the topic (`{hit}`) rather than what "
                          f"is unresolved and who it costs. This is the one "
                          f"sentence every reader reads: state the stake. "
                          f"Provenance may appear later, or not at all",
                "remedy": "open on the problem, the tension, or the cost of "
                          "not knowing - the naming history is not a reason "
                          "to keep reading"})

    for path in paths:
        stem = os.path.splitext(os.path.basename(path))[0]
        text = _strip_comments(_read(path))
        if not text.strip():
            continue
        paras = paragraphs(text)
        rec: dict = {"section": stem, "paragraphs": len(paras),
                     "bare_demonstratives": 0, "given_new": [],
                     "tail_rank_last": 0, "abbreviations": 0,
                     "abbrev_per_100w": 0.0, "words": 0,
                     "max_subject_verb_gap": 0}
        seen_here: set = set()

        for para in paras:
            body = para["text"]
            rec["words"] += len(_words(body))

            # given-new bridge: content-word overlap between this
            # paragraph's first sentence and the previous paragraph's last.
            # Rule 4's "no zig-zag" and Gopen-Swan's topic position, made
            # countable. Zero overlap is a jump. Reuses the outline
            # aligner's own overlap function rather than a second one.
            sents = _sentences(body)
            if sents and prev_tail:
                overlap = len(_tokens(sents[0]) & _tokens(prev_tail))
                rec["given_new"].append(
                    {"location": f"{stem} ¶{para['n']}", "overlap": overlap})
                if overlap == 0:
                    findings.append({
                        "location": f"{stem} ¶{para['n']}",
                        "rule": "given_new_gap", "severity": "advisory",
                        "detail": "this paragraph's first sentence shares no "
                                  "content word with the last sentence of "
                                  "the one before it - the reader is being "
                                  "asked to start somewhere new with no "
                                  "bridge",
                        # The three paragraph-joint findings carry a remedy
                        # because the repair is a SENTENCE THAT DOES NOT
                        # EXIST YET (flow-and-conclusion 2.3). A finding
                        # that says "there is no bridge here" and stops
                        # invites the cheapest possible repair - a
                        # connective word at the front - which the user
                        # named as insufficient before anyone proposed it:
                        # "Transitions were generally rough - not just short
                        # phrases. Whole sentences or ideas are needed."
                        "remedy": "a bridge is a sentence, not a connective. "
                                  "Name what the last paragraph established, "
                                  "then say what this one does with it. A "
                                  "`However,` at the front shares no content "
                                  "word either and is not a fix"})
            if sents:
                prev_tail = sents[-1]

            tr = _tail_rank(body)
            if tr["rank"] is not None and tr["rank"] == tr["of"]:
                rec["tail_rank_last"] += 1
                findings.append({
                    "location": f"{stem} ¶{para['n']}",
                    "rule": "tail_rank_last", "severity": "advisory",
                    "detail": f"the closing sentence ranks last of "
                              f"{tr['of']} by share of what this paragraph "
                              f"is about - the paragraph dead-ends rather "
                              f"than handing off",
                    "remedy": "the closing sentence is the hand-off. Either "
                              "move the paragraph's point out of it, or "
                              "replace it with what the next paragraph needs "
                              "the reader holding"})

            for sent in sents:
                m = _BARE_DEMONSTRATIVE_RE.match(sent)
                if m and m.group(2).lower() in _DEMONSTRATIVE_VERBS:
                    rec["bare_demonstratives"] += 1
                    findings.append({
                        "location": f"{stem} ¶{para['n']}",
                        "rule": "bare_demonstrative", "severity": "advisory",
                        "detail": f"opens `{m.group(1)} {m.group(2)}` with no "
                                  f"head noun - the most common cause of "
                                  f"having to read a sentence twice",
                        "remedy": f"`{m.group(1)} <what>` names the referent"})
                gap = _subject_verb_gap(sent)
                rec["max_subject_verb_gap"] = max(
                    rec["max_subject_verb_gap"], gap)
                if gap >= 12:
                    findings.append({
                        "location": f"{stem} ¶{para['n']}",
                        "rule": "subject_verb_gap", "severity": "advisory",
                        "detail": f"{gap} words between the subject and its "
                                  f"verb. This is what a long sentence does "
                                  f"when it is guilty; length alone is not",
                        "sentence": sent[:160]})

                for am in _ABBREV_TOKEN_RE.finditer(sent):
                    token = am.group(0)
                    if (token in exempt or token.rstrip("s") in exempt
                            or _is_formula(token)):
                        exempted += 1
                        continue
                    if token in defined:
                        continue
                    defined.add(token)
                    seen_here.add(token)
                    # The commonest definition by far is the token BEING
                    # the parenthetical: `self-assembled monolayers (SAMs)`.
                    # A check that only looked for a parenthetical BEFORE
                    # the token fired on every correctly defined
                    # abbreviation in the paper, which is the worst
                    # possible direction for a check that is already
                    # fragile enough to ship switched off.
                    #
                    # Item 101: the parenthesis is read WHOLE and split, so
                    # one that introduces the abbreviation alongside a
                    # synonym or a second abbreviation defines both. Two
                    # parentheses count - one that closed before this token,
                    # and the one holding it. A parenthesis that opens after
                    # it does not: the finding is that the term is used
                    # BEFORE it is expanded.
                    stem_tok = token.rstrip("s") or token
                    expanded = False
                    for pm in _ABBREV_PAREN_RE.finditer(sent):
                        holds = (pm.start() <= am.start()
                                 and am.end() <= pm.end())
                        if not (holds or pm.end() <= am.start()):
                            continue
                        if stem_tok in _paren_abbrevs(pm.group(1)):
                            expanded = True
                            break
                    if not expanded:
                        abbrev_firings.append({
                            "location": f"{stem} ¶{para['n']}",
                            "rule": "abbreviation_undefined",
                            "severity": "advisory", "token": token,
                            "detail": f"`{token}` is used before it is "
                                      f"expanded anywhere in the manuscript",
                            "sentence": sent[:160]})
            rec["abbreviations"] = len(seen_here)

        rec["abbrev_per_100w"] = (round(100.0 * len(seen_here) / rec["words"],
                                        2) if rec["words"] else 0.0)
        sections.append(rec)

    # topic return: a content word present in paragraph n and n+k and absent
    # between them. Rule 4's zig-zag across a whole section rather than one
    # boundary.
    returns: list[dict] = []
    for path in paths:
        stem = os.path.splitext(os.path.basename(path))[0]
        paras = paragraphs(_strip_comments(_read(path)))
        vocab = [_tokens(pp["text"]) for pp in paras]
        for i in range(len(vocab)):
            for k in range(2, min(5, len(vocab) - i)):
                shared = vocab[i] & vocab[i + k]
                between = set().union(*vocab[i + 1:i + k]) if k > 1 else set()
                gone = shared - between
                if gone:
                    returns.append({
                        "location": f"{stem} ¶{paras[i]['n']} -> "
                                    f"¶{paras[i + k]['n']}",
                        "terms": sorted(gone)[:5], "gap": k})
    for r in returns:
        findings.append({
            "location": r["location"], "rule": "topic_return",
            "severity": "advisory",
            "detail": f"{', '.join(r['terms'])} comes back after "
                      f"{r['gap']} paragraph(s) away - the reader has to "
                      f"reload it",
            "remedy": f"the reader last saw this {r['gap']} paragraphs ago. "
                      f"Either restate it where it returns, or move the two "
                      f"mentions together"})

    if report_abbreviations:
        findings.extend(abbrev_firings)

    return {
        "audience": audience,
        "sections": sections,
        "findings": findings,
        "abbreviations": {
            "firings": len(abbrev_firings),
            "exempted": exempted,
            "known": sorted(known),
            "reported": report_abbreviations,
            # Said in the output, not only in a spec: a measure whose
            # calibration is unstated is a measure whose reader has to guess
            # whether to believe it.
            "note": ("measured naively this fires 3.5 times per published "
                     "abstract, on 18 of 24 real papers in this field, so "
                     "it is OFF by default and is not in `findings` until "
                     "somebody calibrates it against real drafted prose. "
                     "--abbreviations turns it on."
                     if not report_abbreviations else
                     "reported at your request; %d firing(s) were exempted "
                     "as elements, SI units or known_abbreviations"
                     % exempted),
        },
        "topic_returns": returns,
        "counts": {"findings": len(findings),
                   "sections": len(sections)},
        # Every finding here is advisory at every dial value, and this
        # command always exits 0. A measurement instrument may not become a
        # gate: a gate on a prose measurement makes the drafter optimize the
        # metric, and the metric is not the thing anybody wants.
        "advisory": True,
        "register_note": (
            "no reading level is computed and none will be. Measured on 192 "
            "sentences of published prose in this field: mean 22.7 words, sd "
            "15.7, 40% over 25 words, 29% passive. A general-audience "
            "standard would flag two fifths of peer-reviewed work here and "
            "be wrong every time. The yardstick is `audience`, which this "
            "report states: " + audience),
    }


# ---------------------------------------------------------------------------
# voice - prose 3.4. An instrument, never a gate.
# ---------------------------------------------------------------------------
# There is no threshold in this command and there must never be one. A gate on
# a prose measurement recreates the problem the whole spec is about one layer
# up: the drafter optimizes the metric, and mean sentence length is not the
# thing anybody wants. What this hands the revision pass is a MAP, so its
# judgment is informed rather than impressionistic - which is also why the
# standard deviation is reported beside the mean. A fix that lowers the mean
# while collapsing the sd has made the prose worse, and only the sd can say so.


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _sd(xs: list[float]) -> float:
    """Population sd, because these are the sentences of the section and not a
    sample of some larger population of them."""
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def _content_words(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[A-Za-z][A-Za-z-]{2,}", mask_excluded(text)):
        low = w.lower().strip("-")
        if len(low) >= 4 and low not in STOPWORDS:
            out.append(low)
    return out


def lead_rank(paragraph: str) -> dict:
    """Where the paragraph's first sentence falls when its sentences are ranked
    by share of the paragraph's distinctive content words.

    The digest calls the paragraph-lead rule non-negotiable, and until now
    nothing in `tools/` could see it - `grep` finds it only in prose. A lead
    that ranks LAST contains nothing the paragraph is about, which is what a
    throat-clearing opener is: "Several factors influence film growth."

    Distinctive means recurring first: a content word the paragraph uses in
    more than one sentence is a word the paragraph is about. Below two such
    words there is nothing to discriminate with - measured on a real paragraph
    whose only recurring word was `film`, every sentence mentioning film scored
    1.0 and the throat-clearing opener came out first - so the fallback is the
    paragraph's whole content vocabulary, where a five-word opener covering two
    of nine terms ranks where it belongs. With no content words at all the
    honest answer is None rather than a rank of 1.

    Reported. Never a verdict - a rank of last exits 0 like everything else
    here.
    """
    sents = _sentences(paragraph)
    if not sents:
        return {"rank": None, "of": 0, "share": 0.0, "distinctive": []}
    counts: dict[str, int] = {}
    for s in sents:
        for w in set(_content_words(s)):
            counts[w] = counts.get(w, 0) + 1
    recurring = sorted(w for w, n in counts.items() if n > 1)
    distinctive = recurring if len(recurring) > 1 else sorted(counts)
    if not distinctive or len(sents) < 2:
        return {"rank": None, "of": len(sents), "share": 0.0,
                "distinctive": distinctive}
    shares = []
    for s in sents:
        have = set(_content_words(s)) & set(distinctive)
        shares.append(len(have) / len(distinctive))
    lead = shares[0]
    # Rank by share, ties sharing the better rank: a lead that ties the best
    # sentence is not "second".
    rank = 1 + sum(1 for sh in shares if sh > lead)
    return {"rank": rank, "of": len(sents), "share": round(lead, 3),
            "distinctive": distinctive}


def _count_words(text: str, words: list[str]) -> int:
    low = " " + " ".join(_words(text)).lower() + " "
    total = 0
    for w in words:
        total += len(re.findall(rf"(?<![\w-]){re.escape(w)}(?![\w-])", low))
    return total


def _sentence_measures(sentence: str) -> dict:
    n_words = len(_words(sentence))
    keys = CITEKEY_RE.findall(re.sub(r"`[^`]*`", " ", sentence))
    nums = count_numbers(sentence)
    return {"words": n_words, "citekeys": sorted(set(keys)),
            "numbers": nums["numeric_tokens"],
            "passive": bool(PASSIVE_RE.search(sentence))}


def voice(paths: list[str], per_paragraph: bool = False) -> dict:
    """How the prose reads, per section. Reports; exits 0 always."""
    sections: list[dict] = []
    for path in paths:
        stem = os.path.splitext(os.path.basename(path))[0]
        text = _read(path)
        if not text.strip():
            sections.append({"section": stem, "words": 0, "sentences": 0,
                             "empty": True})
            continue
        paras = paragraphs(text)
        sent_recs: list[dict] = []
        para_recs: list[dict] = []
        for p in paras:
            body = p["text"]
            sents = _sentences(body)
            measures = [_sentence_measures(s) for s in sents]
            for s, m in zip(sents, measures):
                sent_recs.append({**m, "text": s, "paragraph": p["n"]})
            lr = lead_rank(body)
            words_here = len(_words(_strip_comments(body)))
            rec = {
                "n": p["n"], "line": p["line"], "words": words_here,
                "sentences": len(sents),
                "opener_words": measures[0]["words"] if measures else 0,
                "lead_rank": lr["rank"], "lead_rank_of": lr["of"],
                "lead_share": lr["share"],
                "lead": (sents[0][:160] if sents else ""),
                "hedges": _count_words(body, HEDGE_WORDS),
                "transitions": _count_words(body, TRANSITION_WORDS),
                "nominalizations": len(NOMINALIZATION_RE.findall(body)),
                "nominalizations_with_weak_verb": sum(
                    1 for m in NOMINALIZATION_RE.finditer(body)
                    if _near_weak_verb(body, m.start(), m.end())),
                "passive_sentences": sum(1 for m in measures if m["passive"]),
            }
            para_recs.append(rec)

        lens = [float(m["words"]) for m in sent_recs]
        openers = [float(r["opener_words"]) for r in para_recs]
        para_lens = [float(r["words"]) for r in para_recs]
        words_total = sum(int(r["words"]) for r in para_recs)
        nominal = sum(int(r["nominalizations"]) for r in para_recs)
        weak_nominal = sum(int(r["nominalizations_with_weak_verb"])
                           for r in para_recs)
        hedges = sum(int(r["hedges"]) for r in para_recs)
        transitions = sum(int(r["transitions"]) for r in para_recs)
        per100 = (100.0 / words_total) if words_total else 0.0
        longest = sorted(sent_recs, key=lambda r: -r["words"])[:5]

        rec_out = {
            "section": stem, "empty": False,
            "words": words_total, "sentences": len(sent_recs),
            "paragraphs": len(para_recs),
            "sentence_words": {
                "mean": round(_mean(lens), 1), "sd": round(_sd(lens), 1),
                "median": round(_median(lens), 1),
                "max": int(max(lens)) if lens else 0},
            "longest": [{"words": r["words"], "paragraph": r["paragraph"],
                         "text": r["text"][:240]} for r in longest],
            "openers": {"mean": round(_mean(openers), 1),
                        "sd": round(_sd(openers), 1),
                        "max": int(max(openers)) if openers else 0,
                        "each": [int(o) for o in openers]},
            "leads_ranked_last": sum(
                1 for r in para_recs
                if r["lead_rank"] is not None
                and r["lead_rank"] == r["lead_rank_of"]),
            "citekeys": {
                "per_sentence": round(
                    _mean([float(len(m["citekeys"])) for m in sent_recs]), 2),
                "sentences_with_two_plus": sum(
                    1 for m in sent_recs if len(m["citekeys"]) >= 2)},
            "numbers": {"per_sentence": round(
                _mean([float(m["numbers"]) for m in sent_recs]), 2)},
            "hedges": {"count": hedges,
                       "per_100_words": round(hedges * per100, 2)},
            "transitions": {"count": transitions,
                            "per_100_words": round(transitions * per100, 2)},
            "nominalizations": {"count": nominal,
                                "with_weak_verb": weak_nominal,
                                "per_100_words": round(nominal * per100, 2)},
            "passive": {"sentences": sum(1 for m in sent_recs if m["passive"]),
                        "fraction": round(
                            _mean([1.0 if m["passive"] else 0.0
                                   for m in sent_recs]), 2)},
            "paragraph_words": {"mean": round(_mean(para_lens), 1),
                                "sd": round(_sd(para_lens), 1)},
        }
        if per_paragraph:
            rec_out["paragraph_detail"] = para_recs
        sections.append(rec_out)

    return {"sections": sections,
            "counts": {"sections": len(sections),
                       "words": sum(int(s.get("words", 0)) for s in sections),
                       "sentences": sum(int(s.get("sentences", 0))
                                        for s in sections)}}


def _near_weak_verb(text: str, start: int, end: int, window: int = 40) -> bool:
    """Is a weak verb sitting beside this nominalization?

    [GS]'s pattern is the pair, not the noun: "an investigation was performed"
    for "we investigated". The bare count is reported too, because the ratio
    between the two is the half that says whether the nouns are doing harm.
    """
    around = text[max(0, start - window):end + window].lower()
    return any(re.search(rf"(?<![\w-]){v}(?![\w-])", around)
               for v in WEAK_VERBS)


# ---------------------------------------------------------------------------
# metaprose - prose 0.2. Pipeline text that reached the manuscript.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# claim - the abstract's named take-home (prose 1.3)
# ---------------------------------------------------------------------------
#
# Module 1b derives the take-home from the drafted text and is denied the
# README, the outline, the hypothesis and the conversation. That is correct
# and does not change: an abstract agent that has read the hypothesis will
# write the abstract the hypothesis wanted.
#
# What was missing is not an input. It is an OUTPUT COMMITMENT. Nothing
# required 1b to decide WHICH of the available claims is the one, and an agent
# that has not committed to one claim has only one way to signal importance -
# state more results. That is the measured shape of the abstract this was
# written against: 22 inline numbers against a 5.88 budget, 6 of 8 sentences
# over 25 words, a 55-word opener. The number density there is a symptom.
#
# So both checks below are MECHANICAL, and deliberately so. This is layer 2
# rule 1 - one central contribution - with the only enforcement that rule can
# honestly have: asking the agent to name the contribution rather than grading
# its prose.

CLAIM_SECTION_RE = re.compile(r"^##\s*(.+?)\s*$", re.MULTILINE)

# `source_text/results.md ¶4` or `results ¶4` or just `discussion`.
CLAIM_CITE_RE = re.compile(
    r"([A-Za-z_]+)(?:\.md)?(?:\s*¶\s*\d+)?", re.IGNORECASE)

# A LOCATOR, and nothing else, is what `## Supported by` cites (item 38).
#
# The old parse split the heading's body on commas and newlines and searched
# inside each fragment for something file-shaped. A person writing this
# report writes a one-line note under each heading - which is how a person
# writes a report - and the note "results.md and discussion.md are still
# empty stubs" was then read as citing results and discussion: the two files
# the note was explicitly saying carry nothing. Moving the note into an HTML
# comment did not help, because the comment was parsed too. Three rewrites of
# a correct abstract, and every failure was about the file's punctuation.
#
# So the match is anchored: the WHOLE fragment has to be a locator, not
# contain one. Prose does not accidentally satisfy that. A bare section name
# with no paragraph number stays legal - `results` on its own line is the
# documented shape and is unambiguous - but it has to be the whole line.
CLAIM_LOCATOR_RE = re.compile(
    r"""^
        (?:[\w.@+-]+[/\\])*                          # optional directory
        (?P<stem>[A-Za-z][\w-]*)                     # file or section name
        (?:\.md)?                                    # written either way
        (?:\s*(?:¶|para\.?|paragraph)\s*\d+)?   # optional paragraph
        $""",
    re.IGNORECASE | re.VERBOSE)


def _claim_locators(body: str) -> tuple[list[str], list[dict]]:
    """Split `## Supported by` into the locators and the prose to ignore.

    Returned second is what was skipped and why. A line silently dropped is
    the same defect from the other side: the author who wrote `see the r0
    build for context` should be told that line cited nothing, not left to
    infer it from a support list one entry short.
    """
    cited: list[str] = []
    ignored: list[dict] = []
    for raw in body.splitlines():
        line = re.sub(r"^\s*[-*+]\s+", "", raw).strip()
        line = re.sub(r"[.;,]+$", "", line).strip()
        if not line:
            continue
        parts = [p.strip().strip("`*_").strip() for p in line.split(",")]
        parts = [p for p in parts if p]
        if parts and all(CLAIM_LOCATOR_RE.match(p) for p in parts):
            cited.extend(parts)
        else:
            ignored.append({"line": " ".join(line.split()),
                            "why": "not a source locator - a path, or a "
                                   "section name with a paragraph number"})
    return cited, ignored


def parse_abstract_claim(text: str) -> dict:
    """`## Take-home` and `## Supported by` out of the prose report.

    HTML comments come off first, here as everywhere else: they are invisible
    in every other consumer of these files, and a parser that reads them is a
    parser that disagrees with what the author is looking at.
    """
    out: dict = {"claim": "", "supported_by": [], "sections": [],
                 "ignored": []}
    text = _strip_comments(text)
    parts = re.split(r"^##\s+", text, flags=re.MULTILINE)
    for part in parts[1:]:
        head, _, body = part.partition("\n")
        key = head.strip().lower()
        if key.startswith("take-home") or key.startswith("take home"):
            # The first sentence of the first paragraph, and then stop.
            #
            # The take-home is ONE sentence by construction; anything after
            # it is the author talking, and folding a one-line Note in
            # diluted a 100% abstract overlap to 45% against an abstract
            # that was right throughout (item 38). Reading the whole block
            # is what did that.
            #
            # Not "the first line": the documented example hard-wraps its
            # sentence over two lines, and half a sentence is a worse claim
            # than a diluted one. Take the paragraph, then its first
            # sentence - which also drops a note written with no blank line
            # in front of it.
            para: list[str] = []
            for line in body.splitlines():
                if line.strip():
                    para.append(line.strip())
                elif para:
                    break
            joined = " ".join(" ".join(para).split())
            sent = _sentences(joined)
            out["claim"] = sent[0] if sent else joined
        elif key.startswith("supported"):
            cited, ignored = _claim_locators(body.strip())
            out["supported_by"] = cited
            out["ignored"] = ignored
    seen: list[str] = []
    for ref in out["supported_by"]:
        m = CLAIM_LOCATOR_RE.match(os.path.basename(ref))
        if not m:
            continue
        name = m.group("stem").lower()
        stem = SECTION_ALIASES.get(name, name)
        # A review's body stems are the project's own, so they cannot be
        # enumerated here - this function is handed text and no project.
        # The SHAPE is checkable, and that is what is checked
        # (review-paper 7).
        if stem in SECTION_FILES or REVIEW_STEM_RE.match(stem):
            if stem not in seen:
                seen.append(stem)
        else:
            out["ignored"].append(
                {"line": ref,
                 "why": "names no section file; the sections are "
                        + ", ".join(SECTION_FILES)
                        + ", or a review's own NN_slug stems"})
    out["sections"] = seen
    return out


def claim_report_path(project: str) -> str:
    """Where the take-home is written, and where it used to be.

    It folded into `prose_report.md` under its own headings (item 43): one
    module writes both, the claim is a fact about the abstract's prose, and a
    four-line file of its own was one more thing in a folder the user had
    stopped opening. A project that still has the old file is read from it
    rather than told it has nothing - the report is a record, and moving the
    engine must not make an existing record invisible.
    """
    new = os.path.join(project, "reports", "prose_report.md")
    old = os.path.join(project, "reports", "abstract_claim.md")
    if not os.path.isfile(new) and os.path.isfile(old):
        return old
    return new


def claim(project: str, report: str | None = None) -> dict:
    """The two conditions final-check verifies, neither a style judgment."""
    path = report or claim_report_path(project)
    findings: list[dict] = []
    text = _read(path) if os.path.isfile(path) else ""
    parsed = parse_abstract_claim(text)

    if not text.strip():
        # Absent is a FINDING, not a build failure (6). The paper is still
        # assemblable; what is missing is the commitment, and saying so is the
        # whole of the remedy.
        findings.append({
            "location": os.path.basename(path), "rule": "claim_absent",
            "severity": "warning",
            "detail": "no `## Take-home` in %s. Module 1b names the one "
                      "take-home the abstract carries and the sections that "
                      "support it; without it nothing requires the abstract "
                      "to commit to a single claim, and an abstract that has "
                      "not committed signals importance the only other way "
                      "it can - by stating more results"
                      % os.path.basename(path)})
        return {"path": path, "claim": "", "supported_by": [], "sections": [],
                "ignored": [], "findings": findings,
                "counts": {"findings": len(findings), "supported": 0}}

    if not parsed["claim"]:
        findings.append({
            "location": os.path.basename(path), "rule": "claim_empty",
            "severity": "warning",
            "detail": "%s has no `## Take-home` sentence"
                      % os.path.basename(path)})
    if not parsed["sections"]:
        # An ignored line is named here and nowhere else. Reporting nothing
        # behind the claim while the author is looking at a `## Supported by`
        # with three lines under it sends them to rewrite the abstract; what
        # is wrong is the shape of those lines (item 38).
        why = ""
        if parsed["ignored"]:
            why = ". " + "; ".join(
                f"ignored `{i['line'][:60]}` - {i['why']}"
                for i in parsed["ignored"][:3])
        findings.append({
            "location": os.path.basename(path), "rule": "claim_unsourced",
            "severity": "warning",
            "detail": "no `## Supported by` naming section files. A claim "
                      "with nothing behind it is the thing this file exists "
                      "to prevent" + why})

    want = set(_content_words(parsed["claim"]))
    st = source_text_dir(project)

    # Condition 1: the claim's content words appear in the sections it cites.
    for stem in parsed["sections"]:
        body = _read(os.path.join(st, f"{stem}.md"))
        have = set(_content_words(body))
        missing = sorted(want - have)
        if want and len(missing) > len(want) / 2:
            findings.append({
                "location": f"{stem}.md", "rule": "claim_unsupported",
                "severity": "warning", "claim": parsed["claim"],
                "detail": f"the take-home cites {stem}.md, but "
                          f"{len(missing)} of its {len(want)} content words "
                          f"do not appear there: "
                          f"{', '.join(missing[:8])}"})

    # Condition 2: the abstract carries a sentence bearing the claim.
    # title_abstract.md is a form, and its `# Abstract` label is not a
    # sentence of the abstract. Left in, the heading rides along on the front
    # of the sentence it precedes and the reported "sentence carrying the
    # claim" is not one.
    ta = "\n".join(
        l for l in _read(os.path.join(st, "title_abstract.md"))
        .splitlines() if not l.lstrip().startswith("#"))
    best, best_score = "", 0.0
    for sentence in _sentences(ta):
        have = set(_content_words(sentence))
        if not want or not have:
            continue
        score = len(want & have) / len(want)
        if score > best_score:
            best, best_score = sentence, score
    if want and best_score < 0.5:
        findings.append({
            "location": "title_abstract.md", "rule": "claim_not_in_abstract",
            "severity": "warning", "claim": parsed["claim"],
            "detail": f"no sentence in the abstract carries the take-home "
                      f"(best overlap {best_score:.0%}"
                      + (f", '{best[:70]}'" if best else "") + ")"})

    return {"path": path, "claim": parsed["claim"],
            "supported_by": parsed["supported_by"],
            "sections": parsed["sections"],
            "ignored": parsed["ignored"],
            "abstract_sentence": best if best_score >= 0.5 else "",
            "abstract_overlap": round(best_score, 2),
            "findings": findings,
            "counts": {"findings": len(findings),
                       "supported": len(parsed["sections"]),
                       "ignored": len(parsed["ignored"])}}


def print_readability(res: dict) -> None:
    print(f"  reader: {res['audience']}")
    for sec in res["sections"]:
        print(f"  {sec['section']:18} {sec['words']:>5}w  "
              f"{sec['paragraphs']:>2} ¶  "
              f"bare demonstratives {sec['bare_demonstratives']:>2}  "
              f"abbrevs {sec['abbreviations']:>2} "
              f"({sec['abbrev_per_100w']}/100w)  "
              f"max subj-verb gap {sec['max_subject_verb_gap']:>2}")
    by_rule: dict = {}
    for f in res["findings"]:
        by_rule.setdefault(f["rule"], []).append(f)
    for rule, items in sorted(by_rule.items()):
        print(f"\n  {rule}  ({len(items)})")
        for f in items[:8]:
            print(f"    {f['location']:22} {f['detail']}")
        if len(items) > 8:
            print(f"    ... {len(items) - 8} more")
    ab = res["abbreviations"]
    print(f"\n  abbreviations: {ab['firings']} firing(s), {ab['exempted']} "
          f"exempted as elements, SI units or known_abbreviations")
    print(f"    {ab['note']}")
    print(f"\n  {res['register_note']}")


def print_claim(res: dict) -> None:
    if not res["claim"]:
        print("no take-home named")
    else:
        print(f"take-home: {res['claim']}")
        print(f"  supported by: {', '.join(res['sections']) or '(nothing)'}")
        if res.get("abstract_sentence"):
            print(f"  carried in the abstract by: "
                  f"{res['abstract_sentence'][:100]}")
    for i in res.get("ignored", []):
        # Said out loud even when the claim is fully sourced: a note under
        # `## Supported by` is legitimate, and the author should be able to
        # see that it was read as a note rather than as a citation.
        print(f"  ignored under `## Supported by`: {i['line'][:70]}")
        print(f"    {i['why']}")
    print("")
    for f in res["findings"]:
        print(f"  {f['severity']:8} {f['location']}  {f['detail']}")
    if not res["findings"]:
        print("  the claim's words are in the sections it cites, and the "
              "abstract carries it")


# ---------------------------------------------------------------------------
# ai_voice - the countable half of the AI-voice audit (prose 10.3)
# ---------------------------------------------------------------------------
#
# 15 of the 25 patterns in writing_guides/ai_patterns_audit.md land here. Not
# one of them carries a threshold, and the command exits 0 on a file full of
# findings: under prose 0.2 nothing in this audit may block, and under 10.1
# published prose is a FLOOR for a check that is wrong when it fires on good
# work and NOT A TARGET for a style principle the paper is trying to beat. All
# 15 are the second kind, so they report a count and a location and carry no
# bar at all.
#
# Four of them are DENSITIES rather than events - #10, #13, #14, #16 - and the
# distinction is load-bearing downstream, not cosmetic. A finding names a
# sentence and can be fixed by a local edit; a density names no sentence, and
# the only way to act on one is to hunt the construct and eliminate it, which
# is metric optimization by definition. 10.10: the densities go to the user
# and are not passed to the rewrite pass at all.
#
# What is NOT here, and must not be added:
#   #5  vague attribution - claim_unsourced, background_uncited and
#       citekey_missing are stricter already. A second copy is a defect.
#   #18-20 chatbot artifacts - they are not prose, they are chatbot text that
#       reached a manuscript, and they take the FLAG path through metaprose's
#       detector (CHATBOT_ARTIFACT_RE), one detector family per shape (item 7).
#   #25's structural half - tail_rank_last, given_new_gap and topic_return in
#       `readability` already measure it. What is added here is only the shape
#       readability does not see.
#   #1-4, #6, #24 - judgment, and module 1e's.

# #21. A flat substitution table with no judgment in it: each left-hand side
# means the right-hand side and costs the reader more words to say it.
FILLER_PHRASES = [
    (r"in order to\b", "to"),
    (r"due to the fact that\b", "because"),
    (r"at this point in time\b", "now"),
    (r"it is important to note that\b", "delete, or restate directly"),
    (r"it should be noted that\b", "delete"),
    (r"as mentioned above\b", "restructure, or delete"),
]

# #8. "serves as" where "is" would do. The audit file itself says to KEEP it
# where the serving relationship is scientifically meaningful, so this is a
# location and a sentence handed to 1e, never an instruction to substitute.
COPULA_AVOIDANCE = ["serves as", "serve as", "functions as", "function as",
                    "acts as", "act as", "stands as", "stand as",
                    "operates as", "operate as"]

# #7 and #17 ship in TWO TIERS, and prose 0.2's word-list warning is why. A
# flat list catches "the film was deliberately over-oxidized", which is real
# science. `robust to noise` is a claim, `high-throughput` is the name of a
# method, and `comprehensive` is an ordinary scientific word.
#
# Tier 1 is words with no scientific use at all - there is no experiment that
# delves, and no measurement that is a testament. These are asserted.
AI_VOCAB_UNCONDITIONAL = ["delve", "delves", "delved", "delving",
                          "testament", "showcase", "showcases", "showcased",
                          "showcasing", "shed light on", "sheds light on",
                          "shedding light on", "unpack", "unpacks",
                          "unpacked", "unpacking", "endeavor", "endeavour",
                          "endeavors", "endeavours"]

# Tier 2 is reported WITH ITS SENTENCE and never asserted as a defect. Where a
# tier cannot be decided by shape it is the module's job, not the check's
# (10.3), and 10.11 names #7/#17 as a case where `leave` is often right.
AI_VOCAB_CONTEXTUAL = ["landscape", "crucial", "pivotal", "nuanced",
                       "comprehensive", "robust", "seamless", "notable",
                       "leverage", "leverages", "leveraging", "foster",
                       "fosters", "empower", "empowers", "utilize",
                       "utilizes", "utilise", "utilises", "ascertain",
                       "underscore", "underscores", "highlight", "highlights"]

# #17. Same two tiers, same reason: `state-of-the-art` is a slogan wherever it
# appears; `high-throughput` is what the method is called.
HYPHEN_COMPOUND_UNCONDITIONAL = ["cutting-edge", "state-of-the-art",
                                 "game-changing", "next-generation",
                                 "world-class"]
HYPHEN_COMPOUND_CONTEXTUAL = ["data-driven", "high-throughput",
                              "high-performance", "fine-grained",
                              "large-scale", "real-world"]

# #23. A conclusion that would fit any paper in any field is a conclusion that
# says nothing about this one.
GENERIC_CONCLUSIONS = [
    r"more research is needed",
    r"further (?:studies|work|research) (?:are|is) (?:needed|warranted|required)",
    r"future (?:work|studies|research) should (?:investigate|explore|examine|address)",
    r"future (?:work|studies|research) (?:will|may) (?:investigate|explore|examine|address)",
    r"additional (?:studies|work|research) (?:are|is) (?:needed|warranted|required)",
]

# #9. "It is not merely X, but rather Y" - the positive is in there and the
# construction declines to lead with it.
NEGATIVE_PARALLELISM_RE = re.compile(
    r"\b(?:is|are|was|were|does|do|did)\s+not\s+"
    r"(?:merely|just|simply|only)\b[^.;]{0,120}?"
    r"(?:,\s*but\s+rather\b|\s+but\s+rather\b|;\s*it\s+|,\s*but\b)",
    re.IGNORECASE)

# #12. "from X to Y" joining unrelated items. The shape is the tell: a real
# range is numeric or shares a unit, so a range whose endpoints are bare nouns
# is the one being reported.
FALSE_RANGE_RE = re.compile(
    r"\brang(?:e|es|ing|ed)\s+from\s+([^.;,]{2,60}?)\s+to\s+([^.;,]{2,60}?)"
    r"(?=[.;,]|$)", re.IGNORECASE)

# #15. A bold label opening a line, with its description after the colon. The
# audit file's note is that this is common in AI output and absent in Nature
# papers; the check is one regex and no judgment.
INLINE_HEADER_RE = re.compile(r"^\s*\*\*[^*\n]{1,60}:?\*\*:?\s+\S", re.M)

# #16. The density is the position, not a per-manuscript maximum (10.5): a
# paragraph opening on `Furthermore` has not stated its own claim, it has
# borrowed its reason for existing from the paragraph before it.
PARAGRAPH_OPENER_FILLER = TRANSITION_WORDS + [
    "in conclusion", "in summary", "notably", "importantly", "crucially"]

# #13. U+2014 ONLY, and this is measured rather than assumed. The Biochem Res
# Int 2022 paper in writing_guides/ contains 22 en dashes and ALL 22 are number
# ranges, so a check counting "em/en dashes" reports a chemistry paper's
# `2-3 nm` as a style tell. Never count U+2013.
EM_DASH = "—"
EN_DASH = "–"

# #11. Synonym cycling, for THE TWO ENTITY SETS THE AUDIT FILE NAMES and no
# others. This agrees with the house digest's terminology rule rather than
# contradicting it, which is what makes it safe to land: the digest says
# repeat the technical term.
#
# Two more sets were written here first - method/methodology/approach/
# technique, and results/findings/outcomes - and both are cut. Neither is in
# the source digest; both were invented while implementing it, which is the
# thing 10.3 says not to do ("implementation starts from that file, not from
# a paraphrase of it").
#
# The calibration caught the first one: measured 2026-09-16 over 24 published
# chemistry abstracts, `method`/`technique` was the ONLY asserted finding any
# of 10.7's five gated patterns produced (1 of 24), and it was right about the
# words and wrong about the entity - in this field a technique is XPS, a
# method is the procedure, and an approach is the strategy. The second is cut
# by the same argument without needing a firing: the digest's own resolution
# of rule 4 is that repetition wins FOR TECHNICAL TERMS AND DEFINED QUANTITIES
# and [N]'s vary-your-words rule survives for ordinary vocabulary. `results`
# and `findings` are ordinary vocabulary, so a check demanding one spelling of
# them contradicts the digest - and per 10.9 a pattern that contradicts the
# digest is dropped, not merged.
SYNONYM_SETS = [
    ["participants", "subjects", "individuals"],
    ["samples", "specimens", "materials"],
]

AI_VOICE_DENSITY_RULES = ("rule_of_three", "em_dash_density",
                          "boldface_density", "paragraph_opener_filler")


def metaprose(paths: list[str], insert: bool = False) -> dict:
    """Text whose subject is the document itself, flagged and left alone.

    A drafted Results section has contained a paragraph explaining the
    pipeline's own intent - "This section is deliberately without numbers..." -
    which would have built into the .docx. The instinct is a phrase list and a
    build failure, and both halves are wrong: a word list catches "the film was
    deliberately over-oxidized", which is real science, and a build failure on
    a false positive is the class of prohibition that teaches the user to stop
    reading the report.

    So the match is the grammatical shape - the subject of the clause is the
    document - plus bare TODO/FIXME/XXX/lorem, and nothing else. On a match a
    `**[FLAG: meta-prose ...]**` goes in and the paragraph's words are left
    exactly as they were. The user already has a workflow for flags, `assemble`
    already counts them, and a flag the user dismisses costs one line.
    """
    hits: list[dict] = []
    written: list[str] = []
    for path in paths:
        stem = os.path.splitext(os.path.basename(path))[0]
        text = _read(path)
        if not text.strip():
            continue
        here: list[dict] = []
        for p in paragraphs(text):
            body = p["text"]
            if "meta-prose" in body and FLAG_RE.search(body):
                continue        # already flagged; flagging twice says nothing
            why = None
            m = META_SUBJECT_RE.search(mask_excluded(body))
            if m:
                why = ("the subject of the clause is the document: "
                       f"{' '.join(m.group(0).split())!r}")
            else:
                m2 = META_PLACEHOLDER_RE.search(body)
                if m2:
                    why = f"a bare placeholder: {m2.group(0)!r}"
                else:
                    # Patterns 18-20. Same detector, same flag, same
                    # insert-don't-substitute rule - the words are left
                    # exactly as they are and the user decides.
                    m3 = CHATBOT_ARTIFACT_RE.search(mask_excluded(body))
                    if m3:
                        why = ("chatbot text, not manuscript text: "
                               f"{' '.join(m3.group(0).split())!r}")
            if not why:
                continue
            first = next((l for l in body.splitlines() if l.strip()), "")
            hit = {"file": path, "location": f"{stem} ¶{p['n']}",
                   "line": p["line"], "detail": why,
                   "severity": "info", "rule": "meta_prose",
                   "flagged": False, "text": body[:200], "first_line": first}
            here.append(hit)
            hits.append(hit)

        if not (insert and here):
            continue
        # Anchored on the paragraph's own first line rather than on a text
        # search, and inserted rather than substituted: the mask must not
        # change what it is masking, which is the same rule _strip_comments
        # follows for line numbers.
        lines = text.splitlines(keepends=True)
        for hit in here:
            target = hit["first_line"].strip()
            if not target:
                continue
            start = max(0, int(hit["line"]) - 1)
            for i in range(start, len(lines)):
                if lines[i].strip() != target:
                    continue
                if META_FLAG in lines[i]:
                    break
                indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
                lines[i] = indent + META_FLAG + " " + lines[i].lstrip()
                hit["flagged"] = True
                break
        if any(h["flagged"] for h in here):
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write("".join(lines))
            written.append(path)
    for hit in hits:
        hit.pop("first_line", None)
    return {"findings": hits, "written": written,
            "counts": {"findings": len(hits), "written": len(written)}}


def _av(rule: str, pattern: int, path: str, stem: str, n: int, line: int,
        detail: str, text: str, tier: str = "asserted") -> dict:
    """One ai_voice finding. Every field here is consumed downstream.

    `tier` is the half of 10.3 that keeps a word list honest: `asserted` for a
    word with no scientific use, `contextual` for one that has one and is
    reported WITH its sentence so module 1e can tell `robust to noise` from
    `robust methodology`. A contextual finding is not a defect claim.
    """
    return {"file": path, "location": f"{stem} ¶{n}", "line": line,
            "rule": rule, "pattern": pattern, "detail": detail,
            "text": text[:220], "tier": tier}


# A leading coordinator belongs to the clause, not to the list item: the
# comma before `and XPS, TEM and XRD` is a clause comma, and a regex reading
# it as the start of item 1 produces `['and XPS', 'TEM', 'XRD confirmed the
# assignment']`. Caught by the test rather than by reading the regex.
_LIST_LEAD_RE = re.compile(r"^(?:and|or|but|then|so|yet|nor)\s+", re.I)


def _three_item_lists(sentence: str) -> list[list[str]]:
    """Three-item lists, with their items (#10).

    Three-ness is NOT the finding - a chemistry paper legitimately writes
    `XPS, TEM and XRD`. The items come back so module 1e can judge whether the
    third earns its place or is a near-synonym of one of the other two, which
    is the actual pattern (10.5).

    THE MIDDLE ITEM IS THE RULER, and the outer two are trimmed to its width.
    That is the pattern's own definition doing the work rather than a
    heuristic bolted on: a rule-of-three list is PARALLEL by construction, so
    an item running longer than its siblings is the regex having eaten the
    sentence around the list. Item 2 is the only one delimited on both sides
    by punctuation, which makes it the one the regex cannot over-capture.

    Item 1 keeps its LAST `width` words and item 3 its FIRST, because each
    over-captures away from the list. Measured against the three shapes that
    broke the first two attempts:

        XPS, TEM and XRD confirmed the assignment
            -> ['XPS', 'TEM', 'XRD']          not '...XRD confirmed the...'
        The method is fast, scalable, and reproducible
            -> ['fast', 'scalable', 'reproducible']   not 'The method is fast'
        Arrays were imaged, scored and ranked by two observers
            -> ['imaged', 'scored', 'ranked']

    A boundary lookahead was the obvious fix and is wrong: it drops every
    list that is a sentence's subject, which is most of them.
    """
    out = []
    for m in re.finditer(
            r"\b([A-Za-z][\w-]*(?:\s+[\w-]+){0,3}),\s+"
            r"([A-Za-z][\w-]*(?:\s+[\w-]+){0,3}),?\s+and\s+"
            r"([A-Za-z][\w-]*(?:\s+[\w-]+){0,3})\b", sentence):
        two = m.group(2).strip()
        width = len(two.split())
        one = " ".join(
            _LIST_LEAD_RE.sub("", m.group(1).strip()).split()[-width:])
        three = " ".join(m.group(3).split()[:width])
        if one and two and three:
            out.append([one, two, three])
    return out


def ai_voice(paths: list[str]) -> dict:
    """The 15 countable AI-voice patterns (prose 10.3).

    Report only, exit 0, no threshold anywhere in it. Emits two different
    things and the difference is the point (10.10):

      findings   a rule, a location and the matched sentence. Module 1e
                 adjudicates each one and `revise-prose --from-quality` may
                 act on the ones 1e verdicts `apply`.
      densities  #10, #13, #14, #16 per section. These name no sentence, so
                 the only way to act on one is to hunt the construct and
                 eliminate it. They go to the user and to 1e, and are NEVER
                 passed to the rewrite pass.

    Reuses HEDGE_WORDS (#22) and TRANSITION_WORDS (#16) rather than declaring
    a second copy, and leaves #25's structural half to `readability`, which
    already measures it as tail_rank_last, given_new_gap and topic_return.
    """
    findings: list[dict] = []
    densities: list[dict] = []

    for path in paths:
        stem = os.path.splitext(os.path.basename(path))[0]
        text = _read(path)
        if not text.strip():
            continue
        paras = paragraphs(text)
        if not paras:
            continue

        sec_words = sum(len(_words(p["text"])) for p in paras)
        em = 0
        bold = 0
        openers: list[dict] = []
        triples: list[dict] = []
        # One term per entity, checked over the WHOLE section: synonym cycling
        # is invisible inside a single paragraph, which is why the audit file
        # calls it cycling.
        seen_synonyms: dict[int, dict[str, list[str]]] = {}

        for p in paras:
            body = p["text"]
            n, line = p["n"], p["line"]
            # Masked for the checks that must not read a citation, a
            # cross-reference or a quantity as prose; raw for the ones whose
            # subject is the markup itself (#14, #15).
            masked = mask_excluded(body)
            low = masked.lower()

            # --- #13, #14, #16: densities, counted not judged -------------
            em += body.count(EM_DASH)
            bold += len(re.findall(r"\*\*[^*\n]+\*\*", body))
            first = _sentences(body)[0] if _sentences(body) else ""
            head = re.sub(r"^[^A-Za-z]*", "", first).lower()
            for w in PARAGRAPH_OPENER_FILLER:
                if head.startswith(w + " ") or head.startswith(w + ","):
                    openers.append({"paragraph": n, "line": line, "word": w,
                                    "text": first[:160]})
                    break

            for sentence in _sentences(body):
                s_low = mask_excluded(sentence).lower()
                for items in _three_item_lists(mask_excluded(sentence)):
                    triples.append({"paragraph": n, "line": line,
                                    "items": items})

                # --- #21 filler phrases ------------------------------------
                for pat, better in FILLER_PHRASES:
                    for m in re.finditer(pat, s_low):
                        findings.append(_av(
                            "filler_phrase", 21, path, stem, n, line,
                            f"{m.group(0)!r} -> {better}", sentence))

                # --- #8 copula avoidance -----------------------------------
                for phrase in COPULA_AVOIDANCE:
                    if phrase in s_low:
                        findings.append(_av(
                            "copula_avoidance", 8, path, stem, n, line,
                            f"{phrase!r} where 'is' may do - keep it where "
                            f"the serving relationship is scientifically "
                            f"meaningful", sentence, "contextual"))

                # --- #7 AI vocabulary, two tiers ---------------------------
                for w in AI_VOCAB_UNCONDITIONAL:
                    if re.search(r"\b" + re.escape(w) + r"\b", s_low):
                        findings.append(_av(
                            "ai_vocabulary", 7, path, stem, n, line,
                            f"{w!r} has no scientific use", sentence))
                for w in AI_VOCAB_CONTEXTUAL:
                    if re.search(r"\b" + re.escape(w) + r"\b", s_low):
                        findings.append(_av(
                            "ai_vocabulary_contextual", 7, path, stem, n, line,
                            f"{w!r} - ordinary in science and a tell when "
                            f"vague; read the sentence", sentence,
                            "contextual"))

                # --- #17 hyphenated compounds, two tiers -------------------
                for w in HYPHEN_COMPOUND_UNCONDITIONAL:
                    if w in s_low:
                        findings.append(_av(
                            "hyphen_compound", 17, path, stem, n, line,
                            f"{w!r} is a slogan wherever it appears",
                            sentence))
                for w in HYPHEN_COMPOUND_CONTEXTUAL:
                    if w in s_low:
                        findings.append(_av(
                            "hyphen_compound_contextual", 17, path, stem, n,
                            line, f"{w!r} - precise in some methods and "
                            f"loose elsewhere; read the sentence", sentence,
                            "contextual"))

                # --- #9 negative parallelism -------------------------------
                m = NEGATIVE_PARALLELISM_RE.search(sentence)
                if m:
                    findings.append(_av(
                        "negative_parallelism", 9, path, stem, n, line,
                        "states what it is not before what it is; lead with "
                        "the positive", sentence))

                # --- #12 false ranges --------------------------------------
                m = FALSE_RANGE_RE.search(sentence)
                if m:
                    a, b = m.group(1).strip(), m.group(2).strip()
                    # A real range shares a dimension. Numbers on both sides
                    # are a measurement, not a rhetorical sweep.
                    if not (NUM_TOKEN_RE.search(a) and NUM_TOKEN_RE.search(b)):
                        findings.append(_av(
                            "false_range", 12, path, stem, n, line,
                            f"'from {a} to {b}' joins two things that share "
                            f"no dimension", sentence))

                # --- #23 generic conclusions -------------------------------
                for pat in GENERIC_CONCLUSIONS:
                    m = re.search(pat, s_low)
                    if m:
                        findings.append(_av(
                            "generic_conclusion", 23, path, stem, n, line,
                            f"{m.group(0)!r} would fit any paper in any "
                            f"field; name the question and the design",
                            sentence))
                        break

                # --- #22 hedge stacks --------------------------------------
                # Reuses HEDGE_WORDS. The floor in 0.2 forbids dropping a
                # hedge that carries real uncertainty, so a stack is reported
                # and 1e decides - 10.11 names this as a `leave` case.
                stacked = [w for w in HEDGE_WORDS
                           if re.search(r"\b" + re.escape(w) + r"\b", s_low)]
                if len(stacked) >= 3:
                    findings.append(_av(
                        "hedge_stack", 22, path, stem, n, line,
                        f"{len(stacked)} hedges in one sentence: "
                        f"{', '.join(stacked)}", sentence, "contextual"))

            # --- #15 inline-header lists ------------------------------------
            for m in INLINE_HEADER_RE.finditer(body):
                findings.append(_av(
                    "inline_header_list", 15, path, stem, n, line,
                    "a bold label opening a line, with its description after "
                    "it - flowing prose instead", m.group(0).strip()))

            # --- #11 synonym cycling, gathered per section ------------------
            for i, group in enumerate(SYNONYM_SETS):
                for w in group:
                    if re.search(r"\b" + re.escape(w) + r"\b", low):
                        seen_synonyms.setdefault(i, {}).setdefault(
                            w, []).append(f"¶{n}")

        for i, used in seen_synonyms.items():
            if len(used) < 2:
                continue
            where = "; ".join(f"{w} ({', '.join(sorted(set(v)))})"
                              for w, v in sorted(used.items()))
            findings.append(_av(
                "synonym_cycling", 11, path, stem, 0, 0,
                f"one entity under {len(used)} names - {where}", where))

        # --- #25 structural predictability ---------------------------------
        # Only the half `readability` does not already see: whether every
        # paragraph runs to the same LENGTH in sentences. tail_rank_last,
        # given_new_gap and topic_return carry the rest, and a second copy of
        # any of them would be a defect rather than a feature.
        lens = [len(_sentences(p["text"])) for p in paras]
        if len(lens) >= 4 and len(set(lens)) == 1:
            findings.append(_av(
                "structural_predictability", 25, path, stem, 0, 0,
                f"all {len(lens)} paragraphs run exactly {lens[0]} sentences; "
                f"vary by logical need", stem, "contextual"))

        per_k = (lambda c: round(c * 1000 / sec_words, 1)) if sec_words else \
            (lambda c: 0.0)
        densities.append({
            "file": path, "section": stem, "words": sec_words,
            "em_dashes": em, "em_dashes_per_1000": per_k(em),
            "en_dashes_not_counted": text.count(EN_DASH),
            "boldface": bold, "boldface_per_1000": per_k(bold),
            "paragraph_opener_filler": len(openers),
            "openers": openers,
            "three_item_lists": len(triples), "triples": triples,
        })

    by_rule: dict[str, int] = {}
    for f in findings:
        by_rule[f["rule"]] = by_rule.get(f["rule"], 0) + 1
    return {
        "findings": findings,
        "densities": densities,
        "by_rule": dict(sorted(by_rule.items())),
        "density_rules": list(AI_VOICE_DENSITY_RULES),
        "counts": {"findings": len(findings),
                   "asserted": sum(1 for f in findings
                                   if f["tier"] == "asserted"),
                   "contextual": sum(1 for f in findings
                                     if f["tier"] == "contextual"),
                   "sections": len(densities)},
    }


def print_ai_voice(res: dict) -> None:
    for d in res["densities"]:
        print(f"  {d['section']:16} {d['words']:5} words   "
              f"em dashes {d['em_dashes']:3} ({d['em_dashes_per_1000']}/1000)"
              f"   bold {d['boldface']:3}"
              f"   filler openers {d['paragraph_opener_filler']:2}"
              f"   three-item lists {d['three_item_lists']:2}")
        if d["en_dashes_not_counted"]:
            print(f"    {d['en_dashes_not_counted']} en dashes present and "
                  f"NOT counted - they are number ranges, not style")
    if res["findings"]:
        print()
    for f in res["findings"]:
        mark = " " if f["tier"] == "asserted" else "?"
        print(f"  {mark} {f['location']:16} #{f['pattern']:<3} "
              f"{f['rule']:28} {f['detail']}")
    c = res["counts"]
    print(f"\n  {c['findings']} findings over {c['sections']} sections "
          f"({c['asserted']} asserted, {c['contextual']} contextual - a "
          f"contextual one is a sentence to read, not a defect)")
    print("  No threshold, no budget, no pass/fail. Densities are for you, "
          "not for the rewrite pass.")


# ---------------------------------------------------------------------------
# Dialect - specs/user-asks-2026-09-18.md 3
# ---------------------------------------------------------------------------
#
# The one check in this file that WRITES without being asked, and the reasons
# it is allowed to are all of the same kind: the finding names an exact word at
# an exact offset, the correction is a lookup rather than a judgment, there is
# no threshold to calibrate, and every false positive can be listed by name
# rather than estimated. Nothing else in prose 10 has any of those properties,
# which is why nothing else here writes.
#
# The map is a WORD LIST and not a set of suffix rules, and that is the load-
# bearing decision. Written as rules, `-ise -> -ize` fires on surprise,
# comprise, exercise, revise, precise, devise, promise and two dozen more, and
# `-yse -> -yze` on `analyses`, which is the plural of `analysis` in both
# dialects - so the "fix" would turn a noun into a verb. A list cannot make
# that mistake: a word is in it or it is not. Inflections are GENERATED from
# stems below, which is a different thing - the generator only ever produces
# members of a family already decided to be British.

def _ise(stem: str) -> dict:
    """The -ise/-ize family of one verb stem, both dialects.

    `stem` is the part before `ise`: `characteris` -> characterise,
    characterises, characterised, characterising, characteriser(s),
    characterisation(s). The noun in -isation is included because it is the
    form that actually appears in a methods section.
    """
    out = {}
    for be, ae in (("ise", "ize"), ("ises", "izes"), ("ised", "ized"),
                   ("ising", "izing"), ("iser", "izer"), ("isers", "izers"),
                   ("isation", "ization"), ("isations", "izations"),
                   ("isable", "izable"), ("isability", "izability")):
        out[stem + be] = stem + ae
    return out


def _yse(stem: str) -> dict:
    """The -yse/-yze family, MINUS the third-person singular.

    `analyses`, `catalyses`, `hydrolyses`, `electrolyses` and `dialyses` are
    each the plural of a noun in `-ysis` as well as a British verb form, and
    the two are spelled identically. Converting one converts the other, and
    "the analyses agree" would become "the analyzes agree". The verb form is
    therefore left alone in every member of this family - a miss, deliberately
    chosen over a change that alters what a sentence means.
    """
    out = {}
    for be, ae in (("yse", "yze"), ("ysed", "yzed"), ("ysing", "yzing"),
                   ("yser", "yzer"), ("ysers", "yzers")):
        out[stem + be] = stem + ae
    return out


def _our(stem: str) -> dict:
    """The -our/-or family: colour, colours, coloured, colouring, colourless."""
    out = {}
    for suffix in ("", "s", "ed", "ing", "less", "ful"):
        out[stem + "our" + suffix] = stem + "or" + suffix
    return out


def _double_l(stem: str) -> dict:
    """British doubles the l before a suffix: labelled, labelling, labeller."""
    out = {}
    for be, ae in (("lled", "led"), ("lling", "ling"), ("ller", "ler"),
                   ("llers", "lers")):
        out[stem + be] = stem + ae
    return out


def _dialect_map() -> dict[str, str]:
    m: dict[str, str] = {}

    # -our -> -or
    for stem in ("col", "behavi", "fav", "flav", "harb", "hon", "lab",
                 "neighb", "od", "rig", "vap", "vig", "tum", "hum", "arm",
                 "endeav", "clam", "rum", "cand", "ferv", "sav", "splend",
                 "val", "parl", "demean"):
        m.update(_our(stem))
    m.update({
        "colourless": "colorless", "colouration": "coloration",
        "discolour": "discolor", "discoloured": "discolored",
        "discolouration": "discoloration",
        "behavioural": "behavioral", "behaviourally": "behaviorally",
        "favourable": "favorable", "favourably": "favorably",
        "unfavourable": "unfavorable", "unfavourably": "unfavorably",
        "favourite": "favorite", "favourites": "favorites",
        "neighbouring": "neighboring", "neighbourhood": "neighborhood",
        "odourless": "odorless", "vapourise": "vaporize",
        "vapourised": "vaporized", "vapourising": "vaporizing",
        "humoural": "humoral",
    })

    # -re -> -er. `metre` and `litre` carry their SI prefixes, because
    # `nanometre` is the form that actually appears in this user's work.
    m.update({"centre": "center", "centres": "centers",
              "centred": "centered", "centring": "centering",
              "centreline": "centerline",
              "fibre": "fiber", "fibres": "fibers",
              "theatre": "theater", "theatres": "theaters",
              "calibre": "caliber", "sombre": "somber", "lustre": "luster",
              "sceptre": "scepter", "spectre": "specter",
              "titre": "titer", "titres": "titers", "goitre": "goiter",
              "mitre": "miter", "ochre": "ocher", "saltpetre": "saltpeter",
              "manoeuvre": "maneuver", "manoeuvres": "maneuvers",
              "manoeuvred": "maneuvered", "manoeuvring": "maneuvering"})
    for prefix in ("", "milli", "micro", "centi", "deci", "kilo", "nano",
                   "pico", "femto", "atto", "hecto", "deca"):
        m[prefix + "metre"] = prefix + "meter"
        m[prefix + "metres"] = prefix + "meters"
        m[prefix + "litre"] = prefix + "liter"
        m[prefix + "litres"] = prefix + "liters"

    # -ise -> -ize. The stem is the word MINUS `ise`, so every entry here was
    # checked by reading it back with the suffix on: `character` + `ise` is a
    # word, `quantif` + `ise` is not and is absent.
    for stem in ("character", "optim", "minim", "maxim", "normal", "stabil",
                 "standard", "steril", "summar", "synthes", "util", "visual",
                 "polar", "ion", "oxid", "organ", "recogn", "real", "emphas",
                 "hypothes", "general", "local", "mobil", "modern", "neutral",
                 "pressur", "pulver", "random", "rational", "sensit",
                 "solubil", "special", "immobil", "homogen", "hybrid",
                 "crystall", "volatil", "atom", "deion", "deodor", "digit",
                 "equal", "final", "formal", "fossil", "initial", "legal",
                 "linear", "liquid", "metabol", "militar", "monet",
                 "pasteur", "penal", "plural", "priorit", "public",
                 "revolution", "scrutin", "symbol", "sympath", "system",
                 "theor", "victim", "vocal", "author", "categor", "central",
                 "colon", "commercial", "computer", "critic", "custom",
                 "decentral", "depolar", "derivat", "destabil", "familiar",
                 "harmon", "hospital", "human", "ideal", "individual",
                 "industrial", "internal", "item", "magnet", "material",
                 "memor", "national", "natural", "particular", "personal",
                 "popular", "professional", "regular", "revital", "social",
                 "subsid", "synchron", "tantal", "trivial", "verbal",
                 "vulgar", "weapon"):
        m.update(_ise(stem))
    # `vapourise` is the one member of the family whose stem also changes.
    m.update({"vapourise": "vaporize", "vapourises": "vaporizes",
              "vapourised": "vaporized", "vapourising": "vaporizing",
              "vapourisation": "vaporization"})

    # -yse -> -yze, third person deliberately absent (see _yse).
    for stem in ("anal", "catal", "hydrol", "paral", "electrol", "dial"):
        m.update(_yse(stem))

    # British doubles a final l before a suffix; American does not.
    for stem in ("labe", "mode", "trave", "cance", "channe", "fue", "signa",
                 "tota", "leve", "marve", "counse", "equa", "funne", "penci",
                 "quarre", "shrive", "tunne", "unrave", "dia",
                 "refue", "remode", "swive"):
        m.update(_double_l(stem))
    # ...and doubles it in the OTHER direction on these, which is the half a
    # one-way rule gets wrong.
    m.update({"fulfil": "fulfill", "fulfils": "fulfills",
              "fulfilment": "fulfillment", "fulfilments": "fulfillments",
              "enrol": "enroll", "enrols": "enrolls",
              "enrolment": "enrollment", "enrolments": "enrollments",
              "instalment": "installment", "instalments": "installments",
              "instil": "instill", "instils": "instills",
              "distil": "distill", "distils": "distills",
              "skilful": "skillful", "skilfully": "skillfully",
              "wilful": "willful", "wilfully": "willfully",
              "appal": "appall", "appals": "appalls"})

    # The pairs that are not a rule at all. Every one of these is a word, not
    # a pattern, and the chemistry half is the half that matters here.
    m.update({
        "aluminium": "aluminum",
        "caesium": "cesium", "caesiums": "cesiums",
        "sulphur": "sulfur", "sulphurs": "sulfurs",
        "sulphuric": "sulfuric", "sulphurous": "sulfurous",
        "sulphate": "sulfate", "sulphates": "sulfates",
        "sulphide": "sulfide", "sulphides": "sulfides",
        "sulphite": "sulfite", "sulphites": "sulfites",
        "sulphonate": "sulfonate", "sulphonates": "sulfonates",
        "sulphonic": "sulfonic", "sulphoxide": "sulfoxide",
        "sulphydryl": "sulfhydryl", "disulphide": "disulfide",
        "disulphides": "disulfides", "thiosulphate": "thiosulfate",
        "polysulphide": "polysulfide", "sulphonamide": "sulfonamide",
        "grey": "gray", "greys": "grays", "greyed": "grayed",
        "greyish": "grayish", "greyscale": "grayscale",
        "mould": "mold", "moulds": "molds", "moulded": "molded",
        "moulding": "molding", "smoulder": "smolder",
        "smouldering": "smoldering",
        "defence": "defense", "defences": "defenses",
        "offence": "offense", "offences": "offenses",
        "pretence": "pretense", "licence": "license", "licences": "licenses",
        "practise": "practice", "practised": "practiced",
        "practising": "practicing",
        "programme": "program", "programmes": "programs",
        "catalogue": "catalog", "catalogues": "catalogs",
        "catalogued": "cataloged", "cataloguing": "cataloging",
        "analogue": "analog", "analogues": "analogs",
        "homologue": "homolog", "homologues": "homologs",
        "ageing": "aging", "judgement": "judgment",
        "judgements": "judgments",
        "acknowledgement": "acknowledgment",
        "acknowledgements": "acknowledgments",
        "draught": "draft", "draughts": "drafts",
        "plough": "plow", "ploughed": "plowed",
        "kerb": "curb", "cheque": "check", "cheques": "checks",
        "tyre": "tire", "tyres": "tires", "storey": "story",
        "storeys": "stories",
        "focussed": "focused", "focusses": "focuses",
        "focussing": "focusing",
        "targetted": "targeted", "targetting": "targeting",
        "benefitted": "benefited", "benefitting": "benefiting",
        "whilst": "while",
        # Medical and biological, which a biochemistry paper reaches for.
        "haemoglobin": "hemoglobin", "haematology": "hematology",
        "haematological": "hematological", "haemolysis": "hemolysis",
        "haemolytic": "hemolytic", "haemorrhage": "hemorrhage",
        "haemorrhagic": "hemorrhagic", "haematoxylin": "hematoxylin",
        "haematopoietic": "hematopoietic", "haemostasis": "hemostasis",
        "haem": "heme", "anaemia": "anemia", "anaemic": "anemic",
        "ischaemia": "ischemia", "ischaemic": "ischemic",
        "leukaemia": "leukemia", "oedema": "edema", "oedematous": "edematous",
        "oesophagus": "esophagus", "oesophageal": "esophageal",
        "oestrogen": "estrogen", "oestrogens": "estrogens",
        "oestrous": "estrous", "oestrus": "estrus",
        "foetal": "fetal", "foetus": "fetus", "foetuses": "fetuses",
        "paediatric": "pediatric", "paediatrics": "pediatrics",
        "gynaecology": "gynecology", "orthopaedic": "orthopedic",
        "diarrhoea": "diarrhea", "coeliac": "celiac",
        "aetiology": "etiology", "aetiological": "etiological",
        "paedophile": "pedophile", "amoeboid": "ameboid",
    })
    return m


BRITISH_TO_AMERICAN = _dialect_map()

DIALECTS = ("us", "uk")

# A bare URL is not prose and its host is not a spelling. `mask_excluded`
# blanks a markdown link but not a bare one, so this runs beside it.
_URL_RE = re.compile(r"https?://\S+|www\.\S+|\b\S+@\S+\.\w+")

# Quoted material - the other author's spelling, and not ours to correct.
# Straight and curly pairs, plus a markdown blockquote line.
_QUOTE_RES = [
    re.compile(r'"[^"\n]{0,400}"'),
    re.compile(r"“[^”\n]{0,400}”"),
    re.compile(r"‘[^’\n]{0,400}’"),
    re.compile(r"^>.*$", re.M),
]

_WORD_RE = re.compile(r"[A-Za-z]+")


def _apply_case(british: str, american: str) -> str:
    """The user's capitalisation, not the map's.

    `Colour` at the start of a sentence is still at the start of a sentence
    after the fix, and `COLOUR` on a dial label is still a label.
    """
    if british.isupper():
        return american.upper()
    if british[:1].isupper():
        return american[:1].upper() + american[1:]
    return american


def _quote_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    for rx in _QUOTE_RES:
        spans.extend((m.start(), m.end()) for m in rx.finditer(text))
    return spans


def _proper_noun_here(text: str, start: int, end: int) -> bool:
    """Is this capitalised word part of a capitalised multi-word name?

    `Medical Research Centre` is an institution and keeps its own spelling;
    `Colour was recorded` is a sentence that begins with a word. The only
    thing that separates them is the neighbours, so the neighbours are what
    is read: a capitalised word touching another capitalised word is a name.
    `The` and `A` are excluded as neighbours, or every sentence-initial word
    would qualify through the article in front of it.
    """
    if not text[start:end][:1].isupper():
        return False
    # Only the same sentence on the same line counts as a neighbour. Without
    # the split, a heading two lines up is the "previous word" - `# Methods`
    # then `Colour was recorded` reads as the name *Methods Colour* and the
    # first word of every paragraph after a heading is protected from the
    # fix. Measured: that is exactly what happened on the first run.
    before = re.split(r"[\n.!?;:]", text[max(0, start - 60):start])[-1]
    after = re.split(r"[\n.!?;:]", text[end:end + 60])[0]
    prev = re.search(r"([A-Za-z][\w'-]*)\W+$", before)
    nxt = re.match(r"\W+([A-Za-z][\w'-]*)", after)
    stop = {"the", "a", "an", "of", "for", "and", "in", "on", "at", "to"}
    for m, word in ((prev, prev.group(1) if prev else ""),
                    (nxt, nxt.group(1) if nxt else "")):
        if not m or not word:
            continue
        if word[:1].isupper() and word.lower() not in stop:
            return True
    return False


def spelling(paths: list[str], fix: bool = False,
             dialect: str = "us") -> dict:
    """British spellings in the manuscript, corrected in place.

    Reports like every other command here; writes when asked, which no other
    measurement in this file does. See the section comment above for why this
    one is allowed to, and specs/user-asks-2026-09-18.md 3.2 for the rule it
    is an instance of: a finding that names a word is fixed, a finding that
    names a number is handed to the user.

    `dialect` is `us` - the only direction the map runs. `uk` is accepted so
    the flag has a spelling, and refuses rather than pretending to convert
    the other way: an American-to-British map is a different word list and
    nobody has asked for one.
    """
    out: dict = {"findings": [], "written": [], "refused": [],
                 "dialect": dialect,
                 "counts": {"findings": 0, "fixed": 0, "files": 0}}
    if dialect != "us":
        out["refused"].append(
            {"path": "", "why": "only `us` is built; there is no "
                                "American-to-British map and inventing one "
                                "silently would be worse than refusing"})
        return out

    for path in paths:
        # A .bib entry's title is quoted material with a DOI attached. The
        # refusal is at the door rather than at the word, because a published
        # title that happens to contain no British spelling today would still
        # have been at risk tomorrow.
        if os.path.splitext(path)[1].lower() in (".bib", ".ris", ".json",
                                                 ".yml", ".yaml", ".csl"):
            out["refused"].append(
                {"path": path,
                 "why": "a reference file - a published title is quoted "
                        "material and keeps the spelling it was printed with"})
            continue

        text = _read(path)
        if not text.strip():
            continue
        out["counts"]["files"] += 1
        stem = os.path.splitext(os.path.basename(path))[0]
        masked = mask_excluded(text)
        masked = _URL_RE.sub(lambda m: " " * len(m.group(0)), masked)
        quotes = _quote_spans(text)

        here: list[tuple[int, int, str, str]] = []
        for m in _WORD_RE.finditer(text):
            word = m.group(0)
            american = BRITISH_TO_AMERICAN.get(word.lower())
            if not american:
                continue
            if masked[m.start():m.end()].strip() == "":
                continue
            in_quote = any(s <= m.start() < e for s, e in quotes)
            proper = _proper_noun_here(text, m.start(), m.end())
            line = text.count("\n", 0, m.start()) + 1
            body = text[max(0, m.start() - 60):m.end() + 60]
            finding = {
                "file": path, "location": f"{stem}:{line}", "line": line,
                "rule": "british_spelling", "severity": "info",
                "british": word.lower(), "american": american,
                "detail": f"{word!r} is British; American is "
                          f"{_apply_case(word, american)!r}",
                "text": " ".join(body.split()),
                "in_quote": in_quote, "proper_noun": proper,
                "fixed": False,
            }
            if in_quote or proper:
                # Recorded, not reported: the user does not need a list of
                # words the pass correctly declined to touch, and a finding
                # that names no action is noise. Kept on the object so a test
                # can prove the exclusion fired rather than that the word was
                # never seen.
                continue
            out["findings"].append(finding)
            here.append((m.start(), m.end(), word, american))

        if not (fix and here):
            continue
        # Right to left, so every offset still points where it pointed when
        # it was measured. Same reason _blank() pads instead of deleting.
        new = text
        for start, end, word, american in reversed(here):
            new = new[:start] + _apply_case(word, american) + new[end:]
        if new != text:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(new)
            out["written"].append(path)
            for f in out["findings"]:
                if f["file"] == path:
                    f["fixed"] = True

    out["counts"]["findings"] = len(out["findings"])
    out["counts"]["fixed"] = sum(1 for f in out["findings"] if f["fixed"])
    return out


def print_spelling(res: dict) -> None:
    c = res["counts"]
    for r in res["refused"]:
        print(f"  refused  {r['path'] or '(dialect)'}: {r['why']}")
    if not res["findings"]:
        print(f"American spelling: nothing to change in {c['files']} file(s).")
        return
    print(f"American spelling - {c['findings']} word(s) in {c['files']} "
          f"file(s)\n")
    width = max(len(f["location"]) for f in res["findings"])
    for f in res["findings"]:
        mark = "fixed" if f["fixed"] else "found"
        print(f"  {mark}  {f['location']:{width}}  {f['british']} -> "
              f"{f['american']}")
    if res["written"]:
        print(f"\n{c['fixed']} corrected in {len(res['written'])} file(s).")
    else:
        print("\nNothing was written. Re-run with --fix to correct them.")


# ---------------------------------------------------------------------------
# Italics - specs/user-asks-2026-09-18-second.md 1
# ---------------------------------------------------------------------------
#
# The second check in this file that writes, and it earns it the same way
# `spelling` does: the finding names an exact substring, the replacement is a
# lookup, there is no threshold, and every false positive can be listed by
# name. What it must not do is decide a question that has two right answers -
# so the term list is split three ways and only one third of it is ever
# written.
#
# The dial is not politeness. ACS sets `in vivo` roman as naturalised English;
# the user wants it italic. Both are correct in different houses, and a check
# that hard-codes one of them is wrong half the time on purpose. The house
# default is the user's stated preference and `requirements.yml` supersedes it,
# and the report says which rule it applied.

# Italic in every house this toolkit will meet. These are the --fix set.
ITALIC_ALWAYS = ("in vivo", "in vitro", "in situ", "ex vivo", "in silico",
                 "de novo", "in utero", "in ovo", "ad libitum", "a priori",
                 "post hoc", "sensu stricto")

# Real disagreement between houses, so the default is roman and these fire on
# NOTHING until a journal file turns one on - and even then they are reported
# rather than written. `via` is here and defaults off because it is the one
# term measured in the real draft this came from, and italicising it would be
# this check's first false positive.
ITALIC_JOURNAL = ("et al.", "i.e.", "e.g.", "cf.", "vs.", "per se", "via")

# A stop list, so ITALIC_ALWAYS can never grow into it. Every one of these is
# a Latin-derived word that is ordinary English now, and every one of them is
# the kind of word somebody adds to the always list in good faith.
ITALIC_NEVER = frozenset({"data", "media", "agenda", "bacteria", "criteria",
                          "status", "versus"})

# Is this capitalised token being used as a GENE rather than as its protein
# product? Human gene symbols are italic and their products are roman, and in
# a biochemistry paper `CMG2` (protein) and `ANTXR2` (gene) can be the
# same molecule under two conventions. Read within the sentence only - see
# _gene_context for why the window cannot cross a line.
GENE_CONTEXT_WORDS = (
    "gene", "genes", "genetic", "genetically", "mrna", "cdna", "transcript",
    "transcripts", "transcription", "locus", "loci", "allele", "alleles",
    "allelic", "biallelic", "homozygous", "heterozygous", "mutation",
    "mutations", "variant", "variants", "exon", "exons", "intron", "introns",
    "promoter", "knockout", "knockdown", "knocked", "polymorphism",
    "polymorphisms", "genotype", "genotypes", "haplotype", "sequencing",
    "deletion", "frameshift", "missense", "nonsense",
)

_GENE_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}\b")


def _blank_lines_kept(match: re.Match) -> str:
    """Blank a span to spaces, keeping its newlines.

    `_blank` flattens a multi-line span into one run of spaces, which is fine
    for the single-line patterns it was written for and wrong here: a fenced
    block or an HTML comment spans lines, and every finding this command emits
    carries a line number counted by `\\n`.
    """
    return re.sub(r"[^\n]", " ", match.group(0))


# Order matters, and it is the whole check. The regions that may CONTAIN any
# of the others go first: a citekey inside a fenced block must not be
# re-matched once the block is already blank, and an emphasis span inside a
# link must not reopen the link's parentheses.
_ITALIC_MASKS = (
    re.compile(r"<!--.*?-->", re.DOTALL),                    # HTML comment
    re.compile(r"^```.*?(?:^```|\Z)", re.M | re.S),          # fenced block
    re.compile(r"^~~~.*?(?:^~~~|\Z)", re.M | re.S),          # the other fence
    re.compile(r"\A---\n.*?^(?:---|\.\.\.)[ \t]*$",
               re.M | re.S),                                 # YAML front matter
    re.compile(r"`[^`\n]*`"),                                # inline code
    re.compile(r"\*\*\[FLAG:.*?\]\*\*", re.DOTALL),          # an engine flag
    re.compile(r"!?\[[^\]\[]*\]\([^)]*\)"),                  # link or image
    re.compile(r"\[[^\]\[]*@[^\]\[]*\]"),                    # [@key], [@a; @b]
    re.compile(r"\[\d+(?:\s*[,–-]\s*\d+)*\]"),               # a rendered [7]
    re.compile(r"https?://\S+|www\.\S+|\b\S+@\S+\.\w+"),     # a bare URL
    re.compile(r"^[ \t]{0,3}#{1,6}[ \t].*$", re.M),          # a styled heading
    re.compile(r"\*\*[^\n]+?\*\*"),                          # **bold**
    re.compile(r"(?<!\*)\*[^*\n]+?\*(?!\*)"),                # *italic*
    re.compile(r"(?<![\w*_])_[^_\n]+?_(?![\w_])"),           # _italic_
)


def italic_excluded(text: str) -> str:
    """Everything that is not prose, blanked to spaces of the same length.

    Run BEFORE matching, never after. The draft this was measured on carries
    a citekey on nearly every line, so a matcher that finds first and excludes
    afterwards italicises something inside `[@scobie2003human]` on its first
    real run - which is spec 7.1's prediction and the reason this function is
    the first thing written.
    """
    for pat in _ITALIC_MASKS:
        text = pat.sub(_blank_lines_kept, text)
    return text


def _italic_term_re(term: str) -> re.Pattern:
    """Word-boundary, case-insensitive, and tolerant of the space inside.

    `et al.` ends in a period, so a trailing `\\b` would never match; the
    boundary is asserted only where the term's own edge is a word character.
    """
    body = r"\s+".join(re.escape(part) for part in term.split())
    head = r"(?<![\w-])" if term[:1].isalnum() else ""
    tail = r"(?![\w-])" if term[-1:].isalnum() else ""
    return re.compile(head + body + tail, re.IGNORECASE)


_ITALIC_RES = {t: _italic_term_re(t) for t in ITALIC_ALWAYS + ITALIC_JOURNAL}


def _gene_context(text: str, start: int, end: int,
                  window: int = 80) -> list[str]:
    """The gene words in the same sentence as this token, and no others.

    Bounded to the sentence for the reason `_proper_noun_here` is: without
    the split, `mRNA` at the end of one paragraph is the "context" of a token
    four lines later, and `the ANTXR2 ectodomain` - which is the protein -
    reads as a gene. Measured: that is exactly what an unbounded window does
    to 02_structure.md.

    And bounded again at an UNCLOSED parenthesis, which is the correction the
    first real run forced. `Capillary morphogenesis gene 2 (CMG2, also
    ANTXR2)` is the receptor's own NAME, and the word `gene` inside it is part
    of that name rather than a statement about a gene. Read whole, it fired on
    every definition of the protein. A
    parenthetical is its own scope.
    """
    before = re.split(r"[\n.!?;:]", text[max(0, start - window):start])[-1]
    after = re.split(r"[\n.!?;:]", text[end:end + window])[0]
    open_paren = before.rfind("(")
    if open_paren != -1 and ")" not in before[open_paren:]:
        before = before[open_paren + 1:]
    close_paren = after.find(")")
    if close_paren != -1 and "(" not in after[:close_paren]:
        after = after[:close_paren]
    near = (before + " " + after).lower()
    return [w for w in GENE_CONTEXT_WORDS
            if re.search(r"\b" + re.escape(w) + r"\b", near)]


def italics(paths: list[str], fix: bool = False,
            journal_terms: list[str] | None = None,
            journal: str = "", roman_terms: list[str] | None = None) -> dict:
    """Latin and foreign phrases that should be italic, and gene symbols.

    Two findings, and only one of them writes:

    `latin_not_italic` names an exact substring and an exact replacement, so
    `--fix` writes it - but only for the `always` set. A `journal` term is
    italic in one house and roman in the next, which is not a lookup, so it
    is reported with its sentence and the user applies it.

    `gene_symbol_roman` never writes. Deciding whether a given `ANTXR2` means
    the gene or the protein it encodes is judgment; the finding names the
    sentence and the drafting pass decides. It surfaces candidates rather
    than asserting a defect, and says so in `detail`.

    `roman_terms` is the journal superseding the house on the OTHER side, and
    it is why the always set is not a hard-coded answer. ACS sets `in vivo`
    roman as naturalised English and the user wants it italic; both are right
    in different houses, so the house default is the user's preference and a
    journal that says otherwise wins without argument.
    """
    on = {t.lower().strip() for t in (journal_terms or [])}
    off = {t.lower().strip() for t in (roman_terms or [])}
    unknown = sorted(t for t in on
                     if t not in {j.lower() for j in ITALIC_JOURNAL})
    out: dict = {
        "findings": [], "written": [], "refused": [], "journal": journal,
        "source": ("journal requirements.yml" if (on or off)
                   else "house default"),
        "terms": {"always": [t for t in ITALIC_ALWAYS if t.lower() not in off],
                  "roman_here": sorted(off),
                  "journal_on": sorted(on - set(unknown)),
                  "journal_off": sorted(t for t in ITALIC_JOURNAL
                                        if t.lower() not in on)},
        "counts": {"findings": 0, "fixed": 0, "files": 0},
    }
    for term in unknown:
        out["refused"].append(
            {"path": "", "why": f"{term!r} is not in the journal-dial set; "
                                f"only {', '.join(ITALIC_JOURNAL)} may be "
                                f"turned on, and the always set is not a dial"})

    for path in paths:
        # Same refusal as `spelling`, at the door and for the same reason: a
        # published title keeps the typography it was printed with.
        if os.path.splitext(path)[1].lower() in (".bib", ".ris", ".json",
                                                 ".yml", ".yaml", ".csl"):
            out["refused"].append(
                {"path": path,
                 "why": "a reference file - a published title keeps the "
                        "typography it was printed with"})
            continue

        text = _read(path)
        if not text.strip():
            continue
        out["counts"]["files"] += 1
        stem = os.path.splitext(os.path.basename(path))[0]
        masked = italic_excluded(text)

        def _at(start: int, end: int) -> dict:
            line = text.count("\n", 0, start) + 1
            body = text[max(0, start - 70):end + 70]
            return {"file": path, "location": f"{stem}:{line}", "line": line,
                    "text": " ".join(body.split())}

        here: list[tuple[int, int, str]] = []
        for term in ITALIC_ALWAYS + ITALIC_JOURNAL:
            in_always = term in ITALIC_ALWAYS
            if in_always and term.lower() in off:
                continue          # this journal sets it roman, and wins
            if not in_always and term.lower() not in on:
                continue
            for m in _ITALIC_RES[term].finditer(text):
                if masked[m.start():m.end()].strip() == "":
                    continue
                found = m.group(0)
                if found.lower() in ITALIC_NEVER:
                    continue
                writes = in_always
                f = _at(m.start(), m.end())
                f.update({
                    "rule": "latin_not_italic", "severity": "info",
                    "set": "always" if in_always else "journal",
                    "term": term, "token": found,
                    "replacement": f"*{found}*", "writes": writes,
                    "detail": f"{found!r} is a Latin phrase this house sets "
                              f"italic; write *{found}*"
                    + ("" if in_always else
                       f" - turned on for {journal or 'this journal'}, and "
                       f"applied by hand because houses disagree"),
                    "fixed": False,
                })
                out["findings"].append(f)
                if writes:
                    here.append((m.start(), m.end(), found))

        # --- gene symbols, reported and never written ------------------
        for m in _GENE_TOKEN_RE.finditer(text):
            token = m.group(0)
            if masked[m.start():m.end()].strip() == "":
                continue
            if _is_formula(token) or token.lower() in ITALIC_NEVER:
                continue
            words = _gene_context(text, m.start(), m.end())
            if not words:
                continue
            f = _at(m.start(), m.end())
            f.update({
                "rule": "gene_symbol_roman", "severity": "info",
                "set": "gene", "term": token, "token": token,
                "replacement": f"*{token}*", "writes": False,
                "context": words, "fixed": False,
                "detail": f"{token!r} sits beside {', '.join(words[:3])} - if "
                          f"this sentence means the gene it is italic, and if "
                          f"it means the protein it is roman. Nothing here "
                          f"decides that",
            })
            out["findings"].append(f)

        if not (fix and here):
            continue
        # Right to left, so every offset still points where it pointed when it
        # was measured - same reason spelling() reverses.
        new = text
        for start, end, found in sorted(here, reverse=True):
            new = new[:start] + f"*{found}*" + new[end:]
        if new != text:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(new)
            out["written"].append(path)
            for f in out["findings"]:
                if f["file"] == path and f["writes"]:
                    f["fixed"] = True

    out["counts"]["findings"] = len(out["findings"])
    out["counts"]["fixed"] = sum(1 for f in out["findings"] if f["fixed"])
    return out


def print_italics(res: dict) -> None:
    c = res["counts"]
    for r in res["refused"]:
        print(f"  refused  {r['path'] or '(terms)'}: {r['why']}")
    print(f"Italics - term list from {res['source']}"
          + (f" ({res['journal']})" if res["journal"] else ""))
    if not res["findings"]:
        print(f"  nothing to change in {c['files']} file(s).")
        return
    print(f"  {c['findings']} finding(s) in {c['files']} file(s)\n")
    width = max(len(f["location"]) for f in res["findings"])
    for f in res["findings"]:
        mark = ("fixed" if f["fixed"] else
                ("found" if f["writes"] else "yours"))
        print(f"  {mark}  {f['location']:{width}}  {f['rule']}  "
              f"{f['token']}")
    if res["written"]:
        print(f"\n{c['fixed']} italicised in {len(res['written'])} file(s).")
    else:
        left = sum(1 for f in res["findings"] if f["writes"])
        print(f"\nNothing was written."
              + (f" Re-run with --fix to italicise {left}." if left else ""))
    yours = [f for f in res["findings"] if not f["writes"]]
    if yours:
        print(f"{len(yours)} finding(s) are yours to apply - a gene symbol "
              f"and a house-dependent term are both judgment, not lookup.")


# ---------------------------------------------------------------------------
# Abbreviations - specs/user-asks-2026-09-18-second.md 3
# ---------------------------------------------------------------------------
#
# Separate from `readability` on purpose: that one counts abbreviation LOAD
# and is off by default for calibration reasons that have nothing to do with
# these findings, and this one counts abbreviation DEFINITION.
#
# The rule, in the user's words: "Usually, define the abbreviation in the
# abstract and then define it again at its first occurrence in the main text."
# And: "But the paper rules would supercede this." Both sentences are the
# rule. `abbreviations_in_abstract` in requirements.yml is the second one, and
# it wins without argument - a check that silently picks a house is how a
# paper gets formatted for the wrong journal, so the report says which rule it
# applied and why.

ABBREV_IN_ABSTRACT = ("define", "avoid", "unknown")

# Both of these are STARTING POINTS drawn from the measurement in §3.1, not
# calibrated numbers, and the payload says so rather than pretending
# otherwise (prose 10.7).
#
# `N` is field-, house- and term-dependent: a methods section may write a
# reagent out four times on purpose, and a term whose conventional
# abbreviation does not exist must never be abbreviated at all.
#
# The distance is the readability half of the user's rule expressed as
# something countable - "but only as dictated by readability". A
# re-expansion a few paragraphs after the definition is a lapse; one thousands
# of words later is a conclusion re-orienting a reader who skipped to it. 500
# sits between the two and is not a calibrated number.
NEVER_ABBREVIATED_N = 4
REEXPANSION_DISTANCE_WORDS = 500

# The boundaries of a candidate term. A phrase that starts or ends on one of
# these is a fragment of a sentence rather than the name of a thing.
_TERM_STOPWORDS = frozenset("""
a an the and or but of in on at to for with from into over under by as is are
was were be been being this that these those it its their his her our your my
we they he she not no nor so than then there here which who whom whose what
when where why how all any both each few more most other some such only own
same too very can will just also may might could would should has have had do
does did between within without across during after before while either
neither about against above below up down out off again further once per via
""".split())

# A phrase whose last word is one of these is a clause, not a term:
# `domain carries`, `receptor binds`, `mutations showed`. Measured - this is
# what the naive n-gram sweep produced on the real draft beside the three
# terms that were actually wanted.
_TERM_VERB_TAIL = frozenset("""
carries carry carried shows show showed shown binds bind bound reveals reveal
revealed indicates indicate indicated suggests suggest suggested found
reports report reported causes cause caused forms form formed gives give
given makes make made requires require required drives drive driven mediates
mediate mediated occurs occur occurred remains remain remained appears appear
appeared contains contain contained increases increase increased decreases
decrease decreased correlates correlate correlated produces produce produced
yields yield yielded leads lead led allows allow allowed prevents prevent
prevented blocks block blocked uses use used retained retain retains includes
include included becomes become became seen see saw measured measure
measures observed observe observes
""".split())

# The token regex and the parenthesis reader used to be defined here, a
# second time, with `readability` up the file using weaker versions of both.
# That divergence WAS items 100 and 101. They are now defined once, under
# ABBREVIATION CONTRACT beside `readability`, and this command uses those.

_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>\n]*>")
_HEADING_LINE_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.*\S)[ \t]*$", re.M)
_TERM_WORD_RE = re.compile(r"(?<![\w-])[A-Za-z][A-Za-z-]*[A-Za-z](?![\w-])")


def _abbrev_prose(text: str) -> str:
    """The prose an abbreviation check may read, blanked everywhere else.

    Headings go too - `italic_excluded` already blanks them - because §3.4's
    second suppression is that an expansion inside a heading, caption or
    title is a label rather than a lapse. They are read back separately, so
    the suppression can be RECORDED rather than silently applied.
    """
    text = _strip_comments(text)
    text = _HTML_TAG_RE.sub(_blank_lines_kept, text)
    return italic_excluded(text)


def _abbrev_matches(abbrev: str, words: list[str]) -> bool:
    """Do this abbreviation's letters run through these words, in order?

    Initials alone are too strict and it is not close: `vWA` is `von
    Willebrand factor A` and `ELISA` is `enzyme-linked immunosorbent assay`,
    and neither is the string of first letters. What IS true of every real
    abbreviation is that its letters appear in the expansion in order, and
    that its first letter opens the first word.
    """
    if not words or not abbrev:
        return False
    joined = "".join(words).lower()
    if not joined.startswith(abbrev[0].lower()):
        return False
    i = 0
    for ch in abbrev.lower():
        if not ch.isalnum():
            continue
        i = joined.find(ch, i)
        if i < 0:
            return False
        i += 1
    return True


def _expansion_before(text: str, at: int, abbrev: str) -> str:
    """The spelled-out term immediately before `(ABBREV)`, or "".

    Reads backwards a word at a time and takes the LONGEST run that still
    starts on the abbreviation's own first letter, so `the von Willebrand
    factor A (vWA)` yields the term and not the article in front of it.
    """
    before = text[max(0, at - 220):at]
    # Digits are words here. `Capillary morphogenesis gene 2 (CMG2)` puts the
    # `2` of the abbreviation in a token of its own, and a letters-only
    # tokenizer drops it - which on the real draft made the paper's own
    # subject read as undefined in its own abstract.
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", before)
    best = ""
    for k in range(2, min(len(words), len(abbrev) + 4) + 1):
        run = words[-k:]
        if run[0][:1].lower() != abbrev[0].lower():
            continue
        if _abbrev_matches(abbrev, run):
            best = " ".join(run)
    return best


def _term_candidates(text: str, n_max: int = 5) -> dict:
    """Every multi-word noun-ish phrase in this text, with its count.

    An n-gram sweep, bounded at both ends by a stop list and at the tail by a
    verb list, because the naive version produced `domain carries` and `sup
    sup` beside the three terms that were wanted.
    """
    out: dict[str, int] = {}
    words = [m.group(0) for m in _TERM_WORD_RE.finditer(text)]
    lows = [w.lower() for w in words]
    for n in range(2, n_max + 1):
        for i in range(len(words) - n + 1):
            run = lows[i:i + n]
            if any(w in _TERM_STOPWORDS for w in run):
                continue
            if run[-1] in _TERM_VERB_TAIL or run[0] in _TERM_VERB_TAIL:
                continue
            out[" ".join(run)] = out.get(" ".join(run), 0) + 1
    return out


def abbreviations(paths: list[str], known_abbreviations: list[str] | None = None,
                  in_abstract: str = "define",
                  never_n: int = NEVER_ABBREVIATED_N,
                  reexpansion_distance: int = REEXPANSION_DISTANCE_WORDS
                  ) -> dict:
    """Never defined, defined only in the body, and defined then ignored.

    Five findings. Three of them name a sentence and an exact token and rest
    on no threshold, so they are `info` and go to module 1c. Two rest on a
    threshold that has not been calibrated against published prose, so they
    are `advisory`, they say `calibrated: false`, and they go to the user in
    the round summary.

    NOTHING HERE WRITES, and `defined_then_spelled_out` is the reason it is
    worth saying twice. Defining `(vWA)` and never using it wastes the
    definition; but *always* using it after defining it is worse - it is the
    mechanical reading of a rule that exists to serve a reader, and a pass
    that swapped every expansion for its short form would produce a paper
    that is technically consistent and worse to read. The user's own
    qualifier - "but only as dictated by readability" - is the instruction
    not to build this as a rewrite.
    """
    rule = in_abstract if in_abstract in ("define", "avoid") else "define"
    if in_abstract == "unknown":
        source = "house default (journal file is silent)"
    elif in_abstract in ("define", "avoid"):
        source = "journal requirements.yml" if in_abstract == "avoid" \
            else "house default"
    else:
        source = "house default"
    known = {k.strip() for k in (known_abbreviations or []) if k.strip()}
    exempt = _ELEMENTS | _UNITS | known

    findings: list[dict] = []
    suppressed: list[dict] = []

    # --- the regions, in reading order --------------------------------
    #
    # The abstract is found by HEADING and not by filename. It is
    # `title_abstract.md` today, but a journal that puts the abstract in the
    # body file would silently break a scope split keyed on the name - and
    # the scope split is most of this command.
    regions: list[dict] = []
    headings: list[dict] = []
    base = 0
    for path in paths:
        stem = os.path.splitext(os.path.basename(path))[0]
        if stem in ("live_captions", "toc_graphic"):
            continue
        raw = _read(path)
        if not raw.strip():
            continue
        for m in _HEADING_LINE_RE.finditer(_strip_comments(raw)):
            headings.append({"stem": stem, "text": m.group(1),
                             "line": raw.count("\n", 0, m.start()) + 1})
        abstract = _abstract_text(raw)
        if abstract.strip():
            # Only the abstract region of this file is read. The rest of it
            # is the title page, and a title is allowed to be shorter than a
            # sentence (3.6).
            body = _abbrev_prose(abstract)
            regions.append({"kind": "abstract", "stem": stem, "text": body,
                            "base": base, "path": path,
                            "line0": raw.count("\n", 0, raw.find(abstract)) + 1})
            base += len(body.split())
            continue
        body = _abbrev_prose(raw)
        regions.append({"kind": "body", "stem": stem, "text": body,
                        "base": base, "path": path, "line0": 1})
        base += len(body.split())

    def _loc(reg: dict, at: int) -> str:
        return "%s:%d" % (reg["stem"], reg["text"].count("\n", 0, at) + 1)

    def _wordat(reg: dict, at: int) -> int:
        return reg["base"] + len(reg["text"][:at].split())

    # --- every definition in the manuscript ----------------------------
    defs: dict[str, dict] = {}
    for reg in regions:
        for m in _ABBREV_PAREN_RE.finditer(reg["text"]):
            for frag in _PAREN_SPLIT_RE.split(m.group(1)):
                abbrev = frag.strip().rstrip(".").rstrip("s") or frag.strip()
                if not abbrev or " " in abbrev or len(abbrev) > 10:
                    continue
                if abbrev in exempt or _is_formula(abbrev):
                    continue
                if not _ABBREV_TOKEN_RE.fullmatch(abbrev):
                    continue
                term = _expansion_before(reg["text"], m.start(), abbrev)
                if not term:
                    continue
                rec = defs.setdefault(abbrev, {"abbrev": abbrev, "term": term,
                                               "where": {}, "first": None})
                rec["where"].setdefault(reg["kind"], {
                    "location": _loc(reg, m.start()),
                    "word": _wordat(reg, m.start()),
                    "sentence": " ".join(
                        reg["text"][max(0, m.start() - 120):m.end() + 60].split())})
                if rec["first"] is None:
                    rec["first"] = {"kind": reg["kind"],
                                    "word": _wordat(reg, m.start()),
                                    "location": _loc(reg, m.start())}

    # --- every bare use of an abbreviation, per region ------------------
    used: dict[str, list[dict]] = {}
    for reg in regions:
        for m in _ABBREV_TOKEN_RE.finditer(reg["text"]):
            token = m.group(0)
            if (token in exempt or token.rstrip("s") in exempt
                    or _is_formula(token)):
                continue
            lhs = reg["text"][max(0, m.start() - 2):m.start()]
            rhs = reg["text"][m.end():m.end() + 2]
            if lhs.rstrip().endswith("(") and rhs.lstrip().startswith(")"):
                continue                       # this IS the definition
            used.setdefault(token, []).append({
                "kind": reg["kind"], "location": _loc(reg, m.start()),
                "word": _wordat(reg, m.start()),
                "sentence": " ".join(
                    reg["text"][max(0, m.start() - 110):m.end() + 110].split())})

    abstract_regions = [r for r in regions if r["kind"] == "abstract"]
    # Whitespace-collapsed before anything is looked for in it. A term wraps
    # across a line in a hand-written abstract as often as not, and a plain
    # substring test misses every one that does - which on the first run was
    # `metal-ion-dependent adhesion site`, one of the two terms this check
    # exists to find.
    abstract_text = " ".join(" ".join(r["text"].split())
                             for r in abstract_regions).lower()
    has_abstract = bool(abstract_regions)

    # --- 1. the abstract spells it out and defines nothing --------------
    if rule == "define":
        for abbrev, rec in sorted(defs.items()):
            if "abstract" in rec["where"] or not has_abstract:
                continue
            if rec["term"].lower() not in abstract_text:
                continue
            if not any(u["kind"] == "body" for u in used.get(abbrev, [])):
                continue
            findings.append({
                "location": abstract_regions[0]["stem"] + ":abstract",
                "rule": "abstract_missing_definition", "severity": "info",
                "term": rec["term"].lower(), "abbreviation": abbrev,
                "advisory": False,
                "detail": f"the abstract writes {rec['term']!r} out in full "
                          f"and gives no abbreviation, and the body then uses "
                          f"{abbrev!r} bare. The abstract is read alone: "
                          f"define it there as well",
                "sentence": rec["where"]["body"]["sentence"][:200]})
    else:
        # `avoid` inverts it: an abbreviation INTRODUCED in the abstract is
        # what this journal does not want.
        for abbrev, rec in sorted(defs.items()):
            if "abstract" not in rec["where"]:
                continue
            findings.append({
                "location": rec["where"]["abstract"]["location"],
                "rule": "abstract_defines_abbreviation", "severity": "info",
                "term": rec["term"].lower(), "abbreviation": abbrev,
                "advisory": False,
                "detail": f"this journal asks that abbreviations be avoided "
                          f"in the abstract, and {abbrev!r} is introduced "
                          f"there",
                "sentence": rec["where"]["abstract"]["sentence"][:200]})

    # --- 2. defined in the abstract and nowhere else --------------------
    for abbrev, rec in sorted(defs.items()):
        if "abstract" not in rec["where"] or "body" in rec["where"]:
            continue
        findings.append({
            "location": rec["where"]["abstract"]["location"],
            "rule": "abstract_only_definition", "severity": "info",
            "term": rec["term"].lower(), "abbreviation": abbrev,
            "advisory": False,
            "detail": f"{abbrev!r} is defined in the abstract and never again "
                      f"in the body. The main text is read alone too, so it "
                      f"has to stand alone",
            "sentence": rec["where"]["abstract"]["sentence"][:200]})

    # --- 3. undefined at first use, scoped per region -------------------
    #
    # The existing `abbreviation_undefined`, moved here and scoped. It stays
    # OFF inside `readability`, so no existing caller changes behaviour.
    for abbrev, uses in sorted(used.items()):
        rec = defs.get(abbrev)
        for kind in ("abstract", "body"):
            first = next((u for u in uses if u["kind"] == kind), None)
            if first is None:
                continue
            here = (rec or {}).get("where", {}).get(kind)
            if here and here["word"] <= first["word"]:
                continue
            findings.append({
                "location": first["location"],
                "rule": "undefined_at_first_use", "severity": "info",
                "term": (rec or {}).get("term", "").lower(),
                "abbreviation": abbrev, "region": kind, "advisory": False,
                "detail": f"{abbrev!r} is used in the {kind} before it is "
                          f"expanded there"
                + (f" (it is defined in the {'body' if kind == 'abstract' else 'abstract'})"
                   if rec else ""),
                "sentence": first["sentence"][:200]})

    # --- 4. never abbreviated at all ------------------------------------
    #
    # Advisory, and the threshold is the reason. It reports the term and its
    # count and proposes nothing: coining `HFS` for a term the field spells
    # out is worse than the thing it fixes (3.6).
    whole = " ".join(r["text"] for r in regions)
    counts = _term_candidates(whole)
    known_terms = {rec["term"].lower() for rec in defs.values()}
    # A candidate that CONTAINS an abbreviation the paper already defined is
    # not an unabbreviated term - `vwa domain` fired on the real draft, and
    # `vWA` is the abbreviation. Measured, and it is the one false positive
    # in this rule that the paper itself can rule out.
    known_short = {a.lower() for a in defs} | {a.lower() for a in exempt}
    keep: dict[str, int] = {}
    for term, n in counts.items():
        if n < max(2, never_n):
            continue
        if any(term in kt for kt in known_terms):
            continue
        if any(w in known_short for w in term.split()):
            continue
        if re.search(re.escape(term) + r"\s*\(", whole.lower()):
            continue
        keep[term] = n
    for term, n in sorted(keep.items()):
        # Subsumption: `hyaline fibromatosis` and `hyaline fibromatosis
        # syndrome` are one term, and the longer one is the term.
        if any(term != other and term in other for other in keep):
            continue
        where = next((r for r in regions if term in r["text"].lower()), None)
        findings.append({
            "location": (where or regions[0])["stem"] if regions else "",
            "rule": "never_abbreviated", "severity": "advisory",
            "term": term, "count": n, "abbreviation": "", "advisory": True,
            "detail": f"{term!r} is written out {n} times and never given a "
                      f"short form. If the field has a conventional "
                      f"abbreviation, define it once; if it does not, leave "
                      f"it spelled out - nothing here proposes one"})

    # --- 5. defined, then spelled out again -----------------------------
    #
    # Four suppressions, and two of the three real firings measured on the
    # paper this came from are cases this has to stay quiet on. Each one is
    # RECORDED rather than silently applied, so a later change that removes
    # one is visible in the payload.
    for abbrev, rec in sorted(defs.items()):
        term = rec["term"]
        if not term or " " not in term:
            continue
        exact = re.compile(r"(?<![\w-])" + r"\s+".join(
            re.escape(w) for w in term.split()) + r"(?![\w-])", re.IGNORECASE)
        # A prefix of the term is a coordinated phrase, not an expansion.
        head = term.split()[:2]
        partial = re.compile(r"(?<![\w-])" + r"\s+".join(
            re.escape(w) for w in head) + r"(?![\w-])", re.IGNORECASE)
        first_word = (rec["first"] or {}).get("word", 0)
        for reg in regions:
            seen_full = {m.start(): m.end() for m in exact.finditer(reg["text"])}
            for m in partial.finditer(reg["text"]):
                at, word = m.start(), _wordat(reg, m.start())
                full = at in seen_full
                # Is this expansion a DEFINITION - the term followed by its
                # own parenthetical? Then it is what the house rule asks for,
                # wherever it is: once in the abstract and again at first use
                # in the body, because each is read alone. Measured on the
                # real draft, where the introduction re-defining `CMG2` after
                # the abstract had defined it read as a lapse.
                defines = bool(full and re.match(
                    r"\s*\([^()\n]{0,90}\)",
                    reg["text"][seen_full[at]:seen_full[at] + 100])
                    and abbrev.lower() in reg["text"][
                        seen_full[at]:seen_full[at] + 100].lower())
                why = ""
                if not full:
                    why = ("a coordinated or partial phrase, where the "
                           "abbreviation is not a drop-in")
                elif defines:
                    why = "the definition itself"
                elif reg["kind"] == "abstract":
                    why = ("in the abstract, which is its own scope and is "
                           "read alone")
                elif word <= first_word:
                    why = "the definition itself"
                elif word - first_word > reexpansion_distance:
                    why = ("too far from the definition to read as a lapse "
                           "rather than a reminder")
                if why:
                    suppressed.append({"why": why, "abbreviation": abbrev,
                                       "term": term.lower(),
                                       "location": _loc(reg, at)})
                    continue
                findings.append({
                    "location": _loc(reg, at),
                    "rule": "defined_then_spelled_out", "severity": "advisory",
                    "term": term.lower(), "abbreviation": abbrev,
                    "distance_words": word - first_word, "advisory": True,
                    "detail": f"{term!r} is written out again {word - first_word} "
                              f"words after {abbrev!r} was defined, and "
                              f"{abbrev!r} is used elsewhere. Only shorten it "
                              f"where the long form was not doing work - this "
                              f"reports and never substitutes",
                    "sentence": " ".join(
                        reg["text"][max(0, at - 110):m.end() + 110].split())[:220]})
        # The heading half of the same suppression, read back from the
        # headings rather than from the masked prose so it can be named.
        for h in headings:
            if exact.search(h["text"]) or partial.search(h["text"]):
                suppressed.append({
                    "why": "in a heading, caption or title, which is a label "
                           "rather than a sentence",
                    "abbreviation": abbrev, "term": term.lower(),
                    "location": "%s:%d" % (h["stem"], h["line"])})

    by_rule: dict[str, int] = {}
    for f in findings:
        by_rule[f["rule"]] = by_rule.get(f["rule"], 0) + 1
    return {
        "findings": findings,
        "by_rule": by_rule,
        "suppressed": suppressed,
        # Nothing here writes, and the key is present and empty rather than
        # absent so a caller cannot mistake this for a command that might.
        "written": [],
        "rule": rule,
        "source": source,
        "abstract": {"found": has_abstract,
                     "where": abstract_regions[0]["stem"]
                     if abstract_regions else "",
                     "note": "found by HEADING, not by filename - a journal "
                             "that puts the abstract in the body file would "
                             "otherwise break the scope split silently"},
        "definitions": [{"abbreviation": a, "term": r["term"],
                         "defined_in": sorted(r["where"])}
                        for a, r in sorted(defs.items())],
        "thresholds": {
            "never_abbreviated_n": never_n,
            "reexpansion_distance_words": reexpansion_distance,
            "calibrated": False, "advisory": True,
            "note": "both numbers are starting points drawn from one real "
                    "paper, not calibrated against published prose. Every "
                    "finding that rests on one is `advisory` and gates "
                    "nothing. Calibrate in specs/probes/abbreviations-<date>/ "
                    "- the number to look for is how FAR apart a definition "
                    "and a re-expansion are when a published paper does it",
        },
        "counts": {"findings": len(findings), "suppressed": len(suppressed),
                   "definitions": len(defs), "regions": len(regions)},
    }


def print_abbreviations(res: dict) -> None:
    print("Abbreviations - `%s` in the abstract, from %s"
          % (res["rule"], res["source"]))
    if not res["abstract"]["found"]:
        print("  no `## Abstract` heading anywhere - the abstract-scoped "
              "findings did not run")
    if not res["findings"]:
        print("  nothing to report across %d region(s), %d definition(s)."
              % (res["counts"]["regions"], res["counts"]["definitions"]))
    else:
        width = max(len(f["location"]) for f in res["findings"])
        for f in res["findings"]:
            mark = "advise" if f.get("advisory") else " found"
            print("  %s  %-*s  %-28s %s"
                  % (mark, width, f["location"], f["rule"],
                     f.get("abbreviation") or f.get("term", "")))
    if res["suppressed"]:
        print("\n%d expansion(s) deliberately not reported:"
              % len(res["suppressed"]))
        for s in res["suppressed"]:
            print("  %-14s %s - %s" % (s["location"], s["term"], s["why"]))
    th = res["thresholds"]
    print("\nThresholds: never_abbreviated N=%d, re-expansion distance=%d "
          "words. UNCALIBRATED and advisory - nothing here gates."
          % (th["never_abbreviated_n"], th["reexpansion_distance_words"]))


# ---------------------------------------------------------------------------
# Entity forms - specs/user-asks-2026-09-18-second.md 2
# ---------------------------------------------------------------------------
#
# The rule: an entity is named the same way in the abstract as it is in the
# body, or the difference is deliberate and someone said so.
#
# WORD-BOUNDARY ANCHORED, and the spec says so because it was measured: run
# unanchored over the real abstract this came from, `rat` finds three words
# in 163 - `rather`, `lite`+`rat`+`ures`, `st`+`rat`+`egies` - and none of
# them is an animal. That is the first thing a quick implementation gets
# wrong.
ENTITY_QUALIFIERS = ("human", "murine", "mouse", "rat", "bovine", "porcine",
                     "yeast", "bacterial", "viral", "recombinant",
                     "wild-type", "mutant")

# The single-letter shapes, and they are the risk this check carries. `hCMG2`
# is human CMG2; `mTOR` is not murine TOR. The guard is that the remainder
# has to be an entity THIS MANUSCRIPT actually names somewhere else - which
# `TOR` is not, in any paper that talks about mTOR.
ENTITY_PREFIXES = {"h": "human", "m": "murine", "r": "rat", "b": "bovine"}

_QUALIFIER_RE = re.compile(
    r"(?<![\w-])(" + "|".join(re.escape(q) for q in ENTITY_QUALIFIERS)
    + r")(?![\w])", re.IGNORECASE)

# The MANUSCRIPT-LEVEL species reading, which is a different question from
# "what word sits in front of this token". §2.1's measurement is this one: is
# there a species anywhere in this section, and is there one anywhere in the
# abstract. A body that talks about mice and patients throughout and an
# abstract that mentions no organism at all is the shape the user pointed at,
# and no adjacency rule finds it - the real draft never once writes `human
# CMG2` in its eight body sections.
ENTITY_SPECIES_WORDS = ("human", "humans", "murine", "mouse", "mice", "rat",
                        "rats", "patient", "patients", "bovine", "porcine",
                        "yeast")

_SPECIES_RE = re.compile(
    r"(?<![\w-])(" + "|".join(ENTITY_SPECIES_WORDS) + r")(?![\w-])",
    re.IGNORECASE)


def entity_forms(paths: list[str]) -> dict:
    """Every form each entity takes, and where the abstract disagrees.

    Reports and never writes. It cannot know whether `489-residue` is
    species-specific - that is a fact about the protein, not about the text -
    so the finding is *"the body qualifies CMG2 as human at first use and the
    abstract does not"*, and what follows is the drafter's call.
    """
    regions: list[dict] = []
    for path in paths:
        stem = os.path.splitext(os.path.basename(path))[0]
        if stem in ("live_captions", "toc_graphic"):
            continue
        raw = _read(path)
        if not raw.strip():
            continue
        abstract = _abstract_text(raw)
        if abstract.strip():
            # The title lives in this same file and is NOT read: a title is
            # allowed to be shorter than a sentence and routinely drops
            # qualifiers on purpose (2.4).
            regions.append({"kind": "abstract", "stem": stem,
                            "text": _abbrev_prose(abstract)})
            continue
        regions.append({"kind": "body", "stem": stem,
                        "text": _abbrev_prose(raw)})

    # Pass 1: every token, as written. The bare set is what the prefix guard
    # is resolved against, so it has to be complete before anything is split.
    raw_hits: list[dict] = []
    for reg in regions:
        for m in _ABBREV_TOKEN_RE.finditer(reg["text"]):
            token = m.group(0)
            if _is_formula(token) or token in _ELEMENTS or token in _UNITS:
                continue
            raw_hits.append({"reg": reg, "token": token, "at": m.start(),
                             "end": m.end()})
    bare_tokens = {h["token"] for h in raw_hits
                   if not (h["token"][:1].islower()
                           and h["token"][:1] in ENTITY_PREFIXES)}

    entities: dict[str, dict] = {}
    for h in raw_hits:
        reg, token, at = h["reg"], h["token"], h["at"]
        prefix = ""
        stem_tok = token
        if (token[:1] in ENTITY_PREFIXES and token[:1].islower()
                and token[1:] in bare_tokens):
            prefix, stem_tok = token[:1], token[1:]
        # The qualifier is the word immediately before, and only that word.
        before = reg["text"][max(0, at - 30):at]
        tail = re.split(r"[\n.!?;:,]", before)[-1]
        words = re.findall(r"[A-Za-z][A-Za-z-]*", tail)
        qual = ""
        if words and _QUALIFIER_RE.fullmatch(words[-1]):
            qual = words[-1].lower()
        elif prefix:
            qual = ENTITY_PREFIXES[prefix]
        form = (f"{qual} {stem_tok}" if qual and not prefix
                else (token if prefix else stem_tok))
        rec = entities.setdefault(stem_tok, {
            "token": stem_tok, "forms": [], "qualifiers": [],
            "abstract": [], "body": [], "first_body": None,
            "first_abstract": None})
        if form not in rec["forms"]:
            rec["forms"].append(form)
        if qual and qual not in rec["qualifiers"]:
            rec["qualifiers"].append(qual)
        where = {"location": "%s:%d" % (reg["stem"],
                                        reg["text"].count("\n", 0, at) + 1),
                 "form": form, "qualifier": qual,
                 "sentence": " ".join(
                     reg["text"][max(0, at - 110):h["end"] + 110].split())}
        rec[reg["kind"]].append(where)
        key = "first_" + reg["kind"]
        if rec[key] is None:
            rec[key] = where

    # §2.1's own measurement, carried whether or not anything fires on it.
    species = {"abstract": [], "sections": {}, "sections_with": 0,
               "sections_total": 0}
    for reg in regions:
        words = sorted({m.group(1).lower()
                        for m in _SPECIES_RE.finditer(reg["text"])})
        if reg["kind"] == "abstract":
            species["abstract"] = words
            continue
        species["sections_total"] += 1
        species["sections"][reg["stem"]] = words
        if words:
            species["sections_with"] += 1
    silent = (bool(species["sections_with"]) and not species["abstract"])

    findings: list[dict] = []
    for token, rec in sorted(entities.items()):
        if not rec["abstract"] or not rec["body"]:
            continue
        a, b = rec["first_abstract"], rec["first_body"]
        assert a is not None and b is not None
        body_quals = sorted({w["qualifier"] for w in rec["body"]
                             if w["qualifier"]})
        # `silent` is a fact about the MANUSCRIPT and never about an entity,
        # so it strengthens a finding and never creates one. Made to create
        # one, it fired on every bare token in the abstract - `mTOR`, `RhoA`,
        # `PA63` - which is exactly what 2.4 forbids: an unqualified name
        # everywhere is a consistent paper, not a defect.
        if body_quals and not a["qualifier"]:
            findings.append({
                "location": a["location"], "token": token,
                "rule": "abstract_qualifier_dropped", "severity": "info",
                "abstract_form": a["form"], "body_form": b["form"],
                "body_qualifiers": body_quals,
                "body_location": b["location"],
                "species_silent_abstract": silent,
                "detail": (
                    (f"the body qualifies {token!r} "
                     f"({', '.join(body_quals)}) and the abstract names it "
                     f"bare. " if body_quals else "")
                    + (f"{species['sections_with']} of "
                       f"{species['sections_total']} body sections name an "
                       f"organism and the abstract names none, while it "
                       f"carries {token!r} bare. " if silent else "")
                    + "The abstract is read alone, quoted alone and indexed "
                      "alone - nothing here decides whether that difference "
                      "is deliberate"),
                "sentence": a["sentence"][:220]})
            continue
        if a["form"] != b["form"]:
            findings.append({
                "location": a["location"], "token": token,
                "rule": "abstract_form_disagrees", "severity": "info",
                "abstract_form": a["form"], "body_form": b["form"],
                "body_qualifiers": body_quals,
                "body_location": b["location"],
                "detail": f"the abstract writes {a['form']!r} and the body's "
                          f"first use is {b['form']!r}. One of them is the "
                          f"paper's name for this entity - nothing here "
                          f"decides which",
                "sentence": a["sentence"][:220]})

    by_rule: dict[str, int] = {}
    for f in findings:
        by_rule[f["rule"]] = by_rule.get(f["rule"], 0) + 1
    return {
        "findings": findings,
        "by_rule": by_rule,
        "written": [],
        "entities": [
            {"token": r["token"], "forms": r["forms"],
             "qualifiers": r["qualifiers"],
             "in_abstract": len(r["abstract"]), "in_body": len(r["body"])}
            for _, r in sorted(entities.items())],
        "qualifiers": list(ENTITY_QUALIFIERS),
        "prefixes": ENTITY_PREFIXES,
        # Carried whether or not anything fired on it, because it is the
        # measurement the ask came from and a reader should be able to see it
        # rather than infer it from a finding.
        "species": species,
        "counts": {"findings": len(findings), "entities": len(entities),
                   "regions": len(regions)},
    }


def print_entity_forms(res: dict) -> None:
    c = res["counts"]
    sp = res["species"]
    if sp["sections_total"]:
        print("Species reading: %d of %d body sections name an organism; the "
              "abstract names %s."
              % (sp["sections_with"], sp["sections_total"],
                 ", ".join(sp["abstract"]) if sp["abstract"] else "none"))
    if not res["findings"]:
        print("Entity forms: the abstract and the body agree across %d "
              "entity(ies) in %d region(s)." % (c["entities"], c["regions"]))
        return
    print("Entity forms - %d finding(s) over %d entity(ies)\n"
          % (c["findings"], c["entities"]))
    width = max(len(f["location"]) for f in res["findings"])
    for f in res["findings"]:
        print("  %-*s  %-26s abstract %r vs body %r [%s]"
              % (width, f["location"], f["rule"], f["abstract_form"],
                 f["body_form"], f["body_location"]))
    print("\nReported, never decided: an abstract may legitimately be "
          "broader, and only the drafter knows whether this one is.")


# ---------------------------------------------------------------------------
# Text output
# ---------------------------------------------------------------------------

_SEV = {"error": "ERROR", "warning": "warn", "info": "info",
        "claim_changed": "CLAIM", "decision": "ASK",
        # prose 0.1 and 4.3: a logged override or a section the user has taken
        # over. Still printed - the point is that it is on the record, not that
        # it is silent - but it is not asking anyone to act.
        "advisory": "note"}


def _print_findings(findings: list[dict]) -> None:
    if not findings:
        print("  no findings")
        return
    width = max(len(f.get("location", "")) for f in findings)
    for f in findings:
        sev = _SEV.get(f.get("severity", ""), f.get("severity", ""))
        print(f"  {sev:5}  {f.get('location',''):{width}}  "
              f"{f.get('rule','')}: {f.get('detail','')}")


def print_density(res: dict) -> None:
    print(f"number density - {res['level']}\n")
    if res.get("budgets"):
        print(f"  {'section':16} {'budget':>16} {'basis':>9}  where it came "
              f"from")
        for sec, b in res["budgets"].items():
            shape = (f"{b['inline']:g}/{b['clusters']:g}"
                     if b["inline"] is not None else "unenforced")
            level = f" ({b['level']})" if b.get("level") else ""
            print(f"  {sec:16} {shape:>16} {b['basis']:>9}  "
                  f"{b['source']}{level}")
        print()
    print(f"  {'paragraph':22} {'words':>5} {'inline':>7} {'clusters':>9} "
          f"{'numeric':>8}")
    for p in res["paragraphs"]:
        b = p["budget"]
        inline = f"{len(p['inline'])}" + (f"/{b['inline']:g}" if b else "")
        clus = f"{len(p['clusters'])}" + (f"/{b['clusters']:g}" if b else "")
        mark = "  waived" if p["waived"] else (
            "  advisory" if p.get("advisory") else "")
        print(f"  {p['location']:22} {p['words']:>5} {inline:>7} {clus:>9} "
              f"{p['numeric_fraction']:>8.0%}{mark}")
    print()
    _print_findings(res["findings"])


def print_voice(res: dict) -> None:
    print("voice - reported, never graded (prose 3.4)\n")
    for s in res["sections"]:
        if s.get("empty"):
            print(f"  {s['section']}: empty\n")
            continue
        sw = s["sentence_words"]
        print(f"  {s['section']}  {s['words']} words, {s['sentences']} "
              f"sentences, {s['paragraphs']} paragraphs")
        print(f"    sentence words   mean {sw['mean']}  sd {sw['sd']}  "
              f"median {sw['median']}  max {sw['max']}")
        print(f"    openers          mean {s['openers']['mean']}  "
              f"sd {s['openers']['sd']}  max {s['openers']['max']}  "
              f"each {s['openers']['each']}")
        print(f"    leads last       {s['leads_ranked_last']} of "
              f"{s['paragraphs']} paragraphs")
        print(f"    citekeys         {s['citekeys']['per_sentence']}/sentence, "
              f"{s['citekeys']['sentences_with_two_plus']} sentences carry 2+")
        print(f"    numbers          {s['numbers']['per_sentence']}/sentence")
        print(f"    hedges           {s['hedges']['count']} "
              f"({s['hedges']['per_100_words']}/100w)   transitions "
              f"{s['transitions']['count']} "
              f"({s['transitions']['per_100_words']}/100w)")
        print(f"    nominalizations  {s['nominalizations']['count']} "
              f"({s['nominalizations']['with_weak_verb']} beside a weak verb)")
        print(f"    passive          {s['passive']['sentences']} sentences "
              f"({s['passive']['fraction']:.0%})")
        print(f"    paragraph words  mean {s['paragraph_words']['mean']}  "
              f"sd {s['paragraph_words']['sd']}")
        for i, l in enumerate(s["longest"], 1):
            print(f"    longest {i}  ¶{l['paragraph']}  {l['words']}w  "
                  f"{l['text'][:110]}")
        for p in s.get("paragraph_detail", []):
            rank = (f"{p['lead_rank']}/{p['lead_rank_of']}"
                    if p["lead_rank"] else "n/a")
            print(f"      ¶{p['n']:<3} {p['words']:>4}w  {p['sentences']:>2} "
                  f"sent  opener {p['opener_words']:>3}w  lead rank {rank:>5}"
                  f"  {p['lead'][:60]}")
        print()


def print_metaprose(res: dict) -> None:
    n = res["counts"]["findings"]
    print(f"{n} paragraph(s) of pipeline text" + (":" if n else ".") + "\n")
    for f in res["findings"]:
        mark = "  flagged" if f.get("flagged") else ""
        print(f"  {f['location']:16} {f['detail']}{mark}")
        print(f"                   {f['text'][:120]}")
    if res["written"]:
        print(f"\n  wrote a **[FLAG: meta-prose ...]** into "
              f"{len(res['written'])} file(s)")


def print_outline(res: dict) -> None:
    print(f"outline adherence - {res.get('label', res['adherence'])}"
          f"  ({res['note']})")
    if res["state"] == "missing":
        print("\n  plan/outline.md does not exist. `manuscript.py outline "
              "<project>` reports what one could be built from.\n")
    elif res["state"] == "empty":
        print("\n  plan/outline.md exists but has no paragraph lines in it "
              "yet.\n")
    else:
        print(f"\n  {res['counts']['outline_lines']} paragraph lines, "
              f"{res['counts']['notes_lines']} lines of notes"
              + (f", {res['counts'].get('waived', 0)} waived"
                 if res['counts'].get('waived') else "") + "\n")
    if res.get("waived"):
        print("  settled for good - not drafted, not counted in adherence:")
        for w in res["waived"]:
            print(f"    outline.md:{w['line']:<4} {w['claim'][:56]}")
            print(f"      because: {w['because'] or '(no reason given)'}")
        print("")
    print(f"  {'section':16} {'outline lines':>13} {'paragraphs':>11}")
    for s in res["sections"]:
        print(f"  {s['section']:16} {s['outline_lines']:>13} "
              f"{s['paragraphs']:>11}")
    print()
    _print_findings(res["findings"])


def print_flags(res: dict) -> None:
    print(f"{res['counts']['flags']} flags "
          f"({res['needs_a_human']} only a human can answer)\n")
    for f in res["flags"]:
        print(f"  {f['type']:9} {f['location']:16} {f['message']}")
    if res["unknown_types"]:
        print(f"\n  unknown flag types: {', '.join(res['unknown_types'])}")


def print_citekeys(res: dict) -> None:
    print(f"{res['entries']} bib entries, {res['cited']} keys cited\n")
    _print_findings(res["findings"])


def print_captions(res: dict) -> None:
    print(f"{res['counts']['floats']} floats\n")
    for f in res["floats"]:
        where = ", ".join(f["referenced_in"]) or "not referenced"
        print(f"  {f['label']:12} {f['words']:>4}w  {where}")
        if f["subtitle"]:
            print(f"               {f['subtitle'][:96]}")
    print()
    _print_findings(res["findings"])


def print_crossrefs(res: dict) -> None:
    c = res["counts"]
    # The DID NOT RUN line LEADS, before any count. A count printed first is
    # read as a result whatever follows it.
    if res.get("did_not_run"):
        print("DID NOT RUN  crossrefs - %s\n" % res["did_not_run"])
    print(f"{c['floats']} floats, {c['mentions']} callouts, "
          f"{c['panels_declared']} panels declared "
          f"({c['panels_missing']} never cited)\n")
    print(f"  {'float':12} {'first mention':18} {'panels':14} callouts")
    for f in res["floats"]:
        panels = ("/".join(f["panels_referenced"]) or "-")
        if f["panels_missing"]:
            panels += f"  missing {','.join(f['panels_missing'])}"
        paren = sum(1 for m in f["mentions"]
                    if m["parenthetical"] and not m["implied"])
        real = sum(1 for m in f["mentions"] if not m["implied"])
        print(f"  {f['label']:12} {f['first_mention'] or 'never':18} "
              f"{panels:14} {real} ({paren} in parentheses)")
    print()
    _print_findings(res["findings"])


def print_length(res: dict) -> None:
    c = res["counts"]
    print(f"word counts - policy '{res['policy']}'\n")
    # The breakdown is this project's own sections in document order, not the
    # IMRaD four: an author of a review asked to cut 300 words needs to see
    # the sections they wrote. `counts` is built in document order.
    skip = {"title_abstract", "figures", "tables", "body", "total", "abstract"}
    scopes = (["abstract"] + [k for k in c if k not in skip]
              + ["body", "total"])
    w = max([14] + [len(s) + 1 for s in scopes])
    for scope in scopes:
        cap = res["limits"].get(scope)
        mark = ""
        if cap:
            mark = f" / {cap}" + ("  OVER" if c[scope] > cap else "")
        print(f"  {scope:{w}} {c[scope]:>6}{mark}")
    print(f"  {'figures':{w}} {c['figures']:>6}"
          + (f" / {res['limits']['figures_max']}"
             if res["limits"].get("figures_max") else ""))
    print(f"  {'tables':{w}} {c['tables']:>6}"
          + (f" / {res['limits']['tables_max']}"
             if res["limits"].get("tables_max") else ""))
    print(f"\n  counted: {res['counted']}\n")
    _print_findings(res["findings"])


def print_numbers(res: dict) -> None:
    print(f"{res['checked']} numbers in the text, "
          f"{res['stats_values']} values in the stats output\n")
    _print_findings(res["findings"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _unquote(value: str) -> str:
    """One value out of writing_config.yml.

    This is a CONTRACT with manuscript.py's parse_config, which writes the
    file: a quoted item keeps everything inside the quotes, and only an
    unquoted one has a trailing comment stripped. Getting that backwards
    truncates a waiver at its first '#' - and a waiver that does not match its
    paragraph id silently stops waiving, so the same paragraph is re-flagged
    every round with nothing reporting why. Measured; it really happened.
    Change this, change manuscript.py's `_unquote`, and re-run tests/prose.py.
    """
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value.split("#", 1)[0].strip()


def _flow_mapping(text: str) -> dict | None:
    """`{inline: 6, clusters: 2, basis: absolute}` as a dict, or None.

    The one nested shape writing_config.yml is allowed to carry (prose 5.9).
    A number stays a number and everything else stays a string, because
    `basis` is a word and `inline` is a rate.
    """
    text = text.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return None
    out: dict = {}
    for part in text[1:-1].split(","):
        if not part.strip():
            continue
        key, _, val = part.partition(":")
        key, val = key.strip(), _unquote(val)
        if not key:
            return None
        try:
            out[key] = float(val) if re.match(r"^-?\d+(\.\d+)?$", val) else val
        except ValueError:
            out[key] = val
    return out


def load_density_config(config_path: str | None) -> dict:
    """number_density, its per-section mapping, and the waivers (prose 5.9).

    Deliberately a small hand parse rather than a yaml dependency: the file is
    flat apart from this one key, and the rest of this toolkit is standard
    library only.

    The scalar spelling keeps its meaning exactly - `number_density: low` is
    still one level for every section, because an existing project's config
    cannot change meaning under a new version of the tool. A mapping under the
    same key is the new spelling, and the two are distinguished by whether the
    value on the key's own line is empty.
    """
    out: dict = {"level": None, "sections": {}, "waivers": [],
                 "problems": []}
    if not config_path or not os.path.isfile(config_path):
        return out
    lines = _read(config_path).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if re.match(r"^number_density\s*:", line):
            rest = _unquote(line.split(":", 1)[1])
            if rest:
                out["level"] = rest
                # An indented block under a key that already has a value is
                # the one shape this file can carry that means two different
                # things at once. Reading the level and silently dropping the
                # mapping gives an answer nobody wrote, so it is reported.
                while i < len(lines) and (not lines[i].strip()
                                          or re.match(r"^\s+\S", lines[i])):
                    if re.match(r"^\s+([a-z_]+)\s*:", lines[i]):
                        out["problems"].append(
                            f"number_density: {rest!r} is on the key's own "
                            f"line and {lines[i].strip()!r} is indented under "
                            f"it. The level wins and the mapping is not being "
                            f"read - write one or the other")
                    i += 1
                continue
            # A mapping: every indented `section: value` under the key.
            while i < len(lines):
                nxt = lines[i]
                if not nxt.strip():
                    i += 1
                    continue
                if not re.match(r"^\s+\S", nxt) or re.match(r"^\s+-\s", nxt):
                    break
                i += 1
                m = re.match(r"^\s+([a-z_]+)\s*:\s*(.*)$", nxt)
                if not m:
                    continue
                sec, val = m.group(1), m.group(2).strip()
                # A flow mapping may wrap; without this the value read is not
                # the value written, which is the failure mode manuscript.py's
                # own reader already had to grow a guard for.
                while val.startswith("{") and "}" not in val and i < len(lines):
                    val += " " + lines[i].strip()
                    i += 1
                mapping = _flow_mapping(val)
                if mapping is not None:
                    if "inline" not in mapping:
                        out["problems"].append(
                            f"number_density.{sec}: a rate needs `inline`; "
                            f"{val!r} has none, so it is being ignored")
                        continue
                    out["sections"][sec] = mapping
                else:
                    out["sections"][sec] = _unquote(val)
        elif re.match(r"^number_density_waivers\s*:", line):
            rest = line.split(":", 1)[1].strip()
            if rest.startswith("[") and rest.endswith("]"):
                inner = rest[1:-1].strip()
                if inner:
                    out["waivers"].extend(_unquote(w) for w in inner.split(",")
                                          if w.strip())
            else:
                while i < len(lines) and re.match(r"^\s+-\s*", lines[i]):
                    out["waivers"].append(
                        _unquote(re.sub(r"^\s+-\s*", "", lines[i])))
                    i += 1
    if out["level"] and out["sections"]:
        out["problems"].append(
            "number_density carries both a level on its own line and a "
            "per-section mapping under it; the level wins, and the mapping is "
            "not being read")
    return out


def load_waivers(config_path: str | None) -> tuple[str | None, list[str]]:
    """The pair `manuscript.py` and its tests read. Kept as it was."""
    cfg = load_density_config(config_path)
    return cfg["level"], cfg["waivers"]


OVERRIDE_ROW_RE = re.compile(
    r"^\|\s*([A-Za-z_][\w ]*?)\s*\|\s*(?:¶\s*)?(\d+)\s*\|\s*([^|]*?)\s*\|")


def load_overrides(path: str | None) -> dict:
    """`reports/rN/overrides.md` as the paragraphs whose budget yielded.

    prose 0.1: where a layer 1-3 rule requires breaking the density budget the
    drafter does it and writes the row. A finding on a paragraph with a logged
    override is advisory, not a warning - that is the mechanism that makes the
    prohibition locally yieldable with a reason on the record, rather than
    loosened globally.
    """
    rows: list[dict] = []
    advisory: list[str] = []
    if not path or not os.path.isfile(path):
        return {"rows": rows, "advisory": advisory}
    for line in _read(path).splitlines():
        m = OVERRIDE_ROW_RE.match(line)
        if not m:
            continue
        section, para, rule = m.group(1).strip(), m.group(2), m.group(3)
        if section.lower() in ("section", "---"):
            continue
        loc = f"{section} ¶{para}"
        rows.append({"location": loc, "rule": rule})
        if re.search(r"densit|number", rule, re.IGNORECASE):
            advisory.append(loc)
    return {"rows": rows, "advisory": advisory}


def load_learned_budgets(rules_path: str | None, research_type: str | None,
                         problems: list[str] | None = None) -> dict:
    """Steps 2 and 3 of the resolution order, from the learned registry.

    `learn.py` owns the registry and its YAML, so it is imported rather than
    re-implemented here - the same reuse `scholar.py` makes of `pubmed.py`.
    A missing or unreadable registry is reported and resolution falls through
    to the built-in defaults; it never silently changes a budget.
    """
    if not rules_path or not research_type:
        return {}
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import learn as _learn                      # local engine, stdlib only
    except Exception as exc:                        # pragma: no cover - import
        if problems is not None:
            problems.append(f"cannot load learn.py to read {rules_path}: "
                            f"{exc}")
        return {}
    try:
        return _learn.resolved_budgets(rules_path, research_type)
    except Exception as exc:
        if problems is not None:
            problems.append(f"cannot read {rules_path}: {exc}")
        return {}


def main() -> int:
    p = argparse.ArgumentParser(
        prog="prose.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true",
                   help="emit JSON instead of text")

    # --json on either side of the subcommand, per the convention in CLAUDE.md.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true",
                        default=argparse.SUPPRESS,
                        help="emit JSON instead of text")

    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("density", parents=[common],
                       help="number budget per paragraph")
    d.add_argument("paths", nargs="+",
                   help="section .md files, or a project directory")
    d.add_argument("--level", choices=list(DENSITY_BUDGETS), default=None,
                   help="force one level on every section; without it each "
                        "section resolves its own budget (prose 5.9)")
    d.add_argument("--config", default=None,
                   help="writing_config.yml to read the level and waivers from")
    d.add_argument("--waiver", action="append", default=[],
                   help="a paragraph id to exempt, e.g. 'results ¶3'")
    d.add_argument("--rules", default=None,
                   help="writing_guides/learned/rules.yml, for a learned "
                        "budget (resolution steps 2 and 3)")
    d.add_argument("--research-type", default=None,
                   help="project.yml:research_type, which scopes every "
                        "learned rule")
    d.add_argument("--paper-kind", default="", choices=["", *PAPER_KINDS],
                   help="`review` releases every budget finding to advisory "
                        "(review-paper 4.1): comparing quantities across "
                        "studies is a review's substance, not a wall of "
                        "numbers. Read from the first path's project.yml "
                        "when that path is a project directory. It does NOT "
                        "release where a number came from - that is layer 0 "
                        "and lives in `numbers` and `review.py tier`")
    d.add_argument("--overrides", default=None,
                   help="reports/rN/overrides.md; a logged override makes its "
                        "paragraph's finding advisory (prose 0.1)")
    d.add_argument("--advisory", action="append", default=[],
                   metavar="SECTION",
                   help="a section the user has taken over: its budget "
                        "findings are advisory (prose 4.3). Repeatable")

    o = sub.add_parser("outline", parents=[common],
                       help="outline lines vs. paragraphs, and evidence brackets")
    o.add_argument("project")
    o.add_argument("--adherence", choices=ADHERENCE_VALUES,
                   default=str(ADHERENCE_DEFAULT),
                   help="1 none | 2 loose | 3 medium | 4 tight | 5 strict; "
                        "the number and the name are the same value")
    o.add_argument("--file", dest="source", default=None, metavar="PATH",
                   help="read the outline lines out of this file instead of "
                        "the project's plan/outline.md - what to run when "
                        "the user says the outline lives somewhere else")

    f = sub.add_parser("flags", parents=[common],
                       help="collect **[FLAG: ...]** with locations")
    f.add_argument("paths", nargs="+",
                   help="section .md files, or a project directory")

    c = sub.add_parser("citekeys", parents=[common],
                       help="in-text citekeys vs. references.bib")
    c.add_argument("project")
    c.add_argument("--max-refs", type=int, default=None,
                   help="the journal's reference cap")

    cp = sub.add_parser("captions", parents=[common],
                        help="claim-first legends and float/text pairing")
    cp.add_argument("project")
    cp.add_argument("--cap", type=int, default=None,
                    help="the journal's caption word cap")

    x = sub.add_parser("crossrefs", parents=[common],
                       help="every float and panel called out, in order")
    x.add_argument("project")
    x.add_argument("--style", default="unknown",
                   choices=["parenthetical", "running_text", "either",
                            "unknown"],
                   help="figures.citation_style from requirements.yml")
    x.add_argument("--table-style", default="unknown",
                   choices=["parenthetical", "running_text", "either",
                            "unknown"],
                   help="tables.citation_style from requirements.yml")
    x.add_argument("--order", default="",
                   help="reading order of the sections, comma separated; "
                        "defaults to the scaffold's")
    x.add_argument("--callout-emphasis", action="store_true",
                   help="the journal wants callouts emphasized "
                        "(typography.callout_emphasis); do not report them")

    lg = sub.add_parser("length", parents=[common],
                        help="word counts against the journal's budget")
    lg.add_argument("project")
    lg.add_argument("--limit", action="append", default=[], metavar="SCOPE=N",
                    help="a journal limit, e.g. total=3500, abstract=250, "
                         "figures_max=6")
    lg.add_argument("--policy", default="ask", choices=LENGTH_POLICIES,
                    help="what the user already decided about going over")

    n = sub.add_parser("numbers", parents=[common],
                       help="every number in the text vs. the stats output")
    n.add_argument("project")

    v = sub.add_parser("voice", parents=[common],
                       help="how the prose reads - reported, never graded")
    v.add_argument("paths", nargs="+",
                   help="section .md files, or a project directory")
    v.add_argument("--per-paragraph", action="store_true",
                   help="also report every paragraph: opener length, lead "
                        "rank, hedges, passives")

    rd = sub.add_parser("readability", parents=[common],
                        help="what `voice` does not measure - reported, "
                             "never graded, and no reading level ever")
    rd.add_argument("paths", nargs="+",
                    help="section .md files IN READING ORDER, or a project "
                         "directory. Order matters: the abbreviation and "
                         "given-new measures are manuscript-wide")
    rd.add_argument("--audience", default="specialist",
                    choices=["specialist", "field", "broad"],
                    help="the reader this was measured for. It is printed in "
                         "the report, because a finding whose reader is "
                         "unstated cannot be argued with")
    rd.add_argument("--known", action="append", default=[], metavar="ABBREV",
                    help="an abbreviation this project does not expand, e.g. "
                         "--known XPS. writing_config.yml's "
                         "`known_abbreviations` is the durable place for it")
    rd.add_argument("--abbreviations", action="store_true",
                    help="report the undefined-abbreviation findings. OFF by "
                         "default and measured that way: written the obvious "
                         "way the check fires on 18 of 24 real published "
                         "abstracts, which teaches the reader to discount "
                         "the whole class")
    rd.add_argument("--config", default=None,
                    help="writing_config.yml, for `audience` and "
                         "`known_abbreviations`")

    cl = sub.add_parser("claim", parents=[common],
                        help="the abstract's named take-home, and whether "
                             "the paper carries it")
    cl.add_argument("project")
    cl.add_argument("--report", default=None,
                    help="the report carrying `## Take-home` and "
                         "`## Supported by`; defaults to "
                         "reports/prose_report.md, falling back to a "
                         "reports/abstract_claim.md left by an older round")

    mp = sub.add_parser("metaprose", parents=[common],
                        help="pipeline text that reached the manuscript")
    mp.add_argument("paths", nargs="+",
                    help="section .md files, or a project directory")
    mp.add_argument("--insert", action="store_true",
                    help="write a **[FLAG: meta-prose ...]** into each "
                         "matching paragraph, leaving its words alone")

    av = sub.add_parser("ai_voice", parents=[common],
                        help="the 15 countable AI-voice patterns "
                             "(prose 10.3) - report only, never a gate")
    av.add_argument("paths", nargs="+",
                    help="section .md files, or a project directory")

    sp = sub.add_parser("spelling", parents=[common],
                        help="British spellings in the manuscript - the one "
                             "check here that CORRECTS rather than reports")
    sp.add_argument("paths", nargs="+",
                    help="section .md files, or a project directory. A .bib "
                         "or other reference file is refused: a published "
                         "title keeps the spelling it was printed with")
    sp.add_argument("--fix", action="store_true",
                    help="write the corrections. Without it the words are "
                         "listed and nothing is touched")
    sp.add_argument("--dialect", default="us", choices=list(DIALECTS),
                    help="the dialect to write IN. Only `us` is built")

    it = sub.add_parser("italics", parents=[common],
                        help="Latin phrases that should be italic, and gene "
                             "symbols that may be - the second check here "
                             "that CORRECTS")
    it.add_argument("paths", nargs="+",
                    help="section .md files, or a project directory. A .bib "
                         "or other reference file is refused: a published "
                         "title keeps the typography it was printed with")
    it.add_argument("--fix", action="store_true",
                    help="italicise the `always` set. The journal set and "
                         "every gene symbol are reported and never written")
    it.add_argument("--journal", default="",
                    help="the journal's name, for the report")
    it.add_argument("--journal-term", action="append", default=[],
                    metavar="TERM",
                    help=f"turn on one of the house-dependent terms "
                         f"({', '.join(ITALIC_JOURNAL)}). Repeatable. "
                         f"requirements.yml is the durable place for it")
    it.add_argument("--roman-term", action="append", default=[],
                    metavar="TERM",
                    help="a term this journal sets ROMAN, e.g. --roman-term "
                         "'in vivo' for a house that treats it as naturalised "
                         "English. It leaves the always set and is neither "
                         "reported nor written. Repeatable")

    ef = sub.add_parser("entity_forms", parents=[common],
                        help="every form each entity takes, and where the "
                             "abstract names it differently from the body")
    ef.add_argument("paths", nargs="+",
                    help="section .md files IN READING ORDER, or a project "
                         "directory. Order matters: `first use in the body` "
                         "is a fact about the manuscript")

    ab = sub.add_parser("abbreviations", parents=[common],
                        help="defined nowhere, defined only in the body, or "
                             "defined and then spelled out again")
    ab.add_argument("paths", nargs="+",
                    help="section .md files IN READING ORDER, or a project "
                         "directory. Order matters: the abstract and the body "
                         "are separate scopes and the distance between a "
                         "definition and a re-expansion is manuscript-wide")
    ab.add_argument("--in-abstract", default="define",
                    choices=list(ABBREV_IN_ABSTRACT),
                    help="requirements.yml's `abbreviations_in_abstract`. "
                         "`define` is the house rule; `avoid` inverts the "
                         "finding for a journal that forbids abbreviations "
                         "in an abstract; `unknown` means the file is silent "
                         "and the house applies, and the report says so")
    ab.add_argument("--known", action="append", default=[], metavar="ABBREV",
                    help="an abbreviation this project does not expand. "
                         "writing_config.yml's `known_abbreviations` is the "
                         "durable place for it")
    ab.add_argument("--never-n", type=int, default=NEVER_ABBREVIATED_N,
                    help=f"how many times a term is written out before "
                         f"`never_abbreviated` fires (default "
                         f"{NEVER_ABBREVIATED_N}). UNCALIBRATED")
    ab.add_argument("--reexpansion-distance", type=int,
                    default=REEXPANSION_DISTANCE_WORDS,
                    help=f"how many words after its definition an expansion "
                         f"still reads as a lapse rather than a reminder "
                         f"(default {REEXPANSION_DISTANCE_WORDS}). "
                         f"UNCALIBRATED")

    args = p.parse_args()
    as_json = getattr(args, "json", False)

    if args.cmd == "density":
        paths: list[str] = []
        for path in args.paths:
            paths.extend(source_files(path) if os.path.isdir(path) else [path])
        cfg = load_density_config(args.config)
        overrides = load_overrides(args.overrides)
        problems = list(cfg["problems"])
        learned = load_learned_budgets(args.rules, args.research_type,
                                       problems)
        kind = args.paper_kind or next(
            (paper_kind(x) for x in args.paths if os.path.isdir(x)),
            "research")
        res = density(paths, args.level or cfg["level"],
                      cfg["waivers"] + args.waiver,
                      config=cfg["sections"], learned=learned,
                      advisory=overrides["advisory"] + args.advisory,
                      released=(kind == "review"))
        res["paper_kind"] = kind
        for problem in problems:
            res["problems"].append(problem)
            res["findings"].append({"location": "writing_config.yml",
                                    "rule": "unreadable_budget",
                                    "severity": "warning", "detail": problem})
        res["counts"]["findings"] = len(res["findings"])
        res["overrides"] = overrides["rows"]
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_density(res)
        return 1 if any(f["severity"] == "error" for f in res["findings"]) else 0

    if args.cmd == "outline":
        if not os.path.isdir(args.project):
            print(f"No such directory: {args.project}", file=sys.stderr)
            return 2
        if args.source and not os.path.isfile(args.source):
            print(f"No such file: {args.source}", file=sys.stderr)
            return 2
        res = outline(args.project, args.adherence, args.source)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_outline(res)
        return 1 if any(f["severity"] == "error" for f in res["findings"]) else 0

    if args.cmd == "flags":
        paths = []
        for path in args.paths:
            paths.extend(source_files(path) if os.path.isdir(path) else [path])
        res = flags(paths)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_flags(res)
        return 0

    if args.cmd == "citekeys":
        if not os.path.isdir(args.project):
            print(f"No such directory: {args.project}", file=sys.stderr)
            return 2
        res = citekeys(args.project, args.max_refs)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_citekeys(res)
        return 1 if any(f["severity"] == "error" for f in res["findings"]) else 0

    if args.cmd == "captions":
        if not os.path.isdir(args.project):
            print(f"No such directory: {args.project}", file=sys.stderr)
            return 2
        res = captions(args.project, args.cap)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_captions(res)
        return 1 if any(f["severity"] == "error" for f in res["findings"]) else 0

    if args.cmd == "crossrefs":
        if not os.path.isdir(args.project):
            print(f"No such directory: {args.project}", file=sys.stderr)
            return 2
        order = [s.strip() for s in args.order.split(",") if s.strip()] or None
        res = crossrefs(args.project, args.style, args.table_style, order,
                        args.callout_emphasis)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_crossrefs(res)
        return 1 if any(f["severity"] == "error" for f in res["findings"]) else 0

    if args.cmd == "length":
        if not os.path.isdir(args.project):
            print(f"No such directory: {args.project}", file=sys.stderr)
            return 2
        limits: dict = {}
        for item in args.limit:
            key, _, val = item.partition("=")
            try:
                limits[key.strip()] = int(val)
            except ValueError:
                print(f"--limit wants SCOPE=N, got {item!r}", file=sys.stderr)
                return 2
        res = length(args.project, limits, args.policy)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_length(res)
        # Over the budget is a decision to make, never a failure to build on:
        # exit 1 says "there is something outstanding here", the same thing an
        # unfinished paper says.
        return 1 if res["over"] else 0

    if args.cmd == "numbers":
        if not os.path.isdir(args.project):
            print(f"No such directory: {args.project}", file=sys.stderr)
            return 2
        res = numbers(args.project)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_numbers(res)
        return 1 if any(f["severity"] == "error" for f in res["findings"]) else 0

    if args.cmd == "voice":
        paths = []
        for path in args.paths:
            paths.extend(source_files(path) if os.path.isdir(path) else [path])
        res = voice(paths, args.per_paragraph)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_voice(res)
        # No threshold, no budget, no pass/fail (prose 3.4). A gate here would
        # recreate the problem the spec exists to fix one layer up.
        return 0

    if args.cmd == "readability":
        paths = []
        for path in args.paths:
            paths.extend(source_files(path) if os.path.isdir(path) else [path])
        known = list(args.known)
        audience = args.audience
        if args.config:
            cfg_text = _read(args.config)
            m = re.search(r"^audience:\s*(\S+)", cfg_text, re.M)
            if m and m.group(1) in ("specialist", "field", "broad"):
                audience = m.group(1)
            m = re.search(r"^known_abbreviations:\s*\[(.*?)\]", cfg_text,
                          re.M | re.S)
            if m:
                known += [x.strip().strip("\"'") for x in m.group(1).split(",")
                          if x.strip()]
        res = readability(paths, known, audience, args.abbreviations)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_readability(res)
        # Always 0. Every finding here is advisory at every dial value, and
        # the command exits 0 on a file full of them (prose 3.4).
        return 0

    if args.cmd == "claim":
        res = claim(args.project, args.report)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_claim(res)
        # An absent or unsupported claim is a finding, not a build failure
        # (prose 6). What is missing is the commitment, and saying so is the
        # whole of the remedy.
        return 0

    if args.cmd == "ai_voice":
        paths = []
        for path in args.paths:
            paths.extend(source_files(path) if os.path.isdir(path) else [path])
        res = ai_voice(paths)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_ai_voice(res)
        # Always 0, on any number of findings. Under prose 0.2 not one of the
        # 25 is a layer-0 invariant, so not one may block, and under 10.1
        # these carry no bar to fail against: they are principles the paper is
        # trying to beat the average on, not floors it must clear.
        return 0

    if args.cmd == "spelling":
        paths = []
        for path in args.paths:
            paths.extend(source_files(path) if os.path.isdir(path) else [path])
        res = spelling(paths, args.fix, args.dialect)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_spelling(res)
        # 2 when the request itself was refused - an unbuilt dialect is bad
        # input, not a finding. Otherwise 0: this command's job is to leave
        # the file correct, and a corrected file is not a failure. Under
        # prose 0.2 a spelling is not a layer-0 invariant either, so the
        # un-fixed case exits 0 as well and says what is left.
        if res["refused"] and not res["counts"]["files"]:
            return 2
        return 0

    if args.cmd == "italics":
        paths = []
        for path in args.paths:
            paths.extend(source_files(path) if os.path.isdir(path) else [path])
        res = italics(paths, args.fix, args.journal_term, args.journal,
                      args.roman_term)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_italics(res)
        # Always 0, with findings and without. Under prose 0.2 typography is
        # not a layer-0 invariant, and the half of this command that cannot be
        # applied by lookup is handed to the user rather than failed at them.
        return 0

    if args.cmd == "entity_forms":
        paths = []
        for path in args.paths:
            paths.extend(source_files(path) if os.path.isdir(path) else [path])
        res = entity_forms(paths)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_entity_forms(res)
        # Always 0. A difference between the abstract and the body is a
        # question for the drafter, and this command cannot answer it.
        return 0

    if args.cmd == "abbreviations":
        paths = []
        for path in args.paths:
            paths.extend(source_files(path) if os.path.isdir(path) else [path])
        res = abbreviations(paths, args.known, args.in_abstract,
                            args.never_n, args.reexpansion_distance)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_abbreviations(res)
        # Always 0. Two of the five findings rest on an uncalibrated
        # threshold and the other three are about typography of reference, so
        # under prose 0.2 not one of them is a layer-0 invariant.
        return 0

    if args.cmd == "metaprose":
        paths = []
        for path in args.paths:
            paths.extend(source_files(path) if os.path.isdir(path) else [path])
        res = metaprose(paths, args.insert)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if as_json \
            else print_metaprose(res)
        # A flag, never a build failure (prose 0.2): a word list will have
        # false positives, and a false positive that fails a build is what
        # teaches the user to stop reading the report.
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
