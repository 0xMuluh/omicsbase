source("recipe_runtime.R")

config <- yaml::read_yaml("study_config.yml")
feature_path <- config$paths$feature_table
metadata_path <- config$paths$metadata
sample_id_column <- config$identifiers$sample_id
feature_id_column <- config$identifiers$feature_id
inventory_parameters <- config$analyses$recipe_parameters[["microbiome.inventory"]] %||% list()
orientation <- inventory_parameters$feature_orientation %||% config$features$orientation %||% "auto"
min_prevalence <- as.numeric(inventory_parameters$min_prevalence %||% 0.10)
min_total_abundance <- as.numeric(inventory_parameters$min_total_abundance %||% 0)

raw_features <- read_study_table(feature_path)
metadata <- NULL

if (inherits(raw_features, "SummarizedExperiment")) {
  if (!requireNamespace("SummarizedExperiment", quietly = TRUE)) {
    stop("Package 'SummarizedExperiment' is required for the uploaded RDS object.")
  }
  assay_matrix <- SummarizedExperiment::assay(raw_features)
  metadata <- as.data.frame(SummarizedExperiment::colData(raw_features), check.names = FALSE)
  metadata$.sample_id <- colnames(assay_matrix)
} else if (is.list(raw_features) && !is.data.frame(raw_features) && !is.null(raw_features$assay)) {
  assay_matrix <- as.matrix(raw_features$assay)
  metadata <- as.data.frame(raw_features$metadata %||% data.frame(), check.names = FALSE)
  if (nrow(metadata) == ncol(assay_matrix)) {
    metadata$.sample_id <- colnames(assay_matrix)
  }
} else {
  raw_features <- as.data.frame(raw_features, check.names = FALSE)
  if (ncol(raw_features) < 2) {
    stop("Feature table must contain an identifier column and at least one measurement column.")
  }

  if (!is.null(metadata_path) && nzchar(metadata_path)) {
    metadata <- as.data.frame(read_study_table(metadata_path), check.names = FALSE)
  }

  first_column <- feature_id_column %||% names(raw_features)[[1]]
  assert_columns(raw_features, first_column, "Feature table")
  identifiers <- as.character(raw_features[[first_column]])
  measurement_data <- raw_features[, setdiff(names(raw_features), first_column), drop = FALSE]
  numeric_measurements <- as_numeric_frame(measurement_data)
  assay_candidate <- as.matrix(numeric_measurements)

  metadata_ids <- character(0)
  if (!is.null(metadata) && nrow(metadata) > 0) {
    sample_id_column <- sample_id_column %||% names(metadata)[[1]]
    assert_columns(metadata, sample_id_column, "Metadata table")
    metadata_ids <- as.character(metadata[[sample_id_column]])
  }

  column_overlap <- sum(colnames(assay_candidate) %in% metadata_ids)
  row_overlap <- sum(identifiers %in% metadata_ids)
  transpose_input <- orientation == "samples_by_features" ||
    (orientation == "auto" && row_overlap > column_overlap)

  if (transpose_input) {
    rownames(assay_candidate) <- identifiers
    assay_matrix <- t(assay_candidate)
  } else {
    rownames(assay_candidate) <- identifiers
    assay_matrix <- assay_candidate
  }
}

storage.mode(assay_matrix) <- "numeric"
if (any(assay_matrix < 0, na.rm = TRUE)) {
  stop("Microbiome abundance input contains negative values.")
}
missing_measurements <- sum(is.na(assay_matrix))
assay_matrix[is.na(assay_matrix)] <- 0
feature_prevalence <- rowMeans(assay_matrix > 0)
feature_totals <- rowSums(assay_matrix)
keep_features <- feature_prevalence >= min_prevalence & feature_totals >= min_total_abundance
if (!any(keep_features)) {
  stop("No features remain after the configured prevalence and abundance filters.")
}
assay_matrix <- assay_matrix[keep_features, , drop = FALSE]

if (is.null(colnames(assay_matrix))) {
  stop("The abundance matrix does not contain sample identifiers.")
}

if (is.null(metadata) || nrow(metadata) == 0) {
  metadata <- data.frame(.sample_id = colnames(assay_matrix), stringsAsFactors = FALSE)
} else {
  sample_id_column <- sample_id_column %||% if (".sample_id" %in% names(metadata)) ".sample_id" else names(metadata)[[1]]
  assert_columns(metadata, sample_id_column, "Metadata table")
  metadata$.sample_id <- as.character(metadata[[sample_id_column]])
}

shared_samples <- intersect(colnames(assay_matrix), metadata$.sample_id)
unmatched_assay <- setdiff(colnames(assay_matrix), metadata$.sample_id)
unmatched_metadata <- setdiff(metadata$.sample_id, colnames(assay_matrix))
if (length(shared_samples) == 0) {
  stop("No sample identifiers overlap between the abundance matrix and metadata.")
}

assay_matrix <- assay_matrix[, shared_samples, drop = FALSE]
metadata <- metadata[match(shared_samples, metadata$.sample_id), , drop = FALSE]
rownames(metadata) <- metadata$.sample_id

sample_totals <- colSums(assay_matrix, na.rm = TRUE)
keep_samples <- is.finite(sample_totals) & sample_totals > 0
assay_matrix <- assay_matrix[, keep_samples, drop = FALSE]
metadata <- metadata[colnames(assay_matrix), , drop = FALSE]
sample_totals <- sample_totals[keep_samples]

relative_abundance <- sweep(assay_matrix, 2, sample_totals, "/")
relative_abundance[!is.finite(relative_abundance)] <- 0
metadata$observed <- colSums(assay_matrix > 0)
metadata$shannon <- apply(relative_abundance, 2, function(values) {
  positive <- values[values > 0]
  -sum(positive * log(positive))
})
metadata$simpson <- apply(relative_abundance, 2, function(values) 1 - sum(values ^ 2))

input_scale <- if (all(abs(sample_totals - 1) < 0.05)) "relative_abundance" else "counts_or_abundance"
analysis_data <- list(
  domain = "microbiome",
  assay = assay_matrix,
  relative_abundance = relative_abundance,
  metadata = metadata,
  feature_ids = rownames(assay_matrix),
  sample_ids = colnames(assay_matrix),
  input_scale = input_scale,
  config = config
)

dir.create("../output/derived", recursive = TRUE, showWarnings = FALSE)
saveRDS(analysis_data, "../output/derived/analysis_data.rds")
write_validation(
  "../output/derived/data_validation.json",
  "passed",
  list(
    sample_alignment = length(shared_samples) > 0,
    nonnegative_abundance = TRUE,
    nonzero_samples = all(keep_samples),
    feature_filter_retained_data = any(keep_features)
  ),
  list(
    domain = "microbiome",
    samples = ncol(assay_matrix),
    features = nrow(assay_matrix),
    filtered_features = sum(!keep_features),
    min_prevalence = min_prevalence,
    min_total_abundance = min_total_abundance,
    input_scale = input_scale,
    missing_measurements_replaced_with_zero = missing_measurements,
    unmatched_assay_samples = unmatched_assay,
    unmatched_metadata_samples = unmatched_metadata
  )
)
