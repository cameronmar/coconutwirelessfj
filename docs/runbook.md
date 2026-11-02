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

1. TIN letter or card uploaded and legible
2. If Electrical selected, electrical licence uploaded
3. If Plumbing selected, plumber licence uploaded
4. If Air Conditioning selected, air conditioning / refrigeration licence uploaded
5. Business details and service towns look valid
6. Verification notes updated in admin when rejecting/suspending
