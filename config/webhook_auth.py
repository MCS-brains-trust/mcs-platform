"""
Webhook authentication for external service integrations (n8n, etc.).

Supports two modes:
1. HMAC-SHA256 signature verification (preferred)
2. Bearer token authentication (simpler alternative)
"""
import hashlib
import hmac
import logging

from django.conf import settings
from django.http import JsonResponse

logger = logging.getLogger(__name__)


def verify_webhook(request):
    """
    Verify an incoming webhook request.
    Checks HMAC signature header or Bearer token.
    Returns (True, None) on success, (False, JsonResponse) on failure.
    """
    webhook_secret = getattr(settings, "WEBHOOK_SECRET", "")
    if not webhook_secret:
        logger.error("WEBHOOK_SECRET not configured — rejecting webhook request")
        return False, JsonResponse(
            {"status": "error", "message": "Webhook authentication not configured"},
            status=403,
        )

    # Method 1: HMAC-SHA256 signature (preferred)
    signature = request.headers.get("X-Webhook-Signature", "")
    if signature:
        expected = hmac.new(
            webhook_secret.encode(),
            request.body,
            hashlib.sha256,
        ).hexdigest()
        if hmac.compare_digest(signature, expected):
            return True, None
        logger.warning("Webhook HMAC signature verification failed")
        return False, JsonResponse(
            {"status": "error", "message": "Invalid signature"},
            status=403,
        )

    # Method 2: Bearer token
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if hmac.compare_digest(token, webhook_secret):
            return True, None
        logger.warning("Webhook Bearer token verification failed")
        return False, JsonResponse(
            {"status": "error", "message": "Invalid token"},
            status=403,
        )

    logger.warning("Webhook request missing authentication")
    return False, JsonResponse(
        {"status": "error", "message": "Authentication required"},
        status=401,
    )
