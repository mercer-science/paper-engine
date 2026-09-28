# plan/setup.R ---------------------------------------------------------------
# The one line every figure, table, and analysis script starts with:
#
#     source(here::here("plan", "setup.R"))
#
# It loads the common packages, then sources the theme and helper layers *in
# that order* (helpers.R reads BASE_SIZE and the panel constants that
# theme_journal.R defines - getting the order wrong fails in a confusing way,
# which is the whole reason this file exists), adds the caption parser and the
# PowerPoint panel renderer, exposes PROJECT from
# project.yml, and stamps data/analysis/provenance.json so the software half of
# the methods section is recorded without anyone typing it.

suppressPackageStartupMessages({
  library(here)
  library(ggplot2)
  library(dplyr)
  library(readr)
  library(patchwork)
  library(yaml)
})

PROJECT <- yaml::read_yaml(here::here("project.yml"))

# Seed every script the same way, so a jittered point or a bootstrap CI does not
# move between runs and quietly change a figure.
PROJECT_SEED <- if (is.null(PROJECT$seed)) 1L else as.integer(PROJECT$seed)
set.seed(PROJECT_SEED)

source(here::here("plan", "theme", "theme_journal.R"))
source(here::here("plan", "theme", "helpers.R"))
source(here::here("plan", "theme", "read_captions.R"))
source(here::here("plan", "theme", "render_pptx.R"))

# --- free provenance --------------------------------------------------------
# Written once per R session, not once per script, so a full render does not
# rewrite it a dozen times. `write_provenance()` is exported for the rare case
# of wanting it refreshed mid-session.

write_provenance <- function() {
  pkgs <- c("base", "ggplot2", "dplyr", "readr", "patchwork", "yaml", "here",
            "officer", "flextable")
  versions <- vapply(pkgs, function(p) {
    v <- tryCatch(as.character(utils::packageVersion(p)), error = function(e) NA_character_)
    if (is.na(v)) "not installed" else v
  }, character(1))

  out <- here::here("data", "analysis", "provenance.json")
  dir.create(dirname(out), recursive = TRUE, showWarnings = FALSE)
  jsonlite::write_json(
    list(generated = format(Sys.time(), "%Y-%m-%dT%H:%M:%S%z"),
         r_version = R.version.string,
         platform  = R.version$platform,
         seed      = PROJECT_SEED,
         packages  = as.list(versions)),
    out, auto_unbox = TRUE, pretty = TRUE)
  invisible(out)
}

if (!isTRUE(getOption("project.provenance.written"))) {
  write_provenance()
  options(project.provenance.written = TRUE)
}
