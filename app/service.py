"""One classification request, end to end: checks, the run, the count check and the answer. No HTTP in here."""
import threading

from app import classifier
from app.config import settings
from app.errors import RequestRejected
from app.logs import api_log
from app.rl import STATUSES, reconcile, rl_hash, summarize_rows, validate_rl
from app.text import MultiTextSource

_runs = threading.BoundedSemaphore(settings.MAX_CONCURRENT_RUNS)       # simultaneous requests; protects the API key and the bill


async def classify_request(rl_items, files, rl_id=None, enforce_run_limit=True):
    """rl_items: [{name, description, count?}], files: [{file_id, chunks|text, file_name?}].
    Returns the response dict, or raises RequestRejected (with the HTTP status to answer).
    enforce_run_limit=False for the Pulsar worker, which bounds its own concurrency (PULSAR_MAX_IN_FLIGHT)."""
    if not files:
        raise RequestRejected(422, "send at least one file")
    if len(files) > settings.MAX_FILES:
        raise RequestRejected(413, f"{len(files)} files in one request; the limit is {settings.MAX_FILES}. Send them in batches.")
    ids = [f["file_id"] for f in files]
    if len(set(ids)) != len(ids):
        raise RequestRejected(422, "duplicate file_id in request")
    try:
        rl = validate_rl(rl_items)
    except ValueError as e:
        raise RequestRejected(422, str(e))
    rl_id = rl_id or rl_hash(rl)
    api_log.info("classify files=%d rl_id=%s categories=%d", len(ids), rl_id, len(rl))
    if enforce_run_limit and not _runs.acquire(blocking=False):
        raise RequestRejected(429, "Too many classification runs in progress. Try again in a moment.")
    try:
        rows, usage = await classifier.arun(rl, MultiTextSource(files), rl_id)
    except RuntimeError as e:                      # no API key, bad key, unknown model (RunAborted is a RuntimeError)
        raise RequestRejected(503, str(e))
    finally:
        if enforce_run_limit:
            _runs.release()
    llm_failed = [r for r in rows if r["status"] == "error" and r["chunks_used"] > 0]
    if len(llm_failed) == len(rows):               # every file had text but the LLM call failed (e.g. provider outage)
        raise RequestRejected(502, f"LLM call failed for every file: {llm_failed[0]['reason']}")
    counts = reconcile(rl_items, rows)
    by_status = {s: 0 for s in STATUSES}
    for r in rows:
        by_status[r["status"]] += 1
    _, by_type = summarize_rows(rl, rows)
    api_log.info("files done: rl_id=%s by_status=%s by_type=%s counts=%s", rl_id, by_status, by_type,
                 [(c["rl"], c["status"]) for c in counts])
    return {"rl_id": rl_id, "results": rows, "counts": counts, "by_status": by_status, "by_type": by_type, "usage": usage}
