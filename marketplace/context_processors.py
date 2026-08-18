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
