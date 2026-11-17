"""
backend/tasks/transcribe.py
---------------------------
Transcription module for ClipGenerator.
Extracts audio from video and generates word-level timestamps using:
  1. Groq Whisper Cloud API (whisper-large-v3, blazing fast & 100% free)
  2. faster-whisper (local offline fallback)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple

from backend.config import get_settings
from backend.tasks.ffmpeg_utils import run_ffmpeg

logger = logging.getLogger(__name__)


def extract_audio(video_path: Path, output_audio_path: Path) -> Path:
    """
    Extract mono 48kbps MP3 audio from a video file.
    48kbps mono speech keeps even a 2-hour video under 35 MB,
    easily fitting within API upload limits while retaining high transcription accuracy.
    """
    output_audio_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-y",
        "-i", str(video_path),
        "-vn",                    # no video
        "-ac", "1",               # mono
        "-ar", "16000",           # 16kHz sample rate optimal for Whisper
        "-b:a", "48k",            # 48 kbps
        str(output_audio_path),
    ]
    run_ffmpeg(args, check=True)
    return output_audio_path


def _transcribe_with_groq(audio_path: Path, api_key: str, model_name: str) -> Tuple[List[Dict], str]:
    """
    Transcribe audio via the Groq Whisper API with word-level timestamps.
    """
    from groq import Groq

    client = Groq(api_key=api_key)
    logger.info("Transcribing audio with Groq Whisper API (model=%s)...", model_name)

    with open(audio_path, "rb") as f:
        # verbose_json with word timestamp granularity
        response = client.audio.transcriptions.create(
            file=(audio_path.name, f),
            model=model_name,
            response_format="verbose_json",
            timestamp_granularities=["word", "segment"],
        )

    # Response is a Transcription object or dict-like
    resp_dict = response.to_dict() if hasattr(response, "to_dict") else (
        response.model_dump() if hasattr(response, "model_dump") else json.loads(response)
    )

    full_text = resp_dict.get("text", "")
    raw_words = resp_dict.get("words", [])

    words: List[Dict] = []
    if raw_words:
        for w in raw_words:
            word_str = w.get("word", "").strip()
            if word_str:
                words.append({
                    "word": word_str,
                    "start": float(w.get("start", 0.0)),
                    "end": float(w.get("end", 0.0)),
                })
    else:
        # If words weren't returned, extract from segments
        segments = resp_dict.get("segments", [])
        for seg in segments:
            seg_text = seg.get("text", "").strip()
            s_start = float(seg.get("start", 0.0))
            s_end = float(seg.get("end", s_start + 1.0))
            seg_words = seg_text.split()
            if not seg_words:
                continue
            word_dur = (s_end - s_start) / max(1, len(seg_words))
            for i, sw in enumerate(seg_words):
                w_s = s_start + i * word_dur
                w_e = w_s + word_dur
                words.append({"word": sw, "start": round(w_s, 2), "end": round(w_e, 2)})

    logger.info("Groq transcription successful: %d words, %d characters.", len(words), len(full_text))
    return words, full_text


def _transcribe_with_local_whisper(audio_path: Path) -> Tuple[List[Dict], str]:
    """
    Transcribe audio locally using faster-whisper as offline fallback.
    """
    from faster_whisper import WhisperModel

    settings = get_settings()
    logger.info(
        "Loading local faster-whisper model: %s on %s (%s)...",
        settings.whisper_model_size,
        settings.whisper_device,
        settings.whisper_compute_type,
    )

    model = WhisperModel(
        settings.whisper_model_size,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
    )

    segments_generator, info = model.transcribe(
        str(audio_path),
        word_timestamps=True,
        beam_size=5,
    )

    words: List[Dict] = []
    text_chunks: List[str] = []

    for seg in segments_generator:
        text_chunks.append(seg.text)
        if seg.words:
            for w in seg.words:
                words.append({
                    "word": w.word.strip(),
                    "start": float(w.start),
                    "end": float(w.end),
                })
        else:
            # Approximate word timestamps if model didn't emit words
            seg_words = seg.text.strip().split()
            dur = (seg.end - seg.start) / max(1, len(seg_words))
            for i, sw in enumerate(seg_words):
                ws = seg.start + i * dur
                we = ws + dur
                words.append({"word": sw, "start": round(ws, 2), "end": round(we, 2)})

    full_text = " ".join(text_chunks).strip()
    return words, full_text


def transcribe_video(job_id: str, video_path: str) -> Tuple[List[Dict], str]:
    """
    Main entrypoint:
    1. Extracts audio from video.
    2. Transcribes with Groq Whisper API (primary) or local faster-whisper (fallback).
    3. Cleans up temporary audio.
    4. Returns (words, transcript_text).
    """
    settings = get_settings()
    video = Path(video_path)
    audio_path = video.parent / f"extracted_{job_id}.mp3"

    try:
        logger.info("[%s] Extracting audio from %s...", job_id, video.name)
        extract_audio(video, audio_path)

        # 1. Try Groq Whisper API if key is available
        if settings.groq_api_key and settings.groq_api_key.startswith("gsk_"):
            try:
                return _transcribe_with_groq(
                    audio_path,
                    settings.groq_api_key,
                    settings.groq_whisper_model,
                )
            except Exception as e:
                logger.warning("[%s] Groq Whisper API failed (%s). Falling back to local whisper...", job_id, e)

        # 2. Local Whisper fallback
        return _transcribe_with_local_whisper(audio_path)

    finally:
        # Clean up temporary audio file
        if audio_path.exists():
            try:
                audio_path.unlink()
            except OSError:
                pass
