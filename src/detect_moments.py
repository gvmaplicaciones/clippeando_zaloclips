"""Deteccion de momentos destacados en una transcripcion usando la API de Claude."""
from __future__ import annotations

import json
from pathlib import Path

import anthropic

from src.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, MOMENTS_DIR

SYSTEM_PROMPT = (
    "Sos un editor de video experto en encontrar momentos altamente "
    "compartibles (clips) dentro de la transcripcion de un stream o VOD. "
    "Devolves exclusivamente JSON valido, sin texto adicional."
)

USER_PROMPT_TEMPLATE = """\
A continuacion esta la transcripcion de un video, segmentada con timestamps
en segundos. Identifica los mejores momentos para convertir en clips cortos
(entre 15 y 90 segundos), priorizando humor, momentos virales, reacciones
fuertes o historias completas con inicio y cierre claros.

Devolve un JSON con esta forma exacta:
{{
  "moments": [
    {{"start": <segundos>, "end": <segundos>, "title": "<titulo corto>", "reason": "<por que es un buen clip>"}}
  ]
}}

Transcripcion:
{transcript_text}
"""


def _build_transcript_text(segments: list[dict]) -> str:
    lines = [f"[{seg['start']:.1f} - {seg['end']:.1f}] {seg['text']}" for seg in segments]
    return "\n".join(lines)


def detect_moments(transcript_path: Path, output_dir: Path | None = None) -> Path:
    """Analiza un transcript JSON y guarda los momentos detectados como JSON."""
    transcript_path = Path(transcript_path)
    target_dir = output_dir or MOMENTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    transcript_text = _build_transcript_text(transcript["segments"])

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=4096,
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

    moments = json.loads(response_text)

    output_path = target_dir / f"{transcript_path.stem}.json"
    output_path.write_text(json.dumps(moments, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
