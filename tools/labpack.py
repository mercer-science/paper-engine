#!/usr/bin/env python3
"""labpack.py - find the lab resource pack, find the drop-zones, and rescue
what is stranded in one.

Spec: specs/lab-resource-pack.md. Two ladders live here and they are
deliberately not the same ladder:

  A PACK IS FOUND.        Ordered, stop at the first hit, say so and continue
                          when none of them resolves (4.1). Two packs stop and
                          ask, because a member in two labs is a real case and
                          guessing which lab's SOPs to quote is worse than a
                          question.

  A DROP-ZONE IS CHOSEN.  Ordered, and ALL hits are read (4.1) - a member with
                          material in two places should have both inventoried.
                          The location is the whole point, because of 4.5.

4.5, stated once here because three commands depend on it: a file dropped into
an INSTALLED PLUGIN's `resources/` is deleted by `/plugin update`, which
replaces that directory wholesale. Nothing warns and nothing errors. That is
item 94, and the fix is not a warning - it is a default that puts the file
somewhere else (`~/.paper-engine/resources/`, rung 3), plus detection by name
and a one-command rescue. A member who never reads the README is already safe,
which is the only kind of safe that survives contact with a lab.

`~/.paper-engine/` is deliberately NOT `${CLAUDE_PLUGIN_DATA}`: that
destination survives an update and is auto-deleted on uninstall, which
specs/plugin-packaging.md 5 records as an unfixed hazard for `rules.yml`.
Picking it here would be repeating a choice already known to be wrong.

**No lab name appears in this file, ever.** The engine never learns a lab's
name (decision 9); a pack carries its own, and `tests/portability.py` enforces
it.

  python tools/labpack.py show
  python tools/labpack.py config --path "<a pack folder>"
  python tools/labpack.py config --resources                 # the durable one
  python tools/labpack.py config --resources --adopt         # rescue
  python tools/labpack.py inventory                          # the live read
  python tools/labpack.py bump
"""

from __future__ import annotations

import argparse
import csv
import datetime
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except ValueError:
            pass

# The marker in a pack's plugin.json that makes it a pack rather than any
# other installed plugin. Measured on the real manifest 2026-09-17.
PACK_MARKER = "labPack"

# The two reserved filenames, and the only two. Everything else in a drop-zone
# is free-form - 4.5's defect was that a member who dropped `Methods.docx` got
# no error and no effect.
RESERVED = ("instruments.md", "bootcamp.md")

# `README.md` at the root of the SHIPPED resources/ is the engine's own file,
# not the member's. It is never adopted and never inventoried as lab material;
# adopting it would move the engine's documentation into a member's durable
# folder and then let the next update write it back, which is a loop.
SHIPPED_FILES = {"README.md"}

# Only these are opened for a first heading. A .pdf or a .xlsx has no readable
# first line and guessing at one is worse than using the filename.
TITLED_EXTS = (".md", ".markdown", ".txt", ".rst")


# ---------------------------------------------------------------------------
# Roots. Every one of them is overridable, so a test never reads - and
# `adopt` never MOVES - the author's own files.
# ---------------------------------------------------------------------------

def home_dir() -> str:
    """`~/.paper-engine`, the per-user location no plugin lifecycle touches.

    specs/plugin-packaging.md 5 names "a stable per-user location that no
    plugin lifecycle touches" as the real fix for item 66 and item 30's class.
    This is the first instance of it. `$PAPER_ENGINE_HOME` exists so the suite
    can move it; nothing else should set it.
    """
    env = os.environ.get("PAPER_ENGINE_HOME")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(os.path.expanduser("~"), ".paper-engine")


def config_path() -> str:
    return os.path.join(home_dir(), "config.json")


def toolkit_root() -> str:
    """This copy of the toolkit.

    `${CLAUDE_PLUGIN_ROOT}` is what Claude Code sets for an installed plugin;
    the parent of `tools/` is what a clone has. Both are read because both are
    real, and which one it is decides whether the shipped drop-zone is at risk.
    """
    env = os.environ.get("PAPER_ENGINE_TOOLKIT")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    plugin = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin and os.path.isdir(plugin):
        return os.path.abspath(plugin)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def toolkit_resources() -> str:
    return os.path.join(toolkit_root(), "resources")


def durable_resources(create: bool = False) -> str:
    """`~/.paper-engine/resources/`, created on first reference (4.1 rung 3).

    Created rather than merely named, because a folder a member is told about
    and then cannot find is a folder they put nothing in.
    """
    path = os.path.join(home_dir(), "resources")
    if create:
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass
    return path


def plugins_dir() -> str:
    """`~/.claude/plugins`, whose layout was measured 2026-09-17.

    See specs/probes/labpack-2026-09-17/. `cache/<marketplace>/<plugin>/
    <version>/` holds the installs, `marketplaces/<marketplace>/` holds the
    git clone each one came from, and `installed_plugins.json` indexes both.
    """
    env = os.environ.get("PAPER_ENGINE_PLUGINS_DIR")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(os.path.expanduser("~"), ".claude", "plugins")


def inside_plugin_install(path: str) -> bool:
    """Is this path inside an installed plugin - i.e. will `/plugin update`
    replace it?

    Path containment rather than `$CLAUDE_PLUGIN_ROOT`, and that is the whole
    point: a member running `python tools/idea.py` from a terminal inside a
    plugin install has no such variable set, and they are exactly the person
    about to lose a file.
    """
    try:
        cache = os.path.abspath(os.path.join(plugins_dir(), "cache"))
        target = os.path.abspath(path)
        return os.path.commonpath([cache, target]) == cache
    except (ValueError, OSError):
        # commonpath raises across drives on Windows, which is a clean "no".
        return False


# ---------------------------------------------------------------------------
# config.json - one file, per machine, outside every plugin directory
# ---------------------------------------------------------------------------

def read_config() -> dict:
    try:
        with io.open(config_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        # A malformed config is the same as none. It is one member's
        # convenience file and refusing to run over it would be worse than
        # ignoring it.
        return {}


def set_config(key: str, value: str) -> dict:
    data = read_config()
    if value:
        data[key] = value
    else:
        data.pop(key, None)
    os.makedirs(home_dir(), exist_ok=True)
    with io.open(config_path(), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    return data


# ---------------------------------------------------------------------------
# Reading a folder: names, subfolders, first headings. No document body.
# ---------------------------------------------------------------------------

def first_heading(path: str) -> str:
    """The first ATX heading of a text file, or "" - never the filename.

    Empty rather than a fallback on purpose: the caller already has the
    filename, and a "title" that is just the filename with the underscores
    taken out reads as though somebody wrote it. `idea.py` delegates here so
    there is one implementation rather than two that drift.
    """
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for _ in range(40):
                line = fh.readline()
                if not line:
                    break
                m = re.match(r"^#{1,3}\s+(.+?)\s*$", line)
                if m:
                    return m.group(1).strip()
    except OSError:
        return ""
    return ""


def inventory_dir(root: str, max_files: int = 40,
                  skip: set | None = None) -> dict:
    """What is in a folder, cheaply.

    Names, subfolders and the first heading of each text file. **No document
    body enters this output**, so it stays cheap enough to call before the
    first literature query; a document is read when it is about to inform a
    question. Same contract as the OneDrive inventory it sits beside.
    """
    out: dict = {"files": [], "subfolders": [], "titles": {},
                 "file_count": 0, "truncated": False}
    if not root or not os.path.isdir(root):
        return out
    skip = skip or set()
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir != ".":
            out["subfolders"].append(rel_dir.replace(os.sep, "/"))
        for f in filenames:
            if f.startswith("."):
                continue
            rel = os.path.join(rel_dir, f).replace("./", "").replace(os.sep, "/")
            if rel.startswith("./"):
                rel = rel[2:]
            if rel in skip:
                continue
            hits.append(rel)
    out["subfolders"] = sorted(set(out["subfolders"]))
    out["file_count"] = len(hits)
    out["files"] = sorted(hits)[:max_files]
    out["truncated"] = len(hits) > max_files
    for rel in out["files"]:
        if rel.lower().endswith(TITLED_EXTS):
            title = first_heading(os.path.join(root, *rel.split("/")))
            if title:
                out["titles"][rel] = title
    return out


# ---------------------------------------------------------------------------
# The drop-zone ladder (4.1). ALL hits are read.
# ---------------------------------------------------------------------------

def resource_roots(max_files: int = 40) -> list[dict]:
    """Every drop-zone this machine has, in rung order, all of them read.

    A pack is found; a drop-zone is chosen. The difference is 4.5: because the
    location decides whether a file survives the next update, a member with
    material in two places should see both rather than have the first one
    silently win.
    """
    ladder = [
        (1, "env", os.environ.get("PAPER_ENGINE_RESOURCES") or ""),
        (2, "config", read_config().get("resources") or ""),
        (3, "durable", durable_resources()),
        (4, "toolkit", toolkit_resources()),
    ]
    out: list[dict] = []
    seen: set = set()
    for rung, source, path in ladder:
        if not path:
            continue
        path = os.path.abspath(os.path.expanduser(path))
        if path in seen:
            # Two rungs pointing at one folder is a member who configured the
            # default. One entry, at the rung they set.
            continue
        seen.add(path)
        skip = SHIPPED_FILES if source == "toolkit" else set()
        inv = inventory_dir(path, max_files=max_files, skip=skip)
        # AT RISK is a property of WHERE the folder is, not of which rung
        # named it. A member who points `config --resources` at a path inside
        # a plugin install has the same file and the same update waiting for
        # it as the one who never configured anything - scoping this to the
        # shipped folder would have protected the default and left the
        # deliberate choice unguarded, which is backwards.
        at_risk = inside_plugin_install(path)
        entry = {"rung": rung, "source": source, "path": path,
                 "exists": os.path.isdir(path),
                 "at_risk": bool(at_risk and inv["files"]),
                 "at_risk_files": list(inv["files"]) if at_risk else []}
        entry.update(inv)
        out.append(entry)
    return out


def at_risk_roots(roots: list[dict] | None = None) -> list[dict]:
    return [r for r in (roots if roots is not None else resource_roots())
            if r["at_risk"]]


def adopt(dry_run: bool = False) -> dict:
    """Move what is stranded in a plugin install to the durable location.

    MOVE, not copy - two copies of an SOP that must agree is how they come to
    disagree, which is the same argument that keeps SOP bodies out of a pack
    (3.2).

    A name that already exists in the durable folder is a CONFLICT and is
    reported rather than resolved. Overwriting the durable copy would destroy
    the one file this command exists to protect, and renaming the arrival
    would leave a member with two SOPs and no way to tell which is current.
    """
    res: dict = {"moved": [], "conflicts": [], "from": [], "dry_run": dry_run,
                 "to": durable_resources(), "notes": []}
    stranded = at_risk_roots()
    if not stranded:
        res["notes"].append(
            "nothing is stranded - no drop-zone in use sits inside a plugin "
            "install, so nothing here can be deleted by /plugin update")
        return res
    dest = durable_resources(create=not dry_run)
    for root in stranded:
        res["from"].append(root["path"])
        for rel in root["files"]:
            src = os.path.join(root["path"], *rel.split("/"))
            dst = os.path.join(dest, *rel.split("/"))
            if os.path.exists(dst):
                res["conflicts"].append(rel)
                continue
            res["moved"].append(rel)
            if dry_run:
                continue
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)
            except OSError as exc:
                res["moved"].remove(rel)
                res["notes"].append(f"could not move {rel}: {exc}")
    res["moved"].sort()
    res["conflicts"].sort()
    return res


def print_adopt(res: dict) -> None:
    if not res["from"]:
        for n in res["notes"]:
            print(f"  {n}")
        return
    verb = "would move" if res["dry_run"] else "moved"
    for path in res["from"]:
        print(f"from {path}")
        print("  this folder is inside a plugin install, so the next "
              "`/plugin update` replaces it")
    print(f"to   {res['to']}")
    for rel in res["moved"]:
        print(f"  {verb} {rel}")
    for rel in res["conflicts"]:
        print(f"  CONFLICT {rel} - a file of that name is already in the "
              f"durable folder. Neither was touched; compare them and move it "
              f"by hand")
    for n in res["notes"]:
        print(f"  note: {n}")
    if res["moved"] and not res["dry_run"]:
        print("\nMoved, not copied. Two copies of an SOP that must agree is "
              "how\nthey come to disagree.")


# ---------------------------------------------------------------------------
# 5 - the live inventory. The one thing that is not snapshotted.
# ---------------------------------------------------------------------------
#
# Six rules, and every one of them is a rule rather than a preference:
#
#   1. OFFERED, never automatic.
#   2. It answers a CONVERSATION, never a file. An on-hand count is not a
#      methods fact and never reaches methods_facts.yml, idea.md, or anything
#      else on disk. There is deliberately no writer in this module.
#   3. UNKNOWN IS NOT OUT OF STOCK. No connector, no answer, an unreadable
#      file, or a row that cannot be matched all produce *unknown*, and
#      unknown never downgrades a candidate.
#   4. Every quoted number carries its READ TIME. A bare number is
#      indistinguishable from a pack snapshot, which is the confusion this
#      whole design exists to prevent.
#   5. NO PERSISTENT CACHE. At most one read per run, held in memory. A
#      cached count that outlives the conversation is a snapshot nobody
#      labelled, which is worse than a second read.
#   6. READ-ONLY. No skill writes to the Box. Ever. The inventory is
#      somebody's working file and a well-meant tidy-up is unrecoverable to
#      them.
#
# THE ROUTE (5.2). The lab is on Box and does NOT sync it locally, so rung 2
# is the expected path and rung 1 is nearly always absent:
#
#   1. `fallback_path` from lab.yml, if it names one that is on this machine.
#      This module does that one.
#   2. The Box connector, if it is authorized in this session. **A connector
#      call is not testable and therefore never load-bearing** (0), and a
#      Python engine cannot make one at all - so this module NAMES that route
#      and the skill performs it. The split is the honest one: everything
#      with a right answer is here, everything that needs a session is not.
#   3. Neither - name which one is missing and continue with everything
#      unknown. That is not a degraded mode to apologize for. It is the
#      conversation as it works today, in which the user knows things the
#      engine does not.

# 5.1 rule 5: in memory, for this process, and cleared by a named call. There
# is no file behind this and there must never be one.
#
# KEYED BY THE PACK IT CAME FROM, which is not fussiness. A run can change
# which pack resolves - `config --path` settles a two-pack ambiguity mid-
# session - and an unkeyed cache would then answer a question about lab B with
# lab A's shelf, at the same confident timestamp. That is rule 4's failure
# arriving by the door rule 5 left open.
_INVENTORY_CACHE: dict = {}

# Column names that mean "how many are there". Deliberately short: a wrong
# guess here invents a quantity, and `has_on_hand_column` in lab.yml is the
# authority whenever the pack states it.
ON_HAND_COLUMNS = ("on_hand", "onhand", "qty", "quantity", "in_stock",
                   "stock", "count", "amount")
NAME_COLUMNS = ("name", "item", "description", "reagent", "product")
UPDATED_COLUMNS = ("updated", "updated_date", "last_updated", "date")


def reset_inventory_cache() -> None:
    """End of run. 5.1 rule 5 - nothing about a read outlives the session."""
    _INVENTORY_CACHE.clear()


def inventory_offer() -> str:
    """5.1 rule 1: it is OFFERED. This is the sentence, stated once.

    Kept in the engine rather than in the skill so every caller offers the
    same thing, including the half that promises nothing is written down -
    which is the half that makes the offer safe to accept.
    """
    return ("Want me to check the current stock in the lab inventory? "
            "Nothing it says gets written anywhere - it answers this "
            "conversation and then it is gone.")


def _read_delimited(path: str) -> dict:
    """A csv or tsv, as a header and a list of row dicts."""
    out: dict = {"header": [], "records": [], "why": ""}
    try:
        with io.open(path, encoding="utf-8-sig", errors="replace",
                     newline="") as fh:
            text = fh.read()
    except OSError as exc:
        out["why"] = f"the file could not be opened ({exc.strerror or exc})"
        return out
    if "\x00" in text:
        out["why"] = ("the file is not readable as text - it has binary "
                      "content in it")
        return out
    delim = "\t" if (text.count("\t") > text.count(",")) else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        out["why"] = "the file is empty"
        return out
    out["header"] = [c.strip() for c in rows[0]]
    for row in rows[1:]:
        rec = {}
        for i, col in enumerate(out["header"]):
            rec[col] = row[i].strip() if i < len(row) else ""
        out["records"].append(rec)
    return out


def _read_xlsx(path: str) -> dict:
    """The first worksheet of an .xlsx, with the standard library only.

    An .xlsx is a zip of XML, so this needs no third-party package - which
    matters, because the one file the pack actually names is a spreadsheet and
    a dependency here would put the inventory read behind an install step. It
    reads the shared-string table and the first sheet, and nothing else: no
    formulas, no formatting, no second sheet. A file it cannot make sense of
    is UNKNOWN, never empty.
    """
    out: dict = {"header": [], "records": [], "why": ""}
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            shared: list = []
            if "xl/sharedStrings.xml" in names:
                root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in root:
                    shared.append("".join(t.text or "" for t in si.iter()
                                          if t.tag.endswith("}t")))
            sheets = sorted(n for n in names
                            if n.startswith("xl/worksheets/sheet")
                            and n.endswith(".xml"))
            if not sheets:
                out["why"] = "the workbook has no worksheet in it"
                return out
            root = ET.fromstring(zf.read(sheets[0]))
    except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
        out["why"] = f"the workbook could not be read ({exc})"
        return out

    grid: list = []
    for row in root.iter():
        if not row.tag.endswith("}row"):
            continue
        cells: list = []
        for cell in row:
            if not cell.tag.endswith("}c"):
                continue
            val = ""
            for child in cell:
                if child.tag.endswith("}v"):
                    val = child.text or ""
                elif child.tag.endswith("}is"):
                    val = "".join(t.text or "" for t in child.iter()
                                  if t.tag.endswith("}t"))
            if cell.get("t") == "s" and val.isdigit():
                idx = int(val)
                val = shared[idx] if idx < len(shared) else ""
            cells.append(val.strip())
        if any(cells):
            grid.append(cells)
    if not grid:
        out["why"] = "the first worksheet has nothing in it"
        return out
    out["header"] = grid[0]
    for row in grid[1:]:
        rec = {}
        for i, col in enumerate(out["header"]):
            if col:
                rec[col] = row[i] if i < len(row) else ""
        out["records"].append(rec)
    return out


def _pick(header: list, wanted: tuple) -> str:
    lowered = {c.lower().strip(): c for c in header if c}
    for want in wanted:
        if want in lowered:
            return lowered[want]
    for low, original in lowered.items():
        if any(want in low for want in wanted):
            return original
    return ""


def inventory(pack: dict | None = None, force: bool = False,
             connector_path: str = "") -> dict:
    """The live read, or a named unknown. Never a refusal, never a zero.

    `connector_path` is rung 2 of 5.2, arriving from the OUTSIDE: a Python
    engine cannot make a Box call, so a skill that has already made one and
    saved what came back names the local copy here, and this function reads
    it exactly as it would a synced `fallback_path` - same two readers, same
    six rules, same shape of answer. That is TODO.md §8's one real gap closed
    the way its own spec argued for: "cheaper than it looks - it puts the
    parse back in the engine already tested for it, and it makes rule 4 work
    for free." What this function does NOT do is call a connector itself, or
    know that the path it was given came from one; a caller that lies about
    that is a caller bug, not a case this reads for.
    """
    pack = pack if pack is not None else find_pack()
    # Keyed on the connector path too (rule 5): a fixture pack has no
    # `fallback_path` on most machines, so two different connector reads in
    # the SAME run must not collide on the pack's own key and answer the
    # second query with the first file's shelf.
    key = (pack.get("path") or "(no pack)") + "|" + connector_path
    if _INVENTORY_CACHE.get(key) is not None and not force:
        cached = dict(_INVENTORY_CACHE[key])
        cached["cached"] = True
        return cached

    now = datetime.datetime.now().isoformat(timespec="seconds")
    out: dict = {"state": "unknown", "route": "", "why": "", "rows": 0,
                 "read_at": now, "records": [], "header": [],
                 "answers": "", "caveat": "", "owner": "", "name": "",
                 "attribution": "", "on_hand_column": "", "name_column": "",
                 "updated_column": "", "cached": False}

    if not pack.get("path"):
        out["why"] = ("no lab resource pack resolved, and a pack is what "
                      "names the lab's inventory. Nothing is out of stock - "
                      "the stock is simply not something this can see.")
        _INVENTORY_CACHE[key] = out
        return out

    lab = read_lab_yml(pack["path"])
    inv = (lab.get("live") or {}).get("inventory")
    if not isinstance(inv, dict):
        out["why"] = ("this pack's lab.yml names no live inventory, so there "
                      "is nothing to read. That is not the same as empty.")
        _INVENTORY_CACHE[key] = out
        return out

    out["owner"] = str(inv.get("owner") or "")
    out["name"] = str(inv.get("name") or "")
    fallback = str(inv.get("fallback_path") or "")
    if fallback:
        fallback = os.path.abspath(os.path.expanduser(fallback))
    connector = os.path.abspath(os.path.expanduser(connector_path)) \
        if connector_path else ""

    # Rung 1 wins when both are present - a member who syncs a copy gets the
    # cheap, ordinary-file read, and it is what the fixture suite can drive
    # without a network. Rung 2 is a copy a skill just pulled through the
    # connector THIS RUN; it is read the same way and never written back.
    if fallback and os.path.isfile(fallback):
        source, route = fallback, "fallback_path"
    elif connector and os.path.isfile(connector):
        source, route = connector, "connector"
    else:
        # Neither rung answers. This module cannot make the connector call
        # itself - that is the skill's to try - so it names the gap rather
        # than guessing at what the call would have returned.
        out["route"] = "connector"
        where = str(inv.get("where") or "the live source")
        out["why"] = (
            f"no local copy of {out['name'] or 'the inventory'} is on this "
            f"machine" + (f" (lab.yml names {fallback}, which is not there)"
                          if fallback else " (lab.yml names no fallback_path)")
            + f", so the only remaining route is the {where} connector, and "
              f"that is the skill's to try rather than this engine's. If it "
              f"is not authorized, everything stays UNKNOWN - which is not "
              f"the same as out of stock.")
        _INVENTORY_CACHE[key] = out
        return out

    reader = _read_xlsx if source.lower().endswith((".xlsx", ".xlsm")) \
        else _read_delimited
    data = reader(source)
    out["route"] = route
    out["header"] = data["header"]
    if data["why"]:
        out["why"] = (f"the copy at {source} could not be read: "
                      f"{data['why']}. Everything stays UNKNOWN, which is not "
                      f"the same as out of stock.")
        _INVENTORY_CACHE[key] = out
        return out
    if not data["records"]:
        out["why"] = (f"the copy at {source} has a header and no rows in it. "
                      f"UNKNOWN rather than 'you have none' - a file nobody "
                      f"has filled in says nothing about the shelf.")
        _INVENTORY_CACHE[key] = out
        return out

    out["state"] = "read"
    out["records"] = data["records"]
    out["rows"] = len(data["records"])
    out["name_column"] = _pick(data["header"], NAME_COLUMNS) or \
        (data["header"][0] if data["header"] else "")
    out["updated_column"] = _pick(data["header"], UPDATED_COLUMNS)

    # The pack's own statement wins over a column-name guess. Measured on the
    # real pack: `has_on_hand_column: false` - the lab's inventory answers
    # "do we own one, and where is it", never "is there any left". A guess
    # that found a column called `count` would invent a quantity.
    declared = inv.get("has_on_hand_column")
    if declared is False:
        out["on_hand_column"] = ""
    else:
        out["on_hand_column"] = _pick(data["header"], ON_HAND_COLUMNS)
        if declared is True and not out["on_hand_column"]:
            out["on_hand_column"] = ""

    if out["on_hand_column"]:
        out["answers"] = "on-hand"
        out["caveat"] = (
            "This file carries a quantity column, so it can say how many - as "
            "of when it was last edited, which is not the same as now.")
    else:
        out["answers"] = "owned-and-where"
        out["caveat"] = (
            "This file has no quantity column. It can say whether the lab "
            "OWNS something and WHERE it is kept, and it cannot say how many "
            "are left. An item listed here may still be an empty box.")

    # The wording marks WHICH rung answered - a synced copy implies an
    # ongoing local mirror somebody maintains, and a connector copy is a
    # single-run pull the skill is responsible for not keeping (rule 5, rule
    # 6). Reading the two the same way must not make them sound the same.
    source_phrase = ("from a local copy" if route == "fallback_path" else
                     "from a copy the Box connector returned this run")
    out["attribution"] = (
        f"{out['name'] or 'the lab inventory'}, as of the read just now "
        f"({out['read_at'][:10]}), {source_phrase}"
        + (f"; kept up to date by the {out['owner']}" if out["owner"] else ""))
    _INVENTORY_CACHE[key] = out
    return out


def inventory_verdict(res: dict) -> str:
    """5.1 rule 3, as a function, so nothing has to remember it.

    Unknown NEVER downgrades a candidate. It becomes a named load-bearing
    unknown instead, which is what Stage 5 already does with "idk".
    """
    return "not-infeasible"


def inventory_lookup(query: str, res: dict | None = None) -> dict:
    """Does the lab have this, and what may be said about the answer.

    Returns a PHRASE as well as the rows, because rule 4 is easy to obey in
    the engine and easy to forget in a sentence: a count without its read time
    is indistinguishable from a pack snapshot.
    """
    res = res if res is not None else inventory()
    out: dict = {"query": query, "state": res["state"], "matches": [],
                 "phrase": "", "attribution": res.get("attribution", "")}
    if res["state"] != "read":
        out["phrase"] = (
            f"Whether the lab has {query} is UNKNOWN - {res['why']}")
        return out

    name_col = res["name_column"]
    needle = query.lower().strip()
    for rec in res["records"]:
        haystack = " ".join(str(v) for v in rec.values()).lower()
        name = str(rec.get(name_col, "")).strip()
        if needle and (needle in name.lower() or needle in haystack):
            out["matches"].append({
                "name": name,
                "on_hand": (rec.get(res["on_hand_column"])
                            if res["on_hand_column"] else None),
                "updated": (rec.get(res["updated_column"])
                            if res["updated_column"] else None),
                "fields": rec})

    if not out["matches"]:
        # A row absent from an ownership list is NOT a zero. The lab may own
        # it and never have listed it, and telling somebody they do not have
        # something is a refusal dressed as a fact.
        out["state"] = "unknown"
        out["phrase"] = (
            f"{query} is not listed in {res.get('name') or 'the inventory'} "
            f"as of the read just now. That is UNKNOWN rather than an answer: "
            f"an item missing from the list may simply never have been added "
            f"to it."
            + (f" The {res['owner']} maintains the file if it should be there."
               if res.get("owner") else ""))
        return out

    first = out["matches"][0]
    when = f"as of the inventory read just now ({res['read_at'][:10]})"
    if res["answers"] == "on-hand" and first["on_hand"] is not None:
        row_date = (f", row last updated {first['updated']}"
                    if first.get("updated") else "")
        out["phrase"] = (f"{first['name']}: {first['on_hand']} {when}"
                         f"{row_date}.")
    else:
        where = ""
        for key, val in first["fields"].items():
            if "location" in key.lower() and val:
                where = f", kept at {val}"
                break
        out["phrase"] = (
            f"The lab owns {first['name']}{where}, {when}. The inventory "
            f"carries no quantity column, so how many are left is unknown.")
    return out


def _release():
    """release.py, loaded by path - ONE implementation of the bump and the
    staleness check, for both plugins.

    A pack and the engine have the same release obligation and the same
    measured trap in the check (`-G`, never `-S`). Two copies of that would
    be the CAPTION_HEADING_RE problem again, and the copy nobody re-reads is
    the one that drifts.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "release.py")
    spec = importlib.util.spec_from_file_location("release_for_labpack", path)
    if spec is None or spec.loader is None:   # pragma: no cover - install bug
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def bump_pack(root: str, dry_run: bool = False, curated: bool = False) -> dict:
    """Cut a dated release of a pack, and optionally re-date its curation.

    **They are two different facts and the command keeps them apart.** The
    version says when this RELEASE was cut; `curated_on` says when the content
    was last checked against the source. A typo fix moves one and not the
    other, and 6.2's staleness line quotes `curated_on`, because "your pack
    was released last week" is no comfort if its facility knowledge is from
    March. So `--curated` is a separate flag and is never implied: a
    maintainer who has not re-read the Box must not be able to claim they
    have by running a release command.
    """
    rl = _release()
    out = rl.bump(root, dry_run=dry_run)
    out["curated_on"] = ""
    lab_path = os.path.join(root, "lab.yml")
    if curated and os.path.isfile(lab_path):
        today = datetime.date.today().isoformat()
        try:
            with io.open(lab_path, encoding="utf-8") as fh:
                text = fh.read()
            new_text, n = re.subn(r'(^curated_on:\s*")[^"]*(")',
                                  lambda m: m.group(1) + today + m.group(2),
                                  text, count=1, flags=re.M)
            if n and not dry_run:
                with io.open(lab_path, "w", encoding="utf-8",
                             newline="\n") as fh:
                    fh.write(new_text)
            out["curated_on"] = today if n else ""
            if not n:
                out["notes"].append(
                    "lab.yml carries no curated_on: line to re-date")
        except OSError as exc:
            out["notes"].append(f"could not re-date lab.yml: {exc}")
    elif curated:
        out["notes"].append(
            "no lab.yml here, so there is no curation date to move")
    else:
        out["notes"].append(
            "curated_on is UNCHANGED - it says when the content was last "
            "checked against the source, which a release does not do. Pass "
            "--curated only if you actually re-read it")
    return out


def print_pack(pack: dict, fresh: dict | None = None) -> None:
    if pack["source"] == "ambiguous":
        print("TWO OR MORE PACKS - nothing resolved, on purpose:")
        for path in pack["ambiguous"]:
            print(f"  {path}")
        print("")
        for line in _wrap(pack["note"]):
            print(f"  {line}")
        return
    if not pack["path"]:
        print("No lab resource pack.")
        for line in _wrap(pack["note"]):
            print(f"  {line}")
        return
    print(f"Pack: {pack['display_name']}  ({pack['version']})")
    print(f"  found at rung {pack['rung']} ({pack['source']}): {pack['path']}")
    if fresh and fresh.get("line"):
        print(f"  {fresh['line']}")
    for note in pack.get("notes") or []:
        print(f"  note: {note}")


def _wrap(text: str, width: int = 72) -> list:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def print_roots(roots: list[dict]) -> None:
    for r in roots:
        where = r["path"]
        state = "" if r["exists"] else "  (not created yet)"
        print(f"  {r['rung']}. {r['source']:8} {where}{state}")
        if r["files"]:
            print(f"       {r['file_count']} file(s) in "
                  f"{len(r['subfolders'])} subfolder(s)")
            for rel in r["files"][:12]:
                title = r["titles"].get(rel)
                print(f"         {rel}" + (f"  -  {title}" if title else ""))
        if r["at_risk"]:
            print("       AT RISK: this folder is inside a plugin install, "
                  "so the next")
            print("       `/plugin update` DELETES these files:")
            for rel in r["at_risk_files"]:
                print(f"         {rel}")
            print("       Run `labpack.py config --resources --adopt` to "
                  "move them to")
            print(f"       {durable_resources()}")


# ---------------------------------------------------------------------------
# The pack ladder (4.1). Ordered, STOP at the first hit.
# ---------------------------------------------------------------------------

def read_json(path: str) -> dict:
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def is_pack(path: str) -> bool:
    """A directory is a pack if its manifest carries the marker.

    The marker and nothing else. A plugin that merely sits beside a pack is
    not one, and a folder full of SOPs with no manifest is a drop-zone.
    """
    if not path or not os.path.isdir(path):
        return False
    manifest = read_json(os.path.join(path, ".claude-plugin", "plugin.json"))
    return bool(manifest.get(PACK_MARKER))


def _scalar(val: str):
    """A yaml scalar, to the depth this file actually uses.

    An inline list, a bare true/false, or a string with its quotes taken off.
    Nothing else - `lab.yml` is five scalars and two nested blocks, and a
    third-party YAML import here would put a dependency on the path that
    decides whether a member has a pack at all.
    """
    val = val.strip()
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        if not inner:
            return []
        return [x.strip().strip(chr(34)).strip(chr(39))
                for x in inner.split(",")]
    if val.lower() in ("true", "yes"):
        return True
    if val.lower() in ("false", "no"):
        return False
    return val.strip(chr(34)).strip(chr(39))


def parse_flat_yaml(text: str) -> dict:
    """Nested `key: value` by indentation, with no dependency.

    Written against the real file rather than against the shape 3.1 sketched:
    the measured `lab.yml` is THREE levels deep (`live.inventory.sections.
    equipment`), not two, and it carries inline lists. A two-level reader
    would have silently dropped the column lists and reported an inventory
    with no columns, which reads exactly like an inventory that has none.
    """
    root: dict = {}
    stack: list = [(-1, root)]
    for raw in text.splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        line = raw.split(" #", 1)[0].rstrip()
        stripped = line.strip()
        if not stripped or ":" not in stripped:
            continue
        indent = len(line) - len(line.lstrip())
        key, _, val = stripped.partition(":")
        key = key.strip()
        if not key or key.startswith("-"):
            continue
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if val.strip():
            parent[key] = _scalar(val)
        else:
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
    return root


def read_lab_yml(path: str) -> dict:
    """`lab.yml` - the only machine-read file in a pack (3.1).

    Everything else in a pack is prose read the way a new student would read
    it. This file is the exception because two things do have to be exact:
    which pack this is, and where the live things are.

    Anything it cannot parse is absent rather than an error - a pack whose
    identity file is malformed is still a pack whose prose reads.
    """
    out: dict = {"path": os.path.join(path, "lab.yml"), "present": False,
                 "pack": "", "display_name": "", "curated_from": "",
                 "curated_on": "", "curated_by": "", "live": {}, "raw": {}}
    try:
        with io.open(out["path"], encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return out
    out["present"] = True
    data = parse_flat_yaml(text)
    out["raw"] = data
    for key in ("pack", "display_name", "curated_from", "curated_on",
                "curated_by"):
        val = data.get(key)
        if isinstance(val, str):
            out[key] = val
    live = data.get("live")
    if isinstance(live, dict):
        out["live"] = live
    return out


def pack_info(path: str) -> dict:
    """Everything about one pack, from its two files.

    The VERSION comes from `plugin.json` and never from `lab.yml` (3.1): a
    second copy in a second file is two files that must agree, which is how
    they come to disagree. `curated_on` is a different fact and earns its
    place - the version says when the release was cut, `curated_on` says when
    the content was last checked against the source.
    """
    manifest = read_json(os.path.join(path, ".claude-plugin", "plugin.json"))
    lab = read_lab_yml(path)
    out = {"path": path, "present": bool(manifest),
           "name": manifest.get("name", ""),
           "version": str(manifest.get("version", "")),
           "display_name": lab.get("display_name")
           or manifest.get("displayName", "") or manifest.get("name", ""),
           "curated_on": lab.get("curated_on", ""),
           "curated_by": lab.get("curated_by", ""),
           "curated_from": lab.get("curated_from", ""),
           "live": lab.get("live") or {},
           "files": [], "notes": []}
    if not lab["present"]:
        out["notes"].append(
            "this pack has no lab.yml, so its lab name and its live sources "
            "are unknown. The content files still read")
    for name in sorted(os.listdir(path)) if os.path.isdir(path) else []:
        if name.endswith(".md") and not name.startswith("."):
            out["files"].append(name)
    return out


def installed_packs() -> list[str]:
    """Every installed plugin carrying the pack marker.

    Reads `installed_plugins.json`, whose shape was measured 2026-09-17 (see
    specs/probes/labpack-2026-09-17/). Three things a guess gets wrong and
    this does not: each value is a LIST of install records, `installPath` is
    absolute and is the path to use rather than one rebuilt from the cache
    layout, and `version` can be the literal string "unknown".

    A directory scan is the fallback when the index is missing or malformed,
    because an index that cannot be parsed is not a reason to tell a member
    they have no pack.
    """
    found: list[str] = []
    index = read_json(os.path.join(plugins_dir(), "installed_plugins.json"))
    plugins = index.get("plugins")
    if isinstance(plugins, dict):
        for records in plugins.values():
            if isinstance(records, dict):
                records = [records]
            if not isinstance(records, list):
                continue
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                path = rec.get("installPath") or ""
                if path and is_pack(path) and path not in found:
                    found.append(path)
    if found:
        return sorted(found)
    cache = os.path.join(plugins_dir(), "cache")
    if not os.path.isdir(cache):
        return []
    for market in sorted(os.listdir(cache)):
        mdir = os.path.join(cache, market)
        if not os.path.isdir(mdir):
            continue
        for plugin in sorted(os.listdir(mdir)):
            pdir = os.path.join(mdir, plugin)
            if not os.path.isdir(pdir):
                continue
            for version in sorted(os.listdir(pdir)):
                vdir = os.path.join(pdir, version)
                if is_pack(vdir) and vdir not in found:
                    found.append(vdir)
    return sorted(found)


def find_pack() -> dict:
    """The pack ladder: env, config, sibling plugin, none.

    Ordered, stop at the first hit, and **say so and continue when none of
    them resolves** - no pack is not an error, not a warning, and not a
    defect. The engine behaves exactly as it does without one.

    Two packs STOP and ask. A member in two labs is a real case, and guessing
    which lab's SOPs to quote is worse than a question.
    """
    out: dict = {"path": "", "source": "none", "rung": 0, "note": "",
                 "candidates": [], "ambiguous": [], "display_name": "",
                 "version": "", "curated_on": ""}

    env = os.environ.get("PAPER_ENGINE_LAB_PACK") or ""
    if env:
        env = os.path.abspath(os.path.expanduser(env))
        out["candidates"].append({"rung": 1, "source": "env", "path": env,
                                  "is_pack": is_pack(env)})
    cfg = read_config().get("pack") or ""
    if cfg:
        cfg = os.path.abspath(os.path.expanduser(cfg))
        out["candidates"].append({"rung": 2, "source": "config", "path": cfg,
                                  "is_pack": is_pack(cfg)})
    siblings = installed_packs()
    for path in siblings:
        out["candidates"].append({"rung": 3, "source": "plugin", "path": path,
                                  "is_pack": True})

    resolved = ""
    for cand in out["candidates"]:
        if not cand["is_pack"]:
            continue
        if cand["rung"] == 3 and len(siblings) > 1:
            # Rung 3 alone can be ambiguous. Rungs 1 and 2 name ONE path, so
            # a member who has answered the question has answered it.
            out["source"] = "ambiguous"
            out["ambiguous"] = list(siblings)
            out["note"] = (
                f"{len(siblings)} installed packs carry the lab-pack marker, "
                "and which lab's SOPs to quote is not something to guess at. "
                "Say which one with: labpack.py config --path \"<the pack "
                "folder>\". The candidates are: " + ", ".join(siblings))
            return out
        resolved = cand["path"]
        out["source"] = cand["source"]
        out["rung"] = cand["rung"]
        break

    if not resolved:
        named = [c for c in out["candidates"] if not c["is_pack"]]
        out["note"] = (
            "no pack resolved, and that is not an error - the engine behaves "
            "exactly as it does without one, and every question a pack would "
            "have made specific is asked generically instead.")
        if named:
            out["note"] += (
                " One path was named and is not a pack (no labPack marker in "
                "its .claude-plugin/plugin.json): "
                + ", ".join(c["path"] for c in named))
        return out

    out["path"] = resolved
    info = pack_info(resolved)
    out["display_name"] = info["display_name"]
    out["version"] = info["version"]
    out["curated_on"] = info["curated_on"]
    out["notes"] = info["notes"]
    return out


# ---------------------------------------------------------------------------
# 6.2 / 6.4 - freshness, entirely offline
# ---------------------------------------------------------------------------

def version_key(version: str) -> tuple:
    """Numeric identifiers compare numerically (6.4), so 2026.9.9 < 2026.10.1.

    A text compare gets that backwards, which is the whole reason the format
    is `2026.9.9` rather than `2026.09.09`: leading zeros are forbidden in a
    semver numeric identifier, so the readable date and the valid version are
    the same string. A non-numeric identifier - `unknown` was measured on a
    real install - sorts below every number rather than raising.
    """
    parts: list[tuple] = []
    for chunk in str(version).split("."):
        if chunk.isdigit():
            parts.append((1, int(chunk), ""))
        else:
            parts.append((0, 0, chunk))
    return tuple(parts)


def _remote():
    """The remote-check engine, or None. Loaded here rather than at module
    scope so every other command keeps working on a copy without it.

    It is loaded for `cached()` only - the half that reads a file. The half
    that reaches a repository is `remote.py check`, which nothing on the
    drafting path calls. 6.2: *what must not happen is a network call on the
    path that generates an idea.*
    """
    try:
        spec = importlib.util.spec_from_file_location(
            "remote_for_labpack",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "remote.py"))
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def marketplace_clone(pack_path: str) -> str:
    """The git clone the installed pack came from, if it is on disk.

    Measured 2026-09-17: `~/.claude/plugins/marketplaces/<marketplace>/` is a
    full checkout sitting beside the install, and `known_marketplaces.json`
    records its `installLocation`. So comparing versions needs no network, no
    token and no credential - the freshness question is already answered on
    disk. An installed pack's path is `cache/<marketplace>/<plugin>/<version>`,
    which is where the marketplace name comes from when the index does not
    give it.
    """
    known = read_json(os.path.join(plugins_dir(), "known_marketplaces.json"))
    parts = os.path.abspath(pack_path).replace("\\", "/").split("/")
    market = ""
    if "cache" in parts:
        i = parts.index("cache")
        if i + 1 < len(parts):
            market = parts[i + 1]
    if market:
        entry = known.get(market)
        if isinstance(entry, dict) and entry.get("installLocation"):
            loc = entry["installLocation"]
            if os.path.isdir(loc):
                return loc
        guess = os.path.join(plugins_dir(), "marketplaces", market)
        if os.path.isdir(guess):
            return guess
    return ""


def freshness(pack: dict | None = None) -> dict:
    """One line for Stage 1: is this pack current, and how old is its content?

    The four inherited rules of 6.2, each with teeth here:

      - **It reports; it never updates.** Changing the facility knowledge
        underneath a member mid-conversation is worse than being one version
        behind it.
      - **It never blocks and never changes an exit code.**
      - **Unknown is its own answer and is said out loud.** A member whose
        background refresh broke reads silence as good news.
      - `curated_on` ages visibly even when nobody bumped a version, which is
        why it is reported in every state rather than only the unknown one.

    Never a network call. 6.2: *what must not happen is a network call on the
    path that generates an idea.* What it compares against is a cached answer
    that `remote.py check` put on disk, and a cache with nothing in it is
    UNKNOWN rather than current.

    **Item 116 is why this reads a cache and not the marketplace clone.** The
    clone was the obvious comparison and it cannot work: it and the install
    are written by the same download, and the thing that advances the clone is
    the background refresh whose failure is the thing being detected. Two
    stale copies agreeing is what `current` used to mean here.
    """
    pack = pack if pack is not None else find_pack()
    out = {"state": "none", "line": "", "installed": "", "available": "",
           "curated_on": "", "age_days": None, "pack": pack.get("path", ""),
           "repo": "", "installed_sha": "", "remote_sha": "",
           "checked_days_ago": None}
    if not pack.get("path"):
        return out

    name = pack.get("display_name") or "Lab"
    curated = pack.get("curated_on") or ""
    out["curated_on"] = curated
    age = ""
    if curated:
        try:
            then = datetime.date.fromisoformat(curated)
            days = (datetime.date.today() - then).days
            out["age_days"] = days
            age = f"curated {curated}"
        except ValueError:
            age = f"curated {curated}"
    else:
        age = "curation date unknown"

    out["installed"] = pack.get("version", "")
    unknown = (f"{name} pack, {age} - whether a newer pack exists is "
               f"UNKNOWN (%s). Nothing here is blocked.")

    rem = _remote()
    if rem is None:
        out["state"] = "unknown"
        out["line"] = unknown % "the remote-check engine could not be loaded"
        return out

    out["repo"] = rem.repo_for_path(pack["path"])
    if not out["repo"]:
        # A clone, a directory source, or a pack somebody put there by hand.
        # None of them has a repository to have fallen behind.
        out["state"] = "unknown"
        out["line"] = unknown % ("this pack did not come from a GitHub "
                                 "marketplace, so there is nothing to "
                                 "compare it against")
        return out

    out["installed_sha"] = rem.installed_sha(pack["path"])
    if not out["installed_sha"]:
        out["state"] = "unknown"
        out["line"] = unknown % ("the plugin index does not record which "
                                 "commit this copy was cut from")
        return out

    seen = rem.cached(out["repo"])
    out["remote_sha"] = seen["sha"]
    out["checked_days_ago"] = seen["age_days"]
    when = ("today" if seen["age_days"] == 0
            else "yesterday" if seen["age_days"] == 1
            else f"{seen['age_days']} days ago")

    if not seen["present"]:
        out["state"] = "unknown"
        out["line"] = unknown % ("the repository has never been asked - "
                                 "`python tools/remote.py check` asks it")
        return out
    if not seen["ok"]:
        out["state"] = "unknown"
        out["line"] = unknown % (f"the last attempt to ask, {when}, did not "
                                 f"get an answer: {seen['error']}")
        return out
    if seen["stale"]:
        # "Last checked 34 days ago" is a different fact from "you are
        # current", and collapsing them is how a member with a broken
        # refresh reads silence as good news.
        out["state"] = "unknown"
        out["line"] = unknown % (f"the repository was last asked {when}, "
                                 f"which is too long ago to stand on")
        return out

    if seen["sha"][:12] != out["installed_sha"][:12]:
        out["state"] = "update_available"
        out["line"] = (
            f"{name} pack, {age} - the repository has moved since this copy "
            f"was installed ({out['installed_sha'][:7]} installed, "
            f"{seen['sha'][:7]} on GitHub as of {when}); `/plugin update` "
            f"when convenient. If that reports nothing to do, the change did "
            f"not carry a version bump and this copy is fine. Nothing here "
            f"is blocked.")
        return out
    out["state"] = "current"
    out["line"] = (f"{name} pack {out['installed']}, {age} (current as of "
                   f"the check {when}).")
    return out


# ---------------------------------------------------------------------------
# The brief - specs/lab-pack-brief-2026-09-24.md
#
# One project's copy of the pack: every step of the lab's route, quoted
# verbatim, with this project's decisions and this project's open questions
# written against each one. It exists because the pack cannot carry a
# decision, cannot carry an open question, and is not in the project folder
# where the question gets asked three weeks later.
#
# Two rules run through all of it:
#
#   IT QUOTES, IT NEVER RESTATES. A paraphrased number can drift from the
#   pack without either copy being wrong on its face, which is the failure
#   the pack itself avoids by leaving SOP bodies in the source.
#
#   IT IS NOT A METHODS FILE, and the destination enforces that as well as
#   the wording does. `data/` is refused by name.
# ---------------------------------------------------------------------------

# A body quoted from the pack is fenced with this while the frame around it
# is tidied, then the fences come off. Nothing that reaches disk carries it.
_VERBATIM = "\x00"

BRIEF_FILENAME = "lab_pack_brief.md"
BRIEF_BANNER = "<!-- labpack-brief -->"
BRIEF_NOTES_HEADING = "## Your Notes"
DEFAULT_WORKFLOW_SOURCE = "sops.md"

# Words that identify nothing. An instrument section whose heading is made
# only of these - `Shared and Borrowed Equipment`, `Software the Lab Actually
# Runs` - is a list rather than an instrument and must never attach to a
# step. No technique, material or lab name is in here and none may be: this
# is English and furniture, and decision 9 holds.
GENERIC_HEADING_WORDS = {
    "a", "an", "and", "any", "are", "at", "be", "by", "for", "from", "in",
    "is", "it", "of", "on", "or", "that", "the", "this", "to", "with",
    "absent", "actually", "also", "available", "bench", "borrowed",
    "building", "college", "common", "department", "easy", "equipment",
    "facility", "general", "group", "hood", "instrument", "instruments",
    "inventory", "items", "lab", "laboratory", "labs", "list", "microscope",
    "microscopes", "miss", "misc", "new", "not", "old", "only", "other",
    "others", "own", "present", "room", "rooms", "runs", "scope", "scopes",
    "shared", "software", "stuff", "suite", "system", "systems", "things",
    "tool", "tools", "university", "used", "where", "which", "workstation",
}

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]*")


def split_sections(text: str, level: int = 2) -> list[dict]:
    """A markdown document as `## ` sections, bodies VERBATIM.

    The preamble above the first heading is not a section - it is the file's
    own note to a reader and belongs to the file rather than to any step.
    Nothing here rewrites a body: the whole value of the brief is that the
    number in it is the number in the pack, character for character.
    """
    out: list[dict] = []
    if not text:
        return out
    marker = "#" * level + " "
    heading: str = ""
    body: list[str] = []
    for line in text.splitlines():
        if line.startswith(marker) and not line.startswith(marker + "#"):
            if heading:
                out.append({"heading": heading,
                            "body": "\n".join(body).strip("\n")})
            heading = line[len(marker):].strip()
            body = []
        elif line.startswith("#" * (level - 1) + " ") and level > 1:
            # A heading ABOVE this level closes the section rather than
            # joining it. A pack that grows a second `# ` region must not
            # have it swallowed into the last step.
            if heading:
                out.append({"heading": heading,
                            "body": "\n".join(body).strip("\n")})
                heading, body = "", []
        elif heading:
            body.append(line)
    if heading:
        out.append({"heading": heading, "body": "\n".join(body).strip("\n")})
    return out


def _norm_heading(name: str) -> str:
    """Case, padding and punctuation, and nothing cleverer.

    A bundle note matches a step by NAME, and the match is deliberately not
    fuzzy past this: a note attached to a step whose name has really changed
    is a note about a step the lab has changed, and guessing at it is how a
    member's decision quietly moves to the wrong place.
    """
    return " ".join(_WORD_RE.findall((name or "").lower()))


def pack_documents(path: str) -> dict:
    """Every `.md` in the pack, as sections. No document is left out."""
    docs: dict = {}
    if not path or not os.path.isdir(path):
        return docs
    for name in sorted(os.listdir(path)):
        if not name.endswith(".md") or name.startswith("."):
            continue
        try:
            with io.open(os.path.join(path, name), encoding="utf-8",
                         errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        docs[name] = {"title": first_heading(os.path.join(path, name)),
                      "sections": split_sections(text)}
    return docs


def distinctive_tokens(headings: list[str], step_texts: list[str]) -> dict:
    """Which word in an instrument's name actually names THAT instrument.

    Three filters, and the third is the one that earns its place. A token is
    distinctive when it is four characters or longer, is not an English or
    furniture word, appears in exactly ONE instrument heading, **and appears
    in no more than a third of the steps**.

    That last one is what keeps a domain word out without the engine knowing
    any domain. `cryo` sits in one instrument heading in the real pack and in
    a third of its steps; matching on it would attach a milling instrument to
    a cell-growth step. Measuring it against the pack's own text costs
    nothing and needs no list of words this engine is not allowed to have.
    """
    counts: dict = {}
    per_heading: dict = {}
    for h in headings:
        # The parenthetical is a location, not a name: `(BYU)`, `(University
        # of Utah)`. Dropping it stops every instrument in one building
        # sharing a token.
        core = re.sub(r"\([^)]*\)", " ", h)
        toks = {t.lower() for t in _WORD_RE.findall(core) if len(t) >= 4}
        toks = {t for t in toks if t not in GENERIC_HEADING_WORDS}
        per_heading[h] = toks
        for t in toks:
            counts[t] = counts.get(t, 0) + 1
    cap = max(1, len(step_texts) // 3)
    lowered = [s.lower() for s in step_texts]
    out: dict = {}
    for h, toks in per_heading.items():
        keep = []
        for t in sorted(toks):
            if counts.get(t, 0) != 1:
                continue
            hits = sum(1 for s in lowered
                       if re.search(r"\b" + re.escape(t) + r"\b", s))
            if hits > cap:
                continue
            keep.append(t)
        out[h] = keep
    return out


def brief_route(pack_path: str, lab: dict | None = None) -> dict:
    """The lab's route: one step per `## ` section of the pack's SOP file.

    In file order, because a human curated that file in the order the work
    happens. A `workflow_source` in `lab.yml` names a different file; a pack
    with neither says so and the brief renders everything else.
    """
    lab = lab if lab is not None else read_lab_yml(pack_path)
    source = ""
    raw = lab.get("raw") or {}
    named = raw.get("workflow_source")
    docs = pack_documents(pack_path)
    if isinstance(named, str) and named.strip():
        source = named.strip()
    elif DEFAULT_WORKFLOW_SOURCE in docs:
        source = DEFAULT_WORKFLOW_SOURCE
    out: dict = {"source": source, "steps": [], "instruments": [],
                 "documents": docs, "notes": []}
    if not source or source not in docs:
        out["source"] = ""
        out["notes"].append(
            "this pack carries no step file, so the brief has no route to "
            "lay out. Everything else in the pack is indexed below"
            + (f" (`{source}` is named in lab.yml and is not in the pack)"
               if source else ""))
        return out

    steps = [dict(s) for s in docs[source]["sections"]]
    inst_file = "instruments.md" if "instruments.md" in docs else ""
    inst_sections = docs[inst_file]["sections"] if inst_file else []
    tokens = distinctive_tokens([s["heading"] for s in inst_sections],
                                [s["heading"] + "\n" + s["body"]
                                 for s in steps])
    for step in steps:
        hay = (step["heading"] + "\n" + step["body"]).lower()
        named_here = []
        for sec in inst_sections:
            toks = tokens.get(sec["heading"]) or []
            if any(re.search(r"\b" + re.escape(t) + r"\b", hay)
                   for t in toks):
                named_here.append(sec["heading"])
        step["instruments"] = named_here
    used: list = []
    for step in steps:
        for name in step["instruments"]:
            if name not in used:
                used.append(name)
    out["steps"] = steps
    out["instruments"] = used
    out["instrument_file"] = inst_file
    out["instrument_sections"] = {s["heading"]: s["body"]
                                  for s in inst_sections}
    return out


def _project_title(root: str) -> str:
    try:
        with io.open(os.path.join(root, "project.yml"),
                     encoding="utf-8") as fh:
            data = parse_flat_yaml(fh.read())
    except OSError:
        return ""
    for key in ("title", "short_name"):
        val = data.get(key)
        if isinstance(val, str) and val.strip() and "{{" not in val:
            return val.strip()
    return ""


def _bullets(items: object) -> list:
    if isinstance(items, str):
        items = [items]
    if not isinstance(items, list):
        return []
    return [str(x).strip() for x in items if str(x).strip()]


def render_brief(pack: dict, route: dict, bundle: dict, project_title: str,
                 lab: dict, carried: str = "") -> str:
    """The file itself.

    Order is load-bearing in one place: **the project's half is first on
    every step.** The pack's half is the long half, and a reader scrolling
    for their own decisions must not have to read three paragraphs of SOP to
    find out there are none.
    """
    bundle = bundle or {}
    today = datetime.date.today().isoformat()
    display = pack.get("display_name") or "this lab"
    curated = pack.get("curated_on") or "an unrecorded date"
    lines: list = [BRIEF_BANNER, ""]
    lines.append("# Lab Pack Brief"
                 + (f" - {project_title}" if project_title else ""))
    lines.append("")
    lines.append(
        f"Generated {today} by `labpack.py brief` from the **{display}** "
        f"pack (`{pack.get('name') or pack.get('path', '')}` "
        f"{pack.get('version') or 'unversioned'}), curated {curated}"
        + (f" from {lab.get('curated_from')}."
           if lab.get("curated_from") else "."))
    lines.append("")
    lines.append(
        "**Nothing here is a methods fact.** Every number below was true on "
        f"the pack's {curated} curation, for the standard case, in the "
        "standard configuration. If one contradicts what you know, you are "
        "right and the pack is stale - say so, and correct it at the source "
        "the pack names. What this project actually did goes in "
        "`data/methods_facts.yml`, written by the person who did it.")
    lines.append("")
    lines.append(
        "**Ask about any of it.** This file is here so the questions that "
        "come up while the data is being collected have somewhere to be "
        "answered from, and `## Your Notes` at the end is yours - "
        "regenerating this file leaves it alone.")
    lines.append("")

    claim = str(bundle.get("claim") or "").strip()
    techniques = _bullets(bundle.get("techniques"))
    plan = str(bundle.get("data_plan") or "").strip()
    if claim or techniques or plan:
        lines += ["## This Project", ""]
        if claim:
            lines += [f"**The claim.** {claim}", ""]
        if techniques:
            lines += ["**Techniques.** " + "; ".join(techniques), ""]
        if plan:
            lines += [f"**What is being collected.** {plan}", ""]

    lines += ["## The Steps", ""]
    if not route["steps"]:
        for note in route["notes"]:
            lines += [f"The pack carries no route to lay out here: {note}.",
                      ""]
    else:
        lines.append(
            f"Every step the pack carries, in the order `{route['source']}` "
            "puts them, including the ones this project never discussed.")
        lines.append("")
    for i, step in enumerate(route["steps"], start=1):
        note = step.get("project") or {}
        lines += [f"### {i}. {step['heading']}", ""]
        if note.get("note"):
            lines += [f"**For this project.** {note['note']}", ""]
        else:
            lines += ["**For this project.** Nothing project-specific is "
                      "recorded for this step yet.", ""]
        decided = _bullets(note.get("decided"))
        if decided:
            lines.append("**Decided.**")
            lines += [f"- {d}" for d in decided]
            lines.append("")
        unknowns = _bullets(note.get("unknowns"))
        if unknowns:
            lines.append("**Still open.**")
            lines += [f"- {u}" for u in unknowns]
            lines.append("")
        lines += [f"**What the pack says** - `{route['source']}`, curated "
                  f"{curated}:", ""]
        lines += [_VERBATIM + step["body"] + _VERBATIM, ""]
        if step.get("instruments"):
            lines += ["**Instruments this step names.** "
                      + "; ".join(step["instruments"])
                      + " - quoted under The Instruments below.", ""]

    if route.get("instruments"):
        lines += ["## The Instruments These Steps Name", ""]
        lines.append(
            "Only the ones the route above touches. The rest are in the "
            f"pack's `{route.get('instrument_file')}`.")
        lines.append("")
        for name in route["instruments"]:
            lines += [f"### {name}", ""]
            lines += [_VERBATIM
                      + route["instrument_sections"].get(name, "")
                      + _VERBATIM, ""]
            lines += [f"- {display} pack, `{route.get('instrument_file')}`, "
                      f"curated {curated}.", ""]

    lines += ["## The Rest of the Pack", ""]
    lines.append(
        "Not quoted here, because a copy of a document is a document that "
        "drifts. Read it in the pack at `" + (pack.get("path") or "") + "`.")
    lines.append("")
    lines += ["| File | What it holds |", "|---|---|"]
    for name, doc in route["documents"].items():
        if name == route.get("source"):
            continue
        heads = [s["heading"] for s in doc["sections"]]
        if name == route.get("instrument_file"):
            # Everything in the instrument file the route did NOT name. An
            # instrument no step touches is still in this lab, and a brief
            # that leaves it out reads as a lab that does not own one.
            heads = [h for h in heads if h not in route["instruments"]]
            if not heads:
                continue
        shown = heads[:8]
        more = "" if len(heads) <= 8 else ", ..."
        lines.append(f"| `{name}` | " + (doc["title"] or name) + ": "
                     + ("; ".join(shown) + more if shown
                        else "no sections") + " |")
    lines.append("")

    inv = ((lab.get("live") or {}).get("inventory") or {})
    lines += ["## The Live Inventory", ""]
    if inv:
        on_hand = bool(inv.get("has_on_hand_column"))
        answers = "on-hand" if on_hand else "owned-and-where"
        lines.append(
            f"The lab's inventory answers **{answers}**"
            + ("" if on_hand else
               " - *do we own one, and where is it kept*, never *is there "
               "any left*") + ". "
            + (f"The {inv['owner']} maintains it. " if inv.get("owner") else "")
            + "It is read on request and written to no file: ask, and the "
              "read is quoted with the time it was taken. **An item that is "
              "not listed is unknown, never absent** - a row missing from an "
              "ownership list may simply never have been added.")
    else:
        lines.append(
            "This pack names no live inventory, so what is on the shelf is "
            "a question for the lab rather than for this file.")
    lines.append("")
    lines.append(f"```\nlabpack.py inventory --item \"<the thing>\"\n```")
    lines.append("")

    lines += [BRIEF_NOTES_HEADING, ""]
    if carried.strip():
        lines.append(carried.strip("\n"))
    else:
        seeded = _bullets(bundle.get("notes"))
        lines.append(
            "Answers, corrections and anything the pack got wrong. This "
            "section is yours - `labpack.py brief` never overwrites it.")
        lines.append("")
        for s in seeded:
            lines.append(f"- {s}")
        if not seeded:
            lines.append("- ")
    # Blank runs are tidied in the FRAME and never inside a quoted body. The
    # bodies are the promise this whole file rests on, so the tidy-up runs
    # over the segments between the fences rather than over the text, and
    # the fences come off here - nothing that reaches disk carries one.
    parts = "\n".join(lines).split(_VERBATIM)
    return "".join(p if i % 2 else re.sub(r"\n{3,}", "\n\n", p)
                   for i, p in enumerate(parts)).rstrip() + "\n"


def _brief_destination(project: str, out: str) -> tuple:
    """Where the file goes, and the one place it may never go.

    `data/` is refused by name because that is the single destination where
    this file would be mistaken for a checked fact. Everything else about
    keeping a pack default out of `methods_facts.yml` is wording; this is
    the part that is enforced.
    """
    root = os.path.abspath(os.path.expanduser(project or "."))
    dest = (os.path.abspath(os.path.expanduser(out)) if out
            else os.path.join(root, "plan", BRIEF_FILENAME)
            if os.path.isdir(os.path.join(root, "plan"))
            else os.path.join(root, BRIEF_FILENAME))
    try:
        rel = os.path.relpath(dest, root)
    except ValueError:                       # a different drive
        rel = dest
    parts = [p.lower() for p in rel.replace("\\", "/").split("/")]
    if "data" in parts:
        return dest, (
            "a lab pack brief is never written under `data/`. Everything "
            "there is a record of what THIS project did - `methods_facts."
            "yml` is rendered into the methods section - and a pack default "
            "sitting beside it is the one way a facility number reaches "
            "print wearing the shape of a checked fact. It goes in `plan/`.")
    return dest, ""


def _carried_notes(path: str) -> tuple:
    """(the `## Your Notes` body, is_ours). A file we did not write is not
    ours to overwrite, and the banner is how that is known."""
    if not os.path.exists(path):
        return "", True
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return "", False
    # The banner is written on line 1. It is looked for in the opening of the
    # file rather than on that exact line so a member who adds a line above
    # it does not lose their notes to a refusal.
    if BRIEF_BANNER not in text[:400]:
        return "", False
    # At the START OF A LINE, and only there. The header names the section
    # in a sentence - "`## Your Notes` at the end is yours" - and a plain
    # `find` matches that first, carries the entire document forward as
    # "notes", and produces a file that grows a copy of itself every run.
    m = re.search(r"^" + re.escape(BRIEF_NOTES_HEADING) + r"\s*$", text,
                  re.MULTILINE)
    if not m:
        return "", True
    return text[m.end():].strip("\n"), True


def brief(project: str = ".", bundle: dict | None = None, out: str = "",
          dry_run: bool = False, force: bool = False) -> dict:
    """One project's copy of the pack. Reads a pack, writes one file.

    Every refusal happens before anything is written, and no refusal is a
    partial write: a member who fixes the bundle and re-runs must not find
    half a brief on disk from the attempt that failed.
    """
    res: dict = {"state": "", "path": "", "text": "", "errors": [],
                 "notes": [], "steps": 0, "instruments": [],
                 "carried_notes": False, "pack": "", "version": "",
                 "curated_on": ""}
    root = os.path.abspath(os.path.expanduser(project or "."))
    if not os.path.isdir(root):
        res["state"] = "refused"
        res["errors"].append(f"no such project directory: {root}")
        return res

    pack = find_pack()
    if pack.get("source") == "ambiguous":
        res["state"] = "ambiguous"
        res["notes"].append(pack.get("note", ""))
        return res
    if not pack.get("path"):
        res["state"] = "no-pack"
        res["notes"].append(pack.get("note", ""))
        return res

    info = pack_info(pack["path"])
    lab = read_lab_yml(pack["path"])
    route = brief_route(pack["path"], lab)
    res["notes"] += route["notes"]
    res["pack"] = info.get("name", "")
    res["version"] = info.get("version", "")
    res["curated_on"] = info.get("curated_on", "")

    # The bundle's notes, attached to the steps they are about. A step that
    # does not exist REFUSES: silently dropping it is a member's decision
    # disappearing between two pack versions.
    bundle = dict(bundle or {})
    by_norm = {_norm_heading(s["heading"]): s for s in route["steps"]}
    seen: set = set()
    for entry in (bundle.get("steps") or []):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("step") or "").strip()
        key = _norm_heading(name)
        if not key:
            res["errors"].append("a step entry names no step")
            continue
        if key not in by_norm:
            res["errors"].append(
                f"the bundle names a step this pack does not carry: "
                f"\"{name}\". The steps in "
                f"`{route['source'] or 'this pack'}` are: "
                + "; ".join(s["heading"] for s in route["steps"]))
            continue
        if key in seen:
            res["errors"].append(
                f"two notes are attached to one step: \"{name}\". One note "
                "per step, so nothing has to decide which of them wins")
            continue
        seen.add(key)
        by_norm[key]["project"] = entry

    dest, why = _brief_destination(root, out)
    if why:
        res["errors"].append(why)
    carried, ours = _carried_notes(dest)
    if not ours and not force:
        res["errors"].append(
            f"{dest} exists and was not written by this command (it carries "
            f"no {BRIEF_BANNER} banner), so it is not this command's file to "
            "overwrite. Write elsewhere with --out, or overwrite it "
            "deliberately with --force")
    if res["errors"]:
        res["state"] = "refused"
        res["path"] = dest
        return res

    text = render_brief({**info, **pack}, route, bundle,
                        _project_title(root), lab, carried)
    res.update({"path": dest, "text": text, "steps": len(route["steps"]),
                "instruments": route["instruments"],
                "carried_notes": bool(carried.strip())})
    if dry_run:
        res["state"] = "dry-run"
        return res
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with io.open(dest, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    except OSError as exc:
        res["state"] = "refused"
        res["errors"].append(f"could not write {dest}: {exc}")
        return res
    res["state"] = "written"
    return res


def brief_check(project: str = ".", out: str = "") -> dict:
    """Is the brief on disk still the pack that is installed?

    Reports. It never regenerates - refreshing facility knowledge underneath
    somebody mid-conversation is the thing `show` refuses to do, and a brief
    carrying a member's own notes is a worse thing to rewrite unasked.
    """
    res: dict = {"state": "", "line": "", "path": "", "was": "", "now": ""}
    root = os.path.abspath(os.path.expanduser(project or "."))
    dest, _ = _brief_destination(root, out)
    res["path"] = dest
    if not os.path.exists(dest):
        res["state"] = "missing"
        res["line"] = (f"no brief at {dest}. `labpack.py brief --project "
                       f"\"{project}\"` writes one.")
        return res
    try:
        with io.open(dest, encoding="utf-8", errors="replace") as fh:
            text = fh.read(4000)
    except OSError as exc:
        res["state"] = "unknown"
        res["line"] = f"the brief could not be read: {exc}"
        return res
    m = re.search(r"pack \(`[^`]*` ([^)]+)\), curated (\d{4}-\d{2}-\d{2})",
                  text)
    res["was"] = m.group(1).strip() if m else ""
    pack = find_pack()
    if not pack.get("path"):
        res["state"] = "unknown"
        res["line"] = ("a brief is on disk and no pack is installed, so "
                       "whether it is current cannot be answered here.")
        return res
    res["now"] = pack_info(pack["path"]).get("version", "")
    if not res["was"]:
        res["state"] = "unknown"
        res["line"] = ("the brief does not record which pack version it came "
                       "from, so whether it is current is unknown.")
        return res
    if res["was"] == res["now"]:
        res["state"] = "current"
        res["line"] = (f"the brief was written from pack {res['was']}, which "
                       "is the one installed.")
        return res
    res["state"] = "stale"
    res["line"] = (f"the brief was written from pack {res['was']} and "
                   f"{res['now']} is installed now. Re-run `labpack.py brief "
                   "--project \"...\"` to bring it up to date; your notes "
                   "are carried forward.")
    return res


def print_brief(res: dict) -> None:
    if res["state"] == "no-pack":
        print("No lab resource pack is installed, so there is nothing to "
              "summarize.")
        for line in _wrap(res["notes"][0] if res["notes"] else ""):
            print(f"  {line}")
        return
    if res["state"] == "ambiguous":
        for line in _wrap(res["notes"][0] if res["notes"] else ""):
            print(f"  {line}")
        return
    if res["state"] == "refused":
        print("REFUSED - nothing was written.")
        for err in res["errors"]:
            for line in _wrap(err):
                print(f"  {line}")
        return
    verb = "would write" if res["state"] == "dry-run" else "wrote"
    print(f"{verb} {res['path']}")
    print(f"  {res['steps']} step(s) from the pack, "
          f"{len(res['instruments'])} instrument(s) named")
    if res["carried_notes"]:
        print("  your `## Your Notes` section was carried forward unchanged")
    for note in res["notes"]:
        for line in _wrap(note):
            print(f"  note: {line}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="labpack.py",
        description="Find the lab resource pack and the drop-zones, and "
                    "rescue what is stranded in one.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true",
                        help="emit JSON instead of text")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("show", parents=[common],
                   help="which pack and which drop-zones this copy resolves")

    bmp = sub.add_parser("bump", parents=[common],
                         help="cut a dated release of the pack")
    bmp.add_argument("--path", default="",
                     help="the pack repository. Default: the resolved pack")
    bmp.add_argument("--curated", action="store_true",
                     help="also move curated_on to today. Only if you "
                          "actually re-read the source")
    bmp.add_argument("--dry-run", action="store_true")

    chk = sub.add_parser("check", parents=[common],
                         help="is a version bump overdue in this pack?")
    chk.add_argument("--path", default="")

    ivy = sub.add_parser("inventory", parents=[common],
                         help="the live inventory, read once, written "
                              "nowhere. Unknown is never out of stock")
    ivy.add_argument("--item", default="",
                     help="ask about one thing by name")
    ivy.add_argument("--connector-file", dest="connector_file", default="",
                     help="rung 2 of 5.2: a LOCAL copy a skill already pulled "
                          "through the Box connector this run - never a Box "
                          "path or URL, this module makes no connector call. "
                          "Read exactly like a synced fallback_path and never "
                          "written back to. Ignored when fallback_path "
                          "resolves on this machine")

    brf = sub.add_parser("brief", parents=[common],
                         help="one project's copy of the pack: every step, "
                              "quoted, with this project's decisions "
                              "against it")
    brf.add_argument("--project", default=".",
                     help="the project directory. Default: here")
    brf.add_argument("--bundle", default="",
                     help="a JSON file of this project's half - the claim, "
                          "the techniques, and a note, a decision or an "
                          "open question per step")
    brf.add_argument("--out", default="",
                     help="write somewhere other than plan/"
                          + BRIEF_FILENAME + ". Never under data/")
    brf.add_argument("--dry-run", action="store_true",
                     help="render it and write nothing")
    brf.add_argument("--force", action="store_true",
                     help="overwrite a file at the destination that this "
                          "command did not write")
    brf.add_argument("--check", action="store_true",
                     help="is the brief on disk still the pack that is "
                          "installed? Reports; never regenerates")

    cfg = sub.add_parser("config", parents=[common],
                         help="record a pack path or a drop-zone, per machine")
    cfg.add_argument("--path", nargs="?", const="", default=None,
                     help="the pack folder to use. With no argument, clears it")
    cfg.add_argument("--resources", nargs="?", const="", default=None,
                     help="the drop-zone folder. With no argument, the "
                          "durable default ~/.paper-engine/resources/")
    cfg.add_argument("--adopt", action="store_true",
                     help="move material stranded inside a plugin install to "
                          "the durable drop-zone")
    cfg.add_argument("--dry-run", action="store_true")

    args = p.parse_args(argv)

    if args.cmd == "config":
        out: dict = {}
        if args.adopt:
            out["adopt"] = adopt(dry_run=args.dry_run)
        if args.path is not None:
            out["config"] = set_config("pack", os.path.abspath(
                os.path.expanduser(args.path)) if args.path else "")
        if args.resources is not None and not args.adopt:
            target = (os.path.abspath(os.path.expanduser(args.resources))
                      if args.resources else durable_resources(create=True))
            out["config"] = set_config("resources", target)
            out["resources"] = target
        if args.json:
            print(json.dumps(out, indent=2, ensure_ascii=False))
        else:
            if "adopt" in out:
                print_adopt(out["adopt"])
            if "resources" in out:
                print(f"drop-zone: {out['resources']}")
            if "config" in out and "resources" not in out:
                print(f"recorded in {config_path()}")
        return 0

    if args.cmd == "brief":
        if args.check:
            chk = brief_check(args.project, out=args.out)
            if args.json:
                print(json.dumps(chk, indent=2, ensure_ascii=False))
            else:
                for line in _wrap(chk["line"]):
                    print(line)
            return 0
        payload: dict = {}
        if args.bundle:
            try:
                with io.open(args.bundle, encoding="utf-8") as fh:
                    loaded = json.load(fh)
            except (OSError, ValueError) as exc:
                print(f"could not read {args.bundle}: {exc}", file=sys.stderr)
                return 2
            if not isinstance(loaded, dict):
                print(f"{args.bundle} is not a JSON object", file=sys.stderr)
                return 2
            payload = loaded
        res = brief(args.project, bundle=payload, out=args.out,
                    dry_run=args.dry_run, force=args.force)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print_brief(res)
        return 2 if res["state"] == "refused" else 0

    if args.cmd == "bump":
        root = args.path or find_pack().get("path") or ""
        if not root:
            print("No pack to bump. Say which one with --path.",
                  file=sys.stderr)
            return 2
        res = bump_pack(root, dry_run=args.dry_run, curated=args.curated)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0
        verb = "would write" if args.dry_run else "wrote"
        if res.get("version"):
            print(f"{verb} version {res['version']} "
                  f"(was {res['was'] or 'none'}) into {res['path']}")
        if res.get("curated_on"):
            print(f"{verb} curated_on {res['curated_on']} into lab.yml")
        for n in res.get("notes") or []:
            for line in _wrap(n):
                print(f"  note: {line}")
        return 0

    if args.cmd == "check":
        root = args.path or find_pack().get("path") or ""
        if not root:
            print("No pack to check. Say which one with --path.",
                  file=sys.stderr)
            return 2
        res = _release().version_staleness(root)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            _release().print_check(res)
        return 0

    if args.cmd == "inventory":
        res = inventory(connector_path=args.connector_file)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0
        if res["state"] != "read":
            print("UNKNOWN - and unknown is never out of stock.")
            for line in _wrap(res["why"]):
                print(f"  {line}")
            return 0
        print(res["attribution"])
        print(f"  {res['rows']} row(s), answering: {res['answers']}")
        for line in _wrap(res["caveat"]):
            print(f"  {line}")
        if args.item:
            print("")
            hit = inventory_lookup(args.item, res)
            for line in _wrap(hit["phrase"]):
                print(f"  {line}")
        print("")
        print("Nothing here is written to any file, and nothing is written "
              "back to")
        print("the source. It answers this run and then it is gone.")
        return 0

    if args.cmd == "show":
        pack = find_pack()
        roots = resource_roots()
        fresh = freshness(pack)
        if args.json:
            print(json.dumps({"pack": pack, "freshness": fresh,
                              "drop_zones": roots}, indent=2,
                             ensure_ascii=False))
            return 0
        print_pack(pack, fresh)
        print("")
        print("Drop-zones (all of them are read):")
        print_roots(roots)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
