# Relevant literature

The wider reading pile: PDFs, a working `refs.bib`, and reading notes. Nothing
here is a submission artifact — `drafts/references.bib` is the bibliography
that ships with the manuscript.

```bash
# fill this folder from a search
python <aids>/tools/pubmed.py pdf 32142651 33301246 --out plan/relevant_literature/

# and check anything already in it
python <aids>/tools/pubmed.py check-refs plan/relevant_literature/refs.bib --fail-on-problem
```

`pubmed.py pdf` writes the open-access PDF where one exists and a `.md` stub
with the metadata and a link where it does not — so a closed-access paper still
leaves a trace you can act on.
