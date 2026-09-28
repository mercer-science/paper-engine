# plan/generate_preview.R ----------------------------------------------------
# Builds plan/preview.html: one self-contained file with every figure and table
# in manuscript order, each with its caption. Figures are base64-embedded, so
# the file can be emailed or dropped in Teams as-is and still render.
#
# Order and captions come from read_captions() - never from a second parser.
# A float on disk with no block in captions.md still renders, loudly flagged;
# a block naming a file that does not exist yet renders as a placeholder. The
# preview is a work-in-progress view, so neither case is an error.
#
# Run via plan/render_all.R, or standalone with:
#     Rscript -e 'source(here::here("plan","setup.R")); source(here::here("plan","generate_preview.R"))'

local({

  esc <- function(x) {
    x <- gsub("&", "&amp;", x, fixed = TRUE)
    x <- gsub("<", "&lt;", x, fixed = TRUE)
    gsub(">", "&gt;", x, fixed = TRUE)
  }

  # Minimal markdown for caption bodies: **bold** and *italic* only. Captions
  # are prose, not documents; anything more wants a real renderer.
  md <- function(x) {
    x <- esc(x)
    x <- gsub("\\*\\*(.+?)\\*\\*", "<strong>\\1</strong>", x)
    gsub("(^|[^*])\\*([^*]+?)\\*", "\\1<em>\\2</em>", x)
  }

  ONEDRIVE_CONFLICT_RE <- "-DESKTOP-[A-Z0-9]+(-[0-9]+)?"
  clean <- function(f) f[!grepl(ONEDRIVE_CONFLICT_RE, basename(f))]

  entries <- read_captions()
  prov <- read_float_provenance()
  dirs <- list_float_dirs()

  # Resolve to what the preview can actually display: a figure's .png or a
  # table's .html, inside the float's own folder. A folder may hold several
  # .png files - panel art rendered from PowerPoint sits beside the figure -
  # so figure.png is preferred and any other .png is the fallback for a script
  # that chose its own stem. It can never hand readLines() a binary .rds.
  find_file <- function(fl) {
    if (is.null(fl)) return("")
    ext <- if (identical(fl$type, "Table")) "html" else "png"
    preferred <- file.path(fl$path,
                           paste0(if (ext == "html") "table" else "figure", ".", ext))
    if (file.exists(preferred)) return(preferred)
    cand <- clean(list.files(fl$path, paste0("\\.", ext, "$"), full.names = TRUE))
    # Panel sources are inputs to the figure, not the figure.
    cand <- cand[!grepl("_source\\.", basename(cand))]
    if (length(cand)) return(sort(cand)[1])
    ""
  }

  # A float is identified by its FOLDER, and a caption block resolves to one by
  # number - so `## Figure 1 - Fig01` still finds the folder after it is renamed
  # to Fig01_yield_by_catalyst. Matching the folder NAME instead would make
  # adding a slug an edit in two places, which is the thing the folder layout
  # exists to remove.
  claimed <- character(0)
  items <- list()
  for (e in entries) {
    fl <- float_dir_for(e, dirs)
    if (!is.null(fl)) claimed <- c(claimed, fl$dir)
    items[[length(items) + 1L]] <- list(entry = e, orphan = FALSE, float = fl)
  }

  # A RENDERED folder that no caption block claims. Listed rather than dropped:
  # a figure that exists and has no caption is a caption someone has not
  # written yet, and the preview is where they should find that out.
  #
  # An UNRENDERED unclaimed folder is skipped, because that is just an empty
  # slot - `scaffold` pre-creates four of them - and a preview that opens with
  # four alarming empty sections on day one is a preview people stop reading.
  n_slots <- 0L
  for (fl in dirs) {
    if (fl$dir %in% claimed) next
    if (!nzchar(find_file(fl))) {
      n_slots <- n_slots + 1L
      next
    }
    items[[length(items) + 1L]] <- list(
      orphan = TRUE, float = fl,
      entry = list(type = fl$type, number = fl$number,
                   supplementary = fl$supplementary,
                   file = fl$dir, label = "(uncaptioned)",
                   subtitle = "", caption = ""))
  }

  any_mock <- any(vapply(prov, function(p) isTRUE(p$mock), logical(1)))

  body <- character(0)
  toc  <- character(0)

  for (i in seq_along(items)) {
    it <- items[[i]]
    e <- it$entry
    anchor <- sprintf("float-%d", i)
    path <- find_file(it$float)
    # The register is keyed by label ("Figure 1"), because that is what the
    # folder says and what save_float() recorded.
    is_mock <- isTRUE(prov[[e$label]]$mock)
    where <- if (is.null(it$float)) e$file else it$float$dir

    heading <- if (it$orphan) {
      sprintf('<h2 id="%s">%s <span class="warn">(no caption in captions.md)</span></h2>',
              anchor, esc(where))
    } else {
      sprintf('<h2 id="%s">%s%s</h2>', anchor, esc(e$label),
              if (is_mock) ' <span class="mock-tag">MOCK DATA</span>' else "")
    }
    toc <- c(toc, sprintf('<li><a href="#%s">%s</a> <span class="dim">%s</span></li>',
                          anchor, esc(if (it$orphan) where else e$label),
                          esc(where)))

    cap <- if (nzchar(e$subtitle) || nzchar(e$caption)) {
      sprintf('<p class="caption"><strong>%s</strong> %s</p>',
              md(e$subtitle), md(e$caption))
    } else if (!it$orphan) {
      '<p class="caption warn">(no caption text under this heading)</p>'
    } else ""

    content <- if (!nzchar(path)) {
      sprintf('<p class="missing">%s has no rendered output in plan/%s/%s/ yet.</p>',
              esc(e$label),
              esc(if (identical(e$type, "Table")) "tables" else "figures"),
              esc(where))
    } else if (grepl("\\.png$", path)) {
      sprintf('<img src="data:image/png;base64,%s" alt="%s">',
              base64enc::base64encode(path), esc(e$label))
    } else {
      paste(readLines(path, warn = FALSE, encoding = "UTF-8"), collapse = "\n")
    }

    # Journal convention, and what create_floats.R does: captions below
    # figures, above tables.
    body <- c(body, "<section>", heading,
              if (identical(e$type, "Table")) c(cap, content) else c(content, cap),
              "</section>")
  }

  if (!length(items)) {
    body <- c('<section><p class="missing">No floats yet. Put a script in ',
              '<code>plan/figures/Fig01/figure.R</code>, add a ',
              '<code>## Figure 1 &mdash; Fig01</code> block to ',
              '<code>plan/captions.md</code>, and re-run ',
              '<code>Rscript plan/render_all.R</code>.</p></section>')
  }

  title <- if (nzchar(PROJECT$title %||% "")) PROJECT$title else
    (PROJECT$short_name %||% "Project")

  html <- c(
    '<!doctype html><html><head><meta charset="utf-8">',
    sprintf("<title>%s - floats</title>", esc(title)),
    "<style>",
    "body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:900px;",
    "  margin:0 auto;padding:2rem 1.5rem;color:#1a1a1a;line-height:1.5}",
    "h1{font-size:1.5rem;margin-bottom:.25rem}",
    "h2{font-size:1.05rem;margin:0 0 .5rem;border-bottom:1px solid #e5e5e5;padding-bottom:.3rem}",
    ".sub{color:#666;font-size:.85rem;margin-top:0}",
    "section{margin:2.5rem 0}",
    "img{max-width:100%;height:auto;border:1px solid #e5e5e5}",
    ".caption{font-size:.85rem;color:#333;margin-top:.6rem}",
    ".warn{color:#c0392b;font-weight:600}",
    ".missing{color:#888;font-style:italic}",
    ".dim{color:#999;font-size:.8rem}",
    "ol.toc{font-size:.9rem;padding-left:1.2rem}",
    ".mock-tag{background:#D55E00;color:#fff;font-size:.7rem;padding:.1rem .4rem;",
    "  border-radius:3px;vertical-align:middle}",
    ".mock-banner{background:#fdf0e6;border:1px solid #D55E00;color:#8a3b00;",
    "  padding:.75rem 1rem;border-radius:4px;font-weight:600}",
    "table{border-collapse:collapse;font-size:.85rem}",
    "</style></head><body>",
    sprintf("<h1>%s</h1>", esc(title)),
    sprintf('<p class="sub">%s &middot; revision %s &middot; %s geometry &middot; generated %s</p>',
            esc(PROJECT$target_journal %||% "no target journal"),
            esc(as.character(PROJECT$revision %||% 1)),
            esc(JOURNAL$preset),
            format(Sys.time(), "%Y-%m-%d %H:%M")),
    if (any_mock) paste0('<p class="mock-banner">This preview contains figures ',
                         'built from MOCK DATA. Nothing here is a result.</p>') else "",
    "<ol class=\"toc\">", toc, "</ol>",
    body,
    "</body></html>")

  out <- here::here("plan", "preview.html")
  writeLines(html, out, useBytes = TRUE)
  options(project.preview.written = out)
  cat(sprintf("preview.html   %d float%s%s%s\n", length(items),
              if (length(items) == 1) "" else "s",
              if (n_slots) sprintf("  (%d empty slot%s)", n_slots,
                                   if (n_slots == 1) "" else "s") else "",
              if (any_mock) "  [MOCK DATA]" else ""))
})
