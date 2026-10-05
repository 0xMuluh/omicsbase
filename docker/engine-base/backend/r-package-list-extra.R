# Packages added after the main list was built, installed in their own image layer
# so the cached layers for r-package-list.R stay valid. Fold these into
# r-package-list.R the next time the base image is rebuilt from scratch.
cran_packages <- c(
  "mikropml"
)

bioc_packages <- c(
  # Single-cell data
  "TENxPBMCData",
  "muscData",
  "SingleCellMultiModal",
  "TabulaMurisSenisData",
  "HCAData",
  "DropletTestFiles",
  "zellkonverter",
  # Spatial data
  "TENxVisiumData",
  "MerfishData",
  "OSTA.data",
  "NanoStringNCTools",
  # Bulk RNA-seq data
  "parathyroidSE",
  "recount3",
  "tximportData",
  "macrophage",
  "fission",
  "tissueTreg",
  "GSE13015",
  "GSE62944",
  "bodymapRat",
  "ALL",
  "golubEsets",
  "breastCancerVDX",
  # Multi-omics and cancer data
  "curatedTCGAData",
  "depmap",
  "CRCL18",
  "mixOmics",
  # Proteomics data
  "scpdata",
  "pRolocdata",
  "RforProteomics",
  # Microbiome
  "lefser"
)

github_packages <- c(
  "himelmallick/IntegratedLearner"
)
