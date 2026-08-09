# Microbiota analysis pipeline

This project provides pipeline for analyzing microbiota data. It includes tools for alpha diversity, beta diversity, differential abundance analysis, and visualization of results.

The pipeline is implemented as a Quarto website, allowing for interactive exploration of the analysis results. It processes metagenomic sequencing data to characterize microbial communities and identify significant differences between sample groups.

## Repository structure

```
.
├── code/
│   ├── _freeze/
│   ├── _quarto.yml
│   ├── data.R
│   ├── funct.R
│   ├── main.R
│   └── site_libs/
├── output/
│   ├── alpha/
│   ├── beta/
│   ├── daa/
│   ├── ratio/
│   ├── index.html
│   ├── search.json
│   └── site_libs/
├── preprocessing/
│   └── remove_columns_metaphlan_db_meta4_combined_reports.R
└── README.md
```

### Key Files:

- `code/_quarto.yml`: Configuration file for the Quarto website
- `code/main.R`: Main script for running the analysis pipeline
- `code/data.R`: Script for data loading and preprocessing
- `code/funct.R`: Custom functions used in the analysis
- `output/`: Directory containing generated HTML reports and visualizations
- `preprocessing/remove_columns_metaphlan_db_meta4_combined_reports.R`: Script for preprocessing MetaPhlAn output

## Usage Instructions

### Prerequisites

- R (version 4.0 or higher)
- Quarto (version 1.0 or higher)
- Required R packages: (list key packages here)

### Installation

1. Clone this repository:
   ```
   git clone https://github.com/example/microbiota_pipeline.git
   cd microbiota_pipeline
   ```

2. Install required R packages:
   ```R
   install.packages(c("package_name"))
   ```

3. Install Quarto following the instructions at https://quarto.org/docs/get-started/

### Running the Analysis

1. Open the project in RStudio or your preferred R environment.

2. Modify the `code/_quarto.yml` file to configure your analysis parameters.

3. Run the main analysis script:
   ```R
   source("code/main.R")
   ```

4. View the results by opening `output/index.html` in a web browser.

### Customizing the Analysis

- Modify `code/data.R` to change data preprocessing steps
- Edit `code/funct.R` to add or modify analysis functions
- Update `code/_quarto.yml` to change the structure and content of the output website

## Data Flow

1. Raw metagenomic sequencing data is processed using MetaPhlAn to generate taxonomic profiles.
2. The `remove_columns_metaphlan_db_meta4_combined_reports.R` script preprocesses the MetaPhlAn output.
3. `data.R` loads and formats the preprocessed data for analysis.
4. `main.R` orchestrates the analysis pipeline, calling functions from `funct.R` to perform:
   - Alpha diversity analysis
   - Beta diversity analysis
   - Differential abundance analysis
5. Results are generated as HTML reports and visualizations in the `output/` directory.
6. The Quarto website compiles all results into an interactive, navigable format.

```
[Raw Data] -> [MetaPhlAn] -> [Preprocessing] -> [Data Loading] -> [Analysis Pipeline] -> [HTML Reports] -> [Quarto Website]
```

## Troubleshooting

- If you encounter memory issues, try increasing the R memory limit:
  ```R
  memory.limit(size=8000)
  ```

- For errors related to missing packages, ensure all required packages are installed:
  ```R
  if (!requireNamespace("BiocManager", quietly = TRUE))
      install.packages("BiocManager")
  BiocManager::install(c("package1", "package2", "package3"))
  ```

- If the Quarto render fails, check the R console for error messages and ensure Quarto is properly installed and in your system PATH.

For additional support, please open an issue on the GitHub repository.