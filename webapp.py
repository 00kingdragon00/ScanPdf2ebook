#!/usr/bin/env python3
import argparse
import os
import re
import threading

from flask import Flask, redirect, render_template, request, send_from_directory, url_for

import main as pipeline
import video

app = Flask(__name__)

INPUT_DIR = "input"
OUTPUT_DIR = "output"

PROGRESS = {}
PROGRESS_LOCK = threading.Lock()


# name -> (default, type). Defaults match what used to be main.py's CLI
# argparse defaults; now the user overrides them via the upload page's
# settings dialog instead of CLI flags.
OCR_SETTINGS = {
    "llama_bin": ("llama.cpp/build/bin/llama-mtmd-cli", str),
    "model": ("models/baidu.Unlimited-OCR.Q8_0.gguf", str),
    "mmproj": ("models/mmproj-baidu.Unlimited-OCR.f16.gguf", str),
    "dpi": (200, int),
    "gpu_layers": (10, int),
    "context": (6144, int),
    "max_tokens": (4096, int),
    "temp": (0.2, float),
    "dry_multiplier": (1.2, float),
    "dry_allowed_length": (8, int),
    "dry_penalty_last_n": (256, int),
    "timeout": (300, int),
}


def ocr_args_from_form(form, work_dir):
    values = {}
    for key, (default, cast) in OCR_SETTINGS.items():
        raw = form.get(key)
        values[key] = cast(raw) if raw not in (None, "") else default
    values["resume"] = False
    values["work_dir"] = work_dir
    return argparse.Namespace(**values)


# YouTube-only settings: how sensitive scene-change frame extraction is, and
# how aggressively near-duplicate frames get collapsed. Defaults come from
# video.py's own empirically-calibrated defaults.
VIDEO_SETTINGS = {
    "scene_threshold": (video.DEFAULT_SCENE_THRESHOLD, float),
    "hash_distance_threshold": (video.DEFAULT_HASH_DISTANCE_THRESHOLD, int),
}


def video_settings_from_form(form):
    values = {}
    for key, (default, cast) in VIDEO_SETTINGS.items():
        raw = form.get(key)
        values[key] = cast(raw) if raw not in (None, "") else default
    return values


def find_input_pdf(book_name):
    candidate = os.path.join(INPUT_DIR, book_name + ".pdf")
    return candidate if os.path.exists(candidate) else None


YOUTUBE_ID_RE = re.compile(r"(?:v=|youtu\.be/|embed/)([A-Za-z0-9_-]{6,})")


def book_name_from_youtube_url(url):
    m = YOUTUBE_ID_RE.search(url)
    return m.group(1) if m else "youtube-video"


def run_pipeline(book_name, source_kind, source_value, args, video_settings=None):
    os.makedirs(args.work_dir, exist_ok=True)

    with PROGRESS_LOCK:
        PROGRESS[book_name] = {
            "done": 0,
            "total": 0,
            "finished": False,
            "error": None,
            "phase": "downloading" if source_kind == "youtube" else "ocr",
        }

    try:
        if source_kind == "youtube":
            def on_phase(phase):
                with PROGRESS_LOCK:
                    PROGRESS[book_name]["phase"] = phase

            settings = video_settings or {}
            image_paths = video.extract_pages_from_youtube(
                source_value,
                args.work_dir,
                phase_cb=on_phase,
                scene_threshold=settings.get("scene_threshold", video.DEFAULT_SCENE_THRESHOLD),
                hash_distance_threshold=settings.get(
                    "hash_distance_threshold", video.DEFAULT_HASH_DISTANCE_THRESHOLD
                ),
            )
        else:
            image_paths = pipeline.render_pages(
                source_value, os.path.join(args.work_dir, "pages"), args.dpi
            )

        with PROGRESS_LOCK:
            PROGRESS[book_name]["total"] = len(image_paths)
            PROGRESS[book_name]["phase"] = "ocr"

        def on_page_done(i, total):
            with PROGRESS_LOCK:
                PROGRESS[book_name]["done"] = i
                PROGRESS[book_name]["total"] = total

        pipeline.ocr_all_pages(image_paths, args.work_dir, args, progress_cb=on_page_done)
    except Exception as exc:
        with PROGRESS_LOCK:
            PROGRESS[book_name]["error"] = str(exc)
    finally:
        with PROGRESS_LOCK:
            PROGRESS[book_name]["finished"] = True


@app.route("/", methods=["GET"])
def upload_form():
    return render_template("upload.html")


@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("pdf")
    has_file = f is not None and f.filename != ""
    youtube_url = (request.form.get("youtube_url") or "").strip()

    if has_file and youtube_url:
        return "Provide either a PDF file or a YouTube URL, not both", 400
    if not has_file and not youtube_url:
        return "No file or YouTube URL provided", 400

    if has_file:
        if not f.filename.lower().endswith(".pdf"):
            return "Only .pdf files are accepted", 400
        os.makedirs(INPUT_DIR, exist_ok=True)
        filename = os.path.basename(f.filename)
        pdf_path = os.path.join(INPUT_DIR, filename)
        f.save(pdf_path)
        book_name = os.path.splitext(filename)[0]
        source_kind, source_value = "pdf", pdf_path
    else:
        book_name = book_name_from_youtube_url(youtube_url)
        source_kind, source_value = "youtube", youtube_url

    work_dir = os.path.join(OUTPUT_DIR, book_name, "ocr_work")
    args = ocr_args_from_form(request.form, work_dir)
    video_settings = video_settings_from_form(request.form)

    thread = threading.Thread(
        target=run_pipeline,
        args=(book_name, source_kind, source_value, args, video_settings),
        daemon=True,
    )
    thread.start()

    return redirect(url_for("progress_page", book=book_name))


@app.route("/progress/<book>")
def progress_page(book):
    return render_template("progress.html", book=book)


@app.route("/status/<book>")
def status(book):
    with PROGRESS_LOCK:
        state = PROGRESS.get(book)
        if state is None:
            return {"done": 0, "total": 0, "finished": False, "phase": None}
        return dict(state)


@app.route("/review/<book>")
def review(book):
    work_dir = os.path.join(OUTPUT_DIR, book, "ocr_work")
    pages_dir = os.path.join(work_dir, "pages")
    raw_dir = os.path.join(work_dir, "raw")
    if not os.path.isdir(pages_dir):
        return "Unknown book", 400
    total = len([n for n in os.listdir(pages_dir) if n.endswith(".png")])
    if total == 0:
        return "No pages found", 400

    pages = []
    detected_title = None
    seen_first_present_page = False
    numbered_pages = []
    for i in range(1, total + 1):
        raw_path = os.path.join(raw_dir, f"page_{i:04d}.txt")
        image_name = f"page_{i:04d}.png"
        if os.path.exists(raw_path):
            with open(raw_path, "r", encoding="utf-8") as fh:
                raw_text = fh.read()
            is_first_present_page = not seen_first_present_page
            seen_first_present_page = True
            md, title = pipeline.build_page_markdown(
                i, raw_text, detect_title=is_first_present_page
            )
            if title and detected_title is None:
                detected_title = title
            failed = False
            numbered_pages.append((i, pipeline.extract_page_number(raw_text)))
        else:
            md = ""
            failed = True
            numbered_pages.append((i, None))
        pages.append({"num": i, "image": image_name, "text": md, "failed": failed})

    gaps = pipeline.find_page_number_gaps(numbered_pages)

    return render_template(
        "review.html",
        book=book,
        pages=pages,
        detected_title=detected_title or "Untitled",
        gaps=gaps,
    )


@app.route("/pages/<book>/<filename>")
def page_image(book, filename):
    pages_dir = os.path.join(OUTPUT_DIR, book, "ocr_work", "pages")
    return send_from_directory(pages_dir, filename)


@app.route("/convert/<book>", methods=["POST"])
def convert(book):
    pages_dir = os.path.join(OUTPUT_DIR, book, "ocr_work", "pages")
    if not os.path.isdir(pages_dir):
        return "Unknown book", 400
    total = len([n for n in os.listdir(pages_dir) if n.endswith(".png")])
    if total == 0:
        return "No pages found", 400

    texts = [
        request.form.get(f"text_{i}", "")
        for i in range(1, total + 1)
        if request.form.get(f"approved_{i}") == "on"
    ]
    if not texts:
        return "No pages selected", 400

    work_dir = os.path.join(OUTPUT_DIR, book, "ocr_work")
    md_path = os.path.join(work_dir, "clean.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n\n".join(texts))

    title = request.form.get("title") or "Untitled"
    author = request.form.get("author") or "Unknown"
    toc_depth = int(request.form.get("toc_depth") or 1)
    output_path = os.path.join(OUTPUT_DIR, f"{book}.epub")

    cover_path = None
    cover_page_raw = request.form.get("cover_page")
    if cover_page_raw:
        pdf_path = find_input_pdf(book)
        if pdf_path:
            cover_path = os.path.join(work_dir, "cover.png")
            pipeline.render_cover(pdf_path, int(cover_page_raw), cover_path)

    pipeline.build_epub(md_path, output_path, title, author, toc_depth, cover_path)

    return render_template("done.html", book=book)


@app.route("/download/<book>.epub")
def download(book):
    return send_from_directory(OUTPUT_DIR, f"{book}.epub", as_attachment=True)


def main():
    app.run(debug=True)


if __name__ == "__main__":
    main()
