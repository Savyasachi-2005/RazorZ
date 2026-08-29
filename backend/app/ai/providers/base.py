from __future__ import annotations

from typing import Protocol

from app.ai.schemas import AssistMode, EvidencePacket, AIAssistResult
from app.copilot.schemas import CopilotAnswer, CopilotContext


class AIProvider(Protocol):
    name: str

    def assist(self, packet: EvidencePacket, mode: AssistMode = "full_analysis") -> AIAssistResult:
        """Return validated structured assistance. Must not mutate financial state."""
        ...

    def copilot(self, context: CopilotContext) -> CopilotAnswer:
        """Explain compact read-only evidence. Must not invent or mutate financial data."""
        ...
