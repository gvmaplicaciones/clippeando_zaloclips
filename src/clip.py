"""Recorte de clips finales con ffmpeg-python hacia OUTPUT_DIR."""
from __future__ import annotations

import json
from pathlib import Path

import ffmpeg

from src.config import OUTPUT_DIR


def cut_clips(video_path: Path, moments_path: Path, output_dir: Path | None = None) -> list[Path]:
    """Corta los clips indicados en moments_path a partir de video_path."""
    video_path = Path(video_path)
    moments_path = Path(moments_path)
    target_dir = output_dir or OUTPUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    moments = json.loads(moments_path.read_text(encoding="utf-8"))["moments"]

    clip_paths = []
    for i, moment in enumerate(moments, start=1):
        start = moment["start"]
        duration = moment["end"] - moment["start"]
        title = moment.get("title", f"clip_{i}")
        safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip()
        output_path = target_dir / f"{video_path.stem}_{i:02d}_{safe_title}.mp4"

        (
            ffmpeg
            .input(str(video_path), ss=start)
            .output(str(output_path), t=duration, c="copy", avoid_negative_ts="make_zero")
            .overwrite_output()
            .run(quiet=True)
        )
        clip_paths.append(output_path)

    return clip_paths
