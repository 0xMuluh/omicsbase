#!/usr/bin/env Rscript

suppressPackageStartupMessages(library(dplyr))

source("report_utils.R")
source("shared/methods_common.R")

rds_path <- "../output/results/targeted/prenatal_diet_study2_results.rds"
stopifnot(
  "Results RDS not found. Run code/prenatal_diet_analysis.R from the repo root." =
    file.exists(rds_path)
)

mae_path <- "../data/MAE_original.rds"
stopifnot(
  "MAE_original.rds not found at data/MAE_original.rds." =
    file.exists(mae_path)
)

results <- readRDS(rds_path)
required_keys <- c(
  "age_specific",
  "longitudinal",
  "age_specific_sensitivity",
  "longitudinal_sensitivity",
  "model_status"
)
missing_keys <- setdiff(required_keys, names(results))
if (length(missing_keys) > 0) {
  stop("Results RDS is missing required keys: ", paste(missing_keys, collapse = ", "))
}

views <- prepare_prenatal_diet_result_views(results)
if (nrow(views$early_all) == 0L || nrow(views$late_all) == 0L) {
  stop("Age-specific early or late result views are empty.")
}

inventory_tbl <- build_result_inventory_prenatal_diet(results)
if (nrow(inventory_tbl) != 5L) {
  stop("Expected 5 inventory rows, found ", nrow(inventory_tbl))
}

expected_components <- c(
  COMPONENT_LABELS$q1_early,
  COMPONENT_LABELS$q2_late,
  COMPONENT_LABELS$q3_mutual_early,
  COMPONENT_LABELS$q3_mutual_late,
  COMPONENT_LABELS$q4_longitudinal
)
if (!identical(sort(inventory_tbl$component), sort(expected_components))) {
  stop("Inventory component labels do not match canonical COMPONENT_LABELS.")
}

expected_rows <- c(
  nrow(views$early_all),
  nrow(views$late_all),
  nrow(views$mutual_early_all),
  nrow(views$mutual_late_all),
  nrow(views$long_res)
)
if (!identical(inventory_tbl$result_rows, expected_rows)) {
  stop(
    "Inventory result_rows do not match independent nrow() checks: ",
    paste(inventory_tbl$result_rows, collapse = ", "),
    " vs ",
    paste(expected_rows, collapse = ", ")
  )
}

long_res <- views$long_res
if (is.null(long_res) || nrow(long_res) == 0L) {
  stop("Longitudinal (Q7) results are empty.")
}
if (!("timing" %in% names(long_res))) {
  stop("Longitudinal results are missing the timing column.")
}

age_specific_features <- unique(views$early_all$feature)
long_features <- unique(long_res$feature)
if (any(is_ratio_feature(long_features))) {
  stop("Longitudinal results include ratio features; Q7 should use track = \"metabolites\" only.")
}
if (!setequal(long_features, age_specific_features)) {
  only_long <- setdiff(long_features, age_specific_features)
  only_age <- setdiff(age_specific_features, long_features)
  stop(
    "Longitudinal feature set does not match age-specific Q4/Q5 panel. ",
    "Only in Q7: ", length(only_long), "; only in Q4/Q5: ", length(only_age)
  )
}

expected_long_rows <- length(unique(interaction(long_res$exposure, long_res$timing))) *
  length(long_features) * 2L
if (nrow(long_res) != expected_long_rows) {
  stop(
    "Unexpected longitudinal row count: ", nrow(long_res),
    " (expected ", expected_long_rows, " = exposures x features x 2 terms)"
  )
}

signal_tbl <- build_signal_summary_prenatal_diet(results)
if (nrow(signal_tbl) < 5L) {
  stop("Signal summary should include Q7 timing rows; found ", nrow(signal_tbl))
}

inventory_overview <- build_result_inventory_prenatal_diet(results)
inventory_s4 <- build_result_inventory_prenatal_diet(results)
if (!identical(inventory_overview, inventory_s4)) {
  stop("Study overview and supplementary inventory builders diverge.")
}

message("Prenatal diet results validation passed: ", rds_path)
message("  inventory rows: ", nrow(inventory_tbl))
message("  longitudinal rows: ", nrow(long_res))
message("  signal summary rows: ", nrow(signal_tbl))
