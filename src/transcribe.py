"""Transcripcion de audio/video con faster-whisper hacia TRANSCRIPTS_DIR."""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from faster_whisper import WhisperModel

from src.config import (
    TRANSCRIPTS_DIR,
    WHISPER_COMPUTE_TYPE,
    WHISPER_DEVICE,
    WHISPER_LANGUAGE,
    WHISPER_MODEL_SIZE,
)

_model: WhisperModel | None = None


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        print(
            f"Cargando modelo Whisper (size={WHISPER_MODEL_SIZE}, "
            f"device={WHISPER_DEVICE}, compute_type={WHISPER_COMPUTE_TYPE})...",
            flush=True,
        )
        _model = WhisperModel(
            WHISPER_MODEL_SIZE,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
        )
        print("Modelo cargado.", flush=True)
    return _model


def transcribe_video(audio_path: Path, output_dir: Path | None = None) -> Path:
    """Transcribe un archivo de audio/video y guarda el resultado como JSON.

    Acepta tanto el .wav extraido por ``src.download.extract_audio`` como
    cualquier archivo de video, ya que faster-whisper decodifica el audio
    internamente con ffmpeg sin importar el formato de entrada.
    """
    audio_path = Path(audio_path)
    target_dir = output_dir or TRANSCRIPTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    model = _get_model()

    print(f"Transcribiendo {audio_path} (puede tardar varios minutos)...", flush=True)
    segments_iter, info = model.transcribe(
        str(audio_path), language=WHISPER_LANGUAGE, word_timestamps=True
    )
    print(f"Audio detectado: idioma={info.language}, duracion={info.duration:.1f}s", flush=True)

    segments = []
    for seg in segments_iter:
        segments.append(
            {
                "start": seg.start,
                "end": seg.end,
                "text": seg.text.strip(),
                "words": [
                    {"start": w.start, "end": w.end, "word": w.word.strip()}
                    for w in (seg.words or [])
                ],
            }
        )
        print(
            f"  [{seg.end:7.1f}s / {info.duration:.1f}s] {seg.text.strip()[:70]}",
            flush=True,
        )

    transcript = {
        "source": audio_path.name,
        "language": info.language,
        "duration": info.duration,
        "segments": segments,
    }

    output_path = target_dir / f"{audio_path.stem}.json"
    output_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Transcript guardado en {output_path} ({len(segments)} segmentos).", flush=True)
    return output_path


def main() -> None:
    print("main() de src.transcribe iniciado.", flush=True)

    parser = argparse.ArgumentParser(description="Transcribe un audio/video con faster-whisper")
    parser.add_argument("--audio", required=True, help="Ruta al audio/video a transcribir")
    args = parser.parse_args()

    try:
        output_path = transcribe_video(Path(args.audio))
    except Exception:
        print("ERROR: la transcripcion fallo. Traceback completo:", file=sys.stderr, flush=True)
        traceback.print_exc()
        sys.exit(1)

    print(f"OK. Transcript: {output_path}")


if __name__ == "__main__":
    main()
