# Sandbox — Work That Is Not This Paper

Put anything here that is not part of this manuscript. A class assignment, a
one-off document somebody asked you for, a version of the paper you made for a
different purpose, a scratch analysis, a draft of an email.

**No engine reads this folder.** Not `scaffold.py`, not `manuscript.py`, not
`prose.py`, not any skill. Nothing here is counted, checked, built, cited or
carried into a round, and nothing here can change what the paper says. That is
the whole promise, and it is the reason the folder exists.

## Why It Is Here

A project folder used to have nowhere to put work that was not the paper, so
that work landed at the project root — beside the real thing, with a name like
`paper.docx`, and nothing anywhere said which document was the manuscript.

It is the same folder you were going to make anyway. Having it named means
`manuscript.py strays` stops asking about the file.

## Where the Real Manuscript Is

```
drafts/<JOURNAL>/submission/manuscript_rN.docx
```

`N` is the current round. That file is the only one the engine builds, and

```bash
python <tools>/manuscript.py strays "<project>"
```

will print its full path, along with every other document in the project and
what each one is. Run it whenever you are not sure which file is the paper.

## What Does Not Belong Here

- **A coauthor's returned edits** — those go in `drafts/edits/`, the project's
  one inbox, where `manuscript.py ingest` reads them and their authority is
  recorded.
- **An earlier round of this paper** — the engine archives those itself, into
  `obsolete/drafts/`. Moving one here loses the manifest that proves what it
  was.
- **Data, figures or analysis this paper uses** — those have their own homes,
  and anything the paper cites has to live where the pipeline can see it.

Nothing stops you putting them here. The engine simply will not find them, and
it will report the paper as missing the thing you moved.
