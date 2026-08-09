# Shared reporting helpers for the Prenatal diet Quarto site.

suppressPackageStartupMessages(library(dplyr))

COMPONENT_LABELS <- list(
  q1_early = "Q1: Early-Pregnancy Age-Specific Models",
  q2_late = "Q2: Late-Pregnancy Age-Specific Models",
  q3_mutual_early = "Q3: Mutual Timing: Early-Pregnancy Term",
  q3_mutual_late = "Q3: Mutual Timing: Late-Pregnancy Term",
  q4_longitudinal = "Q4: Longitudinal Interaction Model",
  q7_early = "Q4 longitudinal | Early",
  q7_late = "Q4 longitudinal | Late"
)

visit_label <- function(v) dplyr::case_when(
  as.integer(v) == 4L ~ "6 Months",
  as.integer(v) == 5L ~ "12 Months",
  as.integer(v) == 6L ~ "24 Months",
  as.integer(v) == 7L ~ "5-6 Years",
  TRUE ~ paste("Visit", v)
)

pretty_var <- function(x) {
  x <- sub("^M", "", x)
  x <- gsub("DietaryPatterns", "Dietary Patterns ", x)
  x <- gsub("DIINormalDiet", "Dietary Inflammatory Index ", x)
  x <- gsub("DIIDensityDiet", "Dietary Inflammatory Index Density ", x)
  x <- gsub("^IDQ1$", "IDQ", x)
  x <- gsub("^IDQ2$", "IDQ", x)
  x <- gsub("EPercent", "Percent ", x)
  x <- gsub("Fiball", "Fibre ", x)
  x <- gsub("CHO", "Carbohydrate", x)
  x <- gsub("Pro", "Protein", x)
  x <- gsub("FAn3", "n-3 fatty acids", x)
  x <- gsub("FAn6", "n-6 fatty acids", x)
  x <- sub("[12]$", "", x)
  x <- gsub("_", " ", x, fixed = TRUE)
  x <- gsub("\\s+", " ", trimws(x))
  tools::toTitleCase(tolower(x))
}

sample_n_label <- function(df, n_col = "n", na_label = "not available") {
  if (is.null(df) || nrow(df) == 0 || !(n_col %in% names(df))) {
    return(na_label)
  }
  vals <- suppressWarnings(as.numeric(df[[n_col]]))
  vals <- vals[is.finite(vals)]
  if (length(vals) == 0) return(na_label)
  if (length(vals) == 1) return(as.character(vals[[1]]))
  paste0(min(vals), "-", max(vals))
}

flatten_age_specific <- function(age_res, timing_label) {
  dplyr::bind_rows(lapply(names(age_res), function(v) {
    df <- age_res[[v]][[timing_label]]
    if (is.null(df) || nrow(df) == 0) return(NULL)
    df %>% dplyr::mutate(visit = as.integer(v))
  }))
}

flatten_mutual <- function(age_res, term = c("early", "late")) {
  term <- match.arg(term)
  dplyr::bind_rows(lapply(names(age_res), function(v) {
    dat <- age_res[[v]]$mutual
    if (is.null(dat) || nrow(dat) == 0) return(NULL)
    dat <- dat %>% dplyr::mutate(visit = as.integer(v))
    if (term == "early") {
      dat %>%
        dplyr::transmute(
          feature, visit, n,
          estimate = estimate_early,
          conf.low = conf.low_early,
          conf.high = conf.high_early,
          p.value = p_early,
          q.value = q_early,
          exposure_pair, analysis
        )
    } else {
      dat %>%
        dplyr::transmute(
          feature, visit, n,
          estimate = estimate_late,
          conf.low = conf.low_late,
          conf.high = conf.high_late,
          p.value = p_late,
          q.value = q_late,
          exposure_pair, analysis
        )
    }
  }))
}

flatten_age <- function(age_res, type = "early") {
  lapply(names(age_res), function(v) {
    df <- age_res[[v]][[type]]
    if (is.null(df)) return(NULL)
    df$visit_age <- v
    df
  }) %>% dplyr::bind_rows()
}

flatten_mutual_term <- function(df, term = c("early", "late")) {
  term <- match.arg(term)
  if (is.null(df) || nrow(df) == 0) return(data.frame())
  if (term == "early") {
    df %>%
      dplyr::transmute(
        exposure_pair,
        feature,
        visit,
        n,
        estimate = estimate_early,
        q.value = q_early
      )
  } else {
    df %>%
      dplyr::transmute(
        exposure_pair,
        feature,
        visit,
        n,
        estimate = estimate_late,
        q.value = q_late
      )
  }
}

split_longitudinal_terms <- function(long_res) {
  if (is.null(long_res) || nrow(long_res) == 0 || !("term" %in% names(long_res))) {
    return(list(main = long_res, interaction = long_res[0, , drop = FALSE]))
  }
  list(
    main = long_res %>% dplyr::filter(!grepl(":", term)),
    interaction = long_res %>% dplyr::filter(grepl(":", term))
  )
}

prepare_prenatal_diet_result_views <- function(results) {
  age_res <- results$age_specific
  long_res <- results$longitudinal
  list(
    early_all = flatten_age_specific(age_res, "early"),
    late_all = flatten_age_specific(age_res, "late"),
    mutual_early_all = flatten_mutual(age_res, "early"),
    mutual_late_all = flatten_mutual(age_res, "late"),
    early_res = flatten_age(age_res, "early"),
    late_res = flatten_age(age_res, "late"),
    mutual_res = flatten_age(age_res, "mutual"),
    long_res = long_res
  )
}

build_result_inventory_prenatal_diet <- function(results) {
  views <- prepare_prenatal_diet_result_views(results)
  tibble::tibble(
    component = c(
      COMPONENT_LABELS$q1_early,
      COMPONENT_LABELS$q2_late,
      COMPONENT_LABELS$q3_mutual_early,
      COMPONENT_LABELS$q3_mutual_late,
      COMPONENT_LABELS$q4_longitudinal
    ),
    result_rows = c(
      nrow(views$early_all),
      nrow(views$late_all),
      nrow(views$mutual_early_all),
      nrow(views$mutual_late_all),
      nrow(views$long_res)
    ),
    significant_rows_q_lt_0_10 = c(
      sum(views$early_all$q.value < 0.10, na.rm = TRUE),
      sum(views$late_all$q.value < 0.10, na.rm = TRUE),
      sum(views$mutual_early_all$q.value < 0.10, na.rm = TRUE),
      sum(views$mutual_late_all$q.value < 0.10, na.rm = TRUE),
      if ("q.value" %in% names(views$long_res)) {
        sum(views$long_res$q.value < 0.10, na.rm = TRUE)
      } else {
        0L
      }
    ),
    complete_case_n_range = c(
      sample_n_label(views$early_all, na_label = NA_character_),
      sample_n_label(views$late_all, na_label = NA_character_),
      sample_n_label(views$mutual_early_all, na_label = NA_character_),
      sample_n_label(views$mutual_late_all, na_label = NA_character_),
      sample_n_label(views$long_res, n_col = "n_obs", na_label = NA_character_)
    )
  )
}

build_signal_summary_prenatal_diet <- function(results) {
  views <- prepare_prenatal_diet_result_views(results)
  long_res <- views$long_res

  age_specific_summary <- dplyr::bind_rows(
    tibble::tibble(
      component = COMPONENT_LABELS$q1_early,
      result_rows = nrow(views$early_res),
      significant_rows = sum(views$early_res$q.value < 0.1, na.rm = TRUE),
      n_complete = sample_n_label(views$early_res)
    ),
    tibble::tibble(
      component = COMPONENT_LABELS$q2_late,
      result_rows = nrow(views$late_res),
      significant_rows = sum(views$late_res$q.value < 0.1, na.rm = TRUE),
      n_complete = sample_n_label(views$late_res)
    ),
    tibble::tibble(
      component = COMPONENT_LABELS$q3_mutual_early,
      result_rows = nrow(views$mutual_res),
      significant_rows = sum(views$mutual_res$q_early < 0.1, na.rm = TRUE),
      n_complete = sample_n_label(views$mutual_res)
    ),
    tibble::tibble(
      component = COMPONENT_LABELS$q3_mutual_late,
      result_rows = nrow(views$mutual_res),
      significant_rows = sum(views$mutual_res$q_late < 0.1, na.rm = TRUE),
      n_complete = sample_n_label(views$mutual_res)
    )
  )

  if (!is.null(long_res) && nrow(long_res) > 0 && "timing" %in% names(long_res)) {
    long_summary <- long_res %>%
      dplyr::group_by(timing) %>%
      dplyr::summarise(
        component = paste0("Q4 longitudinal | ", dplyr::first(timing)),
        result_rows = dplyr::n(),
        significant_rows = sum(q.value < 0.1, na.rm = TRUE),
        n_complete = {
          vals <- sort(unique(n_obs[is.finite(n_obs)]))
          if (length(vals) == 0) {
            "not available"
          } else if (length(vals) == 1) {
            as.character(vals)
          } else {
            paste0(min(vals), "-", max(vals))
          }
        },
        .groups = "drop"
      ) %>%
      dplyr::select(component, result_rows, significant_rows, n_complete)
  } else {
    long_summary <- tibble::tibble(
      component = "Q4 longitudinal | no coefficient rows",
      result_rows = 0L,
      significant_rows = 0L,
      n_complete = "not available"
    )
  }

  dplyr::bind_rows(age_specific_summary, long_summary) %>%
    dplyr::mutate(
      significant_fraction = ifelse(
        result_rows > 0,
        round(significant_rows / result_rows, 3),
        NA_real_
      )
    )
}

compare_primary_sensitivity <- function(primary_df, sens_df, keys) {
  if (is.null(primary_df) || is.null(sens_df) || nrow(primary_df) == 0 || nrow(sens_df) == 0) {
    return(data.frame())
  }

  keys <- intersect(keys, intersect(names(primary_df), names(sens_df)))
  if (length(keys) == 0) return(data.frame())

  p <- primary_df %>%
    dplyr::filter(is.finite(estimate)) %>%
    dplyr::select(dplyr::all_of(keys), estimate_primary = estimate, q_primary = q.value)

  s <- sens_df %>%
    dplyr::filter(is.finite(estimate)) %>%
    dplyr::select(dplyr::all_of(keys), estimate_sensitivity = estimate, q_sensitivity = q.value)

  dplyr::inner_join(p, s, by = keys) %>%
    dplyr::mutate(
      delta_estimate = estimate_sensitivity - estimate_primary,
      abs_delta = abs(delta_estimate),
      direction_flip = sign(estimate_sensitivity) != sign(estimate_primary),
      q_reclassified = (q_primary < 0.1) != (q_sensitivity < 0.1)
    )
}
