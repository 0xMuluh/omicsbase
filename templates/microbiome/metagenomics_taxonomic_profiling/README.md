# Microbiome Analysis Pipeline for Taxonomic Profiling

This project provides a comprehensive pipeline for analyzing microbiome data, focusing on taxonomic profiling and diversity analysis.

The pipeline integrates various tools and techniques to process raw sequencing data, perform quality control, remove host contamination, and conduct in-depth analyses of microbial communities. It is designed to handle short-read sequencing data and produce a wide range of outputs, including alpha and beta diversity metrics, differential abundance analysis, and taxonomic classifications.

Key features include:
- Short read quality control and complexity filtering
- Host DNA removal
- Run merging for multi-lane sequencing data
- Taxonomic profiling using MetaPhlAn
- Alpha and beta diversity analyses
- Differential abundance analysis at multiple taxonomic levels
- Visualization of community composition and diversity metrics

## Repository Structure

- `code/`: Contains the main analysis scripts and configuration files
  - `_freeze/`: Cached results from previous runs
  - `_quarto.yml`: Configuration file for Quarto documentation
  - `data.R`: Data loading and preprocessing script
  - `funct.R`: Custom functions for analysis
  - `main.R`: Main analysis script
  - `alpha/`: Alpha diversity analysis module
  - `beta/`: Beta diversity analysis module
  - `ratio/`: Bacteroidetes/Firmicutes ratio module
  - `daa/`: Differential abundance analysis module
  - `inflammation/`: Inflammatory biomarkers and species-level microbiota changes module
- `output/`: Generated results and visualizations
- `preprocessing/`: Scripts for initial data processing
  - `export_file_list_to_csv.py`: Exports file names to CSV
  - `makesample_list.py`: Creates a sample sheet for sequencing data
  - `merge-fastq.sh`: Merges FastQ files for each sample
  - `taxprofiler_runs.sh`: Runs the nf-core/taxprofiler pipeline

## Usage Instructions

### Prerequisites

- R (version 4.4.2 or later)
- Python (version 3.6 or later)
- Nextflow (latest version)
- Singularity (for running containerized tools)

### Installation

1. Clone the repository:
   ```
   git clone <repository-url>
   cd <repository-name>
   ```

2. Install required R packages:
   ```R
   install.packages(c("tidyverse", "vegan", "phyloseq", "DESeq2"))
   ```

3. Set up the Nextflow environment on your system (refer to Nextflow documentation for specific instructions).

### Running the Pipeline

1. Prepare your input data:
   - Place raw FastQ files in an appropriate directory
   - Create a sample sheet using `preprocessing/makesample_list.py`

2. Configure the pipeline:
   - Edit `code/_quarto.yml` to set project-specific parameters
   - Modify `preprocessing/taxprofiler_runs.sh` to match your computing environment and data paths

3. Run the preprocessing steps:
   ```
   bash preprocessing/merge-fastq.sh
   sbatch preprocessing/taxprofiler_runs.sh
   ```

4. Execute the main analysis:
   ```
   Rscript code/main.R
   ```

5. View the results in the `output/` directory

### Troubleshooting

- If you encounter memory issues during the taxonomic profiling step, try increasing the `--mem` parameter in `taxprofiler_runs.sh`.
- For performance issues, check the log files in the Nextflow work directory for potential bottlenecks.

## Data Flow

1. Raw FastQ files are merged if necessary using `merge-fastq.sh`.
2. The nf-core/taxprofiler pipeline processes the merged FastQ files:
   - Quality control and complexity filtering
   - Host DNA removal
   - Run merging (if applicable)
   - Taxonomic profiling with MetaPhlAn
3. The resulting taxonomic profiles are loaded into R for further analysis.
4. Alpha diversity, beta diversity, and differential abundance analyses are performed.
5. Results are visualized and exported to the `output/` directory.

```
Raw FastQ Files
     |
     v
[merge-fastq.sh]
     |
     v
Merged FastQ Files
     |
     v
[nf-core/taxprofiler]
     |
     v
Taxonomic Profiles
     |
     v
[R analysis scripts]
     |
     v
Final Results and Visualizations
```

## Deployment

The pipeline is designed to run on a high-performance computing cluster using the SLURM workload manager. To deploy:

1. Ensure Nextflow and Singularity are available on your cluster.
2. Modify the SLURM parameters in `taxprofiler_runs.sh` to match your cluster's specifications.
3. Submit the job using `sbatch preprocessing/taxprofiler_runs.sh`.

## Infrastructure

The pipeline utilizes the following key infrastructure components:

- Nextflow: 
  - Workflow management system for running the nf-core/taxprofiler pipeline
- Singularity: 
  - Container platform for running tools in isolated environments
- SLURM: 
  - Workload manager for job scheduling on the HPC cluster

Resources defined in `taxprofiler_runs.sh`:
- Job name: taxprofiler_runs
- Time limit: 72 hours
- Partition: small
- Number of tasks: 10
- CPUs per task: 4
- Memory: 160GB

The script sets up the Singularity environment and loads the Nextflow module before executing the nf-core/taxprofiler pipeline with specified parameters and database paths.