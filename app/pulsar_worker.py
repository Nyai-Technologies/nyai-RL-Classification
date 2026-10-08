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

from pydantic import ValidationError

from app import llm, pricing, service
from app.config import settings
from app.errors import RequestRejected
from app.logs import log, request_id_var, setup_logging
from app.schemas import ClassificationRequest


def _is_timeout(exc):
    return type(exc).__name__ == "Timeout"          # pulsar.Timeout (looked up by name so tests need no broker)


class Worker:
    def __init__(self, consumer, producer, max_in_flight=None):
        self.consumer, self.producer = consumer, producer
        self.max_in_flight = max_in_flight or settings.PULSAR_MAX_IN_FLIGHT

    async def _publish(self, payload, corr):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        await asyncio.to_thread(self.producer.send, data, properties={"correlation_id": corr}, partition_key=corr)

    async def handle(self, msg):
        """Classify one message, publish the result (or an error), acknowledge. Never raises."""
        mid = str(msg.message_id())
        corr = (msg.properties() or {}).get("correlation_id") or mid
        try:
            payload = json.loads(msg.data())
            corr = payload.get("correlation_id") or corr
            req = ClassificationRequest.model_validate(payload)
        except (ValueError, ValidationError, AttributeError, TypeError) as e:
            request_id_var.set(corr)
            detail = str(e).splitlines()[0][:300] if isinstance(e, ValueError) else "invalid message"
            log.warning("pulsar message %s rejected (invalid): %s", mid, detail)
            await self._answer(msg, corr, {"error": {"status_code": 422, "detail": f"invalid message: {detail}"}})
            return
        request_id_var.set(corr)
        log.info("pulsar message %s: correlation_id=%s files=%d rl_types=%d", mid, corr, len(req.files), len(req.rl))
        try:
            result = await service.classify_request([i.model_dump() for i in req.rl], [f.model_dump() for f in req.files], req.rl_id)
        except RequestRejected as e:
            log.warning("pulsar message %s rejected (%d): %s", mid, e.status_code, e.detail)
            await self._answer(msg, corr, {"error": {"status_code": e.status_code, "detail": e.detail}})
            return
        except Exception:
            log.exception("pulsar message %s: unexpected failure", mid)
            await self._answer(msg, corr, {"error": {"status_code": 500, "detail": "unexpected error while classifying"}})
            return
        await self._answer(msg, corr, result)
        log.info("pulsar message %s done: by_status=%s", mid, result["by_status"])

    async def _answer(self, msg, corr, payload):
        """Publish the answer, then acknowledge. If the answer cannot be published the message is not acknowledged."""
        try:
            await self._publish({"correlation_id": corr, **payload}, corr)
        except Exception:
            log.exception("pulsar message %s: could not publish the answer; leaving it to be delivered again", msg.message_id())
            self.consumer.negative_acknowledge(msg)
            return
        self.consumer.acknowledge(msg)

    async def run(self, stop: asyncio.Event):
        slots = asyncio.Semaphore(self.max_in_flight)
        tasks = set()

        async def one(msg):
            try:
                await self.handle(msg)
            finally:
                slots.release()

        log.info("pulsar worker running: up to %d messages in flight", self.max_in_flight)
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
                continue
            t = asyncio.ensure_future(one(msg))
            tasks.add(t)
            t.add_done_callback(tasks.discard)
        log.info("pulsar worker stopping: waiting for %d message(s) in flight", len(tasks))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        log.info("pulsar worker stopped")


def connect():
    """Real connection: (client, consumer, producer) from the PULSAR_* settings."""
    import pulsar
    kw = {"operation_timeout_seconds": 30}
    if settings.PULSAR_AUTH_TOKEN:
        kw["authentication"] = pulsar.AuthenticationToken(settings.PULSAR_AUTH_TOKEN)
    client = pulsar.Client(settings.PULSAR_SERVICE_URL, **kw)
    consumer = client.subscribe(
        settings.PULSAR_INPUT_TOPIC, settings.PULSAR_SUBSCRIPTION,
        consumer_type=pulsar.ConsumerType.Shared,
        initial_position=pulsar.InitialPosition.Earliest)       # a new subscription also gets messages sent before it existed
    producer = client.create_producer(settings.PULSAR_RESULT_TOPIC)
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
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    loop.run_in_executor(None, pricing.warm_caches)
    try:
        await Worker(consumer, producer).run(stop)
    finally:
        await llm.close_shared_clients()
        for c in (consumer, producer, client):
            try:
                c.close()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
