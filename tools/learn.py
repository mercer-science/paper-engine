#!/usr/bin/env python3
"""
learn.py - the learning loop, and the drafting brief it assembles.

`specs/writing-engine-prose.md` 5, and [MK] rule 10. The problem it exists for
is measured, not theoretical: the user edits r1, the engine regenerates r2 from
`plan/outline.md`, and the edits are gone - and the next PROJECT starts from the
same brief that produced the prose the user just spent an afternoon fixing.
Every correction is paid for once and banked never.

So: apply and learn are separate steps and both are required. Applying without
learning is what the toolkit did before this file. Learning without applying
would leave the current paper inconsistent with the lesson just taken from it.

The registry lives in the TOOLKIT (`writing_guides/learned/`), not in a
project, because its whole purpose is to make the next project's r1 better.

Thirteen commands:

  extract    aligned before/after pairs for a section the user edited   (5.1)
  classify   the mechanical evidence that decides an edit's class       (5.2)
  observe    record one observation; promotes it when it earns it       (5.4)
  promote    force a candidate active, deliberately and on the record   (5.4)
  retire     retire a rule; kept, never deleted                         (5.4)
  resolve    record a contradiction's outcome: demote, supersede, both  (5.6)
  conflicts  every active rule whose scope overlaps this one            (5.6)
  merge      union another registry by id - the shared-folder case      (5.8)
  stats      observations per type, thin types, digest overlap          (7)
  budgets    what each section's budget resolves to, and why            (5.9)
  brief      the whole ladder, layers 0-4, as one ordered document      (5.7)
  types      the controlled vocabulary; adding one needs a reason       (5.5)
  frozen     which sections the user has taken over, by hash            (4.1)

What is judgment and what is arithmetic is the same split as everywhere else
here. Whether an edit is a rule is the module's call; whether the numeric
multiset changed, whether two scopes overlap, whether a threshold is met, and
what a budget resolves to all have right answers and live here.

Two commands are not in the spec's list and are needed to make the rest work:
`observe`, which is how an observation reaches the registry at all, and
`resolve`, which is how 5.6's three outcomes get written down. `frozen` is 4.1's
hash, put where the tool that consumes it can reach it.

Usage:
  python learn.py extract --project "path/to/project" --section methods --json
  python learn.py classify --project "path/to/project" --section methods --json
  python learn.py observe --rule "Open the abstract with the material system" \\
      --kind stylistic --research-type surface_science --section title_abstract \\
      --project "Surface Example" --round r1 --paragraph 1 \\
      --before "..." --after "..." --json
  python learn.py brief --research-type surface_science --section results --json
  python learn.py budgets --research-type surface_science --json
  python learn.py conflicts LR-014 --json
  python learn.py stats --json
"""

from __future__ import annotations

import argparse
import collections
import datetime
import difflib
import hashlib
import importlib.util
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

# prose.py holds the sentence splitter, the number counter and the citekey
# regex, and all three are contracts with what the rest of the toolkit
# measures. Reuse rather than a second implementation: an edit that "changed a
# number" has to mean the same thing here as it does in `prose.py numbers`.
# Loaded by path, so it resolves the same whether learn.py is run as a script,
# imported by prose.py, or loaded under another name by a test.
_PROSE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "prose.py")
_spec = importlib.util.spec_from_file_location("prose_for_learn", _PROSE_PATH)
if _spec is None or _spec.loader is None:      # pragma: no cover - install bug
    raise ImportError(f"cannot load {_PROSE_PATH}")
prose = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prose)


class RegistryError(Exception):
    """A registry that cannot be read is not an empty registry.

    Same failure shape as a broken settings.json disabling a deny rule: the
    dangerous outcome is not the error, it is drafting on with no layer 3 and
    nothing saying so.
    """


# ---------------------------------------------------------------------------
# Where things live
# ---------------------------------------------------------------------------

TOOLKIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEARNED_DIR = os.path.join(TOOLKIT_ROOT, "writing_guides", "learned")
TOOLKIT_RULES_FILE = os.path.join(LEARNED_DIR, "rules.yml")
TOOLKIT_TYPES_FILE = os.path.join(LEARNED_DIR, "research_types.yml")
DIGEST_FILE = os.path.join(TOOLKIT_ROOT, "writing_guides", "writing_rules.md")


# 5.8. The two files under writing_guides/learned/ resolve DIFFERENTLY, and
# the difference is the whole reason this is a function rather than a constant.
#
# The toolkit ships as a plugin, and a plugin directory is replaced wholesale
# on update - anything a user wrote inside it is gone. So:
#
#   research_types.yml  ships, read-only. The vocabulary has to be COMMON or
#                       5.5's proliferation guard runs per installation against
#                       a vocabulary of one, and nothing is ever learned while
#                       nothing errors.
#   rules.yml           never ships, always written. One person's evidence,
#                       quoting one person's unpublished prose (5.8).
#
# Before this split `rules.yml` sat at one hard-wired path inside the toolkit,
# which means the first `/plugin update` would have deleted every user's entire
# learning with no error and no warning. That is this spec's own definition of
# the worst kind of defect, which is why the order below is specified rather
# than left to the implementation.

def types_file() -> str:
    """$PWA_TYPES_FILE, else the toolkit. Never CLAUDE_PLUGIN_DATA."""
    return os.environ.get("PWA_TYPES_FILE") or TOOLKIT_TYPES_FILE


def rules_file() -> str:
    """$PWA_RULES_FILE, else ${CLAUDE_PLUGIN_DATA}/rules.yml, else the toolkit.

    The last fallback keeps a source checkout behaving exactly as it did
    before the split, so the author's own registry does not move.
    """
    return rules_home()[0]


def rules_home() -> tuple[str, str]:
    """(path, source) - source is `env`, `plugin_data` or `toolkit`."""
    env = os.environ.get("PWA_RULES_FILE")
    if env:
        return env, "env"
    data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if data:
        return os.path.join(data, "rules.yml"), "plugin_data"
    return TOOLKIT_RULES_FILE, "toolkit"


def stranded_registry() -> str | None:
    """A toolkit rules.yml that resolution is no longer pointing at.

    Not an error and not migrated automatically - but the failure it guards
    against is the quiet one: a registry that exists, is not being read, and
    says nothing while every draft goes out with no layer 3.
    """
    path, source = rules_home()
    if source == "toolkit":
        return None
    if os.path.abspath(path) == os.path.abspath(TOOLKIT_RULES_FILE):
        return None
    if os.path.isfile(TOOLKIT_RULES_FILE) and _read(TOOLKIT_RULES_FILE).strip():
        return TOOLKIT_RULES_FILE
    return None

STATUSES = ("candidate", "active", "conflict", "superseded", "retired")
KINDS = ("stylistic", "structural", "formatting", "numeric")
# `revert` is first because it outranks the rest: every other class is a
# statement about a sentence, and a revert is a statement about a RULE - the
# user saw what the rule did to their paper and undid it (prose 11.5).
EDIT_CLASSES = ("revert", "factual", "content", "citation", "stylistic",
                "structural", "formatting")

# 5.4. One observation is never a rule: a single edit is as likely to be a
# one-off as a preference, and a registry that learns from n=1 is wrong in a
# way that is very expensive to notice - the wrongness arrives as slightly
# worse first drafts on an unrelated paper months later.
THRESHOLDS = {"stylistic": 2, "structural": 2, "numeric": 2, "formatting": 3}

# 5.10d. Three independent waivers demote `active` -> `candidate`. Counted the
# way observations are counted, so three waivers in one section of one round
# are one.
WAIVERS_TO_DEMOTE = 3

# 5.5. The vocabulary is controlled because the failure mode to design against
# is type proliferation: a type invented per project scopes every rule to a
# population of one, no rule ever reaches two observations, and nothing is ever
# learned. Nothing errors while that happens.
SEED_TYPES = [
    {"id": "surface_science", "label": "Surface science / UHV",
     "parents": ["physical_science"]},
    {"id": "biochemistry", "label": "Biochemistry",
     "parents": ["life_science"]},
    {"id": "structural_bio", "label": "Structural biology / cryo-EM",
     "parents": ["life_science"]},
    {"id": "clinical", "label": "Clinical / medical",
     "parents": ["life_science"]},
    {"id": "synthetic_chem", "label": "Synthetic chemistry",
     "parents": ["physical_science"]},
    {"id": "methods_dev", "label": "Methods development", "parents": []},
    {"id": "physical_science", "label": "Physical science (parent)",
     "parents": []},
    {"id": "life_science", "label": "Life science (parent)", "parents": []},
]

UNIVERSAL = "universal"
ANY_SECTION = "any"

# The third scope axis (specs/review-paper.md 1). `research_type` is what a
# paper is ABOUT; `paper_kind` is what it IS, and they are orthogonal - there
# are surface-science reviews and surface-science research papers, and their
# prose is not the same prose.
#
# Without this axis the first review the user edits teaches the registry that
# surface_science papers attribute every sentence to somebody, hedge
# constantly and carry no methods, and those rules are then applied, at layer
# 3, to the next research paper. That is research_type proliferation's mirror
# image: not a rule scoped to a population of one, but a rule scoped to a
# population of two things that are not alike.
#
# ANY_KIND is the absent value, on the same rule research_type follows: every
# record written before this axis existed keeps applying exactly where it
# applies today. A rule only becomes kind-scoped by being INDUCED from
# evidence that is all one kind, which is how the review's lessons stay in
# the review.
PAPER_KINDS = ("research", "review")
ANY_KIND = "any"


# ---------------------------------------------------------------------------
# A YAML subset: read and write, no dependency
# ---------------------------------------------------------------------------
# The toolkit is standard library only, and the registry has exactly two
# shapes in it - a list of records, and mappings of scalars, flow mappings and
# lists of mappings inside each. So this reads that subset and refuses
# everything else LOUDLY, which is the point: a malformed rules.yml must not
# read as an empty one.

_TRUE = {"true", "yes", "on"}
_FALSE = {"false", "no", "off"}
_NULL = {"", "null", "~", "none"}


def _strip_comment(line: str) -> str:
    """Drop a trailing `#` comment, respecting quotes."""
    out, quote = [], ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#" and (not out or out[-1] in " \t"):
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out).rstrip()


def _scalar(text: str, line_no: int) -> object:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        body = text[1:-1]
        return body.replace('\\"', '"').replace("\\n", "\n") \
            if text[0] == '"' else body
    if text.lower() in _NULL:
        return None
    if text.lower() in _TRUE:
        return True
    if text.lower() in _FALSE:
        return False
    if re.match(r"^-?\d+$", text):
        return int(text)
    if re.match(r"^-?\d+\.\d+$", text):
        return float(text)
    if text.startswith(("{", "[")):
        return _parse_flow(text, line_no)
    return text


def _split_flow(body: str, line_no: int) -> list[str]:
    """Split a flow collection's body on commas at depth zero."""
    parts, depth, quote, cur = [], 0, "", []
    for ch in body:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch in "{[":
            depth += 1
            cur.append(ch)
        elif ch in "}]":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if quote:
        raise RegistryError(f"line {line_no}: a quote is never closed")
    parts.append("".join(cur))
    return [p for p in (p.strip() for p in parts) if p]


def _parse_flow(text: str, line_no: int) -> object:
    text = text.strip()
    if text.startswith("{"):
        if not text.endswith("}"):
            raise RegistryError(f"line {line_no}: {{ is never closed")
        out: dict = {}
        for part in _split_flow(text[1:-1], line_no):
            key, sep, val = part.partition(":")
            if not sep:
                raise RegistryError(
                    f"line {line_no}: {part!r} is not `key: value`")
            out[key.strip().strip("\"'")] = _scalar(val, line_no)
        return out
    if text.startswith("["):
        if not text.endswith("]"):
            raise RegistryError(f"line {line_no}: [ is never closed")
        return [_scalar(p, line_no) for p in _split_flow(text[1:-1], line_no)]
    raise RegistryError(f"line {line_no}: {text!r} is not a flow collection")


_VALUE_RE = re.compile(
    r"^(?:-\s+)?(?:(?:[A-Za-z_][\w.-]*|\"[^\"]*\"|'[^']*')\s*:\s*)?(.*)$")


def _flow_closed(text: str) -> bool:
    depth, quote = 0, ""
    for ch in text:
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return True
    return False


def _needs_continuation(content: str) -> bool:
    """Does this line's value carry on to the next one?

    Two things wrap in a hand-edited registry and both have bitten a reader in
    this toolkit already: a flow mapping over two lines, and a quoted scalar
    over several. A reader that takes only the first line does not report an
    error - it reports a different value, which is worse.

    The test is on how the value OPENS, and that is the whole trick: asking
    "are the quotes balanced?" makes `rule: the user's own edits` an unclosed
    string and swallows the rest of the file, and an apostrophe is ordinary in
    a rule written as a sentence.
    """
    m = _VALUE_RE.match(content)
    val = (m.group(1) if m else content).strip()
    if not val:
        return False
    if val.startswith(("{", "[")):
        return not _flow_closed(val)
    if val.startswith(("\"", "'")):
        return not (len(val) > 1 and val.endswith(val[0]))
    return False


def _logical_lines(text: str) -> list[tuple[int, int, str]]:
    """(line number, indent, content), with wrapped values joined."""
    raw = text.splitlines()
    out: list[tuple[int, int, str]] = []
    i = 0
    while i < len(raw):
        line = _strip_comment(raw[i])
        no = i + 1
        i += 1
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        content = line.strip()
        while _needs_continuation(content) and i < len(raw):
            nxt = _strip_comment(raw[i])
            i += 1
            content += " " + nxt.strip()
        out.append((no, indent, content))
    return out


def _looks_like_key(content: str) -> bool:
    return bool(re.match(r"^(?:[A-Za-z_][\w.-]*|\"[^\"]+\"|'[^']+')\s*:"
                         r"(?:\s|$)", content))


def parse_yaml(text: str) -> object:
    """The registry's subset of YAML. Raises RegistryError, never guesses."""
    lines = _logical_lines(text)
    if not lines:
        return []
    value, idx = _parse_block(lines, 0, lines[0][1])
    if idx != len(lines):
        no, _, content = lines[idx]
        raise RegistryError(
            f"line {no}: {content!r} is indented in a way this reader cannot "
            f"place; the registry is a list of records, one `- id: ...` each")
    return value


def _parse_block(lines: list[tuple[int, int, str]], idx: int,
                 indent: int) -> tuple[object, int]:
    if lines[idx][2].startswith("- ") or lines[idx][2] == "-":
        return _parse_sequence(lines, idx, indent)
    return _parse_mapping(lines, idx, indent)


def _parse_sequence(lines: list[tuple[int, int, str]], idx: int,
                    indent: int) -> tuple[list, int]:
    out: list = []
    while idx < len(lines):
        no, ind, content = lines[idx]
        if ind < indent or not (content.startswith("- ") or content == "-"):
            break
        if ind > indent:
            raise RegistryError(f"line {no}: this item is indented further "
                                f"than the list it is in")
        dash = content[1:]
        rest = dash.strip()
        # Where a key on the dash line actually starts, which is where its
        # sibling keys have to be: `- id: x` puts them at ind + 2, and
        # `-   id: x` at ind + 4. Deriving it from the NEXT line instead reads
        # a nested block's indent as the sibling indent, and then `status:`
        # after a nested `scope:` lands inside the scope.
        inner_indent = ind + 1 + (len(dash) - len(dash.lstrip()))
        idx += 1
        if not rest:
            if idx < len(lines) and lines[idx][1] > ind:
                value, idx = _parse_block(lines, idx, lines[idx][1])
                out.append(value)
            else:
                out.append(None)
            continue
        if rest.startswith(("{", "[")):
            out.append(_scalar(rest, no))
            continue
        if _looks_like_key(rest):
            # A mapping whose first key sits on the dash line. Its remaining
            # keys are indented to where that key starts.
            key, _, val = rest.partition(":")
            item, idx = _finish_mapping(lines, idx, inner_indent,
                                        key.strip(), val.strip(), no)
            out.append(item)
            continue
        out.append(_scalar(rest, no))
    return out, idx


def _finish_mapping(lines: list[tuple[int, int, str]], idx: int, indent: int,
                    first_key: str, first_val: str,
                    first_no: int) -> tuple[dict, int]:
    into: dict = {}
    if first_val:
        into[first_key] = _scalar(first_val, first_no)
    elif idx < len(lines) and lines[idx][1] > indent:
        value, idx = _parse_block(lines, idx, lines[idx][1])
        into[first_key] = value
    else:
        into[first_key] = None
    rest, idx = _parse_mapping(lines, idx, indent, allow_empty=True)
    for key, val in rest.items():
        into[key] = val
    return into, idx


def _parse_mapping(lines: list[tuple[int, int, str]], idx: int, indent: int,
                   allow_empty: bool = False) -> tuple[dict, int]:
    out: dict = {}
    while idx < len(lines):
        no, ind, content = lines[idx]
        if ind < indent:
            break
        if ind > indent:
            raise RegistryError(
                f"line {no}: {content!r} is indented further than the key it "
                f"belongs to and this reader cannot place it")
        if content.startswith("- "):
            break
        if not _looks_like_key(content):
            if allow_empty and not out:
                break
            raise RegistryError(f"line {no}: {content!r} is not `key: value`")
        key, _, val = content.partition(":")
        key, val = key.strip().strip("\"'"), val.strip()
        idx += 1
        if val:
            # A plain scalar may continue on the following more-indented lines
            # - the shape a hand-written `note:` takes.
            while (idx < len(lines) and lines[idx][1] > ind
                   and not _looks_like_key(lines[idx][2])
                   and not lines[idx][2].startswith("- ")
                   and not val.startswith(("{", "[", '"', "'"))):
                val += " " + lines[idx][2]
                idx += 1
            out[key] = _scalar(val, no)
            continue
        if idx < len(lines) and lines[idx][1] > ind:
            value, idx = _parse_block(lines, idx, lines[idx][1])
            out[key] = value
        elif (idx < len(lines) and lines[idx][1] == ind
              and lines[idx][2].startswith(("- ", "-"))):
            # A block list written at the key's own indent, which is legal
            # YAML and what a hand edit usually produces.
            value, idx = _parse_sequence(lines, idx, ind)
            out[key] = value
        else:
            out[key] = None
    return out, idx


def _needs_quotes(text: str) -> bool:
    if text == "" or text != text.strip():
        return True
    if text[0] in "-?:,[]{}#&*!|>%@`\"'":
        return True
    if re.search(r":\s|\s#", text):
        return True
    if text.lower() in _TRUE | _FALSE | _NULL:
        return True
    return bool(re.match(r"^-?\d+(\.\d+)?$", text))


# A comma is harmless in block context and a SEPARATOR in flow context, and
# nothing distinguished the two until `demoted_because` became a list of the
# user's own prose. "third time, the rule fights the house style" emitted as a
# bare flow item split into two items and left the `[` unclosed - a registry
# that would not read back. Free text goes in flow only when it is quoted.
_FLOW_UNSAFE = set(",[]{}")


def _emit_scalar(value: object, flow: bool = False) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return f"{value}"
    text = str(value)
    if _needs_quotes(text) or (flow and _FLOW_UNSAFE & set(text)):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') \
            .replace("\n", "\\n") + '"'
    return text


def _emit_flow(value: dict | list) -> str:
    if isinstance(value, dict):
        inner = ", ".join(f"{k}: {_emit_scalar(v, flow=True)}"
                          for k, v in value.items())
        return "{" + inner + "}"
    return "[" + ", ".join(_emit_scalar(v, flow=True) for v in value) + "]"


def _emit(value: object, indent: int, lines: list[str]) -> None:
    pad = " " * indent
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and not _flowable(item):
                first = True
                for key, val in item.items():
                    prefix = f"{pad}- " if first else f"{pad}  "
                    first = False
                    _emit_pair(prefix, key, val, indent + 2, lines)
            else:
                lines.append(f"{pad}- " + (_emit_flow(item)
                                           if isinstance(item, (dict, list))
                                           else _emit_scalar(item)))
        return
    if isinstance(value, dict):
        for key, val in value.items():
            _emit_pair(pad, key, val, indent, lines)
        return
    lines.append(pad + _emit_scalar(value))


def _flowable(value: object) -> bool:
    """Small mappings of scalars are written inline, as the spec writes them."""
    if not isinstance(value, dict) or len(value) > 4:
        return False
    return all(not isinstance(v, (dict, list)) and
               len(str(v)) <= 40 for v in value.values())


def _emit_pair(prefix: str, key: str, value: object, indent: int,
               lines: list[str]) -> None:
    if isinstance(value, dict):
        if _flowable(value):
            lines.append(f"{prefix}{key}: {_emit_flow(value)}")
        else:
            lines.append(f"{prefix}{key}:")
            _emit(value, indent + 2, lines)
        return
    if isinstance(value, list):
        if not value:
            lines.append(f"{prefix}{key}: []")
        elif (all(not isinstance(v, (dict, list)) for v in value)
                and sum(len(str(v)) + 2 for v in value) <= 72):
            lines.append(f"{prefix}{key}: {_emit_flow(value)}")
        else:
            lines.append(f"{prefix}{key}:")
            _emit(value, indent + 2, lines)
        return
    lines.append(f"{prefix}{key}: {_emit_scalar(value)}")


def dump_yaml(value: object) -> str:
    lines: list[str] = []
    _emit(value, 0, lines)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

RECORD_ORDER = ["id", "rule", "kind", "value", "scope", "status",
                "observations", "independent", "waivers", "evidence",
                "waived", "supersedes", "superseded_by", "conflicts_with",
                "demoted_from", "demoted_because", "note", "promoted_by",
                "retired_because", "learned", "reinforced", "updated"]


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


HEADER = """# Learned drafting rules - specs/writing-engine-prose.md 5.4.
#
# Induced from the user's own edits, one record per rule, scoped by research
# type. This file lives in the toolkit rather than in a project because its
# whole purpose is to make the NEXT project's first draft better.
#
# Hand edits are expected and safe: every record is keyed by `id`, `merge`
# unions by id, and `status: retired` is how a rule stops applying. Deleting a
# record throws away the evidence that produced it, which is the one thing
# worth keeping even when the rule was wrong.
"""

TYPES_HEADER = """# The controlled vocabulary of research types - prose 5.5.
#
# Layer 3 of the brief is scoped by these, so they have to be stable. A type
# invented per project scopes every rule to a population of one, no rule ever
# reaches two observations, and nothing is ever learned - and nothing errors
# while that happens. Hence a closed list, extended only with a reason.
#
# `parents` is how a rule generalizes one step: a rule scoped to life_science
# applies to biochemistry, structural_bio and clinical.
"""


def read_registry(path: str | None = None) -> list[dict]:
    path = path or rules_file()
    if not os.path.isfile(path):
        return []
    text = _read(path)
    if not text.strip():
        return []
    data = parse_yaml(text)
    if not isinstance(data, list):
        raise RegistryError(
            f"{os.path.basename(path)} is not a list of records; a registry is "
            f"`- id: LR-001` per rule")
    out: list[dict] = []
    for i, rec in enumerate(data, 1):
        if not isinstance(rec, dict):
            raise RegistryError(f"{os.path.basename(path)}: record {i} is not "
                                f"a mapping")
        for key in ("id", "rule", "kind", "status"):
            if not rec.get(key):
                raise RegistryError(
                    f"{os.path.basename(path)}: record {i} has no {key!r}; a "
                    f"rule with no {key} cannot be applied or audited")
        if rec["kind"] not in KINDS:
            raise RegistryError(
                f"{os.path.basename(path)}: {rec['id']} has kind "
                f"{rec['kind']!r}, not one of {', '.join(KINDS)}")
        if rec["status"] not in STATUSES:
            raise RegistryError(
                f"{os.path.basename(path)}: {rec['id']} has status "
                f"{rec['status']!r}, not one of {', '.join(STATUSES)}")
        scope = rec.get("scope") or {}
        if not isinstance(scope, dict):
            raise RegistryError(f"{os.path.basename(path)}: {rec['id']} has a "
                                f"scope that is not a mapping")
        rec["scope"] = {"research_type": scope.get("research_type")
                        or UNIVERSAL,
                        "paper_kind": scope.get("paper_kind") or ANY_KIND,
                        "section": scope.get("section") or ANY_SECTION}
        ev = rec.get("evidence") or []
        if not isinstance(ev, list):
            raise RegistryError(f"{os.path.basename(path)}: {rec['id']}'s "
                                f"evidence is not a list")
        rec["evidence"] = [e for e in ev if isinstance(e, dict)]
        wv = rec.get("waived") or []
        if not isinstance(wv, list):
            raise RegistryError(f"{os.path.basename(path)}: {rec['id']}'s "
                                f"waived list is not a list")
        rec["waived"] = [w for w in wv if isinstance(w, dict)]
        db = rec.get("demoted_because") or []
        if db and not isinstance(db, list):
            rec["demoted_because"] = [db]
        out.append(rec)
    return out


def write_registry(records: list[dict], path: str | None = None) -> str:
    path = path or rules_file()
    ordered = [_ordered(r) for r in records]
    _write(path, HEADER + "\n" + dump_yaml(ordered))
    return path


def _ordered(rec: dict) -> dict:
    out: dict = {}
    for key in RECORD_ORDER:
        if key in rec and rec[key] not in (None, [], {}):
            out[key] = rec[key]
    for key in rec:
        if key not in out and rec[key] not in (None, [], {}):
            out[key] = rec[key]
    return out


def read_types(path: str | None = None) -> list[dict]:
    path = path or types_file()
    if not os.path.isfile(path):
        return [dict(t) for t in SEED_TYPES]
    data = parse_yaml(_read(path))
    if not isinstance(data, list):
        raise RegistryError(f"{os.path.basename(path)} is not a list of types")
    out: list[dict] = []
    for rec in data:
        if not isinstance(rec, dict) or not rec.get("id"):
            raise RegistryError(f"{os.path.basename(path)}: a type with no id")
        rec.setdefault("parents", [])
        if not isinstance(rec["parents"], list):
            rec["parents"] = [rec["parents"]]
        out.append(rec)
    return out


def write_types(types: list[dict], path: str | None = None) -> str:
    path = path or types_file()
    _write(path, TYPES_HEADER + "\n" + dump_yaml(types))
    return path


def next_id(records: list[dict]) -> str:
    used = []
    for rec in records:
        m = re.match(r"^LR-(\d+)$", str(rec.get("id", "")))
        if m:
            used.append(int(m.group(1)))
    return f"LR-{(max(used) + 1) if used else 1:03d}"


# ---------------------------------------------------------------------------
# Scope: research types, ancestors, overlap
# ---------------------------------------------------------------------------

def type_ancestors(type_id: str, types: list[dict]) -> list[str]:
    """`surface_science` -> [surface_science, physical_science, universal]."""
    by_id = {t["id"]: t for t in types}
    seen: list[str] = []
    queue = [type_id]
    while queue:
        cur = queue.pop(0)
        if cur in seen or not cur:
            continue
        seen.append(cur)
        queue.extend(by_id.get(cur, {}).get("parents", []) or [])
    if UNIVERSAL not in seen:
        seen.append(UNIVERSAL)
    return seen


def scope_applies(rule: dict, research_type: str | None, section: str | None,
                  types: list[dict], paper_kind: str | None = None) -> bool:
    """Does this rule reach (research_type, paper_kind, section)?

    A rule scoped to an ancestor applies to its children - that is what
    `parents` is for - and `any`/`universal` reach everything. The reverse is
    never true: a rule learned in surface_science does not apply to a
    biochemistry paper just because both are science.

    `paper_kind` is a FLAT axis with no ancestry: a review is not a kind of
    research paper and a research paper is not a kind of review. A rule
    scoped to one kind reaches that kind and `any`; a caller that names no
    kind gets everything, because the great majority of callers predate the
    axis and asking them all at once is how a rule quietly stops applying.
    """
    scope = rule.get("scope") or {}
    want_type = scope.get("research_type") or UNIVERSAL
    want_section = scope.get("section") or ANY_SECTION
    want_kind = scope.get("paper_kind") or ANY_KIND
    if section and want_section not in (ANY_SECTION, section):
        return False
    if paper_kind and want_kind not in (ANY_KIND, paper_kind):
        return False
    if not research_type:
        return want_type == UNIVERSAL
    return want_type in type_ancestors(research_type, types)


def scopes_overlap(a: dict, b: dict, types: list[dict]) -> bool:
    """5.6: same section (or `any`), and the same type, an ancestor of it, or
    universal. Mechanical, which is why it is here and not in the module."""
    sa, sb = a.get("scope") or {}, b.get("scope") or {}
    sec_a = sa.get("section") or ANY_SECTION
    sec_b = sb.get("section") or ANY_SECTION
    if ANY_SECTION not in (sec_a, sec_b) and sec_a != sec_b:
        return False
    ka = sa.get("paper_kind") or ANY_KIND
    kb = sb.get("paper_kind") or ANY_KIND
    if ANY_KIND not in (ka, kb) and ka != kb:
        # Two rules that can never meet the same paper do not conflict, and
        # calling them a conflict would put a review's attribution habit and
        # a research paper's results habit into the same fight forever.
        return False
    ta = sa.get("research_type") or UNIVERSAL
    tb = sb.get("research_type") or UNIVERSAL
    if UNIVERSAL in (ta, tb) or ta == tb:
        return True
    return tb in type_ancestors(ta, types) or ta in type_ancestors(tb, types)


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

def observation_key(ev: dict) -> tuple:
    return (str(ev.get("project", "")), str(ev.get("round", "")),
            str(ev.get("section", "")), str(ev.get("paragraph", "")))


def independent_key(ev: dict) -> tuple:
    """What the threshold counts: a section in a round.

    5.4 wants two observations "in different sections or different rounds", and
    says the same edit made twice in one paragraph is one observation. So two
    paragraphs of one section in one round are two observations and one piece
    of independent evidence - which is the honest reading, and the one that
    stops a single afternoon's editing of a single section from minting a rule.
    """
    return (str(ev.get("project", "")), str(ev.get("round", "")),
            str(ev.get("section", "")))


def waiver_key(w: dict) -> tuple:
    """Independence for a waiver, counted exactly as 5.4 counts it for an
    observation: a section in a round of a project.

    Three waivers in one section of one round are ONE waiver. Otherwise a
    single unlucky section demotes a good rule, and the mechanism meant to
    stop the registry being too restrictive becomes a way of losing rules.
    """
    return (str(w.get("project", "")), str(w.get("round", "")),
            str(w.get("section", "")))


def waiver_counts(rec: dict) -> tuple[int, int]:
    """(total, independent)."""
    wv = rec.get("waived") or []
    return len(wv), len({waiver_key(w) for w in wv})


def last_reinforced(rec: dict) -> str:
    """The most recent date any evidence arrived, else when it was learned.

    A rule with three waivers and nothing reinforcing it in six months is a
    different object from one with three observations across two projects,
    and the drafter cannot weigh what it cannot see (5.10a).
    """
    dates = [str(e.get("date")) for e in rec.get("evidence", []) if e.get("date")]
    if rec.get("reinforced"):
        dates.append(str(rec["reinforced"]))
    return max(dates) if dates else str(rec.get("learned") or "")


def projects_in(rec: dict) -> list[str]:
    seen: list[str] = []
    for ev in rec.get("evidence", []):
        name = str(ev.get("project") or "")
        if name and name not in seen:
            seen.append(name)
    return seen


def counted_evidence(rec: dict) -> list[dict]:
    """Evidence that may induce. 7: an edit made to fit a word limit looks
    exactly like a preference for shorter prose, so a round that was over the
    journal's cap is applied and never learned from."""
    return [e for e in rec.get("evidence", [])
            if not e.get("length_forced")]


def observation_counts(rec: dict) -> tuple[int, int]:
    ev = counted_evidence(rec)
    return (len({observation_key(e) for e in ev}),
            len({independent_key(e) for e in ev}))


def research_types_in(rec: dict) -> list[str]:
    return sorted({str(e.get("research_type")) for e in rec.get("evidence", [])
                   if e.get("research_type")})


def paper_kinds_in(rec: dict) -> list[str]:
    """Which paper kinds this rule has actually been observed on.

    Evidence recorded before the axis existed carries no `paper_kind` and is
    not counted either way - it neither widens a rule nor narrows one, which
    is what keeps an existing registry behaving exactly as it does today.
    """
    return sorted({str(e.get("paper_kind")) for e in rec.get("evidence", [])
                   if e.get("paper_kind") in PAPER_KINDS})


def evaluate_status(rec: dict) -> dict:
    """Candidate or active, and why - never a silent promotion."""
    observations, independent = observation_counts(rec)
    rec["observations"] = observations
    rec["independent"] = independent
    total_waivers, indep_waivers = waiver_counts(rec)
    if total_waivers:
        rec["waivers"] = total_waivers
    need = THRESHOLDS.get(str(rec.get("kind")), 2)
    reason = ""
    if rec.get("status") in ("retired", "superseded", "conflict"):
        return {"status": rec["status"], "reason": "left as it was",
                "observations": observations, "independent": independent,
                "threshold": need}
    # 5.10d. A waiver is negative evidence arriving one step earlier than
    # 5.4's "the rule's own prediction was edited away" - from the drafter
    # rather than the user, and far cheaper to collect: a line in a report
    # rather than a round of the user's editing. Three independent ones stop
    # the rule being applied.
    #
    # Demotion, NOT retirement. Retirement stays the user's call and the
    # evidence remains real; `promote --because` brings a wrongly demoted rule
    # back in one command. A deliberate promotion outranks the demotion, which
    # is what makes that recovery work.
    if (indep_waivers >= WAIVERS_TO_DEMOTE and not rec.get("promoted_by")
            and rec.get("kind") != "numeric"):
        rec["status"] = "candidate"
        rec["demoted_because"] = [str(w.get("because") or "").strip()
                                  for w in (rec.get("waived") or [])
                                  if str(w.get("because") or "").strip()]
        return {"status": "candidate",
                "reason": (f"demoted: {indep_waivers} independent waiver(s), "
                           f"threshold {WAIVERS_TO_DEMOTE} - applied to "
                           f"nothing until promoted"),
                "observations": observations, "independent": independent,
                "waivers": total_waivers, "independent_waivers": indep_waivers,
                "demoted": True, "threshold": need}
    if rec.get("promoted_by"):
        rec["status"] = "active"
        reason = f"promoted deliberately: {rec['promoted_by']}"
    elif independent >= need:
        rec["status"] = "active"
        reason = (f"{independent} independent observation(s), threshold "
                  f"{need}")
    else:
        rec["status"] = "candidate"
        reason = (f"{independent} independent observation(s), threshold "
                  f"{need} - applied to nothing")
    # 5.4: universal is earned only by showing up in two different research
    # types, and it is deliberately hard. The default assumption is that a
    # lesson is about the kind of paper it was learned from.
    seen_types = research_types_in(rec)
    if rec["status"] == "active" and len(seen_types) >= 2:
        rec["scope"]["research_type"] = UNIVERSAL
        reason += f"; universal - seen in {', '.join(seen_types)}"
    # The same argument on the paper-kind axis, and it runs in BOTH
    # directions rather than only widening (review-paper 1). A rule seen on
    # one kind only is scoped to that kind: the default assumption is that a
    # lesson is about the kind of document it was learned from, and the cost
    # of getting that wrong is a review's attribution habit reaching a
    # research paper's results section at layer 3, where nothing would ever
    # say where it came from. Evidence from both kinds earns `any`.
    seen_kinds = paper_kinds_in(rec)
    if len(seen_kinds) == 1:
        if rec["scope"].get("paper_kind") != seen_kinds[0]:
            rec["scope"]["paper_kind"] = seen_kinds[0]
            reason += f"; scoped to {seen_kinds[0]} papers - only kind seen"
    elif len(seen_kinds) >= 2 and rec["scope"].get("paper_kind") != ANY_KIND:
        rec["scope"]["paper_kind"] = ANY_KIND
        reason += f"; every paper kind - seen on {', '.join(seen_kinds)}"
    return {"status": rec["status"], "reason": reason,
            "observations": observations, "independent": independent,
            "waivers": total_waivers, "independent_waivers": indep_waivers,
            "demoted": False, "threshold": need}


# ---------------------------------------------------------------------------
# Layer 0, and what may never be learned
# ---------------------------------------------------------------------------

LAYER0 = [
    "No number appears in the text that is not in "
    "`analysis.md`; no methods value that is not in "
    "`methods_facts.yml`. An unresolved value becomes `**[FLAG: ...]**`, "
    "never a plausible guess.",
    "Every citekey resolves in `references.bib`.",
    "Every `**[FLAG: ...]**` block stays verbatim.",
    "No paragraph is more than one third numeric tokens, whatever the "
    "density budget says (parent 6.1).",
    "Every inline number carries its interpretation (parent 6.1).",
]

# 5.9: the single most likely place for the learning loop to quietly eat a
# layer-0 rule, because a fraction cap looks exactly like a budget. The refusal
# is a test, not a comment.
ABSOLUTE_RE = re.compile(
    r"one[- ]third|one third|1/3|33\s*%|numeric fraction|fraction of numeric|"
    r"half numeric|carries its interpretation|interpretation of every number|"
    r"every inline number carries", re.IGNORECASE)


def refuses_numeric(rule_text: str, value: dict | None) -> str:
    """Why this may not become a `kind: numeric` record, or ""."""
    if ABSOLUTE_RE.search(rule_text or ""):
        return ("that is a layer-0 absolute (parent 6.1), not a budget: the "
                "one-third numeric cap and the interpretation rule are not "
                "overridable and cannot be recorded as numbers")
    if value:
        for key in value:
            if str(key).lower() in ("fraction", "numeric_fraction", "cap"):
                return (f"{key!r} is the layer-0 fraction cap, not a budget; a "
                        f"learned budget may only set inline and clusters")
    return ""


# ---------------------------------------------------------------------------
# Layer 2 - the ten rules, as the drafter's brief
# ---------------------------------------------------------------------------
# [MK] Mensh B, Kording K. Ten simple rules for structuring papers. PLOS
# Comput Biol 13(9):e1005619 (2017). [GS] Gopen GD, Swan JA. The Science of
# Scientific Writing. American Scientist 78:550-558 (1990). [W] Whitesides GM.
# Whitesides' Group: Writing a Paper. Adv Mater 16:1375-1377 (2004).
#
# They are here rather than in the digest because they are GENERATIVE - each
# says what shape to produce, where the digest mostly says what to avoid - and
# because the brief has to work at cold start, before any of 5 has ever run.
# Ten is few enough to hold in mind while writing, which is the point of it.

TEN_RULES = [
    ("Focus the paper on one central contribution.",
     "It is already written down: the hypothesis in `plan/README.md` and the "
     "claim lines in `plan/captions.md`. Every paragraph must be traceable to "
     "that one contribution; a paragraph serving a different one is a "
     "paragraph in the wrong paper. [MK 1]"),
    ("Write for flesh-and-blood human beings.",
     "Assume a reader who does not know this work. Define on first use, "
     "minimize abbreviations, respect working memory - a sentence the reader "
     "must hold three unresolved referents through is too long whatever its "
     "word count. [MK 2]"),
    ("Context - Content - Conclusion, at every scale.",
     "The paper, each section, and EACH PARAGRAPH: open by establishing what "
     "question is addressed, give the content, close on the conclusion to be "
     "remembered. This is the house non-negotiable (paragraphs lead with "
     "their main idea) with the other end of the paragraph managed too. "
     "[MK 3]"),
    ("Optimize logical flow.",
     "No zig-zag - cover each subject in one place. Parallelism - similar "
     "ideas take similar grammar. And repeat the technical term rather than "
     "reaching for a synonym: in science a synonym reads as a different "
     "referent, so a reader who meets `coverage ratio` and then `surface "
     "fraction` reasonably infers two quantities. This overrides the digest's "
     "\"do not reuse the same word\" FOR TECHNICAL TERMS AND DEFINED "
     "QUANTITIES only; for ordinary vocabulary the digest still holds. "
     "[MK 4; N]"),
    ("Tell a complete story in the abstract.",
     "All three of C-C-C: narrow context establishing the gap, what was done "
     "and the key results, then the conclusion that addresses the gap. "
     "[MK 5]"),
    ("Communicate why the paper matters, as nested gaps.",
     "Field-level gap, then subfield gap, then the specific gap this paper "
     "fills, then a compact statement of how it fills it - one paragraph per "
     "level, each more specific than the last, closing on the objectives. "
     "[MK 6]"),
    ("Deliver results as logical statements supported by figures.",
     "A results paragraph OPENS ON WHAT WAS FOUND AND WHAT IT MEANS, then "
     "gives the data and the logic that establish it. Figure titles state "
     "conclusions; legends carry the method. The question-and-answer form - "
     "pose the question, give the data, answer it - is ONE good way to do "
     "this and is offered as a pattern, NOT REQUIRED. Use it where the "
     "paragraph genuinely turns on an open question; a section written "
     "entirely in that form reads as a catechism. [MK 7]"),
    ("Discuss the gap you filled, the limitations, and the relevance.",
     "Key findings without re-reporting them, then this work against the "
     "literature one comparison at a time, then limitations linked to the "
     "literature rather than confessed, then what it enables. [MK 8]"),
    ("Allocate effort where the readers are.",
     "Title, abstract, figures and the outline get the most care, because "
     "they get the most readers. The outline carries one sentence per "
     "intended paragraph before drafting starts, and paragraph-lead "
     "sentences and the abstract get a second look before the section is "
     "handed on - measured, opener length decays 12.6 -> 15.1 -> 20.5 -> "
     "41.0 -> 55.0 words across introduction, methods, results, discussion, "
     "abstract, so the section the most people read is written by the most "
     "tired pass. [MK 9; W]"),
    ("Get feedback; reduce, reuse, recycle.",
     "The one rule a drafting brief cannot satisfy - it is a loop, not "
     "something to write. In this toolkit it is layer 1: the user's own edits "
     "to the previous round, applied and then banked. [MK 10]"),
]

GS_RULE = (
    "Open each sentence on information the reader already has; close it on "
    "the thing you want them to keep.",
    "The beginning of a sentence is its topic position, where the reader "
    "expects linkage; the end is its stress position, where they expect the "
    "payoff. This is what mechanically breaks citation packing: three "
    "unrelated citable facts cannot be packed into one sentence that must "
    "open on ground the reader already holds, so each fact needs its own "
    "opening, so each gets its own sentence. Corollary: ONE "
    "citation-bearing claim per sentence. [GS]")

LADDER_INSTRUCTION = (
    "Where two layers conflict, the lower-numbered layer wins. If a layer-4 "
    "rule yields, do what layers 1-3 require and record the override in "
    "`reports/rN/overrides.md` with a one-line reason. Layer 0 never yields; "
    "if a layer-1 lesson would require breaking it, do not apply the lesson - "
    "report it.")

# 5.10b. Appended where the ladder instruction ends, and only when there is a
# layer 3 to say it about. Note what it does NOT say: it does not say prefer
# the rule. A learned rule still outranks layer 4 and still yields to layers
# 0-2; that placement is unchanged. What changes is that its application
# WITHIN that rank is a decision rather than a substitution - because a flat
# list of imperatives becomes a specification to satisfy, and satisfying it is
# not the same as writing well (3.2).
LAYER3_INSTRUCTION = (
    "Layer 3 is what your past edits taught, not a specification. Each rule "
    "states how often it was observed and how often it has been waived - "
    "weigh that. Apply a rule where it makes the sentence better; waive it "
    "where it does not, and give the reason. A rule learned across four "
    "Methods sections may still be wrong for this one. Waiving a "
    "well-evidenced rule needs a better reason than waiving a thin one, but "
    "neither is forbidden. Record both lists in "
    "`reports/rN/learned_rules.md`: every rule you applied and where, and "
    "every rule you waived and why. A section with several rules in scope, "
    "all applied and none waived, is the signature of a checklist rather "
    "than a judgment.")


# ---------------------------------------------------------------------------
# observe, promote, retire, resolve
# ---------------------------------------------------------------------------

def _today() -> str:
    return datetime.date.today().isoformat()


def _norm_rule(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", str(text).lower()).split())


def observe(records: list[dict], rule: str, kind: str, evidence: dict,
            scope: dict, value: dict | None = None,
            rule_id: str | None = None) -> dict:
    """Record one observation, and re-evaluate the rule's status.

    A rule already in the registry with the same text gains the evidence; the
    same observation twice is a no-op. Nothing here promotes anything on its
    own - evaluate_status() decides, and it decides on counted evidence only.
    """
    if kind not in KINDS:
        raise RegistryError(f"kind {kind!r} is not one of {', '.join(KINDS)}")
    if kind == "numeric":
        why = refuses_numeric(rule, value)
        if why:
            raise RegistryError(f"refusing to record a numeric budget: {why}")

    rec = None
    if rule_id:
        rec = next((r for r in records if r.get("id") == rule_id), None)
        if rec is None:
            raise RegistryError(f"no rule {rule_id} in the registry")
    if rec is None:
        for cand in records:
            if (_norm_rule(cand.get("rule", "")) == _norm_rule(rule)
                    and cand.get("kind") == kind
                    and (cand.get("scope") or {}).get("section")
                    == scope.get("section")
                    and cand.get("status") not in ("retired", "superseded")):
                rec = cand
                break
    if rec is None:
        rec = {"id": next_id(records), "rule": rule, "kind": kind,
               "scope": dict(scope), "status": "candidate", "evidence": [],
               "supersedes": [], "conflicts_with": [], "learned": _today()}
        records.append(rec)
    if value:
        rec["value"] = value
    if kind == "numeric" and not rec.get("value"):
        # The value is asked for once, on the record that needs one; a second
        # observation of a budget already in the registry does not have to
        # restate it.
        raise RegistryError(
            "a `kind: numeric` record needs a value, e.g. "
            "--value inline=8,clusters=6,basis=per100")
    rec["updated"] = _today()

    keys = {observation_key(e) for e in rec["evidence"]}
    added = observation_key(evidence) not in keys
    if added:
        rec["evidence"].append(evidence)
        # 5.10a. When the rule last gained evidence is half of what makes it
        # weighable in the brief; the other half is how often it was waived.
        rec["reinforced"] = _today()

    if kind == "numeric":
        rec["value"] = learned_value(rec, value or {})
    verdict = evaluate_status(rec)
    verdict["id"] = rec["id"]
    verdict["added"] = added
    verdict["record"] = rec
    return verdict


def percentile(values: list[float], pct: float = 75.0) -> float:
    """The 75th percentile, rounded to 0.5 - not the maximum.

    7: every override is evidence for a looser budget, and an override is only
    ever logged when the drafter wanted MORE numbers, so the evidence arriving
    at a numeric record is systematically one-sided even though the mechanism
    is symmetric. A percentile rather than the maximum is what slows that
    ratchet; it is not a guarantee, which is why `budgets` prints the drift.
    """
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return round(s[0] * 2) / 2
    pos = (pct / 100.0) * (len(s) - 1)
    low = int(pos)
    high = min(low + 1, len(s) - 1)
    val = s[low] + (s[high] - s[low]) * (pos - low)
    return round(val * 2) / 2


def learned_value(rec: dict, fallback: dict) -> dict:
    """A numeric record's value, from its own evidence."""
    inline = [float(e["count"]) for e in counted_evidence(rec)
              if isinstance(e.get("count"), (int, float))]
    inline += [float(e["count_after"]) for e in counted_evidence(rec)
               if isinstance(e.get("count_after"), (int, float))]
    clusters = [float(e["clusters"]) for e in counted_evidence(rec)
                if isinstance(e.get("clusters"), (int, float))]
    out = dict(rec.get("value") or fallback)
    if inline:
        out["inline"] = percentile(inline)
    if clusters:
        out["clusters"] = percentile(clusters)
    out.setdefault("basis", "per100")
    return out


def promote(records: list[dict], rule_id: str, because: str) -> dict:
    rec = next((r for r in records if r.get("id") == rule_id), None)
    if rec is None:
        raise RegistryError(f"no rule {rule_id} in the registry")
    if not because:
        raise RegistryError("promoting past the threshold needs a reason; "
                            "an unexplained promotion is the thing nobody can "
                            "audit in six months")
    rec["promoted_by"] = because
    rec["updated"] = _today()
    # A rule demoted by waivers is one `promote --because` away from coming
    # back (5.10d, 7). The waivers themselves are kept - they are evidence
    # too, and deleting them would hide why it went away the first time.
    rec.pop("demoted_because", None)
    return evaluate_status(rec)


def waive(records: list[dict], rule_id: str, because: str,
          project: str = "", round_id: str = "", section: str = "") -> dict:
    """Record one waiver against a rule (5.10d).

    The reason is required and is stored verbatim. It has to be: the mechanism
    cannot distinguish "this rule makes this sentence worse" from "applying
    this rule is work", and both arrive as a waiver with a plausible reason.
    No automatic check is possible - judging whether the reason is honest means
    reading the sentence, which is the user's job. What the tool can do is keep
    the reason where the user will read it, and make the demotion loud.
    """
    rec = next((r for r in records if r.get("id") == rule_id), None)
    if rec is None:
        raise RegistryError(f"no rule {rule_id} in the registry")
    if not str(because or "").strip():
        raise RegistryError(
            "waiving a rule needs a reason; an unexplained waiver is the "
            "thing nobody can audit in six months, and three of them demote "
            "the rule")
    entry = {"because": str(because).strip(), "date": _today()}
    for key, val in (("project", project), ("round", round_id),
                     ("section", section)):
        if val:
            entry[key] = val
    was = rec.get("status")
    rec.setdefault("waived", []).append(entry)
    rec["updated"] = _today()
    res = evaluate_status(rec)
    res["id"] = rule_id
    res["was"] = was
    res["recorded"] = entry
    # 5.10d. A candidate waived before it ever promotes is unlikely to be a
    # rule: recorded, but there is nothing to demote.
    if was == "candidate" and res["status"] == "candidate":
        res["reason"] = ("recorded against a candidate - a candidate is "
                         "applied to nothing either way")
    return res


def retire(records: list[dict], rule_id: str, because: str) -> dict:
    rec = next((r for r in records if r.get("id") == rule_id), None)
    if rec is None:
        raise RegistryError(f"no rule {rule_id} in the registry")
    if not because:
        raise RegistryError("retiring a rule needs a reason")
    rec["status"] = "retired"
    rec["retired_because"] = because
    rec["updated"] = _today()
    # Retained rather than deleted: the evidence is still real, and a deleted
    # record cannot be argued with.
    return {"id": rule_id, "status": "retired", "reason": because}


def resolve_conflict(records: list[dict], new_id: str, other_id: str,
                     outcome: str, note: str, types: list[dict]) -> dict:
    """5.6's three outcomes, written down.

    Which outcome applies is judgment - does the contradiction have a reason a
    scientist in each field would recognize? - so the module decides and this
    records it. What is mechanical is that a demotion carries its note, that a
    superseded rule is retained, and that a `conflict` is never auto-resolved.
    """
    new = next((r for r in records if r.get("id") == new_id), None)
    old = next((r for r in records if r.get("id") == other_id), None)
    if new is None or old is None:
        raise RegistryError(f"both rules have to exist: {new_id}, {other_id}")
    if outcome not in ("demote", "supersede", "conflict"):
        raise RegistryError("outcome is demote, supersede or conflict")
    if not note:
        raise RegistryError(
            "every outcome carries its reason. The reason a rule narrowed is "
            "the most useful thing in the registry and the first thing lost "
            "if it is not written at the moment of narrowing")

    if outcome == "demote":
        seen = research_types_in(old)
        if len(seen) != 1:
            raise RegistryError(
                f"{other_id} has evidence from {seen or ['no type']}; a "
                f"demotion has to name the type the evidence came from, so "
                f"record the research_type on its evidence first")
        was = (old.get("scope") or {}).get("research_type")
        old["demoted_from"] = was
        old["scope"]["research_type"] = seen[0]
        old["note"] = note
        old["status"] = "active"
        new.setdefault("conflicts_with", [])
        if other_id not in new["conflicts_with"]:
            new["conflicts_with"].append(other_id)
        old.setdefault("conflicts_with", [])
        if new_id not in old["conflicts_with"]:
            old["conflicts_with"].append(new_id)
    elif outcome == "supersede":
        old["status"] = "superseded"
        old["superseded_by"] = new_id
        old["note"] = note
        new.setdefault("supersedes", [])
        if other_id not in new["supersedes"]:
            new["supersedes"].append(other_id)
    else:
        for rec, other in ((new, other_id), (old, new_id)):
            rec["status"] = "conflict"
            rec["note"] = note
            rec.setdefault("conflicts_with", [])
            if other not in rec["conflicts_with"]:
                rec["conflicts_with"].append(other)

    for rec in (new, old):
        rec["updated"] = _today()
    still = scopes_overlap(new, old, types)
    return {"outcome": outcome, "new": new_id, "other": other_id,
            "note": note, "scopes_still_overlap": still,
            "statuses": {new_id: new["status"], other_id: old["status"]}}


def conflicts(records: list[dict], rule_id: str | None,
              types: list[dict]) -> dict:
    """Every active rule whose scope overlaps - mechanical, per 5.6.

    Whether two overlapping rules actually contradict is judgment and stays
    with the module; what this does is make sure the judgment is never asked
    about a pair nobody noticed.
    """
    active = [r for r in records if r.get("status") == "active"]
    pairs: list[dict] = []
    targets = [r for r in records if r.get("id") == rule_id] if rule_id \
        else active
    if rule_id and not targets:
        raise RegistryError(f"no rule {rule_id} in the registry")
    for rec in targets:
        for other in active:
            if other["id"] == rec["id"]:
                continue
            if not scopes_overlap(rec, other, types):
                continue
            if any(p["a"] == other["id"] and p["b"] == rec["id"]
                   for p in pairs):
                continue
            pairs.append({
                "a": rec["id"], "b": other["id"],
                "a_rule": rec.get("rule"), "b_rule": other.get("rule"),
                "a_scope": rec.get("scope"), "b_scope": other.get("scope"),
                "already_recorded": other["id"] in (
                    rec.get("conflicts_with") or [])})
    return {"rule": rule_id, "overlaps": pairs,
            "counts": {"overlaps": len(pairs)}}


def merge(records: list[dict], other: list[dict]) -> dict:
    """Union by id - the shared-folder case (5.8).

    Same id and same evidence is a no-op; same id and different evidence
    merges the evidence lists and re-evaluates the threshold; a genuinely
    different rule under an id already in use is RENUMBERED rather than
    dropped, and reported. Nothing here is a git operation: do not make a
    shared folder a git repository to solve this.
    """
    by_id = {r["id"]: r for r in records}
    added, merged, renumbered, conflicted = [], [], [], []
    for incoming in other:
        rid = incoming.get("id")
        mine = by_id.get(rid)
        if mine is None:
            records.append(incoming)
            by_id[rid] = incoming
            evaluate_status(incoming)
            added.append(rid)
            continue
        if _norm_rule(mine.get("rule", "")) != _norm_rule(
                incoming.get("rule", "")):
            fresh = dict(incoming)
            fresh["id"] = next_id(records)
            fresh["note"] = (f"renumbered from {rid} on merge: that id is a "
                             f"different rule here")
            records.append(fresh)
            by_id[fresh["id"]] = fresh
            evaluate_status(fresh)
            renumbered.append({"from": rid, "to": fresh["id"]})
            continue
        before = {observation_key(e) for e in mine.get("evidence", [])}
        for ev in incoming.get("evidence", []):
            if observation_key(ev) not in before:
                mine.setdefault("evidence", []).append(ev)
        # Both halves need their own parentheses. Without them Python reads
        # this as one chained comparison - `x in T != (y in T)` - which is
        # `(x in T) and (T != (y in T))`; the second half compares a tuple to
        # a bool, is always True, and the condition collapses to "mine is
        # retired". That reported a conflict when both sides agreed a rule
        # was retired, and reported none when only the incoming side had
        # retired it.
        if ((mine.get("status") in ("retired", "superseded"))
                != (incoming.get("status") in ("retired", "superseded"))):
            conflicted.append(rid)
        evaluate_status(mine)
        merged.append(rid)
    return {"added": added, "merged": merged, "renumbered": renumbered,
            "conflicted": conflicted,
            "counts": {"added": len(added), "merged": len(merged),
                       "renumbered": len(renumbered),
                       "conflicted": len(conflicted)}}


# ---------------------------------------------------------------------------
# budgets - 5.9
# ---------------------------------------------------------------------------

def learned_budgets(records: list[dict], research_type: str,
                    types: list[dict]) -> dict:
    """Steps 2 and 3 of the resolution order, per section.

    The section's own research type beats a parent's, and the id is carried
    through so a finding can be traced to the reason its threshold was what it
    was. Without that a learned budget is unauditable in exactly the way
    `brief --explain` exists to prevent.
    """
    order = type_ancestors(research_type, types)
    out: dict = {}
    for rec in records:
        if rec.get("kind") != "numeric" or rec.get("status") != "active":
            continue
        scope = rec.get("scope") or {}
        rtype = scope.get("research_type") or UNIVERSAL
        section = scope.get("section") or ANY_SECTION
        if rtype not in order or section == ANY_SECTION:
            continue
        rank = order.index(rtype)
        current = out.get(section)
        if current and current["rank"] <= rank:
            continue
        value = dict(rec.get("value") or {})
        source = (f"learned:{rec['id']}" if rank == 0
                  else f"learned:parent:{rec['id']}")
        out[section] = {"rank": rank, "id": rec["id"], "source": source,
                        "inline": value.get("inline"),
                        "clusters": value.get("clusters"),
                        "basis": value.get("basis") or "per100"}
    for value in out.values():
        value.pop("rank", None)
    return out


def resolved_budgets(rules_path: str | None, research_type: str) -> dict:
    """What `prose.py density` calls for steps 2 and 3."""
    records = read_registry(rules_path)
    return learned_budgets(records, research_type, read_types())


def budgets(records: list[dict], research_type: str, types: list[dict],
            config_path: str | None = None) -> dict:
    """What each section's budget resolves to, and why (5.9).

    Prints the learned value beside the built-in default and the drift between
    them, because the ratchet this mechanism can develop is invisible in any
    single finding: a budget that has moved more than 2x wants the user's eye
    rather than another observation.
    """
    cfg = prose.load_density_config(config_path)
    learned = learned_budgets(records, research_type, types)
    rows: list[dict] = []
    for section in prose.SECTION_FILES:
        problems: list[str] = []
        resolved = prose.resolve_density_budget(
            section, cfg["level"], cfg["sections"], learned, problems)
        default = prose.resolve_density_budget(section, None, None, None, None)
        drift = None
        if (resolved["inline"] and default["inline"]
                and resolved["basis"] == default["basis"]):
            drift = round(resolved["inline"] / default["inline"], 2)
        # A section whose default was unenforced and which now has a number is
        # a bigger change than any drift ratio can express: the budget did not
        # move, it came into existence.
        newly_enforced = (default["inline"] is None
                          and resolved["inline"] is not None)
        rows.append({
            "section": section, "inline": resolved["inline"],
            "clusters": resolved["clusters"], "basis": resolved["basis"],
            "level": resolved["level"], "source": resolved["source"],
            "default_inline": default["inline"],
            "default_basis": default["basis"],
            "drift": drift, "newly_enforced": newly_enforced,
            "wants_a_look": bool(newly_enforced
                                 or (drift and (drift >= 2.0
                                                or drift <= 0.5))),
            "problems": problems})
    return {"research_type": research_type, "config": config_path,
            "sections": rows,
            "counts": {"sections": len(rows),
                       "learned": sum(1 for r in rows
                                      if str(r["source"]).startswith(
                                          "learned")),
                       "wants_a_look": sum(1 for r in rows
                                           if r["wants_a_look"])}}


# ---------------------------------------------------------------------------
# extract - 5.1
# ---------------------------------------------------------------------------

def _norm_para(text: str) -> str:
    return " ".join(text.split())


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm_para(a), _norm_para(b)).ratio()


def _numbers_of(text: str) -> list[str]:
    counts = prose.count_numbers(text)
    out = [i["value"] for i in counts["inline"]]
    for cluster in counts["clusters"]:
        out.extend(cluster["values"])
    return sorted(_norm_number(v) for v in out)


def _magnitudes_of(text: str) -> list[str]:
    """Spelled-out quantities, for the seventh invariant.

    `prose.magnitude_tokens` owns the list; this is the multiset view of it,
    and it sits beside `_numbers_of` rather than in an invariant of its own
    (specs/flow-and-conclusion-2026-09-19.md 5.4).
    """
    return sorted(prose.magnitude_tokens(text))


def _norm_number(value: str) -> str:
    """Presentation is normalized; the value is not.

    `0.42` and `0.420` are the same token. `42%` and `0.42` are NOT, because
    converting units is a claim about what was measured (3.1).
    """
    text = value.strip().replace(",", "")
    pct = text.endswith("%")
    text = text.rstrip("%").strip()
    lead = ""
    while text[:1] in "<>≤≥~±+-":
        lead += text[0]
        text = text[1:]
    try:
        num = float(text)
        text = f"{num:g}"
    except ValueError:
        pass
    return f"{lead}{text}{'%' if pct else ''}"


def _citekeys_of(text: str) -> list[str]:
    return sorted(prose.CITEKEY_RE.findall(re.sub(r"`[^`]*`", " ", text)))


def _flags_of(text: str) -> list[str]:
    return sorted(m.group(0) for m in prose.FLAG_RE.finditer(text))


def _multiset_delta(before: list[str], after: list[str]) -> dict:
    """A multiset difference, so moving a token between sentences is allowed
    and duplicating or dropping one is not (3.1)."""
    cb, ca = collections.Counter(before), collections.Counter(after)
    removed = sorted((cb - ca).elements())
    added = sorted((ca - cb).elements())
    return {"removed": removed, "added": added,
            "changed": bool(removed or added)}


def align(before_text: str, after_text: str) -> dict:
    """Aligned before/after pairs at paragraph and sentence granularity.

    Same problem `docx_edits.py` solves for a coauthor's tracked changes, with
    the user as the coauthor: what the reader wants is not a character diff but
    "this sentence became that sentence". Paragraphs first, because a move or a
    merge is invisible in a sentence-level diff; sentences inside a paragraph
    that survived, because that is where a style lesson lives.
    """
    before = prose.paragraphs(before_text)
    after = prose.paragraphs(after_text)
    b_norm = [_norm_para(p["text"]) for p in before]
    a_norm = [_norm_para(p["text"]) for p in after]
    sm = difflib.SequenceMatcher(None, b_norm, a_norm)

    edits: list[dict] = []
    unchanged = 0
    deleted: list[dict] = []
    inserted: list[dict] = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            unchanged += i2 - i1
            for k in range(i2 - i1):
                edits.extend(_sentence_edits(before[i1 + k], after[j1 + k]))
            continue
        if tag == "replace":
            # Pair them up in order; a length mismatch is a split or a merge
            # and is reported as one.
            b_slice = before[i1:i2]
            a_slice = after[j1:j2]
            pairs = _pair_paragraphs(b_slice, a_slice)
            for b_item, a_item in pairs:
                if b_item and a_item:
                    edits.append(_paragraph_edit("replace", b_item, a_item))
                    edits.extend(_sentence_edits(b_item, a_item))
                elif b_item:
                    deleted.append(b_item)
                elif a_item:
                    inserted.append(a_item)
            continue
        if tag == "delete":
            deleted.extend(before[i1:i2])
        elif tag == "insert":
            inserted.extend(after[j1:j2])

    # A deleted paragraph that reappears elsewhere is a MOVE, not a deletion
    # and an insertion - and the difference matters, because a move is a
    # structural lesson and a deletion is a content event.
    moves: list[dict] = []
    for b_item in list(deleted):
        match = max(inserted, key=lambda a: _ratio(b_item["text"], a["text"]),
                    default=None)
        if match and _ratio(b_item["text"], match["text"]) >= 0.75:
            moves.append({"kind": "move", "before_n": b_item["n"],
                          "after_n": match["n"],
                          "before": b_item["text"], "after": match["text"],
                          "similarity": round(
                              _ratio(b_item["text"], match["text"]), 3)})
            deleted.remove(b_item)
            inserted.remove(match)
    for move in moves:
        edits.append({"granularity": "paragraph", "op": "move",
                      "before_n": move["before_n"],
                      "after_n": move["after_n"],
                      "before": move["before"], "after": move["after"],
                      **_edit_evidence(move["before"], move["after"])})
    for item in deleted:
        edits.append({"granularity": "paragraph", "op": "delete",
                      "before_n": item["n"], "after_n": None,
                      "before": item["text"], "after": "",
                      **_edit_evidence(item["text"], "")})
    for item in inserted:
        edits.append({"granularity": "paragraph", "op": "insert",
                      "before_n": None, "after_n": item["n"],
                      "before": "", "after": item["text"],
                      **_edit_evidence("", item["text"])})

    return {"paragraphs_before": len(before), "paragraphs_after": len(after),
            "paragraphs_unchanged": unchanged, "edits": edits,
            "counts": {"edits": len(edits),
                       "paragraph_edits": sum(1 for e in edits
                                              if e["granularity"]
                                              == "paragraph"),
                       "sentence_edits": sum(1 for e in edits
                                             if e["granularity"]
                                             == "sentence")}}


def _pair_paragraphs(before: list[dict], after: list[dict]) -> list[tuple]:
    """Best-effort pairing inside a replaced run, by similarity."""
    pairs: list[tuple] = []
    remaining = list(after)
    for b_item in before:
        best = max(remaining, key=lambda a: _ratio(b_item["text"], a["text"]),
                   default=None)
        if best is not None and _ratio(b_item["text"], best["text"]) >= 0.4:
            pairs.append((b_item, best))
            remaining.remove(best)
        else:
            pairs.append((b_item, None))
    for a_item in remaining:
        pairs.append((None, a_item))
    return pairs


def _paragraph_edit(op: str, b_item: dict, a_item: dict) -> dict:
    b_sents = prose._sentences(b_item["text"])
    a_sents = prose._sentences(a_item["text"])
    shape = op
    if len(a_sents) > len(b_sents):
        shape = "split_sentences"
    elif len(a_sents) < len(b_sents):
        shape = "merged_sentences"
    return {"granularity": "paragraph", "op": op, "shape": shape,
            "before_n": b_item["n"], "after_n": a_item["n"],
            "before": b_item["text"], "after": a_item["text"],
            "sentences_before": len(b_sents), "sentences_after": len(a_sents),
            **_edit_evidence(b_item["text"], a_item["text"])}


def _sentence_edits(b_item: dict, a_item: dict) -> list[dict]:
    b_sents = prose._sentences(b_item["text"])
    a_sents = prose._sentences(a_item["text"])
    sm = difflib.SequenceMatcher(None, [_norm_para(s) for s in b_sents],
                                 [_norm_para(s) for s in a_sents])
    out: list[dict] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if (i1, j1) == (0, 0) and (i2, j2) == (len(b_sents), len(a_sents)):
            # The whole paragraph was rewritten, and that is already reported
            # at paragraph granularity. Emitting it twice would count one
            # lesson as two pieces of evidence.
            continue
        b_text = " ".join(b_sents[i1:i2])
        a_text = " ".join(a_sents[j1:j2])
        op = {"replace": "replace", "delete": "delete",
              "insert": "insert"}[tag]
        rec = {"granularity": "sentence", "op": op,
               "before_n": b_item["n"], "after_n": a_item["n"],
               "before": b_text, "after": a_text,
               "sentences_before": i2 - i1, "sentences_after": j2 - j1,
               **_edit_evidence(b_text, a_text)}
        if i2 - i1 == 1 and j2 - j1 > 1:
            rec["shape"] = "split_sentences"
        elif i2 - i1 > 1 and j2 - j1 == 1:
            rec["shape"] = "merged_sentences"
        elif op == "replace" and i2 - i1 == 1 and j2 - j1 == 1:
            rec["shape"] = "rewritten"
        else:
            rec["shape"] = op
        out.append(rec)
    return out


def _edit_evidence(before: str, after: str) -> dict:
    """The three mechanical questions that decide the three no-rule classes.

    5.2: whether the numeric multiset changed, whether the citekey multiset
    changed, whether a whole paragraph came or went. Those are arithmetic and
    they settle `factual`, `citation` and `content` on their own. Anything that
    changed none of them is the module's judgment to classify.
    """
    numbers = _multiset_delta(_numbers_of(before), _numbers_of(after))
    keys = _multiset_delta(_citekeys_of(before), _citekeys_of(after))
    flags = _multiset_delta(_flags_of(before), _flags_of(after))
    b_words = len(prose._words(before))
    a_words = len(prose._words(after))
    return {
        "numbers": numbers, "citekeys": keys, "flags": flags,
        "words_before": b_words, "words_after": a_words,
        "words_delta": a_words - b_words,
        "whitespace_only": _norm_para(before) == _norm_para(after)
                           and before != after,
        "case_only": (_norm_para(before).lower()
                      == _norm_para(after).lower()
                      and _norm_para(before) != _norm_para(after)),
        "hedges_delta": (prose._count_words(after, prose.HEDGE_WORDS)
                         - prose._count_words(before, prose.HEDGE_WORDS)),
        "passive_delta": (len(prose.PASSIVE_RE.findall(after))
                          - len(prose.PASSIVE_RE.findall(before))),
    }


# ---------------------------------------------------------------------------
# Pass attribution, and the revert loop - prose 11.5
# ---------------------------------------------------------------------------
#
# 11.3 made one rewrite pass automatic, and 11.1 added a second that can be.
# Every automatic pass is a new way for a user's edit to be undone, so the
# guarantee has to work in the other direction too: THE USER'S EDITS OUTRANK
# EVERYTHING, AND EVERYTHING LEARNS FROM THEM.
#
# The extract step already compares the user's returned text against the
# engine's snapshot. What it could not see is WHICH of the engine's sentences
# an automatic pass produced - so a user taking an automatic change back out
# looked exactly like a user editing the drafter's prose, and the pass made
# the same unwanted change again next round. That specific failure is what
# makes automatic rewriting unwelcome, and closing it is why 11.3 is safe to
# default to `auto`.
#
# A revert is the strongest evidence this loop can receive. The user did not
# merely decline a suggestion: they saw the change land in their paper and
# took it back out.
#
# Silence stays worthless. A change the user left alone is NOT evidence the
# change was right, per 5.2, and nothing here counts one.

# One Changed entry in reports/rN/prose_report.md. The three fields the
# report has always carried - before, after, and the one-line reason - plus
# two optional ones a pass may name. Tolerant on purpose: this file is
# written by an agent, and a parser that demands all five silently learns
# nothing the first time one is missing, which is the worst of both.
_CHANGED_HEAD_RE = re.compile(r"^##+\s+Changed\b", re.M)
_NEXT_HEAD_RE = re.compile(r"^##+\s+(?!Changed\b)", re.M)
_FIELD_RE = re.compile(
    r"^\s*[-*]\s*(before|after|why|reason|pass|rule|section)\s*:\s*(.+?)\s*$",
    re.I | re.M)


def read_pass_changes(text: str) -> list[dict]:
    """Every before/after an automatic pass recorded, from one prose report.

    Only the `## Changed` region is read. `## Left alone` is the pass saying
    it declined to act, so there is nothing there for a user to revert, and
    reading it would manufacture attributions for text the pass never wrote.
    """
    m = _CHANGED_HEAD_RE.search(text or "")
    if not m:
        return []
    rest = text[m.end():]
    nxt = _NEXT_HEAD_RE.search(rest)
    region = rest[:nxt.start()] if nxt else rest

    out: list[dict] = []
    current: dict = {}
    for fm in _FIELD_RE.finditer(region):
        key = fm.group(1).lower()
        val = fm.group(2).strip().strip("`").strip('"')
        if key == "reason":
            key = "why"
        # A second `before` starts a new entry: the fields repeat per change
        # and there is no other delimiter that survives an agent's prose.
        if key in current:
            if current.get("before") and current.get("after"):
                out.append(current)
            current = {}
        current[key] = val
    if current.get("before") and current.get("after"):
        out.append(current)
    return [c for c in out if c["before"] != c["after"]]


def attribute(edits: list[dict], changes: list[dict],
              near: float = 0.90, back: float = 0.80) -> list[dict]:
    """Mark each user edit with the automatic change it undid, if any.

    An edit is attributed when the text the USER started from is what a pass
    produced - `edit.before` close to `change.after`. It is a REVERT when what
    the user wrote is nearer the pre-pass text than the pass's own, which is
    the whole question: did they put it back.

    The two thresholds are not a bar on quality, they are a match tolerance,
    and they are deliberately asymmetric. `near` is high because attributing
    an edit to the wrong pass records a waiver against a rule that never fired
    there. `back` is lower because a user reverting a sentence rarely restores
    it to the character - they retype it, and the pre-pass wording comes back
    in substance.
    """
    out: list[dict] = []
    for edit in edits:
        rec = dict(edit)
        b = edit.get("before") or ""
        a = edit.get("after") or ""
        best = None
        best_score = 0.0
        for ch in changes:
            score = _ratio(b, ch["after"])
            if score >= near and score > best_score:
                best, best_score = ch, score
        if best is None:
            rec["pass_origin"] = ""
            rec["reverted"] = False
            out.append(rec)
            continue
        to_pre = _ratio(a, best["before"])
        to_pass = _ratio(a, best["after"])
        rec["pass_origin"] = best.get("pass", "an automatic pass")
        rec["pass_rule"] = best.get("rule", "")
        rec["pass_why"] = best.get("why", "")
        rec["pre_pass_text"] = best["before"]
        rec["match"] = round(best_score, 3)
        rec["toward_pre_pass"] = round(to_pre, 3)
        rec["reverted"] = bool(to_pre >= back and to_pre > to_pass)
        out.append(rec)

    # `align` reports one user action at BOTH granularities - the paragraph
    # that changed, and the sentence inside it. `_sentence_edits` already
    # refuses to emit a whole-paragraph rewrite twice, for the reason it
    # states there: emitting it twice would count one lesson as two pieces of
    # evidence. A revert needs the same guard pointing the other way. Without
    # it one user action produces a WAIVER against the rule at sentence
    # granularity and a stylistic rule CANDIDATE at paragraph granularity -
    # the same action read as evidence both for and against.
    reverted_paras = {e.get("after_n") for e in out
                      if e.get("reverted") and e["granularity"] == "sentence"}
    for rec in out:
        if (rec["granularity"] == "paragraph"
                and not rec.get("reverted")
                and rec.get("after_n") in reverted_paras):
            rec["covered_by_revert"] = True
    return out


def revert_evidence(edits: list[dict], project: str = "",
                    round_id: str = "", section: str = "") -> list[dict]:
    """One waiver payload per reverted change, ready for `waive`.

    The reason is the USER'S OWN SENTENCE, not a generated summary. 5.10d
    requires a reason on every waiver precisely because nobody can audit an
    unexplained one in six months, and the sentence the user actually wrote is
    the most honest reason available here.
    """
    out = []
    for e in edits:
        if not e.get("reverted"):
            continue
        out.append({
            "rule": e.get("pass_rule", ""),
            "pass": e.get("pass_origin", ""),
            "project": project, "round": round_id,
            "section": section or e.get("section", ""),
            "because": ("the user reverted this automatic change. They wrote: "
                        "%r" % (e.get("after") or "")[:300]),
            "pass_wrote": (e.get("before") or "")[:300],
            "pre_pass_text": (e.get("pre_pass_text") or "")[:300],
        })
    return out


def classify(edits: list[dict], length_forced: bool = False) -> list[dict]:
    """The mechanical half of 5.2, and only the mechanical half.

    | class      | becomes a rule? |
    | factual    | never - the recorded source is wrong, and that is a data
    |            |   discrepancy on the recorded-value path |
    | content    | never - an outline event: a new line, or a 4.4 waiver |
    | citation   | never - routed to the existing author-edit citation ingest |
    | stylistic  | yes |
    | structural | yes |
    | formatting | at low weight - three observations, not two |

    The first three are decided here. The last three are the module's call, so
    what comes back for them is `undecided` plus the evidence and a proposal -
    never a class this file invented and the module then trusted.
    """
    out: list[dict] = []
    for edit in edits:
        rec = dict(edit)
        rec["length_forced"] = length_forced
        # 11.5, and it is FIRST because it outranks the rest of the ladder.
        # A revert is a statement about a RULE - the user saw what the rule
        # did to their paper and undid it - where every class below is a
        # statement about a sentence. A revert that also changed a number is
        # still a factual edit on the numeric path, so that one case keeps
        # its own route and is named rather than swallowed.
        if edit.get("covered_by_revert") and not edit.get("reverted"):
            # Counted already, at the granularity where the evidence actually
            # lives. Reported rather than dropped: a paragraph edit that
            # vanishes from the list looks like the aligner missed it.
            rec["class"] = "revert"
            rec["route"] = (
                "already counted - a sentence inside this paragraph was a "
                "revert, and the waiver is recorded there. Nothing further "
                "is learned from the paragraph, or one user action would be "
                "evidence both for and against the same rule (11.5)")
            rec["may_inform"] = ""
            out.append(rec)
            continue
        if edit.get("reverted"):
            rec["class"] = "revert"
            rec["route"] = (
                "a WAIVER against the rule that drove the automatic change "
                "(11.5), recorded with the user's own sentence as the "
                "reason. Three independent reverts demote the rule, on the "
                "same counter and the same independence definition as an "
                "explicit waiver")
            rec["may_inform"] = "layer 1 now, and the rule's status"
            if edit["numbers"]["changed"]:
                rec["route"] += ("; it also changed a number, so the numeric "
                                 "half is a data discrepancy and does not "
                                 "become a prose rule either way")
            out.append(rec)
            continue
        if edit["numbers"]["changed"]:
            rec["class"] = "factual"
            rec["route"] = ("a data discrepancy: the recorded source is wrong, "
                            "or the text was. Never a prose rule")
            # 5.9's narrow exception, stated as such: a factual edit may
            # contribute to a `kind: numeric` record and to NOTHING else.
            rec["may_inform"] = ("numeric budget only" if
                                 len(edit["numbers"]["added"])
                                 != len(edit["numbers"]["removed"]) else "")
        elif edit["citekeys"]["changed"]:
            rec["class"] = "citation"
            rec["route"] = "the author-edit citation ingest (parent 9)"
            rec["may_inform"] = ""
        elif edit["op"] in ("insert", "delete") \
                and edit["granularity"] == "paragraph":
            rec["class"] = "content"
            rec["route"] = ("an outline event: a new outline line, or a 4.4 "
                            "waiver candidate")
            rec["may_inform"] = ""
        elif edit.get("whitespace_only") or edit.get("case_only"):
            rec["class"] = "formatting"
            rec["route"] = "induction at low weight - three observations"
            rec["may_inform"] = "stylistic rule at low weight"
        else:
            rec["class"] = "undecided"
            rec["proposed"] = _propose(edit)
            rec["candidates"] = ["stylistic", "structural", "formatting"]
            rec["route"] = ("the module's judgment: nothing mechanical "
                            "decides between these three")
            rec["may_inform"] = "layer 1 and layer 3"
        if length_forced and rec["class"] not in ("factual", "citation"):
            rec["route"] += ("; this round was over the journal's word cap, so "
                             "the edit is APPLIED and not learned from (7)")
        out.append(rec)
    return out


def _propose(edit: dict) -> str:
    if edit.get("shape") in ("split_sentences", "merged_sentences"):
        return "structural"
    if edit["op"] == "move":
        return "structural"
    if edit["granularity"] == "paragraph" and edit["op"] == "replace":
        return "structural"
    return "stylistic"


# ---------------------------------------------------------------------------
# frozen - 4.1
# ---------------------------------------------------------------------------

PROVENANCE_REL = os.path.join(".provenance", "sections.json")


def normalize_for_hash(text: str) -> str:
    """Trailing whitespace, line endings, and runs of three or more blank
    lines. Nothing else - a changed word is a change (4.1)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def section_hash(text: str) -> str:
    return hashlib.sha256(
        normalize_for_hash(text).encode("utf-8")).hexdigest()


def provenance_path(project: str) -> str:
    return os.path.join(prose.source_text_dir(project), PROVENANCE_REL)


def read_provenance(project: str) -> dict:
    path = provenance_path(project)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise RegistryError(
            f"{path} cannot be read ({exc}); every section would look fresh "
            f"and the engine would redraft prose the user had taken over")
    return data if isinstance(data, dict) else {}


def write_provenance(project: str, data: dict) -> str:
    path = provenance_path(project)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


def frozen(project: str) -> dict:
    """Which sections the user has taken over, inferred by hash (4.1).

    Four states, and the fourth is the one a hash test gets wrong: a section
    whose file is GONE is the existing "empty source_text/ to force a
    redraft" gesture, and it has to keep working.

    Section granularity, never paragraph. A paragraph-level hash would let the
    engine keep writing around the user's edits, which is the behaviour that
    makes a tool untrustworthy.
    """
    data = read_provenance(project)
    st = prose.source_text_dir(project)
    rows: list[dict] = []
    for section in prose.SECTION_FILES:
        path = os.path.join(st, f"{section}.md")
        entry = data.get(section) or {}
        text = _read(path) if os.path.isfile(path) else None
        if text is None:
            state = "wiped" if entry else "fresh"
        elif not text.strip():
            state = "fresh"
        elif not entry.get("sha256"):
            state = "fresh"
        elif entry["sha256"] == section_hash(text):
            state = "engine-owned"
        else:
            state = "frozen"
        rows.append({"section": section, "state": state,
                     "path": path,
                     "written_by": entry.get("written_by"),
                     "revised_by": entry.get("revised_by"),
                     "round": entry.get("round"),
                     "written": entry.get("written"),
                     "hash": entry.get("sha256"),
                     "hash_now": section_hash(text) if text else None})
    return {"project": project, "provenance": provenance_path(project),
            "sections": rows,
            "frozen": [r["section"] for r in rows if r["state"] == "frozen"],
            "counts": {"sections": len(rows),
                       "frozen": sum(1 for r in rows
                                     if r["state"] == "frozen")}}


def record_provenance(project: str, sections: list[str], by: str,
                      round_id: str) -> dict:
    """Write the hash of record after a module wrote or revised a section.

    1c updates it, and that is the regression this design is most likely to
    have: without it a revised section is frozen against itself next round and
    the engine stops touching prose it wrote.
    """
    data = read_provenance(project)
    st = prose.source_text_dir(project)
    written: list[str] = []
    for section in sections:
        path = os.path.join(st, f"{section}.md")
        if not os.path.isfile(path):
            continue
        entry = dict(data.get(section) or {})
        entry["sha256"] = section_hash(_read(path))
        entry["round"] = round_id or entry.get("round")
        entry["written"] = datetime.datetime.now(
            datetime.timezone.utc).replace(microsecond=0).isoformat().replace(
                "+00:00", "Z")
        # Every spelling of 1c is a REVISION, not a first writing.
        # `revise-prose --from-comprehension` and `--from-quality` are the
        # same module with one report in front of it, and recording either as
        # `written_by` would say the engine composed a section that somebody
        # else's draft is underneath.
        if (by or "").split(" ")[0] == "revise-prose":
            entry["revised_by"] = by
            entry.setdefault("written_by", "draft-sections")
        else:
            entry["written_by"] = by
        data[section] = entry
        written.append(section)
    path = write_provenance(project, data)
    return {"provenance": path, "recorded": written,
            "counts": {"recorded": len(written)}}


# ---------------------------------------------------------------------------
# brief - 5.7
# ---------------------------------------------------------------------------

def _digest_sections(text: str) -> dict:
    """The digest split by stage (1). Absent staging is reported, not faked."""
    out = {"drafting": "", "revision": "", "staged": False}
    if not text.strip():
        return out
    parts = re.split(r"^##\s+At\s+(drafting|revision)\s*$", text,
                     flags=re.MULTILINE | re.IGNORECASE)
    if len(parts) < 3:
        return out
    out["staged"] = True
    for i in range(1, len(parts) - 1, 2):
        stage = parts[i].lower()
        body = parts[i + 1]
        # Stop at the next top-level heading that is not a stage heading. The
        # source attribution and the "what this digest does not override"
        # closing sit outside both stages and are read by both (1); without
        # this cut the closing lands in whichever stage happens to be last,
        # and the other stage never sees it.
        cut = re.search(r"^##\s+(?!At\s+(?:drafting|revision)\s*$)",
                        body, flags=re.MULTILINE | re.IGNORECASE)
        if cut:
            body = body[:cut.start()]
        out[stage] = body.strip().rstrip("-").strip()
    return out


def _layer3_entry(rec: dict) -> dict:
    """One layer-3 rule with the evidence that makes it weighable (5.10a)."""
    total_waivers, indep_waivers = waiver_counts(rec)
    return {"id": rec.get("id"), "rule": rec.get("rule"),
            "kind": rec.get("kind"), "status": rec.get("status"),
            "observations": int(rec.get("observations") or 0),
            "independent": int(rec.get("independent") or 0),
            "projects": len(projects_in(rec)),
            "reinforced": last_reinforced(rec),
            "waivers": total_waivers,
            "independent_waivers": indep_waivers,
            # A rule waived as often as it was observed wants the user's eye
            # whether or not it has reached the demotion threshold (5.10d).
            "thin": total_waivers >= max(1, int(rec.get("observations") or 0))}


def _render_layer3(entry: dict) -> str:
    bits = [f"{entry['observations']} observation"
            f"{'' if entry['observations'] == 1 else 's'}",
            f"{entry['projects']} project"
            f"{'' if entry['projects'] == 1 else 's'}"]
    if entry.get("reinforced"):
        bits.append(f"reinforced {entry['reinforced']}")
    bits.append(f"{entry['waivers']} waiver"
                f"{'' if entry['waivers'] == 1 else 's'}")
    line = " \u00b7 ".join(bits)
    return line + (" (!)" if entry.get("thin") else "")


def brief(records: list[dict], research_type: str | None, section: str | None,
          types: list[dict], layer1_path: str | None = None,
          round_id: str = "", digest_path: str | None = None,
          config_path: str | None = None,
          paper_kind: str | None = None) -> dict:
    """The whole ladder as one ordered document (5.7).

    Layers 1 and 3 are always optional: an empty registry, no research type and
    no previous round is the first project's r1, and it has to work without any
    of 5 having ever run. An absent layer is OMITTED rather than emitted as an
    empty heading - a heading with nothing under it reads as a rule set the
    drafter failed to find.
    """
    layer1 = _read(layer1_path) if layer1_path else ""
    digest = _digest_sections(_read(digest_path or DIGEST_FILE))
    learned = [r for r in records
               if r.get("status") == "active" and r.get("kind") != "numeric"
               and scope_applies(r, research_type, section, types,
                                 paper_kind)]
    candidates = [r for r in records
                  if r.get("status") == "candidate"
                  and scope_applies(r, research_type, section, types,
                                    paper_kind)]
    budget_rows = budgets(records, research_type or UNIVERSAL, types,
                          config_path)["sections"]
    budget = next((r for r in budget_rows if r["section"] == section), None)

    layers: list[dict] = [{
        "layer": 0, "title": "Invariants (never overridable)",
        "items": list(LAYER0)}]
    if layer1.strip():
        layers.append({"layer": 1,
                       "title": f"From your edits{' to ' + round_id if round_id else ''}",
                       "body": layer1.strip()})
    layers.append({
        "layer": 2, "title": "The ten rules",
        "items": [f"**{i}. {head}** {body}"
                  for i, (head, body) in enumerate(TEN_RULES, 1)]
        + [f"**11. {GS_RULE[0]}** {GS_RULE[1]}"]})
    if learned:
        # 5.10a. A rule with three waivers and nothing reinforcing it in six
        # months is a different object from one with three observations across
        # two projects, and the drafter cannot weigh what it cannot see.
        layers.append({
            "layer": 3,
            "title": f"Learned for {research_type or UNIVERSAL}",
            "items": [f"{r['id']}  {r['rule']}" for r in learned],
            "rules": [_layer3_entry(r) for r in learned],
            "instruction": LAYER3_INSTRUCTION})
    layer4: list[str] = []
    if budget:
        shape = ("unenforced" if budget["inline"] is None
                 else f"{budget['inline']:g} inline / "
                      f"{budget['clusters']:g} clusters"
                      f" ({budget['basis']})")
        layer4.append(f"Number density for {section}: {shape} "
                      f"[{budget['source']}]")
    if digest["revision"]:
        layer4.append("The digest's `## At revision` prohibitions apply when "
                      "the prose exists, not while it is being written; "
                      "module 1c owns them.")
    elif not digest["staged"]:
        layer4.append("`writing_rules.md` is not staged into `## At drafting` "
                      "and `## At revision` yet, so its editing rules are "
                      "still in the drafting brief. Treat every prohibition "
                      "in it as layer 4.")
    if digest["drafting"]:
        layer4.append("The digest's `## At drafting` rules stand behind these "
                      "ten; where they disagree, these win.")
    if layer4:
        layers.append({"layer": 4,
                       "title": "Presentation and prohibitions "
                                "(yield to layers 1-3; log overrides)",
                       "items": layer4})

    return {"research_type": research_type, "paper_kind": paper_kind,
            "section": section,
            "round": round_id, "layers": layers,
            "candidates": [dict(_layer3_entry(r),
                                demoted_because=r.get("demoted_because") or [])
                           for r in candidates],
            "digest_staged": digest["staged"],
            "instruction": LADDER_INSTRUCTION,
            "counts": {"layers": len(layers), "learned": len(learned),
                       "candidates": len(candidates)}}


def render_brief(res: dict) -> str:
    head = " - ".join(x for x in ("# Drafting brief", res.get("section"),
                                  res.get("research_type"), res.get("round"))
                      if x)
    out = [head, ""]
    for layer in res["layers"]:
        out.append(f"## Layer {layer['layer']} - {layer['title']}")
        out.append("")
        if layer.get("body"):
            out.append(layer["body"])
        if layer.get("rules"):
            for entry in layer["rules"]:
                out.append(f"- {entry['id']}  {entry['rule']}")
                out.append(f"  {_render_layer3(entry)}")
        else:
            for item in layer.get("items", []):
                out.append(f"- {item}")
        out.append("")
        if layer.get("instruction"):
            out += ["> " + layer["instruction"], ""]
    if res["candidates"]:
        out += ["## Candidates - not applied, listed so you can see them", ""]
        for cand in res["candidates"]:
            out.append(f"- {cand['id']}  {cand['rule']}")
            out.append(f"  {_render_layer3(cand)}")
            for why in cand.get("demoted_because") or []:
                out.append(f"  demoted, waived because: {why}")
        out.append("")
    out += [res["instruction"], ""]
    return "\n".join(out)


def explain(records: list[dict], sentence: str, research_type: str | None,
            section: str | None, types: list[dict],
            layer1_path: str | None = None) -> dict:
    """Which layer and which rule id shaped a given sentence (5.7).

    Lexical, and honest about it: this attributes by content-word overlap, so
    it says which rules are ABOUT this sentence, not which one caused it.
    Without it a registry of thirty learned rules is unauditable and the user
    cannot tell a learned rule from a hallucinated one - which is the failure
    this exists to prevent, and a ranked list prevents it.
    """
    want = set(prose._content_words(sentence))
    scored: list[dict] = []

    def score(text: str) -> float:
        have = set(prose._content_words(text))
        if not have or not want:
            return 0.0
        return len(want & have) / len(want | have)

    for i, (head, body) in enumerate(TEN_RULES, 1):
        scored.append({"layer": 2, "id": f"L2-{i:02d}", "rule": head,
                       "score": round(score(head + " " + body), 3)})
    scored.append({"layer": 2, "id": "L2-11", "rule": GS_RULE[0],
                   "score": round(score(GS_RULE[0] + " " + GS_RULE[1]), 3)})
    applicable: list[dict] = []
    for rec in records:
        if rec.get("status") != "active":
            continue
        if not scope_applies(rec, research_type, section, types):
            continue
        # The rule's own evidence is scored beside its text: a learned rule is
        # written as an instruction ("open on the material system") and the
        # sentence it shaped shares no words with it, while the edit it was
        # induced from usually does.
        corpus = str(rec["rule"]) + " " + " ".join(
            str(e.get("after") or "") for e in rec.get("evidence", []))
        row = {"layer": 3, "id": rec["id"], "rule": rec["rule"],
               "score": round(score(corpus), 3)}
        scored.append(row)
        applicable.append(row)
    if layer1_path:
        for line in _read(layer1_path).splitlines():
            if line.strip().startswith(("-", "*")):
                row = {"layer": 1, "id": "L1",
                       "rule": line.strip("-* ").strip(),
                       "score": round(score(line), 3)}
                scored.append(row)
                applicable.append(row)
    ranked = sorted((s for s in scored if s["score"] > 0),
                    key=lambda s: (-s["score"], s["layer"]))
    # A sentence that matches nothing lexically was still written under
    # whatever layers 1 and 3 hold for its section, and saying "nothing" would
    # read as "no learned rule applied here" - which is a different claim, and
    # the one the user would act on.
    fallback = sorted(applicable, key=lambda s: s["layer"])
    return {"sentence": sentence, "attribution": ranked[:8],
            "applies_anyway": [] if ranked else fallback[:8],
            "best": ranked[0] if ranked else None,
            "counts": {"considered": len(scored), "matched": len(ranked),
                       "applicable": len(applicable)}}


# ---------------------------------------------------------------------------
# stats - 7
# ---------------------------------------------------------------------------

def stats(records: list[dict], types: list[dict],
          digest_path: str | None = None) -> dict:
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    per_type: dict[str, dict] = {}
    for rec in records:
        by_status[str(rec.get("status"))] = by_status.get(
            str(rec.get("status")), 0) + 1
        by_kind[str(rec.get("kind"))] = by_kind.get(str(rec.get("kind")), 0) + 1
        rtype = (rec.get("scope") or {}).get("research_type") or UNIVERSAL
        slot = per_type.setdefault(rtype, {"rules": 0, "observations": 0,
                                           "projects": []})
        slot["rules"] += 1
        slot["observations"] += int(rec.get("observations") or 0)
        for ev in rec.get("evidence", []):
            name = str(ev.get("project") or "")
            if name and name not in slot["projects"]:
                slot["projects"].append(name)

    # Type proliferation kills learning silently: nothing errors, no rule ever
    # reaches two observations, and the registry looks populated while being
    # inert. So a type with one project in it is called out by name.
    thin = sorted(t for t, s in per_type.items()
                  if len(s["projects"]) <= 1 and t != UNIVERSAL)

    # The digest is source-derived and the registry is evidence-derived. They
    # are never merged and neither is regenerated from the other, so a registry
    # rule that restates a digest rule is redundant rather than in conflict -
    # and worth retiring.
    digest_lines = [l.strip("-* ").strip() for l in
                    _read(digest_path or DIGEST_FILE).splitlines()
                    if len(l.strip()) > 25]
    redundant: list[dict] = []
    for rec in records:
        if rec.get("status") != "active":
            continue
        mine = set(prose._content_words(str(rec.get("rule"))))
        if not mine:
            continue
        for line in digest_lines:
            theirs = set(prose._content_words(line))
            if not theirs:
                continue
            overlap = len(mine & theirs) / len(mine | theirs)
            if overlap >= 0.5:
                redundant.append({"id": rec["id"], "rule": rec["rule"],
                                  "digest_line": line[:120],
                                  "overlap": round(overlap, 2)})
                break

    # 5.10d. A rule whose waivers outnumber its observations wants the user's
    # eye whether or not it has reached three - the mechanism cannot tell
    # "this rule makes this sentence worse" from "applying this rule is work",
    # so the reasons are put in front of the person who can.
    waived: list[dict] = []
    for rec in records:
        total, indep = waiver_counts(rec)
        if not total:
            continue
        obs = int(rec.get("observations") or 0)
        waived.append({"id": rec["id"], "rule": rec["rule"],
                       "status": rec.get("status"), "waivers": total,
                       "independent_waivers": indep, "observations": obs,
                       "outnumbers": total > obs,
                       "demoted_because": rec.get("demoted_because") or [],
                       "reasons": [str(w.get("because") or "")
                                   for w in (rec.get("waived") or [])]})
    # Keyed on `demoted_because`, not on the waiver count: `promote` clears
    # that field, so a rule brought back deliberately stops being reported as
    # applied to nothing while its waivers stay on the record.
    demoted = [w for w in waived if w["demoted_because"]]

    known = {t["id"] for t in types}
    unknown = sorted({str((r.get("scope") or {}).get("research_type"))
                      for r in records
                      if (r.get("scope") or {}).get("research_type")
                      not in known | {UNIVERSAL}})
    path, source = rules_home()
    stranded = stranded_registry()
    return {"rules": len(records), "by_status": by_status, "by_kind": by_kind,
            "per_type": per_type, "thin_types": thin,
            "restates_the_digest": redundant,
            "unknown_types": [u for u in unknown if u],
            "waived": waived, "demoted": demoted,
            # `volatile` is item 66. ${CLAUDE_PLUGIN_DATA} survives a plugin
            # UPDATE, which is what item 30 moved the registry there for, and
            # is auto-deleted when the plugin is uninstalled from every scope
            # - so the destination is durable against the event it was chosen
            # for and destroyed by a different one. rules.yml is the one file
            # here that quotes unpublished prose and therefore has no upstream
            # copy anywhere, by design, so that is a total loss rather than an
            # inconvenience. The engine cannot stop the harness deleting its
            # own data directory; what it can do is stop the user learning
            # about it afterwards.
            "registry": {"path": path, "source": source,
                         "types": types_file(), "stranded": stranded,
                         "volatile": source == "plugin_data",
                         "volatile_note": (
                             "this path is the plugin's own data directory. "
                             "It survives an update and is DELETED when the "
                             "plugin is uninstalled from every scope. Nothing "
                             "here has an upstream copy - rules.yml quotes "
                             "unpublished prose and never leaves the machine "
                             "- so copy it somewhere of your own and set "
                             "$PWA_RULES_FILE to it before uninstalling, "
                             "moving machines, or reinstalling."
                             if source == "plugin_data" else "")},
            "counts": {"rules": len(records), "thin_types": len(thin),
                       "redundant": len(redundant), "waived": len(waived),
                       "demoted": len(demoted)}}


# ---------------------------------------------------------------------------
# Text output
# ---------------------------------------------------------------------------

def print_extract(res: dict) -> None:
    print(f"{res['counts']['edits']} edits "
          f"({res['counts']['paragraph_edits']} paragraph, "
          f"{res['counts']['sentence_edits']} sentence); "
          f"{res['paragraphs_unchanged']} of {res['paragraphs_before']} "
          f"paragraphs untouched\n")
    _print_reverts(res)
    for edit in res["edits"]:
        head = f"  {edit['granularity']:9} {edit.get('shape', edit['op']):16}"
        marks = []
        if edit["numbers"]["changed"]:
            marks.append(f"numbers {edit['numbers']['removed']}"
                         f"->{edit['numbers']['added']}")
        if edit["citekeys"]["changed"]:
            marks.append(f"citekeys {edit['citekeys']['removed']}"
                         f"->{edit['citekeys']['added']}")
        if edit["flags"]["changed"]:
            marks.append("FLAG block changed")
        print(head + f" {edit['words_before']}w -> {edit['words_after']}w"
              + ("  " + "; ".join(marks) if marks else ""))
        if edit["before"]:
            print(f"    before: {edit['before'][:150]}")
        if edit["after"]:
            print(f"    after:  {edit['after'][:150]}")


def _print_reverts(res: dict) -> None:
    """11.5. Printed loudly, because a revert is the strongest evidence here.

    The user did not merely decline a suggestion - they saw the change land
    in their paper and took it back out.
    """
    if res.get("no_pass_changes"):
        print("  %s" % res["no_pass_changes"])
        return
    if "pass_changes" not in res:
        return
    n = res.get("reverted", 0)
    print("  %d automatic change%s recorded last round, %d reverted"
          % (res["pass_changes"], "" if res["pass_changes"] == 1 else "s", n))
    for r in res.get("reverts", []):
        print("    REVERTED  %s%s"
              % (r["pass"], f" / {r['rule']}" if r["rule"] else ""))
        print("      the pass wrote:  %s" % r["pass_wrote"][:100])
        print("      the user wrote:  %s"
              % r["because"].split("They wrote: ", 1)[-1][:100])
    if n:
        print("    -> a waiver each, with the user's own sentence as the "
              "reason. Three independent\n       reverts demote the rule "
              "(11.5)")


def print_classify(res: dict) -> None:
    counts: dict[str, int] = {}
    for edit in res["edits"]:
        counts[edit["class"]] = counts.get(edit["class"], 0) + 1
    _print_reverts(res)
    print("classification - only stylistic and structural can become rules, "
          "and a revert\ndemotes one\n")
    for name in EDIT_CLASSES + ("undecided",):
        if counts.get(name):
            print(f"  {name:12} {counts[name]}")
    print()
    for edit in res["edits"]:
        proposed = (f" (proposed {edit['proposed']})" if edit.get("proposed")
                    else "")
        print(f"  {edit['class']:10}{proposed:22} {edit.get('shape', edit['op'])}"
              f"  {edit['route']}")
        if edit["before"]:
            print(f"    before: {edit['before'][:120]}")
        if edit["after"]:
            print(f"    after:  {edit['after'][:120]}")


def print_budgets(res: dict) -> None:
    print(f"number density budgets - {res['research_type']}\n")
    print(f"  {'section':16} {'resolved':>14} {'basis':>9} {'default':>8} "
          f"{'drift':>6}  source")
    for row in res["sections"]:
        shape = ("unenforced" if row["inline"] is None
                 else f"{row['inline']:g}/{row['clusters']:g}")
        default = ("unenforced" if row["default_inline"] is None
                   else f"{row['default_inline']:g}")
        drift = f"{row['drift']:g}x" if row["drift"] else "-"
        mark = "  <- look" if row["wants_a_look"] else ""
        print(f"  {row['section']:16} {shape:>14} {row['basis']:>9} "
              f"{default:>8} {drift:>6}  {row['source']}{mark}")
    if res["counts"]["wants_a_look"]:
        print("\n  a budget that has moved more than 2x wants your eye rather "
              "than another observation")


def print_frozen(res: dict) -> None:
    print(f"{res['counts']['frozen']} of {res['counts']['sections']} sections "
          f"have been edited since the engine wrote them\n")
    for row in res["sections"]:
        by = row["revised_by"] or row["written_by"] or "-"
        print(f"  {row['section']:16} {row['state']:14} last written by {by}"
              + (f" in {row['round']}" if row["round"] else ""))
    if res["frozen"]:
        many = len(res["frozen"]) > 1
        print("\n  " + ", ".join(f"{s}.md" for s in res["frozen"])
              + (" have" if many else " has")
              + " been edited since the engine wrote "
              + ("them" if many else "it")
              + ". Nothing will rewrite "
              + ("them" if many else "it")
              + " this round. "
              + ("Their" if many else "Its")
              + " lessons are learned and applied to the other sections; "
                "checks still run and report. `--redraft <section>` hands one "
                "back to the drafter.")


def print_stats_registry(res: dict) -> None:
    reg = res.get("registry") or {}
    where = {"env": "$PWA_RULES_FILE", "plugin_data": "$CLAUDE_PLUGIN_DATA",
             "toolkit": "the toolkit"}.get(str(reg.get("source")), "?")
    print(f"  rules.yml  {reg.get('path')}  [{where}]")
    print(f"  types.yml  {reg.get('types')}")
    if reg.get("volatile"):
        print("  ! %s" % reg["volatile_note"])
    if reg.get("stranded"):
        # The quiet failure this guards: a registry that exists, is not being
        # read, and says nothing while every draft goes out with no layer 3.
        print(f"  ! a non-empty registry sits unread at {reg['stranded']}.")
        print("    Resolution is pointing elsewhere. Copy it across or set "
              "$PWA_RULES_FILE.")
    print("")


def print_stats(res: dict) -> None:
    print(f"{res['rules']} rules in the registry\n")
    print_stats_registry(res)
    print("  by status   " + ", ".join(f"{k} {v}" for k, v
                                       in sorted(res["by_status"].items())))
    print("  by kind     " + ", ".join(f"{k} {v}" for k, v
                                       in sorted(res["by_kind"].items())))
    print()
    print(f"  {'research type':20} {'rules':>5} {'obs':>4} projects")
    for rtype, slot in sorted(res["per_type"].items()):
        print(f"  {rtype:20} {slot['rules']:>5} {slot['observations']:>4} "
              f"{', '.join(slot['projects']) or '-'}")
    if res["thin_types"]:
        print(f"\n  one project only: {', '.join(res['thin_types'])} - a type "
              f"with one project in it learns nothing, and nothing else will "
              f"say so")
    if res["restates_the_digest"]:
        print("\n  restates the digest (redundant, not in conflict):")
        for row in res["restates_the_digest"]:
            print(f"    {row['id']}  {row['rule'][:70]}")
    if res.get("waived"):
        print("\n  waived at least once:")
        for row in res["waived"]:
            mark = "  <- waivers outnumber observations" \
                if row["outnumbers"] else ""
            print(f"    {row['id']}  {row['waivers']} waiver(s) "
                  f"({row['independent_waivers']} independent) vs "
                  f"{row['observations']} observation(s)  "
                  f"[{row['status']}]{mark}")
            for why in row["reasons"]:
                print(f"        because: {why}")
    if res.get("demoted"):
        print(f"\n  demoted by waivers, applied to nothing: "
              f"{', '.join(r['id'] for r in res['demoted'])}")
        print("    `promote <id> --because \"...\"` brings one back.")
    if res["unknown_types"]:
        print(f"\n  scoped to a type that is not in the vocabulary: "
              f"{', '.join(res['unknown_types'])}")


def print_explain(res: dict) -> None:
    print(f"{res['counts']['matched']} of {res['counts']['considered']} rules "
          f"share vocabulary with this sentence\n")
    for row in res["attribution"]:
        print(f"  layer {row['layer']}  {row['id']:8} {row['score']:.2f}  "
              f"{row['rule'][:80]}")
    if res["applies_anyway"]:
        print("  nothing matched on words. These applied to the section it "
              "was written in:")
        for row in res["applies_anyway"]:
            print(f"    layer {row['layer']}  {row['id']:8} "
                  f"{row['rule'][:80]}")


def print_conflicts(res: dict) -> None:
    print(f"{res['counts']['overlaps']} overlapping scope(s)\n")
    for pair in res["overlaps"]:
        mark = "  (already recorded)" if pair["already_recorded"] else ""
        print(f"  {pair['a']} vs {pair['b']}{mark}")
        print(f"    {pair['a']}: {pair['a_rule']}")
        print(f"       scope {pair['a_scope']}")
        print(f"    {pair['b']}: {pair['b_rule']}")
        print(f"       scope {pair['b_scope']}")
    if res["overlaps"]:
        print("  whether these actually contradict is judgment: does the "
              "contradiction have a reason a scientist in each field would "
              "recognize?")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _kv(text: str) -> dict:
    """`inline=8,clusters=6,basis=per100` as a dict."""
    out: dict = {}
    for part in (text or "").split(","):
        if not part.strip():
            continue
        key, _, val = part.partition("=")
        key, val = key.strip(), val.strip()
        try:
            out[key] = float(val) if re.match(r"^-?\d+(\.\d+)?$", val) else val
        except ValueError:
            out[key] = val
    return out


def _project_paper_kind(project: str) -> str | None:
    """project.yml:paper_kind (review-paper 1). Absent reads as research.

    Unrecognised also reads as research, on the same rule manuscript.py
    applies: a typo must not scope a lesson to a kind of paper nobody is
    writing, where it would then apply to nothing and nothing would say so.
    """
    path = os.path.join(project, "project.yml")
    for line in _read(path).splitlines():
        m = re.match(r"^paper_kind\s*:\s*(.*)$", line)
        if m:
            value = m.group(1).split("#", 1)[0].strip().strip("\"'")
            return value if value in PAPER_KINDS else "research"
    return "research"


def _project_research_type(project: str) -> str | None:
    """project.yml:research_type - asked once, cached, never re-asked (5.5)."""
    path = os.path.join(project, "project.yml")
    for line in _read(path).splitlines():
        m = re.match(r"^research_type\s*:\s*(.*)$", line)
        if m:
            value = m.group(1).split("#", 1)[0].strip().strip("\"'")
            return value or None
    return None


# ---------------------------------------------------------------------------
# The preservation invariant - module 1c's checked postcondition (3.1)
# ---------------------------------------------------------------------------
#
# This is the part of the revision pass that matters, and it is what buys the
# wiggle room. A pass that PROVABLY preserves every number, every citekey and
# every flag can be allowed to rewrite anything else however it likes, because
# nothing it does can change the science. Freedom inside a checked box, not a
# rule for every sentence.
#
# So none of it is an instruction to the pass. It is a postcondition, checked
# after the fact, and a failure restores the snapshot.

WORD_BAND = 0.15


# manuscript.py's SUPPLEMENT_STEM - the supplement's source file.
SUPPLEMENT_STEM = "supplementary"


def snapshot_dir(project: str) -> str:
    return os.path.join(prose.source_text_dir(project), ".pre_revision")


def snapshot(project: str, sections: list[str] | None = None) -> dict:
    """Copy each section aside before the pass, so a failure can be undone."""
    st = prose.source_text_dir(project)
    into = snapshot_dir(project)
    os.makedirs(into, exist_ok=True)
    taken: list[str] = []
    # The supplement is revised with the round (item 150), so it is
    # snapshotted and checked with it; absent, it is skipped like any section.
    for section in (sections or prose.SECTION_FILES + [SUPPLEMENT_STEM]):
        path = os.path.join(st, f"{section}.md")
        if not os.path.isfile(path):
            continue
        _write(os.path.join(into, f"{section}.md"), _read(path))
        taken.append(section)
    return {"project": project, "dir": into, "sections": taken,
            "counts": {"sections": len(taken)}}


def preserve(project: str, section: str, restore: bool = False) -> dict:
    """Check one revised section against its snapshot (3.1).

    Six invariants, and a failure REJECTS THE WHOLE PASS FOR THAT SECTION
    rather than patching it: a section that could not be revised safely is
    simply the section the drafter wrote. It is a warning and not a stop,
    because the manuscript is no worse than it was.

    The permissive case is the one worth stating: a revision that rewrites
    every sentence while preserving all three multisets is ACCEPTED. That
    proves the box is a box and not a cage.
    """
    before_path, after_path = _section_paths(project, section)
    if not os.path.isfile(before_path):
        return {"section": section, "verdict": "no-snapshot",
                "detail": f"no {before_path}; nothing to check this against, "
                          f"so nothing is claimed about it",
                "failed": [], "restored": False}
    if not os.path.isfile(after_path):
        return {"section": section, "verdict": "rejected",
                "detail": "the section file is gone",
                "failed": ["empty"], "restored": False}

    before, after = _read(before_path), _read(after_path)
    failed: list[dict] = []

    if not after.strip():
        failed.append({"invariant": "empty",
                       "detail": "the pass rendered the section empty"})

    nd = _multiset_delta(_numbers_of(before), _numbers_of(after))
    if nd["changed"]:
        failed.append({"invariant": "numbers", **nd,
                       "detail": "a number the pass dropped or invented. "
                                 "Presentation is normalized (0.42 == 0.420); "
                                 "a unit conversion is not (42% != 0.42), "
                                 "because converting units is a claim about "
                                 "what was measured"})

    cd = _multiset_delta(_citekeys_of(before), _citekeys_of(after))
    if cd["changed"]:
        failed.append({"invariant": "citekeys", **cd,
                       "detail": "a citekey the pass dropped or invented"})

    fd = _multiset_delta(_flags_of(before), _flags_of(after))
    if fd["changed"]:
        failed.append({"invariant": "flags", **fd,
                       "detail": "a **[FLAG: ...]** block must survive "
                                 "VERBATIM - it is the user's outstanding "
                                 "work list, and a reworded flag is a lost one"})

    wb, wa = len(before.split()), len(after.split())
    if wb and abs(wa - wb) > WORD_BAND * wb:
        failed.append({"invariant": "length", "before": wb, "after": wa,
                       "detail": f"{wa} words against {wb}, outside "
                                 f"+/-{WORD_BAND:.0%}. The band exists to stop "
                                 f"a pass quietly deleting a third of a "
                                 f"section under cover of 'cutting' - which is "
                                 f"the failure mode of the very rules moved "
                                 f"into this pass"})

    # The seventh, and it is ADVISORY (flow-and-conclusion 5.4). A rewrite
    # that turns `a thousandfold` into `a hundredfold` changes zero numeric
    # tokens, zero citekeys, zero flags and almost no words, so every
    # invariant above it passes and `--restore` sees a clean pass. It does
    # not reject, because `specs/writing-engine-prose.md` 10.7 says a check
    # with a threshold - here, a closed word list that can be wrong about a
    # word - is calibrated against real published prose before it is allowed
    # to assert anything, and a preservation invariant is the one place in
    # the prose pipeline that CAN act. It reports the pair it would have
    # blocked until the probe runs: specs/probes/magnitude-2026-09-20/.
    advisory: list[dict] = []
    gd = _multiset_delta(_magnitudes_of(before), _magnitudes_of(after))
    if gd["changed"]:
        advisory.append({
            "invariant": "magnitudes", **gd,
            "calibrated": False, "advisory": True,
            "detail": "a spelled-out quantity changed, and no numeric token "
                      "moved with it - `a thousandfold` to `a hundredfold` "
                      "passes every other invariant here. ADVISORY: this "
                      "list is not calibrated yet, so it reports the pair "
                      "and rejects nothing"})

    verdict = "rejected" if failed else "accepted"
    restored = False
    if failed and restore:
        _write(after_path, before)
        restored = True
    return {"section": section, "verdict": verdict,
            "words_before": wb, "words_after": wa,
            "failed": failed, "advisory": advisory, "restored": restored,
            "snapshot": before_path, "path": after_path,
            "counts": {"failed": len(failed), "advisory": len(advisory)}}


def preserve_all(project: str, sections: list[str] | None = None,
                 restore: bool = False) -> dict:
    """Per section, never per run: one section that could not be revised
    safely does not throw away four that could."""
    rows = [preserve(project, s, restore)
            for s in (sections or prose.SECTION_FILES + [SUPPLEMENT_STEM])
            if os.path.isfile(_section_paths(project, s)[0])]
    return {"project": project, "sections": rows,
            "rejected": [r["section"] for r in rows
                         if r["verdict"] == "rejected"],
            "counts": {"checked": len(rows),
                       "rejected": sum(1 for r in rows
                                       if r["verdict"] == "rejected"),
                       # Counted separately from `rejected` and never added
                       # to it: an advisory finding rejected nothing, and a
                       # count that mixes the two is a count nobody can act
                       # on.
                       "advisory": sum(len(r.get("advisory", []))
                                       for r in rows),
                       "restored": sum(1 for r in rows if r["restored"])}}


def print_preserve(res: dict) -> None:
    rows = res["sections"]
    print(f"{res['counts']['checked']} section(s) checked against their "
          f"pre-revision snapshot; {res['counts']['rejected']} rejected\n")
    for row in rows:
        mark = {"accepted": "ok", "rejected": "REJECTED",
                "no-snapshot": "-"}.get(row["verdict"], row["verdict"])
        print(f"  {row['section']:16} {mark:10} "
              f"{row.get('words_before', 0)}w -> {row.get('words_after', 0)}w"
              + ("  (snapshot restored)" if row["restored"] else ""))
        for bad in row["failed"]:
            print(f"      {bad['invariant']}: {bad['detail']}")
            if bad.get("removed"):
                print(f"        gone:  {', '.join(bad['removed'][:8])}")
            if bad.get("added"):
                print(f"        new:   {', '.join(bad['added'][:8])}")
        # Printed under the section that carries it and marked as what it
        # is. An advisory line that looks like a rejection teaches the
        # reader to discount both.
        for note in row.get("advisory", []):
            print(f"      ADVISORY  {note['invariant']}: {note['detail']}")
            if note.get("removed"):
                print(f"        gone:  {', '.join(note['removed'][:8])}")
            if note.get("added"):
                print(f"        new:   {', '.join(note['added'][:8])}")
    if res["counts"]["rejected"]:
        print("\n  A rejected section is the section the drafter wrote, which "
              "is a worse paper than the one the pass wanted and not a wrong "
              "one. Nothing here blocks the build.")


def _section_paths(project: str, section: str) -> tuple[str, str]:
    st = prose.source_text_dir(project)
    after = os.path.join(st, f"{section}.md")
    before = os.path.join(st, ".pre_revision", f"{section}.md")
    return before, after


def main() -> int:
    p = argparse.ArgumentParser(
        prog="learn.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true",
                   help="emit JSON instead of text")

    # --json on either side of the subcommand, per the convention in AGENTS.md.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true",
                        default=argparse.SUPPRESS,
                        help="emit JSON instead of text")
    common.add_argument("--registry", default=None,
                        help="rules.yml; defaults to the toolkit's")
    common.add_argument("--types-file", default=None,
                        help="research_types.yml; defaults to the toolkit's")

    sub = p.add_subparsers(dest="cmd", required=True)

    ex = sub.add_parser("extract", parents=[common],
                        help="aligned before/after pairs for an edited section")
    ex.add_argument("--project", default=None)
    ex.add_argument("--section", default=None)
    ex.add_argument("--before", default=None, help="the snapshot")
    ex.add_argument("--after", default=None, help="the edited file")
    ex.add_argument("--prose-report", default=None,
                    help="last round's reports/rN/prose_report.md. With it, "
                         "each edit says which automatic pass wrote the text "
                         "the user started from, and whether they reverted "
                         "it (prose 11.5)")

    cl = sub.add_parser("classify", parents=[common],
                        help="the mechanical evidence behind each edit's class")
    cl.add_argument("--project", default=None)
    cl.add_argument("--section", default=None)
    cl.add_argument("--before", default=None)
    cl.add_argument("--after", default=None)
    cl.add_argument("--length-forced", action="store_true",
                    help="this round was over the journal's word cap, so its "
                         "edits are applied and not learned from (7)")
    cl.add_argument("--prose-report", default=None,
                    help="last round's reports/rN/prose_report.md - a "
                         "reverted automatic change is classified `revert` "
                         "and routed to a waiver (prose 11.5)")

    ob = sub.add_parser("observe", parents=[common],
                        help="record one observation of a rule")
    ob.add_argument("--rule", required=True)
    ob.add_argument("--kind", default="stylistic", choices=list(KINDS))
    ob.add_argument("--research-type", required=True)
    ob.add_argument("--section", default=ANY_SECTION)
    ob.add_argument("--scope-type", default=None,
                    help="the scope's research type, if it is not the "
                         "observation's own - e.g. a parent")
    ob.add_argument("--project", default="")
    ob.add_argument("--round", dest="round_id", default="")
    ob.add_argument("--paragraph", default="")
    ob.add_argument("--before", default="")
    ob.add_argument("--after", default="")
    ob.add_argument("--source", default="user_edit",
                    choices=["user_edit", "override"])
    ob.add_argument("--count", type=float, default=None,
                    help="numeric records: the observed inline count")
    ob.add_argument("--clusters", type=float, default=None)
    ob.add_argument("--budget", type=float, default=None,
                    help="numeric records: the budget that was overridden")
    ob.add_argument("--value", default="",
                    help="numeric records: inline=8,clusters=6,basis=per100")
    ob.add_argument("--note", default="")
    ob.add_argument("--id", dest="rule_id", default=None)
    ob.add_argument("--length-forced", action="store_true")
    ob.add_argument("--paper-kind", default="", choices=["", *PAPER_KINDS],
                    help="what the document this edit came from IS "
                         "(review-paper 1). Recorded on the evidence, and a "
                         "rule seen on one kind only is scoped to it: a "
                         "review's attribution habit must not reach a "
                         "research paper's results section. Read from "
                         "project.yml when --project is given")
    ob.add_argument("--scope-kind", default="", choices=["", *PAPER_KINDS,
                                                         ANY_KIND],
                    help="override the scope's paper kind, e.g. `any` for a "
                         "lesson you know applies to every kind of paper")
    ob.add_argument("--dry-run", action="store_true",
                    help="say what would happen and write nothing")

    pr = sub.add_parser("promote", parents=[common],
                        help="force a candidate active, on the record")
    pr.add_argument("id")
    pr.add_argument("--because", required=True)

    sn = sub.add_parser("snapshot", parents=[common],
                        help="copy source_text/ aside before module 1c runs")
    sn.add_argument("project")
    sn.add_argument("--sections", default="",
                    help="comma-separated; default every section present")

    pv = sub.add_parser("preserve", parents=[common],
                        help="check a revised section against its snapshot "
                             "(3.1): numbers, citekeys, flags, length")
    pv.add_argument("project")
    pv.add_argument("--sections", default="",
                    help="comma-separated; default every section with a "
                         "snapshot")
    pv.add_argument("--restore", action="store_true",
                    help="put the snapshot back for any section that failed. "
                         "Per section, never per run - one section that could "
                         "not be revised safely does not throw away four that "
                         "could")

    wv = sub.add_parser("waive", parents=[common],
                        help="record that a rule was not applied, and why")
    wv.add_argument("id")
    wv.add_argument("--because", required=True,
                    help="stored verbatim; three independent waivers demote "
                         "an active rule to candidate")
    wv.add_argument("--project", default="")
    wv.add_argument("--round", dest="round_id", default="")
    wv.add_argument("--section", default="")

    rt = sub.add_parser("retire", parents=[common], help="retire a rule")
    rt.add_argument("id")
    rt.add_argument("--because", required=True)

    rs = sub.add_parser("resolve", parents=[common],
                        help="record a contradiction's outcome")
    rs.add_argument("id", help="the newer rule")
    rs.add_argument("--against", required=True, help="the older rule")
    rs.add_argument("--outcome", required=True,
                    choices=["demote", "supersede", "conflict"])
    rs.add_argument("--note", required=True)

    cf = sub.add_parser("conflicts", parents=[common],
                        help="active rules whose scope overlaps")
    cf.add_argument("id", nargs="?", default=None)

    mg = sub.add_parser("merge", parents=[common],
                        help="union another registry by id")
    mg.add_argument("file")
    mg.add_argument("--dry-run", action="store_true")

    st = sub.add_parser("stats", parents=[common],
                        help="observations per type, thin types, redundancy")
    st.add_argument("--digest", default=None)

    bg = sub.add_parser("budgets", parents=[common],
                        help="what each section's budget resolves to, and why")
    bg.add_argument("--research-type", default=None)
    bg.add_argument("--project", default=None)
    bg.add_argument("--config", default=None,
                    help="this project's writing_config.yml")

    br = sub.add_parser("brief", parents=[common],
                        help="the whole ladder as one ordered document")
    br.add_argument("--research-type", default=None)
    br.add_argument("--paper-kind", default="", choices=["", *PAPER_KINDS],
                    help="which paper kind this brief is for. A rule scoped "
                         "to the other kind is left out of it. Read from "
                         "project.yml when --project is given")
    br.add_argument("--project", default=None)
    br.add_argument("--section", default=None)
    br.add_argument("--round", dest="round_id", default="")
    br.add_argument("--layer1", default=None,
                    help="the module's quoted lessons from the user's edits")
    br.add_argument("--digest", default=None)
    br.add_argument("--config", default=None)
    br.add_argument("--explain", default=None, metavar="SENTENCE",
                    help="which layer and rule id shaped this sentence")
    br.add_argument("--out", default=None, help="also write the brief here")

    ty = sub.add_parser("types", parents=[common],
                        help="the controlled vocabulary of research types")
    ty.add_argument("--add", default=None, metavar="ID")
    ty.add_argument("--label", default="")
    ty.add_argument("--parents", default="")
    ty.add_argument("--because", default="",
                    help="required with --add: the vocabulary is controlled")

    fz = sub.add_parser("frozen", parents=[common],
                        help="which sections the user has taken over")
    fz.add_argument("project")
    fz.add_argument("--record", action="append", default=[], metavar="SECTION",
                    help="write the hash of record for a section a module just "
                         "wrote; `all` for every section present")
    fz.add_argument("--by", default="draft-sections",
                    help="which module wrote it")
    fz.add_argument("--round", dest="round_id", default="")

    args = p.parse_args()
    as_json = getattr(args, "json", False)

    def emit(res: dict, printer) -> None:
        if as_json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            printer(res)

    try:
        return _dispatch(args, emit)
    except RegistryError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2


def _dispatch(args, emit) -> int:
    registry = args.registry if getattr(args, "registry", None) else rules_file()
    types_path = args.types_file if getattr(args, "types_file", None) \
        else types_file()

    if args.cmd in ("extract", "classify"):
        if args.before and args.after:
            before_path, after_path = args.before, args.after
        elif args.project and args.section:
            before_path, after_path = _section_paths(args.project,
                                                     args.section)
        else:
            print("extract needs either --before and --after, or --project "
                  "and --section", file=sys.stderr)
            return 2
        if not os.path.isfile(after_path):
            print(f"No such file: {after_path}", file=sys.stderr)
            return 2
        if not os.path.isfile(before_path):
            # No snapshot is not an error: it is the first round, and there is
            # nothing to have learned from yet.
            res = {"paragraphs_before": 0, "paragraphs_after": 0,
                   "paragraphs_unchanged": 0, "edits": [],
                   "no_baseline": before_path,
                   "counts": {"edits": 0, "paragraph_edits": 0,
                              "sentence_edits": 0}}
        else:
            res = align(_read(before_path), _read(after_path))
        res["before"] = before_path
        res["after"] = after_path
        # 11.5. Attribution first, because `classify` reads the flag it
        # sets. Without the report nothing is attributed and every edit is
        # classified exactly as it was before - the loop degrades to its old
        # behaviour rather than to a wrong one.
        report = getattr(args, "prose_report", None)
        if report:
            if not os.path.isfile(report):
                print(f"No such prose report: {report}", file=sys.stderr)
                return 2
            changes = read_pass_changes(_read(report))
            res["edits"] = attribute(res["edits"], changes)
            res["pass_changes"] = len(changes)
            res["reverted"] = sum(1 for e in res["edits"]
                                  if e.get("reverted"))
            res["reverts"] = revert_evidence(
                res["edits"], args.project or "", "",
                args.section or "")
            if not changes:
                res["no_pass_changes"] = (
                    f"{report} carries no `## Changed` entries with both a "
                    f"before and an after, so nothing could be attributed. "
                    f"That is not the same as nothing having been reverted")
        if args.cmd == "classify":
            res["edits"] = classify(res["edits"], args.length_forced)
            emit(res, print_classify)
        else:
            emit(res, print_extract)
        return 0

    if args.cmd == "observe":
        records = read_registry(registry)
        types = read_types(types_path)
        known = {t["id"] for t in types}
        if args.research_type not in known:
            print(f"{args.research_type!r} is not in the research-type "
                  f"vocabulary. It holds: {', '.join(sorted(known))}. "
                  f"`types --add` extends it, and needs a reason.",
                  file=sys.stderr)
            return 2
        obs_kind = args.paper_kind or (
            _project_paper_kind(args.project) if args.project else "")
        evidence: dict = {"project": args.project, "round": args.round_id,
                          "section": args.section,
                          "research_type": args.research_type,
                          "source": args.source}
        if obs_kind:
            evidence["paper_kind"] = obs_kind
        for key, val in (("paragraph", args.paragraph),
                         ("before", args.before), ("after", args.after),
                         ("count", args.count), ("clusters", args.clusters),
                         ("budget", args.budget), ("note", args.note)):
            if val not in ("", None):
                evidence[key] = val
        if args.length_forced:
            evidence["length_forced"] = True
        scope = {"research_type": args.scope_type or args.research_type,
                 "paper_kind": args.scope_kind or obs_kind or ANY_KIND,
                 "section": args.section}
        verdict = observe(records, args.rule, args.kind, evidence, scope,
                          _kv(args.value) or None, args.rule_id)
        if not args.dry_run:
            verdict["written"] = write_registry(records, registry)
        emit(verdict, lambda r: print(
            f"{r['id']}  {r['status']}  ({r['reason']})"
            + ("" if r["added"] else "  [this observation was already "
                                     "recorded]")))
        return 0

    if args.cmd == "promote":
        records = read_registry(registry)
        res = promote(records, args.id, args.because)
        res["written"] = write_registry(records, registry)
        emit(res, lambda r: print(f"{args.id}  {r['status']}  ({r['reason']})"))
        return 0

    if args.cmd == "snapshot":
        res = snapshot(args.project,
                       [s.strip() for s in args.sections.split(",")
                        if s.strip()] or None)
        emit(res, lambda r: print(
            f"{r['counts']['sections']} section(s) copied to {r['dir']}"))
        return 0

    if args.cmd == "preserve":
        res = preserve_all(args.project,
                           [s.strip() for s in args.sections.split(",")
                            if s.strip()] or None, args.restore)
        emit(res, print_preserve)
        # 3.1: a failed invariant is a WARNING, not a stop. The section that
        # could not be revised safely is the section the drafter wrote, and
        # the manuscript is no worse than it was.
        return 0

    if args.cmd == "waive":
        records = read_registry(registry)
        res = waive(records, args.id, args.because, args.project,
                    args.round_id, args.section)
        res["written"] = write_registry(records, registry)
        emit(res, lambda r: print(
            f"{r['id']}  {r['was']} -> {r['status']}  ({r['reason']})"
            + ("\n  demoted; the reasons on the record are:\n    "
               + "\n    ".join(
                   next(x.get("demoted_because") or [] for x in records
                        if x.get("id") == r["id"]))
               if r.get("demoted") else "")))
        return 0

    if args.cmd == "retire":
        records = read_registry(registry)
        res = retire(records, args.id, args.because)
        res["written"] = write_registry(records, registry)
        emit(res, lambda r: print(f"{r['id']}  retired: {r['reason']}"))
        return 0

    if args.cmd == "resolve":
        records = read_registry(registry)
        types = read_types(types_path)
        res = resolve_conflict(records, args.id, args.against, args.outcome,
                               args.note, types)
        res["written"] = write_registry(records, registry)
        emit(res, lambda r: print(
            f"{r['outcome']}: " + ", ".join(f"{k} -> {v}" for k, v
                                            in r["statuses"].items())))
        return 0

    if args.cmd == "conflicts":
        records = read_registry(registry)
        res = conflicts(records, args.id, read_types(types_path))
        emit(res, print_conflicts)
        return 0

    if args.cmd == "merge":
        if not os.path.isfile(args.file):
            print(f"No such file: {args.file}", file=sys.stderr)
            return 2
        records = read_registry(registry)
        incoming = read_registry(args.file)
        res = merge(records, incoming)
        if not args.dry_run:
            res["written"] = write_registry(records, registry)
        emit(res, lambda r: print(
            f"{r['counts']['added']} added, {r['counts']['merged']} merged, "
            f"{r['counts']['renumbered']} renumbered, "
            f"{r['counts']['conflicted']} conflicting status"))
        return 0

    if args.cmd == "stats":
        records = read_registry(registry)
        res = stats(records, read_types(types_path), args.digest)
        emit(res, print_stats)
        return 0

    if args.cmd == "budgets":
        records = read_registry(registry)
        rtype = args.research_type or (
            _project_research_type(args.project) if args.project else None)
        config = args.config
        res = budgets(records, rtype or UNIVERSAL, read_types(types_path),
                      config)
        emit(res, print_budgets)
        return 0

    if args.cmd == "brief":
        records = read_registry(registry)
        types = read_types(types_path)
        rtype = args.research_type or (
            _project_research_type(args.project) if args.project else None)
        if args.explain:
            res = explain(records, args.explain, rtype, args.section, types,
                          args.layer1)
            emit(res, print_explain)
            return 0
        pkind = args.paper_kind or (
            _project_paper_kind(args.project) if args.project else None)
        res = brief(records, rtype, args.section, types, args.layer1,
                    args.round_id, args.digest, args.config,
                    paper_kind=pkind)
        if args.out:
            _write(args.out, render_brief(res))
            res["written"] = args.out
        emit(res, lambda r: print(render_brief(r)))
        return 0

    if args.cmd == "types":
        types = read_types(types_path)
        if args.add:
            if not args.because:
                print("adding a type needs a reason: the vocabulary is "
                      "controlled because a type per project scopes every "
                      "rule to a population of one, and nothing errors while "
                      "that happens", file=sys.stderr)
                return 2
            if any(t["id"] == args.add for t in types):
                print(f"{args.add} is already in the vocabulary",
                      file=sys.stderr)
                return 2
            types.append({"id": args.add,
                          "label": args.label or args.add,
                          "parents": [x.strip() for x in
                                      args.parents.split(",") if x.strip()],
                          "added": _today(), "because": args.because})
            write_types(types, types_path)
        res = {"types": types, "counts": {"types": len(types)}}
        emit(res, lambda r: [
            print(f"  {t['id']:18} {t.get('label', ''):34} "
                  f"parents {', '.join(t.get('parents') or []) or '-'}")
            for t in r["types"]])
        return 0

    if args.cmd == "frozen":
        if not os.path.isdir(args.project):
            print(f"No such directory: {args.project}", file=sys.stderr)
            return 2
        if args.record:
            wanted = args.record
            if "all" in wanted:
                wanted = list(prose.SECTION_FILES)
            res = record_provenance(args.project, wanted, args.by,
                                    args.round_id)
            emit(res, lambda r: print(
                f"recorded {', '.join(r['recorded']) or 'nothing'} in "
                f"{r['provenance']}"))
            return 0
        res = frozen(args.project)
        emit(res, print_frozen)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
