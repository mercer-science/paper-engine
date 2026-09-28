#!/usr/bin/env Rscript
# plan/render_all.R ----------------------------------------------------------
# The whole float pipeline in one command:
#
#     Rscript plan/render_all.R
#
# 1. check that every package the pipeline needs is installed, once, up front
# 2. run every script in every float folder under plan/figures/ and plan/tables/
# 3. build plan/preview.html
# 4. build plan/floats/figures_and_tables.docx
#
# ONE FLOAT, ONE FOLDER. Figure 1 is plan/figures/Fig01/, and everything that
# belongs to it - the script, the .png, the .pdf, any PowerPoint panel art -
# lives inside it. The folder name carries the number, so renumbering a figure
# is renaming its folder and nothing inside it moves. Folders starting with "_"
# are skipped, which is what keeps plan/figures/_template/ out of the run.
#
# Within a folder, scripts run in sorted order with ONE exception: figure.R
# (table.R) always runs last. A panel-rendering script has to produce its .png
# before the script that composes it reads that .png, and sorted order alone
# puts figure.R first. That rule lives here rather than in a per-project
# convention because getting it wrong builds the figure from the PREVIOUS
# run's panel art, which looks entirely fine.

suppressPackageStartupMessages(library(here))

# --- one up-front package check --------------------------------------------
# Collect everything missing and print a single install line, rather than
# failing on script 4 of 9 and again on script 7 after you fix the first.

REQUIRED <- c("ggplot2", "dplyr", "readr", "patchwork", "yaml", "here",
              "jsonlite", "ragg", "png", "grid", "officer", "flextable",
              "base64enc", "colorspace", "farver")
missing <- REQUIRED[!vapply(REQUIRED, requireNamespace, logical(1), quietly = TRUE)]
if (length(missing)) {
  cat("Missing packages needed by the float pipeline:\n\n")
  cat(sprintf('  install.packages(c(%s))\n\n',
              paste0('"', missing, '"', collapse = ", ")))
  quit(status = 1)
}

source(here::here("plan", "setup.R"))

# --- discovery --------------------------------------------------------------
# OneDrive writes a conflict copy whenever two machines touch the same file
# ("fig02_yield-DESKTOP-A4B6PBU.R", "...-DESKTOP-A4B6PBU-2.R"). Running those
# silently re-renders a stale version over the current one, so they are
# excluded here and hidden in .vscode/settings.json.

ONEDRIVE_CONFLICT_RE <- "-DESKTOP-[A-Z0-9]+(-[0-9]+)?"

# The one ordering rule: figure.R / table.R last. See the header.
LAST_IN_FOLDER <- c("figure.R", "table.R")

scripts_in <- function(dir) {
  f <- list.files(dir, pattern = "\\.R$", full.names = TRUE)
  f <- f[!startsWith(basename(f), "_")]
  f <- f[!grepl(ONEDRIVE_CONFLICT_RE, basename(f))]
  f <- sort(f)
  last <- basename(f) %in% LAST_IN_FOLDER
  c(f[!last], f[last])
}

floats <- list_float_dirs()

jobs <- list()
for (fl in floats) {
  for (sc in scripts_in(fl$path)) {
    jobs[[length(jobs) + 1L]] <- list(script = sc, float = fl)
  }
}
scripts <- vapply(jobs, function(j) j$script, character(1))

# Loose .R files directly in plan/figures/ - reported after the run.
unclaimed <- character(0)
for (kind in c("figures", "tables")) {
  d <- here::here("plan", kind)
  if (!dir.exists(d)) next
  loose <- list.files(d, pattern = "\\.R$", full.names = FALSE)
  loose <- loose[!startsWith(loose, "_")]
  if (length(loose)) unclaimed <- c(unclaimed, file.path("plan", kind, loose))
}

# Stale provenance would let a figure that is no longer built keep claiming a
# slot (and keep the mock-data hard stop armed), so the register is rebuilt
# from scratch on every full render.
prov <- here::here("plan", "floats", "float_provenance.json")
if (file.exists(prov)) unlink(prov)

cat(sprintf("Rendering %d script%s across %d float%s [%s geometry]\n",
            length(scripts), if (length(scripts) == 1) "" else "s",
            length(floats), if (length(floats) == 1) "" else "s",
            JOURNAL$preset))

failed <- character(0)
warned <- character(0)

for (job in jobs) {
  s <- job$script
  fl <- job$float
  nm <- file.path(basename(fl$path), basename(s))
  cat(sprintf("  %-40s ", nm))
  float_reset()
  # The script writes into its own folder and is recorded under its folder's
  # label, so it never has to name either.
  set_float_context(fl$path, fl$label)
  n_warn <- 0L
  ok <- withCallingHandlers(
    tryCatch({
      # Each script gets its own environment: a stray `d` or `p` in one script
      # must not leak into the next and quietly render the wrong data.
      env <- new.env(parent = globalenv())
      sys.source(s, envir = env)
      TRUE
    }, error = function(e) {
      cat("FAILED\n")
      cat("      ", conditionMessage(e), "\n")
      failed <<- c(failed, nm)
      FALSE
    }),
    warning = function(w) {
      if (n_warn == 0L) cat("\n")
      n_warn <<- n_warn + 1L
      cat("      warning: ", conditionMessage(w), "\n", sep = "")
      warned <<- c(warned, nm)
      invokeRestart("muffleWarning")
    },
    message = function(m) {
      if (n_warn == 0L) cat("\n")
      n_warn <<- n_warn + 1L
      cat("      ", conditionMessage(m), sep = "")
      invokeRestart("muffleMessage")
    })
  if (isTRUE(ok) && n_warn == 0L) cat("ok\n")
  set_float_context(NULL, NULL)
}

# An empty float folder is a slot somebody made and has not filled yet, which
# is normal. A loose .R directly in plan/figures/ is not: under the flat layout
# that file WAS the figure, so ignoring it quietly is how a project carried
# over from the old shape renders nothing and still reports success.
if (length(unclaimed)) {
  cat("\n  Not rendered - a float lives in its own folder now:\n")
  for (u in unclaimed) cat("    ", u, "\n", sep = "")
  cat("    Move each into plan/figures/FigNN/figure.R (tables: TableNN/table.R),\n")
  cat("    or run:  python tools/scaffold.py float migrate <project>\n")
}

cat("\n")
options(project.preview.written = NA_character_,
        project.floats.written  = NA_character_)
source(here::here("plan", "generate_preview.R"))
source(here::here("plan", "create_floats.R"))

cat("\n")
cat(sprintf("%d/%d scripts ok", length(scripts) - length(failed), length(scripts)))
if (length(failed)) cat(sprintf(", %d failed: %s", length(failed),
                                paste(failed, collapse = ", ")))
if (length(warned)) cat(sprintf(", %d with warnings", length(unique(warned))))
cat("\n")
# Name only what was actually written. create_floats.R refuses to build the
# co-author document from mock data, and a summary that lists it regardless is
# how someone ends up emailing last week's version of it.
rel <- function(p) sub(paste0("^", here::here(), "/"), "", p)
for (o in c("project.preview.written", "project.floats.written")) {
  v <- getOption(o, NA_character_)
  if (!is.null(v) && !is.na(v)) cat("  ", rel(v), "\n", sep = "")
}
if (is.na(getOption("project.floats.written", NA_character_))) {
  cat("  plan/floats/figures_and_tables.docx   NOT WRITTEN\n")
}

if (length(failed)) quit(status = 1)
