#!/usr/bin/env Rscript
# Secondary sensitivity analyses.
# Methods: NMR group scores, mixed-design audit, Tobit HINE sensitivity,
# Firth HINE sensitivity, joint prediction, MSEA, M_eff, permutation FDR,
# and random-forest variable importance.

suppressPackageStartupMessages({
  library(MultiAssayExperiment)
  library(SummarizedExperiment)
  library(dplyr)
  library(tidyr)
  library(broom)
  library(AER)
  library(logistf)
  library(glmnet)
  library(pls)
})

args_all <- commandArgs(trailingOnly = FALSE)
file_arg <- args_all[grepl("^--file=", args_all)]
script_path <- if (length(file_arg) > 0) {
  normalizePath(sub("^--file=", "", file_arg[[1]]), mustWork = TRUE)
} else {
  normalizePath("scripts/v2/neurocognition_secondary_analysis.R", mustWork = TRUE)
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

calculate_permutation_qvalues <- function(p_obs, p_null_matrix) {
  M <- length(p_obs)
  B <- nrow(p_null_matrix)
  
  ord <- order(p_obs)
  p_obs_sorted <- p_obs[ord]
  
  obs_counts <- sapply(p_obs_sorted, function(p) sum(p_obs_sorted <= p))
  
  null_p_all <- as.numeric(p_null_matrix)
  null_p_sorted <- sort(null_p_all)
  
  null_counts <- sapply(p_obs_sorted, function(p) sum(null_p_sorted <= p))
  expected_counts <- null_counts / B
  
  efdr <- expected_counts / pmax(1, obs_counts)
  efdr <- pmin(1, efdr)
  
  q_val_sorted <- numeric(M)
  current_min <- 1.0
  for(i in rev(seq_len(M))) {
    current_min <- min(current_min, efdr[i])
    q_val_sorted[i] <- current_min
  }
  
  q_val <- numeric(M)
  q_val[ord] <- q_val_sorted
  names(q_val) <- names(p_obs)
  q_val
}

fit_perm_fdr_one_group <- function(df, outcome_col, metabolite_cols, covars, B = 1000) {
  needed_cols <- unique(c(outcome_col, metabolite_cols, covars))
  missing_cols <- needed_cols[!needed_cols %in% names(df)]
  if (length(missing_cols) > 0) {
    return(data.frame(
      outcome = outcome_col,
      exposure = metabolite_cols,
      estimate = NA_real_,
      p.value = NA_real_,
      perm_q_value = NA_real_,
      n = NA_integer_,
      n_input = nrow(df),
      n_complete = NA_integer_,
      permutations = B,
      note = paste0("missing_variables: ", paste(missing_cols, collapse = "|")),
      stringsAsFactors = FALSE
    ))
  }
  
  d <- df[, needed_cols, drop = FALSE]
  d <- d[complete.cases(d), , drop = FALSE]
  if (nrow(d) < 30) {
    return(data.frame(
      outcome = outcome_col,
      exposure = metabolite_cols,
      estimate = NA_real_,
      p.value = NA_real_,
      perm_q_value = NA_real_,
      n = nrow(d),
      n_input = nrow(df),
      n_complete = nrow(d),
      permutations = B,
      note = "too_few_complete_cases",
      stringsAsFactors = FALSE
    ))
  }
  
  for (cv in covars) {
    if (cv %in% factor_cols) {
      d[[cv]] <- as.factor(d[[cv]])
    } else {
      d[[cv]] <- as.numeric(d[[cv]])
    }
  }
  
  y <- as.numeric(d[[outcome_col]])
  Z <- model.matrix(as.formula(paste("~", paste(covars, collapse = " + "))), data = d)
  X_met <- as.matrix(d[, metabolite_cols])
  X_met <- apply(X_met, 2, scale)
  
  n <- nrow(d)
  k <- ncol(Z) + 1
  
  qr_Z <- qr(Z)
  y_res <- qr.resid(qr_Z, y)
  X_res <- qr.resid(qr_Z, X_met)
  
  den_beta <- colSums(X_res^2)
  
  p_obs <- numeric(ncol(X_met))
  names(p_obs) <- metabolite_cols
  
  for (i in seq_along(metabolite_cols)) {
    x_r <- X_res[, i]
    beta <- sum(y_res * x_r) / den_beta[i]
    rss <- sum((y_res - beta * x_r)^2)
    s2 <- rss / (n - k)
    se <- sqrt(s2 / den_beta[i])
    t_stat <- beta / se
    p_obs[i] <- 2 * pt(-abs(t_stat), df = n - k)
  }
  
  set.seed(42)
  p_null <- matrix(NA, nrow = B, ncol = ncol(X_met))
  for (b in seq_len(B)) {
    y_res_perm <- sample(y_res)
    for (i in seq_along(metabolite_cols)) {
      x_r <- X_res[, i]
      beta <- sum(y_res_perm * x_r) / den_beta[i]
      rss <- sum((y_res_perm - beta * x_r)^2)
      s2 <- rss / (n - k)
      se <- sqrt(s2 / den_beta[i])
      t_stat <- beta / se
      p_null[b, i] <- 2 * pt(-abs(t_stat), df = n - k)
    }
  }
  
  perm_q <- calculate_permutation_qvalues(p_obs, p_null)
  
  data.frame(
    outcome = outcome_col,
    exposure = metabolite_cols,
    estimate = vapply(seq_along(metabolite_cols), function(i) {
      sum(y_res * X_res[, i]) / den_beta[i]
    }, numeric(1)),
    p.value = p_obs,
    perm_q_value = perm_q,
    n = n,
    n_input = nrow(df),
    n_complete = n,
    permutations = B,
    note = "ok",
    stringsAsFactors = FALSE
  )
}

fit_rf_one_group <- function(df, outcome_col, metabolite_cols, covars) {
  needed_cols <- unique(c(outcome_col, metabolite_cols, covars))
  missing_cols <- needed_cols[!needed_cols %in% names(df)]
  if (length(missing_cols) > 0) {
    return(data.frame(
      outcome = outcome_col,
      exposure = metabolite_cols,
      importance = NA_real_,
      oob_r2 = NA_real_,
      n = NA_integer_,
      n_input = nrow(df),
      n_complete = NA_integer_,
      note = paste0("missing_variables: ", paste(missing_cols, collapse = "|")),
      stringsAsFactors = FALSE
    ))
  }
  
  d <- df[, needed_cols, drop = FALSE]
  d <- d[complete.cases(d), , drop = FALSE]
  if (nrow(d) < 30) {
    return(data.frame(
      outcome = outcome_col,
      exposure = metabolite_cols,
      importance = NA_real_,
      oob_r2 = NA_real_,
      n = nrow(d),
      n_input = nrow(df),
      n_complete = nrow(d),
      note = "too_few_complete_cases",
      stringsAsFactors = FALSE
    ))
  }
  
  for (cv in covars) {
    if (cv %in% factor_cols) {
      d[[cv]] <- as.factor(d[[cv]])
    } else {
      d[[cv]] <- as.numeric(d[[cv]])
    }
  }
  
  y <- as.numeric(d[[outcome_col]])
  Z <- model.matrix(as.formula(paste("~", paste(covars, collapse = " + "))), data = d)
  X_met <- as.matrix(d[, metabolite_cols])
  X_met <- apply(X_met, 2, scale)
  
  qr_Z <- qr(Z)
  y_res <- qr.resid(qr_Z, y)
  X_res <- qr.resid(qr_Z, X_met)
  colnames(X_res) <- metabolite_cols
  
  fit_rf <- tryCatch(
    ranger::ranger(
      y = y_res,
      x = X_res,
      importance = "permutation",
      num.trees = 500,
      seed = 42
    ),
    error = function(e) e
  )
  
  if (inherits(fit_rf, "error")) {
    return(data.frame(
      outcome = outcome_col,
      exposure = metabolite_cols,
      importance = NA_real_,
      oob_r2 = NA_real_,
      n = nrow(d),
      n_input = nrow(df),
      n_complete = nrow(d),
      note = paste0("rf_failed: ", conditionMessage(fit_rf)),
      stringsAsFactors = FALSE
    ))
  }
  
  imp <- ranger::importance(fit_rf)
  
  data.frame(
    outcome = outcome_col,
    exposure = names(imp),
    importance = as.numeric(imp),
    oob_r2 = fit_rf$r.squared,
    n = nrow(d),
    n_input = nrow(df),
    n_complete = nrow(d),
    note = "ok",
    stringsAsFactors = FALSE
  )
}

# Outcome definitions
composite_outcomes <- c(
  "CCognition_indexpoints6",
  "CLanguage_indexpoints6",
  "CMotor_indexpoints6"
)
hine_continuous <- "Hammersmith6"
binary_outcomes <- "HINE_optimal"
secondary_continuous_outcomes <- c(composite_outcomes, hine_continuous)
secondary_outcomes <- c(secondary_continuous_outcomes, binary_outcomes)
all_outcomes <- secondary_outcomes

# Covariates
core_covars_base <- c(
  "CGender",
  "Bfdurationm",
  "InterventionGroup",
  "MprepBMI",
  "MUniEdu",
  "MPRSmoke",
  "MPrimipara"
)

hine_reduced_covars <- c(
  "CGender",
  "Bfdurationm",
  "InterventionGroup",
  "MPrimipara"
)

factor_cols <- c("CGender", "InterventionGroup", "MUniEdu", "MPRSmoke", "MPrimipara")

get_complete_data <- function(df, outcome_col, exposure, covars) {
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
    return(list(ok = FALSE, data = NULL, n_complete = nrow(d), note = "too_few_complete_cases"))
  }
  
  for (cv in covars) {
    if (cv %in% factor_cols) {
      d[[cv]] <- as.factor(d[[cv]])
    } else {
      d[[cv]] <- as.numeric(d[[cv]])
    }
  }
  
  if (is.character(exposure)) {
    d[[exposure]] <- as.numeric(d[[exposure]])
    if (all(is.na(d[[exposure]])) || sd(d[[exposure]], na.rm = TRUE) == 0) {
      return(list(ok = FALSE, data = NULL, n_complete = nrow(d), note = "invalid_exposure_scale"))
    }
    d$x <- as.numeric(scale(d[[exposure]]))
  }
  list(ok = TRUE, data = d, n_complete = nrow(d), note = "ok")
}

audit_repeated_endpoint_design <- function(visit_data_list, outcome_cols, id_col = "StudyID") {
  long_df <- bind_rows(visit_data_list)
  bind_rows(lapply(outcome_cols, function(out_nm) {
    needed <- unique(c(id_col, "visit", out_nm))
    missing_cols <- setdiff(needed, names(long_df))
    if (length(missing_cols) > 0) {
      return(data.frame(
        outcome = out_nm,
        n_children_with_multiple_visit_rows = NA_integer_,
        n_children_with_within_child_outcome_variation = NA_integer_,
        max_distinct_values_per_child = NA_integer_,
        note = paste0("missing_variables: ", paste(missing_cols, collapse = "|")),
        stringsAsFactors = FALSE
      ))
    }
    
    audit_df <- long_df[, needed, drop = FALSE]
    audit_df <- audit_df[!is.na(audit_df[[id_col]]) & !is.na(audit_df[[out_nm]]), , drop = FALSE]
    per_child <- audit_df %>%
      group_by(.data[[id_col]]) %>%
      summarise(
        n_visit_rows = n_distinct(visit),
        n_distinct_outcome_values = n_distinct(.data[[out_nm]]),
        .groups = "drop"
      ) %>%
      filter(n_visit_rows > 1)
    
    data.frame(
      outcome = out_nm,
      n_children_with_multiple_visit_rows = nrow(per_child),
      n_children_with_within_child_outcome_variation = sum(per_child$n_distinct_outcome_values > 1L),
      max_distinct_values_per_child = if (nrow(per_child) == 0) NA_integer_ else max(per_child$n_distinct_outcome_values),
      note = "not_run: endpoints are single 2-year outcomes repeated across visit rows",
      stringsAsFactors = FALSE
    )
  }))
}


# Official NMR group scores

build_official_group_scores <- function(df, mae) {
  rd <- as.data.frame(rowData(experiments(mae)[["visit_all"]]), stringsAsFactors = FALSE)
  rd$feature <- rownames(rd)
  
  group_list <- split(rd$feature, rd$Group)
  
  out <- df
  cluster_cols <- character(0)
  
  for (grp in names(group_list)) {
    members <- intersect(group_list[[grp]], names(out))
    if (length(members) < 1) next
    
    col_nm <- paste0("cluster_", gsub("[^A-Za-z0-9]", "_", grp))
    mat <- as.matrix(out[, members, drop = FALSE])
    mat <- apply(mat, 2, as.numeric)
    
    valid_n <- rowSums(!is.na(mat))
    score <- rowMeans(mat, na.rm = TRUE)
    score[valid_n < 1] <- NA_real_
    
    out[[col_nm]] <- score
    cluster_cols <- c(cluster_cols, col_nm)
  }
  
  list(
    data = out,
    cluster_cols = cluster_cols,
    group_map = rd[, c("feature", "Group", "Biomarker.name")]
  )
}

# Cluster score models

fit_cluster_lm <- function(df, outcome_col, cluster_col, covars) {
  needed_cols <- unique(c(outcome_col, cluster_col, covars))
  missing_cols <- needed_cols[!needed_cols %in% names(df)]
  if (length(missing_cols) > 0) {
    return(data.frame(
      outcome = outcome_col, exposure = cluster_col, n = NA_integer_,
      estimate = NA_real_, std.error = NA_real_, conf.low = NA_real_,
      conf.high = NA_real_, p.value = NA_real_, note = "missing_variables",
      stringsAsFactors = FALSE
    ))
  }
  
  d <- df[, needed_cols, drop = FALSE]
  d <- d[complete.cases(d), , drop = FALSE]
  if (nrow(d) < 30) {
    return(data.frame(
      outcome = outcome_col, exposure = cluster_col, n = nrow(d),
      estimate = NA_real_, std.error = NA_real_, conf.low = NA_real_,
      conf.high = NA_real_, p.value = NA_real_, note = "too_few_complete_cases",
      stringsAsFactors = FALSE
    ))
  }
  
  for (cv in covars) {
    if (cv %in% factor_cols) {
      d[[cv]] <- as.factor(d[[cv]])
    } else {
      d[[cv]] <- as.numeric(d[[cv]])
    }
  }
  
  d$x <- as.numeric(d[[cluster_col]])
  if (all(is.na(d$x)) || sd(d$x, na.rm = TRUE) == 0) {
    return(data.frame(
      outcome = outcome_col, exposure = cluster_col, n = nrow(d),
      estimate = NA_real_, std.error = NA_real_, conf.low = NA_real_,
      conf.high = NA_real_, p.value = NA_real_, note = "invalid_exposure_scale",
      stringsAsFactors = FALSE
    ))
  }
  
  d$x <- as.numeric(scale(d$x))
  
  if (outcome_col == "HINE_optimal") {
    d$y_bin <- ifelse(to_chr(d[[outcome_col]]) == "Suboptimal", 1L, 0L)
    if (length(unique(d$y_bin)) < 2) {
      return(data.frame(
        outcome = outcome_col, exposure = cluster_col, n = nrow(d),
        estimate = NA_real_, std.error = NA_real_, conf.low = NA_real_,
        conf.high = NA_real_, p.value = NA_real_, note = "constant_binary_outcome",
        stringsAsFactors = FALSE
      ))
    }
    form <- reformulate(c("x", covars), response = "y_bin")
    fit <- tryCatch(glm(form, data = d, family = binomial(link = "logit")), error = function(e) NULL)
    if (is.null(fit)) {
      return(data.frame(
        outcome = outcome_col, exposure = cluster_col, n = nrow(d),
        estimate = NA_real_, std.error = NA_real_, conf.low = NA_real_,
        conf.high = NA_real_, p.value = NA_real_, note = "glm_failed",
        stringsAsFactors = FALSE
      ))
    }
    td <- tryCatch(broom::tidy(fit, conf.int = TRUE, exponentiate = TRUE), error = function(e) NULL)
  } else {
    d$y_cont <- as.numeric(d[[outcome_col]])
    if (sd(d$y_cont, na.rm = TRUE) == 0) {
      return(data.frame(
        outcome = outcome_col, exposure = cluster_col, n = nrow(d),
        estimate = NA_real_, std.error = NA_real_, conf.low = NA_real_,
        conf.high = NA_real_, p.value = NA_real_, note = "constant_continuous_outcome",
        stringsAsFactors = FALSE
      ))
    }
    form <- reformulate(c("x", covars), response = "y_cont")
    fit <- tryCatch(lm(form, data = d), error = function(e) NULL)
    if (is.null(fit)) {
      return(data.frame(
        outcome = outcome_col, exposure = cluster_col, n = nrow(d),
        estimate = NA_real_, std.error = NA_real_, conf.low = NA_real_,
        conf.high = NA_real_, p.value = NA_real_, note = "lm_failed",
        stringsAsFactors = FALSE
      ))
    }
    td <- tryCatch(broom::tidy(fit, conf.int = TRUE), error = function(e) NULL)
  }
  
  if (is.null(td)) {
    return(data.frame(
      outcome = outcome_col, exposure = cluster_col, n = nrow(d),
      estimate = NA_real_, std.error = NA_real_, conf.low = NA_real_,
      conf.high = NA_real_, p.value = NA_real_, note = "tidy_failed",
      stringsAsFactors = FALSE
    ))
  }
  
  coef_row <- td %>% filter(term == "x")
  if (nrow(coef_row) == 0) {
    return(data.frame(
      outcome = outcome_col, exposure = cluster_col, n = nrow(d),
      estimate = NA_real_, std.error = NA_real_, conf.low = NA_real_,
      conf.high = NA_real_, p.value = NA_real_, note = "term_not_estimable",
      stringsAsFactors = FALSE
    ))
  }
  
  data.frame(
    outcome = outcome_col, exposure = cluster_col, n = nrow(d),
    estimate = coef_row$estimate[1], std.error = coef_row$std.error[1],
    conf.low = coef_row$conf.low[1], conf.high = coef_row$conf.high[1],
    p.value = coef_row$p.value[1], note = "ok",
    stringsAsFactors = FALSE
  )
}

# =============================================================================
# PART 2: MIXED-MODEL DESIGN AUDIT
# =============================================================================
# Mixed-effects models are intentionally not implemented here. The available
# neurodevelopmental endpoints are single 2-year outcomes repeated on each
# metabolite visit row, so random-intercept mixed models would not estimate a
# meaningful longitudinal outcome association.

# =============================================================================
# PART 3: TOBIT REGRESSION (FOR CEILING-BOUNDED HAMMERSMITH SCORE)
# =============================================================================

fit_tobit_models <- function(visit_data_list, metabolite_cols) {
  results <- list()
  for (vn in names(visit_data_list)) {
    df_v <- visit_data_list[[vn]]
    v_num <- df_v$visit[1]
    
    for (m in metabolite_cols) {
      prep <- get_complete_data(df_v, hine_continuous, m, core_covars_base)
      if (!isTRUE(prep$ok)) {
        results[[paste(vn, hine_continuous, m, sep = "_")]] <- data.frame(
          visit = v_num,
          outcome = hine_continuous,
          exposure = m,
          n = prep$n_complete,
          estimate = NA_real_,
          std.error = NA_real_,
          conf.low = NA_real_,
          conf.high = NA_real_,
          statistic = NA_real_,
          p.value = NA_real_,
          note = prep$note,
          stringsAsFactors = FALSE
        )
        next
      }
      
      d <- prep$data
      d$y <- as.numeric(d[[hine_continuous]])
      form <- reformulate(c("x", core_covars_base), response = "y")
      
      fit <- tryCatch(
        AER::tobit(form, data = d, right = 78),
        error = function(e) e
      )
      
      if (inherits(fit, "error")) {
        results[[paste(vn, hine_continuous, m, sep = "_")]] <- data.frame(
          visit = v_num,
          outcome = hine_continuous,
          exposure = m,
          n = nrow(d),
          estimate = NA_real_,
          std.error = NA_real_,
          conf.low = NA_real_,
          conf.high = NA_real_,
          statistic = NA_real_,
          p.value = NA_real_,
          note = paste0("tobit_failed: ", conditionMessage(fit)),
          stringsAsFactors = FALSE
        )
        next
      }
      
      s <- summary(fit)
      coef_mat <- s$coefficients
      if (!"x" %in% rownames(coef_mat)) {
        results[[paste(vn, hine_continuous, m, sep = "_")]] <- data.frame(
          visit = v_num,
          outcome = hine_continuous,
          exposure = m,
          n = nrow(d),
          estimate = NA_real_,
          std.error = NA_real_,
          conf.low = NA_real_,
          conf.high = NA_real_,
          statistic = NA_real_,
          p.value = NA_real_,
          note = "term_not_estimable",
          stringsAsFactors = FALSE
        )
        next
      }
      
      est <- coef_mat["x", "Estimate"]
      se <- coef_mat["x", "Std. Error"]
      zval <- coef_mat["x", "z value"]
      pval <- coef_mat["x", "Pr(>|z|)"]
      
      results[[paste(vn, hine_continuous, m, sep = "_")]] <- data.frame(
        visit = v_num,
        outcome = hine_continuous,
        exposure = m,
        n = nrow(d),
        estimate = est,
        std.error = se,
        conf.low = est - 1.96 * se,
        conf.high = est + 1.96 * se,
        statistic = zval,
        p.value = pval,
        note = "ok",
        stringsAsFactors = FALSE
      )
    }
  }
  
  tobit_df <- bind_rows(results)
  tobit_df <- tobit_df %>%
    group_by(visit) %>%
    mutate(q.value = p.adjust(p.value, method = "BH")) %>%
    ungroup() %>%
    arrange(visit, q.value, p.value)
  
  tobit_df
}

# =============================================================================
# PART 4: FIRTH PENALIZED LOGISTIC REGRESSION (FOR HINE_optimal)
# =============================================================================

fit_firth_models <- function(visit_data_list, metabolite_cols) {
  results <- list()
  for (vn in names(visit_data_list)) {
    df_v <- visit_data_list[[vn]]
    v_num <- df_v$visit[1]
    
    for (m in metabolite_cols) {
      prep <- get_complete_data(df_v, "HINE_optimal", m, hine_reduced_covars)
      if (!isTRUE(prep$ok)) {
        results[[paste(vn, "HINE_optimal", m, sep = "_")]] <- data.frame(
          visit = v_num,
          outcome = "HINE_optimal",
          exposure = m,
          n = prep$n_complete,
          estimate = NA_real_,
          log_or = NA_real_,
          std.error = NA_real_,
          conf.low = NA_real_,
          conf.high = NA_real_,
          p.value = NA_real_,
          note = prep$note,
          stringsAsFactors = FALSE
        )
        next
      }
      
      d <- prep$data
      d$y_bin <- ifelse(to_chr(d[["HINE_optimal"]]) == "Suboptimal", 1L, 0L)
      if (length(unique(d$y_bin)) < 2) {
        results[[paste(vn, "HINE_optimal", m, sep = "_")]] <- data.frame(
          visit = v_num,
          outcome = "HINE_optimal",
          exposure = m,
          n = nrow(d),
          estimate = NA_real_,
          log_or = NA_real_,
          std.error = NA_real_,
          conf.low = NA_real_,
          conf.high = NA_real_,
          p.value = NA_real_,
          note = "constant_binary_outcome",
          stringsAsFactors = FALSE
        )
        next
      }
      
      form <- reformulate(c("x", hine_reduced_covars), response = "y_bin")
      fit <- tryCatch(
        logistf::logistf(form, data = d, pl = FALSE),
        error = function(e) e
      )
      
      if (inherits(fit, "error")) {
        results[[paste(vn, "HINE_optimal", m, sep = "_")]] <- data.frame(
          visit = v_num,
          outcome = "HINE_optimal",
          exposure = m,
          n = nrow(d),
          estimate = NA_real_,
          log_or = NA_real_,
          std.error = NA_real_,
          conf.low = NA_real_,
          conf.high = NA_real_,
          p.value = NA_real_,
          note = paste0("firth_failed: ", conditionMessage(fit)),
          stringsAsFactors = FALSE
        )
        next
      }
      
      var_idx <- match("x", fit$terms)
      if (is.na(var_idx)) {
        results[[paste(vn, "HINE_optimal", m, sep = "_")]] <- data.frame(
          visit = v_num,
          outcome = "HINE_optimal",
          exposure = m,
          n = nrow(d),
          estimate = NA_real_,
          log_or = NA_real_,
          std.error = NA_real_,
          conf.low = NA_real_,
          conf.high = NA_real_,
          p.value = NA_real_,
          note = "term_not_estimable",
          stringsAsFactors = FALSE
        )
        next
      }
      
      est_log_or <- fit$coefficients[var_idx]
      se <- sqrt(fit$var[var_idx, var_idx])
      or <- exp(est_log_or)
      ci_low <- exp(est_log_or - 1.96 * se)
      ci_high <- exp(est_log_or + 1.96 * se)
      pval <- fit$prob[var_idx]
      
      results[[paste(vn, "HINE_optimal", m, sep = "_")]] <- data.frame(
        visit = v_num,
        outcome = "HINE_optimal",
        exposure = m,
        n = nrow(d),
        estimate = or,
        log_or = est_log_or,
        std.error = se,
        conf.low = ci_low,
        conf.high = ci_high,
        p.value = pval,
        note = "firth_ok",
        stringsAsFactors = FALSE
      )
    }
  }
  
  firth_df <- bind_rows(results)
  firth_df <- firth_df %>%
    group_by(visit) %>%
    mutate(q.value = p.adjust(p.value, method = "BH")) %>%
    ungroup() %>%
    arrange(visit, q.value, p.value)
  
  firth_df
}

# Joint models

fit_joint_models <- function(visit_data_list, metabolite_cols) {
  enet_results <- list()
  pls_results <- list()
  
  for (vn in names(visit_data_list)) {
    df_v <- visit_data_list[[vn]]
    v_num <- df_v$visit[1]
    
    for (out_nm in composite_outcomes) {
      needed <- unique(c(out_nm, metabolite_cols, core_covars_base))
      d <- df_v[, needed, drop = FALSE]
      d <- d[complete.cases(d), , drop = FALSE]
      if (nrow(d) < 40) next
      
      y_raw <- as.numeric(d[[out_nm]])
      cov_df <- d[, core_covars_base, drop = FALSE]
      for (cv in core_covars_base) {
        if (cv %in% factor_cols) cov_df[[cv]] <- as.factor(cov_df[[cv]])
      }
      res_lm <- lm(reformulate(core_covars_base, response = "y_raw"), data = cov_df)
      y_res <- residuals(res_lm)
      
      X_mat <- as.matrix(d[, metabolite_cols, drop = FALSE])
      X_mat <- apply(X_mat, 2, as.numeric)
      X_mat <- scale(X_mat)
      
      set.seed(42)
      cv_enet <- tryCatch(
        glmnet::cv.glmnet(X_mat, y_res, alpha = 0.5, nfolds = 5),
        error = function(e) NULL
      )
      
      if (!is.null(cv_enet)) {
        best_lambda <- cv_enet$lambda.min
        coefs <- as.matrix(coef(cv_enet, s = "lambda.min"))
        non_zero <- coefs[coefs[, 1] != 0, 1, drop = FALSE]
        non_zero_feats <- setdiff(rownames(non_zero), "(Intercept)")
        
        cv_mse <- min(cv_enet$cvm)
        var_y <- var(y_res)
        cv_r2 <- 1 - cv_mse / var_y
        
        enet_results[[paste(vn, out_nm, sep = "_")]] <- data.frame(
          visit = v_num,
          outcome = out_nm,
          cv_r2 = round(cv_r2, 4),
          n_selected = length(non_zero_feats),
          selected_features = paste(head(non_zero_feats, 10), collapse = ", "),
          stringsAsFactors = FALSE
        )
      }
      
      pls_df <- data.frame(y = y_res)
      pls_df$X <- X_mat
      
      fit_pls <- tryCatch(
        pls::plsr(y ~ X, data = pls_df, ncomp = 5, validation = "CV"),
        error = function(e) NULL
      )
      
      if (!is.null(fit_pls)) {
        val_msep <- pls::MSEP(fit_pls)
        msep_vals <- val_msep$val[1, 1, -1]
        best_comp <- which.min(msep_vals)
        r2_vals <- pls::R2(fit_pls)$val[1, 1, -1]
        best_r2 <- r2_vals[best_comp]
        
        pls_results[[paste(vn, out_nm, sep = "_")]] <- data.frame(
          visit = v_num,
          outcome = out_nm,
          n_components = best_comp,
          cv_r2 = round(best_r2, 4),
          stringsAsFactors = FALSE
        )
      }
    }
  }
  
  list(
    elastic_net = bind_rows(enet_results),
    pls = bind_rows(pls_results)
  )
}

# Metabolite set enrichment

run_formal_msea <- function(primary_results_df, mae) {
  rd <- as.data.frame(rowData(experiments(mae)[["visit_all"]]), stringsAsFactors = FALSE)
  rd$feature <- rownames(rd)
  
  group_members <- split(rd$feature, rd$Group)
  msea_results <- list()
  groups_to_test <- names(group_members)[vapply(group_members, length, integer(1)) >= 3]
  
  unique_combos <- primary_results_df %>%
    distinct(outcome, visit)
  
  for (i in seq_len(nrow(unique_combos))) {
    out_nm <- unique_combos$outcome[i]
    v_num <- unique_combos$visit[i]
    
    sub_df <- primary_results_df %>%
      filter(outcome == out_nm & visit == v_num & !is.na(p.value))
    
    if (nrow(sub_df) < 50) next
    
    sub_df$score <- -log10(sub_df$p.value)
    
    for (grp in groups_to_test) {
      members <- group_members[[grp]]
      in_set <- sub_df$exposure %in% members
      
      if (sum(in_set) < 3) next
      
      set_scores <- sub_df$score[in_set]
      bg_scores <- sub_df$score[!in_set]
      
      wt <- wilcox.test(set_scores, bg_scores, alternative = "greater")
      
      mean_set <- mean(set_scores, na.rm = TRUE)
      mean_bg <- mean(bg_scores, na.rm = TRUE)
      enrich_ratio <- mean_set / ifelse(mean_bg == 0, 1e-4, mean_bg)
      
      msea_results[[paste(out_nm, v_num, grp, sep = "_")]] <- data.frame(
        outcome = out_nm,
        visit = v_num,
        group = grp,
        n_features = sum(in_set),
        mean_neglog10p_set = round(mean_set, 3),
        mean_neglog10p_bg = round(mean_bg, 3),
        enrichment_ratio = round(enrich_ratio, 2),
        p.value = wt$p.value,
        stringsAsFactors = FALSE
      )
    }
  }
  
  msea_df <- bind_rows(msea_results)
  msea_df <- msea_df %>%
    group_by(outcome, visit) %>%
    mutate(q.value = p.adjust(p.value, method = "BH")) %>%
    ungroup() %>%
    arrange(p.value)
  
  msea_df
}

# Main execution

main <- function() {
  mae_path <- file.path(root_dir, "Neurocognition", "data", "MAE.rds")
  out_dir <- file.path(root_dir, "Neurocognition", "output", "results")
  ensure_dir(out_dir)
  
  mae <- readRDS(mae_path)
  message("[neurocognition-secondary] Loaded MAE dataset")
  
  metabolite_cols <- rownames(assay(experiments(mae)[["visit_all"]], "mbo"))
  visits <- c(4L, 5L, 6L)
  
  # Build per-visit data with official MAE Group metadata
  visit_data_list <- list()
  for (v in visits) {
    vn <- paste0("visit_", v)
    tse <- experiments(mae)[[vn]]
    cd <- as.data.frame(colData(tse), stringsAsFactors = FALSE)
    cd$sample_id <- rownames(cd)
    mat <- as.data.frame(t(assay(tse, "mbo")), check.names = FALSE)
    mat$sample_id <- rownames(mat)
    df <- left_join(cd, mat, by = "sample_id")
    df_transformed <- log_z_by_visit(df, metabolite_cols = metabolite_cols, visit_col = "Visit")
    
    cluster_out <- build_official_group_scores(df_transformed, mae)
    df_transformed <- cluster_out$data
    
    df_transformed$visit <- v
    df_transformed$visit_factor <- as.character(v)
    visit_data_list[[vn]] <- df_transformed
  }
  
  cluster_cols <- cluster_out$cluster_cols
  group_map <- cluster_out$group_map
  message("[neurocognition-secondary] 1. Official MAE Group cluster scores built: ", length(cluster_cols), " groups")
  
  # Cluster score regressions
  message("[neurocognition-secondary] Running cluster score regressions...")
  cluster_results_list <- list()
  for (v in visits) {
    vn <- paste0("visit_", v)
    df_v <- visit_data_list[[vn]]
    for (out_nm in all_outcomes) {
      covars <- if (out_nm == "HINE_optimal") hine_reduced_covars else core_covars_base
      for (cl in cluster_cols) {
        res <- fit_cluster_lm(df_v, out_nm, cl, covars)
        res$visit <- v
        res$analysis_type <- "cluster"
        group_label <- gsub("^cluster_", "", cl)
        group_label <- gsub("_", " ", group_label)
        res$group_label <- group_label
        cluster_results_list[[paste(v, out_nm, cl, sep = "_")]] <- res
      }
    }
  }
  cluster_results <- bind_rows(cluster_results_list) %>%
    group_by(outcome, visit) %>%
    mutate(q.value = p.adjust(p.value, method = "BH")) %>%
    ungroup() %>%
    arrange(outcome, visit, q.value, p.value)
  
  # Mixed-model design audit
  message("[neurocognition-secondary] 2. Auditing mixed-model design (models are not run)...")
  mixed_design_audit <- audit_repeated_endpoint_design(visit_data_list, secondary_outcomes)
  mixed_results <- data.frame(
    outcome = secondary_outcomes,
    exposure = NA_character_,
    term = NA_character_,
    estimate = NA_real_,
    std.error = NA_real_,
    statistic = NA_real_,
    p.value = NA_real_,
    n = NA_integer_,
    n_ids = NA_integer_,
    q.value = NA_real_,
    note = "not_run: 2-year outcomes are repeated single endpoints; random-intercept mixed models are not interpretable",
    stringsAsFactors = FALSE
  )
  
  # Tobit models
  message("[neurocognition-secondary] 3. Running Tobit regression models for Hammersmith global score...")
  tobit_df <- fit_tobit_models(visit_data_list, metabolite_cols)
  
  # Firth logistic models
  message("[neurocognition-secondary] 4. Running Firth penalized logistic regressions for HINE optimality...")
  firth_df <- fit_firth_models(visit_data_list, metabolite_cols)
  
  # Joint Elastic Net and PLS models
  message("[neurocognition-secondary] 5. Fitting joint Elastic Net & PLS models...")
  joint_out <- fit_joint_models(visit_data_list, metabolite_cols)
  
  # MSEA
  message("[neurocognition-secondary] 6. Performing formal competitive MSEA...")
  primary_path <- file.path(out_dir, "neurocognition_study_results.rds")
  primary_obj <- readRDS(primary_path)
  primary_df <- primary_obj[["primary_results"]]
  primary_secondary_df <- primary_df %>% filter(outcome %in% secondary_outcomes)
  msea_df <- run_formal_msea(primary_secondary_df, mae)
  
  # M_eff calculation
  message("[neurocognition-secondary] 7. Computing M_eff spectral decomposition...")
  m_eff_results <- list()
  for (v in visits) {
    vn <- paste0("visit_", v)
    df_v <- visit_data_list[[vn]]
    met_mat <- as.matrix(df_v[, metabolite_cols, drop = FALSE])
    met_mat <- apply(met_mat, 2, as.numeric)
    met_mat <- met_mat[complete.cases(met_mat), , drop = FALSE]
    n_complete_all_metabolites <- nrow(met_mat)
    cor_mat <- cor(met_mat, use = "pairwise.complete.obs")
    eigenvalues <- eigen(cor_mat, only.values = TRUE)$values
    eigenvalues <- eigenvalues[eigenvalues > 0]
    m_eff <- sum(as.numeric(eigenvalues >= 1) + (eigenvalues - floor(eigenvalues)))
    m_eff_results[[vn]] <- data.frame(
      visit = v, m_eff = round(m_eff), n_metabolites = length(metabolite_cols),
      n_complete_all_metabolites = n_complete_all_metabolites,
      bonferroni_threshold = 0.05 / round(m_eff),
      stringsAsFactors = FALSE
    )
  }
  m_eff_df <- bind_rows(m_eff_results)
  
  m_eff_hits <- primary_secondary_df %>%
    left_join(m_eff_df %>% select(visit, bonferroni_threshold), by = "visit") %>%
    filter(!is.na(p.value) & p.value < bonferroni_threshold) %>%
    mutate(
      correction_method = "M_eff Bonferroni",
      m_eff_threshold = bonferroni_threshold
    )
  
  continuous_outcomes <- secondary_continuous_outcomes
  
  # Permutation-based FDR
  message("[neurocognition-secondary] 8. Computing permutation-based FDR...")
  perm_results_list <- list()
  for (v in visits) {
    vn <- paste0("visit_", v)
    df_v <- visit_data_list[[vn]]
    for (out_nm in continuous_outcomes) {
      res <- fit_perm_fdr_one_group(df_v, out_nm, metabolite_cols, core_covars_base, B = 1000)
      if (!is.null(res)) {
        res$visit <- v
        res$q.value <- p.adjust(res$p.value, method = "BH")
        perm_results_list[[paste(v, out_nm, sep = "_")]] <- res
      }
    }
  }
  perm_fdr_results <- bind_rows(perm_results_list)
  
  # Random forest variable importance
  message("[neurocognition-secondary] 9. Computing random-forest variable importance...")
  rf_results_list <- list()
  for (v in visits) {
    vn <- paste0("visit_", v)
    df_v <- visit_data_list[[vn]]
    for (out_nm in composite_outcomes) {
      res <- fit_rf_one_group(df_v, out_nm, metabolite_cols, core_covars_base)
      if (!is.null(res)) {
        res$visit <- v
        rf_results_list[[paste(v, out_nm, sep = "_")]] <- res
      }
    }
  }
  rf_results <- bind_rows(rf_results_list)
  
  msea_expected_rows <- length(unique(msea_df$group)) * length(secondary_outcomes) * length(visits)
  analysis_manifest <- data.frame(
    analysis = c(
      "cluster_scores",
      "mixed_design_audit",
      "mixed_models",
      "tobit_hammersmith",
      "firth_hine",
      "elastic_net",
      "pls",
      "msea",
      "m_eff",
      "m_eff_hits",
      "permutation_fdr",
      "random_forest"
    ),
    expected_rows = c(
      length(visits) * length(all_outcomes) * length(cluster_cols),
      length(secondary_outcomes),
      length(secondary_outcomes),
      length(visits) * length(metabolite_cols),
      length(visits) * length(metabolite_cols),
      length(visits) * length(composite_outcomes),
      length(visits) * length(composite_outcomes),
      msea_expected_rows,
      length(visits),
      NA_integer_,
      length(visits) * length(secondary_continuous_outcomes) * length(metabolite_cols),
      length(visits) * length(composite_outcomes) * length(metabolite_cols)
    ),
    observed_rows = c(
      nrow(cluster_results),
      nrow(mixed_design_audit),
      nrow(mixed_results),
      nrow(tobit_df),
      nrow(firth_df),
      nrow(joint_out$elastic_net),
      nrow(joint_out$pls),
      nrow(msea_df),
      nrow(m_eff_df),
      nrow(m_eff_hits),
      nrow(perm_fdr_results),
      nrow(rf_results)
    ),
    interpretable_rows = c(
      sum(cluster_results$note == "ok", na.rm = TRUE),
      0L,
      0L,
      sum(tobit_df$note == "ok", na.rm = TRUE),
      sum(firth_df$note == "firth_ok", na.rm = TRUE),
      nrow(joint_out$elastic_net),
      nrow(joint_out$pls),
      nrow(msea_df),
      nrow(m_eff_df),
      nrow(m_eff_hits),
      sum(perm_fdr_results$note == "ok", na.rm = TRUE),
      sum(rf_results$note == "ok", na.rm = TRUE)
    ),
    note = c(
      "OLS/logistic associations for 18 MAE groups across five secondary outcomes",
      "Audit confirms these are repeated single 2-year endpoints, not longitudinal outcomes",
      "Not run because random-intercept mixed models are not interpretable for repeated single endpoints",
      "Exploratory sensitivity for high-scoring Hammersmith distribution",
      "Exploratory HINE sensitivity with reduced covariate block",
      "Raw cross-validated R-squared; negative values are retained",
      "Cross-validated PLS R-squared",
      "Exploratory rank-sum enrichment of nominal primary p-values restricted to five secondary outcomes",
      "Li-Ji effective-test screen using all-feature complete cases for metabolite correlation",
      "Primary rows within the five-outcome secondary scope passing the M_eff threshold",
      "Complete cases across outcome, covariates, and all 250 metabolites; not directly comparable to primary per-metabolite N",
      "Covariate-residualized random forest variable importance"
    ),
    stringsAsFactors = FALSE
  )
  
  # Save secondary results
  output_file <- file.path(out_dir, "neurocognition_secondary_results.rds")
  saveRDS(
    list(
      cluster_results = cluster_results,
      group_map = group_map,
      cluster_cols = cluster_cols,
      mixed_results = mixed_results,
      mixed_design_audit = mixed_design_audit,
      tobit_results = tobit_df,
      firth_results = firth_df,
      elastic_net = joint_out$elastic_net,
      pls = joint_out$pls,
      msea_results = msea_df,
      m_eff = m_eff_df,
      m_eff_hits = m_eff_hits,
      perm_fdr_results = perm_fdr_results,
      rf_results = rf_results,
      analysis_manifest = analysis_manifest,
      covariates_used = core_covars_base,
      hine_reduced_covars = hine_reduced_covars,
      composite_outcomes = composite_outcomes,
      secondary_continuous_outcomes = secondary_continuous_outcomes,
      secondary_outcomes = secondary_outcomes,
      metabolites = metabolite_cols,
      timestamp = format(Sys.time(), "%Y-%m-%dT%H:%M:%S%z")
    ),
    output_file
  )
  
  message("[neurocognition-secondary] Exploratory secondary pipeline completed successfully: ", output_file)
  message(sprintf("  Cluster score rows: %d", nrow(cluster_results)))
  message(sprintf("  Mixed model rows: %d", nrow(mixed_results)))
  message(sprintf("  Tobit models: %d", nrow(tobit_df)))
  message(sprintf("  Firth models: %d", nrow(firth_df)))
  message(sprintf("  MSEA tests: %d", nrow(msea_df)))
  message(sprintf("  Permutation FDR rows: %d", nrow(perm_fdr_results)))
  message(sprintf("  Random-forest rows: %d", nrow(rf_results)))
}

if (identical(environment(), globalenv())) {
  main()
}
