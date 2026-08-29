from __future__ import annotations

import json
from typing import Any, Dict

import httpx

from app.ai.prompts import SYSTEM_PROMPT
from app.ai.schemas import AIAssistError, AIAssistResult, AssistMode, EvidencePacket
from app.config import settings
from app.copilot.prompts import COPILOT_SYSTEM_PROMPT
from app.copilot.schemas import CopilotAnswer, CopilotContext, CopilotError


class HttpLLMProvider:
    """OpenAI-compatible chat completions provider. Requires AI_API_KEY."""

    name = "http_llm"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else settings.ai_api_key).strip()
        self.base_url = (base_url if base_url is not None else settings.ai_base_url).rstrip("/")
        self.model = model or settings.ai_model
        self.timeout = timeout if timeout is not None else settings.ai_timeout_seconds

    def assist(self, packet: EvidencePacket, mode: AssistMode = "full_analysis") -> AIAssistResult:
        if not self.api_key:
            raise AIAssistError("AI_API_KEY is not configured", code="provider_unavailable")

        user_payload: Dict[str, Any] = {
            "mode": mode,
            "evidence_packet": packet.to_prompt_dict(),
            "rules": [
                "Do not invent amounts",
                "Do not resolve or reject",
                "Keep ai_confidence separate from deterministic confidence",
            ],
        }
        data = self._generate_json(SYSTEM_PROMPT, user_payload)
        try:
            return AIAssistResult.model_validate(data)
        except Exception as exc:
            raise AIAssistError("AI response failed schema validation", code="invalid_response") from exc

    def copilot(self, context: CopilotContext) -> CopilotAnswer:
        """Read-only grounded answer. Same transport as assist(), different schema."""
        if not self.api_key:
            raise CopilotError("AI_API_KEY is not configured", code="provider_unavailable")
        payload = context.to_prompt_dict()
        payload["rules"] = [
            "Use only values present in evidence",
            "Never invent ids, amounts, or counts",
            "You cannot resolve, reject, or modify anything",
        ]
        try:
            data = self._generate_json(COPILOT_SYSTEM_PROMPT, payload)
        except AIAssistError as exc:
            raise CopilotError(exc.message, code=exc.code) from exc
        try:
            return CopilotAnswer.model_validate(data)
        except Exception as exc:
            raise CopilotError("Copilot response failed schema validation", code="invalid_response") from exc

    def _generate_json(self, system_prompt: str, user_payload: Dict[str, Any]) -> Any:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "temperature": 0.2,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": json.dumps(user_payload)},
                        ],
                    },
                )
        except httpx.TimeoutException as exc:
            raise AIAssistError("AI provider timed out", code="timeout") from exc
        except httpx.HTTPError as exc:
            raise AIAssistError("AI provider unavailable", code="provider_unavailable") from exc

        if response.status_code >= 400:
            raise AIAssistError(
                f"AI provider returned HTTP {response.status_code}",
                code="provider_unavailable",
            )

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            return json.loads(content) if isinstance(content, str) else content
        except Exception as exc:
            raise AIAssistError("AI provider returned malformed response", code="invalid_response") from exc
