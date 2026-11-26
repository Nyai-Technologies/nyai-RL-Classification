"""AWS Bedrock with nothing to configure but the key.

The key is AWS_BEARER_TOKEN_BEDROCK, or LLM_API_KEY / OPENAI_API_KEY when that holds a
Bedrock API key (ABSK...) instead of an OpenAI one. The region is
BEDROCK_REGION / AWS_REGION / AWS_DEFAULT_REGION, else the one inside a
short-term key, else us-east-1. The model is BEDROCK_MODEL_ID, else whichever
model the key can actually use: the account's models and inference profiles
are listed (a fixed list when listing isn't allowed), tried best first, and the
first one that answers is remembered for the life of the process.
"""

import base64
import logging
import os
import re
import threading
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("rl.bedrock")

_DEFAULT_REGION = "us-east-1"
_MAX_MODEL_TRIES = 8  # models tried before giving up on one call

# Best first. Within a family, newer ids sort first.
_PROVIDER_RANK = ("anthropic", "amazon", "meta", "mistral", "openai", "cohere", "deepseek", "qwen")
_FAMILY_RANK = ("sonnet", "haiku", "opus", "nova-pro", "nova-premier", "nova-lite", "llama", "mistral-large")

# Used when the key may not list models (bedrock:ListFoundationModels denied).
_FALLBACK_MODELS = (
    "{geo}.anthropic.claude-sonnet-4-20250514-v1:0",
    "{geo}.anthropic.claude-3-5-haiku-20241022-v1:0",
    "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "anthropic.claude-3-haiku-20240307-v1:0",
    "{geo}.amazon.nova-pro-v1:0",
    "amazon.nova-pro-v1:0",
    "{geo}.amazon.nova-lite-v1:0",
    "amazon.nova-lite-v1:0",
)

# Error codes meaning "this model can't be used with this key/region" - try the next one.
_MODEL_UNUSABLE = ("AccessDeniedException", "ResourceNotFoundException", "ValidationException")


def is_bedrock_key(value: Optional[str]) -> bool:
    value = (value or "").strip()
    return value.startswith("ABSK") or value.startswith("bedrock-api-key-")


def bedrock_key() -> Optional[str]:
    explicit = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
    if explicit:
        return explicit
    for name in ("LLM_API_KEY", "OPENAI_API_KEY"):
        value = os.environ.get(name)
        if is_bedrock_key(value):
            return value.strip()
    return None


def available() -> bool:
    """A Bedrock key is set (in either variable)."""
    return bedrock_key() is not None


def _region_in_key(key: str) -> Optional[str]:
    """Short-term keys carry a presigned URL whose credential scope names the region."""
    body = key[4:] if key.startswith("ABSK") else key
    body = body[len("bedrock-api-key-"):] if body.startswith("bedrock-api-key-") else body
    for _ in range(2):  # the URL may itself be base64 inside the key
        try:
            body = base64.b64decode(body + "=" * (-len(body) % 4)).decode("utf-8", "ignore")
        except (ValueError, TypeError):
            return None
        body = body[len("bedrock-api-key-"):] if body.startswith("bedrock-api-key-") else body
        found = re.search(r"/([a-z]{2}(?:-[a-z]+)+-\d)/bedrock/", urllib.parse.unquote(body))
        if found:
            return found.group(1)
    return None


def region() -> str:
    configured = (
        os.environ.get("BEDROCK_REGION") or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    )
    if configured:
        return configured
    key = bedrock_key()
    return (_region_in_key(key) if key else None) or _DEFAULT_REGION


def _geo(region_name: str) -> str:
    prefix = region_name.split("-", 1)[0]
    return {"ap": "apac", "us": "us", "eu": "eu", "ca": "ca", "sa": "sa"}.get(prefix, "us")


_lock = threading.Lock()
_clients: Dict[Tuple[str, str, int, int], Any] = {}
_candidates: Dict[str, List[str]] = {}
_working: Dict[str, str] = {}


def client(service: str = "bedrock-runtime", read_timeout: int = 300, max_attempts: int = 3) -> Any:
    """One client per service/region (boto3 clients are thread-safe). boto3 reads
    the key from AWS_BEARER_TOKEN_BEDROCK, so a key found in OPENAI_API_KEY is
    copied there first; without any key the standard AWS chain is used."""
    key = bedrock_key()
    if key and not os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = key
    region_name = region()
    cache_key = (service, region_name, read_timeout, max_attempts)
    with _lock:
        if cache_key not in _clients:
            import boto3
            from botocore.config import Config

            _clients[cache_key] = boto3.client(
                service,
                region_name=region_name,
                config=Config(read_timeout=read_timeout, retries={"mode": "standard", "max_attempts": max_attempts}),
            )
        return _clients[cache_key]


def _rank(model_id: str) -> Tuple[int, int, int]:
    base = model_id.split(".", 1)[1] if model_id.split(".", 1)[0] in ("us", "eu", "apac", "ca", "sa", "global") else model_id
    provider = base.split(".", 1)[0]
    provider_rank = _PROVIDER_RANK.index(provider) if provider in _PROVIDER_RANK else len(_PROVIDER_RANK)
    family_rank = next((i for i, f in enumerate(_FAMILY_RANK) if f in base), len(_FAMILY_RANK))
    profile_first = 0 if base != model_id else 1
    return provider_rank, family_rank, profile_first


def _discover(region_name: str) -> List[str]:
    """Text models this account can call in the region: system inference profiles
    plus on-demand foundation models, best first."""
    control = client("bedrock", 30)
    ids: List[str] = []
    profiles = control.list_inference_profiles(typeEquals="SYSTEM_DEFINED", maxResults=1000)
    ids += [p["inferenceProfileId"] for p in profiles.get("inferenceProfileSummaries", []) if p.get("status") == "ACTIVE"]
    models = control.list_foundation_models(byOutputModality="TEXT", byInferenceType="ON_DEMAND")
    for m in models.get("modelSummaries", []):
        if "TEXT" in (m.get("inputModalities") or []) and (m.get("modelLifecycle") or {}).get("status") != "LEGACY":
            ids.append(m["modelId"])
    ids = [i for i in dict.fromkeys(ids) if not re.search(r"embed|rerank|image|canvas|reel|sonic|titan", i)]
    # newest first inside a family (ids carry dates / versions), then by provider/family preference
    ids.sort(reverse=True)
    ids.sort(key=_rank)
    return ids


def candidates() -> List[str]:
    configured = os.environ.get("BEDROCK_MODEL_ID")
    if configured:
        return [configured]
    region_name = region()
    with _lock:
        cached = _candidates.get(region_name)
    if cached is not None:
        return cached
    try:
        found = _discover(region_name)
    except Exception as e:  # no permission to list, or the control plane is unreachable
        logger.warning("Could not list Bedrock models in %s (%s); trying the built-in list", region_name, e)
        found = []
    if not found:
        found = [m.format(geo=_geo(region_name)) for m in _FALLBACK_MODELS]
    logger.info("Bedrock models to try in %s: %s", region_name, found[:_MAX_MODEL_TRIES])
    with _lock:
        _candidates[region_name] = found
    return found


def model_unusable(code: str) -> bool:
    """This error means the model can't be used with this key/region: try the next one."""
    return code in _MODEL_UNUSABLE and not os.environ.get("BEDROCK_MODEL_ID")


def order() -> List[str]:
    """Models to try for one call: the remembered working one first, at most _MAX_MODEL_TRIES."""
    with _lock:
        working = _working.get(region())
    return (([working] if working else []) + [m for m in candidates() if m != working])[:_MAX_MODEL_TRIES]


def remember(model_id: str) -> None:
    region_name = region()
    with _lock:
        if _working.get(region_name) != model_id:
            logger.info("Bedrock model in use: %s (region %s)", model_id, region_name)
        _working[region_name] = model_id


def forget(model_id: str) -> None:
    with _lock:
        if _working.get(region()) == model_id:
            del _working[region()]
