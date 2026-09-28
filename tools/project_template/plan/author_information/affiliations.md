# Affiliations and funding

Where the authors are, who paid for the work, and the end-matter statements
that go with it. `authors.md` names the people; this file names the
institutions and the grants, so an address is written once and referenced by
key from every author who shares it.

`manuscript.py` builds the byline, the title page and the Funding,
Acknowledgements and Conflict of Interest statements from this file. Nothing
here is retyped into a submission form.

**It ships blank and should not stay that way**, on the same terms as
`authors.md`: fill it in when you fill that in, before the writing engine runs.
The engine asks for what is missing rather than waiting for you to notice, but
being asked is the fallback. It lives in `plan/` for the same reason - an
address does not change because the paper reached round 3.

Anything written under a statement heading below goes into the manuscript
verbatim, so the instructions for filling one in are in HTML comments: what is
left as prose is what you meant to submit.

## Affiliations

<!-- One row per institution, in the form the journal prints them - full
     department, institution, city, postal code, country. The key is what
     authors.md refers to in its `affiliations` column; the number in the
     byline is assigned from author order at build time, so keys never have to
     be renumbered.

     Worked example:

     | key | affiliation |
     |---|---|
     | A | Department of Chemistry and Biochemistry, Brigham Young University, Provo, UT 84602, United States |
     | B | Department of Physics and Astronomy, Brigham Young University, Provo, UT 84602, United States |
-->

| key | affiliation |
|---|---|
| A |  |

## Funding

<!-- One row per grant. The award number is what the funder's reporting system
     matches on, so it goes in as printed on the notice, and `to` is the author
     who holds it - initials or full name. The statement is written from the
     table: "This work was supported by X (CHE-0000000, to C.D.)".

     Write a sentence here instead when the funder requires exact wording -
     prose under this heading is used verbatim and the table is left as the
     record of what the wording is about.

     Worked example:

     | funder | award | to |
     |---|---|---|
     | National Science Foundation | CHE-0000000 | C.D. |
     | Example Center for Cancer Research |  | A.B. |
-->

| funder | award | to |
|---|---|---|
|  |  |  |

## Acknowledgements

<!-- People and facilities that helped but are not authors: instrument time, a
     gift of material, a reading of the draft. Named individuals should be
     asked first - most journals require it. -->

## Competing interests

<!-- A declaration either way. Most journals require the sentence even when
     there is nothing to declare. -->

## Ethics

<!-- IRB or IACUC approval numbers, consent, biosafety - whatever applies.
     Delete the heading if none of it does. -->
