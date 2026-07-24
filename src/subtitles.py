"""Subtitulos estilo karaoke (palabra por palabra) quemados con ffmpeg/libass.

Si el transcript no tiene timestamps por palabra (formato viejo, generado
antes de que src.transcribe.py empezara a pedirlos), se degrada a mostrar
el texto del segmento completo como caption estatica en vez de resaltar
palabra por palabra.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import ffmpeg

from src.ffmpeg_utils import escape_filter_path
from src.ffmpeg_utils import run as run_ffmpeg
from src.vertical import VERTICAL_HEIGHT, VERTICAL_WIDTH

FONT_NAME = "DejaVu Sans"

# Colores de texto disponibles para el estilo Karaoke por nombre, en formato
# ASS/libass (&HAABBGGRR: alpha, luego B-G-R en vez de R-G-B). "white" es el
# estilo actual (default). Un nombre no listado aca se puede pasar tal cual
# como hex ASS crudo (build_ass no valida el formato).
KARAOKE_COLOR_ASS = {
    "white": "&H00FFFFFF",
    "yellow": "&H0000FFFF",
}


def resolve_subtitle_font(candidates: list[str], fallback: str = FONT_NAME) -> str:
    """Primer nombre de familia de `candidates` instalado en el sistema (via fontconfig).

    libass resuelve el ``Fontname`` del .ass contra fontconfig por nombre de
    familia, no por archivo (a diferencia de drawtext/fontfile en
    src.watermark), asi que la deteccion es por ``fc-list`` en vez de
    chequear rutas de archivo. Si no se encuentra ninguno de los candidatos
    (ej. "Sora" no esta instalada en esta maquina), cae a `fallback`.
    """
    try:
        result = subprocess.run(
            ["fc-list", "--format=%{family}\n"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return fallback
    if result.returncode != 0:
        return fallback
    available = result.stdout.lower()
    for name in candidates:
        if name.lower() in available:
            return name
    return fallback

# El 20% inferior del frame en TikTok suele estar cubierto por la UI de la
# app (caption propia, botones de interaccion, barra de descripcion). Dejamos
# un margen bastante mayor a ese 20% para que el texto no quede pegado al
# limite.
_TIKTOK_BOTTOM_UI_ZONE = 0.20
CAPTION_MARGIN_V = int(VERTICAL_HEIGHT * (_TIKTOK_BOTTOM_UI_ZONE + 0.05))  # ~480px

CLIFFHANGER_LEAD_IN = 2.5  # segundos antes del final donde aparece el aviso
TITLE_DURATION = 2.0  # segundos que se muestra el hook_title al inicio


def flatten_words(transcript: dict) -> list[dict]:
    """Todas las palabras de todos los segmentos, en tiempo absoluto, ordenadas."""
    words: list[dict] = []
    for seg in transcript.get("segments", []):
        words.extend(seg.get("words") or [])
    words.sort(key=lambda w: w["start"])
    return words


def words_in_range(transcript: dict, start: float, end: float) -> list[dict]:
    """Palabras cuyo punto medio cae en [start, end), con tiempos relativos al clip."""
    out = []
    for w in flatten_words(transcript):
        midpoint = (w["start"] + w["end"]) / 2
        if start <= midpoint < end:
            out.append(
                {
                    "start": max(0.0, w["start"] - start),
                    "end": min(end - start, w["end"] - start),
                    "word": w["word"],
                }
            )
    return out


def segments_in_range(transcript: dict, start: float, end: float) -> list[dict]:
    """Fallback sin timestamps por palabra: segmentos completos como caption estatica."""
    out = []
    for seg in transcript.get("segments", []):
        if seg["end"] <= start or seg["start"] >= end:
            continue
        out.append(
            {
                "start": max(0.0, seg["start"] - start),
                "end": min(end - start, seg["end"] - start),
                "text": seg["text"],
            }
        )
    return out


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    centis = round((secs - int(secs)) * 100)
    if centis == 100:
        centis = 0
        secs += 1
    return f"{hours:d}:{minutes:02d}:{int(secs):02d}.{centis:02d}"


def _escape_ass_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").strip()


def _build_ass_header(karaoke_font: str, karaoke_color_ass: str) -> str:
    """Header .ass parametrizado por campaña: fuente y color del estilo Karaoke
    (el texto palabra-por-palabra). Title, Cliffhanger y HookOverlay mantienen
    su estilo fijo en todas las campañas - solo el texto karaoke varia."""
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {VERTICAL_WIDTH}
PlayResY: {VERTICAL_HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{karaoke_font},88,{karaoke_color_ass},&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,6,0,2,60,60,{CAPTION_MARGIN_V},1
Style: Title,{FONT_NAME},72,&H0000D7FF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,6,0,8,60,60,140,1
Style: Cliffhanger,{FONT_NAME},64,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,3,0,0,5,80,80,0,1
Style: HookOverlay,{FONT_NAME},108,&H0000FFFF,&H000000FF,&H00000000,&HAA000000,-1,0,0,0,100,100,2,0,1,7,3,8,80,80,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(
    duration: float,
    words: list[dict] | None = None,
    segments: list[dict] | None = None,
    title: str | None = None,
    cliffhanger_text: str | None = None,
    font_name: str = FONT_NAME,
    text_color: str = "white",
    hook_overlay_text: str | None = None,
) -> str:
    """Arma el contenido de un archivo .ass para un clip de `duration` segundos.

    `words` (timestamps por palabra, tiempos relativos al clip) tiene
    prioridad para el efecto karaoke; si no hay, se usa `segments`
    (texto completo por frase) como caption estatica de fallback.

    `font_name` y `text_color` controlan el estilo Karaoke (el texto
    principal, palabra-por-palabra) para poder variarlo por campaña;
    `text_color` acepta un nombre conocido de KARAOKE_COLOR_ASS ("white",
    "yellow") o un hex ASS crudo (``&HAABBGGRR``) para colores nuevos sin
    tener que tocar este archivo.

    `hook_overlay_text` (si se pasa) se muestra durante TODO el clip con el
    estilo HookOverlay: fuente grande (~108px), negrita, amarillo con contorno
    negro grueso, centrado en la parte superior del frame. Pensado para el
    tramo de hook-teaser (texto corto en mayúsculas tipo "NO TE VAS A CREER
    ESTO") — queda encima de los subtítulos karaoke que van abajo.
    """
    ass_color = KARAOKE_COLOR_ASS.get(text_color, text_color)
    header = _build_ass_header(font_name, ass_color)
    events: list[str] = []

    if hook_overlay_text:
        text = _escape_ass_text(hook_overlay_text)
        if text:
            events.append(
                f"Dialogue: 2,{_ass_time(0)},{_ass_time(duration)},"
                f"HookOverlay,,0,0,0,,{text}"
            )

    cliffhanger_start = None
    if cliffhanger_text:
        cliffhanger_start = max(0.0, duration - CLIFFHANGER_LEAD_IN)
        events.append(
            f"Dialogue: 1,{_ass_time(cliffhanger_start)},{_ass_time(duration)},"
            f"Cliffhanger,,0,0,0,,{_escape_ass_text(cliffhanger_text)}"
        )

    if title:
        title_end = min(TITLE_DURATION, duration)
        events.append(
            f"Dialogue: 1,{_ass_time(0)},{_ass_time(title_end)},"
            f"Title,,0,0,0,,{_escape_ass_text(title)}"
        )

    def _blocked_by_cliffhanger(start: float) -> bool:
        return cliffhanger_start is not None and start >= cliffhanger_start

    if words:
        for w in words:
            if w["end"] <= w["start"] or _blocked_by_cliffhanger(w["start"]):
                continue
            text = _escape_ass_text(w["word"])
            if not text:
                continue
            events.append(
                f"Dialogue: 0,{_ass_time(w['start'])},{_ass_time(w['end'])},"
                f"Karaoke,,0,0,0,,{text}"
            )
    elif segments:
        for seg in segments:
            if seg["end"] <= seg["start"] or _blocked_by_cliffhanger(seg["start"]):
                continue
            text = _escape_ass_text(seg["text"])
            if not text:
                continue
            events.append(
                f"Dialogue: 0,{_ass_time(seg['start'])},{_ass_time(seg['end'])},"
                f"Karaoke,,0,0,0,,{text}"
            )

    return header + "\n".join(events) + "\n"


def _escape_ffmpeg_filter_path(path: Path) -> str:
    """Arma el valor ``filename='...'`` del filtro ``ass``/``subtitles``.

    Verificado empiricamente contra ffmpeg real (no solo por doc): ni pasar
    la ruta como primer valor posicional con ':' escapado (``ass=C\\:/...``)
    ni envolverla en comillas simples sin escapar el ':' funcionan - ffmpeg
    sigue partiendo el string en el ':' y trata el resto como si fuera la
    siguiente opcion del filtro (``original_size``), fallando con "Unable to
    parse option value ... as image size". Lo unico que funciono fue usar la
    clave explicita ``filename=`` con el valor entre comillas simples Y el
    ':' escapado adentro: ``ass=filename='C\\:/Users/.../clip.ass'`` (ver
    ``src.ffmpeg_utils.escape_filter_path`` para el escapado en si).
    """
    return f"filename='{escape_filter_path(path)}'"


def burn_subtitles(input_path: Path, ass_content: str, ass_path: Path, output_path: Path) -> Path:
    """Escribe `ass_content` en `ass_path` y quema esos subtitulos sobre input_path.

    `ass_path` es un archivo intermedio (el filtro ``ass`` de ffmpeg lo lee
    en disco) que el llamador debe ubicar en una carpeta temporal, no en la
    carpeta de salida final: no es un entregable, solo hace falta durante el
    quemado. `output_path` no tiene por que compartir carpeta con `ass_path`.
    """
    input_path = Path(input_path)
    ass_path = Path(ass_path)
    output_path = Path(output_path)
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ass_path.write_text(ass_content, encoding="utf-8")

    vf = f"ass={_escape_ffmpeg_filter_path(ass_path)}"
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
