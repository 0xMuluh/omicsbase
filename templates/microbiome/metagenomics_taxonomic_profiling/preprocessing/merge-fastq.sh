#!/bin/bash

# Loop through unique sample IDs based on their prefix (e.g., 101-1, 102-1, 103-1)
for sample in $(ls *_R1_001.fastq.gz | sed -E 's/D[0-9]+-([0-9]+-[0-9])_S[0-9]+_L[0-9]+_R1_001.fastq.gz/\1/' | sort -u); do
  # Define the sample prefix (e.g., 101-1, 102-1)
  sample_prefix="D*-${sample}_S*_L*_"

  # Concatenate R1 files for the sample
  cat ${sample_prefix}R1_001.fastq.gz > ${sample}_R1.fastq.gz
  
  # Concatenate R2 files for the sample
  cat ${sample_prefix}R2_001.fastq.gz > ${sample}_R2.fastq.gz

  echo "Merged files for sample ${sample} into ${sample}_R1.fastq.gz and ${sample}_R2.fastq.gz"
done