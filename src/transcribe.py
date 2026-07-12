"""Transcripcion de audio/video con faster-whisper hacia TRANSCRIPTS_DIR."""
from __future__ import annotations

import json
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
        _model = WhisperModel(
            WHISPER_MODEL_SIZE,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
        )
    return _model


def transcribe_video(video_path: Path, output_dir: Path | None = None) -> Path:
    """Transcribe un archivo de video/audio y guarda el resultado como JSON."""
    video_path = Path(video_path)
    target_dir = output_dir or TRANSCRIPTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    model = _get_model()
    segments_iter, info = model.transcribe(str(video_path), language=WHISPER_LANGUAGE)

    segments = [
        {"start": seg.start, "end": seg.end, "text": seg.text.strip()}
        for seg in segments_iter
    ]

    transcript = {
        "source": video_path.name,
        "language": info.language,
        "duration": info.duration,
        "segments": segments,
    }

    output_path = target_dir / f"{video_path.stem}.json"
    output_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
