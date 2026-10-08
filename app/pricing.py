"""Money: the model's price, the USD-INR rate, token usage and cost per file / per run."""
import json
import threading
import urllib.request

from app.config import settings
from app.logs import log

_fx = {"rate": None}          # live USD->INR rate, fetched once per process
_price_cache = {}             # model -> (usd per 1M input tokens, usd per 1M output tokens)


def usd_inr():
    """Current USD->INR rate: pinned via USD_INR, else fetched live once and cached, else the fallback."""
    if settings.USD_INR is not None:
        return settings.USD_INR
    if _fx["rate"] is None:
        try:
            with urllib.request.urlopen(settings.FX_URL, timeout=5) as r:
                _fx["rate"] = float(json.load(r)["rates"]["INR"])
            log.info("live USD->INR rate fetched: %.4f", _fx["rate"])
        except Exception as e:
            _fx["rate"] = settings.USD_INR_FALLBACK
            log.warning("live USD->INR fetch failed (%s); using fallback %.2f", e, _fx["rate"])
    return _fx["rate"]


def _fetch_json(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.load(r)


def lookup_price(model):
    """(input, output, source) USD per 1M tokens from public price tables, or None. Never guesses a different model."""
    name = settings.PRICE_MODEL_ALIASES.get(model, model)
    try:
        table = _fetch_json(settings.PRICE_URL_LITELLM)
        for key in (name, name.split("/")[-1]):
            e = table.get(key)
            if e and e.get("input_cost_per_token") is not None and e.get("output_cost_per_token") is not None:
                return e["input_cost_per_token"] * 1e6, e["output_cost_per_token"] * 1e6, f"litellm:{key}"
    except Exception as e:
        log.warning("price lookup (litellm) failed: %s", e)
    try:
        models = {m["id"]: m for m in _fetch_json(settings.PRICE_URL_OPENROUTER)["data"]}
        for key in (name, f"openai/{name}", f"~openai/{name}"):
            if key in models:
                pr = models[key]["pricing"]
                return float(pr["prompt"]) * 1e6, float(pr["completion"]) * 1e6, f"openrouter:{key}"
    except Exception as e:
        log.warning("price lookup (openrouter) failed: %s", e)
    return None


def llm_prices():
    """(input, output) USD per 1M tokens: pinned via env, else auto-fetched once and cached, else (0, 0) = unpriced."""
    if settings.LLM_PRICE_IN_PER_M is not None or settings.LLM_PRICE_OUT_PER_M is not None:
        return settings.LLM_PRICE_IN_PER_M or 0.0, settings.LLM_PRICE_OUT_PER_M or 0.0
    model = settings.LLM_MODEL
    if model not in _price_cache:
        found = lookup_price(model)
        if found:
            log.info("auto price for %s: $%.4f in / $%.4f out per 1M tokens (%s)", model, *found)
            _price_cache[model] = found[:2]
        else:
            log.warning("no price found for model %s; cost unreported (set LLM_PRICE_IN_PER_M / LLM_PRICE_OUT_PER_M)", model)
            _price_cache[model] = (0.0, 0.0)
    return _price_cache[model]


def warm_caches():
    """Look up the model price and the USD-INR rate once (network, cached). Called at startup so no request pays for it."""
    llm_prices()
    usd_inr()


class Usage:
    """Tokens and cost of one run."""

    def __init__(self):
        self._lock = threading.Lock()
        self.calls = self.prompt_tokens = self.completion_tokens = 0

    @staticmethod
    def cost_usd(prompt_tokens, completion_tokens):
        p_in, p_out = llm_prices()
        return (prompt_tokens * p_in + completion_tokens * p_out) / 1e6

    def add(self, usage):
        """Adds one call's tokens; returns (prompt_tokens, completion_tokens, cost_usd) of that call."""
        p = getattr(usage, "prompt_tokens", 0) or 0
        c = getattr(usage, "completion_tokens", 0) or 0
        with self._lock:
            self.calls += 1
            self.prompt_tokens += p
            self.completion_tokens += c
        return p, c, self.cost_usd(p, c)

    def summary(self, files=None):
        """Totals plus cost in USD and INR. With `files`, also per-file cost and a 1000-file estimate."""
        priced = any(llm_prices())
        out = {"calls": self.calls, "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
               "cost_usd": None, "cost_inr": None, "usd_inr_rate": usd_inr()}
        if priced:
            usd = self.cost_usd(self.prompt_tokens, self.completion_tokens)
            out.update(cost_usd=round(usd, 4), cost_inr=round(usd * usd_inr(), 2))
            if files:
                per = usd / files
                out.update(per_file_usd=round(per, 5), per_file_inr=round(per * usd_inr(), 3),
                           est_1000_files_usd=round(per * 1000, 2), est_1000_files_inr=round(per * 1000 * usd_inr(), 2))
        return out


def cost_fields(tokens_in, tokens_out):
    """Per-file cost, USD and INR (None when the model has no known price)."""
    if not any(llm_prices()):
        return {"cost_usd": None, "cost_inr": None}
    usd = Usage.cost_usd(tokens_in, tokens_out)
    return {"cost_usd": round(usd, 6), "cost_inr": round(usd * usd_inr(), 4)}
