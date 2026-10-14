#!/usr/bin/env python3
"""
install_skills.py - make the skills callable from any directory.

The skills already work as they are: point an agent at a SKILL.md and it has
everything it needs. The narrower problem this solves is that the agent has to
be *told* the file exists. A harness that loads skills automatically reads them
from a personal directory, so a session started in a paper's own folder has
none of this in scope - and the failure is silent, because an agent that never
heard of the skill just writes a paper without it.

So this installs a pointer, not a copy. Each installed file is a few lines
naming the real SKILL.md and the toolkit root. The instructions stay in one
place and cannot drift out of sync with it. Only the frontmatter is duplicated,
because that is what a harness reads to decide a skill is relevant, and
`install` rewrites the pointer whenever the source changes - `status` says when
that is due.

Three commands:

  install     write the pointers, or bring them up to date
  status      installed, stale, missing, or a file this tool did not write
  uninstall   remove the pointers this tool wrote, and nothing else

Usage:
  python install_skills.py status
  python install_skills.py install
  python install_skills.py install --dry-run
  python install_skills.py install --dest ~/.config/agent/skills
  python install_skills.py uninstall --json
"""

from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import re
import shutil
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The six skills live one directory down, in skills/, rather than as six
# near-empty folders at the toolkit root. The plugin manifest names the same
# paths; keep the two in step.
SKILLS_DIR = os.path.join(ROOT, "skills")

# The default names one harness's convention - the personal skills directory
# that Claude Code reads. It is a default, not an assumption: --dest takes any
# directory, and what gets written is plain markdown with YAML frontmatter,
# which is what every harness that auto-loads skills expects. AGENTS.md remains
# the harness-neutral contract; this file is the convenience that saves the
# user retyping a path.
DEFAULT_DEST = os.path.join("~", ".claude", "skills")

# Written into every pointer, and the only thing that makes one removable. A
# file without it belongs to someone else and is never overwritten or deleted.
MARKER = "<!-- written by tools/install_skills.py -->"


def source_skills() -> list[tuple[str, str]]:
    """(name, path) for every SKILL.md under skills/, by directory name."""
    out: list[tuple[str, str]] = []
    try:
        entries = sorted(os.listdir(SKILLS_DIR))
    except OSError:
        return out
    for entry in entries:
        path = os.path.join(SKILLS_DIR, entry, "SKILL.md")
        if os.path.isfile(path):
            out.append((entry, path))
    return out


def frontmatter(path: str) -> str | None:
    """The YAML block a harness reads to decide the skill is relevant."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    return m.group(1) if m else None


def pointer(name: str, front: str) -> str:
    """The installed file: frontmatter copied verbatim, body generated.

    The body is deliberately thin. Everything a summary here could say is
    already said better in the file it points at, and a summary that drifts is
    worse than no summary - it would be read instead of the real rules.
    """
    src = os.path.join(SKILLS_DIR, name, "SKILL.md")
    engine = os.path.join(ROOT, "tools", "manuscript.py")
    return f"""---
{front}
---

{MARKER}

# {name}

The instructions for this skill are not in this file. They are here:

**{src}**

Read that file in full before doing anything else. This pointer exists so the
skill can be found from any directory. It is not a summary of the real one, and
acting on it alone means running the skill without the rules that make it worth
running.

**If that file is not there, STOP and tell the user.** It means the toolkit was
moved or renamed after this pointer was written, so the skill's real rules are
unreachable. Do not improvise a replacement and do not carry on without them: a
harness that cannot load a skill does not announce a missing skill, it does the
work without it, and the result looks finished. The toolkit is whichever
directory contains `tools/install_skills.py`; once it is found,
`python <toolkit>/tools/install_skills.py install` rewrites every pointer, and
`status` afterwards must read `current` for all of them.

Two things that file needs and cannot work out from where you are standing:

- **The toolkit is at `{ROOT}`.** Every engine path in the skill is written
  relative to it, so `tools/manuscript.py` means `{engine}`.
- **Do not change directory.** Call the engines by that absolute path and pass
  the user's project as its own absolute path. Nothing here needs a working
  directory, and changing one is how output lands in the wrong folder.

The skill file carries its own instructions in full, so there is no project
brief to read alongside it.
"""


def target_for(name: str, dest: str) -> str:
    return os.path.join(dest, name, "SKILL.md")


def inspect(dest: str) -> dict:
    """What is installed, what is stale, what is not ours to touch."""
    dest = os.path.abspath(os.path.expanduser(dest))
    skills: list[dict] = []
    errors: list[str] = []

    for name, src in source_skills():
        front = frontmatter(src)
        target = target_for(name, dest)
        row = {"name": name, "source": src, "target": target}

        if front is None:
            row["status"] = "unreadable"
            row["note"] = "no YAML frontmatter - a harness cannot index it"
            errors.append(f"{name}: {row['note']}")
            skills.append(row)
            continue

        row["want"] = pointer(name, front)
        if not os.path.isfile(target):
            row["status"] = "missing"
        else:
            try:
                with open(target, "r", encoding="utf-8") as fh:
                    got = fh.read()
            except OSError as e:
                row["status"] = "unreadable"
                row["note"] = str(e)
                errors.append(f"{name}: {e}")
                skills.append(row)
                continue
            if MARKER not in got:
                row["status"] = "foreign"
                row["note"] = ("a skill of this name is already installed and "
                               "this tool did not write it")
            elif got != row["want"]:
                row["status"] = "stale"
                row["note"] = "points somewhere else, or the description changed"
            else:
                row["status"] = "current"
        skills.append(row)

    return {"root": ROOT, "dest": dest, "skills": skills, "errors": errors}


def install(dest: str, dry_run: bool = False, force: bool = False) -> dict:
    res = inspect(dest)

    for row in res["skills"]:
        status = row["status"]
        if status == "unreadable":
            row["action"] = "skipped"
            continue
        if status == "current":
            row["action"] = "unchanged"
            continue
        if status == "foreign" and not force:
            # Refusing is the whole point. Overwriting a skill someone wrote by
            # hand would destroy work with no way back, and a name collision is
            # exactly the case where the user needs to decide.
            row["action"] = "refused"
            res["errors"].append(
                f"{row['name']}: {row['note']} - move it, or re-run with "
                f"--force to overwrite it")
            continue

        row["action"] = "would write" if dry_run else "wrote"
        if dry_run:
            continue
        try:
            os.makedirs(os.path.dirname(row["target"]), exist_ok=True)
            with open(row["target"], "w", encoding="utf-8", newline="\n") as fh:
                fh.write(row["want"])
        except OSError as e:
            row["action"] = "failed"
            res["errors"].append(f"{row['name']}: {e}")

    res["dry_run"] = dry_run
    return res


def uninstall(dest: str, dry_run: bool = False) -> dict:
    res = inspect(dest)

    for row in res["skills"]:
        status = row["status"]
        if status == "missing":
            row["action"] = "not installed"
            continue
        if status == "foreign":
            # Same rule as install, and it matters more here: this command
            # deletes. A file without the marker was written by someone else.
            row["action"] = "left alone"
            continue
        row["action"] = "would remove" if dry_run else "removed"
        if dry_run:
            continue
        try:
            os.remove(row["target"])
            parent = os.path.dirname(row["target"])
            if not os.listdir(parent):
                os.rmdir(parent)
        except OSError as e:
            row["action"] = "failed"
            res["errors"].append(f"{row['name']}: {e}")

    res["dry_run"] = dry_run
    return res


def print_result(res: dict, verb: str) -> None:
    print(f"{verb} {res['dest']}")
    print(f"  toolkit  {res['root']}")
    print()
    for row in res["skills"]:
        action = row.get("action", row["status"])
        print(f"  {action:<14}{row['name']:<26}{row.get('note', '')}")
    if not res["skills"]:
        print(f"  no SKILL.md found under {SKILLS_DIR}")
    for err in res["errors"]:
        print(f"\n  ! {err}")


def print_status(res: dict) -> None:
    print(f"skills in {res['dest']}")
    print(f"  toolkit  {res['root']}")
    print()
    for row in res["skills"]:
        print(f"  {row['status']:<14}{row['name']:<26}{row.get('note', '')}")
    stale = [r for r in res["skills"] if r["status"] in ("missing", "stale")]
    if stale:
        print(f"\n  install them with:  python "
              f"{os.path.join('tools', 'install_skills.py')} install")


def for_json(res: dict) -> dict:
    """The result without the generated file bodies - a caller wants the
    verdict, not several kilobytes of markdown it already has on disk."""
    return {**res, "skills": [{k: v for k, v in row.items() if k != "want"}
                              for row in res["skills"]]}


# ---------------------------------------------------------------------------
# doctor - "Check the paper engine is set up on this machine."
# ---------------------------------------------------------------------------
#
# That sentence is step 4 of the public README and, until 2026-09-24, nothing
# answered it. An agent asked it improvised: eight or ten shell probes, a
# different set every time, in whatever order occurred to it, with no way for
# the member to tell a check that FAILED from one that was never run. That is
# the shape this toolkit exists to refuse, and it was sitting in the install
# instructions.
#
# Every row says what it BLOCKS rather than merely whether it is there. "pandoc
# missing" means nothing to somebody installing this for the first time;
# "pandoc missing - the .docx cannot be built; everything up to it works" is
# the sentence they need. The optional rows say so in the same breath, because
# a member who reads `R  missing` and stops has been failed by the report.
#
# It NEVER installs anything. Reporting a gap and fixing it are different
# operations with different risks, and the second one belongs to the member.

REQUIREMENTS: list[dict] = [
    {"name": "python", "kind": "program", "required": True,
     "blocks": "everything - every engine in the toolkit is Python"},
    {"name": "git", "kind": "program", "required": True,
     "blocks": "installing and updating the toolkit"},
    {"name": "pandoc", "kind": "program", "required": True,
     "blocks": "assemble - the .docx build. Everything up to it still works"},
    {"name": "Rscript", "kind": "program", "required": False,
     "blocks": "rendering figures and tables. On Windows R is often installed "
               "and not on PATH - this looks under Program Files too"},
    {"name": "requests", "kind": "package", "required": True,
     "blocks": "every literature search and every citation check"},
    {"name": "lxml", "kind": "package", "required": True,
     "blocks": "reading full text and tracked changes"},
    {"name": "pypdf", "kind": "package", "required": False,
     "blocks": "reading a PDF a member supplied"},
    {"name": "pptx", "kind": "package", "required": False,
     "blocks": "drawn panel art and the graphical abstract. Without it the "
               "engine asks for a PNG instead", "pip": "python-pptx"},
]

# Windows installs R without putting it on PATH, every time, and CLAUDE.md
# records a real session that reported R absent on a machine that had it.
# Looking is three lines; being wrong about it costs somebody an afternoon.
R_GLOBS = (r"C:\Program Files\R\*\bin\Rscript.exe",
           r"C:\Program Files\R\*\bin\x64\Rscript.exe")

# The floor the README states. A tuple rather than a literal in the
# comparison, so the check is about the machine and not about whatever
# version a type checker happens to be configured for.
MIN_PYTHON: tuple[int, int] = (3, 12)


def _find_program(name: str) -> str:
    hit = shutil.which(name)
    if hit:
        return hit
    if name == "Rscript":
        for pat in R_GLOBS:
            found = sorted(glob.glob(pat))
            if found:
                return found[-1]
    return ""


def _find_package(mod: str) -> str:
    try:
        spec = importlib.util.find_spec(mod)
    except (ImportError, ValueError):
        return ""
    return getattr(spec, "origin", "") or "" if spec is not None else ""


def _labpack():
    """The pack engine, for its one definition of where each CLI keeps its
    plugins. None on a copy without it, and the Codex section says unknown."""
    try:
        spec = importlib.util.spec_from_file_location(
            "labpack_for_install", os.path.join(ROOT, "tools", "labpack.py"))
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def codex_check() -> dict:
    """Is this toolkit installed in Codex, and does Codex see every skill?

    Optional in the way R is: nothing here changes `ready` or the exit code,
    because a member who works only in Claude Code has nothing to fix. What
    it checks is the failure Codex would not report itself. Its manifest
    reader drops a `skills` entry that does not start with `./` (the source
    says *"path must start with `./`"*), and a skill that is dropped is one
    that is never offered, which nobody sees.

    Measured on Codex 0.159.3: the install is a whole copy of the repository
    under `cache/<marketplace>/<plugin>/<version>/`, and `config.toml` holds
    `[plugins."<plugin>@<marketplace>"] enabled = true`.
    """
    manifest = {}
    try:
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"),
                  encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError):
        pass
    name = str(manifest.get("name") or "paper-engine")
    out: dict = {"cli": shutil.which("codex") or "", "plugin": name,
                 "installs": [], "install": "", "version": "",
                 "enabled": None, "skills_listed": [], "skills_missing": [],
                 "state": "unknown", "note": ""}
    lp = _labpack()
    if lp is None:
        out["note"] = "labpack.py could not be loaded"
        return out
    try:
        root = lp.codex_plugins_dir()
        hits = sorted(glob.glob(os.path.join(root, "cache", "*", name, "*",
                                             ".claude-plugin", "plugin.json")))
        out["installs"] = [os.path.dirname(os.path.dirname(h)) for h in hits]
        if not out["installs"]:
            out["state"] = "not_installed"
            return out
        # One version directory is the measured case - an upgrade deletes the
        # old one - so more than one is reported rather than chosen between.
        install = out["installs"][-1]
        out["install"] = install
        im = lp.read_json(os.path.join(install, ".claude-plugin",
                                       "plugin.json"))
        out["version"] = str(im.get("version") or "")
        skills = im.get("skills")
        skills = [skills] if isinstance(skills, str) else (skills or [])
        for entry in skills:
            entry = str(entry)
            ok = (entry.startswith("./") and os.path.isfile(
                os.path.join(install, entry[2:], "SKILL.md")))
            (out["skills_listed"] if ok else out["skills_missing"]).append(
                entry)
        market = os.path.basename(os.path.dirname(os.path.dirname(install)))
        table = (lp.codex_config().get("plugins") or {}).get(
            "%s@%s" % (name, market))
        if isinstance(table, dict) and "enabled" in table:
            out["enabled"] = bool(table["enabled"])
        out["state"] = ("ok" if not out["skills_missing"]
                        and out["enabled"] is not False
                        and len(out["installs"]) == 1 else "problem")
    except Exception as exc:                            # pragma: no cover
        out["state"] = "unknown"
        out["note"] = str(exc)[:200]
    return out


def doctor(dest: str = DEFAULT_DEST) -> dict:
    """Is this machine set up to run the toolkit, and what is each gap for?

    Reports; never installs. Every failure to LOOK is reported as `unknown`
    rather than as a miss, because "we could not check" and "it is not there"
    are different sentences and only one of them means go and install
    something.
    """
    res: dict = {"toolkit": ROOT, "rows": [], "skills": {}, "errors": [],
                 "missing_required": [], "missing_optional": [], "unknown": []}

    for req in REQUIREMENTS:
        row = {"name": req["name"], "kind": req["kind"],
               "required": req["required"], "blocks": req["blocks"],
               "found": "", "state": "unknown"}
        try:
            if req["kind"] == "program":
                # `python` is the interpreter running this file. Asking PATH
                # for it would answer a different question and can answer it
                # wrongly - a member on a venv has a `python` that is not
                # this one.
                row["found"] = (sys.executable if req["name"] == "python"
                                else _find_program(req["name"]))
            else:
                row["found"] = _find_package(req["name"])
            row["state"] = "ok" if row["found"] else "missing"
        except Exception as exc:                        # pragma: no cover
            row["state"] = "unknown"
            row["note"] = str(exc)[:200]
        if req.get("pip"):
            row["pip"] = req["pip"]
        res["rows"].append(row)
        bucket = ("unknown" if row["state"] == "unknown"
                  else "missing_required" if req["required"]
                  else "missing_optional")
        if row["state"] != "ok":
            res[bucket].append(req["name"])

    # Python's own version is a requirement with a number attached, so it is
    # checked rather than assumed by the row above. Read into a plain tuple
    # first: compared against `sys.version_info` directly, a type checker
    # folds the comparison to the version IT is configured for and reports
    # the branch as dead - which is a statement about the checker's config
    # and not about the machine this will run on.
    running = (int(sys.version_info[0]), int(sys.version_info[1]))
    if running < MIN_PYTHON:
        res["errors"].append(
            "Python %d.%d - the toolkit needs %d.%d or newer"
            % (running + MIN_PYTHON))

    # The pointers are the other half of "is it set up", and `inspect` already
    # answers it. Reused rather than re-derived: one definition of stale.
    try:
        st = inspect(dest)
        res["skills"] = {
            "dest": st["dest"],
            "current": [r["name"] for r in st["skills"]
                        if r["status"] == "current"],
            "stale": [r["name"] for r in st["skills"]
                      if r["status"] == "stale"],
            "missing": [r["name"] for r in st["skills"]
                        if r["status"] == "missing"],
            "foreign": [r["name"] for r in st["skills"]
                        if r["status"] not in ("current", "stale", "missing")],
        }
        res["errors"] += list(st.get("errors", []))
    except Exception as exc:
        res["skills"] = {"error": str(exc)[:200]}
        res["errors"].append("could not read the installed skills: %s"
                             % str(exc)[:120])

    res["codex"] = codex_check()

    res["ready"] = (not res["missing_required"] and not res["errors"]
                    and not res["skills"].get("missing")
                    and not res["skills"].get("stale"))
    return res


def print_codex(cx: dict) -> None:
    """The Codex rows. Optional, and said so on the line, as R's is."""
    print()
    state = cx.get("state", "unknown")
    if state == "not_installed":
        print("  -        Codex            optional. %s"
              % ("the codex CLI is here and this toolkit is not installed in "
                 "it - the README's Codex section has the two commands"
                 if cx.get("cli") else "not installed"))
        return
    if state == "unknown":
        print("  unknown  Codex            - %s" % (cx.get("note") or "?"))
        return
    print("  %s  Codex plugin     %s %s in %s"
          % ("ok     " if state == "ok" else "PROBLEM",
             cx.get("plugin", ""), cx.get("version", ""), cx.get("install")))
    if len(cx.get("installs") or []) > 1:
        print("      more than one version is installed: %s"
              % ", ".join(cx["installs"]))
    if cx.get("enabled") is False:
        print("      disabled in Codex's config.toml - its skills are not "
              "offered")
    print("      skills Codex will list: %d" % len(cx.get("skills_listed")
                                                   or []))
    if cx.get("skills_missing"):
        print("      NOT listed (Codex drops these silently): %s"
              % ", ".join(cx["skills_missing"]))


def print_doctor(res: dict) -> None:
    print("paper engine - setup check")
    print("  toolkit  %s" % res["toolkit"])
    print()
    width = max(len(r["name"]) for r in res["rows"])
    for row in res["rows"]:
        mark = {"ok": "ok      ", "missing": "MISSING ",
                "unknown": "unknown "}[row["state"]]
        tail = ""
        if row["state"] != "ok":
            tail = "  - %s%s" % (
                "" if row["required"] else "optional. ", row["blocks"])
            if row.get("pip"):
                tail += "  (pip install %s)" % row["pip"]
        print("  %s%-*s%s" % (mark, width + 2, row["name"], tail))

    sk = res.get("skills") or {}
    print()
    if sk.get("error"):
        print("  unknown  skills           - %s" % sk["error"])
    else:
        print("  %s  skills in %s"
              % ("ok     " if not (sk.get("missing") or sk.get("stale"))
                 else "STALE  ", sk.get("dest", "?")))
        for label, names in (("stale", sk.get("stale") or []),
                             ("missing", sk.get("missing") or []),
                             ("not ours", sk.get("foreign") or [])):
            if names:
                print("      %-9s %s" % (label + ":", ", ".join(names)))
        if sk.get("stale") or sk.get("missing"):
            # Named rather than run. A skill pointer is a file in the
            # member's own directory and this command does not write there.
            print("      fix with:  python tools/install_skills.py install")

    print_codex(res.get("codex") or {})

    for err in res["errors"]:
        print("  ERROR    %s" % err)

    print()
    if res["ready"]:
        print("  Ready. Everything the engine needs is here.")
    elif res["missing_required"]:
        print("  NOT READY - install: %s"
              % ", ".join(res["missing_required"]))
    else:
        print("  Usable, with the gaps above.")
    if res["missing_optional"]:
        print("  Optional and not installed: %s"
              % ", ".join(res["missing_optional"]))


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")

    # --json on either side of the subcommand, per the convention in CLAUDE.md.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="emit JSON instead of text")
    common.add_argument("--dest", default=DEFAULT_DEST,
                        help="the directory the harness reads skills from")

    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("install", parents=[common],
                       help="write the pointers, or bring them up to date")
    i.add_argument("--dry-run", action="store_true",
                   help="report what would be written, write nothing")
    i.add_argument("--force", action="store_true",
                   help="overwrite a skill of the same name this tool did not "
                        "write")

    sub.add_parser("status", parents=[common],
                   help="installed, stale, missing, or not ours")

    sub.add_parser("doctor", parents=[common],
                   help="is this machine set up to run the engine - the "
                        "programs, the packages and the pointers, with what "
                        "each gap blocks. Reports; installs nothing")

    u = sub.add_parser("uninstall", parents=[common],
                       help="remove the pointers this tool wrote")
    u.add_argument("--dry-run", action="store_true",
                   help="report what would be removed, remove nothing")

    args = p.parse_args()

    if args.cmd == "status":
        res = inspect(args.dest)
        print(json.dumps(for_json(res), indent=2, ensure_ascii=False)) if args.json \
            else print_status(res)
        if res["errors"]:
            return 2
        return 0 if all(r["status"] == "current" for r in res["skills"]) else 1

    if args.cmd == "doctor":
        res = doctor(args.dest)
        print(json.dumps(res, indent=2, ensure_ascii=False)) if args.json \
            else print_doctor(res)
        # 2 means something REQUIRED is missing, 1 means it runs with gaps,
        # 0 means ready. A setup check that exits 0 on a machine with no
        # pandoc is the wrong-answer-at-exit-0 shape this toolkit refuses.
        if res["missing_required"] or res["errors"]:
            return 2
        return 0 if res["ready"] else 1

    if args.cmd == "install":
        res = install(args.dest, args.dry_run, args.force)
        print(json.dumps(for_json(res), indent=2, ensure_ascii=False)) if args.json \
            else print_result(res, "installed into" if not args.dry_run
                              else "would install into")
        # There used to be a one-time reporting offer printed here
        # (specs/reporting-offer-2026-09-20.md 3). Upstream defect reporting
        # was removed on 2026-09-21 and there is nothing left to offer:
        # nothing this toolkit records leaves the machine it runs on.
        return 2 if res["errors"] else 0

    if args.cmd == "uninstall":
        res = uninstall(args.dest, args.dry_run)
        print(json.dumps(for_json(res), indent=2, ensure_ascii=False)) if args.json \
            else print_result(res, "removed from" if not args.dry_run
                              else "would remove from")
        return 2 if res["errors"] else 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
