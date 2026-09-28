# Structure

*One line per paragraph, the claim then its evidence.*

## Introduction

-

## Results

-

## Discussion

-

# Notes — anything, in any order

*Anything you have not organized yet; nothing here is ever treated as a paragraph.*

-

<!-- HOW THE TWO REGIONS WORK — the long version.

     # STRUCTURE is the paragraph plan, and it is the project's claims ledger.
     One line per paragraph of the finished paper, in order. Each line: the
     claim that paragraph makes, then [the evidence for it]. Not a section
     outline — a paragraph plan.

     Evidence: [Fig N] [Table N] [stats: <term>] [PMID 12345678] [background]

       ## Introduction
       - Cryo-ET has resolved chemoreceptor arrays in mesophiles, but not above 60 C. [PMID 31234567]
       - Thermophilic arrays should be more ordered; nobody has measured it. [PMID 28901234]
       ## Results
       - Thermophilic arrays are more ordered than mesophilic ones. [Fig 2; stats: symmetry_index ~ species]

     KEEP EVERY LINE SHORT — the idea of the paragraph, never a draft of it.
     Under 25 words and one sentence. Detail written here is detail written
     twice, and the paragraph is the copy anybody reads, so the two drift.

     An empty bracket is a visible gap. Leave it empty rather than filling it
     with something the project has not shown — the drafter turns a bracket it
     cannot resolve into a [FLAG: ...] instead of a plausible sentence.

     [background] no longer means "no citation needed". It means cited from
     plan/literature_landscape.md — the state of the field — rather than from
     a result in this paper, so it takes a PMID or a citekey beside it.

     # NOTES is a working surface, not an archive. Write findings there as
     they happen; you do not have to organize them into an outline first. The
     engine reads this region as a source when it builds or completes the
     outline, and a note that becomes a paragraph line MOVES up into
     # Structure rather than being copied — so nothing is counted twice and
     the notes region stays somewhere you can keep writing.

     WHERE AN IDEA GOES. There are three places an idea can live in this
     project — plan/README.md, # Structure and # Notes — and they are read
     with equal weight. Put an idea wherever it lands; none of them is the
     wrong one. What # Structure alone decides is the ORDER and the paragraph
     plan of the paper. README keeps the framing it already has, and nothing
     you write in either of the other two is lost for being in the "wrong"
     file.

     r1 COMPLETES AN INCOMPLETE OUTLINE. If # Structure is thin or empty when
     a round runs, the engine completes it from your notes, plan/README.md
     and the rest of the inventory, writes it under a "PROPOSED OUTLINE — not
     approved yet" banner, and shows you the diff before anything else in the
     round happens. The lines are Claude's until you accept them. An outline
     that is already complete is left alone. To scope a round to part of the
     paper — "just the introduction and methods for now" — pass
     `--sections introduction,methods`; the rest is reported as out of this
     round's scope rather than as a gap.

     Delete this comment once you have the hang of it. The two headings above
     stay. -->
