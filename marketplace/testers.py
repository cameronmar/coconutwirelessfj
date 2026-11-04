"""
App tester program — the 14-day engagement tutorial.

Flow:
  1. Right after signup (or when an admin uses "Show app tester welcome
     bubble" on existing users), a welcome bubble asks "Are you an app
     tester and have you submitted your Google Play email?". Yes → request_enrolment(). No → "Do you want to be
     one?" with the requirements to tick (email linked to Google Play, an
     Android device) and the reward: the first TESTER_REWARD_SLOTS to finish
     get TESTER_REWARD_CREDIT off all platform fees. Either yes creates a
     TesterEnrolment (status=requested) and emails the admin to confirm.
  2. The admin confirms from the Tester Enrolments admin (or adds a user
     there directly) → enrol() starts the tutorial today, sends the
     enrolment confirmation and Day 1's task.
  3. Every morning `python manage.py send_tester_daily_tasks` calls
     send_due_task() for each enrolled tester.
  4. Testers mark each day done at /testers/ → complete_task(). Finishing
     all 14 marks the enrolment completed and, while slots remain, awards
     the platform fee credit (spent by apply_fee_credit()).

Every notification is logged as an in-app PlatformNotice and also sent as an
Android push (if the user has the app) and an email — all best-effort, so a
failed email or push never blocks enrolment or task delivery.
"""
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import (
    TESTER_PROGRAM_DAYS, TESTER_REWARD_CREDIT, TESTER_REWARD_SLOTS,
    PlatformNotice, PlatformSettings, TesterEnrolment, TesterTask, TesterTaskCompletion, User,
)

def queue_welcome_prompt(request):
    """Called by every signup view: show the "Are you an app tester?"
    welcome bubble on the next page the new user lands on."""
    show_welcome_bubble(User.objects.filter(pk=request.user.pk))


def show_welcome_bubble(users):
    """Queue the welcome bubble for these users (admin action for people
    who signed up before the program, or for testing it yourself). Users
    already enrolled or finished are skipped. Returns how many were set."""
    return users.exclude(
        tester_enrolment__status__in=[TesterEnrolment.STATUS_ENROLLED, TesterEnrolment.STATUS_COMPLETED],
    ).update(show_tester_welcome=True)


def dismiss_welcome_bubble(user):
    User.objects.filter(pk=user.pk).update(show_tester_welcome=False)
    user.show_tester_welcome = False


def reward_slots_remaining():
    awarded = TesterEnrolment.objects.filter(reward_credit_awarded_at__isnull=False).count()
    return max(TESTER_REWARD_SLOTS - awarded, 0)
from .utils import _send_email_notice, notify_admin, send_fcm_push


def _absolute(path):
    return f'{settings.SITE_URL}{path}' if settings.SITE_URL else path


def _notify(user, notice_type, subject, body, push_body):
    PlatformNotice.objects.create(
        recipient=user, notice_type=notice_type,
        channel=PlatformNotice.CHANNEL_IN_PLATFORM, subject=subject, body=body,
    )
    send_fcm_push(user, subject, push_body)
    _send_email_notice(user, subject, body, notice_type)


def request_enrolment(user, request=None, source=TesterEnrolment.SOURCE_INVITED,
                      confirmed_play_email=False, confirmed_android_device=False):
    """Called from the welcome bubble when the user says yes."""
    enrolment, created = TesterEnrolment.objects.get_or_create(user=user, defaults={
        'source': source,
        'confirmed_play_email': confirmed_play_email,
        'confirmed_android_device': confirmed_android_device,
    })
    if not created:
        return enrolment
    if source == TesterEnrolment.SOURCE_VOLUNTEER:
        how = (
            'is not an invited tester but volunteered to become one, and confirmed that their email '
            'is linked to their Google Play account and that they have an Android device'
        )
    else:
        how = 'says they are an app tester and have submitted their Google Play email to the support team'

    def yn(value):
        return 'yes' if value else 'not confirmed'
    checklist = (
        f'Email linked to Google Play: {yn(confirmed_play_email)}\n'
        f'Has an Android device: {yn(confirmed_android_device)}\n\n'
    )
    path = reverse('admin:marketplace_testerenrolment_change', args=[enrolment.pk])
    admin_url = request.build_absolute_uri(path) if request else _absolute(path)
    notify_admin(
        subject=f'Confirm app tester status: {user.full_name}',
        body=(
            f'{user.full_name} ({user.email}) signed up as a {user.get_role_display().lower()} '
            f'in {user.town or "an unknown town"} and {how}.\n\n'
            f'{checklist}'
            f'Remember to add {user.email} to the Google Play closed test.\n\n'
            f'Confirm their app tester status and add them to the 14-day tutorial here:\n{admin_url}\n\n'
            f'Open the enrolment, then use "Enrol in 14-day tutorial" (or decline it).'
        ),
    )
    return enrolment


def enrol_user(user, enrolled_by=None):
    """Admin shortcut: put an existing user straight on the tutorial (no
    bubble, no request step). Returns True if they were newly enrolled."""
    enrolment, _ = TesterEnrolment.objects.get_or_create(
        user=user, defaults={'source': TesterEnrolment.SOURCE_ADMIN},
    )
    dismiss_welcome_bubble(user)
    return enrol(enrolment, enrolled_by=enrolled_by)


def restart(enrolment, enrolled_by=None):
    """Wipe a tester's progress and start again from Day 1 today — handy
    when testing the tutorial on your own account. Any reward credit
    already given is left alone."""
    enrolment.completions.all().delete()
    enrolment.status = TesterEnrolment.STATUS_REQUESTED
    enrolment.save(update_fields=['status'])
    return enrol(enrolment, enrolled_by=enrolled_by)


def enrol(enrolment, enrolled_by=None):
    """Confirm a tester and start their tutorial today. Idempotent for an
    already-enrolled/completed tester."""
    if enrolment.status in (TesterEnrolment.STATUS_ENROLLED, TesterEnrolment.STATUS_COMPLETED):
        return False
    with transaction.atomic():
        enrolment.status = TesterEnrolment.STATUS_ENROLLED
        enrolment.enrolled_at = timezone.now()
        enrolment.enrolled_by = enrolled_by
        enrolment.start_date = timezone.localdate()
        enrolment.last_task_notified_day = 0
        enrolment.save()
    user = enrolment.user
    subject = "You're enrolled as a Coconut Wireless app tester"
    body = (
        f'Bula {user.first_name},\n\n'
        f"Vinaka for helping us test the Coconut Wireless Network app! You're now enrolled in our "
        f'{TESTER_PROGRAM_DAYS}-day tester tutorial.\n\n'
        f"Each day for the next {TESTER_PROGRAM_DAYS} days we'll send you one short task to try in the app. "
        f"Please open the app every day, do that day's task, and tap \"Mark as done\" — "
        f"using the app on {TESTER_PROGRAM_DAYS} days in a row is what helps us launch on Google Play.\n\n"
        f'See your tasks and progress here: {_absolute(reverse("tester_tutorial"))}\n\n'
        f'Vinaka,\nThe Coconut Wireless Network Team'
    )
    _notify(user, PlatformNotice.TYPE_TESTER_ENROLLED, subject, body,
            f"You're enrolled! Your first of {TESTER_PROGRAM_DAYS} daily tasks is ready.")
    send_due_task(enrolment)
    return True


def send_due_task(enrolment, today=None):
    """Send today's task if it hasn't been sent yet. Only the current day's
    task is notified — a tester who missed days isn't flooded with a
    backlog, but can still see and complete earlier tasks at /testers/."""
    if enrolment.status != TesterEnrolment.STATUS_ENROLLED:
        return False
    day = enrolment.current_day(today)
    if day < 1 or day <= enrolment.last_task_notified_day:
        return False
    task = TesterTask.objects.filter(day=day).first()
    if not task:
        return False
    user = enrolment.user
    subject = f'App tester day {day} of {TESTER_PROGRAM_DAYS}: {task.title}'
    body = (
        f'Bula {user.first_name},\n\n'
        f"Here's today's app tester task (day {day} of {TESTER_PROGRAM_DAYS}):\n\n"
        f'{task.title}\n{task.instructions}\n\n'
        f'When you\'ve done it, mark it complete here: {_absolute(reverse("tester_tutorial"))}\n\n'
        f'Vinaka,\nThe Coconut Wireless Network Team'
    )
    _notify(user, PlatformNotice.TYPE_TESTER_DAILY_TASK, subject, body, f'Day {day}: {task.title}')
    enrolment.last_task_notified_day = day
    enrolment.save(update_fields=['last_task_notified_day'])
    return True


def complete_task(enrolment, task, feedback=''):
    """Mark a task done. Only tasks that have unlocked (day <= today's day)
    can be completed. Returns the completion, or None if not allowed."""
    if enrolment.status not in (TesterEnrolment.STATUS_ENROLLED, TesterEnrolment.STATUS_COMPLETED):
        return None
    if task.day > enrolment.current_day():
        return None
    completion, _ = TesterTaskCompletion.objects.get_or_create(
        enrolment=enrolment, task=task, defaults={'feedback': (feedback or '').strip()},
    )
    if (enrolment.status == TesterEnrolment.STATUS_ENROLLED
            and len(enrolment.completed_days()) >= TESTER_PROGRAM_DAYS):
        enrolment.status = TesterEnrolment.STATUS_COMPLETED
        enrolment.save(update_fields=['status'])
        if enrolment.user.is_staff:
            # Admins testing the tutorial on their own account don't use up a reward slot.
            reward_line = 'This is a staff account, so no reward credit was added and no reward slot was used.'
        elif _award_reward_if_slot_left(enrolment):
            reward_line = (
                f'They were one of the first {TESTER_REWARD_SLOTS} and have been given the '
                f'FJD ${TESTER_REWARD_CREDIT:,.0f} platform fee credit.'
            )
        else:
            reward_line = f'All {TESTER_REWARD_SLOTS} reward credits were already given out, so no credit was added.'
        notify_admin(
            subject=f'App tester finished the {TESTER_PROGRAM_DAYS}-day tutorial: {enrolment.user.full_name}',
            body=(
                f'{enrolment.user.full_name} ({enrolment.user.email}) has completed all '
                f'{TESTER_PROGRAM_DAYS} tester tasks ({len(enrolment.on_time_days())} on the day itself).\n\n'
                f'{reward_line}'
            ),
        )
    return completion


def _award_reward_if_slot_left(enrolment):
    """Give the fee credit to one of the first TESTER_REWARD_SLOTS finishers.
    Serialized on the PlatformSettings row (same mutex pattern as founding
    members) so two testers finishing at once can't both take the last slot."""
    with transaction.atomic():
        active = PlatformSettings.get_active()
        if active:
            PlatformSettings.objects.select_for_update().filter(pk=active.pk).exists()
        if enrolment.reward_credit_awarded_at or reward_slots_remaining() <= 0:
            return False
        enrolment.reward_credit_awarded_at = timezone.now()
        enrolment.save(update_fields=['reward_credit_awarded_at'])
        user = User.objects.select_for_update().get(pk=enrolment.user_id)
        user.tester_fee_credit_balance += TESTER_REWARD_CREDIT
        user.save(update_fields=['tester_fee_credit_balance'])
    subject = f"You've earned FJD ${TESTER_REWARD_CREDIT:,.0f} in platform fee credit!"
    body = (
        f'Bula {user.first_name},\n\n'
        f'You finished all {TESTER_PROGRAM_DAYS} days of app testing and were one of the first '
        f'{TESTER_REWARD_SLOTS} testers to do it — vinaka vakalevu!\n\n'
        f'FJD ${TESTER_REWARD_CREDIT:,.0f} of credit has been added to your account. It comes off any '
        f'platform fees you are charged (job fees and Market fees) automatically until it runs out.\n\n'
        f'Vinaka,\nThe Coconut Wireless Network Team'
    )
    _notify(user, PlatformNotice.TYPE_TESTER_ENROLLED, subject, body, subject)
    return True


def apply_fee_credit(user_id, fee_amount):
    """Spend the app tester credit against a platform fee. Call inside the
    caller's transaction.atomic(); returns the discount taken (0 if none)."""
    fee_amount = Decimal(fee_amount)
    if fee_amount <= 0:
        return Decimal('0.00')
    user = User.objects.select_for_update().filter(pk=user_id).first()
    if not user or user.tester_fee_credit_balance <= 0:
        return Decimal('0.00')
    discount = min(user.tester_fee_credit_balance, fee_amount)
    user.tester_fee_credit_balance -= discount
    user.save(update_fields=['tester_fee_credit_balance'])
    return discount
