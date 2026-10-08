"""All settings, read once from the environment (and a local .env). Code reads `settings.X` at use time, never at import time,
so tests can override a value with monkeypatch.setattr(settings, "X", ...)."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _str(name, default=""):
    return os.getenv(name, default)


def _int(name, default):
    return int(os.getenv(name, str(default)) or default)


def _float(name, default):
    return float(os.getenv(name, str(default)) or default)


def _flag(name, default):
    return os.getenv(name, "1" if default else "0").strip().lower() not in ("0", "false", "no", "off")


def _path_or_none(name, default):
    v = os.getenv(name, default).strip()
    return None if v.lower() in ("", "none", "off") else v


def _float_or_none(name):
    v = os.getenv(name)
    return float(v) if v not in (None, "") else None


@dataclass
class Settings:
    # ---- LLM (OpenAI) ----
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "luna"
    LLM_TEMPERATURE: float | None = 0.0          # None = do not send (model default); auto-dropped if the model rejects it
    LLM_TIMEOUT: float = 60.0                    # seconds per LLM call
    LLM_JSON_MODE: bool = True
    MAX_BACKOFF_TRIES: int = 6                   # tries on 429 before giving up
    TRANSIENT_RETRIES: int = 3                   # retries on timeouts / connection / 5xx errors
    SYSTEM_PROMPT_FILE: str | None = None        # optional override of the built-in prompt

    # ---- price per 1M tokens (USD): both set = pinned (0/0 = no cost reporting); unset = auto-fetch ----
    LLM_PRICE_IN_PER_M: float | None = None
    LLM_PRICE_OUT_PER_M: float | None = None
    PRICE_URL_LITELLM: str = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
    PRICE_URL_OPENROUTER: str = "https://openrouter.ai/api/v1/models"
    PRICE_MODEL_ALIASES: dict = field(default_factory=lambda: {"luna": "gpt-luna-latest"})

    # ---- rupees per USD: None = fetch live once; a number pins it ----
    USD_INR: float | None = None
    USD_INR_FALLBACK: float = 88.0
    FX_URL: str = "https://open.er-api.com/v6/latest/USD"

    # ---- classification ----
    MIN_CONFIDENCE: float = 0.80                 # below this a label goes to review
    OTHER_AUTO_CONFIDENCE: float = 0.95          # a confident OTHER is final, no review needed (above 1 = always review)
    FIRST_CHUNKS: int = 3                        # chunks sent on the first try
    RETRY_CHUNKS: int = 6                        # chunks sent on the retry for unclear files
    MAX_CHARS: int = 6000                        # cap on text sent to the LLM
    CHUNK_CHARS: int = 1200                      # chunk size when only raw text is available

    # ---- concurrency ----
    WORKERS: int = 4                             # files of ONE request classified at the same time
    MAX_LLM_CONCURRENCY: int = 16                # LLM calls in flight across ALL requests of this process


    # ---- Pulsar (python -m app.pulsar_worker) ----
    PULSAR_SERVICE_URL: str = "pulsar://localhost:6650"   # pulsar+ssl://... for TLS
    PULSAR_AUTH_TOKEN: str = ""                           # JWT, if the cluster needs one
    PULSAR_INPUT_TOPIC: str = ""                          # required for the worker: your topic names, nothing is assumed
    PULSAR_RESULT_TOPIC: str = ""                         # required for the worker
    PULSAR_SUBSCRIPTION: str = ""                         # required for the worker
    PULSAR_MAX_IN_FLIGHT: int = 4                         # messages classified at the same time per worker
    PULSAR_STATS_INTERVAL_SECONDS: float = 60.0           # how often the worker logs its counters while idle (0 = never)

    # ---- logging (containers/OpenShift: LOG_FORMAT=json LOG_FILE=none COST_LOG_FILE=none) ----
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "text"                     # "json" or "text"
    LOG_FILE: str | None = "logs/classify.log"
    COST_LOG_FILE: str | None = "logs/cost.log"
    COST_LOG_TO_STDOUT: bool = False             # default: on when LOG_FORMAT=json

    @classmethod
    def from_env(cls):
        temp = _str("LLM_TEMPERATURE", "0").strip().lower()
        usd_inr = _str("USD_INR", "auto").strip().lower()
        fmt = _str("LOG_FORMAT", "text").strip().lower()
        return cls(
            LLM_API_KEY=_str("LLM_API_KEY") or _str("OPENAI_API_KEY"),
            LLM_MODEL=_str("LLM_MODEL") or _str("OPENAI_MODEL") or "luna",
            LLM_TEMPERATURE=None if temp in ("", "none", "default") else float(temp),
            LLM_TIMEOUT=_float("LLM_TIMEOUT", 60),
            LLM_JSON_MODE=_flag("LLM_JSON_MODE", True),
            MAX_BACKOFF_TRIES=_int("MAX_BACKOFF_TRIES", 6),
            TRANSIENT_RETRIES=_int("TRANSIENT_RETRIES", 3),
            SYSTEM_PROMPT_FILE=_str("SYSTEM_PROMPT_FILE") or None,
            LLM_PRICE_IN_PER_M=_float_or_none("LLM_PRICE_IN_PER_M"),
            LLM_PRICE_OUT_PER_M=_float_or_none("LLM_PRICE_OUT_PER_M"),
            PRICE_URL_LITELLM=_str("PRICE_URL_LITELLM", cls.PRICE_URL_LITELLM),
            PRICE_URL_OPENROUTER=_str("PRICE_URL_OPENROUTER", cls.PRICE_URL_OPENROUTER),
            USD_INR=None if usd_inr in ("", "auto") else float(usd_inr),
            USD_INR_FALLBACK=_float("USD_INR_FALLBACK", 88),
            FX_URL=_str("FX_URL", cls.FX_URL),
            MIN_CONFIDENCE=_float("MIN_CONFIDENCE", 0.80),
            OTHER_AUTO_CONFIDENCE=_float("OTHER_AUTO_CONFIDENCE", 0.95),
            FIRST_CHUNKS=_int("FIRST_CHUNKS", 3),
            RETRY_CHUNKS=_int("RETRY_CHUNKS", 6),
            MAX_CHARS=_int("MAX_CHARS", 6000),
            CHUNK_CHARS=_int("CHUNK_CHARS", 1200),
            WORKERS=_int("WORKERS", 4),
            MAX_LLM_CONCURRENCY=_int("MAX_LLM_CONCURRENCY", 16),
            PULSAR_SERVICE_URL=_str("PULSAR_SERVICE_URL", cls.PULSAR_SERVICE_URL),
            PULSAR_AUTH_TOKEN=_str("PULSAR_AUTH_TOKEN"),
            PULSAR_INPUT_TOPIC=_str("PULSAR_INPUT_TOPIC").strip(),
            PULSAR_RESULT_TOPIC=_str("PULSAR_RESULT_TOPIC").strip(),
            PULSAR_SUBSCRIPTION=_str("PULSAR_SUBSCRIPTION").strip(),
            PULSAR_MAX_IN_FLIGHT=_int("PULSAR_MAX_IN_FLIGHT", 4),
            PULSAR_STATS_INTERVAL_SECONDS=_float("PULSAR_STATS_INTERVAL_SECONDS", 60),
            LOG_LEVEL=_str("LOG_LEVEL", "INFO").upper(),
            LOG_FORMAT=fmt,
            LOG_FILE=_path_or_none("LOG_FILE", "logs/classify.log"),
            COST_LOG_FILE=_path_or_none("COST_LOG_FILE", "logs/cost.log"),
            COST_LOG_TO_STDOUT=_flag("COST_LOG_TO_STDOUT", fmt == "json"),
        )


settings = Settings.from_env()
