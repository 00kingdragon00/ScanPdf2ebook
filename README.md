# ScanPdf2ebook

Scanned PDF -> OCR (llama.cpp Unlimited-OCR) -> EPUB, via a local web UI.

## Requirements

- `uv sync` (installs Flask + PyMuPDF)
- `pandoc` (`apt install pandoc`)
- llama.cpp build (PR #17400 branch) with an Unlimited-OCR GGUF quant + mmproj file

## Usage

Start the app:

```bash
uv run scanpdftoepub
```

Open `http://127.0.0.1:5000/`. Upload a PDF (saved to `input/<name>.pdf`, never
the project root), watch OCR run with live progress, review/edit every page
against its source image, then convert — the "Convert to EPUB" gate is
enforced server-side, not just in the browser, so every page must be checked
"Approved" before conversion is allowed. Pages where OCR failed show an
empty, still-editable block flagged "OCR FAILED" instead of being hidden.

Click **Settings** on the upload page to override OCR tuning (model path,
mmproj path, GPU layers, context size, temperature, DRY sampling settings,
timeout, etc.) — these used to be CLI flags; there is no command-line
interface anymore, everything is driven from the browser. Title, author,
cover page, and table-of-contents depth are set on the review page before
converting.

The EPUB and its OCR cache are saved under `output/<book-name>/`, keyed off
the uploaded PDF's filename, so converting multiple books never collides:

```
output/
  mybook/
    ocr_work/       # page images + raw OCR (cache)
  mybook.epub
```
