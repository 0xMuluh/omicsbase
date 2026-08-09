#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(dplyr)
  library(readxl)
  library(stringr)
  library(MultiAssayExperiment)
  library(TreeSummarizedExperiment)
  library(S4Vectors)
  library(foreign)
})

pick_first_existing <- function(candidates, available, what) {
  hit <- candidates[candidates %in% available]
  if (length(hit) == 0) {
    stop("Missing ", what, ". Tried: ", paste(candidates, collapse = ", "))
  }
  hit[[1]]
}

merge_measured_twice <- function(data,
                                 study_id,
                                 old_study_visit,
                                 new_study_visit,
                                 no_order) {
  key_id <- trimws(as.character(data[["StudyID"]]))
  key_visit <- trimws(as.character(data[["StudyVisit"]]))

  idx <- which(key_id %in% study_id & key_visit %in% old_study_visit)
  if (length(idx) < 2) {
    return(data)
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
  pheno_data <- read.spss(path, to.data.frame = TRUE)
  pheno_labels <- attr(pheno_data, "variable.labels")

  if (!is.null(pheno_labels)) {
    missing_labels <- names(pheno_labels)[pheno_labels == ""]
    pheno_labels[missing_labels] <- missing_labels
  }

  id_col <- if ("StudyID" %in% names(pheno_data)) {
    "StudyID"
  } else if ("VariableCode" %in% names(pheno_data)) {
    "VariableCode"
  } else {
    stop("Clinical file must contain either StudyID or VariableCode.")
  }

  pheno_data$StudyID <- trimws(as.character(pheno_data[[id_col]]))
  pheno_data <- pheno_data[!is.na(pheno_data$StudyID) & pheno_data$StudyID != "", , drop = FALSE]

  if (anyDuplicated(pheno_data$StudyID) > 0) {
    pheno_data <- pheno_data[!duplicated(pheno_data$StudyID), , drop = FALSE]
  }

  if ("CFastingHours7" %in% names(pheno_data)) {
    pheno_data$CFastingHours7 <- suppressWarnings(as.numeric(pheno_data$CFastingHours7))
  }

  if ("MModeOfDeliv" %in% names(pheno_data)) {
    pheno_data$MModeOfDeliv.1 <- str_replace_all(
      pheno_data$MModeOfDeliv,
      c(
        "vaginal unassisted" = "Vaginal delivery",
        "vacuum extraction" = "Vaginal delivery",
        "elective cesarean" = "Cesarean",
        "acute cesarean" = "Cesarean",
        "emergency cesarean" = "Cesarean"
      )
    )

    if (!is.null(pheno_labels)) {
      pheno_labels["MModeOfDeliv.1"] <- "Mode of delivery"
    }
  }

  rownames(pheno_data) <- pheno_data$StudyID

  list(
    data = pheno_data,
    labels = pheno_labels
  )
}

prepare_metabolomics_data <- function(path) {
  raw_data <- read_xlsx(path, sheet = "SPSS", na = c("NA", "TAG")) %>%
    as.data.frame(check.names = FALSE)

  id_col <- pick_first_existing(c("StudyID", "Study code"), names(raw_data), "metabolomics ID column")
  visit_col <- pick_first_existing(c("Study visit no"), names(raw_data), "metabolomics visit column")
  order_col <- pick_first_existing(c("NoOrder", "No of order"), names(raw_data), "metabolomics order column")

  colnames(raw_data)[colnames(raw_data) == id_col] <- "StudyID"
  colnames(raw_data)[colnames(raw_data) == visit_col] <- "StudyVisit"
  colnames(raw_data)[colnames(raw_data) == order_col] <- "NoOrder"

  mbo_data <- raw_data

  mbo_info <- read_xlsx(path, sheet = "Biomarker annotations") %>%
    as.data.frame(check.names = FALSE)

  if (!("Excel column name" %in% names(mbo_info))) {
    stop("Biomarker annotations sheet must contain 'Excel column name'.")
  }

  rownames(mbo_info) <- mbo_info[["Excel column name"]]

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
  # Sample IDs may have format: "visit_6.M3001_6" or "M3001_6"
  # Extract the StudyID (e.g., "M3001") and visit number (e.g., "6")
  # Handle both with and without prefix
  
  # Remove prefix if present (e.g., "visit_6." -> "")
  clean_ids <- sub("^visit_[0-9]+\\.", "", sample_ids)
  
  # Extract base ID (StudyID) - everything before last underscore
  base_id <- sub("_[^_]*$", "", clean_ids)
  
  # Extract visit number - everything after last underscore
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
                      output_prefix = "MAE2",
                      seed = 123) {
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

  do_impute <- requireNamespace("imputomics", quietly = TRUE)
  imputation_method <- if (do_impute) {
    "GSimp (imputomics::impute_metabimpute_gsimp)"
  } else {
    "none (imputomics not installed)"
  }

  if (do_impute) {
    set.seed(seed)
  }

  impute_assay <- function(dat) {
    if (!do_impute) {
      return(dat)
    }

    out <- imputomics::impute_metabimpute_gsimp(missdf = dat)
    out <- as.data.frame(out, check.names = FALSE)
    rownames(out) <- rownames(dat)
    colnames(out) <- colnames(dat)
    out
  }

  experiments_original <- list(
    visit_all = make_tse(assay_all, row_data, pheno_data, visit_values)
  )
  experiments_imputed <- list(
    visit_all = make_tse(impute_assay(assay_all), row_data, pheno_data, visit_values)
  )

  for (nm in names(assay_by_visit)) {
    visit_assay <- assay_by_visit[[nm]]
    experiments_original[[nm]] <- make_tse(visit_assay, row_data, pheno_data, visit_values)
    experiments_imputed[[nm]] <- make_tse(impute_assay(visit_assay), row_data, pheno_data, visit_values)
  }

  MAE.original <- MultiAssayExperiment(
    experiments = experiments_original,
    metadata = list(
      mbo_info = mbo_info,
      pheno_labels = pheno_labels,
      study_ids_common = common_ids,
      source_file = basename(metabolomics_path),
      imputation = "none"
    )
  )

  MAE <- MultiAssayExperiment(
    experiments = experiments_imputed,
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
  sav_files <- list.files(data_dir, pattern = "\\.sav$", full.names = TRUE)
  if (length(sav_files) != 1) {
    stop("Expected exactly one .sav file in ", data_dir, "; found ", length(sav_files), ".")
  }
  sav_files[[1]]
}

find_metabolomics_file <- function(data_dir) {
  preferred <- file.path(data_dir, "Fopp_childserum_all_visits_MASTER_090326.xlsx")
  if (file.exists(preferred)) {
    return(preferred)
  }

  fallback <- file.path(data_dir, "Fopp_childserum_results.xlsx")
  if (file.exists(fallback)) {
    return(fallback)
  }

  xlsx_files <- list.files(data_dir, pattern = "\\.xlsx$", full.names = TRUE)
  if (length(xlsx_files) != 1) {
    stop("Expected metabolomics xlsx in ", data_dir, ".")
  }
  xlsx_files[[1]]
}

main <- function() {
  data_dir <- normalizePath("../data", mustWork = TRUE)
  clinical_path <- find_clinical_file(data_dir)
  metabolomics_path <- find_metabolomics_file(data_dir)

  build_mae(
    clinical_path = clinical_path,
    metabolomics_path = metabolomics_path,
    out_dir = data_dir
  )
}

if (identical(environment(), globalenv())) {
  main()
}
