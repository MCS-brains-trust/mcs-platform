"""
Webhook endpoints for third-party service callbacks.

Handles:
- FuseSign: signing status updates (signed, declined, voided)
- AWS Textract: OCR completion callbacks (via SNS)
"""
import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)


def _verify_fusesign_webhook(request):
    """
    Verify a FuseSign webhook request using the API key as a shared secret.
    FuseSign sends a Bearer token in the Authorization header that should
    match our configured FUSESIGN_API_KEY.
    """
    from django.conf import settings

    api_key = getattr(settings, "FUSESIGN_API_KEY", "")
    if not api_key:
        logger.error("FUSESIGN_API_KEY not configured — rejecting webhook")
        return False

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        import hmac
        token = auth_header[7:]
        return hmac.compare_digest(token, api_key)

    # Also check for X-Webhook-Secret header (alternate pattern)
    webhook_secret = request.headers.get("X-Webhook-Secret", "")
    if webhook_secret:
        import hmac
        return hmac.compare_digest(webhook_secret, api_key)

    return False


@csrf_exempt
@require_POST
def fusesign_webhook(request):
    """
    Receive FuseSign signing status updates.

    Expected payload:
    {
        "event": "envelope.completed" | "envelope.declined" | "envelope.voided",
        "envelope_id": "...",
        "signed_at": "...",  (optional)
        "declined_reason": "...",  (optional)
    }
    """
    from core.models import LegalDocument, ActivityLog

    if not _verify_fusesign_webhook(request):
        logger.warning("FuseSign webhook authentication failed")
        return JsonResponse({"error": "Authentication failed"}, status=403)

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    envelope_id = payload.get("envelope_id", "")
    event = payload.get("event", "")

    if not envelope_id:
        return JsonResponse({"error": "Missing envelope_id"}, status=400)

    # Find the document by envelope ID
    try:
        doc = LegalDocument.objects.get(fusesign_envelope_id=envelope_id)
    except LegalDocument.DoesNotExist:
        logger.warning("FuseSign webhook: no document found for envelope %s", envelope_id)
        return JsonResponse({"error": "Document not found"}, status=404)

    # Map FuseSign events to our status
    status_map = {
        "envelope.completed": LegalDocument.FuseSignStatus.SIGNED,
        "envelope.signed": LegalDocument.FuseSignStatus.SIGNED,
        "envelope.declined": LegalDocument.FuseSignStatus.DECLINED,
        "envelope.voided": LegalDocument.FuseSignStatus.DECLINED,
    }

    new_status = status_map.get(event)
    if not new_status:
        logger.info("FuseSign webhook: unhandled event '%s' for envelope %s", event, envelope_id)
        return JsonResponse({"status": "ok", "message": "Event acknowledged"})

    old_status = doc.fusesign_status
    doc.fusesign_status = new_status
    doc.save(update_fields=["fusesign_status"])

    logger.info(
        "FuseSign webhook: envelope %s status %s → %s (doc %s)",
        envelope_id, old_status, new_status, doc.pk,
    )

    # Log activity
    ActivityLog.objects.create(
        event_type="fusesign_webhook",
        title=f"FuseSign: {doc.get_document_type_display()} — {event}",
        description=(
            f"Document '{doc.title}' envelope {envelope_id} "
            f"status updated to {new_status} via FuseSign webhook."
        ),
        entity=doc.entity,
    )

    return JsonResponse({"status": "ok"})


def _is_valid_sns_host(url):
    """
    Return True only if *url* is an https URL whose host is a legitimate
    AWS SNS endpoint (sns.<region>.amazonaws.com). Used to prevent SSRF via
    forged SigningCertURL / SubscribeURL values.
    """
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False

    if parsed.scheme != "https":
        return False

    host = (parsed.hostname or "").lower()
    # sns.<region>.amazonaws.com  or  sns.<region>.amazonaws.com.cn
    if not host.startswith("sns."):
        return False
    if not (host.endswith(".amazonaws.com") or host.endswith(".amazonaws.com.cn")):
        return False
    return True


def _sns_string_to_sign(payload):
    """Build the canonical string that AWS SNS signs for this message type."""
    msg_type = payload.get("Type", "")
    if msg_type == "Notification":
        keys = ["Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type"]
    else:
        # SubscriptionConfirmation / UnsubscribeConfirmation
        keys = ["Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type"]

    parts = []
    for key in keys:
        if key in payload and payload.get(key) is not None:
            parts.append(key)
            parts.append(str(payload[key]))
    return ("\n".join(parts) + "\n").encode("utf-8")


def _verify_sns_message(payload):
    """
    Verify that *payload* is a genuine AWS SNS message by validating its
    cryptographic signature against the AWS-hosted signing certificate.

    Returns True only for authentic messages. Rejects anything whose
    SigningCertURL is not a legitimate AWS SNS host (SSRF guard) or whose
    signature does not verify.
    """
    import base64

    import requests
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.x509 import load_pem_x509_certificate

    cert_url = payload.get("SigningCertURL") or payload.get("SigningCertUrl") or ""
    if not _is_valid_sns_host(cert_url):
        logger.warning("SNS message rejected: invalid SigningCertURL host %r", cert_url)
        return False

    signature_b64 = payload.get("Signature", "")
    if not signature_b64:
        logger.warning("SNS message rejected: missing Signature")
        return False

    try:
        resp = requests.get(cert_url, timeout=10)
        resp.raise_for_status()
        cert = load_pem_x509_certificate(resp.content)
        public_key = cert.public_key()
        signature = base64.b64decode(signature_b64)
        string_to_sign = _sns_string_to_sign(payload)

        # SignatureVersion 1 → SHA1, version 2 → SHA256
        version = str(payload.get("SignatureVersion", "1"))
        algorithm = hashes.SHA256() if version == "2" else hashes.SHA1()

        public_key.verify(signature, string_to_sign, padding.PKCS1v15(), algorithm)
        return True
    except Exception as e:
        logger.warning("SNS signature verification failed: %s", e)
        return False


@csrf_exempt
@require_POST
def textract_webhook(request):
    """
    Receive AWS SNS notifications for Textract job completion.

    AWS SNS sends a JSON payload with:
    - SubscriptionConfirmation: initial endpoint verification
    - Notification: actual Textract result notification

    Every message is authenticated via its SNS signature before any action
    is taken, and outbound SubscribeURL fetches are restricted to AWS SNS
    hosts to prevent SSRF.
    """
    from core.models import GoverningDocument

    # SNS sends content-type text/plain with JSON body
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    # Authenticate the message before doing anything with it
    if not _verify_sns_message(payload):
        logger.warning("Textract webhook: SNS signature verification failed")
        return JsonResponse({"error": "Authentication failed"}, status=403)

    message_type = request.headers.get("x-amz-sns-message-type", "") or payload.get("Type", "")

    # Handle SNS subscription confirmation
    if message_type == "SubscriptionConfirmation":
        subscribe_url = payload.get("SubscribeURL", "")
        # SSRF guard: only ever GET a legitimate AWS SNS host over https
        if subscribe_url and _is_valid_sns_host(subscribe_url):
            import requests
            try:
                requests.get(subscribe_url, timeout=10)
                logger.info("SNS subscription confirmed: %s", payload.get("TopicArn", ""))
            except Exception as e:
                logger.error("SNS subscription confirmation failed: %s", e)
        else:
            logger.warning(
                "SNS subscription confirmation rejected: invalid SubscribeURL host %r",
                subscribe_url,
            )
            return JsonResponse({"error": "Invalid SubscribeURL"}, status=400)
        return JsonResponse({"status": "ok"})

    # Handle Textract completion notification
    if message_type == "Notification":
        try:
            message = json.loads(payload.get("Message", "{}"))
        except (json.JSONDecodeError, ValueError):
            message = {}

        job_id = message.get("JobId", "")
        status = message.get("Status", "")

        if not job_id:
            return JsonResponse({"error": "Missing JobId"}, status=400)

        logger.info("Textract webhook: job %s status %s", job_id, status)

        # Find the governing document
        try:
            doc = GoverningDocument.objects.get(textract_job_id=job_id)
        except GoverningDocument.DoesNotExist:
            logger.warning("Textract webhook: no document found for job %s", job_id)
            return JsonResponse({"error": "Document not found"}, status=404)

        if status == "SUCCEEDED":
            # Queue Celery task to process the Textract result
            from core.tasks import process_textract_result
            process_textract_result.delay(str(doc.pk), job_id)
            logger.info("Textract result processing queued for doc %s", doc.pk)
        elif status == "FAILED":
            try:
                doc.extraction_status = "failed"
                doc.extraction_error = message.get("StatusMessage", "Textract job failed")
                doc.save(update_fields=["extraction_status", "extraction_error"])
                logger.error(
                    "Textract job %s failed: %s",
                    job_id, message.get("StatusMessage"),
                )
            except Exception:
                logger.exception(
                    "Textract webhook: failed to persist FAILED status for job %s "
                    "(doc %s) — recovery command will catch this on next sweep",
                    job_id, doc.pk,
                )

        return JsonResponse({"status": "ok"})

    return JsonResponse({"status": "ok", "message": "Unhandled message type"})
