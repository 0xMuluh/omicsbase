suppressPackageStartupMessages({
  library(circlize)
})

safe_logp_circular <- function(p) {
  p <- suppressWarnings(as.numeric(p))
  p[p <= 0 | is.na(p)] <- NA_real_
  -log10(p)
}

plot_circular_manhattan <- function(
  df,
  title_str,
  p_col = "p.value",
  estimate_col = "estimate",
  feature_col = "feature",
  group_col = "feature_group",
  max_logp = 30,
  label_top_per_group = 2L,
  label_cap = 50L,
  nominal_p = 0.05,
  ...
) {
  if (is.null(df) || nrow(df) == 0) return(NULL)

  required_cols <- c(feature_col, group_col, p_col, estimate_col)
  if (!all(required_cols %in% names(df))) {
    stop("Missing required columns for circular Manhattan plot: ",
         paste(setdiff(required_cols, names(df)), collapse = ", "))
  }

  plot_df <- data.frame(
    feature = as.character(df[[feature_col]]),
    feature_group = as.character(df[[group_col]]),
    p.value = suppressWarnings(as.numeric(df[[p_col]])),
    estimate = suppressWarnings(as.numeric(df[[estimate_col]])),
    stringsAsFactors = FALSE
  )

  plot_df$feature_group[is.na(plot_df$feature_group) | plot_df$feature_group == ""] <- "Unknown"
  plot_df$feature[is.na(plot_df$feature) | plot_df$feature == ""] <- "Unnamed feature"

  plot_df <- plot_df[is.finite(plot_df$p.value) & plot_df$p.value > 0 & is.finite(plot_df$estimate), , drop = FALSE]
  if (nrow(plot_df) == 0) return(NULL)

  plot_df <- plot_df[order(plot_df$feature, plot_df$p.value), , drop = FALSE]
  plot_df <- plot_df[!duplicated(plot_df$feature), , drop = FALSE]
  if (nrow(plot_df) == 0) return(NULL)

  plot_df <- plot_df[order(plot_df$feature_group, plot_df$feature), , drop = FALSE]
  plot_df$logp <- pmin(safe_logp_circular(plot_df$p.value), as.numeric(max_logp))
  plot_df$direction <- ifelse(plot_df$estimate >= 0, "positive", "negative")

  n_tests <- nrow(plot_df)
  bonf_p <- nominal_p / n_tests
  bonf_line <- -log10(bonf_p)
  nominal_line <- -log10(nominal_p)

  groups <- unique(plot_df$feature_group)
  top_group_labels <- unlist(lapply(groups, function(g) {
    sub <- plot_df[plot_df$feature_group == g, , drop = FALSE]
    sub <- sub[order(sub$p.value), , drop = FALSE]
    head(sub$feature, as.integer(label_top_per_group))
  }), use.names = FALSE)
  bonf_labels <- plot_df$feature[plot_df$p.value <= bonf_p]
  label_candidates <- unique(c(bonf_labels, top_group_labels))

  if (length(label_candidates) > 0) {
    label_df <- plot_df[match(label_candidates, plot_df$feature), c("feature", "p.value"), drop = FALSE]
    label_df <- label_df[order(label_df$p.value), , drop = FALSE]
    label_keep <- head(label_df$feature, as.integer(label_cap))
  } else {
    label_keep <- character(0)
  }
  plot_df$label_flag <- plot_df$feature %in% label_keep

  group_rle <- rle(plot_df$feature_group)
  gap_after <- rep(1, nrow(plot_df))
  group_end_idx <- cumsum(group_rle$lengths)
  gap_after[group_end_idx] <- 3
  gap_after[length(gap_after)] <- 10

  y_max <- max(c(1, plot_df$logp, bonf_line, nominal_line), na.rm = TRUE)
  y_max <- y_max + 0.5
  #y_ticks <- pretty(c(0, y_max), n = 6)
  y_ticks <- seq(0, floor(y_max), by = 1)
  y_ticks <- y_ticks[y_ticks >= 0 & y_ticks <= y_max]

  old_par <- graphics::par(no.readonly = TRUE)
  on.exit(graphics::par(old_par), add = TRUE)
  circlize::circos.clear()
  on.exit(circlize::circos.clear(), add = TRUE)

  graphics::par(mar = c(1, 1, 3.5, 1), xpd = NA)
  circlize::circos.par(
    start.degree = 70,
    gap.after = gap_after,
    cell.padding = c(0, 0, 0, 0),
    track.margin = c(0.01, 0.01),
    points.overflow.warning = FALSE
  )
  
  #circlize::circos.par(gap.after = c(rep(1, nrow(plot_df)-1), 10))
  
  circlize::circos.initialize(
    factors = plot_df$feature,
    xlim = cbind(rep(0, nrow(plot_df)), rep(1, nrow(plot_df)))
  )

  
  # 4. Track 1: Outer Labels
  circlize::circos.track(
    ylim = c(0, y_max),
    track.height = 0.05,
    bg.border = NA,
    panel.fun = function(x, y) {
      idx <- circlize::CELL_META$sector.numeric.index
      # Grey text for non-significant, Black text for significant (P < 0.05)
      label_col <- if (plot_df$p.value[idx] <= nominal_p) "black" else "grey60"
      
      circlize::circos.text(
        x = 0.5,
        y = 0,
        labels = plot_df$feature[idx],
        facing = "clockwise",
        niceFacing = TRUE,
        adj = c(0, 0.5),
        cex = 0.55,
        col = label_col
      )
    }
  )
  
  circlize::circos.track(
    ylim = c(0, y_max),
    track.height = 0.30,
    bg.border = NA,
    panel.fun = function(x, y) {
      idx <- circlize::CELL_META$sector.numeric.index
      bar_val <- plot_df$logp[idx]
      bar_col <- if (plot_df$direction[idx] == "positive") "#2ca02c" else "#d62728"
      n <- nrow(plot_df)
      
      # concentric grid lines
      for (h in y_ticks) {
        circlize::circos.segments(
          0, h, 1, h,
          col = "grey70",
          lwd = 1,
          lty = 1
        )
      }


      circlize::circos.rect(0, 0, 1, bar_val, col = bar_col, border = bar_col)
      circlize::circos.segments(0, bonf_line, 1, bonf_line, col = "#d62728", lwd = 1.4)
      circlize::circos.segments(0, nominal_line, 1, nominal_line, col = "#1f77b4", lwd = 1.1, lty = 2)

      if (idx == 1) {
        circlize::circos.yaxis(side = "left", at = y_ticks, labels = y_ticks, labels.cex = 0.55, tick.length = 0.02)
      }
      
      if (idx == n) {
        
        circlize::circos.yaxis(side = "right", at = y_ticks, labels = y_ticks, labels.cex = 0.55, tick.length = 0.02)
        circlize::circos.text(
          x = 6, 
          y = y_max / 2, 
          labels = "-log10(p)", 
          facing = "clockwise", # This rotates the text vertically
          niceFacing = TRUE, 
          cex = 0.8, 
          font = 2 # Bold
        )
      }
    }
  )

  # circlize::circos.track(
  #   ylim = c(0, 1),
  #   track.height = 0.22,
  #   bg.border = NA,
  #   panel.fun = function(x, y) {
  #     idx <- circlize::CELL_META$sector.numeric.index
  #     if (isTRUE(plot_df$label_flag[idx])) {
  #       label_col <- if (plot_df$p.value[idx] <= bonf_p) "black" else "grey35"
  #       circlize::circos.text(
  #         x = circlize::CELL_META$xcenter,
  #         y = 0,
  #         labels = plot_df$feature[idx],
  #         facing = "clockwise",
  #         niceFacing = TRUE,
  #         adj = c(0, 0.5),
  #         cex = 0.45,
  #         col = label_col
  #       )
  #     }
  #   }
  # )
  


  graphics::title(main = title_str, cex.main = 1.1, font.main = 2, line = 0.5)

  graphics::legend(
    "topright",
    inset = c(0.02, 0.02),
    bty = "n",
    title = "Thresholds",
    legend = c("Bonferroni correction", "P<0.05"),
    col = c("#d62728", "#1f77b4"),
    lwd = c(2, 1.8),
    lty = c(1, 2),
    seg.len = 2.4
  )

  graphics::legend(
    "bottomright",
    inset = c(0.02, 0.02),
    bty = "n",
    title = "Direction of association",
    legend = c("positive", "negative"),
    pch = 15,
    pt.cex = 1.2,
    col = c("#2ca02c", "#d62728")
  )

  invisible(list(
    n_features = n_tests,
    bonferroni_p = bonf_p,
    n_labeled = sum(plot_df$label_flag),
    labeled_features = plot_df$feature[plot_df$label_flag]
  ))
}
