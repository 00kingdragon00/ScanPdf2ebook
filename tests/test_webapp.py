import io
import os
import sys
from pathlib import Path

# Add parent directory to path to import webapp
sys.path.insert(0, str(Path(__file__).parent.parent))

import webapp


def test_upload_form_page_loads(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"form" in resp.data.lower()


def test_upload_saves_pdf_to_input_dir(client, tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "run_pipeline", lambda book_name, source_kind, source_value, args: None)

    data = {"pdf": (io.BytesIO(b"%PDF-1.4 fake content"), "mybook.pdf")}
    resp = client.post("/upload", data=data, content_type="multipart/form-data")

    assert resp.status_code in (302, 303)
    saved_path = os.path.join(str(tmp_path / "input"), "mybook.pdf")
    assert os.path.exists(saved_path)


def test_upload_rejects_non_pdf(client):
    data = {"pdf": (io.BytesIO(b"not a pdf"), "notes.txt")}
    resp = client.post("/upload", data=data, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_run_pipeline_updates_progress_and_writes_raw_files(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "INPUT_DIR", str(tmp_path / "input"))
    monkeypatch.setattr(webapp, "OUTPUT_DIR", str(tmp_path / "output"))
    webapp.PROGRESS.clear()

    def fake_ocr_page(image_path, args):
        return "<|det|>text [0, 0, 10, 10]<|/det|>fake text\n", None

    monkeypatch.setattr(webapp.pipeline, "ocr_page", fake_ocr_page)

    import fitz

    os.makedirs(str(tmp_path / "input"), exist_ok=True)
    pdf_path = str(tmp_path / "input" / "tinybook.pdf")
    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page()
        page.insert_text((72, 72), "hello page")
    doc.save(pdf_path)
    doc.close()

    work_dir = os.path.join(str(tmp_path / "output"), "tinybook", "ocr_work")
    args = webapp.ocr_args_from_form({}, work_dir)
    webapp.run_pipeline("tinybook", "pdf", pdf_path, args)

    state = webapp.PROGRESS["tinybook"]
    assert state == {"done": 2, "total": 2, "finished": True, "error": None, "phase": "ocr"}

    raw_dir = os.path.join(str(tmp_path / "output"), "tinybook", "ocr_work", "raw")
    assert os.path.exists(os.path.join(raw_dir, "page_0001.txt"))
    assert os.path.exists(os.path.join(raw_dir, "page_0002.txt"))


def test_status_endpoint_returns_progress(client):
    webapp.PROGRESS["somebook"] = {"done": 3, "total": 10, "finished": False, "error": None}
    resp = client.get("/status/somebook")
    assert resp.get_json() == {"done": 3, "total": 10, "finished": False, "error": None}


def test_status_endpoint_unknown_book_returns_zero_state(client):
    resp = client.get("/status/never-uploaded")
    assert resp.get_json() == {"done": 0, "total": 0, "finished": False, "phase": None}


def test_progress_page_loads(client):
    resp = client.get("/progress/somebook")
    assert resp.status_code == 200
    assert b"somebook" in resp.data


def test_run_pipeline_records_error_and_finishes_on_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "INPUT_DIR", str(tmp_path / "input"))
    monkeypatch.setattr(webapp, "OUTPUT_DIR", str(tmp_path / "output"))
    webapp.PROGRESS.clear()

    def fake_render_pages(pdf_path, out_dir, dpi):
        raise RuntimeError("corrupt pdf")

    monkeypatch.setattr(webapp.pipeline, "render_pages", fake_render_pages)

    work_dir = os.path.join(str(tmp_path / "output"), "brokenbook", "ocr_work")
    args = webapp.ocr_args_from_form({}, work_dir)
    webapp.run_pipeline("brokenbook", "pdf", str(tmp_path / "input" / "brokenbook.pdf"), args)

    state = webapp.PROGRESS["brokenbook"]
    assert state["finished"] is True
    assert state["error"] is not None
    assert "corrupt pdf" in state["error"]


def _write_page_fixture(tmp_path, book, page_num, raw_text=None):
    work_dir = tmp_path / "output" / book / "ocr_work"
    pages_dir = work_dir / "pages"
    raw_dir = work_dir / "raw"
    pages_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    (pages_dir / f"page_{page_num:04d}.png").write_bytes(b"\x89PNG\r\n\x1a\n fake png bytes")
    if raw_text is not None:
        (raw_dir / f"page_{page_num:04d}.txt").write_text(raw_text, encoding="utf-8")


def test_review_page_shows_all_pages_with_text_and_flags_failed(client, tmp_path):
    book = "reviewbook"
    _write_page_fixture(tmp_path, book, 1, raw_text=(
        "<|det|>title [0, 0, 100, 10]<|/det|>Book Title\n"
        "<|det|>text [0, 10, 100, 20]<|/det|>Page one text.\n"
    ))
    _write_page_fixture(tmp_path, book, 2, raw_text=None)  # OCR failed, no raw file

    resp = client.get(f"/review/{book}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Page one text." in body
    assert "OCR FAILED" in body
    assert "page_0001.png" in body
    assert "page_0002.png" in body


def test_review_detects_title_from_first_present_page_when_page_1_missing(client, tmp_path):
    book = "reviewbook3"
    _write_page_fixture(tmp_path, book, 1, raw_text=None)  # page 1 OCR failed
    _write_page_fixture(tmp_path, book, 2, raw_text=(
        "<|det|>title [0, 0, 100, 10]<|/det|>Title From Page Two\n"
        "<|det|>text [0, 10, 100, 20]<|/det|>Page two text.\n"
    ))

    resp = client.get(f"/review/{book}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'value="Title From Page Two"' in body


def test_review_returns_400_when_pages_dir_missing(client):
    resp = client.get("/review/no-such-book")
    assert resp.status_code == 400


def test_page_image_route_serves_png(client, tmp_path):
    book = "reviewbook2"
    _write_page_fixture(tmp_path, book, 1, raw_text="<|det|>text [0,0,1,1]<|/det|>x\n")
    resp = client.get(f"/pages/{book}/page_0001.png")
    assert resp.status_code == 200
    assert resp.data.startswith(b"\x89PNG")


def test_convert_rejects_when_not_all_pages_approved(client, tmp_path):
    book = "convertbook1"
    _write_page_fixture(tmp_path, book, 1, raw_text="<|det|>text [0,0,1,1]<|/det|>hello\n")
    _write_page_fixture(tmp_path, book, 2, raw_text="<|det|>text [0,0,1,1]<|/det|>world\n")

    resp = client.post(f"/convert/{book}", data={
        "total": "2",
        "title": "T", "author": "A",
        "text_1": "hello", "approved_1": "on",
        "text_2": "world",  # page 2 not approved
    })
    assert resp.status_code == 400


def test_convert_ignores_client_supplied_total_and_uses_real_page_count(client, tmp_path):
    # Vulnerability repro: 3 real pages on disk, client lies with total=1 and only
    # approves page 1. Server must derive total from disk (3), not trust the form,
    # so pages 2 and 3 are still checked and the unapproved page is caught.
    book = "convertbook3"
    _write_page_fixture(tmp_path, book, 1, raw_text="<|det|>text [0,0,1,1]<|/det|>one\n")
    _write_page_fixture(tmp_path, book, 2, raw_text="<|det|>text [0,0,1,1]<|/det|>two\n")
    _write_page_fixture(tmp_path, book, 3, raw_text="<|det|>text [0,0,1,1]<|/det|>three\n")

    resp = client.post(f"/convert/{book}", data={
        "total": "1",  # lie: claim only 1 page exists
        "title": "T", "author": "A",
        "text_1": "one", "approved_1": "on",
        # page 2 and 3 omitted/not approved
    })
    assert resp.status_code == 400
    assert b"Page 2" in resp.data


def test_convert_returns_400_when_pages_dir_missing(client):
    resp = client.post("/convert/no-such-book", data={"title": "T", "author": "A"})
    assert resp.status_code == 400


def test_convert_builds_epub_from_edited_text_when_all_approved(client, tmp_path, monkeypatch):
    book = "convertbook2"
    _write_page_fixture(tmp_path, book, 1, raw_text="<|det|>text [0,0,1,1]<|/det|>original\n")

    calls = []
    monkeypatch.setattr(
        webapp.pipeline, "build_epub",
        lambda md_path, output_path, title, author, toc_depth, cover_path=None: calls.append(
            (md_path, output_path, title, author)
        ),
    )

    resp = client.post(f"/convert/{book}", data={
        "total": "1",
        "title": "My Title", "author": "My Author",
        "text_1": "edited replacement text", "approved_1": "on",
    })

    assert resp.status_code == 200
    assert len(calls) == 1
    md_path, output_path, title, author = calls[0]
    assert title == "My Title"
    assert author == "My Author"
    assert output_path == os.path.join(str(tmp_path / "output"), f"{book}.epub")
    with open(md_path, encoding="utf-8") as fh:
        assert fh.read() == "edited replacement text"


def test_book_name_from_youtube_url_extracts_video_id():
    assert webapp.book_name_from_youtube_url("https://www.youtube.com/watch?v=abc123XYZ") == "abc123XYZ"
    assert webapp.book_name_from_youtube_url("https://youtu.be/abc123XYZ") == "abc123XYZ"
    assert webapp.book_name_from_youtube_url("https://www.youtube.com/embed/abc123XYZ") == "abc123XYZ"


def test_book_name_from_youtube_url_falls_back_when_no_id_found():
    assert webapp.book_name_from_youtube_url("https://example.com/not-a-youtube-link") == "youtube-video"


def test_upload_rejects_when_neither_pdf_nor_url_given(client):
    resp = client.post("/upload", data={})
    assert resp.status_code == 400


def test_upload_rejects_when_both_pdf_and_url_given(client):
    data = {
        "pdf": (io.BytesIO(b"%PDF-1.4 fake content"), "mybook.pdf"),
        "youtube_url": "https://youtu.be/abc123",
    }
    resp = client.post("/upload", data=data, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_upload_accepts_youtube_url_and_starts_pipeline_with_youtube_source(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        webapp, "run_pipeline",
        lambda book_name, source_kind, source_value, args: calls.append(
            (book_name, source_kind, source_value)
        ),
    )

    resp = client.post("/upload", data={"youtube_url": "https://youtu.be/abc123XYZ"})

    assert resp.status_code in (302, 303)
    assert len(calls) == 1
    book_name, source_kind, source_value = calls[0]
    assert book_name == "abc123XYZ"
    assert source_kind == "youtube"
    assert source_value == "https://youtu.be/abc123XYZ"


def test_run_pipeline_youtube_source_tracks_phases_and_calls_video_module(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "INPUT_DIR", str(tmp_path / "input"))
    monkeypatch.setattr(webapp, "OUTPUT_DIR", str(tmp_path / "output"))
    webapp.PROGRESS.clear()

    def fake_extract_pages_from_youtube(url, work_dir, phase_cb=None):
        if phase_cb:
            phase_cb("downloading")
            phase_cb("extracting frames")
        page_path = os.path.join(work_dir, "pages", "page_0001.png")
        os.makedirs(os.path.dirname(page_path), exist_ok=True)
        with open(page_path, "wb") as fh:
            fh.write(b"fake png")
        return [page_path]

    monkeypatch.setattr(webapp.video, "extract_pages_from_youtube", fake_extract_pages_from_youtube)

    def fake_ocr_page(image_path, args):
        return "<|det|>text [0, 0, 10, 10]<|/det|>fake text\n", None

    monkeypatch.setattr(webapp.pipeline, "ocr_page", fake_ocr_page)

    work_dir = os.path.join(str(tmp_path / "output"), "vidbook", "ocr_work")
    args = webapp.ocr_args_from_form({}, work_dir)
    webapp.run_pipeline("vidbook", "youtube", "https://youtu.be/abc123", args)

    state = webapp.PROGRESS["vidbook"]
    assert state["finished"] is True
    assert state["error"] is None
    assert state["phase"] == "ocr"
    assert state["total"] == 1
    assert state["done"] == 1


def test_status_endpoint_includes_phase(client):
    webapp.PROGRESS["somebook"] = {
        "done": 3, "total": 10, "finished": False, "error": None, "phase": "ocr",
    }
    resp = client.get("/status/somebook")
    assert resp.get_json()["phase"] == "ocr"


def test_review_page_shows_gap_warning_when_page_numbers_skip(client, tmp_path):
    book = "gapbook"
    _write_page_fixture(tmp_path, book, 1, raw_text=(
        "<|det|>text [0, 10, 100, 20]<|/det|>Page one text.\n"
        "<|det|>footer [0, 90, 100, 100]<|/det|>10\n"
    ))
    _write_page_fixture(tmp_path, book, 2, raw_text=(
        "<|det|>text [0, 10, 100, 20]<|/det|>Page two text.\n"
        "<|det|>footer [0, 90, 100, 100]<|/det|>12\n"
    ))

    resp = client.get(f"/review/{book}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "10" in body and "12" in body
    assert "missing" in body.lower() or "gap" in body.lower()


def test_review_page_shows_no_gap_warning_when_page_numbers_consecutive(client, tmp_path):
    book = "nogapbook"
    _write_page_fixture(tmp_path, book, 1, raw_text=(
        "<|det|>text [0, 10, 100, 20]<|/det|>Page one text.\n"
        "<|det|>footer [0, 90, 100, 100]<|/det|>10\n"
    ))
    _write_page_fixture(tmp_path, book, 2, raw_text=(
        "<|det|>text [0, 10, 100, 20]<|/det|>Page two text.\n"
        "<|det|>footer [0, 90, 100, 100]<|/det|>11\n"
    ))

    resp = client.get(f"/review/{book}")
    assert resp.status_code == 200
    assert "gap-warning" not in resp.data.decode()
