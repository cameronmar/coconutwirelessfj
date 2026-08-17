"""
Utility functions for platform fees, invoicing, and business logic.
"""
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta
from django.utils import timezone
from django.db import transaction
from django.db.models import F

from .models import (
    PlatformSettings, PlatformFee, Invoice, InvoiceLine, InvoiceNotification, Quote, Task,
    PlatformNotice, TradieProfile, User, SupplierEnquiry, SupplierQuote, SupplierMessage,
)


def get_active_platform_settings():
    """Get the active platform settings."""
    return PlatformSettings.get_active()


def calculate_platform_fee(job_value, settings=None):
    """
    Calculate platform fee based on job value and active settings.

    Returns: (fee_rate, fee_cap, calculated_fee)
    """
    if settings is None:
        settings = get_active_platform_settings()

    if not settings:
        return Decimal('0'), Decimal('0'), Decimal('0')

    job_value = Decimal(str(job_value))
    if job_value < 0:
        # A negative job value is a data-entry error, not a real discount —
        # never persist a negative fee/credit from it.
        job_value = Decimal('0')

    large_threshold = Decimal(str(settings.large_job_threshold))
    success_rate = Decimal(str(settings.success_fee_rate))
    large_rate = Decimal(str(settings.large_job_fee_rate))
    fee_cap = Decimal(str(settings.success_fee_cap))

    if job_value > large_threshold:
        fee_rate = large_rate
        calculated_fee = (job_value * fee_rate) / Decimal('100')
    else:
        fee_rate = success_rate
        calculated_fee = (job_value * fee_rate) / Decimal('100')
        if calculated_fee > fee_cap:
            calculated_fee = fee_cap

    calculated_fee = calculated_fee.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return fee_rate, fee_cap, calculated_fee


def calculate_quote_with_platform_fee(base_price, settings=None):
    """
    When tradie includes platform fee in quote, calculate what client sees.
    
    Returns: {
        'base_price': base_price,
        'fee_rate': fee_rate,
        'fee_cap': fee_cap,
        'estimated_platform_fee': estimated_fee,
        'client_quote_total': client_sees,
    }
    """
    if settings is None:
        settings = get_active_platform_settings()
    
    if not settings:
        return None
    
    success_rate = Decimal(str(settings.success_fee_rate))
    fee_cap = Decimal(str(settings.success_fee_cap))
    large_threshold = Decimal(str(settings.large_job_threshold))
    large_rate = Decimal(str(settings.large_job_fee_rate))

    if success_rate >= 100 or large_rate >= 100:
        return None

    fee_multiplier = Decimal('1') - (success_rate / Decimal('100'))
    candidate_quote = base_price / fee_multiplier
    estimated_fee = (candidate_quote * success_rate) / Decimal('100')
    fee_rule = f'Standard {success_rate}%'

    if candidate_quote > large_threshold:
        # Large job fee applies instead of standard rate
        candidate_quote = base_price / (Decimal('1') - (large_rate / Decimal('100')))
        estimated_fee = candidate_quote - base_price
        fee_rule = f'Large job {large_rate}%'
    elif estimated_fee > fee_cap:
        candidate_quote = base_price + fee_cap
        estimated_fee = fee_cap
        fee_rule = f'Standard {success_rate}% / FJD ${fee_cap} cap'

    client_total = candidate_quote.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    estimated_fee = estimated_fee.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    return {
        'base_price': base_price,
        'fee_rate': success_rate,
        'fee_cap': fee_cap,
        'estimated_platform_fee': estimated_fee,
        'client_quote_total': client_total,
        'estimated_tradie_take_home': base_price,
        'fee_rule_applied': fee_rule,
        'large_job_threshold': large_threshold,
        'large_job_fee_rate': large_rate,
    }


def calculate_quote_from_take_home(minimum_take_home, settings=None):
    """Reverse-calculate the lowest customer-facing quote for a desired take-home amount."""
    if settings is None:
        settings = get_active_platform_settings()

    if not settings or minimum_take_home <= 0:
        return None

    success_rate = Decimal(str(settings.success_fee_rate))
    fee_cap = Decimal(str(settings.success_fee_cap))
    large_threshold = Decimal(str(settings.large_job_threshold))
    large_rate = Decimal(str(settings.large_job_fee_rate))

    if success_rate >= 100 or large_rate >= 100:
        return None

    standard_multiplier = Decimal('1') - (success_rate / Decimal('100'))
    candidate_quote = minimum_take_home / standard_multiplier
    estimated_fee = (candidate_quote * success_rate) / Decimal('100')
    fee_rule = f'Standard {success_rate}%'

    if candidate_quote > large_threshold:
        candidate_quote = minimum_take_home / (Decimal('1') - (large_rate / Decimal('100')))
        estimated_fee = candidate_quote - minimum_take_home
        fee_rule = f'Large job {large_rate}%'
    elif estimated_fee > fee_cap:
        candidate_quote = minimum_take_home + fee_cap
        estimated_fee = fee_cap
        fee_rule = f'Standard {success_rate}% / FJD ${fee_cap} cap'

    client_total = candidate_quote.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    estimated_fee = estimated_fee.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    provider_take_home = (client_total - estimated_fee).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    return {
        'minimum_take_home_amount': minimum_take_home,
        'customer_facing_quote': client_total,
        'estimated_platform_fee': estimated_fee,
        'estimated_provider_take_home': provider_take_home,
        'fee_rule_applied': fee_rule,
        'success_fee_rate_at_quote_time': success_rate,
        'success_fee_cap_at_quote_time': fee_cap,
        'large_job_threshold_at_quote_time': large_threshold,
        'large_job_fee_rate_at_quote_time': large_rate,
    }


def calculate_quote_without_platform_fee(quote_total, settings=None):
    """
    When tradie absorbs the platform fee (include_platform_fee=False).
    
    Returns: {
        'base_price': quote_total,  # What tradie wants
        'fee_rate': fee_rate,
        'fee_cap': fee_cap,
        'estimated_platform_fee': estimated_fee,
        'client_quote_total': quote_total,  # Client sees this
        'estimated_tradie_take_home': tradie_gets,
    }
    """
    if settings is None:
        settings = get_active_platform_settings()
    
    if not settings:
        return None
    
    fee_rate, fee_cap, estimated_fee = calculate_platform_fee(quote_total, settings)
    tradie_gets = quote_total - estimated_fee

    return {
        'base_price': quote_total,
        'fee_rate': fee_rate,
        'fee_cap': fee_cap,
        'estimated_platform_fee': estimated_fee.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
        'client_quote_total': quote_total,
        'estimated_tradie_take_home': tradie_gets.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
    }


def _q2(value):
    return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def calculate_market_price_per_unit(total_take_home, units, vat_rate=None, settings=None):
    """
    Market listing calculator, take-home → price direction. Given the
    seller's desired TOTAL take-home across the whole batch, the number of
    units/serves, and an optional VAT rate, compute the buyer-facing price
    per unit — as a full step-by-step breakdown so the seller can see exactly
    how the price was built up:
        1. take_home_total                              (what they entered)
        2. + vat_amount   -> subtotal_after_vat = take_home_total / (1 - vat_rate/100)
        3. + fee_amount   -> total_price        = subtotal_after_vat / (1 - fee_rate/100)
        4. ÷ units        -> price_per_unit     = total_price / units

    Uses the flat market_fee_rate — a separate rate from the job/task
    success_fee_rate — since Market pricing doesn't have a natural "job
    value" to tier on. Dividing by (1 - vat)(1 - fee) in two steps (rather
    than one combined multiplier) is mathematically identical — division is
    associative — but lets the UI show VAT and the platform fee as distinct
    line items.

    Returns a dict of Decimals (all money values quantized to cents), or
    None if the inputs/settings are invalid.
    """
    if settings is None:
        settings = get_active_platform_settings()
    if not settings or total_take_home is None or total_take_home <= 0 or not units or units <= 0:
        return None

    fee_rate = Decimal(str(settings.market_fee_rate))
    vat_rate = Decimal(str(vat_rate)) if vat_rate else Decimal('0')
    vat_multiplier = Decimal('1') - (vat_rate / Decimal('100'))
    fee_multiplier = Decimal('1') - (fee_rate / Decimal('100'))
    if vat_multiplier <= 0 or fee_multiplier <= 0:
        return None

    # Each running total is quantized to cents as soon as it's derived, and
    # every "+" line item is then taken as the difference between two
    # already-quantized totals (rather than independently rounding each
    # piece from unrounded intermediate math) — so the displayed breakdown
    # always adds up exactly to the cent, with no off-by-a-cent rows.
    take_home_total = _q2(total_take_home)
    subtotal_after_vat = _q2(take_home_total / vat_multiplier)
    vat_amount = subtotal_after_vat - take_home_total
    total_price = _q2(subtotal_after_vat / fee_multiplier)
    fee_amount = total_price - subtotal_after_vat
    price_per_unit = _q2(total_price / units)
    take_home_per_unit = _q2(take_home_total / units)

    return {
        'take_home_total':     take_home_total,
        'take_home_per_unit':  take_home_per_unit,
        'vat_rate':            vat_rate,
        'vat_amount':          vat_amount,
        'subtotal_after_vat':  subtotal_after_vat,
        'fee_rate':            fee_rate,
        'fee_amount':          fee_amount,
        'total_price':         total_price,
        'units':               units,
        'price_per_unit':      price_per_unit,
    }


def calculate_market_take_home(price_per_unit, units, vat_rate=None, settings=None):
    """
    Market listing calculator, price → take-home direction (working
    backwards, as requested — the seller can enter a price per unit directly
    instead of a take-home target). Given the price per unit and the number
    of units/serves, compute the full breakdown the other way:
        1. price_per_unit × units -> total_price
        2. - fee_amount           -> subtotal_after_fee = total_price - total_price * fee_rate/100
        3. - vat_amount           -> take_home_total     = subtotal_after_fee * (1 - vat_rate/100)
        4. ÷ units                -> take_home_per_unit

    Returns a dict of Decimals (all money values quantized to cents), or
    None if the inputs/settings are invalid.
    """
    if settings is None:
        settings = get_active_platform_settings()
    if not settings or price_per_unit is None or price_per_unit <= 0 or not units or units <= 0:
        return None

    fee_rate = Decimal(str(settings.market_fee_rate))
    vat_rate = Decimal(str(vat_rate)) if vat_rate else Decimal('0')
    vat_multiplier = Decimal('1') - (vat_rate / Decimal('100'))

    # Same running-total-then-derive-the-delta approach as the take-home
    # direction above, so the displayed breakdown always adds up exactly.
    price_per_unit = _q2(price_per_unit)
    total_price = _q2(price_per_unit * units)
    fee_amount = _q2(total_price * fee_rate / Decimal('100'))
    subtotal_after_fee = total_price - fee_amount
    take_home_total = _q2(subtotal_after_fee * vat_multiplier)
    vat_amount = subtotal_after_fee - take_home_total
    take_home_per_unit = _q2(take_home_total / units)

    return {
        'price_per_unit':      price_per_unit,
        'units':               units,
        'total_price':         total_price,
        'fee_rate':            fee_rate,
        'fee_amount':          fee_amount,
        'subtotal_after_fee':  subtotal_after_fee,
        'vat_rate':            vat_rate,
        'vat_amount':          vat_amount,
        'take_home_total':     take_home_total,
        'take_home_per_unit':  take_home_per_unit,
    }


def create_platform_fee_for_task(task, final_job_value):
    """
    Create a PlatformFee record when a task is completed. Applies (and
    consumes) any discount selected on the accepted quote — founding member
    credit or a promo code — capped so it can never exceed the fee itself.
    Recalculated fresh here rather than trusting the quote-time estimate,
    since the credit balance or promo validity may have changed since then.
    """
    if not task.assigned_tradie:
        return None

    settings = get_active_platform_settings()
    if not settings:
        return None

    fee_rate, fee_cap, gross_fee_amount = calculate_platform_fee(final_job_value, settings)

    with transaction.atomic():
        discount_amount = Decimal('0.00')
        accepted_quote = task.quotes.filter(status=Quote.STATUS_ACCEPTED).first()
        if accepted_quote:
            if accepted_quote.used_founding_credit:
                # Locked so two of this tradie's jobs completing at the same
                # time can't both read the same starting balance and both
                # spend it (last-write-wins would otherwise double-spend it).
                profile = (
                    TradieProfile.objects.select_for_update()
                    .filter(user=task.assigned_tradie)
                    .first()
                )
                if profile and profile.is_founding_member and profile.founding_member_credit_balance > 0:
                    discount_amount = min(profile.founding_member_credit_balance, gross_fee_amount)
                    profile.founding_member_credit_balance -= discount_amount
                    profile.save(update_fields=['founding_member_credit_balance'])
            elif accepted_quote.promo_code_id:
                # Locked for the same reason, and re-checked after the lock
                # since is_valid_now()/max_uses could have changed between
                # quote time and now.
                from .models import PromoCode
                promo = PromoCode.objects.select_for_update().filter(pk=accepted_quote.promo_code_id).first()
                if promo and promo.is_valid_now():
                    discount_amount = promo.calculate_discount(gross_fee_amount)
                    promo.times_used = F('times_used') + 1
                    promo.save(update_fields=['times_used'])

        fee_amount = gross_fee_amount - discount_amount

        platform_fee = PlatformFee.objects.create(
            task=task,
            tradie=task.assigned_tradie,
            final_job_value=final_job_value,
            fee_rate=fee_rate,
            fee_cap=fee_cap,
            gross_fee_amount=gross_fee_amount,
            discount_amount=discount_amount,
            fee_amount=fee_amount,
            status=PlatformFee.STATUS_PENDING,
        )

    return platform_fee


def get_eligible_platform_fees(tradie, period_start, period_end):
    """
    PlatformFees eligible for invoicing for `tradie` within [period_start, period_end]:
    pending, linked to a completed task with a confirmed final job value,
    and completed within the period. (status=pending already excludes fees
    that are invoiced, paid, or waived, and fees already linked to an invoice.)
    """
    return PlatformFee.objects.filter(
        tradie=tradie,
        status=PlatformFee.STATUS_PENDING,
        task__status=Task.STATUS_COMPLETED,
        task__final_job_value__isnull=False,
        task__completed_at__date__gte=period_start,
        task__completed_at__date__lte=period_end,
    ).select_related('task').order_by('task__completed_at')


def get_providers_with_pending_fees(period_start, period_end):
    """
    Group eligible pending PlatformFees by provider for the given period.
    Returns a list of {'tradie': User, 'fees': [...], 'total': Decimal}.
    """
    fees = PlatformFee.objects.filter(
        status=PlatformFee.STATUS_PENDING,
        task__status=Task.STATUS_COMPLETED,
        task__final_job_value__isnull=False,
        task__completed_at__date__gte=period_start,
        task__completed_at__date__lte=period_end,
    ).select_related('tradie', 'task').order_by('tradie__first_name', 'tradie__last_name', 'task__completed_at')

    grouped = {}
    order = []
    for fee in fees:
        if fee.tradie_id not in grouped:
            grouped[fee.tradie_id] = {'tradie': fee.tradie, 'fees': [], 'total': Decimal('0')}
            order.append(fee.tradie_id)
        grouped[fee.tradie_id]['fees'].append(fee)
        grouped[fee.tradie_id]['total'] += fee.fee_amount

    return [grouped[tid] for tid in order]


def fee_rule_label(fee, settings=None):
    """Describe which fee rule applied to a PlatformFee, e.g. 'Standard 7.5%' or 'Large job 3%'."""
    if settings is None:
        settings = get_active_platform_settings()
    rate_str = f'{fee.fee_rate:.2f}'.rstrip('0').rstrip('.')
    if settings and fee.fee_rate == settings.large_job_fee_rate and settings.large_job_fee_rate != settings.success_fee_rate:
        return f'Large job {rate_str}%'
    return f'Standard {rate_str}%'


def build_invoice_line_description(fee, settings=None):
    """Build the auto-generated line description for a completed-job platform fee."""
    task = fee.task
    completed = task.completed_at
    completed_str = f'{completed.day} {completed.strftime("%B %Y")}' if completed else 'Not recorded'
    rule = fee_rule_label(fee, settings)

    lines = [
        f'Job: {task.title}',
        f'Completed: {completed_str}',
        f'Final job value: FJD ${fee.final_job_value:.2f}',
    ]
    if rule.startswith('Large job'):
        lines.append(f'Fee rule: {rule}')
    lines.append(f'The Coconut Wireless Network fee: FJD ${fee.fee_amount:.2f}')
    return '\n'.join(lines)


def create_invoice_with_lines(tradie, period_start, period_end, fee_ids, manual_lines=None, due_days=7):
    """
    Create a draft invoice for `tradie` covering [period_start, period_end],
    with one InvoiceLine per selected (still-pending) PlatformFee — marking
    each as invoiced — plus any manual adjustment lines.
    """
    settings = get_active_platform_settings()
    total = Decimal('0')

    with transaction.atomic():
        # Locked, and re-filtered on status=PENDING inside the transaction
        # (not fetched beforehand) — two overlapping invoice-generation runs
        # selecting the same fees must not both flip them to INVOICED and
        # both bill them. Whichever transaction gets here second will simply
        # see these rows no longer match status=PENDING once it acquires
        # the lock, and skip them.
        fees = list(
            PlatformFee.objects.select_for_update()
            .filter(pk__in=fee_ids, tradie=tradie, status=PlatformFee.STATUS_PENDING)
            .select_related('task')
        )

        invoice = Invoice.objects.create(
            tradie=tradie,
            invoice_number=generate_invoice_number(tradie),
            period_start=period_start,
            period_end=period_end,
            total_amount=Decimal('0'),
            status=Invoice.STATUS_DRAFT,
            due_date=timezone.localdate() + timedelta(days=due_days),
        )

        for fee in fees:
            InvoiceLine.objects.create(
                invoice=invoice,
                platform_fee=fee,
                task=fee.task,
                description=build_invoice_line_description(fee, settings),
                final_job_value=fee.final_job_value,
                fee_rate=fee.fee_rate,
                amount=fee.fee_amount,
            )
            total += fee.fee_amount
            fee.status = PlatformFee.STATUS_INVOICED
            fee.save(update_fields=['status'])

        for manual in (manual_lines or []):
            description = (manual.get('description') or '').strip()
            amount = manual.get('amount')
            if not description or amount in (None, ''):
                continue
            amount = Decimal(str(amount))
            InvoiceLine.objects.create(
                invoice=invoice,
                description=description,
                amount=amount,
            )
            total += amount

        invoice.total_amount = total
        invoice.save(update_fields=['total_amount'])

    return invoice


def send_invoice_notifications(invoice):
    """
    Mark the invoice as sent and notify the provider via in-platform message,
    email, and an SMS log entry (no SMS gateway required — admin can send the
    SMS manually using the logged text).

    Returns True if the email was delivered (or there was no address to send
    to), False if the email attempt failed. The invoice is still marked sent
    and the in-platform/SMS log entries are still created either way — a mail
    server outage shouldn't block the admin from progressing an invoice.
    """
    from django.conf import settings as django_settings
    from django.core.mail import send_mail

    tradie = invoice.tradie
    lines = invoice.lines.all()

    period_str = ''
    if invoice.period_start and invoice.period_end:
        period_str = f'{invoice.period_start.strftime("%d %B %Y")} to {invoice.period_end.strftime("%d %B %Y")}'

    job_lines = '\n'.join(
        f'- {(line.task.title if line.task else line.description.splitlines()[0])}: FJD ${line.amount:.2f}'
        for line in lines
    )

    subject = f'The Coconut Wireless Network invoice {invoice.invoice_number}'

    body = (
        f'Bula {tradie.first_name},\n\n'
        f'Your Coconut Wireless Network invoice {invoice.invoice_number} has been issued '
        f'for the period {period_str}.\n\n'
        f'Invoice total: FJD ${invoice.total_amount:.2f}\n'
        f'Due date: {invoice.due_date.strftime("%d %B %Y")}\n\n'
        f'Jobs included:\n{job_lines}\n\n'
        f'Please arrange payment by bank transfer or M-PAiSA.\n\n'
        f'You can view the full invoice in your Billing section.\n\n'
        f'Vinaka,\nThe Coconut Wireless Network Team'
    )

    sms_body = (
        f'The Coconut Wireless Network invoice {invoice.invoice_number} issued. '
        f'Amount: FJD ${invoice.total_amount:.2f}. '
        f'Due: {invoice.due_date.strftime("%d %B %Y")}. '
        f'Check your Coconut Wireless Network messages/email. Vinaka.'
    )

    InvoiceNotification.objects.create(
        invoice=invoice, recipient=tradie, channel=InvoiceNotification.CHANNEL_IN_PLATFORM,
        subject=subject, body=body,
    )

    email_sent = True
    if tradie.email:
        try:
            send_mail(
                subject, body,
                getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@coconutwireless.fj'),
                [tradie.email],
                fail_silently=False,
            )
        except Exception as exc:
            email_sent = False
            import sys
            import traceback
            print(f'send_invoice_notifications: email send failed: {exc!r}', flush=True)
            traceback.print_exc()
            sys.stderr.flush()
            try:
                import sentry_sdk
                sentry_sdk.capture_exception(exc)
            except ImportError:
                pass

    InvoiceNotification.objects.create(
        invoice=invoice, recipient=tradie, channel=InvoiceNotification.CHANNEL_EMAIL,
        subject=subject, body=body,
    )

    InvoiceNotification.objects.create(
        invoice=invoice, recipient=tradie, channel=InvoiceNotification.CHANNEL_SMS,
        subject=subject, body=sms_body,
    )

    invoice.status = Invoice.STATUS_SENT
    invoice.sent_at = timezone.now()
    invoice.save(update_fields=['status', 'sent_at'])

    return email_sent


def create_weekly_invoices(period_start, period_end):
    """
    Generate one DRAFT invoice per provider for all eligible pending platform
    fees within [period_start, period_end]. Invoices are NOT sent automatically —
    admin must review and send each one.
    """
    invoices = []
    for entry in get_providers_with_pending_fees(period_start, period_end):
        invoice = create_invoice_with_lines(
            tradie=entry['tradie'],
            period_start=period_start,
            period_end=period_end,
            fee_ids=[f.pk for f in entry['fees']],
        )
        invoices.append(invoice)
    return invoices


def generate_invoice_number(tradie):
    """Generate a unique invoice number."""
    from django.utils import timezone
    today = timezone.localdate()
    tradie_id = str(tradie.id).zfill(5)
    date_str = today.strftime('%Y%m%d')
    # Format: INV-20260610-00123-001 (date-tradie_id-sequence)
    count = Invoice.objects.filter(tradie=tradie, created_at__date=today).count() + 1
    return f'INV-{date_str}-{tradie_id}-{str(count).zfill(3)}'


def is_tradie_payment_restricted(tradie):
    """
    Check if tradie has overdue invoices > 14 days old.
    If so, restrict them from submitting new quotes.
    """
    from django.utils import timezone
    from datetime import timedelta

    today = timezone.localdate()
    cutoff_date = today - timedelta(days=14)
    
    overdue_old = Invoice.objects.filter(
        tradie=tradie,
        status__in=[Invoice.STATUS_SENT, Invoice.STATUS_OVERDUE],
        due_date__lt=cutoff_date,
    ).exists()
    
    return overdue_old


def get_tradie_unpaid_invoices(tradie):
    """Get all unpaid invoices for a tradie."""
    return Invoice.objects.filter(
        tradie=tradie,
        status__in=[Invoice.STATUS_SENT, Invoice.STATUS_OVERDUE],
    ).order_by('-due_date')


def get_tradie_billing_summary(tradie):
    """Get billing summary for tradie dashboard."""
    from django.utils import timezone
    today = timezone.localdate()
    week_ago = today - timedelta(days=7)
    
    # Current week fees (pending/invoiced)
    week_fees = PlatformFee.objects.filter(
        tradie=tradie,
        created_at__date__gte=week_ago,
        status__in=[PlatformFee.STATUS_PENDING, PlatformFee.STATUS_INVOICED],
    )
    current_week_total = sum(f.fee_amount for f in week_fees) or Decimal('0')
    
    # Unpaid invoices
    unpaid_invoices = get_tradie_unpaid_invoices(tradie)
    unpaid_total = sum(i.total_amount for i in unpaid_invoices) or Decimal('0')
    
    # Next invoice date (7 days from today)
    next_invoice_date = today + timedelta(days=7)
    
    # Check if restricted
    is_restricted = is_tradie_payment_restricted(tradie)
    
    return {
        'current_week_fees': current_week_total,
        'unpaid_invoices_count': unpaid_invoices.count(),
        'unpaid_invoices_total': unpaid_total,
        'next_invoice_date': next_invoice_date,
        'is_payment_restricted': is_restricted,
        'unpaid_invoices': unpaid_invoices,
    }


def send_welcome_notice(user):
    """
    Send a welcome email to a newly registered user and also log an in-platform
    PlatformNotice so it shows up in their in-app Notices inbox (not just email).
    Best-effort only — registration must succeed even if the email fails to send,
    so failures here are swallowed rather than raised.
    """
    from django.conf import settings as django_settings
    from django.core.mail import send_mail

    if user.role == User.ROLE_TRADIE:
        subject = 'Bula Vinaka and welcome to the Coconut Wireless Network!'
        body = (
            f'Bula {user.first_name},\n\n'
            f'Thank you for joining the Coconut Wireless Network as one of our local professionals.\n\n'
            f'This platform was created to help skilled people across Fiji connect with clients, '
            f'find more work, build their reputation, and grow their businesses.\n\n'
            f'Your documents are now pending review — once verified, you can start quoting on tasks. '
            f'As we are just starting out, the number of available tasks may be limited while we '
            f'build awareness and grow our client network. We kindly ask for your patience and '
            f'support during these early stages.\n\n'
            f'By completing your profile, responding professionally to tasks, providing fair quotes, '
            f'and delivering quality service, you will help build trust in the platform and create '
            f'more opportunities for everyone.\n\n'
            f'Your participation is an important part of making the Coconut Wireless Network successful.\n\n'
            f"Let's build the network together and create more opportunities for local professionals "
            f'across Fiji.\n'
            f'Skilled. Reliable. Local.\n\n'
            f'Vinaka,\nThe Coconut Wireless Network Team'
        )
    else:
        subject = 'Bula Vinaka and welcome to the Coconut Wireless Network!'
        body = (
            f'Bula {user.first_name},\n\n'
            f'Thank you for joining the Coconut Wireless Network.\n\n'
            f'We created this platform to make it easier for people across Fiji to connect with '
            f'trusted local professionals and get the help they need.\n\n'
            f'As we are just starting out, you may notice that our network is still growing. We '
            f'kindly ask for your patience as more local professionals join, more services become '
            f'available, and we continue improving the platform.\n\n'
            f'Every task you post and every piece of feedback you share helps us build a stronger '
            f'and more useful network for communities across Fiji.\n\n'
            f'Thank you for supporting local skills and local livelihoods.\n\n'
            f"Let's build the Coconut Wireless Network together.\n"
            f'Local jobs. Local people. Stronger communities.\n\n'
            f'Vinaka,\nThe Coconut Wireless Network Team'
        )

    # Log an in-platform notice (always — this is what the user sees in their
    # Notices inbox) and a separate email-channel record for the audit trail,
    # matching the same per-channel logging pattern as invoice notifications.
    PlatformNotice.objects.create(
        recipient=user,
        notice_type=PlatformNotice.TYPE_WELCOME,
        channel=PlatformNotice.CHANNEL_IN_PLATFORM,
        subject=subject,
        body=body,
    )

    if user.email:
        try:
            send_mail(
                subject, body,
                getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@coconutwireless.fj'),
                [user.email],
                fail_silently=False,
            )
            PlatformNotice.objects.create(
                recipient=user,
                notice_type=PlatformNotice.TYPE_WELCOME,
                channel=PlatformNotice.CHANNEL_EMAIL,
                subject=subject,
                body=body,
            )
        except Exception as exc:
            import sys
            import traceback
            print(f'send_welcome_notice: email send failed: {exc!r}', flush=True)
            traceback.print_exc()
            sys.stderr.flush()
            try:
                import sentry_sdk
                sentry_sdk.capture_exception(exc)
            except ImportError:
                pass


def notify_admin(subject, body, reply_to=None):
    """
    Send an operational notification (support contact click, new tradie
    pending approval, etc.) to settings.ADMIN_EMAIL. Best-effort and silent
    if ADMIN_EMAIL isn't configured — this must never break the user-facing
    request that triggered it.

    Pass reply_to=[email] (e.g. a contact form submitter's address) so the
    admin can just hit Reply in their email client instead of copying it out.
    """
    from django.conf import settings as django_settings
    from django.core.mail import EmailMessage

    admin_email = getattr(django_settings, 'ADMIN_EMAIL', '')
    if not admin_email:
        print(f'notify_admin: ADMIN_EMAIL not configured, skipping notification: {subject}', flush=True)
        return False

    try:
        EmailMessage(
            subject=subject,
            body=body,
            from_email=getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@coconutwireless.fj'),
            to=[admin_email],
            reply_to=reply_to or None,
        ).send(fail_silently=False)
        return True
    except Exception as exc:
        import sys
        import traceback
        print(f'notify_admin: send failed: {exc!r}', flush=True)
        traceback.print_exc()
        sys.stderr.flush()
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
        except ImportError:
            pass
        return False


def send_fcm_push(recipient, title, body):
    """
    Send a Firebase Cloud Messaging push notification to the user's Android
    device if they have a stored FCM token (i.e. they've used the app and the
    token registration succeeded). Best-effort — never raises; failures are
    logged to stderr/Sentry but the calling code always continues.
    """
    from django.conf import settings as django_settings

    token = getattr(recipient, 'fcm_token', '').strip()
    if not token:
        return False

    credentials_json = getattr(django_settings, 'FIREBASE_CREDENTIALS_JSON', '').strip()
    if not credentials_json:
        return False

    try:
        import json as _json
        import firebase_admin
        from firebase_admin import credentials, messaging

        if not firebase_admin._apps:
            cred = credentials.Certificate(_json.loads(credentials_json))
            firebase_admin.initialize_app(cred)

        messaging.send(messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            token=token,
        ))
        return True
    except Exception as exc:
        import sys
        import traceback
        print(f'send_fcm_push: failed for user {recipient.pk}: {exc!r}', flush=True)
        traceback.print_exc()
        sys.stderr.flush()
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
        except ImportError:
            pass
        return False


def _send_email_notice(recipient, subject, body, notice_type):
    from django.conf import settings as django_settings
    from django.core.mail import send_mail

    if not recipient.email:
        return False
    try:
        send_mail(
            subject, body,
            getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@coconutwireless.fj'),
            [recipient.email],
            fail_silently=False,
        )
        PlatformNotice.objects.create(
            recipient=recipient, notice_type=notice_type,
            channel=PlatformNotice.CHANNEL_EMAIL, subject=subject, body=body,
        )
        return True
    except Exception as exc:
        import sys
        import traceback
        print(f'_send_email_notice: send failed: {exc!r}', flush=True)
        traceback.print_exc()
        sys.stderr.flush()
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
        except ImportError:
            pass
        return False


def notify_client_new_quote(quote):
    """
    Notify a client that a new quote was submitted on their task. Always logs
    an in-platform notice (this is about their own task); email is sent only
    if the client has opted in (User.notify_email_new_quote).
    """
    task = quote.task
    client = task.client
    subject = f'New quote on "{task.title}"'
    body = (
        f'Bula {client.first_name},\n\n'
        f'{quote.tradie.full_name} sent you a quote of FJD ${quote.price} for "{task.title}".\n\n'
        f'{quote.message}\n\n'
        f'Log in to view the full quote and respond.\n\n'
        f'Vinaka,\nThe Coconut Wireless Network Team'
    )
    PlatformNotice.objects.create(
        recipient=client, notice_type=PlatformNotice.TYPE_NEW_QUOTE,
        channel=PlatformNotice.CHANNEL_IN_PLATFORM, subject=subject, body=body,
    )
    if client.notify_email_new_quote:
        _send_email_notice(client, subject, body, PlatformNotice.TYPE_NEW_QUOTE)
    send_fcm_push(client, subject, f'{quote.tradie.full_name} sent a quote of FJD ${quote.price} for "{task.title}".')


def notify_message_recipient(message):
    """
    Email a user that they've received a new message. Messages already live
    in the in-app inbox (Messages), so no in-platform notice is logged here —
    email is purely the optional extra channel, gated by
    User.notify_email_new_message.
    """
    recipient = message.recipient
    subject = f'New message from {message.sender.full_name}'
    body = (
        f'Bula {recipient.first_name},\n\n'
        f'{message.sender.full_name} sent you a message about "{message.task.title}":\n\n'
        f'{message.body}\n\n'
        f'Log in to reply.\n\n'
        f'Vinaka,\nThe Coconut Wireless Network Team'
    )
    if recipient.notify_email_new_message:
        _send_email_notice(recipient, subject, body, PlatformNotice.TYPE_NEW_MESSAGE)
    send_fcm_push(recipient, subject, f'{message.sender.full_name}: {message.body[:80]}')


def notify_matching_tradies_new_job(task):
    """
    Notify local professionals whose trades and service towns match a newly
    posted task. Opt-in only (User.notify_email_new_job_match defaults to
    False) — both the in-platform notice and the email are gated by the same
    preference, since neither has any other natural home in the UI and an
    unwanted in-app notice would be just as unwelcome as an unwanted email.
    """
    task_categories = {task.category} if task.category else set()
    task_categories |= set(task.categories.values_list('slug', flat=True))
    if not task_categories:
        return

    subject = f'New job posted: "{task.title}"'
    profiles = (
        TradieProfile.objects.filter(user__notify_email_new_job_match=True)
        .exclude(user=task.client)  # local pros can post jobs too — don't notify them about their own posting
        .select_related('user')
    )
    for profile in profiles:
        if not profile.can_quote():
            continue
        if task.town not in (profile.service_towns or []):
            continue
        if not (task_categories & set(profile.trades or [])):
            continue

        body = (
            f'Bula {profile.user.first_name},\n\n'
            f'A new job matching your trades was just posted in {task.get_town_display()}:\n\n'
            f'"{task.title}"\n{task.description[:200]}\n\n'
            f'Budget: FJD ${task.budget}\n\n'
            f'Log in to view the task and send a quote.\n\n'
            f'Vinaka,\nThe Coconut Wireless Network Team'
        )
        PlatformNotice.objects.create(
            recipient=profile.user, notice_type=PlatformNotice.TYPE_NEW_JOB_MATCH,
            channel=PlatformNotice.CHANNEL_IN_PLATFORM, subject=subject, body=body,
        )
        _send_email_notice(profile.user, subject, body, PlatformNotice.TYPE_NEW_JOB_MATCH)
        send_fcm_push(profile.user, subject, f'New job in {task.get_town_display()}: {task.title[:60]}')


def notify_client_migrated_to_tradie(user):
    """
    Notify a user whose account an admin has just migrated from client to
    local professional (tradie) — e.g. someone who mistakenly registered as
    a client. Not gated by an email preference (unlike the opt-in notices
    above): this is a one-off account-level change the user needs to know
    about regardless, not a recurring activity notification.
    """
    subject = 'Your Coconut Wireless Network account is now a Local Professional account'
    body = (
        f'Bula {user.first_name},\n\n'
        f'Your Coconut Wireless Network account has been switched from a client account '
        f'to a local professional account.\n\n'
        f'Please log in using the same email and password as before — you will now land on '
        f'the local professional side of the platform, where you can browse open jobs, send '
        f'quotes, and sell on the Market.\n\n'
        f'Please take a moment to complete your local professional profile (business name, '
        f'experience, and verification documents) so clients can find and quote you with confidence.\n\n'
        f'Vinaka,\nThe Coconut Wireless Network Team'
    )
    PlatformNotice.objects.create(
        recipient=user, notice_type=PlatformNotice.TYPE_ACCOUNT_MIGRATED,
        channel=PlatformNotice.CHANNEL_IN_PLATFORM, subject=subject, body=body,
    )
    _send_email_notice(user, subject, body, PlatformNotice.TYPE_ACCOUNT_MIGRATED)


def notify_seller_new_market_order(order):
    """Notify a seller that a new Market order was placed. Always logs an
    in-platform notice; email is sent only if opted in (default on)."""
    listing = order.listing
    seller = listing.seller
    subject = f'New order on "{listing.title}"'
    body = (
        f'Bula {seller.first_name},\n\n'
        f'{order.buyer.full_name} ordered {order.quantity} × "{listing.title}" '
        f'for FJD ${order.total_price}, requested for {order.requested_date.strftime("%d %B %Y")}.\n\n'
        + (
            'This listing is set to require your approval — log in to accept or decline the order.\n\n'
            if listing.order_mode == listing.ORDER_MODE_APPROVAL
            else 'This order was auto-accepted based on your listing settings.\n\n'
        )
        + f'Vinaka,\nThe Coconut Wireless Network Team'
    )
    PlatformNotice.objects.create(
        recipient=seller, notice_type=PlatformNotice.TYPE_NEW_MARKET_ORDER,
        channel=PlatformNotice.CHANNEL_IN_PLATFORM, subject=subject, body=body,
    )
    if seller.notify_email_new_market_order:
        _send_email_notice(seller, subject, body, PlatformNotice.TYPE_NEW_MARKET_ORDER)
    send_fcm_push(seller, subject, f'{order.buyer.full_name} ordered {order.quantity} × "{listing.title}".')


def notify_buyer_market_order_update(order):
    """Notify a buyer that their Market order was accepted or declined.
    Always logs an in-platform notice; email is sent only if opted in
    (default on)."""
    listing = order.listing
    buyer = order.buyer
    status_label = order.get_status_display()
    subject = f'Your order on "{listing.title}" was {status_label.lower()}'
    body = (
        f'Bula {buyer.first_name},\n\n'
        f'Your order of {order.quantity} × "{listing.title}" (FJD ${order.total_price}) '
        f'has been {status_label.lower()} by {listing.seller.full_name}.\n\n'
        f'Log in to view the order details.\n\n'
        f'Vinaka,\nThe Coconut Wireless Network Team'
    )
    PlatformNotice.objects.create(
        recipient=buyer, notice_type=PlatformNotice.TYPE_MARKET_ORDER_UPDATE,
        channel=PlatformNotice.CHANNEL_IN_PLATFORM, subject=subject, body=body,
    )
    if buyer.notify_email_market_order_update:
        _send_email_notice(buyer, subject, body, PlatformNotice.TYPE_MARKET_ORDER_UPDATE)
    send_fcm_push(buyer, subject, f'Your order of "{listing.title}" was {status_label.lower()}.')


def notify_supplier_new_enquiry(enquiry):
    """Notify supplier of a new enquiry from a client."""
    supplier = enquiry.supplier
    subject = f'New enquiry: "{enquiry.title}"'
    body = (
        f'Bula {supplier.first_name},\n\n'
        f'{enquiry.client.full_name} has sent you an enquiry about "{enquiry.title}".\n\n'
        f'{enquiry.description}\n\n'
        f'Delivery/pickup location: {enquiry.get_town_display()}\n\n'
        f'Log in to view the enquiry and send a quote.\n\nVinaka,\nThe Coconut Wireless Network Team'
    )
    PlatformNotice.objects.create(
        recipient=supplier, notice_type=PlatformNotice.TYPE_NEW_QUOTE,
        channel=PlatformNotice.CHANNEL_IN_PLATFORM, subject=subject, body=body,
    )
    _send_email_notice(supplier, subject, body, PlatformNotice.TYPE_NEW_QUOTE)
    send_fcm_push(supplier, subject, f'{enquiry.client.full_name} sent an enquiry: {enquiry.title[:60]}')


def notify_client_new_supplier_quote(quote):
    """Notify client that the supplier sent a quote on their enquiry."""
    client = quote.enquiry.client
    subject = f'Quote received for "{quote.enquiry.title}"'
    body = (
        f'Bula {client.first_name},\n\n'
        f'{quote.supplier.full_name} sent you a quote of FJD ${quote.total:.2f} for "{quote.enquiry.title}".\n\n'
        + (f'{quote.message}\n\n' if quote.message else '')
        + f'Log in to view the full itemised quote and respond.\n\nVinaka,\nThe Coconut Wireless Network Team'
    )
    PlatformNotice.objects.create(
        recipient=client, notice_type=PlatformNotice.TYPE_NEW_QUOTE,
        channel=PlatformNotice.CHANNEL_IN_PLATFORM, subject=subject, body=body,
    )
    _send_email_notice(client, subject, body, PlatformNotice.TYPE_NEW_QUOTE)
    send_fcm_push(client, subject, f'{quote.supplier.full_name} quoted FJD ${quote.total:.2f} for "{quote.enquiry.title}".')


def notify_supplier_quote_response(quote):
    """Notify supplier that the client responded to their quote (accepted/modify/rejected)."""
    supplier = quote.supplier
    status_label = quote.get_status_display()
    subject = f'Your quote was {status_label.lower()}: "{quote.enquiry.title}"'
    body = (
        f'Bula {supplier.first_name},\n\n'
        f'{quote.enquiry.client.full_name} has {status_label.lower()} your quote of FJD ${quote.total:.2f} '
        f'for "{quote.enquiry.title}".\n\n'
        + (f'Modification requested:\n{quote.modification_note}\n\n' if quote.modification_note else '')
        + f'Log in to view the full enquiry.\n\nVinaka,\nThe Coconut Wireless Network Team'
    )
    PlatformNotice.objects.create(
        recipient=supplier, notice_type=PlatformNotice.TYPE_NEW_QUOTE,
        channel=PlatformNotice.CHANNEL_IN_PLATFORM, subject=subject, body=body,
    )
    _send_email_notice(supplier, subject, body, PlatformNotice.TYPE_NEW_QUOTE)
    send_fcm_push(supplier, subject, f'Quote {status_label.lower()} by {quote.enquiry.client.full_name}.')
