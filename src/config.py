"""Configuracion centralizada del pipeline.

Todas las rutas son relativas a la raiz del proyecto (o configurables por
variable de entorno) para que el mismo codigo funcione igual en Google
Colab, en un VPS o en una laptop. No hay rutas absolutas hardcodeadas.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _resolve_dir(env_var: str, default: Path) -> Path:
    value = os.getenv(env_var)
    path = Path(value).expanduser() if value else default
    path.mkdir(parents=True, exist_ok=True)
    return path


PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", Path(__file__).resolve().parent.parent)).expanduser()

INPUT_DIR = _resolve_dir("INPUT_DIR", PROJECT_ROOT / "input")
TRANSCRIPTS_DIR = _resolve_dir("TRANSCRIPTS_DIR", PROJECT_ROOT / "transcripts")
MOMENTS_DIR = _resolve_dir("MOMENTS_DIR", PROJECT_ROOT / "moments")
OUTPUT_DIR = _resolve_dir("OUTPUT_DIR", PROJECT_ROOT / "output")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "medium")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "auto")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE") or None
