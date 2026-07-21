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
    monkeypatch.setattr(webapp, "run_pipeline", lambda book_name, pdf_path: None)

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

    webapp.run_pipeline("tinybook", pdf_path)

    state = webapp.PROGRESS["tinybook"]
    assert state == {"done": 2, "total": 2, "finished": True, "error": None}

    raw_dir = os.path.join(str(tmp_path / "output"), "tinybook", "ocr_work", "raw")
    assert os.path.exists(os.path.join(raw_dir, "page_0001.txt"))
    assert os.path.exists(os.path.join(raw_dir, "page_0002.txt"))


def test_status_endpoint_returns_progress(client):
    webapp.PROGRESS["somebook"] = {"done": 3, "total": 10, "finished": False, "error": None}
    resp = client.get("/status/somebook")
    assert resp.get_json() == {"done": 3, "total": 10, "finished": False, "error": None}


def test_status_endpoint_unknown_book_returns_zero_state(client):
    resp = client.get("/status/never-uploaded")
    assert resp.get_json() == {"done": 0, "total": 0, "finished": False}


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

    webapp.run_pipeline("brokenbook", str(tmp_path / "input" / "brokenbook.pdf"))

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


def test_page_image_route_serves_png(client, tmp_path):
    book = "reviewbook2"
    _write_page_fixture(tmp_path, book, 1, raw_text="<|det|>text [0,0,1,1]<|/det|>x\n")
    resp = client.get(f"/pages/{book}/page_0001.png")
    assert resp.status_code == 200
    assert resp.data.startswith(b"\x89PNG")
