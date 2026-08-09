# Load necessary library
library(data.table)

# Path to the file
file_path <- "../data/metaphlan/metaphlan_db_meta4_combined_reports.txt"

# Load the data
data <- fread(file_path, sep = "\t", header = TRUE)

# Columns to remove
columns_to_remove <- c("AK1304", "PP2368", "HK2340", "PP1368", "HK1340")

# Remove specified columns if they exist in the DataFrame
data <- data[, !(names(data) %in% columns_to_remove), with = FALSE]

# Order columns starting from the second one
# Keep first column as-is, then sort remaining columns alphabetically
new_order <- c(1, order(names(data)[-1]) + 1)
data <- data[, ..new_order]

# Save the modified data to the same directory
output_path <- "../data/metaphlan/latest_metaphlan_db_meta4_combined_reports.txt"
fwrite(data, file = output_path, sep = "\t", quote = FALSE, eol = "\n")

cat("Columns removed and modified file saved to", output_path, "\n")
