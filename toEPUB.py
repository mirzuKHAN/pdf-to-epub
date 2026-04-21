import json
import os
import re
from ebooklib import epub
from rapidfuzz import fuzz


def replace_math_in_text(text):
    """Scans raw text/HTML for LaTeX math markers and converts them to MathML."""
    if not text:
        return ""

    def math_replacer(match):
        # Pass the entire matched string (including markers) to the converter
        return convert_to_mathml(match.group(0))

    # Replace \( ... \)
    text = re.sub(r'\\\((.*?)\\\)', math_replacer, text, flags=re.DOTALL)
    # Replace \[ ... \]
    text = re.sub(r'\\\[(.*?)\\\]', math_replacer, text, flags=re.DOTALL)
    # Replace $$ ... $$
    text = re.sub(r'\$\$(.*?)\$\$', math_replacer, text, flags=re.DOTALL)
    # Replace $ ... $ (Uses negative lookbehind to avoid matching currency like $50)
    text = re.sub(r'(?<!\$)\$([^$\n]+)\$(?!\$)', math_replacer, text)

    return text


def convert_to_mathml(latex_str):
    """Converts LaTeX math to native EPUB3 MathML."""
    if not latex_str:
        return ""
    try:
        import latex2mathml.converter

        # Clean up the LaTeX string markers
        latex_str = latex_str.strip()
        if latex_str.startswith('$$') and latex_str.endswith('$$'):
            latex_str = latex_str[2:-2].strip()
        elif latex_str.startswith('$') and latex_str.endswith('$'):
            latex_str = latex_str[1:-1].strip()
        # Add support for escaping parenthesis and bracket wrappers
        elif latex_str.startswith('\\(') and latex_str.endswith('\\)'):
            latex_str = latex_str[2:-2].strip()
        elif latex_str.startswith('\\[') and latex_str.endswith('\\]'):
            latex_str = latex_str[2:-2].strip()

        return latex2mathml.converter.convert(latex_str)
    except ImportError:
        print("WARNING: 'latex2mathml' is not installed. Falling back to raw LaTeX for inline equations.")
        return f" <i>{latex_str}</i> "
    except Exception as e:
        print(f"MathML Conversion error: {e}")
        return f" <i>{latex_str}</i> "

def extract_text(item_list):
    """Safely extracts text, fixes OCR artifacts, and ensures MathML has breathing room."""
    if not item_list: return ""
    if isinstance(item_list, str): return item_list
    if isinstance(item_list, list) and isinstance(item_list[0], str): return " ".join(item_list)

    chunks = []
    for item in item_list:
        if isinstance(item, dict):
            i_type = item.get('type')
            if i_type == 'text':
                text_val = item.get('content', '')
                # Fix known OCR glitches for "not equal"
                text_val = text_val.replace('\f=', '≠').replace('\\f=', '≠').replace('̸=', '≠')

                # --- NEW: Convert stray LaTeX text formatting into HTML ---
                text_val = re.sub(r'\\textbf{([^}]+)}', r'<b>\1</b>', text_val)
                text_val = re.sub(r'\\textit{([^}]+)}', r'<i>\1</i>', text_val)

                # Process any stray math hidden in standard text
                text_val = replace_math_in_text(text_val)

                chunks.append(('text', text_val))

            elif i_type == 'equation_inline':
                latex_str = item.get('content', '')
                mathml = convert_to_mathml(latex_str)
                chunks.append(('math', mathml))

    res = ""
    prev_type = None

    # State machine to assemble text and gracefully space inline equations
    for ctype, content in chunks:
        if ctype == 'math':
            # Add space BEFORE math if the previous text ended with a letter/number
            if prev_type == 'text' and res and not res[-1].isspace() and res[-1] not in "(-[{":
                res += " "
            res += content
        else:
            # Add space AFTER math if this text starts with a letter/number
            if prev_type == 'math' and content and not content[0].isspace() and content[0] not in ".,:;?!-)]}":
                res += " "
            res += content
        prev_type = ctype

    return res


def format_caption(text):
    """Inserts line breaks in merged captions (e.g. separating sub-captions from the main Figure label)."""
    if not text: return ""
    text = re.sub(r'(\S\s*)(Fig\.\s*\d+|Figure\s*\d+|Table\s*\d+)', r'\1<br/><br/>\2', text)
    return text


def should_merge(prev_text, curr_text):
    """
    Heuristic: Checks if the next paragraph starts with a lowercase letter, indicating a split sentence.
    """
    clean_prev = re.sub(r'<[^>]+>', '', prev_text).strip()
    clean_curr = re.sub(r'<[^>]+>', '', curr_text).strip()

    if not clean_prev or not clean_curr:
        return False

    # If current starts with a lowercase letter, we merge
    if clean_curr[0].islower():
        return True

    return False


def is_list_item(text):
    """Regex to detect if a paragraph actually starts like a list item."""
    clean_text = re.sub(r'<[^>]+>', '', text).strip()
    # Matches digits, bullets, asterisks, hyphens, en-dashes, em-dashes and Cyrillic letters
    pattern = r'^\s*(?:\d+[\.\)]|[a-zA-Zа-яА-ЯёЁ][\.\)]|[\-\•\*—–])\s+'
    return bool(re.match(pattern, clean_text))


def normalize_number(s):
    """Converts superscript numbers to regular digits."""
    superscript_map = str.maketrans('¹²³⁴⁵⁶⁷⁸⁹⁰', '1234567890')
    return s.translate(superscript_map)


def create_epub_from_mineru(json_path, output_epub, base_dir_img, skip_pages=None, pdf_path=None, cover_page_index=None,
                            metadata=None):
    if skip_pages is None:
        skip_pages = []
    if metadata is None:
        metadata = {}

    book = epub.EpubBook()
    book.set_identifier('id_mineru_001')

    # Apply dynamic metadata
    book.set_title(metadata.get("title", "MinerU Converted Document"))
    if "author" in metadata:
        book.add_author(metadata["author"])
    book.set_language('ru')  # Adjusted to Russian based on the text provided

    # --- Handle Optional Cover Page Extraction ---
    has_cover = False
    if pdf_path and cover_page_index is not None:
        try:
            import fitz  # PyMuPDF
            if os.path.exists(pdf_path):
                doc = fitz.open(pdf_path)
                if 0 <= cover_page_index < len(doc):
                    page = doc.load_page(cover_page_index)
                    pix = page.get_pixmap(dpi=150)
                    cover_bytes = pix.tobytes("jpeg")

                    book.set_cover("cover.jpg", cover_bytes)
                    has_cover = True
                    print(f"Successfully added cover from {pdf_path} (Page {cover_page_index + 1})")
                else:
                    print(f"WARNING: Cover page index {cover_page_index} is out of bounds for PDF.")
            else:
                print(f"WARNING: PDF path '{pdf_path}' not found. Cannot extract cover.")
        except ImportError:
            print("WARNING: 'PyMuPDF' is not installed. Run 'pip install PyMuPDF' to enable PDF cover extraction.")
        except Exception as e:
            print(f"WARNING: Failed to extract cover page: {e}")

    # --- Load JSON content ---
    with open(json_path, 'r', encoding='utf-8') as f:
        pages = json.load(f)

    # --- Pre-processing: Collect Footnotes Per Page ---
    page_footnotes = {}  # { page_idx: { '1': 'Footnote text...' } }
    flattened_blocks = []

    # Initialize all pages in the dictionary so we can reference them safely
    for page_idx in range(len(pages)):
        page_footnotes[page_idx] = {}

    last_fn_page = None
    last_fn_num = None

    for page_idx, page in enumerate(pages):
        if page_idx in skip_pages: continue

        for block in page:
            b_type = block.get('type')

            if b_type == 'page_header':
                continue

            if b_type == 'page_footer':
                # Sometimes MinerU puts text in footer. We skip or could preserve if needed.
                continue

            if b_type == 'page_footnote':
                text = extract_text(block.get('content', {}).get('page_footnote_content', [])).strip()
                # Matches standard numbers or superscript numbers at the start of the footnote
                match = re.match(r'^([\d¹²³⁴⁵⁶⁷⁸⁹⁰]+)\s*(.*)', text)
                if match:
                    num = normalize_number(match.group(1))
                    page_footnotes[page_idx][num] = match.group(2)
                    last_fn_page = page_idx
                    last_fn_num = num
                else:
                    # This handles footnotes spanning across multiple pages (stitched to the last valid footnote)
                    if last_fn_page is not None and last_fn_num is not None:
                        base_text = page_footnotes[last_fn_page][last_fn_num].strip()
                        # Remove hyphen if it breaks a word across lines/pages
                        if base_text.endswith('-') and len(base_text) > 1 and base_text[-2].isalpha():
                            page_footnotes[last_fn_page][last_fn_num] = base_text[:-1] + text
                        else:
                            page_footnotes[last_fn_page][last_fn_num] = base_text + " " + text
                continue

            # Store the block and its associated page index
            flattened_blocks.append((page_idx, block))

    # Helper to insert footnote links into a text block
    def link_footnotes(text, page_idx):
        if page_idx not in page_footnotes:
            page_footnotes[page_idx] = {}

        fns = page_footnotes[page_idx]

        # 1. Process explicit OLMOCR \footnote{...} tags (handles \f form-feed glitch)
        def inline_footnote_replacer(match):
            fn_text = match.group(1).strip()

            # Check if this matches an existing MinerU footnote (fuzzy or exact)
            matched_num = None
            for num, m_text in fns.items():
                if fn_text[:30].lower() in m_text.lower() or m_text[:30].lower() in fn_text.lower():
                    matched_num = num
                    fns[num] = fn_text  # Update to OLMOCR's text
                    break

            # If not found in MinerU, assign it the next available sequential number
            if not matched_num:
                existing_nums = [int(n) for n in fns.keys() if str(n).isdigit()]
                matched_num = str(max(existing_nums) + 1) if existing_nums else "1"
                fns[matched_num] = fn_text

            return f'<a href="#fn_{page_idx}_{matched_num}" id="ref_{page_idx}_{matched_num}"><sup>{matched_num}</sup></a>'

        # Matches literal \footnote, \\footnote, or [form-feed]ootnote
        text = re.sub(r'(?:\\+footnote|\x0cootnote)\{([^}]+)\}', inline_footnote_replacer, text)

        # 2. Fallback: Standard MinerU superscript regex mapping
        if not fns:
            return text

        pattern = r'(?<=[A-Za-zа-яА-ЯёЁ\.\,\;\?\!\:\"\'\”\’\»\>\]\)])([\d¹²³⁴⁵⁶⁷⁸⁹⁰]+)(?=\s|$|[.,;:?!"\')\]]|<)'

        def replacer(match):
            num_raw = match.group(1)
            num = normalize_number(num_raw)
            if num in fns:
                return f'<a href="#fn_{page_idx}_{num}" id="ref_{page_idx}_{num}"><sup>{num}</sup></a>'
            return match.group(0)

        return re.sub(pattern, replacer, text)

    # --- Render HTML Content ---
    chapter = epub.EpubHtml(title='Main Content', file_name='chap_01.xhtml', lang='ru')

    css_styles = """
    <style>
      img {max-width: 100%; height: auto;} 
      figure {margin: 1.5em 0; text-align: center;} 
      figcaption {font-size: 0.9em; margin-top: 0.5em; line-height: 1.4;}

      /* Equation Styling */
      math {padding: 0 0.2em;} 
      .equation {text-align: center; margin: 1.5em 0; overflow-x: auto;} 
      .equation math {display: block; padding: 0;} 

      li {margin-bottom: 0.5em; line-height: 1.5;}
      table {border-collapse: collapse; width: 100%; font-size: 0.85em; margin: 1em 0;}
      th, td {border: 1px solid #999; padding: 0.4em; text-align: left;}
      .footnotes {font-size: 0.85em; border-top: 1px solid #ccc; margin-top: 2em; padding-top: 1em;}
      .footnote-item {margin-bottom: 0.8em; text-indent: 0;}
      p {text-indent: 1.5em; margin: 0.8em 0; line-height: 1.5;}
      h1, h2, h3 {page-break-after: avoid;}
    </style>
    """

    html_content = [
        f"<html xmlns='http://www.w3.org/1999/xhtml' xmlns:epub='http://www.idpf.org/2007/ops'><head>{css_styles}</head><body>"]

    toc_links = []
    heading_counter = 0
    added_images = set()

    pending_paragraph = ""
    active_list = []
    standard_x0 = 1000

    def get_image_html(img_path, alt_text="Image"):
        if not img_path: return ""
        full_img_path = os.path.join(base_dir_img, img_path)
        img_name = os.path.basename(img_path)

        if os.path.exists(full_img_path):
            if img_name not in added_images:
                with open(full_img_path, 'rb') as img_f:
                    ext = img_name.split('.')[-1].lower()
                    media_type = "image/png" if ext == "png" else "image/jpeg"

                    img_item = epub.EpubItem(
                        uid=img_name,
                        file_name=f"images/{img_name}",
                        media_type=media_type,
                        content=img_f.read()
                    )
                    book.add_item(img_item)
                    added_images.add(img_name)
            return f'<img src="images/{img_name}" alt="{alt_text}"/>'
        return ""

    def flush_paragraph():
        nonlocal pending_paragraph
        if pending_paragraph:
            html_content.append(f"<p>{pending_paragraph.strip()}</p>")
            pending_paragraph = ""

    def flush_list():
        nonlocal active_list
        if active_list:
            html_content.append("<ul>")
            for item in active_list:
                html_content.append(f"<li>{item}</li>")
            html_content.append("</ul>")
            active_list = []

    def flush_text_buffers():
        flush_paragraph()
        flush_list()

    def merge_text(base, addition):
        base_clean = base.strip()
        add_clean = addition.strip()
        if base_clean.endswith('-') and len(base_clean) > 1 and base_clean[-2].isalpha():
            return base_clean[:-1] + add_clean
        return base_clean + " " + add_clean

    # --- Main Block Processing ---
    for page_idx, block in flattened_blocks:
        b_type = block.get('type')
        content = block.get('content', {})
        bbox = block.get('bbox', [0, 0, 0, 0])
        current_x0 = bbox[0]

        if b_type == 'paragraph':
            raw_text = extract_text(content.get('paragraph_content', []))
            if not raw_text.strip(): continue

            # Apply footnote linking locally to this text
            text = link_footnotes(raw_text, page_idx)

            if not active_list and current_x0 < standard_x0 and current_x0 > 0:
                standard_x0 = current_x0

            if active_list:
                if is_list_item(text):
                    active_list.append(text)
                    continue
                if should_merge(active_list[-1], text):
                    active_list[-1] = merge_text(active_list[-1], text)
                    continue

                # NEW LOGIC: Check if it looks like a brand new paragraph
                clean_prev = re.sub(r'<[^>]+>', '', active_list[-1]).strip()
                clean_curr = re.sub(r'<[^>]+>', '', text).strip()
                ends_with_terminator = clean_prev.endswith(('.', '?', '!', '”', '"'))
                starts_with_capital = clean_curr and clean_curr[0].isupper()

                is_indented = current_x0 > (standard_x0 + 15)

                # Only swallow if it's indented AND it doesn't look like a completely new sentence
                if is_indented and not (ends_with_terminator and starts_with_capital):
                    active_list[-1] = active_list[-1].strip() + "<br/><br/>" + text.strip()
                    continue

                flush_list()
                pending_paragraph = text
            else:
                if is_list_item(text):
                    flush_paragraph()
                    active_list.append(text)
                    continue

                if pending_paragraph:
                    if should_merge(pending_paragraph, text):
                        pending_paragraph = merge_text(pending_paragraph, text)
                    else:
                        flush_paragraph()
                        pending_paragraph = text
                else:
                    pending_paragraph = text

        elif b_type == 'list':
            flush_paragraph()
            if current_x0 < standard_x0 and current_x0 > 0:
                standard_x0 = current_x0

            items = []
            for item in content.get('list_items', []):
                raw_item_text = extract_text(item.get('item_content', []))
                # Apply footnote logic
                items.append(link_footnotes(raw_item_text, page_idx))

            if active_list:
                active_list.extend(items)
            else:
                active_list = items

        elif b_type == 'title':
            flush_text_buffers()
            level = content.get('level', 1)
            raw_text = extract_text(content.get('title_content', []))
            text = link_footnotes(raw_text, page_idx)

            heading_id = f"heading_{heading_counter}"
            html_content.append(f"<h{level} id='{heading_id}'>{text}</h{level}>")

            if level == 1:
                # Strip HTML tags for the TOC title
                toc_text = re.sub(r'<[^>]+>', '', text)
                toc_links.append(epub.Link(f'chap_01.xhtml#{heading_id}', toc_text, heading_id))
            heading_counter += 1

        elif b_type == 'image':
            flush_text_buffers()
            img_html = get_image_html(content.get('image_source', {}).get('path', ''))
            if img_html:
                html_content.append('<figure>')
                html_content.append(img_html)

                raw_captions = extract_text(content.get('image_caption', []))
                captions = link_footnotes(raw_captions, page_idx)
                if captions:
                    formatted_caption = format_caption(captions)
                    html_content.append(f'<figcaption>{formatted_caption}</figcaption>')

                html_content.append('</figure>')


        elif b_type == 'table':
            flush_text_buffers()
            table_html = content.get('html', content.get('table_body', ''))

            table_html = replace_math_in_text(table_html)

            raw_captions = extract_text(content.get('table_caption', []))
            raw_footnotes = extract_text(content.get('table_footnote', []))

            captions = link_footnotes(raw_captions, page_idx)
            table_footnotes = link_footnotes(raw_footnotes, page_idx)

            html_content.append('<figure style="overflow-x: auto;">')
            if captions:
                formatted_caption = format_caption(captions)
                html_content.append(
                    f'<figcaption style="font-weight: bold; margin-bottom: 0.5em;">{formatted_caption}</figcaption>')

            if table_html:
                html_content.append(table_html)
            else:
                img_html = get_image_html(content.get('image_source', {}).get('path', ''), alt_text="Table Graphic")
                if img_html: html_content.append(img_html)

            if table_footnotes:
                html_content.append(f'<figcaption style="font-style: italic;">{table_footnotes}</figcaption>')
            html_content.append('</figure>')

        elif b_type in ['equation', 'equation_interline']:
            flush_text_buffers()
            img_html = get_image_html(content.get('image_source', {}).get('path', ''), alt_text="Equation")

            if img_html:
                html_content.append(f'<div class="equation">{img_html}</div>')
            else:
                eq_text = content.get('math_content', content.get('text', ''))
                mathml = convert_to_mathml(eq_text)
                if mathml:
                    html_content.append(f'<div class="equation">{mathml}</div>')
                else:
                    html_content.append(f'<div class="equation"><code>{eq_text}</code></div>')

    flush_text_buffers()

    # --- Append Footnotes Section at the bottom ---
    # Only append if we actually collected any footnotes globally
    if any(page_footnotes.values()):
        html_content.append("<div class='footnotes'><h3>Footnotes</h3>")

        for p_idx in sorted(page_footnotes.keys()):
            fns = page_footnotes[p_idx]
            if not fns:
                continue

            # Iterate sequentially through numeric order of footnotes
            for num in sorted(fns.keys(), key=lambda x: int(x)):
                text = fns[num]
                html_content.append(
                    f"<p class='footnote-item' id='fn_{p_idx}_{num}'><a href='#ref_{p_idx}_{num}'>[{num}]</a> {text}</p>")

        html_content.append("</div>")

    html_content.append("</body></html>")
    chapter.content = "\n".join(html_content)

    book.add_item(chapter)
    book.toc = tuple(toc_links)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # Properly structure the spine to optionally include the cover
    spine = ['nav', chapter]
    if has_cover:
        spine.insert(0, 'cover')

    book.spine = spine

    epub.write_epub(output_epub, book, {})
    print(f"Successfully created EPUB: {output_epub}")


if __name__ == '__main__':
    BASE_DIR_IMG = "mineru"
    JSON_FILE = "merge/corrected.json"
    OUTPUT_EPUB = "outputs/output.epub"

    # --- Cover Settings ---
    # Put None to disable cover, or the 0-indexed page number (e.g., 0 for the first page).
    PDF_FILE = "books/scherer_ar_2012-18-27.pdf"
    COVER_PAGE_INDEX = 0
    PAGES_TO_SKIP = []

    create_epub_from_mineru(
        JSON_FILE,
        OUTPUT_EPUB,
        BASE_DIR_IMG,
        skip_pages=PAGES_TO_SKIP,
        pdf_path=PDF_FILE,
        cover_page_index=COVER_PAGE_INDEX
    )