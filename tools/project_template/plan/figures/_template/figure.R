# plan/figures/<FigNN>/figure.R
#
# ONE FIGURE, ONE FOLDER. Everything Figure N owns lives in this folder - this
# script, figure.png, figure.pdf, and any PowerPoint panel art. The folder name
# carries the number, which is why nothing in here is named "fig03":
# renumbering the figure is renaming the folder, and no file inside it moves.
#
#   python tools/scaffold.py float new <project> --figure     add a folder
#   python tools/scaffold.py float renumber <project> --from 3 --to 2
#
# The loop:
#   1. flip BUILT to TRUE below
#   2. check column names     against data/data_contract.md
#   3. build the plot         save_float(), never a bare ggsave()
#   4. write the caption      add "## Figure 3 - Fig03" to plan/captions.md
#   5. render                 Rscript plan/render_all.R
#   6. look                   open plan/preview.html

source(here::here("plan", "setup.R"))

# Figure N - <one-line title>

# An unbuilt slot renders clean and produces nothing. That is deliberate: a
# freshly scaffolded project has to survive `Rscript plan/render_all.R` on day
# one, and a slot that fails the run teaches you to ignore failures. Flip this
# to TRUE - and delete the guard once the figure is real.
BUILT <- FALSE

if (BUILT) {

  # load_data() records whether this came from data/raw/ or data/mock_data/.
  # Anything downstream of mock_data gets a visible watermark and is blocked
  # from the co-author .docx, so building the figure before the data exists is
  # safe. Replace the file and the column names with your own.
  d <- load_data("data/raw/measurements.csv")

  # A statistic printed on a panel is TYPED from data/analysis/analysis.md,
  # never recomputed here - that is how the asterisk and the results paragraph
  # stay in agreement. Most figures annotate nothing; delete this line.
  # p_value <- 0.002   # yield_pct by catalyst, Welch t-test, analysis.md

  p <- ggplot(d, aes(x = temperature_c, y = yield_pct,
                     colour = catalyst, shape = catalyst)) +
    geom_point(size = 1.6) +
    # EVERY LABEL IS Title Case - axis titles, legend titles, facet strips,
    # panel titles, annotation text. "Growth Temperature (C)", never "growth
    # temperature (C)". Units, gene and protein symbols and species names keep
    # their own casing; the caption sentence is a sentence and is unaffected.
    labs(x = "Temperature (C)", y = "Yield (%)", colour = NULL, shape = NULL)
  # theme_journal() is already applied via theme_set() in theme_journal.R.
  # Map a second aesthetic (shape here, or linetype) to whatever colour
  # encodes: save_float() warns when colour alone separates two series.

  # No name needed. This writes figure.png and figure.pdf into THIS folder at
  # the journal's width and dpi, and records the float under the folder's
  # label - so the script never states its own number.
  save_float(p)
  # save_float(p, width = "single")   # narrow column
  # save_float(p, height = 4.2)       # explicit inches

  message("n = ", nrow(d))            # render_all.R stays quiet otherwise

} else {
  message(float_key(), " not built yet - edit ",
          basename(float_dir()), "/figure.R")
}
