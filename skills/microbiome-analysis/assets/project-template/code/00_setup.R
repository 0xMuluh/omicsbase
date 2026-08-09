`%||%` <- function(x, y) {
  if (is.null(x) || length(x) == 0) y else x
}

required_marker <- function(x) {
  is.character(x) && length(x) == 1 && grepl("^<required", x)
}

optional_marker <- function(x) {
  is.null(x) || (is.character(x) && length(x) == 1 && grepl("^<optional", x))
}

blank_or_marker <- function(x) {
  is.null(x) || length(x) == 0 || (is.character(x) && length(x) == 1 && (is.na(x) || identical(x, "") || required_marker(x) || optional_marker(x)))
}

assert_filled <- function(x, label) {
  if (blank_or_marker(x)) {
    stop(sprintf("Missing required config value: %s", label), call. = FALSE)
  }
  x
}

read_analysis_plan <- function(path = "config/analysis_plan.R") {
  if (!file.exists(path)) stop(sprintf("Analysis plan not found: %s", path), call. = FALSE)
  env <- new.env(parent = baseenv())
  sys.source(path, envir = env)
  if (!exists("analysis_plan", envir = env, inherits = FALSE)) {
    stop("analysis_plan.R must define analysis_plan", call. = FALSE)
  }
  plan <- get("analysis_plan", envir = env, inherits = FALSE)
  if (!is.list(plan)) stop("analysis_plan must be a list", call. = FALSE)
  plan
}

ensure_dir <- function(path) {
  if (!dir.exists(path)) dir.create(path, recursive = TRUE, showWarnings = FALSE)
  invisible(path)
}

read_table_file <- function(path, na_values = c("", "NA", "NaN"), sheet = 1) {
  path <- assert_filled(path, "input path")
  if (!file.exists(path)) stop(sprintf("Input file not found: %s", path), call. = FALSE)
  ext <- tolower(tools::file_ext(path))
  if (ext %in% c("tsv", "txt")) {
    read.delim(path, check.names = FALSE, stringsAsFactors = FALSE, na.strings = na_values, comment.char = "")
  } else if (ext == "csv") {
    read.csv(path, check.names = FALSE, stringsAsFactors = FALSE, na.strings = na_values, comment.char = "")
  } else if (ext %in% c("xls", "xlsx")) {
    if (!requireNamespace("readxl", quietly = TRUE)) stop("Package readxl is required to read Excel files", call. = FALSE)
    as.data.frame(readxl::read_excel(path, sheet = sheet), stringsAsFactors = FALSE)
  } else {
    stop(sprintf("Unsupported table extension for %s. Use csv, tsv, txt, xls, or xlsx.", path), call. = FALSE)
  }
}

write_tsv <- function(x, path) {
  ensure_dir(dirname(path))
  write.table(x, path, sep = "\t", quote = FALSE, row.names = FALSE, na = "")
  invisible(path)
}

split_terms <- function(value) {
  if (is.null(value) || length(value) == 0 || is.na(value) || identical(value, "")) return(character(0))
  unique(trimws(unlist(strsplit(as.character(value), "[;,]"))))
}

get_covariates <- function(plan, block) {
  if (is.null(block) || is.na(block) || block == "" || block == "none") return(character(0))
  if (!is.null(plan$variables$covariates[[block]])) return(plan$variables$covariates[[block]])
  split_terms(block)
}

require_columns <- function(data, columns, label) {
  columns <- columns[!vapply(columns, blank_or_marker, logical(1))]
  missing <- setdiff(columns, names(data))
  if (length(missing)) {
    stop(sprintf("%s is missing required column(s): %s", label, paste(missing, collapse = ", ")), call. = FALSE)
  }
}

append_decision <- function(decision, value, reason, source = "agent", path = "config/decision_log.tsv") {
  ensure_dir(dirname(path))
  row <- data.frame(
    timestamp = format(Sys.time(), "%Y-%m-%d %H:%M:%S %z"),
    decision = decision,
    value = value,
    reason = reason,
    source = source,
    stringsAsFactors = FALSE
  )
  write.table(row, path, sep = "\t", quote = FALSE, row.names = FALSE,
              col.names = !file.exists(path), append = file.exists(path))
  invisible(row)
}

safe_feature_columns <- function(features) {
  make.names(features, unique = TRUE)
}

clean_sample_ids <- function(x, regex = "", replacement = "") {
  x <- as.character(x)
  if (!blank_or_marker(regex) && nzchar(regex)) x <- gsub(regex, replacement, x)
  x
}

validate_unique <- function(x, label) {
  dup <- unique(x[duplicated(x)])
  if (length(dup)) stop(sprintf("%s contains duplicate ID(s): %s", label, paste(head(dup, 20), collapse = ", ")), call. = FALSE)
}

bray_distance <- function(mat) {
  n <- nrow(mat)
  out <- matrix(0, n, n)
  for (i in seq_len(n)) {
    for (j in seq_len(n)) {
      denom <- sum(mat[i, ] + mat[j, ], na.rm = TRUE)
      out[i, j] <- if (denom == 0) 0 else sum(abs(mat[i, ] - mat[j, ]), na.rm = TRUE) / denom
    }
  }
  stats::as.dist(out)
}
