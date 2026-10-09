"""One classification request, end to end: checks, the run, the count check and the answer. No HTTP in here."""
from app import classifier
from app.errors import RequestRejected
from app.logs import api_log
from app.rl import STATUSES, reconcile, rl_hash, summarize_rows, validate_rl
from app.text import MultiTextSource

async def classify_request(rl_items, files, rl_id=None):
    """rl_items: [{name, description, count?}], files: [{file_id, chunks|text, file_name?}].
    Returns the response dict, or raises RequestRejected (with the HTTP status to answer)."""
    if not files:
        raise RequestRejected(422, "send at least one file")
    ids = [f["file_id"] for f in files]
    if len(set(ids)) != len(ids):
        raise RequestRejected(422, "duplicate file_id in request")
    try:
        rl = validate_rl(rl_items)
    except ValueError as e:
        raise RequestRejected(422, str(e))
    rl_id = rl_id or rl_hash(rl)
    api_log.info("classify files=%d rl_id=%s categories=%d", len(ids), rl_id, len(rl))
    try:
        rows, usage = await classifier.arun(rl, MultiTextSource(files), rl_id)
    except RuntimeError as e:                      # no API key, bad key, unknown model (RunAborted is a RuntimeError)
        raise RequestRejected(503, str(e))
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
    results = [{k: v for k, v in r.items() if k not in ("doc_type", "status", "confidence")} for r in rows]  # all in `tags`
    return {"rl_id": rl_id, "results": results, "counts": counts, "by_status": by_status, "by_type": by_type, "usage": usage}
