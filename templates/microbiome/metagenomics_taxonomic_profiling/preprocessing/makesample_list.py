import os
import csv
import re

def create_samplesheet():
    input_dir = "/scratch/project_2011041/RAW_DATA/RAW_DATA_MERGED"
    output_file = "/scratch/project_2011041/CONFIGS/samplesheet.csv"

    # Regular expression to capture sample and replicate information
    pattern = re.compile(r"(\d+-\d+)_R([12])\.fastq\.gz")

    # Dictionary to store file paths for each sample-replicate pair
    samples = {}

    # List all files in the input directory
    for file in os.listdir(input_dir):
        match = pattern.match(file)
        if match:
            sample_id = match.group(1)
            read_direction = match.group(2)
            file_path = os.path.join(input_dir, file)

            # Initialize the sample entry if not present
            if sample_id not in samples:
                samples[sample_id] = {"1": None, "2": None}

            # Assign the file path to the correct read direction
            samples[sample_id][read_direction] = file_path

    # Write the samplesheet
    with open(output_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["sample", "run_accession", "instrument_platform", "fastq_1", "fastq_2", "fasta"])

        # Iterate over each sample-replicate pair to write in the samplesheet
        for sample_id, files in samples.items():
            fastq_1 = files["1"]
            fastq_2 = files["2"]

            if fastq_1 and fastq_2:  # Ensure both R1 and R2 files exist
                run_accession = sample_id  # Use sample_id as run_accession
                instrument_platform = "ILLUMINA"
                
                writer.writerow([sample_id, run_accession, instrument_platform, fastq_1, fastq_2, ""])

    print(f"Samplesheet created at: {output_file}")

if __name__ == "__main__":
    create_samplesheet()