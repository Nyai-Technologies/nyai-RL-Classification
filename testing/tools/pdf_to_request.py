"""
DEV TOOL (not part of the product): turn a test set of PDFs into a request file in the shape the service takes.
The product never parses PDFs; the parser service sends text. This stands in for the parser when testing.

  python testing/tools/pdf_to_request.py testing/test_sets/documents_test
  -> testing/test_sets/documents_test/request.json   {"rl": [...with counts], "files": [{file_id, file_name, text}]}

Needs pypdf (testing/requirements-dev.txt). RL comes from rl.json (or rl.csv); expected counts come from rl.json, else are
counted from ground_truth.csv when there is one. A PDF with no text layer gets empty text, like a parser would return.
"""
import argparse
import csv
import json
import os
import re
from collections import Counter

from pypdf import PdfReader

ap = argparse.ArgumentParser()
ap.add_argument("test_set", help="folder with files/*.pdf and rl.json or rl.csv (and optionally ground_truth.csv)")
ap.add_argument("--chars", type=int, default=8000, help="keep about this many characters per file (only the first chunks are used)")
a = ap.parse_args()
d = a.test_set

if os.path.exists(f"{d}/rl.json"):
    rl = json.load(open(f"{d}/rl.json", encoding="utf-8"))
else:
    rl = [{"name": r["name"], "description": r.get("description", "")} for r in csv.DictReader(open(f"{d}/rl.csv", encoding="utf-8"))]
if os.path.exists(f"{d}/ground_truth.csv") and any("count" not in r for r in rl):
    cnt = Counter(r["expected_label"] for r in csv.DictReader(open(f"{d}/ground_truth.csv", encoding="utf-8")))
    for r in rl:
        r.setdefault("count", cnt.get(r["name"], 0))

files, empty = [], 0
for name in sorted(os.listdir(f"{d}/files")):
    if not name.lower().endswith(".pdf"):
        continue
    text = ""
    try:
        for page in PdfReader(f"{d}/files/{name}").pages:
            text += (page.extract_text() or "") + "\n\n"
            if len(text) >= a.chars:
                break
    except Exception:
        text = ""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text).strip()[: a.chars]
    empty += not text
    files.append({"file_id": name, "file_name": name, "text": text})

json.dump({"rl": rl, "files": files}, open(f"{d}/request.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print(f"{d}/request.json: {len(files)} files ({empty} with no text), RL {[(r['name'], r.get('count')) for r in rl]}")
