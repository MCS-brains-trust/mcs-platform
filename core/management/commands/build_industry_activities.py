"""
build_industry_activities -- regenerate the ATO BIC activity index from the
published PDF.

Usage
-----
    python manage.py build_industry_activities --pdf <path/to/NAT1827.pdf>
    python manage.py build_industry_activities --pdf <path> --dry-run

Getting the document
--------------------
The current edition is BIC 2021 (NAT 1827-12.2021) and it is still current:

  https://www.ato.gov.au/api/public/content/b6ceb8f8-835b-4864-a12e-69198fa86d96_n1827_Business_Industry_Codes_2021_Digital_pdf

ato.gov.au answers a plain scripted GET with 403; it serves the file to a
browser user agent:

  curl -L -A "Mozilla/5.0" -o bic.pdf <url>

The PDF is ~951KB of binary that changes once a year, so it is deliberately NOT
committed. The generated fixture is.

Safety
------
Nothing is written unless the extraction both parses and validates: every code
must be one of the 582 official BICs, and the pair count must clear a floor. A
layout change that silently matches nothing would otherwise replace a working
index with an empty one, which reads as a search that finds nothing rather than
as a failure.
"""
import json
import os

from django.core.management.base import BaseCommand, CommandError

from core.industry_activities import (
    MIN_EXPECTED_PAIRS,
    ActivityParseError,
    diff_index,
    parse_bic_text,
    validate_against_codes,
)
from core.industry_codes import INDUSTRY_CODE_MAP

DEFAULT_OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "fixtures",
    "ato_industry_activities.json",
)


def extract_pdf_text(path):
    """All text from the PDF, pages joined by newline.

    Isolated so the command's decisions can be tested against text rather than
    a checked-in binary.
    """
    try:
        import pypdf
    except ImportError as exc:  # pragma: no cover - dependency is installed
        raise CommandError("pypdf is required to parse the BIC PDF") from exc
    if not os.path.exists(path):
        raise CommandError(f"no such PDF: {path}")
    reader = pypdf.PdfReader(path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


class Command(BaseCommand):
    help = "Regenerate the ATO BIC activity index from the published PDF."

    def add_arguments(self, parser):
        parser.add_argument("--pdf", required=True, help="Path to the NAT 1827 PDF.")
        parser.add_argument("--out", default=DEFAULT_OUT, help="Fixture to write.")
        parser.add_argument("--dry-run", action="store_true", help="Report only.")
        parser.add_argument(
            "--min-pairs", type=int, default=MIN_EXPECTED_PAIRS,
            help="Refuse to write fewer activity/code pairs than this.",
        )

    def handle(self, *args, **options):
        text = extract_pdf_text(options["pdf"])
        try:
            index = parse_bic_text(text)
            pairs = validate_against_codes(
                index, INDUSTRY_CODE_MAP, min_pairs=options["min_pairs"],
            )
        except ActivityParseError as exc:
            raise CommandError(str(exc)) from exc

        previous = {}
        if os.path.exists(options["out"]):
            with open(options["out"]) as handle:
                previous = json.load(handle)

        changes = diff_index(index, previous)
        added = changes["codes_added"]
        removed = changes["codes_removed"]
        changed = changes["codes_changed"]

        self.stdout.write(
            f"{pairs} activity/code pairs across {len(index)} codes "
            f"(+{len(added)} codes, -{len(removed)}, ~{len(changed)} changed)"
        )
        for code in removed:
            self.stdout.write(f"  removed: {code} {INDUSTRY_CODE_MAP.get(code, '?')}")

        if options["dry_run"]:
            self.stdout.write("dry run -- nothing written")
        else:
            with open(options["out"], "w") as handle:
                json.dump(index, handle, indent=1, sort_keys=True, ensure_ascii=False)
                handle.write("\n")
            self.stdout.write(f"wrote {options['out']}")

        # BaseCommand prints whatever handle() returns, so it must be text.
        return f"{pairs} pairs, {len(index)} codes"
