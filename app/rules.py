"""The rules: the prompt-injection guard, and when a label is safe to use automatically."""
import re

from app.config import settings
from app.rl import OTHER

_INJECTION = [
    ("ignore-instructions", r"\b(ignore|disregard|forget|override)\b[^.\n]{0,30}\b(previous|prior|above|earlier|all|any|your|the)\b[^.\n]{0,20}\b(instructions?|prompts?|rules|guidelines)\b"),
    ("role-change", r"\b(you are now|you're now|from now on you)\b"),      # NOT "act as": real contracts say "shall act as a Data Processor"
    ("system-prompt", r"\b(system prompt|developer message)\b"),
    ("new-instructions", r"\bnew instructions?\b"),
    ("fake-output", r"[\"']?doc_type[\"']?\s*[:=]"),
    ("delimiter", r"</?\s*document\s*>"),
]


def detect_injection(text, rl_names=()):
    """Name of the first prompt-injection pattern found in the document text, or None."""
    t = text or ""
    for name, pat in _INJECTION:
        if re.search(pat, t, re.I):
            return name
    if rl_names:
        names = "|".join(re.escape(n) for n in rl_names) + "|other"
        if re.search(rf"\b(classify|label|categori[sz]e|mark)\b[^.\n]{{0,40}}\b(as|to|into)\b[^.\n]{{0,12}}\b({names})\b", t, re.I):
            return "tells-the-classifier-the-answer"
    return None


def decide(doc_type, confidence, min_confidence=None):
    """The status of one answer, from the content alone (the file name is never used). Only `classified` is safe to use
    without a human.

    - OTHER: confident (OTHER_AUTO_CONFIDENCE) -> classified (final: not one of the RL types); otherwise no_match (review)
    - an RL type below MIN_CONFIDENCE -> low_confidence
    - otherwise classified"""
    min_confidence = settings.MIN_CONFIDENCE if min_confidence is None else min_confidence
    if doc_type == OTHER:
        return "classified" if confidence >= settings.OTHER_AUTO_CONFIDENCE else "no_match"
    if confidence < min_confidence:
        return "low_confidence"
    return "classified"
