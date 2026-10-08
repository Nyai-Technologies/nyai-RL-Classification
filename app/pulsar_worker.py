"""Pulsar consumer: the same classification as the API, fed by a topic.

  python -m app.pulsar_worker

In  (PULSAR_INPUT_TOPIC):  one JSON message = the API request body, plus an optional "correlation_id":
  {"correlation_id": "abc-1", "rl": [{name, description, count?}], "files": [{file_id, text | chunks, file_name?}]}
Out (PULSAR_RESULT_TOPIC): one JSON message per input message = the API response plus "correlation_id":
  {"correlation_id": "abc-1", "rl_id": ..., "results": [...], "counts": [...], "by_status": ..., "by_type": ..., "usage": ...}
  or, when the message could not be classified (bad JSON, invalid RL, LLM key problem...):
  {"correlation_id": "abc-1", "error": {"status_code": 422, "detail": "..."}}

Every message is answered once and then acknowledged: there are no retries here (the LLM calls already retry rate limits and
timeouts), so the sender sees an error result and can simply send the message again. The subscription is Shared: run as many
workers as you need. On SIGTERM the worker stops taking messages and finishes the ones in flight.
"""
import asyncio
import json
import signal
import time

from pydantic import ValidationError

from app import llm, pricing, service
from app.config import settings
from app.errors import RequestRejected
from app.logs import log, request_id_var, setup_logging
from app.schemas import ClassificationRequest


def _is_timeout(exc):
    return type(exc).__name__ == "Timeout"          # pulsar.Timeout (looked up by name so tests need no broker)


def _ms(t0):
    return (time.perf_counter() - t0) * 1000


class Worker:
    def __init__(self, consumer, producer, max_in_flight=None):
        self.consumer, self.producer = consumer, producer
        self.max_in_flight = max_in_flight or settings.PULSAR_MAX_IN_FLIGHT
        self.in_flight = 0
        self.stats = {"received": 0, "results": 0, "error_results": 0, "nacked": 0}

    def stats_line(self):
        s = self.stats
        return (f"received={s['received']} results={s['results']} error_results={s['error_results']} "
                f"nacked={s['nacked']} in_flight={self.in_flight}")

    def log_stats(self, why):
        log.info("pulsar worker %s: %s", why, self.stats_line(), extra={"event": "pulsar_stats", **self.stats, "in_flight": self.in_flight})

    async def _publish(self, payload, corr):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        t0 = time.perf_counter()
        await asyncio.to_thread(self.producer.send, data, properties={"correlation_id": corr}, partition_key=corr)
        log.info("pulsar answer published: correlation_id=%s bytes=%d in %.0f ms", corr, len(data), _ms(t0),
                 extra={"event": "pulsar_published", "bytes": len(data)})

    async def handle(self, msg):
        """Classify one message, publish the result (or an error), acknowledge. Never raises."""
        t0 = time.perf_counter()
        mid = str(msg.message_id())
        corr = (msg.properties() or {}).get("correlation_id") or mid
        raw = msg.data()
        try:
            payload, bad_json = json.loads(raw), None
        except ValueError as e:
            payload, bad_json = None, e
        if isinstance(payload, dict) and payload.get("correlation_id"):
            corr = payload["correlation_id"]
        request_id_var.set(corr)                     # every log line of this message carries its correlation_id
        self.stats["received"] += 1
        log.info("pulsar message received: id=%s bytes=%d redelivery_count=%s", mid, len(raw),
                 getattr(msg, "redelivery_count", lambda: 0)(),
                 extra={"event": "pulsar_received", "pulsar_msg_id": mid, "bytes": len(raw)})
        try:
            if bad_json:
                raise bad_json
            req = ClassificationRequest.model_validate(payload)
        except (ValueError, ValidationError, AttributeError, TypeError) as e:
            detail = str(e).splitlines()[0][:300] if isinstance(e, ValueError) else "invalid message"
            log.warning("pulsar message %s rejected (invalid): %s", mid, detail)
            await self._answer(msg, corr, {"error": {"status_code": 422, "detail": f"invalid message: {detail}"}}, "invalid", t0)
            return
        log.info("pulsar message %s parsed: correlation_id=%s files=%d rl_types=%d", mid, corr, len(req.files), len(req.rl),
                 extra={"event": "pulsar_parsed", "pulsar_msg_id": mid, "files": len(req.files), "rl_types": len(req.rl)})
        try:
            result = await service.classify_request([i.model_dump() for i in req.rl], [f.model_dump() for f in req.files], req.rl_id)
        except RequestRejected as e:
            log.warning("pulsar message %s rejected (%d): %s", mid, e.status_code, e.detail)
            await self._answer(msg, corr, {"error": {"status_code": e.status_code, "detail": e.detail}}, f"rejected_{e.status_code}", t0)
            return
        except Exception:
            log.exception("pulsar message %s: unexpected failure", mid)
            await self._answer(msg, corr, {"error": {"status_code": 500, "detail": "unexpected error while classifying"}}, "crashed", t0)
            return
        log.info("pulsar message %s classified: by_status=%s by_type=%s cost_inr=%s", mid, result["by_status"], result["by_type"],
                 result["usage"].get("cost_inr"))
        await self._answer(msg, corr, result, "result", t0)

    async def _answer(self, msg, corr, payload, outcome, t0):
        """Publish the answer, then acknowledge. If the answer cannot be published the message is not acknowledged."""
        mid = str(msg.message_id())
        try:
            await self._publish({"correlation_id": corr, **payload}, corr)
        except Exception:
            log.exception("pulsar message %s: could not publish the answer; it will be delivered again", mid)
            self.stats["nacked"] += 1
            try:
                self.consumer.negative_acknowledge(msg)
            except Exception:
                log.exception("pulsar message %s: could not negatively acknowledge it either", mid)
            else:
                log.warning("pulsar message %s negatively acknowledged (outcome=%s, %.0f ms)", mid, outcome, _ms(t0),
                            extra={"event": "pulsar_nack", "pulsar_msg_id": mid, "outcome": outcome})
            return
        self.stats["results" if outcome == "result" else "error_results"] += 1
        self.consumer.acknowledge(msg)
        log.info("pulsar message %s acknowledged: outcome=%s in %.0f ms", mid, outcome, _ms(t0),
                 extra={"event": "pulsar_acked", "pulsar_msg_id": mid, "outcome": outcome, "duration_ms": round(_ms(t0))})

    async def run(self, stop: asyncio.Event):
        slots = asyncio.Semaphore(self.max_in_flight)
        tasks = set()

        async def one(msg):
            self.in_flight += 1
            try:
                await self.handle(msg)
            finally:
                self.in_flight -= 1
                slots.release()

        interval = settings.PULSAR_STATS_INTERVAL_SECONDS
        last_stats = time.monotonic()
        log.info("pulsar worker running: up to %d messages in flight, stats every %ss", self.max_in_flight, interval or "never")
        while not stop.is_set():
            await slots.acquire()                       # take a message only when there is room to work on it
            if stop.is_set():
                slots.release()
                break
            try:
                msg = await asyncio.to_thread(self.consumer.receive, 1000)
            except Exception as e:
                slots.release()
                if not _is_timeout(e):
                    log.exception("pulsar receive failed; retrying in 2s")
                    await asyncio.sleep(2)
                if interval and time.monotonic() - last_stats >= interval:      # idle: say that we are alive
                    self.log_stats("alive")
                    last_stats = time.monotonic()
                continue
            t = asyncio.ensure_future(one(msg))
            tasks.add(t)
            t.add_done_callback(tasks.discard)
        log.info("pulsar worker stopping: waiting for %d message(s) in flight", len(tasks))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.log_stats("stopped")


def connect():
    """Real connection: (client, consumer, producer) from the PULSAR_* settings."""
    import pulsar
    t0 = time.perf_counter()
    kw = {"operation_timeout_seconds": 30}
    if settings.PULSAR_AUTH_TOKEN:
        kw["authentication"] = pulsar.AuthenticationToken(settings.PULSAR_AUTH_TOKEN)
    log.info("pulsar connecting to %s (token: %s)", settings.PULSAR_SERVICE_URL, "yes" if settings.PULSAR_AUTH_TOKEN else "no")
    client = pulsar.Client(settings.PULSAR_SERVICE_URL, **kw)
    consumer = client.subscribe(
        settings.PULSAR_INPUT_TOPIC, settings.PULSAR_SUBSCRIPTION,
        consumer_type=pulsar.ConsumerType.Shared,
        initial_position=pulsar.InitialPosition.Earliest)       # a new subscription also gets messages sent before it existed
    log.info("pulsar subscribed: topic=%s subscription=%s (Shared, from the earliest message)", settings.PULSAR_INPUT_TOPIC,
             settings.PULSAR_SUBSCRIPTION)
    producer = client.create_producer(settings.PULSAR_RESULT_TOPIC)
    log.info("pulsar producer ready: topic=%s; connected in %.0f ms", settings.PULSAR_RESULT_TOPIC, _ms(t0),
             extra={"event": "pulsar_connected"})
    return client, consumer, producer


async def main():
    setup_logging()
    if not settings.LLM_API_KEY:
        raise SystemExit("OPENAI_API_KEY is not set")
    missing = [n for n in ("PULSAR_INPUT_TOPIC", "PULSAR_RESULT_TOPIC", "PULSAR_SUBSCRIPTION") if not getattr(settings, n)]
    if missing:                                      # no topic names are assumed: they come from your Pulsar setup
        log.error("the Pulsar worker needs these env variables: %s", ", ".join(missing))
        raise SystemExit("set these env variables first: " + ", ".join(missing))
    log.info("pulsar worker starting: %s in=%s out=%s subscription=%s", settings.PULSAR_SERVICE_URL,
             settings.PULSAR_INPUT_TOPIC, settings.PULSAR_RESULT_TOPIC, settings.PULSAR_SUBSCRIPTION)
    try:
        client, consumer, producer = connect()
    except Exception as e:                           # no broker, wrong URL, bad token...
        log.error("could not connect to Pulsar at %s: %s", settings.PULSAR_SERVICE_URL, e)
        raise SystemExit(f"could not connect to Pulsar at {settings.PULSAR_SERVICE_URL}: {e}")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    def on_signal(sig):
        log.info("%s received: not taking new messages, finishing the ones in flight", sig.name)
        stop.set()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, on_signal, sig)
    loop.run_in_executor(None, pricing.warm_caches)
    try:
        await Worker(consumer, producer).run(stop)
    finally:
        await llm.close_shared_clients()
        for c in (consumer, producer, client):
            try:
                c.close()
            except Exception:
                log.warning("pulsar: could not close %s cleanly", type(c).__name__)
        log.info("pulsar connection closed")


if __name__ == "__main__":
    asyncio.run(main())
