"""Rebuilds RL-Classification.postman_collection.json from payloads/request_*.json and payloads/response_*.json.

To refresh the saved responses, start the server and run (from the project root):
  for n in single multi injection invalid_rl missing_file_id; do
    curl -s -X POST localhost:8000/rl-classification -H 'Content-Type: application/json' \
      -d @testing/postman/payloads/request_$n.json -o testing/postman/payloads/response_$n.json; done
  .venv/bin/python testing/postman/build_collection.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(HERE, "payloads")


def body(n):
    return json.dumps(json.load(open(f"{P}/request_{n}.json")), indent=2, ensure_ascii=False)


def resp(n):
    return json.dumps(json.load(open(f"{P}/response_{n}.json")), indent=2, ensure_ascii=False)


def req(name, method, path, raw=None, tests=None, example=None, example_name=None, code=200, desc=""):
    r = {"method": method,
         "header": [{"key": "Content-Type", "value": "application/json"},
                    {"key": "X-Request-ID", "value": "{{$guid}}", "description": "optional: shows up on every log line of this request"}] if raw else [],
         "url": {"raw": "{{base_url}}" + path, "host": ["{{base_url}}"], "path": path.strip("/").split("/")}, "description": desc}
    if raw:
        r["body"] = {"mode": "raw", "raw": raw, "options": {"raw": {"language": "json"}}}
    item = {"name": name, "request": r, "event": [{"listen": "test", "script": {"type": "text/javascript", "exec": tests}}] if tests else []}
    if example:
        item["response"] = [{"name": example_name or name, "originalRequest": r, "status": "OK" if code == 200 else "Unprocessable Entity",
                             "code": code, "_postman_previewlanguage": "json",
                             "header": [{"key": "Content-Type", "value": "application/json"}], "body": example}]
    return item


ok = lambda extra: ['pm.test("status 200", () => pm.response.to.have.status(200));', "const j = pm.response.json();"] + extra
items = [
    {"name": "RL classification", "item": [
        req("1. One file", "POST", "/rl-classification", body("single"),
            ok(['pm.test("one result", () => pm.expect(j.results.length).to.eql(1));',
                'pm.test("classified as MSA", () => { pm.expect(j.results[0].doc_type).to.eql("MSA"); pm.expect(j.results[0].status).to.eql("classified"); });',
                'pm.test("cost is reported", () => pm.expect(j.usage.cost_inr).to.be.a("number"));']),
            resp("single"), "200 OK", desc="One parsed file + a 2-type RL. Each file needs file_id and text (or chunks). The label comes from the content only."),
        req("2. Many files + count check", "POST", "/rl-classification", body("multi"),
            ok(['pm.test("5 results", () => pm.expect(j.results.length).to.eql(5));',
                'pm.test("count check for every RL type", () => pm.expect(j.counts.map(c => c.rl)).to.have.members(["MSA","SOW","NDA","MoM"]));',
                'pm.test("the invoice is confidently OTHER (final)", () => pm.expect(j.by_type.OTHER).to.eql(1));',
                'pm.test("empty text becomes an error row, the run still succeeds", () => pm.expect(j.by_status.error).to.eql(1));']),
            resp("multi"), "200 OK", desc="Text and chunks mixed, an invoice (not in the RL -> OTHER) and a file with empty text (error row). counts[] compares your expected count with what was found; by_type counts classified files per RL type plus OTHER."),
        req("3. Prompt injection (goes to review)", "POST", "/rl-classification", body("injection"),
            ok(['pm.test("never auto-classified", () => pm.expect(j.results[0].status).to.not.eql("classified"));',
                'pm.test("flagged", () => pm.expect(j.results[0].flags).to.eql("prompt_injection_suspected"));']),
            resp("injection"), "200 OK", desc="The text tries to instruct the model. The file is flagged and is never auto-classified."),
    ]},
    {"name": "Errors (what a bad request looks like)", "item": [
        req("4. Invalid RL (only 1 type) -> 422", "POST", "/rl-classification", body("invalid_rl"),
            ['pm.test("status 422", () => pm.response.to.have.status(422));', 'pm.test("has request_id", () => pm.expect(pm.response.json().request_id).to.be.a("string"));'],
            resp("invalid_rl"), "422 invalid RL", code=422),
        req("5. Missing file_id -> 422", "POST", "/rl-classification", body("missing_file_id"),
            ['pm.test("status 422", () => pm.response.to.have.status(422));', 'pm.test("says which field", () => pm.expect(JSON.stringify(pm.response.json().detail)).to.include("file_id"));'],
            resp("missing_file_id"), "422 missing file_id", code=422),
    ]},
    {"name": "Ops", "item": [
        req("Health (liveness)", "GET", "/health", tests=['pm.test("up", () => pm.response.to.have.status(200));']),
        req("Ready (readiness: API key set?)", "GET", "/ready", tests=['pm.test("ready", () => pm.response.to.have.status(200));']),
    ]},
]
col = {"info": {"name": "RL Classification",
                "description": "Local API. Start the server (uvicorn app.main:app --port 8000), pick the 'RL Classification - local' environment, press Send. Swagger docs: {{base_url}}/docs",
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
       "item": items, "variable": [{"key": "base_url", "value": "http://localhost:8000"}]}
json.dump(col, open(f"{HERE}/RL-Classification.postman_collection.json", "w"), indent=2, ensure_ascii=False)
print("collection written:", sum(len(g["item"]) for g in items), "requests")
