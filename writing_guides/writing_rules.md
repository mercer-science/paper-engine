# House writing rules

The digest every drafting module loads. **The source PDFs in this folder are
not read during normal work** — they distil once into this file, and this file
is what drafting reads (spec `writing-engine.md` §8.1).

Regenerate only when a guide is added to `writing_guides/`, or when the user
says they have added one. Each rule carries its source so it can be traced, or
dropped with its guide.

Sources:

- **[N]** Savage V, Yeh P. *Tips from a Pulitzer prizewinner.* Nature 574:441
  (17 Oct 2019) — Cormac McCarthy's editing advice, relayed. 2 pp.
- **[B]** *Essential Guide to Manuscript Writing for Academic Dummies: an
  editor's perspective.* Biochem Res Int 2022:1492058. 11 pp.
- **[MK]** Mensh B, Kording K. *Ten simple rules for structuring papers.* PLOS
  Comput Biol 13(9):e1005619 (2017). Open access. Supplies layer 2 of the
  drafting brief; the ten rules themselves are held in `tools/learn.py` and
  reach the drafter through `learn.py brief`, not through this file.
- **[GS]** Gopen GD, Swan JA. *The Science of Scientific Writing.* American
  Scientist 78:550–558 (1990). Topic and stress position; the known-new
  contract. Also layer 2, also via `learn.py brief`.
- **[W]** Whitesides GM. *Whitesides' Group: Writing a Paper.* Adv Mater
  16:1375–1377 (2004). The outline is the plan, rewritten throughout.

Extracted with `pypdf`; the Hindawi PDF's text layer mangles ligatures
(`jY_he` for "The"), so nothing here is quoted verbatim from it.

**This digest is staged, and each module loads only its own stage** (prose spec
§1). The reason is measured rather than tidy: the strongest rules below are
*editing* rules — "before keeping any punctuation mark, word, sentence,
paragraph or section, ask whether the message survives without it" — and there
is nothing to cut at draft time. A drafter told to produce already-cut prose
does not cut. It **compresses**, packing maximum content per sentence, which is
the 40-word citation-stuffed sentence this staging exists to stop. The rule set
was causing the failure it exists to prevent.

A rule that genuinely belongs to both stages says so where it appears. Nothing
below is silently duplicated.

---

## At drafting

Generative rules only: these say what shape to produce. They stand behind the
ten rules of layer 2, and where they disagree, layer 2 wins.

### The non-negotiable one

**Every paragraph leads with its main idea in the first sentence.** One message
per paragraph; a single sentence can be a paragraph. [N]

This is the only structural rule the drafter may not trade away, and
`final-check` enforces it. Everything else in this file is a rule with judgment
attached.

**Also at revision.** Layer 2 rule 3 (context–content–conclusion) strengthens
it there: this rule constrains a paragraph's first sentence and leaves its last
one unmanaged, and the last sentence is where a paragraph either hands off or
dead-ends. `prose.py voice --per-paragraph` reports lead-sentence rank so the
revision pass can see which openers carry nothing.

### Structure — IMRaD anatomy

The section shapes are [B]; the word-count shares are that guide's, and yield to
the target journal's own limits in `journal_requirements/requirements.yml`.

**Title.** A finding, not a topic. Under ~15 words or 100 characters, built from
the keywords that carry the rationale. Reflect the design or the outcome. No
abbreviations, no "A study of", no "Investigations of", no passive voice.
Decide it last. [B]

**Abstract.** Answers: what is new here, what does it add, what follows. Context
(the gap), content (what was done), conclusion (the one take-home message) —
two or three sentences each, weighted toward results and conclusion. Written
last, and it must stand alone: no citations, nothing that is not in the paper,
no overstated conclusion. [B]

**Keywords.** 5–10, from MeSH where one fits, none of them abbreviations, none
already carried by the title. [B]

**Introduction.** ~10–15% of the paper, funnelling from general to specific in
four moves: what is known → what is not known → the gap this study fills →
what we did and why, ending on the objectives. It is not a review; comparisons
belong in the discussion. Cite recent work, and never omit a contradictory
study on purpose. Ends on the gap and the question, not on a summary of
results. [B]

Layer 2 rule 6 sharpens the four moves into nested gaps — field, subfield, this
paper's — one paragraph per level, each more specific than the last. [MK 6]

**The first sentence has a job, and it is not provenance.** The opening
sentence of the Introduction, and the opening sentence of the Abstract, state
**what is unresolved and who it costs** — the problem, the tension, the stake.
They do not state what the subject was named, who named it, what screen or
assay found it, or what it was first called. Where a paper's subject came from
may appear later, or not at all.

This is the one position where the wrong sentence costs the most, and the
failure is not carelessness — it is what every other rule on this page rewards.
Asked for a true, citable, uncontroversial opening, the shortest thing that
passes every correctness check is the subject's naming history. It is a fact,
it cites cleanly, and it gives the reader no reason to read on. `prose.py
readability` reports an opening whose subject is the naming or discovery of the
topic; it fires on **0 of 24** published openings in the user's own field
(`specs/probes/opening-2026-09-16/`), so a firing is worth reading rather than
dismissing.

**Methods.** The largest section (~30–40%), and the test is replication by a
stranger: setting, design, sample size and sampling, ethical approval and
consent, inclusion/exclusion, every experimental detail, and the statistical
analysis with the significance threshold stated. Equipment, reagents, and
software named with manufacturer and version. A standard published protocol may
be cited rather than restated. Methods say **how** data were collected; what was
collected belongs to results. [B]

**Results.** Past tense, no interpretation. Mirrors the methods in order.
Descriptive statistics before inferential. Do not repeat in prose what a table
or figure already carries — and do not report every analysis performed. Units on
every measurement, one format for p-values throughout. [B]

Layer 2 rule 7 says what to open a results paragraph *with*, which this section
does not: open on what was found and what it means, then give the data and the
logic that establish it. The question-and-answer form is one pattern for doing
that, offered and not required. [MK 7]

**Open on the point applies to a paragraph that continues a line, not to one
that opens a question.** Classify the paragraph first. One that *continues* an
established line — the reader already has the setup — opens on its finding. One
that *opens* a new question — a concern, a hypothesis, a check, a possible
bias — keeps reasoning order: the setup or bridge, then how it was tested, then
the finding, stated plainly where the reasoning reaches it. Moving that
paragraph's result to the front gives the reader the answer before the reason
anyone asked, and it reads backwards. A setup sentence a coauthor wrote is
never reordered without asking. (Author decision, 2026-09-25.)

**Discussion.** A reverse funnel: key results first (without re-reporting them),
then this study against the literature one comparison at a time, then strengths
and limitations, then the generalized take-home message. Fills the gap the
introduction opened. Hedging verbs — may, might, suggest, indicate — are correct
here. Do not bury a result that disagrees with the literature, do not introduce
data that were not in results, and do not discuss a non-significant result at
length. [B]

**Conclusion.** Terse: the memorable message, what is new, what follows. Never
overstates what the data show. [B]

**References.** Relevant and recent (a >10-year-old citation needs a reason);
typically 30–50 in an original study, and within the journal's own cap. Every
citation in a table or figure counts. [B]

### Terminology, and a conflict between two sources

**Repeat the technical term; do not reach for a synonym.** In science a synonym
reads as a different referent — a reader who meets "coverage ratio" and then
"surface fraction" reasonably infers two quantities. [MK 4]

This **contradicts [N]'s** "Do not reuse the same word repeatedly", which
appears at revision below. The conflict is real and it is recorded here rather
than left for a drafter to discover, because a drafter handed both rules
unqualified will hedge by inventing synonyms — the worst of the three outcomes.

**The resolution: repetition wins for technical terms and defined quantities;
[N]'s rule survives for ordinary vocabulary.** A quantity, a material, a
technique or anything with a definition in this paper keeps one name
throughout. Everything else may vary.

---

## At revision

Prohibitions and checklists. These need existing text to act on, so module 1c
owns them and the drafter never sees them. They are **layer 4**: they yield to
the user's edits (layer 1), to the ten rules (layer 2) and to learned rules
(layer 3), and a yield is recorded in `reports/rN/overrides.md` with a reason.

None of them may reject a revision pass. Only the preservation invariants can —
the numbers, the citekeys and the `**[FLAG]**` blocks (prose spec §3.1).

### Cutting

- Decide the paper's theme and the **two or three points every reader must
  remember**. That thread is the paper. Anything not helping the reader follow
  it is cut — a word, a sentence, or a whole section. [N]

  In this toolkit that decision is already made and written down: it is the
  hypothesis in `plan/README.md` and the claim lines in `plan/captions.md`, and
  the revision pass is denied all of them by design. What survives here is the
  cutting, not the deciding.
- Before keeping any punctuation mark, word, sentence, paragraph or section,
  ask whether the message survives without it. If it does, it goes. [N]
- Do not say the same thing three ways in one section. Choose one of
  "elucidate" and "elaborate", not both. [N]

### Sentences

- Short, simply constructed, direct. Minimize clauses, compound sentences, and
  transition words such as "however" and "thus" — they slow the reader down
  between them and the point. [N]

  **Sentence length is a distribution, not a ceiling.** A 35-word sentence
  after three short ones is good writing. Uniform short declaratives trade one
  monotony for another, and `prose.py voice` reports the standard deviation
  precisely so that a fix which lowers the mean while collapsing the spread is
  visible as the regression it is.
- Avoid footnotes: they break the flow and send the eye away from the line. [N]
- Avoid jargon, buzzwords, and overly technical language where a plain word
  exists. Do not reuse the same word repeatedly. [N]

  **Qualified** — see "Terminology" at drafting. This applies to ordinary
  vocabulary. Technical terms and defined quantities keep one name throughout,
  because a synonym there reads as a different quantity. [MK 4]
- Only use an adjective if it is doing work. [N]
- Do not pre-argue with imagined objections. The paper is not a dialogue with
  every reader's possible qualification. [N]
- Commas mark a pause in speech — read the sentence aloud to find them. Dashes
  emphasize the clause you consider most important; parentheses say the same
  thing more quietly. Do not lean on semicolons to join loosely linked
  ideas. [N]
- Impersonal, passive text fools nobody into thinking the work is objective.
  A personal tone engages the reader. [N]
- Keep equations out of the middle of sentences; separate them with line breaks
  and white space. [N]
- Prefer concrete language and concrete examples over abstractions. [N]
- **One citation-bearing claim per sentence.** A sentence carrying two or more
  independent citekeys is doing two jobs. Advisory at drafting, measured here —
  `prose.py voice` counts sentences with ≥2 citekeys, and citation count is the
  measured driver of sentence length (0 citations → 17.8 words, 1 → 31.8,
  3+ → 40.5). [GS]

### The common mistakes, as a checklist

The failures that guide's editor sees most, kept as things to check for rather
than prose to read. A checklist by its own description, so it belongs here and
not at drafting. [B]

| section | check |
|---|---|
| Title | too long; not matching the rationale; abbreviations |
| Abstract | over the word cap; copy-pasted from the main text; contains what the paper does not; carries citations; no take-home message |
| Introduction | over its share by >15%; contradictory studies omitted; rationale and objectives never stated |
| Methods | design not named; no sample size or sampling; no ethics statement; not replicable; statistical test or threshold missing |
| Results | present tense; duplicates the figures; wrong test; units missing; p-values in three different formats; data selected to flatter |
| Discussion | overstates; discusses non-significant results at length; introduces new data; outdated citations; limitations missing |
| Conclusion | vague, or claims more than the data |
| Figures | low resolution; legends missing; never cited in the text; more than ~8 floats |
| Authorship | no contributions statement; corresponding author unmarked; no conflicts or funding declaration; missing ORCID |

The abstract's "copy-pasted from the main text" row has a mechanical signature
worth knowing: high overlap between the abstract's numeric tokens and those in
the Results paragraphs its claim cites. `prose.py density` on `title_abstract`
reports it, advisory.

---

## What this digest does not override

The number-vomit rule (spec §6), the claim-first legend rule (spec §7), and the
recorded-value rule for methods and statistics (spec §5.1) are house rules of
this toolkit and sit **above** anything here. Where a per-invocation style
reference (`style_refs`) conflicts with this digest, the digest wins and the
conflict is reported (spec §8.2).

More precisely, under the precedence ladder (prose spec §0): the recorded-value
rule, citekey resolution and the `**[FLAG]**` convention are **layer 0** and
nothing overrides them — not this digest, not a learned rule, not the user's
own edits. Everything in `## At revision` is **layer 4** and yields to layers
1–3 with the yield on the record. Everything in `## At drafting` stands behind
layer 2's ten rules and yields to them where they disagree.
