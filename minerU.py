#!/usr/bin/env python3
"""
MinerU PDF Extractor (Batch, Parallel, and Merge Edition)
Splits large PDFs, processes them in parallel, and cleanly stitches
the JSON and image outputs back together into a single directory.
"""

import io
import json
import shutil
import time
import zipfile
import sys
import concurrent.futures
from pathlib import Path

import requests
import urllib3
from pypdf import PdfReader, PdfWriter
from rich.console import Console
from rich.panel import Panel

# Disable insecure request warnings caused by expired SSL certs on MinerU's CDN
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

console = Console()


def split_pdf(pdf_path: Path, max_pages: int = 500) -> list[Path]:
    """Splits a PDF into smaller chunks if it exceeds the max_pages limit."""
    reader = PdfReader(pdf_path)
    total_pages = len(reader.pages)

    if total_pages <= max_pages:
        console.print(f"[green]PDF has {total_pages} pages (under limit). No splitting needed.[/green]")
        return [pdf_path]

    console.print(f"[yellow]PDF has {total_pages} pages. Splitting into {max_pages}-page chunks...[/yellow]")
    chunk_paths = []

    temp_dir = pdf_path.parent / f"{pdf_path.stem}_chunks"
    temp_dir.mkdir(exist_ok=True)

    for i in range(0, total_pages, max_pages):
        writer = PdfWriter()
        end_page = min(i + max_pages, total_pages)

        for page_num in range(i, end_page):
            writer.add_page(reader.pages[page_num])

        chunk_name = f"{pdf_path.stem}_part{i // max_pages + 1}.pdf"
        chunk_path = temp_dir / chunk_name

        with open(chunk_path, "wb") as f:
            writer.write(f)

        chunk_paths.append(chunk_path)
        console.print(f"Created chunk: {chunk_name} (Pages {i + 1}-{end_page})")

    return chunk_paths


def upload_file(url: str, filepath: Path):
    """Uploads a single file to a pre-signed URL."""
    with open(filepath, "rb") as f:
        res = requests.put(url, data=f)
        if res.status_code != 200:
            raise Exception(f"Upload failed for {filepath.name}: {res.status_code}")
    return filepath.name


def download_and_extract(zip_url: str, extract_dir: Path):
    """Downloads a ZIP file and extracts it to the specified directory."""
    res = requests.get(zip_url, verify=False)
    if res.status_code != 200:
        raise Exception(f"Failed to download ZIP: {res.status_code}")

    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(res.content)) as z:
        z.extractall(extract_dir)
    return extract_dir


def update_image_paths(obj, part_prefix: str):
    """Recursively updates image paths in the JSON to match the renamed image files."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            # If a value is a string referencing the images folder, rewrite it
            if isinstance(v, str) and v.startswith("images/"):
                obj[k] = v.replace("images/", f"images/{part_prefix}")
            else:
                update_image_paths(v, part_prefix)
    elif isinstance(obj, list):
        for item in obj:
            update_image_paths(item, part_prefix)


def extract_pdfs_with_mineru(pdf_paths: list[Path], token: str):
    """Uploads chunks, waits for processing, downloads results, and stitches them together."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    # Step 1: Request batch upload URLs
    url = "https://mineru.net/api/v4/file-urls/batch"
    payload = {
        "files": [{"name": p.name, "data_id": f"chunk_{i}"} for i, p in enumerate(pdf_paths)],
        "model_version": "vlm",
    }

    console.print(f"\nRequesting upload URLs for {len(pdf_paths)} file(s)...")
    response = requests.post(url, headers=headers, json=payload)

    if response.status_code != 200:
        raise Exception(f"Failed to get upload URLs: {response.text}")

    result = response.json()
    if result.get("code") != 0:
        raise Exception(f"API error: {result.get('msg')}")

    batch_id = result["data"]["batch_id"]
    upload_urls = result["data"]["file_urls"]
    console.print(f"Batch ID: [cyan]{batch_id}[/cyan]")

    # Step 2: Upload chunks in parallel
    console.print("\nUploading files in parallel...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(pdf_paths), 5)) as executor:
        upload_futures = [
            executor.submit(upload_file, upload_urls[i], pdf_paths[i])
            for i in range(len(pdf_paths))
        ]
        for future in concurrent.futures.as_completed(upload_futures):
            console.print(f"[green]✓ Uploaded {future.result()}[/green]")

    # Step 3: Poll for batch results
    console.print("\nWaiting for MinerU parallel parsing... (polling every 10s)")
    results_url = f"https://mineru.net/api/v4/extract-results/batch/{batch_id}"

    completed_results = []
    while True:
        res = requests.get(results_url, headers=headers)
        data = res.json()

        extract_results = data.get("data", {}).get("extract_result", [])
        if not extract_results:
            time.sleep(10)
            continue

        states = [f.get("state") for f in extract_results]
        done_count = states.count("done")
        failed_count = states.count("failed")

        console.print(f"Status: {done_count}/{len(pdf_paths)} done, {failed_count} failed...")

        if failed_count > 0:
            raise Exception("One or more files failed parsing on MinerU.")

        if done_count == len(pdf_paths):
            # Sort results so they remain in chronological order
            completed_results = sorted(extract_results, key=lambda x: x.get("data_id", ""))
            break

        time.sleep(10)

    # Step 4: Download and extract ZIPs
    console.print("\nDownloading and extracting results...")

    base_mineru_dir = (Path.cwd() / "mineru").resolve()
    if base_mineru_dir.exists():
        console.print("[yellow]Clearing existing mineru directory...[/yellow]")
        shutil.rmtree(base_mineru_dir)

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(completed_results), 5)) as executor:
        download_futures = []
        for i, file_result in enumerate(completed_results):
            zip_url = file_result.get("full_zip_url")
            extract_dir = base_mineru_dir / f"part_{i + 1}"
            download_futures.append(executor.submit(download_and_extract, zip_url, extract_dir))

        for future in concurrent.futures.as_completed(download_futures):
            console.print(f"[green]✓ Extracted part[/green]")

    # Step 5: Merge JSON and Images
    console.print("\nStitching combined JSON and Images together...")

    combined_json = []
    combined_images_dir = base_mineru_dir / "images"
    combined_images_dir.mkdir(parents=True, exist_ok=True)

    for i in range(len(completed_results)):
        part_dir = base_mineru_dir / f"part_{i + 1}"

        # 1. Handle Images
        part_images_dir = part_dir / "images"
        part_prefix = f"part_{i + 1}_"

        if part_images_dir.exists():
            for img_file in part_images_dir.glob("*"):
                if img_file.is_file():
                    new_img_name = f"{part_prefix}{img_file.name}"
                    shutil.copy(img_file, combined_images_dir / new_img_name)

        # 2. Handle JSON (content_list_v2.json)
        # Often MinerU stores it inside an auto-generated subfolder inside the ZIP
        json_candidates = list(part_dir.rglob("content_list_v2.json"))

        if json_candidates:
            json_path = json_candidates[0]
            with open(json_path, 'r', encoding='utf-8') as f:
                part_data = json.load(f)

            # Rewrite image references inside the JSON to point to the renamed files
            update_image_paths(part_data, part_prefix)

            # Extend the master list with the pages from this chunk
            combined_json.extend(part_data)

        # Clean up the temporary part directory after merging its contents
        shutil.rmtree(part_dir)

    # Save the final, unified JSON
    final_json_path = base_mineru_dir / "content_list_v2.json"
    with open(final_json_path, 'w', encoding='utf-8') as f:
        json.dump(combined_json, f, ensure_ascii=False, indent=4)

    # Optional: Clean up the local chunked PDF files
    if len(pdf_paths) > 1:
        shutil.rmtree(pdf_paths[0].parent)

    console.print(f"\n[bold green]✓ Success! All parts combined cleanly.[/bold green]")
    console.print(f"Unified JSON saved to: {final_json_path}")
    console.print(f"All images pooled in: {combined_images_dir}")


def extract_pdf_with_mineru(pdf_path: Path, token: str):
    """
    Wrapper function that processes a single PDF path.
    Automatically splits the PDF if it exceeds the page limit,
    processes chunks in parallel, and merges the results.
    """
    # 1. Split the PDF (returns a list containing one or more Path objects)
    chunk_paths = split_pdf(pdf_path, max_pages=500)

    # 2. Pass the list of chunks to the parallel extraction and merging function
    extract_pdfs_with_mineru(chunk_paths, token)


def main():
    console.print(
        Panel.fit(
            "[bold cyan]MinerU PDF Extractor (Batch & Merge Edition)[/bold cyan]\n"
            "Automatically splits large PDFs, processes them in parallel,\n"
            "and beautifully merges `content_list_v2.json` and images together.",
            border_style="cyan",
        )
    )

    token = "eyJ0eXBlIjoiSldUIiwiYWxnIjoiSFM1MTIifQ.eyJqdGkiOiIyMDQwMDE1OSIsInJvbCI6IlJPTEVfUkVHSVNURVIiLCJpc3MiOiJPcGVuWExhYiIsImlhdCI6MTc3NDYwNTg5MywiY2xpZW50SWQiOiJsa3pkeDU3bnZ5MjJqa3BxOXgydyIsInBob25lIjoiIiwib3BlbklkIjpudWxsLCJ1dWlkIjoiMGQxYmU2YTUtNjg1MS00NTZjLWIwMGQtNDA2YjBkZTQ0NmM1IiwiZW1haWwiOiIiLCJleHAiOjE3ODIzODE4OTN9.FN2Dg2NyHvL6AUHdm3Gh7g-bcOe6PdU9p5sr6ldq6DeSoOnrwUVXEFCLiXAIeU-3PFAIFBkxBDDn64BcmzN3EQ"
    pdf_file = "books/Preview-Jonathan-Strange-and-Mr-Norrell-by-Susanna-Clarke.pdf"

    pdf_path = Path(pdf_file).resolve()
    if not pdf_path.exists():
        console.print(f"[red]✗[/red] PDF file not found: {pdf_file}")
        sys.exit(1)

    try:
        # 1. Split the PDF (if necessary)
        chunk_paths = split_pdf(pdf_path, max_pages=500)

        # 2. Upload, parse, download, and merge!
        extract_pdfs_with_mineru(chunk_paths, token)

    except Exception as e:
        console.print(f"\n[bold red]Error: {str(e)}[/bold red]")
        sys.exit(1)


if __name__ == "__main__":
    main()