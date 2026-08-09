#!/usr/bin/env Rscript
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 3L) stop("Usage: check_result_contract.R <path.rds> <component-or-.> <required-cols-comma-separated>", call. = FALSE)
path <- args[[1]]; component <- args[[2]]
required <- trimws(strsplit(args[[3]], ",", fixed = TRUE)[[1]])
required <- required[nzchar(required)]
if (!file.exists(path)) stop("File not found: ", path, call. = FALSE)
if (length(required) == 0L) stop("No required columns supplied", call. = FALSE)
obj <- readRDS(path)
target <- obj
if (component != ".") {
  parts <- strsplit(component, ".", fixed = TRUE)[[1]]
  for (part in parts) {
    if (!is.list(target) || is.null(target[[part]])) stop("Component not found: ", component, call. = FALSE)
    target <- target[[part]]
  }
}
if (!is.data.frame(target)) stop("Selected component is not a data frame: ", component, " class=", paste(class(target), collapse = ","), call. = FALSE)
missing <- setdiff(required, names(target))
if (length(missing) > 0L) {
  cat("contract_failed
missing:", paste(missing, collapse = ","), "
")
  quit(status = 1L)
}
cat("contract_ok
path:", path, "
component:", component, "
rows:", nrow(target), "
cols:", ncol(target), "
")
