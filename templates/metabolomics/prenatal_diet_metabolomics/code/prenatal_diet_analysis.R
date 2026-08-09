
# --- Study 2: Maternal prenatal diet in relation to child metabolomics from 6 months to 5-6 years ---
# Researcher-level implementation following the approved Protocol Matrix.

suppressPackageStartupMessages({
  library(MultiAssayExperiment)
  library(SummarizedExperiment)
  library(dplyr)
  library(tidyr)
  library(limma)
  library(lme4)
  library(lmerTest)
  library(broom.mixed)
})

source("shared/methods_common.R")

# 1. SETUP PATHS
mae_path <- "../data/MAE_original.rds"
output_dir <- "../output/results/targeted"
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

# 2. LOAD DATA
mae <- readRDS(mae_path)

# 3. DEFINE EXPOSURE SETS (13 primary exposures per pregnancy window)
exposures_primary_early <- c(
  "MDietaryPatterns_1",
  "MDIINormalDiet1",
  "MDIIDensityDiet1",
  "MIDQ1",
  "MEPercentProDiet1",
  "MEPercentCHODiet1",
  "MEPercentFatDiet1",
  "MEPercentPUFADiet1",
  "MEPercentMUFADiet1",
  "MEPercentSFADiet1",
  "MEPercentFAn3Diet1",
  "MEPercentFAn6Diet1",
  "MEPercentFiballDiet1"
)

exposures_primary_late <- c(
  "MDietaryPatterns_2",
  "MDIINormalDiet2",
  "MDIIDensityDiet2",
  "MIDQ2",
  "MEPercentProDiet2",
  "MEPercentCHODiet2",
  "MEPercentFatDiet2",
  "MEPercentPUFADiet2",
  "MEPercentMUFADiet2",
  "MEPercentSFADiet2",
  "MEPercentFAn3Diet2",
  "MEPercentFAn6Diet2",
  "MEPercentFiballDiet2"
)

strip_window_suffix <- function(x) {
  x <- sub("_[12]$", "", x)
  sub("[12]$", "", x)
}

# 4. DEFINE COVARIATE BLOCKS
core_maternal <- c(
  "Intervention", "CGender", "MEthnicity", "MDise"
)
sensitivity_extra <- c(
  "MprepBMI", "MAge1", "MprevGDM_new", "Bfdurationm",
  "MPRSmoke", "MUniEdu", "CWeightBirth"
)

make_status_row <- function(question, analysis, exposure, timing = NA_character_,
                            visit = NA_integer_, status, reason,
                            n_complete_min = NA_integer_, n_complete_max = NA_integer_,
                            n_features_tested = 0L, term_family = NA_character_) {
  data.frame(
    question = question,
    analysis = analysis,
    exposure = exposure,
    timing = timing,
    visit = visit,
    status = status,
    reason = reason,
    n_complete_min = n_complete_min,
    n_complete_max = n_complete_max,
    n_features_tested = n_features_tested,
    term_family = term_family,
    stringsAsFactors = FALSE
  )
}

# 5. RUN ANALYSES (Age-specific)
visits <- c(4, 5, 6, 7)
results_age_specific <- list()
results_age_specific_sensitivity <- list()

for (v in visits) {
  message("Running Age ", v, " analysis...")
  tse_v <- experiments(mae)[[paste0("visit_", v)]]
  meta_cols <- colnames(as.data.frame(colData(tse_v)))
  missing_early <- setdiff(exposures_primary_early, meta_cols)
  missing_late <- setdiff(exposures_primary_late, meta_cols)
  if (length(missing_early) > 0 || length(missing_late) > 0) {
    stop(
      "Missing planned exposure columns in visit_", v, ": ",
      paste(unique(c(missing_early, missing_late)), collapse = ", ")
    )
  }
  
  # Timing-specific Tier 2/3 covariates
  # FastingHours name from metadata:  # Define covariates for this visit
  fasting_var <- paste0("CFastingHoursBloodSample", v)
  covars_v <- c(intersect(core_maternal, meta_cols), intersect(fasting_var, meta_cols))
  sens_covars_v <- unique(c(covars_v, intersect(sensitivity_extra, meta_cols)))
  
  # Q4: Early pregnancy diet -> child metabolites at visit V
  message("  Running Early timing (Q4)...")
  res_early <- lapply(exposures_primary_early, function(exp) {
    tryCatch({
      res <- run_targeted_lm_panel(tse_v, exp, covars_v, track = "metabolites")
      if (is.null(res)) {
        message(paste("    -", exp, ": NULL (likely CC < 10 or model failure)"))
        return(NULL)
      }
      if (nrow(res) > 0 && "note" %in% names(res)) {
        message(paste("    -", exp, ":", res$note[1]))
        return(NULL)
      }
      message(paste("    -", exp, ": success,", nrow(res), "features"))
      res %>% mutate(visit = v, timing = "Early", analysis = "Q4")
    }, error = function(e) {
      message(paste("    -", exp, ": Error:", e$message))
      NULL
    })
  }) %>% bind_rows()
  
  # Q5: Late pregnancy diet -> child metabolites at visit V
  message("  Running Late timing (Q5)...")
  res_late <- lapply(exposures_primary_late, function(exp) {
    tryCatch({
      res <- run_targeted_lm_panel(tse_v, exp, covars_v, track = "metabolites")
      if (is.null(res)) {
        message(paste("    -", exp, ": NULL (likely CC < 10 or model failure)"))
        return(NULL)
      }
      if (nrow(res) > 0 && "note" %in% names(res)) {
        message(paste("    -", exp, ":", res$note[1]))
        return(NULL)
      }
      message(paste("    -", exp, ": success,", nrow(res), "features"))
      res %>% mutate(visit = v, timing = "Late", analysis = "Q5")
    }, error = function(e) {
      message(paste("    -", exp, ": Error:", e$message))
      NULL
    })
  }) %>% bind_rows()
  
  # Q6: Mutually adjusted timing
  message("  Running Mutual adjustment (Q6)...")
  res_mutual <- lapply(seq_along(exposures_primary_early), function(i) {
    exp_e <- exposures_primary_early[i]
    exp_l <- exposures_primary_late[i]
    
    tryCatch({
      # Mutual adjustment is not in run_targeted_lm_panel yet, we'll run it manually
      meta <- as.data.frame(colData(tse_v))
      feat <- as.data.frame(t(assay(tse_v, "mbo")))
      vars <- unique(c(exp_e, exp_l, covars_v))
      cc <- complete.cases(meta[, vars])
      
      if (sum(cc) < 10) { # Check for sufficient complete cases
        message(paste("    -", exp_e, "+", exp_l, ": Not enough complete cases (", sum(cc), ")"))
        return(NULL)
      }

      sub_meta <- meta[cc, ]
      sub_feat <- feat[cc, ]
      
      mat_std <- t(apply(sub_feat, 2, function(x) (x - mean(x, na.rm=T)) / sd(x, na.rm=T)))
      
      formula_str <- paste0("~ ", exp_e, " + ", exp_l, " + ", paste(covars_v, collapse = " + "))
      design <- model.matrix(as.formula(formula_str), data = sub_meta)
      
      fit <- lmFit(mat_std, design)
      fit <- eBayes(fit, trend = TRUE)
      
      # Extract BOTH
      e_term <- grep(paste0("^", exp_e), colnames(design), value = TRUE)
      l_term <- grep(paste0("^", exp_l), colnames(design), value = TRUE)
      
      res_e <- topTable(fit, coef = e_term, number = Inf, sort.by = "none", confint = TRUE)
      res_l <- topTable(fit, coef = l_term, number = Inf, sort.by = "none", confint = TRUE)

      data.frame(
        feature = rownames(res_e),
        n = nrow(sub_meta),
        estimate_early = res_e$logFC,
        conf.low_early = res_e$CI.L,
        conf.high_early = res_e$CI.R,
        p_early = res_e$P.Value,
        estimate_late = res_l$logFC,
        conf.low_late = res_l$CI.L,
        conf.high_late = res_l$CI.R,
        p_late = res_l$P.Value,
        visit = v,
        exposure_pair = strip_window_suffix(exp_e),
        analysis = "Q6_Mutual"
      ) %>%
        group_by(visit, exposure_pair) %>%
        mutate(
          q_early = p.adjust(p_early, method = "BH"),
          q_late = p.adjust(p_late, method = "BH")
        ) %>%
        ungroup()
    }, error = function(e) NULL)
  }) %>% bind_rows()
  
  results_age_specific[[as.character(v)]] <- list(early = res_early, late = res_late, mutual = res_mutual)

  # Sensitivity reruns: same model family with expanded covariate block.
  message("  Running Sensitivity timing (Q4/Q5/Q6)...")
  res_early_sens <- lapply(exposures_primary_early, function(exp) {
    tryCatch({
      res <- run_targeted_lm_panel(tse_v, exp, sens_covars_v, track = "metabolites")
      if (is.null(res)) return(NULL)
      if (nrow(res) > 0 && "note" %in% names(res)) return(NULL)
      res %>% mutate(visit = v, timing = "Early", analysis = "Q4_sensitivity")
    }, error = function(e) NULL)
  }) %>% bind_rows()

  res_late_sens <- lapply(exposures_primary_late, function(exp) {
    tryCatch({
      res <- run_targeted_lm_panel(tse_v, exp, sens_covars_v, track = "metabolites")
      if (is.null(res)) return(NULL)
      if (nrow(res) > 0 && "note" %in% names(res)) return(NULL)
      res %>% mutate(visit = v, timing = "Late", analysis = "Q5_sensitivity")
    }, error = function(e) NULL)
  }) %>% bind_rows()

  res_mutual_sens <- lapply(seq_along(exposures_primary_early), function(i) {
    exp_e <- exposures_primary_early[i]
    exp_l <- exposures_primary_late[i]

    tryCatch({
      meta <- as.data.frame(colData(tse_v))
      feat <- as.data.frame(t(assay(tse_v, "mbo")))
      vars <- unique(c(exp_e, exp_l, sens_covars_v))
      cc <- complete.cases(meta[, vars])

      if (sum(cc) < 10) return(NULL)

      sub_meta <- meta[cc, ]
      sub_feat <- feat[cc, ]

      mat_std <- t(apply(sub_feat, 2, function(x) (x - mean(x, na.rm = TRUE)) / sd(x, na.rm = TRUE)))

      formula_str <- paste0("~ ", exp_e, " + ", exp_l, " + ", paste(sens_covars_v, collapse = " + "))
      design <- model.matrix(as.formula(formula_str), data = sub_meta)

      fit <- lmFit(mat_std, design)
      fit <- eBayes(fit, trend = TRUE)

      e_term <- grep(paste0("^", exp_e), colnames(design), value = TRUE)
      l_term <- grep(paste0("^", exp_l), colnames(design), value = TRUE)
      if (length(e_term) == 0 || length(l_term) == 0) return(NULL)

      res_e <- topTable(fit, coef = e_term, number = Inf, sort.by = "none", confint = TRUE)
      res_l <- topTable(fit, coef = l_term, number = Inf, sort.by = "none", confint = TRUE)

      data.frame(
        feature = rownames(res_e),
        n = nrow(sub_meta),
        estimate_early = res_e$logFC,
        conf.low_early = res_e$CI.L,
        conf.high_early = res_e$CI.R,
        p_early = res_e$P.Value,
        estimate_late = res_l$logFC,
        conf.low_late = res_l$CI.L,
        conf.high_late = res_l$CI.R,
        p_late = res_l$P.Value,
        visit = v,
        exposure_pair = strip_window_suffix(exp_e),
        analysis = "Q6_Mutual_sensitivity"
      ) %>%
        group_by(visit, exposure_pair) %>%
        mutate(
          q_early = p.adjust(p_early, method = "BH"),
          q_late = p.adjust(p_late, method = "BH")
        ) %>%
        ungroup()
    }, error = function(e) NULL)
  }) %>% bind_rows()

  results_age_specific_sensitivity[[as.character(v)]] <- list(
    early = res_early_sens,
    late = res_late_sens,
    mutual = res_mutual_sens
  )
}

# 6. RUN LONGITUDINAL ANALYSIS (Q7)
message("Running Longitudinal (Q7) Mixed-Effects Models...")

extract_long_local <- function(mae) {
  visit_names <- grep("^visit_[0-9]+$", names(experiments(mae)), value = TRUE)
  lapply(visit_names, function(vn) {
    tse <- experiments(mae)[[vn]]
    meta <- as.data.frame(colData(tse))
    feat <- as.data.frame(t(assay(tse, "mbo")))

    # Preserve the participant identifier on the metadata side of the join.
    meta$StudyID <- as.character(meta$StudyID)
    feat$sample_id <- rownames(feat)
    meta$sample_id <- rownames(meta)

    df <- left_join(meta, feat, by = "sample_id")
    if (!("StudyID" %in% names(df))) {
      stop("StudyID missing after longitudinal join for ", vn)
    }
    df$visit_num <- as.integer(gsub("visit_", "", vn))
    df
  }) %>% bind_rows()
}

df_long_all <- extract_long_local(mae)
metabolite_cols <- rownames(assay(experiments(mae)[["visit_all"]], "mbo"))
metabolite_cols <- as.character(metabolite_cols)
if (length(metabolite_cols) == 0L) {
  stop("No longitudinal metabolite features found in visit_all assay.")
}

message("Longitudinal DF rows: ", nrow(df_long_all))
message("Longitudinal DF columns: ", paste(head(names(df_long_all), 20), collapse=", "))
message("Visit num distribution: ", paste(table(df_long_all$visit_num), collapse=", "))

run_q7_window <- function(exposures, timing_label) {
  lapply(exposures, function(exp) {
    message("  Longitudinal for ", timing_label, " window: ", exp)
    tryCatch({
      res <- run_targeted_lmm_panel(
        df = df_long_all,
        metabolite_cols = metabolite_cols,
        exposure = exp,
        covars = intersect(core_maternal, names(df_long_all)),
        time_col = "visit_num",
        id_col = "StudyID"
      )
      if (is.null(res) || nrow(res) == 0) {
        message("    [Q7] Result empty for ", exp)
        return(NULL)
      }
      if (!("exposure" %in% names(res))) {
        res$exposure <- exp
      }
      res %>% mutate(analysis = "Q7_Interaction", timing = timing_label)
    }, error = function(e) {
      message("    [Q7] Error for ", exp, ": ", e$message)
      NULL
    })
  }) %>% bind_rows()
}

results_q7 <- bind_rows(
  run_q7_window(exposures_primary_early, "Early"),
  run_q7_window(exposures_primary_late, "Late")
)

q7_sens_covars <- unique(c(intersect(core_maternal, names(df_long_all)), intersect(sensitivity_extra, names(df_long_all))))

run_q7_window_sensitivity <- function(exposures, timing_label) {
  lapply(exposures, function(exp) {
    tryCatch({
      res <- run_targeted_lmm_panel(
        df = df_long_all,
        metabolite_cols = metabolite_cols,
        exposure = exp,
        covars = q7_sens_covars,
        time_col = "visit_num",
        id_col = "StudyID"
      )
      if (is.null(res) || nrow(res) == 0) return(NULL)
      if (!("exposure" %in% names(res))) res$exposure <- exp
      res %>% mutate(analysis = "Q7_Interaction_sensitivity", timing = timing_label)
    }, error = function(e) NULL)
  }) %>% bind_rows()
}

results_q7_sensitivity <- bind_rows(
  run_q7_window_sensitivity(exposures_primary_early, "Early"),
  run_q7_window_sensitivity(exposures_primary_late, "Late")
)

build_age_status <- function(results_df, exposures, meta_df, covars, question, analysis, timing, visit, min_n) {
  bind_rows(lapply(exposures, function(exp) {
    vars <- unique(c(exp, covars))
    vars <- vars[vars %in% names(meta_df)]
    n_cc <- if (length(vars) == 0) 0L else sum(complete.cases(meta_df[, vars, drop = FALSE]))
    res_sub <- if (!is.null(results_df) && nrow(results_df) > 0) {
      results_df %>% filter(.data$exposure == exp, .data$visit == visit)
    } else {
      data.frame()
    }

    if (nrow(res_sub) > 0) {
      return(make_status_row(
        question = question,
        analysis = analysis,
        exposure = exp,
        timing = timing,
        visit = visit,
        status = "ok",
        reason = "ok",
        n_complete_min = min(res_sub$n, na.rm = TRUE),
        n_complete_max = max(res_sub$n, na.rm = TRUE),
        n_features_tested = dplyr::n_distinct(res_sub$feature)
      ))
    }

    make_status_row(
      question = question,
      analysis = analysis,
      exposure = exp,
      timing = timing,
      visit = visit,
      status = if (n_cc < min_n) "skipped" else "failed",
      reason = if (n_cc < min_n) "too_few_complete_cases" else "no_output",
      n_complete_min = n_cc,
      n_complete_max = n_cc,
      n_features_tested = 0L
    )
  }))
}

build_mutual_status <- function(results_df, exposures_early, exposures_late, meta_df, covars, analysis, visit, min_n = 10L) {
  bind_rows(lapply(seq_along(exposures_early), function(i) {
    exp_e <- exposures_early[[i]]
    exp_l <- exposures_late[[i]]
    exp_pair <- strip_window_suffix(exp_e)
    vars <- unique(c(exp_e, exp_l, covars))
    vars <- vars[vars %in% names(meta_df)]
    n_cc <- if (length(vars) == 0) 0L else sum(complete.cases(meta_df[, vars, drop = FALSE]))
    res_sub <- if (!is.null(results_df) && nrow(results_df) > 0) {
      results_df %>% filter(.data$exposure_pair == exp_pair, .data$visit == visit)
    } else {
      data.frame()
    }

    if (nrow(res_sub) > 0) {
      return(make_status_row(
        question = "Q6",
        analysis = analysis,
        exposure = exp_pair,
        timing = "Mutual",
        visit = visit,
        status = "ok",
        reason = "ok",
        n_complete_min = min(res_sub$n, na.rm = TRUE),
        n_complete_max = max(res_sub$n, na.rm = TRUE),
        n_features_tested = dplyr::n_distinct(res_sub$feature),
        term_family = "early_and_late"
      ))
    }

    make_status_row(
      question = "Q6",
      analysis = analysis,
      exposure = exp_pair,
      timing = "Mutual",
      visit = visit,
      status = if (n_cc < min_n) "skipped" else "failed",
      reason = if (n_cc < min_n) "too_few_complete_cases" else "no_output",
      n_complete_min = n_cc,
      n_complete_max = n_cc,
      n_features_tested = 0L,
      term_family = "early_and_late"
    )
  }))
}

build_longitudinal_status <- function(results_df, exposures, timing, analysis, covars, df_long_all) {
  bind_rows(lapply(exposures, function(exp) {
    vars <- unique(c("StudyID", "visit_num", exp, covars))
    vars <- vars[vars %in% names(df_long_all)]
    cc_df <- if (length(vars) == 0) df_long_all[0, , drop = FALSE] else df_long_all[complete.cases(df_long_all[, vars, drop = FALSE]), , drop = FALSE]
    n_cc <- nrow(cc_df)
    res_sub <- if (!is.null(results_df) && nrow(results_df) > 0) {
      results_df %>% filter(.data$exposure == exp, .data$timing == timing)
    } else {
      data.frame()
    }

    if (nrow(res_sub) > 0) {
      return(make_status_row(
        question = "Q7",
        analysis = analysis,
        exposure = exp,
        timing = timing,
        status = "ok",
        reason = "ok",
        n_complete_min = min(res_sub$n_obs, na.rm = TRUE),
        n_complete_max = max(res_sub$n_obs, na.rm = TRUE),
        n_features_tested = dplyr::n_distinct(res_sub$feature),
        term_family = paste(sort(unique(res_sub$term)), collapse = "|")
      ))
    }

    make_status_row(
      question = "Q7",
      analysis = analysis,
      exposure = exp,
      timing = timing,
      status = if (n_cc < 50L) "skipped" else "failed",
      reason = if (n_cc < 50L) "too_few_complete_cases_for_mixed_model" else "no_models_converged_or_no_output",
      n_complete_min = n_cc,
      n_complete_max = n_cc,
      n_features_tested = 0L,
      term_family = "main_and_interaction"
    )
  }))
}

model_status <- bind_rows(lapply(visits, function(v) {
  tse_v <- experiments(mae)[[paste0("visit_", v)]]
  meta_v <- as.data.frame(colData(tse_v))
  fasting_var <- paste0("CFastingHoursBloodSample", v)
  covars_v <- c(intersect(core_maternal, names(meta_v)), intersect(fasting_var, names(meta_v)))
  sens_covars_v <- unique(c(covars_v, intersect(sensitivity_extra, names(meta_v))))

  bind_rows(
    build_age_status(
      results_df = results_age_specific[[as.character(v)]]$early,
      exposures = exposures_primary_early,
      meta_df = meta_v,
      covars = covars_v,
      question = "Q4",
      analysis = "Q4",
      timing = "Early",
      visit = v,
      min_n = 30L
    ),
    build_age_status(
      results_df = results_age_specific[[as.character(v)]]$late,
      exposures = exposures_primary_late,
      meta_df = meta_v,
      covars = covars_v,
      question = "Q5",
      analysis = "Q5",
      timing = "Late",
      visit = v,
      min_n = 30L
    ),
    build_mutual_status(
      results_df = results_age_specific[[as.character(v)]]$mutual,
      exposures_early = exposures_primary_early,
      exposures_late = exposures_primary_late,
      meta_df = meta_v,
      covars = covars_v,
      analysis = "Q6_Mutual",
      visit = v,
      min_n = 10L
    ),
    build_age_status(
      results_df = results_age_specific_sensitivity[[as.character(v)]]$early,
      exposures = exposures_primary_early,
      meta_df = meta_v,
      covars = sens_covars_v,
      question = "Q4",
      analysis = "Q4_sensitivity",
      timing = "Early",
      visit = v,
      min_n = 30L
    ),
    build_age_status(
      results_df = results_age_specific_sensitivity[[as.character(v)]]$late,
      exposures = exposures_primary_late,
      meta_df = meta_v,
      covars = sens_covars_v,
      question = "Q5",
      analysis = "Q5_sensitivity",
      timing = "Late",
      visit = v,
      min_n = 30L
    ),
    build_mutual_status(
      results_df = results_age_specific_sensitivity[[as.character(v)]]$mutual,
      exposures_early = exposures_primary_early,
      exposures_late = exposures_primary_late,
      meta_df = meta_v,
      covars = sens_covars_v,
      analysis = "Q6_Mutual_sensitivity",
      visit = v,
      min_n = 10L
    )
  )
}))

model_status <- bind_rows(
  model_status,
  build_longitudinal_status(
    results_df = results_q7,
    exposures = exposures_primary_early,
    timing = "Early",
    analysis = "Q7_Interaction",
    covars = intersect(core_maternal, names(df_long_all)),
    df_long_all = df_long_all
  ),
  build_longitudinal_status(
    results_df = results_q7,
    exposures = exposures_primary_late,
    timing = "Late",
    analysis = "Q7_Interaction",
    covars = intersect(core_maternal, names(df_long_all)),
    df_long_all = df_long_all
  ),
  build_longitudinal_status(
    results_df = results_q7_sensitivity,
    exposures = exposures_primary_early,
    timing = "Early",
    analysis = "Q7_Interaction_sensitivity",
    covars = q7_sens_covars,
    df_long_all = df_long_all
  ),
  build_longitudinal_status(
    results_df = results_q7_sensitivity,
    exposures = exposures_primary_late,
    timing = "Late",
    analysis = "Q7_Interaction_sensitivity",
    covars = q7_sens_covars,
    df_long_all = df_long_all
  )
)

# 7. SAVE RESULTS
saveRDS(list(
  age_specific = results_age_specific,
  longitudinal = results_q7,
  age_specific_sensitivity = results_age_specific_sensitivity,
  longitudinal_sensitivity = results_q7_sensitivity,
  model_status = model_status
), 
        file.path(output_dir, "prenatal_diet_study2_results.rds"))
message("Analysis complete. Results saved to ", file.path(output_dir, "prenatal_diet_study2_results.rds"))
