"""What goes in and what comes out of the API (also what the Swagger docs show)."""
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class RLItem(BaseModel):
    name: str
    description: str = ""
    count: Optional[int] = Field(None, ge=0, description="how many files of this type the user says they gave")


class FileText(BaseModel):
    """Send `chunks` (your own chunking, in reading order) or plain `text` (we split it)."""
    file_id: str = Field(min_length=1)
    text: Optional[str] = None
    chunks: Optional[list[str]] = None
    file_name: Optional[str] = None


class ClassificationRequest(BaseModel):
    rl: list[RLItem]
    files: list[FileText]
    rl_id: Optional[str] = Field(None, description="your own id for this RL (default: derived from its content)")

    model_config = {"json_schema_extra": {"example": {
        "rl": [
            {"name": "MSA", "description": "Master Services Agreement. Umbrella contract setting general legal terms "
                                           "for all future engagements: term, payment, liability, governing law.", "count": 1},
            {"name": "SOW", "description": "Statement of Work. Project-specific deliverables, milestones, fees and "
                                           "acceptance criteria issued under an MSA.", "count": 1},
        ],
        "files": [
            {"file_id": "f-001", "file_name": "doc_0017.pdf",
             "text": "MASTER SERVICES AGREEMENT. This Agreement is entered into between Orchid Retail and Vertex..."},
            {"file_id": "f-002", "chunks": ["STATEMENT OF WORK No. 4", "1. Project overview ...", "3. Deliverables ..."]},
        ]}}}


class Tag(BaseModel):
    doc_type: str = Field(description="an RL name, or 'no rl class identified for this' when no RL type fits")
    confidence: float
    status: Literal["high", "medium", "low"]


class FileResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    file_id: str
    file_name: str
    reason: str = ""
    tags: list[Tag] = Field(default_factory=list, description="every RL type that genuinely fits (one or more), best first; empty when the file could not be classified (see `reason`)")
    attempt: int = 1
    chunks_used: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Optional[float] = None
    cost_inr: Optional[float] = None


class CountCheck(BaseModel):
    rl: str
    expected: Optional[int] = None
    classified: int
    needs_review: int
    status: str = Field(description="OK | MISSING n | EXTRA n | PENDING_REVIEW | NO_COUNT_GIVEN")


class UsageSummary(BaseModel):
    model_config = ConfigDict(extra="allow")
    calls: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Optional[float] = None
    cost_inr: Optional[float] = None
    usd_inr_rate: Optional[float] = None
    aborted: Optional[str] = Field(None, description="why the run stopped early, if it did")


class ClassificationResponse(BaseModel):
    rl_id: str
    results: list[FileResult]
    counts: list[CountCheck]
    by_status: dict[str, int]
    by_type: dict[str, int] = Field(description="high-confidence files per RL type, plus OTHER")
    usage: UsageSummary


class ErrorResponse(BaseModel):
    detail: Union[str, list]
    request_id: str
