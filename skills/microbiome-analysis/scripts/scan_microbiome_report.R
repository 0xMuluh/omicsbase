#!/usr/bin/env Rscript
args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1L) stop("Usage: scan_microbiome_report.R <file-or-dir>...", call. = FALSE)
collect_files <- function(path) {
  if (dir.exists(path)) list.files(path, pattern = "[.](qmd|R|r|md)$", recursive = TRUE, full.names = TRUE)
  else if (file.exists(path)) path
  else { warning("Path not found: ", path, call. = FALSE); character(0) }
}
patterns <- list(todo = "TODO|FIXME", phyloseq = "phyloseq", tree_summarized_experiment = "TreeSummarizedExperiment|TreeSE", alpha_diversity = "Shannon|Simpson|Observed|alpha", beta_diversity = "Bray|Jaccard|Aitchison|UniFrac|PCoA|ordination|beta", permanova = "adonis|PERMANOVA", differential_abundance = "ANCOM|ALDEx|MaAsLin|DESeq|differential abundance", rarefaction = "rarefy|rarefaction", relative_abundance = "relative abundance")
files <- unique(unlist(lapply(args, collect_files), use.names = FALSE))
findings <- data.frame(file = character(), line = integer(), check = character(), text = character())
for (f in files) {
  lines <- readLines(f, warn = FALSE)
  for (nm in names(patterns)) {
    hit <- grep(patterns[[nm]], lines, ignore.case = TRUE)
    if (length(hit) > 0L) findings <- rbind(findings, data.frame(file = f, line = hit, check = nm, text = trimws(lines[hit]), stringsAsFactors = FALSE))
  }
}
if (nrow(findings) == 0L) { cat("scan_ok_no_method_markers
"); quit(status = 0L) }
for (i in seq_len(nrow(findings))) cat(findings$file[i], ":", findings$line[i], " [", findings$check[i], "] ", findings$text[i], "
", sep = "")
quit(status = if (any(findings$check == "todo")) 1L else 0L)
