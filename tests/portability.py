#!/usr/bin/env python3
"""
tests/portability.py - the claims in AGENTS.md, enforced.

The toolkit is meant to survive a change of agent product: the engines are the
deliverable and the skills are a script for driving them. That only stays true
if nobody quietly adds a dependency, drops --json from a new subcommand, or
writes a skill against a mechanism one harness happens to have.

Fully offline, ~5s. Every check here corresponds to a sentence in AGENTS.md; if
one fails, either the code drifted or that file now says something untrue.

  python tests/portability.py
  python tests/portability.py -v
"""

from __future__ import annotations

import ast
import datetime
import glob
import importlib.util
import io
import json
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
TOOLS = os.path.join(ROOT, "tools")

VERBOSE = "-v" in sys.argv
PASS = FAIL = SKIP = 0

STDLIB = set(sys.stdlib_module_names)

# The five packages AGENTS.md declares, and which engines may need them. A
# module-level import outside this map is a new dependency, and a new
# dependency is a portability decision that belongs in AGENTS.md rather than in
# a diff nobody read.
#
# Keyed by IMPORT name, which is not always the pip name: `pptx` installs as
# `python-pptx`. AGENTS.md names the pip one, because that is what somebody
# types.
DECLARED = {
    # hard in both: each exits at startup without it. scholar.py is a client for
    # five HTTP APIs, so this is the same decision pubmed.py already made.
    "requests": {"pubmed.py", "scholar.py", "structure.py", "sequence.py"},
    # lazy in docx_edits.py; hard in help_deck.py, which writes OOXML by hand.
    "lxml": {"docx_edits.py", "help_deck.py"},
    # lazy in both: manuscript.py for a PDF reviewer report, toc_graphic.py to
    # read the journal's own example graphics out of its guidance PDF.
    "pypdf": {"manuscript.py", "toc_graphic.py"},
    # help_deck.py, where it is the one dependency no manuscript needs -
    # without it you are missing a slide deck about the toolkit, not a
    # capability of it - and graphic_figure.py, where it is NOT in that class
    # and is lazy on purpose. `geometry` opens a drawn float's .pptx and
    # checks that the slide's own elements do not collide (item 103). Absent
    # the package it reports DID NOT RUN and exits 2 rather than reporting a
    # slide it never read, and `render` goes ahead: a check that cannot run
    # and stays quiet is worse than no check, because the silence is
    # indistinguishable from a pass.
    "pptx": {"help_deck.py", "graphic_figure.py"},
    # lazy in manuscript.py, for one thing: converting a rendered figure into
    # the file type a separate-upload journal asks for (item 135). Without it
    # that figure is reported DID NOT RUN and nothing is written in its place;
    # a render already in an accepted format is copied and needs nothing.
    "PIL": {"manuscript.py"},
}

ENGINES = sorted(glob.glob(os.path.join(TOOLS, "*.py")))

# One engine importing another is reuse, not a dependency: `scholar.py` imports
# `pubmed.py` so the title matching and the CASSI abbreviations have exactly one
# implementation. These ship in the same directory, so they cannot go missing
# the way a pip package can, and they are not a portability decision.
LOCAL_MODULES = {os.path.splitext(os.path.basename(p))[0] for p in ENGINES}
SKILLS_DIR = os.path.join(ROOT, "skills")
SKILLS = sorted(glob.glob(os.path.join(SKILLS_DIR, "*", "SKILL.md")))

# A skill's body is not one file any more. `writing-engine`'s SKILL.md was
# 219 KB, larger than the whole engine payload of a submission round, and it
# is now a spine plus one `reference/*.md` per stage
# (specs/context-compaction.md 3).
#
# THE DANGEROUS HALF OF THAT SPLIT is this file: every grep below runs over
# "the skill", and pointed at SKILL.md alone it would now be looking at a
# THIRD of the text it used to cover - and would stay green for months while
# covering less and less. So the greps run over SKILL_TEXTS, and
# `test_skill_reference_files_are_covered` asserts that each one actually
# visited every reference file rather than trusting that it did.
SKILL_REFERENCES = sorted(glob.glob(
    os.path.join(SKILLS_DIR, "*", "reference", "*.md")))
SKILL_TEXTS = SKILLS + SKILL_REFERENCES

# grep name -> the paths it read. Written by the loops themselves, so a new
# grep that forgets the reference files is caught by the coverage test rather
# than by nobody.
VISITED: dict[str, set[str]] = {}


def visiting(grep: str, path: str) -> str:
    VISITED.setdefault(grep, set()).add(path)
    return read(path)


def owning_skill(path: str) -> str:
    """The SKILL.md a file belongs to - itself, or its parent's."""
    if os.path.basename(path) == "SKILL.md":
        return path
    return os.path.join(os.path.dirname(os.path.dirname(path)), "SKILL.md")

# Vocabulary that names a mechanism only one agent product has. A skill may
# describe WHAT it needs (its own context, a web fetch); it may not assume HOW.
#
# "sub-agent" is deliberately NOT on this list: it is ordinary vocabulary for a
# thing several harnesses have, and banning the word would only push the
# assumption somewhere less visible. What is checked instead is that every
# skill using it also states the fallback - see test_context_isolation_fallback.
#
# ${CLAUDE_PLUGIN_ROOT} is the second case of that shape, and it is here
# because it would otherwise pass this list by accident rather than by
# decision: `\bclaude(?:\.ai|\s+code)\b` needs `.ai` or whitespace-then-`code`
# after `claude`, and CLAUDE_PLUGIN_ROOT gives it `_`. That is a hole, not a
# permission. The token names a mechanism one agent product has, so it is
# allowed only as the FIRST rung of a resolution order whose remaining rungs
# work without it - see test_tools_placeholder_is_defined, which also holds it
# to one occurrence per skill so it cannot spread out of that list.
HARNESS_SPECIFIC = [
    (r"\bTask tool\b", "the Task tool"),
    (r"\bAgent tool\b", "the Agent tool"),
    (r"\bslash command\b", "slash commands"),
    (r"\bMCP\b", "MCP"),
    (r"\bclaude(?:\.ai|\s+code)\b", "Claude Code"),
    (r"\banthropic\b", "Anthropic"),
    (r"\bCLAUDE\.md\b", "CLAUDE.md"),
    (r"\bopenai\b|\bcodex\b|\bchatgpt\b", "an OpenAI product"),
    (r"\bcursor\b|\bcopilot\b", "another editor product"),
]

# Item 57: an example in the toolkit's own text reaches every agent that reads
# it, for the life of the project it came from. Sample-id SHAPES rather than a
# list of specific ids, deliberately - a list only ever catches the one that
# has already been found.
#
# One copy, two corpora: the `.md` files an agent reads, and the engine's own
# prompt tables, which arrive in an agent's context by the same route and are
# indistinguishable from documentation once they are there.
DOC_PATTERNS = [
    (r"\bRMx\d", "a sample id from a real project"),
    (r"\bfive failed\b", "an unrecorded count from a real project"),
]

# ---------------------------------------------------------------------------
# The shipping corpus (specs/defect-item-shape.md 5)
# ---------------------------------------------------------------------------
#
# A SECOND corpus, with its own stated reason, alongside the every-round one
# above. That one is "what reaches an agent's context every round", which is a
# good reason for it and the wrong reason for this: `system-changes.md`,
# `CLAUDE.md`, `README.md`, `TODO.md` and `tests/*.py` never reach an agent
# that way, and on 2026-09-08 a real sample id sat at system-changes.md:851
# while this suite passed 589/589. The reason here is **what reaches GitHub**.
#
# THE EXEMPTION IS THE PART THAT IS EASY TO GET WRONG. Scanning
# `system-changes.md` wholesale would flag every `Specific Example:` bullet -
# which is precisely where a specific is ALLOWED to be. So the scan skips the
# content of those bullets and scans everything else, which makes this guard
# and the `example` field the same rule stated twice: a specific that has been
# corralled stops being a finding, and one in a detail paragraph starts being
# one.
SHIPPING_FILES = ("system-changes.md", "CLAUDE.md", "README.md", "TODO.md",
                  "AGENTS.md", "CONTRIBUTING.md", "history/BUILD-LOG.md")

# Two files have to contain these strings in order to do their job, and a
# guard that fails on its own specification and its own proof is a guard
# nobody can maintain. Named individually WITH the reason, rather than matched
# by a pattern, so that a third exemption is a visible edit somebody has to
# justify - which is the difference between an exemption and a hole.
SHAPE_EXEMPT = {
    "specs/defect-item-shape.md":
        "the spec that defines the shapes; it quotes them to document them",
    "tests/portability.py":
        "this file - test_the_specific_shapes_match_what_they_must carries "
        "the must-match literals, and a shape proved against nothing is a "
        "shape that is not proved",
}

# Identifier families that carry these shapes legitimately, each with the
# reason. `JV`/`GJ`/`RW` are coauthor INITIALS in the tracked-change fixtures,
# not sample ids - a real distinct meaning, and the fixtures are fictional.
#
# `ZQ` and `Xx` are THE TOOLKIT'S RESERVED FICTIONAL SPECIFICS, and they exist
# because of a limit worth stating plainly: this guard matches on SHAPE, so it
# cannot tell a fictional sample id from a real one. spec 5 anticipated that
# ("the remedy is either an unmistakably fictional fixture value or an explicit
# allowlist entry with a reason beside it") and only the second half is
# actually available - a value with no sample-id shape does not test a
# sample-id shape. So: any fixture that needs a specific-shaped token uses
# `ZQ...` for a sample id and `Xx/Yy` for a composition, and nothing else is
# allowlisted. Deliberately not a blanket exemption for `tests/`.
SHIPPING_ALLOW = {
    "JV": "coauthor initials in the docx tracked-change fixtures, fictional",
    "GJ": "the same, in manuscript and outline fixtures",
    "RW": "the same, in a reviewer fixture",
    "ZQ": "the toolkit's reserved fictional SAMPLE ID, for fixtures and "
          "documentation that need one - no element or project uses it",
    "Xx": "the toolkit's reserved fictional COMPOSITION (Xx/Yy) - neither is "
          "a real element symbol",
}

EXAMPLE_BULLET_RE = re.compile(r"^- \*\*Specific Example:\*\*.*$", re.M)


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


def note(text: str) -> None:
    """Something the maintainer should see that is not a pass or a fail.

    The version-bump check REPORTS - a commit is not a release - so its stale
    verdict arrives here rather than as a failure, exactly as `voice` and
    `float lint` do.
    """
    print(f"[note] {text}")


def _load_engine(name: str, filename: str):
    path = os.path.join(ROOT, "tools", filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rel = _load_engine("pwa_release", "release.py")
rem = _load_engine("pwa_remote", "remote.py")


def skip(name: str, why: str) -> None:
    """Announce a check that did NOT run. Never a pass.

    The point of this suite is that a check nobody can see the result of is
    worse than no check, so a file this tree cannot reach is reported as
    unreached and counted separately - not quietly satisfied.
    """
    global SKIP
    SKIP += 1
    print(f"[skip] {name}\n         {why}")


DOCS_REPO = "paper-engine-documentation"
DOCS_URL = "https://github.com/mercer-science/" + DOCS_REPO


def docs_root():
    """Where the user-facing README lives: this repository.

    It lived in a separate documentation repository from 2026-09-17 until
    this one went public on 2026-09-28; everything this suite pins about the
    README - the install commands, the two environment variables, the git
    pull block, the prose promise - is about the page a member actually
    reads, and that page is this repository's README again.
    """
    return ROOT if os.path.isfile(os.path.join(ROOT, "README.md")) else None


NO_DOCS = (
    "no " + DOCS_REPO + " clone, so the README these checks are about cannot "
    "be read from here. Clone " + DOCS_URL + " beside this repository, or set "
    "PAPER_ENGINE_DOCS to it. These checks did NOT run.")


def section(title: str) -> None:
    print(f"\n{title}\n" + "-" * len(title))


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def top_level_imports(path: str) -> set[str]:
    """Modules imported at import time, ignoring anything inside a function.

    A lazy import inside a function is not a dependency for anyone who never
    calls that code path, which is exactly how lxml and pypdf are used here.
    """
    tree = ast.parse(read(path))
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def lazy_imports(path: str) -> set[str]:
    tree = ast.parse(read(path))
    top = {id(n) for n in tree.body}
    found: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in top:
            continue
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found - top_level_imports(path)


def test_engines_are_standalone() -> None:
    section("The engines run on a bare Python install")

    check("there are engines to check", len(ENGINES) >= 6, True)

    for path in ENGINES:
        name = os.path.basename(path)
        used = (top_level_imports(path) | lazy_imports(path)) - STDLIB - LOCAL_MODULES
        undeclared = sorted(m for m in used if m not in DECLARED)
        check(f"{name} pulls in no undeclared package", undeclared, [],
              "a new dependency is a portability decision - put it in AGENTS.md")
        wrong = sorted(m for m in used
                       if m in DECLARED and name not in DECLARED[m])
        check(f"{name} uses only the packages AGENTS.md assigns it", wrong, [])

    # The engines that must run on a bare Python install really do.
    for name in ("scaffold.py", "idea.py", "manuscript.py", "prose.py",
                 "learn.py"):
        path = os.path.join(TOOLS, name)
        hard = top_level_imports(path) - STDLIB
        check(f"{name} needs nothing installed to import", sorted(hard), [],
              "AGENTS.md says these five are standard library only")

    # pubmed.py's requests import is the one hard requirement, and it has to
    # fail with an instruction rather than a traceback.
    src = read(os.path.join(TOOLS, "pubmed.py"))
    check("pubmed.py says how to install requests when it is missing",
          "pip install requests" in src, True)

    agents = read(os.path.join(ROOT, "AGENTS.md"))
    for mod, owners in DECLARED.items():
        check(f"AGENTS.md declares {mod}", mod in agents, True)
        for owner in owners:
            check(f"AGENTS.md ties {mod} to {owner}",
                  bool(re.search(rf"`{mod}`.*?{re.escape(owner)}", agents,
                                 re.DOTALL)), True)


def test_every_engine_answers_help() -> None:
    section("Every engine is usable with no skill file at all")

    for path in ENGINES:
        name = os.path.basename(path)
        run = subprocess.run([sys.executable, path, "--help"],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=60)
        check(f"{name} --help exits 0", run.returncode, 0)
        check(f"{name} --help names its commands",
              bool(re.search(r"\{[a-z-]+,", run.stdout)), True,
              "an agent with no instructions has only --help to go on")


def subcommands(path: str) -> list[str]:
    run = subprocess.run([sys.executable, path, "--help"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=60)
    m = re.search(r"\{([a-z0-9,_-]+)\}", run.stdout)
    return m.group(1).split(",") if m else []


def test_json_on_either_side() -> None:
    section("--json works on both sides of every subcommand")

    # The convention exists because a flag in the trailing position silently
    # errored once. Every caller depends on --json, so this is pinned per
    # subcommand rather than spot-checked.
    for path in ENGINES:
        name = os.path.basename(path)
        cmds = subcommands(path)
        check(f"{name} exposes subcommands", bool(cmds), True)
        for cmd in cmds:
            for order in ("leading", "trailing"):
                argv = ([sys.executable, path, "--json", cmd, "--help"]
                        if order == "leading" else
                        [sys.executable, path, cmd, "--help"])
                run = subprocess.run(argv, capture_output=True, text=True,
                                     encoding="utf-8", errors="replace",
                                     timeout=60)
                check(f"{name} {cmd} accepts --json ({order})",
                      run.returncode == 0 and "--json" in run.stdout, True,
                      run.stderr.strip()[:200])


def test_exit_codes_documented() -> None:
    section("Exit codes mean what AGENTS.md says they mean")

    agents = read(os.path.join(ROOT, "AGENTS.md"))
    for code, meaning in (("0", "clean"), ("1", "findings"), ("2", "refused")):
        check(f"AGENTS.md documents exit {code}",
              bool(re.search(rf"`{code}`\s+{meaning}", agents)), True)

    # The one that is easy to get wrong: an unfinished paper is a normal state,
    # so `completeness` must report 1 and not 2. A 2 reads as "refused" and a
    # caller would stop.
    src = read(os.path.join(TOOLS, "manuscript.py"))
    check("completeness returns 1 for an unfinished paper, never 2",
          'return 0 if res["complete"] else 1' in src, True)


def test_skills_are_harness_neutral() -> None:
    section("No skill assumes one agent product")

    check("every skill directory is present", len(SKILLS), 8,
          f"found {[os.path.basename(os.path.dirname(s)) for s in SKILLS]}",
          )

    for path in SKILL_TEXTS:
        name = os.path.relpath(path, SKILLS_DIR).replace(os.sep, "/")
        text = visiting("harness-specific", path)
        for pattern, label in HARNESS_SPECIFIC:
            hits = re.findall(pattern, text, re.IGNORECASE)
            check(f"{name} does not assume {label}", hits, [],
                  "describe WHAT the module needs, not HOW one product "
                  "provides it")
    for path in SKILLS:
        name = os.path.basename(os.path.dirname(path))
        text = read(path)

        m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        check(f"{name} has frontmatter", bool(m), True)
        if m:
            for key in ("name:", "description:"):
                check(f"{name} frontmatter carries {key}",
                      key in m.group(1), True)
        check(f"{name} still reads as plain markdown after the frontmatter",
              len(text[m.end():].strip()) > 500 if m else False, True,
              "a harness that ignores frontmatter must still get instructions")


def test_the_label_rule_reaches_the_person_writing_the_label() -> None:
    section("A standing preference lives where the work happens (item 83)")

    # The Title Case rule was recorded in the maintainer's own CLAUDE.md,
    # which is the brief for THIS repository: it is not loaded when an agent
    # works inside a project directory and not loaded when a skill runs, so
    # it reached nobody who was actually writing a figure label. Every
    # rendered figure shipped lower case and the user restated the preference
    # after each round. A standing preference that has to be restated is not
    # recorded anywhere that matters.
    wants = [
        os.path.join(SKILLS_DIR, "refine-figure", "SKILL.md"),
        os.path.join(SKILLS_DIR, "create-graphic-figure", "SKILL.md"),
        os.path.join(ROOT, "tools", "project_template", "plan", "figures",
                     "_template", "figure.R"),
        os.path.join(ROOT, "tools", "project_template", "plan", "tables",
                     "_template", "table.R"),
    ]
    for path in wants:
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        text = read(path)
        check(f"{rel} carries the Title Case rule",
              "Title Case" in text, True,
              "it has to be in front of whoever writes the label at the "
              "moment they write it")
        check(f"...and {rel} says what keeps its own casing",
              bool(re.search(r"(?i)unit|symbol|species", text)), True,
              "gene and protein symbols, species names and units are not "
              "retitled, and a rule that does not say so gets applied to "
              "them")


def test_user_state_never_lives_where_an_update_replaces_it() -> None:
    section("Nothing a user supplies lives in the installable tree (item 84)")

    # learn.py learned this first: the learned registry resolves $ENV, then
    # CLAUDE_PLUGIN_DATA, then the toolkit. pubmed.py did not, and wrote the
    # user's NCBI key beside its own script - inside the directory a plugin
    # update replaces. Gitignoring it protected it from being published and
    # not at all from being overwritten, and it failed silently: the key
    # simply stopped being present and every request dropped to the anonymous
    # rate limit.
    #
    # report.py's spool was the second row of this table until upstream
    # reporting was removed on 2026-09-21. The rule outlived it: three
    # engines still keep something the user supplied.
    holders = {
        "learn.py": ("rules_home", "rules.yml"),
        "pubmed.py": ("env_home", "credentials.env"),
        # The fourth, and the first on a SCIENCE path rather than a
        # housekeeping one: `sequence.py fold --submit` needs an account with
        # a folding service, so it inherits the rule from this suite rather
        # than from anyone's memory (user-asks-2 4.6).
        "sequence.py": ("fold_token", "PWA_FOLD_TOKEN"),
    }
    for engine, (fn, kept) in holders.items():
        text = read(os.path.join(ROOT, "tools", engine))
        m = re.search(r"def %s\(.*?\n(?=\S)" % fn, text, re.DOTALL)
        body = m.group(0) if m else ""
        check(f"{engine} resolves where it keeps {kept} in one place",
              bool(body), True, f"no {fn}() found")
        check(f"...through $CLAUDE_PLUGIN_DATA before the toolkit",
              "CLAUDE_PLUGIN_DATA" in body, True,
              "the toolkit is the LAST fallback, so a source checkout is "
              "unchanged and an installed copy keeps the user's file outside "
              "the tree an update replaces")
        check(f"...and reports which of the three it used",
              '"plugin_data"' in body, True,
              "a path with no source is a path nobody can check")

    # The specific regression: a credentials path built from the script's own
    # location and used as the answer.
    pub = read(os.path.join(ROOT, "tools", "pubmed.py"))
    beside = re.findall(
        r"^ENV_PATH\s*=\s*os\.path\.join\(os\.path\.dirname", pub, re.M)
    check("pubmed.py does not resolve its credentials file from __file__",
          beside, [],
          "that is the line item 84 is about; env_home() is the resolver now")
    check("...and something looks for a key left stranded in the old place",
          "def stranded_env" in pub, True,
          "a credentials file that exists, is not being read, and says "
          "nothing is the silent half of this defect")


def test_sequence_refusals_reach_the_skill() -> None:
    section("Sequence evidence is never evidence for a gap (user-asks-2 4.7)")

    # The rule lives in the ENGINE's notes and in the SKILL's body, and both
    # halves matter: the engine's `notes` reach a JSON consumer, and the
    # skill's sentences reach the agent holding the conversation. A rule that
    # only one of them carries is a rule that survives exactly one refactor.
    #
    # This is external-services 4.6 extended to sequence data, and the reason
    # it gets its own check is that it is the single most likely way this
    # engine gets misused: "no homolog was found, therefore this is
    # unstudied" is the PubMed-found-nothing fallacy with a different index.
    engine = read(os.path.join(ROOT, "tools", "sequence.py"))
    skill = read(os.path.join(ROOT, "skills", "idea-generation", "SKILL.md"))

    check("the engine says a miss is not a gap, in its own notes",
          "not evidence of a gap" in engine, True)
    check("...and the skill says it where an agent will read it",
          re.search(r"(?i)not evidence of (a )?novelty|never.{0,20}evidence "
                    r"for a gap", skill) is not None, True)
    check("...both naming feasibility and methods as what it IS evidence for",
          "feasibility" in engine.lower() and "feasibility" in skill.lower(),
          True)
    check("the skill says it must not run unasked - not a network call per "
          "noun", "must not run unasked" in skill.lower(), True)
    check("...and that it seeds questions rather than answering them",
          "seeds the questions" in skill.lower()
          or "not eleven findings" in skill, True)
    check("...and that a sequence hit is not a citation",
          "not a citation" in skill.lower(), True)
    check("the skill calls the engine through the <tools> placeholder, like "
          "every other skill here",
          "<tools>/sequence.py" in skill, True)
    check("...and never with a literal path",
          "tools/sequence.py" in skill.replace("<tools>/sequence.py", ""),
          False)


def test_context_isolation_fallback() -> None:
    section("A skill that needs isolation says what to do without it")

    # This is the one capability whose absence changes the OUTPUT rather than
    # stopping the run, and it does so silently: a stats-check that has read
    # the hypothesis still produces a confident report. So any skill that leans
    # on context isolation has to name the fallback in its own text, not rely
    # on the reader having found AGENTS.md.
    for path in SKILLS:
        name = os.path.basename(os.path.dirname(path))
        text = read(path)
        if not re.search(r"\bsub-?agents?\b|allow-list", text, re.IGNORECASE):
            continue
        # A skill that says outright it needs no isolation is not leaning on
        # any, and demanding it document a fallback for a capability it never
        # uses is the check firing on its own vocabulary. The disclaimer has to
        # be explicit: merely not mentioning sub-agents is already the skip
        # above, and this is the narrower "considered it, do not need it" case.
        if re.search(r"needs? no sub-?agent", text, re.IGNORECASE):
            check(f"{name} says plainly that it needs no isolated context",
                  bool(re.search(r"no isolated context", text, re.IGNORECASE)),
                  True, "say it once, unambiguously, or the reader cannot "
                        "tell it from an omission")
            continue
        check(f"{name} names the fallback for a harness without sub-agents",
              bool(re.search(r"separate (invocation|session)", text,
                             re.IGNORECASE)), True,
              "say what to do instead, or the isolation quietly stops "
              "happening")
        check(f"{name} says to skip rather than run a contaminated check",
              bool(re.search(r"skip", text, re.IGNORECASE)), True)

    agents = read(os.path.join(ROOT, "AGENTS.md"))
    check("AGENTS.md explains why the isolation matters",
          "silently" in agents or "nothing visibly fails" in agents, True)


def test_plugin_manifest_matches_the_tree() -> None:
    section("The plugin manifest ships exactly the skills that exist")

    # `plugin.json`'s `skills` field ADDS to the default and accepts any
    # directory that already holds a SKILL.md, so the six live together under
    # `skills/` and are named here one by one. Nesting them keeps the toolkit
    # root readable; naming them keeps one SKILL.md per skill, read
    # identically by the clone route and the plugin route
    # (specs/plugin-packaging.md 1).
    #
    # The cost of an explicit list is that it can fall out of step with the
    # directories, and a skill that is not shipped is not an error anybody
    # sees. So it is asserted in BOTH directions against the same glob that
    # backs `len(SKILLS) == 6` - the same rule as tools/project_template/ and
    # its manifest in scaffold.py.
    path = os.path.join(ROOT, ".claude-plugin", "plugin.json")
    check(".claude-plugin/plugin.json exists", os.path.isfile(path), True,
          "the plugin route needs a manifest at the marketplace root")
    if not os.path.isfile(path):
        return

    try:
        manifest = json.loads(read(path))
    except json.JSONDecodeError as exc:
        check("plugin.json parses", str(exc), "")
        return

    check("plugin.json names the plugin", manifest.get("name"), "paper-engine")
    check("...in kebab-case, as the schema requires",
          bool(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*",
                            str(manifest.get("name", "")))), True)
    check("plugin.json carries a version, so installs are pinned",
          bool(re.fullmatch(r"\d+\.\d+\.\d+",
                            str(manifest.get("version", "")))), True,
          "an unpinned plugin tracking a branch changes under a member "
          "mid-paper")

    # Every component field either REPLACES or MERGES a default; `skills` is
    # the one that adds. Declaring an empty `commands`/`agents`/`hooks` is how
    # a default gets replaced with nothing, so the toolkit declares none of
    # them (specs/plugin-packaging.md 2.1).
    for field in ("commands", "agents", "hooks", "mcpServers", "lspServers"):
        check(f"plugin.json declares no {field}", field in manifest, False,
              "an empty component field replaces the harness default with "
              "nothing; omitting it leaves the default alone")

    listed = manifest.get("skills", [])
    check("plugin.json lists skills explicitly, not as \"./skills\"",
          isinstance(listed, list), True,
          'a bare "./skills" sweeps any future directory holding a '
          "SKILL.md into the plugin without anyone deciding to ship it")
    if not isinstance(listed, list):
        return

    on_disk = sorted(os.path.basename(os.path.dirname(p)) for p in SKILLS)
    # Every entry is `./skills/<name>`; compare on the name, and let the
    # prefix be checked separately so a path that has drifted out of
    # `skills/` fails as a bad path rather than as a missing skill.
    in_manifest = sorted(str(entry).rsplit("/", 1)[-1] for entry in listed)

    check("every skill on disk is in the manifest",
          [n for n in on_disk if n not in in_manifest], [],
          "a skill missing from the manifest ships to nobody, and nothing "
          "reports it")
    check("every skill in the manifest is on disk",
          [n for n in in_manifest if n not in on_disk], [],
          "an install fails, or silently skips, on a path that is not there")
    check("...and the manifest paths are relative and do not escape the root",
          [e for e in listed
           if not str(e).startswith("./skills/") or ".." in str(e)], [],
          "a skill is shipped from skills/, one directory per skill")

    for name in on_disk:
        text = read(os.path.join(SKILLS_DIR, name, "SKILL.md"))
        m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        front = re.search(r"^name:\s*(\S+)\s*$", m.group(1), re.M) if m else None
        # The frontmatter `name` governs the invocation name, so a directory
        # and a frontmatter that disagree ship a skill nobody can call by the
        # name the manifest advertises.
        check(f"{name} frontmatter name matches its directory",
              front.group(1) if front else None, name)


def test_a_report_is_not_an_answer() -> None:
    """prose 12.5 test 7 - the false claim, banned. The true one is code.

    What was objected to: "Giving a user report 'This is correct, but they
    are not very readable' is super unhelpful. The whole point is to draft
    the paper so it's readable." Two things were wrong at once - the engine
    reported instead of fixing, and the documentation presented that absence
    as a feature: *"These report. They never block."* Both were fixed. This
    guards the second.

    **Rewritten 2026-09-18. It used to require three exact sentences to be
    present in the public README, and that was wrong three ways.**

    - It pinned the WORDING and not the truth. Deleting the paragraph failed;
      saying the same thing in better words also failed; writing something
      false while keeping the three strings passed. That is not a test of
      anything.
    - It pinned prose in a DIFFERENT REPOSITORY - one whose whole purpose is
      to be edited - so an ordinary edit to a page broke the engine's suite.
      It fired exactly that way on 2026-09-17 when the readability section
      was shortened on purpose.
    - **The three claims were already pinned against the code**, which is
      where a claim about behaviour belongs. Every one of them, in
      `tests/manuscript.py`:

        "fixed in the round that found them"
            `test_comprehension_fix_runs` - module 1c is handed
            `reports/rN/reading.json`, which is what makes the fix land
            before any checker reads the draft.
        "no prose measurement can fail a build"
            `test_how_it_reads_reaches_the_build` - 500 findings are planted
            into the reading block and the verdict is asserted unchanged.
        "withheld from the rewrite"
            `test_comprehension_fix_runs` - the densities are in the module's
            DENIAL list and absent from its payload, over planted prose that
            proves there was something to withhold.

    So the presence half is gone and nothing is lost by it. What stays is the
    ban, over the pages THIS repository ships, because a false claim creeping
    back is a real regression and nothing else watches for it.
    """
    section("a prose report is acted on, and the docs do not claim otherwise")

    banned = ("they never block", "these report. they never block",
              "report. they never")

    # In-repo only. The public page lives in the documentation repository and
    # is the user's to edit; a test here that dictated its sentences made an
    # ordinary rewrite look like an engine failure.
    pages = [
        (os.path.join("help", "instructions.md"),
         os.path.join(ROOT, "help", "instructions.md")),
        ("README.md", os.path.join(ROOT, "README.md")),
        (os.path.join("skills", "writing-engine", "SKILL.md"),
         os.path.join(ROOT, "skills", "writing-engine", "SKILL.md")),
        (os.path.join("skills", "writing-engine", "reference",
                      "prose-rules.md"),
         os.path.join(ROOT, "skills", "writing-engine", "reference",
                      "prose-rules.md")),
    ]

    # A page that is not there is REPORTED, never raised. The first version of
    # this rewrite opened each path directly, and when `skills/writing-engine/`
    # was moved out from under it the whole suite died on a FileNotFoundError
    # part-way through - so every check after this one silently did not run,
    # which is item 80's shape inside the file that exists to catch it.
    #
    # The corpus is asserted non-empty for the same reason `_shipping_corpus`
    # is: a scan that finds nothing to scan passes for free.
    present = [(rel, p) for rel, p in pages if os.path.isfile(p)]
    for rel, path in pages:
        if not os.path.isfile(path):
            note(f"{rel} is not on disk, so it was not scanned")
    check("there is something to scan for the banned wording",
          bool(present), True,
          "every page this check knows about is missing")

    for rel, path in present:
        text = read(path).lower()
        for phrase in banned:
            check(f"{rel} does not present 'nothing acts on this' as a "
                  f"feature: {phrase!r}",
                  phrase in text, False)

    # The claims themselves, asserted where they are true: in the code. Named
    # rather than re-implemented - a second copy of an assertion is a second
    # thing to keep in step, and this file has no project to run them against.
    for suite, name in (
        ("tests/manuscript.py", "test_comprehension_fix_runs"),
        ("tests/manuscript.py", "test_how_it_reads_reaches_the_build"),
    ):
        body = read(os.path.join(ROOT, suite))
        check(f"{name} still exists to pin the behaviour this wording "
              f"describes", ("def %s(" % name) in body, True)


def test_readme_documents_both_routes() -> None:
    section("README's install instructions match the manifests they describe")

    # Both distribution routes stay supported, so there are two sets of
    # install instructions to keep true and one of them is now generated from
    # a manifest. A member types the names out of README; if a rename lands in
    # plugin.json and not here, the command they type fails with "no such
    # plugin" and nothing in the toolkit noticed.
    docs = docs_root()
    if docs is None:
        skip("README's install claims against the manifests", NO_DOCS)
        return
    readme = read(os.path.join(docs, "README.md"))

    manifest_path = os.path.join(ROOT, ".claude-plugin", "plugin.json")
    market_path = os.path.join(ROOT, ".claude-plugin", "marketplace.json")
    if not (os.path.isfile(manifest_path) and os.path.isfile(market_path)):
        check("both manifests exist before README is checked against them",
              False, True)
        return
    plugin = json.loads(read(manifest_path))
    market = json.loads(read(market_path))

    invocation = f"{plugin['name']}@{market['name']}"
    check("README names the plugin exactly as the manifests do",
          invocation in readme, True,
          f"expected the string {invocation!r} - a member types this")

    # Neither of these is guessable and both are silent when missing: the
    # first fails an SSH clone on a repo the member can genuinely read, the
    # second lets a private marketplace stop refreshing without saying so.
    # They are steps, not troubleshooting (specs/plugin-packaging.md 6.1).
    for var in ("CLAUDE_CODE_PLUGIN_PREFER_HTTPS",
                "CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE"):
        check(f"README gives {var} as a step", var in readme, True)

    # TODO.md 0: the rename happened, install was not re-run, and all six
    # pointers were dead for days while sessions kept writing papers without
    # the skills. `status` is the only thing that reports it, so it belongs
    # beside `git pull` rather than in a later section.
    # Any mention of `git pull` will do, so long as ONE of them carries both
    # commands within reach - the file mentions it twice now, once in the
    # route table and once in the block that does the work.
    pulls = [m.start() for m in re.finditer(r"git pull", readme)]
    check("README's update instructions include git pull", bool(pulls), True)
    check("...with install and status right beside it",
          any("install_skills.py install" in readme[p:p + 400]
              and "install_skills.py status" in readme[p:p + 400]
              for p in pulls), True,
          "a harness that cannot resolve a skill does not report a "
          "missing skill - it writes the paper without it")

    # Discovery is automatic for the clone route and explicit for the plugin
    # route, so adding a skill touches one more file than the instruction used
    # to say.
    #
    # THE INSTRUCTION MOVED AND THE ASSERTION DID NOT CHANGE. `README.md` is
    # user-facing now - install it, say what you want - and everything a
    # maintainer needs is `CONTRIBUTING.md`. What matters is that the
    # instruction exists somewhere a person adding a skill will read, and that
    # it names the manifest; pinning it to README's old heading would have
    # failed the move rather than the mistake. The trail from README is
    # checked too, because an instruction nobody can find is the same failure
    # as an instruction nobody wrote.
    check("README points at CONTRIBUTING.md for the maintainer half",
          "CONTRIBUTING.md" in readme, True,
          "README is user-facing; the instruction lives in CONTRIBUTING.md")
    contributing_path = os.path.join(ROOT, "CONTRIBUTING.md")
    check("CONTRIBUTING.md exists", os.path.isfile(contributing_path), True,
          contributing_path)
    if os.path.isfile(contributing_path):
        contributing = read(contributing_path)
        add = contributing.find("Adding a skill")
        check("CONTRIBUTING's 'Adding a skill' names the plugin manifest",
              add != -1 and "plugin.json" in contributing[add:add + 1200],
              True,
              "an unlisted skill ships to nobody, and only the suite notices")


def test_tools_placeholder_is_defined() -> None:
    section("Every skill defines the placeholder it calls the engines through")

    # Items 64 and 65. No skill body writes `python tools/x.py`; every engine
    # call is `python <tools>/...` against a placeholder the skill resolves
    # for itself. That is what let the plugin add one rung rather than rewrite
    # call sites - but only if the block is actually there, and flag-resolver
    # used the placeholder twice with no block at all.
    for path in SKILL_TEXTS:
        name = os.path.relpath(path, SKILLS_DIR).replace(os.sep, "/")
        text = visiting("tools-placeholder", path)
        if "<tools>" not in text:
            continue
        # A reference file may USE the placeholder without redefining it -
        # the spine is read on every invocation and states the resolution
        # once. What is not allowed is a file using it where NOTHING in its
        # skill says how to resolve it.
        check(f"{name} defines <tools> before calling engines through it",
              bool(re.search(r"Resolve\s+`<tools>`",
                             read(owning_skill(path)))), True,
              "a placeholder with no rule resolves to whatever the reader "
              "guesses, and a guessed engine path is a skill doing the work "
              "by hand")
        if os.path.basename(path) != "SKILL.md":
            continue

        # Item 65: rung 2 named the author's own folder layout, which is true
        # on one machine and false for every member the toolkit ships to. The
        # rule is rename it where it is an instruction, keep it where it is a
        # record; a resolution order is an instruction.
        layout = re.findall(r"Paper Engine/tools|under the user's\s+`?Research",
                            text)
        check(f"{name} names no folder layout it cannot know", layout, [],
              "resolve from the skill's own directory, the harness, or the "
              "pointer that named the root - never from a directory tree "
              "that happens to exist on the author's machine")

    # ${CLAUDE_PLUGIN_ROOT} is vocabulary naming a mechanism one agent product
    # has, and it is exactly what HARNESS_SPECIFIC exists to keep out - but it
    # does not trip that list, because `\bclaude(?:\.ai|\s+code)\b` needs `.ai`
    # or whitespace-then-`code` after `claude` and gets `_`. That is a hole,
    # not a permission. It is closed the way "sub-agent" already is: the token
    # is allowed only where the same file states the resolution that does not
    # need it.
    for path in SKILL_TEXTS:
        name = os.path.relpath(path, SKILLS_DIR).replace(os.sep, "/")
        text = visiting("plugin-token", path)
        if "CLAUDE_PLUGIN_ROOT" not in text:
            continue
        check(f"{name} names a resolution that does not need the plugin token",
              bool(re.search(r"relative to this skill's own directory",
                             read(owning_skill(path)))),
              True, "a skill may say WHAT it needs; it may not assume the "
                    "only harness that provides it")
        check(f"...and {name} uses the token once, inside that ordered list",
              len(re.findall(r"CLAUDE_PLUGIN_ROOT", text)), 1)


def test_agents_md_is_current() -> None:
    section("AGENTS.md still describes what is actually here")

    agents = read(os.path.join(ROOT, "AGENTS.md"))
    for path in ENGINES:
        name = os.path.basename(path)
        if name.startswith("_"):
            continue
        check(f"AGENTS.md lists tools/{name}", f"tools/{name}" in agents, True,
              "a new engine needs a line in the portable entry point")
    for path in SKILLS:
        name = os.path.basename(os.path.dirname(path))
        check(f"AGENTS.md lists the {name} skill", name in agents, True)

    suites = sorted(os.path.basename(p) for p in
                    glob.glob(os.path.join(HERE, "*.py"))
                    if not os.path.basename(p).startswith(("make_", "_")))
    for suite in suites:
        check(f"AGENTS.md names tests/{suite}", f"tests/{suite}" in agents,
              True, "a suite nobody is told to run does not get run")


def test_no_absolute_paths_baked_in() -> None:
    section("Nothing is hard-coded to this machine")

    for path in ENGINES + SKILLS:
        name = os.path.basename(path)
        text = read(path)
        # The lookbehind is what keeps this honest: without it "https://" and
        # an escape sequence inside a string literal ("single:\ndouble") both
        # read as drive-letter paths, and the check cries wolf until someone
        # deletes it for being useless. C:\Program Files\R is the one
        # documented exception - R really is installed off PATH here, and the
        # probe is a fallback rather than a requirement.
        bad = [m for m in re.findall(
                   r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][\w \\/.-]{6,}", text)
               if "Program Files" not in m]
        check(f"{name} has no machine-specific absolute path", bad, [])

        user = re.findall(r"[Uu]sers[\\/](?!<)[A-Za-z]+", text)
        check(f"{name} names no user's home directory", user, [])


def test_documentation_names_nothing_real() -> None:
    section("A real project's facts are not quoted as examples (item 58)")

    # The orchestrator reads SKILL.md every round. A worked example quoting a
    # real sample id and an unrecorded fact out of one of this group's own
    # projects therefore lands in its context on every run of that project,
    # from where it can be passed into a drafter's brief as though it had
    # been recorded - which is exactly what happened once, and the drafter
    # was right to refuse it.
    #
    # The patterns live at module scope now, because the corpus grew: the
    # engine's own prompt tables reach an agent by the same route these files
    # do, and are scanned with the same patterns rather than a second copy of
    # them (see test_the_engines_own_prompt_text_is_documentation).
    # `history/` is globbed beside `specs/` because a spec that becomes a
    # record does not stop reaching GitHub - `build-order.md` and
    # `build-order-2026-09-07.md` moved there on 2026-09-24 and took six
    # checks of this suite with them until this line was widened. The same
    # trap as the SKILL.md split: a suite pointed at the old path reports the
    # same green over less text.
    for path in SKILL_TEXTS + sorted(
            glob.glob(os.path.join(ROOT, "specs", "*.md"))
            + glob.glob(os.path.join(ROOT, "history", "*.md"))):
        name = os.path.relpath(path, ROOT).replace(os.sep, "/")
        text = visiting("documentation", path)
        for pat, why in DOC_PATTERNS:
            hits = re.findall(pat, text)
            check(f"{name} quotes no {why}", hits, [],
                  "examples in the toolkit's own documentation reach every "
                  "agent that reads it, for the life of the project they "
                  "came from - so they name nothing real")


def _shape_contract(engine: str = "manuscript") -> dict:
    """The contract block, extracted and executed in its own namespace.

    Executing it rather than importing `manuscript` is deliberate twice over:
    this suite imports no engine anywhere (it reads the tree as text), and
    running the block standalone PROVES it is self-contained - a contract that
    only works because of something else in its file is not a contract that
    can be copied into a second file.
    """
    head = "# --- SPECIFIC SHAPES CONTRACT ---"
    tail = "# --- END SPECIFIC SHAPES CONTRACT ---"
    text = read(os.path.join(ROOT, "tools", engine + ".py"))
    i, j = text.find(head), text.find(tail)
    ns: dict = {"re": re}
    if i >= 0 and j > i:
        exec(compile(text[i:j + len(tail)], "<contract>", "exec"), ns)
    return ns


def _shipping_corpus() -> list[str]:
    out = [os.path.join(ROOT, n) for n in SHIPPING_FILES
           if os.path.exists(os.path.join(ROOT, n))]
    out += sorted(glob.glob(os.path.join(ROOT, "tests", "*.py")))
    out += sorted(glob.glob(os.path.join(ROOT, "tools", "*.md")))
    out += sorted(glob.glob(os.path.join(ROOT, "specs", "*.md")))
    # See the note in test_documentation_quotes_nothing_real: a record in
    # `history/` reaches GitHub exactly as a spec does.
    out += sorted(glob.glob(os.path.join(ROOT, "history", "*.md")))
    out += SKILLS
    return out


def test_shipping_corpus_carries_no_project_specifics() -> None:
    section("No project specific reaches GitHub outside a Specific Example "
            "(defect-item-shape 5)")

    ms = _shape_contract()
    files = _shipping_corpus()

    # The corpus itself is asserted, not just its contents. A corpus that
    # silently shrinks is a guard that silently stops guarding - the same
    # shape as the existing "every module has to be in the scanned set"
    # check, and the exact failure that let a sample id sit in this tree for
    # a month.
    rel = {os.path.relpath(p, ROOT).replace(os.sep, "/") for p in files}
    check("the shipping corpus is non-empty", bool(files), True)
    # system-changes.md and TODO.md are local to each machine now, so they
    # are scanned when present and not required.
    for must in ("CLAUDE.md", "README.md"):
        check(f"the shipping corpus contains {must}", must in rel, True)

    for path in files:
        name = os.path.relpath(path, ROOT).replace(os.sep, "/")
        if name in SHAPE_EXEMPT:
            continue                      # see SHAPE_EXEMPT for the reason
        text = read(path)
        # The exemption: blank the Specific Example bullets and scan the rest.
        text = EXAMPLE_BULLET_RE.sub("- **Specific Example:** <redacted>", text)
        # REFUSE tier only, and this is the measurement doing the deciding
        # rather than a preference. Over these 54 files the refuse shapes hit
        # 16 things; the report shapes hit 90, every one of them a legitimate
        # illustrative value - `40 nm` in a flag example, `400 C` in a prose
        # spec, `9 Torr` in a learn fixture. Gating on those would need a
        # ~40-entry allowlist, which is the list-of-known-strings this spec
        # refuses to become. They lint at the writer and redact on the way
        # upstream instead, which is the honest ceiling for a shape whose
        # false positives outnumber its true ones 6 to 1.
        hits = [t for t, _why, _tier in ms["specific_hits"](text, "refuse")
                if not any(t.startswith(k) for k in SHIPPING_ALLOW)]
        check(f"{name} names no project specific", sorted(set(hits)), [],
              "an item's description is generic - a statement about the "
              "engine. A project specific belongs in a `Specific Example:` "
              "bullet, which is the one place this guard exempts")


def test_the_specific_shapes_match_what_they_must() -> None:
    section("Every specific shape matches a string it is meant to catch")

    # THE POINT OF THIS TEST. A pattern that has never matched anything is
    # indistinguishable from a pattern that is working, and the tree is clean,
    # so the scan above passes either way. This is what tells the difference.
    # It is not hypothetical: the throwaway inventory that found the 142 scrub
    # candidates used `\bCu\(0\)\b`, which can NEVER match - `\b` after `)`
    # needs a word character and the next character is a space - so one real
    # hit was missed and found only by re-reading the fixture by eye.
    ms = _shape_contract()
    must_match = [
        ("RMx1 was the sample", "a sample-id-shaped token"),
        ("run RMa-12 again", "a sample-id-shaped token"),
        ("Cu(0) appears before the framework is gone", "an oxidation state"),
        ("Fe(III) oxide", "an oxidation state"),
        ("a Cu/Zn alloy", "a composition"),
        ("4.144 um across", "a measurement with a unit"),
        ("30 kV", "a measurement with a unit"),
        ("a 1:3 mix", "a bare ratio"),
    ]
    for text, why in must_match:
        whys = [w for _t, w, _tier in ms["specific_hits"](text)]
        check(f"{text!r} is caught as {why}", why in whys, True,
              "a shape that matches nothing passes the tree scan silently")

    # And what it must NOT catch, which is the half that decides whether the
    # guard is usable at all. Measured: a general sample-id shape hits 182
    # things in this tree and almost every one is the toolkit's own
    # vocabulary. What separates them is a separator or a lowercase run
    # between the letters and the digits.
    must_miss = ["CH3 group", "NH2 terminus", "IFD0 tag", "BLE001 stub",
                 "LR-001 fired", "SHA-256", "UTF-8", "EMD-3061", "HKUST-1",
                 "Fig01", "A/B test", "I/O bound", "at 06:49", "12:00 noon"]
    for text in must_miss:
        check(f"{text!r} is not a specific", ms["specific_hits"](text), [],
              "a false positive here is a refusal an agent routes around")


def test_the_shape_contract_has_one_copy() -> None:
    section("The specific-shapes contract lives in exactly one engine")

    # This used to compare two byte-identical copies, in manuscript.py and in
    # report.py, because repetition is only safe while something compares the
    # copies. Upstream reporting was removed on 2026-09-21 and report.py with
    # it, so the thing to hold is the opposite one: that a second copy has not
    # quietly reappeared somewhere. A contract with two owners and no
    # comparison is how CAPTION_HEADING_RE and read_flat_yml each grew a
    # weaker twin.
    head = "# --- SPECIFIC SHAPES CONTRACT ---"
    carriers = [os.path.basename(p)
                for p in sorted(glob.glob(os.path.join(ROOT, "tools", "*.py")))
                if head in read(p)]
    check("exactly one engine carries the contract", carriers,
          ["manuscript.py"],
          "a second copy needs a test comparing it, and there is none")


# A quoted span this long inside a denial reason is a sentence, and the only
# sentences worth quoting at this length come from a manuscript. The
# precedent is the QUOTE_LIMIT of 120 characters that report.py applied to an
# agent-origin defect detail before that engine was removed on 2026-09-21
# (specs/distribution.md 4); the threshold is far tighter here
# because a denial reason is TEMPLATE text, written once by a person about the
# engine. Every legitimate quoted span in the table today is a file name, a
# heading or a flag - the longest is 24 characters.
DENIAL_QUOTE_LIMIT = 40

# The fields of AGENT_MODULES whose text is emitted verbatim into an isolated
# agent's prompt on every round. `denied` carries tuples, and both halves
# reach the prompt: the thing withheld and the reason it is.
PROMPT_FIELDS = ("role", "why", "task", "denied")

QUOTE_PATTERNS = (re.compile(r'"([^"\n]+)"'),
                  re.compile("“([^”\n]+)”"),
                  re.compile(r"`([^`\n]+)`"))


def _string_constants(node: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _agent_prompt_corpus() -> tuple[dict, list[tuple[str, str, str]]]:
    """Every string in manuscript.py that reaches an isolated agent's prompt.

    Read out of the table with `ast` rather than grepped for: a blanket grep
    of `tools/*.py` would scan the detection patterns themselves, which is how
    a check earns being deleted instead of fixed.

    Derived from AGENT_MODULES, so a module added later is covered without
    an edit here - which is the property the corpus assertion below pins.
    """
    tree = ast.parse(read(os.path.join(TOOLS, "manuscript.py")))
    by_module: dict[str, list[tuple[str, str]]] = {}
    extra: list[tuple[str, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [getattr(t, "id", "") for t in node.targets]
        elif isinstance(node, ast.AnnAssign):
            names = [getattr(node.target, "id", "")]
        else:
            continue
        if "AGENT_MODULES" not in names or not isinstance(node.value, ast.Dict):
            continue
        for key, spec in zip(node.value.keys, node.value.values):
            module = getattr(key, "value", "")
            if not isinstance(module, str) or not isinstance(spec, ast.Dict):
                continue
            got: list[tuple[str, str]] = []
            for field, value in zip(spec.keys, spec.values):
                if getattr(field, "value", "") in PROMPT_FIELDS:
                    got += [(getattr(field, "value", ""), s)
                            for s in _string_constants(value)]
            by_module[module] = got

    # The `fallback` and `spawn` text is composed in agent_brief rather than
    # sitting in the table, and it reaches every prompt the table's entries
    # do - so it is part of the same corpus.
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "agent_brief":
            continue
        for ret in ast.walk(node):
            if not isinstance(ret, ast.Return) or \
                    not isinstance(ret.value, ast.Dict):
                continue
            for field, value in zip(ret.value.keys, ret.value.values):
                name = getattr(field, "value", "")
                if name in ("fallback", "spawn"):
                    extra += [("agent_brief", name, s)
                              for s in _string_constants(value)]
    return by_module, extra


def _long_quoted_spans(where: str, field: str,
                       text: str) -> list[tuple[str, str, str]]:
    out = []
    for pat in QUOTE_PATTERNS:
        for m in pat.finditer(text):
            if len(m.group(1)) >= DENIAL_QUOTE_LIMIT:
                out.append((where, field, m.group(1)))
    return out


def test_the_engines_own_prompt_text_is_documentation() -> None:
    section("A string the engine puts in an agent's context is held to "
            "documentation's rules (engine-self-report 3)")

    # Item 57's corpus was SKILLS + specs/*.md, and it did not scan tools/.
    # But `AGENT_MODULES` reaches an isolated agent by exactly the same route
    # as SKILL.md does, with exactly the same consequence, and from inside the
    # agent the two are indistinguishable: both arrive as text it is expected
    # to act on. Guarding one and not the other guards the half that happens
    # to live in `.md` files.
    corpus, extra = _agent_prompt_corpus()
    # Asked of the engine rather than read out of the same table twice: this
    # is the list of modules a brief will actually be built for.
    listed = subprocess.run(
        [sys.executable, os.path.join(TOOLS, "manuscript.py"), "agent-brief",
         ROOT, "--list", "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    modules = [m["module"] for m in
               json.loads(listed.stdout or "{}").get("modules", [])
               if m.get("runs_as_agent")]
    check("the corpus is the module table itself, not a typed-out list",
          sorted(corpus), sorted(modules),
          "every module agent-brief will build a prompt for has to be in the "
          "scanned set, or the guard silently stops covering one")
    check("...and every one of them contributes prompt text",
          [m for m, got in corpus.items()
           if not any(f == "why" for f, _ in got)
           or not any(f == "task" for f, _ in got)], [])
    check("...and the fallback and spawn text is in it too",
          sorted({f for _, f, _ in extra}), ["fallback", "spawn"])

    scanned = 0
    for module, got in sorted(corpus.items()):
        scanned += len(got)
        for pat, why in DOC_PATTERNS:
            hits = ["%s: %s" % (field, m) for field, text in got
                    for m in re.findall(pat, text)]
            check(f"AGENT_MODULES[{module}] quotes no {why}", hits, [],
                  "these strings are emitted verbatim into an isolated "
                  "agent's prompt on every round")
    for pat, why in DOC_PATTERNS:
        hits = ["%s %s: %s" % (where, field, m) for where, field, text in extra
                for m in re.findall(pat, text)]
        check(f"the fallback and spawn text quotes no {why}", hits, [])
    check("the scan covered every prompt string in the table",
          scanned >= 40, True, f"{scanned} strings")

    # The lint. It REPORTS and the check exits 0: the remedy is to rewrite an
    # explanation, and a check that fails a build over prose style is a check
    # somebody routes around (prose 0.2).
    found: list[tuple[str, str, str]] = []
    for module, got in sorted(corpus.items()):
        for field, text in got:
            found += _long_quoted_spans(module, field, text)
    for where, field, text in extra:
        found += _long_quoted_spans(where, field, text)
    for module, field, span in found:
        print(f"  REPORT  AGENT_MODULES[{module}].{field} carries a "
              f"{len(span)}-character quoted span - a denial reason is "
              f"template text, and a span this long is a probable real "
              f"sentence: {span[:60]!r}")
    check("the quoted-span lint reports and does not refuse",
          isinstance(found, list), True,
          f"{len(found)} span(s) reported; the check still passes")
    # ...and it is a lint that works, asserted against a planted case rather
    # than against the absence of one.
    planted = _long_quoted_spans(
        "stats-check", "denied",
        'the one place the hoped-for result is written in plain words - '
        '"the thermophile looks tighter than the mesophile, as expected"')
    check("a long quoted span IS caught", len(planted), 1, str(planted))
    check("...and reported with the module and the field it is in",
          [p[:2] for p in planted], [("stats-check", "denied")])
    check("...while a file name in backticks is not",
          _long_quoted_spans("outline", "task", "the `# Structure` region of "
                             "`plan/outline.md`, and `plan/README.md`"), [])


# There was a `test_no_suite_writes_the_live_spool` here, and it is worth
# knowing why it is not any more. Measured 2026-09-14: `tests/manuscript.py`
# drove `log-issue` dozens of times per run with fictional codes, and every
# record landed in the toolkit's own `.spool/pending` because that is where
# report.py resolved to when nothing overrode it. 28 items had accumulated,
# eight of them that suite's probes and three more real codes whose run
# counts it had inflated - one to 697. The first push would have carried test
# fixtures into a repository other people read.
#
# Upstream reporting was removed on 2026-09-21: there is no spool, no push
# and no repository, so the leak has no destination and the guard has nothing
# to hold. What replaced it is narrower and still real - a suite must not
# write the LEDGER either - and that is
# `test_no_suite_writes_the_live_defect_ledger` below.


def test_no_suite_writes_the_live_defect_ledger() -> None:
    section("A suite that files a defect files it somewhere temporary")

    # The spool is gone; system-changes.md is not, and it is now the only
    # record a stray `log-issue` can reach. A suite that files a fictional
    # code into the shipping ledger inflates a run count in the one file
    # whose whole value is that its history is true - the same damage as
    # before, one file along.
    for path in sorted(glob.glob(os.path.join(HERE, "*.py"))):
        text = read(path)
        name = os.path.basename(path)
        if "log-issue" not in text:
            continue
        check(f"{name} files defects into a ledger of its own",
              ("system_changes_path" in text or "system-changes.md" in text),
              True,
              "it drives log-issue, so it must name the temporary file it "
              "writes rather than letting the engine resolve the shipping "
              "one")


def test_no_module_is_given_a_path_under_obsolete() -> None:
    section("obsolete/ is a record and never an input (round-archive 1.1)")

    # The archive can be complete without being believed, and that is what
    # makes keeping every closed round the right default. Exactly ONE reader
    # is permitted to treat `obsolete/` as input - `retention_baseline`, which
    # holds round N's prose against round N-1's - and it is a postcondition
    # check rather than a source: it never puts a retired sentence back, it
    # only refuses to let one disappear unremarked.
    #
    # Everything else is denied, and this is the half of that a test can hold.
    # A drafting or rewriting agent handed a retired round's prose can revive
    # an edit `edit-authority.md` 1 declared SPENT, and it will do so
    # sincerely, because the text is genuinely there and genuinely this
    # paper's. This test is the only one in this suite that guards a RULE
    # rather than a behaviour, which is why it resolves the lists rather than
    # grepping them.
    import importlib.util
    import shutil
    import tempfile

    spec = importlib.util.spec_from_file_location(
        "manuscript_portability", os.path.join(TOOLS, "manuscript.py"))
    if spec is None or spec.loader is None:
        check("tools/manuscript.py loads", False, True)
        return
    ms = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ms)

    tmp = tempfile.mkdtemp(prefix="obsolete_")
    try:
        root = os.path.join(tmp, "proj")
        # A POPULATED archive, in both layouts and with a retired journal, so
        # a resolver that walks the folder has something to find. No scaffold:
        # `_agent_paths` builds every path from the project root, and this
        # suite is offline and fast by contract.
        for rel in ("project.yml",
                    "drafts/source_text_r3/introduction.md",
                    "drafts/Langmuir/writing_config.yml",
                    "obsolete/drafts/r1/introduction.md",
                    "obsolete/drafts/r2/source_text_r2/introduction.md",
                    "obsolete/drafts/JPCC/writing_config.yml",
                    "obsolete/figures/r1/fig01_geometry.png",
                    "obsolete/tables/r1/table01.csv",
                    "obsolete/analysis/analysis.md",
                    "obsolete/notes/notes.md"):
            path = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("revision: 3\ntitle: Archive rule\n"
                         if rel == "project.yml" else "retired text\n")

        archive = os.path.realpath(os.path.join(root, "obsolete"))
        check("the fixture archive is where the rule says it is",
              os.path.isdir(archive), True)

        paths = ms._agent_paths(root, "Langmuir")

        def resolve(key: str) -> list[str] | None:
            """Every absolute path one `given` key hands over, or None.

            `body_sections` is an ENUMERATING key: `agent_brief` expands it to
            one entry per body section rather than granting the folder,
            because a read scope that is a directory hands over the files a
            module is denied by name four lines later (item 78). A key in
            neither table is a source named in a brief and handed to nobody,
            and it fails below rather than being skipped.
            """
            if key == "body_sections":
                return [os.path.abspath(os.path.join(
                    ms.source_text_dir(root), "%s.md" % stem))
                    for stem in ms.body_sections(root)]
            entry = paths.get(key)
            return None if entry is None else [entry[1]]

        seen = 0
        for module, mspec in sorted(ms.AGENT_MODULES.items()):
            for kind in ("research", "review"):
                resolved = mspec if kind == "research" else \
                    ms.review_module_spec(mspec, module, root, "Langmuir")
                for key in resolved["given"]:
                    got = resolve(key)
                    if got is None:
                        check(f"{module}: `{key}` resolves to a real input",
                              False, True,
                              "a given key that neither _agent_paths nor the "
                              "enumerating branch of agent_brief knows is a "
                              "source named in a brief and handed to nobody")
                        continue
                    for absolute in got:
                        seen += 1
                        under = os.path.realpath(absolute).startswith(
                            archive + os.sep)
                        check(f"{module}: `{key}` is not under obsolete/",
                              under, False, absolute)
        check("every module's given list was actually resolved", seen > 40,
              True, str(seen))

        # And the one permitted reader still reads it, so the rule above is a
        # denial rather than a deletion.
        base = ms.retention_baseline(root, "introduction", 3)
        check("retention_baseline is still allowed to read the archive",
              base["kind"], "snapshot", base)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # One path builder, named in the spec, and nothing else may build it -
    # two call sites is how one of them gets fixed and the other keeps
    # resolving to nothing at exit 0 (round-archive 3.2).
    engine = read(os.path.join(TOOLS, "manuscript.py"))
    check("exactly one function builds the retired-section path",
          len(re.findall(r'"obsolete", "drafts", "r%d" % rnd', engine)), 1,
          "retired_section_path is it; retention_baseline calls it")


def test_skill_reference_files_are_covered() -> None:
    section("Splitting a skill did not shrink what this suite reads (3.2)")

    # THE DANGEROUS HALF of the SKILL.md split, and the reason it had to land
    # in the same commit as the split rather than after it. Every grep in this
    # file runs over "the skill"; pointed at SKILL.md alone after the split it
    # would be looking at under a third of the text it used to cover, and it
    # would report the same green it always did. A suite that passes because
    # it is now looking at a smaller file measures nothing, and it would stay
    # green for months.
    #
    # So the claim is checked rather than intended: each grep records the
    # paths it read, and every reference file has to appear in every one.
    check("there are reference files to cover", bool(SKILL_REFERENCES), True,
          "writing-engine's spine plus one file per stage")
    check("...and the greps above all ran", sorted(VISITED),
          ["documentation", "harness-specific", "plugin-token",
           "reporting-removed", "tools-placeholder"],
          "this test reads what the other tests recorded, so it has to run "
          "after them")

    for grep in sorted(VISITED):
        missed = sorted(os.path.relpath(p, ROOT).replace(os.sep, "/")
                        for p in set(SKILL_TEXTS) - VISITED[grep])
        check(f"the {grep} grep visited every file of every skill", missed,
              [], "a reference file no grep reads is text that left this "
                  "suite's coverage when it left SKILL.md")

    # And the bytes, which is the claim stated the other way round: whatever
    # the file list says, the suite must read at least as much of this skill
    # as it did before the split.
    engine = [p for p in SKILL_TEXTS if "writing-engine" in p]
    covered = sum(len(read(p).encode("utf-8")) for p in engine)
    spine = [p for p in engine if os.path.basename(p) == "SKILL.md"]
    spine_bytes = sum(len(read(p).encode("utf-8")) for p in spine)
    check("the writing-engine skill is read whole, not spine-only",
          covered > 3 * spine_bytes, True,
          "%d bytes covered against a %d-byte spine" % (covered, spine_bytes))


    # --- a pointer that stopped resolving when the file was split ---------
    #
    # The failure class a split creates, and the one that caught this build
    # out: a sentence saying `Read "X" below` is still true in the file X
    # left, and false the moment X is in another file. It reads as an
    # instruction either way, which is what makes it expensive - the reader
    # goes looking for a section that is not there and carries on without it.
    homes: dict[str, str] = {}
    bodies: dict[str, str] = {}
    for path in SKILL_TEXTS:
        text = read(path)
        bodies[path] = text
        fence = None
        for line in text.splitlines():
            tick = re.match(r"^(```|~~~)", line)
            if tick:
                fence = None if fence else tick.group(1)
                continue
            if fence:
                continue
            head = re.match(r"^#{1,4} (.+?)\s*$", line)
            if head:
                homes.setdefault(head.group(1).strip(), path)

    for path, text in bodies.items():
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        # `"Some heading" below` / `above` - the shape the skills actually use
        # to point at one another.
        for m in re.finditer(r'"([^"\n]{6,70})"\s+(?:below|above)', text):
            name = m.group(1).strip()
            home = homes.get(name)
            if home is None:
                continue
            check(f"{rel}: the pointer to {name!r} resolves in this file",
                  os.path.basename(home), os.path.basename(path),
                  "`below` and `above` mean this file; a section that moved "
                  "needs the file it moved to named instead")

    # The Title Case rule, item 83, is a standing preference that has to be in
    # front of whoever writes the label. A reference file that gives drafting
    # or float instructions is such a place, so the rule's reach is checked
    # against the whole body rather than against the file it was first put in.
    for path in SKILL_REFERENCES:
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        text = read(path)
        if not re.search(r"(?i)\b(caption|label|heading|figure title)\b",
                         text):
            continue
        owner = read(owning_skill(path))
        check(f"{rel} is covered by its skill's Title Case rule",
              ("Title Case" in text) or ("Title Case" in owner), True,
              "the rule has to be reachable from wherever a label gets "
              "written, and the spine is read on every invocation")


def _git(repo: str, *argv: str) -> tuple:
    proc = subprocess.run(["git", *argv], cwd=repo, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout, proc.stderr


def _scratch_plugin(version: str = "0.1.0") -> str:
    """A throwaway git repo shaped like a plugin, with one commit."""
    repo = tempfile.mkdtemp(prefix="pwa_release_")
    os.makedirs(os.path.join(repo, ".claude-plugin"), exist_ok=True)
    os.makedirs(os.path.join(repo, "skills"), exist_ok=True)
    with io.open(os.path.join(repo, ".claude-plugin", "plugin.json"), "w",
                 encoding="utf-8", newline=chr(10)) as fh:
        json.dump({"name": "scratch", "version": version}, fh, indent=2)
    with io.open(os.path.join(repo, "skills", "a.md"), "w",
                 encoding="utf-8", newline=chr(10)) as fh:
        fh.write("# A" + chr(10))
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "first")
    return repo


def test_version_bump_check() -> None:
    section("A release-shaped obligation nothing else notices (lab-pack 6.4)")

    # The whole check: is the last commit that changed the `version` line
    # older than the last commit that changed shipped content? A commit is
    # not a release, so this REPORTS and never refuses - same rule as `voice`,
    # `metaprose` and `float lint`.
    repo = _scratch_plugin()
    try:
        res = rel.version_staleness(repo)
        check("a repo whose only commit created both is not stale",
              res["state"], "current", res.get("why"))

        # Shipped content moves, version does not. This is the real case:
        # `plugin.json` itself was edited to add a skill, two lines below the
        # version it left alone, and /plugin update reported everyone current.
        with io.open(os.path.join(repo, "skills", "b.md"), "w",
                     encoding="utf-8", newline=chr(10)) as fh:
            fh.write("# B" + chr(10))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "a second skill")
        res = rel.version_staleness(repo)
        check("shipped content after the last version bump is STALE",
              res["state"], "stale", res.get("why"))
        check("and it names how many commits of content came after",
              res["commits_since"], 1, res)
        check("and it names the remedy as a COMMAND, not a sentence",
              "bump" in (res["remedy"] or ""), True, res.get("remedy"))
        check("it reports rather than refusing - a verdict is a line",
              res.get("fatal", False), False,
              "a maintainer mid-way through a change has every right to an "
              "unbumped manifest")

        # A real bump, through the command the remedy names.
        out = rel.bump(repo)
        check("bump writes TODAY as the version",
              out["version"], datetime.date.today().strftime("%Y.%-m.%-d")
              if os.name != "nt"
              else datetime.date.today().strftime("%Y.%#m.%#d"), out)
        check("and the format has no leading zeros - valid semver AND a date",
              re.match(r"^\d{4}\.(0|[1-9]\d*)\.(0|[1-9]\d*)$",
                       out["version"]) is not None, True,
              "semver forbids leading zeros in a numeric identifier, so "
              "2026.09.09 risks being rejected on a member's machine")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "bump")
        res = rel.version_staleness(repo)
        check("and the check goes QUIET after a correct bump",
              res["state"], "current", res.get("why"))
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_version_check_sees_every_pack_file() -> None:
    section("item 162 - in a lab pack, every root .md is shipped content")

    # labpack.py reads EVERY .md at a pack's root, so every one of them
    # reaches a member. A fixed list of names cannot see a file added after
    # it was written, and an edit to that file alone reported `current`.
    repo = _scratch_plugin()
    try:
        with io.open(os.path.join(repo, "lab.yml"), "w", encoding="utf-8",
                     newline=chr(10)) as fh:
            fh.write("pack: scratch" + chr(10))
        with io.open(os.path.join(repo, "a_new_pack_file.md"), "w",
                     encoding="utf-8", newline=chr(10)) as fh:
            fh.write("# New" + chr(10))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "a pack file")
        rel.bump(repo)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "bump")
        check("a bumped pack is current",
              rel.version_staleness(repo)["state"], "current")

        with io.open(os.path.join(repo, "a_new_pack_file.md"), "a",
                     encoding="utf-8", newline=chr(10)) as fh:
            fh.write("More." + chr(10))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "edit only the new file")
        res = rel.version_staleness(repo)
        check("an edit to a pack .md the old list never named is STALE",
              res["state"], "stale", res.get("why"))

    finally:
        shutil.rmtree(repo, ignore_errors=True)

    # The engine repository is not a pack: its root .md are docs, and
    # CLAUDE.md moving must not demand a release.
    repo = _scratch_plugin()
    try:
        with io.open(os.path.join(repo, "NOTES.md"), "w", encoding="utf-8",
                     newline=chr(10)) as fh:
            fh.write("# Notes" + chr(10))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "a doc edit")
        check("with no lab.yml a root .md is not shipped content",
              rel.version_staleness(repo)["state"], "current")
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_version_check_uses_G_not_S() -> None:
    section("6.4 - `git log -S` is the natural reach and is the WRONG tool")

    # Measured 2026-09-09 and reproduced here, because a check built on `-S`
    # reports STALE forever - including immediately after a correct bump - and
    # a check that cries wolf is deleted rather than fixed.
    repo = _scratch_plugin()
    try:
        path = os.path.join(repo, ".claude-plugin", "plugin.json")
        with io.open(path, "w", encoding="utf-8", newline=chr(10)) as fh:
            json.dump({"name": "scratch", "version": "0.2.0"}, fh, indent=2)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "a real bump")

        _, s_out, _ = _git(repo, "log", "--format=%s", "-S", '"version"',
                           "--", ".claude-plugin/plugin.json")
        _, g_out, _ = _git(repo, "log", "--format=%s", "-G", '^ *"version":',
                           "--", ".claude-plugin/plugin.json")
        s_hits = [x for x in s_out.splitlines() if x.strip()]
        g_hits = [x for x in g_out.splitlines() if x.strip()]

        check("-S finds ONLY the commit that created the line",
              s_hits, ["first"],
              "-S counts OCCURRENCES of a string, and a bump changes the "
              "value while leaving one occurrence before and after")
        check("-G finds the bump as well, which is the question being asked",
              "a real bump" in g_hits, True, str(g_hits))
        check("so the engine uses -G", "-G" in rel.VERSION_PICKAXE, True,
              str(rel.VERSION_PICKAXE))
        check("and never -S", "-S" not in rel.VERSION_PICKAXE, True,
              str(rel.VERSION_PICKAXE))

        res = rel.version_staleness(repo)
        check("which is why the check is quiet right after a bump",
              res["state"], "current", res.get("why"))
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_version_check_is_silent_without_git() -> None:
    section("6.4 - absent git is not a failure. A plugin install is not a "
            "checkout")

    plain = tempfile.mkdtemp(prefix="pwa_nogit_")
    try:
        os.makedirs(os.path.join(plain, ".claude-plugin"), exist_ok=True)
        with io.open(os.path.join(plain, ".claude-plugin", "plugin.json"), "w",
                     encoding="utf-8", newline=chr(10)) as fh:
            json.dump({"name": "scratch", "version": "2026.9.9"}, fh, indent=2)
        res = rel.version_staleness(plain)
        check("no history, no verdict", res["state"], "unknown", res)
        check("and no error - the same shape as an empty resources/",
              res.get("fatal", False), False, res)
        check("the version is still reported, because that much is on disk",
              res["version"], "2026.9.9", res)
    finally:
        shutil.rmtree(plain, ignore_errors=True)


def test_every_skill_asks_before_it_starts() -> None:
    """Item 117 - a check nothing runs is a check that does not exist.

    The staleness check was correct and unreachable: `remote.py check` was a
    command a member had to type, nothing typed it, and the freshness line
    said UNKNOWN for ever. Worse, six of the eight skills never surfaced the
    line at all, so a fresh answer sitting on disk went unread.

    **The fix is eight copies of one instruction, which is eight chances to
    drift.** That is what this test is for. It does not check prose - it
    checks that every skill names the command, and that the three properties
    making it safe to run uninvited are stated where the reader is.
    """
    section("117 - every skill opens by asking whether this copy is behind")

    names = sorted(os.path.basename(os.path.dirname(p)) for p in SKILLS)
    for name in names:
        text = read(os.path.join(SKILLS_DIR, name, "SKILL.md"))
        check(f"{name} runs the refresh at its opening",
              "remote.py refresh" in text, True,
              "item 117: a skill that does not ask is a member who is never "
              "told")
        # It must be positioned, not merely mentioned. The `<tools>` block is
        # the one anchor all eight share, and the refresh belongs after it -
        # before it, `<tools>` is not resolved and the command cannot run.
        if "remote.py refresh" in text and "If none of them resolves" in text:
            check(f"...after `<tools>` resolves, not before",
                  text.index("If none of them resolves")
                  < text.index("remote.py refresh"), True,
                  "the command is spelled `python <tools>/remote.py`, so it "
                  "cannot precede the block that resolves <tools>")
        # 6.2's four rules survive being run automatically, or they were
        # never rules. The skill has to say so where somebody reads it.
        check(f"...and says it never blocks",
              "never blocks" in text, True,
              "6.2: it reports, it never updates, it never blocks")
        check(f"...and says it never updates",
              "never updates" in text, True,
              "changing the facility knowledge under somebody mid-"
              "conversation is worse than being a version behind it")
        check(f"...and says it must not run again later in the session",
              "again later in the session" in text, True,
              "6.2: no network call on the path that generates an idea. "
              "The opening is not that path; a later stage is")

    # Item 118 - a skill that explains an engine's internals has to be
    # rewritten when the internals are replaced. The marketplace-clone
    # comparison is item 116's retired mechanism, and no skill may still
    # describe freshness in terms of it.
    for name in names:
        text = read(os.path.join(SKILLS_DIR, name, "SKILL.md"))
        stale = [line.strip() for line in text.splitlines()
                 if "marketplace clone" in line]
        check(f"{name} does not explain freshness by the retired clone read",
              stale, [],
              "item 118: item 116 deleted that mechanism. A skill still "
              "naming it makes a guarantee whose reasoning has been removed")


def test_the_lab_pack_brief_has_callers_and_one_refusal() -> None:
    """specs/lab-pack-brief-2026-09-24.md 4 and 4.1 - who writes it, who
    reads it, and the one skill that must never touch it.

    Items 96 and 97 in one sentence: a capability wired end to end with no
    caller at one end reports success for ever. `labpack.py brief` is built
    and tested by 77 checks in `tests/labpack.py`, and every one of them
    passes on a machine where no skill has ever run it.

    The refusal half matters more than the caller half. `writing-engine`
    renders `data/methods_facts.yml` into a methods section as measured
    truth; a route from the brief into that file is a facility default
    reaching print wearing the shape of a checked fact, and it is
    unfalsifiable from inside the project.
    """
    section("lab pack brief - three writers, one reader, one refusal (4.1)")

    writers = ("idea-generation", "setup-project-directory",
               "reorganize-directory")
    for name in writers:
        text = read(os.path.join(SKILLS_DIR, name, "SKILL.md"))
        check(f"{name} writes the brief",
              "labpack.py brief" in text, True,
              "4.1: a file nobody is pointed at is item 96's shape")
        check(f"...and says a pack value is never a methods fact",
              "methods fact" in text, True,
              "0.1: the one guard that is wording rather than code")

    analysis = read(os.path.join(SKILLS_DIR, "analysis", "SKILL.md"))
    check("analysis names the brief where it lists what to read first",
          "lab_pack_brief.md" in analysis, True,
          "4: 'while they are figuring out their data' is that skill's "
          "whole stage")
    check("...and says it never writes it",
          "never writes it" in analysis, True,
          "one writer, so a note the user made cannot be half-owned")

    # The refusal. Checked over the spine AND every reference file, because
    # the methods section is drafted from one of them.
    engine = [p for p in SKILL_TEXTS if "writing-engine" in p]
    check("there is a writing-engine skill to check", bool(engine), True)
    for path in engine:
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        check(f"{rel} does not read the lab pack brief",
              "lab_pack_brief" in read(path), False,
              "4: a pack value routed into methods_facts.yml is a facility "
              "number reaching print as a checked fact")


def test_the_methods_notebook_has_callers_and_one_refusal() -> None:
    """specs/methods-notebook-2026-09-28.md 3.7 - two writers, one refusal.

    Same shape as the brief's test, for the same two reasons: a command with
    no caller passes every one of its own checks for ever, and a to-do list
    drafted into the methods section is a facility number reaching print.
    """
    section("methods notebook - two writers, one refusal (3.7)")

    for name in ("idea-generation", "setup-project-directory"):
        text = read(os.path.join(SKILLS_DIR, name, "SKILL.md"))
        check(f"{name} writes the notebook",
              "labpack.py notebook" in text, True,
              "a file nobody is pointed at is item 96's shape")
        check("...and says it is a to-do list, not a methods section",
              "not a methods section" in text, True,
              "3.1: the one sentence that keeps it out of the draft")
    setup = read(os.path.join(SKILLS_DIR, "setup-project-directory",
                              "SKILL.md"))
    check("setup-project-directory defines the lab's methods in one place",
          "The Lab's Methods, in Four Files" in setup, True,
          "0: the ask was that it be explicitly defined")
    check("...and offers the software prefill",
          "--software" in setup, True, "2.2")
    idea_skill = read(os.path.join(SKILLS_DIR, "idea-generation", "SKILL.md"))
    check("idea-generation names the pack source kind",
          "pack:<file>.md - <the section heading>" in idea_skill, True, "1")
    template = read(os.path.join(ROOT, "tools", "project_template",
                                 "CLAUDE.md"))
    check("the project template names the notebook",
          "plan/methods_notebook.md" in template, True, "3.7")

    engine = [p for p in SKILL_TEXTS if "writing-engine" in p]
    for path in engine:
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        check(f"{rel} does not read the methods notebook",
              "methods_notebook" in read(path), False,
              "3.7: a to-do list is not a record of what was done")


def test_the_refresh_is_rate_limited_and_short() -> None:
    """Item 117 - what makes it safe to run at every skill's opening.

    Running an unconditional `check` eight ways would be 2.4s of network on
    a good connection and 40s on a bad one, paid every time anybody starts
    anything. Three numbers stop that, and each is asserted here rather than
    trusted: the rate limit, the retry backoff after a failure, and a
    timeout shorter than the one a typed `check` may take.
    """
    section("117 - the refresh is cheap, or it does not belong at an opening")

    check("a refresh gives up sooner than a typed check",
          rem.REFRESH_TIMEOUT < rem.TIMEOUT, True,
          "`check` is typed on purpose and can afford to wait; `refresh` is "
          "uninvited and must never be why a skill feels slow to start")
    check("...and a good answer is reused for a day",
          rem.REFRESH_AFTER_HOURS >= 24, True)
    check("...and a FAILED one is retried sooner than that",
          rem.RETRY_AFTER_HOURS < rem.REFRESH_AFTER_HOURS, True,
          "without a shorter retry a member whose network is down pays the "
          "full timeout on every single skill invocation")

    # `due` is the whole rate limit. Drive it against a cache we control.
    home = tempfile.mkdtemp(prefix="pwa_refresh_")
    keep = os.environ.get("PAPER_ENGINE_HOME")
    os.environ["PAPER_ENGINE_HOME"] = home
    try:
        rem._LOADED.clear()

        def put(hours_ago: float, ok: bool) -> None:
            when = (datetime.datetime.now()
                    - datetime.timedelta(hours=hours_ago))
            os.makedirs(home, exist_ok=True)
            with io.open(os.path.join(home, rem.CACHE), "w",
                         encoding="utf-8", newline=chr(10)) as fh:
                json.dump({"repos": {"a/b": {
                    "repo": "a/b", "sha": "abc1234", "ok": ok,
                    "error": "" if ok else "could not reach it",
                    "asked_at": when.replace(microsecond=0).isoformat()}}},
                    fh, indent=2)

        check("a repository never asked is due", rem.due("a/b"), True)
        put(1.0, True)
        check("...one answered an hour ago is not", rem.due("a/b"), False,
              "the overwhelming majority of skill openings must touch no "
              "network at all")
        put(rem.REFRESH_AFTER_HOURS + 1, True)
        check("...one answered over a day ago is", rem.due("a/b"), True)
        put(0.2, False)
        check("...a failure minutes old is not retried", rem.due("a/b"), False,
              "otherwise an offline member pays the timeout every time")
        put(rem.RETRY_AFTER_HOURS + 0.1, False)
        check("...but a failure over an hour old is", rem.due("a/b"), True)
        # A restored machine or a corrected timezone reads as a negative age.
        # That is not evidence the answer is fresh.
        put(-5.0, True)
        check("...and a clock that ran backwards is asked, not trusted",
              rem.due("a/b"), True)

        # Nothing is asked when nothing is due, and the result says so
        # without having reached anything.
        put(1.0, True)
        res = rem.refresh(["a/b"])
        check("a refresh with nothing due asks nothing", res["asked"], 0,
              "this is the case that runs at almost every skill opening")
        check("...and says what it skipped", res["skipped"], 1, res)
        check("...and still returns the news list", isinstance(res["news"],
              list), True, res)
    finally:
        if keep is None:
            os.environ.pop("PAPER_ENGINE_HOME", None)
        else:
            os.environ["PAPER_ENGINE_HOME"] = keep
        rem._LOADED.clear()
        shutil.rmtree(home, ignore_errors=True)


def test_offline_staleness_check() -> None:
    """plugin-packaging 6.2 step 6, the member's half of `stale`."""
    section("6.2 - is THIS copy behind the one already on disk, offline")

    # No network call on the path that drafts a paper - 6.2 forbids it, and
    # the assertion is against the SOURCE rather than against a run, because
    # a call that only fires on somebody else's machine still fires.
    src = read(os.path.join(TOOLS, "release.py"))
    body = src.split("def freshness(", 1)[-1].split("\ndef print_freshness",
                                                    1)[0]
    for reach in ("requests", "urlopen", "urllib", "http", "socket",
                  "subprocess"):
        check(f"the freshness check makes no {reach} call",
              reach in body, False,
              "6.2: what must not happen is a network call on the path that "
              "drafts a paper")

    res = rel.freshness(ROOT)
    check("it answers on this copy at all", bool(res["line"]), True, res)
    check("...with one of the four states it declares",
          res["state"] in ("current", "update_available", "unknown",
                           "not_installed"), True, res)
    check("...and never says it is blocking anything",
          "blocked" in res["line"] or res["state"] == "current", True, res)

    # Unknown is its own answer and is SAID - a member whose background
    # refresh broke reads silence as good news.
    plain = tempfile.mkdtemp(prefix="pwa_fresh_")
    try:
        os.makedirs(os.path.join(plain, ".claude-plugin"), exist_ok=True)
        with io.open(os.path.join(plain, ".claude-plugin", "plugin.json"), "w",
                     encoding="utf-8", newline=chr(10)) as fh:
            json.dump({"name": "scratch", "version": "2026.9.9"}, fh, indent=2)
        res2 = rel.freshness(plain)
        check("a copy that is not a plugin install says which route it IS",
              res2["state"], "not_installed", res2)
        check("...and names the command that DOES reach it",
              "git pull" in res2["line"], True, res2)
    finally:
        shutil.rmtree(plain, ignore_errors=True)

    # It reports; it never updates. The word `update` appears only as
    # something the member runs.
    check("the engine never updates itself",
          "/plugin update" in src and "shutil.copy" not in body, True,
          "an engine that updated itself mid-paper would change the tool "
          "between one round and the next")

    # And it reaches the line the user already reads.
    ms_src = read(os.path.join(TOOLS, "manuscript.py"))
    check("plan carries it in the payload", '"toolkit": fresh' in ms_src, True)
    check("...and only speaks up when there is something to say",
          'fresh.get("state") in ("update_available"' in ms_src, True,
          "a member who is current does not need a sentence about it every "
          "round")


def test_nothing_offers_to_send_anything_upstream() -> None:
    """Upstream defect reporting was removed 2026-09-21."""
    section("Nothing this toolkit records leaves the machine it runs on")

    # The inverse of the test that used to stand here. That one held the
    # offer to ONE carrier, because eight skills asking the same question is
    # how a member learns to dismiss it. There is no question now, and the
    # failure to guard against is the opposite: a skill body, a help page or
    # an engine that still tells a member to mint a token and grant consent
    # sends them looking for machinery that is not there - and the member
    # cannot tell a removed feature from a broken one.
    stale = ("consent --grant", "report.py", "paper-engine-reports",
             "PWA_REPORTS_REPO", "PWA_SPOOL_DIR", "sync_reports")
    asking = []
    for path in SKILL_TEXTS:
        text = visiting("reporting-removed", path)
        for token in stale:
            if token in text:
                asking.append("%s: %s" % (os.path.relpath(path, SKILLS_DIR),
                                          token))
    check("no skill body names the reporting machinery", asking, [],
          "it was removed; a skill that still names it sends a member after "
          "a token that configures nothing")

    check("the engine is gone",
          os.path.exists(os.path.join(TOOLS, "report.py")), False)
    check("...and so is its suite",
          os.path.exists(os.path.join(HERE, "report.py")), False)
    check("...and the scheduled-task script with it",
          os.path.exists(os.path.join(TOOLS, "sync_reports.ps1")), False)
    check("...and the spool it fed",
          os.path.exists(os.path.join(ROOT, ".spool")), False)

    # The half that had to survive. `--stage` was recorded ONLY in the
    # spooled copy of a record, so removing the spool without moving it would
    # have left a flag that validates its argument and then drops it.
    ms_src = read(os.path.join(TOOLS, "manuscript.py"))
    check("the stage vocabulary moved to the engine that kept the file",
          "STAGES = (" in ms_src, True)
    check("...and the ledger item renders it",
          '"- **Stage:** %s' in ms_src, True,
          "a flag that reports success and writes nothing is item 114")
    check("...and no engine imports the one that went",
          'os.path.join(HERE, "report.py")' in ms_src, False)


def test_both_plugins_carry_date_versions() -> None:
    section("6.4 - both manifests use date versions, and this repo is one")

    manifest = os.path.join(ROOT, ".claude-plugin", "plugin.json")
    version = json.loads(read(manifest)).get("version", "")
    check("the engine's version is a date, not semver-by-judgment",
          re.match(r"^\d{4}\.(0|[1-9]\d*)\.(0|[1-9]\d*)$", version)
          is not None, True,
          f"{version!r} - a script sees '40 files changed' and cannot tell a "
          f"patch from a breaking change, so the number it picks is a lie "
          f"people act on")
    check("and it is not the 0.1.0 that never moved", version != "0.1.0", True)

    # The check the maintainer actually gets, run against this repository's
    # own real history. It REPORTS - a red line here is a reminder, not a
    # failed suite - so the assertion is that it produced a verdict at all.
    res = rel.version_staleness(ROOT)
    check("the check answers for this repository",
          res["state"] in ("current", "stale", "unknown"), True, res)
    if res["state"] == "stale":
        note(f"version bump is due: {res['why']} - run {res['remedy']}")


# The repository is public. What must never be committed to it is named here
# and checked against what git actually tracks, not against what is on disk:
# the files may well be on the maintainer's machine, where they belong.
PRIVATE_PATHS = ("system-changes.md", "TODO.md", "specs/", "history/")


def test_private_notes_are_never_tracked() -> None:
    section("The defect ledger and the design notes never reach GitHub")
    import subprocess
    r = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True,
                       text=True)
    if r.returncode != 0:
        skip("tracked files", "not a git checkout")
        return
    tracked = r.stdout.splitlines()
    for p in PRIVATE_PATHS:
        hits = [t for t in tracked if t == p or t.startswith(p)]
        check(f"{p} is not tracked", hits, [],
              "it is written against real projects; `git rm --cached` it")
    ignore = read(os.path.join(ROOT, ".gitignore")).splitlines()
    for p in PRIVATE_PATHS:
        check(f"{p} is gitignored", p in ignore, True)
    check("a fresh clone has the ledger's template",
          os.path.isfile(os.path.join(ROOT, "tools",
                                      "system-changes.template.md")), True)


def main() -> int:
    print("portability - the AGENTS.md contract, enforced")
    test_engines_are_standalone()
    test_every_engine_answers_help()
    test_json_on_either_side()
    test_exit_codes_documented()
    test_skills_are_harness_neutral()
    test_the_label_rule_reaches_the_person_writing_the_label()
    test_user_state_never_lives_where_an_update_replaces_it()
    test_sequence_refusals_reach_the_skill()
    test_context_isolation_fallback()
    test_plugin_manifest_matches_the_tree()
    test_tools_placeholder_is_defined()
    test_agents_md_is_current()
    test_no_absolute_paths_baked_in()
    test_documentation_names_nothing_real()
    test_shipping_corpus_carries_no_project_specifics()
    test_the_specific_shapes_match_what_they_must()
    test_the_shape_contract_has_one_copy()
    test_the_engines_own_prompt_text_is_documentation()
    test_no_suite_writes_the_live_defect_ledger()
    test_no_module_is_given_a_path_under_obsolete()
    # Both of these were unreachable until 2026-09-17. The second has been
    # defined and called by nothing since it was written - CLAUDE.md and
    # CONTRIBUTING.md both say portability "pins README's install claims
    # against the manifests" and it has never once run. Filed as a defect;
    # registering it is the fix.
    test_a_report_is_not_an_answer()
    test_readme_documents_both_routes()
    test_both_plugins_carry_date_versions()
    test_version_check_is_silent_without_git()
    test_every_skill_asks_before_it_starts()
    test_the_lab_pack_brief_has_callers_and_one_refusal()
    test_the_methods_notebook_has_callers_and_one_refusal()
    test_the_refresh_is_rate_limited_and_short()
    test_offline_staleness_check()
    test_nothing_offers_to_send_anything_upstream()
    test_private_notes_are_never_tracked()
    test_version_check_uses_G_not_S()
    test_version_check_sees_every_pack_file()
    test_version_bump_check()
    # Last: it reads what every grep above recorded.
    test_skill_reference_files_are_covered()
    print(f"\n{PASS + FAIL} checks: {PASS} passed, {FAIL} failed"
          + (f", {SKIP} skipped" if SKIP else ""))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
