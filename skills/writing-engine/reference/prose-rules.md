# The House Prose Digest

`writing_guides/writing_rules.md`, its two stages, and how it is regenerated.

Part of the `writing-engine` skill. **`SKILL.md` is the spine and is read on
every invocation; this file is read when the round reaches this stage**, and
`manuscript.py handoff --stage prose-rules` is what names it. Everything the spine
says still applies here - the `<tools>` resolution, the module isolation
contract, and **Things that are wrong to do here** are stated there once and
are not repeated in this file.

A `§N` in this file is a module number. `SKILL.md`'s **What to read, and
when** table says which file each module's section is in; the others beside
this one are `drafting.md`, `outline.md`, `revision.md`, `submission.md`.

---

## Regenerating the digest

`writing_guides/writing_rules.md` is distilled from the PDFs in that folder,
and it has **two mandatory top-level sections**:

```markdown
## At drafting
## At revision
```

The source attribution and the "what this digest does not override" closing sit
outside both and are read by both. Every rule goes in one or the other, and a
rule that genuinely belongs to both **says so where it appears** rather than
appearing twice silently. A digest in which any rule is unstaged is not a
digest — refuse it and say which rule has no stage.

Which stage a rule belongs to:

| rule family | stage | why |
|---|---|---|
| IMRaD anatomy, word-count shares, terminology | draft | generative — they say what to write |
| every prohibition (*minimize*, *avoid*, *do not*, *only if*) | revise | they need existing text to act on |
| "cut anything the message survives without" | revise | nothing to cut yet at draft |
| "decide the two or three points every reader must remember" | revise | that decision is `plan/outline.md`'s job, already made |
| the per-section common-mistakes table | revise | it is a checklist, by its own description |

**Record conflicts between sources inline, with their resolution.** The digest
already carries one: [N]'s *"do not reuse the same word repeatedly"* against
[MK]'s *"repeat the technical term; do not reach for a synonym"*. The conflict
is real and it resolves toward repetition **for technical terms and defined
quantities**, with [N]'s rule surviving for ordinary vocabulary. It has to be
stated inline, because a drafter handed both rules unqualified will hedge by
inventing synonyms — the worst of the three outcomes.
