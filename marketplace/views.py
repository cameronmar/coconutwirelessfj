"""
views.py — all marketplace views.

Privacy rule enforced here:
  PrivateReview is NEVER imported into this file.
  It only lives in admin.py.
"""
from django.contrib import messages as flash
from django.contrib.auth import authenticate, login, logout
from datetime import datetime
from decimal import Decimal
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, connection, transaction
from django.db.models import Avg, Count, F, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .constants import (
    MARKET_FOUNDING_CREDIT,
    MARKET_FOUNDING_SLOTS,
    PRIVATE_REVIEW_CRITERIA,
    PUBLIC_REVIEW_CRITERIA,
    TOWN_CHOICES,
)
from .forms import (
    ClientRegistrationForm,
    ContactSupportForm,
    LoginForm,
    MarketListingForm,
    MarketOrderForm,
    MessageForm,
    NotificationPreferencesForm,
    PrivateReviewForm,
    PublicReviewForm,
    QuoteForm,
    QuotingAppointmentForm,
    ServiceAreaForm,
    TaskForm,
    TradieRegistrationForm,
)
from .models import (
    Invoice,
    MarketListing,
    MarketOrder,
    Message,
    PlatformNotice,
    PlatformSettings,
    PromoCode,
    PublicReview,
    Quote,
    QuotingAppointment,
    QuotingAppointmentSlot,
    Sponsor,
    Task,
    TermsAcceptance,
    TradeCategory,
    TradieProfile,
    User,
)
from .utils import (
    calculate_market_price_per_unit,
    calculate_market_take_home,
    calculate_platform_fee,
    calculate_quote_from_take_home,
    create_platform_fee_for_task,
    get_active_platform_settings,
    get_tradie_billing_summary,
    notify_admin,
    notify_buyer_market_order_update,
    notify_client_new_quote,
    notify_matching_tradies_new_job,
    notify_message_recipient,
    notify_seller_new_market_order,
    send_welcome_notice,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_role(request, role):
    if not request.user.is_authenticated or request.user.role != role:
        raise PermissionDenied


def _require_task_poster(request):
    """Gate for actions on a task the requesting user posted. Both clients
    and local professionals can post tasks — a tradie who needs another
    local pro doesn't need a second account for it — so this allows either
    role. Ownership of the specific task is still enforced separately by
    each view's client=request.user queryset filter."""
    if not request.user.is_authenticated or request.user.role not in (User.ROLE_CLIENT, User.ROLE_TRADIE):
        raise PermissionDenied


def _get_tradie_profile(user):
    try:
        return user.tradie_profile
    except TradieProfile.DoesNotExist:
        return None


def _require_quoting_tradie(request):
    """Gate for actions that place a quote/appointment request. Pending
    tradies are allowed through (they can quote while awaiting verification —
    clients see a pending badge) — only rejected/suspended accounts, and
    Electrical/Plumbing tradies whose safety documents haven't been reviewed
    yet, are blocked. See TradieProfile.can_quote()/quote_block_reason()."""
    if not request.user.is_authenticated:
        raise PermissionDenied
    if request.user.role != User.ROLE_TRADIE:
        flash.error(request, 'Only local professional accounts can access this action.')
        return redirect('dashboard')
    profile = _get_tradie_profile(request.user)
    if not profile:
        flash.warning(request, 'Your local professional profile could not be found. Please contact support.')
        return redirect('tradie_dashboard')
    if not profile.can_quote():
        flash.error(request, profile.quote_block_reason())
        return redirect('tradie_dashboard')
    return None


def _task_conversation_parties(task):
    """Everyone eligible to message about this task: the client, the
    assigned tradie, anyone who's quoted, and anyone who's requested a
    quoting appointment — regardless of quote/appointment status, since a
    withdrawn/rejected quote shouldn't retroactively lock someone out of
    a conversation they were already legitimately part of."""
    ids = {task.client_id}
    if task.assigned_tradie_id:
        ids.add(task.assigned_tradie_id)
    ids.update(task.quotes.values_list('tradie_id', flat=True))
    ids.update(task.quoting_appointments.values_list('provider_id', flat=True))
    return ids


def _build_conversations(user):
    """Return a list of dicts describing each unique (task, other_user) conversation,
    ordered by most-recent message first."""
    msgs = (
        Message.objects.filter(Q(sender=user) | Q(recipient=user))
        .select_related('task', 'sender', 'recipient')
        .order_by('-created_at')
    )
    seen = set()
    convs = []
    for msg in msgs:
        other = msg.recipient if msg.sender == user else msg.sender
        key = (msg.task_id, other.pk)
        if key not in seen:
            seen.add(key)
            convs.append({'task': msg.task, 'other': other, 'last_msg': msg})
    return convs


# ── Static pages ─────────────────────────────────────────────────────────────

def home(request):
    return render(request, 'marketplace/home.html', {
        'sponsors': Sponsor.get_active_for_placement('homepage'),
    })


def how_it_works(request):
    return render(request, 'marketplace/how_it_works.html', {
        'sponsors': Sponsor.get_active_for_placement('how_it_works'),
    })


def terms(request):
    return render(request, 'marketplace/terms.html')


def privacy(request):
    return render(request, 'marketplace/privacy.html')


def contact_support(request):
    """
    Sitewide "Contact" page (footer link, and a dashboard sidebar link for
    logged-in users since the footer is hidden inside the dashboard layout).
    Includes a "Report a problem" topic alongside general inquiries. Lets
    clients and local professionals send a message straight to the admin
    inbox, Reply-To set to their own address so replying from the admin's
    inbox goes directly back to them.
    """
    if request.method == 'POST':
        form = ContactSupportForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            if request.user.is_authenticated:
                who = f'{request.user.full_name} ({request.user.email}, {request.user.get_role_display()})'
            else:
                who = f'{cd["name"]} ({cd["email"]}) — not logged in'
            topic_label = dict(ContactSupportForm.TOPIC_CHOICES).get(cd['topic'], cd['topic'])
            notify_admin(
                subject=f'[{topic_label}] {cd["subject"]}',
                body=(
                    f'From: {who}\n'
                    f'Topic: {topic_label}\n'
                    f'Time: {timezone.now().strftime("%d %B %Y, %H:%M")} UTC\n\n'
                    f'{cd["message"]}'
                ),
                reply_to=[cd['email']],
            )
            flash.success(request, "Thanks for reaching out — we've been notified and will be in touch soon.")
            return redirect('contact_support')
        for error_list in form.errors.values():
            for message in error_list:
                flash.error(request, message)
    else:
        initial = {}
        if request.user.is_authenticated:
            initial = {'name': request.user.full_name, 'email': request.user.email}
        requested_topic = request.GET.get('topic')
        if requested_topic in dict(ContactSupportForm.TOPIC_CHOICES):
            initial['topic'] = requested_topic
        form = ContactSupportForm(initial=initial)

    return render(request, 'marketplace/support_contact.html', {'form': form})


def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse({'status': 'error'}, status=503)
    return JsonResponse({'status': 'ok'})


# ── Auth ──────────────────────────────────────────────────────────────────────

def register_client(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = ClientRegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        settings_obj = PlatformSettings.get_active()
        TermsAcceptance.objects.create(
            user=user,
            terms_version=settings_obj.terms_version if settings_obj else '1.0',
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )
        login(request, user)
        send_welcome_notice(user)
        flash.success(request, f'Bula, {user.first_name}! Your client account is ready.')
        return redirect('client_dashboard')
    return render(request, 'marketplace/register_client.html', {
        'form': form,
        'closed_beta_enabled': settings.CLOSED_BETA_ENABLED,
        'beta_gate_clients': settings.BETA_GATE_CLIENT_SIGNUPS,
    })


def register_tradie(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = TradieRegistrationForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        try:
            user = form.save()
        except Exception as exc:
            import sys
            import traceback
            print(f'register_tradie: form.save() failed: {exc!r}', flush=True)
            traceback.print_exc()
            sys.stderr.flush()
            try:
                import sentry_sdk
                sentry_sdk.capture_exception(exc)
            except ImportError:
                pass
            flash.error(
                request,
                'Something went wrong creating your account (likely a document upload issue). '
                'Please try again — if it keeps failing, contact support.',
            )
            return render(request, 'marketplace/register_tradie.html', {
                'form': form,
                'trade_choices': TradeCategory.get_choices(),
                'town_choices': TOWN_CHOICES,
                'closed_beta_enabled': settings.CLOSED_BETA_ENABLED,
                'beta_gate_tradies': settings.BETA_GATE_TRADIE_SIGNUPS,
            })
        settings_obj = PlatformSettings.get_active()
        TermsAcceptance.objects.create(
            user=user,
            terms_version=settings_obj.terms_version if settings_obj else '1.0',
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            accepted_platform_circumvention=form.cleaned_data.get('accepted_platform_circumvention', False),
            accepted_invoicing_terms=form.cleaned_data.get('accepted_invoicing_terms', False),
        )
        login(request, user)
        send_welcome_notice(user)
        try:
            approve_url = request.build_absolute_uri(
                reverse('admin:marketplace_tradieprofile_change', args=[user.tradie_profile.pk])
            )
        except Exception:
            approve_url = '(check /admin/marketplace/tradieprofile/)'
        notify_admin(
            subject=f'New local professional signup awaiting approval: {user.full_name}',
            body=(
                f'{user.full_name} ({user.email}) just registered as a local professional in {user.town}.\n\n'
                f'Their documents are pending review. Approve or reject here:\n{approve_url}'
            ),
        )
        flash.success(request, f'Bula, {user.first_name}! Your local professional account is created and pending document verification.')
        return redirect('tradie_dashboard')
    return render(request, 'marketplace/register_tradie.html', {
        'form': form,
        'trade_choices': TradeCategory.get_choices(),
        'town_choices': TOWN_CHOICES,
        'closed_beta_enabled': settings.CLOSED_BETA_ENABLED,
        'beta_gate_tradies': settings.BETA_GATE_TRADIE_SIGNUPS,
    })


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = LoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        cd   = form.cleaned_data
        user = authenticate(request, username=cd['email'], password=cd['password'])
        if user:
            login(request, user)
            nxt = request.GET.get('next', '')
            return redirect(nxt or 'dashboard')
        else:
            flash.error(request, 'Incorrect email or password.')
    return render(request, 'marketplace/login.html', {'form': form})


def logout_view(request):
    logout(request)
    return redirect('home')


@login_required
def dashboard(request):
    if request.user.role == User.ROLE_TRADIE:
        return redirect('tradie_dashboard')
    return redirect('client_dashboard')


# ── Client dashboard ──────────────────────────────────────────────────────────

@login_required
def client_dashboard(request):
    _require_role(request, User.ROLE_CLIENT)
    tasks = request.user.tasks.prefetch_related('quotes')
    ctx = {
        'open_tasks':      tasks.filter(status=Task.STATUS_OPEN),
        'assigned_tasks':  tasks.filter(status=Task.STATUS_ASSIGNED),
        'completed_tasks': tasks.filter(status=Task.STATUS_COMPLETED),
        'total_tasks':     tasks.count(),
        'new_quotes':      Quote.objects.filter(
            task__client=request.user, status=Quote.STATUS_PENDING
        ).count(),
        'appointments':    QuotingAppointment.objects.filter(client=request.user).select_related('task', 'provider').prefetch_related('slots').order_by('-created_at'),
        'sponsors':        Sponsor.get_active_for_placement('client_dashboard'),
    }
    return render(request, 'marketplace/client_dashboard.html', ctx)


# ── Tradie dashboard ──────────────────────────────────────────────────────────

@login_required
def tradie_dashboard(request):
    _require_role(request, User.ROLE_TRADIE)
    profile = _get_tradie_profile(request.user)
    if not profile:
        flash.warning(request, 'Your local professional profile could not be found. Please contact support.')
        return redirect('contact_support')
    tradie_can_quote = profile.can_quote()

    nearby = Task.objects.none()
    if tradie_can_quote and profile.service_towns:
        nearby = (
            Task.objects.filter(
                status=Task.STATUS_OPEN,
                town__in=profile.service_towns,
            )
            .exclude(quotes__tradie=request.user)
            .exclude(client=request.user)  # local pros can post jobs too — don't suggest quoting on their own
            .order_by('-created_at')[:10]
        )

    my_quotes = (
        request.user.quotes
        .select_related('task', 'task__client')
        .order_by('-created_at')
    )

    # Local pros can post jobs too (e.g. needing another local pro themselves)
    # instead of needing a second client account — same Task.client FK either way.
    posted_tasks = request.user.tasks.prefetch_related('quotes')

    provider_appointments = request.user.provider_quoting_appointments.select_related('task', 'client').prefetch_related('slots').order_by('-created_at')
    ctx = {
        'profile':         profile,
        'pending_quotes':  my_quotes.filter(status=Quote.STATUS_PENDING),
        'accepted_quotes': my_quotes.filter(status=Quote.STATUS_ACCEPTED),
        'completed_tasks': request.user.assigned_tasks.filter(status=Task.STATUS_COMPLETED),
        'nearby_tasks':    nearby,
        'posted_open_tasks':      posted_tasks.filter(status=Task.STATUS_OPEN),
        'posted_assigned_tasks':  posted_tasks.filter(status=Task.STATUS_ASSIGNED),
        'posted_completed_tasks': posted_tasks.filter(status=Task.STATUS_COMPLETED),
        'posted_new_quotes':      Quote.objects.filter(task__client=request.user, status=Quote.STATUS_PENDING).count(),
        'review_count':    PublicReview.objects.filter(ratee=request.user).count(),
        'avg_rating':      (
            PublicReview.objects.filter(ratee=request.user)
            .aggregate(
                a=Avg('reliability_punctuality'), b=Avg('quote_price_accuracy'), c=Avg('value_for_money'),
                d=Avg('service_quality_workmanship'), e=Avg('communication_after_service'),
            )
        ),
        'appointments':    provider_appointments,
        'sponsors':        Sponsor.get_active_for_placement('tradie_dashboard'),
        'tradie_can_quote': tradie_can_quote,
    }
    return render(request, 'marketplace/tradie_dashboard.html', ctx)


@login_required
def edit_service_area(request):
    _require_role(request, User.ROLE_TRADIE)
    profile = _get_tradie_profile(request.user)
    if not profile:
        flash.warning(request, 'Your local professional profile could not be found. Please contact support.')
        return redirect('tradie_dashboard')

    form = ServiceAreaForm(request.POST or None, instance=profile)
    if request.method == 'POST' and form.is_valid():
        form.save()
        flash.success(request, 'Your service area has been updated.')
        return redirect('tradie_dashboard')
    return render(request, 'marketplace/edit_service_area.html', {
        'form': form,
        'town_choices': TOWN_CHOICES,
    })


@login_required
def billing(request):
    _require_role(request, User.ROLE_TRADIE)

    invoices = (
        request.user.invoices
        .prefetch_related('lines', 'lines__task', 'notifications')
        .order_by('-created_at')
    )

    ctx = {
        'invoices': invoices,
        'summary': get_tradie_billing_summary(request.user),
    }
    return render(request, 'marketplace/billing.html', ctx)


# ── Browse tasks ──────────────────────────────────────────────────────────────

def browse_tasks(request):
    qs = Task.objects.filter(status=Task.STATUS_OPEN).select_related('client')
    category = request.GET.get('category', '').strip()
    keyword  = request.GET.get('q', '').strip()
    town     = request.GET.get('town', '').strip()
    if category:
        qs = qs.filter(category=category)
    if keyword:
        qs = qs.filter(Q(title__icontains=keyword) | Q(description__icontains=keyword))
    if town:
        qs = qs.filter(town=town)
    return render(request, 'marketplace/browse_tasks.html', {
        'tasks':           qs,
        'category_filter': category,
        'keyword_filter':  keyword,
        'town_filter':     town,
        'category_choices': TradeCategory.get_choices(),
        'town_choices':    TOWN_CHOICES,
        'sponsors':        Sponsor.get_active_for_placement('browse_tasks_sidebar'),
    })


# ── Browse local professionals ──────────────────────────────────────────────────

def browse_tradies(request):
    # Trade/town are JSONField lists — same cross-DB portability limitation
    # noted elsewhere (Sponsor.placements, MarketListing.available_dates):
    # containment queries don't behave identically on SQLite vs Postgres, so
    # those two filters are applied in Python over the already-narrowed
    # (optionally keyword-filtered) queryset rather than at the DB level.
    #
    # Pending tradies are shown too (with a "Pending verification" badge in
    # the template) — same "browse while awaiting verification" policy as
    # quoting itself (TradieProfile.can_quote()). Rejected/suspended accounts
    # are excluded outright.
    category = request.GET.get('category', '').strip()
    town     = request.GET.get('town', '').strip()
    keyword  = request.GET.get('q', '').strip()

    qs = (
        TradieProfile.objects.filter(
            verification_status__in=[TradieProfile.VERIFICATION_PENDING, TradieProfile.VERIFICATION_APPROVED]
        )
        .select_related('user')
        .order_by('verification_status', 'business_name')
    )
    if keyword:
        qs = qs.filter(
            Q(business_name__icontains=keyword) | Q(bio__icontains=keyword)
            | Q(user__first_name__icontains=keyword) | Q(user__last_name__icontains=keyword)
        )

    profiles = list(qs)
    if category:
        profiles = [p for p in profiles if category in (p.trades or [])]
    if town:
        profiles = [p for p in profiles if town in (p.service_towns or [])]

    # Bulk-aggregate ratings in a single query rather than the two per-profile
    # queries get_public_rating_breakdown()/public_completed_job_count() would
    # otherwise run (N+1 — this page isn't paginated, so a growing directory
    # would mean a growing number of extra queries per request).
    rating_rows = (
        PublicReview.objects.filter(ratee_id__in=[p.user_id for p in profiles])
        .values('ratee_id')
        .annotate(
            avg_reliability=Avg('reliability_punctuality'), avg_price=Avg('quote_price_accuracy'),
            avg_value=Avg('value_for_money'), avg_quality=Avg('service_quality_workmanship'),
            avg_comm=Avg('communication_after_service'), avg_timeline=Avg('timeline_schedule_delivery'),
            review_count=Count('id'),
        )
    )
    rating_map = {}
    for row in rating_rows:
        total = sum(row[k] for k in (
            'avg_reliability', 'avg_price', 'avg_value', 'avg_quality', 'avg_comm', 'avg_timeline'
        ))
        rating_map[row['ratee_id']] = {'overall': round(total / 6, 1), 'count': row['review_count']}

    for p in profiles:
        r = rating_map.get(p.user_id)
        p.overall_rating = r['overall'] if r else None
        p.review_count = r['count'] if r else 0

    return render(request, 'marketplace/browse_tradies.html', {
        'profiles':         profiles,
        'category_filter':  category,
        'town_filter':      town,
        'keyword_filter':   keyword,
        'category_choices': TradeCategory.get_choices(),
        'town_choices':     TOWN_CHOICES,
    })


# ── Post task ─────────────────────────────────────────────────────────────────

@login_required
def post_task(request):
    _require_task_poster(request)
    form = TaskForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        task = form.save(commit=False)
        task.client = request.user
        task.save()
        form.save_m2m()
        notify_matching_tradies_new_job(task)
        flash.success(request, 'Task posted! Local pros will start sending quotes soon.')
        return redirect('task_detail', pk=task.pk)
    return render(request, 'marketplace/post_task.html', {
        'form': form,
        'category_choices': TradeCategory.get_choices(),
    })


# ── Task detail ───────────────────────────────────────────────────────────────

def task_detail(request, pk):
    task   = get_object_or_404(Task, pk=pk)
    quotes = task.quotes.select_related('tradie', 'tradie__tradie_profile').order_by('created_at')

    user_quote        = None
    can_quote         = False
    tradie_can_quote  = False
    can_accept        = False
    can_complete      = False
    can_rate_tradie   = False
    can_rate_client   = False
    client_has_rated  = False
    tradie_has_rated  = False

    founding_credit_balance = Decimal('0.00')
    if request.user.is_authenticated:
        u = request.user
        tradie_profile = _get_tradie_profile(u) if u.role == User.ROLE_TRADIE else None
        tradie_can_quote = tradie_profile.can_quote() if tradie_profile else False
        if tradie_profile and tradie_profile.is_founding_member:
            founding_credit_balance = tradie_profile.founding_member_credit_balance
        if u.role == User.ROLE_TRADIE and tradie_can_quote and task.status == Task.STATUS_OPEN and u != task.client:
            try:
                user_quote = quotes.get(tradie=u)
            except Quote.DoesNotExist:
                can_quote = True

        if u == task.client:
            if task.status == Task.STATUS_OPEN:
                can_accept = True
            if task.status == Task.STATUS_ASSIGNED:
                can_complete = True

    appointments = task.quoting_appointments.select_related('provider', 'client', 'selected_slot').prefetch_related('slots').order_by('-created_at')
    can_book_appointment = (
        request.user.is_authenticated
        and request.user.role == User.ROLE_TRADIE
        and tradie_can_quote
        and task.status == Task.STATUS_OPEN
        and request.user != task.client
    )
    user_has_appointment = request.user.is_authenticated and appointments.filter(provider=request.user).exists()

    can_message_client = (
        request.user.is_authenticated and request.user != task.client and (
            bool(user_quote) or user_has_appointment or request.user == task.assigned_tradie
        )
    )

    if request.user.is_authenticated and task.status == Task.STATUS_COMPLETED:
        client_has_rated = PublicReview.objects.filter(task=task, rater=task.client).exists()
        if u == task.client:
            can_rate_tradie = not client_has_rated
        if task.assigned_tradie:
            tradie_has_rated = PublicReview.objects.filter(
                task=task, rater=task.assigned_tradie
            ).exists()
            # NOTE: tradie rates client via PrivateReview — that form is at rate_client URL
            if u == task.assigned_tradie:
                # Check via separate import-free approach: query PrivateReview via model
                from .models import PrivateReview as _PR
                tradie_has_rated = _PR.objects.filter(task=task, rater=u).exists()
                can_rate_client = not tradie_has_rated

    # Quotes: client sees all; tradie sees only their own; others see none
    visible_quotes = []
    if request.user.is_authenticated:
        if request.user == task.client:
            visible_quotes = list(quotes)
        elif request.user.role == User.ROLE_TRADIE:
            visible_quotes = [q for q in quotes if q.tradie == request.user]

    # Who posted a job is only shown to local pros (who need it to decide
    # whether to quote) and to the poster themselves — not to other clients
    # or anonymous visitors browsing open tasks.
    can_see_poster_identity = (
        request.user.is_authenticated
        and (request.user.role == User.ROLE_TRADIE or request.user == task.client)
    )

    return render(request, 'marketplace/task_detail.html', {
        'task':            task,
        'can_see_poster_identity': can_see_poster_identity,
        'quotes':          visible_quotes,
        'user_quote':      user_quote,
        'can_quote':       can_quote,
        'can_accept':      can_accept,
        'can_complete':    can_complete,
        'can_rate_tradie': can_rate_tradie,
        'can_rate_client': can_rate_client,
        'client_has_rated': client_has_rated,
        'tradie_has_rated': tradie_has_rated,
        'quote_form':      QuoteForm() if can_quote else None,
        'platform_settings': get_active_platform_settings(),
        'submit_quote_url': reverse('submit_quote', args=[task.pk]),
        'appointments':    appointments,
        'can_book_appointment': can_book_appointment,
        'user_has_appointment': user_has_appointment,
        'can_message_client': can_message_client,
        'tradie_can_participate': tradie_can_quote,
        'sponsors':        Sponsor.get_active_for_placement('task_detail_sidebar'),
        'founding_credit_balance': founding_credit_balance,
        'check_promo_code_url': reverse('check_promo_code', args=[task.pk]),
    })


# ── Submit quote ──────────────────────────────────────────────────────────────

@login_required
def submit_quote(request, pk):
    approval_redirect = _require_quoting_tradie(request)
    if approval_redirect:
        return approval_redirect
    task = get_object_or_404(Task, pk=pk, status=Task.STATUS_OPEN)
    if task.client == request.user:
        flash.error(request, "You can't quote on your own job posting.")
        return redirect('task_detail', pk=pk)
    if Quote.objects.filter(task=task, tradie=request.user).exists():
        flash.error(request, 'You have already quoted on this task.')
        return redirect('task_detail', pk=pk)
    form = QuoteForm(request.POST)
    if form.is_valid():
        q = form.save(commit=False)
        q.task   = task
        q.tradie = request.user
        q.customer_facing_quote = q.price
        q.client_quote_total = q.price
        q.minimum_take_home_amount = form.cleaned_data.get('minimum_take_home_amount')

        # Discount selection — mutually exclusive, validated server-side
        # regardless of what the client-side calculator showed.
        promo_input = (form.cleaned_data.get('promo_code_input') or '').strip()
        if q.used_founding_credit and promo_input:
            flash.error(request, 'Choose either your founding member credit or a promo code, not both.')
            return redirect('task_detail', pk=pk)

        if q.used_founding_credit:
            profile = _get_tradie_profile(request.user)
            if not profile or not profile.is_founding_member or profile.founding_member_credit_balance <= 0:
                flash.error(request, 'Your founding member credit is not available.')
                return redirect('task_detail', pk=pk)

        promo = None
        if promo_input:
            promo = PromoCode.objects.filter(code__iexact=promo_input).first()
            if not promo or not promo.is_valid_now():
                flash.error(request, f'"{promo_input}" is not a valid or active promo code.')
                return redirect('task_detail', pk=pk)
            q.promo_code = promo

        settings = get_active_platform_settings()
        totals = None
        if q.price is not None and q.price > 0:
            fee_rate, fee_cap, _ = calculate_platform_fee(q.price, settings)
            q.success_fee_rate_at_quote_time = fee_rate
            q.success_fee_cap_at_quote_time = fee_cap
            q.large_job_threshold_at_quote_time = settings.large_job_threshold
            q.large_job_fee_rate_at_quote_time = settings.large_job_fee_rate
            q.fee_rule_applied = ''

            if q.price > settings.large_job_threshold:
                q.fee_rule_applied = f'Large job {settings.large_job_fee_rate}%'
            else:
                estimated_fee = (q.price * q.success_fee_rate_at_quote_time) / 100
                if estimated_fee > q.success_fee_cap_at_quote_time:
                    q.fee_rule_applied = f'Standard {q.success_fee_rate_at_quote_time}% / FJD ${q.success_fee_cap_at_quote_time} cap'
                else:
                    q.fee_rule_applied = f'Standard {q.success_fee_rate_at_quote_time}%'

            q.estimated_platform_fee = (q.price * (q.success_fee_rate_at_quote_time if q.price <= settings.large_job_threshold else q.large_job_fee_rate_at_quote_time) / 100)
            if q.price <= settings.large_job_threshold and q.estimated_platform_fee > q.success_fee_cap_at_quote_time:
                q.estimated_platform_fee = q.success_fee_cap_at_quote_time
            q.estimated_platform_fee = q.estimated_platform_fee.quantize(Decimal('0.01'))

            discount = Decimal('0.00')
            if q.used_founding_credit:
                profile = _get_tradie_profile(request.user)
                discount = min(profile.founding_member_credit_balance, q.estimated_platform_fee)
            elif promo:
                discount = promo.calculate_discount(q.estimated_platform_fee)
            q.estimated_discount_amount = discount.quantize(Decimal('0.01'))

            effective_fee = q.estimated_platform_fee - q.estimated_discount_amount
            q.estimated_provider_take_home = (q.price - effective_fee).quantize(Decimal('0.01'))
            q.estimated_tradie_take_home = q.estimated_provider_take_home
        q.save()
        notify_client_new_quote(q)
        flash.success(request, 'Quote submitted! The client will be in touch.')
    else:
        for error_list in form.errors.values():
            for message in error_list:
                flash.error(request, message)
    return redirect('task_detail', pk=pk)


@login_required
def check_promo_code(request, pk):
    """
    AJAX endpoint for the quote calculator: validate a promo code and report
    back its discount type/value so the client-side calculator can preview
    the discount. The real, authoritative check happens again in
    submit_quote() — this is UI feedback only, not the source of truth.
    """
    code = (request.GET.get('code') or '').strip()
    if not code:
        return JsonResponse({'valid': False, 'message': 'Enter a code.'})
    promo = PromoCode.objects.filter(code__iexact=code).first()
    if not promo or not promo.is_valid_now():
        return JsonResponse({'valid': False, 'message': 'Not a valid or active promo code.'})
    return JsonResponse({
        'valid': True,
        'discount_type': promo.discount_type,
        'discount_value': str(promo.discount_value),
        'message': (
            f'{promo.discount_value}% off the platform fee'
            if promo.discount_type == PromoCode.DISCOUNT_PERCENT
            else f'FJD ${promo.discount_value} off the platform fee'
        ),
    })


# ── Book quoting appointment ─────────────────────────────────────────────────

@login_required
def book_quoting_appointment(request, pk):
    approval_redirect = _require_quoting_tradie(request)
    if approval_redirect:
        return approval_redirect
    task = get_object_or_404(Task, pk=pk, status=Task.STATUS_OPEN)
    if task.client == request.user:
        flash.error(request, "You can't request a quoting appointment on your own job posting.")
        return redirect('task_detail', pk=pk)
    existing_appointments = task.quoting_appointments.filter(provider=request.user).prefetch_related('slots').order_by('-created_at')
    form = QuotingAppointmentForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save(task, request.user)
        flash.success(request, 'Appointment request sent. The client can choose one of your proposed times.')
        return redirect('task_detail', pk=pk)
    return render(request, 'marketplace/book_quoting_appointment.html', {
        'task': task,
        'form': form,
        'existing_appointments': existing_appointments,
    })


@login_required
@require_POST
def accept_quoting_appointment_slot(request, pk, appt_pk, slot_pk):
    _require_task_poster(request)
    task = get_object_or_404(Task, pk=pk, client=request.user)
    appointment = get_object_or_404(QuotingAppointment, pk=appt_pk, task=task, status=QuotingAppointment.STATUS_REQUESTED)
    slot = get_object_or_404(QuotingAppointmentSlot, pk=slot_pk, quoting_appointment=appointment)
    appointment.status = QuotingAppointment.STATUS_ACCEPTED
    appointment.selected_slot = slot
    appointment.save()
    appointment.slots.update(is_selected=False)
    slot.is_selected = True
    slot.save()
    flash.success(request, 'Quoting appointment confirmed. Your local professional will see the accepted slot.')
    return redirect('task_detail', pk=pk)


@login_required
@require_POST
def decline_quoting_appointment(request, pk, appt_pk):
    _require_task_poster(request)
    task = get_object_or_404(Task, pk=pk, client=request.user)
    appointment = get_object_or_404(QuotingAppointment, pk=appt_pk, task=task, status=QuotingAppointment.STATUS_REQUESTED)
    appointment.status = QuotingAppointment.STATUS_DECLINED
    appointment.save()
    flash.success(request, 'You declined the appointment request. The local professional can send a new request if needed.')
    return redirect('task_detail', pk=pk)


@login_required
@require_POST
def cancel_quoting_appointment(request, pk, appt_pk):
    approval_redirect = _require_quoting_tradie(request)
    if approval_redirect:
        return approval_redirect
    appointment = get_object_or_404(QuotingAppointment, pk=appt_pk, task__pk=pk, provider=request.user, status=QuotingAppointment.STATUS_REQUESTED)
    appointment.status = QuotingAppointment.STATUS_CANCELLED
    appointment.save()
    flash.success(request, 'Your quoting appointment request has been cancelled.')
    return redirect('task_detail', pk=pk)


# ── Accept quote ──────────────────────────────────────────────────────────────

@login_required
def accept_quote(request, pk, qpk):
    _require_task_poster(request)
    task  = get_object_or_404(Task, pk=pk, client=request.user, status=Task.STATUS_OPEN)
    quote = get_object_or_404(Quote, pk=qpk, task=task, status=Quote.STATUS_PENDING)
    # Accept this quote, decline all others
    task.quotes.exclude(pk=qpk).update(status=Quote.STATUS_DECLINED)
    quote.status = Quote.STATUS_ACCEPTED
    quote.save()
    task.status          = Task.STATUS_ASSIGNED
    task.assigned_tradie = quote.tradie
    task.save()
    flash.success(request, f'Quote accepted! {quote.tradie.first_name} is assigned.')
    return redirect('task_detail', pk=pk)


# ── Complete task ─────────────────────────────────────────────────────────────

@login_required
def complete_task(request, pk):
    _require_task_poster(request)
    task = get_object_or_404(Task, pk=pk, client=request.user, status=Task.STATUS_ASSIGNED)
    task.status = Task.STATUS_COMPLETED
    task.completed_at = timezone.now()
    accepted_quote = task.quotes.filter(status=Quote.STATUS_ACCEPTED).first()
    if accepted_quote:
        task.final_job_value = accepted_quote.price
    task.save()
    if task.final_job_value is not None and not task.has_platform_fee():
        create_platform_fee_for_task(task, task.final_job_value)
    flash.success(request, 'Task marked as complete. Please leave a review!')
    return redirect('task_detail', pk=pk)


# ── Rate tradie (public review, client → tradie) ──────────────────────────────

@login_required
def rate_tradie(request, pk):
    _require_task_poster(request)
    task = get_object_or_404(Task, pk=pk, client=request.user, status=Task.STATUS_COMPLETED)
    if PublicReview.objects.filter(task=task, rater=request.user).exists():
        flash.info(request, 'You have already reviewed this job.')
        return redirect('task_detail', pk=pk)
    if not task.assigned_tradie:
        flash.error(request, 'This task has no assigned local professional to review.')
        return redirect('task_detail', pk=pk)
    form = PublicReviewForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        cd = form.cleaned_data
        PublicReview.objects.create(
            task=task,
            rater=request.user,
            ratee=task.assigned_tradie,
            reliability_punctuality   = int(cd['reliability_punctuality']),
            quote_price_accuracy      = int(cd['quote_price_accuracy']),
            value_for_money           = int(cd['value_for_money']),
            service_quality_workmanship = int(cd['service_quality_workmanship']),
            communication_after_service  = int(cd['communication_after_service']),
            timeline_schedule_delivery   = int(cd['timeline_schedule_delivery']),
            comment                    = cd.get('comment', ''),
        )
        flash.success(request, 'Vinaka! Your review has been posted.')
        return redirect('tradie_profile', pk=task.assigned_tradie.pk)
    return render(request, 'marketplace/rate_tradie.html', {
        'task':     task,
        'form':     form,
        'criteria': PUBLIC_REVIEW_CRITERIA,
    })


# ── Rate client (private review, tradie → client) ────────────────────────────
# PrivateReview is imported here only via the models layer; no template ever sees it.

@login_required
def rate_client(request, pk):
    _require_role(request, User.ROLE_TRADIE)
    task = get_object_or_404(Task, pk=pk, assigned_tradie=request.user, status=Task.STATUS_COMPLETED)
    # Import PrivateReview locally so it CANNOT accidentally be passed to context
    from .models import PrivateReview
    if PrivateReview.objects.filter(task=task, rater=request.user).exists():
        flash.info(request, 'You have already submitted your feedback for this job.')
        return redirect('task_detail', pk=pk)
    form = PrivateReviewForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        cd = form.cleaned_data
        PrivateReview.objects.create(
            task=task,
            rater=request.user,
            ratee=task.client,
            access_readiness = int(cd['access_readiness']),
            scope_clarity    = int(cd['scope_clarity']),
            communication    = int(cd['communication']),
            payment          = int(cd['payment']),
            conduct          = int(cd['conduct']),
            comment          = cd.get('comment', ''),
        )
        flash.success(request, 'Feedback recorded. Vinaka!')
        return redirect('task_detail', pk=pk)
    # Pass criteria but NOT the PrivateReview model or any queryset
    return render(request, 'marketplace/rate_client.html', {
        'task':     task,
        'form':     form,
        'criteria': PRIVATE_REVIEW_CRITERIA,
    })


# ── Tradie profile ────────────────────────────────────────────────────────────

def tradie_profile(request, pk):
    tradie  = get_object_or_404(User, pk=pk, role=User.ROLE_TRADIE)
    profile = get_object_or_404(TradieProfile, user=tradie)
    reviews = PublicReview.objects.filter(ratee=tradie).select_related('rater', 'task').order_by('-created_at')
    count   = reviews.count()

    raw_avgs = reviews.aggregate(
        reliability_punctuality   = Avg('reliability_punctuality'),
        quote_price_accuracy      = Avg('quote_price_accuracy'),
        value_for_money           = Avg('value_for_money'),
        service_quality_workmanship = Avg('service_quality_workmanship'),
        communication_after_service  = Avg('communication_after_service'),
        timeline_schedule_delivery   = Avg('timeline_schedule_delivery'),
    )

    criteria_data = []
    total = 0
    for c in PUBLIC_REVIEW_CRITERIA:
        avg = raw_avgs.get(c['field']) or 0
        total += avg
        criteria_data.append({
            'label': c['label'],
            'field': c['field'],
            'avg':   round(avg, 1),
            'pct':   round(avg / 5 * 100),
        })
    overall = round(total / 6, 1) if count else 0

    return render(request, 'marketplace/tradie_profile.html', {
        'tradie':        tradie,
        'profile':       profile,
        'reviews':       reviews,
        'review_count':  count,
        'criteria_data': criteria_data,
        'overall':       overall,
        'jobs_done':     tradie.assigned_tasks.filter(status=Task.STATUS_COMPLETED).count(),
    })


# ── Notices ───────────────────────────────────────────────────────────────────

@login_required
def notices(request):
    return render(request, 'marketplace/notices.html', {
        'notices': PlatformNotice.objects.filter(recipient=request.user).order_by('-sent_at'),
    })


@login_required
def notification_settings(request):
    form = NotificationPreferencesForm(request.POST or None, instance=request.user)
    if request.method == 'POST' and form.is_valid():
        form.save()
        flash.success(request, 'Notification preferences updated.')
        return redirect('notification_settings')
    return render(request, 'marketplace/notification_settings.html', {'form': form})


# ── Messages inbox ────────────────────────────────────────────────────────────

@login_required
def inbox(request):
    convs = _build_conversations(request.user)
    return render(request, 'marketplace/messages.html', {
        'conversations':  convs,
        'active_task':    None,
        'active_other':   None,
        'chat_messages':  [],
        'compose_form':   None,
    })


@login_required
def conversation(request, tpk, opk):
    task        = get_object_or_404(Task, pk=tpk)
    other_user  = get_object_or_404(User, pk=opk)
    # Both the requester AND the other party must actually be part of this
    # task — otherwise a logged-in user could message (or read messages
    # with) a complete stranger by guessing a user pk in the URL.
    u = request.user
    party_ids = _task_conversation_parties(task)
    if u.pk not in party_ids or other_user.pk not in party_ids:
        raise PermissionDenied

    if request.method == 'POST':
        form = MessageForm(request.POST)
        if form.is_valid():
            msg = Message.objects.create(
                task=task, sender=u, recipient=other_user,
                body=form.cleaned_data['body'],
            )
            notify_message_recipient(msg)
        else:
            flash.error(request, "Message wasn't sent — it can't be empty.")
        return redirect('conversation', tpk=tpk, opk=opk)

    chat_messages = Message.objects.filter(
        task=task
    ).filter(
        Q(sender=u, recipient=other_user) | Q(sender=other_user, recipient=u)
    ).order_by('created_at')

    convs = _build_conversations(u)
    return render(request, 'marketplace/messages.html', {
        'conversations': convs,
        'active_task':   task,
        'active_other':  other_user,
        'chat_messages': chat_messages,
        'compose_form':  MessageForm(),
    })


# ── Market (local professionals selling items/serves) ──────────────────────────

def market_browse(request):
    # Status + stock filtered at the DB level rather than loading every
    # active listing and checking in Python — same class of fix as the
    # earlier category_label N+1. Whether a listing's available_dates have
    # all lapsed can't be pushed into a JSONField query cross-DB (same
    # limitation as Sponsor.placements), so that one check runs in Python —
    # but only over the already-narrowed (active, in-stock) result set.
    listings = (
        MarketListing.objects.filter(status=MarketListing.STATUS_ACTIVE)
        .filter(units_sold__lt=F('units_available'))
        .select_related('seller')
    )
    category = request.GET.get('category', '').strip()
    food_type = request.GET.get('food_type', '').strip()
    if category:
        listings = listings.filter(category=category)
    if category == MarketListing.CATEGORY_FOOD and food_type:
        listings = listings.filter(food_type=food_type)
    listings = [l for l in listings if l.has_future_dates()]
    return render(request, 'marketplace/market_browse.html', {
        'listings': listings,
        'category_choices': MarketListing.CATEGORY_CHOICES,
        'food_type_choices': MarketListing.FOOD_TYPE_CHOICES,
        'category_filter': category,
        'food_type_filter': food_type,
        'can_sell': request.user.is_authenticated and request.user.role in (User.ROLE_TRADIE, User.ROLE_CLIENT),
    })


def market_listing_detail(request, pk):
    listing = get_object_or_404(MarketListing.objects.select_related('seller'), pk=pk)
    order_form = None
    user_orders = []

    if request.user.is_authenticated:
        user_orders = list(
            MarketOrder.objects.filter(listing=listing, buyer=request.user).order_by('-created_at')
        )
        # Clients can now sell on the Market too, so a client viewing their
        # own listing must not see an order form for it.
        if request.user.role == User.ROLE_CLIENT and request.user != listing.seller and listing.is_purchasable():
            if request.method == 'POST':
                order_form = MarketOrderForm(request.POST, listing=listing)
                if order_form.is_valid():
                    cd = order_form.cleaned_data
                    with transaction.atomic():
                        locked = MarketListing.objects.select_for_update().get(pk=listing.pk)
                        if cd['quantity'] > locked.units_remaining():
                            flash.error(request, f'Only {locked.units_remaining()} left — someone may have just ordered.')
                            return redirect('market_listing_detail', pk=pk)
                        total_price = (locked.price_per_unit * cd['quantity']).quantize(Decimal('0.01'))
                        fee_amount = (total_price * locked.fee_rate_at_listing / Decimal('100')).quantize(Decimal('0.01'))
                        if locked.use_founding_credit:
                            seller_locked = User.objects.select_for_update().get(pk=locked.seller_id)
                            if seller_locked.is_market_founding_member and seller_locked.market_founding_credit_balance > 0:
                                discount = min(seller_locked.market_founding_credit_balance, fee_amount)
                                fee_amount -= discount
                                seller_locked.market_founding_credit_balance -= discount
                                seller_locked.save(update_fields=['market_founding_credit_balance'])
                        order = MarketOrder.objects.create(
                            listing=locked,
                            buyer=request.user,
                            quantity=cd['quantity'],
                            unit_price_at_order=locked.price_per_unit,
                            total_price=total_price,
                            platform_fee_amount=fee_amount,
                            fulfillment_method=cd['fulfillment_method'],
                            delivery_town=cd.get('delivery_town', ''),
                            requested_date=datetime.strptime(cd['requested_date'], '%Y-%m-%d').date(),
                            status=(
                                MarketOrder.STATUS_ACCEPTED
                                if locked.order_mode == MarketListing.ORDER_MODE_AUTO
                                else MarketOrder.STATUS_PENDING
                            ),
                        )
                        locked.units_sold += cd['quantity']
                        locked.save(update_fields=['units_sold'])
                    notify_seller_new_market_order(order)
                    flash.success(request, 'Order placed! ' + (
                        'Auto-accepted — the seller has been notified.'
                        if order.status == MarketOrder.STATUS_ACCEPTED
                        else 'Waiting on the seller to accept.'
                    ))
                    return redirect('market_listing_detail', pk=pk)
            else:
                order_form = MarketOrderForm(listing=listing)

    return render(request, 'marketplace/market_listing_detail.html', {
        'listing': listing,
        'order_form': order_form,
        'user_orders': user_orders,
        'can_order': (
            request.user.is_authenticated and request.user.role == User.ROLE_CLIENT
            and request.user != listing.seller and listing.is_purchasable()
        ),
    })


@login_required
def create_market_listing(request):
    # Both local professionals and clients can sell on the Market. Tradies
    # still go through the same verification gate as quoting (rejected/
    # suspended blocked); clients have no equivalent profile/status concept,
    # so any authenticated client account is allowed straight through.
    if request.user.role == User.ROLE_TRADIE:
        approval_redirect = _require_quoting_tradie(request)
        if approval_redirect:
            return approval_redirect
    elif request.user.role != User.ROLE_CLIENT:
        raise PermissionDenied

    # First MARKET_FOUNDING_SLOTS sellers to post a listing become Market
    # founding members — this is their first-ever listing and a slot is
    # still open. Only used to decide whether to show the credit checkbox;
    # the actual grant is re-checked for real (race-safe) at save time below.
    is_new_founder_eligible = (
        not request.user.is_market_founding_member
        and not MarketListing.objects.filter(seller=request.user).exists()
        and User.objects.filter(is_market_founding_member=True).count() < MARKET_FOUNDING_SLOTS
    )
    show_founding_credit = is_new_founder_eligible or (
        request.user.is_market_founding_member and request.user.market_founding_credit_balance > 0
    )

    form = MarketListingForm(request.POST or None, request.FILES or None)
    if not show_founding_credit:
        form.fields.pop('use_founding_credit', None)

    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            listing = form.save(commit=False)
            listing.seller = request.user
            if is_new_founder_eligible:
                # Re-check under lock at save time — two sellers hitting
                # "first listing" concurrently must not both slip in under
                # the slot cap.
                locked_user = User.objects.select_for_update().get(pk=request.user.pk)
                if (
                    not locked_user.is_market_founding_member
                    and User.objects.filter(is_market_founding_member=True).count() < MARKET_FOUNDING_SLOTS
                ):
                    locked_user.is_market_founding_member = True
                    locked_user.market_founding_credit_balance = Decimal(MARKET_FOUNDING_CREDIT)
                    locked_user.save(update_fields=['is_market_founding_member', 'market_founding_credit_balance'])
                    flash.success(request, f"You're one of our first {MARKET_FOUNDING_SLOTS} Market sellers — FJD ${MARKET_FOUNDING_CREDIT} platform fee credit added to your account!")
            listing.save()
        flash.success(request, 'Listing posted to the Market!')
        return redirect('my_market_listings')
    founding_credit_amount = (
        request.user.market_founding_credit_balance
        if request.user.is_market_founding_member
        else Decimal(MARKET_FOUNDING_CREDIT)
    )
    return render(request, 'marketplace/create_market_listing.html', {
        'form': form, 'show_founding_credit': show_founding_credit,
        'founding_credit_amount': founding_credit_amount,
    })


@login_required
def my_market_listings(request):
    if request.user.role not in (User.ROLE_TRADIE, User.ROLE_CLIENT):
        raise PermissionDenied
    listings = (
        MarketListing.objects.filter(seller=request.user)
        .prefetch_related('orders', 'orders__buyer')
    )
    return render(request, 'marketplace/my_market_listings.html', {'listings': listings})


@login_required
@require_POST
def market_order_respond(request, pk, action):
    if action == 'fulfill':
        order = get_object_or_404(MarketOrder, pk=pk, listing__seller=request.user, status=MarketOrder.STATUS_ACCEPTED)
        order.status = MarketOrder.STATUS_FULFILLED
        flash.success(request, 'Order marked as fulfilled.')
        order.save(update_fields=['status'])
        notify_buyer_market_order_update(order)
        return redirect('my_market_listings')

    order = get_object_or_404(MarketOrder, pk=pk, listing__seller=request.user, status=MarketOrder.STATUS_PENDING)
    if action == 'accept':
        order.status = MarketOrder.STATUS_ACCEPTED
        flash.success(request, 'Order accepted.')
    elif action == 'decline':
        order.status = MarketOrder.STATUS_DECLINED
        with transaction.atomic():
            locked = MarketListing.objects.select_for_update().get(pk=order.listing_id)
            locked.units_sold = max(locked.units_sold - order.quantity, 0)
            locked.save(update_fields=['units_sold'])
        flash.success(request, 'Order declined — units returned to stock.')
    else:
        raise PermissionDenied
    order.save(update_fields=['status'])
    notify_buyer_market_order_update(order)
    return redirect('my_market_listings')


@login_required
@require_POST
def market_order_cancel(request, pk):
    order = get_object_or_404(MarketOrder, pk=pk, buyer=request.user)
    if order.status not in (MarketOrder.STATUS_PENDING, MarketOrder.STATUS_ACCEPTED):
        flash.error(request, 'This order can no longer be cancelled.')
        return redirect('my_market_orders')
    order.status = MarketOrder.STATUS_CANCELLED
    order.save(update_fields=['status'])
    with transaction.atomic():
        locked = MarketListing.objects.select_for_update().get(pk=order.listing_id)
        locked.units_sold = max(locked.units_sold - order.quantity, 0)
        locked.save(update_fields=['units_sold'])
    flash.success(request, 'Order cancelled.')
    return redirect('my_market_orders')


@login_required
def my_market_orders(request):
    _require_role(request, User.ROLE_CLIENT)
    orders = (
        MarketOrder.objects.filter(buyer=request.user)
        .select_related('listing', 'listing__seller')
    )
    return render(request, 'marketplace/my_market_orders.html', {'orders': orders})


@login_required
def calculate_market_price(request):
    """AJAX endpoint mirroring check_promo_code — live preview only, the
    authoritative calculation happens again server-side in MarketListingForm."""
    direction = request.GET.get('direction', 'take_home')
    vat_applicable = request.GET.get('vat_applicable') == 'true'
    vat_rate = request.GET.get('vat_rate', '').strip()
    vat_rate = Decimal(vat_rate) if (vat_applicable and vat_rate) else None

    try:
        units = int(request.GET.get('units_available', '').strip() or '0')
    except ValueError:
        units = 0
    if units <= 0:
        return JsonResponse({'valid': False, 'error': 'units'})

    try:
        if direction == 'price':
            price = Decimal(request.GET.get('price_per_unit', '').strip() or '0')
            breakdown = calculate_market_take_home(price, units, vat_rate)
        else:
            total_take_home = Decimal(request.GET.get('take_home_total', '').strip() or '0')
            breakdown = calculate_market_price_per_unit(total_take_home, units, vat_rate)
        if not breakdown:
            return JsonResponse({'valid': False})
        return JsonResponse({'valid': True, **{k: str(v) for k, v in breakdown.items()}})
    except Exception:
        return JsonResponse({'valid': False})
