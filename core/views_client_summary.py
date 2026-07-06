"""Views for Eva Client Summary — view, regenerate, and download summaries."""
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from config.authorization import get_financial_year_for_user
from core.eva_client_summary import generate_client_summary
from core.models import EvaClientSummary, FinancialYear

logger = logging.getLogger(__name__)


@login_required
def client_summary_view(request, pk):
    """View the client summary for a financial year."""
    fy = get_financial_year_for_user(request, pk)
    summaries = EvaClientSummary.objects.filter(financial_year=fy).order_by("-generated_at")
    latest = summaries.first()

    return render(request, "core/client_summary.html", {
        "fy": fy,
        "entity": fy.entity,
        "summary": latest,
        "all_summaries": summaries,
    })


@login_required
def client_summary_api(request, pk):
    """API: Get the latest client summary for a financial year."""
    fy = get_financial_year_for_user(request, pk)
    summary = EvaClientSummary.objects.filter(financial_year=fy).order_by("-generated_at").first()

    if not summary:
        return JsonResponse({"status": "none", "message": "No summary generated yet."})

    return JsonResponse({
        "status": "ok",
        "summary": {
            "id": str(summary.pk),
            "format_type": summary.format_type,
            "financial_highlights": summary.financial_highlights,
            "compliance_status": summary.compliance_status,
            "tax_position": summary.tax_position,
            "recommendations": summary.recommendations,
            "year_on_year_comparison": summary.year_on_year_comparison,
            "full_content": summary.full_content,
            "version": summary.version,
            "generated_at": summary.generated_at.isoformat(),
        },
    })


@login_required
@require_POST
def client_summary_generate(request, pk):
    """Generate or regenerate a client summary."""
    fy = get_financial_year_for_user(request, pk)
    format_type = request.POST.get("format_type", "bullet")

    try:
        summary = generate_client_summary(fy.pk, format_type)
        if summary:
            return JsonResponse({
                "status": "ok",
                "message": f"Summary v{summary.version} generated successfully.",
                "summary_id": str(summary.pk),
            })
        else:
            return JsonResponse(
                {"status": "error", "error": "Failed to generate summary. Check logs."},
                status=500,
            )
    except Exception as e:
        logger.exception("Client summary generation failed: %s", e)
        return JsonResponse({"status": "error", "error": str(e)}, status=500)
