#!/usr/bin/env Rscript
args <- commandArgs(trailingOnly = TRUE)
get_arg <- function(flag, default = "") {
  hit <- which(args == flag)
  if (!length(hit) || hit[[1]] == length(args)) return(default)
  args[[hit[[1]] + 1L]]
}
has_flag <- function(flag) any(args == flag)
manifest_path <- get_arg("--manifest", "config/r_package_manifest.csv")
output_path <- get_arg("--output", "results/r_package_status.tsv")
install_missing <- has_flag("--install")
include_optional <- has_flag("--include-optional")

if (!file.exists(manifest_path)) stop(sprintf("Package manifest not found: %s", manifest_path), call. = FALSE)
manifest <- read.csv(manifest_path, stringsAsFactors = FALSE, check.names = FALSE)
required <- c("package", "source", "priority", "purpose")
missing_cols <- setdiff(required, names(manifest))
if (length(missing_cols)) stop(sprintf("Manifest missing columns: %s", paste(missing_cols, collapse = ", ")), call. = FALSE)
if (!include_optional) manifest <- manifest[manifest$priority != "optional", , drop = FALSE]

install_one <- function(pkg, source) {
  if (!install_missing) return("missing_not_installed")
  if (source == "base") return("missing_base_package")
  if (source == "cran") {
    utils::install.packages(pkg, repos = "https://cloud.r-project.org")
    return("install_attempted_cran")
  }
  if (source == "bioc") {
    if (!requireNamespace("BiocManager", quietly = TRUE)) {
      utils::install.packages("BiocManager", repos = "https://cloud.r-project.org")
    }
    BiocManager::install(pkg, ask = FALSE, update = FALSE)
    return("install_attempted_bioc")
  }
  "unknown_source"
}

rows <- list()
for (i in seq_len(nrow(manifest))) {
  pkg <- manifest$package[[i]]
  src <- manifest$source[[i]]
  installed <- requireNamespace(pkg, quietly = TRUE)
  action <- if (installed) "already_installed" else install_one(pkg, src)
  installed_after <- requireNamespace(pkg, quietly = TRUE)
  version <- if (installed_after) as.character(utils::packageVersion(pkg)) else NA_character_
  rows[[i]] <- data.frame(
    package = pkg,
    source = src,
    priority = manifest$priority[[i]],
    purpose = manifest$purpose[[i]],
    installed = installed_after,
    version = version,
    action = action,
    stringsAsFactors = FALSE
  )
}
status <- do.call(rbind, rows)
dir.create(dirname(output_path), recursive = TRUE, showWarnings = FALSE)
write.table(status, output_path, sep = "\t", quote = FALSE, row.names = FALSE, na = "")
write.table(status, stdout(), sep = "\t", quote = FALSE, row.names = FALSE, na = "")
missing_primary <- status$priority == "primary" & !status$installed
if (any(missing_primary)) quit(status = 2L)
