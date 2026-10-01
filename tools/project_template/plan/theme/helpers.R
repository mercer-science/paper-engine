# plan/theme/helpers.R -------------------------------------------------------
# Panel composition, float saving, data provenance, and the legibility check.
# Sourced by plan/setup.R *after* theme_journal.R, which defines BASE_SIZE,
# COL_SINGLE/COL_DOUBLE, DPI and the PANEL_* constants used below.
#
# The rule the whole float pipeline rests on: scripts call save_float() and
# save_table(), never a bare ggsave() or write_*(). Those two functions are
# what make the .png/.pdf pair, the journal geometry, the mock-data watermark,
# and the legibility check automatic instead of per-script discipline.

# --- data provenance --------------------------------------------------------
# Mock data exists so figures can be built before the instrument time is
# booked (see data/mock_data/). The danger is equally obvious, so provenance
# is tracked at the read and enforced at the write: load_data() records where
# each file came from, save_float() watermarks anything downstream of
# data/mock_data/, and create_floats.R refuses to build the co-author document
# from mock input at all.

.float_env <- new.env(parent = emptyenv())
.float_env$inputs <- list()

float_reset <- function() {
  .float_env$inputs <- list()
  invisible(NULL)
}

float_inputs <- function() .float_env$inputs

float_used_mock <- function() {
  any(vapply(.float_env$inputs, function(i) isTRUE(i$mock), logical(1)))
}

#' Read a data file, recording whether it was real or mock.
#'
#' Accepts a project-relative path ("data/raw/x.csv") or an absolute one.
#' Dispatches on extension: .csv .tsv .txt .rds .xlsx.
load_data <- function(path, ...) {
  full <- if (file.exists(path)) path else here::here(path)
  if (!file.exists(full)) {
    # A script still pointing into a folder the project removed on purpose
    # (scaffold.py drop) is a different fix from a file not delivered yet,
    # so it gets a different message: point the script somewhere else.
    gone <- !dir.exists(dirname(full))
    stop("load_data(): no such file: ", path,
         if (gone) paste0("\n  ", dirname(path), "/ does not exist in this ",
                          "project - point this script at where the data ",
                          "actually lives.") else "",
         "\n  Column names and files are contracted in data/data_contract.md.",
         call. = FALSE)
  }
  norm <- normalizePath(full, winslash = "/", mustWork = FALSE)
  is_mock <- grepl("/mock_data/", norm, fixed = TRUE)

  ext <- tolower(tools::file_ext(norm))
  d <- switch(
    ext,
    csv  = readr::read_csv(full, show_col_types = FALSE, ...),
    tsv  = readr::read_tsv(full, show_col_types = FALSE, ...),
    txt  = readr::read_tsv(full, show_col_types = FALSE, ...),
    rds  = readRDS(full),
    xlsx = {
      if (!requireNamespace("readxl", quietly = TRUE)) {
        stop("load_data(): reading .xlsx needs the readxl package", call. = FALSE)
      }
      readxl::read_excel(full, ...)
    },
    stop("load_data(): unhandled file type '.", ext, "'", call. = FALSE)
  )

  .float_env$inputs[[length(.float_env$inputs) + 1L]] <-
    list(path = sub(paste0("^", here::here(), "/"), "",
                    norm, ignore.case = TRUE),
         mock = is_mock)
  if (is_mock) {
    message("  [mock] ", basename(norm), " - outputs will be watermarked")
  }
  d
}

# Per-float provenance is written to disk rather than kept in memory because
# render_all.R runs every script in its own environment, and generate_preview.R
# and create_floats.R run afterwards and need to know what fed what.
.provenance_path <- function() here::here("plan", "floats", "float_provenance.json")

record_float <- function(name, kind, mock, files) {
  path <- .provenance_path()
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  reg <- list()
  if (file.exists(path)) {
    reg <- tryCatch(jsonlite::read_json(path), error = function(e) list())
  }
  reg[[name]] <- list(kind = kind, mock = isTRUE(mock), files = as.list(files),
                      inputs = float_inputs(),
                      written = format(Sys.time(), "%Y-%m-%dT%H:%M:%S"))
  jsonlite::write_json(reg, path, auto_unbox = TRUE, pretty = TRUE)
  invisible(path)
}

read_float_provenance <- function() {
  path <- .provenance_path()
  if (!file.exists(path)) return(list())
  tryCatch(jsonlite::read_json(path), error = function(e) list())
}

# --- which float am I? ------------------------------------------------------
# Every script in plan/figures/Fig01/ writes into plan/figures/Fig01/, and is
# recorded under the label "Figure 1". Both come from the folder, so the script
# never names its own number and renumbering is a folder rename.
#
# render_all.R sets these options before sourcing each script. It does NOT rely
# on R telling a script where it lives: `sys.frame(1)$ofile` is set under
# source() but NULL under `Rscript`, so a script that resolved its own path
# would work in a full render and silently write to the working directory when
# run on its own. Measured, on the TOC graphic's render script, and the
# fallback below is the lesson - it resolves --file= first and only then gives
# up to getwd().

.script_dir <- function() {
  a <- commandArgs(trailingOnly = FALSE)
  hit <- grep("^--file=", a)
  if (length(hit)) return(dirname(normalizePath(sub("^--file=", "", a[hit[1]]))))
  of <- tryCatch(sys.frame(1)$ofile, error = function(e) NULL)
  if (!is.null(of)) return(dirname(normalizePath(of)))
  getwd()
}

#' The folder of the float currently being built.
float_dir <- function() {
  d <- getOption("project.float.dir", NULL)
  if (!is.null(d) && nzchar(d)) return(d)
  .script_dir()
}

#' The label the current float is recorded under: "Figure 1", "Table S2".
#'
#' Falls back to the stem when a script is run outside a float folder, so a
#' one-off render still lands in the register under something readable rather
#' than under NA.
float_key <- function(name = NULL) {
  k <- getOption("project.float.key", NULL)
  if (!is.null(k) && nzchar(k)) return(k)
  f <- parse_float_dir(basename(float_dir()))
  if (!is.null(f)) return(f$label)
  if (!is.null(name)) return(name)
  basename(float_dir())
}

#' A path inside the current float's folder.
#'
#' The one place a float's own files are named. Scripts say
#' float_file("panelA_source.png") rather than a path from the project root, so
#' renaming the folder to renumber the float leaves every script untouched.
float_file <- function(...) file.path(float_dir(), ...)

#' Set / clear the current float. Called by render_all.R around each script.
set_float_context <- function(dir = NULL, key = NULL) {
  options(project.float.dir = dir, project.float.key = key)
  invisible(NULL)
}

# --- geometry ---------------------------------------------------------------

#' Resolve "single" / "double" / a number of inches to a width.
float_width <- function(width = "double") {
  if (is.numeric(width)) return(width)
  switch(tolower(as.character(width)),
         single = COL_SINGLE,
         double = COL_DOUBLE,
         full   = COL_DOUBLE,
         stop("float_width(): width must be 'single', 'double', or inches",
              call. = FALSE))
}

#' Panel grid and a proportionate height for an n-panel figure.
#'
#' A 3-panel and a 6-panel figure at the same width should not come out at the
#' same height; this is the arithmetic that stops that being hand-tuned.
fig_layout <- function(n_panels, aspect = 0.75, width = COL_DOUBLE) {
  n_panels <- max(1L, as.integer(n_panels))
  ncol <- if (n_panels <= 3L) n_panels else ceiling(sqrt(n_panels))
  nrow <- ceiling(n_panels / ncol)
  list(ncol = ncol, nrow = nrow, width = width,
       height = (width / ncol) * aspect * nrow)
}

#' Height for a figure of aspect-locked IMAGE panels, `per_row` to a row.
#'
#' fig_layout() answers the same question for charts and cannot answer it
#' here: an image panel locks its own aspect and carries a title row above it,
#' so the height a chart grid would take is short by exactly that row. Getting
#' it wrong is not subtle - the panels shrink to stamps and the titles collide
#' over the top of them.
image_fig_height <- function(n_panels, per_row = 2, aspect = 0.75,
                             width = "double", titled = TRUE) {
  per_row <- max(1L, as.integer(per_row))
  nrow <- ceiling(max(1L, as.integer(n_panels)) / per_row)
  (float_width(width) / per_row) * aspect * nrow * (if (titled) 1.18 else 1)
}

.count_panels <- function(plot) {
  if (inherits(plot, "patchwork")) return(length(.collect_ggplots(plot)))
  1L
}

.collect_ggplots <- function(plot) {
  out <- list()
  if (inherits(plot, "patchwork")) {
    for (sub in plot$patches$plots) out <- c(out, .collect_ggplots(sub))
    # A patchwork object is also the last plot added to it.
    stripped <- plot
    stripped$patches <- NULL
    class(stripped) <- setdiff(class(stripped), "patchwork")
    if (inherits(stripped, "ggplot")) out <- c(out, list(stripped))
  } else if (inherits(plot, "ggplot")) {
    out <- list(plot)
  }
  out
}

# --- the legibility check ---------------------------------------------------
# A check, not a conversion. Nothing is ever written in grayscale: the saved
# .png and .pdf stay full colour. The point is to catch the case where colour
# is the *only* channel carrying the distinction, which fails in print and for
# roughly 8% of male readers. The fix is always a redundant aesthetic - shape,
# linetype, a direct label - never desaturation.

LEGIBILITY_GRAY_MIN    <- 0.15  # min difference in L* (0-1) to survive grayscale
LEGIBILITY_DELTA_E_MIN <- 12    # min CIE76 dE between deuteranope-simulated colours

.discrete_series <- function(built) {
  out <- list()
  scales <- tryCatch(built$plot$scales$scales, error = function(e) list())
  for (sc in scales) {
    if (!any(c("colour", "color", "fill") %in% sc$aesthetics)) next
    if (isTRUE(sc$is_discrete()) == FALSE) next
    lims <- tryCatch(sc$get_limits(), error = function(e) NULL)
    if (is.null(lims) || length(lims) < 2) next
    labs <- tryCatch(as.character(sc$get_labels()), error = function(e) as.character(lims))
    cols <- tryCatch(as.character(sc$map(lims)), error = function(e) NULL)
    if (is.null(cols) || anyNA(cols)) next
    out[[length(out) + 1L]] <- list(labels = labs, colours = cols)
  }
  out
}

.has_redundant_aesthetic <- function(built) {
  for (layer_data in built$data) {
    for (a in c("shape", "linetype")) {
      if (a %in% names(layer_data)) {
        vals <- unique(stats::na.omit(layer_data[[a]]))
        if (length(vals) > 1) return(TRUE)
      }
    }
  }
  for (lyr in built$plot$layers) {
    if (inherits(lyr$geom, "GeomText") || inherits(lyr$geom, "GeomLabel")) return(TRUE)
  }
  FALSE
}

check_legibility <- function(plot, name = "figure") {
  if (!requireNamespace("colorspace", quietly = TRUE) ||
      !requireNamespace("farver", quietly = TRUE)) {
    return(invisible(character(0)))
  }
  problems <- character(0)
  for (p in .collect_ggplots(plot)) {
    built <- tryCatch(ggplot2::ggplot_build(p), error = function(e) NULL)
    if (is.null(built)) next
    if (.has_redundant_aesthetic(built)) next
    for (series in .discrete_series(built)) {
      cols <- series$colours
      labs <- series$labels
      lab_real <- farver::convert_colour(farver::decode_colour(cols), "rgb", "lab")
      lab_deut <- farver::convert_colour(
        farver::decode_colour(colorspace::deutan(cols)), "rgb", "lab")
      for (i in seq_along(cols)) {
        for (j in seq_len(i - 1L)) {
          gray_d <- abs(lab_real[i, "l"] - lab_real[j, "l"]) / 100
          de     <- sqrt(sum((lab_deut[i, ] - lab_deut[j, ])^2))
          if (gray_d < LEGIBILITY_GRAY_MIN && de < LEGIBILITY_DELTA_E_MIN) {
            problems <- c(problems, sprintf(
              "%s: series \"%s\" and \"%s\" are indistinguishable in grayscale\n          and under deuteranopia (dL* %.2f, dE %.1f) - add a shape or linetype aesthetic",
              name, labs[j], labs[i], gray_d, de))
          }
        }
      }
    }
  }
  for (msg in problems) warning(msg, call. = FALSE)
  invisible(problems)
}

# --- saving -----------------------------------------------------------------

.draw_watermark <- function(width_in) {
  grid::upViewport(0)
  grid::grid.text("MOCK DATA", rot = 30,
                  gp = grid::gpar(col = "#D55E00", alpha = 0.28,
                                  fontface = "bold", cex = width_in * 0.9))
}

#' Write a float as .png (raster, journal dpi) and .pdf (vector) in one call.
#'
#' Writes into the float's own folder - plan/figures/Fig01/figure.png - so the
#' script does not name its own number and renumbering is a folder rename.
#'
#' @param plot   a ggplot or patchwork object
#' @param name   file stem inside the folder; defaults to "figure"
#' @param width  "single", "double", or inches
#' @param height inches; derived from fig_layout() when NULL
save_float <- function(plot, name = NULL, width = "double", height = NULL,
                       aspect = 0.75, dpi = DPI) {
  w <- float_width(width)
  if (is.null(height)) {
    height <- fig_layout(.count_panels(plot), aspect = aspect, width = w)$height
  }
  dir <- float_dir()
  if (is.null(name)) name <- "figure"
  dir.create(dir, recursive = TRUE, showWarnings = FALSE)
  png_path <- file.path(dir, paste0(name, ".png"))
  pdf_path <- file.path(dir, paste0(name, ".pdf"))
  mock <- float_used_mock()

  render <- function(open_device) {
    open_device()
    on.exit(grDevices::dev.off(), add = TRUE)
    print(plot)
    if (mock) .draw_watermark(w)
  }

  render(function() ragg::agg_png(png_path, width = w, height = height,
                                  units = "in", res = dpi, background = "white"))
  render(function() grDevices::cairo_pdf(pdf_path, width = w, height = height,
                                         onefile = FALSE))

  check_legibility(plot, float_key(name))
  record_float(float_key(name), "figure", mock, c(png_path, pdf_path))
  if (mock) message("  [mock] ", float_key(name), " watermarked")
  invisible(c(png = png_path, pdf = pdf_path))
}

#' Uniform flextable styling for the Word floats.
style_float_ft <- function(ft) {
  ft |>
    flextable::theme_booktabs() |>
    flextable::fontsize(size = BASE_SIZE, part = "all") |>
    flextable::font(fontname = "Arial", part = "all") |>
    flextable::bold(part = "header") |>
    flextable::align(align = "left", part = "all") |>
    flextable::padding(padding.top = 2, padding.bottom = 2, part = "all") |>
    flextable::autofit()
}

#' Write a table as .html (preview), .rds (native Word table), and optionally .tex.
#'
#' The .rds is the one that matters: create_floats.R reads it and emits a real,
#' editable Word table, so nothing is converted downstream and no formatting
#' can break on the way to a co-author.
save_table <- function(x, name = NULL, tex = FALSE) {
  ft <- if (inherits(x, "flextable")) x else style_float_ft(flextable::flextable(as.data.frame(x)))
  if (!inherits(x, "flextable")) ft <- style_float_ft(ft)
  dir <- float_dir()
  if (is.null(name)) name <- "table"
  dir.create(dir, recursive = TRUE, showWarnings = FALSE)
  html_path <- file.path(dir, paste0(name, ".html"))
  rds_path  <- file.path(dir, paste0(name, ".rds"))
  files <- c(html_path, rds_path)

  writeLines(flextable::htmltools_value(ft) |> as.character(), html_path, useBytes = TRUE)
  saveRDS(ft, rds_path)
  if (isTRUE(tex)) {
    tex_path <- file.path(dir, paste0(name, ".tex"))
    writeLines(flextable::as_latex(ft), tex_path, useBytes = TRUE)
    files <- c(files, tex_path)
  }
  record_float(float_key(name), "table", float_used_mock(), files)
  invisible(files)
}

# --- panel composition ------------------------------------------------------
# Ported wholesale from the pipeline these were solved on. patchwork makes the
# simple cases easy and these three cases hard; do not re-derive them.

#' The grey box an image panel shows before its image exists.
#'
#' Light grey rather than a hard placeholder colour: it has to read as "not
#' collected yet" in a preview somebody scrolls past, without competing with
#' the panels beside it that hold real data. The description is centred and
#' wrapped; the filename sits under it in smaller type, because the filename
#' is what you need when you finally have the image and are looking for where
#' to put it.
.pending_panel <- function(filename, describe = NULL) {
  parts <- list(grid::rectGrob(gp = grid::gpar(fill = "grey95", col = "grey75",
                                               lty = "dashed", lwd = 0.8)))
  if (is.null(describe) || !nzchar(describe)) {
    parts <- c(parts, list(
      grid::textGrob(paste0("pending: ", filename),
                     gp = grid::gpar(col = "grey45", cex = 0.8))))
  } else {
    parts <- c(parts, list(
      grid::textGrob(paste(strwrap(describe, width = 26), collapse = "
"),
                     y = 0.58, gp = grid::gpar(col = "grey35", cex = 0.72,
                                               lineheight = 1.2)),
      grid::textGrob(filename, y = 0.12,
                     gp = grid::gpar(col = "grey60", cex = 0.55))))
  }
  do.call(grid::grobTree, parts)
}

#' A PNG (micrograph, spectrum, gel scan) as a panel with its aspect preserved.
#'
#' Renders a light grey placeholder when the file does not exist yet, so a
#' render never breaks on art that is still being acquired.
#'
#' `describe` is what the placeholder SAYS. A panel reserved for a micrograph
#' that has not been collected is not an error state, it is the normal state of
#' an imaging project on day one - and a grey box reading "pending:
#' panelA.png" tells whoever opens the preview nothing about what belongs
#' there. One sentence does:
#'
#'     image_panel(float_file("panelA_micrograph.png"), "Overview", "A",
#'                 describe = "Motion-corrected micrograph, 1.2 um defocus")
#'
#' `aspect` locks the placeholder's shape to the one the real image will have,
#' so the composed figure does not reflow when the image lands.
image_panel <- function(path, title = NULL, tag = NULL, describe = NULL,
                        aspect = 0.75) {
  full <- if (file.exists(path)) path else here::here(path)
  if (file.exists(full)) {
    img <- png::readPNG(full)
    aspect <- dim(img)[1] / dim(img)[2]
    g <- grid::rasterGrob(img, interpolate = TRUE)
  } else {
    g <- .pending_panel(basename(path), describe)
  }
  p <- ggplot2::ggplot(data.frame(x = c(0, 1), y = c(0, 1)), ggplot2::aes(x, y)) +
    ggplot2::geom_blank() +
    ggplot2::annotation_custom(g, -Inf, Inf, -Inf, Inf) +
    ggplot2::coord_fixed(ratio = aspect, xlim = c(0, 1), ylim = c(0, 1), expand = FALSE) +
    ggplot2::theme_void()
  if (!is.null(title) || !is.null(tag)) p <- panel_title(p, title, tag)
  p
}

#' The A/B/C tag above a short panel title above the content - identical in
#' every figure, which is the only reason a multi-panel figure reads as one.
panel_title <- function(plot, title = NULL, tag = NULL) {
  plot +
    ggplot2::labs(title = title, tag = tag) +
    ggplot2::theme(
      plot.title = ggplot2::element_text(
        size = PANEL_TITLE_SIZE, hjust = 0, face = "plain",
        margin = ggplot2::margin(b = PANEL_TITLE_GAP_PT, l = PANEL_INDENT_PT)),
      plot.tag = ggplot2::element_text(size = PANEL_TITLE_SIZE + 1, face = "bold"),
      plot.tag.position = c(0, 1))
}

#' The split version of panel_title(), for figures that must drive their own
#' row heights: an aspect-locked image beside a chart will not otherwise line
#' up, because the image ignores the height patchwork hands it.
#'
#' Use as:  (panel_header("Overview", "A") / panel_body(p)) +
#'            patchwork::plot_layout(heights = c(0.12, 1))
panel_header <- function(title, tag = NULL) {
  label <- if (is.null(tag)) title else paste0(tag, "  ", title)
  ggplot2::ggplot() +
    ggplot2::annotate("text", x = 0, y = 0, label = label, hjust = 0, vjust = 0.5,
                      size = PANEL_TITLE_SIZE / .pt,
                      fontface = if (is.null(tag)) "plain" else "bold") +
    ggplot2::scale_x_continuous(limits = c(0, 1), expand = c(0, 0)) +
    ggplot2::theme_void()
}

panel_body <- function(plot) {
  plot + ggplot2::labs(title = NULL, tag = NULL) +
    ggplot2::theme(plot.margin = ggplot2::margin(0, 2, 2, PANEL_INDENT_PT))
}

#' A PowerPoint-authored panel, composed the way an aspect-locked image needs.
#'
#' The one-liner for panel art made by tools/graphic_figure.py:
#'
#'     p_a <- graphic_panel("panelA_source.png", "Reaction scheme", "A")
#'
#' It is panel_header()/panel_body() rather than panel_title() for the reason
#' those two exist: image_panel() locks the aspect ratio, so the image ignores
#' the height patchwork hands it, and a title drawn INSIDE that panel floats
#' over whatever sits beside it. Measured - two graphic panels side by side
#' rendered with their titles overlapping into "Reaction sccAhpepmaeratus".
#' Giving the title its own thin row cannot collide.
#'
#' The .png does not have to exist yet. image_panel() draws a dashed
#' placeholder for art still being made, which is what lets a figure be
#' composed before any of it is drawn.
graphic_panel <- function(png, title = NULL, tag = NULL, header_height = 0.12,
                          describe = NULL, aspect = 0.75) {
  body <- panel_body(image_panel(float_file(png), describe = describe,
                                 aspect = aspect))
  if (is.null(title) && is.null(tag)) return(body)
  (panel_header(title, tag) / body) +
    patchwork::plot_layout(heights = c(header_height, 1))
}

# --- annotating a statistic on a figure --------------------------------------
# A figure plots from data/raw/ directly. It does NOT read the analysis.
#
# There used to be a load_stats()/stat_p() pair here, reading p-values out of
# an analysis.rds so a script could annotate an asterisk without recomputing
# the test. The file and both functions are gone: figures come straight from
# the data, so the lookup had no caller.
#
# THE RULE THEY ENFORCED IS STILL THE RULE, and it is now yours to keep:
# **never recompute a test inside a figure script.** A p-value printed on a
# panel is typed from data/analysis/analysis.md - the same place the results
# paragraph takes it from, which is what keeps the asterisk and the sentence
# in agreement. An inline t.test() in figure.R drifts from the results section
# the first time a row is excluded or a test choice changes, and both still
# look right.
#
#   p + annot_label(paste0("p = ", 0.002, p_stars(0.002)))

#' Conventional significance stars. ns below the threshold, never blank.
p_stars <- function(p) {
  ifelse(is.na(p), "",
         ifelse(p < 0.001, "***",
                ifelse(p < 0.01, "**",
                       ifelse(p < 0.05, "*", "ns"))))
}

# --- in-panel annotation ----------------------------------------------------
# The stat label in a panel corner and the legend parked inside a panel are the
# two things every multi-panel figure needs and every figure styles slightly
# differently, which is what makes six good panels look like six figures. Both
# read ANNOT_* from theme_journal.R, so a journal change moves them together.

#' A stat label in a panel corner, in the shared "fogged" style.
#'
#' Defaults to the top-right corner, which is where a fit statistic or an n
#' belongs unless the data is there. `x`/`y` take Inf/-Inf for the corners, or
#' data coordinates when a corner will not do.
#'
#'   p + annot_label(sprintf("n = %d\nr = %.2f", n, r))
#'   p + annot_label("p < 0.001", x = -Inf, hjust = 0)   # top-left
annot_label <- function(label, x = Inf, y = Inf, hjust = 1, vjust = 1, ...) {
  ggplot2::annotate("label", x = x, y = y, label = label,
                    hjust = hjust, vjust = vjust, size = ANNOT_SIZE,
                    colour = ANNOT_COLOUR, fill = ANNOT_FILL,
                    linewidth = 0, ...)
}

#' Park the legend inside the panel, in the same style as annot_label().
#'
#' A legend below a panel steals height from every panel in the row. Inside the
#' plotting region it costs nothing, which is what lets a six-panel figure keep
#' its panels at a readable size.
#'
#'   p + theme_inside_legend()                    # bottom-right
#'   p + theme_inside_legend(c(0.03, 0.97), c(0, 1))   # top-left
theme_inside_legend <- function(position = c(0.97, 0.03),
                                justification = c(1, 0)) {
  ggplot2::theme(
    legend.position            = "inside",
    legend.position.inside     = position,
    legend.justification.inside = justification,
    legend.background          = ggplot2::element_rect(fill = ANNOT_FILL, colour = NA),
    legend.text                = ggplot2::element_text(colour = ANNOT_COLOUR),
    legend.key                 = ggplot2::element_blank(),
    legend.key.height          = grid::unit(10, "pt"))
}
