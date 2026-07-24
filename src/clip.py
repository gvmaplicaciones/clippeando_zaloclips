"""Genera los clips finales: recorte -> vertical 9:16 -> subtitulos karaoke.

Para momentos que superan SPLIT_THRESHOLD segundos, en vez de un clip largo
se generan varias partes de ~PART_MIN_DURATION-PART_MAX_DURATION segundos,
con overlay de cliffhanger ("PARTE N+1 PROXIMAMENTE") al final de cada parte
salvo la ultima. Si hay timestamps por palabra en el transcript, los cortes
entre partes se ajustan al limite de palabra mas cercano.

Sin marca de agua: este modulo no aplica watermark. Si mas adelante queres
marca de agua sobre clips ya generados, usa src/watermark.py aparte (script
independiente que no toca este flujo).
"""
from __future__ import annotations

import argparse
import json
import math
import tempfile
from pathlib import Path

from src.config import OUTPUT_DIR
from src.download import get_video_title
from src.ffmpeg_utils import concat_clips, verify_video_and_audio
from src.naming import sanitize_filename
from src.subtitles import build_ass, burn_subtitles, flatten_words, segments_in_range, words_in_range
from src.vertical import VERTICAL_HEIGHT, VERTICAL_WIDTH, crop_to_vertical, crop_to_vertical_blur_fill
from src.video_filters import apply_video_filter, list_video_filters, resolve_video_filter

SPLIT_THRESHOLD = 90.0
PART_MIN_DURATION = 60.0
PART_MAX_DURATION = 70.0
SNAP_WINDOW = 5.0  # tolerancia (segundos) para ajustar cortes a limites de palabra


def _compute_num_parts(duration: float) -> int:
    """Numero de partes para un momento largo, apuntando a partes de ~60-70s."""
    if duration <= SPLIT_THRESHOLD:
        return 1
    n = math.ceil(duration / PART_MAX_DURATION)
    while n > 2 and duration / n < PART_MIN_DURATION:
        n -= 1
    return max(n, 2)


def _snap_to_word_boundary(target_time: float, words: list[dict], window: float = SNAP_WINDOW) -> float:
    """Ajusta target_time al 'end' de la palabra mas cercana dentro de +-window segundos.

    Si no hay palabras cerca (o no hay timestamps por palabra disponibles),
    devuelve target_time sin modificar.
    """
    candidates = [w for w in words if abs(w["end"] - target_time) <= window]
    if not candidates:
        return target_time
    return min(candidates, key=lambda w: abs(w["end"] - target_time))["end"]


def _plan_parts(moment: dict, all_words: list[dict]) -> list[dict]:
    """Divide un momento en partes si supera SPLIT_THRESHOLD segundos.

    Devuelve una lista de dicts {start, end, part_num, is_last, title} con
    tiempos absolutos (mismo eje que el video original). part_num es None
    para momentos que no se dividen.
    """
    start, end = moment["start"], moment["end"]
    duration = end - start
    n = _compute_num_parts(duration)
    hook_title = moment.get("hook_title") or "Momento destacado"

    if n == 1:
        return [{"start": start, "end": end, "part_num": None, "is_last": True, "title": hook_title}]

    raw_part_len = duration / n
    boundaries = [start]
    for i in range(1, n):
        target = start + i * raw_part_len
        boundaries.append(_snap_to_word_boundary(target, all_words))
    boundaries.append(end)
    # El ajuste a limite de palabra podria dejar dos boundaries muy cerca o
    # invertidas; forzamos orden estrictamente creciente.
    for i in range(1, len(boundaries)):
        if boundaries[i] <= boundaries[i - 1]:
            boundaries[i] = boundaries[i - 1] + 1.0

    original_ref = moment.get("hook_title") or moment.get("reason", "")
    brief = (original_ref[:40] + "…") if len(original_ref) > 40 else original_ref

    parts = []
    for i in range(n):
        part_num = i + 1
        title = hook_title if part_num == 1 else f"PARTE {part_num} - Sigue: {brief}"
        parts.append(
            {
                "start": boundaries[i],
                "end": boundaries[i + 1],
                "part_num": part_num,
                "is_last": part_num == n,
                "title": title,
            }
        )
    return parts


def _dedupe_name(base_name: str, used_names: set[str]) -> str:
    """Evita pisar un clip si dos momentos generan el mismo nombre de archivo."""
    candidate = f"{base_name}.mp4"
    n = 2
    while candidate.lower() in used_names:
        candidate = f"{base_name} ({n}).mp4"
        n += 1
    used_names.add(candidate.lower())
    return candidate


VERTICAL_MODES = ("crop", "blur_fill")
HOOK_VERTICAL_MODES = ("crop", "blur_fill", "same")


def cut_clips(
    video_path: Path,
    moments_path: Path,
    transcript_path: Path | None = None,
    output_dir: Path | None = None,
    video_title_override: str | None = None,
    max_clips: int | None = None,
    video_filter: str | None = None,
    vertical_mode: str = "crop",
    hook_teaser: bool = True,
    hook_vertical_mode: str = "same",
) -> list[Path]:
    """Genera los clips finales a partir de los momentos detectados.

    `video_filter` (una clave de src.video_filters.VIDEO_FILTERS, ej.
    "vintage") se aplica sobre todo el clip DESPUES del crop vertical pero
    ANTES de quemar subtitulos, para que el texto siempre quede nitido
    encima del filtro. Sin `video_filter`, sin cambios.

    `max_clips`, si se pasa, limita cuantos MOMENTOS se procesan (no
    archivos finales): se ordenan por score descendente y se descartan
    todos salvo los `max_clips` mejores ANTES de generar nada, para no
    gastar tiempo de ffmpeg en momentos que no se van a usar. Un momento
    largo que termina dividido en varias partes (PARTE 1/PARTE 2) sigue
    contando como un solo momento para este limite, aunque genere mas de
    un .mp4.

    Los clips se guardan en ``<output_dir>/<titulo del video>/``, nombrados
    "<hook_title>.mp4" (o "<hook_title> PARTE N.mp4" para momentos
    divididos en partes). El titulo del video sale de `video_title_override`
    si se paso explicitamente (ej. desde app.py, un campo "Nombre del
    video" que el usuario completa a mano); si no, de la metadata guardada
    por src.download, o del nombre de archivo si no hay metadata ni
    override.

    Pipeline por cada parte: recorte + crop vertical 9:16 en una sola pasada
    (para que el corte quede en el frame exacto) -> filtro de video opcional
    -> quemado de subtitulos karaoke (+ titulo inicial y cliffhanger si
    aplica). Sin marca de agua - para eso, src/watermark.py aparte sobre
    los clips ya generados.
    """
    video_path = Path(video_path)
    moments_path = Path(moments_path)
    base_dir = output_dir or OUTPUT_DIR

    if vertical_mode not in VERTICAL_MODES:
        raise ValueError(f"--vertical-mode invalido: {vertical_mode!r}. Opciones: {', '.join(VERTICAL_MODES)}")
    if hook_vertical_mode not in HOOK_VERTICAL_MODES:
        raise ValueError(f"--hook-vertical-mode invalido: {hook_vertical_mode!r}. Opciones: {', '.join(HOOK_VERTICAL_MODES)}")

    # Resolver el filtro de video ANTES de generar nada: si el nombre no
    # existe, mejor fallar de una con un mensaje claro que despues de
    # procesar todos los clips.
    resolved_video_filter = resolve_video_filter(video_filter)

    video_title = sanitize_filename(video_title_override) if video_title_override else sanitize_filename(get_video_title(video_path))
    target_dir = base_dir / video_title
    target_dir.mkdir(parents=True, exist_ok=True)

    moments = json.loads(moments_path.read_text(encoding="utf-8"))

    if max_clips is not None and len(moments) > max_clips:
        ranked = sorted(moments, key=lambda m: m.get("score", 0), reverse=True)
        selected, discarded = ranked[:max_clips], ranked[max_clips:]
        print(
            f"--max-clips {max_clips}: se seleccionan los {max_clips} momentos de mayor score "
            f"de los {len(moments)} detectados (se descartan {len(discarded)} antes de generar nada)."
        )
        for m in discarded:
            print(f"  Descartado (score {m.get('score', '?')}): {m.get('hook_title') or '(sin titulo)'!r}")
        # Se vuelve a ordenar por tiempo de inicio para generar los clips en
        # el mismo orden cronologico de siempre, no por score.
        moments = sorted(selected, key=lambda m: m["start"])

    transcript = None
    if transcript_path is not None and Path(transcript_path).exists():
        transcript = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
    all_words = flatten_words(transcript) if transcript else []

    clip_paths: list[Path] = []
    used_names: set[str] = set()

    _crop_fn = crop_to_vertical_blur_fill if vertical_mode == "blur_fill" else crop_to_vertical

    # hook_vertical_mode "same" (default) -> mismo modo que el cuerpo.
    if hook_vertical_mode == "same":
        _hook_crop_fn = _crop_fn
    elif hook_vertical_mode == "blur_fill":
        _hook_crop_fn = crop_to_vertical_blur_fill
    else:  # "crop"
        _hook_crop_fn = crop_to_vertical

    with tempfile.TemporaryDirectory(prefix="clip_pipeline_") as tmp:
        tmp_dir = Path(tmp)

        for i, moment in enumerate(moments, start=1):
            hook_title = sanitize_filename(moment.get("hook_title") or "Momento destacado")
            m_hook_start = moment.get("hook_start")
            m_hook_end = moment.get("hook_end")

            for part in _plan_parts(moment, all_words):
                part_start, part_end = part["start"], part["end"]
                part_num = part["part_num"]
                p = part_num or 0

                base_name = hook_title if part_num is None else f"{hook_title} PARTE {part_num}"
                final_name = _dedupe_name(base_name, used_names)

                vertical_path = tmp_dir / f"vertical_{i:02d}_{p}.mp4"
                filtered_path = tmp_dir / f"filtered_{i:02d}_{p}.mp4"
                ass_path = tmp_dir / f"sub_{i:02d}_{p}.ass"
                final_path = target_dir / final_name

                _crop_fn(video_path, vertical_path, start=part_start, duration=part_end - part_start)

                source_for_subtitles = vertical_path
                if resolved_video_filter is not None:
                    apply_video_filter(vertical_path, filtered_path, resolved_video_filter)
                    source_for_subtitles = filtered_path

                cliffhanger_text = None
                if part_num is not None and not part["is_last"]:
                    cliffhanger_text = f"PARTE {part_num + 1} PROXIMAMENTE"

                words = words_in_range(transcript, part_start, part_end) if transcript else []
                segments = (
                    segments_in_range(transcript, part_start, part_end)
                    if transcript and not words
                    else []
                )

                # Hook teaser: solo en clips simples (part_num is None) o en
                # la primera parte de un momento dividido. No tiene sentido
                # preponer un teaser a PARTE 2, 3, etc.
                use_hook = (
                    hook_teaser
                    and m_hook_start is not None
                    and m_hook_end is not None
                    and (part_num is None or part_num == 1)
                )

                if use_hook:
                    hook_start = float(m_hook_start)
                    hook_end = min(float(m_hook_end), hook_start + 4.0)
                    hook_dur = hook_end - hook_start

                    hook_v = tmp_dir / f"hook_v_{i:02d}_{p}.mp4"
                    hook_f = tmp_dir / f"hook_f_{i:02d}_{p}.mp4"
                    hook_ass = tmp_dir / f"hook_s_{i:02d}_{p}.ass"
                    hook_subbed = tmp_dir / f"hook_sub_{i:02d}_{p}.mp4"
                    full_subbed = tmp_dir / f"full_sub_{i:02d}_{p}.mp4"

                    # Hook: siempre crop_to_vertical (pantalla completa) salvo
                    # que --hook-vertical-mode lo sobreescriba explicitamente.
                    _hook_crop_fn(video_path, hook_v, start=hook_start, duration=hook_dur)
                    hook_src = hook_v
                    if resolved_video_filter is not None:
                        apply_video_filter(hook_v, hook_f, resolved_video_filter)
                        hook_src = hook_f

                    hook_words = words_in_range(transcript, hook_start, hook_end) if transcript else []
                    hook_segs = (
                        segments_in_range(transcript, hook_start, hook_end)
                        if transcript and not hook_words else []
                    )
                    burn_subtitles(
                        hook_src,
                        build_ass(
                            duration=hook_dur,
                            words=hook_words or None,
                            segments=hook_segs or None,
                            hook_overlay_text=moment.get("hook_overlay_text") or None,
                        ),
                        hook_ass,
                        hook_subbed,
                    )

                    # Clip completo con subtítulos → temp
                    burn_subtitles(
                        source_for_subtitles,
                        build_ass(
                            duration=part_end - part_start,
                            words=words or None,
                            segments=segments or None,
                            title=part["title"],
                            cliffhanger_text=cliffhanger_text,
                        ),
                        ass_path,
                        full_subbed,
                    )

                    concat_clips(hook_subbed, full_subbed, final_path)
                    verify_video_and_audio(final_path)

                else:
                    burn_subtitles(
                        source_for_subtitles,
                        build_ass(
                            duration=part_end - part_start,
                            words=words or None,
                            segments=segments or None,
                            title=part["title"],
                            cliffhanger_text=cliffhanger_text,
                        ),
                        ass_path,
                        final_path,
                    )

                clip_paths.append(final_path)

    return clip_paths


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regenera los clips finales a partir de un video, sus momentos y "
        "(opcionalmente) su transcript ya existentes, sin re-descargar ni re-transcribir."
    )
    parser.add_argument("--video", help="Ruta al video original (ej. input/xxx.mp4)")
    parser.add_argument("--moments", help="Ruta al JSON de momentos (moments/xxx.json)")
    parser.add_argument(
        "--transcript",
        help="Ruta al transcript JSON (transcripts/xxx.json). Habilita subtitulos karaoke "
        "y el ajuste de cortes a limite de palabra si tiene timestamps por palabra.",
    )
    parser.add_argument(
        "--video-title",
        help="Nombre a usar para la carpeta de salida en vez del titulo automatico "
        "(metadata de yt-dlp o nombre de archivo). Util si el titulo real es muy largo "
        "o preferis organizar las carpetas a mano.",
    )
    parser.add_argument(
        "--max-clips",
        type=int,
        help="Limita cuantos momentos se procesan, quedandose con los de mayor score "
        "(sin esto, sin limite). Se aplica a momentos, no a archivos finales: un "
        "momento largo dividido en PARTE 1/PARTE 2 sigue contando como uno solo.",
    )
    parser.add_argument(
        "--filter",
        dest="video_filter",
        help="Filtro visual (de src.video_filters.VIDEO_FILTERS, ver --list-filters) aplicado "
        "sobre todo el clip antes de los subtitulos. Sin esto, sin filtro.",
    )
    parser.add_argument(
        "--list-filters",
        action="store_true",
        help="Lista los filtros de video disponibles y termina, sin generar clips.",
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

    if not args.video or not args.moments:
        parser.error("--video y --moments son requeridos (salvo con --list-filters)")

    try:
        clip_paths = cut_clips(
            Path(args.video),
            Path(args.moments),
            transcript_path=Path(args.transcript) if args.transcript else None,
            video_title_override=args.video_title,
            max_clips=args.max_clips,
            video_filter=args.video_filter,
            vertical_mode=args.vertical_mode,
            hook_teaser=args.hook_teaser,
            hook_vertical_mode=args.hook_vertical_mode,
        )
    except ValueError as e:
        parser.error(str(e))

    parts = sum(1 for p in clip_paths if " PARTE " in p.stem)
    print(f"Clips generados ({len(clip_paths)}), de los cuales {parts} son partes de momentos largos:")
    for clip_path in clip_paths:
        print(f"  - {clip_path}")


if __name__ == "__main__":
    main()
