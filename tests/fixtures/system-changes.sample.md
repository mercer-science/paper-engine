<!-- A scrubbed sample of the defect ledger for the test suites: real item
     numbers, codes and statuses, no detail and no project text. -->
# system-changes.md

Running list of defects and wanted changes in the writing-engine toolkit,
gathered from real runs against real projects. It stays on this computer:
it is gitignored, because its items describe real, often unpublished work.

## If you were handed this file to work from

Read this before touching an item.

**Fixing an item does not clear it.** The instinct on finishing a fix is to
tick the item off; do not. Move it to `FIX ATTEMPTED`, record what you changed
and when, and leave it in the open list. Only a completed engine run that
fails to reproduce the issue may move it to `VERIFIED FIXED`, and only then
does it go to Closed. You are almost never the one who closes an item — the
next run is.

**Since 2026-09-06 the run does that itself, for the defects it can detect.**
A build that succeeds closes every item whose `Code:` the engine actively
looks for and did not find — `promote_verified()` in `tools/manuscript.py`.
That is a narrow set on purpose: the codes in `ENGINE_ISSUES`, and nothing
else. For every other item the engine detects nothing, so "this run did not
encounter it" would be vacuously true and one build would close thirty items
having checked none of them. Those still need a person to run their **verify
by** line. A run reports what it closed *and* names what it could not judge,
so "nothing was promoted" never has to be read as "nothing was promotable".

**Only engine misbehavior belongs in this file.** A problem with a
*manuscript* — bad phrasing, a wrong claim, a badly organized section, a
missing float — is feedback for that draft, and it already has somewhere to
live: `completeness`, the reports, and the PAPER NOT COMPLETE block. A
problem with the *engine* is a section silently dropped, a heading not
emitted, an author name inverted, an exit code of 0 over a broken build. That
distinction is the thing most likely to blur when someone reads this file
cold, and blurring it fills the list with things no code change can fix.

**Adding an item.** Match the shape of the existing ones: status, first seen
(date, project, journal), where in the source, what happens, wanted behavior,
and a **verify by** line. The verify-by is not optional — without it, "did
this run see the issue?" is answerable only by whoever originally found it,
and the status convention stops working.

## How this file works

**Status values.** Every item carries exactly one.

| Status | Meaning |
| --- | --- |
| `OPEN` | Seen on a run. Nothing attempted. |
| `FIX ATTEMPTED` | A change was made that is meant to fix it. **Not verified.** |
| `VERIFIED FIXED` | A full engine run has completed since the fix without the issue reappearing. Only then may the item be moved to the Closed section. Written by the run itself where the engine detects the defect; by hand, after running the item's **verify by**, where it does not. |

**The rule that matters:** an item is **never cleared on the strength of the
fix alone**. Editing the engine moves an item to `FIX ATTEMPTED` and records
what was changed and when. It stays in the open list, visible, until an engine
run of the kind that originally exposed it finishes *without* the issue
occurring — at which point it becomes `VERIFIED FIXED` and moves down to
Closed. A fix that was attempted and then seen to fail goes back to `OPEN`
with the failed attempt kept in its history, so the same wrong fix is not
tried twice.

Each item records **how to verify** it, because "did this run see the issue?"
has to be answerable by whoever is running the engine next, not just by
whoever found it.

---

## Open items

### 8. A rebuild silently destroys an edit made in the built `.docx`
- **Status:** FIX ATTEMPTED 2026-09-03
- **First seen:** 2026-09-03, example_project

### 10. `toc_graphic.py` reads six requirement keys under names nothing writes
- **Status:** FIX ATTEMPTED 2026-09-03
- **First seen:** 2026-09-03, example_project

### 12. The `.bib` journal name is one no ACS journal will accept
- **Status:** FIX ATTEMPTED 2026-09-03
- **First seen:** 2026-09-03, example_project

### 13. A `VERIFIED FIXED` item that recurs is bumped inside Closed and never reopens
- **Status:** FIX ATTEMPTED 2026-09-03
- **First seen:** 2026-09-03, example_project

### 15. `revision` re-drafts every section whether or not anything asked it to
- **Status:** FIX ATTEMPTED 2026-09-03
- **First seen:** 2026-09-03, example_project

### 16. `submission-package` is in every preset and does not exist
- **Status:** FIX ATTEMPTED 2026-09-03
- **First seen:** 2026-09-03, example_project

### 17. The journal folder keeps every round, and the folder named for the live one is empty
- **Status:** FIX ATTEMPTED 2026-09-03
- **First seen:** 2026-09-03, example_project

### 18. Round numbers restart at r1 in a new journal, and no log says which journal a round was
- **Status:** FIX ATTEMPTED 2026-09-03
- **First seen:** 2026-09-03, example_project

### 19. The abstract is never drafted, and nothing says it is written last
- **Status:** FIX ATTEMPTED 2026-09-03
- **First seen:** 2026-09-03, example_project

### 20. The house style's own worked example is the sentence the user could not read
- **Status:** FIX ATTEMPTED 2026-09-03
- **First seen:** 2026-09-03, example_project

### 21. draft-sections is given no view of the field, and `background` licenses citing nothing
- **Status:** FIX ATTEMPTED 2026-09-03
- **First seen:** 2026-09-03, example_project

### 22. A flag that carries its own answer is still written as a flag
- **Status:** FIX ATTEMPTED 2026-09-03
- **First seen:** 2026-09-03, example_project

### 23. `plan/outline.md`'s notes region is invisible on opening and dead-ended once written
- **Status:** FIX ATTEMPTED 2026-09-03
- **First seen:** 2026-09-03, example_project

### 24. `read_flat_yml` truncates a multi-line quoted value, and says nothing
- **Status:** FIX ATTEMPTED 2026-09-03
- **First seen:** 2026-09-03, example_project

### 25. A scaffolded project cannot be seen working, and the methods template's one filled value verifies numbers nobody recorded
- **Status:** FIX ATTEMPTED 2026-09-03
- **First seen:** 2026-09-03, example_project

### 27. The engine invented citekeys out of a core@shell formula, then failed the build on them
- **Status:** FIX ATTEMPTED 2026-09-04
- **First seen:** 2026-09-04, example_project

### 28. A status conflict in the shared rule registry was decided by one side only
- **Status:** FIX ATTEMPTED 2026-09-04
- **First seen:** 2026-09-04, example_project

### 29. The pinned type-check config hid 34 warnings the editor was showing
- **Status:** FIX ATTEMPTED 2026-09-04
- **First seen:** 2026-09-04, example_project

### 30. A plugin update would have deleted every user's learned rules, silently
- **Status:** FIX ATTEMPTED 2026-09-04
- **Code:** `learned_rules_stranded` - reported by `learn.py stats`
- **First seen:** 2026-09-04, example_project

### 31. The source text folder does not say which round it holds
- **Status:** FIX ATTEMPTED 2026-09-04
- **Code:** n/a - a layout change
- **First seen:** 2026-09-04, example_project

### 32. The running title and the keyword list reach the .docx unsourced
- **Status:** FIX ATTEMPTED 2026-09-04
- **Code:** `front_matter_unsourced`
- **First seen:** 2026-09-04, example_project

### 33. ACS end matter is two-level and `section_order` is a flat list
- **Status:** FIX ATTEMPTED 2026-09-06
- **Code:** `end_matter_flat`
- **First seen:** 2026-09-04, example_project

### 34. Nothing checked the figure files: not that they exist, not what they are called
- **Status:** FIX ATTEMPTED
- **Code:** `figure_files_unchecked`
- **First seen:** 2026-09-05, example_project

### 35. Nine user-visible messages still said `source_text/` after the round label landed
- **Status:** FIX ATTEMPTED
- **Code:** `unlabelled_path_residue`
- **First seen:** 2026-09-05, example_project

### 36. citation-check should run itself, quietly, not be offered as a choice
- **Status:** FIX ATTEMPTED 2026-09-05
- **Code:** `citation_check_should_not_be_asked` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-05, example_project
- **Last seen:** 2026-09-05, example_project (1 runs)

### 37. the outline-change ask shows a diff but never says what KIND of change each line is
- **Status:** FIX ATTEMPTED 2026-09-05
- **Code:** `outline_diff_does_not_say_what_kind` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-05, example_project
- **Last seen:** 2026-09-05, example_project (1 runs)

### 38. prose.py claim reads notes and HTML comments as part of the claim and as cited sections
- **Status:** FIX ATTEMPTED 2026-09-05
- **Code:** `claim_report_parser_swallows_prose` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-05, example_project
- **Last seen:** 2026-09-05, example_project (1 runs)

### 39. `completeness()` ignores the round's section scope, so a scoped round reports the rest of the paper as gaps
- **Status:** FIX ATTEMPTED 2026-09-06
- **First seen:** 2026-09-05, example_project
- **Last seen:** 2026-09-05, example_project (1 runs)

### 40. `required_if_used` is read as `required`, so an unused statement is flagged as owed
- **Status:** FIX ATTEMPTED 2026-09-06
- **First seen:** 2026-09-05, example_project
- **Last seen:** 2026-09-05, example_project (1 runs)

### 41. A user-given scope is not sticky, has no inverse, and is not stated as outranking the outline
- **Status:** FIX ATTEMPTED 2026-09-06
- **First seen:** 2026-09-05, example_project
- **Last seen:** 2026-09-05, example_project (1 runs)

### 42. Four state and log files per journal folder, two of which are renderings of the other two
- **Status:** FIX ATTEMPTED 2026-09-06
- **First seen:** 2026-09-05, example_project

### 43. `reports/` duplicates the flags and is never opened
- **Status:** FIX ATTEMPTED 2026-09-06
- **First seen:** 2026-09-05, example_project

### 44. A stray blank line above `## Closed items` silently disabled the engine's own defect log
- **Status:** FIX ATTEMPTED 2026-09-06
- **Code:** `system_changes_anchor_too_literal` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-06, example_project
- **Last seen:** 2026-09-06, example_project (1 runs)

### 45. A detector emitted a code the logger refuses, so its findings were thrown away
- **Status:** FIX ATTEMPTED 2026-09-06
- **Code:** `emitted_code_not_registered` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-06, example_project
- **Last seen:** 2026-09-06, example_project (1 runs)

### 46. A methods flag was raised for a fact sitting unread in the project data
- **Status:** FIX ATTEMPTED 2026-09-07
- **Code:** `flag_raised_over_unread_data` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-06, example_project
- **Last seen:** 2026-09-06, example_project (1 runs)

### 47. A malformed identifier was reported as the index being down
- **Status:** FIX ATTEMPTED 2026-09-06
- **Code:** `miss_reported_as_outage` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-06, example_project
- **Last seen:** 2026-09-06, example_project (1 runs)

### 48. related() answered with papers that have nothing to do with the query
- **Status:** FIX ATTEMPTED 2026-09-06
- **Code:** `related_returned_unrelated_papers` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-06, example_project
- **Last seen:** 2026-09-06, example_project (1 runs)

### 49. A quota lockout was reported as three failed tries
- **Status:** FIX ATTEMPTED 2026-09-06
- **Code:** `ratelimit_reported_as_outage` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-06, example_project
- **Last seen:** 2026-09-06, example_project (1 runs)

### 50. A landscape sourced with DOIs could never unblock drafting
- **Status:** FIX ATTEMPTED 2026-09-06
- **Code:** `landscape_ignored_non_pmid_refs` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-06, example_project
- **Last seen:** 2026-09-06, example_project (1 runs)

### 51. The idea stage refused to record a paper that has no PMID
- **Status:** FIX ATTEMPTED 2026-09-06
- **Code:** `idea_rejected_papers_without_pmid` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-06, example_project
- **Last seen:** 2026-09-06, example_project (1 runs)

### 52. Explore-mode literature summary names a refs.bib that is never written
- **Status:** FIX ATTEMPTED 2026-09-07
- **Code:** `explore_summary_names_absent_files` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-06, example_project
- **Last seen:** 2026-09-06, example_project (1 runs)

### 53. Filling the last section of plan/outline.md deletes the whole # Notes region
- **Status:** FIX ATTEMPTED 2026-09-07
- **Code:** `fill_or_append_eats_level1_region` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-07, example_project
- **Last seen:** 2026-09-07, example_project (1 runs)

### 54. Every unit-bearing key in methods_facts.yml is invisible to _recorded_fields
- **Status:** FIX ATTEMPTED 2026-09-07
- **Code:** `methods_facts_key_regex_drops_capitals` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-07, example_project
- **Last seen:** 2026-09-07, example_project (1 runs)

### 55. status reports no journal on a project whose project.yml holds target_journal
- **Status:** FIX ATTEMPTED 2026-09-07
- **Code:** `cached_journal_not_reported` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-07, example_project
- **Last seen:** 2026-09-07, example_project (1 runs)

### 56. agent-brief's draft-sections prompt names a scope it never supplies
- **Status:** FIX ATTEMPTED 2026-09-07
- **Code:** `brief_omits_round_scope` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-07, example_project
- **Last seen:** 2026-09-07, example_project (1 runs)

### 57. agent-brief builds a nonsense absolute path for the abstract module's second output
- **Status:** FIX ATTEMPTED 2026-09-07
- **Code:** `brief_writes_path_mangled` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-07, example_project
- **Last seen:** 2026-09-07, example_project (1 runs)

### 58. the harness auto-loads Paper Engine/CLAUDE.md into isolated sub-agents, unrequested
- **Status:** FIX ATTEMPTED 2026-09-07
- **Code:** `isolation_not_enforced_by_harness` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-07, example_project
- **Last seen:** 2026-09-07, example_project (1 runs)

### 59. a user's section scope narrows only draft-sections and revise-prose, so a two-section request still runs a whole submission build
- **Status:** FIX ATTEMPTED 2026-09-07
- **Code:** `section_scope_does_not_scope_the_round` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-07, example_project
- **Last seen:** 2026-09-07, example_project (1 runs)

### 60. the agent-call estimate never counted the abstract module, while the plan listed it as running
- **Status:** FIX ATTEMPTED 2026-09-07
- **Code:** `agent_call_estimate_omits_abstract` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-07, example_project
- **Last seen:** 2026-09-07, example_project (1 runs)

### 61. a `|` in a coauthor's request gained a backslash on every round
- **Status:** FIX ATTEMPTED 2026-09-07
- **Code:** `ledger_escape_survives_the_read` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-07, example_project
- **Last seen:** 2026-09-07, example_project (1 runs)

### 62. the retained-source marker was appended once per regeneration, not once
- **Status:** FIX ATTEMPTED 2026-09-07
- **Code:** `retained_marker_stacks_per_round` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-07, example_project
- **Last seen:** 2026-09-07, example_project (1 runs)

### 63. a denial reason illustrated itself with the sentence the denial exists to keep out
- **Status:** FIX ATTEMPTED 2026-09-07
- **Code:** `denial_reason_quotes_a_sentence` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-07, example_project
- **Last seen:** 2026-09-07, example_project (1 runs)

### 64. A skill calls an engine through a placeholder it never defines
- **Status:** FIX ATTEMPTED 2026-09-07
- **Code:** `skill_placeholder_undefined` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-07, example_project
- **Last seen:** 2026-09-07, example_project (1 runs)

### 65. Five skills ship the author's own folder layout as an instruction to every reader
- **Status:** FIX ATTEMPTED 2026-09-07
- **Code:** `skill_names_authors_own_layout` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-07, example_project
- **Last seen:** 2026-09-07, example_project (1 runs)

### 66. Uninstalling the plugin silently destroys the user's entire learned registry
- **Status:** FIX ATTEMPTED 2026-09-16
- **Code:** `plugin_data_lost_on_uninstall` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-07, example_project
- **Last seen:** 2026-09-07, example_project (1 runs)

### 67. A directory-source plugin install copies gitignored files, secrets included
- **Status:** FIX ATTEMPTED 2026-09-08
- **Code:** `directory_install_copies_ignored_files` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-07, example_project
- **Last seen:** 2026-09-07, example_project (1 runs)

### 68. a generated skill pointer whose target has moved gives the reader no instruction, so the skill is dropped silently
- **Status:** FIX ATTEMPTED 2026-09-09
- **Code:** `pointer_dead_path_gives_no_instruction` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-09, example_project
- **Last seen:** 2026-09-09, example_project (1 runs)

### 69. A test suite's defect records landed in the live spool that gets pushed upstream
- **Status:** FIX ATTEMPTED 2026-09-14
- **Code:** `suite_writes_the_shipping_spool` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-14, example_project
- **Last seen:** 2026-09-14, example_project (1 runs)

### 70. review.py search comma-joins --source into one scholar.py argument, which rejects it
- **Status:** FIX ATTEMPTED 2026-09-16
- **Code:** `corpus_search_multi_source_rejected` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-15, example_project
- **Last seen:** 2026-09-15, example_project (1 runs)

### 71. The outline inventory reads a review's corpus as nothing, so can_draft is false on a fully built corpus
- **Status:** FIX ATTEMPTED 2026-09-16
- **Code:** `review_outline_inventory_ignores_corpus` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-15, example_project
- **Last seen:** 2026-09-15, example_project (1 runs)

### 72. outline --write rejects a review's author-named sections, which the outline template itself tells the author to invent
- **Status:** FIX ATTEMPTED 2026-09-16
- **Code:** `review_outline_rejects_its_own_section_names` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-15, example_project
- **Last seen:** 2026-09-15, example_project (1 runs)

### 73. On a review, draft-sections is given citekeys but never the corpus the citekeys stand for
- **Status:** FIX ATTEMPTED 2026-09-16
- **Code:** `draft_sections_brief_omits_the_corpus` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-15, example_project
- **Last seen:** 2026-09-15, example_project (1 runs)

### 74. Harness auto-loaded instruction files reach an isolated module, and the only defence is asking the agent to confess
- **Status:** FIX ATTEMPTED 2026-09-16
- **Code:** `auto_loaded_instructions_bypass_the_brief` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-15, example_project
- **Last seen:** 2026-09-15, example_project (1 runs)

### 75. review.py tier reports four classes of false positive, and on a clean draft every finding it raised was one
- **Status:** FIX ATTEMPTED 2026-09-16
- **Code:** `tier_check_false_positives` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-15, example_project
- **Last seen:** 2026-09-15, example_project (1 runs)

### 76. prose.py length raises KeyError on any review, because section_words sums the four IMRaD stems unconditionally
- **Status:** FIX ATTEMPTED 2026-09-16
- **Code:** `prose_length_crashes_on_a_review` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-15, example_project
- **Last seen:** 2026-09-15, example_project (1 runs)

### 77. structure.py resolve crashes on a common-name --organism, and the traceback blames --name, which is the one argument that was correct
- **Status:** FIX ATTEMPTED 2026-09-16
- **Code:** `resolve_organism_name_crashes_and_blames_the_name` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-15, example_project
- **Last seen:** 2026-09-15, example_project (1 runs)

### 78. attribution-check's READ list grants the whole source_text directory, which contains title_abstract.md - so the brief hands the module the thesis its DENIED list exists to withhold
- **Status:** FIX ATTEMPTED 2026-09-16
- **Code:** `attribution_read_list_grants_the_abstract_it_denies` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-15, example_project
- **Last seen:** 2026-09-15, example_project (1 runs)

### 79. push --dry-run reports a send that consent and the missing token would both refuse
- **Status:** FIX ATTEMPTED 2026-09-16
- **Code:** `report_dry_run_ignores_consent_and_token` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-15, example_project
- **Last seen:** 2026-09-15, example_project (1 runs)

### 80. A crashed prose check is dropped at the call site, so the build loses the check and says nothing
- **Status:** FIX ATTEMPTED 2026-09-16
- **Code:** `prose_module_error_dropped_at_call_site` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-15, example_project
- **Last seen:** 2026-09-15, example_project (1 runs)

### 81. readability, voice and metaprose are built, tested and never called by a build
- **Status:** FIX ATTEMPTED 2026-09-15
- **Code:** `readability_engines_built_but_never_run` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-15, example_project
- **Last seen:** 2026-09-15, example_project (1 runs)

### 82. A float the user asked for as a graphic is built as an R script, and create-graphic-figure is never routed to
- **Status:** FIX ATTEMPTED 2026-09-16
- **Code:** `graphic_float_built_as_r_only` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-15, example_project
- **Last seen:** 2026-09-15, example_project (1 runs)

### 83. The Title Case rule lives only in the maintainer CLAUDE.md, so every figure label ships lower case
- **Status:** FIX ATTEMPTED 2026-09-16
- **Code:** `title_case_rule_never_reaches_the_skills` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-15, example_project
- **Last seen:** 2026-09-15, example_project (1 runs)

### 84. The NCBI key is written inside the plugin tree, so an update that replaces the directory destroys it
- **Status:** FIX ATTEMPTED 2026-09-16
- **Code:** `api_key_stored_inside_the_plugin_tree` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-15, example_project
- **Last seen:** 2026-09-15, example_project (1 runs)

### 85. Nothing asks the opening sentence to state a stake, so the paper opens on provenance
- **Status:** FIX ATTEMPTED 2026-09-16
- **Code:** `opening_sentence_has_no_stake_requirement` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-15, example_project
- **Last seen:** 2026-09-15, example_project (1 runs)

### 86. A suite check asserts a promotion outcome the defect loop itself may change, so a legitimately reopened item turns the suite red
- **Status:** FIX ATTEMPTED 2026-09-15
- **Code:** `suite_asserts_a_mutable_defect_status` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-15, example_project
- **Last seen:** 2026-09-15, example_project (1 runs)

### 87. A crashed engine subprocess still renders as a clean, empty result outside prose.py
- **Status:** FIX ATTEMPTED 2026-09-16
- **Code:** `engine_error_dropped_at_call_site` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-16, example_project
- **Last seen:** 2026-09-16, example_project (1 runs)

### 88. The section freeze has never fired: nothing writes the provenance ledger
- **Status:** FIX ATTEMPTED 2026-09-16
- **Code:** `section_freeze_ledger_never_written` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-16, example_project
- **Last seen:** 2026-09-16, example_project (1 runs)

### 89. An apply round prints 'It must not recompose any section' and then recomposes sections
- **Status:** FIX ATTEMPTED 2026-09-16
- **Code:** `round_intent_must_not_is_printed_only` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-16, example_project
- **Last seen:** 2026-09-16, example_project (1 runs)

### 90. A review is scaffolded with an IMRaD section_order, so its first build is refused
- **Status:** FIX ATTEMPTED 2026-09-16
- **Code:** `review_scaffolds_an_imrad_section_order` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-16, example_project
- **Last seen:** 2026-09-16, example_project (1 runs)

### 91. The Writing-Engine Skill States Module Counts That Its Own Engine Contradicts
- **Status:** FIX ATTEMPTED 2026-09-16
- **Code:** `skill_module_count_stale` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-16, example_project
- **Last seen:** 2026-09-16, example_project (1 runs)

### 92. A Standing Label Preference Did Not Reach the Skill That Writes the Labels
- **Status:** FIX ATTEMPTED 2026-09-16
- **Code:** `standing_preference_unreachable_from_the_writer` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-16, example_project
- **Last seen:** 2026-09-16, example_project (1 runs)

### 93. A suite function is defined and never called, so its contract is documented but unenforced
- **Status:** FIX ATTEMPTED 2026-09-17
- **Code:** `suite_test_never_registered` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-17, example_project
- **Last seen:** 2026-09-17, example_project (1 runs)

### 94. A file dropped into an installed plugin's resources/ is deleted by /plugin update
- **Status:** FIX ATTEMPTED 2026-09-17
- **Code:** `drop_zone_erased_by_plugin_update` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-17, example_project
- **Last seen:** 2026-09-17, example_project (1 runs)

### 95. The plugin version never moved while shipped content changed, and /plugin update reported everyone current
- **Status:** FIX ATTEMPTED 2026-09-17
- **Code:** `version_never_bumped_while_content_shipped` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-17, example_project
- **Last seen:** 2026-09-17, example_project (1 runs)

### 96. The facility prefill is built and tested, and no skill ever calls it
- **Status:** FIX ATTEMPTED 2026-09-17
- **Code:** `prefill_built_and_never_called` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-17, example_project
- **Last seen:** 2026-09-17, example_project (1 runs)

### 97. A curated resource pack can ship with no machine-readable half, and the prefill path then does nothing silently
- **Status:** FIX ATTEMPTED 2026-09-17
- **Code:** `pack_ships_without_the_machine_readable_half` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-17, example_project
- **Last seen:** 2026-09-17, example_project (1 runs)

### 98. a reference that is not a paper was reported not_found, the word reserved for invented citations
- **Status:** FIX ATTEMPTED 2026-09-18
- **Code:** `database_record_reported_not_found` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-18, example_project
- **Last seen:** 2026-09-18, example_project (1 runs)

### 99. survey proposed the manuscript folder for every Word document at a project root, including ones that are not this paper
- **Status:** FIX ATTEMPTED 2026-09-18
- **Code:** `survey_proposes_drafts_for_every_document` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-18, example_project
- **Last seen:** 2026-09-18, example_project (1 runs)

### 100. readability's abbreviation counter cannot see a lower-case-initial abbreviation
- **Status:** FIX ATTEMPTED 2026-09-21
- **Code:** `abbrev_token_case_blind` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-18, example_project
- **Last seen:** 2026-09-18, example_project (1 runs)

### 101. a parenthesis that defines two abbreviations at once defines neither
- **Status:** FIX ATTEMPTED 2026-09-21
- **Code:** `expansion_re_single_token_only` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-18, example_project
- **Last seen:** 2026-09-18, example_project (1 runs)

### 102. A superseded manuscript stays in drafts/ when the folder is not engine-scaffolded
- **Status:** FIX ATTEMPTED 2026-09-21
- **Code:** `superseded_round_not_retired` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-18, example_project
- **Last seen:** 2026-09-18, example_project (1 runs)

### 103. A drawn float's leaders and labels can overlap, and nothing checks it
- **Status:** FIX ATTEMPTED 2026-09-21
- **Code:** `graphic_elements_overlap_text` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-18, example_project
- **Last seen:** 2026-09-18, example_project (1 runs)

### 104. Nothing checks that a cited source supports the sentence it is attached to
- **Status:** FIX ATTEMPTED 2026-09-20
- **Code:** `citation_claim_unverified` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-19, example_project
- **Last seen:** 2026-09-19, example_project (1 runs)

### 105. A project cannot declare that its reference list is peer-reviewed only
- **Status:** FIX ATTEMPTED 2026-09-21
- **Code:** `peer_reviewed_only_not_enforceable` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-19, example_project
- **Last seen:** 2026-09-19, example_project (1 runs)

### 106. A skill's table promises an engine payload the engine never emits, so the resource pack goes unread in the one case it exists for
- **Status:** FIX ATTEMPTED 2026-09-21
- **Code:** `skill_claims_output_engine_never_emits` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-20, example_project
- **Last seen:** 2026-09-20, example_project (1 runs)

### 107. A fabricated author, a dropped first author and an off-by-one year all verify clean
- **Status:** FIX ATTEMPTED 2026-09-20
- **Code:** `reference_byline_and_year_checked_too_loosely` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-20, example_project
- **Last seen:** 2026-09-20, example_project (1 runs)

### 108. Two entries that render as the same author-year citation are never reported, so the reader cannot tell which paper is meant
- **Status:** FIX ATTEMPTED 2026-09-20
- **Code:** `author_year_citation_collision_unchecked` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-20, example_project
- **Last seen:** 2026-09-20, example_project (1 runs)

### 109. A sentence boundary is lost when a citation follows the terminal punctuation, in all three splitters
- **Status:** FIX ATTEMPTED 2026-09-20
- **Code:** `sentence_boundary_lost_to_trailing_citation` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-20, example_project
- **Last seen:** 2026-09-20, example_project (1 runs)

### 110. The preservation invariant is blind to spelled-out quantities, so a rewrite can change a magnitude silently
- **Status:** FIX ATTEMPTED 2026-09-20
- **Code:** `preservation_blind_to_spelled_out_magnitudes` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-20, example_project
- **Last seen:** 2026-09-20, example_project (1 runs)

### 111. A skill file documents the rule an engine stopped following, so the operator announces a round that does not happen
- **Status:** FIX ATTEMPTED 2026-09-20
- **Code:** `skill_documents_a_rule_the_code_stopped_following` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-20, example_project
- **Last seen:** 2026-09-20, example_project (1 runs)

### 112. A spreadsheet extension is classified as clear, so --apply moves an administrative file into the analysis pipeline's folder
- **Status:** FIX ATTEMPTED 2026-09-20
- **Code:** `adopt_moved_an_ambiguous_spreadsheet_into_data_raw` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-20, example_project
- **Last seen:** 2026-09-20, example_project (1 runs)

### 113. A case-differing filename satisfies a manifest entry, so check reports a template present that was never written
- **Status:** FIX ATTEMPTED 2026-09-20
- **Code:** `case_differing_name_satisfies_a_manifest_entry` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-20, example_project
- **Last seen:** 2026-09-20, example_project (1 runs)

### 114. The float-order check reports 0 floats at exit 0 when it had no section text to read, which is indistinguishable from a clean pass
- **Status:** FIX ATTEMPTED 2026-09-20
- **Code:** `crossrefs_reports_zero_floats_when_it_could_not_run` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-20, example_project
- **Last seen:** 2026-09-20, example_project (1 runs)

### 115. The in-paper AI declaration is never built from the file that holds it
- **Status:** FIX ATTEMPTED 2026-09-21
- **Code:** `ai_disclosure_section_not_built` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-21, example_project
- **Last seen:** 2026-09-21, example_project (1 runs)

### 116. The pack freshness check reports `current` by comparing two copies that go stale together
- **Status:** FIX ATTEMPTED 2026-09-23
- **Code:** `pack_current_while_stale` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-23, example_project
- **Last seen:** 2026-09-23, example_project (1 runs)

### 117. The freshness check is correct and nothing ever runs it, so a member sees UNKNOWN for ever
- **Status:** FIX ATTEMPTED 2026-09-24
- **Code:** `freshness_never_asked` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-24, example_project
- **Last seen:** 2026-09-24, example_project (1 runs)

### 118. A skill still documents the mechanism item 116 retired, so its stated guarantee rests on a thing that no longer exists
- **Status:** FIX ATTEMPTED 2026-09-24
- **Code:** `skill_doc_states_retired_mechanism` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-24, example_project
- **Last seen:** 2026-09-24, example_project (1 runs)

### 119. A round's end state (resolve-only vs submission-ready) is never asked when the request is ambiguous
- **Status:** FIX ATTEMPTED 2026-09-24
- **Code:** `round_intent_not_asked` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-24, example_project
- **Last seen:** 2026-09-24, example_project (1 runs)

### 120. A project whose earlier rounds exist only as built .docx files cannot be adopted
- **Status:** OPEN
- **Code:** `docx_only_project_not_adoptable` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-24, example_project
- **Last seen:** 2026-09-24, example_project (1 runs)

### 121. scholar.py verify returned a Year Book reprint as the verified match at confidence 1.0
- **Status:** OPEN
- **Code:** `verify_matched_a_reprint` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-24, example_project
- **Last seen:** 2026-09-24, example_project (1 runs)

### 122. requirements.yml reader treats a quoted '#' inside an inline list as a comment
- **Status:** FIX ATTEMPTED 2026-09-24
- **Code:** `yaml_hash_in_quoted_list` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-24, example_project
- **Last seen:** 2026-09-24, example_project (1 runs)

### 123. A template's commented-out worked example is read as real data
- **Status:** FIX ATTEMPTED 2026-09-24
- **Code:** `template_example_read_as_data` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-24, example_project
- **Last seen:** 2026-09-24, example_project (1 runs)

### 124. round aborts half-done when OneDrive denies rmdir of an emptied reports folder
- **Status:** FIX ATTEMPTED 2026-09-24
- **Code:** `round_rmdir_onedrive_partial` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-24, example_project
- **Last seen:** 2026-09-24, example_project (1 runs)

### 125. publish-docs swallows the comment terminator and drops the public-only tail
- **Status:** FIX ATTEMPTED 2026-09-24
- **Code:** `publish_docs_header_eats_terminator` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-24, example_project
- **Last seen:** 2026-09-24, example_project (1 runs)

### 126. attribution-check brief miscounts pairs when a citekey has no corpus record
- **Status:** FIX ATTEMPTED 2026-09-24
- **Code:** `attribution_pair_count_drops_unretrieved` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-24, example_project
- **Last seen:** 2026-09-24, example_project (1 runs)

### 128. no module can polish imported prose except via comprehension/quality findings
- **Status:** FIX ATTEMPTED 2026-09-24
- **Code:** `revise_prose_no_path_for_imported_prose` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-24, example_project
- **Last seen:** 2026-09-24, example_project (1 runs)

### 129. assemble writes byline, affiliations and ethics placeholder into a double-blind manuscript
- **Status:** FIX ATTEMPTED 2026-09-24
- **Code:** `assemble_ignores_anonymized_review` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-24, example_project
- **Last seen:** 2026-09-24, example_project (1 runs)

### 130. agent-brief puts redrafted sections in EDIT mode unless --redraft is passed to it again
- **Status:** FIX ATTEMPTED 2026-09-24
- **Code:** `agent_brief_ignores_run_redraft` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-24, example_project
- **Last seen:** 2026-09-24, example_project (1 runs)

### 131. A module's brief states a count of a file the engine could have counted
- **Status:** FIX ATTEMPTED 2026-09-24
- **Code:** `brief_states_a_count_of_its_own_input` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-24, example_project
- **Last seen:** 2026-09-24, example_project (1 runs)

### 132. A YAML block list is read back as one bracketed string, so an item containing a comma becomes two
- **Status:** FIX ATTEMPTED 2026-09-24
- **Code:** `block_list_items_lose_their_commas` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-24, example_project
- **Last seen:** 2026-09-24, example_project (1 runs)

### 133. An item whose fix a later run saw fail can never be re-marked and can never close
- **Status:** FIX ATTEMPTED 2026-09-24
- **Code:** `no_rung_for_a_second_fix` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-24, example_project
- **Last seen:** 2026-09-24, example_project (1 runs)

### 134. A section the journal's order names and the build carries is outside every prose pass and outside the freeze
- **Status:** OPEN
- **Code:** `ordered_section_outside_every_prose_pass` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-24, example_project
- **Last seen:** 2026-09-24, example_project (1 runs)

### 135. Separate-upload figures are never exported, and the folder is outside the submission package
- **Status:** FIX ATTEMPTED 2026-09-24
- **Code:** `figure_uploads_never_exported` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-24, example_project
- **Last seen:** 2026-09-24, example_project (1 runs)

### 136. supplementary_info_rN.docx is specified but never built
- **Status:** FIX ATTEMPTED 2026-09-24
- **Code:** `supplementary_docx_not_built` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-24, example_project
- **Last seen:** 2026-09-24, example_project (1 runs)

### 137. Introduction Opener Is Fixed by Rule Instead of Chosen per Paper
- **Status:** OPEN
- **Code:** `intro_opens_on_gap_not_subject` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-25, example_project
- **Last seen:** 2026-09-25, example_project (1 runs)

### 138. Running Title Is Placed After the Abstract Whatever the Section Order Says
- **Status:** FIX ATTEMPTED 2026-09-28
- **Code:** `no_page_break_after_abstract` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-25, example_project
- **Last seen:** 2026-09-25, example_project (1 runs)

### 139. Page Breaks Are Hard-Coded Instead of Read From the Journal Requirements
- **Status:** FIX ATTEMPTED 2026-09-28
- **Code:** `page_breaks_not_from_requirements` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-25, example_project
- **Last seen:** 2026-09-25, example_project (1 runs)

### 140. Drafter writes section headings one level too deep; assemble duplicates the section heading
- **Status:** FIX ATTEMPTED 2026-09-28
- **Code:** `drafted_heading_level_off_by_one` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-25, example_project
- **Last seen:** 2026-09-25, example_project (1 runs)

### 141. The CSL file can contradict the reference style the journal names, and nothing checks
- **Status:** FIX ATTEMPTED 2026-09-28
- **Code:** `csl_contradicts_style_name` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-25, example_project
- **Last seen:** 2026-09-25, example_project (1 runs)

### 142. A prose pass wrote framing that contradicts a recorded author decision
- **Status:** FIX ATTEMPTED 2026-09-28
- **Code:** `prose_contradicts_author_decision` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-25, example_project
- **Last seen:** 2026-09-25, example_project (1 runs)

### 143. The abstract does not state the basis of the paper's central comparison
- **Status:** FIX ATTEMPTED 2026-09-28
- **Code:** `abstract_omits_comparison_basis` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-25, example_project
- **Last seen:** 2026-09-25, example_project (1 runs)

### 144. Importing prose from a .docx drops its reviewer comments without a word
- **Status:** FIX ATTEMPTED 2026-09-28
- **Code:** `import_drops_docx_comments` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-25, example_project
- **Last seen:** 2026-09-25, example_project (1 runs)

### 145. A PI's or coauthor's explicit edit requests are not given priority over the engine's own revision goals
- **Status:** FIX ATTEMPTED 2026-09-28
- **Code:** `coauthor_edits_not_prioritized` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-25, example_project
- **Last seen:** 2026-09-25, example_project (1 runs)

### 146. Bold or italic figure and table callouts pass every check
- **Status:** FIX ATTEMPTED 2026-09-28
- **Code:** `callout_emphasis_not_checked` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-25, example_project
- **Last seen:** 2026-09-25, example_project (1 runs)

### 147. Prose passes rewrite or remove a coauthor's tracked-change insertions, above all his transitions
- **Status:** FIX ATTEMPTED 2026-09-28
- **Code:** `prose_pass_reverts_coauthor_edits` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-25, example_project
- **Last seen:** 2026-09-25, example_project (1 runs)

### 148. The open-on-the-point rule puts the finding before the reasoning that leads to it
- **Status:** FIX ATTEMPTED 2026-09-28
- **Code:** `claim_first_breaks_reasoning_order` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-25, example_project
- **Last seen:** 2026-09-25, example_project (1 runs)

### 149. A required statement is inserted as its own section outside the journal's section order
- **Status:** FIX ATTEMPTED 2026-09-28
- **Code:** `required_statement_placed_outside_order` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-25, example_project
- **Last seen:** 2026-09-25, example_project (1 runs)

### 150. The supplement is left out of the round's revision, so fixes to the main text never reach it
- **Status:** FIX ATTEMPTED 2026-09-28
- **Code:** `supplement_skipped_by_revision` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-25, example_project
- **Last seen:** 2026-09-25, example_project (1 runs)

### 151. When the journal says nothing about page breaks, break before every major section (amends item 139)
- **Status:** FIX ATTEMPTED 2026-09-28
- **Code:** `page_break_default_when_silent` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-25, example_project
- **Last seen:** 2026-09-25, example_project (1 runs)

### 152. Paragraph indentation is never read from the journal, and the engine has no default
- **Status:** FIX ATTEMPTED 2026-09-28
- **Code:** `paragraph_indent_not_from_requirements` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-25, example_project
- **Last seen:** 2026-09-25, example_project (1 runs)

### 153. The requirements capture stops part-way through a source and still builds the package
- **Status:** FIX ATTEMPTED 2026-09-28
- **Code:** `requirements_source_partly_read` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-25, example_project
- **Last seen:** 2026-09-25, example_project (1 runs)

### 154. A FLAG asks the author to put an identifier into the anonymized manuscript
- **Status:** FIX ATTEMPTED 2026-09-28
- **Code:** `flag_requests_identifier_in_blinded_file` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-25, example_project
- **Last seen:** 2026-09-25, example_project (1 runs)

### 155. The supplement is always built as .docx, even when the journal asks for a single PDF
- **Status:** FIX ATTEMPTED 2026-09-28
- **Code:** `supplement_format_not_journal_preferred` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-25, example_project
- **Last seen:** 2026-09-25, example_project (1 runs)

### 156. The supplement's content is never checked against the journal's rule on what it may contain
- **Status:** FIX ATTEMPTED 2026-09-28
- **Code:** `supplement_content_rule_unchecked` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-25, example_project
- **Last seen:** 2026-09-25, example_project (1 runs)

### 157. Correction to item 152: IJROBP says nothing about indentation, so the default applies
- **Status:** FIX ATTEMPTED 2026-09-28
- **Code:** `indent_source_correction_152` - filed with `manuscript.py log-issue` during a run
- **First seen:** 2026-09-25, example_project
- **Last seen:** 2026-09-25, example_project (1 runs)

---

## Closed items
*(An item arrives here only after a run has completed without seeing it - `VERIFIED FIXED`, never `FIX ATTEMPTED`. Only its code, status and last sighting are kept; the detail is cleared on closing.)*

### 1. `assemble` advances the round number on every rebuild
- **Status:** VERIFIED FIXED

### 2. A `refs`-kind section is built with no heading
- **Status:** VERIFIED FIXED

### 3. `read_flat_yml` truncates a multi-line inline list, and the build still exits 0
- **Status:** VERIFIED FIXED

### 4. `pubmed.py cite --style bibtex` inverts every author name
- **Status:** VERIFIED FIXED

### 5. `structure.section_order` slugs print verbatim as headings, with no guard
- **Status:** VERIFIED FIXED

### 6. Make the engine maintain this file itself
- **Status:** VERIFIED FIXED — built 2026-09-03 on request. **Not verified:**

### 7. `toc_graphic.py` has its own weaker copy of `read_flat_yml`
- **Status:** VERIFIED FIXED

### 11. A `**[FLAG: ...]**` spanning paragraphs reaches the `.docx` as literal asterisks
- **Status:** VERIFIED FIXED 2026-09-07; was FIX ATTEMPTED 2026-09-03. Details cleared on closing; the fix is in the code and its commit.
- **Code:** `markdown_on_the_page` - registered 2026-09-06 so a run can log it and close it

### 26. A citekey survived citeproc into the rendered document
- **Status:** VERIFIED FIXED 2026-09-07; was FIX ATTEMPTED 2026-09-04. Details cleared on closing; the fix is in the code and its commit.
- **Code:** `citekey_on_the_page` - logged by the engine on the run that saw it
- **Last seen:** 2026-09-03, example_project (1 runs)

### 14. Two word counts, three times apart, and neither says what it counted
- **Status:** VERIFIED FIXED 2026-09-16; was FIX ATTEMPTED 2026-09-16. Details cleared on closing; the fix is in the code and its commit.
- **Code:** `word_counts_unexplained` - registered 2026-09-06 so a run can log it and close it
- **Last seen:** 2026-09-15, example_project (19 runs)

### 9. The build summary's section list is not the document's section order
- **Status:** VERIFIED FIXED 2026-09-25; was FIX ATTEMPTED 2026-09-24. Details cleared on closing; the fix is in the code and its commit.
- **Code:** `summary_not_the_document` - registered 2026-09-06 so a run can log it and close it
- **Last seen:** 2026-09-24, example_project (30 runs)

### 127. revise-prose --from-quality sees ai_voice densities through quality_report.md
- **Status:** VERIFIED FIXED 2026-09-25; was FIX ATTEMPTED 2026-09-24. Details cleared on closing; the fix is in the code and its commit.
- **Code:** `quality_report_leaks_densities` - filed with `manuscript.py log-issue` during a run
- **Last seen:** 2026-09-24, example_project (1 runs)
## Run history
Which runs have exercised the engine.

| Date | Project | Journal | Preset | Result |
| --- | --- | --- | --- | --- |
| 2026-09-16 | example_project | JACS | `submission` | r1: built manuscript_r1.docx |
| 2026-09-16 | example_project | JACS | `submission` | r1: built manuscript_r1.docx |
| 2026-09-17 | example_project | JACS | `submission` | r1: built manuscript_r1.docx |
| 2026-09-24 | example_project | IJROBP | `submission` | r4: built manuscript_r1.docx |
| 2026-09-25 | example_project | LANGMUIR | `sections` | r2: built manuscript_r1.docx |
