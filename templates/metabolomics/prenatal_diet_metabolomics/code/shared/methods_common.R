suppressPackageStartupMessages({
  library(dplyr)
  library(tidyr)
})

`%||%` <- function(x, y) {
  if (is.null(x) || length(x) == 0) y else x
}

is_ratio_feature <- function(x, ratio_pattern = "-ratio") {
  grepl(ratio_pattern, x, fixed = TRUE)
}

get_track_features <- function(feature_names, track = c("all", "metabolites", "ratio"), ratio_pattern = "-ratio") {
  track <- match.arg(track)
  if (track == "all") {
    return(feature_names)
  }
  if (track == "metabolites") {
    return(feature_names[!is_ratio_feature(feature_names, ratio_pattern = ratio_pattern)])
  }
  feature_names[is_ratio_feature(feature_names, ratio_pattern = ratio_pattern)]
}

make_exposure_tertiles_common <- function(x) {
  x <- suppressWarnings(as.numeric(x))
  out <- rep(NA_character_, length(x))
  keep <- is.finite(x)
  if (sum(keep) < 9L) {
    return(factor(out, levels = c("Low", "Mid", "High")))
  }

  r <- rank(x[keep], ties.method = "average")
  g <- ceiling(3 * r / max(r))
  g[g < 1] <- 1
  g[g > 3] <- 3
  out[keep] <- c("Low", "Mid", "High")[g]
  factor(out, levels = c("Low", "Mid", "High"))
}

prepare_panel_data <- function(tse, exposure, covars = character(0), track = "all", min_n = 30L) {
  meta <- as.data.frame(SummarizedExperiment::colData(tse), stringsAsFactors = FALSE)
  feat <- as.data.frame(t(SummarizedExperiment::assay(tse, "mbo")), check.names = FALSE)
  meta$sample_id <- rownames(meta)
  feat$sample_id <- rownames(feat)

  feature_names <- rownames(SummarizedExperiment::assay(tse, "mbo"))
  keep_features <- get_track_features(feature_names, track = track)
  keep_features <- intersect(keep_features, colnames(feat))

  if (length(keep_features) < 10L) {
    return(list(
      status = "skip",
      note = "too_few_features_for_track"
    ))
  }

  covars <- intersect(covars, names(meta))
  if (!exposure %in% names(meta)) {
    return(list(
      status = "skip",
      note = "exposure_missing"
    ))
  }

  vars <- unique(c(exposure, covars))
  cc <- complete.cases(meta[, vars, drop = FALSE])
  meta2 <- meta[cc, , drop = FALSE]

  if (nrow(meta2) < min_n) {
    return(list(
      status = "skip",
      note = "too_few_complete_cases"
    ))
  }

  x <- suppressWarnings(as.numeric(meta2[[exposure]]))
  if (length(unique(x[is.finite(x)])) < 3L) {
    return(list(
      status = "skip",
      note = "constant_or_sparse_exposure"
    ))
  }
  meta2[[exposure]] <- x

  rownames(meta2) <- meta2$sample_id
  feat2 <- feat[meta2$sample_id, keep_features, drop = FALSE]

  feat2[] <- lapply(feat2, function(v) suppressWarnings(as.numeric(v)))
  keep_var <- vapply(feat2, function(v) {
    vv <- stats::sd(v, na.rm = TRUE)
    is.finite(vv) && vv > 0
  }, logical(1))
  feat2 <- feat2[, keep_var, drop = FALSE]

  if (ncol(feat2) < 10L) {
    return(list(
      status = "skip",
      note = "too_few_nonconstant_features"
    ))
  }

  list(
    status = "ok",
    note = "ok",
    exposure = exposure,
    covars = covars,
    metadata = meta2,
    features = feat2,
    n_samples = nrow(meta2),
    n_features = ncol(feat2)
  )
}


# --- TARGETED ANALYTICAL ENGINE ---

#' Run feature-wise linear regression using limma
#' @param tse A TreeSummarizedExperiment / SummarizedExperiment
#' @param exposure String, name of the dietary exposure variable
#' @param covars Character vector of covariate names
#' @param track String, "metabolites" or "ratio"
#' @param min_n Minimum number of complete cases
run_targeted_lm_panel <- function(tse, 
                                  exposure, 
                                  covars = character(0), 
                                  track = c("metabolites", "ratio"),
                                  min_n = 30L) {
  track <- match.arg(track)
  
  # 1. Prepare data
  feat_names <- rownames(tse)
  keep_feat <- get_track_features(feat_names, track = track)
  tse_sub <- tse[keep_feat, ]
  
  meta <- as.data.frame(SummarizedExperiment::colData(tse_sub), stringsAsFactors = FALSE)
  vars <- unique(c(exposure, covars))
  
  # Ensure variables are present and check cases
  ok_vars <- vars %in% names(meta)
  if (!all(ok_vars)) {
    stop("Missing variables in colData: ", paste(vars[!ok_vars], collapse = ", "))
  }
  
  cc <- stats::complete.cases(meta[, vars, drop = FALSE])
  if (sum(cc) < min_n) {
    return(data.frame(note = paste0("too_few_complete_cases: ", sum(cc))))
  }
  
  meta_cc <- meta[cc, , drop = FALSE]
  mat <- SummarizedExperiment::assay(tse_sub, "mbo")[, cc, drop = FALSE]
  
  # 2. Preprocess outcomes: Log-transform and Standardize
  # Note: Assay is already supposed to be log-transformed according to pre-processing rules.
  # We apply standardization (Z-score) here to report in SD units.
  mat_std <- t(apply(mat, 1, function(x) {
    x <- suppressWarnings(as.numeric(x))
    if (stats::sd(x, na.rm = TRUE) == 0) return(rep(NA_real_, length(x)))
    (x - mean(x, na.rm = TRUE)) / stats::sd(x, na.rm = TRUE)
  }))
  
  # 3. Fit limma model
  # Formula: ~ Exposure + Covars
  formula_str <- paste0("~ ", exposure, if (length(covars) > 0) paste0(" + ", paste(covars, collapse = " + ")) else "")
  design <- stats::model.matrix(stats::as.formula(formula_str), data = meta_cc)
  
  fit <- limma::lmFit(mat_std, design)
  fit <- limma::eBayes(fit, trend = TRUE)
  
  # 4. Extract results for the exposure term
  # We look for the term matching the exposure name
  target_terms <- grep(paste0("^", exposure), colnames(design), value = TRUE)
  if (length(target_terms) == 0) {
    stop("Exposure term not found in design matrix.")
  }
  
  # For the table, we handle the case where a factor has multiple terms
  # If there is only one term (continuous or 2-level factor), we get logFC (Beta)
  # If there are multiple (multi-level factor), we get the contrast with the first non-reference level
  # BUT we always calculate the omnibus p-value if there are multiple.
  
  if (length(target_terms) == 1) {
    res <- limma::topTable(fit, coef = target_terms, number = Inf, sort.by = "none", confint = TRUE)
    out <- data.frame(
      feature = rownames(res),
      n = sum(cc),
      estimate = res$logFC, 
      conf.low = res$CI.L,
      conf.high = res$CI.R,
      p.value = res$P.Value,
      q.value = NA_real_,
      exposure = exposure,
      term = target_terms,
      stringsAsFactors = FALSE
    )
  } else {
    # Omnibus test for all levels of the factor
    res_omnibus <- limma::topTable(fit, coef = target_terms, number = Inf, sort.by = "none")
    
    # Get estimates and CIs for each level
    coef_list <- lapply(target_terms, function(term_i) {
      res_i <- limma::topTable(fit, coef = term_i, number = Inf, sort.by = "none", confint = TRUE)
      clean_label <- gsub(paste0("^", exposure), "", term_i)
      # Shorten the level labels
      clean_label <- gsub("moderate but needs to be improved", "Moderate", clean_label)
      clean_label <- gsub("ok/good", "Good", clean_label, ignore.case = TRUE)
      list(est = res_i$logFC, ci_l = res_i$CI.L, ci_r = res_i$CI.R, label = clean_label)
    })
    
    n_feats <- nrow(res_omnibus)
    lvl_det <- character(n_feats)
    est_det <- character(n_feats)
    ci_det <- character(n_feats)
    
    for (idx in 1:n_feats) {
      parts_lvl <- character(length(coef_list))
      parts_est <- character(length(coef_list))
      parts_ci <- character(length(coef_list))
      for (k in seq_along(coef_list)) {
        lvl <- coef_list[[k]]
        parts_lvl[k] <- lvl$label
        parts_est[k] <- as.character(round(lvl$est[idx], 3))
        parts_ci[k] <- paste0("[", round(lvl$ci_l[idx], 3), ", ", round(lvl$ci_r[idx], 3), "]")
      }
      lvl_det[idx] <- paste(parts_lvl, collapse = "<br>")
      est_det[idx] <- paste(parts_est, collapse = "<br>")
      ci_det[idx] <- paste(parts_ci, collapse = "<br>")
    }
    
    out <- data.frame(
      feature = rownames(res_omnibus),
      n = sum(cc),
      estimate = coef_list[[1]]$est, # First contrast remains as numeric placeholder
      conf.low = coef_list[[1]]$ci_l,
      conf.high = coef_list[[1]]$ci_r,
      p.value = res_omnibus$P.Value, # Use the omnibus P-value
      q.value = NA_real_,
      exposure = exposure,
      term = paste(target_terms, collapse = "|"),
      levels_detailed = lvl_det,
      estimates_detailed = est_det,
      ci_detailed = ci_det,
      stringsAsFactors = FALSE
    )
  }
  
  # Apply BH-FDR within the returned feature-level analysis family
  out$q.value <- stats::p.adjust(out$p.value, method = "BH")
  
  out %>% dplyr::arrange(q.value, p.value)
}

#' Run feature-wise mixed-effects models for longitudinal analysis
#' @param df Longitudinal dataframe (long format)
#' @param metabolite_cols Character vector of metabolites
#' @param exposure Exposure variable
#' @param covars Covariates (fixed effects)
#' @param time_col Time variable (e.g. child age)
#' @param id_col ID variable (e.g. StudyID for random intercept)
run_targeted_lmm_panel <- function(df, 
                                   metabolite_cols, 
                                   exposure, 
                                   covars = character(0), 
                                   time_col = "visit_num",
                                   id_col = "StudyID") {
  
  if (!requireNamespace("lmerTest", quietly = TRUE)) {
    stop("Package 'lmerTest' is required for LMM analysis.")
  }
  
  res_list <- lapply(metabolite_cols, function(m) {
    # Prepare single metabolite data
    needed <- unique(c(id_col, time_col, exposure, covars, m))
    missing <- needed[!needed %in% names(df)]
    if (length(missing) > 0) {
      # message("  [LMM] Missing columns for ", m, ": ", paste(missing, collapse=", "))
      return(NULL)
    }
    
    sub_df <- df[, needed]
    sub_df <- sub_df[stats::complete.cases(sub_df), ]
    
    if (nrow(sub_df) < 50L) return(NULL)

    if (is.character(sub_df[[exposure]])) {
      sub_df[[exposure]] <- factor(sub_df[[exposure]])
    }
    for (cv in covars) {
      if (cv %in% names(sub_df) && is.character(sub_df[[cv]])) {
        sub_df[[cv]] <- factor(sub_df[[cv]])
      }
    }
    
    # Standardize outcome
    y <- sub_df[[m]]
    y_sd <- stats::sd(y, na.rm = TRUE)
    if (!is.finite(y_sd) || y_sd == 0) return(NULL)
    sub_df[[m]] <- (y - mean(y, na.rm = TRUE)) / y_sd
    
    # Formula: m ~ exposure * time_col + covars + (1 | id)
    # Use backticks for feature name if it has special characters
    m_clean <- paste0("`", m, "`")
    f <- stats::as.formula(paste0(
      m_clean, " ~ ", exposure, " * ", time_col, 
      if (length(covars) > 0) paste0(" + ", paste(covars, collapse = " + ")) else "",
      " + (1 | ", id_col, ")"
    ))
    
    fit <- tryCatch(lmerTest::lmer(f, data = sub_df), error = function(e) {
      # message("  [LMM] Fit failed for ", m, ": ", e$message)
      NULL
    })
    if (is.null(fit)) return(NULL)
    
    # Tidy results
    sum_tbl <- tryCatch(broom.mixed::tidy(fit, effects = "fixed"), error = function(e) NULL)
    if (is.null(sum_tbl) || nrow(sum_tbl) == 0) return(NULL)
    
    # We are interested in:
    # 1. Main effect of diet (exposure)
    # 2. Interaction (diet x age)
    
    out_m <- sum_tbl %>%
      filter(
        grepl(paste0("^", exposure), term) |
          grepl(paste0(":", exposure), term) |
          grepl(paste0(exposure, ":"), term)
      ) %>%
      mutate(
        feature = m,
        n_obs = nrow(sub_df),
        n_ind = length(unique(sub_df[[id_col]])),
        note = "ok"
      )
    
    if (nrow(out_m) == 0) return(NULL)
    out_m
  })
  
  out <- dplyr::bind_rows(res_list)
  
  if (nrow(out) == 0) {
    return(data.frame(note = "no_models_converged"))
  }
  
  # Multiplicity adjustment per exposure and coefficient family (main vs interaction).
  # Called once per exposure; pool BH across metabolites within each family.
  if ("term" %in% names(out)) {
    out <- out %>%
      mutate(term_family = ifelse(grepl(":", term), "interaction", "main")) %>%
      group_by(term_family) %>%
      mutate(q.value = stats::p.adjust(p.value, method = "BH")) %>%
      ungroup()
  }
  
  out %>% dplyr::arrange(term, q.value)
}
