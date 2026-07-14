"""Perfiles de campaña: agrupan marca de agua + comportamiento de split +
estilo de subtitulos bajo un unico ``--campaign <numero>``.

Los perfiles se definen en ``campaigns.json`` (raiz del repo), un objeto
JSON con un bloque por campaña, indexado por su ID numerico (como string).
Agregar una campaña nueva es agregar un bloque con el siguiente numero ahi
- no hace falta tocar codigo en ningun otro archivo.

Formato de cada bloque:

    "3": {
      "name": "Nombre de la campaña",
      "watermark": {"text": "...", "logo": "archivo.png"},   // opcional
      "allow_split": true,                                    // opcional, default true
      "subtitle_style": {                                     // opcional
        "text_color": "white",                                // ver subtitles.KARAOKE_COLOR_ASS
        "font_candidates": ["DejaVu Sans"]                     // en orden de preferencia
      },
      "video_filter": "vintage"                               // opcional, ver src.video_filters
    }

``watermark``/``subtitle_style`` ausentes caen a sin marca / al estilo
default de subtitulos respectivamente. ``logo`` es relativo a la raiz del
repo (PROJECT_ROOT). ``video_filter`` ausente cae a sin filtro; si se pasa
``--filter`` explicito al generar clips, ese gana sobre el de la campaña
(ver `src.clip.cut_clips`).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from src.config import PROJECT_ROOT

CAMPAIGNS_FILE = PROJECT_ROOT / "campaigns.json"


@dataclass
class SubtitleStyle:
    text_color: str = "white"
    font_candidates: list[str] = field(default_factory=lambda: ["DejaVu Sans"])


@dataclass
class Campaign:
    id: str
    name: str
    watermark_text: str | None
    watermark_logo: Path | None
    allow_split: bool
    subtitle_style: SubtitleStyle
    video_filter: str | None = None


def _load_raw() -> dict:
    if not CAMPAIGNS_FILE.exists():
        return {}
    return json.loads(CAMPAIGNS_FILE.read_text(encoding="utf-8"))


def _parse_campaign(campaign_id: str, raw: dict) -> Campaign:
    wm = raw.get("watermark") or {}
    sub = raw.get("subtitle_style") or {}
    logo = wm.get("logo")
    return Campaign(
        id=campaign_id,
        name=raw.get("name") or f"Campaña {campaign_id}",
        watermark_text=wm.get("text"),
        watermark_logo=(PROJECT_ROOT / logo) if logo else None,
        allow_split=raw.get("allow_split", True),
        subtitle_style=SubtitleStyle(
            text_color=sub.get("text_color", "white"),
            font_candidates=sub.get("font_candidates") or ["DejaVu Sans"],
        ),
        video_filter=raw.get("video_filter"),
    )


def get_all_campaigns() -> dict[str, Campaign]:
    """Todas las campañas de campaigns.json, parseadas, indexadas por ID y
    ordenadas numericamente. Fuente unica para --list-campaigns (CLI) y
    para poblar el selector de campañas en app.py, sin duplicar el parseo.
    """
    raw_all = _load_raw()
    return {
        cid: _parse_campaign(cid, data)
        for cid, data in sorted(raw_all.items(), key=lambda kv: int(kv[0]))
    }


def get_campaign(campaign_id: str) -> Campaign:
    """Devuelve el perfil de la campaña `campaign_id` (ej. "1", "2").

    Lanza ValueError con la lista de campañas validas si el ID no existe.
    """
    campaigns = get_all_campaigns()
    if campaign_id not in campaigns:
        valid = ", ".join(f"{cid} ({c.name})" for cid, c in campaigns.items())
        raise ValueError(
            f"Campaña desconocida: {campaign_id!r}. Campañas validas: {valid or '(ninguna definida en campaigns.json)'}"
        )
    return campaigns[campaign_id]


def describe_campaign(campaign: Campaign) -> str:
    """Resumen de una linea de la config de `campaign` (marca, split, subtitulos).

    Usado tanto por list_campaigns() (CLI) como por la pestaña "Campañas" de
    app.py para no duplicar el texto descriptivo.
    """
    wm_desc = f'marca "{campaign.watermark_text}"' if campaign.watermark_text else "sin marca"
    split_desc = (
        "permite dividir en partes"
        if campaign.allow_split
        else "PROHIBIDO dividir en partes (recorta a sub-segmento via LLM o descarta)"
    )
    filter_desc = f", filtro: {campaign.video_filter!r}" if campaign.video_filter else ""
    return (
        f"{wm_desc}, {split_desc}, subtitulos: color {campaign.subtitle_style.text_color!r}, "
        f"fuente preferida {campaign.subtitle_style.font_candidates[0]!r}{filter_desc}"
    )


def list_campaigns() -> None:
    """Imprime todas las campañas disponibles con un resumen de su config."""
    campaigns = get_all_campaigns()
    if not campaigns:
        print(f"No hay campañas definidas en {CAMPAIGNS_FILE}")
        return
    print("Campañas disponibles:")
    for cid, campaign in campaigns.items():
        print(f"  {cid}: {campaign.name} - {describe_campaign(campaign)}")
