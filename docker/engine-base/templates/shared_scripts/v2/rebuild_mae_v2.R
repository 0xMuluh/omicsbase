#!/usr/bin/env Rscript

args_all <- commandArgs(trailingOnly = FALSE)
file_arg <- args_all[grepl("^--file=", args_all)]
script_path <- if (length(file_arg) > 0) {
  normalizePath(sub("^--file=", "", file_arg[[1]]), mustWork = TRUE)
} else {
  normalizePath("scripts/v2/rebuild_mae_v2.R", mustWork = TRUE)
}

root_dir <- normalizePath(file.path(dirname(script_path), "..", ".."), mustWork = TRUE)
source(file.path(dirname(script_path), "helpers_v2.R"))

load_make_mae_env <- function(path) {
  env <- new.env(parent = globalenv())
  source(path, local = env)
  required <- c("build_mae", "find_clinical_file", "find_metabolomics_file")
  missing <- setdiff(required, ls(env))
  if (length(missing) > 0) {
    stop("Missing required symbols in ", path, ": ", paste(missing, collapse = ", "))
  }
  env
}

rebuild_study_mae_v2 <- function(study) {
  message("[v2] Rebuilding MAE for ", study)

  make_mae_path <- file.path(root_dir, study, "code", "make_mae.R")
  data_dir <- normalizePath(file.path(root_dir, study, "data"), mustWork = TRUE)
  out_dir <- file.path(root_dir, study, "data_v2")
  ensure_dir(out_dir)

  env <- load_make_mae_env(make_mae_path)

  clinical_path <- env$find_clinical_file(data_dir)
  metabolomics_path <- env$find_metabolomics_file(data_dir)

  env$build_mae(
    clinical_path = clinical_path,
    metabolomics_path = metabolomics_path,
    out_dir = out_dir,
    output_prefix = "MAE"
  )

  mae_path <- file.path(out_dir, "MAE.rds")
  mae_orig_path <- file.path(out_dir, "MAE_original.rds")
  if (!file.exists(mae_path) || !file.exists(mae_orig_path)) {
    stop("MAE rebuild did not produce expected files for ", study)
  }

  list(
    study = study,
    mae = normalizePath(mae_path, mustWork = TRUE),
    mae_original = normalizePath(mae_orig_path, mustWork = TRUE)
  )
}

main <- function() {
  prenatal_diet <- rebuild_study_mae_v2("Prenatal diet")
  child_diet <- rebuild_study_mae_v2("Child diet")

  message("[v2] MAE rebuild complete")
  print(list(prenatal_diet = prenatal_diet, child_diet = child_diet))
}

if (identical(environment(), globalenv())) {
  main()
}
