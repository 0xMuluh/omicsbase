# Load libraries
library(quarto)
library(fs)
library(Cairo)

# Create the data
# OK to run just once
# source("data.R") # Creates tse.rds

# Alpha diversity analysis
# Define indices for analysis
indices <- c("shannon", "observed")

# Loop through each index
lapply(indices, function(index) {
    orig_dir <- dirname("alpha/alpha.qmd")
    temp_qmd <- file.path(orig_dir, paste0("alpha_", index, ".qmd"))
    file.copy("alpha/alpha.qmd", temp_qmd)
    
    quarto::quarto_render(
        input = temp_qmd,
        execute_params = list(index = index)
    )
    file.remove(temp_qmd)
})

# Render MaAsLin3 models first so that their output TSV files exist for downstream Wilcoxon reports
quarto::quarto_render(input = "daa/daa_maaslin_species.qmd")
quarto::quarto_render(input = "daa/daa_maaslin_genus.qmd")
quarto::quarto_render(input = "daa/daa_pa.qmd")

# Render Wilcoxon taxonomic levels
taxa.levels <- c("species_prevalent", "genus_prevalent")
lapply(taxa.levels, function(tax.level) {
    orig_dir <- dirname("daa/daa_level.qmd")
    temp_qmd <- file.path(orig_dir, paste0("daa_", tax.level, "_wilcoxon", ".qmd"))
    file.copy("daa/daa_level.qmd", temp_qmd)
    
    quarto::quarto_render(
        input = temp_qmd,
        execute_params = list(tax.level = tax.level)
    )
    file.remove(temp_qmd)
})

# Render Wilcoxon pathways
quarto::quarto_render(input = "daa/daa_pa_wilcox.qmd")

# Render LimROTS reports
quarto::quarto_render(input = "daa/daa_limrots_species.qmd")
quarto::quarto_render(input = "daa/daa_limrots_genus.qmd")
quarto::quarto_render(input = "daa/daa_limrots_pa.qmd")

# Render DAA Methods Comparison report (depends on MaAsLin3 and LimROTS output files)
quarto::quarto_render(input = "daa/daa_comparison.qmd")

# Render overall website
quarto::quarto_render()
