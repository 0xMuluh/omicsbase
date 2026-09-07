# Shared reporting helpers for the Child diet Quarto site.

suppressPackageStartupMessages(library(dplyr))

COMPONENT_LABELS <- list(
  concurrent_2y = "Concurrent at 2 Years",
  concurrent_56y = "Concurrent at 5-6 Years",
  prospective = "Prospective: 2-Year Diet to 5-6-Year Metabolites",
  rm_main = "Repeated-Measures Main Effect",
  rm_interaction = "Repeated-Measures Diet-by-Time Interaction",
  rm_both = "Repeated-Measures Across 2 Years and 5-6 Years"
)

family_order <- c("Nutrients", "Diet Quality")

visit_label <- function(v) ifelse(v == 6L, "2 Years", "5-6 Years")

base_exposure_child_diet <- function(x) sub("(_)?[67]$", "", x)

pretty_var <- function(x) {
  x <- base_exposure_child_diet(x)
  x <- gsub("CEPercent", "Percent ", x)
  x <- gsub("CFiball", "Fibre ", x)
  x <- gsub("C_", "", x)
  x <- gsub("DietQuality", "Diet Quality ", x)
  x <- gsub("CHO", "Carbohydrate", x)
  x <- gsub("Pro", "Protein", x)
  x <- gsub("FAn3", "n-3 fatty acids", x)
  x <- gsub("FAn6", "n-6 fatty acids", x)
  x <- gsub("_", " ", x, fixed = TRUE)
  x <- gsub("\\s+", " ", trimws(x))
  tools::toTitleCase(tolower(x))
}

pretty_exposure <- pretty_var

sample_n_label <- function(df, n_col = "n", na_label = "not available") {
  if (is.null(df) || nrow(df) == 0 || !(n_col %in% names(df))) {
    return(if (is.na(na_label)) NA_character_ else na_label)
  }
  vals <- suppressWarnings(as.numeric(df[[n_col]]))
  vals <- vals[is.finite(vals)]
  if (length(vals) == 0) {
    return(if (is.na(na_label)) NA_character_ else na_label)
  }
  if (length(vals) == 1) return(as.character(vals[[1]]))
  paste0(min(vals), "-", max(vals))
}

exposure_family <- function(exposure) {
  ifelse(grepl("DietQuality", exposure, ignore.case = TRUE), "Diet Quality", "Nutrients")
}

add_exposure_family <- function(df) {
  if (is.null(df) || nrow(df) == 0) return(df)
  if ("exposure_pair" %in% names(df)) {
    df <- df %>% dplyr::mutate(exposure = exposure_pair)
  }
  if (!("exposure" %in% names(df))) return(df)
  df %>% dplyr::mutate(exposure_family = exposure_family(exposure))
}

split_q4_terms <- function(q4) {
  list(
    main = q4 %>% dplyr::filter(!grepl(":", term)),
    interaction = q4 %>% dplyr::filter(grepl(":", term))
  )
}

build_result_inventory <- function(results) {
  q4_split <- split_q4_terms(results$q4)
  dplyr::bind_rows(
    add_exposure_family(results$q1) %>% dplyr::mutate(
      component = COMPONENT_LABELS$concurrent_2y, n_col = n
    ),
    add_exposure_family(results$q2) %>% dplyr::mutate(
      component = COMPONENT_LABELS$concurrent_56y, n_col = n
    ),
    add_exposure_family(results$q3) %>% dplyr::mutate(
      component = COMPONENT_LABELS$prospective, n_col = n
    ),
    add_exposure_family(q4_split$main) %>% dplyr::mutate(
      component = COMPONENT_LABELS$rm_main, n_col = n_obs
    ),
    add_exposure_family(q4_split$interaction) %>% dplyr::mutate(
      component = COMPONENT_LABELS$rm_interaction, n_col = n_obs
    )
  ) %>%
    dplyr::group_by(component, exposure_family) %>%
    dplyr::summarise(
      result_rows = dplyr::n(),
      significant_rows_q_lt_0_10 = sum(q.value < 0.10, na.rm = TRUE),
      complete_case_n_range = {
        vals <- sort(unique(n_col[is.finite(n_col)]))
        if (length(vals) == 0) NA_character_ else paste0(min(vals), "-", max(vals))
      },
      .groups = "drop"
    ) %>%
    dplyr::mutate(exposure_family = factor(exposure_family, levels = family_order)) %>%
    dplyr::arrange(component, exposure_family)
}

build_signal_summary <- function(results) {
  q4_split <- split_q4_terms(results$q4)
  dplyr::bind_rows(
    add_exposure_family(results$q1) %>% dplyr::mutate(
      component = COMPONENT_LABELS$concurrent_2y, n_col = n
    ),
    add_exposure_family(results$q2) %>% dplyr::mutate(
      component = COMPONENT_LABELS$concurrent_56y, n_col = n
    ),
    add_exposure_family(results$q3) %>% dplyr::mutate(
      component = COMPONENT_LABELS$prospective, n_col = n
    ),
    add_exposure_family(q4_split$main) %>% dplyr::mutate(
      component = COMPONENT_LABELS$rm_main, n_col = n_obs
    ),
    add_exposure_family(q4_split$interaction) %>% dplyr::mutate(
      component = COMPONENT_LABELS$rm_interaction, n_col = n_obs
    )
  ) %>%
    dplyr::group_by(component, exposure_family) %>%
    dplyr::summarise(
      result_rows = dplyr::n(),
      significant_rows = sum(q.value < 0.1, na.rm = TRUE),
      n_complete = {
        vals <- sort(unique(n_col[is.finite(n_col)]))
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
    dplyr::mutate(
      exposure_family = factor(exposure_family, levels = family_order),
      significant_fraction = round(significant_rows / result_rows, 3)
    ) %>%
    dplyr::arrange(component, exposure_family)
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

has_any_nonmissing <- function(df, cols) {
  cols <- intersect(cols, names(df))
  if (length(cols) == 0) return(rep(FALSE, nrow(df)))
  rowSums(!is.na(df[, cols, drop = FALSE])) > 0
}

component_label <- function(x) dplyr::case_when(
  x == "Concurrent 2y" ~ COMPONENT_LABELS$concurrent_2y,
  x == "Concurrent 5-6y" ~ COMPONENT_LABELS$concurrent_56y,
  x == "Prospective 2y -> 5-6y" ~ COMPONENT_LABELS$prospective,
  x == "Repeated main effect" ~ COMPONENT_LABELS$rm_main,
  x == "Repeated interaction" ~ COMPONENT_LABELS$rm_interaction,
  TRUE ~ x
)

component_order <- c(
  "Concurrent 2y",
  "Concurrent 5-6y",
  "Prospective 2y -> 5-6y",
  "Repeated main effect",
  "Repeated interaction"
)
