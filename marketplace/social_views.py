"""
Facebook Login views — kept separate from views.py (already 1500+ lines)
since this is a distinct concern: an OAuth-style flow and a webhook-style
receiver, rather than core marketplace actions.

No templates are touched by this module. The data-deletion status page
returns JSON rather than rendered HTML for now; a real UI can replace that
response later without changing the URL contract.
"""
import secrets

from django.conf import settings
from django.contrib import messages as flash
from django.contrib.auth import login
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import meta_client, meta_security, workspaces
from .forms import _validate_closed_beta_email
from .models import SocialDataDeletionRequest, SocialIdentity, User
from .views import _rate_limited

FACEBOOK_OAUTH_STATE_SESSION_KEY = 'facebook_oauth_state'


# ── Facebook Login ────────────────────────────────────────────────────────────

def facebook_connect(request):
    if not settings.FACEBOOK_LOGIN_ENABLED:
        raise Http404
    state = secrets.token_urlsafe(32)
    request.session[FACEBOOK_OAUTH_STATE_SESSION_KEY] = state
    return redirect(meta_client.get_oauth_dialog_url(state))


def facebook_callback(request):
    if not settings.FACEBOOK_LOGIN_ENABLED:
        raise Http404
    if _rate_limited(request, 'facebook_callback', max_attempts=20, window_seconds=300):
        flash.error(request, 'Too many Facebook login attempts. Please wait a few minutes and try again.')
        return redirect('login')

    if request.GET.get('error'):
        flash.error(request, 'Facebook login was cancelled or failed.')
        return redirect('login')

    expected_state = request.session.pop(FACEBOOK_OAUTH_STATE_SESSION_KEY, None)
    state = request.GET.get('state')
    if not expected_state or not state or not secrets.compare_digest(expected_state, state):
        flash.error(request, 'Facebook login could not be verified. Please try again.')
        return redirect('login')

    code = request.GET.get('code')
    if not code:
        flash.error(request, 'Facebook login did not return an authorization code.')
        return redirect('login')

    try:
        token_data = meta_client.exchange_code_for_token(code)
        profile = meta_client.get_user_profile(token_data['access_token'])
    except meta_client.GraphAPIError:
        flash.error(request, "We couldn't complete Facebook login. Please try again or use your password.")
        return redirect('login')

    provider_user_id = profile.get('id')
    if not provider_user_id:
        flash.error(request, "We couldn't complete Facebook login. Please try again or use your password.")
        return redirect('login')
    email = (profile.get('email') or '').lower()
    display_name = profile.get('name', '')

    identity = (
        SocialIdentity.objects
        .filter(provider=SocialIdentity.PROVIDER_FACEBOOK, provider_user_id=provider_user_id)
        .select_related('user')
        .first()
    )
    if identity:
        identity.last_login_at = timezone.now()
        identity.save(update_fields=['last_login_at'])
        login(request, identity.user)
        flash.success(request, 'Logged in with Facebook.')
        return redirect('dashboard')

    if request.user.is_authenticated:
        # Linking flow: attach this Facebook identity to the logged-in account.
        SocialIdentity.objects.create(
            user=request.user, provider=SocialIdentity.PROVIDER_FACEBOOK,
            provider_user_id=provider_user_id, email_at_link_time=email,
            display_name_at_link_time=display_name, last_login_at=timezone.now(),
        )
        flash.success(request, 'Facebook account linked.')
        return redirect('dashboard')

    if email and User.objects.filter(email=email).exists():
        # Account-linking challenge — never silently merge on an email match
        # alone. The existing account holder must authenticate with their
        # password; they can link Facebook afterward.
        flash.info(
            request,
            'An account with this email already exists. Please log in with your password, '
            'then link Facebook from your account settings.',
        )
        return redirect('login')

    # No existing identity, no authenticated user, no email collision — a
    # genuinely new signup. Still subject to the same closed-beta gate as
    # the normal client registration form.
    if settings.BETA_GATE_CLIENT_SIGNUPS:
        try:
            _validate_closed_beta_email(email, True)
        except ValidationError:
            flash.error(request, 'Signups are currently invite-only for closed beta. Please request access from the team.')
            return redirect('login')

    # NOTE: Facebook Login is explicitly not identity verification, and
    # there's no onboarding wizard for this path, so there is no
    # terms-acceptance checkbox to record here — unlike
    # ClientRegistrationForm, this path does not create a TermsAcceptance
    # row. Revisit once the onboarding wizard collects real consent.
    with transaction.atomic():
        first_name, _, last_name = display_name.partition(' ')
        login_email = email or f'facebook-{provider_user_id}@users.coconutwireless.fj'
        user = User.objects.create_user(
            email=login_email,
            password=None,  # make_password(None) -> unusable password; Facebook is the only login method
            first_name=first_name or display_name,
            last_name=last_name,
            role=User.ROLE_CLIENT,
        )
        workspaces.ensure_user_capability(user)
        workspaces.create_client_workspace(user)
        SocialIdentity.objects.create(
            user=user, provider=SocialIdentity.PROVIDER_FACEBOOK,
            provider_user_id=provider_user_id, email_at_link_time=email,
            display_name_at_link_time=display_name, last_login_at=timezone.now(),
        )
    login(request, user)
    flash.success(request, 'Welcome! Your account has been created with Facebook.')
    return redirect('dashboard')


# ── Data deletion ──────────────────────────────────────────────────────────────
# Required by Meta's platform policy for any app using Facebook Login,
# independent of whether Page linking/Messenger are ever added.

@csrf_exempt
@require_POST
def meta_data_deletion(request):
    if _rate_limited(request, 'meta_data_deletion', max_attempts=30, window_seconds=3600):
        return JsonResponse({'error': 'rate limited'}, status=429)

    try:
        payload = meta_security.parse_and_verify_signed_request(request.POST.get('signed_request', ''))
    except meta_security.SignedRequestError:
        return JsonResponse({'error': 'invalid signed_request'}, status=403)

    provider_user_id = str(payload.get('user_id', ''))
    if not provider_user_id:
        return JsonResponse({'error': 'missing user_id'}, status=400)

    confirmation_code = secrets.token_urlsafe(16)
    identity = (
        SocialIdentity.objects
        .filter(provider=SocialIdentity.PROVIDER_FACEBOOK, provider_user_id=provider_user_id)
        .select_related('user')
        .first()
    )

    deletion_request = SocialDataDeletionRequest.objects.create(
        user=identity.user if identity else None,
        provider_user_id_hash=meta_security.hash_provider_user_id(provider_user_id),
        confirmation_code=confirmation_code,
    )

    if identity:
        # Financial/legal records (Task, Quote, Invoice, TermsAcceptance, ...)
        # are untouched — only the Facebook identity link itself is
        # Facebook-derived data.
        identity.delete()
        deletion_request.status = SocialDataDeletionRequest.STATUS_COMPLETED
    else:
        deletion_request.status = SocialDataDeletionRequest.STATUS_NOT_FOUND
    deletion_request.completed_at = timezone.now()
    deletion_request.save(update_fields=['status', 'completed_at'])

    status_url = request.build_absolute_uri(reverse('meta_data_deletion_status', args=[confirmation_code]))
    return JsonResponse({'url': status_url, 'confirmation_code': confirmation_code})


def meta_data_deletion_status(request, confirmation_code):
    deletion_request = get_object_or_404(SocialDataDeletionRequest, confirmation_code=confirmation_code)
    return JsonResponse({
        'confirmation_code': deletion_request.confirmation_code,
        'status': deletion_request.status,
        'requested_at': deletion_request.requested_at.isoformat(),
        'completed_at': deletion_request.completed_at.isoformat() if deletion_request.completed_at else None,
    })
