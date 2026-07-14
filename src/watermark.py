"""Quema un watermark permanente (logo + texto) sobre clips ya generados.

Script independiente del pipeline principal: opera sobre .mp4 que ya
existen (vertical, con subtitulos ya quemados), no reprocesa nada desde el
video original. Superpone el logo de YouTube (``Youtube_logo.png`` en la
raiz del repo por default) en la esquina inferior derecha, con el texto
"ampeterby7" a su izquierda, durante el 100% de la duracion del clip.
"""
from __future__ import annotations

import argparse
import os
import sys
import textwrap
from pathlib import Path

from PIL import Image

from src.config import PROJECT_ROOT
from src.ffmpeg_utils import escape_filter_path
from src.ffmpeg_utils import probe_duration
from src.ffmpeg_utils import run_command as run_ffmpeg_command

WATERMARK_TEXT = "ampeterby7"
DEFAULT_LOGO_PATH = PROJECT_ROOT / "Youtube_logo.png"

# Call-to-action opcional ("Puedes ver el video completo en") arriba del
# logo+nombre de canal, para redirigir al publico. Apagado por default -
# solo se agrega si se pasa cta_text explicitamente.
DEFAULT_CTA_TEXT = "Puedes ver el video completo en"
CTA_WRAP_WIDTH = 18  # caracteres aprox. por linea, para que entren ~2 lineas cortas
CTA_FONT_SIZE = 20  # mas chico que el texto del canal (WATERMARK_FONT_SIZE=32): no debe ser intrusivo
CTA_LINE_GAP_PX = 4  # separacion vertical entre las lineas del CTA
CTA_GAP_ABOVE_LOGO_PX = 10  # separacion entre el CTA y la fila logo+nombre de canal

# Los clips finales del pipeline duran como mucho unos pocos minutos
# (src.clip.MAX_CLIP_DURATION = 180s antes de dividir en partes). Si el
# .mp4 de entrada dura mucho mas que eso, casi seguro es el VOD original
# mezclado por error en la carpeta (--folder apuntando mal, o el archivo
# fuente copiado ahi) y no un clip ya cortado - mejor cortar la ejecucion
# ahi que quemar horas de CPU procesando el video equivocado.
MAX_EXPECTED_CLIP_SECONDS = 10 * 60

# Alto fijo del logo en pixeles (los clips del pipeline son siempre 1080x1920,
# asi que un valor fijo en vez de una fraccion de la resolucion es mas facil
# de razonar visualmente). El ancho se deriva de este alto respetando la
# proporcion original del PNG.
LOGO_HEIGHT_PX = 50
# Separacion horizontal entre el texto y el logo.
TEXT_LOGO_GAP_PX = 14

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


def wrap_cta_lines(text: str, width: int = CTA_WRAP_WIDTH) -> list[str]:
    """Parte `text` en lineas cortas (~2, "o asi" si el texto es largo) para
    que el CTA no ocupe mucho ancho de pantalla."""
    return textwrap.wrap(text, width=width) or [text]


def check_logo_transparency(logo_path: Path) -> bool:
    """Devuelve True si `logo_path` tiene canal alpha con transparencia real.

    No solo chequea el modo ("RGBA"); un PNG puede declarar canal alpha y
    tenerlo siempre en 255 (opaco), lo que en la practica seria una caja de
    fondo solida igual. Chequea que existan pixeles con alpha < 255.
    """
    with Image.open(logo_path) as img:
        if img.mode not in ("RGBA", "LA", "PA"):
            return False
        alpha = img.convert("RGBA").split()[-1]
        min_alpha, _max_alpha = alpha.getextrema()
        return min_alpha < 255


def _logo_scaled_width(logo_path: Path, target_height: int) -> int:
    with Image.open(logo_path) as img:
        orig_w, orig_h = img.size
    return max(1, round(target_height * orig_w / orig_h))


def _build_filter_complex(
    text: str,
    logo_w: int,
    logo_h: int,
    font_file: str | None,
    cta_lines: list[str] | None = None,
) -> str:
    """Arma el filter_complex: escala el logo, lo superpone, agrega el texto
    del canal y (opcional) un CTA de 1-varias lineas arriba de todo eso.

    Cadena: [1:v] se escala al tamaño del logo -> se overlay-ea sobre [0:v]
    en la esquina inferior derecha -> se le agrega el texto del canal con
    drawtext inmediatamente a la izquierda del logo, centrado verticalmente
    con el -> si hay `cta_lines`, se apilan arriba de esa fila, cada una
    alineada a la derecha de forma independiente (con su propio `text_w` en
    vez de un `text_align` que necesitaria una caja fija para tener efecto).
    Todas las posiciones usan fracciones del frame (no pixeles fijos) salvo
    el tamaño del logo en si, que es un valor fijo en px por diseño.
    """
    resolved_font = font_file or _find_font_file()

    logo_x = f"W-w-({MARGIN_RIGHT_FRACTION}*W)"
    logo_y = f"H-h-({MARGIN_BOTTOM_FRACTION}*H)"
    # shortest=1: sin esto, el overlay filter por default (eof_action=repeat)
    # sigue generando frames indefinidamente una vez que el input principal
    # termina, porque el logo (input en loop) nunca llega a EOF por si solo -
    # verificado empiricamente: sin este flag, un clip de 5s sin pista de
    # audio se cuelga corriendo ffmpeg mas alla de su duracion real (el
    # global -shortest de la salida no alcanza a cortarlo).
    overlay_filter = f"[0:v][logo]overlay=x='{logo_x}':y='{logo_y}':shortest=1[with_logo]"

    def _drawtext_options(value: str, fontsize: int, x_expr: str, y_expr: str) -> str:
        options = [f"text='{_escape_drawtext_text(value)}'"]
        if resolved_font:
            options.append(f"fontfile='{escape_filter_path(resolved_font)}'")
        else:
            # Ultimo recurso si no se encontro ningun archivo de fuente
            # conocido: confiar en fontconfig (requiere un ffmpeg compilado
            # con --enable-fontconfig; no todos los builds de Windows lo
            # traen).
            options.append("font=Sans")
        options += [
            f"fontsize={fontsize}",
            f"fontcolor={WATERMARK_FONT_COLOR}",
            f"borderw={WATERMARK_BORDER_WIDTH}",
            f"bordercolor={WATERMARK_BORDER_COLOR}",
            f"shadowx={WATERMARK_SHADOW_OFFSET}",
            f"shadowy={WATERMARK_SHADOW_OFFSET}",
            f"shadowcolor={WATERMARK_SHADOW_COLOR}",
            f"x={x_expr}",
            f"y={y_expr}",
        ]
        return ":".join(options)

    # Mismas formulas que logo_x/logo_y pero evaluadas con w/h (dimensiones
    # del frame en drawtext) y con logo_w/logo_h ya conocidos en Python, para
    # que el texto quede pegado al logo sin superponerse.
    channel_text_x = f"w-{logo_w}-({MARGIN_RIGHT_FRACTION}*w)-{TEXT_LOGO_GAP_PX}-text_w"
    channel_text_y = f"h-{logo_h}-({MARGIN_BOTTOM_FRACTION}*h)+(({logo_h}-text_h)/2)"

    # Cada elemento es el cuerpo de opciones de un drawtext (sin el prefijo
    # "drawtext=" ni las etiquetas de entrada/salida), encadenados en orden.
    stages = [_drawtext_options(text, WATERMARK_FONT_SIZE, channel_text_x, channel_text_y)]

    if cta_lines:
        # Se apilan de abajo hacia arriba (la ultima linea del CTA queda mas
        # cerca del logo). Todas usan la misma fontsize, asi que su text_h
        # renderizado es igual entre si - se puede usar el text_h de cada
        # instancia para calcular cuanto subir sin tener que pasarse valores
        # entre filtros (cada drawtext solo conoce su propia geometria).
        top_of_row_y = f"h-{logo_h}-({MARGIN_BOTTOM_FRACTION}*h)"
        for idx, line in enumerate(reversed(cta_lines)):
            lines_below = idx  # cuantas lineas de CTA ya renderizadas debajo de esta
            cta_x = f"w-({MARGIN_RIGHT_FRACTION}*w)-text_w"
            cta_y = (
                f"({top_of_row_y})-{CTA_GAP_ABOVE_LOGO_PX}-(text_h*{lines_below + 1})"
                f"-({CTA_LINE_GAP_PX}*{lines_below})"
            )
            stages.append(_drawtext_options(line, CTA_FONT_SIZE, cta_x, cta_y))

    filters = [f"[1:v]scale={logo_w}:{logo_h}[logo]", overlay_filter]
    last_label = "with_logo"
    for idx, body in enumerate(stages):
        out_label = "out" if idx == len(stages) - 1 else f"txt{idx}"
        filters.append(f"[{last_label}]drawtext={body}[{out_label}]")
        last_label = out_label

    return ";".join(filters)


def add_watermark(
    clip_path: Path,
    output_path: Path | None = None,
    text: str = WATERMARK_TEXT,
    logo_path: Path = DEFAULT_LOGO_PATH,
    font_file: str | None = None,
    cta_text: str | None = None,
) -> Path:
    """Superpone el logo de YouTube + `text` como watermark permanente
    (100% de la duracion, estatico) sobre `clip_path`.

    Si `output_path` es None, guarda como "<nombre>_wm.mp4" junto al
    original (no destructivo). Si `output_path` es el mismo archivo que
    `clip_path`, lo sobreescribe de forma segura: renderiza a un temporal y
    recien reemplaza el original si ffmpeg termina bien.

    `cta_text` es opcional (por default no se agrega nada): si se pasa, se
    parte en un par de lineas cortas (`wrap_cta_lines`) y se dibuja arriba
    del logo+nombre de canal, en letra chica, para redirigir al publico
    (ej. "Puedes ver el video completo en").
    """
    clip_path = Path(clip_path)
    logo_path = Path(logo_path)
    if not logo_path.exists():
        raise FileNotFoundError(
            f"No se encontro el logo en {logo_path}. Pasa --logo-file para usar otra ruta."
        )

    input_duration = probe_duration(clip_path)
    duration_desc = f"{input_duration:.1f}s" if input_duration is not None else "desconocida (fallo ffprobe)"
    print(f"  Clip de entrada: {clip_path} (duracion detectada: {duration_desc})", flush=True)
    if input_duration is not None and input_duration > MAX_EXPECTED_CLIP_SECONDS:
        raise RuntimeError(
            f"{clip_path} dura {input_duration / 60:.1f} minutos - esto no parece un clip corto "
            "ya cortado, sino probablemente el video original/fuente mezclado por error en la "
            "carpeta. Revisa que --folder apunte solo a la carpeta de clips finales."
        )

    output_path = Path(output_path) if output_path else clip_path.with_name(
        f"{clip_path.stem}_wm{clip_path.suffix}"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    overwriting_in_place = output_path.resolve() == clip_path.resolve()
    render_path = output_path
    if overwriting_in_place:
        render_path = output_path.with_name(f".{output_path.stem}.watermark_tmp{output_path.suffix}")

    cta_lines = wrap_cta_lines(cta_text) if cta_text else None
    logo_w = _logo_scaled_width(logo_path, LOGO_HEIGHT_PX)
    filter_complex = _build_filter_complex(text, logo_w, LOGO_HEIGHT_PX, font_file, cta_lines=cta_lines)

    args = [
        "ffmpeg", "-y",
        "-i", str(clip_path),
        "-loop", "1", "-i", str(logo_path),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-map", "0:a?",
        # "ultrafast": esto es un segundo pase liviano (overlay+texto) sobre
        # un clip corto para redes sociales, no una entrega final - prioriza
        # velocidad sobre tamaño/compresión optima.
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-shortest",
    ]
    if input_duration is not None:
        # Tope explicito ademas de -shortest y overlay=shortest=1: no confiar
        # en un solo mecanismo para cortar la duracion (ver comentario en
        # overlay_filter sobre el bug de eof_action=repeat).
        args += ["-t", f"{input_duration:.3f}"]
    args.append(str(render_path))
    run_ffmpeg_command(args)

    if overwriting_in_place:
        os.replace(render_path, output_path)

    return output_path


def process_folder(
    folder: Path,
    text: str = WATERMARK_TEXT,
    logo_path: Path = DEFAULT_LOGO_PATH,
    font_file: str | None = None,
    overwrite: bool = False,
    limit: int | None = None,
    cta_text: str | None = None,
) -> list[Path]:
    """Aplica el watermark a los .mp4 de `folder` (no recursivo).

    `limit`, si se pasa, procesa solo los primeros N clips (util para
    probar en un clip real antes de correr el batch completo). `cta_text`
    es opcional, ver `add_watermark`.
    """
    folder = Path(folder)
    logo_path = Path(logo_path)
    if not logo_path.exists():
        raise FileNotFoundError(
            f"No se encontro el logo en {logo_path}. Pasa --logo-file para usar otra ruta."
        )

    has_transparency = check_logo_transparency(logo_path)
    if not has_transparency:
        print(
            f"AVISO: {logo_path} no parece tener transparencia real (canal alpha "
            "ausente o siempre opaco). El logo se va a superponer con una caja de "
            "fondo solida detras. Si no es lo que queres, conseguí un PNG con fondo "
            "transparente antes de aplicar esto a todos los clips.",
            flush=True,
        )

    clips = sorted(
        p for p in folder.glob("*.mp4")
        if not p.stem.endswith("_wm") and not p.stem.startswith(".")
    )

    if not clips:
        print(f"No se encontraron .mp4 en {folder}")
        return []

    if limit is not None:
        clips = clips[:limit]

    resolved_font = font_file or _find_font_file()
    print(f"Fuente usada para el watermark: {resolved_font or 'font=Sans (fontconfig, sin archivo especifico)'}")
    print(f"Logo usado: {logo_path} (transparencia real: {'si' if has_transparency else 'NO'})")
    if cta_text:
        print(f"CTA: {' / '.join(wrap_cta_lines(cta_text))!r}")

    total = len(clips)
    results = []
    for i, clip_path in enumerate(clips, start=1):
        print(f"[{i}/{total}] Aplicando watermark a: {clip_path.name}", flush=True)
        output_path = clip_path if overwrite else None
        result = add_watermark(
            clip_path, output_path=output_path, text=text, logo_path=logo_path, font_file=font_file, cta_text=cta_text
        )
        print(f"  -> {result.name}", flush=True)
        results.append(result)

    print(f"Listo: {total} clip(s) procesados.")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Quema el logo de YouTube + texto sobre todos los .mp4 de una carpeta "
        "(clips ya generados, no reprocesa desde el video original)."
    )
    parser.add_argument("--folder", required=True, help="Carpeta con los .mp4 a marcar")
    parser.add_argument("--text", default=WATERMARK_TEXT, help=f"Texto del watermark (default: {WATERMARK_TEXT!r})")
    parser.add_argument(
        "--logo-file",
        default=str(DEFAULT_LOGO_PATH),
        help=f"Ruta al PNG del logo a superponer (default: {DEFAULT_LOGO_PATH})",
    )
    parser.add_argument("--font-file", help="Ruta a un archivo de fuente .ttf/.otf a usar (override de la autodeteccion)")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Sobreescribe el .mp4 original en vez de generar una copia con sufijo _wm (mas riesgoso: sin esto no se toca el original)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Procesa solo los primeros N .mp4 de la carpeta (para probar en 1 clip antes del batch completo, ej. --limit 1)",
    )
    parser.add_argument(
        "--cta-text",
        nargs="?",
        const=DEFAULT_CTA_TEXT,
        default=None,
        help="Mensaje opcional arriba del logo+nombre de canal, en letra chica y en un par de "
        f"lineas cortas, para redirigir al publico (ej. {DEFAULT_CTA_TEXT!r}). Sin este flag, no "
        f"se agrega nada. Pasado sin valor usa el default; con un valor propio, usa ese texto.",
    )
    args = parser.parse_args()

    try:
        process_folder(
            Path(args.folder),
            text=args.text,
            logo_path=Path(args.logo_file),
            font_file=args.font_file,
            overwrite=args.overwrite,
            limit=args.limit,
            cta_text=args.cta_text,
        )
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
