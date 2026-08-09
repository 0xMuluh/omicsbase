#!/usr/bin/env Rscript
args <- commandArgs(trailingOnly = TRUE)
output <- ""
if (length(args) >= 2L && identical(args[1], "--output")) output <- args[2]

checks <- data.frame(
  package = c(
    "mia", "mia", "mia", "mia", "mia", "mia", "mia", "mia",
    "TreeSummarizedExperiment", "SingleCellExperiment", "SingleCellExperiment", "SummarizedExperiment",
    "miaViz", "miaViz", "scater", "scater", "vegan",
    "stats", "stats", "stats", "lmerTest", "emmeans", "emmeans", "sjPlot", "sjPlot",
    "maaslin3", "ANCOMBC", "phyloseq", "ALDEx2"
  ),
  function_name = c(
    "importMetaPhlAn", "transformAssay", "addAlpha", "agglomerateByRanks",
    "agglomerateByPrevalence", "meltSE", "getTop", "getDissimilarity",
    "TreeSummarizedExperiment", "altExp", "altExpNames", "SummarizedExperiment",
    "plotAbundance", "plotBoxplot", "runMDS", "plotReducedDim", "adonis2",
    "wilcox.test", "kruskal.test", "p.adjust", "lmer", "emmeans", "contrast", "tab_model", "plot_model",
    "maaslin3", "ancombc2", "phyloseq", "aldex.clr"
  ),
  priority = c(
    rep("primary", 26),
    rep("optional", 3)
  ),
  stringsAsFactors = FALSE
)

inspect_one <- function(pkg, fun, priority) {
  installed <- requireNamespace(pkg, quietly = TRUE)
  version <- if (installed) as.character(utils::packageVersion(pkg)) else NA_character_
  obj <- NULL
  if (installed) {
    obj <- tryCatch(getExportedValue(pkg, fun), error = function(e) NULL)
    if (is.null(obj)) obj <- tryCatch(get(fun, envir = asNamespace(pkg), inherits = FALSE), error = function(e) NULL)
  }
  has_function <- is.function(obj)
  args_text <- if (has_function) paste(names(formals(obj)), collapse = ",") else NA_character_
  status <- if (!installed) "missing_package" else if (!has_function) "missing_function" else "ok"
  data.frame(
    package = pkg,
    function_name = fun,
    priority = priority,
    installed = installed,
    version = version,
    has_function = has_function,
    arguments = args_text,
    status = status,
    stringsAsFactors = FALSE
  )
}

result <- do.call(
  rbind,
  Map(inspect_one, checks$package, checks$function_name, checks$priority)
)

if (!identical(output, "")) {
  dir.create(dirname(output), recursive = TRUE, showWarnings = FALSE)
  write.table(result, output, sep = "\t", quote = FALSE, row.names = FALSE, na = "")
} else {
  write.table(result, stdout(), sep = "\t", quote = FALSE, row.names = FALSE, na = "")
}

primary_missing <- result$status != "ok" & result$priority == "primary"
if (any(primary_missing)) {
  message("Primary package/function gaps detected; inspect status rows before running methods.")
}
