#load the functions needed
source("funct.R")

tse <- readRDS("../data/tse.Rds")

altExp_names <- c("phylum_prevalent", "family_prevalent", 
                  "genus_prevalent", "species_prevalent")
base_exp <- "phylum_prevalent"
other_exps <- setdiff(altExp_names, base_exp)

tse_combined <- combine_delta_altExps(tse, base_exp, other_exps)

saveRDS(tse_combined, file="../data/tse_delta.Rds")