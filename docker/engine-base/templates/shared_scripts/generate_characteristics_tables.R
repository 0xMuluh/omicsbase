#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(MultiAssayExperiment)
  library(SummarizedExperiment)
  library(dplyr)
  library(tidyr)
  library(readr)
})

to_chr <- function(x) {
  if (inherits(x, "haven_labelled")) {
    x <- haven::as_factor(x)
  }
  out <- tryCatch(as.character(x), error = function(e) as.character(unclass(x)))
  out
}

first_non_missing <- function(x) {
  chr <- to_chr(x)
  idx <- which(!is.na(chr) & trimws(chr) != "")
  if (length(idx) == 0) return(NA)
  x[[idx[1]]]
}

safe_num <- function(x) suppressWarnings(as.numeric(to_chr(x)))

fmt_mean_sd <- function(x, digits = 1) {
  x <- safe_num(x)
  x <- x[is.finite(x)]
  if (length(x) == 0) return("Not available")
  sprintf(paste0("%.", digits, "f (%.", digits, "f)"), mean(x), stats::sd(x))
}

fmt_mean_sd_mult <- function(x, mult=1, digits=1) {
  x <- safe_num(x) * mult
  x <- x[is.finite(x)]
  if (length(x) == 0) return("Not available")
  sprintf(paste0("%.", digits, "f (%.", digits, "f)"), mean(x), stats::sd(x))
}

fmt_median_iqr <- function(x, digits = 1) {
  x <- safe_num(x)
  x <- x[is.finite(x)]
  if (length(x) == 0) return("Not available")
  q <- stats::quantile(x, probs = c(0.25, 0.50, 0.75), na.rm = TRUE)
  sprintf(paste0("%.", digits, "f (%.", digits, "f–%.", digits, "f)"), q[2], q[1], q[3])
}

fmt_visit_age <- function(mae, visit_name, age_var, mult = 1, digits = 1) {
  if (!visit_name %in% names(experiments(mae))) return("NA")
  cd <- get_coldata(mae, visit_name)
  if (!age_var %in% names(cd)) return("NA")
  fmt_mean_sd_mult(cd[[age_var]], mult = mult, digits = digits)
}

fmt_n_pct <- function(x, predicate) {
  z <- predicate(x)
  keep <- !is.na(z)
  if (sum(keep) == 0) return("Not available")
  n <- sum(z[keep])
  pct <- 100 * n / sum(keep)
  sprintf("%d (%.1f%%)", n, pct)
}

fmt_cat_prop <- function(x, category) {
  chr <- to_chr(x)
  keep <- !is.na(chr) & trimws(chr) != ""
  if (sum(keep) == 0) return("Not available")
  z <- tolower(trimws(chr[keep])) == tolower(category)
  n <- sum(z)
  pct <- 100 * n / sum(keep)
  sprintf("%d (%.1f%%)", n, pct)
}

safe_p <- function(p) {
  if (!is.finite(p)) return("")
  if (p < 0.001) return("<0.001")
  sprintf("%.3f", p)
}

paired_p <- function(x1, x2) {
  a <- safe_num(x1)
  b <- safe_num(x2)
  keep <- is.finite(a) & is.finite(b)
  if (sum(keep) < 5) return(NA_real_)
  tryCatch(stats::t.test(a[keep], b[keep], paired = TRUE)$p.value, error = function(e) NA_real_)
}

cat_p <- function(x1, x2) {
  df <- data.frame(e = to_chr(x1), l = to_chr(x2))
  df <- df[!is.na(df$e) & trimws(df$e) != "" & !is.na(df$l) & trimws(df$l) != "", ]
  if (nrow(df) < 5) return(NA_real_)
  tab <- table(df$e, df$l)
  if (all(dim(tab) >= c(2, 2))) {
    tryCatch(stats::mcnemar.test(tab)$p.value, error = function(e) NA_real_)
  } else {
    NA_real_
  }
}

is_yes <- function(v) {
  res <- rep(NA, length(v))
  valid <- !is.na(v) & trimws(to_chr(v)) != ""
  s <- tolower(trimws(to_chr(v[valid])))
  res[valid] <- s %in% c("1", "yes", "y", "true", "vaginal delivery", "overweight", "obese")
  res
}

is_female <- function(v) {
  res <- rep(NA, length(v))
  valid <- !is.na(v) & trimws(to_chr(v)) != ""
  s <- tolower(trimws(to_chr(v[valid])))
  res[valid] <- s %in% c("female", "girl", "f", "2")
  res
}

is_preterm <- function(v) {
  res <- rep(NA, length(v))
  valid <- !is.na(v) & trimws(to_chr(v)) != ""
  s <- tolower(trimws(to_chr(v[valid])))
  x <- safe_num(v[valid])
  res[valid] <- s %in% c("1", "yes", "y", "true", "preterm") | (is.finite(x) & x < 37)
  res
}

is_overweight <- function(v) {
  res <- rep(NA, length(v))
  valid <- !is.na(v) & trimws(to_chr(v)) != ""
  s <- tolower(trimws(to_chr(v[valid])))
  x <- safe_num(v[valid])
  res[valid] <- s %in% c("overweight", "bmi <30", "bmi < 30") | (is.finite(x) & x >= 25 & x < 30)
  res
}

is_obese <- function(v) {
  res <- rep(NA, length(v))
  valid <- !is.na(v) & trimws(to_chr(v)) != ""
  s <- tolower(trimws(to_chr(v[valid])))
  x <- safe_num(v[valid])
  res[valid] <- s %in% c("obese", "bmi =/>30", "bmi =/> 30") | (is.finite(x) & x >= 30)
  res
}

is_vaginal <- function(v1, v2) {
  v_use <- ifelse(is.na(v2), v1, v2)
  res <- rep(NA, length(v_use))
  valid <- !is.na(v_use) & trimws(to_chr(v_use)) != ""
  s <- tolower(trimws(to_chr(v_use[valid])))
  res[valid] <- s %in% c("vaginal", "vaginal delivery", "vaginal unassisted", "vacuum extraction", "1")
  res
}

is_vaginal_delivery <- function(v) {
  res <- rep(NA, length(v))
  valid <- !is.na(v) & trimws(to_chr(v)) != ""
  s <- tolower(trimws(to_chr(v[valid])))
  res[valid] <- s %in% c("vaginal", "vaginal delivery", "vaginal unassisted", "vacuum extraction", "unassisted", "1", "2")
  res
}

is_overweight_or_obese <- function(v) {
  res <- rep(NA, length(v))
  valid <- !is.na(v) & trimws(to_chr(v)) != ""
  s <- tolower(trimws(to_chr(v[valid])))
  x <- safe_num(v[valid])
  res[valid] <- s %in% c("overweight", "obese", "bmi =/>30", "bmi =/> 30") | (is.finite(x) & x >= 25)
  res
}

get_coldata <- function(mae, vn) {
  as.data.frame(SummarizedExperiment::colData(experiments(mae)[[vn]]), stringsAsFactors = FALSE)
}

# -----------------------------
# Prenatal diet
# -----------------------------
prenatal_diet_mae <- readRDS("data/MAE2_original.rds")
prenatal_diet_all <- get_coldata(prenatal_diet_mae, "visit_all")

has_mmode <- "MModeOfDeliv" %in% names(prenatal_diet_all)
has_mmode2 <- "MModeOfDeliv.1" %in% names(prenatal_diet_all)
has_premature <- "Premature" %in% names(prenatal_diet_all)
has_prwlabor <- "PRWLabor" %in% names(prenatal_diet_all)

prenatal_diet_base <- prenatal_diet_all %>%
  arrange(StudyID) %>%
  group_by(StudyID) %>%
  summarise(
    MAge1 = first_non_missing(MAge1),
    MUniEdu = first_non_missing(MUniEdu),
    MPrimipara = first_non_missing(MPrimipara),
    MprepSmoke = first_non_missing(MprepSmoke),
    MPRSmoke = first_non_missing(MPRSmoke),
    MprepBMI = first_non_missing(MprepBMI),
    MBMIOverwOrObese = first_non_missing(MBMIOverwOrObese),
    MprevGDM_new = first_non_missing(MprevGDM_new),
    MModeOfDeliv = if (has_mmode) first_non_missing(MModeOfDeliv) else NA,
    MModeOfDeliv.1 = if (has_mmode2) first_non_missing(`MModeOfDeliv.1`) else NA,
    PRWLabor = if (has_prwlabor) first_non_missing(PRWLabor) else NA_real_,
    CGender = first_non_missing(CGender),
    Premature = if (has_premature) first_non_missing(Premature) else NA,
    CWeightBirth = first_non_missing(CWeightBirth),
    CHeightBirth = first_non_missing(CHeightBirth),
    CHeadBirth = first_non_missing(CHeadBirth),
    Bfdurationm = first_non_missing(Bfdurationm),
    MDietaryPatterns_1 = first_non_missing(MDietaryPatterns_1),
    MDIINormalDiet1 = first_non_missing(MDIINormalDiet1),
    MDIIDensityDiet1 = first_non_missing(MDIIDensityDiet1),
    MIDQ1 = first_non_missing(MIDQ1),
    MIDQ1_categorized = first_non_missing(MIDQ1_categorized),
    MEkcalDiet1 = first_non_missing(MEkcalDiet1),
    MEPercentProDiet1 = first_non_missing(MEPercentProDiet1),
    MEPercentCHODiet1 = first_non_missing(MEPercentCHODiet1),
    MEPercentFatDiet1 = first_non_missing(MEPercentFatDiet1),
    MEPercentPUFADiet1 = first_non_missing(MEPercentPUFADiet1),
    MEPercentMUFADiet1 = first_non_missing(MEPercentMUFADiet1),
    MEPercentSFADiet1 = first_non_missing(MEPercentSFADiet1),
    MEPercentFAn3Diet1 = first_non_missing(MEPercentFAn3Diet1),
    MEPercentFAn6Diet1 = first_non_missing(MEPercentFAn6Diet1),
    MEPercentFiballDiet1 = first_non_missing(MEPercentFiballDiet1),
    MDietaryPatterns_2 = first_non_missing(MDietaryPatterns_2),
    MDIINormalDiet2 = first_non_missing(MDIINormalDiet2),
    MDIIDensityDiet2 = first_non_missing(MDIIDensityDiet2),
    MIDQ2 = first_non_missing(MIDQ2),
    MIDQ2_categorized = first_non_missing(MIDQ2_categorized),
    MEkcalDiet2 = first_non_missing(MEkcalDiet2),
    MEPercentProDiet2 = first_non_missing(MEPercentProDiet2),
    MEPercentCHODiet2 = first_non_missing(MEPercentCHODiet2),
    MEPercentFatDiet2 = first_non_missing(MEPercentFatDiet2),
    MEPercentPUFADiet2 = first_non_missing(MEPercentPUFADiet2),
    MEPercentMUFADiet2 = first_non_missing(MEPercentMUFADiet2),
    MEPercentSFADiet2 = first_non_missing(MEPercentSFADiet2),
    MEPercentFAn3Diet2 = first_non_missing(MEPercentFAn3Diet2),
    MEPercentFAn6Diet2 = first_non_missing(MEPercentFAn6Diet2),
    MEPercentFiballDiet2 = first_non_missing(MEPercentFiballDiet2),
    .groups = "drop"
  )

prenatal_diet_visit_n <- sapply(c("visit_4", "visit_5", "visit_6", "visit_7"), function(vn) {
  if (!vn %in% names(experiments(prenatal_diet_mae))) return(NA_integer_)
  ncol(assay(experiments(prenatal_diet_mae)[[vn]], "mbo"))
})

prenatal_diet_t1 <- tibble::tibble(
  variable = c(
    "Maternal characteristics",
    "Mother's age (y)",
    "Education (college or university education) (n, %)",
    "Primipara (n, %)",
    "Smoking before pregnancy (n, %)",
    "Smoking during pregnancy (n, %)",
    "Pre-pregnancy BMI (kg/m2)",
    "Overweight (25 ≤BMI <30)",
    "Obesity (BMI ≥30)",
    "GDM diagnosis (n, %)",
    "Gestational weeks at the delivery (wk)",
    "Vaginal delivery (n, %)",
    "Child characteristics",
    "Girl sex (n, %)",
    "Born preterm (< 37+0 gw) (n, %)",
    "Birth weight (g)",
    "Birth height (cm)",
    "Birth head circumference (cm)",
    "Breast feeding duration (mo)",
    "Age at metabolomics sampling",
    "6 months (mo)",
    "12 months (mo)",
    "24 months (mo)",
    "5-6 years (y)"
  ),
  total_n = c(
    "",
    fmt_mean_sd(prenatal_diet_base$MAge1),
    fmt_n_pct(prenatal_diet_base$MUniEdu, is_yes),
    fmt_n_pct(prenatal_diet_base$MPrimipara, is_yes),
    fmt_n_pct(prenatal_diet_base$MprepSmoke, is_yes),
    fmt_n_pct(prenatal_diet_base$MPRSmoke, is_yes),
    fmt_median_iqr(prenatal_diet_base$MprepBMI),
    fmt_n_pct(prenatal_diet_base$MBMIOverwOrObese, is_overweight),
    fmt_n_pct(prenatal_diet_base$MBMIOverwOrObese, is_obese),
    fmt_n_pct(prenatal_diet_base$MprevGDM_new, is_yes),
    fmt_mean_sd(prenatal_diet_base$PRWLabor),
    fmt_n_pct(prenatal_diet_base$MModeOfDeliv, is_vaginal_delivery),
    "",
    fmt_n_pct(prenatal_diet_base$CGender, is_female),
    fmt_n_pct(prenatal_diet_base$Premature, is_preterm),
    fmt_mean_sd(prenatal_diet_base$CWeightBirth),
    fmt_mean_sd(prenatal_diet_base$CHeightBirth),
    fmt_mean_sd(prenatal_diet_base$CHeadBirth),
    fmt_median_iqr(prenatal_diet_base$Bfdurationm),
    "",
    fmt_visit_age(prenatal_diet_mae, "visit_4", "CAge4", mult = 12),
    fmt_visit_age(prenatal_diet_mae, "visit_5", "CAge5", mult = 12),
    fmt_visit_age(prenatal_diet_mae, "visit_6", "CAge6", mult = 12),
    fmt_visit_age(prenatal_diet_mae, "visit_7", "CAge7")
  )
)

prenatal_diet_t2_rows <- tibble::tibble(
  variable = character(), early = character(), late = character(), p_value_early_vs_late = character()
)
add_row <- function(df, v, e, l, p) bind_rows(df, tibble::tibble(variable=v, early=e, late=l, p_value_early_vs_late=p))

prenatal_diet_t2_rows <- add_row(prenatal_diet_t2_rows, "Dietary pattern", "", "", safe_p(cat_p(prenatal_diet_base$MDietaryPatterns_1, prenatal_diet_base$MDietaryPatterns_2)))
prenatal_diet_t2_rows <- add_row(prenatal_diet_t2_rows, "Healthy", fmt_cat_prop(prenatal_diet_base$MDietaryPatterns_1, "Healthy"), fmt_cat_prop(prenatal_diet_base$MDietaryPatterns_2, "Healthy"), "")
prenatal_diet_t2_rows <- add_row(prenatal_diet_t2_rows, "Unhealthy", fmt_cat_prop(prenatal_diet_base$MDietaryPatterns_1, "Less healthy"), fmt_cat_prop(prenatal_diet_base$MDietaryPatterns_2, "Less healthy"), "")
prenatal_diet_t2_rows <- add_row(prenatal_diet_t2_rows, "Dietary inflammatory index", fmt_mean_sd(prenatal_diet_base$MDIINormalDiet1), fmt_mean_sd(prenatal_diet_base$MDIINormalDiet2), safe_p(paired_p(prenatal_diet_base$MDIINormalDiet1, prenatal_diet_base$MDIINormalDiet2)))
prenatal_diet_t2_rows <- add_row(prenatal_diet_t2_rows, "Diet quality score (IDQ)", "", "", safe_p(cat_p(prenatal_diet_base$MIDQ1_categorized, prenatal_diet_base$MIDQ2_categorized)))
prenatal_diet_t2_rows <- add_row(prenatal_diet_t2_rows, "Poor", fmt_cat_prop(prenatal_diet_base$MIDQ1_categorized, "Poor"), fmt_cat_prop(prenatal_diet_base$MIDQ2_categorized, "Poor"), "")
prenatal_diet_t2_rows <- add_row(prenatal_diet_t2_rows, "Good", fmt_cat_prop(prenatal_diet_base$MIDQ1_categorized, "Good"), fmt_cat_prop(prenatal_diet_base$MIDQ2_categorized, "Good"), "")
prenatal_diet_t2_rows <- add_row(prenatal_diet_t2_rows, "Energy (KJ)", fmt_mean_sd_mult(prenatal_diet_base$MEkcalDiet1, 4.184), fmt_mean_sd_mult(prenatal_diet_base$MEkcalDiet2, 4.184), safe_p(paired_p(prenatal_diet_base$MEkcalDiet1, prenatal_diet_base$MEkcalDiet2)))
prenatal_diet_t2_rows <- add_row(prenatal_diet_t2_rows, "Protein (E%)", fmt_mean_sd(prenatal_diet_base$MEPercentProDiet1), fmt_mean_sd(prenatal_diet_base$MEPercentProDiet2), safe_p(paired_p(prenatal_diet_base$MEPercentProDiet1, prenatal_diet_base$MEPercentProDiet2)))
prenatal_diet_t2_rows <- add_row(prenatal_diet_t2_rows, "Carbohydrate (E%)", fmt_mean_sd(prenatal_diet_base$MEPercentCHODiet1), fmt_mean_sd(prenatal_diet_base$MEPercentCHODiet2), safe_p(paired_p(prenatal_diet_base$MEPercentCHODiet1, prenatal_diet_base$MEPercentCHODiet2)))
prenatal_diet_t2_rows <- add_row(prenatal_diet_t2_rows, "Total fat (E%)", fmt_mean_sd(prenatal_diet_base$MEPercentFatDiet1), fmt_mean_sd(prenatal_diet_base$MEPercentFatDiet2), safe_p(paired_p(prenatal_diet_base$MEPercentFatDiet1, prenatal_diet_base$MEPercentFatDiet2)))
prenatal_diet_t2_rows <- add_row(prenatal_diet_t2_rows, "PUFA (E%)", fmt_mean_sd(prenatal_diet_base$MEPercentPUFADiet1), fmt_mean_sd(prenatal_diet_base$MEPercentPUFADiet2), safe_p(paired_p(prenatal_diet_base$MEPercentPUFADiet1, prenatal_diet_base$MEPercentPUFADiet2)))
prenatal_diet_t2_rows <- add_row(prenatal_diet_t2_rows, "MUFA (E%)", fmt_mean_sd(prenatal_diet_base$MEPercentMUFADiet1), fmt_mean_sd(prenatal_diet_base$MEPercentMUFADiet2), safe_p(paired_p(prenatal_diet_base$MEPercentMUFADiet1, prenatal_diet_base$MEPercentMUFADiet2)))
prenatal_diet_t2_rows <- add_row(prenatal_diet_t2_rows, "SFA (E%)", fmt_mean_sd(prenatal_diet_base$MEPercentSFADiet1), fmt_mean_sd(prenatal_diet_base$MEPercentSFADiet2), safe_p(paired_p(prenatal_diet_base$MEPercentSFADiet1, prenatal_diet_base$MEPercentSFADiet2)))
prenatal_diet_t2_rows <- add_row(prenatal_diet_t2_rows, "n-3 fatty acids", fmt_mean_sd(prenatal_diet_base$MEPercentFAn3Diet1), fmt_mean_sd(prenatal_diet_base$MEPercentFAn3Diet2), safe_p(paired_p(prenatal_diet_base$MEPercentFAn3Diet1, prenatal_diet_base$MEPercentFAn3Diet2)))
prenatal_diet_t2_rows <- add_row(prenatal_diet_t2_rows, "n-6 fatty acids", fmt_mean_sd(prenatal_diet_base$MEPercentFAn6Diet1), fmt_mean_sd(prenatal_diet_base$MEPercentFAn6Diet2), safe_p(paired_p(prenatal_diet_base$MEPercentFAn6Diet1, prenatal_diet_base$MEPercentFAn6Diet2)))
prenatal_diet_t2_rows <- add_row(prenatal_diet_t2_rows, "Fiber (g)", fmt_mean_sd(prenatal_diet_base$MEPercentFiballDiet1), fmt_mean_sd(prenatal_diet_base$MEPercentFiballDiet2), safe_p(paired_p(prenatal_diet_base$MEPercentFiballDiet1, prenatal_diet_base$MEPercentFiballDiet2)))

write_csv(prenatal_diet_t1, "data/prenatal_diet_characteristics_table1.csv")
write_csv(prenatal_diet_t2_rows, "data/prenatal_diet_characteristics_table2.csv")

# -----------------------------
# Child diet
# -----------------------------
child_diet_mae <- readRDS("data/MAE2_original.rds")

child_diet_rows <- c(
  "Education (college or university education) (n, %)",
  "Mother's age (y)",
  "Primiparity (n, %)",
  "Smoking during pregnancy (n, %)",
  "Pre-pregnancy BMI (kg/m2)",
  "Overweight (25 ≤BMI <30) (n, %)",
  "Obesity (BMI ≥30) (n, %)",
  "Gestational diabetes mellitus diagnosis (n, %)",
  "Vaginal delivery (n, %)",
  "Gestational weeks at delivery (wk)",
  "Breast feeding duration (mo)",
  "Girl sex (n, %)",
  "Born preterm (n, %)",
  "Birth",
  "Birth height (cm)",
  "Birth weight (g)",
  "Birth head circumference (cm)",
  "Child's age at blood sampling (y)",
  "Fasting time before blood sampling (h)",
  "Healthy at the time of blood sampling (n, %)",
  "Use of medication (n, %)"
)

# Custom predicates per row (NULL = use auto-detect is_yes/continuous)
child_diet_predicates <- list(
  NULL,  # Education -> is_yes
  NULL,  # Age -> continuous
  NULL,  # Primiparity -> is_yes
  NULL,  # Smoking -> is_yes
  NULL,  # Pre-pregnancy BMI -> continuous
  is_overweight,  # overweight -> custom
  is_obese,       # obesity -> custom
  NULL,  # GDM -> is_yes
  is_vaginal_delivery,  # Delivery mode -> custom
  NULL,  # Gestational weeks -> continuous
  NULL,  # Breast feeding -> continuous
  is_female,  # Sex -> custom
  NULL,  # Born pre-term -> is_yes
  NULL,  # Birth -> section header
  NULL,  # Height -> continuous
  NULL,  # Weight -> continuous
  NULL,  # Head circumference -> continuous
  NULL,  # Child's age -> continuous
  NULL,  # Fasting time -> continuous
  NULL,  # Healthy -> is_yes
  NULL   # Medication -> is_yes
)

child_diet_var_map <- tibble::tibble(
  row = child_diet_rows,
  col6 = c(
    "MUniEdu", "MAge1", "MPrimipara", "MPRSmoke", "MprepBMI", "MBMIOverwOrObese",
    "MBMIOverwOrObese",
    "MGDMOGTT1OR2Fi", "MModeOfDelivery_cat", "PRWLabor", "Bfdurationm", "CGender",
    "Premature", NA_character_, "CHeightBirth", "CWeightBirth", "CHeadBirth",
    "CDecimalAge6", "CFastingHoursBloodSample6", "CHealthy6", "CMed6"
  ),
  col7 = c(
    "MUniEdu", "MAge1", "MPrimipara", "MPRSmoke", "MprepBMI", "MBMIOverwOrObese",
    "MBMIOverwOrObese",
    "MGDMOGTT1OR2Fi", "MModeOfDelivery_cat", "PRWLabor", "Bfdurationm", "CGender",
    "Premature", NA_character_, "CHeightBirth", "CWeightBirth", "CHeadBirth",
    "CDecimalAge7", "CFastingHoursBloodSample7", "CHealthy7", "CMed7"
  )
)

summarize_child_diet_visit <- function(v) {
  vn <- paste0("visit_", v)
  cd <- get_coldata(child_diet_mae, vn)
  grp_col <- paste0("C_DietQuality_classes_", v)
  if (!grp_col %in% names(cd)) {
    stop("Missing ", grp_col, " in ", vn)
  }

  grp <- tolower(trimws(to_chr(cd[[grp_col]])))
  
  # Map actual names to template names
  cd$.__grp <- rep("<missing>", nrow(cd))
  cd$.__grp[grp %in% c("ok/good", "good", "3")] <- "Good"
  cd$.__grp[grp %in% c("moderate but needs to be improved", "moderate", "2")] <- "Moderate"
  cd$.__grp[grp %in% c("needs to be vastly improved", "poor", "1")] <- "Poor"

  level_order <- c("Good", "Moderate", "Poor")
  present <- intersect(level_order, unique(cd$.__grp))
  if (length(present) == 0) {
    present <- sort(unique(cd$.__grp))
  }

  one_row <- function(row_name, col_name, custom_pred = NULL) {
    if (is.na(col_name) || !nzchar(col_name)) {
      return(tibble::tibble(
        characteristic = row_name,
        all_mothers_children = "",
        good = "",
        moderate = "",
        poor = "",
        p_value = ""
      ))
    }

    x <- cd[[col_name]]
    x_num <- safe_num(x)

    # Use custom predicate if provided, otherwise auto-detect
    if (!is.null(custom_pred)) {
      pred <- custom_pred
      is_cont <- FALSE
    } else {
      pred <- is_yes
      is_cont <- sum(is.finite(x_num)) >= 20 && length(unique(x_num[is.finite(x_num)])) >= 8
    }

    is_normal_var <- function(col_name) {
      col_name %in% c("MAge1", "CWeightBirth", "CHeightBirth", "CHeadBirth", "PRWLabor", "CDecimalAge6", "CDecimalAge7")
    }

    fmt_continuous <- function(x_num, col_name, digits = 1) {
      if (is_normal_var(col_name)) {
        fmt_mean_sd(x_num, digits = digits)
      } else {
        fmt_median_iqr(x_num, digits = digits)
      }
    }

    all_val <- if (is_cont) fmt_continuous(x_num, col_name) else fmt_n_pct(x, pred)

    group_vals <- sapply(level_order, function(g) {
      if (!g %in% present) return("NA")
      idx <- cd$.__grp == g
      if (is_cont) fmt_continuous(x_num[idx], col_name) else fmt_n_pct(x[idx], pred)
    }, simplify = TRUE)

    p <- NA_real_
    idx_nonmiss_grp <- !is.na(cd$.__grp) & cd$.__grp != "<missing>"
    if (is_cont) {
      dat <- data.frame(y = x_num[idx_nonmiss_grp], g = cd$.__grp[idx_nonmiss_grp])
      dat <- dat[is.finite(dat$y), , drop = FALSE]
      if (length(unique(dat$g)) >= 2 && nrow(dat) >= 10) {
        p <- tryCatch(stats::kruskal.test(y ~ g, data = dat)$p.value, error = function(e) NA_real_)
      }
    } else {
      y <- ifelse(pred(x), "Yes", "No")
      tab <- table(cd$.__grp[idx_nonmiss_grp], y[idx_nonmiss_grp])
      if (all(dim(tab) >= c(2, 2))) {
        p <- tryCatch(
          stats::fisher.test(tab)$p.value,
          error = function(e) tryCatch(stats::chisq.test(tab)$p.value, error = function(e2) NA_real_)
        )
      }
    }

    tibble::tibble(
      characteristic = row_name,
      all_mothers_children = all_val,
      good = group_vals[["Good"]],
      moderate = group_vals[["Moderate"]],
      poor = group_vals[["Poor"]],
      p_value = safe_p(p)
    )
  }

  rows_list <- lapply(seq_len(nrow(child_diet_var_map)), function(i) {
    one_row(child_diet_var_map$row[[i]], if (v == 6) child_diet_var_map$col6[[i]] else child_diet_var_map$col7[[i]], custom_pred = child_diet_predicates[[i]])
  })

  mother_row <- tibble::tibble(characteristic="Mother", all_mothers_children="", good="", moderate="", poor="", p_value="")
  child_row <- tibble::tibble(characteristic="Child", all_mothers_children="", good="", moderate="", poor="", p_value="")

  bind_rows(
    mother_row,
    bind_rows(rows_list[1:11]),
    child_row,
    bind_rows(rows_list[12:21])
  )
}

child_diet_v6 <- summarize_child_diet_visit(6)
child_diet_v7 <- summarize_child_diet_visit(7)

write_csv(child_diet_v6, "data/child_diet_characteristics_visit6.csv")
write_csv(child_diet_v7, "data/child_diet_characteristics_visit7.csv")

message("Wrote characteristics CSV outputs for Prenatal diet and Child diet.")
