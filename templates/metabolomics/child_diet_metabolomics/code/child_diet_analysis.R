
# --- Study 1: Child diet in relation to child serum metabolites at 2 and 5-6 years ---
# Researcher-level implementation following the approved Protocol Matrix.

suppressPackageStartupMessages({
  library(MultiAssayExperiment)
  library(SummarizedExperiment)
  library(dplyr)
  library(tidyr)
  library(limma)
})

source("scripts/methods_common.R")

# 1. SETUP PATHS
mae_path <- "data/MAE_original.rds"
output_dir <- "output/results/targeted_v2"
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

# 2. LOAD DATA
mae <- readRDS(mae_path)

# 3. DEFINE EXPOSURE SETS
exposures_nutrients_v6 <- c(
  "CEPercentProDiet6", "CEPercentCHODiet6", "CEPercentFatDiet6", 
  "CEPercentSFADiet6", "CEPercentMUFADiet6", "CEPercentPUFADiet6", 
  "CEPercentFAn3Diet6", "CEPercentFAn6Diet6"
)

exposures_fiber_v6 <- "CFiballDiet6"
exposures_dq_v6 <- c("C_DietQuality_points_6", "C_DietQuality_classes_6")

# Combined exposure list for Visit 6
all_exposures_v6 <- c(exposures_nutrients_v6, exposures_fiber_v6, exposures_dq_v6)

map_v6_to_v7 <- function(x) {
  x <- gsub("_6$", "_7", x)
  gsub("6$", "7", x)
}

visit7_meta_vars <- names(colData(experiments(mae)[["visit_7"]]))
all_exposures_v7 <- unique(vapply(all_exposures_v6, map_v6_to_v7, character(1)))
all_exposures_v7 <- intersect(all_exposures_v7, visit7_meta_vars)

# 4. DEFINE COVARIATE BLOCKS
core_covars_v6 <- c(
  "Intervention", "MPrimipara", "MUniEdu", "MGDMOGTT1OR2Fi",
  "CGender", "CWeightBirth", "Bfdurationm"
)

core_covars_v7 <- c(
  "Intervention", "MPrimipara", "MUniEdu", "MGDMOGTT1OR2Fi",
  "CGender", "CWeightBirth", "Bfdurationm"
)

baseline_covars_q3 <- c(
  "Intervention", "MPrimipara", "MUniEdu", "MGDMOGTT1OR2Fi",
  "CGender", "CWeightBirth", "Bfdurationm"
)

sensitivity_extra_v6 <- c("CSGA", "CMacrosomy", "CHealthy6")
sensitivity_extra_v7 <- c("MPRSmoke", "Premature", "CSGA", "CMacrosomy", "CHealthy7")
sensitivity_extra_q3 <- c("CSGA", "CMacrosomy", "CHealthy6")

resolve_covars <- function(meta_df, planned_covars) {
  intersect(planned_covars, names(meta_df))
}

make_status_row <- function(question, analysis, exposure, status, reason,
                            n_complete_min = NA_integer_, n_complete_max = NA_integer_,
                            n_features_tested = 0L, term_family = NA_character_,
                            visit_exposure = NA_integer_, visit_outcome = NA_integer_) {
  data.frame(
    question = question,
    analysis = analysis,
    exposure = exposure,
    status = status,
    reason = reason,
    n_complete_min = n_complete_min,
    n_complete_max = n_complete_max,
    n_features_tested = n_features_tested,
    term_family = term_family,
    visit_exposure = visit_exposure,
    visit_outcome = visit_outcome,
    stringsAsFactors = FALSE
  )
}

run_cross_sectional_with_status <- function(tse, exposures, covars, analysis_label, question_label, visit_label) {
  results_list <- vector("list", length(exposures))
  status_list <- vector("list", length(exposures))

  for (i in seq_along(exposures)) {
    exp <- exposures[[i]]
    res <- tryCatch(
      run_targeted_lm_panel(
        tse = tse,
        exposure = exp,
        covars = covars,
        track = "metabolites"
      ),
      error = function(e) e
    )

    if (inherits(res, "error")) {
      status_list[[i]] <- make_status_row(
        question = question_label,
        analysis = analysis_label,
        exposure = exp,
        status = "failed",
        reason = paste0("error: ", conditionMessage(res)),
        visit_exposure = visit_label,
        visit_outcome = visit_label
      )
      next
    }

    if (is.null(res) || nrow(res) == 0) {
      status_list[[i]] <- make_status_row(
        question = question_label,
        analysis = analysis_label,
        exposure = exp,
        status = "failed",
        reason = "empty_result",
        visit_exposure = visit_label,
        visit_outcome = visit_label
      )
      next
    }

    if ("note" %in% names(res)) {
      status_list[[i]] <- make_status_row(
        question = question_label,
        analysis = analysis_label,
        exposure = exp,
        status = "skipped",
        reason = res$note[[1]],
        visit_exposure = visit_label,
        visit_outcome = visit_label
      )
      next
    }

    results_list[[i]] <- res %>% mutate(visit = visit_label, analysis = analysis_label)
    status_list[[i]] <- make_status_row(
      question = question_label,
      analysis = analysis_label,
      exposure = exp,
      status = "ok",
      reason = "ok",
      n_complete_min = min(res$n, na.rm = TRUE),
      n_complete_max = max(res$n, na.rm = TRUE),
      n_features_tested = nrow(res),
      term_family = if ("term" %in% names(res)) paste(unique(res$term), collapse = "|") else NA_character_,
      visit_exposure = visit_label,
      visit_outcome = visit_label
    )
  }

  list(
    results = bind_rows(results_list),
    status = bind_rows(status_list)
  )
}

extract_prospective_feature_results <- function(sub_df, outcome_var, exposure, covars, baseline_var,
                                                visit_exposure, visit_outcome, analysis_label) {
  vars <- unique(c(outcome_var, baseline_var, exposure, covars))
  vars <- vars[vars %in% names(sub_df)]
  dat <- sub_df[, vars, drop = FALSE]
  dat <- dat[complete.cases(dat), , drop = FALSE]

  if (nrow(dat) < 30) {
    return(NULL)
  }

  dat[[outcome_var]] <- suppressWarnings(as.numeric(dat[[outcome_var]]))
  dat[[baseline_var]] <- suppressWarnings(as.numeric(dat[[baseline_var]]))

  if (is.character(dat[[exposure]])) {
    dat[[exposure]] <- factor(dat[[exposure]])
  }

  y_sd <- sd(dat[[outcome_var]], na.rm = TRUE)
  if (!is.finite(y_sd) || y_sd == 0) {
    return(NULL)
  }
  dat$outcome_std <- (dat[[outcome_var]] - mean(dat[[outcome_var]], na.rm = TRUE)) / y_sd

  formula_terms <- c(exposure, baseline_var, covars)
  fit <- tryCatch(
    lm(
      reformulate(termlabels = formula_terms, response = "outcome_std"),
      data = dat
    ),
    error = function(e) NULL
  )
  if (is.null(fit)) {
    return(NULL)
  }

  coef_tab <- summary(fit)$coefficients
  target_terms <- grep(paste0("^", exposure), rownames(coef_tab), value = TRUE)
  if (length(target_terms) == 0) {
    return(NULL)
  }

  if (length(target_terms) == 1) {
    p_value <- unname(coef_tab[target_terms, "Pr(>|t|)"])
    estimate <- unname(coef_tab[target_terms, "Estimate"])
    ci <- tryCatch(confint(fit, parm = target_terms), error = function(e) c(NA_real_, NA_real_))
    levels_detailed <- NA_character_
    estimates_detailed <- NA_character_
    ci_detailed <- NA_character_
  } else {
    reduced_fit <- tryCatch(
      update(fit, reformulate(c(baseline_var, covars), response = "outcome_std")),
      error = function(e) NULL
    )
    if (is.null(reduced_fit)) {
      return(NULL)
    }
    model_comp <- tryCatch(anova(reduced_fit, fit), error = function(e) NULL)
    if (is.null(model_comp) || nrow(model_comp) < 2) {
      return(NULL)
    }
    p_value <- unname(model_comp$`Pr(>F)`[2])
    
    # Extract all levels
    parts_lvl <- character(length(target_terms))
    parts_est <- character(length(target_terms))
    parts_ci <- character(length(target_terms))
    for (k in seq_along(target_terms)) {
      term_i <- target_terms[k]
      est_i <- unname(coef_tab[term_i, "Estimate"])
      ci_i <- tryCatch(confint(fit, parm = term_i), error = function(e) c(NA_real_, NA_real_))
      clean_label <- gsub(paste0("^", exposure), "", term_i)
      # Shorten the level labels
      clean_label <- gsub("moderate but needs to be improved", "Moderate", clean_label)
      clean_label <- gsub("ok/good", "Good", clean_label, ignore.case = TRUE)
      
      parts_lvl[k] <- clean_label
      parts_est[k] <- as.character(round(est_i, 3))
      parts_ci[k] <- paste0("[", round(ci_i[1], 3), ", ", round(ci_i[2], 3), "]")
    }
    levels_detailed <- paste(parts_lvl, collapse = "<br>")
    estimates_detailed <- paste(parts_est, collapse = "<br>")
    ci_detailed <- paste(parts_ci, collapse = "<br>")
    
    estimate <- unname(coef_tab[target_terms[1], "Estimate"])
    ci <- tryCatch(confint(fit, parm = target_terms[1]), error = function(e) c(NA_real_, NA_real_))
  }

  data.frame(
    feature = outcome_var,
    n = nrow(dat),
    estimate = estimate,
    conf.low = unname(ci[1]),
    conf.high = unname(ci[2]),
    p.value = p_value,
    exposure = exposure,
    visit_exposure = visit_exposure,
    visit_outcome = visit_outcome,
    analysis = analysis_label,
    levels_detailed = levels_detailed,
    estimates_detailed = estimates_detailed,
    ci_detailed = ci_detailed,
    stringsAsFactors = FALSE
  )
}

run_prospective_exposure <- function(sub_df, exposure, covars, analysis_label,
                                     metabolite_cols, baseline_metabolite_names,
                                     visit_exposure = 6L, visit_outcome = 7L) {
  feature_results <- vector("list", length(metabolite_cols))
  feature_cc <- integer(length(metabolite_cols))

  for (i in seq_along(metabolite_cols)) {
    vars_i <- unique(c(metabolite_cols[[i]], baseline_metabolite_names[[i]], exposure, covars))
    vars_i <- vars_i[vars_i %in% names(sub_df)]
    feature_cc[[i]] <- sum(complete.cases(sub_df[, vars_i, drop = FALSE]))

    feature_results[[i]] <- extract_prospective_feature_results(
      sub_df = sub_df,
      outcome_var = metabolite_cols[[i]],
      exposure = exposure,
      covars = covars,
      baseline_var = baseline_metabolite_names[[i]],
      visit_exposure = visit_exposure,
      visit_outcome = visit_outcome,
      analysis_label = analysis_label
    )
  }

  out <- bind_rows(feature_results)
  if (nrow(out) > 0) {
    out <- out %>%
      group_by(exposure) %>%
      mutate(q.value = p.adjust(p.value, method = "BH")) %>%
      ungroup()

    status <- make_status_row(
      question = "Q3",
      analysis = analysis_label,
      exposure = exposure,
      status = "ok",
      reason = "ok",
      n_complete_min = min(out$n, na.rm = TRUE),
      n_complete_max = max(out$n, na.rm = TRUE),
      n_features_tested = nrow(out),
      visit_exposure = visit_exposure,
      visit_outcome = visit_outcome
    )
  } else {
    cc_max <- if (length(feature_cc) == 0) NA_integer_ else max(feature_cc, na.rm = TRUE)
    status <- make_status_row(
      question = "Q3",
      analysis = analysis_label,
      exposure = exposure,
      status = if (!is.na(cc_max) && cc_max < 30L) "skipped" else "failed",
      reason = if (!is.na(cc_max) && cc_max < 30L) "too_few_complete_cases" else "model_failure_or_constant_outcome",
      n_complete_min = if (all(is.na(feature_cc))) NA_integer_ else min(feature_cc, na.rm = TRUE),
      n_complete_max = if (all(is.na(feature_cc))) NA_integer_ else cc_max,
      n_features_tested = 0L,
      visit_exposure = visit_exposure,
      visit_outcome = visit_outcome
    )
  }

  list(results = out, status = status)
}

panel_from_tse <- function(tse, keep_features) {
  meta <- as.data.frame(colData(tse), stringsAsFactors = FALSE)
  feat <- as.data.frame(t(assay(tse, "mbo")[keep_features, , drop = FALSE]), check.names = FALSE)
  meta$sample_id <- rownames(meta)
  feat$sample_id <- rownames(feat)
  left_join(meta, feat, by = "sample_id")
}

coerce_harmonized_value <- function(x) {
  if (is.factor(x) || is.character(x)) {
    return(as.character(x))
  }
  if (inherits(x, "labelled")) {
    return(as.character(x))
  }
  suppressWarnings(as.numeric(x))
}

build_mixed_panel <- function(df6_panel, df7_panel, exp6, exp7, include_healthy = FALSE) {
  if (!(exp6 %in% names(df6_panel)) || !(exp7 %in% names(df7_panel))) {
    return(NULL)
  }

  d6 <- df6_panel %>%
    mutate(
      StudyID = as.character(StudyID),
      visit_num = 6L,
      visit_factor = factor("6", levels = c("6", "7")),
      exposure_it = coerce_harmonized_value(.data[[exp6]]),
      CFastingHoursBloodSample_it = suppressWarnings(as.numeric(CFastingHoursBloodSample6)),
      CMed_it = if ("CMed6" %in% names(.)) as.character(CMed6) else NA_character_,
      CAb_it = if ("CAb6" %in% names(.)) as.character(CAb6) else NA_character_,
      CHealthy_it = if ("CHealthy6" %in% names(.)) as.character(CHealthy6) else NA_character_
    )

  d7 <- df7_panel %>%
    mutate(
      StudyID = as.character(StudyID),
      visit_num = 7L,
      visit_factor = factor("7", levels = c("6", "7")),
      exposure_it = coerce_harmonized_value(.data[[exp7]]),
      CFastingHoursBloodSample_it = suppressWarnings(as.numeric(CFastingHoursBloodSample7)),
      CMed_it = if ("CMed7" %in% names(.)) as.character(CMed7) else NA_character_,
      CAb_it = if ("CAbQ7" %in% names(.)) as.character(CAbQ7) else NA_character_,
      CHealthy_it = if ("CHealthy7" %in% names(.)) as.character(CHealthy7) else NA_character_
    )

  out <- bind_rows(d6, d7)
  if (!include_healthy) {
    out <- out %>% select(-any_of("CHealthy_it"))
  }
  out
}

run_mixed_exposure <- function(df_panel, exposure_label, covars, analysis_label, question_label) {
  vars <- unique(c("StudyID", "visit_num", "exposure_it", covars))
  vars <- vars[vars %in% names(df_panel)]
  cc_rows <- complete.cases(df_panel[, vars, drop = FALSE])
  cc_df <- df_panel[cc_rows, , drop = FALSE]

  n_complete <- nrow(cc_df)
  n_ids <- length(unique(cc_df$StudyID))
  if (n_complete < 60L || n_ids < 25L) {
    return(list(
      results = NULL,
      status = make_status_row(
        question = question_label,
        analysis = analysis_label,
        exposure = exposure_label,
        status = "skipped",
        reason = "too_few_complete_cases_for_mixed_model",
        n_complete_min = n_complete,
        n_complete_max = n_complete,
        n_features_tested = 0L,
        term_family = "main_and_interaction",
        visit_exposure = 6L,
        visit_outcome = 7L
      )
    ))
  }

  res <- tryCatch(
    run_targeted_lmm_panel(
      df = df_panel,
      metabolite_cols = metabolite_cols,
      exposure = "exposure_it",
      covars = covars,
      time_col = "visit_num",
      id_col = "StudyID"
    ),
    error = function(e) e
  )

  if (inherits(res, "error")) {
    return(list(
      results = NULL,
      status = make_status_row(
        question = question_label,
        analysis = analysis_label,
        exposure = exposure_label,
        status = "failed",
        reason = paste0("error: ", conditionMessage(res)),
        n_complete_min = n_complete,
        n_complete_max = n_complete,
        n_features_tested = 0L,
        term_family = "main_and_interaction",
        visit_exposure = 6L,
        visit_outcome = 7L
      )
    ))
  }

  if (is.null(res) || nrow(res) == 0) {
    return(list(
      results = NULL,
      status = make_status_row(
        question = question_label,
        analysis = analysis_label,
        exposure = exposure_label,
        status = "failed",
        reason = "empty_result",
        n_complete_min = n_complete,
        n_complete_max = n_complete,
        n_features_tested = 0L,
        term_family = "main_and_interaction",
        visit_exposure = 6L,
        visit_outcome = 7L
      )
    ))
  }

  if ("note" %in% names(res) && nrow(res) == 1 && identical(res$note[[1]], "no_models_converged")) {
    return(list(
      results = NULL,
      status = make_status_row(
        question = question_label,
        analysis = analysis_label,
        exposure = exposure_label,
        status = "failed",
        reason = "no_models_converged",
        n_complete_min = n_complete,
        n_complete_max = n_complete,
        n_features_tested = 0L,
        term_family = "main_and_interaction",
        visit_exposure = 6L,
        visit_outcome = 7L
      )
    ))
  }

  res <- res %>%
    mutate(
      analysis = analysis_label,
      exposure = exposure_label,
      exposure_pair = sub("6$", "", gsub("_6$", "", exposure_label))
    )

  list(
    results = res,
    status = make_status_row(
      question = question_label,
      analysis = analysis_label,
      exposure = exposure_label,
      status = "ok",
      reason = "ok",
      n_complete_min = min(res$n_obs, na.rm = TRUE),
      n_complete_max = max(res$n_obs, na.rm = TRUE),
      n_features_tested = dplyr::n_distinct(res$feature),
      term_family = paste(sort(unique(res$term)), collapse = "|"),
      visit_exposure = 6L,
      visit_outcome = 7L
    )
  )
}

# 5. RUN ANALYSES
model_status <- list()

# --- Q1: DIET AT 2Y ASSOCIATED WITH METABOLITES AT 2Y ---
message("Running Q1: Cross-sectional at 2 years...")
tse6 <- experiments(mae)[["visit_6"]]
q1_run <- run_cross_sectional_with_status(
  tse = tse6,
  exposures = all_exposures_v6,
  covars = resolve_covars(as.data.frame(colData(tse6)), core_covars_v6),
  analysis_label = "Q1_Concurrent",
  question_label = "Q1",
  visit_label = 6L
)
results_q1 <- q1_run$results
model_status[[length(model_status) + 1L]] <- q1_run$status

sens_covars_q1 <- unique(c(
  resolve_covars(as.data.frame(colData(tse6)), core_covars_v6),
  intersect(sensitivity_extra_v6, names(as.data.frame(colData(tse6))))
))
q1_sens_run <- run_cross_sectional_with_status(
  tse = tse6,
  exposures = all_exposures_v6,
  covars = sens_covars_q1,
  analysis_label = "Q1_Concurrent_sensitivity",
  question_label = "Q1",
  visit_label = 6L
)
results_q1_sensitivity <- q1_sens_run$results
model_status[[length(model_status) + 1L]] <- q1_sens_run$status

# --- Q2: DIET AT 5-6Y ASSOCIATED WITH METABOLITES AT 5-6Y ---
message("Running Q2: Cross-sectional at 5-6 years...")
tse7 <- experiments(mae)[["visit_7"]]
q2_run <- run_cross_sectional_with_status(
  tse = tse7,
  exposures = all_exposures_v7,
  covars = resolve_covars(as.data.frame(colData(tse7)), core_covars_v7),
  analysis_label = "Q2_Concurrent",
  question_label = "Q2",
  visit_label = 7L
)
results_q2 <- q2_run$results
model_status[[length(model_status) + 1L]] <- q2_run$status

sens_covars_q2 <- unique(c(
  resolve_covars(as.data.frame(colData(tse7)), core_covars_v7),
  intersect(sensitivity_extra_v7, names(as.data.frame(colData(tse7))))
))
q2_sens_run <- run_cross_sectional_with_status(
  tse = tse7,
  exposures = all_exposures_v7,
  covars = sens_covars_q2,
  analysis_label = "Q2_Concurrent_sensitivity",
  question_label = "Q2",
  visit_label = 7L
)
results_q2_sensitivity <- q2_sens_run$results
model_status[[length(model_status) + 1L]] <- q2_sens_run$status

# --- Q3: DIET AT 2Y PREDICTS METABOLITES AT 5-6Y ---
message("Running Q3: Prospective 2y -> 5-6y...")
# For Q3, we need exposure from V6 and outcome from V7 joined on StudyID
df6 <- as.data.frame(colData(experiments(mae)[["visit_6"]]))
df7_meta <- as.data.frame(colData(experiments(mae)[["visit_7"]]))
df7_feat <- as.data.frame(t(assay(experiments(mae)[["visit_7"]], "mbo")), check.names = FALSE)
df7_feat$StudyID <- df7_meta$StudyID

df6_feat <- as.data.frame(t(assay(experiments(mae)[["visit_6"]], "mbo")), check.names = FALSE)
df6_feat$StudyID <- df6$StudyID

metabolite_cols <- intersect(
  colnames(df7_feat)[colnames(df7_feat) != "StudyID"],
  colnames(df6_feat)[colnames(df6_feat) != "StudyID"]
)
baseline_metabolite_names <- paste0(metabolite_cols, "_baseline")

q3_covars <- resolve_covars(df6, baseline_covars_q3)
analytic_q3_all <- df7_feat %>%
  inner_join(df6 %>% select(StudyID, all_of(unique(c(all_exposures_v6, q3_covars)))), by = "StudyID") %>%
  inner_join(
    df6_feat %>% select(StudyID, all_of(metabolite_cols)) %>% setNames(c("StudyID", baseline_metabolite_names)),
    by = "StudyID"
  )

q3_run <- lapply(all_exposures_v6, function(exp) {
  message("  Running prospective for ", exp)
  run_prospective_exposure(
    sub_df = analytic_q3_all,
    exposure = exp,
    covars = q3_covars,
    analysis_label = "Q3_Prospective",
    metabolite_cols = metabolite_cols,
    baseline_metabolite_names = baseline_metabolite_names,
    visit_exposure = 6L,
    visit_outcome = 7L
  )
})
results_q3 <- bind_rows(lapply(q3_run, `[[`, "results"))
model_status[[length(model_status) + 1L]] <- bind_rows(lapply(q3_run, `[[`, "status"))

sens_q3_vars <- unique(c(q3_covars, intersect(sensitivity_extra_q3, names(df6))))
analytic_q3_sens <- df7_feat %>%
  inner_join(df6 %>% select(StudyID, all_of(unique(c(all_exposures_v6, sens_q3_vars)))), by = "StudyID") %>%
  inner_join(
    df6_feat %>% select(StudyID, all_of(metabolite_cols)) %>% setNames(c("StudyID", baseline_metabolite_names)),
    by = "StudyID"
  )

q3_sens_run <- lapply(all_exposures_v6, function(exp) {
  run_prospective_exposure(
    sub_df = analytic_q3_sens,
    exposure = exp,
    covars = sens_q3_vars,
    analysis_label = "Q3_Prospective_sensitivity",
    metabolite_cols = metabolite_cols,
    baseline_metabolite_names = baseline_metabolite_names,
    visit_exposure = 6L,
    visit_outcome = 7L
  )
})
results_q3_sensitivity <- bind_rows(lapply(q3_sens_run, `[[`, "results"))
model_status[[length(model_status) + 1L]] <- bind_rows(lapply(q3_sens_run, `[[`, "status"))

# --- Q4: REPEATED-MEASURES DIET ASSOCIATIONS ACROSS VISITS 6 AND 7 ---
message("Running Q4: Repeated-measures mixed models across visits 6 and 7...")
df6_panel <- panel_from_tse(tse6, metabolite_cols)
df7_panel <- panel_from_tse(tse7, metabolite_cols)

mixed_covars_core <- c(
  "Intervention", "MPrimipara", "MUniEdu", "MGDMOGTT1OR2Fi",
  "CGender", "CWeightBirth", "Bfdurationm"
)

mixed_covars_sensitivity <- c(
  mixed_covars_core,
  "MPRSmoke", "Premature", "CSGA", "CMacrosomy", "CHealthy_it"
)

q4_run <- lapply(all_exposures_v6, function(exp6) {
  exp7 <- map_v6_to_v7(exp6)
  panel_df <- build_mixed_panel(df6_panel, df7_panel, exp6, exp7, include_healthy = FALSE)
  if (is.null(panel_df)) {
    return(list(
      results = NULL,
      status = make_status_row(
        question = "Q4",
        analysis = "Q4_Repeated",
        exposure = exp6,
        status = "skipped",
        reason = "paired_exposure_missing",
        visit_exposure = 6L,
        visit_outcome = 7L
      )
    ))
  }
  run_mixed_exposure(
    df_panel = panel_df,
    exposure_label = exp6,
    covars = resolve_covars(panel_df, mixed_covars_core),
    analysis_label = "Q4_Repeated",
    question_label = "Q4"
  )
})
results_q4 <- bind_rows(lapply(q4_run, `[[`, "results"))
model_status[[length(model_status) + 1L]] <- bind_rows(lapply(q4_run, `[[`, "status"))

q4_sens_run <- lapply(all_exposures_v6, function(exp6) {
  exp7 <- map_v6_to_v7(exp6)
  panel_df <- build_mixed_panel(df6_panel, df7_panel, exp6, exp7, include_healthy = TRUE)
  if (is.null(panel_df)) {
    return(list(
      results = NULL,
      status = make_status_row(
        question = "Q4",
        analysis = "Q4_Repeated_sensitivity",
        exposure = exp6,
        status = "skipped",
        reason = "paired_exposure_missing",
        visit_exposure = 6L,
        visit_outcome = 7L
      )
    ))
  }
  run_mixed_exposure(
    df_panel = panel_df,
    exposure_label = exp6,
    covars = resolve_covars(panel_df, mixed_covars_sensitivity),
    analysis_label = "Q4_Repeated_sensitivity",
    question_label = "Q4"
  )
})
results_q4_sensitivity <- bind_rows(lapply(q4_sens_run, `[[`, "results"))
model_status[[length(model_status) + 1L]] <- bind_rows(lapply(q4_sens_run, `[[`, "status"))

# 6. SAVE RESULTS
saveRDS(
  list(
    q1 = results_q1,
    q2 = results_q2,
    q3 = results_q3,
    q4 = results_q4,
    q1_sensitivity = results_q1_sensitivity,
    q2_sensitivity = results_q2_sensitivity,
    q3_sensitivity = results_q3_sensitivity,
    q4_sensitivity = results_q4_sensitivity,
    model_status = bind_rows(model_status)
  ),
  file.path(output_dir, "child_diet_study1_results_v2.rds")
)
message("Analysis complete. Results saved to ", file.path(output_dir, "child_diet_study1_results_v2.rds"))
