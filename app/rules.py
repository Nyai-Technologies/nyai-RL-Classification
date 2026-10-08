"""When is a label safe to use automatically?"""
from app.config import settings
from app.rl import OTHER


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
