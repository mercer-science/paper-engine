# plan/theme/theme_journal.R -------------------------------------------------
# Journal geometry, palette, and the ggplot theme every float inherits.
#
# Load-bearing, not decoration: this file defines BASE_SIZE and the PANEL_*
# constants that helpers.R reads, and it calls theme_set(), so every plot in
# the project picks the theme up without asking. setup.R sources it first.
#
# Geometry comes from project.yml:target_journal. Change the journal there,
# re-run plan/render_all.R, and every float is re-emitted at the new width.

JOURNAL_PRESETS <- list(
  acs = list(
    label     = "ACS (JACS, ACS Catal., Inorg. Chem.)",
    single    = 3.33,          # inches
    double    = 7.00,
    dpi       = 300,           # raster
    dpi_line  = 1200,          # line art
    base_size = 8,
    family    = "sans"),
  wiley = list(
    label     = "Wiley (Angew. Chem., Chem. Eur. J.)",
    single    = 8.0 / 2.54,    # 8.0 cm
    double    = 17.0 / 2.54,   # 17.0 cm
    dpi       = 300,
    dpi_line  = 300,
    base_size = 8,
    family    = "sans"),
  nature = list(
    label     = "Nature",
    single    = 89 / 25.4,     # 89 mm
    double    = 183 / 25.4,    # 183 mm
    dpi       = 300,
    dpi_line  = 300,
    base_size = 7,
    family    = "sans"),
  generic = list(
    label     = "generic",
    single    = 3.5,
    double    = 7.0,
    dpi       = 600,
    dpi_line  = 600,
    base_size = 9,
    family    = "sans")
)

# Substring match against a lowercased journal name. Deliberately loose: the
# point is that "Angewandte Chemie International Edition" and "Angew. Chem."
# both land on the Wiley geometry without anyone maintaining a journal list.
JOURNAL_ALIASES <- c(
  "jacs" = "acs", "am chem soc" = "acs", "acs " = "acs", "acs." = "acs",
  "inorg chem" = "acs", "org lett" = "acs", "j org chem" = "acs",
  "chem mater" = "acs", "j phys chem" = "acs", "biochemistry" = "acs",
  "angew" = "wiley", "chem eur j" = "wiley", "chemistry - a european" = "wiley",
  "adv mater" = "wiley", "adv funct" = "wiley", "small" = "wiley",
  "wiley" = "wiley",
  "nature" = "nature", "nat commun" = "nature", "nat chem" = "nature",
  "nat struct" = "nature", "nat methods" = "nature", "nat. " = "nature"
)

journal_preset <- function(journal = NULL) {
  key <- "generic"
  if (!is.null(journal) && nzchar(trimws(journal))) {
    j <- tolower(trimws(journal))
    hit <- names(JOURNAL_ALIASES)[vapply(names(JOURNAL_ALIASES),
                                         function(a) grepl(a, j, fixed = TRUE),
                                         logical(1))]
    if (length(hit)) key <- unname(JOURNAL_ALIASES[[hit[1]]])
    if (j %in% names(JOURNAL_PRESETS)) key <- j   # "acs", "wiley", ... spelled directly
  }
  c(JOURNAL_PRESETS[[key]], list(preset = key))
}

#' Sourced geometry from the journal's own author guidelines, if we have it.
#'
#' writing-engine writes plan/theme/journal_target.yml out of the journal
#' requirements it fetched, and this file prefers it over the preset table.
#' The presets are a good guess by publisher family; the target file is the
#' journal's actual column widths and DPI. A figure sized to a preset and
#' submitted to a journal with different columns is exactly what a copy editor
#' bounces, so a sourced value always wins.
#'
#' Only fields that carry a value are taken. A blank in the file means the
#' requirement was not determined, and an unknown requirement must never turn
#' into an invented column width - the preset stays, and the discrepancy is
#' visible in journal_requirements/requirements.md.
apply_journal_target <- function(preset,
                                 path = here::here("plan", "theme",
                                                   "journal_target.yml")) {
  if (!file.exists(path)) return(preset)
  target <- tryCatch(yaml::read_yaml(path), error = function(e) NULL)
  if (!length(target)) return(preset)
  taken <- character(0)
  for (k in c("single", "double", "dpi", "dpi_line", "base_size")) {
    v <- target[[k]]
    if (!is.null(v) && length(v) == 1 && !is.na(v) && is.numeric(v)) {
      preset[[k]] <- v
      taken <- c(taken, k)
    }
  }
  if (length(taken)) {
    preset$preset <- paste0(preset$preset, " + journal_target(",
                            paste(taken, collapse = ", "), ")")
    preset$label <- paste0(if (is.null(target$journal)) "journal"
                           else target$journal, " (sourced geometry)")
  }
  preset
}

JOURNAL <- apply_journal_target(
  journal_preset(if (exists("PROJECT")) PROJECT$target_journal else NULL))

BASE_SIZE  <- JOURNAL$base_size
COL_SINGLE <- JOURNAL$single
COL_DOUBLE <- JOURNAL$double
DPI        <- JOURNAL$dpi

# Panel composition constants. helpers.R reads all three.
PANEL_TITLE_SIZE   <- BASE_SIZE + 1
PANEL_TITLE_GAP_PT <- 2
PANEL_INDENT_PT    <- 4

# Okabe-Ito: the standard eight-colour set chosen to stay separable under the
# common colour vision deficiencies. save_float()'s legibility check still runs
# - a safe palette used carelessly (two adjacent blues) is still unreadable.
PALETTE <- c("#0072B2", "#D55E00", "#009E73", "#CC79A7",
             "#E69F00", "#56B4E9", "#F0E442", "#000000")

# --- in-panel annotation ----------------------------------------------------
# A stat label, an inside legend, or a note drawn INSIDE a panel sits on top of
# the data, so it needs a background to stay readable - and it needs ONE style,
# or six panels stop reading as one figure. White at 70% is "fogged": the label
# is legible and the data under it is still visible, which a solid box loses.
#
# helpers.R reads all four. Use annot_label() rather than a bare annotate().
ANNOT_SIZE_PT <- BASE_SIZE + 1
ANNOT_FILL    <- scales::alpha("white", 0.7)
ANNOT_COLOUR  <- "black"
ANNOT_SIZE    <- ANNOT_SIZE_PT / ggplot2::.pt   # geom_* size is mm, not points

theme_journal <- function(base_size = BASE_SIZE, base_family = JOURNAL$family) {
  ggplot2::theme_bw(base_size = base_size, base_family = base_family) +
    ggplot2::theme(
      panel.grid.minor   = ggplot2::element_blank(),
      panel.grid.major   = ggplot2::element_line(linewidth = 0.25, colour = "grey90"),
      panel.border       = ggplot2::element_rect(linewidth = 0.4, colour = "grey20"),
      axis.ticks         = ggplot2::element_line(linewidth = 0.3, colour = "grey20"),
      axis.text          = ggplot2::element_text(size = base_size - 1, colour = "grey20"),
      axis.title         = ggplot2::element_text(size = base_size),
      strip.background   = ggplot2::element_rect(fill = "grey95", colour = NA),
      strip.text         = ggplot2::element_text(size = base_size, margin = ggplot2::margin(3, 3, 3, 3)),
      legend.key.size    = grid::unit(0.9, "lines"),
      legend.text        = ggplot2::element_text(size = base_size - 1),
      legend.title       = ggplot2::element_text(size = base_size),
      legend.background  = ggplot2::element_blank(),
      plot.title         = ggplot2::element_text(size = PANEL_TITLE_SIZE, hjust = 0,
                                                 margin = ggplot2::margin(b = PANEL_TITLE_GAP_PT)),
      plot.tag           = ggplot2::element_text(size = PANEL_TITLE_SIZE + 1, face = "bold"),
      plot.margin        = ggplot2::margin(2, 2, 2, 2)
    )
}

ggplot2::theme_set(theme_journal())
options(ggplot2.discrete.colour = PALETTE,
        ggplot2.discrete.fill   = PALETTE)
