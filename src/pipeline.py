"""Orquesta el pipeline completo: descarga -> transcripcion -> momentos -> clips.

Flujo pensado para correr con un solo comando (ej. disparado por control
remoto desde el celular) sin pasos intermedios: descarga/toma el archivo,
transcribe, detecta momentos (clasificando el tipo de contenido solo si no
se fuerza con --content-type), corta, pone vertical y quema subtitulos.
Sin marca de agua - para eso, corre src/watermark.py aparte sobre los
clips ya generados.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from src.clip import HOOK_VERTICAL_MODES, VERTICAL_MODES, cut_clips
from src.config import OUTPUT_DIR
from src.content_types import get_content_type_prompt, list_content_types
from src.detect_moments import detect_moments
from src.download import download_vod, get_video_title, ingest
from src.naming import sanitize_filename
from src.report import generate_report
from src.transcribe import transcribe_video
from src.video_filters import list_video_filters, resolve_video_filter


def run_pipeline(
    url: str | None = None,
    file: str | None = None,
    video_title_override: str | None = None,
    max_clips: int | None = None,
    video_filter: str | None = None,
    content_type: str | None = None,
    vertical_mode: str = "crop",
    hook_teaser: bool = True,
    hook_vertical_mode: str = "same",
) -> list[Path]:
    """Corre el pipeline completo a partir de una URL o un archivo local.

    `content_type` (ej. "invitado", "viajes", "podcast" - ver
    --list-content-types) fuerza el criterio de seleccion de momentos de
    `prompts/<content_type>.txt`. Sin `content_type`, `src.detect_moments`
    clasifica el transcript solo (entretenimiento con invitados / viaje /
    podcast / narrativo de un streamer) y aplica el criterio que
    corresponda, en la misma llamada - no hace falta indicarlo a mano.

    `video_title_override`, si se pasa, se usa como nombre de la carpeta de
    salida en vez del titulo automatico (metadata de yt-dlp o nombre de
    archivo) - ver `src.clip.cut_clips`. `max_clips`, si se pasa, limita
    cuantos MOMENTOS (no archivos finales) se procesan, quedandose con los
    de mayor score - ver `src.clip.cut_clips`. `video_filter`, si se pasa,
    aplica un filtro visual sobre todo el clip antes de los subtitulos -
    ver `src.video_filters`.
    """
    if not url and not file:
        raise ValueError("Debes indicar --url o --file")

    # Validar --filter/--content-type ANTES de descargar/transcribir/
    # detectar momentos: un nombre invalido no deberia descubrirse recien
    # despues de gastar tiempo (y costo de API de Claude) en esos pasos.
    resolve_video_filter(video_filter)
    if content_type:
        get_content_type_prompt(content_type)

    video_path, audio_path = ingest(url=url, file=file)

    transcript_path = transcribe_video(audio_path)
    moments_path, moments, _usage = detect_moments(transcript_path, content_type=content_type)
    clip_paths = cut_clips(
        video_path,
        moments_path,
        transcript_path=transcript_path,
        video_title_override=video_title_override,
        max_clips=max_clips,
        video_filter=video_filter,
        vertical_mode=vertical_mode,
        hook_teaser=hook_teaser,
        hook_vertical_mode=hook_vertical_mode,
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
    # No "required=True": --list-content-types/--list-filters deben poder correr solos.
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
        "los subtitulos. Sin esto, sin filtro.",
    )
    parser.add_argument(
        "--list-filters",
        action="store_true",
        help="Lista los filtros de video disponibles y termina, sin correr el pipeline.",
    )
    parser.add_argument(
        "--content-type",
        help="Fuerza el criterio de seleccion de momentos de prompts/<nombre>.txt (ver "
        "--list-content-types). Sin esto, el modelo clasifica el transcript solo.",
    )
    parser.add_argument(
        "--list-content-types",
        action="store_true",
        help="Lista los tipos de contenido disponibles (prompts/*.txt) y termina, sin correr el pipeline.",
    )
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Solo descarga el video a input/ y termina. Sin transcripcion, sin clips.",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Flujo simplificado: descarga, transcribe, detecta momentos y genera report.md "
        "con timestamps para edicion manual. No corta ni procesa ningun clip.",
    )
    parser.add_argument(
        "--vertical-mode",
        choices=VERTICAL_MODES,
        default="crop",
        help="Modo de conversion a vertical: 'crop' (recorte centrado, por defecto) o "
        "'blur_fill' (video entero sobre fondo difuminado, sin perder contenido de los bordes).",
    )
    parser.add_argument(
        "--hook-teaser",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Antepone un avance de 2-4s con la frase mas impactante del clip (default: activado). "
        "Usa --no-hook-teaser para desactivar.",
    )
    parser.add_argument(
        "--hook-vertical-mode",
        choices=HOOK_VERTICAL_MODES,
        default="same",
        help="Modo vertical del hook-teaser: 'same' (default, mismo que --vertical-mode), "
        "'crop' (pantalla completa recortada), 'blur_fill' (fondo difuminado).",
    )
    args = parser.parse_args()

    if args.list_filters:
        list_video_filters()
        return
    if args.list_content_types:
        list_content_types()
        return

    if not args.url and not args.file:
        parser.error("--url o --file son requeridos (salvo con --list-filters/--list-content-types)")

    try:
        if args.download_only:
            if not args.url:
                parser.error("--download-only requiere --url")
            video_path = download_vod(args.url)
            title = sanitize_filename(args.video_title or get_video_title(video_path))
            target_dir = OUTPUT_DIR / title
            target_dir.mkdir(parents=True, exist_ok=True)
            dest = target_dir / video_path.name
            if not dest.exists():
                print(f"Copiando video a {dest} ...")
                shutil.copy2(video_path, dest)
            else:
                print(f"Video ya existe en destino: {dest}")
            print(f"Video descargado: {video_path}")
            return
        if args.report_only:
            generate_report(
                url=args.url,
                file=args.file,
                video_title_override=args.video_title,
                content_type=args.content_type,
            )
        else:
            run_pipeline(
                url=args.url,
                file=args.file,
                video_title_override=args.video_title,
                max_clips=args.max_clips,
                video_filter=args.video_filter,
                content_type=args.content_type,
                vertical_mode=args.vertical_mode,
                hook_teaser=args.hook_teaser,
                hook_vertical_mode=args.hook_vertical_mode,
            )
    except ValueError as e:
        parser.error(str(e))


if __name__ == "__main__":
    main()
