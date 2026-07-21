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
    assert state == {"done": 2, "total": 2, "finished": True}

    raw_dir = os.path.join(str(tmp_path / "output"), "tinybook", "ocr_work", "raw")
    assert os.path.exists(os.path.join(raw_dir, "page_0001.txt"))
    assert os.path.exists(os.path.join(raw_dir, "page_0002.txt"))


def test_status_endpoint_returns_progress(client):
    webapp.PROGRESS["somebook"] = {"done": 3, "total": 10, "finished": False}
    resp = client.get("/status/somebook")
    assert resp.get_json() == {"done": 3, "total": 10, "finished": False}


def test_status_endpoint_unknown_book_returns_zero_state(client):
    resp = client.get("/status/never-uploaded")
    assert resp.get_json() == {"done": 0, "total": 0, "finished": False}


def test_progress_page_loads(client):
    resp = client.get("/progress/somebook")
    assert resp.status_code == 200
    assert b"somebook" in resp.data
