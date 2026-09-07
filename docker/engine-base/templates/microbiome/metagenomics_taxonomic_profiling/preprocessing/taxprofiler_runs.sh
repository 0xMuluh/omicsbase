#!/bin/bash
#SBATCH --job-name=taxprofiler_runs
#SBATCH --time=72:00:00
#SBATCH --partition=small        #for multinode, large partition should be use
#SBATCH --ntasks=10
#SBATCH --account=project_2011041
#SBATCH --cpus-per-task=4
#SBATCH --mem=160G


export SINGULARITY_TMPDIR=$PWD
export SINGULARITY_CACHEDIR=$PWD
unset XDG_RUNTIME_DIR

# Activate Nextflow on Puhti
module load nextflow

#run the nextflow
#for testing (in profile params use test,singularity flag )

nextflow run nf-core/taxprofiler -r 1.1.5 -c /scratch/project_2011041/CONFIGS/nextflow.config \
   -with-report /scratch/project_2011041/test_report_final_run.html -with-trace \
   -profile singularity \
   --input /scratch/project_2011041/CONFIGS/samplesheet.csv \
   --databases /scratch/project_2011041/CONFIGS/database.csv \
   --outdir /scratch/project_2011041/RESULTS_FINAL_RUN \
   --perform_shortread_qc \
   --perform_shortread_complexityfilter  --shortread_complexityfilter_tool bbduk \
   --perform_shortread_hostremoval \
   --hostremoval_reference /projappl/project_2009677/DB/T2T-CHM13v2.0.zip \
   --shortread_hostremoval_index /projappl/project_2009677/DB/human_CHM13/ \
   --perform_runmerging \
   --save_analysis_ready_fastqs \
   --save_hostremoval_unmapped \
   --run_profile_standardisation \
   --run_metaphlan
 