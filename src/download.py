"""Descarga de VODs con yt-dlp y extraccion de audio hacia INPUT_DIR."""
from __future__ import annotations

import argparse
from pathlib import Path

import ffmpeg
import yt_dlp

from src.config import INPUT_DIR, YTDLP_COOKIES_FILE, YTDLP_COOKIES_FROM_BROWSER


def download_vod(url: str, output_dir: Path | None = None) -> Path:
    """Descarga un video desde una URL y devuelve la ruta local del archivo.

    Si YouTube (u otro sitio) pide confirmar que no sos un bot, configura
    YTDLP_COOKIES_FILE (ruta a un cookies.txt exportado del navegador) o
    YTDLP_COOKIES_FROM_BROWSER (solo en una maquina con navegador local) en
    el .env. Ver README para instrucciones de como exportar el cookies.txt.
    """
    target_dir = output_dir or INPUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        "outtmpl": str(target_dir / "%(id)s.%(ext)s"),
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
    }

    if YTDLP_COOKIES_FILE:
        ydl_opts["cookiefile"] = YTDLP_COOKIES_FILE
    if YTDLP_COOKIES_FROM_BROWSER:
        ydl_opts["cookiesfrombrowser"] = (YTDLP_COOKIES_FROM_BROWSER,)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)

    downloaded_path = Path(filename)
    if downloaded_path.suffix != ".mp4":
        downloaded_path = downloaded_path.with_suffix(".mp4")
    return downloaded_path


def extract_audio(video_path: Path, output_dir: Path | None = None) -> Path:
    """Extrae el audio de un video a WAV 16kHz mono (formato esperado por Whisper)."""
    video_path = Path(video_path)
    target_dir = output_dir or INPUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    audio_path = target_dir / f"{video_path.stem}.wav"

    (
        ffmpeg
        .input(str(video_path))
        .output(str(audio_path), ac=1, ar=16000, acodec="pcm_s16le")
        .overwrite_output()
        .run(quiet=True)
    )

    return audio_path


def ingest(url: str | None = None, file: str | None = None) -> tuple[Path, Path]:
    """Descarga (o toma un archivo existente) y extrae su audio. Devuelve (video, audio)."""
    if not url and not file:
        raise ValueError("Debes indicar --url o --file")

    video_path = download_vod(url) if url else Path(file)
    audio_path = extract_audio(video_path)

    return video_path, audio_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Descarga un VOD y extrae su audio")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="URL del VOD a descargar (yt-dlp)")
    group.add_argument("--file", help="Ruta a un video ya existente en input/")
    args = parser.parse_args()

    video_path, audio_path = ingest(url=args.url, file=args.file)

    print(f"Video: {video_path}")
    print(f"Audio: {audio_path}")


if __name__ == "__main__":
    main()
