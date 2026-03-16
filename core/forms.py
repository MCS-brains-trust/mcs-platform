"""MCS Platform - Core Forms"""
from django import forms
from .models import (
    Client, Entity, FinancialYear, AccountMapping,
    AdjustingJournal, JournalLine, ClientAccountMapping,
    EntityOfficer, ClientAssociate, AccountingSoftware, MeetingNote,
    CryptoPortfolio,
)


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ("name", "contact_email", "assigned_accountant", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "form-check-input"
            else:
                field.widget.attrs["class"] = "form-control"


class EntityForm(forms.ModelForm):
    industry = forms.ChoiceField(
        required=True,
        help_text="ATO Business Industry Code (NAT 1827).",
    )

    class Meta:
        model = Entity
        fields = (
            "entity_name", "trading_as", "entity_type", "industry", "abn", "acn", "tfn",
            "reporting_framework", "company_size", "show_cents",
            "is_small_business_entity", "is_base_rate_entity",
            "is_gst_registered", "bas_frequency",
            "contact_email",
            "address_line_1", "address_line_2", "suburb", "state", "postcode", "country",
            "assigned_accountant",
        )
        widgets = {
            "tfn": forms.TextInput(attrs={
                "autocomplete": "off",
                "inputmode": "numeric",
                "maxlength": "9",
                "placeholder": "9 digit TFN",
            }),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from core.industry_codes import INDUSTRY_CHOICES
        # Set up industry choices with grouped optgroups
        self.fields["industry"].choices = [("", "— Select industry —")] + INDUSTRY_CHOICES

        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "form-check-input"
            else:
                field.widget.attrs["class"] = "form-control"

        # EncryptedCharField inherits from TextField, so force single-line input
        self.fields["tfn"].widget = forms.TextInput(attrs={
            "class": "form-control",
            "autocomplete": "off",
            "inputmode": "numeric",
            "maxlength": "9",
            "placeholder": "9 digit TFN",
        })

        # Searchable select for industry
        self.fields["industry"].widget.attrs.update({
            "class": "form-select",
            "id": "id_industry",
        })

        # ── Compulsory fields ─────────────────────────────────────────
        self.fields["abn"].required = True
        self.fields["tfn"].required = True
        self.fields["contact_email"].required = True
        self.fields["address_line_1"].required = True
        self.fields["suburb"].required = True
        self.fields["state"].required = True
        self.fields["postcode"].required = True
        self.fields["bas_frequency"].required = True

        # Tax classification fields — Yes/No selects for companies
        for bool_field in ("is_small_business_entity", "is_base_rate_entity"):
            self.fields[bool_field].widget = forms.Select(
                choices=[("", "— Select —"), ("true", "Yes"), ("false", "No")],
                attrs={"class": "form-select"},
            )
            # Pre-populate from instance value
            inst_val = getattr(self.instance, bool_field, None)
            if inst_val is True:
                self.initial[bool_field] = "true"
            elif inst_val is False:
                self.initial[bool_field] = "false"
            else:
                self.initial[bool_field] = ""

        # Only senior users can change assigned_accountant
        if user and not user.is_senior:
            self.fields.pop("assigned_accountant", None)

    def clean(self):
        cleaned = super().clean()
        entity_type = cleaned.get("entity_type", "")
        acn = cleaned.get("acn", "")
        # ACN is compulsory for companies
        if entity_type == "company" and not acn:
            self.add_error("acn", "ACN is required for companies.")
        # Tax classification fields — convert string to bool, required for companies
        for bool_field in ("is_small_business_entity", "is_base_rate_entity"):
            raw = self.data.get(bool_field, "")
            if entity_type == "company":
                if raw == "":
                    self.add_error(bool_field, "This field is required for companies.")
                else:
                    cleaned[bool_field] = raw == "true"
            else:
                cleaned[bool_field] = None
        return cleaned

    def save(self, commit=True):
        entity = super().save(commit=False)
        # Keep primary_accountant in sync with assigned_accountant
        if entity.assigned_accountant and not entity.primary_accountant:
            entity.primary_accountant = entity.assigned_accountant
        if commit:
            entity.save()
        return entity


class FinancialYearForm(forms.ModelForm):
    class Meta:
        model = FinancialYear
        fields = ("year_label", "period_type", "start_date", "end_date")
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"


class TrialBalanceUploadForm(forms.Form):
    file = forms.FileField(
        help_text="Upload a .xlsx file with columns: Account Code, Account Name, Opening Balance, Debit, Credit",
        widget=forms.FileInput(attrs={"class": "form-control", "accept": ".xlsx"}),
    )


class CryptoPortfolioForm(forms.ModelForm):
    class Meta:
        model = CryptoPortfolio
        fields = ("name", "exchange", "base_currency", "notes")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs["class"] = "form-control"
                field.widget.attrs.setdefault("rows", 3)
            else:
                field.widget.attrs["class"] = "form-control"


class CryptoTradeImportForm(forms.Form):
    portfolio_name = forms.CharField(
        required=False,
        max_length=255,
        help_text="Optional. If no existing portfolio is selected, a new one will be created with this name.",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "My Crypto Portfolio"}),
    )
    portfolio = forms.ModelChoiceField(
        queryset=CryptoPortfolio.objects.none(),
        required=False,
        empty_label="Create new portfolio",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    file = forms.FileField(
        help_text="Upload a Bybit spot trade history CSV file.",
        widget=forms.FileInput(attrs={"class": "form-control", "accept": ".csv,text/csv"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user and getattr(user, "is_authenticated", False):
            self.fields["portfolio"].queryset = CryptoPortfolio.objects.filter(owner=user, is_active=True)
        self.user = user

    def clean(self):
        cleaned = super().clean()
        portfolio = cleaned.get("portfolio")
        portfolio_name = (cleaned.get("portfolio_name") or "").strip()
        if not portfolio and not portfolio_name:
            self.add_error("portfolio_name", "Provide a portfolio name or choose an existing portfolio.")
        return cleaned


class AccountMappingForm(forms.ModelForm):
    class Meta:
        model = AccountMapping
        fields = (
            "standard_code", "line_item_label", "financial_statement",
            "statement_section", "display_order", "applicable_entities",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"


class ClientAccountMappingForm(forms.Form):
    """Form for mapping a single client account to a standard line item."""
    mapped_line_item = forms.ModelChoiceField(
        queryset=AccountMapping.objects.all(),
        required=False,
        empty_label="-- Select mapping --",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )


# ---------------------------------------------------------------------------
# Enhanced Journal Entry Forms
# ---------------------------------------------------------------------------
class AdjustingJournalForm(forms.ModelForm):
    class Meta:
        model = AdjustingJournal
        fields = ("journal_type", "journal_date", "description", "narration")
        widgets = {
            "journal_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.TextInput(attrs={"placeholder": "Brief description of the journal entry"}),
            "narration": forms.Textarea(attrs={"rows": 2, "placeholder": "Additional notes for audit trail (optional)"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"
        self.fields["narration"].required = False


class JournalLineForm(forms.ModelForm):
    """Enhanced journal line form with account picker support."""

    # Hidden field for account selection via JavaScript
    account_select = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )

    class Meta:
        model = JournalLine
        fields = ("account_code", "account_name", "description", "debit", "credit")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control form-control-sm"
        self.fields["description"].required = False
        self.fields["description"].widget.attrs["placeholder"] = "Line description (optional)"
        # Use text inputs for debit/credit so we can show comma formatting
        self.fields["debit"].widget = forms.TextInput(attrs={
            "class": "form-control form-control-sm dr-cr-field",
            "inputmode": "decimal",
        })
        self.fields["credit"].widget = forms.TextInput(attrs={
            "class": "form-control form-control-sm dr-cr-field",
            "inputmode": "decimal",
        })

    def _is_truly_empty(self):
        """Check if this form row is truly empty (no account code and no amounts)."""
        code = self.data.get(self.add_prefix('account_code'), '').strip()
        debit_str = self.data.get(self.add_prefix('debit'), '').strip()
        credit_str = self.data.get(self.add_prefix('credit'), '').strip()
        # Parse debit/credit, treating empty or "0" as zero
        from decimal import Decimal, InvalidOperation
        try:
            debit_val = Decimal(debit_str.replace(',', '') or '0')
        except (InvalidOperation, ValueError):
            debit_val = Decimal('0')
        try:
            credit_val = Decimal(credit_str.replace(',', '') or '0')
        except (InvalidOperation, ValueError):
            credit_val = Decimal('0')
        return not code and debit_val == 0 and credit_val == 0

    def has_changed(self):
        """Override to treat rows with no account code and zero amounts as unchanged."""
        if self._is_truly_empty():
            return False
        return super().has_changed()

    def clean_debit(self):
        """Strip commas from debit value before validation."""
        val = self.data.get(self.add_prefix('debit'), '0')
        if isinstance(val, str):
            val = val.replace(',', '')
        from decimal import Decimal, InvalidOperation
        try:
            return Decimal(val or '0')
        except InvalidOperation:
            raise forms.ValidationError('Enter a valid number.')

    def clean_credit(self):
        """Strip commas from credit value before validation."""
        val = self.data.get(self.add_prefix('credit'), '0')
        if isinstance(val, str):
            val = val.replace(',', '')
        from decimal import Decimal, InvalidOperation
        try:
            return Decimal(val or '0')
        except InvalidOperation:
            raise forms.ValidationError('Enter a valid number.')


JournalLineFormSet = forms.inlineformset_factory(
    AdjustingJournal,
    JournalLine,
    form=JournalLineForm,
    extra=4,
    can_delete=True,
)


# ---------------------------------------------------------------------------
# Entity Officer Forms
# ---------------------------------------------------------------------------
class EntityOfficerForm(forms.ModelForm):
    class Meta:
        model = EntityOfficer
        fields = (
            "full_name", "role", "title", "date_appointed", "date_ceased",
            "is_signatory", "is_chairperson", "display_order", "profit_share_percentage",
            "distribution_percentage",
        )
        widgets = {
            "date_appointed": forms.DateInput(attrs={"type": "date"}),
            "date_ceased": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, entity_type=None, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "form-check-input"
            else:
                field.widget.attrs["class"] = "form-control"

        # Filter role choices based on entity type
        if entity_type:
            role_map = {
                "company": [
                    EntityOfficer.OfficerRole.DIRECTOR,
                    EntityOfficer.OfficerRole.SECRETARY,
                    EntityOfficer.OfficerRole.PUBLIC_OFFICER,
                ],
                "trust": [
                    EntityOfficer.OfficerRole.TRUSTEE,
                    EntityOfficer.OfficerRole.BENEFICIARY,
                    EntityOfficer.OfficerRole.DIRECTOR,  # directors of trustee company
                ],
                "partnership": [
                    EntityOfficer.OfficerRole.PARTNER,
                ],
                "sole_trader": [
                    EntityOfficer.OfficerRole.SOLE_TRADER,
                ],
                "smsf": [
                    EntityOfficer.OfficerRole.TRUSTEE,
                    EntityOfficer.OfficerRole.DIRECTOR,  # corporate trustee directors
                ],
            }
            allowed_roles = role_map.get(entity_type, EntityOfficer.OfficerRole.choices)
            self.fields["role"].choices = [
                (r.value, r.label) for r in allowed_roles
            ]

        # Show/hide partnership and trust specific fields
        if entity_type != "partnership":
            self.fields["profit_share_percentage"].widget = forms.HiddenInput()
        if entity_type != "trust":
            self.fields["distribution_percentage"].widget = forms.HiddenInput()


# ---------------------------------------------------------------------------
# Client Associate Forms
# ---------------------------------------------------------------------------
class ClientAssociateForm(forms.ModelForm):
    class Meta:
        model = ClientAssociate
        fields = (
            "name", "relationship_type", "date_of_birth", "email", "phone",
            "occupation", "employer", "abn", "tfn_last_three",
            "related_entity", "notes", "is_active",
        )
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "form-check-input"
            else:
                field.widget.attrs["class"] = "form-control"
        # Make select fields use form-select
        self.fields["relationship_type"].widget.attrs["class"] = "form-select"
        self.fields["related_entity"].widget.attrs["class"] = "form-select"
        self.fields["related_entity"].required = False
        # Remove related_client field (no longer used)
        if "related_client" in self.fields:
            del self.fields["related_client"]


# ---------------------------------------------------------------------------
# Accounting Software Forms
# ---------------------------------------------------------------------------
class AccountingSoftwareForm(forms.ModelForm):
    class Meta:
        model = AccountingSoftware
        fields = (
            "software_type", "software_version", "is_cloud",
            "login_email", "organisation_name", "has_advisor_access",
            "advisor_login_email", "subscription_level", "notes", "is_primary",
        )
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, client=None, entity=None, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "form-check-input"
            else:
                field.widget.attrs["class"] = "form-control"
        self.fields["software_type"].widget.attrs["class"] = "form-select"
        # Remove entity field from the form since it's set automatically
        if "entity" in self.fields:
            del self.fields["entity"]


# ---------------------------------------------------------------------------
# Meeting Note Forms
# ---------------------------------------------------------------------------
class MeetingNoteForm(forms.ModelForm):
    class Meta:
        model = MeetingNote
        fields = (
            "title", "meeting_date", "meeting_type", "attendees",
            "discussion_points", "action_items", "notes",
            "follow_up_date", "follow_up_completed", "is_pinned", "tags",
        )
        widgets = {
            "meeting_date": forms.DateInput(attrs={"type": "date"}),
            "follow_up_date": forms.DateInput(attrs={"type": "date"}),
            "discussion_points": forms.Textarea(attrs={"rows": 5, "placeholder": "Key topics discussed..."}),
            "action_items": forms.Textarea(attrs={"rows": 4, "placeholder": "Action items and follow-ups..."}),
            "notes": forms.Textarea(attrs={"rows": 4, "placeholder": "General notes and observations..."}),
            "attendees": forms.TextInput(attrs={"placeholder": "e.g. Elio Scarton, John Smith"}),
            "tags": forms.TextInput(attrs={"placeholder": "e.g. tax-planning, smsf, urgent"}),
        }

    def __init__(self, *args, client=None, entity=None, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "form-check-input"
            else:
                field.widget.attrs["class"] = "form-control"
        self.fields["meeting_type"].widget.attrs["class"] = "form-select"
        # Remove entity field from the form since it's set automatically
        if "entity" in self.fields:
            del self.fields["entity"]
