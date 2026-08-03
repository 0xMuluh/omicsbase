suppressPackageStartupMessages({
  library(yaml)
  library(jsonlite)
})

`%||%` <- function(x, fallback) {
  if (is.null(x) || length(x) == 0 || all(is.na(x))) fallback else x
}

read_study_table <- function(path, sheet = NULL) {
  if (!file.exists(path)) {
    stop("Configured input file does not exist: ", path)
  }

  extension <- tolower(tools::file_ext(path))
  if (extension == "csv") {
    return(utils::read.csv(path, check.names = FALSE, stringsAsFactors = FALSE))
  }
  if (extension %in% c("tsv", "txt")) {
    return(utils::read.delim(path, check.names = FALSE, stringsAsFactors = FALSE))
  }
  if (extension %in% c("xlsx", "xls")) {
    if (!requireNamespace("readxl", quietly = TRUE)) {
      stop("Package 'readxl' is required to read Excel input.")
    }
    available <- readxl::excel_sheets(path)
    selected <- sheet %||% if ("SPSS" %in% available) "SPSS" else available[[1]]
    return(as.data.frame(readxl::read_excel(path, sheet = selected), check.names = FALSE))
  }
  if (extension == "sav") {
    if (!requireNamespace("haven", quietly = TRUE)) {
      stop("Package 'haven' is required to read SPSS input.")
    }
    return(as.data.frame(haven::read_sav(path), check.names = FALSE))
  }
  if (extension == "rds") {
    return(readRDS(path))
  }
  stop("Deterministic recipe does not support input extension: ", extension)
}

as_numeric_frame <- function(data) {
  converted <- lapply(data, function(value) suppressWarnings(as.numeric(value)))
  as.data.frame(converted, check.names = FALSE)
}

write_validation <- function(path, status, checks, details = list()) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  payload <- c(
    list(
      status = status,
      generated_at = format(Sys.time(), tz = "UTC", usetz = TRUE),
      checks = checks
    ),
    details
  )
  jsonlite::write_json(payload, path, auto_unbox = TRUE, pretty = TRUE, null = "null")
}

assert_columns <- function(data, columns, source_name) {
  missing <- setdiff(columns, names(data))
  if (length(missing) > 0) {
    stop(source_name, " is missing configured columns: ", paste(missing, collapse = ", "))
  }
  invisible(TRUE)
}
