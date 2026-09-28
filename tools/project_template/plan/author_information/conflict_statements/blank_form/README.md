# The Journal's Blank Form

**The unsigned form, as the journal publishes it.** One folder up is where the
*signed* ones go; this is the thing people sign.

It is a separate folder for one reason: a file sitting among the signed forms
is counted as a signed form. `JV.pdf` in the folder above means JV returned
one. A blank `coi_form.pdf` up there would mean an author called CF returned
one, or nothing at all, and either way the count stops meaning anything.

## Who puts it here

The writing engine does, on the round where the Competing Interests statement
first gets flagged. It reads the journal's author guidelines, finds the
disclosure form they require, downloads it here, and records where it came
from in `journal_requirements/requirements.yml` under
`submission.coi_form_url`. You should not have to go looking for it.

If it could not find one, it says so rather than leaving the folder quietly
empty — some journals collect disclosures in their submission portal and
publish no form at all, and that is a different situation from nobody having
looked. Set `submission.coi_form: not_required` when that is the case and
nothing will ask again.

You can also just drop one in. Anything here counts.

## What to do with it

Send it to every author, collect the signed copies, and put those in the
folder above named with each author's initials. `manuscript.py
submission-package` then tells you who is outstanding.

More than one file is fine — a journal that wants an ICMJE form and its own
supplementary declaration wants both, and they both belong here.
