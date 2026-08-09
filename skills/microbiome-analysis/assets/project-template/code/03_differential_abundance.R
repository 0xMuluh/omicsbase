source("code/00_setup.R")

plan <- read_analysis_plan()
input_path <- file.path(plan$paths$derived_dir, "microbiome_analysis_data.rds")
if (!file.exists(input_path)) stop("Run code/01_prepare_data.R before differential abundance", call. = FALSE)
obj <- readRDS(input_path)
model_spec <- read_table_file("config/model_specification.csv")

da_specs <- model_spec[model_spec$analysis_family == "differential_abundance", , drop = FALSE]
metadata <- obj$metadata
mat <- obj$feature_matrix
pseudo_count <- plan$features$pseudo_count
log_mat <- log(mat + pseudo_count)
clr <- log_mat - rowMeans(log_mat, na.rm = TRUE)

results <- list()
status <- list()
ri <- 0L
si <- 0L

clean_value <- function(x) {
  if (is.null(x) || length(x) == 0 || is.na(x[[1]])) return("")
  trimws(as.character(x[[1]]))
}

is_blank_or_marker <- function(x) {
  x <- clean_value(x)
  identical(x, "") || required_marker(x) || optional_marker(x)
}

add_status <- function(analysis_id, feature, method, status_value, reason, n = NA_integer_) {
  si <<- si + 1L
  status[[si]] <<- data.frame(
    analysis_id = analysis_id,
    feature = feature,
    method = method,
    status = status_value,
    reason = reason,
    n = n,
    stringsAsFactors = FALSE
  )
}

add_result <- function(row) {
  ri <<- ri + 1L
  results[[ri]] <<- row
}

min_n_for <- function(spec) {
  out <- suppressWarnings(as.integer(clean_value(spec$min_n)))
  if (is.na(out)) out <- plan$analyses$default_min_n
  out
}

common_inputs <- function(spec) {
  exposure <- clean_value(spec$exposure)
  method <- clean_value(spec$method)
  analysis_id <- clean_value(spec$analysis_id)
  covariates <- get_covariates(plan, clean_value(spec$covariates))
  if (is_blank_or_marker(exposure) || !exposure %in% names(metadata)) {
    add_status(analysis_id, "__analysis__", method, "missing_exposure", paste("Exposure not found:", exposure))
    return(NULL)
  }
  missing_covariates <- setdiff(covariates, names(metadata))
  if (length(missing_covariates)) {
    add_status(analysis_id, "__analysis__", method, "missing_covariates", paste(missing_covariates, collapse = ", "))
    return(NULL)
  }
  list(exposure = exposure, covariates = covariates, terms = c(exposure, covariates))
}


fit_clr_rank_tests <- function(spec) {
  inputs <- common_inputs(spec)
  if (is.null(inputs)) return(invisible(NULL))
  analysis_id <- clean_value(spec$analysis_id)
  exposure <- inputs$exposure
  method <- "clr_rank_tests"
  min_n <- min_n_for(spec)
  for (feature in colnames(clr)) {
    fit_data <- cbind(metadata, response = clr[, feature])
    complete <- stats::complete.cases(fit_data[, c("response", exposure), drop = FALSE])
    if (sum(complete) < min_n) {
      add_status(analysis_id, feature, method, "skipped", sprintf("n=%s below min_n=%s", sum(complete), min_n), sum(complete))
      next
    }
    dat <- fit_data[complete, c("response", exposure), drop = FALSE]
    dat[[exposure]] <- factor(dat[[exposure]])
    lv <- levels(dat[[exposure]])
    if (length(lv) < 2L) {
      add_status(analysis_id, feature, method, "skipped", "Exposure has fewer than two levels", nrow(dat))
      next
    }
    if (length(lv) == 2L) {
      fit <- try(stats::wilcox.test(dat$response ~ dat[[exposure]], exact = FALSE), silent = TRUE)
      if (inherits(fit, "try-error")) {
        add_status(analysis_id, feature, method, "failed", as.character(fit)[1], nrow(dat))
        next
      }
      means <- tapply(dat$response, dat[[exposure]], mean, na.rm = TRUE)
      effect <- unname(means[[lv[2]]] - means[[lv[1]]])
      term <- paste(lv, collapse = " vs ")
    } else {
      fit <- try(stats::kruskal.test(dat$response ~ dat[[exposure]]), silent = TRUE)
      if (inherits(fit, "try-error")) {
        add_status(analysis_id, feature, method, "failed", as.character(fit)[1], nrow(dat))
        next
      }
      effect <- NA_real_
      term <- exposure
    }
    add_result(data.frame(
      analysis_id = analysis_id,
      scenario = clean_value(spec$scenario),
      method = method,
      feature = feature,
      taxon = feature,
      term = term,
      effect = effect,
      std.error = NA_real_,
      statistic = unname(fit$statistic),
      p.value = fit$p.value,
      q.value = NA_real_,
      n = nrow(dat),
      status = "fitted",
      stringsAsFactors = FALSE
    ))
    add_status(analysis_id, feature, method, "fitted", "CLR rank test fitted", nrow(dat))
  }
}

fit_clr_lm <- function(spec) {
  inputs <- common_inputs(spec)
  if (is.null(inputs)) return(invisible(NULL))
  analysis_id <- clean_value(spec$analysis_id)
  min_n <- min_n_for(spec)
  exposure <- inputs$exposure
  covariates <- inputs$covariates
  terms <- inputs$terms
  for (feature in colnames(clr)) {
    fit_data <- cbind(metadata, response = clr[, feature])
    complete <- stats::complete.cases(fit_data[, c("response", terms), drop = FALSE])
    if (sum(complete) < min_n) {
      add_status(analysis_id, feature, "clr_lm", "skipped", sprintf("n=%s below min_n=%s", sum(complete), min_n), sum(complete))
      next
    }
    fit <- try(stats::lm(stats::reformulate(terms, response = "response"), data = fit_data[complete, , drop = FALSE]), silent = TRUE)
    if (inherits(fit, "try-error")) {
      add_status(analysis_id, feature, "clr_lm", "failed", as.character(fit)[1], sum(complete))
      next
    }
    coefs <- as.data.frame(summary(fit)$coefficients, stringsAsFactors = FALSE)
    coefs$term <- rownames(coefs)
    names(coefs)[1:4] <- c("effect", "std.error", "statistic", "p.value")
    term_rows <- coefs$term == exposure | startsWith(coefs$term, exposure)
    if (!any(term_rows)) term_rows <- grepl(exposure, coefs$term, fixed = TRUE)
    selected <- coefs[term_rows, , drop = FALSE]
    for (k in seq_len(nrow(selected))) {
      add_result(data.frame(
        analysis_id = analysis_id,
        scenario = clean_value(spec$scenario),
        method = "clr_lm",
        feature = feature,
        taxon = feature,
        term = selected$term[k],
        effect = selected$effect[k],
        std.error = selected$std.error[k],
        statistic = selected$statistic[k],
        p.value = selected$p.value[k],
        q.value = NA_real_,
        n = sum(complete),
        status = "fitted",
        stringsAsFactors = FALSE
      ))
    }
    add_status(analysis_id, feature, "clr_lm", "fitted", "CLR linear model fitted", sum(complete))
  }
}


fit_maaslin3 <- function(spec) {
  inputs <- common_inputs(spec)
  if (is.null(inputs)) return(invisible(NULL))
  analysis_id <- clean_value(spec$analysis_id)
  exposure <- inputs$exposure
  covariates <- inputs$covariates
  method <- "maaslin3"
  min_n <- min_n_for(spec)
  if (!requireNamespace("maaslin3", quietly = TRUE)) {
    add_status(analysis_id, "__analysis__", method, "unavailable", "Package maaslin3 is required for MaAsLin3 differential abundance")
    return(invisible(NULL))
  }
  complete <- stats::complete.cases(metadata[, c(exposure, covariates), drop = FALSE])
  if (sum(complete) < min_n) {
    add_status(analysis_id, "__analysis__", method, "skipped", sprintf("n=%s below min_n=%s", sum(complete), min_n), sum(complete))
    return(invisible(NULL))
  }
  fn <- tryCatch(getExportedValue("maaslin3", "maaslin3"), error = function(e) NULL)
  if (!is.function(fn)) {
    add_status(analysis_id, "__analysis__", method, "missing_function", "maaslin3::maaslin3 was not found")
    return(invisible(NULL))
  }
  arg_names <- names(formals(fn))
  if (!all(c("output", "formula") %in% arg_names)) {
    add_status(analysis_id, "__analysis__", method, "changed_signature", paste("maaslin3::maaslin3 arguments:", paste(arg_names, collapse = ",")))
    return(invisible(NULL))
  }
  subject_id <- clean_value(plan$identifiers$subject_id)
  visit <- clean_value(plan$identifiers$visit)
  formula_terms <- c(exposure, covariates)
  if (!is_blank_or_marker(subject_id) && !is_blank_or_marker(visit) && all(c(subject_id, visit) %in% names(metadata)) && !identical(exposure, visit)) {
    formula_text <- paste0("~ ", exposure, " * ", visit, " + (1 | ", subject_id, ")")
  } else {
    formula_text <- paste("~", paste(formula_terms, collapse = " + "))
  }
  output_dir <- file.path(plan$paths$results_dir, analysis_id)
  ensure_dir(output_dir)
  input_data <- mat[complete, , drop = FALSE]
  meta_cc <- metadata[complete, , drop = FALSE]
  rownames(meta_cc) <- rownames(input_data)
  call_args <- list(output = output_dir, formula = formula_text)
  defaults <- list(
    normalization = "TSS",
    transform = "LOG",
    augment = TRUE,
    standardize = TRUE,
    max_significance = 0.25,
    plot_associations = FALSE,
    median_comparison_abundance = TRUE,
    median_comparison_prevalence = FALSE,
    verbosity = "WARN"
  )
  for (nm in names(defaults)) if (nm %in% arg_names) call_args[[nm]] <- defaults[[nm]]
  if (all(c("input_data", "input_metadata") %in% arg_names)) {
    call_args$input_data <- input_data
    call_args$input_metadata <- meta_cc
  } else if ("data" %in% arg_names) {
    call_args$data <- input_data
    if ("metadata" %in% arg_names) call_args$metadata <- meta_cc
  } else if (length(arg_names) > 0L && !arg_names[[1]] %in% names(call_args)) {
    call_args[[arg_names[[1]]]] <- input_data
    if (length(arg_names) > 1L && grepl("meta", arg_names[[2]], ignore.case = TRUE)) call_args[[arg_names[[2]]]] <- meta_cc
  } else {
    add_status(analysis_id, "__analysis__", method, "changed_signature", paste("Cannot identify input-data argument from:", paste(arg_names, collapse = ",")))
    return(invisible(NULL))
  }
  fit <- try(do.call(fn, call_args), silent = TRUE)
  if (inherits(fit, "try-error")) {
    add_status(analysis_id, "__analysis__", method, "failed", as.character(fit)[1], sum(complete))
    return(invisible(NULL))
  }
  result_files <- file.path(output_dir, c("all_results.tsv", "all_results.tsv2"))
  result_files <- result_files[file.exists(result_files)]
  if (!length(result_files)) {
    add_status(analysis_id, "__analysis__", method, "fitted_no_table", "MaAsLin3 ran but all_results.tsv was not found", sum(complete))
    return(invisible(NULL))
  }
  tbl <- read_table_file(result_files[[1]])
  tbl <- tbl[!is.na(tbl[[1]]), , drop = FALSE]
  pick_col <- function(candidates) {
    hit <- intersect(candidates, names(tbl))
    if (length(hit)) hit[[1]] else NA_character_
  }
  feature_col <- pick_col(c("feature", "Feature", "EnzymeName"))
  term_col <- pick_col(c("value", "term", "metadata"))
  effect_col <- pick_col(c("coef", "coefficient", "effect"))
  p_col <- pick_col(c("pval", "p.value", "p_value", "qval_individual"))
  q_col <- pick_col(c("qval_joint", "qval_individual", "q.value", "q_value"))
  model_col <- pick_col(c("model"))
  for (k in seq_len(nrow(tbl))) {
    feature_value <- if (!is.na(feature_col)) as.character(tbl[[feature_col]][k]) else rownames(tbl)[k]
    add_result(data.frame(
      analysis_id = analysis_id,
      scenario = clean_value(spec$scenario),
      method = method,
      feature = feature_value,
      taxon = feature_value,
      term = if (!is.na(term_col)) as.character(tbl[[term_col]][k]) else exposure,
      effect = if (!is.na(effect_col)) suppressWarnings(as.numeric(tbl[[effect_col]][k])) else NA_real_,
      std.error = NA_real_,
      statistic = NA_real_,
      p.value = if (!is.na(p_col)) suppressWarnings(as.numeric(tbl[[p_col]][k])) else NA_real_,
      q.value = if (!is.na(q_col)) suppressWarnings(as.numeric(tbl[[q_col]][k])) else NA_real_,
      n = sum(complete),
      status = if (!is.na(model_col)) as.character(tbl[[model_col]][k]) else "fitted",
      stringsAsFactors = FALSE
    ))
  }
  add_status(analysis_id, "__analysis__", method, "fitted", paste("MaAsLin3 fitted with formula", formula_text), sum(complete))
}

fit_aldex2 <- function(spec) {
  inputs <- common_inputs(spec)
  if (is.null(inputs)) return(invisible(NULL))
  analysis_id <- clean_value(spec$analysis_id)
  exposure <- inputs$exposure
  covariates <- inputs$covariates
  method <- clean_value(spec$method)
  min_n <- min_n_for(spec)
  if (!requireNamespace("ALDEx2", quietly = TRUE)) {
    add_status(analysis_id, "__analysis__", method, "unavailable", "Package ALDEx2 is required for aldex2 differential abundance")
    return(invisible(NULL))
  }
  complete <- stats::complete.cases(metadata[, c(exposure, covariates), drop = FALSE])
  if (sum(complete) < min_n) {
    add_status(analysis_id, "__analysis__", method, "skipped", sprintf("n=%s below min_n=%s", sum(complete), min_n), sum(complete))
    return(invisible(NULL))
  }
  counts <- t(round(mat[complete, , drop = FALSE]))
  counts[counts < 0 | is.na(counts)] <- 0
  meta_cc <- metadata[complete, , drop = FALSE]
  conds <- as.factor(meta_cc[[exposure]])
  if (nlevels(conds) < 2) {
    add_status(analysis_id, "__analysis__", method, "skipped", "ALDEx2 requires at least two exposure levels")
    return(invisible(NULL))
  }
  res <- try({
    if (length(covariates) == 0 && nlevels(conds) == 2) {
      clr_obj <- ALDEx2::aldex.clr(counts, conds, mc.samples = 128, denom = "all", verbose = FALSE)
      tt <- ALDEx2::aldex.ttest(clr_obj)
      eff <- ALDEx2::aldex.effect(clr_obj)
      out <- data.frame(
        feature = rownames(tt),
        effect = if ("effect" %in% names(eff)) eff$effect else NA_real_,
        std.error = NA_real_,
        statistic = if ("we.ep" %in% names(tt)) tt$we.ep else NA_real_,
        p.value = if ("we.ep" %in% names(tt)) tt$we.ep else tt[[1]],
        stringsAsFactors = FALSE
      )
    } else {
      design <- stats::model.matrix(stats::reformulate(c(exposure, covariates)), data = meta_cc)
      clr_obj <- ALDEx2::aldex.clr(counts, design, mc.samples = 128, denom = "all", verbose = FALSE)
      glm_res <- ALDEx2::aldex.glm(clr_obj, design)
      p_col <- grep(paste0("^", exposure, ".*Pr"), names(glm_res), value = TRUE)[1]
      e_col <- grep(paste0("^", exposure), names(glm_res), value = TRUE)[1]
      out <- data.frame(
        feature = rownames(glm_res),
        effect = if (!is.na(e_col)) glm_res[[e_col]] else NA_real_,
        std.error = NA_real_,
        statistic = NA_real_,
        p.value = if (!is.na(p_col)) glm_res[[p_col]] else NA_real_,
        stringsAsFactors = FALSE
      )
    }
    out
  }, silent = TRUE)
  if (inherits(res, "try-error")) {
    add_status(analysis_id, "__analysis__", method, "failed", as.character(res)[1], sum(complete))
    return(invisible(NULL))
  }
  res$q.value <- stats::p.adjust(res$p.value, method = plan$analyses$fdr_method)
  for (k in seq_len(nrow(res))) {
    add_result(data.frame(
      analysis_id = analysis_id,
      scenario = clean_value(spec$scenario),
      method = method,
      feature = res$feature[k],
      taxon = res$feature[k],
      term = exposure,
      effect = res$effect[k],
      std.error = res$std.error[k],
      statistic = res$statistic[k],
      p.value = res$p.value[k],
      q.value = res$q.value[k],
      n = sum(complete),
      status = "fitted",
      stringsAsFactors = FALSE
    ))
  }
  add_status(analysis_id, "__analysis__", method, "fitted", "ALDEx2 branch fitted", sum(complete))
}

fit_ancombc <- function(spec) {
  inputs <- common_inputs(spec)
  if (is.null(inputs)) return(invisible(NULL))
  analysis_id <- clean_value(spec$analysis_id)
  exposure <- inputs$exposure
  covariates <- inputs$covariates
  method <- clean_value(spec$method)
  min_n <- min_n_for(spec)
  if (!requireNamespace("ANCOMBC", quietly = TRUE) || !requireNamespace("phyloseq", quietly = TRUE)) {
    add_status(analysis_id, "__analysis__", method, "unavailable", "Packages ANCOMBC and phyloseq are required for ANCOM-BC")
    return(invisible(NULL))
  }
  complete <- stats::complete.cases(metadata[, c(exposure, covariates), drop = FALSE])
  if (sum(complete) < min_n) {
    add_status(analysis_id, "__analysis__", method, "skipped", sprintf("n=%s below min_n=%s", sum(complete), min_n), sum(complete))
    return(invisible(NULL))
  }
  counts <- t(round(mat[complete, , drop = FALSE]))
  counts[counts < 0 | is.na(counts)] <- 0
  meta_cc <- metadata[complete, , drop = FALSE]
  rownames(meta_cc) <- rownames(mat)[complete]
  colnames(counts) <- rownames(meta_cc)
  res <- try({
    phy <- phyloseq::phyloseq(
      phyloseq::otu_table(counts, taxa_are_rows = TRUE),
      phyloseq::sample_data(meta_cc)
    )
    formula_rhs <- paste(c(exposure, covariates), collapse = " + ")
    out_obj <- ANCOMBC::ancombc2(
      data = phy,
      assay_name = "counts",
      tax_level = NULL,
      fix_formula = formula_rhs,
      p_adj_method = plan$analyses$fdr_method,
      prv_cut = 0,
      lib_cut = 0,
      group = exposure,
      struc_zero = FALSE,
      neg_lb = FALSE,
      global = FALSE
    )
    out_obj
  }, silent = TRUE)
  if (inherits(res, "try-error")) {
    add_status(analysis_id, "__analysis__", method, "failed", as.character(res)[1], sum(complete))
    return(invisible(NULL))
  }
  tbl <- NULL
  if (is.list(res) && "res" %in% names(res)) tbl <- res$res
  if (is.null(tbl) || !is.data.frame(tbl)) {
    add_status(analysis_id, "__analysis__", method, "failed", "Could not locate ANCOM-BC result table", sum(complete))
    return(invisible(NULL))
  }
  feature_col <- intersect(c("taxon", "feature", "otu", "lfc"), names(tbl))[1]
  if (is.na(feature_col) || feature_col == "lfc") tbl$feature <- rownames(tbl) else tbl$feature <- tbl[[feature_col]]
  p_col <- grep(paste0("^p_", exposure, "|^p_val.*", exposure, "|p_val"), names(tbl), value = TRUE)[1]
  q_col <- grep(paste0("^q_", exposure, "|^q_val.*", exposure, "|q_val"), names(tbl), value = TRUE)[1]
  lfc_col <- grep(paste0("^lfc_", exposure, "|^beta.*", exposure, "|lfc"), names(tbl), value = TRUE)[1]
  for (k in seq_len(nrow(tbl))) {
    add_result(data.frame(
      analysis_id = analysis_id,
      scenario = clean_value(spec$scenario),
      method = method,
      feature = tbl$feature[k],
      taxon = tbl$feature[k],
      term = exposure,
      effect = if (!is.na(lfc_col)) tbl[[lfc_col]][k] else NA_real_,
      std.error = NA_real_,
      statistic = NA_real_,
      p.value = if (!is.na(p_col)) tbl[[p_col]][k] else NA_real_,
      q.value = if (!is.na(q_col)) tbl[[q_col]][k] else NA_real_,
      n = sum(complete),
      status = "fitted",
      stringsAsFactors = FALSE
    ))
  }
  add_status(analysis_id, "__analysis__", method, "fitted", "ANCOM-BC branch fitted", sum(complete))
}

for (i in seq_len(nrow(da_specs))) {
  spec <- da_specs[i, , drop = FALSE]
  method <- tolower(clean_value(spec$method))
  if (method %in% c("clr_rank_tests", "clr_rank", "clr_wilcox", "clr_kruskal")) {
    fit_clr_rank_tests(spec)
  } else if (method == "clr_lm") {
    fit_clr_lm(spec)
  } else if (method %in% c("maaslin3", "maaslin")) {
    fit_maaslin3(spec)
  } else if (method %in% c("aldex2", "aldex")) {
    fit_aldex2(spec)
  } else if (method %in% c("ancombc", "ancombc2", "ancom-bc", "ancom-bc2")) {
    fit_ancombc(spec)
  } else {
    add_status(clean_value(spec$analysis_id), "__analysis__", method, "unavailable", paste("No method branch defined for", method))
  }
}

result_table <- if (length(results)) do.call(rbind, results) else data.frame()
if (nrow(result_table)) {
  missing_q <- is.na(result_table$q.value) & !is.na(result_table$p.value)
  if (any(missing_q)) {
    result_table$q.value[missing_q] <- ave(result_table$p.value, result_table$analysis_id, FUN = function(p) stats::p.adjust(p, method = plan$analyses$fdr_method))[missing_q]
  }
}
model_status <- if (length(status)) do.call(rbind, status) else data.frame()

result_object <- list(
  plan = plan,
  model_specification = model_spec,
  sample_summary = obj$sample_summary,
  feature_summary = obj$feature_summary,
  differential_abundance = result_table,
  model_status = model_status,
  method_contract = list(
    clr_rank_tests = "Centered log-ratio transform with Wilcoxon/Kruskal feature tests",
    clr_lm = "Centered log-ratio transform with feature-wise linear model when explicitly configured",
    maaslin3 = "MaAsLin3 TSS/LOG model branch when package and function signature checks pass",
    aldex2 = "Optional ALDEx2 sensitivity branch only when explicitly configured",
    ancombc2 = "Optional ANCOM-BC2 sensitivity branch only when explicitly configured"
  )
)
ensure_dir(plan$paths$results_dir)
saveRDS(result_object, file.path(plan$paths$results_dir, "microbiome_differential_abundance_results.rds"))
write_tsv(result_table, file.path(plan$paths$results_dir, "differential_abundance_results.tsv"))
write_tsv(model_status, file.path(plan$paths$results_dir, "differential_abundance_model_status.tsv"))
append_decision("differential_abundance_methods", paste(unique(da_specs$method), collapse = ","), "Configured by model_specification.csv", "code/03_differential_abundance.R")
message("Wrote microbiome differential abundance outputs")
