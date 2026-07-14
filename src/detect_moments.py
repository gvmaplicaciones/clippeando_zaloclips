"""Deteccion de momentos virales en una transcripcion usando la API de Claude."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import anthropic

from src.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, MOMENTS_DIR
from src.content_types import get_content_type_prompt, list_content_types

# Precio por millon de tokens de claude-haiku-4-5 (USD).
PRICE_PER_MTOK_INPUT = 1.00
PRICE_PER_MTOK_OUTPUT = 5.00

# Rango absoluto que se valida en codigo (ver _enforce_duration). El prompt
# pide ademas un rango preferente mas angosto dentro de este.
MIN_CLIP_DURATION = 20.0
MAX_CLIP_DURATION = 180.0

# Prompt default cuando no se pasa --content-type: en vez de un unico
# criterio generico, el modelo primero identifica internamente (sin decirlo
# en la respuesta) a cual de estas 4 categorias pertenece el transcript y
# aplica el criterio de seleccion correspondiente, en una sola llamada. El
# caso "narrativo de un solo streamer" reusa el criterio generico original
# de este pipeline (el que habia antes de agregar tipos de contenido).
AUTO_CLASSIFY_SYSTEM_PROMPT = """\
Sos un editor experto en clipping viral para YouTube Shorts, TikTok y \
Reels. Antes de elegir los momentos, identifica internamente (sin decirlo \
en la respuesta) a cual de estas 4 categorias pertenece el transcript, y \
aplica el criterio de seleccion que corresponda:

SI ES ENTRETENIMIENTO CON INVITADOS (formato Ibai, Sidemen, retos, \
"adivina quien", debates, dinamicas sociales entre varias personas):
Busca tension social, respuestas incomodas, humor espontaneo, caos entre \
invitados, acusaciones, sospechas, reveals, giros, verguenza. Mini-historia \
setup->tension->payoff. El clip debe empezar con hook inmediato (pregunta, \
acusacion, reaccion) y cerrar con sensacion de resolucion. Evita empezar \
con saludos o reglas explicadas sin tension.

SI ES VIAJE/AVENTURA (pais, presupuesto, hoteles, comida local, choque \
cultural):
Busca hook de curiosidad inmediata ("Estamos en el pais mas caro..."), \
choque cultural ("¿Como que eso cuesta tanto?"), precio como tension \
narrativa, problemas del viaje (hotel malo, tormenta, presupuesto \
agotado), momentos visualmente potentes aunque no tengan gran frase, y \
reflexion final con payoff emocional.

SI ES PODCAST/ENTREVISTA (una persona entrevistando a otra, sin dinamica \
de grupo ni componente de viaje):
Se agresivo seleccionando - preferi pocos clips excelentes. Hook inmediato \
con frase de curiosidad/shock. Historia completa con inicio-desarrollo- \
cierre entendible sin contexto previo. Tension progresiva (cada frase mas \
fuerte que la anterior). Prioriza miedo, dolor, humillacion, \
supervivencia, presion psicologica, traicion, orgullo, dilemas morales. \
Empeza el clip desde la frase mas potente del entrevistado, no \
necesariamente desde la pregunta.

SI ES NARRATIVO DE UN SOLO STREAMER (sin invitados, sin viaje, sin \
formato entrevista - ej. gaming, futbol, reaccion en solitario):
Busca ganchos fuertes (frases que enganchan en los primeros segundos), \
punchlines y remates comicos, datos o afirmaciones sorprendentes, \
momentos de humor, controversia u opiniones polemicas, cambios de tono \
marcados, remates de historias o jugadas con inicio y cierre claros.

EN TODOS LOS CASOS:
- Duracion: nunca generes un clip de menos de 20s ni de mas de 180s. El \
rango preferente es 45-90s; usa 20-44s solo para un momento aislado muy \
potente, y 91-180s solo si una secuencia completa necesita todo ese \
contexto para tener sentido.
- Los momentos no pueden solaparse significativamente entre si.
- No inventes timestamps ni dialogo que no este en el transcript.
- Usa TODO el rango de 0 a 100 con criterio real y honesto: la mayoria del \
contenido normal deberia puntuar entre 50 y 70, reserva 90-100 \
unicamente para 1 o 2 momentos verdaderamente excepcionales.

Devuelve SOLO un array JSON, sin texto adicional antes ni despues, con \
este esquema exacto:
[{"start": segundos_float, "end": segundos_float, "hook_title": "titulo \
corto y viral", "reason": "por que funciona en 1 linea", "score": 0-100}]
"""

USER_PROMPT_TEMPLATE = """\
A continuacion esta la transcripcion de un video, segmentada con timestamps
en segundos. Identifica TODOS los momentos con potencial para convertirse en
clips virales, aplicando el criterio de seleccion indicado.

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


def find_moments(
    transcript_path: Path,
    output_dir: Path | None = None,
    content_type: str | None = None,
) -> tuple[Path, list[dict], anthropic.types.Usage]:
    """Analiza un transcript JSON y guarda los momentos detectados como JSON.

    `content_type` (ej. "invitado", "viajes", "podcast" - ver prompts/ y
    --list-content-types) fuerza el criterio de seleccion de ese archivo de
    prompt. Sin `content_type`, se usa AUTO_CLASSIFY_SYSTEM_PROMPT: el
    modelo identifica solo, en la misma llamada, a que tipo de contenido
    pertenece el transcript y aplica el criterio correspondiente - no hace
    falta indicarlo a mano.
    """
    transcript_path = Path(transcript_path)
    target_dir = output_dir or MOMENTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    # Resolver el prompt ANTES de leer/mandar nada: si el content_type no
    # existe, mejor fallar de una con un mensaje claro.
    system_prompt = get_content_type_prompt(content_type) if content_type else AUTO_CLASSIFY_SYSTEM_PROMPT

    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    transcript_text = _build_transcript_text(transcript["segments"])

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=8192,
        system=system_prompt,
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
    parser.add_argument("--transcript", help="Ruta al transcript JSON (de transcribe.py)")
    parser.add_argument(
        "--content-type",
        help="Fuerza el criterio de seleccion de prompts/<nombre>.txt (ver --list-content-types). "
        "Sin esto, el modelo clasifica el transcript solo entre esos mismos criterios.",
    )
    parser.add_argument(
        "--list-content-types",
        action="store_true",
        help="Lista los tipos de contenido disponibles (prompts/*.txt) y termina.",
    )
    args = parser.parse_args()

    if args.list_content_types:
        list_content_types()
        return

    if not args.transcript:
        parser.error("--transcript es requerido (salvo con --list-content-types)")

    try:
        output_path, moments, usage = find_moments(Path(args.transcript), content_type=args.content_type)
    except ValueError as e:
        parser.error(str(e))

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
