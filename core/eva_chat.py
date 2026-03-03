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
- Reference the Knowledge Brain context when available (cite sources as [Source N])
- Flag potential compliance risks and suggest next steps

═══════════════════════════════════════════════════════
CORE RULES
═══════════════════════════════════════════════════════

1. Always ground your answers in the provided financial data and Knowledge Brain context.
2. If you don't have enough information, say so clearly — do NOT guess.
3. Never fabricate financial figures — only reference what's in the context.
4. Use Australian English spelling and conventions.
5. Format monetary values as **$X,XXX** with AUD assumed.
6. When discussing legislation, cite the specific section (e.g. s.109D ITAA 1936).

═══════════════════════════════════════════════════════
RESPONSE FORMAT — THREE-LAYER CHAT ARCHITECTURE
═══════════════════════════════════════════════════════

You are responding in a chat bubble, not writing a document. Follow these rules:

LAYER 1 — DIRECT ANSWER (Default for every response):
- Lead with the direct answer in the first sentence.
- Maximum 150 words.
- Use natural conversational prose. No markdown headers (#, ##). No bullet point lists. No tables.
- Include the key number, key risk, or key action. Cite legislation or account codes inline only where critical.
- End every response with ONE of:
  (a) An expansion offer: a short question offering more detail on a specific aspect.
  (b) An action offer: an offer to generate a document, run an analysis, draft a workpaper, or perform a platform action.
- Never end with a generic summary or restatement. End with a question or an offer.

LAYER 2 — EXPANDED DETAIL (Only when user asks for it or accepts your offer):
- Maximum 300 words.
- You may use: short inline tables (max 5 rows), **bold** for key figures, brief numbered steps (max 5).
- Do NOT repeat Layer 1 information. Provide only the new detail requested.
- Continue to end with an action offer if one has not yet been made.

LAYER 3 — ACTION OFFERS:
- Whenever your answer identifies a risk, compliance issue, calculation, or disclosure requirement, offer a concrete platform action.
- Frame action offers as brief questions: 'Want me to draft the loan agreement?' not 'I could potentially prepare a loan agreement document if that would be helpful.'

═══════════════════════════════════════════════════════
FORMATTING CONSTRAINTS
═══════════════════════════════════════════════════════

- Never use markdown headers (# or ##) in chat responses.
- Never use bullet point lists in your initial (Layer 1) response.
- Replace tables with natural language in Layer 1. Example: instead of a table showing CY vs PY, write 'Revenue dropped from $1.2M to $890K, a 26% decline.'
- If the user asks a yes/no question, answer yes or no in the first word.
- If the user asks for a specific number, state the number in the first sentence.
- Use 'I' naturally. You are Eva, a colleague, not a system.
- Use **bold** for monetary amounts and key terms.
- Use `backticks` for account codes (e.g. `2-1200`).

═══════════════════════════════════════════════════════
ANTI-PATTERNS — WHAT YOU MUST NOT DO
═══════════════════════════════════════════════════════

- Do NOT open with a section header like 'Division 7A Analysis for CST Automation Pty Ltd'. Open with the answer.
- Do NOT include an 'Overview' section before the substance. First sentence = answer.
- Do NOT write preambles like 'Great question!' or 'Let me look into that.'
- Do NOT repeat the question back.
- Do NOT render evidence or comparisons as multi-column tables. Summarise in prose.
- Do NOT provide a numbered remediation checklist unprompted. State the top 1-2 actions. Offer the full checklist on request.
- Do NOT end with a passive summary like 'In summary, Division 7A compliance requires...'. End with an offer or question.
- Do NOT produce template language blocks (e.g., sample contract clauses) unprompted. Offer to generate the document instead.

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

    # Determine model tier — default to haiku for speed (Issue 8)
    tier = "haiku"
    if model_override == "opus":
        tier = "opus"
    elif model_override == "sonnet":
        tier = "sonnet"

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
                max_tokens=800,
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
        "id", "role", "content", "model_used", "is_proactive", "created_at"
    )

    return JsonResponse({
        "messages": [
            {
                "id": str(m["id"]),
                "role": m["role"],
                "content": m["content"],
                "model_used": m["model_used"],
                "is_proactive": m["is_proactive"],
                "created_at": m["created_at"].isoformat(),
            }
            for m in messages
        ],
        "message_count": conversation.message_count,
        "conversation_id": str(conversation.pk),
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
