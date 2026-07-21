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
