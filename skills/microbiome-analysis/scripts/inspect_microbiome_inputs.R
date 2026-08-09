#!/usr/bin/env Rscript
args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1L) {
  stop("Usage: inspect_microbiome_inputs.R <file-or-dir>...", call. = FALSE)
}

collect_files <- function(path) {
  if (dir.exists(path)) {
    list.files(path, pattern = "[.](rds|csv|tsv|txt)$", recursive = TRUE, full.names = TRUE)
  } else if (file.exists(path)) {
    path
  } else {
    warning("Path not found: ", path, call. = FALSE)
    character(0)
  }
}

summarize_rds <- function(path) {
  obj <- readRDS(path)
  cat("file:", path, "\n")
  cat("  class:", paste(class(obj), collapse = ","), "\n")
  if (is.data.frame(obj) || is.matrix(obj)) {
    cat("  dim:", paste(dim(obj), collapse = "x"), "\n")
    if (!is.null(colnames(obj))) {
      cat("  columns:", paste(head(colnames(obj), 20L), collapse = ", "), "\n")
    }
  } else if (is.list(obj)) {
    cat("  list_length:", length(obj), "\n")
    if (!is.null(names(obj))) {
      cat("  names:", paste(head(names(obj), 30L), collapse = ", "), "\n")
    }
  }
}

summarize_table <- function(path) {
  sep <- if (grepl("[.]csv$", path, ignore.case = TRUE)) "," else "\t"
  dat <- tryCatch(
    read.table(path, sep = sep, header = TRUE, quote = "\"", comment.char = "", check.names = FALSE, nrows = 10L),
    error = function(e) e
  )
  cat("file:", path, "\n")
  if (inherits(dat, "error")) {
    cat("  error:", conditionMessage(dat), "\n")
  } else {
    cat("  preview_rows:", nrow(dat), "\n")
    cat("  columns:", paste(head(names(dat), 30L), collapse = ", "), "\n")
  }
}

files <- unique(unlist(lapply(args, collect_files), use.names = FALSE))
for (f in files) {
  if (grepl("[.]rds$", f, ignore.case = TRUE)) summarize_rds(f) else summarize_table(f)
}
