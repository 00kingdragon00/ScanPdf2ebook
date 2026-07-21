import os
import sys
from pathlib import Path

# Add parent directory to path to import main
sys.path.insert(0, str(Path(__file__).parent.parent))

import main


PAGE1_RAW = (
    "<|det|>title [0, 0, 100, 10]<|/det|>My Book Title\n"
    "<|det|>text [0, 10, 100, 20]<|/det|>First paragraph of the book.\n"
)
PAGE2_RAW = (
    "<|det|>title [0, 0, 100, 10]<|/det|>Chapter Two\n"
    "<|det|>text [0, 10, 100, 20]<|/det|>Second page content.\n"
)


def test_blocks_to_markdown_detect_title_true_promotes_first_title():
    blocks = main.parse_blocks(PAGE1_RAW)
    md, title = main.blocks_to_markdown(blocks, detect_title=True)
    assert title == "My Book Title"
    assert "# My Book Title" in md
    assert "First paragraph of the book." in md


def test_blocks_to_markdown_detect_title_false_never_promotes():
    blocks = main.parse_blocks(PAGE2_RAW)
    md, title = main.blocks_to_markdown(blocks, detect_title=False)
    assert title is None
    assert "# Chapter Two" not in md
    assert "## Chapter Two" in md


def test_build_page_markdown_only_detects_title_on_page_1():
    md1, title1 = main.build_page_markdown(1, PAGE1_RAW)
    md2, title2 = main.build_page_markdown(2, PAGE2_RAW)
    assert title1 == "My Book Title"
    assert title2 is None
    assert "# My Book Title" in md1
    assert "## Chapter Two" in md2


def test_build_clean_markdown_reports_missing_pages(tmp_path):
    work_dir = tmp_path
    raw_dir = work_dir / "raw"
    raw_dir.mkdir()
    (raw_dir / "page_0001.txt").write_text(PAGE1_RAW, encoding="utf-8")
    # page 2's raw file is intentionally absent -> should be "missing"
    (raw_dir / "page_0003.txt").write_text(PAGE2_RAW, encoding="utf-8")

    image_paths = ["page_0001.png", "page_0002.png", "page_0003.png"]
    md, detected_title, missing = main.build_clean_markdown(image_paths, str(work_dir))

    assert missing == [2]
    assert detected_title == "My Book Title"
    assert "First paragraph of the book." in md
    assert "Second page content." in md


def test_ocr_all_pages_calls_progress_cb_per_page(tmp_path, monkeypatch):
    def fake_ocr_page(image_path, args):
        return "<|det|>text [0, 0, 10, 10]<|/det|>fake page text\n", None

    monkeypatch.setattr(main, "ocr_page", fake_ocr_page)

    calls = []

    class Args:
        resume = False

    main.ocr_all_pages(
        ["a.png", "b.png"], str(tmp_path), Args(), progress_cb=lambda i, t: calls.append((i, t))
    )

    assert calls == [(1, 2), (2, 2)]
    assert os.path.exists(tmp_path / "raw" / "page_0001.txt")
    assert os.path.exists(tmp_path / "raw" / "page_0002.txt")
