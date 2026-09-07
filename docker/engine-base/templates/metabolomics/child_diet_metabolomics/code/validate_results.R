#!/usr/bin/env Rscript

suppressPackageStartupMessages(library(dplyr))

source("report_utils.R")
source("../../scripts/methods_common.R")

rds_path <- "../output/results/targeted_v2/child_diet_study1_results_v2.rds"
stopifnot(
  "Results RDS not found. Run code/child_diet_analysis.R from the repo root." =
    file.exists(rds_path)
)

results <- readRDS(rds_path)
required_keys <- c(
  "q1", "q2", "q3", "q4",
  "q1_sensitivity", "q2_sensitivity", "q3_sensitivity", "q4_sensitivity",
  "model_status"
)
missing_keys <- setdiff(required_keys, names(results))
if (length(missing_keys) > 0) {
  stop("Results RDS is missing required keys: ", paste(missing_keys, collapse = ", "))
}

q4_split <- split_q4_terms(results$q4)
if (nrow(q4_split$main) == 0L || nrow(q4_split$interaction) == 0L) {
  stop("Q4 main or interaction term split is empty. Check Q4 term labels in the RDS.")
}

inventory_tbl <- build_result_inventory(results)
if (nrow(inventory_tbl) != 10L) {
  stop("Expected 10 inventory rows, found ", nrow(inventory_tbl))
}

expected_counts <- bind_rows(
  add_exposure_family(results$q1) %>% count(exposure_family, name = "n"),
  add_exposure_family(results$q2) %>% count(exposure_family, name = "n"),
  add_exposure_family(results$q3) %>% count(exposure_family, name = "n"),
  add_exposure_family(q4_split$main) %>% count(exposure_family, name = "n"),
  add_exposure_family(q4_split$interaction) %>% count(exposure_family, name = "n")
)

inventory_components <- sort(unique(inventory_tbl$component))
expected_components <- sort(c(
  COMPONENT_LABELS$concurrent_2y,
  COMPONENT_LABELS$concurrent_56y,
  COMPONENT_LABELS$prospective,
  COMPONENT_LABELS$rm_main,
  COMPONENT_LABELS$rm_interaction
))
if (!identical(inventory_components, expected_components)) {
  stop("Inventory component labels do not match canonical COMPONENT_LABELS.")
}

signal_tbl <- build_signal_summary(results)
if (!identical(
  inventory_tbl %>% arrange(component, exposure_family) %>% pull(result_rows),
  signal_tbl %>% arrange(component, exposure_family) %>% pull(result_rows)
)) {
  stop("Inventory result_rows do not match signal summary result_rows.")
}

q1_features <- unique(results$q1$feature)
q3_features <- unique(results$q3$feature)
q4_features <- unique(results$q4$feature)
if (any(is_ratio_feature(q4_features))) {
  stop("Q4 repeated-measures results include ratio features; expected metabolites track only.")
}
if (!all(q4_features %in% q1_features)) {
  stop("Q4 feature set is not a subset of the Q1 primary metabolite panel.")
}
if (length(q4_features) != length(q1_features)) {
  stop(
    "Q4 feature count (", length(q4_features), ") does not match Q1 panel (",
    length(q1_features), ")."
  )
}

message("Child diet results validation passed: ", rds_path)
message("  inventory rows: ", nrow(inventory_tbl))
message("  q4 main rows: ", nrow(q4_split$main))
message("  q4 interaction rows: ", nrow(q4_split$interaction))
