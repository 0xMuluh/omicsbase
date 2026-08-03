source("recipe_runtime.R")

config <- yaml::read_yaml("study_config.yml")
feature_path <- config$paths$feature_table
metadata_path <- config$paths$metadata
sample_id_column <- config$identifiers$sample_id
visit_column <- config$identifiers$visit
grouping_variable <- config$variables$grouping
configured_features <- unlist(config$features$columns %||% character(0), use.names = FALSE)

feature_data <- read_study_table(feature_path)
if (!is.data.frame(feature_data)) {
  stop("Metabolomics deterministic ingestion currently requires a tabular input.")
}
feature_data <- as.data.frame(feature_data, check.names = FALSE)

if (!is.null(metadata_path) && nzchar(metadata_path) && normalizePath(metadata_path) != normalizePath(feature_path)) {
  metadata <- as.data.frame(read_study_table(metadata_path), check.names = FALSE)
} else {
  metadata <- feature_data
}

sample_id_column <- sample_id_column %||% names(metadata)[[1]]
assert_columns(metadata, sample_id_column, "Metadata table")
metadata$.sample_id <- as.character(metadata[[sample_id_column]])

if (anyDuplicated(metadata$.sample_id) && !is.null(visit_column) && visit_column %in% names(metadata)) {
  metadata$.sample_id <- paste(metadata$.sample_id, metadata[[visit_column]], sep = "_")
}
if (anyDuplicated(metadata$.sample_id)) {
  stop("Sample identifiers are not unique; configure a visit/time column for repeated measurements.")
}

if (length(configured_features) > 0) {
  missing_features <- setdiff(configured_features, names(feature_data))
  if (length(missing_features) > 0) {
    stop("Configured metabolite columns are missing: ", paste(missing_features, collapse = ", "))
  }
  feature_columns <- configured_features
} else {
  excluded <- unique(c(
    sample_id_column,
    visit_column,
    grouping_variable,
    unlist(config$variables$covariates %||% character(0), use.names = FALSE)
  ))
  candidates <- setdiff(names(feature_data), excluded)
  numeric_fraction <- vapply(feature_data[candidates], function(value) {
    converted <- suppressWarnings(as.numeric(value))
    mean(is.finite(converted))
  }, numeric(1))
  feature_columns <- candidates[numeric_fraction >= 0.80]
}

if (length(feature_columns) < 2) {
  stop("Fewer than two numeric metabolite features were detected.")
}

feature_sample_ids <- as.character(feature_data[[sample_id_column]])
if (anyDuplicated(feature_sample_ids) && !is.null(visit_column) && visit_column %in% names(feature_data)) {
  feature_sample_ids <- paste(feature_sample_ids, feature_data[[visit_column]], sep = "_")
}
shared_samples <- intersect(metadata$.sample_id, feature_sample_ids)
if (length(shared_samples) == 0) {
  stop("No sample identifiers overlap between metabolite measurements and metadata.")
}

feature_data <- feature_data[match(shared_samples, feature_sample_ids), , drop = FALSE]
metadata <- metadata[match(shared_samples, metadata$.sample_id), , drop = FALSE]
rownames(metadata) <- shared_samples

raw_matrix <- t(as.matrix(as_numeric_frame(feature_data[feature_columns])))
rownames(raw_matrix) <- feature_columns
colnames(raw_matrix) <- shared_samples

transformed_matrix <- t(apply(raw_matrix, 1, function(values) {
  finite_values <- values[is.finite(values)]
  if (length(finite_values) == 0) return(rep(NA_real_, length(values)))
  if (all(finite_values >= 0)) {
    positive <- finite_values[finite_values > 0]
    pseudocount <- if (length(positive)) min(positive) / 2 else 1
    values <- log10(values + pseudocount)
  }
  center <- mean(values, na.rm = TRUE)
  spread <- stats::sd(values, na.rm = TRUE)
  if (!is.finite(spread) || spread == 0) return(rep(NA_real_, length(values)))
  (values - center) / spread
}))
rownames(transformed_matrix) <- feature_columns
colnames(transformed_matrix) <- shared_samples

analysis_data <- list(
  domain = "metabolomics",
  assay = transformed_matrix,
  raw_assay = raw_matrix,
  metadata = metadata,
  feature_ids = feature_columns,
  sample_ids = shared_samples,
  config = config
)

dir.create("../output/derived", recursive = TRUE, showWarnings = FALSE)
saveRDS(analysis_data, "../output/derived/analysis_data.rds")
mae_written <- FALSE
if (
  requireNamespace("MultiAssayExperiment", quietly = TRUE) &&
  requireNamespace("SummarizedExperiment", quietly = TRUE) &&
  requireNamespace("S4Vectors", quietly = TRUE)
) {
  metabolomics_experiment <- SummarizedExperiment::SummarizedExperiment(
    assays = list(mbo = transformed_matrix),
    colData = S4Vectors::DataFrame(metadata)
  )
  mae <- MultiAssayExperiment::MultiAssayExperiment(
    experiments = list(metabolomics = metabolomics_experiment)
  )
  saveRDS(mae, "../output/derived/MAE.rds")
  mae_written <- TRUE
}
write_validation(
  "../output/derived/data_validation.json",
  "passed",
  list(
    unique_sample_ids = !anyDuplicated(shared_samples),
    sample_alignment = length(shared_samples) > 0,
    multiple_features = length(feature_columns) >= 2,
    mae_contract_written = mae_written
  ),
  list(
    domain = "metabolomics",
    samples = length(shared_samples),
    features = length(feature_columns),
    grouping_variable = grouping_variable,
    inferred_feature_columns = length(configured_features) == 0
  )
)
