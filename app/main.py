"""The FastAPI application: startup/shutdown, request context (ids and timing), error answers.

  uvicorn app.main:app --port 8000
"""
import asyncio
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__, llm, pricing, routes
from app.config import settings
from app.errors import RequestRejected
from app.logs import api_log as log
from app.logs import request_id_var, setup_logging


@asynccontextmanager
async def lifespan(app):
    log.info("service starting: model=%s api_key_set=%s log_format=%s concurrency(per request/process)=%d/%d min_confidence=%.2f",
             settings.LLM_MODEL, bool(settings.LLM_API_KEY), settings.LOG_FORMAT, settings.WORKERS,
             settings.MAX_LLM_CONCURRENCY,
             settings.MIN_CONFIDENCE)
    if not settings.LLM_API_KEY:
        log.error("OPENAI_API_KEY is not set: /rl-classification will answer 503 until it is configured")
    asyncio.get_running_loop().run_in_executor(None, pricing.warm_caches)      # price + FX lookup in the background
    yield
    log.info("service stopping: closing LLM connections")
    await llm.close_shared_clients()
    log.info("service stopped")


def _request_id(request: Request):
    return getattr(request.state, "request_id", "-")


def _error(request: Request, status_code, detail):
    rid = _request_id(request)
    return JSONResponse({"detail": detail, "request_id": rid}, status_code=status_code, headers={"X-Request-ID": rid})


def create_app():
    setup_logging()
    app = FastAPI(
        title="RL Classification", version=__version__, lifespan=lifespan,
        description="Classify each file into one of the user's RL document types (or OTHER) with an LLM. "
                    "Input: file ids + parsed text (or chunks) and the RL (name, description, expected count). "
                    "Output: type, status and confidence per file, a count check per RL type, and token/cost totals.",
        openapi_tags=[{"name": "RL classification", "description": "Classify files against an RL"},
                      {"name": "ops", "description": "Probes for the platform (OpenShift/Kubernetes)"}])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        """Gives every request an id (X-Request-ID in and out), puts it on every log line, logs start and end."""
        rid = (request.headers.get("x-request-id") or uuid.uuid4().hex[:12])[:64]
        request.state.request_id = rid
        request_id_var.set(rid)
        t0 = time.perf_counter()
        log.info("request start: %s %s", request.method, request.url.path)
        try:
            response = await call_next(request)
        except Exception:
            log.error("request failed: %s %s after %.0f ms (unhandled error, see the traceback above)",
                      request.method, request.url.path, (time.perf_counter() - t0) * 1000)
            raise
        response.headers["X-Request-ID"] = rid
        log.info("request done: %s %s -> %d in %.0f ms", request.method, request.url.path, response.status_code,
                 (time.perf_counter() - t0) * 1000)
        return response

    @app.exception_handler(RequestRejected)
    async def rejected(request: Request, exc: RequestRejected):
        """A request the service refuses on purpose (limits, busy, bad key...): log the reason, answer JSON."""
        log.log(40 if exc.status_code >= 500 else 30, "request rejected (%d): %s", exc.status_code, exc.detail)
        return _error(request, exc.status_code, exc.detail)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        log.log(40 if exc.status_code >= 500 else 30, "request rejected (%d): %s", exc.status_code, exc.detail)
        return _error(request, exc.status_code, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        """Malformed body. The answer lists where and why, never the submitted values (they may be document text)."""
        errors = [{"loc": [str(x) for x in e.get("loc", [])], "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        log.warning("request rejected (422): %d validation error(s), first: %s", len(errors),
                    f"{'.'.join(errors[0]['loc'])}: {errors[0]['msg']}" if errors else "?")
        return _error(request, 422, errors)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        """Anything we did not anticipate: log the traceback under the request id and return JSON, never an HTML 500."""
        ref = _request_id(request)
        log.exception("unhandled error ref=%s on %s %s", ref, request.method, request.url.path)
        return _error(request, 500, f"Internal error (ref {ref}). The details are in the app log.")

    app.include_router(routes.router)
    return app


app = create_app()
