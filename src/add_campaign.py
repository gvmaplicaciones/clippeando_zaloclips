"""Crea campañas "estandar" en campaigns.json sin editarlo a mano.

Patron estandar (el mismo que usan las campañas "Ampeter"/"Ibai": logo de
YouTube + nombre de canal como watermark, split permitido, subtitulos
blancos):

    {
      "name": "<nombre bonito>",
      "watermark": {"text": "<canal de YouTube>", "logo": "Youtube_logo.png"},
      "allow_split": true,
      "subtitle_style": {"text_color": "white"}
      # + "video_filter" si se eligio uno (ver src.video_filters)
    }

El ID se asigna solo (maximo ID existente + 1, nunca hardcodeado). No usa
logica propia para leer/interpretar campañas existentes: reutiliza
src.campaigns (mismo modulo que usan --list-campaigns y el resto del
pipeline) para no duplicar el parseo.
"""
from __future__ import annotations

import argparse
import json

from src.campaigns import CAMPAIGNS_FILE
from src.campaigns import list_campaigns as list_campaigns_cli
from src.video_filters import FILTER_LABELS, VIDEO_FILTERS

DEFAULT_LOGO_FILENAME = "Youtube_logo.png"


def _load_raw() -> dict:
    if not CAMPAIGNS_FILE.exists():
        return {}
    return json.loads(CAMPAIGNS_FILE.read_text(encoding="utf-8"))


def _save_raw(data: dict) -> None:
    # Se reescribe ordenado por ID numerico para que el archivo quede legible
    # a mano, aunque el orden de las claves en si no le importa a json.loads.
    ordered = dict(sorted(data.items(), key=lambda kv: int(kv[0])))
    CAMPAIGNS_FILE.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def next_campaign_id(data: dict | None = None) -> str:
    """Maximo ID existente + 1 (como string), o "1" si no hay ninguna campaña."""
    data = data if data is not None else _load_raw()
    if not data:
        return "1"
    return str(max(int(cid) for cid in data) + 1)


def find_campaign_by_channel(channel: str, data: dict | None = None) -> str | None:
    """ID de una campaña existente cuyo watermark.text coincide con `channel`, o None."""
    data = data if data is not None else _load_raw()
    for cid, entry in data.items():
        if (entry.get("watermark") or {}).get("text") == channel:
            return cid
    return None


def build_standard_campaign(
    channel: str,
    display_name: str | None = None,
    video_filter: str | None = None,
) -> dict:
    """Arma (sin guardar) el bloque de campaña estandar para `channel`."""
    if video_filter is not None and video_filter not in VIDEO_FILTERS:
        valid = ", ".join(VIDEO_FILTERS)
        raise ValueError(f"Filtro de video desconocido: {video_filter!r}. Filtros validos: {valid}")

    entry: dict = {
        "name": display_name or channel,
        "watermark": {"text": channel, "logo": DEFAULT_LOGO_FILENAME},
        "allow_split": True,
        "subtitle_style": {"text_color": "white"},
    }
    if video_filter:
        entry["video_filter"] = video_filter
    return entry


def add_standard_campaign(
    channel: str,
    display_name: str | None = None,
    video_filter: str | None = None,
    allow_duplicate: bool = False,
) -> tuple[str, dict]:
    """Agrega una campaña estandar para `channel` a campaigns.json y la guarda.

    Devuelve (id_asignado, entrada_creada). Lanza ValueError si `channel` ya
    existe en alguna campaña y `allow_duplicate` es False - el llamador
    decide si preguntar al usuario o abortar directamente.
    """
    if not channel or not channel.strip():
        raise ValueError("El nombre del canal no puede estar vacio.")
    channel = channel.strip()

    data = _load_raw()

    existing_id = find_campaign_by_channel(channel, data)
    if existing_id is not None and not allow_duplicate:
        raise ValueError(
            f"Ya existe la campaña {existing_id} ({data[existing_id].get('name')!r}) con el mismo "
            f"canal ({channel!r}). Volve a llamar con allow_duplicate=True (o confirma el prompt "
            "interactivo / pasa --force) para crear una duplicada de todos modos."
        )

    new_id = next_campaign_id(data)
    entry = build_standard_campaign(channel, display_name=display_name, video_filter=video_filter)
    data[new_id] = entry
    _save_raw(data)
    return new_id, entry


def _print_created(campaign_id: str, entry: dict) -> None:
    print(f"\nCampaña creada con ID {campaign_id} (en {CAMPAIGNS_FILE}):")
    print(json.dumps({campaign_id: entry}, ensure_ascii=False, indent=2))


def _prompt_filter_choice() -> str | None:
    names = list(VIDEO_FILTERS)
    print("Filtro de video para esta campaña:")
    print("  0: Sin filtro")
    for idx, name in enumerate(names, start=1):
        print(f"  {idx}: {name} - {FILTER_LABELS[name]}")
    while True:
        choice = input("Elegi un numero (Enter = sin filtro): ").strip()
        if not choice or choice == "0":
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(names):
            return names[int(choice) - 1]
        print("Opcion invalida, intenta de nuevo.")


def _interactive_add() -> None:
    channel = input("Nombre del canal de YouTube (tal como debe aparecer en el watermark): ").strip()
    while not channel:
        channel = input("El nombre del canal no puede estar vacio. Nombre del canal: ").strip()

    display_name = input("Nombre para mostrar (Enter para usar el mismo): ").strip() or None

    video_filter = _prompt_filter_choice()

    data = _load_raw()
    existing_id = find_campaign_by_channel(channel, data)
    allow_duplicate = False
    if existing_id is not None:
        print(
            f"\nAviso: ya existe la campaña {existing_id} ({data[existing_id].get('name')!r}) "
            f"con el mismo canal ({channel!r})."
        )
        answer = input("Crear de todos modos una campaña duplicada? [s/N]: ").strip().lower()
        if answer not in ("s", "si", "sí", "y", "yes"):
            print("Cancelado, no se creo ninguna campaña.")
            return
        allow_duplicate = True

    new_id, entry = add_standard_campaign(
        channel, display_name=display_name, video_filter=video_filter, allow_duplicate=allow_duplicate
    )
    _print_created(new_id, entry)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Crea una campaña 'estandar' (watermark logo+canal, split permitido, "
        "subtitulos blancos) en campaigns.json, sin editarlo a mano. Sin --channel, modo "
        "interactivo (pregunta por input())."
    )
    parser.add_argument(
        "--channel",
        help="Nombre del canal de YouTube tal como debe aparecer en el watermark (ej. 'IbaiLlanos'). "
        "Sin esto, arranca el modo interactivo.",
    )
    parser.add_argument("--display-name", help="Nombre 'bonito' para mostrar (default: igual al canal).")
    parser.add_argument(
        "--filter",
        dest="video_filter",
        help="Filtro de video default para esta campaña (ver src.video_filters.VIDEO_FILTERS). "
        "Sin esto, sin filtro.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Si el canal ya existe en otra campaña, crea igual una duplicada en vez de abortar "
        "(en modo no interactivo no hay prompt para confirmar).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Lista las campañas existentes (igual que --list-campaigns de src.clip) y termina.",
    )
    args = parser.parse_args()

    if args.list:
        list_campaigns_cli()
        return

    if args.channel:
        try:
            new_id, entry = add_standard_campaign(
                args.channel,
                display_name=args.display_name,
                video_filter=args.video_filter,
                allow_duplicate=args.force,
            )
        except ValueError as e:
            parser.error(str(e))
        _print_created(new_id, entry)
        return

    _interactive_add()


if __name__ == "__main__":
    main()
