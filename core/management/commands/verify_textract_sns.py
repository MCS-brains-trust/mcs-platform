"""
Verify the Textract SNS topic has at least one Confirmed HTTPS subscription
pointing at the production webhook URL.

Required IAM additions for the `statementhub-textract` user (apply manually
in AWS, NOT from code):

    {
      "Version": "2012-10-17",
      "Statement": [
        {
          "Effect": "Allow",
          "Action": [
            "sns:ListSubscriptionsByTopic",
            "sns:GetTopicAttributes"
          ],
          "Resource": "<TEXTRACT_SNS_TOPIC_ARN>"
        }
      ]
    }

Exits non-zero if no Confirmed HTTPS subscription is found.

Usage:
    python manage.py verify_textract_sns
    python manage.py verify_textract_sns --expected-endpoint https://statementhub.com.au/webhooks/textract/
"""
import logging
import os
import sys

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Verify a Confirmed HTTPS subscription exists on the Textract SNS topic."

    def add_arguments(self, parser):
        parser.add_argument(
            "--expected-endpoint", type=str, default=None,
            help="Optional: assert one subscription matches this exact endpoint",
        )

    def handle(self, *args, **opts):
        import boto3
        from botocore.exceptions import ClientError

        topic_arn = (
            os.environ.get("AWS_TEXTRACT_SNS_TOPIC_ARN")
            or os.environ.get("TEXTRACT_SNS_TOPIC_ARN", "")
        )
        if not topic_arn:
            self.stderr.write("AWS_TEXTRACT_SNS_TOPIC_ARN is not set")
            sys.exit(2)

        aws_region = os.environ.get("AWS_REGION", "ap-southeast-2")
        client = boto3.client("sns", region_name=aws_region)

        try:
            paginator = client.get_paginator("list_subscriptions_by_topic")
            subs = []
            for page in paginator.paginate(TopicArn=topic_arn):
                subs.extend(page.get("Subscriptions", []))
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            self.stderr.write(
                f"sns:ListSubscriptionsByTopic failed ({code}). "
                "Check IAM policy on the textract user — see this command's docstring."
            )
            sys.exit(2)

        confirmed_https = [
            s for s in subs
            if s.get("Protocol") in ("https", "http")
            and s.get("SubscriptionArn", "").startswith("arn:aws:sns:")
        ]

        self.stdout.write(f"Topic: {topic_arn}")
        self.stdout.write(f"Total subscriptions: {len(subs)}")
        self.stdout.write(f"Confirmed HTTP(S) subscriptions: {len(confirmed_https)}")
        for s in subs:
            self.stdout.write(
                f"  - protocol={s.get('Protocol')} "
                f"endpoint={s.get('Endpoint')} "
                f"arn={s.get('SubscriptionArn')}"
            )

        if not confirmed_https:
            self.stderr.write(self.style.ERROR(
                "No Confirmed HTTPS subscription on Textract SNS topic — "
                "OCR webhook delivery is broken. Polling fallback will still "
                "complete jobs, but with up to 15 min latency."
            ))
            sys.exit(1)

        if opts["expected_endpoint"]:
            match = [s for s in confirmed_https if s.get("Endpoint") == opts["expected_endpoint"]]
            if not match:
                self.stderr.write(self.style.ERROR(
                    f"No Confirmed subscription matches endpoint {opts['expected_endpoint']!r}"
                ))
                sys.exit(1)

        self.stdout.write(self.style.SUCCESS("Textract SNS subscription OK"))
