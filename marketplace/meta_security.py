"""
Security primitives for the Facebook Login integration: verifying Meta's
data-deletion signed_request. No network calls here — see meta_client.py
for those. Independently unit-testable with synthetic data — no real Meta
credentials needed to exercise any of it.
"""
import base64
import hashlib
import hmac
import json

from django.conf import settings


class SignedRequestError(Exception):
    """Raised for any malformed or invalid Meta signed_request — callers
    must treat this as 'reject the request', never fall back to processing
    it unverified."""


def _base64_url_decode(value):
    padding = '=' * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def parse_and_verify_signed_request(signed_request, app_secret=None):
    """
    Verifies and decodes a Meta signed_request (used by the data-deletion
    callback): '<base64url(hmac_sha256_signature)>.<base64url(json_payload)>',
    signed over the *encoded* payload segment using the app secret. Raises
    SignedRequestError on anything malformed or invalid — never returns a
    partially-trusted result.
    """
    app_secret = app_secret or settings.META_APP_SECRET
    if not app_secret:
        raise SignedRequestError('META_APP_SECRET is not configured.')
    try:
        encoded_sig, encoded_payload = signed_request.split('.', 1)
    except (ValueError, AttributeError) as exc:
        raise SignedRequestError('Malformed signed_request.') from exc
    try:
        signature = _base64_url_decode(encoded_sig)
        payload = json.loads(_base64_url_decode(encoded_payload))
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise SignedRequestError('Malformed signed_request.') from exc
    if not isinstance(payload, dict) or str(payload.get('algorithm', '')).upper() != 'HMAC-SHA256':
        raise SignedRequestError('Unsupported or missing signed_request algorithm.')
    expected_signature = hmac.new(app_secret.encode(), encoded_payload.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected_signature):
        raise SignedRequestError('Invalid signed_request signature.')
    return payload


def hash_provider_user_id(provider_user_id):
    """One-way hash for SocialDataDeletionRequest.provider_user_id_hash —
    we record that a deletion was requested for this Meta user id without
    retaining the id itself in that audit row."""
    return hashlib.sha256(provider_user_id.encode()).hexdigest()
