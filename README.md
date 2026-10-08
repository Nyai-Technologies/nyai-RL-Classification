# RL Classification

Classifies each file into one of the user's RL document types (or `OTHER`) with an LLM.
Input: file ids + the parsed text (or chunks) from the parser service, and the RL (name, description, expected count).
Output: type, status, confidence and reason per file; a count check per RL type (expected vs found); token and cost totals.
Parsing, chunking, storage and UI are not part of this service: it is stateless.

## Setup
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    cp .env.example .env        # set OPENAI_API_KEY and LLM_MODEL; everything else has a default

## Run
    .venv/bin/python -m uvicorn app.main:app --port 8000
Swagger docs: http://localhost:8000/docs

## API
`POST /rl-classification`
```json
{"rl":    [{"name": "MSA", "description": "Master Services Agreement ...", "count": 1},
           {"name": "SOW", "description": "Statement of Work ...",        "count": 1}],
 "files": [{"file_id": "f-001", "text": "<parsed text>"},
           {"file_id": "f-002", "chunks": ["chunk 1", "chunk 2"], "file_name": "x.pdf"}]}
```
Send one file or many. Response: `results[]` (per file: `doc_type`, `status`, `confidence`, `reason`, tokens, cost),
`counts[]` (per RL type: `expected`, `classified`, `needs_review`, `status` = OK | MISSING n | EXTRA n | PENDING_REVIEW |
NO_COUNT_GIVEN), `by_status`, `by_type` (classified files per RL type, plus OTHER), `usage` (tokens, cost in USD and INR).

The label comes from the content only: the file name is never used and never shown to the model.
Status per file: `classified` (safe to use; `doc_type` is an RL type, or `OTHER` when the model is confident the file is not
in the RL) | `low_confidence` (below MIN_CONFIDENCE, or suspected prompt injection: needs review) | `no_match` (an unsure
OTHER: needs review) | `error` (no text received, or the LLM gave no valid answer).

Also `GET /health`. The endpoint is `async`: the LLM calls are non-blocking, files of a request run concurrently (`WORKERS`),
and the call returns when all its files are done.

## Layout
    app/
      main.py         the FastAPI app: startup/shutdown, request ids and timing, error answers
      routes.py       the endpoints (thin): /rl-classification, /health, /ready
      schemas.py      request and response models (what the Swagger docs show)
      service.py      one request end to end: checks, the run, the count check, the answer
      classifier.py   the pipeline: one file (retry, guard, status, cost) and one run of many files
      llm.py          the LLM: pooled async client, global limiter, prompt, retries, error handling
      pricing.py      model price, USD-INR rate, token usage and cost
      rules.py        prompt-injection guard and the status rules
      rl.py           RL validation, id, and the expected-vs-found count check
      text.py         cleaning and chunks
      config.py       all settings (read from the environment once)
      logs.py         logging (json/text, request ids, secret masking)
      errors.py       the errors the service raises on purpose
      cli.py          command line for testing: python -m app.cli --input request.json
      pulsar_worker.py  Pulsar consumer: python -m app.pulsar_worker
    .env.example      every setting, with comments
    Dockerfile        container image
    testing/          unit tests, test sets, Postman collection, evaluation tools (see testing/README.md)
    logs/             created at runtime: classify.log and cost.log (git-ignored)

## Configuration
All settings are environment variables, documented in `.env.example`: model and API key, MIN_CONFIDENCE, chunk counts,
WORKERS, price and exchange-rate auto-fetch, guardrails (limits, prompt-injection guard, cost cap, concurrency), logging.

## Pulsar
The same classification, fed by a topic instead of HTTP: `python -m app.pulsar_worker` (needs `PULSAR_SERVICE_URL`, the topics and
`OPENAI_API_KEY`; all `PULSAR_*` settings are in `.env.example`).

- **In** (`PULSAR_INPUT_TOPIC`): one JSON message = the API request body + an optional `correlation_id`:
  `{"correlation_id": "abc-1", "rl": [{name, description, count?}], "files": [{"file_id", "text" | "chunks"}]}`.
  A message may carry one file (the parser sends them one by one) or many.
- **Out** (`PULSAR_RESULT_TOPIC`): one message per input message = the API response + `correlation_id` (also a message property
  and the message key). A message that can never be classified (bad JSON, invalid RL, too many files) gets
  `{"correlation_id": ..., "error": {"status_code": 422, "detail": "..."}}` and is acknowledged.
- **Failures:** a temporary problem (LLM provider down or rate limiting, key/model misconfigured, crash, result could not be published)
  is negatively acknowledged and delivered again after `PULSAR_NACK_DELAY_SECONDS`; after `PULSAR_MAX_REDELIVER` tries Pulsar moves it
  to the dead-letter topic. A redelivered message is classified again (and paid for again).
- **Scale:** the subscription is `Shared`: run more workers (pods) to go faster. Per worker, `PULSAR_MAX_IN_FLIGHT` messages are
  worked on at once; the LLM limits (`MAX_LLM_CONCURRENCY`, `WORKERS`) apply as in the API. SIGTERM finishes the messages in flight.
- **Try it:** start a broker (`docker run -p 6650:6650 -p 8080:8080 apachepulsar/pulsar:latest bin/pulsar standalone`), start the worker,
  then `python testing/tools/pulsar_send.py testing/postman/payloads/request_multi.json`.
- The worker is tested with a fake consumer/producer and with the real LLM, but not yet against a live broker.

## Scaling
- **Async I/O:** one process serves many requests at once; waiting for the LLM never blocks the event loop.
- **Bounded concurrency at three levels:** `WORKERS` (files per request), `MAX_LLM_CONCURRENCY` (LLM calls in flight, all requests
  of the process share it), `MAX_CONCURRENT_RUNS` (simultaneous requests, then 429). A request waiting for a slot costs nothing.
- **Stateless:** no database, no in-memory state between requests, so add capacity by adding processes or containers
  (`uvicorn app.main:app --workers N`, or replicas behind a load balancer; the `Dockerfile` does this with `WEB_CONCURRENCY`).
  The limits above are per process: total LLM concurrency = processes x `MAX_LLM_CONCURRENCY`, so size them against your
  provider's rate limit.
- **Resilience:** one pooled connection set to the provider, `Retry-After` honoured on 429, backoff on timeouts/5xx,
  `FILE_TIMEOUT_SECONDS` so one stuck file cannot hold a slot, a spend cap per run.
- **Limit:** a request is answered when all its files are done, so keep batches modest (`MAX_FILES`, default 300) or send files
  one by one. A queue/job style (e.g. Pulsar) is the next step for very large volumes.
- In containers use `LOG_FILE=none` (logs go to stdout) so processes do not share a log file.

## Logging and OpenShift (OCP)
- Every step is logged: service start/stop (config summary, no secrets), each request (start, end, status, duration), request
  rejected (limits, busy, validation), run start/progress/end, each file (start, attempt 1/2, FINAL decision, injection flag,
  timeout, error), each LLM call (tokens, cost), retries/backoff, price and FX lookups.
- Every line carries `request_id` (also the `X-Request-ID` header, in and out), `rl_id` and `file_id`, so one request or one file
  can be followed through the logs. Document text and API keys are never logged (keys are masked even by accident).
- In containers set `LOG_FORMAT=json LOG_FILE=none COST_LOG_FILE=none` (the `Dockerfile` does): logs go to stdout as one JSON
  object per line, and the cost lines (`event` = `llm_call` | `file_cost` | `run_cost`) go there too. Read them with
  `oc logs -f deployment/rl-classification` (add `| jq .`) or in the cluster's logging stack. No volume is needed.
- Probes: liveness `GET /health`, readiness `GET /ready` (503 until `OPENAI_API_KEY` is set). On SIGTERM in-flight requests finish
  (`--timeout-graceful-shutdown 60`).
- Config on OCP: `OPENAI_API_KEY` from a Secret, the other settings from a ConfigMap (env). The image runs as a non-root user.
  Not yet tried on a cluster (and the image is not built yet: the Docker daemon was off).
```yaml
        # container snippet
        ports: [{containerPort: 8080}]
        envFrom: [{configMapRef: {name: rl-classification}}]
        env: [{name: OPENAI_API_KEY, valueFrom: {secretKeyRef: {name: rl-classification, key: OPENAI_API_KEY}}}]
        livenessProbe:  {httpGet: {path: /health, port: 8080}}
        readinessProbe: {httpGet: {path: /ready,  port: 8080}}
```

## Guardrails and error handling
- Input limits (files per request, text kept per file, RL size): an over-limit request gets a clear 413/422.
  A file with no text becomes an `error` row with the reason; the rest of the batch still runs.
- Prompt injection: document text is data. Text that tries to instruct the model flags the file: never auto-classified.
- LLM output must be an RL name or OTHER (else one retry, then `error`); confidence is clamped to 0-1.
- Provider errors: 429 and timeouts/5xx retry with backoff; a bad key / unknown model stops the run at once (503).
- Cost: `MAX_RUN_COST_USD` stops a run at a spend cap; `MAX_CONCURRENT_RUNS` returns 429 when busy.
- Every error answer is JSON with a `request_id` (also in the `X-Request-ID` header): 422 lists where/why (never the submitted
  text), 413/429/503 carry the reason, an unexpected 500 says `ref <request_id>` and the traceback is in the log under that id.

## Cost
Token and cost per file and per run (USD and INR) are in every response and in `logs/cost.log`. The model's price and the
USD-INR rate are fetched automatically (override in `.env`).
