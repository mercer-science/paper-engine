# Data contract

Every column this project's scripts depend on, and what it is allowed to
contain. When a script fails on a missing or misnamed column, **this file is
the first thing to check** — the error messages point here on purpose.

The **kind** column is not decoration. It is what decides both the geom a
figure uses and the statistical test `analysis.R` picks, so recording it
is what makes the templates useful. One of:

| kind | means | typical geom | typical test |
|---|---|---|---|
| `continuous` | measured on a real scale | point, line, box | t / Wilcoxon, ANOVA / Kruskal |
| `count` | non-negative integers | col, point | Poisson / negative binomial |
| `proportion` | bounded 0–1 or 0–100 % | col, point | logistic / beta |
| `categorical` | unordered labels | fill, facet | chi-square / Fisher |
| `ordinal` | ordered labels | fill, point | ordinal / rank |
| `identifier` | sample or run ID, never plotted as data | — | — |
| `datetime` | a timestamp | x axis | — |

---

## `data/raw/<file>.csv`

| column | kind | units | allowed range / levels | notes |
|---|---|---|---|---|
| | | | | |

<!-- Repeat one table per file in data/raw/. Header-only CSVs matching these
     tables belong in data/templates/, so a collaborator can fill one in
     without guessing at column names. -->

## Conventions

- **snake_case** column names, no spaces, no units in the name — units go in
  the table above.
- One row per observation. If a row means "three replicates averaged", that is
  a derived file, not raw data; say so here.
- Missing values are empty, never `NA` typed as text, never `-999`.
- Sample identifiers are stable across files: the same sample is the same
  string everywhere.
