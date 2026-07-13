"""Orquesta el pipeline completo: descarga -> transcripcion -> momentos -> clips."""
from __future__ import annotations

import argparse
from pathlib import Path

from src.clip import cut_clips
from src.detect_moments import detect_moments
from src.download import ingest
from src.transcribe import transcribe_video


def run_pipeline(url: str | None = None, file: str | None = None) -> list[Path]:
    """Corre el pipeline completo a partir de una URL o un archivo local."""
    if not url and not file:
        raise ValueError("Debes indicar --url o --file")

    video_path, audio_path = ingest(url=url, file=file)

    transcript_path = transcribe_video(audio_path)
    moments_path, moments, _usage = detect_moments(transcript_path)
    clip_paths = cut_clips(video_path, moments_path, transcript_path=transcript_path)

    parts = sum(1 for p in clip_paths if " PARTE " in p.stem)
    print(f"Video: {video_path}")
    print(f"Audio: {audio_path}")
    print(f"Transcript: {transcript_path}")
    print(f"Moments: {moments_path} ({len(moments)} momentos detectados)")
    print(f"Clips generados ({len(clip_paths)}), de los cuales {parts} son partes de momentos largos:")
    for clip_path in clip_paths:
        print(f"  - {clip_path}")

    return clip_paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline de generacion de clips")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="URL del VOD a descargar (yt-dlp)")
    group.add_argument("--file", help="Ruta a un video ya existente en input/")
    args = parser.parse_args()

    run_pipeline(url=args.url, file=args.file)


if __name__ == "__main__":
    main()
