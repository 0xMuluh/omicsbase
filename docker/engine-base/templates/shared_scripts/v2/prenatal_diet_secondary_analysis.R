#!/usr/bin/env Rscript
# =============================================================================
# Prenatal diet Secondary Analysis: Biological Group Scores & Sex-Specific Models
# =============================================================================
# This script performs secondary analyses on 5 biological metabolite group
# scores (Inflammation, Amino Acids, Glycolysis/Energy, Fatty Acids,
# Lipoproteins/Lipids) derived from Nightingale NMR metabolomics data.
#
# Analyses:
#   Q4  - Cross-sectional: Early pregnancy diet → group scores (per visit)
#   Q5  - Cross-sectional: Late pregnancy diet  → group scores (per visit)
#   Q6  - Mutual timing:   Early + Late diet    → group scores (per visit)
#   Q7  - Longitudinal:    Diet × age interaction (mixed models)
#   Sex - Interaction:     Diet × CGender, with stratified models if p < 0.05
#
# Output: output/results/targeted_v2/prenatal_diet_secondary_results.rds
# =============================================================================

suppressPackageStartupMessages({
  library(MultiAssayExperiment)
  library(SummarizedExperiment)
  library(dplyr)
  library(tidyr)
  library(lme4)
  library(lmerTest)
  library(broom)
  library(broom.mixed)
})

args_all <- commandArgs(trailingOnly = FALSE)
file_arg <- args_all[grepl("^--file=", args_all)]
script_path <- if (length(file_arg) > 0) {
  normalizePath(sub("^--file=", "", file_arg[[1]]), mustWork = TRUE)
} else {
  normalizePath("scripts/v2/prenatal_diet_secondary_analysis.R", mustWork = TRUE)
}

root_dir <- normalizePath(file.path(dirname(script_path), "..", ".."), mustWork = TRUE)
source(file.path(dirname(script_path), "helpers_v2.R"))
source(file.path(root_dir, "scripts", "metabolomics_common.R"))

# =============================================================================
# BIOLOGICAL GROUP DEFINITIONS
# =============================================================================
# Regex patterns to classify Nightingale metabolite features into 5 groups.
# These are applied in order; first match wins.

GROUP_REGEX <- c(

  inflammation = paste0(
    "GlycA|Glycoprotein"
  ),

  amino_acids = paste0(
    "(^|[-.])(Ala|Gln|Gly|His|Ile|Leu|Val|Phe|Tyr)([-.]|$)|BCAA"
  ),

  glycolysis_energy = paste0(
    "Glucose|Lactate|Pyruvate|Citrate|Acetate|Acetoacetate|",
    "bOHbutyrate|Hydroxybutyrate|Creatinine|Albumin|Acetone|Glycerol"
  ),

  fatty_acids = paste0(
    "Omega|Total-FA|Unsaturation|DHA|EPA|PUFA|MUFA|SFA|",
    "FAn3|FAn6|FA-ratio|LinA|LA"
  ),

  lipoproteins_lipids = paste0(
    "VLDL|LDL|IDL|HDL|Apo|Remnant|non-HDL|",
    "Total-C|Total-TG|Total-PL|Total-CE|Total-FC|Total-L|Total-P|",
    "Phosphoglyc|Phosphatidylc|Cholines|Sphingomyelins|TG/PG"
  )
)

GROUP_LABELS <- c(
  inflammation       = "Inflammation",
  amino_acids        = "Amino Acids",
  glycolysis_energy  = "Glycolysis & Energy",
  fatty_acids        = "Fatty Acids",
  lipoproteins_lipids = "Lipoproteins & Lipids"
)

# =============================================================================
# HELPER: Classify features into biological groups
# =============================================================================
classify_features <- function(feature_names) {
  map <- data.frame(
    feature = feature_names,
    group   = NA_character_,
    stringsAsFactors = FALSE
  )
  for (grp in names(GROUP_REGEX)) {
    idx <- which(is.na(map$group) & grepl(GROUP_REGEX[[grp]], map$feature, ignore.case = TRUE))
    if (length(idx) > 0) map$group[idx] <- grp
  }
  map
}

# =============================================================================
# HELPER: Compute group scores from a metabolite matrix
# =============================================================================
# Input: matrix (features × samples), already log-transformed and Z-scored
# Output: data.frame (samples × groups) with mean Z-score per group
compute_group_scores <- function(mat, feature_map) {
  groups <- unique(na.omit(feature_map$group))
  scores <- data.frame(row.names = colnames(mat))

  for (grp in groups) {
    members <- feature_map$feature[feature_map$group == grp & !is.na(feature_map$group)]
    members <- intersect(members, rownames(mat))
    if (length(members) == 0) {
      scores[[paste0("grp_", grp)]] <- NA_real_
      next
    }
    sub_mat <- mat[members, , drop = FALSE]
    # Z-score each feature within the current sample set
    sub_z <- t(apply(sub_mat, 1, function(x) {
      x <- suppressWarnings(as.numeric(x))
      sx <- sd(x, na.rm = TRUE)
      if (!is.finite(sx) || sx == 0) return(rep(NA_real_, length(x)))
      (x - mean(x, na.rm = TRUE)) / sx
    }))
    # Group score = mean of Z-scored features
    score <- colMeans(sub_z, na.rm = TRUE)
    valid_n <- colSums(!is.na(sub_z))
    score[valid_n < 2] <- NA_real_
    scores[[paste0("grp_", grp)]] <- score
  }
  scores
}

# =============================================================================
# HELPER: Run OLS for a single group score outcome
# =============================================================================
run_group_lm <- function(df, outcome_col, exposure, covars, min_n = 30L) {
  vars <- unique(c(outcome_col, exposure, covars))
  if (!all(vars %in% names(df))) {
    return(tibble(
      outcome = outcome_col, exposure = exposure,
      estimate = NA_real_, std.error = NA_real_,
      conf.low = NA_real_, conf.high = NA_real_,
      p.value = NA_real_, n = NA_integer_,
      note = "missing_variables"
    ))
  }

  d <- df[, vars, drop = FALSE]
  d <- d[complete.cases(d), , drop = FALSE]

  if (nrow(d) < min_n) {
    return(tibble(
      outcome = outcome_col, exposure = exposure,
      estimate = NA_real_, std.error = NA_real_,
      conf.low = NA_real_, conf.high = NA_real_,
      p.value = NA_real_, n = nrow(d),
      note = "too_few_complete_cases"
    ))
  }

  y <- d[[outcome_col]]
  x <- suppressWarnings(as.numeric(d[[exposure]]))
  if (sd(y, na.rm = TRUE) == 0 || sd(x, na.rm = TRUE) == 0 || length(unique(x[is.finite(x)])) < 3) {
    return(tibble(
      outcome = outcome_col, exposure = exposure,
      estimate = NA_real_, std.error = NA_real_,
      conf.low = NA_real_, conf.high = NA_real_,
      p.value = NA_real_, n = nrow(d),
      note = "constant_outcome_or_exposure"
    ))
  }

  # Cast factors and filter out constant covariates
  valid_covars <- c()
  for (cv in covars) {
    if (is.character(d[[cv]])) d[[cv]] <- as.factor(d[[cv]])
    if (inherits(d[[cv]], "haven_labelled")) d[[cv]] <- as.factor(as.integer(d[[cv]]))
    if (length(unique(d[[cv]])) > 1) {
      valid_covars <- c(valid_covars, cv)
    }
  }
  if (inherits(d[[exposure]], "haven_labelled")) d[[exposure]] <- as.numeric(as.integer(d[[exposure]]))

  form <- reformulate(termlabels = c(exposure, valid_covars), response = outcome_col)
  fit <- tryCatch(lm(form, data = d), error = function(e) e)
  if (inherits(fit, "error")) {
    return(tibble(
      outcome = outcome_col, exposure = exposure,
      estimate = NA_real_, std.error = NA_real_,
      conf.low = NA_real_, conf.high = NA_real_,
      p.value = NA_real_, n = nrow(d),
      note = paste0("lm_failed:", conditionMessage(fit))
    ))
  }

  ci <- tryCatch(confint(fit, exposure, level = 0.95), error = function(e) NULL)
  td <- broom::tidy(fit) %>% filter(term == exposure)

  if (nrow(td) == 0) {
    return(tibble(
      outcome = outcome_col, exposure = exposure,
      estimate = NA_real_, std.error = NA_real_,
      conf.low = NA_real_, conf.high = NA_real_,
      p.value = NA_real_, n = nrow(d),
      note = "term_not_estimable"
    ))
  }

  tibble(
    outcome   = outcome_col,
    exposure  = exposure,
    estimate  = td$estimate[1],
    std.error = td$std.error[1],
    conf.low  = if (!is.null(ci)) ci[1, 1] else td$estimate[1] - 1.96 * td$std.error[1],
    conf.high = if (!is.null(ci)) ci[1, 2] else td$estimate[1] + 1.96 * td$std.error[1],
    p.value   = td$p.value[1],
    n         = nrow(d),
    note      = "ok"
  )
}

# =============================================================================
# HELPER: Run interaction model (Diet × CGender) for a single group score
# =============================================================================
run_interaction_lm <- function(df, outcome_col, exposure, sex_var, covars, min_n = 30L) {
  vars <- unique(c(outcome_col, exposure, sex_var, covars))
  if (!all(vars %in% names(df))) {
    return(tibble(
      outcome = outcome_col, exposure = exposure,
      interaction_estimate = NA_real_, interaction_se = NA_real_,
      interaction_p = NA_real_, n = NA_integer_,
      note = "missing_variables"
    ))
  }

  d <- df[, vars, drop = FALSE]
  # Convert haven_labelled columns
  for (v in vars) {
    if (inherits(d[[v]], "haven_labelled")) {
      if (v == sex_var) {
        d[[v]] <- as.factor(as.integer(d[[v]]))
      } else if (v %in% covars) {
        d[[v]] <- as.factor(as.integer(d[[v]]))
      } else {
        d[[v]] <- as.numeric(as.integer(d[[v]]))
      }
    }
  }
  for (cv in covars) {
    if (is.character(d[[cv]])) d[[cv]] <- as.factor(d[[cv]])
  }
  if (is.character(d[[sex_var]])) d[[sex_var]] <- as.factor(d[[sex_var]])

  d <- d[complete.cases(d), , drop = FALSE]

  if (nrow(d) < min_n) {
    return(tibble(
      outcome = outcome_col, exposure = exposure,
      interaction_estimate = NA_real_, interaction_se = NA_real_,
      interaction_p = NA_real_, n = nrow(d),
      note = "too_few_complete_cases"
    ))
  }

  # Check at least 2 sex levels
  if (length(unique(d[[sex_var]])) < 2) {
    return(tibble(
      outcome = outcome_col, exposure = exposure,
      interaction_estimate = NA_real_, interaction_se = NA_real_,
      interaction_p = NA_real_, n = nrow(d),
      note = "single_sex_level"
    ))
  }

  # Cast factors and filter out constant covariates
  valid_covars <- c()
  for (cv in covars) {
    if (length(unique(d[[cv]])) > 1) {
      valid_covars <- c(valid_covars, cv)
    }
  }

  # Formula: outcome ~ exposure * sex + covars
  rhs <- c(
    paste0(exposure, " * ", sex_var),
    valid_covars[!valid_covars %in% c(sex_var)]
  )
  form <- reformulate(termlabels = rhs, response = outcome_col)
  fit <- tryCatch(lm(form, data = d), error = function(e) e)

  if (inherits(fit, "error")) {
    return(tibble(
      outcome = outcome_col, exposure = exposure,
      interaction_estimate = NA_real_, interaction_se = NA_real_,
      interaction_p = NA_real_, n = nrow(d),
      note = paste0("interaction_lm_failed:", conditionMessage(fit))
    ))
  }

  td <- broom::tidy(fit)
  # Find the interaction term
  int_row <- td %>% filter(grepl(paste0(exposure, ":"), term) | grepl(paste0(":", exposure), term))

  if (nrow(int_row) == 0) {
    return(tibble(
      outcome = outcome_col, exposure = exposure,
      interaction_estimate = NA_real_, interaction_se = NA_real_,
      interaction_p = NA_real_, n = nrow(d),
      note = "interaction_term_not_found"
    ))
  }

  tibble(
    outcome              = outcome_col,
    exposure             = exposure,
    interaction_term     = int_row$term[1],
    interaction_estimate = int_row$estimate[1],
    interaction_se       = int_row$std.error[1],
    interaction_p        = int_row$p.value[1],
    n                    = nrow(d),
    note                 = "ok"
  )
}

# =============================================================================
# HELPER: Run sex-stratified models
# =============================================================================
run_stratified_lm <- function(df, outcome_col, exposure, sex_var, covars, min_n = 15L) {
  results <- list()

  # Convert haven_labelled
  if (inherits(df[[sex_var]], "haven_labelled")) {
    df[[sex_var]] <- as.factor(as.integer(df[[sex_var]]))
  }
  if (is.character(df[[sex_var]])) df[[sex_var]] <- as.factor(df[[sex_var]])

  for (lvl in levels(df[[sex_var]])) {
    d_sub <- df[df[[sex_var]] == lvl, , drop = FALSE]
    # Remove sex from covars for stratified model
    covars_strat <- covars[!covars %in% c(sex_var)]
    res <- run_group_lm(d_sub, outcome_col, exposure, covars_strat, min_n = min_n)
    res$sex_level <- lvl
    results[[lvl]] <- res
  }
  bind_rows(results)
}

# =============================================================================
# HELPER: Run longitudinal mixed model for a single group score
# =============================================================================
run_group_lmm <- function(df, outcome_col, exposure, covars, time_col = "visit_num", id_col = "StudyID", min_n = 50L) {
  needed <- unique(c(id_col, time_col, outcome_col, exposure, covars))
  missing <- needed[!needed %in% names(df)]
  if (length(missing) > 0) {
    return(tibble(
      outcome = outcome_col, exposure = exposure,
      term = NA_character_,
      estimate = NA_real_, std.error = NA_real_,
      p.value = NA_real_, n_obs = NA_integer_, n_ind = NA_integer_,
      note = "missing_variables"
    ))
  }

  d <- df[, needed, drop = FALSE]
  # Convert haven_labelled
  for (v in needed) {
    if (inherits(d[[v]], "haven_labelled")) {
      if (v %in% c(id_col)) {
        d[[v]] <- as.character(as.integer(d[[v]]))
      } else if (v %in% covars) {
        d[[v]] <- as.factor(as.integer(d[[v]]))
      } else {
        d[[v]] <- as.numeric(as.integer(d[[v]]))
      }
    }
  }
  # Cast factors and filter out constant covariates
  valid_covars <- c()
  for (cv in covars) {
    if (is.character(d[[cv]])) d[[cv]] <- as.factor(d[[cv]])
    if (length(unique(d[[cv]])) > 1) {
      valid_covars <- c(valid_covars, cv)
    }
  }
  d[[id_col]] <- as.character(d[[id_col]])

  d <- d[complete.cases(d), , drop = FALSE]
  if (nrow(d) < min_n) {
    return(tibble(
      outcome = outcome_col, exposure = exposure,
      term = NA_character_,
      estimate = NA_real_, std.error = NA_real_,
      p.value = NA_real_, n_obs = nrow(d), n_ind = length(unique(d[[id_col]])),
      note = "too_few_complete_cases"
    ))
  }

  # Formula: outcome ~ exposure * time + covars + (1|id)
  rhs <- c(
    paste0(exposure, " * ", time_col),
    valid_covars
  )
  form <- as.formula(paste0(
    "`", outcome_col, "` ~ ",
    paste(rhs, collapse = " + "),
    " + (1 | ", id_col, ")"
  ))

  fit <- tryCatch(lmerTest::lmer(form, data = d), error = function(e) e)
  if (inherits(fit, "error")) {
    return(tibble(
      outcome = outcome_col, exposure = exposure,
      term = NA_character_,
      estimate = NA_real_, std.error = NA_real_,
      p.value = NA_real_, n_obs = nrow(d), n_ind = length(unique(d[[id_col]])),
      note = paste0("lmm_failed:", conditionMessage(fit))
    ))
  }

  td <- broom.mixed::tidy(fit, effects = "fixed")
  # Keep exposure main effect and interaction terms
  out <- td %>%
    filter(
      grepl(paste0("^", exposure), term) |
        grepl(paste0(":", exposure), term) |
        grepl(paste0(exposure, ":"), term)
    ) %>%
    mutate(
      outcome = outcome_col,
      n_obs = nrow(d),
      n_ind = length(unique(d[[id_col]])),
      note = "ok"
    )

  if (nrow(out) == 0) {
    return(tibble(
      outcome = outcome_col, exposure = exposure,
      term = NA_character_,
      estimate = NA_real_, std.error = NA_real_,
      p.value = NA_real_, n_obs = nrow(d), n_ind = length(unique(d[[id_col]])),
      note = "term_not_estimable"
    ))
  }

  out$exposure <- exposure
  out %>% select(all_of(c("outcome", "exposure", "term", "estimate", "std.error", "p.value", "n_obs", "n_ind", "note")))
}

# =============================================================================
# MAIN
# =============================================================================
main <- function() {
  mae_path <- file.path(root_dir, "Prenatal diet", "data_v2", "MAE.rds")
  out_dir  <- file.path(root_dir, "Prenatal diet", "output", "results", "targeted_v2")
  ensure_dir(out_dir)

  mae <- readRDS(mae_path)
  message("[secondary] MAE loaded")

  # -------------------------------------------------------------------------
  # Exposure definitions (same as primary analysis)
  # -------------------------------------------------------------------------
  exposures_early <- c(
    "MDietaryPatterns_1", "MDIINormalDiet1", "MDIIDensityDiet1", "MIDQ1",
    "MEPercentProDiet1", "MEPercentCHODiet1", "MEPercentFatDiet1", "MEPercentPUFADiet1",
    "MEPercentMUFADiet1", "MEPercentSFADiet1", "MEPercentFAn3Diet1", "MEPercentFAn6Diet1", "MEPercentFiballDiet1"
  )

  exposures_late <- c(
    "MDietaryPatterns_2", "MDIINormalDiet2", "MDIIDensityDiet2", "MIDQ2",
    "MEPercentProDiet2", "MEPercentCHODiet2", "MEPercentFatDiet2", "MEPercentPUFADiet2",
    "MEPercentMUFADiet2", "MEPercentSFADiet2", "MEPercentFAn3Diet2", "MEPercentFAn6Diet2", "MEPercentFiballDiet2"
  )

  strip_window_suffix <- function(x) {
    x <- sub("_[12]$", "", x)
    sub("[12]$", "", x)
  }

  core_covars_planned <- c("Intervention", "CGender", "MEthnicity", "MDise")
  sex_var <- "CGender"

  visits <- c(4, 5, 6, 7)

  # Group score columns
  group_cols <- paste0("grp_", names(GROUP_REGEX))

  # -------------------------------------------------------------------------
  # Build feature classification from the visit_all assay
  # -------------------------------------------------------------------------
  tse_ref <- experiments(mae)[["visit_all"]]
  all_features <- rownames(assay(tse_ref, "mbo"))
  metabolite_features <- pick_metabolite_features(all_features)
  feature_map <- classify_features(metabolite_features)

  n_classified <- sum(!is.na(feature_map$group))
  n_total <- nrow(feature_map)
  message(sprintf("[secondary] Feature classification: %d/%d mapped to %d groups",
                  n_classified, n_total, length(unique(na.omit(feature_map$group)))))

  # =========================================================================
  # CROSS-SECTIONAL: Q4, Q5, Q6 per visit
  # =========================================================================
  results_q4 <- list()
  results_q5 <- list()
  results_q6 <- list()
  results_interaction <- list()
  results_stratified  <- list()
  results_ind_interaction <- list()
  results_ind_stratified  <- list()

  for (v in visits) {
    message(sprintf("[secondary] Processing visit %d ...", v))
    tse_v <- experiments(mae)[[paste0("visit_", v)]]
    meta  <- as.data.frame(colData(tse_v), stringsAsFactors = FALSE)
    mat   <- assay(tse_v, "mbo")

    fasting_var <- paste0("CFastingHoursBloodSample", v)
    covars <- resolve_covars(meta, c(core_covars_planned, fasting_var))$present

    # Compute group scores and transpose individual metabolites for this visit
    feats_v <- intersect(metabolite_features, rownames(mat))
    scores <- compute_group_scores(mat[feats_v, , drop = FALSE], feature_map)
    mat_df <- as.data.frame(t(mat[feats_v, , drop = FALSE]), check.names = FALSE)
    mat_df$sample_id <- rownames(mat_df)
    meta$sample_id <- rownames(meta)
    meta_aug <- left_join(meta, mat_df, by = "sample_id")
    meta_aug <- cbind(meta_aug, scores)

    # --- Q4: Early pregnancy exposures ---
    for (exp_nm in exposures_early) {
      if (!exp_nm %in% names(meta_aug)) next
      for (gc in group_cols) {
        if (!gc %in% names(meta_aug)) next
        res <- run_group_lm(meta_aug, gc, exp_nm, covars)
        res$visit <- v
        res$timing <- "Early"
        res$analysis <- "Q4_group"
        results_q4[[paste(v, exp_nm, gc)]] <- res
      }
    }

    # --- Q5: Late pregnancy exposures ---
    for (exp_nm in exposures_late) {
      if (!exp_nm %in% names(meta_aug)) next
      for (gc in group_cols) {
        if (!gc %in% names(meta_aug)) next
        res <- run_group_lm(meta_aug, gc, exp_nm, covars)
        res$visit <- v
        res$timing <- "Late"
        res$analysis <- "Q5_group"
        results_q5[[paste(v, exp_nm, gc)]] <- res
      }
    }

    # --- Q6: Mutual timing (early + late together) ---
    for (i in seq_along(exposures_early)) {
      exp_e <- exposures_early[[i]]
      exp_l <- exposures_late[[i]]
      if (!(exp_e %in% names(meta_aug)) || !(exp_l %in% names(meta_aug))) next

      for (gc in group_cols) {
        if (!gc %in% names(meta_aug)) next
        vars_needed <- unique(c(gc, exp_e, exp_l, covars))
        d <- meta_aug[, intersect(vars_needed, names(meta_aug)), drop = FALSE]
        # Convert haven_labelled
        for (v_col in names(d)) {
          if (inherits(d[[v_col]], "haven_labelled")) {
            if (v_col %in% covars) {
              d[[v_col]] <- as.factor(as.integer(d[[v_col]]))
            } else {
              d[[v_col]] <- as.numeric(as.integer(d[[v_col]]))
            }
          }
          if (is.character(d[[v_col]]) && v_col %in% covars) d[[v_col]] <- as.factor(d[[v_col]])
        }
        d <- d[complete.cases(d), , drop = FALSE]

        if (nrow(d) < 30) {
          results_q6[[paste(v, exp_e, gc)]] <- tibble(
            outcome = gc, exposure_early = exp_e, exposure_late = exp_l,
            estimate_early = NA_real_, p_early = NA_real_,
            estimate_late = NA_real_, p_late = NA_real_,
            n = nrow(d), visit = v, analysis = "Q6_group",
            note = "too_few_complete_cases"
          )
          next
        }

        covars_clean <- intersect(covars, names(d))
        form <- reformulate(
          termlabels = c(exp_e, exp_l, covars_clean),
          response = gc
        )
        fit <- tryCatch(lm(form, data = d), error = function(e) e)
        if (inherits(fit, "error")) {
          results_q6[[paste(v, exp_e, gc)]] <- tibble(
            outcome = gc, exposure_early = exp_e, exposure_late = exp_l,
            estimate_early = NA_real_, p_early = NA_real_,
            estimate_late = NA_real_, p_late = NA_real_,
            n = nrow(d), visit = v, analysis = "Q6_group",
            note = "lm_failed"
          )
          next
        }

        td <- broom::tidy(fit)
        row_e <- td %>% filter(term == exp_e)
        row_l <- td %>% filter(term == exp_l)

        ci_e <- tryCatch(confint(fit, exp_e, level = 0.95), error = function(e) NULL)
        ci_l <- tryCatch(confint(fit, exp_l, level = 0.95), error = function(e) NULL)

        results_q6[[paste(v, exp_e, gc)]] <- tibble(
          outcome = gc,
          exposure_early = exp_e, exposure_late = exp_l,
          estimate_early = if (nrow(row_e) > 0) row_e$estimate[1] else NA_real_,
          se_early       = if (nrow(row_e) > 0) row_e$std.error[1] else NA_real_,
          conf.low_early = if (!is.null(ci_e)) ci_e[1, 1] else NA_real_,
          conf.high_early = if (!is.null(ci_e)) ci_e[1, 2] else NA_real_,
          p_early        = if (nrow(row_e) > 0) row_e$p.value[1] else NA_real_,
          estimate_late  = if (nrow(row_l) > 0) row_l$estimate[1] else NA_real_,
          se_late        = if (nrow(row_l) > 0) row_l$std.error[1] else NA_real_,
          conf.low_late  = if (!is.null(ci_l)) ci_l[1, 1] else NA_real_,
          conf.high_late = if (!is.null(ci_l)) ci_l[1, 2] else NA_real_,
          p_late         = if (nrow(row_l) > 0) row_l$p.value[1] else NA_real_,
          n = nrow(d),
          visit = v,
          analysis = "Q6_group",
          note = "ok"
        )
      }
    }

    # --- Sex interaction and stratification (Q4/Q5 exposures) ---
    all_exposures <- c(exposures_early, exposures_late)
    for (exp_nm in all_exposures) {
      if (!exp_nm %in% names(meta_aug)) next
      for (gc in group_cols) {
        if (!gc %in% names(meta_aug)) next

        # Interaction test
        int_res <- run_interaction_lm(meta_aug, gc, exp_nm, sex_var, covars)
        int_res$visit <- v
        results_interaction[[paste(v, exp_nm, gc)]] <- int_res

        # If interaction p < 0.05, run stratified models
        if (!is.na(int_res$interaction_p[1]) && int_res$interaction_p[1] < 0.05) {
          strat_res <- run_stratified_lm(meta_aug, gc, exp_nm, sex_var, covars)
          strat_res$visit <- v
          strat_res$analysis <- "sex_stratified"
          results_stratified[[paste(v, exp_nm, gc)]] <- strat_res
        }
      }
    }

    # --- Individual metabolite sex interaction and stratification (Q4/Q5 exposures) ---
    for (exp_nm in all_exposures) {
      if (!exp_nm %in% names(meta_aug)) next
      for (m in metabolite_features) {
        if (!m %in% names(meta_aug)) next

        # Interaction test at metabolite level
        int_res <- run_interaction_lm(meta_aug, m, exp_nm, sex_var, covars, min_n = 30L)
        if (!is.null(int_res)) {
          int_res$visit <- v
          int_res$analysis <- "ind_sex_interaction"
          results_ind_interaction[[paste(v, exp_nm, m)]] <- int_res

          # If interaction p < 0.05, run stratified models
          if (!is.na(int_res$interaction_p[1]) && int_res$interaction_p[1] < 0.05) {
            strat_res <- run_stratified_lm(meta_aug, m, exp_nm, sex_var, covars, min_n = 15L)
            if (!is.null(strat_res) && nrow(strat_res) > 0) {
              strat_res$visit <- v
              strat_res$analysis <- "ind_sex_stratified"
              results_ind_stratified[[paste(v, exp_nm, m)]] <- strat_res
            }
          }
        }
      }
    }
  }

  message("[secondary] Cross-sectional analyses complete")

  # =========================================================================
  # LONGITUDINAL: Q7 mixed models
  # =========================================================================
  message("[secondary] Building longitudinal dataset ...")

  # Extract long-format data with group scores and individual metabolites
  extract_long_groups <- function(mae_obj, metabolite_features, feature_map) {
    ex <- experiments(mae_obj)
    out <- lapply(names(ex), function(vn) {
      if (!grepl("^visit_[0-9]+$", vn)) return(NULL)
      tse <- ex[[vn]]
      meta <- as.data.frame(colData(tse), stringsAsFactors = FALSE)
      mat  <- assay(tse, "mbo")
      feats_avail <- intersect(metabolite_features, rownames(mat))
      scores <- compute_group_scores(mat[feats_avail, , drop = FALSE], feature_map)
      
      mat_df <- as.data.frame(t(mat[feats_avail, , drop = FALSE]), check.names = FALSE)
      mat_df$sample_id <- rownames(mat_df)
      meta$sample_id <- rownames(meta)
      meta_full <- left_join(meta, mat_df, by = "sample_id")
      meta_full <- cbind(meta_full, scores)
      
      vnum <- as.integer(gsub("visit_", "", vn))
      fasting_var <- paste0("CFastingHoursBloodSample", vnum)
      meta_full$visit_num <- vnum
      meta_full$CFastingHoursBloodSample_it <- if (fasting_var %in% names(meta_full)) {
        suppressWarnings(as.numeric(meta_full[[fasting_var]]))
      } else {
        NA_real_
      }
      meta_full$StudyID <- as.character(meta_full$StudyID)
      meta_full
    })
    bind_rows(out)
  }

  df_long <- extract_long_groups(mae, metabolite_features, feature_map)
  covars_long <- resolve_covars(df_long, c(core_covars_planned, "CFastingHoursBloodSample_it"))

  results_q7 <- list()
  all_q7_exposures <- c(exposures_early, exposures_late)

  for (exp_nm in all_q7_exposures) {
    if (!exp_nm %in% names(df_long)) next
    timing <- if (exp_nm %in% exposures_early) "Early" else "Late"

    for (gc in group_cols) {
      if (!gc %in% names(df_long)) next
      res <- run_group_lmm(df_long, gc, exp_nm, covars_long$present)
      res$timing <- timing
      res$analysis <- "Q7_group"
      results_q7[[paste(exp_nm, gc)]] <- res
    }
  }

  message("[secondary] Longitudinal mixed models complete")

  # --- Q7 sex interaction (longitudinal) ---
  results_q7_interaction <- list()
  results_q7_stratified  <- list()

  for (exp_nm in all_q7_exposures) {
    if (!exp_nm %in% names(df_long)) next

    for (gc in group_cols) {
      if (!gc %in% names(df_long)) next

      # Interaction model: outcome ~ exposure * visit_num * CGender + covars + (1|StudyID)
      # Simplified: test exposure:CGender interaction
      d <- df_long
      needed <- unique(c("StudyID", "visit_num", gc, exp_nm, sex_var, covars_long$present))
      missing <- needed[!needed %in% names(d)]
      if (length(missing) > 0) next

      for (v_col in needed) {
        if (inherits(d[[v_col]], "haven_labelled")) {
          if (v_col == sex_var || v_col %in% covars_long$present) {
            d[[v_col]] <- as.factor(as.integer(d[[v_col]]))
          } else if (v_col != "StudyID") {
            d[[v_col]] <- as.numeric(as.integer(d[[v_col]]))
          }
        }
        if (is.character(d[[v_col]]) && v_col %in% c(covars_long$present, sex_var)) {
          d[[v_col]] <- as.factor(d[[v_col]])
        }
      }
      d$StudyID <- as.character(d$StudyID)
      d <- d[, needed, drop = FALSE]
      d <- d[complete.cases(d), , drop = FALSE]

      if (nrow(d) < 50 || length(unique(d[[sex_var]])) < 2) next

      covars_no_sex <- covars_long$present[!covars_long$present %in% c(sex_var)]
      rhs <- c(
        paste0(exp_nm, " * visit_num"),
        paste0(exp_nm, " * ", sex_var),
        covars_no_sex
      )
      form <- as.formula(paste0(
        "`", gc, "` ~ ", paste(rhs, collapse = " + "),
        " + (1 | StudyID)"
      ))
      fit <- tryCatch(lmerTest::lmer(form, data = d), error = function(e) e)

      if (inherits(fit, "error")) next

      td <- broom.mixed::tidy(fit, effects = "fixed")
      int_row <- td %>% filter(
        grepl(paste0(exp_nm, ":"), term) & grepl(sex_var, term) |
        grepl(paste0(":", exp_nm), term) & grepl(sex_var, term)
      )

      if (nrow(int_row) > 0) {
        results_q7_interaction[[paste(exp_nm, gc)]] <- tibble(
          outcome = gc, exposure = exp_nm,
          interaction_term = int_row$term[1],
          interaction_estimate = int_row$estimate[1],
          interaction_se = int_row$std.error[1],
          interaction_p = int_row$p.value[1],
          n_obs = nrow(d), n_ind = length(unique(d$StudyID)),
          analysis = "Q7_sex_interaction",
          note = "ok"
        )

        # Stratified if significant
        if (!is.na(int_row$p.value[1]) && int_row$p.value[1] < 0.05) {
          for (lvl in levels(d[[sex_var]])) {
            d_sub <- d[d[[sex_var]] == lvl, , drop = FALSE]
            res_s <- run_group_lmm(d_sub, gc, exp_nm, covars_no_sex)
            res_s$sex_level <- lvl
            res_s$analysis <- "Q7_sex_stratified"
            results_q7_stratified[[paste(exp_nm, gc, lvl)]] <- res_s
          }
        }
      }
    }
  }

  message("[secondary] Sex-specific longitudinal analyses complete")

  # --- Q7 individual metabolite sex interaction (longitudinal) ---
  results_q7_ind_interaction <- list()
  results_q7_ind_stratified  <- list()

  for (exp_nm in all_q7_exposures) {
    if (!exp_nm %in% names(df_long)) next

    for (m in metabolite_features) {
      if (!m %in% names(df_long)) next

      d <- df_long
      needed <- unique(c("StudyID", "visit_num", m, exp_nm, sex_var, covars_long$present))
      missing <- needed[!needed %in% names(d)]
      if (length(missing) > 0) next

      for (v_col in needed) {
        if (inherits(d[[v_col]], "haven_labelled")) {
          if (v_col == sex_var || v_col %in% covars_long$present) {
            d[[v_col]] <- as.factor(as.integer(d[[v_col]]))
          } else if (v_col != "StudyID") {
            d[[v_col]] <- as.numeric(as.integer(d[[v_col]]))
          }
        }
        if (is.character(d[[v_col]]) && v_col %in% c(covars_long$present, sex_var)) {
          d[[v_col]] <- as.factor(d[[v_col]])
        }
      }
      d$StudyID <- as.character(d$StudyID)
      d <- d[, needed, drop = FALSE]
      d <- d[complete.cases(d), , drop = FALSE]

      if (nrow(d) < 50 || length(unique(d[[sex_var]])) < 2) next

      # Standardize individual metabolite
      d[[m]] <- as.numeric(scale(as.numeric(d[[m]])))

      covars_no_sex <- covars_long$present[!covars_long$present %in% c(sex_var)]
      rhs <- c(
        paste0(exp_nm, " * visit_num"),
        paste0(exp_nm, " * ", sex_var),
        covars_no_sex
      )
      form <- as.formula(paste0(
        "`", m, "` ~ ", paste(rhs, collapse = " + "),
        " + (1 | StudyID)"
      ))
      fit <- tryCatch(lmerTest::lmer(form, data = d), error = function(e) e)

      if (inherits(fit, "error")) next

      td <- broom.mixed::tidy(fit, effects = "fixed")
      int_row <- td %>% filter(
        grepl(paste0(exp_nm, ":"), term) & grepl(sex_var, term) |
        grepl(paste0(":", exp_nm), term) & grepl(sex_var, term)
      )

      if (nrow(int_row) > 0) {
        results_q7_ind_interaction[[paste(exp_nm, m)]] <- tibble(
          outcome = m, exposure = exp_nm,
          interaction_term = int_row$term[1],
          interaction_estimate = int_row$estimate[1],
          interaction_se = int_row$std.error[1],
          interaction_p = int_row$p.value[1],
          n_obs = nrow(d), n_ind = length(unique(d$StudyID)),
          analysis = "Q7_ind_sex_interaction",
          note = "ok"
        )

        # Stratified if significant
        if (!is.na(int_row$p.value[1]) && int_row$p.value[1] < 0.05) {
          for (lvl in levels(d[[sex_var]])) {
            d_sub <- d[d[[sex_var]] == lvl, , drop = FALSE]
            res_s <- run_group_lmm(d_sub, m, exp_nm, covars_no_sex)
            if (!is.null(res_s) && nrow(res_s) > 0) {
              res_s$sex_level <- lvl
              res_s$analysis <- "Q7_ind_sex_stratified"
              results_q7_ind_stratified[[paste(exp_nm, m, lvl)]] <- res_s
            }
          }
        }
      }
    }
  }

  message("[secondary] Sex-specific longitudinal individual analyses complete")

  # =========================================================================
  # ASSEMBLE AND SAVE
  # =========================================================================
  q4_df <- bind_rows(results_q4)
  q5_df <- bind_rows(results_q5)
  q6_df <- bind_rows(results_q6)
  q7_df <- bind_rows(results_q7)
  interaction_df     <- bind_rows(results_interaction)
  stratified_df      <- bind_rows(results_stratified)
  q7_interaction_df  <- bind_rows(results_q7_interaction)
  q7_stratified_df   <- bind_rows(results_q7_stratified)
  
  ind_interaction_df    <- bind_rows(results_ind_interaction)
  ind_stratified_df     <- bind_rows(results_ind_stratified)
  q7_ind_interaction_df <- bind_rows(results_q7_ind_interaction)
  q7_ind_stratified_df  <- bind_rows(results_q7_ind_stratified)

  # Apply BH-FDR within each analysis family
  apply_fdr <- function(df, p_col = "p.value") {
    if (nrow(df) == 0 || !p_col %in% names(df)) return(df)
    ok <- !is.na(df[[p_col]])
    df$q.value <- NA_real_
    if (any(ok)) df$q.value[ok] <- p.adjust(df[[p_col]][ok], method = "BH")
    df
  }

  q4_df <- apply_fdr(q4_df)
  q5_df <- apply_fdr(q5_df)
  
  ind_interaction_df    <- apply_fdr(ind_interaction_df, "interaction_p")
  q7_ind_interaction_df <- apply_fdr(q7_ind_interaction_df, "interaction_p")

  result <- list(
    q4_group_scores        = q4_df,
    q5_group_scores        = q5_df,
    q6_group_scores        = q6_df,
    q7_group_scores        = q7_df,
    sex_interaction        = interaction_df,
    sex_stratified         = stratified_df,
    q7_sex_interaction     = q7_interaction_df,
    q7_sex_stratified      = q7_stratified_df,
    ind_sex_interaction    = ind_interaction_df,
    ind_sex_stratified     = ind_stratified_df,
    q7_ind_sex_interaction = q7_ind_interaction_df,
    q7_ind_sex_stratified  = q7_ind_stratified_df,
    feature_map            = feature_map,
    group_labels           = GROUP_LABELS,
    group_cols             = group_cols
  )

  out_path <- file.path(out_dir, "prenatal_diet_secondary_results.rds")
  saveRDS(result, out_path)
  message("[secondary] Saved: ", out_path)

  # Summary
  message(sprintf("[secondary] Q4 results: %d rows", nrow(q4_df)))
  message(sprintf("[secondary] Q5 results: %d rows", nrow(q5_df)))
  message(sprintf("[secondary] Q6 results: %d rows", nrow(q6_df)))
  message(sprintf("[secondary] Q7 results: %d rows", nrow(q7_df)))
  message(sprintf("[secondary] Interaction tests: %d rows", nrow(interaction_df)))
  message(sprintf("[secondary] Stratified models: %d rows", nrow(stratified_df)))
  message(sprintf("[secondary] Q7 interaction: %d rows", nrow(q7_interaction_df)))
  message(sprintf("[secondary] Q7 stratified: %d rows", nrow(q7_stratified_df)))
  message(sprintf("[secondary] Ind interaction: %d rows", nrow(ind_interaction_df)))
  message(sprintf("[secondary] Ind stratified: %d rows", nrow(ind_stratified_df)))
  message(sprintf("[secondary] Q7 ind interaction: %d rows", nrow(q7_ind_interaction_df)))
  message(sprintf("[secondary] Q7 ind stratified: %d rows", nrow(q7_ind_stratified_df)))

  invisible(out_path)
}

if (identical(environment(), globalenv())) {
  main()
}
