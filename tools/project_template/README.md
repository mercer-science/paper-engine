# {{title_or_name}}

{{one_liner}}

A short map of this folder. The working document is **`plan/README.md`** — the
background, the research question, the design, and the current state of the
argument all live there.

```
project.yml     title, target journal, revision number  (machine-readable state)
CLAUDE.md       project brief, conventions, the float loop

data/
  data_contract.md    every column: name, kind, units, range
  methods_facts.yml   instrument settings and software versions, filled in as you work
  templates/          header-only CSVs defining the contract
  raw/                the real data
  raw_images/         micrographs, spectra, gel scans
  mock_data/          hypothesis-shaped synthetic data, for building figures early
  analysis/           analysis.R and its outputs

plan/
  README.md           THE LIVING PROJECT DOCUMENT
  outline.md          one line per paragraph, with the evidence for each
  captions.md         float captions and float order (source of truth)
  author_information/ everything about the people on the paper
    authors.md          author list + the CRediT contribution matrix
    affiliations.md     addresses, grants, and the end-matter statements
    conflict_statements/  the signed COI forms, one file per author
  relevant_literature/  PDFs, refs.bib, reading notes
  figures/  tables/   one script per float
  theme/              journal geometry, panel helpers, the captions parser
  preview.html        GENERATED - every float with its caption, in one file
  floats/             GENERATED - figures_and_tables.docx for co-authors

drafts/
  source_text_rN/     the editing surface: one .md per section
  edits/              the inbox: coauthor returns and reviewer reports go
                      here, whichever journal the paper is aimed at
  references.bib      the submission bibliography
  <JOURNAL>/          one folder per journal: its requirements, its reports,
                      and the package that goes to it

obsolete/             retired work, mirroring the live tree. Nothing is
                      deleted and nothing is drafted from
  drafts/rN/          one closed round, whole: source_text_rN/, submission/,
                      reports/, edits/, and a hash manifest
  drafts/<JOURNAL>/   a journal you are no longer sending to
  figures/rN/  tables/rN/  analysis/  notes/
```

## Day to day

```bash
Rscript data/analysis/analysis.R         # recompute statistics
Rscript plan/render_all.R                # every float, then preview + .docx
```

Open `plan/preview.html` to see the float set as it stands.
