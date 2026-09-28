# writing-engine — rounds and resuming

Read this when a round **opens or closes**, or when `status` says a run was
left open. Nothing here is needed to draft a section or to build a document,
which is why it is not in the spine: it answers *where does the last round
go*, *what happens to the file somebody has already edited*, and *where did
the interrupted run get to*.

**Clearing the context mid-run is NOT here — it is in the spine**, because it
fires during any run and a context that has just been cleared is the one case
that cannot rely on having read a stage file first.

`manuscript.py handoff --stage rounds --json` hands a fresh context this
file and the run state together.

## Rounds

```bash
python <tools>/manuscript.py round "<project>" --journal IJROBP
```

Run this **once, when round N+1 opens** — not from `render_all.R`, which runs
thirty times while you iterate on a panel.

**The journal folder holds the live round and the standing files. Everything
else is that round's record, and a round's record retires whole.** This
reverses the earlier rule — every `manuscript_rN.docx` used to stay in the
journal folder — on the user's instruction after seeing the folder: the root
mixed bookkeeping, inputs (`journal_requirements/`, and `edits/` as it then
was) and the one
artefact that is actually sent, while `submission/` — the folder named for the
thing that is sent — stood empty. "What goes to the journal right now" had no
single answer in the folder, and by r5 it had five.

**The bookkeeping is three files now, not five (item 42).** Opening the folder,
the user could not tell which of `log.md`, `run_log.md`, `round_state.json`,
`run_state.json` and `writing_config.yml` to read, and two of them held
nothing they did not read out of the other two.

| now | was | why |
| --- | --- | --- |
| `drafts/log.md` | one `log.md` per journal folder | the round counter is the project's, so a per-journal history is one sequence split across folders. Existing per-journal files are merged in, newest round first, and deleted only after the merge is read back |
| `{JOURNAL}/round_state.json` | `round_state.json` + `run_state.json` | one state file per journal: the round, its scope, how far the open run got, and the SHA-256 of everything written. It stays in the journal folder because that is where you look to see what *this* journal is on |
| `{JOURNAL}/writing_config.yml` | unchanged | the dials, the standing instructions and `manages` differ per journal by design |
| `{JOURNAL}/reports/rN/run_log.md` | `run_log.md` in the folder root | still append-only — a state file can be rewritten and "how far did the run that died get" needs an answer nothing overwrites — but with the rest of that round's record, so `round` retires it |

| Stays in the journal folder | Retires to `obsolete/drafts/rN/` on `round` |
| --- | --- |
| `writing_config.yml` | `submission/` — the manuscript and its package |
| `round_state.json` | `reports/rN/` — that round's reports and its run log |
| `journal_requirements/` | the received files in `drafts/edits/` for round N |
| — | (already) the `source_text/` snapshot |
| | (already) that round's floats and scripts |

**Two exceptions, both deliberate.** `log.md` is the history and does not
retire — it is one file of prose, newest round first, and since item 42 it is
not in the journal folder at all. `drafts/edits/edits_status.md` does
not retire either, though the `.docx` and `.pdf` files beside it do:
final-check re-verifies that *every* `applied` item is still present — not
just last round's, every one, every round — and retiring the ledger with its
round would break that check silently.

`obsolete/drafts/rN/` is then the complete record of one round: what the text
said, what was sent, what the checks found, and what came back. Its
`manifest.md` names the journal at its head and lists every moved file, so the
folder reads alone.

**Round numbers are the project's, not the journal folder's.** LANGMUIR stops
at r7 and a JPCC opened beside it starts at r8, so `manuscript_r8.docx` is
unambiguous across the whole project — where a folder-local counter gave one
project two files called `manuscript_r1.docx` meaning different things.
`project.yml:revision` is the counter, `project.yml:rounds` is the ledger of
which journal each round belonged to, `target_journal` follows the open round,
and every `drafts/log.md` entry is headed `## rN — JOURNAL — date`. A journal
change is one event with one record, and the r7/r8 boundary reads as the
rejection it was — in one file, which is the whole reason the log is the
project's rather than each journal's.

What rolls into `obsolete/` besides the round's record is the `source_text/`
snapshot, plus the entire float set — rendered files, the `.rds` flextables,
**and the generating `.R` scripts**.

**Snapshots are unconditional, byte-identical copies included.** The instinct to
skip unchanged files defeats the point: the guarantee is that
`obsolete/figures/r3/` *is* what r3 contained. A sparse archive forces the
question "was it unchanged, or did the snapshot fail?". Each round's
`manifest.md` carries a SHA-256 per file and a "changed since r(N-1)" column, so
"did Figure 2 actually change" is answered by diffing two text files.

## A round that already exists

A second invocation on a project that has already been built is the common
case, not the exception, and it is the one where this pipeline can destroy
work. **The built `manuscript_rN.docx` is an output. People edit it anyway** —
it is the file they were sent, it is the file that opens in Word, and marking
it up is the obvious thing to do with it. A rebuild replaces it.

`assemble` now records the SHA-256 of every manuscript it writes and compares
it on the next build: unchanged, it overwrites silently; **changed or with no
build on record, it refuses**, names the file, and says the change has to go
through `edits/`. `--force` overwrites anyway and loses the edits. Do not
reach for `--force` on the user's behalf — the refusal exists because the file
is where people make their edits.

Before planning anything on a project where `status` shows a round already
built:

1. **Look at the built file before you overwrite it.**

   ```bash
   python <tools>/docx_edits.py extract "<project>/drafts/<JOURNAL>/manuscript_rN.docx" --json
   ```

   Tracked changes, comments, a different author in the OOXML — any of those
   means somebody has worked on that file and a rebuild would delete it. If
   there are, **stop and say so**, and offer to copy it into `edits/` and
   `ingest` it. Never move or overwrite it without asking.

2. **`status` reports `edits_inbox`. Read it.** Files there are the supported
   path and `ingest` is what reads them.

3. **If there are no edits anywhere — in `edits/`, in the built `.docx`, and
   nothing new in `source_text/` — ask why you are being run.** A rebuild that
   changes nothing costs a round and risks the file. The honest question,
   asked plainly:

   > This project already has `manuscript_r1.docx` from 2026-09-03 and I can't
   > find any edits to fold in — `edits/` is empty and the built file carries
   > no tracked changes or comments. What would you like this run to do?
   > Rebuild it unchanged, redraft a particular section, or something else?

   Do not guess. "Run it again" and "run it again because I changed the
   outline" lead to different plans, and only the user knows which this is.

**The edits win, and the drafts do not have to be redone.** When there *are*
edits, they are the work order and they set the scope:

- **Apply what the edits ask for, and nothing else.** A section nobody
  commented on does not get redrafted. Re-running `draft-sections` over the
  whole paper to satisfy one comment throws away every hand edit in the
  sections nobody mentioned, and then `final-check` has to verify back in what
  the same run just removed.
- **Redraft a section only when the edits dictate it** — a comment asking for
  a restructure, a reviewer point that needs a new argument, a tracked change
  that invalidates the paragraphs around it. Say which sections you are
  redrafting and why, from the `edits_status.md` rows, before you start.
- **`--preset revision` currently plans `draft-sections` for every section
  regardless** (`system-changes.md` item 15). Scope it yourself: run
  `draft-sections` only for the sections with pending items, and record the
  rest with `run --step draft-sections --item <section> --state skipped`.
- **Nothing outside the edits changes.** If you think a section unrelated to
  any edit should also change, that is a proposal to put to the user, not a
  thing to do in the same pass.

## Picking up an interrupted run

A full run is several agent calls over a lot of text, and it stops in the
middle for reasons that have nothing to do with the paper: usage runs out, a
session closes, a machine reboots. **The next invocation must be able to
continue rather than start again**, and that only works if every module says
where it got to as it goes.

```bash
python <tools>/manuscript.py run "<project>" --journal IJROBP --start --preset coauthor
python <tools>/manuscript.py run "<project>" --journal IJROBP \
    --step draft-sections --item results --state done \
    --wrote drafts/source_text_r8/results.md
python <tools>/manuscript.py run "<project>" --journal IJROBP \
    --step draft-sections --state done
python <tools>/manuscript.py run "<project>" --journal IJROBP --finish
```

**Record a step the moment it finishes, not at the end of the run.** A record
written only at the end is exactly the record that is missing when the run
dies. Rules that follow from that:

- **Use `--item` for anything a module does more than once.** `draft-sections`
  writes five section files and can die after three; without items, the resume
  point is "draft-sections" and all five get rewritten.
- **Pass `--wrote <path>` for every file the step produced.** It is stored
  with a SHA-256 and re-checked on every read, so a resume is told when a
  recorded output has since been edited by hand or has gone missing. The
  ledger is never believed over the folder.
- **`--wrote` on a section file is also what arms the freeze, so it is not
  optional for a module that writes prose.** The freeze is inferred by
  comparing a section against the hash the engine recorded when it last wrote
  it, and a module that finishes without saying what it wrote leaves no hash —
  so the section reads *fresh*, not *frozen*, and the user's next hand edit to
  it is indistinguishable from the engine's own writing. `run --step` records
  the hash for every `--wrote` path that is a section of the live source text,
  on a terminal state only, and reports which sections it claimed. The plan
  says how many sections are frozen and how many carry prose with no hash of
  record, so **a count of zero is a measurement rather than a silence**.
- **`--state failed` is a real answer.** A module that failed is recorded as
  failed with a note, so the next session works on it rather than rediscovering
  it.
- **`--finish` refuses while any module is still pending.** Mark the ones that
  never ran as `skipped` with a reason. A run closed as finished while modules
  silently never ran is the record claiming work the pipeline did not do.
- If the user stops for the day mid-run, leave the run **open** — that is what
  the next session reads. `--abandon --note "..."` is for a run that is being
  given up on, not one that is being paused.

Two files carry this. `{JOURNAL}/round_state.json` is the record — the run
lives under its `run` key, beside the round and the scope it belongs to — and
`{JOURNAL}/reports/rN/run_log.md` is the same events in order, in prose,
append-only — it is the file that still answers "how far did the run that died
get" after the state file has moved on. Neither replaces `drafts/log.md`,
which stays what it always was: the prose account of every round, for you and
your coauthors.

**On resume, say what you are doing before you do it:**

> Last run stopped in `draft-sections` after introduction and results. Picking
> up at methods; discussion, evidence-check, stats-check, assemble and
> citation-check are still to come. Nothing before it needs re-running.

If `stale_outputs` names a file, raise it: a section recorded as drafted that
has since changed was probably edited by the user, and re-drafting it would
throw that away.
