"""
Check MIN_CONFIDENCE (and the OTHER rule) against real LLM confidences instead of guessing.

Run the classifier on a test set first (the predictions CSV has doc_type / status / confidence per file), then:
  python testing/tools/tune_thresholds.py predictions.csv ground_truth.csv [--other-auto 0.95] [--target-precision 1.0]

For every MIN_CONFIDENCE it re-applies the status rules to the stored final answers and reports:
  auto      = files that would be used without a human (RL-type labels at or above the threshold, plus confident OTHER)
  precision = how many of those are correct
  bad_auto  = wrong labels that would go out without review (the number you want at 0)
The file name is never used. Recommended value = the lowest threshold with bad_auto == 0.
"""
import argparse
import csv

ap = argparse.ArgumentParser()
ap.add_argument("predictions")
ap.add_argument("ground_truth")
ap.add_argument("--target-precision", type=float, default=1.0)
ap.add_argument("--other-auto", type=float, default=0.95, help="OTHER_AUTO_CONFIDENCE: a confident OTHER is final")
a = ap.parse_args()

gt = {r["file_name"]: r["expected_label"] for r in csv.DictReader(open(a.ground_truth, encoding="utf-8"))}
preds = [r for r in csv.DictReader(open(a.predictions, encoding="utf-8"))
         if r.get("confidence") not in (None, "") and r["file_name"] in gt]
print(f"{len(preds)} scored files ({sum(1 for r in preds if gt[r['file_name']] == 'OTHER')} out-of-RL)\n")


def stats(xs):
    xs = sorted(xs)
    return f"{xs[0]:.2f} / {xs[len(xs) // 2]:.2f} / {xs[-1]:.2f}  (n={len(xs)})" if xs else "-"


labelled = [r for r in preds if r["doc_type"] != "OTHER"]
print("Confidence by outcome (min / median / max):")
print(f"  RL label correct  {stats([float(r['confidence']) for r in labelled if r['doc_type'] == gt[r['file_name']]])}")
print(f"  RL label wrong    {stats([float(r['confidence']) for r in labelled if r['doc_type'] != gt[r['file_name']]])}")
others = [r for r in preds if r["doc_type"] == "OTHER"]
print(f"  OTHER correct     {stats([float(r['confidence']) for r in others if gt[r['file_name']] == 'OTHER'])}")
print(f"  OTHER wrong       {stats([float(r['confidence']) for r in others if gt[r['file_name']] != 'OTHER'])}\n")


def evaluate(t):
    auto = ok = 0
    for r in preds:
        y, label, conf = gt[r["file_name"]], r["doc_type"], float(r["confidence"])
        if label == "OTHER":
            if conf < a.other_auto:
                continue                      # unsure OTHER -> review
        elif conf < t:
            continue                          # unsure label -> review
        auto += 1
        ok += (label == y)
    return auto, ok


rows = []
for t in [x / 100 for x in range(50, 101)]:
    auto, ok = evaluate(t)
    rows.append((t, auto, ok, (ok / auto) if auto else 1.0, auto - ok))

print("MIN_CONFIDENCE  auto  precision  bad_auto")
for t, auto, ok, prec, bad in rows:
    if round(t * 100) % 5 == 0:
        print(f"  {t:>12.2f}  {auto:>4}  {prec:>9.0%}  {bad:>8}")

good = [r for r in rows if r[3] >= a.target_precision]
print()
if good:
    best = min(good, key=lambda r: r[0])
    print(f"Lowest MIN_CONFIDENCE with auto-precision >= {a.target_precision:.0%}: {best[0]:.2f} (auto {best[1]}, bad_auto {best[4]})")
    print("Small test sets overfit: prefer a value a few points above the lowest one that works.")
else:
    print("No threshold reaches the target. Look at the wrong answers first (evaluate.py lists them).")
