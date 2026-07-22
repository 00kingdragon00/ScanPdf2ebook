# ScanPdf2ebook

Scanned PDF -> OCR (llama.cpp Unlimited-OCR) -> EPUB, via a local web UI.

## Requirements

- `uv sync` (installs Flask, PyMuPDF, yt-dlp, imagehash)
- `pandoc` (`apt install pandoc`)
- `ffmpeg` (`apt install ffmpeg`) — only needed for the YouTube source
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

### YouTube source

Instead of a PDF, paste a YouTube URL (a screen-recording of an ebook —
physical-book filming with camera perspective/glare isn't supported). Frames
are extracted at scene changes (ffmpeg), near-duplicate frames from the same
page are collapsed (perceptual hashing), and the results feed into the exact
same OCR/review/convert flow as a PDF. Progress shows "Downloading video...",
then "Extracting frames...", then normal OCR page-by-page progress.

The right scene-change sensitivity is content-dependent — a video's page-turn
transitions can register very differently on ffmpeg's scene-change metric
depending on the source. If almost no pages get extracted, lower **Scene-change
sensitivity** in Settings (try 0.02-0.05); if too many near-duplicate frames
show up, raise it. **Dedup hash distance** controls how aggressively
consecutive similar frames get collapsed into one page.

The review page also shows a warning banner if it detects a gap in printed
page numbers (e.g. page 12 followed by page 14, with no 13) — useful for
catching a frame a video/OCR pass missed. This works for PDF books too,
whenever OCR picks up a page-number footer.

### Output layout

The EPUB and its OCR cache are saved under `output/<book-name>/`, keyed off
the uploaded PDF's filename (or the video's ID for a YouTube source), so
converting multiple books never collides:

```
output/
  mybook/
    ocr_work/       # page images + raw OCR (cache)
  mybook.epub
```
