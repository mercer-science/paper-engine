---
name: reorganize-directory
description: Sort out a research folder that already has months of work in it - scaffold what is missing, propose a home for every unfiled file and folder, sweep the ones nothing can place into sandbox/, read the figure and table order off the filenames, and end with a report of what this folder can and cannot now do. Use when the user says their folder is a mess, that files are everywhere, that they do not know where anything is, or asks to reorganize, sort out, clean up, tidy, organise or restructure a directory, or to sort their figures out. Also use when a project directory has four months of work in it and no structure around it.
---

# reorganize-directory

For the folder that already has the work in it. **The product is a
conversation, not a move** — on a real 28-entry folder, 21 entries came back as
questions and 6 as certainties, and working through those 21 one line each is
the job.

Spec: `specs/reorganize-directory-2026-09-20.md`. Read it before changing
behaviour here.

**This skill and `setup-project-directory` share one engine and split by
audience.** That one starts a project; this one rescues a folder. Both call
`scaffold.py adopt`, both read the same `confidence` / `action` / `why` fields
from the same table, and **neither of them classifies anything itself**. A
skill that re-derives a rule will derive it slightly differently the first
time the vocabulary grows.

## The engine does the work

Resolve `<tools>` in this order, and stop at the first hit:

1. `${CLAUDE_PLUGIN_ROOT}/tools/`, if the harness expands that variable
2. `../../tools/` relative to this skill's own directory (the skill is
   authored inside the toolkit at `skills/<name>/`, so this wins when it has
   not been synced out)
3. `tools/` under the toolkit root, if the pointer file that led you here
   named that root

If none of them resolves, say so and stop.

## First, Say Whether This Copy Is Behind

As soon as `<tools>` resolves, before any other command:

```bash
python <tools>/remote.py refresh
```

**Print whatever it prints, verbatim, then carry on in the same breath.**
Most of the time it prints nothing at all, and nothing is the intended
outcome. When it does print, the line says that the toolkit or the lab pack
is behind the repository it came from, or that whether it is behind is
UNKNOWN and why.

**This is the only command in the toolkit that reaches a network, and this is
the only moment it may run.** It asks GitHub at most once a day per
repository and gives up after six seconds; every other invocation reads an
answer already on disk. Nothing later in the session — no stage, no engine,
no draft — makes a network call for this, so a member working offline gets
the same run with one silent failure recorded at the start.

**It reports; it never updates, never blocks, never gates a stage and never
changes what you do next.** Do not offer to run `/plugin update` for them,
do not wait for a reply, and do not raise it again later in the session. **Do not sort the folder by hand.**
The rules table in `scaffold.py` is the contract, and a move made on a guess
is a move nobody can audit.

```bash
python <tools>/scaffold.py adopt "<path>" --json [--depth N] [--expects-data ...]
python <tools>/scaffold.py adopt "<path>" --json --apply [--sweep-unknown]
python <tools>/scaffold.py float-order "<path>" --json
python <tools>/scaffold.py report "<path>" --json
python <tools>/scaffold.py check "<path>" --json
```

`adopt` scaffolds first — so a proposal can name a destination that exists —
then classifies everything the structure does not explain. **It moves nothing
without `--apply`, it never deletes, and it never renames.**

## Routing

| The user says | What to do |
|---|---|
| my folder is a mess, files everywhere, I don't know where anything is | this skill, from the top |
| reorganize / sort out / clean up / tidy / organise this folder | this skill |
| sort my figures out, which figure is which, I have three files called Figure 2 | `float-order` first, then the rest if they want it |
| set up / start / scaffold a project | `setup-project-directory` — an empty folder is a different job |
| there is a `notes/` folder, can the drafter use it | `scaffold.py survey` alone; no reorganisation needed |
| is anything missing | `scaffold.py check` alone |

## The Conversation, in Order

The numbered rules are the same ones `setup-project-directory` follows, with
four more steps on the end.

1. **Report the structure first, and lead with the reassuring true half.**
   *"53 pieces added, nothing overwritten"* is true and it is what the user
   needs to hear before a list of questions.
2. **Then the certain moves, as one question for all of them.** Four CSVs into
   `data/raw/` is one decision, not four.
3. **Then the judgement calls, one line each, with the engine's own `why`.**
   These are short questions with real answers — *"is `figures/` your old
   renders, or source images?"* — and the answer changes where things land.
4. **Then the sweep, as its own question**, with the count and the manifest
   path named, and every exemption read out.
5. **Then the float order**, and when there is no prose, read the honest
   sentence rather than a numbering.
6. **Then the lab pack brief**, if this lab has a pack — one command, one
   line said about it, no question.
7. **Then the report** — every row saying what it did or why it could not.

**The rule that outranks all six: do not move the `ask` rows on the user's
behalf, even when it seems obvious.** This skill adds and it sorts; it does not
reorganise somebody's work on a guess.

## Depth — Ask Which Request This Is

```bash
python <tools>/scaffold.py adopt "<path>" --depth 2 --json
```

**`--depth 1` is the default and is the top level only.** *"My folder has
folders in it"* and *"reorganise the inside of my folders"* are different
requests and only the user knows which one they are making, so ask before
passing a depth.

Four fences hold at any depth, and each is a refusal:

- **Never descends into a folder the tree owns.** `plan/`, `data/`, `drafts/`,
  `obsolete/`, `sandbox/` are the structure's business.
- **Never descends into a folder whose own name matched a rule.** If
  `Raw Data/` classified as data, it moves whole. Taking a folder that has a
  meaning apart on a rules table is exactly what the old *"top level only"*
  rule was protecting against, and that reasoning is not thrown away — the
  fence moved.
- **Descends only into folders nothing matched at all** — the `misc/` with
  four months of everything in it, which is the only place depth buys
  anything.
- **Nothing found below the top level is ever `clear`**, so `--apply` cannot
  reach it. The proposal is the product and a human still says yes.

A walk that hits the cap reports `truncated`. **Say so out loud** — a report
that silently covered the first 4000 of 60,000 files is a report that says the
folder is tidier than it is.

## The Sweep — `sandbox/unsorted/`, and Only the Shrugs

```bash
python <tools>/scaffold.py adopt "<path>" --apply --sweep-unknown --json
```

The user asked for *"`sandbox/` if it can't tell"*, and **one tier means that
and the others do not.**

| tier | what it means | swept? |
|---|---|---|
| `clear` | the extension or the whole folder name can only mean one thing | no — it has a real destination |
| `likely` | a rule matched, but the thing can mean two things | **no.** A rule matched |
| `ask` | a rule matched and wrote a question into its reason | **no, and this is the row that matters** |
| `keep` | talks, grant admin — fine where it is | no |
| `unknown` **with no proposal** | no rule matched at all, and nothing inside it spoke | **yes** |

**Why the `ask` row matters:** on the real folder this was measured on,
`manuscript FINAL v3.docx` classifies as `ask`. A sweep of the uncertain tiers
would move the paper into the one folder nothing reads and report success. The
folder would look tidy and the manuscript would be gone from the engine's view.

Four more things the engine does, and you report all four:

- **It is not part of `--apply`.** Saying yes to moving four CSVs is not saying
  yes to relocating everything the engine does not recognise.
- **The destination is `sandbox/unsorted/`, not `sandbox/`** — *"the engine put
  this here because it could not tell"* and *"I put this here on purpose"* must
  never become the same statement.
- **The manifest is the product.** `sandbox/unsorted/WHERE-THESE-CAME-FROM.md`
  carries one row per entry with its original path and the engine's own reason,
  and a second run appends. A move nobody can reverse is a deletion with extra
  steps.
- **Nothing manuscript-shaped is ever swept**, whatever its tier, and the
  exemption is reported per file. `manuscript.py strays` decides what is
  manuscript-shaped; two commands disagreeing about a `.docx` is the case that
  loses a paper.

## Figure and Table Order

```bash
python <tools>/scaffold.py float-order "<path>" --json
```

It reads **names**, never file contents, and it **proposes only** — it creates
no float folder, writes no caption and renames nothing. `float new` and
`float renumber` do that and they own `plan/captions.md`.

What to read out:

- **`version_groups` is always a question.** Two files claiming one slot —
  `Figure 2 final.png` beside `Figure 2.png` — is never resolved for them.
  Which one reaches the build must not be the order the disk happens to list
  them in.
- **`version_marks` is evidence, not an answer.** `fig1_old.png` parses to
  Figure 1 and is almost certainly not Figure 1 any more; the word `old` in
  that name is worth more than the digit, and the engine reports both.
- **`disagrees_with_prose` means the prose wins.** Floats are numbered in order
  of first mention, which is a fact about the manuscript; a filename is a fact
  about whoever saved the file.
- **When there is no prose, say the honest sentence**, which the engine prints
  for you:

  > The order of a paper's figures is decided by the order the paper mentions
  > them, so until the sections exist, every order here is a guess the author
  > has to confirm.

  Getting this right is not something the tool can do by itself before the
  paper is written, and **saying so is the deliverable.**

## The Report This Skill Ends On

```bash
python <tools>/scaffold.py report "<path>" --json
```

One row per capability, and **every row says what it did or why it could not,
and never nothing.** Read the whole thing out; the two rows people do not
expect are the last two.

| Row | What it answers |
|---|---|
| Structure | what was added, what is still missing, any name collision |
| Sorted | how many entries are outside the structure, how many are questions, how many nothing could place |
| Float order | what the filenames imply, or why nothing was checked |
| AI disclosure | reachable, or **not reachable yet — no journal initialised** |
| Cover letter | the same, and it arrives with the submission package whether or not the journal asks for one |
| Citations | how many bibliography entries there are to check |

**The AI-disclosure row is routing, not a gap.** `manuscript.py ai-disclosure`
is built, it knows fifteen section-name aliases and three separate obligations,
and it **refuses to write the statement itself** — the party whose use is being
disclosed is the wrong party to trust with the disclosure. It refuses cleanly
at exit 2 with *run init first*, which is correct; what was missing was that
nobody was told it is waiting one `init` away.

`requirements.yml` ships `ai_disclosure.required: unknown`, and **`unknown`
blocks**: nobody has read the journal's policy yet, and a default of `no` would
be the engine deciding a compliance question. The group's own disclosure
wording is still an open decision, so `ai_disclosure.md` is a stub of flags on
every project. **That is the correct state, not a bug** — say it here rather
than letting the user find it at submission.

## The Lab Pack Brief, If This Lab Has a Pack

Run it after the structure exists and before the report. Spec:
`specs/lab-pack-brief-2026-09-24.md`.

```bash
python <tools>/labpack.py brief --project "<path>"
```

`plan/lab_pack_brief.md`: **every step of this lab's route, quoted from the
pack verbatim and stamped with its curation date**, the instruments those
steps name, and an index of the rest of the pack. This skill exists for the
folder that already has four months of work in it, and that folder is exactly
the one where *how did we do this step* has already been asked and answered in
a conversation nobody wrote down. The brief gives those answers somewhere to
live — `## Your Notes` at the end is the user's and no regeneration touches
it.

**No bundle, and no question first.** There is no idea conversation here to
draw a project half from, so every step says *"nothing project-specific is
recorded for this step yet"* — true, and an invitation. `idea-generation`
fills those lines in later if this project ever goes through it, and carries
the notes forward.

- **No pack, no file, no sentence.** Two packs stop and ask, once.
- **A brief already on disk is regenerated and the notes are kept.** A file at
  that path the engine did not write is **refused** rather than overwritten —
  which on this skill's folder is a real case, so read the refusal out rather
  than reaching for `--force`.
- **It is a source and never a result.** Nothing in it is a methods fact; if a
  number contradicts what the user knows, the user is right and the pack is
  stale.

## What This Skill Does Not Do

- **It does not rename anything, ever.** A file called `Figure 2 final.png`
  keeps that name inside `plan/figures/Fig02/`. The name is the author's record
  of what they thought.
- **It does not delete, and it never says "this looks like a duplicate".** Two
  files that look like versions of each other are a question in a report.
- **It does not read inside a file to classify it.** *Is this the same figure
  as that one* is not answerable from bytes, and a classifier that opened a
  `.docx` to decide where it goes has read a manuscript nobody declared it may
  read.
- **It does not infer `expects_data`.** Declared, never sniffed. A folder of
  CSVs in an `expects_data: none` project stays the contradiction the engine
  already surfaces — one of the two is wrong and only the user knows which.
- **It writes no disclosure and no cover letter.** Both are built elsewhere and
  refuse honestly; a reorganize-time disclosure would be a second answer to a
  compliance question.

## When the engine gets it wrong

A defect found while reorganizing a folder dies with the session unless you
record it:

```bash
python <tools>/manuscript.py log-issue "<project>" --stage setup \
    --code a_short_slug --detail "what happened, GENERICALLY" \
    --example "what happened on THIS project" \
    --title "one line" --wanted "what it should do" \
    --verify "how the next run answers 'is it still there?'"
```

**`--stage setup`**, because unstated the stage is inferred from the journal
and the round, and both are empty here. **`--detail` is generic and
`--example` is the one place a project specific may live** — `system-changes.md`
is read by everyone who has the toolkit. And **never edit
`system-changes.md` by hand**: `log-issue` is its only writer.
