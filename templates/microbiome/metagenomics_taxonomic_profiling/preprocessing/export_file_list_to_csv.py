import os
import csv

# Specify the directory path
directory_path = '/scratch/project_2011041/CONFIG'
output_csv = 'raw_data_FFGC_240003_II_Hieta_2_list.csv'

# Get a list of file names in the directory
file_names = os.listdir('/scratch/project_2011041/RAW_DATA/RAW_DATA_FFGC/240003_II_Hieta_2')

# Write the file names to a CSV
with open(output_csv, 'w', newline='') as csvfile:
    writer = csv.writer(csvfile)
    writer.writerow(["File Name"])  # Write header
    for file_name in file_names:
        writer.writerow([file_name])

print(f"File names exported to {output_csv}")