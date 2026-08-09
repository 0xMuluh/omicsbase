suppressPackageStartupMessages({
  library(dplyr)
})

`%||%` <- function(x, y) {
  if (is.null(x) || length(x) == 0) y else x
}

now_iso <- function() {
  format(Sys.time(), "%Y-%m-%dT%H:%M:%S%z")
}

ensure_dir <- function(path) {
  dir.create(path, recursive = TRUE, showWarnings = FALSE)
  normalizePath(path, winslash = "/", mustWork = TRUE)
}

resolve_covars <- function(df, planned_covars) {
  planned_covars <- unique(as.character(planned_covars))
  present <- planned_covars[planned_covars %in% names(df)]
  missing <- setdiff(planned_covars, present)
  list(present = present, missing = missing)
}

pick_metabolite_features <- function(feature_names, ratio_pattern = "-ratio") {
  feature_names[!grepl(ratio_pattern, feature_names, fixed = TRUE)]
}

init_execution_log <- function() {
  data.frame(
    study = character(),
    question = character(),
    model_family = character(),
    exposure_id = character(),
    visit_or_frame = character(),
    status = character(),
    reason = character(),
    n_input = integer(),
    n_complete = integer(),
    n_features_tested = integer(),
    present_covars = character(),
    missing_covars = character(),
    started_at = character(),
    finished_at = character(),
    stringsAsFactors = FALSE
  )
}

append_execution_log <- function(log_df,
                                 study,
                                 question,
                                 model_family,
                                 exposure_id,
                                 visit_or_frame,
                                 status,
                                 reason,
                                 n_input = NA_integer_,
                                 n_complete = NA_integer_,
                                 n_features_tested = NA_integer_,
                                 present_covars = character(0),
                                 missing_covars = character(0),
                                 started_at = now_iso(),
                                 finished_at = now_iso()) {
  row <- data.frame(
    study = as.character(study %||% ""),
    question = as.character(question %||% ""),
    model_family = as.character(model_family %||% ""),
    exposure_id = as.character(exposure_id %||% ""),
    visit_or_frame = as.character(visit_or_frame %||% ""),
    status = as.character(status %||% ""),
    reason = as.character(reason %||% ""),
    n_input = suppressWarnings(as.integer(n_input[1])),
    n_complete = suppressWarnings(as.integer(n_complete[1])),
    n_features_tested = suppressWarnings(as.integer(n_features_tested[1])),
    present_covars = paste(present_covars, collapse = "|"),
    missing_covars = paste(missing_covars, collapse = "|"),
    started_at = as.character(started_at %||% now_iso()),
    finished_at = as.character(finished_at %||% now_iso()),
    stringsAsFactors = FALSE
  )
  bind_rows(log_df, row)
}

status_from_notes <- function(df, ok_note = "ok") {
  if (is.null(df) || nrow(df) == 0) {
    return(list(status = "failed", reason = "empty_result", n_features_ok = 0L))
  }

  if ("note" %in% names(df)) {
    ok_rows <- which(df$note == ok_note)
    if (length(ok_rows) > 0) {
      return(list(status = "ok", reason = "ok", n_features_ok = length(unique(df$outcome[ok_rows] %||% seq_along(ok_rows)))))
    }
    tab <- sort(table(df$note), decreasing = TRUE)
    top_reason <- names(tab)[1]
    return(list(status = "failed", reason = top_reason, n_features_ok = 0L))
  }

  if ("feature" %in% names(df)) {
    return(list(status = "ok", reason = "ok", n_features_ok = length(unique(df$feature))))
  }

  list(status = "ok", reason = "ok", n_features_ok = nrow(df))
}

build_manifest <- function(rows) {
  out <- bind_rows(rows)
  out$created_at <- now_iso()
  out
}

validate_result_contract <- function(result_obj, required_keys, label = "result") {
  missing <- setdiff(required_keys, names(result_obj))
  if (length(missing) > 0) {
    stop(label, " is missing required keys: ", paste(missing, collapse = ", "))
  }
  TRUE
}

safe_quarto_render <- function(code_dir) {
  wd <- normalizePath(code_dir, winslash = "/", mustWork = TRUE)
  cache_dir <- file.path(wd, ".cache_v2")
  dir.create(cache_dir, recursive = TRUE, showWarnings = FALSE)
  old <- getwd()
  on.exit(setwd(old), add = TRUE)
  setwd(wd)
  env_vars <- c(
    paste0("XDG_CACHE_HOME=", cache_dir),
    paste0("QUARTO_CACHE_DIR=", cache_dir)
  )
  out <- system2("quarto", c("render"), stdout = TRUE, stderr = TRUE, env = env_vars)
  status <- attr(out, "status")
  if (!is.null(status) && status != 0) {
    stop("quarto render failed for ", wd, "\n", paste(out, collapse = "\n"))
  }
  invisible(out)
}
