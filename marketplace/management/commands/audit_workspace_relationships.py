"""
Management command: python manage.py audit_workspace_relationships [--repair]

Compares each new workspace FK against the legacy User FK it mirrors:
  Task.client_workspace              vs Task.client
  Task.assigned_provider_workspace   vs Task.assigned_tradie
  Quote.provider_workspace           vs Quote.tradie
  PlatformFee.provider_workspace     vs PlatformFee.tradie
  Invoice.provider_workspace         vs Invoice.tradie
  PublicReview.reviewer_workspace    vs PublicReview.rater
  PublicReview.reviewed_workspace    vs PublicReview.ratee
  PrivateReview.reviewer_workspace   vs PrivateReview.rater
  PrivateReview.reviewed_workspace   vs PrivateReview.ratee

Reports three buckets per relationship:
  consistent          — workspace FK set and its owner matches the legacy user FK.
  missing (repairable)— workspace FK is null but exactly one candidate
                         workspace of the right type exists for that user.
                         --repair fills these in; a dry run only counts them.
  mismatched          — workspace FK is set but its owner does NOT match the
                         legacy user FK. Never auto-repaired, with or without
                         --repair — "never silently choose a workspace when
                         ambiguous." Printed for manual review.

A "missing" row with zero candidate workspaces (the user has none of the
required type) is counted separately as unresolved — also never repaired,
since there's nothing correct to fill in yet.
"""
from django.core.management.base import BaseCommand

from marketplace.models import (
    Task, Quote, PlatformFee, Invoice, PublicReview, PrivateReview, Workspace,
)

# (model, workspace_field, user_field, workspace_type, only_when_user_field_set)
CHECKS = [
    (Task, 'client_workspace', 'client', Workspace.TYPE_CLIENT, False),
    (Task, 'assigned_provider_workspace', 'assigned_tradie', Workspace.TYPE_INDIVIDUAL_PROVIDER, True),
    (Quote, 'provider_workspace', 'tradie', Workspace.TYPE_INDIVIDUAL_PROVIDER, False),
    (PlatformFee, 'provider_workspace', 'tradie', Workspace.TYPE_INDIVIDUAL_PROVIDER, False),
    (Invoice, 'provider_workspace', 'tradie', Workspace.TYPE_INDIVIDUAL_PROVIDER, False),
    (PublicReview, 'reviewer_workspace', 'rater', Workspace.TYPE_CLIENT, False),
    (PublicReview, 'reviewed_workspace', 'ratee', Workspace.TYPE_INDIVIDUAL_PROVIDER, False),
    (PrivateReview, 'reviewer_workspace', 'rater', Workspace.TYPE_INDIVIDUAL_PROVIDER, False),
    (PrivateReview, 'reviewed_workspace', 'ratee', Workspace.TYPE_CLIENT, False),
]


class Command(BaseCommand):
    help = 'Audit (and optionally repair) workspace FK consistency against the legacy user FKs they mirror.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--repair', action='store_true',
            help='Fill in missing (null) workspace FKs where exactly one candidate workspace exists. '
                 'Never touches a mismatched FK — those need manual review.',
        )

    def handle(self, *args, **options):
        repair = options['repair']
        grand_consistent = grand_missing_repairable = grand_missing_unresolved = grand_mismatched = 0

        for model, ws_field, user_field, ws_type, only_when_set in CHECKS:
            ws_field_id = f'{ws_field}_id'
            user_field_id = f'{user_field}_id'
            label = f'{model.__name__}.{ws_field} vs {user_field}'

            # (owner, type) is unique-constrained for client/individual_provider,
            # so this lookup is never ambiguous in Phase 1.
            candidate_by_owner = dict(
                Workspace.objects.filter(workspace_type=ws_type).values_list('owner_id', 'id')
            )

            qs = model.objects.all()
            if only_when_set:
                qs = qs.exclude(**{user_field_id: None})

            consistent = missing_repairable = missing_unresolved = mismatched = 0
            mismatch_examples = []
            to_repair = []

            for obj in qs.only('id', ws_field, user_field).iterator():
                user_id = getattr(obj, user_field_id)
                ws_id = getattr(obj, ws_field_id)
                if user_id is None:
                    continue
                expected_ws_id = candidate_by_owner.get(user_id)
                if ws_id is None:
                    if expected_ws_id:
                        missing_repairable += 1
                        to_repair.append((obj.pk, expected_ws_id))
                    else:
                        missing_unresolved += 1
                elif expected_ws_id == ws_id:
                    consistent += 1
                else:
                    mismatched += 1
                    if len(mismatch_examples) < 10:
                        mismatch_examples.append(
                            f"{model.__name__}#{obj.pk}: {ws_field}_id={ws_id} but {user_field}'s "
                            f'{ws_type} workspace is {expected_ws_id}'
                        )

            repaired_note = ''
            if repair and to_repair:
                objs = [model(pk=pk, **{ws_field_id: ws_id}) for pk, ws_id in to_repair]
                model.objects.bulk_update(objs, [ws_field_id], batch_size=500)
                repaired_note = ' (repaired)'

            self.stdout.write(
                f'{label}: consistent={consistent} missing_repairable={missing_repairable}{repaired_note} '
                f'missing_unresolved={missing_unresolved} mismatched={mismatched}'
            )
            for example in mismatch_examples:
                self.stdout.write(self.style.WARNING(f'    MISMATCH: {example}'))

            grand_consistent += consistent
            grand_missing_repairable += missing_repairable
            grand_missing_unresolved += missing_unresolved
            grand_mismatched += mismatched

        self.stdout.write('')
        if grand_mismatched:
            self.stdout.write(self.style.ERROR(
                f'{grand_mismatched} mismatched relationship(s) found — never auto-repaired, needs manual review.'
            ))
        elif not repair and grand_missing_repairable:
            self.stdout.write(self.style.WARNING(
                f'{grand_missing_repairable} row(s) have a resolvable but unset workspace FK. Re-run with --repair to fill them in.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('No inconsistencies found.'))
