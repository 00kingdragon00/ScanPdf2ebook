#!/usr/bin/env python3
import argparse
import os
import threading

from flask import Flask, redirect, render_template, request, url_for

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


if __name__ == "__main__":
    app.run(debug=True)
