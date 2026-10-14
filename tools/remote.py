#!/usr/bin/env python3
"""remote.py - ask the repository whether a newer copy exists, and cache it.

Spec: specs/plugin-packaging.md 6.2, and item 116, which is the reason this
file exists at all.

**What item 116 was.** `labpack.freshness()` and `release.freshness()` both
answered *"is there a newer one?"* by reading the marketplace clone sitting on
the member's own disk. Two copies, one download, and nothing on the machine
advances either of them - Claude Code's background refresh does, and on a
private repository that refresh is off. So the check compared a stale thing to
a stale thing, found them equal, and reported `current`. Measured on the
maintainer's own machine 2026-09-23: installed 2026.9.9, clone 2026.9.9,
repository 2026.9.20, eleven days of corrected instrument entries unread.

**The fix is one measurement, and it is the reason a token was not needed.**
Claude Code's background refresh *disables git credential helpers*, which is
why it cannot reach a private repository and why a scoped read-only token was
proposed in TODO.md 2.1. A check the toolkit runs itself is under no such
handicap:

    git ls-remote https://github.com/<org>/<repo> HEAD
    -> 9a94d9b5d407...  HEAD

Measured 2026-09-23 with nothing but the login already on the machine. No
token, no prompt, no browser. The member's own credentials, which already
work, and which the background refresh throws away.

**Two halves, and the split is the whole design.**

  - The ASKING half is this file. One `ls-remote` per repository: refs only,
    nothing downloaded, and it writes what it learnt into
    `~/.paper-engine/remote-check.json`. It runs at the OPENING of a skill
    and NEVER while a paper is being drafted.
  - The READING half is `labpack.freshness()` and `release.freshness()`.
    They read that one file and nothing else. They remain exactly as offline
    as 6.2 requires: *what must not happen is a network call on the path that
    generates an idea.*

**Why the commit id and not the version.** 6.2 records the version
comparison's single point of failure - *"the maintainer must bump the version
on every release, or members are pinned to what they installed and the update
check correctly reports that everyone is current forever."* A commit id cannot
be forgotten. It moves when the repository moves, whoever remembered what. The
cost is the mirror of that: a commit that did not bump the version is real
news about the repository and still leaves `/plugin update` with nothing to
do, so the freshness line says which of the two it is rather than sending a
member off to run a command that does nothing.

**Four rules inherited from 6.2, and none is relaxed here.** It reports, it
never updates. It never blocks and never changes an exit code - every failure
lands as `ok: false` and a reason, and there is no path out of this file that
raises. Unknown is its own answer and is said out loud. And it surfaces on a
line the member already reads.

**Three commands, and only the first one runs by itself.**

  python tools/remote.py refresh   # every skill opens with this. Item 117:
                                   # rate-limited to once a day per
                                   # repository, 6s timeout, and it prints
                                   # NOTHING unless there is news
  python tools/remote.py check     # typed on purpose: always asks, always
                                   # reports, 20s timeout
  python tools/remote.py show      # what is on file, and how old it is.
                                   # Never touches a network
  python tools/remote.py check --repo mercer-science/jensen-resource-pack
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import os
import re
import subprocess
import sys

CACHE = "remote-check.json"
TIMEOUT = 20

# `refresh` is the automatic entry point - every skill opens by calling it,
# so its cost is paid on every use of the toolkit and these three numbers are
# what keep that cost near zero. Item 117.
#
#   - A good answer is worth a day. Asking again sooner learns nothing: the
#     member cannot act on news faster than `/plugin update` anyway.
#   - A failed attempt is retried in an hour, not a day. Without this a
#     member whose network is down pays the full timeout on every skill
#     invocation; with it they pay it once an hour. The failure is still
#     remembered, so the freshness line keeps saying UNKNOWN in between.
#   - The timeout is SHORT. `check` is typed on purpose and can afford to
#     wait; `refresh` is uninvited and must never be the reason a skill feels
#     slow to start. Measured 2026-09-23: a reachable ls-remote answers in
#     ~1.2s per repository.
REFRESH_AFTER_HOURS = 24
RETRY_AFTER_HOURS = 1
REFRESH_TIMEOUT = 6
# Beyond this, a cached answer is no longer evidence of anything. 6.2: "last
# checked 34 days ago" is a different fact from "you are current", and
# collapsing them is how a member with a broken refresh reads silence as good
# news.
STALE_AFTER_DAYS = 7

# A URL can carry a credential if the member configured the rewrite TODO.md
# 2.1 describes. Nothing git says about one reaches a log, a cache or a
# terminal with it still in place.
_CREDENTIAL = re.compile(r"//[^/@\s]*@")

# Sibling engines, loaded at most once per process. See `_load`.
_LOADED: dict = {}


def _load(name: str, filename: str):
    """A sibling engine, loaded once and remembered, or None.

    Loaded here rather than at module scope so this file keeps working on a
    copy where the sibling is missing. **Remembered** because `refresh` now
    runs at the opening of every skill: measured 2026-09-23, reading the cache
    alone re-executed `labpack.py` five times in one call for 0.075s of pure
    repetition, and that was before anything ran automatically.
    """
    if name in _LOADED:
        return _LOADED[name]
    mod = None
    try:
        spec = importlib.util.spec_from_file_location(
            name,
            os.path.join(os.path.dirname(os.path.abspath(__file__)), filename))
        if spec is not None and spec.loader is not None:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
    except Exception:
        mod = None
    _LOADED[name] = mod
    return mod


def _labpack():
    """The pack engine, or None."""
    return _load("labpack_for_remote", "labpack.py")


def _release():
    """The toolkit-release engine, or None."""
    return _load("release_for_remote", "release.py")


def home_dir() -> str:
    """`~/.paper-engine`, borrowed from labpack rather than re-derived.

    Item 7 is the standing warning here: a second copy of a reader is two
    readers that must agree, which is how they come to disagree.
    """
    lp = _labpack()
    if lp is not None:
        try:
            return lp.home_dir()
        except Exception:
            pass
    env = os.environ.get("PAPER_ENGINE_HOME")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(os.path.expanduser("~"), ".paper-engine")


def cache_path() -> str:
    return os.path.join(home_dir(), CACHE)


def scrub(text) -> str:
    """Whatever git said, minus anything that looks like a credential."""
    return _CREDENTIAL.sub("//", str(text or ""))


# ---------------------------------------------------------------------------
# What is installed, and where it came from
# ---------------------------------------------------------------------------

def marketplace_of(path: str) -> str:
    """The marketplace name out of an installed plugin path.

    `cache/<marketplace>/<plugin>/<version>` - measured 2026-09-17, see
    specs/probes/labpack-2026-09-17/.
    """
    parts = os.path.abspath(path).replace("\\", "/").split("/")
    for anchor in ("cache", "marketplaces"):
        if anchor in parts:
            i = parts.index(anchor)
            if i + 1 < len(parts):
                return parts[i + 1]
    return ""


def known_marketplaces(host: str = "claude") -> dict:
    """The marketplace records of one CLI, in Claude Code's shape.

    Claude Code's are `known_marketplaces.json`. Codex's are the
    `[marketplaces.<name>]` tables of its `config.toml` - measured on 0.159.3:
    `codex plugin marketplace add mercer-science/paper-engine` records
    `source_type = "git"`, `source = "https://github.com/mercer-science/
    paper-engine.git"`. A GitHub URL is translated into Claude Code's
    `{"source": "github", "repo": "org/name"}`, so `repo_of` has one shape to
    read. Any other Git host has no `org/name` to ask about here, and is left
    as it is.
    """
    lp = _labpack()
    if lp is None:
        return {}
    if host == "codex":
        try:
            tables = lp.codex_config().get("marketplaces")
        except Exception:
            return {}
        out: dict = {}
        for name, table in (tables if isinstance(tables, dict) else {}).items():
            if not isinstance(table, dict):
                continue
            repo = github_repo(str(table.get("source") or ""))
            out[name] = ({"source": {"source": "github", "repo": repo}}
                         if repo and table.get("source_type") == "git"
                         else {"source": dict(table)})
        return out
    try:
        known = lp.read_json(os.path.join(lp.plugins_dir(),
                                          "known_marketplaces.json"))
    except Exception:
        return {}
    return known if isinstance(known, dict) else {}


_GITHUB_URL = re.compile(
    r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")


def github_repo(url: str) -> str:
    """`org/name` out of a GitHub URL, or the empty string."""
    m = _GITHUB_URL.match(url.strip())
    return "%s/%s" % (m.group(1), m.group(2)) if m else ""


def repo_of(marketplace: str, host: str = "claude") -> str:
    """`org/name` for a marketplace, from that CLI's records.

    Only a `github` source yields one. A local or directory source has no
    remote to ask, which is a clean "nothing to check" rather than a failure.
    """
    entry = known_marketplaces(host).get(marketplace)
    if not isinstance(entry, dict):
        return ""
    source = entry.get("source")
    if not isinstance(source, dict):
        return ""
    if source.get("source") != "github":
        return ""
    return str(source.get("repo") or "")


def _host(path: str) -> str:
    lp = _labpack()
    try:
        return (lp.host_of(path) if lp is not None else "") or "claude"
    except Exception:
        return "claude"


def repo_for_path(path: str) -> str:
    """The repository an installed copy came from, or the empty string.

    Read from the records of the CLI whose cache holds the copy: the same
    marketplace name can be added to both, and each CLI's answer is about its
    own install.
    """
    market = marketplace_of(path)
    return repo_of(market, _host(path)) if market else ""


def installed_sha(path: str) -> str:
    """The commit the installed copy was cut from.

    `installed_plugins.json` records `gitCommitSha` beside each install -
    measured 2026-09-23. It is the anchor the whole check turns on, and when
    it is absent the answer is `unknown`, never `current`.

    Codex records no commit. What it does, measured on 0.159.3, is copy the
    marketplace snapshot into the cache whole, `.git` included, and replace
    that copy on every `marketplace upgrade`, so the copy's own HEAD is the
    commit it was cut from. That is a side effect of a recursive copy and not
    a promise, so a copy with no `.git` is `unknown` too.
    """
    if _host(path) == "codex":
        return git_head(path)
    lp = _labpack()
    if lp is None:
        return ""
    try:
        index = lp.read_json(os.path.join(lp.plugins_dir(),
                                          "installed_plugins.json"))
    except Exception:
        return ""
    plugins = index.get("plugins")
    if not isinstance(plugins, dict):
        return ""
    target = os.path.abspath(path).replace("\\", "/").rstrip("/").lower()
    for records in plugins.values():
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, list):
            continue
        for rec in records:
            if not isinstance(rec, dict):
                continue
            where = str(rec.get("installPath") or "")
            if not where:
                continue
            where = os.path.abspath(where).replace("\\", "/").rstrip("/")
            if where.lower() == target:
                return str(rec.get("gitCommitSha") or "")
    return ""


_SHA = re.compile(r"^[0-9a-f]{40}$")


def git_head(path: str) -> str:
    """The commit `<path>/.git/HEAD` names, read as files - no git, no
    subprocess, because `freshness()` is on the offline side of the line.

    A loose ref first, then `packed-refs`. Anything else - no `.git`, a
    `.git` file pointing elsewhere, a ref that resolves to nothing - is the
    empty string, which every caller reads as unknown.
    """
    git = os.path.join(path, ".git")
    try:
        with open(os.path.join(git, "HEAD"), encoding="utf-8") as fh:
            head = fh.read().strip()
    except OSError:
        return ""
    if _SHA.match(head):
        return head
    if not head.startswith("ref: "):
        return ""
    ref = head[5:].strip()
    try:
        with open(os.path.join(git, *ref.split("/")), encoding="utf-8") as fh:
            sha = fh.read().strip()
        if _SHA.match(sha):
            return sha
    except OSError:
        pass
    try:
        with open(os.path.join(git, "packed-refs"), encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref and _SHA.match(parts[0]):
                    return parts[0]
    except OSError:
        pass
    return ""


def no_sha_reason(path: str) -> str:
    """Why `installed_sha` came back empty, in the words of that CLI."""
    if _host(path) == "codex":
        return ("this Codex install carries no .git to say which commit it "
                "was cut from")
    return "the plugin index does not record which commit this copy was cut from"


def installed_repos() -> list:
    """Every github-sourced marketplace on this machine, in either CLI,
    deduplicated."""
    out: list = []
    for host in ("claude", "codex"):
        for market in sorted(known_marketplaces(host)):
            repo = repo_of(market, host)
            if repo and repo not in out:
                out.append(repo)
    return out


# ---------------------------------------------------------------------------
# The asking half - the only place in the toolkit that reaches a remote
# ---------------------------------------------------------------------------

def ask(repo: str, timeout: int = TIMEOUT) -> dict:
    """One `ls-remote` against one repository. Never raises.

    Refs only: nothing is cloned, nothing is fetched, nothing lands on disk.
    That is a few hundred bytes over the wire, which is why this can be a
    round trip rather than the re-clone TODO.md 2.1 route three was rejected
    for.

    `GIT_TERMINAL_PROMPT=0` and `GCM_INTERACTIVE=never` are load-bearing, not
    tidiness: without them an expired credential opens a login window in the
    middle of somebody's session. The credential helper itself is deliberately
    LEFT ON - it is the member's own login, it already works, and using it is
    the entire reason this needs no token.
    """
    now = datetime.datetime.now().replace(microsecond=0).isoformat()
    out = {"repo": repo, "sha": "", "ok": False, "error": "", "asked_at": now}
    if not repo:
        out["error"] = "no repository to ask"
        return out
    url = "https://github.com/%s" % repo
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    try:
        proc = subprocess.run(["git", "ls-remote", url, "HEAD"],
                              capture_output=True, text=True, env=env,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        out["error"] = "no answer within %ds" % timeout
        return out
    except FileNotFoundError:
        out["error"] = "git is not on PATH"
        return out
    except Exception as exc:                            # it never raises
        out["error"] = scrub(exc)
        return out
    if proc.returncode != 0:
        err = scrub(proc.stderr).strip().splitlines()
        out["error"] = err[0] if err else "git exited %d" % proc.returncode
        return out
    first = scrub(proc.stdout).split()
    if not first:
        out["error"] = "the repository named no HEAD"
        return out
    out["sha"] = first[0]
    out["ok"] = True
    return out


def read_cache() -> dict:
    try:
        with open(cache_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_cache(data: dict) -> str:
    path = cache_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
    except OSError:
        # A cache that cannot be written is a check that says UNKNOWN for
        # ever. That is a poor outcome and an honest one: it never learns,
        # and it never claims to have.
        return ""
    return path


def cached(repo: str) -> dict:
    """What is on file about one repository, with its age.

    Reads a file and nothing else - this is the half `freshness()` is allowed
    to call, and the reason the drafting path stays offline.
    """
    out = {"repo": repo, "sha": "", "ok": False, "error": "", "asked_at": "",
           "age_days": None, "age_hours": None, "stale": True,
           "present": False}
    entry = (read_cache().get("repos") or {}).get(repo)
    if not isinstance(entry, dict):
        return out
    out["present"] = True
    out["sha"] = str(entry.get("sha") or "")
    out["ok"] = bool(entry.get("ok"))
    out["error"] = str(entry.get("error") or "")
    out["asked_at"] = str(entry.get("asked_at") or "")
    if out["asked_at"]:
        try:
            then = datetime.datetime.fromisoformat(out["asked_at"])
            gap = datetime.datetime.now() - then
            out["age_days"] = gap.days
            out["age_hours"] = gap.total_seconds() / 3600.0
            out["stale"] = out["age_days"] > STALE_AFTER_DAYS
        except ValueError:
            out["age_days"] = None
            out["age_hours"] = None
    return out


def due(repo: str) -> bool:
    """Is this repository worth asking again right now?

    The rate limit, and the whole reason `refresh` can sit at the opening of
    every skill. A clock that has gone backwards - a restored machine, a
    corrected timezone - reads as a negative age, which is not evidence that
    the answer is fresh, so it is asked.
    """
    seen = cached(repo)
    if not seen["present"] or seen["age_hours"] is None:
        return True
    if seen["age_hours"] < 0:
        return True
    window = REFRESH_AFTER_HOURS if seen["ok"] else RETRY_AFTER_HOURS
    return seen["age_hours"] >= window


def check(repos: list | None = None, timeout: int = TIMEOUT) -> dict:
    """Ask each repository and record the answer.

    The asking half's entry point, and the only thing in the toolkit that
    reaches a network.
    """
    repos = repos if repos is not None else installed_repos()
    data = read_cache()
    store = data.get("repos")
    if not isinstance(store, dict):
        store = {}
    results = []
    for repo in repos:
        res = ask(repo, timeout=timeout)
        store[repo] = res
        results.append(res)
    data["repos"] = store
    data["checked_at"] = datetime.datetime.now().replace(
        microsecond=0).isoformat()
    written = write_cache(data)
    return {"results": results, "cache": written, "asked": len(results),
            "reached": sum(1 for r in results if r["ok"])}


def refresh(repos: list | None = None, timeout: int = REFRESH_TIMEOUT,
            force: bool = False) -> dict:
    """The automatic entry point. Ask only what is due, and say only what is
    news. Item 117.

    **This is the one thing every skill runs, and it is not on the drafting
    path.** It runs once, at a skill's opening step, before any work has
    begun - the rule 6.2 states is *no network call on the path that
    generates an idea*, and an opening step is not that path. Everything
    after it, for the rest of the session, reads the cache this left behind.

    Three properties earn it that position. It asks at most once a day per
    repository (`REFRESH_AFTER_HOURS`), so the overwhelming majority of
    invocations touch no network at all. It gives up in `REFRESH_TIMEOUT`
    seconds rather than 20. And it cannot fail: every error is already an
    `ok: false` record, and there is no path out of here that raises or that
    returns non-zero.
    """
    want = repos if repos is not None else installed_repos()
    ask_now = list(want) if force else [r for r in want if due(r)]
    res = (check(ask_now, timeout=timeout) if ask_now
           else {"results": [], "cache": "", "asked": 0, "reached": 0})
    res["considered"] = len(want)
    res["skipped"] = len(want) - len(ask_now)
    res["news"] = news()
    return res


def news() -> list:
    """The freshness lines worth interrupting somebody with.

    Silence is reserved for the states a member cannot act on and does not
    need: `current`, a git clone that updates by `git pull`, and no pack
    installed. `unknown` is NOT silent - 6.2's third rule is that unknown is
    its own answer and is said out loud, because somebody whose credential
    expired must not read silence as good news.

    Reads what the two `freshness()` halves return and nothing else; it makes
    no comparison of its own, so there is one place where staleness is
    decided and this is not it.
    """
    out = []
    rel = _release()
    if rel is not None:
        try:
            fresh = rel.freshness()
            if fresh.get("state") in ("update_available", "unknown"):
                out.append({"what": "toolkit", "state": fresh["state"],
                            "line": fresh.get("line", "")})
        except Exception:
            pass
    lp = _labpack()
    if lp is not None:
        try:
            fresh = lp.freshness()
            if fresh.get("state") in ("update_available", "unknown"):
                out.append({"what": "pack", "state": fresh["state"],
                            "line": fresh.get("line", "")})
        except Exception:
            pass
    return [one for one in out if one["line"]]


def print_refresh(res: dict) -> None:
    """Nothing at all when there is nothing to say.

    A skill opens with this, so its quiet case has to be genuinely quiet -
    an "all current" line printed eight ways is how a member learns to skip
    the thing that will one day matter.
    """
    for one in res["news"]:
        print(one["line"])


def print_check(res: dict) -> None:
    if not res["results"]:
        print("no github-sourced marketplace is installed, so there is "
              "nothing to ask. Nothing here is blocked.")
        return
    for one in res["results"]:
        if one["ok"]:
            print("  %-46s %s" % (one["repo"], one["sha"][:7]))
        else:
            print("  %-46s could not ask: %s" % (one["repo"], one["error"]))
    print("asked %d, reached %d" % (res["asked"], res["reached"]))
    if not res["cache"]:
        print("  note: the answer could not be cached, so the freshness line "
              "will keep saying UNKNOWN. Nothing here is blocked.")


def print_show(entries: list) -> None:
    if not entries:
        print("nothing has been asked yet. `remote.py check` asks. Until "
              "then every freshness line says UNKNOWN, which is the honest "
              "answer rather than a failure.")
        return
    for entry in entries:
        age = ("never" if entry["age_days"] is None
               else "today" if entry["age_days"] == 0
               else "%d days ago" % entry["age_days"])
        state = entry["sha"][:7] if entry["ok"] else "could not ask"
        print("  %-46s %-14s checked %s%s"
              % (entry["repo"], state, age,
                 "  (STALE)" if entry["stale"] else ""))


def main(argv: list | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="remote.py",
        description="Ask the repository whether a newer copy exists, and "
                    "cache the answer for the offline freshness line.")
    p.add_argument("cmd", choices=["refresh", "check", "show"])
    p.add_argument("--repo", default=None, action="append", dest="repos",
                   help="org/name. Repeatable. Default: every github-sourced "
                        "marketplace installed on this machine")
    p.add_argument("--timeout", type=int, default=None,
                   help="seconds to wait per repository. Default %d for "
                        "`check`, %d for `refresh`, which is uninvited and "
                        "must not be why a skill feels slow to start"
                        % (TIMEOUT, REFRESH_TIMEOUT))
    p.add_argument("--force", action="store_true",
                   help="`refresh` only: ask even when the cached answer is "
                        "younger than the rate limit")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    repos = [r for r in (args.repos or []) if r]

    if args.cmd == "show":
        want = repos or installed_repos()
        entries = [cached(r) for r in want]
        if args.json:
            print(json.dumps(entries, indent=2))
        else:
            print_show(entries)
        return 0

    if args.cmd == "refresh":
        res = refresh(repos or None,
                      timeout=args.timeout if args.timeout else REFRESH_TIMEOUT,
                      force=args.force)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print_refresh(res)
        return 0

    res = check(repos or None,
                timeout=args.timeout if args.timeout else TIMEOUT)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print_check(res)
    # It reports; it never refuses. A repository that could not be reached is
    # still exit 0 - 6.2: it never blocks and never changes an exit code.
    return 0


if __name__ == "__main__":
    sys.exit(main())
