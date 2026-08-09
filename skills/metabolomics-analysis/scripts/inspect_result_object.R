#!/usr/bin/env Rscript
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1L) stop("Usage: inspect_result_object.R <path.rds>", call. = FALSE)
path <- args[[1]]
if (!file.exists(path)) stop("File not found: ", path, call. = FALSE)
obj <- readRDS(path)
cat("path:", path, "
")
cat("root_class:", paste(class(obj), collapse = ","), "
")
summarize_one <- function(x, label, depth = 0L) {
  indent <- paste(rep("  ", depth), collapse = "")
  cat(indent, "- ", label, ": class=", paste(class(x), collapse = ","), sep = "")
  if (is.data.frame(x)) {
    cat(" rows=", nrow(x), " cols=", ncol(x), "
", sep = "")
    cols <- names(x)
    if (length(cols) > 0L) cat(indent, "  columns: ", paste(head(cols, 40L), collapse = ", "), if (length(cols) > 40L) ", ..." else "", "
", sep = "")
  } else if (is.matrix(x) || is.array(x)) {
    cat(" dim=", paste(dim(x), collapse = "x"), "
", sep = "")
  } else if (is.list(x)) {
    nm <- names(x)
    cat(" length=", length(x), "
", sep = "")
    if (!is.null(nm) && length(nm) > 0L) cat(indent, "  names: ", paste(head(nm, 30L), collapse = ", "), if (length(nm) > 30L) ", ..." else "", "
", sep = "")
    if (depth < 2L) {
      child_names <- names(x); if (is.null(child_names)) child_names <- as.character(seq_along(x))
      for (i in seq_len(min(length(x), 25L))) summarize_one(x[[i]], child_names[[i]], depth + 1L)
    }
  } else {
    cat(" length=", length(x), "
", sep = "")
  }
}
summarize_one(obj, "root")
