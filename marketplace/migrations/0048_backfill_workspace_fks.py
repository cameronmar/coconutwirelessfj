import sys

from django.db import migrations


def backfill_workspace_fks(apps, schema_editor):
    """
    Populate the new workspace FKs on existing Task/Quote/PlatformFee/
    Invoice/PublicReview/PrivateReview rows by resolving each legacy user FK
    to that user's workspace (guaranteed to exist after 0035). Unambiguous
    today — no business workspaces exist yet, so every tradie has exactly
    one provider-type workspace. Any row that can't be resolved is logged,
    never guessed (spec §8.3/§9.5) — audit_workspace_relationships can find
    these again later if something's still off.
    """
    Task = apps.get_model('marketplace', 'Task')
    Quote = apps.get_model('marketplace', 'Quote')
    PlatformFee = apps.get_model('marketplace', 'PlatformFee')
    Invoice = apps.get_model('marketplace', 'Invoice')
    PublicReview = apps.get_model('marketplace', 'PublicReview')
    PrivateReview = apps.get_model('marketplace', 'PrivateReview')
    Workspace = apps.get_model('marketplace', 'Workspace')

    client_ws = dict(Workspace.objects.filter(workspace_type='client').values_list('owner_id', 'id'))
    provider_ws = dict(Workspace.objects.filter(workspace_type='individual_provider').values_list('owner_id', 'id'))

    unresolved = []

    tasks = []
    for task in Task.objects.all().iterator():
        changed = False
        cw = client_ws.get(task.client_id)
        if cw:
            task.client_workspace_id = cw
            changed = True
        elif task.client_id:
            unresolved.append(f'Task {task.id}: no client workspace for user {task.client_id}')
        if task.assigned_tradie_id:
            pw = provider_ws.get(task.assigned_tradie_id)
            if pw:
                task.assigned_provider_workspace_id = pw
                changed = True
            else:
                unresolved.append(f'Task {task.id}: no provider workspace for assigned_tradie {task.assigned_tradie_id}')
        if changed:
            tasks.append(task)
    if tasks:
        Task.objects.bulk_update(tasks, ['client_workspace_id', 'assigned_provider_workspace_id'], batch_size=500)

    quotes = []
    for quote in Quote.objects.all().iterator():
        pw = provider_ws.get(quote.tradie_id)
        if pw:
            quote.provider_workspace_id = pw
            quotes.append(quote)
        else:
            unresolved.append(f'Quote {quote.id}: no provider workspace for tradie {quote.tradie_id}')
    if quotes:
        Quote.objects.bulk_update(quotes, ['provider_workspace_id'], batch_size=500)

    fees = []
    for fee in PlatformFee.objects.all().iterator():
        pw = provider_ws.get(fee.tradie_id)
        if pw:
            fee.provider_workspace_id = pw
            fees.append(fee)
        else:
            unresolved.append(f'PlatformFee {fee.id}: no provider workspace for tradie {fee.tradie_id}')
    if fees:
        PlatformFee.objects.bulk_update(fees, ['provider_workspace_id'], batch_size=500)

    invoices = []
    for invoice in Invoice.objects.all().iterator():
        pw = provider_ws.get(invoice.tradie_id)
        if pw:
            invoice.provider_workspace_id = pw
            invoices.append(invoice)
        else:
            unresolved.append(f'Invoice {invoice.id}: no provider workspace for tradie {invoice.tradie_id}')
    if invoices:
        Invoice.objects.bulk_update(invoices, ['provider_workspace_id'], batch_size=500)

    public_reviews = []
    for review in PublicReview.objects.all().iterator():
        changed = False
        rw = client_ws.get(review.rater_id)
        if rw:
            review.reviewer_workspace_id = rw
            changed = True
        else:
            unresolved.append(f'PublicReview {review.id}: no client workspace for rater {review.rater_id}')
        dw = provider_ws.get(review.ratee_id)
        if dw:
            review.reviewed_workspace_id = dw
            changed = True
        else:
            unresolved.append(f'PublicReview {review.id}: no provider workspace for ratee {review.ratee_id}')
        if changed:
            public_reviews.append(review)
    if public_reviews:
        PublicReview.objects.bulk_update(public_reviews, ['reviewer_workspace_id', 'reviewed_workspace_id'], batch_size=500)

    private_reviews = []
    for review in PrivateReview.objects.all().iterator():
        changed = False
        rw = provider_ws.get(review.rater_id)
        if rw:
            review.reviewer_workspace_id = rw
            changed = True
        else:
            unresolved.append(f'PrivateReview {review.id}: no provider workspace for rater {review.rater_id}')
        dw = client_ws.get(review.ratee_id)
        if dw:
            review.reviewed_workspace_id = dw
            changed = True
        else:
            unresolved.append(f'PrivateReview {review.id}: no client workspace for ratee {review.ratee_id}')
        if changed:
            private_reviews.append(review)
    if private_reviews:
        PrivateReview.objects.bulk_update(private_reviews, ['reviewer_workspace_id', 'reviewed_workspace_id'], batch_size=500)

    if unresolved:
        print(f'\n0036_backfill_workspace_fks: {len(unresolved)} row(s) could not be resolved:', file=sys.stderr)
        for line in unresolved[:50]:
            print(f'  - {line}', file=sys.stderr)


def noop_reverse(apps, schema_editor):
    """Reversing 0034 (which drops the FK columns) is the real rollback path."""


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0047_backfill_workspaces'),
    ]

    operations = [
        migrations.RunPython(backfill_workspace_fks, noop_reverse),
    ]
