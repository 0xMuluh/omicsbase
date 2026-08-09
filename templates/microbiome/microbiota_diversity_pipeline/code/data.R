#load the functions needed
source("funct.R")

# Import metaphlan abundance table as TreeSE object
metaphlan.file <- "../data/metaphlan/latest_metaphlan_db_meta4_combined_reports.txt" 
# Specify file path
# Import the file as TreeSE
tse <- importMetaPhlAn(metaphlan.file,
                       removeTaxaPrefixes=TRUE
) 
# Simplify the tse colnames and bring them to same format as in sample metadata
colnames(tse) <- gsub("(_.*|\\..*)", "", colnames(tse))

# Import sample metadata
samdf <-  read_excel("../data/metaphlan/latest_metadata.xlsx", sheet = 1, col_names = TRUE)

# Convert to a data frame if necessary
samdf <- as.data.frame(samdf)
rownames(samdf) <- samdf$sample
# Group works better as factor 
samdf$gender <- factor(samdf$gender)
samdf$visit <- factor(samdf$visit)
samdf$diet_in <- factor(samdf$diet)
samdf$meal_group <- factor(samdf$meal_group, levels = c("1", "2", "3", "4")) 

# "Meal": intervention Experiment (some hours)
# Half of each diet group received either meal, yielding 4 "diet-meal" groups:
# oats-oats, oats-rice, rice-oats ja rice-rice

samdf <- samdf %>%
  assign_timepoint() %>%
  assign_paired() %>%
  assign_time() %>%
  assign_meal()  %>%
  assign_diet() %>%
  assign_duration() 

samdf$intervention <- factor(word(samdf$meal, 2, sep="-"), levels=c("rice", "oat"))

samdf$timepoint <- factor(samdf$timepoint, levels = c("before", "after"))
samdf$duration <- factor(samdf$duration, levels = c("Baseline", "Week 6"))
# Check that the sample data and assay data match by sample names
if (!all(rownames(samdf)==colnames(tse))) {stop("Check sample ID matching")}

metadata_df <- read_excel("../data/ext/correlation_data.xlsx", sheet = "Sheet1")

# Pivot only time columns (T0_, T6_, etc.)
metadata_long <- metadata_df %>%
  pivot_longer(
    cols = starts_with("T"),
    names_to = "key",
    values_to = "value"
  ) %>%
  mutate(
    time = case_when(
      str_starts(key, "T0_") ~ 1,
      str_starts(key, "T6_") ~ 2,
      TRUE ~ NA_real_
    ),
    variable = str_remove(key, "^T[0-9]+_")
  ) %>%
  select(id, diet, time, variable, value)

metadata_wide <- metadata_long %>%
  pivot_wider(names_from = variable, values_from = value)

# Join with df2
sam_metadata <- samdf %>%
  dplyr::left_join(metadata_wide, by = c("id", "time", "diet"))

rownames(sam_metadata) <- sam_metadata$sample

# Add sample metadata to the TreeSE as colData 
colData(tse) <- DataFrame(sam_metadata[colnames(tse),])

#the metaphlan results is essentially relative abundance, so "counts"="relabundance"
#check with colSums(assay(tse, "counts"))
colSums(assay(tse, "metaphlan"))
# removing plasmids
tse <- tse[grep("plasmid", rowData(tse)[,"kingdom"],
                ignore.case = TRUE, invert = TRUE),]

tse <- transformAssay(tse, assay.type= "metaphlan", method = "relabundance")
# Add alpha diversity
# Check addAlpha--not working for different assay
tse <- addAlpha(tse, assay.type = 'metaphlan',
                index = c('observed', 'shannon'))

assays(tse) <- assays(tse)[-which(names(assays(tse)) == "metaphlan")]

# make primary comparison into tse
# Create a new column for group in colData of the TreeSummarizedExperiment object
# Initialize the group column with NA values directly in tse
tse$group <- NA  

# Assign group labels directly within tse
for (comp in comparisons) {
  # Extract diet and visit information from the comparisons
  group1_info <- extract_diet_visit(comp[1])
  group2_info <- extract_diet_visit(comp[2])
  
  # Assign group labels directly in tse object based on diet and visit
  tse$group[colData(tse)$diet_in == group1_info$diet_in & colData(tse)$visit == group1_info$visit] <- comp[1]
  tse$group[colData(tse)$diet_in == group2_info$diet_in & colData(tse)$visit == group2_info$visit] <- comp[2]
}

# There are some duplicates Id in diet group which prevent the code from 
# performing paired test:
# to remove, and keep first occurrence
# Function to remove duplicates within specific group categories
# Use the function to remove duplicates across both diet groups
# tse <- remove_duplicates(tse)

tse <- agglomerateByRanks(tse)

# Loop starts:
for (alt_name in altExpNames(tse)) {
  prevalent_tse <- agglomerateByPrevalence(
    altExp(tse, alt_name),
    assay.type = "relabundance",
    detection = 0.1 / 100,
    prevalence = 10 / 100
  )
  
  # Adding back with "_prevalent"
  altExp(tse, paste0(alt_name, "_prevalent")) <- prevalent_tse
}

# Changes old levels with new levels
tse$group <- factor(tse$group)

# Add functional predictions to tse
# Read functional prediction data
file_paths <- list(
  pathabundance = "../data/HUMAnN3/processed/pathabundance_unstratified.txt",
  # pathcoverage = "../data/HUMAnN3/processed/pathcoverage_unstratified.txt",
  # KO = "../data/HUMAnN3/final/Renorm_genefamilies_Uniref90_KO_unstratified.txt",
  metacyc = "../data/HUMAnN3/final/Renorm_genefamilies_Uniref90_MetaCyc_unstratified.txt"
)


columns_to_remove <- c("AK1304", "PP2368", "HK2340", "PP1368", "HK1340")

# Function to process each file
process_file <- function(file_path, tse_colnames, feature_name) {
  data <- read.csv(file_path, header = TRUE, row.names = 1, sep = "\t", check.names = FALSE, stringsAsFactors = FALSE)
  columns_to_keep <- !grepl(paste(columns_to_remove, collapse = "|"), colnames(data))
  abundance_matrix <- data[, columns_to_keep]
  colnames(abundance_matrix) <- tse_colnames
  
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

# Save TreeSE object for later use
saveRDS(tse, file="../data/tse.Rds")
