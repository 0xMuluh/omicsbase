suppressPackageStartupMessages({
  library(dplyr)
  library(tidyr)
  library(broom)
  library(broom.mixed)
})

#' Compare primary vs sensitivity results
#' @param prim Tidy dataframe of primary results
#' @param sens Tidy dataframe of sensitivity results
#' @return Combined dataframe with deltas and robustness flags
compare_primary_vs_sensitivity <- function(prim, sens) {
  # Join on key identifiers
  # We expect columns: outcome (or feature), exposure, term, visit (if applicable)
  join_cols <- intersect(names(prim), names(sens))
  join_cols <- intersect(join_cols, c("feature", "outcome", "exposure", "term", "visit", "timing"))
  
  comp <- prim %>%
    inner_join(
      sens %>% select(all_of(join_cols), estimate_sens = estimate, std.error_sens = std.error, 
                     p_sens = p.value, q_sens = q.value, n_sens = n, conf.low_sens = conf.low, conf.high_sens = conf.high),
      by = join_cols,
      suffix = c("_prim", "_sens")
    ) %>%
    mutate(
      delta_beta = (estimate_sens - estimate) / estimate,
      abs_delta_beta = abs(delta_beta),
      ci_overlap = !((conf.high_sens < estimate) | (conf.low_sens > estimate)),
      dir_consistent = sign(estimate) == sign(estimate_sens),
      robustness_flag = case_when(
        !dir_consistent ~ "Unstable (Direction)",
        abs_delta_beta > 0.5 ~ "Unstable (Magnitude)",
        abs_delta_beta > 0.2 ~ "Attenuated",
        ci_overlap ~ "Robust",
        TRUE ~ "Stable"
      )
    )
  
  comp
}

#' Run sensitivity LM wrapper
#' @param df Dataframe
#' @param outcome Outcome variable
#' @param exposure Exposure variable
#' @param covars Covariate vector
#' @param filter_expr Optional expression for filtering data (e.g. antibiotic users)
#' @param ... Passed to fit_lm_one
run_sensitivity_lm_one <- function(df, outcome, exposure, covars, filter_expr = NULL, ...) {
  if (!is.null(filter_expr)) {
    df <- df %>% filter(!!rlang::enquo(filter_expr))
  }
  
  # Use the existing fit_lm_one from metabolomics_common.R
  # Assuming fit_lm_one is available in the environment
  fit_lm_one(df, outcome, exposure, covars, ...)
}

#' Summarize sensitivity findings
#' @param comp_df Result from compare_primary_vs_sensitivity
#' @return Summary table of robustness
summarize_sensitivity_results <- function(comp_df) {
  comp_df %>%
    group_by(robustness_flag) %>%
    summarise(
      n = n(),
      mean_abs_delta = mean(abs_delta_beta, na.rm = TRUE),
      .groups = "drop"
    ) %>%
    arrange(desc(n))
}

#' Plot sensitivity comparison
#' @param comp_df Result from compare_primary_vs_sensitivity
#' @param title Plot title
plot_sensitivity_comparison <- function(comp_df, title = "Sensitivity vs Primary Estimates") {
  library(ggplot2)
  ggplot(comp_df, aes(x = estimate, y = estimate_sens, color = robustness_flag)) +
    geom_abline(slope = 1, intercept = 0, linetype = "dashed", color = "grey50") +
    geom_point(alpha = 0.7) +
    theme_minimal() +
    labs(
      x = "Primary Estimate (Beta)",
      y = "Sensitivity Estimate (Beta)",
      color = "Robustness",
      title = title
    )
}
