# Academic Research Paper Engine

A complete engine for academic research papers, starting from
*"there might be a gap here"* to a journal-formatted submission: finds the gap,
scaffolds the project, plans the figures, drafts the sections, checks every number
and every citation, and assembles the submission package.

It is not designed to replace the requirements for critical thinking in research, but is designed to help with the mundane tasks. It is meant to be a creative starting point. Many projects will be different than this template provides, but you can easily branch and explore.

Every value it writes comes from real literature or from experimental data you provide.
Where there is no value it writes a visible `**[FLAG: ...]**` rather than a plausible guess.

**It is iterative.** It produces a document that you can comment on and edit in Word.
Use track changes or simply comment (ex: "Claude: This paragraph is not very readable"
or "Claude: The answer to this flag is:..."). When you run it again, the engine will
automatically update the paragraphs, reconfirm all citations, and learn from your
changes so that it works better in the future.

**Want each project on GitHub, kept in step between computers?** The engine offers it at setup
and hands over to
[github-ai-project-manager](https://github.com/mercer-science/github-ai-project-manager),
which works with or without the engine.

**It is modular.** The engine is a collection of claude skills (creating agents) and python
scripts. You can use any stages of the engine at any point, and it will automatically know
which agents to call (ex: "Please use the citations checker to confirm my paragraph's citations")

**You do not have to learn any commands.** Start a session in any working directory.
It will automatically call the correct skills based on your conversation.

Designed using Claude Code, for Claude Code, but in theory it could work with Codex.
It is meant to run on your own computer. A Claude Code cloud session can run the
Python and R, but you cannot open the drafts in Word or the figures in PowerPoint
there, and nothing is kept unless it is pushed to GitHub. Use the cloud for
one-off checks, such as confirming a paragraph's citations, not for the edit and
re-run loop.

**This page is the short version.** The full guide — every stage, every file
the engine reads and writes, and what to do when one of them is wrong — is
**[help/instructions.md](help/instructions.md)**, in this repository. There is also a
[slide overview](help/paper_engine_overview.pptx) of how the four stages hand off to
each other.

**It is open source** under the [MIT License](LICENSE): the code, the skills and this
documentation are all in this repository.

---

# Setting It Up

Four steps, about fifteen minutes, once per machine. Do them in order.

## Step 1 — Install the Required Programs

Claude Code on its own is not enough. Install these first; the engine cannot
work around a missing one.

| Requirement | Purpose | Notes |
|---|---|---|
| **Claude Code or Codex** | The LLM that manages the paper creation | Claude Code is what it was built and tested on |
| **Git** | The version history and plugin install manager | Must be on `PATH` |
| **Python 3.12+** | Multiple engine functions | `python` on Windows, `python3` on macOS and Linux |
| **pandoc** | Building the `.docx` and submission files | Must be on `PATH` |
| **R** | Generating figures and tables | On Windows it does *not* need to be on `PATH` |

VS Code is also recommended for file management and interacting with Claude Code.

Then one line in a terminal:

```bash
pip install requests lxml pypdf python-pptx
```
This installs the libraries that are required by python. You can also direct your AI agent
to install the necessary libraries.

R packages are figure-dependent, and your AI agent will help you install them as necessary.

**Optional:**
| Requirement | Purpose | Notes |
|---|---|---|
| **VS Code** | For file management and interacting with Claude. | Easiest way to manage a project. |
| **PowerPoint or LibreOffice** | For graphics and drawn artwork | Without it, the engine asks for a PNG instead |
| **NCBI Key** | Increases how fast the scientific databases will answer | The system runs perfectly well without one |
| **Lab Resource Pack** | Your lab's own methods, materials and equipment | Makes idea generation more concrete |
| **OneDrive or GitHub** | Sharing project files between people in a lab | Files sync between machines |

## Step 2 — Install the Engine

In the terminal you start Claude Code from (VS Code Recommended):

```powershell
# Windows PowerShell
$env:CLAUDE_CODE_PLUGIN_PREFER_HTTPS = "1"
$env:CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE = "1"
```

```bash
# macOS / Linux
export CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1
export CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE=1
```

Then start Claude Code and type these two commands into it:

```
/plugin marketplace add mercer-science/paper-engine
/plugin install paper-engine@paper-engine-marketplace
```


## Step 3 — Install Your Lab's Resource Pack (Optional)

The engine knows how to write a paper; the pack is an optional add on that
includes your Lab's resources, materials, common methods, instruments, and equipment.
It makes idea generation more concrete.

For example, for the Jensen lab, two more commands inside Claude Code:

```
/plugin marketplace add mercer-science/jensen-resource-pack
/plugin install jensen-resource-pack@jensen-resource-pack-marketplace
```

In another lab, ask your PI which pack is yours and install that one instead —
same two commands with a different name. Packs are **content only**: no code, no
skills, nothing that runs. One pack per lab, and it is curated by hand so the
numbers in it can be changed as a lab evolves.

## Step 4 — Confirm Plugin Installation

One sentence to the agent:

> Check the paper engine is set up on this machine.

It runs one command and reports every program, package and skill pointer,
saying what each gap would stop you doing. It installs nothing — anything
missing is yours to add.

That is the whole install.

## Keeping It Up to Date

**You do not have to remember to check.** Every skill checks at its opening
step whether GitHub has a newer copy of the engine than the one you installed,
and if it does, it says so in one line before it starts work. When you are up to
date it says nothing. It asks GitHub at most once a day and gives up after a few
seconds, so it never slows a session down, and it compares the exact commit you
installed rather than a version number, so no update can slip past it. Nothing
updates itself, and nothing is ever blocked.

When it tells you there is an update:

- **Plugin:** run `/plugin update` — but read the caution about
  `/plugin uninstall` at the bottom of this page first.
- **Cloned:** the automatic check covers plugin installs; a clone updates with
  `git pull`, so pull now and then. An update is three commands, and the third
  is not optional:

  ```bash
  git pull
  python tools/install_skills.py install
  python tools/install_skills.py status
  ```

  `status` is the only thing that reports a skill that stopped being found after
  a pull moved a file, so run it while you are still thinking about the update.

To ask by hand, the commands are in
[section 11.1 of the guide](help/instructions.md#111-am-i-behind).

### Installed Before 2026-09-28?

The repository was republished on 2026-09-28, when it became public, and its
history starts again from that date. An update will say the histories are
unrelated. That is expected and needs doing once:

- **Cloned:** in the toolkit folder, **copy your defect list out first** — the old
  history tracked `system-changes.md`, so the reset below would delete it. The
  same commands work in PowerShell and in a macOS or Linux terminal:

  ```
  cp system-changes.md ../system-changes.backup.md
  git fetch origin
  git reset --hard origin/main
  cp ../system-changes.backup.md system-changes.md
  ```

  Then run the two `install_skills.py` commands above. Anything git already
  ignored, such as `tools/.env`, is left alone. Any other file you changed in
  the toolkit and want to keep: copy it out the same way first, because
  `reset --hard` discards it.
- **Plugin:** run `/plugin marketplace update paper-engine-marketplace`. If it cannot
  update, ask Claude to back up the plugin's data directory (your learned writing
  rules live there) **before** removing and re-adding the marketplace; see the
  caution about `/plugin uninstall` below.

---

# Using It

Generally the skills can be used from any point, but to truly begin a project, you should
start from a consistent directory.

The engine assumes you are working in a folder for your projects, or perhaps a folder for your lab.
(Personal example: I have a folder for Jensen Lab/Projects/ - each project lives in a specific directory in here. I share the folders with other members of the lab as needed.)

You can simply start a new project by saying something like:

> Is there a gap worth a paper in copper-MOF thermal stability?
>
> Set up a project for the array-symmetry paper.
>
> Please help me generate ideas for a paper about bacteriophages.

The system then follows this general format:

## Step 1 — Idea Generation

Claude will call a scientific research agent. This is a Python tool that talks to the
scientific databases directly, through their own public APIs — it does not search the
web and it does not guess. Seven databases are built in:

| Database | What it is for |
|---|---|
| **PubMed** | Life sciences and medicine. The first place a search looks |
| **Crossref** | The DOI registry — almost every journal article ever published, in every field |
| **OpenAlex** | Broad coverage across all fields, plus citation counts |
| **Europe PMC** | Everything in PubMed, plus preprints and full text |
| **arXiv** | Preprints. Deliberately searched last, so a published paper is never mistaken for its own earlier preprint |
| **Semantic Scholar** | "Who cited this paper" and "what else should I be reading" |
| **PubChem** | Chemical compounds — structures, formulas, and alternative names |

A topic search merges hits from PubMed, Crossref, OpenAlex and arXiv. When the engine
needs to confirm that one specific reference is real, it tries the databases in order
until one of them produces the paper — and if none does, it says so rather than
accepting the citation.

(You can discuss with Claude if you wish to add other databases. This is customizable, but others will likely need keys.)

The research agent will find many sources about the topic and will find gaps in current research.
It will suggest topic ideas and will ask questions about what kind of methods or projects you want to pursue. It will also look for stated "future work" sections of research papers.

Once you have decided on an idea, it will ask you if you would like to create a project directory.
If you say no, a summary of the research done and references will go into an Ideas/ folder
(In my case, Jensen Lab/Ideas/)
If you say yes, it will ask further questions:

It will ask what kind of data you expect. It will set up `data/` with a `data_contract.md` saying what each column means, and will ask if you wish to include "mock data"

"Mock data" is completely fabricated and not publication ready. The goal is to provide an example of what the data MIGHT look like, making the project more understandable for undergraduates and beginners in the field.

It will also ask you if you wish to generate starting figures and tables. These are R scripts that plot straight from the files in `data/`. At first, they plot the mock data.

## Step 2 — Project Development

The project directory should be completely set up. Explore it to get a feel for each area.

As you do research and you receive real data, you may update:
| File | File Name | Location | Notes |
|---|---|---|---|
| **Methods** | methods_facts.yml | data/ | The facts a methods section is built from — instruments, settings, materials. Generated with the project; update it as things change. The prose lives in `drafts/source_text_rN/methods.md`. |
| **Data** | .csv files | data/ | The structure is whatever your project needs; a `data_contract.md` beside it says what the columns mean. Numeric data is not required — many projects have none. |
| **Source Images** | Raw images | data/raw_images/ | Micrographs, gels, photographs — anything a figure panel is built from rather than plotted. |
| **Analysis** | analysis.R | data/analysis/ | Created as a skeleton when the project is scaffolded, and filled in with you — say *"help me set up my statistics"* and the analysis skill works out which tests your design actually supports. It runs every statistic the paper reports, in R, over the files in `data/`, and writes the results next to it as `analysis.md`. The paper may only quote numbers that appear in `analysis.md`, which is what makes the statistics reproducible and stops one being invented. |
| **Outline** | outline.md | plan/ | This file can be a structure or unstructured outline of the project. Can also include notes that you wish to include in the paper. |
| **README** | README.md | plan/ | A general overview of the goals of the project, and usually much of its history. |
| **Rough Draft** | rough_draft.md | drafts/ | Optional. If you would rather write the paper yourself, write it here and the engine works from it instead of drafting from scratch. |
| **Figures** | R scripts | plan/figures/ | One R script per figure, plotting straight from the data files. This is what lets a figure change when new data arrives, and what makes a hallucinated value impossible. |
| **Tables** | R scripts | plan/tables/ | Same as figures. `render_all.R` builds every one of them into an HTML preview. |
| **Captions** | captions.md | plan/ | The captions carry a large part of the story — they are what the paper is really meant to say. Update them as you go; they appear in the preview. |
| **Authors** | authors.md | plan/ | Generated with the project. Fill in each person, their affiliation, and what they contributed. |

ALL of which are optional. The figures are arguably the most important. You can discuss with Claude what needs to change with each figure until you see something you like. There is a render_all.R that will create an html preview of the figures and tables.

## Step 3 — Paper Engine

Once you are satisfied with the state of your project, start the paper engine.
To start the paper engine, simply say something like

> Please write me a research paper for the MOF project.
>
> Do the first round of the array-symmetry paper.
>
> Write the bacteriophage draft.

The engine will automatically take the files from the previous list to generate a paper. It will show you all of them that it finds and ask if there is anything else you would like to include. You can weight them in order of priority.

IMPORTANT: You can also specify other requirements, such as a rubric, reference papers for writing style, or other requirements you have. You can tell it to just do the information that you have.

The engine will then follow these steps:

### Draft

The 'draft' tool will take whatever is available from the outline, figures, tables, etc. and develop a story (if an outline is not present, it will propose one and discuss with the user). You can also specify how strict you want it to stick to the outline.

The 'draft' tool will create source files for each section (in `drafts/source_text_rN/`, where N is the round) - The goal of this is to make sure that Claude does not forget the context of each portion of the paper. If it wrote the entire paper at once, the goal and aims of the paper may drift by the end.

The 'draft' tool will also call the 'research' tool from before and find citations for the paper. It will create a references.bib. It is designed to be fairly universal and usable with EndNote or another citations editor.

If the drafts/ directory is not present, it will create one.

### Organize

The 'organize' tool will ask the user for the target journal and organize a submission package appropriately.

It will create a folder for the journal (ex: drafts/LANGMUIR/). In the journal folder, it will create edits/, journal_requirements/, reports/ and submission/

edits/ starts empty. This is meant for edits or revisions from any corresponding authors. (For example, if an undergrad is using the engine, and they receive feedback or edits from their PI, they can put the file in this directory. The engine automatically checks any future edits or iterations to make sure that it does not undo the PI's advice. It will bring the issue to the user if there is a conflict.)

Based on the target journal, the 'organize' tool will automatically find the journal requirements online and produce submission-ready, formatted documents in the submission/ folder. (For example, if they specify the journal "Langmuir", it will find the submission and journal requirements online, save them to the project directory, and format a submission folder with the appropriate contents, including the manuscript, SI, and cover_letter.)

If the organize tool detects a graphical abstract is needed, it will use the previous figure creation skills to propose one. It will create a powerpoint of its creation, that you can make manual changes to. The manual changes will then be incorporated on the next iteration.

If at any point you wish to switch journals, you can call it again on a new journal, and it will use the same source_text with whatever iteration round it's on.

When you do, it offers to retire the old journal's folder into obsolete/drafts/, whole, so that drafts/ holds one live journal folder and you are never looking at two. It is an offer and never automatic, it deletes nothing, and it refuses rather than bury anything still standing - an open run, an edit from your PI you have not applied yet, a round log it could not merge. The rounds are not renumbered: r1-r7 went to the first journal and the record still says so, which is also what keeps the new journal opening at r8.

The submission package will include any necessary disclaimers for generative AI use.

### Writing Review

Every other check in the engine asks whether the paper is *correct*. This one asks
whether it is *readable* — and then **fixes what it finds, before you ever see the
draft.** It runs on every build, over the drafted sections, in the order a person
would read them.

Three things are measured:

- **Readability** — the things that make a reader stop and go back a line. An
  abbreviation used before it is defined. A "this" or "these" with no noun behind
  it. A paragraph that introduces a new idea before it has finished the old one. A
  sentence that puts half a line between the subject and its verb. A paragraph
  whose point is buried in the last sentence instead of the first.
- **Voice** — how the writing actually sounds, in numbers. Sentence length and, more
  importantly, how much it varies (prose where every sentence is the same length
  reads as machine-made). How long each paragraph's opening sentence is. How much of
  the section is passive. How often it leans on hedges — *may*, *could potentially* —
  or on filler transitions like *moreover* and *furthermore*.
- **Metaprose** — sentences about the writing that leaked into the writing. *"This
  section will discuss…"*, *"as outlined above"*. A reader of the journal never sees
  these in a published paper, and they are the clearest sign of a draft that was
  assembled rather than written.
- **AI Voice Check** - An audit for the 25 known tells of AI-written prose.

### Stats Review

A new agent, with NO context of the project, will then go through and check the statistics and facts. The goal of this is to eliminate bias. The agent is prevented from having project context to make sure that each statistic is real and not blown out of proportion in its significance. It flags any issues for the user to resolve that it cannot on its own.

### Citations Review

A new agent will use the research skill to check each citation - verifying that they are accurate and real with the back end tools. One final check for source hallucination.

### Mock Reviewer

A new agent will adopt the role of a reviewer for the target journal and evaluate your paper. It will check for things like font size and readability in figures, etc. Does not run every time, but generally as you get closer to submission.

## Step 4 — Iterate

The paper will likely have flags scattered through it, each marking a question only you can answer. Work through them with Claude.

Use the "Track Changes" and "Comment" feature in Word to develop your paper. You can make manual edits, or you can simply comment for claude: "Claude: Reword this section - it sounds weird."

Run the engine again, and it will use the appropriate tools to develop the project. When you are ready for submission, say so, and it will run the final checks.

### Recursive Learning

The engine learns from your edits and builds writing rules that are unique to
you, so it fits you better on the next project. **Those rules never leave your
machine** — nothing is uploaded, and nothing is shared with other users.

## Review Papers

A review is **a second kind of paper, not a second toolkit**. Everything above
still applies; three things change:

- **The corpus is the review's data.** Where a research paper checks its numbers
  against `data/`, a review checks every sentence against `data/corpus/` — what
  was searched, what was screened in or out, and one record per included paper.
- **Nothing may out-run how much of a paper you actually read.** Every record
  carries the tier it was read at — abstract, sections, full text — and a
  sentence that characterizes a source beyond its tier is a finding, not a style
  note.
- **The number-density limits come off**, because a review is mostly argument
  rather than measurement. What tightens instead is provenance.

Just say what the review is about and let the agent scaffold it.

---

# When Something Looks Wrong

**Tell the agent what happened.** It files the defect itself, in the right
shape, in the right place — a numbered item in `system-changes.md`, the
toolkit's defect list, with a way to check the fix against. You do not have to
know the format.

**`system-changes.md` stays on your computer.** It sits in the toolkit folder,
git ignores it, and nothing uploads it. Keep it: it is how a fix gets made. Two
ways to pass what it holds on:

- **Fix it yourself** and open a pull request on
  [this repository](https://github.com/mercer-science/paper-engine). The item's
  *verify by* line says how to prove the fix.
- **Send the file** to **rmercer2@byu.edu**, and the fix is made upstream.

> [!WARNING]
> An item can carry a **Specific Example** line quoting the project it was seen
> on. Read those lines before you send the file, and **never paste the file into a
> public issue or pull request** — describe the engine's behavior instead.

---

# Things That Destroy Work

> [!CAUTION]
> **Never run `/plugin uninstall`.** It deletes the plugin's data directory,
> and that takes the writing rules the engine has learned from your edits with
> it — no prompt, no warning, and no copy exists anywhere else, by design. To
> move machines, copy that directory out first. This is an open defect, not
> intended behaviour.

> [!CAUTION]
> **Never pass this toolkit around as a folder** — not over OneDrive or a shared
> drive, not as a zip, and not with `/plugin marketplace add <a local path>`. A
> directory source is a filesystem *copy*, so it carries the files this
> repository deliberately does not ship, including whatever keys and tokens the
> sender had. Git or the plugin; nothing else.

> [!CAUTION]
> **Never edit `system-changes.md` by hand** — the engine maintains it, and the
> numbering, the statuses and the "seen again" counts are all computed.
> [Section 11 of the guide](help/instructions.md#11-when-something-looks-wrong)
> lists what is known-broken and not yet fixed.
---

# Where Else to Read

| | |
|---|---|
| **[The full guide](help/instructions.md)** | Every stage, every file the engine reads and writes, and what to do when one of them is wrong. Start at section 4 — it is the only one you have to read. |
| **[CONTRIBUTING.md](https://github.com/mercer-science/paper-engine/blob/main/CONTRIBUTING.md)** | Working *on* the toolkit rather than with it: the layout, adding a skill or an engine, the suites, and who owns which document. Nothing in it is needed to write a paper. |
| **[The slide overview](help/paper_engine_overview.pptx)** | The four stages and how they hand off, on one page. |

---

# License

**[MIT](LICENSE)** — the code, the skills, the guide and the slide overview.
Use it, change it and share it, commercially or not; keep the copyright notice.

> © 2026 Russell Mercer. *Academic Research Paper Engine*, mercer-science.
> <https://github.com/mercer-science/paper-engine>
