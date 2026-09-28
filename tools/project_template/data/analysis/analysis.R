#!/usr/bin/env Rscript
# data/analysis/analysis.R ---------------------------------------------------
# The project's ONE analysis script. It holds all the tests. Fill in sections 1
# and 2; sections 3 and 4 are provided and should not need editing.
#
# It exists so that every project calls the same tests the same way, and so
# that every number the paper might use is computed once, in one place, by one
# script.
#
#     Rscript data/analysis/analysis.R
#
# Output:
#   data/analysis/analysis.md    the numbers, and the only place the drafter
#                                may take a number from
#
# IT WRITES ONE FILE. There used to be an analysis.rds beside it, holding the
# same rows as an R data frame so that a figure script could look up a p-value
# and annotate an asterisk with it. Figures plot from data/raw/ directly and do
# not read this analysis at all, so the second file had one reader that never
# existed. If a figure ever does need to print a p-value, take it from
# analysis.md - the same place the results paragraph takes it from, which is
# what keeps the asterisk and the sentence in agreement. Never recompute a
# test inside a figure script: that is the drift this file exists to prevent,
# and it is now a rule rather than a mechanism.
#
# THE TWO REGIONS OF analysis.md, AND WHY THIS SCRIPT ONLY OWNS ONE
# analysis.md carries a marker line. Everything ABOVE it is rewritten on every
# run; everything BELOW it is never touched - not by this script, not by the
# writing engine, not by `manuscript.py round`. The writing engine appends
# numbers there, and a number that has once been in this file stays in it. That
# is what lets a value be cut from the paper without the value being lost.
#
# If the file exists and has NO marker, the whole of it is kept below the line.
# That is the migration path for a hand-written analysis.md from before this
# file was generated, and it means a first run cannot destroy anybody's notes.
#
# WHAT DOES NOT GO IN analysis.md: what the numbers mean. No takeaways, no
# interpretation - that is the writing engine's job, and a conclusion written
# here arrives in the one file every downstream module trusts as fact.
# Observations go in plan/README.md; a finding you want said goes in the
# `# Notes` region of plan/outline.md.
#
# Every number in the paper is in this file. NOT every number in this file need
# be in the paper - a value nothing points at is kept, reported by nothing, and
# that is a supported state. Mark the ones that must be carried by appending
# `[must appear]` to the line; `manuscript.py completeness` then reports one
# that is missing from the draft.

source(here::here("plan", "setup.R"))

# 1. LOAD --------------------------------------------------------------------
# TODO: read the project's data. Column names are contracted in
# data/data_contract.md - check them there before changing anything here.

# d <- load_data("data/raw/measurements.csv")

# 2. DECLARE -----------------------------------------------------------------
# TODO: which columns are outcomes, and which columns split them into groups.
# Every outcome is tested against every grouping.

outcomes <- character(0)   # e.g. c("yield_pct", "turnover_number")
groups   <- character(0)   # e.g. c("catalyst", "temperature_class")

# 3. RUN ---------------------------------------------------------------------
# Provided. The battery, in order:
#   descriptives  n, mean +/- SD, median (IQR) per group
#   assumptions   Shapiro-Wilk (normality, per group) + Brown-Forsythe (variance)
#   2 groups      Welch t-test if normal, otherwise Wilcoxon rank-sum
#   3+ groups     Welch ANOVA if normal, otherwise Kruskal-Wallis,
#                 then pairwise post-hoc with Holm correction
#   effect size   Hedges' g (2, normal) / rank-biserial (2, non-normal)
#                 / eta-squared (3+), each with a 95% CI
#
# The alpha for the assumption checks is deliberately loose (0.05 on
# Shapiro-Wilk). The choice it drives is recorded in the `reason` column of
# every row, so a reader can disagree with the automatic pick and see exactly
# what it was based on.

ALPHA_ASSUMPTION <- 0.05
BOOT_N <- 1000

.fmt <- function(x, digits = 3) formatC(x, digits = digits, format = "g")

.describe <- function(x, label) {
  x <- x[is.finite(x)]
  data.frame(group = label, n = length(x),
             mean = mean(x), sd = stats::sd(x),
             median = stats::median(x),
             q25 = unname(stats::quantile(x, 0.25)),
             q75 = unname(stats::quantile(x, 0.75)),
             stringsAsFactors = FALSE)
}

.shapiro_ok <- function(splits) {
  ps <- vapply(splits, function(x) {
    x <- x[is.finite(x)]
    if (length(x) < 3 || length(x) > 5000 || stats::sd(x) == 0) return(NA_real_)
    stats::shapiro.test(x)$p.value
  }, numeric(1))
  list(p = ps, ok = all(is.na(ps) | ps > ALPHA_ASSUMPTION))
}

# Brown-Forsythe: Levene's test on absolute deviations from the median.
# Implemented here rather than pulled from car(), which is a heavy dependency
# for fifteen lines of arithmetic.
.brown_forsythe <- function(values, grp) {
  keep <- is.finite(values) & !is.na(grp)
  values <- values[keep]; grp <- droplevels(factor(grp[keep]))
  if (nlevels(grp) < 2) return(NA_real_)
  med <- tapply(values, grp, stats::median)
  z <- abs(values - med[as.character(grp)])
  if (stats::sd(z) == 0) return(1)
  stats::anova(stats::lm(z ~ grp))[["Pr(>F)"]][1]
}

.hedges_g <- function(a, b) {
  a <- a[is.finite(a)]; b <- b[is.finite(b)]
  n1 <- length(a); n2 <- length(b)
  s <- sqrt(((n1 - 1) * stats::var(a) + (n2 - 1) * stats::var(b)) / (n1 + n2 - 2))
  if (!is.finite(s) || s == 0) return(list(est = NA_real_, lo = NA_real_, hi = NA_real_))
  d <- (mean(a) - mean(b)) / s
  J <- 1 - 3 / (4 * (n1 + n2) - 9)          # small-sample bias correction
  g <- d * J
  se <- sqrt((n1 + n2) / (n1 * n2) + d^2 / (2 * (n1 + n2)))
  list(est = g, lo = g - 1.96 * se, hi = g + 1.96 * se)
}

.rank_biserial <- function(a, b) {
  a <- a[is.finite(a)]; b <- b[is.finite(b)]
  n1 <- length(a); n2 <- length(b)
  if (n1 < 1 || n2 < 1) return(list(est = NA_real_, lo = NA_real_, hi = NA_real_))
  U <- suppressWarnings(stats::wilcox.test(a, b, exact = FALSE)$statistic)
  r <- unname(2 * U / (n1 * n2) - 1)
  boot <- replicate(BOOT_N, {
    aa <- sample(a, n1, TRUE); bb <- sample(b, n2, TRUE)
    U <- suppressWarnings(stats::wilcox.test(aa, bb, exact = FALSE)$statistic)
    unname(2 * U / (n1 * n2) - 1)
  })
  list(est = r, lo = unname(stats::quantile(boot, .025, na.rm = TRUE)),
       hi = unname(stats::quantile(boot, .975, na.rm = TRUE)))
}

.eta_squared <- function(values, grp) {
  fit <- stats::aov(values ~ grp)
  ss <- summary(fit)[[1]][["Sum Sq"]]
  est <- ss[1] / sum(ss)
  boot <- replicate(BOOT_N, {
    i <- sample(seq_along(values), length(values), TRUE)
    s <- tryCatch(summary(stats::aov(values[i] ~ grp[i]))[[1]][["Sum Sq"]],
                  error = function(e) NULL)
    if (is.null(s) || length(s) < 2) NA_real_ else s[1] / sum(s)
  })
  list(est = est, lo = unname(stats::quantile(boot, .025, na.rm = TRUE)),
       hi = unname(stats::quantile(boot, .975, na.rm = TRUE)))
}

run_comparison <- function(d, outcome, group) {
  values <- suppressWarnings(as.numeric(d[[outcome]]))
  grp <- droplevels(factor(d[[group]]))
  keep <- is.finite(values) & !is.na(grp)
  values <- values[keep]; grp <- droplevels(grp[keep])
  levs <- levels(grp)
  if (length(levs) < 2) return(NULL)

  splits <- split(values, grp)
  desc <- do.call(rbind, Map(.describe, splits, names(splits)))
  sw <- .shapiro_ok(splits)
  bf <- .brown_forsythe(values, grp)
  normal <- sw$ok

  reason <- sprintf("Shapiro-Wilk %s (min p = %s); Brown-Forsythe p = %s",
                    if (normal) "not rejected" else "rejected",
                    .fmt(suppressWarnings(min(sw$p, na.rm = TRUE))), .fmt(bf))

  rows <- list()
  if (length(levs) == 2) {
    a <- splits[[1]]; b <- splits[[2]]
    if (normal) {
      tt <- stats::t.test(a, b)   # Welch by default: unequal variance is the norm
      es <- .hedges_g(a, b)
      rows[[1]] <- data.frame(
        test = "Welch t-test", effect = "Hedges' g",
        statistic = unname(tt$statistic), p = tt$p.value,
        effect_size = es$est, ci_low = es$lo, ci_high = es$hi)
    } else {
      wt <- suppressWarnings(stats::wilcox.test(a, b, exact = FALSE))
      es <- .rank_biserial(a, b)
      rows[[1]] <- data.frame(
        test = "Wilcoxon rank-sum", effect = "rank-biserial r",
        statistic = unname(wt$statistic), p = wt$p.value,
        effect_size = es$est, ci_low = es$lo, ci_high = es$hi)
    }
    rows[[1]]$comparison <- paste(levs[1], "vs", levs[2])
    rows[[1]]$p_adj <- rows[[1]]$p
  } else {
    if (normal) {
      om <- stats::oneway.test(values ~ grp, var.equal = FALSE)
      es <- .eta_squared(values, grp)
      omnibus <- data.frame(test = "Welch ANOVA", effect = "eta-squared",
                            statistic = unname(om$statistic), p = om$p.value,
                            effect_size = es$est, ci_low = es$lo, ci_high = es$hi,
                            comparison = "omnibus")
      ph <- stats::pairwise.t.test(values, grp, p.adjust.method = "holm",
                                   pool.sd = FALSE)
      post_test <- "Welch t-test, Holm"
    } else {
      kw <- stats::kruskal.test(values ~ grp)
      es <- .eta_squared(values, grp)
      omnibus <- data.frame(test = "Kruskal-Wallis", effect = "eta-squared",
                            statistic = unname(kw$statistic), p = kw$p.value,
                            effect_size = es$est, ci_low = es$lo, ci_high = es$hi,
                            comparison = "omnibus")
      ph <- suppressWarnings(stats::pairwise.wilcox.test(values, grp,
                                                         p.adjust.method = "holm"))
      post_test <- "Wilcoxon, Holm"
    }
    omnibus$p_adj <- omnibus$p
    rows[[1]] <- omnibus
    m <- ph$p.value
    for (r in rownames(m)) for (cc in colnames(m)) {
      if (is.na(m[r, cc])) next
      rows[[length(rows) + 1L]] <- data.frame(
        test = post_test, effect = NA_character_,
        statistic = NA_real_, p = m[r, cc],
        effect_size = NA_real_, ci_low = NA_real_, ci_high = NA_real_,
        comparison = paste(cc, "vs", r), p_adj = m[r, cc])
    }
  }

  out <- do.call(rbind, rows)
  out$outcome <- outcome
  out$group_var <- group
  out$n_total <- length(values)
  out$n_groups <- length(levs)
  out$reason <- reason
  list(results = out[, c("outcome", "group_var", "comparison", "test", "statistic",
                         "p", "p_adj", "effect", "effect_size", "ci_low", "ci_high",
                         "n_total", "n_groups", "reason")],
       descriptives = cbind(outcome = outcome, group_var = group, desc))
}

# 4. WRITE -------------------------------------------------------------------
# Provided. Do not edit.

all_results <- list()
all_desc <- list()
md <- c(sprintf("# Analysis - %s", PROJECT$short_name %||% PROJECT$title %||% ""),
        "",
        sprintf("Generated %s. Test selection is automatic; the reason for each",
                format(Sys.time(), "%Y-%m-%d %H:%M")),
        "choice is recorded with the result. Regenerate with",
        "`Rscript data/analysis/analysis.R`.",
        "",
        "Numbers only - what they mean belongs in the paper, not here.", "")

if (!length(outcomes) || !length(groups)) {
  md <- c(md, "No outcomes or groups declared yet - fill in section 2 of",
          "`data/analysis/analysis.R`. The `analysis` skill will do it with",
          "you if you would rather talk it through than edit R.")
} else {
  for (o in outcomes) for (g in groups) {
    res <- tryCatch(run_comparison(d, o, g), error = function(e) {
      message("  skipped ", o, " ~ ", g, ": ", conditionMessage(e)); NULL
    })
    if (is.null(res)) next
    all_results[[length(all_results) + 1L]] <- res$results
    all_desc[[length(all_desc) + 1L]] <- res$descriptives

    md <- c(md, sprintf("## %s by %s", o, g), "",
            "| group | n | mean +/- SD | median (IQR) |", "|---|---|---|---|")
    for (i in seq_len(nrow(res$descriptives))) {
      r <- res$descriptives[i, ]
      md <- c(md, sprintf("| %s | %d | %s +/- %s | %s (%s-%s) |", r$group, r$n,
                          .fmt(r$mean), .fmt(r$sd), .fmt(r$median),
                          .fmt(r$q25), .fmt(r$q75)))
    }
    md <- c(md, "", sprintf("Test chosen: **%s** - %s", res$results$test[1],
                            res$results$reason[1]), "",
            "| comparison | test | statistic | p (adj) | effect |", "|---|---|---|---|---|")
    for (i in seq_len(nrow(res$results))) {
      r <- res$results[i, ]
      eff <- if (is.na(r$effect_size)) "-" else
        sprintf("%s = %s [%s, %s]", r$effect, .fmt(r$effect_size),
                .fmt(r$ci_low), .fmt(r$ci_high))
      md <- c(md, sprintf("| %s | %s | %s | %s | %s |", r$comparison, r$test,
                          if (is.na(r$statistic)) "-" else .fmt(r$statistic),
                          .fmt(r$p_adj), eff))
    }
    md <- c(md, "")
  }
}

stats_df <- if (length(all_results)) do.call(rbind, all_results) else
  data.frame(outcome = character(0), group_var = character(0),
             comparison = character(0), test = character(0),
             statistic = numeric(0), p = numeric(0), p_adj = numeric(0),
             effect = character(0), effect_size = numeric(0),
             ci_low = numeric(0), ci_high = numeric(0),
             n_total = integer(0), n_groups = integer(0), reason = character(0))

attr(stats_df, "descriptives") <- if (length(all_desc)) do.call(rbind, all_desc) else NULL

# The marker. Everything above it is ours; everything below it is the user's
# and the writing engine's, and is copied through byte for byte.
#
# MARKER is matched on the first line alone rather than on the whole block, so
# a user who reflows the comment or edits its wording still has a file this
# script recognises. Losing the marker means losing the tail, so the match is
# made as forgiving as it can be while still being unambiguous.

MARKER_HEAD <- "GENERATED ABOVE, KEPT BELOW."
MARKER <- c(
  "<!-- ------------------------------------------------------------------",
  sprintf("     %s", MARKER_HEAD),
  "     Rscript data/analysis/analysis.R rewrites everything ABOVE this line",
  "     on every run, and never touches anything below it. Numbers added by",
  "     the writing engine, or by hand, go below. Nothing here is ever",
  "     deleted - a value cut from the paper is still a value you measured.",
  "",
  "     Mark a line `[must appear]` and completeness reports it when the",
  "     draft does not carry it.",
  "     --------------------------------------------------------------- -->")

out_path <- here::here("data", "analysis", "analysis.md")

# Read back whatever is already there and keep the tail. Three cases, and the
# unmarked one is the important one: a hand-written analysis.md from before
# this file was generated is preserved WHOLE rather than overwritten.
kept <- character(0)
if (file.exists(out_path)) {
  prev <- readLines(out_path, warn = FALSE, encoding = "UTF-8")
  hit <- grep(MARKER_HEAD, prev, fixed = TRUE)
  if (length(hit)) {
    # Below the marker BLOCK, not below the matched line - the marker is a
    # multi-line comment and its closing `-->` must not survive into the tail.
    close <- grep("-->", prev, fixed = TRUE)
    close <- close[close >= hit[1]]
    start <- if (length(close)) close[1] + 1L else hit[1] + 1L
    kept <- if (start <= length(prev)) prev[start:length(prev)] else character(0)
  } else {
    kept <- c("",
              sprintf("<!-- Kept from the analysis.md that was here before %s.",
                      format(Sys.Date(), "%Y-%m-%d")),
              "     Nothing below this line is ever generated or removed. -->",
              "", prev)
  }
}

writeLines(c(md, MARKER, kept), out_path, useBytes = TRUE)
write_provenance()

message("analysis: ", nrow(stats_df), " comparison(s) -> ",
        "data/analysis/analysis.md; ", length(kept),
        " line(s) kept below the marker")
