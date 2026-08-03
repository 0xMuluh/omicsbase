script_args <- commandArgs(trailingOnly = FALSE)
script_file_arg <- grep("^--file=", script_args, value = TRUE)
script_dir <- if (length(script_file_arg) > 0) {
  dirname(normalizePath(sub("^--file=", "", script_file_arg[[1]])))
} else {
  getwd()
}

source(file.path(script_dir, "r-package-list.R"), local = TRUE)
required_packages <- unique(c(
  cran_packages,
  bioc_packages,
  vapply(github_packages, basename, character(1))
))
missing_packages <- required_packages[
  !vapply(required_packages, requireNamespace, logical(1), quietly = TRUE)
]
if (length(missing_packages) > 0) {
  cat("Missing R package(s): ", paste(missing_packages, collapse = ", "), "\n", sep = "")
  cat("Fix: make r-deps\n")
  quit(status = 1)
}
cat("All standard R analysis packages are installed.\n")
