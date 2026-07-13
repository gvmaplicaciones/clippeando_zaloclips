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

# Rango absoluto que se valida en codigo (ver _enforce_duration). El prompt
# pide ademas un rango preferente mas angosto dentro de este.
MIN_CLIP_DURATION = 20.0
MAX_CLIP_DURATION = 180.0

SYSTEM_PROMPT = (
    "Sos un experto en clipping viral para TikTok con años de experiencia "
    "identificando los momentos de un video que mejor funcionan como clips "
    "cortos. Te especializas en contenido de futbol y gaming, donde los "
    "mejores momentos suelen ser 'jugadas' con planteo, accion y remate, no "
    "una sola frase suelta. Analizas transcripciones con timestamps y "
    "detectas los fragmentos con mayor potencial de viralidad. Devolves "
    "exclusivamente JSON valido, sin texto adicional antes ni despues."
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
- Remates de historias o jugadas con inicio y cierre claros

Reglas estrictas:
- Duracion: el rango preferente y por defecto es 45-90 segundos (el contenido
  es futbol/gaming con narrativa de "jugada" — necesita espacio para el
  planteo, la accion y el remate). Usa 20-44 segundos SOLO para un momento
  aislado muy potente que no necesita mas contexto (una frase o reaccion
  puntual). Usa 91-180 segundos SOLO si una secuencia completa necesita todo
  ese contexto para tener sentido (ej. una tanda de penaltis completa). Nunca
  generes un clip de menos de 20s ni de mas de 180s.
- Los momentos no pueden solaparse significativamente entre si.
- Usa TODO el rango de 0 a 100 con criterio real y honesto, no infles los
  scores. La mayoria del contenido normal deberia puntuar entre 50 y 70.
  Reserva 80 para momentos muy buenos. Reserva 90-100 unicamente para 1 o 2
  momentos verdaderamente excepcionales de todo el video, con potencial
  viral claro. Si todo te parece 70-90, estas siendo demasiado generoso: se
  mas estricto.

Devolve TODOS los momentos que detectes con score >= 60, sin limitar la
cantidad.

Devolve SOLO un array JSON con esta forma exacta, sin texto adicional:
[
  {{
    "start": 45.2,
    "end": 112.4,
    "reason": "por que este momento tiene potencial",
    "hook_title": "texto corto para overlay en los primeros 2s",
    "score": 72
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


def _parse_json_object(response_text: str) -> dict:
    """Parsea un objeto JSON (no array) de la respuesta, tolerando texto extra alrededor."""
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", response_text, re.DOTALL)
    if not match:
        raise ValueError(f"No se encontro un objeto JSON en la respuesta: {response_text!r}")
    return json.loads(match.group(0))


SHRINK_SYSTEM_PROMPT = (
    "Sos un editor de clips para TikTok. Tu tarea es tomar un momento largo "
    "que NO se puede dividir en varias partes numeradas para esta campaña, y "
    "encontrar dentro de el un unico sub-segmento mas corto que funcione "
    "solo, de principio a fin, sin necesitar el resto del momento para tener "
    "sentido. Si ningun sub-segmento de esa duracion es autocontenido (el "
    "momento entero requiere todo su contexto para funcionar), decis que no "
    "es viable en vez de forzar un recorte que quede a medias. Devolves "
    "exclusivamente JSON valido, sin texto adicional antes ni despues."
)

SHRINK_USER_PROMPT_TEMPLATE = """\
Este momento fue detectado como viral pero dura {full_duration:.0f} segundos \
y esta campaña no permite dividirlo en varias partes (PARTE 1/PARTE 2). \
Necesito que selecciones un UNICO sub-segmento autocontenido de entre \
{min_duration:.0f} y {max_duration:.0f} segundos, con inicio y remate \
propios, que capture el nucleo del momento sin necesitar el resto.

Motivo original por el que se detecto este momento: {reason}

Transcripcion del momento completo (timestamps absolutos en segundos):
{transcript_text}

Devolve SOLO un JSON con esta forma exacta, sin texto adicional:
- Si encontras un buen sub-segmento (start/end en segundos absolutos, dentro \
del rango de la transcripcion de arriba):
  {{"viable": true, "start": 123.4, "end": 175.9, "hook_title": "...", "reason": "..."}}
- Si ningun sub-segmento de esa duracion funciona solo:
  {{"viable": false}}
"""


def shrink_moment_to_subsegment(
    moment: dict,
    transcript: dict,
    min_duration: float = MIN_CLIP_DURATION,
    max_duration: float = 90.0,
) -> dict | None:
    """Para un momento que no se puede dividir en partes (campaña con
    ``allow_split=False``), le pide a Claude un unico sub-segmento
    autocontenido de `min_duration`-`max_duration` segundos dentro de el.

    Devuelve un dict con la misma forma que un momento normal (start, end,
    hook_title, reason - el resto de los campos del moment original se
    conserva) o None si el modelo determino que ningun sub-segmento de esa
    duracion funciona por si solo, en cuyo caso el momento se debe descartar.
    """
    start, end = moment["start"], moment["end"]
    segments = [
        seg for seg in transcript.get("segments", [])
        if seg["end"] > start and seg["start"] < end
    ]
    transcript_text = _build_transcript_text(segments)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=1024,
        system=SHRINK_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": SHRINK_USER_PROMPT_TEMPLATE.format(
                    full_duration=end - start,
                    min_duration=min_duration,
                    max_duration=max_duration,
                    reason=moment.get("reason", ""),
                    transcript_text=transcript_text,
                ),
            }
        ],
    )
    response_text = "".join(
        block.text for block in message.content if block.type == "text"
    ).strip()
    result = _parse_json_object(response_text)

    if not result.get("viable"):
        return None

    sub_start = max(start, float(result["start"]))
    sub_end = min(end, float(result["end"]))
    if sub_end - sub_start < min_duration:
        return None
    if sub_end - sub_start > max_duration:
        sub_end = sub_start + max_duration

    return {
        **moment,
        "start": sub_start,
        "end": sub_end,
        "hook_title": result.get("hook_title") or moment.get("hook_title"),
        "reason": result.get("reason") or moment.get("reason"),
    }


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


def dedupe_highlights(highlights: list[dict]) -> list[dict]:
    """Descarta un momento si solapa >50% con uno de mayor score ya aceptado.

    Portado de dedupe_highlights() en SamurAIGPT/AI-Youtube-Shorts-Generator
    (highlights.py), adaptado a nuestros nombres de campo (start/end/score).
    """
    highlights = sorted(highlights, key=lambda h: int(h.get("score", 0)), reverse=True)
    kept: list[dict] = []
    for h in highlights:
        h_start = float(h["start"])
        h_end = float(h["end"])
        h_dur = h_end - h_start
        overlapping = False
        for k in kept:
            latest_start = max(h_start, float(k["start"]))
            earliest_end = min(h_end, float(k["end"]))
            overlap = earliest_end - latest_start
            if overlap > 0 and overlap > 0.5 * h_dur:
                overlapping = True
                break
        if not overlapping:
            kept.append(h)
    return kept


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
    moments = dedupe_highlights(moments)
    moments.sort(key=lambda m: m["start"])

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
