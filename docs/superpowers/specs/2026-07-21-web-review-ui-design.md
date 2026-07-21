# Web review UI: upload + per-page approval gate before EPUB conversion

## Context

`main.py` is a CLI pipeline: PDF -> page images -> OCR (llama.cpp Unlimited-OCR) -> merged markdown -> EPUB (pandoc). Two gaps prompted this feature:

1. Input PDFs are manually dropped in the project root (no dedicated place for them).
2. OCR output goes straight to EPUB with no human review step, so OCR defects (e.g. the repetition-loop bug fixed in `f950c8c`, or a silently-missing paragraph) can end up baked into the final book unnoticed.

This spec adds a local Flask web UI with one review-and-approve gate between OCR and EPUB conversion, plus a proper `input/` folder for uploads.

## Non-goals

- Not replacing the CLI (`main.py` keeps working standalone).
- Not exposing every CLI OCR flag (model path, temp, dry-* settings) in the web UI — it uses the same defaults as the CLI.
- Not multi-user / authenticated — local, single-shot, single-book-at-a-time use.

## Architecture

New `webapp.py` alongside `main.py`. It imports and reuses `main.py`'s existing functions rather than duplicating logic:
- `render_pages` — PDF -> page PNGs
- `ocr_all_pages` / `ocr_page` / `has_repetition_loop` — OCR with existing retry-on-loop-detection
- `parse_blocks` — raw OCR text -> typed blocks
- `build_epub` — markdown -> EPUB via pandoc

One refactor to `main.py`: extract a **per-page** markdown builder. `blocks_to_markdown` currently runs once over the whole book's concatenated blocks. Split it so the review page can render and edit each page's markdown independently, then the final "convert" step joins the approved per-page text in page order before calling `build_epub`. The whole-book CLI path continues to produce the same merged output as today (join-then-build vs. current build-then-nothing-in-between is behaviorally identical for the CLI).

New `input/` folder (gitignored, same treatment as `output/`) holds uploaded PDFs.

## Flow

1. **Upload page (`GET /`)** — file picker for a PDF. `POST /upload` saves it to `input/<filename>.pdf` (overwriting if the name already exists) and starts a background thread running `render_pages` + `ocr_all_pages` for that book, using the same defaults main.py's CLI uses (model/mmproj paths, `temp 0.2`, DRY settings, etc.).

2. **Progress page** — polls `GET /status/<book>` (in-memory or simple JSON-file counter updated by the background thread) showing "OCR page X/N". Auto-redirects to the review gate when OCR finishes.

3. **Review gate (`GET /review/<book>`)** — the one approval gate. Single page, one block per PDF page in page order:
   - source page image (from `output/<book>/ocr_work/pages/page_NNNN.png`)
   - editable textarea pre-filled with that page's markdown (built via the new per-page markdown builder from `output/<book>/ocr_work/raw/page_NNNN.txt`)
   - an "approved" checkbox, unchecked by default
   - pages with no raw OCR file (OCR failed after retry) render with an empty textarea and a visible "OCR failed" flag instead of being hidden or skipped

4. **Convert (`POST /convert/<book>`)** — client-side: the Convert button stays disabled until every checkbox is checked. Server-side: re-validates that all pages are marked approved before proceeding (never trust the client-only check). On success:
   - edited per-page texts are joined in page order into `clean.md`
   - existing `build_epub` runs unchanged (title/author fields editable on this page, same as CLI's `--title`/`--author`, defaulting the same way: explicit title > auto-detected > "Untitled")
   - page shows a success message and a link to the finished `.epub` under `output/<book>/`

## Error handling

- OCR failures (repetition loop persisted after retry, timeout) are shown, not hidden — matches the existing `missing`-pages warning philosophy already in `main.py`. User can type replacement text manually or leave blank, then approve like any other page.
- "All approved" is enforced both client-side (disabled button, cheap UX) and server-side (real gate) on the convert endpoint.
- Re-uploading a PDF with the same filename overwrites `input/<filename>.pdf`; OCR re-run reuses `output/<book>/ocr_work` the same way the CLI's `--resume` cache already does (this feature doesn't change that caching behavior).

## Testing / verification

Manual end-to-end pass using the existing test book (`HOW TO ATTAIN ANYTHING YOU WANT THROUGH MIND VISUALISATIONS.pdf`, currently sitting in the project root — moving it into `input/` is part of exercising this feature):

1. Upload it through the UI; confirm it lands in `input/`, not the project root.
2. Confirm OCR runs and progress updates page-by-page.
3. Confirm the review gate shows all 16 pages with their source images.
4. Edit page 9's text (the page that historically hit the repetition-loop bug) and confirm the edit is what ends up in the final EPUB, not the original OCR text.
5. Confirm Convert is blocked until every page is checked, and unblocks correctly once all are.
6. Confirm a deliberately-failed page (or a re-triggered failure) shows the "OCR failed" flag with an empty, still-editable/approvable block.
