# Setting this up for someone else

The short version. The [README](../README.md) has the same two install routes
with the reasoning behind every line; this file is the checklist and nothing
else.

> [!NOTE]
> **The engine is public since 2026-09-28**, so nobody needs to be granted access
> to it. The steps below that add a member to `paper-engine-users` matter only
> for the lab's **resource pack**, which is still a private repository.

**The link to put in the invitation email** is the member-facing guide
beside this file: <https://github.com/mercer-science/paper-engine/blob/main/help/instructions.md>.
GitHub renders it as a page for anybody; the repository is public.

---

## Part 1 — What you do, once, ever

A team, so you set permissions one time instead of once per person.

1. **Create the team.** <https://github.com/orgs/mercer-science/new-team>
   - Name: `paper-engine-users`
   - Create team.

2. **Give the team read on the lab's resource pack.** Miss this and the pack
   install in Part 3 fails with "marketplace not found" — the pack is a
   separate private repository, unlike the engine.
   <https://github.com/mercer-science/jensen-resource-pack/settings/access>
   - *Add teams* → `paper-engine-users` → role **Read** → Add.
   - A different lab means a different pack repository; grant on that one.

That is the permission model, complete. Read on the pack, granted to one team,
and nothing else.

> **There used to be two more steps here and they are gone.** Write on a
> `paper-engine-reports` repository, and an org setting allowing fine-grained
> tokens — which was the step that silently blocked the other one, because
> without it a member mints a token that cannot see the repository and gets no
> useful error. Upstream defect reporting was removed on 2026-09-21, so there
> is no token to mint. Nothing this toolkit records leaves the machine it runs
> on.

## Part 2 — What you do for each person

**One invitation per person. GitHub has no bulk invite** — no CSV, no shared
join link, no password. Bulk provisioning is SCIM, which is Enterprise Cloud
only. So this screen, once per person:

<https://github.com/orgs/mercer-science/people> → **Invite member**

- Their GitHub username, or the email their GitHub account uses
- Role in the organization: **Member**
- Under *Add to teams*: tick **`paper-engine-users`**
- Send invitation

Ticking the team is what grants both repositories. If you forget it they join
the org and can see nothing.

**You can still write one email to everybody** with Part 3 and Part 4 in it.
It is the GitHub *invitations* that are one at a time, not the instructions.

---

## Part 3 — What the new member installs

**Claude Code is not enough on its own.** Four things, and the first three are
what the toolkit cannot work without:

| | Why | Notes |
|---|---|---|
| **Git** | `/plugin marketplace add` clones the repo | must be on PATH |
| **Python 3.12 or newer** | every engine | `python` on Windows, `python3` elsewhere |
| **pandoc** | building the `.docx`; nothing else can do it | **must be on PATH** — `shutil.which("pandoc")` is the only lookup |
| **R** | the figure and table pipeline | does **not** need to be on PATH on Windows; a default `C:\Program Files\R\` install is found |

Then two install lines:

```
pip install requests lxml pypdf python-pptx
```

- `requests` — all literature search and citation verification
- `lxml` — reading a coauthor's tracked changes out of a `.docx`
- `pypdf` — reading a PDF reviewer report
- `python-pptx` — builds the help deck only; no paper needs it

```
install.packages(c("ggplot2", "dplyr", "readr", "patchwork", "yaml", "here",
                   "jsonlite", "ragg", "png", "officer", "flextable",
                   "base64enc", "colorspace", "farver"))
```

They do not have to get that list right up front. The first render prints
exactly which packages are missing and the `install.packages` line that fixes
them — all of them at once, before doing any work.

**Optional, and genuinely optional:**

- **PowerPoint or LibreOffice** — only for drawn artwork panels and the
  graphical abstract. With neither, the engine stops and asks for a PNG
  exported by hand; that is a supported path, not a failure.
- **A free NCBI API key** — raises the rate limit on PubMed. Everything works
  without one.

## Part 4 — What the new member types

**First, in the terminal they will start Claude Code from.** Both lines are
steps, not troubleshooting — each prevents a failure whose error message does
not mention it (the [README](../README.md) says which).

Windows PowerShell:

```powershell
$env:CLAUDE_CODE_PLUGIN_PREFER_HTTPS = "1"
$env:CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE = "1"
```

macOS / Linux:

```bash
export CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1
export CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE=1
```

**Then, inside Claude Code, two commands:**

```
/plugin marketplace add mercer-science/paper-engine
/plugin install paper-engine@paper-engine-marketplace
```

There is no token and no server; the engine's repository is public.

**Then two more, for the lab's resource pack.** Tell them this is part of the
install, not an extra: the pack is what tells the engine which instruments
exist, where they are, and what the SOPs allow. Without it the engine asks them
for every one of those facts by hand, on every project.

```
/plugin marketplace add mercer-science/jensen-resource-pack
/plugin install jensen-resource-pack@jensen-resource-pack-marketplace
```

Content only — no code, no skills, nothing that runs. If they are in a
different lab, they install that lab's pack instead, and Part 1 step 3 has to
have granted the team read on it.

**Have them check it resolved, once.** The engine finds the pack by a marker in
its manifest and needs no path configured, so the only thing worth confirming
is that it found one:

```
python tools/labpack.py show
```

It prints the pack's name, its version, how old its content is, and every
drop-zone folder on that machine. Three answers and what each means:

| It says | Means |
|---|---|
| a pack, with a curation date | done, nothing else to do |
| **no pack** | not an error. The engine works exactly as it does without one, and asks the facility questions generically. Check the install and Part 1 step 3 |
| **two packs** | they are in two labs, which is a real case. It deliberately resolves neither; settle it once with `python tools/labpack.py config --path "<the pack folder>"` |

**The second thing to tell them: nothing updates itself, but they will be
told.** Auto-update is off for both marketplaces and is staying off — measured
2026-09-23, a third-party marketplace defaults to off, and the lab pack is a
private repository the background refresh cannot authenticate to. The
replacement needs **no token, no setup and nothing to paste**, and as of
2026-09-24 it needs nothing typed either: **every skill checks at its opening
step** and says one line if their copy is behind, nothing if it is not.

So the thing to tell them is not a command. It is what the line means when it
appears:

> the repository has moved since this copy was installed — run `/plugin
> update` when convenient. If that reports nothing to do, the change did not
> carry a version bump and this copy is fine.

They can still ask by hand, and `remote.py check` is the one to name if they
want an answer right now regardless of the cache:

```
python tools/remote.py check          # asks GitHub with THEIR own login
python tools/release.py freshness     # is the toolkit behind?
python tools/labpack.py show          # is the lab pack behind?
```

`remote.py` is the only thing in the toolkit that reaches the network, it runs
only at a skill's opening, and it uses the same GitHub credential their
`/plugin marketplace add` already used. Everything after it reads a cached
answer and works offline. When there is no recent answer the line says
**UNKNOWN** and why — it will not guess, and a member whose credential has
expired must not read silence as good news. Full detail in
`help/instructions.md` §11.1.

**One thing to warn them about, because it looks like a bug.** The check
notices any commit, not just a version bump. If you push without bumping the
version, everybody is told they are behind and `/plugin update` then reports
nothing to do. The line already says that is harmless — but bump the date
version on every push you actually want them to take, and it never comes up.

**A third thing to tell them, because nothing warns about it.** If they put
their own material — a methods note, an SOP, an instrument sheet — into the
toolkit's `resources/` folder while running from a plugin install, **the next
`/plugin update` deletes it**, silently. So the engine also reads
`~/.paper-engine/resources/`, which no update touches, and that is where
anything they care about should go. If something is already stranded:

```
python tools/labpack.py config --resources --adopt
```

It moves what is at risk to the safe folder and names every file it moved.

**After that they stop typing commands and just talk.** The skills resolve
their own engine paths, so nobody needs to know where the plugin landed:

> Check the paper engine is set up on this machine.
> Set up a new project for the array-symmetry paper.
> Draft the manuscript in this folder for Cell.
> Ingest the tracked changes my coauthor sent and show me what changed.
> What is still blocking this paper?

Start the session **in the paper's own folder**, not in the toolkit.

## Part 5 — There is no reports half any more

**Removed 2026-09-21, and this section is kept as a signpost** because the
setup steps it used to carry were the longest part of this guide and somebody
will come looking for them.

A member used to mint a fine-grained GitHub token, point `report.py` at a
private `paper-engine-reports` repository, read the exact bytes that would
leave their machine, and grant consent once. The engine then pushed one
redacted record per defect code upstream, where the maintainer folded them
into `system-changes.md`.

**Two things killed it.** The setup was three browser screens and an org
setting whose failure mode was a token that cannot see the repository with no
useful error — paid by every member, before they had written a word. And the
loop never ran: on the maintainer's own machine 69 records sat spooled, no
token was ever minted, and `report.py` never once met real GitHub.

The engine still keeps its own defect list. It is `system-changes.md`, it is
in this repository, and what a member's run notices stays on their machine
unless they choose to tell somebody. **Skip nothing, set up nothing — there is
nothing here to set up.**

---

## Three things to say in the invitation email

1. **Never uninstall the plugin.** `/plugin uninstall` deletes the plugin's
   data directory, and that takes their learned writing rules with it — no
   prompt, no warning, and **no copy exists anywhere else, by design**. To
   move machines or reinstall, copy that directory out first. Open defect,
   `../system-changes.md` item 66.
2. **Never pass the toolkit around as a folder** — not over OneDrive, not as a
   zip, not with `/plugin marketplace add <a local path>`. A directory source
   is a filesystem copy and takes the gitignored files with it, secrets
   included. Measured; item 67. Git or the plugin, nothing else.
3. **If figures or drafts look thin, check the install.** A harness that
   cannot resolve a skill does not report a missing skill — it writes the
   paper without one and nothing looks wrong.
