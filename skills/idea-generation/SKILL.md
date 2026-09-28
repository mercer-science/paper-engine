---
name: idea-generation
description: Turn a rough research intent into a settled, literature-grounded idea - the gap, the papers that establish it, the claim the paper makes, the float set that carries it, and the data that would need to exist. Use when the user wants to work out what to write about, find a gap, develop a project idea, decide what figures a paper needs, or populate a freshly scaffolded project. Works standalone or after setup-project-directory, in either order.
---

# idea-generation

Takes "I'm in a cryo-EM lab and I want to write about how chemoreceptor arrays
change across species" and ends with a project whose gap is stated, whose
evidence is retrieved and verified, whose float set is decided, and whose data
contract exists before any data does.

**Interactive and staged.** Six stages, each ending by showing what was found
and waiting. This is not a one-shot dump of ten ideas — the staging is what
makes it useful, and §6 of the spec lists what goes wrong without it.

Spec: `specs/idea-generation.md`. Read it before changing behaviour here.

## The two layers

The conversation is yours. The mechanical parts belong to engines, and going
around them is how a citation from memory or an unparseable caption gets into a
project.

| Engine | What it does |
|---|---|
| `<tools>/pubmed.py` | `sections`, `pdf` — the only route to full text. Its `search`/`related` reach one index of six; use `scholar.py` for those |
| `<tools>/scholar.py` | search, related, cited-by, verify, check-refs, cite, compound — discovery and verification across PubMed + Crossref + OpenAlex + Europe PMC + arXiv, with Semantic Scholar behind `related`/`cited-by`. **Use this to search and to verify anything.** |
| `<tools>/idea.py` | `resolve` a directory, `context` on what is already recorded, `write` the settled idea |

Resolve `<tools>` in this order, and stop at the first hit:

1. `${CLAUDE_PLUGIN_ROOT}/tools/`, if the harness expands that variable
2. `../../tools/` relative to this skill's own directory (the skill is
   authored inside the toolkit at `skills/<name>/`, so this wins when it has
   not been synced out)
3. `tools/` under the toolkit root, if the pointer file that led you here named
   that root

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
do not wait for a reply, and do not raise it again later in the session.

**Never write the artifacts yourself.** `idea.py write` takes one JSON bundle
and produces every file at once, and it refuses bundles that would put something
wrong into the project (§"The bundle" below). Writing `plan/captions.md` by hand
means a block that `read_captions()` cannot parse — invisible to the preview,
the Word floats, and the manuscript, with nothing reporting it.

## Stage 0 — where are we working

Before anything else, and without asking what you can find out:

```bash
python <tools>/idea.py resolve "<path as the user typed it>" --json
```

| status | What to do |
|---|---|
| `scaffolded` | Use it. Run `context` and go to Stage 1 |
| `close_match` | Show the candidate and confirm: "Did you mean `.../Projects/array_symmetry/`?" |
| `unscaffolded` | Say so, offer to run `setup-project-directory` there first |
| `missing` | Offer to create and scaffold it |

When `setup-project-directory` hands off, the path is already known. **Never
re-ask it.**

If there is no project directory at all, this runs in **explore-only mode**: a
folder under `<PI> - <Field>/Ideas/{Query Name}/` when the lab is known or
inferable (the usual case — the lab is normally stated in the first sentence),
`Research/Ideas/{Query Name}/` when it genuinely is not. `{Query Name}` is a
short title-case slug you propose and the user confirms — `Chemoreceptor Array
Symmetry`, not `cryo-em-idea-2026-09-01`. Confirm the path before creating it.

Then, always:

```bash
python <tools>/idea.py context "<project>" --json
```

This reports `project.yml`, which `plan/README.md` and `plan/outline.md`
sections already say something, existing caption blocks, and an inventory of the
lab's `Resources/` folder. **Do not ask for anything it already reports.**
Re-asking for a research question that is written down is the fastest way to
lose the user's confidence.

## Stage 1 — the intent, in the user's words

Ask what they want to write about. Let the answer be loose; the example at the
top of this file is a complete Stage 1 answer.

Establish only what changes the search:

- The topic as stated.
- The lab / field, if not already implied.
- Rough horizon — a semester, a year, a thesis chapter.
- **Do they already have data?** This flips the skill. "I have data on X" means
  the feasibility set is already known, so the job becomes finding which gap the
  existing data answers, and **Stage 4 runs before Stage 3**.

**Do not ask technique-level questions yet.** That is Stage 3, and the ordering
is deliberate.

### Report the Lab Pack, in One Line, Without Being Asked

If this lab has a **resource pack** installed — its own plugin, holding the
lab's instruments, SOPs, bootcamp levels and bench locations — say so here,
once, as part of what you found out rather than as a question:

```bash
python <tools>/labpack.py show --json
```

Quote the `freshness.line` it returns, verbatim, and move on:

> Jensen Lab pack, curated 2026-09-09 (current as of the check today).

> Jensen Lab pack, curated 2026-03-02 — the repository has moved since this
> copy was installed (`73e5331` installed, `9a94d9b` on GitHub as of today);
> `/plugin update` when convenient. If that reports nothing to do, the change
> did not carry a version bump and this copy is fine. Nothing here is blocked.

**Four rules, and each one has teeth.**

- **It reports; it never updates.** Changing the facility knowledge underneath
  somebody mid-conversation is worse than being one version behind it.
- **It never blocks anything.** Not a gate, not a prompt, not a stage.
- **Unknown is its own answer and is said out loud.** "Whether a newer pack
  exists is unknown" is a different fact from "you are current", and somebody
  whose background refresh broke reads silence as good news.
- **No pack is not an error, not a warning and not a defect.** Say nothing at
  all and run the stage exactly as you would otherwise. Every question a pack
  would have made specific gets asked generically instead, which is how this
  skill has always worked.

If `show` reports **two packs**, it resolves neither and says so. Ask which lab
this project belongs to and record the answer once:
`labpack.py config --path "<the pack folder>"`. Guessing which lab's SOPs to
quote is worse than the question.

**This is never a network call, and it is not the staleness check.** `show`
reads what is on this machine: the pack's name, its version, its curation
date. Whether a *newer* pack exists was settled by `remote.py refresh` at the
top of this skill, which is the one command that reaches a repository and has
already run. A member on a plane gets the same run.

**So do not say the pack is behind twice.** If `refresh` already printed that
line, report the pack here by name and curation date and say nothing further
about its freshness.

## Stage 2 — get current on the literature

**Read the lab's `Resources/` folder first, before the first query.** The SOPs
name this lab's actual instruments, techniques and materials, and **those names
are the search vocabulary** — a lab whose procedures folder is full of QCM and
TPD should not have its first gap sweep run on the words the user happened to
type in Stage 1. It needs no project directory:

```bash
python <tools>/idea.py resources "<the lab folder, or anything inside it>" --json
```

You get the file list, the subfolders and the first heading of every text
document. **No document body**, so it is cheap enough to call before the first
query; read a document when it is about to inform a query or a question.

**Three places hold that vocabulary, and the command returns all three.**

| Where | What it is |
|---|---|
| `resources` / `files` | the lab's OneDrive `Resources/` folder, as before |
| `drop_zones` | every drop-zone on this machine — what somebody dropped in for this copy of the toolkit to read |
| `pack` | the lab's curated pack: its name, its freshness line, and one heading per file — the instrument names, the SOP titles, the locations |

**`pack.resolved` is `false` when no pack is installed**, with a `note` saying
so. That is a normal state and not an error; it is reported rather than left
as an empty list, because an empty list and an unread pack look identical.

**For somebody whose OneDrive `Resources/` folder is empty, `pack.headings` IS
the vocabulary** — which is most new members, and was previously a gap sweep
run on whatever words they happened to type.

Read a pack document only when it is about to inform a query:

```bash
python <tools>/labpack.py show --json        # names the pack's folder
```

Then read `instruments.md` or `sops.md` from that folder. The headings you
already have from `resources` are enough to build the search vocabulary; the
bodies are for answering a specific question.

> **This table was false until 2026-09-21 and is the reason to check one
> against the other.** It promised all three sources and the command returned
> two — the pack was not in the payload at all — so a reader who trusted the
> table had already been told this command covered it. Filed as item 106 and
> fixed on the engine side, because the skill already runs this lookup once at
> Stage 1 for the freshness line and asking twice would be the thing to
> explain.

**If `drop_zones_at_risk` is not empty, say so before moving on.** It means
this copy of the toolkit is an installed plugin and somebody has put files in
its `resources/` folder, where the next `/plugin update` will delete them. Name
the files and offer the one-command rescue:

```bash
python <tools>/labpack.py config --resources --adopt
```

It **moves** them to `~/.paper-engine/resources/`, which no plugin lifecycle
touches. Do not let this become a stage — it is one sentence, then back to the
literature.

Then, through `scholar.py --json`, which merges PubMed, Crossref, OpenAlex and
arXiv in one sweep and tags each row with the index it came from:

1. `search "<topic>" --limit 25 --from <year-5>` — recent work.
2. A second search restricted to **reviews** — a review states its own field's
   open questions, which is the highest-yield gap source there is.
3. `related <DOI-or-PMID>` and `cited-by <DOI-or-PMID>` on the two or three most
   central hits — `related` catches the vocabulary the user did not use, and
   `cited-by` catches what the paper led to, which is where a live gap shows.

Do **not** run this stage through `pubmed.py search`. A gap sweep that asks one
biomedical index and comes back empty cannot tell "nobody has done this" from
"this literature is not in PubMed", and for surface science it is always the
second: *Surface Science*, *Applied Surface Science*, *J. Vac. Sci. Technol.*
and *Vacuum* are not indexed there at all. An imaginary gap is the most
expensive mistake this stage can make.

**Read abstracts only here.** Triage — on topic, states a limitation, conflicts
with another paper — is answerable from the abstract, and pulling full text for
25 papers spends the context window on papers that turn out to be irrelevant.
Scan for gap language: *remains unclear*, *has not been established*, *future
work*, *limited to*, *we were unable to*; results that conflict between groups;
single reports never replicated.

**Escalate only what survives triage**, and only with a reason:

```bash
python <tools>/pubmed.py sections <PMID> --sections discussion,conclusions --json
```

"This is one of the papers that might establish the gap and I need its stated
limitation" is a reason. "It came back in the search" is not. Sections come from
PMC, which covers the open-access subset only; for a non-OA paper the abstract is
all there is, and the engine says so rather than erroring. **Never imply you read
a full text you did not.**

**Report the search, not just the conclusions** — the queries run, the counts,
the date range. These go into the bundle's `search` field and end up in
`ideas.md`.

**If a query returns almost nothing, say which it is.** For a chemistry topic
that usually means PubMed's coverage is thin, **not** that the field is empty.
Confusing the two is a listed failure mode.

## Stage 3 — informed clarifying questions

**This is why Stage 2 comes first.** Now, having seen what the field looks like,
ask the 2–4 questions whose answers change which gaps are reachable — each tied
to the consequence that makes it worth asking:

> "Are you doing single-particle analysis or tomography? The gaps split cleanly
> — the in-situ tomography literature has an open question about array symmetry
> across species, while the SPA side is much more crowded."

Batch them. Do not interrogate one at a time. Free-text answers are expected and
are more useful than the options offered — "mostly tomography but we have SPA
access" is a real answer, not an invalid one.

## Stage 4 — gap synthesis

**3–5 candidates, hard cap.** More and the user stops reading carefully. Each as
a compact block:

- **The gap** — one sentence, stated as what is not known.
- **Evidence** — 2–4 PMIDs, one phrase each on what that paper does or does not
  establish.
- **Why it is still open** — no one has the assay, results conflict, never
  replicated, assumed rather than measured.
- **What answering it would take** — in the user's own technique vocabulary from
  Stage 3.

**A gap needs a paper stating the limitation, or two papers that conflict.** An
absence you noticed is not a gap; inventing one from a plausible-sounding
absence is a listed failure mode.

Ask which are worth taking forward, and drop the rest now.

## Stage 5 — the feasibility dialogue

**Read the lab's `Resources/` folder before asking anything.** `idea.py context`
inventories it. It holds SOPs, training notes, and safety data sheets — the
lab's own record of what it can actually do. A question grounded in one is
better than a generic one:

> "Your negative-stain SOP puts screening at ~2 hours a grid, so a 12-condition
> screen is about three days of scope time — is that available, or should we
> scope this to six?"

Cite the SOP when a judgment rests on it, so the user can tell you it is stale.
Skip anything the SOPs already answer.

### A Pack Seeds the Questions. It Never Answers Them.

This is the rule that matters most in this whole stage, and it is easy to break
by accident. **A pack's numbers make a question SPECIFIC; a question is still
asked.**

The failure it forbids: holding a file that names the lab's instruments and
their throughput, you can produce a confident feasibility verdict without
asking anybody anything — and you will be right often enough that nobody
notices the times you are not. What a pack knows is what was true on its
`curated_on` date, for the standard case, in the general configuration. It does
not know that the detector was swapped in March, that the technique moved from
*shown* to *trained*, or that this particular sample behaves differently.

So:

- **Quote the pack's number WITH its curation date**, always. "Your SOP puts
  screening at ~2 hours a grid, as of the pack's 2026-09-09 curation" is
  answerable with "that's stale"; a bare number is not.
- **A pack value the user contradicts is the user's, immediately and without
  argument.** Add a note that the pack line looks stale so it can be corrected
  at the source later — never argue, never re-ask, never hedge.
- **Every existing rule below is untouched.** "idk" is still first-class,
  unknown is still not infeasible, and `methods_facts.yml` stays editable
  forever. Nothing a pack says makes a project value harder to change, and a
  project value never syncs back to a pack.
- **Nothing from a pack is ever written into a project uncommented.** If you
  offer a facility default, it arrives through `scaffold.py prefill`, commented
  out, tagged with its layer, and counted as MISSING until a human deletes
  the `#`. A pack is *more* dangerous here than an empty folder, not less — it
  is more specific and therefore more plausible.

### The Live Inventory — Offered, Never Automatic

The pack is a snapshot. The current stock is not in it, deliberately. When a
candidate turns on whether something is on the shelf, **offer** the read:

> "Want me to check the current stock in the lab inventory? Nothing it says
> gets written anywhere."

```bash
python <tools>/labpack.py inventory --json --item "<the thing>"
```

**Six rules.** The engine obeys them; your sentences have to as well.

1. **Offered, never automatic.** Ask first, every time.
2. **It answers a conversation, never a file.** An on-hand count is not a
   methods fact. It never reaches `methods_facts.yml`, `idea.md`, or any other
   artifact.
3. **Unknown is not out of stock.** No connector, no answer, an unreadable
   file, or a row that cannot be matched all produce *unknown*, and unknown
   never downgrades a candidate — it becomes a named load-bearing unknown, the
   same as "idk".
4. **Every quoted number carries its read time.** Never a bare number: a bare
   number is indistinguishable from a pack snapshot, which is the confusion
   this whole design exists to prevent. The engine returns a `phrase` field
   with the timestamp already in it — quote that.
5. **One read per run, and nothing is cached past it.**
6. **Read-only. No skill writes to the source. Ever** — not a correction, not
   a tidy-up, not a value you are confident about. It is somebody's working
   file. `lab.yml` records the role to tell instead.

**Read the `answers` field before you phrase anything.** A lab's inventory may
carry no quantity column at all — the Jensen lab's does not, measured
2026-09-09 — in which case it answers *do we own one, and where is it kept*,
and saying anything about how many are left is invention. The engine reports
`answers: "owned-and-where"` and a `caveat` for exactly this; and an item that
is **not listed** is *unknown*, not *absent*, because a row missing from an
ownership list may simply never have been added.

**If the connector route is the only one left**, the engine says so and stops
there — it cannot make a connector call, and that is the honest split
(`route: "connector"` in its JSON, and a `why` that names the file). What to
do about it, closing TODO.md §8's one remaining build question:

1. **Try it, and let ANY failure read the same as "not authorized".** Do not
   distinguish a clean permission error from a timeout, a malformed response
   or anything else a connector tool can raise — every one of them means the
   route is missing, and the feasibility conversation must not stop on a
   traceback. Catch it, say which route was missing, and continue with
   everything unknown, exactly as rung 3 already reads.
2. **Fetch by `lab.yml`'s `live.inventory.file_id`, never by a name search.**
   A search can return several files, an old copy among them, and picking the
   wrong one silently is worse than returning unknown. If a pack's `lab.yml`
   names no `file_id`, that is the same as the route being missing — say so
   and stop; do not search for the file by its `name` as a substitute.
3. **Save what comes back, then hand it to the engine — do not parse it
   yourself.** Write the connector's raw file content to a temporary path
   outside the pack and outside the project (never `resources/`, never
   `plan/`), then run:

   ```bash
   python <tools>/labpack.py inventory --json --connector-file "<the saved copy>" [--item "<the thing>"]
   ```

   `labpack.py` reads it with the same two readers it already uses for a
   synced `fallback_path`, so this is the parser this toolkit already tests
   (`tests/labpack.py`) rather than a second one living in a conversation.
   Discard the saved copy once the run answers — rule 5 still means nothing
   outlives the run, and that is now your responsibility rather than
   `lab.yml`'s, because nothing about a connector pull is recorded there.
4. **The read time still comes free.** `read_at` and the quoted `phrase` are
   the engine's, computed the moment it opens the file you handed it — there
   is nothing extra to track for rule 4 on this route.
5. **The no-quantity-column fact already survives the route change**, and it
   is not something this route has to re-derive: `answers` and `caveat` are
   read from `lab.yml`'s own `has_on_hand_column`, before either reader looks
   at a row, so "owned-and-where" versus "on-hand" never depends on which
   rung answered.
6. **Read-only, on this route too.** Fetch content; never upload a version,
   never write a comment, never touch a metadata field. The inventory is
   somebody's working file whether it reaches you through a synced path or a
   connector call, and rule 6 does not have an exception for either.

That is not a degraded mode to apologize for: it is this stage as it has
always worked, a conversation in which the user knows things the engine does
not.

### Sequence and Structure Evidence — Offered, Never Automatic

When a candidate turns on a protein, a gene, a genome or a phage, three
questions have an engine behind them: **what else looks like this**, **what
else is in this region**, and **is there a structure**. Spec:
`specs/user-asks-2026-09-18-second.md` §4.

```bash
python <tools>/sequence.py blast     --accession P58335 --program blastp --db nr --submit --json
python <tools>/sequence.py blast     --check <RID> --json
python <tools>/sequence.py neighbors --accession <acc> --window 10 --json
python <tools>/sequence.py fold      --accession P58335 --json
python <tools>/sequence.py status    --json
```

Everything it finds goes into `plan/evidence/sequence/` when you pass
`--project` — the query, the date, the program, the database **and the
database version string the service returned**, every hit at its returned
rank, and the RID or job handle. `nr` last month and `nr` today are different
databases, and a hit count without a version is unreproducible. An idea that
cannot be reproduced is not evidence.

**Four rules, and the first one is the whole section.**

1. **A BLAST miss is not evidence of novelty.** *"No homolog was found,
   therefore this is unstudied"* is the PubMed-found-nothing fallacy with a
   different index in front of it — measured in this toolkit, a full-text
   search for the very well-studied MOF HKUST-1 returns `total_count: 1`.
   Sequence evidence is evidence for **feasibility** and for **methods**. It
   is **never** evidence for a gap, and this stage may not promote a BLAST
   miss into a novelty claim. A gap still needs a paper stating the
   limitation, or two papers that conflict.
2. **It seeds the questions and never answers them.** Same rule the lab pack
   gets, and the forbidden failure is identical: the feasibility dialogue
   turning into a lookup. `neighbors` returning eleven genes is eleven things
   to ask the user about, not eleven findings.
3. **It must not run unasked.** No stage fires a BLAST search because a
   protein was mentioned. Offer it — *"want me to check what else looks like
   this? It takes a few minutes and NCBI asks for one search per ten
   seconds"* — and run it when the user says yes. The user asked for
   *automatic* in the sense of *"I should not have to leave and go do this by
   hand"*, which is what a CLI on the path buys, not in the sense of a
   network call per noun.
4. **It must not become a literature source.** `scholar.py` verifies
   citations. A sequence hit is not a citation and may not enter that cascade,
   for exactly the reason `structure.py` is a separate engine.

**`blast` is asynchronous and the CLI does not hide it.** `--submit` hands
back a Request ID at once and `--check <RID>` reads it; the plain form polls
with an explicit `--timeout`. Say which one you ran. A search still going when
the session ends is not lost — the RID is in `plan/evidence/sequence/`.

**`fold` reports the gap and then offers.** A hit in AlphaFold DB returns the
model and stops. A miss returns `status: "no_model"` with the sequence, its
length, and the routes that could produce one — **that is a real answer, and
it is not a gap**. Only then does it offer to submit, naming what submission
costs: a service, a credential, and a wait measured in minutes to hours.
Nothing folds because a lookup missed, and `--submit` with no credential
configured refuses by saying so rather than failing at the network.

Then, per surviving candidate, ask what it specifically requires — from the
candidate, not a fixed list. Cluster around: can you make or obtain the sample;
instrument access, and whether "access" means this month; sample quantity and
stability; whether a public dataset (EMPIAR, PDB, EMDB) would do instead; time
to first result.

**The rules here are the load-bearing part:**

- **"idk" is a first-class answer.** Record it as unknown and move on
  immediately. Never re-ask, never stall, never require a number.
- One candidate at a time, a few questions each. Never present a grid.
- Collect every "idk" into a **"worth finding out"** list — the unknowns that
  would most change the ranking. That turns uncertainty into a next action.
- **Unknown is not infeasible.** Do not quietly downgrade a candidate for an
  "idk"; name the load-bearing unknown and let the user decide.

**Keep these answers step by step, because they are what fills the lab pack
brief.** Every "we will do it this way", every "idk", and every number the
user corrected belongs to a particular step of the lab's route, and at the end
of Stage 6 they are written against that step in `plan/lab_pack_brief.md`. A
flat list of answers loses which step each one is about, and the step is the
part the user needs three weeks later.

Public datasets and literature-only outputs are legitimate winners, not
consolation prizes.

## Stage 6 — narratives and the float set

For the settled idea, offer **2–3 ways to write it up**, each with a working
title, **the single claim the paper makes** in one sentence, the framing, the
section order it implies, the float set, and a target journal tier.

A methods paper, a mechanism paper, and a scope paper can all come out of one
dataset. They are different papers.

**The float set is the important output.** 3–6 figures and tables, each with a
one-line statement of the claim it carries — figures decided as *claims* before
any plotting code exists, which is what produces a coherent set rather than a
pile of plots.

**Each figure also carries how it is made: plotted or drawn.** A chart is
plotted. A domain architecture, a mechanism, a reaction scheme, an apparatus, a
pathway or a sample-prep flow is **drawn**, and the words *graphic, schematic,
diagram, scheme* and *illustration* in the user's own request settle it. Put it
in the bundle as `"art": "drawn"` on that float, and say which each one is when
you show the set.

It matters because of what happens next: a drawn float goes to
**`create-graphic-figure`**, which authors the art as a PowerPoint slide the
user can reopen and edit, while a float that is not marked gets a bare
`figure.R`. A schematic hand-drawn in `annotate()` calls renders once and is
then editable only by somebody who will edit R — the cost is not a worse
picture, it is a picture the user cannot change (item 82).

Evaluate every option against the house constraints, which are written into
`plan/README.md` as standing rules:

- **Easy to understand.** One claim per figure. Order for the fewest forward
  references.
- **Engaging.** The first paragraph answers *why should I care*, not *what has
  been done before*.
- **Simple for the reviewer.** Every claim traces to a float or a cited PMID. A
  reviewer who cannot find the support for a sentence in ten seconds will ask
  for it in review.
- **Prefer the framing whose negative result is still interesting.** A candidate
  that is only publishable if it works is a fragile candidate.

## Citation integrity

**Every citation goes through `scholar.py verify` before it reaches any written
file**, and its verdict travels in the bundle:

```bash
python <tools>/scholar.py verify --pmid 22267509 --json
python <tools>/scholar.py verify --doi 10.1016/j.susc.2005.01.038 --json
python <tools>/scholar.py verify --title "<exact title>" --year 2005 --json
```

`scholar.py` asks PubMed first, then Crossref, OpenAlex, Europe PMC and finally
arXiv, and reports which index answered in `source`. arXiv is last so a preprint
never outranks its own published version; anything that verifies as one carries a
`PREPRINT` flag, which must reach the user rather than being quietly dropped. A chemistry or surface-science paper
has no PMID and is invisible to PubMed; it verifies here. Read `status` and
`flags` from the JSON exactly as before — the shape is a superset of
`pubmed.py verify`'s.

`idea.py write` refuses a bundle whose literature has no `status`, or a status
other than `verified` / `partial`, and refuses any PMID cited in prose that is
not in the retrieved set. Record whichever identifier the paper actually has —
a PMID, a DOI, or an arXiv id. Most of this group's own literature has no PMID,
so a DOI is the normal case, not the exception. **Never name a paper you have not retrieved.** No
citations from memory — the idea stage is where a bad citation is cheapest to
catch, and this is what `scholar.py` exists for.

A `not_found` from `scholar.py` is a stronger signal than one from `pubmed.py`
was: five indexes were asked, not one. It still is not proof of fabrication —
`sources_tried` and `unreachable` say what was actually reached, and a source
that failed makes the result incomplete rather than negative.

Retractions and expressions of concern are carried through in `flags` and
written with the flag shown. A `verified` paper can still be one you should not
cite.

## The bundle

Nothing is written until Stage 6 settles, then all at once, **always previewed
first**. Build one JSON bundle and hand it to the engine:

```bash
python <tools>/idea.py write "<project>" --bundle idea.json --dry-run   # show
python <tools>/idea.py write "<project>" --bundle idea.json             # write
python <tools>/idea.py write "<folder>"  --bundle idea.json --mode explore
```

`tests/idea_bundle.json` is a complete, realistic worked example — read it
rather than reconstructing the shape from this table.

| field | goes to |
|---|---|
| `project` | `title`, `short_name`, `target_journal` in `project.yml` |
| `background`, `gap` | `plan/README.md` |
| `question`, `hypothesis`, `prediction_if_true`, `prediction_if_false` | `plan/README.md` |
| `design` | the study-design table |
| `narrative`, `open_questions` | `plan/README.md` |
| `floats[]` — `label`, `claim`, optional `folder`/`slug` and `description` | `plan/captions.md` blocks, the float-set table, **and a folder per float** |
| `outline[]` — `section`, `line`, `evidence` | `plan/outline.md`, one line per paragraph |
| `literature[]` — `pmid`, `status`, `flags`, `title`, `authors`, `journal_abbrev`, `year`, `doi`, `why` | the literature summary |
| `data_files[]` — `file`, `group_column`, `columns[]` with `name`/`kind`/`units`/`levels`/`mock` | `data/data_contract.md`, `data/templates/*.csv`, the mock generator |
| `mock` — `n`, `seed`, `hypothesis` | the header of `generate_mock_data.py` |
| `search`, `ideas`, `narratives` | `plan/ideas.md` |
| `query_name`, `takeaway` | `summary.md` in an `Ideas/` folder — the 3-4 sentence version |
| `methods_proposed` — block → key → `value`, `source`, `why` | `data/methods_proposed.yml` |

**A float is a folder, and `write` makes it.** One float, one folder:
`plan/figures/Fig01_<slug>/figure.R`. The folder name is derived from the
`label`, so the number in the folder and the number in the caption heading have
one source and cannot disagree. Give a `slug` (or a whole `folder`) when a
readable name helps; the number is the identity either way, so a slug can be
added later without touching anything else.

`write` never renames a folder holding work. It *will* rename one of the
scaffold's untouched slots — `Fig02` to `Fig02_symmetry_by_species` — because a
slot still byte-identical to the template is nobody's work yet.

**Column `kind` is not decoration** — it decides both the geom and the
statistical test, so guess it now. One of `continuous`, `count`, `proportion`,
`categorical`, `ordinal`, `identifier`, `datetime`.

**`mock` per column is what makes the synthetic rows worth having.** Give each
group's parameters — `{"thermophile": [0.82, 0.05], "mesophile": [0.71, 0.05]}`
for a continuous column, a rate for a count. The rows then encode *what we
expect to see if the hypothesis holds*, so a figure built on them tests the
figure and the claim before a sample is prepped. **Include the control that
should show no effect** — a mock dataset where everything differs proves
nothing. Columns you say nothing about are written blank, on purpose, and
reported.

`ideas.candidates[]` must carry the dropped ones, each with `why_dropped`. **The
rejected candidates are the valuable part of `ideas.md`** — they are what stops
the same ground being re-covered in three months.

### Reading the result

`errors` refuse the write and name what is wrong. `warnings` do not — they are
the judgment caps (3–8 papers, 3–6 floats, 3–5 candidates) and conventions.
Surface warnings to the user rather than silently accepting them.

## Where a settled idea gets saved — three destinations

Asked at the end of Stage 6, once the idea is settled, and **previewed in full
before anything is written**.

| # | Destination | Command |
|---|---|---|
| 1 | `Ideas/{Query Name}/` — thinking that has not earned a project | `idea.py write "<folder>" --mode explore` |
| 2 | **a project that already has work in it** | `idea.py merge "<project>"` |
| 3 | a new project directory | `setup-project-directory`, then `idea.py write` |

### Destination 1 writes four files

`summary.md` first — the fifteen-second read: 3-4 sentences of the takeaway,
then 3-4 sentences per paper, then the citation list. Then
`relevant_literature_summary.md`, `refs.bib` (the machine-readable copy of the
same set, which is what makes the folder upgradable later), and `ideas.md` (the
record: candidates dropped, searches run, feasibility answers as given).

The 3-4 sentence caps are **warnings, not refusals** — they are judgment caps
like every other one here. But they are reported, and that is what stops a
one-line note or a page of prose being written and forgotten.

### Destination 2 is additive, and that is enforced

```bash
python <tools>/idea.py merge "<project>" --bundle idea.json            # dry run
python <tools>/idea.py merge "<project>" --bundle idea.json --apply
```

**A dry run is the default**, because a merge must never be a surprise. Every
row in the preview is ADD, APPEND, NEW or PROPOSE; there is no MODIFY row, and
the writer refuses to commit one. Read the preview to the user before `--apply`.

Four things it does, and the rules that matter:

- **Guiding papers** go to `plan/relevant_literature/` and its `refs.bib`, and
  **never to `drafts/references.bib`** — the submission bibliography is derived
  from what the manuscript cites and drops the rest, so a motivating paper
  written into it is either silently dropped or pushed into a paper that does
  not cite it. A paper already in the project is reported as already present,
  matched on DOI, then PMID, then title.
- **The takeaway** appends one dated block to `plan/README.md`, and the
  rejected candidates append to `plan/ideas.md`.
- **Proposed methods** is a new file (`data/methods_proposed.yml`), so it is
  additive by construction. An existing one is skipped, never regenerated over.
- **The outline and the captions** are proposed, not written in place. An empty
  outline is filled (with the PROPOSED banner on it, because the lines are
  yours and not the user's); one that already has lines gets a
  `plan/idea_proposal_<date>.md` beside it instead.

**Two things stop the merge and ask you a question.** Neither writes anything:

- **The project is being drafted.** If `drafts/` holds a `manuscript_rN.docx`,
  only the guiding papers and the takeaway are applied; everything else goes
  into the proposal note. Say so plainly: *"`manuscript_r2.docx` exists, so
  this project is being drafted. I'll add the papers and the takeaway and leave
  the outline alone — here is what I would have proposed."*
- **The guiding-paper cap.** Past eight papers the merge is **blocked**, not
  warned. Show the combined list, ask which to keep, then re-run with
  `--cap-ack`. Nothing is dropped silently and nothing exceeds the cap
  silently.

Afterwards, run the `check-refs` the merge names in `next` — a merge that
breaks the project's bibliography must not be discovered three weeks later by
the writing engine.

### Proposing methods

Offer it, once the guiding papers are settled: the Methods sections of the
guiding papers and the lab's own SOPs both carry values the user would
otherwise guess at. They go to `data/methods_proposed.yml`, **never** to
`data/methods_facts.yml`.

**That separation is the whole point.** A value in `methods_facts.yml` is a
fact about what we did, and `writing-engine` renders it into prose — so a
`blot_time_s: 4` copied out of somebody else's paper becomes a number in the
methods section that nobody measured. It will look right, because it came from
a real paper about a similar experiment, and that is exactly what makes it
dangerous. A missing value becomes a `**[FLAG: author]**` instead, which is the
correct behaviour and needs no new logic anywhere.

Every entry is a block with three keys, and `source` is **mandatory** with only
two legal kinds:

```yaml
acquisition:
  total_dose_e_per_A2:
    value: 50
    source: "PMID 22267509 - Methods"        # a citation AND its section
    why: "Dose used for the closest published tomography of this array type"
sample_prep:
  glow_discharge:
    value: "25 mA, 30 s, air"
    source: "Resources/Standard Operating Procedures/negative_stain.md"
    why: "The lab's own documented setting - likely correct as-is"
```

A proposed value with no source is not a proposal, it is a guess, and the
engine refuses the bundle. So does a citation with no section, and so does one
naming a paper that is not in the retrieved set — `source` is a citation, and
the citation-integrity rule applies to it like any other.

A value from the lab's own SOP is usually right as written; one from a paper
usually needs converting to local conditions, and that is what `why` is for.
**Present the proposals grouped by block and ask which to keep.** The file is
short and useful, or it is a wall of plausible numbers nobody reads.

## After the write

Show what was written, then say what is now true: the float set exists as
claims, the data contract exists before the data, and
`data/mock_data/*_mock.csv` means every figure script can run from day one.

The obvious next step is one figure script. `write` has already made a folder
for every float it named — `plan/figures/Fig01_<slug>/figure.R` and so on — so
open the first one, flip its `BUILT` guard to `TRUE`, point it at the mock CSV,
and run `Rscript plan/render_all.R`. That exercises the whole pipeline and puts a
watermarked draft of the paper's central figure on screen in an afternoon. The
co-author `.docx` is refused while any input is mock, which is correct and not
an error to work around.

**Explore-only hand-off.** End with: *"Want to turn this into a project
directory?"* If yes, call `setup-project-directory` with the suggested name,
then re-run `write` in project mode — **moving** the PDFs and summary into
`plan/relevant_literature/` rather than re-downloading them.

### The Lab Pack Brief — Written, Not Offered

**If a pack resolved at Stage 1, write the brief now**, in the same breath as
the rest of the write. Spec: `specs/lab-pack-brief-2026-09-24.md`.

```bash
python <tools>/labpack.py brief --project "<project>" --bundle brief.json --dry-run
python <tools>/labpack.py brief --project "<project>" --bundle brief.json
```

It puts `plan/lab_pack_brief.md` in the project: **every step of this lab's
route, quoted from the pack verbatim and dated**, with this project's
decisions and open questions written against the steps they belong to. It is
what the user asks questions of at the bench three weeks from now, when the
conversation this skill just had is gone.

The bundle is this project's half, and **every field of it is optional** — a
brief with no bundle is still worth writing:

```json
{
  "claim": "the single claim from Stage 6",
  "techniques": ["the vocabulary from Stage 3"],
  "data_plan": "one line on what is being collected",
  "steps": [
    {"step": "<the pack's own heading, exactly>",
     "note": "what this project does at this step",
     "decided": ["a value the user settled in Stage 5"],
     "unknowns": ["a load-bearing unknown from the 'worth finding out' list"]}
  ]
}
```

**Five rules, and each one is a way this goes wrong.**

- **Every `note`, `decided` and `unknown` is something the user SAID.** This
  is not a place to summarise the SOP back at them, and not a place to decide
  a value they did not decide. The pack's half is already in the file,
  verbatim; anything you add that they did not say is the only unsourced
  sentence in it.
- **The "worth finding out" list from Stage 5 belongs here, split by step.**
  That is the one thing this file does that `plan/ideas.md` cannot — it puts
  each unknown beside the step it blocks.
- **A `step` must be the pack's heading.** The engine matches on case,
  padding and punctuation and nothing looser, and **refuses the write** when
  a step does not exist, listing the ones that do. Read the headings off
  `sops.md` (or whatever `lab.yml`'s `workflow_source` names) rather than
  typing them from memory.
- **Never write it under `data/`.** The engine refuses, and the reason is
  `lab-resource-pack.md` §4.3: a pack default sitting beside
  `methods_facts.yml` is how a facility number reaches print wearing the
  shape of a checked fact. Nothing in this file is a methods fact and the
  file says so in its own header.
- **No pack, no file, no sentence about it.** Exactly like Stage 1: a machine
  with no pack behaves as it always has. Two packs stop and ask, once.

Say one line about it when you show what was written — where it is, and that
the `## Your Notes` section at the end is theirs and survives every
regeneration. Re-running it later is free: `--check` says whether the pack has
moved since.

## Things that are wrong to do here

- **Dumping ten ideas at once.** The stages exist to prevent it.
- **Clarifying questions before the search.** Stage 3 after Stage 2, always.
- **Stalling on "idk."**
- **Confusing "PubMed found nothing" with "nobody has done this."**
- **Inventing a gap from a plausible-sounding absence.**
- **Letting `relevant_literature/` become a dump.** The 3–8 cap is a design
  feature — it answers "why is this paper worth writing", and stops answering
  the moment it grows.
- **Re-asking what `project.yml` or `plan/README.md` already records.**
- **Filling a section the user has written in.** The engine appends under it
  with a dated marker; do not work around that.

## When the Engine Itself Is Wrong

**File it. From this stage, not only from the writing one.** If the engine does
something during developing the idea that it cannot justify — refuses a valid input, reports
a number it cannot have measured, writes a file in the wrong place, exits 0 on
a check that never ran — that is a defect in the toolkit, and it is lost with
this session unless you record it:

```bash
python <tools>/manuscript.py log-issue "<project>" --stage idea     --code a_short_slug --detail "what happened, GENERICALLY"     --example "what happened on THIS project"     --title "one line" --wanted "what it should do"     --verify "how the next run answers 'is it still there?'"
```

Three things about this command, and each of them has cost something already:

- **`--stage idea`.** Unstated, the stage is inferred, and the inference is
  built around the writing stage's journal and round — both of which are empty
  here. The record marks an inferred stage as inferred, so a wrong guess is
  visible rather than silent, but saying it is better than being guessed at.
- **`--detail` is generic; `--example` is the one place a project specific may
  live.** `system-changes.md` is read by everyone who has the toolkit, so the
  description has to be true of every project this could happen on.
  Write *"the engine dropped a row from the table"*, not *"it dropped sample
  ZQx7"*. A sample id or a composition in `--detail`, `--title`, `--wanted` or
  `--verify` is **refused** by name; `--example` is never linted.
- **`--verify` is required and is not a formality.** An item the next run
  cannot check is an item nobody can ever close.

The project argument is positional and defaults to the current directory, so
this works before a project exists. `--list-codes` prints the codes the engine already files by itself —
reuse one if it fits. If you fixed it in the same session, add `--fixed`, and
`--detail` then says what you changed.

**Only engine misbehaviour goes here.** A missing file, an empty template, a
question the user has not answered yet are the project's business and are
already reported where they belong.
