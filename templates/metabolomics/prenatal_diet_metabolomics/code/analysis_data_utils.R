suppressPackageStartupMessages({
  library(MultiAssayExperiment)
  library(SummarizedExperiment)
  library(dplyr)
  library(tidyr)
})

source('shared/metabolomics_common.R')

make_table <- function(tbl, page_len = 10, caption = NULL, table_mode = c("auto", "compact", "wide"), export = TRUE) {
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
  filter_mode <- if (is_compact) "none" else "top"
  wrapper_class <- if (is_compact) "dt-wrap dt-wrap-compact" else "dt-wrap dt-wrap-wide"

  if (requireNamespace('DT', quietly = TRUE)) {
    options_list <- list(
      pageLength = page_len,
      scrollX = FALSE,
      autoWidth = FALSE
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
      filter = filter_mode,
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

    htmltools::div(class = wrapper_class, dt)
  } else {
    tbl
  }
}

round_numeric <- function(tbl, digits = 3) {
  num_cols <- vapply(tbl, is.numeric, logical(1))
  tbl[num_cols] <- lapply(tbl[num_cols], function(x) round(x, digits))
  tbl
}

add_ci <- function(tbl) {
  if (!all(c('estimate', 'std.error') %in% names(tbl))) return(tbl)
  tbl %>%
    mutate(
      conf.low = ifelse(is.na(estimate) | is.na(std.error), NA_real_, estimate - 1.96 * std.error),
      conf.high = ifelse(is.na(estimate) | is.na(std.error), NA_real_, estimate + 1.96 * std.error)
    )
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

prepare_analysis_data <- function(mae_path = '../data/MAE_original.rds', visits = NULL) {
  mae <- readRDS(mae_path)
  metabolite_cols <- rownames(assay(experiments(mae)[['visit_all']], 'mbo'))

  df <- extract_long_from_mae(mae, visits = visits)
  df$StudyID <- as.character(df$StudyID)

  df <- log_z_by_visit(df, metabolite_cols = metabolite_cols, visit_col = 'visit_num')

  cl <- build_cluster_scores(df, metabolite_cols)

  list(
    mae = mae,
    data = cl$data,
    metabolite_cols = metabolite_cols,
    cluster_cols = cl$cluster_cols,
    cluster_map = cl$cluster_map
  )
}

ensure_results_dir <- function(path = '../output/results') {
  dir.create(path, recursive = TRUE, showWarnings = FALSE)
  path
}

split_feature_tracks <- function(feature_cols, ratio_pattern = "-ratio") {
  ratio_idx <- grepl(ratio_pattern, feature_cols, fixed = TRUE)
  list(
    metabolites = feature_cols[!ratio_idx],
    ratio = feature_cols[ratio_idx]
  )
}

make_exposure_tertiles <- function(x) {
  x <- suppressWarnings(as.numeric(x))
  out <- rep(NA_character_, length(x))

  keep <- is.finite(x)
  if (sum(keep) < 6) {
    return(factor(out, levels = c("Low", "Mid", "High")))
  }

  r <- rank(x[keep], ties.method = "average")
  g <- ceiling(3 * r / max(r))
  g[g < 1] <- 1
  g[g > 3] <- 3

  out[keep] <- c("Low", "Mid", "High")[g]
  factor(out, levels = c("Low", "Mid", "High"))
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

run_distance_tests <- function(df, feature_cols, group_var, min_feature_obs = 0.7) {
  out <- data.frame(
    group = group_var,
    n_samples = nrow(df),
    n_features = NA_integer_,
    n_groups = NA_integer_,
    betadisper_p = NA_real_,
    permanova_p = NA_real_,
    stringsAsFactors = FALSE
  )

  if (!requireNamespace("vegan", quietly = TRUE)) {
    out$group <- paste0(group_var, " (vegan unavailable)")
    return(out)
  }
  if (!group_var %in% names(df)) {
    out$group <- paste0(group_var, " (missing)")
    return(out)
  }

  mat <- prepare_feature_matrix(df, feature_cols, min_feature_obs = min_feature_obs)
  if (is.null(mat)) {
    out$group <- paste0(group_var, " (insufficient feature matrix)")
    return(out)
  }

  grp <- as.factor(df[[group_var]])
  keep <- !is.na(grp)
  if (sum(keep) < 8) {
    out$group <- paste0(group_var, " (too few grouped samples)")
    return(out)
  }

  mat <- mat[keep, , drop = FALSE]
  grp <- droplevels(grp[keep])

  out$n_samples <- nrow(mat)
  out$n_features <- ncol(mat)
  out$n_groups <- nlevels(grp)
  if (out$n_groups < 2) {
    out$group <- paste0(group_var, " (single group)")
    return(out)
  }

  dist_obj <- tryCatch(vegan::vegdist(mat, method = "euclidean"), error = function(e) NULL)
  if (is.null(dist_obj)) {
    out$group <- paste0(group_var, " (distance failed)")
    return(out)
  }

  bd <- tryCatch(vegan::betadisper(dist_obj, grp), error = function(e) NULL)
  if (!is.null(bd)) {
    out$betadisper_p <- tryCatch(as.numeric(stats::anova(bd)[["Pr(>F)"]][1]), error = function(e) NA_real_)
  }

  ad <- tryCatch(vegan::adonis2(dist_obj ~ grp, permutations = 999), error = function(e) NULL)
  if (!is.null(ad) && "Pr(>F)" %in% colnames(ad)) {
    out$permanova_p <- as.numeric(ad[1, "Pr(>F)"])
  }

  out
}

run_kruskal_screen <- function(df, feature_cols, group_var, min_complete = 24L) {
  feature_cols <- intersect(feature_cols, names(df))
  if (!group_var %in% names(df) || length(feature_cols) == 0) {
    return(data.frame())
  }

  grp_full <- as.factor(df[[group_var]])
  res <- lapply(feature_cols, function(feat) {
    y <- suppressWarnings(as.numeric(df[[feat]]))
    keep <- !is.na(y) & !is.na(grp_full)
    g <- droplevels(grp_full[keep])
    y <- y[keep]

    if (length(y) < min_complete || nlevels(g) < 2 || length(unique(y)) < 5) {
      return(data.frame(
        feature = feat,
        p.value = NA_real_,
        q.value = NA_real_,
        n = length(y),
        groups = nlevels(g),
        note = "insufficient_data",
        stringsAsFactors = FALSE
      ))
    }

    p <- tryCatch(stats::kruskal.test(y ~ g)$p.value, error = function(e) NA_real_)
    data.frame(
      feature = feat,
      p.value = p,
      q.value = NA_real_,
      n = length(y),
      groups = nlevels(g),
      note = ifelse(is.na(p), "test_failed", "ok"),
      stringsAsFactors = FALSE
    )
  })

  res <- bind_rows(res)
  ok <- res$note == "ok" & !is.na(res$p.value)
  if (any(ok)) {
    res$q.value[ok] <- p.adjust(res$p.value[ok], method = "BH")
  }
  res %>% arrange(q.value, p.value)
}

make_heatmap_df <- function(df, feature_cols, group_var) {
  feature_cols <- intersect(feature_cols, names(df))
  if (length(feature_cols) == 0 || !group_var %in% names(df)) {
    return(data.frame())
  }

  dat <- df %>%
    dplyr::select(all_of(c(group_var, feature_cols))) %>%
    tidyr::pivot_longer(cols = all_of(feature_cols), names_to = "feature", values_to = "value") %>%
    mutate(value = suppressWarnings(as.numeric(value))) %>%
    filter(!is.na(.data[[group_var]]), is.finite(value)) %>%
    group_by(.data[[group_var]], feature) %>%
    summarise(mean_value = mean(value, na.rm = TRUE), .groups = "drop")

  names(dat)[1] <- "group"
  dat
}
