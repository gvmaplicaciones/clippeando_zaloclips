"""Deteccion de momentos virales en una transcripcion usando la API de Claude."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import anthropic

from src.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, MOMENTS_DIR

# Precio por millon de tokens de claude-haiku-4-5 (USD).
PRICE_PER_MTOK_INPUT = 1.00
PRICE_PER_MTOK_OUTPUT = 5.00

MIN_CLIP_DURATION = 15.0
MAX_CLIP_DURATION = 60.0

SYSTEM_PROMPT = (
    "Sos un experto en clipping viral para TikTok con años de experiencia "
    "identificando los momentos de un video que mejor funcionan como clips "
    "cortos. Analizas transcripciones con timestamps y detectas los "
    "fragmentos con mayor potencial de viralidad. Devolves exclusivamente "
    "JSON valido, sin texto adicional antes ni despues."
)

USER_PROMPT_TEMPLATE = """\
A continuacion esta la transcripcion de un video, segmentada con timestamps
en segundos. Identifica TODOS los momentos con potencial para convertirse en
clips virales de TikTok, evaluando estos criterios:

- Ganchos fuertes (frases que enganchan en los primeros segundos)
- Punchlines y remates comicos
- Datos o afirmaciones sorprendentes
- Momentos de humor
- Controversia o opiniones polemicas
- Cambios de tono marcados
- Remates de historias con inicio y cierre claros

Reglas estrictas:
- Cada clip debe durar EXACTAMENTE entre 15 y 60 segundos (end - start). Nunca
  generes un clip mas corto que 15s ni mas largo que 60s: si un momento fuerte
  dura mas de 60s, recorta el rango a la parte con mas potencial.
- Los momentos no pueden solaparse entre si: el "start" de un momento debe ser
  mayor o igual al "end" del momento anterior.
- Usa TODO el rango de 1 a 10 con criterio real y honesto, no infles los
  scores. La mayoria del contenido normal deberia puntuar entre 5 y 7. Reserva
  8 para momentos muy buenos. Reserva 9-10 unicamente para 1 o 2 momentos
  verdaderamente excepcionales de todo el video, con potencial viral claro.
  Si todo te parece un 7-9, estas siendo demasiado generoso: se mas estricto.

Devolve TODOS los momentos que detectes con score >= 6, sin limitar la
cantidad.

Devolve SOLO un array JSON con esta forma exacta, sin texto adicional:
[
  {{
    "start": 45.2,
    "end": 78.9,
    "reason": "por que este momento tiene potencial",
    "hook_title": "texto corto para overlay en los primeros 2s",
    "score": 8
  }}
]

Transcripcion:
{transcript_text}
"""


def _build_transcript_text(segments: list[dict]) -> str:
    lines = [f"[{seg['start']:.1f} - {seg['end']:.1f}] {seg['text']}" for seg in segments]
    return "\n".join(lines)


def _parse_moments(response_text: str) -> list[dict]:
    """Parsea el array JSON de la respuesta, tolerando texto extra alrededor."""
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\[.*\]", response_text, re.DOTALL)
    if not match:
        raise ValueError(f"No se encontro un array JSON en la respuesta: {response_text!r}")
    return json.loads(match.group(0))


def _enforce_duration(moments: list[dict]) -> list[dict]:
    """Descarta momentos mas cortos que el minimo y recorta los mas largos que el maximo."""
    validated = []
    for moment in moments:
        duration = moment["end"] - moment["start"]
        if duration < MIN_CLIP_DURATION:
            continue
        if duration > MAX_CLIP_DURATION:
            moment = {**moment, "end": moment["start"] + MAX_CLIP_DURATION}
        validated.append(moment)
    return validated


def _remove_overlaps(moments: list[dict]) -> list[dict]:
    """Ordena por 'start' y, ante solapamientos, descarta el de menor score."""
    ordered = sorted(moments, key=lambda m: m["start"])
    result: list[dict] = []
    for moment in ordered:
        if result and moment["start"] < result[-1]["end"]:
            if moment.get("score", 0) > result[-1].get("score", 0):
                result[-1] = moment
            # si no supera el score del momento ya aceptado, se descarta
        else:
            result.append(moment)
    return result


def find_moments(transcript_path: Path, output_dir: Path | None = None) -> tuple[Path, list[dict], anthropic.types.Usage]:
    """Analiza un transcript JSON y guarda los momentos detectados como JSON."""
    transcript_path = Path(transcript_path)
    target_dir = output_dir or MOMENTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    transcript_text = _build_transcript_text(transcript["segments"])

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": USER_PROMPT_TEMPLATE.format(transcript_text=transcript_text),
            }
        ],
    )

    response_text = "".join(
        block.text for block in message.content if block.type == "text"
    ).strip()

    moments = _parse_moments(response_text)
    moments = _enforce_duration(moments)
    moments = _remove_overlaps(moments)

    output_path = target_dir / f"{transcript_path.stem}.json"
    output_path.write_text(json.dumps(moments, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path, moments, message.usage


# Alias retrocompatible con el nombre usado por src.pipeline.
detect_moments = find_moments


def main() -> None:
    parser = argparse.ArgumentParser(description="Detecta momentos virales en un transcript")
    parser.add_argument("--transcript", required=True, help="Ruta al transcript JSON (de transcribe.py)")
    args = parser.parse_args()

    output_path, moments, usage = find_moments(Path(args.transcript))

    cost = (
        usage.input_tokens * PRICE_PER_MTOK_INPUT / 1_000_000
        + usage.output_tokens * PRICE_PER_MTOK_OUTPUT / 1_000_000
    )

    print(f"Momentos detectados: {len(moments)}")
    print(f"Guardado en: {output_path}")
    print(f"Tokens: {usage.input_tokens} in / {usage.output_tokens} out")
    print(f"Costo aproximado: ${cost:.4f}")


if __name__ == "__main__":
    main()
