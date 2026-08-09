source("code/00_setup.R")

plan <- read_analysis_plan()
ensure_dir(plan$paths$derived_dir)
ensure_dir(plan$paths$results_dir)

sample_id <- assert_filled(plan$identifiers$sample_id, "identifiers$sample_id")
input_kind <- tolower(plan$preprocessing$input_kind %||% "feature_table")
metadata_sheet <- plan$preprocessing$metadata_sheet %||% 1
metadata <- read_table_file(assert_filled(plan$paths$metadata, "paths$metadata"), sheet = metadata_sheet)
require_columns(metadata, c(sample_id), "metadata")
metadata[[sample_id]] <- as.character(metadata[[sample_id]])
validate_unique(metadata[[sample_id]], "metadata sample IDs")

cleanup_regex <- plan$preprocessing$sample_name_cleanup_regex %||% ""
cleanup_replacement <- plan$preprocessing$sample_name_cleanup_replacement %||% ""
feature_id_config <- plan$preprocessing$feature_id_column %||% plan$identifiers$feature_id
if (blank_or_marker(feature_id_config)) feature_id_config <- plan$identifiers$feature_id

read_feature_rows <- function(path, feature_id = NULL) {
  dat <- read_table_file(path)
  if (ncol(dat) < 2L) stop("Feature/profile table must have at least one feature column and one sample column", call. = FALSE)
  if (blank_or_marker(feature_id) || !feature_id %in% names(dat)) feature_id <- names(dat)[[1]]
  names(dat)[names(dat) == feature_id][1] <- "feature_id"
  dat <- dat[!is.na(dat$feature_id) & nzchar(as.character(dat$feature_id)), , drop = FALSE]
  dat
}

terminal_rank_filter <- function(features, rank, enabled = TRUE, input_kind = "feature_table") {
  if (!enabled || blank_or_marker(rank)) return(rep(TRUE, length(features)))
  rank <- tolower(as.character(rank))
  prefixes <- c(kingdom = "k__", phylum = "p__", class = "c__", order = "o__", family = "f__", genus = "g__", species = "s__", strain = "t__")
  if (!rank %in% names(prefixes)) return(rep(TRUE, length(features)))
  idx <- match(rank, names(prefixes))
  keep <- grepl(prefixes[[rank]], features, fixed = TRUE)
  if (idx < length(prefixes)) {
    lower <- prefixes[(idx + 1L):length(prefixes)]
    lower_hit <- Reduce(`|`, lapply(lower, grepl, x = features, fixed = TRUE))
    keep <- keep & !lower_hit
  }
  if (!any(keep)) {
    if (input_kind == "metaphlan_profile") {
      stop(sprintf("No MetaPhlAn features matched terminal rank '%s'. Check taxonomy_rank or disable rank_filter_terminal.", rank), call. = FALSE)
    }
    return(rep(TRUE, length(features)))
  }
  keep
}

make_samples_by_features <- function(features, orientation, sample_id, feature_id = NULL, input_kind = "feature_table") {
  orientation <- tolower(orientation)
  if (orientation == "samples_as_rows") {
    require_columns(features, c(sample_id), "feature table")
    features[[sample_id]] <- clean_sample_ids(features[[sample_id]], cleanup_regex, cleanup_replacement)
    validate_unique(features[[sample_id]], "feature table sample IDs")
    feature_cols_original <- setdiff(names(features), sample_id)
    feature_cols_safe <- safe_feature_columns(feature_cols_original)
    names(features)[match(feature_cols_original, names(features))] <- feature_cols_safe
    feature_data <- features[c(sample_id, feature_cols_safe)]
  } else if (orientation == "features_as_rows") {
    if (blank_or_marker(feature_id)) feature_id <- names(features)[[1]]
    require_columns(features, c(feature_id), "feature table")
    feature_cols_original <- as.character(features[[feature_id]])
    sample_cols <- setdiff(names(features), feature_id)
    sample_cols_clean <- clean_sample_ids(sample_cols, cleanup_regex, cleanup_replacement)
    if (anyDuplicated(sample_cols_clean)) stop("Sample cleanup created duplicate feature-table sample names", call. = FALSE)
    rank_keep <- terminal_rank_filter(feature_cols_original, plan$features$taxonomy_rank, isTRUE(plan$preprocessing$rank_filter_terminal), input_kind)
    features <- features[rank_keep, , drop = FALSE]
    feature_cols_original <- feature_cols_original[rank_keep]
    feature_cols_safe <- safe_feature_columns(feature_cols_original)
    value_block <- features[sample_cols]
    for (nm in names(value_block)) value_block[[nm]] <- suppressWarnings(as.numeric(value_block[[nm]]))
    feature_matrix <- as.data.frame(t(as.matrix(value_block)), check.names = FALSE, stringsAsFactors = FALSE)
    names(feature_matrix) <- feature_cols_safe
    feature_matrix[[sample_id]] <- sample_cols_clean
    rownames(feature_matrix) <- NULL
    feature_data <- feature_matrix[c(sample_id, feature_cols_safe)]
  } else {
    stop("features$feature_orientation must be samples_as_rows or features_as_rows", call. = FALSE)
  }
  list(data = feature_data, original = feature_cols_original, safe = feature_cols_safe)
}

read_existing_object <- function(path, kind) {
  obj <- readRDS(path)
  if (kind == "tree_summarized_experiment_rds") {
    if (!requireNamespace("SummarizedExperiment", quietly = TRUE)) stop("SummarizedExperiment is required to read TSE/SE objects", call. = FALSE)
    assay_name <- plan$features$assay_name %||% ""
    assay_names <- SummarizedExperiment::assayNames(obj)
    if (blank_or_marker(assay_name) || !assay_name %in% assay_names) assay_name <- assay_names[[1]]
    mat <- t(as.matrix(SummarizedExperiment::assay(obj, assay_name)))
    md <- as.data.frame(SummarizedExperiment::colData(obj), stringsAsFactors = FALSE)
    md[[sample_id]] <- rownames(md)
    return(list(matrix = mat, metadata = md, original_object = obj, source_assay = assay_name))
  }
  if (kind == "phyloseq_rds") {
    if (!requireNamespace("phyloseq", quietly = TRUE)) stop("phyloseq is required to read phyloseq objects", call. = FALSE)
    otu <- as(phyloseq::otu_table(obj), "matrix")
    if (phyloseq::taxa_are_rows(obj)) otu <- t(otu)
    md <- as.data.frame(phyloseq::sample_data(obj), stringsAsFactors = FALSE)
    md[[sample_id]] <- rownames(md)
    return(list(matrix = otu, metadata = md, original_object = obj, source_assay = "otu_table"))
  }
  stop(sprintf("Unsupported existing object input_kind: %s", kind), call. = FALSE)
}

feature_table_path <- assert_filled(plan$paths$feature_table, "paths$feature_table")
original_object <- NULL
source_assay <- NA_character_
effective_orientation <- plan$features$feature_orientation

if (input_kind %in% c("tree_summarized_experiment_rds", "phyloseq_rds")) {
  imported <- read_existing_object(feature_table_path, input_kind)
  mat <- imported$matrix
  metadata <- imported$metadata
  require_columns(metadata, c(sample_id), "object metadata")
  metadata[[sample_id]] <- as.character(metadata[[sample_id]])
  validate_unique(metadata[[sample_id]], "object metadata sample IDs")
  feature_cols_original <- colnames(mat)
  feature_cols_safe <- safe_feature_columns(feature_cols_original)
  colnames(mat) <- feature_cols_safe
  feature_data <- data.frame(sample_id_tmp = rownames(mat), mat, check.names = FALSE)
  names(feature_data)[1] <- sample_id
  original_object <- imported$original_object
  source_assay <- imported$source_assay
  effective_orientation <- "object_assay"
} else {
  if (input_kind %in% c("metaphlan_profile", "humann_profile")) {
    raw_features <- read_feature_rows(feature_table_path, feature_id_config)
    orientation <- "features_as_rows"
    feature_id <- "feature_id"
  } else {
    raw_features <- read_table_file(feature_table_path)
    orientation <- plan$features$feature_orientation
    feature_id <- if (blank_or_marker(feature_id_config)) plan$identifiers$feature_id else feature_id_config
  }
  effective_orientation <- orientation
  shaped <- make_samples_by_features(raw_features, orientation, sample_id, feature_id, input_kind)
  feature_data <- shaped$data
  feature_cols_original <- shaped$original
  feature_cols_safe <- shaped$safe
}

remove_patterns <- plan$preprocessing$remove_feature_patterns %||% character(0)
if (length(remove_patterns)) {
  remove_hit <- Reduce(`|`, lapply(remove_patterns, grepl, x = feature_cols_original, ignore.case = TRUE))
  if (any(remove_hit)) {
    feature_data <- feature_data[, c(sample_id, feature_cols_safe[!remove_hit]), drop = FALSE]
    feature_cols_original <- feature_cols_original[!remove_hit]
    feature_cols_safe <- feature_cols_safe[!remove_hit]
  }
}

for (col in feature_cols_safe) feature_data[[col]] <- suppressWarnings(as.numeric(feature_data[[col]]))
if (any(feature_data[feature_cols_safe] < 0, na.rm = TRUE)) stop("Microbiome feature values must be non-negative for this template", call. = FALSE)

feature_samples <- unique(as.character(feature_data[[sample_id]]))
metadata_samples <- unique(as.character(metadata[[sample_id]]))
sample_alignment <- data.frame(
  sample_id = sort(unique(c(feature_samples, metadata_samples))),
  in_metadata = sort(unique(c(feature_samples, metadata_samples))) %in% metadata_samples,
  in_features = sort(unique(c(feature_samples, metadata_samples))) %in% feature_samples,
  stringsAsFactors = FALSE
)
write_tsv(sample_alignment, file.path(plan$paths$results_dir, "sample_alignment.tsv"))
if (!isTRUE(plan$preprocessing$allow_unmatched_samples) && any(!sample_alignment$in_metadata | !sample_alignment$in_features)) {
  stop("Sample IDs do not align between metadata and feature/profile table. See results/sample_alignment.tsv", call. = FALSE)
}

joined <- merge(metadata, feature_data, by = sample_id, all = FALSE, sort = FALSE)
if (!nrow(joined)) stop("No rows remain after joining metadata and feature table by sample ID", call. = FALSE)

mat <- as.matrix(joined[feature_cols_safe])
rownames(mat) <- joined[[sample_id]]
prevalence <- colMeans(mat > 0, na.rm = TRUE)
abundance <- colMeans(mat, na.rm = TRUE)
keep <- prevalence >= plan$features$prevalence_min & abundance >= plan$features$abundance_min
if (!any(keep)) stop("No features remain after prevalence/abundance filtering", call. = FALSE)
filtered_features <- feature_cols_safe[keep]
filtered_mat <- mat[, filtered_features, drop = FALSE]

feature_map <- data.frame(
  feature = feature_cols_original,
  feature_column = feature_cols_safe,
  retained = feature_cols_safe %in% filtered_features,
  prevalence = prevalence,
  mean_abundance = abundance,
  stringsAsFactors = FALSE
)

read_functional_table <- function(path, name) {
  if (blank_or_marker(path)) return(NULL)
  dat <- read_feature_rows(path, feature_id = NULL)
  shaped <- make_samples_by_features(dat, "features_as_rows", sample_id, "feature_id", "humann_profile")
  fd <- shaped$data
  for (col in shaped$safe) fd[[col]] <- suppressWarnings(as.numeric(fd[[col]]))
  joined_f <- merge(joined[, setdiff(names(joined), feature_cols_safe), drop = FALSE], fd, by = sample_id, all = FALSE, sort = FALSE)
  mat_f <- as.matrix(joined_f[shaped$safe])
  rownames(mat_f) <- joined_f[[sample_id]]
  list(name = name, matrix = mat_f, features = shaped$original)
}
functional_matrices <- list()
for (nm in names(plan$paths$functional_tables %||% list())) {
  item <- read_functional_table(plan$paths$functional_tables[[nm]], nm)
  if (!is.null(item)) functional_matrices[[nm]] <- item
}

sample_summary <- data.frame(
  metric = c("metadata_rows", "feature_table_samples", "joined_samples", "metadata_only_samples", "feature_only_samples"),
  value = c(nrow(metadata), length(feature_samples), nrow(joined), sum(sample_alignment$in_metadata & !sample_alignment$in_features), sum(sample_alignment$in_features & !sample_alignment$in_metadata)),
  stringsAsFactors = FALSE
)
feature_summary <- data.frame(
  metric = c("features_before_filter", "features_after_filter", "prevalence_min", "abundance_min"),
  value = c(length(feature_cols_safe), length(filtered_features), plan$features$prevalence_min, plan$features$abundance_min),
  stringsAsFactors = FALSE
)
preprocessing_summary <- data.frame(
  field = c("input_kind", "input_scale", "taxonomy_rank", "source_assay", "sample_cleanup_regex", "feature_orientation", "functional_tables"),
  value = c(input_kind, plan$features$input_scale, plan$features$taxonomy_rank, source_assay, cleanup_regex, effective_orientation, paste(names(functional_matrices), collapse = ",")),
  stringsAsFactors = FALSE
)

object <- list(
  plan = plan,
  metadata = joined[, setdiff(names(joined), feature_cols_safe), drop = FALSE],
  feature_matrix = filtered_mat,
  feature_map = feature_map,
  sample_alignment = sample_alignment,
  sample_summary = sample_summary,
  feature_summary = feature_summary,
  preprocessing_summary = preprocessing_summary,
  functional_matrices = functional_matrices,
  original_object = original_object
)

saveRDS(object, file.path(plan$paths$derived_dir, "microbiome_analysis_data.rds"))
write_tsv(sample_summary, file.path(plan$paths$results_dir, "sample_summary.tsv"))
write_tsv(feature_summary, file.path(plan$paths$results_dir, "feature_summary.tsv"))
write_tsv(feature_map, file.path(plan$paths$results_dir, "feature_map.tsv"))
write_tsv(preprocessing_summary, file.path(plan$paths$results_dir, "preprocessing_summary.tsv"))
append_decision("input_kind", input_kind, "Selected parser for raw microbiome input", "code/01_prepare_data.R")
append_decision("feature_filter", paste(plan$features$prevalence_min, plan$features$abundance_min, sep = ","), "Applied prevalence and abundance filters", "code/01_prepare_data.R")
append_decision("sample_alignment", sprintf("metadata_only=%s;feature_only=%s", sum(sample_alignment$in_metadata & !sample_alignment$in_features), sum(sample_alignment$in_features & !sample_alignment$in_metadata)), "Checked sample ID alignment", "code/01_prepare_data.R")
message("Wrote derived/microbiome_analysis_data.rds and preprocessing summaries")
