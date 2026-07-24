"""Genera un reporte Markdown con los mejores momentos de un vídeo para edición manual.

Flujo simplificado: descarga/ingesta -> transcripción -> detección de momentos -> report.md
Sin corte de clips, sin vertical, sin subtítulos — solo el documento guía para CapCut.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from src.config import OUTPUT_DIR
from src.content_types import get_content_type_prompt, list_content_types
from src.detect_moments import find_moments
from src.download import get_video_title, ingest
from src.naming import sanitize_filename
from src.transcribe import transcribe_video


def _fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _build_report(title: str, moments: list[dict]) -> str:
    ranked = sorted(moments, key=lambda m: m.get("score", 0), reverse=True)
    max_score = max((m.get("score", 0) for m in moments), default=0)

    lines = [f"# {title}", ""]
    lines.append(f"## Momentos detectados ({len(moments)} en total, ordenados por score)")
    lines.append("")

    for i, m in enumerate(ranked, 1):
        score = m.get("score", "?")
        start = float(m["start"])
        end = float(m["end"])
        dur = end - start
        hook_title = m.get("hook_title") or "Sin titulo"
        reason = m.get("reason") or ""

        lines.append(f'### {i}. "{hook_title}" — Score: {score}')
        lines.append(f"**Timestamp:** {_fmt_ts(start)} – {_fmt_ts(end)} ({dur:.0f}s)")
        lines.append(f"**Por que funciona:** {reason}")
        lines.append("")

    if max_score < 70:
        lines.append("---")
        lines.append("")
        lines.append(
            "> **Nota:** Este video no tiene momentos con gancho fuerte por si solos — "
            "considera un angulo editorial mas atrevido (ironia, comparacion incomoda, "
            "controversia ligera) en vez de forzar el material tal cual."
        )
        lines.append("")

    return "\n".join(lines)


def generate_report(
    url: str | None = None,
    file: str | None = None,
    video_title_override: str | None = None,
    content_type: str | None = None,
    output_dir: Path | None = None,
) -> Path:
    """Descarga/ingesta, transcribe, detecta momentos y genera report.md + copia el video.

    Devuelve la ruta al report.md generado.
    """
    if not url and not file:
        raise ValueError("Debes indicar --url o --file")

    if content_type:
        get_content_type_prompt(content_type)

    video_path, audio_path = ingest(url=url, file=file)
    transcript_path = transcribe_video(audio_path)
    _moments_path, moments, _usage = find_moments(transcript_path, content_type=content_type)

    raw_title = video_title_override or get_video_title(video_path)
    title = sanitize_filename(raw_title)
    base_dir = output_dir or OUTPUT_DIR
    target_dir = base_dir / title
    target_dir.mkdir(parents=True, exist_ok=True)

    # Copia el video original a la carpeta de salida (no mover: input/ actua como cache)
    dest_video = target_dir / video_path.name
    if not dest_video.exists():
        print(f"Copiando video a {dest_video} ...")
        shutil.copy2(video_path, dest_video)
    else:
        print(f"Video ya existe en destino, no se sobreescribe: {dest_video}")

    report_path = target_dir / "report.md"
    report_path.write_text(_build_report(title, moments), encoding="utf-8")

    ranked = sorted(moments, key=lambda m: m.get("score", 0), reverse=True)
    print(f"\nReport generado: {report_path}")
    print(f"Momentos detectados: {len(moments)} (score maximo: {ranked[0].get('score', '?')})")
    print("Top 5:")
    for m in ranked[:5]:
        start, end = float(m["start"]), float(m["end"])
        print(f"  [{m.get('score','?')}] {_fmt_ts(start)}-{_fmt_ts(end)} — {m.get('hook_title','')}")

    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Genera report.md con los mejores momentos para edicion manual en CapCut"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--url", help="URL del VOD a descargar (yt-dlp)")
    group.add_argument("--file", help="Ruta a un video ya existente en input/")
    parser.add_argument(
        "--video-title",
        help="Nombre de la carpeta de salida (por defecto: titulo del video)",
    )
    parser.add_argument(
        "--content-type",
        help="Fuerza el criterio de seleccion de prompts/<nombre>.txt (ver --list-content-types).",
    )
    parser.add_argument(
        "--list-content-types",
        action="store_true",
        help="Lista los tipos de contenido disponibles y termina.",
    )
    args = parser.parse_args()

    if args.list_content_types:
        list_content_types()
        return

    if not args.url and not args.file:
        parser.error("--url o --file son requeridos (salvo con --list-content-types)")

    try:
        generate_report(
            url=args.url,
            file=args.file,
            video_title_override=args.video_title,
            content_type=args.content_type,
        )
    except ValueError as e:
        parser.error(str(e))


if __name__ == "__main__":
    main()
