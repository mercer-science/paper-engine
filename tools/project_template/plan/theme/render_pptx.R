# plan/theme/render_pptx.R ---------------------------------------------------
# Turn a PowerPoint slide into a .png at an exact pixel size.
#
# This is the graphic half of the float pipeline. A reaction scheme, an
# apparatus diagram, a mechanism - the things nobody draws in ggplot - are
# authored as ONE PowerPoint slide per panel and rendered here. ACS's own
# guidance for the graphical abstract says to think of it as a single slide,
# and the same reasoning holds for a figure panel: the editing surface people
# already know beats any drawing API this toolkit could offer.
#
# The .pptx is YOURS. Nothing here ever writes one - tools/graphic_figure.py
# creates it once, and after that it is only ever read.
#
# Three backends, tried in order, and the caller is told which one ran:
#
#   1. PowerPoint via COM (Windows). Slide.Export takes explicit pixel width
#      and height, so the output lands on the requested size exactly rather
#      than approximately. Driven through PowerShell because R has no COM
#      bridge.
#   2. LibreOffice headless.
#   3. Nothing - and then it STOPS AND SAYS SO rather than producing a
#      wrong-sized image, because the wrong-sized image is the one that
#      reaches the editor.
#
# NOTE: tools/toc_graphic.py carries a second copy of this backend chain, on
# purpose. The TOC graphic lives in a journal folder that may belong to a
# manuscript-only project with no plan/ tree at all, so it cannot source this
# file. tests/graphic_figure.py asserts the two try the same backends in the
# same order; if you change one, change both.

# --- staleness --------------------------------------------------------------
# One record per float folder, beside the sources it describes.
#
# Staleness is answered by the CONTENT of the .pptx, never by its mtime:
# OneDrive rewrites modification times on sync, so an mtime comparison
# re-renders files nobody touched and - worse - can call a file current after a
# sync has replaced it. tools::sha256sum is base R, and it is byte-for-byte the
# same digest tools/graphic_figure.py writes, so either side can answer the
# question and the two cannot disagree.

PPTX_STATE_FILE <- ".render_state.json"

.pptx_state_path <- function(pptx) file.path(dirname(pptx), PPTX_STATE_FILE)

.pptx_read_state <- function(pptx) {
  p <- .pptx_state_path(pptx)
  if (!file.exists(p)) return(list())
  tryCatch(jsonlite::read_json(p), error = function(e) list())
}

#' Has the .pptx changed since the .png beside it was made?
pptx_stale <- function(pptx, png_out = NULL) {
  if (is.null(png_out)) png_out <- sub("[.]pptx$", ".png", pptx)
  if (!file.exists(png_out)) return(TRUE)
  st <- .pptx_read_state(pptx)[[basename(pptx)]]
  if (is.null(st$sha256)) return(TRUE)
  !identical(as.character(st$sha256), unname(tools::sha256sum(pptx)))
}

pptx_record <- function(pptx, png_out, backend, px) {
  p <- .pptx_state_path(pptx)
  st <- .pptx_read_state(pptx)
  st[[basename(pptx)]] <- list(
    sha256 = unname(tools::sha256sum(pptx)),
    png = basename(png_out), backend = backend,
    px = as.list(as.integer(px)),
    rendered = format(Sys.time(), "%Y-%m-%dT%H:%M:%S"))
  jsonlite::write_json(st, p, auto_unbox = TRUE, pretty = TRUE)
  invisible(p)
}

# --- reading the slide's own size -------------------------------------------

#' The slide size recorded in the .pptx, in inches.
#'
#' Read out of the file rather than assumed, so a slide somebody has resized in
#' PowerPoint still renders at its true aspect ratio instead of being squashed
#' into whatever shape the generator happened to choose.
pptx_slide_size <- function(pptx) {
  EMU <- 914400
  xml <- ""
  con <- tryCatch(unz(pptx, "ppt/presentation.xml"), error = function(e) NULL)
  if (!is.null(con)) {
    xml <- tryCatch(paste(readLines(con, warn = FALSE), collapse = ""),
                    error = function(e) "",
                    finally = tryCatch(close(con), error = function(e) NULL))
  }
  grab <- function(attr) {
    m <- regmatches(xml, regexpr(paste0(attr, '="[0-9]+"'), xml))
    if (!length(m)) return(NA_real_)
    as.numeric(regmatches(m, regexpr("[0-9]+", m)))
  }
  w <- grab("cx")
  h <- grab("cy")
  if (is.na(w) || is.na(h) || w <= 0 || h <= 0) {
    return(list(width_in = 3.3, height_in = 2.5, read = FALSE))
  }
  list(width_in = w / EMU, height_in = h / EMU, read = TRUE)
}

# --- the render -------------------------------------------------------------

#' Render one .pptx slide to .png at an exact pixel size.
#'
#' @return the backend that ran, or NA when the .png was already current.
render_pptx <- function(pptx, png_out = NULL, px_w = NULL, px_h = NULL,
                        force = FALSE, quiet = FALSE) {
  if (!file.exists(pptx)) {
    stop("render_pptx(): no such file: ", pptx, call. = FALSE)
  }
  if (is.null(png_out)) png_out <- sub("[.]pptx$", ".png", pptx)
  if (is.null(px_w) || is.null(px_h)) {
    d <- pptx_slide_size(pptx)
    dpi <- if (exists("DPI")) DPI else 300
    if (is.null(px_w)) px_w <- as.integer(round(d$width_in * dpi))
    if (is.null(px_h)) px_h <- as.integer(round(d$height_in * dpi))
  }

  if (!force && file.exists(png_out) && !pptx_stale(pptx, png_out)) {
    return(NA_character_)
  }

  backend <- NA_character_

  # --- 1. PowerPoint via COM ------------------------------------------------
  pp_exe <- Sys.glob(c("C:/Program Files/Microsoft Office/root/Office*/POWERPNT.EXE",
                       "C:/Program Files (x86)/Microsoft Office/root/Office*/POWERPNT.EXE",
                       "C:/Program Files/Microsoft Office/Office*/POWERPNT.EXE"))
  ps <- Sys.which("powershell")
  if (length(pp_exe) > 0 && nzchar(ps)) {
    script <- sprintf(paste(
      '$ErrorActionPreference="Stop";',
      '$pp = New-Object -ComObject PowerPoint.Application;',
      '$pres = $pp.Presentations.Open("%s", $true, $false, $false);',
      '$pres.Slides.Item(1).Export("%s", "PNG", %d, %d);',
      '$pres.Close(); $pp.Quit();'),
      normalizePath(pptx, winslash = "\\", mustWork = TRUE),
      normalizePath(png_out, winslash = "\\", mustWork = FALSE), px_w, px_h)
    suppressWarnings(system2(ps, c("-NoProfile", "-NonInteractive", "-Command",
                                   shQuote(script)),
                             stdout = TRUE, stderr = TRUE))
    if (file.exists(png_out)) backend <- "powerpoint-com"
  }

  # --- 2. LibreOffice headless ---------------------------------------------
  if (is.na(backend)) {
    soffice <- Sys.which("soffice")
    if (!nzchar(soffice)) {
      cand <- Sys.glob(c("C:/Program Files/LibreOffice/program/soffice.exe",
                         "C:/Program Files (x86)/LibreOffice/program/soffice.exe",
                         "/usr/bin/soffice", "/usr/local/bin/soffice",
                         "/Applications/LibreOffice.app/Contents/MacOS/soffice"))
      if (length(cand) > 0) soffice <- cand[1]
    }
    if (nzchar(soffice)) {
      filt <- sprintf(paste0('png:impress_png_Export:{"PixelWidth":',
                             '{"type":"long","value":%d},"PixelHeight":',
                             '{"type":"long","value":%d}}'), px_w, px_h)
      suppressWarnings(system2(soffice, c("--headless", "--convert-to",
                                          shQuote(filt), "--outdir",
                                          shQuote(dirname(png_out)),
                                          shQuote(pptx)),
                               stdout = TRUE, stderr = TRUE))
      if (file.exists(png_out)) backend <- "libreoffice"
    }
  }

  # --- 3. no backend --------------------------------------------------------
  if (is.na(backend)) {
    stop(paste0(
      "could not render ", basename(pptx), " automatically - neither ",
      "PowerPoint nor LibreOffice was found.\n",
      "  Do this instead, it takes about thirty seconds:\n",
      "    1. open ", pptx, "\n",
      "    2. File > Save As, choose PNG\n",
      "    3. save it as ", png_out, "\n",
      "  Then re-run plan/render_all.R - everything downstream is identical.\n",
      "  The slide is already the right size, so PowerPoint's own export\n",
      "  gives the right number of pixels."), call. = FALSE)
  }

  pptx_record(pptx, png_out, backend, c(px_w, px_h))
  if (!quiet) {
    message("rendered ", basename(png_out), " via ", backend,
            "  (", px_w, " x ", px_h, " px)")
  }
  backend
}

#' Render every *_source.pptx in a float folder.
#'
#' This is what a float folder's panels.R calls. Unchanged sources are skipped,
#' so a full render does not pay for a PowerPoint launch per panel per run.
render_panel_sources <- function(dir = float_dir(), force = FALSE) {
  srcs <- sort(list.files(dir, pattern = "_source[.]pptx$", full.names = TRUE))
  # PowerPoint writes ~$name.pptx beside a file it has open. Rendering that
  # lock stub produces either an error or a blank slide, and the blank slide
  # is the one that would quietly reach the figure.
  srcs <- srcs[!startsWith(basename(srcs), "~$")]
  out <- character(0)
  for (s in srcs) {
    png_out <- sub("[.]pptx$", ".png", s)
    render_pptx(s, png_out)
    out <- c(out, png_out)
  }
  invisible(out)
}
