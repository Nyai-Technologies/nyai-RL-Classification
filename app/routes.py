"""The endpoints. Thin: they take the request, call the service, return the answer."""
from fastapi import APIRouter, HTTPException

from app import service
from app.config import settings
from app.schemas import ClassificationRequest, ClassificationResponse, ErrorResponse

router = APIRouter()


@router.post(
    "/rl-classification", tags=["RL classification"], summary="RL classification",
    response_model=ClassificationResponse, response_model_exclude_unset=True,
    responses={422: {"model": ErrorResponse, "description": "invalid input"},
               502: {"model": ErrorResponse, "description": "the LLM provider failed for every file"},
               503: {"model": ErrorResponse, "description": "API key or model is not configured correctly"}})
async def rl_classification(req: ClassificationRequest):
    """Classify the given files against the RL.

    Send one file or many. Each file carries its `file_id` and either `text` or your own `chunks`.
    The response has `results` (per file), `counts` (expected vs found per RL type), `by_status`, and `usage`
    (tokens, cost in USD and INR).
    """
    return await service.classify_request([i.model_dump() for i in req.rl], [f.model_dump() for f in req.files], req.rl_id)


@router.get("/ready", tags=["ops"])
async def ready():
    """Readiness probe: can this instance classify? (no LLM call is made)"""
    if not settings.LLM_API_KEY:
        raise HTTPException(503, "OPENAI_API_KEY is not set")
    return {"ready": True, "model": settings.LLM_MODEL}


@router.get("/health", tags=["ops"])
async def health():
    """Liveness probe: the process is up."""
    return {"ok": True, "model": settings.LLM_MODEL}
