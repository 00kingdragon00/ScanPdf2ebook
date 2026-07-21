#!/usr/bin/env python3
import os
import threading

from flask import Flask, redirect, render_template, request, url_for

import main as pipeline

app = Flask(__name__)

INPUT_DIR = "input"
OUTPUT_DIR = "output"


def run_pipeline(book_name, pdf_path):
    raise NotImplementedError("wired up in Task 3")


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

    return redirect(url_for("upload_form"))


if __name__ == "__main__":
    app.run(debug=True)
