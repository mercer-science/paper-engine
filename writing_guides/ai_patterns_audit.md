# The 25 AI-Writing Patterns — Source Digest

**Nothing reads this file.** It is captured source material for `TODO.md` §5.1,
not a rule set, and no engine loads it. It becomes rules only when
`specs/writing-engine-prose.md` is amended and each pattern below is calibrated
against real published prose in this group's field — the same discipline the
abbreviation check already follows, and for the same reason: a check that fires
on peer-reviewed work is a check the author learns to skim (item 75).

**Captured 2026-09-15 from the repository the user pointed at**,
`github.com/eniktab/scientific-paper-writer`, file
`skills/scientific-paper-writer/SKILL.md`, MIT licensed. That repo credits two
upstream sources: *Wikipedia: Signs of AI Writing* and the `blader/humanizer`
framework. The README's four-family summary is thinner than the skill file; what
is reproduced here is the skill file, which carries the actual trigger lists.

## The Standing Constraint on All of This

**`writing_rules.md` is not overwritten and is not renegotiated here.** The user
said so explicitly. Where a pattern below disagrees with the house digest, **the
digest wins** — the audit is about sounding like a person, the digest is about
sounding like a scientist, and only the digest has a peer-reviewed source behind
it. A pattern that contradicts the digest is dropped, not merged.

Three below are style preferences dressed as rules and must not be adopted as
written: **#10** (rule of three), **#13** (one em dash per section) and **#16**
(one transition word per manuscript) set arbitrary numeric limits that ordinary
scientific prose breaks constantly. Measure them before believing them.

---

## Content Patterns (1–6)

**1. Significance inflation.** Trigger: "pivotal moment", "watershed
development", "marks a significant shift", "testament to", "vital role",
"transformative impact". Fix: quantify — "Expression increased 3.2-fold" rather
than asserting importance.

**2. Notability name-dropping.** Trigger: prestige as evidence — "As reported in
Nature", "Top researchers agree". Fix: cite the paper, not the journal.

**3. Superficial -ing analyses.** Trigger: sentences ending in a vague process
clause — "thereby highlighting the importance of X", "reflecting the continued
relevance of Y", "underscoring the need for further research". Fix: state what
the finding means mechanistically.

**4. Promotional language.** Trigger: adjectives without quantification —
"vibrant community", "rich dataset", "robust methodology", "exciting findings",
"promising results", "novel approach". Fix: quantify.

**5. Vague attributions.** Trigger: "Experts argue", "Research suggests",
"Studies have shown", "It is widely believed". Fix: every factual claim carries a
specific citation.

**6. Formulaic challenge-and-triumph.** Trigger: "Despite these challenges…",
"However, progress has been hampered by…", "Building on these foundational
discoveries…". Fix: be specific about the challenge and about what this work does
differently.

## Language Patterns (7–12)

**7. AI vocabulary.** Words near-absent in Nature and near-universal in AI
output: *testament, landscape, delve, crucial, pivotal, nuanced, comprehensive,
robust* (when vague), *seamless, notable, leverage/leveraging, foster, empower,
utilize, ascertain, endeavor, shed light on, unpack, showcase, underscore,
highlight*. Fix: the exact word. "Use" beats "utilize"; "find" beats "ascertain".

**8. Copula avoidance.** Trigger: "serves as", "functions as", "acts as", "stands
as", "operates as" where "is" would do. Fix: use "is". Keep "serves as" for when
the serving relationship is scientifically meaningful.

**9. Negative parallelisms.** Trigger: "It is not merely X, but rather Y"; "This
is not just a technical advance; it represents a paradigm shift." Fix: state the
positive.

**10. Rule of three.** Trigger: three-item lists — "fast, scalable, and
reproducible". Fix: if two things are true, say two. *(Style preference. Do not
adopt as a threshold without measuring.)*

**11. Synonym cycling.** Trigger: varying the word for one entity —
participants/subjects/individuals, samples/specimens/materials. Fix: one term per
entity, used consistently. *(This one agrees with the house digest's terminology
rule and should be easy to land.)*

**12. False ranges.** Trigger: "from X to Y" joining unrelated items —
"Applications range from drug discovery to climate modeling." Fix: be specific
with citations, or cut the claim.

## Style Patterns (13–17)

**13. Em-dash overuse.** Fix as written: at most one em dash per major section.
*(Arbitrary. Measure first.)*

**14. Boldface overuse.** AI boldfaces predictably — key terms, topic phrases.
Scientific prose reserves boldface for statistical thresholds, vectors and
matrices, and figure labels.

**15. Inline-header lists.** Trigger: **Bold label:** followed by a description —
common in AI, absent in Nature papers. Fix: flowing prose.

**16. Sentence-initial conjunctions as filler.** Trigger: "Furthermore",
"Moreover", "Additionally", "In conclusion", "In summary" opening a paragraph.
Fix as written: one per manuscript maximum. *(Arbitrary. Measure first.)*

**17. Hyphenated-compound overuse.** Trigger: "data-driven", "cutting-edge",
"state-of-the-art", "high-throughput" used loosely. Fix: only where compounding
adds precision.

## Communication and Filler Patterns (18–25)

**18. Chatbot artifacts.** "I hope this helps!", "Feel free to ask",
"Certainly!", "Of course", "Great question!" Fix: remove.

**19. Sycophantic framing.** "This is indeed an important area", "The authors
have done well to address…". Fix: state the science.

**20. Cutoff and limitation disclaimers.** "As of my knowledge cutoff", "I may
not have current information." Fix: verify, or mark unverified.

**21. Filler phrases**, as automatic replacements:

| Written | Replace with |
| --- | --- |
| in order to | to |
| due to the fact that | because |
| at this point in time | now |
| it is important to note that | delete, or restate directly |
| it should be noted that | delete |
| as mentioned above | restructure, or delete |

**22. Excessive hedging stacks.** Trigger: "These results may suggest that X
could potentially play a role in…". Fix: one hedge, where warranted.

**23. Generic conclusions.** Trigger: "More research is needed", "Future work
should investigate", "Further studies are warranted". Fix: name the question, the
design, and why it advances understanding.

**24. Epistemic uniformity.** Trigger: every paragraph claiming the same
certainty. Fix: calibrate to evidence strength — *demonstrates* (replicated),
*indicates* (single studies), *suggests* (preliminary). **This is the most
valuable pattern in the list for this toolkit**, because the engine already knows
each claim's evidence tier and could grade the verb against it rather than
guessing.

**25. Structural predictability.** Trigger: every paragraph running topic
sentence → evidence → interpretation → linking sentence. Fix: vary by logical
need — some paragraphs defend a claim, some derive one, some open with a question
and resolve it.

## Perplexity and Burstiness

**Perplexity** — word predictability. AI produces low-perplexity text; raise it
with precise field-specific terminology that is unexpected to a language model
and ordinary to a domain expert. Their example: *"The mutation disrupted
protein–protein interaction by removing a critical hydrogen bond at Arg245"* is
unmistakably human where a generic description is not.

**Burstiness** — sentence-length variation. AI produces uniform lengths, around
22 words each; human prose varies erratically — 8 words, then 31, then 14. The
target is visible variation within a paragraph, earned by logical necessity
rather than imposed.

**`prose.py voice` already measures burstiness and does not grade it.** It
reports sentence-length mean, sd, median and max, and per-paragraph opener
lengths. On the draft that started all of this it reported mean 32.7, sd 10.6,
and openers of 38, 47 and 38 words — mean 41.0, sd 4.2. That is the number to
grade, and it needs no new engine.
