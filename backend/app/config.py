from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env")
load_dotenv(_ROOT / "backend" / ".env")


def _get_env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    return value.strip() if value else default


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]

    if not url.startswith("postgresql+psycopg://"):
        return url

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("connect_timeout", "10")
    if host not in {"localhost", "127.0.0.1"}:
        query.setdefault("sslmode", "require")
    return urlunparse(parsed._replace(query=urlencode(query)))


@dataclass(frozen=True)
class ReconciliationThresholds:
    auto_resolve: Decimal = Decimal(_get_env("RECON_AUTO_RESOLVE", "0.99"))
    auto_resolve_warning: Decimal = Decimal(_get_env("RECON_AUTO_RESOLVE_WARNING", "0.90"))
    human_review: Decimal = Decimal(_get_env("RECON_HUMAN_REVIEW", "0.70"))


@dataclass(frozen=True)
class Settings:
    app_name: str = "RAZORZ"
    database_url: str = normalize_database_url(_get_env("DATABASE_URL", "sqlite:///./razorz.db"))
    ai_provider: str = _get_env("AI_PROVIDER", "mock")
    ai_model: str = _get_env("AI_MODEL", "mock-model")
    ai_api_key: str = _get_env("AI_API_KEY", "")
    # Default host for Gemini; OpenAI-compatible providers override via AI_BASE_URL.
    ai_base_url: str = _get_env("AI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
    ai_timeout_seconds: float = float(_get_env("AI_TIMEOUT_SECONDS", "20") or "20")
    env: str = _get_env("ENVIRONMENT", "development")
    # Comma-separated API keys. Empty disables auth outside production.
    razorz_api_keys: str = _get_env("RAZORZ_API_KEYS", _get_env("RAZORZ_API_KEY", ""))
    # Require a user session (or API key) on protected routes even without keys.
    razorz_auth_required: bool = _get_env("RAZORZ_AUTH_REQUIRED", "false").lower() == "true"
    debug: bool = _get_env("DEBUG", "true").lower() == "true"
    supabase_url: str = _get_env("SUPABASE_URL", "")
    supabase_anon_key: str = _get_env("SUPABASE_ANON_KEY", "")
    supabase_service_role_key: str = _get_env("SUPABASE_SERVICE_ROLE_KEY", "")
    frontend_origin: str = _get_env("FRONTEND_ORIGIN", "http://localhost:5173")
    razorpay_key_id: str = _get_env("RAZORPAY_KEY_ID", "")
    razorpay_key_secret: str = _get_env("RAZORPAY_KEY_SECRET", "")
    razorpay_mode: str = _get_env("RAZORPAY_MODE", "test")
    razorpay_base_url: str = _get_env("RAZORPAY_BASE_URL", "https://api.razorpay.com/v1")
    razorpay_timeout_seconds: float = float(_get_env("RAZORPAY_TIMEOUT_SECONDS", "20") or "20")
    razorpay_allow_live: bool = _get_env("RAZORPAY_ALLOW_LIVE", "false").lower() == "true"
    razorpay_webhook_secret: str = _get_env("RAZORPAY_WEBHOOK_SECRET", "")
    # Webhooks ingest immediately; reconciliation stays a full-dataset operation
    # driven by the existing polling adapter unless this is enabled.
    razorpay_webhook_reconcile: bool = _get_env("RAZORPAY_WEBHOOK_RECONCILE", "false").lower() == "true"
    thresholds: ReconciliationThresholds = ReconciliationThresholds()


settings = Settings()
