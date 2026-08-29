from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


AssistMode = Literal["full_analysis", "suggest_note", "investigation_steps"]


class EvidencePacket(BaseModel):
    """Compact, token-efficient context. Money fields are strings; never invent missing ones."""

    exception_type: str
    exception_id: str
    status: str
    deterministic_reason: str
    confidence: float = Field(description="Deterministic reconciliation confidence (0-1)")
    difference: Optional[str] = None
    pair_type: Optional[str] = None
    order_id: Optional[str] = None
    payment_id: Optional[str] = None
    settlement_id: Optional[str] = None
    refund_id: Optional[str] = None
    fee_id: Optional[str] = None
    matched_with: Optional[str] = None
    order_amount: Optional[str] = None
    payment_amount: Optional[str] = None
    evidence: List[str] = Field(default_factory=list)
    description: str = ""
    possible_root_causes: List[str] = Field(default_factory=list)
    recommended_action: Optional[str] = None

    def to_prompt_dict(self) -> Dict[str, Any]:
        data = self.model_dump(exclude_none=True)
        return data


class AIAssistResult(BaseModel):
    likely_cause: str = Field(min_length=3)
    explanation: str = Field(min_length=3)
    investigation_steps: List[str] = Field(min_length=1)
    suggested_action: str = Field(min_length=3)
    suggested_review_note: str = Field(min_length=3)
    ai_confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("investigation_steps")
    @classmethod
    def _non_empty_steps(cls, value: List[str]) -> List[str]:
        cleaned = [step.strip() for step in value if str(step).strip()]
        if not cleaned:
            raise ValueError("investigation_steps must contain at least one step")
        return cleaned


class AIAssistError(Exception):
    """Controlled AI failure — never mutates financial data."""

    def __init__(self, message: str, *, code: str = "ai_failure"):
        super().__init__(message)
        self.code = code
        self.message = message
