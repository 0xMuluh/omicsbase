library(cardx)
library(dplyr)
library(DT)
library(emmeans)
library(ggplot2)
library(ggpubr)
library(ggsignif)
library(ggrepel)
library(gtsummary)
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
library(lmerTest)
library(kableExtra)
library(stringr)
library(readxl)

# Define variables
taxa     <- c("genus","species")
variable <- "diet"
outdir ="./output/"

# Define the list of comparisons
comparisons <- list(
  c("diet_1_visit_1", "diet_1_visit_2"),
  c("diet_2_visit_1", "diet_2_visit_2"),
  c("diet_1_visit_1", "diet_2_visit_1"),
  c("diet_1_visit_2", "diet_2_visit_2")
)

# # Define the list of comparisons
comparisons_before <- list(
  # Between-Group Comparisons at Baseline (before treatment)
  c("Oat", "Rice")
)

comparisons_after <- list(
  # Between-Group Comparisons after treatment
  c("Oat", "Rice")
)

comparisons_paired <- list(
  # Within-Group Comparisons after treatment
  c("Baseline", "Week 6")
)

# Define function to extract diet and visit info
extract_diet_visit <- function(comparison) {
  condition <- unlist(strsplit(comparison, "_"))
  list(diet_in = condition[2], visit = condition[4])
}

assign_timepoint <- function(df) {
  df$timepoint <- ifelse(df$visit == 1, "before", 
                    ifelse(df$visit == 2, "after", NA))
  return(df)
}

assign_duration <- function(df) {
  df$duration <- ifelse(df$visit == 1, "Baseline", 
                         ifelse(df$visit == 2, "Week 6", NA))
  return(df)
}

assign_paired <- function(df) {
  df$paired <- ifelse(duplicated(df$id) | duplicated(df$id, fromLast = TRUE), 
                      "yes", "no")
  return(df)
}

assign_time <- function(df) {
  df$time <- ifelse(df$timepoint == "before", 1, 
                    ifelse(df$timepoint == "after", 2, NA))
  return(df)
}

assign_diet <- function(df) {
  df$diet <- rep(NA, nrow(df))
  df$diet[df$diet_in == 1] <- "oat"
  df$diet[df$diet_in == 2] <- "rice"
  df$diet <- factor(df$diet, levels=c("rice", "oat"))
  return(df)
}

assign_meal <- function(df) {
  df$meal <- rep(NA, nrow(df))
  df$meal[df$meal_group == "1"] <- "oat-rice"
  df$meal[df$meal_group == "2"] <- "rice-oat"
  df$meal[df$meal_group == "3"] <- "rice-rice"
  df$meal[df$meal_group == "4"] <- "oat-oat"
  # meal is now combination diet-meal; extract just the meal
  df$meal <- factor(df$meal, levels=c("rice-oat", "rice-rice", "oat-rice", "oat-oat"))
  # df$meal <- factor(word(df$meal, 2, sep="-"), levels=c("rice", "oat"))
  return(df)
}

# The following code contain the list of functions that were needed to conduct 
# the 2 groups comparison
# based on primary group of interest

# COMPARISONS (PRIMARY)
#  •	diet 1, visit 1	vs. 	diet 1, visit 2
#  •	diet 2, visit 1	vs. 	diet 2, visit 2
# Did the diet change the microbiota?
#  •	diet 1, visit 1	vs.	diet 2, visit 1
# Was the microbiota equal between the diet groups before treatment?
#  •	diet 1, visit 2	vs.	diet 2, visit 2
# Was there a difference in microbiota between the diet groups after the 
# treatment?

# There are some duplicates Id in diet group which prevent the code from 
# performing paired test:
# to remove, and keep first occurrence
# Function to remove duplicates within specific group categories
remove_duplicates <- function(tse) {
  # Extract colData as a data frame for easier manipulation
  df <- as.data.frame(colData(tse))
  # Identify groups (assuming 'group' is the column name for 
  # group categorization)
  unique_groups <- unique(df$group)
  # Initialize a list to store indices of unique samples
  unique_indices <- c()
  # Loop through each group to handle duplicates
  for (group in unique_groups) {
    # Subset data for the current group
    group_data <- df[df$group == group, ]
    # Identify duplicates within the current group
    duplicates <- group_data[duplicated(group_data$id) | duplicated(group_data$id, 
                                                                    fromLast = TRUE), ]
    if (nrow(duplicates) > 0) {
      # Print duplicates found in the current group
      print(paste("Duplicate subjects found in group:", group))
      print(duplicates)
      # Select only the first occurrence of each duplicate within the current 
      # group
      unique_subset <- group_data[!duplicated(group_data$id), ]
      # Alternatively, if you want to select randomly:
      # unique_subset <- group_data %>% slice_sample(n = 1)
      # Store the row indices of unique samples
      unique_indices <- c(unique_indices, rownames(unique_subset))
    } else {
      # If no duplicates, keep all samples from the group
      unique_indices <- c(unique_indices, rownames(group_data))
    }
  }
  
  # Subset the original TSE object to keep only unique samples
  tse_unique <- tse[, unique_indices]
  return(tse_unique)
}

run_lmer <- function(tse, target){
  
  df <- colData(tse) %>% as.data.frame
  df$y <- df[[target]]
  df <- df[, c("y", "diet", "duration", "id")]
  
  # Filter out missing data cases
  df_no_miss <- df[df %>% complete.cases,]
  
  m <- lmerTest::lmer(y ~ diet * duration + (1 | id), data = df_no_miss)
  return(m)
}

generate_prevalence_label <- function(df_prevalence, feature_id) {
  df_prevalence %>%
    filter(FeatureID == feature_id) %>%
    transmute(
      before_oat  = paste0("Oat Baseline: n=", oat_Baseline_nonzero_n,  " (", oat_Baseline_pct_nonzero, "%)"),
      after_oat   = paste0("Oat Week 6: n=", `oat_Week 6_nonzero_n`,   " (", `oat_Week 6_pct_nonzero`, "%)"),
      before_rice = paste0("Rice Baseline: n=", rice_Baseline_nonzero_n, " (", rice_Baseline_pct_nonzero, "%)"),
      after_rice  = paste0("Rice Week 6: n=", `rice_Week 6_nonzero_n`,  " (", `rice_Week 6_pct_nonzero`, "%)")
    ) %>%
    unite("label", everything(), sep = " | ") %>%
    pull(label)
}

make_delta_tse <- function(tse, tse_name) {
  
  tse <- altExp(tse, tse_name)
  
  tse <- transformAssay(tse, assay.type = "relabundance", method = "clr", 
                        pseudocount = T)
  
  
  df <- mia::meltSE(tse, assay.type = "clr", add.col = T)
  
  df <- df %>% select(-c(sample, visit, meal_group, gender, age, diet_in, 
                         paired, meal, duration, intervention, timepoint))
  
  df_delta <- df %>%
    arrange(FeatureID, id, time) %>%
    group_by(FeatureID, id) %>%
    mutate(
      across(
        .cols = where(is.numeric) & !c(time),  # all numeric except time column
        .fns = ~ . - lag(.),
        .names = "{.col}_delta"
      )
    ) %>%
    ungroup()
  
  df_deltas_only <- df_delta %>% 
    filter(time != "1") %>%
    select(
      FeatureID,
      diet,
      id,
      ends_with("_delta")
    )
  
  assay_df <- df_deltas_only %>%
    select(FeatureID, id, clr_delta) %>%
    pivot_wider(
      id_cols = FeatureID,
      names_from = id,
      values_from = clr_delta
    )
  
  assay_mat <- assay_df %>%
    column_to_rownames("FeatureID") %>%
    as.matrix()
  
  # scfa_vars <- c("acetic_delta", "propionic_delta", "butyric_delta", 
  #                "isobutyr_delta", "succinic_delta",
  #                "valeric_delta", "isovaler_delta", "lactic_delta", 
  #                "indolelactic_delta", "indolebutyric_delta",
  #                "indolepropionic_delta", "heptanoic_delta")
  # 
  # inflamm_markers <- c("IL1B_delta", "MMP12_delta", 
  #                      "TNFSF12_delta", "EGF_delta")
  # 
  # diet_vars <- c("fat_delta", "carb_delta", "prot_delta", "fiber_delta", 
  #                "SFA_delta", "MUFA_delta", "PUFA_delta")
  # 
  # bio_vars <- c("waist_delta", "Kol_delta", "LDLkol_delta", 
  #               "HDLkol_delta", "Trigly_delta")
  
  # summary_df <- df_deltas_only %>%
  #   ungroup() %>%
  #   select(id, all_of(scfa_vars), all_of(inflamm_markers), 
  #          all_of(diet_vars), all_of(bio_vars)) %>%
  #   group_by(id) %>%
  #   summarise(
  #     scfa_sum = rowSums(across(all_of(scfa_vars)), na.rm = TRUE),
  #     inflamm_sum = rowSums(across(all_of(inflamm_markers)), na.rm = TRUE),
  #     diet_sum = rowSums(across(all_of(diet_vars)), na.rm = TRUE),
  #     bio_sum = rowSums(across(all_of(bio_vars)), na.rm = TRUE),
  #     .groups = "drop"
  #   ) %>%
  #   distinct(id, .keep_all = TRUE) %>%
  #   as.data.frame()
   
  col_data <- df_deltas_only %>%
    ungroup() %>%  # ⬅️ This removes any lingering groupings
    select(-FeatureID, -clr_delta) %>%
    distinct(id, .keep_all = TRUE) %>%
    as.data.frame()
  
  # col_data <- dplyr::left_join(col_data, summary_df, by = "id")
  rownames(col_data) <- col_data$id
  
  tse2 <- TreeSummarizedExperiment(
    assays = list(clr_delta = assay_mat),
    colData = col_data
  )
  
  rowData(tse2) <- rowData(tse)
  
  return(tse2)
  
}

combine_delta_altExps <- function(tse, base_exp, other_exps) {
  # Create the base delta TSE
  base_delta <- make_delta_tse(tse, base_exp)
  
  # For each other altExp, create delta TSE and add as altExp inside base_delta
  for (nm in other_exps) {
    message("Processing delta for altExp: ", nm)
    delta_tse <- make_delta_tse(tse, nm)
    altExp(base_delta, nm) <- delta_tse
  }
  
  return(base_delta)
}