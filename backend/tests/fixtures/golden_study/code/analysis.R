study <- readRDS("../output/study.rds")
summary <- aggregate(value ~ group, data = study, FUN = mean)
summary <- summary[order(summary$group), ]
write.csv(summary, "../output/results.csv", row.names = FALSE)
writeLines(sprintf("control_mean=%.1f\ntreatment_mean=%.1f\n", summary$value[summary$group == "control"], summary$value[summary$group == "treatment"]), "../output/summary.txt")
