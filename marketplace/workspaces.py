"""
Workspace service layer: slug generation, workspace creation, and the
session-based active-workspace contract (marketplace command §7).

Mirrors the pattern already used by utils.py's notification helpers — a set
of purpose-specific functions rather than a single generic dispatcher/class.

MULTI_WORKSPACE_ENABLED gates *creation* of additional business workspaces
through the UI (not built yet — see the plan roadmap) — it has no effect on
create_client_workspace/create_individual_provider_workspace below, which
exist to provision the one client workspace and (for tradies) the one
individual-provider workspace every user is entitled to regardless of the
flag.
"""
import logging

from django.db import IntegrityError, transaction
from django.utils.text import slugify

from .models import UserCapability, Workspace, WorkspaceMembership

logger = logging.getLogger(__name__)


def ensure_user_capability(user, can_offer_services=False):
    """
    Idempotent: get_or_create so this is safe to call from every
    registration path without worrying about ordering vs. an earlier call.
    Only flips can_offer_services on (never back off) — a client becoming a
    tradie later shouldn't need this function reasoned about at every call
    site; see the future migrate-to-tradie flow.
    """
    capability, _created = UserCapability.objects.get_or_create(
        user=user, defaults={'can_hire': True, 'can_offer_services': can_offer_services},
    )
    if can_offer_services and not capability.can_offer_services:
        capability.can_offer_services = True
        capability.save(update_fields=['can_offer_services'])
    return capability


def generate_unique_workspace_slug(display_name):
    """
    Preview-only: returns the slug Workspace.save() would currently assign
    for this display_name, without reserving it (no row is created here, so
    it can go stale if something else claims it before an actual save() —
    fine for a live "your workspace URL will be..." UI preview, not fine as
    the only thing standing between two writers and a collision). The
    authoritative, collision-safe generation happens in Workspace.save()
    itself, which every creation path (including this module's
    _create_workspace, the admin, and any future code) goes through.
    """
    base = slugify(display_name) or 'workspace'
    candidate = base
    suffix = 2
    existing = set(
        Workspace.objects.filter(slug__startswith=base).values_list('slug', flat=True)
    )
    while candidate in existing:
        candidate = f'{base}-{suffix}'
        suffix += 1
    return candidate


def _create_workspace(owner, workspace_type, display_name):
    """
    Create a Workspace + owner WorkspaceMembership atomically. Workspace.save()
    generates the slug itself, so this only needs to handle a genuine
    concurrent-write IntegrityError: if it's two requests racing to create
    the same owner's client/individual_provider workspace, the loser should
    return the winner's row rather than blindly retrying — retrying would
    just fail the same owner+type uniqueness constraint again regardless of
    slug, and eventually surface a 500 instead of a clean result. A true
    slug collision (two different owners' display names colliding at the
    same instant) has no such existing row to find, so it falls through to
    a retry, and the model regenerates a fresh slug on each attempt.
    """
    for attempt in range(5):
        try:
            with transaction.atomic():
                workspace = Workspace.objects.create(
                    owner=owner, workspace_type=workspace_type, display_name=display_name,
                )
                WorkspaceMembership.objects.create(
                    workspace=workspace,
                    user=owner,
                    role=WorkspaceMembership.ROLE_OWNER,
                )
                return workspace
        except IntegrityError:
            existing = Workspace.objects.filter(owner=owner, workspace_type=workspace_type).first()
            if existing:
                return existing
            if attempt == 4:
                raise
    raise IntegrityError('Could not create workspace after retries.')  # pragma: no cover


def create_client_workspace(user):
    """Idempotent: returns the existing client workspace if one already exists."""
    existing = get_client_workspace(user)
    if existing:
        return existing
    display_name = user.full_name or user.email
    return _create_workspace(user, Workspace.TYPE_CLIENT, display_name)


def create_individual_provider_workspace(user):
    """Idempotent: returns the existing individual-provider workspace if one already exists."""
    existing = get_individual_provider_workspace(user)
    if existing:
        return existing
    display_name = user.full_name or user.email
    return _create_workspace(user, Workspace.TYPE_INDIVIDUAL_PROVIDER, display_name)


def get_client_workspace(user):
    """
    Returns the user's client Workspace, or None. Deliberately non-raising:
    every write path treats a missing workspace as "leave the new FK null,
    don't block the marketplace action" (spec §26) — a workspace-linking gap
    must never break task/quote/fee/invoice/review creation.
    """
    if not user or not user.is_authenticated:
        return None
    return user.owned_workspaces.filter(workspace_type=Workspace.TYPE_CLIENT).first()


def get_individual_provider_workspace(user):
    """See get_client_workspace — same non-raising contract."""
    if not user or not user.is_authenticated:
        return None
    return user.owned_workspaces.filter(workspace_type=Workspace.TYPE_INDIVIDUAL_PROVIDER).first()


def resolve_client_workspace_for_write(user, context):
    """
    Same lookup as get_client_workspace, for the money-handling write paths
    (post_task, rate_tradie, ...) rather than idempotency checks. A None
    result there means a real row is about to be created with a null
    workspace FK — worth a log line so it's discoverable (Sentry etc.)
    without an admin having to remember to run
    audit_workspace_relationships. Not used inside create_client_workspace's
    own idempotency check, where None is the expected, unremarkable case.
    """
    workspace = get_client_workspace(user)
    if workspace is None and user is not None:
        logger.warning('workspace-link-gap: no client workspace for user_id=%s (%s)', user.pk, context)
    return workspace


def resolve_individual_provider_workspace_for_write(user, context):
    """See resolve_client_workspace_for_write — same rationale, provider side."""
    workspace = get_individual_provider_workspace(user)
    if workspace is None and user is not None:
        logger.warning('workspace-link-gap: no individual_provider workspace for user_id=%s (%s)', user.pk, context)
    return workspace


def get_accessible_workspaces(user):
    """
    Workspaces a user may act through: everything they own, plus anything
    they hold an active membership on (relevant once business workspaces
    with non-owner staff/managers exist — today that's always a subset of
    owned workspaces, since only owners exist).
    """
    if not user or not user.is_authenticated:
        return Workspace.objects.none()
    owned_ids = user.owned_workspaces.values_list('id', flat=True)
    member_ids = WorkspaceMembership.objects.filter(user=user, active=True).values_list('workspace_id', flat=True)
    return Workspace.objects.filter(
        id__in=set(owned_ids) | set(member_ids), active=True
    ).order_by('workspace_type', 'display_name')


SESSION_KEY = 'active_workspace_id'


def get_active_workspace(request):
    """
    Resolve the session's active workspace, verifying access every time
    (never trust the stored id blindly). Falls back safely: to the client
    workspace if the stored one is gone/inaccessible/suspended, or to the
    single accessible workspace if there's only one.
    """
    user = request.user
    if not user.is_authenticated:
        return None

    accessible = get_accessible_workspaces(user)
    workspace_id = request.session.get(SESSION_KEY)

    if workspace_id:
        workspace = accessible.filter(id=workspace_id).first()
        if workspace:
            return workspace
        # Stored workspace is gone, suspended, or no longer accessible.
        request.session.pop(SESSION_KEY, None)

    if accessible.count() == 1:
        workspace = accessible.first()
        request.session[SESSION_KEY] = workspace.id
        return workspace

    client_workspace = accessible.filter(workspace_type=Workspace.TYPE_CLIENT).first()
    if client_workspace:
        request.session[SESSION_KEY] = client_workspace.id
    return client_workspace


def set_active_workspace(request, workspace_id):
    """
    Switch the session's active workspace. Returns the Workspace on success,
    or None if the user doesn't own/have active membership on that workspace
    id — callers must not trust a browser-submitted id without this check.
    """
    user = request.user
    if not user.is_authenticated:
        return None
    workspace = get_accessible_workspaces(user).filter(id=workspace_id).first()
    if not workspace:
        return None
    request.session[SESSION_KEY] = workspace.id
    return workspace
