#!/usr/bin/env Rscript
args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1L) stop("Usage: scan_metabolomics_report.R <file-or-dir>...", call. = FALSE)
collect_files <- function(path) {
  if (dir.exists(path)) list.files(path, pattern = "[.](qmd|R|r|md)$", recursive = TRUE, full.names = TRUE)
  else if (file.exists(path)) path
  else { warning("Path not found: ", path, call. = FALSE); character(0) }
}
files <- unique(unlist(lapply(args, collect_files), use.names = FALSE))
patterns <- list(todo = "TODO|FIXME", fragile_visit_interaction = ":visit_num[$]", generic_exposure_in_report = "exposure_it", hardcoded_interaction_term = "x:visit_factor[0-9]+", external_hypothesis_embed = "hypothes[.]is/embed[.]js")
findings <- data.frame(file = character(), line = integer(), check = character(), text = character())
for (f in files) {
  lines <- readLines(f, warn = FALSE)
  for (nm in names(patterns)) {
    hit <- grep(patterns[[nm]], lines, ignore.case = nm == "todo")
    if (length(hit) > 0L) findings <- rbind(findings, data.frame(file = f, line = hit, check = nm, text = trimws(lines[hit]), stringsAsFactors = FALSE))
  }
}
if (nrow(findings) == 0L) { cat("scan_ok
"); quit(status = 0L) }
for (i in seq_len(nrow(findings))) cat(findings$file[i], ":", findings$line[i], " [", findings$check[i], "] ", findings$text[i], "
", sep = "")
quit(status = if (any(findings$check %in% c("todo", "fragile_visit_interaction"))) 1L else 0L)
