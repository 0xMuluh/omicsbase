#!/usr/bin/env Rscript
# =============================================================================
# Neurocognition Primary and Sensitivity Analysis: Child Metabolites & Neurocognition
# =============================================================================
# This script performs association analyses for Neurocognition's child metabolomics
# (measured at 6 months, 1 year, and 2 years) and 2-year neurocognitive outcomes.
#
# Covariates:
#   Primary initial: CGender, Bfdurationm, InterventionGroup, MprepBMI, MUniEdu, MPRSmoke, MPrimipara
#   Sensitivity: Core Covariates + CAgePsychologist6 (Bayley) or CAgePhysiotherapist6 (HINE)
#
# Outcomes:
#   Continuous (Bayley-III & HINE global score): OLS Linear Regression
#   Binary (HINE optimality): Logistic Regression (excluding preterm)
#
# Output: output/results/neurocognition_study_results.rds
# =============================================================================

suppressPackageStartupMessages({
  library(MultiAssayExperiment)
  library(SummarizedExperiment)
  library(dplyr)
  library(tidyr)
  library(broom)
})

args_all <- commandArgs(trailingOnly = FALSE)
file_arg <- args_all[grepl("^--file=", args_all)]
script_path <- if (length(file_arg) > 0) {
  normalizePath(sub("^--file=", "", file_arg[[1]]), mustWork = TRUE)
} else {
  normalizePath("scripts/v2/neurocognition_analysis_v2.R", mustWork = TRUE)
}

root_dir <- normalizePath(file.path(dirname(script_path), "..", ".."), mustWork = TRUE)
source(file.path(dirname(script_path), "helpers_v2.R"))
source(file.path(root_dir, "scripts", "metabolomics_common.R"))

to_chr <- function(x) {
  if (inherits(x, "haven_labelled")) {
    x <- haven::as_factor(x)
  }
  as.character(x)
}

# =============================================================================
# DEFINITIONS
# =============================================================================

# Continuous outcomes
bayley_outcomes <- c(
  "CCognition_indexpoints6",
  "CLanguage_indexpoints6",
  "CExpressivelanguage_standardpoints6",
  "CReceptivelanguage_standardpoints6",
  "CMotor_indexpoints6",
  "CFinemotor_standardpoints6",
  "CGrossmotor_standardpoints6"
)
hine_outcome <- "Hammersmith6"
continuous_outcomes <- c(bayley_outcomes, hine_outcome)

# Binary outcomes
binary_outcomes <- "HINE_optimal"
all_outcomes <- c(continuous_outcomes, binary_outcomes)

# Core Covariates (User-defined starting covariates)
core_covars_base <- c(
  "CGender",
  "Bfdurationm",
  "InterventionGroup",
  "MprepBMI",
  "MUniEdu",
  "MPRSmoke",
  "MPrimipara"
)

# psychologist / physiotherapist visit ages (sensitivity variables)
sensitivity_map <- list(
  CCognition_indexpoints6 = c("CAgePsychologist6", "CAgePhysiotherapist6"),
  CLanguage_indexpoints6 = c("CAgePsychologist6", "CAgePhysiotherapist6"),
  CExpressivelanguage_standardpoints6 = c("CAgePsychologist6", "CAgePhysiotherapist6"),
  CReceptivelanguage_standardpoints6 = c("CAgePsychologist6", "CAgePhysiotherapist6"),
  CMotor_indexpoints6 = c("CAgePsychologist6", "CAgePhysiotherapist6"),
  CFinemotor_standardpoints6 = c("CAgePsychologist6", "CAgePhysiotherapist6"),
  CGrossmotor_standardpoints6 = c("CAgePsychologist6", "CAgePhysiotherapist6"),
  Hammersmith6 = "CAgePhysiotherapist6",
  HINE_optimal = "CAgePhysiotherapist6"
)

# Helper to check variable types and cast to factor if needed
make_model_row <- function(outcome_col,
                           exposure,
                           analysis_type,
                           n,
                           estimate = NA_real_,
                           std.error = NA_real_,
                           conf.low = NA_real_,
                           conf.high = NA_real_,
                           p.value = NA_real_,
                           note = "ok") {
  data.frame(
    outcome = outcome_col,
    exposure = exposure,
    analysis_type = analysis_type,
    n = suppressWarnings(as.integer(n[1])),
    estimate = estimate,
    std.error = std.error,
    conf.low = conf.low,
    conf.high = conf.high,
    p.value = p.value,
    note = note,
    stringsAsFactors = FALSE
  )
}

clean_regression_data <- function(df, outcome_col, exposure, covars) {
  needed_cols <- unique(c(outcome_col, exposure, covars))
  missing_cols <- needed_cols[!needed_cols %in% names(df)]
  if (length(missing_cols) > 0) {
    return(list(
      ok = FALSE,
      data = NULL,
      n_complete = NA_integer_,
      note = paste0("missing_variables: ", paste(missing_cols, collapse = "|"))
    ))
  }
  
  d <- df[, needed_cols, drop = FALSE]
  d <- d[complete.cases(d), , drop = FALSE]
  if (nrow(d) < 30) {
    return(list(
      ok = FALSE,
      data = NULL,
      n_complete = nrow(d),
      note = "too_few_complete_cases"
    ))
  }
  
  for (cv in covars) {
    if (inherits(d[[cv]], "haven_labelled")) {
      d[[cv]] <- as.factor(as.integer(d[[cv]]))
    } else if (is.character(d[[cv]])) {
      d[[cv]] <- as.factor(d[[cv]])
    } else if (cv %in% c("CGender", "InterventionGroup", "MUniEdu", "MPRSmoke", "MPrimipara")) {
      d[[cv]] <- as.factor(d[[cv]])
    } else {
      d[[cv]] <- as.numeric(d[[cv]])
    }

    if (is.factor(d[[cv]]) && nlevels(droplevels(d[[cv]])) < 2) {
      return(list(
        ok = FALSE,
        data = NULL,
        n_complete = nrow(d),
        note = paste0("single_level_covariate: ", cv)
      ))
    }
  }
  
  d[[exposure]] <- as.numeric(d[[exposure]])
  if (all(is.na(d[[exposure]])) || sd(d[[exposure]], na.rm = TRUE) == 0) {
    return(list(
      ok = FALSE,
      data = NULL,
      n_complete = nrow(d),
      note = "invalid_exposure_scale"
    ))
  }
  
  if (outcome_col == "HINE_optimal") {
    d$y_bin <- ifelse(to_chr(d[[outcome_col]]) == "Suboptimal", 1L, 0L)
    if (length(unique(d$y_bin)) < 2) {
      return(list(
        ok = FALSE,
        data = NULL,
        n_complete = nrow(d),
        note = "constant_binary_outcome"
      ))
    }
  } else {
    d$y_cont <- as.numeric(d[[outcome_col]])
    if (sd(d$y_cont, na.rm = TRUE) == 0) {
      return(list(
        ok = FALSE,
        data = NULL,
        n_complete = nrow(d),
        note = "constant_continuous_outcome"
      ))
    }
  }
  
  list(ok = TRUE, data = d, n_complete = nrow(d), note = "ok")
}

capture_model_warnings <- function(expr, warning_env) {
  withCallingHandlers(
    expr,
    warning = function(w) {
      warning_env[["messages"]] <- c(warning_env[["messages"]], conditionMessage(w))
      invokeRestart("muffleWarning")
    }
  )
}

model_note <- function(warning_env) {
  messages <- unique(warning_env[["messages"]])
  if (length(messages) == 0) {
    "ok"
  } else {
    paste0("warnings: ", paste(messages, collapse = " | "))
  }
}

# =============================================================================
# FIT SINGLE MODEL
# =============================================================================
fit_association_model <- function(df, outcome_col, exposure, covars, model_type = c("primary", "sensitivity")) {
  model_type <- match.arg(model_type)
  
  current_covars <- covars
  if (model_type == "sensitivity") {
    sens_vars <- sensitivity_map[[outcome_col]]
    if (!is.null(sens_vars)) {
      current_covars <- unique(c(current_covars, sens_vars))
    }
  }
  
  prep <- clean_regression_data(df, outcome_col, exposure, current_covars)
  if (!isTRUE(prep$ok)) {
    return(make_model_row(outcome_col, exposure, model_type, prep$n_complete, note = prep$note))
  }
  d <- prep$data
  exposure_term <- paste0("`", exposure, "`")
  warning_env <- new.env(parent = emptyenv())
  warning_env[["messages"]] <- character()
  
  if (outcome_col == "HINE_optimal") {
    form <- reformulate(c(exposure_term, current_covars), response = "y_bin")
    fit_error <- NULL
    fit <- tryCatch(
      capture_model_warnings(
        glm(form, data = d, family = binomial(link = "logit")),
        warning_env
      ),
      error = function(e) {
        fit_error <<- conditionMessage(e)
        NULL
      }
    )
    if (is.null(fit)) {
      return(make_model_row(outcome_col, exposure, model_type, nrow(d), note = paste0("glm_failed: ", fit_error)))
    }
    
    tidy_error <- NULL
    td <- tryCatch(
      capture_model_warnings(
        broom::tidy(fit, conf.int = TRUE, exponentiate = TRUE),
        warning_env
      ),
      error = function(e) {
        tidy_error <<- conditionMessage(e)
        NULL
      }
    )
    if (is.null(td)) {
      return(make_model_row(outcome_col, exposure, model_type, nrow(d), note = paste0("tidy_failed: ", tidy_error)))
    }
    
    coef_row <- td %>% filter(term == exposure | term == exposure_term | gsub("`", "", term) == exposure)
    if (nrow(coef_row) == 0) {
      return(make_model_row(outcome_col, exposure, model_type, nrow(d), note = "exposure_term_not_estimable"))
    }
    
    make_model_row(
      outcome_col = outcome_col,
      exposure = exposure,
      analysis_type = model_type,
      n = nrow(d),
      estimate = coef_row$estimate[1],
      std.error = coef_row$std.error[1],
      conf.low = coef_row$conf.low[1],
      conf.high = coef_row$conf.high[1],
      p.value = coef_row$p.value[1],
      note = model_note(warning_env)
    )
  } else {
    form <- reformulate(c(exposure_term, current_covars), response = "y_cont")
    fit_error <- NULL
    fit <- tryCatch(
      capture_model_warnings(
        lm(form, data = d),
        warning_env
      ),
      error = function(e) {
        fit_error <<- conditionMessage(e)
        NULL
      }
    )
    if (is.null(fit)) {
      return(make_model_row(outcome_col, exposure, model_type, nrow(d), note = paste0("lm_failed: ", fit_error)))
    }
    
    tidy_error <- NULL
    td <- tryCatch(
      capture_model_warnings(
        broom::tidy(fit, conf.int = TRUE),
        warning_env
      ),
      error = function(e) {
        tidy_error <<- conditionMessage(e)
        NULL
      }
    )
    if (is.null(td)) {
      return(make_model_row(outcome_col, exposure, model_type, nrow(d), note = paste0("tidy_failed: ", tidy_error)))
    }
    
    coef_row <- td %>% filter(term == exposure | term == exposure_term | gsub("`", "", term) == exposure)
    if (nrow(coef_row) == 0) {
      return(make_model_row(outcome_col, exposure, model_type, nrow(d), note = "exposure_term_not_estimable"))
    }
    
    make_model_row(
      outcome_col = outcome_col,
      exposure = exposure,
      analysis_type = model_type,
      n = nrow(d),
      estimate = coef_row$estimate[1],
      std.error = coef_row$std.error[1],
      conf.low = coef_row$conf.low[1],
      conf.high = coef_row$conf.high[1],
      p.value = coef_row$p.value[1],
      note = model_note(warning_env)
    )
  }
}

# =============================================================================
# MAIN ANALYSIS RUNNER
# =============================================================================
main <- function() {
  mae_path <- file.path(root_dir, "Neurocognition", "data", "MAE.rds")
  out_dir <- file.path(root_dir, "Neurocognition", "output", "results")
  ensure_dir(out_dir)
  
  mae <- readRDS(mae_path)
  message("[neurocognition] Loaded MAE dataset")
  
  # Retrieve metadata
  metabolite_cols <- rownames(assay(experiments(mae)[["visit_all"]], "mbo"))
  
  # Align and prepare data (extract visits 4, 5, 6)
  visits <- c(4L, 5L, 6L)
  
  results_all <- list()
  
  for (v in visits) {
    vn <- paste0("visit_", v)
    tse <- experiments(mae)[[vn]]
    
    # Use the user-specified core covariates only
    current_core_covars <- core_covars_base
    
    # Get colData
    cd <- as.data.frame(colData(tse), stringsAsFactors = FALSE)
    cd$sample_id <- rownames(cd)
    
    # Exclude premature babies from HINE optimal outcomes in primary colData
    # HINE_optimal is derived in make_mae.R and already sets preterm to NA.
    
    # Get metabolites assay
    mat <- as.data.frame(t(assay(tse, "mbo")), check.names = FALSE)
    mat$sample_id <- rownames(mat)
    
    # Merge colData with metabolites
    df_visit <- left_join(cd, mat, by = "sample_id")
    
    # Log-z transform metabolite exposures using the MAE visit label from colData
    df_transformed <- log_z_by_visit(df_visit, metabolite_cols = metabolite_cols, visit_col = "Visit")
    
    message(sprintf("[neurocognition] Visit %d: Running regressions for %d metabolites...", v, length(metabolite_cols)))
    
    # Run primary models
    primary_list <- list()
    sensitivity_list <- list()
    
    for (out_nm in all_outcomes) {
      # Primary models
      primary_rows <- lapply(metabolite_cols, function(m) {
        fit_association_model(df_transformed, out_nm, m, current_core_covars, "primary")
      })
      primary_df <- bind_rows(primary_rows)
      if (nrow(primary_df) != length(metabolite_cols)) {
        stop("Primary model row count mismatch for visit ", v, ", outcome ", out_nm, ".")
      }
      primary_df[["visit"]] <- v
      primary_list[[out_nm]] <- primary_df
      
      # Sensitivity models
      sens_rows <- lapply(metabolite_cols, function(m) {
        fit_association_model(df_transformed, out_nm, m, current_core_covars, "sensitivity")
      })
      sens_df <- bind_rows(sens_rows)
      if (nrow(sens_df) != length(metabolite_cols)) {
        stop("Sensitivity model row count mismatch for visit ", v, ", outcome ", out_nm, ".")
      }
      sens_df[["visit"]] <- v
      sensitivity_list[[out_nm]] <- sens_df
    }
    
    results_all[[vn]] <- list(
      primary = bind_rows(primary_list),
      sensitivity = bind_rows(sensitivity_list)
    )
  }
  
  # Combine results across visits
  primary_results <- bind_rows(lapply(results_all, function(x) x[["primary"]]))
  sensitivity_results <- bind_rows(lapply(results_all, function(x) x[["sensitivity"]]))
  expected_rows <- length(visits) * length(all_outcomes) * length(metabolite_cols)
  if (nrow(primary_results) != expected_rows) {
    stop("Primary combined row count mismatch: expected ", expected_rows, ", got ", nrow(primary_results), ".")
  }
  if (nrow(sensitivity_results) != expected_rows) {
    stop("Sensitivity combined row count mismatch: expected ", expected_rows, ", got ", nrow(sensitivity_results), ".")
  }
  
  # Multiple comparison correction (Benjamini-Hochberg)
  # Done within outcome, visit, and analysis family
  adjust_fdr <- function(res_df) {
    if (nrow(res_df) == 0) stop("Cannot adjust FDR on an empty result table.")
    res_df %>%
      group_by(outcome, visit, analysis_type) %>%
      mutate(q.value = p.adjust(p.value, method = "BH")) %>%
      ungroup() %>%
      arrange(outcome, visit, q.value, p.value)
  }
  
  primary_results <- adjust_fdr(primary_results)
  sensitivity_results <- adjust_fdr(sensitivity_results)
  
  # Save RDS output
  output_file <- file.path(out_dir, "neurocognition_study_results.rds")
  saveRDS(
    list(
      primary_results = primary_results,
      sensitivity_results = sensitivity_results,
      covariates_used = core_covars_base,
      metabolites = metabolite_cols,
      timestamp = format(Sys.time(), "%Y-%m-%dT%H:%M:%S%z")
    ),
    output_file
  )
  
  message("[neurocognition] Saved: ", output_file)
  message(sprintf("[neurocognition] Total Primary rows: %d", nrow(primary_results)))
  message(sprintf("[neurocognition] Total Sensitivity rows: %d", nrow(sensitivity_results)))
}

if (identical(environment(), globalenv())) {
  main()
}
