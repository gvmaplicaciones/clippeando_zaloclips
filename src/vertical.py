"""Recorte a formato vertical 9:16 (crop centrado, sin face-tracking)."""
from __future__ import annotations

from pathlib import Path

import ffmpeg

VERTICAL_WIDTH = 1080
VERTICAL_HEIGHT = 1920


def crop_to_vertical(
    input_path: Path,
    output_path: Path,
    start: float | None = None,
    duration: float | None = None,
    width: int = VERTICAL_WIDTH,
    height: int = VERTICAL_HEIGHT,
) -> Path:
    """Recorta el frame horizontal a 9:16 centrado y escala a width x height.

    El crop se centra tanto horizontal como verticalmente (comportamiento
    por defecto del filtro ``crop`` de ffmpeg cuando no se pasan x/y): para
    un video 16:9 esto recorta los costados y conserva todo el alto.

    Si se pasan `start`/`duration`, el recorte temporal del clip se hace en
    esta misma pasada (en vez de un ``-c copy`` previo) para que el corte
    quede en el frame exacto: con audio/video copy, ffmpeg solo puede cortar
    en keyframes, lo que desalinea el inicio real respecto al pedido -
    critico cuando un momento se divide en varias partes contiguas.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Crop centrado (ffmpeg centra automaticamente si no se pasan x/y) al
    # aspect ratio destino, conservando el mayor recorte posible: para un
    # video 16:9 esto recorta los costados y conserva todo el alto.
    crop_w = f"min(iw,ih*{width}/{height})"
    crop_h = f"min(ih,iw*{height}/{width})"

    input_kwargs = {"ss": start} if start is not None else {}
    output_kwargs = {
        "c:v": "libx264",
        "preset": "veryfast",
        "crf": 20,
        "pix_fmt": "yuv420p",
        "c:a": "aac",
        "b:a": "128k",
    }
    if duration is not None:
        output_kwargs["t"] = duration

    (
        ffmpeg
        .input(str(input_path), **input_kwargs)
        .filter("crop", crop_w, crop_h)
        .filter("scale", width, height)
        .filter("setsar", 1)
        .output(str(output_path), **output_kwargs)
        .overwrite_output()
        .run(quiet=True)
    )

    return output_path
