"""
Recover GoverningDocument records stuck in `ocr_pending`.

Background: AWS Textract async results are purged after 7 days. The webhook
path (SNS -> textract_webhook) is best-effort delivery and has historically
gone silent. This command is the polling/recovery fallback that guarantees
no document sits stuck indefinitely.

For each stuck doc it calls GetDocumentAnalysis on the stored JobId and:
  SUCCEEDED            -> pull blocks, save extracted_text, status=completed
  FAILED               -> save StatusMessage to extraction_error, status=failed
  IN_PROGRESS          -> log + skip
  InvalidJobIdException -> retrigger using the existing S3 object (no re-upload)
  empty job_id         -> retrigger from the source file

Usage:
    python manage.py recover_stuck_ocr --dry-run --all
    python manage.py recover_stuck_ocr --all
    python manage.py recover_stuck_ocr --entity <entity_id>
    python manage.py recover_stuck_ocr --doc <doc_pk>
    python manage.py recover_stuck_ocr --all --min-age-minutes 30 --limit 50
"""
import logging
import os
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Recover GoverningDocument records stuck in ocr_pending."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Sweep every stuck doc")
        parser.add_argument("--entity", type=str, default=None, help="Restrict to one entity_id")
        parser.add_argument("--doc", type=str, default=None, help="Restrict to one GoverningDocument pk")
        parser.add_argument("--dry-run", action="store_true", help="Report only, no writes")
        parser.add_argument(
            "--min-age-minutes", type=int, default=60,
            help="Only act on docs whose updated_at is older than N minutes (default 60)",
        )
        parser.add_argument("--limit", type=int, default=200, help="Cap the sweep size")

    def handle(self, *args, **opts):
        from core.models import GoverningDocument
        from core.ocr_service import (
            process_textract_callback,
            retrigger_textract,
        )

        if not (opts["all"] or opts["entity"] or opts["doc"]):
            self.stderr.write("Specify one of --all, --entity <id>, --doc <pk>")
            return

        cutoff = timezone.now() - timedelta(minutes=opts["min_age_minutes"])
        qs = GoverningDocument.objects.filter(extraction_status="ocr_pending")
        if opts["doc"]:
            qs = qs.filter(pk=opts["doc"])
        elif opts["entity"]:
            qs = qs.filter(entity_id=opts["entity"])
        else:
            qs = qs.filter(updated_at__lte=cutoff)

        qs = qs.order_by("uploaded_at")[: opts["limit"]]
        docs = list(qs)

        self.stdout.write(
            f"Found {len(docs)} stuck doc(s) "
            f"(min_age={opts['min_age_minutes']}m, dry_run={opts['dry_run']})"
        )

        summary = {"completed": 0, "failed": 0, "in_progress": 0, "retriggered": 0, "errors": 0}

        for doc in docs:
            label = f"{doc.pk} entity={doc.entity_id} job={doc.textract_job_id or '<none>'}"
            try:
                action = self._handle_doc(doc, dry_run=opts["dry_run"])
            except Exception as exc:
                logger.exception("recover_stuck_ocr: error handling %s", doc.pk)
                action = f"ERROR: {exc}"
                summary["errors"] += 1
            else:
                if action in summary:
                    summary[action] += 1

            self.stdout.write(f"  {label} -> {action}")

        self.stdout.write(self.style.SUCCESS(f"Summary: {summary}"))

    def _handle_doc(self, doc, dry_run):
        import boto3
        from botocore.exceptions import ClientError
        from core.ocr_service import process_textract_callback, retrigger_textract

        job_id = (doc.textract_job_id or "").strip()
        if not job_id:
            if dry_run:
                return "would-retrigger (no job_id)"
            result = retrigger_textract(doc)
            return "retriggered" if result.get("status") == "ocr_pending" else "failed"

        aws_region = os.environ.get("AWS_REGION", "ap-southeast-2")
        client = boto3.client("textract", region_name=aws_region)

        try:
            resp = client.get_document_analysis(JobId=job_id)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "InvalidJobIdException":
                if dry_run:
                    return "would-retrigger (expired)"
                result = retrigger_textract(doc)
                return "retriggered" if result.get("status") == "ocr_pending" else "failed"
            raise

        status = resp.get("JobStatus", "")
        if status == "SUCCEEDED":
            if dry_run:
                return "would-complete"
            cb = process_textract_callback(str(doc.pk), job_id)
            return "completed" if cb.get("status", "").startswith("completed") else "failed"

        if status in ("FAILED", "PARTIAL_SUCCESS"):
            if dry_run:
                return f"would-fail ({status})"
            doc.extraction_status = "failed"
            doc.extraction_error = (resp.get("StatusMessage") or status)[:2000]
            doc.save(update_fields=["extraction_status", "extraction_error"])
            return "failed"

        if status == "IN_PROGRESS":
            return "in_progress"

        # Unknown status — don't touch
        return f"unknown ({status})"
