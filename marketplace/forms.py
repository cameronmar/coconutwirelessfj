import os
from datetime import datetime
from decimal import Decimal

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction

from .constants import TOWN_CHOICES, EXPERIENCE_CHOICES, FOUNDING_MEMBER_SLOTS, FOUNDING_MEMBER_CREDIT
from .models import User, TradieProfile, Task, Quote, Message, TradeCategory, TaskPhoto, MarketListing, MarketOrder, PlatformSettings, SupplyCategory, SupplierProfile, ContentReport
from . import workspaces


# ── Helpers ───────────────────────────────────────────────────────────────────

def _input(placeholder='', type_='text', **kwargs):
    return forms.TextInput(attrs={'placeholder': placeholder, 'class': 'form-input', **kwargs})


def _select(**kwargs):
    return forms.Select(attrs={'class': 'form-input', **kwargs})


MAX_VERIFICATION_DOCUMENT_SIZE = 10 * 1024 * 1024  # 10MB — scans/photos of documents, not thumbnails
ALLOWED_VERIFICATION_DOCUMENT_TYPES = {
    'application/pdf': ('.pdf',),
    'image/jpeg':       ('.jpg', '.jpeg'),
    'image/png':        ('.png',),
}


def _clean_verification_document(file):
    """Shared validation for tin_letter/business_licence/public_liability_
    insurance/electrical_contractors_licence/plumber_licence — previously
    these FileFields accepted any file of any size, unlike MarketListing's
    clean_photo(). These are stored (S3 in production) and later linked to
    directly from the Django admin, so an oversized upload is a storage-
    cost concern and an unexpected file type is a content-safety concern."""
    if not file:
        return file
    if hasattr(file, 'size') and file.size > MAX_VERIFICATION_DOCUMENT_SIZE:
        raise ValidationError('File must be smaller than 10MB.')
    ext = os.path.splitext(getattr(file, 'name', '') or '')[1].lower()
    allowed_extensions = ALLOWED_VERIFICATION_DOCUMENT_TYPES.get(getattr(file, 'content_type', None))
    if not allowed_extensions or ext not in allowed_extensions:
        raise ValidationError('Only PDF, JPG, or PNG files are accepted.')
    return file


def _validate_closed_beta_email(email, gate_enabled):
    if not gate_enabled:
        return

    if email in settings.BETA_ALLOWED_EMAILS:
        return

    domain = email.rsplit('@', 1)[-1].lower()
    if domain in settings.BETA_ALLOWED_DOMAINS:
        return

    raise ValidationError('Signups are currently invite-only for closed beta. Please request access from the team.')


# ── Auth forms ────────────────────────────────────────────────────────────────

class ClientRegistrationForm(forms.Form):
    first_name       = forms.CharField(max_length=50,  widget=_input('e.g. Priya'))
    last_name        = forms.CharField(max_length=50,  widget=_input('e.g. Sharma'))
    email            = forms.EmailField(widget=_input('you@example.fj', type_='email'))
    mobile           = forms.CharField(max_length=20,  widget=_input('+679 123 4567'))
    town             = forms.ChoiceField(choices=[('', 'Select town…')] + list(TOWN_CHOICES), widget=_select())
    password         = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': 'At least 8 characters'}))
    password_confirm = forms.CharField(label='Confirm password', widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': 'Repeat password'}))
    accepted_terms   = forms.BooleanField(
        required=True,
        error_messages={'required': 'You must accept the Terms & Conditions, Privacy Policy and Platform Rules to register.'}
    )

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if User.objects.filter(email=email).exists():
            raise ValidationError('An account with this email already exists.')
        _validate_closed_beta_email(email, settings.BETA_GATE_CLIENT_SIGNUPS)
        return email

    def clean(self):
        cd = super().clean()
        p1, p2 = cd.get('password'), cd.get('password_confirm')
        if p1 and p2 and p1 != p2:
            raise ValidationError('Passwords do not match.')
        return cd

    def save(self):
        cd = self.cleaned_data
        with transaction.atomic():
            user = User.objects.create_user(
                email=cd['email'],
                password=cd['password'],
                first_name=cd['first_name'],
                last_name=cd['last_name'],
                mobile=cd['mobile'],
                town=cd['town'],
                role=User.ROLE_CLIENT,
            )
            workspaces.ensure_user_capability(user)
            workspaces.create_client_workspace(user)
        return user


class TradieRegistrationForm(forms.Form):
    first_name       = forms.CharField(max_length=50,  widget=_input('e.g. Sailosi'))
    last_name        = forms.CharField(max_length=50,  widget=_input('e.g. Tora'))
    email            = forms.EmailField(widget=_input('you@example.fj', type_='email'))
    mobile           = forms.CharField(max_length=20,  widget=_input('+679 XXX XXXX'))
    town             = forms.ChoiceField(choices=[('', 'Select town / service area…')] + list(TOWN_CHOICES), widget=_select())
    password         = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': 'At least 8 characters'}))
    password_confirm = forms.CharField(label='Confirm password', widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': 'Repeat password'}))
    # Provider profile
    business_name    = forms.CharField(max_length=100, required=False, label='Company / Business Name', widget=_input('e.g. Tora Plumbing Suva (leave blank if you work as an individual contractor)'))
    tin              = forms.CharField(max_length=50, required=False, label='TIN Number (optional)', widget=_input('e.g. P033-12345'))
    years_experience = forms.ChoiceField(choices=[('', 'Select…')] + list(EXPERIENCE_CHOICES), widget=_select())
    bio              = forms.CharField(widget=forms.Textarea(attrs={'class': 'form-input', 'rows': 4, 'placeholder': 'Tell clients about your experience, specialties and work area…'}))
    trades           = forms.MultipleChoiceField(choices=[], widget=forms.CheckboxSelectMultiple)
    service_towns    = forms.MultipleChoiceField(choices=TOWN_CHOICES, widget=forms.CheckboxSelectMultiple)
    # Verification documents
    tin_letter                    = forms.FileField(label='TIN Letter', help_text='Upload your FRCA TIN letter (PDF, JPG, or PNG, up to 10MB). Required.')
    business_licence              = forms.FileField(label='Business Licence', required=False, help_text='Optional.')
    public_liability_insurance    = forms.FileField(label='Public Liability Insurance', required=False, help_text='Optional.')
    electrical_contractors_licence = forms.FileField(label='Electrical Contractors Licence', required=False, help_text="Electrical work is safety-critical — upload this now if you have it. If not, you can still register, but your account won't be able to bid on jobs until our team has reviewed it.")
    plumber_licence               = forms.FileField(label='Plumber Licence', required=False, help_text="Plumbing work is safety-critical — upload this now if you have it. If not, you can still register, but your account won't be able to bid on jobs until our team has reviewed it.")
    # Terms acceptance
    accepted_terms                = forms.BooleanField(
        required=True,
        error_messages={'required': 'You must accept the Terms & Conditions, Privacy Policy and Platform Rules to register.'}
    )
    accepted_platform_circumvention = forms.BooleanField(
        required=True,
        error_messages={'required': 'You must acknowledge the Platform Circumvention Fee policy.'}
    )
    accepted_invoicing_terms      = forms.BooleanField(
        required=True,
        error_messages={'required': 'You must acknowledge the invoicing and payment obligations.'}
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['trades'].choices = TradeCategory.get_choices()

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if User.objects.filter(email=email).exists():
            raise ValidationError('An account with this email already exists.')
        _validate_closed_beta_email(email, settings.BETA_GATE_TRADIE_SIGNUPS)
        return email

    def clean_tin_letter(self):
        return _clean_verification_document(self.cleaned_data.get('tin_letter'))

    def clean_business_licence(self):
        return _clean_verification_document(self.cleaned_data.get('business_licence'))

    def clean_public_liability_insurance(self):
        return _clean_verification_document(self.cleaned_data.get('public_liability_insurance'))

    def clean_electrical_contractors_licence(self):
        return _clean_verification_document(self.cleaned_data.get('electrical_contractors_licence'))

    def clean_plumber_licence(self):
        return _clean_verification_document(self.cleaned_data.get('plumber_licence'))

    def clean(self):
        cd = super().clean()
        p1, p2 = cd.get('password'), cd.get('password_confirm')
        if p1 and p2 and p1 != p2:
            raise ValidationError('Passwords do not match.')
        return cd

    def save(self):
        cd = self.cleaned_data
        with transaction.atomic():
            user = User.objects.create_user(
                email=cd['email'],
                password=cd['password'],
                first_name=cd['first_name'],
                last_name=cd['last_name'],
                mobile=cd['mobile'],
                town=cd['town'],
                role=User.ROLE_TRADIE,
            )
            # Lock the (singleton) PlatformSettings row as a mutex so
            # concurrent registrations can't all read the same pre-commit
            # TradieProfile count and all slip in under FOUNDING_MEMBER_SLOTS
            # — select_for_update() on one shared row serializes callers
            # without needing a dedicated counter table.
            PlatformSettings.objects.select_for_update().filter(pk=PlatformSettings.get_active().pk).exists()
            is_founder = TradieProfile.objects.count() < FOUNDING_MEMBER_SLOTS
            profile = TradieProfile(
                user=user,
                business_name=cd.get('business_name', ''),
                tin=cd.get('tin', ''),
                years_experience=cd.get('years_experience', ''),
                bio=cd.get('bio', ''),
                trades=list(cd.get('trades', [])),
                service_towns=list(cd.get('service_towns', [])),
                is_founding_member=is_founder,
                founding_member_credit_balance=Decimal(FOUNDING_MEMBER_CREDIT) if is_founder else Decimal('0.00'),
            )
            for field in ('tin_letter', 'business_licence', 'public_liability_insurance',
                          'electrical_contractors_licence', 'plumber_licence'):
                if cd.get(field):
                    setattr(profile, field, cd[field])
            profile.save()
            workspaces.ensure_user_capability(user, can_offer_services=True)
            workspaces.create_client_workspace(user)
            workspaces.create_individual_provider_workspace(user)
        return user


class ServiceAreaForm(forms.ModelForm):
    """Lets an already-registered local pro update the towns they service,
    after signup — TradieRegistrationForm.service_towns only ever sets
    this once, at registration."""
    service_towns = forms.MultipleChoiceField(
        choices=TOWN_CHOICES, widget=forms.CheckboxSelectMultiple,
        label='Towns you service',
    )

    class Meta:
        model = TradieProfile
        fields = ['service_towns']

    def clean_service_towns(self):
        return list(self.cleaned_data['service_towns'])


class LoginForm(forms.Form):
    email       = forms.EmailField(widget=_input('you@example.fj', type_='email'))
    password    = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': 'Your password'}))
    remember_me = forms.BooleanField(required=False, label='Keep me logged in')


class PasswordResetRequestForm(forms.Form):
    email = forms.EmailField(widget=_input('you@example.fj', type_='email'))


class SetNewPasswordForm(forms.Form):
    password         = forms.CharField(label='New password', widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': 'At least 8 characters'}))
    password_confirm = forms.CharField(label='Confirm new password', widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': 'Repeat new password'}))

    def clean(self):
        cd = super().clean()
        p1, p2 = cd.get('password'), cd.get('password_confirm')
        if p1 and p2 and p1 != p2:
            raise ValidationError('Passwords do not match.')
        if p1 and len(p1) < 8:
            raise ValidationError('Password must be at least 8 characters.')
        return cd


class ChangePasswordForm(forms.Form):
    """For an already-logged-in user changing their password by choice —
    distinct from SetNewPasswordForm, which is for the locked-out reset
    flow and has no current-password field to check."""
    current_password = forms.CharField(label='Current password', widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': 'Your current password'}))
    new_password         = forms.CharField(label='New password', widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': 'At least 8 characters'}))
    new_password_confirm = forms.CharField(label='Confirm new password', widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': 'Repeat new password'}))

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        current = self.cleaned_data['current_password']
        if not self.user or not self.user.check_password(current):
            raise ValidationError('Your current password is incorrect.')
        return current

    def clean(self):
        cd = super().clean()
        p1, p2 = cd.get('new_password'), cd.get('new_password_confirm')
        if p1 and p2 and p1 != p2:
            raise ValidationError('New passwords do not match.')
        if p1 and len(p1) < 8:
            raise ValidationError('New password must be at least 8 characters.')
        return cd


# ── Task form ─────────────────────────────────────────────────────────────────

DATE_TYPE_CHOICES = [
    ('flexible', "I'm flexible / no specific date"),
    ('single',   'On a specific date'),
    ('range',    'Within a date range'),
]


def _initial_date_type(task):
    if task and task.pk and task.preferred_date_end:
        return 'range'
    if task and task.pk and task.preferred_date:
        return 'single'
    return 'flexible'


def _clean_task_dates(cleaned_data):
    """Reconciles the non-model date_type choice with preferred_date /
    preferred_date_end, shared by TaskForm and TaskDateForm so single-date,
    range, and flexible mean the same thing everywhere a job's schedule can
    be set."""
    date_type = cleaned_data.get('date_type') or 'flexible'
    start = cleaned_data.get('preferred_date')
    end = cleaned_data.get('preferred_date_end')
    if date_type == 'range':
        if not start or not end:
            raise ValidationError('Please provide both a start and end date for the date range.')
        if end < start:
            raise ValidationError('The end date must be on or after the start date.')
    elif date_type == 'single':
        if not start:
            raise ValidationError('Please choose a date.')
        cleaned_data['preferred_date_end'] = None
    else:
        cleaned_data['preferred_date'] = None
        cleaned_data['preferred_date_end'] = None
    return cleaned_data


class TaskForm(forms.ModelForm):
    category = forms.ChoiceField(
        choices=[], widget=forms.Select(attrs={'class': 'form-input'})
    )
    categories = forms.ModelMultipleChoiceField(
        queryset=TradeCategory.objects.filter(active=True).order_by('name'),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label='Additional categories (optional — for bigger jobs spanning multiple trades)',
    )
    date_type = forms.ChoiceField(
        choices=DATE_TYPE_CHOICES, required=False,
        widget=forms.RadioSelect(attrs={'class': 'form-radio'}),
    )
    budget_negotiable = forms.BooleanField(
        required=False,
        label="I'm not sure — my budget is negotiable",
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].choices = TradeCategory.get_choices()
        self.fields['categories'].label_from_instance = lambda obj: f'{obj.icon} {obj.name}'.strip()
        self.fields['date_type'].initial = _initial_date_type(self.instance)

    def clean(self):
        cd = _clean_task_dates(super().clean())
        if cd.get('budget_negotiable'):
            # Negotiable wins even if a figure was also typed in — budget_type
            # already has a 'quote_needed' choice for exactly this case, it
            # just wasn't wired to anything until now.
            cd['budget'] = None
            cd['budget_type'] = 'quote_needed'
        elif not cd.get('budget'):
            self.add_error('budget', 'Enter a budget, or check "negotiable" if you’re not sure yet.')
        return cd

    class Meta:
        model  = Task
        fields = [
            'title', 'category', 'categories', 'description', 'budget', 'town',
            'date_type', 'preferred_date', 'preferred_date_end',
            'materials_responsibility', 'meals_provided', 'parking_available_flag', 'site_access_available',
            'tools_required', 'rubbish_removal_required', 'after_hours_required', 'on_site_inspection_required',
            'delivery_required', 'clean_up_required', 'client_provide_photos', 'warranty_followup_requested',
            'materials_notes', 'parking_notes', 'access_notes', 'special_instructions',
            'urgency', 'budget_type'
        ]
        widgets = {
            'title':               forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Fix leaking tap in kitchen'}),
            'description':         forms.Textarea(attrs={'class': 'form-input', 'rows': 5, 'placeholder': 'Describe the job in detail — include make/model, measurements, or access instructions.'}),
            'budget':              forms.NumberInput(attrs={'class': 'form-input', 'placeholder': 'e.g. 150', 'min': '0', 'step': '0.01'}),
            'town':                forms.Select(attrs={'class': 'form-input'}),
            'preferred_date':      forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'preferred_date_end':  forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'materials_responsibility': forms.Select(attrs={'class': 'form-input'}),
            'meals_provided':      forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'parking_available_flag': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'site_access_available': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'tools_required':      forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'rubbish_removal_required': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'after_hours_required': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'on_site_inspection_required': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'delivery_required':   forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'clean_up_required':   forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'client_provide_photos': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'warranty_followup_requested': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'materials_notes':     forms.Textarea(attrs={'class': 'form-input', 'rows': 3, 'placeholder': 'Optional details about materials or supplier access…'}),
            'parking_notes':       forms.Textarea(attrs={'class': 'form-input', 'rows': 3, 'placeholder': 'Optional parking or entry details…'}),
            'access_notes':        forms.Textarea(attrs={'class': 'form-input', 'rows': 3, 'placeholder': 'e.g. Side gate has a code, dogs on property…'}),
            'special_instructions': forms.Textarea(attrs={'class': 'form-input', 'rows': 3, 'placeholder': 'Any special instructions for local professionals…'}),
            'urgency':             forms.Select(attrs={'class': 'form-input'}),
            'budget_type':         forms.Select(attrs={'class': 'form-input'}),
        }


class TaskDateForm(forms.ModelForm):
    """Lets a client change an already-posted task's schedule without
    reopening the full post-task form."""
    date_type = forms.ChoiceField(
        choices=DATE_TYPE_CHOICES, required=False,
        widget=forms.RadioSelect(attrs={'class': 'form-radio'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['date_type'].initial = _initial_date_type(self.instance)

    def clean(self):
        return _clean_task_dates(super().clean())

    class Meta:
        model  = Task
        fields = ['date_type', 'preferred_date', 'preferred_date_end']
        widgets = {
            'preferred_date':     forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'preferred_date_end': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
        }


class TaskPhotoForm(forms.ModelForm):
    class Meta:
        model  = TaskPhoto
        fields = ['image', 'caption']
        widgets = {
            'image':   forms.FileInput(attrs={'class': 'form-input', 'accept': 'image/*'}),
            'caption': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Kitchen tap close-up'}),
        }

    def clean_image(self):
        image = self.cleaned_data.get('image')
        max_bytes = 5 * 1024 * 1024
        if image and hasattr(image, 'size') and image.size > max_bytes:
            raise ValidationError('Photo must be smaller than 5MB.')
        return image


# ── Quote form ────────────────────────────────────────────────────────────────

class QuoteForm(forms.ModelForm):
    # Not model fields directly — resolved/validated against the requesting
    # tradie's eligibility and the PromoCode table in submit_quote().
    promo_code_input = forms.CharField(required=False, max_length=30, widget=forms.HiddenInput())

    class Meta:
        model  = Quote
        fields = [
            'minimum_take_home_amount', 'price', 'customer_facing_quote',
            'vat_applicable', 'vat_rate',
            'estimated_platform_fee', 'estimated_provider_take_home', 'fee_rule_applied',
            'success_fee_rate_at_quote_time', 'success_fee_cap_at_quote_time',
            'large_job_threshold_at_quote_time', 'large_job_fee_rate_at_quote_time',
            'include_platform_fee', 'base_price', 'includes_materials',
            'quote_includes', 'earliest_available_date', 'estimated_job_duration', 'warranty_or_followup_included',
            'used_founding_credit', 'message'
        ]
        widgets = {
            'minimum_take_home_amount':    forms.NumberInput(attrs={'class': 'form-input', 'placeholder': 'FJD $', 'min': '0', 'step': '0.01', 'id': 'id_minimum_take_home_amount'}),
            'vat_applicable':               forms.CheckboxInput(),
            'vat_rate':                     _input('e.g. 15', type_='number'),
            'used_founding_credit':        forms.HiddenInput(),
            'customer_facing_quote':       forms.HiddenInput(),
            'estimated_platform_fee':      forms.HiddenInput(),
            'estimated_provider_take_home': forms.HiddenInput(),
            'fee_rule_applied':            forms.HiddenInput(),
            'success_fee_rate_at_quote_time': forms.HiddenInput(),
            'success_fee_cap_at_quote_time': forms.HiddenInput(),
            'large_job_threshold_at_quote_time': forms.HiddenInput(),
            'large_job_fee_rate_at_quote_time': forms.HiddenInput(),
            'include_platform_fee':         forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'base_price':                   forms.NumberInput(attrs={'class': 'form-input', 'placeholder': 'e.g. 140', 'min': '0', 'step': '0.01'}),
            'price':                        forms.NumberInput(attrs={'class': 'form-input', 'placeholder': 'Customer-facing quote total (FJD $)', 'min': '0', 'step': '0.01', 'id': 'id_price'}),
            'includes_materials':           forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'quote_includes':               forms.Select(attrs={'class': 'form-input'}),
            'earliest_available_date':      forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'estimated_job_duration':       forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. 2-3 days, Half day, etc'}),
            'warranty_or_followup_included': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'message':                      forms.Textarea(attrs={'class': 'form-input', 'rows': 4, 'placeholder': 'Introduce yourself, describe your approach, and state your availability…'}),
        }

    def clean_price(self):
        price = self.cleaned_data.get('price')
        if price is not None and price <= 0:
            raise ValidationError('Customer-facing quote must be greater than zero.')
        return price

    def clean(self):
        cd = super().clean()
        if cd.get('vat_applicable') and not cd.get('vat_rate'):
            self.add_error('vat_rate', 'Enter a VAT rate, or untick "Charge VAT on this job".')
        return cd


# ── Message form ──────────────────────────────────────────────────────────────

class QuotingAppointmentForm(forms.Form):
    slot_1_date  = forms.DateField(required=False, widget=forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}))
    slot_1_start = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    slot_1_end   = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))

    slot_2_date  = forms.DateField(required=False, widget=forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}))
    slot_2_start = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    slot_2_end   = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))

    slot_3_date  = forms.DateField(required=False, widget=forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}))
    slot_3_start = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    slot_3_end   = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))

    appointment_note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-input', 'rows': 4, 'placeholder': 'I can inspect the site during any of these times. Please choose what works best.'})
    )

    def clean(self):
        cd = super().clean()
        slots = []
        for i in range(1, 4):
            date = cd.get(f'slot_{i}_date')
            start = cd.get(f'slot_{i}_start')
            end = cd.get(f'slot_{i}_end')
            filled = bool(date or start or end)
            if filled:
                if not date or not start or not end:
                    raise forms.ValidationError(f'Appointment option {i} needs a date, start time and end time.')
                if start >= end:
                    raise forms.ValidationError(f'Appointment option {i} must end after it starts.')
                slots.append({'date': date, 'start': start, 'end': end})
        if not slots:
            raise forms.ValidationError('Please offer at least one appointment option.')
        cd['slots'] = slots
        return cd

    def save(self, task, provider):
        from .models import QuotingAppointment, QuotingAppointmentSlot
        appointment = QuotingAppointment.objects.create(
            task=task,
            client=task.client,
            provider=provider,
            appointment_note=self.cleaned_data.get('appointment_note', ''),
        )
        for slot in self.cleaned_data['slots']:
            QuotingAppointmentSlot.objects.create(
                quoting_appointment=appointment,
                proposed_date=slot['date'],
                start_time=slot['start'],
                end_time=slot['end'],
            )
        return appointment


class MessageForm(forms.Form):
    body = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-input', 'rows': 2, 'placeholder': 'Type a message…', 'required': 'required'})
    )


# ── Rating forms ──────────────────────────────────────────────────────────────

SCORE_WIDGET = forms.RadioSelect

class PublicReviewForm(forms.Form):
    reliability_punctuality   = forms.ChoiceField(choices=[(i, i) for i in range(1, 6)], widget=SCORE_WIDGET)
    quote_price_accuracy      = forms.ChoiceField(choices=[(i, i) for i in range(1, 6)], widget=SCORE_WIDGET)
    value_for_money           = forms.ChoiceField(choices=[(i, i) for i in range(1, 6)], widget=SCORE_WIDGET)
    service_quality_workmanship = forms.ChoiceField(choices=[(i, i) for i in range(1, 6)], widget=SCORE_WIDGET)
    communication_after_service  = forms.ChoiceField(choices=[(i, i) for i in range(1, 6)], widget=SCORE_WIDGET)
    timeline_schedule_delivery   = forms.ChoiceField(choices=[(i, i) for i in range(1, 6)], widget=SCORE_WIDGET)
    comment                    = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-input', 'rows': 3, 'placeholder': 'Optional comment…'})
    )


class PrivateReviewForm(forms.Form):
    access_readiness = forms.ChoiceField(choices=[(i, i) for i in range(1, 6)], widget=SCORE_WIDGET)
    scope_clarity    = forms.ChoiceField(choices=[(i, i) for i in range(1, 6)], widget=SCORE_WIDGET)
    communication    = forms.ChoiceField(choices=[(i, i) for i in range(1, 6)], widget=SCORE_WIDGET)
    payment          = forms.ChoiceField(choices=[(i, i) for i in range(1, 6)], widget=SCORE_WIDGET)
    conduct          = forms.ChoiceField(choices=[(i, i) for i in range(1, 6)], widget=SCORE_WIDGET)
    comment          = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-input', 'rows': 3, 'placeholder': 'Optional note for dispute records…'})
    )


# ── Contact / Support form ───────────────────────────────────────────────────

class ContactSupportForm(forms.Form):
    TOPIC_GENERAL        = 'general'
    TOPIC_REPORT_PROBLEM = 'report_problem'
    TOPIC_CHOICES = [
        (TOPIC_GENERAL,        'General inquiry'),
        (TOPIC_REPORT_PROBLEM, 'Report a problem'),
    ]
    topic   = forms.ChoiceField(choices=TOPIC_CHOICES, initial=TOPIC_GENERAL, widget=_select())
    name    = forms.CharField(max_length=100, widget=_input('Your name'))
    email   = forms.EmailField(widget=_input('you@example.fj', type_='email'))
    subject = forms.CharField(max_length=150, widget=_input('What can we help with?'))
    message = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-input', 'rows': 5, 'placeholder': 'Tell us more…'})
    )


# ── Notification preferences ─────────────────────────────────────────────────

class NotificationPreferencesForm(forms.ModelForm):
    class Meta:
        model = User
        fields = [
            'notify_email_new_quote', 'notify_email_new_message', 'notify_email_new_job_match',
            'notify_email_new_market_order', 'notify_email_market_order_update',
        ]
        widgets = {
            'notify_email_new_quote':            forms.CheckboxInput(),
            'notify_email_new_message':           forms.CheckboxInput(),
            'notify_email_new_job_match':          forms.CheckboxInput(),
            'notify_email_new_market_order':        forms.CheckboxInput(),
            'notify_email_market_order_update':      forms.CheckboxInput(),
        }


# ── Market (local professionals selling items/serves) ───────────────────────

class MarketListingForm(forms.ModelForm):
    # Which field the seller actually typed into — the other side of the
    # calculator is authoritatively computed server-side from this one.
    calc_direction = forms.ChoiceField(
        choices=[('take_home', 'take_home'), ('price', 'price')],
        widget=forms.HiddenInput(), initial='take_home', required=False,
    )
    delivery_towns = forms.MultipleChoiceField(choices=TOWN_CHOICES, required=False, widget=forms.CheckboxSelectMultiple)
    available_dates_input = forms.CharField(
        required=True, label='Available dates',
        # Populated by the date-picker widget in the template via JS, as a
        # comma-separated list — kept as CharField so clean()'s parsing logic
        # below is unchanged regardless of how the UI collects the dates.
        widget=forms.HiddenInput(),
        help_text='Comma-separated dates buyers can choose from (YYYY-MM-DD).',
    )

    class Meta:
        model = MarketListing
        fields = [
            'title', 'description', 'photo', 'category', 'food_type',
            'vat_applicable', 'vat_rate', 'take_home_per_unit', 'price_per_unit',
            'units_available', 'fulfillment_method', 'pickup_town', 'order_mode',
            'use_founding_credit',
        ]
        widgets = {
            'title':               _input('e.g. Chocolate Mud Cake'),
            'description':         forms.Textarea(attrs={'class': 'form-input', 'rows': 4, 'placeholder': "Describe what you're selling…"}),
            'category':            _select(),
            'food_type':           _select(),
            'vat_applicable':      forms.CheckboxInput(),
            'vat_rate':            _input('e.g. 15', type_='number'),
            # Despite the model field name (kept as take_home_per_unit for
            # schema stability — see clean()), this box collects the
            # seller's desired TOTAL take-home across all units, per the
            # "the calculator has to take the amount of serves into
            # account" request — the per-unit amount is derived from it.
            'take_home_per_unit':  _input('FJD $', type_='number'),
            'price_per_unit':      _input('FJD $', type_='number'),
            'units_available':     _input('e.g. 20', type_='number'),
            'fulfillment_method':  _select(),
            'pickup_town':         _select(),
            'order_mode':          _select(),
            'use_founding_credit': forms.CheckboxInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['food_type'].required = False
        self.fields['take_home_per_unit'].required = False
        self.fields['price_per_unit'].required = False
        self.fields['vat_rate'].required = False
        self.fields['pickup_town'].required = False
        self.fields['use_founding_credit'].required = False

    def clean_photo(self):
        photo = self.cleaned_data.get('photo')
        # Market browse shows this at small thumbnail size — an unoptimized
        # multi-MB upload would slow that page down for every visitor.
        max_bytes = 5 * 1024 * 1024
        if photo and hasattr(photo, 'size') and photo.size > max_bytes:
            raise ValidationError('Photo must be smaller than 5MB.')
        return photo

    def clean(self):
        cd = super().clean()
        if cd.get('category') == MarketListing.CATEGORY_FOOD and not cd.get('food_type'):
            self.add_error('food_type', 'Select whether this food is cooked, premade, or produce.')

        vat_applicable = cd.get('vat_applicable')
        vat_rate = cd.get('vat_rate') if vat_applicable else None
        if vat_applicable and not vat_rate:
            self.add_error('vat_rate', 'Enter a VAT rate, or untick VAT applicable.')
            return cd

        units_available = cd.get('units_available')
        if not units_available or units_available <= 0:
            self.add_error('units_available', 'Enter the number of units/serves available first — the calculator needs it to work out your price per unit.')
            return cd

        from .utils import calculate_market_price_per_unit, calculate_market_take_home
        direction = cd.get('calc_direction') or 'take_home'
        if direction == 'price' and cd.get('price_per_unit'):
            breakdown = calculate_market_take_home(cd['price_per_unit'], units_available, vat_rate)
            if not breakdown:
                self.add_error('price_per_unit', 'Could not calculate — check the price and try again.')
                return cd
            self.fee_rate_at_listing = breakdown['fee_rate']
            cd['take_home_per_unit'] = breakdown['take_home_per_unit']
        elif cd.get('take_home_per_unit'):
            # This field holds the seller's desired TOTAL take-home (see the
            # widget comment in Meta) — the breakdown divides by units to get
            # the true per-unit amount the model field stores.
            breakdown = calculate_market_price_per_unit(cd['take_home_per_unit'], units_available, vat_rate)
            if not breakdown:
                self.add_error('take_home_per_unit', 'Could not calculate — check the amount and try again.')
                return cd
            cd['price_per_unit'] = breakdown['price_per_unit']
            self.fee_rate_at_listing = breakdown['fee_rate']
            cd['take_home_per_unit'] = breakdown['take_home_per_unit']
        else:
            self.add_error('take_home_per_unit', 'Enter your desired total take-home amount, or a price per unit.')
            return cd

        fulfillment = cd.get('fulfillment_method')
        if fulfillment in (MarketListing.FULFILLMENT_DELIVERY, MarketListing.FULFILLMENT_BOTH) and not cd.get('delivery_towns'):
            self.add_error('delivery_towns', 'Select at least one delivery town.')
        if fulfillment in (MarketListing.FULFILLMENT_PICKUP, MarketListing.FULFILLMENT_BOTH) and not cd.get('pickup_town'):
            self.add_error('pickup_town', 'Select a pickup town.')

        from django.utils import timezone
        today = timezone.localdate().isoformat()
        dates_raw = cd.get('available_dates_input', '')
        parsed_dates = []
        for d in [part.strip() for part in dates_raw.split(',') if part.strip()]:
            try:
                parsed = datetime.strptime(d, '%Y-%m-%d').date().isoformat()
            except ValueError:
                self.add_error('available_dates_input', f'"{d}" is not a valid date (use YYYY-MM-DD).')
                continue
            if parsed < today:
                # The date-picker's min attribute is a UI hint only — a
                # direct POST could still submit a past date, so this is
                # the real, authoritative check.
                self.add_error('available_dates_input', f'"{parsed}" is in the past.')
                continue
            parsed_dates.append(parsed)
        if parsed_dates:
            cd['available_dates'] = sorted(set(parsed_dates))
        elif 'available_dates_input' not in self.errors:
            self.add_error('available_dates_input', 'Add at least one available date.')
        return cd

    def save(self, commit=True):
        listing = super().save(commit=False)
        cd = self.cleaned_data
        listing.vat_rate = cd.get('vat_rate') if cd.get('vat_applicable') else None
        listing.take_home_per_unit = cd['take_home_per_unit']
        listing.price_per_unit = cd['price_per_unit']
        listing.fee_rate_at_listing = self.fee_rate_at_listing
        listing.delivery_towns = list(cd.get('delivery_towns') or [])
        listing.available_dates = cd.get('available_dates', [])
        if commit:
            listing.save()
        return listing


class MarketOrderForm(forms.Form):
    quantity           = forms.IntegerField(min_value=1, widget=_input('Quantity', type_='number'))
    fulfillment_method  = forms.ChoiceField(choices=MarketListing.FULFILLMENT_CHOICES, widget=_select())
    delivery_town        = forms.ChoiceField(choices=[('', '—')] + list(TOWN_CHOICES), required=False, widget=_select())
    requested_date         = forms.ChoiceField(choices=[], widget=_select())

    def __init__(self, *args, listing=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.listing = listing
        if listing:
            from django.utils import timezone
            today = timezone.localdate().isoformat()
            future_dates = [d for d in listing.available_dates if d >= today]
            self.fields['requested_date'].choices = [(d, d) for d in future_dates]
            if listing.delivery_towns:
                town_labels = dict(TOWN_CHOICES)
                self.fields['delivery_town'].choices = [('', '—')] + [
                    (t, town_labels.get(t, t)) for t in listing.delivery_towns
                ]
            if listing.fulfillment_method == MarketListing.FULFILLMENT_BOTH:
                # A single order is pickup OR delivery, never "both" — that
                # choice only makes sense at the listing level, offering
                # buyers a choice of the two, not as a selectable value itself.
                self.fields['fulfillment_method'].choices = [
                    c for c in MarketListing.FULFILLMENT_CHOICES if c[0] != MarketListing.FULFILLMENT_BOTH
                ]
            else:
                label = dict(MarketListing.FULFILLMENT_CHOICES)[listing.fulfillment_method]
                self.fields['fulfillment_method'].choices = [(listing.fulfillment_method, label)]
            # Native browser validation + a data attribute the client-side JS
            # reads for instant feedback, on top of the authoritative server check.
            remaining = listing.units_remaining()
            self.fields['quantity'].widget.attrs['max'] = remaining
            self.fields['quantity'].widget.attrs['data-max'] = remaining

    def clean(self):
        cd = super().clean()
        qty = cd.get('quantity')
        if self.listing and qty and qty > self.listing.units_remaining():
            self.add_error('quantity', f'Only {self.listing.units_remaining()} left.')
        fulfillment = cd.get('fulfillment_method')
        if fulfillment == MarketListing.FULFILLMENT_DELIVERY:
            town = cd.get('delivery_town')
            if not town:
                self.add_error('delivery_town', 'Select a delivery town.')
            elif self.listing and town not in (self.listing.delivery_towns or []):
                self.add_error('delivery_town', "This town isn't in the seller's delivery area.")
        return cd


# ── Supplier forms ────────────────────────────────────────────────────────────

class SupplierRegistrationForm(forms.Form):
    first_name       = forms.CharField(max_length=50,  widget=_input('e.g. Josaia'))
    last_name        = forms.CharField(max_length=50,  widget=_input('e.g. Vuli'))
    email            = forms.EmailField(widget=_input('you@example.fj', type_='email'))
    mobile           = forms.CharField(max_length=20,  widget=_input('+679 XXX XXXX'))
    town             = forms.ChoiceField(choices=[('', 'Select town…')] + list(TOWN_CHOICES), widget=_select())
    password         = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': 'At least 8 characters'}))
    password_confirm = forms.CharField(label='Confirm password', widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': 'Repeat password'}))
    business_name    = forms.CharField(max_length=100, required=False, label='Business Name (optional)', widget=_input('e.g. Pacific Supplies Ltd'))
    tin              = forms.CharField(max_length=50, required=False, label='TIN Number (optional)', widget=_input('e.g. P033-12345'))
    bio              = forms.CharField(required=False, widget=forms.Textarea(attrs={'class': 'form-input', 'rows': 4, 'placeholder': 'Describe your business, the products you supply, and your service area…'}))
    supply_categories = forms.MultipleChoiceField(choices=[], widget=forms.CheckboxSelectMultiple)
    service_towns     = forms.MultipleChoiceField(choices=TOWN_CHOICES, widget=forms.CheckboxSelectMultiple)
    tin_letter                = forms.FileField(label='TIN Letter', help_text='Upload your FRCA TIN letter (PDF, JPG, or PNG, up to 10MB). Required.')
    business_registration     = forms.FileField(label='Business Registration', required=False, help_text='Optional.')
    import_export_licence     = forms.FileField(label='Import/Export Licence', required=False, help_text='Optional.')
    accepted_terms            = forms.BooleanField(
        required=True,
        error_messages={'required': 'You must accept the Terms & Conditions, Privacy Policy and Platform Rules to register.'}
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['supply_categories'].choices = SupplyCategory.get_choices()

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if User.objects.filter(email=email).exists():
            raise ValidationError('An account with this email already exists.')
        return email

    def clean_tin_letter(self):
        return _clean_verification_document(self.cleaned_data.get('tin_letter'))

    def clean_business_registration(self):
        return _clean_verification_document(self.cleaned_data.get('business_registration'))

    def clean_import_export_licence(self):
        return _clean_verification_document(self.cleaned_data.get('import_export_licence'))

    def clean(self):
        cd = super().clean()
        p1, p2 = cd.get('password'), cd.get('password_confirm')
        if p1 and p2 and p1 != p2:
            raise ValidationError('Passwords do not match.')
        if not cd.get('supply_categories'):
            raise ValidationError('Please select at least one supply category.')
        if not cd.get('service_towns'):
            raise ValidationError('Please select at least one service town.')
        return cd

    def save(self, request=None):
        cd = self.cleaned_data
        with transaction.atomic():
            user = User.objects.create_user(
                email=cd['email'],
                password=cd['password'],
                first_name=cd['first_name'],
                last_name=cd['last_name'],
                mobile=cd['mobile'],
                town=cd['town'],
                role=User.ROLE_SUPPLIER,
            )
            profile = SupplierProfile(
                user=user,
                business_name=cd.get('business_name', ''),
                tin=cd.get('tin', ''),
                bio=cd.get('bio', ''),
                supply_categories=list(cd.get('supply_categories', [])),
                service_towns=list(cd.get('service_towns', [])),
            )
            for field in ('tin_letter', 'business_registration', 'import_export_licence'):
                if cd.get(field):
                    setattr(profile, field, cd[field])
            profile.save()
            workspaces.ensure_user_capability(user, can_offer_services=True)
            workspaces.create_supplier_workspace(user)
            workspaces.create_client_workspace(user)
        return user


class SupplierEnquiryForm(forms.Form):
    title       = forms.CharField(max_length=200, widget=_input('e.g. 500kg rice, 20 boxes of cooking oil…'))
    description = forms.CharField(widget=forms.Textarea(attrs={'class': 'form-input', 'rows': 5, 'placeholder': 'Describe what you need in detail — quantities, specifications, delivery requirements…'}))
    town        = forms.ChoiceField(choices=[('', 'Select delivery/pickup town…')] + list(TOWN_CHOICES), widget=_select())


class SupplierQuoteResponseForm(forms.Form):
    STATUS_CHOICES = [
        ('accepted',              'Accept'),
        ('modification_requested', 'Request Modification'),
        ('rejected',              'Reject'),
    ]
    status            = forms.ChoiceField(choices=STATUS_CHOICES, widget=_select())
    modification_note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-input', 'rows': 3, 'placeholder': 'Describe what changes you need…'}),
    )

    def clean(self):
        cd = super().clean()
        if cd.get('status') == 'modification_requested' and not (cd.get('modification_note') or '').strip():
            raise ValidationError('Please describe what modifications you need.')
        return cd


# ── Account deletion ──────────────────────────────────────────────────────────

class DeleteAccountForm(forms.Form):
    """Requires re-entering the current password so a hijacked/left-open
    session can't be used to delete an account with one click."""
    password = forms.CharField(
        label='Confirm your password',
        widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': 'Enter your password to confirm'}),
    )
    confirm = forms.BooleanField(
        required=True,
        label='I understand this will permanently deactivate my account',
        error_messages={'required': 'Please confirm you understand this action.'},
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password(self):
        password = self.cleaned_data['password']
        if not self.user or not self.user.check_password(password):
            raise ValidationError('Incorrect password.')
        return password


# ── Multi-role accounts & account linking ───────────────────────────────────

class AddTradieRoleForm(forms.Form):
    """Adds a TradieProfile (and the individual-provider workspace) to a
    user who already has an account under some other role — never creates a
    separate account. Field set is TradieRegistrationForm's role-specific
    subset only: first_name/last_name/mobile/town/password_confirm/
    accepted_terms are all already on `user` and dropped. email/password
    are kept but repurposed as a lightweight re-authentication step (see
    clean_email/clean_password), per an explicit request that this form
    still look like the familiar sign-up page."""
    email    = forms.EmailField(widget=_input('you@example.fj', type_='email'))
    use_same_credentials = forms.BooleanField(
        required=False, initial=True, label="Use my current account's email",
    )
    password = forms.CharField(
        label='Re-enter your password to confirm',
        widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': 'Your current password'}),
    )
    business_name    = forms.CharField(max_length=100, required=False, label='Company / Business Name', widget=_input('e.g. Tora Plumbing Suva (leave blank if you work as an individual contractor)'))
    tin              = forms.CharField(max_length=50, required=False, label='TIN Number (optional)', widget=_input('e.g. P033-12345'))
    years_experience = forms.ChoiceField(choices=[('', 'Select…')] + list(EXPERIENCE_CHOICES), widget=_select())
    bio              = forms.CharField(widget=forms.Textarea(attrs={'class': 'form-input', 'rows': 4, 'placeholder': 'Tell clients about your experience, specialties and work area…'}))
    trades           = forms.MultipleChoiceField(choices=[], widget=forms.CheckboxSelectMultiple)
    service_towns    = forms.MultipleChoiceField(choices=TOWN_CHOICES, widget=forms.CheckboxSelectMultiple)
    tin_letter                     = forms.FileField(label='TIN Letter', required=False, help_text='Upload your FRCA TIN letter (PDF, JPG, or PNG, up to 10MB).')
    business_licence               = forms.FileField(label='Business Licence', required=False, help_text='Optional.')
    public_liability_insurance     = forms.FileField(label='Public Liability Insurance', required=False, help_text='Optional.')
    electrical_contractors_licence = forms.FileField(label='Electrical Contractors Licence', required=False, help_text="Electrical work is safety-critical — upload this now if you have it. If not, you can still register, but your account won't be able to bid on jobs until our team has reviewed it.")
    plumber_licence                = forms.FileField(label='Plumber Licence', required=False, help_text="Plumbing work is safety-critical — upload this now if you have it. If not, you can still register, but your account won't be able to bid on jobs until our team has reviewed it.")
    accepted_platform_circumvention = forms.BooleanField(
        required=True,
        error_messages={'required': 'You must acknowledge the Platform Circumvention Fee policy.'}
    )
    accepted_invoicing_terms = forms.BooleanField(
        required=True,
        error_messages={'required': 'You must acknowledge the invoicing and payment obligations.'}
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields['trades'].choices = TradeCategory.get_choices()
        if user:
            self.fields['email'].initial = user.email
            self.reused_tin_letter = bool(hasattr(user, 'supplier_profile') and user.supplier_profile.tin_letter)
            if hasattr(user, 'supplier_profile'):
                sp = user.supplier_profile
                self.fields['business_name'].initial = sp.business_name
                self.fields['tin'].initial = sp.tin
                self.fields['service_towns'].initial = sp.service_towns
        else:
            self.reused_tin_letter = False
        if self.reused_tin_letter:
            self.fields['tin_letter'].help_text = "We'll reuse the TIN letter already on file for your supplier account, or upload a new one."

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if not self.user or email != self.user.email.lower():
            raise ValidationError("This must match your current account's email — adding a role only ever applies to your own account.")
        return email

    def clean_password(self):
        password = self.cleaned_data['password']
        if not self.user or not self.user.check_password(password):
            raise ValidationError('Incorrect password.')
        return password

    def clean_tin_letter(self):
        return _clean_verification_document(self.cleaned_data.get('tin_letter'))

    def clean_business_licence(self):
        return _clean_verification_document(self.cleaned_data.get('business_licence'))

    def clean_public_liability_insurance(self):
        return _clean_verification_document(self.cleaned_data.get('public_liability_insurance'))

    def clean_electrical_contractors_licence(self):
        return _clean_verification_document(self.cleaned_data.get('electrical_contractors_licence'))

    def clean_plumber_licence(self):
        return _clean_verification_document(self.cleaned_data.get('plumber_licence'))

    def clean(self):
        cd = super().clean()
        if not cd.get('tin_letter') and not self.reused_tin_letter:
            self.add_error('tin_letter', 'Upload your TIN letter, or add a supplier account first so we can reuse it.')
        return cd

    def save(self):
        cd = self.cleaned_data
        user = self.user
        with transaction.atomic():
            # Same founding-member mutex as TradieRegistrationForm.save() —
            # someone adding this role later should still be eligible while
            # slots remain.
            PlatformSettings.objects.select_for_update().filter(pk=PlatformSettings.get_active().pk).exists()
            is_founder = TradieProfile.objects.count() < FOUNDING_MEMBER_SLOTS
            profile = TradieProfile(
                user=user,
                business_name=cd.get('business_name', ''),
                tin=cd.get('tin', ''),
                years_experience=cd.get('years_experience', ''),
                bio=cd.get('bio', ''),
                trades=list(cd.get('trades', [])),
                service_towns=list(cd.get('service_towns', [])),
                is_founding_member=is_founder,
                founding_member_credit_balance=Decimal(FOUNDING_MEMBER_CREDIT) if is_founder else Decimal('0.00'),
            )
            for field in ('business_licence', 'public_liability_insurance',
                          'electrical_contractors_licence', 'plumber_licence'):
                if cd.get(field):
                    setattr(profile, field, cd[field])
            if cd.get('tin_letter'):
                profile.tin_letter = cd['tin_letter']
            elif self.reused_tin_letter:
                src = user.supplier_profile.tin_letter
                profile.tin_letter.save(src.name, ContentFile(src.read()), save=False)
            profile.save()
            workspaces.ensure_user_capability(user, can_offer_services=True)
            workspaces.create_client_workspace(user)
            workspaces.create_individual_provider_workspace(user)
        return user


class AddSupplierRoleForm(forms.Form):
    """Mirrors AddTradieRoleForm — see its docstring. Adds a SupplierProfile
    (and the supplier workspace) to a user who already has an account under
    some other role."""
    email    = forms.EmailField(widget=_input('you@example.fj', type_='email'))
    use_same_credentials = forms.BooleanField(
        required=False, initial=True, label="Use my current account's email",
    )
    password = forms.CharField(
        label='Re-enter your password to confirm',
        widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': 'Your current password'}),
    )
    business_name     = forms.CharField(max_length=100, required=False, label='Business Name (optional)', widget=_input('e.g. Pacific Supplies Ltd'))
    tin               = forms.CharField(max_length=50, required=False, label='TIN Number (optional)', widget=_input('e.g. P033-12345'))
    bio               = forms.CharField(required=False, widget=forms.Textarea(attrs={'class': 'form-input', 'rows': 4, 'placeholder': 'Describe your business, the products you supply, and your service area…'}))
    supply_categories = forms.MultipleChoiceField(choices=[], widget=forms.CheckboxSelectMultiple)
    service_towns     = forms.MultipleChoiceField(choices=TOWN_CHOICES, widget=forms.CheckboxSelectMultiple)
    tin_letter                = forms.FileField(label='TIN Letter', required=False, help_text='Upload your FRCA TIN letter (PDF, JPG, or PNG, up to 10MB).')
    business_registration     = forms.FileField(label='Business Registration', required=False, help_text='Optional.')
    import_export_licence     = forms.FileField(label='Import/Export Licence', required=False, help_text='Optional.')

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields['supply_categories'].choices = SupplyCategory.get_choices()
        if user:
            self.fields['email'].initial = user.email
            self.reused_tin_letter = bool(hasattr(user, 'tradie_profile') and user.tradie_profile.tin_letter)
            if hasattr(user, 'tradie_profile'):
                tp = user.tradie_profile
                self.fields['business_name'].initial = tp.business_name
                self.fields['tin'].initial = tp.tin
                self.fields['service_towns'].initial = tp.service_towns
        else:
            self.reused_tin_letter = False
        if self.reused_tin_letter:
            self.fields['tin_letter'].help_text = "We'll reuse the TIN letter already on file for your local pro account, or upload a new one."

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if not self.user or email != self.user.email.lower():
            raise ValidationError("This must match your current account's email — adding a role only ever applies to your own account.")
        return email

    def clean_password(self):
        password = self.cleaned_data['password']
        if not self.user or not self.user.check_password(password):
            raise ValidationError('Incorrect password.')
        return password

    def clean_tin_letter(self):
        return _clean_verification_document(self.cleaned_data.get('tin_letter'))

    def clean_business_registration(self):
        return _clean_verification_document(self.cleaned_data.get('business_registration'))

    def clean_import_export_licence(self):
        return _clean_verification_document(self.cleaned_data.get('import_export_licence'))

    def clean(self):
        cd = super().clean()
        if not cd.get('supply_categories'):
            raise ValidationError('Please select at least one supply category.')
        if not cd.get('service_towns'):
            raise ValidationError('Please select at least one service town.')
        if not cd.get('tin_letter') and not self.reused_tin_letter:
            self.add_error('tin_letter', 'Upload your TIN letter, or add a local pro account first so we can reuse it.')
        return cd

    def save(self):
        cd = self.cleaned_data
        user = self.user
        with transaction.atomic():
            profile = SupplierProfile(
                user=user,
                business_name=cd.get('business_name', ''),
                tin=cd.get('tin', ''),
                bio=cd.get('bio', ''),
                supply_categories=list(cd.get('supply_categories', [])),
                service_towns=list(cd.get('service_towns', [])),
            )
            for field in ('business_registration', 'import_export_licence'):
                if cd.get(field):
                    setattr(profile, field, cd[field])
            if cd.get('tin_letter'):
                profile.tin_letter = cd['tin_letter']
            elif self.reused_tin_letter:
                src = user.tradie_profile.tin_letter
                profile.tin_letter.save(src.name, ContentFile(src.read()), save=False)
            profile.save()
            workspaces.ensure_user_capability(user, can_offer_services=True)
            workspaces.create_client_workspace(user)
            workspaces.create_supplier_workspace(user)
        return user


class MergeAccountForm(forms.Form):
    """Links a second, already-existing account to the requesting user's
    own — see workspaces.link_accounts()/get_linked_users(). Verifies the
    target account's password directly via check_password(), not
    authenticate() — this never logs the requester in as the target account,
    that only ever happens later, on-demand, via views.switch_tab, once the
    two are already linked."""
    email    = forms.EmailField(label="The other account's email", widget=_input('you@example.fj', type_='email'))
    password = forms.CharField(label="That account's password", widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': 'Password'}))

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cd = super().clean()
        email, password = cd.get('email'), cd.get('password')
        if not email or not password:
            return cd
        target = User.objects.filter(email__iexact=email).first()
        # Same message either way — no enumeration of which of email/
        # password was wrong, or whether the account even exists.
        if not target or not target.is_active or not target.check_password(password):
            raise ValidationError('Incorrect email or password.')
        if target.pk == self.user.pk:
            raise ValidationError("That's your current account.")
        if target.account_deleted_at:
            raise ValidationError('That account is no longer active.')
        self.target_user = target
        return cd

    def save(self):
        return workspaces.link_accounts(self.user, self.target_user)


class ClearLinkedLoginForm(forms.Form):
    """Re-checks the REQUESTING user's own current password before
    disabling any login in their linked group — see views.clear_linked_login.
    Deliberately not the target's password: control of the group was
    already proven once, at merge time."""
    password = forms.CharField(
        label='Your current password',
        widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': 'Your current password'}),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password(self):
        password = self.cleaned_data['password']
        if not self.user or not self.user.check_password(password):
            raise ValidationError('Incorrect password.')
        return password


# ── Content reporting ─────────────────────────────────────────────────────────

class ContentReportForm(forms.Form):
    reason  = forms.ChoiceField(choices=ContentReport.REASON_CHOICES, widget=_select())
    details = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-input', 'rows': 4, 'placeholder': 'Add any details that will help us review this…'}),
    )
