from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.integrations.razorpay.errors import RazorpayIntegrationError

DEFAULT_BASE_URL = "https://api.razorpay.com/v1"


class RazorpayClient:
    """Thin HTTP client for Razorpay REST API (Basic auth). Never logs secrets."""

    def __init__(
        self,
        *,
        key_id: str | None = None,
        key_secret: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        mode: str | None = None,
        allow_live: bool | None = None,
    ) -> None:
        self.key_id = (key_id if key_id is not None else settings.razorpay_key_id).strip()
        self.key_secret = (key_secret if key_secret is not None else settings.razorpay_key_secret).strip()
        self.base_url = (base_url or settings.razorpay_base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout if timeout is not None else settings.razorpay_timeout_seconds
        self.mode = (mode if mode is not None else settings.razorpay_mode).strip().lower() or "test"
        self.allow_live = settings.razorpay_allow_live if allow_live is None else bool(allow_live)

    @property
    def configured(self) -> bool:
        return bool(self.key_id and self.key_secret)

    def assert_ready(self) -> None:
        if not self.configured:
            raise RazorpayIntegrationError(
                "Razorpay keys are not configured (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET)",
                code="not_configured",
            )
        if self.mode != "test" and not self.allow_live:
            raise RazorpayIntegrationError(
                "Live mode sync is disabled. Set RAZORPAY_MODE=test or RAZORPAY_ALLOW_LIVE=true.",
                code="live_blocked",
            )
        if self.mode == "test" and not self.key_id.startswith("rzp_test_"):
            raise RazorpayIntegrationError(
                "RAZORPAY_MODE=test requires a test key id (rzp_test_…)",
                code="key_mode_mismatch",
            )

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.assert_ready()
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(
                    url,
                    params=params or {},
                    auth=(self.key_id, self.key_secret),
                    headers={"Accept": "application/json"},
                )
        except httpx.TimeoutException as exc:
            raise RazorpayIntegrationError("Razorpay API timed out", code="timeout") from exc
        except httpx.HTTPError as exc:
            raise RazorpayIntegrationError("Razorpay API request failed", code="network_error") from exc

        if response.status_code in {401, 403}:
            raise RazorpayIntegrationError("Razorpay authentication failed", code="auth_failed")
        if response.status_code == 429:
            raise RazorpayIntegrationError("Razorpay rate limit exceeded", code="rate_limited")
        if response.status_code >= 400:
            # Do not include response body secrets; keep message short.
            raise RazorpayIntegrationError(
                f"Razorpay API error ({response.status_code})",
                code="api_error",
            )
        try:
            payload = response.json()
        except Exception as exc:
            raise RazorpayIntegrationError("Razorpay returned non-JSON", code="invalid_response") from exc
        if not isinstance(payload, dict):
            raise RazorpayIntegrationError("Unexpected Razorpay payload", code="invalid_response")
        return payload

    def fetch_collection(self, resource: str, *, count: int = 100, skip: int = 0) -> List[Dict[str, Any]]:
        """Fetch one page of a collection endpoint (`orders`, `payments`, `refunds`, `settlements`)."""
        count = max(1, min(int(count), 100))
        skip = max(0, int(skip))
        payload = self._get(f"/{resource}", params={"count": count, "skip": skip})
        items = payload.get("items")
        if items is None:
            return []
        if not isinstance(items, list):
            raise RazorpayIntegrationError(f"Invalid {resource} collection", code="invalid_response")
        return [item for item in items if isinstance(item, dict)]

    def ping(self) -> Dict[str, Any]:
        """Lightweight connectivity check (fetch 1 payment; empty is OK)."""
        self.assert_ready()
        self.fetch_collection("payments", count=1, skip=0)
        return {"ok": True, "mode": self.mode, "key_id_prefix": self.key_id[:12] + "…"}
