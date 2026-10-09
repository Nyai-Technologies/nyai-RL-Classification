"""How sure is a label? The status is the confidence band, nothing else."""
from app.config import settings


def decide(confidence, min_confidence=None, medium_confidence=None):
    """The status of one answer, from its confidence alone (the file name is never used). The label (`doc_type`) and the
    confidence are always sent as they are; the status only says how far to trust them.

    - confidence >= MIN_CONFIDENCE (80)    -> high
    - above MEDIUM_CONFIDENCE (60), below 80 -> medium
    - MEDIUM_CONFIDENCE (60) or below      -> low"""
    min_confidence = settings.MIN_CONFIDENCE if min_confidence is None else min_confidence
    medium_confidence = settings.MEDIUM_CONFIDENCE if medium_confidence is None else medium_confidence
    if confidence >= min_confidence:
        return "high"
    return "medium" if confidence > medium_confidence else "low"
