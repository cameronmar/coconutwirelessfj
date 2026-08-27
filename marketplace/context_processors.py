from django.conf import settings


def beta_features(request):
    """
    Exposes whether the current viewer can see unlaunched features, for
    templates like base.html that render on every page (nav links) and
    can't rely on each view passing this through explicitly. Mirrors the
    bypass in views.beta_feature — same rule, template-side.
    """
    user = getattr(request, 'user', None)
    can_preview = bool(
        user and user.is_authenticated and user.can_preview_unlaunched_features()
    )
    return {
        'suppliers_visible': settings.SUPPLIERS_ENABLED or can_preview,
    }


def minor_session(request):
    """Exposes whether the current viewer is a Minor User (16-17), for
    templates that must never render sponsor banners/carousels to them
    (Terms §12 / Privacy §9) — same "every page" problem beta_features
    solves above."""
    user = getattr(request, 'user', None)
    return {
        'is_minor_session': bool(user and user.is_authenticated and user.is_minor),
    }
