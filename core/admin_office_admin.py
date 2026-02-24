"""
MCS Platform - Office Admin Model Registration
Register Office Admin models in Django admin for management.
"""
from django.contrib import admin
from .models_office_admin import (
    Correspondence, ASICReturn, NOARecord,
    DebtorRecord, PaymentPlan, DailyTask, DailyTaskCompletion,
)


@admin.register(Correspondence)
class CorrespondenceAdmin(admin.ModelAdmin):
    list_display = ['entity', 'direction', 'correspondence_type', 'status', 'date_received', 'logged_by']
    list_filter = ['direction', 'correspondence_type', 'status', 'date_received']
    search_fields = ['entity__entity_name', 'subject', 'notes']
    date_hierarchy = 'date_received'
    raw_id_fields = ['entity', 'assigned_to', 'logged_by']


@admin.register(ASICReturn)
class ASICReturnAdmin(admin.ModelAdmin):
    list_display = ['entity', 'return_type', 'due_date', 'status', 'amount']
    list_filter = ['return_type', 'status']
    search_fields = ['entity__entity_name']
    date_hierarchy = 'due_date'
    raw_id_fields = ['entity', 'logged_by']


@admin.register(NOARecord)
class NOARecordAdmin(admin.ModelAdmin):
    list_display = ['entity', 'noa_type', 'amount', 'status', 'date_received', 'date_sent']
    list_filter = ['noa_type', 'status']
    search_fields = ['entity__entity_name']
    date_hierarchy = 'date_received'
    raw_id_fields = ['entity', 'logged_by']


@admin.register(DebtorRecord)
class DebtorRecordAdmin(admin.ModelAdmin):
    list_display = ['entity', 'amount_outstanding', 'days_overdue', 'escalation_stage', 'status']
    list_filter = ['status', 'escalation_stage']
    search_fields = ['entity__entity_name', 'invoice_number']
    raw_id_fields = ['entity', 'logged_by']


@admin.register(PaymentPlan)
class PaymentPlanAdmin(admin.ModelAdmin):
    list_display = ['entity', 'total_amount', 'instalment_amount', 'frequency', 'next_payment_date', 'status']
    list_filter = ['status', 'frequency']
    search_fields = ['entity__entity_name']
    raw_id_fields = ['entity']


@admin.register(DailyTask)
class DailyTaskAdmin(admin.ModelAdmin):
    list_display = ['title', 'frequency', 'scheduled_time', 'display_order', 'is_active']
    list_filter = ['frequency', 'is_active']
    list_editable = ['display_order', 'is_active', 'scheduled_time']
    ordering = ['display_order']


@admin.register(DailyTaskCompletion)
class DailyTaskCompletionAdmin(admin.ModelAdmin):
    list_display = ['task', 'completed_by', 'completed_date', 'completed_at']
    list_filter = ['completed_date']
    date_hierarchy = 'completed_date'
    raw_id_fields = ['task', 'completed_by']
