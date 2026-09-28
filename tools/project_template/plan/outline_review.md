# Structure

*One line per paragraph, the claim then its evidence. The `##` headings here
are THE SECTIONS OF YOUR REVIEW — name them yourself, and the section files
follow.*

## Introduction

-

## Outlook

-

# Notes — anything, in any order

*Anything you have not organized yet; nothing here is ever treated as a paragraph.*

-

<!-- HOW THE TWO REGIONS WORK, AND THE ONE THING A REVIEW DOES DIFFERENTLY.

     THE `##` HEADINGS ARE THE PAPER'S SECTIONS. A research paper has four
     fixed body sections and this file just plans paragraphs inside them. A
     review does not: its body is N thematic sections, named by you, and
     nobody but you knows how many there are or what they are called. So the
     `##` headings under # Structure ARE the section list, in document order,
     and `drafts/source_text_rN/` gets one file per heading:

       ## Monolayer Formation      ->  02_monolayer_formation.md
       ## Defects and Ordering     ->  03_defects_and_ordering.md

     The two-digit prefix carries the order and is the only thing that does;
     it never appears in the built document. Renaming a heading renames the
     file, and the round snapshot in obsolete/drafts/rN/ is what makes that
     safe. Two headings are seeded above so the file is not empty — replace
     them, add to them, reorder them.

     # STRUCTURE is the paragraph plan, and it is the review's claims ledger.
     One line per paragraph of the finished paper, in order. Each line: the
     claim that paragraph makes, then [the evidence for it].

     Evidence for a review is usually a CITEKEY, and the grammar already
     takes one: [@ulman1996formation]. [Fig N], [Table N] and [PMID 12345678]
     work exactly as they do anywhere else.

       ## Monolayer Formation
       - Thiols chemisorb through a Au-S bond rather than physisorbing. [@nuzzo1983adsorption]
       - Ordering completes over hours, not minutes. [@bain1989formation; @poirier1994scanning]

     SEVERAL CITEKEYS SUPPORTING ONE CLAIM IS CORRECT — "several groups have
     reported this [a; b; c]" is standard and is not packing. Two INDEPENDENT
     claims in one line still is; split them.

     KEEP EVERY LINE SHORT — the idea of the paragraph, never a draft of it.
     Under 25 words and one sentence.

     AN EMPTY BRACKET IS A VISIBLE GAP. Leave it empty rather than filling it
     with something the corpus does not hold — the drafter turns a bracket it
     cannot resolve into a [FLAG: ...] instead of a plausible sentence, and
     for a review that matters more than anywhere else: every sentence is a
     claim about somebody else's work, and a plausible false one reads like
     scholarship and is unfalsifiable from inside this folder.

     WHAT THE EVIDENCE IS CHECKED AGAINST is data/corpus/papers/, one file
     per source, and the `read_tier:` line in each. A sentence may not
     characterize a source beyond the tier at which that source was read: an
     abstract licenses the paper's own stated claim and headline result, and
     not its n, its conditions, its controls, its limitations, or what its
     figures show. `review.py tier` is the countable half of that, and
     `attribution-check` is the half that reads.

     # NOTES is a working surface, not an archive. The engine reads it as a
     source when it builds or completes the outline, and a note that becomes
     a paragraph line MOVES up into # Structure rather than being copied.

     Delete this comment once you have the hang of it. The two headings stay.
     -->
