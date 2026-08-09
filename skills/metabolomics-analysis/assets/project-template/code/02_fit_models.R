source("code/00_setup.R")

plan <- read_analysis_plan()
input_path <- file.path(plan$paths$derived_dir, "metabolomics_analysis_data.rds")
if (!file.exists(input_path)) stop("Run code/01_prepare_data.R before fitting models", call. = FALSE)
obj <- readRDS(input_path)
model_spec <- read_table_file("config/model_specification.csv")

data <- obj$analysis_data
feature_map <- obj$feature_map
factor_vars <- intersect(plan$variables$factors, names(data))
for (col in factor_vars) data[[col]] <- as.factor(data[[col]])

required_spec_cols <- c(
  "analysis_id", "scenario", "model_family", "exposure", "exposure_secondary", "exposure_pair",
  "outcome", "outcome_type", "time", "event", "feature_set", "visit", "formula_rhs",
  "covariates", "random_effect", "time_variable", "fdr_family", "min_n", "min_ids", "primary"
)
for (nm in required_spec_cols) {
  if (!nm %in% names(model_spec)) model_spec[[nm]] <- ""
}

results <- list()
status <- list()
result_i <- 0L
status_i <- 0L

clean_value <- function(x) {
  if (is.null(x) || length(x) == 0 || is.na(x[[1]])) return("")
  trimws(as.character(x[[1]]))
}

is_blank_or_marker <- function(x) {
  x <- clean_value(x)
  identical(x, "") || required_marker(x) || optional_marker(x)
}

min_n_for <- function(spec) {
  out <- suppressWarnings(as.integer(clean_value(spec$min_n)))
  if (is.na(out)) out <- plan$models$default_min_n
  out
}

min_ids_for <- function(spec) {
  out <- suppressWarnings(as.integer(clean_value(spec$min_ids)))
  if (is.na(out)) out <- 20L
  out
}

add_status <- function(analysis_id, model_family, feature, exposure, status_value, reason, n_complete = NA_integer_, n_features_tested = NA_integer_) {
  status_i <<- status_i + 1L
  status[[status_i]] <<- data.frame(
    analysis_id = analysis_id,
    model_family = model_family,
    feature = feature,
    exposure = exposure,
    status = status_value,
    reason = reason,
    n_complete = n_complete,
    n_features_tested = n_features_tested,
    stringsAsFactors = FALSE
  )
}

add_result <- function(row) {
  result_i <<- result_i + 1L
  results[[result_i]] <<- row
}

safe_formula_terms <- function(terms) {
  terms <- terms[!is.na(terms) & nzchar(terms)]
  terms
}

standardize_rows <- function(mat) {
  out <- t(apply(mat, 1, function(x) {
    x <- suppressWarnings(as.numeric(x))
    sx <- stats::sd(x, na.rm = TRUE)
    if (!is.finite(sx) || sx == 0) return(rep(NA_real_, length(x)))
    (x - mean(x, na.rm = TRUE)) / sx
  }))
  rownames(out) <- rownames(mat)
  colnames(out) <- colnames(mat)
  out
}

extract_lm_terms <- function(coefs, exposure) {
  term_rows <- coefs$term == exposure | startsWith(coefs$term, exposure)
  if (!any(term_rows)) term_rows <- grepl(exposure, coefs$term, fixed = TRUE)
  term_rows
}

fit_feature_scan_lm <- function(spec, exposure, covariates, analysis_label) {
  analysis_id <- clean_value(spec$analysis_id)
  family <- clean_value(spec$model_family)
  scenario <- clean_value(spec$scenario)
  min_n <- min_n_for(spec)

  if (is_blank_or_marker(exposure) || !exposure %in% names(data)) {
    add_status(analysis_id, family, "__analysis__", exposure, "missing_exposure", paste("Exposure not found:", exposure))
    return(invisible(NULL))
  }
  missing_covariates <- setdiff(covariates, names(data))
  if (length(missing_covariates)) {
    add_status(analysis_id, family, "__analysis__", exposure, "missing_covariates", paste(missing_covariates, collapse = ", "))
    return(invisible(NULL))
  }

  terms <- safe_formula_terms(c(exposure, covariates))
  complete_terms <- stats::complete.cases(data[, terms, drop = FALSE])
  n_complete_terms <- sum(complete_terms)
  if (n_complete_terms < min_n) {
    add_status(analysis_id, family, "__analysis__", exposure, "skipped", sprintf("n=%s below min_n=%s", n_complete_terms, min_n), n_complete_terms, 0L)
    return(invisible(NULL))
  }

  feature_cols <- feature_map$feature_column
  feature_names <- feature_map$feature

  if (requireNamespace("limma", quietly = TRUE)) {
    meta_cc <- data[complete_terms, terms, drop = FALSE]
    mat <- t(as.matrix(data[complete_terms, feature_cols, drop = FALSE]))
    rownames(mat) <- feature_names
    mat_std <- standardize_rows(mat)
    keep <- rowSums(is.finite(mat_std)) >= min_n
    if (!any(keep)) {
      add_status(analysis_id, family, "__analysis__", exposure, "skipped", "No non-constant features with sufficient data", n_complete_terms, 0L)
      return(invisible(NULL))
    }
    mat_std <- mat_std[keep, , drop = FALSE]
    design <- stats::model.matrix(stats::reformulate(terms), data = meta_cc)
    target_terms <- grep(paste0("^", exposure), colnames(design), value = TRUE)
    if (!length(target_terms)) {
      add_status(analysis_id, family, "__analysis__", exposure, "failed", "Exposure term not found in design matrix", n_complete_terms, 0L)
      return(invisible(NULL))
    }
    fit <- limma::lmFit(mat_std, design)
    fit <- limma::eBayes(fit, trend = TRUE)
    tested <- 0L
    for (term in target_terms) {
      tt <- limma::topTable(fit, coef = term, number = Inf, sort.by = "none", confint = TRUE)
      if (!nrow(tt)) next
      qv <- stats::p.adjust(tt$P.Value, method = plan$models$fdr_method)
      for (k in seq_len(nrow(tt))) {
        add_result(data.frame(
          analysis_id = analysis_id,
          scenario = scenario,
          model_family = family,
          engine = "limma_lmFit_eBayes",
          feature = rownames(tt)[k],
          feature_column = feature_map$feature_column[match(rownames(tt)[k], feature_map$feature)],
          exposure = exposure,
          exposure_secondary = "",
          exposure_pair = clean_value(spec$exposure_pair),
          outcome = clean_value(spec$outcome),
          term = term,
          term_role = "primary_exposure",
          estimate = tt$logFC[k],
          std.error = NA_real_,
          conf.low = if ("CI.L" %in% names(tt)) tt$CI.L[k] else NA_real_,
          conf.high = if ("CI.R" %in% names(tt)) tt$CI.R[k] else NA_real_,
          statistic = if ("t" %in% names(tt)) tt$t[k] else NA_real_,
          p.value = tt$P.Value[k],
          q.value = qv[k],
          n = n_complete_terms,
          n_ids = NA_integer_,
          status = "fitted",
          stringsAsFactors = FALSE
        ))
        tested <- tested + 1L
      }
    }
    add_status(analysis_id, family, "__analysis__", exposure, "fitted", "Feature scan fitted with limma::lmFit/eBayes", n_complete_terms, tested)
    return(invisible(NULL))
  }

  tested <- 0L
  for (j in seq_len(nrow(feature_map))) {
    feature <- feature_map$feature[j]
    feature_col <- feature_map$feature_column[j]
    needed <- c(feature_col, terms)
    complete <- stats::complete.cases(data[, needed, drop = FALSE])
    n_complete <- sum(complete)
    if (n_complete < min_n) {
      add_status(analysis_id, family, feature, exposure, "skipped", sprintf("n=%s below min_n=%s", n_complete, min_n), n_complete, 0L)
      next
    }
    fit_data <- data[complete, needed, drop = FALSE]
    y <- suppressWarnings(as.numeric(fit_data[[feature_col]]))
    sy <- stats::sd(y, na.rm = TRUE)
    if (!is.finite(sy) || sy == 0) {
      add_status(analysis_id, family, feature, exposure, "skipped", "constant_feature", n_complete, 0L)
      next
    }
    fit_data[[feature_col]] <- (y - mean(y, na.rm = TRUE)) / sy
    formula <- stats::reformulate(terms, response = feature_col)
    fit <- try(stats::lm(formula, data = fit_data), silent = TRUE)
    if (inherits(fit, "try-error")) {
      add_status(analysis_id, family, feature, exposure, "failed", as.character(fit)[1], n_complete, 0L)
      next
    }
    coefs <- as.data.frame(summary(fit)$coefficients, stringsAsFactors = FALSE)
    coefs$term <- rownames(coefs)
    names(coefs)[1:4] <- c("estimate", "std.error", "statistic", "p.value")
    term_rows <- extract_lm_terms(coefs, exposure)
    if (!any(term_rows)) {
      add_status(analysis_id, family, feature, exposure, "failed", "Exposure term not found in coefficients", n_complete, 0L)
      next
    }
    selected <- coefs[term_rows, c("term", "estimate", "std.error", "statistic", "p.value"), drop = FALSE]
    for (k in seq_len(nrow(selected))) {
      add_result(data.frame(
        analysis_id = analysis_id,
        scenario = scenario,
        model_family = family,
        engine = "stats_lm_fallback",
        feature = feature,
        feature_column = feature_col,
        exposure = exposure,
        exposure_secondary = "",
        exposure_pair = clean_value(spec$exposure_pair),
        outcome = clean_value(spec$outcome),
        term = selected$term[k],
        term_role = "primary_exposure",
        estimate = selected$estimate[k],
        std.error = selected$std.error[k],
        conf.low = NA_real_,
        conf.high = NA_real_,
        statistic = selected$statistic[k],
        p.value = selected$p.value[k],
        q.value = NA_real_,
        n = n_complete,
        n_ids = NA_integer_,
        status = "fitted",
        stringsAsFactors = FALSE
      ))
      tested <- tested + 1L
    }
    add_status(analysis_id, family, feature, exposure, "fitted", "Feature scan fitted with stats::lm fallback because limma is unavailable", n_complete, 1L)
  }
}

fit_mutual_timing <- function(spec, covariates) {
  analysis_id <- clean_value(spec$analysis_id)
  family <- clean_value(spec$model_family)
  scenario <- clean_value(spec$scenario)
  exposure <- clean_value(spec$exposure)
  exposure_secondary <- clean_value(spec$exposure_secondary)
  exposure_pair <- clean_value(spec$exposure_pair)
  min_n <- min_n_for(spec)

  if (!requireNamespace("limma", quietly = TRUE)) {
    add_status(analysis_id, family, "__analysis__", exposure_pair, "unavailable", "Package limma is required for mutual-timing feature scans")
    return(invisible(NULL))
  }
  if (is_blank_or_marker(exposure) || is_blank_or_marker(exposure_secondary) || !all(c(exposure, exposure_secondary) %in% names(data))) {
    add_status(analysis_id, family, "__analysis__", exposure_pair, "missing_exposure", "Both exposure and exposure_secondary must exist for mutual timing")
    return(invisible(NULL))
  }
  missing_covariates <- setdiff(covariates, names(data))
  if (length(missing_covariates)) {
    add_status(analysis_id, family, "__analysis__", exposure_pair, "missing_covariates", paste(missing_covariates, collapse = ", "))
    return(invisible(NULL))
  }

  terms <- safe_formula_terms(c(exposure, exposure_secondary, covariates))
  cc <- stats::complete.cases(data[, terms, drop = FALSE])
  n_complete <- sum(cc)
  if (n_complete < min_n) {
    add_status(analysis_id, family, "__analysis__", exposure_pair, "skipped", sprintf("n=%s below min_n=%s", n_complete, min_n), n_complete, 0L)
    return(invisible(NULL))
  }
  meta_cc <- data[cc, terms, drop = FALSE]
  mat <- t(as.matrix(data[cc, feature_map$feature_column, drop = FALSE]))
  rownames(mat) <- feature_map$feature
  mat_std <- standardize_rows(mat)
  keep <- rowSums(is.finite(mat_std)) >= min_n
  mat_std <- mat_std[keep, , drop = FALSE]
  if (!nrow(mat_std)) {
    add_status(analysis_id, family, "__analysis__", exposure_pair, "skipped", "No non-constant features with sufficient data", n_complete, 0L)
    return(invisible(NULL))
  }
  design <- stats::model.matrix(stats::reformulate(terms), data = meta_cc)
  primary_terms <- grep(paste0("^", exposure), colnames(design), value = TRUE)
  secondary_terms <- grep(paste0("^", exposure_secondary), colnames(design), value = TRUE)
  if (!length(primary_terms) || !length(secondary_terms)) {
    add_status(analysis_id, family, "__analysis__", exposure_pair, "failed", "Mutual timing terms not found in design matrix", n_complete, 0L)
    return(invisible(NULL))
  }
  fit <- limma::lmFit(mat_std, design)
  fit <- limma::eBayes(fit, trend = TRUE)
  tested <- 0L
  term_sets <- list(primary_timing = primary_terms, secondary_timing = secondary_terms)
  for (role in names(term_sets)) {
    for (term in term_sets[[role]]) {
      tt <- limma::topTable(fit, coef = term, number = Inf, sort.by = "none", confint = TRUE)
      qv <- stats::p.adjust(tt$P.Value, method = plan$models$fdr_method)
      for (k in seq_len(nrow(tt))) {
        add_result(data.frame(
          analysis_id = analysis_id,
          scenario = scenario,
          model_family = family,
          engine = "limma_lmFit_eBayes",
          feature = rownames(tt)[k],
          feature_column = feature_map$feature_column[match(rownames(tt)[k], feature_map$feature)],
          exposure = if (role == "primary_timing") exposure else exposure_secondary,
          exposure_secondary = exposure_secondary,
          exposure_pair = exposure_pair,
          outcome = clean_value(spec$outcome),
          term = term,
          term_role = role,
          estimate = tt$logFC[k],
          std.error = NA_real_,
          conf.low = if ("CI.L" %in% names(tt)) tt$CI.L[k] else NA_real_,
          conf.high = if ("CI.R" %in% names(tt)) tt$CI.R[k] else NA_real_,
          statistic = if ("t" %in% names(tt)) tt$t[k] else NA_real_,
          p.value = tt$P.Value[k],
          q.value = qv[k],
          n = n_complete,
          n_ids = NA_integer_,
          status = "fitted",
          stringsAsFactors = FALSE
        ))
        tested <- tested + 1L
      }
    }
  }
  add_status(analysis_id, family, "__analysis__", exposure_pair, "fitted", "Mutual timing model fitted with both exposure terms in one limma design", n_complete, tested)
}

fit_repeated_mixed <- function(spec, covariates) {
  analysis_id <- clean_value(spec$analysis_id)
  family <- clean_value(spec$model_family)
  scenario <- clean_value(spec$scenario)
  exposure <- clean_value(spec$exposure)
  id_col <- clean_value(spec$random_effect)
  time_col <- clean_value(spec$time_variable)
  if (is_blank_or_marker(id_col)) id_col <- clean_value(plan$identifiers$subject_id)
  if (is_blank_or_marker(time_col)) time_col <- clean_value(plan$identifiers$visit)
  min_n <- min_n_for(spec)
  min_ids <- min_ids_for(spec)

  if (!requireNamespace("lmerTest", quietly = TRUE) || !requireNamespace("broom.mixed", quietly = TRUE)) {
    add_status(analysis_id, family, "__analysis__", exposure, "unavailable", "Packages lmerTest and broom.mixed are required for repeated-measures mixed models")
    return(invisible(NULL))
  }
  needed_core <- c(exposure, id_col, time_col, covariates)
  if (any(is_blank_or_marker(needed_core)) || !all(needed_core %in% names(data))) {
    add_status(analysis_id, family, "__analysis__", exposure, "missing_variables", paste(setdiff(needed_core, names(data)), collapse = ", "))
    return(invisible(NULL))
  }

  tested <- 0L
  for (j in seq_len(nrow(feature_map))) {
    feature <- feature_map$feature[j]
    feature_col <- feature_map$feature_column[j]
    needed <- unique(c(feature_col, needed_core))
    d <- data[, needed, drop = FALSE]
    d <- d[stats::complete.cases(d), , drop = FALSE]
    n_complete <- nrow(d)
    n_ids <- length(unique(d[[id_col]]))
    if (n_complete < min_n || n_ids < min_ids) {
      add_status(analysis_id, family, feature, exposure, "skipped", sprintf("n=%s or ids=%s below thresholds", n_complete, n_ids), n_complete, 0L)
      next
    }
    y <- suppressWarnings(as.numeric(d[[feature_col]]))
    sy <- stats::sd(y, na.rm = TRUE)
    if (!is.finite(sy) || sy == 0) {
      add_status(analysis_id, family, feature, exposure, "skipped", "constant_feature", n_complete, 0L)
      next
    }
    d[[feature_col]] <- (y - mean(y, na.rm = TRUE)) / sy
    d[[id_col]] <- as.factor(d[[id_col]])
    if (is.character(d[[time_col]])) d[[time_col]] <- as.factor(d[[time_col]])
    rhs <- c(paste0("`", exposure, "` * `", time_col, "`"), paste0("`", covariates, "`"))
    rhs <- rhs[nzchar(rhs)]
    formula <- stats::as.formula(paste0("`", feature_col, "` ~ ", paste(rhs, collapse = " + "), " + (1 | `", id_col, "`)"))
    fit <- try(lmerTest::lmer(formula, data = d, REML = FALSE), silent = TRUE)
    if (inherits(fit, "try-error")) {
      add_status(analysis_id, family, feature, exposure, "failed", as.character(fit)[1], n_complete, 0L)
      next
    }
    td <- try(broom.mixed::tidy(fit, effects = "fixed"), silent = TRUE)
    if (inherits(td, "try-error") || !nrow(td)) {
      add_status(analysis_id, family, feature, exposure, "failed", "Could not tidy mixed model", n_complete, 0L)
      next
    }
    term_rows <- td$term == exposure | grepl(paste0("^`?", exposure, "`?"), td$term) | grepl(":", td$term) & grepl(exposure, td$term, fixed = TRUE)
    if (!any(term_rows)) {
      add_status(analysis_id, family, feature, exposure, "failed", "Exposure or interaction term not estimable", n_complete, 0L)
      next
    }
    selected <- td[term_rows, , drop = FALSE]
    for (k in seq_len(nrow(selected))) {
      add_result(data.frame(
        analysis_id = analysis_id,
        scenario = scenario,
        model_family = family,
        engine = "lmerTest_lmer_random_intercept",
        feature = feature,
        feature_column = feature_col,
        exposure = exposure,
        exposure_secondary = "",
        exposure_pair = clean_value(spec$exposure_pair),
        outcome = clean_value(spec$outcome),
        term = selected$term[k],
        term_role = if (grepl(":", selected$term[k], fixed = TRUE)) "exposure_time_interaction" else "main_exposure",
        estimate = selected$estimate[k],
        std.error = selected$std.error[k],
        conf.low = NA_real_,
        conf.high = NA_real_,
        statistic = if ("statistic" %in% names(selected)) selected$statistic[k] else NA_real_,
        p.value = if ("p.value" %in% names(selected)) selected$p.value[k] else NA_real_,
        q.value = NA_real_,
        n = n_complete,
        n_ids = n_ids,
        status = "fitted",
        stringsAsFactors = FALSE
      ))
      tested <- tested + 1L
    }
    add_status(analysis_id, family, feature, exposure, "fitted", "Mixed model fitted with random subject intercept", n_complete, 1L)
  }
}

fit_metabolite_outcome <- function(spec, covariates) {
  analysis_id <- clean_value(spec$analysis_id)
  family <- clean_value(spec$model_family)
  scenario <- clean_value(spec$scenario)
  outcome <- clean_value(spec$outcome)
  outcome_type <- clean_value(spec$outcome_type)
  if (is_blank_or_marker(outcome_type)) outcome_type <- "continuous"
  min_n <- min_n_for(spec)
  if (is_blank_or_marker(outcome) || !outcome %in% names(data)) {
    add_status(analysis_id, family, "__analysis__", "__features__", "missing_outcome", paste("Outcome not found:", outcome))
    return(invisible(NULL))
  }
  missing_covariates <- setdiff(covariates, names(data))
  if (length(missing_covariates)) {
    add_status(analysis_id, family, "__analysis__", "__features__", "missing_covariates", paste(missing_covariates, collapse = ", "))
    return(invisible(NULL))
  }
  tested <- 0L
  for (j in seq_len(nrow(feature_map))) {
    feature <- feature_map$feature[j]
    feature_col <- feature_map$feature_column[j]
    if (outcome_type == "survival") {
      time_col <- clean_value(spec$time)
      event_col <- clean_value(spec$event)
      needed <- unique(c(time_col, event_col, feature_col, covariates))
      if (!requireNamespace("survival", quietly = TRUE)) {
        add_status(analysis_id, family, feature, feature, "unavailable", "Package survival is required for survival outcome models")
        next
      }
      if (any(is_blank_or_marker(needed)) || !all(needed %in% names(data))) {
        add_status(analysis_id, family, feature, feature, "missing_variables", paste(setdiff(needed, names(data)), collapse = ", "))
        next
      }
      d <- data[, needed, drop = FALSE]
    } else {
      needed <- unique(c(outcome, feature_col, covariates))
      d <- data[, needed, drop = FALSE]
    }
    d <- d[stats::complete.cases(d), , drop = FALSE]
    n_complete <- nrow(d)
    if (n_complete < min_n) {
      add_status(analysis_id, family, feature, feature, "skipped", sprintf("n=%s below min_n=%s", n_complete, min_n), n_complete, 0L)
      next
    }
    x <- suppressWarnings(as.numeric(d[[feature_col]]))
    sx <- stats::sd(x, na.rm = TRUE)
    if (!is.finite(sx) || sx == 0) {
      add_status(analysis_id, family, feature, feature, "skipped", "constant_feature", n_complete, 0L)
      next
    }
    d[[feature_col]] <- (x - mean(x, na.rm = TRUE)) / sx
    fit <- try({
      if (outcome_type == "binary") {
        stats::glm(stats::reformulate(c(feature_col, covariates), response = outcome), data = d, family = stats::binomial())
      } else if (outcome_type == "survival") {
        time_col <- clean_value(spec$time)
        event_col <- clean_value(spec$event)
        survival::coxph(stats::as.formula(paste0("survival::Surv(`", time_col, "`, `", event_col, "`) ~ `", feature_col, "`", if (length(covariates)) paste0(" + ", paste(paste0("`", covariates, "`"), collapse = " + ")) else "")), data = d)
      } else {
        stats::lm(stats::reformulate(c(feature_col, covariates), response = outcome), data = d)
      }
    }, silent = TRUE)
    if (inherits(fit, "try-error")) {
      add_status(analysis_id, family, feature, feature, "failed", as.character(fit)[1], n_complete, 0L)
      next
    }
    coefs <- as.data.frame(summary(fit)$coefficients, stringsAsFactors = FALSE)
    coefs$term <- rownames(coefs)
    names(coefs)[1:min(4, ncol(coefs) - 1)] <- c("estimate", "std.error", "statistic", "p.value")[1:min(4, ncol(coefs) - 1)]
    row <- coefs[coefs$term == feature_col | coefs$term == paste0("`", feature_col, "`"), , drop = FALSE]
    if (!nrow(row)) {
      add_status(analysis_id, family, feature, feature, "failed", "Feature term not estimable", n_complete, 0L)
      next
    }
    add_result(data.frame(
      analysis_id = analysis_id,
      scenario = scenario,
      model_family = family,
      engine = paste0("outcome_", outcome_type),
      feature = feature,
      feature_column = feature_col,
      exposure = feature,
      exposure_secondary = "",
      exposure_pair = clean_value(spec$exposure_pair),
      outcome = outcome,
      term = row$term[1],
      term_role = "metabolite_predictor",
      estimate = row$estimate[1],
      std.error = row$std.error[1],
      conf.low = NA_real_,
      conf.high = NA_real_,
      statistic = if ("statistic" %in% names(row)) row$statistic[1] else NA_real_,
      p.value = row$p.value[1],
      q.value = NA_real_,
      n = n_complete,
      n_ids = NA_integer_,
      status = "fitted",
      stringsAsFactors = FALSE
    ))
    tested <- tested + 1L
    add_status(analysis_id, family, feature, feature, "fitted", paste("Outcome model fitted:", outcome_type), n_complete, 1L)
  }
}

for (i in seq_len(nrow(model_spec))) {
  spec <- model_spec[i, , drop = FALSE]
  family <- clean_value(spec$model_family)
  exposure <- clean_value(spec$exposure)
  covariates <- get_covariates(plan, clean_value(spec$covariates))

  if (family %in% c("linear_feature_scan", "cross_sectional_feature_scan", "prospective_feature_scan", "sensitivity_feature_scan")) {
    fit_feature_scan_lm(spec, exposure, covariates, family)
  } else if (family %in% c("mutual_timing_feature_scan", "mutual_timing", "mutual_timing_sensitivity")) {
    fit_mutual_timing(spec, covariates)
  } else if (family %in% c("repeated_measures_mixed_model", "longitudinal_mixed", "repeated_mixed", "longitudinal_mixed_sensitivity", "repeated_mixed_sensitivity")) {
    fit_repeated_mixed(spec, covariates)
  } else if (family %in% c("metabolite_outcome_model", "outcome_model", "clinical_outcome_model")) {
    fit_metabolite_outcome(spec, covariates)
  } else {
    add_status(clean_value(spec$analysis_id), family, "__analysis__", exposure, "unavailable", paste("No method branch defined for", family))
  }
}

result_table <- if (length(results)) do.call(rbind, results) else data.frame()
if (nrow(result_table)) {
  missing_q <- is.na(result_table$q.value) & !is.na(result_table$p.value)
  if (any(missing_q)) {
    groups <- paste(result_table$analysis_id, result_table$term_role, sep = "__")
    result_table$q.value[missing_q] <- ave(result_table$p.value, groups, FUN = function(p) stats::p.adjust(p, method = plan$models$fdr_method))[missing_q]
  }
}
model_status <- if (length(status)) do.call(rbind, status) else data.frame()

result_object <- list(
  plan = plan,
  model_specification = model_spec,
  feature_map = feature_map,
  data_summary = obj$data_summary,
  results = result_table,
  model_status = model_status,
  method_contract = list(
    linear_feature_scan = "limma::lmFit/eBayes when available; stats::lm fallback otherwise",
    mutual_timing_feature_scan = "single limma design with exposure and exposure_secondary jointly adjusted",
    repeated_measures_mixed_model = "lmerTest::lmer with exposure*time and random subject intercept",
    metabolite_outcome_model = "lm, logistic glm, or survival::coxph according to outcome_type"
  )
)

ensure_dir(plan$paths$results_dir)
saveRDS(result_object, file.path(plan$paths$results_dir, "metabolomics_results.rds"))
write_tsv(result_table, file.path(plan$paths$results_dir, "primary_results.tsv"))
write_tsv(model_status, file.path(plan$paths$results_dir, "model_status.tsv"))
message("Wrote results/metabolomics_results.rds, results/primary_results.tsv, and results/model_status.tsv")
