from django.db import migrations
from django.utils.text import slugify


def backfill_workspaces(apps, schema_editor):
    """
    Give every existing User a UserCapability + client Workspace, and
    additionally an individual_provider Workspace for role='tradie' users.
    Staff/superuser accounts (role='') are included too, per the confirmed
    plan decision — every human account gets a client workspace regardless
    of marketplace role.
    """
    User = apps.get_model('marketplace', 'User')
    UserCapability = apps.get_model('marketplace', 'UserCapability')
    Workspace = apps.get_model('marketplace', 'Workspace')
    WorkspaceMembership = apps.get_model('marketplace', 'WorkspaceMembership')

    existing_slugs = set(Workspace.objects.values_list('slug', flat=True))

    def unique_slug(display_name):
        base = slugify(display_name) or 'workspace'
        candidate = base
        suffix = 2
        while candidate in existing_slugs:
            candidate = f'{base}-{suffix}'
            suffix += 1
        existing_slugs.add(candidate)
        return candidate

    def make_workspace(user, workspace_type, display_name):
        workspace = Workspace.objects.create(
            owner_id=user.id,
            workspace_type=workspace_type,
            display_name=display_name,
            slug=unique_slug(display_name),
        )
        WorkspaceMembership.objects.create(workspace=workspace, user_id=user.id, role='owner')
        return workspace

    for user in User.objects.all().iterator():
        display_name = f'{user.first_name} {user.last_name}'.strip() or user.email
        is_tradie = user.role == 'tradie'
        UserCapability.objects.create(
            user_id=user.id,
            can_hire=True,
            can_offer_services=is_tradie,
        )
        make_workspace(user, 'client', display_name)
        if is_tradie:
            make_workspace(user, 'individual_provider', display_name)


def noop_reverse(apps, schema_editor):
    """
    Not meaningfully reversible on its own (would need to figure out which
    rows this migration created vs. anything added since) — reversing 0034
    (which drops these tables entirely) is the real rollback path.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0046_workspace_models'),
    ]

    operations = [
        migrations.RunPython(backfill_workspaces, noop_reverse),
    ]
