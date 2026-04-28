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


@csrf_exempt
@require_POST
def textract_webhook(request):
    """
    Receive AWS SNS notifications for Textract job completion.

    AWS SNS sends a JSON payload with:
    - SubscriptionConfirmation: initial endpoint verification
    - Notification: actual Textract result notification
    """
    from core.models import GoverningDocument

    # SNS sends content-type text/plain with JSON body
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    message_type = request.headers.get("x-amz-sns-message-type", "")

    # Handle SNS subscription confirmation
    if message_type == "SubscriptionConfirmation":
        subscribe_url = payload.get("SubscribeURL", "")
        if subscribe_url:
            import requests
            try:
                requests.get(subscribe_url, timeout=10)
                logger.info("SNS subscription confirmed: %s", payload.get("TopicArn", ""))
            except Exception as e:
                logger.error("SNS subscription confirmation failed: %s", e)
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
