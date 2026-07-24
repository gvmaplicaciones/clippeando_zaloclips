"""Conversion a formato vertical 9:16 (crop centrado o blur-fill)."""
from __future__ import annotations

from pathlib import Path

import ffmpeg

from src.ffmpeg_utils import has_audio_stream
from src.ffmpeg_utils import run as run_ffmpeg

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

    input_stream = ffmpeg.input(str(input_path), **input_kwargs)
    # Encadenar .filter() directo sobre el input (en vez de sobre
    # input_stream.video) genera un unico stream de salida filtrado y ese es
    # el unico que ffmpeg-python mapea en el output: el audio original queda
    # afuera del comando por completo (ni siquiera hay que descartarlo, un
    # -map explicito lo excluye por default). Hay que tomar el audio del
    # input aparte y pasarlo tambien a .output() para que se mapee.
    video = (
        input_stream.video
        .filter("crop", crop_w, crop_h)
        .filter("scale", width, height)
        .filter("setsar", 1)
    )

    if has_audio_stream(input_path):
        stream = ffmpeg.output(video, input_stream.audio, str(output_path), **output_kwargs).overwrite_output()
    else:
        # Video sin pista de audio (caso raro, ej. descarga corrupta): no
        # forzar -map de un stream de audio que no existe.
        output_kwargs.pop("c:a", None)
        output_kwargs.pop("b:a", None)
        stream = ffmpeg.output(video, str(output_path), **output_kwargs).overwrite_output()
    run_ffmpeg(stream)

    return output_path


def crop_to_vertical_blur_fill(
    input_path: Path,
    output_path: Path,
    start: float | None = None,
    duration: float | None = None,
    width: int = VERTICAL_WIDTH,
    height: int = VERTICAL_HEIGHT,
    blur_sigma: int = 20,
    darken: float = -0.06,
) -> Path:
    """Convierte a 9:16 sin recortar el video: fondo difuminado + video entero centrado.

    Genera dos capas desde el mismo input via split:
    - Fondo: video escalado para CUBRIR width x height (force_original_aspect_ratio=increase
      + crop), desenfocado con gblur y ligeramente oscurecido con eq.
    - Primer plano: video entero escalado para CABER en width x height
      (force_original_aspect_ratio=decrease), centrado verticalmente sobre el fondo.

    El audio se mapea explicitamente igual que en crop_to_vertical() para
    evitar el bug conocido donde el audio queda fuera del -map implicito
    cuando hay un filter_complex activo.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

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

    input_stream = ffmpeg.input(str(input_path), **input_kwargs)

    # split genera dos referencias independientes al mismo stream de video para
    # poder alimentar dos cadenas de filtros distintas (fondo y primer plano)
    # sin que ffmpeg se queje de que el mismo stream se consume dos veces.
    split = input_stream.video.filter_multi_output("split")
    bg_in = split[0]
    fg_in = split[1]

    bg = (
        bg_in
        .filter("scale", width, height, force_original_aspect_ratio="increase")
        .filter("crop", width, height)
        .filter("gblur", sigma=blur_sigma)
        .filter("eq", brightness=darken)
    )

    # scale con force_original_aspect_ratio=decrease: el video cabe entero
    # dentro de width x height sin que ninguna dimension lo supere.
    fg = fg_in.filter("scale", width, height, force_original_aspect_ratio="decrease")

    out_video = (
        ffmpeg.filter([bg, fg], "overlay", x="(W-w)/2", y="(H-h)/2")
        .filter("setsar", 1)
    )

    if has_audio_stream(input_path):
        stream = ffmpeg.output(out_video, input_stream.audio, str(output_path), **output_kwargs).overwrite_output()
    else:
        output_kwargs.pop("c:a", None)
        output_kwargs.pop("b:a", None)
        stream = ffmpeg.output(out_video, str(output_path), **output_kwargs).overwrite_output()

    run_ffmpeg(stream)
    return output_path
