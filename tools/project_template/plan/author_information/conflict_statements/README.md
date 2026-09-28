# Signed Competing-Interest Forms

**Put the signed disclosure forms here — one file per author.** That is the
whole job of this folder.

Most journals want a completed conflict-of-interest form from every author
before they will send a paper out for review, and most of them generate it as
a PDF you download, sign and send back. They come in over days or weeks, from
different people, in whatever format that journal's system emits. This is
where they wait.

## How to name them

Name each file with the author's initials, the same initials `authors.md`
uses:

    JV.pdf
    RWM_icmje.pdf
    ABell-coi-2026.pdf

`manuscript.py submission-package` reads the file *names* and nothing else. It
matches the initials at the front of each name against the author list and
tells you which authors have not returned a form yet, so a submission does not
stall on a missing signature nobody was tracking. It never opens the files,
and it never reads a word out of them.

Anything the matcher cannot place is reported as unmatched rather than
ignored — a form filed under a name that does not resolve to an author is the
same thing as a form that never arrived.

## The blank form is in `blank_form/`

The journal's own unsigned form — the thing everybody signs — goes in the
subfolder, not here. A blank sitting among the signed ones is counted as a
signed one, and then the count stops meaning anything.

The writing engine puts it there for you, on the round where the Competing
Interests statement first gets flagged: it reads the journal's author
guidelines, downloads the disclosure form they require, and records the URL in
`requirements.yml`. `blank_form/README.md` has the rest.

## What does NOT go here

**The Competing Interests statement that gets printed in the paper.** That is
prose, the journal dictates its wording, and it lives with the rest of the end
matter under `## Competing interests` in `affiliations.md`. These files are
the paperwork behind that sentence, not the sentence.

## Nothing here is submitted by this toolkit

The forms usually go to the journal through its own portal, attached to the
submission record rather than to the manuscript file. `submission-package`
counts them and reports on them; it does not copy them into the package.
