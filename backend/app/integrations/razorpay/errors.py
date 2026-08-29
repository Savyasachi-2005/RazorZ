from __future__ import annotations


class RazorpayIntegrationError(Exception):
    """Controlled Razorpay adapter failure — never mutates financial truth."""

    def __init__(self, message: str, *, code: str = "razorpay_error"):
        super().__init__(message)
        self.message = message
        self.code = code
