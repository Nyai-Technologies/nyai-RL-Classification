"""
Compare classifier output against ground_truth.csv.

predictions.csv needs at least: file_name, doc_type, status
  status in {classified, low_confidence, no_match, error, manual}
  (doc_type = predicted label; use OTHER for no match)

python evaluate.py predictions.csv [ground_truth.csv]   (default: ./ground_truth.csv)
"""
import csv, sys
from collections import Counter, defaultdict

gt_path = sys.argv[2] if len(sys.argv) > 2 else "ground_truth.csv"
gt = {r["file_name"]: r for r in csv.DictReader(open(gt_path, encoding="utf-8"))}
pred = {r["file_name"]: r for r in csv.DictReader(open(sys.argv[1], encoding="utf-8"))}

missing = [f for f in gt if f not in pred]
if missing:
    print(f"WARNING: {len(missing)} files missing from predictions: {missing[:5]}...")

labels = sorted({r["expected_label"] for r in gt.values()})
conf = defaultdict(Counter)
by_case = defaultdict(lambda: [0, 0])
status = Counter()
wrong = []
for f, g in gt.items():
    p = pred.get(f, {})
    y, yhat, st = g["expected_label"], p.get("doc_type", "MISSING"), p.get("status", "missing")
    conf[y][yhat] += 1
    status[st] += 1
    ok = (y == yhat)
    by_case[g["case_type"]][0] += ok
    by_case[g["case_type"]][1] += 1
    if not ok:
        wrong.append((f, y, yhat, st, g["notes"]))

n = len(gt)
correct = sum(conf[y][y] for y in labels)
print(f"\nOverall accuracy: {correct}/{n} = {correct / n:.0%}")

auto = [f for f in gt if pred.get(f, {}).get("status") == "classified"]
auto_ok = sum(pred[f]["doc_type"] == gt[f]["expected_label"] for f in auto)
if auto:
    print(f"Auto-classified (no human needed): {len(auto)}/{n} = {len(auto) / n:.0%}, precision {auto_ok}/{len(auto)} = {auto_ok / len(auto):.0%}")
print("Status counts:", dict(status))

print("\nPer label (precision / recall):")
for y in labels:
    tp = conf[y][y]
    pred_y = sum(conf[t][y] for t in conf)
    actual_y = sum(conf[y].values())
    print(f"  {y:6s} P={tp}/{pred_y or 0:<3} R={tp}/{actual_y}")

print("\nAccuracy by case type:")
for c, (ok, tot) in sorted(by_case.items()):
    print(f"  {c:13s} {ok}/{tot}")

print("\nConfusion (rows = expected, cols = predicted):")
cols = labels + sorted({p for r in conf.values() for p in r} - set(labels))
print("        " + " ".join(f"{c[:6]:>6}" for c in cols))
for y in labels:
    print(f"  {y:6s}" + " ".join(f"{conf[y][c]:>6}" for c in cols))

if wrong:
    print("\nMisclassified:")
    for f, y, yhat, st, note in wrong:
        print(f"  {f}: expected {y}, got {yhat} [{st}] {('- ' + note) if note else ''}")
