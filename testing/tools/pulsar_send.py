"""Send one request to the Pulsar input topic and print the result that comes back. Needs a running broker.

  python testing/tools/pulsar_send.py testing/postman/payloads/request_multi.json [--wait 90]

The file is the same JSON as the API body. A correlation_id is added; the result message with that id is printed.
Start the worker in another terminal first:  python -m app.pulsar_worker
A throw-away broker for local testing:  docker run -p 6650:6650 -p 8080:8080 apachepulsar/pulsar:latest bin/pulsar standalone
"""
import argparse
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import pulsar  # noqa: E402

from app.config import settings  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("request_json")
ap.add_argument("--wait", type=int, default=90, help="seconds to wait for the result")
a = ap.parse_args()

body = json.load(open(a.request_json, encoding="utf-8"))
corr = body.setdefault("correlation_id", "test-" + uuid.uuid4().hex[:8])
kw = {"authentication": pulsar.AuthenticationToken(settings.PULSAR_AUTH_TOKEN)} if settings.PULSAR_AUTH_TOKEN else {}
if settings.PULSAR_TLS_TRUST_CERTS:
    kw["tls_trust_certs_file_path"] = settings.PULSAR_TLS_TRUST_CERTS
client = pulsar.Client(settings.PULSAR_SERVICE_URL, **kw)
results = client.subscribe(settings.PULSAR_RESULT_TOPIC, "pulsar-send-" + corr, initial_position=pulsar.InitialPosition.Latest)
producer = client.create_producer(settings.PULSAR_INPUT_TOPIC)
producer.send(json.dumps(body).encode("utf-8"), properties={"correlation_id": corr})
print(f"sent correlation_id={corr} to {settings.PULSAR_INPUT_TOPIC}; waiting for the result on {settings.PULSAR_RESULT_TOPIC} ...")
deadline = time.time() + a.wait
while time.time() < deadline:
    try:
        msg = results.receive(timeout_millis=1000)
    except Exception:
        continue
    results.acknowledge(msg)
    out = json.loads(msg.data())
    if out.get("correlation_id") == corr:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        break
else:
    print("no result within", a.wait, "seconds (is the worker running? check its logs and the dead-letter topic)")
client.close()
