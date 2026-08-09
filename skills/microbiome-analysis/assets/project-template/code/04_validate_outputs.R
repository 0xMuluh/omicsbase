source("code/00_setup.R")

plan <- read_analysis_plan()
expected <- c(
  file.path(plan$paths$derived_dir, "microbiome_analysis_data.rds"),
  file.path(plan$paths$results_dir, "sample_summary.tsv"),
  file.path(plan$paths$results_dir, "feature_summary.tsv"),
  file.path(plan$paths$report_dir, "code", "_quarto.yml"),
  file.path(plan$paths$report_dir, "code", "index.qmd"),
  file.path(plan$paths$report_dir, "code", "analysis-plan.qmd"),
  file.path(plan$paths$report_dir, "code", "data-summary.qmd"),
  file.path(plan$paths$report_dir, "code", "diversity-results.qmd"),
  file.path(plan$paths$report_dir, "code", "differential-abundance.qmd"),
  file.path(plan$paths$report_dir, "code", "diagnostics.qmd")
)
exists_flag <- file.exists(expected)
validation <- data.frame(
  artifact = expected,
  exists = exists_flag,
  stringsAsFactors = FALSE
)
write_tsv(validation, file.path(plan$paths$results_dir, "validation_summary.tsv"))
if (!all(exists_flag)) {
  stop("Validation found missing required artifacts; see results/validation_summary.tsv", call. = FALSE)
}
message("Validation passed; wrote results/validation_summary.tsv")
