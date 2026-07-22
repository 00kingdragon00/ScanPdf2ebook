import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
from PIL import Image

import video


def _write_solid_png(path, page_id):
    # NOTE: a truly solid/flat-color image is a degenerate input for
    # imagehash.phash (DCT-based) -- every flat-color image hashes to the
    # same value regardless of color, since there's no AC content to
    # transform. Real page frames always carry content (text/graphics), so
    # we simulate that here with seeded pixel noise: same page_id -> pixel-
    # identical image -> hash distance 0 (page repeated across frames);
    # different page_id -> unrelated image -> large hash distance (a
    # different page).
    rng = np.random.RandomState(page_id)
    arr = rng.randint(0, 256, (64, 64, 3), dtype="uint8")
    Image.fromarray(arr).save(path)


def test_dedupe_frames_collapses_consecutive_near_duplicates(tmp_path):
    # Three near-identical "page 1" frames (a real page-turn transition
    # produces a few frames of the same content), then one distinct "page 2".
    paths = []
    for i in range(3):
        p = tmp_path / f"candidate_{i:04d}.png"
        _write_solid_png(p, page_id=1)
        paths.append(str(p))
    page2 = tmp_path / "candidate_0003.png"
    _write_solid_png(page2, page_id=2)
    paths.append(str(page2))

    kept = video.dedupe_frames(paths)

    assert kept == [paths[0], paths[3]]


def test_dedupe_frames_keeps_all_when_every_frame_is_distinct(tmp_path):
    paths = []
    page_ids = [1, 2, 3]
    for i, page_id in enumerate(page_ids):
        p = tmp_path / f"candidate_{i:04d}.png"
        _write_solid_png(p, page_id=page_id)
        paths.append(str(p))

    kept = video.dedupe_frames(paths)

    assert kept == paths


def test_dedupe_frames_empty_list_returns_empty():
    assert video.dedupe_frames([]) == []


def test_extract_pages_from_youtube_downloads_extracts_dedupes_and_renumbers(tmp_path, monkeypatch):
    work_dir = str(tmp_path / "ocr_work")

    def fake_download(url, out_path):
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        Path(out_path).write_bytes(b"fake video bytes")

    candidate_dir = tmp_path / "fake_candidates"
    candidate_dir.mkdir()
    candidate_paths = []
    for i, page_id in enumerate([1, 2]):
        p = candidate_dir / f"candidate_{i:04d}.png"
        _write_solid_png(p, page_id=page_id)
        candidate_paths.append(str(p))

    def fake_extract_candidates(video_path, out_dir, scene_threshold=video.DEFAULT_SCENE_THRESHOLD):
        assert os.path.exists(video_path)
        return candidate_paths

    monkeypatch.setattr(video, "download_video", fake_download)
    monkeypatch.setattr(video, "extract_candidate_frames", fake_extract_candidates)

    phases = []
    page_paths = video.extract_pages_from_youtube(
        "https://youtu.be/fakeid123", work_dir, phase_cb=phases.append
    )

    assert phases == ["downloading", "extracting frames"]
    assert len(page_paths) == 2
    assert page_paths[0].endswith("page_0001.png")
    assert page_paths[1].endswith("page_0002.png")
    for p in page_paths:
        assert os.path.exists(p)


def test_extract_pages_from_youtube_passes_custom_thresholds_through(tmp_path, monkeypatch):
    # Regression guard: a real 6.5-hour book video only produced 1 candidate
    # frame at the old default scene_threshold=0.3 -- the whole point of
    # exposing these as overridable settings is that they actually reach
    # the underlying ffmpeg/dedup calls, not just accept-and-ignore.
    work_dir = str(tmp_path / "ocr_work")

    def fake_download(url, out_path):
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        Path(out_path).write_bytes(b"fake video bytes")

    seen = {}

    def fake_extract_candidates(video_path, out_dir, scene_threshold=video.DEFAULT_SCENE_THRESHOLD):
        seen["scene_threshold"] = scene_threshold
        return []

    def fake_dedupe(frame_paths, hash_distance_threshold=video.DEFAULT_HASH_DISTANCE_THRESHOLD):
        seen["hash_distance_threshold"] = hash_distance_threshold
        return []

    monkeypatch.setattr(video, "download_video", fake_download)
    monkeypatch.setattr(video, "extract_candidate_frames", fake_extract_candidates)
    monkeypatch.setattr(video, "dedupe_frames", fake_dedupe)

    video.extract_pages_from_youtube(
        "https://youtu.be/fakeid123",
        work_dir,
        scene_threshold=0.02,
        hash_distance_threshold=10,
    )

    assert seen["scene_threshold"] == 0.02
    assert seen["hash_distance_threshold"] == 10
