#!/usr/bin/env python3
import os
import shutil
import subprocess

import imagehash
import yt_dlp
from PIL import Image

DEFAULT_SCENE_THRESHOLD = 0.3
DEFAULT_HASH_DISTANCE_THRESHOLD = 4


def download_video(url, out_path):
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    ydl_opts = {
        "outtmpl": out_path,
        "format": "best",
        "quiet": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


def extract_candidate_frames(video_path, out_dir, scene_threshold=DEFAULT_SCENE_THRESHOLD):
    os.makedirs(out_dir, exist_ok=True)
    pattern = os.path.join(out_dir, "candidate_%04d.png")
    cmd = [
        "ffmpeg",
        "-i",
        video_path,
        "-vf",
        f"select='gt(scene,{scene_threshold})'",
        "-vsync",
        "vfr",
        pattern,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return sorted(
        os.path.join(out_dir, name)
        for name in os.listdir(out_dir)
        if name.startswith("candidate_") and name.endswith(".png")
    )


def dedupe_frames(frame_paths, hash_distance_threshold=DEFAULT_HASH_DISTANCE_THRESHOLD):
    kept = []
    kept_hash = None
    for path in frame_paths:
        h = imagehash.phash(Image.open(path))
        if kept_hash is None or (h - kept_hash) > hash_distance_threshold:
            kept.append(path)
            kept_hash = h
    return kept


def extract_pages_from_youtube(url, work_dir, phase_cb=None):
    pages_dir = os.path.join(work_dir, "pages")
    candidates_dir = os.path.join(work_dir, "candidates")
    os.makedirs(pages_dir, exist_ok=True)

    if phase_cb:
        phase_cb("downloading")
    video_path = os.path.join(work_dir, "source.mp4")
    download_video(url, video_path)

    if phase_cb:
        phase_cb("extracting frames")
    candidate_paths = extract_candidate_frames(video_path, candidates_dir)
    kept_paths = dedupe_frames(candidate_paths)

    page_paths = []
    for i, src in enumerate(kept_paths, 1):
        dst = os.path.join(pages_dir, f"page_{i:04d}.png")
        shutil.copyfile(src, dst)
        page_paths.append(dst)
    return page_paths
