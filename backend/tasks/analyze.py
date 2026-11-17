"""
backend/tasks/analyze.py
------------------------
AI Content Analysis module for ClipGenerator.
Analyzes full video transcripts using Groq LLM (llama-3.3-70b-versatile)
to identify the most striking, viral, educational, or memorable moments.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from backend.config import get_settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an elite viral video editor and storytelling strategist specializing in YouTube Shorts, Instagram Reels, and TikTok.
Your job is to analyze the full transcript of a video (podcast, documentary, educational talk, lecture, interview) and extract the absolute BEST, most engaging clips.

For each clip, you must provide:
1. "title": A punchy, click-worthy headline.
2. "start_seconds": Exact start timestamp (floating point seconds).
3. "end_seconds": Exact end timestamp (floating point seconds).
4. "reason": A detailed editorial reasoning statement (2-4 sentences) explaining:
   - Exactly WHY you chose this specific moment from the video.
   - What makes it striking, memorable, or educational (the hook, unexpected revelation, emotional resonance, or actionable insight).
   - Why it will hold viewers' attention on social media.
5. "viral_score": An estimated retention/virality score from 1 to 10.

Evaluation criteria for clips:
- Hook: Starts with an intriguing statement, bold question, surprising fact, or emotional hook.
- Completeness: Must be a coherent, self-contained thought. DO NOT cut off speakers mid-sentence.
- Optimal Duration: Between 20 and 65 seconds long (never shorter than 15s or longer than 90s).
- Dynamic Count: Choose the optimal number of clips based on content richness (typically 2 to 6 clips).

Return ONLY valid JSON with this exact structure:
{
  "clips": [
    {
      "start_seconds": 12.5,
      "end_seconds": 48.0,
      "title": "Short Catchy Click-Worthy Title",
      "reason": "This moment was chosen because the speaker reveals a counterintuitive truth about human motivation. The opening question immediately hooks the viewer, and the 30-second explanation delivers a complete, high-retention takeaway without any fluff.",
      "viral_score": 9
    }
  ]
}
"""


def _clean_json_response(content: str) -> str:
    """Strip markdown code blocks if the LLM wrapped JSON in ```json ... ```."""
    content = content.strip()
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
    if match:
        return match.group(1).strip()
    return content


def analyze_transcript(transcript_text: str, duration_seconds: float) -> List[Dict[str, Any]]:
    """
    Call Groq LLM to identify top clip segments from the transcript.

    Parameters
    ----------
    transcript_text : Full transcribed text
    duration_seconds : Total length of video in seconds

    Returns
    -------
    List of clip dicts: [{"start_seconds": float, "end_seconds": float, "title": str, "reason": str}]
    """
    settings = get_settings()

    if not transcript_text or len(transcript_text.strip()) < 20:
        logger.warning("Transcript is too short or empty for analysis.")
        return []

    # If duration is 0, estimate from word count (~140 words per minute)
    if duration_seconds <= 0:
        word_count = len(transcript_text.split())
        duration_seconds = max(30.0, (word_count / 140.0) * 60.0)

    # Groq API Key check
    if not settings.groq_api_key or not settings.groq_api_key.startswith("gsk_"):
        logger.warning("No valid Groq API key found. Using heuristic clip selection.")
        return _fallback_clip_selection(transcript_text, duration_seconds)

    try:
        from groq import Groq

        client = Groq(api_key=settings.groq_api_key)
        user_prompt = f"""VIDEO INFORMATION:
Total Duration: {duration_seconds:.1f} seconds (~{duration_seconds / 60:.1f} minutes)

FULL TRANSCRIPT:
\"\"\"
{transcript_text[:18000]}
\"\"\"

Carefully analyze the transcript above and select the highest quality clips that would perform best on YouTube Shorts, Instagram Reels, and TikTok.
Ensure all start_seconds and end_seconds are within 0.0 and {duration_seconds:.1f} seconds.
"""

        candidate_models = [settings.groq_model]
        # Common alternative fallbacks in case the primary model is deprecated or unavailable
        for fallback in ["llama-3.1-8b-instant", "llama3-70b-8192", "mixtral-8x7b-32768"]:
            if fallback not in candidate_models:
                candidate_models.append(fallback)

        response_text = None
        used_model = None

        for model_name in candidate_models:
            try:
                logger.info("Calling Groq LLM (%s) for clip selection...", model_name)
                chat_completion = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.3,
                    max_tokens=2048,
                )
                response_text = chat_completion.choices[0].message.content or ""
                used_model = model_name
                logger.info("Groq LLM (%s) responded successfully.", model_name)
                break
            except Exception as model_err:
                logger.warning("Groq model '%s' failed: %s. Trying next candidate...", model_name, model_err)

        if not response_text:
            raise RuntimeError("All Groq LLM candidate models failed.")

        cleaned_json = _clean_json_response(response_text)
        data = json.loads(cleaned_json)

        # Groq might return {"clips": [...]} or directly a list
        raw_clips = data.get("clips", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

        validated_clips: List[Dict[str, Any]] = []
        for c in raw_clips:
            try:
                start_sec = float(c.get("start_seconds", 0.0))
                end_sec = float(c.get("end_seconds", 0.0))
                title = str(c.get("title", "")).strip() or "Untitled Highlight"
                reason = str(c.get("reason", "")).strip()

                # Basic validation
                if start_sec < 0:
                    start_sec = 0.0
                if end_sec > duration_seconds and duration_seconds > 0:
                    end_sec = duration_seconds
                if end_sec <= start_sec or (end_sec - start_sec) < 8.0:
                    continue  # skip invalid or too-short segments

                validated_clips.append({
                    "start_seconds": round(start_sec, 2),
                    "end_seconds": round(end_sec, 2),
                    "title": title,
                    "reason": reason or "Selected for its engaging delivery and memorable message.",
                })
            except Exception as item_err:
                logger.warning("Error parsing clip item: %s", item_err)
                continue

        logger.info("AI Analysis completed: %d clips selected.", len(validated_clips))
        if validated_clips:
            return validated_clips

    except Exception as e:
        logger.error("Groq LLM analysis failed: %s. Falling back to heuristic selection.", e)

    return _fallback_clip_selection(transcript_text, duration_seconds)


def _fallback_clip_selection(transcript_text: str, duration_seconds: float) -> List[Dict[str, Any]]:
    """
    Heuristic fallback if LLM is unavailable or offline:
    Generates sensible 30-45s clip intervals based on video duration.
    """
    logger.info("Generating fallback clips for duration %.1fs...", duration_seconds)
    clips = []
    if duration_seconds < 25.0:
        clips.append({
            "start_seconds": 0.0,
            "end_seconds": round(duration_seconds, 2),
            "title": "Full Highlight",
            "reason": "Complete short highlight clip.",
        })
        return clips

    # Take first highlight, middle highlight, and closing highlight
    segment_length = min(45.0, duration_seconds * 0.4)
    # Clip 1: Opening hook
    clips.append({
        "start_seconds": 0.0,
        "end_seconds": round(segment_length, 2),
        "title": "Opening Hook & Introduction",
        "reason": "Strong intro setting up the topic.",
    })

    if duration_seconds > 80.0:
        mid_start = duration_seconds * 0.4
        clips.append({
            "start_seconds": round(mid_start, 2),
            "end_seconds": round(min(duration_seconds - 5.0, mid_start + segment_length), 2),
            "title": "Core Insight & Discussion",
            "reason": "Key takeaways and deep dive.",
        })

    return clips
