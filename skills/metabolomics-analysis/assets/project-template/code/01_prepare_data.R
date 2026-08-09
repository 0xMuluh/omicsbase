source("code/00_setup.R")

plan <- read_analysis_plan()
ensure_dir(plan$paths$derived_dir)
ensure_dir(plan$paths$results_dir)

metadata <- read_table_file(assert_filled(plan$paths$metadata, "paths$metadata"), plan$data$missing_values)
features <- read_table_file(assert_filled(plan$paths$features, "paths$features"), plan$data$missing_values)

sample_id <- assert_filled(plan$identifiers$sample_id, "identifiers$sample_id")
subject_id <- plan$identifiers$subject_id
visit <- plan$identifiers$visit
require_columns(metadata, c(sample_id), "metadata")

orientation <- assert_filled(plan$data$feature_orientation, "data$feature_orientation")
if (orientation == "features_as_columns") {
  require_columns(features, c(sample_id), "feature table")
  non_feature <- unique(c(sample_id, subject_id, visit))
  non_feature <- non_feature[!vapply(non_feature, optional_marker, logical(1))]
  feature_cols_original <- setdiff(names(features), non_feature)
  feature_cols_safe <- safe_feature_columns(feature_cols_original)
  names(features)[match(feature_cols_original, names(features))] <- feature_cols_safe
  features_wide <- features
} else if (orientation == "samples_as_columns") {
  feature_id <- assert_filled(plan$identifiers$feature_id, "identifiers$feature_id")
  require_columns(features, c(feature_id), "feature table")
  sample_cols <- setdiff(names(features), feature_id)
  feature_cols_original <- as.character(features[[feature_id]])
  feature_cols_safe <- safe_feature_columns(feature_cols_original)
  feature_matrix <- as.data.frame(t(features[sample_cols]), check.names = FALSE, stringsAsFactors = FALSE)
  names(feature_matrix) <- feature_cols_safe
  feature_matrix[[sample_id]] <- rownames(feature_matrix)
  rownames(feature_matrix) <- NULL
  features_wide <- feature_matrix[c(sample_id, feature_cols_safe)]
} else {
  stop("data$feature_orientation must be features_as_columns or samples_as_columns", call. = FALSE)
}

for (col in feature_cols_safe) {
  features_wide[[col]] <- suppressWarnings(as.numeric(features_wide[[col]]))
}

analysis_data <- merge(metadata, features_wide, by = sample_id, all = FALSE)
if (!nrow(analysis_data)) {
  stop("No rows remain after joining metadata and metabolite features by sample ID", call. = FALSE)
}

feature_map <- data.frame(
  feature = feature_cols_original,
  feature_column = feature_cols_safe,
  stringsAsFactors = FALSE
)

data_summary <- data.frame(
  metric = c("metadata_rows", "feature_table_rows", "joined_rows", "features_available"),
  value = c(nrow(metadata), nrow(features), nrow(analysis_data), length(feature_cols_safe)),
  stringsAsFactors = FALSE
)

object <- list(
  plan = plan,
  metadata = metadata,
  analysis_data = analysis_data,
  feature_map = feature_map,
  data_summary = data_summary
)

saveRDS(object, file.path(plan$paths$derived_dir, "metabolomics_analysis_data.rds"))
write_tsv(data_summary, file.path(plan$paths$results_dir, "data_summary.tsv"))
append_decision("feature_orientation", orientation, "Used to reshape metabolite table", "code/01_prepare_data.R")
append_decision("sample_join", sample_id, "Joined metadata and metabolite features by sample ID", "code/01_prepare_data.R")
message("Wrote derived/metabolomics_analysis_data.rds and results/data_summary.tsv")
