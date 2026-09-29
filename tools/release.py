#!/usr/bin/env python3
"""release.py - cut a dated version, and notice when one is overdue.

Spec: specs/lab-resource-pack.md 6.4, which asked *"can we make it bump
itself? Or add a check to it?"* and found that working out why one could and
one could not was the more useful answer - **because the obstacle was the
version scheme, not the plugin.**

A version can only be computed if it answers a question a machine can answer.
A date answers *when*, and the correct value at release time is today. Semver
answers *what kind of change*, and the correct value is one of three:

    a bug fixed in prose.py                 0.1.1
    the analysis skill added                0.2.0
    a file every project reads, renamed     1.0.0

A script sees "40 files changed, 2688 insertions" and cannot tell those apart.
One that always picks patch will, on the day a change genuinely breaks
people's projects, publish a number saying *safe, trivial* - and **a wrong
version is worse than a stale one, because people act on it.**

What semver buys is a risk signal for people who pin versions, resolve
dependencies, or deliberately stay on an old release. Nobody in this
distribution model does any of those: the toolkit is private, everyone should
always be current, and `/plugin update` is the only path. What a date buys is
the question the lab will actually ask - *how old is this?*

**The format is `2026.9.9`, not `2026.09.09`, and that is not cosmetic.**
`plugin.json` is schema-validated and semver forbids leading zeros in a
numeric identifier, so `2026.09.09` risks being rejected - or worse, accepted
somewhere and rejected on a member's machine. `2026.9.9` is valid semver and a
readable date at the same time, and it orders correctly, because semver
compares numeric identifiers numerically: `2026.9.9` < `2026.10.1`.

**Two things this deliberately does not do.** It does not run from a git hook
- `.git/hooks/` is not version controlled and does not survive a fresh clone,
so it would disappear silently, which is the failure class being fixed rather
than a fix for it. And it does not fire at commit time: **it reports; it never
updates.** `bump` is a command the maintainer runs, not something that happens
behind them.

  python tools/release.py check
  python tools/release.py bump
  python tools/release.py bump --path "<another plugin repo>"
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except ValueError:
            pass

MANIFEST = os.path.join(".claude-plugin", "plugin.json")

# What counts as SHIPPED CONTENT - a change to any of these is a change a
# member receives, so it is a change that should have moved the version.
# `.claude-plugin/` is in the list because the manifest itself ships: the real
# 2026-09-08 case was a commit that edited `plugin.json` to add a seventh
# skill and left the version two lines above it alone.
CONTENT = ("skills", "tools", "resources", ".claude-plugin", "writing_guides",
           "instruments.md", "bootcamp.md", "sops.md", "lab.yml",
           "locations.md", "learning.md", "publication_policy.md")

# MEASURED 2026-09-09, and the obvious spelling is wrong.
#
# `git log -S` is the natural reach and it is the WRONG TOOL here. `-S` finds
# commits where the NUMBER OF OCCURRENCES of a string changes, and a bump
# changes the value on the line while leaving one occurrence of `"version"`
# before and after. On a scratch clone with a real 0.1.0 -> 0.2.0 bump:
#
#     -S'"version"'        the commit that CREATED the line, and never the bump
#     -G'^ *"version":'    the bump AND the commit that created it
#
# So a check built on `-S` reports STALE forever, including immediately after
# a correct bump - and **a check that cries wolf is deleted rather than
# fixed**, the same argument that put a lookbehind in tests/portability.py's
# path check. `tests/portability.py` pins this by reproducing the measurement.
VERSION_PICKAXE = ("-G", r'^ *"version":')


def today_version() -> str:
    """Today, as a version that is also a date and has no leading zeros."""
    now = datetime.date.today()
    return f"{now.year}.{now.month}.{now.day}"


def manifest_path(root: str) -> str:
    return os.path.join(root, MANIFEST)


def read_manifest(root: str) -> dict:
    try:
        with io.open(manifest_path(root), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _git(root: str, *argv: str) -> tuple:
    try:
        proc = subprocess.run(["git", *argv], cwd=root, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=30)
    except (OSError, subprocess.SubprocessError):
        return 1, "", "git is not available"
    return proc.returncode, proc.stdout, proc.stderr


def _last_commit(root: str, paths: list, pickaxe: tuple = ()) -> dict:
    argv = ["log", "-1", "--format=%h%x09%ad%x09%s", "--date=short"]
    argv.extend(pickaxe)
    argv.append("--")
    argv.extend(paths)
    rc, out, _ = _git(root, *argv)
    line = out.strip().splitlines()
    if rc != 0 or not line:
        return {}
    parts = line[0].split("\t")
    if len(parts) < 3:
        return {}
    return {"sha": parts[0], "date": parts[1], "subject": parts[2]}


def version_staleness(root: str) -> dict:
    """Is the last commit that changed `version` older than the last commit
    that changed shipped content?

    Three properties it has to have, and each one was decided rather than
    fallen into:

      - **It reports, and does not refuse.** A commit is not a release, and a
        maintainer mid-way through a change has every right to an unbumped
        manifest. The verdict is a line, not an exit code.
      - **Not a git hook.** `.git/hooks/` is not version controlled and does
        not survive a fresh clone or a new machine, so it would disappear
        silently - the failure class being fixed rather than a fix for it.
      - **Absent git is not a failure.** A plugin install is not a checkout.
        No history, no verdict, no error - the same shape as an empty
        `resources/`.
    """
    root = os.path.abspath(root)
    out: dict = {"root": root, "state": "unknown", "why": "", "remedy": "",
                 "version": str(read_manifest(root).get("version", "")),
                 "version_commit": {}, "content_commit": {},
                 "commits_since": 0, "since": [], "fatal": False}

    if not os.path.isfile(manifest_path(root)):
        out["why"] = f"no {MANIFEST} here, so there is no version to check"
        return out
    rc, _, _ = _git(root, "rev-parse", "--git-dir")
    if rc != 0:
        out["why"] = ("this copy is not a git checkout, so there is no "
                      "history to read. A plugin install is not a checkout, "
                      "and that is not an error")
        return out

    out["version_commit"] = _last_commit(root, [MANIFEST], VERSION_PICKAXE)
    present = [c for c in CONTENT if os.path.exists(os.path.join(root, c))]
    if os.path.isfile(os.path.join(root, "lab.yml")):
        # Item 162: a lab pack ships EVERY root-level .md, because that is
        # what labpack.pack_documents reads. A fixed list of names cannot see
        # a file added after it was written. The engine repository has no
        # lab.yml, and its root .md are documentation rather than shipped.
        present += sorted(n for n in os.listdir(root)
                          if n.endswith(".md") and n not in present
                          and os.path.isfile(os.path.join(root, n)))
    out["content_commit"] = _last_commit(root, present)
    if not out["content_commit"]:
        out["state"] = "current"
        out["why"] = "no shipped content has been committed here yet"
        return out
    if not out["version_commit"]:
        out["state"] = "stale"
        out["why"] = ("the version line has never changed in this history, "
                      "so every commit of shipped content came after it")
        out["remedy"] = _remedy(root)
        return out

    # Which came first, by the commit graph rather than by date: two commits
    # made on the same day are ordered, and a date comparison calls that a
    # tie. `--is-ancestor` answers the question that is actually being asked.
    rc, _, _ = _git(root, "merge-base", "--is-ancestor",
                    out["content_commit"]["sha"], out["version_commit"]["sha"])
    if rc == 0:
        out["state"] = "current"
        out["why"] = (f"the version was last changed at "
                      f"{out['version_commit']['sha']} "
                      f"({out['version_commit']['date']}), at or after the "
                      f"last shipped-content commit")
        return out

    rc, listing, _ = _git(
        root, "log", "--format=%h %s",
        f"{out['version_commit']['sha']}..HEAD", "--", *present)
    rows = [x for x in listing.strip().splitlines() if x.strip()]
    out["state"] = "stale"
    out["since"] = rows
    out["commits_since"] = len(rows)
    out["why"] = (
        f"{len(rows)} commit(s) of shipped content since the last version "
        f"bump ({out['version_commit']['sha']}, "
        f"{out['version_commit']['date']}); content last moved at "
        f"{out['content_commit']['sha']} ({out['content_commit']['date']})")
    out["remedy"] = _remedy(root)
    return out


def _remedy(root: str) -> str:
    """Which command bumps THIS repository.

    A pack and the engine bump with different commands, and naming the wrong
    one is worse than naming none: a maintainer who runs it and sees nothing
    happen concludes the check is broken.
    """
    manifest = read_manifest(root)
    if manifest.get("labPack"):
        return "python tools/labpack.py bump"
    return "python tools/release.py bump"


# ---------------------------------------------------------------------------
# Is THIS copy behind the one on disk - specs/plugin-packaging.md 6.2, step 6
# ---------------------------------------------------------------------------
#
# `version_staleness` above is the MAINTAINER's question: is a bump overdue in
# this repository. This is the MEMBER's: is the copy I am running behind the
# marketplace clone that is already sitting beside it. They are different
# questions with different audiences and they share nothing but the word
# "stale".
#
# **No network call, no token, no new credential, and nothing on the path that
# drafts a paper.** 6.2 forbids all four, and it turns out none of them is
# needed: a member who installed the plugin has the toolkit on disk TWICE -
# the install under `cache/`, and the git clone under `marketplaces/` that
# Claude Code refreshes in the background - so the freshness question is
# already answered locally. That layout was measured on 2026-09-17
# (specs/probes/labpack-2026-09-17/) and `labpack.marketplace_clone()` is the
# reader for it. This does not write a second one: it imports that one and
# points it at the engine instead of at the pack.
#
# The four rules 6.2 fixes, and `labpack.freshness()` already follows:
#
#   - it REPORTS and never updates. `/plugin update` is the member's action,
#     and an engine that updated itself mid-paper would change the tool
#     between one round and the next;
#   - it never blocks and never changes an exit code;
#   - **unknown is its own answer and is said out loud** - "no clone to
#     compare against" is a different fact from "you are current", and
#     collapsing them is how a member with a broken background refresh reads
#     silence as good news;
#   - it surfaces on a line the user already reads, which is `plan`'s
#     approval block, and only when there is something to say.


def _labpack():
    """The pack engine, or None. Imported here rather than at module scope:
    `release.py bump` must keep working on a copy where it is missing."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "labpack_for_release",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "labpack.py"))
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def toolkit_root() -> str:
    """The copy of the toolkit this file belongs to."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _remote():
    """The remote-check engine, or None. Loaded here so `bump` and `check`
    keep working on a copy without it."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "remote_for_release",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "remote.py"))
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def freshness(root: str = "") -> dict:
    """Is this installed copy behind the repository it came from?

    Offline, always - it reads a cached answer that `remote.py check` put on
    disk, never a remote. Returns one of four states - `current`,
    `update_available`, `unknown`, `not_installed` - and a `line` that is
    safe to print verbatim.

    **It used to read the marketplace clone, and that was item 116.** The
    clone and the install are written by the same download, and the thing
    that advances the clone is Claude Code's background refresh - which is
    off on a private marketplace, and whose failure is precisely what this
    check exists to notice. Two stale copies agreeing is what `current` meant
    here until 2026-09-23.
    """
    root = os.path.abspath(root or toolkit_root())
    out = {"root": root, "state": "unknown", "line": "",
           "installed": str(read_manifest(root).get("version", "")),
           "available": "", "clone": "", "repo": "", "installed_sha": "",
           "remote_sha": "", "checked_days_ago": None}

    lp = _labpack()
    if lp is None:
        out["line"] = ("whether a newer version of this toolkit exists is "
                       "UNKNOWN - the reader for the plugin directory could "
                       "not be loaded. Nothing here is blocked.")
        return out

    if not lp.inside_plugin_install(root):
        # A clone is the other supported route and it updates with `git
        # pull`. Saying which route this is, is the useful half: a member
        # who git-cloned and waits for `/plugin update` waits forever.
        out["state"] = "not_installed"
        out["line"] = ("this copy is a clone rather than a plugin install, "
                       "so `/plugin update` does not reach it - `git pull` "
                       "does. Nothing here is blocked.")
        return out

    out["clone"] = lp.marketplace_clone(root)
    version = out["installed"] or "unknown"
    unknown = ("version %s installed; whether a newer one exists is UNKNOWN "
               "(%%s). Nothing here is blocked." % version)

    rem = _remote()
    if rem is None:
        out["line"] = unknown % "the remote-check engine could not be loaded"
        return out

    out["repo"] = rem.repo_for_path(root)
    if not out["repo"]:
        out["line"] = unknown % ("this copy did not come from a GitHub "
                                 "marketplace, so there is nothing to "
                                 "compare it against")
        return out

    out["installed_sha"] = rem.installed_sha(root)
    if not out["installed_sha"]:
        out["line"] = unknown % ("the plugin index does not record which "
                                 "commit this copy was cut from")
        return out

    seen = rem.cached(out["repo"])
    out["remote_sha"] = seen["sha"]
    out["checked_days_ago"] = seen["age_days"]
    when = ("today" if seen["age_days"] == 0
            else "yesterday" if seen["age_days"] == 1
            else "%s days ago" % seen["age_days"])

    if not seen["present"]:
        out["line"] = unknown % ("the repository has never been asked - "
                                 "`python tools/remote.py check` asks it")
        return out
    if not seen["ok"]:
        out["line"] = unknown % ("the last attempt to ask, %s, did not get "
                                 "an answer: %s" % (when, seen["error"]))
        return out
    if seen["stale"]:
        out["line"] = unknown % ("the repository was last asked %s, which is "
                                 "too long ago to stand on" % when)
        return out

    if seen["sha"][:12] != out["installed_sha"][:12]:
        out["state"] = "update_available"
        out["line"] = (
            "the repository has moved since this toolkit was installed (%s "
            "installed, %s on GitHub as of %s); `/plugin update` when "
            "convenient. If that reports nothing to do, the change did not "
            "carry a version bump and this copy is fine. Nothing here is "
            "blocked." % (out["installed_sha"][:7], seen["sha"][:7], when))
        return out
    out["state"] = "current"
    out["line"] = ("toolkit %s (current as of the check %s)."
                   % (out["installed"], when))
    return out


def print_freshness(res: dict) -> None:
    print(res["line"])
    if res.get("clone"):
        print("  compared against %s" % res["clone"])
    print("\nNo network call was made, and nothing here changes an exit "
          "code. `/plugin update` is yours to run.")


def bump(root: str, dry_run: bool = False, version: str = "") -> dict:
    """Write today's date into the manifest. One command, whose failure is
    visible - which is what the maintainer's habit becomes instead of
    *remember a number*."""
    root = os.path.abspath(root)
    path = manifest_path(root)
    out: dict = {"root": root, "path": path, "was": "", "version": "",
                 "written": False, "notes": []}
    try:
        with io.open(path, encoding="utf-8") as fh:
            text = fh.read()
        data = json.loads(text)
    except (OSError, ValueError) as exc:
        out["notes"].append(f"cannot read {path}: {exc}")
        return out

    out["was"] = str(data.get("version", ""))
    out["version"] = version or today_version()
    if out["was"] == out["version"]:
        out["notes"].append(
            f"already {out['version']} - a second release on the same day "
            f"carries the same date, which is the one case this scheme "
            f"cannot express. Say so in the commit message instead")
    # Edited as TEXT rather than re-serialized: a json.dump would reformat a
    # manifest a human maintains, and a release that reindents the file it is
    # releasing makes every diff unreadable.
    new_text, n = re.subn(r'("version"\s*:\s*")[^"]*(")',
                          lambda m: m.group(1) + out["version"] + m.group(2),
                          text, count=1)
    if not n:
        out["notes"].append("no version line in this manifest to change")
        return out
    if dry_run:
        return out
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(new_text)
    out["written"] = True
    return out


def print_check(res: dict) -> None:
    print(f"{res['root']}")
    print(f"  version: {res['version'] or '(none)'}")
    if res["state"] == "unknown":
        print(f"  UNKNOWN - {res['why']}")
        return
    if res["state"] == "current":
        print(f"  current - {res['why']}")
        return
    print(f"  STALE - {res['why']}")
    for row in res["since"][:10]:
        print(f"    {row}")
    print(f"  remedy: {res['remedy']}")
    print("")
    print("  This is a reminder, not a refusal. A commit is not a release.")


def main(argv: list | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="release.py",
        description="Cut a dated version, and notice when one is overdue.")
    p.add_argument("cmd", choices=["check", "bump", "freshness"])
    p.add_argument("--path", default="",
                   help="the plugin repository. Default: this toolkit")
    p.add_argument("--version", default="",
                   help="an explicit version instead of today")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    root = args.path or os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))

    if args.cmd == "freshness":
        # The MEMBER's question, not the maintainer's: is the copy I am
        # running behind the one already on this disk. Offline, always, and
        # exit 0 in every state - it reports, and a stale toolkit still
        # drafts (plugin-packaging 6.2).
        res = freshness(root)
        print(json.dumps(res, indent=2)) if args.json             else print_freshness(res)
        return 0

    if args.cmd == "check":
        res = version_staleness(root)
        print(json.dumps(res, indent=2)) if args.json else print_check(res)
        # Reports, never refuses - so a stale verdict is still exit 0.
        return 0

    res = bump(root, dry_run=args.dry_run, version=args.version)
    if args.json:
        print(json.dumps(res, indent=2))
        return 0
    verb = "would write" if args.dry_run else "wrote"
    if res["version"]:
        print(f"{verb} version {res['version']} (was {res['was'] or 'none'}) "
              f"into {res['path']}")
    for note in res["notes"]:
        print(f"  note: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
