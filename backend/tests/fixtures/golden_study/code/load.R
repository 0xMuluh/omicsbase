study <- read.csv("../data/study.csv", stringsAsFactors = FALSE)
stopifnot(nrow(study) == 4L)
saveRDS(study, "../output/study.rds")
