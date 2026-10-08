"""Logging. Every line carries request_id / rl_id / file_id (context variables); keys are masked; JSON for containers.

Containers (OpenShift/OCP) collect stdout: set LOG_FORMAT=json LOG_FILE=none COST_LOG_FILE=none there."""
import contextvars
import json
import logging
import os
import re
import sys
import time

from app.config import settings

log = logging.getLogger("rl")              # the classification pipeline
api_log = logging.getLogger("rl.api")      # requests and answers
cost_log = logging.getLogger("rl.cost")    # token and cost lines: file (COST_LOG_FILE) and/or stdout

request_id_var = contextvars.ContextVar("request_id", default="-")    # set per HTTP request
rl_id_var = contextvars.ContextVar("rl_id", default="-")              # set per run
file_id_var = contextvars.ContextVar("file_id", default="-")          # set per file

_SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]{8,}|api[_-]?key[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._\-]{8,})", re.I)
_STD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "request_id", "rl_id", "file_id", "color_message"}


def redact(text):
    """Defence in depth: API keys never reach a log line, even by accident."""
    return _SECRET.sub("***", text)


class ContextFilter(logging.Filter):
    def filter(self, record):
        record.request_id, record.rl_id, record.file_id = request_id_var.get(), rl_id_var.get(), file_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: easy for OpenShift logging (EFK/Loki) to index. Extra fields passed with extra={...} are included."""

    def format(self, record):
        d = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
             "level": record.levelname, "logger": record.name, "msg": redact(record.getMessage())}
        for k in ("request_id", "rl_id", "file_id"):
            if getattr(record, k, "-") != "-":
                d[k] = getattr(record, k)
        d.update({k: v for k, v in record.__dict__.items() if k not in _STD_ATTRS and not k.startswith("_")})
        if record.exc_info:
            d["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(d, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record):
        ctx = " ".join(f"{k}={getattr(record, k)}" for k in ("request_id", "rl_id", "file_id") if getattr(record, k, "-") != "-")
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(record.created))} {record.levelname:<7s} " \
               f"{('[' + ctx + '] ') if ctx else ''}{redact(record.getMessage())}"
        if record.exc_info:
            line += "\n" + redact(self.formatException(record.exc_info))
        return line


def _handler(h):
    h.setFormatter(JsonFormatter() if settings.LOG_FORMAT == "json" else TextFormatter())
    h.addFilter(ContextFilter())
    return h


def setup_logging():
    """LOG_LEVEL, LOG_FORMAT (json|text), LOG_FILE / COST_LOG_FILE (path or none). Document text and API keys are never logged."""
    for path in (settings.LOG_FILE, settings.COST_LOG_FILE):
        if path and os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
    handlers = [_handler(logging.StreamHandler(sys.stdout))]
    if settings.LOG_FILE:
        handlers.append(_handler(logging.FileHandler(settings.LOG_FILE, encoding="utf-8")))
    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(settings.LOG_LEVEL)
    for name in ("uvicorn", "uvicorn.error"):              # one stream, one format
        lg = logging.getLogger(name)
        lg.handlers, lg.propagate = [], True
    logging.getLogger("uvicorn.access").disabled = True     # the request middleware logs every request itself
    for name in ("httpx", "httpx2", "httpcore", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)
    cost_log.propagate = False
    cost_log.setLevel(logging.INFO)
    cost_log.handlers = []
    if settings.COST_LOG_FILE:
        cost_log.addHandler(_handler(logging.FileHandler(settings.COST_LOG_FILE, encoding="utf-8", delay=True)))
    if settings.COST_LOG_TO_STDOUT:
        cost_log.addHandler(_handler(logging.StreamHandler(sys.stdout)))
