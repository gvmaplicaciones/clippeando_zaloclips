"""Subtitulos estilo karaoke (palabra por palabra) quemados con ffmpeg/libass.

Si el transcript no tiene timestamps por palabra (formato viejo, generado
antes de que src.transcribe.py empezara a pedirlos), se degrada a mostrar
el texto del segmento completo como caption estatica en vez de resaltar
palabra por palabra.
"""
from __future__ import annotations

from pathlib import Path

import ffmpeg

from src.ffmpeg_utils import run as run_ffmpeg
from src.vertical import VERTICAL_HEIGHT, VERTICAL_WIDTH

FONT_NAME = "DejaVu Sans"

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


_ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {VERTICAL_WIDTH}
PlayResY: {VERTICAL_HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{FONT_NAME},88,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,6,0,2,60,60,{CAPTION_MARGIN_V},1
Style: Title,{FONT_NAME},72,&H0000D7FF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,6,0,8,60,60,140,1
Style: Cliffhanger,{FONT_NAME},64,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,3,0,0,5,80,80,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(
    duration: float,
    words: list[dict] | None = None,
    segments: list[dict] | None = None,
    title: str | None = None,
    cliffhanger_text: str | None = None,
) -> str:
    """Arma el contenido de un archivo .ass para un clip de `duration` segundos.

    `words` (timestamps por palabra, tiempos relativos al clip) tiene
    prioridad para el efecto karaoke; si no hay, se usa `segments`
    (texto completo por frase) como caption estatica de fallback.
    """
    events: list[str] = []

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

    return _ASS_HEADER + "\n".join(events) + "\n"


def _escape_ffmpeg_filter_path(path: Path) -> str:
    """Escapa una ruta para usarla como valor del filtro ``ass``/``subtitles``.

    Dentro del mini-lenguaje de filtros de ffmpeg, ':' separa opciones y
    '\\' es caracter de escape, asi que una ruta de Windows como
    ``C:\\Users\\x\\clip.ass`` rompe el parseo si se pasa tal cual.

    Verificado empiricamente contra ffmpeg real (no solo por doc): ni pasar
    la ruta como primer valor posicional con ':' escapado (``ass=C\\:/...``)
    ni envolverla en comillas simples sin escapar el ':' funcionan - ffmpeg
    sigue partiendo el string en el ':' y trata el resto como si fuera la
    siguiente opcion del filtro (``original_size``), fallando con "Unable to
    parse option value ... as image size". Lo unico que funciono fue usar la
    clave explicita ``filename=`` con el valor entre comillas simples Y el
    ':' escapado adentro: ``ass=filename='C\\:/Users/.../clip.ass'``.

    Se arma como el valor crudo de la opcion ``-vf`` (ver ``burn_subtitles``)
    en vez de via ``.filter()`` de ffmpeg-python: ese metodo aplica su propio
    escapado automatico pensado para grafos de filtros, y al recibir una
    ruta que ya tiene backslashes termina multiplicandolos (`C:\\...` ->
    `C\\\\\\\\\\\\:\\\\...`), generando una ruta corrupta que libass no
    puede abrir.
    """
    escaped = str(path).replace("\\", "/")
    escaped = escaped.replace("'", "'\\''")  # por si la ruta tuviera comillas simples
    escaped = escaped.replace(":", "\\:")
    return f"filename='{escaped}'"


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
