"""Command line, for testing without the API:

  python -m app.cli --input request.json --out predictions.csv

--input is a JSON file with the same shape as the API body ({"rl": [...], "files": [...]}). Parsing is done upstream."""
import argparse
import csv
import json
import sys

from app import classifier
from app.config import settings
from app.errors import RunAborted
from app.logs import log, setup_logging
from app.rl import rl_hash, summarize_rows, validate_rl
from app.text import MultiTextSource


def load_rl(path):
    """RL from a .json (list, or {"rl": [...]}) or .csv (name, description) file."""
    if path.lower().endswith(".csv"):
        with open(path, newline="", encoding="utf-8") as f:
            rl = [{"name": r["name"], "description": r.get("description", "")} for r in csv.DictReader(f)]
    else:
        with open(path, encoding="utf-8") as f:
            rl = json.load(f)
        if isinstance(rl, dict):          # tolerate {"rl": [...]} / {"rls": [...]}
            rl = rl.get("rl") or rl.get("rls") or rl
    log.info("loaded RL from %s (%d items)", path, len(rl))
    return validate_rl(rl)


def print_summary(rl, rows, u, out_csv):
    counts, by_type = summarize_rows(rl, rows)
    print("status:", counts)
    print("classified per RL type:", by_type)
    print(f"tokens: {u['prompt_tokens']} in + {u['completion_tokens']} out over {u['calls']} calls")
    if u["cost_usd"] is None:
        print("cost: n/a (no price found; set LLM_PRICE_IN_PER_M / LLM_PRICE_OUT_PER_M)")
    else:
        print(f"cost: ${u['cost_usd']:.4f} = Rs {u['cost_inr']:.2f} (at Rs {u['usd_inr_rate']:.2f}/USD) for {len(rows)} files")
        if rows:
            print(f"      per file: ${u['per_file_usd']:.5f} = Rs {u['per_file_inr']:.3f} | "
                  f"estimate for 1000 files: ${u['est_1000_files_usd']:.2f} = Rs {u['est_1000_files_inr']:.2f}")
    if out_csv:
        print(f"-> {out_csv}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Classify the files of a request file (testing). Parsing is done upstream.")
    ap.add_argument("--input", required=True, help='JSON: {"rl": [{name, description, count?}], "files": [{file_id, text|chunks}]}')
    ap.add_argument("--rl", default=None, help="optional RL file (.json or .csv) that replaces the RL inside --input")
    ap.add_argument("--rl-id", default=None, help="stable id for this RL (default: hash of its contents)")
    ap.add_argument("--workers", type=int, default=settings.WORKERS)
    ap.add_argument("--out", default="predictions.csv")
    a = ap.parse_args(argv)
    setup_logging()
    try:
        with open(a.input, encoding="utf-8") as f:
            req = json.load(f)
        rl = load_rl(a.rl) if a.rl else validate_rl(req["rl"])
        rows, u = classifier.run(rl, MultiTextSource(req["files"]), a.rl_id or rl_hash(rl), a.out, workers=a.workers)
        print_summary(rl, rows, u, a.out)
    except (ValueError, KeyError, RunAborted, RuntimeError, OSError) as e:
        log.error("%s", e)
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
