#!/usr/bin/env python3dds
import argparse
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


def ocr_all_pages(image_paths, work_dir, args):
    raw_dir = os.path.join(work_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    for i, img_path in enumerate(image_paths, 1):
        raw_path = os.path.join(raw_dir, f"page_{i:04d}.txt")
        if args.resume and os.path.exists(raw_path) and os.path.getsize(raw_path) > 0:
            print(f"[{i}/{len(image_paths)}] cached, skipping")
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
            continue

        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(text)
        print("ok")


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


def blocks_to_markdown(blocks):
    md = []
    first_title_group = []
    collecting_first_title = True
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

    if first_title_group and detected_title is None:
        detected_title = " ".join(first_title_group)
        md.append(f"# {detected_title}\n")

    return "\n".join(md), detected_title


def build_clean_markdown(image_paths, work_dir):
    raw_dir = os.path.join(work_dir, "raw")
    all_blocks = []
    missing = []
    for i in range(1, len(image_paths) + 1):
        raw_path = os.path.join(raw_dir, f"page_{i:04d}.txt")
        if not os.path.exists(raw_path):
            missing.append(i)
            continue
        with open(raw_path, "r", encoding="utf-8") as f:
            all_blocks.extend(parse_blocks(f.read()))
    md, detected_title = blocks_to_markdown(all_blocks)
    return md, detected_title, missing


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


# ---------- main ----------


def main():
    p = argparse.ArgumentParser(
        description="Scanned PDF -> Unlimited-OCR -> EPUB, one command."
    )
    p.add_argument("pdf", help="input scanned PDF")
    p.add_argument(
        "-o", "--output", help="output .epub path (default: <pdf-name>.epub)"
    )
    p.add_argument(
        "--title", default=None, help="book title (default: auto-detected from page 1)"
    )
    p.add_argument("--author", default="Unknown", help="book author")
    p.add_argument("--llama-bin", default="llama.cpp/build/bin/llama-mtmd-cli")
    p.add_argument(
        "--model",
        default="models/baidu.Unlimited-OCR.Q8_0.gguf",
        help="path to Unlimited-OCR GGUF quant",
    )
    p.add_argument(
        "--mmproj",
        default="models/mmproj-baidu.Unlimited-OCR.f16.gguf",
        help="path to mmproj GGUF",
    )
    p.add_argument("--dpi", type=int, default=200)
    p.add_argument(
        "--gpu-layers", "-ngl", type=int, default=10, help="0 = CPU/RAM only"
    )
    p.add_argument("--context", "-c", type=int, default=6144)
    p.add_argument("--max-tokens", "-n", type=int, default=4096)
    p.add_argument(
        "--temp",
        type=float,
        default=0.2,
        help="sampling temperature; 0 (greedy) reliably repetition-loops on some pages",
    )
    p.add_argument("--dry-multiplier", type=float, default=1.2)
    p.add_argument("--dry-allowed-length", type=int, default=8)
    p.add_argument("--dry-penalty-last-n", type=int, default=256)
    p.add_argument("--timeout", type=int, default=300, help="seconds allowed per page")
    p.add_argument(
        "--output-dir",
        default="output",
        help="base dir for the epub and per-book work dir",
    )
    p.add_argument(
        "--work-dir",
        default=None,
        help="cache dir (page images + raw OCR); reused by --resume "
        "(default: <output-dir>/<book-name>/ocr_work)",
    )
    p.add_argument(
        "--resume", action="store_true", help="skip pages already OCR'd in --work-dir"
    )
    p.add_argument(
        "--cover-page",
        type=int,
        default=None,
        help="PDF page number to use as EPUB cover",
    )
    p.add_argument("--toc-depth", type=int, default=1)
    args = p.parse_args()

    book_name = os.path.splitext(os.path.basename(args.pdf))[0]

    if not args.output:
        args.output = os.path.join(args.output_dir, book_name + ".epub")
    if not args.work_dir:
        args.work_dir = os.path.join(args.output_dir, book_name, "ocr_work")

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    os.makedirs(args.work_dir, exist_ok=True)

    print(f"Rendering pages from {args.pdf} at {args.dpi} DPI...")
    image_paths = render_pages(args.pdf, os.path.join(args.work_dir, "pages"), args.dpi)
    print(f"{len(image_paths)} pages.")

    ocr_all_pages(image_paths, args.work_dir, args)

    print("Building markdown from OCR output...")
    md, detected_title, missing = build_clean_markdown(image_paths, args.work_dir)
    md_path = os.path.join(args.work_dir, "clean.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    title = args.title or detected_title or "Untitled"

    cover_path = None
    if args.cover_page:
        cover_path = os.path.join(args.work_dir, "cover.png")
        render_cover(args.pdf, args.cover_page, cover_path)

    print(f"Building EPUB -> {args.output}")
    build_epub(md_path, args.output, title, args.author, args.toc_depth, cover_path)

    print(f"\nDone: {args.output}")
    print(f"Title: {title} | Author: {args.author}")
    if missing:
        print(
            f"WARNING: {len(missing)} page(s) missing from the ebook (OCR failed): {missing}"
        )
        print("Re-run the same command with --resume to retry just those pages.")


if __name__ == "__main__":
    main()
