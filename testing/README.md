# Testing

Everything test-related lives here, so the project root stays clean.

    unit_tests/          pytest suite (LLM mocked: no network, no cost)
    tools/               evaluate.py, tune_thresholds.py, pdf_to_request.py
    postman/             Postman collection + environment + sample request/response payloads
    test_sets/           documents_test (50 PDFs, 8 types), contracts_test (374 PDFs, MSA+SOW), scenarios_test (tricky inputs)
    results/             saved outputs of earlier runs (predictions*.csv, logs). Git-ignored.
    requirements-dev.txt pytest, pypdf, reportlab (only needed for testing)

Install the test extras once: `.venv/bin/pip install -r testing/requirements-dev.txt`.

## Unit tests
    .venv/bin/python -m pytest -q testing/unit_tests

## Running a test set
The product takes text, not PDFs. For the two PDF sets, `pdf_to_request.py` plays the parser and writes a `request.json`
(RL with counts + `file_id` and `text` per file). `scenarios_test/request.json` is already in that shape.

    S=testing/test_sets/documents_test
    .venv/bin/python testing/tools/pdf_to_request.py $S
    .venv/bin/python -m app.cli --input $S/request.json --out testing/results/predictions.csv
    .venv/bin/python testing/tools/evaluate.py testing/results/predictions.csv $S/ground_truth.csv
    .venv/bin/python testing/tools/tune_thresholds.py testing/results/predictions.csv $S/ground_truth.csv

Or send a `request.json` to the API: `curl -X POST localhost:8000/rl-classification -H 'Content-Type: application/json' -d @$S/request.json`.
`scenarios_test` has no ground truth: read the results (injection, other languages, short/garbled text, buried content,
combined documents, confusable types, out-of-RL files, empty text).

## Results so far (gpt-5.6-luna)
- documents_test: 43/43 auto-classified correct; the rest went to review (misnamed, out-of-RL, scanned).
- contracts_test: 374/374 correct.
- scenarios_test: no wrong RL label; prompt-injection files were flagged for review.

## Postman
Import `postman/RL-Classification.postman_collection.json` and `postman/local.postman_environment.json`, select the
"RL Classification - local" environment, start the server (`.venv/bin/python -m uvicorn app.main:app --port 8000`) and press
Send. Each request has tests (status, classification, count check, cost) and saved example responses. The raw request and
response bodies are in `postman/payloads/`.
