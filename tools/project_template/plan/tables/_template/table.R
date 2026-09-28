# plan/tables/<TableNN>/table.R
#
# ONE TABLE, ONE FOLDER. The folder name carries the number, which is why
# nothing in here is named "tab02" - renumbering the table is renaming the
# folder, and no file inside it moves.
#
# Every table script writes three things, and save_table() does all three:
#   table.html   what plan/preview.html embeds
#   table.rds    the flextable object create_floats.R turns into a NATIVE Word
#                table - editable by co-authors, and never converted
#                downstream, which is why the hand-off does not break
#   table.tex    optional, for a LaTeX submission (save_table(..., tex = TRUE))

source(here::here("plan", "setup.R"))

# Table N - <one-line title>
# Caption block:  ## Table 2 - Table02   in plan/captions.md

# An unbuilt slot renders clean and produces nothing - see figures/_template.
BUILT <- FALSE

if (BUILT) {

  d <- load_data("data/raw/measurements.csv")

  tab <- d |>
    dplyr::group_by(catalyst) |>
    dplyr::summarise(
      n = dplyr::n(),
      `Yield (%)` = sprintf("%.1f +/- %.1f", mean(yield_pct), sd(yield_pct)),
      .groups = "drop")

  ft <- style_float_ft(flextable::flextable(tab))
  # EVERY COLUMN HEADER IS Title Case - "Growth Temperature", never
  # "growth temperature", and never the bare column name. Units, gene and
  # protein symbols and species names keep their own casing; the footer line
  # below is a sentence and is unaffected.
  # ft <- flextable::set_header_labels(ft, catalyst = "Catalyst")
  # ft <- flextable::add_footer_lines(ft, "Mean +/- SD of three runs.")

  # No name needed: writes table.html and table.rds into THIS folder, and
  # records the table under the folder's label.
  save_table(ft)

  message(nrow(tab), " rows")

} else {
  message(float_key(), " not built yet - edit ",
          basename(float_dir()), "/table.R")
}
