source("code/00_setup.R")

plan <- read_analysis_plan()
result_path <- file.path(plan$paths$results_dir, "metabolomics_results.rds")
if (!file.exists(result_path)) stop("Result object is missing. Run code/02_fit_models.R first.", call. = FALSE)
obj <- readRDS(result_path)
required_components <- c("plan", "model_specification", "feature_map", "data_summary", "results", "model_status")
missing_components <- setdiff(required_components, names(obj))
required_result_cols <- c("analysis_id", "scenario", "model_family", "feature", "exposure", "term", "estimate", "p.value", "q.value", "n", "status")
missing_result_cols <- if (is.data.frame(obj$results) && nrow(obj$results)) setdiff(required_result_cols, names(obj$results)) else character(0)
validation <- data.frame(
  check = c("missing_components", "missing_result_columns", "n_result_rows", "n_status_rows"),
  value = c(
    paste(missing_components, collapse = ","),
    paste(missing_result_cols, collapse = ","),
    if (is.data.frame(obj$results)) nrow(obj$results) else NA_integer_,
    if (is.data.frame(obj$model_status)) nrow(obj$model_status) else NA_integer_
  ),
  stringsAsFactors = FALSE
)
write_tsv(validation, file.path(plan$paths$results_dir, "validation_summary.tsv"))
if (length(missing_components) || length(missing_result_cols)) {
  stop("Validation found missing result-object fields; see results/validation_summary.tsv", call. = FALSE)
}
message("Validation passed; wrote results/validation_summary.tsv")
