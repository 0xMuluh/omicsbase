library(cardx)
library(dplyr)
library(emmeans)
library(DT)
library(ggplot2)
library(ggpubr)
library(ggsignif)
library(gtsummary)
library(ggrepel)
library(ggtext)
library(grid)
library(gridExtra)
library(mia)
library(miaViz)
library(miaTime)
library(multtest)
library(parameters)
library(patchwork)
library(quarto)
library(scater)
library(sechm)
library(stringdist)
library(tidyr)
library(TreeSummarizedExperiment)
library(tidyverse)
library(reshape2)
library(vegan)
library(kableExtra)

# Define variables
taxa     <- c("genus","species")
variable <- "group"
outdir ="./output/"

# # Define the list of comparisons
comparisons_before <- list(
  # Between-Group Comparisons at Baseline (before treatment)
  c("4.before", "B.before"),
  c("4.before", "C.before"),
  c("B.before", "C.before")  
)

comparisons_after <- list(
  # Between-Group Comparisons after treatment
  c("4.after", "B.after"),
  c("4.after", "C.after"),
  c("B.after", "C.after")  
)

comparisons_paired <- list(
  # Between-Group Comparisons after treatment
  c("4.before", "4.after"),
  c("B.before", "B.after"),
  c("C.before", "C.after")  
)

assign_group <- function(df) {
  # Create a new column 'Group' based on Treatment values and sample_ID suffix
  df$group <- with(df, case_when(
    Treatment == "4" & endsWith(Sample_ID, "1") ~ "4.before",
    Treatment == "4" & endsWith(Sample_ID, "2") ~ "4.after",
    Treatment == "B" & endsWith(Sample_ID, "1") ~ "B.before",
    Treatment == "B" & endsWith(Sample_ID, "2") ~ "B.after",
    Treatment == "C" & endsWith(Sample_ID, "1") ~ "C.before",
    Treatment == "C" & endsWith(Sample_ID, "2") ~ "C.after",
    is.na(Treatment) ~ "Missing",
    TRUE ~ "NA"
  ))
  return(df)
}

assign_timepoint <- function(df) {
  df$timepoint <- with(df, case_when(
    endsWith(Sample_ID, "1") ~ "before",
    endsWith(Sample_ID, "2") ~ "after",
    is.na(Treatment) ~ "Missing",
    TRUE ~ "NA"
  ))
  return(df)
}

assign_subject <- function(df) {
  df$subject <- substr(df$Sample_ID, 1, 3)
  return(df)
}

assign_paired <- function(df) {
  df$paired <- ifelse(duplicated(df$subject) | duplicated(df$subject, fromLast = TRUE), 
                      "yes", "no")
  return(df)
}

assign_time <- function(df) {
  df$time <- ifelse(df$timepoint == "before", "Baseline", 
                    ifelse(df$timepoint == "after", "Week 8", NA))
  return(df)
}

assign_intervention <- function(df) {
  df$Intervention <- ifelse(df$Treatment == "4", "Control", 
                    ifelse(df$Treatment == "B", "HWP", "HWP + LGG"))
  return(df)
}

run_lmer <- function(tse, target){
  
  df <- colData(tse) %>% as.data.frame
  df$y <- df[[target]]
  df <- df[, c("y", "Intervention", "time", "subject")]
  
  # Filter out missing data cases
  df_no_miss <- df[df %>% complete.cases,]
  
  m <- lmerTest::lmer(y ~ Intervention * time + (1 | subject), data = df_no_miss)
  return(m)
}

generate_prevalence_label <- function(df_prevalence, feature_id) {
  df_prevalence %>%
    filter(FeatureID == feature_id) %>%
    transmute(
      before_4 = paste0("Control_Baseline: N=", `4_Baseline_nonzero_n`, " (", `4_Baseline_pct_nonzero`, "%)"),
      after_4  = paste0("Control_Week 8: N=", `4_Week 8_nonzero_n`,  " (", `4_Week 8_pct_nonzero`, "%)"),
      before_B  = paste0("HWP_Baseline: N=", B_Baseline_nonzero_n,  " (", B_Baseline_pct_nonzero, "%)"),
      after_B   = paste0("HWP_Week 8: N=", `B_Week 8_nonzero_n`,   " (", `B_Week 8_pct_nonzero`, "%)"),
      before_C  = paste0("HWP + LGG_Baseline: N=", C_Baseline_nonzero_n,  " (", C_Baseline_pct_nonzero, "%)"),
      after_C   = paste0("HWP + LGG_Week 8: N=", `C_Week 8_nonzero_n`,   " (", `C_Week 8_pct_nonzero`, "%)")
    ) %>%
    unite("label", everything(), sep = " | ") %>%
    pull(label)
}

# Centralized pair-aware diversity and ratio group comparisons
run_diversity_tests <- function(tse, comparisons, 
                                variable, index, 
                                adjust.method = "fdr", paired = FALSE) {
  # Get all values and groups
  values <- colData(tse)[[index]]
  groups <- colData(tse)[[variable]]
  
  # Create an empty results dataframe
  results <- data.frame(
    group1 = character(),
    group2 = character(),
    mean1 = numeric(),
    mean2 = numeric(),
    logFC = numeric(),
    p.value = numeric(),
    stringsAsFactors = FALSE
  )
  
  # Loop through requested comparisons
  for (comp in comparisons) {
    group1 <- comp[1]
    group2 <- comp[2]
    
    # Subset data for just these two groups
    subset_indices <- which(groups %in% c(group1, group2))
    subset_values <- values[subset_indices]
    subset_groups <- groups[subset_indices]
    
    # Ensure paired data is properly aligned (if paired is TRUE)
    if (paired) {
      # Assuming 'subject' column exists for pairing
      subset_subjects <- tse$subject[subset_indices]
      paired_data <- data.frame(
        subject = subset_subjects,
        group = subset_groups,
        value = subset_values
      ) %>%
        tidyr::pivot_wider(names_from = group, values_from = value) %>%
        drop_na()  # Drop pairs with missing data
      
      # Extract paired values
      paired_values <- paired_data %>% select(all_of(c(group1, group2)))
    }
    
    # Perform Wilcoxon test
    if (paired) {
      test_result <- wilcox.test(
        paired_values[[1]], paired_values[[2]],
        paired = TRUE, exact = FALSE
      )
    } else {
      test_result <- wilcox.test(
        subset_values ~ subset_groups,
        exact = FALSE
      )
    }
    
    # Calculate means and fold change
    if (paired) {
      mean1 <- mean(paired_values[[1]], na.rm = TRUE)
      mean2 <- mean(paired_values[[2]], na.rm = TRUE)
      FC <- mean(paired_values[[2]] / paired_values[[1]], na.rm = TRUE)
    } else {
      mean1 <- mean(subset_values[subset_groups == group1], na.rm = TRUE)
      mean2 <- mean(subset_values[subset_groups == group2], na.rm = TRUE)
      FC <- mean2/mean1
    }
    
    # Add results
    results <- rbind(results, data.frame(
      group1 = group1,
      group2 = group2,
      mean1 = mean1,
      mean2 = mean2,
      logFC = log2(FC),
      p.value = test_result$p.value,
      stringsAsFactors = FALSE
    ))
  }
  
  return(results)
}

# Run LimROTS differential abundance analysis
run_limrots_analysis <- function(tse, assay_name, group_col, formula_str, niter = 200, verbose = FALSE) {
  library(LimROTS)
  library(SummarizedExperiment)
  
  # Extract the alternative experiment
  se <- altExp(tse, assay_name)
  
  # Ensure CLR transform is present for log-transformation requirement
  if (!"clr" %in% assayNames(se)) {
    se <- mia::transformAssay(se, assay.type = "relabundance", method = "clr", pseudocount = 1)
  }
  
  # Extract the CLR assay
  clr_matrix <- assay(se, "clr")
  
  # Build a clean SummarizedExperiment object
  se_clean <- SummarizedExperiment(
    assays = list(exprs = clr_matrix),
    colData = as.data.frame(colData(se))
  )
  
  # Extract only columns involved in formula_str and group_col
  vars <- all.vars(as.formula(formula_str))
  required_cols <- unique(c(vars, group_col))
  
  # Ensure group_col is a factor
  se_clean[[group_col]] <- factor(se_clean[[group_col]])
  
  # Run LimROTS
  res <- LimROTS(
    x = se_clean,
    group.name = group_col,
    meta.info = required_cols,
    formula.str = formula_str,
    niter = niter,
    verbose = verbose
  )
  
  # Extract results into a clean dataframe
  results <- data.frame(
    feature = rownames(res),
    logFC = rowData(res)$logfc,
    p.value = rowData(res)$pvalue,
    FDR = rowData(res)$FDR,
    qvalue = rowData(res)$qvalue,
    BH.pvalue = rowData(res)$BH.pvalue,
    stringsAsFactors = FALSE
  )
  
  return(results)
}

# Fit 3 hierarchical linear models for biomarker-species associations
# Q1: Overall association (unadjusted for treatment)
# Q2: Association adjusted for treatment group
# Q3: Treatment × species change interaction
#
# Returns: tibble with one row per model (3 rows), including raw and
#          standardized beta for sp_delta, plus interaction F-test p for Q3.
#
# dat must contain columns: y_delta, y_base, sp_delta, sp_base, Treatment
fit_biomarker_species_models <- function(dat, species_name, biomarker_name) {

  formulas <- list(
    Q1 = y_delta ~ sp_delta + y_base + sp_base,
    Q2 = y_delta ~ sp_delta + y_base + sp_base + Treatment,
    Q3 = y_delta ~ sp_delta * Treatment + y_base + sp_base
  )

  dat$Treatment <- factor(dat$Treatment)
  n <- nrow(dat)

  out <- list()

  for (model_name in names(formulas)) {
    f <- formulas[[model_name]]

    fit_raw <- tryCatch(lm(f, data = dat), error = function(e) NULL)

    # Standardize continuous variables for standardized coefficients
    dat_s <- dat
    dat_s$y_delta  <- as.numeric(scale(dat$y_delta))
    dat_s$y_base   <- as.numeric(scale(dat$y_base))
    dat_s$sp_delta <- as.numeric(scale(dat$sp_delta))
    dat_s$sp_base  <- as.numeric(scale(dat$sp_base))

    fit_std <- tryCatch(lm(f, data = dat_s), error = function(e) NULL)

    if (is.null(fit_raw) || is.null(fit_std)) next

    s_raw <- summary(fit_raw)$coefficients
    s_std <- summary(fit_std)$coefficients

    if (!"sp_delta" %in% rownames(s_raw)) next

    ci_raw <- tryCatch(confint(fit_raw, parm = "sp_delta"), error = function(e) c(NA, NA))
    ci_std <- tryCatch(confint(fit_std, parm = "sp_delta"), error = function(e) c(NA, NA))

    # For Q3: overall interaction F-test via anova comparing Q2 vs Q3
    interaction_p <- NA_real_
    if (model_name == "Q3") {
      fit_q2 <- tryCatch(lm(formulas$Q2, data = dat), error = function(e) NULL)
      if (!is.null(fit_q2)) {
        interaction_p <- tryCatch({
          av <- anova(fit_q2, fit_raw)
          av$`Pr(>F)`[2]
        }, error = function(e) NA_real_)
      }
    }

    out[[model_name]] <- tibble::tibble(
      model         = model_name,
      species       = species_name,
      biomarker     = biomarker_name,
      n_samples     = n,
      beta_raw      = unname(s_raw["sp_delta", "Estimate"]),
      se_raw        = unname(s_raw["sp_delta", "Std. Error"]),
      ci_low_raw    = unname(ci_raw[1]),
      ci_high_raw   = unname(ci_raw[2]),
      beta_std      = unname(s_std["sp_delta", "Estimate"]),
      se_std        = unname(s_std["sp_delta", "Std. Error"]),
      ci_low_std    = unname(ci_std[1]),
      ci_high_std   = unname(ci_std[2]),
      p_value       = unname(s_raw["sp_delta", "Pr(>|t|)"]),
      interaction_p = interaction_p
    )
  }

  dplyr::bind_rows(out)
}