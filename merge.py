import os
import json
import copy
import re
from rapidfuzz import fuzz

# ==========================================
# FILE CONFIGURATION (Defaults for manual runs)
# ==========================================
DEFAULT_MINERU_INPUT = "mineru/content_list_v2.json"
DEFAULT_OLMOCR_INPUT = "olmocr/olmocr_output.json"
DEFAULT_OUTPUT_FILE = "merge/corrected.json"
THRESHOLD = 85.0


# ==========================================

def is_mostly_arabic(text):
    """
    Checks if the majority of alphabetical characters in the text are Arabic.
    Useful for filtering out pure Arabic paragraphs.
    """
    alphas = [c for c in text if c.isalpha()]
    if not alphas:
        return False
    arabic_alphas = [c for c in alphas if
                     '\u0600' <= c <= '\u06FF' or '\u0750' <= c <= '\u077F' or '\u08A0' <= c <= '\u08FF' or '\uFB50' <= c <= '\uFDFF' or '\uFE70' <= c <= '\uFEFF']
    return len(arabic_alphas) / len(alphas) > 0.8


def clean_for_gibberish_check(text):
    """
    Strips out HTML tables, LaTeX equations, and Arabic characters.
    Used exclusively to ensure the Gibberish Detector only evaluates pure, non-Arabic text.
    """
    # Remove HTML tables
    text = re.sub(r'<table.*?</table>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Remove LaTeX equations
    text = re.sub(r'(\\\(.*?\\\)|\$\$.*?\$\$)', '', text)
    # Remove all Arabic characters
    text = re.sub(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]', '', text)
    # Clean up extra whitespace
    return re.sub(r'\s+', ' ', text).strip()


def replace_footnotes_with_numbers(text):
    """
    Safely finds \footnote{...} in text, handling nested braces,
    and replaces them with sequential numbers (1, 2, 3...) per page.
    """
    result = []
    i = 0
    counter = 1

    while i < len(text):
        idx = text.find(r'\footnote{', i)
        if idx == -1:
            result.append(text[i:])
            break

        # Append everything up to the footnote
        result.append(text[i:idx])

        # Find the matching closing brace
        brace_count = 1
        j = idx + 10  # Length of '\footnote{'
        while j < len(text) and brace_count > 0:
            if text[j] == '{':
                brace_count += 1
            elif text[j] == '}':
                brace_count -= 1
            j += 1

        if brace_count == 0:
            # Successfully found the end of the footnote block
            result.append(str(counter))
            counter += 1
            i = j
        else:
            # Malformed bracket (shouldn't happen, but fallback just in case)
            result.append(text[idx:])
            break

    return "".join(result)


def load_olmocr_pages(filepath):
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Could not find OLMOCR file at: '{filepath}'")
    pages_data = []
    with open(filepath, "r", encoding="utf-8") as f:
        file_content = json.load(f)
        for page_item in file_content:
            # Handle new format where contents are nested under "data"
            data = page_item.get("data", {})

            txt = data.get("natural_text", "") or ""
            lang = data.get("primary_language", "en")

            # Eradicate OLMOCR markdown image tags completely
            txt = re.sub(r'!\[.*?\]\(.*?\.png\)', '', txt)
            pages_data.append({
                "text": txt.strip(),
                "lang": lang
            })
    return pages_data


def extract_pure_text(content_list):
    combined_text = []
    has_non_text = False
    for item in content_list:
        if isinstance(item, dict):
            if item.get("type") == "text":
                combined_text.append(item.get("content", ""))
            else:
                has_non_text = True
        elif isinstance(item, str):
            combined_text.append(item)
    return " ".join(combined_text).strip(), has_non_text


def parse_olmocr_to_nodes(text):
    r"""
    Finds LaTeX inline equations \( ... \) or $$ ... $$ in OLMOCR text
    and splits them back into MinerU-style nodes.
    """
    pattern = r'(\\\(.*?\\\)|\$\$.*?\$\$)'
    parts = re.split(pattern, text)

    nodes = []
    for i, part in enumerate(parts):
        if not part: continue
        if i % 2 == 0:
            nodes.append({"type": "text", "content": part})
        else:
            eq_content = part.replace('\\(', '').replace('\\)', '').replace('$$', '').strip()
            nodes.append({"type": "equation_inline", "content": eq_content})
    return nodes


def preserve_prefix(miner_text, olm_text):
    """
    If MinerU has a sub-caption before "Fig." or "Table" (like "(b) 2D map..."),
    extract it and prepend it to the OLMOCR text since OLMOCR often drops them.
    """
    miner_clean = miner_text.strip()
    olm_clean = olm_text.strip()

    # Grab everything before "Fig." or "Table" or "Figure"
    match = re.search(r'^(.*?)(?=Fig\.|Table|Figure)', miner_clean, re.IGNORECASE | re.DOTALL)
    if match and match.group(1).strip():
        prefix = match.group(1).strip()
        if prefix[:15].lower() not in olm_clean.lower():
            return f"{prefix} {olm_clean}"

    # Fallback for just "(a)" or "Fig. 1"
    match2 = re.match(r'^([\(（][a-zA-Z0-9][\)）]|(?:Fig\.|Table|Figure)\s*\d+[\.\:]?)\s*', miner_clean, re.IGNORECASE)
    if match2:
        prefix = match2.group(1).strip()
        prefix = prefix.replace('（', '(').replace('）', ')')
        if not olm_clean.lower().startswith(prefix.lower()):
            return f"{prefix} {olm_clean}"

    return olm_clean


def find_best_olmocr_block(miner_text, olm_blocks, b_type):
    if len(miner_text) < 5:
        return None

    best_score = 0
    best_idx = -1
    miner_lower = miner_text.lower()

    for i, block in enumerate(olm_blocks):
        if block["used"]: continue
        t_raw = block["text"]
        t_compare = re.sub(r'(?:\\+footnote|\x0cootnote)\{[^}]+\}', '', t_raw)

        # Anti-Greed Safety Net: Prevent structural tags from swallowing massive paragraphs
        if b_type in ["title", "page_footnote", "page_header", "page_footer"]:
            if len(t_compare) > max(100, len(miner_text) * 3):
                continue

        t_lower = t_compare.lower()
        if len(miner_text) < len(t_compare) * 0.7:
            score = fuzz.partial_ratio(miner_lower, t_lower)
        else:
            score = fuzz.ratio(miner_lower, t_lower)

        if score > best_score:
            best_score = score
            best_idx = i

    if best_score >= THRESHOLD:
        return best_idx
    return None


def process_block(block, olm_blocks, all_matched_texts):
    b_type = block.get("type")
    b_content = block.get("content", {})
    sort_idx = None
    has_non_text_elements = False
    text_preview = ""

    def attempt_match(content_key, preserve_captions=False):
        nonlocal has_non_text_elements, text_preview
        raw_list = b_content.get(content_key, [])
        combined_text, has_non_text = extract_pure_text(raw_list)
        has_non_text_elements = has_non_text
        if not text_preview: text_preview = combined_text[:40].replace('\n', ' ')

        idx = find_best_olmocr_block(combined_text, olm_blocks, b_type)
        if idx is not None:
            olm_blocks[idx]["used"] = True
            extracted_text = olm_blocks[idx]["text"]

            if preserve_captions:
                extracted_text = preserve_prefix(combined_text, extracted_text)

            all_matched_texts.append(extracted_text)
            b_content[content_key] = parse_olmocr_to_nodes(extracted_text)
            return idx
        return None

    if b_type == "title":
        sort_idx = attempt_match("title_content")
    elif b_type == "paragraph":
        sort_idx = attempt_match("paragraph_content")
    elif b_type in ["page_header", "page_footer", "page_footnote"]:
        sort_idx = attempt_match(f"{b_type}_content")
    elif b_type == "list":
        for li in b_content.get("list_items", []):
            c_text, hnt = extract_pure_text(li.get("item_content", []))
            if hnt: has_non_text_elements = True
            if not text_preview: text_preview = c_text[:40]

            idx = find_best_olmocr_block(c_text, olm_blocks, b_type)
            if idx is not None:
                olm_blocks[idx]["used"] = True
                ext_text = olm_blocks[idx]["text"]
                all_matched_texts.append(ext_text)
                li["item_content"] = parse_olmocr_to_nodes(ext_text)
                if sort_idx is None: sort_idx = idx

    elif b_type == "image":
        has_non_text_elements = True
        sort_idx = attempt_match("image_caption", preserve_captions=True)

    elif b_type == "table":
        has_non_text_elements = True
        sort_idx = attempt_match("table_caption", preserve_captions=True)

        if sort_idx is not None:
            if sort_idx + 1 < len(olm_blocks) and not olm_blocks[sort_idx + 1]["used"]:
                next_block_text = olm_blocks[sort_idx + 1]["text"]
                if next_block_text.lower().startswith("<table"):
                    b_content["html"] = next_block_text
                    olm_blocks[sort_idx + 1]["used"] = True
        else:
            # If the table has no caption (or match failed), aggressively hunt for the HTML block
            for i, ob in enumerate(olm_blocks):
                if not ob["used"] and ob["text"].lower().startswith("<table"):
                    b_content["html"] = ob["text"]
                    ob["used"] = True
                    sort_idx = i
                    break

    return sort_idx, has_non_text_elements, text_preview


def get_olmocr_blocks(page_text):
    blocks = []

    # 1. Isolate HTML tables so they don't get destroyed by the newline splitter
    parts = re.split(r'(<table.*?</table>)', page_text, flags=re.DOTALL | re.IGNORECASE)

    for part in parts:
        part = part.strip()
        if not part: continue

        # If this part is a protected HTML table, add it directly as an unbroken block
        if part.lower().startswith("<table"):
            blocks.append({"text": part, "used": False})
        else:
            # For all standard text, split safely by newlines
            raw_blocks = re.split(r'\n+', part)
            for b in raw_blocks:
                b = b.strip()
                if not b: continue

                # Split single newlines if followed by a digit, superscript, or bullet
                sub_blocks = re.split(r'\n\s*(?=[\d¹²³⁴⁵⁶⁷⁸⁹*\-•])', b)
                for sb in sub_blocks:
                    sb = sb.strip()
                    if sb: blocks.append({"text": sb, "used": False})

    return blocks


def deduplicate_olmocr_blocks(olm_blocks):
    for i in range(len(olm_blocks)):
        for j in range(len(olm_blocks)):
            if i == j: continue
            text_i = olm_blocks[i]["text"]
            text_j = olm_blocks[j]["text"]

            if len(text_i) < 60: continue

            # CASE-INSENSITIVE:
            if len(text_i) < len(text_j):
                if fuzz.partial_ratio(text_i.lower(), text_j.lower()) > 95:
                    olm_blocks[i]["used"] = True
            elif len(text_i) == len(text_j) and i > j:
                if fuzz.ratio(text_i.lower(), text_j.lower()) > 95:
                    olm_blocks[i]["used"] = True


def extract_leftover_olmocr_blocks(olm_blocks, all_matched_texts):
    leftover_blocks = []

    for i, oblock in enumerate(olm_blocks):
        if not oblock["used"]:
            text = oblock["text"]

            # Ignore completely empty strings or unused HTML tables
            if len(text) < 5 or text.lower().startswith("<table"):
                continue

            # Drop purely Arabic injected blocks
            if is_mostly_arabic(text):
                continue

            is_dup = False
            if len(text) >= 60:
                text_lower = text.lower()
                for matched in all_matched_texts[-100:]:
                    if len(text) < len(matched) + 50:
                        # CASE-INSENSITIVE:
                        if fuzz.partial_ratio(text_lower, matched.lower()) > 85:
                            is_dup = True
                            print(f"  [DISCARDED DUPLICATE] '{text[:40].replace(chr(10), ' ')}...'")
                            break

            if not is_dup:
                leftover_blocks.append({
                    "block": {
                        "type": "paragraph",
                        "content": {"paragraph_content": parse_olmocr_to_nodes(text)},
                        "bbox": [0, 0, 0, 0]
                    },
                    "sort_idx": i,
                    "text": text
                })
    return leftover_blocks


def is_mineru_gibberish(miner_blocks, olm_text):
    """
    Checks if MinerU outputted garbage text by comparing it against OLMOCR.
    Strictly evaluates using pure, non-Arabic text.
    """
    miner_texts = []
    for b in miner_blocks:
        # Only evaluate actual text blocks (ignore tables, images, equations)
        if b.get("type") in ["paragraph", "title", "list", "page_header", "page_footer", "page_footnote"]:
            for val in b.get("content", {}).values():
                if isinstance(val, list):
                    t, _ = extract_pure_text(val)
                    if t: miner_texts.append(t)

    miner_full = " ".join(miner_texts)

    # Clean both strings: Remove Arabic characters, Tables, and Equations
    miner_clean = clean_for_gibberish_check(miner_full)
    olm_clean = clean_for_gibberish_check(olm_text)

    # Count only alphanumeric characters to avoid triggering gibberish just on punctuation
    olm_alnum = [c for c in olm_clean if c.isalnum()]
    miner_alnum = [c for c in miner_clean if c.isalnum()]

    # If the remaining text is too short (e.g. the page was almost entirely Arabic), it's not considered gibberish
    if len(olm_alnum) < 20 or len(miner_alnum) < 10:
        return False

    score = fuzz.ratio(miner_clean.lower(), olm_clean.lower())
    return score < 35


def process_gibberish_page(miner_blocks, olm_blocks):
    """
    Uses Proportional Mapping to merge perfect OLMOCR text with perfect MinerU layouts.
    Transfers title tags, heading levels, and footnote tags without relying on text matching.
    """
    merged_items = []

    # 1. Isolate text blocks from layout blocks
    text_miner_blocks = []
    for mb in miner_blocks:
        b_type = mb.get("type")
        if b_type in ["paragraph", "title", "list", "page_header", "page_footer", "page_footnote"]:
            text_miner_blocks.append(mb)

    num_miner_texts = max(1, len(text_miner_blocks))
    num_olm_texts = max(1, len(olm_blocks))

    olm_mapped = []
    for i, ob in enumerate(olm_blocks):
        # Discard Arabic from proportional mapping text
        if is_mostly_arabic(ob["text"]):
            continue

        olm_mapped.append({
            "text": ob["text"],
            "type": "paragraph",
            "level": None,
            "sort_key": (i + 0.5) / num_olm_texts
        })

    for j, mb in enumerate(text_miner_blocks):
        b_type = mb.get("type")
        if b_type in ["title", "page_header", "page_footer", "page_footnote"]:
            m_rel_pos = (j + 0.5) / num_miner_texts
            closest_idx = int(m_rel_pos * num_olm_texts)
            closest_idx = min(max(closest_idx, 0), len(olm_mapped) - 1)

            if closest_idx < 0: continue

            # Anti-Hijack Rule: Do not tag an OLMOCR block as a header/footer if it's longer than 80 chars
            if b_type in ["page_header", "page_footer"] and len(olm_mapped[closest_idx]["text"]) > 80:
                continue

            olm_mapped[closest_idx]["type"] = b_type
            if b_type == "title":
                olm_mapped[closest_idx]["level"] = mb.get("content", {}).get("level", 1)

    for om in olm_mapped:
        # Exclude leftover HTML tables from becoming text blocks
        if om["text"].lower().startswith("<table"):
            continue

        content_dict = {f"{om['type']}_content": parse_olmocr_to_nodes(om["text"])}
        if om["level"] is not None:
            content_dict["level"] = om["level"]

        merged_items.append({
            "sort_key": om["sort_key"],
            "block": {
                "type": om["type"],
                "content": content_dict,
                "bbox": [0, 0, 0, 0]
            }
        })

    # 3. Inject MinerU Media (Images, Tables, Equations) accurately into the text flow
    text_blocks_seen = 0
    for mb in miner_blocks:
        b_type = mb.get("type")
        if b_type in ["paragraph", "title", "list", "page_header", "page_footer", "page_footnote"]:
            text_blocks_seen += 1
        elif b_type in ["image", "table", "equation"]:
            # Wipe gibberish captions
            if "image_caption" in mb.get("content", {}):
                mb["content"]["image_caption"] = []
            if "table_caption" in mb.get("content", {}):
                mb["content"]["table_caption"] = []

            # Place the media immediately after the text block it followed in MinerU
            media_rel_pos = text_blocks_seen / num_miner_texts

            merged_items.append({
                "sort_key": media_rel_pos,
                "block": mb
            })

    # 4. Sort everything top-to-bottom
    merged_items.sort(key=lambda x: x["sort_key"])
    return [item["block"] for item in merged_items]


def run_merge(mineru_input=DEFAULT_MINERU_INPUT, olmocr_input=DEFAULT_OLMOCR_INPUT, output_file=DEFAULT_OUTPUT_FILE):
    """Callable entry point for FastAPI with dynamic paths."""
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    with open(mineru_input, "r", encoding="utf-8") as f:
        miner_pages = json.load(f)

    olm_pages_data = load_olmocr_pages(olmocr_input)
    corrected_data = []
    num_pages = min(len(miner_pages), len(olm_pages_data))
    print(f"Processing {num_pages} pages...\n")

    all_matched_texts = []

    for page_idx in range(num_pages):
        page_blocks = copy.deepcopy(miner_pages[page_idx])
        olm_text = olm_pages_data[page_idx]["text"]

        olm_blocks = get_olmocr_blocks(olm_text)
        # deduplicate_olmocr_blocks(olm_blocks)

        print(f"\n--- PAGE {page_idx + 1} ---")

        # -------------------------------------------------------------
        # MinerU Fallback (If OLMOCR is effectively empty after ONLY Arabic removal)
        # -------------------------------------------------------------
        # Strip only Arabic characters to see if OLMOCR has any non-Arabic text left.
        # We intentionally keep tables and equations here because OLMOCR is good at them!
        olm_no_arabic = re.sub(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]', '', olm_text)

        # Only count actual alphanumeric characters (ignoring spaces, colons, punctuation left behind)
        olm_no_arabic_alnum = [c for c in olm_no_arabic if c.isalnum()]

        if len(olm_no_arabic_alnum) < 10:
            print("[MINERU FALLBACK] OLMOCR has no usable non-Arabic text. Relying entirely on MinerU.")
            kept_blocks = []
            for block in page_blocks:
                b_type = block.get("type")
                ext = ""
                for vals in block.get("content", {}).values():
                    if isinstance(vals, list):
                        ext += "".join(
                            n.get("content", "") for n in vals if isinstance(n, dict) and n.get("type") == "text")
                    elif isinstance(vals, str):
                        ext += vals

                if b_type in ["paragraph", "title", "list", "page_header", "page_footer", "page_footnote"]:
                    if is_mostly_arabic(ext):
                        print(f"[DROPPED ARABIC] {b_type.upper()}: '{ext[:40].replace(chr(10), ' ')}...'")
                        continue

                # If we get here, we keep the block
                if ext:
                    print(f"[RETAINED MINERU] {b_type.upper()}: '{ext[:40].replace(chr(10), ' ')}...'")
                else:
                    print(f"[RETAINED MINERU MEDIA] {b_type.upper()}")
                kept_blocks.append(block)

            corrected_data.append(kept_blocks)
            continue

        # -------------------------------------------------------------
        # Gibberish Detector -> Proportional Mapping Router
        # -------------------------------------------------------------
        if is_mineru_gibberish(page_blocks, olm_text):
            print(f"[GIBBERISH FALLBACK] MinerU OCR failed (Score < 35%). Using proportional structural mapping.")
            corrected_blocks = process_gibberish_page(page_blocks, olm_blocks)
            corrected_data.append(corrected_blocks)
            continue

        # -------------------------------------------------------------
        # Standard High-Precision Fuzzy Text Matching
        # -------------------------------------------------------------
        blocks_meta = [None] * len(page_blocks)

        def process_and_log(orig_idx, block):
            sort_idx, has_non_text, text_preview = process_block(block, olm_blocks, all_matched_texts)
            b_type = block.get("type")

            if sort_idx is not None:
                # SAFE EXTRACT FOR LOGS - Fixes the IndexError!
                ext = ""
                for vals in block.get("content", {}).values():
                    if isinstance(vals, list):
                        ext += "".join(
                            n.get("content", "") for n in vals if isinstance(n, dict) and n.get("type") == "text")
                    elif isinstance(vals, str):
                        ext += vals

                # Rule 1: Drop if mostly Arabic
                keep_block = not is_mostly_arabic(ext)
                if not keep_block:
                    print(f"[DROPPED ARABIC] {b_type.upper()}: '{ext[:40].replace(chr(10), ' ')}...'")
                else:
                    print(f"[MATCHED] {b_type.upper()}: -> '{ext[:40].replace(chr(10), ' ')}...'")

                blocks_meta[orig_idx] = {"block": block, "sort_idx": sort_idx, "orig_idx": orig_idx, "type": b_type,
                                         "keep": keep_block}
            else:
                # Rule 2: Keep unmatched pure text paragraphs
                if b_type == "paragraph" and not has_non_text:
                    keep_unmatched = True
                else:
                    keep_unmatched = has_non_text or b_type in ["image", "footnote", "page_footnote", "table",
                                                                "equation"]

                # Rule 1: Apply Arabic drop check to unmatched retained text
                if keep_unmatched and is_mostly_arabic(text_preview):
                    keep_unmatched = False
                    print(f"[DROPPED ARABIC UNMATCHED] {b_type.upper()}: '{text_preview[:40]}...'")
                elif keep_unmatched:
                    print(f"[UNMATCHED - RETAINED] {b_type.upper()}: '{text_preview[:40]}...'")
                else:
                    print(f"[UNMATCHED - DROPPED] {b_type.upper()}: '{text_preview[:40]}...'")

                blocks_meta[orig_idx] = {"block": block, "sort_idx": None, "orig_idx": orig_idx, "type": b_type,
                                         "keep": keep_unmatched}

        # PASS 1: High Priority (Titles, Tables, Images, Footnotes)
        for orig_idx, block in enumerate(page_blocks):
            if block.get("type") != "paragraph":
                process_and_log(orig_idx, block)

        # PASS 2: Low Priority (Paragraphs)
        for orig_idx, block in enumerate(page_blocks):
            if block.get("type") == "paragraph":
                process_and_log(orig_idx, block)

        kept_meta = [m for m in blocks_meta if m is not None and m["keep"]]

        # Interpolation Logic
        for i in range(len(kept_meta)):
            if kept_meta[i]["sort_idx"] is None:
                prev_val = next(
                    (kept_meta[j]["sort_idx"] for j in range(i - 1, -1, -1) if kept_meta[j]["sort_idx"] is not None),
                    None)

                next_val = None
                next_idx = None
                for j in range(i + 1, len(kept_meta)):
                    if kept_meta[j]["sort_idx"] is not None:
                        next_val = kept_meta[j]["sort_idx"]
                        next_idx = j
                        break

                if prev_val is not None and next_val is not None:
                    gap = next_idx - i
                    step = (next_val - prev_val) / (gap + 1)
                    kept_meta[i]["sort_idx"] = prev_val + step
                elif prev_val is not None:
                    kept_meta[i]["sort_idx"] = prev_val + 0.1
                elif next_val is not None:
                    kept_meta[i]["sort_idx"] = next_val - 0.1
                else:
                    kept_meta[i]["sort_idx"] = 0

        injected_blocks = extract_leftover_olmocr_blocks(olm_blocks, all_matched_texts)
        for inj in injected_blocks:
            print(f"[INJECTED] NEW TEXT: '{inj['text'][:40].replace(chr(10), ' ')}...'")
            kept_meta.append({"block": inj["block"], "sort_idx": inj["sort_idx"], "keep": True})

        kept_meta.sort(key=lambda x: x["sort_idx"])
        corrected_data.append([m["block"] for m in kept_meta])

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(corrected_data, f, indent=4, ensure_ascii=False)

    print(f"\nSuccessfully saved {len(corrected_data)} pages to '{output_file}'.")


if __name__ == "__main__":
    run_merge()