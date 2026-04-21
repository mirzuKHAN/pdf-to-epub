import asyncio
import aiohttp
import base64
import json
import os
import sys
import logging
import fitz  # PyMuPDF
from dotenv import load_dotenv
import json_repair
import yaml
import re

load_dotenv()

# Configuration for independent runs
PDF_PATH = "books/scherer_ar_2012.pdf"
OUTPUT_JSON = "olmocr/olmocr_output.json"
MAX_CONCURRENT_REQUESTS = 10
MAX_RETRIES = 2
MODEL_NAME = "allenai/olmOCR-2-7B-1025"
API_URL = "https://api.deepinfra.com/v1/openai/chat/completions"
# API_URL = "http://127.0.0.1:8080/v1/openai/chat/completions"

# Prompts
SYSTEM_PROMPT = ""

USER_PROMPT = """Attached is one page of a document that you must process. Just return the plain text representation of this document as if you were reading it naturally. Convert equations to LateX and tables to HTML.
If there are any figures or charts, label them with the following markdown syntax ![Alt text describing the contents of the figure](page_startx_starty_width_height.png)
Return your output as markdown, with a front matter section on top specifying values for the primary_language, is_rotation_valid, rotation_correction, is_table, and is_diagram parameters."""
# SYSTEM_PROMPT = """You are a highly accurate, multilingual document transcription AI. Your sole job is to extract the natural reading text from the provided document page EXACTLY as it appears, while strictly following all omission and formatting rules."""
#
# USER_PROMPT = """Attached is an image of one page of a document. Please return the plain text representation of this document as if you were reading it naturally.
#Transcribe the text EXACTLY in its original language and alphabet (e.g., Cyrillic for Russian). DO NOT translate, phoneticize, or transliterate names or words into Chinese or any other language (e.g., do not convert 'Анас' to '阿纳斯').
# Follow these STRICT formatting rules:
# 1. **Math:** Convert all inline equations to LaTeX wrapped in `\( ... \)` and block equations wrapped in `$$ ... $$`.
#
# Return your output as a raw JSON object with the following schema:
# {
#   "primary_language": "en",
#   "natural_text": "The extracted text goes here..."
# }
# Output ONLY the valid JSON block, with no markdown formatting blocks like ```json."""

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# def pdf_to_base64_images(pdf_path, dpi=300):
#     """Converts a PDF into a list of base64 encoded PNG images (one per page)."""
#     logging.info(f"Converting PDF {pdf_path} to images...")
#     base64_images = []
#
#     try:
#         doc = fitz.open(pdf_path)
#     except Exception as e:
#         logging.error(f"Failed to open PDF: {e}")
#         raise e
#
#     for page_num in range(len(doc)):
#         page = doc.load_page(page_num)
#         # Zoom factor to increase resolution (dpi/72)
#         zoom = dpi / 72.0
#         mat = fitz.Matrix(zoom, zoom)
#         pix = page.get_pixmap(matrix=mat, alpha=False)
#
#         # Convert to PNG bytes then to base64
#         img_bytes = pix.tobytes("png")
#         b64_str = base64.b64encode(img_bytes).decode('utf-8')
#         base64_images.append((page_num + 1, b64_str))
#
#     doc.close()
#     logging.info(f"Successfully converted {len(base64_images)} pages.")
#     return base64_images


def pdf_to_base64_images(pdf_path, max_dim=2048, preview_dir="olmocr/previews"):
    """Converts a PDF into a list of base64 encoded PNG images and saves previews."""
    logging.info(f"Converting PDF {pdf_path} to images...")
    base64_images = []

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        logging.error(f"Failed to open PDF: {e}")
        raise e

    # Create the preview directory if it doesn't exist
    if preview_dir:
        os.makedirs(preview_dir, exist_ok=True)
        logging.info(f"Saving image previews to ./{preview_dir}/")

    for page_num in range(len(doc)):
        page = doc.load_page(page_num)

        # Calculate scale to keep the longest side under max_dim
        rect = page.rect
        longest_side = max(rect.width, rect.height)
        scale = min(max_dim / longest_side, 3.0)

        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, alpha=False)

        # Get raw PNG bytes
        img_bytes = pix.tobytes("png")

        # --- PREVIEW SAVING ---
        if preview_dir:
            preview_path = os.path.join(preview_dir, f"page_{page_num + 1}.png")
            with open(preview_path, "wb") as img_file:
                img_file.write(img_bytes)
        # ----------------------

        # Convert to base64 for the API
        b64_str = base64.b64encode(img_bytes).decode('utf-8')
        base64_images.append((page_num + 1, b64_str))

    doc.close()
    logging.info(f"Successfully converted {len(base64_images)} pages.")
    return base64_images


async def process_page(session, semaphore, page_num, b64_image, api_key):
    """Sends a single page to the DeepInfra API with concurrency limits and retries."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": MODEL_NAME,
        "max_tokens": 8192,
        "temperature": 0.1,
        # "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": USER_PROMPT
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{b64_image}"
                        }
                    }
                ]
            }
        ]
    }

    async with semaphore:
        for attempt in range(MAX_RETRIES):
            try:
                logging.info(f"Processing page {page_num} (Attempt {attempt + 1}/{MAX_RETRIES})...")
                async with session.post(API_URL, headers=headers, json=payload) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logging.warning(f"Page {page_num} failed with status {response.status}: {error_text}")
                        if response.status == 429:
                            await asyncio.sleep(2 ** attempt)  # Exponential backoff for rate limits
                        continue

                    data = await response.json()
                    content = data['choices'][0]['message']['content'].strip()

                    if content.startswith('```json'): content = content[7:]
                    if content.startswith('```'): content = content[3:]
                    if content.endswith('```'): content = content[:-3]

                    # parsed_json = json_repair.loads(content)
                    parsed_data = {
                        "primary_language": "unknown",
                        "is_rotation_valid": True,
                        "rotation_correction": 0,
                        "is_table": False,
                        "is_diagram": False,
                        "natural_text": ""
                    }

                    # if isinstance(parsed_json, dict):
                    #     # If the model invented random keys, stitch them back into natural_text
                    #     hallucinated_keys = [k for k in parsed_json.keys() if
                    #                          k not in ["primary_language", "natural_text"]]
                    #
                    #     if hallucinated_keys:
                    #         stitched_text = []
                    #         # Add whatever it put in natural_text first
                    #         if "natural_text" in parsed_json and parsed_json["natural_text"]:
                    #             stitched_text.append(str(parsed_json["natural_text"]))
                    #
                    #         # Append the hallucinated keys and their values
                    #         for k in hallucinated_keys:
                    #             stitched_text.append(str(k))
                    #             if parsed_json[k]:  # if the value isn't empty
                    #                 stitched_text.append(str(parsed_json[k]))
                    #             del parsed_json[k]  # Remove the bad key
                    #
                    #         # Reassign the stitched text to the correct key
                    #         parsed_json["natural_text"] = "\n\n".join(stitched_text)
                    #
                    #     # Ensure primary_language exists
                    #     if "primary_language" not in parsed_json:
                    #         parsed_json["primary_language"] = "unknown"
                    # # ---------------------------------------------------
                    #
                    # logging.info(f"Successfully processed page {page_num}.")
                    # return {"page": page_num, "data": parsed_json}

                    yaml_pattern = re.compile(r'^---\n(.*?)\n---\n(.*)', re.DOTALL)
                    match = yaml_pattern.search(content)

                    if match:
                        yaml_text = match.group(1)
                        markdown_text = match.group(2).strip()

                        try:
                            # Load the front matter values
                            front_matter = yaml.safe_load(yaml_text)
                            if isinstance(front_matter, dict):
                                parsed_data.update(front_matter)
                        except yaml.YAMLError:
                            logging.warning(f"Failed to parse YAML front matter on page {page_num}.")

                        parsed_data["natural_text"] = markdown_text
                    else:
                        # Fallback if the model forgot the front matter blocks
                        logging.warning(f"No front matter found on page {page_num}. Storing raw content.")
                        parsed_data["natural_text"] = content

                    logging.info(f"Successfully processed page {page_num}.")
                    return {"page": page_num, "data": parsed_data}


            except Exception as e:
                logging.warning(f"Exception on page {page_num} (Attempt {attempt + 1}): {e}")
                await asyncio.sleep(2 ** attempt)

        logging.error(f"Failed to process page {page_num} after {MAX_RETRIES} attempts.")
        return {"page": page_num,
                "data": {"primary_language": "unknown", "natural_text": "", "error": "Extraction failed."}}


async def run_olmocr(pdf_path: str, output_json: str, api_key: str):
    """Callable entry point for FastAPI."""
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found at path: {pdf_path}")

    # FIX: Offload the heavy CPU-bound image conversion to a background thread
    # so it doesn't block the asyncio event loop!
    pages = await asyncio.to_thread(pdf_to_base64_images, pdf_path)

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    timeout = aiohttp.ClientTimeout(total=180)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [
            process_page(session, semaphore, page_num, b64_image, api_key)
            for page_num, b64_image in pages
        ]
        results = await asyncio.gather(*tasks)

    results.sort(key=lambda x: x["page"])

    os.makedirs(os.path.dirname(output_json), exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    logging.info(f"Extraction complete. Results saved to {output_json}")


async def main():
    api_key = os.environ.get("DEEPINFRA_TOKEN")
    if not api_key:
        logging.error("Please set the DEEPINFRA_TOKEN environment variable.")
        sys.exit(1)

    if not os.path.exists(PDF_PATH):
        logging.error(f"PDF file not found at path: {PDF_PATH}")
        sys.exit(1)

    # 1. Convert PDF pages to base64 images
    pages = pdf_to_base64_images(PDF_PATH)

    # 2. Setup concurrency and session
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    timeout = aiohttp.ClientTimeout(total=180)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        # 3. Create tasks
        tasks = [
            process_page(session, semaphore, page_num, b64_image, api_key)
            for page_num, b64_image in pages
        ]

        # 4. Execute concurrently and save ON THE GO
        results = []
        for coro in asyncio.as_completed(tasks):
            result = await coro
            results.append(result)

            # Keep the array sorted by page number even as they arrive out of order
            results.sort(key=lambda x: x["page"])

            # Safely overwrite the JSON file with the current progress
            with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)

            logging.info(f"Progress saved: {len(results)}/{len(pages)} pages completed.")

    logging.info(f"Extraction fully complete. Final results are in {OUTPUT_JSON}")

if __name__ == "__main__":
    asyncio.run(main())