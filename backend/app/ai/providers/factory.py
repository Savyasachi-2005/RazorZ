from __future__ import annotations

from app.ai.providers.gemini import GeminiProvider
from app.ai.providers.http_llm import HttpLLMProvider
from app.ai.providers.mock import MockProvider
from app.ai.schemas import AIAssistError
from app.config import settings


def get_provider(name: str | None = None):
    provider_name = (name or settings.ai_provider or "mock").strip().lower()
    if provider_name in {"mock", "mock-model", ""}:
        return MockProvider()
    if provider_name in {"gemini", "google", "google_gemini"}:
        return GeminiProvider()
    if provider_name in {"http", "http_llm", "openai", "openai_compatible"}:
        return HttpLLMProvider()
    # Prefer Gemini when an API key is present but provider label is unknown.
    if settings.ai_api_key:
        return GeminiProvider()
    raise AIAssistError(f"Unknown AI_PROVIDER '{provider_name}' and no API key", code="provider_unavailable")
