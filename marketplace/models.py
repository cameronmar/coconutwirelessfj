from decimal import Decimal

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils.text import slugify

from .constants import TOWN_CHOICES, EXPERIENCE_CHOICES
from .managers import PublicReviewManager, PrivateReviewManager


# ── Custom user manager (email login, no username) ──────────────────────────

class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError('Email address is required.')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', '')
        return self._create_user(email, password, **extra_fields)


# ── User ─────────────────────────────────────────────────────────────────────

class User(AbstractUser):
    username = None
    email = models.EmailField(unique=True)

    ROLE_CLIENT   = 'client'
    ROLE_TRADIE   = 'tradie'
    ROLE_SUPPLIER = 'supplier'
    ROLE_CHOICES  = [
        (ROLE_CLIENT,   'Client'),
        (ROLE_TRADIE,   'Tradie'),
        (ROLE_SUPPLIER, 'Supplier'),
        ('',            'Staff / Admin'),
    ]
    role   = models.CharField(max_length=10, choices=ROLE_CHOICES, blank=True, default='')
    mobile = models.CharField(max_length=20, blank=True)
    town   = models.CharField(max_length=50, blank=True)

    # Email notification preferences — always logged in-app regardless (see
    # Notices), these only control the optional extra email channel.
    notify_email_new_quote     = models.BooleanField(default=True, verbose_name='Email me when a local professional quotes on my job')
    notify_email_new_message   = models.BooleanField(default=True, verbose_name='Email me when I receive a new message')
    notify_email_new_job_match = models.BooleanField(default=False, verbose_name='Email me when a new job matching my trades and towns is posted')
    notify_email_new_market_order    = models.BooleanField(default=True, verbose_name='Email me when someone orders from my Market listing')
    notify_email_market_order_update = models.BooleanField(default=True, verbose_name='Email me when my Market order is accepted or declined')

    # Android app push notifications — stores the Firebase Cloud Messaging device
    # token registered by the Android WebView app. Blank means the user has never
    # logged in via the app (or the token hasn't been registered yet).
    fcm_token = models.CharField(max_length=255, blank=True, default='')

    # Market founding seller program — first MARKET_FOUNDING_SLOTS sellers
    # (tradie or client) to post a Market listing get a badge + FJD $100
    # platform fee credit, spent down automatically as their listings sell.
    is_market_founding_member          = models.BooleanField(default=False, verbose_name='Market founding seller')
    market_founding_credit_balance     = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        verbose_name='Market founding seller credit balance (FJD)',
    )

    # Beta/tester access — admin-granted, independent of role (a tester is
    # still a client/tradie/supplier account; this just lets them preview
    # features gated behind a not-yet-launched flag, e.g. SUPPLIERS_ENABLED).
    # Not part of ROLE_CHOICES since it's an orthogonal capability, not an
    # account type.
    is_tester = models.BooleanField(default=False, verbose_name='Beta tester (can preview unlaunched features)')

    # Self-service account deletion (views.delete_account). The row itself
    # is never hard-deleted — Task/Quote/Invoice/PlatformFee/etc. all
    # reference User and must survive for financial/dispute record-keeping
    # (see Terms §6, Privacy Policy). Deletion instead anonymizes the
    # account's own PII fields and deactivates login; account_deleted_at
    # marks when that happened.
    account_deleted_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD  = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        name = f'{self.first_name} {self.last_name}'.strip()
        return name if name else self.email

    @property
    def full_name(self):
        return f'{self.first_name} {self.last_name}'.strip() or self.email

    @property
    def initials(self):
        parts = [self.first_name[:1], self.last_name[:1]]
        return ''.join(p for p in parts if p).upper() or '?'

    def can_preview_unlaunched_features(self):
        return self.is_staff or self.is_tester


# ── Tradie profile ────────────────────────────────────────────────────────────

class TradieProfile(models.Model):
    VERIFICATION_PENDING = 'pending'
    VERIFICATION_APPROVED = 'approved'
    VERIFICATION_REJECTED = 'rejected'
    VERIFICATION_SUSPENDED = 'suspended'
    VERIFICATION_STATUS_CHOICES = [
        (VERIFICATION_PENDING, 'Pending review'),
        (VERIFICATION_APPROVED, 'Approved'),
        (VERIFICATION_REJECTED, 'Rejected'),
        (VERIFICATION_SUSPENDED, 'Suspended'),
    ]

    user            = models.OneToOneField(User, on_delete=models.CASCADE, related_name='tradie_profile')
    business_name   = models.CharField(max_length=100, blank=True)
    tin             = models.CharField(max_length=50, blank=True, verbose_name='TIN Number (optional)')
    years_experience = models.CharField(max_length=20, blank=True, choices=EXPERIENCE_CHOICES)
    bio             = models.TextField(blank=True)
    trades          = models.JSONField(default=list)        # list of TradeCategory slugs
    service_towns   = models.JSONField(default=list)        # list of town keys from TOWN_CHOICES

    # Provider verification documents
    tin_letter                    = models.FileField(upload_to='provider_documents/', blank=True, verbose_name='TIN Letter')
    business_licence              = models.FileField(upload_to='provider_documents/', blank=True, verbose_name='Business Licence')
    public_liability_insurance    = models.FileField(upload_to='provider_documents/', blank=True, verbose_name='Public Liability Insurance')
    electrical_contractors_licence = models.FileField(upload_to='provider_documents/', blank=True, verbose_name='Electrical Contractors Licence')
    plumber_licence               = models.FileField(upload_to='provider_documents/', blank=True, verbose_name='Plumber Licence')
    verification_status           = models.CharField(
        max_length=20,
        choices=VERIFICATION_STATUS_CHOICES,
        default=VERIFICATION_PENDING,
        verbose_name='Verification status',
    )
    documents_verified            = models.BooleanField(default=False, verbose_name='Documents verified by admin')
    verification_notes            = models.TextField(blank=True, verbose_name='Verification notes (admin only)')

    # Electrical/Plumbing are safety-critical trades — those tradies can
    # register without their licence documents (rather than being blocked at
    # signup), but can't bid on any job until an admin has reviewed those
    # documents and ticked this on, regardless of overall verification_status.
    safety_documents_reviewed     = models.BooleanField(
        default=False,
        verbose_name='Safety documents reviewed (Electrical/Plumbing licence)',
    )

    # Founding member program — first 20 tradies get a badge + FJD $200 platform
    # fee credit, spent down automatically as their jobs complete.
    is_founding_member             = models.BooleanField(default=False, verbose_name='Founding member')
    founding_member_credit_balance = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        verbose_name='Founding member credit balance (FJD)',
    )

    def __str__(self):
        return f'{self.user} – Tradie Profile'

    def save(self, *args, **kwargs):
        """Keep legacy documents_verified in sync with verification_status."""
        self.documents_verified = self.verification_status == self.VERIFICATION_APPROVED
        super().save(*args, **kwargs)

    def is_approved(self):
        return self.verification_status == self.VERIFICATION_APPROVED

    def is_admin_restricted(self):
        """True if an admin has rejected/suspended this account (distinct
        from the safety-document-review hold below, which isn't punitive —
        used to pick the right tone/wording in the UI)."""
        return self.verification_status in (self.VERIFICATION_REJECTED, self.VERIFICATION_SUSPENDED)

    SAFETY_CRITICAL_TRADES = {'electrical', 'plumbing'}

    def requires_safety_document_review(self):
        return bool(set(self.trades or []) & self.SAFETY_CRITICAL_TRADES)

    def can_quote(self):
        """Pending tradies may browse and quote while awaiting verification —
        only rejected/suspended accounts are blocked. Clients see a pending
        badge on their profile and quotes in the meantime. Electrical/Plumbing
        tradies are an exception on top of that: safety-critical work, so they
        can't bid at all until their licence documents are specifically
        reviewed, independent of overall verification_status."""
        if self.verification_status not in (self.VERIFICATION_PENDING, self.VERIFICATION_APPROVED):
            return False
        if self.requires_safety_document_review() and not self.safety_documents_reviewed:
            return False
        from .utils import is_tradie_payment_restricted  # deferred: utils imports models
        if is_tradie_payment_restricted(self.user):
            return False
        return True

    def quote_block_reason(self):
        """Human-readable reason quoting/appointments are disabled, or '' if allowed."""
        if self.verification_status == self.VERIFICATION_REJECTED:
            return 'Your local professional account verification was rejected. Please contact support.'
        if self.verification_status == self.VERIFICATION_SUSPENDED:
            return 'Your local professional account is suspended. Please contact support.'
        if self.requires_safety_document_review() and not self.safety_documents_reviewed:
            return (
                'Electrical and Plumbing work is safety-critical, so we need to review your licence '
                "documents before you can bid on jobs. If you haven't already sent them, please "
                'contact support — our team will review them and enable bidding shortly.'
            )
        from .utils import is_tradie_payment_restricted  # deferred: utils imports models
        if is_tradie_payment_restricted(self.user):
            return (
                'You have an invoice more than 14 days overdue. Please settle your outstanding '
                'balance from the Billing page before submitting new quotes.'
            )
        return ''

    def trades_display(self):
        lookup = TradeCategory.get_label_map()
        return [lookup.get(t, t) for t in (self.trades or [])]

    def service_towns_display(self):
        return ', '.join(self.service_towns or [])

    def public_completed_job_count(self):
        """Count of completed jobs with public reviews."""
        return PublicReview.objects.filter(ratee=self.user).count()

    def get_public_rating_breakdown(self):
        """
        Get average rating for each criterion.
        Returns dict with criterion averages.
        Overall is computed from these, never stored.
        """
        from django.db.models import Avg
        
        reviews = PublicReview.objects.filter(ratee=self.user)
        if not reviews.exists():
            return None
        
        breakdown = reviews.aggregate(
            reliability_punctuality=Avg('reliability_punctuality'),
            quote_price_accuracy=Avg('quote_price_accuracy'),
            value_for_money=Avg('value_for_money'),
            service_quality_workmanship=Avg('service_quality_workmanship'),
            communication_after_service=Avg('communication_after_service'),
            timeline_schedule_delivery=Avg('timeline_schedule_delivery'),
        )
        
        # Compute overall average
        if breakdown['reliability_punctuality']:
            values = [
                breakdown['reliability_punctuality'],
                breakdown['quote_price_accuracy'],
                breakdown['value_for_money'],
                breakdown['service_quality_workmanship'],
                breakdown['communication_after_service'],
                breakdown['timeline_schedule_delivery'],
            ]
            breakdown['overall'] = sum(values) / len(values)
        
        return breakdown


# ── Workspaces (multi-workspace accounts) ────────────────────────────────────
# One human User may operate a client workspace, an individual-provider
# workspace, and/or any number of business workspaces — see
# marketplace/workspaces.py for the session-based active-workspace helpers and
# the create_*_workspace()/get_*_workspace() functions used throughout this
# file's write paths. Existing legacy User FKs (Task.client, Quote.tradie,
# etc.) remain the source of truth for now; the *_workspace FKs added further
# below are additive and populated alongside them (see
# audit_workspace_relationships for consistency checking).

class UserCapability(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='capabilities')

    can_hire               = models.BooleanField(default=True)
    can_offer_services     = models.BooleanField(default=False)
    can_manage_businesses  = models.BooleanField(default=False)

    phone_verified         = models.BooleanField(default=False)
    onboarding_completed   = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'User Capability'
        verbose_name_plural = 'User Capabilities'

    def __str__(self):
        return f'Capabilities – {self.user}'


class Workspace(models.Model):
    TYPE_CLIENT = 'client'
    TYPE_INDIVIDUAL_PROVIDER = 'individual_provider'
    TYPE_BUSINESS = 'business'
    WORKSPACE_TYPES = [
        (TYPE_CLIENT,              'Hire Someone'),
        (TYPE_INDIVIDUAL_PROVIDER, 'Find Work'),
        (TYPE_BUSINESS,            'Manage Business'),
    ]

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='owned_workspaces')

    workspace_type = models.CharField(max_length=30, choices=WORKSPACE_TYPES, db_index=True)
    display_name   = models.CharField(max_length=160)
    slug           = models.SlugField(max_length=220, unique=True, editable=False)
    profile_image  = models.ImageField(upload_to='workspace_profiles/', blank=True, null=True)
    active         = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['workspace_type', 'display_name']
        verbose_name = 'Workspace'
        verbose_name_plural = 'Workspaces'
        constraints = [
            # A brand-new, currently-empty table — safe to apply these
            # constraints immediately rather than staging them into a later
            # migration the way the spec does for pre-existing dual-FK
            # tables (nothing here can already violate them).
            models.UniqueConstraint(
                fields=['owner', 'workspace_type'],
                condition=models.Q(workspace_type='client'),
                name='unique_client_workspace_per_user',
            ),
            models.UniqueConstraint(
                fields=['owner', 'workspace_type'],
                condition=models.Q(workspace_type='individual_provider'),
                name='unique_individual_provider_workspace_per_user',
            ),
        ]

    def __str__(self):
        return f'{self.display_name} ({self.get_workspace_type_display()})'

    def save(self, *args, **kwargs):
        """
        Auto-generate a unique slug on first save if one wasn't already set.
        `slug` is editable=False (never shown/settable via a form, including
        the admin), so without this, anything that creates a Workspace
        outside marketplace/workspaces.py's create_*_workspace() helpers —
        the Django admin's "Add Workspace" page, the shell, a future script —
        would insert a blank slug: harmless for the first row, then a raw
        IntegrityError (duplicate blank slug) on every one after it. This is
        the single source of truth for slug generation; workspaces.py's
        generate_unique_workspace_slug() is a preview-only helper that
        computes but doesn't reserve one.
        """
        if not self.slug:
            base = slugify(self.display_name) or 'workspace'
            candidate = base
            suffix = 2
            while Workspace.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f'{base}-{suffix}'
                suffix += 1
            self.slug = candidate
        super().save(*args, **kwargs)


class WorkspaceMembership(models.Model):
    ROLE_OWNER   = 'owner'
    ROLE_MANAGER = 'manager'
    ROLE_STAFF   = 'staff'
    ROLE_CHOICES = [
        (ROLE_OWNER,   'Owner'),
        (ROLE_MANAGER, 'Manager'),
        (ROLE_STAFF,   'Staff'),
    ]

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='memberships')
    user      = models.ForeignKey(User, on_delete=models.CASCADE, related_name='workspace_memberships')
    role      = models.CharField(max_length=20, choices=ROLE_CHOICES)
    active     = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Workspace Membership'
        verbose_name_plural = 'Workspace Memberships'
        constraints = [
            models.UniqueConstraint(fields=['workspace', 'user'], name='unique_workspace_membership'),
        ]

    def __str__(self):
        return f'{self.user} – {self.get_role_display()} of {self.workspace}'


class BusinessProfile(models.Model):
    """Only valid on a Workspace of type TYPE_BUSINESS — see clean()."""
    VERIFICATION_NOT_SUBMITTED = 'not_submitted'
    VERIFICATION_PENDING   = 'pending'
    VERIFICATION_APPROVED  = 'approved'
    VERIFICATION_REJECTED  = 'rejected'
    VERIFICATION_SUSPENDED = 'suspended'
    VERIFICATION_STATUSES = [
        (VERIFICATION_NOT_SUBMITTED, 'Not Submitted'),
        (VERIFICATION_PENDING,       'Pending'),
        (VERIFICATION_APPROVED,      'Approved'),
        (VERIFICATION_REJECTED,      'Rejected'),
        (VERIFICATION_SUSPENDED,     'Suspended'),
    ]

    workspace = models.OneToOneField(Workspace, on_delete=models.CASCADE, related_name='business_profile')

    legal_name            = models.CharField(max_length=200, blank=True)
    trading_name          = models.CharField(max_length=200)
    business_description  = models.TextField(blank=True)

    business_phone = models.CharField(max_length=30, blank=True)
    business_email = models.EmailField(blank=True)

    town               = models.CharField(max_length=100, blank=True)
    service_radius_km  = models.PositiveIntegerField(default=10)

    # Fiji-specific business identifiers — deliberately not Australian terms.
    tin                                = models.CharField(max_length=50, blank=True, verbose_name='TIN')
    fiji_business_registration_number  = models.CharField(max_length=100, blank=True, verbose_name='Fiji business registration number')
    fiji_trade_licence_number          = models.CharField(max_length=100, blank=True, verbose_name='Fiji trade or professional licence number')

    facebook_page_url = models.URLField(blank=True)
    facebook_page_id  = models.CharField(max_length=100, blank=True, db_index=True)

    verification_status = models.CharField(max_length=30, choices=VERIFICATION_STATUSES, default=VERIFICATION_NOT_SUBMITTED)
    verified_at          = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Business Profile'
        verbose_name_plural = 'Business Profiles'

    def __str__(self):
        return f'{self.trading_name} ({self.workspace})'

    def clean(self):
        if self.workspace_id and self.workspace.workspace_type != Workspace.TYPE_BUSINESS:
            raise ValidationError({'workspace': 'A business profile can only be attached to a business workspace.'})


# ── Task ──────────────────────────────────────────────────────────────────────

class TradeCategory(models.Model):
    """Trade categories can now be M2M on Task."""
    name  = models.CharField(max_length=50, unique=True)
    icon  = models.CharField(max_length=10, blank=True)
    slug  = models.SlugField(unique=True)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='children')
    active = models.BooleanField(default=True)

    CHOICES_CACHE_KEY = 'trade_category_choices'

    class Meta:
        verbose_name = 'Service Category'
        verbose_name_plural = 'Service Categories'
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        cache.delete(self.CHOICES_CACHE_KEY)

    def delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)
        cache.delete(self.CHOICES_CACHE_KEY)

    @classmethod
    def get_choices(cls):
        """
        Live (slug, "icon name") choices for job/trade categories — the single
        source of truth for category pickers and skill selection across the
        site. Renaming a category here (e.g. Chef -> Catering) updates every
        picker and display label without a code change. Falls back to the
        static TRADE_CHOICES seed list only if the table is empty (e.g. a
        fresh install before migrations have seeded it).

        Cached (short TTL, cleared on save/delete) since this is called once
        per task on every list page via Task.category_label — uncached, that
        was a fresh query per row rendered.
        """
        cached = cache.get(cls.CHOICES_CACHE_KEY)
        if cached is not None:
            return cached
        rows = list(cls.objects.filter(active=True).order_by('name').values_list('slug', 'icon', 'name'))
        if not rows:
            from .constants import TRADE_CHOICES
            return TRADE_CHOICES
        choices = [(slug, f'{icon} {name}'.strip()) for slug, icon, name in rows]
        cache.set(cls.CHOICES_CACHE_KEY, choices, 300)
        return choices

    @classmethod
    def get_label_map(cls):
        """Dict of slug -> 'icon name' display label, for quick lookups."""
        return dict(cls.get_choices())


class Task(models.Model):
    STATUS_OPEN        = 'open'
    STATUS_ASSIGNED    = 'assigned'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_COMPLETED   = 'completed'
    STATUS_CANCELLED   = 'cancelled'
    STATUS_DISPUTED    = 'disputed'
    STATUS_CHOICES     = [
        (STATUS_OPEN,        'Open'),
        (STATUS_ASSIGNED,    'Assigned'),
        (STATUS_IN_PROGRESS, 'In Progress'),
        (STATUS_COMPLETED,   'Completed'),
        (STATUS_CANCELLED,   'Cancelled'),
        (STATUS_DISPUTED,    'Disputed'),
    ]

    client          = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tasks')
    title           = models.CharField(max_length=200)
    category        = models.CharField(max_length=20, blank=True, db_index=True)  # Slug into TradeCategory; see category_label
    categories      = models.ManyToManyField(TradeCategory, related_name='tasks', blank=True)  # New multi-category
    description     = models.TextField()
    budget          = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Blank means the client marked this negotiable — see budget_display / budget_type.',
    )
    town            = models.CharField(max_length=50, choices=TOWN_CHOICES, db_index=True)
    preferred_date  = models.DateField(null=True, blank=True)
    preferred_date_end = models.DateField(
        null=True, blank=True,
        help_text='Set together with preferred_date to describe a date range instead of a single date.',
    )
    status          = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)
    assigned_tradie = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_tasks'
    )
    # Workspace-scoped mirrors of client/assigned_tradie above — see the
    # module docstring near Workspace. owner must match client/assigned_tradie
    # respectively (enforced in clean(), not in save() — see Workspace notes).
    client_workspace = models.ForeignKey(
        'Workspace', on_delete=models.PROTECT, null=True, blank=True, related_name='posted_tasks',
    )
    assigned_provider_workspace = models.ForeignKey(
        'Workspace', on_delete=models.PROTECT, null=True, blank=True, related_name='assigned_tasks',
    )
    # New task quality fields
    materials_required  = models.CharField(max_length=50, blank=True, choices=[
        ('client_supplies', 'Client supplies materials'),
        ('tradie_supplies', 'Tradie supplies materials'),
        ('not_sure', 'Not sure'),
    ])
    access_notes        = models.TextField(blank=True)
    parking_available   = models.CharField(max_length=50, blank=True, choices=[
        ('yes', 'Yes'),
        ('no', 'No'),
        ('not_sure', 'Not sure'),
    ])
    urgency             = models.CharField(max_length=50, blank=True, choices=[
        ('urgent', 'Urgent'),
        ('this_week', 'This week'),
        ('flexible', 'Flexible'),
    ])
    budget_type         = models.CharField(max_length=50, blank=True, choices=[
        ('fixed', 'Fixed price'),
        ('flexible', 'Flexible'),
        ('quote_needed', 'Quote needed'),
    ])
    materials_responsibility = models.CharField(max_length=50, blank=True, choices=[
        ('client_will_supply', 'Client will supply materials'),
        ('provider_should_supply', 'Local professional should supply materials'),
        ('provider_to_advise_after_inspection', 'Local professional should advise after inspection'),
        ('not_applicable', 'Not applicable'),
        ('not_sure', 'Not sure'),
    ])
    meals_provided              = models.BooleanField(default=False)
    parking_available_flag      = models.BooleanField(default=False)
    site_access_available       = models.BooleanField(default=False)
    tools_required              = models.BooleanField(default=False)
    rubbish_removal_required    = models.BooleanField(default=False)
    after_hours_required        = models.BooleanField(default=False)
    on_site_inspection_required = models.BooleanField(default=False)
    delivery_required           = models.BooleanField(default=False)
    clean_up_required           = models.BooleanField(default=False)
    client_provide_photos       = models.BooleanField(default=False)
    warranty_followup_requested = models.BooleanField(default=False)
    materials_notes             = models.TextField(blank=True)
    parking_notes               = models.TextField(blank=True)
    special_instructions        = models.TextField(blank=True)

    removed_at          = models.DateTimeField(null=True, blank=True)
    removed_by          = models.ForeignKey('User', null=True, blank=True, on_delete=models.SET_NULL, related_name='removed_tasks')
    removal_reason      = models.TextField(blank=True)
    cancellation_reason = models.TextField(blank=True)
    backdoor_monitoring_flag  = models.BooleanField(default=False, verbose_name='Platform Circumvention Flag')
    backdoor_monitoring_note  = models.TextField(blank=True, verbose_name='Platform Circumvention Note')
    backdoor_reviewed         = models.BooleanField(default=False, verbose_name='Circumvention Reviewed')
    backdoor_reviewed_at      = models.DateTimeField(null=True, blank=True, verbose_name='Circumvention Reviewed At')
    backdoor_reviewed_by      = models.ForeignKey('User', null=True, blank=True, on_delete=models.SET_NULL, related_name='backdoor_reviewed_tasks', verbose_name='Circumvention Reviewed By')
    # Final job value (set when completed)
    final_job_value     = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    completed_at        = models.DateTimeField(null=True, blank=True)
    created_at          = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    @property
    def category_label(self):
        """Display label for `category`, live from TradeCategory (editable in admin)."""
        if not self.category:
            return ''
        return TradeCategory.get_label_map().get(self.category, self.category)

    @property
    def quote_count(self):
        return self.quotes.count()

    @property
    def is_date_range(self):
        return bool(self.preferred_date and self.preferred_date_end and self.preferred_date_end != self.preferred_date)

    @property
    def date_display(self):
        """Human-readable schedule: a range, a single date, or nothing set."""
        if self.is_date_range:
            return f'{self.preferred_date:%d %b %Y} – {self.preferred_date_end:%d %b %Y}'
        return self.preferred_date

    @property
    def budget_display(self):
        """'FJD $150.00', or 'Negotiable' when the client didn't set a
        figure (TaskForm.clean() sets budget_type='quote_needed' in that
        case). Never used for fee calculations — those are always based on
        final_job_value / the accepted quote's price, never this field."""
        if self.budget is None:
            return 'Negotiable'
        return f'FJD ${self.budget:.2f}'

    def has_quoting_appointments(self):
        return self.quoting_appointments.exists()

    def has_accepted_quote(self):
        return self.quotes.filter(status=Quote.STATUS_ACCEPTED).exists()

    def has_platform_fee(self):
        return self.platform_fees.exists()

    def flag_backdoor_monitoring(self):
        if self.status == self.STATUS_CANCELLED or self.removed_at:
            if self.has_quoting_appointments() and not (
                self.has_accepted_quote() or self.assigned_tradie or self.status == self.STATUS_COMPLETED or self.has_platform_fee()
            ):
                self.backdoor_monitoring_flag = True
                if not self.backdoor_monitoring_note:
                    self.backdoor_monitoring_note = (
                        'Potential platform circumvention: quoting appointment requested/booked, '
                        'task removed before quote acceptance or completion.'
                    )

    def clean(self):
        if self.client_workspace_id and self.client_id and self.client_workspace.owner_id != self.client_id:
            raise ValidationError({'client_workspace': 'Client workspace must belong to the task client.'})
        if (
            self.assigned_provider_workspace_id and self.assigned_tradie_id
            and self.assigned_provider_workspace.owner_id != self.assigned_tradie_id
        ):
            raise ValidationError({
                'assigned_provider_workspace': 'Assigned provider workspace must belong to the assigned tradie.',
            })

    def save(self, *args, **kwargs):
        self.flag_backdoor_monitoring()
        super().save(*args, **kwargs)


# ── Quote ─────────────────────────────────────────────────────────────────────

class Quote(models.Model):
    STATUS_PENDING  = 'pending'
    STATUS_ACCEPTED = 'accepted'
    STATUS_DECLINED = 'declined'
    STATUS_CHOICES  = [
        (STATUS_PENDING,  'Pending'),
        (STATUS_ACCEPTED, 'Accepted'),
        (STATUS_DECLINED, 'Declined'),
    ]

    task                            = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='quotes')
    tradie                          = models.ForeignKey(User, on_delete=models.CASCADE, related_name='quotes')
    provider_workspace              = models.ForeignKey(
        'Workspace', on_delete=models.PROTECT, null=True, blank=True, related_name='submitted_quotes',
    )
    price                           = models.DecimalField(max_digits=10, decimal_places=2)
    message                         = models.TextField()
    status                          = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    # New: Quote calculator and fee tracking
    vat_applicable                  = models.BooleanField(default=False, verbose_name='VAT applicable')
    vat_rate                        = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, verbose_name='VAT rate (%)')
    minimum_take_home_amount        = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    customer_facing_quote           = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    estimated_platform_fee          = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    estimated_provider_take_home    = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    fee_rule_applied                = models.CharField(max_length=100, blank=True)
    success_fee_rate_at_quote_time  = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    success_fee_cap_at_quote_time   = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    large_job_threshold_at_quote_time = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    large_job_fee_rate_at_quote_time = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    # Legacy fee fields
    base_price                      = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    include_platform_fee            = models.BooleanField(default=False)
    platform_fee_rate_at_quote_time = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)  # 7.5, 10, etc
    platform_fee_cap_at_quote_time  = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)  # FJD $75
    client_quote_total              = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    estimated_tradie_take_home      = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # Quote detail fields
    includes_materials              = models.BooleanField(default=False, verbose_name='Includes materials')
    earliest_available_date         = models.DateField(null=True, blank=True)
    estimated_job_duration          = models.CharField(max_length=100, blank=True)  # e.g. "2-3 days"
    quote_includes = models.CharField(max_length=50, blank=True, choices=[
        ('labour_only', 'Labour only'),
        ('labour_and_materials', 'Labour and materials'),
        ('materials_to_be_confirmed_after_inspection', 'Materials to be confirmed after inspection'),
        ('service_only', 'Service only'),
        ('service_plus_products', 'Service plus products/supplies'),
        ('not_applicable', 'Not applicable'),
    ])
    warranty_or_followup_included   = models.BooleanField(default=False)
    created_at                      = models.DateTimeField(auto_now_add=True)
    # Discount selected at quote time — actually applied (and consumed) when the
    # job completes and a real PlatformFee is created. Mutually exclusive.
    used_founding_credit            = models.BooleanField(default=False)
    promo_code                      = models.ForeignKey('PromoCode', null=True, blank=True, on_delete=models.SET_NULL, related_name='quotes')
    estimated_discount_amount       = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        unique_together = ('task', 'tradie')
        ordering = ['created_at']

    def __str__(self):
        return f'Quote by {self.tradie} on "{self.task}" – FJD ${self.price}'

    def clean(self):
        if (
            self.provider_workspace_id and self.tradie_id
            and self.provider_workspace.owner_id != self.tradie_id
        ):
            raise ValidationError({'provider_workspace': 'Provider workspace must belong to the quoting tradie.'})


# ── Message ───────────────────────────────────────────────────────────────────

class QuotingAppointment(models.Model):
    STATUS_REQUESTED            = 'requested'
    STATUS_ALTERNATIVE_PROPOSED = 'alternative_proposed'
    STATUS_ACCEPTED             = 'accepted'
    STATUS_DECLINED             = 'declined'
    STATUS_COMPLETED            = 'completed'
    STATUS_CANCELLED            = 'cancelled'
    STATUS_NO_SHOW              = 'no_show'
    STATUS_CHOICES   = [
        (STATUS_REQUESTED,            'Requested'),
        (STATUS_ALTERNATIVE_PROPOSED, 'Alternative times proposed'),
        (STATUS_ACCEPTED,             'Accepted'),
        (STATUS_DECLINED,             'Declined'),
        (STATUS_COMPLETED,            'Completed'),
        (STATUS_CANCELLED,            'Cancelled'),
        (STATUS_NO_SHOW,              'No show'),
    ]

    task             = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='quoting_appointments')
    client           = models.ForeignKey(User, on_delete=models.CASCADE, related_name='client_quoting_appointments')
    provider         = models.ForeignKey(User, on_delete=models.CASCADE, related_name='provider_quoting_appointments')
    appointment_note = models.TextField(blank=True)
    # Set when the client counters with different times instead of just
    # declining — see QuotingAppointmentSlot.proposed_by for where those
    # alternative slots themselves live (same table, same appointment).
    alternative_note = models.TextField(blank=True)
    status           = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_REQUESTED)
    selected_slot    = models.ForeignKey('QuotingAppointmentSlot', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Appointment request by {self.provider} for "{self.task}"'

    def selected_slot_display(self):
        if self.selected_slot:
            return (
                f'{self.selected_slot.proposed_date} '
                f'{self.selected_slot.start_time.strftime("%H:%M")}–{self.selected_slot.end_time.strftime("%H:%M")} '
                f'({self.selected_slot.proposed_date})'
            )
        return ''

    def has_selected_slot(self):
        return self.selected_slot is not None


class QuotingAppointmentSlot(models.Model):
    PROPOSED_BY_PROVIDER = 'provider'
    PROPOSED_BY_CLIENT   = 'client'
    PROPOSED_BY_CHOICES  = [
        (PROPOSED_BY_PROVIDER, 'Provider'),
        (PROPOSED_BY_CLIENT,   'Client'),
    ]

    quoting_appointment = models.ForeignKey(QuotingAppointment, on_delete=models.CASCADE, related_name='slots')
    proposed_date       = models.DateField()
    start_time          = models.TimeField()
    end_time            = models.TimeField()
    is_selected         = models.BooleanField(default=False)
    # Provider proposes the original 1-3 options; if the client counters
    # instead of accepting/declining, their alternative slots land in this
    # same table on the same appointment, tagged 'client' — lets the
    # provider then accept one the same way the client accepts theirs.
    proposed_by         = models.CharField(max_length=10, choices=PROPOSED_BY_CHOICES, default=PROPOSED_BY_PROVIDER)
    created_at          = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['proposed_date', 'start_time']

    def __str__(self):
        return f'{self.proposed_date} {self.start_time.strftime("%H:%M")}–{self.end_time.strftime("%H:%M")}'


class Message(models.Model):
    task         = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='messages')
    sender       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_messages')
    recipient    = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_messages')
    body         = models.TextField()
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at      = models.DateTimeField(null=True, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    # Edit/delete are both soft — body is never actually cleared or dropped.
    # deleted_at just flips how it renders (recipient sees "message was
    # deleted", never the real text again); edit_history keeps every prior
    # version. Both exist so a dispute/circumvention review always has the
    # real record, regardless of what either party did on their end.
    edited_at    = models.DateTimeField(null=True, blank=True)
    deleted_at   = models.DateTimeField(null=True, blank=True)
    edit_history = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'{self.sender} → {self.recipient} [{self.task}]'


# ── Public review (client → tradie) ──────────────────────────────────────────
# Displayed on the Tradie Profile page.  Safe to query in public views.

SCORE_VALIDATORS = [MinValueValidator(1), MaxValueValidator(5)]
SCORE_CHOICES    = [(i, str(i)) for i in range(1, 6)]


class PublicReview(models.Model):
    # Nullable: normal reviews go through a completed Task, but an admin can
    # also add a review with no task attached — for a local pro's past work
    # (before they joined the platform) or to correct/replace a malicious
    # review without a real job to point it at. See admin_note below.
    task               = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='public_reviews', null=True, blank=True)
    rater              = models.ForeignKey(User, on_delete=models.CASCADE, related_name='public_reviews_given')
    ratee              = models.ForeignKey(User, on_delete=models.CASCADE, related_name='public_reviews_received')
    # reviewer_workspace = rater's client workspace; reviewed_workspace = ratee's
    # provider workspace — PublicReview is always client -> tradie.
    reviewer_workspace = models.ForeignKey(
        'Workspace', on_delete=models.PROTECT, null=True, blank=True, related_name='public_reviews_written',
    )
    reviewed_workspace = models.ForeignKey(
        'Workspace', on_delete=models.PROTECT, null=True, blank=True, related_name='public_reviews_received',
    )
    # Six public rating criteria for all service providers.
    reliability_punctuality   = models.IntegerField(choices=SCORE_CHOICES, validators=SCORE_VALIDATORS)
    quote_price_accuracy      = models.IntegerField(choices=SCORE_CHOICES, validators=SCORE_VALIDATORS)
    value_for_money           = models.IntegerField(choices=SCORE_CHOICES, validators=SCORE_VALIDATORS)
    service_quality_workmanship = models.IntegerField(choices=SCORE_CHOICES, validators=SCORE_VALIDATORS)
    communication_after_service  = models.IntegerField(choices=SCORE_CHOICES, validators=SCORE_VALIDATORS)
    timeline_schedule_delivery   = models.IntegerField(choices=SCORE_CHOICES, validators=SCORE_VALIDATORS, default=5)
    comment                    = models.TextField(blank=True)
    admin_note = models.CharField(
        max_length=200, blank=True,
        verbose_name='Context (shown publicly in place of a job title when no task is linked)',
        help_text='e.g. "Renovated my kitchen, 2024" — for a review added without a platform job.',
    )
    created_at         = models.DateTimeField(auto_now_add=True)

    objects = PublicReviewManager()

    class Meta:
        unique_together = ('task', 'rater')
        verbose_name = 'Public Review (Client → Provider)'
        verbose_name_plural = 'Public Reviews (Client → Provider)'

    def __str__(self):
        return f'Review by {self.rater} for {self.ratee} on "{self.task}"'

    def clean(self):
        if self.reviewer_workspace_id and self.rater_id and self.reviewer_workspace.owner_id != self.rater_id:
            raise ValidationError({'reviewer_workspace': 'Reviewer workspace must belong to the rater.'})
        if self.reviewed_workspace_id and self.ratee_id and self.reviewed_workspace.owner_id != self.ratee_id:
            raise ValidationError({'reviewed_workspace': 'Reviewed workspace must belong to the ratee.'})

    @property
    def overall(self):
        """Compute overall rating from six public criteria (not stored)."""
        return (
            self.reliability_punctuality + self.quote_price_accuracy + self.value_for_money
            + self.service_quality_workmanship + self.communication_after_service + self.timeline_schedule_delivery
        ) / 6


class PrivateReview(models.Model):
    """
    Tradie's confidential rating of a client.

    ⚠️  ADMIN ONLY — never expose via views, URLs, or templates.
        Only import this model inside admin.py.
        Access via: PrivateReview.objects.admin_only()
    """
    task            = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='private_reviews')
    rater           = models.ForeignKey(User, on_delete=models.CASCADE, related_name='private_reviews_given')
    ratee           = models.ForeignKey(User, on_delete=models.CASCADE, related_name='private_reviews_received')
    # reviewer_workspace = rater's provider workspace; reviewed_workspace = ratee's
    # client workspace — PrivateReview is always tradie -> client.
    reviewer_workspace = models.ForeignKey(
        'Workspace', on_delete=models.PROTECT, null=True, blank=True, related_name='private_reviews_written',
    )
    reviewed_workspace = models.ForeignKey(
        'Workspace', on_delete=models.PROTECT, null=True, blank=True, related_name='private_reviews_received',
    )
    # Five private criteria
    access_readiness = models.IntegerField(choices=SCORE_CHOICES, validators=SCORE_VALIDATORS)
    scope_clarity    = models.IntegerField(choices=SCORE_CHOICES, validators=SCORE_VALIDATORS)
    communication    = models.IntegerField(choices=SCORE_CHOICES, validators=SCORE_VALIDATORS)
    payment          = models.IntegerField(choices=SCORE_CHOICES, validators=SCORE_VALIDATORS)
    conduct          = models.IntegerField(choices=SCORE_CHOICES, validators=SCORE_VALIDATORS)
    comment          = models.TextField(blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)

    objects = PrivateReviewManager()

    class Meta:
        unique_together = ('task', 'rater')
        verbose_name        = '⚠️ Private Review — Dispute Record (Tradie → Client)'
        verbose_name_plural = '⚠️ Private Reviews — Dispute Records (Tradie → Client)'

    def __str__(self):
        return f'[PRIVATE] {self.rater} rated client {self.ratee} on "{self.task}"'

    def clean(self):
        if self.reviewer_workspace_id and self.rater_id and self.reviewer_workspace.owner_id != self.rater_id:
            raise ValidationError({'reviewer_workspace': 'Reviewer workspace must belong to the rater.'})
        if self.reviewed_workspace_id and self.ratee_id and self.reviewed_workspace.owner_id != self.ratee_id:
            raise ValidationError({'reviewed_workspace': 'Reviewed workspace must belong to the ratee.'})


# ── Task Photos ───────────────────────────────────────────────────────────────

class TaskPhoto(models.Model):
    task        = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='photos')
    image       = models.ImageField(upload_to='task_photos/%Y/%m/%d/')
    caption     = models.CharField(max_length=200, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['uploaded_at']
        verbose_name = 'Task Photo'
        verbose_name_plural = 'Task Photos'

    def __str__(self):
        return f'Photo for {self.task}'


# ── Promo Codes ────────────────────────────────────────────────────────────────

class PromoCode(models.Model):
    """
    Admin-issued discount codes a tradie can apply to a quote, reducing the
    platform fee charged when that job completes. Mutually exclusive with a
    tradie's own founding-member credit — only one discount applies per quote.
    """
    DISCOUNT_FIXED   = 'fixed'
    DISCOUNT_PERCENT = 'percent'
    DISCOUNT_TYPE_CHOICES = [
        (DISCOUNT_FIXED,   'Fixed amount off (FJD)'),
        (DISCOUNT_PERCENT, 'Percentage off'),
    ]

    code           = models.CharField(max_length=30, unique=True)
    discount_type  = models.CharField(max_length=10, choices=DISCOUNT_TYPE_CHOICES, default=DISCOUNT_FIXED)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, help_text='Dollar amount, or percentage if "Percentage off" is selected.')
    active         = models.BooleanField(default=True)
    start_date     = models.DateField(null=True, blank=True, help_text='Leave blank for no start restriction.')
    end_date       = models.DateField(null=True, blank=True, help_text='Leave blank for no end restriction.')
    max_uses       = models.PositiveIntegerField(null=True, blank=True, help_text='Leave blank for unlimited uses.')
    times_used     = models.PositiveIntegerField(default=0)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Promo Code'
        verbose_name_plural = 'Promo Codes'

    def __str__(self):
        return self.code

    def is_valid_now(self):
        from django.utils import timezone
        today = timezone.localdate()
        if not self.active:
            return False
        if self.start_date and today < self.start_date:
            return False
        if self.end_date and today > self.end_date:
            return False
        if self.max_uses is not None and self.times_used >= self.max_uses:
            return False
        return True

    def calculate_discount(self, fee_amount):
        """Discount amount for a given platform fee, capped so it never exceeds that fee."""
        fee_amount = Decimal(str(fee_amount))
        if self.discount_type == self.DISCOUNT_PERCENT:
            discount = (fee_amount * self.discount_value / Decimal('100')).quantize(Decimal('0.01'))
        else:
            discount = self.discount_value
        return min(discount, fee_amount)


# ── Platform Settings (fees) ──────────────────────────────────────────────────

class PlatformSettings(models.Model):
    """
    Configurable platform-wide settings.
    Only one active record at any time.
    """
    success_fee_rate = models.DecimalField(
        max_digits=5, decimal_places=2,
        default=7.5,
        help_text='Success fee percentage (e.g. 7.5 for 7.5%)'
    )
    market_fee_rate = models.DecimalField(
        max_digits=5, decimal_places=2,
        default=2.0,
        help_text='Platform fee percentage applied to Market listing sales (e.g. 2 for 2%) — separate from the job/task success fee rate.'
    )
    success_fee_cap = models.DecimalField(
        max_digits=10, decimal_places=2,
        default=75.00,
        help_text='Maximum fee cap per job (e.g. 75.00 for FJD $75)'
    )
    large_job_threshold = models.DecimalField(
        max_digits=10, decimal_places=2,
        default=5000.00,
        help_text='Customer-facing quote threshold for large job fee rate (e.g. 5000.00)'
    )
    large_job_fee_rate = models.DecimalField(
        max_digits=5, decimal_places=2,
        default=3.00,
        help_text='Fee percentage for large jobs over threshold'
    )
    terms_version = models.CharField(
        max_length=20, default='1.0',
        help_text='Active terms version presented to users at registration'
    )
    active        = models.BooleanField(default=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Platform Settings'
        verbose_name_plural = 'Platform Settings'

    def __str__(self):
        return f'Platform Settings – {self.success_fee_rate}% / FJD ${self.success_fee_cap} cap'

    def save(self, *args, **kwargs):
        """Enforce at most one active row regardless of call path — the
        admin's has_add_permission() only blocked *creating* a second one;
        flipping an existing inactive row's `active` back to True bypassed
        it entirely, and get_active() has no defined behaviour for which
        row wins if more than one is active."""
        super().save(*args, **kwargs)
        if self.active:
            PlatformSettings.objects.exclude(pk=self.pk).filter(active=True).update(active=False)

    @classmethod
    def get_active(cls):
        """Get the active settings record."""
        return cls.objects.filter(active=True).first() or cls.objects.create()


# ── Platform Fee (created on job completion) ──────────────────────────────────

class PlatformFee(models.Model):
    STATUS_PENDING  = 'pending'
    STATUS_INVOICED = 'invoiced'
    STATUS_PAID     = 'paid'
    STATUS_WAIVED   = 'waived'
    STATUS_OVERDUE  = 'overdue'
    STATUS_CHOICES  = [
        (STATUS_PENDING,  'Pending'),
        (STATUS_INVOICED, 'Invoiced'),
        (STATUS_PAID,     'Paid'),
        (STATUS_WAIVED,   'Waived'),
        (STATUS_OVERDUE,  'Overdue'),
    ]

    task               = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='platform_fees')
    tradie             = models.ForeignKey(User, on_delete=models.CASCADE, related_name='platform_fees')
    provider_workspace = models.ForeignKey(
        'Workspace', on_delete=models.PROTECT, null=True, blank=True, related_name='platform_fees',
    )
    final_job_value    = models.DecimalField(max_digits=10, decimal_places=2)
    fee_rate           = models.DecimalField(max_digits=5, decimal_places=2)  # Stored for audit trail
    fee_cap            = models.DecimalField(max_digits=10, decimal_places=2)  # Stored for audit trail
    gross_fee_amount   = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)  # Before discount
    discount_amount    = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    fee_amount         = models.DecimalField(max_digits=10, decimal_places=2)  # What's actually owed (after discount)
    status             = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    created_at         = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Platform Fee'
        verbose_name_plural = 'Platform Fees'

    def __str__(self):
        return f'Fee: FJD ${self.fee_amount} on task "{self.task}" ({self.status})'

    def clean(self):
        if (
            self.provider_workspace_id and self.tradie_id
            and self.provider_workspace.owner_id != self.tradie_id
        ):
            raise ValidationError({'provider_workspace': 'Provider workspace must belong to the fee tradie.'})


# ── Invoice ───────────────────────────────────────────────────────────────────

class Invoice(models.Model):
    STATUS_DRAFT   = 'draft'
    STATUS_SENT    = 'sent'
    STATUS_PAID    = 'paid'
    STATUS_OVERDUE = 'overdue'
    STATUS_VOID    = 'void'
    STATUS_CHOICES = [
        (STATUS_DRAFT,   'Draft'),
        (STATUS_SENT,    'Sent'),
        (STATUS_PAID,    'Paid'),
        (STATUS_OVERDUE, 'Overdue'),
        (STATUS_VOID,    'Void'),
    ]

    tradie          = models.ForeignKey(User, on_delete=models.CASCADE, related_name='invoices')
    provider_workspace = models.ForeignKey(
        'Workspace', on_delete=models.PROTECT, null=True, blank=True, related_name='invoices',
    )
    invoice_number  = models.CharField(max_length=50, unique=True)
    period_start    = models.DateField(null=True, blank=True)
    period_end      = models.DateField(null=True, blank=True)
    total_amount    = models.DecimalField(max_digits=10, decimal_places=2)
    status          = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    due_date        = models.DateField()
    created_at      = models.DateTimeField(auto_now_add=True)
    sent_at         = models.DateTimeField(null=True, blank=True)
    paid_at         = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Invoice'
        verbose_name_plural = 'Invoices'

    def __str__(self):
        return f'Invoice {self.invoice_number} – {self.tradie} – FJD ${self.total_amount}'

    def clean(self):
        if (
            self.provider_workspace_id and self.tradie_id
            and self.provider_workspace.owner_id != self.tradie_id
        ):
            raise ValidationError({'provider_workspace': 'Provider workspace must belong to the invoice tradie.'})

    @property
    def is_overdue(self):
        from django.utils import timezone
        return self.status in (self.STATUS_SENT, self.STATUS_OVERDUE) and self.due_date < timezone.localdate()

    def void(self):
        """Void the invoice and return any invoiced PlatformFees to pending (unless already paid)."""
        PlatformFee.objects.filter(
            invoice_lines__invoice=self, status=PlatformFee.STATUS_INVOICED
        ).update(status=PlatformFee.STATUS_PENDING)
        self.status = self.STATUS_VOID
        self.save(update_fields=['status'])


# ── Invoice Line ──────────────────────────────────────────────────────────────

class InvoiceLine(models.Model):
    LINE_TYPE_CHOICES = [
        ('platform_fee',              'Platform Fee'),
        ('platform_circumvention_fee','Platform Circumvention Fee'),
        ('adjustment',                'Adjustment'),
        ('recovery_cost',             'Recovery Cost'),
    ]

    invoice         = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='lines')
    platform_fee    = models.ForeignKey(PlatformFee, on_delete=models.SET_NULL, null=True, blank=True, related_name='invoice_lines')
    task            = models.ForeignKey(Task, on_delete=models.SET_NULL, null=True, blank=True, related_name='invoice_lines')
    line_type       = models.CharField(max_length=30, choices=LINE_TYPE_CHOICES, default='platform_fee', blank=True)
    description     = models.TextField()
    final_job_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    fee_rate        = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    amount          = models.DecimalField(max_digits=10, decimal_places=2)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']
        verbose_name = 'Invoice Line Item'
        verbose_name_plural = 'Invoice Line Items'

    def __str__(self):
        return f'{self.invoice} – {self.description} – FJD ${self.amount}'


# ── Invoice Notification (in-platform / email / SMS log) ───────────────────────

class InvoiceNotification(models.Model):
    CHANNEL_IN_PLATFORM = 'in_platform'
    CHANNEL_EMAIL       = 'email'
    CHANNEL_SMS         = 'sms'
    CHANNEL_CHOICES = [
        (CHANNEL_IN_PLATFORM, 'In-platform message'),
        (CHANNEL_EMAIL,       'Email'),
        (CHANNEL_SMS,         'SMS / phone log'),
    ]

    invoice    = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='notifications')
    recipient  = models.ForeignKey(User, on_delete=models.CASCADE, related_name='invoice_notifications')
    channel    = models.CharField(max_length=20, choices=CHANNEL_CHOICES)
    subject    = models.CharField(max_length=200, blank=True)
    body       = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Invoice Notification'
        verbose_name_plural = 'Invoice Notifications'

    def __str__(self):
        return f'{self.get_channel_display()} to {self.recipient} – {self.invoice.invoice_number}'


# ── Sponsor / Ad Banner ────────────────────────────────────────────────────────

class Sponsor(models.Model):
    PLACEMENT_CHOICES = [
        ('homepage',               'Homepage'),
        ('browse_tasks_sidebar',   'Browse Tasks Sidebar'),
        ('task_detail_sidebar',    'Task Detail Sidebar'),
        ('client_dashboard',       'Client Dashboard'),
        ('tradie_dashboard',       'Tradie Dashboard'),
        ('how_it_works',           'How It Works'),
    ]

    business_name  = models.CharField(max_length=200)
    banner_image   = models.ImageField(upload_to='sponsors/')
    destination_url = models.URLField()
    placements     = models.JSONField(default=list)  # list of placement slugs; can span multiple pages
    start_date     = models.DateField()
    end_date       = models.DateField()
    active         = models.BooleanField(default=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Sponsor / Ad Banner'
        verbose_name_plural = 'Sponsors / Ad Banners'

    def __str__(self):
        return f'{self.business_name} – {", ".join(self.placements)}'

    @classmethod
    def get_active_for_placement(cls, placement):
        """
        Get active sponsors that include the given placement among their pages.
        Filtered in Python rather than via a JSONField `contains` lookup, since
        that lookup isn't supported on SQLite (only Postgres/MySQL) — this way
        works identically on both local dev and production.
        """
        from django.utils import timezone
        today = timezone.localdate()
        candidates = cls.objects.filter(
            active=True,
            start_date__lte=today,
            end_date__gte=today,
        )
        return [s for s in candidates if placement in s.placements]


# ── Terms Acceptance ─────────────────────────────────────────────────────────────

class TermsAcceptance(models.Model):
    user                           = models.ForeignKey(User, on_delete=models.CASCADE, related_name='terms_acceptances')
    terms_version                  = models.CharField(max_length=20)
    accepted_at                    = models.DateTimeField(auto_now_add=True)
    ip_address                     = models.GenericIPAddressField(null=True, blank=True)
    user_agent                     = models.TextField(blank=True)
    accepted_platform_circumvention = models.BooleanField(default=False, verbose_name='Accepted Platform Circumvention Fee policy')
    accepted_invoicing_terms       = models.BooleanField(default=False, verbose_name='Accepted invoicing / payment obligations')

    class Meta:
        ordering = ['-accepted_at']
        verbose_name = 'Terms Acceptance'
        verbose_name_plural = 'Terms Acceptances'

    def __str__(self):
        return f'{self.user} – v{self.terms_version} at {self.accepted_at:%Y-%m-%d %H:%M}'


# ── Platform Circumvention Case ───────────────────────────────────────────────

class PlatformCircumventionCase(models.Model):
    STATUS_OPEN      = 'open'
    STATUS_INVOICED  = 'invoiced'
    STATUS_PAID      = 'paid'
    STATUS_WAIVED    = 'waived'
    STATUS_DISPUTED  = 'disputed'
    STATUS_CLOSED    = 'closed'
    STATUS_CHOICES   = [
        (STATUS_OPEN,     'Open'),
        (STATUS_INVOICED, 'Invoiced'),
        (STATUS_PAID,     'Paid'),
        (STATUS_WAIVED,   'Waived'),
        (STATUS_DISPUTED, 'Disputed'),
        (STATUS_CLOSED,   'Closed'),
    ]

    client             = models.ForeignKey(User, on_delete=models.CASCADE, related_name='circumvention_cases_as_client')
    provider           = models.ForeignKey(User, on_delete=models.CASCADE, related_name='circumvention_cases_as_provider')
    task               = models.ForeignKey(Task, on_delete=models.SET_NULL, null=True, blank=True, related_name='circumvention_cases')
    total_job_value    = models.DecimalField(max_digits=10, decimal_places=2)
    fee_percentage     = models.DecimalField(max_digits=5, decimal_places=2, default=5.00)
    minimum_fee        = models.DecimalField(max_digits=10, decimal_places=2, default=50.00)
    client_fee_amount  = models.DecimalField(max_digits=10, decimal_places=2)
    provider_fee_amount = models.DecimalField(max_digits=10, decimal_places=2)
    status             = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN)
    evidence_notes     = models.TextField(blank=True)
    created_at         = models.DateTimeField(auto_now_add=True)
    reviewed_by        = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='circumvention_cases_reviewed')
    reviewed_at        = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Platform Circumvention Case'
        verbose_name_plural = 'Platform Circumvention Cases'

    def __str__(self):
        return f'Circumvention Case #{self.pk}: {self.client} & {self.provider} – FJD ${self.client_fee_amount} + ${self.provider_fee_amount} ({self.status})'

    @classmethod
    def calculate_fee(cls, total_job_value, fee_percentage=None, minimum_fee=None):
        from decimal import Decimal
        if fee_percentage is None:
            fee_percentage = Decimal('5.00')
        if minimum_fee is None:
            minimum_fee = Decimal('50.00')
        total_job_value = Decimal(str(total_job_value))
        fee_percentage  = Decimal(str(fee_percentage))
        minimum_fee     = Decimal(str(minimum_fee))
        fee = total_job_value * fee_percentage / Decimal('100')
        return max(fee, minimum_fee)


# ── Platform Notice (admin-issued communications) ────────────────────────────

class PlatformNotice(models.Model):
    TYPE_WELCOME           = 'welcome'
    TYPE_INVOICE           = 'invoice'
    TYPE_PAYMENT_REMINDER  = 'payment_reminder'
    TYPE_CIRCUMVENTION     = 'circumvention'
    TYPE_TERMS_UPDATE      = 'terms_update'
    TYPE_GENERAL           = 'general'
    TYPE_NEW_QUOTE         = 'new_quote'
    TYPE_NEW_MESSAGE       = 'new_message'
    TYPE_NEW_JOB_MATCH     = 'new_job_match'
    TYPE_NEW_MARKET_ORDER  = 'new_market_order'
    TYPE_MARKET_ORDER_UPDATE = 'market_order_update'
    TYPE_ACCOUNT_MIGRATED  = 'account_migrated'
    TYPE_CHOICES = [
        (TYPE_WELCOME,          'Welcome Message'),
        (TYPE_INVOICE,          'Invoice Notice'),
        (TYPE_PAYMENT_REMINDER, 'Payment Reminder'),
        (TYPE_CIRCUMVENTION,    'Platform Circumvention Notice'),
        (TYPE_TERMS_UPDATE,     'Terms Update Notice'),
        (TYPE_GENERAL,          'General Notice'),
        (TYPE_NEW_QUOTE,        'New Quote Received'),
        (TYPE_NEW_MESSAGE,      'New Message'),
        (TYPE_NEW_JOB_MATCH,    'New Job Match'),
        (TYPE_NEW_MARKET_ORDER, 'New Market Order'),
        (TYPE_MARKET_ORDER_UPDATE, 'Market Order Update'),
        (TYPE_ACCOUNT_MIGRATED, 'Account Migrated to Local Professional'),
    ]

    CHANNEL_EMAIL       = 'email'
    CHANNEL_IN_PLATFORM = 'in_platform'
    CHANNEL_SMS         = 'sms'
    CHANNEL_CHOICES = [
        (CHANNEL_EMAIL,       'Email'),
        (CHANNEL_IN_PLATFORM, 'In-Platform'),
        (CHANNEL_SMS,         'SMS'),
    ]

    recipient   = models.ForeignKey(User, on_delete=models.CASCADE, related_name='platform_notices')
    notice_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    channel     = models.CharField(max_length=20, choices=CHANNEL_CHOICES, default=CHANNEL_EMAIL)
    subject     = models.CharField(max_length=200)
    body        = models.TextField()
    sent_by     = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='notices_sent')
    sent_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-sent_at']
        verbose_name = 'Platform Notice'
        verbose_name_plural = 'Platform Notices'

    def __str__(self):
        return f'{self.get_notice_type_display()} → {self.recipient} – {self.subject}'


# ── Market (local professionals selling items/serves, e.g. baked goods) ────────

class MarketListing(models.Model):
    FULFILLMENT_PICKUP   = 'pickup'
    FULFILLMENT_DELIVERY = 'delivery'
    FULFILLMENT_BOTH     = 'both'
    FULFILLMENT_CHOICES = [
        (FULFILLMENT_PICKUP,   'Pickup only'),
        (FULFILLMENT_DELIVERY, 'Delivery only'),
        (FULFILLMENT_BOTH,     'Pickup or delivery'),
    ]

    ORDER_MODE_AUTO     = 'auto'
    ORDER_MODE_APPROVAL = 'approval'
    ORDER_MODE_CHOICES = [
        (ORDER_MODE_AUTO,     'Auto-accept orders'),
        (ORDER_MODE_APPROVAL, 'Require my approval'),
    ]

    STATUS_ACTIVE = 'active'
    STATUS_CLOSED = 'closed'
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Active'),
        (STATUS_CLOSED, 'Closed'),
    ]

    # Market's own content taxonomy — distinct from TradeCategory (which is
    # for job/trade skills like "plumbing"), since Market items are physical
    # goods for sale, not services.
    CATEGORY_FOOD         = 'food'
    CATEGORY_CLOTHES_TOYS = 'clothes_toys'
    CATEGORY_FURNITURE    = 'furniture'
    CATEGORY_TOOLS_PARTS  = 'tools_parts'
    CATEGORY_OTHER        = 'other'
    CATEGORY_CHOICES = [
        (CATEGORY_FOOD,         '🍽️ Food'),
        (CATEGORY_CLOTHES_TOYS, '🧸 Clothes & Toys'),
        (CATEGORY_FURNITURE,    '🪑 Furniture'),
        (CATEGORY_TOOLS_PARTS,  '🔧 Tools & Spare Parts'),
        (CATEGORY_OTHER,        '📦 Other'),
    ]

    FOOD_TYPE_COOKED  = 'cooked'
    FOOD_TYPE_PREMADE = 'premade'
    FOOD_TYPE_PRODUCE = 'produce'
    FOOD_TYPE_CHOICES = [
        (FOOD_TYPE_COOKED,  'Cooked'),
        (FOOD_TYPE_PREMADE, 'Premade (uncooked)'),
        (FOOD_TYPE_PRODUCE, 'Produce'),
    ]

    seller      = models.ForeignKey(User, on_delete=models.CASCADE, related_name='market_listings')
    category    = models.CharField(max_length=20, choices=CATEGORY_CHOICES, db_index=True)
    food_type   = models.CharField(max_length=20, choices=FOOD_TYPE_CHOICES, blank=True, verbose_name='Food type (if category is Food)')
    title       = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    photo       = models.ImageField(upload_to='market_listings/%Y/%m/%d/', blank=True)

    # Calculator inputs/results — all stored for transparency, same pattern as Quote's fee snapshot.
    vat_applicable      = models.BooleanField(default=False, verbose_name='VAT applicable')
    vat_rate            = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, verbose_name='VAT rate (%)')
    take_home_per_unit  = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Take-home per unit (FJD)')
    price_per_unit      = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Buyer price per unit (FJD)')
    fee_rate_at_listing = models.DecimalField(max_digits=5, decimal_places=2, verbose_name='Platform fee rate used (%)')

    units_available = models.PositiveIntegerField()
    units_sold       = models.PositiveIntegerField(default=0)

    fulfillment_method = models.CharField(max_length=10, choices=FULFILLMENT_CHOICES, default=FULFILLMENT_PICKUP)
    pickup_town          = models.CharField(max_length=50, blank=True, choices=TOWN_CHOICES)
    delivery_towns         = models.JSONField(default=list, blank=True)  # list of town keys from TOWN_CHOICES

    order_mode = models.CharField(max_length=10, choices=ORDER_MODE_CHOICES, default=ORDER_MODE_APPROVAL)
    available_dates = models.JSONField(default=list)  # list of 'YYYY-MM-DD' strings the buyer must choose from

    # Whether the seller has opted to spend down their Market founding-seller
    # credit (if any) against the platform fee on orders from this listing.
    use_founding_credit = models.BooleanField(default=False, verbose_name='Apply founding seller credit to platform fees on this listing')

    status     = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Market Listing'
        verbose_name_plural = 'Market Listings'

    def __str__(self):
        return f'{self.title} ({self.seller})'

    def units_remaining(self):
        return max(self.units_available - self.units_sold, 0)

    def has_future_dates(self):
        """Whether at least one available date hasn't passed yet — a listing
        whose dates have all lapsed shouldn't be orderable even if units and
        status still look fine. ISO date strings sort lexicographically the
        same as chronologically, so plain string comparison is safe here."""
        from django.utils import timezone
        today = timezone.localdate().isoformat()
        return any(d >= today for d in (self.available_dates or []))

    def is_purchasable(self):
        return self.status == self.STATUS_ACTIVE and self.units_remaining() > 0 and self.has_future_dates()


class MarketOrder(models.Model):
    STATUS_PENDING   = 'pending'
    STATUS_ACCEPTED  = 'accepted'
    STATUS_DECLINED  = 'declined'
    STATUS_CANCELLED = 'cancelled'
    STATUS_FULFILLED = 'fulfilled'
    STATUS_CHOICES = [
        (STATUS_PENDING,   'Pending approval'),
        (STATUS_ACCEPTED,  'Accepted'),
        (STATUS_DECLINED,  'Declined'),
        (STATUS_CANCELLED, 'Cancelled'),
        (STATUS_FULFILLED, 'Fulfilled'),
    ]

    FEE_PENDING  = 'pending'
    FEE_INVOICED = 'invoiced'
    FEE_PAID     = 'paid'
    FEE_WAIVED   = 'waived'
    FEE_STATUS_CHOICES = [
        (FEE_PENDING,  'Pending'),
        (FEE_INVOICED, 'Invoiced'),
        (FEE_PAID,     'Paid'),
        (FEE_WAIVED,   'Waived'),
    ]

    listing   = models.ForeignKey(MarketListing, on_delete=models.CASCADE, related_name='orders')
    buyer     = models.ForeignKey(User, on_delete=models.CASCADE, related_name='market_orders')
    quantity  = models.PositiveIntegerField()
    unit_price_at_order  = models.DecimalField(max_digits=10, decimal_places=2)  # snapshot, in case listing price changes later
    total_price            = models.DecimalField(max_digits=10, decimal_places=2)
    platform_fee_amount     = models.DecimalField(max_digits=10, decimal_places=2)

    fulfillment_method = models.CharField(max_length=10, choices=MarketListing.FULFILLMENT_CHOICES)
    delivery_town         = models.CharField(max_length=50, blank=True, choices=TOWN_CHOICES)
    requested_date          = models.DateField()

    status     = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    fee_status = models.CharField(max_length=10, choices=FEE_STATUS_CHOICES, default=FEE_PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Market Order'
        verbose_name_plural = 'Market Orders'

    def __str__(self):
        return f'{self.buyer} × {self.quantity} "{self.listing.title}" ({self.status})'


# ── Suppliers ────────────────────────────────────────────────────────────────

class SupplyCategory(models.Model):
    name       = models.CharField(max_length=50, unique=True)
    icon       = models.CharField(max_length=10, blank=True)
    slug       = models.SlugField(unique=True)
    active     = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    CHOICES_CACHE_KEY = 'supply_category_choices'

    class Meta:
        verbose_name        = 'Supply Category'
        verbose_name_plural = 'Supply Categories'
        ordering            = ['sort_order', 'name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        cache.delete(self.CHOICES_CACHE_KEY)

    def delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)
        cache.delete(self.CHOICES_CACHE_KEY)

    @classmethod
    def get_choices(cls):
        cached = cache.get(cls.CHOICES_CACHE_KEY)
        if cached is not None:
            return cached
        rows = list(cls.objects.filter(active=True).order_by('sort_order', 'name').values_list('slug', 'icon', 'name'))
        choices = [(slug, f'{icon} {name}'.strip()) for slug, icon, name in rows]
        cache.set(cls.CHOICES_CACHE_KEY, choices, 300)
        return choices

    @classmethod
    def get_label_map(cls):
        return dict(cls.get_choices())


class SupplierProfile(models.Model):
    VERIFICATION_PENDING   = 'pending'
    VERIFICATION_APPROVED  = 'approved'
    VERIFICATION_REJECTED  = 'rejected'
    VERIFICATION_SUSPENDED = 'suspended'
    VERIFICATION_STATUS_CHOICES = [
        (VERIFICATION_PENDING,   'Pending review'),
        (VERIFICATION_APPROVED,  'Approved'),
        (VERIFICATION_REJECTED,  'Rejected'),
        (VERIFICATION_SUSPENDED, 'Suspended'),
    ]

    user              = models.OneToOneField(User, on_delete=models.CASCADE, related_name='supplier_profile')
    business_name     = models.CharField(max_length=100, blank=True)
    tin               = models.CharField(max_length=50, blank=True, verbose_name='TIN Number (optional)')
    bio               = models.TextField(blank=True)
    supply_categories = models.JSONField(default=list)
    service_towns     = models.JSONField(default=list)

    tin_letter             = models.FileField(upload_to='supplier_documents/', blank=True, null=True)
    business_registration  = models.FileField(upload_to='supplier_documents/', blank=True, null=True)
    import_export_licence  = models.FileField(upload_to='supplier_documents/', blank=True, null=True)

    verification_status = models.CharField(
        max_length=20, choices=VERIFICATION_STATUS_CHOICES,
        default=VERIFICATION_PENDING, db_index=True,
    )
    documents_verified  = models.BooleanField(default=False)
    verification_notes  = models.TextField(blank=True)

    is_founding_member             = models.BooleanField(default=False)
    founding_member_credit_balance = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        verbose_name        = 'Supplier Profile'
        verbose_name_plural = 'Supplier Profiles'

    def __str__(self):
        return self.business_name or self.user.full_name

    def save(self, *args, **kwargs):
        self.documents_verified = (self.verification_status == self.VERIFICATION_APPROVED)
        super().save(*args, **kwargs)

    def can_receive_enquiries(self):
        return self.verification_status not in (self.VERIFICATION_REJECTED, self.VERIFICATION_SUSPENDED)

    def enquiry_block_reason(self):
        if self.verification_status == self.VERIFICATION_REJECTED:
            return 'Your supplier account has been rejected. Please contact support.'
        if self.verification_status == self.VERIFICATION_SUSPENDED:
            return 'Your supplier account is currently suspended. Please contact support.'
        return ''

    def supply_categories_display(self):
        lookup = SupplyCategory.get_label_map()
        return [lookup.get(s, s) for s in (self.supply_categories or [])]

    def service_towns_display(self):
        return ', '.join(self.service_towns or [])

    def public_completed_enquiry_count(self):
        return SupplierEnquiry.objects.filter(supplier=self.user, status=SupplierEnquiry.STATUS_ACCEPTED).count()

    def get_public_rating_breakdown(self):
        from django.db.models import Avg
        reviews = PublicReview.objects.filter(ratee=self.user)
        if not reviews.exists():
            return None
        breakdown = reviews.aggregate(
            reliability_punctuality=Avg('reliability_punctuality'),
            quote_price_accuracy=Avg('quote_price_accuracy'),
            value_for_money=Avg('value_for_money'),
            service_quality_workmanship=Avg('service_quality_workmanship'),
            communication_after_service=Avg('communication_after_service'),
            timeline_schedule_delivery=Avg('timeline_schedule_delivery'),
        )
        if breakdown['reliability_punctuality']:
            values = [v for v in breakdown.values() if v is not None]
            breakdown['overall'] = sum(values) / len(values)
        return breakdown


class SupplierEnquiry(models.Model):
    STATUS_OPEN     = 'open'
    STATUS_QUOTED   = 'quoted'
    STATUS_ACCEPTED = 'accepted'
    STATUS_CLOSED   = 'closed'
    STATUS_CHOICES  = [
        (STATUS_OPEN,     'Open'),
        (STATUS_QUOTED,   'Quoted'),
        (STATUS_ACCEPTED, 'Accepted'),
        (STATUS_CLOSED,   'Closed'),
    ]

    client      = models.ForeignKey(User, on_delete=models.CASCADE, related_name='supplier_enquiries')
    supplier    = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_enquiries')
    title       = models.CharField(max_length=200)
    description = models.TextField()
    town        = models.CharField(max_length=50, choices=TOWN_CHOICES)
    status      = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['-created_at']
        verbose_name        = 'Supplier Enquiry'
        verbose_name_plural = 'Supplier Enquiries'

    def __str__(self):
        return f'Enquiry: "{self.title}" ({self.client} → {self.supplier})'


class SupplierQuote(models.Model):
    STATUS_PENDING               = 'pending'
    STATUS_ACCEPTED              = 'accepted'
    STATUS_MODIFICATION_REQUESTED = 'modification_requested'
    STATUS_REJECTED              = 'rejected'
    STATUS_CHOICES = [
        (STATUS_PENDING,                'Pending'),
        (STATUS_ACCEPTED,               'Accepted'),
        (STATUS_MODIFICATION_REQUESTED, 'Modification Requested'),
        (STATUS_REJECTED,               'Rejected'),
    ]

    enquiry             = models.ForeignKey(SupplierEnquiry, on_delete=models.CASCADE, related_name='quotes')
    supplier            = models.ForeignKey(User, on_delete=models.CASCADE, related_name='supplier_quotes')
    items               = models.JSONField(default=list)
    vep_subtotal        = models.DecimalField(max_digits=10, decimal_places=2)
    vat_rate            = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('9.00'))
    vat_amount          = models.DecimalField(max_digits=10, decimal_places=2)
    platform_fee_rate   = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('3.00'))
    platform_fee_amount = models.DecimalField(max_digits=10, decimal_places=2)
    total               = models.DecimalField(max_digits=10, decimal_places=2)
    message             = models.TextField(blank=True)
    lead_time           = models.CharField(max_length=100, blank=True)
    valid_until         = models.DateField(null=True, blank=True)
    status              = models.CharField(max_length=25, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    modification_note   = models.TextField(blank=True)
    created_at          = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['-created_at']
        verbose_name        = 'Supplier Quote'
        verbose_name_plural = 'Supplier Quotes'

    def __str__(self):
        return f'Quote FJD ${self.total} on "{self.enquiry.title}" ({self.status})'


class SupplierMessage(models.Model):
    enquiry      = models.ForeignKey(SupplierEnquiry, on_delete=models.CASCADE, related_name='messages')
    sender       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_supplier_messages')
    recipient    = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_supplier_messages')
    body         = models.TextField()
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at      = models.DateTimeField(null=True, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    # See Message.deleted_at / edit_history — same soft-edit/soft-delete
    # rationale, kept consistent across both message types.
    edited_at    = models.DateTimeField(null=True, blank=True)
    deleted_at   = models.DateTimeField(null=True, blank=True)
    edit_history = models.JSONField(default=list, blank=True)

    class Meta:
        ordering            = ['created_at']
        verbose_name        = 'Supplier Message'
        verbose_name_plural = 'Supplier Messages'

    def __str__(self):
        return f'Msg from {self.sender} on enquiry #{self.enquiry_id}'


# ── Content reporting & user blocking ─────────────────────────────────────────

class ContentReport(models.Model):
    """User-submitted report of abusive/inappropriate content or conduct —
    reviewed by staff in the admin (see admin.py). Not auto-actioned;
    staff decide the outcome and record it here."""
    REASON_SPAM           = 'spam'
    REASON_HARASSMENT     = 'harassment'
    REASON_INAPPROPRIATE  = 'inappropriate'
    REASON_FRAUD          = 'fraud'
    REASON_OTHER          = 'other'
    REASON_CHOICES = [
        (REASON_SPAM,          'Spam or scam'),
        (REASON_HARASSMENT,    'Harassment or abuse'),
        (REASON_INAPPROPRIATE, 'Inappropriate content'),
        (REASON_FRAUD,         'Fraud or misrepresentation'),
        (REASON_OTHER,         'Other'),
    ]

    TYPE_USER    = 'user'
    TYPE_TASK    = 'task'
    TYPE_MESSAGE = 'message'
    TYPE_REVIEW  = 'review'
    TYPE_CHOICES = [
        (TYPE_USER,    'User / Profile'),
        (TYPE_TASK,    'Task'),
        (TYPE_MESSAGE, 'Message'),
        (TYPE_REVIEW,  'Review'),
    ]

    STATUS_OPEN      = 'open'
    STATUS_ACTIONED  = 'actioned'
    STATUS_DISMISSED = 'dismissed'
    STATUS_CHOICES = [
        (STATUS_OPEN,      'Open'),
        (STATUS_ACTIONED,  'Actioned'),
        (STATUS_DISMISSED, 'Dismissed'),
    ]

    reporter      = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reports_filed')
    reported_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reports_received', null=True, blank=True)
    report_type   = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_USER)
    task          = models.ForeignKey(Task, on_delete=models.SET_NULL, null=True, blank=True, related_name='reports')
    reference_note = models.CharField(max_length=255, blank=True, help_text='e.g. which message/review this report is about')

    reason  = models.CharField(max_length=20, choices=REASON_CHOICES)
    details = models.TextField(blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    reviewed_by     = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='reports_reviewed')
    reviewed_at     = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Content Report'
        verbose_name_plural = 'Content Reports'

    def __str__(self):
        return f'Report by {self.reporter} – {self.get_reason_display()} ({self.status})'


class UserBlock(models.Model):
    """One-directional block: blocker no longer sends/receives Platform
    messages with blocked (see the block check in views.conversation and
    views.supplier_enquiry_messages). Blocking is not mutual by itself —
    the blocked user isn't notified and can independently block back."""
    blocker = models.ForeignKey(User, on_delete=models.CASCADE, related_name='blocks_made')
    blocked = models.ForeignKey(User, on_delete=models.CASCADE, related_name='blocked_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'User Block'
        verbose_name_plural = 'User Blocks'
        constraints = [
            models.UniqueConstraint(fields=['blocker', 'blocked'], name='unique_user_block'),
        ]

    def __str__(self):
        return f'{self.blocker} blocked {self.blocked}'

    def clean(self):
        if self.blocker_id and self.blocked_id and self.blocker_id == self.blocked_id:
            raise ValidationError('A user cannot block themselves.')

    @classmethod
    def exists_between(cls, user_a, user_b):
        """True if either user has blocked the other — used to gate
        message-sending in both directions regardless of who blocked whom."""
        return cls.objects.filter(
            models.Q(blocker=user_a, blocked=user_b) | models.Q(blocker=user_b, blocked=user_a)
        ).exists()


# ── Signals ─────────────────────────────────────────────────────────────────────
from django.db.models.signals import pre_delete
from django.dispatch import receiver


@receiver(pre_delete, sender=Invoice)
def _release_platform_fees_on_invoice_delete(sender, instance, **kwargs):
    """If an invoice is deleted, return any invoiced PlatformFees to pending (unless already paid)."""
    PlatformFee.objects.filter(
        invoice_lines__invoice=instance, status=PlatformFee.STATUS_INVOICED
    ).update(status=PlatformFee.STATUS_PENDING)
