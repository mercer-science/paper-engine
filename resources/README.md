# `resources/` — A Drop-Zone, and It Arrives Empty

Lab-specific material goes here: methods notes, SOPs, instrument sheets,
training notes — anything that says what *this* lab can actually do.
`idea-generation` inventories it at Stage 2 and Stage 5, and
`setup-project-directory` offers to prefill a project's methods facts from it.
Filenames are free-form; **only two names have a reserved meaning**,
`instruments.md` and `bootcamp.md`, and the contract for both is at the bottom
of this file.

**Where this folder is safe, and where it is not.** In a git clone, what you
add here stays — untracked files survive `git pull`. In a **plugin install**,
`/plugin update` replaces this folder and everything you added to it, with no
warning and no error. So the toolkit reads `~/.paper-engine/resources/` as
well, and that is the better place to put anything you care about. Run
`python tools/labpack.py config --resources --adopt` to move what is already
here.

## Why This Folder Ships Empty

It used to ship two skeletons and an argument for filling them in: correct an
instrument's detector model once here, and it is corrected for everybody on
their next `git pull`. That argument breaks the moment the engine goes to a
second lab. A negative-stain SOP correcting itself for a surface-science lab is
not a correction, it is contamination — and the numbers in it are exactly the
numbers a feasibility conversation quotes back at somebody as if they were true
here.

**The only facility content the engine can ship safely is none.** So the folder
stays, its readers stay, its two reserved filenames stay, and it arrives empty.
Spec: `specs/lab-resource-pack.md` §1.2.

## The Four Places Lab Knowledge Can Live

| Layer | Scope | Travels to the rest of the lab | Survives `/plugin update` |
|---|---|---|---|
| **this folder**, in a plugin install | whoever holds this copy | no | **no** |
| **this folder**, in a git clone | whoever holds this copy | no | yes |
| **`~/.paper-engine/resources/`** | whoever holds this machine | no | **yes** |
| **a lab resource pack** | one lab, curated, versioned | **yes, by plugin** | yes |
| `<lab>/Resources/` in OneDrive | one lab, working copy | yes | n/a |

**A drop-zone is where a lab starts; a pack is what it graduates to.** A pack is
its own plugin — content and a manifest, no code — and it is how a correction
reaches everybody instead of one laptop. See `specs/lab-resource-pack.md` §3
for what is in one and `tools/labpack.py show` for which one this copy resolves.

Precedence when the same instrument appears in two layers: **the narrower layer
wins, and the wider one is still named in the output**, so you can see that two
sources disagreed. A project's own `data/methods_facts.yml` wins over all of
them.

## The One Rule

**Everything here is a source, never an answer.**

Nothing in any layer is written into a project's `data/methods_facts.yml`
without a human confirming it is what they actually did. A prefilled value that
nobody checked is a fabricated methods section that reads perfectly — which
makes it the most dangerous kind of wrong this toolkit can produce, because it
is *usually* right.

So every value arrives **commented out**, tagged with the file and layer it came
from, and `manuscript.py completeness` counts a commented value as missing until
a human removes the `#`.

## What Reads This Folder

```bash
python tools/idea.py resources "<any path>"     # inventories every drop-zone root
python tools/labpack.py show                    # which pack and which drop-zone resolved
python tools/scaffold.py prefill "<project>" --instrument "<a block name>"
```

`idea.py resources` reports **names, subfolders and the first heading of each
text file — no document body**, so it is cheap enough to call before the first
literature query. A document is read when it is about to inform a question.

---

## The Two Reserved Filenames

Write for a person, not for a parser. Nothing here is loaded as YAML and no
heading is a contract — these files are read the way a new student would read
them. Two things are worth keeping true anyway, and `instruments.md` has an
optional half that is machine-read.

Anything not yet known is `[TK: ...]`. An empty line is indistinguishable from a
forgotten one; a `[TK:]` is not, and nothing marked `[TK]` is ever offered to a
project.

### `instruments.md` — One `##` Block per Instrument

```markdown
## <Instrument name> (<what kind of instrument>)

- **What it is for.** One or two sentences. What question it answers.
- **Who can run it.** Bootcamp module, sign-off, or who to ask.
- **Standard settings.** The settings this group actually uses, with units.
- **What goes in a methods paragraph.** The exact list of facts an editor
  expects: manufacturer, model, the manufacturer's city and country, and every
  setting that changes the result.
- **What it cannot do.** The limits that kill an experiment plan. Worth more
  than everything above it.
- **Consumables and lead time.** What has to be ordered, and how long it takes.
```

**"What goes in a methods paragraph" is the line that earns this file.** A
manufacturer's city is not knowledge anybody keeps in their head, it is asked
for by every journal, and it is why a methods section comes back from
copy-editing. Written once, it is right in every paper afterwards.

**"What it cannot do" is the line that saves the most time.** It is read during
the feasibility stage, and a candidate that dies on a missing cryo stage should
die in a conversation rather than after a month of scope time.

A `##` section that is documentation rather than an instrument says so with an
HTML comment, `<!-- not-an-instrument -->`, and is never offered.

#### The Optional Machine-Readable Half

Add a fenced `yaml` block to an instrument's section and `scaffold.py prefill`
can offer those exact keys:

```yaml
instrument_make: Thermo Fisher Scientific
instrument_model: Helios 5 UX
instrument_city: Waltham, MA, USA
accelerating_voltage_kV: 5
beam_current_nA: 0.1
detector: ETD (secondary electron)
working_distance_mm: 4
```

Use the key names the project's `methods_facts.yml` already uses, so a confirmed
value and a facility default land on the same key.

**Nothing is derived from the bullets.** "the manufacturer's city and country"
is a sentence about what to record, not a record, and turning it into
`instrument_city: the manufacturer's city` would put a placeholder into a
methods section wearing the shape of a fact. A block with no `yaml` half
prefills nothing and says so.

### `bootcamp.md` — Modules, and the Level Each One Leaves a Person At

| Level | Means |
|---|---|
| **trained** | has run it unsupervised and can plan an experiment on it |
| **supervised** | may run it with somebody signed off standing there |
| **shown** | has watched it once; would need training before touching it |
| **not covered** | the bootcamp does not touch this |

A candidate idea that needs a **shown** or **not covered** technique is not
infeasible — it is a training request with a lead time, and that is the honest
thing to say about it.

Two sections earn their place more than the module list does: **what we have
access to but do not train in the bootcamp** (the list that turns "we cannot do
that" into "we can, with three weeks of notice"), and **what we do not have**
(the honest negative list, because a gap analysis that lands on a technique
nobody can reach is worse than no gap analysis).
