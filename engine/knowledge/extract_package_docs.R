#!/usr/bin/env Rscript
# Extract function reference documentation and vignettes from installed R packages into structured JSON.

suppressPackageStartupMessages({
  library(jsonlite)
})

extract_package <- function(pkg) {
  if (!pkg %in% rownames(installed.packages())) {
    warning(sprintf("Package '%s' is not installed; skipping.", pkg))
    return(NULL)
  }

  desc <- packageDescription(pkg)
  version <- as.character(desc$Version)
  title <- as.character(desc$Title)
  license <- as.character(desc$License)
  url <- as.character(desc$URL)
  if (length(url) == 0 || is.na(url[1]) || !nzchar(trimws(url[1]))) {
    url <- sprintf("https://bioconductor.org/packages/release/bioc/html/%s.html", pkg)
  } else {
    # If multiple URLs separated by comma or vector, take primary
    url <- trimws(strsplit(url[1], "[, ]+")[[1]][1])
  }

  # 1. Extract Rd Help Database
  topics <- list()
  rd_db <- tryCatch(tools::Rd_db(pkg), error = function(e) NULL)
  
  if (!is.null(rd_db) && length(rd_db) > 0) {
    for (topic_file in names(rd_db)) {
      rd <- rd_db[[topic_file]]
      tags <- tools:::RdTags(rd)
      name <- paste(unlist(lapply(rd[tags == "\\name"], as.character)), collapse = "")
      topic_title <- paste(unlist(lapply(rd[tags == "\\title"], as.character)), collapse = "")
      aliases <- unlist(lapply(rd[tags == "\\alias"], as.character))

      # Extract prose description and arguments
      tmp_txt <- tempfile()
      tryCatch(tools::Rd2txt(rd, out = tmp_txt, options = list(underline_titles = FALSE)), error = function(e) NULL)
      if (file.exists(tmp_txt)) {
        lines <- readLines(tmp_txt, warn = FALSE)
        unlink(tmp_txt)
        ex_idx <- grep("^Examples:\\s*$", lines)
        if (length(ex_idx) > 0) {
          prose <- trimws(paste(lines[1:(ex_idx[1] - 1)], collapse = "\n"))
        } else {
          prose <- trimws(paste(lines, collapse = "\n"))
        }
      } else {
        prose <- ""
      }

      # Extract examples
      tmp_ex <- tempfile()
      tryCatch(tools::Rd2ex(rd, out = tmp_ex), error = function(e) NULL)
      if (file.exists(tmp_ex)) {
        ex_lines <- readLines(tmp_ex, warn = FALSE)
        unlink(tmp_ex)
        header_end <- grep("^### \\*\\* Examples", ex_lines)
        if (length(header_end) > 0 && header_end[1] < length(ex_lines)) {
          code <- trimws(paste(ex_lines[(header_end[1] + 1):length(ex_lines)], collapse = "\n"))
        } else {
          code <- ""
        }
      } else {
        code <- ""
      }

      topics[[length(topics) + 1]] <- list(
        name = name,
        title = topic_title,
        aliases = as.list(aliases),
        prose = prose,
        code = code,
        rd_file = topic_file
      )
    }
  }

  # 2. Extract Vignette files (.Rmd, .qmd)
  doc_dir <- system.file("doc", package = pkg)
  vignette_files <- list()
  if (dir.exists(doc_dir)) {
    v_files <- list.files(doc_dir, pattern = "\\.(Rmd|qmd)$", full.names = TRUE)
    for (vf in v_files) {
      vignette_files[[length(vignette_files) + 1]] <- list(
        path = vf,
        filename = basename(vf)
      )
    }
  }

  list(
    package = pkg,
    version = version,
    title = title,
    license = license,
    url = url,
    topic_count = length(topics),
    vignette_count = length(vignette_files),
    topics = topics,
    vignettes = vignette_files
  )
}

main <- function() {
  args <- commandArgs(trailingOnly = TRUE)
  output_file <- "extracted_package_docs.json"
  pkgs <- c()

  i <- 1
  while (i <= length(args)) {
    if (args[i] == "--output" || args[i] == "-o") {
      output_file <- args[i + 1]
      i <- i + 2
    } else {
      pkgs <- c(pkgs, args[i])
      i <- i + 1
    }
  }

  if (length(pkgs) == 0) {
    pkgs <- c(
      "mia",
      "miaViz",
      "TreeSummarizedExperiment",
      "SingleCellExperiment",
      "SummarizedExperiment",
      "scater",
      "scran",
      "DESeq2"
    )
  }

  cat(sprintf("Extracting documentation for %d packages...\n", length(pkgs)))
  results <- list()
  for (p in pkgs) {
    cat(sprintf("  Extracting '%s'...", p))
    t0 <- Sys.time()
    extracted <- extract_package(p)
    if (!is.null(extracted)) {
      results[[length(results) + 1]] <- extracted
      t1 <- Sys.time()
      cat(sprintf(" done (%d topics, %d vignettes in %.2fs)\n", 
                  extracted$topic_count, extracted$vignette_count, as.numeric(t1 - t0, units = "secs")))
    } else {
      cat(" failed / missing\n")
    }
  }

  cat(sprintf("Writing extracted documentation to %s...\n", output_file))
  jsonlite::write_json(results, path = output_file, auto_unbox = TRUE, pretty = TRUE)
  cat("Extraction complete.\n")
}

if (!interactive()) {
  main()
}
