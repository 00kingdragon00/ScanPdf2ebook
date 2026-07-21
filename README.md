# ScanPdf2ebook

Scanned PDF -> OCR (llama.cpp Unlimited-OCR) -> EPUB, one command.

## Requirements

- `pip install pymupdf` (or `uv sync`)
- `pandoc` (`apt install pandoc`)
- llama.cpp build (PR #17400 branch) with an Unlimited-OCR GGUF quant + mmproj file

## Usage

```bash
python3 main.py mybook.pdf -o mybook.epub \
    --model ./models/Unlimited-OCR-Q8_0.gguf \
    --mmproj ./models/mmproj-Unlimited-OCR-F16.gguf \
    --title "My Book Title" --author "Author Name" \
    --gpu-layers 10 --cover-page 1
```

By default the EPUB and its OCR cache are saved under `output/<book-name>/`,
keyed off the input PDF's filename, so running multiple books never collides:

```
output/
  mybook/
    ocr_work/       # page images + raw OCR (cache)
  mybook.epub
```

Resume after a crash/interrupt (only re-OCRs missing pages):

```bash
python3 main.py mybook.pdf --resume \
    --model ./models/Unlimited-OCR-Q8_0.gguf \
    --mmproj ./models/mmproj-Unlimited-OCR-F16.gguf
```

## Web UI

Upload a PDF, watch OCR run, review/edit every page against its source image, then convert:

```bash
uv run python webapp.py
```

Open `http://127.0.0.1:5000/`. Uploaded PDFs are saved to `input/<name>.pdf`. The
review page won't let you convert until every page is checked "Approved" (enforced
server-side, not just in the browser). Pages where OCR failed show an empty,
still-editable block flagged "OCR FAILED" instead of being hidden.

## Key options

| Flag | Default | Meaning |
|---|---|---|
| `-o, --output` | `<pdf-name>.epub` | output EPUB path |
| `--title` | auto-detected | book title |
| `--author` | `Unknown` | book author |
| `--dpi` | `200` | page render DPI |
| `--gpu-layers, -ngl` | `10` | GPU layers (0 = CPU only) |
| `--context, -c` | `6144` | context size |
| `--output-dir` | `output` | base dir for epub + per-book work dir |
| `--work-dir` | `<output-dir>/<book-name>/ocr_work` | cache dir (page images + raw OCR) |
| `--cover-page` | none | PDF page number to use as cover |
