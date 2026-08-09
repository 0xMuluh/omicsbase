#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(dplyr)
  library(readxl)
  library(stringr)
  library(MultiAssayExperiment)
  library(TreeSummarizedExperiment)
  library(S4Vectors)
  library(haven)
})

merge_measured_twice <- function(data,
                                 study_id,
                                 old_study_visit,
                                 new_study_visit,
                                 no_order) {
  key_id <- trimws(as.character(data[["StudyID"]]))
  key_visit <- trimws(as.character(data[["StudyVisit"]]))

  idx <- which(key_id %in% study_id & key_visit %in% old_study_visit)
  if (length(idx) != 2) {
    stop(
      "Duplicate-measurement merge expected exactly two rows for StudyID ",
      paste(study_id, collapse = "|"),
      " and StudyVisit ",
      paste(old_study_visit, collapse = "|"),
      "; found ", length(idx), "."
    )
  }

  feature_cols <- setdiff(colnames(data), c("NoOrder", "StudyID", "StudyVisit"))

  tmp <- data[idx, feature_cols, drop = FALSE]
  tmp[] <- lapply(tmp, function(x) suppressWarnings(as.numeric(as.character(x))))

  tmp_avg <- vapply(tmp, function(x) {
    if (all(is.na(x))) {
      NA_real_
    } else {
      mean(x, na.rm = TRUE)
    }
  }, numeric(1))

  new_row <- data[idx[1], , drop = FALSE]
  new_row[,] <- NA

  new_row$NoOrder <- if (is.numeric(data$NoOrder)) {
    suppressWarnings(as.numeric(no_order))
  } else {
    as.character(no_order)
  }
  new_row$StudyID <- as.character(study_id)
  new_row$StudyVisit <- as.character(new_study_visit)

  for (nm in names(tmp_avg)) {
    new_row[[nm]] <- tmp_avg[[nm]]
  }

  bind_rows(data[-idx, , drop = FALSE], new_row)
}

prepare_clinical_data <- function(path) {
  pheno_data <- as.data.frame(haven::read_sav(path))
  
  # Normalize all column names by trimming spaces
  colnames(pheno_data) <- trimws(colnames(pheno_data))
  
  if (!("StudyID" %in% names(pheno_data))) {
    stop("Clinical file must contain the required column 'StudyID'. No alternate ID column is allowed.")
  }

  pheno_data$StudyID <- trimws(as.character(pheno_data$StudyID))
  pheno_data <- pheno_data[!is.na(pheno_data$StudyID) & pheno_data$StudyID != "", , drop = FALSE]

  if (anyDuplicated(pheno_data$StudyID) > 0) {
    pheno_data <- pheno_data[!duplicated(pheno_data$StudyID), , drop = FALSE]
  }

  # Derive dichotomized Mode of Delivery (1-2: Vaginal, 3-5: Cesarean)
  if ("MModeOfDeliv" %in% names(pheno_data)) {
    pheno_data$MModeOfDeliv.1 <- factor(
      ifelse(pheno_data$MModeOfDeliv %in% c(1, 2), "Vaginal delivery",
             ifelse(pheno_data$MModeOfDeliv %in% c(3, 4, 5), "Cesarean", NA_character_)),
      levels = c("Vaginal delivery", "Cesarean")
    )
  }

  # Premature is SPSS-coded as 2 = yes in the current clinical file.
  # Derive HINE optimality (Optimal >= 74, Suboptimal < 74) after excluding premature children.
  if ("Hammersmith6" %in% names(pheno_data) && "Premature" %in% names(pheno_data)) {
    is_premature <- !is.na(pheno_data$Premature) & pheno_data$Premature == 2
    pheno_data$HINE_optimal <- factor(
      ifelse(is_premature, NA_character_,
             ifelse(pheno_data$Hammersmith6 >= 74, "Optimal", "Suboptimal")),
      levels = c("Optimal", "Suboptimal")
    )
  }

  rownames(pheno_data) <- pheno_data$StudyID

  list(
    data = pheno_data,
    labels = attr(pheno_data, "variable.labels")
  )
}

prepare_metabolomics_data <- function(path) {
  raw_data <- read_xlsx(path, sheet = "SPSS", na = c("NA", "TAG")) %>%
    as.data.frame(check.names = FALSE)

  required_cols <- c("StudyID", "Study visit no", "NoOrder")
  missing_cols <- setdiff(required_cols, names(raw_data))
  if (length(missing_cols) > 0) {
    stop(
      "Metabolomics SPSS sheet missing required column(s): ",
      paste(missing_cols, collapse = ", "),
      ". No alternate column names are allowed."
    )
  }

  colnames(raw_data)[colnames(raw_data) == "Study visit no"] <- "StudyVisit"

  mbo_data <- raw_data

  mbo_info <- read_xlsx(path, sheet = "Biomarker annotations") %>%
    as.data.frame(check.names = FALSE)

  if (!("Excel column name" %in% names(mbo_info))) {
    stop("Biomarker annotations sheet must contain 'Excel column name'.")
  }

  rownames(mbo_info) <- mbo_info[["Excel column name"]]

  # Pre-specified duplicate measurement resolution from manual sample review.
  merge_plan <- list(
    list(study_id = "P1003", old_visit = "6", new_visit = "6", no_order = "6"),
    list(study_id = "M3162", old_visit = c("4", "4b"), new_visit = "4", no_order = "367"),
    list(study_id = "P1023", old_visit = c("5", "5b"), new_visit = "5", no_order = "56"),
    list(study_id = "P1092", old_visit = c("5", "5b?"), new_visit = "5", no_order = "208")
  )

  for (m in merge_plan) {
    mbo_data <- merge_measured_twice(
      data = mbo_data,
      study_id = m$study_id,
      old_study_visit = m$old_visit,
      new_study_visit = m$new_visit,
      no_order = m$no_order
    )
  }

  feature_cols <- setdiff(colnames(mbo_data), c("NoOrder", "StudyID", "StudyVisit"))
  mbo_data[feature_cols] <- lapply(
    mbo_data[feature_cols],
    function(x) suppressWarnings(as.numeric(as.character(x)))
  )

  mbo_data$StudyID <- trimws(as.character(mbo_data$StudyID))
  mbo_data$StudyVisit <- trimws(as.character(mbo_data$StudyVisit))

  list(
    data = mbo_data,
    info = mbo_info
  )
}

make_col_data <- function(sample_ids, pheno_data, visit_levels) {
  clean_ids <- sub("^visit_[0-9]+\\.", "", sample_ids)
  base_id <- sub("_[^_]*$", "", clean_ids)
  visit_num <- sub("^.*_", "", clean_ids)

  col_data <- pheno_data[base_id, , drop = FALSE]
  rownames(col_data) <- sample_ids
  col_data$Visit <- factor(paste0("visit_", visit_num), levels = paste0("visit_", visit_levels))

  DataFrame(col_data, check.names = FALSE)
}

make_tse <- function(assay_df, row_data, pheno_data, visit_levels) {
  sample_ids <- rownames(assay_df)
  col_data <- make_col_data(sample_ids, pheno_data, visit_levels)
  row_data_subset <- row_data[colnames(assay_df), , drop = FALSE]

  TreeSummarizedExperiment(
    assays = SimpleList(mbo = t(as.matrix(assay_df))),
    colData = col_data,
    rowData = DataFrame(row_data_subset, check.names = FALSE)
  )
}

build_mae <- function(clinical_path,
                      metabolomics_path,
                      out_dir,
                      output_prefix = "MAE2") {
  clinical <- prepare_clinical_data(clinical_path)
  metabolomics <- prepare_metabolomics_data(metabolomics_path)

  pheno_data <- clinical$data
  pheno_labels <- clinical$labels
  mbo_data <- metabolomics$data
  mbo_info <- metabolomics$info

  common_ids <- intersect(pheno_data$StudyID, unique(mbo_data$StudyID))

  pheno_data <- pheno_data[pheno_data$StudyID %in% common_ids, , drop = FALSE]
  mbo_data <- mbo_data[mbo_data$StudyID %in% common_ids, , drop = FALSE]

  sample_id <- paste0(mbo_data$StudyID, "_", mbo_data$StudyVisit)

  feature_cols <- setdiff(colnames(mbo_data), c("NoOrder", "StudyID", "StudyVisit"))
  assay_df <- mbo_data[, feature_cols, drop = FALSE]
  rownames(assay_df) <- sample_id

  colnames(assay_df) <- gsub(pattern = " %", replacement = "-ratio", x = colnames(assay_df))
  rownames(mbo_info) <- gsub(pattern = " %", replacement = "-ratio", x = rownames(mbo_info))

  common_features <- intersect(colnames(assay_df), rownames(mbo_info))
  if (length(common_features) == 0) {
    stop("No overlapping metabolite features between SPSS and biomarker annotations.")
  }

  assay_df <- assay_df[, common_features, drop = FALSE]
  row_data <- mbo_info[common_features, , drop = FALSE]

  visit_values <- sort(unique(mbo_data$StudyVisit))
  visit_values <- visit_values[grepl("^[0-9]+$", visit_values)]
  if (length(visit_values) == 0) {
    stop("No numeric visit labels found after metabolomics preprocessing.")
  }

  assay_by_visit <- lapply(visit_values, function(v) {
    idx <- mbo_data$StudyVisit == v
    out <- assay_df[idx, , drop = FALSE]
    rownames(out) <- sample_id[idx]
    out
  })
  names(assay_by_visit) <- paste0("visit_", visit_values)

  assay_all <- do.call(rbind, assay_by_visit)

  imputation_method <- "none (not performed)"

  experiments_original <- list(
    visit_all = make_tse(assay_all, row_data, pheno_data, visit_values)
  )
  experiments_no_imputation <- list(
    visit_all = make_tse(assay_all, row_data, pheno_data, visit_values)
  )

  for (nm in names(assay_by_visit)) {
    visit_assay <- assay_by_visit[[nm]]
    experiments_original[[nm]] <- make_tse(visit_assay, row_data, pheno_data, visit_values)
    experiments_no_imputation[[nm]] <- make_tse(visit_assay, row_data, pheno_data, visit_values)
  }

  MAE.original <- MultiAssayExperiment(
    experiments = experiments_original,
    metadata = list(
      mbo_info = mbo_info,
      pheno_labels = pheno_labels,
      study_ids_common = common_ids,
      source_file = basename(metabolomics_path),
      imputation = imputation_method
    )
  )

  MAE <- MultiAssayExperiment(
    experiments = experiments_no_imputation,
    metadata = list(
      mbo_info = mbo_info,
      pheno_labels = pheno_labels,
      study_ids_common = common_ids,
      source_file = basename(metabolomics_path),
      imputation = imputation_method
    )
  )

  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

  saveRDS(MAE.original, file.path(out_dir, paste0(output_prefix, "_original.rds")))
  saveRDS(MAE, file.path(out_dir, paste0(output_prefix, ".rds")))

  message("Saved: ", file.path(out_dir, paste0(output_prefix, "_original.rds")))
  message("Saved: ", file.path(out_dir, paste0(output_prefix, ".rds")))
  message("Source workbook: ", basename(metabolomics_path))
  message("Common StudyID count: ", length(common_ids))
  message("Visits in MAE: ", paste(visit_values, collapse = ", "))
  message("Imputation: ", imputation_method)
  message("MAE and MAE_original are both no-imputation builds; both filenames are retained for compatibility.")

  invisible(list(
    MAE = MAE,
    MAE_original = MAE.original,
    common_ids = common_ids,
    visits = visit_values,
    imputation = imputation_method,
    source_file = metabolomics_path
  ))
}

find_clinical_file <- function(data_dir) {
  path <- file.path(data_dir, "FOPP_clinical_variables_child_cognition_20260617.sav")
  if (!file.exists(path)) {
    stop("Required clinical file not found: ", path)
  }
  path
}

find_metabolomics_file <- function(data_dir) {
  path <- file.path(data_dir, "Fopp_childserum_all_visits_MASTER_090326.xlsx")
  if (!file.exists(path)) {
    stop("Required metabolomics workbook not found: ", path)
  }
  path
}

main <- function() {
  data_dir <- normalizePath("../data", mustWork = TRUE)
  clinical_path <- find_clinical_file(data_dir)
  metabolomics_path <- find_metabolomics_file(data_dir)

  build_mae(
    clinical_path = clinical_path,
    metabolomics_path = metabolomics_path,
    out_dir = data_dir,
    output_prefix = "MAE2"
  )
  
  build_mae(
    clinical_path = clinical_path,
    metabolomics_path = metabolomics_path,
    out_dir = data_dir,
    output_prefix = "MAE"
  )
}

if (identical(environment(), globalenv())) {
  main()
}
