# help/

Three files, for a person who has never used any of this and is not going to
read a spec.

| File | Written by | Edit it? |
|---|---|---|
| `instructions.md` | **by hand** | yes — it is the guide |
| `paper_engine_overview.pptx` | you, in PowerPoint | **yes** — see below |
| `SETUP-NEW-MEMBER.md` | by hand | yes |

---

## `instructions.md` — the guide, and it is Markdown for one reason

**GitHub renders Markdown in a repository view and renders HTML for nobody.**
This guide used to be a styled, self-contained `instructions.html`, and every
member who followed the link in `../README.md` got a wall of markup instead of
a page. That is not a setting and it is not fixable from inside the file:
`htmlpreview.github.io` and `raw.githack.com` both need anonymous access to the
raw file, which the repository did not give while it was private.

**GitHub Pages was the alternative and it is unavailable here.** Pages cannot
serve a *private* repository on a Free organization at all — it needs Pro, Team
or Enterprise — and even where it is available, Pages access control is
Enterprise Cloud only, so the site would have been public. Both halves of that
were dead ends, so the `.html` file and the Pages workflow that briefly existed
to serve it are gone. `git log` has them.

So the formatting is whatever GitHub's own renderer provides and nothing else:

- **alert blocks** (`> [!NOTE]`, `> [!WARNING]`, `> [!IMPORTANT]`,
  `> [!CAUTION]`, `> [!TIP]`) for the things that must not be skimmed past
- **tables** for every reference list
- **`<details>`** to collapse the command reference, which is the longest
  section and the one people scroll past

**No `style=` and no `class=`** — GitHub strips both, so a rule that depends on
them is a rule that silently does nothing. No images, no external anything. It
also has to stay readable as plain text in an editor, which is the other reason
there is nothing clever in it.

**Links to a `SKILL.md` are relative** (`../skills/<name>/SKILL.md`). They
resolve in the GitHub view, in a clone, and in an editor's preview alike.

### Two rules that keep the drift small

Nothing checks the prose in this file, so when a module changes **this is the
file that has to be remembered.**

- **Nothing in it may contradict a `SKILL.md`.** Where a detail could drift,
  link to the skill instead of restating it. This is an orientation document;
  the skills are the instructions.
- **It states what is not built yet**, inline, in the section where somebody
  would otherwise go looking. A guide describing a module that does not exist is
  worse than one that says "not yet".

`tests/help_deck.py` holds what can be held mechanically: that the guide exists,
that every section anchor its own contents table links to resolves, that every
relative link points at a file that is really there, that every skill on disk is
linked, and that the install section names the real repository rather than the
one it named for months after the rename.

## `paper_engine_overview.pptx` — hand-editable, and the generator is on request only

```bash
python tools/help_deck.py build            # ONLY when you ask for it
python tools/help_deck.py build --no-animation
python tools/help_deck.py check            # measures the shipped deck
python tools/help_deck.py check --out "some\other\deck.pptx"
```

Note `check --out`, with the flag. `check` takes no positional argument — a
bare path is rejected as an unrecognised argument, which is what this file used
to tell you to type.

**`build` overwrites the deck and discards every hand edit in it.** So it is not
run as part of anything: not by a preset, not at the end of a build order, not
by a test, and **not by an agent unless you asked for it in those words**. The
deck in this folder is yours to open in PowerPoint and change.

That is a deliberate reversal. The deck used to be treated as a generated
artifact that a rebuild was expected to reclaim, and this file said in bold not
to hand-edit it. The reason for the change is that the drawing is worth more
than the regeneration: a person laying out a flow chart beats the generator's
grid, and the module list changes rarely enough that re-drawing is cheaper than
never being allowed to draw.

**What the generator is still for.** It is the fastest way to get a correct
first draft of the deck after a real change to the module set, and the only
thing that can. The tables at the top of `tools/help_deck.py` are the whole
content — `STAGES`, `IDEA_STEPS`, `RESEARCH_WORK`, `WRITING_CHAIN`,
`ENGINE_MODULES`, `OTHER_TOOLS`, `LOOPS`, `ROUTES`, `MODULAR`. So when modules
change: add the entry, rebuild into a **scratch path**, and copy across what
you want.

Two of those are held against the engine by hand rather than by a test, so they
are the ones to re-read when something moves:

- **`RESEARCH_WORK`** is `WEIGHTABLE_SOURCES` in `manuscript.py` plus the two
  files `submission-package` needs. It is the slide that answers *"what else
  does the engine expect me to fill in"*, and the honest answer is whatever
  `manuscript.py completeness` reports — so if that list grows, this one has to.
- **`ENGINE_MODULES`** is fifteen entries, not fourteen. `abstract` is a real
  module, it is spawned, and it is one of the **four** blind ones. It was
  missing here for a while, which made the slide understate the exact property
  the slide exists to state. `tests/help_deck.py` catches a *renamed* module
  and a *wrong* blind marker; it cannot catch an omitted one.

```bash
python tools/help_deck.py build --out "%TEMP%\new_deck.pptx"   # never over yours
```

`check` is worth running on whatever you end up shipping, hand-edited or not. It
re-parses every XML part of the file and asserts two things.

**Every animated shape id exists on the slide that animates it.** A mismatched
id is worth catching because PowerPoint drops the effect, the deck opens fine,
and the animation is simply gone.

**Every shape is actually on the slide it is on.** Nothing measured where a
shape *ended* until 2026-09-08, and two slides had been running off the bottom
edge for as long as their lists had been that long — the routing slide by half
an inch, the modular slide by two inches, with its last five commands not in the
rendered deck at all. Both were written as `y = top + i * step`, both opened
fine, and both looked authoritative. Same failure mode as a stale figure, so it
gets the same treatment. There is a quarter-inch of slack, so a rounded corner's
bounding box does not fire it.

`--no-animation` writes the identical deck with no `<p:timing>` block. It exists
because animation XML is the one part of a `.pptx` that PowerPoint refuses to
open rather than degrading, so there has to be a fallback that is known to work.
The deck is legible either way: the timing block buys the *order* things appear
in, never the content.

`tests/help_deck.py` holds the **generator** against the engine — every module
it names has to be a real one, and every `blind` marker has to match
`agent-brief`'s own list. It also checks that the shipped deck still opens and
still carries its animations, which is the assertion a bad hand-edit would trip.
It does **not** require the shipped deck to be byte-identical to a fresh build,
because it no longer is one.

## `SETUP-NEW-MEMBER.md` — the checklist for setting somebody up

What you do once ever, what you do per person, what they install, what they
type. The [README](../README.md) has the same two install routes with the
reasoning behind every line; this file is the checklist and nothing else.

It carries org-admin URLs and the token-minting procedure. That was a publishing
hazard while the guide beside it was being served as a public website; with the
Pages route gone, everything in this folder is as private as the repository.
