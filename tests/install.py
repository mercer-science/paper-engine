#!/usr/bin/env python3
"""Measure the skill installer: that a pointer really points, that drift is
visible, and - the one that matters most - that a file this tool did not write
is never overwritten or deleted.

`install_skills.py` is small but it writes into the user's home directory and
one of its commands removes files. Everything here runs against a temporary
--dest, so the real personal skills directory is never touched by the suite.

Run:  python tests/install.py
"""

import importlib.util
import json
import re
import os
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

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
TOOLS = os.path.join(ROOT, "tools")
ENGINE = os.path.join(TOOLS, "install_skills.py")


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(TOOLS, filename))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {filename}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ins = _load("_install_engine", "install_skills.py")
lp = _load("_labpack_for_install", "labpack.py")

PASSED = 0
FAILED = 0


def check(label, passed, detail: object = ""):
    global PASSED, FAILED
    if passed:
        PASSED += 1
    else:
        FAILED += 1
    print(f"[{'pass' if passed else 'FAIL'}] {label}")
    # Detail is diagnostic, so it prints only when there is something to
    # diagnose - several of these carry a whole command's output.
    if detail and not passed:
        for line in str(detail).rstrip().splitlines():
            print(f"         {line}")
    return passed


SKIPPED = 0


def skip(name, why):
    """Announce a check that did NOT run. Never a pass.

    The real pack lives in its own repository and is not always beside this
    one. A contract measured only against a fixture is still measured, but
    saying so out loud is the difference between "we checked" and "there was
    nothing to check".
    """
    global SKIPPED
    SKIPPED += 1
    print(f"[skip] {name}" + chr(10) + f"         {why}")


def section(title):
    print(f"\n--- {title} " + "-" * max(0, 58 - len(title)))


def run(*argv):
    p = subprocess.run([sys.executable, ENGINE, *argv], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       timeout=60)
    return p.returncode, p.stdout, p.stderr


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def statuses(dest):
    return {r["name"]: r["status"] for r in ins.inspect(dest)["skills"]}


def test_pointers(dest):
    section("A pointer is written for every skill, and it points")

    names = [n for n, _ in ins.source_skills()]
    check("the toolkit has skills to install", len(names) >= 3, names)

    code, out, err = run("install", "--dest", dest)
    check("install exits 0", code == 0, err or out)

    for name in names:
        target = os.path.join(dest, name, "SKILL.md")
        if not check(f"{name}: a pointer is on disk", os.path.isfile(target)):
            continue
        got = read(target)
        src = os.path.join(ROOT, "skills", name, "SKILL.md")

        # The frontmatter is the only duplicated text in the whole design, and
        # it is what a harness reads to decide the skill is relevant. A
        # paraphrase here would change when the skill fires.
        front = ins.frontmatter(src)
        check(f"{name}: frontmatter is copied verbatim",
              got.startswith(f"---\n{front}\n---\n"),
              "the description decides when the skill fires")

        check(f"{name}: the pointer names the real SKILL.md", src in got)
        check(f"{name}: the pointer names the toolkit root", ROOT in got)
        check(f"{name}: the pointer carries the marker", ins.MARKER in got)

        # TODO.md §0, paid for twice on the same folder. The recorded path
        # dies whenever the toolkit is renamed or moved, and a harness that
        # cannot resolve a skill does not report a missing skill - it writes
        # the paper without it. No path form avoids this: the pointer lives in
        # the harness's directory and the target lives in the checkout, so
        # every route between them, absolute or relative, spells the folder
        # being renamed. What the pointer CAN do is refuse to be improvised
        # around, which is the difference between a stop and a wrong answer.
        check(f"{name}: the pointer says to stop when the path is dead",
              "STOP" in got,
              "a dead pointer that gives no instruction gets guessed around, "
              "and the guess looks like output")
        check(f"{name}: it names the command that repairs it",
              "install_skills.py install" in got,
              "the reader is told to stop, so it has to be told what undoes "
              "the stop")
        check(f"{name}: it gives a rule for finding the moved toolkit",
              "contains `tools/install_skills.py`" in got,
              "a reader that only has the path that just failed cannot "
              "recover; it needs what identifies the checkout")

        # A pointer that grew into a summary is the failure this design
        # exists to prevent: the summary gets read instead of the rules.
        body = got.split("---\n", 2)[-1]
        check(f"{name}: the body stays a pointer, not a copy",
              len(body) < 2500, f"{len(body)} chars")
        tail = read(src).strip()[-200:]
        check(f"{name}: no instructions are duplicated into it",
              tail not in got)

    check("status now reports every skill current",
          set(statuses(dest).values()) == {"current"}, statuses(dest))
    code, _, _ = run("status", "--dest", dest)
    check("status exits 0 when everything is installed", code == 0)

    before = {n: os.path.getmtime(os.path.join(dest, n, "SKILL.md"))
              for n in names}
    code, out, _ = run("install", "--dest", dest)
    after = {n: os.path.getmtime(os.path.join(dest, n, "SKILL.md"))
             for n in names}
    check("re-installing reports unchanged", out.count("unchanged"), len(names))
    check("re-installing rewrites nothing", before == after,
          "an unchanged pointer should not be touched")
    return names


def test_drift(dest, names):
    section("Drift is visible, and one command fixes it")

    name = names[0]
    target = os.path.join(dest, name, "SKILL.md")
    keep = read(target)

    write(target, keep.replace(ROOT, os.path.join("somewhere", "else")))
    check("a pointer to the wrong place reads as stale",
          statuses(dest)[name] == "stale", statuses(dest)[name])
    code, _, _ = run("status", "--dest", dest)
    check("status exits 1 when something needs installing", code == 1)

    code, out, _ = run("install", "--dest", dest)
    check("install repairs it", code == 0 and read(target) == keep, out)

    os.remove(target)
    check("a deleted pointer reads as missing",
          statuses(dest)[name] == "missing")
    check("the others are still current",
          {v for k, v in statuses(dest).items() if k != name} == {"current"})
    run("install", "--dest", dest)
    check("install puts it back", read(target) == keep)


def test_foreign(dest, names):
    section("A file this tool did not write is never touched")

    name = names[-1]
    target = os.path.join(dest, name, "SKILL.md")
    mine = "---\nname: x\ndescription: hand written by the user\n---\n\nkeep me\n"
    write(target, mine)

    check("a file without the marker reads as foreign",
          statuses(dest)[name] == "foreign", statuses(dest)[name])

    code, out, _ = run("install", "--dest", dest)
    check("install refuses rather than overwriting it", code == 2, out)
    check("the refusal leaves the file exactly as it was", read(target) == mine)
    check("the refusal says how to proceed", "--force" in out)
    check("the other skills still installed alongside the refusal",
          out.count("unchanged"), len(names) - 1)

    code, out, _ = run("uninstall", "--dest", dest, "--dry-run")
    check("uninstall would leave it alone", "left alone" in out, out)

    code, out, _ = run("uninstall", "--dest", dest)
    check("uninstall exits 0", code == 0, out)
    check("uninstall did not delete it", read(target) == mine)
    for other in names[:-1]:
        check(f"uninstall removed the pointer for {other}",
              not os.path.exists(os.path.join(dest, other, "SKILL.md")))
        check(f"uninstall cleaned up the empty {other} directory",
              not os.path.exists(os.path.join(dest, other)))

    code, out, _ = run("install", "--dest", dest, "--force")
    check("--force overwrites it when the user says so", code == 0, out)
    check("the forced pointer is ours now", statuses(dest)[name] == "current")


def test_dry_run():
    section("A dry run writes nothing")

    dest = tempfile.mkdtemp(prefix="skills_dry_")
    try:
        code, out, _ = run("install", "--dest", dest, "--dry-run")
        check("install --dry-run exits 0", code == 0, out)
        check("install --dry-run says what it would write",
              "would write" in out, out)
        check("install --dry-run creates nothing", os.listdir(dest) == [],
              os.listdir(dest))

        run("install", "--dest", dest)
        names = [n for n, _ in ins.source_skills()]
        code, out, _ = run("uninstall", "--dest", dest, "--dry-run")
        check("uninstall --dry-run says what it would remove",
              "would remove" in out, out)
        check("uninstall --dry-run deletes nothing",
              all(os.path.isfile(os.path.join(dest, n, "SKILL.md"))
                  for n in names))
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def test_json(dest):
    section("--json answers on both sides of the subcommand")

    for order in ("leading", "trailing"):
        argv = (["--json", "status", "--dest", dest] if order == "leading"
                else ["status", "--dest", dest, "--json"])
        _code, out, _err = run(*argv)
        try:
            res = json.loads(out)
        except json.JSONDecodeError as e:
            check(f"status --json parses ({order})", False, f"{e}: {out[:200]}")
            continue
        check(f"status --json parses ({order})", True)
        check(f"it carries root, dest and skills ({order})",
              {"root", "dest", "skills"} <= set(res), sorted(res))
        # The generated file bodies are several kB of markdown the caller
        # already has on disk; JSON is for the verdict.
        check(f"it does not dump the generated files ({order})",
              all("want" not in row for row in res["skills"]))


def test_pointer_reaches_a_reference_file(dest):
    section("A split skill's reference files resolve from its pointer")

    # `writing-engine`'s SKILL.md was 219 KB in one file and is now a spine
    # plus one `reference/*.md` per stage (specs/context-compaction.md 3).
    # The pointer mechanism is unchanged by that - the pointer carries the
    # frontmatter verbatim plus the absolute path of the real SKILL.md, and
    # the reference files are siblings resolved relative to that path. But
    # "unchanged" is a claim, and this suite exists to check claims.
    import glob

    refs = sorted(glob.glob(os.path.join(ROOT, "skills", "*", "reference",
                                         "*.md")))
    check("some skill has reference files", bool(refs), refs)
    if not refs:
        return

    for ref in refs:
        skill = os.path.basename(os.path.dirname(os.path.dirname(ref)))
        rel = os.path.relpath(ref, os.path.join(ROOT, "skills", skill))
        rel = rel.replace(os.sep, "/")
        pointer = os.path.join(dest, skill, "SKILL.md")
        if not check(f"{skill}: the pointer is on disk",
                     os.path.isfile(pointer)):
            continue
        got = read(pointer)
        # The one path the pointer carries. Everything else in the skill is
        # found from it, so this is the whole of what has to resolve.
        named = os.path.join(ROOT, "skills", skill, "SKILL.md")
        check(f"{skill}: the pointer still names the real SKILL.md",
              named in got)
        landed = os.path.join(os.path.dirname(named),
                              *rel.split("/"))
        check(f"{skill}: {rel} resolves from it",
              os.path.isfile(landed), landed)

        # And the spine names it, so a reader that has resolved the pointer
        # can find the file without being told the directory layout.
        check(f"{skill}: the spine names {rel}",
              rel in read(named),
              "a reference file the spine does not name is a rule with no "
              "route to a reader")

    # The pointer is NOT a second copy of the skill: it carries frontmatter
    # and a path, and the reference files travel with the real directory.
    for skill in sorted({os.path.basename(os.path.dirname(os.path.dirname(r)))
                         for r in refs}):
        pointer = os.path.join(dest, skill, "SKILL.md")
        check(f"{skill}: no reference file was copied beside the pointer",
              not os.path.isdir(os.path.join(dest, skill, "reference")),
              "the pointer route must not fork the skill's text in two")


# ---------------------------------------------------------------------------
# The pack, as a second distribution (lab-resource-pack 6, 7 step 10)
# ---------------------------------------------------------------------------
#
# A pack is content and a manifest and NO CODE, so the thing to hold it to is
# not "does its installer work" - there is no installer - but the two claims a
# member's `/plugin install` depends on: that the repo is its own marketplace,
# and that the manifest carries the marker the engine resolves it by.
#
# Measured against a REAL install on 2026-09-17 before any of this was written
# (specs/probes/labpack-2026-09-17/): a skill-less, content-only plugin does
# install, at `cache/<marketplace>/<plugin>/<version>/`, with the marketplace's
# git clone beside it. Everything here is the contract that install proved.

PACK_MARKER = "labPack"


def _pack_roots():
    """Every pack this machine can see, for the shape checks.

    A fixture pack always, because the suite must measure the CONTRACT on any
    machine. The real pack as well when it is reachable - as a sibling of the
    toolkit, or installed - because a contract nothing real satisfies is a
    contract nobody has tested.
    """
    roots = []
    sibling = os.path.join(os.path.dirname(ROOT), "jensen-resource-pack")
    if os.path.isfile(os.path.join(sibling, ".claude-plugin", "plugin.json")):
        roots.append(("the pack repository beside this toolkit", sibling))
    return roots


def _fixture_pack(root):
    """A pack built to the 3 contract, with nothing else in it."""
    os.makedirs(os.path.join(root, ".claude-plugin"), exist_ok=True)
    write(os.path.join(root, ".claude-plugin", "plugin.json"), json.dumps({
        "$schema": "https://code.claude.com/schemas/plugin.json",
        "name": "fixture-resource-pack",
        "displayName": "Fixture Lab Resource Pack",
        "version": "2026.9.9",
        "description": "Facility knowledge for a fixture lab. Content only.",
        "author": {"name": "A Maintainer", "email": "someone@example.edu"},
        "license": "UNLICENSED",
        "labPack": True}, indent=2))
    write(os.path.join(root, ".claude-plugin", "marketplace.json"),
          json.dumps({
              "$schema": "https://code.claude.com/schemas/marketplace.json",
              "name": "fixture-resource-pack-marketplace",
              "owner": {"name": "A Maintainer",
                        "email": "someone@example.edu"},
              "description": "A fixture lab's facility knowledge.",
              "plugins": [{"name": "fixture-resource-pack",
                           "source": "./"}]}, indent=2))
    write(os.path.join(root, "lab.yml"),
          "pack: fixture-resource-pack" + chr(10) +
          'display_name: "Fixture Lab"' + chr(10) +
          'curated_on: "2026-09-09"' + chr(10))
    for name in ("instruments.md", "bootcamp.md", "sops.md", "README.md"):
        write(os.path.join(root, name), "# " + name + chr(10))
    return root


def _check_pack_shape(label, root):
    manifest = json.loads(read(
        os.path.join(root, ".claude-plugin", "plugin.json")))
    market_path = os.path.join(root, ".claude-plugin", "marketplace.json")

    check(f"{label}: the manifest carries the pack marker",
          manifest.get(PACK_MARKER) is True,
          "rung 3 of the resolution ladder finds a pack by this and nothing "
          "else, so a pack without it is invisible and nothing says so")
    check(f"{label}: it names a version",
          bool(str(manifest.get("version", "")).strip()), manifest)
    check(f"{label}: the version is a DATE, no leading zeros",
          re.match(r"^\d{4}\.(0|[1-9]\d*)\.(0|[1-9]\d*)$",
                   str(manifest.get("version", ""))) is not None,
          f"{manifest.get('version')!r} - semver forbids leading zeros in a "
          f"numeric identifier, so 2026.09.09 risks being accepted here and "
          f"rejected on a member's machine")

    check(f"{label}: the repo is its own marketplace",
          os.path.isfile(market_path),
          "two commands for a member, and the second one needs this file")
    market = json.loads(read(market_path))
    listed = [e.get("name") for e in market.get("plugins") or []]
    check(f"{label}: the marketplace lists the plugin by its own name",
          manifest.get("name") in listed, (manifest.get("name"), listed))
    sources = [e.get("source") for e in market.get("plugins") or []]
    check(f"{label}: and sources it from the repo root, not a subfolder",
          sources == ["./"], sources)

    # 3.3 - what deliberately is NOT in a pack. A pack that can execute is a
    # pack that has to be reviewed like code before a lab installs it.
    for forbidden in ("skills", "commands", "hooks", "agents", "tools"):
        check(f"{label}: no {forbidden}/ - a pack is content only",
              not os.path.isdir(os.path.join(root, forbidden)),
              "3.3: content only, so installing one is not running one")
    check(f"{label}: no rules.yml - learning never travels",
          not os.path.isfile(os.path.join(root, "rules.yml")),
          "plugin-packaging 6.0 is not weakened by there being a new "
          "repository to not put it in")
    check(f"{label}: lab.yml is present and is the only machine-read file",
          os.path.isfile(os.path.join(root, "lab.yml")))

    # The engine has to actually resolve what this describes.
    check(f"{label}: the engine recognises it as a pack",
          lp.is_pack(root), root)
    info = lp.pack_info(root)
    check(f"{label}: and reads a display name out of it",
          bool(info["display_name"]), info)
    check(f"{label}: the version it reports comes from plugin.json",
          info["version"] == str(manifest.get("version")), info)


def test_pack_manifests():
    section("the pack: two manifests, a marker, and no code")

    fixture = tempfile.mkdtemp(prefix="pack_fixture_")
    try:
        _check_pack_shape("fixture pack", _fixture_pack(fixture))
    finally:
        shutil.rmtree(fixture, ignore_errors=True)

    real = _pack_roots()
    if not real:
        skip("the real pack's manifests",
             "no pack repository beside this toolkit - the contract is still "
             "measured against the fixture above")
        return
    for label, root in real:
        _check_pack_shape(label, root)


def test_pack_carries_no_engine_and_engine_carries_no_lab():
    section("the engine never learns a lab's name (decision 9)")

    # The pack contract is what makes lab number two cheap: a new repo, five
    # files, `pack:` set to something else, and nothing in the engine changes.
    engine_manifest = json.loads(read(
        os.path.join(ROOT, ".claude-plugin", "plugin.json")))
    check("the engine's own manifest carries no pack marker",
          PACK_MARKER not in engine_manifest,
          "the engine is not a pack and must never resolve as one")
    check("and the engine's version is a date too",
          re.match(r"^\d{4}\.(0|[1-9]\d*)\.(0|[1-9]\d*)$",
                   str(engine_manifest.get("version", ""))) is not None,
          engine_manifest.get("version"))

    # tests/portability.py owns the no-lab-name guard over the whole tree.
    # This is the narrower half of it, stated where the pack is described.
    for rel in ("tools/labpack.py", "tools/release.py"):
        text = read(os.path.join(ROOT, rel)).lower()
        check(f"{rel} names no lab",
              "jensen" not in text,
              "a lab name in the engine is a lab the engine has to be "
              "changed for")


def test_doctor(dest):
    """`doctor` - the engine half of README step 4.

    The sentence "Check the paper engine is set up on this machine" had no
    command behind it until 2026-09-24, so an agent improvised a different
    set of probes every time. What is asserted here is the part improvisation
    cannot give: a fixed row set, a `blocks` sentence on every row, and an
    exit code that distinguishes ready from usable-with-gaps from not-ready.
    """
    code, out, _err = run("doctor", "--dest", dest, "--json")
    res = json.loads(out)

    names = [r["name"] for r in res["rows"]]
    check("doctor reports every requirement the README names",
          set(names) >= {"python", "git", "pandoc", "Rscript",
                         "requests", "lxml", "pptx"}, names)
    # 'pandoc missing' means nothing to somebody installing this for the
    # first time; what it stops them doing is the useful half.
    check("every row says what its absence BLOCKS",
          [r["name"] for r in res["rows"] if not r.get("blocks")] == [],
          [r["name"] for r in res["rows"] if not r.get("blocks")])
    check("every row is ok, missing or unknown - never a bare boolean",
          {r["state"] for r in res["rows"]} <= {"ok", "missing", "unknown"},
          sorted({r["state"] for r in res["rows"]}))
    # A member who reads `R MISSING` and stops has been failed by the
    # report: R is optional, and the row has to say so.
    check("the required and the optional are separated",
          all(isinstance(r["required"], bool) for r in res["rows"]),
          [(r["name"], r.get("required")) for r in res["rows"]])
    check("pandoc is required and R is not - one builds the .docx, the "
          "other renders floats a paper can be written without",
          [(r["name"], r["required"]) for r in res["rows"]
           if r["name"] in ("pandoc", "Rscript")]
          == [("pandoc", True), ("Rscript", False)],
          [(r["name"], r["required"]) for r in res["rows"]
           if r["name"] in ("pandoc", "Rscript")])

    # The trap CLAUDE.md records: Windows installs R and does not put it on
    # PATH, and a session once reported R absent on a machine that had it.
    r_row = [r for r in res["rows"] if r["name"] == "Rscript"][0]
    if shutil.which("Rscript") is None and r_row["state"] == "ok":
        check("R is found under Program Files when it is not on PATH",
              "Program Files" in r_row["found"], r_row["found"])
    else:
        skip("the Program Files fallback for R",
             "R is on PATH here, or is not installed")

    check("the pointers are reported beside the programs",
          set(res["skills"]) >= {"dest", "current", "stale", "missing"},
          sorted(res["skills"]))
    check("...and doctor reads the --dest it was given",
          os.path.normcase(res["skills"]["dest"])
          == os.path.normcase(dest), res["skills"]["dest"])

    # A freshly made temporary dest has no pointers in it, so this run is
    # the not-ready case and must say so rather than exiting 0.
    check("a machine with no pointers installed is not reported ready",
          res["ready"] is False, res["skills"])
    # A setup check that exits 0 on a machine that is not set up is the
    # wrong-answer-at-exit-0 shape this toolkit refuses.
    check("...and the exit code says so", code in (1, 2), code)

    # And it must not have DONE anything about it: reporting a gap and
    # fixing it are different operations with different risks.
    check("doctor installed nothing", os.listdir(dest) == [],
          os.listdir(dest))

    code2, out2, _ = run("doctor", "--dest", dest)
    check("the text printer runs and names the toolkit",
          "paper engine - setup check" in out2 and "toolkit" in out2,
          out2[:200])
    check("...and names the command that fixes a missing pointer",
          "install_skills.py install" in out2, out2[-300:])
    check("...at the same exit code as --json", code2 == code, (code2, code))



def test_manifest_skills_reach_codex():
    """specs/codex-plugin-2026-09-30.md 5: Codex reads `.claude-plugin/
    plugin.json` itself, and its manifest reader drops a `skills` entry that
    does not start with `./` (*"path must start with `./`"*, core-plugins
    manifest.rs). A dropped skill is one Codex never offers, and nothing
    says so - so the shape is held here, where it can fail."""
    section("the manifest's skills reach Codex: every entry ./ and real")
    manifest = json.loads(read(os.path.join(ROOT, ".claude-plugin",
                                            "plugin.json")))
    skills = manifest.get("skills")
    skills = [skills] if isinstance(skills, str) else list(skills or [])
    check("every skills entry starts with ./",
          [e for e in skills if not str(e).startswith("./")] == [],
          [e for e in skills if not str(e).startswith("./")])
    check("...and names a folder with a SKILL.md in it",
          [e for e in skills if not os.path.isfile(
              os.path.join(ROOT, str(e)[2:], "SKILL.md"))] == [],
          skills)
    on_disk = sorted("./skills/" + n for n in os.listdir(
        os.path.join(ROOT, "skills"))
        if os.path.isfile(os.path.join(ROOT, "skills", n, "SKILL.md")))
    check("...and lists every skill there is", sorted(skills) == on_disk,
          (skills, on_disk))
    # One manifest, not two (spec 0's decision). If a Codex-only manifest is
    # ever added, it must not drift from this one.
    codex = os.path.join(ROOT, ".codex-plugin", "plugin.json")
    if os.path.isfile(codex):
        other = json.loads(read(codex))
        check("a .codex-plugin manifest agrees on name, version and skills",
              [other.get(k) for k in ("name", "version", "skills")]
              == [manifest.get(k) for k in ("name", "version", "skills")],
              other)
    else:
        check("there is one manifest, which Codex reads",
              not os.path.exists(os.path.join(ROOT, ".codex-plugin")), True)


def test_doctor_codex(dest):
    """doctor's Codex rows: optional, so they never change `ready`, and they
    see the failure Codex itself is silent about."""
    section("doctor - the Codex rows")
    sandbox = tempfile.mkdtemp(prefix="doctor_codex_")
    keep = {k: os.environ.get(k) for k in
            ("PAPER_ENGINE_CODEX_PLUGINS_DIR", "CODEX_HOME")}
    try:
        codex = os.path.join(sandbox, "codex", "plugins")
        os.environ["PAPER_ENGINE_CODEX_PLUGINS_DIR"] = codex
        os.environ.pop("CODEX_HOME", None)

        none = ins.codex_check()
        check("no Codex install reads as not_installed, not as a failure",
              none["state"] == "not_installed", none)
        res = ins.doctor(dest)
        check("doctor carries the Codex section", "codex" in res,
              sorted(res))
        ready_without = res["ready"]

        install = os.path.join(codex, "cache", "paper-engine-marketplace",
                               "paper-engine", "2026.9.30")
        shutil.copytree(os.path.join(ROOT, ".claude-plugin"),
                        os.path.join(install, ".claude-plugin"))
        shutil.copytree(os.path.join(ROOT, "skills"),
                        os.path.join(install, "skills"))
        write(os.path.join(sandbox, "codex", "config.toml"),
              '[plugins."paper-engine@paper-engine-marketplace"]\n'
              'enabled = true\n')
        cx = ins.codex_check()
        check("an install found in Codex's cache is ok", cx["state"] == "ok",
              cx)
        check("...and every skill in the manifest is one Codex will list",
              len(cx["skills_listed"]) == 8 and cx["skills_missing"] == [],
              cx)
        check("...and the enabled flag is read from config.toml",
              cx["enabled"] is True, cx)
        check("the Codex rows never change `ready`",
              ins.doctor(dest)["ready"] == ready_without, ready_without)

        mpath = os.path.join(install, ".claude-plugin", "plugin.json")
        m = json.loads(read(mpath))
        m["skills"] = ["skills/analysis"] + m["skills"][1:]
        write(mpath, json.dumps(m))
        cx = ins.codex_check()
        check("an entry without ./ is named as one Codex drops",
              cx["skills_missing"] == ["skills/analysis"]
              and cx["state"] == "problem", cx)

        write(os.path.join(sandbox, "codex", "config.toml"),
              '[plugins."paper-engine@paper-engine-marketplace"]\n'
              'enabled = false\n')
        check("a plugin disabled in Codex is a problem, said as one",
              ins.codex_check()["enabled"] is False, ins.codex_check())

        _code, out, _ = run("doctor", "--dest", dest)
        check("the text printer has a Codex row", "Codex" in out, out[-400:])
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(sandbox, ignore_errors=True)


def main():
    print("install - the skill pointers, and what they refuse to overwrite")
    dest = tempfile.mkdtemp(prefix="skills_test_")
    try:
        names = test_pointers(dest)
        test_pointer_reaches_a_reference_file(dest)
        test_drift(dest, names)
        test_foreign(dest, names)
        test_json(dest)
    finally:
        shutil.rmtree(dest, ignore_errors=True)
    # Its own dest, and deliberately EMPTY: doctor's not-ready branch is the
    # one worth asserting, and the dest above has pointers in it by now.
    doc_dest = tempfile.mkdtemp(prefix="skills_doctor_")
    try:
        test_doctor(doc_dest)
        test_doctor_codex(doc_dest)
    finally:
        shutil.rmtree(doc_dest, ignore_errors=True)
    test_dry_run()
    test_pack_manifests()
    test_pack_carries_no_engine_and_engine_carries_no_lab()
    test_manifest_skills_reach_codex()

    print(f"\n{PASSED}/{PASSED + FAILED} passed"
          + (f", {FAILED} FAILED" if FAILED else "")
          + (f", {SKIPPED} skipped" if SKIPPED else ""))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
