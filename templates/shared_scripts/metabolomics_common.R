suppressPackageStartupMessages({
  library(haven)
  library(readxl)
  library(dplyr)
  library(tidyr)
  library(stringr)
  library(purrr)
  library(broom)
  library(broom.mixed)
  library(lmerTest)
})

extract_visit_num <- function(x) {
  suppressWarnings(as.integer(gsub("[^0-9]", "", as.character(x))))
}

prepare_metabolite_data <- function(path, sheet = "SPSS") {
  m <- read_excel(path, sheet = sheet)

  required_cols <- c("StudyID", "Study visit no")
  if (!all(required_cols %in% names(m))) {
    stop("Metabolite sheet missing required columns: ", paste(setdiff(required_cols, names(m)), collapse = ", "))
  }

  m <- m %>%
    mutate(
      StudyID = trimws(as.character(StudyID)),
      visit_num = extract_visit_num(`Study visit no`)
    )

  id_cols <- c("StudyID", "visit_num")
  drop_cols <- c("NoOrder", "Study visit no")
  metabolite_cols <- setdiff(names(m), c(id_cols, drop_cols))

  m[metabolite_cols] <- lapply(m[metabolite_cols], function(x) suppressWarnings(as.numeric(x)))

  dup_rows <- m %>%
    count(StudyID, visit_num, name = "n") %>%
    filter(n > 1)

  duplicate_count <- if (nrow(dup_rows) == 0) 0L else sum(dup_rows$n - 1L)

  if (nrow(dup_rows) > 0) {
    m <- m %>%
      group_by(StudyID, visit_num) %>%
      summarise(across(all_of(metabolite_cols), ~ {
        x <- .x
        if (all(is.na(x))) NA_real_ else mean(x, na.rm = TRUE)
      }), .groups = "drop")
  } else {
    m <- m %>% select(all_of(c(id_cols, metabolite_cols)))
  }

  list(
    data = m,
    metabolite_cols = metabolite_cols,
    duplicate_rows_collapsed = duplicate_count
  )
}

log_z_by_visit <- function(df, metabolite_cols, visit_col) {
  if (missing(visit_col)) {
    stop("log_z_by_visit: visit_col must be supplied explicitly.")
  }
  visit_col <- as.character(visit_col)[1]
  if (is.na(visit_col) || !nzchar(visit_col)) {
    stop("log_z_by_visit: visit_col must name an explicit visit column.")
  }
  if (!visit_col %in% names(df)) {
    stop(
      "log_z_by_visit: visit column '", visit_col,
      "' not found in data. Columns present: ",
      paste(names(df), collapse = ", ")
    )
  }

  metabolite_cols <- unique(as.character(metabolite_cols))
  missing_metabolites <- setdiff(metabolite_cols, names(df))
  if (length(missing_metabolites) > 0) {
    stop(
      "log_z_by_visit: missing metabolite column(s): ",
      paste(utils::head(missing_metabolites, 10), collapse = ", "),
      if (length(missing_metabolites) > 10) " ..." else ""
    )
  }

  out <- df
  visits <- sort(unique(out[[visit_col]]))
  visits <- visits[!is.na(visits)]
  if (length(visits) == 0) {
    stop("log_z_by_visit: visit column '", visit_col, "' has no non-missing values.")
  }

  for (v in visits) {
    idx <- which(out[[visit_col]] == v)

    for (nm in metabolite_cols) {
      x <- as.numeric(out[[nm]][idx])
      if (all(is.na(x))) {
        out[[nm]][idx] <- NA_real_
        next
      }

      pos <- x[is.finite(x) & x > 0]
      if (length(pos) > 0) {
        pseudo <- min(pos, na.rm = TRUE) / 2
        if (any(x <= 0, na.rm = TRUE)) {
          x <- log10(x + pseudo)
        } else {
          x <- log10(x)
        }
      }

      z <- suppressWarnings(as.numeric(scale(x)))
      out[[nm]][idx] <- z
    }
  }

  out
}

build_cluster_scores <- function(df, metabolite_cols) {
  cluster_regex <- c(
    inflammation = "GlycA|Glycoprotein",
    amino_acids = "(^|[-.])(Ala|Gln|Gly|His|Ile|Leu|Val|Phe|Tyr)([-.]|$)|BCAA",
    glycolysis_energy = "Glucose|Lactate|Pyruvate|Citrate|Acetate|Acetoacetate|Hydroxybutyrate|Creatinine|Albumin",
    fatty_acids = "Omega|Total-FA|Unsaturation|DHA|EPA|PUFA|MUFA|SFA|FAn3|FAn6|FA-ratio",
    lipoproteins = "VLDL|LDL|HDL|Apo|Remnant|non-HDL|Total-C|Total-TG|Total-PL|Total-CE|Total-FC|Total-L|Total-P"
  )

  feature_map <- data.frame(
    metabolite = metabolite_cols,
    cluster = NA_character_,
    stringsAsFactors = FALSE
  )

  for (cl in names(cluster_regex)) {
    idx <- which(is.na(feature_map$cluster) & grepl(cluster_regex[[cl]], feature_map$metabolite, ignore.case = TRUE))
    if (length(idx) > 0) {
      feature_map$cluster[idx] <- cl
    }
  }

  cluster_members <- split(feature_map$metabolite[!is.na(feature_map$cluster)], feature_map$cluster[!is.na(feature_map$cluster)])

  out <- df
  cluster_cols <- character(0)

  for (cl in names(cluster_members)) {
    members <- cluster_members[[cl]]
    col_nm <- paste0("cluster_", cl)

    mat <- as.matrix(out[, members, drop = FALSE])
    valid_n <- rowSums(!is.na(mat))
    score <- rowMeans(mat, na.rm = TRUE)
    score[valid_n < 2] <- NA_real_

    out[[col_nm]] <- score
    cluster_cols <- c(cluster_cols, col_nm)
  }

  list(
    data = out,
    cluster_cols = cluster_cols,
    cluster_map = feature_map
  )
}

cast_factor_cols <- function(df, factor_cols) {
  for (nm in intersect(factor_cols, names(df))) {
    df[[nm]] <- as.factor(df[[nm]])
  }
  df
}

standardize_numeric <- function(x) {
  x <- as.numeric(x)
  z <- suppressWarnings(as.numeric(scale(x)))
  z
}

parallel_apply <- function(X, FUN, n_cores = 1L) {
  n_cores <- max(1L, as.integer(n_cores))
  if (.Platform$OS.type != "windows" && n_cores > 1L) {
    parallel::mclapply(X, FUN, mc.cores = n_cores)
  } else {
    lapply(X, FUN)
  }
}

fit_lm_one <- function(df, outcome, exposure, covars, factor_cols = character(0), min_n = 30L) {
  vars <- unique(c(outcome, exposure, covars))
  if (!all(vars %in% names(df))) {
    return(tibble(
      outcome = outcome,
      term = exposure,
      estimate = NA_real_,
      std.error = NA_real_,
      statistic = NA_real_,
      p.value = NA_real_,
      n = NA_integer_,
      note = "missing_variables"
    ))
  }

  d <- df[, vars, drop = FALSE]
  d <- d[complete.cases(d), , drop = FALSE]

  if (nrow(d) < min_n) {
    return(tibble(
      outcome = outcome,
      term = exposure,
      estimate = NA_real_,
      std.error = NA_real_,
      statistic = NA_real_,
      p.value = NA_real_,
      n = nrow(d),
      note = "too_few_complete_cases"
    ))
  }

  if (length(unique(d[[outcome]])) < 5) {
    return(tibble(
      outcome = outcome,
      term = exposure,
      estimate = NA_real_,
      std.error = NA_real_,
      statistic = NA_real_,
      p.value = NA_real_,
      n = nrow(d),
      note = "constant_outcome"
    ))
  }

  if (length(unique(d[[exposure]])) < 3) {
    return(tibble(
      outcome = outcome,
      term = exposure,
      estimate = NA_real_,
      std.error = NA_real_,
      statistic = NA_real_,
      p.value = NA_real_,
      n = nrow(d),
      note = "constant_exposure"
    ))
  }

  d <- cast_factor_cols(d, factor_cols)
  d$y <- as.numeric(d[[outcome]])
  d$x <- standardize_numeric(d[[exposure]])

  if (all(is.na(d$x)) || sd(d$x, na.rm = TRUE) == 0) {
    return(tibble(
      outcome = outcome,
      term = exposure,
      estimate = NA_real_,
      std.error = NA_real_,
      statistic = NA_real_,
      p.value = NA_real_,
      n = nrow(d),
      note = "invalid_exposure_scale"
    ))
  }

  if (sd(d$y, na.rm = TRUE) == 0) {
    return(tibble(
      outcome = outcome,
      term = exposure,
      estimate = NA_real_,
      std.error = NA_real_,
      statistic = NA_real_,
      p.value = NA_real_,
      n = nrow(d),
      note = "invalid_outcome_scale"
    ))
  }

  form <- reformulate(termlabels = c("x", covars), response = "y")
  fit <- tryCatch(lm(form, data = d), error = function(e) e)

  if (inherits(fit, "error")) {
    return(tibble(
      outcome = outcome,
      term = exposure,
      estimate = NA_real_,
      std.error = NA_real_,
      statistic = NA_real_,
      p.value = NA_real_,
      n = nrow(d),
      note = "lm_failed"
    ))
  }

  td <- broom::tidy(fit)
  row <- td %>% filter(term == "x")

  if (nrow(row) == 0) {
    return(tibble(
      outcome = outcome,
      term = exposure,
      estimate = NA_real_,
      std.error = NA_real_,
      statistic = NA_real_,
      p.value = NA_real_,
      n = nrow(d),
      note = "term_not_estimable"
    ))
  }

  tibble(
    outcome = outcome,
    term = exposure,
    estimate = row$estimate[1],
    std.error = row$std.error[1],
    statistic = row$statistic[1],
    p.value = row$p.value[1],
    n = nrow(d),
    note = "ok"
  )
}

run_cross_sectional <- function(df, visit_col, visits, outcomes, exposures, covars, factor_cols = character(0), min_n = 30L, n_cores = 1L) {
  res <- list()

  for (v in visits) {
    dv <- df %>% filter(.data[[visit_col]] == v)

    for (exp_nm in exposures) {
      tmp <- bind_rows(parallel_apply(outcomes, function(out_nm) {
        fit_lm_one(
          df = dv,
          outcome = out_nm,
          exposure = exp_nm,
          covars = covars,
          factor_cols = factor_cols,
          min_n = min_n
        )
      }, n_cores = n_cores))

      tmp <- tmp %>%
        mutate(
          visit = as.character(v),
          exposure = exp_nm,
          model = "age_specific"
        )

      ok <- tmp$note == "ok" & !is.na(tmp$p.value)
      tmp$q.value <- NA_real_
      if (any(ok)) {
        tmp$q.value[ok] <- p.adjust(tmp$p.value[ok], method = "BH")
      }

      res[[paste0("v", v, "_", exp_nm)]] <- tmp
    }
  }

  bind_rows(res)
}

fit_mixed_one <- function(df, outcome, exposure, covars, id_col = "StudyID", visit_col = "visit_factor", factor_cols = character(0), min_n = 60L, min_ids = 25L) {
  vars <- unique(c(outcome, exposure, covars, id_col, visit_col))
  if (!all(vars %in% names(df))) {
    return(tibble(
      outcome = outcome,
      term = NA_character_,
      estimate = NA_real_,
      std.error = NA_real_,
      statistic = NA_real_,
      p.value = NA_real_,
      n = NA_integer_,
      n_ids = NA_integer_,
      note = "missing_variables"
    ))
  }

  d <- df[, vars, drop = FALSE]
  d <- d[complete.cases(d), , drop = FALSE]

  n_ids <- dplyr::n_distinct(d[[id_col]])
  if (nrow(d) < min_n || n_ids < min_ids) {
    return(tibble(
      outcome = outcome,
      term = NA_character_,
      estimate = NA_real_,
      std.error = NA_real_,
      statistic = NA_real_,
      p.value = NA_real_,
      n = nrow(d),
      n_ids = n_ids,
      note = "too_few_complete_cases"
    ))
  }

  if (length(unique(d[[outcome]])) < 5 || length(unique(d[[exposure]])) < 3) {
    return(tibble(
      outcome = outcome,
      term = NA_character_,
      estimate = NA_real_,
      std.error = NA_real_,
      statistic = NA_real_,
      p.value = NA_real_,
      n = nrow(d),
      n_ids = n_ids,
      note = "constant_outcome_or_exposure"
    ))
  }

  d <- cast_factor_cols(d, unique(c(factor_cols, id_col, visit_col)))
  d[[visit_col]] <- factor(d[[visit_col]], levels = sort(unique(d[[visit_col]])))
  d$y <- as.numeric(d[[outcome]])
  d$x <- standardize_numeric(d[[exposure]])

  if (all(is.na(d$x)) || sd(d$x, na.rm = TRUE) == 0 || sd(d$y, na.rm = TRUE) == 0) {
    return(tibble(
      outcome = outcome,
      term = NA_character_,
      estimate = NA_real_,
      std.error = NA_real_,
      statistic = NA_real_,
      p.value = NA_real_,
      n = nrow(d),
      n_ids = n_ids,
      note = "invalid_scaled_data"
    ))
  }

  rhs <- c(
    paste0("x * `", visit_col, "`"),
    if (length(covars) > 0) paste0("`", covars, "`")
  )
  rhs <- rhs[!is.na(rhs)]

  fml <- as.formula(paste0(
    "y ~ ",
    paste(rhs, collapse = " + "),
    " + (1|`", id_col, "`)"
  ))

  fit <- tryCatch(
    lmerTest::lmer(fml, data = d, REML = FALSE),
    error = function(e) e
  )

  if (inherits(fit, "error")) {
    return(tibble(
      outcome = outcome,
      term = NA_character_,
      estimate = NA_real_,
      std.error = NA_real_,
      statistic = NA_real_,
      p.value = NA_real_,
      n = nrow(d),
      n_ids = n_ids,
      note = "mixed_model_failed"
    ))
  }

  td <- broom.mixed::tidy(fit, effects = "fixed")
  rows <- td %>%
    filter(term == "x" | grepl("^x:`?", term)) %>%
    mutate(
      outcome = outcome,
      n = nrow(d),
      n_ids = n_ids,
      note = "ok"
    ) %>%
    select(outcome, term, estimate, std.error, statistic, p.value, n, n_ids, note)

  if (nrow(rows) == 0) {
    return(tibble(
      outcome = outcome,
      term = NA_character_,
      estimate = NA_real_,
      std.error = NA_real_,
      statistic = NA_real_,
      p.value = NA_real_,
      n = nrow(d),
      n_ids = n_ids,
      note = "term_not_estimable"
    ))
  }

  rows
}

run_mixed_models <- function(df, outcomes, exposures, covars, id_col = "StudyID", visit_col = "visit_factor", factor_cols = character(0), min_n = 60L, min_ids = 25L, n_cores = max(1L, parallel::detectCores() - 1L)) {
  res <- list()

  for (exp_nm in exposures) {
    tmp <- bind_rows(parallel_apply(outcomes, function(out_nm) {
      fit_mixed_one(
        df = df,
        outcome = out_nm,
        exposure = exp_nm,
        covars = covars,
        id_col = id_col,
        visit_col = visit_col,
        factor_cols = factor_cols,
        min_n = min_n,
        min_ids = min_ids
      )
    }, n_cores = n_cores)) %>%
      mutate(exposure = exp_nm, model = "mixed")

    ok <- tmp$note == "ok" & !is.na(tmp$p.value)
    tmp$q.value <- NA_real_
    if (any(ok)) {
      tmp$q.value[ok] <- p.adjust(tmp$p.value[ok], method = "BH")
    }

    res[[exp_nm]] <- tmp
  }

  bind_rows(res)
}

fit_conditional_one <- function(df, outcome7, outcome6, exposure6, covars, factor_cols = character(0), min_n = 40L) {
  vars <- unique(c(outcome7, outcome6, exposure6, covars))
  if (!all(vars %in% names(df))) {
    return(tibble(
      outcome = outcome7,
      term = exposure6,
      estimate = NA_real_,
      std.error = NA_real_,
      statistic = NA_real_,
      p.value = NA_real_,
      n = NA_integer_,
      note = "missing_variables"
    ))
  }

  d <- df[, vars, drop = FALSE]
  d <- d[complete.cases(d), , drop = FALSE]

  if (nrow(d) < min_n) {
    return(tibble(
      outcome = outcome7,
      term = exposure6,
      estimate = NA_real_,
      std.error = NA_real_,
      statistic = NA_real_,
      p.value = NA_real_,
      n = nrow(d),
      note = "too_few_complete_cases"
    ))
  }

  d <- cast_factor_cols(d, factor_cols)
  d$y <- as.numeric(d[[outcome7]])
  d$y0 <- as.numeric(d[[outcome6]])
  d$x <- standardize_numeric(d[[exposure6]])

  if (sd(d$y, na.rm = TRUE) == 0 || sd(d$y0, na.rm = TRUE) == 0 || sd(d$x, na.rm = TRUE) == 0) {
    return(tibble(
      outcome = outcome7,
      term = exposure6,
      estimate = NA_real_,
      std.error = NA_real_,
      statistic = NA_real_,
      p.value = NA_real_,
      n = nrow(d),
      note = "invalid_scaled_data"
    ))
  }

  form <- reformulate(termlabels = c("x", "y0", covars), response = "y")
  fit <- tryCatch(lm(form, data = d), error = function(e) e)

  if (inherits(fit, "error")) {
    return(tibble(
      outcome = outcome7,
      term = exposure6,
      estimate = NA_real_,
      std.error = NA_real_,
      statistic = NA_real_,
      p.value = NA_real_,
      n = nrow(d),
      note = "conditional_model_failed"
    ))
  }

  td <- broom::tidy(fit)
  row <- td %>% filter(term == "x")
  if (nrow(row) == 0) {
    return(tibble(
      outcome = outcome7,
      term = exposure6,
      estimate = NA_real_,
      std.error = NA_real_,
      statistic = NA_real_,
      p.value = NA_real_,
      n = nrow(d),
      note = "term_not_estimable"
    ))
  }

  tibble(
    outcome = outcome7,
    term = exposure6,
    estimate = row$estimate[1],
    std.error = row$std.error[1],
    statistic = row$statistic[1],
    p.value = row$p.value[1],
    n = nrow(d),
    note = "ok"
  )
}

run_conditional_models <- function(df, outcomes_visit7, outcomes_visit6, exposures6, covars, factor_cols = character(0), min_n = 40L, n_cores = 1L) {
  stopifnot(length(outcomes_visit7) == length(outcomes_visit6))

  res <- list()

  for (exp6 in exposures6) {
    tmp <- bind_rows(parallel_apply(seq_along(outcomes_visit7), function(i) {
      fit_conditional_one(
        df = df,
        outcome7 = outcomes_visit7[i],
        outcome6 = outcomes_visit6[i],
        exposure6 = exp6,
        covars = covars,
        factor_cols = factor_cols,
        min_n = min_n
      )
    }, n_cores = n_cores)) %>%
      mutate(exposure = exp6, model = "conditional")

    ok <- tmp$note == "ok" & !is.na(tmp$p.value)
    tmp$q.value <- NA_real_
    if (any(ok)) {
      tmp$q.value[ok] <- p.adjust(tmp$p.value[ok], method = "BH")
    }

    res[[exp6]] <- tmp
  }

  bind_rows(res)
}

summarise_signals <- function(df, outcome_family = c("metabolite", "cluster")) {
  outcome_family <- match.arg(outcome_family)
  if (!("visit" %in% names(df))) {
    df$visit <- NA_character_
  }

  df %>%
    mutate(is_sig = !is.na(q.value) & q.value <= 0.05) %>%
    group_by(model, exposure, visit = ifelse(is.na(visit), "all", as.character(visit))) %>%
    summarise(
      tested = sum(note == "ok"),
      sig_q05 = sum(is_sig, na.rm = TRUE),
      min_q = suppressWarnings(min(q.value[note == "ok"], na.rm = TRUE)),
      .groups = "drop"
    ) %>%
    mutate(
      outcome_family = outcome_family,
      min_q = ifelse(is.infinite(min_q), NA_real_, min_q)
    )
}

make_effect_plot <- function(df, top_n = 20L, title = "Top Effects") {
  library(ggplot2)

  dd <- df %>%
    filter(note == "ok", !is.na(q.value), !is.na(estimate)) %>%
    arrange(q.value, desc(abs(estimate))) %>%
    slice_head(n = top_n) %>%
    mutate(outcome = factor(outcome, levels = rev(unique(outcome))))

  if (nrow(dd) == 0) {
    return(
      ggplot() +
        annotate("text", x = 0, y = 0, label = "No q<=0.05 associations") +
        theme_void() +
        ggtitle(title)
    )
  }

  ggplot(dd, aes(x = estimate, y = outcome, color = q.value)) +
    geom_point(size = 2) +
    geom_vline(xintercept = 0, linetype = 2, color = "grey60") +
    scale_color_viridis_c(option = "magma", direction = -1) +
    theme_minimal(base_size = 11) +
    labs(x = "Standardized beta", y = "Outcome", color = "q-value", title = title)
}
