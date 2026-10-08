# RL Classification

Classifies each file into one of the user's RL document types (or `OTHER`) with an LLM.
Input: file ids + the parsed text (or chunks) from the parser service, and the RL (name, description, expected count).
Output: type, status, confidence and reason per file; a count check per RL type (expected vs found); token and cost totals.
Parsing, chunking, storage and UI are not part of this service: it is stateless.

## Setup
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    cp .env.example .env        # set OPENAI_API_KEY and OPENAI_MODEL; everything else has a default

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
in the RL) | `low_confidence` (below MIN_CONFIDENCE: needs review) | `no_match` (an unsure
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
      rules.py        the status rule: when is a label safe to use automatically
      rl.py           RL validation, id, and the expected-vs-found count check
      text.py         cleaning and chunks
      config.py       all settings (read from the environment once)
      logs.py         logging (json/text, request ids, secret masking)
      errors.py       the errors the service raises on purpose
      cli.py          command line for testing: python -m app.cli --input request.json
      pulsar_worker.py  Pulsar consumer: python -m app.pulsar_worker
    .env.example      the two required settings (and the Pulsar topics)
    Dockerfile        container image
    logs/             created at runtime: classify.log and cost.log (git-ignored)

## Configuration
All settings are environment variables with defaults; only `OPENAI_API_KEY` and `OPENAI_MODEL` are required (the Pulsar topics too,
if you run the worker). Every setting, with its default and a comment, is listed in `app/config.py`.

## Pulsar
The same classification, fed by a topic instead of HTTP: `python -m app.pulsar_worker` (needs `PULSAR_SERVICE_URL`, `PULSAR_INPUT_TOPIC`,
`PULSAR_RESULT_TOPIC`, `PULSAR_SUBSCRIPTION` and `OPENAI_API_KEY`; no topic names are assumed, the worker stops with a clear message if one is missing).

- **In** (`PULSAR_INPUT_TOPIC`): one JSON message = the API request body + an optional `correlation_id`:
  `{"correlation_id": "abc-1", "rl": [{name, description, count?}], "files": [{"file_id", "text" | "chunks"}]}`.
  A message may carry one file (the parser sends them one by one) or many.
- **Out** (`PULSAR_RESULT_TOPIC`): one message per input message = the API response + `correlation_id` (also a message property
  and the message key). A message that can never be classified (bad JSON, invalid RL, too many files) gets
  `{"correlation_id": ..., "error": {"status_code": 422, "detail": "..."}}` and is acknowledged.
- **Failures:** every message is answered once and acknowledged. If it cannot be classified (bad JSON, invalid RL, LLM key problem,
  a crash) the result topic gets `{"correlation_id", "error": {"status_code", "detail"}}` instead. There are no retries in the worker
  (the LLM calls already retry rate limits and timeouts); the sender can simply send the message again. Only if the answer
  itself cannot be published is the message left to be delivered again.
- **Scale:** the subscription is `Shared`: run more workers (pods) to go faster. Per worker, `PULSAR_MAX_IN_FLIGHT` messages are
  worked on at once; the LLM concurrency (`MAX_LLM_CONCURRENCY`, `WORKERS`) applies as in the API. SIGTERM finishes the messages in flight.
- **Try it:** start a broker (`docker run -p 6650:6650 -p 8080:8080 apachepulsar/pulsar:latest bin/pulsar standalone`), start the worker,
  then send a request message (same JSON as the API body) to the input topic.
- The worker was tested with a fake consumer/producer and with the real LLM, but not against a live broker.

## Scaling
- **Async I/O:** one process serves many requests at once; waiting for the LLM never blocks the event loop.
- **Bounded concurrency:** `WORKERS` (files of one request at the same time) and `MAX_LLM_CONCURRENCY` (LLM calls in flight, shared by
  all requests of the process). A request waiting for a slot costs nothing.
- **Stateless:** no database, no in-memory state between requests, so add capacity by adding processes or containers
  (`uvicorn app.main:app --workers N`, or replicas behind a load balancer; the `Dockerfile` does this with `WEB_CONCURRENCY`).
  The limit above is per process: total LLM concurrency = processes x `MAX_LLM_CONCURRENCY`, so size it against your
  provider's rate limit.
- **Resilience:** one pooled connection set to the provider, `Retry-After` honoured on 429, backoff on timeouts/5xx.
- **Note:** a request is answered when all its files are done, so for very many files send them in batches or one by one. A queue/job style (e.g. Pulsar) is the next step for very large volumes.
- In containers use `LOG_FILE=none` (logs go to stdout) so processes do not share a log file.

## Logging and OpenShift (OCP)
- Every step is logged: service start/stop (config summary, no secrets), each request (start, end, status, duration), request
  rejected (validation, key/model), run start/progress/end, each file (start, attempt 1/2, FINAL decision,
  error), each LLM call (tokens, cost), retries/backoff, price and FX lookups.
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

## Error handling
- Invalid input gets a clear 422 (fewer than 2 RL types, a missing or duplicate `file_id`, a reserved RL name...). There are no size limits.
  A file with no text becomes an `error` row with the reason; the rest of the batch still runs.
  Only the first `MAX_CHARS` (6000) characters of a file go to the model, which keeps cost and context size predictable.
- The label must be an RL name or OTHER (else one retry, then `error`); confidence is clamped to 0-1.
- LLM provider errors: 429, timeouts and 5xx are retried with backoff (and `Retry-After` is honoured). A bad key / unknown model
  stops the run at once (503 with the message) instead of failing every file. Tokens spent on failed calls are still counted.
- Every error answer is JSON with a `request_id` (also in the `X-Request-ID` header); 422 lists where/why (never the submitted
  text). An unexpected 500 says `ref <request_id>` and the traceback is in the log under that id.

## Cost
Token and cost per file and per run (USD and INR) are in every response and in `logs/cost.log`. The model's price and the
USD-INR rate are fetched automatically (override in `.env`).
