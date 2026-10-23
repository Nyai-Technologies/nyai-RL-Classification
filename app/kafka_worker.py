"""Kafka consumer: the same classification as the API, fed by a topic.

  python -m app.kafka_worker

In  (KAFKA_INPUT_TOPIC):  one JSON message = the API request body, plus an optional "correlation_id" (also read from the message header or key):
  {"correlation_id": "abc-1", "rl": [{name, description, count?}], "files": [{file_id, text | chunks, file_name?}]}
Out (KAFKA_OUTPUT_TOPIC): one JSON message per input message = the API response plus "correlation_id" (also the message key and a header):
  {"correlation_id": "abc-1", "rl_id": ..., "results": [...], "counts": [...], "by_status": ..., "by_type": ..., "usage": ...}
  or, when the message could not be classified (bad JSON, invalid RL, LLM key problem...):
  {"correlation_id": "abc-1", "error": {"status_code": 422, "detail": "..."}}

Every message is answered once and then acknowledged: there are no retries here (the LLM calls already retry rate limits and timeouts),
so the sender sees an error result and can simply send the message again. A result over KAFKA_MAX_MESSAGE_BYTES is answered with a small
error result (413), because publishing it would fail the same way forever. Only if the answer cannot be published is the message not
acknowledged: it is read again after KAFKA_NACK_REDELIVERY_DELAY_SECONDS. On SIGTERM the worker stops taking messages and finishes the
ones in flight.

Kafka has no per-message ack: a partition's progress is one committed offset ("everything before this is done"). Messages finish out of
order, so each partition tracks the offsets still in flight and commits only up to the oldest unfinished one (KafkaIO below). A pod that
dies mid-run has its unfinished messages re-read by whichever pod takes over the partition, possibly with a few finished ones: results
can be duplicated, never lost. Scale with more pods in the same consumer group (up to one per partition).
"""
import asyncio
import json
import queue
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app import llm, pricing, service
from app.config import settings
from app.errors import RequestRejected
from app.logs import log, request_id_var, setup_logging
from app.schemas import ClassificationRequest

_SEND_TIMEOUT_SECONDS = 30                 # the producer gives up delivering a result after this
_CLOSE_FLUSH_SECONDS = 10
_ADMIN_TIMEOUT_SECONDS = 30
_DEFAULT_FETCH_MAX_BYTES = 52428800        # librdkafka default fetch.max.bytes
_RECEIVE_OVERHEAD_BYTES = 1024 * 1024      # receive.message.max.bytes must exceed fetch.max.bytes


def _ms(t0):
    return (time.perf_counter() - t0) * 1000


# ---------------------------------------------------------------- the Kafka side

@dataclass(frozen=True)
class Message:
    data: bytes
    id: str                  # "partition:offset"
    topic: str
    key: str | None
    headers: dict
    handle: Any              # only meaningful to KafkaIO


@dataclass
class _Partition:
    """Progress of one assigned partition. Only touched on the consumer thread."""
    in_flight: dict = field(default_factory=dict)    # offset -> generation it was read in (a nack of an older copy is stale)
    next_offset: int | None = None                   # one past the highest offset read
    committed: int | None = None
    generation: int = 0

    def commit_position(self):
        return min(self.in_flight) if self.in_flight else self.next_offset


@dataclass(frozen=True)
class _Handle:
    topic: str
    partition_id: int
    offset: int
    generation: int
    partition: _Partition                            # identity check: ignore completions from before a rebalance


def client_security_config():
    """librdkafka security settings shared by the producer, consumer and admin client. Empty for PLAINTEXT."""
    protocol = (settings.KAFKA_SECURITY_PROTOCOL or "PLAINTEXT").strip().upper()
    if protocol == "PLAINTEXT":
        return {}
    config = {"security.protocol": protocol}
    if protocol in ("SSL", "SASL_SSL") and settings.KAFKA_SSL_CAFILE:
        config["ssl.ca.location"] = settings.KAFKA_SSL_CAFILE
    if protocol.startswith("SASL"):
        config["sasl.mechanism"] = (settings.KAFKA_SASL_MECHANISM or "PLAIN").strip().upper()
        config["sasl.username"] = settings.KAFKA_SASL_USERNAME
        config["sasl.password"] = settings.KAFKA_SASL_PASSWORD
    return config


def ensure_topics(security, admin=None):
    """Create the input and output topics if they do not exist, with a max.message.bytes that fits our size limits; raise it on an existing
    topic if it is too small (never lower it, never touch partitions). Blocking; never raises: without permission a warning says what
    the topics must look like."""
    if not settings.KAFKA_CREATE_TOPICS:
        return
    from confluent_kafka import KafkaError, KafkaException
    from confluent_kafka.admin import AdminClient, AlterConfigOpType, ConfigEntry, ConfigResource, NewTopic, ResourceType
    room = settings.KAFKA_ENVELOPE_HEADROOM_BYTES
    required = {settings.KAFKA_INPUT_TOPIC: settings.KAFKA_MAX_REQUEST_BYTES + room,
                settings.KAFKA_OUTPUT_TOPIC: settings.KAFKA_MAX_MESSAGE_BYTES + room}

    def raise_limit(topic, size):
        resource = ConfigResource(ResourceType.TOPIC, topic)
        entry = admin.describe_configs([resource], request_timeout=_ADMIN_TIMEOUT_SECONDS)[resource].result(_ADMIN_TIMEOUT_SECONDS).get("max.message.bytes")
        current = int(entry.value) if entry is not None and entry.value is not None else 0
        if current >= size:
            log.info("kafka topic '%s' ready (max.message.bytes=%d)", topic, current)
            return
        change = ConfigResource(ResourceType.TOPIC, topic, incremental_configs=[
            ConfigEntry("max.message.bytes", str(size), incremental_operation=AlterConfigOpType.SET)])
        admin.incremental_alter_configs([change], request_timeout=_ADMIN_TIMEOUT_SECONDS)[change].result(_ADMIN_TIMEOUT_SECONDS)
        log.info("kafka topic '%s' max.message.bytes raised %d -> %d", topic, current, size)

    def create(topic, size):
        parts, repl = settings.KAFKA_TOPIC_PARTITIONS, settings.KAFKA_TOPIC_REPLICATION_FACTOR
        new = NewTopic(topic, num_partitions=parts if parts > 0 else -1, replication_factor=repl if repl > 0 else -1,
                       config={"max.message.bytes": str(size)})
        try:
            admin.create_topics([new], request_timeout=_ADMIN_TIMEOUT_SECONDS)[topic].result(_ADMIN_TIMEOUT_SECONDS)
        except KafkaException as e:
            if e.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
                raise
            raise_limit(topic, size)                 # created meanwhile by another pod: check its limit instead
            return
        log.info("kafka topic '%s' created: partitions=%s replication=%s max.message.bytes=%d", topic,
                 parts if parts > 0 else "broker default", repl if repl > 0 else "broker default", size)

    try:
        admin = admin or AdminClient({"bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS, **security})
        existing = set(admin.list_topics(timeout=_ADMIN_TIMEOUT_SECONDS).topics)
        for topic, size in required.items():
            raise_limit(topic, size) if topic in existing else create(topic, size)
    except Exception as e:
        log.warning("could not verify/create the Kafka topics %s (they must exist with max.message.bytes %s): %s", list(required), required, e)


class KafkaIO:
    """The confluent_kafka consumer and producer, opened once for the worker's lifetime.

    The Consumer is used only from one thread (receive, maintain, close, and the rebalance callback); acknowledge/negative_acknowledge
    may be called from anywhere: they put the outcome on a queue that maintain() drains. The Producer is thread-safe."""

    def __init__(self):
        from confluent_kafka import Consumer, Producer
        if not settings.KAFKA_CONSUMER_GROUP:        # librdkafka aborts the whole process on an empty group.id
            raise ValueError("KAFKA_CONSUMER_GROUP is not set")
        self._partitions = {}
        self._completions = queue.SimpleQueue()      # ("ack" | "nack", _Handle)
        self._rewinds = []                           # (due monotonic time, nacked handle)
        self.security = client_security_config()
        room = settings.KAFKA_ENVELOPE_HEADROOM_BYTES
        compression = settings.KAFKA_COMPRESSION_TYPE
        self._producer = Producer({
            "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
            "acks": settings.KAFKA_ACKS,
            "enable.idempotence": settings.KAFKA_IDEMPOTENT_PRODUCER,
            "compression.type": "none" if compression in ("", "none") else compression,
            "message.max.bytes": settings.KAFKA_MAX_MESSAGE_BYTES + room,
            "message.timeout.ms": _SEND_TIMEOUT_SECONDS * 1000,
            **self.security})
        log.info("kafka producer opened for '%s' (%s)", settings.KAFKA_OUTPUT_TOPIC, settings.KAFKA_BOOTSTRAP_SERVERS)
        fetch = max(_DEFAULT_FETCH_MAX_BYTES, settings.KAFKA_MAX_REQUEST_BYTES + 2 * room)     # requests carry the whole document text
        self._consumer = Consumer({
            "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
            "group.id": settings.KAFKA_CONSUMER_GROUP,
            "enable.auto.commit": False,             # an offset is committed only after its answer is published
            "auto.offset.reset": settings.KAFKA_AUTO_OFFSET_RESET,
            "max.poll.interval.ms": settings.KAFKA_MAX_POLL_INTERVAL_MS,    # far above the default: one run can take minutes
            "fetch.max.bytes": fetch,
            "receive.message.max.bytes": fetch + _RECEIVE_OVERHEAD_BYTES,
            **self.security})

    def start(self):
        ensure_topics(self.security)
        self._consumer.subscribe([settings.KAFKA_INPUT_TOPIC], on_revoke=self._on_revoke)

    def receive(self, timeout_seconds):
        """Next message, or None if nothing arrived in time."""
        from confluent_kafka import KafkaError, KafkaException
        m = self._consumer.poll(timeout_seconds)
        if m is None:
            return None
        if m.error():
            if m.error().code() == KafkaError._PARTITION_EOF:
                return None
            raise KafkaException(m.error())
        progress = self._partitions.setdefault((m.topic(), m.partition()), _Partition())
        offset = m.offset()
        progress.in_flight[offset] = progress.generation
        progress.next_offset = max(progress.next_offset or 0, offset + 1)
        key = m.key()
        headers = {k: (v.decode("utf-8", "replace") if isinstance(v, bytes) else v) for k, v in (m.headers() or [])}
        return Message(data=m.value() or b"", id=f"{m.partition()}:{offset}", topic=m.topic(),
                       key=key.decode("utf-8", "replace") if isinstance(key, bytes) else key, headers=headers,
                       handle=_Handle(m.topic(), m.partition(), offset, progress.generation, progress))

    def acknowledge(self, message):
        self._completions.put(("ack", message.handle))

    def negative_acknowledge(self, message):
        self._completions.put(("nack", message.handle))

    def maintain(self):
        """Housekeeping on the consumer thread: apply acks/nacks, rewind nacked messages once due, commit offsets."""
        self._apply_completions()
        self._apply_due_rewinds()
        self._commit(asynchronous=True)

    def _apply_completions(self):
        while True:
            try:
                kind, h = self._completions.get_nowait()
            except queue.Empty:
                return
            progress = self._partitions.get((h.topic, h.partition_id))
            if progress is not h.partition:
                continue                             # revoked since: its new owner re-reads from the last commit
            if kind == "ack":
                progress.in_flight.pop(h.offset, None)
            else:                                    # stays in flight, so nothing at or after it is committed meanwhile
                self._rewinds.append((time.monotonic() + settings.KAFKA_NACK_REDELIVERY_DELAY_SECONDS, h))

    def _apply_due_rewinds(self):
        from confluent_kafka import TopicPartition
        now = time.monotonic()
        due = sorted((h for t, h in self._rewinds if t <= now), key=lambda h: h.offset)
        self._rewinds = [(t, h) for t, h in self._rewinds if t > now]
        for h in due:
            progress = self._partitions.get((h.topic, h.partition_id))
            if progress is not h.partition or progress.in_flight.get(h.offset) != h.generation:
                continue                             # revoked, finished by another copy, or already re-read by an earlier rewind
            progress.generation += 1
            progress.in_flight = {o: g for o, g in progress.in_flight.items() if o < h.offset}
            progress.next_offset = h.offset
            self._consumer.seek(TopicPartition(h.topic, h.partition_id, h.offset))
            log.info("kafka REWOUND %s[%d] to offset %d to redeliver a message whose answer could not be published",
                     h.topic, h.partition_id, h.offset, extra={"event": "kafka_rewound"})

    def _commit(self, asynchronous, only=None):
        from confluent_kafka import TopicPartition
        offsets = []
        for (topic, pid), progress in self._partitions.items():
            if only is not None and (topic, pid) not in only:
                continue
            pos = progress.commit_position()
            if pos is not None and pos != progress.committed:
                offsets.append(TopicPartition(topic, pid, pos))
                progress.committed = pos
        if offsets:
            self._consumer.commit(offsets=offsets, asynchronous=asynchronous)

    def _on_revoke(self, consumer, partitions):
        """Rebalance: commit what is finished on the partitions handed to another pod, then forget them."""
        from confluent_kafka import KafkaException
        self._apply_completions()
        revoked = {(tp.topic, tp.partition) for tp in partitions}
        try:
            self._commit(asynchronous=False, only=revoked)
        except KafkaException:
            log.exception("kafka commit on partition revoke failed: the new owner may re-read some messages")
        for tp in revoked:
            self._partitions.pop(tp, None)
        log.info("kafka partitions revoked: %s", sorted(revoked))

    def send(self, payload, key):
        """Publish one result, blocking until the broker accepts it. Returns "partition:offset"."""
        from confluent_kafka import KafkaException
        delivered, outcome = threading.Event(), {}

        def on_delivery(err, msg):
            outcome["err"], outcome["msg"] = err, msg
            delivered.set()

        self._producer.produce(settings.KAFKA_OUTPUT_TOPIC, value=payload, key=key, on_delivery=on_delivery,
                               headers=[("correlation_id", key.encode("utf-8"))] if key else None)
        deadline = time.monotonic() + _SEND_TIMEOUT_SECONDS + 5
        while not delivered.is_set():
            if time.monotonic() > deadline:
                raise TimeoutError(f"no delivery report from Kafka for '{settings.KAFKA_OUTPUT_TOPIC}' within {_SEND_TIMEOUT_SECONDS}s")
            self._producer.poll(0.1)                 # serves delivery callbacks for any thread's messages
        if outcome["err"] is not None:
            raise KafkaException(outcome["err"])
        return f"{outcome['msg'].partition()}:{outcome['msg'].offset()}"

    def close(self):
        from confluent_kafka import KafkaException
        try:
            self._apply_completions()
            self._commit(asynchronous=False)
        except KafkaException:
            log.exception("final Kafka offset commit failed: some messages may be re-read")
        self._consumer.close()
        self._producer.flush(_CLOSE_FLUSH_SECONDS)


# ---------------------------------------------------------------- the worker

class Worker:
    def __init__(self, io, max_in_flight=None):
        self.io = io
        self.max_in_flight = max_in_flight or settings.KAFKA_MAX_CONCURRENT_JOBS
        self.in_flight = 0
        self.stats = {"received": 0, "results": 0, "error_results": 0, "nacked": 0}
        self._consumer_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kafka-consumer")   # the Consumer lives on one thread

    def stats_line(self):
        s = self.stats
        return (f"received={s['received']} results={s['results']} error_results={s['error_results']} "
                f"nacked={s['nacked']} in_flight={self.in_flight}")

    def log_stats(self, why):
        log.info("kafka worker %s: %s", why, self.stats_line(), extra={"event": "kafka_stats", **self.stats, "in_flight": self.in_flight})

    def _on_consumer_thread(self, fn, *args):
        return asyncio.get_running_loop().run_in_executor(self._consumer_thread, fn, *args)

    async def _publish(self, payload, corr):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(data) > settings.KAFKA_MAX_MESSAGE_BYTES:         # publishing it would fail the same way forever
            log.warning("kafka result of %d bytes is over KAFKA_MAX_MESSAGE_BYTES=%d: publishing an error result instead",
                        len(data), settings.KAFKA_MAX_MESSAGE_BYTES)
            data = json.dumps({"correlation_id": corr, "error": {"status_code": 413, "detail":
                               f"result too large ({len(data)} bytes, limit {settings.KAFKA_MAX_MESSAGE_BYTES}); send fewer files per message"}},
                              ensure_ascii=False).encode("utf-8")
        t0 = time.perf_counter()
        await asyncio.to_thread(self.io.send, data, corr)
        log.info("kafka answer published: correlation_id=%s bytes=%d in %.0f ms", corr, len(data), _ms(t0),
                 extra={"event": "kafka_published", "bytes": len(data)})

    async def handle(self, msg):
        """Classify one message, publish the result (or an error), acknowledge. Never raises."""
        t0 = time.perf_counter()
        mid = msg.id
        corr = msg.headers.get("correlation_id") or msg.key or mid
        raw = msg.data
        try:
            payload, bad_json = json.loads(raw), None
        except ValueError as e:
            payload, bad_json = None, e
        if isinstance(payload, dict) and payload.get("correlation_id"):
            corr = str(payload["correlation_id"])
        request_id_var.set(corr)                     # every log line of this message carries its correlation_id
        self.stats["received"] += 1
        log.info("kafka message received: id=%s topic=%s bytes=%d", mid, msg.topic, len(raw),
                 extra={"event": "kafka_received", "kafka_msg_id": mid, "bytes": len(raw)})
        try:
            if bad_json:
                raise bad_json
            req = ClassificationRequest.model_validate(payload)
        except (ValueError, ValidationError, AttributeError, TypeError) as e:
            detail = str(e).splitlines()[0][:300] if isinstance(e, ValueError) else "invalid message"
            log.warning("kafka message %s rejected (invalid): %s", mid, detail)
            await self._answer(msg, corr, {"error": {"status_code": 422, "detail": f"invalid message: {detail}"}}, "invalid", t0)
            return
        log.info("kafka message %s parsed: correlation_id=%s files=%d rl_types=%d", mid, corr, len(req.files), len(req.rl),
                 extra={"event": "kafka_parsed", "kafka_msg_id": mid, "files": len(req.files), "rl_types": len(req.rl)})
        try:
            result = await service.classify_request([i.model_dump() for i in req.rl], [f.model_dump() for f in req.files], req.rl_id)
        except RequestRejected as e:
            log.warning("kafka message %s rejected (%d): %s", mid, e.status_code, e.detail)
            await self._answer(msg, corr, {"error": {"status_code": e.status_code, "detail": e.detail}}, f"rejected_{e.status_code}", t0)
            return
        except Exception:
            log.exception("kafka message %s: unexpected failure", mid)
            await self._answer(msg, corr, {"error": {"status_code": 500, "detail": "unexpected error while classifying"}}, "crashed", t0)
            return
        log.info("kafka message %s classified: by_status=%s by_type=%s cost_inr=%s", mid, result["by_status"], result["by_type"],
                 result["usage"].get("cost_inr"))
        await self._answer(msg, corr, result, "result", t0)

    async def _answer(self, msg, corr, payload, outcome, t0):
        """Publish the answer, then acknowledge. If the answer cannot be published the message is not acknowledged: it is read again later."""
        try:
            await self._publish({"correlation_id": corr, **payload}, corr)
        except Exception:
            log.exception("kafka message %s: could not publish the answer; it will be read again in %.0f s", msg.id,
                          settings.KAFKA_NACK_REDELIVERY_DELAY_SECONDS)
            self.stats["nacked"] += 1
            self.io.negative_acknowledge(msg)
            log.warning("kafka message %s negatively acknowledged (outcome=%s, %.0f ms)", msg.id, outcome, _ms(t0),
                        extra={"event": "kafka_nack", "kafka_msg_id": msg.id, "outcome": outcome})
            return
        self.stats["results" if outcome == "result" else "error_results"] += 1
        self.io.acknowledge(msg)
        log.info("kafka message %s acknowledged: outcome=%s in %.0f ms", msg.id, outcome, _ms(t0),
                 extra={"event": "kafka_acked", "kafka_msg_id": msg.id, "outcome": outcome, "duration_ms": round(_ms(t0))})

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

        interval = settings.KAFKA_STATS_INTERVAL_SECONDS
        last_stats = time.monotonic()
        poll = settings.KAFKA_POLL_TIMEOUT_MS / 1000
        log.info("kafka worker running: up to %d messages in flight, stats every %ss", self.max_in_flight, interval or "never")
        while not stop.is_set():
            await slots.acquire()                       # take a message only when there is room to work on it
            if stop.is_set():
                slots.release()
                break
            try:
                await self._on_consumer_thread(self.io.maintain)
                msg = await self._on_consumer_thread(self.io.receive, poll)
            except Exception:
                slots.release()
                log.exception("kafka receive failed; retrying in %ss", settings.KAFKA_RECEIVE_RETRY_SECONDS)
                await asyncio.sleep(settings.KAFKA_RECEIVE_RETRY_SECONDS)
                continue
            if msg is None:
                slots.release()
                if interval and time.monotonic() - last_stats >= interval:      # idle: say that we are alive
                    self.log_stats("alive")
                    last_stats = time.monotonic()
                continue
            t = asyncio.ensure_future(one(msg))
            tasks.add(t)
            t.add_done_callback(tasks.discard)
        log.info("kafka worker stopping: waiting for %d message(s) in flight", len(tasks))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.log_stats("stopped")


async def main():
    setup_logging()
    if not settings.LLM_API_KEY:
        raise SystemExit("OPENAI_API_KEY is not set")
    missing = [n for n in ("KAFKA_INPUT_TOPIC", "KAFKA_OUTPUT_TOPIC", "KAFKA_CONSUMER_GROUP") if not getattr(settings, n)]
    if missing:                                      # no topic names are assumed: they come from your Kafka setup
        log.error("the Kafka worker needs these env variables: %s", ", ".join(missing))
        raise SystemExit("set these env variables first: " + ", ".join(missing))
    log.info("kafka worker starting: %s in=%s out=%s group=%s", settings.KAFKA_BOOTSTRAP_SERVERS, settings.KAFKA_INPUT_TOPIC,
             settings.KAFKA_OUTPUT_TOPIC, settings.KAFKA_CONSUMER_GROUP)
    try:
        io = KafkaIO()
        await asyncio.to_thread(io.start)
    except Exception as e:                           # wrong address, bad credentials...
        log.error("could not start the Kafka worker (%s): %s", settings.KAFKA_BOOTSTRAP_SERVERS, e)
        raise SystemExit(f"could not start the Kafka worker ({settings.KAFKA_BOOTSTRAP_SERVERS}): {e}")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    def on_signal(sig):
        log.info("%s received: not taking new messages, finishing the ones in flight", sig.name)
        stop.set()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, on_signal, sig)
    loop.run_in_executor(None, pricing.warm_caches)
    worker = Worker(io)
    try:
        await worker.run(stop)
    finally:
        await llm.close_shared_clients()
        await worker._on_consumer_thread(io.close)           # same thread as every other consumer call
        log.info("kafka connection closed")


if __name__ == "__main__":
    asyncio.run(main())
