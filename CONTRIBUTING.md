# Changing the toolkit

For working **on** the Academic Research Paper Engine. If you only want to use
it, everything you need is the [README](README.md) and
[`help/instructions.md`](help/instructions.md); nothing here is required to
write a paper.

> [!IMPORTANT]
> **Nothing from a real project goes into this repository — it is public.** Not a
> sentence from a draft, not a result, not a filename, not a collaborator's name.
> Examples in skills, tests and comments are made up (`EXAMPLE_STUDY`, Ada Bell).
> The defect ledger `system-changes.md` is the one place a project specific may be
> recorded, and it is gitignored: it stays on the machine that wrote it.

`AGENTS.md` is the harness-neutral contract an agent reads before touching this
repository; `CLAUDE.md` beside it is the short project brief. The reasoning
behind every piece is in the maintainer's design notes (`specs/`, kept private
and gitignored); a maintainer who has them should **read the spec for a piece in full before changing
it**; several of them record decisions that read as arbitrary without the
history.

## Layout

```
skills/<name>/SKILL.md  the eight skills, one directory each - thin callers.
                        writing-engine is a spine plus reference/*.md per stage
tools/                  the engines: standalone CLIs, no agent required
tests/                  suites that measure the engines against reality
resources/              a drop-zone for lab material; it ships EMPTY
help/                   the guide, the flow-chart deck, the new-member checklist
writing_guides/         the distilled house writing rules the drafter loads
```

Gitignored, and on the maintainer's machine only: `specs/`, `history/`,
`TODO.md` and `system-changes.md` (the engine's own defect list; never edit it
by hand).

**Two folders are deliberately NOT here.** The probe corpus (two real
unpublished manuscripts) and the copyrighted source PDFs the house digest was
distilled from live in the sibling `paper-engine-corpus/`, so this tree is
only what a member receives. Both were gitignored before they moved; the two
probes that read the corpus resolve it through `$PAPER_ENGINE_CORPUS`, then
that sibling, then the old in-tree path.

**Filing a defect: the description is generic, the specifics go in one field.**
`manuscript.py log-issue` refuses a sample id, composition or oxidation state
in `--title`, `--detail`, `--wanted` or `--verify`, and names the field and the
token when it does. `--example` is the one field that is never linted and
renders as a `Specific Example:` bullet. `tests/portability.py` scans
everything that reaches GitHub for the same shapes, exempting those bullets —
so the guard and the field are one rule stated twice.

**The lint outlived the reason it was built, and still earns its place.** It
was written when a redacted copy of every item was pushed to a repository the
whole group read. `system-changes.md` is local now and never pushed, but the
lint keeps a project specific out of the fields a session reads most, and
`tests/portability.py` still scans everything that reaches GitHub for the same
shapes.

**If a fixture or an example needs a specific-shaped token, use the reserved
fictional ones: `ZQx7` for a sample id, `Xx/Yy` for a composition.** The guard
matches on shape and therefore cannot tell a fictional specific from a real
one, so those two are allowlisted by name in `tests/portability.py` with their
reason. Anything else of that shape fails the suite, and there is deliberately
no blanket exemption for `tests/` — a realistic fixture is the guard being
right, not the guard being wrong.

**The engines are the product; the skills are a script for driving them.** A
skill shells out to `tools/*.py` and reasons over `--json`. One engine, many
callers: improving retrieval in the engine upgrades every skill at once, and a
different agent product needs to read the markdown rather than reimplement the
Python. No skill reimplements engine logic, and no skill body writes a literal
`python tools/x.py` — every call goes through the `<tools>` placeholder each
skill resolves for itself, which is what let the plugin route add one rung
without rewriting a single call site.

## Adding an engine

Write `tools/<name>.py` and `tests/<name>.py` beside it, then give it a line in
each of these — `tests/portability.py` will tell you which one you forgot
rather than leaving it to be noticed:

1. the **engine table** in `AGENTS.md`
2. the **suite list** in that file's *Before you finish* section — a suite
   nobody is told to run does not get run
3. the `DECLARED` map in `tests/portability.py`, if it imports a third-party
   package, keyed by **import** name

If it keeps anything the user supplies — a credential, a cache, a database —
it needs a resolver of the `$ENV` → `${CLAUDE_PLUGIN_DATA}` → toolkit shape
that **names which of the three it used**, and a row in
`test_user_state_never_lives_where_an_update_replaces_it`. Nothing a user
supplies may live in a directory a plugin update replaces; the toolkit stays
last so a source checkout is unchanged. That rule is `specs/distribution.md`
§5.6, and it is enforced by that suite rather than by anyone's memory.

## Adding a skill

Create a directory under `skills/` with a `SKILL.md` carrying `name` and
`description` frontmatter — the frontmatter `name` must match the directory
name — then re-run `python tools/install_skills.py install`.

Three files then need a line about it, and `tests/portability.py` will tell you
so rather than leaving it to be noticed:

1. the skill table in `AGENTS.md`
2. the skill count in that suite
3. **the `skills` list in `.claude-plugin/plugin.json`**

That third one is why discovery is only *half* automatic: the clone route scans
`skills/` for a `SKILL.md`, but the plugin ships an explicit list, and **a skill
missing from that list ships to nobody without erroring.** The suite asserts the
list and the directories match in both directions, so a forgotten line fails the
build rather than shipping quietly.

### Splitting a skill that has grown too large

`writing-engine` is a **spine plus per-stage `reference/*.md`** rather than one
file, because the single file had reached 219 KB — larger than the whole engine
payload of a full submission round — and a harness reads a skill's body when
the skill is *invoked*, so a context cleared mid-round re-paid for all of it
before it could be told which part it needed. Three rules if you split another
one, and the second is the one that costs something if you get it wrong:

1. **The spine keeps what every invocation needs regardless of stage** — how to
   start, how to state the plan, the registry and the isolation contract, and
   the prohibitions. Everything else goes out by stage.
2. **`tests/portability.py`'s file list widens in the SAME commit.** Its greps
   run over "the skill"; pointed at `SKILL.md` alone after a split they cover a
   fraction of the text and report the same green they always did, for months.
   `SKILL_TEXTS` is the list, `VISITED` records which grep read what, and
   `test_skill_reference_files_are_covered` asserts each grep reached every
   file rather than trusting that it did. Widening it caught a real defect on
   its first run.
3. **Every reference file has a route to a reader.** A section moved out of a
   `SKILL.md` and named by nothing is a rule deleted with extra steps. Here the
   route is `manuscript.py handoff --stage`, and `tests/manuscript.py` asserts
   the union of the stages' read lists covers every file under `reference/`.

The pointer mechanism is unchanged: the reference files are siblings of the
real `SKILL.md`, resolved relative to it, and they travel with the directory on
both routes. `tests/install.py` asserts that rather than assuming it.

## Adding an engine

Three places: the engine table and the dependency table in `AGENTS.md`, and the
package in `tests/portability.py`'s `DECLARED` map — which is keyed by **import**
name while `AGENTS.md` names the pip one. Every subcommand takes `--json` on
either side of it, and exit codes mean `0` clean, `1` findings, `2` refused.

There are exactly five third-party packages (`requests`, `lxml`, `pypdf`,
`python-pptx`, `pillow`) and none of them is needed to take a paper from an idea to a
built `.docx`. Keep it that way: a new dependency has to earn its place in
`AGENTS.md`'s table, with what breaks without it.

## Running the suites

`AGENTS.md`'s **Before you finish** section lists every suite with its check
count and runtime. Run the ones for whatever you touched; run
`tests/portability.py` whenever you add a skill or an engine. All are offline
except `reliability.py`, `fulltext.py` and `scholar.py`, which hit the real
indexes and are not part of the offline total.

`pyright --project .` should be clean. The IDE's diagnostics only cover open
tabs, so run it before claiming the Problems panel is empty.

## Distribution

Two routes, one source: `README.md` covers both, `specs/plugin-packaging.md` and
`specs/distribution.md` carry the reasoning. Three rules:

- **Git or the plugin, nothing else.** A directory source is a filesystem copy
  and `.gitignore` only applies to a route that goes through git, so a folder
  hand-off carries `tools/.env` and the copyrighted PDFs under
  `writing_guides/` with it. Measured, not assumed — `system-changes.md` item 67.
- **Both manifests live at the repository root** and the repository *is* the
  marketplace. There is no server and no token for the plugin half.
- **The two environment variables in `README.md` are steps, not
  troubleshooting.** Neither failure names the variable that fixes it.

## Defect reports — local, and fixed rather than filed

**Nothing this toolkit records leaves the machine it runs on.** There was a
second repository, a fine-grained token per member, a consent gate and a
spool that pushed one redacted record per defect code upstream. All of it was
removed on 2026-09-21. Both halves of the reason matter: the token setup cost
every member a browser session and an org setting that fails with no useful
error, and in the year the machinery existed **it never once met real
GitHub** — 69 records sat spooled and unsent, so it was paying a real setup
cost for a loop that had never run.

What is left is the half that was always doing the work:

```bash
python tools/manuscript.py log-issue --list-codes     # what the engine files by itself
python tools/manuscript.py log-issue . --code <code> --detail "..." \
       --title "..." --wanted "..." --verify "..." --stage writing
```

**The standing rule is that a defect found is a defect fixed.** Filing an
item and moving on is what produced a ledger with six OPEN entries nobody had
started, some of them months old. So an item is written *and* the fix is made
in the same session, and `log-issue --fixed --detail` records what changed.
The ledger stays, because a fix still has to be checked by a run that
exercises it — the item is the thing that says what to check. Filing without
fixing is for one case only: the fix belongs to a part of the system the
current work cannot safely reach, and then the item says so.

The three rungs are unchanged and so is who may write each: `log-issue` opens
an item, `--fixed --detail` moves it to `FIX ATTEMPTED`, and only a completed
run that failed to reproduce the defect may write `VERIFIED FIXED`. Learned
prose rules were never part of a report and are still never part of
anything — they quote unpublished prose and stay on the machine that learned
them.

> [!CAUTION]
> **`system-changes.md` is written by the engine.** The numbering, the statuses
> and the "seen again" counts are all computed. `manuscript.py log-issue` is the
> only thing that writes it — including `--fixed --detail`, which is the only
> writer of the `FIX ATTEMPTED` rung. Items marked by hand could not be closed
> at all. One stray blank line in that file once stopped every write for days,
> and the refusal was reported in a field nobody was reading.

## The documentation, and who owns which file

| File | Rule |
|---|---|
| `README.md` | **user-facing only.** What it is, how to install it, what to say. Anything a maintainer needs belongs in this file instead. `tests/portability.py` pins its install claims against the manifests |
| `help/instructions.md` | the in-depth guide. Written by hand, GitHub-rendered Markdown, no `style=`, no `class=`, no images. `tests/help_deck.py` pins its anchors, its links and the things that must not go stale. **Where it and a `SKILL.md` disagree, the skill is right** |
| `help/README.md` | which files in `help/` may be hand-edited, and how |
| `help/paper_engine_overview.pptx` | hand-editable. `help_deck.py build` overwrites it with no undo, so **nothing runs it except on request** |
| `AGENTS.md` | the harness-neutral contract. It gains nothing about plugins — that is the point of it |
| `CLAUDE.md` | the short project brief: current state, the doc map, the recurring traps. **It is auto-loaded into every session, so it stays short** |
| `system-changes.md` | engine-written, never by hand, and **gitignored**: local to each machine, created from `tools/system-changes.template.md` |
| `specs/`, `history/`, `TODO.md` | the maintainer's design notes. **Gitignored**, because they are written against real projects; a comment citing `specs/<name>.md` is citing one of them |

### One Repository

Until 2026-09-28 the engine was private and its user-facing page lived in a
separate public `paper-engine-documentation` repository, with the guide and
the deck mirrored into it by a script. The engine is public now, so there is
one repository and nothing is mirrored. Lab resource packs (`jensen-resource-pack`
is the example the documentation names) are still separate, private
repositories, one per lab.

The code, the skills, the guide and the deck are all under the **MIT License**
(`LICENSE`); `.claude-plugin/plugin.json` says `MIT`.
