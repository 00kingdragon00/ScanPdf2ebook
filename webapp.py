#!/usr/bin/env python3
import argparse
import os
import threading

from flask import Flask, redirect, render_template, request, send_from_directory, url_for

import main as pipeline

app = Flask(__name__)

INPUT_DIR = "input"
OUTPUT_DIR = "output"

PROGRESS = {}
PROGRESS_LOCK = threading.Lock()


def default_ocr_args(work_dir):
    # mirrors main.py's CLI argparse defaults (main.py:252-277)
    return argparse.Namespace(
        llama_bin="llama.cpp/build/bin/llama-mtmd-cli",
        model="models/baidu.Unlimited-OCR.Q8_0.gguf",
        mmproj="models/mmproj-baidu.Unlimited-OCR.f16.gguf",
        dpi=200,
        gpu_layers=10,
        context=6144,
        max_tokens=4096,
        temp=0.2,
        dry_multiplier=1.2,
        dry_allowed_length=8,
        dry_penalty_last_n=256,
        timeout=300,
        resume=False,
        work_dir=work_dir,
    )


def run_pipeline(book_name, pdf_path):
    work_dir = os.path.join(OUTPUT_DIR, book_name, "ocr_work")
    os.makedirs(work_dir, exist_ok=True)
    args = default_ocr_args(work_dir)

    with PROGRESS_LOCK:
        PROGRESS[book_name] = {"done": 0, "total": 0, "finished": False, "error": None}

    try:
        image_paths = pipeline.render_pages(pdf_path, os.path.join(work_dir, "pages"), args.dpi)
        with PROGRESS_LOCK:
            PROGRESS[book_name]["total"] = len(image_paths)

        def on_page_done(i, total):
            with PROGRESS_LOCK:
                PROGRESS[book_name]["done"] = i
                PROGRESS[book_name]["total"] = total

        pipeline.ocr_all_pages(image_paths, work_dir, args, progress_cb=on_page_done)
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
    if f is None or f.filename == "":
        return "No file uploaded", 400
    if not f.filename.lower().endswith(".pdf"):
        return "Only .pdf files are accepted", 400

    os.makedirs(INPUT_DIR, exist_ok=True)
    filename = os.path.basename(f.filename)
    pdf_path = os.path.join(INPUT_DIR, filename)
    f.save(pdf_path)

    book_name = os.path.splitext(filename)[0]
    thread = threading.Thread(target=run_pipeline, args=(book_name, pdf_path), daemon=True)
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
            return {"done": 0, "total": 0, "finished": False}
        return dict(state)


@app.route("/review/<book>")
def review(book):
    work_dir = os.path.join(OUTPUT_DIR, book, "ocr_work")
    pages_dir = os.path.join(work_dir, "pages")
    raw_dir = os.path.join(work_dir, "raw")
    total = len([n for n in os.listdir(pages_dir) if n.endswith(".png")])

    pages = []
    detected_title = None
    for i in range(1, total + 1):
        raw_path = os.path.join(raw_dir, f"page_{i:04d}.txt")
        image_name = f"page_{i:04d}.png"
        if os.path.exists(raw_path):
            with open(raw_path, "r", encoding="utf-8") as fh:
                raw_text = fh.read()
            md, title = pipeline.build_page_markdown(i, raw_text)
            if title and detected_title is None:
                detected_title = title
            failed = False
        else:
            md = ""
            failed = True
        pages.append({"num": i, "image": image_name, "text": md, "failed": failed})

    return render_template(
        "review.html", book=book, pages=pages, detected_title=detected_title or "Untitled"
    )


@app.route("/pages/<book>/<filename>")
def page_image(book, filename):
    pages_dir = os.path.join(OUTPUT_DIR, book, "ocr_work", "pages")
    return send_from_directory(pages_dir, filename)


@app.route("/convert/<book>", methods=["POST"])
def convert(book):
    total = int(request.form["total"])

    texts = []
    for i in range(1, total + 1):
        if request.form.get(f"approved_{i}") != "on":
            return f"Page {i} is not approved", 400
        texts.append(request.form.get(f"text_{i}", ""))

    work_dir = os.path.join(OUTPUT_DIR, book, "ocr_work")
    md_path = os.path.join(work_dir, "clean.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n\n".join(texts))

    title = request.form.get("title") or "Untitled"
    author = request.form.get("author") or "Unknown"
    output_path = os.path.join(OUTPUT_DIR, f"{book}.epub")

    pipeline.build_epub(md_path, output_path, title, author, toc_depth=1)

    return render_template("done.html", book=book, output_path=output_path)


if __name__ == "__main__":
    app.run(debug=True)
