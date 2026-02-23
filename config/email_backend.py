"""
Custom Django email backend using Resend HTTP API.

Bypasses SMTP entirely — uses HTTPS (port 443) which is never blocked.
Requires RESEND_API_KEY in environment / Django settings.
"""

import json
import logging
import requests
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)


class ResendEmailBackend(BaseEmailBackend):
    """Send emails via Resend's HTTP API (https://api.resend.com/emails)."""

    API_URL = "https://api.resend.com/emails"

    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.api_key = getattr(settings, "RESEND_API_KEY", "")

    def send_messages(self, email_messages):
        if not self.api_key:
            logger.error("RESEND_API_KEY is not set. Cannot send emails.")
            if not self.fail_silently:
                raise ValueError("RESEND_API_KEY is not configured.")
            return 0

        sent_count = 0
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        for message in email_messages:
            try:
                payload = {
                    "from": message.from_email,
                    "to": list(message.to),
                    "subject": message.subject,
                }

                # Use HTML if available, otherwise plain text
                if hasattr(message, "alternatives") and message.alternatives:
                    for content, mimetype in message.alternatives:
                        if mimetype == "text/html":
                            payload["html"] = content
                            break

                # Always include plain text
                if message.body:
                    payload["text"] = message.body

                # If no HTML was set from alternatives, check if body is the only content
                if "html" not in payload and message.content_subtype == "html":
                    payload["html"] = message.body
                    payload.pop("text", None)

                # Handle CC and BCC
                if message.cc:
                    payload["cc"] = list(message.cc)
                if message.bcc:
                    payload["bcc"] = list(message.bcc)

                # Handle reply-to
                if message.reply_to:
                    payload["reply_to"] = list(message.reply_to)

                response = requests.post(
                    self.API_URL,
                    headers=headers,
                    data=json.dumps(payload),
                    timeout=10,
                )

                if response.status_code == 200:
                    sent_count += 1
                    logger.info(
                        f"Email sent via Resend to {message.to} "
                        f"(id={response.json().get('id', 'unknown')})"
                    )
                else:
                    logger.error(
                        f"Resend API error ({response.status_code}): "
                        f"{response.text} — to={message.to}"
                    )
                    if not self.fail_silently:
                        raise Exception(
                            f"Resend API error {response.status_code}: {response.text}"
                        )

            except requests.RequestException as e:
                logger.error(f"Resend HTTP request failed: {e} — to={message.to}")
                if not self.fail_silently:
                    raise

        return sent_count
