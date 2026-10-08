"""The classification pipeline: one file (retry, guard, status, cost) and one run of many files (bounded concurrency,
timeouts, spend cap, abort on configuration errors)."""
import asyncio
import csv
import time

from app import llm
from app.config import settings
from app.errors import LLMError, RunAborted
from app.logs import cost_log, file_id_var, log, rl_id_var
from app.pricing import Usage, cost_fields, warm_caches
from app.rl import OTHER, summarize_rows
from app.rules import decide

PRED_COLS = ["file_id", "file_name", "doc_type", "status", "confidence", "reason",
             "attempt", "chunks_used", "tokens_in", "tokens_out", "cost_usd", "cost_inr"]


def with_cost(row, tokens_in, tokens_out):
    """Adds tokens and cost to a file's row, and writes its cost line."""
    out = {**row, "tokens_in": tokens_in, "tokens_out": tokens_out, **cost_fields(tokens_in, tokens_out)}
    cost_log.info("FILE file=%s status=%s tokens_in=%d tokens_out=%d usd=%s inr=%s attempts=%s", row["file_name"],
                  row["status"], tokens_in, tokens_out, out["cost_usd"], out["cost_inr"], row.get("attempt"),
                  extra={"event": "file_cost", "file": row["file_name"], "status": row["status"], "tokens_in": tokens_in,
                         "tokens_out": tokens_out, "cost_usd": out["cost_usd"], "cost_inr": out["cost_inr"],
                         "attempts": row.get("attempt")})
    return out


def classify_file(src, clf, file_id):
    """Blocking wrapper for scripts and tests; the service uses aclassify_file()."""
    return asyncio.run(aclassify_file(src, clf, file_id))


async def aclassify_file(src, clf, file_id):
    name, chunks = src.chunks(file_id, settings.FIRST_CHUNKS)
    row = {"file_id": file_id, "file_name": name}
    log.info("[%s] start: name=%s chunks=%d", file_id, name, len(chunks))
    if not chunks:
        why = "no text received (empty, or a scanned file that needs OCR)"
        log.warning("[%s] %s: %s -> status=error, LLM not called", file_id, name, why)
        return with_cost({**row, "doc_type": "", "status": "error", "confidence": "", "reason": why,
                          "attempt": 1, "chunks_used": 0}, 0, 0)
    try:
        res = await clf.aclassify(name, chunks)
    except LLMError as e:
        log.error("[%s] %s: LLM failed -> status=error: %s", file_id, name, e)
        return with_cost({**row, "doc_type": "", "status": "error", "confidence": "", "reason": str(e)[:200],
                          "attempt": 1, "chunks_used": len(chunks)}, *e.tokens)
    attempt, used = 1, len(chunks)
    tin, tout = res["tokens_in"], res["tokens_out"]
    status = decide(res["doc_type"], res["confidence"])
    log.info("[%s] attempt 1: doc_type=%s conf=%.2f -> %s", file_id, res["doc_type"], res["confidence"], status)

    # unclear -> retry with more chunks; OTHER too, because the title may sit beyond the first chunks
    if status != "classified" or res["doc_type"] == OTHER:
        _, more = src.chunks(file_id, settings.RETRY_CHUNKS)
        if len(more) > len(chunks):
            log.info("[%s] unclear (%s), retrying with %d chunks", file_id, status, len(more))
            try:
                res2 = await clf.aclassify(name, more)
                tin, tout = tin + res2["tokens_in"], tout + res2["tokens_out"]
                res, attempt, used = res2, 2, len(more)
                status = decide(res["doc_type"], res["confidence"])
                log.info("[%s] attempt 2: doc_type=%s conf=%.2f -> %s", file_id, res["doc_type"],
                         res["confidence"], status)
            except LLMError as e:
                tin, tout = tin + e.tokens[0], tout + e.tokens[1]      # failed retry still cost tokens
                log.warning("[%s] retry failed, keeping attempt 1 result: %s", file_id, e)
        else:
            log.info("[%s] no extra chunks available, skipping retry", file_id)

    log.info("[%s] FINAL %s: doc_type=%s status=%s conf=%.2f reason=%s", file_id, name, res["doc_type"], status,
             res["confidence"], res["reason"])
    res = {k: v for k, v in res.items() if k not in ("tokens_in", "tokens_out")}
    return with_cost({**row, **res, "status": status, "attempt": attempt, "chunks_used": used}, tin, tout)


def run(rl, src, rl_id, out_csv=None, workers=None, progress=None, client=None):
    """Blocking wrapper for scripts and tests; the service awaits arun()."""
    async def go():
        try:
            return await arun(rl, src, rl_id, out_csv, workers, progress, client)
        finally:
            await llm.close_shared_clients()
    return asyncio.run(go())


async def arun(rl, src, rl_id, out_csv=None, workers=None, progress=None, client=None):
    """Classifies every file of `src` against `rl`. Returns (rows, usage). Raises RunAborted on a configuration error."""
    rl_id_var.set(rl_id)                            # every log line of this run (and of its file tasks) carries the rl_id
    usage = Usage()
    clf = llm.LLMClassifier(rl, client=client, usage=usage)
    files = list(src.file_ids())
    workers = workers or settings.WORKERS
    log.info("run start: rl_id=%s categories=%d files=%d model=%s concurrency=%d min_conf=%.2f chunks=%d/%d max_chars=%d",
             rl_id, len(rl), len(files), settings.LLM_MODEL, workers, settings.MIN_CONFIDENCE, settings.FIRST_CHUNKS,
             settings.RETRY_CHUNKS, settings.MAX_CHARS)
    await asyncio.to_thread(warm_caches)           # price and FX lookups may hit the network: keep them off the event loop

    abort = {"reason": None}
    gate = asyncio.Semaphore(workers)

    def error_row(fid, reason, attempt=1):
        return {"file_id": fid, "file_name": fid, "doc_type": "", "status": "error", "confidence": "",
                "reason": reason, "attempt": attempt, "chunks_used": 0,
                "tokens_in": 0, "tokens_out": 0, **cost_fields(0, 0)}

    async def work(fid):
        file_id_var.set(fid)                        # per-task context: log lines of this file carry its file_id
        async with gate:
            if abort["reason"]:
                return error_row(fid, f"skipped: run stopped ({abort['reason']})", attempt=0)
            try:
                row = await aclassify_file(src, clf, fid)
                if clf.fatal and not abort["reason"]:
                    abort["reason"] = clf.fatal             # bad key / unknown model: the other files would fail the same way
                return row
            except Exception as e:
                log.exception("[%s] unexpected failure", fid)
                return error_row(fid, str(e)[:200])

    rows, t0 = [], time.time()
    tasks = [asyncio.ensure_future(work(f)) for f in files]       # created in file order, so files start in file order
    for i, fut in enumerate(asyncio.as_completed(tasks), 1):
        rows.append(await fut)
        if progress:
            progress(i, len(files))
        if i % 10 == 0 or i == len(files):
            log.info("progress %d/%d (%.0fs)", i, len(files), time.time() - t0)
    rows.sort(key=lambda r: r["file_name"])
    if clf.fatal:                                   # bad key / unknown model / no access: nothing useful came out
        raise RunAborted(f"LLM configuration problem: {clf.fatal}")

    if out_csv:
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=PRED_COLS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        log.info("wrote %d rows to %s", len(rows), out_csv)

    counts, by_type = summarize_rows(rl, rows)
    u = usage.summary(files=len(files))
    u["aborted"] = abort["reason"]
    if u["cost_usd"] is not None:
        row_sum = sum(r.get("cost_usd") or 0 for r in rows)
        log.info("per-file cost sums to $%.6f; run total $%.6f", row_sum, u["cost_usd"])
    cost_log.info("RUN rl_id=%s files=%d calls=%d tokens_in=%d tokens_out=%d usd=%s inr=%s per_file_usd=%s "
                  "per_file_inr=%s est_1000_files_usd=%s est_1000_files_inr=%s (usd_inr=%s, model=%s)",
                  rl_id, len(files), u["calls"], u["prompt_tokens"], u["completion_tokens"], u["cost_usd"],
                  u["cost_inr"], u.get("per_file_usd"), u.get("per_file_inr"), u.get("est_1000_files_usd"),
                  u.get("est_1000_files_inr"), u["usd_inr_rate"], settings.LLM_MODEL,
                  extra={"event": "run_cost", "files": len(files), "calls": u["calls"], "tokens_in": u["prompt_tokens"],
                         "tokens_out": u["completion_tokens"], "cost_usd": u["cost_usd"], "cost_inr": u["cost_inr"],
                         "per_file_inr": u.get("per_file_inr"), "model": settings.LLM_MODEL, "aborted": u["aborted"]})
    log.info("run done in %.0fs: status=%s by_type=%s usage=%s", time.time() - t0, counts, by_type, u)
    return rows, u
