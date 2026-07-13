"""Descarga de VODs con yt-dlp y extraccion de audio hacia INPUT_DIR."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import ffmpeg
import yt_dlp
from dotenv import load_dotenv

from src.config import INPUT_DIR
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
    args = parser.parse_args()

    video_path, audio_path = ingest(url=args.url, file=args.file)

    print(f"Video: {video_path}")
    print(f"Audio: {audio_path}")


if __name__ == "__main__":
    main()
