library(fs)
library(stringr)
library(readxl)

#load the functions needed
source("funct.R")

# Import metaphlan abundance table as TreeSE object
metaphlan.file <- "../data/metaphlan/metaphlan_merged_abundance_table.txt" 
# Specify file path
# Import the file as TreeSE
tse <- importMetaPhlAn(metaphlan.file,
                       removeTaxaPrefixes=TRUE
) 
# Simplify the tse colnames and bring them to same format as in sample metadata
colnames(tse) <- gsub("(_.*|\\..*)", "", colnames(tse))

# Import sample metadata
samdf <-  read_excel("../data/metaphlan/Metadata_04.12.24.xlsx", 
                     sheet = 1, col_names = TRUE)

# Convert to a data frame if necessary
samdf <- as.data.frame(samdf)
rownames(samdf) <- samdf$Sample_ID
# Group works better as factor
samdf$Treatment <- factor(samdf$Treatment)
samdf$BMI <- as.numeric(samdf$BMI)

# Assign group labels directly within metadata
samdf <- samdf %>%
  assign_group() %>%
  assign_timepoint() %>%
  assign_time() %>%
  assign_subject() %>%
  assign_paired() %>%
  assign_intervention()

samdf$timepoint <- factor(samdf$timepoint, levels = c("before", "after"))
samdf$Intervention <- factor(samdf$Intervention, levels = c("Control", "HWP", "HWP + LGG"))
# samdf$time <- factor(samdf$time, levels = c("1", "2"))
samdf$group <- factor(samdf$group, 
                      levels = c("4.before", "4.after", "B.before", "B.after", "C.before", "C.after"))
# Backup original tse.Rds before saving updates
if (file.exists("../data/tse.Rds") && !file.exists("../data/tse_backup.Rds")) {
  file.copy("../data/tse.Rds", "../data/tse_backup.Rds")
}

# Merge inflammatory markers into samdf
infl_path <- "../data/DATA VAR-Mikro_only infl markers_no antibiotics use.xlsx"
if (file.exists(infl_path)) {
  infl_df <- readxl::read_excel(infl_path, sheet = "Data inflammation markers")
  infl_df <- as.data.frame(infl_df)
  infl_df$Subject_ID <- as.character(as.integer(infl_df$ID))
  
  # Standardize column names
  colnames(infl_df)[colnames(infl_df) == "Supar1 (ng/ml)"]  <- "Supar1"
  colnames(infl_df)[colnames(infl_df) == "Supar3 (ng/ml)"]  <- "Supar3"
  colnames(infl_df)[colnames(infl_df) == "Calpro1 (ug/ml)"] <- "Calpro1"
  colnames(infl_df)[colnames(infl_df) == "Calpro3 (ug/ml)"] <- "Calpro3"
  colnames(infl_df)[colnames(infl_df) == "hs-CRP1 (mg/l)"]  <- "hsCRP1"
  # Note: hs-CRP2 represents follow-up at week 8. We rename it to hsCRP3 to keep 
  # naming consistent with other biomarkers (e.g. Supar3, Calpro3, Hapto3) representing timepoint 3.
  colnames(infl_df)[colnames(infl_df) == "hs-CRP2 (mg/l)"]  <- "hsCRP3"
  colnames(infl_df)[colnames(infl_df) == "hs-CRP_delta"]   <- "hsCRP_delta"
  colnames(infl_df)[colnames(infl_df) == "Hapto1 (g/l)"]   <- "Hapto1"
  colnames(infl_df)[colnames(infl_df) == "Hapto3 (g/l)"]   <- "Hapto3"
  
  # Compute log10 transformations for skewed markers
  infl_df$log10_Supar1       <- log10(infl_df$Supar1)
  infl_df$log10_Supar3       <- log10(infl_df$Supar3)
  infl_df$log10_Supar_delta <- infl_df$log10_Supar3 - infl_df$log10_Supar1
  
  infl_df$log10_Calpro1       <- log10(infl_df$Calpro1)
  infl_df$log10_Calpro3       <- log10(infl_df$Calpro3)
  infl_df$log10_Calpro_delta <- infl_df$log10_Calpro3 - infl_df$log10_Calpro1
  
  infl_df$log10_hsCRP1       <- log10(infl_df$hsCRP1)
  infl_df$log10_hsCRP3       <- log10(infl_df$hsCRP3)
  infl_df$log10_hsCRP_delta <- infl_df$log10_hsCRP3 - infl_df$log10_hsCRP1
  
  infl_df$log10_Hapto1       <- log10(infl_df$Hapto1)
  infl_df$log10_Hapto3       <- log10(infl_df$Hapto3)
  infl_df$log10_Hapto_delta <- infl_df$log10_Hapto3 - infl_df$log10_Hapto1
  
  samdf$Subject_ID <- gsub("-.*", "", samdf$Sample_ID)
  samdf <- dplyr::left_join(samdf, infl_df %>% dplyr::select(-dplyr::any_of(c("Treatment"))), by = "Subject_ID")
  rownames(samdf) <- samdf$Sample_ID
}

# Coerce metadata columns to flat R vectors to avoid Bioconductor list-column errors
samdf[] <- lapply(samdf, function(x) {
  if (is.list(x) || inherits(x, "List")) as.vector(unlist(x)) else x
})

# Check that the sample data and assay data match by sample names
if (!all(rownames(samdf)==colnames(tse))) {stop("Check sample ID matching")}

# Add sample metadata to the TreeSE as colData 
colData(tse) <- DataFrame(samdf[colnames(tse),])

# The metaphlan results is essentially relative abundance, 
# so "counts"="relabundance"
# Check with colSums(assay(tse, "counts"))
colSums(assay(tse, "metaphlan"))

# Yeah but transform assay just to be super sure
tse <- transformAssay(tse, assay.type = "metaphlan", method = "relabundance")
# Add alpha diversity
tse <- addAlpha(x = tse, assay.type = 'metaphlan',
                index = c('observed', 'shannon')) 
assays(tse) <- assays(tse)[-which(names(assays(tse)) == "metaphlan")]
# removing plasmids
tse <- tse[grep("plasmid", rowData(tse)[,"kingdom"],
                ignore.case = TRUE, invert = TRUE),]

tse <- agglomerateByRanks(tse)

# Changes old levels with new levels
tse$group <- factor(tse$group)

# Loop starts:
for (alt_name in altExpNames(tse)) {
  prevalent_tse <- agglomerateByPrevalence(
    altExp(tse, alt_name),
    assay.type = "relabundance",
    detection = 0.1 / 100,
    prevalence = 0.1
  )
  
  # Adding back with "_prevalent"
  altExp(tse, paste0(alt_name, "_prevalent")) <- prevalent_tse
}
# Define the list of samples to exclude

excluded_samples <- c(
  "114-1", "122-1", "123-1", "137-1", "148-1", "149-1", "150-1", "151-1",
  "154-1", "167-1", "170-1", "171-1", "177-1", "179-1", "184-1", "187-1",
  "190-1", "195-1", "206-1", "214-1", "217-1", "218-1", "221-1", "222-1",
  "232-1", "233-1", "251-1", "252-1", "254-1", "255-1", "256-1", "166-2",
  "173-2", "222-2", "253-2"
)

# Add the excluded samples to the metadata
metadata(tse)$excluded_samples <- excluded_samples

# Exclude samples with missing data
tse <- tse[, !colnames(tse) %in% metadata(tse)$excluded_samples]

# Keep only fully paired subjects (ensure longitudinal pairing)
paired_subjects <- names(which(table(tse$subject) == 2))
tse <- tse[, tse$subject %in% paired_subjects]

# Add functional predictions to tse
# Read functional prediction data
file_paths <- list(
  pathabundance = "../data/HUMAnN3/final/pathabundance_unstratified.txt",
  # pathcoverage = "../data/HUMAnN3/processed/pathcoverage_unstratified.txt",
  # KO = "../data/HUMAnN3/final/Renorm_genefamilies_Uniref90_KO_unstratified.txt",
  metacyc = "../data/HUMAnN3/final/Renorm_genefamilies_Uniref90_MetaCyc_unstratified.txt"
)

# Function to process each file
process_file <- function(file_path, tse_colnames, feature_name) {
  data <- read.csv(file_path, header = TRUE, row.names = 1, sep = "\t", check.names = FALSE, stringsAsFactors = FALSE)
  
  # Standardize column names to match tse sample IDs (remove _pathabundance, etc.)
  colnames(data) <- gsub("(_.*|\\..*)", "", colnames(data))
  
  # Intersect and align column names
  common_columns <- intersect(tse_colnames, colnames(data))
  abundance_matrix <- data[, common_columns, drop = FALSE]
  
  # Reorder to match tse_colnames exactly
  abundance_matrix <- abundance_matrix[, tse_colnames, drop = FALSE]
  
  SummarizedExperiment(
    assays = list(counts = abundance_matrix),
    rowData = DataFrame(Feature = rownames(abundance_matrix)),
    colData = colData(tse)
  )
}

# Add functional predictions to tse
for (name in names(file_paths)) {
  altExp(tse, name) <- process_file(file_paths[[name]], colnames(tse), name)
  altExp(tse, name) <- transformAssay(altExp(tse, name), method = "relabundance")
}


# Print the group assignments
print(table(tse$group))

# Backup original tse.Rds before saving updates
if (file.exists("../data/tse.Rds") && !file.exists("../data/tse_backup.Rds")) {
  file.copy("../data/tse.Rds", "../data/tse_backup.Rds")
}

# Read target species list and store in metadata(tse)
infl_path <- "../data/DATA VAR-Mikro_only infl markers_no antibiotics use.xlsx"
if (file.exists(infl_path)) {
  species_df <- readxl::read_excel(infl_path, sheet = "Bacteria species")
  species_list <- c(names(species_df)[1], species_df[[1]])
  species_list <- species_list[!is.na(species_list) & species_list != ""]
  metadata(tse)$target_species <- species_list
}

# Save TreeSE object for later use
saveRDS(tse, file="../data/tse.Rds")
