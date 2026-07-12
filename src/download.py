"""Descarga de VODs con yt-dlp hacia INPUT_DIR."""
from __future__ import annotations

from pathlib import Path

import yt_dlp

from src.config import INPUT_DIR


def download_vod(url: str, output_dir: Path | None = None) -> Path:
    """Descarga un video desde una URL y devuelve la ruta local del archivo."""
    target_dir = output_dir or INPUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        "outtmpl": str(target_dir / "%(id)s.%(ext)s"),
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)

    downloaded_path = Path(filename)
    if downloaded_path.suffix != ".mp4":
        downloaded_path = downloaded_path.with_suffix(".mp4")
    return downloaded_path
