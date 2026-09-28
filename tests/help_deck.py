#!/usr/bin/env python3
"""
tests/help_deck.py - measure tools/help_deck.py against the cases
specs/orchestration.md 5.4 names.

Offline, ~15 s, and it exists because the deck is the one artifact in this
toolkit whose failure mode is **"opens fine, says something false"**. A hand-
drawn flow chart goes stale silently while still looking authoritative; a
generated one goes stale silently while still looking authoritative UNLESS
something holds its content against the engine it describes. That is this file.

The two that matter most:

  - every module the deck names is a module `manuscript.py agent-brief --list`
    reports. A module renamed in the engine has to fail here, rather than
    quietly leaving a wrong slide in circulation.
  - every `blind` marker in the deck matches agent-brief's own `blind` list. A
    slide that calls a module blind when it is not is worse than no slide: it
    is the one property a reader would rely on without checking.

  python tests/help_deck.py
  python tests/help_deck.py -v
"""

from __future__ import annotations

import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except ValueError:
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOLS = os.path.join(ROOT, "tools")


def _load(name: str, filename: str):
    path = os.path.join(TOOLS, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load " + path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hd = _load("_help_deck_engine", "help_deck.py")
ms = _load("_manuscript_engine", "manuscript.py")

VERBOSE = "-v" in sys.argv
PASSED = FAILED = SKIPPED = 0


def check(label: str, passed, detail: object = "") -> bool:
    global PASSED, FAILED
    if passed:
        PASSED += 1
        if VERBOSE:
            print("  ok   %s" % label)
    else:
        FAILED += 1
        print("  FAIL %s" % label)
        if detail:
            for line in str(detail).rstrip().splitlines():
                print("         %s" % line)
    return bool(passed)


def skip(label: str, why: str) -> None:
    global SKIPPED
    SKIPPED += 1
    print("[skip] %s\n         %s" % (label, why))


def section(title: str) -> None:
    print("\n--- %s %s" % (title, "-" * max(0, 56 - len(title))))


# ---------------------------------------------------------------------------
# 1. The deck cannot name a module the engine does not have
# ---------------------------------------------------------------------------

def test_modules_are_real() -> None:
    section("every module the deck names is a real one")

    listing = ms.agent_brief_list()
    known = {m["module"] for m in listing["modules"]}
    known |= set(ms.MODULES)
    check("the engine reports modules to check against", len(known) > 8, known)

    named = [name for name, _mark, _what in hd.ENGINE_MODULES]
    unknown = [n for n in named if n not in known]
    check("no slide names a module that does not exist", unknown == [],
          "a module renamed in the engine must fail here rather than leave a "
          "wrong slide in circulation: " + repr(unknown))

    # And the other direction, as a warning rather than a failure: which
    # modules exist and are not on a slide. The deck is a flow chart and is
    # allowed to leave things out; what it may not do is leave them out
    # silently, so the count is printed.
    missing = [m for m in ms.MODULES if m not in named]
    check("the deck covers most of the pipeline",
          len(named) >= len(ms.MODULES) - 4,
          "on a slide: %d of %d modules. Not on one: %s"
          % (len(named), len(ms.MODULES), ", ".join(missing)))


def test_blind_markers_match() -> None:
    section("every `blind` marker matches the engine's own")

    engine_blind = {name for name, spec in ms.AGENT_MODULES.items()
                    if spec.get("isolation") == "blind"}
    engine_agent = set(ms.AGENT_MODULES)

    deck_blind = {name for name, mark, _what in hd.ENGINE_MODULES
                  if "blind" in (mark or "")}
    deck_agent = {name for name, mark, _what in hd.ENGINE_MODULES
                  if mark}

    check("the engine has blind modules to check against",
          bool(engine_blind), engine_blind)
    wrong = sorted(deck_blind - engine_blind)
    check("no slide calls a module blind that is not",
          wrong == [],
          "a slide that calls a module blind when it is not is worse than no "
          "slide - it is the one property a reader relies on without "
          "checking: " + repr(wrong))
    missed = sorted(m for m in engine_blind if m in deck_agent
                    and m not in deck_blind)
    check("and no blind module is shown without the marker", missed == [],
          missed)

    for name, mark, _what in hd.ENGINE_MODULES:
        if not mark:
            continue
        check("%s: the marker agrees with the engine" % name,
              (name in engine_agent) or mark == "",
              "the deck marks it %r and agent-brief does not run it as its "
              "own agent" % mark)


def test_modular_commands_resolve() -> None:
    section("every command on the modular slide resolves")

    # Two shapes of row, and both are real in the deck:
    #   "idea.py mock  /  scaffold.py mock-floats"   two engines
    #   "prose.py density / crossrefs / length"      one engine, three
    #                                                subcommands
    pairs = []
    for what, command in hd.MODULAR:
        for chunk in re.split(r"\s{2,}/\s{2,}", command):
            chunk = chunk.strip()
            m = re.match(r"^([a-z_]+\.py)\s+(.*)$", chunk)
            if not m:
                check("%s: parses as <engine> <subcommand>" % chunk, False,
                      "row %r" % what)
                continue
            engine, rest = m.group(1), m.group(2)
            for sub in re.split(r"\s*/\s*", rest):
                sm = re.match(r"^([a-z][a-z-]*)", sub.strip())
                if sm:
                    pairs.append((what, engine, sm.group(1)))

    helps: dict = {}
    for what, engine, sub in pairs:
        path = os.path.join(TOOLS, engine)
        if engine not in helps:
            check("%s exists" % engine, os.path.isfile(path), path)
            # --help only, and cached per engine. Nothing is RUN: this suite
            # is offline and every subcommand here would need a project.
            run = subprocess.run([sys.executable, path, "--help"],
                                 capture_output=True, text=True,
                                 encoding="utf-8", errors="replace",
                                 timeout=90)
            helps[engine] = run.stdout
        check("%s %s is a real subcommand (row: %s)" % (engine, sub, what),
              re.search(r"[{,]%s[},]" % re.escape(sub), helps[engine])
              is not None,
              helps[engine][:400])


# ---------------------------------------------------------------------------
# 2. The round trip, and the fallback that has to stay real
# ---------------------------------------------------------------------------

def _shape_count(path: str) -> int:
    n = 0
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if re.match(r"^ppt/slides/slide\d+\.xml$", name):
                n += z.read(name).decode("utf-8").count("<p:sp>")
    return n


def _timing_blocks(path: str) -> int:
    n = 0
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if re.match(r"^ppt/slides/slide\d+\.xml$", name):
                n += z.read(name).decode("utf-8").count("<p:timing>")
    return n


def test_build_then_check() -> None:
    section("build, then check, returns ok")

    tmp = tempfile.mkdtemp(prefix="helpdeck_")
    try:
        out = os.path.join(tmp, "deck.pptx")
        res = hd.build(out)
        check("build reports no errors", not res.get("errors"),
              res.get("errors"))
        check("it wrote the file", os.path.isfile(out), out)
        check("nine slides", res.get("slides") == 9, res.get("slides"))
        check("and animations on every one",
              res.get("slides_with_timing") == res.get("slides"),
              (res.get("slides_with_timing"), res.get("slides")))

        chk = hd.check(out)
        check("check comes back with no problems",
              chk.get("problems") == [], chk.get("problems"))

        # THE SILENT-DROP CASE. A shape id that is animated and not on the
        # slide that animates it: PowerPoint drops the effect, the deck opens
        # fine, and the animation is simply gone.
        check("every animated shape id is on its own slide",
              not [p for p in chk.get("problems", [])
                   if "id" in str(p).lower()], chk.get("problems"))

        # The fallback path has to stay real. Animation XML is the one part of
        # a .pptx PowerPoint refuses to open rather than degrading, so
        # --no-animation is the escape hatch and it must produce the same deck.
        plain = os.path.join(tmp, "plain.pptx")
        res2 = hd.build(plain, animate=False)
        check("--no-animation still writes nine slides",
              res2.get("slides") == 9, res2.get("slides"))
        check("and no slide carries a timing block",
              res2.get("slides_with_timing") == 0,
              res2.get("slides_with_timing"))
        check("with no timing blocks at all", _timing_blocks(plain) == 0,
              _timing_blocks(plain))
        check("and the same shape count as the animated deck",
              _shape_count(plain) == _shape_count(out),
              (_shape_count(plain), _shape_count(out)))
        check("so the flow chart is legible with animations ignored",
              _shape_count(plain) > 100, _shape_count(plain))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_shipped_deck() -> None:
    section("the deck that ships still opens and still animates")

    # NOT "is what the generator writes" any more. The deck is hand-editable
    # and `build` runs only when somebody asks for it, so the shipped file is
    # allowed to differ from a fresh build - that is the point of the change.
    # What is still worth holding is that whatever is in the folder is a valid
    # deck that PowerPoint will open with its animations intact, because THAT
    # is what a bad hand-edit breaks, and it breaks it silently.

    shipped = os.path.join(ROOT, "help", "paper_engine_overview.pptx")
    if not os.path.isfile(shipped):
        skip("the shipped deck", "help/paper_engine_overview.pptx is absent")
        return
    chk = hd.check(shipped)
    check("the shipped deck has no problems", chk.get("problems") == [],
          chk.get("problems"))
    check("and carries its animations", chk.get("effects", 0) > 50,
          chk.get("effects"))


# ---------------------------------------------------------------------------
# 3. help/README.md and help/instructions.md
# ---------------------------------------------------------------------------

def _gh_slug(heading: str) -> str:
    """GitHub's heading -> anchor rule, as much of it as this file needs.

    Lowercase, strip inline markup and punctuation, spaces to hyphens. The
    reason the guide's headings use a colon rather than an em dash is in here:
    an em dash is dropped and leaves the two spaces around it as a DOUBLE
    hyphen, which is easy to write by hand and get wrong.
    """
    s = heading.strip().lower()
    s = re.sub(r"[`*_\[\]()]", "", s)
    s = re.sub(r"[^\w\s-]", "", s, flags=re.U)
    return s.strip().replace(" ", "-")


def test_help_files() -> None:
    section("help/README.md, SETUP-NEW-MEMBER.md and instructions.md")

    readme = os.path.join(ROOT, "help", "README.md")
    check("help/README.md exists", os.path.isfile(readme), readme)
    if os.path.isfile(readme):
        text = io.open(readme, encoding="utf-8").read()
        low = text.lower()
        # THE DECK IS HAND-EDITABLE AND THE GENERATOR IS ON REQUEST ONLY.
        # This reverses what this suite used to assert. The old checks demanded
        # the README call the deck generated and warn against hand-editing it;
        # holding that wording now would pin the file to a policy that no
        # longer applies, which is the failure mode of a test nobody revisits.
        check("it says the deck is hand-editable",
              "hand-editable" in low or "yours to edit" in low)
        check("and that a rebuild discards hand edits",
              "discards" in low and "hand edit" in low)
        check("and that the generator runs only on request",
              "on request only" in low or "only when you ask" in low)
        check("and that nothing runs it as part of anything",
              "not by an agent" in low or "not run as part of" in low)
        check("it says the guide is written by hand",
              "by hand" in low and "instructions.md" in text)
        check("it gives the build command", "help_deck.py build" in text)
        check("and the check command", "help_deck.py check" in text)
        # `check` takes --out, not a positional. This file documented a bare
        # path for months and the command errors out.
        check("and documents check's --out flag rather than a bare path",
              "check --out" in text)
        # WHY THE GUIDE IS MARKDOWN has to be written down where somebody
        # editing the folder will read it, or the next person "improves" it
        # back into HTML and the link breaks again.
        check("it says why the guide is Markdown",
              "renders" in low and "html" in low)

    setup = os.path.join(ROOT, "help", "SETUP-NEW-MEMBER.md")
    check("help/SETUP-NEW-MEMBER.md is in help/", os.path.isfile(setup), setup)

    # The HTML page and the Pages workflow are GONE, and both have to stay
    # gone: the page could not render in a repo view, and Pages cannot serve a
    # private repository on a Free organization at all. A reappearance means
    # somebody rebuilt a route that does not work.
    check("the HTML guide is not back",
          not os.path.isfile(os.path.join(ROOT, "help", "instructions.html")))
    check("and neither is the Pages workflow",
          not os.path.isfile(os.path.join(
              ROOT, ".github", "workflows", "help-page.yml")))

    guide = os.path.join(ROOT, "help", "instructions.md")
    check("help/instructions.md exists", os.path.isfile(guide), guide)
    if not os.path.isfile(guide):
        return
    text = io.open(guide, encoding="utf-8").read()

    # ------------------------------------------------ it has to RENDER right
    # GitHub strips style= and class=, so a rule that depends on either is a
    # rule that silently does nothing. Catching it here is the only way it
    # gets caught, because the file still renders - just plainly.
    #
    # SCAN WHAT GITHUB RENDERS, WHICH IS NOT THE WHOLE FILE. The guide opens
    # with an HTML comment explaining these very rules, and both checks fired
    # on it - twice, for two different reasons. First as a bare word match on
    # prose ABOUT the rule rather than a violation of it, which is a check
    # that cries wolf, and a check that cries wolf gets deleted rather than
    # fixed. Then, once narrowed to an attribute inside a tag, `<[^>]*` still
    # matched: an HTML comment holds no `>` until its own terminator, so the
    # pattern ran from `<!--` across several lines to the word inside it.
    # Comments render as nothing, so they are not part of the question.
    visible = re.sub(r"<!--.*?-->", "", text, flags=re.S)

    check("no style= attribute in a tag (GitHub strips it)",
          not re.search(r"<[^>]*\bstyle\s*=", visible))
    check("no class= attribute in a tag (GitHub strips it too)",
          not re.search(r"<[^>]*\bclass\s*=", visible))
    check("nothing is loaded from the network",
          not re.search(r"!\[[^\]]*\]\(\s*https?://", visible)
          and not re.search(r"<img\b", visible))
    # The formatting that makes it read as a guide rather than a wall of text.
    check("it uses GitHub alert blocks", text.count("> [!") >= 8,
          text.count("> [!"))
    check("it collapses the command reference", "<details>" in text)
    check("<details> blocks are all closed",
          text.count("<details>") == text.count("</details>"),
          (text.count("<details>"), text.count("</details>")))

    # ------------------------------------------------ every anchor resolves
    heads = re.findall(r"^#{1,6}\s+(.*)$", text, re.M)
    anchors = {_gh_slug(h) for h in heads}
    links = re.findall(r"\]\(#([^)]+)\)", text)
    check("it has a contents table linking into itself", len(links) >= 10,
          len(links))
    broken = sorted({l for l in links if l not in anchors})
    check("every internal anchor resolves to a real heading",
          broken == [], broken)

    # ------------------------------- every relative link points at something
    # A relative link is the whole reason this file can defer to the skills
    # instead of restating them, so a dead one silently turns a deferral into
    # a dead end.
    rel = re.findall(r"\]\((?!#)(?!https?://)([^)]+)\)", text)
    base = os.path.join(ROOT, "help")
    missing = sorted({r for r in rel
                      if not os.path.exists(os.path.join(base, r))})
    check("every relative link points at a file that exists",
          missing == [], missing)

    for anchor in ("1-what-this-is", "2-install-it-once", "3-the-four-stages",
                   "4-just-say-what-you-want", "5-stage-a-idea-and-setup",
                   "6-stage-b-your-own-work",
                   "7-stage-c-the-writing-engine", "8-stage-d-submission",
                   "9-what-it-learns-from-you",
                   "10-every-command-on-its-own",
                   "11-when-something-looks-wrong",
                   "12-the-rules-that-never-bend"):
        check("section %s is anchored" % anchor, anchor in anchors)

    check("the cue table is above the module reference",
          text.index("4. Just say what you want")
          < text.index("10. Every command, on its own"))
    check("it names raw_images and the folder people call it instead",
          "source_images" in text and "raw_images" in text)
    check("it says blank is not an option for the AI-disclosure row",
          "not an option" in text or "blank is not" in text)
    check("it states that learned rules never leave the machine",
          "never leave" in text)
    check("every command shown is Windows-correct",
          "\\tools\\" not in text,
          "forward slashes: python tools/x.py, which is what the README says")
    check("it says what is not built yet where somebody would look",
          "not built" in text.lower() or "not yet" in text.lower())

    # ------------------------------------------------- the install section
    # This is the section that had gone stale hardest: a repository name that
    # does not exist, and no mention of the route that is now the primary one.
    check("it does NOT name the old repository",
          "research-paper-writing-aids" not in text,
          "the repo is mercer-science/paper-engine")
    check("it names the real repository",
          "mercer-science/paper-engine" in text)
    check("it gives the marketplace command",
          "/plugin marketplace add mercer-science/paper-engine" in text)
    check("and the install command",
          "/plugin install paper-engine@paper-engine-marketplace" in text)
    check("and both environment variables the install needs",
          "CLAUDE_CODE_PLUGIN_PREFER_HTTPS" in text
          and "CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE" in text)
    check("it still documents the clone route as well",
          "install_skills.py install" in text)
    check("it names the four prerequisites",
          all(w in text for w in ("Git", "pandoc", "PATH", "3.12")))

    # THE TWO HAZARDS THAT DESTROY WORK. Both are open defects with no fix, so
    # the warning is the only mitigation that exists and it has to be on the
    # page a new member actually reads.
    check("it warns never to run /plugin uninstall",
          "/plugin uninstall" in text
          and ("learned" in text.lower() or "no upstream" in text.lower()))
    check("it warns never to pass the toolkit around as a folder",
          "as a folder" in text.lower() and "zip" in text.lower())

    # Every skill the toolkit ships should be reachable from the page that
    # defers to it, and the page should not name a skill that is not there.
    skills_dir = os.path.join(ROOT, "skills")
    if os.path.isdir(skills_dir):
        for name in sorted(os.listdir(skills_dir)):
            if not os.path.isdir(os.path.join(skills_dir, name)):
                continue
            check("it links to the %s skill" % name,
                  "../skills/%s/SKILL.md" % name in text)


def main() -> int:
    print("help deck and help pages - specs/orchestration.md 5.1-5.4")
    test_modules_are_real()
    test_blind_markers_match()
    test_modular_commands_resolve()
    test_build_then_check()
    test_shipped_deck()
    test_help_files()
    print("\n%d/%d passed" % (PASSED, PASSED + FAILED)
          + (", %d FAILED" % FAILED if FAILED else "")
          + (", %d skipped" % SKIPPED if SKIPPED else ""))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
