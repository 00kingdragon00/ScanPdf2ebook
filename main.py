#!/usr/bin/env python3
import os
import re
import subprocess
import sys
import fitz  # PyMuPDF

DET_RE = re.compile(r"<\|det\|>(\w+)\s*\[[^\]]*\]<\|/det\|>(.*)$")
SKIP_TYPES = {"image", "footer"}
ASIDE_TYPES = {"aside_text"}


# ---------- PDF -> page images ----------


def render_pages(pdf_path, out_dir, dpi):
    os.makedirs(out_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)
    paths = []
    for i, page in enumerate(doc):
        img_path = os.path.join(out_dir, f"page_{i + 1:04d}.png")
        if not os.path.exists(img_path):
            pix = page.get_pixmap(matrix=mat)
            pix.save(img_path)
        paths.append(img_path)
    doc.close()
    return paths


def render_cover(pdf_path, page_num, out_path, dpi=300):
    doc = fitz.open(pdf_path)
    if page_num < 1 or page_num > len(doc):
        doc.close()
        raise ValueError(
            f"--cover-page {page_num} out of range (PDF has {len(doc)} pages)"
        )
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)
    pix = doc[page_num - 1].get_pixmap(matrix=mat)
    pix.save(out_path)
    doc.close()


# ---------- OCR ----------


def ocr_page(image_path, args):
    cmd = [
        args.llama_bin,
        "-m",
        args.model,
        "--mmproj",
        args.mmproj,
        "--image",
        image_path,
        "-p",
        "document parsing.",
        "--chat-template",
        "deepseek-ocr",
        "--no-jinja",
        "--temp",
        str(args.temp),
        "--flash-attn",
        "off",
        "--no-warmup",
        "-n",
        str(args.max_tokens),
        "-c",
        str(args.context),
        "-ngl",
        str(args.gpu_layers),
        "--dry-multiplier",
        str(args.dry_multiplier),
        "--dry-base",
        "1.75",
        "--dry-allowed-length",
        str(args.dry_allowed_length),
        "--dry-penalty-last-n",
        str(args.dry_penalty_last_n),
        "--dry-sequence-breaker",
        "none",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=args.timeout
        )
    except subprocess.TimeoutExpired:
        return None, "timed out"
    except Exception as e:
        return None, str(e)
    if result.returncode != 0:
        return None, result.stderr[-800:]
    return result.stdout.strip(), None


# Empirically-derived threshold: normal pages produce ~8-20 <|det|> blocks;
# a degenerate repetition loop was observed producing 65-223. An unclosed
# trailing tag means generation was cut off by --max-tokens mid-loop.
REPETITION_TAG_THRESHOLD = 40


def has_repetition_loop(text):
    tag_count = len(re.findall(r"<\|det\|>", text))
    truncated = text.rfind("<|det|>") > text.rfind("<|/det|>")
    return tag_count > REPETITION_TAG_THRESHOLD or truncated


def ocr_all_pages(image_paths, work_dir, args, progress_cb=None):
    raw_dir = os.path.join(work_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    for i, img_path in enumerate(image_paths, 1):
        raw_path = os.path.join(raw_dir, f"page_{i:04d}.txt")
        if args.resume and os.path.exists(raw_path) and os.path.getsize(raw_path) > 0:
            print(f"[{i}/{len(image_paths)}] cached, skipping")
            if progress_cb:
                progress_cb(i, len(image_paths))
            continue

        print(f"[{i}/{len(image_paths)}] OCR {img_path} ...", end=" ", flush=True)
        text, err = ocr_page(img_path, args)
        if text is not None and has_repetition_loop(text):
            print("repetition loop detected, retrying ...", end=" ", flush=True)
            text, err = ocr_page(img_path, args)
            if text is not None and has_repetition_loop(text):
                text, err = None, "repetition loop persisted after retry"

        if text is None:
            print("FAILED")
            print(f"    error: {err}", file=sys.stderr)
        else:
            with open(raw_path, "w", encoding="utf-8") as f:
                f.write(text)
            print("ok")

        if progress_cb:
            progress_cb(i, len(image_paths))


# ---------- clean raw OCR -> markdown ----------


def parse_blocks(raw_text):
    blocks = []
    current_type = None
    current_lines = []

    def flush():
        if current_type is not None:
            text = " ".join(l.strip() for l in current_lines if l.strip())
            if text:
                blocks.append((current_type, text))

    for line in raw_text.splitlines():
        m = DET_RE.match(line.strip())
        if m:
            flush()
            current_type = m.group(1)
            current_lines = [m.group(2)]
        elif line.strip() == "":
            continue
        else:
            current_lines.append(line)
    flush()
    return blocks


def blocks_to_markdown(blocks, detect_title=True):
    md = []
    first_title_group = []
    collecting_first_title = detect_title
    detected_title = None

    for btype, text in blocks:
        if btype == "title" and collecting_first_title:
            first_title_group.append(text)
            continue

        if collecting_first_title:
            collecting_first_title = False
            if first_title_group:
                detected_title = " ".join(first_title_group)
                md.append(f"# {detected_title}\n")

        if btype in SKIP_TYPES:
            continue
        elif btype == "title":
            md.append(f"\n## {text}\n")
        elif btype in ASIDE_TYPES:
            md.append(f"*{text}*\n")
        else:
            md.append(f"{text}\n")

    if detect_title and first_title_group and detected_title is None:
        detected_title = " ".join(first_title_group)
        md.append(f"# {detected_title}\n")

    return "\n".join(md), detected_title


def build_page_markdown(page_num, raw_text, detect_title=None):
    if detect_title is None:
        detect_title = page_num == 1
    blocks = parse_blocks(raw_text)
    return blocks_to_markdown(blocks, detect_title=detect_title)


def build_clean_markdown(image_paths, work_dir):
    raw_dir = os.path.join(work_dir, "raw")
    page_mds = []
    missing = []
    detected_title = None
    seen_first_present_page = False
    for i in range(1, len(image_paths) + 1):
        raw_path = os.path.join(raw_dir, f"page_{i:04d}.txt")
        if not os.path.exists(raw_path):
            missing.append(i)
            continue
        with open(raw_path, "r", encoding="utf-8") as f:
            is_first_present_page = not seen_first_present_page
            seen_first_present_page = True
            page_md, page_title = build_page_markdown(
                i, f.read(), detect_title=is_first_present_page
            )
        if page_title and detected_title is None:
            detected_title = page_title
        page_mds.append(page_md)
    return "\n".join(page_mds), detected_title, missing


# ---------- markdown -> epub ----------


def build_epub(md_path, output_path, title, author, toc_depth, cover_path=None):
    cmd = [
        "pandoc",
        md_path,
        "-o",
        output_path,
        "--metadata",
        f"title={title}",
        "--metadata",
        f"author={author}",
        "--toc",
        f"--toc-depth={toc_depth}",
        "--split-level=1",
    ]
    if cover_path:
        cmd += ["--epub-cover-image", cover_path]
    subprocess.run(cmd, check=True)


