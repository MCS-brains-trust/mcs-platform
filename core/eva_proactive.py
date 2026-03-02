"""
Eva Proactive Suggestions — Signal-Driven AI Insights
=====================================================
Generates proactive Eva messages when significant events occur,
without requiring the accountant to ask a question first.

Trigger types:
  - risk_flags_raised: HIGH/CRITICAL risk flags detected after risk engine run
  - bank_classification_complete: Bank statement AI classification finished
  - significant_variance: Large variance detected during TB import

Each trigger builds a context-specific prompt, calls the LLM, and stores
the result as an EvaMessage with is_proactive=True.
"""
import logging
from django.utils import timezone

logger = logging.getLogger(__name__)

# Prompt templates for each trigger type
PROMPTS = {
    "risk_flags_raised": """You are Eva, the AI practice intelligence assistant for MC & S Accountants.

The risk engine has just flagged {flag_count} issue(s) for {entity_name} ({year_label}), including {critical_count} critical and {high_count} high severity flags.

Key flags:
{flag_details}

Write a brief, proactive message (3-5 sentences) to the accountant:
1. Summarise what the risk engine found
2. Highlight the most urgent item(s) requiring attention
3. Suggest a specific next step

Keep the tone professional but approachable. Do NOT use bullet points — write in flowing prose.
Start directly with the insight, not with "Hi" or a greeting.""",

    "bank_classification_complete": """You are Eva, the AI practice intelligence assistant for MC & S Accountants.

Bank statement classification has just completed for {entity_name} ({year_label}).

Summary:
- Total transactions: {total_transactions}
- Auto-classified: {auto_classified} ({auto_pct}%)
- Needs review: {needs_review}
- New accounts detected: {new_accounts}

Write a brief, proactive message (3-5 sentences) to the accountant:
1. Summarise the classification results
2. Highlight anything that needs manual attention
3. Suggest the next step (e.g., review unclassified transactions)

Keep the tone professional but approachable. Start directly with the insight.""",

    "significant_variance": """You are Eva, the AI practice intelligence assistant for MC & S Accountants.

A trial balance import for {entity_name} ({year_label}) has revealed significant variances:

{variance_details}

Write a brief, proactive message (3-5 sentences) to the accountant:
1. Summarise the key variances
2. Flag which ones are most likely to need investigation
3. Suggest whether journal entries or client queries might be needed

Keep the tone professional but approachable. Start directly with the insight.""",
}


def generate_proactive_suggestion(financial_year_id, trigger_type, trigger_context):
    """
    Generate a proactive Eva suggestion and store it as an EvaMessage.

    Args:
        financial_year_id: UUID of the FinancialYear
        trigger_type: One of the PROMPTS keys
        trigger_context: Dict with template variables for the prompt

    Returns:
        Dict with message_id and content, or None if generation fails.
    """
    from core.models import FinancialYear, EvaConversation, EvaMessage
    from core.ai_service import _call_llm

    try:
        fy = FinancialYear.objects.select_related("entity").get(pk=financial_year_id)
    except FinancialYear.DoesNotExist:
        logger.error("FY %s not found for proactive suggestion", financial_year_id)
        return None

    # Don't generate proactive suggestions for finalised years
    if fy.status == FinancialYear.Status.FINALISED:
        return None

    # Build the prompt
    prompt_template = PROMPTS.get(trigger_type)
    if not prompt_template:
        logger.error("Unknown trigger type: %s", trigger_type)
        return None

    # Merge standard context with trigger-specific context
    context = {
        "entity_name": fy.entity.entity_name,
        "year_label": fy.year_label,
    }
    context.update(trigger_context)

    try:
        prompt = prompt_template.format(**context)
    except KeyError as e:
        logger.error("Missing context key for proactive prompt: %s", e)
        return None

    # Call the LLM (use haiku/nano for proactive suggestions — cost-efficient)
    try:
        response = _call_llm(
            system_prompt=(
                "You are Eva, an AI practice intelligence assistant for MC & S Accountants. "
                "You are generating a proactive insight based on a system event. "
                "Be concise, specific, and actionable."
            ),
            user_prompt=prompt,
            model_tier="haiku",
        )
        content = response.get("content", "").strip()
        if not content:
            logger.warning("Empty response from LLM for proactive suggestion")
            return None
    except Exception:
        logger.exception("LLM call failed for proactive suggestion")
        return None

    # Get or create a conversation for the system user (proactive messages
    # are stored in a special "system" conversation per FY)
    from django.contrib.auth import get_user_model
    User = get_user_model()

    # Use the first staff user as the conversation owner (proactive messages
    # appear for all users viewing this FY)
    system_user = User.objects.filter(is_staff=True).order_by("pk").first()
    if not system_user:
        logger.error("No staff user found for proactive conversation")
        return None

    conversation, _ = EvaConversation.objects.get_or_create(
        financial_year=fy,
        user=system_user,
    )

    # Create the proactive message
    message = EvaMessage.objects.create(
        conversation=conversation,
        role=EvaMessage.Role.ASSISTANT,
        content=content,
        model_used="haiku",
        is_proactive=True,
        interaction_type=EvaMessage.InteractionType.GENERAL,
        token_count_prompt=response.get("prompt_tokens", 0),
        token_count_response=response.get("completion_tokens", 0),
    )

    conversation.message_count = conversation.messages.count()
    conversation.save(update_fields=["message_count", "last_active_at"])

    logger.info(
        "Proactive suggestion created: %s for FY %s (trigger: %s)",
        message.pk, financial_year_id, trigger_type,
    )

    return {
        "message_id": str(message.pk),
        "content": content,
        "trigger_type": trigger_type,
    }
