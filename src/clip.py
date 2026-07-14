"""Genera los clips finales: recorte -> vertical 9:16 -> subtitulos karaoke.

Para momentos que superan SPLIT_THRESHOLD segundos, en vez de un clip largo
se generan varias partes de ~PART_MIN_DURATION-PART_MAX_DURATION segundos,
con overlay de cliffhanger ("PARTE N+1 PROXIMAMENTE") al final de cada parte
salvo la ultima. Si hay timestamps por palabra en el transcript, los cortes
entre partes se ajustan al limite de palabra mas cercano.
"""
from __future__ import annotations

import argparse
import json
import math
import tempfile
from pathlib import Path

import ffmpeg

from src.campaigns import get_campaign
from src.campaigns import list_campaigns as list_campaign_profiles
from src.config import OUTPUT_DIR
from src.detect_moments import MIN_CLIP_DURATION, shrink_moment_to_subsegment
from src.download import get_video_title
from src.naming import sanitize_filename
from src.subtitles import (
    build_ass,
    burn_subtitles,
    flatten_words,
    resolve_subtitle_font,
    segments_in_range,
    words_in_range,
)
from src.vertical import crop_to_vertical
from src.watermark import add_watermark
from src.watermarks import get_watermark, list_watermarks

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


def cut_clips(
    video_path: Path,
    moments_path: Path,
    transcript_path: Path | None = None,
    output_dir: Path | None = None,
    watermark: str | None = None,
    campaign: str | None = None,
    video_title_override: str | None = None,
) -> list[Path]:
    """Genera los clips finales a partir de los momentos detectados.

    Los clips se guardan en ``<output_dir>/<titulo del video>/``, nombrados
    "<hook_title>.mp4" (o "<hook_title> PARTE N.mp4" para momentos
    divididos en partes). El titulo del video sale de `video_title_override`
    si se paso explicitamente (ej. desde app.py, un campo "Nombre del
    video" que el usuario completa a mano); si no, de la metadata guardada
    por src.download, o del nombre de archivo si no hay metadata ni
    override.

    Pipeline por cada parte: recorte + crop vertical 9:16 en una sola pasada
    (para que el corte quede en el frame exacto) -> quemado de subtitulos
    karaoke (+ titulo inicial y cliffhanger si aplica) -> si se paso
    `watermark` o `campaign`, se aplica la marca sobre el archivo final ya
    en esta misma corrida, sin necesidad de correr src.watermark aparte
    despues.

    `campaign` (ID de src.campaigns.WATERMARKS, ej. "1") agrupa marca de
    agua + `allow_split` + estilo de subtitulos en un solo perfil; no se
    puede combinar con `watermark` suelto (`campaign` ya incluye su propia
    marca). Si la campaña tiene `allow_split=False`, los momentos de mas de
    SPLIT_THRESHOLD segundos no se dividen en partes: en su lugar se le
    pide a Claude (src.detect_moments.shrink_moment_to_subsegment) un
    sub-segmento autocontenido mas corto, o se descarta el momento si
    Claude determina que ninguno funciona solo.
    """
    video_path = Path(video_path)
    moments_path = Path(moments_path)
    base_dir = output_dir or OUTPUT_DIR

    if watermark and campaign:
        raise ValueError(
            "--watermark y --campaign no se pueden combinar: --campaign ya incluye su propia marca de agua."
        )

    # Resolver marca de agua y campaña ANTES de generar nada: si el nombre
    # no existe, mejor fallar de una con un mensaje claro que despues de
    # procesar todos los clips.
    campaign_obj = get_campaign(campaign) if campaign else None
    wm_config = get_watermark(watermark) if watermark else None
    if campaign_obj is not None and campaign_obj.watermark_text:
        wm_config = {"text": campaign_obj.watermark_text, "logo": campaign_obj.watermark_logo}

    allow_split = campaign_obj.allow_split if campaign_obj is not None else True

    ass_style_kwargs: dict = {}
    if campaign_obj is not None:
        preferred_font = campaign_obj.subtitle_style.font_candidates[0]
        resolved_font = resolve_subtitle_font(campaign_obj.subtitle_style.font_candidates)
        if resolved_font != preferred_font:
            print(
                f"Aviso: fuente preferida '{preferred_font}' para la campaña '{campaign_obj.name}' "
                f"no esta instalada en este sistema, usando '{resolved_font}' en su lugar."
            )
        ass_style_kwargs = {"font_name": resolved_font, "text_color": campaign_obj.subtitle_style.text_color}

    video_title = sanitize_filename(video_title_override) if video_title_override else sanitize_filename(get_video_title(video_path))
    target_dir = base_dir / video_title
    target_dir.mkdir(parents=True, exist_ok=True)

    moments = json.loads(moments_path.read_text(encoding="utf-8"))

    transcript = None
    if transcript_path is not None and Path(transcript_path).exists():
        transcript = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
    all_words = flatten_words(transcript) if transcript else []

    clip_paths: list[Path] = []
    used_names: set[str] = set()

    with tempfile.TemporaryDirectory(prefix="clip_pipeline_") as tmp:
        tmp_dir = Path(tmp)

        for i, moment in enumerate(moments, start=1):
            duration = moment["end"] - moment["start"]
            if not allow_split and duration > SPLIT_THRESHOLD:
                label = moment.get("hook_title") or "Momento destacado"
                if transcript is None:
                    print(
                        f"  Momento {i} ('{label}', {duration:.0f}s): esta campaña no permite "
                        "dividir en partes y no hay transcript para pedirle a Claude un "
                        "sub-segmento. Se descarta."
                    )
                    continue
                shrunk = shrink_moment_to_subsegment(
                    moment, transcript, min_duration=MIN_CLIP_DURATION, max_duration=SPLIT_THRESHOLD
                )
                if shrunk is None:
                    print(
                        f"  Momento {i} ('{label}', {duration:.0f}s): no se puede dividir en "
                        "partes para esta campaña y Claude no encontro un sub-segmento "
                        "autocontenido. Se descarta."
                    )
                    continue
                print(
                    f"  Momento {i} ('{label}'): recortado de {duration:.0f}s a un sub-segmento "
                    f"autocontenido de {shrunk['end'] - shrunk['start']:.0f}s "
                    f"({shrunk['start']:.1f}-{shrunk['end']:.1f}s) porque esta campaña no "
                    "permite dividir en partes."
                )
                moment = shrunk

            hook_title = sanitize_filename(moment.get("hook_title") or "Momento destacado")

            for part in _plan_parts(moment, all_words):
                part_start, part_end = part["start"], part["end"]
                part_num = part["part_num"]

                base_name = hook_title if part_num is None else f"{hook_title} PARTE {part_num}"
                final_name = _dedupe_name(base_name, used_names)

                vertical_path = tmp_dir / f"vertical_{i:02d}_{part_num or 0}.mp4"
                ass_path = tmp_dir / f"sub_{i:02d}_{part_num or 0}.ass"
                final_path = target_dir / final_name

                crop_to_vertical(
                    video_path, vertical_path, start=part_start, duration=part_end - part_start
                )

                cliffhanger_text = None
                if part_num is not None and not part["is_last"]:
                    cliffhanger_text = f"PARTE {part_num + 1} PROXIMAMENTE"

                words = words_in_range(transcript, part_start, part_end) if transcript else []
                segments = (
                    segments_in_range(transcript, part_start, part_end)
                    if transcript and not words
                    else []
                )

                ass_content = build_ass(
                    duration=part_end - part_start,
                    words=words or None,
                    segments=segments or None,
                    title=part["title"],
                    cliffhanger_text=cliffhanger_text,
                    **ass_style_kwargs,
                )
                burn_subtitles(vertical_path, ass_content, ass_path, final_path)

                if wm_config is not None:
                    add_watermark(
                        final_path,
                        output_path=final_path,
                        text=wm_config["text"],
                        logo_path=Path(wm_config["logo"]),
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
    wm_group = parser.add_mutually_exclusive_group()
    wm_group.add_argument(
        "--watermark",
        help="Nombre de una marca de src.watermarks.WATERMARKS a aplicar sobre cada clip "
        "final en la misma corrida (ver --list-watermarks). Sin esto, los clips salen sin marca.",
    )
    wm_group.add_argument(
        "--campaign",
        help="ID numerico de una campaña de campaigns.json (agrupa marca de agua + si "
        "permite dividir en partes + estilo de subtitulos en un solo perfil, ver "
        "--list-campaigns). No se puede combinar con --watermark.",
    )
    parser.add_argument(
        "--list-watermarks",
        action="store_true",
        help="Lista las marcas de agua disponibles y termina, sin generar clips.",
    )
    parser.add_argument(
        "--list-campaigns",
        action="store_true",
        help="Lista las campañas disponibles (campaigns.json) y termina, sin generar clips.",
    )
    args = parser.parse_args()

    if args.list_watermarks:
        list_watermarks()
        return
    if args.list_campaigns:
        list_campaign_profiles()
        return

    if not args.video or not args.moments:
        parser.error("--video y --moments son requeridos (salvo con --list-watermarks/--list-campaigns)")

    try:
        clip_paths = cut_clips(
            Path(args.video),
            Path(args.moments),
            transcript_path=Path(args.transcript) if args.transcript else None,
            watermark=args.watermark,
            campaign=args.campaign,
            video_title_override=args.video_title,
        )
    except ValueError as e:
        parser.error(str(e))

    parts = sum(1 for p in clip_paths if " PARTE " in p.stem)
    print(f"Clips generados ({len(clip_paths)}), de los cuales {parts} son partes de momentos largos:")
    for clip_path in clip_paths:
        print(f"  - {clip_path}")


if __name__ == "__main__":
    main()
