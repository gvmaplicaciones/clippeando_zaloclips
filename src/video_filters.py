"""Filtros visuales opcionales aplicados sobre todo el clip vertical.

Se aplican DESPUES del crop a 9:16 pero ANTES de quemar subtitulos y
marca de agua (ver src.clip.cut_clips), para que el texto y el logo
siempre queden nitidos encima del filtro, no filtrados tambien.
"""
from __future__ import annotations

from pathlib import Path

import ffmpeg

from src.ffmpeg_utils import run as run_ffmpeg

# Cadena de filtros de ffmpeg por nombre. "curves=preset=vintage" es un
# preset incluido en el filtro curves de ffmpeg (no inventado). El resto
# son combinaciones de filtros estandar (hflip, eq, hue, vignette,
# drawgrid) sin dependencias externas.
VIDEO_FILTERS: dict[str, str] = {
    "mirror": "hflip",
    "vintage": "curves=preset=vintage,vignette",
    "tv_scanlines": "eq=contrast=1.1:saturation=0.7,drawgrid=w=iw:h=3:t=1:c=black@0.35",
    "grayscale": "hue=s=0,eq=contrast=1.1",
    "cinematic": "curves=preset=strong_contrast,eq=saturation=1.15",
}

FILTER_LABELS: dict[str, str] = {
    "mirror": "Modo espejo (flip horizontal)",
    "vintage": "Vintage (colores calidos + viñeta)",
    "tv_scanlines": "TV a rayas (scanlines estilo CRT)",
    "grayscale": "Blanco y negro",
    "cinematic": "Cinematico (alto contraste)",
}


def resolve_video_filter(name: str | None) -> str | None:
    """Valida `name` contra VIDEO_FILTERS. None (o "") -> None (sin filtro).

    Lanza ValueError con los nombres validos si `name` no esta vacio pero
    tampoco es un filtro conocido.
    """
    if not name:
        return None
    if name not in VIDEO_FILTERS:
        valid = ", ".join(VIDEO_FILTERS)
        raise ValueError(f"Filtro de video desconocido: {name!r}. Filtros validos: {valid}")
    return name


def list_video_filters() -> None:
    """Imprime los filtros de video disponibles."""
    print("Filtros de video disponibles:")
    for name, label in FILTER_LABELS.items():
        print(f"  {name}: {label}")


def apply_video_filter(input_path: Path, output_path: Path, filter_name: str) -> Path:
    """Aplica el filtro `filter_name` (una clave de VIDEO_FILTERS) sobre todo `input_path`."""
    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if filter_name not in VIDEO_FILTERS:
        valid = ", ".join(VIDEO_FILTERS)
        raise ValueError(f"Filtro de video desconocido: {filter_name!r}. Filtros validos: {valid}")

    vf = VIDEO_FILTERS[filter_name]
    stream = (
        ffmpeg
        .input(str(input_path))
        .output(
            str(output_path),
            vf=vf,
            **{"c:v": "libx264", "preset": "veryfast", "crf": 20, "pix_fmt": "yuv420p", "c:a": "copy"},
        )
        .overwrite_output()
    )
    run_ffmpeg(stream)

    return output_path
