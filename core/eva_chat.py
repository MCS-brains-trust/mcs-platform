"""
Eva Chat Interface — Views, Context Payload Builder, RAG Retrieval

Endpoints:
- POST /api/financial-years/{id}/eva-chat/ — send a chat message
- GET  /api/financial-years/{id}/eva-chat/ — get conversation history
"""
import json
import logging
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------
EVA_SYSTEM_PROMPT = """You are Eva, the AI Practice Intelligence assistant for MC & S Accountants.
You are embedded in StatementHub, the firm's financial statement preparation platform.

Your role:
- Help accountants with questions about the entity they are working on
- Provide guidance on Australian tax law, accounting standards (AASB), and ATO compliance
- Reference the Knowledge Brain context when available (cite sources)
- Flag potential compliance risks and suggest next steps
- Be concise, professional, and practical

Important rules:
1. Always ground your answers in the provided financial data and Knowledge Brain context
2. When citing Knowledge Brain sources, reference them as [Source N]
3. If you don't have enough information, say so clearly
4. Never fabricate financial figures — only reference what's in the context
5. For complex tax questions, recommend the accountant verify with the managing director
6. Use Australian English spelling and conventions
7. Format monetary values as $X,XXX with AUD assumed
8. When discussing legislation, cite the specific section (e.g. s.109D ITAA 1936)

Entity context and financial data will be provided with each message.
"""


# ---------------------------------------------------------------------------
# Context Payload Builder
# ---------------------------------------------------------------------------
def _decimal_to_str(val):
    """Convert Decimal to string for JSON serialisation."""
    if isinstance(val, Decimal):
        return str(val)
    return val


def build_context_payload(financial_year):
    """
    Build the full context payload for Eva's chat, including:
    - Entity details (type, name, ABN, FY period)
    - Full trial balance with CY/PY balances
    - Posted adjusting journals
    - Directors/trustees/beneficiaries
    - Open/resolved Eva findings
    - Open amber indicators

    Returns:
        Formatted context string
    """
    from core.models import (
        AdjustingJournal, EvaFinding, EvaReview,
    )

    fy = financial_year
    entity = fy.entity

    sections = []

    # ── Entity Information ────────────────────────────────────────────
    sections.append(f"""=== ENTITY INFORMATION ===
Name: {entity.entity_name}
Type: {entity.get_entity_type_display()}
ABN: {entity.abn or 'Not recorded'}
GST Registered: {'Yes' if entity.is_gst_registered else 'No'}
Financial Year: {fy.year_label} ({fy.start_date} to {fy.end_date})
Status: {fy.get_status_display()}
""")

    # ── Trial Balance ─────────────────────────────────────────────────
    tb_lines = fy.trial_balance_lines.select_related(
        "mapped_line_item"
    ).order_by("account_code")

    if tb_lines.exists():
        tb_text = ["=== TRIAL BALANCE ==="]
        tb_text.append(f"{'Code':<8} {'Account Name':<40} {'CY Debit':>14} {'CY Credit':>14} {'PY Debit':>14} {'PY Credit':>14}")
        tb_text.append("-" * 110)

        for line in tb_lines:
            mapped = ""
            if line.mapped_line_item:
                mapped = f" [{line.mapped_line_item.standard_code}]"
            tb_text.append(
                f"{line.account_code:<8} "
                f"{(line.account_name or '')[:38]:<40} "
                f"{_decimal_to_str(line.debit):>14} "
                f"{_decimal_to_str(line.credit):>14} "
                f"{_decimal_to_str(line.prior_debit):>14} "
                f"{_decimal_to_str(line.prior_credit):>14}"
                f"{mapped}"
            )

        sections.append("\n".join(tb_text))

    # ── Adjusting Journals ────────────────────────────────────────────
    journals = AdjustingJournal.objects.filter(
        financial_year=fy,
        status="posted",
    ).prefetch_related("lines")

    if journals.exists():
        jnl_text = ["=== POSTED ADJUSTING JOURNALS ==="]
        for jnl in journals:
            jnl_text.append(
                f"\nJournal: {jnl.reference_number or 'Draft'} — {jnl.description}"
            )
            jnl_text.append(f"Date: {jnl.journal_date or 'Not set'}")
            for line in jnl.lines.all():
                jnl_text.append(
                    f"  {line.account_code} {line.account_name}: "
                    f"DR {_decimal_to_str(line.debit)} / CR {_decimal_to_str(line.credit)}"
                    f" — {line.description or ''}"
                )
        sections.append("\n".join(jnl_text))

    # ── Officers (Directors/Trustees/Beneficiaries) ────────────────────
    try:
        from core.models import EntityOfficer
        officers = entity.officers.filter(date_ceased__isnull=True).order_by("display_order")
        if officers.exists():
            officer_text = ["=== OFFICERS (Directors/Trustees/Partners/Beneficiaries) ==="]
            for o in officers:
                role = o.get_role_display()
                title_str = f" — {o.title}" if o.title else ""
                appointed = f", appointed {o.date_appointed}" if o.date_appointed else ""
                shares = f", {o.shares_held} shares" if o.shares_held else ""
                signatory = " [Signatory]" if o.is_signatory else ""
                officer_text.append(
                    f"- {o.full_name} ({role}{title_str}{appointed}{shares}{signatory})"
                )
            sections.append("\n".join(officer_text))
    except Exception as e:
        logger.warning(f"Eva context: failed to load officers: {e}")

    # ── Eva Findings ──────────────────────────────────────────────────
    latest_review = EvaReview.objects.filter(
        financial_year=fy
    ).order_by("-triggered_at").first()

    if latest_review:
        findings = latest_review.findings.all()
        if findings.exists():
            find_text = ["=== EVA FINDINGS ==="]
            for f in findings:
                find_text.append(
                    f"\n[{f.get_severity_display()}] {f.title or f.check_name} — "
                    f"Status: {f.get_status_display()}"
                )
                find_text.append(f"  Explanation: {f.plain_english_explanation}")
                if f.recommendation:
                    find_text.append(f"  Recommendation: {f.recommendation}")
                if f.resolution_note:
                    find_text.append(f"  Resolution: {f.resolution_note}")
            sections.append("\n".join(find_text))

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Chat API Endpoints
# ---------------------------------------------------------------------------
@login_required
@require_POST
def eva_chat_send(request, pk):
    """
    Send a message to Eva and get a streaming response via SSE.

    POST /api/financial-years/<pk>/eva-chat/
    Body: {"message": "...", "model_override": "opus"}  (model_override optional)

    Returns: text/event-stream with token chunks, then a final [DONE] event
    containing the message_id and sources.
    """
    from django.http import StreamingHttpResponse
    from core.models import (
        EvaConversation, EvaMessage, FinancialYear, ActivityLog,
    )
    from core.ai_service import _call_llm_stream
    from core.eva_knowledge import retrieve_relevant_chunks, format_rag_context

    try:
        fy = FinancialYear.objects.select_related("entity").get(pk=pk)
    except FinancialYear.DoesNotExist:
        return JsonResponse({"error": "Financial year not found"}, status=404)

    # Parse request body
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    user_message = body.get("message", "").strip()
    if not user_message:
        return JsonResponse({"error": "Message is required"}, status=400)

    model_override = body.get("model_override", "")

    # Determine model tier
    tier = "sonnet"
    if model_override == "opus":
        tier = "opus"

    # Get or create conversation for this FY + user
    conversation, created = EvaConversation.objects.get_or_create(
        financial_year=fy,
        user=request.user,
    )

    # Save user message
    user_msg = EvaMessage.objects.create(
        conversation=conversation,
        role="user",
        content=user_message,
    )
    conversation.message_count = conversation.messages.count()
    conversation.save(update_fields=["message_count", "last_active_at"])

    # Build context
    entity_context = build_context_payload(fy)

    # RAG retrieval
    rag_chunks = retrieve_relevant_chunks(user_message, top_k=8)
    rag_context = format_rag_context(rag_chunks)
    retrieved_ids = [item["chunk_id"] for item in rag_chunks]

    # Build conversation history (last 10 messages for context window)
    history_msgs = list(
        conversation.messages.order_by("-created_at")[:10]
    )
    history_msgs.reverse()

    # Build the user prompt with context + history + current message
    history_text = ""
    if len(history_msgs) > 1:  # More than just the current message
        history_lines = []
        for msg in history_msgs[:-1]:  # Exclude current message
            role_label = "Accountant" if msg.role == "user" else "Eva"
            history_lines.append(f"{role_label}: {msg.content}")
        history_text = (
            "\n=== CONVERSATION HISTORY ===\n"
            + "\n".join(history_lines)
            + "\n"
        )

    user_prompt = f"""{entity_context}

{rag_context}

{history_text}

=== CURRENT QUESTION ===
{user_message}
"""

    # Capture variables needed in the generator closure
    _fy = fy
    _conversation = conversation
    _tier = tier
    _retrieved_ids = retrieved_ids
    _rag_chunks = rag_chunks
    _request_user = request.user

    def stream_response():
        full_text = []
        try:
            for chunk in _call_llm_stream(
                system_prompt=EVA_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                tier=_tier,
                temperature=0.3,
                max_tokens=2000,
            ):
                full_text.append(chunk)
                # SSE format: data: <json>\n\n
                yield f"data: {json.dumps({'token': chunk})}\n\n"
        except Exception as e:
            logger.error(f"Eva chat LLM stream error: {e}")
            error_msg = "I'm sorry, I encountered an error. Please try again."
            full_text.append(error_msg)
            yield f"data: {json.dumps({'token': error_msg})}\n\n"

        # Save the complete response to DB
        response_text = "".join(full_text)
        try:
            assistant_msg = EvaMessage.objects.create(
                conversation=_conversation,
                role="assistant",
                content=response_text,
                model_used=_tier,
                retrieved_chunk_ids=_retrieved_ids,
            )
            _conversation.message_count = _conversation.messages.count()
            _conversation.save(update_fields=["message_count", "last_active_at"])

            # Send final event with metadata
            sources = [
                {
                    "title": item["document_title"],
                    "category": item["category"],
                    "score": round(item["score"], 2),
                }
                for item in _rag_chunks[:3]
            ]
            yield f"data: {json.dumps({'done': True, 'message_id': str(assistant_msg.pk), 'sources': sources})}\n\n"
        except Exception as e:
            logger.error(f"Eva chat save error: {e}")
            yield f"data: {json.dumps({'done': True, 'message_id': '', 'sources': []})}\n\n"

        # Log activity (non-blocking)
        try:
            ActivityLog.objects.create(
                user=_request_user,
                event_type="eva_chat",
                title=f"Eva Chat — {_fy.entity.entity_name}",
                description=f"Chat message sent in {_fy.year_label}. Model: {_tier}.",
                entity=_fy.entity,
                financial_year=_fy,
                url=f"/entities/years/{_fy.pk}/",
            )
        except Exception:
            pass

    response = StreamingHttpResponse(
        stream_response(),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"  # Disable nginx buffering
    return response


@login_required
@require_GET
def eva_chat_history(request, pk):
    """
    Get conversation history for the current user and financial year.

    GET /api/financial-years/<pk>/eva-chat/
    """
    from core.models import EvaConversation, FinancialYear

    try:
        fy = FinancialYear.objects.get(pk=pk)
    except FinancialYear.DoesNotExist:
        return JsonResponse({"error": "Financial year not found"}, status=404)

    conversation = EvaConversation.objects.filter(
        financial_year=fy,
        user=request.user,
    ).first()

    if not conversation:
        return JsonResponse({"messages": [], "message_count": 0})

    messages = conversation.messages.order_by("created_at").values(
        "id", "role", "content", "model_used", "created_at"
    )

    return JsonResponse({
        "messages": [
            {
                "id": str(m["id"]),
                "role": m["role"],
                "content": m["content"],
                "model_used": m["model_used"],
                "created_at": m["created_at"].isoformat(),
            }
            for m in messages
        ],
        "message_count": conversation.message_count,
    })


# ---------------------------------------------------------------------------
# Dispatch — routes GET/POST to the correct handler
# ---------------------------------------------------------------------------
@login_required
def eva_chat_dispatch(request, pk):
    """
    Dispatch GET/POST to the appropriate chat handler.
    GET  → conversation history
    POST → send message
    """
    if request.method == "POST":
        return eva_chat_send(request, pk)
    else:
        return eva_chat_history(request, pk)
