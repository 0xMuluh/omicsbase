#!/usr/bin/env Rscript
args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1L) stop("Usage: scan_report_text.R <file-or-dir>...", call. = FALSE)
collect_files <- function(path) {
  if (dir.exists(path)) list.files(path, pattern = "[.](qmd|md|R|r)$", recursive = TRUE, full.names = TRUE)
  else if (file.exists(path)) path
  else { warning("Path not found: ", path, call. = FALSE); character(0) }
}
patterns <- list(todo = "TODO|FIXME", chatbot_filler = "comprehensive|robust dashboard|valuable insights|fascinating|delve into|unlock|seamless|user-friendly", meta_commentary = "currently scoped|this site is currently|work in progress|not yet report", apology = "sorry|apologize", raw_header_underscores = "^#+ .*_[A-Za-z0-9]")
files <- unique(unlist(lapply(args, collect_files), use.names = FALSE))
findings <- data.frame(file = character(), line = integer(), check = character(), text = character())
for (f in files) {
  lines <- readLines(f, warn = FALSE)
  for (nm in names(patterns)) {
    hit <- grep(patterns[[nm]], lines, ignore.case = TRUE)
    if (length(hit) > 0L) findings <- rbind(findings, data.frame(file = f, line = hit, check = nm, text = trimws(lines[hit]), stringsAsFactors = FALSE))
  }
}
if (nrow(findings) == 0L) { cat("scan_ok
"); quit(status = 0L) }
for (i in seq_len(nrow(findings))) cat(findings$file[i], ":", findings$line[i], " [", findings$check[i], "] ", findings$text[i], "
", sep = "")
quit(status = 1L)
