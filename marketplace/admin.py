"""
admin.py — the ONLY file that may import PrivateReview.
Private reviews are labelled clearly as dispute records.
"""
from datetime import datetime
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q
from django.db.models.functions import Greatest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone

from .constants import TOWN_CHOICES
from .models import (
    MarketListing,
    MarketOrder,
    Message,
    PlatformCircumventionCase,
    PlatformFee,
    PlatformNotice,
    PlatformSettings,
    PromoCode,
    PrivateReview,   # ← only imported here
    PublicReview,
    Quote,
    QuotingAppointment,
    QuotingAppointmentSlot,
    Sponsor,
    SupplyCategory,
    SupplierProfile,
    SupplierEnquiry,
    SupplierQuote,
    SupplierMessage,
    Task,
    TaskPhoto,
    TermsAcceptance,
    TradeCategory,
    TradieProfile,
    Invoice,
    InvoiceLine,
    InvoiceNotification,
    User,
)
from .utils import (
    build_invoice_line_description,
    create_invoice_with_lines,
    fee_rule_label,
    get_active_platform_settings,
    get_eligible_platform_fees,
    get_providers_with_pending_fees,
    notify_client_migrated_to_tradie,
    send_invoice_notifications,
)


# ── User ──────────────────────────────────────────────────────────────────────

class HasAcceptedTermsFilter(admin.SimpleListFilter):
    """Surfaces accounts with zero TermsAcceptance records — either a
    legacy account that registered before that tracking existed (see
    migration 0010), or one created directly via this admin's "Add user"
    button, which (unlike the public registration forms) has no terms
    checkbox and never writes this record. Both cases end up looking the
    same here; there's no way to tell them apart from this data alone."""
    title = 'accepted terms'
    parameter_name = 'accepted_terms'

    def lookups(self, request, model_admin):
        return (('yes', 'Accepted'), ('no', 'Not accepted / no record'))

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(has_accepted_terms=True)
        if self.value() == 'no':
            return queryset.filter(has_accepted_terms=False)
        return queryset


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ['email']
    list_display = ['email', 'first_name', 'last_name', 'role', 'town', 'is_tester', 'is_market_founding_member', 'is_active', 'date_joined', 'accepted_terms_display', 'migrate_link']
    list_filter  = ['role', 'town', 'is_tester', 'is_market_founding_member', 'is_active', HasAcceptedTermsFilter]
    search_fields = ['email', 'first_name', 'last_name']
    actions = ['grant_tester_access', 'revoke_tester_access']
    fieldsets = (
        (None,           {'fields': ('email', 'password')}),
        ('Personal',     {'fields': ('first_name', 'last_name', 'mobile', 'town', 'role')}),
        ('Beta Access',  {'fields': ('is_tester',), 'description': 'Testers can preview features gated behind an unlaunched-feature flag (e.g. supplier accounts) before general availability.'}),
        ('Market Founding Seller', {'fields': ('is_market_founding_member', 'market_founding_credit_balance')}),
        ('Permissions',  {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Dates',        {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'first_name', 'last_name', 'role', 'town', 'mobile', 'password1', 'password2'),
        }),
    )

    @admin.action(description='Grant tester access (can preview unlaunched features)')
    def grant_tester_access(self, request, queryset):
        updated = queryset.update(is_tester=True)
        messages.success(request, f'Granted tester access to {updated} user(s).')

    @admin.action(description='Revoke tester access')
    def revoke_tester_access(self, request, queryset):
        updated = queryset.update(is_tester=False)
        messages.success(request, f'Revoked tester access from {updated} user(s).')

    def get_queryset(self, request):
        # migrate_link()'s hasattr(obj, 'tradie_profile') check would
        # otherwise run one query per row on this (the busiest) changelist;
        # has_accepted_terms likewise avoids a query-per-row for the new
        # column/filter above.
        return (
            super().get_queryset(request)
            .select_related('tradie_profile')
            .annotate(has_accepted_terms=Exists(TermsAcceptance.objects.filter(user=OuterRef('pk'))))
        )

    def accepted_terms_display(self, obj):
        return '✅' if obj.has_accepted_terms else '❌'
    accepted_terms_display.short_description = 'Terms'
    accepted_terms_display.admin_order_field = 'has_accepted_terms'

    def migrate_link(self, obj):
        from django.utils.html import format_html
        if obj.role == User.ROLE_CLIENT and not hasattr(obj, 'tradie_profile'):
            url = reverse('admin:marketplace_user_migrate_to_tradie', args=[obj.pk])
            return format_html('<a class="button" href="{}">Migrate to Local Pro</a>', url)
        return '—'
    migrate_link.short_description = 'Migrate'

    def get_urls(self):
        custom = [
            path('<int:pk>/migrate-to-tradie/', self.admin_site.admin_view(self.migrate_to_tradie_view), name='marketplace_user_migrate_to_tradie'),
        ]
        return custom + super().get_urls()

    def migrate_to_tradie_view(self, request, pk):
        """
        Move a user who mistakenly registered as a client over to the local
        professional (tradie) side — admin picks their trade(s) and service
        town(s), a bare TradieProfile is created (pending verification, same
        as a fresh tradie registration), and the user is emailed telling them
        to log back in on the local pro side.
        """
        if not (self.has_change_permission(request) and request.user.has_perm('marketplace.add_tradieprofile')):
            raise PermissionDenied

        user = get_object_or_404(User, pk=pk)
        if user.role != User.ROLE_CLIENT:
            messages.error(request, f'{user} is not a client account.')
            return redirect(reverse('admin:marketplace_user_changelist'))
        if hasattr(user, 'tradie_profile'):
            messages.error(request, f'{user} already has a local professional profile.')
            return redirect(reverse('admin:marketplace_user_changelist'))

        trade_choices = TradeCategory.get_choices()
        selected_trades = []
        selected_towns = [user.town] if user.town else []
        business_name_value = user.full_name

        if request.method == 'POST':
            selected_trades = request.POST.getlist('trades')
            selected_towns = request.POST.getlist('service_towns')
            business_name_value = (request.POST.get('business_name') or '').strip()

            if not selected_trades:
                messages.error(request, 'Select at least one trade.')
            elif not selected_towns:
                messages.error(request, 'Select at least one service town.')
            else:
                with transaction.atomic():
                    user.role = User.ROLE_TRADIE
                    user.save(update_fields=['role'])
                    TradieProfile.objects.create(
                        user=user,
                        business_name=business_name_value or user.full_name,
                        trades=selected_trades,
                        service_towns=selected_towns or ([user.town] if user.town else []),
                        verification_status=TradieProfile.VERIFICATION_PENDING,
                    )
                notify_client_migrated_to_tradie(user)
                messages.success(request, f'{user} migrated to a local professional account and notified by email.')
                return redirect(reverse('admin:marketplace_tradieprofile_change', args=[user.tradie_profile.pk]))

        context = dict(
            self.admin_site.each_context(request),
            title=f'Migrate {user} to Local Professional',
            opts=self.model._meta,
            user_obj=user,
            trade_choices=trade_choices,
            town_choices=TOWN_CHOICES,
            selected_trades=selected_trades,
            selected_towns=selected_towns,
            business_name_value=business_name_value,
        )
        return render(request, 'admin/marketplace/user/migrate_to_tradie.html', context)


# ── TradieProfile ─────────────────────────────────────────────────────────────

@admin.register(TradieProfile)
class TradieProfileAdmin(admin.ModelAdmin):
    list_display  = ['user', 'business_name', 'years_experience', 'service_towns_display', 'verification_status', 'has_tin_letter', 'documents_verified', 'needs_safety_review_display', 'is_founding_member', 'founding_member_credit_balance']
    list_filter   = ['verification_status', 'documents_verified', 'safety_documents_reviewed', 'is_founding_member']
    search_fields = ['user__email', 'user__first_name', 'business_name', 'tin']
    raw_id_fields = ['user']
    readonly_fields = [
        'documents_verified',
        'tin_letter_link',
        'business_licence_link',
        'public_liability_insurance_link',
        'electrical_contractors_licence_link',
        'plumber_licence_link',
    ]
    fieldsets = (
        ('Business Details',  {'fields': ('user', 'business_name', 'tin', 'years_experience')}),
        ('Trade Categories',  {'fields': ('trades', 'service_towns', 'bio')}),
        ('Verification Documents', {'fields': (
            'tin_letter', 'tin_letter_link',
            'business_licence', 'business_licence_link',
            'public_liability_insurance', 'public_liability_insurance_link',
            'electrical_contractors_licence', 'electrical_contractors_licence_link',
            'plumber_licence', 'plumber_licence_link',
        )}),
        ('Approval Status', {'fields': ('verification_status', 'documents_verified')}),
        ('Safety Document Review', {'fields': ('safety_documents_reviewed',), 'description': (
            'Electrical and Plumbing are safety-critical trades — tradies with either trade selected '
            "can't bid on jobs until this is ticked on, regardless of their overall verification status. "
            'Review their Electrical Contractors Licence / Plumber Licence above before enabling.'
        )}),
        ('Founding Member', {'fields': ('is_founding_member', 'founding_member_credit_balance')}),
        ('Notes',            {'fields': ('verification_notes',)}),
    )
    actions = ['mark_safety_documents_reviewed']

    def service_towns_display(self, obj):
        return obj.service_towns_display()
    service_towns_display.short_description = 'Service towns'

    def has_tin_letter(self, obj):
        return bool(obj.tin_letter)
    has_tin_letter.boolean = True
    has_tin_letter.short_description = 'TIN letter'

    def needs_safety_review_display(self, obj):
        if not obj.requires_safety_document_review():
            return '—'
        return '✅ Reviewed' if obj.safety_documents_reviewed else '⏳ Needs review'
    needs_safety_review_display.short_description = 'Safety docs (Electrical/Plumbing)'

    def mark_safety_documents_reviewed(self, request, queryset):
        updated = queryset.update(safety_documents_reviewed=True)
        self.message_user(request, f'Marked safety documents as reviewed for {updated} profile(s) — they can now bid on jobs.')
    mark_safety_documents_reviewed.short_description = 'Mark safety documents as reviewed (enables bidding)'

    def _doc_link(self, file_field, label):
        from django.utils.html import format_html
        if file_field:
            return format_html('<a href="{}" target="_blank">View {}</a>', file_field.url, label)
        return '—'

    def tin_letter_link(self, obj):
        return self._doc_link(obj.tin_letter, 'TIN Letter')
    tin_letter_link.short_description = 'TIN Letter (view)'

    def business_licence_link(self, obj):
        return self._doc_link(obj.business_licence, 'Business Licence')
    business_licence_link.short_description = 'Business Licence (view)'

    def public_liability_insurance_link(self, obj):
        return self._doc_link(obj.public_liability_insurance, 'Public Liability Insurance')
    public_liability_insurance_link.short_description = 'Public Liability Insurance (view)'

    def electrical_contractors_licence_link(self, obj):
        return self._doc_link(obj.electrical_contractors_licence, 'Electrical Contractors Licence')
    electrical_contractors_licence_link.short_description = 'Electrical Licence (view)'

    def plumber_licence_link(self, obj):
        return self._doc_link(obj.plumber_licence, 'Plumber Licence')
    plumber_licence_link.short_description = 'Plumber Licence (view)'


# ── Task ──────────────────────────────────────────────────────────────────────

class HasQuotingAppointmentFilter(admin.SimpleListFilter):
    title = 'has quoting appointment'
    parameter_name = 'has_quoting_appointment'

    def lookups(self, request, model_admin):
        return (('yes', 'Yes'), ('no', 'No'))

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(quoting_appointments__isnull=False).distinct()
        if self.value() == 'no':
            return queryset.filter(quoting_appointments__isnull=True)
        return queryset


class AppointmentStatusFilter(admin.SimpleListFilter):
    title = 'appointment status'
    parameter_name = 'appointment_status'

    def lookups(self, request, model_admin):
        return QuotingAppointment.STATUS_CHOICES

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(quoting_appointments__status=self.value()).distinct()
        return queryset


class CompletedJobBillingFilter(admin.SimpleListFilter):
    title = 'completed job billing status'
    parameter_name = 'billing_status'

    def lookups(self, request, model_admin):
        return (
            ('uninvoiced', 'Completed - fee pending (uninvoiced)'),
            ('invoiced', 'Completed - fee invoiced'),
            ('no_fee', 'Completed - no fee record'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'uninvoiced':
            return queryset.filter(status=Task.STATUS_COMPLETED, platform_fees__status=PlatformFee.STATUS_PENDING).distinct()
        if self.value() == 'invoiced':
            return queryset.filter(status=Task.STATUS_COMPLETED, platform_fees__status=PlatformFee.STATUS_INVOICED).distinct()
        if self.value() == 'no_fee':
            return queryset.filter(status=Task.STATUS_COMPLETED, platform_fees__isnull=True)
        return queryset


class RemovedOrCancelledFilter(admin.SimpleListFilter):
    title = 'removed / cancelled'
    parameter_name = 'removed_or_cancelled'

    def lookups(self, request, model_admin):
        return (('yes', 'Yes'), ('no', 'No'))

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(Q(removed_at__isnull=False) | Q(status=Task.STATUS_CANCELLED))
        if self.value() == 'no':
            return queryset.exclude(Q(removed_at__isnull=False) | Q(status=Task.STATUS_CANCELLED))
        return queryset


class QuotingAppointmentSlotInline(admin.TabularInline):
    model = QuotingAppointmentSlot
    extra = 0
    readonly_fields = ['proposed_date', 'start_time', 'end_time', 'is_selected', 'created_at']
    fields = ['proposed_date', 'start_time', 'end_time', 'is_selected', 'created_at']
    can_delete = False
    show_change_link = False


class QuotingAppointmentInline(admin.TabularInline):
    model = QuotingAppointment
    extra = 0
    readonly_fields = ['provider', 'status', 'appointment_note', 'selected_slot', 'created_at', 'updated_at']
    fields = ['provider', 'status', 'appointment_note', 'selected_slot', 'created_at', 'updated_at']
    can_delete = False
    show_change_link = True


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display  = ['title', 'client', 'category', 'town', 'budget', 'status', 'assigned_tradie', 'circumvention_flag_display', 'created_at']
    list_filter   = [
        'status', 'category', 'town', 'urgency',
        HasQuotingAppointmentFilter, AppointmentStatusFilter, RemovedOrCancelledFilter,
        CompletedJobBillingFilter,
        'backdoor_monitoring_flag', 'backdoor_reviewed', 'created_at',
    ]
    search_fields = ['title', 'client__email', 'description']
    raw_id_fields = ['client', 'assigned_tradie', 'removed_by', 'backdoor_reviewed_by']
    readonly_fields = ['created_at', 'completed_at']
    fieldsets = (
        ('Task Info', {'fields': ('title', 'category', 'categories', 'description')}),
        ('Location & Schedule', {'fields': ('town', 'preferred_date', 'preferred_date_end', 'completed_at')}),
        ('Budget & Value', {'fields': ('budget', 'final_job_value', 'budget_type')}),
        ('Materials & Inclusions', {'fields': ('materials_responsibility', 'meals_provided', 'parking_available_flag', 'site_access_available', 'tools_required', 'rubbish_removal_required', 'after_hours_required', 'on_site_inspection_required', 'delivery_required', 'clean_up_required', 'client_provide_photos', 'warranty_followup_requested', 'materials_notes', 'parking_notes', 'access_notes', 'special_instructions')}),
        ('Assignment & Status', {'fields': ('client', 'assigned_tradie', 'status')}),
        ('Removal & Platform Circumvention Monitoring', {'fields': ('removed_at', 'removed_by', 'removal_reason', 'cancellation_reason', 'backdoor_monitoring_flag', 'backdoor_monitoring_note', 'backdoor_reviewed', 'backdoor_reviewed_at', 'backdoor_reviewed_by')}),
        ('Timestamps', {'fields': ('created_at',)}),
    )
    date_hierarchy = 'created_at'
    filter_horizontal = ['categories']
    inlines = [QuotingAppointmentInline]
    actions = ['mark_circumvention_reviewed', 'flag_for_circumvention_monitoring', 'clear_circumvention_flag']

    def circumvention_flag_display(self, obj):
        return obj.backdoor_monitoring_flag
    circumvention_flag_display.boolean = True
    circumvention_flag_display.short_description = 'Circumvention Flag'

    def mark_circumvention_reviewed(self, request, queryset):
        from django.utils import timezone
        updated = queryset.update(
            backdoor_reviewed=True,
            backdoor_reviewed_at=timezone.now(),
            backdoor_reviewed_by=request.user,
        )
        self.message_user(request, f'{updated} task(s) marked as Platform Circumvention reviewed.')
    mark_circumvention_reviewed.short_description = 'Mark as Platform Circumvention reviewed'

    def flag_for_circumvention_monitoring(self, request, queryset):
        updated = queryset.update(backdoor_monitoring_flag=True, backdoor_reviewed=False)
        self.message_user(request, f'{updated} task(s) flagged for Platform Circumvention monitoring.')
    flag_for_circumvention_monitoring.short_description = 'Flag for Platform Circumvention monitoring'

    def clear_circumvention_flag(self, request, queryset):
        updated = queryset.update(backdoor_monitoring_flag=False)
        self.message_user(request, f'Cleared Platform Circumvention flag on {updated} task(s).')
    clear_circumvention_flag.short_description = 'Clear Platform Circumvention flag'


# ── Quote ─────────────────────────────────────────────────────────────────────

@admin.register(Quote)
class QuoteAdmin(admin.ModelAdmin):
    list_display  = ['task', 'tradie', 'customer_facing_quote', 'quote_includes', 'status', 'include_platform_fee', 'created_at']
    list_select_related = ['task', 'tradie']
    list_filter   = ['status', 'include_platform_fee', 'quote_includes', 'created_at']
    search_fields = ['task__title', 'tradie__email']
    raw_id_fields = ['task', 'tradie']
    readonly_fields = ['created_at']
    fieldsets = (
        ('Quote Assignment', {'fields': ('task', 'tradie')}),
        ('Quote Pricing', {'fields': ('minimum_take_home_amount', 'customer_facing_quote', 'price', 'include_platform_fee')}),
        ('Fee Estimates', {'fields': ('estimated_platform_fee', 'estimated_provider_take_home', 'fee_rule_applied', 'success_fee_rate_at_quote_time', 'success_fee_cap_at_quote_time', 'large_job_threshold_at_quote_time', 'large_job_fee_rate_at_quote_time', 'client_quote_total', 'estimated_tradie_take_home')}),
        ('Discount', {'fields': ('used_founding_credit', 'promo_code', 'estimated_discount_amount')}),
        ('Job Details', {'fields': ('includes_materials', 'quote_includes', 'earliest_available_date', 'estimated_job_duration', 'warranty_or_followup_included')}),
        ('Message', {'fields': ('message',)}),
        ('Status', {'fields': ('status',)}),
        ('Timestamps', {'fields': ('created_at',)}),
    )
    date_hierarchy = 'created_at'


# ── Quoting appointment ───────────────────────────────────────────────────────

@admin.register(QuotingAppointment)
class QuotingAppointmentAdmin(admin.ModelAdmin):
    list_display = ['task', 'provider', 'client', 'status', 'selected_slot', 'created_at']
    list_select_related = ['task', 'provider', 'client', 'selected_slot']
    list_filter = ['status', 'created_at']
    search_fields = ['task__title', 'provider__email', 'client__email']
    raw_id_fields = ['task', 'provider', 'client', 'selected_slot']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        ('Appointment Request', {'fields': ('task', 'provider', 'client', 'status', 'selected_slot')}),
        ('Slots', {'fields': ('appointment_note',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )
    inlines = [QuotingAppointmentSlotInline]


@admin.register(QuotingAppointmentSlot)
class QuotingAppointmentSlotAdmin(admin.ModelAdmin):
    list_display = ['quoting_appointment', 'proposed_date', 'start_time', 'end_time', 'is_selected', 'created_at']
    list_select_related = ['quoting_appointment']
    list_filter = ['is_selected', 'proposed_date', 'created_at']
    search_fields = ['quoting_appointment__task__title', 'quoting_appointment__provider__email']
    raw_id_fields = ['quoting_appointment']
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'


# ── Message ───────────────────────────────────────────────────────────────────

@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    # edited_at/deleted_at/edit_history readonly here on purpose — this is
    # the dispute/circumvention-review record, not something to hand-edit.
    # body is deliberately never cleared by a "delete", so it's always the
    # real text even when a user sees "This message was deleted".
    list_display  = ['task', 'sender', 'recipient', 'created_at', 'edited_at', 'deleted_at']
    list_filter   = ['deleted_at']
    list_select_related = ['task', 'sender', 'recipient']
    search_fields = ['sender__email', 'recipient__email', 'body']
    raw_id_fields = ['task', 'sender', 'recipient']
    readonly_fields = ['edited_at', 'deleted_at', 'edit_history']
    date_hierarchy = 'created_at'


# ── Public review ─────────────────────────────────────────────────────────────

class HasTaskFilter(admin.SimpleListFilter):
    title = 'source'
    parameter_name = 'source'

    def lookups(self, request, model_admin):
        return (('platform', 'Platform job'), ('historical', 'Historical (admin-added)'))

    def queryset(self, request, queryset):
        if self.value() == 'platform':
            return queryset.filter(task__isnull=False)
        if self.value() == 'historical':
            return queryset.filter(task__isnull=True)
        return queryset


@admin.register(PublicReview)
class PublicReviewAdmin(admin.ModelAdmin):
    list_display  = ['ratee', 'rater', 'source_display', 'reliability_punctuality', 'timeline_schedule_delivery', 'service_quality_workmanship', 'overall_display', 'created_at']
    list_select_related = ['ratee', 'rater', 'task']
    list_filter   = [HasTaskFilter, 'created_at']
    search_fields = ['rater__email', 'ratee__email', 'task__title', 'admin_note']
    raw_id_fields = ['task', 'rater', 'ratee']
    readonly_fields = ['created_at', 'overall_display']
    fieldsets = (
        ('Review Assignment', {'fields': ('task', 'rater', 'ratee'), 'description': (
            'To edit or correct an existing review (e.g. a malicious one), just change its scores/comment below. '
            'To add a NEW review — e.g. for a local pro you\'ve worked with before they joined the platform — '
            'leave "task" blank, pick any rater (yourself or the client who worked with them), and fill in '
            '"Context" under Comment instead of a job.'
        )}),
        ('Criteria Scores', {'fields': ('reliability_punctuality', 'quote_price_accuracy', 'value_for_money', 'service_quality_workmanship', 'communication_after_service', 'timeline_schedule_delivery', 'overall_display')}),
        ('Comment', {'fields': ('comment', 'admin_note')}),
        ('Timestamps', {'fields': ('created_at',)}),
    )
    date_hierarchy = 'created_at'

    def source_display(self, obj):
        return obj.task if obj.task else f'📝 {obj.admin_note or "Historical"}'
    source_display.short_description = 'Job / source'

    def overall_display(self, obj):
        # obj is a blank unsaved instance on the Add page — most of the score
        # fields have no default (None), so obj.overall's sum would raise a
        # TypeError there. Only compute it once every score is actually set.
        scores = [
            obj.reliability_punctuality, obj.quote_price_accuracy, obj.value_for_money,
            obj.service_quality_workmanship, obj.communication_after_service, obj.timeline_schedule_delivery,
        ]
        if any(s is None for s in scores):
            return '—'
        return f'{obj.overall:.1f} / 5'
    overall_display.short_description = 'Overall (computed)'
    overall_display.readonly = True


# ── Private review (ADMIN / DISPUTE RECORDS ONLY) ────────────────────────────

@admin.register(PrivateReview)
class PrivateReviewAdmin(admin.ModelAdmin):
    """
    ⚠️  DISPUTE RECORDS — tradie's confidential rating of a client.
    Never shown on any public page. For admin use / dispute handling only.
    """
    list_display  = ['task', 'rater', 'ratee', 'access_readiness', 'payment', 'conduct', 'created_at']
    search_fields = ['rater__email', 'ratee__email', 'task__title']
    raw_id_fields = ['task', 'rater', 'ratee']
    # Read-only in the list view to discourage accidental changes
    readonly_fields = ['task', 'rater', 'ratee', 'created_at']

    def has_add_permission(self, request):
        return False  # created only via the rate_client view

    def get_queryset(self, request):
        return super().get_queryset(request)  # uses PrivateReviewManager.admin_only() implicitly


# ── Trade Category ────────────────────────────────────────────────────────────

@admin.register(TradeCategory)
class TradeCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'icon', 'parent', 'active']
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ['name', 'slug']


# ── Task Photo ────────────────────────────────────────────────────────────────

@admin.register(TaskPhoto)
class TaskPhotoAdmin(admin.ModelAdmin):
    list_display = ['task', 'caption', 'uploaded_at']
    list_select_related = ['task']
    list_filter = ['uploaded_at']
    search_fields = ['task__title', 'caption']
    raw_id_fields = ['task']
    readonly_fields = ['uploaded_at']


# ── Platform Settings ─────────────────────────────────────────────────────────

@admin.register(PlatformSettings)
class PlatformSettingsAdmin(admin.ModelAdmin):
    list_display = ['success_fee_rate', 'market_fee_rate', 'success_fee_cap', 'large_job_threshold', 'large_job_fee_rate', 'active', 'updated_at']
    list_filter = ['active', 'updated_at']
    readonly_fields = ['updated_at']
    fieldsets = (
        ('Fee rules', {
            'fields': (
                'success_fee_rate', 'success_fee_cap',
                'large_job_threshold', 'large_job_fee_rate',
                'market_fee_rate',
                'active', 'updated_at'
            )
        }),
        ('Legal', {'fields': ('terms_version',)}),
    )

    def has_add_permission(self, request):
        # Allow only one active record
        return not PlatformSettings.objects.filter(active=True).exists()


# ── Promo Codes ────────────────────────────────────────────────────────────────

@admin.register(PromoCode)
class PromoCodeAdmin(admin.ModelAdmin):
    list_display  = ['code', 'discount_type', 'discount_value', 'active', 'start_date', 'end_date', 'times_used', 'max_uses']
    list_filter   = ['discount_type', 'active']
    search_fields = ['code']
    readonly_fields = ['times_used', 'created_at']
    fieldsets = (
        ('Code', {'fields': ('code', 'active')}),
        ('Discount', {'fields': ('discount_type', 'discount_value')}),
        ('Availability', {'fields': ('start_date', 'end_date', 'max_uses')}),
        ('Usage', {'fields': ('times_used', 'created_at')}),
    )


# ── Platform Fee ──────────────────────────────────────────────────────────────

@admin.register(PlatformFee)
class PlatformFeeAdmin(admin.ModelAdmin):
    list_display = ['task', 'tradie', 'final_job_value', 'gross_fee_amount', 'discount_amount', 'fee_amount', 'status', 'created_at']
    list_select_related = ['task', 'tradie']
    list_filter = ['status', 'tradie', 'created_at']
    search_fields = ['task__title', 'tradie__email']
    raw_id_fields = ['task', 'tradie']
    readonly_fields = ['created_at']
    fieldsets = (
        ('Task & Tradie', {'fields': ('task', 'tradie')}),
        ('Fee Details', {'fields': ('final_job_value', 'fee_rate', 'fee_cap', 'gross_fee_amount', 'discount_amount', 'fee_amount')}),
        ('Status', {'fields': ('status',)}),
        ('Timestamps', {'fields': ('created_at',)}),
    )


# ── Invoice ───────────────────────────────────────────────────────────────────

class SentInvoiceFilter(admin.SimpleListFilter):
    title = 'sent status'
    parameter_name = 'sent_status'

    def lookups(self, request, model_admin):
        return (('sent', 'Sent'), ('unsent', 'Not yet sent (draft)'))

    def queryset(self, request, queryset):
        if self.value() == 'sent':
            return queryset.exclude(sent_at__isnull=True)
        if self.value() == 'unsent':
            return queryset.filter(sent_at__isnull=True)
        return queryset


class UnpaidInvoiceFilter(admin.SimpleListFilter):
    title = 'unpaid invoices'
    parameter_name = 'unpaid'

    def lookups(self, request, model_admin):
        return (('yes', 'Unpaid (sent or overdue)'),)

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(status__in=[Invoice.STATUS_SENT, Invoice.STATUS_OVERDUE])
        return queryset


class OverdueInvoiceFilter(admin.SimpleListFilter):
    title = 'overdue invoices'
    parameter_name = 'overdue'

    def lookups(self, request, model_admin):
        return (('yes', 'Overdue'),)

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            today = timezone.localdate()
            return queryset.filter(status__in=[Invoice.STATUS_SENT, Invoice.STATUS_OVERDUE], due_date__lt=today)
        return queryset


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0
    raw_id_fields = ['platform_fee', 'task']
    fields = ['task', 'description', 'final_job_value', 'fee_rate', 'amount']
    readonly_fields = ['task', 'final_job_value', 'fee_rate']


class InvoiceNotificationInline(admin.TabularInline):
    model = InvoiceNotification
    extra = 0
    fields = ['channel', 'subject', 'body', 'created_at']
    readonly_fields = ['channel', 'subject', 'body', 'created_at']
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    change_list_template = 'admin/marketplace/invoice/invoice_changelist.html'
    list_display = ['invoice_number', 'tradie', 'period_start', 'period_end', 'total_amount', 'status', 'due_date', 'is_overdue_display']
    list_filter = [
        'status', 'tradie', SentInvoiceFilter, UnpaidInvoiceFilter, OverdueInvoiceFilter,
        'period_start', 'due_date', 'created_at',
    ]
    search_fields = ['invoice_number', 'tradie__email', 'tradie__first_name', 'tradie__last_name']
    raw_id_fields = ['tradie']
    readonly_fields = ['invoice_number', 'created_at', 'sent_at', 'paid_at']
    fieldsets = (
        ('Invoice Details', {'fields': ('invoice_number', 'tradie', 'period_start', 'period_end', 'total_amount')}),
        ('Status', {'fields': ('status', 'due_date')}),
        ('Timestamps', {'fields': ('created_at', 'sent_at', 'paid_at')}),
    )
    date_hierarchy = 'created_at'
    inlines = [InvoiceLineInline, InvoiceNotificationInline]
    actions = ['send_invoices_action', 'void_invoices_action']

    def is_overdue_display(self, obj):
        return '🔴 OVERDUE' if obj.is_overdue else '✓'
    is_overdue_display.short_description = 'Overdue'

    def send_invoices_action(self, request, queryset):
        sendable = queryset.filter(status__in=[Invoice.STATUS_DRAFT, Invoice.STATUS_OVERDUE])
        skipped = queryset.count() - sendable.count()
        sent = 0
        email_failures = 0
        for invoice in sendable:
            if not send_invoice_notifications(invoice):
                email_failures += 1
            sent += 1
        if sent:
            self.message_user(request, f'Sent {sent} invoice(s) to provider(s).')
            if email_failures:
                self.message_user(
                    request,
                    f'{email_failures} invoice email(s) failed to send (mail server issue). '
                    f'Invoices are still marked sent — in-platform and SMS log notices were created.',
                    level=messages.WARNING,
                )
        else:
            self.message_user(request, 'No invoices were sent.', level=messages.WARNING)
        if skipped:
            self.message_user(
                request,
                f'Skipped {skipped} invoice(s) that were already sent, paid, or void — '
                f'use the invoice detail page if you really need to resend one.',
                level=messages.WARNING,
            )
    send_invoices_action.short_description = 'Send selected invoices to provider'

    def void_invoices_action(self, request, queryset):
        voided = 0
        for invoice in queryset.exclude(status=Invoice.STATUS_VOID):
            invoice.void()
            voided += 1
        self.message_user(request, f'Voided {voided} invoice(s). Linked platform fees returned to pending where unpaid.')
    void_invoices_action.short_description = 'Void selected invoices'

    # ── Custom views: create invoice / weekly invoices ─────────────────────────

    def get_urls(self):
        custom = [
            path('create/', self.admin_site.admin_view(self.create_invoice_view), name='marketplace_invoice_create'),
            path('weekly/', self.admin_site.admin_view(self.weekly_invoices_view), name='marketplace_invoice_weekly'),
            path('<int:object_id>/send/', self.admin_site.admin_view(self.send_invoice_view), name='marketplace_invoice_send'),
            path('<int:object_id>/void/', self.admin_site.admin_view(self.void_invoice_view), name='marketplace_invoice_void'),
        ]
        return custom + super().get_urls()

    def send_invoice_view(self, request, object_id):
        if not self.has_change_permission(request):
            raise PermissionDenied
        invoice = get_object_or_404(Invoice, pk=object_id)
        already_sent = invoice.status not in (Invoice.STATUS_DRAFT, Invoice.STATUS_OVERDUE)
        if invoice.status == Invoice.STATUS_VOID:
            messages.error(request, 'Cannot send a voided invoice.')
        elif already_sent and request.POST.get('confirm_resend') != '1':
            messages.warning(
                request,
                f'Invoice {invoice.invoice_number} was already sent (status: {invoice.get_status_display()}). '
                f'Sending again will re-notify {invoice.tradie.full_name} and create duplicate notification '
                f'records. If you really mean to resend it, use the "Resend anyway" link.',
            )
        else:
            email_sent = send_invoice_notifications(invoice)
            messages.success(request, f'Invoice {invoice.invoice_number} sent to {invoice.tradie.full_name}.')
            if not email_sent:
                messages.warning(
                    request,
                    f'The email to {invoice.tradie.email} failed to send (mail server issue). '
                    f'The invoice is still marked sent — in-platform and SMS log notices were created.',
                )
        return redirect(reverse('admin:marketplace_invoice_change', args=[invoice.pk]))

    def void_invoice_view(self, request, object_id):
        if not self.has_change_permission(request):
            raise PermissionDenied
        invoice = get_object_or_404(Invoice, pk=object_id)
        if invoice.status != Invoice.STATUS_VOID:
            invoice.void()
            messages.success(request, f'Invoice {invoice.invoice_number} voided. Linked platform fees returned to pending where unpaid.')
        return redirect(reverse('admin:marketplace_invoice_change', args=[invoice.pk]))

    def create_invoice_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied
        providers = User.objects.filter(role=User.ROLE_TRADIE).order_by('first_name', 'last_name')
        context = dict(
            self.admin_site.each_context(request),
            title='Create Invoice',
            providers=providers,
            opts=self.model._meta,
            step='select',
        )

        step = request.POST.get('step')

        if step == 'fetch':
            tradie = get_object_or_404(User, pk=request.POST.get('tradie'), role=User.ROLE_TRADIE)
            period_start = request.POST.get('period_start')
            period_end = request.POST.get('period_end')
            try:
                start = datetime.strptime(period_start, '%Y-%m-%d').date()
                end = datetime.strptime(period_end, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                messages.error(request, 'Please provide a valid invoice period.')
                return render(request, 'admin/marketplace/invoice/create_invoice.html', context)

            settings_obj = get_active_platform_settings()
            fees = get_eligible_platform_fees(tradie, start, end)
            fee_rows = [
                {
                    'fee': fee,
                    'description': build_invoice_line_description(fee, settings_obj),
                    'rule': fee_rule_label(fee, settings_obj),
                }
                for fee in fees
            ]

            context.update(
                step='preview',
                tradie=tradie,
                period_start=period_start,
                period_end=period_end,
                fee_rows=fee_rows,
                total_jobs=sum((row['fee'].final_job_value for row in fee_rows), Decimal('0')),
                total_fees=sum((row['fee'].fee_amount for row in fee_rows), Decimal('0')),
                manual_line_range=range(1, 4),
            )
            return render(request, 'admin/marketplace/invoice/create_invoice.html', context)

        if step == 'create':
            tradie = get_object_or_404(User, pk=request.POST.get('tradie'), role=User.ROLE_TRADIE)
            start = datetime.strptime(request.POST.get('period_start'), '%Y-%m-%d').date()
            end = datetime.strptime(request.POST.get('period_end'), '%Y-%m-%d').date()
            fee_ids = request.POST.getlist('fee_ids')

            manual_lines = []
            for i in range(1, 4):
                desc = (request.POST.get(f'manual_description_{i}') or '').strip()
                amount = (request.POST.get(f'manual_amount_{i}') or '').strip()
                if desc and amount:
                    manual_lines.append({'description': desc, 'amount': amount})

            invoice = create_invoice_with_lines(
                tradie=tradie, period_start=start, period_end=end,
                fee_ids=fee_ids, manual_lines=manual_lines,
            )
            messages.success(request, f'Invoice {invoice.invoice_number} created as a draft. Review it below, then click "Send Invoice" when ready.')
            return redirect(reverse('admin:marketplace_invoice_change', args=[invoice.pk]))

        return render(request, 'admin/marketplace/invoice/create_invoice.html', context)

    def weekly_invoices_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied
        context = dict(
            self.admin_site.each_context(request),
            title='Create Weekly Invoices',
            opts=self.model._meta,
            step='select',
        )

        step = request.POST.get('step')

        if step == 'preview':
            period_start = request.POST.get('period_start')
            period_end = request.POST.get('period_end')
            try:
                start = datetime.strptime(period_start, '%Y-%m-%d').date()
                end = datetime.strptime(period_end, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                messages.error(request, 'Please provide a valid date range.')
                return render(request, 'admin/marketplace/invoice/weekly_invoices.html', context)

            entries = get_providers_with_pending_fees(start, end)
            context.update(
                step='preview',
                period_start=period_start,
                period_end=period_end,
                entries=entries,
            )
            return render(request, 'admin/marketplace/invoice/weekly_invoices.html', context)

        if step == 'generate':
            start = datetime.strptime(request.POST.get('period_start'), '%Y-%m-%d').date()
            end = datetime.strptime(request.POST.get('period_end'), '%Y-%m-%d').date()
            tradie_ids = request.POST.getlist('tradie_ids')

            created = []
            for tradie_id in tradie_ids:
                tradie = get_object_or_404(User, pk=tradie_id, role=User.ROLE_TRADIE)
                fee_ids = [f.pk for f in get_eligible_platform_fees(tradie, start, end)]
                if not fee_ids:
                    continue
                created.append(create_invoice_with_lines(
                    tradie=tradie, period_start=start, period_end=end, fee_ids=fee_ids,
                ))

            if created:
                messages.success(request, f'Created {len(created)} draft invoice(s). Review and send each one individually.')
            else:
                messages.warning(request, 'No invoices were created — no providers had eligible jobs in this period.')
            return redirect(reverse('admin:marketplace_invoice_changelist'))

        return render(request, 'admin/marketplace/invoice/weekly_invoices.html', context)


# ── Invoice Line ──────────────────────────────────────────────────────────────

@admin.register(InvoiceLine)
class InvoiceLineAdmin(admin.ModelAdmin):
    list_display = ['invoice', 'line_type', 'description', 'amount']
    list_select_related = ['invoice']
    list_filter = ['line_type', 'invoice__created_at']
    search_fields = ['invoice__invoice_number', 'description']
    raw_id_fields = ['invoice', 'platform_fee', 'task']


# ── Invoice Notification ─────────────────────────────────────────────────────

@admin.register(InvoiceNotification)
class InvoiceNotificationAdmin(admin.ModelAdmin):
    list_display = ['invoice', 'recipient', 'channel', 'created_at']
    list_select_related = ['invoice', 'recipient']
    list_filter = ['channel', 'created_at']
    search_fields = ['invoice__invoice_number', 'recipient__email']
    raw_id_fields = ['invoice', 'recipient']
    readonly_fields = ['invoice', 'recipient', 'channel', 'subject', 'body', 'created_at']

    def has_add_permission(self, request):
        return False


# ── Legal & Terms ────────────────────────────────────────────────────────────

@admin.register(TermsAcceptance)
class TermsAcceptanceAdmin(admin.ModelAdmin):
    list_display  = ['user', 'user_role', 'terms_version', 'accepted_platform_circumvention', 'accepted_invoicing_terms', 'ip_address', 'accepted_at']
    list_filter   = ['terms_version', 'accepted_platform_circumvention', 'accepted_invoicing_terms', 'accepted_at']
    search_fields = ['user__email', 'user__first_name', 'user__last_name']
    raw_id_fields = ['user']
    readonly_fields = ['user', 'terms_version', 'accepted_at', 'ip_address', 'user_agent',
                       'accepted_platform_circumvention', 'accepted_invoicing_terms']
    date_hierarchy = 'accepted_at'

    def user_role(self, obj):
        return obj.user.get_role_display() if obj.user.role else 'Staff / Admin'
    user_role.short_description = 'Role'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# ── Platform Circumvention Cases ──────────────────────────────────────────────

@admin.register(PlatformCircumventionCase)
class PlatformCircumventionCaseAdmin(admin.ModelAdmin):
    list_display  = ['id', 'client', 'provider', 'task', 'total_job_value', 'client_fee_amount', 'provider_fee_amount', 'status', 'created_at']
    list_filter   = ['status', 'created_at']
    search_fields = ['client__email', 'provider__email', 'task__title', 'evidence_notes']
    raw_id_fields = ['client', 'provider', 'task', 'reviewed_by']
    readonly_fields = ['created_at', 'calculated_fee_display']
    fieldsets = (
        ('Parties',     {'fields': ('client', 'provider', 'task')}),
        ('Fee Details', {'fields': ('total_job_value', 'fee_percentage', 'minimum_fee', 'calculated_fee_display', 'client_fee_amount', 'provider_fee_amount')}),
        ('Status',      {'fields': ('status',)}),
        ('Evidence',    {'fields': ('evidence_notes',)}),
        ('Review',      {'fields': ('reviewed_by', 'reviewed_at')}),
        ('Timestamps',  {'fields': ('created_at',)}),
    )
    actions = ['mark_invoiced', 'mark_paid', 'mark_waived', 'mark_disputed', 'mark_closed']
    date_hierarchy = 'created_at'

    def calculated_fee_display(self, obj):
        from decimal import Decimal
        fee = PlatformCircumventionCase.calculate_fee(
            obj.total_job_value or Decimal('0'),
            obj.fee_percentage,
            obj.minimum_fee,
        )
        return f'FJD ${fee:.2f} (each party)'
    calculated_fee_display.short_description = 'Calculated fee (each party)'

    def _set_status(self, request, queryset, status, label):
        updated = queryset.update(status=status, reviewed_by=request.user, reviewed_at=timezone.now())
        self.message_user(request, f'{updated} case(s) marked as {label}.')

    def mark_invoiced(self, request, queryset):
        self._set_status(request, queryset, PlatformCircumventionCase.STATUS_INVOICED, 'Invoiced')
    mark_invoiced.short_description = 'Mark selected cases as Invoiced'

    def mark_paid(self, request, queryset):
        self._set_status(request, queryset, PlatformCircumventionCase.STATUS_PAID, 'Paid')
    mark_paid.short_description = 'Mark selected cases as Paid'

    def mark_waived(self, request, queryset):
        self._set_status(request, queryset, PlatformCircumventionCase.STATUS_WAIVED, 'Waived')
    mark_waived.short_description = 'Mark selected cases as Waived'

    def mark_disputed(self, request, queryset):
        self._set_status(request, queryset, PlatformCircumventionCase.STATUS_DISPUTED, 'Disputed')
    mark_disputed.short_description = 'Mark selected cases as Disputed'

    def mark_closed(self, request, queryset):
        self._set_status(request, queryset, PlatformCircumventionCase.STATUS_CLOSED, 'Closed')
    mark_closed.short_description = 'Mark selected cases as Closed'


# ── Platform Notices (Communications) ────────────────────────────────────────

@admin.register(PlatformNotice)
class PlatformNoticeAdmin(admin.ModelAdmin):
    list_display  = ['recipient', 'notice_type', 'channel', 'subject', 'sent_by', 'sent_at']
    list_filter   = ['notice_type', 'channel', 'sent_at']
    search_fields = ['recipient__email', 'recipient__first_name', 'subject', 'body']
    raw_id_fields = ['recipient', 'sent_by']
    readonly_fields = ['sent_at']
    fieldsets = (
        ('Recipient',   {'fields': ('recipient',)}),
        ('Notice',      {'fields': ('notice_type', 'channel', 'subject', 'body')}),
        ('Sent By',     {'fields': ('sent_by', 'sent_at')}),
    )
    date_hierarchy = 'sent_at'

    def save_model(self, request, obj, form, change):
        if not obj.sent_by_id:
            obj.sent_by = request.user
        super().save_model(request, obj, form, change)

        if change:
            return  # only deliver on initial creation, not on later edits

        if obj.channel == PlatformNotice.CHANNEL_EMAIL:
            if not obj.recipient.email:
                self.message_user(
                    request, f'{obj.recipient} has no email address on file — nothing was sent.',
                    level=messages.WARNING,
                )
                return
            from django.conf import settings as django_settings
            from django.core.mail import send_mail
            try:
                send_mail(
                    obj.subject, obj.body,
                    getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@coconutwireless.fj'),
                    [obj.recipient.email],
                    fail_silently=False,
                )
                self.message_user(request, f'Notice emailed to {obj.recipient.email}.')
            except Exception as exc:
                import sys
                import traceback
                print(f'PlatformNoticeAdmin: email send failed: {exc!r}', flush=True)
                traceback.print_exc()
                sys.stderr.flush()
                try:
                    import sentry_sdk
                    sentry_sdk.capture_exception(exc)
                except ImportError:
                    pass
                self.message_user(
                    request,
                    f'Notice was saved but the email to {obj.recipient.email} failed to send (mail server issue).',
                    level=messages.WARNING,
                )
        elif obj.channel == PlatformNotice.CHANNEL_IN_PLATFORM:
            self.message_user(request, f'In-app notice saved — {obj.recipient} will see it under their Notices page.')
        else:
            self.message_user(request, 'Notice saved (SMS channel is logged only — send it manually).')


# ── Sponsor / Ad Banner ───────────────────────────────────────────────────────

class SponsorAdminForm(forms.ModelForm):
    placements = forms.MultipleChoiceField(
        choices=Sponsor.PLACEMENT_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=True,
        label='Placements (select one or more pages to display this ad)',
    )

    class Meta:
        model = Sponsor
        fields = '__all__'


@admin.register(Sponsor)
class SponsorAdmin(admin.ModelAdmin):
    form = SponsorAdminForm
    list_display = ['business_name', 'placements_display', 'start_date', 'end_date', 'active', 'is_active_display']
    list_filter = ['active', 'start_date', 'end_date']
    search_fields = ['business_name', 'destination_url']
    readonly_fields = ['created_at']
    fieldsets = (
        ('Sponsor Details', {'fields': ('business_name', 'destination_url', 'banner_image')}),
        ('Placement', {'fields': ('placements',)}),
        ('Schedule', {'fields': ('start_date', 'end_date', 'active')}),
        ('Timestamps', {'fields': ('created_at',)}),
    )
    date_hierarchy = 'created_at'

    def placements_display(self, obj):
        return ', '.join(obj.placements)
    placements_display.short_description = 'Placements'

    def is_active_display(self, obj):
        from django.utils import timezone
        today = timezone.localdate()
        if obj.active and obj.start_date <= today <= obj.end_date:
            return '✅ ACTIVE'
        return '❌'
    is_active_display.short_description = 'Currently Active'


# ── Market ────────────────────────────────────────────────────────────────────

class MarketOrderInline(admin.TabularInline):
    model = MarketOrder
    extra = 0
    fields = ['buyer', 'quantity', 'total_price', 'platform_fee_amount', 'requested_date', 'status', 'fee_status']
    # status is view-only here — editing it inline would bypass the stock
    # reconciliation in MarketOrderAdmin.save_model(). Change status from the
    # Market Order admin page instead, where units_sold stays in sync.
    readonly_fields = ['buyer', 'quantity', 'total_price', 'platform_fee_amount', 'requested_date', 'status']
    can_delete = False


@admin.register(MarketListing)
class MarketListingAdmin(admin.ModelAdmin):
    list_display = ['title', 'seller', 'category', 'price_per_unit', 'units_remaining', 'units_available', 'use_founding_credit', 'status', 'order_mode', 'created_at']
    list_select_related = ['seller']
    list_filter = ['status', 'order_mode', 'fulfillment_method', 'category', 'use_founding_credit']
    search_fields = ['title', 'seller__first_name', 'seller__last_name', 'seller__email']
    raw_id_fields = ['seller']
    readonly_fields = ['created_at', 'fee_rate_at_listing']
    inlines = [MarketOrderInline]
    date_hierarchy = 'created_at'

    def units_remaining(self, obj):
        return obj.units_remaining()


@admin.register(MarketOrder)
class MarketOrderAdmin(admin.ModelAdmin):
    list_display = ['listing', 'buyer', 'quantity', 'total_price', 'platform_fee_amount', 'requested_date', 'status', 'fee_status', 'created_at']
    list_select_related = ['listing', 'buyer']
    list_filter = ['status', 'fee_status', 'fulfillment_method']
    search_fields = ['listing__title', 'buyer__first_name', 'buyer__last_name', 'buyer__email']
    raw_id_fields = ['listing', 'buyer']
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'

    # Stock-holding vs stock-releasing states — mirrors the reconciliation
    # already done in market_order_respond/market_order_cancel, so an admin
    # changing status here (the only other place status can change) keeps
    # units_sold in sync instead of silently drifting out of it.
    _RELEASES_STOCK = {MarketOrder.STATUS_DECLINED, MarketOrder.STATUS_CANCELLED}

    def save_model(self, request, obj, form, change):
        old_status = MarketOrder.objects.filter(pk=obj.pk).values_list('status', flat=True).first() if change else None
        super().save_model(request, obj, form, change)
        if old_status is not None and old_status != obj.status:
            released_before = old_status in self._RELEASES_STOCK
            released_after = obj.status in self._RELEASES_STOCK
            if released_after and not released_before:
                MarketListing.objects.filter(pk=obj.listing_id).update(
                    units_sold=Greatest(F('units_sold') - obj.quantity, 0)
                )
            elif released_before and not released_after:
                MarketListing.objects.filter(pk=obj.listing_id).update(
                    units_sold=F('units_sold') + obj.quantity
                )


# ── Suppliers ─────────────────────────────────────────────────────────────────

@admin.register(SupplyCategory)
class SupplyCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'icon', 'active', 'sort_order')
    prepopulated_fields = {'slug': ('name',)}
    list_editable = ('active', 'sort_order')


@admin.register(SupplierProfile)
class SupplierProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'business_name', 'verification_status', 'documents_verified')
    list_filter = ('verification_status',)
    actions = ['approve_suppliers', 'reject_suppliers', 'suspend_suppliers']
    readonly_fields = ('documents_verified',)

    @admin.action(description='Approve selected suppliers')
    def approve_suppliers(self, request, queryset):
        queryset.update(verification_status='approved', documents_verified=True)

    @admin.action(description='Reject selected suppliers')
    def reject_suppliers(self, request, queryset):
        queryset.update(verification_status='rejected', documents_verified=False)

    @admin.action(description='Suspend selected suppliers')
    def suspend_suppliers(self, request, queryset):
        queryset.update(verification_status='suspended', documents_verified=False)


@admin.register(SupplierEnquiry)
class SupplierEnquiryAdmin(admin.ModelAdmin):
    list_display = ('title', 'client', 'supplier', 'status', 'created_at')
    list_filter = ('status',)
    raw_id_fields = ('client', 'supplier')


@admin.register(SupplierQuote)
class SupplierQuoteAdmin(admin.ModelAdmin):
    list_display = ('enquiry', 'supplier', 'total', 'status', 'created_at')
    list_filter = ('status',)


@admin.register(SupplierMessage)
class SupplierMessageAdmin(admin.ModelAdmin):
    list_display = ('enquiry', 'sender', 'recipient', 'created_at', 'edited_at', 'deleted_at')
    list_filter = ('deleted_at',)
    raw_id_fields = ('sender', 'recipient', 'enquiry')
    readonly_fields = ('edited_at', 'deleted_at', 'edit_history')

