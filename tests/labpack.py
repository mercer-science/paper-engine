#!/usr/bin/env python3
"""
tests/labpack.py - measure tools/labpack.py against the cases
specs/lab-resource-pack.md names.

Fully offline, ~2s. Nothing here touches Box, touches the network, or reads
the real `~/.paper-engine/` or `~/.claude/plugins/` - every root is redirected
into a temporary directory by the three escape hatches labpack.py declares for
exactly that purpose. Spec 5.3: *the connector CALL has no test and is
documented as having none.* What IS tested, because it needs no network at
all: once a skill has made that call and saved what came back, reading that
local copy through `--connector-file` is the same two readers as
`fallback_path`, fixture for fixture.

The five that matter most, because each one fails quietly:

  - a value a pack contributes arrives COMMENTED OUT (spec 4.3). This failing
    means a plausible facility default - a real detector model, a real
    throughput - reaches a methods section wearing the shape of a checked
    fact. It is the most dangerous wrong this toolkit can produce, because it
    is usually right.
  - TWO packs stop and ask (spec 4.1). This failing means a member in two labs
    gets one lab's SOPs quoted at them with no sign the other existed.
  - a drop-zone root inside a plugin install is named AS AT RISK, by filename
    (spec 4.5, item 94). This failing means `/plugin update` deletes a
    member's material and the first sign of it is a feasibility question that
    stopped being specific.
  - `--adopt` MOVES rather than copies. This failing leaves two copies of an
    SOP that must agree, which is how they come to disagree.
  - an inventory that is missing, malformed or empty produces UNKNOWN rather
    than absent (spec 5.1 rule 3). This failing turns "I could not read the
    file" into "you are out of stock", which is a refusal dressed as a fact.

  python tests/labpack.py
"""

from __future__ import annotations

import datetime
import importlib.util
import io
import json
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
    """tests/labpack.py and tools/labpack.py share a name, so a plain import
    resolves to this file. By path, under a distinct name, as every other
    suite here does."""
    path = os.path.join(TOOLS, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


lp = _load("pwa_labpack", "labpack.py")
idea = _load("pwa_idea_for_labpack", "idea.py")
sc = _load("pwa_scaffold_for_labpack", "scaffold.py")
ms = _load("pwa_manuscript_for_labpack", "manuscript.py")

PASSED = 0
FAILED = 0


def check(label: str, passed: bool, detail: object = "") -> bool:
    global PASSED, FAILED
    if passed:
        PASSED += 1
    else:
        FAILED += 1
    print(f"[{'pass' if passed else 'FAIL'}] {label}")
    if detail and not passed:
        for line in str(detail).rstrip().splitlines():
            print(f"         {line}")
    return passed


def section(title: str) -> None:
    print(f"\n--- {title} " + "-" * max(0, 58 - len(title)))


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def read(path: str) -> str:
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def run(*argv: str) -> tuple[int, str, str]:
    p = subprocess.run([sys.executable, os.path.join(TOOLS, "labpack.py"),
                        *argv], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    return p.returncode, p.stdout, p.stderr


# ---------------------------------------------------------------------------
# A sandbox: every root this engine can reach, redirected into one temp dir.
# ---------------------------------------------------------------------------

class Sandbox:
    """The three escape hatches, plus the two the ladders declare.

    `$PAPER_ENGINE_HOME` moves the durable drop-zone and the config file,
    `$PAPER_ENGINE_PLUGINS_DIR` moves the installed-plugins index, and
    `$PAPER_ENGINE_TOOLKIT` moves the shipped folder. Without all three a
    suite run would read - and `--adopt` would MOVE - the author's own files,
    which is the failure this class exists to make impossible.
    """

    KEYS = ("PAPER_ENGINE_HOME", "PAPER_ENGINE_PLUGINS_DIR",
            "PAPER_ENGINE_CODEX_PLUGINS_DIR", "CODEX_HOME",
            "PAPER_ENGINE_TOOLKIT", "PAPER_ENGINE_LAB_PACK",
            "PAPER_ENGINE_RESOURCES", "CLAUDE_PLUGIN_ROOT")

    def __init__(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="pwa_labpack_")
        self.home = os.path.join(self.dir, "home")
        self.plugins = os.path.join(self.dir, "plugins")
        # Codex's root sits inside a Codex home, because its other records
        # (config.toml, .tmp/marketplaces/) are read from one level up.
        self.codex_home = os.path.join(self.dir, "codex_home")
        self.codex = os.path.join(self.codex_home, "plugins")
        self.toolkit = os.path.join(self.dir, "toolkit")
        os.makedirs(os.path.join(self.toolkit, "resources"), exist_ok=True)
        os.makedirs(self.plugins, exist_ok=True)
        self._saved = {k: os.environ.get(k) for k in self.KEYS}
        for k in self.KEYS:
            os.environ.pop(k, None)
        os.environ["PAPER_ENGINE_HOME"] = self.home
        os.environ["PAPER_ENGINE_PLUGINS_DIR"] = self.plugins
        os.environ["PAPER_ENGINE_CODEX_PLUGINS_DIR"] = self.codex
        os.environ["PAPER_ENGINE_TOOLKIT"] = self.toolkit
        # A new sandbox is a new run. Without this the in-memory inventory
        # cache carries one test's shelf into the next one's assertions.
        lp.reset_inventory_cache()

    def close(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.dir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False

    # -- fixtures ---------------------------------------------------------

    def install_pack(self, name: str = "jensen-resource-pack",
                     marketplace: str = "", version: str = "2026.9.9",
                     lab_pack: bool = True, lab_yml: str = "",
                     display: str = "Jensen Lab",
                     clone_version: str = "",
                     sha: str = "73e53311cb26d523da3b285d11ea468e4088f0c3") -> str:
        """A pack installed the way the 2026-09-17 probe measured one."""
        marketplace = marketplace or f"{name}-marketplace"
        path = os.path.join(self.plugins, "cache", marketplace, name, version)
        manifest: dict = {"name": name, "displayName": display,
                    "version": version, "description": "a fixture pack"}
        if lab_pack:
            manifest["labPack"] = True
        write(os.path.join(path, ".claude-plugin", "plugin.json"),
              json.dumps(manifest, indent=2))
        write(os.path.join(path, "lab.yml"), lab_yml or (
            f"pack: {name}\n"
            f"display_name: \"{display}\"\n"
            "curated_from: \"Box, folder 000\"\n"
            "curated_on: \"2026-09-09\"\n"
            "curated_by: \"someone@example.edu\"\n"))
        write(os.path.join(path, "instruments.md"),
              "# Instruments\n\n## Fixture Scope (TEM)\n\n"
              "- **What it is for.** A fixture.\n\n"
              "```yaml\ninstrument_make: Fixture Co\n"
              "instrument_model: Fixture Model One\n"
              "accelerating_voltage_kV: 200\n```\n")
        write(os.path.join(path, "sops.md"),
              "# SOPs\n\n## Negative stain screening\n\n"
              "- Throughput: about 2 hours a grid.\n")
        # The index, in the measured shape: a LIST of install records.
        index = os.path.join(self.plugins, "installed_plugins.json")
        try:
            data = json.loads(read(index))
        except (OSError, ValueError):
            data = {"version": 2, "plugins": {}}
        data["plugins"][f"{name}@{marketplace}"] = [
            {"scope": "user", "installPath": path, "version": version,
             "installedAt": "2026-09-17T21:02:52.142Z",
             "gitCommitSha": sha}]
        write(index, json.dumps(data, indent=2))
        # The marketplace clone beside it. Item 116: this is what freshness
        # USED to read, and it is deliberately still written here so the
        # suite proves the check no longer believes it.
        clone = os.path.join(self.plugins, "marketplaces", marketplace)
        cm = dict(manifest)
        cm["version"] = clone_version or version
        write(os.path.join(clone, ".claude-plugin", "plugin.json"),
              json.dumps(cm, indent=2))
        write(os.path.join(self.plugins, "known_marketplaces.json"),
              json.dumps({marketplace: {
                  "source": {"source": "github", "repo": f"x/{name}"},
                  "installLocation": clone}}, indent=2))
        return path

    def install_codex_pack(self, name: str = "jensen-resource-pack",
                           marketplace: str = "", version: str = "2026.9.9",
                           display: str = "Jensen Lab", sha: str = "",
                           packed: bool = False,
                           source: str = "") -> str:
        """A pack installed the way Codex 0.159.3 was measured to install
        one: a whole copy of the marketplace snapshot, `.git` included, in
        the same cache layout; the marketplace in `config.toml`; the snapshot
        under `.tmp/marketplaces/`. No `installed_plugins.json`."""
        marketplace = marketplace or f"{name}-marketplace"
        path = os.path.join(self.codex, "cache", marketplace, name, version)
        manifest = {"name": name, "displayName": display, "version": version,
                    "description": "a fixture pack", "labPack": True}
        write(os.path.join(path, ".claude-plugin", "plugin.json"),
              json.dumps(manifest, indent=2))
        write(os.path.join(path, "lab.yml"),
              f"pack: {name}\ndisplay_name: \"{display}\"\n"
              "curated_on: \"2026-09-09\"\n")
        if sha:
            write(os.path.join(path, ".git", "HEAD"), "ref: refs/heads/main\n")
            if packed:
                write(os.path.join(path, ".git", "packed-refs"),
                      "# pack-refs with: peeled fully-peeled sorted\n"
                      f"{sha} refs/heads/main\n")
            else:
                write(os.path.join(path, ".git", "refs", "heads", "main"),
                      sha + "\n")
        snap = os.path.join(self.codex_home, ".tmp", "marketplaces",
                            marketplace)
        write(os.path.join(snap, ".claude-plugin", "plugin.json"),
              json.dumps(manifest, indent=2))
        url = source or f"https://github.com/x/{name}.git"
        cfg = os.path.join(self.codex_home, "config.toml")
        try:
            text = read(cfg)
        except OSError:
            text = ""
        text += (f"\n[marketplaces.{marketplace}]\nsource_type = \"git\"\n"
                 f"source = \"{url}\"\n\n"
                 f"[plugins.\"{name}@{marketplace}\"]\nenabled = true\n")
        write(cfg, text)
        return path

    def remote_says(self, sha: str, repo: str = "x/jensen-resource-pack",
                    ok: bool = True, error: str = "",
                    days_ago: int = 0) -> str:
        """What `remote.py check` would have left on disk.

        Written rather than asked for: the suite is offline, and the whole
        point of item 116's fix is that the reading half only ever reads this
        file. A test that reached GitHub would be testing GitHub.
        """
        asked = (datetime.datetime.now()
                 - datetime.timedelta(days=days_ago)).replace(
                     microsecond=0).isoformat()
        path = os.path.join(self.home, "remote-check.json")
        write(path, json.dumps({"repos": {repo: {
            "repo": repo, "sha": sha, "ok": ok, "error": error,
            "asked_at": asked}}}, indent=2))
        return path


# ---------------------------------------------------------------------------
# Step 3 - the drop-zone ladder
# ---------------------------------------------------------------------------

def test_dropzone_ladder() -> None:
    section("the drop-zone ladder (4.1) - ALL hits, not the first")

    with Sandbox() as sb:
        # Nothing configured, nothing dropped: the durable root is named and
        # the shipped one is named, and neither is an error.
        roots = lp.resource_roots()
        by_source = {r["source"]: r for r in roots}
        check("the durable root is always in the ladder",
              "durable" in by_source, [r["source"] for r in roots])
        check("and the shipped folder is too",
              "toolkit" in by_source, [r["source"] for r in roots])
        check("nothing resolves to an error when neither holds a file",
              all(not r.get("at_risk") for r in roots), roots)

        # 4.1: the durable default is created ON FIRST REFERENCE.
        durable = lp.durable_resources(create=True)
        check("~/.paper-engine/resources/ is created on first reference",
              os.path.isdir(durable), durable)
        check("and it is OUTSIDE every plugin directory",
              not lp.inside_plugin_install(durable), durable)

        # All four rungs, each with a file in it, all read.
        write(os.path.join(durable, "durable_sop.md"), "# Durable SOP\n")
        write(os.path.join(sb.toolkit, "resources", "shipped_note.md"),
              "# Shipped Note\n")
        envdir = os.path.join(sb.dir, "envzone")
        write(os.path.join(envdir, "env_sop.md"), "# Env SOP\n")
        cfgdir = os.path.join(sb.dir, "cfgzone")
        write(os.path.join(cfgdir, "cfg_sop.md"), "# Config SOP\n")
        lp.set_config("resources", cfgdir)
        os.environ["PAPER_ENGINE_RESOURCES"] = envdir

        roots = lp.resource_roots()
        check("all four rungs resolve at once",
              [r["source"] for r in roots]
              == ["env", "config", "durable", "toolkit"],
              [r["source"] for r in roots])
        check("and the rung numbers are the spec's, in order",
              [r["rung"] for r in roots] == [1, 2, 3, 4],
              [r["rung"] for r in roots])
        names = [f for r in roots for f in r["files"]]
        check("EVERY hit is read, not just the first",
              sorted(names) == ["cfg_sop.md", "durable_sop.md",
                                "env_sop.md", "shipped_note.md"],
              sorted(names))
        check("a root that does not exist is reported, not dropped",
              all("exists" in r for r in roots), roots)

        os.environ.pop("PAPER_ENGINE_RESOURCES", None)


def test_dropzone_at_risk_by_name() -> None:
    section("a plugin-install drop-zone is named AT RISK, by filename")

    with Sandbox() as sb:
        # The toolkit itself living inside the plugins cache is the whole
        # hazard: /plugin update replaces that directory wholesale.
        installed = os.path.join(sb.plugins, "cache", "pwa-marketplace",
                                 "paper-engine", "2026.9.17")
        res = os.path.join(installed, "resources")
        write(os.path.join(res, "TEM SOP.md"), "# TEM SOP\n")
        write(os.path.join(res, "Methods.docx"), "not really a docx")
        os.environ["PAPER_ENGINE_TOOLKIT"] = installed

        check("the toolkit copy is recognised as a plugin install",
              lp.inside_plugin_install(installed), installed)

        roots = lp.resource_roots()
        shipped = [r for r in roots if r["source"] == "toolkit"][0]
        check("its drop-zone is flagged at risk", shipped["at_risk"], shipped)
        check("and the files are named ONE BY ONE, not counted",
              sorted(shipped["at_risk_files"]) == ["Methods.docx",
                                                   "TEM SOP.md"],
              shipped.get("at_risk_files"))

        # A free-form filename is inventoried too - 4.5's "a member who drops
        # Methods.docx gets no error and no effect" is the defect being fixed.
        check("a free-form filename is inventoried, not ignored",
              "Methods.docx" in shipped["files"], shipped["files"])

        # The same folder in a CLONE is not at risk. Overstating the hazard is
        # its own problem (4.5's table).
        os.environ["PAPER_ENGINE_TOOLKIT"] = sb.toolkit
        write(os.path.join(sb.toolkit, "resources", "TEM SOP.md"), "# x\n")
        roots = lp.resource_roots()
        shipped = [r for r in roots if r["source"] == "toolkit"][0]
        check("the same folder in a git clone is NOT at risk",
              not shipped["at_risk"], shipped)

        # Neither is the durable root, ever.
        durable = [r for r in roots if r["source"] == "durable"][0]
        check("and the durable root is never at risk",
              not durable["at_risk"], durable)

        # At risk is a property of WHERE the folder is, not of which rung
        # named it: a member who deliberately points --resources into a
        # plugin install has exactly the same update waiting for their file.
        configured = os.path.join(installed, "resources")
        lp.set_config("resources", configured)
        roots = lp.resource_roots()
        cfg = [r for r in roots if r["source"] == "config"][0]
        check("a CONFIGURED root inside a plugin install is at risk too",
              cfg["at_risk"], cfg)
        check("and adopt rescues it as well",
              sorted(lp.adopt(dry_run=True)["moved"])
              == ["Methods.docx", "TEM SOP.md"],
              lp.adopt(dry_run=True))
        lp.set_config("resources", "")


def test_adopt_moves() -> None:
    section("--adopt MOVES what is stranded, and reports what moved")

    with Sandbox() as sb:
        installed = os.path.join(sb.plugins, "cache", "pwa-marketplace",
                                 "paper-engine", "2026.9.17")
        res = os.path.join(installed, "resources")
        write(os.path.join(res, "TEM SOP.md"), "# TEM SOP\nbody\n")
        write(os.path.join(res, "sub", "stain.md"), "# Stain\n")
        write(os.path.join(res, "README.md"), "# the shipped README\n")
        os.environ["PAPER_ENGINE_TOOLKIT"] = installed

        dry = lp.adopt(dry_run=True)
        check("a dry run moves nothing",
              os.path.isfile(os.path.join(res, "TEM SOP.md")))
        check("and still reports what it would move",
              sorted(dry["moved"]) == ["TEM SOP.md", "sub/stain.md"],
              dry["moved"])

        out = lp.adopt()
        durable = lp.durable_resources()
        check("the file arrives in the durable location",
              os.path.isfile(os.path.join(durable, "TEM SOP.md")))
        check("a subfolder's file keeps its subfolder",
              os.path.isfile(os.path.join(durable, "sub", "stain.md")))
        check("and the stranded copy is GONE - moved, not copied",
              not os.path.isfile(os.path.join(res, "TEM SOP.md")),
              "two copies of an SOP that must agree is how they disagree")
        check("the shipped README is left where it belongs",
              os.path.isfile(os.path.join(res, "README.md")),
              "it is the engine's file, not the member's")
        check("and it is not reported as moved",
              "README.md" not in out["moved"], out["moved"])
        check("the report names every file that moved",
              sorted(out["moved"]) == ["TEM SOP.md", "sub/stain.md"],
              out["moved"])
        check("the content survives the move",
              read(os.path.join(durable, "TEM SOP.md")) == "# TEM SOP\nbody\n")

        again = lp.adopt()
        check("a second run has nothing to move and says so",
              again["moved"] == [] and not again.get("error"), again)


def test_adopt_never_overwrites() -> None:
    section("--adopt refuses rather than overwriting a durable file")

    with Sandbox() as sb:
        installed = os.path.join(sb.plugins, "cache", "m", "paper-engine",
                                 "1")
        res = os.path.join(installed, "resources")
        write(os.path.join(res, "stain.md"), "the stranded one\n")
        os.environ["PAPER_ENGINE_TOOLKIT"] = installed
        durable = lp.durable_resources(create=True)
        write(os.path.join(durable, "stain.md"), "the durable one\n")

        out = lp.adopt()
        check("the durable file is untouched",
              read(os.path.join(durable, "stain.md")) == "the durable one\n")
        check("the stranded file is still there, not destroyed",
              os.path.isfile(os.path.join(res, "stain.md")))
        check("and the collision is reported by name",
              out["conflicts"] == ["stain.md"], out)


def test_idea_resources_reads_the_dropzone() -> None:
    section("idea.py resources inventories the drop-zone as a second root")

    with Sandbox() as sb:
        lab = os.path.join(sb.dir, "Jensen - Cryo-EM")
        write(os.path.join(lab, "Resources", "SOPs", "stain.md"),
              "# Negative stain\n")
        os.makedirs(os.path.join(lab, "Ideas"), exist_ok=True)
        durable = lp.durable_resources(create=True)
        write(os.path.join(durable, "my_notes.md"), "# My Own Notes\n")

        res = idea.lab_resources(lab)
        check("the lab's OneDrive Resources/ still resolves exactly as before",
              res.get("resources", "").endswith("Resources"), res.get("resources"))
        check("and its files are still listed",
              "SOPs/stain.md" in (res.get("files") or []), res.get("files"))

        zones = res.get("drop_zones") or []
        check("the drop-zone roots are reported alongside it",
              any(z["source"] == "durable" for z in zones),
              [z.get("source") for z in zones])
        dz = [z for z in zones if z["source"] == "durable"][0]
        check("with the file that was dropped there",
              "my_notes.md" in dz["files"], dz)
        check("and its first heading, the same contract as the lab folder",
              dz["titles"].get("my_notes.md") == "My Own Notes", dz["titles"])
        check("no document body enters the inventory",
              "body" not in json.dumps(dz).lower()
              or "My Own Notes" in json.dumps(dz),
              "names, subfolders and first headings only")


def test_idea_resources_with_neither_present() -> None:
    section("neither root present is not an error")

    with Sandbox() as sb:
        nowhere = os.path.join(sb.dir, "nowhere")
        os.makedirs(nowhere, exist_ok=True)
        res = idea.lab_resources(nowhere)
        check("no lab folder is reported as not-an-error",
              "not an error" in (res.get("note") or ""), res.get("note"))
        zones = res.get("drop_zones") or []
        check("the drop-zone ladder still answers",
              isinstance(zones, list) and bool(zones), zones)
        check("and reports every root as holding nothing",
              all(z["files"] == [] for z in zones),
              [(z["source"], z["files"]) for z in zones])
        rc, out, _ = run("show")
        check("labpack.py show exits 0 with nothing configured", rc == 0, out)


# ---------------------------------------------------------------------------
# Step 5 - the pack ladder
# ---------------------------------------------------------------------------

def test_pack_ladder_order() -> None:
    section("the pack ladder (4.1) - ordered, STOP at the first hit")

    with Sandbox() as sb:
        res = lp.find_pack()
        check("no pack is not an error, not a warning, not a defect",
              res["path"] == "" and res["source"] == "none"
              and not res.get("error"), res)
        check("and it says so plainly rather than silently",
              "no pack" in (res["note"] or "").lower(), res.get("note"))

        # Rung 3: a sibling plugin carrying the marker, found with no setup.
        installed = sb.install_pack()
        res = lp.find_pack()
        check("rung 3 finds an installed pack with no setup at all",
              res["path"] == installed and res["source"] == "plugin", res)
        check("and reports the rung it came from", res["rung"] == 3, res)
        check("the pack's display name comes from the pack, not the engine",
              res["display_name"] == "Jensen Lab", res)

        # Rung 2 outranks it.
        cfg_pack = os.path.join(sb.dir, "configured-pack")
        write(os.path.join(cfg_pack, ".claude-plugin", "plugin.json"),
              json.dumps({"name": "configured-pack", "version": "2026.1.1",
                          "labPack": True}))
        write(os.path.join(cfg_pack, "lab.yml"),
              "pack: configured-pack" + chr(10) +
              'display_name: "Configured"' + chr(10))
        lp.set_config("pack", cfg_pack)
        res = lp.find_pack()
        check("a configured path outranks an installed plugin",
              res["path"] == cfg_pack and res["rung"] == 2, res)

        # Rung 1 outranks both. This is how the suite points at a fixture.
        env_pack = os.path.join(sb.dir, "env-pack")
        write(os.path.join(env_pack, ".claude-plugin", "plugin.json"),
              json.dumps({"name": "env-pack", "version": "2026.2.2",
                          "labPack": True}))
        write(os.path.join(env_pack, "lab.yml"), "pack: env-pack" + chr(10))
        os.environ["PAPER_ENGINE_LAB_PACK"] = env_pack
        res = lp.find_pack()
        check("the environment variable outranks everything",
              res["path"] == env_pack and res["rung"] == 1, res)
        check("and the lower rungs are still reported as candidates",
              len(res["candidates"]) == 3, res["candidates"])
        os.environ.pop("PAPER_ENGINE_LAB_PACK", None)


def test_two_packs_stop_and_ask() -> None:
    section("two packs STOP and ask (4.1) - guessing is worse than a question")

    with Sandbox() as sb:
        a = sb.install_pack(name="lab-a-pack", display="Lab A")
        b = sb.install_pack(name="lab-b-pack", display="Lab B")
        res = lp.find_pack()
        check("nothing resolves", res["path"] == "", res)
        check("the source says AMBIGUOUS rather than none",
              res["source"] == "ambiguous", res)
        check("both packs are named",
              sorted(res["ambiguous"]) == sorted([a, b]), res["ambiguous"])
        check("and the note asks rather than picking",
              "which" in (res["note"] or "").lower()
              and "--path" in (res["note"] or ""),
              res.get("note"))

        # A member in two labs settles it once, per machine.
        lp.set_config("pack", a)
        res = lp.find_pack()
        check("config --path settles it and the ambiguity is gone",
              res["path"] == a and res["source"] == "config", res)


def test_a_plugin_without_the_marker_is_not_a_pack() -> None:
    section("only the marker makes a plugin a pack")

    with Sandbox() as sb:
        sb.install_pack(name="something-else", lab_pack=False)
        res = lp.find_pack()
        check("an installed plugin with no labPack marker is ignored",
              res["path"] == "" and res["source"] == "none", res)
        check("and the engine is silent about it, not warning",
              not res.get("error"), res)


def test_install_index_shapes() -> None:
    section("the measured index shapes, which a guess gets wrong")

    with Sandbox() as sb:
        path = sb.install_pack()
        index = os.path.join(sb.plugins, "installed_plugins.json")

        # Measured: each value is a LIST of install records, because a plugin
        # installed at user AND project scope has two.
        data = json.loads(read(index))
        key = "jensen-resource-pack@jensen-resource-pack-marketplace"
        data["plugins"][key].append(
            {"scope": "project", "projectPath": "C:/somewhere",
             "installPath": path, "version": "2026.9.9"})
        write(index, json.dumps(data, indent=2))
        res = lp.find_pack()
        check("a plugin installed at two scopes is ONE pack, not two",
              res["path"] == path and res["source"] == "plugin", res)

        # Measured: version can be the literal string "unknown".
        data["plugins"][key] = [{"scope": "user", "installPath": path,
                                 "version": "unknown"}]
        write(index, json.dumps(data, indent=2))
        res = lp.find_pack()
        check('a version of "unknown" does not break resolution',
              res["path"] == path, res)

        # A malformed index is not an error either.
        write(index, "{not json")
        res = lp.find_pack()
        check("a malformed index falls through quietly, no traceback",
              res["source"] in ("none", "plugin"), res)


def test_lab_yml_is_the_only_machine_read_file() -> None:
    section("lab.yml (3.1) - and there is no pack_version field in it")

    with Sandbox() as sb:
        path = sb.install_pack()
        lab = lp.read_lab_yml(path)
        check("pack and display_name are read",
              lab["pack"] == "jensen-resource-pack"
              and lab["display_name"] == "Jensen Lab", lab)
        check("curated_on is read - it is a different fact from the version",
              lab["curated_on"] == "2026-09-09", lab)

        info = lp.pack_info(path)
        check("the VERSION comes from plugin.json, never from lab.yml",
              info["version"] == "2026.9.9", info)
        check("and lab.yml carries no pack_version to disagree with it",
              "pack_version" not in read(os.path.join(path, "lab.yml")),
              "two files that must agree is how they come to disagree")

        # A pack with no lab.yml at all is still a pack.
        os.remove(os.path.join(path, "lab.yml"))
        info = lp.pack_info(path)
        check("a pack with no lab.yml still resolves", info["present"], info)
        check("and says the file is missing rather than inventing a lab",
              any("lab.yml" in n for n in info["notes"]), info["notes"])


def test_pack_freshness_is_offline() -> None:
    section("6.2 / item 116 - freshness from the cached remote answer")

    HERE = "73e53311cb26d523da3b285d11ea468e4088f0c3"
    MOVED = "9a94d9b5d4070d8f8a4dc62f22872424e1575e5c"

    with Sandbox() as sb:
        # ITEM 116, and it is first because it is the whole reason this
        # check was rewritten. The install and the marketplace clone agree -
        # they always do, they came down together - while the repository has
        # moved on. The old check read the clone and said `current`.
        sb.install_pack(version="2026.9.9", clone_version="2026.9.9",
                        sha=HERE)
        sb.remote_says(sha=MOVED)
        fresh = lp.freshness()
        check("item 116: install and clone agreeing is NOT current",
              fresh["state"] == "update_available", fresh)
        check("...and the verdict came from the repository, not the clone",
              fresh["remote_sha"] == MOVED and fresh["installed_sha"] == HERE,
              fresh)
        check("the line names both commits, so it can be checked by hand",
              MOVED[:7] in fresh["line"] and HERE[:7] in fresh["line"],
              fresh["line"])
        check("and says nothing is blocked",
              "blocked" in fresh["line"].lower(), fresh["line"])
        check("it REPORTS - it never updates",
              fresh.get("updated") is not True, fresh)
        check("...and it warns that /plugin update may have nothing to do",
              "version bump" in fresh["line"], fresh["line"])

        # The repository has not moved: current, and it says when it last
        # had grounds to believe that.
        sb.remote_says(sha=HERE)
        fresh = lp.freshness()
        check("the same commit at both ends is current",
              fresh["state"] == "current", fresh)
        check("...and the line dates the evidence rather than just asserting",
              "as of the check" in fresh["line"], fresh["line"])
        check("and the line names the curation date, not the release date",
              "2026-09-09" in fresh["line"], fresh["line"])

        # Never asked: UNKNOWN, said out loud, and it names the remedy.
        os.remove(os.path.join(sb.home, "remote-check.json"))
        fresh = lp.freshness()
        check("a repository never asked gives UNKNOWN, not current",
              fresh["state"] == "unknown", fresh)
        check("...and says so in capitals on the line a member reads",
              "UNKNOWN" in fresh["line"], fresh["line"])
        check("...and names the command that would answer it",
              "remote.py check" in fresh["line"], fresh["line"])
        check("and the age is still reported from curated_on",
              "curated" in fresh["line"].lower(), fresh["line"])

        # Asked, and the asking failed. A member whose credential expired
        # must not read that as good news.
        sb.remote_says(sha="", ok=False, error="could not read Username")
        fresh = lp.freshness()
        check("an attempt that got no answer is UNKNOWN, not current",
              fresh["state"] == "unknown", fresh)
        check("...and the line carries the reason it failed",
              "could not read Username" in fresh["line"], fresh["line"])

        # Asked long enough ago that the answer proves nothing. 6.2: "last
        # checked 34 days ago" is a different fact from "you are current".
        sb.remote_says(sha=HERE, days_ago=40)
        fresh = lp.freshness()
        check("a 40-day-old answer is UNKNOWN even when it matched",
              fresh["state"] == "unknown", fresh)
        check("...and the line says how old it is",
              "40 days ago" in fresh["line"], fresh["line"])

        # A fresh-enough one still stands.
        sb.remote_says(sha=HERE, days_ago=2)
        check("a 2-day-old answer is still evidence",
              lp.freshness()["state"] == "current", lp.freshness())

        # The index records no commit: UNKNOWN. Guessing would be worse.
        sb.install_pack(version="2026.9.9", sha="")
        sb.remote_says(sha=MOVED)
        fresh = lp.freshness()
        check("no recorded commit for the install gives UNKNOWN",
              fresh["state"] == "unknown", fresh)

        # 6.4: the ordering is numeric, so 2026.9.9 < 2026.10.1.
        check("2026.9.9 sorts BEFORE 2026.10.1, numerically not as text",
              lp.version_key("2026.9.9") < lp.version_key("2026.10.1"),
              "a text compare puts 2026.9.9 after 2026.10.1")


def test_freshness_reads_and_never_reaches() -> None:
    section("6.2 - the reading half cannot make a network call")

    # Against the SOURCE rather than against a run: a call that only fires on
    # somebody else's machine still fires. The split is the design - `ask`
    # may reach a remote, and nothing `freshness()` calls may.
    src = read(os.path.join(TOOLS, "labpack.py"))
    body = src.split("def freshness(", 1)[-1].split("\ndef main(", 1)[0]
    for reach in ("requests", "urlopen", "urllib", "socket", "subprocess"):
        check(f"labpack.freshness() makes no {reach} call",
              reach not in body,
              "6.2: what must not happen is a network call on the path that "
              "generates an idea")

    rsrc = read(os.path.join(TOOLS, "remote.py"))
    for name in ("cached", "read_cache"):
        half = rsrc.split(f"def {name}(", 1)[-1].split("\ndef ", 1)[0]
        for reach in ("requests", "urlopen", "urllib", "socket",
                      "subprocess"):
            check(f"remote.{name}() makes no {reach} call either",
                  reach not in half,
                  "freshness() calls it, so it is on the drafting path")

    # ...and the asking half really is the one that asks, or the split is
    # decoration and the check has quietly stopped working.
    ask = rsrc.split("def ask(", 1)[-1].split("\ndef ", 1)[0]
    check("remote.ask() is the half that reaches the repository",
          "subprocess.run" in ask and "ls-remote" in ask)
    check("...and it cannot open a login window mid-session",
          "GIT_TERMINAL_PROMPT" in ask and "GCM_INTERACTIVE" in ask,
          "an expired credential must fail quietly, not prompt")
    check("...and it never raises, whatever git does",
          ask.count("except") >= 3)


def test_remote_cache_is_credential_safe() -> None:
    section("2.1 rule 1 - no credential reaches a cache, a log or a screen")

    rem = _load("remote", os.path.join(TOOLS, "remote.py"))
    dirty = ("fatal: could not read from "
             "https://x-access-token:ghp_SECRETVALUE1234@github.com/org/repo")
    clean = rem.scrub(dirty)
    check("a token in git's own error text is scrubbed",
          "ghp_SECRETVALUE1234" not in clean, clean)
    check("...and the rest of the message survives, or it is not a message",
          "could not read from" in clean and "github.com/org/repo" in clean,
          clean)


def test_show_reports_both_ladders() -> None:
    section("labpack.py show - both ladders, one command")

    with Sandbox() as sb:
        sb.install_pack()
        durable = lp.durable_resources(create=True)
        write(os.path.join(durable, "mine.md"), "# Mine" + chr(10))

        rc, out, err = run("show", "--json")
        check("show exits 0", rc == 0, err)
        data = json.loads(out)
        check("it names the pack",
              data["pack"]["display_name"] == "Jensen Lab", data.get("pack"))
        check("and every drop-zone beside it",
              any(z["source"] == "durable" for z in data["drop_zones"]),
              data.get("drop_zones"))

        rc, out, _ = run("show")
        check("the text form names the pack and where it came from",
              "Jensen Lab" in out and "plugin" in out, out)
        check("and names the drop-zone the member should use",
              durable in out, out)


# ---------------------------------------------------------------------------
# Step 6 - scaffold.py prefill merges pack OVER drop-zone
# ---------------------------------------------------------------------------

def _project(sb, name: str = "proj") -> str:
    """A scaffolded project, so prefill has a methods_facts.yml to write to."""
    root = os.path.join(sb.dir, name)
    values = sc.build_values(project_name=name, title="A Fixture Paper",
                             field="chemistry")
    sc.scaffold(root, values)
    return root


def test_prefill_layer_precedence() -> None:
    section("6 - the narrower layer wins, and the wider one is still named")

    with Sandbox() as sb:
        sb.install_pack()
        durable = lp.durable_resources(create=True)
        # The SAME instrument in two layers, disagreeing about one key and
        # naming a key the other does not.
        write(os.path.join(durable, "instruments.md"),
              "# Instruments" + chr(10) + chr(10) +
              "## Fixture Scope (TEM)" + chr(10) + chr(10) +
              "```yaml" + chr(10) +
              "instrument_make: Drop Zone Co" + chr(10) +
              "detector: K3" + chr(10) +
              "```" + chr(10) + chr(10) +
              "## Bench Only (a second instrument)" + chr(10) + chr(10) +
              "```yaml" + chr(10) +
              "bench_model: B-1" + chr(10) +
              "```" + chr(10))

        merged = sc.merged_instrument_blocks()
        names = [b["name"] for b in merged["blocks"]]
        check("an instrument in EITHER layer is offered",
              sorted(names) == ["Bench Only (a second instrument)",
                                "Fixture Scope (TEM)"], names)

        by_name = {b["name"]: b for b in merged["blocks"]}
        scope = by_name["Fixture Scope (TEM)"]
        check("the pack wins the key both layers name",
              scope["keys"]["instrument_make"] == "Fixture Co",
              scope["keys"])
        check("and the key only the drop-zone names still arrives",
              scope["keys"]["detector"] == "K3", scope["keys"])
        check("the layer that LOST is still named, not silently dropped",
              any("drop-zone" in n and "instrument_make" in n
                  for n in merged["notes"]),
              merged["notes"])
        check("a drop-zone-only instrument keeps its own layer",
              by_name["Bench Only (a second instrument)"]["layer"]
              == "drop-zone",
              by_name["Bench Only (a second instrument)"]["layer"])
        check("and a pack instrument is labelled pack",
              scope["layer"] == "pack", scope["layer"])


def test_prefill_marker_names_its_layer() -> None:
    section("6 - the marker comment names the layer each key came from")

    with Sandbox() as sb:
        sb.install_pack()
        proj = _project(sb)
        res = sc.prefill(proj, ["Fixture Scope (TEM)"])
        facts = read(os.path.join(proj, "data", "methods_facts.yml"))

        check("the block was written", res["written"], res)
        check("the marker names the LAYER, not just the filename",
              "prefilled from pack:instruments.md" in facts,
              [ln for ln in facts.splitlines() if "prefilled from" in ln])
        check("and names the pack it came from by display name",
              "Jensen Lab" in facts,
              "a member has to be able to tell which lab's number this is")

        # 4.3, the rule that does not bend: EVERY value arrives commented out.
        check("every key arrives COMMENTED OUT",
              "# instrument_make: Fixture Co" in facts
              and "# accelerating_voltage_kV: 200" in facts, facts[-600:])
        check("and NO key arrives uncommented",
              not re.search(r"^instrument_make:", facts, re.M),
              "a pack is MORE dangerous here, not less - it is more specific "
              "and therefore more plausible")
        check("the block says a commented value counts as MISSING",
              "counts as MISSING" in facts)

        # And the whole downstream chain agrees it is not a value.
        check("scaffold's own reader does not count it",
              "instrument_make" not in sc.confirmed_keys(facts),
              sorted(sc.confirmed_keys(facts)))

        # A re-run must still not write it twice - the marker changed shape.
        again = sc.prefill(proj, ["Fixture Scope (TEM)"])
        check("a re-run with the NEW marker does not write it twice",
              again["already"] == ["Fixture Scope (TEM)"], again)


def test_prefill_reads_the_old_marker_forever() -> None:
    section("6 - the marker that shipped before the layer existed still reads")

    with Sandbox() as sb:
        sb.install_pack()
        proj = _project(sb)
        facts_path = os.path.join(proj, "data", "methods_facts.yml")
        # What every project scaffolded before today has on disk.
        with io.open(facts_path, "a", encoding="utf-8", newline=chr(10)) as fh:
            fh.write(chr(10) + chr(10) +
                     "# --- prefilled from resources/instruments.md "
                     "(Fixture Scope (TEM)), 2026-09-07 ---" + chr(10) +
                     "# instrument_make: Old Co" + chr(10))
        facts = read(facts_path)
        check("the pre-layer marker is still recognised",
              sc.prefilled_blocks(facts) == ["Fixture Scope (TEM)"],
              sc.prefilled_blocks(facts))
        check("so a re-run does not write the block a second time",
              sc.prefill(proj, ["Fixture Scope (TEM)"])["already"]
              == ["Fixture Scope (TEM)"],
              "a migration would have re-prefilled every project on disk")
        check("and manuscript.py still counts its keys as unconfirmed",
              "instrument_make" in ms._unconfirmed_prefill(facts).get(
                  "Fixture Scope (TEM)", []),
              ms._unconfirmed_prefill(facts))


def test_prefill_with_no_pack_is_unchanged() -> None:
    section("6 - with no pack, prefill behaves exactly as it did")

    with Sandbox() as sb:
        durable = lp.durable_resources(create=True)
        write(os.path.join(durable, "instruments.md"),
              "# Instruments" + chr(10) + chr(10) +
              "## Only Here (a scope)" + chr(10) + chr(10) +
              "```yaml" + chr(10) + "instrument_make: Local" + chr(10) +
              "```" + chr(10))
        proj = _project(sb)
        res = sc.prefill(proj, ["Only Here (a scope)"])
        facts = read(os.path.join(proj, "data", "methods_facts.yml"))
        check("the drop-zone alone still prefills", res["written"], res)
        check("and the marker names the drop-zone as its layer",
              "prefilled from drop-zone:instruments.md" in facts,
              [ln for ln in facts.splitlines() if "prefilled from" in ln])

        # And with NEITHER: absent is not an error, which already held and
        # must keep holding.
        os.remove(os.path.join(durable, "instruments.md"))
        proj2 = _project(sb, "proj2")
        res = sc.prefill(proj2, ["Anything At All"])
        check("no pack and no drop-zone writes nothing and refuses nothing",
              res["written"] == [] and res["available"] == [], res)
        check("and says the absence is not a defect",
              any("not a scaffold defect" in n for n in res["notes"]),
              res["notes"])


TWO_SCOPES = ("# Instruments\n\n"
              "## Fixture Scope (TEM)\n\n```yaml\n"
              "instrument_make: Fixture Co\n"
              "instrument_model: Fixture Model One\n"
              "detector: Fixture Camera\n```\n\n"
              "## Second Fixture Scope (SEM)\n\n```yaml\n"
              "instrument_make: Other Fixture Co\n"
              "instrument_model: Fixture Model Two\n```\n")


def test_prefill_confirming_one_key_keeps_the_rest() -> None:
    section("item 160 - confirming one key does not hide the rest of its block")

    with Sandbox() as sb:
        pack = sb.install_pack()
        write(os.path.join(pack, "instruments.md"), TWO_SCOPES)
        proj = _project(sb)
        sc.prefill(proj, ["Fixture Scope (TEM)"])
        path = os.path.join(proj, "data", "methods_facts.yml")
        facts = read(path)
        before = ms._unconfirmed_prefill(facts).get("Fixture Scope (TEM)", [])
        check("three keys are unconfirmed after the prefill",
              sorted(before) == ["detector", "instrument_make",
                                 "instrument_model"], before)

        # Confirming a key IS deleting its '#', which is the line the old
        # reader stopped at.
        write(path, facts.replace("# instrument_make: Fixture Co",
                                  "instrument_make: Fixture Co"))
        after = ms._unconfirmed_prefill(read(path)).get(
            "Fixture Scope (TEM)", [])
        check("confirming the FIRST key leaves the other two unconfirmed",
              sorted(after) == ["detector", "instrument_model"], after)
        check("and the confirmed one is no longer listed",
              "instrument_make" not in after, after)


def test_prefill_key_confirmed_in_another_block() -> None:
    section("item 161 - a key confirmed in one block is that block's alone")

    with Sandbox() as sb:
        pack = sb.install_pack()
        write(os.path.join(pack, "instruments.md"), TWO_SCOPES)
        proj = _project(sb)
        path = os.path.join(proj, "data", "methods_facts.yml")
        sc.prefill(proj, ["Fixture Scope (TEM)"])
        write(path, read(path).replace("# instrument_make: Fixture Co",
                                       "instrument_make: Fixture Co"))

        res = sc.prefill(proj, ["Second Fixture Scope (SEM)"])
        facts = read(path)
        check("the second instrument is written",
              bool(res["written"]) and res["written"][0]["block"]
              == "Second Fixture Scope (SEM)", res)
        check("WITH its own make, not skipped as already recorded",
              "# instrument_make: Other Fixture Co" in facts,
              facts[-700:])
        check("and nothing is reported as left alone",
              res["written"][0]["left_alone"] == [], res["written"])

        # A key the project records OUTSIDE every block still outranks.
        write(path, "instrument_model: Measured Here\n" + read(path))
        proj_res = sc.prefill(proj, ["Second Fixture Scope (SEM)"])
        check("a re-run still does not write the block twice",
              proj_res["already"] == ["Second Fixture Scope (SEM)"], proj_res)
        proj2 = _project(sb, "proj2")
        p2 = os.path.join(proj2, "data", "methods_facts.yml")
        write(p2, "instrument_model: Measured Here\n" + read(p2))
        r2 = sc.prefill(proj2, ["Second Fixture Scope (SEM)"])
        check("a key recorded outside every block is still left alone",
              r2["written"][0]["left_alone"] == ["instrument_model"],
              r2["written"])


SOFTWARE_MD = ("# Software\n\nOne block per program.\n\n"
               "## Fixture Aligner\n\n"
               "Refines an alignment. **It does not make one.**\n\n"
               "```yaml\n"
               "software_name: Fixture Aligner\n"
               "software_step: refine the tilt-series alignment\n"
               "software_citation: \"doi:10.0000/fixture.1 - a fixture\"\n"
               "software_code: https://example.org/fixture-aligner\n"
               "```\n\n"
               "## Fixture Denoiser\n\n"
               "Denoises. **Averaging goes back to the undenoised data.**\n\n"
               "```yaml\n"
               "software_name: Fixture Denoiser\n"
               "software_step: denoise before picking\n"
               "```\n\n"
               "## Fixture Picking\n\n"
               "<!-- not-a-program -->\n"
               "A technique; the package is not named.\n")


def test_prefill_software() -> None:
    section("B - the pack's software, prefilled, commented out")

    with Sandbox() as sb:
        pack = sb.install_pack()
        write(os.path.join(pack, "software.md"), SOFTWARE_MD)
        proj = _project(sb)
        path = os.path.join(proj, "data", "methods_facts.yml")

        listing = sc.prefill(proj, [])
        check("the listing offers the software blocks",
              listing.get("available_software")
              == ["Fixture Aligner", "Fixture Denoiser"], listing)
        check("and keeps them apart from the instruments",
              "Fixture Aligner" not in listing["available"]
              and "Fixture Scope (TEM)" in listing["available"], listing)
        check("a not-a-program block is neither offered nor reported",
              "Fixture Picking" not in (listing.get("available_software")
                                         or [])
              and not any("Fixture Picking" in n for n in listing["notes"]),
              listing)
        check("the listing writes nothing",
              "prefilled from" not in read(path))

        res = sc.prefill(proj, [], software=["Fixture Aligner"])
        facts = read(path)
        check("the program is written", bool(res["written"])
              and res["written"][0]["block"] == "Fixture Aligner", res)
        check("the marker names software.md and its layer",
              "# --- prefilled from pack:software.md (Fixture Aligner)"
              in facts, [ln for ln in facts.splitlines()
                         if "prefilled from" in ln])
        check("every key arrives COMMENTED OUT",
              "# software_name: Fixture Aligner" in facts
              and "# software_citation: doi:10.0000/fixture.1" in facts
              and not re.search(r"^software_name:", facts, re.M),
              facts[-800:])
        check("and the block tells the member to record their version",
              "version" in facts[facts.index("(Fixture Aligner)"):].lower(),
              facts[-800:])
        un = ms._unconfirmed_prefill(facts).get("Fixture Aligner", [])
        check("manuscript.py counts the software keys as unconfirmed",
              sorted(un) == ["software_citation", "software_code",
                             "software_name", "software_step"], un)
        check("and the version line is not mistaken for a key",
              "record" not in un and "version" not in un, un)

        again = sc.prefill(proj, [], software=["Fixture Aligner"])
        check("a re-run does not write it twice",
              again["already"] == ["Fixture Aligner"], again)

        # Every program block names software_name. Item 161 is what makes a
        # second program possible at all once the first is confirmed.
        write(path, read(path).replace("# software_name: Fixture Aligner",
                                       "software_name: Fixture Aligner"))
        second = sc.prefill(proj, [], software=["Fixture Denoiser"])
        check("a second program carries its OWN name after the first is "
              "confirmed", "# software_name: Fixture Denoiser" in read(path)
              and second["written"][0]["left_alone"] == [], second)

        bad = sc.prefill(proj, [], software=["No Such Program"])
        check("an unknown program is reported, not ignored",
              bad["unknown"] == ["No Such Program"], bad)

    check("the two marker readers are the same pattern",
          sc.PREFILL_MARKER_RE.pattern == ms.PREFILL_MARKER_RE.pattern,
          [sc.PREFILL_MARKER_RE.pattern, ms.PREFILL_MARKER_RE.pattern])
    check("and both still read the pre-layer instruments marker",
          bool(ms.PREFILL_MARKER_RE.search(
              "# --- prefilled from resources/instruments.md (X), "
              "2026-09-07 ---")), "")

    with Sandbox() as sb:
        pack = sb.install_pack()
        write(os.path.join(pack, "software.md"), SOFTWARE_MD)
        proj = _project(sb)
        p = subprocess.run([sys.executable, os.path.join(TOOLS, "scaffold.py"),
                            "prefill", proj, "--software", "Fixture Denoiser",
                            "--json"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        try:
            payload = json.loads(p.stdout)
        except ValueError:
            payload = {}
        check("the CLI takes --software",
              bool(p.returncode == 0 and payload.get("written")
                   and payload["written"][0]["block"] == "Fixture Denoiser"),
              p.stdout[-600:] + p.stderr[-600:])

    with Sandbox() as sb:
        sb.install_pack()
        proj = _project(sb)
        res = sc.prefill(proj, [])
        check("a pack with no software.md lists no software, silently",
              res.get("available_software") == []
              and not any("software" in n for n in res["notes"]), res)


# ---------------------------------------------------------------------------
# Step 8 - the live inventory read (5)
# ---------------------------------------------------------------------------
#
# Nothing here touches Box. 5.3: the connector path has no test and is
# documented as having none, which is 0's consequence stated where somebody
# will meet it.

def _pack_with_inventory(sb, csv_name: str = "inv.csv", body: str = "",
                         extra: str = "") -> tuple:
    """A pack whose lab.yml names a local fallback copy (5.2 rung 1)."""
    inv = os.path.join(sb.dir, csv_name)
    if body:
        write(inv, body)
    lab = ("pack: fixture-pack" + chr(10) +
           'display_name: "Fixture Lab"' + chr(10) +
           'curated_on: "2026-09-09"' + chr(10) +
           "live:" + chr(10) +
           "  inventory:" + chr(10) +
           "    where: box" + chr(10) +
           '    file_id: "123"' + chr(10) +
           '    name: "The Inventory"' + chr(10) +
           '    owner: "lab manager"' + chr(10) +
           extra +
           '    fallback_path: "' + inv.replace(chr(92), "/") + '"' + chr(10))
    path = sb.install_pack(name="fixture-pack", display="Fixture Lab",
                           lab_yml=lab)
    return path, inv


def test_inventory_from_fallback_path() -> None:
    section("5.2 rung 1 - a synced local copy, when a member has one")

    with Sandbox() as sb:
        _pack_with_inventory(sb, body=(
            "name,location_in_E130,company,cat,comments" + chr(10) +
            "Quantifoil R2/2 grids,Shelf B3,EMS,Q225,for screening" + chr(10) +
            "Uranyl acetate,Cabinet 4,SPI,02624,LIGHT SENSITIVE" + chr(10)))

        res = lp.inventory()
        check("the local copy is read", res["state"] == "read", res)
        check("and the route says which rung answered",
              res["route"] == "fallback_path", res)
        check("every row is available to the conversation",
              res["rows"] == 2, res)

        hit = lp.inventory_lookup("quantifoil", res)
        check("a query matches a row on its name",
              hit["matches"] and hit["matches"][0]["name"]
              == "Quantifoil R2/2 grids", hit)
        check("and the row carries where it is, which is what the file knows",
              hit["matches"][0]["fields"].get("location_in_E130") == "Shelf B3",
              hit["matches"][0])

        # 5.1 rule 4: every quoted number carries its read time.
        check("the read carries a timestamp",
              res["read_at"] and len(res["read_at"]) >= 10, res.get("read_at"))
        check("and the phrase a skill quotes says WHEN it was read",
              "as of" in res["attribution"].lower()
              and res["read_at"][:10] in res["attribution"],
              res.get("attribution"))


def test_unknown_is_never_out_of_stock() -> None:
    section("5.1 rule 3 - missing, malformed and empty all give UNKNOWN")

    with Sandbox() as sb:
        # 1. The file lab.yml names is not on this machine.
        _pack_with_inventory(sb, body="")
        res = lp.inventory()
        check("a missing local copy is UNKNOWN, not empty",
              res["state"] == "unknown", res)
        check("and it names WHICH route was missing",
              "connector" in res["why"].lower()
              or "fallback" in res["why"].lower(), res["why"])
        # A naive "the words 'out of stock' never appear" assertion fires on
        # the engine EXPLICITLY REFUSING that inference, which is the thing
        # the rule wants. Assert the refusal instead.
        check("and refuses the out-of-stock inference in so many words",
              "not the same as out of stock" in res["why"].lower(),
              res["why"])
        check("no count and no rows are reported either",
              res["rows"] == 0 and res["records"] == [], res)

    with Sandbox() as sb:
        # 2. Malformed.
        _pack_with_inventory(sb, body="\x00\x01 not a table at all")
        res = lp.inventory()
        check("a malformed file is UNKNOWN, not empty",
              res["state"] == "unknown", res)
        check("and says so rather than raising",
              bool(res["why"]), res)

    with Sandbox() as sb:
        # 3. A real file with a header and no rows.
        _pack_with_inventory(sb, body="name,location_in_E130,comments" + chr(10))
        res = lp.inventory()
        check("a header with no rows is UNKNOWN, not 'you have none'",
              res["state"] == "unknown", res)
        check("and the reason distinguishes empty from unreadable",
              "no rows" in res["why"].lower(), res["why"])

    with Sandbox() as sb:
        # 4. No pack at all, so no lab.yml, so no inventory is even named.
        res = lp.inventory()
        check("no pack gives UNKNOWN and no error",
              res["state"] == "unknown" and not res.get("error"), res)
        check("and says a pack is what names an inventory",
              "pack" in res["why"].lower(), res["why"])

        # The rule stated once, for every one of those four.
        for state in ("unknown",):
            check(f"a {state} read never downgrades a candidate",
                  lp.inventory_verdict({"state": state}) == "not-infeasible",
                  "unknown is a named load-bearing unknown, never a no")


def test_inventory_is_offered_never_automatic() -> None:
    section("5.1 rule 1 and 5 - offered, and never cached past the run")

    with Sandbox() as sb:
        _pack_with_inventory(sb, body="name,comments" + chr(10) +
                             "Grids,plenty" + chr(10))

        check("the engine states the offer rather than performing it",
              "want me to check" in lp.inventory_offer().lower(),
              lp.inventory_offer())
        check("and the offer says nothing it returns is written anywhere",
              "written" in lp.inventory_offer().lower(),
              lp.inventory_offer())

        # 5.1 rule 5: at most one read per run, held in memory only.
        first = lp.inventory()
        second = lp.inventory()
        check("a second call in the same run reuses the first read",
              second["read_at"] == first["read_at"], (first, second))
        check("and says it was the cached one",
              second.get("cached") is True, second)
        lp.reset_inventory_cache()
        third = lp.inventory()
        check("resetting the run clears it - no cache outlives the session",
              third.get("cached") is not True, third)

        # 5.1 rule 2: it answers a conversation, never a file.
        files_before = sorted(os.listdir(sb.dir))
        lp.inventory()
        check("reading the inventory writes no file anywhere",
              sorted(os.listdir(sb.dir)) == files_before)
        check("and the engine has no writer for it at all",
              not hasattr(lp, "write_inventory"),
              "5.1 rule 6: no skill writes to the Box. Ever")


def test_inventory_answers_what_the_file_can_answer() -> None:
    section("5 - a file with no quantity column never implies a quantity")

    with Sandbox() as sb:
        # Measured on the real pack 2026-09-17: the lab's inventory has NO
        # on-hand column. It answers "do we own one, and where is it".
        _pack_with_inventory(
            sb,
            extra="    has_on_hand_column: false" + chr(10),
            body="name,location_in_E130,comments" + chr(10) +
                 "Uranyl acetate,Cabinet 4,light sensitive" + chr(10))
        res = lp.inventory()
        check("the read succeeds", res["state"] == "read", res)
        check("and declares that it cannot answer HOW MANY",
              res["answers"] == "owned-and-where", res)
        check("the caveat says so in words a skill can quote",
              "how many" in res["caveat"].lower()
              and "where" in res["caveat"].lower(), res.get("caveat"))

        hit = lp.inventory_lookup("uranyl", res)
        check("a hit reports owned and where, with no count",
              hit["matches"][0]["on_hand"] is None, hit["matches"][0])
        check("and the phrasing offered to the skill says OWNED, not IN STOCK",
              "own" in hit["phrase"].lower()
              and "in stock" not in hit["phrase"].lower(), hit["phrase"])

        miss = lp.inventory_lookup("a thing nobody has", res)
        check("a MISS is unknown, not 'we do not have it'",
              miss["state"] == "unknown", miss)
        check("because a row absent from an ownership list is not a zero",
              "not listed" in miss["phrase"].lower()
              and "do not have" not in miss["phrase"].lower(), miss["phrase"])


def test_inventory_with_an_on_hand_column() -> None:
    section("5.1 rule 4 - a real count is quoted WITH its read time")

    with Sandbox() as sb:
        _pack_with_inventory(
            sb,
            extra="    has_on_hand_column: true" + chr(10),
            body="item,on_hand,unit,updated" + chr(10) +
                 "Quantifoil grids,12,box,2026-09-01" + chr(10))
        res = lp.inventory()
        check("the count column is found", res["answers"] == "on-hand", res)
        hit = lp.inventory_lookup("quantifoil", res)
        check("the count is read", hit["matches"][0]["on_hand"] == "12", hit)
        check("and never quoted bare - the read time travels with it",
              "as of" in hit["phrase"].lower(), hit["phrase"])
        check("the row's own updated date travels too, when it has one",
              "2026-09-01" in hit["phrase"], hit["phrase"])


def _make_xlsx(path: str, header: list, rows: list) -> str:
    """A minimal .xlsx, written with the standard library.

    Real spreadsheets store text in a shared-string table and reference it by
    index, so that is what this writes - reading the index correctly is the
    half of `_read_xlsx` that a test with inline strings would never touch.
    The one file the lab's pack actually names is a spreadsheet, so a reader
    with no test behind it is a reader that answers a feasibility question
    wrongly the first time somebody syncs Box.
    """
    strings: list = []

    def sid(val: str) -> int:
        if val not in strings:
            strings.append(val)
        return strings.index(val)

    grid = [[sid(str(c)) for c in header]]
    for row in rows:
        grid.append([sid(str(c)) for c in row])

    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    sheet = [f"<worksheet {ns}><sheetData>"]
    for r, cells in enumerate(grid, start=1):
        sheet.append(f'<row r="{r}">')
        for c, idx in enumerate(cells):
            ref = chr(ord("A") + c) + str(r)
            sheet.append(f'<c r="{ref}" t="s"><v>{idx}</v></c>')
        sheet.append("</row>")
    sheet.append("</sheetData></worksheet>")

    sst = [f'<sst {ns} count="{len(strings)}" '
           f'uniqueCount="{len(strings)}">']
    for val in strings:
        safe = (val.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))
        sst.append(f"<si><t>{safe}</t></si>")
    sst.append("</sst>")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml",
                    '<?xml version="1.0"?><Types xmlns="http://schemas.'
                    'openxmlformats.org/package/2006/content-types"/>')
        zf.writestr("xl/sharedStrings.xml", "".join(sst))
        zf.writestr("xl/worksheets/sheet1.xml", "".join(sheet))
    return path


def test_inventory_reads_a_real_spreadsheet() -> None:
    section("5.2 rung 1 - the local copy is a SPREADSHEET, and it is read")

    with Sandbox() as sb:
        inv = os.path.join(sb.dir, "JensenLab_inventory.xlsx")
        _make_xlsx(inv,
                   ["name", "location_in_E130", "company", "comments"],
                   [["Quantifoil R2/2 grids", "Shelf B3", "EMS",
                     "for screening"],
                    ["Uranyl acetate", "Cabinet 4", "SPI",
                     "light sensitive"]])
        lab = ("pack: fixture-pack" + chr(10) +
               'display_name: "Fixture Lab"' + chr(10) +
               'curated_on: "2026-09-09"' + chr(10) +
               "live:" + chr(10) +
               "  inventory:" + chr(10) +
               "    where: box" + chr(10) +
               '    name: "JensenLab_inventory.xlsx"' + chr(10) +
               "    has_on_hand_column: false" + chr(10) +
               '    owner: "lab manager"' + chr(10) +
               '    fallback_path: "' + inv.replace(chr(92), "/") + '"'
               + chr(10))
        sb.install_pack(name="fixture-pack", display="Fixture Lab",
                        lab_yml=lab)

        res = lp.inventory()
        check("an .xlsx local copy is read, with no third-party package",
              res["state"] == "read", res.get("why"))
        check("the header row becomes the columns",
              res["header"] == ["name", "location_in_E130", "company",
                                "comments"], res["header"])
        check("both rows are read", res["rows"] == 2, res)
        check("and the shared-string table is resolved, not left as indexes",
              res["records"][0]["name"] == "Quantifoil R2/2 grids",
              res["records"][0])

        hit = lp.inventory_lookup("uranyl", res)
        check("a lookup finds the row", len(hit["matches"]) == 1, hit)
        check("and reports where it is kept, which is what this file knows",
              "Cabinet 4" in hit["phrase"], hit["phrase"])
        check("with no quantity, because the file carries none",
              hit["matches"][0]["on_hand"] is None, hit["matches"][0])

        # A file that is not a workbook at all must be UNKNOWN, not empty.
        write(inv, "this is not a zip")
        lp.reset_inventory_cache()
        res = lp.inventory()
        check("a corrupt workbook is UNKNOWN and does not raise",
              res["state"] == "unknown", res)
        check("and names the file it could not read",
              "could not be read" in res["why"], res["why"])


def test_inventory_from_connector_file() -> None:
    section("5.2 rung 2 - a copy a skill just pulled through the connector")

    with Sandbox() as sb:
        # No fallback_path at all - the ordinary case per 5.2, since the lab
        # does not sync. `--connector-file` is how a skill hands the engine
        # the copy it just saved, without touching lab.yml.
        lab = ("pack: fixture-pack" + chr(10) +
               'display_name: "Fixture Lab"' + chr(10) +
               'curated_on: "2026-09-09"' + chr(10) +
               "live:" + chr(10) +
               "  inventory:" + chr(10) +
               "    where: box" + chr(10) +
               '    file_id: "123"' + chr(10) +
               '    name: "The Inventory"' + chr(10) +
               '    owner: "lab manager"' + chr(10))
        sb.install_pack(name="fixture-pack", display="Fixture Lab",
                        lab_yml=lab)

        pulled = os.path.join(sb.dir, "connector_pull.csv")
        write(pulled, "name,location_in_E130,comments" + chr(10) +
              "Quantifoil R2/2 grids,Shelf B3,for screening" + chr(10))

        res = lp.inventory(connector_path=pulled)
        check("a connector-pulled copy is read", res["state"] == "read",
              res.get("why"))
        check("and the route names rung 2, not rung 1",
              res["route"] == "connector", res)
        check("the row is there", res["rows"] == 1, res)
        check("the attribution says a CONNECTOR returned it, not a synced "
              "copy",
              "connector returned" in res["attribution"].lower(), res)
        check("...and does not claim a local copy - that implies an ongoing "
              "sync this is not",
              "from a local copy" not in res["attribution"], res)

        # A lookup and its phrase work identically to rung 1 - same reader,
        # same rules, only the label differs.
        hit = lp.inventory_lookup("quantifoil", res)
        check("a lookup finds the row exactly as it would from a fallback",
              hit["matches"] and hit["matches"][0]["name"]
              == "Quantifoil R2/2 grids", hit)

        # RULE 6, extended: the engine never writes to a connector copy
        # either - it is read the same way a synced file is, never patched,
        # never "corrected".
        before = read(pulled)
        lp.inventory(connector_path=pulled, force=True)
        check("the connector copy is untouched by the read",
              read(pulled) == before, before)

    with Sandbox() as sb:
        # fallback_path, when it resolves, still wins over a connector copy -
        # the cheap, ordinary-file rung answers first (5.2's own order).
        _path, _inv_fallback = _pack_with_inventory(
            sb, body="name,comments" + chr(10)
                     + "From The Synced Copy,x" + chr(10))
        pulled = os.path.join(sb.dir, "connector_pull.csv")
        write(pulled, "name,comments" + chr(10)
              + "From The Connector,x" + chr(10))
        res = lp.inventory(connector_path=pulled)
        check("fallback_path answers first when both are present",
              res["route"] == "fallback_path", res)
        check("...and it is the synced row that comes back",
              res["records"][0]["name"] == "From The Synced Copy", res)

    with Sandbox() as sb:
        # Neither rung resolves - a connector path that names a file NOT on
        # this machine is exactly the fallback case: named, not guessed at.
        _pack_with_inventory(sb, body="")
        res = lp.inventory(connector_path=os.path.join(sb.dir, "absent.csv"))
        check("a connector path that does not exist falls through to the "
              "same named-gap answer as a missing fallback",
              res["state"] == "unknown" and res["route"] == "connector", res)
        check("still refuses the out-of-stock inference",
              "not the same as out of stock" in res["why"].lower(), res["why"])

    with Sandbox() as sb:
        # The CLI flag, end to end - `--connector-file` on a real subprocess.
        sb.install_pack(name="fixture-pack", display="Fixture Lab",
                        lab_yml=(
                            "pack: fixture-pack" + chr(10) +
                            'display_name: "Fixture Lab"' + chr(10) +
                            'curated_on: "2026-09-09"' + chr(10) +
                            "live:" + chr(10) + "  inventory:" + chr(10) +
                            "    where: box" + chr(10) +
                            '    name: "The Inventory"' + chr(10)))
        pulled = os.path.join(sb.dir, "connector_pull.csv")
        write(pulled, "name,comments" + chr(10) + "CLI Row,x" + chr(10))
        code, out, err = run("inventory", "--connector-file", pulled,
                             "--json")
        check("the CLI flag runs clean", code == 0, err[-400:])
        cli_res = json.loads(out)
        check("...and reads the connector copy",
              cli_res["state"] == "read" and cli_res["route"] == "connector",
              cli_res)
        check("...with the row from the pulled file",
              cli_res["records"][0]["name"] == "CLI Row", cli_res)


def test_a_prose_only_layer_says_so_once() -> None:
    section("6 - a layer with no yaml halves is ONE fact, not eleven")

    with Sandbox():
        # Measured on the real pack 2026-09-17: eleven `##` blocks, not one
        # with a machine-readable half, so prefill printed eleven identical
        # notes and offered nothing. Eleven lines saying the same thing is a
        # check people learn to scroll past, which is how a real one gets
        # missed.
        blocks = "".join(
            "## Instrument " + str(n) + chr(10) + chr(10) +
            "- **What it is for.** Prose only." + chr(10) + chr(10)
            for n in range(1, 12))
        durable = lp.durable_resources(create=True)
        write(os.path.join(durable, "instruments.md"),
              "# Instruments" + chr(10) + chr(10) + blocks)

        merged = sc.merged_instrument_blocks()
        no_keys = [n for n in merged["notes"] if "no keys" in n]
        check("eleven prose-only blocks produce ONE note, not eleven",
              len(no_keys) == 1, no_keys)
        check("and the note says how many there were",
              "11" in no_keys[0], no_keys[0])
        check("and names some of them, so it is still actionable",
              "Instrument 1" in no_keys[0], no_keys[0])
        check("and says what to do about it",
              "yaml" in no_keys[0], no_keys[0])

        # A layer where only ONE block is prose-only still names that block,
        # because then the note IS the actionable thing.
        write(os.path.join(durable, "instruments.md"),
              "# Instruments" + chr(10) + chr(10) +
              "## Has Keys" + chr(10) + chr(10) +
              "```yaml" + chr(10) + "instrument_make: Co" + chr(10) +
              "```" + chr(10) + chr(10) +
              "## Prose Only" + chr(10) + chr(10) + "- nothing" + chr(10))
        merged = sc.merged_instrument_blocks()
        no_keys = [n for n in merged["notes"] if "no keys" in n]
        check("one prose-only block beside a real one is still named",
              len(no_keys) == 1 and "Prose Only" in no_keys[0], no_keys)
        check("and the block with keys is still offered",
              [b["name"] for b in merged["blocks"]] == ["Has Keys"],
              merged["blocks"])


# ---------------------------------------------------------------------------
# The brief - specs/lab-pack-brief-2026-09-24.md
#
# Four of these fail quietly if they regress, and they are the reason this
# block is longer than the feature:
#
#   - a pack number is PARAPHRASED rather than quoted. The brief then carries
#     a number that can drift from the pack without either copy being wrong on
#     its face, which is the failure the pack itself is built to avoid.
#   - a step the pack carries is MISSING from the brief. The ask was every
#     step, because the question that arrives at the bench three weeks later
#     is about the step nobody discussed.
#   - a member's own note is overwritten by a regeneration.
#   - a note attached to a step the pack has since renamed is silently
#     dropped, which is a member's decision disappearing between two pack
#     versions.
# ---------------------------------------------------------------------------

BRIEF_SOPS = """# SOPs and Protocols

An index, not the procedures. Bodies stay in the source.

Curated 2026-09-09.

---

## Culture Growth for the Fixture Route

`Lab_protocols/growth.docx` - 2026-08-12 - [Box](https://example.invalid/1)

**Numbers that set the schedule.** Colony to usable culture is **2-3 days at
30 C** to reach OD600 1-2.

**What it rules out.** Once concentrated, grids must be frozen within
**30 minutes** or the cells shed their flagella.

## Grid Vitrification on the Fixture Plunger

`Lab_general/SOPs/SOP_Plunger.docx` - v1.0, effective 2026-07-02

The SOP carries no default blot parameters on purpose - blot time, blot force
and humidity are chosen per sample and recorded per grid.

## Glow Discharge on the Quorum Fixture

`Lab_protocols/Q_GlowDischarge_protocol.docx` - 2026-08-09

**A glow-discharged grid is good for about 30 minutes.**



And this paragraph sits after a deliberate run of blank lines, because a body
is quoted as it is written and the frame is tidied around it.

## Index Only - Governs a Technique, Carries No Planning Number

Two SOPs govern techniques and carry no number a plan would quote.
"""

BRIEF_INSTRUMENTS = """# Instruments

## Fixture Scope (TEM)

- **What it is for.** A fixture.

```yaml
instrument_make: Fixture Co
instrument_model: Fixture Model One
accelerating_voltage_kV: 200
```

## Fixture Plunger (BYU)

Plunge freezer. Chamber holds 4 C to 25 C.

## Quorum Fixture Glow Discharger (BYU)

25 mA, 30 s, air.

## Software the Lab Actually Runs

A list, not an instrument.

## Shared and Borrowed Equipment

Also a list, and also not an instrument.
"""

BRIEF_LOCATIONS = """# Where Things Are

## How to Read a Location Code

Bench, shelf, box.

## Bench by Bench

A list.
"""


BRIEF_LAB_YML = """pack: jensen-resource-pack
display_name: "Jensen Lab"
curated_from: "Box, folder 000"
curated_on: "2026-09-09"
curated_by: "someone@example.edu"

live:
  inventory:
    where: box
    file_id: "1"
    name: "fixture_inventory.xlsx"
    sections:
      equipment: [name, location, company]
    has_on_hand_column: false
    owner: "lab manager"
    fallback_path: ""
"""


def _brief_pack(sb, **kw) -> str:
    """The fixture pack with enough content for a route to exist in it."""
    kw.setdefault("lab_yml", BRIEF_LAB_YML)
    path = sb.install_pack(**kw)
    write(os.path.join(path, "sops.md"), BRIEF_SOPS)
    write(os.path.join(path, "instruments.md"), BRIEF_INSTRUMENTS)
    write(os.path.join(path, "locations.md"), BRIEF_LOCATIONS)
    return path


BRIEF_BUNDLE = {
    "claim": "The fixture route resolves the thing nobody has resolved",
    "techniques": ["cryo-ET of intact cells"],
    "data_plan": "12 grids, two blot forces, one control",
    "steps": [
        {"step": "Grid Vitrification on the Fixture Plunger",
         "note": "12 grids a session, two blot forces",
         "decided": ["Blot force 6, 4 s, from the growth protocol"],
         "unknowns": ["Whether humidity holds at 100% with the door cycling"]},
    ],
}


def test_brief_section_splitter() -> None:
    section("brief - the section splitter quotes, it does not restate")

    secs = lp.split_sections(BRIEF_SOPS)
    heads = [s["heading"] for s in secs]
    check("every '## ' heading becomes a section, in file order",
          heads == ["Culture Growth for the Fixture Route",
                    "Grid Vitrification on the Fixture Plunger",
                    "Glow Discharge on the Quorum Fixture",
                    "Index Only - Governs a Technique, Carries No Planning "
                    "Number"], heads)
    body = secs[0]["body"]
    check("the body is VERBATIM, bold markers and all",
          "**2-3 days at" in body and "**30 minutes**" in body, body)
    check("and it stops at the next heading",
          "Grid Vitrification" not in body, body)
    check("the preamble above the first heading is not a section",
          all("An index, not the procedures" not in s["body"] for s in secs),
          secs[0])
    check("a document with no '## ' heading yields no sections",
          lp.split_sections("# Title\n\nJust prose.\n") == [],
          lp.split_sections("# Title\n\nJust prose.\n"))


def test_brief_has_every_step() -> None:
    section("brief - EVERY step, not the ones that came up")

    with Sandbox() as sb:
        _brief_pack(sb)
        proj = _project(sb)
        res = lp.brief(proj, bundle=BRIEF_BUNDLE)
        check("the write succeeded", res["state"] == "written", res)
        text = read(res["path"])

        for i, name in enumerate(
                ["Culture Growth for the Fixture Route",
                 "Grid Vitrification on the Fixture Plunger",
                 "Glow Discharge on the Quorum Fixture",
                 "Index Only - Governs a Technique, Carries No Planning "
                 "Number"], start=1):
            check(f"step {i} is in the brief: {name[:38]}",
                  f"### {i}. {name}" in text, text[:400])

        check("the step count is reported",
              res["steps"] == 4, res)
        check("and a step nobody discussed says so rather than being empty",
              "Nothing project-specific is recorded for this step yet"
              in text, text)


def test_brief_quotes_the_pack_verbatim() -> None:
    section("brief - quoted, dated, and never paraphrased")

    with Sandbox() as sb:
        _brief_pack(sb)
        proj = _project(sb)
        text = lp.brief(proj)["text"]

        check("a pack number arrives byte for byte",
              "Colony to usable culture is **2-3 days at\n30 C** to reach "
              "OD600 1-2." in text, text)
        check("and nothing of the renderer's own leaks into the file",
              chr(0) not in text, repr(text[:200]))
        check("every quoted block names its file and its curation date",
              text.count("sops.md`, curated 2026-09-09") >= 4, text)
        # The frame is tidied; a BODY is not. A blank run inside a quoted
        # section is how that file is written and is not the renderer's to
        # decide about.
        check("a blank-line run inside a quoted body survives",
              "about 30 minutes.**\n\n\n\nAnd this paragraph" in text,
              text[text.index("### 3."):][:600])
        check("the header says nothing here is a methods fact",
              "nothing here is a methods fact" in text.lower(), text[:2000])
        check("and it names the pack, its version and its curation date",
              "Jensen Lab" in text and "2026.9.9" in text
              and "2026-09-09" in text, text[:2000])


def test_brief_project_half_comes_first() -> None:
    section("brief - the project's half is first on every step")

    with Sandbox() as sb:
        _brief_pack(sb)
        proj = _project(sb)
        text = lp.brief(proj, bundle=BRIEF_BUNDLE)["text"]

        start = text.index("### 2. Grid Vitrification")
        block = text[start:text.index("### 3.")]
        check("the note is in the step's block",
              "12 grids a session, two blot forces" in block, block)
        check("a decision is carried",
              "Blot force 6, 4 s" in block, block)
        check("an unknown is carried, and named as still open",
              "Still open" in block and "door cycling" in block, block)
        check("and all of it comes BEFORE the pack's half",
              block.index("12 grids a session")
              < block.index("What the pack says"), block)
        check("the header carries the project's claim",
              "resolves the thing nobody has resolved" in text, text[:2500])


def test_brief_refuses_a_step_the_pack_does_not_have() -> None:
    section("brief - a note on a step that does not exist REFUSES")

    with Sandbox() as sb:
        _brief_pack(sb)
        proj = _project(sb)
        bad = {"steps": [{"step": "A Step This Pack Does Not Carry",
                          "note": "a decision about a step that is gone"}]}
        res = lp.brief(proj, bundle=bad)

        check("the write is refused", res["state"] == "refused", res)
        check("the step is named in the error",
              any("A Step This Pack Does Not Carry" in e
                  for e in res["errors"]), res["errors"])
        check("and the headings that DO exist are listed",
              any("Glow Discharge on the Quorum Fixture" in e
                  for e in res["errors"]), res["errors"])
        check("nothing was written",
              not os.path.exists(os.path.join(proj, "plan",
                                              "lab_pack_brief.md")),
              os.listdir(os.path.join(proj, "plan")))

        dupe = {"steps": [{"step": "Glow Discharge on the Quorum Fixture"},
                          {"step": "glow discharge on the QUORUM fixture"}]}
        res2 = lp.brief(proj, bundle=dupe)
        check("two notes on one step is also a refusal",
              res2["state"] == "refused", res2)

        ok = {"steps": [{"step": "  glow discharge on the quorum fixture ",
                         "note": "normalised match is allowed"}]}
        res3 = lp.brief(proj, bundle=ok)
        check("case, padding and punctuation still match",
              res3["state"] == "written"
              and "normalised match is allowed" in res3["text"], res3)


def test_brief_is_never_written_under_data() -> None:
    section("brief - the one destination it may never reach (0.1)")

    with Sandbox() as sb:
        _brief_pack(sb)
        proj = _project(sb)
        res = lp.brief(proj, out=os.path.join(proj, "data", "pack.md"))
        check("a destination under data/ is refused",
              res["state"] == "refused", res)
        check("and the refusal says why, naming methods facts",
              any("methods" in e.lower() for e in res["errors"]),
              res["errors"])
        check("nothing was written there",
              not os.path.exists(os.path.join(proj, "data", "pack.md")))

        default = lp.brief(proj)
        check("the default destination is plan/lab_pack_brief.md",
              default["path"].replace(os.sep, "/").endswith(
                  "plan/lab_pack_brief.md"), default["path"])

        flat = os.path.join(sb.dir, "explore")
        os.makedirs(flat, exist_ok=True)
        res2 = lp.brief(flat)
        check("a folder with no plan/ takes the file at its root",
              res2["path"].replace(os.sep, "/").endswith(
                  "explore/lab_pack_brief.md"), res2["path"])


def test_brief_carries_your_notes_forward() -> None:
    section("brief - regeneration never eats what a member wrote")

    with Sandbox() as sb:
        pack = _brief_pack(sb)
        proj = _project(sb)
        first = lp.brief(proj, bundle=BRIEF_BUNDLE)
        path = first["path"]
        check("the notes section exists to be written in",
              "## Your Notes" in read(path), read(path)[-800:])

        text = read(path)
        write(path, text.rstrip() + "\n\n- 2026-09-25: the plunger chamber "
                                    "reads 2 C low, ask the manager.\n")

        # The pack moves under them, exactly as a /plugin update would.
        write(os.path.join(pack, "sops.md"),
              BRIEF_SOPS.replace("2-3 days at", "3-4 days at"))
        second = lp.brief(proj, bundle=BRIEF_BUNDLE)
        again = read(second["path"])

        check("the member's line survives regeneration",
              "the plunger chamber reads 2 C low" in again, again[-800:])
        check("and the pack half is re-read rather than kept",
              "3-4 days at" in again and "2-3 days at" not in again, again)
        check("the carry-forward is reported rather than silent",
              second.get("carried_notes") is True, second)
        # As a HEADING. The header paragraph names the section in a sentence
        # too, and that inline mention is what made the first implementation
        # carry the whole document forward as "notes".
        heads = re.findall(r"^## Your Notes\s*$", again, re.MULTILINE)
        check("and the notes heading is not duplicated",
              len(heads) == 1, len(heads))


def test_brief_never_overwrites_a_foreign_file() -> None:
    section("brief - a file this command did not write is not its file")

    with Sandbox() as sb:
        _brief_pack(sb)
        proj = _project(sb)
        dest = os.path.join(proj, "plan", "lab_pack_brief.md")
        write(dest, "# My own notes about the lab\n\nHand written.\n")

        res = lp.brief(proj)
        check("it refuses", res["state"] == "refused", res)
        check("the file is untouched",
              "Hand written." in read(dest), read(dest))
        check("and the refusal names the way through",
              any("--force" in e for e in res["errors"]), res["errors"])

        forced = lp.brief(proj, force=True)
        check("--force writes", forced["state"] == "written", forced)


def test_brief_instruments_attach_by_name() -> None:
    section("brief - instruments attach by distinctive token, and a miss "
            "is silent")

    with Sandbox() as sb:
        _brief_pack(sb)
        proj = _project(sb)
        res = lp.brief(proj)
        text = res["text"]

        check("an instrument the route names is attached to its step",
              "Fixture Plunger (BYU)" in text, text)
        check("and is quoted once, under its own section",
              text.count("Plunge freezer. Chamber holds") == 1, text)
        check("an instrument no step names is NOT re-printed",
              "accelerating_voltage_kV" not in text, text)
        check("a heading with no distinctive token never matches",
              "A list, not an instrument." not in text
              and "Also a list, and also not an instrument." not in text,
              text)
        check("the glow discharger matched on its own token",
              "Quorum Fixture Glow Discharger (BYU)" in res["instruments"],
              res["instruments"])


def test_brief_indexes_the_rest_of_the_pack() -> None:
    section("brief - what it does NOT quote, it points at")

    with Sandbox() as sb:
        _brief_pack(sb)
        proj = _project(sb)
        text = lp.brief(proj)["text"]

        check("a pack file that is not the route is indexed by name",
              "locations.md" in text, text)
        check("its headings are listed",
              "How to Read a Location Code" in text, text)
        check("and its body is NOT copied",
              "Bench, shelf, box." not in text, text)
        check("the live-inventory rule is stated, not the stock",
              "owned-and-where" in text or "do we own one" in text, text)
        # An instrument no step names is still in this lab. Left out of both
        # halves it reads as a lab that does not own one.
        rest = text[text.index("## The Rest of the Pack"):]
        check("an instrument the route never names is still indexed",
              "Fixture Scope (TEM)" in rest, rest)
        check("and the ones quoted above are not listed twice",
              "Fixture Plunger (BYU)" not in rest, rest)


def test_brief_with_no_pack_writes_nothing() -> None:
    section("brief - no pack is not an error, and writes no file")

    with Sandbox() as sb:
        proj = _project(sb)
        res = lp.brief(proj)
        check("the state says so plainly", res["state"] == "no-pack", res)
        check("no file is written",
              not os.path.exists(os.path.join(proj, "plan",
                                              "lab_pack_brief.md")),
              os.listdir(os.path.join(proj, "plan")))
        check("and it is not an error",
              not res["errors"], res["errors"])

        rc, out, _ = run("brief", "--project", proj, "--json")
        check("the CLI exits 0 on a machine with no pack", rc == 0, out)


def test_brief_two_packs_stop_and_ask() -> None:
    section("brief - two packs stop and ask, unchanged from find_pack")

    with Sandbox() as sb:
        _brief_pack(sb)
        _brief_pack(sb, name="second-resource-pack", display="Second Lab")
        proj = _project(sb)
        res = lp.brief(proj)
        check("it does not guess which lab", res["state"] == "ambiguous", res)
        check("and it says how to answer",
              any("config --path" in n for n in res["notes"]), res["notes"])
        check("nothing is written",
              not os.path.exists(os.path.join(proj, "plan",
                                              "lab_pack_brief.md")))


def test_brief_check_reports_staleness() -> None:
    section("brief - a stale brief is reported, never refreshed underneath")

    with Sandbox() as sb:
        _brief_pack(sb)
        proj = _project(sb)
        lp.brief(proj)
        fresh = lp.brief_check(proj)
        check("a brief written from the installed pack is current",
              fresh["state"] == "current", fresh)

        sb.install_pack(version="2026.9.24")
        lp.reset_inventory_cache()
        stale = lp.brief_check(proj)
        check("a pack that has moved makes the brief stale",
              stale["state"] == "stale", stale)
        check("both versions are named",
              "2026.9.9" in stale["line"] and "2026.9.24" in stale["line"],
              stale["line"])
        check("and --check writes nothing",
              "2026.9.24" not in read(os.path.join(proj, "plan",
                                                   "lab_pack_brief.md")),
              "")

        none = lp.brief_check(os.path.join(sb.dir, "no-such-project"))
        check("no brief at all is its own state",
              none["state"] == "missing", none)


def test_brief_workflow_source_is_the_packs_to_name() -> None:
    section("brief - the pack says which file carries the route")

    with Sandbox() as sb:
        path = _brief_pack(sb, lab_yml=(
            "pack: jensen-resource-pack\n"
            "display_name: \"Jensen Lab\"\n"
            "curated_on: \"2026-09-09\"\n"
            "workflow_source: \"route.md\"\n"))
        write(os.path.join(path, "route.md"),
              "# The Route\n\n## One Single Step\n\nDo the thing.\n")
        proj = _project(sb)
        text = lp.brief(proj)["text"]
        check("the named file is the route",
              "### 1. One Single Step" in text, text)
        check("and sops.md becomes one of the indexed files instead",
              "Culture Growth for the Fixture Route" not in text
              or "sops.md" in text, text)

    with Sandbox() as sb:
        path = sb.install_pack()
        os.remove(os.path.join(path, "sops.md"))
        proj = _project(sb)
        res = lp.brief(proj)
        check("a pack with no route file still writes a brief",
              res["state"] == "written", res)
        check("and says the route is not written down anywhere it can read",
              any("no step file" in n or "carries no route" in n
                  for n in res["notes"]), res["notes"])


def test_brief_cli() -> None:
    section("brief - the CLI, end to end")

    with Sandbox() as sb:
        _brief_pack(sb)
        proj = _project(sb)
        bundle = os.path.join(sb.dir, "brief.json")
        write(bundle, json.dumps(BRIEF_BUNDLE, indent=2))

        rc, out, err = run("brief", "--project", proj, "--bundle", bundle,
                           "--dry-run", "--json")
        check("--dry-run exits 0", rc == 0, err)
        payload = json.loads(out)
        check("and reports it would write", payload["state"] == "dry-run",
              payload)
        check("nothing is on disk after a dry run",
              not os.path.exists(os.path.join(proj, "plan",
                                              "lab_pack_brief.md")))

        rc, out, err = run("brief", "--project", proj, "--bundle", bundle)
        check("the real run exits 0", rc == 0, err)
        check("and the file is there",
              os.path.exists(os.path.join(proj, "plan",
                                          "lab_pack_brief.md")))
        check("the text output names the path", "lab_pack_brief.md" in out,
              out)

        rc, out, _ = run("brief", "--project", proj, "--out",
                         os.path.join(proj, "data", "x.md"), "--json")
        check("a refusal exits 2", rc == 2, out)


# ---------------------------------------------------------------------------
# C - plan/methods_notebook.md (specs/methods-notebook-2026-09-28.md 3)
# ---------------------------------------------------------------------------

NOTEBOOK_SOPS_EXTRA = ("\n## Processing on the Fixture Cluster\n\n"
                       "Run the Fixture Aligner script over every tilt "
                       "series. **Never run it on the login node.**\n")


def _notebook_pack(sb, **kw) -> str:
    path = _brief_pack(sb, **kw)
    write(os.path.join(path, "sops.md"), BRIEF_SOPS + NOTEBOOK_SOPS_EXTRA)
    write(os.path.join(path, "software.md"), SOFTWARE_MD)
    return path


NOTEBOOK_BUNDLE = {
    "steps": [
        {"step": "Glow Discharge on the Quorum Fixture",
         "todo": ["Book the glow discharger for the freezing morning",
                  "Glow discharge at 25 mA for 30 s"],
         "record": ["The grid lot"]},
        {"step": "Fixture Aligner",
         "todo": ["Run it on the three best tilt series first"],
         "decided": ["Three tilt series, the three with the most beads"]},
        {"step": "Culture Growth for the Fixture Route",
         "todo": ["Start the culture 2-3 days before freezing"]},
    ],
    "needs": ["A carbon grid box for the glow-discharge morning"],
}


def test_notebook_unnarrowed() -> None:
    section("notebook - with no bundle, every step, and it says so")

    with Sandbox() as sb:
        _notebook_pack(sb)
        proj = _project(sb)
        res = lp.notebook(proj)
        check("the write succeeded", res["state"] == "written", res)
        check("it lands at plan/methods_notebook.md",
              res["path"].replace(os.sep, "/").endswith(
                  "plan/methods_notebook.md"), res["path"])
        text = read(res["path"])

        check("line 1 is the banner",
              text.splitlines()[0] == "<!-- labpack-notebook -->",
              text[:80])
        check("the header says it is a to-do list and not a methods fact",
              "to-do list" in text[:1500]
              and "nothing here is a methods fact" in text[:1500].lower(),
              text[:1500])
        check("and says it has not been narrowed to this project",
              "not been narrowed" in text[:2500], text[:2500])
        for i, name in enumerate(
                ["Culture Growth for the Fixture Route",
                 "Grid Vitrification on the Fixture Plunger",
                 "Glow Discharge on the Quorum Fixture"], start=1):
            check(f"route step {i} is a numbered step",
                  f"### {i}. {name}" in text, text[:600])
        check("What We Need comes BEFORE the steps",
              0 < text.index("## What We Need") < text.index("## Steps"),
              text[:900])
        check("a step with no bundle bullets points at the brief",
              "- [ ] Read this step in `plan/lab_pack_brief.md`" in text,
              text)
        check("the protocol link is carried, verbatim",
              "[Box](https://example.invalid/1)" in text, text)
        check("the log section exists to be written in",
              re.search(r"^## Your Log\s*$", text, re.M) is not None,
              text[-400:])


def test_notebook_watch_out_quotes_the_pack() -> None:
    section("notebook - Watch Out is the pack's own emphasis, verbatim")

    with Sandbox() as sb:
        _notebook_pack(sb)
        proj = _project(sb)
        text = lp.notebook(proj)["text"]
        block = text[text.index("### 1. Culture"):text.index("### 2.")]
        check("a paragraph opening with a constraint LABEL is quoted whole",
              "**What it rules out.** Once concentrated, grids must be "
              "frozen within **30 minutes** or the cells shed their "
              "flagella." in block, block)
        check("a bold span with no constraint word is not quoted",
              "Numbers that set the schedule" not in block, block)
        proc = text[text.index("Processing on the Fixture Cluster"):]
        proc = proc[:proc.index("\n### ") if "\n### " in proc
                    else len(proc)]
        check("a constraint bold span elsewhere is quoted",
              "**Never run it on the login node.**" in proc, proc)
        check("the quote is dated with the pack's curation",
              "curated 2026-09-09" in block, block)


def test_notebook_uses_and_needs() -> None:
    section("notebook - what each step uses, and What We Need")

    with Sandbox() as sb:
        _notebook_pack(sb)
        proj = _project(sb)
        text = lp.notebook(proj)["text"]
        need = text[text.index("## What We Need"):text.index("## Steps")]
        check("an instrument a step names is in What We Need",
              "Quorum Fixture Glow Discharger (BYU)" in need, need)
        check("with the step that uses it",
              re.search(r"Quorum Fixture Glow Discharger \(BYU\) - step 3",
                        need) is not None, need)
        check("software a route step names is attached by token",
              re.search(r"Fixture Aligner - step 5", need) is not None, need)
        check("an instrument no chosen step uses is not needed",
              "Fixture Scope (TEM)" not in need, need)
        check("What We Need is tick boxes",
              "- [ ] Quorum Fixture Glow Discharger (BYU)" in need, need)

    # Measured on the real pack: a program matched on its HEADING attached
    # to every step sharing one of the heading's words. It is matched on the
    # name its yaml half gives it.
    with Sandbox() as sb:
        pack = _notebook_pack(sb)
        write(os.path.join(pack, "software.md"), SOFTWARE_MD
              + "\n## Cluster Scripts\n\nA helper.\n\n```yaml\n"
                "software_name: Quiet Tool\n```\n")
        proj = _project(sb)
        text = lp.notebook(proj)["text"]
        check("a program whose heading word a step uses, but whose NAME no "
              "step uses, attaches to nothing",
              "Cluster Scripts" not in text.split("## Steps", 1)[0], text)


def test_notebook_narrowed() -> None:
    section("notebook - a bundle narrows it, in the project's order")

    with Sandbox() as sb:
        _notebook_pack(sb)
        proj = _project(sb)
        res = lp.notebook(proj, bundle=NOTEBOOK_BUNDLE)
        check("the write succeeded", res["state"] == "written", res)
        text = res["text"]
        check("the steps are in the BUNDLE's order, across both files",
              "### 1. Glow Discharge on the Quorum Fixture" in text
              and "### 2. Fixture Aligner" in text
              and "### 3. Culture Growth for the Fixture Route" in text,
              text[:2500])
        check("a step the bundle left out is not there",
              "Grid Vitrification" not in text.split("## Steps", 1)[1],
              text)
        check("and it no longer says it is unnarrowed",
              "not been narrowed" not in text, text[:2500])
        check("a todo is a tick box",
              "- [ ] Book the glow discharger for the freezing morning"
              in text, text)
        check("a decision is carried",
              "Three tilt series, the three with the most beads" in text,
              text)
        check("what to record is under Write Down, naming methods_facts",
              re.search(r"Write Down.*methods_facts\.yml.*\n+- The grid lot",
                        text) is not None, text)
        check("a need the user named is in What We Need",
              "- [ ] A carbon grid box for the glow-discharge morning"
              in text.split("## Steps", 1)[0], text[:2500])
        check("a software step uses itself",
              re.search(r"- \[ \] Fixture Aligner - step 2",
                        text.split("## Steps", 1)[0]) is not None,
              text[:2500])
        check("a software step with bullets does not add the default",
              "Read this step in `software.md`" not in text, text)
        check("a program step always ends by asking for the version",
              "- The version of Fixture Aligner you ran" in text, text)
        check("and a route step does not",
              "The version of Glow Discharge" not in text, text)


def test_notebook_refusals() -> None:
    section("notebook - what it refuses, and the number rule")

    with Sandbox() as sb:
        _notebook_pack(sb)
        proj = _project(sb)
        dest = os.path.join(proj, "plan", "methods_notebook.md")

        def attempt(bundle, **kw):
            return lp.notebook(proj, bundle=bundle, **kw)

        res = attempt({"steps": [{"step": "A Step Nobody Has"}]})
        check("an unknown step is refused", res["state"] == "refused", res)
        check("listing the headings of BOTH files",
              any("Culture Growth for the Fixture Route" in e
                  and "Fixture Denoiser" in e for e in res["errors"]),
              res["errors"])
        check("and nothing is written", not os.path.exists(dest))

        res = attempt({"steps": [{"step": "Fixture Aligner"},
                                 {"step": "fixture aligner"}]})
        check("two entries for one step are refused",
              res["state"] == "refused", res)

        res = attempt({"steps": [{"step": "Fixture Aligner"}],
                       "software": ["No Such Program"],
                       "instruments": ["No Such Scope"]})
        check("an unknown software or instrument name is refused",
              res["state"] == "refused"
              and any("No Such Program" in e for e in res["errors"])
              and any("No Such Scope" in e for e in res["errors"]),
              res["errors"])

        res = attempt({"steps": [{"step": "Glow Discharge on the Quorum "
                                          "Fixture",
                                  "todo": ["Glow discharge for 45 s"]}]})
        check("a number the pack does not carry is REFUSED",
              res["state"] == "refused"
              and any("45" in e and "Glow discharge for 45 s" in e
                      for e in res["errors"]), res["errors"])
        res = attempt({"steps": [{"step": "Glow Discharge on the Quorum "
                                          "Fixture",
                                  "todo": ["Glow discharge for 45 s"],
                                  "decided": ["45 s, for the gold grids"]}]})
        check("the same number is allowed when the user decided it",
              res["state"] == "written", res["errors"])
        res = attempt({"steps": [{"step": "Glow Discharge on the Quorum "
                                          "Fixture"}],
                       "needs": ["Ten 400-mesh grids"]})
        check("a need with an unsourced number is refused too",
              res["state"] == "refused"
              and any("400" in e for e in res["errors"]), res["errors"])

        res = lp.notebook(proj, out=os.path.join(proj, "data", "nb.md"))
        check("a destination under data/ is refused",
              res["state"] == "refused"
              and any("methods" in e.lower() for e in res["errors"]),
              res["errors"])

        write(dest, "# My own bench notes\n")
        res = lp.notebook(proj)
        check("a file this command did not write is not overwritten",
              res["state"] == "refused" and "My own bench notes"
              in read(dest), res)
        check("and --force is named as the way through",
              any("--force" in e for e in res["errors"]), res["errors"])


def test_notebook_carries_ticks_and_log() -> None:
    section("notebook - regeneration keeps the ticks and the log")

    with Sandbox() as sb:
        _notebook_pack(sb)
        proj = _project(sb)
        path = lp.notebook(proj, bundle=NOTEBOOK_BUNDLE)["path"]
        text = read(path)
        text = text.replace(
            "- [ ] Book the glow discharger for the freezing morning",
            "- [x] Book the glow discharger for the freezing morning")
        text = text.rstrip() + "\n\n- 2026-09-30: booked for Tuesday.\n"
        write(path, text)

        changed = json.loads(json.dumps(NOTEBOOK_BUNDLE))
        changed["steps"][0]["todo"][1] = "Glow discharge at 25 mA, 30 s, air"
        again = lp.notebook(proj, bundle=changed)
        new = read(again["path"])
        check("a ticked bullet whose text is unchanged stays ticked",
              "- [x] Book the glow discharger for the freezing morning"
              in new, new)
        check("a bullet whose text changed starts unticked",
              "- [ ] Glow discharge at 25 mA, 30 s, air" in new, new)
        check("the member's log line survives",
              "2026-09-30: booked for Tuesday." in new, new[-500:])
        check("the log heading is not duplicated",
              len(re.findall(r"^## Your Log\s*$", new, re.M)) == 1, new)
        check("the carry-forward is reported",
              again.get("carried_log") is True
              and again.get("carried_ticks") == 1, again)


def test_notebook_no_pack_two_packs_check_list_cli() -> None:
    section("notebook - no pack, two packs, --check, --list and the CLI")

    with Sandbox() as sb:
        proj = _project(sb)
        res = lp.notebook(proj)
        check("no pack: no file and no error",
              res["state"] == "no-pack" and not res["errors"]
              and not os.path.exists(os.path.join(proj, "plan",
                                                  "methods_notebook.md")),
              res)

    with Sandbox() as sb:
        _notebook_pack(sb)
        _notebook_pack(sb, name="second-resource-pack", display="Second Lab")
        proj = _project(sb)
        check("two packs stop and ask",
              lp.notebook(proj)["state"] == "ambiguous")

    with Sandbox() as sb:
        _notebook_pack(sb)
        proj = _project(sb)
        listing = lp.notebook_list()
        check("--list names both files' steps",
              "Processing on the Fixture Cluster" in listing["route_steps"]
              and "Fixture Aligner" in listing["software"], listing)
        check("and the instruments",
              "Fixture Scope (TEM)" in listing["instruments"], listing)
        check("and names the route file",
              listing["route_source"] == "sops.md", listing)

        lp.notebook(proj)
        check("a notebook from the installed pack is current",
              lp.notebook_check(proj)["state"] == "current")
        sb.install_pack(version="2026.9.24")
        stale = lp.notebook_check(proj)
        check("a pack that has moved makes it stale, naming both versions",
              stale["state"] == "stale" and "2026.9.9" in stale["line"]
              and "2026.9.24" in stale["line"], stale)

    with Sandbox() as sb:
        _notebook_pack(sb)
        proj = _project(sb)
        bundle = os.path.join(sb.dir, "nb.json")
        write(bundle, json.dumps(NOTEBOOK_BUNDLE))
        rc, out, err = run("notebook", "--project", proj, "--bundle", bundle,
                           "--dry-run", "--json")
        check("--dry-run exits 0 and writes nothing",
              rc == 0 and json.loads(out)["state"] == "dry-run"
              and not os.path.exists(os.path.join(proj, "plan",
                                                  "methods_notebook.md")),
              out + err)
        rc, out, err = run("notebook", "--project", proj, "--bundle", bundle)
        check("the real run exits 0 and names the path",
              rc == 0 and "methods_notebook.md" in out, out + err)
        rc, out, _ = run("notebook", "--project", proj, "--list", "--json")
        check("--list over the CLI", rc == 0
              and "Fixture Aligner" in json.loads(out)["software"], out)
        rc, out, _ = run("notebook", "--project", proj, "--check")
        check("--check over the CLI", rc == 0 and "current" in out, out)
        rc, out, _ = run("notebook", "--project", proj, "--out",
                         os.path.join(proj, "data", "x.md"), "--json")
        check("a refusal exits 2", rc == 2, out)



# ---------------------------------------------------------------------------
# Codex - specs/codex-plugin-2026-09-30.md 3 and 5. Every layout below was
# measured on Codex 0.159.3 (2026-10-01), not read off its docs.
# ---------------------------------------------------------------------------

def test_codex_root_is_read() -> None:
    section("Codex - a pack installed in Codex is found")

    with Sandbox() as sb:
        path = sb.install_codex_pack()
        res = lp.find_pack()
        check("a pack under the Codex root resolves at rung 3",
              res["path"] == path and res["source"] == "plugin", res)
        check("its version comes from its own manifest",
              res["version"] == "2026.9.9", res)
        check("Codex's cache is a plugin install",
              lp.inside_plugin_install(os.path.join(path, "resources", "x.md")),
              path)
        check("...and it is named as Codex's", lp.host_of(path) == "codex",
              lp.host_of(path))
        check("a path beside the cache is not inside it",
              not lp.inside_plugin_install(os.path.join(sb.codex, "x")),
              sb.codex)
        check("the update a Codex member is told to run is Codex's",
              lp.update_command(path) == "`codex plugin marketplace upgrade`",
              lp.update_command(path))
        check("the marketplace snapshot is found where Codex keeps it",
              lp.marketplace_clone(path) == os.path.join(
                  sb.codex_home, ".tmp", "marketplaces",
                  "jensen-resource-pack-marketplace"),
              lp.marketplace_clone(path))


def test_codex_and_claude_both() -> None:
    section("Codex - one pack in both CLIs is one pack; two packs still ask")

    with Sandbox() as sb:
        claude = sb.install_pack()
        sb.install_codex_pack()
        res = lp.find_pack()
        check("the same pack in both roots is not ambiguous",
              res["source"] == "plugin", res)
        check("...and Claude Code's copy is the one read", res["path"] == claude,
              res["path"])

    with Sandbox() as sb:
        a = sb.install_pack()
        b = sb.install_codex_pack(name="bell-resource-pack", display="Bell Lab")
        res = lp.find_pack()
        check("two DIFFERENT packs, one per CLI, stop and ask",
              res["source"] == "ambiguous"
              and sorted(res["ambiguous"]) == sorted([a, b]), res)


def test_codex_roots_ladder() -> None:
    section("Codex - where its root is, and scaffold.py agrees")

    keep = {k: os.environ.get(k) for k in
            ("PAPER_ENGINE_PLUGINS_DIR", "PAPER_ENGINE_CODEX_PLUGINS_DIR",
             "CODEX_HOME")}
    try:
        for k in keep:
            os.environ.pop(k, None)
        home = os.path.expanduser("~")
        check("the default is ~/.codex/plugins",
              lp.codex_plugins_dir() == os.path.join(home, ".codex", "plugins"),
              lp.codex_plugins_dir())
        os.environ["CODEX_HOME"] = os.path.join(home, "elsewhere")
        check("$CODEX_HOME moves it, as it moves Codex",
              lp.codex_plugins_dir() == os.path.join(home, "elsewhere",
                                                     "plugins"),
              lp.codex_plugins_dir())
        os.environ["PAPER_ENGINE_CODEX_PLUGINS_DIR"] = os.path.join(home, "t")
        check("$PAPER_ENGINE_CODEX_PLUGINS_DIR beats it, so a test never "
              "reads the member's own",
              lp.codex_plugins_dir() == os.path.join(home, "t"),
              lp.codex_plugins_dir())
        # Two ladders that must agree, so they are compared, in all three
        # states above.
        for state in ("override", "codex_home", "default"):
            if state == "codex_home":
                os.environ.pop("PAPER_ENGINE_CODEX_PLUGINS_DIR", None)
            if state == "default":
                os.environ.pop("CODEX_HOME", None)
            check(f"scaffold.py and labpack.py name the same roots ({state})",
                  [os.path.abspath(r) for r in sc._plugin_roots()]
                  == [r["path"] for r in lp.plugin_roots()],
                  (sc._plugin_roots(), lp.plugin_roots()))
        check("Claude Code's root comes first",
              [r["host"] for r in lp.plugin_roots()] == ["claude", "codex"],
              lp.plugin_roots())
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_codex_freshness() -> None:
    section("Codex - freshness reads Codex's own records")

    rem = _load("remote_for_codex", os.path.join(TOOLS, "remote.py"))
    HERE = "73e53311cb26d523da3b285d11ea468e4088f0c3"
    MOVED = "9a94d9b5d4070d8f8a4dc62f22872424e1575e5c"

    with Sandbox() as sb:
        path = sb.install_codex_pack(sha=HERE)
        check("the repository comes from config.toml's marketplace table",
              rem.repo_for_path(path) == "x/jensen-resource-pack",
              rem.repo_for_path(path))
        check("...and is asked about by `remote.py check`",
              "x/jensen-resource-pack" in rem.installed_repos(),
              rem.installed_repos())
        check("the installed commit is the copy's own .git HEAD",
              rem.installed_sha(path) == HERE, rem.installed_sha(path))

        sb.remote_says(sha=MOVED)
        fresh = lp.freshness()
        check("a moved repository is update_available",
              fresh["state"] == "update_available", fresh)
        check("...and the line names Codex's command, not Claude Code's",
              "codex plugin marketplace upgrade" in fresh["line"]
              and "/plugin update" not in fresh["line"], fresh["line"])
        # Measured: `marketplace upgrade` applied a commit with no version
        # bump. "it may have nothing to do" would be false in Codex.
        check("...and drops the no-version-bump caveat, which is false there",
              "version bump" not in fresh["line"], fresh["line"])

        sb.remote_says(sha=HERE)
        check("the same commit is current",
              lp.freshness()["state"] == "current", lp.freshness())

    with Sandbox() as sb:
        path = sb.install_codex_pack(sha=HERE, packed=True)
        check("a HEAD resolved through packed-refs is read too",
              rem.installed_sha(path) == HERE, rem.installed_sha(path))

    with Sandbox() as sb:
        sb.install_codex_pack()
        sb.remote_says(sha=MOVED)
        fresh = lp.freshness()
        check("a Codex copy with no .git is UNKNOWN, never current",
              fresh["state"] == "unknown", fresh)
        check("...and says why in Codex's terms",
              "Codex install carries no .git" in fresh["line"], fresh["line"])

    with Sandbox() as sb:
        path = sb.install_codex_pack(
            sha=HERE, source="https://gitlab.example.edu/x/pack.git")
        check("a marketplace that is not on GitHub has no repository to ask",
              rem.repo_for_path(path) == "", rem.repo_for_path(path))
        check("...so freshness is UNKNOWN and says so",
              lp.freshness()["state"] == "unknown", lp.freshness())

    with Sandbox() as sb:
        sb.install_pack(sha=HERE)
        sb.remote_says(sha=MOVED)
        line = lp.freshness()["line"]
        check("Claude Code's line is unchanged: its command and its caveat",
              "/plugin update" in line and "version bump" in line, line)


def main() -> int:
    test_dropzone_ladder()
    test_dropzone_at_risk_by_name()
    test_adopt_moves()
    test_adopt_never_overwrites()
    test_idea_resources_reads_the_dropzone()
    test_idea_resources_with_neither_present()
    test_pack_ladder_order()
    test_two_packs_stop_and_ask()
    test_a_plugin_without_the_marker_is_not_a_pack()
    test_install_index_shapes()
    test_lab_yml_is_the_only_machine_read_file()
    test_pack_freshness_is_offline()
    test_freshness_reads_and_never_reaches()
    test_remote_cache_is_credential_safe()
    test_show_reports_both_ladders()
    test_prefill_layer_precedence()
    test_prefill_marker_names_its_layer()
    test_prefill_reads_the_old_marker_forever()
    test_prefill_with_no_pack_is_unchanged()
    test_prefill_confirming_one_key_keeps_the_rest()
    test_prefill_key_confirmed_in_another_block()
    test_prefill_software()
    test_a_prose_only_layer_says_so_once()
    test_inventory_from_fallback_path()
    test_unknown_is_never_out_of_stock()
    test_inventory_is_offered_never_automatic()
    test_inventory_answers_what_the_file_can_answer()
    test_inventory_with_an_on_hand_column()
    test_inventory_reads_a_real_spreadsheet()
    test_inventory_from_connector_file()
    test_brief_section_splitter()
    test_brief_has_every_step()
    test_brief_quotes_the_pack_verbatim()
    test_brief_project_half_comes_first()
    test_brief_refuses_a_step_the_pack_does_not_have()
    test_brief_is_never_written_under_data()
    test_brief_carries_your_notes_forward()
    test_brief_never_overwrites_a_foreign_file()
    test_brief_instruments_attach_by_name()
    test_brief_indexes_the_rest_of_the_pack()
    test_brief_with_no_pack_writes_nothing()
    test_brief_two_packs_stop_and_ask()
    test_brief_check_reports_staleness()
    test_brief_workflow_source_is_the_packs_to_name()
    test_brief_cli()
    test_notebook_unnarrowed()
    test_notebook_watch_out_quotes_the_pack()
    test_notebook_uses_and_needs()
    test_notebook_narrowed()
    test_notebook_refusals()
    test_notebook_carries_ticks_and_log()
    test_notebook_no_pack_two_packs_check_list_cli()
    test_codex_root_is_read()
    test_codex_and_claude_both()
    test_codex_roots_ladder()
    test_codex_freshness()

    print(f"\n{PASSED}/{PASSED + FAILED} passed"
          + (f", {FAILED} FAILED" if FAILED else ""))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
