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

from src.config import OUTPUT_DIR
from src.subtitles import build_ass, burn_subtitles, flatten_words, segments_in_range, words_in_range
from src.vertical import crop_to_vertical

SPLIT_THRESHOLD = 90.0
PART_MIN_DURATION = 60.0
PART_MAX_DURATION = 70.0
SNAP_WINDOW = 5.0  # tolerancia (segundos) para ajustar cortes a limites de palabra


def _slugify(text: str, max_len: int = 60) -> str:
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in text).strip()
    return safe[:max_len] or "clip"


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


def cut_clips(
    video_path: Path,
    moments_path: Path,
    transcript_path: Path | None = None,
    output_dir: Path | None = None,
) -> list[Path]:
    """Genera los clips finales a partir de los momentos detectados.

    Pipeline por cada parte: recorte + crop vertical 9:16 en una sola pasada
    (para que el corte quede en el frame exacto) -> quemado de subtitulos
    karaoke (+ titulo inicial y cliffhanger si aplica).
    """
    video_path = Path(video_path)
    moments_path = Path(moments_path)
    target_dir = output_dir or OUTPUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    moments = json.loads(moments_path.read_text(encoding="utf-8"))

    transcript = None
    if transcript_path is not None and Path(transcript_path).exists():
        transcript = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
    all_words = flatten_words(transcript) if transcript else []

    clip_paths: list[Path] = []

    with tempfile.TemporaryDirectory(prefix="clip_pipeline_") as tmp:
        tmp_dir = Path(tmp)

        for i, moment in enumerate(moments, start=1):
            for part in _plan_parts(moment, all_words):
                part_start, part_end = part["start"], part["end"]
                part_num = part["part_num"]

                if part_num is None:
                    final_name = f"{video_path.stem}_{i:02d}_{_slugify(part['title'])}.mp4"
                else:
                    final_name = f"{video_path.stem}_{i:02d}_PARTE{part_num}.mp4"

                vertical_path = tmp_dir / f"vertical_{i:02d}_{part_num or 0}.mp4"
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
                )
                burn_subtitles(vertical_path, ass_content, final_path)

                clip_paths.append(final_path)

    return clip_paths


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regenera los clips finales a partir de un video, sus momentos y "
        "(opcionalmente) su transcript ya existentes, sin re-descargar ni re-transcribir."
    )
    parser.add_argument("--video", required=True, help="Ruta al video original (ej. input/xxx.mp4)")
    parser.add_argument("--moments", required=True, help="Ruta al JSON de momentos (moments/xxx.json)")
    parser.add_argument(
        "--transcript",
        help="Ruta al transcript JSON (transcripts/xxx.json). Habilita subtitulos karaoke "
        "y el ajuste de cortes a limite de palabra si tiene timestamps por palabra.",
    )
    args = parser.parse_args()

    clip_paths = cut_clips(
        Path(args.video),
        Path(args.moments),
        transcript_path=Path(args.transcript) if args.transcript else None,
    )

    parts = sum(1 for p in clip_paths if "_PARTE" in p.stem)
    print(f"Clips generados ({len(clip_paths)}), de los cuales {parts} son partes de momentos largos:")
    for clip_path in clip_paths:
        print(f"  - {clip_path}")


if __name__ == "__main__":
    main()
