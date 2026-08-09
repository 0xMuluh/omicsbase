#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(officer)
  library(flextable)
  library(readr)
  library(dplyr)
})

# Helper to create formatted flextable
make_ft_from_csv <- function(csv_path) {
  df <- read_csv(
    csv_path,
    col_types = cols(.default = col_character()),
    na = character(),
    show_col_types = FALSE
  )
  ft <- flextable(df) %>%
    autofit() %>%
    align(align = "left")
  
  if ("characteristic" %in% names(df)) {
    idx <- which(df$characteristic %in% c("Mother", "Child"))
    if (length(idx) > 0) {
      ft <- bold(ft, i = idx, bold = TRUE)
    }
  }
  if ("variable" %in% names(df)) {
    idx <- which(df$variable %in% c("Maternal characteristics", "Child characteristics", "Age at metabolomics sampling"))
    if (length(idx) > 0) {
      ft <- bold(ft, i = idx, bold = TRUE)
    }
  }
  ft
}

# ====================================
# PRENATAL DIET
# ====================================
populate_prenatal_diet_docx <- function() {
  cat("Processing Prenatal diet characteristics DOCX...\n")
  
  # Create fresh document
  doc <- read_docx()
  
  # Add title and tables
  doc <- body_add_par(doc, "Prenatal diet Study: Maternal and Child Characteristics", style = "heading 1")
  doc <- body_add_par(doc, "")
  doc <- body_add_par(doc, "Table 1. Maternal and Child Baseline Characteristics", style = "heading 2")
  
  # Add Table 1
  ft1 <- make_ft_from_csv("data/prenatal_diet_characteristics_table1.csv")
  doc <- body_add_flextable(doc, value = ft1)
  
  doc <- body_add_par(doc, "")
  doc <- body_add_par(doc, "Table 2. Maternal Diet Characteristics: Early vs Late Pregnancy", style = "heading 2")
  
  # Add Table 2
  ft2 <- make_ft_from_csv("data/prenatal_diet_characteristics_table2.csv")
  doc <- body_add_flextable(doc, value = ft2)
  

  # Save updated document
  output_path <- "data/Table of characteristics_POPULATED.docx"
  print(doc, target = output_path)
  cat("  ✓ Saved:", output_path, "\n")
  
  invisible(doc)
}

# ====================================
# CHILD DIET
# ====================================
populate_child_diet_docx <- function() {
  cat("Processing Child diet characteristics DOCX...\n")
  
  # Create fresh document
  doc <- read_docx()
  
  # Add title and tables
  doc <- body_add_par(doc, "Child diet Study: Child Characteristics by Visit", style = "heading 1")
  doc <- body_add_par(doc, "")
  doc <- body_add_par(doc, "Table 1. Characteristics at Visit 6 (2 years)", style = "heading 2")
  
  # Add Visit 6 table
  ft6 <- make_ft_from_csv("data/child_diet_characteristics_visit6.csv")
  doc <- body_add_flextable(doc, value = ft6)
  
  # Add Visit 7 table
  doc <- body_add_par(doc, "")
  doc <- body_add_par(doc, "Table 2. Characteristics at Visit 7 (5-6 years)", style = "heading 2")
  
  ft7 <- make_ft_from_csv("data/child_diet_characteristics_visit7.csv")
  doc <- body_add_flextable(doc, value = ft7)
  
  # Save updated document
  output_path <- "data/Characteristics table_draft_250326_POPULATED.docx"
  print(doc, target = output_path)
  cat("  ✓ Saved:", output_path, "\n")
  
  invisible(doc)
}

# ====================================
# MAIN
# ====================================
main <- function() {
  cat("=== Populating Characteristics DOCX Files ===\n\n")
  
  tryCatch({
    populate_prenatal_diet_docx()
    cat("\n")
    populate_child_diet_docx()
    cat("\n✓ All DOCX files populated successfully!\n")
  }, error = function(e) {
    cat("ERROR:", conditionMessage(e), "\n")
  })
}

if (identical(environment(), globalenv())) {
  main()
}
