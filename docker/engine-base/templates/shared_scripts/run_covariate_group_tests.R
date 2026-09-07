#!/usr/bin/env Rscript

suppressPackageStartupMessages(library(haven))

infer_var_type <- function(x, discrete_cutoff = 10L) {
  lbl <- attr(x, "labels", exact = TRUE)
  nonmiss <- x[!is.na(x)]
  n_unique <- length(unique(nonmiss))

  if (is.character(x) || is.factor(x)) {
    return("discrete")
  }

  if (!is.null(lbl) && length(lbl) > 0) {
    return("discrete")
  }

  if (is.numeric(x) || inherits(x, "haven_labelled")) {
    if (n_unique <= discrete_cutoff) {
      return("discrete")
    }
    return("continuous")
  }

  "discrete"
}

safe_p_value <- function(expr) {
  tryCatch(expr, error = function(e) NA_real_)
}

run_covariate_test <- function(df, group_var, covariate) {
  out <- data.frame(
    covariate = covariate,
    test = NA_character_,
    var_type = NA_character_,
    n_nonmissing = NA_integer_,
    n_unique = NA_integer_,
    n_groups = NA_integer_,
    p_value = NA_real_,
    note = NA_character_,
    stringsAsFactors = FALSE
  )

  if (!covariate %in% names(df)) {
    out$note <- "covariate_missing_in_data"
    return(out)
  }

  if (!group_var %in% names(df)) {
    out$note <- "group_var_missing_in_data"
    return(out)
  }

  if (identical(covariate, group_var)) {
    out$note <- "group_variable_not_tested"
    return(out)
  }

  x <- df[[covariate]]
  g <- df[[group_var]]
  keep <- !is.na(x) & !is.na(g)

  if (!any(keep)) {
    out$note <- "no_complete_cases"
    return(out)
  }

  x <- x[keep]
  g <- as.factor(g[keep])

  out$n_nonmissing <- length(x)
  out$n_unique <- length(unique(x))
  out$n_groups <- nlevels(g)
  out$var_type <- infer_var_type(x)

  if (out$n_groups < 2L) {
    out$note <- "fewer_than_two_groups"
    return(out)
  }

  if (out$n_unique < 2L) {
    out$note <- "constant_covariate"
    return(out)
  }

  if (identical(out$var_type, "continuous")) {
    out$test <- "kruskal_wallis"
    p <- safe_p_value({
      y <- as.numeric(x)
      kruskal.test(y ~ g)$p.value
    })
    out$p_value <- p
    out$note <- ifelse(is.na(p), "test_failed", "ok")
    return(out)
  }

  x_cat <- as.factor(x)
  if (nlevels(x_cat) < 2L) {
    out$note <- "fewer_than_two_levels"
    return(out)
  }

  tbl <- table(g, x_cat)
  if (any(dim(tbl) < 2L)) {
    out$note <- "invalid_contingency_table"
    return(out)
  }

  out$test <- "fisher_exact"
  p <- safe_p_value(fisher.test(tbl)$p.value)

  out$p_value <- p
  out$note <- ifelse(is.na(p), "test_failed", "ok")
  out
}

run_panel <- function(name, data_path, decision_csv, group_var, out_csv, subset_expr = NULL) {
  df <- read_sav(data_path)

  if (!is.null(subset_expr)) {
    keep <- with(df, eval(parse(text = subset_expr)))
    keep[is.na(keep)] <- FALSE
    df <- df[keep, , drop = FALSE]
  }

  decision_tbl <- read.csv(decision_csv, stringsAsFactors = FALSE, check.names = FALSE)
  covariates <- unique(decision_tbl$variable)

  results <- do.call(
    rbind,
    lapply(covariates, function(v) run_covariate_test(df, group_var = group_var, covariate = v))
  )

  results$p_adj_bh <- NA_real_
  ok <- !is.na(results$p_value)
  if (any(ok)) {
    results$p_adj_bh[ok] <- p.adjust(results$p_value[ok], method = "BH")
  }

  if ("decision" %in% names(decision_tbl)) {
    idx <- match(results$covariate, decision_tbl$variable)
    results$decision <- decision_tbl$decision[idx]
  }
  if ("reason" %in% names(decision_tbl)) {
    idx <- match(results$covariate, decision_tbl$variable)
    results$decision_reason <- decision_tbl$reason[idx]
  }

  results <- results[order(results$p_adj_bh, results$p_value, na.last = TRUE), ]
  write.csv(results, out_csv, row.names = FALSE, na = "")

  cat(
    sprintf(
      "%s: wrote %s (%d covariates; %d tested)\n",
      name,
      out_csv,
      nrow(results),
      sum(!is.na(results$p_value))
    )
  )
}

run_panel(
  name = "child_diet_visit6",
  data_path = "data/FOPP_clinical_diet_variables_mother-child_270426.sav",
  decision_csv = "covariate_decision_child_diet_data_driven.csv",
  group_var = "Intervention",
  subset_expr = "!is.na(Filter6) & as.numeric(Filter6) == 1",
  out_csv = "covariate_group_tests_child_diet_visit6.csv"
)

run_panel(
  name = "child_diet_visit7",
  data_path = "data/FOPP_clinical_diet_variables_mother-child_270426.sav",
  decision_csv = "covariate_decision_child_diet_data_driven.csv",
  group_var = "Intervention",
  subset_expr = "!is.na(Filter7) & as.numeric(Filter7) == 1",
  out_csv = "covariate_group_tests_child_diet_visit7.csv"
)

run_panel(
  name = "prenatal_diet_overall",
  data_path = "data/FOPP_clinical_diet_variables_maternal_26062026.sav",
  decision_csv = "covariate_decision_prenatal_diet_data_driven.csv",
  group_var = "Intervention",
  out_csv = "covariate_group_tests_prenatal_diet_overall.csv"
)
