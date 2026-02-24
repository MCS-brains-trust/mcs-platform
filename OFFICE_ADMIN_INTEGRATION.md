# Office Admin Dashboard — Integration Guide

## Overview

This document explains how to integrate the new Office Admin dashboard into the existing StatementHub Django project. The implementation adds a complete reception/office admin experience with its own sidebar navigation, models, views, and templates.

## Files Added

### Models
| File | Purpose |
|------|---------|
| `core/models_office_admin.py` | 7 new models: Correspondence, ASICReturn, NOARecord, DebtorRecord, PaymentPlan, DailyTask, DailyTaskCompletion |

### Views
| File | Purpose |
|------|---------|
| `core/views_office_admin.py` | Dashboard + 15 views for correspondence, ASIC/ATO, debtors, and task management |

### URLs
| File | Purpose |
|------|---------|
| `core/urls_office_admin.py` | URL routing for all Office Admin pages under `/admin/` prefix |

### Templates
| File | Purpose |
|------|---------|
| `templates/office_admin/base.html` | Base template with Office Admin sidebar navigation |
| `templates/office_admin/dashboard.html` | Main dashboard with stat cards, tasks, correspondence, ASIC/ATO, debtors |
| `templates/office_admin/correspondence_list.html` | Correspondence list with filtering |
| `templates/office_admin/correspondence_form.html` | Log new correspondence |
| `templates/office_admin/noa_list.html` | NOA tracker list |
| `templates/office_admin/asic_list.html` | ASIC returns list (also used for burning list) |
| `templates/office_admin/company_register.html` | Company register for ASIC tracking |
| `templates/office_admin/debtors_list.html` | Debtors list (aged receivables, overdue, statements sent) |
| `templates/office_admin/payment_plans.html` | Payment plans list |

### Admin
| File | Purpose |
|------|---------|
| `core/admin_office_admin.py` | Django admin registration for all Office Admin models |

### Context Processors
| File | Purpose |
|------|---------|
| `core/context_processors.py` | Injects sidebar badge counts for Office Admin users |

### Migrations
| File | Purpose |
|------|---------|
| `core/migrations/0002_office_admin.py` | Creates all 7 new database tables |
| `core/migrations/0003_seed_daily_tasks.py` | Seeds 15 default daily/weekly/monthly tasks |

---

## Integration Steps

### 1. Add URL routing to the main `urls.py`

In your project's main `config/urls.py` (or wherever the root URL conf is), add:

```python
from django.urls import path, include

urlpatterns = [
    # ... existing patterns ...
    path("admin/", include("core.urls_office_admin")),
]
```

### 2. Add context processor to settings

In `config/settings.py`, add the context processor to `TEMPLATES`:

```python
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                # ... existing processors ...
                'core.context_processors.office_admin_context',
            ],
        },
    },
]
```

### 3. Register admin models

In `core/admin.py`, add at the bottom:

```python
# Import Office Admin admin registrations
from . import admin_office_admin  # noqa: F401
```

### 4. Add role field to User model (if not already present)

The system checks for a `role` field on the User model OR membership in an "Office Admin" group. If using the group-based approach, create the group in Django admin:

```python
# Option A: Add to your custom User model
class User(AbstractUser):
    class UserRole(models.TextChoices):
        ADMIN = 'admin', 'Administrator'
        ACCOUNTANT = 'accountant', 'Accountant'
        OFFICE_ADMIN = 'office_admin', 'Office Admin'
        RECEPTION = 'reception', 'Reception'
    
    role = models.CharField(max_length=20, choices=UserRole.choices, default=UserRole.ACCOUNTANT)

# Option B: Use Django groups
# Create a group called "Office Admin" in Django admin and add Eliza to it
```

### 5. Add role-based dashboard redirect

In the main `dashboard` view in `core/views.py`, add a redirect at the top:

```python
@login_required
def dashboard(request):
    # Redirect Office Admin users to their dashboard
    if hasattr(request.user, 'role') and request.user.role in ('office_admin', 'reception'):
        return redirect('office_admin:dashboard')
    if request.user.groups.filter(name__in=['Office Admin', 'Reception']).exists():
        return redirect('office_admin:dashboard')
    
    # ... existing accountant dashboard code ...
```

### 6. Run migrations

```bash
python manage.py migrate
```

This will:
- Create 7 new database tables
- Seed 15 default daily tasks for the checklist

### 7. Update the existing base template sidebar (optional)

If you want accountants to also see a link to the Office Admin area (for admin users who need both views), add to the existing sidebar:

```html
{% if is_office_admin %}
<a href="{% url 'office_admin:dashboard' %}">
    <i class="bi bi-grid-1x2-fill"></i> Office Admin
</a>
{% endif %}
```

---

## Sidebar Navigation Structure

The Office Admin sidebar replaces the accountant sidebar entirely for users with the `office_admin` or `reception` role:

```
MAIN
├── Dashboard
└── Entity Hub

CLIENT CORRESPONDENCE
├── Incoming Mail (badge: new today)
├── Outgoing Mail
├── Awaiting Reply (badge: count)
└── Documents In

ASIC / ATO
├── NOA Tracker
├── ASIC Returns (badge: burning count)
├── Burning List
└── Company Register

DEBTORS
├── Aged Receivables
├── Statements Sent
├── Overdue (badge: count)
└── Payment Plans
```

---

## Dashboard Layout

The dashboard displays:

1. **4 Stat Cards** — Correspondence, ASIC/ATO, Debtors, Today's Tasks
2. **Today's Tasks** — Interactive checklist with AJAX toggle (daily/weekly/monthly)
3. **Recent Correspondence** — Last 10 items with status badges
4. **ASIC/ATO Action Required** — Burning items + NOAs to send
5. **Debtors Overdue** — Top 10 overdue with escalation tracking
6. **Recently Viewed Entities** — Quick access to entity hub

---

## Data Model Summary

| Model | Purpose | Key Fields |
|-------|---------|------------|
| `Correspondence` | All incoming/outgoing mail | entity, direction, type, status, date |
| `ASICReturn` | ASIC annual returns & renewals | entity, return_type, due_date, status |
| `NOARecord` | ATO Notices of Assessment | entity, noa_type, amount, status |
| `DebtorRecord` | Outstanding debtor balances | entity, amount, days_overdue, escalation_stage |
| `PaymentPlan` | Active payment arrangements | entity, instalment_amount, frequency, next_date |
| `DailyTask` | Recurring task definitions | title, frequency, scheduled_time, display_order |
| `DailyTaskCompletion` | Task completion records | task, completed_by, completed_date |

All models use UUID primary keys consistent with the existing codebase.
