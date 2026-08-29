from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Every tool the Copilot may call. There is deliberately no mutation tool.
ToolName = Literal[
    "get_reconciliation_summary",
    "search_exceptions",
    "get_exception",
    "get_audit_events",
    "search_records",
    "get_record_relationships",
    "get_financial_summary",
    "get_unsettled_payments",
    "get_settlement_summary",
    "get_cross_source_summary",
]

READ_ONLY_TOOLS: tuple[str, ...] = (
    "get_reconciliation_summary",
    "search_exceptions",
    "get_exception",
    "get_audit_events",
    "search_records",
    "get_record_relationships",
    "get_financial_summary",
    "get_unsettled_payments",
    "get_settlement_summary",
    "get_cross_source_summary",
)


class CopilotError(Exception):
    """Controlled Copilot failure. Never mutates data."""

    def __init__(self, message: str, *, code: str = "copilot_failure"):
        super().__init__(message)
        self.code = code
        self.message = message


class ToolResult(BaseModel):
    """One tool invocation and its deterministic result."""

    tool: str
    ok: bool = True
    data: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=2000)


class CopilotQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    history: List[ConversationTurn] = Field(default_factory=list)

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("question must not be empty")
        return cleaned


class DataPoint(BaseModel):
    label: str
    value: str


class CopilotAnswer(BaseModel):
    """Validated model output. Financial values come from tool results, not the model."""

    answer: str = Field(min_length=3)
    key_findings: List[str] = Field(default_factory=list)
    data_points: List[DataPoint] = Field(default_factory=list)
    sources_used: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("key_findings")
    @classmethod
    def _clean_findings(cls, value: List[str]) -> List[str]:
        return [item.strip() for item in value if str(item).strip()]

    @field_validator("sources_used")
    @classmethod
    def _only_known_tools(cls, value: List[str]) -> List[str]:
        # Never surface a citation for a tool that does not exist.
        return [item for item in value if item in READ_ONLY_TOOLS]


class CopilotContext(BaseModel):
    """Compact evidence sent to the LLM. Intentionally small."""

    question: str
    intent: str
    tool_results: List[ToolResult] = Field(default_factory=list)
    recent_turns: List[ConversationTurn] = Field(default_factory=list)

    def to_prompt_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "intent": self.intent,
            "evidence": {
                result.tool: result.data if result.ok else {"error": result.error}
                for result in self.tool_results
            },
            "recent_turns": [turn.model_dump() for turn in self.recent_turns],
        }
