find_project_root <- function(start = getwd()) {
  candidates <- unique(normalizePath(
    c(start, file.path(start, ".."), file.path(start, "../.."), file.path(start, "../../..")),
    mustWork = FALSE
  ))
  hit <- candidates[file.exists(file.path(candidates, "config", "analysis_plan.R"))]
  if (!length(hit)) stop("Could not locate project root containing config/analysis_plan.R", call. = FALSE)
  normalizePath(hit[[1]], mustWork = TRUE)
}

is_absolute_path <- function(path) {
  grepl("^/", path) || grepl("^[A-Za-z]:", path)
}

project_root <- find_project_root()
project_file <- function(...) {
  path <- file.path(...)
  if (is_absolute_path(path)) return(path)
  file.path(project_root, path)
}

source(project_file("code", "00_setup.R"))
plan <- read_analysis_plan(project_file("config", "analysis_plan.R"))

read_if_exists <- function(path) {
  full_path <- project_file(path)
  if (!file.exists(full_path)) return(data.frame(note = sprintf("Missing file: %s", path), stringsAsFactors = FALSE))
  read_table_file(full_path)
}

read_rds_if_exists <- function(path) {
  full_path <- project_file(path)
  if (!file.exists(full_path)) return(NULL)
  readRDS(full_path)
}

collapse_scalar <- function(x) {
  if (is.null(x) || length(x) == 0) return("")
  if (is.list(x)) {
    if (!is.null(names(x)) && any(nzchar(names(x)))) return(paste(names(x), collapse = ", "))
    return(paste(vapply(x, collapse_scalar, character(1)), collapse = ", "))
  }
  paste(as.character(x), collapse = ", ")
}

kv_table <- function(x) {
  data.frame(
    field = names(x),
    value = vapply(x, collapse_scalar, character(1)),
    stringsAsFactors = FALSE
  )
}

table_out <- function(x, caption = NULL, n = 50) {
  if (is.null(x) || !is.data.frame(x) || nrow(x) == 0) {
    x <- data.frame(note = "No rows available", stringsAsFactors = FALSE)
  }
  x <- utils::head(x, n)
  if (requireNamespace("knitr", quietly = TRUE)) return(knitr::kable(x, caption = caption))
  x
}

sort_by_q <- function(x) {
  if (!is.data.frame(x) || !"q.value" %in% names(x)) return(x)
  x[order(is.na(x$q.value), x$q.value), , drop = FALSE]
}
