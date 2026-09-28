# Authors and contributions

Two tables. The first is the author list, in submission order. The second is
the contribution matrix: one row per author, one column per thing a person can
do on a paper. Put an `x` in every column that author did.

The matrix is the source of truth for the CRediT statement the journal asks
for, and `manuscript.py` writes that statement from it - so it is filled in
once, here, and never retyped into a submission form.

**It ships blank, and blank is where you start - not where you stay.** This is
a scaffolded file with an empty table in it, so a fresh project has nothing to
report and nothing complains. That is the only sense in which empty is fine.

**Fill it in before you run the writing engine.** It takes two minutes, you
know the answers now, and the alternative is being asked mid-round for
somebody's ORCID. Nothing about the paper has to be settled first - the author
list is usually the most settled thing on a project.

If it is still empty when the engine runs, the engine asks: who is on this, in
what order, where each one is, who did what - and fills it in from the answers,
adding a matrix row per author as it goes. **That is a backstop, not the
plan.** It costs a round trip in the middle of drafting to recover something
that should have been typed at the start.

**This file lives in `plan/`, not in `drafts/source_text_rN/`.** The source
text folder is renamed forward on every round; an author list belongs to the
project rather than to a round, and holding one copy is what stops round 8's
byline disagreeing with round 1's.

## Author list

Affiliations are the letter keys defined in `affiliations.md`; an
author with two affiliations gets `A,B`. The `#` column is submission order,
and it is what the byline follows - reorder by editing the numbers, not by
moving rows, so a first author cannot become third by a stray cut and paste.

| # | name | affiliations | ORCID | email |
|---|---|---|---|---|
| 1 |  |  |  |  |

Corresponding author:

<!-- Worked example, delete or ignore:

| # | name | affiliations | ORCID | email |
|---|---|---|---|---|
| 1 | Ada Bell | A | 0000-0002-1825-0097 | abell@example.edu |
| 2 | Cyd Doyle | A,B |  | cdoyle@example.edu |

Corresponding author: Cyd Doyle, cdoyle@example.edu
-->

## Contributions (CRediT)

One `x` per thing that author did. Every author needs at least one; an author
with none is not an author, and `manuscript.py authors` reports the row.

| author | concept | methods | software | validation | analysis | experiments | resources | data | draft | revision | figures | supervision | admin | funding |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |

<!-- The column headings are the plain-English word; the statement is written
     out in the official CRediT wording:

     concept      Conceptualization        data         Data curation
     methods      Methodology              draft        Writing - original draft
     software     Software                 revision     Writing - review & editing
     validation   Validation               figures      Visualization
     analysis     Formal analysis          supervision  Supervision
     experiments  Investigation            admin        Project administration
     resources    Resources                funding      Funding acquisition

     Add a column of your own if the paper needed something this list does not
     name - an unrecognised heading is carried into the statement verbatim
     rather than dropped. A column left blank for everyone is not mentioned.

     Worked example:

     | author | concept | methods | software | validation | analysis | experiments | resources | data | draft | revision | figures | supervision | admin | funding |
     |---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
     | Ada Bell |  |  |  |  | x | x |  | x | x |  | x |  |  |  |
     | Cyd Doyle | x | x |  | x |  |  | x |  |  | x |  | x | x | x |
-->

## Initials

Only needed where initials do not fall out of the name - `Russell W. Mercer`
gives `RWM`, and a coauthor who signs their returned `.docx` as `RM` would
otherwise be read as somebody the paper has never heard of. One heading per
person, and `manuscript.py ingest` resolves returned files through them.

<!-- ## RM - Russell W. Mercer -->

## Data and code availability
