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

safe_num <- function(x) suppressWarnings(as.numeric(to_chr(x)))

fmt_mean_sd <- function(x, digits = 1) {
  x <- safe_num(x)
  x <- x[is.finite(x)]
  if (length(x) == 0) return("Not available")
  sprintf(paste0("%.", digits, "f (%.", digits, "f)"), mean(x), stats::sd(x))
}

fmt_n_pct <- function(x, predicate) {
  z <- predicate(x)
  keep <- !is.na(z)
  if (sum(keep) == 0) return("Not available")
  n <- sum(z[keep])
  pct <- 100 * n / sum(keep)
  sprintf("%d (%.1f%%)", n, pct)
}

safe_p <- function(p) {
  if (!is.finite(p)) return("")
  if (p < 0.001) return("<0.001")
  sprintf("%.3f", p)
}

is_yes <- function(v) {
  res <- rep(NA, length(v))
  valid <- !is.na(v) & trimws(to_chr(v)) != ""
  s <- tolower(trimws(to_chr(v[valid])))
  res[valid] <- s %in% c("1", "yes", "y", "true", "vaginal delivery", "overweight", "obese", "optimal", "preterm", "2", "kky", "kylla")
  res
}

is_preterm <- function(v) {
  res <- rep(NA, length(v))
  valid <- !is.na(v) & trimws(to_chr(v)) != ""
  s <- tolower(trimws(to_chr(v[valid])))
  x <- safe_num(v[valid])
  res[valid] <- s %in% c("1", "yes", "y", "true", "preterm", "2") | (is.finite(x) & x < 37)
  res
}

is_female <- function(v) {
  res <- rep(NA, length(v))
  valid <- !is.na(v) & trimws(to_chr(v)) != ""
  s <- tolower(trimws(to_chr(v[valid])))
  res[valid] <- s %in% c("female", "girl", "f", "1", "tytto", "tyttö")
  res
}

is_bmi_ge_30 <- function(v) {
  res <- rep(NA, length(v))
  valid <- !is.na(v) & trimws(to_chr(v)) != ""
  s <- tolower(trimws(to_chr(v[valid])))
  res[valid] <- s %in% c("obese", "bmi =/>30", "bmi =/> 30", "bmi >=30", "bmi >= 30", "2", "yes")
  res
}

is_bmi_lt_30 <- function(v) {
  res <- rep(NA, length(v))
  valid <- !is.na(v) & trimws(to_chr(v)) != ""
  s <- tolower(trimws(to_chr(v[valid])))
  res[valid] <- s %in% c("bmi <30", "bmi < 30", "1", "no")
  res
}

is_vaginal_delivery <- function(v) {
  res <- rep(NA, length(v))
  valid <- !is.na(v) & trimws(to_chr(v)) != ""
  s <- tolower(trimws(to_chr(v[valid])))
  res[valid] <- s %in% c("vaginal", "vaginal delivery", "vaginal unassisted", "unassisted", "1", "vaginal delivery")
  res
}

get_coldata <- function(mae, vn) {
  as.data.frame(SummarizedExperiment::colData(experiments(mae)[[vn]]), stringsAsFactors = FALSE)
}

# Custom predicates per row (NULL = use auto-detect is_yes/continuous)
is_fish_oil_placebo <- function(v) {
  res <- rep(NA, length(v))
  valid <- !is.na(v) & trimws(to_chr(v)) != ""
  s <- tolower(trimws(to_chr(v[valid])))
  res[valid] <- s %in% c("fish oil+placebo")
  res
}
is_probiotics_placebo <- function(v) {
  res <- rep(NA, length(v))
  valid <- !is.na(v) & trimws(to_chr(v)) != ""
  s <- tolower(trimws(to_chr(v[valid])))
  res[valid] <- s %in% c("probiotics+placebo")
  res
}
is_fish_oil_probiotics <- function(v) {
  res <- rep(NA, length(v))
  valid <- !is.na(v) & trimws(to_chr(v)) != ""
  s <- tolower(trimws(to_chr(v[valid])))
  res[valid] <- s %in% c("fish oil+probiotics")
  res
}
is_placebo_placebo <- function(v) {
  res <- rep(NA, length(v))
  valid <- !is.na(v) & trimws(to_chr(v)) != ""
  s <- tolower(trimws(to_chr(v[valid])))
  res[valid] <- s %in% c("placebo+placebo")
  res
}
is_living_with_partner <- function(v) {
  res <- rep(NA, length(v))
  valid <- !is.na(v) & trimws(to_chr(v)) != ""
  s <- tolower(trimws(to_chr(v[valid])))
  res[valid] <- grepl("^avioliitto", s)
  res
}

# We want to map the row names and variables for the table
neurocognition_rows <- c(
  "Mother",
  "Mother's age (y)",
  "Education (college or university education) (n, %)",
  "Primipara (n, %)",
  "Intervention: Fish oil + placebo (n, %)",
  "Intervention: Probiotics + placebo (n, %)",
  "Intervention: Fish oil + probiotics (n, %)",
  "Intervention: Placebo + placebo (n, %)",
  "Marital status: married/cohabiting (n, %)",
  "Smoking during pregnancy (n, %)",
  "Pre-pregnancy BMI (kg/m2)",
  "BMI <30 category (normal/overweight) (n, %)",
  "Obesity (BMI >=30) (n, %)",
  "Gestational diabetes mellitus diagnosis (n, %)",
  "Gestational weeks at delivery (wk)",
  "Vaginal delivery (n, %)",
  "Child",
  "Born preterm (< 37+0 gw) (n, %)",
  "Small for gestational weight (n, %)",
  "Large for gestational weight (n, %)",
  "Apgar score at 1 min",
  "Apgar score at 5 min",
  "Birth weight (g)",
  "Birth height (cm)",
  "Birth head circumference (cm)",
  "Breast feeding duration (mo)",
  "Child age at psychologist visit (y)",
  "Child age at physiotherapist visit (y)",
  "Child Cognitive Function (2 years)",
  "Bayley Composite Cognitive Score",
  "Bayley Composite Language Score",
  "  Expressive Language Standard Score",
  "  Receptive Language Standard Score",
  "Bayley Composite Motor Score",
  "  Fine Motor Standard Score",
  "  Gross Motor Standard Score",
  "HINE Global Score",
  "HINE Optimality Score (Optimal, preterm excluded) (n, %)",
  "Metabolite sampling details",
  "Fasting time before blood sampling (h)"
)

neurocognition_predicates <- list(
  NULL,  # Section: Mother
  NULL,  # MAge1 -> continuous
  is_yes,  # MUniEdu -> custom
  is_yes,  # MPrimipara -> custom
  is_fish_oil_placebo,   # Intervention: Fish oil + placebo
  is_probiotics_placebo, # Intervention: Probiotics + placebo
  is_fish_oil_probiotics,# Intervention: Fish oil + probiotics
  is_placebo_placebo,    # Intervention: Placebo + placebo
  is_living_with_partner,# Marital status: married/cohabiting
  is_yes,  # MPRSmoke -> custom
  NULL,  # MprepBMI -> continuous
  is_bmi_lt_30, # MBMIOverwOrObese: BMI <30 category; includes normal and overweight
  is_bmi_ge_30, # MBMIOverwOrObese: BMI <30 vs BMI =/>30
  is_yes,  # GDM -> custom
  NULL,  # PRWLabor -> continuous
  is_vaginal_delivery,  # Mode of delivery -> custom
  NULL,  # Section: Child
  is_preterm,  # Born preterm -> custom
  is_yes,  # CSGA -> custom
  is_yes,  # CMacrosomy -> custom
  NULL,  # CApgar1 -> continuous
  NULL,  # CApgar5 -> continuous
  NULL,  # CBW -> continuous
  NULL,  # Birth height -> continuous
  NULL,  # Head circumference -> continuous
  NULL,  # Breast feeding -> continuous
  NULL,  # Psychologist age -> continuous
  NULL,  # Physiotherapist age -> continuous
  NULL,  # Section: Cognitive
  NULL,  # CCognition_indexpoints6 -> continuous
  NULL,  # CLanguage_indexpoints6 -> continuous
  NULL,  # CExpressivelanguage_standardpoints6 -> continuous
  NULL,  # CReceptivelanguage_standardpoints6 -> continuous
  NULL,  # CMotor_indexpoints6 -> continuous
  NULL,  # CFinemotor_standardpoints6 -> continuous
  NULL,  # CGrossmotor_standardpoints6 -> continuous
  NULL,  # Hammersmith6 -> continuous
  is_yes,  # HINE_optimal -> custom (Optimal vs Suboptimal)
  NULL,  # Section: Metabolite sampling
  NULL   # Fasting hours -> continuous
)

# Variable mapping for Visit 4, 5, 6
neurocognition_var_map <- tibble::tibble(
  row = neurocognition_rows,
  col4 = c(
    NA_character_, "MAge1", "MUniEdu", "MPrimipara", 
    "InterventionGroup", "InterventionGroup", "InterventionGroup", "InterventionGroup", "MLIVE1", 
    "MPRSmoke", "MprepBMI", "MBMIOverwOrObese", "MBMIOverwOrObese",
    "MGDMOGTT1OR2Fi", "PRWLabor", "MModeOfDeliv.1", NA_character_, "Premature", "CSGA", "CMacrosomy",
    "CApgar1", "CApgar5", "CBW", "Height.0", "Head.0", "Bfdurationm", "CAgePsychologist6", "CAgePhysiotherapist6",
    NA_character_, "CCognition_indexpoints6", "CLanguage_indexpoints6", "CExpressivelanguage_standardpoints6",
    "CReceptivelanguage_standardpoints6", "CMotor_indexpoints6", "CFinemotor_standardpoints6", "CGrossmotor_standardpoints6",
    "Hammersmith6", "HINE_optimal", NA_character_, "CFastingHoursBloodSample4"
  ),
  col5 = c(
    NA_character_, "MAge1", "MUniEdu", "MPrimipara", 
    "InterventionGroup", "InterventionGroup", "InterventionGroup", "InterventionGroup", "MLIVE1", 
    "MPRSmoke", "MprepBMI", "MBMIOverwOrObese", "MBMIOverwOrObese",
    "MGDMOGTT1OR2Fi", "PRWLabor", "MModeOfDeliv.1", NA_character_, "Premature", "CSGA", "CMacrosomy",
    "CApgar1", "CApgar5", "CBW", "Height.0", "Head.0", "Bfdurationm", "CAgePsychologist6", "CAgePhysiotherapist6",
    NA_character_, "CCognition_indexpoints6", "CLanguage_indexpoints6", "CExpressivelanguage_standardpoints6",
    "CReceptivelanguage_standardpoints6", "CMotor_indexpoints6", "CFinemotor_standardpoints6", "CGrossmotor_standardpoints6",
    "Hammersmith6", "HINE_optimal", NA_character_, "CFastingHoursBloodSample5"
  ),
  col6 = c(
    NA_character_, "MAge1", "MUniEdu", "MPrimipara", 
    "InterventionGroup", "InterventionGroup", "InterventionGroup", "InterventionGroup", "MLIVE1", 
    "MPRSmoke", "MprepBMI", "MBMIOverwOrObese", "MBMIOverwOrObese",
    "MGDMOGTT1OR2Fi", "PRWLabor", "MModeOfDeliv.1", NA_character_, "Premature", "CSGA", "CMacrosomy",
    "CApgar1", "CApgar5", "CBW", "Height.0", "Head.0", "Bfdurationm", "CAgePsychologist6", "CAgePhysiotherapist6",
    NA_character_, "CCognition_indexpoints6", "CLanguage_indexpoints6", "CExpressivelanguage_standardpoints6",
    "CReceptivelanguage_standardpoints6", "CMotor_indexpoints6", "CFinemotor_standardpoints6", "CGrossmotor_standardpoints6",
    "Hammersmith6", "HINE_optimal", NA_character_, "CFastingHoursBloodSample6"
  )
)

# List of continuous variables to override auto-detection
continuous_vars <- c(
  "MAge1", "MprepBMI", "PRWLabor", "CApgar1", "CApgar5", "CBW", "Height.0", "Head.0",
  "Bfdurationm", "CAgePsychologist6", "CAgePhysiotherapist6",
  "CCognition_indexpoints6", "CLanguage_indexpoints6", "CExpressivelanguage_standardpoints6",
  "CReceptivelanguage_standardpoints6", "CMotor_indexpoints6", "CFinemotor_standardpoints6",
  "CGrossmotor_standardpoints6", "Hammersmith6", "CFastingHoursBloodSample4",
  "CFastingHoursBloodSample5", "CFastingHoursBloodSample6"
)

neurocognition_mae <- readRDS("data/MAE_original.rds")

summarize_neurocognition_visit <- function(v) {
  vn <- paste0("visit_", v)
  cd <- get_coldata(neurocognition_mae, vn)
  
  # Filter to matched analysis sample (having at least one valid primary neurocognitive outcome)
  primary_outcomes <- c("CCognition_indexpoints6", "CLanguage_indexpoints6", "CMotor_indexpoints6", "Hammersmith6")
  has_outcome <- rowSums(!is.na(cd[, intersect(primary_outcomes, names(cd)), drop = FALSE])) > 0
  cd <- cd[has_outcome, , drop = FALSE]
  
  # Child sex variable
  sex_var <- "CGender"
  if (!sex_var %in% names(cd)) {
    stop("Missing CGender in ", vn)
  }
  
  # Create a clean child sex factor: Girls vs Boys
  is_g <- is_female(cd[[sex_var]])
  cd$.__grp <- rep("<missing>", nrow(cd))
  cd$.__grp[is_g == TRUE] <- "Girls"
  cd$.__grp[is_g == FALSE] <- "Boys"
  
  level_order <- c("Girls", "Boys")
  present <- intersect(level_order, unique(cd$.__grp))
  
  one_row <- function(row_name, col_name, custom_pred = NULL) {
    # If it is a section header, return empty values
    if (is.na(col_name) || !nzchar(col_name)) {
      return(tibble::tibble(
        characteristic = row_name,
        all_children = "",
        girls = "",
        boys = "",
        p_value = ""
      ))
    }
    
    # If the column does not exist in cd, return Not available
    if (!col_name %in% names(cd)) {
      return(tibble::tibble(
        characteristic = row_name,
        all_children = "Not available",
        girls = "",
        boys = "",
        p_value = ""
      ))
    }
    
    x <- cd[[col_name]]
    x_num <- safe_num(x)
    
    # Check if continuous or categorical
    if (col_name %in% continuous_vars) {
      is_cont <- TRUE
      pred <- NULL
    } else if (!is.null(custom_pred)) {
      pred <- custom_pred
      is_cont <- FALSE
    } else {
      pred <- is_yes
      is_cont <- sum(is.finite(x_num)) >= 20 && length(unique(x_num[is.finite(x_num)])) >= 8
    }
    
    all_val <- if (is_cont) fmt_mean_sd(x_num) else fmt_n_pct(x, pred)
    
    group_vals <- sapply(level_order, function(g) {
      if (!g %in% present) return("NA")
      idx <- cd$.__grp == g
      if (is_cont) fmt_mean_sd(x_num[idx]) else fmt_n_pct(x[idx], pred)
    }, simplify = TRUE)
    
    p <- NA_real_
    idx_nonmiss_grp <- !is.na(cd$.__grp) & cd$.__grp != "<missing>"
    if (is_cont) {
      dat <- data.frame(y = x_num[idx_nonmiss_grp], g = cd$.__grp[idx_nonmiss_grp])
      dat <- dat[is.finite(dat$y), , drop = FALSE]
      if (length(unique(dat$g)) >= 2 && nrow(dat) >= 5) {
        # Two-sample Wilcoxon/Kruskal test
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
      all_children = all_val,
      girls = group_vals[["Girls"]],
      boys = group_vals[["Boys"]],
      p_value = safe_p(p)
    )
  }
  
  col_name_vec <- if (v == 4) neurocognition_var_map$col4 else if (v == 5) neurocognition_var_map$col5 else neurocognition_var_map$col6
  
  rows_list <- lapply(seq_len(nrow(neurocognition_var_map)), function(i) {
    one_row(neurocognition_var_map$row[[i]], col_name_vec[[i]], custom_pred = neurocognition_predicates[[i]])
  })
  
  bind_rows(rows_list)
}

# Generate summaries for visits 4, 5, 6
neurocognition_v4 <- summarize_neurocognition_visit(4)
neurocognition_v5 <- summarize_neurocognition_visit(5)
neurocognition_v6 <- summarize_neurocognition_visit(6)

# Write to CSV files in data/
dir.create("data", recursive = TRUE, showWarnings = FALSE)
write_csv(neurocognition_v4, "data/neurocognition_characteristics_visit4.csv")
write_csv(neurocognition_v5, "data/neurocognition_characteristics_visit5.csv")
write_csv(neurocognition_v6, "data/neurocognition_characteristics_visit6.csv")

message("Wrote characteristics CSV outputs for Neurocognition visits 4, 5, and 6.")
