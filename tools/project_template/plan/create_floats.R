# plan/create_floats.R -------------------------------------------------------
# Builds plan/floats/figures_and_tables.docx - the document that goes to
# co-authors.
#
# Every table is a NATIVE, EDITABLE Word table, built from the flextable each
# table script saved as .rds. That is the point of the whole triple-output
# convention: nothing is converted on the way out, so nothing can lose its
# formatting in transit, and a co-author can retype a cell without asking.
# Figures are embedded as images at journal width. Order comes from
# captions.md, one float per page.
#
# HARD STOP on mock data. The preview only warns, because the preview is a
# working view; this document leaves the project folder, and a watermark that
# someone crops out of a screenshot is not a control. Override deliberately:
#     FORCE_MOCK=1 Rscript plan/render_all.R

local({

  suppressPackageStartupMessages({
    library(officer)
    library(flextable)
  })

  entries <- read_captions()
  prov <- read_float_provenance()
  dirs <- list_float_dirs()

  mock_floats <- names(prov)[vapply(prov, function(p) isTRUE(p$mock), logical(1))]
  forced <- nzchar(Sys.getenv("FORCE_MOCK"))

  if (length(mock_floats) && !forced) {
    cat("floats.docx    REFUSED - built from mock data:\n")
    for (m in sort(mock_floats)) cat("                 ", m, "\n")
    cat("               This document is the co-author hand-off. Point the\n")
    cat("               scripts at data/raw/, or set FORCE_MOCK=1 to override.\n")
    options(project.floats.written = NA_character_)
    return(invisible(NULL))
  }

  doc <- read_docx()
  doc <- body_add_par(doc, "Figures and Tables", style = "heading 1")
  subtitle <- paste0(
    if (nzchar(PROJECT$title %||% "")) PROJECT$title else PROJECT$short_name %||% "",
    "  |  revision ", PROJECT$revision %||% 1,
    "  |  ", format(Sys.Date(), "%Y-%m-%d"))
  doc <- body_add_par(doc, subtitle, style = "Normal")
  if (forced && length(mock_floats)) {
    doc <- body_add_par(doc, "WARNING: contains figures built from MOCK DATA.",
                        style = "Normal")
  }

  # Word's usable text width on US Letter with 1 in margins. A figure wider
  # than this gets silently scaled by Word, which is how a 300 dpi figure
  # arrives looking soft.
  MAX_W <- 6.5
  n <- 0L

  # Resolve each caption block to its float FOLDER, and read the rendered
  # output out of that folder. Same rule the preview uses: matched by number,
  # so a folder renamed to carry a slug still answers to its caption block.
  in_float <- function(fl, stem, ext) {
    if (is.null(fl)) return("")
    p <- file.path(fl$path, paste0(stem, ".", ext))
    if (file.exists(p)) return(p)
    cand <- list.files(fl$path, paste0("\\.", ext, "$"), full.names = TRUE)
    cand <- cand[!grepl("_source\\.", basename(cand))]
    if (length(cand)) return(sort(cand)[1])
    ""
  }

  for (e in entries) {
    fl <- float_dir_for(e, dirs)

    doc <- body_add_break(doc)
    n <- n + 1L

    caption <- paste0(e$label, ". ",
                      if (nzchar(e$subtitle)) paste0(e$subtitle, " ") else "",
                      e$caption)

    if (identical(e$type, "Table")) {
      doc <- body_add_par(doc, caption, style = "Normal")
      rds <- in_float(fl, "table", "rds")
      if (nzchar(rds)) {
        doc <- body_add_flextable(doc, readRDS(rds))
      } else {
        doc <- body_add_par(doc, paste0("[", e$label, " has no rendered .rds - ",
                                        "run plan/render_all.R]"), style = "Normal")
      }
    } else {
      png_path <- in_float(fl, "figure", "png")
      if (nzchar(png_path)) {
        dims <- dim(png::readPNG(png_path))
        # The .png was written at the journal dpi, so pixels / dpi is its true
        # printed size; only shrink it if it overruns the text column.
        w <- min(dims[2] / DPI, MAX_W)
        h <- w * (dims[1] / dims[2])
        doc <- body_add_img(doc, png_path, width = w, height = h)
      } else {
        doc <- body_add_par(doc, paste0("[", e$label, " not rendered yet]"),
                            style = "Normal")
      }
      doc <- body_add_par(doc, caption, style = "Normal")
    }
  }

  if (!n) {
    doc <- body_add_par(doc, "No floats yet.", style = "Normal")
  }

  out_dir <- here::here("plan", "floats")
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  out <- file.path(out_dir, "figures_and_tables.docx")
  print(doc, target = out)
  options(project.floats.written = out)
  cat(sprintf("floats.docx    %d float%s%s\n", n, if (n == 1) "" else "s",
              if (forced && length(mock_floats)) "  [FORCED, MOCK DATA]" else ""))
})
