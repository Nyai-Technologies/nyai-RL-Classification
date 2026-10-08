"""Pulsar worker, with a fake consumer/producer (no broker needed)."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app import llm as llm_mod
from app import pulsar_worker as pw
from app import service as service_mod
from app.config import settings
from app.errors import RequestRejected
from test_classify import FakeLLM, SlowAsyncLLM, real, reply

RL = [{"name": "MSA", "description": "Master Services Agreement umbrella contract", "count": 1},
      {"name": "SOW", "description": "Statement of Work project deliverables", "count": 1}]


class Timeout(Exception):
    """Looks like pulsar.Timeout to the worker (it checks the class name)."""


class FakeMsg:
    def __init__(self, body, props=None, mid="msg-1"):
        self._data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self._props, self._mid = props or {}, mid

    def data(self): return self._data
    def properties(self): return self._props
    def message_id(self): return self._mid


class FakeConsumer:
    def __init__(self, msgs=()):
        self.queue, self.acked, self.nacked, self.fail_receive = list(msgs), [], [], 0

    def receive(self, timeout_millis=None):
        if self.fail_receive:
            self.fail_receive -= 1
            raise RuntimeError("broker connection lost")
        if self.queue:
            return self.queue.pop(0)
        raise Timeout()

    def acknowledge(self, msg): self.acked.append(msg.message_id())
    def negative_acknowledge(self, msg): self.nacked.append(msg.message_id())


class FakeProducer:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send(self, data, properties=None, partition_key=None):
        if self.fail:
            raise RuntimeError("producer closed")
        self.sent.append({"body": json.loads(data), "properties": properties, "key": partition_key})


def worker(msgs=(), fail_publish=False, **kw):
    c, p = FakeConsumer(msgs), FakeProducer(fail=fail_publish)
    return pw.Worker(c, p, **kw), c, p


def good(corr="abc-1", **extra):
    return {"correlation_id": corr, "rl": RL, "files": [{"file_id": "f1", "text": "MASTER SERVICES AGREEMENT"}], **extra}


def handle(w, msg):
    asyncio.run(w.handle(msg))


@pytest.fixture
def fake_llm(monkeypatch):
    llm = FakeLLM([reply("MSA", 0.97)] * 20)
    monkeypatch.setattr(llm_mod, "LLMClassifier", lambda rl, client=None, usage=None: real(rl, client=llm, usage=usage))
    return llm


def test_a_good_message_gets_a_result_and_is_acknowledged(fake_llm):
    w, c, p = worker()
    handle(w, FakeMsg(good(), mid="m1"))
    assert c.acked == ["m1"] and c.nacked == []
    out = p.sent[0]
    assert out["body"]["correlation_id"] == "abc-1" and out["properties"] == {"correlation_id": "abc-1"} and out["key"] == "abc-1"
    assert out["body"]["results"][0]["doc_type"] == "MSA" and out["body"]["results"][0]["status"] == "classified"
    assert out["body"]["counts"][0]["status"] == "OK" and out["body"]["by_type"]["MSA"] == 1 and "usage" in out["body"]


def test_correlation_id_falls_back_to_the_message_property_then_the_message_id(fake_llm):
    w, c, p = worker()
    body = good(); body.pop("correlation_id")
    handle(w, FakeMsg(body, props={"correlation_id": "from-prop"}, mid="m1"))
    handle(w, FakeMsg(body, mid="m2"))
    assert [s["body"]["correlation_id"] for s in p.sent] == ["from-prop", "m2"]


def test_a_message_that_is_not_json_gets_an_error_result_and_is_acknowledged():
    w, c, p = worker()
    handle(w, FakeMsg(b"this is not json", mid="bad"))
    assert c.acked == ["bad"] and c.nacked == []
    assert p.sent[0]["body"]["error"]["status_code"] == 422 and p.sent[0]["body"]["correlation_id"] == "bad"


def test_a_message_with_the_wrong_shape_gets_an_error_result():
    w, c, p = worker()
    handle(w, FakeMsg({"correlation_id": "x1", "rl": RL, "files": [{"text": "no file id"}]}, mid="m1"))
    assert c.acked == ["m1"] and p.sent[0]["body"]["error"]["status_code"] == 422 and p.sent[0]["body"]["correlation_id"] == "x1"


def test_permanent_rejections_are_answered_not_retried(monkeypatch):
    monkeypatch.setattr(settings, "MAX_FILES", 1)
    w, c, p = worker()
    body = good(); body["files"] = [{"file_id": "a", "text": "t"}, {"file_id": "b", "text": "t"}]
    handle(w, FakeMsg(body, mid="m1"))
    assert c.acked == ["m1"] and c.nacked == [] and p.sent[0]["body"]["error"]["status_code"] == 413
    one_type = {"correlation_id": "r", "rl": RL[:1], "files": [{"file_id": "a", "text": "t"}]}
    handle(w, FakeMsg(one_type, mid="m2"))
    assert c.acked == ["m1", "m2"] and p.sent[1]["body"]["error"]["status_code"] == 422


@pytest.mark.parametrize("code", [429, 502, 503])
def test_temporary_problems_are_negatively_acknowledged_so_pulsar_redelivers(monkeypatch, code):
    async def refuse(*a, **k):
        raise RequestRejected(code, "try later")
    monkeypatch.setattr(service_mod, "classify_request", refuse)
    w, c, p = worker()
    handle(w, FakeMsg(good(), mid="m1"))
    assert c.nacked == ["m1"] and c.acked == [] and p.sent == []


def test_an_unexpected_crash_is_retried_not_lost(monkeypatch):
    async def boom(*a, **k):
        raise ZeroDivisionError("boom")
    monkeypatch.setattr(service_mod, "classify_request", boom)
    w, c, p = worker()
    handle(w, FakeMsg(good(), mid="m1"))
    assert c.nacked == ["m1"] and c.acked == [] and p.sent == []


def test_if_the_result_cannot_be_published_the_message_is_retried(fake_llm):
    w, c, p = worker(fail_publish=True)
    handle(w, FakeMsg(good(), mid="m1"))
    assert c.nacked == ["m1"] and c.acked == []


def test_the_loop_processes_everything_and_bounds_the_work_in_flight(monkeypatch):
    llm = SlowAsyncLLM(delay=0.05)
    monkeypatch.setattr(llm_mod, "LLMClassifier", lambda rl, client=None, usage=None: real(rl, client=llm, usage=usage))
    msgs = [FakeMsg(good(f"c{i}"), mid=f"m{i}") for i in range(8)]
    w, c, p = worker(msgs, max_in_flight=2)

    async def go():
        stop = asyncio.Event()
        task = asyncio.ensure_future(w.run(stop))
        for _ in range(200):
            await asyncio.sleep(0.05)
            if len(c.acked) == 8:
                break
        stop.set()
        await task
    asyncio.run(go())
    assert sorted(c.acked) == sorted(f"m{i}" for i in range(8)) and c.nacked == []
    assert sorted(s["body"]["correlation_id"] for s in p.sent) == sorted(f"c{i}" for i in range(8))
    assert llm.max_inflight <= 2                      # never more than max_in_flight messages being worked on


def test_the_loop_survives_a_broker_hiccup(fake_llm):
    w, c, p = worker([FakeMsg(good(), mid="m1")])
    c.fail_receive = 1

    async def go():
        stop = asyncio.Event()
        task = asyncio.ensure_future(w.run(stop))
        for _ in range(100):
            await asyncio.sleep(0.1)
            if c.acked:
                break
        stop.set()
        await task
    asyncio.run(go())
    assert c.acked == ["m1"]


def test_connect_uses_a_shared_subscription_a_dead_letter_topic_and_the_token(monkeypatch):
    import pulsar
    seen = {}

    class FakeClient:
        def __init__(self, url, **kw): seen["url"], seen["client_kw"] = url, kw
        def subscribe(self, topic, sub, **kw): seen["sub"] = (topic, sub, kw); return "consumer"
        def create_producer(self, topic): seen["producer"] = topic; return "producer"
    monkeypatch.setattr(pulsar, "Client", FakeClient)
    monkeypatch.setattr(settings, "PULSAR_AUTH_TOKEN", "tok")
    monkeypatch.setattr(settings, "PULSAR_SERVICE_URL", "pulsar+ssl://broker:6651")
    _, consumer, producer = pw.connect()
    assert (consumer, producer) == ("consumer", "producer") and seen["url"] == "pulsar+ssl://broker:6651"
    assert "authentication" in seen["client_kw"]
    topic, sub, kw = seen["sub"]
    assert topic == settings.PULSAR_INPUT_TOPIC and sub == settings.PULSAR_SUBSCRIPTION
    assert kw["consumer_type"] == pulsar.ConsumerType.Shared and kw["initial_position"] == pulsar.InitialPosition.Earliest
    assert kw["negative_ack_redelivery_delay_ms"] == int(settings.PULSAR_NACK_DELAY_SECONDS * 1000)
    assert seen["producer"] == settings.PULSAR_RESULT_TOPIC
