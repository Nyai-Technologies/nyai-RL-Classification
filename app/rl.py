"""The RL (the user's list of document types): validation, identity, and the expected-vs-found count check."""
import hashlib
import json

from app.config import settings
from app.logs import log

OTHER = "OTHER"                                              # "none of the RL types fits"
STATUSES = ["classified", "low_confidence", "no_match", "error"]


def validate_rl(rl):
    """Returns [{name, description}] or raises ValueError with a message the caller can show."""
    if not isinstance(rl, list) or len(rl) < 2:
        raise ValueError("RL must be a list of at least 2 {name, description} items")
    if len(rl) > settings.MAX_RL_TYPES:
        raise ValueError(f"RL has {len(rl)} types; the limit is {settings.MAX_RL_TYPES}")
    out, seen = [], set()
    for i, item in enumerate(rl):
        name = str(item.get("name", "")).strip()
        desc = str(item.get("description", "")).strip()
        if not name:
            raise ValueError(f"RL item {i} has no name")
        if len(name) > settings.MAX_NAME_CHARS:
            raise ValueError(f"RL name '{name[:20]}...' is longer than {settings.MAX_NAME_CHARS} characters")
        if len(desc) > settings.MAX_DESC_CHARS:
            raise ValueError(f"RL '{name}' description is longer than {settings.MAX_DESC_CHARS} characters")
        if name.upper() == OTHER or ":" in name:
            raise ValueError(f"invalid RL name '{name}' ('OTHER' is reserved, ':' not allowed)")
        if name.lower() in seen:
            raise ValueError(f"duplicate RL name '{name}'")
        if len(desc) < 20:
            log.warning("RL '%s' has a very short description; accuracy will suffer", name)
        seen.add(name.lower())
        out.append({"name": name, "description": desc})
    log.info("RL validated: %s", [c["name"] for c in out])
    return out


def rl_hash(rl):
    """Stable id derived from the RL's content (order does not matter)."""
    blob = json.dumps(sorted((c["name"], c["description"]) for c in rl), ensure_ascii=False)
    return "rl_" + hashlib.sha1(blob.encode()).hexdigest()[:10]


def reconcile(rl_items, rows):
    """Expected count (from the user) vs what was found, per RL name.
    classified = auto-labelled; needs_review = low_confidence candidates not yet confirmed."""
    out = []
    for it in rl_items:
        name, expected = it["name"], it.get("count")
        classified = sum(1 for r in rows if r["status"] == "classified" and r["doc_type"] == name)
        review = sum(1 for r in rows if r["status"] == "low_confidence" and r["doc_type"] == name)
        if expected is None:
            status = "NO_COUNT_GIVEN"
        elif classified == expected:
            status = "OK"
        elif classified < expected:
            gap = expected - classified
            status = "PENDING_REVIEW" if review >= gap else f"MISSING {gap}"
        else:
            status = f"EXTRA {classified - expected}"
        out.append({"rl": name, "expected": expected, "classified": classified, "needs_review": review, "status": status})
    return out


def summarize_rows(rl, rows):
    """(status -> count, RL type -> classified count) for log lines and the CLI."""
    counts, by_type = {}, {c["name"]: 0 for c in rl}
    by_type[OTHER] = 0
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        if r["status"] == "classified":
            by_type[r["doc_type"]] = by_type.get(r["doc_type"], 0) + 1
    return counts, by_type
