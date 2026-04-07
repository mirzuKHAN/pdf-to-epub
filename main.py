import os
import shutil
import asyncio
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from minerU import extract_pdf_with_mineru
from olmocr import run_olmocr
from merge import run_merge
from toEPUB import create_epub_from_mineru

load_dotenv()

app = FastAPI(
    title="PDF to EPUB Converter API",
    description="API for converting PDFs to EPUBs using MinerU, OLMOCR, and EpubLib.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, replace "*" with your frontend URL (e.g. "http://localhost:5173")
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BOOKS_DIR = Path("books")
OUTPUTS_DIR = Path("outputs")
MERGE_DIR = Path("merge")
OLMOCR_DIR = Path("olmocr")

for d in [BOOKS_DIR, OUTPUTS_DIR, MERGE_DIR, OLMOCR_DIR]:
    d.mkdir(exist_ok=True)


async def process_pipeline(
        pdf_file: UploadFile,
        token: str,
        skip_merge: bool,
        cover_page_index: int,
        skip_pages: str,
        title: str,
        author: str
) -> Path:
    """Helper function to orchestrate the conversion pipeline."""
    # 1. Save uploaded PDF to /books
    pdf_path = BOOKS_DIR / pdf_file.filename
    with open(pdf_path, "wb") as buffer:
        shutil.copyfileobj(pdf_file.file, buffer)

    # Common paths
    mineru_json = "mineru/content_list_v2.json"
    olmocr_json = "olmocr/olmocr_output.json"
    merged_json = "merge/corrected.json"

    if skip_merge:
        try:
            await asyncio.to_thread(extract_pdf_with_mineru, pdf_path.resolve(), token)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"MinerU extraction failed: {str(e)}")
        final_json = mineru_json
    else:
        # Load DeepInfra token strictly from the server's environment
        api_key = os.environ.get("DEEPINFRA_TOKEN")
        if not api_key:
            raise HTTPException(status_code=500, detail="DEEPINFRA_TOKEN is not configured on the server.")

        # Run BOTH Concurrently
        mineru_task = asyncio.to_thread(extract_pdf_with_mineru, pdf_path.resolve(), token)
        olmocr_task = run_olmocr(str(pdf_path), olmocr_json, api_key)

        try:
            await asyncio.gather(mineru_task, olmocr_task)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Concurrent extraction failed: {str(e)}")

        # Run Merge using the explicit dynamic paths
        try:
            await asyncio.to_thread(run_merge, mineru_input=mineru_json, olmocr_input=olmocr_json,
                                    output_file=merged_json)
            final_json = merged_json
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Merge step failed: {str(e)}")

    output_epub = OUTPUTS_DIR / f"{pdf_path.stem}.epub"
    pages_to_skip = [int(p.strip()) for p in skip_pages.split(",")] if skip_pages else []
    metadata = {"title": title, "author": author}

    try:
        await asyncio.to_thread(
            create_epub_from_mineru,
            json_path=final_json,
            output_epub=str(output_epub),
            base_dir_img="mineru",
            skip_pages=pages_to_skip,
            pdf_path=str(pdf_path),
            cover_page_index=cover_page_index,
            metadata=metadata
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"EPUB generation failed: {str(e)}")

    return output_epub


@app.post("/convert/full", summary="Convert PDF to EPUB (Full Pipeline)")
async def convert_full(
        file: UploadFile = File(...),
        token: str = Form(...),
        cover_page_index: Optional[int] = Form(0),
        skip_pages: Optional[str] = Form("", description="Comma-separated page indexes to skip, e.g., '0,1,2'"),
        title: Optional[str] = Form("Converted Document"),
        author: Optional[str] = Form("Unknown Author")
):
    epub_path = await process_pipeline(
        file, token, skip_merge=False, cover_page_index=cover_page_index,
        skip_pages=skip_pages, title=title, author=author
    )
    return FileResponse(path=epub_path, filename=epub_path.name, media_type='application/epub+zip')


@app.post("/convert/mineru-only", summary="Convert PDF to EPUB (MinerU Only)")
async def convert_mineru_only(
        file: UploadFile = File(...),
        token: str = Form(...),
        cover_page_index: Optional[int] = Form(0),
        skip_pages: Optional[str] = Form(""),
        title: Optional[str] = Form("Converted Document"),
        author: Optional[str] = Form("Unknown Author")
):
    epub_path = await process_pipeline(
        file, token, skip_merge=True, cover_page_index=cover_page_index,
        skip_pages=skip_pages, title=title, author=author
    )
    return FileResponse(path=epub_path, filename=epub_path.name, media_type='application/epub+zip')