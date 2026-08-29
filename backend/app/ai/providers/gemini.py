from __future__ import annotations

import json
from typing import Any, Dict
from urllib.parse import urlencode

import httpx

from app.ai.prompts import SYSTEM_PROMPT
from app.ai.schemas import AIAssistError, AIAssistResult, AssistMode, EvidencePacket
from app.config import settings
from app.copilot.prompts import COPILOT_SYSTEM_PROMPT
from app.copilot.schemas import CopilotAnswer, CopilotContext, CopilotError

DEFAULT_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"


class GeminiProvider:
    """Google Gemini generateContent provider. Requires AI_API_KEY (Gemini API key)."""

    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else settings.ai_api_key).strip()
        configured_base = (base_url if base_url is not None else settings.ai_base_url).rstrip("/")
        # If someone left the OpenAI default in .env while selecting gemini, use Gemini host.
        if "openai.com" in configured_base or not configured_base:
            self.base_url = DEFAULT_GEMINI_BASE
        else:
            self.base_url = configured_base
        model_name = (model or settings.ai_model or DEFAULT_GEMINI_MODEL).strip()
        if not model_name or model_name in {"mock-model", "gpt-4o-mini", "gpt-4o"}:
            model_name = DEFAULT_GEMINI_MODEL
        self.model = model_name
        self.timeout = timeout if timeout is not None else settings.ai_timeout_seconds

    def assist(self, packet: EvidencePacket, mode: AssistMode = "full_analysis") -> AIAssistResult:
        if not self.api_key:
            raise AIAssistError("AI_API_KEY is not configured for Gemini", code="provider_unavailable")

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
            raise CopilotError("AI_API_KEY is not configured for Gemini", code="provider_unavailable")
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
        # models/gemini-2.0-flash:generateContent?key=...
        model_path = self.model if self.model.startswith("models/") else f"models/{self.model}"
        query = urlencode({"key": self.api_key})
        url = f"{self.base_url}/{model_path}:generateContent?{query}"

        body = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": json.dumps(user_payload)}],
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, headers={"Content-Type": "application/json"}, json=body)
        except httpx.TimeoutException as exc:
            raise AIAssistError("Gemini provider timed out", code="timeout") from exc
        except httpx.HTTPError as exc:
            raise AIAssistError("Gemini provider unavailable", code="provider_unavailable") from exc

        if response.status_code >= 400:
            detail = ""
            try:
                detail = str(response.json().get("error", {}).get("message", ""))[:200]
            except Exception:
                detail = response.text[:200]
            raise AIAssistError(
                f"Gemini returned HTTP {response.status_code}" + (f": {detail}" if detail else ""),
                code="provider_unavailable",
            )

        try:
            payload = response.json()
            parts = payload["candidates"][0]["content"]["parts"]
            text = "".join(str(part.get("text", "")) for part in parts).strip()
            # Strip markdown fences if the model wraps JSON anyway.
            if text.startswith("```"):
                text = text.strip("`")
                if text.lower().startswith("json"):
                    text = text[4:].strip()
            return json.loads(text)
        except Exception as exc:
            raise AIAssistError("Gemini returned malformed response", code="invalid_response") from exc
