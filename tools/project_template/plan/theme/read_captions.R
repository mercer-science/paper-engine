# plan/theme/read_captions.R -------------------------------------------------
# The one and only parser for plan/captions.md.
#
# captions.md is hand-written and is the source of truth for float captions,
# their subtitles, and manuscript order. generate_preview.R, create_floats.R,
# and the manuscript-writing skill all call read_captions() - nothing else
# parses that file. That single-source property is what makes editing a caption
# once propagate to the preview, the Word floats, and the paper.
#
# Format (one block per float; document order IS manuscript order):
#
#   ## Figure 1 - Fig01
#   **The bold sentence stating the claim this float supports.**
#   Panel keys and plotting detail. (A) ... (B) ... n = 3; error bars, SD.
#
# The heading names the float's FOLDER (plan/figures/Fig01/), and it is matched
# by NUMBER rather than by spelling - so Fig01, Fig1 and Fig01_yield_by_catalyst
# all resolve to the same float. See float_dir_for() below.
#
# Heading pattern: "## " (Figure|Table) (S?N) <dash> <folder>
# The dash may be an em dash, an en dash, or one or more hyphens - the file is
# typed by hand and being strict about the dash character helps nobody.

CAPTION_HEADING_RE <- "^##\\s+(Figure|Table)\\s+(S?[0-9]+)\\s*(?:—|–|-{1,2})\\s*(\\S+)\\s*$"

#' Parse plan/captions.md into an ordered manifest.
#'
#' @return a list of entries, each with: type ("Figure"/"Table"), number,
#'   supplementary (logical), file, label ("Figure 1"), subtitle, caption, order.
read_captions <- function(path = here::here("plan", "captions.md")) {
  if (!file.exists(path)) return(list())
  lines <- readLines(path, warn = FALSE, encoding = "UTF-8")

  # Strip HTML comments, including multi-line ones - the scaffolded instruction
  # block is a comment and must not read as caption text.
  keep <- rep(TRUE, length(lines))
  in_comment <- FALSE
  for (i in seq_along(lines)) {
    l <- lines[i]
    if (!in_comment && grepl("<!--", l, fixed = TRUE)) {
      keep[i] <- FALSE
      in_comment <- !grepl("-->", l, fixed = TRUE)
    } else if (in_comment) {
      keep[i] <- FALSE
      if (grepl("-->", l, fixed = TRUE)) in_comment <- FALSE
    }
  }
  lines <- lines[keep]

  entries <- list()
  current <- NULL
  body <- character(0)

  flush <- function() {
    if (is.null(current)) return(invisible(NULL))
    txt <- trimws(body)
    txt <- txt[nzchar(txt)]
    subtitle <- ""
    if (length(txt) && grepl("^\\*\\*.*\\*\\*$", txt[1])) {
      subtitle <- trimws(gsub("^\\*\\*|\\*\\*$", "", txt[1]))
      txt <- txt[-1]
    }
    current$subtitle <- subtitle
    current$caption <- paste(txt, collapse = " ")
    current$order <- length(entries) + 1L
    entries[[length(entries) + 1L]] <<- current
    invisible(NULL)
  }

  for (l in lines) {
    m <- regmatches(l, regexec(CAPTION_HEADING_RE, l))[[1]]
    if (length(m) == 4) {
      flush()
      num <- m[3]
      current <- list(type = m[2], number = num,
                      supplementary = startsWith(num, "S"),
                      file = m[4],
                      label = paste(m[2], num))
      body <- character(0)
    } else if (!is.null(current)) {
      body <- c(body, l)
    }
  }
  flush()
  entries
}

#' Look one entry up by the output file it names.
caption_for <- function(entries, file) {
  for (e in entries) if (identical(e$file, basename(file))) return(e)
  NULL
}

# --- float folders ----------------------------------------------------------
# A float lives in its own numbered folder: plan/figures/Fig01/,
# plan/tables/Table01/. The folder name carries the number, so renumbering a
# float is renaming its folder - and the script, .png and .pdf inside it are
# named for their role rather than their position, so nothing else moves.
#
# FLOAT_DIR_RE is a contract with tools/prose.py, which carries the same
# pattern. Change one, change both, and re-run tests/prose.py - it reads this
# regex out of this file and asserts the two agree.
#
# Tolerant on purpose. All of these are Figure 1:
#   Fig01   Fig1   Fig 1   Fig_1   Figure01   Fig01_yield_by_catalyst
# and all of these are Figure S1:
#   FigS01  FigS1  Fig S1  FigureS01
# The trailing slug is free text for the human reading the file tree; the
# number is what identifies the float, which is why adding or changing a slug
# needs no edit anywhere else.

FLOAT_DIR_RE <- "^(Fig|Figure|Tab|Table)[ _-]?(S?)([0-9]+)([ _-].*)?$"

# OneDrive writes "Fig01-DESKTOP-A4B6PBU" beside "Fig01" when two machines
# touch the same folder. Rendering that silently re-renders a stale copy over
# the current one, so it is excluded here exactly as render_all.R excludes the
# file-level version.
ONEDRIVE_CONFLICT_RE_DIR <- "-DESKTOP-[A-Z0-9]+(-[0-9]+)?"

#' Parse a float folder name into its canonical identity.
#'
#' @return NULL when the name is not a float folder, otherwise a list with
#'   type ("Figure"/"Table"), number ("1", "S1"), supplementary, label, dir.
parse_float_dir <- function(name) {
  m <- regmatches(name, regexec(FLOAT_DIR_RE, name, ignore.case = TRUE))[[1]]
  if (length(m) != 5) return(NULL)
  type <- if (tolower(substr(m[2], 1, 3)) == "fig") "Figure" else "Table"
  supp <- toupper(m[3]) == "S"
  # Leading zeros are presentation, not identity: Fig01 and Fig1 are the same
  # float, and a caption block may name either.
  n <- as.integer(m[4])
  num <- paste0(if (supp) "S" else "", n)
  list(type = type, number = num, n = n, supplementary = supp,
       label = paste(type, num), dir = name)
}

#' Every float folder under plan/figures/ and plan/tables/, in manuscript order.
#'
#' Loose .R files sitting directly in those directories are NOT floats and are
#' not returned - see the `unclaimed` note in render_all.R.
list_float_dirs <- function(root = here::here("plan")) {
  out <- list()
  for (kind in c("figures", "tables")) {
    base <- file.path(root, kind)
    if (!dir.exists(base)) next
    for (nm in list.dirs(base, full.names = FALSE, recursive = FALSE)) {
      if (startsWith(nm, "_") || startsWith(nm, ".")) next
      if (grepl(ONEDRIVE_CONFLICT_RE_DIR, nm)) next
      f <- parse_float_dir(nm)
      if (is.null(f)) next
      f$path <- file.path(base, nm)
      f$kind <- kind
      out[[length(out) + 1L]] <- f
    }
  }
  # Figures first, then tables; within each, the main sequence and then the
  # supplementary one, in numeric order. tools/scaffold.py's list_float_dirs()
  # sorts identically - the preview, the Word floats and the Python engines
  # have to agree on what "the float set" is and what order it is in.
  if (!length(out)) return(out)
  ord <- order(vapply(out, function(f) !identical(f$kind, "figures"), logical(1)),
               vapply(out, function(f) f$supplementary, logical(1)),
               vapply(out, function(f) f$n, integer(1)))
  out[ord]
}

#' Find the float folder a caption entry refers to.
#'
#' Resolution is by NUMBER, never by string equality: a caption block naming
#' `Fig01` must still resolve after the folder is renamed to
#' `Fig01_yield_by_catalyst`, because the slug is for the reader and the
#' number is the identity. That is the whole reason a slug can be added
#' without touching captions.md.
float_dir_for <- function(entry, dirs = list_float_dirs()) {
  for (f in dirs) {
    if (identical(f$type, entry$type) && identical(f$number, entry$number)) return(f)
  }
  NULL
}
