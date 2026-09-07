suppressPackageStartupMessages({
  library(MultiAssayExperiment)
  library(SummarizedExperiment)
  library(dplyr)
  library(tidyr)
  library(haven)
})

to_chr <- function(x) {
  if (inherits(x, "haven_labelled")) {
    x <- haven::as_factor(x)
  }
  out <- tryCatch(as.character(x), error = function(e) as.character(unclass(x)))
  out
}

make_table <- function(tbl, page_len = 10, caption = NULL, table_mode = c("auto", "compact", "wide"), export = TRUE, column_widths = NULL) {
  page_len <- suppressWarnings(as.integer(page_len[1]))
  if (!is.finite(page_len) || page_len < 1) {
    page_len <- 10L
  }
  page_len <- min(page_len, 10L)
  table_mode <- match.arg(table_mode)
  is_compact <- if (table_mode == "compact") {
    TRUE
  } else if (table_mode == "wide") {
    FALSE
  } else {
    nrow(tbl) <= 20L && ncol(tbl) <= 6L
  }
  if (is_compact) {
    return(knitr::kable(
      tbl,
      caption = caption,
      format = "html",
      table.attr = 'class="table table-sm table-striped w-auto"'
    ))
  }

  wrapper_class <- "dt-wrap dt-wrap-wide"

  if (requireNamespace('DT', quietly = TRUE)) {
    long_text_cols <- grep(
      paste(
        c(
          "label", "summary", "motivation", "formula", "outcome", "metabolite", "driver",
          "decision", "pair", "levels", "description", "interpretation"
        ),
        collapse = "|"
      ),
      names(tbl),
      ignore.case = TRUE
    ) - 1L
    short_cols <- setdiff(seq_along(tbl) - 1L, long_text_cols)
    column_defs <- list(
      list(targets = "_all", className = "dt-cell")
    )
    if (length(short_cols) > 0) {
      column_defs <- c(column_defs, list(list(targets = short_cols, className = "dt-cell dt-nowrap")))
    }
    if (length(long_text_cols) > 0) {
      column_defs <- c(column_defs, list(list(targets = long_text_cols, className = "dt-cell dt-wrap-cell")))
    }

    use_fixed_layout <- !is.null(column_widths) && length(column_widths) > 0
    if (use_fixed_layout) {
      for (col_name in names(column_widths)) {
        col_idx <- match(col_name, names(tbl)) - 1L
        if (!is.na(col_idx) && col_idx >= 0) {
          column_defs <- c(column_defs, list(list(
            targets = col_idx,
            width = column_widths[[col_name]],
            className = "dt-cell dt-nowrap"
          )))
        }
      }
    }

    options_list <- list(
      pageLength = page_len,
      scrollX = TRUE,
      scrollCollapse = TRUE,
      autoWidth = !use_fixed_layout,
      columnDefs = column_defs
    )
    extensions_list <- character(0)
    if (export) {
      extensions_list <- "Buttons"
      options_list$dom <- "Blfrtip"
      options_list$buttons <- c("copy", "csv", "excel")
    }

    dt <- DT::datatable(
      tbl,
      rownames = FALSE,
      caption = caption,
      filter = "top",
      class = "display compact stripe hover",
      extensions = extensions_list,
      options = options_list
    )

    numeric_cols <- names(tbl)[vapply(tbl, is.numeric, logical(1))]
    if (length(numeric_cols) > 0) {
      is_integer_like <- vapply(tbl[numeric_cols], function(x) {
        x <- x[is.finite(x)]
        if (length(x) == 0) return(FALSE)
        all(abs(x - round(x)) < 1e-8)
      }, logical(1))

      int_cols <- numeric_cols[is_integer_like]
      float_cols <- setdiff(numeric_cols, int_cols)

      if (length(int_cols) > 0) {
        dt <- DT::formatRound(dt, columns = int_cols, digits = 0)
      }
      if (length(float_cols) > 0) {
        dt <- DT::formatRound(dt, columns = float_cols, digits = 3)
      }
    }

    # Conditional formatting based on column name presence (similar to Child diet reports)
    if ("Suggested status" %in% names(tbl)) {
      dt <- dt %>% DT::formatStyle(
        "Suggested status",
        target = "row",
        backgroundColor = DT::styleEqual(
          c("keep_core", "keep_sensitivity", "consider_drop"),
          c("#e2f0d9", "#fff2cc", "#f2f2f2")
        )
      )
    }
    if ("decision" %in% names(tbl)) {
      dt <- dt %>% DT::formatStyle(
        "decision",
        target = "row",
        backgroundColor = DT::styleEqual(
          c("Retain in primary model", "Carry to sensitivity model", 
            "Descriptive or restricted sensitivity only", "Descriptive unless clinically required"),
          c("#e2f0d9", "#fff2cc", "#f2f2f2", "#f2f2f2")
        )
      )
    }
    if ("Collinearity flag" %in% names(tbl)) {
      dt <- dt %>% DT::formatStyle(
        "Collinearity flag",
        target = "row",
        backgroundColor = DT::styleEqual("yes", "#fce4e4")
      )
    }
    if ("flagged" %in% names(tbl)) {
      dt <- dt %>% DT::formatStyle(
        "flagged",
        target = "row",
        backgroundColor = DT::styleEqual(TRUE, "#fce4e4")
      )
    }
    if ("Missing_Pct" %in% names(tbl)) {
      dt <- dt %>% DT::formatStyle(
        "Missing_Pct",
        backgroundColor = DT::styleInterval(c(10, 20), c("transparent", "#ffe6cc", "#fce4e4"))
      )
    }
    if ("Missing_pct" %in% names(tbl)) {
      dt <- dt %>% DT::formatStyle(
        "Missing_pct",
        backgroundColor = DT::styleInterval(c(10, 20), c("transparent", "#ffe6cc", "#fce4e4"))
      )
    }
    if ("Missing pct" %in% names(tbl)) {
      dt <- dt %>% DT::formatStyle(
        "Missing pct",
        backgroundColor = DT::styleInterval(c(10, 20), c("transparent", "#ffe6cc", "#fce4e4"))
      )
    }

    htmltools::div(class = wrapper_class, dt)
  } else {
    knitr::kable(tbl, caption = caption)
  }
}

extract_analysis_data <- function(mae_path = "../data/MAE_original.rds") {
  mae <- readRDS(mae_path)
  
  ex <- experiments(mae)
  visits <- names(ex)
  visits <- visits[grepl('^visit_[0-9]+$', visits)]
  
  out <- lapply(visits, function(vn) {
    tse <- ex[[vn]]
    cd <- as.data.frame(colData(tse), stringsAsFactors = FALSE)
    cd$sample_id <- rownames(cd)
    
    mat <- as.data.frame(t(assay(tse, 'mbo')), check.names = FALSE)
    mat$sample_id <- rownames(mat)
    
    df <- left_join(cd, mat, by = 'sample_id')
    df$visit <- vn
    df$visit_num <- suppressWarnings(as.integer(gsub('visit_', '', vn)))
    df
  })
  
  long_df <- bind_rows(out)
  
  metabolite_cols <- rownames(assay(experiments(mae)[['visit_all']], 'mbo'))
  
  list(
    mae = mae,
    data = long_df,
    metabolite_cols = metabolite_cols
  )
}

round_numeric <- function(tbl, digits = 3) {
  num_cols <- vapply(tbl, is.numeric, logical(1))
  tbl[num_cols] <- lapply(tbl[num_cols], function(x) round(x, digits))
  tbl
}

extract_long_from_mae <- function(mae, visits = NULL) {
  ex <- experiments(mae)
  if (is.null(visits)) {
    visits <- names(ex)
    visits <- visits[grepl('^visit_[0-9]+$', visits)]
  }

  out <- lapply(visits, function(vn) {
    if (!vn %in% names(ex)) return(NULL)
    tse <- ex[[vn]]

    cd <- as.data.frame(colData(tse), stringsAsFactors = FALSE)
    cd$sample_id <- rownames(cd)

    mat <- as.data.frame(t(assay(tse, 'mbo')), check.names = FALSE)
    mat$sample_id <- rownames(mat)

    df <- left_join(cd, mat, by = 'sample_id')
    df$visit <- vn
    df$visit_num <- suppressWarnings(as.integer(gsub('visit_', '', vn)))
    df
  })

  bind_rows(out)
}

prepare_feature_matrix <- function(df, feature_cols, min_feature_obs = 0.7) {
  feature_cols <- intersect(feature_cols, names(df))
  if (length(feature_cols) < 3) {
    return(NULL)
  }

  mat <- as.matrix(df[, feature_cols, drop = FALSE])
  mat <- apply(mat, 2, suppressWarnings, FUN = as.numeric)
  if (!is.matrix(mat)) {
    mat <- matrix(mat, ncol = length(feature_cols))
    colnames(mat) <- feature_cols
  }

  keep_feature <- colMeans(!is.na(mat)) >= min_feature_obs
  mat <- mat[, keep_feature, drop = FALSE]
  if (ncol(mat) < 3 || nrow(mat) < 8) {
    return(NULL)
  }

  for (j in seq_len(ncol(mat))) {
    x <- mat[, j]
    if (all(is.na(x))) next
    med <- stats::median(x, na.rm = TRUE)
    x[is.na(x)] <- med
    mat[, j] <- x
  }

  mat <- mat[, apply(mat, 2, stats::sd, na.rm = TRUE) > 0, drop = FALSE]
  if (ncol(mat) < 3) {
    return(NULL)
  }

  mat
}

compute_pca_df <- function(df, feature_cols, group_cols = character(0), min_feature_obs = 0.7) {
  pca <- compute_pca_details(
    df = df,
    feature_cols = feature_cols,
    group_cols = group_cols,
    min_feature_obs = min_feature_obs,
    top_n_loadings = 8L
  )

  if (is.null(pca)) {
    return(NULL)
  }

  pca$scores
}

compute_pca_details <- function(
  df,
  feature_cols,
  group_cols = character(0),
  min_feature_obs = 0.7,
  top_n_loadings = 8L
) {
  mat <- prepare_feature_matrix(df, feature_cols, min_feature_obs = min_feature_obs)
  if (is.null(mat)) {
    return(NULL)
  }

  group_cols <- intersect(group_cols, names(df))
  top_n_loadings <- max(1L, as.integer(top_n_loadings[1]))

  for (nm in group_cols) {
    attr(mat, paste0("group_", nm)) <- df[[nm]]
  }

  pca_res <- run_pca_mia_first(mat)

  scores <- pca_res$scores
  loadings <- pca_res$loadings
  var_explained <- pca_res$var_explained

  if (ncol(scores) < 2 || ncol(loadings) < 2) {
    return(NULL)
  }

  names(scores)[1:2] <- c("PC1", "PC2")
  colnames(loadings)[1:2] <- c("PC1", "PC2")

  scores$var_pc1 <- var_explained[1]
  scores$var_pc2 <- var_explained[2]

  for (nm in group_cols) {
    scores[[nm]] <- df[[nm]]
  }

  loading_tbl <- data.frame(
    feature = rownames(loadings),
    PC1 = loadings[, 1],
    PC2 = loadings[, 2],
    abs_loading_sum = abs(loadings[, 1]) + abs(loadings[, 2]),
    stringsAsFactors = FALSE
  ) %>%
    arrange(desc(abs_loading_sum))

  top_loadings <- loading_tbl %>%
    slice_head(n = top_n_loadings)

  score_scale <- max(abs(scores$PC1), abs(scores$PC2), na.rm = TRUE)
  loading_scale <- max(abs(top_loadings$PC1), abs(top_loadings$PC2), na.rm = TRUE)
  scale_factor <- ifelse(is.finite(loading_scale) && loading_scale > 0, 0.5 * score_scale / loading_scale, 1)

  top_loadings <- top_loadings %>%
    mutate(
      PC1_scaled = PC1 * scale_factor,
      PC2_scaled = PC2 * scale_factor
    )

  list(
    method = pca_res$method,
    scores = scores,
    loadings = loading_tbl,
    top_loadings = top_loadings,
    var_explained = var_explained
  )
}

run_pca_mia_first <- function(mat) {
  required_pkgs <- c("scater", "TreeSummarizedExperiment", "SingleCellExperiment", "S4Vectors")
  missing_pkgs <- required_pkgs[!vapply(required_pkgs, requireNamespace, logical(1), quietly = TRUE)]
  if (length(missing_pkgs) > 0) {
    stop("Missing required mia ecosystem package(s): ", paste(missing_pkgs, collapse = ", "))
  }

  row_ids <- rownames(mat)
  if (is.null(row_ids)) {
    row_ids <- paste0("sample_", seq_len(nrow(mat)))
    rownames(mat) <- row_ids
  }

  feature_ids <- colnames(mat)
  if (is.null(feature_ids)) {
    feature_ids <- paste0("feature_", seq_len(ncol(mat)))
    colnames(mat) <- feature_ids
  }

  col_data <- S4Vectors::DataFrame(sample_id = row_ids, row.names = row_ids)
  row_data <- S4Vectors::DataFrame(feature = feature_ids, row.names = feature_ids)

  tse <- TreeSummarizedExperiment::TreeSummarizedExperiment(
    assays = list(log10_z = t(mat)),
    rowData = row_data,
    colData = col_data
  )

  set.seed(123)
  tse <- scater::runPCA(
    x = tse,
    assay.type = "log10_z",
    ncomponents = 2,
    scale = TRUE
  )

  rd <- SingleCellExperiment::reducedDim(tse, "PCA")
  scores <- as.data.frame(rd[, 1:2, drop = FALSE], stringsAsFactors = FALSE)
  loadings <- attr(rd, "rotation")
  var_pc <- attr(rd, "percentVar")

  if (is.null(loadings) || ncol(loadings) < 2) {
    stop("PCA loadings unavailable from scater::runPCA")
  }
  if (is.null(var_pc) || length(var_pc) < 2) {
    stop("PCA variance explained unavailable from scater::runPCA")
  }

  list(
    method = "scater::runPCA",
    scores = scores,
    loadings = loadings[, 1:2, drop = FALSE],
    var_explained = as.numeric(var_pc[1:2]) / 100
  )
}
