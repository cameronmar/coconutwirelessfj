from urllib.parse import urlencode

from django.conf import settings
from django.shortcuts import redirect
from django.urls import Resolver404, resolve, reverse


# Pages a logged-out visitor may open. Everything else sends them to sign up.
PUBLIC_URL_NAMES = frozenset({
    # Home, "about us" and "contact us"
    'home', 'how_it_works', 'contact_support',
    # Legal pages: linked from the sign-up form, and app stores require a
    # publicly reachable privacy policy.
    'privacy', 'terms', 'child_safety',
    # Getting an account / getting back in
    'register_client', 'register_tradie', 'register_supplier',
    'login', 'logout', 'google_auth', 'google_signup_complete',
    'password_reset_request', 'password_reset_confirm',
    # Infrastructure: platform health check, and the Android app's device
    # registration, which already handles auth itself.
    'healthz', 'register_fcm_token',
})


class SignupRequiredMiddleware:
    """
    Send logged-out visitors to the sign-up page for anything other than the
    public pages above, carrying ?next= so they land back where they were
    after logging in. Admin has its own login; static/media files and
    unknown URLs (which 404 normally) pass straight through.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.passthrough_prefixes = tuple(
            p for p in ('/admin/', settings.STATIC_URL, settings.MEDIA_URL, '/favicon.ico') if p
        )

    def __call__(self, request):
        if not request.user.is_authenticated and not self._is_public(request):
            signup = reverse('register_client')
            return redirect(f'{signup}?{urlencode({"next": request.get_full_path()})}')
        return self.get_response(request)

    def _is_public(self, request):
        path = request.path_info
        if path.startswith(self.passthrough_prefixes):
            return True
        try:
            match = resolve(path)
        except Resolver404:
            return True
        return match.url_name in PUBLIC_URL_NAMES
