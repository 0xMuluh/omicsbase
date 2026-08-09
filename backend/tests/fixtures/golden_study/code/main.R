status <- system2("quarto", c("render", "report.qmd"))
if (!identical(status, 0L)) stop("Quarto report render failed")
