"""Quema un watermark de texto permanente sobre clips ya generados.

Script independiente del pipeline principal: opera sobre .mp4 que ya
existen (vertical, con subtitulos ya quemados), no reprocesa nada desde el
video original. Por ahora es solo texto - si mas adelante se quiere el logo
real de YouTube, se puede agregar un filtro ``overlay`` con un PNG.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import ffmpeg

from src.ffmpeg_utils import escape_filter_path
from src.ffmpeg_utils import run as run_ffmpeg

WATERMARK_TEXT = "YT: ampeterby7"

WATERMARK_FONT_SIZE = 32
WATERMARK_FONT_COLOR = "white"
WATERMARK_BORDER_WIDTH = 2
WATERMARK_BORDER_COLOR = "black@0.75"
WATERMARK_SHADOW_OFFSET = 2
WATERMARK_SHADOW_COLOR = "black@0.6"

# Margenes como fraccion del ancho/alto del frame (no pixeles fijos) para
# que funcione igual sin importar la resolucion del clip. El margen inferior
# es generoso porque el ~20% inferior de la pantalla en TikTok suele estar
# cubierto por la UI de la app (misma zona de seguridad que usa
# src.subtitles para los subtitulos).
MARGIN_RIGHT_FRACTION = 0.035
MARGIN_BOTTOM_FRACTION = 0.22

# Candidatos de fuente por SO, en orden de preferencia. Se usa un archivo de
# fuente real (fontfile=) en vez de confiar en fontconfig (font=), porque no
# todos los builds de ffmpeg en Windows lo traen habilitado.
_CANDIDATE_FONT_FILES = [
    # Windows
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    # Linux (Debian/Ubuntu, incluido Colab con fonts-dejavu-core instalado)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    # macOS
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]


def _find_font_file() -> str | None:
    for candidate in _CANDIDATE_FONT_FILES:
        if Path(candidate).exists():
            return candidate
    return None


def _escape_drawtext_text(text: str) -> str:
    escaped = text.replace("\\", "\\\\")
    escaped = escaped.replace("'", "\\'")
    escaped = escaped.replace(":", "\\:")
    escaped = escaped.replace("%", "\\%")
    return escaped


def _build_drawtext_filter(text: str, font_file: str | None) -> str:
    resolved_font = font_file or _find_font_file()

    options = [f"text='{_escape_drawtext_text(text)}'"]
    if resolved_font:
        options.append(f"fontfile='{escape_filter_path(resolved_font)}'")
    else:
        # Ultimo recurso si no se encontro ningun archivo de fuente conocido:
        # confiar en fontconfig (requiere un ffmpeg compilado con
        # --enable-fontconfig; no todos los builds de Windows lo traen).
        options.append("font=Sans")

    options += [
        f"fontsize={WATERMARK_FONT_SIZE}",
        f"fontcolor={WATERMARK_FONT_COLOR}",
        f"borderw={WATERMARK_BORDER_WIDTH}",
        f"bordercolor={WATERMARK_BORDER_COLOR}",
        f"shadowx={WATERMARK_SHADOW_OFFSET}",
        f"shadowy={WATERMARK_SHADOW_OFFSET}",
        f"shadowcolor={WATERMARK_SHADOW_COLOR}",
        f"x=w-text_w-({MARGIN_RIGHT_FRACTION}*w)",
        f"y=h-text_h-({MARGIN_BOTTOM_FRACTION}*h)",
    ]
    return "drawtext=" + ":".join(options)


def add_watermark(
    clip_path: Path,
    output_path: Path | None = None,
    text: str = WATERMARK_TEXT,
    font_file: str | None = None,
) -> Path:
    """Quema `text` como watermark permanente (100% de la duracion) sobre `clip_path`.

    Si `output_path` es None, guarda como "<nombre>_wm.mp4" junto al
    original (no destructivo). Si `output_path` es el mismo archivo que
    `clip_path`, lo sobreescribe de forma segura: renderiza a un temporal y
    recien reemplaza el original si ffmpeg termina bien.
    """
    clip_path = Path(clip_path)
    output_path = Path(output_path) if output_path else clip_path.with_name(
        f"{clip_path.stem}_wm{clip_path.suffix}"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    overwriting_in_place = output_path.resolve() == clip_path.resolve()
    render_path = output_path
    if overwriting_in_place:
        render_path = output_path.with_name(f".{output_path.stem}.watermark_tmp{output_path.suffix}")

    vf = _build_drawtext_filter(text, font_file)
    stream = (
        ffmpeg
        .input(str(clip_path))
        .output(
            str(render_path),
            vf=vf,
            **{"c:v": "libx264", "preset": "veryfast", "crf": 20, "pix_fmt": "yuv420p", "c:a": "copy"},
        )
        .overwrite_output()
    )
    run_ffmpeg(stream)

    if overwriting_in_place:
        os.replace(render_path, output_path)

    return output_path


def process_folder(
    folder: Path,
    text: str = WATERMARK_TEXT,
    font_file: str | None = None,
    overwrite: bool = False,
) -> list[Path]:
    """Aplica el watermark a todos los .mp4 de `folder` (no recursivo)."""
    folder = Path(folder)
    clips = sorted(
        p for p in folder.glob("*.mp4")
        if not p.stem.endswith("_wm") and not p.stem.startswith(".")
    )

    if not clips:
        print(f"No se encontraron .mp4 en {folder}")
        return []

    resolved_font = font_file or _find_font_file()
    print(f"Fuente usada para el watermark: {resolved_font or 'font=Sans (fontconfig, sin archivo especifico)'}")

    total = len(clips)
    results = []
    for i, clip_path in enumerate(clips, start=1):
        print(f"[{i}/{total}] Aplicando watermark a: {clip_path.name}", flush=True)
        output_path = clip_path if overwrite else None
        result = add_watermark(clip_path, output_path=output_path, text=text, font_file=font_file)
        print(f"  -> {result.name}", flush=True)
        results.append(result)

    print(f"Listo: {total} clip(s) procesados.")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Quema un watermark de texto sobre todos los .mp4 de una carpeta "
        "(clips ya generados, no reprocesa desde el video original)."
    )
    parser.add_argument("--folder", required=True, help="Carpeta con los .mp4 a marcar")
    parser.add_argument("--text", default=WATERMARK_TEXT, help=f"Texto del watermark (default: {WATERMARK_TEXT!r})")
    parser.add_argument("--font-file", help="Ruta a un archivo de fuente .ttf/.otf a usar (override de la autodeteccion)")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Sobreescribe el .mp4 original en vez de generar una copia con sufijo _wm (mas riesgoso: sin esto no se toca el original)",
    )
    args = parser.parse_args()

    process_folder(
        Path(args.folder),
        text=args.text,
        font_file=args.font_file,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
