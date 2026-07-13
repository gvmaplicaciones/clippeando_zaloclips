"""Descarga de VODs con yt-dlp y extraccion de audio hacia INPUT_DIR."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import ffmpeg
import yt_dlp
from dotenv import load_dotenv

from src.config import INPUT_DIR, METADATA_DIR
from src.ffmpeg_utils import run as run_ffmpeg

# Clientes de YouTube que si respetan las cookies de sesion. Los clientes
# moviles (android, ios, android_vr, etc.) ignoran la autenticacion por
# cookies aunque esten bien configuradas, y son los que suelen disparar el
# "Sign in to confirm you're not a bot" incluso con un cookies.txt valido.
_COOKIE_AWARE_PLAYER_CLIENTS = ["web", "web_creator", "tv"]


def _cookie_ydl_opts() -> dict:
    """Arma las opciones de autenticacion de yt-dlp leyendo el entorno en el
    momento de la llamada (no al importar el modulo).

    Esto evita un problema tipico en notebooks (Colab): si src.download ya
    se importo antes de configurar las cookies, una constante leida solo al
    importar quedaria congelada en None aunque despues se actualice el .env
    o os.environ, y habria que reiniciar el runtime para que tome efecto.
    Volver a llamar a load_dotenv() y leer os.getenv() en cada descarga
    evita ese problema.
    """
    load_dotenv(override=True)

    cookies_file = os.getenv("YTDLP_COOKIES_FILE") or None
    cookies_from_browser = os.getenv("YTDLP_COOKIES_FROM_BROWSER") or None
    player_client_env = os.getenv("YTDLP_PLAYER_CLIENT") or None

    opts: dict = {}
    if cookies_file:
        opts["cookiefile"] = cookies_file
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)

    if player_client_env:
        player_clients = [c.strip() for c in player_client_env.split(",") if c.strip()]
    elif cookies_file or cookies_from_browser:
        player_clients = _COOKIE_AWARE_PLAYER_CLIENTS
    else:
        player_clients = None

    if player_clients:
        opts["extractor_args"] = {"youtube": {"player_client": player_clients}}

    return opts


def _save_metadata(info: dict, output_dir: Path | None = None) -> Path | None:
    """Guarda id/titulo/etc. de un video para reutilizarlos sin volver a pedirlos a yt-dlp."""
    video_id = info.get("id")
    if not video_id:
        return None

    target_dir = output_dir or METADATA_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "id": video_id,
        "title": info.get("title"),
        "webpage_url": info.get("webpage_url"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader"),
    }
    metadata_path = target_dir / f"{video_id}.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata_path


def get_video_title(video_path: Path, metadata_dir: Path | None = None) -> str:
    """Titulo real del video segun METADATA_DIR, o el nombre de archivo si no hay metadata."""
    video_path = Path(video_path)
    target_dir = metadata_dir or METADATA_DIR
    metadata_path = target_dir / f"{video_path.stem}.json"

    if metadata_path.exists():
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            title = data.get("title")
            if title:
                return title
        except (json.JSONDecodeError, OSError):
            pass

    return video_path.stem


def fetch_metadata(url: str) -> dict:
    """Obtiene y guarda id/titulo/etc. de una URL sin descargar el video ni el audio.

    Util para backfillear el titulo de un video que ya se descargo antes de
    que este modulo empezara a guardar metadata.
    """
    ydl_opts = {"noplaylist": True, "skip_download": True}
    ydl_opts.update(_cookie_ydl_opts())

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    _save_metadata(info)
    return info


def download_vod(url: str, output_dir: Path | None = None) -> Path:
    """Descarga un video desde una URL y devuelve la ruta local del archivo.

    Si YouTube (u otro sitio) pide confirmar que no sos un bot, configura
    YTDLP_COOKIES_FILE (ruta a un cookies.txt exportado del navegador) o
    YTDLP_COOKIES_FROM_BROWSER (solo en una maquina con navegador local) en
    el .env. Ver README para instrucciones de como exportar el cookies.txt.
    """
    target_dir = output_dir or INPUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        "outtmpl": str(target_dir / "%(id)s.%(ext)s"),
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
    }
    ydl_opts.update(_cookie_ydl_opts())

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)

    _save_metadata(info)

    downloaded_path = Path(filename)
    if downloaded_path.suffix != ".mp4":
        downloaded_path = downloaded_path.with_suffix(".mp4")
    return downloaded_path


def extract_audio(video_path: Path, output_dir: Path | None = None) -> Path:
    """Extrae el audio de un video a WAV 16kHz mono (formato esperado por Whisper)."""
    video_path = Path(video_path)
    target_dir = output_dir or INPUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    audio_path = target_dir / f"{video_path.stem}.wav"

    stream = (
        ffmpeg
        .input(str(video_path))
        .output(str(audio_path), ac=1, ar=16000, acodec="pcm_s16le")
        .overwrite_output()
    )
    run_ffmpeg(stream)

    return audio_path


def ingest(url: str | None = None, file: str | None = None) -> tuple[Path, Path]:
    """Descarga (o toma un archivo existente) y extrae su audio. Devuelve (video, audio)."""
    if not url and not file:
        raise ValueError("Debes indicar --url o --file")

    video_path = download_vod(url) if url else Path(file)
    audio_path = extract_audio(video_path)

    return video_path, audio_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Descarga un VOD y extrae su audio")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="URL del VOD a descargar (yt-dlp)")
    group.add_argument("--file", help="Ruta a un video ya existente en input/")
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Con --url: solo obtiene y guarda el titulo/metadata, sin descargar video ni audio "
        "(util para backfillear el titulo de un video descargado antes de esta funcionalidad).",
    )
    args = parser.parse_args()

    if args.metadata_only:
        if not args.url:
            parser.error("--metadata-only requiere --url")
        info = fetch_metadata(args.url)
        print(f"Metadata guardada: id={info.get('id')!r} title={info.get('title')!r}")
        return

    video_path, audio_path = ingest(url=args.url, file=args.file)

    print(f"Video: {video_path}")
    print(f"Audio: {audio_path}")


if __name__ == "__main__":
    main()
