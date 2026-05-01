import os
import pandas as pd
import requests
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import time

CSV_PATH = "./data/csv/Orthophoto_Repository_2023_20260501.csv"
OUTPUT_DIR = "./data/tif/"
MAX_WORKERS = 4 
CHUNK_SIZE = 1024 * 1024  # 1MB chunks

def get_confirm_token(response):
    for key, value in response.cookies.items():
        if key.startswith('download_warning'):
            return value
    return None

def download_tile(row):
    file_id = row['file_id']
    file_name = row['file_name']
    dest_path = os.path.join(OUTPUT_DIR, file_name)
    temp_path = dest_path + ".tmp"

    if os.path.exists(dest_path):
        return f"Skipped: {file_name}"

    url = "https://drive.google.com/uc?export=download"
    session = requests.Session()

    try:
        response = session.get(url, params={'id': file_id}, stream=True, timeout=30)
        
        token = get_confirm_token(response)
        if token:
            params = {'id': file_id, 'confirm': token}
            response = session.get(url, params=params, stream=True, timeout=30)

        first_chunk = next(response.iter_content(CHUNK_SIZE), b"")
        
        if b"Google Drive - Virus scan warning" in first_chunk:
            html_content = first_chunk.decode('utf-8', errors='ignore')
            
            # Extract form action and hidden inputs
            action_match = re.search(r'action="([^"]+)"', html_content)
            action_url = action_match.group(1) if action_match else "https://drive.usercontent.google.com/download"
            
            params = {}
            for match in re.finditer(r'name="([^"]+)" value="([^"]+)"', html_content):
                params[match.group(1)] = match.group(2)
            
            if params:
                response = session.get(action_url, params=params, stream=True, timeout=30)
                first_chunk = next(response.iter_content(CHUNK_SIZE), b"")

        # Write content to file
        with open(temp_path, "wb") as f:
            if first_chunk:
                f.write(first_chunk)
            for chunk in response.iter_content(CHUNK_SIZE):
                if chunk:
                    f.write(chunk)

        os.rename(temp_path, dest_path)
        return f"Downloaded: {file_name}"

    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return f"Error downloading {file_name}: {e}"

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if not os.path.exists(CSV_PATH):
        print(f"Error: CSV file not found at {CSV_PATH}")
        return

    df = pd.read_csv(CSV_PATH)
    total_files = len(df)
    
    print(f"Starting bulk download of {total_files} orthophoto tiles...")
    print(f"Destination: {OUTPUT_DIR}")
    print(f"Parallel workers: {MAX_WORKERS}")

    with tqdm(total=total_files, desc="Overall Progress", unit="tile") as pbar:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(download_tile, row): row['file_name'] for _, row in df.iterrows()}
            
            for future in as_completed(futures):
                result = future.result()
                pbar.update(1)
                if not result.startswith("Downloaded"):
                    tqdm.write(result)

    print("\nBulk download process finished.")

if __name__ == "__main__":
    main()
