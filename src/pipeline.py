"""Orquesta el pipeline completo: descarga -> transcripcion -> momentos -> clips."""
from __future__ import annotations

import argparse
from pathlib import Path

from src.campaigns import get_campaign
from src.campaigns import list_campaigns as list_campaign_profiles
from src.clip import cut_clips
from src.detect_moments import detect_moments
from src.download import ingest
from src.transcribe import transcribe_video
from src.video_filters import list_video_filters, resolve_video_filter
from src.watermarks import get_watermark, list_watermarks


def run_pipeline(
    url: str | None = None,
    file: str | None = None,
    watermark: str | None = None,
    campaign: str | None = None,
    video_title_override: str | None = None,
    max_clips: int | None = None,
    video_filter: str | None = None,
) -> list[Path]:
    """Corre el pipeline completo a partir de una URL o un archivo local.

    `video_title_override`, si se pasa, se usa como nombre de la carpeta de
    salida en vez del titulo automatico (metadata de yt-dlp o nombre de
    archivo) - ver `src.clip.cut_clips`. `max_clips`, si se pasa, limita
    cuantos MOMENTOS (no archivos finales) se procesan, quedandose con los
    de mayor score - ver `src.clip.cut_clips`. `video_filter`, si se pasa,
    aplica un filtro visual sobre todo el clip antes de subtitulos y marca
    de agua - ver `src.video_filters`.
    """
    if not url and not file:
        raise ValueError("Debes indicar --url o --file")

    # Validar --watermark/--campaign/--filter ANTES de descargar/transcribir
    # /detectar momentos: un nombre invalido no deberia descubrirse recien
    # despues de gastar tiempo (y costo de API de Claude) en esos pasos.
    if watermark and campaign:
        raise ValueError(
            "--watermark y --campaign no se pueden combinar: --campaign ya incluye su propia marca de agua."
        )
    if watermark:
        get_watermark(watermark)
    if campaign:
        get_campaign(campaign)
    resolve_video_filter(video_filter)

    video_path, audio_path = ingest(url=url, file=file)

    transcript_path = transcribe_video(audio_path)
    moments_path, moments, _usage = detect_moments(transcript_path)
    clip_paths = cut_clips(
        video_path,
        moments_path,
        transcript_path=transcript_path,
        watermark=watermark,
        campaign=campaign,
        video_title_override=video_title_override,
        max_clips=max_clips,
        video_filter=video_filter,
    )

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
    # No "required=True": --list-watermarks debe poder correr solo, sin --url/--file.
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--url", help="URL del VOD a descargar (yt-dlp)")
    group.add_argument("--file", help="Ruta a un video ya existente en input/")
    parser.add_argument(
        "--video-title",
        help="Nombre a usar para la carpeta de salida en vez del titulo automatico "
        "(metadata de yt-dlp o nombre de archivo).",
    )
    parser.add_argument(
        "--max-clips",
        type=int,
        help="Limita cuantos momentos se procesan, quedandose con los de mayor score "
        "(sin esto, sin limite). Se aplica a momentos, no a archivos finales.",
    )
    parser.add_argument(
        "--filter",
        dest="video_filter",
        help="Filtro visual (ver --list-filters) aplicado sobre todo el clip antes de "
        "subtitulos y marca de agua. Sin esto, sin filtro.",
    )
    parser.add_argument(
        "--list-filters",
        action="store_true",
        help="Lista los filtros de video disponibles y termina, sin correr el pipeline.",
    )
    wm_group = parser.add_mutually_exclusive_group()
    wm_group.add_argument(
        "--watermark",
        help="Nombre de una marca de src.watermarks.WATERMARKS a aplicar sobre cada clip "
        "final en la misma corrida (ver --list-watermarks). Sin esto, los clips salen sin marca.",
    )
    wm_group.add_argument(
        "--campaign",
        help="ID numerico de una campaña de campaigns.json (agrupa marca de agua + si "
        "permite dividir en partes + estilo de subtitulos en un solo perfil, ver "
        "--list-campaigns). No se puede combinar con --watermark.",
    )
    parser.add_argument(
        "--list-watermarks",
        action="store_true",
        help="Lista las marcas de agua disponibles y termina, sin correr el pipeline.",
    )
    parser.add_argument(
        "--list-campaigns",
        action="store_true",
        help="Lista las campañas disponibles (campaigns.json) y termina, sin correr el pipeline.",
    )
    args = parser.parse_args()

    if args.list_watermarks:
        list_watermarks()
        return
    if args.list_campaigns:
        list_campaign_profiles()
        return
    if args.list_filters:
        list_video_filters()
        return

    if not args.url and not args.file:
        parser.error(
            "--url o --file son requeridos (salvo con --list-watermarks/--list-campaigns/--list-filters)"
        )

    try:
        run_pipeline(
            url=args.url,
            file=args.file,
            watermark=args.watermark,
            campaign=args.campaign,
            video_title_override=args.video_title,
            max_clips=args.max_clips,
            video_filter=args.video_filter,
        )
    except ValueError as e:
        parser.error(str(e))


if __name__ == "__main__":
    main()
