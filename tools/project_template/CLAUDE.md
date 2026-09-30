# {{title_or_name}}

{{one_liner}}

Scaffolded {{created}} by `setup-project-directory`. Machine-readable state
lives in `project.yml` — title, target journal, revision number, seed. Read it
rather than guessing any of those.

## Where things are

| path | what |
|---|---|
| `plan/README.md` | **the living project document** — background, question, design, the float set, the narrative. Start here |
| `plan/outline.md` | one line per paragraph of the finished paper, each with its evidence in brackets. The claims ledger |
| `plan/captions.md` | **source of truth for every float caption**, and for float order |
| `data/data_contract.md` | the column contract every script depends on. First thing to check when a script fails on a missing column |
| `data/methods_facts.yml` | materials, methods, software, notes — the four things a methods section is made of, filled in **as the work happens** |
| `plan/lab_pack_brief.md` | **if this lab has a resource pack** — every step of the lab's route, quoted from the pack and dated, with this project's decisions and open questions against each one. Ask it things. It is a **source, never a result**: nothing in it is a methods fact, and `## Your Notes` at the end is yours and survives regeneration (`labpack.py brief`) |
| `plan/methods_notebook.md` | **if this lab has a resource pack** — the bench to-do list: what the project needs, then each step as tick boxes, with the pack's own warnings quoted. A **to-do list, never a methods section** — nothing in it is a methods fact, and `writing-engine` never reads it. Ticks and `## Your Log` survive regeneration (`labpack.py notebook`) |
| `data/analysis/analysis.R` | the project's one analysis script - it holds all the tests |
| `drafts/source_text_rN/` | the editing surface: one `.md` per manuscript section. The `rN` is the round it holds — `manuscript.py round` renames it forward, so a journal folder left at an earlier round is visibly not what would build today |
| `drafts/edits/` | the inbox: a coauthor's returned `.docx`, a reviewer report, an editor's letter. One for the project, beside the source text and not inside a journal folder — a coauthor returns to the folder they were sent to, and nobody told them which journal this round is aimed at |
| `plan/author_information/authors.md` | the author list, and the contribution matrix under it — put an `x` in every column that author did. The CRediT statement is written from it. In `plan/` and not the source text, because the source text folder is renamed forward every round and an author list is not a per-round thing |
| `plan/author_information/affiliations.md` | addresses keyed `A`/`B` so each is typed once, the grant table, and the statements that go with them |
| `plan/author_information/conflict_statements/` | the signed competing-interest forms, one file per author, named with their initials. `submission-package` counts them and says who has not returned one; it never opens them |
| `obsolete/` | mirrors the live tree; retired things keep their shape |

## `.here` — do not delete

The zero-byte `.here` file at the project root is what makes `here::here()`
resolve project paths regardless of the working directory. Without it `here()`
walks up to some ancestor — OneDrive's root, at worst — and every path silently
points somewhere wrong.

## GitHub — optional, and not the engine's job

The paper engine does not put this project on GitHub and does not sync it.
[github-ai-project-manager](https://github.com/mercer-science/github-ai-project-manager)
does, with or without the engine. When the user asks to put this project on
GitHub or keep it in step between computers, run
`python <tools>/scaffold.py github-offer . --json`: it says whether the
manager is installed and gives the command that hands over to it. **What is
kept off GitHub is always the user's answer to the manager's question**,
never a rule of the engine's.

## Rendering floats

```bash
Rscript plan/render_all.R          # runs every figure and table script, then
                                   # plan/preview.html and plan/floats/*.docx
```

**One float, one folder.** Figure 1 is `plan/figures/Fig01/`, and everything it
owns lives inside — `figure.R`, `figure.png`, `figure.pdf`, and any PowerPoint
panel art. The script is `figure.R`, not `fig01_yield.R`, because the folder
name carries the number: **renumbering a figure is renaming its folder**, and no
file inside it moves.

`render_all.R` runs every `.R` in every float folder, `figure.R` / `table.R`
last — panel art has to be rendered before the script that composes it reads it.
Folders beginning with `_` are skipped, which is what keeps `_template/` out of
the run. A loose `.R` sitting directly in `plan/figures/` is **reported, not
run**; move it into a folder, or run `scaffold.py float migrate`.

**Adding a float.** Fill in the next empty slot — the scaffold pre-creates
`Fig01`–`Fig04` and `Table01`–`Table02`, each holding a template script behind a
`BUILT <- FALSE` guard so an unbuilt slot renders clean and says so. Flip that
to `TRUE`, check column names against `data/data_contract.md`, use
`save_float()` with no name (it writes into its own folder), add a
`## Figure 1 — Fig01` block to `plan/captions.md`, run
`Rscript plan/render_all.R`, and open `plan/preview.html`.

Need another slot, or a different order:

```bash
python <toolkit>/tools/scaffold.py float new . --figure --slug yield
python <toolkit>/tools/scaffold.py float renumber . --from 3 --to 2
```

`renumber` moves the folder **and** rewrites `plan/captions.md`, blocks
included — document order in that file is manuscript order, so the two must not
be edited apart.

A folder name may carry a slug for readability (`Fig01_yield_by_catalyst`). The
caption block resolves by **number**, so adding one changes nothing else.

## Mock floats

A script carrying `# --- mock float (setup-project-directory) ---` was
written against `data/mock_data/` to give the project a day-one preview. It
runs, and **what it shows is not a result.** Every mock figure is stamped
MOCK DATA and the co-author `.docx` refuses to build while any float is
downstream of `data/mock_data/` — a `REFUSED` line from `render_all.R` is the
safeguard working, not a broken scaffold.

To turn one into the real float: point `load_data()` at the file in
`data/raw/`, check the column names against `data/data_contract.md`, rewrite
the caption in `plan/captions.md` as the claim it actually makes, and delete
the banner. Nothing else has to change — same folder, same number.

Regenerate them (only slots still behind `BUILT <- FALSE` are touched):

```bash
python <toolkit>/tools/idea.py     mock        . --bundle mock.json
python <toolkit>/tools/scaffold.py mock-floats . --bundle mock.json
```

## Panels that are drawings, not plots

A reaction scheme, an apparatus diagram, a mechanism — those are authored as
**one PowerPoint slide** in the figure's own folder:

```bash
python <toolkit>/tools/graphic_figure.py add . --figure 2 --panel A
```

That writes `plan/figures/Fig02/panelA_source.pptx` (yours — open it in
PowerPoint and draw; **do not resize the slide**) and a `panels.R` that renders
it to `.png` whenever the slide changes. `Rscript plan/render_all.R` stays the
only command you run.

Compose it beside ordinary plotted panels:

```r
p_a <- graphic_panel("panelA_source.png", title = "Reaction scheme", tag = "A")
p_b <- panel_title(ggplot(d, aes(x, y)) + geom_point(), title = "Yield", tag = "B")
save_float(p_a | p_b)
```

Art that does not exist yet renders as a dashed placeholder, so a figure can be
composed before any of it is drawn. Use `graphic_panel()` rather than a bare
`image_panel()` when the panel has a title, and state `height =` once the layout
has more than one row — `save_float()` sizes for a single row otherwise.

## Rules that are load-bearing

- **`save_float()` and `save_table()`, never a bare `ggsave()`/`write_*()`.**
  They are what make the `.png`/`.pdf` pair, the journal geometry, the
  mock-data watermark, and the legibility check automatic.
- **Figure scripts plot from `data/raw/`, and never recompute a test.** They
  do not read the analysis at all. A p-value printed on a panel is typed from
  `data/analysis/analysis.md`, which is where the results paragraph takes it
  from too - that is how the asterisk on the chart and the number in the
  sentence stay in agreement. An inline `t.test()` in `figure.R` drifts from
  the results section the first time a row is excluded, and both still look
  right.
- **In-panel labels use `annot_label()` and `theme_inside_legend()`.** A stat
  label in a panel corner and a legend parked inside a panel share one
  "fogged" white-at-70% style defined in `plan/theme/theme_journal.R`. Six
  panels styled six ways stop reading as one figure, and a legend below a
  panel steals height from every panel in its row.
- **A multi-panel figure tags each panel explicitly** —
  `panel_title(p, title = ..., tag = "A")`, not
  `plot_annotation(tag_levels=)`, which recurses into a nested composite and
  burns extra letters.
- **Every label is Title Case.** Panel titles, axis and legend titles, facet
  strips, annotation text, schematic labels and table column headers:
  `Growth Temperature (°C)`, never `growth temperature (°C)`. Gene and protein
  symbols, species names, units and quoted matter keep their own casing, and
  the caption sentence is a sentence — this is a rule about labels.
- **Captions are edited in `plan/captions.md` only.** The preview, the Word
  floats, and the manuscript all read it through `read_captions()`. Nothing
  else parses that file.
- **Mock data is watermarked and cannot reach the co-author `.docx`.** Anything
  read from `data/mock_data/` stamps its figures and hard-stops
  `create_floats.R`. Override deliberately with `FORCE_MOCK=1`.
- **Nothing is written that was not recorded.** Methods prose comes from
  `data/methods_facts.yml` and `data/analysis/provenance.json`; claims come
  from the evidence bracket on each `plan/outline.md` line. A missing value
  becomes a visible `**[FLAG: ...]**`, never a plausible guess.

## Retiring things

`obsolete/` mirrors the live tree: `plan/figures/x.R` → `obsolete/figures/x.R`.
`figures/`, `tables/`, and `drafts/` retire per round (`r1/`, `r2/`, …) — a
full snapshot each time, so `obsolete/figures/r3/` is exactly what round 3
contained. `analysis/` and `notes/` stay flat.

A round's record is the whole round: `obsolete/drafts/r2/` holds `source_text_r2/`
(that round's prose, in a folder named for the one it came out of), and beside it
the `submission/`, `reports/` and `edits/` that retired with it.

A journal you are no longer sending to retires too, whole, into
`obsolete/drafts/<JOURNAL>/` — `manuscript.py retire-journal`, which is offered
and never run for you. `drafts/` then holds one live journal folder.

**`obsolete/` is a record, never an input.** Nothing drafts from it and nothing
sweeps it. One check reads it, to make sure round 3 has not quietly dropped a
sentence round 2 had; nothing puts a retired sentence back.
