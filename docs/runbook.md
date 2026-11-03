# Coconut Wireless Runbook

## Incident Response Basics

1. Confirm scope:
   - Is outage global or partial?
   - Is `/healthz/` failing?
2. Check latest deploy and CI status.
3. Review platform logs for startup/configuration/database errors.
4. Run smoke checks:
   - `/`
   - `/healthz/`
   - `/admin/login/`
5. If `SENTRY_DSN` is configured, inspect Sentry for top exceptions and release markers.

## First-Line Mitigations

- Restart app instance.
- Verify required env vars are set correctly (`DJANGO_ENV`, `DEBUG`, `SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `DATABASE_URL`).
- Verify database availability and credentials.
- Verify object storage settings are valid (`OBJECT_STORAGE_BACKEND=s3`, bucket, keys, endpoint if applicable).
- Verify SMTP settings are valid (`EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`).

## Rollback

1. Select the previous known-good deploy in the hosting platform.
2. Redeploy that version.
3. Re-run smoke checks and `python manage.py check --deploy`.
4. Communicate status update and record timeline.

## Escalation

- Escalate persistent data-layer failures immediately.
- Escalate repeated 5xx responses after rollback.
- Open a follow-up issue with:
  - trigger time
  - impact
  - root cause (if known)
  - remediation and prevention actions

## Tradie Verification Operations

Use Django Admin → **Tradie Profiles** for verification decisions:

- `pending`: default state after provider signup; provider cannot quote or request appointments
- `approved`: documents accepted; provider can quote and book appointments
- `rejected`: provider blocked pending remediation
- `suspended`: provider blocked after approval due to risk/compliance concerns

Recommended verification cadence:

- Closed beta: review pending submissions at least once per business day
- High onboarding periods: review twice daily

Minimum review checklist for each provider:

1. Photo ID uploaded, legible, and the name matches the account (shown on the profile under Verification Documents)
2. TIN letter or card uploaded and legible
3. If Electrical selected, electrical licence uploaded
4. If Plumbing selected, plumber licence uploaded
5. If Air Conditioning selected, air conditioning / refrigeration licence uploaded
6. Business details and service towns look valid
7. Verification notes updated in admin when rejecting/suspending

## App tester program (14-day tutorial)

Google Play needs closed-test testers using the app for 14 days in a row before production release.

1. **Signup bubble.** Right after signing up, every new user sees a welcome bubble asking "Are you an app tester?". If they answer No, it asks whether they'd like to be one, lists the requirements to tick (email linked to their Google Play account, an Android device) and states the reward: the first 15 testers to finish all 14 days get FJD $1,000 credit on all platform fees.
2. **Confirm.** Either yes emails `ADMIN_EMAIL` with "Confirm app tester status: <name>". Add their email to the Google Play closed test, then open **Admin → Tester Enrolments**, select them, and run **Enrol in 14-day tutorial**. You can also add a user directly with status "Enrolled". Enrolling sends an in-app, push and email confirmation, plus the Day 1 task.
3. **Daily tasks.** Schedule `python manage.py send_tester_daily_tasks` once a day, for example as a Railway Cron Job at 8am Fiji time (`0 20 * * *` UTC). It sends each enrolled tester that day's task and never sends the same day twice.
4. **Track.** Testers mark tasks done at `/testers/` (Account menu → App tester tasks). The enrolment list shows day, tasks done, tasks done on the day itself, and streak. Each enrolment page shows a 14-day grid plus any feedback testers left.
5. **Reward.** Finishing all 14 tasks marks the enrolment Completed. While slots remain, it also adds the FJD $1,000 to `User.tester_fee_credit_balance`, which comes off job and Market platform fees automatically.

Edit the 14 tasks under **Admin → Tester Tutorial Tasks**. Set `SITE_URL` (e.g. `https://your-domain`) so emails sent by the cron contain full links.
