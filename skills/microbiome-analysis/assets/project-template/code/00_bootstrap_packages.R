args <- commandArgs(trailingOnly = TRUE)
manifest <- if (length(args) >= 1L) args[[1]] else "config/r_package_manifest.csv"
output <- if (length(args) >= 2L) args[[2]] else "results/r_package_status.tsv"
install_missing <- any(args == "--install")
include_optional <- any(args == "--include-optional")

if (!file.exists(manifest)) stop(sprintf("Package manifest not found: %s", manifest), call. = FALSE)
pkgs <- read.csv(manifest, stringsAsFactors = FALSE, check.names = FALSE)
if (!include_optional) pkgs <- pkgs[pkgs$priority != "optional", , drop = FALSE]

install_one <- function(pkg, source) {
  if (!install_missing) return("missing_not_installed")
  if (source == "cran") {
    utils::install.packages(pkg, repos = "https://cloud.r-project.org")
    return("install_attempted_cran")
  }
  if (source == "bioc") {
    if (!requireNamespace("BiocManager", quietly = TRUE)) utils::install.packages("BiocManager", repos = "https://cloud.r-project.org")
    BiocManager::install(pkg, ask = FALSE, update = FALSE)
    return("install_attempted_bioc")
  }
  "unknown_source"
}

rows <- vector("list", nrow(pkgs))
for (i in seq_len(nrow(pkgs))) {
  pkg <- pkgs$package[[i]]
  installed <- requireNamespace(pkg, quietly = TRUE)
  action <- if (installed) "already_installed" else install_one(pkg, pkgs$source[[i]])
  installed_after <- requireNamespace(pkg, quietly = TRUE)
  rows[[i]] <- data.frame(
    package = pkg,
    source = pkgs$source[[i]],
    priority = pkgs$priority[[i]],
    installed = installed_after,
    version = if (installed_after) as.character(utils::packageVersion(pkg)) else NA_character_,
    action = action,
    stringsAsFactors = FALSE
  )
}
status <- do.call(rbind, rows)
dir.create(dirname(output), recursive = TRUE, showWarnings = FALSE)
write.table(status, output, sep = "\t", quote = FALSE, row.names = FALSE, na = "")
print(status)
if (any(status$priority == "primary" & !status$installed)) quit(status = 2L)
