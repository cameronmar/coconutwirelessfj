"""
Thin wrapper around the Meta Graph API — every real HTTP call the Facebook
Login flow makes goes through a named function here, so tests mock this
module directly (e.g. @mock.patch('marketplace.meta_client.exchange_code_for_token'))
rather than needing an HTTP-mocking library. No caller should use `requests`
directly against graph.facebook.com — go through here instead.
"""
from urllib.parse import urlencode

import requests
from django.conf import settings

GRAPH_API_TIMEOUT = 10  # seconds


class GraphAPIError(Exception):
    """Raised for any Graph API error response or transport failure. Carries
    the raw error payload (when available) for logging — never surfaced
    directly to end users."""

    def __init__(self, message, payload=None):
        super().__init__(message)
        self.payload = payload


def _graph_url(path):
    version = settings.META_GRAPH_API_VERSION or 'v21.0'
    return f'https://graph.facebook.com/{version}/{path.lstrip("/")}'


def _request(method, path, **kwargs):
    try:
        response = requests.request(method, _graph_url(path), timeout=GRAPH_API_TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise GraphAPIError(f'Network error calling Graph API: {exc}') from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise GraphAPIError(f'Graph API returned a non-JSON response (status {response.status_code}).') from exc
    if 'error' in data:
        raise GraphAPIError(data['error'].get('message', 'Graph API error'), payload=data['error'])
    if not response.ok:
        raise GraphAPIError(f'Graph API request failed with status {response.status_code}.', payload=data)
    return data


def get_oauth_dialog_url(state, redirect_uri=None, scope='email,public_profile'):
    """The URL to redirect the user's browser to for the Facebook Login dialog."""
    version = settings.META_GRAPH_API_VERSION or 'v21.0'
    redirect_uri = redirect_uri or settings.META_OAUTH_REDIRECT_URI
    params = urlencode({
        'client_id': settings.META_APP_ID,
        'redirect_uri': redirect_uri,
        'state': state,
        'scope': scope,
        'response_type': 'code',
    })
    return f'https://www.facebook.com/{version}/dialog/oauth?{params}'


def exchange_code_for_token(code, redirect_uri=None):
    """OAuth authorization code -> {'access_token': ..., 'token_type': ..., 'expires_in': ...}."""
    return _request('GET', 'oauth/access_token', params={
        'client_id': settings.META_APP_ID,
        'client_secret': settings.META_APP_SECRET,
        'redirect_uri': redirect_uri or settings.META_OAUTH_REDIRECT_URI,
        'code': code,
    })


def get_user_profile(access_token):
    """-> {'id': ..., 'name': ..., 'email': ...} (email present only if
    granted and the user has one on file with Meta)."""
    return _request('GET', 'me', params={'fields': 'id,name,email', 'access_token': access_token})
