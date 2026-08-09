source("code/00_setup.R")

plan <- read_analysis_plan()
input_path <- file.path(plan$paths$derived_dir, "microbiome_analysis_data.rds")
if (!file.exists(input_path)) stop("Run code/01_prepare_data.R before diversity analysis", call. = FALSE)
obj <- readRDS(input_path)
model_spec <- read_table_file("config/model_specification.csv")

metadata <- obj$metadata
mat <- obj$feature_matrix
row_totals <- rowSums(mat, na.rm = TRUE)
rel <- mat
rel[row_totals > 0, ] <- rel[row_totals > 0, , drop = FALSE] / row_totals[row_totals > 0]
rel[row_totals == 0, ] <- 0

clean_value <- function(x) {
  if (is.null(x) || length(x) == 0 || is.na(x[[1]])) return("")
  trimws(as.character(x[[1]]))
}

is_blank_or_marker <- function(x) {
  x <- clean_value(x)
  identical(x, "") || required_marker(x) || optional_marker(x)
}

bt <- function(x) {
  if (grepl("^[A-Za-z.][A-Za-z0-9._]*$", x)) return(x)
  paste0("`", gsub("`", "", x), "`")
}

alpha <- data.frame(sample_id = rownames(mat), stringsAsFactors = FALSE)
alpha$observed <- rowSums(mat > 0, na.rm = TRUE)
alpha$shannon <- apply(rel, 1, function(p) { p <- p[p > 0]; -sum(p * log(p)) })
alpha$simpson <- apply(rel, 1, function(p) 1 - sum(p ^ 2))
alpha <- merge(metadata, alpha, by.x = plan$identifiers$sample_id, by.y = "sample_id", all.x = FALSE, all.y = FALSE)

alpha_results <- list()
status <- list()
ri <- 0L
si <- 0L

add_status <- function(analysis_id, target, status_value, reason, n = NA_integer_) {
  si <<- si + 1L
  status[[si]] <<- data.frame(
    analysis_id = analysis_id,
    target = target,
    status = status_value,
    reason = reason,
    n = n,
    stringsAsFactors = FALSE
  )
}

add_alpha_result <- function(row) {
  ri <<- ri + 1L
  alpha_results[[ri]] <<- row
}

min_n_for <- function(spec) {
  out <- suppressWarnings(as.integer(clean_value(spec$min_n)))
  if (is.na(out)) out <- plan$analyses$default_min_n
  out
}

fit_alpha_lm <- function(spec, metric, exposure, terms, complete) {
  fit <- stats::lm(stats::reformulate(terms, response = metric), data = alpha[complete, , drop = FALSE])
  coefs <- as.data.frame(summary(fit)$coefficients, stringsAsFactors = FALSE)
  coefs$term <- rownames(coefs)
  names(coefs)[1:4] <- c("estimate", "std.error", "statistic", "p.value")
  term_rows <- coefs$term == exposure | startsWith(coefs$term, exposure)
  if (!any(term_rows)) term_rows <- grepl(exposure, coefs$term, fixed = TRUE)
  selected <- coefs[term_rows, , drop = FALSE]
  for (k in seq_len(nrow(selected))) {
    add_alpha_result(data.frame(
      analysis_id = clean_value(spec$analysis_id),
      scenario = clean_value(spec$scenario),
      method = clean_value(spec$method),
      metric = metric,
      exposure = exposure,
      term = selected$term[k],
      estimate = selected$estimate[k],
      std.error = selected$std.error[k],
      statistic = selected$statistic[k],
      p.value = selected$p.value[k],
      n = sum(complete),
      status = "fitted",
      stringsAsFactors = FALSE
    ))
  }
}

fit_alpha_rank <- function(spec, metric, exposure, complete) {
  dat <- alpha[complete, c(metric, exposure), drop = FALSE]
  dat[[exposure]] <- factor(dat[[exposure]])
  levels_used <- levels(dat[[exposure]])
  if (length(levels_used) < 2L) {
    add_status(clean_value(spec$analysis_id), metric, "skipped", "Exposure has fewer than two levels", nrow(dat))
    return(invisible(NULL))
  }
  if (length(levels_used) == 2L) {
    fit <- stats::wilcox.test(dat[[metric]] ~ dat[[exposure]], exact = FALSE)
    estimate <- diff(tapply(dat[[metric]], dat[[exposure]], mean, na.rm = TRUE))[[1]]
    term <- paste(levels_used, collapse = " vs ")
    method <- "wilcoxon_fdr"
  } else {
    fit <- stats::kruskal.test(dat[[metric]] ~ dat[[exposure]])
    estimate <- NA_real_
    term <- exposure
    method <- "kruskal_fdr"
  }
  add_alpha_result(data.frame(
    analysis_id = clean_value(spec$analysis_id),
    scenario = clean_value(spec$scenario),
    method = method,
    metric = metric,
    exposure = exposure,
    term = term,
    estimate = estimate,
    std.error = NA_real_,
    statistic = unname(fit$statistic),
    p.value = fit$p.value,
    n = nrow(dat),
    status = "fitted",
    stringsAsFactors = FALSE
  ))
}

fit_alpha_lmer <- function(spec, metric, exposure) {
  analysis_id <- clean_value(spec$analysis_id)
  subject_id <- clean_value(plan$identifiers$subject_id)
  visit <- clean_value(plan$identifiers$visit)
  if (is_blank_or_marker(subject_id) || is_blank_or_marker(visit)) {
    add_status(analysis_id, metric, "missing_repeated_structure", "subject_id and visit are required for lmer_interaction")
    return(invisible(NULL))
  }
  needed <- c(metric, exposure, subject_id, visit)
  missing_cols <- setdiff(needed, names(alpha))
  if (length(missing_cols)) {
    add_status(analysis_id, metric, "missing_columns", paste(missing_cols, collapse = ", "))
    return(invisible(NULL))
  }
  if (!requireNamespace("lmerTest", quietly = TRUE)) {
    add_status(analysis_id, metric, "unavailable", "Package lmerTest is required for lmer_interaction")
    return(invisible(NULL))
  }
  complete <- stats::complete.cases(alpha[, needed, drop = FALSE])
  if (sum(complete) < min_n_for(spec)) {
    add_status(analysis_id, metric, "skipped", "Insufficient complete cases", sum(complete))
    return(invisible(NULL))
  }
  formula <- stats::as.formula(sprintf("%s ~ %s * %s + (1 | %s)", bt(metric), bt(exposure), bt(visit), bt(subject_id)))
  fit <- try(lmerTest::lmer(formula, data = alpha[complete, , drop = FALSE]), silent = TRUE)
  if (inherits(fit, "try-error")) {
    add_status(analysis_id, metric, "failed", as.character(fit)[1], sum(complete))
    return(invisible(NULL))
  }
  coefs <- as.data.frame(summary(fit)$coefficients, stringsAsFactors = FALSE)
  coefs$term <- rownames(coefs)
  p_col <- grep("Pr", names(coefs), value = TRUE)[1]
  selected <- coefs[coefs$term != "(Intercept)", , drop = FALSE]
  for (k in seq_len(nrow(selected))) {
    add_alpha_result(data.frame(
      analysis_id = analysis_id,
      scenario = clean_value(spec$scenario),
      method = "lmer_interaction",
      metric = metric,
      exposure = exposure,
      term = selected$term[k],
      estimate = selected$Estimate[k],
      std.error = selected$`Std. Error`[k],
      statistic = selected$`t value`[k],
      p.value = selected[[p_col]][k],
      n = sum(complete),
      status = "fitted",
      stringsAsFactors = FALSE
    ))
  }
  add_status(analysis_id, metric, "fitted", "lmerTest::lmer interaction model fitted", sum(complete))
}

alpha_specs <- model_spec[model_spec$analysis_family == "alpha_diversity", , drop = FALSE]
for (i in seq_len(nrow(alpha_specs))) {
  spec <- alpha_specs[i, , drop = FALSE]
  exposure <- clean_value(spec$exposure)
  analysis_id <- clean_value(spec$analysis_id)
  method <- tolower(clean_value(spec$method))
  if (is_blank_or_marker(exposure) || !exposure %in% names(alpha)) {
    add_status(analysis_id, "alpha", "missing_exposure", paste("Exposure not found:", exposure))
    next
  }
  covariates <- get_covariates(plan, clean_value(spec$covariates))
  missing_covariates <- setdiff(covariates, names(alpha))
  if (length(missing_covariates)) {
    add_status(analysis_id, "alpha", "missing_covariates", paste(missing_covariates, collapse = ", "))
    next
  }
  terms <- c(exposure, covariates)
  for (metric in plan$analyses$alpha_metrics) {
    if (!metric %in% names(alpha)) {
      add_status(analysis_id, metric, "missing_metric", paste("Metric not found:", metric))
      next
    }
    if (method %in% c("lmer_interaction", "lmm", "mixed_model")) {
      fit_alpha_lmer(spec, metric, exposure)
      next
    }
    complete <- stats::complete.cases(alpha[, c(metric, terms), drop = FALSE])
    if (sum(complete) < min_n_for(spec)) {
      add_status(analysis_id, metric, "skipped", "Insufficient complete cases", sum(complete))
      next
    }
    if (method %in% c("wilcoxon_fdr", "kruskal_fdr", "rank_test", "rank_tests")) {
      fit_alpha_rank(spec, metric, exposure, complete)
    } else {
      fit_alpha_lm(spec, metric, exposure, terms, complete)
    }
    add_status(analysis_id, metric, "fitted", paste("Alpha method fitted:", method), sum(complete))
  }
}

alpha_result_table <- if (length(alpha_results)) do.call(rbind, alpha_results) else data.frame()
if (nrow(alpha_result_table)) {
  alpha_result_table$q.value <- ave(alpha_result_table$p.value, alpha_result_table$analysis_id, FUN = function(p) stats::p.adjust(p, method = plan$analyses$fdr_method))
}

beta_status <- data.frame()
beta_specs <- model_spec[model_spec$analysis_family == "beta_diversity", , drop = FALSE]
ordination <- data.frame()
if (nrow(beta_specs)) {
  dist <- bray_distance(mat)
  ord <- stats::cmdscale(dist, k = 2, eig = TRUE)
  ordination <- data.frame(sample_id = rownames(mat), axis1 = ord$points[, 1], axis2 = ord$points[, 2], stringsAsFactors = FALSE)
  write_tsv(ordination, file.path(plan$paths$results_dir, "ordination_coordinates.tsv"))
  for (i in seq_len(nrow(beta_specs))) {
    spec <- beta_specs[i, , drop = FALSE]
    if (!requireNamespace("vegan", quietly = TRUE)) {
      beta_status <- rbind(beta_status, data.frame(analysis_id = spec$analysis_id, target = "permanova", status = "unavailable", reason = "Package vegan is required for adonis2", n = NA_integer_, stringsAsFactors = FALSE))
      next
    }
    exposure <- clean_value(spec$exposure)
    if (is_blank_or_marker(exposure) || !exposure %in% names(metadata)) {
      beta_status <- rbind(beta_status, data.frame(analysis_id = spec$analysis_id, target = "permanova", status = "missing_exposure", reason = paste("Exposure not found:", exposure), n = NA_integer_, stringsAsFactors = FALSE))
      next
    }
    formula <- stats::as.formula(paste("dist ~", paste(c(exposure, get_covariates(plan, clean_value(spec$covariates))), collapse = " + ")))
    set.seed(plan$analyses$seed)
    fit <- vegan::adonis2(formula, data = metadata, permutations = as.integer(spec$permutations))
    write_tsv(as.data.frame(fit), file.path(plan$paths$results_dir, paste0(spec$analysis_id, "_permanova.tsv")))
    beta_status <- rbind(beta_status, data.frame(analysis_id = spec$analysis_id, target = "permanova", status = "fitted", reason = "PERMANOVA fitted with vegan::adonis2", n = NA_integer_, stringsAsFactors = FALSE))
  }
}

model_status <- rbind(if (length(status)) do.call(rbind, status) else data.frame(), beta_status)
result_object <- list(
  plan = plan,
  model_specification = model_spec,
  sample_summary = obj$sample_summary,
  feature_summary = obj$feature_summary,
  alpha_diversity = alpha,
  alpha_results = alpha_result_table,
  beta_diversity = list(ordination = ordination, status = beta_status),
  model_status = model_status
)
ensure_dir(plan$paths$results_dir)
saveRDS(result_object, file.path(plan$paths$results_dir, "microbiome_diversity_results.rds"))
write_tsv(alpha, file.path(plan$paths$results_dir, "alpha_diversity.tsv"))
write_tsv(alpha_result_table, file.path(plan$paths$results_dir, "alpha_diversity_results.tsv"))
write_tsv(model_status, file.path(plan$paths$results_dir, "diversity_model_status.tsv"))
message("Wrote microbiome diversity outputs")
