# 📚 Dual-Engine PDF to EPUB Converter (MinerU + OLMOCR)

An advanced, AI-powered PDF to EPUB conversion pipeline. This project solves the common pitfalls of PDF extraction by intelligently combining the structural layout capabilities of **MinerU** with the high-fidelity text recognition of **OLMOCR**. The result is a beautifully formatted EPUB eBook that preserves reading order, complex tables, mathematical equations, and cross-linked footnotes.

---

## ✨ Key Features
* **Dual-OCR Concurrency:** Runs MinerU (for layout, bounding boxes, tables, and images) and OLMOCR (for high-quality text extraction) simultaneously using asynchronous processing.
* **Intelligent Merging (`merge.py`):** Uses fuzzy matching and proportional mapping algorithms to align superior text with perfect layout.
* **Smart eBook Generation (`toEPUB.py`):** Reconstructs split paragraphs, converts LaTeX to MathML, and dynamically links footnotes.
* **Batch Processing:** Automatically chunks large PDFs to bypass API limits and stitches the results back together.
* **FastAPI Backend:** Fully asynchronous, non-blocking backend for fast processing.

---

## 🧠 Deep Dive: Core Algorithms

This project relies on two highly specialized algorithms to handle the chaotic nature of PDF OCR data.

### 1. The Merging Algorithm (`merge.py`)
PDF layout engines (MinerU) are great at finding *where* things are, but sometimes struggle with *what* the text says. Vision Language Models (OLMOCR) are great at reading text naturally, but lose structural coordinates. `merge.py` is the bridge.

**Techniques & Edge Cases Handled:**
* **Fuzzy Text Matching:** Uses `rapidfuzz` to calculate Levenshtein distance between MinerU structural blocks and OLMOCR text blobs, injecting the highly accurate OLMOCR text into the precise MinerU layout nodes.
* **Gibberish Detection & Proportional Mapping:** Sometimes MinerU outputs pure garbage (e.g., heavily heavily watermarked pages). The script strips HTML/LaTeX/Arabic, evaluates the alphanumeric purity, and if MinerU scores < 35% against OLMOCR, it triggers a **Proportional Mapping Fallback**. It assigns structural tags (Heading 1, Paragraph, Image) to OLMOCR text based on relative vertical positioning rather than text content.
* **Arabic Hallucination Filtering:** VLM-based OCRs (like OLMOCR) sometimes hallucinate pure Arabic text on blank or noisy pages. The script includes an `is_mostly_arabic` heuristic to detect and discard these hallucinations.
* **Anti-Greed Tagging:** Prevents massive paragraphs from being mistakenly tagged as `page_header` or `page_footer` just because of a partial fuzzy match, protecting the document's structure.
* **Prefix Preservation:** OLMOCR often drops sub-captions (like "(a)" or "(b)") before "Fig.". The algorithm uses regex to rescue these prefixes from MinerU and prepend them back to the OLMOCR text.

### 2. The EPUB Generation Algorithm (`toEPUB.py`)
Taking raw JSON OCR data and making it a readable eBook requires fixing human-reading flow issues caused by physical page boundaries.

**Techniques & Edge Cases Handled:**
* **Cross-Page Sentence Stitching:** Uses a custom heuristic (`should_merge`) to detect split sentences. If a new paragraph on a new page starts with a lowercase letter, it intelligently merges it with the previous paragraph, removing arbitrary page-break artifacts.
* **LaTeX to MathML State Machine:** EPUBs require specific formatting for math. The script converts LaTeX arrays into native EPUB3 MathML (`latex2mathml`). A custom state machine tracks the transition between plain text and math to ensure proper spacing (preventing issues where `$x$is` becomes `$x$is` instead of `$x$ is`).
* **Footnote Extraction & Cross-Linking:**
  * Collects all `page_footnote` blocks globally.
  * Handles multi-line footnotes that break across pages.
  * Uses regex to find footnote markers (e.g., superscript numbers or OLMOCR's `\footnote{...}` tags), normalizes them, and injects bi-directional HTML anchors (`<a href="#fn_..."><id="ref_...">`). This allows readers to click a number in the text and jump to the footnote at the end of the chapter.
* **List Detection Heuristics:** Uses regex (`is_list_item`) to detect bullets, numbered lists, or Cyrillic list items that OCR engines often mistakenly classify as standard paragraphs, wrapping them in proper `<ul>`/`<li>` HTML tags.

---

## 🏗️ System Architecture

1. **`minerU.py`:** Splits large PDFs, uploads them to the OpenXLab/MinerU API in parallel, downloads the ZIP results, and merges the chunked JSONs and images back together.
2. **`olmocr.py`:** Converts PDF pages to high-res Base64 images and processes them via the DeepInfra API (`allenai/olmOCR-2-7B-1025`).
3. **`merge.py`:** The brain of the operation. Aligns and merges the two JSON outputs.
4. **`toEPUB.py`:** Parses the merged JSON into a well-structured EPUB using `ebooklib`.
5. **`main.py`:** The FastAPI gateway. Orchestrates the asynchronous execution of the above scripts.

---

## 🚀 Prerequisites

* Python 3.9+
* API Keys:
  * **DeepInfra API Key** (for OLMOCR)
  * **MinerU / OpenXLab Token** (for structural extraction)

## 🛠️ Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/mirzuKHAN/pdf-to-epub.git
   cd pdf-to-epub-fyp
   ```

2. **Install the dependencies:**
   ```bash
   pip install fastapi uvicorn aiohttp PyMuPDF python-dotenv json_repair pypdf rich requests rapidfuzz latex2mathml ebooklib python-multipart
   ```

3. **Set up Environment Variables:**
   Create a `.env` file in the root directory:
   ```env
   DEEPINFRA_TOKEN=your_deepinfra_api_key_here
   ```

## 💻 Usage

### Starting the Server
Run the FastAPI backend using Uvicorn:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
*The API documentation (Swagger UI) will be available at `http://localhost:8000/docs`.*

### 2. Starting the Frontend (Vite)
Open a **second** terminal window and navigate to the frontend folder:
```bash
cd frontend

# Install Node modules
npm install

# Start the Vite development server
npm vite
```
*The frontend interface will be available at `http://localhost:5173`.*

### API Endpoints
* `POST /convert/full`: Runs the full MinerU + OLMOCR + Merge pipeline.
* `POST /convert/mineru-only`: Bypasses OLMOCR and uses only MinerU (Faster, suitable for simple, text-heavy PDFs).

**Form Parameters:**
* `file`: (Required) The PDF file upload.
* `token`: (Required) Your MinerU API token.
* `title`: (Optional) Title of the EPUB.
* `author`: (Optional) Author of the EPUB.
* `cover_page_index`: (Optional) Index of the PDF page to use as the cover (default: `0`).
* `skip_pages`: (Optional) Comma-separated list of pages to skip (e.g., `0,1,2`).

## 📂 Directory Structure

* `/books/` - Temporary storage for uploaded PDFs.
* `/mineru/` - Working directory for MinerU API downloads and extracted images.
* `/olmocr/` - Working directory for OLMOCR JSON outputs.
* `/merge/` - Working directory for the corrected, merged JSON.
* `/outputs/` - Final generated `.epub` files ready for download.

---
*Developed as a Final Year Project showcasing asynchronous API orchestration, fuzzy string matching, automated OCR error correction, and EPUB3 generation.*
