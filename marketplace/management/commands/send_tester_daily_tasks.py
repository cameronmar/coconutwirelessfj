"""
Management command: python manage.py send_tester_daily_tasks

Sends each enrolled app tester today's task from the 14-day tutorial
(in-app notice + Android push + email). Safe to run more than once a day —
a day's task is only ever sent once per tester.

Schedule it daily (e.g. a Railway Cron Job at 8am Fiji time:
`0 20 * * *` in UTC, or `0 19 * * *` during Fiji daylight saving).
"""
from django.core.management.base import BaseCommand

from marketplace.models import TesterEnrolment
from marketplace.testers import send_due_task


class Command(BaseCommand):
    help = "Send each enrolled app tester today's 14-day tutorial task."

    def handle(self, *args, **options):
        sent = 0
        enrolments = TesterEnrolment.objects.filter(
            status=TesterEnrolment.STATUS_ENROLLED,
        ).select_related('user')
        for enrolment in enrolments:
            if send_due_task(enrolment):
                sent += 1
        self.stdout.write(self.style.SUCCESS(f'Sent {sent} tester task notification(s).'))
